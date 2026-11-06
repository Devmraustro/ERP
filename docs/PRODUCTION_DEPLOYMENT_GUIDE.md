# Production deployment guide

**What this document is:** the procedure a platform owner follows to run this
application, written by the people who built it, with every unverified step
marked as unverified.

**What it is not:** evidence that a deployment has been performed. No host has
been provisioned, no image built and no traffic served from this repository's
development environment. `docs/RELEASE_READINESS_MATRIX.md` keeps the
corresponding rows **Blocked (external evidence required)** until someone
produces that evidence.

Verified ingredients (CI-verified on this commit, or verified on this host):

| Ingredient | Status |
|---|---|
| Hash-pinned dependency install (`--require-hashes --no-deps`) | CI-verified |
| `python -m dz_erp.serve` starts against a durable database and refuses an unconfigured one | Verified by tests |
| `/healthz` answers without a credential | Verified by tests |
| Safe backup restore (requires CPython 3.11.17+ / 3.12.10–3.12.x) | CI-verified; refused on 3.11.2 |
| `ops/Dockerfile`, `ops/docker-compose.yml` | **Prepared, never built or run** |
| `ops/erp.env.example` completeness | Enforced by `tests/test_phase37_deployment_assets.py` |

---

## 1. Prerequisites

* Linux host, 2 vCPU / 2 GB RAM minimum for the API plus OCR headroom.
* CPython **3.11.17+** (or 3.12.10 ≤ x < 3.13). Below 3.11.17 the safe archive
  extraction used by backup restore refuses to run, so backups could not be
  verified — the container image pins 3.11.17 for exactly this reason.
* A TLS-terminating reverse proxy on the same host (nginx, Caddy, HAProxy).
* Durable storage for the database, the vault and the security log.
* An off-host destination for rotation-ledger checkpoints and log shipping.

## 2. Filesystem layout

```
/srv/dz-erp/
  app/            source checkout (read-only to the service user)
  state/erp.sqlite3        database (+ -wal, -shm)
  state/vault/             encrypted document vault + the vault key file
  logs/security.jsonl      structured security log
  ops/                     units and checkpoints (this directory)
```

The service user owns `state/` and `logs/`; the checkout is read-only to it.
Creating the first database is the application's job, not the operator's — never
hand-create tables.

## 3. Bootstrap (once, deliberately)

```bash
cd /srv/dz-erp/app
export ERP_DB_PATH=/srv/dz-erp/state/erp.sqlite3
export ERP_VAULT_PATH=/srv/dz-erp/state/vault
python -m dz_erp.serve --bootstrap-admin admin --company-name 'SARL Example'
```

This creates one company and one administrator, prompting for the password. It
is a separate, audited act and is deliberately **not** part of service start-up:
a service that creates an administrator as a side effect of starting is a
backdoor waiting for a typo.

## 4. Configuration

`ops/erp.env.example` is the authoritative list, with the reasoning beside each
variable. The decisions that matter:

| Variable | Guidance |
|---|---|
| `ERP_DB_PATH`, `ERP_VAULT_PATH` | Absolute durable paths. Never a tmpfs, never the image. |
| `ERP_SECURITY_LOG_PATH` | A durable path; ship it off-host (§8). |
| `ERP_HOST` | `127.0.0.1` for a bare-metal install behind a local proxy; `0.0.0.0` only inside a container whose published port is loopback-bound. |
| `ERP_TRUSTED_PROXIES` | **Set this whenever a proxy is in front.** Unset means `X-Forwarded-For` is ignored and every request is attributed to the proxy's address. |
| `ERP_DOCS_ENABLED` | `false` in staging and production. `/docs`, `/redoc`, `/openapi.json` return 404 otherwise. |
| `ERP_TOKEN_TTL_SECONDS` | Session lifetime; 8 hours by default. Shorter for high-privilege deployments. |
| `ERP_AUTH_FAILURE_LIMIT` / `_WINDOW_SECONDS` | Failed logins per (client, account). Per-process: with N replicas the ceiling is N × limit. |
| `ERP_RATE_LIMIT_PER_MINUTE` | `0` (off) unless the proxy cannot rate-limit. |
| `ERP_SECURITY_LOG_MAX_SIZE` / `_MAX_FILES` | Rotation. Every request is logged, so set these before the first busy day. |
| `ERP_KEY_PROVIDER` | Leave unset for file-backed keys. `kms`/`hsm` **refuses to start** unless a provider is supplied — by design. |

Secrets (`ERP_VAULT_KEY`, `ERP_AUDIT_HMAC_SECRET`) come from the platform's
secret store, never from a file next to a compose file.

## 5. Starting the service

Bare metal (systemd):

```ini
[Service]
User=dz-erp
EnvironmentFile=/etc/dz-erp/erp.env
WorkingDirectory=/srv/dz-erp/app
ExecStart=/usr/bin/python -m dz_erp.serve --host 127.0.0.1 --port 8000
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/srv/dz-erp/state /srv/dz-erp/logs
```

Container:

```bash
cp ops/erp.env.example ops/erp.env && $EDITOR ops/erp.env
docker compose -f ops/docker-compose.yml build      # never run in this repo's dev env
docker compose -f ops/docker-compose.yml up -d
docker compose -f ops/docker-compose.yml run --rm bootstrap admin
```

**Health model.** `/healthz` is a *liveness* signal: it answers without a
credential and without touching the database. That is intentional — a database
outage must not restart-loop the API. Database readiness is a separate check the
operator runs (§7), not something the container's healthcheck decides.

**One replica.** The rate limiter is in-process and SQLite has a single writer.
Running two replicas multiplies the login ceiling and invites write contention.
Scaling horizontally is a design change, not a compose edit.

## 6. Reverse proxy

* Terminate TLS; redirect HTTP to HTTPS.
* Set `X-Forwarded-For` and confirm `ERP_TRUSTED_PROXIES` matches the proxy's
  CIDR — otherwise attribution is wrong and the login throttle keys on the proxy.
* Apply your own rate limit: the application's limiter is per process and cannot
  see other replicas.
* Set a request body limit aligned with `ERP_MAX_UPLOAD_BYTES`.
* Do not expose `/docs` even if `ERP_DOCS_ENABLED` is true.

## 7. Verification after start (all of these are operator steps)

```bash
curl -fsS http://127.0.0.1:8000/healthz            # {"status":"ok", ...}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs   # 404
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/accounts  # 401
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forwarded-For: 203.0.113.9' \
     -X POST http://127.0.0.1:8000/api/v1/auth/token \
     -H 'Content-Type: application/json' -d '{"username":"admin","password":"wrong"}'   # 401
# repeat the last call ERP_AUTH_FAILURE_LIMIT times, then expect 429 + Retry-After
```

Then, on a copy of real state: restore a backup into a scratch directory and
verify it (see `docs/BACKUP_AND_RECOVERY_RUNBOOK.md`), and rotate a key to
confirm the checkpoint export reaches off-host storage.

## 8. Logging and monitoring

* `ERP_SECURITY_LOG_PATH` receives `HTTP_REQUEST`, `SECURITY_AUTH_RATE_LIMITED`,
  `AUTH_LOGIN_REJECTED`, `AUTH_REQUEST_REJECTED` and every audited business event.
  It contains no password, no token and no request body (asserted by tests).
* Rotation is enforced by `ERP_SECURITY_LOG_MAX_SIZE` / `_MAX_FILES`; ship the
  files off-host before they rotate away.
* Minimum alerts: `SECURITY_AUTH_RATE_LIMITED` rate, `SECURITY_RATE_LIMITED`
  rate, audit-chain verification failure, restore verification failure,
  checkpoint age, disk headroom on the state volume.

## 9. What remains blocked, and by whom

| Item | Owner | Evidence required |
|---|---|---|
| KMS/HSM key custody | platform owner | provider handle + rotation drill |
| Off-host checkpoints and log shipping | platform owner | delivered files, not a configuration file |
| Production-scale DR and concurrency | platform owner | drill report with numbers |
| Alert delivery | operations | a fired alert that reached a human |
| Qualified Algerian tax review | engagement's expert-comptable | signed review per rate and bracket |
| Law 18-07 / ANPDP registration evidence | legal | registration and retention decision |

No amount of work inside this repository closes any row in that table.
