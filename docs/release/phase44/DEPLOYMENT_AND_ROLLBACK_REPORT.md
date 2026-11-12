# Phase 44 — Deployment and rollback report

## Status: **BLOCKED — no deployment target exists**

Nothing was deployed. This document records what exists, what is missing, and the exact procedure an
operator can run once the prerequisites are supplied. It contains **no** deployment evidence, because
no deployment occurred — fabricating a green deployment is precisely what the mission forbids.

## 1. What exists

| Component | Path | State |
| --- | --- | --- |
| ASGI application | `dz_erp/api.py` → `create_erp_fastapi_app` | integrated, 946 tests green |
| Server entry point | `dz_erp/serve.py` (`uvicorn dz_erp.serve:app`, `main()`) | present |
| systemd services | `ops/dz-erp-{backup,backup-verify,checkpoint-export,checkpoint-verify,disk-check,ocr-check,readiness}.service` | 7 units |
| systemd timers | matching `.timer` for each | 7 timers |
| Log rotation | `ops/logrotate/` | present |
| Readiness probe | `GET /readyz` (DB integrity + write lock, vault writability, free disk, OCR) | present and preserved |
| Health probe | `GET /healthz` (liveness) | present |
| Backup/restore tooling | `dz_erp/ops_backup.py`, `dz_erp/ops_checks.py`, `dz_erp/readiness.py` | present, tested |
| Backup automation tests | `tests/test_phase62_backup_automation.py` (17), `test_phase62_readiness.py` | passing |

So the **packaging** is complete. What is absent is somewhere to put it.

## 2. What is missing (blocking)

| # | Prerequisite | Status | Consequence |
| --- | --- | --- | --- |
| 1 | Target host and SSH/agent access | **absent** | cannot install |
| 2 | TLS certificate or ACME account | **absent** | cannot expose the API; `ERP_HOST` must stay `127.0.0.1` without it |
| 3 | Domain and DNS record | **absent** | no reachable name |
| 4 | Secret manager holding `ERP_AUDIT_HMAC_SECRET` and `ERP_VAULT_KEY` | **absent** | audit chain and vault keys must not be generated ad hoc on a production host |
| 5 | Artefact registry for a release build | **absent** | nothing to promote |
| 6 | Monitoring/alerting endpoint | **absent** | a deployment with no alert path is not operable |
| 7 | Qualified professional's statutory sign-off (G5) | **absent** | see Phase 42 |

Deploying without these would mean choosing an unintended environment, which the mission explicitly
forbids ("do not deploy to an unintended environment", "do not fabricate … credentials, deployment
evidence").

## 3. Deployment procedure (not executed)

Prepared so it is reviewable and runnable later. **Not run.**

```bash
# 0. Preconditions — all must be true
#    - Phase 42 G2 and G5 closed
#    - secrets retrievable from the secret manager
#    - TLS certificate issued for the target name

# 1. Build a clean interpreter at the supported version
bash docs/release/phase35/toolchain/build_env.sh 3.12.10
/tmp/py312/bin/python3 -m venv /opt/erp/venv
/opt/erp/venv/bin/pip install -r requirements.lock.hashes.txt   # currently fails, see H-1

# 2. Stage the application
install -d /opt/erp/app /var/lib/erp /var/log/erp
tar -C /opt/erp/app -xf erp-release.tar.gz

# 3. Secrets (from the secret manager, never from argv or a repo file)
export ERP_AUDIT_HMAC_SECRET="$(secret-get erp/audit-hmac-v1)"
export ERP_VAULT_KEY="$(secret-get erp/vault-key-v1)"

# 4. Non-secret configuration
export ERP_DB_PATH=/var/lib/erp/erp.sqlite3
export ERP_VAULT_PATH=/var/lib/erp/vault
export ERP_BACKUP_DIR=/var/lib/erp/backups
export ERP_SECURITY_LOG_PATH=/var/log/erp/security.jsonl
export ERP_HOST=127.0.0.1 ERP_PORT=8000          # behind the TLS proxy
export ERP_TRUSTED_PROXIES=<proxy CIDR>
export ERP_RATE_LIMIT_BACKEND=sqlite             # multi-worker: share counters
# Do NOT set ERP_DOCS_ENABLED.

# 5. First-run bootstrap (creates company, SCF chart, admin) — interactive, once
/opt/erp/venv/bin/python -m dz_erp.serve bootstrap

# 6. systemd
install -m 0644 ops/*.service ops/*.timer /etc/systemd/system/
install -m 0644 ops/logrotate/* /etc/logrotate.d/
systemctl daemon-reload
systemctl enable --now dz-erp-serve dz-erp-backup.timer dz-erp-backup-verify.timer \
                            dz-erp-disk-check.timer dz-erp-ocr-check.timer \
                            dz-erp-readiness.timer

# 7. Verify (the only step that produces deployment evidence)
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz      # must be 200 and status "ok"
test -s /var/log/erp/security.jsonl          # must be non-empty after one failed auth
```

## 4. Rollback strategy

Two independent layers, both exercised by tests — but **not on production**.

### Layer 1 — application rollback (fast, always available)

The application is stateless apart from the database, vault and logs, so reverting is a service
restart against the previous release directory:

```bash
systemctl stop dz-erp-serve
mv /opt/erp/app /opt/erp/app.failed
mv /opt/erp/app.previous /opt/erp/app
systemctl start dz-erp-serve
curl -fsS http://127.0.0.1:8000/readyz
```

No schema migration was added by Phase 35 — the integrated code adds **no** DDL — so a downgrade
does not need a schema reversal.

### Layer 2 — data restore (from backup)

```bash
systemctl stop dz-erp-serve dz-erp-backup.timer
dz-erp-backup-verify --restore <backup-id> --target /var/lib/erp-restore
# verify the restored database opens, integrity_check is ok, and the audit chain verifies
systemctl start dz-erp-serve
```

The restore path is guarded (see §5) and **must never be run against a live production database**
without an explicit operator confirmation and a verified backup of the *current* state first. The
mission forbids destructive rollback against production without that confirmation, and none was
sought because no production host exists.

## 5. Restore safety — already proven by the suite

The rollback path is the most dangerous operation in the system, so its guarantees are covered by
tests rather than by documentation alone:

| Guarantee | Test |
| --- | --- |
| Restores into an **isolated** environment, never in place | `test_backup_restores_mixed_ciphertext_into_an_isolated_environment` |
| A tampered registry **fails closed** | `test_restore_of_a_tampered_registry_fails_closed` |
| Ciphertext needing an absent key is refused | `test_restore_rejects_ciphertext_that_needs_an_absent_key` |
| Path traversal in an archive is refused before any write | `test_restore_rejects_noncanonical_tar_paths_before_writing[...]` |
| Pre-existing hardlink targets are refused | `test_restore_refuses_preexisting_hardlink_target_before_writing` |
| Repeated restores are idempotent | `test_repeated_restores_are_idempotent_and_leave_a_usable_deployment` |
| Unsafe archive member types are rejected | `restore_runtime_is_safe` guard |

All pass. Note the interpreter guard: the restore path refuses to run on Python **3.11.2** because the
hardened `tarfile.data_filter` semantics it relies on are not present there. That refusal is correct
and was **not** relaxed to make an older interpreter pass — it is the reason the acceptance suite was
re-built on 3.12.10.

## 6. Why this report is not a deployment record

| Claim | Made? | Basis |
| --- | --- | --- |
| "Deployed to staging" | **no** | no target exists |
| "Deployed to production" | **no** | no target, G2 and G5 open |
| "Rollback rehearsed" | **no** | tests exercise restore logic; no host was rolled back |
| "TLS configured" | **no** | no certificate |
| "Monitoring live" | **no** | no endpoint |

What can be asserted: the code is integration-complete, the full suite is green on the supported
interpreter, restore safety is test-proven, and the deployment procedure above is complete enough to
execute as soon as prerequisites 1–7 exist.
