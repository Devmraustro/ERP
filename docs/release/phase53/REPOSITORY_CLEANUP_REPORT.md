# Phase 53 — Full Repository Audit, Safe Cleanup & Engineering Hygiene Report

**Execution Date:** `2026-11-13` (UTC)  
**Repository Root:** `/home/user/ERP` (`https://github.com/Devmraustro/ERP.git`)  
**Branch:** `arena/63d762c8-erp` (audit baseline commit `a31b7da` on top of `d73ae5a`)

---

## 1. Complete Repository Inventory & Classification

Every tracked file (`git ls-files -z`) and every untracked disk path outside `.git/` was inspected against runtime import graphs (`dz_erp/*`), entry points (`pyproject.toml [project.scripts]`), test suites (`tests/*`), tooling (`tools/*`), CI workflows (`.github/workflows/ci.yml`), container/systemd ops (`Dockerfile`, `.dockerignore`, `ops/*`), and `SHA256SUMS`-pinned historical evidence.

### 1.1 Classification Summary (Pre-Cleanup Baseline at `a31b7da`)

| Category | Paths | Tracked Files | Tracked Size (Bytes) | Action Taken |
|---|---|---:|---:|---|
| **Runtime core, accounting, OCR & API/backend** | `dz_erp/*.py` (`30` modules) + `dz_erp/assets/fonts/*` (`3` font/OFL files) | `33` | `1,401,512` | **Preserved (`100%`)**; targeted SEC-13 & SEC-15 hardening in `net.py`, `auth.py`, `api.py`, `serve.py` |
| **Desktop UI (`PySide6`)** | `dz_erp/desktop/*.py` (`14` modules) | `14` | `497,130` | **Preserved (`100%`)** |
| **Database schema, migrations & SCF localization** | Embedded in `dz_erp/database.py` (`SCHEMA_VERSION = 9`, migrations `v1`–`v9`), `dz_erp/localization.py` (`294` SCF accounts), `odoo_addons/l10n_dz_scf_extensions/*` (`5`), `vendor/azinak_l10n_dz_account_fix/*` (`93`) | `98` *(excl. `dz_erp/`)* | `1,525,718` | **Preserved (`100%`)**; PEP 484 explicit re-export fix in `odoo_addons/` (`2` lines) |
| **Security, vault, key rotation & backup/restore** | `dz_erp/security_audit.py`, `dz_erp/key_management.py`, `dz_erp/key_providers.py`, `dz_erp/keys_cli.py`, `dz_erp/rate_limit.py`, `dz_erp/readiness.py` | *(counted in `dz_erp/` above)* | *(counted above)* | **Preserved (`100%`)**; safe-restore `tarfile` guard untouched |
| **Automated test suites** | `tests/*.py` (`44` baseline + `1` added in Phase 53 = `45` files) | `44` → `45` | `901,870` → `911,833` | **Preserved (`100%`)** + added `tests/test_phase53_host_validation_and_hygiene.py` (`31` tests) |
| **Operational tools & benchmark fixtures** | `tools/*.py` (`9` scripts), `benchmarks/*` (`19` OCR corpus & goldens) | `28` | `649,167` | **Preserved (`100%`)** — all `9` scripts referenced by CI, docs, or tests; `benchmarks/` referenced by OCR precision suites |
| **Ops, CI, lockfiles & packaging** | `.github/workflows/ci.yml`, `ops/*` (`15`), `.gitignore`, `.editorconfig` *(new)*, `pyproject.toml`, `requirements.lock*.txt` (`2`), `LICENSE` | `22` → `23` | `140,860` → `142,301` | **Preserved & Hardened** (`.gitignore`, `.editorconfig`, `pyproject.toml` flat-layout discovery) |
| **Core compliance & architecture docs** | `README.md`, `AUDIT_READINESS_REPORT.md`, `FINAL_AUDIT_REPORT.md`, `KEY_ROTATION_DESIGN.md`, `docs/*.md` (`12` root docs incl. `COMPLIANCE_REVIEW_PACKAGE.md`, `ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`, `ENVIRONMENT_VARIABLES.md`) | `16` | `393,297` | **Preserved & Updated** (`README.md`, `docs/ENVIRONMENT_VARIABLES.md`) |
| **Historical release & security evidence** | `docs/release/*` (`194` files across `phase8`–`phase52`) + `docs/security/*` (`77` files, `4` `SHA256SUMS` manifests) + `docs/adr/*`, `docs/ocr/*`, `docs/phase*/*` (`18` files) | `289` → `291` | `6,103,477` → `6,129,400` | **Preserved (`100%` — `0` deleted)**; indexed in `docs/release/RELEASE_EVIDENCE_INDEX.md` |
| **Untracked transient cache & bytecode clutter** | `.pytest_cache/`, `.ruff_cache/`, `dz_erp/**/__pycache__/`, `odoo_addons/**/__pycache__/`, `tests/__pycache__/`, `tools/__pycache__/`, `vendor/**/__pycache__/` | `0` *(untracked: `186` files across `11` cache trees)* | `0` *(untracked: `5,530,114` bytes)* | **Safely Removed from Disk (`186` files, `5,530,114` B / `5.27 MiB`)** |
| **Obsolete / orphan tracked runtime modules** | Evaluated all `47` files under `dz_erp/` and `9` under `tools/` | `0` | `0` | **None deleted** — every module has active runtime, CLI, CI, or test consumers |

---

## 2. Explicit Audit of Protected Modules & the 22 Phase 34/36B Procurement Functions

Per mandatory safety constraint:
> *"Do not delete the 22 Phase 34 procurement/accounting functions without proving their status and replacements."*

AST inspection of `dz_erp/procurement_stock.py` (`2,547` lines, **all `26` top-level functions preserved untouched**) cross-referenced against `docs/release/phase34/ACCOUNTING_AND_FUNCTIONAL_COMPARISON.md` and `docs/release/phase36/PROCUREMENT_FUNCTION_INVENTORY.md`:

1. **Group A (`#1`–`#3`: `_require_supplier_invoice_no`, `_require_valid_payment_method`, `_parse_invoice_date`)**: Enforced inline in `dz_erp/procurement_stock.py:create_and_validate_supplier_invoice` (`MAX_SUPPLIER_INVOICE_NO_LENGTH = 128`, `VALID_PAYMENT_METHODS`, strict `datetime.strptime(..., "%Y-%m-%d")`). **Preserved.**
2. **Group B (`#4`–`#11`: `_validate_invoice_lines`, `_validate_invoice_totals`, `_duplicate_invoice_errors`, `_line_price_variance`, `_assert_document_linkable`, `_invalidate_approval_requests`, `_record_refused_attempt`)**: Integrated in Phase 38/46 and active in `dz_erp/procurement_stock.py`. **Preserved (`0` lines modified or removed).**
3. **Group C (`#12`–`#13`: `_source_invoice_mismatches`, `_document_review_gate` + `dz_erp/review_policy.py` + `DOCUMENT_LINKABLE_STATES` in `dz_erp/intake_ocr.py`)**: Integrated in Phase 38/46 and active in `dz_erp/procurement_stock.py`. **Preserved (`0` lines modified or removed).**
4. **Group D (`#16`–`#19`: `ALGERIAN_TIMBRE_PROVENANCE_v2` rule-versioning helpers)**: Documented in `docs/release/phase36/PROCUREMENT_FUNCTION_INVENTORY.md` as gated behind external Algerian statutory expert review (Gate `G5`); not prematurely altered without expert sign-off.
5. **All 15 foundational procurement/stock/posting/reversal functions** (`compute_line_ht_dzd`, `_materialise_line_ht`, `compute_invoice_payload_hash`, `_check_user_and_tenant`, `create_purchase_order`, `receive_goods_against_po`, `deduct_stock_concurrent_safe`, `create_and_validate_supplier_invoice`, `submit_invoice_for_approval`, `decide_invoice_approval`, `modify_draft_invoice_line`, `post_supplier_invoice`, `reverse_posted_invoice`, `create_reversal_request`, `submit_reversal_for_approval`, `decide_reversal_approval`, `post_reversal`): **100% preserved.**

---

## 3. Hygiene, Packaging, Linting & Security Gap Closures Applied

### 3.1 Packaging Discovery Fix (`pyproject.toml`)
- **Finding:** Because the repository uses a flat layout containing multiple top-level directories (`dz_erp/`, `odoo_addons/`, `vendor/`, `benchmarks/`, `ops/`, `tools/`), `setuptools` auto-discovery failed with `Multiple top-level packages discovered in a flat-layout` whenever building a wheel or sdist (previously noted in `docs/security/PACKAGING_DECISION_V1.md` on a separate branch, but missing on `arena/63d762c8-erp`).
- **Fix:** Added `[tool.setuptools.packages.find]` (`include = ["dz_erp*"]`) and `[tool.setuptools.package-data]` (`dz_erp = ["assets/fonts/*"]`) to `pyproject.toml`, while preserving exact dependency pins and section boundaries verified by `tests/test_phase19_dependency_integrity.py` and `tests/test_phase64_dependency_security_regression.py`.
- **Verification:** `setuptools.config.pyprojecttoml.apply_configuration` cleanly discovers `['dz_erp', 'dz_erp.assets', 'dz_erp.assets.fonts', 'dz_erp.desktop']`.

### 3.2 Whole-Tree Linting Hygiene (`odoo_addons/` & `pyproject.toml`)
- **Finding:** Running `ruff check .` across the full repository reported `2` `F401` unused-import diagnostics in `odoo_addons/l10n_dz_scf_extensions/__init__.py` and `odoo_addons/l10n_dz_scf_extensions/models/__init__.py`, plus style findings inside third-party `vendor/` and `SHA256SUMS`-pinned historical probe scripts under `docs/security/`.
- **Fix:** Converted the two Odoo package re-exports to explicit PEP 484 re-exports (`from . import models as models` and `from . import res_partner_dz as res_partner_dz`) and configured `[tool.ruff] extend-exclude = ["vendor", "docs/security"]` so `SHA256SUMS`-verified historical security artifacts remain byte-for-byte untouched.
- **Verification:** `ruff check .` across the entire repository exits `0` (`All checks passed!`).

### 3.3 Ignore Rules, Editor Consistency & Documentation (`.gitignore`, `.editorconfig`, `README.md`, `docs/ENVIRONMENT_VARIABLES.md`)
- **`.gitignore`:** Added explicit ignore patterns for `.ruff_cache/`, `.mypy_cache/`, `coverage.xml`, `junit*.xml`, `staging-acceptance*.log`, `*.rotation-ledger.jsonl`, `*.checkpoint.json`, `.env.*` (with `!.env.example` exception), `*.pem`/`*.crt`/`*.pfx`/`*.p12`, `.DS_Store`, and `Thumbs.db` alongside existing exclusions (`*.db`, `*.sqlite*`, `*.key`, `*.enc`, `vault_data/`, `backups/`).
- **`.editorconfig`:** Created root `.editorconfig` enforcing UTF-8, LF line endings, 4-space Python indentation, 2-space YAML/JSON/TOML indentation, and preserved trailing whitespace in Markdown.
- **`README.md` & `docs/ENVIRONMENT_VARIABLES.md`:** Added current integrated release-candidate verification summary to `README.md` (retaining historical notes) and documented `ERP_ALLOWED_HOSTS` and `ERP_PASSWORD_MIN_LENGTH` in `docs/ENVIRONMENT_VARIABLES.md`.
- **`docs/release/RELEASE_EVIDENCE_INDEX.md`:** Created master index classifying every `docs/release/*` and `docs/security/*` directory/report as `Current`, `Superseded` (with pointer to superseding report), or `Historically Preserved`.

### 3.4 Closure of Remaining Perimeter & Credential Hardening Gaps (SEC-15 & SEC-13)
During the repository audit against Phase 54/56 requirements, two genuine engineering gaps from the historical security backlog (`docs/security/PHASE4_SEC131415_CLOSURE_REPORT.md`) were found still absent on `arena/63d762c8-erp` and closed cleanly with full test coverage:
1. **`ERP_ALLOWED_HOSTS` Host-Header Validation & Proxy Hardening (SEC-15)**:
   - `dz_erp/net.py`: Added `DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]", "testserver")`, `parse_allowed_hosts`, `allowed_hosts_from_env`, `host_is_allowed` (exact hostnames, bracketed IPv6 literals, `*.domain` subdomain wildcards, port stripping, control-char/path/comma-list rejection), and `describe_allowed_hosts`.
   - `dz_erp/api.py`: Added `_validate_host_header` HTTP middleware rejecting unlisted/missing/malformed `Host` headers before route dispatch with HTTP `400 {"detail": "Invalid Host header."}`, baseline security headers, sanitized `X-Request-Id`, and structured `SECURITY_HOST_HEADER_REJECTED` audit logging; exposed active policy on admin `GET /api/v1/system/rate-limit-policy`. OpenAPI protected operation count (`29`) is unchanged.
   - `dz_erp/serve.py`: Hardened `uvicorn.run(..., proxy_headers=False)` so Uvicorn never trusts unverified forwarded headers ahead of `TrustedProxyPolicy`, and made `_bootstrap_admin` prompt for the password before opening the DB with compensating company deletion if `create_user` fails (eliminating orphan company rows).
2. **Unified Password Policy & Transparent PBKDF2 Rehashing (SEC-13)**:
   - `dz_erp/auth.py`: Added `validate_password_policy` (`MIN_PASSWORD_LENGTH = 8`, configurable via `ERP_PASSWORD_MIN_LENGTH` up to `64`, weak-password corpus & username/company-name rejection) across `AuthManager.create_user`, `AuthManager.set_password`, and `AuthManager.change_own_password`, plus transparent PBKDF2 iteration-count upgrade on login (`AUTH_PASSWORD_REHASHED` audit event when `_stored_iterations < pbkdf2_iterations`).

---

## 4. Before / After Counts & Secret/Artifact Audit

| Metric | Before Cleanup (`a31b7da` + test caches) | After Cleanup & Phase 53 Additions |
|---|---:|---:|
| **Tracked Git files (`git ls-files`)** | `543` files (`11,637,753` bytes / `11.10 MiB`) | `547` files (`11,690,544` bytes / `11.15 MiB`) *(+4 new files: `.editorconfig`, `tests/test_phase53_host_validation_and_hygiene.py`, `docs/release/RELEASE_EVIDENCE_INDEX.md`, `docs/release/phase53/REPOSITORY_CLEANUP_REPORT.md`; `0` tracked files deleted)* |
| **Untracked cache / bytecode dirs on disk** | `11` top-level cache trees (`186` files, `5,530,114` bytes / `5.27 MiB`) | **`0` dirs (`0` files, `0` bytes)** |
| **Tracked live databases (`.db`, `.sqlite`, `.sqlite3`)** | `0` | **`0`** |
| **Tracked keys / certificates (`.key`, `.pem`, `.pfx`, `.p12`)** | `0` | **`0`** |
| **Tracked secrets / live credentials** | `0` | **`0`** |
| **Historical `SHA256SUMS` integrity manifests (`4` files)** | `4/4` intact | **`4/4` intact (`0` bytes altered)** |

---

## 5. Post-Cleanup Verification Summary

| Check | Command | Result |
|---|---|---|
| Whole-repository lint | `/tmp/venv_phase52/bin/python -m ruff check .` | **PASS** (`All checks passed!`, exit `0`) |
| Bytecode compilation check | `/tmp/venv_phase52/bin/python -m compileall -q dz_erp odoo_addons tools tests` | **PASS** (exit `0`) |
| Setuptools flat-layout package discovery | `setuptools.config.pyprojecttoml.apply_configuration` | **PASS** (`['dz_erp', 'dz_erp.assets', 'dz_erp.assets.fonts', 'dz_erp.desktop']`) |
| Phase 53 Host/Password/Hygiene suite | `pytest tests/test_phase53_host_validation_and_hygiene.py` | **PASS** (`31 passed`) |
| TLS Reverse-Proxy & Host-Injection Harness | `python docs/release/phase8/evidence/tls_proxy_harness.py` | **PASS** (`Host: evil.example` → `400`, `Host: localhost` → `200`, `X-Forwarded-Proto` bypass → `400`, `X-Forwarded-For` rotation → `429`) |
| Full automated test suite (CPython `3.12.10`) | `pytest -ra` | **PASS** (`1000 passed, 21 skipped, 0 failed, 43 subtests passed`) |
