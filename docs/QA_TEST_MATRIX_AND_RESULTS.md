# QA Test Matrix and Execution Results (Phase 6)

## 1. Execution Command & Summary (`Passed`)

```bash
/tmp/erp-venv/bin/pytest --cov=dz_erp --cov-report=term-missing
```

- **Total Collected Tests:** `30`
- **Passed:** `30` (`100%`)
- **Failed / Skipped / Errors:** `0`
- **Total Execution Duration:** `17.21s`
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
