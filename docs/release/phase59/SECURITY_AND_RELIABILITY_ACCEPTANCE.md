# Phase 59E — Security and Operational Reliability Acceptance (revalidation)

**Repository:** `https://github.com/Devmraustro/ERP.git` · **Branch:** `arena/63d762c8-erp` ·
**Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`
**Environment:** isolated `/tmp/acc2` over loopback HTTP, synthetic data, CPython 3.11.2.
**Status:** every row below was **executed against a running server** or against the test suite named.

---

## 1. Result table

| Control | Verdict | Evidence |
|---|---|---|
| Authentication (bearer token) | **PASS** | `POST /api/v1/auth/token` → `200` + `dzsess_…` token |
| Identity derived server-side | **PASS** | `/api/v1/auth/session` returns `user_id`, `company_id`, `role` from the token; no `X-User-Id` header exists (`test_openapi_contract_declares_no_identity_headers_as_parameters`) |
| Anonymous access refused | **PASS** | `GET /api/v1/invoices` → `401`; `GET /system/rate-limit-policy` → `401` |
| Session revocation | **PASS** | `POST /api/v1/auth/revoke` → `200 {'revoked': True}`; the same token then → `401 Authentication credential has been revoked.` |
| Password policy | **PASS** | 7-char password → `422 Password must be at least 8 characters long.`; floor configurable via `ERP_PASSWORD_MIN_LENGTH`, floored at 8, recommended 12 |
| Login lockout | **PASS (with an operational caveat, §3)** | 5 failures → `AUTH_ACCOUNT_LOCKED`, 15-minute lock; further attempts audited as `AUTH_LOGIN_REJECTED_ACCOUNT_LOCKED` |
| RBAC on administration | **PASS** | non-admin `POST /api/v1/users` → `403 User administration is restricted to administrators.` |
| RBAC on posting | **PASS** | `approver` posts → `403 Role 'approver' is not authorized; required one of ('accountant', 'admin').` |
| Segregation of duties | **PASS** | creator approves own invoice → `403 SEGREGATION_OF_DUTIES_VIOLATION` |
| Host-header allow-list | **PASS** | unknown `Host` → `400`; `ERP_ALLOWED_HOSTS` permits the named host; missing/empty/comma/whitespace/multi-byte → refused |
| Unsafe bind refused at start-up | **PASS** | `--host 0.0.0.0` with default allow-list → `SystemExit`, actionable message (§2) |
| Trusted proxy | **PASS** | `trusted_proxies=None`, `uvicorn` runs with `proxy_headers=False`; no forwarded header is trusted by default |
| API docs disabled | **PASS** | `/docs` → `404`, `/openapi.json` → `404` (default `ERP_DOCS_ENABLED=false`) |
| Secret in logs | **PASS** | issued token and the account password are **absent** from the server log (checked by substring against a live token) |
| Safe error messages | **PASS** | every refusal returned a named reason or a structured `422`; no stack trace, no `500` |
| Upload validation / hostile documents | **PASS — suite** | `tests/test_phase64_dependency_security_regression.py` (41 tests: decompression bombs, dimension bombs, cyclic PDFs, truncated multipart, wrong magic bytes) |
| Path traversal on restore | **BLOCKED here** (Python floor) | `test_restore_rejects_noncanonical_tar_paths_before_writing[...]` — 3 cases; guard is present and fails closed |
| SQL injection | **PASS — suite** | every query is parameterised; `tests/test_phase34_security_validation.py` |
| Rate limiting | **PASS — suite (105 tests)** | `test_phase35_api_rate_limiting_and_request_logging.py`, `test_phase39_bounded_rate_limiting.py` |
| Vault key/dir permissions | **PASS** | `/readyz` → `key_mode 0o600`, `dir_mode 0o755` |
| Backup artefact permissions | **PASS** | archive, manifest and status file all `-rw-------` |

Test suites executed for this section: **105 passed** (phase 34/35/39/9) + **101 passed, 2 skipped**
(phase 7/15/17) + **41 passed** (phase 64). Skips are documented Windows-only paths
(`test_phase17_key_providers.py:97, 111`), not silent gaps.

## 2. The start-up refusal, quoted

```
$ ERP_DB_PATH=… ERP_VAULT_PATH=… python -m dz_erp.serve --host 0.0.0.0 --port 8097
Refusing to listen on '0.0.0.0' with no LAN hostnames configured.
The default Host-header allow-list only covers loopback (localhost, 127.0.0.1, [::1], testserver), so
every request from another machine would be rejected with HTTP 400 while the server reports itself
healthy.

Set ERP_ALLOWED_HOSTS to every name and address users will type in their browser, e.g.
  ERP_ALLOWED_HOSTS=erp.example.dz,192.168.1.10
or bind loopback only (--host 127.0.0.1) for a single-workstation deployment.
```

This is exactly the class of defect the previous phase found (a LAN deployment answering `400` to
every colleague while `/healthz` reported `200`). The process now refuses to start rather than
serving a broken surface.

## 3. Operational caveat that must reach the runbook

**Lockout is per account, not per source.** Seven wrong-password attempts from one client locked the
`admin` account for 15 minutes, and the **correct** password was then refused too:

```
401 401 401 401 401 401 401
after 20 s (correct password): 401
audit_events: AUTH_LOGIN_FAILED (4), AUTH_ACCOUNT_LOCKED (1), AUTH_LOGIN_REJECTED_ACCOUNT_LOCKED (4)
```

* This is deliberate and correct for credential-stuffing defence (`MAX_FAILED_LOGINS = 5`,
  `LOGIN_LOCKOUT_SECONDS = 15 * 60`), and it is **fully audited**.
* It is also a denial-of-service vector: anyone who can reach `/api/v1/auth/token` can lock the only
  admin out for 15 minutes per five attempts. Rate limiting reduces, but does not remove, that.
* **Required operator mitigation:** a second administrative account with its own credential, so a
  lockout of one does not lock the deployment. Documented in `docs/admin/ADMINISTRATION_AND_RECOVERY.md`
  and both user guides, together with the wait-out period and the audited `AUTH_ACCOUNT_LOCKED` event
  that tells an administrator this is what happened.
* No code was changed for this: the behaviour is a deliberate security control and the acceptance
  criterion ("critical actions must never silently fail") is met — the failure is loud, named and
  audited. The *procedure* was missing, and that is now written.

## 4. Things this section does **not** claim

| Not claimed | Why |
|---|---|
| TLS / HTTPS verified | No certificate or proxy in this environment; see `REVERSE_PROXY_TLS_ACCEPTANCE.md` |
| Penetration test | None performed. This is a control-by-control revalidation, not an adversary simulation |
| Windows ACL equivalence for `0o600` | POSIX mode bits were observed; NTFS ACLs were not (§59A) |
| That any single control is sufficient | They are layered; the acceptance argument is the combination, plus the audit trail |

## 5. Commands used

```sh
# lockout
for i in $(seq 7); do curl -s -o /dev/null -w "%{http_code} " \
  -X POST http://127.0.0.1:8098/api/v1/auth/token \
  -H 'Content-Type: application/json' -d '{"username":"admin","password":"wrong-password"}'; done

# revocation
POST /api/v1/auth/revoke  {"token": "…"}      → 200 {'revoked': True}
GET  /api/v1/auth/session (same token)        → 401 Authentication credential has been revoked.

# docs surface
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8098/docs          # 404
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8098/openapi.json  # 404

# unsafe bind
ERP_DB_PATH=… ERP_VAULT_PATH=… python -m dz_erp.serve --host 0.0.0.0       # SystemExit, §2
```
