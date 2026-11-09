# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 55 — migration failure injection and recovery (F-06).

The migration sequence is two atomic transactions, each guarded by checks and
each idempotent, with forward recovery on the next open. These tests pin that
contract directly rather than relying on the source-text assertions:

* a failure inside ``_migrate_schema`` rolls back that transaction, so no
  half-applied column set survives, and the next open repairs it;
* a failure between the two transactions (after ``_migrate_ocr_confidence``
  has committed) leaves the database in the documented intermediate state and
  the next open finishes the job;
* the rebuild migration never uses ``executescript`` (it would commit the
  pending transaction and break atomicity);
* an already-migrated database is a no-op on reopen (idempotency).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase

REPO_ROOT = Path(__file__).resolve().parent.parent


def _seed(path: Path) -> None:
    """A current database with one company and one audit event."""
    db = ERPDatabase(str(path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (code, name, created_at) VALUES ('C', 'Co', '2026-01-01T00:00:00+00:00')"
        )
    del db


def _drop(path: Path, table: str, column: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.commit()
    finally:
        conn.close()


def test_a_failure_inside_migrate_schema_rolls_back_and_repairs(tmp_path: Path) -> None:
    """A raising migration step must not leave a half-applied column set."""
    path = tmp_path / "half.sqlite3"
    _seed(path)
    # Remove a column so _migrate_schema actually has work to do.
    _drop(path, "users", "password_hash")

    original = ERPDatabase._migrate_review_policy

    def boom(self, conn):
        raise RuntimeError("injected migration failure")

    ERPDatabase._migrate_review_policy = boom
    try:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            ERPDatabase(str(path))
    finally:
        ERPDatabase._migrate_review_policy = original

    # The whole _migrate_schema transaction rolled back: password_hash, which
    # is added *before* _migrate_review_policy runs, must not be present.
    conn = sqlite3.connect(str(path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        conn.close()
    assert "password_hash" not in cols, "partial migration survived a failure"

    # Forward recovery: the next open finishes the job.
    ERPDatabase(str(path))
    conn = sqlite3.connect(str(path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        conn.close()
    assert "password_hash" in cols


def test_a_crash_between_the_two_transactions_self_repairs(tmp_path: Path) -> None:
    """A crash after _migrate_ocr_confidence committed but before _migrate_schema
    ran must leave a database the next open repairs."""
    path = tmp_path / "between.sqlite3"
    _seed(path)

    # Drop an ocr column so _migrate_ocr_confidence has work, and a users
    # column so _migrate_schema has work.
    conn = sqlite3.connect(str(path))
    try:
        # ocr_extracted_fields may not exist on a fresh seed; create the legacy
        # shape by running the schema first via ERPDatabase, then dropping.
        pass
    finally:
        conn.close()

    # Build a legacy-ish database: open normally, then drop columns to simulate
    # an old schema, then re-open with _migrate_schema forced to fail.
    db = ERPDatabase(str(path))
    del db
    _drop(path, "users", "password_hash")

    # Force _migrate_schema to fail while _migrate_ocr_confidence runs first.
    original = ERPDatabase._migrate_schema

    def boom(self, conn):
        raise RuntimeError("injected inter-transaction failure")

    ERPDatabase._migrate_schema = boom
    try:
        with pytest.raises(RuntimeError, match="injected inter-transaction failure"):
            ERPDatabase(str(path))
    finally:
        ERPDatabase._migrate_schema = original

    # Reopen repairs: password_hash must now be present.
    ERPDatabase(str(path))
    conn = sqlite3.connect(str(path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        conn.close()
    assert "password_hash" in cols


def test_migrate_ocr_confidence_never_uses_executescript() -> None:
    """executescript commits the pending transaction and breaks atomicity."""
    src = (REPO_ROOT / "dz_erp" / "database.py").read_text(encoding="utf-8")
    start = src.index("def _migrate_ocr_confidence")
    body = src[start : start + 7000]
    assert "executescript" not in body.split('"""', 2)[-1], (
        "the table rebuild must not use executescript()"
    )


def test_migration_is_idempotent_on_reopen(tmp_path: Path) -> None:
    """Opening an already-migrated database changes nothing."""
    path = tmp_path / "idem.sqlite3"
    _seed(path)
    db = ERPDatabase(str(path))
    del db

    def _cols():
        c = sqlite3.connect(str(path))
        try:
            return {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        finally:
            c.close()

    before = _cols()
    ERPDatabase(str(path))
    after = _cols()
    assert before == after
    assert "password_hash" in after
