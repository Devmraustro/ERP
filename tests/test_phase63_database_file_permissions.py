# SPDX-License-Identifier: LGPL-3.0-only
"""Release-closure phase 63 — a new database file is owner-only.

Defect (found by the security re-check, not hypothesised): SQLite creates a
missing database with ``0666 & ~umask``. A deployment bootstrapped from an
ordinary shell (umask ``022``) therefore left ``erp.sqlite3`` at ``0644`` - the
ledger, the user table and the password hashes readable by every local account -
while the audit key beside it was ``0600``. The systemd units set ``UMask=0077``,
but the documented bootstrap command is run by hand, before any unit exists.

These tests pin the repair and its limits: only a file the application creates
is restricted, and an existing database keeps the mode its operator chose.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="POSIX mode bits are not access control on Windows (risk S5)"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def permissive_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@posix_only
def test_a_new_database_is_owner_only_under_a_permissive_umask(tmp_path, permissive_umask):
    db_path = tmp_path / "erp.sqlite3"

    ERPDatabase(str(db_path))

    assert _mode(db_path) == 0o600


@posix_only
def test_wal_and_shm_files_inherit_the_private_mode(tmp_path, permissive_umask):
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))

    # Hold a connection open mid-transaction so the sidecar files exist.
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (code, name, created_at) VALUES ('PERM', 'Perm SARL', '2026-01-01')"
        )
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sidecars = [p for p in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")) if p.exists()]
        if journal_mode.lower() == "wal":
            assert sidecars, "a WAL database mid-transaction must have sidecar files"
        for sidecar in sidecars:
            assert _mode(sidecar) == 0o600, sidecar.name


@posix_only
def test_an_existing_database_keeps_the_mode_its_operator_chose(tmp_path, permissive_umask):
    db_path = tmp_path / "erp.sqlite3"
    ERPDatabase(str(db_path))
    os.chmod(db_path, 0o640)  # e.g. a deliberate group-read arrangement for a backup account

    ERPDatabase(str(db_path))

    assert _mode(db_path) == 0o640


@posix_only
def test_a_stricter_umask_still_yields_a_writable_database(tmp_path):
    previous = os.umask(0o277)
    try:
        db_path = tmp_path / "erp.sqlite3"
        db = ERPDatabase(str(db_path))
        assert _mode(db_path) == 0o600
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO companies (code, name, created_at) VALUES ('UM', 'Umask SARL', '2026-01-01')"
            )
    finally:
        os.umask(previous)


@posix_only
def test_bootstrap_from_a_shell_leaves_no_world_readable_database(tmp_path, permissive_umask):
    """The documented first command, run exactly as an operator would run it."""
    import subprocess

    db_path = tmp_path / "state" / "erp.sqlite3"
    env = {**os.environ, "ERP_DB_PATH": str(db_path), "ERP_VAULT_PATH": str(tmp_path / "state" / "vault")}
    completed = subprocess.run(
        [sys.executable, "-m", "dz_erp.serve", "--bootstrap-admin", "admin", "--company-name", "Perm SARL"],
        input="Synthetic-Admin-Pass!2026\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert _mode(db_path) == 0o600
    assert _mode(Path(f"{db_path}.audit.key")) == 0o600
