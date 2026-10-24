# Key Migration and Recovery Guide

**Audience:** operators and on-call engineers running this ERP.
**Scope:** upgrading a deployment to key versioning, rotating either key, recovering from an
interrupted rotation, and restoring from backup. Design rationale: `KEY_ROTATION_DESIGN.md`.

> This guide applies to isolated non-production staging **and** to a future production deployment.
> It does not by itself authorise a production change: production also requires the compliance
> gates in `ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` and a verified backup.

---

## 1. What the upgrade changes (and what it does not)

| | Behaviour |
|---|---|
| Existing vault blobs | **Untouched.** Legacy ciphertext stays as-is and remains readable. |
| Existing audit events | **Untouched.** No hash is recomputed and no event is re-signed. |
| Database schema | One column added, `audit_events.hmac_key_version`, `NOT NULL DEFAULT 1`. |
| Key files | Unchanged on upgrade. A `.keys.json` registry appears only at the first rotation. |
| New vault blobs | Carry a `DZKV` + version header. |
| New audit events | Record the key version that signed them. |

The migration is idempotent and runs on every start-up; it performs no destructive operation and
never discards a row or a blob it does not understand.

**Do not skip the backup.** Adding a nullable/defaulted column is a low-risk schema change, but an
irreversible key operation later in this guide depends on having a verified copy.

---

## 2. Before you rotate: preconditions

Run these in order and stop at the first failure.

```bash
# 1. Everything currently verifies and every blob decrypts.
python -m dz_erp.rotate_keys status --db "$ERP_DB_PATH" --vault "$ERP_VAULT_PATH"
#    Expect: "chains": {"valid": true, ...}, "vault": {"verification": {"failed": 0, ...}}

# 2. Take a backup and prove it restores into an isolated directory.
#    (create_encrypted_backup / restore_and_verify_backup; see §5)

# 3. Confirm the backup names the key versions a restore will need.
#    meta["vault_key_versions"] / meta["audit_key_versions"] list versions only - no material.

# 4. Confirm custody of every listed version: they must exist off-host, not only in the
#    directory you are about to modify.
```

Rotate **one store at a time**. Vault rotation and audit rotation are independent; doing them in
separate changes keeps the blast radius and the review surface small.

---

## 3. Rotating the vault key

```bash
# Dry run: shows the distribution of blobs by key version, a full decryptability sweep,
# and which version would be activated. Changes nothing.
python -m dz_erp.rotate_keys vault-rotate --vault "$ERP_VAULT_PATH" --dry-run

# Real rotation.
python -m dz_erp.rotate_keys vault-rotate --vault "$ERP_VAULT_PATH"
```

Success criteria in the JSON report:

* `"verified_ok": true` and `"verification": {"failed": 0}`;
* `"attempted"` equals the number of blobs; `completed + skipped_already_current == attempted`;
* `failed == 0`, or every entry in `errors` is a document you already knew was unreadable.

If `failed > 0`, **stop**. The affected blobs were not modified and were not deleted; see §6.

What happens internally: the new version is registered and persisted, then each blob is decrypted
with its own recorded version, re-encrypted with the new active version, verified by round-trip,
and atomically replaced only if it has not changed since it was read.

### Very large vaults

Rotation is resumable. If you stop it (Ctrl-C, restart, crash), simply run the same command with
the same key again and it continues. When resuming after an interruption, re-run with the **same
key material**; the CLI treats already-registered material as a resume rather than creating a new
version:

```python
# If you need to resume explicitly with the key you generated:
vault.rotate_key(existing_version_material)   # -> report["resumed"] is true
```

Blobs already at the active version are reported as `skipped_already_current`, never as newly
migrated.

---

## 4. Rotating the audit signing key

```bash
python -m dz_erp.rotate_keys audit-rotate --db "$ERP_DB_PATH" --dry-run
python -m dz_erp.rotate_keys audit-rotate --db "$ERP_DB_PATH"
```

Success criteria: `"chain_valid_before": true` **and** `"chain_valid_after": true`.

Nothing is rewritten. The only durable change is one atomic registry file. Historical events keep
the version that signed them and continue to verify; new events use the new version.

The command **refuses** to run if the chain does not already verify:

| Refusal | Meaning | Action |
|---|---|---|
| `REFUSED: … does not verify (['EVENT_HASH_TAMPERED'])` | Someone modified events | Treat as a security incident. Do not rotate. Restore from a verified backup or investigate. |
| `REFUSED: … needs key version(s) […] that are not available` | A key is missing | Restore that key version, then retry. |

---

## 5. Backup and restore

### 5.1 Creating a backup

`create_encrypted_backup(db, vault, output_backup_path, backup_encryption_key)` writes a
Fernet-encrypted archive containing the SQLite online snapshot, the vault blobs, a SHA-256
manifest, **every** key version registry, and a material-free version summary.

* The registries are inside the encrypted archive. The archive's own key is therefore a
  **recovery-critical** secret: without it nothing in the backup can be read. Store it separately
  from the archive.
* `meta["vault_key_versions"]` / `meta["audit_key_versions"]` name the versions a restore will
  need. Print them into your runbook **without** the archive key.

### 5.2 Restoring

`restore_and_verify_backup(backup_path, backup_encryption_key, restore_target_dir)` verifies the
database SHA-256, every vault blob's SHA-256, the trial balance, and every audit chain, then writes
the key registries and the version-1 legacy key files beside the restored artefacts.

**Restoring the database is not the same as restoring the keys.**

| Restored artefact | What it contains | Why it matters |
|---|---|---|
| `erp_snapshot.sqlite3` | Data | Useless without the audit key versions |
| `vault/` | Ciphertext | Useless without the vault key versions |
| `vault.vault.keys.json`, `erp_snapshot.sqlite3.audit.keys.json` | Key **material**, mode `0600` | Produced by the restore from the archive |
| `vault.vault.key`, `erp_snapshot.sqlite3.audit.key` | Version-1 (legacy) key | Keeps pre-rotation tooling meaningful |

A backup restored **without** its archive key cannot be opened. A backup restored with an archive
key whose *registry* is intact is self-sufficient — that is the design goal, and it is verified by
`tests/test_phase12_key_migration_and_backup.py`.

Restore into an empty directory. The command refuses to write into a non-empty target and validates
environment configuration **before** writing anything, so a failed restore does not leave a
half-usable deployment.

---

## 6. Failure handling

| Symptom | Cause | Safe action |
|---|---|---|
| Start-up: `VaultKeyUnavailableError … require vault key version(s) [N]` | Registry lacks a version that ciphertext needs | Restore that version from off-host custody. **Never** generate a replacement: the existing blobs would be permanently unreadable. |
| Start-up: `AuditKeyUnavailableError … signed with key version(s) [N]` | Registry lacks a version that events need | Restore that version. Do not "fix" it by rotating. |
| Read: `InvalidToken` | Corrupt ciphertext, or the wrong key for that blob's version | Correlate with the rotation report. Do not delete the blob. |
| `KeyConfigurationError: ERP_VAULT_KEY disagrees with the active key` | Stale environment override after rotation | Update or remove the variable. The registry is authoritative once it exists. |
| `RotationInProgressError` | Another rotation holds the lock | Wait for it, or investigate a stale process. Never delete a lock file while a rotation may be running. |
| `KeyVersionInUseError` on removal | Data still depends on the version | Re-encrypt first (vault), or keep the version (audit history cannot be re-signed). |
| Report `failed > 0` with `InvalidToken` | Blob was already corrupt before rotation | Quarantine the blob for investigation; the rest of the store migrated normally. |

### Interrupted rotation

State after an interruption is *mixed but consistent*: some blobs at the old version, some at the
new, and **both keys present in the registry**. Everything remains readable. Resume by re-running
the rotation with the same key (§3). The journal
(`<vault>.vault.rotation.jsonl`) shows a `rotation_started` with no matching `rotation_finished`
for the interrupted run.

---

## 7. Rollback

```bash
python -m dz_erp.rotate_keys vault-rollback  --vault "$ERP_VAULT_PATH" --version 1
python -m dz_erp.rotate_keys audit-rollback  --db "$ERP_DB_PATH" --version 1
```

Rollback re-activates an existing version for **new** writes. It never rewrites existing data,
never deletes anything, and leaves documents/events written under the newer version readable.
There is no schema rollback: dropping `hmac_key_version` would discard the only record of which key
signed each event, so forward recovery is the supported direction.

---

## 8. Deleting an old key

Do not. Not when the re-encryption finishes, and not "to tidy up".

A completed rotation proves only that the **live** store no longer needs the old key. Backups,
replicas, snapshots, exports and any copy taken before the rotation still need it. Delete a version
only after every retained copy has expired or been re-keyed, and only with the explicit
acknowledgement:

```bash
python -m dz_erp.rotate_keys vault-remove --vault "$ERP_VAULT_PATH" --version 1 \
    --i-have-verified-retained-copies
```

Removal is refused for the active version and for any version that live data still references.

---

## 9. Verification checklist after any rotation

```bash
python -m dz_erp.rotate_keys status --db "$ERP_DB_PATH" --vault "$ERP_VAULT_PATH"
```

* `vault.verification.failed == 0`
* `vault.blobs_by_version` shows the new active version for every blob you expected to migrate
* `audit.chains.valid == true` for every company
* `audit.events_by_version` reflects the mix you expected after rotation
* the security log and rotation journals contain no key material (they never should)

Then run the application smoke checks and re-create a backup; the new backup's version summary is
the one to keep in the runbook.
