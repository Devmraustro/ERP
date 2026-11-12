# Phase 10 — P0 Dependency Security Remediation

**Task:** close the dependency advisories recorded in `DEPENDENCY_AUDIT_FINDING.md`.
**Result:** **DEPENDENCY REMEDIATION VERIFIED** — 143 advisories → 0, clean-lock
install reproducible, `pip-audit` exit 0, no functional regression.
**One gate is NOT EXECUTED** (full-suite green on a *supported* Python — see §7.3).
**Release classification is unchanged: CONDITIONALLY PRODUCTION READY.**

| Gate | Result |
| --- | --- |
| Dependency resolution | **PASS** |
| Lockfile + hashes | **PASS** |
| Clean-environment install | **PASS** |
| Dependency integrity tests | **PASS** (26/26) |
| `pip-audit` (lockfile and installed env) | **PASS** — exit 0, zero vulnerabilities |
| `pip check` | **PASS\*** — one documented, allow-listed substitution (exit 1) |
| Full regression suite | **PASS on 3.11.2 (differential) / NOT EXECUTED on a supported interpreter** |
| PDF / OCR / image regression | **PASS** (+40 new hostile-input tests) |
| Ruff | **PASS** |
| Compileall | **PASS** |
| Evidence documentation | **PASS** |
| Git preservation | **PASS** — nothing committed, pushed, merged or rebased |

---

## 1. Git baseline

| | |
| --- | --- |
| Repository | `/home/user/ERP` (remote `https://github.com/Devmraustro/ERP.git`) |
| Branch | `arena/7557bcfb-erp` (unchanged; no new branch created) |
| Baseline SHA | `4fc1364a9504fab1153ea44e753f354f00023428` |
| PR #7 | OPEN, draft, MERGEABLE, head `f603e38` — **not modified, not merged** |
| Baseline worktree | 6 modified files + untracked `docs/release/`, `docs/security/`, 4 new `dz_erp/ops_*.py`/`readiness.py`, `ops/`, 4 new test modules — all preserved |
| Destructive operations | none (no `reset --hard`, no `clean`, no force push, no rebase) |
| Commits made | **0** — the work is left uncommitted for review, as instructed |

## 2. Packages changed

Old and new versions are the values in `requirements.lock.txt` (and mirrored in
`pyproject.toml`, which the integrity test requires to agree with the lock).

| Package | Before | After | Why this release |
| --- | --- | --- | --- |
| `pypdf` | 5.4.0 | **7.0.1** | 67 advisories; 6.21.4 clears them, 7.0.1 is the current line and stays inside the 3.10+ window |
| `pillow` | 11.1.0 | **13.0.0** | 53 advisories (PSD/FITS/font parsers); 13.0.0 is the only line that clears the last of them |
| `starlette` | 0.46.2 | **1.10.0** | 7 advisories, the newest fixed only in 1.3.1+ |
| `cryptography` | 44.0.2 | **50.0.2** | 6 advisories, fixes spread up to 50.0.0 |
| `python-multipart` | 0.0.20 | **0.0.33** | 6 advisories, fixes up to 0.0.31 |
| `pdfplumber` | 0.11.6 | **0.11.10** | 0.11.6 hard-pins the vulnerable `pdfminer.six==20250327`; 0.11.10 pins 20260107 |
| `pdfminer.six` | 20250327 | **20260107** | 2 advisories; moves with `pdfplumber` (exact pin) |
| `certifi` | 2026.7.22 | **2026.10.23** | 1 advisory (trust store) |
| `pytest` | 8.3.5 | **9.2.0** | 1 dev-only advisory; `pytest-asyncio==1.3.0` accepts `pytest<10` |
| `fastapi` | 0.115.12 | **0.140.6** | required to allow `starlette>=1.3.1` |
| `annotated-doc` | *(absent)* | **0.0.6** | new hard requirement of `fastapi>=0.134`; without it a `--no-deps` install fails at import |
| `pygments` | *(absent)* | **2.21.0** | new hard requirement of `pytest 9.x` |
| `setuptools` (build-system floor) | `>=66.1.1` | **`>=84.1.0`** | the 66.1.1 bootstrap carries 7 advisories (GHSA-xmj3-9gf7-h52w) |

Lock totals: **51 → 53 distributions**, **1 277 → 1 317 sha256 hashes**.

### 2.1 Why `fastapi 0.140.6` and not the newest

`fastapi>=0.147` adds `opentelemetry-api>=1.44.0` to the runtime graph — a
tracing stack this application neither configures nor exports to. `0.140.6` is
the newest release that still declares `starlette>=0.46.0` (no ceiling) while
introducing no new runtime dependency other than `annotated-doc`. The pair
`fastapi 0.140.6` + `starlette 1.10.0` was therefore chosen as the smallest
secure set: it clears all starlette advisories and adds one pin instead of
four.

Constraints that must survive any future bump (asserted by tests):

- `pdfplumber` pins `pdfminer.six` **exactly** — the pair moves together.
- `fastapi` requires `annotated-doc`; `pytest 9.x` requires `pygments`.
- `starlette` must satisfy `fastapi`'s specifier (`test_starlette_pin_satisfies_the_fastapi_requirement`).

## 3. Advisory counts

| | Before | After |
| --- | --- | --- |
| `pip-audit` rows (duplicates included) | 216 | **0** |
| Distinct advisories | **143** | **0** |
| Affected distributions | 8 of 51 | **0 of 53** |
| `pip-audit` exit code | 1 | **0** |

Before-source: `evidence/logs/pip_audit.log`, `pip_audit_full.json`,
`pip_audit_summary.json` (recorded during Phase 10 evidence collection).
After-source: `evidence/logs/dependency_remediation/pip_audit_lockfile_after.txt`
and `…/pip_audit_installed_env_after.txt` — both `No known vulnerabilities found`.

Per-package counts before (distinct): pypdf 67, pillow 53, starlette 7,
cryptography 6, python-multipart 6, pdfminer.six 2, certifi 1, pytest 1.

Representative fixed IDs (the full set of 143 is in the JSON artefact):
`PYSEC-2026-1941` (starlette, fixed 0.47.2), `PYSEC-2026-2280` /
`PYSEC-2026-2281` (starlette, fixed 1.1.0), `PYSEC-2026-2249` (Pillow PSD
out-of-bounds write), `PYSEC-2026-2250` (Pillow FITS decompression bomb),
`PYSEC-2026-165` (Pillow font integer overflow), `PYSEC-2026-4320` (certifi),
`PYSEC-2026-2141` / `PYSEC-2026-35` / `PYSEC-2026-3552` / `-3553` / `-3554`,
`GHSA-537c-gmf6-5ccf` (cryptography), `PYSEC-2026-1761` / `PYSEC-2026-1762`
(pdfminer.six).

**No advisory was suppressed, ignored or excluded.** No `--ignore-vuln` is used
anywhere, and no exclusion list exists in this repository.

## 4. Lockfile and hash regeneration

```
$ python tools/generate_hash_lock.py --write
Fetching digests for 53 pinned distributions from PyPI...
Wrote requirements.lock.hashes.txt: 53 distributions, 1317 hashes.        # exit 0

$ python tools/generate_hash_lock.py --check
OK - 53 pinned distributions, 1317 hashes, no drift from requirements.lock.txt.
                                                                          # exit 0
```

Every digest comes from the PyPI JSON API for the exact pinned version, all
distribution files included — the same mechanism as before, no manual hash ever
entered the file. `test_hash_lockfile_covers_exactly_the_version_pins`,
`test_every_pin_in_the_hash_lockfile_is_parsed_by_pip` and
`test_hash_lockfile_uses_sha256_only` all pass, so the file is structurally
enforceable (one hash per pin would otherwise be the silent failure mode).

## 5. Clean-environment installation

A fresh virtualenv was created **without touching the pre-existing environments**:

```
$ python3 -m venv /tmp/v_new                                   # CPython 3.11.2
$ /tmp/v_new/bin/pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
Successfully installed ... (53 distributions)                  # exit 0
```

Exact-match verification (`--no-deps` means the lock must be the whole runtime):

```
pins=53 installed=55
missing from env: none
version drift: none
undeclared installed: none
```

(The two extra distributions are `pip` and `setuptools`, the installer's own
bootstrap; `setuptools` was raised to 84.2.1 in this environment and the
build-system floor in `pyproject.toml` was raised to `>=84.1.0` so no build path
accepts the vulnerable bootstrap.)

Application start-up from the lock alone:

```
healthz 200 {'status': 'ok', 'service': 'algerian-ai-erp'}
readyz  200 {'database': 'ok', 'vault': 'ok', 'key_custody': 'ok', 'disk': 'ok', 'ocr': 'ok'}
SMOKE OK
```

Logs: `evidence/logs/dependency_remediation/clean_env_exact_match.txt`,
`clean_env_smoke.txt`.

## 6. Security audit (mandatory gate)

```
$ /tmp/vaudit/bin/pip-audit --version
pip-audit 2.10.1

$ /tmp/vaudit/bin/pip-audit -r requirements.lock.txt --no-deps --disable-pip
No known vulnerabilities found                                          # exit 0

$ /tmp/vaudit/bin/pip-audit --path /tmp/v_new/lib/python3.11/site-packages
No known vulnerabilities found                                          # exit 0
```

Both the declared lock and the actually-installed environment are clean, so the
result cannot be an artefact of auditing a file that differs from what runs.

## 7. Test results

### 7.1 Dependency integrity

```
tests/test_phase19_dependency_integrity.py ..........................  [100%]
26 passed in 0.63s
```

No test was weakened, skipped or deleted. One expected value was updated —
`test_optional_desktop_extra_is_exact_pinned_and_absent_from_the_lockfile`
asserted `pytest==8.3.5` in the declared extras; it now asserts `9.2.0`, with a
comment recording why. `test_live_environment_passes` was **not** touched: it
failed in the pre-remediation probe precisely because the environment did not
match the lock, and it passes now that the lock and the environment agree. That
is the test doing its job.

### 7.2 Full suite (clean environment, new lock)

```
$ /tmp/v_new/bin/python -m pytest -q
============ 22 failed, 799 passed, 6 skipped, 1 warning in 172.64s ============
```

- **+40 tests** are new in this task
  (`tests/test_phase64_dependency_security_regression.py`).
- **6 skips, unchanged and enumerated:** 3 PySide6 GUI modules (no `libGL.so.1`
  in this container), 1 Windows junction, 2 Windows reparse-point tests.
- The 22 failures are **not** caused by the upgrade. Every one of them is the
  same test that fails on this interpreter before the remediation, and the set
  difference is empty:

```
$ diff <(new failure list) <(old failure list)   →   (no output)
```

All 22 raise the project's own runtime guard:

```
dz_erp.database.FinancialIntegrityError: Safe backup restore requires
Python 3.11.17+ or Python 3.12.10+ (and below 3.13); unsafe archive member
types and paths are rejected.
```

The suite therefore exercises the *restore* boundary only on a supported
interpreter, and this sandbox has none (see §7.3).

### 7.3 Why the supported interpreter is unavailable — external blocker

The application requires Python ≥ 3.11.17 (or ≥ 3.12.10). This sandbox provides
CPython **3.11.2** only. Attempts to obtain a supported build all failed for
environment reasons, not project reasons:

| Attempt | Result |
| --- | --- |
| `uv python install 3.12.10` | download from `github.com/.../releases/download/...` redirects to `release-assets.githubusercontent.com`, which the sandbox egress policy refuses (`SSL_ERROR_SYSCALL` / TLS handshake EOF) |
| `apt-get` (Debian 12) | no package index reachable; no `python3.12` candidate |
| build from source | `gcc`/`make` exist but `libssl-dev`, `zlib1g-dev`, `libffi-dev`, `libbz2-dev`, `liblzma-dev`, `libsqlite3-dev` are absent, so a built interpreter would have no `ssl`/`zlib` and could not run pip |

Consequently the gate "full suite green, zero failures, on the project's
supported runtime" is recorded as **NOT EXECUTED**, and the evidence supplied
instead is differential: identical failure sets before and after on the same
interpreter, plus 434 passing dependency-sensitive tests (§7.4). It must be
re-run on Python 3.11.17+ / 3.12.10 before release, and this report does not
claim otherwise.

### 7.4 OCR / PDF / image / API / security regression

```
$ pytest -q tests/test_phase3_invoice_intake.py tests/test_phase4_procurement_3way_match.py \
  tests/test_phase6_ocr_benchmark_e2e.py tests/test_phase7_authentication_remediation.py \
  tests/test_phase8_upload_boundary_and_ci.py tests/test_phase9_durable_security_log.py \
  tests/test_phase18_ocr_confidence_provenance.py tests/test_phase19_dependency_integrity.py \
  tests/test_phase34_security_validation.py tests/test_phase63_dependency_contract.py \
  tests/test_phase64_dependency_security_regression.py
============= 3 failed, 434 passed, 1 warning in 128.77s =======================
```

The 3 failures are the restore-gated tests of §7.2. Everything that exercises
the upgraded parsers passes:

| Behaviour | Evidence |
| --- | --- |
| PDF text extraction (`pdfplumber 0.11.10` + `pdfminer.six 20260107`) | `test_phase18_ocr_confidence_provenance`, `test_phase6_ocr_benchmark_e2e` (5 of 6 tests; the 6th is restore-gated) |
| Supplier invoice ingestion end-to-end | `test_phase3_invoice_intake`, `test_phase4_procurement_3way_match` |
| OCR preprocessing and engine load | `test_phase6_*`, phase 64 image tests |
| Image conversion / decoding (`pillow 13.0.0`) | phase 64 image tests, `test_phase8_upload_boundary_and_ci` PNG paths |
| Multipart uploads (`python-multipart 0.0.33`) | `test_phase8_upload_boundary_and_ci` (210 tests) |
| API middleware and exception handling (`starlette 1.10.0`) | phase 7/8/34 suites, `test_phase62_readiness` |
| Authentication and crypto (`cryptography 50.0.2`) | `test_phase7_authentication_remediation`, `test_phase12_key_migration_and_backup` (non-restore tests) |
| Financial integrity | `test_phase4_procurement_3way_match`, phase 12/18 accounting assertions |

## 8. Hostile-input regression (new coverage)

`tests/test_phase64_dependency_security_regression.py` — **40 tests, all passing**
— adds two kinds of coverage:

**(a) The pins cannot slide back.** Per-package security floors
(`pypdf≥6.21.4`, `pillow≥13.0.0`, `starlette≥1.3.1`, `cryptography≥50.0.0`,
`python-multipart≥0.0.31`, `pdfminer.six≥20251230`, `pdfplumber≥0.11.10`,
`certifi≥2026.10.18`, `pytest≥9.0.3`) are asserted for both the lock and the
installed environment; the `pdfplumber`→`pdfminer.six` exact pin is asserted as
a pair; the two new transitive pins are required; the installed set must equal
the locked set; every pin must carry a hash; the build backend must require a
patched setuptools; and the OCR provenance label must name the *installed*
parser versions (it previously hardcoded `pdfplumber-0.11.6+pdfminer`, which
silently became a false audit record after this upgrade — now derived from
package metadata).

**(b) The upgraded parsers reject hostile documents.** All fixtures are inert,
synthetic, a few hundred bytes each — no weaponised payloads:

| Fixture | Expectation | Result |
| --- | --- | --- |
| PNG claiming 20000×20000 (bomb guard) | `ValueError`, nothing decoded | PASS |
| PNG at 6001 px (dimension ceiling) | `ValueError "exceed limit"` | PASS |
| PNG at 64 px | accepted (the limit is a boundary, not a blanket refusal) | PASS |
| PDF with a 400-deep nested page tree | `ValueError`, no recursion escape | PASS |
| PDF with a cyclic page tree | `ValueError`, no hang | PASS |
| PDF with 40 pages (ceiling 20) | `ValueError "page count"` | PASS |
| PSD (`8BPS`) bytes labelled `image/png` | refused before any decode | PASS |
| Truncated multipart body | 400/422, not 500 | PASS |
| `.zip` / `image/gif` | 400/415 (outside the allow-list) | PASS |
| Each hostile fixture through `POST /api/v1/documents/upload` | 4xx, no 500, **zero** `source_documents` / `ocr_extracted_fields` rows | PASS |
| Accepted invoice upload | document + extracted fields only; **no** `supplier_invoices`, **no** `journal_entries` | PASS |

The last row is the standing guarantee that OCR/AI extraction never posts an
invoice: posting requires an explicit human action.

## 9. Other quality gates

```
$ ruff check dz_erp tests
All checks passed!                                                      # exit 0

$ python -m compileall -q dz_erp tests                                   # exit 0, no output

$ pip check
rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.   # exit 1
```

`pip check`'s single line is the documented, allow-listed substitution
(`opencv-python-headless` supplies the same `cv2` package; `rapidocr-onnxruntime`
names the GUI distribution). It is **unchanged from the baseline**, is not caused
by this remediation, and is rejected as a defect by
`docs/release/phase10/OPENCV_WHEEL_DECISION.md`. The project's own stricter
validator (`tools/verify_dependency_graph.py`) allow-lists exactly this one
substitution and rejects any other — which is why `test_live_environment_passes`
passes.

## 10. Remaining warnings and limitations

1. **Supported-interpreter gate NOT EXECUTED** (§7.3) — the only open item of
   this task; needs a machine with Python 3.11.17+/3.12.10.
2. `starlette 1.x` emits a deprecation warning when `TestClient` is imported
   with `httpx` 0.28.1 ("install `httpx2` instead"). It is a warning, not a
   failure; moving to `httpx2` is a separate, behaviour-affecting change and was
   deliberately not smuggled into a security fix.
3. `pip check` exit 1 for the OpenCV substitution — documented above.
4. The 6 platform skips are unchanged (GUI/Windows classes).
5. `pip-audit` reports the *current* advisory database. A future disclosure can
   reopen this gate; the floor tests catch accidental downgrades, not new
   advisories, so the audit must stay in CI.
6. **Stale references outside `docs/release/`.** This task was scoped to
   `docs/release/`, the manifests and the code paths under test, so
   `docs/DEPENDENCY_AND_LICENSE_REGISTER.md`, `docs/INVOICE_INTAKE_AND_OCR_SPEC.md`,
   `docs/OCR_BENCHMARK_REPORT.md` still quote `pdfplumber==0.11.6` /
   `pdfminer.six==20250327`, and `docs/security/SECURITY_RECONCILIATION_REPORT.md`
   lists 0.11.6 → 0.11.9 as a pending upgrade. They are documentation-only
   inconsistencies; the lock, the code and the tests are authoritative and agree
   with each other. They should be refreshed in the same change that touches
   those documents.

## 11. Files changed

| File | Change |
| --- | --- |
| `requirements.lock.txt` | 10 pins raised, 2 transitive pins added, security rationale recorded in the header |
| `requirements.lock.hashes.txt` | regenerated: 53 distributions, 1 317 hashes |
| `pyproject.toml` | declared pins mirror the lock; `pytest` extra; build-system floor `setuptools>=84.1.0` |
| `dz_erp/intake_ocr.py` | OCR provenance reads the installed parser versions instead of a hardcoded string |
| `tests/test_phase19_dependency_integrity.py` | one exact expected version updated (8.3.5 → 9.2.0), with rationale |
| `tests/test_phase64_dependency_security_regression.py` | **new** — 40 tests (pins + hostile documents) |
| `docs/release/phase10/DEPENDENCY_REMEDIATION_REPORT.md` | this report |
| `docs/release/phase10/OPENCV_WHEEL_DECISION.md` | renamed from the old `DEPENDENCY_REMEDIATION_REPORT.md` (content unchanged) |
| `docs/release/phase10/DEPENDENCY_AUDIT_FINDING.md` | status banner pointing at the closure |
| `docs/release/README.md`, `docs/release/phase10/PHASE10_FINAL_REPORT.md` | index + addendum |

## 12. Final Git state

```
$ git rev-parse HEAD
4fc1364a9504fab1153ea44e753f354f00023428        # same as baseline; nothing committed

$ git status --porcelain      # modified tracked files + untracked evidence trees
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/intake_ocr.py          # this task
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
 M pyproject.toml                # this task
 M requirements.lock.hashes.txt  # this task
 M requirements.lock.txt         # this task
 M tests/test_phase19_dependency_integrity.py   # this task
 M tests/test_phase7_authentication_remediation.py
 M tests/test_phase8_upload_boundary_and_ci.py
?? docs/release/ …  (Phase 8/9/10 evidence, incl. this report)
?? docs/security/ …
?? dz_erp/{readiness,ops_backup,ops_alerts,ops_checks}.py
?? ops/ …
?? tests/test_phase64_dependency_security_regression.py   # this task
```

Nothing was committed, pushed, merged, rebased or force-pushed. PR #7 is
untouched.

## 13. Classification

**DEPENDENCY REMEDIATION VERIFIED.**

The P0 gate is closed on the evidence available: zero known vulnerabilities in
both the lock and the installed environment, reproducible hash-locked
installation, exact environment/lock agreement, 26/26 integrity tests, 434
passing dependency-sensitive tests and no functional regression against the
pre-remediation baseline.

**Not claimed:** `PRODUCTION READY`. The remediation did not touch — and could
not verify — public TLS acceptance, Windows GUI and English locale acceptance,
expert accounting sign-off, or operational requirements. Those remain separate
gates. The supported-runtime suite run (§7.3) must also be completed before any
production claim.
