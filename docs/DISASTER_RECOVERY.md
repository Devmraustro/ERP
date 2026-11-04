# Disaster Recovery Guide

## Overview

ERP Algeria supports secure, tested disaster recovery with local and optional
off-host backup storage. All backups are Fernet-encrypted and integrity-verified.

## Backup Architecture

- **Database:** SQLite online backup (consistent snapshot, no downtime).
- **Vault:** Encrypted document files copied with SHA-256 hashes.
- **Manifest:** JSON with DB hash, vault hashes, key ring metadata.
- **Encryption:** Fernet-encrypted tar.gz archive.
- **Key material:** Vault and audit key rings embedded in encrypted archive.

## Local Backup Configuration

Backups are stored locally by default:

```python
from dz_erp.security_audit import create_encrypted_backup
from dz_erp.backup_storage import LocalStorageProvider

meta = create_encrypted_backup(
    db=db,
    vault=vault,
    output_backup_path=Path("/backups/erp.enc"),
    backup_encryption_key=backup_key,
)

storage = LocalStorageProvider(Path("/backups"))
result = storage.upload(Path(meta["backup_path"]), "erp.enc")
assert result.verified
```

## Backup Encryption Key

Set `ERP_BACKUP_ENCRYPTION_KEY` to a dedicated Fernet key, separate from
the vault encryption key:

```bash
# Generate a key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Set environment variable
export ERP_BACKUP_ENCRYPTION_KEY="<generated-key>"
```

The key is never embedded in the backup archive. It must be stored separately
(e.g., on a different host, in a KMS, or in a secure vault).

## Remote Storage (Optional)

S3-compatible providers (MinIO, Cloudflare R2, Backblaze B2) can be used
for off-host storage. Requires `boto3` (optional dependency).

```python
# Example: MinIO (self-hosted)
from dz_erp.backup_storage_s3 import S3CompatibleProvider
provider = S3CompatibleProvider(
    endpoint_url="http://minio:9000",
    bucket="erp-backups",
    access_key=os.environ["MINIO_ACCESS_KEY"],
    secret_key=os.environ["MINIO_SECRET_KEY"],
)
```

## Key Separation

- `ERP_VAULT_ENCRYPTION_KEY` — vault document encryption
- `ERP_AUDIT_HMAC_SECRET` — audit chain HMAC
- `ERP_BACKUP_ENCRYPTION_KEY` — backup archive encryption (separate)

All three must be different in production.

## Key Rotation

Key rotation is supported via `dz_erp.rotate_keys` and `dz_erp.key_rotation`.
Rotated keys are tracked in a `KeyRing` with version numbers. Backups include
all key versions needed to restore.

## Legacy Backup Restoration

Old backups (created before key versioning) are supported via `KeyRing.bootstrap()`.
The legacy single-key field in the manifest is used as version 1.

## Restore Procedure

```python
from dz_erp.security_audit import restore_and_verify_backup

report = restore_and_verify_backup(
    backup_path=Path("/backups/erp.enc"),
    backup_encryption_key=backup_key,
    restore_target_dir=Path("/restore/target"),
)
assert report["db_sha256_verified"]
assert report["audit_chains_valid"]
```

Restore verifies:
1. Archive decryption
2. Tar structure and path safety
3. Database SHA-256
4. Vault file hashes
5. Key ring integrity
6. SQLite integrity_check
7. Foreign key check
8. Trial balance
9. Audit chains

## Interrupted Restore Recovery

If a restore is interrupted:
1. The staging directory may remain. Clean it manually.
2. The working database is never overwritten until all validations pass.
3. Re-run the restore from the beginning.

## Retention Policy

```python
from dz_erp.backup_storage import apply_retention

result = apply_retention(
    provider=storage,
    max_count=7,
    max_age_days=30.0,
    min_verified=1,
    dry_run=True,  # Preview first
)
print(result)
```

Safety: Never deletes the only verified recoverable backup.

## RPO/RTO Configuration

```bash
export ERP_RPO_TARGET_SECONDS=86400   # 24 hours
export ERP_RTO_TARGET_SECONDS=3600    # 1 hour
```

## Running DR Drills

```bash
python tools/dr_drill.py --output-dir /tmp/dr_report
```

The drill creates synthetic data, backs it up, simulates host loss, restores,
and verifies integrity. It reports measured backup/restore durations and
RPO/RTO compliance.

**Limitation:** Same-host simulation only. Not proof of independent off-host recovery.

## Total-Host-Loss Recovery

1. Retrieve backup from off-host storage.
2. Retrieve backup encryption key from separate custody (KMS, separate host, etc.).
3. Run restore_and_verify_backup with the key.
4. Verify all integrity checks pass.
5. Update DNS/configuration to point to restored instance.

## Security Limitations

- SHA-256 only (no CRC). Fernet provides authenticated encryption.
- Key material travels inside the encrypted archive (same protection as data).
- Same-host simulation is not proof of off-host recovery.
- No automated backup scheduling (operator-dependent).
- No alerting beyond structured logging (webhook optional).

## Free/Self-Hosted Deployment

All components support free/self-hosted options:
- Storage: MinIO, local NAS, rsync over SSH
- Key custody: file on separate host, environment variable
- Alerting: structured log, webhook to free service (ntfy, Gotify)
- No mandatory paid services
