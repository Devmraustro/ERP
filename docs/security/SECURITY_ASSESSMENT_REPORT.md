# Security Assessment Report — ERP Algeria (Independent Validation)

| Field | Value |
| :--- | :--- |
| Assessment type | Independent, evidence-based security validation of an existing build |
| Date of assessment | 2026-11-09 |
| Repository | `Devmraustro/ERP` (checkout `/home/user/ERP`) |
| Branch assessed | `arena/7557bcfb-erp` |
| Initial HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` (identical to `origin/main`) |
| Final HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` (no commits created; see §11) |
| Working tree at start | Clean (`git status --porcelain` empty) |
| Python runtime | 3.11.2 (project declares `requires-python >= 3.11.17`) |
| Assessment method | Source review + isolated-execution dynamic testing on disposable SQLite/vault fixtures |
| Final classification | **SECURITY VALIDATION PASSED WITH RESIDUAL RISKS** |
| Project deployment status | **STAGING VERIFIED — unchanged** (staging could not be independently re-verified here, §2) |

> This report does not claim OWASP certification. Requirements are *mapped* to
> OWASP ASVS 4.0.3 and OWASP API Security Top 10 (2023) categories as an
> assessment structure only.

---

## 1. Scope

In scope: the Python service in this checkout — `dz_erp/api.py`,
`dz_erp/auth.py`, `dz_erp/security_audit.py`, `dz_erp/procurement_stock.py`,
`dz_erp/intake_ocr.py`, `dz_erp/database.py`, `dz_erp/upload.py`,
`dz_erp/net.py`, `dz_erp/key_provider.py`, `dz_erp/key_rotation.py`,
`dz_erp/decimal_utils.py`, `dz_erp/serve.py`, the dependency set in
`pyproject.toml` / `requirements.lock.txt`, and the test suite in `tests/`.

Out of scope by instruction: accounting/tax formulas (no security defect was
found that required touching them), compliance documents and legal
interpretations, the unmerged pull-request branches, and any production system.

## 2. Preservation and environment verification (performed first)

The task brief named three preservation anchors. **All three differ from this
workspace**, so they were investigated and recorded rather than forced:

| Anchor required by the brief | Observed in this workspace | Action |
| :--- | :--- | :--- |
| Branch `round7-dep-remediation` | Branch does not exist locally or on `origin` (`git branch -a`); session branch is `arena/7557bcfb-erp` | Worked exclusively on the session branch as required; did not create, rename or switch branches |
| HEAD `5a3e47ddfba2b789bcff71d6438db4927f5c7986` | That SHA is the head of `arena/7f12d627-erp` (PR #7), **not** of this branch. This branch is at `4fc1364a…` === `origin/main` | Did **not** reset/checkout/merge anything. Assessed the code actually present |
| "10 existing user modifications" | Working tree was clean at start; 0 user modifications present | Preserved trivially; nothing was discarded, stashed or cleaned |
| PR #7 open and unmerged | **Confirmed**: `gh pr view 7` → `state=OPEN`, `isDraft=true`, `headRefOid=5a3e47dd…`, base `fix/phase29-invoice-discount-and-line-net-consistency` | Not merged, not closed, not pushed to, not retargeted |

Forbidden operations were not performed: no `git reset --hard`, `git clean`,
`git stash`, no history rewrite, no force push, no branch deletion, no database
or volume deletion, no `docker compose` command, no service stop.

Environment findings:

| Item | Evidence | Consequence |
| :--- | :--- | :--- |
| Docker | `docker ps` → `bash: docker: command not found` | No container inspection possible |
| Staging service `127.0.0.1:18002` / container `ops-erp-1` / port 8000 / 18003 | `ss -ltnp` shows only `:111`, `:22`, `:49983`; `ps aux` shows no uvicorn/minio process | **The staging stack described in the brief does not exist in this sandbox** |
| Health endpoint | `GET /healthz` exercised against an in-process app built from this checkout (200) | Liveness logic verified; the *deployed* staging health check is NOT VERIFIED (§2.1) |
| Database / storage paths | No `ERP_DB_PATH`, no `/var/lib/erp`, no `ops_erp-state-staging` volume | No live data existed to endanger |
| Data classification | All databases/vaults created during this assessment were **synthetic**, under `/tmp/probe/**` and pytest `tmp_path` | No real or production data touched |

### 2.1 Isolation statement

Because no staging host, container, volume or database was reachable, every
state-changing test ran against a freshly created, disposable SQLite database
plus a disposable vault directory (`/tmp/probe/state*/`, pytest `tmp_path`).
This satisfies the isolation requirement, but it means **no test in this report
was executed against the described staging deployment**. Items that depend on
it are marked NOT EXECUTED rather than PASS.

## 3. Methodology

1. Static review of every request-handling module, the RBAC matrix
   (`docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md`), the key-custody design, and
   the dependency pins; cross-checked claims against implementation.
2. Dynamic probing from an in-process ASGI client against synthetic tenants: two
   companies, seven roles, one document, one invoice, synthetic PDFs.
3. Each suspected weakness was reproduced **before** any code change; the
   reproduction is recorded in `SECURITY_TEST_EVIDENCE.md`.
4. Minimal remediation at the narrowest chokepoint that closes the defect;
   regression tests added in `tests/test_phase34_security_validation.py`.
5. Re-run of the exact probe, then the whole suite, then static checks.
6. Dependency scanning with `pip-audit 2.10.1` (read-only, as scoped by the
   brief) plus the repository's own hash-lock and dependency-graph verifiers.

## 4. Workstreams executed

| # | Workstream | Executed | Summary |
| :--- | :--- | :--- | :--- |
| A | Authentication & session management | Yes | Solid; no defect found (see §5) |
| B | RBAC & segregation of duties | Yes | **One Medium defect (SEC-01)**, fixed |
| C | Multi-tenant isolation | Yes | No defect; cross-tenant attempts refused and audited |
| D | API security (authorization, BOLA, mass assignment, error handling) | Yes | **SEC-03/04/05/06**, fixed; identity-header spoofing refused |
| E | SQL injection & input validation | Yes | No injection path; **SEC-02/06** data-integrity defects fixed |
| F | File upload & OCR security | Yes | Boundary holds; no defect beyond RBAC (SEC-01) |
| G | Local file vault & storage security | Yes | Traversal refused; key files 0600; **SEC-07** backup mode fixed |
| H | Audit log integrity | Yes | Triggers + HMAC chain verified; fail-closed on lost key |
| I | Encryption & key management | Yes | Fail-closed provider contract verified; KMS live NOT VERIFIED |
| J | Backup & DR security | Partially | Restore path NOT EXECUTED (interpreter floor); backup creation verified |
| K | HTTP security configuration | Yes | **SEC-08/10** fixed; docs disabled; no CORS; residual proxy/TLS items |
| L | Dependency & secret scanning | Yes | No secrets; **SEC-11** vulnerable pins documented (read-only, not upgraded) |

## 5. Findings by severity

Full detail, reproduction and retest evidence: `SECURITY_FINDINGS_REGISTER.md`.
Remediation diffs and rationale: `SECURITY_REMEDIATION_LOG.md`.

| ID | Severity | Component | Title | Status |
| :--- | :--- | :--- | :--- | :--- |
| SEC-01 | Medium | `dz_erp/api.py` document endpoints | Document upload and OCR field correction ignored the documented RBAC matrix | **Fixed** |
| SEC-02 | Medium | `dz_erp/decimal_utils.py` | Non-finite/oversized decimal input stored as `'NaN'` in a money column or raised HTTP 500 | **Fixed** |
| SEC-11 | Medium | `requirements.lock.txt` | Known-vulnerable pinned dependencies reachable from untrusted input | **Open — documented blocker** |
| SEC-03 | Low | `dz_erp/api.py:GET /api/v1/invoices` | Out-of-range `limit` answered 500 instead of 422 | **Fixed** |
| SEC-04 | Low | `dz_erp/procurement_stock.py` | Unsupported `payment_method` surfaced as `sqlite3.IntegrityError` (500) | **Fixed** |
| SEC-05 | Low | `dz_erp/procurement_stock.py` | Optional `line_ht_dzd` omitted by the caller raised `TypeError` (500) | **Fixed** |
| SEC-06 | Low | `dz_erp/procurement_stock.py` | `invoice_date` never validated; empty/oversized supplier reference accepted; internal parser text echoed | **Fixed** |
| SEC-07 | Low | `dz_erp/security_audit.py` | Backup archive (carries key rings) written world-readable `0644` | **Fixed** |
| SEC-08 | Low | `dz_erp/api.py` | Unbounded JSON request bodies, including on the unauthenticated token endpoint | **Fixed** (Content-Length cap; chunked residual) |
| SEC-09 | Low | `dz_erp/api.py` | Username accepted control characters | **Fixed** |
| SEC-10 | Informational | `dz_erp/api.py` | No browser-facing hardening headers | **Fixed** |
| SEC-12 | Low | deployment | No in-application rate limiting | Open — accepted residual (documented) |
| SEC-13 | Low | `dz_erp/api.py` user creation | Password policy is length-only; `password`/`12345678` accepted | Open — policy decision |
| SEC-14 | Low | `dz_erp/api.py` field correction | `new_value` is stored without type/length validation | Open — deferred to unmerged review-binding work |
| SEC-15 | Informational | deployment | No app-level Host validation or TLS; requires proxy termination | Open — deployment requirement |

Totals: 0 Critical, 0 High, 3 Medium (2 fixed, 1 open with blocker), 9 Low
(6 fixed, 3 accepted/deferred), 2 Informational (1 fixed, 1 deployment).

## 6. What was verified as holding

These were tested, not assumed; evidence in `SECURITY_TEST_EVIDENCE.md`.

* **Credential handling** — 401 for missing/malformed/unknown/revoked/expired
  tokens, wrong password, unknown user, inactive user; identical body for
  "unknown user" and "wrong password"; opaque 50-char `dzsess_` tokens with only
  SHA-256 digests persisted; PBKDF2-HMAC-SHA256 at 600,000 iterations; constant-time
  comparison; `WWW-Authenticate: Bearer` on every refusal.
* **Brute force** — account locks on the 5th consecutive failure, correct
  password then refused for the lockout window, attempts audited
  (`AUTH_LOGIN_FAILED`, `AUTH_ACCOUNT_LOCKED`, `AUTH_LOGIN_REJECTED_ACCOUNT_LOCKED`).
* **Session lifecycle** — logout revokes immediately; reuse after revocation is
  401; expiry enforced server-side; deactivation kills live tokens.
* **Identity spoofing** — `X-User-Id` / `X-Company-Id` / `X-Role` cannot
  authenticate (401 with no token) and cannot raise privilege (403 for a
  warehouse token presenting admin headers).
* **Tenant isolation** — company-2 admin reading company-1 invoice, document
  fields, document content and document file receives 403, and the attempt is
  written to the *target* tenant's audit chain
  (`SECURITY_CROSS_COMPANY_READ_BLOCKED`, `SECURITY_CROSS_COMPANY_FILE_ACCESS_BLOCKED`).
  Approve/submit/post against a foreign invoice id → 403 "not found in target
  company". A body or query `company_id` has no effect.
* **Segregation of duties** — creator cannot approve own invoice; clerk cannot
  post/approve/reverse; approver cannot post; only accountant/admin post;
  user administration is admin-only.
* **Upload boundary** — POSIX/Windows traversal, reserved device names, 300-char
  names, extension↔MIME mismatch, content↔MIME mismatch, empty file, malformed
  PDF, disguised executable, HTML/script payload all refused (400/415);
  EICAR and `/JavaScript` PDFs quarantined (`security_status=QUARANTINED`,
  document `Rejected`, **no invoice created**); oversize → 413; duplicate →
  `DuplicateRejected`; no spool file left behind.
* **Vault confinement** — blobs only under `company_<id>/`; a poisoned
  `source_documents.vault_path` (`/etc/passwd`, `../../etc/passwd`) is refused
  with `TenantIsolationError`; key files are `0600`.
* **Audit integrity** — direct `UPDATE`/`DELETE` on `audit_events` aborts with
  `AUDIT_TAMPER_VIOLATION`; chain verifies; deleting the audit key file makes
  startup refuse to mint a replacement rather than report tampering.
* **Key provider contract** — `ERP_KEY_PROVIDER=kms|hsm|vault|FILE` fails closed
  (`kms`, `hsm` and unknown values raise `KeyConfigurationError`); malformed
  Fernet material is refused at construction.
* **API surface** — `/docs`, `/redoc`, `/openapi.json` are 404 by default;
  no `Access-Control-Allow-Origin` is emitted; preflight returns 405;
  `/healthz` discloses only `{"status":"ok","service":"algerian-ai-erp"}`.
* **Injection** — SQL metacharacters and Arabic/RTL/emoji input are stored and
  returned as data; table integrity checked after a `'; DROP TABLE users; --`
  reference. Mass-assignment keys (`company_id`, `workflow_state`,
  `payload_sha256`, `id`) are ignored by the Pydantic models.
* **Static** — `ruff` clean, `compileall` clean, dependency graph valid,
  hash-lock (51 pins / 1,277 hashes) no drift, no credential material found by
  pattern scan.

## 7. Fixes implemented

| Commit-free change | File | Lines (post-change) |
| :--- | :--- | :--- |
| Role guard for document intake (`require_role_secure`) | `dz_erp/security_audit.py` | 664–714 |
| Guard wired to upload | `dz_erp/api.py` | 450–470 |
| Guard wired to field correction | `dz_erp/api.py` | 557–578 |
| Documented-role constant | `dz_erp/api.py` | 153 |
| Request-body cap + headers middleware | `dz_erp/api.py` | 162–185, 231–261 |
| `ValueError → 422` for invoice list limit | `dz_erp/api.py` | 831–835 |
| Username validation | `dz_erp/api.py` | 990–1001 |
| Finite/range guard on decimals | `dz_erp/decimal_utils.py` | 21–47, 49–75 |
| Payment-method / date / reference validation | `dz_erp/procurement_stock.py` | 18–19, 445–468 |
| Derived line net (`compute_line_ht_dzd`, `_materialise_line_ht`) | `dz_erp/procurement_stock.py` | 22–52, 468 |
| Backup archive created 0600 | `dz_erp/security_audit.py` | 944–959 |

No accounting or tax formula was modified. `compute_line_ht_dzd` reproduces the
existing `expected_line_ht` expression verbatim and is now the single definition
used by both the payload hash and the validation loop.

## 8. Regression tests added

`tests/test_phase34_security_validation.py` — **51 tests**, all passing
(`51 passed in 3.90s`). Each finding has at least one reproduction-based test,
and every fixed control has a positive control so the fix cannot be "achieved"
by denying legitimate use. Test names are listed per finding in
`SECURITY_REMEDIATION_LOG.md`.

## 9. Test results (exact)

Commands run from `/home/user/ERP` with the isolated interpreter
`.venv/bin/python` (dependencies installed from `requirements.lock.txt`,
`pip install --no-deps -r requirements.lock.txt`).

| Run | Command | Collected | Passed | Failed | Skipped | Exit |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Baseline (pre-fix) | `.venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=/tmp/baseline_junit.xml` | 695 (+2 collection-skip) | 671 | 20 | 6 | **1** |
| Post-fix | `.venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=/tmp/postfix_junit.xml` | 746 (+2 collection-skip) | 722 | 20 | 6 | **1** |
| New file only | `.venv/bin/python -m pytest tests/test_phase34_security_validation.py -q` | 51 | 51 | 0 | 0 | **0** |
| Static | `.venv/bin/python -m ruff check dz_erp tests tools` | — | — | — | — | **0** |
| Byte-compile | `.venv/bin/python -m compileall -q dz_erp tests tools` | — | — | — | — | **0** |
| Dependency graph | `.venv/bin/python tools/verify_dependency_graph.py` | — | — | — | — | **0** |
| Hash lock | `.venv/bin/python tools/generate_hash_lock.py --check` | — | — | — | — | **0** |
| Dependency advisory scan | `.venv/bin/pip-audit --local --progress-spinner off` | 75 packages | — | 11 packages with advisories | — | 1 |

The 20 failures are **byte-identical before and after** the remediation
(`diff` of the sorted `FAILED` lists is empty). Every one is the same
environment guard:

```
dz_erp.database.FinancialIntegrityError: Safe backup restore requires
Python 3.11.17+ or Python 3.12.10+ (and below 3.13); unsafe archive member
types and paths are rejected.
```

raised at `dz_erp/security_audit.py:1087` from
`restore_and_verify_backup`. This host runs 3.11.2, below the project's own
declared floor (`requires-python >=3.11.17`). The control is working as designed
(fail closed); the tests that exercise the restore path simply cannot run here.
They are reported as **NOT EXECUTED ON THIS HOST**, not as regressions and not
as passes. Affected modules: `test_phase11_audit_key_rotation.py` (3),
`test_phase12_key_migration_and_backup.py` (10),
`test_phase5_security_audit_backup.py` (1),
`test_phase6_ocr_benchmark_e2e.py` (1),
`test_phase8_upload_boundary_and_ci.py` (2),
`test_phase9_process_boundary.py` (3).

Acceptance checks (staging acceptance, deployment smoke) are reported
separately from pytest totals and are **NOT EXECUTED** — see §10.

## 10. Untested items and why

| Item | Status | Reason (exact) |
| :--- | :--- | :--- |
| Staging acceptance against `127.0.0.1:18002` / `ops-erp-1` | NOT EXECUTED | No Docker, no staging host, no listening port, no container (`docker: command not found`; `ss -ltnp` shows only 111/22/49983) |
| Staging health check | NOT EXECUTED | Same; `/healthz` was verified against the in-process app only |
| Backup restore end-to-end, archive path-traversal cases, hardlink refusal, restored-chain verification | NOT EXECUTED | Python 3.11.2 < 3.11.17 safe-tar floor; the code refuses to run (by design). Covered by CI on 3.11/3.12 |
| AWS KMS / HSM live verification | **NOT VERIFIED** | No AWS credentials, no test CMK; this build has no KMS/HSM client and refuses `ERP_KEY_PROVIDER=kms|hsm` by design |
| MinIO / S3 object storage paths | NOT EXECUTED | No MinIO service; not part of `dz_erp` runtime in this revision |
| Desktop UI (PySide6) end-to-end | NOT EXECUTED | Optional dependency not installed; desktop module tests that do not need Qt passed (6 skips are the Qt-dependent ones) |
| Trusted-proxy resolution with a real peer IP | PARTIAL | `TestClient` peer is the literal string `testclient`, so no CIDR can match; policy logic verified at unit level and by code review, forwarded headers provably ignored in the unconfigured case |
| TLS termination, HSTS, certificate chain | NOT EXECUTED | Application serves plain HTTP; TLS is a deployment (proxy) responsibility |
| Sustained rate-limit / credential-stuffing load test | NOT EXECUTED | Out of scope for a synthetic single-process fixture; no in-app limiter exists (SEC-12) |
| Concurrency/soak beyond the existing phase-16 rotation tests | PARTIAL | Existing suite covers rotation convergence; no multi-process load run here |
| Arabic/French OCR recognition quality | NOT EXECUTED | Correctness question, not a security control |
| Container image / OS package CVE scan | NOT EXECUTED | No Docker; repository CI performs this gate |

## 11. Git and project preservation (final check)

| Check | Result |
| :--- | :--- |
| Branch | `arena/7557bcfb-erp` (unchanged; `round7-dep-remediation` never existed here) |
| Initial HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` |
| Final HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` |
| Commits created | None (committing/pushing requires explicit authorization) |
| PR #7 | Still `OPEN`, `DRAFT`, head `5a3e47dd…` — not merged, closed, pushed to or retargeted |
| User modifications lost | None (none existed; tree was clean at start) |
| Databases / volumes deleted | None (no `docker`, no volumes; synthetic `/tmp` fixtures only) |
| Production systems touched | None |
| Unexpected files overwritten | None |
| Secrets exposed | None (no secret values appear in this report or the logs) |

Working-tree changes (all intentional, all on the session branch):

```
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
?? tests/test_phase34_security_validation.py
?? docs/security/            (6 documents)
```

Generated artefacts deliberately kept out of Git: `.venv/` (gitignored), probe
scripts and scratch databases under `/tmp/probe/` (outside the repository),
pytest caches (gitignored).

## 12. Deployment recommendations

1. Run the suite on Python ≥ 3.11.17 (or ≥ 3.12.10) so the restore/DR tests
   actually execute before any promotion; today those 20 tests are unexecuted on
   this interpreter.
2. Re-verify the staging deployment itself (health, auth boundary, RBAC matrix,
   cross-tenant refusals) once a reachable staging host exists; this assessment
   tested the code, not the deployment.
3. Schedule the dependency remediation (SEC-11) as its own change: upgrade
   `starlette`/`fastapi`, `python-multipart`, `pypdf`, `pillow`, `pdfminer.six`,
   `cryptography`, `certifi`, regenerate `requirements.lock.hashes.txt`, and
   re-run the dependency-graph and hash-lock gates.
4. Keep the reverse proxy responsible for rate limiting, TLS termination and
   body-size limits, and set `ERP_TRUSTED_PROXIES` to the proxy range only.
5. Set `ERP_SECURITY_LOG_PATH` in staging/production (otherwise rejection
   records are in-memory only), and keep `ERP_DOCS_ENABLED` unset.
6. Adopt an in-application password blocklist (SEC-13) and a request-level
   limiter; both are policy additions rather than defect fixes.

## 13. Final classification

**SECURITY VALIDATION PASSED WITH RESIDUAL RISKS.**

Rationale: no Critical or High defect was found; the two Medium defects in
first-party code were reproduced, fixed and covered by regression tests; the
third Medium (vulnerable pinned dependencies) is a documented, read-only finding
whose remediation is a coordinated upgrade outside this authorized scope. Two
Low items (SEC-13, SEC-14) and two deployment items (SEC-12, SEC-15) remain open
by decision, and a substantial part of the DR/restore and all staging
verification is **NOT EXECUTED** on this host, which is precisely why the
classification is not an unqualified pass. The project's own status remains
**STAGING VERIFIED** and must not be promoted on the basis of this report alone:
a green unit suite, including the 51 new security tests, does not establish
production readiness.
