# Phase 5 — CI-equivalent execution on CPython 3.12.10

**Status line:**

```
B1 CLOSED · 1373 PASSED / 0 FAILED / 6 SKIPPED · B2 REVIEWED —
V1 SHIPS FROM THE SOURCE TREE · CLASSIFICATION: READY FOR STAGING
```

Date: 2026-11-09 · Tree: `5a3e47dd` + cumulative security patch + phase-58/59/60
tests · Interpreter: **CPython 3.12.10** · Evidence:
`docs/security/phase5_ci_312/`

---

## 0. How the CI environment was obtained (and why not GitHub Actions)

The instruction was to run the matrix in CI. This repository's only workflow
(`.github/workflows/ci.yml`) can be triggered only by a push or by a dispatch
against a ref that already contains it — and **pushing is the very action the
release gate forbids until the matrix has passed**. Running "CI" by pushing a
workflow would have inverted the authorisation.

What was done instead is the same thing a CI job does, executed in this sandbox:
a **trusted interpreter built from pinned, verifiable sources**, a **hash-pinned
dependency install**, the **full suite**, the **failure-mode matrix**, and
**signed-by-SHA-256 logs**. The sandbox has no reachable
`release-assets.githubusercontent.com`, so the standalone interpreter bundles
could not be downloaded; CPython was built from the upstream tag instead
(`codeload.github.com/python/cpython` → `v3.12.10`), together with its three
missing dependencies:

| Component | Source | Result |
| :--- | :--- | :--- |
| CPython | `python/cpython` tag `v3.12.10`, built with GCC 12.2.0 (`--enable-shared`, PGO off) | `Python 3.12.10 (main, Nov  9 2026) [GCC 12.2.0]` |
| OpenSSL | `openssl/openssl` tag `openssl-3.0.15` | 3.0.15 — `_ssl` importable |
| zlib | `madler/zlib` tag `v1.3.1` | 1.3.1 — `zlib`/tarfile gzip importable |
| SQLite | amalgamation 3.53.4 (npm `better-sqlite3` 13.0.3) | `sqlite3.sqlite_version` 3.53.4 |
| libffi | headers generated from `libffi/libffi` `v3.4.6` + system `libffi.so.8` | `_ctypes` importable (needed by `pypdfium2`) |
| dependencies | `requirements.lock.hashes.txt`, `pip install --no-deps --require-hashes` | 53 pins / 1304 hashes → 54 distributions, `pip check` clean except the documented `opencv-python → opencv-python-headless` substitution |

The interpreter floor was then re-measured on the built binary:
`safe_restore_supported((3,12,10)) is True`.

`docs/security/phase5_ci_312/logs/interpreter_provenance.log` records all of it,
including the exact `Requires-Python` metadata: `>=3.11.17,<3.13` with
`!=3.12.0…!=3.12.9` — the packaging metadata and the runtime gate agree.

## 1. Full suite — the headline number

| Run | Result |
| :--- | :--- |
| **Full suite, CPython 3.12.10** | **1373 passed, 0 failed, 6 skipped** in 292.9 s (1379 test cases) |
| Same suite on 3.11.2 (Phase 5 baseline, kept for comparison) | 20 failed, 1340 passed, 10 skipped |
| The **20 previously-gated tests**, re-run explicitly by node id | **20 passed** in 10.8 s |

Every one of the twenty failures disappeared, and none was silenced: the tests
moved from "skipped / refused by the interpreter gate" to "executed and passing",
and two of the four restore-integrity tests that used to self-skip
(`test_phase48_restore_interpreter_matrix`, `test_phase58_…round_trips…`) now run.

Remaining skips (6, unchanged in nature, none added by this work):

| Test | Reason |
| :--- | :--- |
| `tests.test_phase27_desktop_ui` (collection) | `PySide6` not installed in a server environment |
| `tests.test_phase28_desktop_users_ui` (collection) | same |
| `test_phase26_desktop::test_mainwindow_constructs_and_switches_to_rtl` | same |
| `test_phase12…::test_restore_refuses_existing_windows_junction_prefix` | Windows-only reparse-point path |
| `test_phase17…::test_windows_provider_refuses_to_follow_a_symlinked_key` | Windows-only |
| `test_phase17…::test_windows_provider_rejects_reparse_handle_without_symlink_privilege` | Windows-only |

Zero restore/backup skips remain.

## 2. The six required scenario families, run explicitly

Each family was run on its own so the evidence maps to the request
(`logs/categories.log`):

| # | Family | Tests | Result |
| :--- | :--- | :--- | :--- |
| 1 | Encrypted Backup / Restore (phase 5, 12 + new failure modes) | 39 | **38 passed, 1 skipped** (the Windows junction case) |
| 2 | Key Rotation and Recovery (phase 11, 13, 17) | 62 | **60 passed, 2 skipped** (Windows reparse cases) |
| 3 | Isolated Restore (phase 9 process boundary, 41 migration evidence, 48 interpreter matrix) | 65 | **65 passed** |
| 4 | Trial Balance Verification (phase 32, 33, 47, 56) | 120 | **120 passed** |
| 5 | Audit Chain Integrity (`-k audit_chain / chain_valid / audit_events / tamper / immutab` across the suite) | 25 | **23 passed, 2 skipped** (desktop modules) |
| 6 | Vault Backup/Restore E2E (phase 10 + vault/backup/restore selections from phase 58) | 115 | **30 passed** (85 deselected by the filter) |

## 3. Failure-mode matrix requested in item 6

New module: `tests/test_phase60_backup_failure_modes.py` (9 tests, all passing).
Everything it does happens inside `tmp_path`; no real database, vault or key is
touched.

| Scenario | Method | Assertion |
| :--- | :--- | :--- |
| **Disk full during backup** | forked child with `RLIMIT_FSIZE=4096`, `SIGXFSZ` ignored → every write fails with `EFBIG`, exactly as a full filesystem fails with `ENOSPC` | a raised error (not a killed process), **no archive left behind**, no `.tmp_backup_*` staging debris, source database still intact |
| **Disk full during restore** | same limit applied to the restore | a raised error, **archive byte-identical afterwards** (a failed restore must not destroy the only copy), no promoted `erp.sqlite3` in the target |
| **Permission denied — backup output** | output directory `chmod 0500` | `OSError`/`PermissionError` raised, directory still empty |
| **Permission denied — restore target** | target directory `chmod 0500` | refused, archive unchanged, nothing promoted |
| **Truncated archive** | half the ciphertext | refused, no database promoted |
| **Corrupted ciphertext** | one flipped byte | refused, no database promoted |
| **Wrong key** | a freshly generated Fernet key | refused, no database promoted |
| **Staging permissions after a failed write** | as above | no leftover staging entry readable by group/other |
| **Control** | the same backup+restore **without** the limit | succeeds — this is what makes the two disk-full tests meaningful rather than a broken fixture |

Observed failure paths (recorded in `logs/`):

* backup under write exhaustion → `sqlite3.OperationalError: disk I/O error`;
* restore under write exhaustion → `dz_erp.database.FinancialIntegrityError… [Errno 27] File too large`.

**Diagnostics observation (not a security finding):** the restore path reports a
write-exhaustion as "unsafe or invalid tar entry". It fails closed and writes
nothing — the behaviour is right — but the message points an operator at the
archive's contents instead of at the disk. Recorded in
`SECURITY_RESIDUAL_RISKS.md` §7 rather than patched, because changing a
verification message needs its own review.

## 4. Local File Vault on 3.12.10

Re-run unchanged on the new interpreter (`logs/vault_probe.log`), all green:
blobs `0600`, key file `0600`, no plaintext in ciphertext (`DZKV` header
present), no temporary debris, cross-tenant read → `TenantIsolationError`,
tampered byte → `InvalidToken`, 16 concurrent stores → 16 distinct readable
blobs, rotation v1→v2 re-encrypts all of them and they remain readable,
read-only vault → `PermissionError`, `verify_blobs` → 16 checked / 0 failed.

Restore-side vault safety is now *executed* rather than skipped: the phase-12
and phase-58 vault/backup tests run on this interpreter, and the phase-60 module
adds the write-exhaustion path. Audit-log redaction was re-measured
independently (`logs/redaction_probe.log`): 5 key-based secrets redacted, NIN and
IBAN pattern-redacted, record preserved, file `0600`.

Disk-full for the *vault write* itself is covered indirectly (the backup path
copies vault files into the archive and the failure is asserted to leave
nothing); a dedicated vault-store ENOSPC test is still not written — see §7.

## 5. API / deployment boundary on 3.12.10

Both an in-process client (`logs/api_smoke.log`) and a **real server started from
the source tree** (`logs/deployment_smoke.log`) were exercised:

| Probe | Result |
| :--- | :--- |
| `GET /healthz` | 200 `{"status":"ok","service":"algerian-ai-erp"}` |
| `GET /openapi.json` | 404 |
| `GET /api/v1/users` without token | 401 |
| `POST /api/v1/auth/token` (correct / wrong password) | 200 / 401 |
| `GET /api/v1/auth/session` with token | 200 |
| `POST /api/v1/auth/password` with a 5-character password (SEC-13) | 422 |
| `Host: evil.example` (SEC-15) | 400 |
| `Host: <unlisted preview domain>` (SEC-15, live server) | 400 |

The live-server run also proves the documented first-run procedure
(`--bootstrap-admin`) and the documented entry point
(`python -m dz_erp.serve`) work on the release interpreter.

## 6. Quality gates on 3.12.10

| Gate | Result |
| :--- | :--- |
| `ruff check dz_erp tests tools` | **All checks passed!** |
| `python -m compileall dz_erp tests tools` | OK |
| `tools/verify_dependency_graph.py` | OK — 54 installed distributions match the declared graph; only the documented `opencv-python → opencv-python-headless` substitution |
| `pip check` | only the same documented substitution |
| Dependency advisories (see §8) | 0 findings across 53 locked packages |

## 7. Test-file changes in this phase (disclosed in full)

| File | Change | Justification |
| :--- | :--- | :--- |
| `tests/test_phase58_security_selective_integration.py` | `test_backup_round_trips_and_verifies_the_chain`: the call now uses keyword arguments, and the assertion targets the report the function actually returns (`db_sha256_verified`, plus `audit_chains_valid`, `integrity_check`, `foreign_key_check`) | The test had **never executed** (it self-skipped on 3.11.2). Its first real run exposed two authoring defects: a positional/keyword collision (`TypeError`) and an assertion on a key (`db_sha256`) that the API never returned. Four assertions replace one — strictly more coverage. Delta: `deltas/phase5-phase58-restore-assertion-fix.patch`; new hash `3e582afd…86acc0`, preserved original `527bcc99…492837` untouched in `phase3_blocker_closure/` |
| `tests/test_phase60_backup_failure_modes.py` | **new** module (9 tests) | The failure modes the brief requires (disk full, restore failure, permission denied) had no executed coverage at all. Nothing pre-existing was weakened |

No assertion was deleted, no test removed, no skip added: the full-run skip count
fell from 10 to 6 because four interpreter-gated skips became real runs.

## 8. Dependencies (SEC-11) — refreshed pins, audited

The integrated lockfile no longer contains the pins the residual-risk register
described (`starlette 0.46.2`, `cryptography 44.0.2`, `pillow 11.1.0`,
`pypdf 5.4.0`, `pdfminer.six 20250327`, `python-multipart 0.0.20`,
`certifi 2026.7.22`, `setuptools 66.1.1`). It now pins `starlette 1.3.1`,
`fastapi 0.133.0`, `cryptography 50.0.0`, `pillow 13.0.0`, `pypdf 6.21.4`,
`pdfminer.six 20251230`, `python-multipart 0.0.31`, `certifi 2026.10.18`
(`logs/pip_freeze_312.txt`).

Audit method: every one of the 53 locked packages was queried against the GitHub
Advisory Database (`gh api /advisories?ecosystem=pip&affects=…`) and its exact
pinned version checked against each advisory's affected range.

* **installed pins: 0 advisories match, 0 inconclusive ranges.**
* **negative control:** the eight old pins from the register were run through the
  same detector and **all eight matched** (e.g. `pillow 11.1.0` → 55 advisories
  including critical ones, `pypdf 5.4.0` → 67, `starlette 0.46.2` → 7). The
  detector is therefore not vacuously returning "clean".

Limitations, stated plainly: GHSA coverage is not identical to OSV/pip-audit
coverage, CVE-only entries without a GHSA record are not covered, and
`pip-audit` could not run here because its advisory endpoint is unreachable from
this sandbox. Repository CI must still run `pip-audit` as a blocking gate.

## 9. B1 and B2 — status

**B1: CLOSED.** A supported interpreter (CPython 3.12.10) was built and used; the
restore matrix, key-rotation restore, isolated restore, trial-balance
verification, audit-chain integrity and vault backup/restore E2E all executed;
the twenty gated failures all pass.

**B2: reviewed, decision documented, defect still open.** `pip wheel .` still
fails on the flat-layout error; the preserved Phase-3 patch fixes it (44-entry
wheel, single top-level package) but is deliberately kept as a separate change.
The wheel is **not** a V1 delivery artefact — it carries no `ops/` assets, no
lockfile, no server entry point (its only script is the desktop app). V1 is
deployed **from the source tree**, verified end to end on 3.12.10. Full
reasoning: `docs/security/PACKAGING_DECISION_V1.md`.

## 10. Readiness classification

**READY FOR STAGING.**

Rationale: every security gate that can be executed in this environment passes on
the release interpreter, with no unexplained failure and no silenced test. It is
not "ready for production candidate" because:

* staging acceptance (B3) has never been run against a real host;
* SEC-11's audit here is GHSA-based; repository CI must run `pip-audit`;
* B2 is open (packaging), and V1 depends on the source-tree route by decision;
* AWS KMS / HSM remains out of V1 scope by decision (B4), so custody claims must
  stay at "file-based".

## 11. Artefacts

| Path | Contents |
| :--- | :--- |
| `phase5_ci_312/logs/full_suite_312.xml` / `.log` | JUnit + console output of the 1379-case run on 3.12.10 |
| `phase5_ci_312/logs/failing_tests.txt` | empty — zero failures (kept so the file's absence is not mistaken for a missing run) |
| `phase5_ci_312/logs/skipped_tests.txt` | the 6 skips with reasons |
| `phase5_ci_312/logs/gated20.txt` / `gated20_rerun.log` | the 20 previously-gated node ids and their passing re-run |
| `phase5_ci_312/logs/categories.log` | the six scenario families |
| `phase5_ci_312/logs/interpreter_provenance.log` | how the interpreter was built, floor values, lockfile counts |
| `phase5_ci_312/logs/quality_gates.log` | ruff / compileall / dependency graph |
| `phase5_ci_312/logs/packaging.log` | flat-layout failure and patched build on 3.12 |
| `phase5_ci_312/logs/vault_probe.log`, `redaction_probe.log`, `api_smoke.log` | vault, redaction and in-process API evidence |
| `phase5_ci_312/logs/deployment_smoke.log` | real server started from the source tree |
| `phase5_ci_312/logs/pip_freeze_312.txt`, `pip_check.log` | exact installed set |
| `phase5_ci_312/deltas/phase5-phase58-restore-assertion-fix.patch` | the only change to a preserved test |
| `phase5_ci_312/artefacts/` | copies of the two test files, hashed in `SHA256SUMS` |
| `phase5_ci_312/probes/deployment_smoke.sh` | rerun the source-tree deployment verification |
| `phase5_release_gate/` | **kept unchanged**: the 3.11.2 blocker evidence that B1 recorded |
