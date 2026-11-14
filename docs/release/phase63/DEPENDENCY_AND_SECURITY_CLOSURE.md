# Phase 63 — Dependency and Security Closure

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Interpreters used | CPython 3.12.10 (built from source for this audit) and system CPython 3.11.2 |
| Dependencies changed | **None.** No package was upgraded, added or removed. |
| Result | Dependency gates pass. **Three security defects were found by running the product, fixed, and covered by regression tests.** Eight register risks remain Open with owners; five of them are High. |

## 1. Dependency set

| File | Content | SHA-256 (first/last 8) |
|---|---|---|
| `requirements.lock.txt` | 53 exact pins (`colorama` is `sys_platform == "win32"` only) | `194dd124…f7370c60` |
| `requirements.lock.hashes.txt` | the same 53 distributions with **1317** hashes | `e6d5dbe7…afd74390` |
| `requirements-desktop.lock.txt` | 4 pins, PySide6 family 6.11.2 | `a7b00d41…29cb06c` |
| `requirements-desktop.lock.hashes.txt` | 4 distributions, 20 hashes | `53c56174…cfa1f5` |

| Check | Command | Result |
|---|---|---|
| Lock ↔ hash file agree | `python tools/generate_hash_lock.py --check` | ok — 53 distributions, 1317 hashes |
| Desktop lock ↔ hash file | `python tools/generate_hash_lock.py --desktop --check` | ok — 4 / 20 |
| Hash-enforced install | `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` into a clean venv | installs; every artefact matched a pinned hash |
| Dependency graph | `python tools/verify_dependency_graph.py` | ok |
| Known vulnerabilities, main lock | `pip-audit` 2.10.1 on the committed lock, 52 non-Windows distributions | **0 known vulnerabilities** |
| Known vulnerabilities, Windows-only | `pip-audit` on `colorama` alone | 0 |
| Known vulnerabilities, desktop lock | `pip-audit`, 4 distributions | 0 |
| Lint | `ruff check dz_erp tests tools` | clean (before and after the fixes) |
| Byte-compile | `python -m compileall dz_erp` | clean |

Two things a reader should not over-read:

* `pip-audit` reports what is **published in the advisory database on the day it runs**. Zero today is
  not a property of the software; the audit must be repeated at release time.
* `--require-hashes` only succeeds with `--no-deps`, and `pip check` reports one unmet requirement:
  `rapidocr-onnxruntime` asks for `opencv-python`, while the lock deliberately ships the headless
  build. This is register risk **B1 (Accepted)**; it is unchanged.

The desktop lock was audited by metadata only. PySide6 was not installed and no Qt code was executed
here (see Phase 62 and Phase 64).

## 2. Defects found and fixed in this mission

All three were found by starting the product and looking at what it wrote to disk, not by reading
code. Each fix has a test that fails without it.

| ID | Defect | Severity | Fix | Regression test |
|---|---|---|---|---|
| S63-1 | **A new database file was created `0644`.** `--bootstrap-admin` under the common umask 022 left the SQLite ledger readable by every local account, while the key files beside it were `0600`. | High on a shared host | `dz_erp/database.py`: the file is created `0600` before SQLite opens it. Commit `ac79921`. | `tests/test_phase63_database_file_permissions.py` — 5 tests; 4 fail without the fix |
| S66-1 | **A restore left the database and `manifest.json` `0644`**, whatever the umask. The manifest carries every audit and vault key version. The key store tightens the directory to `0700` a few steps later, so the exposure was bounded — but the files were readable during extraction and verification, and the documented promotion step moved a `0644` database into the live location. | Medium | `dz_erp/security_audit.py`: everything a restore writes is set `0600`/`0700`. Commit `89a5fec`. | `tests/test_phase66_restore_file_permissions.py` — 4 tests; 3 fail without the fix |
| S66-2 | **The backup staging directory depended on the umask.** While a backup is assembled, a plaintext database copy and the key manifest sit in `.tmp_backup_*`. | Low (transient; the shipped systemd units already set `UMask=0077`) | Same commit: the directory is created `0700`. | Same file, `test_the_backup_staging_directory_is_owner_only` |

These are POSIX controls. On Windows, mode bits do not express access control at all — that is register
risk **S5**, still Mitigated-not-closed, and is the reason Phase 64 asks for an NTFS ACL check.

A fourth defect, functional rather than a security one, is described in Phase 67 (credit notes could
not be approved or posted over HTTP).

## 3. Security controls — verified by execution

Checked against a running server on loopback with synthetic data (evidence:
`docs/release/phase67/evidence/acceptance_checks.json`, checks `W01` and `W24`).

| Control | Expected | Observed |
|---|---|---|
| Start without `ERP_DB_PATH` | refuse | refused |
| Start with no users | refuse | exit 2, instructs to bootstrap |
| `ERP_HOST=0.0.0.0` without `ERP_ALLOWED_HOSTS` | refuse | refused |
| Unknown `Host` header | 400 | 400 `Invalid Host header.` |
| No credential | 401 | 401 |
| Wrong password | 401, no user enumeration | 401 `Invalid username or password.` |
| Password shorter than `ERP_PASSWORD_MIN_LENGTH=12` | refuse | 422 `Password must be at least 12 characters long.` |
| API docs endpoints | off by default | `/docs`, `/redoc`, `/openapi.json` → 404 |
| Response headers | nosniff, no-referrer, frame DENY, request id | present |
| Session token | opaque, server-side, expiring | prefix `dzsess_`, 8 h expiry, company resolved server-side |
| Logout | token dead immediately | revoke 200, then 401 `…has been revoked.` |
| Deactivated user | live token dead immediately | 401 `User account is inactive or revoked.` |
| Admin self-deactivation | refuse | 409 |
| Role checks | least privilege | clerk cannot manage users (403) or approve; approver cannot post; auditor cannot create partners or purchase orders |
| Segregation of duties | creator ≠ approver | 403 `SEGREGATION_OF_DUTIES_VIOLATION` for invoices and for reversals |
| Posted ledger rows | immutable even to direct SQL | UPDATE/DELETE on posted entries and lines refused by trigger |
| Audit events | append-only, chained | UPDATE/DELETE refused by trigger; chain verifies before rotation, after rotation, and after restore |
| Security log | durable, no secrets | 71 records; neither a password nor a session token appears in the file |
| Vault | encrypted at rest | stored blob does not start with `%PDF`; decrypts to the original SHA-256 after rotation and restore |
| Key files | `0600`, directory `0700` | as expected; `/readyz` reports `key_mode 0o600`, `dir_mode 0o700` |
| Backup artefact | encrypted, authenticated | `0600`; no SQLite header in the file; a one-bit change or a wrong key is refused before anything is written |
| Login rate limit | bounded, fails closed | 10 failures / 300 s per client+account, in-memory (single process) |
| Upload limits | size and type | 10 MiB; pdf / jpeg / png |

## 4. Observations that are not fixed here

Each has an owner and a disposition. None was changed, because each is a design or policy decision and
this mission is a feature freeze.

| ID | Observation | Severity | Owner | Disposition |
|---|---|---|---|---|
| S63-2 | Running `--bootstrap-admin` again with the **same** username is refused ("this database is already bootstrapped") — that is what Phase 59 recorded as idempotence. With a **different** username it succeeds and creates a **second company** (`BOOT-ADMIN2`) with its own admin; re-confirmed on a scratch database. Tenant isolation holds, but an operator who mistypes a username gets a silent second tenant. | Medium | Product owner | Open. Decide: refuse when any company exists unless an explicit flag is given. Until then the operator guide says "bootstrap exactly once". |
| S63-3 | After `rotate_keys audit-rotate`, a **running** server keeps signing with the previous key version until it is restarted (observed: 49 events on v1 after rotation; v2 used only after restart). The chain stays valid, but the old key is still in use, which defeats a rotation done because a key is suspected leaked. | Medium | Operations owner | Open. The operator guide now requires a restart after any rotation. A code change (reload or refuse-while-running) needs a decision. |
| S63-4 | Documentation drift: `docs/RISK_REGISTER.md` row B2 and `docs/STAGING_SECURITY_CONFIGURATION.md` line 276 say **51 pins / 1277 hashes**; the committed lock has **53 / 1317**. | Low | Documentation owner | Open. Not edited here to avoid touching prior release evidence; correct in the next documentation change. |
| S63-5 | Rate limiting is in-memory by default and bounds one process only. | Low for the supported single-process deployment | Operations owner | Accepted as documented in `docs/ENVIRONMENT_VARIABLES.md`; set `ERP_RATE_LIMIT_BACKEND=sqlite` if more than one worker is ever run. |
| S63-6 | Rejected business operations (duplicate invoice, failed validation) answer **HTTP 200** with a state such as `DuplicateRejected`, and each validation error is listed twice. | Low | Product owner | Open. Integrators must read `workflow_state`, not the status code. Stated in the operator guide. |

## 5. Risk register mapped to evidence

`docs/RISK_REGISTER.md`: 31 rows — 8 Open, 17 Mitigated, 5 Accepted, 1 Closed
(`python tools/risk_register_counts.py` → consistent). The register was **not edited**.

| ID | Risk | Severity | Status | Owner (from register) | What this mission adds |
|---|---|---|---|---|---|
| S1 | Key material is a readable file, no HSM | High | Open | Operations owner | File modes verified; fixes S63-1/S66-1 close two places where a file was *more* readable than the keys. The risk itself is unchanged. |
| S3 | No two-person rule for key operations | High | Open | Operations owner | Unchanged. Rotation was run by a single operator in the drill. |
| C1 | SCF mapping / journals / rounding vs statute | High | Open | Expert-Comptable | Unchanged — G5 |
| C2 | VAT rates, timbre brackets, G50 mapping | High | Open | Fiscaliste | Unchanged — G5 |
| C6 | Law 18-07 obligations | High | Open | DPO / counsel | Unchanged |
| C4 | 380 vs 408 clearing account | Medium | Open | Expert-Comptable | Behaviour confirmed: receipts and invoices use `380`. Decision still the expert's. |
| C5 | Retention period unapproved | Medium | Open | Expert-Comptable + DPO | Unchanged |
| D4 | No drill on production-scale data or real media | Medium | Open | Operations owner | Phase 66 drill is again synthetic (87 KB). Unchanged. |
| S5 | Windows mode bits are not access control | Medium | Mitigated | Operations owner | The new permission fixes are no-ops on Windows by design; NTFS ACLs remain the control and are untested. |
| B1 | `opencv-python-headless` substituted | see register | Accepted | see register | Confirmed still the only `pip check` complaint. |
| B2 | Lock size statement | Medium | Mitigated | see register | Stale numbers, see S63-4. |

**Proposed new register rows** (for the owner to add; not added here): S63-2, S63-3, and from Phase 67
the unrecorded stock deduction helper (W67-3).

## 6. Gate

| Gate | Status |
|---|---|
| Dependency pins, hashes, graph, audit | **PASS** (as of 2026-11-14) |
| Lint | **PASS** |
| Security defaults | **PASS** after fixes S63-1, S66-1, S66-2 |
| Critical or High defects known and unfixed | **None in code.** Five High *risks* remain Open in the register (S1, S3, C1, C2, C6); they are custody, compliance and legal items that code cannot close. |
