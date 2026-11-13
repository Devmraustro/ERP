# Phase 30 — the 22-failure baseline, classified

**Verdict: all 22 are environmental. None is a regression. Proven by execution, not by assertion —
every one of the 22 passes on CPython 3.12.10.**

## Method

Three independent runs, same source tree:

| # | Environment | Interpreter | Command | Result |
| --- | --- | --- | --- | --- |
| A | GUI (`QT_QPA_PLATFORM=offscreen`, PySide6 6.11.2) | CPython 3.11.2 | `pytest tests/ -q --deselect …test_no_installed_distribution_is_absent_from_the_lock` | **838 passed, 22 failed, 3 skipped** |
| B | Lock-only (no Qt) | CPython 3.11.2 | `pytest tests/ -q` | **813 passed, 22 failed, 20 skipped** |
| C | Supported runtime | CPython **3.12.10** | `pytest tests/ -q` | **835 passed, 0 failed, 20 skipped** |

Run C is the decisive one: the same 22 test IDs, on an interpreter the guard accepts, with zero
failures. Comparing A and C isolates the variable to the interpreter version.

## The root cause, demonstrated

The guard at `dz_erp/security_audit.py:1084` refuses to extract a backup archive on an interpreter
that lacks the CVE-hardened `tarfile` extraction filter:

```
Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13);
unsafe archive member types and paths are rejected.
```

Measured on the sandbox interpreter:

| Probe | CPython 3.11.2 (sandbox) | CPython 3.12.10 (built for this phase) |
| --- | --- | --- |
| `sys.version_info[:3]` | `(3, 11, 2)` | `(3, 12, 10)` |
| `hasattr(tarfile, "data_filter")` | **False** | **True** |
| Guard passes | **No** | **Yes** |
| All 22 tests | 22 failed | **22 passed** |

`tarfile.data_filter` was introduced in 3.11.4 / 3.12.0. The sandbox's 3.11.2 predates it, so the
extraction filter the restore path depends on **does not exist**. The guard is therefore correct:
the restore path genuinely cannot run safely there. It is a security control, and per the Phase 30
brief it was **not** modified.

## Per-test classification

All 22 share one classification: **environmental (interpreter below the safe-restore floor)**.
Evidence is identical for every row — the test passes in run C on the same source.

| # | Test | Raised | Fails on 3.11.2 | Passes on 3.12.10 |
| --- | --- | --- | --- | --- |
| 1 | `test_phase11_audit_key_rotation.py::test_a_pre_versioning_database_restores_from_backup` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 2 | `…::test_backup_restore_preserves_every_audit_key_version` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 3 | `…::test_restore_reports_a_missing_key_version_as_unavailable` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 4 | `test_phase12_key_migration_and_backup.py::test_backup_restores_mixed_ciphertext_into_an_isolated_environment` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 5 | `…::test_restore_of_a_tampered_registry_fails_closed` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 6 | `…::test_restore_rejects_ciphertext_that_needs_an_absent_key` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 7 | `…::test_restore_accepts_last_windows_binary_runtime_with_strict_preflight` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 8 | `…::test_restore_rejects_noncanonical_tar_paths_before_writing[../outside.txt]` | guard raised before path check | yes | **yes** |
| 9 | `…::test_restore_rejects_noncanonical_tar_paths_before_writing[vault/../outside.txt]` | guard raised before path check | yes | **yes** |
| 10 | `…::test_restore_rejects_noncanonical_tar_paths_before_writing[../evil/../dest/sub/file]` | guard raised before path check | yes | **yes** |
| 11 | `…::test_restore_refuses_preexisting_hardlink_target_before_writing` | `FinancialIntegrityError: Safe backup extraction is unavailable in this Python runtime…` | yes | **yes** |
| 12 | `…::test_restore_requires_a_backup_key_and_does_not_leak_on_failure` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 13 | `…::test_restore_with_a_contradicting_audit_key_is_refused` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 14 | `test_phase5_security_audit_backup.py::test_audit_immutability_log_redaction_and_encrypted_backup_restore` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 15 | `test_phase62_backup_automation.py::test_deep_verify_restores_and_compares_the_ledger` | `ok=False, deep: restore failed: FinancialIntegrityError` | yes | **yes** |
| 16 | `…::test_cli_verify_deep_restores` | CLI exit 1 (`BACKUP_INTEGRITY_FAILED`, deep restore) | yes | **yes** |
| 17 | `test_phase6_ocr_benchmark_e2e.py::test_complete_14_step_end_to_end_supplier_invoice_workflow` | `FinancialIntegrityError` (guard, at the restore step) | yes | **yes** |
| 18 | `test_phase8_upload_boundary_and_ci.py::test_backup_and_restore_preserve_the_audit_key` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 19 | `…::test_restored_database_and_vault_are_reopenable_without_the_original_secrets` | `FinancialIntegrityError` (guard) | yes | **yes** |
| 20 | `test_phase9_process_boundary.py::test_restore_is_usable_from_a_fresh_process_in_a_different_directory` | child process exit 1 with the guard message | yes | **yes** |
| 21 | `…::test_incomplete_restore_is_rejected` | guard message instead of the expected `failed SHA-256 verification` | yes | **yes** |
| 22 | `…::test_repeated_restores_are_idempotent_and_leave_a_usable_deployment` | child process exit 1 with the guard message | yes | **yes** |

Rows 8–11 and 21 are the interesting ones: they assert *specific* failure modes (path escapes,
hardlinks, tamper detection). On 3.11.2 the guard fires **before** the code under test, so the
expected message never appears — the tests fail for the right reason and the product is not at
fault. On 3.12.10 they pass, which means the path-traversal and hardlink defences are genuinely
exercised.

## GUI vs lock-only: why the counts differ, and why the failure sets do not

| | GUI (A) | Lock-only (B) |
| --- | --- | --- |
| Passed | 838 | 813 |
| Failed | 22 | 22 |
| Skipped | 3 | 20 |
| Deselected | 1 | 0 |

- **The difference is PySide6.** With Qt present, 15 desktop window tests run; without it they are
  skipped by design (`requires_qt` marks them `skipif` so a machine without the `desktop` extra
  skips instead of erroring). 838 − 813 = 25 = 15 newly-running tests + 10 other tests that only
  execute when Qt is importable.
- **The one deselection in run A** is `test_no_installed_distribution_is_absent_from_the_lock`,
  which by construction fails when PySide6 is installed outside the lock (it is a `desktop` extra
  of `pyproject.toml`, deliberately absent from `requirements.lock.txt`). Run B executes it and it
  passes — that is the environment it was written for.
- **The failure set is identical in both** (the same 22 IDs), which is what makes the
  interpretation safe: the failures track the interpreter, not the presence of Qt.

## Acceptance criteria

| Criterion | Result |
| --- | --- |
| Zero newly introduced failures | **MET** — 22 failed before Phase 29 and after; 22/22 pass on a supported runtime |
| Every existing failure individually classified | **MET** — table above |
| No failure dismissed as environmental without reproducible evidence | **MET** — run C reproduces the pass; `restore_drill.py` reproduces the full restore |
| GUI vs lock-only differences explained | **MET** — PySide6 presence only |
| Safe-restore guard not weakened | **MET** — `dz_erp/security_audit.py` unmodified |
