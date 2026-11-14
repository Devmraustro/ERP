# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 53 — Host-header allow-list (SEC-15), bootstrap atomicity, and repository hygiene."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import (
    AuthManager,
    InvalidCredentialError,
    PasswordPolicyError,
    validate_password_policy,
)
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.net import (
    DEFAULT_ALLOWED_HOSTS,
    allowed_hosts_from_env,
    describe_allowed_hosts,
    host_is_allowed,
    parse_allowed_hosts,
)
from dz_erp.serve import _bootstrap_admin, main as serve_main

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_PW = "Admin#2026Secure"


def _make_app(tmp_path: Path, **kwargs):
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Entreprise Alpha', 'DZ', 'dz', '2026-01-01T00:00:00+00:00')"
        )
    auth = AuthManager(db)
    auth.create_user(username="admin", password=ADMIN_PW, role="admin", company_id=1, actor_user_id=None)
    vault = PrivateDocumentVault(tmp_path / "vault")
    app = create_erp_fastapi_app(db, vault, auth_manager=auth, **kwargs)
    return db, app


def test_default_allowed_hosts_are_loopback_and_testserver_only() -> None:
    assert DEFAULT_ALLOWED_HOSTS == ("localhost", "127.0.0.1", "[::1]", "testserver")
    assert allowed_hosts_from_env({}) == DEFAULT_ALLOWED_HOSTS
    assert allowed_hosts_from_env({"ERP_ALLOWED_HOSTS": "   ,  "}) == DEFAULT_ALLOWED_HOSTS


def test_parse_allowed_hosts_normalises_ports_wildcards_and_ipv6() -> None:
    parsed = parse_allowed_hosts("ERP.Example.DZ:8443, *.sub.example.dz:443 [::1]:8000 127.0.0.1:9000")
    assert parsed == ("erp.example.dz", "*.sub.example.dz", "[::1]", "127.0.0.1")


def test_parse_allowed_hosts_drops_malformed_entries() -> None:
    parsed = parse_allowed_hosts("good.dz, https://bad.dz/path, bad/slash, [unclosed, host:notaport")
    assert parsed == ("good.dz",)


@pytest.mark.parametrize(
    "header",
    [
        "localhost",
        "LOCALHOST:8000",
        "127.0.0.1",
        "127.0.0.1:54321",
        "[::1]",
        "[::1]:8443",
        "app.corp.dz",
        "deep.app.corp.dz:443",
    ],
)
def test_host_is_allowed_accepts_configured_exact_and_wildcard_hosts(header: str) -> None:
    allowed = ("localhost", "127.0.0.1", "[::1]", "*.corp.dz")
    assert host_is_allowed(header, allowed) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        "evil.example",
        "corp.dz",  # wildcard *.corp.dz must not match bare apex unless explicitly listed
        "evilcorp.dz",
        "localhost, evil.example",
        "localhost/path",
        "localhost:abc",
        "[::1]:abc",
        "localhost\r\nX-Injected: 1",
    ],
)
def test_host_is_allowed_rejects_unknown_empty_and_malformed_headers(header: str | None) -> None:
    allowed = ("localhost", "127.0.0.1", "[::1]", "*.corp.dz")
    assert host_is_allowed(header, allowed) is False


def test_api_rejects_unlisted_host_with_400_and_audits_the_rejection(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, allowed_hosts=["erp.company.dz", "testserver"])
    client = TestClient(app)

    bad = client.get("/healthz", headers={"Host": "evil.attacker.invalid", "X-Request-Id": "req-host-001"})
    assert bad.status_code == 400
    assert bad.json() == {"detail": "Invalid Host header."}
    assert bad.headers["X-Request-Id"] == "req-host-001"
    assert bad.headers["X-Content-Type-Options"] == "nosniff"

    events = [r for r in app.state.logger.records if r["event"] == "SECURITY_HOST_HEADER_REJECTED"]
    assert len(events) == 1
    assert events[0]["correlation_id"] == "req-host-001"
    assert events[0]["context"]["host"] == "evil.attacker.invalid"
    assert events[0]["context"]["path"] == "/healthz"


def test_api_host_rejection_replaces_hostile_request_id(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, allowed_hosts=["erp.company.dz"])
    client = TestClient(app)

    bad = client.get("/healthz", headers={"Host": "evil.example", "X-Request-Id": "bad id with spaces\t!"})
    assert bad.status_code == 400
    echoed = bad.headers["X-Request-Id"]
    assert "bad" not in echoed and " " not in echoed


def test_api_accepts_configured_erp_allowed_hosts_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ERP_ALLOWED_HOSTS", "intranet.sarl.dz, *.branches.sarl.dz")
    _, app = _make_app(tmp_path)
    client = TestClient(app)

    assert client.get("/healthz", headers={"Host": "intranet.sarl.dz:8443"}).status_code == 200
    assert client.get("/healthz", headers={"Host": "setif.branches.sarl.dz"}).status_code == 200
    assert client.get("/healthz", headers={"Host": "localhost"}).status_code == 400


def test_admin_policy_endpoint_describes_active_allowed_hosts(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, allowed_hosts=["testserver", "*.erp.dz"])
    client = TestClient(app)
    tok = client.post("/api/v1/auth/token", json={"username": "admin", "password": ADMIN_PW}).json()["access_token"]
    resp = client.get("/api/v1/system/rate-limit-policy", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    host_policy = resp.json()["allowed_hosts"]
    assert host_policy == describe_allowed_hosts(("testserver", "*.erp.dz"))


def test_serve_main_passes_proxy_headers_false_to_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "serve.sqlite3"
    vault_path = tmp_path / "vault"
    monkeypatch.setenv("ERP_DB_PATH", str(db_path))
    monkeypatch.setenv("ERP_VAULT_PATH", str(vault_path))

    with patch("dz_erp.serve.getpass.getpass", return_value=ADMIN_PW):
        assert serve_main(["--bootstrap-admin", "admin", "--company-name", "SARL Test"]) == 0

    captured: dict = {}

    def _fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    with patch("uvicorn.run", side_effect=_fake_run):
        assert serve_main(["--host", "127.0.0.1", "--port", "8123"]) == 0

    assert captured["proxy_headers"] is False
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123


def test_bootstrap_admin_leaves_no_orphan_company_when_password_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "boot.sqlite3"
    # 1. Aborted prompt before DB write -> zero companies
    with patch("dz_erp.serve.getpass.getpass", side_effect=EOFError("no tty")):
        with pytest.raises(EOFError):
            _bootstrap_admin(db_path, "admin", "Orphan Check")
    assert not db_path.exists()

    # 2. Weak password rejected by AuthManager -> compensating delete leaves zero companies
    with patch("dz_erp.serve.getpass.getpass", return_value="short"):
        with pytest.raises(SystemExit):
            _bootstrap_admin(db_path, "admin", "Orphan Check")
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
    assert count == 0


def test_bootstrap_admin_loads_the_scf_chart(tmp_path: Path) -> None:
    """A bootstrapped deployment must be able to record an invoice.

    `--bootstrap-admin` created the company and the admin user but never loaded
    the SCF chart, so a freshly installed server answered `GET /api/v1/accounts`
    with an empty list and every master-data write failed with "… is not an
    account of this company's SCF chart". Posting validates account codes, so the
    deployment was unusable for its stated purpose while reporting itself
    healthy. The desktop bootstrap always loaded the chart; the CLI path did not.
    """
    from dz_erp.database import ERPDatabase

    db_path = tmp_path / "boot.sqlite3"
    with patch("dz_erp.serve.getpass.getpass", return_value="Str0ng-Admin-Pass!2026"):
        user_id = _bootstrap_admin(db_path, "admin", "Chart Check")
    assert user_id >= 1

    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("companies", "users", "accounts", "account_groups", "taxes")
        }
        loaded = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE action_type = 'LOAD_CHART_TEMPLATE'"
        ).fetchone()["n"]
    assert counts["companies"] == 1
    assert counts["users"] == 1
    # The chart sizes are asserted as a floor, not an exact number: a future SCF
    # template may legitimately add accounts, but a *missing* chart is the defect.
    assert counts["accounts"] >= 290, f"chart not loaded: {counts}"
    assert counts["account_groups"] >= 60, f"chart not loaded: {counts}"
    assert counts["taxes"] >= 8, f"chart not loaded: {counts}"
    assert loaded == 1, "the chart load must be recorded in the audit trail"


def test_bootstrap_admin_rejects_a_weak_password_before_writing_anything(tmp_path: Path) -> None:
    """The credential is validated before the first database write.

    Loading the chart rewrites `accounts`, `account_groups`, `taxes` and
    `timbre_rules` and writes an `audit_events` row. That row cannot be deleted
    (`trg_audit_no_delete`), so a rejected password discovered *after* the chart
    load would leave a company that can never be cleanly removed. Validating the
    policy first - it needs no connection - keeps bootstrap atomic.
    """
    from dz_erp.database import ERPDatabase

    db_path = tmp_path / "boot.sqlite3"
    with patch("dz_erp.serve.getpass.getpass", return_value="short"):
        with pytest.raises(SystemExit):
            _bootstrap_admin(db_path, "admin", "Weak Check")

    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        for table in ("companies", "users", "accounts", "account_groups", "taxes"):
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert n == 0, f"{table} was written before the password was validated"
        assert conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"] == 0


def test_repository_hygiene_and_packaging_configuration() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == ["dz_erp*"]
    assert "assets/fonts/*" in pyproject["tool"]["setuptools"]["package-data"]["dz_erp"]

    env_docs = (REPO_ROOT / "docs" / "ENVIRONMENT_VARIABLES.md").read_text(encoding="utf-8")
    assert "ERP_ALLOWED_HOSTS" in env_docs

    tracked = [
        p
        for p in subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO_ROOT)
        .decode("utf-8")
        .split("\0")
        if p
    ]
    forbidden_exts = {".db", ".sqlite", ".sqlite3", ".key", ".pem", ".pfx", ".p12", ".pyc", ".pyo"}
    leaked = [p for p in tracked if Path(p).suffix.lower() in forbidden_exts]
    assert leaked == [], f"forbidden artifacts tracked in Git: {leaked}"


def test_unified_password_policy_blocks_short_weak_and_identifier_passwords() -> None:
    with pytest.raises(PasswordPolicyError) as short_exc:
        validate_password_policy("short7!")
    assert short_exc.value.code == "PASSWORD_TOO_SHORT"

    with pytest.raises(PasswordPolicyError) as common_exc:
        validate_password_policy("password1")
    assert common_exc.value.code == "PASSWORD_TOO_COMMON"

    with pytest.raises(PasswordPolicyError) as user_exc:
        validate_password_policy("ComptableDZ", username="comptabledz")
    assert user_exc.value.code == "PASSWORD_EQUALS_USERNAME"

    with pytest.raises(PasswordPolicyError) as env_exc:
        validate_password_policy("TenChars#1", env={"ERP_PASSWORD_MIN_LENGTH": "12"})
    assert env_exc.value.code == "PASSWORD_TOO_SHORT"
    assert validate_password_policy("TwelveChars#1", env={"ERP_PASSWORD_MIN_LENGTH": "12"}) == "TwelveChars#1"


def test_password_rehashing_upgrades_stored_iterations_on_login_and_supports_self_service_change(
    tmp_path: Path,
) -> None:
    db = ERPDatabase(str(tmp_path / "rehash.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'SARL Rehash', 'DZ', 'dz', '2026-01-01T00:00:00+00:00')"
        )
    legacy_auth = AuthManager(db, pbkdf2_iterations=1_000)
    created = legacy_auth.create_user(
        username="legacy_acct",
        password="Legacy#2026Valid",
        role="accountant",
        company_id=1,
    )
    uid = created["user_id"]

    upgraded_auth = AuthManager(db, pbkdf2_iterations=2_500)
    issued = upgraded_auth.issue_token("legacy_acct", "Legacy#2026Valid")
    assert issued["access_token"].startswith("dzsess_")

    with db.transaction() as conn:
        stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()["password_hash"]
        rehash_events = conn.execute(
            "SELECT * FROM audit_events WHERE action_type = 'AUTH_PASSWORD_REHASHED'"
        ).fetchall()
    assert stored.startswith("pbkdf2_sha256$2500$")
    assert len(rehash_events) == 1

    # Self-service password rotation requires proof of current password
    with pytest.raises(InvalidCredentialError):
        upgraded_auth.change_own_password(uid, "WrongCurrent#1", "Rotated#2026Valid")
    upgraded_auth.change_own_password(uid, "Legacy#2026Valid", "Rotated#2026Valid")
    assert upgraded_auth.issue_token("legacy_acct", "Rotated#2026Valid")["access_token"].startswith("dzsess_")

