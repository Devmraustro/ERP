# Phase 65 — Expert Review Response Matrix

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Gate | **G5 — BLOCKED.** No licensed expert has answered any question. Every response field below is empty on purpose. |
| What this is | The questions, exactly as committed, each tied to what the software does today and to the test that pins that behaviour, with a blank space for the expert's determination. |
| What this is not | A legal opinion. **A passing test shows the software does what its author intended. It says nothing about whether that intention matches Algerian law.** No statute, article or rate is asserted here as correct. |

## 0. Source of the questions — read this first

The mission names `docs/compliance/EXPERT_REVIEW_QUESTIONS.md`. **That file does not exist on any
branch** (Phase 61, R61-1). It was not created, because doing so would invent a source.

The twelve questions are taken, character for character, from
`docs/release/phase10/EXPERT_COMPLIANCE_REVIEW_PACKAGE.md`, *Part B — The 12 questions*, which Phase 59
also names as the source of record.

A **second, different** list also labelled `Q1`–`Q12` exists in
`docs/release/phase55/EXPERT_APPROVAL_PACKAGE.md` §2 (the "code-level register" from Phase 46). The two
lists overlap but are not the same: for example, `Q10` is the 380/408 question in Phase 10 and the
governing-date question in Phase 55. To keep an expert's answer unambiguous, this matrix uses:

* **`P10-Q1` … `P10-Q12`** — the Phase 10 list (§1). This is the list answered here.
* **`P55-Q1` … `P55-Q12`** — the Phase 55 list (§2), cross-referenced only, wording untouched.

**Action for the release owner before sending this to an expert:** confirm in writing which list is the
list of record, or that both must be answered.

## 1. The twelve questions (Phase 10 Part B, verbatim)

"Current behaviour" was observed on 2026-11-14 on synthetic data unless a test is cited instead.

### P10-Q1

> Are the timbre brackets/rates in `DEFAULT_TIMBRE_BRACKETS` current for the period under review?

| | |
|---|---|
| Why it blocks (verbatim) | every cash invoice carries a treasury liability |
| Rule / code | `dz_erp/localization.py` — `DEFAULT_TIMBRE_BRACKETS`, `calculate_droit_de_timbre`; seeded rule `LF-2025-CASH-TIMBRE`, effective `2025-01-01`, in table `timbre_rules` |
| Current behaviour | Base is HT + TVA. Observed: cash invoice of 2 142,00 → duty 21,42. Phase 59 §3 tabulates 1 % / 1,5 % / 2 % brackets, floor 5,00. Posted to account `642`. |
| Tests | `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| Expert determination | ☐ Confirmed ☐ Confirmed with reservation ☐ Incorrect — correction required |
| Legal reference supplied by the expert | |
| Effective date(s) | |
| Reservations | |

### P10-Q2

> Does the 300,00 DZD exemption threshold still apply, and exactly to which payment means?

| | |
|---|---|
| Why it blocks (verbatim) | changes the duty on most small cash purchases |
| Rule / code | `calculate_droit_de_timbre(threshold="300.00")`; `timbre_rules` threshold column |
| Current behaviour | Threshold is inclusive (300,00 → no duty; 300,01 → floor 5,00). Duty applies to `CASH` only; `BANK_TRANSFER` and `CHEQUE` carry none. A cash invoice declaring 0,00 where 21,42 is expected is held: `TIMBRE_MISMATCH:declared=0.00!=expected=21.42_for_CASH`. |
| Tests | `tests/test_phase2_localization.py` |
| Expert determination | ☐ Confirmed ☐ Confirmed with reservation ☐ Incorrect |
| Legal reference | |
| Effective date(s) | |
| Reservations | |

### P10-Q3

> Which G50 line receives each `g50_tag` in use (`E3B91_base`, `E3B91_balance`, `TIMBRE_PURCHASE`)?

| | |
|---|---|
| Why it blocks (verbatim) | misclassification is a filing error |
| Rule / code | Tax table seeds in `dz_erp/localization.py` (`g50_base_tag`, `g50_tax_tag`); `generate_g50_preparation_workbook` |
| Current behaviour | The workbook is generated per period and company, refuses another company's request, and is labelled `PREPARATORY_WORKBOOK_REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF`. **It is a preparation aid, not a filing.** |
| Tests | `tests/test_phase2_localization.py::test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| Expert determination | ☐ Confirmed ☐ Confirmed with reservation ☐ Incorrect — supply the box for each tag |
| Legal reference / form version | |
| Reservations | |

### P10-Q4

> Are imports and exempt operations in or out of this deployment's scope?

| | |
|---|---|
| Why it blocks (verbatim) | V1 covers domestic purchase invoices only |
| Rule / code | No customs (`D10`) or reverse-charge code exists. Exempt purchase tax row `l10n_dz_vat_purchase_0_exempt` exists in the tax table. |
| Current behaviour | Imports: not implemented, not tested. |
| Tests | none for imports |
| Expert / owner determination | ☐ Out of scope accepted ☐ Required before go-live |
| Reservations | |

### P10-Q5

> Are the configured filing periods/deadlines authoritative for this regime?

| | |
|---|---|
| Why it blocks (verbatim) | late filing penalties |
| Rule / code | Period is a `YYYY-MM` prefix passed to the G50 workbook. **The software enforces no filing deadline and sends no reminder.** |
| Tests | `tests/test_phase2_localization.py` (fiscal configuration) |
| Expert determination | ☐ Confirmed ☐ Incorrect |
| Legal reference | |
| Reservations | |

### P10-Q6

> Does the chart satisfy the bilan/compte de résultat presentation you file?

| | |
|---|---|
| Why it blocks (verbatim) | statutory accounts format |
| Rule / code | Vendored chart loaded by `load_official_odoo_chart`; 294 accounts present after bootstrap (observed) |
| Current behaviour | Trial balance only. **No bilan or compte de résultat report is produced.** |
| Tests | `tests/test_phase27_scf_first_run.py::test_first_run_seeds_official_chart_exactly`, `::test_double_chart_load_is_idempotent` |
| Expert determination | ☐ Confirmed ☐ Confirmed with reservation ☐ Incorrect |
| Reservations | |

### P10-Q7

> Must a "grand livre" be printable in a specific form, and with what columns?

| | |
|---|---|
| Why it blocks (verbatim) | audit acceptance |
| Current behaviour | No general-ledger print or export exists; journal lines are stored and the trial balance is available over the API. |
| Tests | none |
| Expert determination | ☐ Not required ☐ Required — specify layout |
| Legal reference | |
| Reservations | |

### P10-Q8

> Is the reversal convention (contre-passation, new entry, original untouched) acceptable to an auditor?

| | |
|---|---|
| Why it blocks (verbatim) | audit trail interpretation |
| Rule / code | `dz_erp/procurement_stock.py` — `create_reversal_request`, `submit_reversal_for_approval`, `decide_reversal_approval`, `post_reversal` |
| Current behaviour | Observed end to end: a credit note (`AVOIR-<number>`) and a draft entry `RAVOIR/<year>/<id>` are created; a second person must approve; on posting, every line of the original is mirrored; the original invoice and its entry are unchanged (and cannot be changed, by trigger); the supplier balance returned from 14 063,42 to 2 163,42. One invoice can have **one** credit note; a rejected credit note cannot be re-requested (Phase 67, W67-4). |
| Tests | `tests/test_phase67_reversal_over_http.py` (new in this mission) |
| Note | Until this mission the credit note could not be approved or posted over HTTP at all. The convention itself was not changed. |
| Expert determination | ☐ Acceptable ☐ Acceptable with reservation ☐ Not acceptable |
| Reservations (numbering of credit notes, partial reversals, rejected reversals) | |

### P10-Q9

> What retention period applies to accounting records and source documents?

| | |
|---|---|
| Why it blocks (verbatim) | no retention policy is authoritative yet (`DATA_RETENTION_POLICY.md`) |
| Current behaviour | Backup retention is configurable (default 7 daily / 4 weekly, observed). Nothing deletes ledger rows or vault documents. **No statutory retention period is enforced or claimed.** |
| Tests | `tests/test_phase62_backup_automation.py` (retention mechanics only) |
| Expert determination — period for records / for source documents | / |
| Legal reference | |

### P10-Q10

> For goods received not invoiced, is the correct account `380` or `408`?

| | |
|---|---|
| Why it blocks (verbatim) | two defensible readings; affects the balance sheet |
| Rule / code | `products.clearing_account_code`, default `380`; register risk C4 |
| Current behaviour | Observed: goods receipt posts Dr `30` / Cr `380`; the supplier invoice posts Dr `380`, Dr `4456` / Cr `401`. Account `408` exists in the chart and is not used by any posting. A received-not-invoiced balance therefore sits as a credit on `380`. |
| Tests | `tests/test_phase4_procurement_3way_match.py` |
| Expert determination | ☐ `380` ☐ `408` ☐ depends — specify per transaction type and at year end |
| Reservations | |
| Consequence if `408` | A code and data change; to be implemented only with this written determination and regression tests. |

### P10-Q11

> Under Law 18-07, is the current treatment of supplier/employee data sufficient?

| | |
|---|---|
| Why it blocks (verbatim) | regulatory exposure beyond accounting |
| Current behaviour | Encrypted document vault, role checks, tenant isolation, redacting security log (verified: no password or token in the log). No processing register, no data-subject request workflow, no authority filing — register risk C6, Open, High. |
| Owner | DPO / legal counsel, not the accountant |
| Determination | ☐ Sufficient ☐ Insufficient — list the required measures |

### P10-Q12

> Is an Arabic-language rendering of invoices/reports required, and in what format?

| | |
|---|---|
| Why it blocks (verbatim) | language obligations |
| Current behaviour | Desktop interface in French and Arabic. Account names carry `name_fr` and `name_ar`. No Arabic report layout. Arabic scanned images are not recognised; they go to manual transcription. |
| Tests | Desktop i18n tests (CI Desktop job only) |
| Expert determination | ☐ Not required ☐ Required — specify documents and format |

## 2. Cross-reference to the Phase 55 list (wording untouched; not answered here)

| Phase 55 heading (verbatim) | Related Phase 10 question |
|---|---|
| Q1 — Stamp-Duty (`Droit de Timbre`) Bracket Schedule (`TIMBRE-02`) | P10-Q1 |
| Q2 — `300.00 DZD` Stamp-Duty Exemption Threshold (`TIMBRE-04`) | P10-Q2 |
| Q3 — `5.00 DZD` Minimum Stamp-Duty Floor & Maximum Cap (`TIMBRE-03`) | P10-Q1 (floor); **the cap has no Phase 10 counterpart** |
| Q4 — Payment-Method Scope of Stamp Duty (`TIMBRE-01`) | P10-Q2 |
| Q5 — Stamp-Duty Calculation Base (`HT + TVA` vs `HT`) | **none** |
| Q6 — Admissible Algerian TVA Rates (`VAT-01`, `VAT-06`) | **none** |
| Q7 — Per-Line TVA Rounding vs Header-Level Rounding & Supplier Tolerance (`VAT-02`, `VAT-03`, `VAT-04`) | **none** |
| Q8 — Commercial Discount Before TVA | **none** |
| Q9 — Reversal / Credit-Note (`Avoir`) Convention (`Part B Q8`) | P10-Q8 |
| Q10 — Governing Date for Statutory Rule Changes & Late-Received Invoices (`VAT-05`, `DATE-01`) | **none** |
| Q11 — Historical Provenance Freeze After Statutory Rule Change (`TIMBRE-05`) | **none** |
| Q12 — SCF Account Mapping (`294` Accounts, `380` vs `408` GR/IR, `4456`/`4457`) & `G50` Box Tags (…) | P10-Q3, P10-Q6, P10-Q10 |

Six Phase 55 questions have no counterpart in the Phase 10 list. An expert who answers only §1 has
**not** ruled on the stamp-duty base, the admissible VAT rates, rounding, discounts, or effective dating.

## 3. Topics the mission asked to be watched, and where they stand

| Topic | Implemented? | Covered by a question? |
|---|---|---|
| SCF treatment / chart | Chart loaded; trial balance only | P10-Q6 |
| Double entry | Every entry balanced (observed per entry and in total); unbalanced posting refused by tests | Not a question — a mechanical property |
| VAT | Purchase VAT to `4456`; header/line consistency enforced | Only in P55-Q6/Q7 |
| G50 | Preparation workbook only | P10-Q3, P10-Q5 |
| Invoice numbering | Supplier numbers are the supplier's; duplicates rejected including re-typed variants. Journal numbers `ACH/<year>/<id>` follow the invoice id and are therefore **not gapless** (observed `00001` then `00006`). | **No question asks whether journal numbering must be gapless.** Proposed addition, to be put to the expert. |
| Credit notes | See P10-Q8 | P10-Q8 |
| Period close / reopen | **Not implemented.** | **No question.** A posting can be dated into any period. |
| Inventory valuation | Receipts valued at purchase-order price. **A stock issue produces no stock move and no accounting entry** (Phase 67, W67-3). | **No question.** |
| Effective dating | Timbre rules carry effective dates | P55-Q10/Q11 only |
| 380 vs 408 | Default `380` | P10-Q10 |
| Timbre fiscal | See P10-Q1, P10-Q2 | P10-Q1, P10-Q2 |

The three "no question" rows are gaps in the question set itself. They are listed for the release owner
to raise; they were **not** added to the twelve.
