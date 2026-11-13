# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 34 — Independent security validation regression tests.

Each test here reproduces a defect that was confirmed during the independent
security validation of this revision and asserts the corrected behaviour, so
the finding cannot silently return:

  * SEC-01  Document upload and OCR field correction ignored the RBAC matrix in
            ``docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md``: ``warehouse``,
            ``auditor`` and ``ocr_worker`` accounts could create documents and
            rewrite extracted values.
  * SEC-02  ``Decimal("NaN")`` survived ``quantize`` and was stored as the TEXT
            ``'NaN'`` in a money column with HTTP 200, while ``sNaN``,
            ``Infinity``, ``1e400`` and 400-digit amounts raised an unhandled
            ``decimal.InvalidOperation`` (HTTP 500).
  * SEC-03  ``GET /api/v1/invoices`` with an out-of-range ``limit`` answered 500
            instead of the 422 every other list endpoint returns.
  * SEC-04  An unsupported ``payment_method`` reached the schema CHECK and
            surfaced as ``sqlite3.IntegrityError`` (HTTP 500).
  * SEC-05  A line omitting the optional ``line_ht_dzd`` raised ``TypeError``
            (HTTP 500).
  * SEC-06  ``invoice_date`` was never validated (``2026-02-31`` and
            ``0000-00-00`` were accepted) and an empty ``supplier_invoice_no``
            was accepted; a malformed date echoed an internal
            ``invalid literal for int()`` message.
  * SEC-07  A backup archive - which carries the vault and audit key rings
            inside its Fernet envelope - was written world-readable (0644).
  * SEC-08  Every JSON endpoint, including the unauthenticated token endpoint,
            accepted a request body of unbounded size.
  * SEC-09  A username containing control characters was accepted.
  * SEC-10  No browser-facing hardening headers were emitted.

Positive controls are included so the suite also proves what must keep working
(tenant isolation, allowed roles, mass-assignment immunity, SQL-metacharacter
handling as data).
"""

import io
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from dz_erp.api import DEFAULT_MAX_JSON_REQUEST_BYTES, create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import ERPDatabase
from dz_erp.decimal_utils import to_decimal
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import create_encrypted_backup

# (user_id, company_id, username, role, password)
USERS = [
    (1, 1, "clerk_a", "clerk", "Clerk#2026x"),
    (2, 1, "wh_a", "warehouse", "Wh#2026x"),
    (3, 1, "approver_a", "approver", "Appr#2026x"),
    (4, 1, "acct_a", "accountant", "Acct#2026x"),
    (5, 1, "auditor_a", "auditor", "Aud#2026x"),
    (6, 1, "ocr_a", "ocr_worker", "Ocr#2026x"),
    (7, 1, "admin_a", "admin", "Adm#2026x"),
    (20, 2, "admin_b", "admin", "AdminB#2026x"),
]

SUPPLIER_SPEC = {
    "supplier_name": "Supplier Alpha",
    "invoice_number": "FAC-SEC34-001",
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

DOC_BYTES = b"%PDF-1.4\n% synthetic phase-34 fixture - not a real invoice\n1 0 obj\nendobj\n"


def _invoice_body(**overrides):
    body = {
        "supplier_id": 10,
        "supplier_invoice_no": "FAC-SEC34-001",
        "invoice_date": "2026-10-15",
        "payment_method": "BANK_TRANSFER",
        "amount_ht_dzd": "10000.00",
        "amount_tva_dzd": "1900.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "11900.00",
        "lines": [
            {
                "description": "Service",
                "qty": "1.00",
                "unit_price_dzd": "10000.00",
                "tax_rate": "19.0",
                "line_ht_dzd": "10000.00",
            }
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    """Two-tenant, isolated ERP with a document owned by tenant 1."""
    db = ERPDatabase(str(tmp_path / "sec34.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
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
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, page_count,"
            " file_sha256, vault_path, security_status, workflow_state, uploaded_by_id, uploaded_at)"
            " VALUES ('doc-1', 1, 'alpha.pdf', 'application/pdf', ?, 1, ?, ?, 'CLEAN', 'NeedsReview',"
            " 1, '2026-10-20T10:00:00+00:00')",
            (len(DOC_BYTES), "a" * 64, vault.store_bytes(1, "a" * 64, DOC_BYTES)),
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index,"
            " field_name, raw_text, normalized_value, confidence, confidence_kind,"
            " confidence_calibrated, bbox_json, ocr_model_version, created_at)"
            " VALUES (1, 'doc-1', 1, 0, 'amount_ttc_dzd', '11 900,00', '11900.00', 0.9,"
            " 'engine_line', 0, '{}', 'synthetic-ocr-1', '2026-10-20T10:00:01+00:00')"
        )
    client = TestClient(create_erp_fastapi_app(db, vault))
    return SimpleNamespace(db=db, vault=vault, client=client, tmp_path=tmp_path)


def _token(env: SimpleNamespace, username: str) -> str:
    password = next(p for _uid, _cid, name, _role, p in USERS if name == username)
    resp = env.client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _count(env: SimpleNamespace, table: str) -> int:
    with env.db.transaction() as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _audit_actions(env: SimpleNamespace) -> list:
    with env.db.transaction() as conn:
        return [
            r["action_type"]
            for r in conn.execute("SELECT action_type FROM audit_events ORDER BY id").fetchall()
        ]


# ---------------------------------------------------------------------------
# SEC-01 — document intake RBAC (upload + OCR field correction)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("username", ["wh_a", "auditor_a", "ocr_a"])
def test_upload_is_refused_for_roles_the_rbac_matrix_denies(env: SimpleNamespace, username: str) -> None:
    """SEC-01: warehouse/auditor/ocr_worker must not create documents.

    Before the fix these roles received HTTP 200 and a ``source_documents``
    row was written, contradicting the documented matrix.
    """
    before = _count(env, "source_documents")
    pdf = render_synthetic_invoice_pdf(SUPPLIER_SPEC)
    resp = env.client.post(
        "/api/v1/documents/upload",
        files={"file": ("denied.pdf", io.BytesIO(pdf), "application/pdf")},
        headers=_auth(_token(env, username)),
    )
    assert resp.status_code == 403, resp.text
    assert _count(env, "source_documents") == before
    assert "SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED" in _audit_actions(env)


@pytest.mark.parametrize("username", ["clerk_a", "approver_a", "acct_a", "admin_a"])
def test_upload_is_allowed_for_roles_the_rbac_matrix_permits(env: SimpleNamespace, username: str) -> None:
    """SEC-01 positive control: the permitted roles keep working."""
    pdf = render_synthetic_invoice_pdf({**SUPPLIER_SPEC, "invoice_number": f"FAC-UP-{username}"})
    resp = env.client.post(
        "/api/v1/documents/upload",
        files={"file": ("allowed.pdf", io.BytesIO(pdf), "application/pdf")},
        headers=_auth(_token(env, username)),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"]


@pytest.mark.parametrize("username", ["wh_a", "auditor_a", "ocr_a"])
def test_field_correction_is_refused_for_denied_roles(env: SimpleNamespace, username: str) -> None:
    """SEC-01: a role denied the upload must not rewrite reviewed values."""
    resp = env.client.patch(
        "/api/v1/documents/doc-1/fields",
        json={"field_name": "amount_ttc_dzd", "new_value": "1.00"},
        headers=_auth(_token(env, username)),
    )
    assert resp.status_code == 403, resp.text
    with env.db.transaction() as conn:
        row = conn.execute(
            "SELECT human_override_value FROM ocr_extracted_fields WHERE document_id='doc-1'"
        ).fetchone()
    assert row["human_override_value"] is None


def test_field_correction_is_allowed_for_clerk_and_audited(env: SimpleNamespace) -> None:
    """SEC-01 positive control: clerk corrects a field; the edit is audited."""
    resp = env.client.patch(
        "/api/v1/documents/doc-1/fields",
        json={"field_name": "amount_ttc_dzd", "new_value": "11900.00"},
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 200, resp.text
    assert "OCR_HUMAN_FIELD_EDIT" in _audit_actions(env)


def test_document_reads_remain_tenant_isolated(env: SimpleNamespace) -> None:
    """Regression guard: hardening must not weaken cross-tenant refusal."""
    other = _auth(_token(env, "admin_b"))
    for path in (
        "/api/v1/documents/doc-1/fields",
        "/api/v1/documents/doc-1/content",
        "/api/v1/documents/doc-1/file",
    ):
        assert env.client.get(path, headers=other).status_code == 403, path
    assert (
        env.client.patch(
            "/api/v1/documents/doc-1/fields",
            json={"field_name": "amount_ttc_dzd", "new_value": "1.00"},
            headers=other,
        ).status_code
        in (403, 404)
    )


# ---------------------------------------------------------------------------
# SEC-02 — non-finite / out-of-range decimals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "1e400", "9" * 400])
def test_non_finite_or_oversized_amounts_are_rejected_without_a_write(
    env: SimpleNamespace, value: str
) -> None:
    """SEC-02: 422, and no money column may hold a non-numeric/degenerate value."""
    before = _count(env, "supplier_invoices")
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(amount_ht_dzd=value, amount_ttc_dzd=value),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 422, resp.text
    assert _count(env, "supplier_invoices") == before
    with env.db.transaction() as conn:
        stored = [
            r["amount_ht_dzd"]
            for r in conn.execute("SELECT amount_ht_dzd FROM supplier_invoices").fetchall()
        ]
    assert not any(s.lower().lstrip("+-") in {"nan", "snan", "inf", "infinity"} for s in stored)


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "1e400", 10**30])
def test_to_decimal_refuses_non_finite_and_out_of_range_values(value) -> None:
    """SEC-02 unit level: the single conversion point refuses the input."""
    with pytest.raises(ValueError):
        to_decimal(value)


def test_to_decimal_still_accepts_real_amounts() -> None:
    """SEC-02 positive control: the guard must not narrow legitimate input."""
    assert str(to_decimal("11900.00")) == "11900.00"
    assert str(to_decimal("11 900,00")) == "11900.00"
    assert str(to_decimal("0")) == "0"
    assert str(to_decimal("1e17")) == "1E+17"


def test_bool_is_not_a_decimal() -> None:
    """SEC-02: ``bool`` is an ``int`` subclass and silently became 1.00."""
    with pytest.raises(TypeError):
        to_decimal(True)


# ---------------------------------------------------------------------------
# SEC-03 — list limit contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", ["0", "-1", "501", "100000"])
def test_invoice_list_rejects_out_of_range_limit_with_422(env: SimpleNamespace, limit: str) -> None:
    """SEC-03: previously an unhandled ValueError produced HTTP 500."""
    resp = env.client.get("/api/v1/invoices", params={"limit": limit}, headers=_auth(_token(env, "clerk_a")))
    assert resp.status_code == 422, resp.text


def test_invoice_list_accepts_the_documented_boundaries(env: SimpleNamespace) -> None:
    """SEC-03 positive control: 1 and 500 remain valid."""
    for limit in ("1", "500"):
        resp = env.client.get("/api/v1/invoices", params={"limit": limit}, headers=_auth(_token(env, "clerk_a")))
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# SEC-04 — payment method validation
# ---------------------------------------------------------------------------


def test_unsupported_payment_method_is_a_422_not_a_500(env: SimpleNamespace) -> None:
    """SEC-04: the schema CHECK must not be the first validator."""
    before = _count(env, "supplier_invoices")
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(payment_method="BITCOIN"),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 422, resp.text
    assert _count(env, "supplier_invoices") == before


# ---------------------------------------------------------------------------
# SEC-05 — optional line net
# ---------------------------------------------------------------------------


def test_line_without_declared_net_is_derived_not_a_500(env: SimpleNamespace) -> None:
    """SEC-05: ``line_ht_dzd`` is optional in the request model."""
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(lines=[{"description": "Service", "qty": "1.00", "unit_price_dzd": "10000.00",
                                   "tax_rate": "19.0"}]),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 200, resp.text
    invoice_id = resp.json()["invoice_id"]
    with env.db.transaction() as conn:
        line = conn.execute(
            "SELECT line_ht_dzd FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
    assert line["line_ht_dzd"] == "10000.00"


# ---------------------------------------------------------------------------
# SEC-06 — invoice date and reference validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_date", ["not-a-date", "2026-13-45", "2026-02-31", "0000-00-00", ""])
def test_invalid_invoice_date_is_rejected_without_leaking_internals(
    env: SimpleNamespace, bad_date: str
) -> None:
    """SEC-06: an invalid date is a 422 and the response carries no parser text."""
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(invoice_date=bad_date, supplier_invoice_no=f"FAC-DATE-{abs(hash(bad_date)) % 9999}"),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 422, resp.text
    assert "invalid literal for int()" not in resp.text


@pytest.mark.parametrize("reference", ["", "   ", "F" * 129])
def test_invalid_supplier_reference_is_rejected(env: SimpleNamespace, reference: str) -> None:
    """SEC-06: empty/oversized invoice references are refused."""
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(supplier_invoice_no=reference),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 422, resp.text


def test_sql_metacharacters_are_stored_as_data(env: SimpleNamespace) -> None:
    """Positive control: a quote-laden reference is data, never SQL."""
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(supplier_invoice_no="FAC-1'; DROP TABLE users; --"),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 200, resp.text
    assert _count(env, "users") == len(USERS)
    with env.db.transaction() as conn:
        row = conn.execute("SELECT supplier_invoice_no FROM supplier_invoices LIMIT 1").fetchone()
    assert row["supplier_invoice_no"] == "FAC-1'; DROP TABLE users; --"


def test_mass_assignment_fields_are_ignored(env: SimpleNamespace) -> None:
    """Positive control: identity/state fields in the body cannot be asserted."""
    resp = env.client.post(
        "/api/v1/invoices",
        json=_invoice_body(company_id=2, workflow_state="Posted", payload_sha256="deadbeef", id=999),
        headers=_auth(_token(env, "clerk_a")),
    )
    assert resp.status_code == 200, resp.text
    with env.db.transaction() as conn:
        row = conn.execute(
            "SELECT company_id, workflow_state, payload_sha256 FROM supplier_invoices"
            " WHERE supplier_invoice_no = 'FAC-SEC34-001'"
        ).fetchone()
    assert row["company_id"] == 1
    assert row["workflow_state"] == "Validated"
    assert row["payload_sha256"] != "deadbeef"


# ---------------------------------------------------------------------------
# SEC-07 — backup permissions
# ---------------------------------------------------------------------------


def test_backup_archive_is_owner_only(env: SimpleNamespace) -> None:
    """SEC-07: the archive carries the key rings and must not be world-readable."""
    target = env.tmp_path / "backups" / "state.enc"
    create_encrypted_backup(env.db, env.vault, target, Fernet.generate_key())
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert not target.stat().st_mode & stat.S_IROTH


# ---------------------------------------------------------------------------
# SEC-08 — request body bound
# ---------------------------------------------------------------------------


def test_oversized_json_body_is_refused_before_authentication(env: SimpleNamespace) -> None:
    """SEC-08: the token endpoint must not buffer an arbitrary body."""
    oversized = json.dumps({"username": "clerk_a", "password": "x" * (DEFAULT_MAX_JSON_REQUEST_BYTES + 1024)})
    resp = env.client.post(
        "/api/v1/auth/token", content=oversized, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413, resp.text


def test_oversized_authenticated_json_body_is_refused(env: SimpleNamespace) -> None:
    """SEC-08: authenticated JSON endpoints are bounded too."""
    payload = json.dumps({"reason": "x" * (DEFAULT_MAX_JSON_REQUEST_BYTES + 1024)})
    resp = env.client.post(
        "/api/v1/invoices/1/reverse",
        content=payload,
        headers={**_auth(_token(env, "acct_a")), "Content-Type": "application/json"},
    )
    assert resp.status_code == 413, resp.text


# ---------------------------------------------------------------------------
# SEC-09 — username validation
# ---------------------------------------------------------------------------


def test_username_with_control_characters_is_rejected(env: SimpleNamespace) -> None:
    """SEC-09: a newline in a username forges log and audit lines."""
    resp = env.client.post(
        "/api/v1/users",
        json={"username": "evil\nadmin", "password": "Abcd1234!", "role": "clerk"},
        headers=_auth(_token(env, "admin_a")),
    )
    assert resp.status_code == 422, resp.text
    assert _count(env, "users") == len(USERS)


# ---------------------------------------------------------------------------
# SEC-10 — transport / browser hardening
# ---------------------------------------------------------------------------


def test_security_headers_are_present(env: SimpleNamespace) -> None:
    """SEC-10: nosniff on every response, no-store on authenticated ones."""
    public = env.client.get("/healthz")
    assert public.headers["X-Content-Type-Options"] == "nosniff"
    authenticated = env.client.get("/api/v1/invoices", headers=_auth(_token(env, "clerk_a")))
    assert authenticated.headers["X-Content-Type-Options"] == "nosniff"
    assert authenticated.headers["Cache-Control"] == "no-store"
    assert authenticated.headers["X-Frame-Options"] == "DENY"


def test_api_documentation_is_disabled_by_default(env: SimpleNamespace) -> None:
    """Positive control: the schema surface stays closed unless opted in."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert env.client.get(path).status_code == 404, path


def test_no_cors_wildcard_is_served(env: SimpleNamespace) -> None:
    """Positive control: no permissive CORS policy is configured."""
    resp = env.client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_spoofed_identity_headers_do_not_authenticate(env: SimpleNamespace) -> None:
    """Positive control: X-User-Id / X-Company-Id are not credentials."""
    resp = env.client.get(
        "/api/v1/invoices/1",
        headers={"X-User-Id": "7", "X-Company-Id": "1", "X-Role": "admin"},
    )
    assert resp.status_code == 401, resp.text
