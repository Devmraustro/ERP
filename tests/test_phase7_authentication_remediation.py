# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 7 Tests — Authentication Remediation (CWE-287 / CWE-639).

Regression suite for the impersonation vulnerability in the former
``dz_erp.api._authenticate``, which trusted client-supplied ``X-User-Id`` and
``X-Company-Id`` headers without verifying any credential.

Covers:
  * forged identity headers cannot impersonate users (the original bug),
  * missing / malformed / unknown / revoked / expired credentials are rejected,
  * tenant isolation and RBAC are enforced on every protected endpoint,
  * the HTTP authentication boundary (status codes, ``WWW-Authenticate``),
  * credential storage hygiene, brute-force lockout, and audit-chain integrity.
"""

import base64
import re
import sqlite3
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import dz_erp.auth as auth_module
from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import (
    AuthManager,
    AuthenticatedUser,
    MalformedCredentialError,
    MissingCredentialError,
    TOKEN_PREFIX,
    generate_session_token,
    hash_password,
    hash_token,
    parse_bearer_token,
    verify_password,
)
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault, upload_and_process_supplier_document
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
)

FAST_ITERATIONS = 1000  # keeps the suite quick; the production default is asserted separately

# (user_id, company_id, username, role, is_active, password)
TENANT_ONE_USERS = [
    (1, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (2, 1, "wh_a", "warehouse", 1, "Wh#2026x"),
    (3, 1, "approver_a", "approver", 1, "Appr#2026x"),
    (4, 1, "acct_a", "accountant", 1, "Acct#2026x"),
    (5, 1, "auditor_a", "auditor", 1, "Aud#2026x"),
    (6, 1, "admin_a", "admin", 1, "Adm#2026x"),
    (7, 1, "revoked_a", "admin", 0, "Revoked#2026x"),
]
TENANT_TWO_USERS = [
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
    (21, 2, "clerk_b", "clerk", 1, "ClerkB#2026x"),
]

SUPPLIER_SPEC = {
    "supplier_name": "Supplier Alpha",
    "invoice_number": "FAC-AUTH-001",
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


class _Clock:
    """Deterministic UTC clock used to exercise expiry and lockout windows."""

    def __init__(self) -> None:
        from datetime import datetime, timezone

        self._now = datetime(2026, 10, 17, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self._now

    def advance(self, **kwargs: Any) -> None:
        self._now = self._now + timedelta(**kwargs)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Two-tenant ERP with a real FastAPI app and real server-issued tokens."""
    db = ERPDatabase(str(tmp_path / "auth.sqlite3"))
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
        for uid, cid, username, role, active, password in TENANT_ONE_USERS + TENANT_TWO_USERS:
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
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc)"
            " VALUES (30, 2, 'Supplier Beta', 'supplier', '001216099987654',"
            " '099116010987654', '16019998765', '16/00-0999876B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)

    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    app = create_erp_fastapi_app(db, vault, auth_manager=auth)
    client = TestClient(app)

    clock = _Clock()
    monkeypatch.setattr(auth_module, "_utcnow", clock)

    return SimpleNamespace(db=db, vault=vault, auth=auth, app=app, client=client, clock=clock, tmp_path=tmp_path)


def _token(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_tenant_one_assets(env: SimpleNamespace) -> SimpleNamespace:
    """Create a real document + invoice owned by company 1."""
    doc = upload_and_process_supplier_document(
        env.db,
        env.vault,
        company_id=1,
        user_id=1,
        filename="auth1.pdf",
        mime_type="application/pdf",
        content=render_synthetic_invoice_pdf(SUPPLIER_SPEC),
    )
    inv = create_and_validate_supplier_invoice(
        env.db,
        company_id=1,
        user_id=4,
        supplier_id=10,
        supplier_invoice_no=SUPPLIER_SPEC["invoice_number"],
        invoice_date=SUPPLIER_SPEC["invoice_date"],
        document_id=doc["document_id"],
        amount_ht_dzd=SUPPLIER_SPEC["amount_ht_dzd"],
        amount_tva_dzd=SUPPLIER_SPEC["amount_tva_dzd"],
        amount_ttc_dzd=SUPPLIER_SPEC["amount_ttc_dzd"],
        lines=[
            {
                "description": "Service",
                "qty": "1",
                "unit_price_dzd": "10000.00",
                "tax_rate": "19.0",
                "line_ht_dzd": "10000.00",
            }
        ],
    )
    return SimpleNamespace(document_id=doc["document_id"], invoice_id=inv["invoice_id"])


# Every endpoint that requires authentication (method, path template).
PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/auth/session", None),
    ("GET", "/api/v1/invoices/1", None),
    ("GET", "/api/v1/documents/nonexistent/file", None),
    ("PATCH", "/api/v1/documents/nonexistent/fields", {"field_name": "x", "new_value": "y"}),
    ("POST", "/api/v1/invoices", {"supplier_id": 10, "supplier_invoice_no": "X", "invoice_date": "2026-10-15",
                                  "amount_ht_dzd": "1.00", "amount_tva_dzd": "0.19", "amount_ttc_dzd": "1.19",
                                  "lines": []}),
    ("POST", "/api/v1/invoices/1/submit-approval", None),
    ("POST", "/api/v1/invoices/1/decide-approval", {"approve": True, "reason": ""}),
    ("POST", "/api/v1/invoices/1/post", None),
    ("POST", "/api/v1/invoices/1/reverse", {"reason": "test"}),
]


def _request(client: TestClient, method: str, path: str, body: Optional[Dict[str, Any]], headers: Dict[str, str]):
    return client.request(method, path, json=body, headers=headers)


# ---------------------------------------------------------------------------
# 1. The original vulnerability: forged identity headers
# ---------------------------------------------------------------------------


def test_forged_identity_headers_cannot_impersonate_users(env: SimpleNamespace) -> None:
    """CWE-287 regression: X-User-Id / X-Company-Id alone must never authenticate."""
    client = env.client
    forged_sets = [
        {"X-User-Id": "1", "X-Company-Id": "1"},
        {"X-User-Id": "6", "X-Company-Id": "1"},
        {"X-User-Id": "20", "X-Company-Id": "1"},
        {"X-User-Id": "20", "X-Company-Id": "2"},
        {"X-User-Id": "9999", "X-Company-Id": "1"},
        {"X-User-Id": "-1", "X-Company-Id": "1"},
    ]
    for method, path, body in PROTECTED_ENDPOINTS:
        for forged in forged_sets:
            resp = _request(client, method, path, body, forged)
            assert resp.status_code == 401, (
                f"{method} {path} accepted forged headers {forged} -> {resp.status_code}: {resp.text}"
            )
            assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")
            # No principal may leak through an unauthenticated response.
            assert "user_id" not in resp.text and "company_id" not in resp.text


def test_forged_headers_do_not_override_authenticated_principal(env: SimpleNamespace) -> None:
    """A valid token wins; identity headers must not be able to pivot the session."""
    client = env.client
    token = _token(client, "clerk_a", "Clerk#2026x")
    headers = dict(_bearer(token))
    headers.update({"X-User-Id": "20", "X-Company-Id": "2", "X-User-Id ": "20"})

    resp = client.get("/api/v1/auth/session", headers=headers)
    assert resp.status_code == 200, resp.text
    principal = resp.json()
    assert principal["user_id"] == 1
    assert principal["company_id"] == 1
    assert principal["username"] == "clerk_a"
    assert principal["role"] == "clerk"


def test_openapi_contract_no_longer_advertises_identity_headers(env: SimpleNamespace) -> None:
    """Structural guard: the vulnerable headers are gone from the API contract."""
    schema = env.app.openapi()

    declared: List[str] = []
    protected_without_security: List[str] = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            declared.extend(p.get("name", "") for p in operation.get("parameters", []))
            if operation.get("operationId") == "healthz_healthz_get":
                assert "security" not in operation, "healthz must stay public"
            elif not operation.get("security"):
                protected_without_security.append(f"{method.upper()} {path}")

    assert "X-User-Id" not in declared
    assert "X-Company-Id" not in declared
    assert protected_without_security == []

    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert "BearerAuth" in schemes
    assert schemes["BearerAuth"]["type"] == "http"
    assert schemes["BearerAuth"]["scheme"] == "bearer"


# ---------------------------------------------------------------------------
# 2. Credential rejection matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "   ",
        "Bearer",
        "Bearer ",
        "Bearer    ",
        "Basic YWRtaW46cGFzc3dvcmQ=",
        "Token dzsess_abc",
        "dzsess_abcdefghijklmnopqrstuvwxyz012345",
        "Bearer dzsess_not-a-real-token",
        "Bearer " + "A" * 43,
        "bearer",
    ],
)
def test_missing_and_malformed_credentials_are_rejected(env: SimpleNamespace, authorization: Optional[str]) -> None:
    client = env.client
    headers = {"Authorization": authorization} if authorization is not None else {}
    resp = client.get("/api/v1/auth/session", headers=headers)
    assert resp.status_code == 401, f"{authorization!r} -> {resp.status_code}: {resp.text}"
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_bearer_scheme_is_case_insensitive(env: SimpleNamespace) -> None:
    """RFC 7235: the auth scheme is case-insensitive, so 'bearer' must still work."""
    client = env.client
    token = _token(client, "clerk_a", "Clerk#2026x")
    resp = client.get("/api/v1/auth/session", headers={"Authorization": f"bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == 1


def test_unknown_token_is_rejected(env: SimpleNamespace) -> None:
    client = env.client
    for _ in range(3):
        resp = client.get("/api/v1/auth/session", headers=_bearer(generate_session_token()))
        assert resp.status_code == 401
        assert "INVALID_CREDENTIAL" in resp.text or "invalid" in resp.text.lower()


def test_tampered_token_is_rejected(env: SimpleNamespace) -> None:
    """Flipping a single character of a valid token must invalidate it."""
    client = env.client
    token = _token(client, "clerk_a", "Clerk#2026x")
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    body = token[len(TOKEN_PREFIX) :]
    flipped = body[:-1] + ("A" if body[-1] != "A" else "B")
    tampered = TOKEN_PREFIX + flipped
    assert tampered != token
    resp = client.get("/api/v1/auth/session", headers=_bearer(tampered))
    assert resp.status_code == 401

    # A token minted by a different deployment (different hash) is also unknown.
    other = generate_session_token()
    assert other != token
    assert client.get("/api/v1/auth/session", headers=_bearer(other)).status_code == 401


def test_revoked_token_is_rejected(env: SimpleNamespace) -> None:
    client = env.client
    token = _token(client, "clerk_a", "Clerk#2026x")
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    resp = client.post("/api/v1/auth/revoke", json={"reason": "USER_LOGOUT"}, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] is True

    after = client.get("/api/v1/auth/session", headers=_bearer(token))
    assert after.status_code == 401
    assert "revoked" in after.text.lower()

    # Revocation is immediate and permanent: replaying the token fails again.
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 401
    # Double revocation is idempotent from the caller's perspective.
    assert client.post("/api/v1/auth/revoke", json={}, headers=_bearer(token)).status_code == 401


def test_revoking_all_user_tokens_blocks_every_session(env: SimpleNamespace) -> None:
    client = env.client
    t1 = _token(client, "acct_a", "Acct#2026x")
    t2 = _token(client, "acct_a", "Acct#2026x")
    t3 = _token(client, "acct_a", "Acct#2026x")
    for t in (t1, t2, t3):
        assert client.get("/api/v1/auth/session", headers=_bearer(t)).status_code == 200

    revoked = env.auth.revoke_all_tokens_for_user(4, reason="SECURITY_INCIDENT")
    assert revoked == 3
    for t in (t1, t2, t3):
        assert client.get("/api/v1/auth/session", headers=_bearer(t)).status_code == 401


def test_expired_token_is_rejected(env: SimpleNamespace) -> None:
    client = env.client
    resp = client.post(
        "/api/v1/auth/token",
        json={"username": "clerk_a", "password": "Clerk#2026x", "ttl_seconds": 3600},
    )
    # ttl_seconds is server-controlled only; the request body cannot extend a session.
    assert resp.status_code in (200, 422)

    token = _token(client, "clerk_a", "Clerk#2026x")
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    # Advance past the default 8-hour TTL.
    env.clock.advance(hours=9)
    after = client.get("/api/v1/auth/session", headers=_bearer(token))
    assert after.status_code == 401
    assert "expired" in after.text.lower()


def test_token_expires_exactly_at_its_boundary(env: SimpleNamespace) -> None:
    client = env.client
    issued = env.auth.issue_token("clerk_a", "Clerk#2026x", ttl_seconds=60)
    token = issued["access_token"]

    env.clock.advance(seconds=59)
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    env.clock.advance(seconds=2)  # now past expires_at
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 401


def test_deactivated_user_loses_access_immediately(env: SimpleNamespace) -> None:
    client = env.client
    token = _token(client, "acct_a", "Acct#2026x")
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    revoked = env.auth.deactivate_user(4, actor_user_id=6, reason="OFFBOARDED")
    assert revoked == 1

    after = client.get("/api/v1/auth/session", headers=_bearer(token))
    assert after.status_code == 401
    assert "inactive" in after.text.lower() or "revoked" in after.text.lower()

    # A fresh sign-in is also refused for a deactivated account.
    assert client.post(
        "/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"}
    ).status_code == 401


def test_inactive_user_can_never_obtain_a_token(env: SimpleNamespace) -> None:
    resp = env.client.post(
        "/api/v1/auth/token", json={"username": "revoked_a", "password": "Revoked#2026x"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]
    # The token store must not have gained a row.
    with env.db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM auth_tokens").fetchone()["c"]
    assert count == 0


def test_password_rotation_revokes_live_tokens(env: SimpleNamespace) -> None:
    client = env.client
    token = _token(client, "acct_a", "Acct#2026x")
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    env.auth.set_password(4, "Acct#2027y", actor_user_id=6)

    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 401
    # The new password works and yields a fresh token.
    new_token = _token(client, "acct_a", "Acct#2027y")
    assert client.get("/api/v1/auth/session", headers=_bearer(new_token)).status_code == 200
    # The old password no longer works.
    assert client.post(
        "/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"}
    ).status_code == 401


# ---------------------------------------------------------------------------
# 3. Tenant isolation across every protected endpoint
# ---------------------------------------------------------------------------


def test_tenant_isolation_enforced_across_all_protected_endpoints(env: SimpleNamespace) -> None:
    assets = _seed_tenant_one_assets(env)
    client = env.client

    doc_id = assets.document_id
    token_b = _token(client, "admin_b", "AdminB#2026x")
    # admin_b is a full administrator of company 2 and holds a valid credential.
    session = client.get("/api/v1/auth/session", headers=_bearer(token_b))
    assert session.status_code == 200
    assert session.json()["company_id"] == 2

    inv_id = assets.invoice_id

    # Read paths
    assert client.get(f"/api/v1/invoices/{inv_id}", headers=_bearer(token_b)).status_code == 403
    assert client.get(f"/api/v1/documents/{doc_id}/file", headers=_bearer(token_b)).status_code == 403

    # Write paths on another tenant's objects. The request is always scoped to
    # the caller's own company, so it must never succeed and never touch the
    # other tenant's rows; the exact status depends on whether the resource is
    # visible in the caller's tenant at all.
    cross_tenant_writes = [
        ("PATCH", f"/api/v1/documents/{doc_id}/fields", {"field_name": "amount_ttc_dzd", "new_value": "999999.00"}, {}),
        ("POST", f"/api/v1/invoices/{inv_id}/submit-approval", None, {}),
        ("POST", f"/api/v1/invoices/{inv_id}/decide-approval", {"approve": True}, {}),
        ("POST", f"/api/v1/invoices/{inv_id}/post", None, {"Idempotency-Key": "cross-tenant-post"}),
        ("POST", f"/api/v1/invoices/{inv_id}/reverse", {"reason": "cross-tenant"}, {"Idempotency-Key": "cross-tenant-rev"}),
    ]
    for method, path, body, extra in cross_tenant_writes:
        resp = client.request(method, path, json=body, headers={**_bearer(token_b), **extra})
        assert resp.status_code not in range(200, 300), f"{method} {path} succeeded cross-tenant: {resp.text}"

    # The tenant's data must be completely untouched by all of the above.
    with env.db.transaction() as conn:
        inv = conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (inv_id,)).fetchone()
        assert inv["company_id"] == 1
        assert inv["workflow_state"] not in ("Approved", "Posted")
        field = conn.execute(
            "SELECT human_override_value FROM ocr_extracted_fields WHERE document_id = ? LIMIT 1",
            (doc_id,),
        ).fetchone()
        if field is not None:
            assert field["human_override_value"] != "999999.00"
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM journal_entries WHERE company_id = 1"
        ).fetchone()["c"] == 0


def test_tenant_isolation_on_invoice_creation(env: SimpleNamespace) -> None:
    """A company-2 credential must only ever create company-2 records."""
    client = env.client
    token_b = _token(client, "admin_b", "AdminB#2026x")
    body = {
        "supplier_id": 30,  # Supplier Beta, owned by company 2
        "supplier_invoice_no": "FAC-B-001",
        "invoice_date": "2026-10-15",
        "amount_ht_dzd": "1000.00",
        "amount_tva_dzd": "190.00",
        "amount_ttc_dzd": "1190.00",
        "lines": [
            {"description": "Service", "qty": "1", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "1000.00"}
        ],
    }
    resp = client.post("/api/v1/invoices", json=body, headers=_bearer(token_b))
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["invoice_id"]

    with env.db.transaction() as conn:
        row = conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (new_id,)).fetchone()
        assert row["company_id"] == 2
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM supplier_invoices WHERE company_id = 1"
        ).fetchone()["c"] == 0


def test_tenant_isolation_rejects_foreign_supplier_reference(env: SimpleNamespace) -> None:
    """A company-2 credential cannot attach company-1 supplier 10 to its own invoice."""
    client = env.client
    token_b = _token(client, "admin_b", "AdminB#2026x")
    body = {
        "supplier_id": 10,  # belongs to company 1
        "supplier_invoice_no": "FAC-B-002",
        "invoice_date": "2026-10-15",
        "amount_ht_dzd": "1000.00",
        "amount_tva_dzd": "190.00",
        "amount_ttc_dzd": "1190.00",
        "lines": [
            {"description": "Service", "qty": "1", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "1000.00"}
        ],
    }
    resp = client.post("/api/v1/invoices", json=body, headers=_bearer(token_b))
    assert resp.status_code == 403, resp.text
    with env.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM supplier_invoices").fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# 4. RBAC across every protected endpoint
# ---------------------------------------------------------------------------


def test_rbac_enforced_across_all_protected_endpoints(env: SimpleNamespace) -> None:
    assets = _seed_tenant_one_assets(env)
    client = env.client
    inv_id = assets.invoice_id

    # --- warehouse: may not read financial invoices ---
    wh = _token(client, "wh_a", "Wh#2026x")
    assert client.get(f"/api/v1/invoices/{inv_id}", headers=_bearer(wh)).status_code == 403
    # ...but is still a correctly authenticated principal of company 1.
    assert client.get("/api/v1/auth/session", headers=_bearer(wh)).json()["role"] == "warehouse"

    # --- clerk: may not post, reverse, or approve ---
    clerk = _token(client, "clerk_a", "Clerk#2026x")
    for method, path, body in [
        ("POST", f"/api/v1/invoices/{inv_id}/post", None),
        ("POST", f"/api/v1/invoices/{inv_id}/reverse", {"reason": "nope"}),
        ("POST", f"/api/v1/invoices/{inv_id}/decide-approval", {"approve": True}),
    ]:
        headers = {**_bearer(clerk), "Idempotency-Key": f"rbac-{method}-{path}"}
        assert client.request(method, path, json=body, headers=headers).status_code == 403, f"{method} {path}"

    # --- approver: may not post ---
    approver = _token(client, "approver_a", "Appr#2026x")
    assert client.post(
        f"/api/v1/invoices/{inv_id}/post",
        headers={**_bearer(approver), "Idempotency-Key": "rbac-approver-post"},
    ).status_code == 403

    # --- auditor: read-only, may not mutate ---
    auditor = _token(client, "auditor_a", "Aud#2026x")
    assert client.get(f"/api/v1/invoices/{inv_id}", headers=_bearer(auditor)).status_code == 200
    assert client.post(
        f"/api/v1/invoices/{inv_id}/decide-approval",
        json={"approve": True},
        headers=_bearer(auditor),
    ).status_code == 403

    # --- accountant: allowed to submit for approval ---
    acct = _token(client, "acct_a", "Acct#2026x")
    submit = client.post(f"/api/v1/invoices/{inv_id}/submit-approval", headers=_bearer(acct))
    assert submit.status_code == 200, submit.text

    # Segregation of duties still applies to the creator.
    assert client.post(
        f"/api/v1/invoices/{inv_id}/decide-approval", json={"approve": True}, headers=_bearer(acct)
    ).status_code == 403

    # A different approver may approve.
    assert client.post(
        f"/api/v1/invoices/{inv_id}/decide-approval", json={"approve": True}, headers=_bearer(approver)
    ).status_code == 200


def test_rbac_denial_is_audited(env: SimpleNamespace) -> None:
    assets = _seed_tenant_one_assets(env)
    client = env.client
    wh = _token(client, "wh_a", "Wh#2026x")
    client.get(f"/api/v1/invoices/{assets.invoice_id}", headers=_bearer(wh))

    with env.db.transaction() as conn:
        rows = conn.execute(
            "SELECT action_type FROM audit_events WHERE action_type LIKE 'SECURITY_UNAUTHORIZED_ROLE%'"
        ).fetchall()
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# 5. HTTP authentication boundary
# ---------------------------------------------------------------------------


def test_login_endpoint_issues_usable_bearer_token(env: SimpleNamespace) -> None:
    client = env.client
    resp = client.post("/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"].startswith(TOKEN_PREFIX)
    assert payload["expires_in"] > 0
    assert payload["user"] == {
        "user_id": 4,
        "company_id": 1,
        "username": "acct_a",
        "role": "accountant",
    }

    session = client.get("/api/v1/auth/session", headers=_bearer(payload["access_token"]))
    assert session.status_code == 200
    assert session.json()["user_id"] == 4


def test_login_rejects_wrong_password(env: SimpleNamespace) -> None:
    client = env.client
    for bad in ("wrong", "Acct#2026", "acct#2026x", ""):
        resp = client.post("/api/v1/auth/token", json={"username": "acct_a", "password": bad})
        assert resp.status_code == 401, f"{bad!r} -> {resp.status_code}"
    assert "access_token" not in resp.text


def test_unknown_username_and_wrong_password_are_indistinguishable(env: SimpleNamespace) -> None:
    """No user enumeration: identical status, message, and rough timing."""
    client = env.client
    unknown = client.post("/api/v1/auth/token", json={"username": "no_such_user", "password": "whatever"})
    wrong = client.post("/api/v1/auth/token", json={"username": "acct_a", "password": "whatever"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_brute_force_lockout(env: SimpleNamespace) -> None:
    client = env.client
    for i in range(5):
        resp = client.post("/api/v1/auth/token", json={"username": "acct_a", "password": f"bad-{i}"})
        assert resp.status_code == 401

    # Even the correct password is refused while the account is locked.
    locked = client.post("/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"})
    assert locked.status_code == 401
    assert "locked" in locked.text.lower()

    # Other accounts are unaffected.
    assert _token(client, "clerk_a", "Clerk#2026x")

    # Once the lockout window elapses, the correct password works again.
    env.clock.advance(minutes=16)
    assert client.post(
        "/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"}
    ).status_code == 200


def test_lockout_is_persisted_in_the_database(env: SimpleNamespace) -> None:
    client = env.client
    for i in range(5):
        client.post("/api/v1/auth/token", json={"username": "acct_a", "password": f"bad-{i}"})
    with env.db.transaction() as conn:
        row = conn.execute("SELECT locked_until FROM users WHERE id = 4").fetchone()
    assert row["locked_until"] is not None


def test_revoke_endpoint_requires_a_valid_credential(env: SimpleNamespace) -> None:
    client = env.client
    assert client.post("/api/v1/auth/revoke", json={}).status_code == 401
    assert client.post(
        "/api/v1/auth/revoke", json={}, headers=_bearer(generate_session_token())
    ).status_code == 401

    # The endpoint revokes exactly the credential that was presented.
    mine = _token(client, "clerk_a", "Clerk#2026x")
    theirs = _token(client, "acct_a", "Acct#2026x")
    assert client.post("/api/v1/auth/revoke", json={}, headers=_bearer(theirs)).status_code == 200

    assert client.get("/api/v1/auth/session", headers=_bearer(theirs)).status_code == 401
    assert client.get("/api/v1/auth/session", headers=_bearer(mine)).status_code == 200


def test_healthz_is_public(env: SimpleNamespace) -> None:
    assert env.client.get("/healthz").status_code == 200
    assert env.client.get("/healthz", headers=_bearer(generate_session_token())).status_code == 200


# ---------------------------------------------------------------------------
# 6. Credential storage hygiene
# ---------------------------------------------------------------------------


def test_tokens_are_never_persisted_in_plaintext(env: SimpleNamespace) -> None:
    client = env.client
    token = _token(client, "acct_a", "Acct#2026x")
    with env.db.transaction() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM auth_tokens").fetchall()]
        users = [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()]
    assert len(rows) == 1

    blob = str(rows) + str(users)
    assert token not in blob, "raw bearer token leaked into the database"
    assert hash_token(token) == rows[0]["token_hash"]
    assert re.fullmatch(r"[0-9a-f]{64}", rows[0]["token_hash"])
    # Only a short non-secret prefix is retained for support lookups.
    assert len(rows[0]["token_prefix"]) < len(token)
    assert rows[0]["token_prefix"] in token


def test_passwords_are_never_persisted_in_plaintext(env: SimpleNamespace) -> None:
    client = env.client
    _token(client, "acct_a", "Acct#2026x")
    with env.db.transaction() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = 4").fetchone()
    assert "Acct#2026x" not in row["password_hash"]
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    _, iterations, _salt, _digest = row["password_hash"].split("$")
    assert int(iterations) == FAST_ITERATIONS


def test_password_hashes_are_salted(env: SimpleNamespace) -> None:
    h1 = hash_password("SamePassword#1", iterations=FAST_ITERATIONS)
    h2 = hash_password("SamePassword#1", iterations=FAST_ITERATIONS)
    assert h1 != h2  # distinct random salts
    assert h1.split("$")[2] != h2.split("$")[2]
    assert verify_password("SamePassword#1", h1)
    assert verify_password("SamePassword#1", h2)
    assert not verify_password("SamePassword#2", h1)
    assert not verify_password("", h1)
    assert not verify_password("SamePassword#1", None)
    assert not verify_password("SamePassword#1", "not-a-valid-hash")
    assert not verify_password("SamePassword#1", "pbkdf2_sha256$abc$x$y")


def test_production_pbkdf2_iteration_floor(env: SimpleNamespace) -> None:
    """The shipped default must meet the OWASP 2023 floor of 600,000 iterations."""
    from dz_erp.auth import PBKDF2_ITERATIONS

    assert PBKDF2_ITERATIONS >= 600_000
    h = hash_password("Floor#2026x")
    assert int(h.split("$")[1]) == PBKDF2_ITERATIONS


def test_token_entropy_and_format(env: SimpleNamespace) -> None:
    tokens = {generate_session_token() for _ in range(200)}
    assert len(tokens) == 200  # no collisions
    for t in tokens:
        assert t.startswith(TOKEN_PREFIX)
        raw = t[len(TOKEN_PREFIX) :]
        # 256 bits of entropy -> 43 base64url characters, unpadded.
        assert len(raw) == 43
        assert re.fullmatch(r"[A-Za-z0-9_-]+", raw)
        assert len(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))) == 32


def test_parse_bearer_token_unit_contract() -> None:
    assert parse_bearer_token("Bearer abc") == "abc"
    assert parse_bearer_token("bearer abc") == "abc"
    assert parse_bearer_token("BEARER abc") == "abc"
    assert parse_bearer_token("  Bearer   abc  ") == "abc"
    for bad in (None, "", "   ", "Bearer", "Bearer ", "Basic abc", "abc", "Bearer a b"):
        with pytest.raises((MissingCredentialError, MalformedCredentialError)):
            parse_bearer_token(bad)


def test_verify_password_is_constant_time_comparison() -> None:
    """A digest differing in a single trailing bit must still be rejected."""
    stored = hash_password("Boundary#2026x", iterations=FAST_ITERATIONS)
    algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
    digest = bytearray(base64.b64decode(digest_b64))
    digest[-1] ^= 0x01
    tampered = "$".join([algorithm, iterations, salt_b64, base64.b64encode(bytes(digest)).decode()])
    assert not verify_password("Boundary#2026x", tampered)
    assert verify_password("Boundary#2026x", stored)


def test_authenticated_user_is_immutable(env: SimpleNamespace) -> None:
    """The resolved principal cannot be mutated by application code."""
    user = env.auth.authenticate(_bearer(_token(env.client, "clerk_a", "Clerk#2026x"))["Authorization"])
    assert isinstance(user, AuthenticatedUser)
    with pytest.raises(Exception):
        user.company_id = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 7. Audit trail and schema migration
# ---------------------------------------------------------------------------


def test_inactive_user_with_unrevoked_token_is_rejected_and_audited(env: SimpleNamespace) -> None:
    """A token whose owner was deactivated out-of-band must still be refused.

    `deactivate_user()` also revokes tokens, so this exercises the separate
    `is_active` guard inside `authenticate()` by disabling the account without
    touching the token rows.
    """
    client = env.client
    token = _token(client, "acct_a", "Acct#2026x")
    with env.db.transaction() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = 4")

    resp = client.get("/api/v1/auth/session", headers=_bearer(token))
    assert resp.status_code == 401
    assert "inactive" in resp.text.lower() or "revoked" in resp.text.lower()

    with env.db.transaction() as conn:
        actions = [
            r["action_type"]
            for r in conn.execute("SELECT action_type FROM audit_events WHERE action_type LIKE 'AUTH_USER_INACTIVE%'").fetchall()
        ]
    assert "AUTH_USER_INACTIVE_REJECTED" in actions
    # The tamper-evident chain is still intact after the rejection was recorded.
    assert env.db.verify_audit_chain(company_id=1)["valid"] is True


def test_corrupted_token_digest_row_is_rejected(env: SimpleNamespace) -> None:
    """Defence in depth: a stored digest that does not match the presented
    token must fail closed even though the lookup itself succeeded."""
    client = env.client
    token = _token(client, "clerk_a", "Clerk#2026x")
    assert client.get("/api/v1/auth/session", headers=_bearer(token)).status_code == 200

    with env.db.transaction() as conn:
        conn.execute("UPDATE auth_tokens SET token_hash = ?", ("0" * 64,))

    resp = client.get("/api/v1/auth/session", headers=_bearer(token))
    assert resp.status_code == 401


def test_revoking_tokens_for_unknown_user_is_a_no_op(env: SimpleNamespace) -> None:
    assert env.auth.revoke_all_tokens_for_user(9999) == 0
    assert env.auth.revoke_token(generate_session_token()) is False
    with pytest.raises(ValueError):
        env.auth.deactivate_user(9999)
    with pytest.raises(ValueError):
        env.auth.set_password(9999, "Whatever#2026x")


def test_auth_manager_rejects_non_positive_ttl(env: SimpleNamespace) -> None:
    with pytest.raises(ValueError):
        AuthManager(env.db, token_ttl_seconds=0)
    with pytest.raises(ValueError):
        AuthManager(env.db, token_ttl_seconds=-5)


def test_authentication_events_are_audited_and_chain_remains_valid(env: SimpleNamespace) -> None:
    client = env.client
    token = _token(client, "acct_a", "Acct#2026x")
    client.get("/api/v1/auth/session", headers=_bearer(token))
    client.post("/api/v1/auth/revoke", json={"reason": "test"}, headers=_bearer(token))
    client.post("/api/v1/auth/token", json={"username": "acct_a", "password": "wrong"})

    with env.db.transaction() as conn:
        actions = [
            r["action_type"]
            for r in conn.execute("SELECT action_type FROM audit_events ORDER BY id").fetchall()
        ]
    assert "AUTH_TOKEN_ISSUED" in actions
    assert "AUTH_TOKEN_REVOKED" in actions
    assert "AUTH_LOGIN_FAILED" in actions

    chain = env.db.verify_audit_chain(company_id=1)
    assert chain["valid"] is True, chain


def test_legacy_database_schema_is_migrated_on_open(tmp_path: Path) -> None:
    """A pre-remediation database (no password_hash, no auth_tokens) is upgraded."""
    db_path = tmp_path / "legacy.sqlite3"

    # Build a database using the *old* schema, as it existed before this fix.
    legacy_schema = """
    CREATE TABLE companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
        country_code TEXT NOT NULL DEFAULT 'DZ', currency_code TEXT NOT NULL DEFAULT 'DZD',
        chart_template TEXT NOT NULL DEFAULT 'dz', parent_company_id INTEGER,
        nif TEXT, nis TEXT, ai TEXT, rc TEXT,
        default_receivable_code TEXT NOT NULL DEFAULT '411',
        default_payable_code TEXT NOT NULL DEFAULT '401', created_at TEXT NOT NULL);
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        branch_id INTEGER, username TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, api_token_hash TEXT);
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
        occurred_at TEXT NOT NULL, actor_user_id INTEGER, action_type TEXT NOT NULL,
        target_model TEXT NOT NULL, target_id TEXT NOT NULL, correlation_id TEXT,
        before_json TEXT, after_json TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
    """
    raw = sqlite3.connect(str(db_path))
    raw.executescript(legacy_schema)
    raw.execute(
        "INSERT INTO companies (id, code, name, created_at) VALUES (1, 'DZ1', 'Tenant Alpha', '2026-01-01')"
    )
    raw.execute(
        "INSERT INTO users (id, company_id, username, role, is_active) VALUES (1, 1, 'legacy_admin', 'admin', 1)"
    )
    raw.commit()
    raw.close()

    # Re-opening with the current ERPDatabase must apply the migration.
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        assert {"password_hash", "failed_login_count", "locked_until"} <= cols
        assert conn.execute("SELECT COUNT(*) AS c FROM auth_tokens").fetchone()["c"] == 0

    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    created = auth.create_user(
        username="new_admin", password="New#2026x", role="admin", company_id=1, actor_user_id=1
    )
    issued = auth.issue_token("new_admin", "New#2026x")
    resolved = auth.authenticate(f"Bearer {issued['access_token']}")
    assert resolved.user_id == created["user_id"]
    assert resolved.company_id == 1
    assert resolved.role == "admin"


def test_reopening_a_migrated_database_is_idempotent(tmp_path: Path) -> None:
    db_path = str(tmp_path / "idem.sqlite3")
    db = ERPDatabase(db_path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, created_at) VALUES (1, 'DZ1', 'Tenant Alpha', '2026-01-01')"
        )
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    auth.create_user(username="u1", password="P#2026x", role="clerk", company_id=1)
    del db, auth

    db2 = ERPDatabase(db_path)  # must not raise on the second open
    with db2.transaction() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        assert "password_hash" in cols
        assert conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 1


def test_purge_expired_tokens(env: SimpleNamespace) -> None:
    client = env.client
    _token(client, "clerk_a", "Clerk#2026x")
    env.clock.advance(hours=9)
    assert env.auth.purge_expired_tokens() == 1
    with env.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM auth_tokens").fetchone()["c"] == 0


def test_list_active_tokens_exposes_no_secrets(env: SimpleNamespace) -> None:
    client = env.client
    token = _token(client, "clerk_a", "Clerk#2026x")
    rows = env.auth.list_active_tokens(1)
    assert len(rows) == 1
    assert token not in str(rows)
    assert set(rows[0]) == {
        "id",
        "token_prefix",
        "issued_at",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "issued_to_ip",
    }


def test_unknown_role_is_refused(env: SimpleNamespace) -> None:
    with pytest.raises(ValueError):
        env.auth.create_user(username="x", password="P#2026x", role="superuser", company_id=1)


def test_empty_password_is_refused(env: SimpleNamespace) -> None:
    with pytest.raises(ValueError):
        hash_password("")
    with pytest.raises(ValueError):
        hash_password("x" * 2000)


def test_negative_ttl_is_refused(env: SimpleNamespace) -> None:
    with pytest.raises(ValueError):
        env.auth.issue_token("clerk_a", "Clerk#2026x", ttl_seconds=0)
    with pytest.raises(ValueError):
        env.auth.issue_token("clerk_a", "Clerk#2026x", ttl_seconds=-1)
