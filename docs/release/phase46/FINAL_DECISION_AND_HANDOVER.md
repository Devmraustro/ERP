# Phase 46 — Step 9: Final Release Decision and Handover Report

**Recorded:** 2026-11-13 (UTC)
**Baseline branch / PR #8 head:** `arena/7557bcfb-erp` @ `098483a1f02df0308f3889c93714efab5d13b550`
**Continuation session branch:** `arena/63d762c8-erp` (branched from `098483a1f02df0308f3889c93714efab5d13b550`)
**Pull Request:** PR #8 (`arena/7557bcfb-erp` → `main`) — `OPEN`, **deliberately not merged**

---

## 1. Release Classification and Decision

> **Classification: `STAGING VERIFIED — EXPERT APPROVAL PENDING`**
>
> **Production Release Decision: `NOT APPROVED` (G5 and G6 are `BLOCKED`; PR #8 is NOT merged into `main`; nothing was deployed).**

The integrated codebase — combining the Phase 25–30 desktop client, SEC-13/14/15 security controls, Phase 38 accounting gates, and the Phase 29/46 enforced OCR field-review subsystem — is **complete, defect-remediated, and verified green across all three GitHub Actions CI jobs** on `098483a1f02df0308f3889c93714efab5d13b550` (Python 3.11, Python 3.12, and Windows Python 3.12.10).

Production release and merge to `main` remain blocked by two mandatory governance gates that cannot be satisfied by software engineering alone, plus one pre-production defence-in-depth finding uncovered during G6 inspection:

1. **G5 (`BLOCKED`) — Algerian statutory and accounting expert sign-off:** None of the 12 statutory questions (`Q1`–`Q12`) in `ALGERIAN_EXPERT_APPROVAL_REGISTER.md` has been answered or signed by a qualified Algerian `expert-comptable`. No tax or accounting output of this software may be represented as tax-authoritative.
2. **G6 (`BLOCKED`) — Production deployment readiness:** No production environment, target host, domain, TLS certificate, secret store, alert destination, or named operator exists; in addition, `ERP_ALLOWED_HOSTS` host-header validation is documented in earlier phases as enforced but is not implemented in `dz_erp/` (pre-production blocker).

---

## 2. Gate Status Summary (G1–G6)

| Gate | Requirement | Final Status | Authoritative Evidence |
| --- | --- | --- | --- |
| **G1** | Integration completeness & zero open Critical/High code defects | **PASS** | `eb5b09a`, `02068c0`; `docs/release/phase46/BASELINE.md`, `FINAL_REGRESSION.md` |
| **G2** | Dependency integrity & hash-locked supply chain (H-1) | **BLOCKED (upstream metadata) — MITIGATED & VERIFIED, NOT CI-AFFECTING** | `docs/release/phase46/H1_DEPENDENCY_HASH_LOCK.md` + `logs/h1_*` |
| **G3** | Security controls (RBAC, SoD, SEC-13/14/15, audit chain, safe restore) | **PASS** *(with G6 host-allowlist gap flagged pre-prod)* | `FINAL_REGRESSION.md` §5; CI run `43450664678` |
| **G4** | Accounting, procurement & 3-way match invariants | **PASS** | `FINAL_REGRESSION.md` §5–§6; CI run `43450664678` |
| **G5** | Algerian statutory / `expert-comptable` sign-off (12 questions) | **BLOCKED** | `docs/release/phase46/ALGERIAN_EXPERT_APPROVAL_REGISTER.md` |
| **G6** | Production target, TLS, secrets, host allowlist & operational readiness | **BLOCKED** | `docs/release/phase46/G6_DEPLOYMENT_READINESS.md` |

---

## 3. Confirmed Remote CI Verification on Exact Head `098483a`

When `FINAL_REGRESSION.md` (`098483a`) and the updated PR #8 description were written in Step 5–6, CI had completed on `02068c0` (run `43339096973`) and was still running on `098483a`. All three CI jobs on `098483a1f02df0308f3889c93714efab5d13b550` have now completed and passed:

| Property | Verified Value |
| --- | --- |
| Workflow | `CI` (`.github/workflows/ci.yml`) |
| Run ID | **`43450664678`** (`https://github.com/Devmraustro/ERP/actions/runs/43450664678`) |
| Trigger / Branch | `pull_request` on `arena/7557bcfb-erp` (PR #8) |
| Head SHA | `098483a1f02df0308f3889c93714efab5d13b550` |
| Window | `2026-11-13T15:14:54Z` → `2026-11-13T15:28:26Z` |
| Overall conclusion | **`SUCCESS`** |

| Job ID | Job Name | Key Verified Steps | Result |
| --- | --- | --- | --- |
| `131971818147` | `Test, coverage and static checks (Python 3.11)` | Hash-enforced install (`--no-deps --require-hashes`), `generate_hash_lock.py --check`, `verify_dependency_graph.py`, `cv2` + `RapidOCR` import, `ruff`, `compileall`, full `pytest` suite with coverage, synthetic staging acceptance (`tools/staging_acceptance.py`), `risk_register_counts.py` | **`SUCCESS`** |
| `131971818500` | `Test, coverage and static checks (Python 3.12)` | Hash-enforced install, lock check, dependency graph check, `cv2` + `RapidOCR` import, `ruff`, `compileall`, full `pytest` suite with coverage on Python 3.12 | **`SUCCESS`** |
| `131971818756` | `Key rotation, backup and recovery on Windows` | Windows (`windows-latest`) Python `3.12.10`, hash-enforced install, lock & graph checks, `ruff`, `compileall`, Windows key-rotation / backup-restore / process-boundary / reparse-point suites, Windows synthetic staging acceptance | **`SUCCESS`** |

**Note on CI log/artifact retrieval:** Direct artifact/log streaming via `gh run view --log` / `gh run download` in this sandbox returns zero-byte/EOF responses from the artifact endpoint; therefore no unverified per-job pass counts from CI stdout are fabricated here. Local counts (`947 passed / 22 failed / 21 skipped` on CPython `3.11.2`, where all 22 failures are proven to be the `>=3.11.17` / `>=3.12.10` safe-restore runtime guard) and CI step-level `SUCCESS` conclusions via the GitHub API are reported strictly as observed.

---

## 4. Phase 46 Step-by-Step Completion Record (Steps 1–9)

| Step | Scope | Outcome | Deliverable / Commit |
| --- | --- | --- | --- |
| **Step 1** | Baseline revalidation, ref-reset recovery & red-CI root cause | Recovered reset worktree to `90f382a` with zero content loss; diagnosed CI collection abort (`ImportError` in `tests/test_phase29_ocr_review_policy.py`); integrated Phase 29 field-review subsystem (`49/49` passing), fixed 4 genuine code defects, and updated synthetic staging acceptance for enforced field review. | `docs/release/phase46/BASELINE.md` (`eb5b09a`, `02068c0`) |
| **Step 2** | H-1 dependency integrity & hash lock | Proved H-1 is an upstream `rapidocr-onnxruntime==1.4.4` metadata contradiction (`opencv-python>=4.5.1.48` vs pinned `opencv-python-headless==4.11.0.86`); proved clean `--no-deps --require-hashes` install (`53/53` pins, `1,317` hashes), zero GUI OpenCV symbols, and real RapidOCR neural inference (`0.975` / `0.994` confidence). | `docs/release/phase46/H1_DEPENDENCY_HASH_LOCK.md` + `logs/h1_*` (`3d112e1`) |
| **Step 3** | G5 Algerian statutory expert approval register | Documented `Q1`–`Q12` explicitly labelled **reconstructed**, re-verified every file/line citation, left all expert answer/signature fields blank, and documented that effective-dated `resolve_vat_rates` / `resolve_timbre_rule` helpers referenced in docstrings do not exist. | `docs/release/phase46/ALGERIAN_EXPERT_APPROVAL_REGISTER.md` (`9263215`) |
| **Step 4** | G6 production deployment readiness | Assessed every operational criterion honestly (`✅` / `⚠️` / `❌`), prepared non-executed operator runbook commands with placeholders, and uncovered that `ERP_ALLOWED_HOSTS` is documented as enforced in Phase 8/9/10 reports but unimplemented in `dz_erp/`. | `docs/release/phase46/G6_DEPLOYMENT_READINESS.md` (`1963a33`) |
| **Step 5** | Final regression & static verification | Ran full suite on CPython `3.11.2` (`947P / 22F / 21S`), proved all 22 failures (including 2 wrapped assertions) are exclusively the safe-restore interpreter floor without relaxing the guard, and verified `ruff`, `compileall`, hash lock, dependency graph, and `38/40` staging acceptance. | `docs/release/phase46/FINAL_REGRESSION.md` + `logs/final_*` (`098483a`) |
| **Step 6** | PR #8 update & CI confirmation on final head | Updated PR #8 body (`https://github.com/Devmraustro/ERP/pull/8`) to replace stale test counts and stale "no schema migration" text; verified all 3 CI jobs green on `098483a` (run `43450664678`). | PR #8 + §3 above |
| **Step 7** | Merge evaluation & branch-protection check | Queried `repos/Devmraustro/ERP/branches/main/protection` → `HTTP 403` (`Resource not accessible by integration`). **Refused merge to `main`** because mandatory gates G5 and G6 are `BLOCKED`. PR #8 stays `OPEN` (`MERGEABLE`, `CLEAN`). | §6 below |
| **Step 8** | Production deployment & post-deploy smoke | **NOT EXECUTED (`BLOCKED` on G5 & G6).** No production target exists; no unintended host or platform was targeted. | `G6_DEPLOYMENT_READINESS.md` §1, §4–§5 |
| **Step 9** | Final release decision & handover package | Completed and committed in this document (`FINAL_DECISION_AND_HANDOVER.md`), re-verified locally (`generate_hash_lock.py --check`, `verify_dependency_graph.py`, `ruff`, `compileall`, `risk_register_counts.py`, `test_phase22_register_and_docs_consistency.py`, `test_phase29_ocr_review_policy.py`). | `docs/release/phase46/FINAL_DECISION_AND_HANDOVER.md` |

---

## 5. Defects Fixed and Findings Raised in Phase 46

### 5.1 Five defects fixed in code (commits `eb5b09a`, `02068c0`)

1. **Suite-wide CI collection abort (`tests/test_phase29_ocr_review_policy.py`):** Commit `3106882` had ported the 49-test Phase 29 policy suite without porting the field-review subsystem (`decide_extracted_field_review`, `ocr_field_reviews`, conflict tracking, review routes, and enforced submit/approve review gate). Integrated surgically at function level without losing `/readyz` or desktop endpoints (`49/49` tests now pass).
2. **`compute_invoice_payload_hash` omitted `discount_pct`:** Modifying a line's commercial discount left the invoice payload hash unchanged, allowing a stale approval to remain valid across a mutated discount. Fixed and covered by tests.
3. **`modify_draft_invoice_line` silently dropped stored line discounts:** Editing unit price or quantity recomputed `qty × unit_price` without `discount_pct`, silently inflating discounted lines back to gross HT (e.g. `990.00` → `1100.00` DZD). Fixed using `compute_line_ht_dzd`.
4. **`_validate_invoice_lines` existed but was never called on the write path:** Out-of-range discounts (e.g. `-5 %`) reconciled arithmetically and reached `workflow_state='Validated'`. Wired onto the live validation path.
5. **`update_extracted_field_by_human` lacked role gate, document-state gate, and value validation:** Replaced with the hardened implementation enforcing `REVIEW_CAPABLE_ROLES`, mutable document states, and normalized value checks; updated `tools/staging_acceptance.py` (`02068c0`) to prove refusal-before-review and success-after-review over HTTP.

### 5.2 Two findings intentionally recorded rather than silently patched

1. **`ERP_ALLOWED_HOSTS` is documented as enforced but not implemented (Medium-High — G6 pre-production blocker):**
   - `docs/release/phase8/REVERSE_PROXY_TLS_ACCEPTANCE.md:66`, `docs/release/phase9/DEPLOYMENT_READINESS_REPORT.md:29`, and `docs/release/phase10/PUBLIC_TLS_ACCEPTANCE.md:78` claim app-level `ERP_ALLOWED_HOSTS` validation.
   - `dz_erp/api.py` registers only `_security_headers` and `_request_id_and_throttle`; nothing in `dz_erp/` reads `ERP_ALLOWED_HOSTS`.
   - Recorded as Owner Action #1 in `G6_DEPLOYMENT_READINESS.md` so it is implemented cleanly with dedicated middleware tests and real reverse-proxy harness validation before production exposure.
2. **Effective-dated statutory rule resolvers referenced in docstrings do not exist (Correctness/Documentation — blocked on G5 `Q10`):**
   - `dz_erp/procurement_stock.py` docstrings cite `dz_erp.localization.resolve_vat_rates` and `resolve_timbre_rule`, neither of which exists; compiled-in constants always govern even though `timbre_rules` exists in SQLite.
   - Cannot be wired honestly until an `expert-comptable` answers `Q10` (invoice date vs posting date) and `Q11` (post-rule-change re-stamping vs frozen provenance).

---

## 6. Merge, Branch Protection, and Session Branch Accounting

1. **Branch protection visibility (`main`):**
   - Querying `gh api repos/Devmraustro/ERP/branches/main/protection` returns `HTTP 403: Resource not accessible by integration`. Branch protection settings on `main` therefore cannot be verified by the repository integration token and must be confirmed by the repository owner in GitHub settings.
2. **Why PR #8 is NOT merged:**
   - PR #8 (`arena/7557bcfb-erp` → `main`) is `OPEN`, `MERGEABLE`, and `CLEAN` with all three CI jobs green (`43450664678`), but merging to `main` as a production release is withheld because mandatory gates **G5** and **G6** remain `BLOCKED`.
3. **Commit lineage (`main` → Phase 46):**
   - `4fc1364` — `main` base (`Merge pull request #2`)
   - `3106882` — Phase 35–41 code integration (SEC-13/14/15 and PR #7 integrity gates)
   - `f48db93` — Phase 35–45 release evidence and deliverables
   - `a20a478` — Phase 45 final handover report
   - `90f382a` — Phase 41 re-verification on rebuilt toolchain
   - `eb5b09a` — Phase 46: integrate Phase 29 field-review subsystem and fix 4 defects
   - `02068c0` — Phase 46 Step 1 (`BASELINE.md`) + enforced field review in `tools/staging_acceptance.py`
   - `3d112e1` — Phase 46 Step 2 (`H1_DEPENDENCY_HASH_LOCK.md` + `logs/h1_*`)
   - `9263215` — Phase 46 Step 3 (`ALGERIAN_EXPERT_APPROVAL_REGISTER.md`)
   - `1963a33` — Phase 46 Step 4 (`G6_DEPLOYMENT_READINESS.md`)
   - `098483a` — Phase 46 Step 5 (`FINAL_REGRESSION.md` + `logs/final_*`) — **PR #8 head, CI run `43450664678` green**
   - Continuation commit on `arena/63d762c8-erp` — Phase 46 Step 9 (`FINAL_DECISION_AND_HANDOVER.md`)

---

## 7. Explicit Non-Claims (Honesty Boundaries)

- **No Algerian tax or accounting compliance is claimed.** All 12 statutory questions in `ALGERIAN_EXPERT_APPROVAL_REGISTER.md` remain unanswered (`G5: BLOCKED`).
- **No production deployment occurred.** No host, domain, certificate, KMS, or secret was provisioned or contacted (`G6: BLOCKED`).
- **No local deep-restore test passed on CPython `3.11.2`.** Deep-restore verification requires Python `>=3.11.17` or `>=3.12.10 (<3.13)` by security design; its passing status comes from remote CI (`Python 3.11` patched runner, `Python 3.12`, and `Windows Python 3.12.10`), not from weakening the guard.
- **No local interactive Windows/Linux GUI visual test was executed in Phase 46.** `PySide6` is absent from the hash-locked server runtime venv; Windows storage/process/rotation/staging paths are covered by the Windows CI job (`131971818756`), while native Windows interactive GUI display-scaling checks remain an owner acceptance step on real Windows hardware.
- **No static type-checker (`mypy`) claim is made.** `mypy` is not configured in `pyproject.toml` (`NOT RUN`).

---

## 8. Ordered Owner Actions Required Before Production Release

1. **Implement `ERP_ALLOWED_HOSTS` trusted-host validation** in `dz_erp/api.py`, add unit/integration tests for rejected `Host` headers, reconcile the Phase 8/9/10 docs that claimed it was already active, and re-run `docs/release/phase8/evidence/tls_proxy_harness.py` (**G6 pre-production security blocker**).
2. **Engage a qualified Algerian `expert-comptable`** to answer and sign `Q1`–`Q12` in `docs/release/phase46/ALGERIAN_EXPERT_APPROVAL_REGISTER.md` with verbatim rulings, professional capacity, date, and verifiable engagement evidence (**unblocks G5**).
3. **Wire effective-dated VAT and timbre rule resolution** (`resolve_vat_rates`, `resolve_timbre_rule`, and posting-time provenance rules per `Q10`/`Q11`) once the `expert-comptable` answers `Q10` and `Q11`, then re-run the statutory test suite.
4. **Provision the production environment** following `docs/release/phase46/G6_DEPLOYMENT_READINESS.md` §4–§5:
   - Host running supported CPython (`>=3.11.17` or `>=3.12.10, <3.13`) so safe backup deep-verification works natively;
   - Owned domain, public-CA TLS termination at the reverse proxy with automated renewal, backend bound strictly to `127.0.0.1`;
   - Platform-managed secrets (`ERP_DB_PATH`, `ERP_VAULT_PATH`, `ERP_BACKUP_KEY_FILE`, audit key custody), off-host encrypted backup storage, and configured alert delivery;
   - All 7 rate-limit variables set (`ERP_RATE_LIMIT_BACKEND=sqlite` whenever running `>1` worker);
   - Named individuals recorded for Release Owner, Operator, DBA/Data Owner, and Security Owner;
   - Full staging rehearsal of backup → additive schema migration on a DB copy → start → `/readyz` → `tools/staging_acceptance.py` → deep restore rollback (**unblocks G6**).
5. **(Optional supply-chain cleanup — H-1):** Evaluate an OCR package release whose metadata declares `opencv-python-headless` directly against the `benchmarks/` corpus so `--require-hashes` works even without `--no-deps`.
