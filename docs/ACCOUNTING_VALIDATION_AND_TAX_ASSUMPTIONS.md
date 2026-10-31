# Accounting validation and tax assumptions

**Status:** arithmetic and configuration mechanics are `DONE` and verified by
`tests/test_phase36_accounting_and_tax_configuration.py` (29 tests). The legal
correctness of any particular rate, bracket or threshold remains
`BLOCKED_EXTERNAL` — see §6. This document separates those two claims on purpose:
the code can be proved to compute exactly what it is configured to compute, and
that is *not* the same as being compliant.

---

## 1. Arithmetic rules the code enforces

| Rule | Implementation | Verified by |
|---|---|---|
| Money is `Decimal`, never binary float | `to_decimal()` raises `FloatMonetaryError` on a float | `test_money_is_rounded_half_up_and_floats_are_refused` |
| Rounding is commercial half-up to the centime | `round_dzd()` → `ROUND_HALF_UP`, 2 dp | same |
| Rounding happens **per line**, then lines are summed | `compute_invoice_totals`, `_validate_invoice_lines` | `test_header_totals_are_the_sum_of_rounded_lines` |
| Line net = qty × price × (100 − discount)/100, rounded once | `compute_invoice_totals` | `test_discount_is_applied_before_tax_on_the_line_net` |
| TVA is charged on the discounted net, not the gross | idem | idem |
| TTC = HT + TVA + timbre | idem | `test_ttc_identity_holds_with_and_without_stamp_duty` |
| Tax breakdown reconciles with the header | `tax_breakdown` accumulates per rate | `test_tax_breakdown_reconciles_with_the_header` |
| Line order is irrelevant to every total | independent per-line rounding | `test_line_order_does_not_change_any_total` |
| No sub-centime residue survives (3 × 0.10 DZD) | quantisation at each step | `test_awkward_quantities_leave_no_sub_centime_residue` |
| A line quantity and unit price must be **strictly positive** | `_validate_invoice_lines` (`LINE_n_QTY_NONPOSITIVE`, `LINE_n_PRICE_NONPOSITIVE`), re-checked on a correcting edit and at posting | `test_fr2_4_*` (phase33) |
| Header HT/TVA/timbre/TTC may be **zero**, never negative | `_validate_invoice_totals` (`HEADER_AMOUNT_NEGATIVE`) | `test_fr2_4_a_negative_header_total_is_refused_even_with_positive_lines` |
| A credit note is a **positive-amount** `IN_REFUND`, never a negative invoice | `reverse_posted_invoice` | `test_fr2_4_a_reversal_still_credit_notes_with_positive_amounts` |
| A receipt-cost ↔ invoice-price difference does not stay on the clearing account | `_line_price_variance`, `products.price_variance_account_code` | `tests/test_phase47_price_variance_clearing.py` |

Quantities are carried at 6 decimals and money at 2, which is why
`3 × 0.10` yields `0.30` rather than a binary-float artefact.

## 2. Droit de timbre

```
base = round(HT + TVA)                      # stamp duty base includes TVA
not CASH            -> 0.00
base <= threshold   -> 0.00                 # boundary is inclusive
otherwise           -> round(base × rate), floored at min, capped at max
```

Verified boundaries: exactly `300.00` → `0.00`; `300.01` → the `5.00` minimum;
`20 000.00` → `200.00`; `150 000.00` → `3 000.00`; a configured `max_duty`
caps the result; `BANK_TRANSFER` and `CHEQUE` (any case) always yield `0.00`.

**The shipped seed applies no ceiling.** Whether the top band (2 % above
100,000.00 DZD) is capped at all, and at what figure, is **UNDETERMINED** and
must not be inferred from this repository: it is blocker **LB-1** in
`docs/REMAINING_BLOCKERS.md`, owned by the accountant of record. A rule version
cannot acquire a cap position by omission — the administration path requires an
explicit declaration, a decimal ceiling or `none` (`TIMBRE_RULE_CAP_NOT_DECLARED`
otherwise) — and the per-invoice provenance records the ceiling that was actually
applied.

## 3. Effective-dated configuration (the change in this phase)

The schema has carried `taxes(valid_from, valid_to, active)` and
`timbre_rules(version_code, valid_from, valid_to, …)` from the start, but the
calculation ignored both and always used compiled-in constants. A configured,
reviewed rule version therefore could not take effect, and nothing said which
version a posted invoice used.

Both are now resolved **by effective date** for the invoice's own date:

```python
resolve_timbre_rule(conn, company_id, invoice_date)  # -> TimbreRule(+version)
resolve_vat_rates(conn, company_id, invoice_date)    # -> VatRateSet(+label)
```

Resolution rules, identical for both, and each covered by a test:

1. **Rows exist and one covers the date** → that rule applies. Of several
   covering rows the latest `valid_from` wins, so superseding a rule is adding
   a row, never editing history.
2. **Rows exist but none covers the date** (a gap, or a rule configured for the
   future) → `TaxRuleConfigurationError`, fail-closed. Guessing here would
   compute a different amount of tax than the operator configured.
3. **No rows at all** → the compiled-in constants, explicitly labelled
   `builtin-default-v1` in the result, so a stored figure can always be told
   apart from a configured one.
4. **Malformed bracket JSON, a negative rate, or a bracket table whose last band
   is closed** → `TaxRuleConfigurationError`. A closed final band would silently
   drop large invoices to the 1 % default.
5. **Tenant-scoped**: a company never borrows another company's rate table.
6. **Inactive rows are ignored** for VAT (`active = 0`).

### 3.1 Adding a rule version (the administration path)

A version reaches the database only through `dz_erp.localization`
(`create_timbre_rule_version`, `approve_timbre_rule_version`) — reachable over
HTTP at `POST /api/v1/tax/timbre-rules` and
`POST /api/v1/tax/timbre-rules/{version_code}/approve`. Raw SQL remains
possible for an operator with database access; it is no longer the *supported*
path, and a hand-inserted row is still subject to the immutability triggers.

* **Nothing is active on creation.** A new rule is `review_state = 'DRAFT'` and
  the resolver ignores it entirely, so a proposal cannot price an invoice, not
  even for a moment.
* **A second, different person activates it.** Approval requires an accountant
  or admin role *and* a reviewer other than the creator (`TIMBRE_RULE_SOD_VIOLATION`),
  so one person cannot both propose and bless a rate. Neither step has a
  default: an incomplete rule is refused (`TIMBRE_RULE_VALUE_REQUIRED`) rather
  than filled with a number nobody chose.
* **Overlap is decided at approval**, not silently. Two windows claiming the
  same day are refused by default (`TIMBRE_RULE_OVERLAP`);
  `overlap_policy='supersede'` closes the older window to the day before the
  new one, and is refused outright if a posted invoice cites that older version
  (`TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE`) — history is narrowed forward,
  never rewritten.
* **Both steps are audited** in the same transaction as the change
  (`TIMBRE_RULE_VERSION_CREATED`, `TIMBRE_RULE_VERSION_APPROVED`, carrying
  actor, timestamp, version, window, bracket values and reason), so the
  configuration history is part of the hash-chained audit log.
* **Tenant-scoped and role-checked at the service**, not merely at the
  endpoint: a direct caller is refused identically to an HTTP client.

Covered by `tests/test_phase46_timbre_rule_administration.py` (35 tests).

Wiring: invoice creation and revalidation resolve the rule for the invoice date
and pass it to line and header validation; the post-edit recompute path resolves
it again. The line-rate whitelist is the resolved set, so a 9 % line on a 2026
invoice is refused when the configured 2026 table does not contain 9 % — even
though the compiled-in constants happen to.

## 4. What is now refused instead of silently accepted

| Situation | Before | Now |
|---|---|---|
| Rate table configured for a future period only | silently used compiled-in rates | `VAT_RATE_NOT_EFFECTIVE_ON_DATE` |
| Timbre rules with a gap in coverage | silently used compiled-in constants | `TIMBRE_RULE_NOT_EFFECTIVE_ON_DATE` |
| Bracket JSON malformed | last bracket defaulted to 1 % | `TIMBRE_RULE_BRACKETS_INVALID` |
| Config with a closed top band | large invoices under-dutied | refused at resolution |
| Per-line rate not in the configured table | accepted if it was 0/9/19 | refused, invoice stays in `NeedsReview` |

These raise rather than degrade, because a wrong tax figure is worse than a
stopped invoice: the invoice stays in `NeedsReview` where a human sees it, and
nothing reaches the ledger.

## 5. G50 preparation workbook

Box arithmetic is closed and traceable: `E3B110 = précompte + E3B91 + E3B92`;
exactly one of `E3B120` (payable) and `E3B130` (credit carried forward) is
non-zero and `payable − carry = collected − deductions`; every figure is listed
in a `traceability` array naming the entry that produced it, including
`E2_droit_de_timbre` (added in this phase — the stamp-duty total previously had
no trace line). The workbook reports POSTED entries of the requested period only,
refuses a cross-tenant request, and its `statutory_status` field states in words
that it is a preparatory workbook requiring an expert-comptable's sign-off.

## 6. What this document does **not** claim — `BLOCKED_EXTERNAL`

No test here asserts a legal fact. Specifically, **not verified by engineering**:

* that 19 % / 9 % / 0 % are the correct TVA rates for any given supply in any
  given period;
* that the seeded timbre brackets and thresholds match the current Finance Law,
  or the law in force on any particular invoice date;
* the treatment of exemptions, exports, imports, IFU-regime taxpayers, or
  self-billed invoices;
* retention and reporting obligations under Law 18-07.

Closing these requires a **qualified Algerian expert-comptable** to review
`docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md` and the seeded configuration, and to
record the legal reference (Finance Law article and date) behind each rate and
bracket. Owner: the engagement's tax signatory. Required evidence: a signed
review naming the source text and effective dates. Until then the release
readiness matrix keeps the tax rows **Blocked (external evidence required)**.

## 7. Rule identity *is* persisted, and how it is read back

> **Correction (R-06).** Earlier revisions of this document — and of
> `docs/TECHNICAL_REPORT.md` and `docs/RISK_REGISTER.md` — stated that the
> resolved rule label was "not yet persisted on the invoice" and tracked the
> work as `NOT_STARTED`. That was out of date: the follow-up landed in Phase 40
> and the claim was never retired. The product was being described as weaker
> than it is, which is its own kind of inaccuracy.

Validation records the governing rule on the invoice itself, in six columns of
`supplier_invoices`:

| Column | Meaning |
|---|---|
| `timbre_rule_version` | the `version_code` that governed the calculation |
| `timbre_rule_source` | `configured` or `builtin_default` (the compiled-in `builtin-default-v1` fallback), so a stored figure can never be mistaken for a configured one |
| `timbre_rule_hash` | SHA-256 over the canonical rule content, so "same version code, different brackets" is detectable |
| `timbre_rule_valid_from` / `_valid_to` | the window in force on the invoice date |
| (snapshot) | the resolved bracket/threshold/min/max inputs, replayed to reproduce the stored amount without re-resolving — which is what makes the figure reproducible even after later configuration changes |

The identity is written on first use and reused; an append-only
`timbre_rule_versions` ledger records the content of each version the first
time any invoice uses it. Immutability triggers abort `UPDATE`/`DELETE` of a
`timbre_rules` row cited by a **Posted** invoice, and posting re-resolves and
refuses with `TIMBRE_RULE_CHANGED_SINCE_VALIDATION` if the rule moved between
validation and posting. `GET /api/v1/invoices/{id}` returns the provenance.

Two limits are real and remain stated:

* **a legacy invoice** (validated before Phase 40) reports `unavailable` and is
  **not back-filled** — inventing provenance for a calculation nobody recorded
  would be fabrication; posting such an invoice records the rule governing it at
  posting time, labelled as such;
* **a reversal** inherits the original invoice's provenance rather than
  re-resolving, because a reversal must reverse the posting that happened.

Evidence: `tests/test_phase40_timbre_rule_provenance.py` (22 tests), covering
recording, snapshot replay, first-use-once, the effective-date boundary,
`test_a_new_rule_version_does_not_change_an_existing_invoice`,
rewrite/delete refusal, ledger append-onlyness, posting-time re-resolution,
concurrent change-and-post, legacy behaviour and tenant scoping.
