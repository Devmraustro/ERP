# Operational Readiness Report — Phase 8

**Baseline:** PR #7 head `f603e38`. **Scope:** can this V1 be *operated* — started,
observed, backed up, restored, and left running — without a developer present?

## 1. Start / stop

```bash
python -m dz_erp.serve --host 127.0.0.1 --port 8000
python -m dz_erp.serve --bootstrap-admin admin --company-name "…"   # first run
```

Verified by execution: the server was started as a **real process** in the TLS harness
(`:8099`) and again in the performance probe (`:8097`), bootstrapped from an empty
directory, and served authenticated traffic. Defaults are `ERP_HOST=127.0.0.1`,
`ERP_PORT=8000` — loopback by default, which is the safe default behind a proxy.

## 2. Health

| Endpoint | Auth | Returns |
| --- | --- | --- |
| `GET /healthz` | none | `{"status":"ok","service":"algerian-ai-erp"}` |

**Limitation, stated plainly:** `/healthz` is a *liveness* signal only. It does not
open the database, does not check the vault directory, and does not check free disk.
A dead disk or an unreadable key directory still returns `200`. A readiness probe
that performs a real write/read round-trip is a Phase 9 recommendation, not a
Phase 8 claim.

## 3. Configuration surface (verified present)

`ERP_DB_PATH`, `ERP_VAULT_PATH`, `ERP_ALLOWED_HOSTS`, `ERP_TRUSTED_PROXIES`,
`ERP_MAX_REQUEST_BYTES`, `ERP_RATE_LIMIT_PER_MINUTE`, `ERP_RATE_LIMIT_BACKEND`,
`ERP_RATE_LIMIT_FAILURE_MODE`, `ERP_AUTH_FAILURE_LIMIT`,
`ERP_AUTH_FAILURE_WINDOW_SECONDS`, `ERP_AUTH_CLIENT_FAILURE_LIMIT`,
`ERP_AUTH_FAILURE_MAX_KEYS`, `ERP_HOST`, `ERP_PORT`.
Every one of these is read from the environment and none is written to a log.

Operator-facing, unauthenticated introspection: `GET /api/v1/upload-policy` (limits)
and, authenticated, `GET /api/v1/system/rate-limit-policy` (effective host + limiter
policy), which is what an operator needs to answer "why is this client limited?".

## 4. Backup and restore

The application ships encrypted backup and verified restore:
`create_encrypted_backup` / `restore_and_verify_backup` with a generation id, a
manifest of database and vault hashes, and an isolated restore target.

Executed in Phase 9F (`docs/release/phase9/DISASTER_RECOVERY_ACCEPTANCE.md`):
backup 0.011 s / 17 656 bytes; restore 0.023 s; DB hash verified; vault file hash
verified; `integrity_check` ok; 0 foreign-key violations; trial balance identical and
balanced; audit chain verified with 3 events and identical hashes.

**Not done in Phase 8:** no *scheduled* backup job, no retention rotation, no
off-host copy. Those are operational procedures, not code paths; they are specified in
Phase 9J and require a real host to schedule.

## 5. Logging

- Every response carries `X-Request-Id`; the id is echoed into the request log.
- Authentication attempts are logged **without** credentials
  (`test_credentials_never_reach_the_security_log`).
- Log destination is stderr by default; no log rotation is configured in V1 — the
  operator's service manager must handle rotation.

## 6. Failure behaviour

| Failure | Behaviour |
| --- | --- |
| Unknown/oversized body | `413`, no partial write |
| Too many auth failures | `429` + `Retry-After`, other accounts unaffected |
| Limiter backend failure | **fail closed** (`ERP_RATE_LIMIT_FAILURE_MODE` default) |
| Empty trusted-proxy config | forwarded headers ignored (fail closed) — verified |
| Forged/expired token | `401`, no data leak |
| Direct backend exposure | *not* prevented by the app — must be prevented by the deployment |

## 7. Operational blockers for V1

1. No readiness probe (see §2) — an operator cannot automate "is it actually able to
   work?".
2. No scheduled backup / retention policy in the product; must be a documented cron
   job on the host.
3. No log rotation in the product.
4. Desktop client unverified (see `WINDOWS_DESKTOP_ACCEPTANCE.md`).

None of these is a security defect; all are operational gaps that a runbook must
close before a real company's books run on it.

## 8. Readiness verdict

**Operable for a supervised pilot. Not operable unattended** until the readiness probe,
scheduled encrypted backups with retention, and log rotation exist. The runbook that
would make it unattended is `docs/release/phase9/OPERATIONAL_READINESS_REPORT.md`.
