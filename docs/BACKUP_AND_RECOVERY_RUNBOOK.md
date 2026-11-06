# Backup and recovery runbook

**Purpose:** produce a backup you can prove is restorable, and recover from the
failures that actually happen (lost host, corrupted database, lost key, bad
migration, operator error).

**The one rule:** *an unverified backup is not a backup.* Every step below ends in
a verification, and a backup that fails verification is treated as an incident,
not as a retry.

---

## 1. What must be backed up together

| Item | Path (default) | Why it must travel with the database |
|---|---|---|
| SQLite database | `$ERP_DB_PATH` (`state/erp.sqlite3`) | the record itself |
| Document vault | `$ERP_VAULT_PATH` (`state/vault/`) | encrypted source documents referenced by `vault_path` |
| Vault key file | beside the vault | without it the vault contents are unreadable |
| Audit HMAC key | key registry beside the database | without it the audit chain cannot verify |
| Key-rotation ledger | beside the vault | proves which key version signed what |

`create_encrypted_backup(db, vault, path, key)` captures a consistent SQLite
online snapshot plus the vault, encrypts the archive (Fernet) and writes a SHA-256
manifest. A backup that contains the database but not the key material is a
backup of ciphertext.

## 2. Taking a backup

```bash
python - <<'PY'
import os
from pathlib import Path
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import create_encrypted_backup

db = ERPDatabase(os.environ["ERP_DB_PATH"])
vault = PrivateDocumentVault(Path(os.environ["ERP_VAULT_PATH"]))
key = os.environ["ERP_BACKUP_KEY"].encode()          # from the secret store
report = create_encrypted_backup(db, vault, Path("backup/erp-backup.tar.fernet"), key)
print(report)   # includes the manifest digest
PY
```

Never store `ERP_BACKUP_KEY` next to the archive: a backup whose key travels with
it protects against disk failure, not against theft.

## 3. Verifying a backup (every time, in a separate process)

```bash
python - <<'PY'
import os
from pathlib import Path
from dz_erp.security_audit import restore_and_verify_backup

report = restore_and_verify_backup(
    Path("backup/erp-backup.tar.fernet"),
    os.environ["ERP_BACKUP_KEY"].encode(),
    Path("/srv/restore-verify"),      # an isolated directory, not the live one
)
print(report["verified"], report["audit_chain"], report["row_counts"])
PY
```

The restore verifies the manifest digest, the audit chain, the file count and the
schema. It runs in a **separate OS process** from the writer, with no secrets from
the running service — a restore that verified itself in memory would prove
nothing, and that is exactly the defect this check was built to catch.

**Interpreter requirement.** Safe archive extraction requires CPython **3.11.17+**
(or 3.12.10 ≤ x < 3.13). Below that the restore **refuses** rather than
extracting unsafely. That is why the container image pins 3.11.17: a deployment on
an older patch cannot verify its backups.

## 4. Recovery procedures

### 4.1 Lost host
1. Provision a host, install the pinned interpreter and the locked dependencies.
2. Restore the newest verified backup into a fresh directory (§3).
3. Point `ERP_DB_PATH` / `ERP_VAULT_PATH` at the restored paths.
4. Start the service; confirm `/healthz`, one login, one invoice read, and
   `verify_audit_chain` on the restored company.
5. Record the recovery in the incident log with the backup's manifest digest.

### 4.2 Corrupted database
The service will fail to open the database; it does not "repair" silently.
1. Preserve the corrupt file (evidence, not garbage).
2. Restore the newest verified backup that predates the corruption.
3. Reconcile the gap: every posted invoice after that backup must exist in the
   supplier paperwork. Re-key the missing documents through the normal intake and
   review path — **never** with direct SQL.

### 4.3 Lost or rotated key material
Follow `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`. If the audit HMAC key for a
version is lost, the chain for that version cannot be verified: record the version,
the date and the affected range in the incident log, and **do not** rewrite the
events to make verification pass.

### 4.4 Failed or interrupted migration
See `docs/MIGRATION_AND_DATA_SAFETY.md`: the next start repairs a half-applied
upgrade. If it does not, restore the pre-migration backup. There is no
down-migration by design.

### 4.5 Accidental deletion of a document
The vault is the copy of record; the encrypted backup is the second. Restore into
a scratch directory and copy the single file back — do not restore the whole
database over live state to recover one file.

## 5. Schedule and retention (operator decision, recorded here as a template)

| Backup | Frequency | Retention | Verified |
|---|---|---|---|
| Full (db + vault + keys) | daily | 30 days | daily, automated |
| Pre-migration | before every upgrade | until the next successful business day | yes, before starting |
| Monthly archive | monthly | per legal retention requirement | quarterly restore drill |

Retention of financial records follows Algerian commercial-law obligations, which
this repository does **not** encode. The engagement's accountant must state the
period; until then the template above is a placeholder, not a policy.

## 6. Drills (what "tested" means)

| Drill | Frequency | Pass condition |
|---|---|---|
| Restore a backup into a scratch directory | weekly, automated | manifest verifies, audit chain verifies, row counts match |
| Full recovery on a clean host | quarterly | service serves a login and an invoice read within the RTO |
| Key rotation + checkpoint export | quarterly | checkpoint reaches off-host storage and verification passes |
| Interpreter/dependency reinstall from the lockfile | quarterly | `--require-hashes` install succeeds and the suite passes |

No drill has been performed against production-sized data in this repository's
environment. **RTO/RPO are not established** and must not be quoted until a drill
produces them; the release readiness matrix keeps that row blocked.
