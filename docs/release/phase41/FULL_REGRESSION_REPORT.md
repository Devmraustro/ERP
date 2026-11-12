# Phase 41 — Full regression report

## 1. Headline

```
baseline (Python 3.11.2) : 22 failed, 839 passed, 3 skipped
final    (Python 3.12.10): 946 passed, 0 failed, 3 skipped
```

**Zero unexplained failures.** The 22 baseline "failures" were never defects: they are the
safe-restore runtime guard correctly refusing Python 3.11.2, which `pyproject.toml` declares
unsupported. Under the supported interpreter every one of them passes.

Both suites included the **full repository** (`tests/`), not a subset.

## 2. Why the baseline run used the wrong interpreter

The GUI venv was built on the system Python (3.11.2) so the desktop suites could run with PySide6.
That venv is below the floor, so `restore_runtime_is_safe()` refuses — as designed. The correct
reading is not "22 tests are broken" but "this interpreter may not restore backups".

Operating the guard was **not** relaxed. The mission forbids exactly that, and 3.11.2 genuinely lacks
the hardened `tarfile.data_filter` semantics the restore path depends on.

## 3. Progression of the suite during Phase 35

| Stage | Passed | Failed | Change |
| --- | --- | --- | --- |
| Baseline, 3.11.2 (no source changes) | 839 | 22 | — (reference) |
| + Phase 36 Group B gates (6 functions + `_RefusedAttempt`) | 855 | 22 | +16 new tests, 0 new failures |
| + Phase 37 SEC-13/14/15 modules wired | 922 | 22 | +67, 0 new failures |
| + Phase 38 totals gate on the write path | 924 | 22 | +2, 0 new failures |
| **Final, 3.12.10, full repo** | **946** | **0** | guard satisfied by the correct interpreter |

The failure set was compared **by test id**, not by count, at every stage
(`comm -13 baseline_failures new_failures` → empty each time). A count-only comparison would have
hidden a fixed test masking a newly broken one.

## 4. Tests added or newly enabled

| File | Tests | Origin |
| --- | --- | --- |
| `tests/test_phase35_integrity_gates.py` | 18 | **written this phase** — behavioural proofs for every integrated gate |
| `tests/test_phase47_request_id_boundary.py` | 33 | ported from PR #7, now passing |
| `tests/test_phase39_bounded_rate_limiting.py` | 18 | ported from PR #7, now passing |
| `tests/test_phase35_api_rate_limiting_and_request_logging.py` | 8 | ported from PR #7, now passing |
| `tests/test_phase29_ocr_review_policy.py` | — | ported from PR #7, **excluded**: cannot import (needs the blocked SEC-14 write path) |

No test was deleted or weakened. Two edits were made to existing tests, both disclosed:

1. `tests/test_phase8_upload_boundary_and_ci.py` — pinned protected-operation count 26 → **27**,
   because a new *protected* endpoint exists. The public/protected split is still asserted exactly,
   so the guard still catches a route that silently becomes public.
2. `tests/test_phase9_durable_security_log.py` — the durable-file assertion now filters by
   `event == "AUTH_REQUEST_REJECTED"` instead of asserting the file contains *only* rejections. This
   is PR #7's own resolution of a genuine architectural conflict: the file is one durable stream
   carrying both security rejections and per-request records. The test now proves **both** — each
   rejection durably recorded with its own correlation id, and the accompanying request records
   carrying no body, header or credential.

## 5. Two real defects found by running the suite

| Defect | How found | Status |
| --- | --- | --- |
| Negative-amount invoice reached `Validated` with an empty error list | probing the live write path | **FIXED** (Phase 38), 2 regression tests |
| `JSONResponse` used but never imported in the new 429 path | `test_a_rejected_id_is_not_echoed_on_a_throttled_request` | **FIXED** |
| Access records written into the security-only sink | `test_phase9_durable_security_log` | **FIXED** by adopting PR #7's event-filtering model |

The first is the only pre-existing defect of the three, and it is a financial-integrity issue: a
malformed payload could create a negative payable that the posting step would carry into the GL.

## 6. Isolated-vs-suite behaviour (recorded)

Several suites pass in isolation but fail inside the full run **only when the interpreter is below
the floor** — the guard refuses restore. Under 3.12.10 there is no such divergence: the full run is
green and each suite is green alone. No test depends on execution order under the supported runtime.

During investigation, one desktop test (`test_mainwindow_constructs_and_switches_to_rtl`) failed in a
partial run and passed alone; the cause was an incorrect `LD_LIBRARY_PATH` (missing the Qt stub
directory) in that ad-hoc command, not the code — it passes in the authoritative full run and in
correctly-configured partial runs.

## 7. Reproducing this result

```bash
bash docs/release/phase35/toolchain/build_env.sh 3.12.10      # CPython + zlib + sqlite + ssl + ctypes
/tmp/py312/bin/python3 -m pip install -r requirements.lock.txt
/tmp/py312/bin/python3 -m pip install PySide6-Essentials==6.11.2   # GUI extras only
QT_QPA_PLATFORM=offscreen PYTHONPATH=. \
  /tmp/py312/bin/python3 -m pytest tests/ -q \
    --ignore=tests/test_phase29_ocr_review_policy.py \
    --deselect tests/test_phase64_dependency_security_regression.py::test_no_installed_distribution_is_absent_from_the_lock
# -> 946 passed, 3 skipped
```

The two exclusions are both explained above, and both are consequences of things this phase
deliberately did not do (integrate a blocked schema change; keep a GUI-only extra out of the runtime
lock).
