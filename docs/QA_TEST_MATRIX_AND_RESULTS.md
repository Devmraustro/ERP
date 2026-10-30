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

## 2C. Phase 25 — read-only listing & reporting API for installed clients (`tests/test_phase25_read_api.py`) — `Passed`

The desktop client (§2D) presents data through these endpoints instead of ad-hoc SQL, so the
controls below are the client's controls too. Locally: **6 passed in 3.84 s** (Python 3.12.10),
ruff clean.

| Test | What it proves |
| :--- | :--- |
| `test_read_endpoints_require_authentication` | All five new endpoints (`/api/v1/accounts`, `/api/v1/products`, `/api/v1/documents`, `/api/v1/invoices`, `/api/v1/accounting/trial-balance`) answer **401** without a server-issued token. |
| `test_reference_lists_are_tenant_scoped` | Two tenants each see only their own accounts/products/documents (company scope is derived from the verified credential, never a caller argument). The document list **excludes `vault_path` and `file_sha256`** — custody facts are not list-view data. |
| `test_unscoped_reads_are_refused_at_the_data_layer` | A `None` company scope raises `TenantIsolationError` for every read model — a missing scope is a bug, not an empty result. |
| `test_financial_lists_refuse_warehouse_role_with_audited_denial` | The warehouse role gets **403** on the invoice list and the trial balance, and each refusal is recorded in the append-only audit log as `SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED` (targets `("supplier_invoices","list")` and `("journal_entries","trial_balance")`), raised **after commit** so the denial survives. A list view is never a bypass of the detail view. |
| `test_invoice_list_and_trial_balance_scoped_balanced_and_posted_only` | Full verified lifecycle (create → submit → approve → post): the posted invoice is visible to its company only; the trial balance accumulates in exact `Decimal` arithmetic over **POSTED entries only** (a balanced 500.00 DZD DRAFT entry is excluded), reports `11900.00` = `11900.00` (balanced), and an empty ledger reports `0.00` for the other tenant. |
| `test_list_limit_is_clamped` | `limit=0`, `limit=501` and `limit=abc` → **422**; `limit=1` returns exactly one row. Limits are clamped server-side (1–500). |

## 2D. Phase 26 — installed desktop client (`tests/test_phase26_desktop.py`) — `Passed`

The product direction becomes an installed, local-first **desktop** application (Mode A: single
workstation with an embedded loopback service; Mode B: LAN client to a company server). The
desktop layer is split into Qt-free modules (config, embedded service, API client, i18n — testable
headless and on CI) and a PySide6 UI layer that is an **optional** extra (`pip install
algerian-ai-erp[desktop]`, PySide6==6.11.2, LGPL-3.0). Design rules with regression tests:
the client never opens the SQLite file; the embedded service is built by the same
`serve.build_app` as a deployment and binds `127.0.0.1:0` only; the session token is memory-only;
the persisted config is a secret-free whitelist.

| Test | What it proves |
| :--- | :--- |
| `test_config_persists_whitelist_only` | `config.json` contains exactly the whitelisted path/preference fields; no password/token field can persist; round-trip save → load preserves values. |
| `test_corrupt_config_recovers_to_defaults` | A damaged config file is moved aside (kept for forensics, `config.json.corrupt-<pid>`) and defaults are restored — preferences never take accounting data down with them. |
| `test_config_validation_refuses_bad_values` | Unknown language, `mode=server` without `server_url`, non-HTTP(S) URL → `ValueError`; defaults validate. |
| `test_i18n_covers_both_languages_and_rtl` | FR and AR string tables are populated; `is_rtl("ar")` is true; missing keys fall back to the key, unknown languages fall back to French. |
| `test_desktop_package_version_is_declared` | Package identity (`AlgerianERP`, `0.1.0`). |
| `test_embedded_service_refuses_non_loopback_bind` | `host="0.0.0.0"` → `ValueError` ("loopback interface only"); a LAN-visible bind from the embedded service is a design violation, never a warning. |
| `test_first_run_refuses_existing_installation` | Re-running first-run against a database that already has users raises and **changes nothing** — an "attacker" username cannot overwrite an existing admin. |
| `test_mode_a_full_cycle_start_login_read_stop` | The real Mode A loop: bootstrap (company + admin, audit keys minted beside the DB) → embedded service on an **ephemeral** loopback port → `/healthz`-gated start → unauthenticated call refused locally → wrong password → **HTTP 401** → login → session, the seeded official SCF chart (294 accounts), empty products/documents/invoices lists, balanced `0.00` trial balance → logout (server-side revoke) → stop. The `ERP_SECURITY_LOG_PATH` environment variable is restored after app construction (sentinel asserted), and `service.port` raises after stop. |
| `test_smoke_command_full_cycle` | `python -m dz_erp.desktop --smoke --data-dir …` as a **subprocess**: exit 0, JSON payload with status/user/port/base_url, and the user-provided data dir survives as evidence. |
| `test_mainwindow_constructs_and_switches_to_rtl` | (requires PySide6, run with `QT_QPA_PLATFORM=offscreen`) The real `MainWindow` constructs against a live embedded service (8 navigation views), and switching to Arabic flips the application to `RightToLeft`, back to French to `LeftToRight`. |

Headless Mode A smoke (also the CI-runnable proxy for "the installed app starts"), real output on
this workstation (Python 3.12.10, exit code 0, re-measured at the Phase 27 head after the SCF
first-run seeding landed — the port and temp path vary per run):

```json
{
  "status": "ok",
  "version": "0.1.0",
  "mode": "local",
  "base_url": "http://127.0.0.1:62609",
  "port": 62609,
  "data_dir": "C:\\Users\\rabdz\\AppData\\Local\\Temp\\dz-erp-smoke-a4t13gun",
  "user": "admin",
  "role": "admin",
  "accounts": 294,
  "products": 0,
  "documents": 0,
  "invoices": 0,
  "trial_balance_rows": 0,
  "total_debit_dzd": "0.00"
}
```

`"accounts": 294` is the official SCF chart seeded at first run (§2E) — a fresh install now starts
with the complete Algerian chart of accounts rather than an empty one; products/documents/invoices
start empty and fill through real workflows.

Honest gaps (also in `docs/DESKTOP_APPLICATION.md` §8.1): the empty-chart gap of Phase 26 is closed
(§2E). Still not wired into the UI: user administration screens, multi-line invoice editing and
purchase-order/GRN matching screens (the verified API contract supports them). Arabic OCR accuracy
is not claimed — extraction fields carry honest measured confidence (or "not measured") and
uncertain fields are routed to manual review. Packaging remains build-script + SHA-256 sidecar; an
unsigned-installer distribution is not claimed as solved, and the packaged smoke run is blocked on
this host by application-control policy (documented in `docs/DESKTOP_APPLICATION.md` §8.2).

**Full suite at the Phase 26 head** (`99fc41e`, Windows/CPython 3.12.10, clean re-run after the two
contract-pin fixes below): 620 passed, 20 skipped, 0 failed, exit 0 (36 min 48 s; the single
warning is the benign third-party starlette/anyio `DeprecationWarning`). Coverage **85 %** (5,485
statements, 804 missed): the statement count grew by ~910 with the desktop layer, and the drop from
the 87 % Phase-24 baseline comes entirely from the two new GUI-heavy modules measured headless —
`desktop/ui.py` (58 %; widget bodies execute only in interactive Qt) and `desktop/app.py`
(39 %; GUI orchestration) — while the modified core modules keep their prior profiles
(`database.py` 93 %, `api.py` 83 %). The first run of this phase had exactly two failures, both
*contract-pin tests updated by the new surface*, never product behaviour:
`test_pyproject_declarations_are_all_pinned_in_the_lockfile` (the lock test predated optional
extras; re-scoped to the core `dependencies` array **plus a new stronger test** asserting both
extras are exact-pinned and PySide6 stays out of the headless lockfile) and
`test_openapi_contract_declares_exactly_the_public_operations` (protected-operation count pinned
at 14 → 19 for the five new authenticated read endpoints; the public set is unchanged). Both
re-verified green with their files (202 passed) and in the clean full run above. CI for this
exact head is pending the authorized push (the workflow triggers on `pull_request`), which is
reported, not assumed. The Phase 27 full-suite re-measurement on the new head is recorded in §2E.

## 2E. Phase 27 — SCF first-run seeding, review/navigation API and desktop review UI — `Passed`

Phase 27 turns the Phase 26 shell into a usable local-first ERP workflow: (A) first run seeds the
**official SCF chart of accounts** (vendored Odoo 18.0 `l10n_dz`, MPL-2.0 — never an invented
substitute) with exact validation and fail-closed missing-source handling; (B) four authenticated
read endpoints for the review screens (`/api/v1/partners`, `/api/v1/stock`,
`/api/v1/documents/{id}/fields`, `/api/v1/documents/{id}/content`) plus the human field-correction
PATCH already verified in Phase 12; (C) the desktop UI wires suppliers/stock views, an invoice
review dialog (fields with honest confidence display, correction, explicit submit → approve → post)
and an invoice creation form; (D) this matrix. Locally (Python 3.12.10, ruff clean):

- `tests/test_phase27_scf_first_run.py` — **8 passed in 10.67 s**
- `tests/test_phase27_review_api.py` — **5 passed** (with the Phase 25 file: 18 passed in 14.72 s)
- `tests/test_phase27_desktop_ui.py` — **4 passed** (with the Phase 26 file: 14 passed in 52.04 s)
- Phase 8 contract tests after the OpenAPI pin update — 46 passed (`-k "openapi or forged"`)

### A. SCF chart initialization at first run (`tests/test_phase27_scf_first_run.py`)

| Test | What it proves |
| :--- | :--- |
| `test_first_run_seeds_official_chart_exactly` | `bootstrap_first_run` loads the vendored official chart and the result is validated **exactly**: `accounts_loaded == len(accounts_by_code)` (**294**), key accounts **401/411/380/642** present, company defaults **411/401**, exactly **8** standard taxes, timbre rule **`LF-2025-CASH-TIMBRE`**, one `LOAD_CHART_TEMPLATE` audit event with `actor_user_id IS NULL`. The vendored CSV already defines 380/642, so the loader's defensive extra inserts are no-ops — the exact-count pin catches any source drift. |
| `test_repeated_first_run_after_success_is_refused` | Re-running first-run against an existing install raises `RuntimeError` ("already contains users") and **changes nothing**: still one company, one user, the full chart. |
| `test_missing_vendor_data_blocks_before_any_write` | With the verified source absent, `SetupBlockerError` fires **before any database write** — no database file exists afterwards — and the remediation (`ERP_VENDOR_DATA_DIR`) is named in the message. Restoring the real vendored source makes the same first run succeed. |
| `test_loader_failure_rolls_back_to_clean_database` | A simulated chart-loader failure is compensated: the loader is atomic (its audit event rolls back with it), so the unpinned company row is deleted and the database is left with zero users/companies/accounts/groups/taxes/timbre rules. The retry then succeeds from a clean state. |
| `test_admin_failure_leaves_resumable_state_and_retry_completes` | A simulated admin-creation failure happens **after** the chart commit audit-pinned the company (append-only chain), so the state is *resumable*: one company, chart present, no users. The retry reuses the **same company id**, re-runs the idempotent chart load and completes with exactly one user. |
| `test_multiple_orphaned_companies_fail_closed` | Two partially initialized companies from interrupted runs → `SetupBlockerError` ("partially initialized companies"); the state is never guessed at. |
| `test_double_chart_load_is_idempotent` | Reloading the official chart duplicates nothing (same exact count, 8 taxes) and both loads are honest, audited facts (2 events); the audit chain verifies valid. |
| `test_vendor_data_dir_resolution_priority` | Resolution priority: explicit `ERP_VENDOR_DATA_DIR` override wins → a frozen build resolves its bundled copy (`sys._MEIPASS/vendor`) → otherwise the checkout's `vendor/` (so a genuinely missing source then fails closed in the pre-flight). This is the frozen-build story that can be unit-tested without executing a frozen exe. |

### B. Review and navigation read API (`tests/test_phase27_review_api.py`)

| Test | What it proves |
| :--- | :--- |
| `test_review_reads_require_authentication` | All four new reads answer **401** without a server-issued token. |
| `test_partners_and_stock_are_tenant_scoped_and_clamped` | Two tenants each see only their own partners/stock (scope from the verified credential); the server clamps `limit` (0 and 10 000 → **422**, including on the fields read — a bad limit is a contract error, never a mistaken 404 for the document); unscoped data-layer reads raise `TenantIsolationError`. |
| `test_document_fields_are_tenant_scoped_and_geometry_free` | OCR field rows carry raw text (immutable), current value, honest confidence (`0.42` measured / `None` → "not measured" kind), human override — and never `bbox_json`. The other tenant's decoy field does not leak; a cross-company read is **403** and audited (`SECURITY_CROSS_COMPANY_READ_BLOCKED`, committed before the raise); an unknown document is **404**, not a cross-company hit. |
| `test_document_content_returns_exact_bytes_and_metadata` | `/documents/{id}/content` returns the **exact stored bytes** with the stored MIME type and original filename; warehouse staff may read documents/partners/stock (their domain) while financial invoice reads stay **403** with the audited `SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED`; every successful content read is audited (`DOCUMENT_FILE_ACCESSED`); cross-company content read → 403 + `SECURITY_CROSS_COMPANY_FILE_ACCESS_BLOCKED`; unknown → 404. |
| `test_field_correction_preserves_raw_and_confidence_and_audits` | The human correction PATCH stores the override while **raw OCR text and confidence are untouched** — the reviewer always sees what OCR originally said; `edited_by_id`/`edited_at` recorded; `OCR_HUMAN_FIELD_EDIT` audited; an unknown field is 404. |

### C. Desktop review UI (`tests/test_phase27_desktop_ui.py`, offscreen Qt against the real embedded service)

| Test | What it proves |
| :--- | :--- |
| `test_new_views_present_and_rtl_switch` | The navigation exposes the **suppliers** and **stock** views with live data from the new endpoints, the dashboard reports all six counts, and the French/Arabic switch still flips `RightToLeft`/`LeftToRight` with the enlarged view set. |
| `test_review_dialog_full_cycle_with_correction_and_audit` | The review dialog loads detail → OCR fields → document state → decrypted preview **off the GUI thread** (one chained worker, the document id coming from the detail); shows the state badge in lockstep with the invoice workflow; displays honest confidence ("non mesurée" for the unmeasured field); the human correction preserves raw OCR and confidence and writes the audit event; explicit submit → approval → posting; the trial balance is balanced with account 401 present; a **duplicate post is refused by the server and surfaced as a readable error** (no silent swallow, no double journal). Segregation of duties is inherited from the server: the admin who created the invoice cannot approve it — approval is done by a separate approver user through the same dialog. |
| `test_review_dialog_surfaces_unauthorized_role` | A warehouse user opening the review dialog gets the server's **403 surfaced as an error** (never a silent empty grid) and the refusal is audited. |
| `test_review_dialog_surfaces_unknown_invoice` | An unknown invoice id surfaces the server's **404** as the same readable error path. |

### D. Contract updates and cross-phase consistency

- OpenAPI protected-operation pin: **19 → 23** (`PROTECTED_GETS` extended with the four new paths;
  the forged-header parametrization now covers them). The public set is unchanged.
- `tests/test_phase26_desktop.py` updated for the seeded chart (return-dict contract, `count ==
  accounts_loaded`, account 401 present) — **10 passed**, and its Mode-A row above now describes
  the seeded chart.
- A latent desktop-client bug found by the UI cycle test and fixed: a caller-supplied header (the
  posting `Idempotency-Key`) collided with the authentication header in `ErpClient._request`;
  headers now merge. Covered by the passing post cycle above.
- A limit-clamp consistency fix found by the review-API test and fixed: the fields read clamps
  through the same single-source helper every read model uses, so a bad limit is a **422** contract
  error — never a mistaken 404 for the document (the wrapper re-clamps as defense in depth).

**Full suite at the Phase 27 head** (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`,
`pytest tests -q --cov=dz_erp --cov-report=term`): **661 passed, 20 skipped, 0 failed, exit 0 in
19 min 18 s**; coverage **85 %** (6,033 statements, 935 missed — up from 5,485 statements at the
Phase 26 head, with the statement growth covered at 85 %). Per module: `api.py` 85 %,
`database.py` 94 %, `security_audit.py` 77 %, `localization.py` 94 %, `desktop/service.py` 87 %,
`desktop/ui.py` 63 % (widget bodies still execute only in interactive Qt; the review/create UI
tests raised it from 58 %), `desktop/app.py` 39 %. The single warning is the benign third-party
starlette/anyio `DeprecationWarning`. An earlier same-day attempt of this exact suite degraded
environmentally (8 h wall clock; the three subprocess-worker tests of
`test_phase9_process_boundary.py` failed with a 600 s `subprocess` budget under system load) —
those three pass in isolation and in file-order sequence with the Phase 27 files ahead of them
(269 passed in 2 min 04 s), and the clean re-run above is the recorded result. No test was
suppressed, weakened or skipped to obtain it. CI for this exact head is pending the authorized
push (the workflow triggers on `pull_request`), which is reported, not assumed.

## 2F. Phase 28 — user administration end to end - `Passed`

Phase 28 closes the #1 documented product gap: **user administration**, implemented end to end at
every boundary — three authenticated endpoints (`GET /api/v1/users`,
`POST /api/v1/users`, `POST /api/v1/users/{user_id}/deactivate`), the secure wrappers that make the
admin role, tenant scope and audit trail server-derived facts (never client claims), and the
desktop users view (creation dialog with role picker, confirmation-gated deactivation) visible only
when the **server-resolved** session role is `admin`. Locally (Python 3.12.10, ruff clean):

- `tests/test_phase28_user_admin.py` — **12 passed in 7.99 s**
- `tests/test_phase28_desktop_users_ui.py` — **7 passed in 26.72 s** (offscreen Qt against the real
  embedded service)
- Focused regression after the OpenAPI/nav changes — `tests/test_phase8_upload_boundary_and_ci.py`
  + `test_phase25_read_api.py` + `test_phase26_desktop.py` + `test_phase27_desktop_ui.py` +
  `test_phase27_review_api.py`: **231 passed in 81.29 s**

### A. User administration API (`tests/test_phase28_user_admin.py`)

| Test | What it proves |
| :--- | :--- |
| `test_user_endpoints_require_authentication` | All three endpoints answer **401** without a server-issued credential. |
| `test_non_admin_is_refused_and_attempt_audited` | Every non-admin attempt (list, create, deactivate) is **403** and writes one `SECURITY_USER_ADMIN_ACCESS_BLOCKED` event (actor = the attempted actor) **committed before the refusal is raised** — the transaction context manager rolls back on an in-scope raise, so the guard raises after the commit; nothing changes server-side. |
| `test_admin_lists_only_own_tenant_and_never_credential_material` | The listing is scoped from the verified credential (tenant Beta's rows never appear in tenant Alpha's list) and the read model exposes only `id/username/role/is_active/branch_id/password_updated_at` — **never `password_hash`/`api_token_hash`**. |
| `test_admin_creates_user_who_can_then_authenticate` | Creation returns the account, it appears in the list as active, the password was really hashed into a **working credential** (fresh token login + session), and `AUTH_USER_CREATED` is audited with the acting admin as actor. |
| `test_create_rejects_duplicate_username` / `_unknown_role` / `_short_password` / `_blank_username` | Uniqueness (schema-enforced) surfaces as **409**; an unknown role and a **7-character** password are **422** at the API boundary (NIST SP 800-63B floor; the only authority — the dialog merely restates it as a hint); a blank username is **422**. |
| `test_deactivation_takes_effect_for_live_and_fresh_sessions` | After deactivation the target's **live token is rejected at the next request** (`authenticate` re-reads `is_active` server-side — no token-revocation step to forget) and fresh login is refused; `AUTH_USER_DEACTIVATED` is audited. |
| `test_admin_cannot_deactivate_self` | Self-deactivation is a **409** conflict. |
| `test_deactivating_an_inactive_user_conflicts` | Deactivating an already-inactive account is a **409** with the account named. |
| `test_cross_tenant_and_unknown_targets_are_conflated` | A foreign-tenant id and a nonexistent id both answer **403 with the identical detail** — no tenant- or id-existence oracle; the foreign clerk remains untouched and authenticated. |

### B. Desktop users UI (`tests/test_phase28_desktop_users_ui.py`, offscreen Qt against the real embedded service)

| Test | What it proves |
| :--- | :--- |
| `test_users_view_visible_for_admin_and_lists_accounts` | The nav row is hidden until `set_session` adopts the **server-resolved** role; for `admin` it becomes visible, the view lists the tenant's accounts (role + active state; no credential material) and the FR/AR labels resolve. |
| `test_users_view_hidden_for_non_admin` | A clerk session keeps the row hidden (hidden, never removed — row→stack-index mapping stays stable for every other view). |
| `test_create_user_via_dialog_then_created_account_logs_in` | The dialog drives the authorized API: the created account appears as active, **logs in over HTTP** with the password typed in the dialog, and `AUTH_USER_CREATED` carries the acting admin as actor. |
| `test_create_dialog_local_validation_makes_no_api_call` | A blank username and a 7-character password are refused locally with readable messages and **no worker started** (no API call). |
| `test_create_dialog_surfaces_server_rejection` | A duplicate username (server 409) is surfaced inside the dialog ("Création impossible… HTTP 409…") — never swallowed, dialog not accepted. |
| `test_deactivation_confirmation_takes_effect_and_is_audited` | Deactivation requires confirmation; afterwards the row shows "désactivé", the target's **live token is rejected at the next request** (401), `AUTH_USER_DEACTIVATED` carries the acting admin as actor, and no error dialog fired. |
| `test_deactivation_without_selection_shows_guidance` | With no row selected, a readable guidance message is shown instead of a silent no-op. |

### C. Contract updates and cross-phase consistency

- OpenAPI protected-operation pin: **23 → 26** (`PROTECTED_GETS` extended with `/api/v1/users`).
  The public set is unchanged; the deny-by-default OpenAPI declaration covered the new endpoints
  automatically.
- `desktop/ui.py` gained the `users` view (nav row + table + create dialog + deactivate button) and
  `MainWindow.set_session` now stores the server-resolved role that drives the row visibility; the
  phase-26/27 desktop suites re-ran green against the enlarged view set (231 passed, above).

**Full suite at the Phase 28 head** (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`,
`pytest tests -q --cov=dz_erp --cov-report=term`): **686 passed, 20 skipped, 0 failed, exit 0 in
24 min 51 s** (pytest's internal duration; the run was suspended by the harness at conversation
turn boundaries — the phase-9 process-boundary subprocess workers were observed active and
cycling throughout, so the wall clock across turns overstates it; the healthy Phase 27 reference
is 19 min 18 s). Coverage **85 %** (6,239 statements, 939 missed; up from 6,033 statements at the
Phase 27 head). Per module: `api.py` 87 % (322 → 364 statements), `database.py` 94 %,
`security_audit.py` 79 % (469 → 501), `localization.py` 94 %, `desktop/service.py` 87 %,
`desktop/ui.py` 69 % (612 → 727; the users-view tests raised it from 63 %),
`desktop/api_client.py` 68 %, `desktop/app.py` 39 %. The single warning is the benign third-party
starlette/anyio `DeprecationWarning`. The total count reconciles exactly: 661 (Phase 27 head) +
12 user-admin API + 7 users-UI tests + 6 new forged-header parametrization instances (the
`/api/v1/users` pin × 6 header sets) = 686. No test was suppressed, weakened or skipped to obtain
this result. CI for this exact head is pending the authorized push (the workflow triggers on
`pull_request`), which is reported, not assumed.

## 2G. Phase 29 - fail-closed OCR uncertainty handling and field-level human review - `Passed`

Phase 29 turns OCR uncertainty handling from advisory into **enforced**. Extraction stays read-only
evidence; a human decision is a separate, attributed record (`ocr_field_reviews`) bound to the exact
value it was made on. New pieces, all server-side: `dz_erp/review_policy.py` (per-field rules,
tri-state status, fail-closed aggregation, extracted-totals arithmetic, Arabic/no-fields
transcription semantics); conflict capture at intake (a second, different reading of the same field
is recorded, never dropped); hardened human field edits; a decision service (CONFIRMED at the
reviewed value / CORRECTED or honestly transcribed / FLAGGED / NOT_PRESENT for genuinely absent
optional fields — refused for required ones); the invoice submit/approval gates that re-evaluate the
policy inside the same IMMEDIATE transaction (TOCTOU-safe) and audit every refusal
(`INVOICE_SUBMIT_BLOCKED_UNREVIEWED` / `INVOICE_APPROVAL_BLOCKED_UNREVIEWED`); a document-link state
whitelist; two authenticated API endpoints (`POST /api/v1/documents/{id}/fields/decide`,
`GET /api/v1/documents/{id}/review-policy`); and an idempotent migration that upgrades pre-phase-29
databases without losing a stored row. A measured score can only mark a field `low_confidence` — it
never accepts it; confidence unavailable is never treated as measured. Auto-accept remains
unimplemented on purpose (`docs/OCR_EVALUATION_PROTOCOL.md` §6).

Locally (Python 3.12.10, ruff clean, `QT_QPA_PLATFORM=offscreen`):

- `tests/test_phase29_ocr_review_policy.py` — **24 passed in 12.28 s**
- `python tools/staging_acceptance.py` — **40/40 checks** (three new checks: submission refused
  while the review is incomplete; the verdict readable and unsatisfied before decisions; field
  decisions satisfying the policy through the documented API)
- Full suite and coverage at the phase-29 head: recorded in the closing paragraph of this section.

### A. The enforcement and its refusals (`tests/test_phase29_ocr_review_policy.py`)

| Test | What it proves |
| :--- | :--- |
| `test_unreviewed_document_blocks_submission_and_the_block_is_audited` | Submitting an invoice whose document has no field decisions is **refused** (`DOCUMENT_REVIEW_UNSATISFIED`), audited as `INVOICE_SUBMIT_BLOCKED_UNREVIEWED` with the attempting clerk as actor, **committed before the refusal** (chain stays valid), and nothing moved. |
| `test_field_review_satisfies_the_gate_and_approval_rechecks_it` | Confirming every extracted required field (+ line quantity) and honestly asserting the absent optional ones satisfies the policy; the decisions are attributed (`reviewed_by_id`) and value-bound; submit and approval then succeed. |
| `test_approval_recheck_refuses_a_document_disturbed_after_submit` | A flag that appears after submission (out-of-band write — the only way the closed review can change) is caught by the approval gate's re-evaluation: refusal + `INVOICE_APPROVAL_BLOCKED_UNREVIEWED`; rejection (the safe direction) is never gated. |
| `test_legacy_invoice_without_document_is_unaffected` | The fully-typed, document-less invoice flow (validated → approved → posted) passes untouched — the gate only governs OCR-backed invoices. |
| `test_required_field_cannot_be_asserted_absent_but_optional_can` | `NOT_PRESENT` is honest only for a genuinely absent **optional** field; for a **required** field it is refused (`FIELD_REQUIRED_CANNOT_BE_ABSENT`) — a critical field must be transcribed, never asserted away. |
| `test_manual_transcription_route_is_satisfiable_with_honest_provenance` | The Arabic-only/no-fields route (`manual_transcription`) is satisfiable end to end: transcription creates rows with `human-manual-entry` provenance, raw `[manual entry]`, **no measured confidence** — and the transcribed invoice posts through the normal lifecycle. |
| `test_low_confidence_is_flagged_but_still_requires_a_decision` | A measured 0.31 is flagged `low_confidence` (harder scrutiny) and still requires a decision; the decision never touches the measured score or its provenance. |
| `test_conflicting_extraction_is_recorded_not_dropped` / `test_end_to_end_conflicting_totals_reach_the_database` | A second, different reading of the same field is kept as a recorded conflict (unit and rendered-PDF end-to-end), flagged `conflicting_extraction`; a decision on the current value satisfies the gate while the conflicting reading stays visible. |
| `test_inconsistent_extracted_totals_block_even_when_every_field_was_confirmed` | A confident misread (TTC edited to a value that no longer sums) blocks the document via `INCONSISTENT_EXTRACTED_TOTALS` even with every field confirmed; correcting the value re-satisfies the arithmetic **and** re-binds the stale decision. |
| `test_totals_rounding_boundary_and_the_timbre_note` | Exact 2-decimal sums pass; the missing timbre is a named, benign gap (`timbre not extracted`) rather than a silent one. |
| `test_transcribed_timbre_closes_the_gap_only_when_it_sums` | Transcribing a timbre closes the benign gap only when the sum holds; a 10.00 timbre on a 1190.00 invoice re-opens `inconsistent`. |
| `test_decision_bound_to_value_goes_stale_after_an_edit` | A CONFIRMED bound to the reviewed value goes `decision_stale` after a human edit and blocks submission (`FIELD_FLAGGED`); a CORRECTED re-binds it. |
| `test_flagged_field_blocks_until_superseded` | An open flag blocks; superseding with CONFIRMED on the exact current value clears it and submission proceeds. |
| `test_line_quantity_decision_binds_canonically_and_correction_recomputes` | Line decisions bind to the canonical 6-decimal quantity ("2" ≡ "2.000000"); a correction rewrites the quantity and recomputes the line amount; a wrong expected value is refused (`FIELD_VALUE_CHANGED_SINCE_READ`). |
| `test_decisions_are_idempotent_and_concurrency_is_refused` | An exact repeat of the latest decision is a replay (no new row, same timestamp); the same decision by another reviewer is a new event; a value changed between read and confirm is refused and writes nothing. |
| `test_every_decision_and_denial_is_audited_with_identity` | `OCR_FIELD_REVIEW_DECIDED` carries the reviewer's identity; the chain verifies. |
| `test_role_and_state_gates_on_decisions_and_edits_are_audited` | A warehouse role is refused (audited `SECURITY_FIELD_REVIEW_BLOCKED`/`SECURITY_FIELD_EDIT_BLOCKED`); once the document backs a submitted approval, decisions and edits are closed (`FIELD_REVIEW_CLOSED`/`FIELD_EDIT_CLOSED`) and those refusals are audited too. |
| `test_inactive_user_cannot_decide` | A deactivated account is refused outright. |
| `test_cross_tenant_decisions_are_impossible_and_reads_are_audited` | A foreign-tenant user cannot decide on (or even read the verdict of) the document; the cross-company read attempt is audited in the **victim's** chain; the tenant's data is untouched. |
| `test_document_link_state_whitelist` | A draft may cite a document only while it is `NeedsReview`/`Validated`; once it backs a submitted approval the link is refused (`DOCUMENT_LINK_STATE_REFUSED`) and the document's state is never clobbered. |
| `test_migration_upgrades_legacy_database_preserving_rows` | A real pre-phase-29 database (current schema minus the phase-29 objects) gains the conflict column, the decisions table and the refreshed immutability trigger through the idempotent migration; stored rows survive and decisions work immediately. |
| `test_api_decide_endpoint_contract` | The decide endpoint: 401 unauthenticated, 403 wrong role, 200 + idempotent replay, 422 for every contract violation (unknown decision, invalid transcription value, `NOT_PRESENT` on a present required field, flag on a non-policy field) with stable reason prefixes, 404 that conflates foreign and missing documents. |
| `test_api_review_policy_endpoint` | The verdict endpoint: 401/403/404 and the same evaluation the gates enforce, with per-field status, honest confidence provenance, totals and blocking reasons. |

### B. Contract updates and cross-phase consistency

- OpenAPI protected-operation pin: **26 → 28** (the two phase-29 endpoints); the public set is
  unchanged.
- Shared review fixture: `tests/conftest.py` gained `complete_document_field_review` (confirms
  every extracted required field, asserts genuinely-absent optional fields, asserts satisfaction);
  the phase-5/6/7/13 lifecycle tests now complete the field review before driving an invoice
  forward — the intended fail-closed flow, no test weakened or skipped.
- The phase-27 desktop review-dialog test completes the field review through the service before
  submitting (its minimal two-field fixture follows the honest transcription path; a desktop
  decision UI remains future work).
- `tools/staging_acceptance.py`: **37 → 40 checks** (the gate refusal, the unsatisfied verdict, and
  API-driven decision completion before the invoice workflow).
- Documentation: `docs/OCR_EVALUATION_PROTOCOL.md` §6 now states the enforced review and the
  deliberately unimplemented auto-accept path; `docs/RISK_REGISTER.md` O1/O2/O3 updated;
  `docs/RELEASE_READINESS_MATRIX.md` R-OCR/R-SCF citations updated.

**Full suite at the Phase 29 head** (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`,
`pytest tests -q --cov=dz_erp --cov-report=term`): **710 passed, 20 skipped, 0 failed, exit 0 in
29 min 15 s**. Coverage **85 %** (6,740 statements, 993 missed; up from 6,239 statements at the
Phase 28 head). Per module: the new `review_policy.py` **88 %** (242 statements), `api.py` 87 %
(322 → 397), `database.py` 94 %, `intake_ocr.py` 92 %, `procurement_stock.py` 83 %,
`security_audit.py` 79 %. A second, plain no-coverage confirmation run passed **710 passed,
20 skipped, 0 failed, exit 0 in 23 min 48 s**. The single warning is the benign third-party
starlette/anyio `DeprecationWarning`. The total count reconciles exactly: 686 (Phase 28 head) +
24 phase-29 tests = 710. No test was suppressed, weakened or skipped to obtain this result. CI for
this exact head is pending the authorized push (the workflow triggers on `pull_request`), which is
reported, not assumed.

### C. Follow-up on the same phase: reviewed source line unit prices are compared (`fix/phase29-invoice-line-price-consistency`)

PR #4 bound invoice approval to the reviewed source but compared line quantities only — it named
per-line unit prices as the explicit follow-up. This branch closes that gap. The prerequisite was
verified first: the extracted line payload carries `unit_price_dzd`, but no decision could bind to
it, so the per-line unit price becomes its own decided review element (`line_unit_price`), keyed by
the same `line_index`, bound to the canonical 6-decimal form that invoice and purchase-order lines
store (`PU` is quantized exactly like a quantity everywhere it is persisted). CONFIRMED binds the
price the reviewer saw; CORRECTED transcribes or repairs it and rewrites the line payload with the
recomputed line amount (`qty × PU × (100 − discount)/100`) without staling the quantity decision of
the same line; NOT_PRESENT is refused for line elements; an absent or malformed extracted price has
no reviewable current value, so the only repair is an attributed transcription. The
submit/approval consistency gate now also requires each invoice line price to equal its reviewed
source price by exact 6-decimal equality (no floats, no tolerance; all conversions via
`to_decimal`/`round_qty`/`round_dzd`), inside the same IMMEDIATE transaction, with the same
attributed, bounded, fail-closed audit (`DOCUMENT_INVOICE_MISMATCH`,
`INVOICE_{SUBMIT,APPROVAL}_BLOCKED_SOURCE_MISMATCH`, details truncated at 20). Lines are associated
positionally — the invoice line table stores no supplier SKU to join on — and a transposed order is
itself a flagged mismatch, never silently rematched. No schema migration is needed
(`ocr_field_reviews.field_name` is free TEXT; the immutability trigger already permits
`normalized_value` updates).

Deliberate boundary — per-line `line_ht_dzd` is **not** compared: the extracted payload carries it,
but it is a derived value (recomputed from qty × PU × discount on every correction), never a decided
one, and the extracted-totals arithmetic does not cross-check per-line sums — so there is no
reviewed evidence to compare and no remediation path for a legitimate misread. Header HT, TVA and
TTC totals **are** decided and compared, and invoice-internal validation pins each line HT to
`qty × PU × (1 − discount)`. The remaining slack (a per-line `discount_pct` nobody decides) is a
known, documented limitation, not a silent one. The extraction regex `([0-9]+(?:\.[0-9]+)?)` cannot
produce a negative or malformed price from a real extraction; only out-of-band tampering can, and
the fail-closed paths below cover it.

New regression instances in `tests/test_phase29_ocr_review_policy.py` (27 → 37):

| Test | What it proves |
| :--- | :--- |
| `test_reviewed_source_line_unit_price_must_match_invoice` | A matching line quantity does not license a different price: 2 × 625.00 (+19 %) is internally consistent but differs from the reviewed 2 × 500.00 — refused `LINE_1_PRICE_SOURCE_MISMATCH`, audited, chain valid. |
| `test_swapped_invoice_line_prices_are_flagged_not_silently_matched` | Transposed prices with identical header totals and row-wise matching quantities are flagged on **both** positions (`LINE_1_`/`LINE_2_PRICE_SOURCE_MISMATCH`), with no quantity flag — positional association, never unordered-set matching. |
| `test_missing_or_malformed_source_price_blocks_until_transcribed` (absent + malformed) | A price that is not on file cannot be confirmed (`FIELD_VALUE_CHANGED_SINCE_READ`); the document blocks as `REQUIRED_FIELD_NOT_DECIDED:line_unit_price[0]`; a CORRECTED transcription rewrites the payload (`500.000000`) and the line amount (`1000.00`), and the matching invoice then flows. |
| `test_invoice_line_price_tampered_after_submit_blocks_approval` | An invoice line price changed out-of-band after submission is caught by the approval recheck (`INVOICE_APPROVAL_BLOCKED_SOURCE_MISMATCH`, chain valid). |
| `test_invoice_line_price_unparseable_blocks_submission` | An invoice-side price left unparseable out-of-band is refused as `LINE_1_PRICE_SOURCE_UNPARSEABLE` (audited) — the comparison never silently skips a price because a value is unreadable. |
| `test_source_price_changed_after_decision_blocks_and_rebinds_fail_closed` | A source price edited after the reviewer bound it goes `decision_stale` (`FIELD_FLAGGED:line_unit_price[0]`), the gate refuses on the review itself, a CORRECTED transcription re-binds to the current value, and the previously matching invoice is then refused on price — fail closed in both directions. |
| `test_duplicate_identical_lines_match_positionally` | Two identical source rows and two identical invoice lines match deterministically positionally and the invoice flows; ambiguity is resolved by order, never by unordered-set rematching. |
| `test_line_price_decision_binds_canonically_and_correction_recomputes` | "500.00" ≡ "500.000000"; `600,50` (French decimal) lands on `600.500000`; NOT_PRESENT refused; a CORRECTED price rewrites the payload and recomputes the line amount without staling the bound quantity; exact replay is idempotent; a negative transcription is refused (`FIELD_VALUE_NEGATIVE_AMOUNT`); zero binds canonically (`0.000000`, line amount recomputed to `0.00`). |
| `test_sub_centime_price_identity_is_enforced_at_six_decimals` | A 3-decimal misread confirmed as read (500.004) flags the header-identical centime invoice (500.00) — exact identity, never rounded into agreement; transcribing the printed price re-satisfies the policy and the same invoice flows. |
| `test_legacy_invoice_without_document_is_unaffected` | The fully-typed, document-less invoice path still validates → approves → posts untouched. |

Cross-tenant isolation is covered by the existing `test_cross_tenant_decisions_are_impossible_and_reads_are_audited`
(unchanged): every gate query is company-scoped (`source_documents`, `ocr_extracted_fields`,
`ocr_field_reviews`, `supplier_invoice_lines`, `partners`), and the full suite re-proves the
invoice-creation, approval, posting, trial-balance, duplicate-post, migration and recovery behavior
it always covered. No test was weakened or skipped.

Local verification (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`, on this branch's tree):

- `tests/test_phase29_ocr_review_policy.py` — **37 passed** (18.33 s).
- `python tools/staging_acceptance.py` — **40/40 checks, exit 0** (the API-driven review completion
  now decides `line_unit_price` as well).
- Ruff on the CI scope (`dz_erp tests tools`) — clean; `compileall` — clean; dependency graph — OK;
  hash-lock — OK (51 pinned distributions, 1,277 hashes); risk register — 31 rows, consistent;
  `cv2`/`RapidOCR` import assertion — OK; phase-22 doc-consistency tests — **15/15 passed**.
- Full suite and coverage at this head: recorded in the closing paragraph below.

**Full suite at the line-price head** (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`,
`pytest tests -q --cov=dz_erp --cov-report=term-missing --cov-report=xml`): **723 passed, 20 skipped, 0 failed, exit 0 in 22 min 53 s**.
Coverage **85 %** (6,844 statements, 1,002 missed; from 6,740 statements at
the Phase 29 head). Per module: `review_policy.py` 90 % (258 statements),
`intake_ocr.py` 92 %, `procurement_stock.py` 83 %, `api.py` 87 %,
`database.py` 94 %, `decimal_utils.py` 89 %. The total count reconciles exactly:
710 (Phase 29 head) + 3 PR #4 tests + 10 new instances (9 functions, one parametrized ×2) = 723.
No test was suppressed, weakened or skipped to obtain this result. CI for this exact head is
reported with the PR, not assumed.

### D. Follow-up on the same phase: the line discount is decided and the line net is re-derived (`fix/phase29-invoice-discount-and-line-net-consistency`)

Subsection C named the remaining limitation explicitly: the per-line `discount_pct` was a
decided-by-nobody input. This branch closes it. The prerequisite was verified first: invoice-side
discounts are a first-class persisted field (`supplier_invoice_lines.discount_pct`, stored in the
same 2-decimal `format_dzd` form as every other money string), the invoice's own validation pins
every line's net to `qty × PU × (1 − discount/100)`, and the extraction pattern emits no discount at
all — so the line discount becomes the third decided line element (`line_discount`, keyed by the
same `line_index`, threshold 0.85), whose only entry path is the established attributed
CORRECTED-transcription flow. The documented business rule is **0 ≤ discount ≤ 100, inclusive at
both ends, never clamped**: a negative transcription is refused (`FIELD_VALUE_NEGATIVE_AMOUNT`),
anything above 100 is refused (`FIELD_VALUE_DISCOUNT_OUT_OF_RANGE`), and the same range is enforced
at invoice creation (`LINE_{n}_DISCOUNT_OUT_OF_RANGE` → `NeedsReview`, unsubmittable). A corrected
discount rewrites the line payload and re-derives the line net deterministically — exactly like the
quantity and price corrections — without staling the other decisions on the same line. Because the
reviewed quantity, price and discount are now all decided, the source-derived line net becomes
semantically comparable: the gate re-derives `round_dzd(qty × PU × (1 − discount/100))` from the
*decided* canonical values (never the payload's stored `line_ht_dzd` reading, which is derived and
undecided) inside the same IMMEDIATE transaction at submit and approval, and compares it exactly
with the invoice line's `line_ht_dzd` (2-decimal, `round_dzd`, ROUND_HALF_UP on both sides — the
same formula and rounding invoice creation enforces). Documented rounding policy: the decided
2-decimal discount is authoritative; an invoice whose net was validated against a raw discount with
more precision than the invoice column stores may be flagged by up to a centime — the honest repair
is entering the invoice with the canonical 2-decimal discount (pinned by a test). No schema
migration is needed (`ocr_field_reviews.field_name` is free TEXT; the trigger permits
`normalized_value` updates). Two adjacent integrity gaps were closed in the same pass, both
strictly fail-closed: `compute_invoice_payload_hash` now binds `discount_pct` (a first-class
financial field was absent from the approved-payload digest), and `modify_draft_invoice_line`
re-derives the net *with* the persisted discount instead of silently dropping it.

Exact source-to-invoice mapping (all comparisons exact equality, no floats, no tolerance):

| # | Source (reviewed decision) | Canonical | Invoice (financial record) | Canonical | Gate refusal codes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `line_item_json[i]` → `qty` | 6-dec `round_qty` | `supplier_invoice_lines.qty` | 6-dec `round_qty` | `LINE_{i}_QUANTITY_SOURCE_MISMATCH` / `_UNPARSEABLE` |
| 2 | `line_unit_price[i]` → payload `unit_price_dzd` | 6-dec `round_qty` | `supplier_invoice_lines.unit_price_dzd` | 6-dec `round_qty` | `LINE_{i}_PRICE_SOURCE_MISMATCH` / `_UNPARSEABLE` |
| 3 | `line_discount[i]` → payload `discount_pct` (attributed transcription only) | 2-dec `format_dzd` | `supplier_invoice_lines.discount_pct` | 2-dec `format_dzd` | `LINE_{i}_DISCOUNT_SOURCE_MISMATCH` / `_UNPARSEABLE` |
| 4 | derived net from the three decided values | 2-dec `round_dzd` | `supplier_invoice_lines.line_ht_dzd` | 2-dec `round_dzd` | `LINE_{i}_LINE_HT_SOURCE_MISMATCH` / `_UNPARSEABLE` |

Not mapped (unchanged boundaries): PO lines carry no discount column (the 3-way match compares
quantity, unit price and tax rate only); the payload's own `line_ht_dzd` reading is never compared
(it is derived, undecided, and re-derived on every correction); per-line TVA/TTC remain derived-only
with the header TVA/TTC totals decided and compared.

New regression instances in `tests/test_phase29_ocr_review_policy.py` (37 → 48):

| Test | What it proves |
| :--- | :--- |
| `test_reviewed_discount_and_line_net_match_flows` | A discounted paper, fully decided (discount transcribed 10 %, header totals corrected to the discounted values), authorizes exactly that invoice: discount equality and the derived net (900.00) agree exactly at submit and approval. |
| `test_mismatched_source_discount_blocks_submission` | A decided 10 % discount the invoice ignores flags both the discount and the derived net (`LINE_1_DISCOUNT_SOURCE_MISMATCH` + `LINE_1_LINE_HT_SOURCE_MISMATCH:derived=900.00!=invoiced=1000.00`), audited, chain valid. |
| `test_line_net_rounding_boundary_authoritative_discount` | An invoice entered with raw discount 10.004 (stored 10.00, net raw-derived 899.96) passes its own math but is flagged at the gate against the decided 2-decimal discount (derived 900.00) — with no discount mismatch; the documented rounding-policy boundary, pinned. |
| `test_discount_decision_boundaries_are_refused_or_bound_exactly` | Negative refused (`FIELD_VALUE_NEGATIVE_AMOUNT`), 100.01 refused (`FIELD_VALUE_DISCOUNT_OUT_OF_RANGE`), French `12,5` binds canonically (`12.50`, net re-derived 875.00), idempotent replay, 100.00 binds inclusively and zeroes the net. |
| `test_invoice_creation_refuses_out_of_range_discount` | −5 % and 150 % invoice lines land in `NeedsReview` with `LINE_1_DISCOUNT_OUT_OF_RANGE` and cannot be submitted — no clamping, even with an arithmetically consistent declared net. |
| `test_undecided_discount_blocks_until_transcribed` | Quantity and price confirmed, discount undecided → `REQUIRED_FIELD_NOT_DECIDED:line_discount[0]`, submission refused; the honest 0.00 transcription (the paper's net equals qty × PU) satisfies and the same invoice flows. |
| `test_absent_or_malformed_payload_discount_routes_to_transcription` | A discount that is not (correctly) on file cannot be CONFIRMED (absent or malformed out-of-band write both refuse `FIELD_VALUE_CHANGED_SINCE_READ`); CORRECTED transcription is the only repair and binds the canonical value. |
| `test_source_discount_changed_after_decision_blocks_and_rebinds` | A source discount edited after the binding decision goes stale (`FIELD_FLAGGED:line_discount[0]`), submission refuses on the review, a CORRECTED transcription re-binds and re-derives the net (800.00) without staling the quantity/price decisions, and the still-10 % invoice is then refused on both the discount and the net — fail closed both ways. |
| `test_invoice_discount_tampered_after_submit_blocks_approval` | The invoice's discount tampered out-of-band after submission is caught at approval (`INVOICE_APPROVAL_BLOCKED_SOURCE_MISMATCH`, chain valid). |
| `test_invoice_discount_or_net_unparseable_blocks_submission` | An out-of-band unparseable invoice discount refuses `LINE_1_DISCOUNT_SOURCE_UNPARSEABLE`; an unparseable net refuses `LINE_1_LINE_HT_SOURCE_UNPARSEABLE` — the comparison never silently skips an unreadable value. |
| `test_legacy_discounted_invoice_recomputes_and_posts` | Document-less discounted invoices approve and post; the payload hash binds the discount (different hash per discount); a price edit re-derives the net with the persisted discount (990.00, not the discount-dropping 1100.00) and returns the invoice to NeedsReview. |

Cross-tenant isolation, attributed audit with bounded details, commit-then-refuse and audit-chain
validation are re-proven unchanged (every gate query stays company-scoped; the new codes flow into
the existing `mismatches[:20]` truncating audit), and the full suite re-proves invoice creation,
approval, posting, trial-balance, duplicate-post, migration and recovery behavior it always
covered. No test was weakened or skipped. Reordered lines remain covered by the swap test
(positionally flagged, unchanged); duplicate lines by the duplicate test, now also deciding
discounts.

Local verification (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`, on this branch's tree):

- `tests/test_phase29_ocr_review_policy.py` — **48 passed** (45.85 s).
- `python tools/staging_acceptance.py` — **40/40 checks, exit 0** (the API-driven review completion
  now also transcribes each line's discount through the documented API).
- Ruff on the CI scope (`dz_erp tests tools`) — clean; `compileall` — clean; dependency graph — OK;
  hash-lock — OK (51 pinned distributions, 1,277 hashes); risk register — 31 rows, consistent; `cv2`/`RapidOCR` import
  assertion — OK; phase-22 doc-consistency tests — **15/15 passed**.
- Full suite and coverage at this head: recorded in the closing paragraph below.

**Full suite at the discount head** (Windows/CPython 3.12.10, `QT_QPA_PLATFORM=offscreen`,
`pytest tests -q --cov=dz_erp --cov-report=term-missing --cov-report=xml`): **734 passed, 20 skipped, 0 failed, exit 0 in 21 min 25 s**.
Coverage **85 %** (6,916 statements, 1,006 missed; from 6,844 statements at
the Phase 30 head). Per module: `review_policy.py` 91 % (281 statements), `intake_ocr.py` 92 %,
`procurement_stock.py` 83 %, `api.py` 87 %, `database.py` 94 %,
`decimal_utils.py` 89 %. The total count reconciles exactly: 723 (Phase 30 head) + 11 new
instances = 734. No test was suppressed, weakened or skipped to obtain this result. CI for this
exact head is reported with the PR, not assumed.

---

## 2H. Phase 33 — recoverable invoice lifecycle and accounting error semantics (`tests/test_phase33_invoice_lifecycle.py`) — `Passed`

Phase 32's independent audit (`docs/PHASE32_INDEPENDENT_INTEGRATION_AUDIT.md`)
reported four reproducible findings and, by design, did not fix them. Phase 33
reproduces all four on the verified Phase 32 head
`2a2b9b14de0cf792356488d645f20e5c4d4ad565` and remediates them with one rule
behind every change: **an attempt that is refused must leave the books, the
evidence and the audit chain exactly as they were, while the refusal itself
becomes durable, attributed evidence.**

| Finding | What was wrong (Phase 32 tree) | What the test now pins |
| :--- | :--- | :--- |
| P32-F2 | `NeedsReview` was terminal: no service re-validated an invoice, and re-creating it was refused as a duplicate of the broken draft | correction + re-validation on the same invoice identity, every prerequisite re-run, previous attempts preserved, digest recomputed so a rejected result can never authorise posting |
| P32-F3 | a refused duplicate attempt rewrote the *original* document's `workflow_state` to `DuplicateRejected` | no document write on refusal, one paper = one live invoice, refusal audited independently |
| P32-F4 | modification left the document `PendingApproval` and the request `PENDING` while the invoice became `NeedsReview`, with no binding to the approved payload | explicit `INVALIDATED` + state sync + payload/fingerprint binding, legacy `NULL` binding tolerated |
| P32-F5 | a second reversal escaped as `sqlite3.IntegrityError` (HTTP 500) from a unique constraint | `INVOICE_ALREADY_REVERSED` domain refusal (409) detected before the insert, attributed audit, exactly one credit note under a race |

| Test group | Cases | What they prove |
| :--- | ---: | :--- |
| `test_f2_*` | 12 | correct-in-place and post the result; repeated re-validation is idempotent and each attempt is audited; a failed re-validation is recorded and still blocks submission; the approved digest is the corrected one, never the rejected one; `PendingApproval`/`Approved`/`Posted`/`Rejected` refuse re-validation (4 params); a duplicate-rejected draft is correctable; re-validation cannot steal another invoice's reference; the reviewed-source gate survives re-validation; document-less legacy invoices are correctable; role and tenant scoping; HTTP `200` / `409` |
| `test_f3_*` | 3 | the refused attempt leaves both documents and the original invoice byte-identical, records actor/company/document/reference/reason with `document_state_touched: false`; a second live invoice on an owned document is refused `DOCUMENT_ALREADY_INVOICED` with one invoice remaining; duplicate detection stays company-scoped |
| `test_f4_*` | 8 | price/quantity/discount modifications each invalidate the approval, re-derive the exact line net and header totals and invalidate every request with actor + reason; document and invoice stay consistent; a corrected manual invoice can be resubmitted and approved; an obsolete request cannot authorise a modified invoice; approval is bound to the source-review fingerprint; a legacy `NULL` fingerprint still works; rejection of a modified invoice is never gated |
| `test_f5_*` | 5 | first reversal succeeds and the second is a domain conflict with an attributed audit event and an HTTP `409`; a two-thread `threading.Barrier` race yields exactly one credit note and a balanced ledger; cross-company attempts are refused; an entry-number collision is mapped to `REVERSAL_ENTRY_UNIQUE_CONFLICT` |
| `test_invariants_*`, `test_migration_*` | 3 | posted journal entries and their lines are never rewritten by any later refused operation; a full lifecycle (broken → corrected → posted → reversed) stays balanced with one `INVOICE_REVALIDATED` and one `REVERSAL_POSTED`; the additive `approval_requests.source_review_fingerprint` column migrates idempotently, and a `DROP COLUMN` round trip proves the pre-Phase-33 schema still reads and approves |

Exact measured evidence on this host (Linux, Python **3.11.2**, pytest 8.3.5,
coverage 6.0.0, ruff 0.14.0, hash-locked dependency set):

* `pytest tests/test_phase33_invoice_lifecycle.py -q` → **33 passed**, 4.96 s.
* `pytest tests/test_phase32_cross_phase_integration_audit.py tests/test_phase29_ocr_review_policy.py -q` → **92 passed, 1 skipped**, 12.02 s. The three Phase 32 characterisation tests (`T`, `U`, `W`) were converted into resolution assertions and `T2` was added; none was deleted, skipped or weakened.
* `pytest tests/test_phase8_upload_boundary_and_ci.py -q` → 204 passed, 2 failed. Both failures are the interpreter floor (`Safe backup restore requires Python 3.11.17+`), an environment limit of this host, not an application result.
* Full suite with coverage → **796 passed, 20 failed, 7 skipped**, 162.05 s, coverage **77 %** (7,116 statements, 1,623 missed; `api.py` 86 %, `database.py` 94 %, `procurement_stock.py` 87 %). All 20 failures are the same interpreter floor in `security_audit.py`'s restore gate; no application assertion failed, and none is recorded as a pass.
* `ruff check dz_erp tests tools` → All checks passed; `compileall` clean; `tools/verify_dependency_graph.py` → OK; `tools/generate_hash_lock.py --check` → 51 distributions / 1,277 hashes, no drift; `cv2 4.11.0` and `RapidOCR` import OK; `tools/staging_acceptance.py` → 38/40 (the two failures are the restore check and its dependent restart check, same interpreter floor); `tools/risk_register_counts.py` → 36 rows consistent; `pytest tests/test_phase22_register_and_docs_consistency.py` → 15 passed; `git diff --check` clean; secret scan over the whole diff → no matches.
* The protected-operation pin in `tests/test_phase8_upload_boundary_and_ci.py` moves from 28 to 29 with the new `POST /api/v1/invoices/{invoice_id}/revalidate` endpoint documented in the comment — the assertion itself is not relaxed.

No Algerian SCF, TVA, timbre or G50 rule was changed and no external production
gate was marked verified.

**CI on the exact published SHA `f8facf036f76d75b7ed6e23bd5d4e9adbc12062e`
(GitHub Actions run 41089747877) — 3/3 jobs success, zero non-success steps:**
Python 3.11 (hash-locked install, dependency graph, cv2/RapidOCR import, ruff,
compileall, full suite with coverage, staging acceptance, risk-register
validation), Python 3.12 (full suite; the two Linux-only steps are skipped by
design), and Windows 3.12.10 (rotation/migration/backup/recovery suite and
staging acceptance). The restore paths that this host cannot run below Python
3.11.17 are consequently proven green in CI — on CI, not here. Per-step log text
is not retrievable from this sandbox (the log host is outside the allowlist), so
the CI evidence cited is the run, job and per-step conclusions from the GitHub
API, not copied console output.

---

## 2I. Phase 34 — duplicate OCR extraction rows must not fail open (`tests/test_phase34_ocr_duplicate_row_integrity.py`) — `Passed`

Phase 29 made field review *enforced*, but it assumed one logical field is one row
in `ocr_extracted_fields`. Nothing enforced that assumption, and the policy did not
act on the flag it raised. Reproduced on `852f389f` before any change: a fully
reviewed document plus a second `amount_ht_dzd` row holding `5555.00` produced
`conflicting_extraction` on the field **and** `satisfied: true`, so the invoice
validated and was submitted for approval.

| Weakness | Resolution |
|---|---|
| `conflicting_extraction` / duplicate rows never affected `satisfied` | A duplicate group is its own blocking reason, `DUPLICATE_EXTRACTION_ROWS:<field>`; the field cannot be satisfied at all while the group is unresolved |
| `rows[0]` was silently adopted as "the" value | The group is reported with every row id (`duplicate_row_ids`), and the evaluated `value` follows the reviewer's explicit selection, never insertion order |
| A decision keyed by `(field_name, line_index)` authorised any later row | Each decision records `evidence_json` = `{row_ids, selected_row_id, group_size, selected_source}`; satisfaction requires the binding to cover **exactly** the rows present now |
| Line-level duplicates were blocked only by accident | Line rows are grouped by logical index; a duplicate index blocks quantity, price and discount with the same reason code |
| Nothing re-checked the review at posting | Posting re-runs the same gate inside the posting transaction; a refusal rolls the attempt back, records the refusal durably, invalidates the approval and returns the invoice to `NeedsReview` (no dead-end) |

| Test | What it proves |
| --- | --- |
| `test_1_duplicate_header_rows_with_identical_values_still_block` | Identical duplicates are record-integrity evidence: the system never decides which of two equal rows is redundant |
| `test_2_duplicate_header_rows_with_different_values_block_until_resolved` | **The regression**: the conflicting duplicate that used to submit is refused at submission and audited |
| `test_3_duplicate_rows_on_different_pages_block_and_are_all_named` | Page scope does not make a duplicate benign; every row is listed so the reviewer chooses with the evidence visible |
| `test_4_duplicate_line_rows_with_the_same_index_block_every_element` | A cloned line blocks quantity, price and discount, and the displayed value never follows the clone |
| `test_5_distinct_line_indices_are_not_duplicates` | Two real lines stay two lines — the fix does not collapse evidence |
| `test_6_explicit_reviewer_resolution_records_identity_value_and_evidence` | The resolution is attributed (actor, timestamp, note), names the selected row and the whole group, and appears in the audit chain |
| `test_6b_selection_cannot_name_another_documents_row` | A selection cannot be pointed at another document's evidence (`EXTRACTION_ROW_NOT_IN_GROUP`) |
| `test_7_unresolved_duplicates_block_every_required_field_type` | Money, identity and date fields all block, with the right diagnosis (not a misleading "not decided") |
| `test_8_recorded_alternate_reading_is_a_distinct_concept` | `conflict_values_json` (intake-recorded alternate reading) stays separate from duplicate *rows* and neither masks the other; resolution still works the Phase 29 way |
| `test_9_a_row_appearing_after_resolution_stales_the_decision` | The binding is to the evidence *set*; a third row re-opens the field, and re-resolution is possible (no dead-end) and idempotent once the set is stable |
| `test_10_submission_is_blocked_and_the_refusal_is_audited` | `INVOICE_SUBMIT_BLOCKED_UNREVIEWED`, invoice stays `Validated`, no approval row, no journal entry |
| `test_11_approval_is_blocked_when_a_duplicate_appears_after_submission` | `INVOICE_APPROVAL_BLOCKED_UNREVIEWED`, invoice stays `PendingApproval`, nothing posted |
| `test_12_posting_re_runs_the_gate_so_a_late_duplicate_cannot_reach_the_ledger` | Posting refuses, records `INVOICE_POSTING_BLOCKED_UNREVIEWED`, invalidates the approval (`SOURCE_REVIEW_INVALID_AT_POSTING`), returns the invoice and document to `NeedsReview`, then the repaired invoice posts exactly once and balances at 1190.00 |
| `test_13_duplicate_evidence_is_tenant_scoped` | Another tenant cannot decide on the document and sees no part of its duplicate set |
| `test_14_concurrent_review_and_submission_cannot_race_past_the_gate` | Two threads (resolution + submission) always end in one coherent state, with a valid audit chain |
| `test_15_evidence_column_migrates_idempotently_and_keeps_legacy_decisions` | `ocr_field_reviews.evidence_json` is additive and nullable; `DROP COLUMN` round trip; legacy `NULL` bindings still satisfy a single-row field and do **not** cover a duplicate set |
| `test_16_a_flagged_duplicate_cannot_be_confirmed_into_satisfaction` | FLAG/NOT_PRESENT are observations, not selections: neither launders an unresolved group, and no row is deleted |
| `test_17_transcription_resolves_the_group_without_deleting_rows` | A human transcription supersedes the group, both rows keep their raw evidence, the invoice posts once and balances |
| `test_18_json_extraction_never_duplicates_a_field` | The intake path itself writes one row per logical field (guards the assumption this phase closes) |

Measured on this host (Linux, CPython 3.11.2, pytest 8.3.5):

* `pytest tests/test_phase34_ocr_duplicate_row_integrity.py -q` → **19 passed**, 4.43 s.
* Phase 29 + 32 + 33 + OCR/Arabic/doc-consistency suites → **196 passed, 1 skipped** (the skip is the interpreter floor).
* Full suite → **815 passed, 20 failed, 7 skipped**, 214 s, 77 % coverage; the 20 failures are `diff`-identical to the pre-change baseline and are all `restore_and_verify_backup`'s interpreter floor.
* `ruff check dz_erp tests tools` → All checks passed; `compileall` clean; risk-register summary consistent.

Nothing was deleted or merged: duplicate extraction rows remain stored evidence.
No UNIQUE constraint was added, because a constraint cannot distinguish a
legitimate re-extraction artefact from an operator error, and enforcing one on
historical data would either fail the migration or silently drop rows.

---

## 2J. Phase 35 — authentication throttling and request logging (`tests/test_phase35_api_rate_limiting_and_request_logging.py`) — `Passed`

The token endpoint accepted unlimited guesses: a wrong password cost a 401 and nothing else,
while the server paid for a full password hash each time. That is a credential-stuffing target
and a denial-of-service lever in one. Every request was also unattributable in the log.

| Test | What it proves |
| --- | --- |
| `test_sliding_window_counts_and_expires_fail_closed` | The window counts, refuses, keeps counting a refused attempt, and genuinely expires — a throttle, not a lockout |
| `test_a_disabled_limiter_never_interferes` | `limit=0` disables a rule rather than blocking everything |
| `test_login_key_is_case_and_space_insensitive_per_account` | `" Admin "` and `"admin"` share one budget; different clients do not |
| `test_policy_description_is_secret_free` | The operator-facing description contains no credential-bearing field |
| `test_repeated_wrong_passwords_are_throttled_and_audited` | The 4th attempt is 429 with `Retry-After`, the correct password is **not** verified once throttled, and `SECURITY_AUTH_RATE_LIMITED` is recorded |
| `test_throttling_one_account_does_not_deny_service_to_another` | Locking `admin_a` leaves `clerk_a` working — no system-wide denial |
| `test_a_successful_login_clears_the_counter` | Mistyping then succeeding restores the budget |
| `test_the_throttle_expires_so_no_one_is_locked_out_forever` | After the window, the correct password works again |
| `test_forwarded_for_cannot_escape_the_limit_without_a_trusted_proxy` | Rotating `X-Forwarded-For` from an untrusted peer still hits 429 — the bypass that matters |
| `test_a_trusted_proxy_addresses_clients_separately` | Behind a **declared** proxy the forwarded address is the identity: one noisy client does not penalise another |
| `test_every_response_carries_a_correlation_id` | A supplied `X-Request-Id` is echoed; otherwise a UUIDv4 is issued (including on 401) |
| `test_request_log_records_path_status_and_duration` | `HTTP_REQUEST` carries method, path, status and a float duration |
| `test_no_password_or_token_is_ever_logged` | Neither the password, the wrong password, nor the bearer token appears anywhere in the log |
| `test_the_request_log_lands_in_the_durable_security_sink` | Records reach `JsonlSecurityLog` on disk, still credential-free |
| `test_global_ceiling_is_off_by_default_and_enforces_when_configured` | 40 requests pass with the default; with `requests_per_minute=5`, attempts 6–7 are 429 and audited with the path |
| `test_throttle_defaults_come_from_the_environment` | Environment configuration applies, and garbage (`not-a-number`, `-5`, `-1`) falls back to the documented default rather than disabling the control |

Measured (Linux, CPython 3.11.2, pytest 8.3.5): **16 passed**, 2.28 s. The surrounding API and
authentication suites (`test_phase25_read_api`, `test_phase27_review_api`,
`test_phase28_user_admin`, `test_phase7_authentication_remediation`,
`test_phase5_security_audit_backup`) → 99 passed, 1 failed, and that one failure is
`restore_and_verify_backup`'s Python 3.11.17 interpreter floor, identical to the baseline.

The honest limit of this control is documented rather than hidden: counters live in process
memory, so the bound is per worker and a multi-replica deployment must also limit at the proxy.
