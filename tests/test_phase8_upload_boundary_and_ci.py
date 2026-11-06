# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 8 Tests — Upload Boundary Hardening, Forwarded-Header Trust, and Auth Regression.

Covers the three Phase 8 priorities against the remediated code:

* **Upload boundary** (`dz_erp/upload.py`): a documented, configurable size
  limit enforced *while* streaming, type allowlisting that does not trust the
  filename or the declared Content-Type, and guaranteed resource release.
* **Forwarded-header trust** (`dz_erp/net.py`): `X-Forwarded-For` is ignored
  unless the request arrives through an explicitly configured trusted proxy.
* **Authentication regression**: the Phase 7 remediation is re-verified here
  against the current commit, not assumed from the earlier report.

All fixtures are local and synthetic; no production data is used.
"""

import io
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import AuthManager, hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import (
    AUDIT_KEY_SUFFIX,
    DEFAULT_AUDIT_HMAC_SECRET,
    ERPDatabase,
)
from dz_erp.intake_ocr import (
    VAULT_KEY_ENV,
    VAULT_KEY_SUFFIX,
    PrivateDocumentVault,
)
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup
from dz_erp.localization import load_official_odoo_chart
from dz_erp.net import (
    TrustedProxyPolicy,
    describe_trusted_proxy_policy,
    parse_forwarded_for,
    trusted_proxy_policy_from_env,
)
from dz_erp.upload import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    DEFAULT_MAX_UPLOAD_BYTES,
    UPLOAD_CHUNK_BYTES,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
    UploadValidationError,
    cleanup_partial_upload,
    close_upload,
    configured_max_upload_bytes,
    describe_upload_policy,
    extension_of,
    read_bounded_upload,
    sniff_mime_type,
    upload_file_is_closed,
    validate_content_signature,
    validate_upload_metadata,
)
from dz_erp.upload import WINDOWS_RESERVED_NAMES, _validate_filename_shape  # exercised directly below

FAST_ITERATIONS = 1000

TENANT_USERS = [
    (1, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (2, 1, "wh_a", "warehouse", 1, "Wh#2026x"),
    (3, 1, "approver_a", "approver", 1, "Appr#2026x"),
    (4, 1, "acct_a", "accountant", 1, "Acct#2026x"),
    (5, 1, "admin_a", "admin", 1, "Adm#2026x"),
    (6, 1, "revoked_a", "admin", 0, "Revoked#2026x"),
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
    (21, 2, "clerk_b", "clerk", 1, "ClerkB#2026x"),
]

SUPPLIER_SPEC = {
    "supplier_name": "Supplier Alpha",
    "invoice_number": "FAC-P8-001",
    "invoice_date": "2026-10-15",
    "supplier_nif": "001216099123456",
    "supplier_rc": "16/00-0991234B12",
    "supplier_ai": "16019912345",
    "payment_method": "BANK_TRANSFER",
    "amount_ht_dzd": "10000.00",
    "amount_tva_dzd": "1900.00",
    "amount_timbre_dzd": "0.00",
    "amount_ttc_dzd": "11900.00",
    "lines": [
        {"sku": "S1", "description": "Service", "qty": "1.00", "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}
    ],
}


class _FakeUpload:
    """Minimal UploadFile stand-in that records closure and read calls."""

    def __init__(self, content: bytes, chunk_bytes: int = 1024, filename: str = "doc.pdf", content_type: str = "application/pdf"):
        self._stream = io.BytesIO(content)
        self._chunk = chunk_bytes
        self.filename = filename
        self.content_type = content_type
        self.size = len(content)
        self.closed = False
        self.read_calls = 0
        # Starlette's UploadFile exposes the backing stream as ``.file``.
        self.file = self._stream

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if size is None or size < 0:
            return self._stream.read()
        return self._stream.read(min(size, self._chunk))

    async def close(self) -> None:
        self.closed = True
        self._stream.close()


class _StreamingUpload:
    """Upload whose body is produced lazily, so its true size is unknown up front."""

    def __init__(self, total: int, declared_size: Optional[int] = None, filename: str = "big.pdf", content_type: str = "application/pdf"):
        self._total = total
        self._produced = 0
        self.size = declared_size
        self.filename = filename
        self.content_type = content_type
        self.closed = False
        self.read_calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self._produced >= self._total:
            return b""
        remaining = self._total - self._produced
        take = remaining if size is None or size < 0 else min(size, remaining)
        self._produced += take
        return b"%PDF-" + b"A" * (take - 5) if take >= 5 else b"A" * take

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    db = ERPDatabase(str(tmp_path / "p8.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute("INSERT INTO companies (id, code, name, created_at) VALUES (1, 'DZ1', 'Alpha', '2026-01-01')")
        conn.execute("INSERT INTO companies (id, code, name, created_at) VALUES (2, 'DZ2', 'Beta', '2026-01-01')")
        for uid, cid, username, role, active, password in TENANT_USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, cid, username, role, active, hash_password(password, iterations=FAST_ITERATIONS)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc) VALUES (10, 1, 'Supplier Alpha', 'supplier',"
            " '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
        )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc) VALUES (30, 2, 'Supplier Beta', 'supplier',"
            " '001216099987654', '099116010987654', '16019998765', '16/00-0999876B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)

    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    app = create_erp_fastapi_app(db, vault, auth_manager=auth)
    return SimpleNamespace(db=db, vault=vault, auth=auth, app=app, client=TestClient(app), tmp_path=tmp_path)


def _token(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pdf(extra_pad: int = 0) -> bytes:
    """A valid synthetic supplier invoice PDF, optionally padded."""
    content = render_synthetic_invoice_pdf(SUPPLIER_SPEC)
    if extra_pad:
        content = content + b"\x00" * extra_pad
    return content


def _pdf_byte_size(env: SimpleNamespace, document_id: str) -> int:
    with env.db.transaction() as conn:
        row = conn.execute("SELECT byte_size FROM source_documents WHERE id = ?", (document_id,)).fetchone()
    return int(row["byte_size"])


def _png(width: int = 64, height: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _upload(client: TestClient, token: str, filename: str, content: bytes, content_type: str):
    return client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, io.BytesIO(content), content_type)},
        headers=_auth(token),
    )


def _count(env: SimpleNamespace, table: str) -> int:
    with env.db.transaction() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


# ===========================================================================
# Priority 0 — Upload size boundary
# ===========================================================================


def test_upload_policy_is_documented_and_configurable() -> None:
    policy = describe_upload_policy()
    assert policy["max_upload_bytes"] == DEFAULT_MAX_UPLOAD_BYTES
    assert policy["chunk_bytes"] == UPLOAD_CHUNK_BYTES
    assert policy["allowed_mime_types"] == sorted(ALLOWED_MIME_TYPES)
    assert policy["allowed_extensions"] == sorted(ALLOWED_EXTENSIONS)
    assert policy["enforced_by"].startswith("application")
    assert policy["env_override"] == "ERP_MAX_UPLOAD_BYTES"
    # The default must be a sane, conservative bound for an initial ERP release.
    assert 0 < DEFAULT_MAX_UPLOAD_BYTES <= 32 * 1024 * 1024


def test_upload_policy_endpoint_is_public_and_reports_limits(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/v1/upload-policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["upload"]["max_upload_bytes"] == DEFAULT_MAX_UPLOAD_BYTES
    assert "application/pdf" in body["upload"]["allowed_mime_types"]
    # Fail-closed default: no trusted proxies configured.
    assert body["forwarded_headers"]["forwarded_headers_honoured"] is False


def test_upload_limit_is_configurable_via_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert configured_max_upload_bytes() == DEFAULT_MAX_UPLOAD_BYTES
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", "2048")
    assert configured_max_upload_bytes() == 2048
    # A malformed or non-positive override must never silently disable the limit.
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", "not-a-number")
    assert configured_max_upload_bytes() == DEFAULT_MAX_UPLOAD_BYTES
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", "0")
    assert configured_max_upload_bytes() == DEFAULT_MAX_UPLOAD_BYTES
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", "-5")
    assert configured_max_upload_bytes() == DEFAULT_MAX_UPLOAD_BYTES


async def test_read_bounded_upload_valid_file_below_limit() -> None:
    content = _pdf()
    upload = _FakeUpload(content, chunk_bytes=512)
    result = await read_bounded_upload(upload, max_bytes=10 * 1024 * 1024)
    assert result == content
    assert upload.closed is False  # closing is the caller's job


async def test_read_bounded_upload_exactly_at_limit() -> None:
    """A body of exactly `limit` bytes is accepted."""
    content = _pdf()
    limit = len(content)
    upload = _FakeUpload(content, chunk_bytes=512)
    result = await read_bounded_upload(upload, max_bytes=limit)
    assert len(result) == limit
    assert result == content


async def test_read_bounded_upload_one_byte_over_limit() -> None:
    """A body of `limit + 1` bytes is rejected and not returned."""
    content = _pdf()
    limit = len(content) - 1
    upload = _FakeUpload(content, chunk_bytes=512)
    with pytest.raises(UploadTooLargeError) as excinfo:
        await read_bounded_upload(upload, max_bytes=limit)
    assert excinfo.value.limit == limit
    # The oversized body must not be handed back.
    assert "content" not in str(excinfo.value)


async def test_read_bounded_upload_stops_reading_early_on_overflow() -> None:
    """Reading must stop at the limit, not consume the whole body."""
    total = 5 * 1024 * 1024
    limit = 1024 * 1024
    upload = _StreamingUpload(total=total, declared_size=None)
    with pytest.raises(UploadTooLargeError):
        await read_bounded_upload(upload, max_bytes=limit)
    # Far fewer reads than would be needed to consume 5 MiB.
    assert upload.read_calls < (total // UPLOAD_CHUNK_BYTES)
    assert upload.read_calls * UPLOAD_CHUNK_BYTES < total


async def test_read_bounded_upload_rejects_lying_declared_size() -> None:
    """A declared Content-Length under the limit cannot smuggle a larger body."""
    limit = 1024
    upload = _StreamingUpload(total=limit + 1, declared_size=10)
    with pytest.raises(UploadTooLargeError):
        await read_bounded_upload(upload, max_bytes=limit)


async def test_read_bounded_upload_declared_size_over_limit_is_rejected_before_reading() -> None:
    upload = _StreamingUpload(total=10, declared_size=10 * 1024 * 1024)
    with pytest.raises(UploadTooLargeError):
        await read_bounded_upload(upload, max_bytes=1024, declared_size=upload.size)
    assert upload.read_calls == 0


async def test_read_bounded_upload_empty_body_is_accepted_as_empty() -> None:
    upload = _FakeUpload(b"", chunk_bytes=512)
    assert await read_bounded_upload(upload, max_bytes=1024) == b""


@pytest.mark.parametrize("bad_limit", [0, -1])
async def test_read_bounded_upload_rejects_non_positive_limit(bad_limit: int) -> None:
    upload = _FakeUpload(b"x", chunk_bytes=512)
    with pytest.raises(ValueError):
        await read_bounded_upload(upload, max_bytes=bad_limit)


# ===========================================================================
# Priority 0 — HTTP behaviour of the upload boundary
# ===========================================================================


def test_upload_valid_file_below_limit_is_accepted(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    content = _pdf()
    resp = _upload(env.client, token, "invoice.pdf", content, "application/pdf")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document_id"]
    # The stored byte size must be the size of what we sent.
    assert _pdf_byte_size(env, body["document_id"]) == len(content)


def test_upload_exactly_at_limit_is_accepted(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A body of exactly the configured limit must be accepted, not rejected."""
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(_pdf())))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")
    resp = _upload(client, token, "invoice.pdf", _pdf(), "application/pdf")
    assert resp.status_code == 200, resp.text


def test_upload_one_byte_over_limit_returns_413(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    content = _pdf()
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")
    resp = _upload(client, token, "invoice.pdf", content, "application/pdf")
    assert resp.status_code == 413, resp.text
    assert "maximum allowed size" in resp.json()["detail"]


def test_oversized_upload_creates_no_document_record(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    content = _pdf()
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")

    before_docs = _count(env, "source_documents")
    before_vault = len(list((env.tmp_path / "vault").rglob("*.enc")))
    resp = _upload(client, token, "invoice.pdf", content, "application/pdf")
    assert resp.status_code == 413

    assert _count(env, "source_documents") == before_docs
    assert _count(env, "ocr_extracted_fields") == 0
    assert _count(env, "supplier_invoices") == 0
    # No orphaned vault file.
    assert len(list((env.tmp_path / "vault").rglob("*.enc"))) == before_vault


def test_oversized_upload_does_not_trigger_ocr(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """No OCR work is performed for a rejected body."""
    content = _pdf()
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")

    before = _count(env, "ocr_extracted_fields")
    resp = _upload(client, token, "invoice.pdf", content, "application/pdf")
    assert resp.status_code == 413
    assert _count(env, "ocr_extracted_fields") == before == 0


def test_oversized_upload_is_rejected_before_authentication_is_bypassed(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """An oversized body from an unauthenticated caller is still 401, not 413."""
    content = _pdf()
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    resp = _upload(client, "", "invoice.pdf", content, "application/pdf")
    assert resp.status_code == 401


def test_oversized_upload_streamed_body_exceeds_declared_size(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A streamed body larger than the limit is rejected even though its declared
    Content-Length claims a small size."""
    limit = 64 * 1024
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(limit))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")

    # A generator body: httpx streams it and sends no Content-Length, so the
    # application must discover the overflow itself while reading.
    boundary = "----Phase8Boundary"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="big.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    remaining = 512 * 1024  # well over the 64 KiB limit

    def streamed_body():
        nonlocal remaining
        yield head
        chunk = b"%PDF-" + b"A" * (UPLOAD_CHUNK_BYTES - 5)
        while remaining > 0:
            take = min(len(chunk), remaining)
            remaining -= take
            yield chunk[:take]
        yield tail

    resp = client.post(
        "/api/v1/documents/upload",
        content=streamed_body(),
        headers={**_auth(token), "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 413, resp.text
    assert _count(env, "source_documents") == 0


def test_repeated_oversized_uploads_leave_no_state(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    content = _pdf()
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")

    for i in range(5):
        resp = _upload(client, token, "invoice.pdf", content, "application/pdf")
        assert resp.status_code == 413, f"attempt {i}: {resp.status_code}"
    assert _count(env, "source_documents") == 0
    assert len(list((env.tmp_path / "vault").rglob("*.enc"))) == 0
    # Legitimate uploads still work afterwards.
    monkeypatch.delenv("ERP_MAX_UPLOAD_BYTES")
    ok = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    ok_token = _token(TestClient(ok), "clerk_a", "Clerk#2026x")
    assert _upload(TestClient(ok), ok_token, "invoice.pdf", content, "application/pdf").status_code == 200


def test_concurrent_oversized_uploads_are_all_rejected(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent oversized uploads must each be rejected, with no partial state."""
    import concurrent.futures

    content = _pdf()
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")

    def attempt(_: int) -> int:
        return _upload(client, token, "invoice.pdf", content, "application/pdf").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(12)))

    assert results == [413] * 12, f"unexpected statuses: {sorted(set(results))}"
    assert _count(env, "source_documents") == 0
    assert _count(env, "ocr_extracted_fields") == 0


def test_upload_rejection_closes_the_uploaded_file(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """The uploaded file is always closed, including on rejection."""

    content = _pdf()
    captured: List[Any] = []

    original = read_bounded_upload

    async def spy(file, max_bytes=None, **kwargs):
        captured.append(file)
        return await original(file, max_bytes=max_bytes, **kwargs)

    import dz_erp.api as api_module

    monkeypatch.setattr(api_module, "read_bounded_upload", spy)
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", str(len(content) - 1))
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    client = TestClient(app)
    token = _token(client, "clerk_a", "Clerk#2026x")

    resp = client.post(
        "/api/v1/documents/upload",
        files={"file": ("invoice.pdf", content, "application/pdf")},
        headers=_auth(token),
    )
    assert resp.status_code == 413
    # The finally-block must have closed it even though the read raised.
    assert captured, "upload was not read"
    assert upload_file_is_closed(captured[0]) is True


async def test_close_upload_is_finally_safe() -> None:
    upload = _FakeUpload(b"data")
    await close_upload(upload)
    assert upload.closed is True
    # Closing twice must not raise.
    await close_upload(upload)
    # None and objects without close() must be tolerated.
    await close_upload(None)
    await close_upload(object())


async def test_close_upload_never_propagates_a_close_failure() -> None:
    """A failure while releasing the upload must not mask the original error
    (for example UploadTooLargeError) that triggered the cleanup."""

    class _BadClose:
        async def close(self):
            raise RuntimeError("close exploded")

    # Must not raise, so the caller's original exception is what surfaces.
    await close_upload(_BadClose())


async def test_close_upload_propagates_the_callers_original_error() -> None:
    """The error that caused the cleanup still reaches the caller."""

    class _BadClose:
        async def close(self):
            raise RuntimeError("close exploded")

    try:
        try:
            raise ValueError("original rejection")
        finally:
            await close_upload(_BadClose())
    except ValueError as exc:
        assert str(exc) == "original rejection"
    else:  # pragma: no cover - the test above must raise
        raise AssertionError("the original error was swallowed")


def test_cleanup_partial_upload_removes_files(env: SimpleNamespace) -> None:
    target = env.tmp_path / "partial.tmp"
    target.write_bytes(b"partial")
    assert cleanup_partial_upload(target) is True
    assert not target.exists()
    # Idempotent and safe for a missing path.
    assert cleanup_partial_upload(target) is False
    assert cleanup_partial_upload(None) is False


def test_upload_file_is_closed_helper() -> None:
    upload = _FakeUpload(b"x")
    assert upload_file_is_closed(upload) is False
    asyncio_run = __import__("asyncio").run
    asyncio_run(upload.close())
    assert upload_file_is_closed(upload) is True


# ===========================================================================
# Priority 0 — File-type validation (never trust filename or Content-Type alone)
# ===========================================================================


@pytest.mark.parametrize(
    "filename,mime,expected",
    [
        ("invoice.pdf", "application/pdf", "application/pdf"),
        ("scan.PNG", "image/png", "image/png"),
        ("scan.jpg", "image/jpeg", "image/jpeg"),
        ("scan.jpeg", "IMAGE/JPEG", "image/jpeg"),
        ("invoice.pdf", "application/pdf; charset=binary", "application/pdf"),
    ],
)
def test_upload_metadata_accepts_allowed_types(filename: str, mime: str, expected: str) -> None:
    assert validate_upload_metadata(filename, mime) == expected


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("invoice.pdf", "application/x-msdownload"),
        ("invoice.exe", "application/pdf"),
        ("invoice.php", "application/pdf"),
        ("archive.zip", "application/zip"),
        ("noextension", "application/pdf"),
        ("", "application/pdf"),
        ("invoice.pdf", ""),
        ("invoice.pdf", None),
        ("../etc/passwd.pdf", "application/pdf"),
        ("invoice.pdf.txt", "application/pdf"),
        ("/etc/passwd.pdf", "application/pdf"),
        ("C:\\invoice.pdf", "application/pdf"),
        ("invoice.pdf/", "application/pdf"),
        ("invoice\x00.pdf", "application/pdf"),
    ],
)
def test_upload_metadata_rejects_disallowed_types(filename: str, mime: Optional[str]) -> None:
    with pytest.raises((UnsupportedMediaTypeError, UploadValidationError)):
        validate_upload_metadata(filename, mime)


def test_upload_metadata_rejects_extension_mime_mismatch() -> None:
    with pytest.raises(UploadValidationError, match="does not match"):
        validate_upload_metadata("invoice.png", "application/pdf")


def test_extension_of_is_case_insensitive() -> None:
    assert extension_of("A.PDF") == ".pdf"
    assert extension_of("no_ext") == ""
    assert extension_of(None) == ""
    assert extension_of("") == ""


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"%PDF-1.7 rest", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n rest", "image/png"),
        (b"\xff\xd8\xff\xe0 rest", "image/jpeg"),
        (b"MZ\x90\x00 rest", None),
        (b"#!/bin/sh\n", None),
        (b"", None),
    ],
)
def test_sniff_mime_type_detects_from_content(content: bytes, expected: Optional[str]) -> None:
    assert sniff_mime_type(content) == expected


def test_validate_content_signature_rejects_label_content_mismatch() -> None:
    with pytest.raises(UnsupportedMediaTypeError, match="does not match"):
        validate_content_signature(b"%PDF-1.7", "image/png")


def test_validate_content_signature_rejects_unknown_content() -> None:
    with pytest.raises(UnsupportedMediaTypeError, match="does not match any supported"):
        validate_content_signature(b"MZ binary", "application/pdf")


def test_upload_endpoint_rejects_exe_with_pdf_name(env: SimpleNamespace) -> None:
    """A renamed executable must not pass the filename/MIME allowlist."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "malware.pdf", b"MZ\x90\x00\x03\x00\x00\x00", "application/pdf")
    assert resp.status_code in (400, 415), resp.text
    assert _count(env, "source_documents") == 0


def test_upload_endpoint_rejects_unsupported_extension(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "payload.exe", b"MZ\x90\x00", "application/pdf")
    assert resp.status_code == 415, resp.text
    assert _count(env, "source_documents") == 0


def test_upload_endpoint_rejects_unsupported_declared_mime(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "invoice.pdf", b"%PDF-1.7", "application/zip")
    assert resp.status_code == 415, resp.text


def test_upload_endpoint_rejects_png_bytes_labelled_pdf(env: SimpleNamespace) -> None:
    """Content signature beats the declared label."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "invoice.pdf", _png(), "application/pdf")
    assert resp.status_code == 415, resp.text
    assert _count(env, "source_documents") == 0


def test_upload_endpoint_rejects_pdf_bytes_labelled_png(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "invoice.png", _pdf(), "image/png")
    assert resp.status_code == 415, resp.text


def test_upload_endpoint_accepts_valid_png(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "scan.png", _png(), "image/png")
    assert resp.status_code == 200, resp.text


def test_upload_quarantine_is_preserved_for_malware(env: SimpleNamespace) -> None:
    """The pre-existing EICAR quarantine behaviour must be unchanged."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    resp = _upload(env.client, token, "eicar.pdf", b"%PDF-1.7\n" + eicar, "application/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.json()["security_status"] == "QUARANTINED"
    assert _count(env, "source_documents") == 1


# ===========================================================================
# Reproducible environment — no hidden host dependencies
# ===========================================================================


def test_benchmark_font_is_bundled_and_resolves_without_the_system_font() -> None:
    """The OCR benchmark must not depend on a font installed on the host.

    The benchmark renders synthetic invoices and the suite asserts measured OCR
    accuracy on that output.  Resolving the font from a hardcoded system path
    made the result a function of the runner image: a different DejaVu build,
    or its absence (silently falling back to Helvetica), shifted glyph shapes
    and therefore the accuracy numbers, so the same commit passed on one
    machine and failed on another.  The font is now vendored and takes
    precedence.
    """
    import dz_erp.benchmark as benchmark

    assert benchmark.BUNDLED_DEJAVU_TTF.is_file(), (
        f"the vendored benchmark font is missing: {benchmark.BUNDLED_DEJAVU_TTF}"
    )
    # The bundled font must win even when the system path does not exist.
    original = benchmark.DEJAVU_TTF
    try:
        benchmark.DEJAVU_TTF = "/nonexistent/DejaVuSans.ttf"
        assert benchmark._resolve_font_path() == benchmark.BUNDLED_DEJAVU_TTF
    finally:
        benchmark.DEJAVU_TTF = original


def test_benchmark_rendering_is_reproducible() -> None:
    """Rendering the same spec twice must yield the same extracted content.

    This is what makes the measured OCR accuracy a property of the code rather
    than of the machine that generated it.  The raw PDF bytes are *not*
    compared, because reportlab embeds a creation timestamp; the rasterised
    image carries no such metadata and is compared byte for byte.
    """
    import pdfplumber

    from dz_erp.benchmark import render_synthetic_invoice_image, render_synthetic_invoice_pdf

    spec = dict(SUPPLIER_SPEC, arabic_header="شركة أطلس للتجهيز الصناعي - فاتورة")

    first_pdf = render_synthetic_invoice_pdf(spec)
    second_pdf = render_synthetic_invoice_pdf(spec)
    with pdfplumber.open(io.BytesIO(first_pdf)) as opened:
        first_text = opened.pages[0].extract_text() or ""
    with pdfplumber.open(io.BytesIO(second_pdf)) as opened:
        second_text = opened.pages[0].extract_text() or ""
    assert first_text == second_text
    assert "FAC-P8-001" in first_text

    # The raster path (what RapidOCR actually sees) must be byte-identical.
    assert render_synthetic_invoice_image(spec) == render_synthetic_invoice_image(spec)


# ===========================================================================
# Priority 2 — Forwarded-header trust
# ===========================================================================


def test_forwarded_headers_are_ignored_by_default() -> None:
    policy = TrustedProxyPolicy()
    assert policy.trusted_proxies == ()
    # Even a well-formed forwarded chain is discarded when no proxy is trusted.
    assert policy.resolve_client_ip("203.0.113.9", "1.2.3.4, 5.6.7.8") == "203.0.113.9"
    assert policy.resolve_client_ip("10.0.0.5", "1.2.3.4") == "10.0.0.5"


def test_forwarded_headers_honoured_only_from_trusted_proxy() -> None:
    policy = TrustedProxyPolicy(["10.0.0.0/8"])
    assert policy.is_trusted("10.1.2.3") is True
    assert policy.is_trusted("203.0.113.9") is False
    # Peer is trusted -> honour the chain.
    assert policy.resolve_client_ip("10.1.2.3", "203.0.113.9") == "203.0.113.9"
    # Peer is NOT trusted -> ignore the header entirely.
    assert policy.resolve_client_ip("203.0.113.9", "1.2.3.4") == "203.0.113.9"


def test_forwarded_chain_uses_rightmost_untrusted_address() -> None:
    policy = TrustedProxyPolicy(["10.0.0.0/8", "192.168.0.0/16"])
    # client -> trusted-lb -> trusted-internal -> untrusted-client
    assert policy.resolve_client_ip("10.0.0.1", "1.2.3.4, 192.168.1.1, 10.0.0.1") == "1.2.3.4"


def test_forwarded_header_cannot_be_forged_by_prepending() -> None:
    policy = TrustedProxyPolicy(["10.0.0.0/8"])
    # A caller prepending a fake entry must not win: the right-most untrusted
    # address (what the trusted proxy actually saw) is used.
    assert policy.resolve_client_ip("10.0.0.1", "9.9.9.9, 203.0.113.9") == "203.0.113.9"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, []),
        ("", []),
        ("   ", []),
        ("1.2.3.4", ["1.2.3.4"]),
        ("1.2.3.4, 5.6.7.8", ["1.2.3.4", "5.6.7.8"]),
        ("1.2.3.4,5.6.7.8", ["1.2.3.4", "5.6.7.8"]),
        ("1.2.3.4:5678", ["1.2.3.4"]),
        ("[2001:db8::1]:443", ["2001:db8::1"]),
        ("not-an-ip", []),
        ("1.2.3.4, garbage, 5.6.7.8", ["1.2.3.4", "5.6.7.8"]),
        ("1.2.3.4, , 5.6.7.8", ["1.2.3.4", "5.6.7.8"]),
    ],
)
def test_parse_forwarded_for_handles_malformed_input(value: Optional[str], expected: List[str]) -> None:
    assert parse_forwarded_for(value) == expected


def test_trusted_proxy_policy_ignores_malformed_configuration() -> None:
    policy = TrustedProxyPolicy(["not-a-cidr", "", "10.0.0.0/8", "garbage/33"])
    assert policy.trusted_proxies == ("10.0.0.0/8",)


def test_trusted_proxy_policy_supports_bare_addresses() -> None:
    policy = TrustedProxyPolicy(["203.0.113.9"])
    assert policy.is_trusted("203.0.113.9") is True
    assert policy.is_trusted("203.0.113.10") is False


def test_resolve_client_ip_handles_missing_peer() -> None:
    policy = TrustedProxyPolicy(["10.0.0.0/8"])
    assert policy.resolve_client_ip(None, "1.2.3.4") == "unknown"
    assert policy.resolve_client_ip("", "1.2.3.4") == "unknown"


def test_trusted_proxy_policy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERP_TRUSTED_PROXIES", raising=False)
    assert trusted_proxy_policy_from_env().trusted_proxies == ()
    monkeypatch.setenv("ERP_TRUSTED_PROXIES", "10.0.0.0/8, 192.168.0.0/16")
    assert trusted_proxy_policy_from_env().trusted_proxies == ("10.0.0.0/8", "192.168.0.0/16")


def test_describe_trusted_proxy_policy_reports_default() -> None:
    description = describe_trusted_proxy_policy(TrustedProxyPolicy())
    assert description["forwarded_headers_honoured"] is False
    assert "ignored" in description["default_behaviour"]


def _recorded_session_ip(env: SimpleNamespace) -> Optional[str]:
    with env.db.transaction() as conn:
        row = conn.execute("SELECT issued_to_ip FROM auth_tokens ORDER BY id DESC LIMIT 1").fetchone()
    return None if row is None else row["issued_to_ip"]


def test_spoofed_forwarded_header_does_not_change_recorded_session_ip(env: SimpleNamespace) -> None:
    """A spoofed X-Forwarded-For must not end up in the session record."""
    _token(env.client, "clerk_a", "Clerk#2026x")
    recorded = _recorded_session_ip(env)
    # The peer address observed by TestClient - never the forged header value.
    assert recorded is not None
    assert recorded != "1.2.3.4"


def test_spoofed_forwarded_header_with_trusted_proxy_is_rejected_from_untrusted_peer(env: SimpleNamespace) -> None:
    """Even with a trusted-proxy policy, a caller whose peer is NOT the trusted
    proxy cannot inject a client IP."""
    policy = TrustedProxyPolicy(["10.0.0.0/8"])  # TestClient's peer is not in this range
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth, trusted_proxies=policy)
    client = TestClient(app)
    _token(client, "clerk_a", "Clerk#2026x")
    # The forged chain is discarded; the direct peer address is recorded.
    assert _recorded_session_ip(env) != "1.2.3.4"


def test_trusted_proxy_peer_does_not_invent_a_client_address(env: SimpleNamespace) -> None:
    """A chain consisting only of the trusted peer itself yields that peer."""
    policy = TrustedProxyPolicy(["testclient"])  # Starlette TestClient peer name
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth, trusted_proxies=policy)
    client = TestClient(app)
    _token(client, "clerk_a", "Clerk#2026x")
    assert _recorded_session_ip(env) == "testclient"


def test_forwarded_header_is_never_used_for_authorisation(env: SimpleNamespace) -> None:
    """A forged forwarded header must not affect any authorisation decision."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    session = env.client.get(
        "/api/v1/auth/session",
        headers={**_auth(token), "X-Forwarded-For": "10.0.0.99", "X-Real-IP": "10.0.0.99"},
    )
    assert session.status_code == 200
    assert session.json()["company_id"] == 1
    assert session.json()["user_id"] == 1


def test_proxy_policy_env_configuration_is_fail_closed(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERP_TRUSTED_PROXIES", raising=False)
    app = create_erp_fastapi_app(env.db, env.vault, auth_manager=env.auth)
    assert app.state.proxy_policy.trusted_proxies == ()


# ===========================================================================
# Priority 2 — Authentication regression (re-verified against this commit)
# ===========================================================================


FORGED_HEADER_SETS = [
    {"X-User-Id": "1", "X-Company-Id": "1"},
    {"X-User-Id": "5", "X-Company-Id": "1"},
    {"X-User-Id": "20", "X-Company-Id": "1"},
    {"X-User-Id": "20", "X-Company-Id": "2"},
    {"X-User-Id": "9999", "X-Company-Id": "1"},
    {"X-User-Id": "-1", "X-Company-Id": "1"},
]

PROTECTED_GETS = [
    "/api/v1/auth/session",
    "/api/v1/invoices/1",
    "/api/v1/documents/does-not-exist/file",
    # Phase 27 review/navigation reads must be authenticated like the rest.
    "/api/v1/partners",
    "/api/v1/stock",
    "/api/v1/documents/does-not-exist/fields",
    "/api/v1/documents/does-not-exist/content",
    # Phase 28 user administration must be authenticated like the rest.
    "/api/v1/users",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
@pytest.mark.parametrize("forged", FORGED_HEADER_SETS)
def test_forged_identity_headers_cannot_authenticate(env: SimpleNamespace, path: str, forged: Dict[str, str]) -> None:
    resp = env.client.get(path, headers=forged)
    assert resp.status_code == 401, f"{path} with {forged} -> {resp.status_code}: {resp.text}"
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_forged_headers_cannot_switch_company_for_authenticated_user(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = env.client.get(
        "/api/v1/auth/session",
        headers={**_auth(token), "X-User-Id": "20", "X-Company-Id": "2"},
    )
    assert resp.status_code == 200
    principal = resp.json()
    assert principal["user_id"] == 1
    assert principal["company_id"] == 1
    assert principal["role"] == "clerk"


@pytest.mark.parametrize(
    "authorization",
    [None, "", "   ", "Bearer", "Bearer ", "Basic YWRtaW46cGFzcw==", "Token abc", "dzsess_abc", "Bearer dzsess_bogus"],
)
def test_missing_and_malformed_credentials_rejected(env: SimpleNamespace, authorization: Optional[str]) -> None:
    headers = {"Authorization": authorization} if authorization is not None else {}
    resp = env.client.get("/api/v1/auth/session", headers=headers)
    assert resp.status_code == 401, f"{authorization!r} -> {resp.status_code}"


def test_invalid_token_is_rejected(env: SimpleNamespace) -> None:
    from dz_erp.auth import generate_session_token

    for _ in range(3):
        resp = env.client.get("/api/v1/auth/session", headers=_auth(generate_session_token()))
        assert resp.status_code == 401


def test_tampered_token_is_rejected(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    body = token[len("dzsess_") :]
    tampered = "dzsess_" + body[:-1] + ("A" if body[-1] != "A" else "B")
    assert env.client.get("/api/v1/auth/session", headers=_auth(tampered)).status_code == 401


def test_revoked_token_is_rejected(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    assert env.client.get("/api/v1/auth/session", headers=_auth(token)).status_code == 200
    assert env.client.post("/api/v1/auth/revoke", json={}, headers=_auth(token)).status_code == 200
    assert env.client.get("/api/v1/auth/session", headers=_auth(token)).status_code == 401
    # Replay after revocation still fails.
    assert env.client.get("/api/v1/auth/session", headers=_auth(token)).status_code == 401


def test_expired_token_is_rejected(env: SimpleNamespace) -> None:
    issued = env.auth.issue_token("clerk_a", "Clerk#2026x", ttl_seconds=60)
    token = issued["access_token"]
    assert env.client.get("/api/v1/auth/session", headers=_auth(token)).status_code == 200
    # Expire the token by moving its stored expiry into the past.
    with env.db.transaction() as conn:
        conn.execute("UPDATE auth_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert env.client.get("/api/v1/auth/session", headers=_auth(token)).status_code == 401


def test_inactive_user_token_is_rejected(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    with env.db.transaction() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = 1")
    resp = env.client.get("/api/v1/auth/session", headers=_auth(token))
    assert resp.status_code == 401


def test_inactive_user_cannot_obtain_a_token(env: SimpleNamespace) -> None:
    resp = env.client.post("/api/v1/auth/token", json={"username": "revoked_a", "password": "Revoked#2026x"})
    assert resp.status_code == 401


def test_cross_company_access_is_denied_on_invoice_read(env: SimpleNamespace) -> None:
    from dz_erp.procurement_stock import create_and_validate_supplier_invoice

    doc = _upload(env.client, _token(env.client, "clerk_a", "Clerk#2026x"), "invoice.pdf", _pdf(), "application/pdf")
    assert doc.status_code == 200
    inv = create_and_validate_supplier_invoice(
        env.db,
        company_id=1,
        user_id=4,
        supplier_id=10,
        supplier_invoice_no=SUPPLIER_SPEC["invoice_number"],
        invoice_date=SUPPLIER_SPEC["invoice_date"],
        document_id=doc.json()["document_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"description": "Service", "qty": "1", "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    inv_id = inv["invoice_id"]

    token_b = _token(env.client, "admin_b", "AdminB#2026x")
    assert env.client.get(f"/api/v1/invoices/{inv_id}", headers=_auth(token_b)).status_code == 403
    assert env.client.get(f"/api/v1/documents/{doc.json()['document_id']}/file", headers=_auth(token_b)).status_code == 403


def test_cross_company_write_operations_are_denied(env: SimpleNamespace) -> None:
    from dz_erp.procurement_stock import create_and_validate_supplier_invoice

    doc = _upload(env.client, _token(env.client, "clerk_a", "Clerk#2026x"), "invoice.pdf", _pdf(), "application/pdf")
    inv = create_and_validate_supplier_invoice(
        env.db,
        company_id=1,
        user_id=4,
        supplier_id=10,
        supplier_invoice_no="FAC-P8-X",
        invoice_date="2026-10-15",
        document_id=doc.json()["document_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"description": "Service", "qty": "1", "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    inv_id = inv["invoice_id"]
    token_b = _token(env.client, "admin_b", "AdminB#2026x")

    for method, path, body in [
        ("POST", f"/api/v1/invoices/{inv_id}/submit-approval", None),
        ("POST", f"/api/v1/invoices/{inv_id}/decide-approval", {"approve": True}),
    ]:
        resp = env.client.request(method, path, json=body, headers=_auth(token_b))
        assert resp.status_code not in range(200, 300), f"{method} {path} -> {resp.status_code}"


def test_unauthorized_financial_operation_is_denied(env: SimpleNamespace) -> None:
    from dz_erp.procurement_stock import create_and_validate_supplier_invoice

    doc = _upload(env.client, _token(env.client, "clerk_a", "Clerk#2026x"), "invoice.pdf", _pdf(), "application/pdf")
    inv = create_and_validate_supplier_invoice(
        env.db,
        company_id=1,
        user_id=4,
        supplier_id=10,
        supplier_invoice_no="FAC-P8-RBAC",
        invoice_date="2026-10-15",
        document_id=doc.json()["document_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"description": "Service", "qty": "1", "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    inv_id = inv["invoice_id"]

    clerk = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = env.client.post(
        f"/api/v1/invoices/{inv_id}/post",
        headers={**_auth(clerk), "Idempotency-Key": "p8-rbac-key"},
    )
    assert resp.status_code == 403, resp.text


def test_audit_chain_remains_valid_after_phase8_activity(env: SimpleNamespace) -> None:
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    _upload(env.client, token, "invoice.pdf", _pdf(), "application/pdf")
    env.client.post("/api/v1/auth/revoke", json={}, headers=_auth(token))
    chain = env.db.verify_audit_chain(company_id=1)
    assert chain["valid"] is True, chain


def test_all_protected_routes_use_the_authenticated_path(env: SimpleNamespace) -> None:
    """Every non-public route must reject an unauthenticated request."""
    public = {"/healthz", "/api/v1/upload-policy", "/api/v1/auth/token"}
    schema = env.app.openapi()
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            full = f"{method.upper()} {path}"
            if path in public:
                continue
            resp = env.client.request(method, path, json={})
            assert resp.status_code == 401, f"{full} accepted an unauthenticated request -> {resp.status_code}"


# ----------------------------------------------------------------------
# Malformed documents (structurally invalid, header-valid)
# ----------------------------------------------------------------------
# These payloads carry a correct magic-byte header but are not parseable
# documents.  Before the fix they escaped the security boundary as an
# unhandled pypdf/PIL exception, i.e. HTTP 500, which both denied service to
# the caller and told an attacker the payload had reached the parser.
MALFORMED_DOCUMENTS = [
    ("truncated.pdf", b"%PDF-1.7", "application/pdf", "PDF magic bytes only"),
    ("header_only.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf", "header plus a bare EOF marker"),
    (
        "broken_xref.pdf",
        b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer<</Root 1 0 R>>\n",
        "application/pdf",
        "object and trailer with no cross-reference table",
    ),
    (
        "unterminated_stream.pdf",
        b"%PDF-1.4\n1 0 obj\n<</Length 99>>\nstream\nshort\nendobj\n",
        "application/pdf",
        "stream shorter than its declared length",
    ),
    ("truncated.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "image/png", "PNG signature with no IHDR"),
    ("truncated.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 40, "image/jpeg", "JPEG SOI with no frame data"),
]


@pytest.mark.parametrize(
    "filename,content,mime,label",
    MALFORMED_DOCUMENTS,
    ids=[case[3] for case in MALFORMED_DOCUMENTS],
)
def test_structurally_invalid_document_is_rejected_not_raised(
    env: SimpleNamespace, filename: str, content: bytes, mime: str, label: str
) -> None:
    """A header-valid but unparseable document must yield a clean 4xx, never a 500."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, filename, content, mime)
    assert resp.status_code == 400, f"{label}: expected 400, got {resp.status_code}: {resp.text[:200]}"
    # The rejection must be actionable rather than an opaque internal error.
    assert "structurally valid" in resp.text, f"{label}: {resp.text[:200]}"
    # And nothing may be persisted for a document that was never parsed.
    assert _count(env, "source_documents") == 0
    assert _count(env, "ocr_extracted_fields") == 0


def test_malformed_documents_leave_no_vault_artifact(env: SimpleNamespace) -> None:
    """Rejection must happen before anything is written to the encrypted vault."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    for filename, content, mime, _label in MALFORMED_DOCUMENTS:
        _upload(env.client, token, filename, content, mime)
    stored = list((env.tmp_path / "vault").rglob("*.enc"))
    assert stored == [], f"vault retained encrypted files: {stored[:5]}"


def test_malformed_pdf_does_not_corrupt_the_audit_chain(env: SimpleNamespace) -> None:
    """A rejected document must not break hash-chain continuity for the tenant."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    for filename, content, mime, _label in MALFORMED_DOCUMENTS:
        _upload(env.client, token, filename, content, mime)
    _upload(env.client, token, "good.pdf", _pdf(), "application/pdf")
    assert env.db.verify_audit_chain(company_id=1)["valid"] is True


# ----------------------------------------------------------------------
# Filename hardening: control characters and Windows device names
# ----------------------------------------------------------------------
HOSTILE_FILENAMES = [
    ("a\nb.pdf", "embedded newline"),
    ("a\rb.pdf", "embedded carriage return"),
    ("a\tb.pdf", "embedded tab"),
    ("a\x01b.pdf", "C0 control character"),
    ("a\x1bb.pdf", "terminal escape sequence"),
    ("a\x7fb.pdf", "DEL character"),
    ("con.pdf", "Windows reserved device name CON"),
    ("CON.PDF", "reserved device name, upper case"),
    ("prn.pdf", "reserved device name PRN"),
    ("aux.pdf", "reserved device name AUX"),
    ("nul.pdf", "reserved device name NUL"),
    ("com1.pdf", "reserved device name COM1"),
    ("lpt1.pdf", "reserved device name LPT1"),
]

BENIGN_FILENAMES = [
    "ok.pdf",
    "invoice-2026-0001.pdf",
    "conveyor.pdf",
    "auxiliary.pdf",
    "com1x.pdf",
    "nullement.pdf",
    "PRN-2026.pdf",
    "a b.pdf",
    "scan.png",
]


@pytest.mark.parametrize("filename,label", HOSTILE_FILENAMES, ids=[c[1] for c in HOSTILE_FILENAMES])
def test_upload_rejects_control_characters_and_reserved_device_names(filename: str, label: str) -> None:
    """Neither can traverse a path, but both corrupt logs, terminals and Windows hosts."""
    with pytest.raises(UploadValidationError):
        _validate_filename_shape(filename)


@pytest.mark.parametrize("filename", BENIGN_FILENAMES, ids=BENIGN_FILENAMES)
def test_upload_accepts_ordinary_filenames(filename: str) -> None:
    """The hardening must be exact-match, not a prefix match on the stem."""
    assert _validate_filename_shape(filename) == filename


def test_upload_endpoint_rejects_hostile_filename_over_http(env: SimpleNamespace) -> None:
    """The reserved-device-name rejection must hold end to end, not only at the validator."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    for filename, _label in HOSTILE_FILENAMES:
        if filename.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
            resp = _upload(env.client, token, filename, _pdf(), "application/pdf")
            assert resp.status_code == 400, f"{filename!r} -> {resp.status_code}: {resp.text[:160]}"
            assert _count(env, "source_documents") == 0


def test_upload_never_stores_a_filename_with_control_characters(env: SimpleNamespace) -> None:
    """Whatever the multipart layer does with control characters, none may reach storage.

    The multipart parser percent-encodes them, so a raw newline arrives as the
    literal text ``%0A``.  That neutralisation belongs to a third-party parser,
    not to this code, so the assertion is on the *invariant* - no stored
    filename contains a control character - rather than on the parser's chosen
    encoding.
    """
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    for filename, _label in HOSTILE_FILENAMES[:6]:
        _upload(env.client, token, filename, _pdf(), "application/pdf")
    with env.db.transaction() as conn:
        stored = [row["filename"] for row in conn.execute("SELECT filename FROM source_documents").fetchall()]
    assert stored, "no documents were stored, so the invariant is untested"
    offenders = [name for name in stored if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name)]
    assert offenders == [], f"stored filenames contain control characters: {offenders!r}"


# ----------------------------------------------------------------------
# OpenAPI contract accuracy
# ----------------------------------------------------------------------
def test_openapi_contract_declares_exactly_the_public_operations(env: SimpleNamespace) -> None:
    """The published security requirement must match what the code actually serves.

    Declaring a protected route public is the dangerous direction.  The reverse -
    which this guards against - overstates the boundary: a generated client
    sends credentials it does not need, and an auditor reading the schema alone
    concludes a route is protected when it is not.
    """
    schema = env.app.openapi()
    public, protected = [], []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            (public if not operation.get("security") else protected).append(f"{method.upper()} {path}")

    assert sorted(public) == [
        "GET /api/v1/upload-policy",
        "GET /healthz",
        "POST /api/v1/auth/token",
    ], f"unexpected public set: {sorted(public)}"
    # 14 original protected operations + the 5 Phase 25 read/report endpoints
    # (accounts, products, documents, invoices, accounting/trial-balance)
    # + the 4 Phase 27 review/navigation endpoints (partners, stock,
    # documents/{id}/fields, documents/{id}/content)
    # + the 3 Phase 28 user-administration endpoints (list, create,
    # deactivate).
    # + the 2 Phase 29 field-review endpoints (POST fields/decide,
    # GET review-policy).
    # + the Phase 33 invoice re-validation endpoint (POST
    # invoices/{invoice_id}/revalidate), which replaces the out-of-band repair
    # P32-F2 had no supported path for.
    # + the Phase 39 rate-limit policy endpoint (GET system/rate-limit-policy),
    # which reports the live throttling policy to administrators only.
    # + the 3 Phase 46 timbre-rule administration endpoints (GET and POST
    # tax/timbre-rules, POST tax/timbre-rules/{version_code}/approve), added to
    # close finding R-05: without them the only way to adopt a new stamp-duty
    # rule was raw SQL, which produced no actor, reason or audit event.
    # The count pin catches accidental surface changes; the desktop read
    # endpoints are authenticated like every other protected operation.
    assert len(protected) == 33, (
        f"expected 33 protected operations, got {len(protected)}: {sorted(protected)}"
    )


def test_openapi_contract_declares_no_identity_headers_as_parameters(env: SimpleNamespace) -> None:
    """No operation may accept X-User-Id / X-Company-Id / X-Role as a parameter.

    The strings legitimately appear in the BearerAuth *description*, which states
    that they are not accepted; what must never appear is a parameter that would
    let FastAPI bind them.
    """
    schema = env.app.openapi()
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            names = {p.get("name", "").lower() for p in operation.get("parameters", []) or []}
            forbidden = names & {"x-user-id", "x-company-id", "x-role"}
            assert not forbidden, f"{method.upper()} {path} declares identity parameters {forbidden}"
    assert "BearerAuth" in schema["components"]["securitySchemes"]


# ----------------------------------------------------------------------
# Audit-chain key management (Workstream D)
# ----------------------------------------------------------------------
def test_file_backed_database_never_uses_the_published_default_key(tmp_path: Path) -> None:
    """The default HMAC key is public, so a persistent database must not use it.

    Before this, ``ERPDatabase`` fell back to a key hardcoded in the source tree
    whenever ``ERP_AUDIT_HMAC_SECRET`` was unset.  Anyone who has read the
    repository could then forge an arbitrary audit chain, silently - no error, no
    warning, and the chain still "verified" because it verified against the
    attacker's own key.
    """
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    assert db._hmac_secret != DEFAULT_AUDIT_HMAC_SECRET


def test_audit_key_is_generated_beside_the_database_with_owner_only_permissions(
    tmp_path: Path, assert_owner_only_permissions
) -> None:
    """The generated key must be stable across restarts and unreadable by others."""

    db_path = tmp_path / "erp.sqlite3"
    first = ERPDatabase(str(db_path))._hmac_secret
    key_file = tmp_path / (db_path.name + AUDIT_KEY_SUFFIX)

    assert key_file.exists(), "no key file was generated beside the database"
    assert_owner_only_permissions(key_file)
    # Stable across a reopen: otherwise verify_audit_chain would fail on restart.
    assert ERPDatabase(str(db_path))._hmac_secret == first


def test_audit_key_precedence_is_explicit_then_environment_then_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most-trusted source wins, and the in-memory default is still reachable."""
    db_path = tmp_path / "erp.sqlite3"
    ERPDatabase(str(db_path))  # creates the key file

    explicit = ERPDatabase(str(db_path), hmac_secret=b"explicit")
    assert explicit._hmac_secret == b"explicit"

    monkeypatch.setenv("ERP_AUDIT_HMAC_SECRET", "from-environment")
    assert ERPDatabase(str(db_path))._hmac_secret == b"from-environment"

    monkeypatch.delenv("ERP_AUDIT_HMAC_SECRET")
    from_file = ERPDatabase(str(db_path))._hmac_secret
    assert from_file != b"from-environment" and from_file != DEFAULT_AUDIT_HMAC_SECRET

    # An ephemeral in-memory database keeps the documented default, which the
    # test suite relies on.
    assert ERPDatabase(":memory:")._hmac_secret == DEFAULT_AUDIT_HMAC_SECRET


def test_audit_chain_still_verifies_after_restart_with_a_generated_key(tmp_path: Path) -> None:
    """The generated key must not break the property it exists to protect."""
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'SARL Atlas', 'DZ', 'dz', '2026-01-01')"
        )
        db.append_audit_event(
            conn, company_id=1, actor_user_id=None, action_type="TEST_EVENT",
            target_model="companies", target_id="1", after_state={"ok": True},
        )
    assert db.verify_audit_chain(company_id=1)["valid"] is True

    # Reopen: a different key would make the chain unverifiable.
    reopened = ERPDatabase(str(db_path))
    assert reopened.verify_audit_chain(company_id=1)["valid"] is True


def test_backup_and_restore_preserve_the_audit_key(env: SimpleNamespace) -> None:
    """A restored database must be able to verify the chain it was backed up with.

    The audit key travels inside the Fernet-encrypted archive, under the same
    protection as the vault key.  Without it, the restored database would resolve
    a *different* key and every chain would fail verification.
    """
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    _upload(env.client, token, "backup.pdf", _pdf(), "application/pdf")

    meta = create_encrypted_backup(
        db=env.db,
        vault=env.vault,
        output_backup_path=env.tmp_path / "backups" / "b.enc",
        backup_encryption_key=env.vault.encryption_key,
    )
    report = restore_and_verify_backup(
        backup_path=Path(meta["backup_path"]),
        backup_encryption_key=env.vault.encryption_key,
        restore_target_dir=env.tmp_path / "isolated",
    )
    assert report["db_sha256_verified"] is True
    assert report["audit_chains_valid"] is True


# ----------------------------------------------------------------------
# Vault key persistence (Workstream D)
# ----------------------------------------------------------------------
def test_vault_key_is_persisted_so_documents_survive_a_restart(
    tmp_path: Path, assert_owner_only_permissions
) -> None:
    """A fresh key per process silently destroys every stored document.

    ``PrivateDocumentVault`` used to call ``Fernet.generate_key()`` whenever no
    key was supplied.  The key then lived only in memory, so the next process
    started with a different one and every document already in the vault raised
    ``InvalidToken`` - permanently, with no error at the time of storage.  Tests
    never caught it because each test builds its vault in the same process that
    wrote to it.
    """
    vault_root = tmp_path / "vault"
    first = PrivateDocumentVault(vault_root)
    stored_path = first.store_bytes(company_id=1, file_sha256="a" * 64, content=b"SECRET INVOICE BYTES")

    key_file = tmp_path / (vault_root.name + VAULT_KEY_SUFFIX)
    assert key_file.exists(), "no key file was generated beside the vault"
    assert_owner_only_permissions(key_file)

    # A new instance over the same directory must decrypt what the first wrote.
    restarted = PrivateDocumentVault(vault_root)
    assert restarted.read_bytes(1, 1, stored_path) == b"SECRET INVOICE BYTES"
    assert restarted.encryption_key == first.encryption_key


def test_vault_key_is_not_stored_inside_the_encrypted_directory(tmp_path: Path) -> None:
    """The key must not sit among the blobs it protects."""
    vault_root = tmp_path / "vault"
    PrivateDocumentVault(vault_root).store_bytes(1, "b" * 64, b"x")
    assert list(vault_root.rglob("*" + VAULT_KEY_SUFFIX)) == []


def test_vault_key_precedence_and_failure_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit, then environment, then the generated file - and bad input fails loudly."""
    from cryptography.fernet import Fernet

    vault_root = tmp_path / "vault"
    PrivateDocumentVault(vault_root)

    explicit = Fernet.generate_key()
    assert PrivateDocumentVault(vault_root, encryption_key=explicit).encryption_key == explicit

    monkeypatch.setenv(VAULT_KEY_ENV, Fernet.generate_key().decode())
    from_env = os.environ[VAULT_KEY_ENV]
    assert PrivateDocumentVault(vault_root).encryption_key.decode() == from_env

    # A malformed key must raise rather than silently mint a new one, because a
    # silent new key is exactly the failure mode this guards against.
    monkeypatch.setenv(VAULT_KEY_ENV, "not-a-fernet-key")
    with pytest.raises(ValueError):
        PrivateDocumentVault(vault_root)


# ----------------------------------------------------------------------
# Restore must leave a usable deployment (Workstream F)
# ----------------------------------------------------------------------
def test_restored_database_and_vault_are_reopenable_without_the_original_secrets(
    tmp_path: Path, assert_owner_only_permissions
) -> None:
    """A restore that only works through the returned objects is not a restore.

    The keys were passed in memory, so ``restore_and_verify_backup`` verified
    its own work and reported success - while leaving no key on disk.  Any
    later process opening the restored artefacts resolved a brand-new key, so
    the audit chain read as ``EVENT_HASH_TAMPERED`` and every document raised
    ``InvalidToken``.  Disaster recovery is precisely "open it later", so the
    keys have to survive the restore.
    """
    from dz_erp.database import ERPDatabase as _ERPDB
    from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

    root = tmp_path
    db = _ERPDB(str(root / "src.sqlite3"))
    vault = PrivateDocumentVault(root / "vault")
    stored_path = vault.store_bytes(1, "c" * 64, b"SECRET INVOICE BYTES")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at) VALUES (1,'C','N','DZ','2026-01-01')"
        )
    with db.transaction() as conn:
        db.append_audit_event(
            conn=conn, company_id=1, actor_user_id=None, action_type="RESTORE_PROBE",
            target_model="probe", target_id="1", after_state={"k": "v"},
        )

    meta = create_encrypted_backup(
        db=db, vault=vault, output_backup_path=root / "b.enc", backup_encryption_key=vault.encryption_key
    )
    report = restore_and_verify_backup(
        backup_path=Path(meta["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=root / "out",
    )
    assert report["audit_chains_valid"] is True

    out = root / "out"
    audit_key = out / "erp_snapshot.sqlite3.audit.key"
    vault_key = out / "vault.vault.key"
    assert_owner_only_permissions(audit_key)
    assert_owner_only_permissions(vault_key)

    # Reopen exactly as a recovering deployment would: no secrets supplied.
    reopened_db = _ERPDB(str(out / "erp_snapshot.sqlite3"))
    assert reopened_db.verify_audit_chain(company_id=1)["valid"] is True
    reopened_vault = PrivateDocumentVault(out / "vault")
    assert reopened_vault.read_bytes(1, 1, stored_path) == b"SECRET INVOICE BYTES"


def test_vault_read_reanchors_a_stale_absolute_path(tmp_path: Path) -> None:
    """``source_documents.vault_path`` stores an absolute path.

    After a restore into a different directory every one of those paths dangles
    and the documents become unreadable.  The blob is named by its content
    SHA-256, so re-anchoring by basename is safe and confined to the company
    directory - and must not weaken the traversal guard.
    """
    from dz_erp.database import TenantIsolationError

    source_root = tmp_path / "original"
    vault = PrivateDocumentVault(source_root)
    stored_path = vault.store_bytes(1, "d" * 64, b"DOC")

    moved_root = tmp_path / "relocated"
    # Same key, different root - only the stored path is stale.
    moved = PrivateDocumentVault(moved_root, encryption_key=vault.encryption_key)
    shutil.copytree(source_root / "company_1", moved_root / "company_1")
    assert moved.read_bytes(1, 1, stored_path) == b"DOC", "a relocated vault orphaned its own documents"

    for hostile in ("../../../etc/passwd", "/etc/passwd", str(tmp_path / "elsewhere" / "company_1" / "x.enc")):
        with pytest.raises(TenantIsolationError):
            moved.read_bytes(1, 1, hostile)


# ----------------------------------------------------------------------
# The deployment entry point (Workstream F)
# ----------------------------------------------------------------------
def test_serve_builds_a_working_app_and_persists_the_vault_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``dz_erp/serve.py`` is the only deployment surface, so it must be exercised.

    Importing it builds the ASGI app at module level, which is exactly what
    ``uvicorn dz_erp.serve:app`` does in production.  Run it in a subprocess so
    the import happens with a clean environment and the real code path is used.
    """
    import subprocess
    import sys

    db_path = tmp_path / "erp.sqlite3"
    vault_path = tmp_path / "vault"
    env = dict(os.environ)
    env.update({"ERP_DB_PATH": str(db_path), "ERP_VAULT_PATH": str(vault_path)})
    env.pop("ERP_TRUSTED_PROXIES", None)
    env.pop("ERP_AUDIT_HMAC_SECRET", None)

    script = (
        "import dz_erp.serve as s, sys\n"
        "from fastapi.testclient import TestClient\n"
        "c = TestClient(s.app)\n"
        "r = c.get('/healthz')\n"
        "print(r.status_code)\n"
        "print(len(s.app.state.proxy_policy.trusted_proxies))\n"
        # The vault key must be on disk, not minted per process.
        "v = s.app.state.vault\n"
        "p = v.store_bytes(1, 'a'*64, b'DOC')\n"
        "from dz_erp.intake_ocr import PrivateDocumentVault\n"
        "print(PrivateDocumentVault(v.vault_root).read_bytes(1, 1, p) == b'DOC')\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.split()
    assert lines[0] == "200", f"healthz did not return 200: {proc.stdout}"
    assert lines[1] == "0", "the entry point did not run with a fail-closed proxy policy"
    assert lines[2] == "True", "the vault key was not persisted by the entry point"


def test_serve_refuses_to_start_without_durable_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh deployment must fail loudly, not run against a throwaway file."""

    from dz_erp import serve

    # Test the factory directly: reloading the module re-runs its import-time
    # ``app = build_app()``, so an unconfigured environment would abort before
    # the assertion is reached.
    monkeypatch.delenv("ERP_DB_PATH", raising=False)
    monkeypatch.delenv("ERP_VAULT_PATH", raising=False)
    monkeypatch.setenv("ERP_VAULT_PATH", str(tmp_path / "vault"))
    with pytest.raises(SystemExit):
        serve.build_app()
    monkeypatch.setenv("ERP_DB_PATH", str(tmp_path / "erp.sqlite3"))
    monkeypatch.delenv("ERP_VAULT_PATH", raising=False)
    with pytest.raises(SystemExit):
        serve.build_app()

    monkeypatch.setenv("ERP_VAULT_PATH", str(tmp_path / "vault"))
    app = serve.build_app()
    assert app.state.db is not None and app.state.vault is not None


def test_serve_bootstraps_the_first_admin_and_then_serves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty database yields an API where everything 401s and nothing explains why."""
    from dz_erp import serve

    db_path = tmp_path / "erp.sqlite3"
    monkeypatch.setenv("ERP_DB_PATH", str(db_path))
    monkeypatch.setenv("ERP_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setattr(serve.getpass, "getpass", lambda prompt="": "Bootstrap#2026")

    rc = serve.main(["--bootstrap-admin", "admin1", "--company-name", "SARL Bootstrap"])
    assert rc == 0
    assert db_path.exists()

    # With a user present the pre-flight check passes and the server would be
    # started - stubbed out so the test does not bind a port.
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(serve, "_has_any_user", lambda p: True)
    assert serve.main(["--host", "127.0.0.1", "--port", "0"]) == 0
    assert len(started) == 1, "main() did not hand the built app to uvicorn"

    # Without one it refuses to start, with instructions on stderr.
    monkeypatch.setattr(serve, "_has_any_user", lambda p: False)
    assert serve.main([]) == 2
    assert len(started) == 1, "main() started a server despite having no users"
