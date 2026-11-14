# Administration and Recovery

**Branch:** `arena/63d762c8-erp` · **Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`
**Audience:** the administrator of a real deployment.
**Every command below was executed on this branch unless it is marked NOT EXECUTED.**

> This system has **not** been approved by an Algerian `Expert-Comptable` (`G5` BLOCKED, `Q1`–`Q12`
> PENDING) and has **not** been deployed to production infrastructure (`G6` BLOCKED). Treat it as a
> staging-grade system until both are closed.

---

## 1. What you are administering

| Component | Where it lives | Notes |
|---|---|---|
| Database | `ERP_DB_PATH` (SQLite file) | required, **no default** |
| Document vault | `ERP_VAULT_PATH` | required, **no default**; holds encrypted originals |
| Vault key | beside the vault | created on first use, `0o600` |
| Backups | `ERP_BACKUP_DIR` | encrypted archives + sidecar manifests |
| Backup key | `ERP_BACKUP_KEY_FILE` | **must not** live beside the backups |
| API | `python -m dz_erp.serve` | loopback by default |

Both `ERP_DB_PATH` and `ERP_VAULT_PATH` are mandatory. Starting without them exits with an explicit
message rather than creating a database next to the source tree — this is deliberate.

## 2. First installation

```sh
# 1. dependencies, hash-verified
pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
python tools/generate_hash_lock.py --check          # must print "OK - 53 pinned distributions …"

# 2. desktop client (optional, separate machine or same machine)
pip install --no-deps --require-hashes -r requirements-desktop.lock.hashes.txt
python tools/generate_hash_lock.py --desktop --check

# 3. create the first company + admin + SCF chart
ERP_DB_PATH=/var/lib/erp/erp.sqlite3 ERP_VAULT_PATH=/var/lib/erp/vault \
  python -m dz_erp.serve --bootstrap-admin admin --company-name 'SARL Exemple'

# 4. verify the chart was loaded — MUST print 294
sqlite3 /var/lib/erp/erp.sqlite3 'SELECT COUNT(*) FROM accounts;'
```

**Step 4 is not optional.** A deployment created before this phase's fix has an **empty** chart and no
invoice can ever be posted. If the count is `0`, see §3.

```sh
# 5. run the API
ERP_DB_PATH=/var/lib/erp/erp.sqlite3 ERP_VAULT_PATH=/var/lib/erp/vault \
ERP_ALLOWED_HOSTS=erp.example.dz,192.168.1.10 \
ERP_PASSWORD_MIN_LENGTH=12 \
  python -m dz_erp.serve --host 0.0.0.0 --port 8000
```

`ERP_ALLOWED_HOSTS` is **required** when binding anything other than loopback. Without it the process
refuses to start (see `SECURITY_AND_RELIABILITY_ACCEPTANCE.md` §2). This is protection against the
"server healthy, every browser gets 400" failure.

## 3. Repairing a deployment bootstrapped before the chart fix

```sh
ERP_DB_PATH=/var/lib/erp/erp.sqlite3 python - <<'PY'
import os, sqlite3
from dz_erp.database import ERPDatabase
from dz_erp.localization import load_official_odoo_chart

db = ERPDatabase(os.environ["ERP_DB_PATH"])
with db.transaction() as conn:
    company_id = conn.execute("SELECT id FROM companies ORDER BY id LIMIT 1").fetchone()["id"]
    n = conn.execute("SELECT COUNT(*) n FROM accounts WHERE company_id=?", (company_id,)).fetchone()["n"]
print("company", company_id, "accounts before:", n)
if n == 0:
    load_official_odoo_chart(db, company_id, apply_fixes_immediately=True)
    print("chart loaded")
PY
sqlite3 /var/lib/erp/erp.sqlite3 'SELECT COUNT(*) FROM accounts;'   # expect 294
```

The load is audited (`LOAD_CHART_TEMPLATE`). It is idempotent with respect to an already-loaded chart;
running it twice is not a repair.

**Alternative:** bootstrap a new database and re-enter master data. Choose this if the existing data
is disposable — it is the cleaner path.

## 4. Daily operations

| Task | Command | Expected |
|---|---|---|
| Liveness | `curl -fsS http://127.0.0.1:8000/healthz` | `200` |
| Readiness | `curl -fsS http://127.0.0.1:8000/readyz` | `200`; `status: ok` once a fresh backup exists, `degraded` before that |
| Backup | `python -m dz_erp.ops_backup --backup-dir … run` | JSON with `sha256`, `size_bytes` |
| Verify | `… verify` | `{"ok": true, "verified": N, "failed": 0}` |
| Deep verify | `… verify --deep` | adds a real restore + trial-balance comparison |
| Retention | `… prune` | keeps the newest artefact and anything on legal hold |
| Monitoring | `… status` | JSON for your monitor |

`degraded` is the **correct** state for a fresh install: the application is serving but has no recent
backup. It is not an error; investigate only if it stays `degraded` after a successful backup.

**Scheduling.** `ops/` contains systemd units (backup, verify, disk check, checkpoint export). Point
them at your paths; they are templates, not an installer.

## 5. Backup keys and rotation

* `ERP_BACKUP_KEY_FILE` must be readable **only** by the service account (`chmod 600`). Keep it on a
  volume separate from the backups — a key stored beside the archive is one theft from plaintext.
* Rotation: the vault and audit keys are versioned. The manifest records the active versions
  (`vault_key_versions`, `audit_key_versions`) so a restore can tell you what it needs.
* **An interrupted rotation is recoverable** (`tests/test_phase13_key_rotation_regression.py`). If a
  rotation is interrupted, do **not** hand-edit the registry: re-run the rotation tooling, which
  completes or rolls back.
* A backup taken before a rotation remains restorable as long as the superseded key version is still
  available. **Never delete an old key version while a backup that needs it is retained.**

## 6. Restore

```sh
# 1. restore into an ISOLATED directory first — never over the live deployment
python - <<'PY'
import os
from dz_erp.security_audit import restore_and_verify_backup
report = restore_and_verify_backup(
    backup_path="/backups/erp-20261113T235818Z.erpbackup",
    backup_encryption_key=open("/etc/erp/backup.key","rb").read(),
    restore_target_dir="/srv/restore-drill",
)
print(report)
PY

# 2. compare the trial balance of the restored copy against the source
# 3. only then promote: stop the service, move the restored files into place, start, check /readyz
```

**Restore requires CPython 3.11.17+ or 3.12.10+.** On an older interpreter every attempt fails closed
with `FinancialIntegrityError` ("Safe backup restore requires Python 3.11.17+ …"). This is a security
guard, not a bug: `tarfile` link handling below those versions cannot be made safe.

**Do not quote an RPO or RTO** until a restore has actually been performed on the real host with real
data and timed. No such measurement exists.

### 6.1 The verify contract changed in this phase

An artefact **without** its `.manifest.json` now verifies as `ok: false` with
`MANIFEST_MISSING`. Previously it reported success. If your monitoring now turns red after a
directory migration, that is the fix working: those artefacts genuinely could not be verified.
Remedy: re-take the backup, or restore the manifest from a trusted copy.

## 7. Users and access

```sh
# create an operator (admin token required)
curl -sS -X POST http://127.0.0.1:8000/api/v1/users \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"username":"compta1","password":"…","role":"accountant"}'

# deactivate
curl -sS -X POST http://127.0.0.1:8000/api/v1/users/2/deactivate -H "Authorization: Bearer $TOKEN"
```

Roles: `admin`, `accountant`, `approver`, `viewer`.

**Two rules that will surprise you:**

1. **Segregation of duties.** The user who created an invoice cannot approve it
   (`SEGREGATION_OF_DUTIES_VIOLATION`). Make sure at least two people hold approving roles, otherwise
   invoices stop at `PendingApproval`.
2. **Account lockout.** Five wrong passwords lock an account for 15 minutes — including the correct
   password. Anyone who can reach `/api/v1/auth/token` can lock the only admin out. **Always keep a
   second administrative account** and watch for `AUTH_ACCOUNT_LOCKED` in the audit log.

## 8. Security configuration checklist

- [ ] `ERP_ALLOWED_HOSTS` names every hostname/IP users type, and nothing else
- [ ] API bound to loopback behind a TLS-terminating proxy (never exposed directly)
- [ ] `ERP_DOCS_ENABLED` unset (default `false`); confirm `/docs` returns `404`
- [ ] `ERP_PASSWORD_MIN_LENGTH=12`
- [ ] `ERP_BACKUP_KEY_FILE` on a separate volume, mode `600`
- [ ] database, vault and key readable only by the service account
- [ ] `ERP_KEY_PROVIDER` left at the default unless a real KMS/HSM client exists — declaring `kms`
      **stops start-up** by design
- [ ] backups shipped off-host, encrypted, and one restore from the off-host copy tested
- [ ] audit log shipped somewhere the service account cannot rewrite

## 9. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| Start-up exits: `ERP_VAULT_PATH is not set` | required variable missing | set it; do not "temporarily" point it at `/tmp` |
| Start-up exits: `Refusing to listen on '0.0.0.0'` | no `ERP_ALLOWED_HOSTS` | set it (§2) |
| Start-up exits: `ERP_TOKEN_TTL_SECONDS='abc' is not an integer` | bad value | use a positive integer |
| Start-up exits: `ERP_KEY_PROVIDER='kms' cannot be honoured` | no KMS client bundled | leave the provider unset |
| Every browser gets `400` | `Host` not in `ERP_ALLOWED_HOSTS` | add it; look for `SECURITY_HOST_HEADER_REJECTED` |
| `stock_account_code '300' is not an account…` | `300` is not an SCF code in this chart | use `30`; check **Plan comptable** |
| `Cannot post invoice N in state 'NeedsReview'` | a validation control failed | open **Réviser…**, fix every banner line |
| `SEGREGATION_OF_DUTIES_VIOLATION` | approving your own invoice | another approver must decide |
| Admin login `401` despite correct password | account locked (5 failures) | wait 15 min or use the second admin account |
| `/readyz` `degraded` | no recent backup | run `ops_backup run`, then check again |
| `verify` reports `MANIFEST_MISSING` | artefact copied without its sidecar | re-take the backup |
| `verify --deep` fails on an old Python | 3.11.17+/3.12.10+ required | upgrade the interpreter |

## 10. What is deliberately not automated

* No auto-posting of OCR documents — every posting is an approved, human-decided document.
* No silent recovery from a corrupted database: `integrity_check` and `foreign_key_violations` are
  reported and the operator decides.
* No automatic key deletion during retention.
* No "fix" that edits posted journal entries. Corrections are reversals.
