# SPDX-License-Identifier: LGPL-3.0-only
"""F-R6-1 — bootstrapping the first admin must never leave a half-built tenant.

The documented first deployment step is interactive::

    docker compose -f ops/docker-compose.yml run --rm bootstrap admin

The bootstrap used to **insert and commit the company first** and only then
prompt for the password. An operator who mistyped, pressed Ctrl-C, or ran the
command without a TTY was left with a company row, no user and no audit event —
and every retry then died with a raw
``sqlite3.IntegrityError: UNIQUE constraint failed: companies.code`` instead of
an actionable message. The deployment could not be bootstrapped without manual
SQL, on the very first step of the runbook.

The behaviour pinned here:

1. an aborted prompt writes **nothing**;
2. the documented command still succeeds afterwards;
3. an orphaned bootstrap company (the state the old ordering left behind) is
   **reused**, so recovery needs no SQL and no deletion of company rows;
4. an already-bootstrapped company is a documented refusal, not a traceback;
5. if the user insert fails, the company row is removed again.

No password policy is asserted: the module only requires that a failed bootstrap
leaves the database exactly as it found it.
"""

from __future__ import annotations

import getpass
import sqlite3
from pathlib import Path

import pytest

from dz_erp.auth import AuthManager
from dz_erp.database import ERPDatabase
from dz_erp.serve import _bootstrap_admin


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "boot.sqlite3"
    ERPDatabase(str(path))  # creates the schema, as the CLI does
    return path


def _state(path: Path) -> tuple[list, int]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        companies = [dict(r) for r in conn.execute("SELECT id, code, name FROM companies")]
        users = int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])
    finally:
        conn.close()
    return companies, users


def _prompt_aborts(*_args, **_kwargs):
    raise EOFError("no TTY")


def test_fr6_1_an_aborted_password_prompt_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    path = _db(tmp_path)
    monkeypatch.setattr(getpass, "getpass", _prompt_aborts)

    with pytest.raises(EOFError):
        _bootstrap_admin(path, "admin", "Smoke Co")

    companies, users = _state(path)
    assert companies == [], f"an aborted prompt must not create a company: {companies}"
    assert users == 0


def test_fr6_1_an_aborted_prompt_then_the_documented_retry_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """The exact operator sequence the old ordering broke."""
    path = _db(tmp_path)
    monkeypatch.setattr(getpass, "getpass", _prompt_aborts)
    with pytest.raises(EOFError):
        _bootstrap_admin(path, "admin", "Smoke Co")

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "Str0ng-Pass!2026")
    user_id = _bootstrap_admin(path, "admin", "Smoke Co")

    companies, users = _state(path)
    assert user_id == 1
    assert users == 1
    assert [c["code"] for c in companies] == ["BOOT-ADMIN"]


def test_fr6_1_an_orphaned_bootstrap_company_is_reused_not_duplicated(
    tmp_path: Path, monkeypatch
) -> None:
    """Recovery from the state the pre-fix code left on disk, with no manual SQL."""
    path = _db(tmp_path)
    db = ERPDatabase(str(path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (code, name, created_at) VALUES ('BOOT-ADMIN', ?, ?)",
            ("Smoke Co", "2026-01-01T00:00:00+00:00"),
        )
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "Str0ng-Pass!2026")

    user_id = _bootstrap_admin(path, "admin", "Smoke Co")

    companies, users = _state(path)
    assert len(companies) == 1, f"the orphan must be reused, not duplicated: {companies}"
    assert companies[0]["id"] == 1
    assert users == 1
    assert user_id == 1


def test_fr6_1_an_already_bootstrapped_company_is_a_documented_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    path = _db(tmp_path)
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "Str0ng-Pass!2026")
    _bootstrap_admin(path, "admin", "Smoke Co")

    with pytest.raises(SystemExit) as refused:
        _bootstrap_admin(path, "admin", "Smoke Co")
    message = str(refused.value)
    assert "already bootstrapped" in message
    assert "IntegrityError" not in message and "UNIQUE" not in message

    companies, users = _state(path)
    assert len(companies) == 1 and users == 1


def test_fr6_1_a_failed_user_insert_removes_the_company_it_created(
    tmp_path: Path, monkeypatch
) -> None:
    """The one window that cannot be one transaction, closed by compensation."""
    path = _db(tmp_path)
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "Str0ng-Pass!2026")

    def exploding_create_user(self, **_kwargs):
        raise sqlite3.IntegrityError("simulated failure after the company insert")

    monkeypatch.setattr(AuthManager, "create_user", exploding_create_user)

    with pytest.raises(SystemExit) as failed:
        _bootstrap_admin(path, "admin", "Smoke Co")
    assert "removed" in str(failed.value)

    companies, users = _state(path)
    assert companies == [], f"the half-built company must be removed: {companies}"
    assert users == 0


def test_fr6_1_a_failed_user_insert_leaves_a_pre_existing_company_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """Compensation must never delete a company it did not create."""
    path = _db(tmp_path)
    db = ERPDatabase(str(path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (code, name, created_at) VALUES ('BOOT-ADMIN', ?, ?)",
            ("Smoke Co", "2026-01-01T00:00:00+00:00"),
        )
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "Str0ng-Pass!2026")

    def exploding_create_user(self, **_kwargs):
        raise sqlite3.IntegrityError("simulated failure")

    monkeypatch.setattr(AuthManager, "create_user", exploding_create_user)

    with pytest.raises(SystemExit) as failed:
        _bootstrap_admin(path, "admin", "Smoke Co")
    assert "left untouched" in str(failed.value)

    companies, _users = _state(path)
    assert len(companies) == 1, "a pre-existing company must survive a failed bootstrap"
