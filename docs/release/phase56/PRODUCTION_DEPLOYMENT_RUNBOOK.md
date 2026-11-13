# Phase 56 — Production Infrastructure Assessment & Deployment Runbook (Gate G6)

**Repository:** `/home/user/ERP` (`https://github.com/Devmraustro/ERP.git`)  
**Branch & Baseline:** `arena/63d762c8-erp`  
**Prepared Date:** `2026-11-13` (UTC)  
**Release Gate:** **G6 — Verified Production Infrastructure & Operational Custody**  
**Current Gate Status:** **`BLOCKED` (Application Controls Ready; Target Production Infrastructure Not Provisioned)**

---

## 1. Honest Infrastructure Inspection Summary

All in-repository operational artifacts (`ops/*.service`, `ops/*.timer`, `ops/logrotate/dz-erp`, `dz_erp/serve.py`, `dz_erp/net.py`, `dz_erp/readiness.py`, `dz_erp/ops_backup.py`, `dz_erp/ops_checks.py`, `dz_erp/ops_alerts.py`, `dz_erp/rotate_keys.py`, `.github/workflows/ci.yml`) and connected platform capabilities were inspected.

- **No live production server, Algerian sovereign/on-premise Linux host, domain name, public TLS certificate, offsite backup mount, KMS endpoint, or alerting webhook has been provisioned or supplied.**
- **Connected Serverless App (`vercel`) Evaluation:** Although a Vercel connector is enabled in the workspace session, **Vercel is architecturally incompatible with this ERP and was intentionally NOT called for deployment**:
  1. The ERP requires a persistent POSIX filesystem for its SQLite WAL ledger (`ERP_DB_PATH`), `0600` owner-only encrypted `PrivateDocumentVault` (`ERP_VAULT_PATH`), keyrings (`*.keys.json`), and `systemd` backup/rotation/checkpoint timers (`ops/*`), plus Algerian Law `18-07` data-residency governance. Ephemeral serverless functions lose local SQLite/vault state and violate `0600` key-custody checks.
  2. Release governance strictly prohibits any production deployment while **Gate G5** (`Expert-Comptable` statutory approval) or **Gate G6** (production infrastructure) is **`BLOCKED`**.
- **Zero fake production hostnames, certificates, or secrets have been created.**

---

## 2. 16-Area Production Readiness Matrix

Legend: **✅ Code/Ops Artifact Verified in Repo** | **❌ External Production Resource Missing (Blocks G6)**

| # | Infrastructure Area | In-Repository Implementation | External Production Status | Gate Verdict |
|---:|---|---|---|---|
| **1** | **`ERP_ALLOWED_HOSTS`** | ✅ Enforced in `dz_erp/net.py` & `dz_erp/api.py:_validate_host_header` (closed in Phase 53; defaults to loopback; rejects unlisted `Host` with `400` + `SECURITY_HOST_HEADER_REJECTED`; verified by `test_phase53` & `tls_proxy_harness.py`) | ❌ Production FQDN not yet designated | **Code ✅ / Infra ❌** |
| **2** | **Domain & DNS** | ✅ Documented in `docs/ENVIRONMENT_VARIABLES.md` | ❌ No production domain or DNS zone supplied | **BLOCKED** |
| **3** | **TLS Termination & Renewal** | ✅ Edge TLS + HSTS (`max-age=31536000; includeSubDomains`) + `301` HTTP→HTTPS verified via `docs/release/phase8/evidence/tls_proxy_harness.py` | ❌ No publicly trusted certificate or ACME/PKI renewal timer provisioned | **BLOCKED** |
| **4** | **Reverse Proxy Boundary** | ✅ `dz_erp/net.py:TrustedProxyPolicy` (`ERP_TRUSTED_PROXIES`), `dz_erp/serve.py` (`--host 127.0.0.1`, `uvicorn proxy_headers=False`) | ❌ No production Nginx/Caddy/HAProxy edge deployed | **Code ✅ / Infra ❌** |
| **5** | **Production Database** | ✅ `dz_erp/database.py` SQLite WAL + `FOREIGN KEYS=ON` + `BEGIN IMMEDIATE` write serialization + `7` immutability triggers (`ERP_DB_PATH`) | ❌ No dedicated host volume (`/srv/dz-erp/data/erp.sqlite3`) provisioned | **Code ✅ / Infra ❌** |
| **6** | **Schema Migrations** | ✅ Idempotent `_init_schema` + additive `_migrate_schema` (`dz_erp/database.py`), verified on legacy DB upgrades (`test_phase7`, `test_phase12`) | ⚠️ Must execute pre-traffic migration step (§3.3) on target host | **Code ✅ / Infra ❌** |
| **7** | **Secrets & Key Custody** | ✅ `dz_erp/key_provider.py` (`FileKeyProvider` `0600`, `EnvironmentKeyProvider`, `KmsKeyProvider`), `ERP_PASSWORD_MIN_LENGTH` (`12` recommended), zero hardcoded secrets | ❌ Production vault/audit/backup keys and off-host KMS/secret store not provisioned | **BLOCKED** |
| **8** | **Encrypted Backups** | ✅ `dz_erp/security_audit.py:create_encrypted_backup` (`0600` Fernet archive) + `dz_erp/ops_backup.py` + `ops/dz-erp-backup.service` & `.timer` | ❌ Off-host backup storage (`/mnt/backup-offsite/dz-erp`) & `ERP_BACKUP_KEY_FILE` not provisioned | **BLOCKED** |
| **9** | **Key Rotation & Checkpoint Ledger** | ✅ `dz_erp/rotate_keys.py` (`vault` & `audit` rotation, crash-safe resume, `RotationLedger`) + `ops/dz-erp-checkpoint-export.*` & `verify.*` | ❌ Separate-device checkpoint target not mounted | **Code ✅ / Infra ❌** |
| **10** | **Monitoring** | ✅ `ops/dz-erp-readiness.service`/`.timer`, `ops/dz-erp-disk-check.*`, `ops/dz-erp-ocr-check.*`, `ops/dz-erp-backup-verify.*`, `dz_erp/ops_checks.py` | ❌ Not installed on a live `systemd` production host | **Code ✅ / Infra ❌** |
| **11** | **Alerting** | ✅ `dz_erp/ops_alerts.py` structured JSON incident emitter (`ERP_ALERT_WEBHOOK`) | ❌ No production `ERP_ALERT_WEBHOOK` / on-call receiver configured | **BLOCKED** |
| **12** | **Security & Audit Logs** | ✅ `dz_erp/security_audit.py` (`JsonlSecurityLog`, `StructuredRedactingLogger`, `ERP_SECURITY_LOG_PATH`) + `ops/logrotate/dz-erp` | ❌ No production SIEM / durable log shipping target configured | **Code ✅ / Infra ❌** |
| **13** | **`/healthz` & `/readyz` Probes** | ✅ `GET /healthz` (liveness `200`) and `GET /readyz` / `GET /readyz?deep=1` (`dz_erp/readiness.py`: DB write-lock, vault custody, disk free bytes, OCR runtime, advisory backup staleness; `503` on critical failure) | ✅ Ready for reverse-proxy & systemd health checks once host exists | **READY (Code)** |
| **14** | **Deployment Procedure** | ✅ Documented step-by-step below (§3) using CPython `3.12.10` + `--require-hashes` | ❌ Waiting on G5 & G6 | **READY (Runbook)** |
| **15** | **Rollback & Restore Procedure** | ✅ Safe-restore runtime guard (`assert_safe_tar_extract_runtime`) + `restore_encrypted_backup` + `rotate_keys` rollback documented below (§4) | ❌ Quarterly restore rehearsal on production hardware pending | **READY (Runbook)** |
| **16** | **Retention Policy** | ✅ `ERP_BACKUP_RETAIN_DAILY` / `ERP_BACKUP_RETAIN_WEEKLY` in `dz_erp/ops_backup.py` + `docs/release/phase10/DATA_RETENTION_POLICY.md` | ❌ Statutory retention schedule awaiting G5 legal/accounting sign-off | **BLOCKED (G5/G6)** |

---

## 3. Target Production Deployment Runbook *(Execute ONLY After G5 = `PASS` and G6 Host Provisioned)*

### 3.1 Host & Runtime Prerequisites
- **Operating System:** Hardened Linux x86_64 host (Ubuntu 24.04 LTS / Debian 12) located within approved jurisdiction (Law `18-07`).
- **Python Runtime:** Patched Python `3.12.10+` (or `3.11.9+` with `hasattr(tarfile, "data_filter") == True` so safe backup restore is supported).
- **Dedicated System Account & Directories:**
  ```bash
  sudo useradd --system --home-dir /srv/dz-erp --shell /usr/sbin/nologin dz-erp
  sudo install -d -o dz-erp -g dz-erp -m 0750 /srv/dz-erp /srv/dz-erp/data /srv/dz-erp/vault /var/log/dz-erp
  sudo install -d -o root -g dz-erp -m 0750 /etc/dz-erp
  # Mount independent off-host volumes for backups and rotation checkpoints:
  #   /mnt/backup-offsite/dz-erp
  #   /mnt/checkpoint-offsite/dz-erp
  ```

### 3.2 Reproducible Hash-Locked Installation
```bash
sudo -u dz-erp python3.12 -m venv /srv/dz-erp/.venv
sudo -u dz-erp /srv/dz-erp/.venv/bin/python -m pip install --no-deps --require-hashes -r /srv/dz-erp/app/requirements.lock.hashes.txt
sudo -u dz-erp /srv/dz-erp/.venv/bin/python /srv/dz-erp/app/tools/verify_dependency_graph.py
```

### 3.3 Secret Custody, Required Environment Variables & Pre-Traffic Migration
Generate independent `0600` Fernet/HMAC key files **outside** `/srv/dz-erp/vault` (or configure `KmsKeyProvider`), and create `/etc/dz-erp/erp.env` (`0640`, `root:dz-erp`):
```ini
ERP_DB_PATH=/srv/dz-erp/data/erp.sqlite3
ERP_VAULT_PATH=/srv/dz-erp/vault
ERP_SECURITY_LOG_PATH=/var/log/dz-erp/security.jsonl
ERP_BACKUP_DIR=/mnt/backup-offsite/dz-erp
ERP_BACKUP_KEY_FILE=/etc/dz-erp/backup.key
ERP_ALLOWED_HOSTS=erp.<your-verified-domain.dz>
ERP_TRUSTED_PROXIES=127.0.0.1
ERP_PASSWORD_MIN_LENGTH=12
ERP_RATE_LIMIT_BACKEND=sqlite
ERP_RATE_LIMIT_FAILURE_MODE=closed
ERP_ALERT_WEBHOOK=https://<your-verified-alert-receiver>
```
- **First-Run Bootstrap / Pre-Traffic Migration:**
  - *Fresh install:* Run interactive admin bootstrap once (prompts for password via TTY; never pass passwords on CLI):
    ```bash
    sudo -u dz-erp env $(cat /etc/dz-erp/erp.env | xargs) /srv/dz-erp/.venv/bin/python -m dz_erp.serve --bootstrap-admin <admin_username> --company-name "<Legal Company Name>"
    ```
  - *Upgrading an existing database:* Take an encrypted backup first (`python -m dz_erp.ops_backup --backup-dir /mnt/backup-offsite/dz-erp run`), open the DB once via `python -m dz_erp.ops_checks readiness` to apply `_migrate_schema` before routing traffic, and verify `python tools/verify_audit_integrity.py`.

### 3.4 Application Binding, Reverse Proxy & Systemd Timers
1. **Application Process (Single-Worker SQLite Write Discipline, Loopback-Only):**
   - Run `/srv/dz-erp/.venv/bin/python -m dz_erp.serve --host 127.0.0.1 --port 8000` under `systemd` (`User=dz-erp`, `UMask=0077`, `EnvironmentFile=/etc/dz-erp/erp.env`). Never expose port `8000` on `0.0.0.0` in production, and keep `--workers 1` against a single SQLite WAL database.
2. **Reverse Proxy (Nginx / Caddy on `443`):**
   - Terminate TLS 1.2/1.3 with publicly trusted certificate + automated ACME renewal.
   - Emit `Strict-Transport-Security: max-age=31536000; includeSubDomains`, redirect HTTP `80` → HTTPS `443` (`301`), preserve incoming `Host` (or pin to `erp.<domain.dz>`), set `X-Forwarded-For $remote_addr`, cap `client_max_body_size 15m`, and proxy to `http://127.0.0.1:8000`.
3. **Operational Timers & Log Rotation:**
   - Install `ops/logrotate/dz-erp` → `/etc/logrotate.d/dz-erp`.
   - Install and enable `ops/dz-erp-backup.timer`, `ops/dz-erp-backup-verify.timer`, `ops/dz-erp-checkpoint-export.timer`, `ops/dz-erp-checkpoint-verify.timer`, `ops/dz-erp-disk-check.timer`, `ops/dz-erp-ocr-check.timer`, and `ops/dz-erp-readiness.timer`.

### 3.5 Post-Deploy Verification Checklist
1. `curl -fsS https://erp.<domain.dz>/healthz` → `200 {"status":"ok","service":"algerian-ai-erp"}`.
2. `curl -fsS https://erp.<domain.dz>/readyz?deep=1` → `200 {"status":"ready", ...}` (or `degraded` only prior to first scheduled backup; immediately trigger `dz-erp-backup.service` and re-check for `ready`).
3. `curl -sS -o /dev/null -w "%{http_code}\n" -H "Host: evil.invalid" https://127.0.0.1:443/healthz --insecure` → **`400`**.
4. `curl -sS -o /dev/null -w "%{http_code}\n" https://erp.<domain.dz>/docs` → **`404`** (OpenAPI UI disabled in production).

---

## 4. Rollback & Disaster-Recovery Restore Procedure

### 4.1 Application Code Rollback (Zero Schema Destructive Change)
Because all schema migrations in `dz_erp/database.py` are strictly additive:
1. Stop the application unit (`sudo systemctl stop dz-erp-api`).
2. Switch `/srv/dz-erp/app` to the previous verified Git tag/commit and reinstall its `requirements.lock.hashes.txt` into a clean virtualenv.
3. Run `python -m dz_erp.ops_checks readiness` and `python -m dz_erp.rotate_keys status --db $ERP_DB_PATH --vault $ERP_VAULT_PATH`.
4. Start `dz-erp-api` and verify `/healthz` and `/readyz?deep=1`.

### 4.2 Full Disaster-Recovery Restore from Encrypted Backup
1. Stop `dz-erp-api` so no writer holds `erp.sqlite3`.
2. Restore into a **clean isolated staging directory** on a patched Python `3.12.10+` runtime:
   ```bash
   sudo -u dz-erp /srv/dz-erp/.venv/bin/python -c "
   from pathlib import Path
   from dz_erp.ops_backup import resolve_backup_key
   from dz_erp.security_audit import restore_encrypted_backup
   res = restore_encrypted_backup(
       Path('/mnt/backup-offsite/dz-erp/<latest>.enc'),
       Path('/srv/dz-erp/restore_staging'),
       backup_encryption_key=resolve_backup_key(),
   )
   print(res)
   "
   ```
3. Verify the restored database SHA-256, trial-balance equilibrium (`db.trial_balance(company_id)`), vault blob readability, and every tenant's HMAC audit chain (`db.verify_all_audit_chains()`).
4. Atomically swap `/srv/dz-erp/data` and `/srv/dz-erp/vault` (retaining the damaged directories quarantined under `/srv/dz-erp/quarantine-<timestamp>` for forensic review) and restart `dz-erp-api`.
