# Security Validation Summary — ERP Algeria

| | |
| :--- | :--- |
| Date | 2026-11-09 |
| Branch | `arena/7557bcfb-erp` |
| Initial / final HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` / unchanged |
| Commits created | None (commit/push not authorized) |
| PR #7 | Still OPEN, DRAFT, head `5a3e47dd…` — untouched |
| **Final classification** | **SECURITY VALIDATION PASSED WITH RESIDUAL RISKS** |
| Project status | **STAGING VERIFIED** (unchanged — staging itself was not re-verified here) |

Companion documents: `SECURITY_ASSESSMENT_REPORT.md`,
`SECURITY_FINDINGS_REGISTER.md`, `SECURITY_REMEDIATION_LOG.md`,
`SECURITY_TEST_EVIDENCE.md`, `SECURITY_RESIDUAL_RISKS.md`.

---

## Executive summary

The assessment found **no Critical and no High** defect. It confirmed **three
Medium** issues and **nine Low** issues plus two informational gaps in
first-party code, and reproduced each one before changing anything.

The two most important Medium findings were firm, exploitable-in-context
authorization and data-integrity defects:

* **SEC-01** — the document-upload and OCR-field-correction endpoints enforced
  *tenant* isolation but not *role* authorization, so `warehouse`, `auditor` and
  `ocr_worker` accounts could create supplier documents and rewrite the extracted
  values a reviewer later approves, contradicting the project's own RBAC matrix.
* **SEC-02** — `Decimal('NaN')` survived `quantize`, so `amount_ht_dzd="NaN"` was
  accepted with HTTP 200 and stored as the text `'NaN'` in a money column, while
  `sNaN`, `Infinity`, `1e400` and very large digit strings escaped as HTTP 500.

Both are fixed, with regression tests that assert the *side effect* is absent
(no document row, no invoice row, unchanged field value), not merely that the
status code changed. Six Low findings (error-contract 500s, unvalidated
invoice dates/references, world-readable backup archive, unbounded request
bodies, control characters in usernames, missing hardening headers) were fixed
in the same pass. The third Medium — known-vulnerable pinned dependencies — was
**not** fixed: the brief scopes dependency work to read-only inspection, and the
upgrade requires a coordinated framework bump plus regeneration of the hash-locked
supply-chain files. It is documented with `pip-audit` evidence and an
applicability triage rather than being claimed as resolved.

The verification base is honest about its limits. The full suite went from
**671 passed / 20 failed** to **722 passed / 20 failed**, with the failing set
byte-identical before and after — the 51 new security tests pass, and the same
20 tests fail for one environment reason: this host runs Python 3.11.2 while the
project requires ≥ 3.11.17 for safe tar-based restore, so those restore/DR tests
refuse to run by design. **They are reported as NOT EXECUTED, not as passes.**
No staging host, container, volume or database existed in this sandbox, so every
dynamic test ran against disposable synthetic SQLite/vault fixtures; the
deployed staging service is NOT VERIFIED here.

Consequently the classification is a qualified pass, and the project must not be
promoted on the strength of a green unit suite. The blockers to production
remain what they were: dependency remediation (SEC-11), proxy-level rate
limiting (SEC-12), TLS/Host enforcement (SEC-15), and a real staging + DR
execution on a supported interpreter.

---

## 1. Scope and environment

* **In scope**: `dz_erp/` runtime modules, `tests/`, `pyproject.toml`,
  `requirements.lock*.txt`, CI workflow, and the security-relevant docs that
  define the contract (`SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md`,
  `AUTHENTICATION_HARDENING_SPEC.md`, `KEY_PROVIDER_CONTRACT.md`).
* **Out of scope by instruction**: accounting/tax formulas (none needed
  changing), compliance/legal documents, unmerged PR branches, production.
* **Environment**: `/home/user/ERP`, Python 3.11.2 in an isolated `.venv`
  installed from the pinned lockfile. **No Docker** (`docker: command not
  found`), **no staging stack** (`ss -ltnp` shows only 111/22/49983; no
  `ops-erp-1`, no uvicorn/minio process), **no `/var/lib/erp`**, no
  `ops_erp-state-staging` volume. All test data was synthetic and disposable.
* **Preservation anchors from the brief did not match this workspace** and were
  investigated rather than forced: there is no `round7-dep-remediation` branch
  (session branch `arena/7557bcfb-erp` was used, as required), HEAD is
  `4fc1364a…` not `5a3e47dd…` (the latter is PR #7's head on
  `arena/7f12d627-erp`), and the working tree held **0** user modifications
  rather than 10. No reset, checkout, stash, clean, merge or history change was
  performed. PR #7 remains open and unmerged.

## 2. Methodology

Source review against the project's own stated contracts → targeted dynamic
reproduction on isolated fixtures → minimal fix at the narrowest chokepoint →
regression test asserting both refusal and absence of side effects → re-run of
the original probe → full-suite and static verification. Every finding records
reproduction conditions, evidence, severity rationale, remediation status,
regression test name and retest result (`SECURITY_FINDINGS_REGISTER.md`).

## 3. Security workstreams tested

| Workstream | Outcome |
| :--- | :--- |
| A. Authentication & session management | PASS — no defect found (401 matrix, token storage, revocation, expiry, lockout, timing) |
| B. RBAC & segregation of duties | **SEC-01 fixed**; invoice lifecycle RBAC and creator≠approver verified |
| C. Multi-tenant isolation | PASS — 403 for every cross-tenant read/write path, denials audited; audit/backup endpoints do not exist (N/A); no tenant-scoped background jobs in this revision (N/A) |
| D. API security | SEC-03/04/05/06 fixed; mass assignment ignored; identity headers ignored; docs disabled; no CORS |
| E. SQL injection & input validation | No injection path (parameterised SQL throughout); SEC-02/06 fixed |
| F. File upload & OCR security | PASS — type/size/content/quarantine/cleanup/vault-confinement all hold; the only gap was SEC-01's RBAC |
| G. Local file vault & storage | PASS — traversal refused from a poisoned DB path; keys `0600`; **SEC-07 fixed** (backup mode) |
| H. Audit log integrity | PASS — append-only triggers, HMAC chain, fail-closed on lost key |
| I. Encryption & key management | PASS in fail-closed direction (provider contract, key rings, versions); **AWS KMS live NOT VERIFIED** |
| J. Backup & DR security | PARTIAL — backup creation verified; restore/path-traversal/DR **NOT EXECUTED** (interpreter floor) |
| K. HTTP security configuration | SEC-08/10 fixed; TLS/Host delegated to proxy (SEC-15) |
| L. Dependency & secret scanning | No secrets found; **SEC-11 documented** (read-only per scope) |

Explicit ASVS 4.0.3 / OWASP API Top 10 (2023) mapping per test:
`SECURITY_TEST_EVIDENCE.md` §2. This is an organising framework, **not** an
OWASP certification claim.

## 4. Findings by severity

| Severity | Count | Fixed | Open |
| :--- | :--- | :--- | :--- |
| Critical | 0 | — | — |
| High | 0 | — | — |
| Medium | 3 | 2 (SEC-01, SEC-02) | 1 (SEC-11, documented blocker) |
| Low | 9 | 6 (SEC-03…SEC-09) | 3 (SEC-12, SEC-13, SEC-14) |
| Informational | 2 | 1 (SEC-10) | 1 (SEC-15) |
| **Total** | **14** | **9** | **5** |

(Counts include two accepted false positives excluded here: FP-01, FP-02; see
the register.)

## 5. Fixes implemented

| File | Change | Findings |
| :--- | :--- | :--- |
| `dz_erp/security_audit.py` | `require_role_secure()`; backup archive created `0600` | SEC-01, SEC-07 |
| `dz_erp/api.py` | role guard on upload + field edit; `ValueError`→422 on invoice list; request-body cap; hardening headers; username validation | SEC-01, SEC-03, SEC-08, SEC-09, SEC-10 |
| `dz_erp/decimal_utils.py` | finite/range guard on every conversion branch; reject `bool` | SEC-02 |
| `dz_erp/procurement_stock.py` | payment-method, date and reference validation; derived line net from one shared definition | SEC-04, SEC-05, SEC-06 |

No accounting or tax formula changed; no schema migration; no public endpoint
removed; no control weakened to make a test pass.

## 6. Regression tests added

`tests/test_phase34_security_validation.py` — **51 tests**, all passing.
Organised one block per finding, each with a positive control so a fix cannot be
"achieved" by refusing legitimate use. Full name list:
`SECURITY_REMEDIATION_LOG.md` §8.

## 7. Test results (exact)

| Run | Collected | Passed | Failed | Skipped | Exit |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Full suite, pre-fix | 695 (+2 collection-skip) | 671 | 20 | 6 | 1 |
| Full suite, post-fix | 746 (+2 collection-skip) | 722 | 20 | 6 | 1 |
| Full suite, final confirmation run (after the documentation was added) | 746 (+2 collection-skip) | 722 | 20 | 6 | 1 |
| New security file only | 51 | 51 | 0 | 0 | 0 |
| `ruff check dz_erp tests tools` | — | — | — | — | 0 |
| `compileall` | — | — | — | — | 0 |
| `tools/verify_dependency_graph.py` | — | — | — | — | 0 |
| `tools/generate_hash_lock.py --check` | — | — | — | — | 0 |
| `pip-audit --local` | 75 packages | — | 11 with advisories | — | 1 |

Failure set is identical before/after (`diff` of sorted `FAILED` lines is empty).
All 20 are `FinancialIntegrityError: Safe backup restore requires Python 3.11.17+
or Python 3.12.10+ …` raised at `dz_erp/security_audit.py:1087` — an environment
guard, not a regression. Acceptance checks (staging, deployment smoke) are
separate from pytest totals and were **NOT EXECUTED**.

## 8. Untested items (with exact reasons)

* Staging acceptance / deployed health — no Docker, no container, no listening
  staging port in this sandbox.
* Backup restore, archive traversal, hardlink refusal, restored-chain and DR
  idempotency — Python 3.11.2 below the safe-tar floor; the control refuses to
  run.
* AWS KMS/HSM live operation — no credentials, no test CMK; the build has no
  KMS client and rejects `ERP_KEY_PROVIDER=kms|hsm` by design.
* MinIO/S3 paths, Docker image CVE scan, PySide6 desktop end-to-end, TLS/HSTS,
  Host allow-listing, sustained rate-limit load — see
  `SECURITY_ASSESSMENT_REPORT.md` §10.

## 9. External dependencies

AWS KMS/HSM (absent), a TLS-terminating reverse proxy with rate limiting
(deployment), a supported Python ≥ 3.11.17 interpreter for DR verification,
MinIO/S3 for remote backup (not exercised), and the advisory database behind
`pip-audit` (available). No external service was sent source code or secrets.

## 10. Remaining risks

See `SECURITY_RESIDUAL_RISKS.md`. Summary: vulnerable pinned dependencies
(Medium), no in-app rate limiting (Low), length-only password policy (Low),
unvalidated OCR override values (Low), TLS/Host delegation (Informational), plus
environment-caused verification gaps (no staging, no DR execution on this
interpreter, no KMS).

## 11. Deployment recommendations

1. Verify and promote **only** after the suite runs on Python ≥ 3.11.17 so the
   DR/restore tests execute, and after the staging acceptance procedure passes
   against the real deployment.
2. Land the dependency remediation (SEC-11) as a reviewed change with
   hash-lock regeneration and an OCR regression run.
3. Keep TLS termination, HSTS, `Host` allow-listing, request-size and rate
   limits at the proxy; set `ERP_TRUSTED_PROXIES` to the proxy range only; set
   `ERP_SECURITY_LOG_PATH`; leave `ERP_DOCS_ENABLED` unset.
4. Treat the backup encryption key and the audit/vault key files as tier-0
   secrets (custody separation, off-host checkpoints, no world-readable files).
5. Schedule a repeat of this assessment after the dependency upgrade and after
   the unmerged review-binding work lands, since SEC-14 interacts with it.

## 12. Final security classification

**SECURITY VALIDATION PASSED WITH RESIDUAL RISKS.**

Justification, restated plainly: no Critical/High defect exists in the assessed
revision; the two exploitable Medium defects in first-party code were
reproduced, fixed and locked down with side-effect-aware regression tests; the
third Medium is a documented supply-chain blocker outside the authorized
read-only dependency scope; five low/informational items remain by decision; and
a material part of the verification (all staging checks, the DR/restore suite,
live KMS) could not be executed in this environment and is therefore reported as
NOT VERIFIED rather than assumed. The project remains **STAGING VERIFIED** — not
production-ready — until those items are closed with evidence.
