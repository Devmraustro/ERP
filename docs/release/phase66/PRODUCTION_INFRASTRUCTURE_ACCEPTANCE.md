# Phase 66 — Production Infrastructure Acceptance

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Gate G6 | **BLOCKED** |
| Reason | **No production infrastructure exists or was shown to this session.** No host, domain, certificate, storage volume, off-host backup target, monitoring endpoint or named operator was provided. Every item that needs real infrastructure is `NOT PROVISIONED`. |
| What was verified | The parts of each item that depend only on the software, on a throw-away instance with synthetic data. Those are marked `SOFTWARE VERIFIED` and are **not** infrastructure acceptance. |

## 1. Deployment model the software supports

One Linux host, one process (`python -m dz_erp.serve`), one SQLite file on local disk, bound to
loopback, behind a TLS-terminating reverse proxy, with the systemd units in `ops/`.

**Not supported:** serverless or ephemeral platforms (Vercel, Lambda-style functions, containers
without a persistent volume), any network file system for the database, and more than one application
host. The application needs a durable local file system, a long-lived process and local key files. A
Vercel connector is available to this session; **it was not used and must not be**.

## 2. Acceptance items

| # | Item | Infrastructure status | What was verified in software | Evidence required to close |
|---|---|---|---|---|
| 1 | Named production host (OS, CPU, RAM, disk) | NOT PROVISIONED | — | Host inventory signed by the operations owner |
| 2 | Dedicated unprivileged service account | NOT PROVISIONED | Units in `ops/` set `UMask=0077` | `id`, unit file as installed |
| 3 | Supported interpreter (CPython 3.12.10+ in 3.12, or 3.11.17+) | NOT PROVISIONED | Restore refuses older interpreters (Phase 62 §4) | `python --version` on the host |
| 4 | Hash-locked installation | NOT PROVISIONED | SOFTWARE VERIFIED — Phase 63 §1 | Install log from the host |
| 5 | Persistent local volume for database and vault | NOT PROVISIONED | — | Mount table; file-system type; confirmation it is not NFS/SMB |
| 6 | Database and key file permissions | NOT PROVISIONED | SOFTWARE VERIFIED — new database `0600`; keys `0600`; key directory `0700` (fix S63-1) | `stat` output on the host |
| 7 | Loopback bind and reverse proxy | NOT PROVISIONED | SOFTWARE VERIFIED — refuses `0.0.0.0` without `ERP_ALLOWED_HOSTS`; rejects unknown `Host` | Proxy configuration; external port scan |
| 8 | TLS certificate and renewal | NOT PROVISIONED | The application does not terminate TLS | Certificate chain, expiry, renewal job |
| 9 | `ERP_ALLOWED_HOSTS`, `ERP_TRUSTED_PROXIES` set for the real domain | NOT PROVISIONED | SOFTWARE VERIFIED — defaults are loopback names only | Environment file (secrets removed) |
| 10 | Firewall: only 443 exposed | NOT PROVISIONED | — | Rule listing and external scan |
| 11 | Backup key stored on a different volume from the backups | NOT PROVISIONED | Drill kept key and artefacts in separate directories only | Paths and custody record |
| 12 | Scheduled backup | NOT PROVISIONED | SOFTWARE VERIFIED — `ops_backup run`; timers exist in `ops/` | `systemctl list-timers`; seven consecutive daily artefacts |
| 13 | Scheduled backup verification | NOT PROVISIONED | SOFTWARE VERIFIED — `verify --deep` | Timer status; `verify-status.json` history |
| 14 | Off-host, encrypted backup copy | NOT PROVISIONED | Simulated by a second local directory; hash matched | Destination, transfer job, a restore **from that copy** |
| 15 | Restore drill on the real host class with production-scale data | NOT PROVISIONED | Synthetic drill only — `RECOVERY_DRILL_REPORT.md` | A measured drill; register risk D4 |
| 16 | Approved RPO and RTO | **NONE APPROVED** | No target was given, so none is claimed | Targets signed by the business owner, then a drill that meets them |
| 17 | Readiness and health monitoring with alerting | NOT PROVISIONED | SOFTWARE VERIFIED — `/readyz` went `degraded` → `ready` after the first backup; `ops_checks` has cron exit codes | Monitor configuration; a test alert received by a named person |
| 18 | Durable security log and rotation | NOT PROVISIONED | SOFTWARE VERIFIED — `ERP_SECURITY_LOG_PATH` written, no secrets in it; `ops/logrotate` exists | Log path, rotation and retention on the host |
| 19 | Key custody procedure (who holds what, two-person rule) | NOT PROVISIONED | Rotation works; a single operator can do it (risks S1, S3 Open) | Signed custody procedure |
| 20 | Disk-space, time synchronisation and patching policy | NOT PROVISIONED | SOFTWARE VERIFIED — readiness fails below `ERP_MIN_FREE_BYTES` (default observed 512 MiB) | NTP status; patch policy; named owner |

**20 items: 0 accepted. 0 provisioned.** Nine have their software half verified; that changes nothing
about the gate.

## 3. Sign-off

| Field | Value |
|---|---|
| Operations owner (name) | |
| Host identifier | |
| Date provisioned | |
| Items accepted / open | / |
| RPO / RTO approved by (name, date) | |
| Signature | |

**G6 remains BLOCKED until every row has evidence from the real environment.**
