# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 64 — Dependency security remediation and hostile-document regression.

Background
----------
Phase 10's audit (`pip-audit 2.10.1`) found 143 distinct advisories across eight
pinned distributions, all of them in the code path that touches untrusted
supplier documents: `pypdf` (67), `pillow` (53), `starlette` (7),
`cryptography` (6), `python-multipart` (6), `pdfminer.six` (2), `certifi` (1)
and `pytest` (1). The pins were raised to the smallest releases that clear the
advisories while keeping Python 3.11/3.12 support (see
`docs/release/phase10/DEPENDENCY_REMEDIATION_REPORT.md`).

This module is the regression net for that change. It has two halves:

1. **The pins cannot silently slide back.** Floors are asserted per package, the
   `pdfplumber` -> `pdfminer.six` exact pin is checked as a pair (upgrading one
   without the other reintroduces the vulnerable parser), the two new
   transitive pins that the upgrade requires are asserted present, and the
   installed environment is checked against the lock rather than against a
   hardcoded expectation.

2. **The newly-pinned parsers reject hostile documents cleanly.** Decompression
   bombs, dimension bombs, deeply nested and cyclic PDF structures, truncated
   multipart bodies, wrong magic bytes and disallowed containers must produce a
   4xx - never a 500, never a crash, and never a persisted financial record.
   Nothing here is a weaponised payload: every fixture is a few hundred bytes of
   structurally hostile but inert markup.

The fixtures are synthetic. No production data and no network access are used.
"""

from __future__ import annotations

import io
import re
import struct
import zlib
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Tuple

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import AuthManager, hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import (
    PrivateDocumentVault,
    _pdfplumber_model_version,
    inspect_and_validate_file_security,
)
from dz_erp.localization import load_official_odoo_chart

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements.lock.txt"
HASH_LOCKFILE = REPO_ROOT / "requirements.lock.hashes.txt"

FAST_ITERATIONS = 1000

# The advisory set this remediation closed. Each entry is
# (distribution, minimum fixed version, reason). The floor is the *smallest*
# release that clears every advisory that applied to the version we shipped.
SECURITY_FLOORS: List[Tuple[str, str, str]] = [
    ("pypdf", "6.21.4", "67 advisories in 5.4.0 (parses untrusted PDFs)"),
    ("pillow", "13.0.0", "53 advisories in 11.1.0 (PSD/FITS/font parsing)"),
    ("starlette", "1.3.1", "7 advisories in 0.46.2 (request handling)"),
    ("cryptography", "50.0.0", "6 advisories in 44.0.2 (TLS primitives)"),
    ("python-multipart", "0.0.31", "6 advisories in 0.0.20 (multipart parsing)"),
    ("pdfminer.six", "20251230", "2 advisories in 20250327 (PDF text extraction)"),
    ("pdfplumber", "0.11.10", "pins the vulnerable pdfminer.six==20250327 below this"),
    ("certifi", "2026.10.18", "1 advisory in 2026.7.22 (trust store)"),
    ("pytest", "9.0.3", "1 advisory in 8.3.5 (dev tooling)"),
]

TENANT_USERS = [
    (1, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (4, 1, "acct_a", "accountant", 1, "Acct#2026x"),
    (5, 1, "admin_a", "admin", 1, "Adm#2026x"),
]

SUPPLIER_SPEC = {
    "supplier_name": "Supplier Alpha",
    "invoice_number": "FAC-P64-001",
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


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _parse_lock() -> Dict[str, str]:
    pins: Dict[str, str] = {}
    for raw in LOCKFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def _version_tuple(value: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts)


def _installed(name: str) -> str:
    return metadata.version(name)


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    db = ERPDatabase(str(tmp_path / "p64.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, created_at) VALUES (1, 'DZ1', 'Alpha', '2026-01-01')"
        )
        for uid, cid, username, role, active, password in TENANT_USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (uid, cid, username, role, active, hash_password(password, iterations=FAST_ITERATIONS)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc)"
            " VALUES (10, 1, 'Supplier Alpha', 'supplier', '001216099123456',"
            " '099116010123456', '16019912345', '16/00-0991234B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    app = create_erp_fastapi_app(db, vault, auth_manager=auth)
    return SimpleNamespace(db=db, vault=vault, auth=auth, app=app, client=TestClient(app), tmp_path=tmp_path)


def _token(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _upload(client: TestClient, token: str, filename: str, content: bytes, content_type: str):
    return client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, io.BytesIO(content), content_type)},
        headers={"Authorization": f"Bearer {token}"},
    )


def _count(env: SimpleNamespace, table: str) -> int:
    with env.db.transaction() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _png_claiming(width: int, height: int) -> bytes:
    """A PNG whose IHDR declares huge dimensions with one tiny, valid IDAT.

    Pillow's decompression-bomb guard triggers on the declared pixel count, so
    this exercises that guard without allocating the claimed image.
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 3)  # one pixel row of RGB
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _pdf_with_deep_nesting(depth: int = 400) -> bytes:
    """A PDF whose page tree is nested far past any sane limit."""
    inner = b"<<>>"
    nested = inner
    for _ in range(depth):
        nested = b"[" + nested + b"]"
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [" + nested + b"] /Count 1 >>\nendobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def _pdf_with_cyclic_page_tree() -> bytes:
    """A page tree whose kid points back at its parent (infinite traversal bait)."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 /Parent 3 0 R >>\nendobj\n"
        b"3 0 obj\n<< /Type /Pages /Kids [2 0 R] /Count 1 /Parent 2 0 R >>\nendobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def _pdf_with_many_pages(pages: int = 40) -> bytes:
    """A structurally valid PDF whose page count exceeds the configured maximum."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf)
    for index in range(pages):
        doc.drawString(72, 720, f"page {index}")
        doc.showPage()
    doc.save()
    return buf.getvalue()


# ======================================================================
# 1. The pins cannot slide back into the vulnerable range
# ======================================================================
@pytest.mark.parametrize("name,floor,reason", SECURITY_FLOORS, ids=[case[0] for case in SECURITY_FLOORS])
def test_lock_pin_is_at_or_above_the_security_floor(name: str, floor: str, reason: str) -> None:
    """Every remediated pin must stay at or above its documented fix threshold.

    A future `pip-compile` against an old constraint file, or a hand edit that
    "restores what worked", would reintroduce exactly the advisories Phase 10
    closed. The floor is stated per package so the failure message explains the
    risk instead of just naming a version.
    """
    pins = _parse_lock()
    key = name.lower().replace("_", "-")
    assert key in pins, f"{name} is no longer pinned - the lock cannot be audited"
    assert _version_tuple(pins[key]) >= _version_tuple(floor), (
        f"{name}=={pins[key]} is below the security floor {floor} ({reason})"
    )


@pytest.mark.parametrize("name,floor,reason", SECURITY_FLOORS, ids=[case[0] for case in SECURITY_FLOORS])
def test_installed_version_is_at_or_above_the_security_floor(name: str, floor: str, reason: str) -> None:
    """The running environment must not be older than the lock claims."""
    installed = _installed(name)
    assert _version_tuple(installed) >= _version_tuple(floor), (
        f"installed {name}=={installed} is below the security floor {floor} ({reason})"
    )


def test_pdfplumber_and_pdfminer_are_pinned_as_a_compatible_pair() -> None:
    """`pdfplumber` pins `pdfminer.six` exactly; the two must move together.

    `pdfplumber==0.11.6` declares `pdfminer.six==20250327`, so any attempt to
    upgrade the vulnerable parser alone is unsatisfiable, and any attempt to
    upgrade `pdfplumber` alone silently changes the parser. This asserts the
    installed metadata agrees with the lock, which is the only way both can be
    true at once.
    """
    requires = metadata.requires("pdfplumber") or []
    exact = [req for req in requires if req.replace(" ", "").startswith("pdfminer.six==")]
    assert exact, f"pdfplumber no longer pins pdfminer.six exactly: {requires}"
    required_version = exact[0].split("==")[1]
    pins = _parse_lock()
    assert pins["pdfminer.six"] == required_version, (
        f"lock pins pdfminer.six=={pins['pdfminer.six']} but pdfplumber requires {required_version}"
    )
    assert _installed("pdfminer.six") == required_version


def test_transitive_pins_required_by_the_upgrade_are_present() -> None:
    """`annotated-doc` (fastapi) and `pygments` (pytest) are new hard requirements.

    Both were missing from the pre-remediation lock; installing it with
    `--no-deps` failed at import time (`ModuleNotFoundError: annotated_doc`).
    Pinning them keeps the documented `--no-deps` install complete.
    """
    pins = _parse_lock()
    assert "annotated-doc" in pins, "fastapi requires annotated-doc; --no-deps installs need it pinned"
    assert "pygments" in pins, "pytest 9.x requires pygments; --no-deps installs need it pinned"

    fastapi_requires = " ".join(metadata.requires("fastapi") or [])
    assert "annotated-doc" in fastapi_requires
    pytest_requires = " ".join(metadata.requires("pytest") or [])
    assert "pygments" in pytest_requires


def test_starlette_pin_satisfies_the_fastapi_requirement() -> None:
    """The two must stay mutually compatible after any future bump."""
    from packaging.requirements import Requirement

    pins = _parse_lock()
    starlette_req = next(
        Requirement(req) for req in (metadata.requires("fastapi") or []) if req.lower().startswith("starlette")
    )
    assert starlette_req.specifier.contains(pins["starlette"]), (
        f"fastapi requires {starlette_req.specifier}, lock pins starlette=={pins['starlette']}"
    )


def test_no_installed_distribution_is_absent_from_the_lock() -> None:
    """`--no-deps` installation means the lock must describe the whole runtime.

    Anything installed but unpinned would be an undeclared dependency of the
    deployment. `pip` and `setuptools` are excluded: they are the installer's
    own bootstrap tooling and cannot be installed by themselves.
    """
    pins = {name for name in _parse_lock()}
    allowed_extra = {"pip", "setuptools"}
    installed = {
        dist.metadata["Name"].lower().replace("_", "-")
        for dist in metadata.distributions()
        if dist.metadata["Name"]
    }
    undeclared = installed - pins - allowed_extra
    assert not undeclared, f"installed but not pinned in requirements.lock.txt: {sorted(undeclared)}"


def test_every_pin_carries_at_least_one_hash() -> None:
    """Hash pinning is what makes the version pins enforceable."""
    text = HASH_LOCKFILE.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
    counted = 0
    for block in blocks:
        if not re.match(r"^[A-Za-z0-9_.\-]+==", block.strip()):
            continue
        counted += 1
        assert "--hash=sha256:" in block, f"pin without a digest: {block.strip().splitlines()[0]!r}"
    assert counted == len(_parse_lock())


def test_build_backend_requires_a_patched_setuptools() -> None:
    """The build path must not accept an old setuptools (GHSA-xmj3-9gf7-h52w)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^requires\s*=\s*\[(.*?)\]", text, re.MULTILINE)
    assert match, "build-system requires not found"
    floor = re.search(r"setuptools>=([0-9.]+)", match.group(1))
    assert floor, "setuptools is not floor-pinned in the build-system requirements"
    assert _version_tuple(floor.group(1)) >= (84, 1, 0)


def test_ocr_provenance_reports_the_installed_parsers() -> None:
    """The provenance label used to be a hardcoded "pdfplumber-0.11.6+pdfminer".

    A provenance field that drifts from reality is worse than no field: it makes
    a reviewed document look like it was parsed by a version that never ran.
    """
    label = _pdfplumber_model_version()
    assert _installed("pdfplumber") in label, label
    assert _installed("pdfminer.six") in label, label
    assert "0.11.6" not in label, f"stale hardcoded parser version in provenance: {label}"


# ======================================================================
# 2. Hostile documents against the upgraded parsers
# ======================================================================
def test_decompression_bomb_png_is_rejected_without_allocating() -> None:
    """A PNG claiming 20000x20000 must be refused by the bomb guard, not decoded."""
    with pytest.raises(ValueError):
        inspect_and_validate_file_security(_png_claiming(20000, 20000), "image/png")


def test_dimension_limit_rejects_oversized_images() -> None:
    """6 000 px is the configured ceiling; 6 001 must fail the same way."""
    buf = io.BytesIO()
    Image.new("RGB", (6001, 10), (255, 255, 255)).save(buf, format="PNG")
    with pytest.raises(ValueError, match="exceed limit"):
        inspect_and_validate_file_security(buf.getvalue(), "image/png")


def test_dimension_limit_accepts_an_image_inside_the_boundary() -> None:
    """The boundary is a limit, not a blanket refusal - 64x64 must pass."""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (255, 255, 255)).save(buf, format="PNG")
    result = inspect_and_validate_file_security(buf.getvalue(), "image/png")
    assert result["security_status"] == "CLEAN"


def test_deeply_nested_pdf_structure_is_rejected_not_raised() -> None:
    """400 levels of nested arrays must surface as a clean rejection."""
    with pytest.raises(ValueError):
        inspect_and_validate_file_security(_pdf_with_deep_nesting(), "application/pdf")


def test_cyclic_page_tree_is_rejected_not_raised() -> None:
    """A page tree that points at itself must not hang or crash the validator."""
    with pytest.raises(ValueError):
        inspect_and_validate_file_security(_pdf_with_cyclic_page_tree(), "application/pdf")


def test_pdf_page_count_limit_is_enforced() -> None:
    """40 pages exceeds the 20-page ceiling."""
    with pytest.raises(ValueError, match="page count"):
        inspect_and_validate_file_security(_pdf_with_many_pages(40), "application/pdf")


def test_psd_magic_bytes_cannot_pass_as_an_image_upload() -> None:
    """The PSD parser is not reachable: only .pdf/.png/.jpg are accepted.

    Pillow's PSD advisory is the reason this matters - the file format is never
    opened, because the signature check runs before any decode.
    """
    psd = b"8BPS\x00\x01" + b"\x00" * 64
    with pytest.raises(ValueError):
        inspect_and_validate_file_security(psd, "image/png")


HOSTILE_UPLOADS: List[Tuple[str, bytes, str, str]] = [
    ("bomb.png", _png_claiming(20000, 20000), "image/png", "decompression/pixel bomb"),
    ("nested.pdf", _pdf_with_deep_nesting(), "application/pdf", "400-deep nested page tree"),
    ("cyclic.pdf", _pdf_with_cyclic_page_tree(), "application/pdf", "cyclic page tree"),
    ("many.pdf", _pdf_with_many_pages(40), "application/pdf", "page count over the limit"),
    ("psd.png", b"8BPS\x00\x01" + b"\x00" * 64, "image/png", "PSD container labelled PNG"),
]


@pytest.mark.parametrize(
    "filename,content,mime,label", HOSTILE_UPLOADS, ids=[case[3] for case in HOSTILE_UPLOADS]
)
def test_hostile_document_is_rejected_by_the_api_without_a_server_error(
    env: SimpleNamespace, filename: str, content: bytes, mime: str, label: str
) -> None:
    """The endpoint must answer 4xx and leave no state behind - never a 500."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, filename, content, mime)
    assert resp.status_code in (400, 413, 415, 422), f"{label}: {resp.status_code} {resp.text[:200]}"
    assert resp.status_code != 500, label
    assert _count(env, "source_documents") == 0, f"{label}: a rejected document was persisted"
    assert _count(env, "ocr_extracted_fields") == 0


def test_truncated_multipart_body_is_a_client_error(env: SimpleNamespace) -> None:
    """A body cut mid-part must be rejected by the parser, not by an exception."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    boundary = "----p64boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="x.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
        "%PDF-1.4\n"
    ).encode()  # deliberately never closed
    resp = env.client.post(
        "/api/v1/documents/upload",
        content=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    assert resp.status_code in (400, 422), resp.text[:200]
    assert _count(env, "source_documents") == 0


def test_disallowed_container_and_mime_are_refused(env: SimpleNamespace) -> None:
    """A .zip and an image/gif are outside the allow-list, whatever they contain."""
    token = _token(env.client, "clerk_a", "Clerk#2026x")
    zip_bytes = b"PK\x03\x04" + b"\x00" * 32
    assert _upload(env.client, token, "bundle.zip", zip_bytes, "application/zip").status_code in (400, 415)
    assert _upload(env.client, token, "anim.gif", b"GIF89a" + b"\x00" * 32, "image/gif").status_code in (400, 415)
    assert _count(env, "source_documents") == 0


def test_accepted_document_creates_no_financial_record_without_human_approval(env: SimpleNamespace) -> None:
    """OCR/AI extraction must never post by itself.

    Uploading and extracting a supplier invoice may create a source document and
    extracted fields; it must not create a supplier invoice, a journal entry or
    any approval decision. Those require an explicit human action.
    """
    from dz_erp.benchmark import render_synthetic_invoice_pdf

    token = _token(env.client, "clerk_a", "Clerk#2026x")
    resp = _upload(env.client, token, "invoice.pdf", render_synthetic_invoice_pdf(SUPPLIER_SPEC), "application/pdf")
    assert resp.status_code in (200, 201, 202), resp.text[:300]
    assert _count(env, "source_documents") == 1
    assert _count(env, "supplier_invoices") == 0, "OCR ingestion posted a supplier invoice without approval"
    assert _count(env, "journal_entries") == 0, "OCR ingestion created a journal entry without approval"
