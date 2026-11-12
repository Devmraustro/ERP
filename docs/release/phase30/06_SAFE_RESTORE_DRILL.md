# Phase 30 — isolated safe-restore drill

**Result: 25/25 checks pass on CPython 3.12.10. The drill is no longer BLOCKED.**

Script: `evidence/restore_drill.py` · Log: `logs/restore_drill_py312.log`

## Why it was blocked before

Phase 29 recorded the restore drill as not executable because the sandbox interpreter is
**CPython 3.11.2**, which predates `tarfile.data_filter` (added in 3.11.4/3.12.0). The restore path
is guarded and refuses to extract on such an interpreter — a deliberate security control protecting
against tar path-traversal and link-fallback attacks. The guard was **not** weakened.

## How a supported runtime was obtained

No supported interpreter was installed and no package manager could provide one
(`apt-get` has no `libsqlite3-dev`, `libssl-dev`, `libffi-dev`, or `libz-dev`, and release assets are
outside the allowed network). The interpreter was therefore **built from source**:

| Component | Version | Source |
| --- | --- | --- |
| CPython | **3.12.10** | `codeload.github.com/python/cpython` tag `v3.12.10` |
| zlib | 1.3.1 | `codeload.github.com/madler/zlib` tag `v1.3.1` |
| OpenSSL | 3.0.16 | `codeload.github.com/openssl/openssl` tag `openssl-3.0.16` |
| sqlite3 | 3.51.1 | `pysqlite3-binary` cp312 manylinux wheel, extension grafted in |
| libffi | system `libffi.so.8` | minimal header written for the `_ctypes` build (`evidence/ffi-minimal.h`) |
| Dependencies | **the project's own 53 pinned distributions** from `requirements.lock.txt` | PyPI wheels for cp312 |

The 53 pins are exactly those in the lock file — the drill runs the shipped dependency set, not an
ad-hoc selection. Reproducing the build is scripted in `evidence/mkstub.py` (linker stubs) and
described in `09_ENVIRONMENT.md`.

## Drill content and result

Isolated per run: a fresh temporary directory, a synthetic company (`Drill Company SARL`), the SCF
chart via `bootstrap_first_run`, synthetic audit events, one synthetic document, and **one invoice
posted through the real API** (clerk creates → approver approves → admin posts). No production
database or vault is touched.

| # | Check | Result |
| --- | --- | --- |
| 1 | Real invoice posted through the API (`workflow_state=Posted`) | **PASS** |
| 2 | Source database created | **PASS** |
| 3 | Source uses two audit key versions — `{1: 4, 2: 9}` | **PASS** |
| 4 | Backup written (50 040 bytes) | **PASS** |
| 5 | Backup reports both audit key versions `[1, 2]` | **PASS** |
| 6 | Backup report carries no key material | **PASS** |
| 7 | Restore returned a report (7 keys) | **PASS** |
| 8 | Restored database file exists | **PASS** |
| 9 | `PRAGMA integrity_check` → `ok` | **PASS** |
| 10 | `PRAGMA foreign_key_check` → 0 rows | **PASS** |
| 11 | Restored ledger balances — debit = credit = **148 750,00** | **PASS** |
| 12 | Restored totals equal source totals | **PASS** |
| 13 | Report says audit chains valid | **PASS** |
| 14 | Restored audit key versions `[1, 2]` | **PASS** |
| 15 | Audit events per key version match the source — `{1: 4, 2: 9}` | **PASS** |
| 16 | Audit chain **v1** verifies | **PASS** |
| 17 | Audit chain **v2** verifies | **PASS** |
| 18 | Audit key file restored alongside the DB | **PASS** |
| 19 | Audit key **registry** restored (`….audit.keys.json`) | **PASS** |
| 20 | Vault key material restored (`vault.vault.key`, `vault.vault.keys.json`) | **PASS** |
| 21 | Restored vault **decrypts** the stored blob, byte-identical | **PASS** |
| 22 | Source backup **byte-identical** after restore (SHA-256 unchanged) | **PASS** |
| 23 | Second restore into a fresh directory succeeds (repeatable) | **PASS** |
| 24 | Restore with a **wrong key** is refused (`InvalidToken`) | **PASS** |

Also proven by the same run: the restored database reopens through `ERPDatabase` (the recovery
deployment path), and both audit-key generations still verify after the restore.

## What this does and does not establish

**Established:** on a supported, security-patched interpreter the backup → restore → verify chain
works end to end for a database with a posted ledger, a versioned audit chain and an encrypted
vault; tamper-relevant controls (wrong key) fail closed; the source artefact is not modified.

**Not established:**

- The drill runs on **Linux**, not on the shipped Windows runtime. The guard's version rule was
  written with Windows binary installers in mind (see the comment in `security_audit.py`); the
  Windows restore path remains **NOT EXECUTED**.
- No restore was performed against a real deployment's data volume, no backup was taken from a
  running production service, and no retention/rotation policy was exercised.
- The 22 suite failures on CPython 3.11.2 are **still present and still correct** — they are the
  guard working. Run C (`02_P0_FAILURE_BASELINE.md`) proves they are environmental.
