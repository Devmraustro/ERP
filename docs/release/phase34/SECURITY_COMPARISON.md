# Phase 34 — Security comparison

**Scope:** commit `2427fda` (PR #7 head minus one docs commit) versus the current staging workspace
on `arena/7557bcfb-erp` at `4fc1364`.

**Method:** read-only. The PR tree was exported with `git archive` to `/tmp/pr7`; the workspace was
not modified. Every claim below was produced by running code, not by reading the PR's own reports.

---

## 0. The headline number is misleading

`2427fda` is described as "89 changed files, 16,448 insertions". That is true and materially
overstates the security change. Measured by `git diff --numstat`:

| Category | Files | Insertions | Share |
| --- | --- | --- | --- |
| Documentation (reports, logs, patches, hashes) | **75** | **13,650** | **83.0%** |
| Tests | 5 | 1,824 | 11.1% |
| **Production code** | **9** | **974** | **5.9%** |

The nine code files, with real deltas:

| File | +/− | What it actually does |
| --- | --- | --- |
| `dz_erp/api.py` | +298/−5 | Request-size ceiling, invoice-number/date/payment-method validation, SEC-15 middleware wiring, JSON body cap |
| `dz_erp/auth.py` | +225/−14 | SEC-13 password policy, length floor/cap, PBKDF2 rehash-on-login |
| `dz_erp/net.py` | +101/−0 | SEC-15 Host allow-list (entirely new) |
| `dz_erp/procurement_stock.py` | +117/−2 | Invoice metadata validation at the domain layer |
| `dz_erp/security_audit.py` | +123/−4 | Key-rotation imports, `reset_password_admin_secure` |
| `dz_erp/decimal_utils.py` | +31/−10 | Non-finite/oversized decimal refusal |
| `dz_erp/intake_ocr.py` | +30/−0 | SEC-14 override validation |
| `dz_erp/serve.py` | +11/−1 | Startup wiring |
| `ops/erp.env.example` | +38/−0 | Env documentation |

**Conclusion:** 16,448 insertions is a documentation artifact. The security substance is ~974 lines
across nine files. That is a reasonable amount of real work — it is not the transformation the
number suggests.

---

## 1. SEC-15 — Host header validation

**Implementation:** `dz_erp/net.py` `parse_allowed_hosts`, `_strip_port`, `allowed_hosts_from_env`,
`host_is_allowed`, `describe_allowed_hosts`; middleware `_validate_host_header` in `dz_erp/api.py`
(registered before the logging layer so refusals are logged like any other 4xx).

**Threat addressed:** Host-header injection — cache poisoning, absolute-URL generation, and
password-reset link poisoning once a mailer exists. Real and correctly scoped.

**Tests:** `tests/test_phase59_sec13_14_15_closure.py`.

**Does the workspace have an equivalent?** **No.** `dz_erp/net.py` in the workspace has
`TrustedProxyPolicy` and `parse_forwarded_for` but **zero** Host validation
(`grep -c allowed_hosts` → 0). `dz_erp/api.py` has no middleware.

**Safer / equivalent / incomplete?** **Safer — genuinely.** I executed the PR implementation against
16 Host values:

```
127.0.0.1:48123    -> True         evil.com              -> False
localhost:8000     -> True         evil.com:80           -> False
[::1]:5000         -> True         localhost.evil.com    -> False
testserver         -> True         127.0.0.1.nip.io      -> False
(empty)            -> False        None                  -> False
127.0.0.1:notaport -> False        127.0.0.1:99999       -> True (port ignored, name matches)
```

Wildcard handling is correct too: `*.example.dz` accepts `api.example.dz` and **rejects**
`badexample.dz` and bare `example.dz`. `parse_allowed_hosts` discards `https://bad.com/` (returns
`()`), collapses `,` and whitespace, lower-cases, and de-duplicates. A missing `Host` is refused
rather than treated as "any".

**One operational hazard, which I want on the record.** `DEFAULT_ALLOWED_HOSTS = ("localhost",
"127.0.0.1", "[::1]", "testserver")`. An operator who deploys this and forgets `ERP_ALLOWED_HOSTS`
gets **HTTP 400 on every request**. That is fail-closed (the right *direction* of failure) and it is
documented in `docs/PRODUCTION_DEPLOYMENT_GUIDE.md:135-141`, but it is a hard cutover: the app
becomes unusable rather than degraded. It must be in the deployment runbook, and it is the single
most likely cause of a "the new version won't load" incident on go-live.

**Verdict: MUST INTEGRATE.**

---

## 2. SEC-13 — password policy and lifecycle

**Implementation:** `dz_erp/auth.py` — `MIN_PASSWORD_LENGTH=8`, `RECOMMENDED_PASSWORD_MIN_LENGTH=12`,
`MAX_PASSWORD_LENGTH=1024`, `ENV_PASSWORD_MIN_LENGTH`, `PasswordPolicyError`,
`configured_password_min_length`, `validate_password_policy`, `_stored_iterations`, plus
rehash-on-login and `reset_password_admin_secure`.

**Threat addressed:** Trivially short passwords; unbounded PBKDF2 work (DoS via a megabyte password);
credentials stuck on an old iteration count.

**Executed behaviour:**

| Input | Result |
| --- | --- |
| 5 chars | REFUSED `PASSWORD_TOO_SHORT` |
| 7 chars (`P#2026x`) | REFUSED `PASSWORD_TOO_SHORT` |
| 10 chars | ACCEPTED |
| 65 chars | ACCEPTED |
| `ERP_PASSWORD_MIN_LENGTH=4` | floored to **8** (cannot be weakened) |
| `ERP_PASSWORD_MIN_LENGTH=999` | capped to **64** |
| `ERP_PASSWORD_MIN_LENGTH=abc` / `""` | 8 |

**Does the workspace have an equivalent?** **No.** `grep -c validate_password_policy
dz_erp/auth.py` → **0**. The workspace hashes with PBKDF2-SHA256 at 600,000 iterations (OWASP 2023)
but imposes **no length policy at all**: a 1-character password is accepted today.

**Safer / equivalent / incomplete?** **Safer.** The floor cannot be configured downward, the cap
bounds work per request, and the rehash-on-login migration is a genuinely subtle piece of good
engineering — it upgrades stored hashes without forcing a password reset. Audit event
`AUTH_PASSWORD_REHASHED` records `from_iterations`/`to_iterations`.

**Docstring defect (minor):** the comment block describes the cap as 64, but
`MAX_PASSWORD_LENGTH = 1024` (the 64 is the *minimum's* ceiling). Cosmetic; no behavioural risk.
Worth fixing while the file is touched.

**Migration cost, measured:** SEC-13 breaks test fixtures that use short passwords. In the whole
workspace suite only **2 occurrences** (`password="P#2026x"`) in **1 file** fail. PR #7 fixed them
by lengthening to `Prov#2026x`. This is a 2-line change, not a migration project.

**Verdict: MUST INTEGRATE.**

---

## 3. SEC-14 — OCR override validation

**Implementation:** `dz_erp/intake_ocr.py` `update_extracted_field_by_human` — validates the
corrected value against the field's value class using the same validator the decision path uses,
refuses invalid values, writes `SECURITY_FIELD_EDIT_REJECTED` to the audit chain, and raises
`FinancialIntegrityError("FIELD_VALUE_REJECTED:…")`.

**Threat addressed:** a reviewer's "correction" could be an arbitrary string stored into both
`human_override_value` **and** `normalized_value`, which the review policy would only reject later.
That is a data-integrity defect in a financial field — exactly the kind this product must not have.

**Does the workspace have an equivalent?** **No — and it cannot have one.** The workspace has no
`dz_erp/review_policy.py` and no `validate_corrected_value`. `grep -c review_policy
dz_erp/intake_ocr.py` → **0**.

**Critical dependency finding:** SEC-14 is **not** a standalone fix. It imports
`TEXT_FALLBACK_RULE, rule_for, validate_corrected_value` from `dz_erp/review_policy`, a module that
arrived in the **Phase 29 OCR fail-closed** line of work (`29645e5`, `c466cea`, `e4e47b3` on branch
`phase29/ocr-failclosed`) — **not** in `2427fda`, and **absent from this workspace**.

So: the PR's SEC-14 *closes a hole created by the Phase 29 review-policy system*. This workspace
never got that system, so it has a different and larger gap: **no field-level review enforcement at
all**. Copying `intake_ocr.py`'s SEC-14 hunk without `review_policy.py` would raise `ImportError` at
call time; copying both means importing a large subsystem with its own acceptance suite.

**Safer / equivalent / incomplete?** The PR's implementation is **safer**, but it is inseparable
from the Phase 29 review architecture. Treat SEC-14 as *part of* that integration, not as a
standalone patch.

**Verdict: REQUIRES EXPERT REVIEW** — it is MUST INTEGRATE for production, but it must travel with
`review_policy.py` and its tests, not alone.

---

## 4. Vault, key rotation, backup/restore

The PR's report claims a "Local File Vault" verification with 0600 permissions, tenant isolation,
tamper refusal, 16-way concurrency, v1→v2 rotation and permission-denied fail-closed.

**Measured:** `2427fda` **does not modify** `key_rotation.py`, `key_provider.py`, `rotate_keys.py` or
`rotation_audit.py` — those already existed in `5a3e47dd`. The vault report under
`docs/security/PHASE5_B1_LOCAL_VAULT_RELEASE_GATE_REPORT.md` is a *new document describing
pre-existing code*, and it records `B1 OPEN … COMMIT AND PUSH WITHHELD` in its own status line.

**Does the workspace have equivalents?** Partially. `dz_erp/key_rotation.py` **is present** in the
workspace. The later DR line (`restore_generation.py`, `backup_storage.py`, `dr_metrics.py`) is
present in PR #7 and **absent** here.

**Assessment:** the vault work is real but it is *verification of code both branches share*, not a
new control introduced by `2427fda`. It should not be credited to this commit.

---

## 5. Dependency and supply-chain security

PR #7 states: 53 locked packages checked against the GitHub Advisory Database, **0 matches**, versus
8/8 flagged on old pins. Both branches now carry `requirements.lock.hashes.txt` and a
`--require-hashes` install path; the earlier Phase 30 work closed 143→0 advisories on this branch.

**Verification:** I installed PR #7's lockfile with `--require-hashes` on CPython 3.12.10 and it
resolved cleanly (all 53 packages). Both branches use the same pinned-dependency discipline.

**Verdict: ALREADY PRESENT** (equivalent posture; no divergence found).

---

## 6. Authentication and authorization

PR #7 adds `require_role_secure` (document-intake role gate) and `reset_password_admin_secure`
(privileged reset). The **current workspace already has `require_role_secure`** with the same
signature, the same body, and only different docstring wording — I compared the two with an AST
diff. The functional difference is one line: PR #7 drops a redundant
`user: Optional[Any] = None` local declaration.

This is **convergent evolution** — two agents fixed the same hole independently. It is not
divergence, and it needs no merge decision beyond "keep the workspace's docstring".

**Verdict: ALREADY PRESENT.**

---

## 7. Audit-chain integrity

No difference in mechanism between the branches: both use hash-chained append-only audit with
versioned keys and `describe`/verify helpers, and both accept multiple key versions on restore. My
Phase 30 restore drill proved the workspace chain verifies for two key generations and that audit
event census matches the source. PR #7's phase-60 failure-mode suite (9 tests: disk-full, restore
failure, permission denied) adds *failure-path* coverage this workspace lacks.

**Verdict: SHOULD INTEGRATE** (the failure-mode tests, not a code change).

---

## 8. Security regression tests

| Suite | Tests | Present here? |
| --- | --- | --- |
| `test_phase58_security_selective_integration.py` | 55 | No |
| `test_phase59_sec13_14_15_closure.py` | 39 | No |
| `test_phase60_backup_failure_modes.py` | 9 | No |
| **Total new** | **103** | **No** |

**Executed:** on CPython 3.12.10 the full PR suite is **1373 passed, 0 failed, 6 skipped** — I
reproduced this exactly, including by building a working `_sqlite3`, `_ssl`, `_hashlib` and `_ctypes`
for the interpreter (see `ARCHITECTURE_COMPARISON.md` §7). On 3.11.2 the same tree yields
**1340 passed, 20 failed**, and all 20 failures are the restore-path interpreter guard
(`Safe backup restore requires Python 3.11.17+ or Python 3.12.10+`), identical in nature to the 20
skips in this workspace.

---

## 9. Summary table

| Control | Workspace | PR #7 | Verdict |
| --- | --- | --- | --- |
| Host-header allow-list (SEC-15) | **absent** | correct, tested | **MUST INTEGRATE** |
| Password policy (SEC-13) | **absent** | correct, tested | **MUST INTEGRATE** |
| OCR override validation (SEC-14) | absent (and no review policy) | correct, needs `review_policy` | **REQUIRES EXPERT REVIEW** |
| Role gate on upload | present | present (identical) | **ALREADY PRESENT** |
| Decimal non-finite refusal | present | present (same constant, different function name) | **ALREADY PRESENT** |
| Hash-chained audit | present | present | **ALREADY PRESENT** |
| Pinned hashes / advisories | 0 advisories | 0 advisories | **ALREADY PRESENT** |
| Backup failure modes | no tests | 9 tests | **SHOULD INTEGRATE** |
| Request-size ceiling | no | 2 MiB cap | **SHOULD INTEGRATE** |
| Key rotation / vault code | **present** | present (unchanged by this commit) | **ALREADY PRESENT** |
| DR generation/remote storage | **absent** | present (earlier commits) | **REQUIRES EXPERT REVIEW** |

## 10. Which branch has the stronger security implementation?

**PR #7, unambiguously.** It has three controls this workspace lacks entirely (SEC-13, SEC-14,
SEC-15), 103 security tests this workspace does not have, and its failure-mode coverage is broader.
The workspace's security is not *bad* — it has the role gate, audit chain, decimal guards, restore
guards and a clean dependency posture — but it is missing the three closure items.

That does **not** make PR #7 the better release candidate overall: it has 1799→3583 lines of
accounting logic this workspace lacks and a desktop application this workspace has and PR #7 never
touched. See `FINAL_ASSESSMENT.md`.
