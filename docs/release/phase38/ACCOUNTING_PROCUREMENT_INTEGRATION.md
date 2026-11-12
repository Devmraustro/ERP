# Phase 38 — Accounting & procurement integration

## 1. The two defects Phase 34 named: both already fixed

Phase 34's `FINAL_ASSESSMENT.md` listed two live data-integrity defects. Neither exists in the
working tree:

| Phase 34 claim | Measured reality |
| --- | --- |
| `supplier_invoice_no` has **no length bound** | Already bounded: `MAX_SUPPLIER_INVOICE_NO_LENGTH = 128`, enforced inline at line 451 |
| `_parse_invoice_date` is `int(invoice_date[:4])`, **accepts `2026-99-99`** | Already strict: `datetime.strptime(..., "%Y-%m-%d")` at line 461 |

Executed against a disposable database, **before any Phase 35 change**:

```
date 2026-99-99   REFUSED ValueError: invoice_date must be a valid calendar date in YYYY-MM-DD form.
date 2026-13-45   REFUSED (same)
date ''           REFUSED (same)
inv_no 200 chars  REFUSED ValueError: supplier_invoice_no exceeds 128 characters.
inv_no ''         REFUSED ValueError: supplier_invoice_no is required.
payment CRYPTO    REFUSED ValueError: payment_method 'CRYPTO' is not supported...
```

Phase 34 measured `main`/`HEAD`, not the uncommitted working tree. Recorded as a correction rather
than a silent pass, because the Phase 42 defect register referenced it.

## 2. The defect that *is* real: negative amounts reached `Validated`

Found by probing the live write path rather than by reading code.

**Before:**

```
=== NEGATIVE amount handling (workspace, pre-Phase 38) ===
negative invoice -> wf: Validated
                   errors: []
```

A request declaring `HT=-100, TVA=-19, TTC=-119` with matching negative lines reconciles perfectly
against *itself*: `Σline_HT == header_HT`, `Σline_TVA == header_TVA`,
`TTC == HT+TVA+timbre`. Every arithmetic check in the workspace's inline validation compares one
declared number against another declared number, and none of them asks whether the number is
**positive**. So the invoice was written with `workflow_state='Validated'` and an **empty** error
list — a negative payable that the posting step would carry straight into the general ledger.

A credit note in this system is a positive-amount reversal document (`invoice_type='IN_REFUND'`,
`reversal_of_invoice_id` set), so a negative header can only come from a malformed payload.

**After** wiring `_validate_invoice_totals` into the write path:

```
=== AFTER Phase 38 wiring ===
negative invoice -> wf: NeedsReview
                    errors: ['HEADER_AMOUNT_NEGATIVE:HT=-100.00',
                             'HEADER_AMOUNT_NEGATIVE:TVA=-19.00',
                             'HEADER_AMOUNT_NEGATIVE:TTC=-119.00']
```

Regression tests: `test_a_negative_invoice_cannot_reach_validated` and
`test_a_coherent_positive_invoice_still_validates` (so the gate provably does not reject the
ordinary case).

## 3. What was and was not wired

The workspace already performs substantial validation **inline** in
`create_and_validate_supplier_invoice`: exact/fuzzy duplicate detection via
`levenshtein_distance`, per-line HT arithmetic, Algerian VAT rate membership, PO three-way match
(qty and price), header HT/TVA reconciliation, timbre, and TTC. Those checks are not duplicated.

| Gate | Wired into the live write path? | Reason |
| --- | --- | --- |
| `_validate_invoice_totals` | **YES** | closes the negative-amount hole |
| `_validate_invoice_lines` | **NO** | the inline loop already performs an equivalent check per line; wiring both would run the same rules twice and emit duplicate error codes |
| `_duplicate_invoice_errors` | **NO** | inline duplicate detection already exists and is what the current tests assert; the ported function has a different contract (`Tuple[List[str], bool]`) and a different severity model, so swapping it would silently change which duplicates are *rejected* vs *flagged* |
| `_line_price_variance` | **NO** | inline three-way price comparison already covers the PO case; the ported version adds GRN-sourced variance which requires data the workspace does not collect |
| `_invalidate_approval_requests` | **NO (available)** | relevant only once an edit-after-approval path invalidates approvals; callable and tested, awaiting that call site |
| `_record_refused_attempt` | **NO (available)** | pairs with `_RefusedAttempt` on the refuse-and-audit path; callable and tested |

Wiring a gate that duplicates an existing check is not neutral: it doubles the error codes in
`validation_errors_json`, which feeds the reviewer UI. They are therefore integrated, tested and
**available**, and only the one that closes a real gap is live.

## 4. The 22 functions — final disposition

| Group | Count | Status |
| --- | --- | --- |
| A — validators (`_require_supplier_invoice_no`, `_require_valid_payment_method`, `_parse_invoice_date`) | 3 | **already present** inline; behaviour proven by execution |
| B — pure validation gates | 8 | **7 integrated**; `_source_review_fingerprint` rejected (needs `ocr_field_reviews`) |
| C — review-policy gates | 4 | **blocked** on SEC-14 write path (schema migration) |
| D — timbre-provenance chain | 4 | **REQUIRES EXPERT REVIEW — BLOCKED** |

**Preserved:** all 22 behaviours remain either present, integrated, or explicitly listed as blocked
with the reason. None was dropped silently.

## 5. Why the timbre-provenance group (D) is not integrated

`_post_supplier_invoice_in_transaction`, `_create_and_validate_supplier_invoice`,
`_create_reversal_request` and `revalidate_supplier_invoice` depend on seven functions absent from
the workspace's 677-line `localization.py`:

```
inherit_timbre_provenance          persist_invoice_timbre_provenance
assert_timbre_rule_unchanged       record_timbre_rule_version
resolve_timbre_rule                resolve_vat_rates
validate_algerian_identifiers
```

Together these implement effective-dated statutory stamp-duty rule versioning: they decide **which
rule version an invoice is stamped under**, and they bind that version to the invoice so a later
rule change cannot retroactively alter a posted document.

Integrating them would change the amount of `droit de timbre` on invoices by selecting a different
statutory rule. That is an accounting-rule decision reserved to a qualified professional, and the 12
Algerian accounting/tax questions in the Phase 42 register are **PENDING**. The mission forbids
inventing or reinterpreting accounting rules without authoritative evidence or expert approval, so
this group is reported BLOCKED with its prerequisites listed, not disabled or approximated.

## 6. Verification

| Check | Result |
| --- | --- |
| Negative invoice rejected | `NeedsReview` + 3 × `HEADER_AMOUNT_NEGATIVE` |
| Positive coherent invoice unaffected | no `HEADER_AMOUNT_NEGATIVE`, unchanged path |
| `tests/test_phase35_integrity_gates.py` | **18 passed** |
| Full suite failure set vs baseline | **identical, 0 new** |
