# Phase 68 — Operator and Installation Guide

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Applies to | Commit `89a5fecede594e5e0f001fe2decad6379325964f` |
| Status of this guide | Every command below was **run in this session on Linux with CPython 3.12.10**, or its syntax was checked against the tool's own `--help`. Commands marked **(Windows — not run)** were not executed; see Phase 64. |
| Precedence | Where this guide and an older document disagree, this guide reflects what the software did on 2026-11-14. The differences are listed in §20. |

This is one process on one machine with one SQLite file. It is not a clustered or cloud-native
system, and it is **not approved for production** (G5 and G6 are blocked — see the readiness report).

## 1. What you are installing

| Part | What it is |
|---|---|
| API server | `python -m dz_erp.serve` — one long-lived process |
| Database | One SQLite file, `ERP_DB_PATH` |
| Document vault | A directory of encrypted files, `ERP_VAULT_PATH` |
| Keys | Files beside the database and the vault (`*.audit.key`, `*.audit.keys.json`, `*.vault.key`, `*.vault.keys.json`), plus one backup key you create |
| Desktop client | Optional Qt application, `dz-erp` |

Scope of version 1: suppliers, items, supplier invoices with approval and posting, credit notes, trial
balance, G50 preparation workbook, users, backup and restore. **Not included:** customer invoicing,
payments, bank, period closing, customs, and any screen or API for purchase orders and goods receipts.

## 2. Requirements

* Linux with a local (not network) file system, or Windows 10/11 for the desktop client.
* **CPython 3.12.10 or later 3.12.x**, or 3.11.17 or later 3.11.x. Not 3.13. Older patch releases start
  the server but **refuse to restore a backup** — do not use them.
* Not supported: serverless platforms (for example Vercel), containers without a persistent volume,
  NFS/SMB for the database, more than one server process on the same database.

## 3. Install

```sh
python3.12 -m venv /srv/dz-erp/.venv
/srv/dz-erp/.venv/bin/python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
/srv/dz-erp/.venv/bin/python -m pip install --no-deps -e .
```

The first two lines were run in this session. The editable install on the third line was not — the
checks here ran from the source tree — so treat it as unverified until Phase 64 is executed.

`--no-deps` is required: with it every package must match a pinned hash; without it pip tries to
resolve one unpinned optional requirement and refuses. `pip check` afterwards reports that
`rapidocr-onnxruntime` wants `opencv-python`; the headless build is installed instead on purpose.

Verify the lock files have not been altered:

```sh
python tools/generate_hash_lock.py --check          # expect 53 distributions, 1317 hashes
python tools/verify_dependency_graph.py
```

## 4. Configuration

All settings are environment variables; the full table is `docs/ENVIRONMENT_VARIABLES.md`. The minimum
for a server:

```sh
export ERP_DB_PATH=/var/lib/dz-erp/erp.sqlite3
export ERP_VAULT_PATH=/var/lib/dz-erp/vault
export ERP_BACKUP_DIR=/var/backups/dz-erp
export ERP_BACKUP_KEY_FILE=/etc/dz-erp/backup.key      # on a different volume from the backups
export ERP_SECURITY_LOG_PATH=/var/log/dz-erp/security.jsonl
export ERP_PASSWORD_MIN_LENGTH=12
```

Run everything as one dedicated unprivileged account with `umask 077`.

Create the backup key once and keep a copy somewhere that is not this machine. **Losing it makes every
backup unreadable; there is no recovery.**

```sh
( umask 077; python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /etc/dz-erp/backup.key )
```

## 5. First start — create the company, once

```sh
python -m dz_erp.serve --bootstrap-admin admin --company-name "Your Company"
```

It prompts for a password (at least `ERP_PASSWORD_MIN_LENGTH` characters), creates the database, loads
the chart of accounts and exits.

**Run this exactly once.** Running it again with a *different* username does not fail — it creates a
second, separate company in the same database (finding S63-2).

Check afterwards: `ls -l` must show the database and every key file as `-rw-------`.

## 6. Start and stop

```sh
python -m dz_erp.serve --host 127.0.0.1 --port 8000
```

* The server refuses to start without `ERP_DB_PATH`, without any user, or when bound to `0.0.0.0`
  without `ERP_ALLOWED_HOSTS`.
* Stop with SIGTERM (Ctrl-C). It shuts down cleanly.
* The repository ships systemd units for the scheduled jobs in `ops/` but **no unit for the server
  itself**; write one with `User=dz-erp`, `UMask=0077` and an `EnvironmentFile`.

## 7. Network exposure

Keep the server on `127.0.0.1` and put a TLS-terminating reverse proxy in front. The server does not
do TLS. Set `ERP_ALLOWED_HOSTS` to your host name (requests with any other `Host` get 400) and
`ERP_TRUSTED_PROXIES` to the proxy's address, otherwise forwarded client addresses are ignored.
Leave `ERP_DOCS_ENABLED` unset.

## 8. Health

| Check | Command | Meaning |
|---|---|---|
| Alive | `curl http://127.0.0.1:8000/healthz` | `{"status":"ok"}` |
| Ready | `curl http://127.0.0.1:8000/readyz` | `ready`, or `degraded` with the failing check named |
| From cron | `python -m dz_erp.ops_checks readiness` (also `disk`, `backup-age`, `ocr`) | exit code for monitoring |

A new installation is `degraded` with `backup: fail` until the first backup. That is correct.

## 9. Users and roles

Users are created by an administrator through the API or the desktop client. Roles: `clerk` (enter),
`approver` (approve), `accountant` (post, reverse), `warehouse`, `auditor` (read only), `admin`.

* The person who created an invoice or requested a reversal cannot approve it. You need at least two
  people for any posting.
* Deactivating a user ends their open sessions immediately. An administrator cannot deactivate
  themselves.
* Sessions last 8 hours by default (`ERP_TOKEN_TTL_SECONDS`).

## 10. Daily work — what operators must know

* **Read the state, not the HTTP status.** A rejected duplicate or an invoice that fails validation is
  answered with HTTP 200 and `workflow_state` `DuplicateRejected` or `NeedsReview`.
* Posting needs an `Idempotency-Key` header. Repeating a request with the same key is safe and returns
  the same entry.
* Posted entries cannot be edited or deleted, by anyone, including with direct database access. The
  only correction is a credit note: reverse → submit → another person approves → post.
* One invoice can have one credit note. If that credit note is rejected, the invoice cannot be
  reversed again (finding W67-4) — decide carefully before rejecting.
* The G50 output is a preparation workbook for your accountant. It is not a tax filing.

## 11. Backup

```sh
python -m dz_erp.ops_backup run              # one encrypted backup + manifest, then retention
python -m dz_erp.ops_backup verify           # check every artefact
python -m dz_erp.ops_backup verify --deep    # also restore each one to a scratch directory
python -m dz_erp.ops_backup status           # JSON summary for monitoring
```

Schedule them with the timers in `ops/` (`dz-erp-backup.timer` daily 02:15 UTC,
`dz-erp-backup-verify.timer` daily 06:00 UTC), after editing the paths.

* Retention keeps the newest artefact, one per day for 7 days and one per week for 4 weeks. **Several
  backups taken on the same day are thinned.** If you take a backup before a risky change, copy it
  elsewhere before the next run.
* Copy backups off the machine. A backup on the same disk as the database is not a backup.
* The backup contains the audit and vault keys, encrypted with the backup key. Protect the backup key
  accordingly, and never store it next to the backups.

## 12. Restore

Always restore into a **new, empty directory**, never over the live files. There is no command-line
restore; use:

```sh
python - <<'PY'
from pathlib import Path
from dz_erp.security_audit import restore_and_verify_backup
report = restore_and_verify_backup(
    backup_path=Path("/var/backups/dz-erp/erp-YYYYMMDDTHHMMSSZ.erpbackup"),
    backup_encryption_key=Path("/etc/dz-erp/backup.key").read_bytes().strip(),
    restore_target_dir=Path("/srv/restore-drill"),
)
print({k: v for k, v in report.items() if not k.startswith("restored_")})
PY
```

A good restore prints `db_sha256_verified: True`, `audit_chains_valid: True` and equal trial-balance
totals. A wrong key or a damaged file is refused and nothing is written.

To bring the restored copy into service:

1. Stop the server.
2. The restored database is named **`erp_snapshot.sqlite3`** and its key files are named after it.
   Set `ERP_DB_PATH` to that file and `ERP_VAULT_PATH` to the restored `vault` directory. **This is the
   path that was tested.** If you prefer the old file name, the database, `erp_snapshot.sqlite3.audit.key`
   and `erp_snapshot.sqlite3.audit.keys.json` must be renamed together so they keep a common prefix;
   that variant was **not rehearsed**. Renaming only the database loses the audit keys.
3. Start the server; check `/readyz`; log in; compare the trial balance with your last known figures.
4. Take a new backup immediately.

Anything entered after the backup you restored is gone. Nobody has measured how long a restore takes
on real data, and no recovery-time or recovery-point target has been approved.

## 13. Key rotation

```sh
python -m dz_erp.rotate_keys status  --db "$ERP_DB_PATH" --vault "$ERP_VAULT_PATH"
python -m dz_erp.rotate_keys vault-rotate --vault "$ERP_VAULT_PATH"     # re-encrypts every document
python -m dz_erp.rotate_keys audit-rotate --db "$ERP_DB_PATH"           # new signing key; history kept
python -m dz_erp.rotate_keys monitor --db "$ERP_DB_PATH" --vault "$ERP_VAULT_PATH"
```

* Both rotations support `--dry-run`.
* **Restart the server after `audit-rotate`.** A running server keeps signing with the previous key
  until it is restarted (finding S63-3).
* Take a backup after a rotation.
* Do not remove an old key version while any retained backup still needs it. `vault-remove` and
  `audit-remove` exist and are irreversible.
* If a rotation is interrupted, run the same command again. Do not edit the key files by hand.

## 14. Logs

| Log | Where | Contains |
|---|---|---|
| Server | standard output/error of the process | requests, errors |
| Security | `ERP_SECURITY_LOG_PATH` | every refusal, as JSON lines; no passwords or tokens |
| Audit | inside the database | every business action, hash-chained, append-only |

`ops/logrotate/dz-erp` is a template for rotation. Without `ERP_SECURITY_LOG_PATH`, refusals are kept
in memory only and are lost on restart.

## 15. Upgrading

1. Take a backup and verify it with `--deep`. Copy it elsewhere.
2. Stop the server.
3. Check out the new commit; repeat §3.
4. Start; check `/readyz`; log in; compare the trial balance.

No upgrade between two released versions has been rehearsed, because there has been no release.
Rolling back means stopping, checking out the previous commit and — if the database was changed by
the newer version — restoring the backup from step 1.

## 16. Desktop client (Windows — not run in this session)

```bat
py -3.12 -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
py -3.12 -m pip install --no-deps --require-hashes -r requirements-desktop.lock.hashes.txt
py -3.12 -m pip install --no-deps -e .
dz-erp                       :: single workstation, embedded server
dz-erp --mode server --server-url http://erp-intranet:8000
dz-erp --language ar
dz-erp --smoke
```

Data lives in `%LOCALAPPDATA%\AlgerianERP`. Uninstalling does not delete it. See
`docs/DESKTOP_APPLICATION.md`. None of this has been accepted on a clean Windows machine (Phase 64).
On Windows the owner-only file permissions described in §5 do not apply; protection depends on the
folder's NTFS permissions, which have not been tested.

## 17. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| Server exits at once, mentions `ERP_DB_PATH` or "no users" | not configured / not bootstrapped | §4, §5 |
| Every request answers 400 `Invalid Host header.` | host name not in `ERP_ALLOWED_HOSTS` | §7 |
| `/readyz` `degraded`, `backup: fail` | no recent backup | §11 |
| Login answers 429 | 10 failed logins in 5 minutes from that client for that account | wait; counters are per process and reset on restart |
| Restore fails: "Safe backup restore requires Python 3.11.17+ or Python 3.12.10+" | interpreter too old | §2 |
| Restore fails: `InvalidToken` | wrong backup key, or the file is damaged | try the key copy; try an older backup |
| Credit note stuck in `NeedsReview` | it has not been submitted for approval | submit, approve (another person), post |
| "already has a reversal" (409) | the invoice already has a credit note | find and complete that credit note |

## 18. What not to do

* Do not run two servers on one database, or put the database on a network share.
* Do not edit the database with SQL tools. Posted rows and audit rows will refuse; anything else you
  change is outside the audit trail.
* Do not delete key files, and do not restore a database without its key files.
* Do not expose port 8000 directly.
* Do not enter real data before the gates in the readiness report are closed.

## 19. Before go-live

Not a formality — none of these is done:

- [ ] Written answers from a licensed accountant to the compliance questions (G5)
- [ ] A real host, proxy, certificate, monitoring and off-host backups (G6)
- [ ] A restore rehearsed from the off-host copy on realistic data, with approved targets
- [ ] Windows installation and user acceptance signed (Phase 64)
- [ ] CI green on the release commit, with test totals read from the reports
- [ ] A named person for each open finding in the Phase 69 audit

## 20. Where this guide differs from older documents

| Older document | Says | Observed |
|---|---|---|
| `docs/admin/ADMINISTRATION_AND_RECOVERY.md` §6 | "move the restored files into place" | The restored database has a different name and its key files follow that name — §12 |
| same, §5 | describes rotation | does not say a restart is needed after `audit-rotate` — §13 |
| `docs/STAGING_SECURITY_CONFIGURATION.md` line 276, `docs/RISK_REGISTER.md` B2 | 51 pins / 1277 hashes | 53 / 1317 |
| `docs/DESKTOP_APPLICATION.md` §4 | `pip install -e ".[desktop]"` | Works, but does not enforce hashes; §16 uses the hash-locked files |
| `docs/ENVIRONMENT_VARIABLES.md` | `ERP_PASSWORD_MIN_LENGTH` default 8 | Correct; this guide sets 12 as recommended there |

The older documents were not edited, to leave earlier release evidence as it was.
