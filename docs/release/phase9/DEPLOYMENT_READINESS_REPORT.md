# Phase 9I — Deployment & Infrastructure Readiness

**Baseline:** PR #7 head `f603e38` · **No production deployment was performed.**

## 1. V1 architecture that this report preserves (not renegotiated)

| Element | V1 decision | Preserved |
| --- | --- | --- |
| Document storage | **Local File Vault** on the host filesystem | yes |
| Key custody | key file `0600`, key directory `0700`, host-local | yes |
| Deployment form | **source tree** + `python -m dz_erp.serve` | yes |
| Configuration | environment variables | yes |
| Interpreter | **CPython 3.12.10** | yes |
| KMS/HSM, MinIO/S3 | **out of V1 scope** — not blockers | yes |

No step below introduces KMS/HSM or object storage as a mandatory component.

## 2. Verified by execution

| Item | Evidence |
| --- | --- |
| Source-tree start | `python -m dz_erp.serve` bootstrapped a fresh DB and served traffic (TLS harness `:8099`, perf probe `:8097`) |
| Locked dependencies install | `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` → **exit 0** |
| Dependency integrity tests | `test_phase19_dependency_integrity` (26 tests, passed) |
| Deployment asset tests | `test_phase37_deployment_assets` (6 tests, passed) |
| Upload boundary in CI | `test_phase8_upload_boundary_and_ci` (82 tests, passed) |
| Vault permissions | key file `0600`, key dir `0700` — asserted by the custody suites (`test_phase15_key_custody_and_monitoring`) |
| HTTPS through a proxy | 11 PASS, 1 NOT EXECUTED, 1 OBSERVED (Phase 8C) |
| Host restriction | `ERP_ALLOWED_HOSTS` enforced, injection → `400` |
| Proxy trust | `ERP_TRUSTED_PROXIES` explicit, empty = fail closed |
| No secrets in repository or docs | all examples use placeholders; no key material in `docs/release/` |

## 3. Reference deployment (V1, single host)

```
[ internet ]
     |
  nginx/caddy  :443  (TLS termination, HSTS, HTTP→HTTPS 301)
     |  127.0.0.1:8000 only
  python -m dz_erp.serve --host 127.0.0.1 --port 8000
     |
  ERP_DB_PATH=/srv/erp/data/erp.sqlite3
  ERP_VAULT_PATH=/srv/erp/vault          (0700, key file 0600)
```

Required environment:

```
ERP_ALLOWED_HOSTS=erp.example.dz
ERP_TRUSTED_PROXIES=127.0.0.1
ERP_DB_PATH=/srv/erp/data/erp.sqlite3
ERP_VAULT_PATH=/srv/erp/vault
ERP_MAX_REQUEST_BYTES=10485760
ERP_RATE_LIMIT_PER_MINUTE=120
ERP_RATE_LIMIT_FAILURE_MODE=fail_closed
```

Hard rules the application cannot enforce for you:

1. **Never publish the backend port.** It answers plain HTTP; exposing it bypasses TLS.
2. **Bind the process to loopback**, let the proxy face the network.
3. **Do not run as root.** A dedicated user owning `/srv/erp` with `0700` on the vault.
4. **Back up with the product's encrypted backup**, then copy **off-host**.

## 4. Secrets handling

- No credential, key, or token is written into any document under `docs/release/`.
- Configuration is environment-only; there is no config file containing secrets.
- The application logs authentication outcomes without credentials
  (`test_credentials_never_reach_the_security_log`).

## 5. Gaps that remain before a real deployment

| Gap | Impact |
| --- | --- |
| No readiness probe (database/vault/disk) | orchestration cannot tell "working" from "up" |
| No backup scheduler / retention in the product | operator must cron it; RPO depends on that cron |
| No log rotation | stderr grows until the host fills |
| No systemd unit shipped (documented, not generated here) | must be written per host |
| Desktop client unverified | ship API-first, or block |
| Single-host SQLite + Local Vault | no horizontal scaling by design; acceptable for V1 scope |

## 6. Verdict

**Deployable to a single host by an operator following §3, with the five gaps above
closed operationally.** No production deployment was performed, and no claim is made
that one was.
