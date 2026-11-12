# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 58 - selective security integration (upload RBAC, payload digest, body
limits, hardening headers, middleware order, backup custody).

Scope
-----
This module is the regression evidence for the *selective* integration of the
remediation workspace's security fixes into the Phase-29..57 line of
development. Only the findings that were independently confirmed to be missing
upstream are fixed here:

  * SEC-01 (upload half)  - the document-upload endpoint consulted no role at
    all, so ``warehouse``, ``auditor``, ``ocr_worker`` and ``treasury`` could
    create documents. The OCR *field-edit* half was already enforced upstream by
    ``REVIEW_CAPABLE_ROLES`` and is not touched.
  * SEC-05 - ``line_ht_dzd`` is optional in the request model, but the payload
    digest formats ``line["line_ht_dzd"]``; an omitted amount therefore raised
    ``KeyError`` (after the upstream ``.get(...)`` change, ``TypeError`` before
    it) and the request answered 500. The amount is now derived from the same
    rule the validator compares against, *before* the digest is computed.
  * SEC-07 - the encrypted backup archive (and its staging directory, which
    carries the vault key, the audit HMAC secret and both key rings in clear)
    was created with the process umask, i.e. world-readable on a default Linux
    install.
  * SEC-08 - every JSON endpoint, including the unauthenticated token endpoint,
    buffered a body of any size before any rule ran.
  * SEC-10 - no browser-facing hardening headers were emitted at all.

SEC-02 (non-finite decimals) and SEC-12 (rate limiting) are already implemented
upstream and deliberately not duplicated; their upstream tests are the evidence.
SEC-11 is a dependency-lock question (see the integration report), not code.

The tests are written against observable behaviour (HTTP status, stored rows,
file modes, structured log records) so a future refactor of the fix cannot make
them pass vacuously.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from dz_erp.api import (
    DEFAULT_MAX_JSON_REQUEST_BYTES,
    MULTIPART_FRAMING_ALLOWANCE,
    UPLOAD_PATH,
    _configured_max_request_bytes,
    create_erp_fastapi_app,
)
from dz_erp.auth import hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.procurement_stock import compute_invoice_payload_hash, compute_line_ht_dzd
from dz_erp.rate_limit import RequestThrottle
from dz_erp.security_audit import create_encrypted_backup, safe_restore_supported

# (user_id, company_id, username, role, password)
USERS = [
    (1, 1, "clerk_a", "clerk", "Clerk#2026x"),
    (2, 1, "wh_a", "warehouse", "Wh#2026x"),
    (3, 1, "approver_a", "approver", "Appr#2026x"),
    (4, 1, "acct_a", "accountant", "Acct#2026x"),
    (5, 1, "auditor_a", "auditor", "Aud#2026x"),
    (6, 1, "ocr_a", "ocr_worker", "Ocr#2026x"),
    (7, 1, "admin_a", "admin", "Adm#2026x"),
    (8, 1, "treasury_a", "treasury", "Trez#2026x"),
    (20, 2, "admin_b", "admin", "AdminB#2026x"),
]

#: Roles ``docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md`` grants "Upload Document".
AUTHORIZED_UPLOADERS = ["clerk_a", "approver_a", "acct_a", "admin_a"]
#: Roles the same matrix explicitly denies.
DENIED_UPLOADERS = ["wh_a", "auditor_a", "ocr_a", "treasury_a"]

HEADER_KEYS = ("x-content-type-options", "referrer-policy", "x-frame-options")


def _supplier_spec(nonce: str) -> Dict[str, Any]:
    return {
        "supplier_name": "Supplier Alpha",
        "invoice_number": f"FAC-SEC58-{nonce}",
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
            {
                "sku": "S1",
                "description": "Service",
                "qty": "1.00",
                "unit_price_dzd": "10000.00",
                "tax_rate": "19.0",
                "line_ht_dzd": "10000.00",
            }
        ],
    }


def _invoice_body(**overrides: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "supplier_id": 10,
        "supplier_invoice_no": "FAC-SEC58-001",
        "invoice_date": "2026-10-15",
        "payment_method": "BANK_TRANSFER",
        "amount_ht_dzd": "10000.00",
        "amount_tva_dzd": "1900.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "11900.00",
        "lines": [{"description": "Service", "qty": "1.00", "unit_price_dzd": "10000.00", "tax_rate": "19.0"}],
    }
    body.update(overrides)
    return body


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    """Two-tenant ERP, one supplier, no pre-existing documents."""
    db = ERPDatabase(str(tmp_path / "sec58.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        for cid, code in ((1, "DZ1"), (2, "DZ2")):
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
                " VALUES (?, ?, ?, 'DZ', 'dz', '2026-01-01')",
                (cid, code, f"Tenant {code}"),
            )
        for uid, cid, username, role, password in USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (uid, cid, username, role, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc) VALUES (10, 1, 'Supplier Alpha', 'supplier', 'DZ', 'REEL',"
            " '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
        )
    app = create_erp_fastapi_app(db, vault)
    client = TestClient(app)
    return SimpleNamespace(db=db, vault=vault, client=client, app=app, tmp_path=tmp_path)


def _token(env: SimpleNamespace, username: str) -> str:
    password = next(p for _uid, _cid, name, _role, p in USERS if name == username)
    resp = env.client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _count(env: SimpleNamespace, table: str) -> int:
    with env.db.transaction() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _audit_actions(env: SimpleNamespace) -> List[str]:
    with env.db.transaction() as conn:
        return [r["action_type"] for r in conn.execute("SELECT action_type FROM audit_events ORDER BY id")]


def _vault_files(env: SimpleNamespace) -> List[Path]:
    root = env.vault.vault_root
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _upload(env: SimpleNamespace, token: str, nonce: str, extra_headers: Dict[str, str] | None = None):
    pdf = render_synthetic_invoice_pdf(_supplier_spec(nonce))
    headers = {**_auth(token), **(extra_headers or {})}
    return env.client.post(
        "/api/v1/documents/upload",
        files={"file": (f"{nonce}.pdf", pdf, "application/pdf")},
        headers=headers,
    )


def _events(app: Any) -> List[Dict[str, Any]]:
    return [dict(record) for record in app.state.logger.records]


# ---------------------------------------------------------------------------
# SEC-01 - upload authorization (the half that was missing upstream)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("username", AUTHORIZED_UPLOADERS)
def test_roles_the_matrix_permits_can_upload(env: SimpleNamespace, username: str) -> None:
    """Positive control: the fix must not lock out the roles the matrix allows."""
    before = _count(env, "source_documents")
    resp = _upload(env, _token(env, username), f"ok-{username}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"]
    assert _count(env, "source_documents") == before + 1


@pytest.mark.parametrize("username", DENIED_UPLOADERS)
def test_roles_the_matrix_denies_cannot_upload(env: SimpleNamespace, username: str) -> None:
    """SEC-01: before the fix every one of these roles received HTTP 200."""
    resp = _upload(env, _token(env, username), f"deny-{username}")
    assert resp.status_code == 403, resp.text
    assert "not authorized for document upload" in resp.json()["detail"]


@pytest.mark.parametrize("username", ["wh_a", "auditor_a"])
def test_refused_upload_creates_no_document_row(env: SimpleNamespace, username: str) -> None:
    before = _count(env, "source_documents")
    assert _upload(env, _token(env, username), f"norow-{username}").status_code == 403
    assert _count(env, "source_documents") == before


@pytest.mark.parametrize("username", ["wh_a", "ocr_a"])
def test_refused_upload_leaves_no_orphaned_vault_file(env: SimpleNamespace, username: str) -> None:
    """The refusal must happen before the bytes are stored, not after."""
    before = _vault_files(env)
    assert _upload(env, _token(env, username), f"nofile-{username}").status_code == 403
    assert _vault_files(env) == before


def test_refused_upload_is_audited(env: SimpleNamespace) -> None:
    assert _upload(env, _token(env, "wh_a"), "audited").status_code == 403
    actions = _audit_actions(env)
    assert "SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED" in actions
    chain = env.db.verify_audit_chain(1)
    assert chain["valid"] is True, chain


def test_upload_requires_authentication(env: SimpleNamespace) -> None:
    pdf = render_synthetic_invoice_pdf(_supplier_spec("noauth"))
    resp = env.client.post(
        "/api/v1/documents/upload",
        files={"file": ("noauth.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 401
    assert _count(env, "source_documents") == 0


@pytest.mark.parametrize(
    "spoofed",
    [
        {"X-Role": "admin"},
        {"X-User-Role": "approver"},
        {"X-User-Id": "7", "X-Company-Id": "1"},
        {"X-Authenticated-Role": "accountant"},
    ],
)
def test_spoofed_identity_headers_do_not_authorize_upload(
    env: SimpleNamespace, spoofed: Dict[str, str]
) -> None:
    """The role comes from the credential's user row, never from the request."""
    resp = _upload(env, _token(env, "wh_a"), "spoof", extra_headers=spoofed)
    assert resp.status_code == 403, resp.text
    assert _count(env, "source_documents") == 0


def test_field_review_guard_is_unchanged_upstream(env: SimpleNamespace) -> None:
    """Control: the OCR field-edit path keeps upstream's own enforcement.

    A denied role is refused by ``REVIEW_CAPABLE_ROLES`` inside
    ``update_extracted_field_by_human``; the integration added no second guard
    there, so the refusal must still be a 403 (not a 500, not a 200).
    """
    clerk = _token(env, "clerk_a")
    upload = _upload(env, clerk, "fieldctl")
    assert upload.status_code == 200, upload.text
    document_id = upload.json()["document_id"]
    warehouse = _token(env, "wh_a")
    resp = env.client.patch(
        f"/api/v1/documents/{document_id}/fields",
        json={"field_name": "amount_ttc_dzd", "new_value": "1.00"},
        headers=_auth(warehouse),
    )
    assert resp.status_code in (403, 404), resp.text


# ---------------------------------------------------------------------------
# SEC-05 - the payload digest must never see a missing line_ht_dzd
# ---------------------------------------------------------------------------


def test_omitted_line_ht_is_derived_not_a_500(env: SimpleNamespace) -> None:
    """Before the fix this was ``KeyError: 'line_ht_dzd'`` -> HTTP 500."""
    resp = env.client.post("/api/v1/invoices", json=_invoice_body(), headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["workflow_state"] == "Validated", payload
    assert payload["validation_errors"] == []
    with env.db.transaction() as conn:
        row = conn.execute("SELECT line_ht_dzd FROM supplier_invoice_lines").fetchone()
    assert row["line_ht_dzd"] == "10000.00"


def test_explicit_null_line_ht_is_derived_the_same_way(env: SimpleNamespace) -> None:
    body = _invoice_body()
    body["lines"][0]["line_ht_dzd"] = None
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text
    assert resp.json()["workflow_state"] == "Validated"


def test_omitted_and_explicit_payloads_produce_the_same_digest() -> None:
    """The digest is of what the payload *means*, not of how it was typed."""
    common = dict(
        supplier_id=10,
        supplier_invoice_no="FAC-SEC58-001",
        invoice_date="2026-10-15",
        payment_method="BANK_TRANSFER",
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_timbre_dzd="0.00",
        amount_ttc_dzd="11900.00",
    )
    derived = {"qty": "1.00", "unit_price_dzd": "10000.00", "tax_rate": "19.0"}
    explicit = {**derived, "line_ht_dzd": "10000.00"}
    with_derived = compute_invoice_payload_hash(lines=[{**derived, "line_ht_dzd": compute_line_ht_dzd("1.00", "10000.00")}], **common)
    with_explicit = compute_invoice_payload_hash(lines=[explicit], **common)
    assert with_derived == with_explicit


@pytest.mark.parametrize(
    "value",
    ["abc", "", "NaN", "sNaN", "Infinity", "-Infinity", "1e400", "9" * 60],
)
def test_unusable_line_ht_is_a_422_not_a_500(env: SimpleNamespace, value: str) -> None:
    """A value that cannot be a DZD amount is a request error, not a crash.

    ``NaN``/``sNaN``/``Infinity`` are refused by the upstream ``_require_finite``
    guard this integration deliberately does not duplicate; the magnitude and
    syntax cases are refused by the same conversion point. Either way the answer
    is 422 and nothing is written.
    """
    body = _invoice_body()
    body["lines"][0]["line_ht_dzd"] = value
    before = _count(env, "supplier_invoices")
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 422, resp.text
    assert _count(env, "supplier_invoices") == before


def test_mismatched_line_ht_still_reaches_validation(env: SimpleNamespace) -> None:
    """Preserved behaviour: a *usable but wrong* amount is a validation error."""
    body = _invoice_body()
    body["lines"][0]["line_ht_dzd"] = "9000.00"
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["workflow_state"] == "NeedsReview"
    assert any("HT_MATH_MISMATCH" in err for err in payload["validation_errors"])


def test_discount_is_applied_before_derivation(env: SimpleNamespace) -> None:
    """Derivation uses the same arithmetic (and the same rounding) as validation."""
    body = _invoice_body()
    body["lines"][0]["discount_pct"] = "10.00"
    body["lines"][0]["line_ht_dzd"] = None
    body["amount_ht_dzd"] = "9000.00"
    body["amount_tva_dzd"] = "1710.00"
    body["amount_ttc_dzd"] = "10710.00"
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text
    assert resp.json()["workflow_state"] == "Validated", resp.json()
    assert compute_line_ht_dzd("1.00", "10000.00", "10.00") == "9000.00"


def test_derivation_rounds_like_the_validator() -> None:
    """A repeating decimal must land on the same 2-dp value both sides use."""
    assert compute_line_ht_dzd("3.00", "33.33") == compute_line_ht_dzd("3", "33.33")
    assert compute_line_ht_dzd("3", "33.335") == "100.01"  # 100.005 -> half-up at 2 dp
    assert compute_line_ht_dzd("1.00", "10000.00", None) == "10000.00"


def test_missing_qty_is_a_request_error(env: SimpleNamespace) -> None:
    body = _invoice_body()
    body["lines"][0].pop("qty")
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 422, resp.text
    assert _count(env, "supplier_invoices") == 0


def test_refused_invoice_leaves_no_accounting_or_stock_effect(env: SimpleNamespace) -> None:
    body = _invoice_body()
    body["lines"][0]["line_ht_dzd"] = "not-a-number"
    assert env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a"))).status_code == 422
    for table in ("supplier_invoices", "supplier_invoice_lines", "journal_entries", "stock_moves"):
        assert _count(env, table) == 0, table


def test_valid_invoice_keeps_exact_totals(env: SimpleNamespace) -> None:
    """Accounting control: the security change must not alter what is stored."""
    resp = env.client.post("/api/v1/invoices", json=_invoice_body(), headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text
    with env.db.transaction() as conn:
        row = conn.execute(
            "SELECT amount_ht_dzd, amount_tva_dzd, amount_timbre_dzd, amount_ttc_dzd, workflow_state"
            " FROM supplier_invoices"
        ).fetchone()
    assert (row["amount_ht_dzd"], row["amount_tva_dzd"], row["amount_timbre_dzd"], row["amount_ttc_dzd"]) == (
        "10000.00",
        "1900.00",
        "0.00",
        "11900.00",
    )
    assert row["workflow_state"] == "Validated"


# ---------------------------------------------------------------------------
# SEC-07 - backup custody: owner-only, atomic, no private staging left behind
# ---------------------------------------------------------------------------


def _backup(env: SimpleNamespace, name: str = "backup.enc", key: bytes | None = None) -> Path:
    path = env.tmp_path / "out" / name
    create_encrypted_backup(
        env.db,
        env.vault,
        path,
        backup_encryption_key=Fernet.generate_key() if key is None else key,
    )
    return path


def test_backup_archive_is_owner_only(env: SimpleNamespace) -> None:
    path = _backup(env)
    assert path.exists()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_backup_leaves_no_staging_directory(env: SimpleNamespace) -> None:
    path = _backup(env)
    leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".tmp_backup_")]
    assert leftovers == []


def test_backup_publishes_exactly_one_file(env: SimpleNamespace) -> None:
    """No plaintext snapshot, no manifest with key material beside the archive."""
    path = _backup(env)
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert b"audit_hmac_secret_b64" not in path.read_bytes()


def test_staging_directory_and_manifest_are_private(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Observe the staging area before the ``finally`` cleanup removes it."""
    observed: Dict[str, int] = {}
    real_rmtree = __import__("shutil").rmtree

    def _capture(path, *args, **kwargs):
        p = Path(path)
        if p.name.startswith(".tmp_backup_") and p.exists():
            observed["dir"] = stat.S_IMODE(p.stat().st_mode)
            manifest = p / "manifest.json"
            if manifest.exists():
                observed["manifest"] = stat.S_IMODE(manifest.stat().st_mode)
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("dz_erp.security_audit.shutil.rmtree", _capture)
    _backup(env)
    if os.name == "posix":
        assert observed.get("dir") == 0o700, observed
        assert observed.get("manifest") == 0o600, observed


def test_failed_backup_writes_nothing_and_cleans_up(env: SimpleNamespace) -> None:
    out = env.tmp_path / "out2"
    path = out / "broken.enc"
    with pytest.raises(Exception):
        create_encrypted_backup(env.db, env.vault, path, backup_encryption_key=b"not-a-fernet-key")
    assert not path.exists()
    assert [p for p in out.iterdir() if p.name.startswith(".tmp_backup_")] == []


@pytest.mark.skipif(not safe_restore_supported(), reason="restore requires CPython 3.11.17+ / 3.12.10+")
def test_backup_round_trips_and_verifies_the_chain(env: SimpleNamespace, tmp_path: Path) -> None:
    from dz_erp.security_audit import restore_and_verify_backup

    key = Fernet.generate_key()
    path = _backup(env, key=key)
    restored = restore_and_verify_backup(path, tmp_path / "restored", backup_encryption_key=key)
    assert restored["db_sha256"]


# ---------------------------------------------------------------------------
# SEC-08 - bounded request bodies
# ---------------------------------------------------------------------------


def _app_with_env(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, **limits: str) -> TestClient:
    for key, value in limits.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_erp_fastapi_app(env.db, env.vault))


def test_body_below_the_limit_is_processed(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="1024")
    resp = client.post("/api/v1/auth/token", content=b"{}", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422  # reached the endpoint, failed its schema


def test_body_exactly_at_the_limit_is_not_refused(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="1024")
    payload = b"{" + b" " * 1022 + b"}"
    assert len(payload) == 1024
    resp = client.post("/api/v1/auth/token", content=payload, headers={"Content-Type": "application/json"})
    assert resp.status_code != 413


def test_body_above_the_limit_is_refused(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="1024")
    resp = client.post(
        "/api/v1/auth/token", content=b"{" + b" " * 1024 + b"}", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413
    assert "maximum of 1024 bytes" in resp.json()["detail"]


def test_oversized_body_is_refused_before_authentication(env: SimpleNamespace) -> None:
    """No credential is required to reach the 413: the cap precedes the router."""
    client = TestClient(create_erp_fastapi_app(env.db, env.vault))
    body = b'{"username": "' + b"a" * (DEFAULT_MAX_JSON_REQUEST_BYTES + 16) + b'", "password": "x"}'
    resp = client.post("/api/v1/auth/token", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


def test_oversized_body_has_no_side_effects(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="256")
    before_docs = _count(env, "source_documents")
    before_users = _count(env, "users")
    resp = client.post("/api/v1/invoices", content=b"{}" * 1000, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413
    assert (_count(env, "source_documents"), _count(env, "users")) == (before_docs, before_users)


def test_oversized_body_is_logged(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="512")
    app = client.app
    assert client.post("/api/v1/auth/token", content=b"x" * 2048).status_code == 413
    events = [e for e in _events(app) if e.get("event") == "SECURITY_REQUEST_BODY_LIMIT_EXCEEDED"]
    assert events, [e.get("event") for e in _events(app)]
    assert events[0]["context"]["limit_bytes"] == 512


def test_upload_path_limit_includes_multipart_framing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_MAX_UPLOAD_BYTES", "1000")
    assert _configured_max_request_bytes(UPLOAD_PATH) == 1000 + MULTIPART_FRAMING_ALLOWANCE
    assert _configured_max_request_bytes("/api/v1/invoices") == DEFAULT_MAX_JSON_REQUEST_BYTES


@pytest.mark.parametrize("value", ["banana", "0", "-5", "  "])
def test_malformed_override_cannot_disable_the_cap(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ERP_MAX_REQUEST_BYTES", value)
    assert _configured_max_request_bytes("/api/v1/invoices") == DEFAULT_MAX_JSON_REQUEST_BYTES


def test_chunked_body_without_content_length_is_proxys_responsibility(env: SimpleNamespace) -> None:
    """Documented limitation, asserted rather than assumed.

    The application-level cap reads the *declared* ``Content-Length``. A chunked
    request has none, so it must not be silently dropped or half-read here: it
    reaches the application, where the per-endpoint bounds (the upload reader,
    the JSON schema) still apply. The proxy's own limit is what bounds the
    stream. This test pins that behaviour so a future change is deliberate.
    """
    app = create_erp_fastapi_app(env.db, env.vault)
    body = json.dumps({"username": "x" * 50, "password": "y"}).encode()

    async def _call() -> Dict[str, Any]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/token",
            "raw_path": b"/api/v1/auth/token",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
        sent = {"done": False}

        async def receive() -> Dict[str, Any]:
            if sent["done"]:
                return {"type": "http.disconnect"}
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        captured: Dict[str, Any] = {"status": None}

        async def send(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]

        await app(scope, receive, send)
        return captured

    assert asyncio.run(_call())["status"] != 413


# ---------------------------------------------------------------------------
# SEC-10 / TASK E - hardening headers on every outcome, and the order that
# makes it true
# ---------------------------------------------------------------------------


def _assert_hardened(resp, *, cacheable: bool = True) -> None:
    for name, expected in (
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "no-referrer"),
        ("x-frame-options", "DENY"),
    ):
        values = resp.headers.get_list(name)
        assert values == [expected], (name, values)
    assert len(resp.headers.get_list("x-request-id")) == 1
    if cacheable:
        assert "no-store" not in resp.headers.get("cache-control", "")


def test_headers_on_a_successful_public_response(env: SimpleNamespace) -> None:
    resp = env.client.get("/healthz")
    assert resp.status_code == 200
    _assert_hardened(resp)


def test_headers_on_401(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/v1/invoices")
    assert resp.status_code == 401
    _assert_hardened(resp)


def test_headers_on_403(env: SimpleNamespace) -> None:
    resp = _upload(env, _token(env, "wh_a"), "hdr403")
    assert resp.status_code == 403
    _assert_hardened(resp, cacheable=False)  # an authenticated refusal is no-store


def test_headers_on_413(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="128")
    resp = client.post("/api/v1/auth/token", content=b"x" * 512)
    assert resp.status_code == 413
    _assert_hardened(resp)


def test_headers_on_404(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    _assert_hardened(resp)


def test_authenticated_responses_are_not_cacheable(env: SimpleNamespace) -> None:
    resp = env.client.get("/api/v1/invoices", headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


def test_headers_on_429_and_the_outer_layer_owns_them(env: SimpleNamespace) -> None:
    """A 429 is produced by the *inner* middleware; the outer layer decorates it.

    That is the observable proof of the execution order: the rate limiter
    returns before the router, yet the response still carries the hardening
    headers and exactly one request id.
    """
    app = create_erp_fastapi_app(env.db, env.vault, throttle=RequestThrottle(requests_per_minute=1))
    client = TestClient(app)
    headers = {"X-Request-Id": "abc123"}
    assert client.get("/healthz", headers=headers).status_code == 200
    limited = client.get("/healthz", headers=headers)
    assert limited.status_code == 429
    _assert_hardened(limited)
    assert limited.headers["retry-after"]
    assert limited.headers["x-request-id"] == "abc123"


def test_body_limit_short_circuits_before_the_route(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that would answer 405/404 still answers 413 first."""
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="64")
    resp = client.post("/healthz", content=b"x" * 256)
    assert resp.status_code == 413


def test_body_limit_does_not_consume_a_rate_limit_token(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Order proof: the 413 is produced outside the limiter, so it costs nothing."""
    monkeypatch.setenv("ERP_MAX_REQUEST_BYTES", "64")
    app = create_erp_fastapi_app(env.db, env.vault, throttle=RequestThrottle(requests_per_minute=1))
    client = TestClient(app)
    assert client.post("/api/v1/auth/token", content=b"x" * 256).status_code == 413
    assert client.get("/healthz").status_code == 200  # first counted request
    assert client.get("/healthz").status_code == 429  # limit is 1/minute


def test_request_id_is_echoed_on_rejections(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="64")
    resp = client.post("/api/v1/auth/token", content=b"x" * 256, headers={"X-Request-Id": "req-42"})
    assert resp.status_code == 413
    assert resp.headers["x-request-id"] == "req-42"


def test_invalid_request_id_is_replaced_not_echoed(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app_with_env(env, monkeypatch, ERP_MAX_REQUEST_BYTES="64")
    resp = client.post("/api/v1/auth/token", content=b"x" * 256, headers={"X-Request-Id": "bad\nvalue"})
    assert resp.status_code == 413
    assert resp.headers["x-request-id"] != "bad\nvalue"
    assert resp.headers["x-request-id"].strip() == resp.headers["x-request-id"]


# ---------------------------------------------------------------------------
# SEC-03 - an out-of-range list limit is a request error, not a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, -1, 501, 100000])
def test_invoice_list_rejects_out_of_range_limit_with_422(env: SimpleNamespace, limit: int) -> None:
    resp = env.client.get(f"/api/v1/invoices?limit={limit}", headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 422, resp.text
    assert "limit" in resp.json()["detail"].lower()


@pytest.mark.parametrize("limit", [1, 500])
def test_invoice_list_accepts_the_documented_boundaries(env: SimpleNamespace, limit: int) -> None:
    resp = env.client.get(f"/api/v1/invoices?limit={limit}", headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# SEC-04 - an unsupported payment method is refused before the CHECK constraint
# ---------------------------------------------------------------------------


def test_unsupported_payment_method_is_a_422_not_a_500(env: SimpleNamespace) -> None:
    body = _invoice_body(payment_method="BITCOIN")
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 422, resp.text
    assert "not supported" in resp.json()["detail"]
    assert _count(env, "supplier_invoices") == 0


@pytest.mark.parametrize("method", ["CASH", "BANK_TRANSFER", "CHEQUE"])
def test_every_supported_payment_method_is_accepted(env: SimpleNamespace, method: str) -> None:
    body = _invoice_body(supplier_invoice_no=f"FAC-SEC58-{method}", payment_method=method)
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# SEC-06 - the invoice date and the supplier reference are validated, and the
# internal parser text never reaches the caller
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["not-a-date", "2026-13-45", "2026-02-31", "0000-00-00", ""])
def test_invalid_invoice_date_is_a_422_without_internal_text(env: SimpleNamespace, value: str) -> None:
    body = _invoice_body(invoice_date=value)
    resp = env.client.post("/api/v1/invoices", json=body, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    for leaked in ("invalid literal", "int()", "strptime", "Traceback"):
        assert leaked not in detail
    assert _count(env, "supplier_invoices") == 0


@pytest.mark.parametrize("reference", ["", "   ", "F" * 129])
def test_invalid_supplier_reference_is_a_422(env: SimpleNamespace, reference: str) -> None:
    resp = env.client.post(
        "/api/v1/invoices", json=_invoice_body(supplier_invoice_no=reference), headers=_auth(_token(env, "clerk_a"))
    )
    assert resp.status_code == 422, resp.text
    assert _count(env, "supplier_invoices") == 0


def test_maximum_reference_length_is_accepted(env: SimpleNamespace) -> None:
    reference = "F" * 128
    resp = env.client.post(
        "/api/v1/invoices", json=_invoice_body(supplier_invoice_no=reference), headers=_auth(_token(env, "clerk_a"))
    )
    assert resp.status_code == 200, resp.text


def test_fiscal_year_still_comes_from_the_invoice_date(env: SimpleNamespace) -> None:
    """Accounting control: the duplicate key still uses the invoice's year."""
    assert env.client.post("/api/v1/invoices", json=_invoice_body(), headers=_auth(_token(env, "clerk_a"))).status_code == 200
    with env.db.transaction() as conn:
        assert conn.execute("SELECT fiscal_year FROM supplier_invoices").fetchone()["fiscal_year"] == 2026


def test_correction_path_validates_the_date_and_derives_a_missing_line_ht(env: SimpleNamespace) -> None:
    """The revalidate path had the same two defects as the create path."""
    clerk = _auth(_token(env, "clerk_a"))
    body = _invoice_body()
    body["lines"][0]["line_ht_dzd"] = "9000.00"  # forces NeedsReview
    created = env.client.post("/api/v1/invoices", json=body, headers=clerk)
    assert created.status_code == 200 and created.json()["workflow_state"] == "NeedsReview"
    invoice_id = created.json()["invoice_id"]

    corrected = env.client.post(
        f"/api/v1/invoices/{invoice_id}/revalidate",
        json={"invoice_date": "2026-13-45"},
        headers=clerk,
    )
    assert corrected.status_code == 422, corrected.text

    fixed = env.client.post(
        f"/api/v1/invoices/{invoice_id}/revalidate",
        json={"lines": [{"description": "Service", "qty": "1.00", "unit_price_dzd": "10000.00", "tax_rate": "19.0"}]},
        headers=clerk,
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["workflow_state"] == "Validated", fixed.json()


# ---------------------------------------------------------------------------
# SEC-09 - a username cannot carry control characters or unbounded length
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("username", ["bad\nname", "tab\there", "del\x7fchar", "x" * 65])
def test_username_with_control_characters_or_excessive_length_is_refused(
    env: SimpleNamespace, username: str
) -> None:
    resp = env.client.post(
        "/api/v1/users",
        json={"username": username, "password": "Valid#2026x", "role": "clerk"},
        headers=_auth(_token(env, "admin_a")),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("username", ["ok_user", "x" * 64])
def test_ordinary_usernames_are_still_accepted(env: SimpleNamespace, username: str) -> None:
    resp = env.client.post(
        "/api/v1/users",
        json={"username": username, "password": "Valid#2026x", "role": "clerk"},
        headers=_auth(_token(env, "admin_a")),
    )
    assert resp.status_code == 200, resp.text
