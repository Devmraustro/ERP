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
| 13 | P2 Accounting | Verify arithmetic/rounding/period invariants are covered by executable tests | NOT_STARTED | — | — | — | — |
| 14 | P5 API | Rate limiting and request logging | NOT_STARTED | — | — | — | — |
| 15 | P6 DB | Migration matrix (fresh / upgrade / interrupted / restore) documented per migration | NOT_STARTED | — | — | — | — |
| 16 | P10 Infra | Container build, compose, health checks, environment template | NOT_STARTED | — | — | — | — |
| 17 | P12 Docs | Security, data-protection, threat-model, incident-response, deployment, DR, user guides | NOT_STARTED | — | — | — | — |
| 18 | External | Qualified Algerian SCF/tax review | **BLOCKED_EXTERNAL** | — | — | Requires an engaged expert-comptable | — |
| 19 | External | KMS/HSM custody, provenance, off-host checkpoints, alerting, DR, OCR corpus, Law 18-07 | **BLOCKED_EXTERNAL** | — | — | See `docs/RELEASE_READINESS_MATRIX.md` | — |

## Standing rules applied to every row

* No test was weakened, skipped, deleted or had an assertion relaxed. The three
  Phase 32 characterisation tests were *converted* into resolution assertions.
* Environment failures are never counted as passes: the 20 interpreter-floor
  failures are reported as failures here and are green only in CI.
* No accounting or tax rule was changed. No posted record is rewritten. No
  migration deletes or merges evidence.
* Commits go to `arena/7f12d627-erp` only; no force-push, no merge, no deploy.
