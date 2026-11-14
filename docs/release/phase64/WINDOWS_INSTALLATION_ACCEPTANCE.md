# Phase 64 — Windows Installation Acceptance

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Status | **NOT EXECUTED — GATE BLOCKED.** No Windows machine is available to this session. Nothing below is a result. |
| What this document is | An execution package: the exact steps, the expected outcome of each, and the evidence to capture, so that one person with a clean Windows machine can close the gate in one sitting. |

## 1. What evidence exists today, and what it does not cover

| Evidence | Covers | Does not cover |
|---|---|---|
| CI job "Windows, Python 3.12.10" (run 43607302657 on `5234bf2` and run 43612393787 on the candidate's code, both success) | A 10-file test subset and staging acceptance on a GitHub-hosted Windows runner | Installation from the lock on a clean machine; the desktop application; NTFS permissions; restore by an operator; the full suite |
| CI job "Desktop UI (Qt)" (job 132484609813, success) | Qt tests on Linux with an off-screen display | Windows rendering, fonts, right-to-left layout on Windows, the packaged executable |
| `docs/DESKTOP_APPLICATION.md` §8.2 | One recorded packaging run on a developer workstation (128.3 MiB executable, SHA-256 recorded there) | A clean machine; the current commit |
| Phase 60 restore-path verification | Restore works on CPython 3.12.10 on Linux | Restore on Windows |

## 2. Preconditions

| # | Requirement |
|---|---|
| P1 | Windows 10 22H2 or Windows 11, 64-bit, freshly installed or a clean VM snapshot. No Python, no previous `%LOCALAPPDATA%\AlgerianERP`. |
| P2 | A standard (non-administrator) user account for running the application, and a second local account for the permission check in step 11. |
| P3 | Python **3.12.10** 64-bit from python.org — the last 3.12 release with a Windows installer, and the minimum for restore. Not 3.12.0–3.12.9, not 3.13. |
| P4 | The repository at the release-candidate commit `89a5fecede594e5e0f001fe2decad6379325964f`, obtained by `git clone` + `git checkout`, or as a source archive whose SHA is recorded. |
| P5 | Network access to PyPI for the install step only. Record whether a proxy was used. |
| P6 | **Synthetic data only.** No real company, supplier, employee or invoice data. |

## 3. Steps

Record for every step: the exact command, exit code, and a text capture of the output with any
password or token removed. Screenshots must not show credentials.

| # | Step | Command (PowerShell, repository root) | Expected | Result |
|---|---|---|---|---|
| 1 | Interpreter | `py -3.12 --version` | `Python 3.12.10` | NOT EXECUTED |
| 2 | Source identity | `git rev-parse HEAD` | `89a5fecede594e5e0f001fe2decad6379325964f` | NOT EXECUTED |
| 3 | Virtual environment | `py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1` | prompt shows `(.venv)` | NOT EXECUTED |
| 4 | Hash-enforced server install | `python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | exit 0; includes `colorama` (Windows-only pin); no hash mismatch | NOT EXECUTED |
| 5 | Hash-enforced desktop install | `python -m pip install --no-deps --require-hashes -r requirements-desktop.lock.hashes.txt` | exit 0; PySide6 6.11.2 | NOT EXECUTED |
| 6 | Project install | `python -m pip install --no-deps -e .` | exit 0; `dz-erp --version` prints a version | NOT EXECUTED |
| 7 | Lock integrity | `python tools/generate_hash_lock.py --check; python tools/generate_hash_lock.py --desktop --check; python tools/verify_dependency_graph.py` | all exit 0; 53 / 1317 and 4 / 20 | NOT EXECUTED |
| 8 | Full test suite | `python -m pytest -q -rs --junitxml=reports\windows-full.xml` | 0 failed. Record passed / skipped and every skip reason. Desktop tests must **run**, not skip. | NOT EXECUTED |
| 9 | Headless desktop smoke | `dz-erp --smoke` | exit 0 | NOT EXECUTED |
| 10 | First run | `dz-erp`; complete the first-run wizard with a synthetic company and a 12+ character password | company and admin created; login succeeds; files appear under `%LOCALAPPDATA%\AlgerianERP\data` | NOT EXECUTED |
| 11 | **File permissions (risk S5)** | `icacls "$env:LOCALAPPDATA\AlgerianERP\data"` and the same for `erp.sqlite3`, `*.audit.key`, `vault.vault.key`; then, logged in as the second account, try to read `erp.sqlite3` | Only the owning user, SYSTEM and Administrators have access; the second account is denied | NOT EXECUTED |
| 12 | Second launch | close and reopen `dz-erp` | no wizard; login with the same credentials; data still present | NOT EXECUTED |
| 13 | First-run cannot be repeated | attempt first-run again (see `docs/DESKTOP_APPLICATION.md` §5) | refused; existing admin unchanged | NOT EXECUTED |
| 14 | Backup | close the client; `python -m dz_erp.ops_backup --db-path … --vault-path … --backup-dir D:\erp-backup --key-file D:\keys\backup.key run`, then `verify --deep` | both exit 0; one `.erpbackup` plus manifest | NOT EXECUTED |
| 15 | **Restore on Windows** | restore that backup into an empty directory with `restore_and_verify_backup` (operator guide §11); time it | report shows `db_sha256_verified`, `audit_chains_valid`; trial balance equals the pre-backup one | NOT EXECUTED |
| 16 | Restore refuses damage | flip one byte in a copy of the backup and restore it | refused; target directory left empty | NOT EXECUTED |
| 17 | Packaging | `python -m pip install pyinstaller; python tools/build_desktop.py --smoke` | `dist\AlgerianERP.exe` + `.sha256`; packaged smoke exits 0. Record size and SHA-256. | NOT EXECUTED |
| 18 | Packaged executable on a machine **without Python** | copy the `.exe` to a second clean machine; verify the SHA-256; run it; repeat steps 10 and 12 | starts; first run works; SmartScreen behaviour recorded (the executable is unsigned) | NOT EXECUTED |
| 19 | Uninstall | `pip uninstall` the project (or delete the `.exe`) | `%LOCALAPPDATA%\AlgerianERP` is **not** deleted | NOT EXECUTED |
| 20 | Upgrade in place | install the release candidate over a data directory created by the previous integration commit `5234bf2`; start | starts; data intact; audit chain verifies | NOT EXECUTED |

## 4. Known limits the tester should expect

* The executable is **not code-signed**. Windows SmartScreen will warn. Signing is not implemented.
* There is no MSI/installer; "installation" is `pip install` or copying one `.exe`.
* POSIX file modes mean nothing on Windows. The owner-only permission fixes made in this mission are
  no-ops there by design; step 11 is the only evidence that will exist for Windows file protection.
* The desktop application has no backup or restore button; steps 14–16 use the command line.

## 5. Sign-off

| Field | Value |
|---|---|
| Tester name and role | |
| Machine (edition, build, VM or physical) | |
| Commit tested | |
| Date | |
| Steps passed / failed / not executed | / / |
| Defects raised (IDs) | |
| Signature | |

**Gate status: BLOCKED** until this table is completed by a named person with steps 1–16 passed.
