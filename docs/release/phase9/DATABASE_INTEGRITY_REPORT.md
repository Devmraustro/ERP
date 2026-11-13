# Phase 9E — Database Integrity, Migrations, Concurrency

**Baseline:** PR #7 head `f603e38` · executed 2026-11-09
**Evidence:** `docs/release/phase9/evidence/logs/db_concurrency.log`,
`…/business_suites.log`, migration suites in the full run

## 1. Measured state of a freshly installed database

| Property | Value |
| --- | --- |
| `PRAGMA integrity_check` | **ok** |
| `PRAGMA foreign_key_check` violations | **0** |
| `PRAGMA journal_mode` | **wal** |
| Tables | **27** |
| Foreign keys declared | `ON DELETE RESTRICT` on ledger and voucher children |

## 2. Constraints that make corruption visible instead of silent

- `journal_entries`: `CHECK (total_debit_dzd = total_credit_dzd)`;
  `CHECK` requiring `posted_by_id` + `posted_at` for `state='POSTED'`;
  `UNIQUE (company_id, entry_number)`.
- `journal_entry_lines`: FKs to `companies`, `journal_entries`, `partners`, all
  `ON DELETE RESTRICT`; `reconciled` constrained to `(0,1)`.
- **Immutability triggers** on posted entries and their lines: an `UPDATE`/`DELETE`
  against a posted entry is rejected by the database, not merely by the service layer.
- `verify_audit_chain(company_id)` recomputes the hash chain and returns
  `{valid, checked_count, event_count, last_hash, key_versions}`.

## 3. Transaction behaviour (executed)

```
rollback {before: 1, after: 1, rolled_back: true}
```

A transaction that inserts a row and then raises leaves **nothing** behind. This was
verified on a real file-backed database, not a mock.

## 4. Concurrency (executed)

| Scenario | Result |
| --- | --- |
| Two threads creating the same supplier invoice | 1 `Validated`, 1 `DuplicateRejected` — no torn write, attempt audited |
| Two threads approving concurrently | exactly one decision recorded (`test_j2_concurrent_approvals_record_exactly_one_decision`) |
| Two threads reversing concurrently | exactly one credit note (`test_f5_concurrent_reversals_produce_exactly_one_credit_note`) |
| Rotation under sustained concurrent writes | converges (`test_phase16`, 9 tests) |
| WAL startup contention | `test_phase50_wal_startup_contention` (7 tests, passed) |
| Cross-company reads/writes | refused and audited (phase32/33/59) |

## 5. Migrations

| Property | Evidence |
| --- | --- |
| Fresh install creates the full schema | `test_phase27_scf_first_run`, bootstraps in the TLS and performance drills |
| Migration matrix (old → new) | `test_phase38_migration_matrix` (24 tests, passed) |
| Migration evidence / idempotency | `test_phase41_migration_evidence`; `test_phase33 … test_migration_is_idempotent_and_repeated_runs_are_safe`; `test_r_pre_phase29_database_migrates_idempotently_without_data_loss` |
| Pre-existing database upgrades without data loss | same tests, asserted on row counts and values |
| Restore of an older generation | `test_phase12_key_migration_and_backup`, `test_phase48_restore_interpreter_matrix` |
| No destructive migration on real data | migrations are additive/guarded; **not executed against any real dataset — none exists in this environment** |

## 6. Restart behaviour (executed)

After dropping and re-creating the database layer against the same file:

```
session_status_after_restart: 200  (same user_id/company/role, token_id preserved)
integrity_after_restart: ok
invoices_after_restart: 2
```

Sessions are database-backed, so a restart does not silently log users out (and does
not resurrect a revoked session either).

## 7. Tenant isolation at the data layer

Every ledger and document table carries `company_id`, and queries are company-scoped.
`test_phase32`/`test_phase33`/`test_phase59` assert cross-tenant reads and writes are
refused *and audited*, including a cross-company decide-approval call over HTTP.

## 8. Retention

**No retention or purge policy exists in the product.** The compliance checklist itself
records this (§4.7). Data grows forever; an operator must archive manually. This is an
open gap, not an oversight to be glossed.

## 9. Verdict

Schema, constraints, transactions, concurrency, restart and migration behaviour are
**verified by execution on synthetic data**. Two gaps are stated: no retention policy,
and no migration rehearsal against a real production copy (impossible here, and
destructive if attempted without authorisation).
