# Expert-Comptable Approval Status — Phase 8

**Overall status: PENDING. No rule in this document is approved.**
**No legal citation, tax rate, or statutory deadline in this document is asserted as
correct by this project.** Where a number appears, it is the value the *software*
uses today, labelled as implementation behaviour, and awaiting professional sign-off.

Source of the rule set: `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` (SCF §1, Tax §2,
invoicing §3, Law 18-07 §4, technical §5, non-claims §6, sign-off §7). The rule-code
identifiers (VAT-01…, TIMBRE-01…, G50-01…, ACCOUNT-01/02, DATE-01) are the
traceability labels used by this acceptance record; the checklist itself enumerates
the same obligations by section rather than by code.

## 1. Sign-off table — 18 rules

Every row is `PENDING`. "Implementation behaviour" is what the code does and is
covered by executable tests; "professional confirmation" is what only a qualified
Algerian expert-comptable can give, and it has not been given.

| Code | Obligation under review | Implementation behaviour (tested) | Approval |
| --- | --- | --- | --- |
| VAT-01 | Applicable TVA rates are restricted to the statutory set | line rate validated; unknown rate → `LINE_n_INVALID_ALGERIAN_TVA_RATE` | **PENDING** |
| VAT-02 | TVA computed per line, exact decimal, no binary float | `Decimal` throughout; rounding policy pinned | **PENDING** |
| VAT-03 | Header TVA equals the sum of line TVA | reconciliation refuses mismatch | **PENDING** |
| VAT-04 | TTC = HT + TVA (+ timbre where due) | header reconciliation | **PENDING** |
| VAT-05 | Rates are effective-dated (a historical invoice uses the rate of its date) | effective-date rule resolution | **PENDING** |
| VAT-06 | Exempt / zero-rated handling is explicit, not silent | explicit rate, not omission | **PENDING** |
| VAT-07 | TVA is recoverable/declared on the correct G50 line | tagged via `g50_tag`; mapping unsigned | **PENDING** |
| TIMBRE-01 | Droit de timbre applies to cash payments only | `calculate_droit_de_timbre(…, payment_method)` gates on CASH | **PENDING** |
| TIMBRE-02 | Bracketed computation, not a flat percentage | bracket table per rule version | **PENDING** |
| TIMBRE-03 | Minimum duty floor enforced | `min_duty` default `5.00` DZD | **PENDING** |
| TIMBRE-04 | Exemption threshold honoured | `threshold` default `300.00` DZD | **PENDING** |
| TIMBRE-05 | Rule version used is recorded on the invoice (provenance) | `record_timbre_rule_version`, provenance persisted, unchanged-rule assertion | **PENDING** |
| G50-01 | G50 declaration aggregates the correct accounts | tag-based aggregation implemented | **PENDING** |
| G50-02 | Filing period and deadline are configurable and effective-dated | period config present; statutory dates unsigned | **PENDING** |
| G50-03 | G50 output reconciles to the trial balance for the period | cross-check implemented | **PENDING** |
| ACCOUNT-01 | Chart of accounts follows SCF structure | SCF chart template loaded | **PENDING** |
| ACCOUNT-02 | Account classes/statement mapping (bilan vs compte de résultat) | class mapping present | **PENDING** |
| DATE-01 | Business dates use the fiscal period of the document, not the entry timestamp | document-date driven | **PENDING** |

## 2. Open questions that must be answered by the professional

| # | Question | Why it matters | Status |
| --- | --- | --- | --- |
| Q1 | Is the timbre bracket table in force today the one configured as default? | the amounts are money owed to the treasury | **PENDING** |
| Q2 | Does the 300,00 DZD exemption threshold still apply, and to which payment means exactly? | changes every cash invoice | **PENDING** |
| Q3 | Which G50 line must receive each `g50_tag` in use? | misclassification is a filing error | **PENDING** |
| Q4 | Is TVA on imports and on exempt operations handled outside this scope? | V1 covers domestic purchase invoices only | **PENDING** |
| Q5 | Are the configured filing deadlines authoritative for the company's tax regime? | late filing penalties | **PENDING** |
| Q6 | Does the SCF chart mapping satisfy the bilan/compte de résultat presentation? | statutory accounts format | **PENDING** |
| Q7 | Must a "grand livre" (general ledger) be printable in a specific form? | audit acceptance | **PENDING** |
| Q8 | Is the reversal entry convention (contre-passation) acceptable to the auditor? | audit trail interpretation | **PENDING** |
| Q9 | Which retention period applies to accounting records and to source documents? | checklist §4.7 records that no retention policy exists yet | **PENDING** |
| Q10 | Which GRNI account should be used — `380` or `408` (checklist §1.5 open question)? | two defensible readings exist in the checklist | **PENDING** |
| Q11 | Under Law 18-07 (personal data), is the current consent/retention treatment sufficient for supplier and employee data? | regulatory exposure beyond accounting | **PENDING** |
| Q12 | Is an Arabic-language invoice/judicial rendering required, and in what format? | language obligations | **PENDING** |

## 3. What the project may and may not say today

- **May say:** "the software implements these 18 obligations as described, and each
  behaviour is covered by executable tests."
- **Must not say:** "the software is Algerian-tax-compliant", "the rates are correct",
  "the G50 is filing-ready", or "an expert has approved this".
- The compliance status of this release is **UNSIGNED**, which is why the final
  verdict cannot be higher than *CONDITIONALLY PRODUCTION READY* even if every test
  passes.

## 4. How sign-off is obtained

1. The expert-comptable reads `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` and this
   table.
2. For each of the 18 rules, they record APPROVED / APPROVED WITH CONDITIONS /
   REJECTED, with their name, registration number, date and any citation they wish to
   add — the citation is theirs, never generated by this project.
3. The 12 questions are answered in writing; each answer becomes a test fixture.
4. The signed document is committed and the verdict re-evaluated.

Until then, every row above stays **PENDING**.
