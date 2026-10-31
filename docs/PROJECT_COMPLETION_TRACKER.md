# Project completion tracker

One row per task: what was attempted, what proves it, and what remains. A task is
`DONE` only when a command on this host (or a CI run on the exact published SHA)
produced the recorded result. `IMPLEMENTED_NOT_VERIFIED` means code exists but the
evidence does not. `BLOCKED_EXTERNAL` means no code can close it.

**Branch:** `arena/7f12d627-erp` · **Baseline:** `852f389f` ·
**Environment:** Linux, CPython 3.11.2, pytest 8.3.5, coverage 6.0.0, ruff 0.14.0,
hash-locked dependency set, `cv2` 4.11.0 + RapidOCR importable.

| # | Phase | Task | Status | Files | Tests run | Result | Commit |
|---|---|---|---|---|---|---|---|
| 1 | Baseline | Restore and verify the branch against origin (no reset, no rewrite) | **DONE** | — | `git write-tree` vs origin tree | identical (`def29e72…`); HEAD `852f389f` | — |
| 2 | Baseline | Reproduce the recorded test baseline | **DONE** | — | `pytest -q --cov=dz_erp` | 796 passed, 20 failed (interpreter floor), 7 skipped, 77 % | — |
| 3 | P0 OCR | Reproduce the duplicate-row fail-open condition (P34-F1) | **DONE** | — | `/tmp/repro_dup.py` on `852f389f` | duplicate row `5555.00` ⇒ `satisfied: true` ⇒ submitted for approval | — |
| 4 | P0 OCR | Duplicate rows are their own blocking reason and never collapsed to `rows[0]` | **DONE** | `dz_erp/review_policy.py` | `test_phase34…` (19) | 19 passed | this commit |
| 5 | P0 OCR | Decision bound to the exact extraction row set (`evidence_json`) | **DONE** | `dz_erp/intake_ocr.py`, `dz_erp/database.py` | `test_6`, `test_9`, `test_15` | pass; additive nullable column, idempotent migration | this commit |
| 6 | P0 OCR | Explicit reviewer resolution (attributed, re-bindable, no dead-end) | **DONE** | `dz_erp/intake_ocr.py`, `dz_erp/api.py` | `test_6`, `test_6b`, `test_17` | pass; `selected_row_id` threaded through the API | this commit |
| 7 | P0 OCR | Submit / approve / post enforce the same rule | **DONE** | `dz_erp/procurement_stock.py` | `test_10`, `test_11`, `test_12` | pass; posting re-runs the gate and recovers to `NeedsReview` | this commit |
| 8 | P0 OCR | Tenant isolation and concurrency | **DONE** | — | `test_13`, `test_14` | pass; disjoint duplicate sets per tenant; race resolves to one coherent state | this commit |
| 9 | Regression | Phase 29/32/33 + OCR suites after the policy change | **DONE** | — | 7 suites | 196 passed, 1 skipped (interpreter floor) | this commit |
| 10 | Regression | Full suite and coverage after the change | **DONE** | — | `pytest -q --cov=dz_erp` | 815 passed, 20 failed (**same 20**, `diff`-identical set), 7 skipped, 77 % | this commit |
| 11 | Docs | Baseline and completion plan | **DONE** | `docs/PROJECT_BASELINE_AND_COMPLETION_PLAN.md` | — | — | this commit |
| 12 | Docs | This tracker | **DONE** | `docs/PROJECT_COMPLETION_TRACKER.md` | — | — | this commit |
| 13 | P2 Accounting | Arithmetic, rounding, discount order, TTC identity, order independence, sub-centime residue proved by tests | **DONE** | `tests/test_phase36_accounting_and_tax_configuration.py` | 29 tests | 29 passed | this commit |
| 13a | P2 Tax config | Timbre and TVA resolved by effective date from the versioned tables instead of compiled constants; gaps and malformed config refused | **DONE** | `dz_erp/localization.py`, `dz_erp/procurement_stock.py` | same suite | pass; documented in `docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` | this commit |
| 13b | P2 Tax config | Persist the resolved rule version on the invoice and echo it in the posting audit | **DONE** (P40, closes **F-04**) | `dz_erp/localization.py`, `dz_erp/database.py`, `dz_erp/procurement_stock.py` | `tests/test_phase40_timbre_rule_provenance.py` (22) | 22 passed; content hash + append-only ledger + posting re-check | committed |
| 13c | P2 Accounting | Qualified Algerian expert-comptable review of rates, brackets and thresholds | **BLOCKED_EXTERNAL** | — | — | Requires a signed review with the legal reference per rate | — |
| 14 | P5 API | Failed-login throttling per (client, account) and per-request correlation logging | **DONE** | `dz_erp/rate_limit.py`, `dz_erp/api.py` | `tests/test_phase35_api_rate_limiting_and_request_logging.py` (16) | 16 passed; `docs/STAGING_SECURITY_CONFIGURATION.md` §4.2–4.3 | committed |
| 14a | P5 API | Shared-store rate limiting for multi-replica deployment (or proxy-side limit ratified by the operator) | NOT_STARTED | — | — | Current control is per worker and documented as such | — |
| 14b | P5 API | Evict limiter keys so an unauthenticated client cannot grow memory without bound (**F-01**) | **DONE** (P39) | `dz_erp/rate_limit.py`, `dz_erp/api.py` | `tests/test_phase39_bounded_rate_limiting.py` (32) | 32 passed; cap + sweep + no-allocation probes + per-client aggregate | committed |
| 15 | P6 DB | Migration matrix: convergence, idempotency, preservation, interruption safety, atomic rebuild | **DONE** | `tests/test_phase38_migration_matrix.py`, `tests/test_phase41_migration_evidence.py`, `docs/MIGRATION_AND_DATA_SAFETY.md` | 24 + 17 tests | 24 passed; 16 passed / 1 skipped locally (backup round trip runs in CI) | committed |
| 15a | P6 DB | Migration against production-sized data (timing, locking) | **BLOCKED_EXTERNAL** | — | — | Needs a copy of real state | — |
| 16 | P10 Infra | Container build, compose reference, health model, environment template, build-context hygiene | **IMPLEMENTED_NOT_VERIFIED** | `ops/Dockerfile`, `ops/docker-compose.yml`, `ops/erp.env.example`, `.dockerignore` | `tests/test_phase37_deployment_assets.py` (6) | 6 passed for the *properties*; **the image has never been built** — no container runtime here | committed |
| 16a | P10 Infra | Build the image, run the stack, restore a backup into it, rotate a key in it | NOT_STARTED | — | — | Operator/platform-owner step, listed in `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` §7 | — |
| 17 | P12 Docs | Security final audit, threat model, data-protection posture, incident-response plan, deployment guide, backup/DR runbook, migration safety, accounting validation, QA report, executive and technical reports, AR/FR user guides | **DONE** | `docs/` (15 new documents, indexed in the README) | docs-consistency gate | 15 passed | committed |
| 17a | P12 Docs | User guides reviewed by an Arabic-speaking accountant for terminology | NOT_STARTED | — | — | The guides are accurate about behaviour; terminology review is a human step | — |
| 18 | External | Qualified Algerian SCF/tax review | **BLOCKED_EXTERNAL** | — | — | Requires an engaged expert-comptable | — |
| 19 | External | KMS/HSM custody, provenance, off-host checkpoints, alerting, DR, OCR corpus, Law 18-07 | **BLOCKED_EXTERNAL** | — | — | See `docs/RELEASE_READINESS_MATRIX.md` | — |

## Phases 39–41 — remediation of the audit findings

| # | Phase | Task | Status | Files | Tests run | Result | Commit |
|---|---|---|---|---|---|---|---|
| 20 | P39 | F-01: expiry, hard cap, no-allocation probes, per-client aggregate, shared sqlite backend, failure policy, policy endpoint | **DONE** | `dz_erp/rate_limit.py`, `dz_erp/api.py`, `ops/erp.env.example`, `docs/RATE_LIMITING_AND_DEPLOYMENT_SCOPE.md` | `tests/test_phase39_bounded_rate_limiting.py` (32) + Phase 35 (16) | 48 passed; subprocess proves a shared counter; scope still reported as per-process by default | `6fb650d` |
| 20a | P39 | Distributed limiter across separate databases / replicas | **NOT_STARTED** | — | — | No bundled backend; deliberately not claimed | — |
| 21 | P40 | F-04: rule identity, content hash, snapshot, append-only ledger, mutation guards, posting re-check, legacy handling, reversal inheritance | **DONE** | `dz_erp/localization.py`, `dz_erp/database.py`, `dz_erp/procurement_stock.py`, `dz_erp/security_audit.py` | `tests/test_phase40_timbre_rule_provenance.py` (22) | 22 passed | `0df3c33` |
| 22 | P41 | Migration evidence by schema generation: checksums, FKs, indexes, interruption, large tables, backup→restore | **DONE** (structural) / **BLOCKED_EXTERNAL** (production scale) | `tests/test_phase41_migration_evidence.py`, `docs/MIGRATION_AND_DATA_SAFETY.md` | 17 tests | 16 passed, 1 skipped locally by interpreter floor; runs in CI | committed |
| 23 | P42 | Logging and retention review | **DONE** (review) / **BLOCKED_EXTERNAL** (retention period) | `docs/LOG_RETENTION_AND_ACCESS.md` | — | Content verified credential-free; retention is a DPO determination | — |
| 24 | P39/P40 | Full-suite regression | **DONE** | — | `pytest -q --cov=dz_erp` | 960 passed, 20 failed (interpreter floor, same set as baseline), 8 skipped, 79 % coverage | — |

## Where the workstream stands

| Priority | Area | Status |
|---|---|---|
| P0 | OCR duplicate-row integrity | **DONE** — 19 tests |
| P1 | Re-verify Phases 29–33 | **DONE** — 196 passed / 1 skipped (interpreter floor) |
| P2 | Accounting arithmetic + effective-dated tax configuration | **DONE** (mechanism) / **BLOCKED_EXTERNAL** (legal values) — 29 tests |
| P3 | Core modules | **DONE** (pre-existing, re-verified by the full suite) |
| P4 | OCR/AI production quality | **DONE** (pre-existing benchmark and fail-closed policy); authorised corpus **BLOCKED_EXTERNAL** |
| P5 | Security audit + threat model + data protection + incident response; bounded limiter (P39) | **DONE** (documents, control) / **BLOCKED_EXTERNAL** (legal determinations, distributed limiter) |
| P6 | Migrations and data safety | **DONE** (24 + 17 tests) / **BLOCKED_EXTERNAL** (production scale) |
| P7 | Frontend/UX (Arabic RTL, French LTR) | **DONE** (pre-existing suites) / terminology review **NOT_STARTED** |
| P8 | API hardening / OpenAPI | **DONE** — throttling, correlation logging, contract pin |
| P9 | Test pipeline + final QA report | **DONE** — `docs/FINAL_QA_REPORT.md` |
| P10 | Production infra (prepared, never deployed) | **IMPLEMENTED_NOT_VERIFIED** — image never built |
| P11 | Measured benchmarks / concurrency | **DONE** for the benchmark that exists; production-scale concurrency **BLOCKED_EXTERNAL** |
| P12 | Documentation set | **DONE** |
| P17 | Preserve the PR chain; prepare merge order only | **DONE** — nothing merged, nothing closed |

## Independent verification pass — 2026-10-30

Read-only against Git, PR and CI state. Nothing merged, closed, rebased, marked
ready or deployed. Full detail in `docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md`;
what still blocks release in `docs/REMAINING_BLOCKERS.md`.

| # | Check | Status | Evidence |
|---|---|---|---|
| V1 | Six commits, parents, branch, remote head | **DONE** | HEAD = origin = `8b1b56c9`; `rev-list --left-right` = `0 0`; single-parent linear chain |
| V2 | PR containment | **DONE** | commits→pulls API: all six in **PR #7 only** |
| V3 | CI on the exact head | **DONE** | run `41129089741`, `head_sha=8b1b56c9`, 3/3 jobs success; step-level only — artefact download is blocked from this sandbox |
| V4 | Full suite + baseline re-measured | **DONE** | head 890/20/7 (198.91 s) vs baseline `852f389f` 796/20/7 (162.93 s); FAILED lists `diff`-identical; +94 = phase34–38 |
| V5 | P0 duplicate-row integrity, independently | **DONE** | `/tmp/verify_policy.py` 32/32 across cases A–K; gates at `procurement_stock.py:1660/1774/2222`; recovery at `:2114` |
| V6 | Rate limiter deployment scope | **DONE** | process-local `_events`, self-described `per-process`, `replicas: 1` ⇒ **staging-only**; new defect **F-01** |
| V7 | Request logs free of credentials | **DONE** | `/tmp/verify_logs.py` 7/7; fields = method/path(status query-stripped)/status/duration/client_ip (+ auth username, peer_ip, code, detail) |
| V8 | Effective-dated TVA/Timbre | **DONE** | `/tmp/verify_tax.py` 33/33; thirteen tax assumptions listed for expert-comptable review |
| V9 | Container build | **IMPLEMENTED_NOT_VERIFIED** | no container runtime or socket; prerequisites named in the report §9 |
| V10 | Migration / backup / security docs / release gates | **DONE** | 24 migration tests; backup verified in CI only; 15-row matrix = 6 Verified + 9 Blocked; docs path scan: zero dangling references |
| V11 | PR chain state, read-only | **DONE** | #3 non-draft → #7 draft; all CLEAN; every head 3/3 checks green; unchanged by this pass |

## PR stack review (Phases 29–42, PRs #3–#7)

Strict sequential review of the whole stack, performed from the remote and the
CI API rather than from earlier reports. Full record:
`docs/PR_STACK_REVIEW.md`; execution procedure:
`docs/PR_MERGE_EXECUTION_PLAN.md`. Nothing was merged, closed, rebased,
retargeted, marked ready or deployed.

| # | Check | Status | Evidence |
|---|---|---|---|
| S1 | Remote state re-derived (`main`, every branch head, ancestry) | **DONE** | `main` `4fc1364a`; each PR head a strict descendant of the previous; no merge commits, no rewritten history |
| S2 | Base/head relationships correct | **DONE** | API `base.sha` equals previous PR head for all five; pairwise `merge-base --is-ancestor` all YES |
| S3 | CI on the exact head of every PR | **DONE** | #3 `40763416753`, #4 `40775473917`, #5 `40965006912`, #6 `41064213366`, #7 `41142731746` — all `success` 3/3; step-level only (artefact download blocked here) |
| S4 | Per-PR regression evidence | **DONE** | isolated worktrees at every head: 695/698/708/719/960 passed; failure **set** `diff`-identical to #7 at all four (same 20 interpreter-floor tests) |
| S5 | Full diff reviewed for each PR | **DONE** | 20 / 2 / 10 / 10 / 53 files inspected; architecture, authz, tenant scope, migrations, transaction boundaries, audit persistence, security |
| S6 | Tests assert business behaviour | **DONE** | gates, refusals, audit rows, state transitions and money outcomes asserted; no test deleted or weakened (every removed `def test_` still exists at HEAD; changed assertions got stricter) |
| S7 | Findings raised | **DONE** | R-01 (stale backend-error flag mislabels refusals), R-02 (no concurrent-writer test for the shared limiter), R-03 (`blocked()` takes a write lock), R-04 (unbounded `X-Request-Id`), R-05 (no audited tax-rule configuration path), R-06 (two docs claim the rule label is not persisted), R-07 (interpreter-gated tests are unverified locally), F-06a (trigger drop/recreate window) |
| S8 | Merge order verified | **DONE** | #3 → #4 → #5 → #6 → #7; no mismatch |
| S9 | Merge execution plan | **DONE** (unan executed) | per-PR base/head/CI/actions; the base-retargeting trap documented |
| S10 | Human review on any PR | **BLOCKED** | 0 reviews on #3–#7 |
| S11 | Branch protection / required checks | **BLOCKED (unknown)** | protection endpoint `403` for the available token; rulesets `[]` |
| S12 | Authorization to merge | **BLOCKED** | not granted; this pass is review-only |

Status of the stack: **reviewed, not merged, not release-ready.** No PR is
production-ready and none may be merged without G1–G7 in the execution plan.

## Standing rules applied to every row

* No test was weakened, skipped, deleted or had an assertion relaxed. The three
  Phase 32 characterisation tests were *converted* into resolution assertions.
* Environment failures are never counted as passes: the 20 interpreter-floor
  failures are reported as failures here and are green only in CI.
* No accounting or tax rule was changed. No posted record is rewritten. No
  migration deletes or merges evidence.
* Commits go to `arena/7f12d627-erp` only; no force-push, no merge, no deploy.

---

## S8. Findings remediated (Phase 45) — 2026-10-31

| Row | Work | Status | Evidence |
|---|---|---|---|
| S8a | **R-01** — clear the throttle's backend error on success; distinguish current from historical failure | **DONE** (`FIXED_AND_VERIFIED`) | `dz_erp/rate_limit.py`; 4 dedicated tests + fail-closed test in `test_phase43_limiter_hardening.py` |
| S8b | **R-02** — real concurrent-writer coverage on the shared SQLite backend | **DONE** (`FIXED_AND_VERIFIED`) | multi-process writer / boundary / cleanup / lock-contention / interruption / cardinality tests; `hit()` waited 2036 ms behind a held lock and succeeded |
| S8c | **R-03** — a read-only throttle check that does not take the global write lock | **DONE** (`FIXED_AND_VERIFIED`, bounded) | `ERPDatabase.read_probe()`; 0.794 ms vs 2.331 ms; contention reduced, not eliminated (RLock still held) |
| S8d | **R-04** — bound and validate `X-Request-Id` | **DONE** (`FIXED_AND_VERIFIED`) | `dz_erp/request_id.py`; `test_phase47_request_id_boundary.py` (19 tests) |
| S8e | **R-05** — audited, authorized timbre rule administration | **DONE** (`FIXED_AND_VERIFIED`) for the mechanism; **BLOCKED_EXTERNAL** for the values | `localization.create_timbre_rule_version` / `approve_timbre_rule_version`; 3 endpoints; `test_phase46_timbre_rule_administration.py` (35 tests) |
| S8f | **R-06** — retire the false "rule label is not persisted" claim | **DONE** (`FIXED_AND_VERIFIED`) | `ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §7, `TECHNICAL_REPORT.md`, `RISK_REGISTER.md`, `ALGERIAN_SCF_LOCALIZATION_SPEC.md` §3 |
| S8g | **R-07** — verify safe backup/restore on the supported interpreters | **DONE** (`FIXED_AND_VERIFIED`) for the rule; **BLOCKED_EXTERNAL** for this host | one `safe_restore_supported` predicate; `test_phase48_restore_interpreter_matrix.py` (32 + 1 gated); 3.12.0-3.12.9 now correctly refused |
| S8h | Concurrent start-up migration race (found during S8b) | **DONE** (`FIXED_AND_VERIFIED`) | one `BEGIN IMMEDIATE` around the migration sequence; 5-process regression test |
| S8i | Row 13b (rule identity persisted) | already `DONE` — the docs said otherwise; see S8f | Phase 40, 22 tests |

**Not done, and not implied by the above:** production-scale timing, a container
build, a deployment, multi-replica rate limiting, and every legal/tax
determination. Those rows are untouched (`IMPLEMENTED_NOT_VERIFIED` /
`BLOCKED_EXTERNAL`) and are listed in `docs/REMAINING_BLOCKERS.md`.

### S8 evidence index

| Claim | Where it is reproducible |
|---|---|
| R-01/R-02/R-03 | `tests/test_phase43_limiter_hardening.py` (17) |
| R-04 | `tests/test_phase47_request_id_boundary.py` (19) |
| R-05 | `tests/test_phase46_timbre_rule_administration.py` (35) |
| R-06 | `docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §7 + docs-consistency suite |
| R-07 | `tests/test_phase48_restore_interpreter_matrix.py` (32 + 1 gated) |
| start-up defects | `tests/test_phase49_audit_key_bootstrap_race.py` (7) + the migration-race test |
| CI on the exact head | run 41196478922 (3/3 green), recorded in `docs/PR_STACK_REVIEW.md` §7.3 |

---

## S9. Final human review preparation (2026-10-31)

| Row | Work | Status | Evidence |
|---|---|---|---|
| S9a | Re-derived the final state from GitHub, not from the remediation report | **DONE** | head `07e65782edc4d43b6b3952c699cb2d8d1f1769e4` = remote head, clean tree byte-identical, no commits after the reported state; CI 41198403599 (3/3) |
| S9b | Security and data-integrity re-review with independent probes | **DONE** | audit-key atomic publish (ext4, `os.link`, 20 polls never saw an empty file; 96/96 starts on the no-hardlink fallback with one key per DB), 180/180 concurrent migrations, limiter `allowed=40/limit 40` with 180 rows and 0 violations, tax endpoints 403/401/SoD/tenant-isolation probes, `TIMBRE_RULE_*` audit events with a valid chain, historical duty unchanged after a new rule version |
| S9c | Test integrity | **DONE** | 535 → 859 test functions collected, **0 removed**; 24 deleted lines in `tests/` all traced (6 signature wraps, 4 assertions replaced by stronger filtered ones, 1 count pin 26 → 33, comment edits); base **671P/20F/6S** vs head **1070P/20F/9S** with an **identical 20-failure list** |
| S9d | PR stack verified | **DONE** | five heads, all green 3/3 on the exact head, all `MERGEABLE/CLEAN`, **0 reviews each**; ancestry linear, 0 merge commits |
| S9e | Merge-mechanics trap verified | **DONE** | `delete_branch_on_merge=false` → GitHub will **not** auto-retarget; manual retarget is mandatory; merge commit (not squash) required |
| S9f | Reviewer checklist per PR | **DONE** | `docs/PR_STACK_REVIEW.md` §8.5 |
| S9g | Residual risks recorded | **DONE** | R-08 (no directory fsync after the key link — availability only, fails loud), R-09 (fallback publish window, retry-protected), F-06a improved, #5/#6 nits still open |

**Status: `READY_FOR_HUMAN_REVIEW`.** Nothing merged, retargeted, closed, rebased,
marked ready or deployed. Human review (0 of 5 PRs reviewed) is the gate that
remains; `NOT_PRODUCTION_READY` still applies for the external reasons in
`docs/REMAINING_BLOCKERS.md`.

---

## S10. Second review pass — accounting, recovery and idempotency (2026-10-31)

Round 1 (§S9) produced `READY_FOR_HUMAN_REVIEW`. This pass re-ran the review
from scratch on the current head and **supersedes the §S9 status line**: two
money-affecting defects were reproduced that §S9 did not record. Full detail,
repro output and required regression tests are in `docs/PR_STACK_REVIEW.md` §9.

| Row | Work | Status | Evidence |
|---|---|---|---|
| S10a | State re-verified on the actual head | **DONE** | `b679bc496b8053384ab40bcbd86c5db474140f42` = remote head, clean tree (`§S9` recorded `07e65782`; the docs commit advanced it); all five PR heads 3/3 green, `MERGEABLE/CLEAN`, 0 reviews, bases still a strict one-step chain |
| S10b | Accounting re-derived end to end (not by reading code) | **DONE** | PO→GRN→invoice: `31` +10,000.00 / `380` −10,000.00, then `380` +10,000.00 / `4456` +1,900.00 / `401` −11,900.00 → clearing nets **0.00**, balanced; G50 CASH timbre `E2 = 11.90`, `E3B91 = 190.00`, `E3B110` identity holds, payable/carry mutually exclusive; reversal posts `RAVOIR/2026/00001` dated in the issue period and nets in **October's** G50 |
| S10c | New findings | **DONE** | **F-R2-1 High** — spec says the 2 % timbre band is capped at 10,000.00 (`docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md:66`) but neither `_builtin_timbre_rule()` (`localization.py:550`) nor the seeded row sets `max_amount_dzd`; 600,000.00 base declaring the cap is **refused**. **F-R2-2 Medium** — `procurement_stock.py:2514`/`:2727` replay a `REVERSE_INVOICE` key without comparing `request_sha256` (contrast `:2236`/`:3133`): a different reason is silently reported as a successful replay. **F-R2-3 Medium** (pre-existing, identical at base) — a within-tolerance price difference leaves a permanent balance on `380` with no variance posting. **F-R2-4 Low** — negative amounts validate and post. **F-R2-5/6 Low** — PR #6's `>= 10` row bound vs 16 actual rows; PR #5's unreachable `None` branch |
| S10d | R-08 / R-09 re-investigated behaviourally | **DONE, still open** | missing key + existing events → `AuditKeyUnavailableError`, **no key minted**, chain valid again once restored; empty key → `KeyMaterialUnavailable`, file untouched; slow publisher → full secret, never partial; stray `.tmp` ignored. The missing directory `fsync` is unchanged and no test pins these behaviours, so **neither closes without a fix + tests** |
| S10e | Test integrity at this head | **DONE** | CPython 3.11.2: **20 failed / 1070 passed / 9 skipped** in 287.66 s, the same 20 as base and as CI; every failure is the interpreter gate ("requires Python 3.11.17+ or 3.12.10+ …"), not the stack. Nothing skipped, weakened or deleted |
| S10f | Tenant / authz / logging / limits re-probed | **DONE** | every list endpoint scoped; cross-tenant direct ids → 403; `password`/`token`/RIB redacted, raw RIB absent from the serialized record; invalid limiter mode → `fail_closed`. The earlier `/partners` `LEAK` was a **false positive** (tenant B's own partner is also named `SPARTE`) |
| S10g | Per-PR independent verdict | **DONE** | #3 ACCEPT · #4 ACCEPT · #5 ACCEPT with nit · #6 ACCEPT with nit · #7 ACCEPT with findings — the per-PR diffs pass; the **stack as a shipping candidate** does not (F-R2-1, F-R2-2) |

**Status: `CHANGES_REQUIRED`.** F-R2-1 is **BLOCKED_EXTERNAL** as to *direction*
(a documented tax ceiling the code does not apply — the accountant of record must
supply the rule text in force; the figure must not be invented), with a one-line
fix either way plus a regression test at a 600,000.00 base. F-R2-2 is a contained
fix at two call sites with a clear test. Nothing was merged, retargeted, closed,
rebased, force-pushed, marked ready or deployed to produce this pass, and no
product code was modified.

---

## S11. Round 3 — targeted remediation (2026-10-31)

Supersedes the §S10 status line. §S10 recorded two blockers; this round fixed the
one that needed no external decision, prepared the other for one, and closed the
two review nits. Detail and evidence: `docs/PR_STACK_REVIEW.md` §10.

| Row | Finding | Disposition | Evidence |
|---|---|---|---|
| S11a | **F-R2-2** reversal idempotency | **FIXED** | `request_sha256` compared at both `REVERSE_INVOICE` sites (`procurement_stock.py:2514`, `:2737`); mismatch → `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH`, HTTP 409, audited `REVERSAL_ATTEMPT_REFUSED_IDEMPOTENCY_MISMATCH`; 3 tests incl. concurrency (1 credit note, 3 replays) |
| S11b | **F-R2-1** timbre ceiling | **BLOCKED_EXTERNAL** (LB-1), mechanism FIXED | spec now states the code applies no ceiling and names the decision; `TIMBRE_RULE_CAP_NOT_DECLARED` forces an explicit cap declaration; doc/code guard test verified to fail on a reintroduced claim; boundaries pinned at 100,000.00/100,000.01/500,000.00/600,000.00 |
| S11c | **F-R2-3** clearing 380 | **mechanism FIXED**, policy BLOCKED_EXTERNAL (LB-2) | `products.price_variance_account_code` (nullable, no default, additive migration); invoice debit split receipt-cost / variance; `380` nets to 0.00 when configured; unconfigured residual named + audited as `PRICE_VARIANCE_UNALLOCATED`; 10 tests |
| S11d | **F-R2-4** negative amounts | **FIXED** | non-positive price and negative headers refused at validation, on edit and at posting; zero stays valid; credit notes unaffected (4 tests) |
| S11e | **F-R2-5 / F-R2-6** nits | **CLOSED** | exact 16-row pin + required-field names; `None` checked before parsing with distinct `..._SOURCE_MISSING` codes (1 test) |
| S11f | Verification | **DONE** | full suite 20F/1092P/9S (was 20F/1070P/9S — +22 tests, same 20 interpreter-gate failures); `ruff` clean; risk register consistent at 45 rows; docs consistency 15 passed |

**Status: `CHANGES_REQUIRED` → `READY_FOR_HUMAN_APPROVAL` for the code, with two
external decisions outstanding.**

What that means precisely: every defect this review could fix without inventing a
tax figure or an accounting policy is fixed and tested. What remains is **not**
engineering work:

* **LB-1** — whether the top stamp-duty band is capped, and at what figure
  (accountant of record). The code needs no change when the answer arrives: a new
  rule version through the audited two-person path is enough.
* **LB-2** — which SCF account carries a purchase price variance, and whether the
  policy is "book on invoice" or "revalue stock" (accountant of record). The
  mechanism is in place; only the account is missing.

`NOT_PRODUCTION_READY` still applies, for those two decisions plus the
deployment items that predate this round. **0 of 5 PRs** carry a human review.
Nothing was merged, retargeted, closed, rebased or deployed.

### S11g — delivery and CI

| Item | Value |
|---|---|
| Code commit (F-R2-2/3/4/5/6, schema column, tests) | `9d5d112a7d0bc7a7549b809d14127b54ed46c616` |
| CI on that commit | run `41239621096` — Windows ✅, 3.12 ✅, **3.11 ❌** on `test_phase49_audit_key_bootstrap_race` (`database is locked`) |
| Final commit | `7be86d1…` (docs on top; code identical to `9d5d112a`) |
| CI on the fresh run | run `41241542402` — **3/3 success** |
| F-R3-1 | intermittent, pre-existing, **not reproduced** (12/12 local runs under load, 15×8-process trials 0 failures); the `journal_mode = WAL` hypothesis was measured and disproved; disclosed in `docs/PR_STACK_REVIEW.md` §10.8–10.9 rather than patched speculatively |
