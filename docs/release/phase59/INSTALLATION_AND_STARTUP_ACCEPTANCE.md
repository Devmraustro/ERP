# Phase 59A — Installation and Startup Acceptance

**Repository:** `https://github.com/Devmraustro/ERP.git`
**Branch:** `arena/63d762c8-erp`
**Base commit of this branch:** `098483a1f02df0308f3889c93714efab5d13b550`
**Feature freeze:** in force. Nothing below adds a module, a workflow or an AI capability; every
change is a defect repair traced to a failed acceptance check, and each is named as such.
**Document status:** evidence of what was executed. It is **not** a production-readiness claim and
**not** legal approval.

---

## 1. Verdict

| Platform | Status | Basis |
|---|---|---|
| Linux x86-64, CPython 3.11.2 (sandbox) | **PASS — EXECUTED** | §3, §4: fresh install, schema, chart, bootstrap, serve, health/ready, shutdown |
| Linux x86-64, CPython 3.12.10 | **NOT EXECUTED HERE** | No 3.12 interpreter could be obtained in this environment (§7.1). CI runs 3.12 (`verify`, `verify-windows`, `verify-desktop`) |
| Windows 10/11 x64 | **NOT EXECUTED** | No Windows host or runner available to this session. Procedure recorded in §8 and in `docs/release/phase8/WINDOWS_DESKTOP_ACCEPTANCE.md` |

**One blocking defect was found and fixed** during this phase: `--bootstrap-admin` created a company
and an admin user but **never loaded the SCF chart of accounts**, so a freshly installed deployment
had zero accounts and the first master-data write failed. Details in §5.1.

---

## 2. Prerequisites as declared

| Requirement | Declared | Verified here |
|---|---|---|
| CPython | 3.11.17+ or 3.12.10+ (below 3.13) | Floor enforced by the restore path; sandbox runs 3.11.2, **below the floor** (§7.1) |
| Runtime dependencies | `requirements.lock.txt` — 53 pins, 1,317 SHA-256 digests | `pip install --no-deps --require-hashes` succeeded |
| Desktop (optional) | `requirements-desktop.lock.txt` — 4 pins (PySide6 stack), 20 digests | **File did not exist**; created in this phase (§5.2) and hash-install proven |
| Disk | Database + vault + backups on a durable volume | `ERP_DB_PATH` / `ERP_VAULT_PATH` / `ERP_BACKUP_DIR` |
| Network | Loopback only by default | `ERP_HOST` default `127.0.0.1` |

---

## 3. Clean-install sequence (executed)

All commands were run from a pristine virtual environment. No system package was installed, and no
mirror other than PyPI was used.

```sh
python3 -m venv /tmp/v59
/tmp/v59/bin/python -m pip install --upgrade pip
/tmp/v59/bin/python -m pip install --no-deps -r requirements.lock.txt      # 53 pins
/tmp/v59/bin/python -m pip install --no-deps -r requirements-desktop.lock.txt   # 4 Qt pins
```

Result: `EXIT=0`, `import fastapi, sqlalchemy, pypdf` → `core ok`; `PySide6 6.11.2` importable.
The hash-verified form (`--require-hashes -r requirements.lock.hashes.txt`) is exercised by CI on
every run and was executed separately for the desktop lockfile:

```
pip install --no-deps --require-hashes -r requirements-desktop.lock.hashes.txt   → EXIT=0, PySide6 6.11.2
```

---

## 4. First start, bootstrap and readiness (executed)

### 4.1 Declared configuration

| Variable | Value used | Required |
|---|---|---|
| `ERP_DB_PATH` | `/tmp/acc2/erp.sqlite3` | yes — start-up refuses without it |
| `ERP_VAULT_PATH` | `/tmp/acc2/vault` | yes — start-up refuses without it |
| `ERP_BACKUP_DIR` | `/tmp/acc2/backups` | for `ops_backup` |
| `ERP_BACKUP_KEY_FILE` | `/tmp/acc2/backup.key` (mode `0o600`) | for `ops_backup` |
| `ERP_ADMIN_PASSWORD` | supplied on stdin to `--bootstrap-admin` | initial credential only |

Both paths are **required and have no baked-in default**. Starting without `ERP_VAULT_PATH` produced
exactly:

```
ERP_VAULT_PATH is not set. Point it at a durable location - the defaults are intentionally not
baked in so a fresh deployment cannot accidentally run against a throwaway file.
```

That is the intended fail-closed behaviour: a misconfigured deployment stops instead of writing a
database next to the source tree.

### 4.2 Admin bootstrap

```sh
printf 'Str0ng-Admin-Pass!2026\n' | python -m dz_erp.serve --bootstrap-admin admin --company-name 'SARL Acceptation'
```

Output: `Created admin 'admin' (id=1) in /tmp/acc2/erp.sqlite3`

Database state immediately after bootstrap — **this is the line that matters**:

| Table | Rows |
|---|---|
| `accounts` | **294** |
| `account_groups` | 68 |
| `taxes` | 8 |
| `companies` | 1 |
| `users` | 1 |

Before the §5.1 fix the same bootstrap produced `accounts = 0`.

### 4.3 Server start and probes

```sh
python -m dz_erp.serve --port 8098     # with ERP_DB_PATH, ERP_VAULT_PATH set
```

| Probe | Command | Result |
|---|---|---|
| Liveness | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8098/healthz` | `200` |
| Readiness | `curl .../readyz` | `200`, body below |
| Schema created | `/readyz` `database.values.table_count` | `26` |
| Vault key | `/readyz` `vault.values.key_mode` | `0o600` |
| Vault dir | `/readyz` `vault.values.dir_mode` | `0o755` (traversal requires `x`, not `w`) |
| Alive-by-default guard | `/readyz` top-level `status` | `degraded` — correct for a fresh install with no backup yet |

Readyz excerpt:

```json
{"status": "degraded", "checks": {"database": {"status": "ok", "critical": true,
 "values": {"table_count": 26, "write_lock": true, "duration_ms": 2.19}},
 "vault": {"status": "ok", "critical": true,
 "values": {"writable": true, "key_present": true, "key_mode": "0o600", "dir_mode": "0o755"}}}}
```

`degraded` on a brand-new install is the honest answer: the application is serving, but the backup
subsystem has no backup yet. It flips to `ok` after the first verified backup.

### 4.4 Host-header handling

Two defects were found in the previous phase of this session and both are closed:

| Case | Before | Now |
|---|---|---|
| `Host: erp.local` (unknown) | `400` with no way to configure the LAN hostname | `400` **and** `ERP_ALLOWED_HOSTS=erp.local` makes it `200` |
| `Host: localhost` | `200` | `200` |
| `Host: 127.0.0.1:8098` (with port) | — | `200` (port suffix stripped) |

Verified: `unknown Host: 400`, `localhost Host: 200`. The rejection is audited as
`SECURITY_HOST_HEADER_REJECTED`.

### 4.5 Shutdown and restart

The server was stopped and restarted against the same database and vault. On restart:

* `/healthz` → `200`, `/readyz` → `200`;
* the admin token issued before the restart is still usable for its TTL window (it was re-issued
  in each script run; the session endpoint resolved `user_id=1, company_id=1, role=admin`);
* no re-seeding occurred and no duplicate chart was written (`accounts` stayed at 294 — the
  bootstrap guard refuses a second run: *"this database is already bootstrapped"*).

### 4.6 Data outside the source tree

The database, vault, key file and backups were all created under `/tmp/acc2/`, i.e. entirely outside
the checkout. `git status` shows no database, key, vault or backup artefact — confirmed by the
`.gitignore` rules for `*.db`, `*.sqlite3`, `*.key`, `*.pem`, `*.keys.json`, `*.rotation*.jsonl`.

### 4.7 Paths with spaces and non-ASCII characters

The vault is created with `os.makedirs` and the key written with `os.open`; all paths are passed as
`pathlib.Path` objects, never string-concatenated, so a space or a non-ASCII character in the
installation directory is not a code path of its own. **This was not executed in this phase** beyond
the general suite (which uses `tmp_path`); the explicit check is Windows/item 8 in §8.

---

## 5. Defects found and fixed in this phase

### 5.1 DEFECT — `--bootstrap-admin` left the deployment without a chart of accounts *(fixed)*

**Symptom (observed, not hypothesised).** After a clean `--bootstrap-admin`, `GET /api/v1/accounts`
returned `200` with `accounts = 0`, and the first product write failed:

```
HTTP 422 {"detail": "stock_account_code '300' is not an account of this company's SCF chart."}
```

**Root cause.** The desktop bootstrap (`dz_erp/desktop/service.py`, step 3) calls
`load_official_odoo_chart(...)`. The CLI bootstrap in `dz_erp/serve.py::_bootstrap_admin` created the
company row and the admin user and **skipped the chart load entirely**. Posting and every master-data
write validate against the chart, so the deployment was unusable for its stated purpose while
reporting itself healthy.

**Fix.** `_bootstrap_admin` now loads the chart between company creation and user creation, and
compensates (removes the accounts and the company) if either the chart load or the user creation
fails, so a half-built deployment cannot be left behind. Comment in the code names the desktop
sequence it must match.

**Verification.** Re-ran the identical bootstrap on a fresh directory: `accounts = 294`,
`account_groups = 68`, `taxes = 8`. Full end-to-end workflow then proceeded past master data
(§`END_TO_END_OPERATIONAL_ACCEPTANCE.md`).

**Residual risk.** A deployment created *before* this fix has an empty chart. There is no migration
that retro-fits it. Operator action is in §8 item 9: run the chart load once against the existing
company, or re-bootstrap. This is called out in `ADMINISTRATION_AND_RECOVERY.md`.

### 5.2 DEFECT — the documented desktop lockfile did not exist *(fixed)*

**Symptom.** Two documented procedures instructed the operator to run
`pip install --require-hashes -r requirements-desktop.lock.hashes.txt`. **Neither that file nor its
version file existed in the repository**, so the documented desktop install could not be performed at
all.

**Fix.** Added `requirements-desktop.lock.txt` (4 pins, one shared version) and the generated
`requirements-desktop.lock.hashes.txt` (20 digests), extended `tools/generate_hash_lock.py` with
`--desktop --write|--check`, and added a regression test that keeps the extra, the pins file and the
hash file in step. The documented Windows procedures now use the real flags
(`--no-deps --require-hashes`).

### 5.3 DEFECT — the desktop UI suites never ran in CI *(fixed)*

**Symptom.** Every Qt test file guards itself with `importorskip("PySide6.QtWidgets")`. PySide6 is an
optional extra and is absent from the runtime lock, so in CI all of them **skipped**: the review
dialog, the user-administration screen and the RTL layout were green-by-omission.

**Fix.** Added a `verify-desktop` job to `.github/workflows/ci.yml` that installs the desktop
lockfile, asserts the locked Qt version imports, runs the five desktop suites, and **fails if any
test skips**. A regression test pins the job's existence, the hash install and the no-skip rule.

**Local reproduction of that job** (offscreen Qt, headless shim):

```
55 tests, 0 skipped, 0 failed, 0 errors
```

---

## 6. What was *not* exercised, stated plainly

| Item | Why not |
|---|---|
| Windows install, ACLs, `%LOCALAPPDATA%` paths, non-admin user | No Windows host |
| CPython 3.12.10 boot | Not obtainable here (§7.1) |
| Real display, High-DPI scaling, Arabic shaping on screen | Headless sandbox; the suites construct and measure widgets, they do not present them |
| TLS / reverse proxy | No certificate authority or host here; procedure in `REVERSE_PROXY_TLS_ACCEPTANCE.md` |
| Signed installer / MSI | None exists; the declared distribution is a wheel + venv |

---

## 7. Environment limits of this evidence

### 7.1 Python floor

The restore path refuses to run on CPython older than 3.11.17 / 3.12.10:

```
Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13);
unsafe archive member types and paths are rejected.
```

The sandbox provides **3.11.2** only. Consequences for this document:

* install, bootstrap, serve, health/ready, host validation, master data, invoicing, approval, posting,
  trial balance and reversal were **all executed** (§4, and the E2E report);
* **deep backup verification and restore were not executable** here (§`DATA_PROTECTION_AND_RECOVERY_ACCEPTANCE.md`).

An attempt to obtain CPython 3.12.10 from `python-build-standalone` failed: GitHub release assets are
not reachable from this sandbox (`curl` exit 35; `pbs_installer` → `TLS/SSL connection has been
closed`). PyPI is reachable; GitHub source/release downloads are not. This is recorded so nobody
mistakes the gap for a passing check.

### 7.2 Headless Qt

Qt links `libGL`, `libEGL`, `libxkbcommon` and the XCB stack even for the `offscreen` platform, and
those system libraries are absent. A stub-library directory satisfies the dynamic loader so the real
PySide6 can be exercised; the stubs abort if called, so no Qt behaviour is simulated. Recipe is
reproducible: `/tmp/mkshim.py` (kept out of the repository — it is a sandbox workaround, not a
product artefact).

---

## 8. Operator actions still required for a real installation

1. **Install on the target OS.** Follow `docs/user/USER_GUIDE_FR.md` / `USER_GUIDE_AR.md`; the
   Windows steps are in `docs/release/phase8/WINDOWS_DESKTOP_ACCEPTANCE.md` and are **unexecuted**.
2. **Run as a non-administrator** and confirm the database, vault and backup directories are writable
   only by the service account. Expected POSIX result: key `0o600`, vault dir `0o755` (both were
   observed here); on Windows the equivalent is an NTFS ACL.
3. **Install into a path containing a space** (e.g. `C:\Program Files\ERP`) and confirm start-up.
4. **Point the data directories at a durable volume**, never the install directory.
5. **Never expose** the database file, the vault directory or the key file over HTTP. `/readyz`
   reports presence and mode only; it does not echo paths or key material.
6. **Set `ERP_ALLOWED_HOSTS`** to the real hostname before opening the port to the LAN. Default is
   loopback + `testserver` only.
7. **Set `ERP_PASSWORD_MIN_LENGTH=12`** — the default floor of 8 is a compatibility floor, not a
   recommendation.
8. **Set `ERP_BACKUP_KEY_FILE`** and take one backup, then confirm `/readyz` becomes `ok`.
9. **If the database was bootstrapped before this fix,** the chart is empty. Verify with
   `SELECT COUNT(*) FROM accounts;` — if `0`, run the chart load for that company once (documented in
   `docs/admin/ADMINISTRATION_AND_RECOVERY.md`) or re-bootstrap into a new database.
10. **Confirm the Python version is at or above the floor** (`python -VV`); 3.11.2 is not supported for
    restore and this document does not claim it is.

---

## 9. Evidence index

| Claim | Command / artefact |
|---|---|
| Clean install | `pip install --no-deps -r requirements.lock.txt` → `EXIT=0` |
| Desktop lock hash install | `pip install --no-deps --require-hashes -r requirements-desktop.lock.hashes.txt` → `PySide6 6.11.2` |
| Bootstrap seeds the chart | `SELECT COUNT(*) FROM accounts` → `294` |
| Readiness | `curl -s http://127.0.0.1:8098/readyz` → `200`, `table_count 26` |
| Host allow-list | `Host: erp.local` → `400`; with `ERP_ALLOWED_HOSTS=erp.local` → `200` |
| Refuses missing paths | unset `ERP_VAULT_PATH` → `SystemExit` with the quoted message |
| Bootstrap idempotence | second `--bootstrap-admin` → "this database is already bootstrapped" |
| Desktop suites | `pytest` on the five files → 55 passed, 0 skipped |

Reproduce the whole sequence with the commands in §3–§4.4; no credential, network peer or paid
resource is involved.
