# QA Test Matrix and Execution Results (Phase 7)

## 1. Execution Command & Summary (`Passed`)

```bash
LD_LIBRARY_PATH=/tmp/glstub /tmp/erp-venv/bin/pytest --cov=dz_erp --cov-report=term-missing
```

- **Total Collected Tests:** `87`
- **Passed:** `87` (`100%`)
- **Failed / Skipped / Errors:** `0`
- **Total Execution Duration:** `25.71s`
- **Environment:** Python `3.11.2`, `pytest 8.3.5`, SQLite `3.40.1` (`WAL`), FastAPI `0.115.12`
- **Overall Code Coverage (`dz_erp`):** **`92%`** (`1,848 / 2,001` statements covered; `100%` on `benchmark.py`, `97%` on `auth.py`, `96%` on `database.py` and `odoo_test_adapter.py`, `93%` on `localization.py`, `90%` on `intake_ocr.py` and `procurement_stock.py`, `89%` on `security_audit.py`, `84%` on `api.py`).

> `LD_LIBRARY_PATH=/tmp/glstub` supplies a stub `libGL.so.1` for the headless
> OpenCV build in this sandbox; it is an environment detail, not a code dependency.
- **Overall Code Coverage (`dz_erp`):** **`89%`** (`1,489 / 1,666` statements covered; `100%` on `benchmark.py`, `97%` on `database.py`, `96%` on `odoo_test_adapter.py`, `93%` on `localization.py`, `90%` on `intake_ocr.py`, `89%` on `procurement_stock.py`, `86%` on `security_audit.py`).

---

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
