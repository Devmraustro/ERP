# Phase 9C — Algerian SCF / TVA / Timbre / G50 Verification

**Status of legal compliance: UNSIGNED.** This report separates three things that are
often conflated, and never merges them.

| Layer | Question | Who can answer | Status here |
| --- | --- | --- | --- |
| **Implementation** | Does the code compute what it says it computes, and is that behaviour tested? | this project | **VERIFIED** (see §2) |
| **Legal research** | Is that behaviour what Algerian law requires? | a qualified researcher reading the statutes | **NOT PERFORMED** — no citation is asserted |
| **Professional approval** | Does a named expert-comptable accept it for this company? | the expert-comptable | **PENDING** — `docs/release/phase8/EXPERT_APPROVAL_STATUS.md` |

## 1. Scope

The 18 rules in `EXPERT_APPROVAL_STATUS.md` (VAT-01…07, TIMBRE-01…05, G50-01…03,
ACCOUNT-01/02, DATE-01) and 12 open questions, traced to
`docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`.

## 2. Implementation evidence (executed)

| Suite | Tests | Result | What it covers |
| --- | --- | --- | --- |
| `test_phase36_accounting_and_tax_configuration` | 29 | passed | chart/tax configuration, effective-dated settings |
| `test_phase40_timbre_rule_provenance` | 22 | passed | which timbre rule was used, recorded per invoice |
| `test_phase46_timbre_rule_administration` | 39 | passed | rule versioning, effective dates, change guards |
| `test_phase27_scf_first_run` | 8 | passed | SCF chart on first run |
| `test_phase32_cross_phase_integration_audit` | 45 | passed | end-to-end ledger behaviour |
| `test_phase33_invoice_lifecycle` | 40 | passed | validation → approval → posting → reversal |
| `test_phase4_procurement_3way_match` | 3 | passed | PO/GRN/invoice matching |
| **Total** | **186** | **all passed** | |

Behaviour confirmed in code and by those tests:

- **TVA per line** with an allowed-rate guard
  (`LINE_n_INVALID_ALGERIAN_TVA_RATE` on an unknown rate), exact `Decimal` maths, and
  header reconciliation of HT/TVA/TTC.
- **Droit de timbre** is gated on the payment method (cash only), computed from a
  bracketed, **effective-dated** rule with a `min_duty` floor and an exemption
  `threshold` — and the rule version actually applied is persisted with the invoice
  (`test_phase40`), so a later rule change cannot retroactively alter an old invoice.
- **SCF chart** loaded on a first run from the shipped template.
- **G50 tagging** on journal lines (`g50_tag`) with tag-based aggregation.

**These are statements about behaviour. None of them says the configured rates,
brackets, thresholds, account mappings or filing dates are legally correct.** They are
the values the software was configured with, awaiting sign-off.

## 3. What a review of the code cannot substitute for

- The **timbre bracket table** in force for the company's fiscal year.
- The exact scope of the cash-payment exemption threshold.
- The correct G50 line for each tag in use.
- Whether the SCF mapping satisfies the statutory presentation (bilan / compte de
  résultat).
- Any retention obligation (the checklist itself records that no retention policy
  exists yet — §4.7), and the Law 18-07 personal-data treatment.

## 4. Non-claims

This release **does not** claim: Algerian tax compliance; correct tax rates; a
filing-ready G50; SCF conformity certified by anyone; or expert approval. Any such
statement would be false until the sign-off table is signed by a named professional.

## 5. Verdict

**Implementation verified, compliance unsigned.** The software implements the 18
obligations as configured and proves it with 186 executed tests; the legal layer is
untouched by this work, and the professional layer is PENDING.
