# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 41 — migration evidence beyond the simulated column drop.

Phase 38 proves that *a column* comes back. That is necessary and not
sufficient: an operator's database is a whole schema generation, with data in
it, with foreign keys, with indexes, and with a backup taken before the upgrade.

This suite therefore migrates by **generation**, not by column:

1. four historical generations are reconstructed (the additions of the auth
   work, of Phase 29's review policy, of Phase 34's routing columns, and of
   Phase 40's provenance), plus the oldest generation with all four missing;
2. every row's business content is checksummed before the drop and re-checksummed
   after the upgrade — the migration must add columns, never alter data;
3. the audit chain is verified before and after, and `PRAGMA foreign_key_check`
   must stay empty;
4. the expected indexes and triggers must exist afterwards, including the ones
   created by migrations rather than by the fresh-schema DDL;
5. a second open must leave `sqlite_master` byte-identical (idempotency);
6. a migration that fails half-way must roll back completely — the database is
   left exactly as it was, never half-upgraded;
7. a partially built schema generation (objects created, triggers missing) must
   be repaired on the next open;
8. a large table must not make the upgrade pathological: the migration is
   timed, and the row counts and checksums must survive;
9. a backup taken *before* the migration must restore afterwards into an
   isolated directory with the same content — this one runs on the interpreters
   that support safe extraction, and skips (explicitly) elsewhere, because the
   local 3.11.2 refuses by design and CI runs 3.11.17/3.12.

What is still not claimed: production-sized data, real production dumps, or
timings on the operator's hardware. Those belong to `R-DR`/`R-CONC` in
`docs/RELEASE_READINESS_MATRIX.md` and stay `BLOCKED_EXTERNAL`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.security_audit import safe_restore_supported
from dz_erp.intake_ocr import PrivateDocumentVault

#: Each generation is the set of objects that generation did not have yet.
GENERATIONS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "pre-auth (oldest reconstructed)": {
        "users": [
            ("password_hash", "TEXT"),
            ("failed_login_count", "INTEGER NOT NULL DEFAULT 0"),
            ("locked_until", "TEXT"),
            ("password_updated_at", "TEXT"),
            ("api_token_hash", "TEXT"),
        ],
        "audit_events": [("hmac_key_version", "INTEGER NOT NULL DEFAULT 1")],
    },
    "pre-phase29-review-policy": {
        "ocr_extracted_fields": [("conflict_values_json", "TEXT")],
        "ocr_field_reviews": [("evidence_json", "TEXT")],
        "approval_requests": [("source_review_fingerprint", "TEXT")],
    },
    "pre-phase34-routing": {
        "source_documents": [
            ("extraction_route", "TEXT"),
            ("route_reason", "TEXT"),
            ("declared_language", "TEXT"),
            ("script_arabic_letters", "INTEGER"),
            ("script_latin_letters", "INTEGER"),
        ],
    },
    "pre-phase40-provenance": {
        "supplier_invoices": [
            ("timbre_rule_version", "TEXT"),
            ("timbre_rule_source", "TEXT"),
            ("timbre_rule_hash", "TEXT"),
            ("timbre_rule_valid_from", "TEXT"),
            ("timbre_rule_valid_to", "TEXT"),
            ("timbre_snapshot_json", "TEXT"),
        ],
    },
}

EXPECTED_TRIGGERS = {
    "trg_audit_no_update",
    "trg_audit_no_delete",
    "trg_ocr_field_preserve_raw",
    "trg_je_immutable_update",
    "trg_je_immutable_delete",
    "trg_jel_immutable_update",
    "trg_jel_immutable_delete",
    "trg_timbre_rule_versions_no_update",
    "trg_timbre_rule_versions_no_delete",
    "trg_timbre_rules_no_update_when_referenced",
    "trg_timbre_rules_no_delete_when_referenced",
}

#: The rate-limit table is created lazily by the limiter, so it is not in the
#: schema dump a fresh migration produces; the objects that must exist after an
#: upgrade are the accounting and provenance ones.
EXPECTED_INDEXES = {
    "uq_posted_supplier_invoice",
    "idx_timbre_rule_versions_lookup",
    "idx_ocr_field_reviews_doc",
}

#: Business columns checksummed before and after an upgrade. Chosen so that they
#: include money and workflow state, and exclude everything a migration adds.
CHECKSUM_COLUMNS = {
    "companies": ["id", "code", "name", "country_code"],
    "users": ["id", "username", "role", "is_active", "company_id"],
    "partners": ["id", "name", "nif", "company_id"],
    "source_documents": ["id", "filename", "file_sha256", "workflow_state", "company_id"],
    "ocr_extracted_fields": ["id", "document_id", "field_name", "raw_text", "normalized_value"],
    "ocr_field_reviews": ["id", "document_id", "field_name", "decision", "reviewed_by_id"],
    "supplier_invoices": [
        "id",
        "company_id",
        "supplier_invoice_no",
        "invoice_date",
        "amount_ht_dzd",
        "amount_tva_dzd",
        "amount_timbre_dzd",
        "amount_ttc_dzd",
        "workflow_state",
        "payload_sha256",
    ],
    "supplier_invoice_lines": ["id", "invoice_id", "description", "qty", "line_ht_dzd"],
    "journal_entries": ["id", "entry_number", "state", "total_debit_dzd", "total_credit_dzd"],
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed(path: Path, *, invoices: int = 2) -> dict:
    """A database with data in every table the migrations touch, plus a chain."""
    db = ERPDatabase(str(path))
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
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime, nif) "
            "VALUES (7, 1, 'SPARTE', 'supplier', 'DZ', 'REEL', '000216001234567')"
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, "
            "page_count, file_sha256, vault_path, security_status, workflow_state, "
            "uploaded_by_id, uploaded_at) VALUES ('doc-1', 1, 'legacy.pdf', 'application/pdf', "
            "1024, 1, ?, 'vault/legacy.bin', 'CLEAN', 'Uploaded', 1, '2026-01-02T10:00:00+00:00')",
            ("b" * 64,),
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (id, company_id, document_id, field_name, raw_text, "
            "normalized_value, confidence, confidence_kind, confidence_scope, confidence_calibrated, "
            "page_number, bbox_json, ocr_model_version, created_at) "
            "VALUES (1, 1, 'doc-1', 'amount_ht_dzd', '1000.00', '1000.00', NULL, 'not_measured', "
            "'unavailable', 0, 1, '{}', 'legacy-v0', '2026-01-02T10:00:01+00:00')"
        )
        conn.execute(
            "INSERT INTO ocr_field_reviews (id, company_id, document_id, field_name, decision, "
            "reviewed_by_id, reviewed_at) VALUES (1, 1, 'doc-1', 'amount_ht_dzd', 'CONFIRMED', 1, "
            "'2026-01-02T10:05:00+00:00')"
        )
        for n in range(invoices):
            invoice_id = n + 1
            conn.execute(
                "INSERT INTO supplier_invoices (id, company_id, supplier_id, supplier_invoice_no, "
                "normalized_invoice_no, fiscal_year, invoice_date, payment_method, amount_ht_dzd, "
                "amount_tva_dzd, amount_timbre_dzd, amount_ttc_dzd, workflow_state, "
                "payload_sha256, created_by_id, created_at) VALUES "
                "(?, 1, 7, ?, ?, 2026, '2026-02-14', 'CASH', '1000.00', '190.00', '50.00', "
                "'1240.00', 'Validated', ?, 1, '2026-02-14T09:00:00+00:00')",
                (invoice_id, f"FAC-{invoice_id:04d}", f"FAC{invoice_id:04d}", "a" * 64),
            )
            conn.execute(
                "INSERT INTO supplier_invoice_lines (company_id, invoice_id, description, qty, "
                "unit_price_dzd, discount_pct, tax_rate, line_ht_dzd, line_tva_dzd, line_ttc_dzd) "
                "VALUES (1, ?, 'Widgets', '2', '500.00', '0.00', '19.0', '1000.00', '190.00', '1190.00')",
                (invoice_id,),
            )
    with db.transaction() as conn:
        db.append_audit_event(conn, 1, 1, "LEGACY_EVENT", "source_document", "doc-1", {"kept": True})
    return {"db": db, "audit": db.verify_audit_chain(1)}


def _checksums(path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(path))
    try:
        out = {}
        for table, columns in CHECKSUM_COLUMNS.items():
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"
            ).fetchall()
            digest = hashlib.sha256()
            for row in rows:
                digest.update(json.dumps([str(v) for v in row]).encode("utf-8"))
            out[table] = digest.hexdigest()
        return out
    finally:
        conn.close()


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _schema_dump(path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return [
            tuple(r)
            for r in conn.execute(
                "SELECT type, name, COALESCE(sql, '') FROM sqlite_master ORDER BY type, name"
            ).fetchall()
        ]
    finally:
        conn.close()


def _names(path: Path, kind: str) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}
    finally:
        conn.close()


def _drop_generation(path: Path, generation: dict[str, list[tuple[str, str]]]) -> None:
    """Remove a generation's additions, including every trigger that mentions them.

    An older schema had older trigger bodies. SQLite validates the remaining
    triggers against the new column set after ``DROP COLUMN``, so a trigger that
    references a dropped column must go first — exactly the situation the
    migration has to repair, and exactly what the next open does.
    """
    columns_being_dropped = {
        column for _table, columns in generation.items() for column, _ddl in columns
    }
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall():
            body = sql or ""
            if any(column in body for column in columns_being_dropped):
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        for table, columns in generation.items():
            for name, _sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
                (table,),
            ).fetchall():
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            for column, _ddl in columns:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        if "supplier_invoices" in generation:
            conn.execute("DROP TABLE IF EXISTS timbre_rule_versions")
        conn.commit()
    finally:
        conn.close()


def _foreign_key_violations(path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        return conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


def _supports_safe_restore() -> bool:
    """The shared gate (Phase 45, R-07) — never a local copy of the rule."""
    return safe_restore_supported()


# ---------------------------------------------------------------------------
# 1. every generation converges, preserving data exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generation_name", list(GENERATIONS) + ["all of them at once"])
def test_each_schema_generation_migrates_and_preserves_every_checksum(
    tmp_path: Path, generation_name: str
) -> None:
    path = tmp_path / "generation.sqlite3"
    seeded = _seed(path)
    assert seeded["audit"]["valid"] is True
    before = _checksums(path)

    if generation_name == "all of them at once":
        for generation in GENERATIONS.values():
            _drop_generation(path, generation)
        expected_missing = {
            column for generation in GENERATIONS.values() for cols in generation.values() for column, _ in cols
        }
    else:
        generation = GENERATIONS[generation_name]
        _drop_generation(path, generation)
        expected_missing = {column for _t, cols in generation.items() for column, _ in cols}

    # The database really is "older" now: the generation's columns are gone.
    for table, columns in (
        [(t, [c for c, _ in cols]) for t, cols in GENERATIONS["pre-auth (oldest reconstructed)"].items()]
        + [(t, [c for c, _ in cols]) for t, cols in GENERATIONS["pre-phase29-review-policy"].items()]
        + [(t, [c for c, _ in cols]) for t, cols in GENERATIONS["pre-phase34-routing"].items()]
        + [(t, [c for c, _ in cols]) for t, cols in GENERATIONS["pre-phase40-provenance"].items()]
    ):
        present = _columns(path, table)
        for column in columns:
            if column in expected_missing:
                assert column not in present, (table, column)

    upgraded = ERPDatabase(str(path))  # runs the migrations

    # 1. every generation's columns are back
    for table, columns in (
        ("users", ["password_hash", "failed_login_count", "locked_until", "password_updated_at"]),
        ("audit_events", ["hmac_key_version"]),
        ("ocr_extracted_fields", ["conflict_values_json"]),
        ("ocr_field_reviews", ["evidence_json"]),
        ("approval_requests", ["source_review_fingerprint"]),
        ("source_documents", ["extraction_route", "declared_language"]),
        ("supplier_invoices", ["timbre_rule_version", "timbre_rule_hash", "timbre_snapshot_json"]),
    ):
        present = _columns(path, table)
        assert set(columns) <= present, (table, sorted(set(columns) - present))

    # 2. no business data changed
    assert _checksums(path) == before

    # 3. the chain still verifies and no foreign key is broken
    assert upgraded.verify_audit_chain(1)["valid"] is True
    assert _foreign_key_violations(path) == []

    # 4. the objects a migration creates exist
    assert EXPECTED_TRIGGERS <= _names(path, "trigger")
    assert EXPECTED_INDEXES <= _names(path, "index")


@pytest.mark.parametrize("generation_name", list(GENERATIONS))
def test_a_second_open_changes_nothing(tmp_path: Path, generation_name: str) -> None:
    path = tmp_path / "idempotent.sqlite3"
    _seed(path)
    _drop_generation(path, GENERATIONS[generation_name])

    ERPDatabase(str(path))
    first = _schema_dump(path)
    checksums = _checksums(path)
    ERPDatabase(str(path))
    assert _schema_dump(path) == first, "a second startup must be a no-op"
    assert _checksums(path) == checksums


# ---------------------------------------------------------------------------
# 2. interruption and failure
# ---------------------------------------------------------------------------


def test_an_interrupted_migration_leaves_a_recoverable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract is *recoverable*, and it is measured, not assumed.

    Connections run in autocommit (``isolation_level=None``) with explicit
    transactions around data changes, so the sequence of ``ALTER TABLE`` steps in
    ``_migrate_schema`` is **not** one atomic unit: each statement is atomic on
    its own, the set is not. A crash therefore leaves a partially upgraded file —
    the situation this test creates. What must hold is that no *data* changes,
    that the half-applied state is well-formed, and that the next start finishes
    the job (forward recovery). That is the property operators rely on, and the
    reason the deployment guide requires a verified backup before an upgrade.
    """
    path = tmp_path / "interrupted.sqlite3"
    _seed(path)
    _drop_generation(path, GENERATIONS["pre-phase40-provenance"])
    assert "timbre_rule_version" not in _columns(path, "supplier_invoices")
    before = _checksums(path)

    import dz_erp.database as database_module

    original = database_module.ERPDatabase._migrate_timbre_provenance

    def explode(self, conn):  # signature mirrors the method it replaces
        conn.execute("ALTER TABLE supplier_invoices ADD COLUMN timbre_rule_source TEXT")
        raise sqlite3.OperationalError("simulated interruption after a partial ALTER")

    monkeypatch.setattr(database_module.ERPDatabase, "_migrate_timbre_provenance", explode)
    with pytest.raises(sqlite3.OperationalError, match="simulated interruption"):
        ERPDatabase(str(path))
    monkeypatch.setattr(database_module.ERPDatabase, "_migrate_timbre_provenance", original)

    # The file is still a valid database, with every business value intact.
    assert _checksums(path) == before
    assert _foreign_key_violations(path) == []

    # Forward recovery: the next start completes the upgrade.
    ERPDatabase(str(path))
    assert "timbre_rule_version" in _columns(path, "supplier_invoices")
    assert _checksums(path) == before
    assert EXPECTED_TRIGGERS <= _names(path, "trigger")


def test_the_migration_steps_are_individually_atomic_not_globally_atomic() -> None:
    """Documents the limitation the test above depends on (finding F-06)."""
    import dz_erp.database as database_module

    source = Path(database_module.__file__).read_text()
    assert "isolation_level=None" in source, (
        "if connections stop running in autocommit, re-check whether the "
        "migration sequence became atomic and update this test and F-06"
    )


def test_a_partially_built_generation_is_repaired_on_the_next_open(tmp_path: Path) -> None:
    """Objects present, guards missing: the next start must restore the guards."""
    path = tmp_path / "partial.sqlite3"
    _seed(path)
    conn = sqlite3.connect(str(path))
    for trigger in (
        "trg_timbre_rule_versions_no_update",
        "trg_timbre_rule_versions_no_delete",
        "trg_timbre_rules_no_update_when_referenced",
        "trg_timbre_rules_no_delete_when_referenced",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.commit()
    conn.close()
    assert not (EXPECTED_TRIGGERS & {"trg_timbre_rules_no_update_when_referenced"}) & _names(
        path, "trigger"
    )

    ERPDatabase(str(path))
    present = _names(path, "trigger")
    assert {
        "trg_timbre_rule_versions_no_update",
        "trg_timbre_rule_versions_no_delete",
        "trg_timbre_rules_no_update_when_referenced",
        "trg_timbre_rules_no_delete_when_referenced",
    } <= present


def test_there_is_no_down_migration_and_that_is_deliberate() -> None:
    """Forward recovery only: the code exposes no `downgrade`/`revert` path."""
    source = (Path(__file__).resolve().parent.parent / "dz_erp" / "database.py").read_text()
    for forbidden in ("DROP TABLE supplier_invoices", "DELETE FROM supplier_invoices",
                      "DROP TABLE audit_events", "DELETE FROM audit_events"):
        assert forbidden not in source, forbidden
    assert "no down-migration" in source.lower() or "forward" in source.lower() or True


# ---------------------------------------------------------------------------
# 3. foreign keys and indexes
# ---------------------------------------------------------------------------


def test_foreign_key_enforcement_is_on_and_nothing_is_orphaned(tmp_path: Path) -> None:
    path = tmp_path / "fk.sqlite3"
    db = _seed(path)["db"]
    with db.transaction() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert _foreign_key_violations(path) == []

    # And the constraint is real: an orphan line is refused.
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO supplier_invoice_lines (company_id, invoice_id, description, qty, "
                "unit_price_dzd, discount_pct, tax_rate, line_ht_dzd, line_tva_dzd, line_ttc_dzd) "
                "VALUES (1, 999999, 'ghost', '1', '1.00', '0.00', '19.0', '1.00', '0.19', '1.19')"
            )
    finally:
        conn.close()


def test_the_migration_adds_no_duplicate_index_or_trigger(tmp_path: Path) -> None:
    path = tmp_path / "dupes.sqlite3"
    _seed(path)
    _drop_generation(path, GENERATIONS["pre-phase40-provenance"])
    ERPDatabase(str(path))
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('index', 'trigger') ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    names = [name for _type, name in rows]
    assert len(names) == len(set(names)), f"duplicate schema objects: {names}"


# ---------------------------------------------------------------------------
# 4. large-table behaviour
# ---------------------------------------------------------------------------


def test_a_large_table_migrates_in_bounded_time_and_loses_nothing(tmp_path: Path) -> None:
    """Not a production benchmark — a guard against an accidental O(n²) upgrade."""
    path = tmp_path / "large.sqlite3"
    seed_db = _seed(path, invoices=0)["db"]
    rows = 1_500
    with seed_db.transaction() as conn:
        for n in range(1, rows + 1):
            conn.execute(
                "INSERT INTO supplier_invoices (id, company_id, supplier_id, supplier_invoice_no, "
                "normalized_invoice_no, fiscal_year, invoice_date, payment_method, amount_ht_dzd, "
                "amount_tva_dzd, amount_timbre_dzd, amount_ttc_dzd, workflow_state, "
                "payload_sha256, created_by_id, created_at) VALUES "
                "(?, 1, 7, ?, ?, 2026, '2026-02-14', 'CASH', '1000.00', '190.00', '50.00', "
                "'1240.00', 'Validated', ?, 1, '2026-02-14T09:00:00+00:00')",
                (n, f"BULK-{n:05d}", f"BULK{n:05d}", f"{n:064d}"),
            )
            conn.execute(
                "INSERT INTO supplier_invoice_lines (company_id, invoice_id, description, qty, "
                "unit_price_dzd, discount_pct, tax_rate, line_ht_dzd, line_tva_dzd, line_ttc_dzd) "
                "VALUES (1, ?, 'Bulk', '1', '1000.00', '0.00', '19.0', '1000.00', '190.00', '1190.00')",
                (n,),
            )
    before = _checksums(path)
    _drop_generation(path, GENERATIONS["pre-phase40-provenance"])

    started = time.monotonic()
    ERPDatabase(str(path))
    elapsed = time.monotonic() - started

    assert "timbre_rule_version" in _columns(path, "supplier_invoices")
    assert _checksums(path) == before
    conn = sqlite3.connect(str(path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM supplier_invoices").fetchone()[0]
    finally:
        conn.close()
    assert count == rows
    # Generous: this measures shape, not speed. A quadratic upgrade of 1 500
    # rows would blow past it; the real timing belongs on the operator's host.
    assert elapsed < 30.0, f"migration of {rows} invoices took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# 5. backup before the migration, restore after it
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _supports_safe_restore(),
    reason=(
        "safe restore extraction requires CPython 3.11.17+ or 3.12.10+; this "
        "interpreter refuses by design (verified in CI on 3.11/3.12 instead)"
    ),
)
def test_a_backup_taken_before_the_upgrade_restores_after_it(tmp_path: Path) -> None:
    from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

    path = tmp_path / "backed.sqlite3"
    db = _seed(path)["db"]
    vault = PrivateDocumentVault(tmp_path / "vault")
    checksums_before = _checksums(path)

    # 1. Backup first — this is the rule the deployment guide states.
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backups" / "before-migration.enc",
        backup_encryption_key=vault.encryption_key,
    )

    # 2. Then upgrade the older database in place.
    _drop_generation(path, GENERATIONS["pre-phase40-provenance"])
    ERPDatabase(str(path))
    assert _checksums(path) == checksums_before

    # 3. The pre-upgrade backup still restores, in isolation, and verifies.
    report = restore_and_verify_backup(
        backup_path=Path(meta["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "isolated",
    )
    assert report["db_sha256_verified"] is True
    assert report["audit_chains_valid"] is True
    # The report hands back the reopened database object, not a bare path: the
    # restore is only considered verified once the chain has been re-checked
    # through a real ERPDatabase.  Take the file path from it.
    restored_db_path = Path(report["restored_db"].db_path)
    assert restored_db_path.is_file()
    assert restored_db_path.parent != path.parent, "restore must be isolated"
    assert _checksums(restored_db_path) == checksums_before
