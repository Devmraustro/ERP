# Phase 59H — Final Regression and Release Candidate

**Repository:** `https://github.com/Devmraustro/ERP.git`
**Branch:** `arena/63d762c8-erp`
**Base commit of the branch:** `098483a1f02df0308f3889c93714efab5d13b550`
**Evidence collected on:** 2026-11-13/14, sandbox, CPython **3.11.2**
**Status:** all gates below were executed; **the suite is not fully green in this sandbox** and the
reason is stated precisely in §3 rather than hidden.

---

## 1. Gate results

| Gate | Command | Result |
|---|---|---|
| Static analysis | `python -m ruff check dz_erp tests tools` | **PASS** — `All checks passed!` |
| Byte-compile | `python -m compileall -q dz_erp tests tools` | **PASS** — exit 0 |
| Dependency graph | `python tools/verify_dependency_graph.py` | **PASS** — `OK — the environment matches the declared dependency graph` (58 distributions, only the documented `opencv-python → opencv-python-headless` substitution) |
| Lockfile integrity | `python tools/generate_hash_lock.py --check` | **PASS** — `OK - 53 pinned distributions, 1317 hashes, no drift` |
| Desktop lockfile | `python tools/generate_hash_lock.py --desktop --check` | **PASS** — `OK - 4 pinned distributions, 20 hashes, no drift` |
| Dependency audit | `pip-audit -r requirements.lock.txt --no-deps` | **PASS** — `No known vulnerabilities found` |
| `pip check` | `pip check` | **1 expected warning** — `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed` (documented deliberate substitution; see §5) |
| Full test suite | `python -m pytest -q -ra` | **22 failed, 1012 passed, 3 skipped** — every failure is the CPython floor (§3) |
| Desktop suites | 5 files, offscreen Qt | **55 passed, 0 skipped, 0 failed** |
| Isolated staging acceptance | `python tools/staging_acceptance.py` | **38 / 40** — the 2 failures are the same floor (§3.2) |
| Security suites | phases 7/9/15/17/34/35/39/64 | **247 passed**, 2 skipped (Windows-only paths) |
| Migration / rotation | phases 10/11/12/13 | see §3 — 13 of these are floor-blocked |

Test inventory: **1,037 tests collected** across 45 files.

## 2. The headline numbers

```
22 failed, 1012 passed, 3 skipped   (6 min 01 s)
```

| Skips (3) | Why |
|---|---|
| `test_phase12_key_migration_and_backup.py:697` | Windows junction regression — POSIX host |
| `test_phase17_key_providers.py:97` | Windows reparse-point open path |
| `test_phase17_key_providers.py:111` | Windows reparse-point API path |

All three are **declared** Windows-only tests using `pytest.mark.skipif(sys.platform != "win32")`.
They are not silent gaps; they are covered by the `verify-windows` CI job.

## 3. The 22 failures — one cause, stated exactly

**Every one fails with the same guard:**

```
Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13);
unsafe archive member types and paths are rejected.
```

surfaced as `FinancialIntegrityError` (direct tests) or as
`deep: "restore failed: FinancialIntegrityError"` (backup/verify automation).

| File | Failing tests | Nature |
|---|---|---|
| `test_phase12_key_migration_and_backup.py` | 10 | all call the restore path |
| `test_phase9_process_boundary.py` | 3 | restore from a fresh process |
| `test_phase11_audit_key_rotation.py` | 3 | restore preserves every key version |
| `test_phase8_upload_boundary_and_ci.py` | 2 | backup/restore round-trip |
| `test_phase62_backup_automation.py` | 2 | `verify --deep` |
| `test_phase6_ocr_benchmark_e2e.py` | 1 | 14-step E2E includes a restore |
| `test_phase5_security_audit_backup.py` | 1 | encrypted backup restore |
| **Total** | **22** | |

`test_phase62_backup_automation.py` reports `deep: "restore failed: FinancialIntegrityError"` — i.e.
the guard, not a data-integrity problem.

### 3.1 Why the floor exists (do not "fix" it by lowering the guard)

`tarfile`'s handling of links and special members before 3.11.17/3.12.10 cannot be made safe for
extracting an attacker-supplied archive. The restore path refuses to run on such an interpreter
instead of being quietly unsafe. Lowering it to make a sandbox green would trade a real vulnerability
for a passing test — the wrong trade, and the reason this document reports 22 reds rather than
silencing them.

### 3.2 Why a compliant interpreter could not be obtained here

| Attempt | Result |
|---|---|
| `curl` the `python-build-standalone` 3.12.10 release asset | exit 35 — GitHub **release assets** are not reachable from this sandbox |
| `pbs_installer 3.12.10` | `TLS/SSL connection has been closed` |
| Debian/Ubuntu Python 3.12 from `apt` | mirrors unreachable |
| Build CPython from source | no `zlib.h`, `openssl/ssl.h`, `sqlite3.h`, `ffi.h` in the image |
| System Python | 3.11.2 — below the floor |

`pypi.org` and `github.com` metadata *are* reachable; binary release assets are not. **This is an
environment limit of the sandbox, not a repository defect**, and it is why `59A`/`59D` mark restore
NOT EXECUTED rather than PASS.

### 3.3 What CI will show on the same commit

The `verify` job runs Python **3.11 and 3.12** and the `verify-windows` job runs **3.12.10**, on
interpreters above the floor, so the 22 are expected to pass there. That expectation is **stated as an
expectation**, not as a result: the authoritative evidence is the CI run on the pushed commit SHA.

## 4. Changes made in this phase and their regression tests

| Change | Test that pins it |
|---|---|
| `--bootstrap-admin` loads the SCF chart | `test_phase53_host_validation_and_hygiene.py::test_bootstrap_admin_loads_the_scf_chart` |
| credential validated before any DB write | `…::test_bootstrap_admin_rejects_a_weak_password_before_writing_anything` |
| `verify` fails closed on a missing manifest | `test_phase62_backup_automation.py::test_verify_refuses_an_artefact_whose_manifest_is_missing` |
| `--deep` without a key reports "skipped" | `…::test_a_deep_verify_without_a_key_reports_that_it_could_not_restore` |
| desktop job exists, installs the Qt lockfile, forbids skips | `test_phase19_dependency_integrity.py::test_ci_executes_the_desktop_suites_with_the_qt_extra_installed` |
| desktop lockfile matches the Qt extra | `…::test_desktop_lockfile_covers_exactly_the_qt_extra`, `test_phase64_…::test_desktop_lockfile_pins_exactly_the_declared_gui_extra` |
| corrected transcription does not duplicate the row | `test_phase27_desktop_ui.py::test_review_dialog_full_cycle_with_correction_and_audit` |
| review dialog survives a pre-refresh provenance lookup | `test_phase29_desktop_redesign.py::test_review_correction_box_explains_a_missing_value` |

## 5. `pip check` and `pip-audit`

`pip check` reports exactly one inconsistency:

```
rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.
```

This is **deliberate and documented** in `requirements.lock.txt`: `rapidocr-onnxruntime` declares the
GUI OpenCV wheel, which drags in the whole Qt5/xcb stack and links `libGL.so.1`. The runtime uses
`opencv-python-headless`, which provides the same `cv2` API without a GUI. `verify_dependency_graph.py`
treats this single substitution as the allowed deviation; anything else fails the gate.

`pip-audit` against the lockfile: **No known vulnerabilities found.**

## 6. Not executed, and why

| Gate | Status |
|---|---|
| Windows suite (3.12.10) | **NOT EXECUTED** — no Windows host. Covered by `verify-windows` in CI |
| Desktop suites on Windows | **NOT EXECUTED** — same |
| Real display / High-DPI acceptance | **NOT EXECUTED** — headless |
| TLS / reverse proxy | **NOT EXECUTED** — no certificate or proxy |
| Restore / deep verify | **NOT EXECUTED** — Python floor (§3) |
| Wheel build and clean-install in this phase | **NOT RE-RUN** (done in an earlier phase of this session: 294 accounts, 68 groups, 8 taxes) |

## 7. Reproduce

```sh
export LD_LIBRARY_PATH=/tmp/glshim QT_QPA_PLATFORM=offscreen    # sandbox-only headless Qt
python -m ruff check dz_erp tests tools
python -m compileall -q dz_erp tests tools
python tools/verify_dependency_graph.py
python tools/generate_hash_lock.py --check
python tools/generate_hash_lock.py --desktop --check
python -m pytest -q -ra
python tools/staging_acceptance.py
```

On a supported interpreter (3.12.10) the 22 expected reds disappear; that is the run CI performs.
