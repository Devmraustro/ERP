# Backup & Recovery Runbook

Audience: the operator on call. Every command below was executed at least once during
the Phase 10 drill (`docs/release/phase10/evidence/operational_drill.py`, 16/16 PASS).

## 0. The five rules

1. **The backup key is never stored with the backup.** Key at `/etc/dz-erp/backup.key`
   (0600, root or `dz-erp`), archives on different storage entirely.
2. **An unverified backup is not a backup.** `verify` daily, `verify --deep` weekly.
3. **Restore into a new directory.** Never restore over a live deployment.
4. **Never delete the newest artefact, and never delete a held one.** The tool enforces
   this; do not work around it.
5. **A failed backup is an incident, not a warning.** Exit code 1 + `BACKUP_FAILED` in
   the journal means: fix it today.

## 1. Install (once)

```bash
sudo install -d -m 0700 -o dz-erp -g dz-erp /etc/dz-erp
sudo -u dz-erp python -c "from cryptography.fernet import Fernet; import pathlib; \
    pathlib.Path('/etc/dz-erp/backup.key').write_bytes(Fernet.generate_key())"
sudo chmod 600 /etc/dz-erp/backup.key
sudo install -m 0644 ops/dz-erp-backup.service ops/dz-erp-backup.timer \
    ops/dz-erp-backup-verify.service ops/dz-erp-backup-verify.timer \
    ops/dz-erp-readiness.service ops/dz-erp-readiness.timer \
    ops/dz-erp-disk-check.service ops/dz-erp-disk-check.timer \
    ops/dz-erp-ocr-check.service ops/dz-erp-ocr-check.timer /etc/systemd/system/
sudo install -m 0644 ops/logrotate/dz-erp /etc/logrotate.d/dz-erp
sudo systemctl daemon-reload
sudo systemctl enable --now dz-erp-backup.timer dz-erp-backup-verify.timer \
    dz-erp-readiness.timer dz-erp-disk-check.timer dz-erp-ocr-check.timer
```

**Copy the key into the company's secret store now.** Losing it makes every archive
unreadable, and no support path exists.

## 2. Daily (automated)

| Time (UTC) | Job | Healthy signal |
| --- | --- | --- |
| 02:15 | `ops_backup run` | one new `erp-*.erpbackup` + `.manifest.json`, exit 0 |
| 06:00 | `ops_backup verify` (deep on Sunday) | `verify-status.json` → `"ok": true` |
| 03:40 | `ops_checks ocr` | exit 0 |
| every 5 min | `ops_checks readiness` | exit 0 |
| every 15 min | `ops_checks disk` | exit 0, `free_bytes` above `ERP_MIN_FREE_BYTES` |

## 3. Manual checks

```bash
# freshness, count, size, last verification
python -m dz_erp.ops_backup --backup-dir /mnt/backup-offsite/dz-erp status

# what would retention delete? (deletes nothing)
python -m dz_erp.ops_backup --backup-dir /mnt/backup-offsite/dz-erp prune --dry-run

# verify hashes only
python -m dz_erp.ops_backup --backup-dir /mnt/backup-offsite/dz-erp verify

# verify by restoring (needs the key)
python -m dz_erp.ops_backup --backup-dir /mnt/backup-offsite/dz-erp verify --deep

# readiness from a shell, with the API's own logic
python -m dz_erp.ops_checks readiness
curl -s -o /dev/null -w '%{http_code}\n' https://erp.example.dz/readyz
```

Exit codes everywhere: `0` healthy, `1` check failed, `2` misconfigured (treat as
failed, not healthy).

## 4. Take an ad-hoc backup before a risky change

```bash
sudo -u dz-erp env ERP_DB_PATH=/srv/dz-erp/data/erp.sqlite3 \
  ERP_VAULT_PATH=/srv/dz-erp/vault ERP_BACKUP_KEY_FILE=/etc/dz-erp/backup.key \
  python -m dz_erp.ops_backup --backup-dir /mnt/backup-offsite/dz-erp run
```

Then copy the newest artefact to a second location and record its SHA-256 in the change
ticket.

## 5. Restore (disaster)

```bash
# 1. Stop writers.
sudo systemctl stop dz-erp-backup.timer dz-erp-backup-verify.timer
sudo systemctl stop dz-erp

# 2. Restore into a NEW directory (never over the live one).
python - <<'PY'
from pathlib import Path
from dz_erp.security_audit import restore_and_verify_backup
report = restore_and_verify_backup(
    backup_path=Path("/mnt/backup-offsite/dz-erp/erp-20261110T021500Z.erpbackup"),
    backup_encryption_key=Path("/etc/dz-erp/backup.key").read_bytes().strip(),
    restore_target_dir=Path("/srv/dz-erp-restore-20261110"),
)
print(report["db_sha256_verified"], report["integrity_check"], report["foreign_key_check"],
      report["trial_balance_debit_dzd"], report["trial_balance_credit_dzd"],
      report["audit_chains_valid"])
PY

# 3. Check the four facts before switching: db hash verified, integrity ok,
#    foreign keys clean, trial balance debit == credit, audit chain valid.

# 4. Switch: point ERP_DB_PATH/ERP_VAULT_PATH at the restored directory, start,
#    then confirm readiness and one authenticated call.
sudo systemctl start dz-erp
curl -fsS https://erp.example.dz/readyz | python -m json.tool
curl -fsS https://erp.example.dz/healthz
```

If any of the five facts fails: **stop**. Do not run the business on an unverified
restore. Try the next older artefact and escalate.

## 6. Restore a single record (audit request, wrong entry)

1. Restore a copy into `/srv/dz-erp-inspect-<date>` as in §5.
2. Query the restored database read-only; export the rows.
3. Re-enter any correction through the normal workflow (reversal, not edit) so the
   live audit trail stays complete.

## 7. Key rotation interaction

Rotating the vault or audit key changes what a restore needs. The archive records the
key **versions** it requires (`vault_key_versions`, `audit_key_versions` in its
manifest) and carries the registries encrypted inside the envelope. **Keep every key
version as long as any archive that references it exists** — pruning keys before
archives is the one unrecoverable mistake.

## 8. Alert response table

| Alert | First action |
| --- | --- |
| `BACKUP_FAILED` | check disk space, key file perms, `ERP_BACKUP_DIR` writability; run §4 manually |
| `BACKUP_STALE` | the timer did not run or every run failed; check `systemctl list-timers`, journal |
| `BACKUP_INTEGRITY_FAILED` | storage corruption or truncation; re-run `verify`, isolate the artefact, take a fresh backup to different storage |
| `RETENTION_FAILED` | permissions on the backup directory; the fresh backup is safe, pruning is not |
| `DISK_LOW` | free space or move archives off-host; SQLite and the vault fail at the same moment |
| `READINESS_FAILED` | read the JSON: database → volume/permissions; vault → key/perms; disk → free space |
| `OCR_UNAVAILABLE` | intake silently degrades to manual entry; check the model files and `onnxruntime` |

## 9. Monthly / quarterly proof

- **Monthly:** restore the newest archive on a spare host and run one authenticated
  query (this is the only way to prove the backup is restorable on *other* hardware).
- **Quarterly:** full DR drill — restore, compare trial balance and audit chain with
  the live system, record the measured RTO, and update
  `docs/release/phase9/DISASTER_RECOVERY_ACCEPTANCE.md` with the new numbers.
