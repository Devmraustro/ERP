# Phase 55 — Algerian Statutory & Accounting Expert Approval Package (Gate G5)

**Repository:** `/home/user/ERP` (`https://github.com/Devmraustro/ERP.git`)  
**Branch & Baseline:** `arena/63d762c8-erp` @ `c60cad0`  
**Prepared Date:** `2026-11-13` (UTC)  
**Release Gate:** **G5 — Algerian Chartered Accountant (`Expert-Comptable` / `Fiscaliste`) Statutory Approval**  
**Current Gate Status:** **`BLOCKED` (Approval Pending — Zero Answers or Sign-Offs Recorded)**

---

## 1. Provenance & Governance Rules

1. **Source Verification (`docs/compliance/EXPERT_REVIEW_QUESTIONS.md`):**
   - Full Git history and all remote refs (`origin/main`, `origin/arena/*`) of `Devmraustro/ERP` were inspected (`git log --all --full-history -- docs/compliance/EXPERT_REVIEW_QUESTIONS.md`). That path does **not** exist in this GitHub repository (it belonged to the separate local clone `C:\Users\rabzd\Documents\ERP`).
   - Rather than inventing replacement questions, this package uses **verbatim** the canonical tracked compliance dossiers in this repository:
     - `docs/release/phase10/EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` (*Part A: 18 Rules `VAT-01`–`DATE-01`* and *Part B: Original `Q1`–`Q12`*)
     - `docs/release/phase46/ALGERIAN_EXPERT_APPROVAL_REGISTER.md` (*Code-Level Statutory Assumptions `Q1`–`Q12`*)
     - `docs/COMPLIANCE_REVIEW_PACKAGE.md` and `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`
2. **Strict Non-Fabrication Guarantee:**
   - **No expert answer, professional name, ONEC registration number, firm name, signature, or approval date has been entered.**
   - **No unverified Algerian statute, decree, or DGI circular number is asserted.** Where the repository contains no verified legal citation, the source column explicitly states **`NONE in repository — requires expert verification`**.
   - Until a licensed Algerian `Expert-Comptable` (ONEC) / `Fiscaliste` completes and signs **Section 6**, **Gate G5 remains `BLOCKED`**, no merge to `main` or production deployment is permitted under production release gates, and the software must **not** be described as Algerian-tax-certified.

---

## 2. Canonical Statutory & Accounting Questions (`Q1`–`Q12` — Code-Level Register)

Every line reference below was re-verified against `dz_erp/` at commit `c60cad0`.

### Q1 — Stamp-Duty (`Droit de Timbre`) Bracket Schedule (`TIMBRE-02`)
- **Exact Question for Expert:** *"Are the bracket boundaries (`≤ 30,000 DZD` at `1.0%`, `30,000.01–100,000 DZD` at `1.5%`, `> 100,000 DZD` at `2.0%`) the current lawful schedule for the fiscal period under review, and is the rate applied to the entire base (`taux effectif global` per bracket) or marginally slice-by-slice?"*
- **Code Path:** `dz_erp/localization.py:150–154` (`DEFAULT_TIMBRE_BRACKETS`), `:509–543` (`calculate_droit_de_timbre`), seeded at `:395` (`LF-2025-CASH-TIMBRE`).
- **Current Runtime Behavior & Numerical Examples (verified live):** Applies the **single rate of the matching bracket to the entire HT+TVA base** (`ROUND_HALF_UP` to `0.01 DZD`):
  - Cash base `10,000.00 DZD` → `10,000.00 × 1.0% = 100.00 DZD`
  - Cash base `50,000.00 DZD` → `50,000.00 × 1.5% = 750.00 DZD` *(note: marginal slice calculation would instead yield `300 + 300 = 600.00 DZD` — expert must confirm full-base vs marginal slice!)*
  - Cash base `200,000.00 DZD` → `200,000.00 × 2.0% = 4,000.00 DZD`
- **Automated Test Reference:** `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook`
- **Documented Source in Repo:** Seeded string label `LF-2025-CASH-TIMBRE` (`dz_erp/localization.py:391`); **no verified statutory article text in repository**.
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q2 — `300.00 DZD` Stamp-Duty Exemption Threshold (`TIMBRE-04`)
- **Exact Question for Expert:** *"Does the `300.00 DZD` exemption threshold currently apply, is the boundary inclusive (`≤ 300.00 DZD` exempt) or exclusive (`< 300.00 DZD`), and does it apply to all cash invoices?"*
- **Code Path:** `dz_erp/localization.py:515, 524–525` (`if base <= to_decimal(threshold): return Decimal("0.00")`).
- **Current Runtime Behavior & Example:** Cash base `200.00 DZD` → `0.00 DZD`; cash base `300.00 DZD` → `0.00 DZD`; cash base `300.01 DZD` → `5.00 DZD` (via minimum floor).
- **Automated Test Reference:** `tests/test_phase2_localization.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q3 — `5.00 DZD` Minimum Stamp-Duty Floor & Maximum Cap (`TIMBRE-03`)
- **Exact Question for Expert:** *"Is `5.00 DZD` the current lawful minimum stamp duty above the exemption threshold, and is there a statutory maximum cap (`max_duty`, e.g., `2,500 DZD` or `10,000 DZD` or uncapped) that must be enforced on cash payments?"*
- **Code Path:** `dz_erp/localization.py:513–514, 536–543` (`min_duty="5.00"`, `max_duty=None` by default).
- **Current Runtime Behavior & Example:** Cash base `400.00 DZD` (`1% = 4.00 DZD`) is raised to `5.00 DZD`; cash base `1,000,000.00 DZD` produces `20,000.00 DZD` uncapped unless `max_duty` is supplied.
- **Automated Test Reference:** `tests/test_phase2_localization.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q4 — Payment-Method Scope of Stamp Duty (`TIMBRE-01`)
- **Exact Question for Expert:** *"Is `Droit de Timbre` strictly restricted to cash (`CASH` / espèces) settlements, and how must cash deposits at a bank counter (`versement espèces en banque`) vs `VIREMENT` / `CHEQUE` be classified?"*
- **Code Path:** `dz_erp/localization.py:522–523` (`if payment_method.upper() != "CASH": return Decimal("0.00")`).
- **Current Runtime Behavior & Example:** `payment_method="VIREMENT"` on `200,000.00 DZD` → `0.00 DZD`; `payment_method="CASH"` on `200,000.00 DZD` → `4,000.00 DZD`.
- **Automated Test Reference:** `tests/test_phase2_localization.py`, `tests/test_phase6_ocr_benchmark_e2e.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q5 — Stamp-Duty Calculation Base (`HT + TVA` vs `HT`)
- **Exact Question for Expert:** *"Is the lawful base for `Droit de Timbre` the VAT-inclusive total (`HT + TVA`) as currently coded, or `HT` alone?"*
- **Code Path:** `dz_erp/procurement_stock.py:611, 997, 2076` (`calculate_droit_de_timbre(ht_dec + tva_dec, ...)`).
- **Current Runtime Behavior & Example:** Line `HT = 10,000.00 DZD`, `TVA 19% = 1,900.00 DZD` → timbre base is `11,900.00 DZD` → cash timbre `119.00 DZD`, `TTC = 12,019.00 DZD`.
- **Automated Test Reference:** `tests/test_phase3_invoice_intake.py`, `tests/test_phase6_ocr_benchmark_e2e.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q6 — Admissible Algerian TVA Rates (`VAT-01`, `VAT-06`)
- **Exact Question for Expert:** *"Are `19%`, `9%`, and `0%` the only admissible TVA rates for domestic supplier invoices in scope, and which mandatory exemption codes/mentions must be captured on `0%` exempt operations for G50 reporting?"*
- **Code Path:** `dz_erp/procurement_stock.py:552, 1973` (`allowed_rates = {Decimal("0.0"), Decimal("9.0"), Decimal("19.0")}` → `INVALID_ALGERIAN_TVA_RATE`), `dz_erp/localization.py:366–372`.
- **Current Runtime Behavior & Example:** Rates outside `{0, 9, 19}` are rejected at validation; mixed-rate invoice (`2 × 1,000 @ 19%`, `1 × 1,000 @ 9%`, `1 × 500 @ 0%`) yields `HT = 3,500.00 DZD`, `TVA = 470.00 DZD`, `TTC = 3,970.00 DZD`.
- **Automated Test Reference:** `tests/test_phase2_localization.py`, `tests/test_phase3_invoice_intake.py`
- **Documented Source in Repo:** Vendored Odoo `l10n_dz` CSV (`vendor/azinak_l10n_dz_account_fix/`); **not independently certified by an Algerian tax advisor**.
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q7 — Per-Line TVA Rounding vs Header-Level Rounding & Supplier Tolerance (`VAT-02`, `VAT-03`, `VAT-04`)
- **Exact Question for Expert:** *"Is `ROUND_HALF_UP` to `0.01 DZD` per line followed by summation the required convention, and when a supplier's printed header TVA/TTC differs by `0.01–2.00 DZD` due to total-level rounding, must the invoice be rejected or accepted within a defined tolerance?"*
- **Code Path:** `dz_erp/decimal_utils.py` (`round_dzd`), `dz_erp/localization.py:546` (`compute_invoice_totals`), `dz_erp/procurement_stock.py:1943, 2040` (`_validate_invoice_lines`, `_validate_invoice_totals`).
- **Current Runtime Behavior:** Sums rounded per-line HT and TVA and refuses header/line mismatches beyond exact centime coherence.
- **Automated Test Reference:** `tests/test_phase2_localization.py`, `tests/test_phase3_invoice_intake.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q8 — Commercial Discount Before TVA
- **Exact Question for Expert:** *"Is applying line commercial discounts before computing the taxable TVA base (`qty × unit_price × (1 − discount%/100)`) the required SCF and TVA treatment, and how must financial/settlement discounts (`escompte de règlement`) be handled if encountered?"*
- **Code Path:** `dz_erp/procurement_stock.py:27–40` (`compute_line_ht_dzd`).
- **Current Runtime Behavior & Example:** `10 × 1,000.00 DZD` with `discount_pct = 5.00` → `line_ht = 9,500.00 DZD`, `TVA 19% = 1,805.00 DZD`.
- **Automated Test Reference:** `tests/test_phase3_invoice_intake.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q9 — Reversal / Credit-Note (`Avoir`) Convention (`Part B Q8`)
- **Exact Question for Expert:** *"Is recording reversals and supplier credit notes exclusively as positive-amount `IN_REFUND` documents that generate a new counter-balancing journal entry (`contre-passation`) while leaving the original posted entry untouched acceptable under SCF audit rules?"*
- **Code Path:** `dz_erp/procurement_stock.py:1377, 1554, 1984` (`reverse_posted_invoice`, `create_reversal_request`, `post_reversal`), SQLite triggers `trg_je_immutable_update` / `trg_je_immutable_delete`.
- **Current Runtime Behavior:** Negative invoice lines/headers are refused; reversals require maker-checker approval and post a separate linked reversal entry.
- **Automated Test Reference:** `tests/test_phase4_procurement_3way_match.py`, `tests/test_phase5_security_audit_backup.py`
- **Documented Source in Repo:** SCF immutability design note in `docs/COMPLIANCE_REVIEW_PACKAGE.md`; **requires `Expert-Comptable` sign-off**.
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q10 — Governing Date for Statutory Rule Changes & Late-Received Invoices (`VAT-05`, `DATE-01`)
- **Exact Question for Expert:** *"When TVA or stamp-duty rules change—or when a supplier invoice dated in month `N` arrives in month `N+1` after month `N` is filed—which date governs the applicable tax rule and fiscal period (`invoice_date`, `reception_date`, or `posting_date`)?"*
- **Code Path:** `dz_erp/database.py` (`timbre_rules`), `dz_erp/localization.py:395`, `dz_erp/procurement_stock.py:611, 997`.
- **Current Runtime Behavior (Explicit Engineering Disclosure):** `invoice_date` determines `journal_entries.period` (`YYYY-MM`), **however** `calculate_droit_de_timbre` is currently called without consulting `timbre_rules` effective dates (`DEFAULT_TIMBRE_BRACKETS` compiled-in constant is always used). Wiring effective-dated rule lookup (`ALGERIAN_TIMBRE_PROVENANCE_v2`) is intentionally held until the expert rules on whether `invoice_date` or `posting_date` governs.
- **Automated Test Reference:** `tests/test_phase27_scf_first_run.py`
- **Documented Source in Repo:** Disclosed in `docs/release/phase46/ALGERIAN_EXPERT_APPROVAL_REGISTER.md` §5.
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q11 — Historical Provenance Freeze After Statutory Rule Change (`TIMBRE-05`)
- **Exact Question for Expert:** *"Once an invoice is posted, must its applied stamp-duty/TVA rule provenance be permanently frozen (never recalculated if a rule table is edited later), and what rule-version reference must appear on audit exports?"*
- **Code Path:** `supplier_invoices.amount_timbre_dzd` persisted at validation/posting; `journal_entries` immutable via SQLite triggers; explicit rule-version column freeze (`assert_timbre_rule_unchanged`) pending expert confirmation of Q10/Q11.
- **Automated Test Reference:** `tests/test_phase5_security_audit_backup.py`
- **Documented Source in Repo:** **NONE in repository — requires expert verification.**
- **Expert Verdict / Response:** *(Blank — pending expert)*

### Q12 — SCF Account Mapping (`294` Accounts, `380` vs `408` GR/IR, `4456`/`4457`) & `G50` Box Tags (`VAT-07`, `G50-01`–`G50-03`, `ACCOUNT-01`, `ACCOUNT-02`)
- **Exact Question for Expert:**
  1. *"Is the `294`-account SCF base chart (`dz_erp/localization.py:272`) acceptable for the company's statutory `Bilan` and `Compte de Résultat`?"*
  2. *"For goods received not yet invoiced (`factures non parvenues` / purchases clearing), must the system credit `380xxx` (`Achats stockés` clearing) or `408xxx` (`Fournisseurs — factures non parvenues`)?"*
  3. *"Do `4456` (deductible TVA), `4457` (collected TVA), `E3B91_base`, `E3B91_balance`, `E3B92_base`, `E3B92_balance`, and `TIMBRE_PURCHASE` (`dz_erp/localization.py:366–372, 606–672`; `dz_erp/procurement_stock.py:1159`) map line-for-line to the current DGI `Série G n°50` (`G50`) declaration boxes?"*
- **Code Path:** `dz_erp/localization.py:272–375, 411–505, 606–675`; `dz_erp/procurement_stock.py:980–1170`.
- **Automated Test Reference:** `tests/test_phase2_localization.py`, `tests/test_phase4_procurement_3way_match.py`, `tests/test_phase27_scf_first_run.py`
- **Documented Source in Repo:** Vendored Odoo `l10n_dz` + `azinak_l10n_dz_account_fix` (`vendor/azinak_l10n_dz_account_fix/`); **not independently certified by an ONEC `Expert-Comptable`**.
- **Expert Verdict / Response:** *(Blank — pending expert)*

---

## 3. Additional Governance & Regulatory Questions from Phase 10 Canonical Dossier (`Part B Q4, Q5, Q7, Q9, Q11, Q12`)

To ensure zero loss of scope from `docs/release/phase10/EXPERT_COMPLIANCE_REVIEW_PACKAGE.md`, the following additional operational/regulatory questions remain open for expert & legal sign-off:

| Ref | Canonical Question (`docs/release/phase10/EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` Part B) | Current V1 Code Posture | Status |
|---|---|---|---|
| **P10-Q4** | Are imports (`D10` customs declarations, reverse-charge TVA) and exempt regimes in or out of scope for this deployment? | Out of V1 scope (domestic DZD supplier procurement only). | **PENDING** |
| **P10-Q5** | What is the exact monthly `G50` filing deadline (e.g., 20th of month `N+1`) and late-period declaration procedure for the company's tax regime? | Period strings (`YYYY-MM`) are configurable; no statutory deadline is hard-coded. | **PENDING** |
| **P10-Q7** | Must the `Grand Livre` and `Livre-Journal` be printable/exportable in a prescribed official layout and column set (`Code de Commerce` / SCF)? | Database queries and G50 workbook export exist; statutory print/cote-et-paraphe layout is uncertified. | **PENDING** |
| **P10-Q9** | What mandatory retention duration applies to accounting ledgers, audit chains, and scanned supplier invoices (e.g., 10-year `Code de Commerce` retention)? | Configurable in `dz_erp/ops_backup.py` & `docs/release/phase10/DATA_RETENTION_POLICY.md`; legal sign-off pending. | **PENDING** |
| **P10-Q11** | Under Algerian Data Protection Law `18-07` (`ANPDP`), what declarations/registers are required for supplier & user personal data? | Encryption at rest (Fernet vault), RBAC, redacting logs, and on-premise storage implemented; legal/DPO filing pending. | **PENDING** |
| **P10-Q12** | Are Arabic-language statutory reports/vouchers mandatory alongside French UI/exports, and in what format? | Desktop UI supports French/Arabic (`Noto Naskh Arabic` bundled); Arabic image OCR fails closed to human transcription. | **PENDING** |

---

## 4. Items Explicitly Flagged for Professional Judgment Before Production

1. **Full-base vs marginal-slice `Droit de Timbre` calculation (`Q1`)** and **maximum cap (`Q3`)**.
2. **Activation of effective-dated `timbre_rules` lookup (`Q10` & `Q11`)** once the governing date (`invoice_date` vs `posting_date`) is ruled on by the expert.
3. **SCF clearing account selection (`380` vs `408`) at fiscal year-end (`Q12`)** and **hard fiscal-period closing workflow (`DATE-01`)**.
4. **Exact `Série G n°50` (`G50`) box mapping (`E3B91`, `E3B92`, `E3B100`–`E3B130`, `TIMBRE_PURCHASE`) (`Q12`)**.

---

## 5. Reviewer Verification Commands (Synthetic Data — Zero Production Secrets Required)

A reviewing `Expert-Comptable` or technical auditor can reproduce every calculation above offline:

```bash
python -m pytest tests/test_phase2_localization.py tests/test_phase3_invoice_intake.py tests/test_phase4_procurement_3way_match.py tests/test_phase27_scf_first_run.py -q
python tools/staging_acceptance.py
```

---

## 6. Official Expert Sign-Off Block *(Intentionally Blank — Gate G5 Remains `BLOCKED` Until Executed)*

```text
================================================================================
ALGERIAN STATUTORY & ACCOUNTING COMPLIANCE SIGN-OFF (GATE G5)
================================================================================
Expert-Comptable Full Name:        _____________________________________________
ONEC Registration / Agrément No.:  _____________________________________________
Cabinet / Firm Name & Address:     _____________________________________________
Tax Advisor (Fiscaliste) Name:     _____________________________________________
Legal / Law 18-07 Counsel Name:    _____________________________________________

Reviewed Commit SHA:               _____________________________________________
Review Date (YYYY-MM-DD):          _____________________________________________

Per-Question Determinations Attached (Q1–Q12 & P10-Q4/5/7/9/11/12): [ ] YES  [ ] NO

Overall Statutory Determination:
  [ ] APPROVED FOR PRODUCTION USE IN ALGERIA
  [ ] APPROVED WITH MANDATORY CODE CONDITIONS (attach change list)
  [ ] REJECTED — DO NOT USE FOR STATUTORY FILINGS

Conditions / Statutory Citations Provided by Expert:
  ______________________________________________________________________________
  ______________________________________________________________________________

Authorized Signature & Official Stamp (Cachet):


  _____________________________________________
================================================================================
```
