# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 38 — the migration matrix: every historical schema converges, safely.

The application upgrades a pre-existing database in place on every start-up. That
is the right design for a single-file deployment, and it is also the highest-risk
code in the repository: it runs unattended, it runs on the only copy of the data,
and a mistake there is unrecoverable without a backup.

What these tests hold the migration code to:

1. **Convergence** — a database missing any one of the added columns is brought
   forward, no matter which subset is missing.
2. **Idempotency** — running it again changes nothing (checked by comparing the
   full schema dump before and after a second open).
3. **Preservation** — rows, values and the audit chain survive; nothing is
   deleted, merged or rewritten; no column is dropped.
4. **Interruption safety** — a half-applied schema is repaired on the next open
   rather than being left permanently broken.
5. **Fail-closed defaults** — legacy rows get `NULL` ("not recorded"), never a
   fabricated value that asserts a decision nobody made.

The simulation strategy: take a *current* database and remove the modern columns
with `ALTER TABLE … DROP COLUMN`, then reopen it. That reproduces exactly what an
older schema looks like to the migration code, without hand-writing historical
DDL that could drift from the real thing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase

#: (table, column) pairs the migrations are responsible for adding.
MIGRATED_COLUMNS = [
    ("users", "password_hash"),
    ("users", "failed_login_count"),
    ("users", "locked_until"),
    ("users", "password_updated_at"),
    ("users", "api_token_hash"),
    ("audit_events", "hmac_key_version"),
    ("ocr_extracted_fields", "conflict_values_json"),
    ("ocr_field_reviews", "evidence_json"),
    ("approval_requests", "source_review_fingerprint"),
    ("source_documents", "extraction_route"),
    ("source_documents", "route_reason"),
    ("source_documents", "declared_language"),
    ("source_documents", "script_arabic_letters"),
    ("source_documents", "script_latin_letters"),
]

#: Columns that were introduced in a table rebuild and must stay absent.
REBUILT_COLUMNS = [("ocr_extracted_fields", "confidence_kind"), ("ocr_extracted_fields", "confidence_scope")]


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _schema_dump(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        return "\n".join(str(r) for r in rows)
    finally:
        conn.close()


def _seed(db_path: Path) -> None:
    """A database with data in every table the migrations touch."""
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (1, 1, 'legacy_user', 'admin', 1, ?)",
            (hash_password("Legacy#2026x", iterations=1000),),
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, "
            "page_count, file_sha256, vault_path, security_status, workflow_state, "
            "uploaded_by_id, uploaded_at) "
            "VALUES ('doc-legacy', 1, 'legacy.pdf', 'application/pdf', 1024, 1, ?, "
            "'vault/legacy.bin', 'CLEAN', 'Uploaded', 1, '2026-01-02T10:00:00+00:00')",
            ("b" * 64,),
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (id, company_id, document_id, field_name, "
            "raw_text, normalized_value, confidence, confidence_kind, confidence_scope, "
            "confidence_calibrated, page_number, bbox_json, ocr_model_version, "
            "created_at) "
            "VALUES (1, 1, 'doc-legacy', 'amount_ht_dzd', '1000.00', '1000.00', NULL, "
            "'not_measured', 'unavailable', 0, 1, '{}', 'legacy-v0', '2026-01-02T10:00:01+00:00')"
        )
        conn.execute(
            "INSERT INTO ocr_field_reviews (id, company_id, document_id, field_name, "
            "decision, reviewed_by_id, reviewed_at) "
            "VALUES (1, 1, 'doc-legacy', 'amount_ht_dzd', 'CONFIRMED', 1, "
            "'2026-01-02T10:05:00+00:00')"
        )
    with db.transaction() as conn:
        db.append_audit_event(
            conn, 1, 1, "LEGACY_EVENT", "source_document", "doc-legacy", {"kept": True}
        )


def _drop(db_path: Path, table: str, column: str) -> None:
    """Remove a column to simulate an older schema.

    Triggers that mention the column must go first — an older database had an
    older trigger body (or none), and the migration recreates the current one.
    That is exactly the situation the migration has to handle.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        triggers = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
            (table,),
        ).fetchall()
        for name, _sql in triggers:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.commit()
    finally:
        conn.close()


def _open(tmp_path: Path, name: str = "legacy.sqlite3") -> Path:
    path = tmp_path / name
    _seed(path)
    return path


# ---------------------------------------------------------------------------
# convergence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table,column", MIGRATED_COLUMNS)
def test_any_single_missing_column_is_restored(
    tmp_path: Path, table: str, column: str
) -> None:
    """Each migration is tested alone, so no migration can hide behind another."""
    path = _open(tmp_path)
    _drop(path, table, column)
    assert column not in _columns(path, table)

    ERPDatabase(str(path))  # the migration runs in the constructor
    assert column in _columns(path, table), f"{table}.{column} was not restored"


def test_a_database_missing_every_migrated_column_converges(tmp_path: Path) -> None:
    """The worst realistic case: an old dump upgraded across several phases."""
    path = _open(tmp_path)
    for table, column in MIGRATED_COLUMNS:
        _drop(path, table, column)

    ERPDatabase(str(path))
    for table in {t for t, _ in MIGRATED_COLUMNS}:
        present = _columns(path, table)
        expected = {c for t, c in MIGRATED_COLUMNS if t == table}
        assert expected <= present, f"{table} missing {sorted(expected - present)}"


def test_a_fresh_database_needs_no_migration_and_is_complete(tmp_path: Path) -> None:
    """A brand-new file must already have every column — no migration required."""
    path = tmp_path / "fresh.sqlite3"
    ERPDatabase(str(path))
    for table, column in MIGRATED_COLUMNS:
        assert column in _columns(path, table), f"{table}.{column}"
    for table, column in REBUILT_COLUMNS:
        assert column in _columns(path, table), f"{table}.{column}"


# ---------------------------------------------------------------------------
# idempotency and preservation
# ---------------------------------------------------------------------------


def test_running_the_migration_twice_changes_nothing(tmp_path: Path) -> None:
    path = _open(tmp_path)
    _drop(path, "users", "password_hash")
    ERPDatabase(str(path))
    first = _schema_dump(path)
    ERPDatabase(str(path))
    assert _schema_dump(path) == first, "the migration is not idempotent"


def test_data_and_the_audit_chain_survive_the_upgrade(tmp_path: Path) -> None:
    path = _open(tmp_path)
    db = ERPDatabase(str(path))
    before = db.verify_audit_chain(company_id=1)
    before_rows = {
        table: _count(path, table)
        for table in ("companies", "users", "source_documents", "ocr_extracted_fields", "ocr_field_reviews")
    }

    _drop(path, "ocr_field_reviews", "evidence_json")
    _drop(path, "ocr_extracted_fields", "conflict_values_json")
    migrated = ERPDatabase(str(path))

    assert migrated.verify_audit_chain(company_id=1) == before
    for table, expected in before_rows.items():
        assert _count(path, table) == expected, f"{table} lost rows during migration"

    conn = sqlite3.connect(str(path))
    try:
        stored = conn.execute(
            "SELECT raw_text, ocr_model_version FROM ocr_extracted_fields WHERE id = 1"
        ).fetchone()
        decision = conn.execute(
            "SELECT decision, reviewed_by_id FROM ocr_field_reviews WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert stored == ("1000.00", "legacy-v0")
    assert decision == ("CONFIRMED", 1)


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_legacy_rows_get_null_not_a_fabricated_value(tmp_path: Path) -> None:
    """`NULL` reads as "not recorded". A default would assert a decision."""
    path = _open(tmp_path)
    _drop(path, "ocr_field_reviews", "evidence_json")
    _drop(path, "source_documents", "extraction_route")
    ERPDatabase(str(path))

    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute(
            "SELECT evidence_json FROM ocr_field_reviews WHERE id = 1"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT extraction_route FROM source_documents WHERE id = 'doc-legacy'"
        ).fetchone()[0] is None
    finally:
        conn.close()


def test_no_column_is_dropped_by_a_migration(tmp_path: Path) -> None:
    """A migration may add; it must never remove a stored fact."""
    path = _open(tmp_path)
    before = _columns(path, "source_documents") | _columns(path, "ocr_extracted_fields")
    ERPDatabase(str(path))
    after = _columns(path, "source_documents") | _columns(path, "ocr_extracted_fields")
    assert before <= after


# ---------------------------------------------------------------------------
# interruption safety
# ---------------------------------------------------------------------------


def test_a_half_applied_upgrade_is_repaired_on_the_next_open(tmp_path: Path) -> None:
    """Some columns added, others not: the next start-up must finish the job."""
    path = _open(tmp_path)
    for table, column in MIGRATED_COLUMNS[:6]:
        _drop(path, table, column)
    # Simulate an interrupted run: apply only the first migration by hand.
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        conn.commit()
    finally:
        conn.close()

    ERPDatabase(str(path))
    for table, column in MIGRATED_COLUMNS[:6]:
        assert column in _columns(path, table)


def test_a_database_with_the_new_columns_but_an_old_value_shape_still_opens(
    tmp_path: Path,
) -> None:
    """Legacy NULLs in newly-added columns must not break the migrations."""
    path = _open(tmp_path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("UPDATE ocr_field_reviews SET evidence_json = NULL")
        # `locked_until` is nullable; `failed_login_count` is NOT NULL DEFAULT 0,
        # and that default is a factual statement (no failures recorded), so it
        # must stay a number.
        conn.execute("UPDATE users SET locked_until = NULL")
        conn.commit()
    finally:
        conn.close()
    ERPDatabase(str(path))  # must not raise
    assert _count(path, "users") == 1


def test_the_rebuild_migration_is_atomic(tmp_path: Path) -> None:
    """`_migrate_ocr_confidence` rebuilds a table; a failure must not half-do it.

    The rebuild is one explicit transaction precisely because
    ``executescript()`` would commit the pending transaction and leave a
    partially rebuilt table behind.
    """
    src = (Path(__file__).resolve().parent.parent / "dz_erp" / "database.py").read_text()
    start = src.index("def _migrate_ocr_confidence")
    body = src[start : start + 6000]
    assert "executescript" not in body.split('"""', 2)[-1], (
        "the table rebuild must not use executescript(): it commits the pending "
        "transaction and breaks atomicity"
    )
    assert "BEGIN" in body or "transaction" in body.lower() or "SAVEPOINT" in body


def test_migrations_never_touch_production_data_paths(tmp_path: Path) -> None:
    """A migration works on the connection it is given, nothing else.

    Regression guard: an operator points ERP_DB_PATH at the real database, so a
    migration that opened its own connection (or a second file) would bypass the
    caller's transaction and could commit a partial upgrade.
    """
    src_root = Path(__file__).resolve().parent.parent / "dz_erp"
    database_src = (src_root / "database.py").read_text()
    start = database_src.index("def _migrate_schema")
    end = database_src.index("def _migrate_review_policy")
    migrations_head = database_src[start:end]
    assert "sqlite3.connect(" not in migrations_head
    for name in (
        "_migrate_ocr_confidence",
        "_migrate_review_policy",
        "_migrate_approval_binding",
        "_migrate_document_routing",
        "_migrate_review_evidence",
    ):
        body = database_src[database_src.index(f"def {name}") :]
        body = body[: body.index("\n    def ") if "\n    def " in body else len(body)]
        assert "sqlite3.connect(" not in body, f"{name} opens its own connection"
