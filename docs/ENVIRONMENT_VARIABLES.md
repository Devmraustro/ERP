# Environment variable reference

Every environment variable the application reads, what it does, its default, and what happens when
it is unset. Phase 42 requires that **all** variables be documented, so this file is the
authoritative list; `docs/STAGING_SECURITY_CONFIGURATION.md` remains the staging hardening guide.

Categories: **SECRET** (must come from a secret manager, never from a file in the repo), **PATH**
(location of state), **TUNING** (operational limits).

## Secrets

| Variable | Cat | Default | Meaning | If unset |
| --- | --- | --- | --- | --- |
| `ERP_AUDIT_HMAC_SECRET` | SECRET | none (key file `<dbname>.audit.key`) | HMAC key version 1 for the tamper-evident `audit_events` chain | Falls back to the key file; a deployment must provision one of the two. Losing it makes the chain unverifiable. |
| `ERP_VAULT_KEY` | SECRET | none (`<vaultname>.vault.key`) | Envelope key for every document stored at rest | Falls back to the key file. **Without it, stored documents are unreadable.** |

## Paths and state

| Variable | Cat | Default | Meaning | If unset |
| --- | --- | --- | --- | --- |
| `ERP_DB_PATH` | PATH | platform data dir | SQLite database file | Default data dir is used |
| `ERP_VAULT_PATH` | PATH | platform data dir | Document vault directory | Default |
| `ERP_BACKUP_DIR` | PATH | none | Where backups are written and read back by `/readyz`'s backup-age check | Backup-age check is **advisory only** (reported `degraded`, HTTP stays 200) |
| `ERP_SECURITY_LOG_PATH` | PATH | none | Durable append-only security log (`JsonlSecurityLog`) | **In-memory only — every rejection record is lost on restart.** Never acceptable in staging or production. |
| `ERP_SECURITY_LOG_MAX_SIZE` | TUNING | implementation default | Rotation size for the security log | Default rotation |
| `ERP_SECURITY_LOG_MAX_FILES` | TUNING | implementation default | Rotated files retained | Default retention |

## Network exposure

| Variable | Cat | Default | Meaning | If unset |
| --- | --- | --- | --- | --- |
| `ERP_HOST` | TUNING | `127.0.0.1` | Bind address | Loopback only. Only set `0.0.0.0` behind a TLS-terminating reverse proxy. |
| `ERP_PORT` | TUNING | `8000` | Bind port | 8000 |
| `ERP_TRUSTED_PROXIES` | TUNING | none | CIDR list whose `X-Forwarded-For` is honoured | **No forwarded header is trusted**; the peer address is used. Correct and safe default. |
| `ERP_DOCS_ENABLED` | TUNING | `false` | Serves `/docs`, `/redoc`, `/openapi.json` | Disabled (404). Do not enable in staging or production. |

## Operations

| Variable | Cat | Default | Meaning | If unset |
| --- | --- | --- | --- | --- |
| `ERP_LOG_LEVEL` | TUNING | `INFO` | Root log level | INFO |
| `ERP_TOKEN_TTL_SECONDS` | TUNING | `DEFAULT_TOKEN_TTL_SECONDS` | Bearer session lifetime | Default |
| `ERP_MIN_FREE_BYTES` | TUNING | implementation default | Readiness fails when free disk falls below this | Default threshold |
| `ERP_BACKUP_MAX_AGE_SECONDS` | TUNING | implementation default | Readiness marks the backup stale past this age | Default |
| `ERP_BACKUP_RETAIN_DAILY` | TUNING | implementation default | Daily backups retained | Default |
| `ERP_BACKUP_RETAIN_WEEKLY` | TUNING | implementation default | Weekly backups retained | Default |

## Rate limiting (added in Phase 35 / SEC-13)

Defaults are chosen so that an unconfigured deployment is bounded rather than unbounded. Verify the
running values with `GET /api/v1/system/rate-limit-policy` (admin only) rather than from a config
file — the endpoint reports what the process is actually doing.

| Variable | Cat | Default | Meaning | If unset |
| --- | --- | --- | --- | --- |
| `ERP_RATE_LIMIT_BACKEND` | TUNING | `memory` | `memory` or `sqlite`. `sqlite` shares counters across processes on **one database file** | In-process counters only. **With more than one worker, per-process limits multiply** — see the warning below. |
| `ERP_RATE_LIMIT_PER_MINUTE` | TUNING | `0` (off) | Global per-client ceiling | No global ceiling; the per-endpoint login limiter still applies |
| `ERP_RATE_LIMIT_FAILURE_MODE` | TUNING | `open` | Behaviour when the counter store is unavailable | See fail-mode table below |
| `ERP_AUTH_FAILURE_LIMIT` | TUNING | implementation default | Failed logins allowed per `(client, account)` window | Default limit |
| `ERP_AUTH_FAILURE_WINDOW_SECONDS` | TUNING | implementation default | Window for the login limiter | Default window |
| `ERP_AUTH_CLIENT_FAILURE_LIMIT` | TUNING | implementation default | Failed logins per client address, across accounts | Default |
| `ERP_AUTH_FAILURE_MAX_KEYS` | TUNING | `DEFAULT_MAX_KEYS` (4096) | Bound on tracked keys, so probing keys cannot exhaust memory | Default bound |

### Honest scope of the rate limiter

`ERP_RATE_LIMIT_BACKEND=memory` bounds **one process**. It is not a distributed rate limiter. With
`sqlite` the counters are shared across processes that use the same database file, which is a real
boundary but still not a distributed one: replicas with separate database files, or several hosts,
each enforce their own limit. The `/api/v1/system/rate-limit-policy` response states this
(`deployment_scope`) so a reviewer can see it from the running process.

### Failure mode

When the counter store raises, the configured failure mode decides:

| Mode | Behaviour | When to choose |
| --- | --- | --- |
| `open` | The request proceeds; the failure is logged as `RATE_LIMIT_BACKEND_UNAVAILABLE` | Availability priority — a broken store must not take login down for everyone |
| `closed` | The request is refused | Security priority — never authenticate unthrottled |

Either way the refusal is audited, and only the error behind **the current decision** is logged, so
an ordinary 429 is not reported as a dependency outage for the rest of the process's life.

## Variables provided by the platform (read, not set by operators)

| Variable | Meaning |
| --- | --- |
| `XDG_DATA_HOME` | Linux base for the default data dir |
| `LOCALAPPDATA` | Windows base for the default data dir |

## Audit note

Secrets are read from the environment or from owner-only key files, never logged. The structured
logger redacts `SENSITIVE_KEYS` and matches `SENSITIVE_VALUE_PATTERNS` before anything reaches a
sink, which the Phase 9 and Phase 47 suites both assert from the durable file itself.
