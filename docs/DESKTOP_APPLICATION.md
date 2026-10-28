# Desktop Application — Installed, Local-First ERP (Phases 25–26)

Status: **implemented and locally verified** (see §8 for the exact evidence and
what remains unproven). This document describes the actual implementation, not
a plan.

## 1. Product direction

The product is **installed business software** (logiciel de gestion), not a
website: the user launches a desktop application from their own machine, the
accounting data lives on that machine (or on the company's own server), and
core operations — accounting, invoice intake, reporting — work without
internet. No browser is opened, no cloud account is required, and no public
website is part of the product.

The repository already contained a CI-verified FastAPI + SQLite backend with
server-verified authentication, RBAC, tenant isolation, a hash-chained audit
log, and an encrypted document vault. The desktop work does **not** replace
or parallel that backend: the client is a thin native layer that speaks the
same authenticated REST API, so every verified control is inherited, not
reimplemented.

## 2. Architecture

```
┌───────────────────────────── Mode A: single workstation ─────────────────────────────┐
│                                                                                      │
│  PySide6 desktop client  ──HTTP/127.0.0.1:ephemeral──►  Embedded ERP service          │
│  (dz_erp.desktop.ui)        bearer token               (uvicorn thread started by      │
│        │                                                dz_erp.desktop.service,      │
│        │                                                app built by serve.build_app)│
│        │                                                                                    │
│        ▼                                                                                    │
│  Local data: erp.sqlite3 + audit-key files + encrypted document vault (%LOCALAPPDATA%)   │
└──────────────────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────── Mode B: company LAN ────────────────────────────────────┐
│  N × PySide6 desktop clients  ──HTTP/LAN──►  company-hosted ERP server               │
│  (same client code)                          (python -m dz_erp.serve, existing runbooks)│
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Design rules enforced by code and tests:

- **The client never speaks SQL.** Every business operation goes through the
  authenticated REST API (`dz_erp.desktop.api_client.ErpClient`). Identity,
  company scope and role are derived server-side from the presented token.
- **One service code path.** The embedded service (Mode A) is built by the
  same `serve.build_app` used for a LAN deployment — migrations, audit-chain
  keys, token TTL and the fail-closed key-provider check all apply.
- **Loopback only, ephemeral port.** The embedded service binds
  `127.0.0.1:0`; any other host raises `ValueError`. There is no fixed port
  to collide with or probe, and nothing outside the workstation can connect.
- **Token in memory only.** The session token lives in the client object for
  the process lifetime and is never written to disk; sign-out revokes it
  server-side.
- **No secrets in configuration.** `dz_erp/desktop/config.py` persists a
  whitelist of paths and preferences (`config.json`); passwords and tokens
  have no field to land in.

## 3. Operating modes

| | Mode A — single workstation | Mode B — company LAN |
| :--- | :--- | :--- |
| Service | Embedded, private, in-process | Company-hosted `dz_erp.serve` deployment |
| Database | Local `erp.sqlite3` (WAL) on the workstation | Central DB on the company server; **never** a SQLite file shared over the network |
| Internet | Not required | Not required (LAN) |
| Client start | `dz-erp` | `dz-erp --mode server --server-url http://<server>:<port>` |
| Security log | Local `logs/security.jsonl` | Server-side (existing deployment config) |

Mode B reuse is complete at the client layer: `ErpClient` points at any
verified server. The server-side deployment, hardening and backup procedures
are the existing ones (`docs/STAGING_SECURITY_CONFIGURATION.md`, the
checkpoint/backup runbooks) and are out of scope of this document.

## 4. Installation and launch

```bat
:: from a checkout, with Python 3.11/3.12 installed
py -3.12 -m pip install -e ".[desktop]"
dz-erp                      :: launch the desktop application (Mode A by default)
dz-erp --language ar        :: Arabic interface (right-to-left layout)
dz-erp --mode server --server-url http://erp-intranet:8000   :: Mode B
dz-erp --smoke              :: headless verification of Mode A (no GUI)
dz-erp --version
```

`PySide6==6.11.2` is an **optional** extra (`desktop`): the CI matrix and any
server deployment install without it, and the Qt-free desktop modules
(config, embedded service, API client, i18n) work without it. Starting the
GUI without PySide6 prints the exact install command instead of a traceback.

Entry points: the `dz-erp` console script (`pyproject.toml →
[project.scripts]`) and `python -m dz_erp.desktop` are the same code
(`dz_erp.desktop.app:main`).

## 5. Data locations and first run

Everything lives under the per-user application-data directory —
`%LOCALAPPDATA%\AlgerianERP` on Windows, `~/.local/share/AlgerianERP` (or
`$XDG_DATA_HOME`) elsewhere:

| Path | Contents |
| :--- | :--- |
| `config.json` | Preferences whitelist (language, mode, server_url, paths). No secrets. |
| `data/erp.sqlite3` | The accounting database (schema-migrated, hash-chained audit log, audit-key files beside it). |
| `data/vault/` | Encrypted document vault (key persisted beside itself on first use, per `dz_erp.intake_ocr`). |
| `logs/desktop.log`, `logs/security.jsonl` | Application and security logs (redacting logger; security events append-only JSONL). |

**First run** (Mode A): if the local database has no users, the client shows a
first-run wizard that creates the company and its admin — the same bootstrap
`python -m dz_erp.serve --bootstrap-admin` performs, minus the terminal
prompt. Re-running first-run against an existing installation **refuses and
changes nothing** (regression-tested: an "attacker" username cannot overwrite
an existing admin).

## 6. Security properties (implemented and tested)

| Property | Where | Evidence |
| :--- | :--- | :--- |
| Server-verified authentication | `dz_erp.auth`, reused by the API | Phase 7 suite; desktop tests log in through `/api/v1/auth/token` |
| Tenant isolation on every read | `dz_erp.database._require_company_scope` | Phase 25 tests: unscoped reads raise `TenantIsolationError`; tenant B never sees tenant A rows |
| Least-privilege reads with audited denial | `list_supplier_invoices_secure`, `trial_balance_secure` | Phase 25 tests: warehouse role gets HTTP 403 and a `SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED` audit event, raised after commit |
| Loopback-only embedded service | `EmbeddedErpService.__init__` | Phase 26 test: `host="0.0.0.0"` refused |
| Ephemeral port | `uvicorn` config `port=0` | Phase 26 test asserts `port > 0` and health over `127.0.0.1` |
| Security-log env restored | `EmbeddedErpService.start` | Phase 26 test: sentinel env value survives app construction |
| Memory-only token | `ErpClient._token` | Config whitelist test asserts no secret-bearing key can persist |
| Config crash-safety | `load_config` | Phase 26 test: corrupt file set aside (kept), defaults restored |
| Fail-closed key provider | `serve.build_app → configured_provider()` | Existing suite; unchanged by the desktop layer |

## 7. Backup, restore, uninstall

- **Backup (Mode A):** with the client closed, copy the `data/` directory
  (database + audit-key files + vault) — or use the existing checkpoint
  tooling against the same paths (`docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md`).
  Restoring means restoring all three parts together; the audit chain fails
  closed if key material is missing.
- **Uninstall** removes the program (and PySide6 with `pip uninstall`), but
  **never deletes `%LOCALAPPDATA%\AlgerianERP`** — company accounting data
  and backups stay on the machine unless the user deletes them explicitly.
- Logs may contain usernames and event metadata, never passwords or token
  values (redacting logger, existing suite).

## 8. Verification

Commands (Python 3.12 local run, see `docs/QA_TEST_MATRIX_AND_RESULTS.md` §2B
for current counts):

```bash
python -m pytest tests/test_phase26_desktop.py tests/test_phase25_read_api.py
python -m ruff check dz_erp tests
python -m dz_erp.desktop --smoke      # bootstrap → serve → login → read → stop, exit 0
```

`--smoke` prints a JSON summary (status, base URL, port, list counts, trial
balance) and exits 0. It creates a throwaway data directory (or honours
`--data-dir`) and is CI-runnable without Qt.

### 8.1 What is implemented vs. not (no aspirational claims)

Implemented: first-run wizard (company + admin + **official SCF chart of
accounts** seeded and exactly validated, `SetupBlockerError` when the verified
vendor source is absent); login; navigation with dashboard, SCF chart,
products, **suppliers & clients**, **stock**, documents (with upload), supplier
invoices (**review dialog: extraction fields with honest confidence display,
human correction via the authorized API, explicit submit-approval → approval →
posting** with the server's segregation-of-duties and duplicate protections
surfaced as readable errors; image/PDF preview), **invoice creation form
(single-line MVP)**, trial balance; French/Arabic switching with RTL layout;
about dialog; clean shutdown with token revocation; long operations (upload,
review actions) run off the GUI thread.

Known product gaps, deliberately not faked:

- User administration screens are not yet in the UI (the verified API and
  client methods exist).
- Multi-line invoice editing, purchase-order/GRN matching screens and printed
  reports are not in the UI; the API contract supports them.
- Arabic OCR accuracy is **not** claimed: the extraction fields carry honest,
  measured confidence (or "not measured"), and uncertain fields are visibly
  routed to manual review. No representative Arabic-scan accuracy figure has
  been measured on this branch.
- The packaged-exe smoke run (below) has not been executed on a
  policy-permitting host.

The trial balance and all accounting figures remain subject to review by a
qualified Algerian accountant; nothing here is a statutory certification.

### 8.2 Packaging

`tools/build_desktop.py` produces a self-contained one-file executable plus a
SHA-256 sidecar (PyInstaller is a build tool installed only on the packaging
machine — never a project dependency, never installed by CI):

```bat
py -3.12 -m pip install pyinstaller          :: build tool, packaging machine only
python tools/build_desktop.py --smoke        :: build + SHA-256 + packaged smoke run
python tools/build_desktop.py --check SHA256HEX dist\AlgerianERP.exe
```

Measured on this workstation (PyInstaller 6.22.3, PySide6 6.11.2, Python
3.12.10): the build succeeds — `dist\AlgerianERP.exe`, **128.3 MiB**, SHA-256
`9fedbfb717f23eb4a87e89dc4bec987962b68d990e6d96ca01083db4bfd3cea5`, sidecar
written and verified by `--check` (exit 0). Since Phase 27 the build also
bundles the vendored official SCF chart data (`--add-data`, ~1.6 MB, MPL-2.0)
which `dz_erp.localization.vendor_data_dir()` resolves inside the frozen
payload (`sys._MEIPASS/vendor`), with the `ERP_VENDOR_DATA_DIR` override as
the explicit fallback. Three execution findings recorded honestly: the first
build attempt died with `attempted relative import with no known parent
package` because PyInstaller executes a *script*, not a package `__main__` —
fixed by the `dz_erp.desktop.launcher` entry module (absolute imports, full
dependency graph bundled). Executing the resulting exe is then **blocked by
this host's Windows application-control policy** (WinError 4551, "application
control policy blocked this file") — an unsigned-binary distribution
constraint of the test machine, not an application defect. The Phase 27
`--add-data` build has not been rebuilt/re-executed on this host for the same
policy reason; the packaging change is verified by the frozen-resource unit
test (`vendor_data_dir` frozen path) and must be re-proven by a packaged smoke
run on a policy-permitting workstation:

```bat
dist\AlgerianERP.exe --smoke    :: expected: the same JSON payload as §8, exit 0
```

Integrity model: the SHA-256 sidecar proves artifact integrity, not
publisher identity. Signing the installer (Authenticode or an
enterprise-documented internal procedure) is recorded as an outstanding
item, not claimed as solved.
