# QA Test Matrix and Execution Results (Phase 8)

## 1. Execution Command & Summary (`Passed`)

The suite is run from the **declared reproducible dependency set** — no stub
library, no preconfigured virtualenv, and no manual installation step:

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install --no-deps -r requirements.lock.txt
python tools/verify_dependency_graph.py      # explicit graph validation
python -m pytest --cov=dz_erp --cov-report=term-missing
python -m ruff check dz_erp tests tools      # static check
```

> **Note on `libGL`:** an earlier revision of this document recorded a
> `LD_LIBRARY_PATH=/tmp/glstub` workaround for a locally compiled `libGL.so.1`
> stub.  That is **retired**: a stub is not a supported configuration, it is not
> reproducible in CI, and it masked the real defect.  The defect
> (`rapidocr-onnxruntime` pulling in the GUI `opencv-python` wheel) is fixed at
> the dependency level — see §1.1 below.

### 1.1 OpenCV / RapidOCR dependency defect — root cause and resolution

| Item | Finding |
| :--- | :--- |
| Declared upstream requirement | `rapidocr-onnxruntime==1.4.4` → `opencv-python>=4.5.1.48` (the **GUI** wheel) |
| Why it broke | The GUI wheel bundles the Qt5/xcb GUI stack and links `libGL.so.1`, absent from a minimal Linux container |
| Proof the GUI stack is unnecessary here | `nm -D --undefined-only cv2.abi3.so` → **zero `gl*` symbols**; the `libGL` need comes entirely from the bundled Qt5 libraries, not from the `cv2` extension module |
| No PyPI source for `libGL` | `https://pypi.org/pypi/libgl1/json` → `404`; a system package manager is the only source |
| Why "install Headless last" was rejected | Two independent clean installs (headless first, then headless last in the requirements file) both ended with the GUI build owning `site-packages/cv2/`. **pip does not preserve requirements-file order for these two packages.** |
| Resolution | `opencv-python-headless==4.11.0.86` is declared and pinned; the GUI wheel is excluded from `requirements.lock.txt`, which enumerates the complete transitive closure and is installed with `--no-deps` |
| Result | Order-independent, reproducible, and needs **no** system graphics library |
| Known deviation (not concealed) | `pip check` reports exactly one line: `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.` |
| How the graph is validated instead | `tools/verify_dependency_graph.py` — fails on *any* unsatisfied requirement other than this single allow-listed substitution, asserts the installed `cv2` really is the headless build (the GUI payload directory `opencv_python.libs/` must be absent), and asserts `cv2` and `RapidOCR` both import |
| Mandatory runtime libraries | **None.** Every pinned runtime dependency is a pure-Python or wheel package |
| Optional (GUI build only) | `libgl1`, `libglib2.0-0` — needed only if the GUI `opencv-python` build is deliberately reinstalled for interactive debugging |

### 1.2 Current verification — exact head 2de2ae7 (2026-10-26)

The exact PR head `2de2ae7e8aca1fa522b4e4a1f1210f18f86bc838` passed GitHub Actions run [40382194997](https://github.com/Devmraustro/ERP/actions/runs/40382194997). All three jobs completed successfully.

- **Linux / Python 3.11.17:** 620 passed, 3 skipped, 0 failed; **88% coverage** (4,575 statements, 534 missed); full suite exit 0.
- **Linux / Python 3.12.15:** 620 passed, 3 skipped, 0 failed; **88% coverage** (4,575 statements, 534 missed); full suite exit 0.
- **Windows / Python 3.12.10:** key rotation, migration, backup/restore, logging and recovery subset: 148 passed, 8 platform-specific skipped, 0 failed.
- **Synthetic staging acceptance:** 37/37 on Linux and 37/37 on Windows. The checkpoint path exercise uses a same-host sibling directory; it does not prove real off-host durability.
- **Risk register:** summary validation passed; 31 rows are consistent.
- **Phase 24 POSIX fixture:** the exact-head regression is green in the cross-platform CI matrix; the permission-gate test uses a POSIX-declaring shim without globally patching `os.name`.

Hash-verified dependency installation, dependency-graph validation, OpenCV/RapidOCR imports, Ruff and byte-compilation passed in the Linux jobs. Windows dependency validation, Ruff, byte-compilation and its security/recovery acceptance suite also passed. One warning was emitted in each pytest run; there were no test failures. The 3 Linux skips are platform-specific regression paths, while the 8 Windows skips concern POSIX-only behaviours.

This is evidence for the exact tested commit only. It does not establish external KMS/HSM custody, off-host checkpoint durability, end-to-end alert delivery, production-scale recovery, a representative authorized OCR corpus, or professional/legal sign-off.
### 1.3 Prior (Phase 7) results — retained for traceability

- **Total Collected Tests:** `87`
- **Passed:** `87` (`100%`)
- **Failed / Skipped / Errors:** `0`
- **Total Execution Duration:** `25.71s`
- **Environment:** Python `3.11.2`, `pytest 8.3.5`, SQLite `3.40.1` (`WAL`), FastAPI `0.115.12`
- **Overall Code Coverage (`dz_erp`):** **`92%`** (`1,848 / 2,001` statements covered; `100%` on `benchmark.py`, `97%` on `auth.py`, `96%` on `database.py` and `odoo_test_adapter.py`, `93%` on `localization.py`, `90%` on `intake_ocr.py` and `procurement_stock.py`, `89%` on `security_audit.py`, `84%` on `api.py`).
- **Overall Code Coverage (`dz_erp`):** **`89%`** (`1,489 / 1,666` statements covered; `100%` on `benchmark.py`, `97%` on `database.py`, `96%` on `odoo_test_adapter.py`, `93%` on `localization.py`, `90%` on `intake_ocr.py`, `89%` on `procurement_stock.py`, `86%` on `security_audit.py`).

### 1.4 Explicitly re-run regression groups

| Group | Keyword selection | Result |
| :--- | :--- | :--- |
| Forged / spoofed identity headers | `forged or impersonat or spoof` | `24 passed` |
| Tenant isolation & cross-company access | `tenant or cross_company or cross-company` | `5 passed` |
| Credential lifecycle (missing, malformed, invalid, tampered, revoked, expired, inactive) | `expired or revoked or inactive or invalid or tampered or malformed or missing` | `47 passed` |
| Upload boundary | `oversized or too_large or bounded or upload` | `127 passed` |
| Forwarded-header / proxy trust | `forwarded or proxy or trusted` | `24 passed` |
| OCR end-to-end | `ocr` | `4 passed` |

### 1.5 Continuous Integration — `Verified`

Latest tested code head: `2de2ae7e8aca1fa522b4e4a1f1210f18f86bc838`. GitHub Actions run [40382194997](https://github.com/Devmraustro/ERP/actions/runs/40382194997) completed successfully with all three jobs green. The subsequent commit `6bcb859f43fd9da8c6a34acf5eccadf2f73201e1` only refreshes this QA evidence document; no application or test code changed in that documentation commit.

| Job | Exact tested-code result |
|---|---|
| Linux Python 3.11 full suite, coverage, staging and risk-register check | **success** — 620 passed, 3 skipped, 0 failed; 88% coverage; staging 37/37; 31-row risk-register summary consistent |
| Linux Python 3.12 full suite and static checks | **success** — 620 passed, 3 skipped, 0 failed; 88% coverage |
| Windows key rotation, migration, backup/restore and staging | **success** — 148 passed, 8 platform-specific skipped, 0 failed; staging 37/37 |

This evidence applies to the exact tested code head above. The documentation-only follow-up is not represented as having a separate CI result until a run for that new commit is observed. Release verdict remains **CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY**.
## 2. Detailed Test Matrix (`Passed`)

### 2.1 Phase 2 — Algerian SCF Localization & Upstream `azinak/l10n-dz-account-fix` Suite (`tests/test_phase2_localization.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 1 | `TestAlgerianChartTemplateFixes::test_default_receivable_account` | Template default receivable maps to `l10n_dz_411` (`411`) instead of `l10n_dz_413` (`413`) | `Passed` |
| 2 | `TestAlgerianChartTemplateFixes::test_reconciliation_accounts` | Reconciliation enabled on `409`, `419`, `421`, `425` | `Passed` |
| 3 | `TestAlgerianChartTemplateFixes::test_verified_account_types` | All 24 misclassified SCF accounts mapped to correct types | `Passed` |
| 4 | `TestAlgerianChartTemplateIntegration::test_account_identity_and_code_are_preserved` | XML IDs and account codes remain intact after fix | `Passed` |
| 5 | `TestAlgerianChartTemplateIntegration::test_actual_account_types` | Instantiated company chart has corrected types across all 24 accounts | `Passed` |
| 6 | `TestAlgerianChartTemplateIntegration::test_actual_default_receivable_is_account_411` | Company `property_account_receivable_id` resolves to `411` | `Passed` |
| 7 | `TestAlgerianChartTemplateIntegration::test_actual_reconciliation_flags` | Instantiated accounts `409`, `419`, `421`, `425` have `reconcile = True` | `Passed` |
| 8 | `TestAlgerianChartTemplateIntegration::test_archived_account_is_corrected_without_reactivation` | Archived account (`active=False`) is updated without reactivation | `Passed` |
| 9 | `TestAlgerianChartTemplateIntegration::test_chart_is_loaded_for_the_company` | Full 294-account Algerian chart loads cleanly | `Passed` |
| 10 | `TestAlgerianChartTemplateIntegration::test_existing_user_customization_is_not_overwritten` | Deliberate user customization on `509` (`equity`) is preserved | `Passed` |
| 11 | `TestAlgerianChartTemplateIntegration::test_migration_corrects_official_existing_values` | Migration updates existing `413` and 24 account types | `Passed` |
| 12 | `TestAlgerianChartTemplateIntegration::test_migration_is_idempotent` | Re-running `post_init_hook` migration produces zero unintended diffs | `Passed` |
| 13 | `TestAlgerianChartTemplateIntegration::test_migration_preserves_custom_receivable_fallback` | Custom receivable account (`4119`) is not overwritten | `Passed` |
| 14 | `TestAlgerianChartTemplateIntegration::test_migration_preserves_posted_entry_and_balances` | Posted journal entry on `519`/`411` remains `posted` and balanced | `Passed` |
| 15 | `TestAlgerianChartTemplateIntegration::test_migration_updates_official_receivable_fallback` | Legacy `413` receivable fallback is updated to `411` | `Passed` |
| 16 | `TestAlgerianChartTemplateIntegration::test_non_algerian_company_is_not_modified` | Non-DZ company (`US` / `generic_coa`) is untouched | `Passed` |
| 17 | `TestAlgerianChartTemplateIntegration::test_non_cash_accounts_do_not_create_bank_journals` | Accounts `501`–`509`, `519`, `521`, `529`, `591`, `594` do not create bank journals | `Passed` |
| 18 | `TestAlgerianChartTemplateIntegration::test_reload_is_idempotent` | Reloading chart template is idempotent | `Passed` |
| 19 | `test_upstream_odoo_18_defects_verified_from_source` | Direct inspection of vendored `odoo/odoo` `18.0` files confirms upstream bugs and 69 Wilayas | `Passed` |
| 20 | `test_conservative_migration_preserves_customizations_posted_entries_and_archived_state` | SQL migration preserves custom `509`, archived `594`, posted `413` JE, and US company | `Passed` |
| 21 | `test_decimal_precision_timbre_fiscal_and_g50_workbook` | Binary `float` rejected; graduated *Droit de Timbre* and G50 preparation workbook verified | `Passed` |

### 2.2 Phase 3 — Supplier Invoice Intake & OCR (`tests/test_phase3_invoice_intake.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 22 | `test_document_intake_security_malware_and_format_validation` | MIME/magic-byte mismatch rejected; EICAR malware and active PDF `/JavaScript` quarantined (`Rejected`) | `Passed` |
| 23 | `test_ocr_extraction_raw_immutability_prompt_injection_and_zero_stock_movement` | PDF & `RapidOCR` extraction, SHA-256 duplicate block, human override preserving `raw_text` (trigger-enforced), prompt-injection warning, and zero stock moves | `Passed` |

### 2.3 Phase 4 — Procurement, Stock & Three-Way Matching (`tests/test_phase4_procurement_3way_match.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 24 | `test_po_partial_receipt_backorder_3way_match_and_no_double_stock_movement` | Invoice before receipt held (`NeedsReview`); partial GRN (`60/100`) + backorder (`40`); 3-way match; `380` clearing reconciled; zero stock move on invoice post; over-billed 2nd invoice blocked | `Passed` |
| 25 | `test_duplicate_detection_price_tax_mismatch_idempotent_retry_and_reversal` | Math/price mismatch holds; idempotent post replay; normalized (`FAC 2026-0089`) & fuzzy (`FAC-2026-008B`) duplicate detection; credit note reversal (`AVOIR`) preserving original JE | `Passed` |
| 26 | `test_concurrent_stock_updates_never_go_negative` | 5 threads competing to deduct 4 units from 10 available $\rightarrow$ 2 succeed, 3 fail with `INSUFFICIENT_STOCK_ERROR`, final stock `2.000000` | `Passed` |

### 2.4 Phase 5 — Security, RBAC, Audit Immutability & Backup/Restore (`tests/test_phase5_security_audit_backup.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 27 | `test_rbac_sod_approval_invalidation_and_api_bypass_protection` | SoD self-approval block (`SEGREGATION_OF_DUTIES_VIOLATION`), unauthorized role block, cross-company data/vault block, FastAPI `TestClient` bypass blocks (`401`/`403`), post-approval edit invalidation | `Passed` |
| 28 | `test_audit_immutability_log_redaction_and_encrypted_backup_restore` | SQL triggers block `UPDATE`/`DELETE` on `audit_events`; logger redacts tokens/NIN/IBAN; encrypted backup + isolated restore verifies DB/vault SHA-256, trial balance, and audit chain | `Passed` |

### 2.5 Phase 6 — OCR Benchmark & 14-Step End-to-End Acceptance (`tests/test_phase6_ocr_benchmark_e2e.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 29 | `test_empirical_ocr_benchmark_across_6_algerian_invoice_strata` | Runs 18-document Algerian invoice benchmark across 6 strata (`90.97%` field exact match, `100%` line association, `0.79%` ECE) | `Passed` |
| 30 | `test_complete_14_step_end_to_end_supplier_invoice_workflow` | Executes all 14 mandatory end-to-end steps from upload through human edit, 3-way match, SoD approval, idempotent posting, duplicate block, and isolated backup/restore | `Passed` |

Additionally (19 collected items in the file): the engine must run
single-threaded (`test_ocr_engine_is_configured_for_deterministic_inference`,
`test_ocr_engine_actually_loads_and_recognises_text`); the external-dataset
interface (`load_external_dataset` / `evaluate_external_dataset`,
`docs/OCR_EVALUATION_PROTOCOL.md` §12) refuses manifests without a complete
authorization statement, without ground truth, with escaping/duplicate paths
and unsupported types, and scores an authorized corpus through the real
engine while carrying the authorization into the report; and
`test_benchmark_report_is_reproducible_from_a_clean_output_directory` runs
the benchmark twice from clean directories and requires every non-latency
report key to be identical.

### 2.6 Phase 7 — Authentication Remediation (`tests/test_phase7_authentication_remediation.py`)

Regression suite for the identity-header impersonation vulnerability (`CWE-287` / `CWE-639`)
described in `docs/AUTHENTICATION_HARDENING_SPEC.md`.

> **57 test IDs** (46 test functions; `test_missing_and_malformed_credentials_are_rejected`
> is parametrized over 12 malformed header forms). Rows below are numbered `31`–`71`;
> a single row may cover several closely related test functions.

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 31 | `test_forged_identity_headers_cannot_impersonate_users` | Forged `X-User-Id`/`X-Company-Id` (valid user, cross-tenant user, unknown ID, negative ID) with **no credential** returns `401` on all 9 protected endpoints, with `WWW-Authenticate: Bearer` and no principal leakage | `Passed` |
| 32 | `test_forged_headers_do_not_override_authenticated_principal` | A valid token for `clerk_a` plus forged `X-User-Id: 20`/`X-Company-Id: 2` still resolves to `user_id=1`, `company_id=1`, `role=clerk` | `Passed` |
| 33 | `test_openapi_contract_no_longer_advertises_identity_headers` | OpenAPI declares no `X-User-Id`/`X-Company-Id` parameters; every non-public operation requires `BearerAuth`; `healthz` stays public | `Passed` |
| 34 | `test_missing_and_malformed_credentials_are_rejected` | 12 malformed forms (empty, `Bearer` alone, `Basic`, `Token`, bare token, wrong length) all return `401` with `WWW-Authenticate` | `Passed` |
| 35 | `test_bearer_scheme_is_case_insensitive` | RFC 7235: `bearer <token>` is accepted | `Passed` |
| 36 | `test_unknown_token_is_rejected` | Freshly generated 256-bit tokens are unknown → `401` | `Passed` |
| 37 | `test_tampered_token_is_rejected` | Flipping one base64 character, or a token from another deployment, → `401` | `Passed` |
| 38 | `test_revoked_token_is_rejected` | Token works, is revoked via `POST /api/v1/auth/revoke`, then fails on replay; double revocation is `401` | `Passed` |
| 39 | `test_revoking_all_user_tokens_blocks_every_session` | 3 concurrent sessions all die on bulk revocation | `Passed` |
| 40 | `test_expired_token_is_rejected` | Advancing the clock past the 8-hour TTL → `401` `EXPIRED_CREDENTIAL` | `Passed` |
| 41 | `test_token_expires_exactly_at_its_boundary` | Valid at `TTL-1s`, rejected at `TTL+1s` | `Passed` |
| 42 | `test_deactivated_user_loses_access_immediately` | `deactivate_user()` revokes the token; subsequent use and fresh sign-in both `401` | `Passed` |
| 43 | `test_inactive_user_with_unrevoked_token_is_rejected_and_audited` | `is_active=0` set out-of-band with the token row untouched → `401` **and** an `AUTH_USER_INACTIVE_REJECTED` audit event survives (chain still valid) | `Passed` |
| 44 | `test_inactive_user_can_never_obtain_a_token` | Deactivated account cannot mint a token; `auth_tokens` stays empty | `Passed` |
| 45 | `test_password_rotation_revokes_live_tokens` | `set_password()` kills live tokens; new password works; old password fails | `Passed` |
| 46 | `test_tenant_isolation_enforced_across_all_protected_endpoints` | Tenant-B admin (valid credential) is refused on every tenant-A read/write path, and tenant-A rows are provably unchanged | `Passed` |
| 47 | `test_tenant_isolation_on_invoice_creation` | A company-2 credential can only create `company_id=2` records | `Passed` |
| 48 | `test_tenant_isolation_rejects_foreign_supplier_reference` | A company-2 credential cannot attach company-1 supplier 10 to its own invoice (`403`) | `Passed` |
| 49 | `test_rbac_enforced_across_all_protected_endpoints` | `warehouse` cannot read invoices; `clerk` cannot post/reverse/approve; `approver` cannot post; `auditor` reads but cannot mutate; SoD still blocks creator self-approval | `Passed` |
| 50 | `test_rbac_denial_is_audited` | A blocked role read produces a durable `SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED` audit event | `Passed` |
| 51 | `test_login_endpoint_issues_usable_bearer_token` | `POST /api/v1/auth/token` returns a `dzsess_`-prefixed token that authenticates | `Passed` |
| 52 | `test_login_rejects_wrong_password` | Wrong/case-variant/empty passwords → `401` with no `access_token` in the body | `Passed` |
| 53 | `test_unknown_username_and_wrong_password_are_indistinguishable` | Identical status and message for unknown user vs. wrong password (no user enumeration) | `Passed` |
| 54 | `test_brute_force_lockout` | 5 failed sign-ins lock the account; the correct password is then refused; other accounts unaffected; unlock after 15 minutes | `Passed` |
| 55 | `test_lockout_is_persisted_in_the_database` | `users.locked_until` is populated | `Passed` |
| 56 | `test_revoke_endpoint_requires_a_valid_credential` | Revocation with no/unknown credential → `401`; revokes exactly the presented token | `Passed` |
| 57 | `test_healthz_is_public` | `/healthz` needs no credential | `Passed` |
| 58 | `test_tokens_are_never_persisted_in_plaintext` | No `auth_tokens` or `users` row contains the plaintext token; only a 64-hex SHA-256 digest and a short non-secret prefix | `Passed` |
| 59 | `test_passwords_are_never_persisted_in_plaintext` | `users.password_hash` never contains the plaintext password and uses the PBKDF2 format | `Passed` |
| 60 | `test_password_hashes_are_salted` | Same password → different hashes; malformed/absent stored hashes are rejected | `Passed` |
| 61 | `test_production_pbkdf2_iteration_floor` | Shipped default is `>= 600,000` iterations (OWASP 2023) | `Passed` |
| 62 | `test_token_entropy_and_format` | 200 tokens are unique; 43-char base64url payload decoding to exactly 32 bytes (256 bits) | `Passed` |
| 63 | `test_parse_bearer_token_unit_contract` | Case-insensitive scheme, whitespace trimming, whitespace-bearing tokens rejected | `Passed` |
| 64 | `test_verify_password_is_constant_time_comparison` | A digest differing by one bit is rejected; the correct one still verifies | `Passed` |
| 65 | `test_corrupted_token_digest_row_is_rejected` | A stored digest that does not match the presented token fails closed (defence in depth) | `Passed` |
| 66 | `test_authenticated_user_is_immutable` | The resolved principal is a frozen dataclass and cannot be mutated by application code | `Passed` |
| 67 | `test_authentication_events_are_audited_and_chain_remains_valid` | `AUTH_TOKEN_ISSUED` / `AUTH_TOKEN_REVOKED` / `AUTH_LOGIN_FAILED` are recorded and the HMAC chain verifies | `Passed` |
| 68 | `test_legacy_database_schema_is_migrated_on_open` | A pre-remediation database (no `password_hash`, no `auth_tokens`) is upgraded on open and authentication then works | `Passed` |
| 69 | `test_reopening_a_migrated_database_is_idempotent` | Re-opening a migrated database does not raise or duplicate rows | `Passed` |
| 70 | `test_purge_expired_tokens` / `test_list_active_tokens_exposes_no_secrets` | Expired rows are purged; token metadata listings contain no secret material | `Passed` |
| 71 | `test_unknown_role_is_refused` / `test_empty_password_is_refused` / `test_negative_ttl_is_refused` / `test_auth_manager_rejects_non_positive_ttl` / `test_revoking_tokens_for_unknown_user_is_a_no_op` | Input validation and no-op safety on unknown users | `Passed` |

---

## 2A. Phase 8 — Upload Boundary Hardening, Forwarded-Header Trust & CI (`tests/test_phase8_upload_boundary_and_ci.py`)

All `129` tests in this module passed (the module is new in Phase 8). The rows below enumerate the `71` distinct test **functions**; the remaining `58` collected cases are the parametrised variants of rows 99, 127, 129 and the `test_read_bounded_upload_*` boundary series.

### 2A.1 Upload size boundary (`dz_erp/upload.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 72 | `test_upload_policy_is_documented_and_configurable` | Policy is machine-readable: limit, chunk size, allowlists, enforcing layer, env override; default is a conservative `<= 32 MiB` | `Passed` |
| 73 | `test_upload_policy_endpoint_is_public_and_reports_limits` | `GET /api/v1/upload-policy` publishes the enforced limit and the fail-closed proxy default without authentication | `Passed` |
| 74 | `test_upload_limit_is_configurable_via_environment` | `ERP_MAX_UPLOAD_BYTES` overrides the default; malformed / zero / negative overrides are ignored so a typo can never disable the limit | `Passed` |
| 75 | `test_read_bounded_upload_valid_file_below_limit` | A body below the limit is returned intact, in 512-byte chunks | `Passed` |
| 76 | `test_read_bounded_upload_exactly_at_limit` | A body of **exactly** the limit is accepted (boundary is inclusive) | `Passed` |
| 77 | `test_read_bounded_upload_one_byte_over_limit` | A body of `limit + 1` bytes raises `UploadTooLargeError` and is **not** returned | `Passed` |
| 78 | `test_read_bounded_upload_stops_reading_early_on_overflow` | Reading stops at the limit — far fewer reads than would be needed to consume a 5 MiB body; nothing is fully materialised | `Passed` |
| 79 | `test_read_bounded_upload_rejects_lying_declared_size` | A declared `Content-Length` of 10 bytes cannot smuggle a `limit + 1` body | `Passed` |
| 80 | `test_read_bounded_upload_declared_size_over_limit_is_rejected_before_reading` | An over-limit declared size is rejected before **any** byte is read | `Passed` |
| 81 | `test_read_bounded_upload_empty_body_is_accepted_as_empty` | An empty body is accepted as empty (no false rejection) | `Passed` |
| 82 | `test_read_bounded_upload_rejects_non_positive_limit` | `max_bytes` of `0` or `-1` raises `ValueError` | `Passed` |
| 83 | `test_upload_valid_file_below_limit_is_accepted` | A valid invoice uploads and the stored `byte_size` equals the sent size | `Passed` |
| 84 | `test_upload_exactly_at_limit_is_accepted` | An HTTP upload of exactly the configured limit succeeds | `Passed` |
| 85 | `test_upload_one_byte_over_limit_returns_413` | An HTTP upload of `limit + 1` bytes returns `413` with the limit named in the message | `Passed` |
| 86 | `test_oversized_upload_creates_no_document_record` | After a `413`: `source_documents`, `ocr_extracted_fields`, `supplier_invoices` unchanged and **no** orphaned vault file | `Passed` |
| 87 | `test_oversized_upload_does_not_trigger_ocr` | No OCR work is performed for a rejected body | `Passed` |
| 88 | `test_oversized_upload_is_rejected_before_authentication_is_bypassed` | An oversized body with **no** credential still yields `401`, never `413` — the size check cannot bypass authentication | `Passed` |
| 89 | `test_oversized_upload_streamed_body_exceeds_declared_size` | A generator-backed multipart body (no `Content-Length`) over the limit → `413`, no records | `Passed` |
| 90 | `test_repeated_oversized_uploads_leave_no_state` | 5 consecutive oversized uploads all `413`; state stays clean; a legitimate upload then still succeeds | `Passed` |
| 91 | `test_concurrent_oversized_uploads_are_all_rejected` | 12 concurrent oversized uploads (6 workers) **all** return `413`; no partial state | `Passed` |
| 92 | `test_upload_rejection_closes_the_uploaded_file` | The `finally` block closes the upload even though the read raised | `Passed` |
| 93 | `test_close_upload_is_finally_safe` | `close_upload` is idempotent and tolerates `None` / objects without `close()` | `Passed` |
| 94 | `test_close_upload_never_propagates_a_close_failure` | A failure while releasing the upload never surfaces, so it cannot mask the original error | `Passed` |
| 95 | `test_close_upload_propagates_the_callers_original_error` | The error that triggered the cleanup still reaches the caller | `Passed` |
| 96 | `test_cleanup_partial_upload_removes_files` | A partial artifact is removed; the call is idempotent and safe for a missing path / `None` | `Passed` |
| 97 | `test_upload_file_is_closed_helper` | `upload_file_is_closed` reflects the backing stream state | `Passed` |

### 2A.2 File-type validation (never trust the filename or the declared MIME alone)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 98 | `test_upload_metadata_accepts_allowed_types` | Allowed extension/MIME pairs, including case variants and `; charset=` parameters, are normalised correctly | `Passed` |
| 99 | `test_upload_metadata_rejects_disallowed_types` | Disallowed extensions/MIME types, missing filename, missing Content-Type, empty strings, and **path-traversal / NUL / Windows-separator filenames** are rejected | `Passed` |
| 100 | `test_upload_metadata_rejects_extension_mime_mismatch` | A `.png` filename claiming `application/pdf` is rejected | `Passed` |
| 101 | `test_extension_of_is_case_insensitive` | Extension extraction is case-insensitive and safe for `None` / empty | `Passed` |
| 102 | `test_sniff_mime_type_detects_from_content` | Magic-byte sniffing identifies PDF / PNG / JPEG and returns `None` for `MZ`, shell scripts and empty content | `Passed` |
| 103 | `test_validate_content_signature_rejects_label_content_mismatch` | PDF bytes labelled `image/png` are rejected | `Passed` |
| 104 | `test_validate_content_signature_rejects_unknown_content` | Content matching no supported signature is rejected | `Passed` |
| 105 | `test_upload_endpoint_rejects_exe_with_pdf_name` | A renamed `MZ` executable with a `.pdf` name → `400`/`415`, no record | `Passed` |
| 106 | `test_upload_endpoint_rejects_unsupported_extension` | `payload.exe` → `415` | `Passed` |
| 107 | `test_upload_endpoint_rejects_unsupported_declared_mime` | A `.pdf` claiming `application/zip` → `415` | `Passed` |
| 108 | `test_upload_endpoint_rejects_png_bytes_labelled_pdf` | PNG bytes labelled `application/pdf` → `415` (content beats the label) | `Passed` |
| 109 | `test_upload_endpoint_rejects_pdf_bytes_labelled_png` | PDF bytes labelled `image/png` → `415` | `Passed` |
| 110 | `test_upload_endpoint_accepts_valid_png` | A genuine PNG uploads successfully | `Passed` |
| 111 | `test_upload_quarantine_is_preserved_for_malware` | The pre-existing EICAR quarantine behaviour is unchanged: `security_status == QUARANTINED` | `Passed` |

### 2A.3 Reproducible environment — no hidden host dependencies

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 141 | `test_benchmark_font_is_bundled_and_resolves_without_the_system_font` | The OCR benchmark font is vendored in-repo and wins even when the system font path does not exist | `Passed` |
| 142 | `test_benchmark_rendering_is_reproducible` | The same spec renders to the same extracted text and byte-identical raster image, so measured OCR accuracy is a property of the code, not of the host | `Passed` |

### 2A.4 Forwarded-header / proxy trust (`dz_erp/net.py`)

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 112 | `test_forwarded_headers_are_ignored_by_default` | With no trusted proxies, a well-formed `X-Forwarded-For` chain is **discarded** and the direct peer is used | `Passed` |
| 113 | `test_forwarded_headers_honoured_only_from_trusted_proxy` | The chain is honoured only when the peer is inside a configured CIDR | `Passed` |
| 114 | `test_forwarded_chain_uses_rightmost_untrusted_address` | The right-most **untrusted** entry wins — what the trusted proxy actually observed | `Passed` |
| 115 | `test_forwarded_header_cannot_be_forged_by_prepending` | Prepending a fake entry does not win; the right-most untrusted address is used | `Passed` |
| 116 | `test_parse_forwarded_for_handles_malformed_input` | Absent, empty, whitespace, duplicate commas, ports, IPv6 brackets, and non-IP garbage are handled without raising | `Passed` |
| 117 | `test_trusted_proxy_policy_ignores_malformed_configuration` | Malformed CIDR entries in configuration are dropped rather than failing open | `Passed` |
| 118 | `test_trusted_proxy_policy_supports_bare_addresses` | A bare address is accepted as a single-host range | `Passed` |
| 119 | `test_resolve_client_ip_handles_missing_peer` | A missing peer address yields `unknown` and never invents a value | `Passed` |
| 120 | `test_trusted_proxy_policy_from_env` | `ERP_TRUSTED_PROXIES` (comma/space separated) configures the policy | `Passed` |
| 121 | `test_describe_trusted_proxy_policy_reports_default` | The policy self-describes as fail-closed by default | `Passed` |
| 122 | `test_spoofed_forwarded_header_does_not_change_recorded_session_ip` | A spoofed header does not reach the stored `issued_to_ip` | `Passed` |
| 123 | `test_spoofed_forwarded_header_with_trusted_proxy_is_rejected_from_untrusted_peer` | Even with a trusted-proxy policy, a caller whose peer is **not** that proxy cannot inject a client IP | `Passed` |
| 124 | `test_trusted_proxy_peer_does_not_invent_a_client_address` | A chain containing only the trusted peer itself resolves to that peer — no invented address | `Passed` |
| 125 | `test_forwarded_header_is_never_used_for_authorisation` | Forged `X-Forwarded-For` / `X-Real-IP` never change the resolved `user_id`, `company_id` or `role` | `Passed` |
| 126 | `test_proxy_policy_env_configuration_is_fail_closed` | With no `ERP_TRUSTED_PROXIES` set, the policy trusts nothing | `Passed` |

### 2A.5 Authentication regression re-verified against the current commit

| # | Test Identifier | Requirement Verified | Status |
| :---: | :--- | :--- | :---: |
| 127 | `test_forged_identity_headers_cannot_authenticate` (parametrised × 6 header sets × 3 paths = 18 cases) | Forged `X-User-Id` / `X-Company-Id` combinations (own user, other user, cross-tenant, non-existent, negative) never authenticate — always `401` with a `WWW-Authenticate: Bearer` challenge | `Passed` |
| 128 | `test_forged_headers_cannot_switch_company_for_authenticated_user` | A valid company-1 clerk presenting company-2 headers is still resolved as company-1 | `Passed` |
| 129 | `test_missing_and_malformed_credentials_rejected` (parametrised × 9) | Absent, empty, whitespace, scheme-only, `Basic`, `Token`, bare and bogus `dzsess_` values all → `401` | `Passed` |
| 130 | `test_invalid_token_is_rejected` | Freshly generated, never-issued tokens → `401` | `Passed` |
| 131 | `test_tampered_token_is_rejected` | A token with a single flipped character → `401` | `Passed` |
| 132 | `test_revoked_token_is_rejected` | Revocation takes effect immediately and survives replay | `Passed` |
| 133 | `test_expired_token_is_rejected` | A token whose stored expiry is moved into the past → `401` | `Passed` |
| 134 | `test_inactive_user_token_is_rejected` | Deactivating the user out-of-band invalidates a live token | `Passed` |
| 135 | `test_inactive_user_cannot_obtain_a_token` | A deactivated account cannot mint a token | `Passed` |
| 136 | `test_cross_company_access_is_denied_on_invoice_read` | Company-2 admin is refused on a company-1 invoice **and** its source document | `Passed` |
| 137 | `test_cross_company_write_operations_are_denied` | Company-2 admin cannot submit or decide approval on a company-1 invoice | `Passed` |
| 138 | `test_unauthorized_financial_operation_is_denied` | A `clerk` cannot post a supplier invoice → `403` | `Passed` |
| 139 | `test_audit_chain_remains_valid_after_phase8_activity` | The HMAC audit chain still verifies after uploads and revocation | `Passed` |
| 140 | `test_all_protected_routes_use_the_authenticated_path` | **Every** non-public route in the OpenAPI contract rejects an unauthenticated request | `Passed` |

### 2A.6 Neural-OCR assertions

`tests/test_phase3_invoice_intake.py` asserts an **exact** value on the PDF path,
because `pdfplumber` reads a text layer and is deterministic. It asserts a
**confusion-tolerant** value on the PNG path, because that path is neural
inference on a rasterised image: ONNX Runtime, OpenCV and FreeType all dispatch
SIMD kernels by host CPU feature, so a borderline glyph can legitimately be read
differently on another machine. The comparison folds the classic OCR digit/letter
confusions (`O`/`0`, `I`/`1`, `L`/`1`, `Z`/`2`, `S`/`5`, `B`/`8`, `G`/`6`,
`T`/`7`), then removes whitespace and hyphens so a line break in the middle of a
reference still counts as a recognition.

Recognising the number is asserted against the **raw OCR output**, not the parsed
field: the parser applies a per-line regular expression, and the neural
detector's line segmentation is not stable across CPUs - the same PNG yields
`FACTUREN:FAC-2026-0778` plus a separate `DATE:...` line on one machine and a
differently segmented set of lines on another, which changes whether that one
regex matches. The number is recognised either way, and that is the property
worth asserting. The failure message reports the recognised text.

Verified against a table of detector segmentations rather than assumed: exact
matches, single/double/mixed confusions, lower-case, dropped hyphens, dropped
spaces, and the number split across two detector lines are all accepted;
genuinely wrong digits, off-by-one values, unrelated references and empty results
are all rejected.

### 2A.7 Static check

`ruff check dz_erp tests tools` with the ruleset declared in `pyproject.toml`
(`F`, `E741`, `RUF100`) reports **All checks passed!**. Introducing this check
surfaced and fixed one genuine latent defect that the passing test suite had
been hiding: `test_tenant_isolation_enforced_across_all_protected_endpoints`
referenced a `doc_id` that was never bound in its own scope and only resolved
through test-order pollution.

---

## 2B. Later phases — provenance, integrity, checkpoints, routing, document consistency

These suites were added after the phases above. Each one exists because a claim in the
documentation could previously not be checked by running anything.

| Suite | Tests | What it pins |
| :--- | ---: | :--- |
| `tests/test_phase18_ocr_confidence_provenance.py` | 19 | The removed `0.995` constant; `confidence: null` + `not_measured` for native text; `engine_line` scope for engine scores; `confidence_calibrated` hard-coded `False`; the `IS NOT` trigger comparison; legacy-row relabelling. Reinstating the constant fails exactly two tests. |
| `tests/test_phase19_dependency_integrity.py` | 25 | Hash lockfile ↔ version lockfile agreement; `--require-hashes` install step in both CI jobs; the validator rejecting wrong version, missing package, unsatisfied specifier, unparseable requirement and non-allow-listed substitution; per-line trailing backslash so pip enforces **every** digest, not just the first; the offline-wheelhouse parser (continuation joining, missing-hash refusal, unparseable entry, empty lockfile) and verifier (accept pinned, reject tampered/mismatched/missing, flag unexpected non-artifact files **and nested directories**, refuse a digest recorded for two pins, accept legitimate multi-platform duplicates) and hash-enforced build command; the documented offline install carries `--no-index --find-links --require-hashes`. |
| `tests/test_phase20_checkpoints_and_monitoring.py` | 52 | `validate_checkpoint` rejecting empty / partial / wrong-format / wrong-schema / negative / malformed-hash documents that previously reported `valid: true`; truncation and rewritten-head detection; export refused from an unverifiable ledger or into the ledger's own directory; monitor exit codes `0/2/3`; read-only behaviour (no key material written, none printed); `ops/` units parsed with real systemd continuation semantics. |
| `tests/test_phase21_arabic_review_routing.py` | 19 | `ARABIC_RECOGNITION_HEAD_AVAILABLE = False`; native Arabic keeps `standard_review`; declared or dominant Arabic scans route to `manual_transcription`; a scan yielding nothing is not reported as a parsing success; route/reason persisted on `source_documents` and in the audit event; API `declared_language` round trip; schema rejects an out-of-vocabulary route; migration is idempotent and leaves old rows `NULL`. |
| `tests/test_phase22_register_and_docs_consistency.py` | 15 | The risk-register summary equals the derived counts; identifiers unique; every cited file resolves; runbook names existing units and flags; exit-code table matches the CLI; the mechanisms behind the mitigated rows still exist in code; the key-provider contract document describes a surface the code really has (describe() keys, exceptions, env vars, no hardware-custody claim), including the fail-closed `ERP_KEY_PROVIDER` gate and the outage-behaviour section; the release-readiness matrix is machine-checked (unique IDs, required coverage, fixed status vocabulary, blocked rows name their external dependency, cited paths exist) and the readiness checker cites only existing matrix rows. |
| `tests/test_phase23_staging_acceptance.py` | 1 | Runs `tools/staging_acceptance.py` end to end and requires the full 37-check functional acceptance to pass. |
| `tests/test_phase24_operational_readiness.py` | 20 | The deployment readiness checker: complete configuration passes; missing/invalid paths, enabled API docs and non-loopback binds fail with named remediations; exactly-one key source per key; checkpoint existence/shape/freshness; verification commands listed but executed only on request, with exit codes folded into the result; exit codes 0/1/2; external-evidence items reported as required, never as satisfied; the POSIX permission gate refuses peer-readable key files (fixture key files are `0600`; the gate's fail direction is pinned cross-platform); Windows custody honestly warns about ACLs. |

### 2B.1 Defects these tests caught in their own implementation

Each is worth recording because in each case the code *looked* right:

1. **The hash lockfile enforced one digest per package.** `pip` treats a `--hash` line without a
   trailing backslash as the end of the requirement, so ~1250 lines were discarded as "has
   `--hash` but no requirement" and a second artefact for `cffi` failed the install. Fixed by
   joining the digest lines with a trailing backslash and a newline; a structural test now
   asserts every continuation line ends in a backslash and that pip's own parser reports
   **zero** warnings.
2. **Monitoring regenerated a lost ledger key and then blamed the operator.** `RotationLedger.open`
   defaults to `create=True`, so a monitor run with the keyring missing generated a fresh key and
   reported `RECORD_TAMPERED` — a false accusation of deliberate modification, plus key material
   written during a read-only report. Fixed with `create=False` on every read-only path and a
   distinct `LEDGER_KEY_UNAVAILABLE` reason. A monitor that "repairs" what it measures is worse
   than one that fails.

A third, smaller one: the checkpointer's own unit files were parsed by a naive test helper that
ignored systemd's backslash continuation rule, which made a valid unit look broken. The helper now
joins continuations the way systemd does, and a test asserts that behaviour directly.

3. **The same bug existed a second time, in the vault path.** Read-only monitoring did not only
   recreate a missing *ledger* key; it also constructed `PrivateDocumentVault`, which mints a key on
   first use. Pointing `monitor --vault` at a mistyped path therefore created a directory tree, a
   fresh `vault.vault.key`, and a healthy-looking v1 deployment — and pointing it at a real but
   unconfigured directory did the same. `_require_existing_vault` now refuses unless a key, a version
   registry or `ERP_VAULT_KEY` exists, and reports `VAULT_NOT_CONFIGURED` with exit `3`. The general
   lesson recorded here: a read-only command must be proved read-only by *asserting the absence of
   side effects*, not by reading its own docstring - which is what
   `test_monitor_does_not_initialise_a_missing_deployment` does.

4. **The CI failure that proved the fixture, not the checker, was wrong.** CI run #103
   (`40370500808`, on `ccc874b`) failed both Linux jobs at the test-suite step: every
   "complete configuration is ready" test failed because the checker refused the key files.
   Cause: `_good_env` wrote the key files with `write_bytes` and no `chmod`, so under the
   runners' umask they landed `0644` — and the checker's POSIX permission gate, doing exactly
   its job, refused them (`mode & 0o077`). On Windows that gate only warns, which is why every
   local run had passed: the checker was right and the fixture was incomplete on POSIX. Fixed
   by writing key files `0600` (vault directory `0700` on POSIX) and adding
   `test_posix_permission_gate_refuses_peer_readable_key_files`, which pins the gate's fail
   direction cross-platform (a posix-declaring shim of the checker module's `os` binding —
   patching `os.name` globally breaks `pathlib` on Windows). The lesson: a fixture must be
   held to the same discipline the assertions enforce, and "passes locally" is not "passes on
   POSIX" while a check is platform-conditional.

### 2B.2 Mutation check (each protection removed, the matching test expected to fail)

Performed on a scratch copy, restoring each file byte-for-byte afterwards (verified with `cmp`):

| Mutation | Test outcome |
| :--- | :--- |
| `_verify_ledger_without_creating_keys` → `create=True` | 3 failed — missing-key refusal, no-replacement-key, key-loss-not-tampering |
| `LEDGER_KEY_UNAVAILABLE` → `RECORD_TAMPERED` for a lost key | 2 failed |
| Tail-truncation comparison disabled | 3 failed — phase 20 truncation, CLI truncation, phase 14 truncation |
| `validate_checkpoint` made a no-op | 13 failed — every malformed-document case |
| Off-host checkpoint refusal disabled | 2 unit failures **and staging acceptance exit 1 (36/37)** |
| Unknown directive added to a unit file | `test_units_pass_systemd_analyze_verify` failed |
| Declared-Arabic scan routed to `standard_review` | 5 failed, including the API round trip |
| `ARABIC_RECOGNITION_HEAD_AVAILABLE` flipped to `True` | 4 failed |
| Monitor re-initialises a missing deployment | 3 failed |

One mutation initially **passed when it should have failed**: the systemd test filtered out
`Unknown key` lines, which is precisely the defect a typo'd directive produces. The assertion was
corrected to treat those lines as failures, and re-running the mutation then failed as required.
That is a test defect, not an application defect, and it is recorded rather than quietly fixed.
