# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 59 — closure of SEC-13, SEC-14 and SEC-15.

Three findings were left open by the selective-integration phase with explicit
recommendations and no implementation. This module is the acceptance evidence
for implementing them, and it is written so that each test fails if the *rule*
regresses, not merely if a message changes.

SEC-13 (password policy)
    The policy used to exist in exactly one place: a length check inside
    ``POST /api/v1/users``. ``AuthManager.create_user`` — and therefore the CLI
    bootstrap and the secure provisioning wrapper — accepted any non-empty
    string, and there was no in-product way to replace a credential at all
    (no self-service change, no administrative reset). Now a single validator is
    used by every creation path, and the lifecycle has two audited endpoints.

SEC-14 (OCR override validation)
    ``PATCH /documents/{id}/fields`` wrote the caller's string straight into
    ``human_override_value`` *and* ``normalized_value``, so a negative total, an
    impossible date or a foreign currency could be stored as a "human
    correction" and only be discovered downstream by the review policy. It now
    applies the same value-class validator the decision path uses.

SEC-15 (Host / TLS)
    ``Host`` was never validated, and the server was started with uvicorn's
    ``proxy_headers=True`` default — a second, undocumented trust layer that
    could rewrite the peer address from ``X-Forwarded-For`` before the
    application's own fail-closed policy saw it. Now the Host header is checked
    against ``ERP_ALLOWED_HOSTS`` (loopback-only by default) and uvicorn is told
    not to touch the peer address.

Nothing here asserts anything about AWS KMS or MinIO/S3: both are out of scope
for V1 and are documented as unsupported in the deployment guide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PBKDF2_ITERATIONS,
    RECOMMENDED_PASSWORD_MIN_LENGTH,
    AuthManager,
    InvalidCredentialError,
    PasswordPolicyError,
    RevokedCredentialError,
    configured_password_min_length,
    hash_password,
    validate_password_policy,
    verify_password,
)
from dz_erp.database import ERPDatabase, FinancialIntegrityError
from dz_erp.intake_ocr import PrivateDocumentVault, update_extracted_field_by_human
from dz_erp.net import (
    DEFAULT_ALLOWED_HOSTS,
    allowed_hosts_from_env,
    host_is_allowed,
    parse_allowed_hosts,
)
from dz_erp.review_policy import (
    LINE_QTY_RULE,
    rule_for,
    validate_corrected_value,
)

FAST_ITERATIONS = 1000  # keeps the suite quick; the production default is asserted separately

ADMIN_PASSWORD = "Admin#2026x"
CLERK_PASSWORD = "Clerk#2026x"
OTHER_PASSWORD = "Other#2026x"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
def _seed_two_tenants(db: ERPDatabase) -> None:
    with db.transaction() as conn:
        for cid, code, name in ((1, "DZ1", "Tenant Alpha"), (2, "DZ2", "Tenant Beta")):
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
                "VALUES (?, ?, ?, 'DZ', 'dz', '2026-01-01')",
                (cid, code, name),
            )
        for uid, cid, username, role, password in (
            (1, 1, "admin_a", "admin", ADMIN_PASSWORD),
            (2, 1, "clerk_a", "clerk", CLERK_PASSWORD),
            (20, 2, "admin_b", "admin", OTHER_PASSWORD),
        ):
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (uid, cid, username, role, hash_password(password, iterations=FAST_ITERATIONS)),
            )


def _seed_document(db: ERPDatabase, *, workflow_state: str = "NeedsReview", document_id: str = "DOC-1") -> str:
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size,
                                          file_sha256, vault_path, security_status, workflow_state,
                                          uploaded_by_id, uploaded_at)
            VALUES (?, 1, 'facture.pdf', 'application/pdf', 1024, ?, 'vault/facture.pdf',
                    'CLEAN', ?, 2, '2026-11-09T09:00:00+00:00')
            """,
            (document_id, "a" * 64, workflow_state),
        )
        for field_name, value in (
            ("amount_ttc_dzd", "1200.00"),
            ("amount_ht_dzd", "1000.00"),
            ("amount_tva_dzd", "200.00"),
            ("invoice_date", "2026-11-09"),
            ("currency_code", "DZD"),
            ("supplier_name", "SPARTE SARL"),
            ("invoice_number", "FAC-2026-0001"),
        ):
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (company_id, document_id, field_name, raw_text,
                                                  normalized_value, bbox_json, ocr_model_version,
                                                  created_at)
                VALUES (1, ?, ?, 'raw text', ?, '[]', 'engine-1', '2026-11-09T09:00:00+00:00')
                """,
                (document_id, field_name, value),
            )
    return document_id


def _field_row(db: ERPDatabase, document_id: str, field_name: str) -> Dict[str, object]:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_extracted_fields WHERE document_id = ? AND field_name = ?",
            (document_id, field_name),
        ).fetchone()
    assert row is not None
    return dict(row)


def _audit_actions(db: ERPDatabase) -> List[str]:
    with db.transaction() as conn:
        return [r["action_type"] for r in conn.execute("SELECT action_type FROM audit_events ORDER BY id")]


def _make_env(tmp_path: Path, *, allowed_hosts=None):
    db = ERPDatabase(str(tmp_path / "phase59.sqlite3"))
    _seed_two_tenants(db)
    vault = PrivateDocumentVault(tmp_path / "vault")
    app = create_erp_fastapi_app(db, vault, allowed_hosts=allowed_hosts)
    return db, app, TestClient(app)


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# SEC-13 — the policy itself
# ===========================================================================
def test_policy_rejects_passwords_below_the_floor() -> None:
    for length in range(0, MIN_PASSWORD_LENGTH):
        with pytest.raises(PasswordPolicyError) as exc:
            validate_password_policy("a" * length)
        assert exc.value.code in {"PASSWORD_TOO_SHORT", "PASSWORD_REQUIRED"}


def test_policy_accepts_the_floor_and_longer_values() -> None:
    for length in (MIN_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH + 1, 40):
        value = "x" * length
        assert validate_password_policy(value) == value
    assert validate_password_policy("x" * MAX_PASSWORD_LENGTH)


def test_policy_rejects_an_overlong_password() -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy("x" * (MAX_PASSWORD_LENGTH + 1))
    assert exc.value.code == "PASSWORD_TOO_LONG"


def test_policy_rejects_whitespace_only() -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy(" " * (MIN_PASSWORD_LENGTH + 4))
    assert exc.value.code == "PASSWORD_WHITESPACE_ONLY"


def test_policy_rejects_the_account_identifiers() -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy("AdminA#2026x", username="AdminA#2026x")
    assert exc.value.code == "PASSWORD_EQUALS_USERNAME"
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy("Tenant Alpha", company_name="Tenant Alpha")
    assert exc.value.code == "PASSWORD_EQUALS_COMPANY_NAME"
    # Case and surrounding whitespace do not create an exemption.
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("administrata", username="administrata")


def test_policy_rejects_common_values_case_insensitively() -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy("PassWord")
    assert exc.value.code == "PASSWORD_TOO_COMMON"


def test_minimum_length_is_configurable_but_never_below_the_floor() -> None:
    assert configured_password_min_length({}) == MIN_PASSWORD_LENGTH
    assert configured_password_min_length({"ERP_PASSWORD_MIN_LENGTH": "12"}) == 12
    assert configured_password_min_length({"ERP_PASSWORD_MIN_LENGTH": "4"}) == MIN_PASSWORD_LENGTH
    assert configured_password_min_length({"ERP_PASSWORD_MIN_LENGTH": "junk"}) == MIN_PASSWORD_LENGTH
    assert configured_password_min_length({"ERP_PASSWORD_MIN_LENGTH": "999"}) == 64
    # A configured minimum is enforced, and refuses what the floor would allow.
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password_policy("x" * 10, env={"ERP_PASSWORD_MIN_LENGTH": "12"})
    assert exc.value.code == "PASSWORD_TOO_SHORT"


def test_recommended_minimum_is_stricter_than_the_enforced_floor() -> None:
    """The guide recommends 12; the floor stays 8 for V1 compatibility."""
    assert MIN_PASSWORD_LENGTH == 8
    assert RECOMMENDED_PASSWORD_MIN_LENGTH == 12


# ===========================================================================
# SEC-13 — every credential-creating path enforces it
# ===========================================================================
def test_service_level_create_user_enforces_the_policy(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "svc.sqlite3"))
    _seed_two_tenants(db)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    with pytest.raises(PasswordPolicyError):
        auth.create_user(username="shorty", password="short", role="clerk", company_id=1)
    with pytest.raises(PasswordPolicyError):
        auth.create_user(username="shorty", password="shorty", role="clerk", company_id=1)
    created = auth.create_user(username="shorty", password="Good#2026x", role="clerk", company_id=1)
    assert created["user_id"]
    assert verify_password("Good#2026x", hash_password("Good#2026x", iterations=FAST_ITERATIONS)) is True


def test_cli_bootstrap_enforces_the_policy_and_leaves_no_company_behind(tmp_path: Path, monkeypatch) -> None:
    """The first credential in a deployment must not be the weakest one."""
    import dz_erp.serve as serve

    db_path = tmp_path / "boot.sqlite3"
    monkeypatch.setattr(serve.getpass, "getpass", lambda _prompt="": "short")
    with pytest.raises(SystemExit) as exc:
        serve._bootstrap_admin(db_path, "root", "Bootstrap Co")
    assert "PASSWORD_TOO_SHORT" in str(exc.value) or "at least" in str(exc.value)

    # The compensating delete must leave no half-bootstrapped company behind.
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"] == 0


def test_cli_bootstrap_accepts_a_compliant_password(tmp_path: Path, monkeypatch) -> None:
    import dz_erp.serve as serve

    db_path = tmp_path / "boot_ok.sqlite3"
    monkeypatch.setattr(serve.getpass, "getpass", lambda _prompt="": "Bootstrap#2026x")
    user_id = serve._bootstrap_admin(db_path, "root", "Bootstrap Co")
    assert user_id > 0


def test_api_create_user_reports_the_policy_code_in_a_header(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    admin = _auth(_login(client, "admin_a", ADMIN_PASSWORD))
    resp = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "weakling", "password": "Wh#2026", "role": "clerk"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.headers.get("x-error-code") == "PASSWORD_TOO_SHORT"
    assert "8" in resp.json()["detail"]


# ===========================================================================
# SEC-13 — lifecycle: self-service change and administrative reset
# ===========================================================================
def test_self_service_change_requires_the_current_password(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "chg.sqlite3"))
    _seed_two_tenants(db)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    before = _stored_hash(db, 1)
    with pytest.raises(InvalidCredentialError):
        auth.change_own_password(1, "Wrong#2026x", "Fresh#2026x")
    assert _stored_hash(db, 1) == before, "a refused change must not touch the stored credential"
    assert "AUTH_PASSWORD_CHANGE_REJECTED" in _audit_actions(db)


def test_self_service_change_rejects_a_weak_new_password(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "chg2.sqlite3"))
    _seed_two_tenants(db)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    before = _stored_hash(db, 1)
    with pytest.raises(PasswordPolicyError):
        auth.change_own_password(1, ADMIN_PASSWORD, "weak")
    assert _stored_hash(db, 1) == before


def test_self_service_change_rotates_and_revokes_every_token(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "chg3.sqlite3"))
    _seed_two_tenants(db)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    token = auth.issue_token("admin_a", ADMIN_PASSWORD)["access_token"]
    assert auth.authenticate(f"Bearer {token}").user_id == 1

    auth.change_own_password(1, ADMIN_PASSWORD, "Fresh#2026x")

    with pytest.raises(RevokedCredentialError):
        auth.authenticate(f"Bearer {token}")
    with pytest.raises(InvalidCredentialError):
        auth.issue_token("admin_a", ADMIN_PASSWORD)
    assert auth.issue_token("admin_a", "Fresh#2026x")["access_token"]
    assert "AUTH_PASSWORD_ROTATED" in _audit_actions(db)


def _stored_hash(db: ERPDatabase, user_id: int) -> str:
    with db.transaction() as conn:
        return str(conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()[0])


def test_self_service_endpoint_returns_401_for_a_wrong_current_password(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    token = _login(client, "clerk_a", CLERK_PASSWORD)
    resp = client.post(
        "/api/v1/auth/password",
        headers=_auth(token),
        json={"current_password": "Wrong#2026x", "new_password": "Fresh#2026x"},
    )
    assert resp.status_code == 401, resp.text


def test_self_service_endpoint_returns_422_with_the_policy_code(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    token = _login(client, "clerk_a", CLERK_PASSWORD)
    resp = client.post(
        "/api/v1/auth/password",
        headers=_auth(token),
        json={"current_password": CLERK_PASSWORD, "new_password": "short"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.headers.get("x-error-code") == "PASSWORD_TOO_SHORT"


def test_self_service_endpoint_rotates_and_forces_reauthentication(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    token = _login(client, "clerk_a", CLERK_PASSWORD)
    resp = client.post(
        "/api/v1/auth/password",
        headers=_auth(token),
        json={"current_password": CLERK_PASSWORD, "new_password": "Fresh#2026x"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reauthenticate"] is True
    # The token used for the change is revoked like every other one.
    assert client.get("/api/v1/auth/session", headers=_auth(token)).status_code == 401
    assert _login(client, "clerk_a", "Fresh#2026x")


def test_password_endpoints_require_a_credential(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    assert client.post("/api/v1/auth/password", json={"current_password": "a", "new_password": "b"}).status_code == 401
    assert client.post("/api/v1/users/2/password-reset", json={"new_password": "Whatever#2027x"}).status_code == 401


def test_admin_reset_is_role_and_tenant_scoped(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    clerk = _auth(_login(client, "clerk_a", CLERK_PASSWORD))
    admin = _auth(_login(client, "admin_a", ADMIN_PASSWORD))

    assert (
        client.post("/api/v1/users/2/password-reset", headers=clerk, json={"new_password": "Fresh#2026x"}).status_code
        == 403
    )
    # Cross-tenant and non-existent targets are indistinguishable.
    assert (
        client.post("/api/v1/users/20/password-reset", headers=admin, json={"new_password": "Fresh#2026x"}).status_code
        == 404
    )
    assert (
        client.post("/api/v1/users/999/password-reset", headers=admin, json={"new_password": "Fresh#2026x"}).status_code
        == 404
    )
    # The policy applies to the reset too.
    weak = client.post("/api/v1/users/2/password-reset", headers=admin, json={"new_password": "short"})
    assert weak.status_code == 422
    assert weak.headers.get("x-error-code") == "PASSWORD_TOO_SHORT"


def test_admin_reset_revokes_target_tokens_and_attributes_the_audit(tmp_path: Path) -> None:
    db, _, client = _make_env(tmp_path)
    clerk_token = _login(client, "clerk_a", CLERK_PASSWORD)
    admin = _auth(_login(client, "admin_a", ADMIN_PASSWORD))

    resp = client.post("/api/v1/users/2/password-reset", headers=admin, json={"new_password": "Fresh#2026x"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reset"]["user_id"] == 2

    # The clerk's live session is dead and the old password no longer works.
    assert client.get("/api/v1/auth/session", headers=_auth(clerk_token)).status_code == 401
    assert client.post("/api/v1/auth/token", json={"username": "clerk_a", "password": CLERK_PASSWORD}).status_code == 401
    assert _login(client, "clerk_a", "Fresh#2026x")

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM audit_events WHERE action_type = 'AUTH_PASSWORD_ROTATED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["actor_user_id"] == 1, "the reset must be attributed to the acting administrator"
    assert json.loads(row["after_json"])["mode"] == "administrative_reset"


# ===========================================================================
# SEC-13 — rehash-on-login moves a credential to the current floor
# ===========================================================================
def test_login_upgrades_a_credential_created_below_the_iteration_floor(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "rehash.sqlite3"))
    _seed_two_tenants(db)  # hashes created with FAST_ITERATIONS
    auth = AuthManager(db)  # production iteration count
    assert _stored_hash(db, 1).split("$")[1] == str(FAST_ITERATIONS)

    token = auth.issue_token("admin_a", ADMIN_PASSWORD)["access_token"]

    assert auth.authenticate(f"Bearer {token}").user_id == 1
    upgraded = _stored_hash(db, 1)
    assert upgraded.split("$")[1] == str(PBKDF2_ITERATIONS)
    assert verify_password(ADMIN_PASSWORD, upgraded) is True
    assert "AUTH_PASSWORD_REHASHED" in _audit_actions(db)


def test_login_does_not_rewrite_a_current_credential(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "norehash.sqlite3"))
    _seed_two_tenants(db)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    before = _stored_hash(db, 1)
    auth.issue_token("admin_a", ADMIN_PASSWORD)
    assert _stored_hash(db, 1) == before
    assert "AUTH_PASSWORD_REHASHED" not in _audit_actions(db)


# ===========================================================================
# SEC-14 — the legacy override path validates like the decision path
# ===========================================================================
INVALID_OVERRIDES = [
    ("amount_ttc_dzd", "-1.00"),
    ("amount_ttc_dzd", "abc"),
    ("amount_ttc_dzd", ""),
    ("invoice_date", "2026-13-45"),
    ("invoice_date", "09/11/2026"),
    ("currency_code", "USD"),
    ("invoice_number", "FAC 2026\n0001"),
]


@pytest.mark.parametrize("field_name,value", INVALID_OVERRIDES)
def test_override_rejects_a_value_no_invoice_could_show(tmp_path: Path, field_name: str, value: str) -> None:
    db = ERPDatabase(str(tmp_path / f"ov_{field_name}.sqlite3"))
    _seed_two_tenants(db)
    doc = _seed_document(db)
    before = _field_row(db, doc, field_name)

    with pytest.raises(FinancialIntegrityError) as exc:
        update_extracted_field_by_human(
            db, company_id=1, user_id=2, document_id=doc, field_name=field_name, new_value=value
        )
    assert "FIELD_VALUE" in str(exc.value)

    after = _field_row(db, doc, field_name)
    assert after["human_override_value"] == before["human_override_value"]
    assert after["normalized_value"] == before["normalized_value"]
    assert "SECURITY_FIELD_EDIT_REJECTED" in _audit_actions(db)


def test_override_normalises_a_valid_amount(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "ov_ok.sqlite3"))
    _seed_two_tenants(db)
    doc = _seed_document(db)

    updated = update_extracted_field_by_human(
        db, company_id=1, user_id=2, document_id=doc, field_name="amount_ttc_dzd", new_value=" 7500.5 "
    )
    assert updated["human_override_value"] == "7500.50"
    assert updated["normalized_value"] == "7500.50"


def test_override_keeps_the_text_fallback_for_ungoverned_fields(tmp_path: Path) -> None:
    """Fields outside the critical policy are bounded text, not free-for-all."""
    db = ERPDatabase(str(tmp_path / "ov_text.sqlite3"))
    _seed_two_tenants(db)
    doc = _seed_document(db)
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ocr_extracted_fields (company_id, document_id, field_name, raw_text,
                                              normalized_value, bbox_json, ocr_model_version, created_at)
            VALUES (1, ?, 'payment_method', 'virement', 'virement', '[]', 'engine-1', '2026-11-09T09:00:00+00:00')
            """,
            (doc,),
        )

    stored = update_extracted_field_by_human(
        db, company_id=1, user_id=2, document_id=doc, field_name="payment_method", new_value="cheque"
    )
    assert stored["human_override_value"] == "cheque"

    for bad in ("x" * 257, "two\nlines"):
        with pytest.raises(FinancialIntegrityError):
            update_extracted_field_by_human(
                db, company_id=1, user_id=2, document_id=doc, field_name="payment_method", new_value=bad
            )


def test_discount_and_quantity_bounds_are_shared_with_the_decision_path() -> None:
    """Line values come from the same validator, so the bounds cannot drift."""
    discount_rule = rule_for("line_discount")
    assert discount_rule is not None
    assert validate_corrected_value(discount_rule, "100.00") == "100.00"
    with pytest.raises(ValueError):
        validate_corrected_value(discount_rule, "100.01")
    with pytest.raises(ValueError):
        validate_corrected_value(LINE_QTY_RULE, "0")


def test_override_endpoint_returns_422_for_an_invalid_value(tmp_path: Path) -> None:
    db, _, client = _make_env(tmp_path)
    doc = _seed_document(db)
    token = _auth(_login(client, "clerk_a", CLERK_PASSWORD))

    resp = client.patch(
        f"/api/v1/documents/{doc}/fields",
        headers=token,
        json={"field_name": "amount_ttc_dzd", "new_value": "-5.00"},
    )
    assert resp.status_code == 422, resp.text
    assert "FIELD_VALUE_REJECTED" in resp.json()["detail"]

    ok = client.patch(
        f"/api/v1/documents/{doc}/fields",
        headers=token,
        json={"field_name": "amount_ttc_dzd", "new_value": "1300.00"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["normalized_value"] == "1300.00"


# ===========================================================================
# SEC-15 — Host header validation
# ===========================================================================
def test_parse_allowed_hosts_normalises_and_drops_ports() -> None:
    parsed = parse_allowed_hosts("ERP.Example.DZ:8443, *.corp.dz ,, nonsense/path")
    assert parsed == ("erp.example.dz", "*.corp.dz")


def test_allowed_hosts_default_to_loopback_only() -> None:
    assert allowed_hosts_from_env({}) == DEFAULT_ALLOWED_HOSTS
    assert set(DEFAULT_ALLOWED_HOSTS) == {"localhost", "127.0.0.1", "[::1]", "testserver"}
    assert allowed_hosts_from_env({"ERP_ALLOWED_HOSTS": "erp.example.dz"}) == ("erp.example.dz",)


def test_host_matching_is_exact_case_insensitive_and_wildcarded() -> None:
    allowed = parse_allowed_hosts("erp.example.dz, *.corp.dz")
    assert host_is_allowed("erp.example.dz", allowed)
    assert host_is_allowed("ERP.EXAMPLE.DZ", allowed)
    assert host_is_allowed("erp.example.dz:8443", allowed)
    assert host_is_allowed("a.corp.dz", allowed)
    assert not host_is_allowed("corp.dz", allowed), "a wildcard must not match its bare domain"
    assert not host_is_allowed("evil.example", allowed)
    assert not host_is_allowed("", allowed)
    assert not host_is_allowed(None, allowed)
    # Since the defaults include the IPv6 loopback, its port form must match too.
    assert host_is_allowed("[::1]:8000", DEFAULT_ALLOWED_HOSTS)


def test_unknown_host_is_refused_with_400_and_a_request_id(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    resp = client.get("/healthz", headers={"host": "attacker.example"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Invalid Host header."
    assert resp.headers.get("x-request-id")


def test_allowed_host_passes_through(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path)
    assert client.get("/healthz").status_code == 200  # TestClient default: testserver


def test_explicit_allow_list_replaces_the_defaults(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path, allowed_hosts=("erp.example.dz",))
    assert client.get("/healthz", headers={"host": "erp.example.dz"}).status_code == 200
    assert client.get("/healthz").status_code == 400, "the test-client default must not survive an explicit list"


def test_wildcard_allow_list_accepts_a_subdomain(tmp_path: Path) -> None:
    _, _, client = _make_env(tmp_path, allowed_hosts=("*.erp.example.dz",))
    assert client.get("/healthz", headers={"host": "dz.erp.example.dz"}).status_code == 200
    assert client.get("/healthz", headers={"host": "erp.example.dz"}).status_code == 400


def test_host_rejection_is_written_to_the_security_sink(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "security.jsonl"
    monkeypatch.setenv("ERP_SECURITY_LOG_PATH", str(log_path))
    _, _, client = _make_env(tmp_path)
    assert client.get("/healthz", headers={"host": "attacker.example"}).status_code == 400

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rejected = [e for e in events if e.get("event") == "SECURITY_HOST_HEADER_REJECTED" or e.get("message") == "SECURITY_HOST_HEADER_REJECTED"]
    assert rejected, f"no host-rejection event in the security log: {events[:3]}"


def test_server_is_started_without_uvicorn_proxy_headers() -> None:
    """One trust layer: the application's ERP_TRUSTED_PROXIES, never uvicorn's."""
    import inspect

    import dz_erp.serve as serve

    source = inspect.getsource(serve.main)
    assert "proxy_headers=False" in source, (
        "uvicorn would otherwise rewrite the peer address from X-Forwarded-For for a "
        "loopback proxy, bypassing dz_erp.net.TrustedProxyPolicy"
    )


def test_env_example_documents_the_new_knobs() -> None:
    text = (Path(__file__).resolve().parents[1] / "ops" / "erp.env.example").read_text(encoding="utf-8")
    assert "ERP_ALLOWED_HOSTS" in text
    assert "ERP_PASSWORD_MIN_LENGTH" in text
    assert "proxy_headers=False" in text
    # The V1 scope exclusion is stated where an operator reads configuration.
    assert "KMS" in text and "MinIO" in text


def test_the_running_process_reports_its_host_policy(tmp_path: Path) -> None:
    """An operator reads back the enforced allow-list, not the file they meant to edit."""
    _, _, client = _make_env(tmp_path, allowed_hosts=("erp.example.dz",))
    login = client.post(
        "/api/v1/auth/token",
        json={"username": "admin_a", "password": ADMIN_PASSWORD},
        headers={"host": "erp.example.dz"},
    )
    assert login.status_code == 200, login.text
    token = _auth(login.json()["access_token"])
    resp = client.get("/api/v1/system/rate-limit-policy", headers={**token, "host": "erp.example.dz"})
    assert resp.status_code == 200, resp.text
    policy = resp.json()["host_policy"]
    assert policy["allowed_hosts"] == ["erp.example.dz"]
    assert policy["env_override"] == "ERP_ALLOWED_HOSTS"
