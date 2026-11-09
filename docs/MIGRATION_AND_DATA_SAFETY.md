# Migration and data safety

How this application upgrades a database in place, what is proved about that
upgrade, what is not, and what an operator must do before letting it run.

Evidence: `tests/test_phase38_migration_matrix.py` (24 tests, per-column
matrix) and `tests/test_phase41_migration_evidence.py` (17 tests, per-generation
matrix with checksums, foreign keys, interruption, large tables and a
backup/restore round trip). Nothing here claims production-scale safety: see §8.

---

## 1. The rules the migrations follow

1. **Additive only.** No migration drops a column, deletes a row, renames data or
   rewrites a stored value. `grep` over `dz_erp/database.py` shows no
   `DROP TABLE`, `DELETE FROM` or `UPDATE … SET` against a business table inside
   the migration path.
2. **Idempotent.** Every step is guarded by a `PRAGMA table_info` / `sqlite_master`
   check, so a second start-up changes nothing. Verified by comparing the whole
   `sqlite_master` dump before and after a second open (phase38 and phase41).
3. **`NULL` means "not recorded".** A legacy row gets `NULL`, never a fabricated
   default that would assert a decision nobody made — the clearest case being
   Phase 40's rule provenance, where backfilling today's rule onto an old invoice
   would be a false statement about the past.
4. **No down-migration.** There is no `downgrade` path, by design: reversing an
   upgrade would have to delete or reinterpret data that has since been written.
   Recovery is by restoring the pre-upgrade backup (§7), not by rewinding the
   schema.
5. **One connection, one transaction boundary.** Migrations run on the connection
   the caller opened. They never open their own connection or touch a second
   file (asserted structurally in phase38).

## 2. What a "schema generation" means here

The repository has no version table; the migration is driven by what the schema
*is*. Phase 38 tested one column at a time; Phase 41 tests whole generations:

| Generation | What it is missing |
|---|---|
| pre-auth | `users.password_hash`, `failed_login_count`, `locked_until`, `password_updated_at`, `api_token_hash`, `audit_events.hmac_key_version` |
| pre-Phase-29 (review policy) | `ocr_extracted_fields.conflict_values_json`, `ocr_field_reviews.evidence_json`, `approval_requests.source_review_fingerprint` |
| pre-Phase-34 (routing) | `source_documents.extraction_route`, `route_reason`, `declared_language`, `script_arabic_letters`, `script_latin_letters` |
| pre-Phase-40 (provenance) | the six `supplier_invoices.timbre_*` columns and the `timbre_rule_versions` table |
| oldest reconstructed | all of the above at once |

Each generation is built by starting from the current schema and removing that
generation's additions (simulating an older database with `ALTER TABLE … DROP
COLUMN`, dropping first the triggers whose bodies mention the column — which is
what an older trigger set looked like). The upgrade must then:

* restore every column of every generation;
* keep every business value identical (SHA-256 over a fixed column set per table,
  before and after);
* keep the audit chain valid;
* leave `PRAGMA foreign_key_check` empty;
* create the objects a migration owns — the audit, journal, OCR-preservation and
  Phase 40 triggers, and the provenance index;
* be a no-op on the second open.

All of that is asserted. The checksums cover money and workflow state
(`supplier_invoices.amount_*`, `workflow_state`, `payload_sha256`), ledger totals
and the evidence rows, so a migration that silently rewrote a value would fail
the suite.

## 3. Interruption: what actually happens (finding F-06)

**The migration sequence is not one atomic unit.** Connections run with
`isolation_level=None` and explicit `BEGIN IMMEDIATE` around *data* changes, so
each `ALTER TABLE` is atomic on its own but the set of them is not. A crash
between two steps leaves a partially upgraded file.

That is measured, not assumed. `test_an_interrupted_migration_leaves_a_recoverable_database`
injects a failure after a partial `ALTER` and asserts the three properties an
operator depends on:

1. no business value changed (checksums identical);
2. the file is still a valid database with no foreign-key violations;
3. the next start completes the upgrade (forward recovery), with the guards and
   indexes present afterwards.

`test_the_migration_steps_are_individually_atomic_not_globally_atomic` pins the
assumption: if the connection mode ever changes, the test fails and this section
must be revisited rather than quietly becoming wrong.

The exception is the one table rebuild (`_migrate_ocr_confidence`), which wraps
itself in an explicit transaction precisely because `executescript()` would
commit a pending transaction and leave a half-rebuilt table (asserted in
phase38).

**Consequence for operators:** a verified backup before an upgrade is not a
formality, it is the recovery plan. §7.

## 4. Foreign keys, indexes and triggers

* Foreign keys are enabled on every non-read-only connection
  (`PRAGMA foreign_keys = ON`), so a migration runs with the constraints live.
  A constraint violation therefore aborts the upgrade instead of writing an
  orphan.
* `PRAGMA foreign_key_check` must return nothing after a migration; asserted per
  generation, and asserted to still reject an orphan insert afterwards (the
  constraint is real, not merely declared).
* No migration creates a duplicate object: index and trigger names are unique
  after the upgrade (asserted).

## 5. Large tables

`test_a_large_table_migrates_in_bounded_time_and_loses_nothing` seeds 1 500
invoices with lines, upgrades, and asserts the row count, the checksums and a
wall-clock bound (30 s, deliberately generous). It is a *shape* guard — it would
catch an accidental O(n²) step — not a benchmark. Real timing on the operator's
storage belongs to the DR drill (`R-DR`).

## 6. Read-only inspection never migrates

An `ERPDatabase` opened for inspection refuses to create schema objects, switch
the journal mode, or generate replacement key material. Restoring a backup and
inspecting it therefore cannot "fix" the very difference you are investigating.
(Implemented and tested before this document; re-verified in phase12's suites.)

## 7. The operator procedure

1. Stop the service (do not upgrade under a live writer).
2. Take a verified backup: `create_encrypted_backup`, then `restore_and_verify_backup`
   into an isolated directory and confirm `db_sha256_verified` and
   `audit_chains_valid` are true.
3. Start the service once. The migration runs. If it fails, do **not** retry
   blindly: read the error, keep the file, and see §3 — a partial upgrade is
   repairable, but the failure may indicate corruption that the backup also has.
4. Verify: the service starts, `verify_audit_chain` is valid, `PRAGMA
   foreign_key_check` is empty, and the trial balance still balances.
5. Only then re-enable writes.

`test_a_backup_taken_before_the_upgrade_restores_after_it` exercises steps 2–4
end-to-end in the supported interpreter (it skips on CPython < 3.11.17, where the
safe-extraction gate refuses by design; CI runs it on 3.11.17 and 3.12).

## 8. What is *not* claimed

* **Production-scale data.** 1 500 invoices is a shape test, not an operator's
  ledger. Timing, WAL growth and disk headroom on real data are unmeasured.
* **A real historical dump.** The generations are reconstructed by removing
  columns from the current schema. That is faithful for structural properties and
  cannot drift from hand-written historical DDL, but it is not the operator's
  actual old file. Replaying one in staging is `R-DR`, still
  `BLOCKED_EXTERNAL`.
* **Migration under concurrent writers.** The procedure requires a stopped
  service; multi-writer upgrades are not supported and not tested.
* **Atomicity of the whole sequence.** See §3 — forward recovery only.

Owners and pass criteria for the external rows are in
`docs/RELEASE_READINESS_MATRIX.md` (`R-DR`, `R-CONC`).
