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

### 6.1 TLS and Host hardening (SEC-15)

The service speaks plain HTTP and expects TLS to end in front of it. The
application now validates the `Host` header itself: every request whose `Host`
does not match `ERP_ALLOWED_HOSTS` is refused with `400 Invalid Host header.`
before routing, and the refusal is logged as `SECURITY_HOST_HEADER_REJECTED`.
When `ERP_ALLOWED_HOSTS` is unset only loopback/test names are accepted, so a
public deployment **must** set it:

```
ERP_ALLOWED_HOSTS=erp.example.dz,*.erp.example.dz
ERP_TRUSTED_PROXIES=<the proxy's own address>/32
```

Configuration rules, in order of how often they are got wrong:

1. **One trust layer.** The server starts with `proxy_headers=False`, so uvicorn
   never rewrites the peer address from a header; `ERP_TRUSTED_PROXIES` is the
   only thing that decides whether `X-Forwarded-For` is believed. Do not set
   `FORWARDED_ALLOW_IPS` — and never to `*` — in a wrapper script; that would
   reintroduce the bypass this configuration exists to prevent.
2. **Never trust a range you do not control.** `0.0.0.0/0` or `::/0` in
   `ERP_TRUSTED_PROXIES` hands IP spoofing (and the audit trail) back to the
   caller. Leave the variable unset when there is no proxy.
3. **The proxy should enforce the same host list**, so an unknown name is
   rejected before it reaches the application at all. The in-process check is the
   backstop, not the primary control.
4. **Headers the proxy must add**: `Strict-Transport-Security` (after the first
   successful HTTPS deployment), `X-Content-Type-Options: nosniff`,
   `X-Frame-Options: DENY` (or CSP `frame-ancestors 'none'`), and a body ceiling
   (`client_max_body_size`) matching `ERP_MAX_REQUEST_BYTES`.
5. **No cookies are used.** The API authenticates with a bearer token in the
   `Authorization` header, which is not sent automatically by browsers, so there
   is no CSRF surface to configure. If a cookie is ever introduced it must be
   `Secure; HttpOnly; SameSite=Strict` and the CSRF story must be re-reviewed.

### 6.2 Password policy (SEC-13)

Every credential-creating path — the admin API, `--bootstrap-admin`, self-service
change and administrative reset — applies the same policy in
`dz_erp.auth.validate_password_policy`:

* length floor `ERP_PASSWORD_MIN_LENGTH` (cannot be configured below 8; **12 is
  the recommended value** for a deployment);
* maximum 1024 characters (bounds the work per request);
* no composition classes (NIST SP 800-63B favours length), but the password may
  not equal the username or company name and a small weak-value list is refused;
* hashing is PBKDF2-HMAC-SHA256 with 600,000 iterations; a credential created
  under a lower iteration count is re-hashed automatically on its next
  successful login (`AUTH_PASSWORD_REHASHED`).

Lifecycle endpoints (both audit their outcome, both revoke live tokens):

| Endpoint | Who | Effect |
|---|---|---|
| `POST /api/v1/auth/password` | the account holder | requires the current password; revokes **all** tokens, so the client re-authenticates |
| `POST /api/v1/users/{id}/password-reset` | administrator, same company | sets a new password and revokes the target's tokens; cross-tenant targets answer 404 |

There is no self-service "forgot password" flow in V1: an operator who cannot
authenticate is reset by an administrator. A mailer-based reset is a separate
feature with its own threat model (reset-link poisoning, which is why Host
validation above matters).

### 6.3 V1 scope: AWS KMS and MinIO/S3 are explicitly out

| Capability | V1 status | Why |
|---|---|---|
| AWS KMS / HSM key custody | **not supported** | no KMS/HSM client is bundled; declaring `ERP_KEY_PROVIDER=kms|hsm` is refused at start-up instead of being served by file-backed material |
| MinIO / S3-compatible backup storage | **not supported** | `dz_erp/backup_storage_s3.py` requires `boto3`, which is deliberately absent from the hash-locked `requirements.lock.txt` |

Supported V1 configuration: local key custody (owner-only files, see
`docs/KEY_CUSTODY_THREAT_MODEL.md`) and local backup storage. Anyone who enables
either capability is running an untested, unverified configuration.

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
