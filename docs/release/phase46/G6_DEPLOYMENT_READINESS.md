# Phase 46 — Step 4: G6 Deployment Readiness

**Gate:** G6 — production deployment readiness
**Status: BLOCKED — no production target exists**
**Recorded:** 2026-11-09

---

## 1. Does a production environment actually exist?

**No.** Verified by direct inspection of the repository and the environment:

| Probe | Result |
| --- | --- |
| Infrastructure-as-code (`deploy*/`, `infra*/`, `terraform*/`, `k8s*/`, `ansible*/`) | **absent** |
| `Dockerfile` / `docker-compose` | **absent** |
| Deployment job in CI | **absent** — `ci.yml` states "No deployment or production resource is touched" |
| Production secrets / `.env` | **absent** |
| Known production host, domain, or IP | **none provided** at any point in Phases 29–46 |

Consequently **no deployment was attempted**, and none can be. This is the correct
outcome: deploying to a target that does not exist would mean inventing one.

What follows is therefore a **readiness assessment and a runbook**, not a
deployment record. Every item is marked with what is genuinely available
(✅ verified in-repo), what is configured but unverified in a real environment
(⚠️), and what does not exist (❌).

---

## 2. Criterion-by-criterion readiness

### 2.1 Host / domain ownership

| Item | Status |
| --- | --- |
| Production hostname | ❌ not supplied |
| Domain ownership evidence | ❌ none |
| DNS control | ❌ not established |

**Owner action:** designate the production hostname and provide evidence of control
(registrar or DNS zone access). Never infer a hostname.

### 2.2 TLS and renewal

| Item | Status |
| --- | --- |
| TLS termination design | ✅ documented — `docs/release/phase8/REVERSE_PROXY_TLS_ACCEPTANCE.md` (proxy terminates TLS, app stays on `127.0.0.1`) |
| Acceptance harness | ✅ present — `docs/release/phase8/evidence/tls_proxy_harness.py` |
| Certificate issuance | ❌ no certificate exists |
| Renewal automation | ❌ not configured (no ACME client, no cron/timer) |

TLS is **designed and harness-tested, not deployed**. The proxy terminates TLS and
the app must bind loopback only (§2.3).

### 2.3 Reverse proxy and host-header allow-list

| Item | Status |
| --- | --- |
| Reverse-proxy pattern documented | ✅ `REVERSE_PROXY_TLS_ACCEPTANCE.md` |
| `ERP_TRUSTED_PROXIES` support (X-Forwarded-For, trusted-CIDR policy) | ✅ implemented in `dz_erp/net.py:130` |
| **Host-header allow-list (`ERP_ALLOWED_HOSTS`)** | ❌ **documented but NOT implemented** — see §3 |

### 2.4 Production database

| Item | Status |
| --- | --- |
| Engine | SQLite, single file (`ERP_DB_PATH`) |
| Schema creation | ✅ automatic — `_init_schema` runs `SCHEMA_SQL` then `_migrate_schema` at startup (`dz_erp/database.py:862–866`) |
| Migrations idempotent | ✅ `_migrate_schema` is additive and safe to re-run |
| Migration *before* traffic | ⚠️ happens implicitly on first connection — the operator must start the app once against a copy and verify, or migration occurs during live serving |
| Production DB file | ❌ does not exist |

**Deployment note (new in Phase 46):** the Phase 46 schema additions
(`ocr_field_reviews` table + index, `ocr_extracted_fields.conflict_values_json`
column) are applied by `_migrate_schema` and are additive — no data rewrite, no
column dropped. A pre-Phase-46 database upgrades in place on first open, which the
migration test (`test_migration_upgrades_legacy_database_preserving_rows`) exercises.

### 2.5 Secret management

| Item | Status |
| --- | --- |
| Backup key (`ERP_BACKUP_KEY` / key file) | ❌ no production key; implementation supports explicit key or file |
| Audit-signing key custody | ✅ implemented (`check_key_custody` in `dz_erp/readiness.py:197`) |
| Vault encryption keyring | ✅ implemented, versioned (`vault.vault.keys.json`) |
| Vault / secret manager integration | ❌ none (no Vault, no cloud KMS) |

**No secret value is printed in this document**, by design. Secrets must be
provisioned by the operator through the deployment platform's secret store.

### 2.6 Backup storage and restore

| Item | Status |
| --- | --- |
| Backup creation | ✅ `python -m dz_erp.ops_backup run` |
| Verification (shallow + deep) | ✅ `verify`, with manifest + SHA-256 |
| Retention / legal hold | ✅ implemented |
| Deep restore (full restore-and-compare) | ⚠️ **requires Python ≥ 3.11.17 or ≥ 3.12.10** — the safe-restore guard |
| Production backup target | ❌ no storage configured |
| Restore rehearsal on production hardware | ❌ NOT EXECUTED |

The guard is a deliberate security control (unsafe tar-member handling is patched
only in those versions). **It must not be relaxed to make a rehearsal pass.** The
production host must run a supported, patched Python — this is now a hard
deployment prerequisite, not a suggestion.

### 2.7 Monitoring and alerting

| Item | Status |
| --- | --- |
| Alerting module | ✅ `dz_erp/ops_alerts.py` |
| Checks module | ✅ `dz_erp/ops_checks.py` (disk, OCR, backup verify, readiness) |
| systemd units + timers | ✅ 14 files in `ops/` (backup, verify, disk-check, ocr-check, readiness) |
| logrotate config | ✅ `ops/logrotate/` |
| Alert delivery target (email/webhook) | ❌ not configured |
| Monitoring backend | ❌ none |

All the *plumbing* is implemented and unit-tested; the *destination* does not exist.

### 2.8 Application logs

| Item | Status |
| --- | --- |
| Structured JSONL security log | ✅ `dz_erp/security_audit.py` (`JsonlSecurityLog`) |
| Redaction | ✅ `StructuredRedactingLogger` |
| Correlation ids | ✅ `dz_erp/request_id.py` (SEC-15) |
| Log destination on a production host | ❌ none |

### 2.9 Health and readiness endpoints

| Endpoint | Status |
| --- | --- |
| `GET /healthz` — liveness | ✅ `dz_erp/api.py:423` |
| `GET /readyz` — readiness, returns **503** on critical failure | ✅ `dz_erp/api.py:427` |

`/readyz` is genuinely substantive — it checks the database (integrity + write
lock), vault writability, key custody, free disk, OCR subsystem, and backup
freshness (`dz_erp/readiness.py`: `check_database`, `check_vault`,
`check_key_custody`, `check_disk`, `check_ocr`, `check_backup`). A load balancer
can rely on it.

### 2.10 Rate limiting — all seven variables

All seven are implemented and documented in `docs/ENVIRONMENT_VARIABLES.md`
(verified count: 7).

| # | Variable | Default | Production guidance |
| --- | --- | --- | --- |
| 1 | `ERP_RATE_LIMIT_BACKEND` | `memory` | Use `sqlite` if >1 worker, else limits multiply per process |
| 2 | `ERP_RATE_LIMIT_PER_MINUTE` | `0` (off) | Set a global ceiling |
| 3 | `ERP_RATE_LIMIT_FAILURE_MODE` | `open` | Consider `closed` where login availability is not critical |
| 4 | `ERP_AUTH_FAILURE_LIMIT` | implementation default | Per `(client, account)` window |
| 5 | `ERP_AUTH_FAILURE_WINDOW_SECONDS` | implementation default | Window length |
| 6 | `ERP_AUTH_CLIENT_FAILURE_LIMIT` | implementation default | Per client address, across accounts |
| 7 | `ERP_AUTH_FAILURE_MAX_KEYS` | `4096` | Bounds memory against key-probing |

**Critical operational caveat (documented, not a defect):** `memory` backend bounds
**one process**. Behind multiple workers, effective limits multiply. Set
`ERP_RATE_LIMIT_BACKEND=sqlite` for multi-worker deployments.

Verify the running policy at runtime with `GET /api/v1/system/rate-limit-policy`
(admin only) — it reports what the process is actually doing, not what a config
file claims.

### 2.11 Operational access controls

| Item | Status |
| --- | --- |
| Application RBAC | ✅ roles enforced (`_authenticate`, `REVIEW_CAPABLE_ROLES`, segregation of duties) |
| Operator shell access | ❌ not defined (no deployment, no accounts) |
| Least-privilege service account | ❌ not defined |
| Runbook ownership | ❌ no named operator |

---

## 3. Security finding recorded during preparation

**`ERP_ALLOWED_HOSTS` is documented as enforced but is not implemented.**

Multiple release documents assert host-header validation:

- `docs/release/phase9/DEPLOYMENT_READINESS_REPORT.md:29` — "Host restriction |
  `ERP_ALLOWED_HOSTS` enforced, injection → `400`"
- `docs/release/phase8/REVERSE_PROXY_TLS_ACCEPTANCE.md:66` — "Host validation |
  **app** (`ERP_ALLOWED_HOSTS`)"
- `docs/release/phase10/PUBLIC_TLS_ACCEPTANCE.md:78` — "Host allow-listing
  (`ERP_ALLOWED_HOSTS`, default deny of unknown hosts) | `dz_erp/api.py`
  (trusted-host middleware)"

**Verified reality:**

```
$ grep -rn "ERP_ALLOWED_HOSTS" --include=*.py dz_erp/ tools/ ops/
(no matches)

$ grep -n "add_middleware\|@app.middleware" dz_erp/api.py
281:    @app.middleware("http")   # _security_headers
315:    @app.middleware("http")   # _request_id_and_throttle
```

There is **no** trusted-host middleware and **no** `ERP_ALLOWED_HOSTS` consumer.
The Phase 46 mission's G6 checklist requires "host-header allowlist" — it does not
exist in code, only in documentation.

**Impact:** Host-header injection (cache poisoning, password-reset link poisoning,
vhost confusion) is **not** mitigated by the application. Because the app binds
`127.0.0.1` behind a reverse proxy that should reject unknown `Host` values, the
practical exposure depends entirely on proxy configuration — but the documented
defence-in-depth layer is absent.

**Severity: Medium-High.** Recorded, not fixed in Phase 46. Implementing host
validation is a security-control *addition*; doing it inside a release-gate phase
would also change the request path immediately before a merge, and the documented
acceptance evidence (`tls_proxy_harness.py` check 10) would need re-running against
a real proxy that does not exist here. It is recorded as the first owner action and
as a **pre-production blocker** for G6.

---

## 4. Exact build / migration / deploy commands

Prepared but **NOT EXECUTED** — there is no target. All paths are placeholders to
be filled by the operator; none is a real credential or host.

### 4.1 Build

```bash
# On the target host, supported Python (>=3.11.17 or >=3.12.10, <3.13)
git fetch --tags origin
git checkout <MERGE_SHA>                 # the exact merge commit being released
python -m venv /opt/dz-erp/venv
/opt/dz-erp/venv/bin/pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
/opt/dz-erp/venv/bin/python tools/verify_dependency_graph.py
/opt/dz-erp/venv/bin/python tools/generate_hash_lock.py --check
```

### 4.2 Pre-deploy backup

```bash
# Mandatory before any migration. Abort the deployment if this fails.
/opt/dz-erp/venv/bin/python -m dz_erp.ops_backup run --backup-dir <BACKUP_DIR>
/opt/dz-erp/venv/bin/python -m dz_erp.ops_backup verify --backup-dir <BACKUP_DIR> --deep
/opt/dz-erp/venv/bin/python -m dz_erp.ops_backup status --backup-dir <BACKUP_DIR>
```

`verify --deep` performs a full restore-and-compare and therefore **requires the
supported Python version** (§2.6).

### 4.3 Migration

```bash
# Migrations are applied inside the app at first connection (_init_schema ->
# _migrate_schema). Run once against a COPY first and confirm it upgrades cleanly:
cp <PROD_DB> /tmp/pre-migration-check.sqlite3
ERP_DB_PATH=/tmp/pre-migration-check.sqlite3 /opt/dz-erp/venv/bin/python -c \
  "from dz_erp.database import ERPDatabase; ERPDatabase('/tmp/pre-migration-check.sqlite3')"
# Inspect: expected new objects include ocr_field_reviews, idx_ocr_field_reviews_doc,
# and ocr_extracted_fields.conflict_values_json
```

### 4.4 Start

```bash
# Bind loopback ONLY; the reverse proxy is the sole public listener.
ERP_DB_PATH=<PROD_DB> \
ERP_VAULT_PATH=<VAULT_DIR> \
ERP_BACKUP_KEY_FILE=<KEY_FILE> \
ERP_RATE_LIMIT_BACKEND=sqlite \
ERP_RATE_LIMIT_PER_MINUTE=<N> \
ERP_TRUSTED_PROXIES=<PROXY_CIDR> \
/opt/dz-erp/venv/bin/python -m dz_erp.serve --host 127.0.0.1 --port 8000
```

### 4.5 Health checks

```bash
curl -fsS http://127.0.0.1:8000/healthz            # expect {"status":"ok",...}
curl -fsS http://127.0.0.1:8000/readyz             # expect 200; 503 = do not route
curl -fsS -H "Authorization: Bearer <ADMIN>" \
     http://127.0.0.1:8000/api/v1/system/rate-limit-policy
```

### 4.6 Smoke tests

`tools/staging_acceptance.py` is the executable smoke suite (38/40 locally; the 2
gaps are the restore guard, which passes on a supported Python). Run it against the
new deployment's state directory before declaring success.

### 4.7 Rollback

```bash
# Stop, restore the pre-deploy backup, restart the previous release.
systemctl stop dz-erp
/opt/dz-erp/venv/bin/python -m dz_erp.ops_backup restore \
    --backup-dir <BACKUP_DIR> --file <PRE_DEPLOY_ARTEFACT>
systemctl start dz-erp
curl -fsS http://127.0.0.1:8000/readyz
```

**Destructive rollback against live production requires explicit confirmation** and
is not authorized by this document.

### 4.8 Responsible operator / permissions

| Role | Responsibility | Named |
| --- | --- | --- |
| Release owner | approves the merge SHA and deployment window | ❌ none |
| Operator | executes build/migrate/deploy/rollback | ❌ none |
| DBA / data owner | verifies backup and migration on a copy | ❌ none |
| Security owner | confirms TLS, proxy, host allow-list, RBAC | ❌ none |

---

## 5. Gate decision

> **G6: BLOCKED.**

Nothing in this document is a deployment. No host, credential, certificate, or
secret was invented, and no service was contacted.

### Owner actions, in order

1. **Implement host-header validation** (§3) — pre-production blocker. Add a
   trusted-host middleware honouring `ERP_ALLOWED_HOSTS`, re-run
   `tls_proxy_harness.py`, and correct the three documents that claim it exists.
2. Provision a host running a **supported Python** (≥3.11.17 / ≥3.12.10, <3.13).
3. Designate the production hostname and provide DNS/TLS control evidence.
4. Configure reverse proxy + ACME renewal; keep the app on `127.0.0.1`.
5. Provision the database, vault, backup key and backup storage via the platform's
   secret store (never in the repository).
6. Configure the 7 rate-limit variables; use `sqlite` if >1 worker.
7. Point alerting at a real destination; validate the systemd timers.
8. Name the four responsible roles in §4.8.
9. Rehearse backup → migration → start → readiness → smoke → rollback on staging.
10. Re-issue G6 with the staging evidence.

### What distinguishes these three things

| Dimension | Status |
| --- | --- |
| **Technical readiness** | Largely ready — migration, readiness, backup, alerting, rate limiting all implemented and tested; host validation missing |
| **Statutory approval (G5)** | BLOCKED — no expert sign-off; see `ALGERIAN_EXPERT_APPROVAL_REGISTER.md` |
| **Operational production readiness (G6)** | BLOCKED — no target, no host, no TLS, no secrets, no operator |
