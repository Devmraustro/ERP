# Algeria Compliance Gate Checklist

**Status of this document:** an evidence-based register of what the software *does*, separated from
what still requires **qualified Algerian professional review**.
**It is not a legal opinion, certification or approval.** No item below has been reviewed by an
Algerian accountant, auditor, tax adviser or data-protection counsel.

**How to read the "Software evidence" column:** each entry names concrete code and a test or
command that can be re-run. "Implemented" means the behaviour exists and is tested; it does **not**
mean the behaviour is legally correct.

**Blocking definitions**

* **Blocks production** — the item must be closed by a named reviewer before a real company's books
  or personal data go through this system.
* **Does not block staging** — synthetic data in an isolated environment is acceptable without it.

---

## 1. Statutory accounting (SCF)

| # | Item | Software evidence | Outstanding professional question | Responsible reviewer | Required evidence / approval | Blocks production |
|---|---|---|---|---|---|---|
| 1.1 | SCF chart of accounts mapping | `dz_erp/localization.py` loads a vendored Odoo DZ chart (`vendor/odoo_18_l10n_dz`, `vendor/azinak_l10n_dz_account_fix`); `load_official_odoo_chart()`; tested in `tests/test_phase2_localization.py` | Are the account codes, classes and grouping the ones a DZ statutory filing requires, as amended for the current SCF? | Expert-comptable (Algerian chartered accountant) | Signed mapping table: every account code used in posting → SCF class/account | **Yes** |
| 1.2 | Journal codes and entry sequencing | `procurement_stock.py` posting functions; `journal_entries.journal_code`, `entry_number` constrained per company | Are the journal codes (purchase, cash, miscellaneous) and numbering legal for statutory books? | Expert-comptable | Written confirmation + sample journal export | **Yes** |
| 1.3 | Decimal precision and DZD rounding | `dz_erp/decimal_utils.py` (`Decimal`-only arithmetic, `round_dzd`); no float in posting paths; tested across the suite | Is 2-decimal DZD rounding with the chosen method acceptable for VAT and timbre aggregation? | Expert-comptable | Confirmation of rounding rule and its documentation in the accounting policy | **Yes** |
| 1.4 | Balanced entries and immutability | `journal_entries` CHECK (`total_debit_dzd = total_credit_dzd`), immutability triggers, `FINANCIAL_INTEGRITY_VIOLATION`; `tests/test_phase4_procurement_3way_match.py`, `test_phase13_key_rotation_regression.py` | None for software behaviour. Legal question: does the audit trail satisfy statutory record-keeping? | Expert-comptable / statutory auditor | Confirmation that the audit trail is acceptable evidence | **Yes** |
| 1.5 | **Clearing accounts 380 vs 408** | `localization.py:76` defines `380` "Stored purchases (Clearing)"; `:78` defines `408` "Suppliers – invoices not yet received"; `products.clearing_account_code` defaults to `380` | **Which account is correct for goods received not invoiced (GRNI) in this company's SCF, and when does 408 apply instead?** The code currently has both accounts and defaults inventory clearing to 380. | Expert-comptable | Written treatment decision per transaction type, with the SCF reference | **Yes** |
| 1.6 | Reversal / credit-note treatment | Reversal workflow with approval + segregation of duties (`procurement_stock.create_reversal_request` … `post_reversal`); `tests/test_phase4_procurement_3way_match.py` | Must a reversal be a credit note, an extourne, or either, per DZ practice? | Expert-comptable | Documented policy + approval of the implemented workflow | **Yes** |

---

## 2. Tax

| # | Item | Software evidence | Outstanding professional question | Responsible reviewer | Required evidence / approval | Blocks production |
|---|---|---|---|---|---|---|
| 2.1 | VAT rates and treatment | `taxes` table seeded by `localization.py` (rate, `type_tax_use`, `exemption_reason`, `g50_*_tag`); `calculate_invoice_totals` | Are the seeded rates (e.g. 19 %) and exemption categories current and complete for this company's activity? | Tax adviser (fiscaliste) | Rate table approval with effective dates | **Yes** |
| 2.2 | VAT on the invoice line vs header | Lines carry `line_tva_dzd`; header `amount_tva_dzd`; totals validated against line sums (`validate_invoice_totals`) | Does the statutory invoice layout require per-line VAT to be shown, and to what precision? | Fiscaliste + expert-comptable | Approved invoice template | **Yes** |
| 2.3 | **Stamp duty (droit de timbre)** | `calculate_droit_de_timbre()`; `timbre_rules` table with versioned brackets (`LF-2025-CASH-TIMBRE`, `DEFAULT_TIMBRE_BRACKETS`), thresholds 5.00/300.00 DZD, applied to cash payments | Are the brackets, thresholds, rounding and the cash-payment trigger still those of the current Finance Law? Are they applied to the right base (TTC vs HT)? | Fiscaliste | Current Finance Law citation + worked examples replayed against `calculate_droit_de_timbre` | **Yes** |
| 2.4 | **G50 declaration aggregation** | `generate_g50_preparation_workbook()` aggregates posted entries by `g50_tag` into `E2_droit_de_timbre` and related fields, tenant-scoped | Is the mapping from journal lines to G50 boxes complete and correct, including TVA collectée/déductible, timbre and any withholding? Is the period cut-off right? | Fiscaliste | Approved box-by-box mapping + a reconciliation of one real period | **Yes** |
| 2.5 | Withholding taxes, IFU regime | `partners.tax_regime` supports `REEL`, `IFU`, `EXEMPT`, `FOREIGN`; no withholding calculation is implemented | Does this deployment need withholding (e.g. on services/foreign payments)? If so it is **not implemented**. | Fiscaliste | Statement of applicable withholdings; implementation request if needed | **Yes** |

---

## 3. Invoicing requirements

| # | Item | Software evidence | Outstanding professional question | Responsible reviewer | Required evidence / approval | Blocks production |
|---|---|---|---|---|---|---|
| 3.1 | Mandatory mentions on a supplier invoice | `intake_ocr.parse_structured_invoice_text` extracts NIF, NIS, AI, RC, invoice number, date, totals; `validate_algerian_identifiers` checks identifier formats | Are all mandatory mentions captured/validated, and is the *issued* document (if this system issues invoices) compliant? | Expert-comptable / fiscaliste | Checklist of mandatory mentions signed off against a real invoice | **Yes** |
| 3.2 | Sequential numbering, duplicates | `normalized_invoice_no` + partial unique index `uq_posted_supplier_invoice`; duplicate detection in `create_and_validate_supplier_invoice` | Does the duplicate rule match the statutory identity of an invoice (supplier + number + fiscal year)? | Expert-comptable | Confirmation | **Yes** |
| 3.3 | Retention of original documents | Vault stores the original upload encrypted at rest; `source_documents` records SHA-256, MIME, size, page count; raw OCR preserved immutably (trigger `trg_ocr_field_preserve_raw`) | What retention period applies, and does an encrypted-at-rest copy satisfy it? | Expert-comptable + DPO | Retention policy approval | **Yes** |

---

## 4. Personal data — Law 18-07 and the ANPDP

| # | Item | Software evidence | Outstanding professional question | Responsible reviewer | Required evidence / approval | Blocks production |
|---|---|---|---|---|---|---|
| 4.1 | Lawful basis and register of processing | **Not implemented.** No processing register, consent or lawful-basis record exists in the code. | What is the lawful basis for processing supplier contacts, employee accounts and any OCR-extracted personal data? | DPO / legal counsel | Completed processing register per Law 18-07 | **Yes** |
| 4.2 | Data-subject rights (access, rectification, objection) | **Not implemented** as a workflow. Only tenant-scoped read APIs and the audit trail exist. | What procedure and SLA apply, and what must the software support? | DPO | Documented procedure + required software changes | **Yes** |
| 4.3 | **ANPDP formalities** | **Not implemented.** No filing/authorisation artefact, no ANPDP reference stored. | Which formalities (declaration/authorisation) are required for this processing, and have they been completed? | DPO / legal counsel | Evidence of the required ANPDP filing/authorisation | **Yes** |
| 4.4 | Personal-data minimisation in logs | `StructuredRedactingLogger` redacts `password`, `token`, `Authorization`, `encryption_key`, `hmac_secret`, `bank_rib`, `raw_text`, plus patterns for bearer tokens, DZ IBAN/RIB and 18/20-digit identifiers; `tests/test_phase5_security_audit_backup.py`, `test_phase13_key_rotation_regression.py` | Are the redaction patterns sufficient for the identifier types actually stored (NIN, NIF, RIB, phone, email)? | DPO + security engineer | Reviewed redaction list against the data inventory | **Yes** |
| 4.5 | Access control and least privilege | Bearer-token authentication (`dz_erp/auth.py`), server-side tenant/company scoping, RBAC + segregation of duties, revocation, lockout; `tests/test_phase7_authentication_remediation.py` | Does the role model match the organisation's confidentiality requirements? | DPO + security officer | Role matrix approval | **Yes** |
| 4.6 | **Cross-border hosting / transfer** | **Undetermined.** Nothing in the code constrains where it runs; keys and database are local files. | Where will the database, backups and key material be hosted? Does that transfer require authorisation under Law 18-07? | DPO / legal counsel | Hosting decision + transfer assessment | **Yes** |
| 4.7 | Retention and secure deletion | Retention is configurable only for the security log (`ERP_SECURITY_LOG_MAX_SIZE`, `ERP_SECURITY_LOG_MAX_FILES`). **No business-record retention or erasure policy is implemented.** Financial records are immutable by design. | What retention periods apply per record class, and how is erasure reconciled with statutory book-keeping? | Expert-comptable + DPO | Retention schedule; documented conflict resolution | **Yes** |
| 4.8 | Backup confidentiality | Backups are Fernet-encrypted; keys travel inside the archive; manifest lists versions only | Who holds the archive key, and is that custody documented? | DPO + security officer | Key-custody procedure | **Yes** |

---

## 5. Technical controls that support compliance (implemented, not legal proof)

| Control | Evidence | Status |
|---|---|---|
| Immutable posted journal entries | DB triggers; `FINANCIAL_INTEGRITY_VIOLATION`; tested | Implemented and tested |
| Tamper-evident audit chain with per-event key version | `dz_erp/database.py`, `dz_erp/key_rotation.py`; `tests/test_phase11_audit_key_rotation.py` | Implemented and tested |
| Encrypted-at-rest documents with versioned keys and rotation | `dz_erp/intake_ocr.py`; `tests/test_phase10_vault_key_rotation.py` | Implemented and tested |
| Encryption of backups with verified restore | `dz_erp/security_audit.py`; `tests/test_phase12_key_migration_and_backup.py` | Implemented and tested |
| OCR cannot post, approve or pay | OCR writes review data only; posting requires human approval + RBAC; staging check `ocr: extraction creates no invoice and no journal entry` | Implemented and tested |
| Segregation of duties on approval and reversal | `approval_requests` CHECK; reversal workflow; tested | Implemented and tested |
| API documentation disabled by default | `create_erp_fastapi_app(docs_enabled=False)`; 404 asserted | Implemented and tested |
| Durable security log with rotation/retention | `JsonlSecurityLog`; `tests/test_phase9_durable_security_log.py` | Implemented and tested |
| Fail-closed key handling (no silent regeneration) | `tests/test_phase9_process_boundary.py`, `test_phase10`, `test_phase12` | Implemented and tested |

---

## 6. What this document deliberately does not claim

* It does **not** assert that the SCF mapping, VAT rates, stamp duty brackets or G50 aggregation are
  legally correct.
* It does **not** assert conformance with Law 18-07, nor that any ANPDP formality has been
  completed.
* It does **not** convert a passing test suite into an accounting or legal approval. A green build
  proves the software behaves as specified; it says nothing about whether the specification matches
  Algerian law.
* OCR accuracy is not claimed. The benchmark covers 18 synthetic documents; human review is
  mandatory in the workflow.

## 7. Sign-off block (to be completed by qualified reviewers)

| Gate | Reviewer name / body | Date | Evidence reference | Decision |
|---|---|---|---|---|
| SCF account mapping (§1) | | | | |
| Clearing account 380 vs 408 (§1.5) | | | | |
| VAT rates and treatment (§2) | | | | |
| Stamp duty brackets (§2.3) | | | | |
| G50 aggregation (§2.4) | | | | |
| Invoicing mentions (§3) | | | | |
| Law 18-07 register and rights (§4.1–4.2) | | | | |
| ANPDP formalities (§4.3) | | | | |
| Cross-border hosting (§4.6) | | | | |
| Retention / erasure (§4.7) | | | | |

**No gate above has been signed. All are open.**
