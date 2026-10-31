# Final QA report

**Commit under test:** `42334da` on `arena/7f12d627-erp` (plus the documentation
commits that follow it) · **Branch base:** `852f389f` · **`main`:** `4fc1364`

This report states what was executed and what it produced. Where a number comes
from CI rather than this host, it says so; where something was not run, it says
that instead of implying a pass.

---

## 1. Suites executed on this host

Environment: Linux, CPython **3.11.2**, pytest 8.3.5, coverage 6.0.0, ruff 0.14.0,
hash-locked dependencies, `cv2` 4.11.0 + RapidOCR importable.

| Run | Command | Result |
|---|---|---|
| Full suite (baseline, before this work) | `pytest -q --cov=dz_erp` | 796 passed, 20 failed, 7 skipped, 77 %, 187 s |
| Full suite (after Phases 34–36) | idem | 890 passed, 20 failed, 7 skipped, 78 %, 232 s |
| Duplicate-row integrity | `pytest tests/test_phase34_ocr_duplicate_row_integrity.py` | **19 passed**, 4.43 s |
| API throttling and request logging | `pytest tests/test_phase35_api_rate_limiting_and_request_logging.py` | **16 passed**, 2.28 s |
| Accounting and tax configuration | `pytest tests/test_phase36_accounting_and_tax_configuration.py` | **29 passed**, 1.84 s |
| Deployment assets | `pytest tests/test_phase37_deployment_assets.py` | **6 passed**, 0.04 s |
| Migration matrix | `pytest tests/test_phase38_migration_matrix.py` | **24 passed**, 1.98 s |
| Regression: Phase 29/32/33/OCR/Arabic/docs | 7 suites | 196 passed, 1 skipped |
| Regression: API/auth/desktop | 6 suites | 99 passed, 1 failed (interpreter floor) |
| Static analysis | `ruff check dz_erp tests tools` | All checks passed |
| Byte-compilation | `python -m compileall -q dz_erp tests tools` | clean |
| Risk register consistency | `python tools/risk_register_counts.py` | consistent |
| Dependency graph | `python tools/verify_dependency_graph.py` | consistent (CI step) |

## 2. The 20 failures, named honestly

Every one of them is the same cause:

```
dz_erp.database.FinancialIntegrityError: Safe backup restore requires
Python 3.11.17+ or Python 3.12.10+ (and below 3.13)
```

This host runs CPython 3.11.2, below the floor at which Python's `tarfile` filters
are safe to use. The restore **refuses** rather than extracting unsafely. The
failure set was captured before any change and compared with `diff` after every
phase: **identical to baseline, all 20**. (Phase 35 briefly added a 21st — an older
test counted records in the durable sink, which now also receives `HTTP_REQUEST`
entries; it was rewritten to assert by event and to add that request records are
credential-free, a strengthening, and the set returned to exactly the baseline.)

These same paths are **green in CI on the published head**, where CPython 3.11.17
and 3.12.15 are used. Neither result is hidden and neither is presented as the
other.

## 3. CI

| Run | Head | Result |
|---|---|---|
| 41093527978 | `852f389f` (previous published head) | 3/3 jobs success |
| 41124627918 / 41124878394 | `09918a05` / `49b9201c` | **cancelled** — superseded by a later push on the same branch (concurrency), not a failure |
| **41127176839** | `edbd1d8e` (final head) | **3/3 jobs success** — full suite with coverage on CPython 3.11.17 and 3.12.15, plus the Windows rotation/backup/staging job. Every step in each job concluded `success` (`gh api …/jobs`), including "Run the complete test suite with coverage" and "Run isolated synthetic staging acceptance" |

The cancellation pattern is stated because a cancelled run can be mistaken for a
green one. The authoritative run is the one on the final head, and it is green:
**41127176839** on `edbd1d8e`. That is the direct answer to the 20 local failures —
the same commit, the same suite, on an interpreter where the safe-restore path is
permitted, and every step succeeds.

## 4. Coverage

78 % overall on this host (77 % at baseline). The modules touched by this work:

| Module | Coverage |
|---|---|
| `dz_erp/localization.py` | 97 % |
| `dz_erp/review_policy.py` | 91 % |
| `dz_erp/intake_ocr.py` | 90 % |
| `dz_erp/database.py` | 93 % |
| `dz_erp/procurement_stock.py` | 87 % |
| `dz_erp/api.py` | 86 % |

Coverage is reported as measured. It is not a quality claim: the failures that
mattered in Phase 34 (a flag raised but never consulted) were in code that
*was* covered.

## 5. Functional verification beyond unit tests

| Check | Result |
|---|---|
| Reproduce the duplicate-row fail-open **before** the fix | Reproduced: satisfied `true`, submission `PendingApproval` with `5555.00` unreviewed |
| Same path **after** the fix | `DUPLICATE_EXTRACTION_ROWS:amount_ht_dzd` blocks submission; explicit resolution with evidence unblocks; invoice posts once and balances |
| Posting-time refusal recovery | Approval invalidated (`SOURCE_REVIEW_INVALID_AT_POSTING`), invoice + document returned to `NeedsReview`, nothing posted, audit chain valid |
| Throttle before password verification | Correct password returned 429 once throttled (the verification never ran) |
| Effective-dated tax resolution | A 2026 invoice refuses a 9 % line when the configured 2026 table has no 9 % |
| Migration convergence | 14 columns restored individually; second open byte-identical schema |

## 6. Not executed (and therefore not claimed)

| Item | Why |
|---|---|
| Container build / `docker compose up` | No container runtime in this environment |
| Staging acceptance against a real host (40 checks) | Interpreter floor: 38/40 pass here, 2 cannot run |
| Production-scale load, concurrency and DR drills | No production-sized dataset |
| Legal/tax qualification of rates and brackets | Requires an Algerian expert-comptable |
| Law 18-07 / ANPDP compliance evidence | Requires legal counsel |
| KMS/HSM integration | Requires a provider |
| Penetration test | Not performed |

## 7. Release-readiness verdict

**Engineering-controllable work: implemented and verified.** Every defect found in
this workstream (P34-F1–F3, P35-F1–F2, P36-F1–F2, P38-F1) is fixed with a
regression test, and the regression set is identical to baseline plus the intended
additions.

**Not production-ready**, for reasons no code change in this repository can fix:
external key custody, off-host evidence delivery, production-scale DR and
concurrency, a qualified Algerian tax review, and data-protection determinations.
`docs/RELEASE_READINESS_MATRIX.md` is the authoritative table; it is unchanged in
its verdict.
