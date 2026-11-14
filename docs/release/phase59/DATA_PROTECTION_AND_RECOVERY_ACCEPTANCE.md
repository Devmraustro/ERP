# Phase 59D — Data Protection and Recovery Acceptance

**Repository:** `https://github.com/Devmraustro/ERP.git` · **Branch:** `arena/63d762c8-erp` ·
**Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`
**Environment:** isolated `/tmp/acc2`, synthetic data, CPython **3.11.2**.
**Status:** backup creation and integrity verification were **executed**. Restore was **NOT**
executable in this environment (§5) — this document does not claim otherwise.

---

## 1. Verdict, item by item

| Property | Status | Evidence |
|---|---|---|
| Encrypted backup produced | **PASS — EXECUTED** | `erp-20261113T235818Z.erpbackup`, 47 692 bytes |
| Archive really is encrypted | **PASS — EXECUTED** | first bytes `gAAAAABq96Qa…` (Fernet), no `SQLite format 3` header, no `ustar` at offset 257 |
| Archive + manifest permissions | **PASS — EXECUTED** | both `-rw-------` (`0o600`) |
| Manifest content and hash | **PASS — EXECUTED** | `sha256 4da34acb…bd52` matches the file; `db_sha256 9dcca3fe…0901`; schema `1`; key versions recorded |
| Tamper detection | **PASS — EXECUTED** | one flipped byte → `ok: false`, `sha256_matches: false` |
| **Missing manifest is refused** | **FIXED IN THIS PHASE** | previously `ok: true`; now `ok: false` + `MANIFEST_MISSING` (§3.1) |
| Wrong key refused | **PASS (shallow) — EXECUTED** | restore attempt fails, `ok: false` |
| Deep restore + trial-balance parity | **BLOCKED — environment** | Python 3.11.2 < 3.11.17 floor (§5) |
| Interrupted-rotation recovery | **PASS — automated suite** | `tests/test_phase10_vault_key_rotation.py`, `test_phase13_key_rotation_regression.py` |
| Key-version compatibility across restore | **BLOCKED here** (needs 3.12.10) | `tests/test_phase11_audit_key_rotation.py` — 3 tests fail on this floor |
| RPO / RTO | **NOT MEASURED** | No production cadence or host; no number is asserted |

## 2. What the backup actually contains

Manifest as written (verbatim, one artefact):

```json
{"audit_key_versions": [{"age_days": 0.0, "created_at": "2026-11-13T23:58:18.820609+00:00",
                         "status": "active", "version": 1}],
 "backup_file": "erp-20261113T235818Z.erpbackup",
 "db_sha256": "9dcca3fe23f8f064683b11ee8cbe6a422250603b93aeb71acc41e86b81190901",
 "legal_hold": false, "retention_class": "daily", "schema": 1,
 "sha256": "4da34acb2360faf17530ca23829168c4406554299d1c873955ae43df449cbd52",
 "size_bytes": 47692, "tool": "dz_erp.ops_backup/phase10", "vault_file_count": 0,
 "vault_key_versions": [{"age_days": 0.0, "status": "active", "version": 1}]}
```

Points worth an auditor's attention:

* the manifest carries **no key material and no customer data** — only digests, sizes, a schema
  number and key-version *descriptors*. That is what lets an integrity monitor verify a backup
  **without** the decryption key.
* `vault_file_count: 0` is correct for this test deployment: no document was uploaded. A real
  deployment that has processed invoices must expect a non-zero count; if it is `0` while documents
  exist in `source_documents`, the vault directory is misconfigured.
* the manifest is a **sidecar** (`<name>.manifest.json`), written `0o600` alongside the archive, and a
  prune removes the two together (`test_prune_removes_the_manifest_with_the_artefact`).

## 3. Defect found and fixed in this phase

### 3.1 DEFECT — an unverifiable artefact reported success *(fixed)*

**Observation (executed).** Copying a valid archive to a new name **without** its manifest and calling
`verify_backup` returned:

```json
{"ok": true, "checks": {"exists": true, "manifest_present": false,
                        "sha256_matches": null, "size_matches": true}}
```

`verify_all` over that directory likewise reported `ok: true`.

**Why that is a defect, not a design choice.** `dz_erp/ops_backup.py` states its own purpose in its
module docstring: *"an integrity monitor must be able to detect a truncated or corrupted file
**without** the decryption key. A separately written `.manifest.json` … makes that possible"*. The
archive is Fernet-encrypted, so with the manifest absent there is **nothing** to compare the bytes
against. Returning success means a monitor stays green while the artefact may be truncated,
corrupted or swapped — the precise failure the manifest exists to prevent. No test asserted the
permissive behaviour, so it was an unexamined default (`sha256_matches in (True, None)`), not a
contract.

**Fix.** `verify_backup` now fails closed:

```python
ok = checks["exists"] and checks["sha256_matches"] is True and checks.get("size_matches", True)
```

and a missing manifest records `failure: "MANIFEST_MISSING: …"`.

**Verification (executed, after the fix):**

| Case | `ok` | `sha256_matches` |
|---|---|---|
| intact archive + manifest | `true` | `true` |
| one byte flipped + manifest | `false` | `false` |
| intact archive, **no manifest** | **`false`** | `null` + `MANIFEST_MISSING` |

**Regression test.** `tests/test_phase62_backup_automation.py::
test_verify_refuses_an_artefact_whose_manifest_is_missing` covers all three cases **and** asserts that
`verify_all` over the directory does not report a clean sweep.

**Operational consequence to state plainly.** Any deployment that has ever pruned, moved or copied
backups **without** their `.manifest.json` will now see `BACKUP_INTEGRITY_FAILED` where it previously
saw green. That is the intended behaviour: those artefacts genuinely were not verifiable. The remedy
is to re-take a backup, or to regenerate the manifest from a trusted copy.

### 3.2 Secondary finding — `--deep` without a key

`verify_backup(..., deep=True, key=None)` returns `deep: "skipped: no key supplied"` and leaves
`deep_ok` absent, while `ok` stays `true` on the digest. That is defensible (the digest *did* pass)
**provided the report is read carefully**. It is now pinned by
`test_a_deep_verify_without_a_key_reports_that_it_could_not_restore`, so the wording cannot silently
change from "skipped" to something that reads like "passed".

**Operator rule:** a `--deep` run without `ERP_BACKUP_KEY_FILE` proves *nothing* about restorability.

## 4. Rotation, key versions and the crash-safety design

Covered by the automated suite, not re-executed by hand here:

| Property | Test |
|---|---|
| Vault key rotation, old versions still readable | `tests/test_phase10_vault_key_rotation.py` |
| Audit-key rotation keeps every version | `tests/test_phase11_audit_key_rotation.py` |
| Rotation interrupted mid-way is recoverable | `tests/test_phase13_key_rotation_regression.py` |
| Pre-versioning database still restores | `test_a_pre_versioning_database_restores_from_backup` |
| Restore refuses a contradicting audit key | `test_restore_with_a_contradicting_audit_key_is_refused` |
| Restore refuses an absent key version | `test_restore_reports_a_missing_key_version_as_unavailable` |
| Restore rejects non-canonical tar paths | `test_restore_rejects_noncanonical_tar_paths_before_writing[../outside.txt]` and 2 more |
| Restore refuses a pre-existing hardlink target | `test_restore_refuses_preexisting_hardlink_target_before_writing` |
| Restore is idempotent from a fresh process | `test_phase9_process_boundary.py::test_repeated_restores_are_idempotent_and_leave_a_usable_deployment` |

**These 9 rows could not be executed in this sandbox** and fail with the version guard (§5). On
CPython 3.12.10 they are expected to pass — CI runs them on 3.12 — but this document does not present
an unexecuted expectation as a result.

## 5. Why restore could not be executed here

```
Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13);
unsafe archive member types and paths are rejected.
```

Observed as `deep: "restore failed: FinancialIntegrityError"` on every artefact. The guard is
deliberate: `tarfile` link/special-member handling was the subject of a CVE class, and the restore
path refuses to run on interpreters without the fix rather than being silently unsafe. The sandbox
has 3.11.2. Attempts to obtain a compliant interpreter:

| Attempt | Result |
|---|---|
| `curl` python-build-standalone 3.12.10 release asset | exit 35 (TLS/connection) — GitHub release assets unreachable |
| `pbs_installer 3.12.10` | `TLS/SSL connection has been closed` |
| Debian/apt Python 3.12 | mirrors unreachable from this sandbox |
| build from source | no `zlib.h`, `openssl/ssl.h`, `sqlite3.h` or `ffi.h` in the image |

So the honest status is **BLOCKED — environment**, with the exact command an operator should run on a
supported interpreter in §7.

## 6. What an operator must still do (handover)

1. **Set `ERP_BACKUP_KEY_FILE`** to a `0o600` file on a volume *separate* from the backup directory.
   A key stored beside the backups is not protection; it is one theft away from plaintext.
2. **Take a backup and verify it** on the target host:
   `python -m dz_erp.ops_backup run` then `… verify` — expect `ok: true` with `manifest_present: true`.
3. **Run the deep verify on a supported interpreter** (3.12.10+) and keep the JSON:
   `python -m dz_erp.ops_backup verify --deep`.
4. **Restore into an isolated directory** and compare the trial balance against the source
   (`docs/admin/ADMINISTRATION_AND_RECOVERY.md` has the procedure). Until that has been done once on
   the real host with real data, **no RPO or RTO figure may be quoted**.
5. **Ship backups off-host, encrypted**, and confirm a restore from the off-host copy — an on-host
   copy is not a disaster-recovery plan.
6. **Monitor the verify job.** Now that a missing manifest fails, a green verify genuinely means every
   artefact had a matching digest.

## 7. Evidence index

| Claim | Command |
|---|---|
| Backup created | `python -m dz_erp.ops_backup --backup-dir … run` |
| Shallow verify | `python -m dz_erp.ops_backup --backup-dir … verify` → `verified: 1, failed: 0` |
| Deep verify | `… verify --deep` → **fails here on the Python floor** |
| Encryption | `head -c 32 file.erpbackup` → `gAAAAA…`, no SQLite header |
| Permissions | `ls -la` → `-rw-------` for archive, manifest and status file |
| Tamper | flip a byte, copy the manifest → `sha256_matches: false` |
| Missing manifest | copy without the manifest → `ok: false`, `MANIFEST_MISSING` |
| Regression tests | `pytest tests/test_phase62_backup_automation.py -k "manifest or deep"` |
