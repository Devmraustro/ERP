# Expert-Comptable Review Package — 18 Rules and 12 Questions

**For:** the company's expert-comptable
**Purpose:** a reviewable inventory of every tax/accounting rule the software
implements, where it lives, what proves it works, and **the exact question** that only
a qualified professional can answer.
**Approval status of every row: PENDING. Legal certification: NOT GIVEN.**

> **No legal source is cited below.** The software vendor has not independently
> verified any Algerian statute, decree or DGI instruction for this release, so the
> "Legal source" column says exactly that. If the expert supplies a citation, it is
> recorded as *their* citation, with their name and date — never generated here.
>
> **What the "Implementation" column means:** the behaviour the code performs today,
> verified by the referenced test. It is a statement about the software, **not** a
> statement that the behaviour is legally correct.

## How to use this package

1. Read one row at a time; open the implementation location if you want to see the code.
2. Answer the question in writing.
3. Record: **APPROVED / APPROVED WITH CONDITIONS / REJECTED**, your name, your
   registration number, the date, and any citation you wish to add.
4. Any REJECTED or conditionally approved row becomes a change request against the
   software; it is not waived.

Baseline reviewed: session branch `arena/7557bcfb-erp` @ `4fc1364` + uncommitted work,
CPython 3.12.10, hash-pinned dependencies. Evidence logs:
`docs/release/phase10/evidence/logs/`.

---

## Part A — The 18 rules

### A.1 Value-added tax (TVA)

| Field | VAT-01 |
| --- | --- |
| **Rule** | Only the statutory TVA rates may be applied to an invoice line |
| **Implementation** | `dz_erp/procurement_stock.py:542` — `if rate not in (Decimal("0.0"), Decimal("9.0"), Decimal("19.0"))` → error `LINE_n_INVALID_ALGERIAN_TVA_RATE` (line 543) |
| **Test reference** | `tests/test_phase3_invoice_intake.py`, `tests/test_phase5_security_audit_backup.py`, `tests/test_phase6_ocr_benchmark_e2e.py` (14-step workflow) |
| **Legal source** | **Not independently verified by the vendor** |
| **Exact expert question** | "Are 0 %, 9 % and 19 % the only rates this company may apply on purchase invoices for the fiscal year under review, and must any other rate (e.g. a specific-rate regime) be representable?" |
| **Status** | **PENDING** |

| Field | VAT-02 |
| --- | --- |
| **Rule** | TVA is computed per line in exact decimal arithmetic, never binary floating point |
| **Implementation** | `dz_erp/decimal_utils.py` (`round_dzd`, `to_decimal`); per-line computation in `dz_erp/procurement_stock.py` validation block |
| **Test reference** | `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| **Legal source** | Not applicable (arithmetic), but the **rounding convention** needs approval — see VAT-06 |
| **Exact expert question** | "Is the DZD rounding convention used here (round half away from zero, 2 decimals, per line then summed) the one you require for declared amounts?" |
| **Status** | **PENDING** |

| Field | VAT-03 |
| --- | --- |
| **Rule** | Header TVA must equal the sum of line TVA, or the invoice is refused |
| **Implementation** | Header reconciliation in `dz_erp/procurement_stock.py` (`create_and_validate_supplier_invoice` → validation errors) |
| **Test reference** | `tests/test_phase3_invoice_intake.py`, `tests/test_phase33_invoice_lifecycle`-equivalent flows inside `tests/test_phase6_ocr_benchmark_e2e.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Must the TVA on a supplier invoice be allocated line by line, or may the supplier's global TVA figure be accepted even when line allocation differs by rounding?" |
| **Status** | **PENDING** |

| Field | VAT-04 |
| --- | --- |
| **Rule** | TTC = HT + TVA (+ timbre where due); a mismatch blocks posting |
| **Implementation** | `dz_erp/procurement_stock.py` header reconciliation, `TOTAL_TTC` check |
| **Test reference** | `tests/test_phase5_security_audit_backup.py` (balanced posting), `tests/test_phase6_ocr_benchmark_e2e.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "When a supplier's TTC differs from HT+TVA by 1–2 DZD, must the invoice be rejected or accepted with a tolerance, and if tolerated, which figure is authoritative?" |
| **Status** | **PENDING** |

| Field | VAT-05 |
| --- | --- |
| **Rule** | The rate applied is the rate in force on the **document's** date, not the system date |
| **Implementation** | Business date comes from the invoice (`invoice_date`); timbre rule rows carry effective dates (`dz_erp/localization.py`, `DEFAULT_TIMBRE_BRACKETS` seeded at chart load, `dz_erp/procurement_stock.py:395`) |
| **Test reference** | `tests/test_phase27_scf_first_run.py`, `tests/test_phase2_localization.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "For a rate change mid-period, which date governs an invoice received late — invoice date, reception date, or booking date?" |
| **Status** | **PENDING** |

| Field | VAT-06 |
| --- | --- |
| **Rule** | Zero-rated and exempt operations are recorded explicitly, not by omitting the field |
| **Implementation** | Rate `0.0` is a first-class value in VAT-01's allowed set; the field is mandatory in extraction/review |
| **Test reference** | `tests/test_phase3_invoice_intake.py`, `tests/test_phase29`-equivalent review policy in `tests/test_phase18_ocr_confidence_provenance.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Which exemption codes must appear on a purchase invoice for the exemption to be valid, and where must they be recorded for the G50?" |
| **Status** | **PENDING** |

| Field | VAT-07 |
| --- | --- |
| **Rule** | TVA is declared on the correct G50 line |
| **Implementation** | Journal lines carry `g50_tag`; posting writes `E3B91_base`, `E3B91_balance`, `TIMBRE_PURCHASE` (`dz_erp/procurement_stock.py:1035,1047,1059`) and account `4456` (line 1043) |
| **Test reference** | `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| **Legal source** | **Not independently verified** |
| **Exact expert question** | "Do `E3B91_base`, `E3B91_balance` and `TIMBRE_PURCHASE` map to the correct G50 lines for this company's regime, and which box receives deductible TVA on purchases?" |
| **Status** | **PENDING** |

### A.2 Droit de timbre

| Field | TIMBRE-01 |
| --- | --- |
| **Rule** | Stamp duty applies only to cash payments |
| **Implementation** | `dz_erp/localization.py:509` `calculate_droit_de_timbre(...)`: `if payment_method.upper() != "CASH": return Decimal("0.00")` |
| **Test reference** | `tests/test_phase2_localization.py`, `tests/test_phase6_ocr_benchmark_e2e.py` (S3 and S6 strata are CASH invoices) |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Is the duty limited to cash settlement, and are 'versement en espèces' and a cash payment recorded through a bank desk treated identically?" |
| **Status** | **PENDING** |

| Field | TIMBRE-02 |
| --- | --- |
| **Rule** | Duty is bracketed, not a flat percentage |
| **Implementation** | `dz_erp/localization.py:150` `DEFAULT_TIMBRE_BRACKETS = [{≤30 000, 1 %}, {≤100 000, 1.5 %}, {>100 000, 2 %}]` |
| **Test reference** | `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| **Legal source** | **Not independently verified — this is a money-to-the-treasury figure and must be confirmed** |
| **Exact expert question** | "Are these bracket boundaries and rates the ones in force for the reviewed period, and is the rate applied to the whole base or marginally per bracket?" |
| **Status** | **PENDING** |

| Field | TIMBRE-03 |
| --- | --- |
| **Rule** | A minimum duty floor is enforced |
| **Implementation** | `dz_erp/localization.py:513` `min_duty: str = "5.00"` (with `max_duty` optional, default None) |
| **Test reference** | `tests/test_phase2_localization.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Is the 5,00 DZD floor current, and is there a cap (`max_duty`) that this deployment should configure instead of leaving it unset?" |
| **Status** | **PENDING** |

| Field | TIMBRE-04 |
| --- | --- |
| **Rule** | An exemption threshold is honoured |
| **Implementation** | `dz_erp/localization.py:513` `threshold: str = "300.00"`: duty is 0 when HT+TVA ≤ threshold |
| **Test reference** | `tests/test_phase2_localization.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Is the exemption threshold still 300,00 DZD, is it applied to HT+TVA, and does it apply to all cash payments or only to specific receipt types?" |
| **Status** | **PENDING** |

| Field | TIMBRE-05 |
| --- | --- |
| **Rule** | The rule actually used is recorded with the invoice, so later changes cannot rewrite history |
| **Implementation** | Brackets persisted per company at chart load (`dz_erp/procurement_stock.py:395`), audit event on load; invoice stores `amount_timbre_dzd` |
| **Test reference** | `tests/test_phase27_scf_first_run.py`, `tests/test_phase2_localization.py` |
| **Legal source** | Not applicable (provenance property) |
| **Exact expert question** | "If you change a bracket mid-year, should already-posted invoices keep the old bracket (as they do now), and do you require a printed trace of which rule version was applied?" |
| **Status** | **PENDING** |

### A.3 G50 declaration

| Field | G50-01 |
| --- | --- |
| **Rule** | The declaration aggregates the correct accounts |
| **Implementation** | `g50_tag` on journal lines; aggregation over POSTED entries only; report code in `dz_erp/localization.py` / trial-balance path |
| **Test reference** | `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Does the G50 aggregation in the workbook this software produces match the boxes you file, line for line?" |
| **Status** | **PENDING** |

| Field | G50-02 |
| --- | --- |
| **Rule** | Filing period and deadline are configurable and effective-dated |
| **Implementation** | Period/tax settings are configuration rows; no statutory date is hard-coded |
| **Test reference** | `tests/test_phase2_localization.py` (fiscal configuration), `tests/test_phase27_scf_first_run.py` |
| **Legal source** | **Not independently verified** |
| **Exact expert question** | "What is the filing deadline for this company's regime, and does a missed deadline change how a late period must be declared?" |
| **Status** | **PENDING** |

| Field | G50-03 |
| --- | --- |
| **Rule** | The G50 output reconciles to the trial balance for the period |
| **Implementation** | Both are computed from POSTED entries only; the trial balance refuses an unbalanced aggregate |
| **Test reference** | `tests/test_phase2_localization.py`, `tests/test_phase5_security_audit_backup.py` |
| **Legal source** | Not applicable (internal consistency) |
| **Exact expert question** | "Which reconciliations do you personally perform before signing a G50, so the software can produce the same evidence?" |
| **Status** | **PENDING** |

### A.4 Chart of accounts and dates

| Field | ACCOUNT-01 |
| --- | --- |
| **Rule** | The chart follows the SCF structure |
| **Implementation** | `dz_erp/localization.py:272` `load_official_odoo_chart(...)` loads the vendored official chart; first run creates 294 accounts (measured) |
| **Test reference** | `tests/test_phase27_scf_first_run.py::test_first_run_seeds_official_chart_exactly`, `::test_double_chart_load_is_idempotent` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Is the vendored chart (class/code structure, 294 accounts) acceptable as the company's base chart, and which accounts must be added or removed for this activity?" |
| **Status** | **PENDING** |

| Field | ACCOUNT-02 |
| --- | --- |
| **Rule** | Accounts map correctly to the statutory statements (bilan / compte de résultat) |
| **Implementation** | Account classes/statement mapping come from the chart definition; posting uses `401`, `4456`, `380`, `601`-class accounts |
| **Test reference** | `tests/test_phase27_scf_first_run.py`, `tests/test_phase4_procurement_3way_match.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Is the class→statement mapping correct for the SCF presentation you file, and must '380' or '408' be used for goods received not invoiced?" (**open item recorded in the compliance checklist §1.5**) |
| **Status** | **PENDING** |

| Field | DATE-01 |
| --- | --- |
| **Rule** | Business dates come from the document and the fiscal period, not the entry timestamp |
| **Implementation** | `invoice_date` drives period assignment; `posted_at` is server time and is used only for the audit trail |
| **Test reference** | `tests/test_phase3_invoice_intake.py`, `tests/test_phase6_ocr_benchmark_e2e.py` |
| **Legal source** | Not independently verified |
| **Exact expert question** | "Which date determines the fiscal period for a supplier invoice received in month N+1 for month N, and how must a period already closed be handled?" |
| **Status** | **PENDING** |

---

## Part B — The 12 questions

| # | Question | Why it blocks | Status |
| --- | --- | --- | --- |
| Q1 | Are the timbre brackets/rates in `DEFAULT_TIMBRE_BRACKETS` current for the period under review? | every cash invoice carries a treasury liability | **PENDING** |
| Q2 | Does the 300,00 DZD exemption threshold still apply, and exactly to which payment means? | changes the duty on most small cash purchases | **PENDING** |
| Q3 | Which G50 line receives each `g50_tag` in use (`E3B91_base`, `E3B91_balance`, `TIMBRE_PURCHASE`)? | misclassification is a filing error | **PENDING** |
| Q4 | Are imports and exempt operations in or out of this deployment's scope? | V1 covers domestic purchase invoices only | **PENDING** |
| Q5 | Are the configured filing periods/deadlines authoritative for this regime? | late filing penalties | **PENDING** |
| Q6 | Does the chart satisfy the bilan/compte de résultat presentation you file? | statutory accounts format | **PENDING** |
| Q7 | Must a "grand livre" be printable in a specific form, and with what columns? | audit acceptance | **PENDING** |
| Q8 | Is the reversal convention (contre-passation, new entry, original untouched) acceptable to an auditor? | audit trail interpretation | **PENDING** |
| Q9 | What retention period applies to accounting records and source documents? | no retention policy is authoritative yet (`DATA_RETENTION_POLICY.md`) | **PENDING** |
| Q10 | For goods received not invoiced, is the correct account `380` or `408`? | two defensible readings; affects the balance sheet | **PENDING** |
| Q11 | Under Law 18-07, is the current treatment of supplier/employee data sufficient? | regulatory exposure beyond accounting | **PENDING** |
| Q12 | Is an Arabic-language rendering of invoices/reports required, and in what format? | language obligations | **PENDING** |

---

## Part C — Sign-off block

To be completed by the reviewer. Until this block is signed, the release status remains
**legal certification PENDING** and no document may describe the software as
Algerian-tax-compliant.

```
Reviewer name:
Professional registration no.:
Firm / capacity:
Date:
Scope reviewed (rules):  .....................
Verdict per rule:        VAT-01 … DATE-01  (attach the completed Part A tables)
Overall:                 [ ] APPROVED   [ ] APPROVED WITH CONDITIONS   [ ] REJECTED
Conditions / changes required:
Signature:
```

## Part D — What the vendor may and may not say

| May say | Must not say |
| --- | --- |
| "These 18 behaviours are implemented and covered by the referenced tests." | "The software is Algerian-tax-compliant." |
| "Every rate, bracket and threshold is configurable and recorded with provenance." | "The rates are correct." |
| "Approval is PENDING for all 18 rules and 12 questions." | "Approved by an expert-comptable." |
