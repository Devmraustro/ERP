# Phase 10 — Operational Readiness Report

**Baseline:** session branch `arena/7557bcfb-erp` @ `4fc1364` + the user's uncommitted
work · CPython 3.12.10 · executed 2026-11-10
**Evidence:** `docs/release/phase10/evidence/logs/` (`operational_drill.log`,
`operational_drill.json`, `final_full.log`, `ruff.log`, `compileall.log`)
**Drill script:** `docs/release/phase10/evidence/operational_drill.py` (16/16 PASS)

## 1. What was missing after Phase 9, and what now exists

| Requirement | Phase 9 | Phase 10 |
| --- | --- | --- |
| Readiness endpoint | none | **`GET /readyz`** — 200/`ready`, 200/`degraded`, 503/`not_ready` |
| Database readiness | none | `PRAGMA quick_check`, core-table presence, **write lock actually taken and rolled back** |
| OCR subsystem health | none | shallow: package present; deep (`?deep=1`): **engine really loads** |
| Scheduled encrypted backups | manual only | `python -m dz_erp.ops_backup run` + `dz-erp-backup.timer` (02:15 UTC nightly) |
| Backup retention policy | none | daily 7 / weekly 4, legal-hold aware, **never deletes the newest** |
| Backup integrity monitoring | none | SHA-256 sidecar manifest + `verify` + weekly `verify --deep` (real restore) |
| Log rotation | none | `ops/logrotate/dz-erp` (copytruncate, 14 daily, compressed, 0600) |
| Disk-space monitoring | none | `ops_checks disk` + `dz-erp-disk-check.timer` (15 min), 512 MiB floor |
| Backup failure alerts | none | `dz_erp.ops_alerts` → journal JSON + optional webhook |
| Recovery runbook | absent | `BACKUP_AND_RECOVERY_RUNBOOK.md` |

## 2. The drill, step by step (all executed)

`operational_drill.py` starts a **real** `python -m dz_erp.serve` process, seeds a
balanced posted entry, and drives both HTTP and the real CLIs as subprocesses:

| # | Step | Result |
| --- | --- | --- |
| 1 | Bootstrap a fresh installation | PASS — admin created |
| 2 | `GET /healthz` | PASS — 200 |
| 3 | `GET /readyz` at boot (no backup yet) | PASS — 200, `degraded`, **all critical checks ok** |
| 4 | Token issuance over HTTP | PASS |
| 5 | `ops_backup run` | PASS — 1 artefact, exit 0 |
| 6 | Artefact + manifest | PASS — mode `0600`, sha256 recorded, 13 796 bytes |
| 7 | `GET /readyz` after the first backup | PASS — 200, **`ready`** |
| 8 | `ops_backup verify` | PASS — exit 0 |
| 9 | Corrupt one byte, verify again | PASS — exit 1 and **`BACKUP_INTEGRITY_FAILED`** on stderr |
| 10 | `ops_backup verify --deep` | PASS — restore, integrity, trial balance, audit chain |
| 11 | `ops_checks disk` with an impossible floor | PASS — exit 1 and **`DISK_LOW`** |
| 12 | `ops_checks backup-age` on an aged artefact | PASS — exit 1 and **`BACKUP_STALE`** |
| 13 | Retention (2 daily / 1 weekly + legal hold) | PASS — 6 → 4 artefacts, held one survived |
| 14 | `GET /readyz` with the vault made unwritable | PASS — **503**, `not_ready` |
| 15 | `GET /healthz` after that failure | PASS — still 200 (liveness ≠ readiness) |
| 16 | Database after the drill | PASS — `integrity_check=ok`, 1 posted entry |

Summary line from the run: `{"steps": 16, "passed": 16, "failed": 0}`.

## 3. Readiness semantics (deliberate, and documented in the code)

- **Critical fails → 503.** Database unreadable/unwritable, missing core tables, vault
  missing/unwritable, loose key-file permissions, disk below the floor.
- **Advisory fails → 200 `degraded`.** Stale backup; key *directory* mode not 0700.
  A late backup must not pull the application out of a load balancer.
- **Nothing sensitive in the body.** No paths, no key material, no customer counts. A
  readiness endpoint is scraped by infrastructure and is not authenticated.
- **Writability is proved, not assumed.** The check takes a write lock and rolls it
  back. *Tested finding:* a `0444` database file inside a writable directory is still
  writable (SQLite writes to `-wal`), so the check targets the volume — which is the
  failure operators actually hit — and the test says so instead of asserting a
  property SQLite does not have.

## 4. Backup automation: what is enforced in code

1. Every artefact gets a `.manifest.json` sidecar with SHA-256 and size, written `0600`.
   **A monitor can detect a corrupted backup without the decryption key.**
2. `run` writes the artefact, the manifest, then prunes — and a retention failure is
   alerted but never loses the fresh backup.
3. `prune` keeps: the newest artefact always; the newest per day for `--retain-daily`;
   the newest per ISO week for `--retain-weekly`; **and anything with
   `legal_hold: true`, forever.**
4. `verify` recomputes every hash; `verify --deep` restores each artefact into a
   temporary directory and inspects the restored database **independently** —
   `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and a Decimal sum of posted
   debit/credit — rather than trusting the restorer's own summary fields.
5. The backup key is read from a `0600` file (preferred) or an environment variable;
   a key file with any group/other permission bit is **refused** (`PermissionError`).

## 5. Alerts (secret-free, journal-first)

`BACKUP_FAILED`, `BACKUP_STALE`, `BACKUP_INTEGRITY_FAILED`, `RETENTION_FAILED`,
`DISK_LOW`, `READINESS_FAILED`, `OCR_UNAVAILABLE`. Each is a single JSON line on stderr
(systemd journal) and, if `ERP_ALERT_WEBHOOK` is set, an HTTP POST. The payload carries
event, severity, timestamp and counters — never a path, a key, a document or an amount.

## 6. Still missing (unchanged honesty)

| Gap | Consequence |
| --- | --- |
| No metrics endpoint (Prometheus) | alerting is threshold-based on logs; no dashboards, no trend |
| No off-host copy implemented by the product | the runbook requires it; the software does not ship an S3/rsync client (V1 scope: no MinIO/S3) |
| Webhook is fire-and-forget | a lost webhook is lost; the journal remains the durable record |
| No per-record legal-hold flag in the database | holds are operational + backup-level (see retention policy §4) |
| Log rotation covers the security log and `/var/log/dz-erp` | not a general log pipeline |

## 7. Verdict

Operational readiness moved from *"a supervised pilot only"* to **"runnable unattended
with a runbook"**: readiness is observable and fails closed, backups are scheduled,
retained, hashed, restorable and alerted on, disk and OCR health are checked on a timer,
and the security log is rotated. The residual gaps above are monitoring depth and
off-host transport, not correctness.
