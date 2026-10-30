# Migration and data safety

The application upgrades its own SQLite database in place, on every start-up,
inside the deployment that holds the only copy of the data. This document states
the rules that code follows and what proves them.

**Evidence:** `tests/test_phase38_migration_matrix.py` — 24 tests, passing on this
host. The simulation method is explicit: a *current* database has modern columns
removed (`ALTER TABLE … DROP COLUMN`, with the table's triggers dropped first,
exactly as an older schema would lack them), then reopened. That reproduces what
the migration code actually sees, without hand-written historical DDL that could
drift from reality.

## 1. The rules

| Rule | Why | Test |
|---|---|---|
| A migration only **adds** | It may never destroy a stored fact; the database is the record | `test_no_column_is_dropped_by_a_migration` |
| Migrations are **idempotent** | They run on every start-up, including after a crash mid-way | `test_running_the_migration_twice_changes_nothing`, `test_a_half_applied_upgrade_is_repaired_on_the_next_open` |
| Each migration is **independently tested** | One migration must not be able to hide behind another | `test_any_single_missing_column_is_restored[…, 14 columns]` |
| Old rows get **`NULL`, not a default** | `NULL` means "not recorded". A default would assert a decision nobody made — e.g. claiming a document was routed normally when the concept did not exist | `test_legacy_rows_get_null_not_a_fabricated_value` |
| Data and the **audit chain survive** | The hash chain must verify before and after | `test_data_and_the_audit_chain_survive_the_upgrade` |
| A table rebuild is **one transaction** | `sqlite3.executescript()` commits the pending transaction, which would leave a half-rebuilt table | `test_the_rebuild_migration_is_atomic` |
| A migration **never opens its own connection** | It must work inside the caller's transaction, or a partial upgrade could be committed | `test_migrations_never_touch_production_data_paths` |
| A fresh database needs **no migration** | New files must already be complete | `test_a_fresh_database_needs_no_migration_and_is_complete` |

## 2. The migrations

| # | Column(s) | Phase | Legacy meaning of `NULL` |
|---|---|---|---|
| 1 | `users.password_hash` | auth | user predates authentication; cannot log in until a hash is set |
| 2 | `users.failed_login_count`, `users.locked_until`, `users.password_updated_at` | auth hardening | no failures recorded / not locked / age unknown |
| 3 | `users.api_token_hash` | auth | retained legacy column; superseded by `auth_tokens` |
| 4 | `audit_events.hmac_key_version` | key rotation | every pre-existing row was signed with the only key there was, so `DEFAULT 1` is factual, not a guess |
| 5 | `ocr_extracted_fields.conflict_values_json` | 29 | the field was extracted before conflicts were recorded — never "there were no conflicts" |
| 6 | `ocr_extracted_fields` rebuild (nullable, provenance-bearing confidence) | 29 | legacy scores marked `not_measured`; fabricated confidence cleared; raw text, bbox, model version and human overrides copied |
| 7 | `approval_requests.source_review_fingerprint` | 33 | the request predates binding; the approval gate reads `NULL` as "not bound" and skips the re-check, so a legacy pending request keeps working |
| 8 | `ocr_field_reviews.evidence_json` | 34 | the decision covered "the single row that existed then"; if several rows exist now, a `NULL` binding authorises **none** of them |
| 9 | `source_documents.extraction_route`, `route_reason`, `declared_language`, `script_arabic_letters`, `script_latin_letters` | 29 | unknown route — not "standard_review" |

## 3. Running a migration on a real deployment

1. **Back up first, and verify the backup** — not just that a file appeared
   (`docs/BACKUP_AND_RECOVERY_RUNBOOK.md`).
2. Stop the service (one process owns the SQLite writer).
3. Start it once with the new version. Migration runs on open; if it raises, the
   service does not start and nothing is served.
4. Confirm: `verify_audit_chain`, row counts for the tables above, and a read of
   one legacy document.
5. Keep the pre-migration backup until the new version has run a full business
   day.

**Rollback is restore.** There is no down-migration, by design: a down-migration
would have to delete columns, which rule 1 forbids. Rolling back means restoring
the verified pre-migration backup.

## 4. What is not covered here

* No migration has been run against a **production-sized** dataset; all evidence
  here is synthetic data in the hundreds of rows. Timing and lock behaviour at
  scale are `BLOCKED_EXTERNAL` (needs a copy of real state).
* SQLite's `ALTER TABLE … DROP COLUMN` (SQLite ≥ 3.35) is used **only by the
  tests**, never by the application.
* No schema-version table exists: migrations are structural probes
  (`PRAGMA table_info`). This is safe precisely *because* they are idempotent and
  additive; introducing a version now would add a failure mode without adding a
  guarantee.
