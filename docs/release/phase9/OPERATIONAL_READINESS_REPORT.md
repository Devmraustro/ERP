# Phase 9J — Monitoring & Operational Readiness

**Baseline:** PR #7 head `f603e38` · executed 2026-11-09

## 1. What actually exists in the product

| Capability | Present | Where / evidence |
| --- | --- | --- |
| Liveness endpoint | yes | `GET /healthz` → `{"status":"ok","service":"algerian-ai-erp"}` (measured 1.76 ms p50) |
| Readiness (DB/vault/disk) | **no** | — |
| Request correlation | yes | `X-Request-Id` on every response, echoed into the log |
| Security event log | yes | auth outcomes without credentials (executed test) |
| DR alert taxonomy | yes | `dz_erp/alerting.py`: `BACKUP_FAILED`, `BACKUP_UPLOAD_FAILED`, `RESTORE_FAILED`, `KEY_UNAVAILABLE`, `RETENTION_FAILED`, `RPO_EXCEEDED`, `RTO_EXCEEDED` |
| Alert delivery | log or webhook | `LogAlertProvider`, `WebhookAlertProvider`; payload is explicitly secret-free |
| RPO/RTO targets | configurable | `ERP_RPO_TARGET_SECONDS` (default 86 400), `ERP_RTO_TARGET_SECONDS` (default 3 600) |
| Metrics endpoint (Prometheus) | **no** | — |
| Checkpoint/monitoring suite | tests present | `test_phase20_checkpoints_and_monitoring`, `test_phase24_operational_readiness` (both passed in the full run) |

## 2. Measured DR figures against the configured targets

| Quantity | Measured in the drill | Configured target | Verdict |
| --- | --- | --- | --- |
| Restore time | 0.023 s (17 KB synthetic DB) | RTO 3 600 s | within target — but the artefact is tiny, see the DR report §4 |
| Backup→restore gap | 0.011 s | RPO 86 400 s | within target by construction |

These are **drill** numbers with a labelled caveat: the real RPO equals the backup
schedule interval, and today **no schedule ships with the product**.

## 3. The runbook an operator must be able to follow

1. **Start:** `python -m dz_erp.serve` under the service manager, bound to `127.0.0.1`,
   behind the TLS proxy.
2. **Watch:** `GET /healthz` every 30 s (liveness); `X-Request-Id` in logs to trace a
   complaint; `GET /api/v1/system/rate-limit-policy` to explain a `429`.
3. **Back up:** run the encrypted backup on a schedule (`cron`/timer), copy the artefact
   **off-host**, alert on `BACKUP_FAILED`.
4. **Test restore:** monthly, into an isolated directory, and verify the trial balance
   and the audit chain — this drill is the script to use
   (`docs/release/phase9/evidence/dr_drill.py`).
5. **Rotate keys** per the rotation runbook (`dz_erp/rotate_keys.py`), never by editing
   files by hand.
6. **Restart:** sessions are database-backed and survive a restart (executed), so a
   restart is safe; verify `integrity_check` after any unclean shutdown
   (`test_phase50_wal_startup_contention` covers WAL contention).

## 4. Gaps (unchanged from Phase 8, plus what Phase 9 adds)

| Gap | Severity | Mitigation |
| --- | --- | --- |
| No readiness probe | medium | operator-side synthetic check of a cheap authenticated endpoint |
| No metrics endpoint | medium | log-based counting; add Prometheus exporter before scaling |
| No backup scheduler/retention | **high** for a real business | cron + off-host copy + retention, documented in §3.3 |
| No log rotation | medium | journald / logrotate |
| No on-call escalation | operational | out of technical scope; must be agreed with the company |
| Retention policy for records (legal) | **high** | blocked on the expert-comptable question Q9 |

## 5. Verdict

Monitoring is **sufficient for a supervised pilot and insufficient for unattended
production**: liveness, correlation ids, secret-free security logs and a complete DR
alert taxonomy exist; readiness, metrics, scheduling and rotation do not.
