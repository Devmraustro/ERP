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
| 13b | P2 Tax config | Persist the resolved rule version on the invoice and echo it in the posting audit | NOT_STARTED | — | — | Mechanically resolved today, only not stored | — |
| 13c | P2 Accounting | Qualified Algerian expert-comptable review of rates, brackets and thresholds | **BLOCKED_EXTERNAL** | — | — | Requires a signed review with the legal reference per rate | — |
| 14 | P5 API | Failed-login throttling per (client, account) and per-request correlation logging | **DONE** | `dz_erp/rate_limit.py`, `dz_erp/api.py` | `tests/test_phase35_api_rate_limiting_and_request_logging.py` (16) | 16 passed; `docs/STAGING_SECURITY_CONFIGURATION.md` §4.2–4.3 | committed |
| 14a | P5 API | Shared-store rate limiting for multi-replica deployment (or proxy-side limit ratified by the operator) | NOT_STARTED | — | — | Current control is per worker and documented as such | — |
| 15 | P6 DB | Migration matrix: convergence, idempotency, preservation, interruption safety, atomic rebuild | **DONE** | `tests/test_phase38_migration_matrix.py`, `docs/MIGRATION_AND_DATA_SAFETY.md` | 24 tests | 24 passed | committed |
| 15a | P6 DB | Migration against production-sized data (timing, locking) | **BLOCKED_EXTERNAL** | — | — | Needs a copy of real state | — |
| 16 | P10 Infra | Container build, compose reference, health model, environment template, build-context hygiene | **IMPLEMENTED_NOT_VERIFIED** | `ops/Dockerfile`, `ops/docker-compose.yml`, `ops/erp.env.example`, `.dockerignore` | `tests/test_phase37_deployment_assets.py` (6) | 6 passed for the *properties*; **the image has never been built** — no container runtime here | committed |
| 16a | P10 Infra | Build the image, run the stack, restore a backup into it, rotate a key in it | NOT_STARTED | — | — | Operator/platform-owner step, listed in `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` §7 | — |
| 17 | P12 Docs | Security final audit, threat model, data-protection posture, incident-response plan, deployment guide, backup/DR runbook, migration safety, accounting validation, QA report, executive and technical reports, AR/FR user guides | **DONE** | `docs/` (15 new documents, indexed in the README) | docs-consistency gate | 15 passed | committed |
| 17a | P12 Docs | User guides reviewed by an Arabic-speaking accountant for terminology | NOT_STARTED | — | — | The guides are accurate about behaviour; terminology review is a human step | — |
| 18 | External | Qualified Algerian SCF/tax review | **BLOCKED_EXTERNAL** | — | — | Requires an engaged expert-comptable | — |
| 19 | External | KMS/HSM custody, provenance, off-host checkpoints, alerting, DR, OCR corpus, Law 18-07 | **BLOCKED_EXTERNAL** | — | — | See `docs/RELEASE_READINESS_MATRIX.md` | — |

## Where the workstream stands

| Priority | Area | Status |
|---|---|---|
| P0 | OCR duplicate-row integrity | **DONE** — 19 tests |
| P1 | Re-verify Phases 29–33 | **DONE** — 196 passed / 1 skipped (interpreter floor) |
| P2 | Accounting arithmetic + effective-dated tax configuration | **DONE** (mechanism) / **BLOCKED_EXTERNAL** (legal values) — 29 tests |
| P3 | Core modules | **DONE** (pre-existing, re-verified by the full suite) |
| P4 | OCR/AI production quality | **DONE** (pre-existing benchmark and fail-closed policy); authorised corpus **BLOCKED_EXTERNAL** |
| P5 | Security audit + threat model + data protection + incident response | **DONE** (documents) / **BLOCKED_EXTERNAL** (legal determinations) |
| P6 | Migrations and data safety | **DONE** (24 tests) / **BLOCKED_EXTERNAL** (production scale) |
| P7 | Frontend/UX (Arabic RTL, French LTR) | **DONE** (pre-existing suites) / terminology review **NOT_STARTED** |
| P8 | API hardening / OpenAPI | **DONE** — throttling, correlation logging, contract pin |
| P9 | Test pipeline + final QA report | **DONE** — `docs/FINAL_QA_REPORT.md` |
| P10 | Production infra (prepared, never deployed) | **IMPLEMENTED_NOT_VERIFIED** — image never built |
| P11 | Measured benchmarks / concurrency | **DONE** for the benchmark that exists; production-scale concurrency **BLOCKED_EXTERNAL** |
| P12 | Documentation set | **DONE** |
| P17 | Preserve the PR chain; prepare merge order only | **DONE** — nothing merged, nothing closed |

## Standing rules applied to every row

* No test was weakened, skipped, deleted or had an assertion relaxed. The three
  Phase 32 characterisation tests were *converted* into resolution assertions.
* Environment failures are never counted as passes: the 20 interpreter-floor
  failures are reported as failures here and are green only in CI.
* No accounting or tax rule was changed. No posted record is rewritten. No
  migration deletes or merges evidence.
* Commits go to `arena/7f12d627-erp` only; no force-push, no merge, no deploy.
