# Phase 59C — Accounting and Algerian Compliance Readiness

**Repository:** `https://github.com/Devmraustro/ERP.git` · **Branch:** `arena/63d762c8-erp` ·
**Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`
**Status of this document:** a reconciliation of three things that must not be confused —
**what the code does**, **what the tests prove about that code**, and **what only a qualified Algerian
professional can decide**. It is **not** a legal opinion, not a certification, and not approval.

---

## 1. The one-line verdict

| Question | Answer |
|---|---|
| Does the software implement a coherent SCF chart, VAT, stamp duty, balanced entries, reversal and a trial balance? | **Yes**, and it is exercised end-to-end (`END_TO_END_OPERATIONAL_ACCEPTANCE.md`). |
| Is that implementation *legally correct* for an Algerian company? | **Unknown. `G5` is BLOCKED.** Twelve questions (`Q1`–`Q12`) remain unanswered by a licensed `Expert-Comptable` / `Fiscaliste`. |
| May the software be described as "Algerian-tax-compliant"? | **No.** Not in the README, not in a proposal, not verbally to a customer. |

`Q1`–`Q12` are reproduced **verbatim and unchanged** in §5. Nothing in this phase altered,
re-ordered, merged or re-scoped them. A passing test is **never** presented here as legal approval.

---

## 2. What is implemented — and the test that keeps it honest

| Area | Implementation | Automated evidence |
|---|---|---|
| Chart of accounts | `dz_erp/localization.py::load_official_odoo_chart` loads a vendored Odoo DZ chart (294 accounts, 68 groups in a fresh bootstrap) | `tests/test_phase2_localization.py`; observed `GET /api/v1/accounts` → 294 |
| Chart isolation | Accounts are per-company; a code not in *this* company's chart is refused | E2E: `422 stock_account_code '9999' is not an account of this company's SCF chart.` |
| VAT rates | 8 seeded taxes: sale 19/9/0-domestic-exempt/0-export, purchase 19/9/19-depreciable/0-exempt, each with `valid_from`/`valid_to`/`active` | `tests/test_phase2_localization.py` |
| VAT arithmetic | `calculate_invoice_totals`, per-line `line_tva_dzd`, header `amount_tva_dzd`, totals validated against line sums | `tests/test_phase3_*`, `test_phase4_procurement_3way_match.py` |
| Rounding | `dz_erp/decimal_utils.py` — `Decimal` only, `ROUND_HALF_UP`, quantised to `0.01`; no float on any posting path | suite-wide; `round_dzd` is the single entry point |
| Stamp duty | `calculate_droit_de_timbre` — brackets `≤30 000 → 1 %`, `≤100 000 → 1,5 %`, above → `2 %`; floor `5.00`; exemption threshold `300.00`; **cash only** | `tests/test_phase3_*`; live table in §3 |
| Stamp duty versioning | `timbre_rules` table carries dated brackets; `DEFAULT_TIMBRE_BRACKETS` is the seed | see §4, item `Q1`/`Q10` |
| Balanced entries | `journal_entries` CHECK `total_debit_dzd = total_credit_dzd`; immutability triggers; `FINANCIAL_INTEGRITY_VIOLATION` | E2E: `debit 11900.00 = credit 11900.00`; `tests/test_phase4_*` |
| Trial balance | `generate_g50_preparation_workbook`, `GET /api/v1/accounting/trial-balance`, `entry_state=POSTED` only | E2E: balanced, three SCF rows |
| Reversal | Approval + segregation of duties; **contre-passation** into the `RAVOIR` journal; original untouched | E2E: `RAVOIR/2026/00002`, original still `Posted` |
| Duplicate invoices | `normalized_invoice_no` + partial unique index `uq_posted_supplier_invoice`; `DuplicateRejected` state | E2E: `409 Cannot post invoice 4 in state 'DuplicateRejected'` |
| G50 preparation | `g50_base_tag` / `g50_tax_tag` per tax; workbook aggregation by tag | `tests/test_phase3_*` |
| Audit trail | Hash-chained `audit_events`; refusals audited as well as successes | E2E: 24 events incl. both refusals |
| Arabic rendering | SCF account names carry `name_ar` (observed: `المشتريات المخزنة`); desktop is fully RTL | 286/286/286 i18n parity, `ar` layout direction |

## 3. Stamp duty — the actual behaviour, so the expert can check it

Executed against the shipped function (`calculate_droit_de_timbre`, defaults):

| Base (HT+TVA) | Payment | Duty |
|---|---|---|
| `100.00` | CASH | `0.00` (below threshold) |
| `300.00` | CASH | `0.00` (threshold is inclusive) |
| `300.01` | CASH | `5.00` (**floor applies**, not 1 % = 3.00) |
| `10 000.00` | CASH | `100.00` (1 %) |
| `50 000.00` | CASH | `750.00` (1,5 %) |
| `200 000.00` | CASH | `4 000.00` (2 %) |
| `50 000.00` | BANK_TRANSFER | `0.00` |
| `50 000.00` | CHEQUE | `0.00` |

The base is **TTC (HT + TVA)**, the threshold is `300.00` **inclusive**, the floor is `5.00`, and
non-cash methods carry no duty. **Whether each of those four statements matches the current Finance
Law is exactly `Q1`/`Q2` and is not decided here.**

## 4. Discrepancies between documentation, code and tests

These are the places where the repository said one thing and did another. Each is either fixed or
recorded as an open decision — none is papered over.

| # | Discrepancy | Resolution |
|---|---|---|
| 1 | `docs/release/phase8` and `phase9` instructed `pip install --require-hashes -r requirements-desktop.lock.hashes.txt`; **the file did not exist** | **Fixed**: created both files, extended the generator, added a regression test |
| 2 | A fresh `--bootstrap-admin` deployment had **0 accounts**, so no invoice could ever post | **Fixed**: bootstrap now loads the chart; 294 accounts observed |
| 3 | The phase-10 review package asks `Q10` ("`380` or `408`?") while `products.clearing_account_code` **defaults to `380`** | **Open by design** — the default is a decision the expert must ratify, not the software's claim. Recorded in §5 `Q10` |
| 4 | `docs/release/phase40` states PySide6 is "a GUI extra, not part of the lock" | **Consistent**, and now **actionable**: the extra has its own hash-locked file and a CI job that runs it |
| 5 | Desktop Qt suites were green in CI **by skipping** | **Fixed**: `verify-desktop` job fails on any skip |

## 5. `Q1`–`Q12` — unchanged, all PENDING

Source of record: `docs/release/phase10/EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` Part B. Reproduced
verbatim. **No answer has been given, inferred or assumed.**

| # | Question | Status |
|---|---|---|
| Q1 | Are the timbre brackets/rates in `DEFAULT_TIMBRE_BRACKETS` current for the period under review? | **PENDING** |
| Q2 | Does the 300,00 DZD exemption threshold still apply, and exactly to which payment means? | **PENDING** |
| Q3 | Which G50 line receives each `g50_tag` in use (`E3B91_base`, `E3B91_balance`, `TIMBRE_PURCHASE`)? | **PENDING** |
| Q4 | Are imports and exempt operations in or out of this deployment's scope? | **PENDING** |
| Q5 | Are the configured filing periods/deadlines authoritative for this regime? | **PENDING** |
| Q6 | Does the chart satisfy the bilan/compte de résultat presentation you file? | **PENDING** |
| Q7 | Must a "grand livre" be printable in a specific form, and with what columns? | **PENDING** |
| Q8 | Is the reversal convention (contre-passation, new entry, original untouched) acceptable to an auditor? | **PENDING** |
| Q9 | What retention period applies to accounting records and source documents? | **PENDING** |
| Q10 | For goods received not invoiced, is the correct account `380` or `408`? | **PENDING** |
| Q11 | Under Law 18-07, is the current treatment of supplier/employee data sufficient? | **PENDING** |
| Q12 | Is an Arabic-language rendering of invoices/reports required, and in what format? | **PENDING** |

The wider gate register (statutory accounting, tax, invoicing mentions, Law 18-07, audit evidence) is
`docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`; every "Blocks production" row there is still open.

## 6. Gate `G5`

**`G5` — Algerian professional sign-off: BLOCKED.**

| Requirement | State |
|---|---|
| Named `Expert-Comptable` / `Fiscaliste` reviewer | none engaged |
| Written determination on `Q1`–`Q12` | none |
| Signed ONEC sign-off block | blank (`docs/release/phase55/EXPERT_APPROVAL_PACKAGE.md`) |
| Any parameter change arising from the answers | not applied, because no answer exists |
| Software-side blockers to obtaining sign-off | none — the package, worked examples and live outputs are all reproducible (§3) |

**Handover.** Give `docs/release/phase55/EXPERT_APPROVAL_PACKAGE.md` to the reviewer; the worked
stamp-duty table (§3), a sample posted journal entry (§3 of the E2E report) and the seeded VAT list
are the artefacts they need. Until that block is signed, the classification of this release cannot
improve beyond *staging*.

## 7. Explicit prohibitions observed in this document

* No test result is described as legal approval.
* No `Q` is answered or "assumed for now".
* No statutory parameter (rate, threshold, bracket, account default) was changed in this phase —
  changing one without an answer would be the fabrication this phase must avoid.
* `G5` is not marked PASS, PARTIAL or "effectively closed".
