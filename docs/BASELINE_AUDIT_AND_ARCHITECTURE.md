# Baseline Audit and System Architecture

## 1. Repository & Upstream Discovery Summary (`Verified`)

| Attribute | Verified Value | Status |
| :--- | :--- | :--- |
| **Target Repository** | `Devmraustro/ERP` (`/home/user/ERP`) | `Verified` |
| **Working Branch** | `arena/f74b041f-erp` | `Verified` |
| **Original Base Commit** | `0e106e05bc1328780e7a837a65b870d535d9f6d6` (`Initial commit`, containing only `README.md`, 2 bytes) | `Verified` |
| **Remote Branches / PRs / Tags** | `main` only; `0` open/closed PRs; `0` tags; `0` workflows | `Verified` |
| **Other `Devmraustro` Repositories** | `Algeria_Map`, `Algerian_Stock_Market`, `Crypto-Analytics-Dashboard`, `EasyOCR`, `Financial_Analysis_Assistant`, `Portfolio` (none contain ERP code) | `Verified` |
| **Execution Environment** | Debian GNU/Linux 12 (bookworm), Linux `6.1.158`, Python `3.11.2`, SQLite `3.40.1`, isolated virtual environment `/tmp/erp-venv` | `Verified` |

---

## 2. Core ERP Backbone Evaluation & Selection (`Verified`)

We evaluated **Odoo Community (`18.0` / `19.0`)** against **ERPNext (`v15` / `v16`)** by inspecting the actual upstream repositories and source files:

1. **`odoo/odoo` (`LGPL-3.0`)**:
   - **18.0 LTS Commit:** `50ff2d0e5db6b9c89e6fbfa86477427b6425b6c3`
   - **19.0 Stable Commit:** `dbe0c5015da3b1f00c25613572fed695d8470d20`
   - Includes `addons/l10n_dz` (`template_dz.py`, 294 accounts in `account.account-dz.csv`, 43 groups in `account.group-dz.csv`, 14 taxes in `account.tax-dz.csv`, 3 tax groups `0%`/`9%`/`19%`, 4 fiscal positions, and `data/tax_report.xml`).
   - Upstream `addons/l10n_dz` has **0 automated tests** and specific SCF/accounting defects (default receivable set to `l10n_dz_413` instead of `l10n_dz_411`, 24 misclassified account types including `519/521/529/591/594` as `asset_cash` and `481` as `asset_receivable`, missing `reconcile=True` on `409/419/421/425`, French group translation errors on groups `32` and `66`, no Arabic account names, and 0 Algerian Wilayas in `base/data/res_country_data.xml`).
   - All of these defects are deterministically remediated by `azinak/l10n-dz-account-fix` (`83056300237a6df09ac001d25cc854179774ede2`), `taghrastech/odoo-l10n-dz` (`252e35942c45e48681d8b8829b23efb049b584fd`), and our `odoo_addons/l10n_dz_scf_extensions` + `dz_erp` core modules.
2. **`frappe/erpnext` (`GPL-3.0`)**:
   - **`v15.123.0` (`e864addb6c367688d2b264f284c2dd9b9360a97d`)**: Contains **zero** Algerian Chart of Accounts files.
   - **`v16.51.1` (`5af6561f5aa48104559692fb4fd82f2580e8d338`)**: Introduces `dz_plan_comptable_general_avec_code.json`, which misclassifies entire SCF Class 1 liabilities (`16` Emprunts, `15` Provisions) under `root_type: "Equity"` and SCF Class 5 bank overdrafts (`519` Concours bancaires courants) under `root_type: "Asset"`. Furthermore, its `GPL-3.0` license is more restrictive for proprietary commercial extensions than `LGPL-3.0`.

**Architectural Decision:** Standardize on **Odoo Community `18.0` LTS** (`LGPL-3.0`) for modular ERP addons (`odoo_addons/l10n_dz_scf_extensions`, `vendor/azinak_l10n_dz_account_fix`, `vendor/taghrastech_odoo_l10n_dz`) paired with our standalone, database-enforced transactional core and FastAPI/OCR intake service (`dz_erp/`), preserving 100% single-tenant (on-premises or dedicated VPC) and multi-tenant deployment portability.

---

## 3. Implemented Repository Architecture (`Verified`)

```text
/home/user/ERP/
├── dz_erp/
│   ├── __init__.py
│   ├── decimal_utils.py       # Strict Decimal arithmetic (ROUND_HALF_UP, 0.01 DZD), rejects binary float
│   ├── database.py            # Transactional SQL schema, immutability triggers, HMAC-SHA256 audit hash-chain
│   ├── localization.py        # Algerian SCF chart loader, conservative idempotent migration, Timbre, G50 workbook
│   ├── odoo_test_adapter.py   # Odoo 18 ORM test adapter executing upstream l10n_dz_account_fix tests
│   ├── intake_ocr.py          # Security checks, Fernet PrivateDocumentVault, pdfplumber + RapidOCR PP-OCRv4
│   ├── procurement_stock.py   # PO, GRN stock moves (380 clearing), 3-way match, SoD approval, idempotent GL post
│   ├── security_audit.py      # Secret-redacting JSON logger, tenant-isolated readers, encrypted backup/restore
│   ├── api.py                 # FastAPI REST endpoints with RBAC, tenant isolation, and Idempotency-Key enforcement
│   └── benchmark.py           # Synthetic Algerian invoice generator & empirical OCR benchmark evaluator
├── odoo_addons/
│   └── l10n_dz_scf_extensions/ # Modular Odoo 18/19 addon extending l10n_dz & l10n_dz_account_fix
├── vendor/
│   ├── odoo_18_l10n_dz/             # Pinned upstream odoo/odoo 18.0 addons/l10n_dz files
│   ├── azinak_l10n_dz_account_fix/  # Pinned azinak/l10n-dz-account-fix 18.0 module & test suite
│   └── taghrastech_odoo_l10n_dz/    # Pinned taghrastech/odoo-l10n-dz 69-Wilaya data
├── tests/
│   ├── test_phase2_localization.py
│   ├── test_phase3_invoice_intake.py
│   ├── test_phase4_procurement_3way_match.py
│   ├── test_phase5_security_audit_backup.py
│   └── test_phase6_ocr_benchmark_e2e.py
├── benchmarks/results/        # Empirical OCR benchmark dataset (18 docs across 6 strata) & JSON report
├── pyproject.toml             # Project configuration & dependency specifications
└── requirements.lock.txt      # Exact pinned lockfile from /tmp/erp-venv
```

---

## 4. Non-Negotiable AI & Financial Safety Boundaries (`Verified`)

1. **AI Assistance vs. Deterministic Accounting:** The OCR/extraction layer (`dz_erp/intake_ocr.py`) writes exclusively to untrusted staging tables (`source_documents`, `ocr_extracted_fields`, `ocr_extracted_lines`). It cannot approve documents, post journal entries, execute payments, or move inventory.
2. **Stock Movement Boundary:** Physical inventory (`stock_moves`, `stock_quants`) is mutated **only** by `receive_goods_against_po` and `deduct_stock_concurrent_safe`. Supplier invoice upload, OCR extraction, validation, approval, and GL posting never create or mutate stock moves; instead, storable invoice lines clear the `380` (*Achats stockés — Compte transitoire*) interim clearing account created at goods receipt.
3. **Database-Level Immutability:** SQL triggers (`trg_audit_no_update`, `trg_audit_no_delete`, `trg_je_immutable_update`, `trg_je_immutable_delete`, `trg_jel_immutable_update`, `trg_jel_immutable_delete`, `trg_ocr_field_preserve_raw`) block in-place tampering or deletion of audit events, posted journal entries, and raw OCR extraction evidence even if application code is bypassed.
