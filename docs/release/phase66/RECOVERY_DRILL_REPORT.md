# Phase 66 — Recovery Drill Report

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Environment | Throw-away sandbox, one Linux machine, CPython 3.12.10, local disk. **Not production, not production-like.** |
| Data | Synthetic only — the Phase 67 acceptance company: 6 users, 5 journal entries, 14 journal lines, 7 invoices, 1 vault document. Backup artefact **87 500 bytes**. |
| Outcome | **PASS for the software path. One defect found and fixed (S66-1). No RPO or RTO is claimed.** |

## 1. What this drill can and cannot support

It shows that the backup → off-machine copy → restore → restart path works end to end and loses
nothing that was in the backup, on this code, at this size.

It **cannot** support any recovery-time or recovery-point figure. The times below are for an 87 KB
artefact on a local disk; they say nothing about a real ledger, real media, a real network or a real
operator. **No RPO or RTO target has been approved by anyone, so none can be met or missed.** Register
risk D4 (no drill on production-scale data) stays Open.

## 2. Scenario

"The server is lost after business has been done, a key rotation has happened, and one more record was
entered after the last backup."

| Step | Action | Result |
|---|---|---|
| 1 | Business activity (Phase 67 walk) on the live instance | 5 posted/draft entries; posted trial balance 37 763,42 = 37 763,42 |
| 2 | `/readyz` before any backup | `degraded` — `backup: fail`; all other checks ok. Correct for a new install. |
| 3 | `python -m dz_erp.ops_backup run` | exit 0; artefact `0600`, manifest `0600`, directory `0700`; no SQLite header in the artefact |
| 4 | `ops_backup verify`, then `verify --deep` | both exit 0, `ok: true` |
| 5 | `/readyz` after backup | `ready` |
| 6 | `rotate_keys vault-rotate --vault …` | `COMPLETED_VERIFIED`; the one document re-encrypted to key version 2 |
| 7 | `rotate_keys audit-rotate --db …` | `COMPLETED_VERIFIED`; versions 1 and 2 retained |
| 8 | A write through the still-running server | accepted; audit chain valid, 49 events — **all still signed with version 1** (finding D66-2) |
| 9 | Final backup = the recovery point | exit 0; 87 500 bytes; SHA-256 `3347f1bd…362242f` |
| 10 | Copy artefact + manifest to a separate directory standing in for off-host storage; copy the backup key separately | SHA-256 of the copy equals the original |
| 11 | One more supplier created **after** the recovery point | accepted (partner id 5) |
| 12 | Server stopped. The live directory is treated as lost and is not read again. | — |
| 13 | Restore attempt with a **wrong key** | refused (`InvalidToken`); nothing written |
| 14 | Restore attempt with **one bit flipped** in the artefact | refused (`InvalidToken`); nothing written |
| 15 | `restore_and_verify_backup(copy, key, empty directory)` under umask 022 | succeeded: `db_sha256_verified`, 1 vault file verified, trial balance 37 763,42 = 37 763,42, `audit_chains_valid` |
| 16 | Start `dz_erp.serve` on the restored files | started; `/healthz` 200; `/readyz` `degraded` only because the new instance has no backup yet |
| 17 | Verification on the restored instance | see §3 |

## 3. Verification of the restored instance

| Check | Result |
|---|---|
| Row counts for 11 tables (companies, users, partners, products, invoices, entries, lines, stock moves, quants, audit events, documents) | identical to the recovery point |
| SHA-256 over every journal line (entry, account, debit, credit) | identical |
| `PRAGMA integrity_check` / `foreign_key_check` | `ok` / 0 violations |
| Trial balance rows over HTTP | identical to the pre-loss trial balance |
| Admin login with the original password | 200 |
| A user deactivated before the backup | still refused (401) |
| The vault document | decrypts; SHA-256 equals the stored hash |
| Audit chain | valid, 49 events |
| The record entered after the recovery point (step 11) | **absent — as expected.** This is the data-loss window, made visible. |
| New write on the restored instance | accepted; chain valid at 53 events, the 4 new ones signed with key **version 2** |
| Direct SQL change to a posted line on the restored copy | refused by trigger |
| File modes of everything restored | all `0600`, directories `0700` (after fix S66-1) |

Evidence: `evidence/drill_pre.json`, `evidence/drill_restore.json`, `evidence/drill_post.json`.

## 4. Measured times (this sandbox, 87 KB, local disk — not a basis for any target)

| Operation | Wall clock |
|---|---|
| `ops_backup run` (whole command, including interpreter start) | 0,49 s |
| `ops_backup verify --deep` (whole command) | 0,45 s |
| Copy to the stand-in off-host directory | < 0,01 s |
| `restore_and_verify_backup` (function only) | 0,02 s |
| Server start until the port was listening | under 1,3 s |

## 5. Findings

| ID | Finding | Severity | Status |
|---|---|---|---|
| S66-1 | Restore left the database and the key-bearing `manifest.json` `0644` | Medium | **Fixed** in `89a5fec` with regression tests (Phase 63 §2) |
| S66-2 | Backup staging directory mode depended on the umask | Low | **Fixed**, same commit |
| D66-2 | After `audit-rotate`, a running server keeps signing with the old key until restarted. Nothing tells the operator. | Medium | Open — operations owner. Operator guide now says to restart. Same item as S63-3. |
| D66-3 | Retention keeps one artefact per day (plus weekly and the newest). Four backups were taken within a minute during this drill; two remained. A manual "before the risky change" backup can therefore be deleted by the next scheduled run on the same day. | Low | Open — operations owner. Documented in the operator guide; copy such a backup elsewhere. |
| D66-4 | The restored database is named `erp_snapshot.sqlite3`, and the key files are named after it. Promoting it means either pointing `ERP_DB_PATH` at that name or renaming the database **and** its `.audit.key` / `.audit.keys.json` together. The administration guide says only "move the restored files into place". | Low — but an operator who renames only the database loses audit verification | Open — documentation owner. The operator guide spells it out. |
| D66-5 | The restore function has no command-line entry point; an operator must run a Python snippet. | Low | Open — product owner |
| D66-6 | The live directory was not securely destroyed; "loss" was simulated by not reading it. Media failure, partial writes and a restore from genuinely remote storage were not exercised. | Limitation | Carried in risk D4 |

## 6. Conclusion

The software recovery path works and is tamper-evident at this scale. **G6 stays BLOCKED**: there is no
production host, no off-host target, no approved RPO/RTO and no drill on realistic data.
