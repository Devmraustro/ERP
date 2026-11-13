# Phase 46 — Step 5: Final Regression

**Recorded:** 2026-11-09
**Commit under test:** `1963a33` (docs only on top of `3d112e1`; code identical to `02068c0`)
**Status:** COMPLETE — all results reconciled

---

## 1. The headline, stated precisely

| Environment | Result |
| --- | --- |
| **Local sandbox**, CPython **3.11.2** | **947 passed / 22 failed / 21 skipped** |
| **Remote CI**, Python **3.11** | **pass** |
| **Remote CI**, Python **3.12.10** | **pass** |
| **Remote CI**, Windows | **pass** |

The 22 local failures are **not regressions**. Every one is the safe-restore
runtime guard refusing to extract archives on Python 3.11.2, which is below its
3.11.17 security floor. On CI's supported interpreters the same tree passes
completely.

This distinction is the whole point of running both: the sandbox interpreter is
unsuitable for restore tests, so a local red run cannot by itself establish
failure — and a green CI run is needed to prove the tests do pass where the
runtime is supported.

---

## 2. Exact commit and environment

| Item | Value |
| --- | --- |
| Commit tested | `1963a33` |
| Code content identical to | `02068c0` (the H-1/G5/G6 commits are documentation only) |
| CI run covering the code | `43339096973` on `02068c0` |
| Local interpreter | CPython **3.11.2** (`/home/user/.venvs/q46`) |
| Local SQLite / OpenSSL | as shipped with 3.11.2 |
| Dependency set | hash-pinned, `--no-deps --require-hashes`, 53/53 match |
| PySide6 | **not installed** (hence GUI skips) |
| OS | Linux (sandbox) |

Environment record: `docs/release/phase46/logs/final_env.txt`

---

## 3. Commands, exit codes and logs

| # | Command | Exit | Log |
| --- | --- | --- | --- |
| 1 | `python -m pytest -q -ra` | **1** (22 guard failures) | `logs/final_full_suite_3112.log` |
| 2 | `python -m ruff check dz_erp tests tools` | 0 | `logs/final_static_checks.log` |
| 3 | `python -m compileall -q dz_erp tools tests` | 0 | `logs/final_static_checks.log` |
| 4 | `python tools/generate_hash_lock.py --check` | 0 | `logs/final_static_checks.log` |
| 5 | `python tools/verify_dependency_graph.py` | 0 | `logs/final_static_checks.log` |
| 6 | `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | 0 | `logs/h1_install_hash_enforced.log` |
| 7 | `pip install --require-hashes -r requirements.lock.hashes.txt` (no `--no-deps`) | 1 | `logs/h1_install_hash_mode_failure.log` |
| 8 | `python tools/staging_acceptance.py` | 38/40 | §6 |
| 9 | `python -m pytest tests/test_phase29_ocr_review_policy.py` | 0 | 49/49 — §5 |

Static-check output verbatim:

```
=== ruff ===
All checks passed!

=== compileall ===
compileall OK (exit 0)

=== hash lock check ===
OK - 53 pinned distributions, 1317 hashes, no drift from requirements.lock.txt.

=== dependency graph ===
OK - the environment matches the declared dependency graph.
     The only deviation from upstream metadata is the documented
     substitution opencv-python -> opencv-python-headless.
```

---

## 4. The 22 failures — each accounted for

**Root cause, single:** `dz_erp/security_audit.py:1094–1106` refuses safe backup
extraction unless the interpreter is ≥ 3.11.17 or ≥ 3.12.10 (< 3.13):

> "Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below
> 3.13); unsafe archive member types and paths are rejected."

This is a deliberate security control (a later-patched unsafe-tar handling path).
**It was not relaxed.** The mission explicitly forbids modifying the safe-restore
guard to make unsupported Python versions pass, and it was not modified.

20 of the 22 emit that message directly. The other 2 wrap it in a larger assertion
object, so they were proven individually rather than assumed:

| Test | Independent proof |
| --- | --- |
| `test_deep_verify_restores_and_compares_the_ledger` | Direct call: `verify_backup(deep=True)` → `deep: restore failed: FinancialIntegrityError`, `deep_ok: False`. The exception is the guard raised inside the restore. |
| `test_complete_14_step_end_to_end_supplier_invoice_workflow` | After the review gate is satisfied, fails at the backup-restore step with the guard message verbatim. |

| File | Count |
| --- | --- |
| `test_phase12_key_migration_and_backup.py` | 10 |
| `test_phase11_audit_key_rotation.py` | 3 |
| `test_phase9_process_boundary.py` | 3 |
| `test_phase62_backup_automation.py` | 2 |
| `test_phase8_upload_boundary_and_ci.py` | 2 |
| `test_phase5_security_audit_backup.py` | 1 |
| `test_phase6_ocr_benchmark_e2e.py` | 1 |
| **Total** | **22** |

Every one is restore/backup/rotation or an end-to-end path containing a restore.
All of them pass on CI's 3.12.10.

---

## 5. Coverage by required area

| Area | Evidence |
| --- | --- |
| **Security** | `test_phase5_*`, `test_phase7_authentication_remediation` (59/59 pass), `test_phase34_security_validation`, `test_phase35_*`, RBAC/SoD, audit immutability, host-independent authz. Passing apart from guard-dependent restores. |
| **OCR** | `test_phase29_ocr_review_policy.py` **49/49 passing** (was 0/49 at baseline); benchmark e2e passes except its restore step; real RapidOCR inference verified (0.975 confidence). |
| **Accounting / procurement / inventory** | Invoice validation, discount bounds, negative-amount refusal, timbre, 3-way match, posting, reversal, trial balance — all passing. |
| **DB / migration** | `test_migration_upgrades_legacy_database_preserving_rows` passes; additive `ocr_field_reviews` + `conflict_values_json` verified on a pre-existing DB. |
| **Backup / restore / audit chain** | Logic passes; the **deep-restore executions** are the guard-limited subset (22). On CI's 3.12.10 they pass. |
| **Dependency audit** | `generate_hash_lock.py --check` and `verify_dependency_graph.py` both exit 0. |
| **Lint / types / compilation** | ruff clean; compileall clean. No separate static type checker (mypy) is configured in this repository — **NOT RUN**, not skipped silently. |
| **Desktop smoke** | **NOT EXECUTED locally** (PySide6 absent). Covered by CI's Windows job (pass). No GUI claim is made beyond that. |
| **Fresh hash-verified install** | Exit 0 in a from-scratch venv; 53/53 pins match. |

### Skips (21) and why

| Reason | Count |
| --- | --- |
| `PySide6` not installed (GUI modules) | 2 (+ related desktop modules) |
| Windows-only reparse-point / junction regressions | 2 |
| Remaining platform/feature-conditional skips | balance of 21 |

Full list: `logs/final_full_suite_3112.log` (`-ra` short summary).

---

## 6. Staging acceptance

`python tools/staging_acceptance.py` against a fresh state directory: **38/40**.

Passing includes the full enforcement chain through the real HTTP API:

```
[PASS] review: submission is refused while the document review is incomplete
[PASS] review: the review-policy verdict is readable and unsatisfied before decisions
[PASS] review: field decisions satisfy the review policy
[PASS] workflow: submitted for approval
[PASS] workflow: requester cannot approve their own invoice (segregation of duties)
[PASS] workflow: independent approver approves
[PASS] workflow: accountant posts the approved invoice
[PASS] accounting: posted journal entry is balanced at 23800.00 DZD
[PASS] accounting: posting is idempotent for the same key
[PASS] audit: chain verifies before rotation
[PASS] vault rotation: every document re-encrypted and verified
[PASS] logs: no key material in the security log or rotation journals
```

The 2 failures are both the restore guard:

- `restore: isolated restore verifies hashes, trial balance and audit chain` —
  `FinancialIntegrityError: Safe backup restore requires Python 3.11.17+…`
- `restart: a new OS process reads the restored data` — consequence of the same:
  nothing was restored to read.

---

## 7. What changed since the 946-pass Phase 35 report

The Phase 35 report recorded 946P/3S/1D/0F. That count was produced by a run that
could **not** have collected the Phase 29 test file (it was added later by
`3106882` and raised an ImportError). Phase 46:

- Fixed that collection abort — the whole reason CI was red.
- Added the missing subsystem the file exercises.
- Fixed four genuine defects found while integrating it (payload-hash omission of
  `discount_pct`; lost discount in line modification; unwired line-bounds
  validation; unguarded field-edit path).
- Grew the suite: 988 items collected (2 modules skip at import, giving 990
  outcomes reported), against 949 at Phase 35 (946P/3S/1D/0F).

Net effect on supported runtimes: **from a suite that could not run at all on CI to
all three CI jobs green.**

---

## 8. Residual risks carried forward

| Risk | Owner action |
| --- | --- |
| Local deep-restore verification impossible on 3.11.2 | Run restore tests on a supported interpreter; CI already does |
| `ERP_ALLOWED_HOSTS` documented but not implemented | Implement trusted-host middleware — G6 pre-production blocker |
| Effective-dated VAT/timbre rules referenced in docstrings but absent | Resolve Q10 (G5), then implement |
| H-1 upstream metadata contradiction | Adopt a headless-declaring OCR dependency |
| 2 GUI modules unverified locally | Windows CI covers them; desktop smoke still not executed here |

**No Critical or High defect is open in the integrated code.** The
`ERP_ALLOWED_HOSTS` gap is rated Medium-High as a *missing defence-in-depth layer*
whose practical exposure depends entirely on proxy configuration, and it is
recorded as a pre-production blocker rather than a shipping defect.
