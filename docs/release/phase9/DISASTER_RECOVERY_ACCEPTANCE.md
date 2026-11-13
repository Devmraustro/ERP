# Phase 9F — Disaster Recovery Acceptance (measured RPO / RTO)

**Executed:** 2026-11-09 · **Script:** `docs/release/phase9/evidence/dr_drill.py`
**Log:** `docs/release/phase9/evidence/logs/dr_drill.log` · **Verdict: PASS**

Everything ran inside one `mkdtemp()` directory. No production database, key, backup
or secret was read, moved or written.

## 1. Drill design

1. Build a **source** deployment: SQLite database (company, user, supplier, one posted
   balanced journal entry of 1 234 567,00 DZD), a document in the Local File Vault, and
   three real hash-chained audit events.
2. Take an **encrypted backup** with `create_encrypted_backup` (manifest + hashes +
   generation id).
3. **Restore into a different directory** with `restore_and_verify_backup` and treat the
   restored copy as the object under test — never the source.
4. Verify: DB hash, vault hashes, `integrity_check`, `foreign_key_check`, trial
   balance equality, audit-chain equality.
5. Measure wall-clock for each phase; these measurements *are* the RTO figures.

## 2. Measured results

| Metric | Measured |
| --- | --- |
| Backup duration | **0.011 s** |
| Backup artefact size | **17 656 bytes** |
| Restore + verification duration | **0.023 s** |
| Total drill duration | **0.077 s** |
| Source last-write → backup start (RPO of the drill) | **0.011 s** |
| Generation id | `gen_1794269169055` |

## 3. Correctness checks

| Check | Result |
| --- | --- |
| Interpreter supports restore (`safe_restore_supported`) | `true` |
| Database SHA-256 matches the manifest | `true` |
| Vault files verified / present on disk | `1 / 1` |
| SQLite `PRAGMA integrity_check` on the restored DB | `ok` |
| `PRAGMA foreign_key_check` violations | `0` |
| Trial balance source | 1 234 567,00 debit / 1 234 567,00 credit — balanced |
| Trial balance restored | identical, balanced |
| Audit chain source | `valid=true`, 3 events, key version 1, last hash `b761c420…bab98` |
| Audit chain restored | **identical** hash, `valid=true` |
| Overall verdict | **PASS** |

The audit chain check is the one that matters most: an empty chain verifies trivially,
so the drill deliberately writes three events and requires a **non-empty** verified
chain whose terminal hash survives the round trip unchanged. The first version of the
drill produced an empty chain and would have "passed" on nothing; that was corrected
before this result was recorded.

## 4. What RPO and RTO actually mean here

- **RTO measured: 0.023 s** to restore a 17 KB database with one vault document. This
  is a *lower bound* on a real system: it does not include downloading the artefact
  from off-host storage, provisioning a host, or the operator's decision time. **Do not
  quote 0.023 s as a production RTO.**
- **RPO measured: 0.011 s** is the gap between the last write and the backup start in
  this drill. The real RPO is the *backup schedule interval* — if backups run hourly,
  RPO is up to one hour. The application does not ship a scheduler; the schedule is the
  operator's, and that is the honest statement.

## 5. Failure handling exercised elsewhere

| Scenario | Evidence |
| --- | --- |
| Interrupted transaction leaves no partial row | `db_concurrency_restart.py`: `rollback {before:1, after:1, rolled_back:true}` |
| Restart after restore keeps data and sessions | same script: session `200` after a database-layer restart, `integrity ok`, invoices preserved |
| Wrong interpreter for restore is refused | `test_phase48_restore_interpreter_matrix`, `safe_restore_supported` gate |
| Key-version handling in the audit chain | `test_phase11_audit_key_rotation`, `test_phase49_audit_key_bootstrap_race` |
| Backup/restore regression suite | `test_phase12_key_migration_and_backup` |

## 6. Gaps (not claimed)

- No **off-host** backup, no retention rotation, no scheduled job — operational, not
  coded.
- No restore drill from *the* production artefact (none exists in this environment).
- No measured RTO for a multi-gigabyte database; the drill's dataset is synthetic and
  small, and the numbers are labelled accordingly.

## 7. Verdict

**DR restore verified end-to-end on synthetic data, with measured and correctly
labelled timings.** The mechanism (encrypted backup → verified restore → identical
trial balance and audit chain) works. The operating discipline around it (schedule,
off-host copy, retention) remains an operational task.
