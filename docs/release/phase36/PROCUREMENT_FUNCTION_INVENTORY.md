# Phase 36B — Inventory of the 22 procurement/accounting functions

Measured by AST over `/tmp/pr7/dz_erp/procurement_stock.py` (export of `f603e38`) and compared
against the working tree. Every "already present" / "absent" verdict below is from executing the
code, not from reading it.

## 0. Headline correction to Phase 34

Phase 34 reported two live data-integrity defects in the workspace:

> - `supplier_invoice_no` has **no length bound** here (PR #7: 128 chars).
> - `_parse_invoice_date` here is `int(invoice_date[:4])`, which **accepts `2026-99-99`**.

**Both are wrong for the current working tree.** They describe `HEAD`/`main`, not the uncommitted
workspace. I measured the workspace's live behaviour against a real SQLite database:

```
=== CURRENT workspace behaviour (before any Phase 35 change) ===
  date 2026-99-99          REFUSED ValueError: invoice_date must be a valid calendar date in YYYY-MM-DD for
  date 2026-13-45          REFUSED ValueError: invoice_date must be a valid calendar date in YYYY-MM-DD for
  date ''                  REFUSED ValueError: invoice_date must be a valid calendar date in YYYY-MM-DD for
  inv_no 200 chars         REFUSED ValueError: supplier_invoice_no exceeds 128 characters.
  inv_no ''                REFUSED ValueError: supplier_invoice_no is required.
  payment CRYPTO           REFUSED ValueError: payment_method 'CRYPTO' is not supported; expected one of ['
  valid baseline           ACCEPTED (workflow_state=NeedsReview)
```

The workspace's `create_and_validate_supplier_invoice` already performs all three checks **inline**
(`procurement_stock.py` lines 444–465): `MAX_SUPPLIER_INVOICE_NO_LENGTH = 128`, membership in
`VALID_PAYMENT_METHODS`, and `datetime.strptime(..., "%Y-%m-%d")`. Both constants already exist in
the workspace at lines 18–19.

**Consequence for this phase:** "unbounded supplier invoice number" and "invalid dates accepted" are
**not** open defects and require **no integration work**. The only refinement PR #7 offers is
extracting the inline checks into three named helpers (`_require_supplier_invoice_no`,
`_require_valid_payment_method`, `_parse_invoice_date`) so they can be reused by
`_create_and_validate_supplier_invoice` and `revalidate_supplier_invoice`. That is a refactor for
reuse, not a fix. It becomes worthwhile only together with the functions that call them.

This reclassification is recorded rather than quietly dropped, because Phase 34's FINAL_ASSESSMENT
and the Phase 42 defect register both referenced it.

## 1. The 22 functions

`procurement_stock.py` in PR #7 has 40 functions; 22 exist only there. Grouped by what it takes to
port them.

### Group A — self-contained validators (no porting risk)

| # | Function | Lines | Ext deps | In workspace? | Independently portable |
| --- | --- | --- | --- | --- | --- |
| 1 | `_require_supplier_invoice_no` | 9 | none | logic present inline | **Yes** |
| 2 | `_require_valid_payment_method` | 7 | none | logic present inline | **Yes** |
| 3 | `_parse_invoice_date` | 15 | `datetime.strptime` | logic present inline | **Yes** |

Behaviour is already enforced; these only name it. **Verdict: ALREADY PRESENT (as inline code).**

### Group B — pure validation, needs only `conn` + decimal helpers

| # | Function | Lines | Purpose | Needs |
| --- | --- | --- | --- | --- |
| 4 | `_validate_invoice_lines` | 96 | per-line qty/price/VAT bounds, product existence, tolerance | `to_decimal`, `round_dzd`, `round_qty`, `format_dzd` — **all present** |
| 5 | `_validate_invoice_totals` | 47 | HT/TVA/timbre/TTC coherence vs Σ lines | `calculate_droit_de_timbre` (**present**), `round_dzd` |
| 6 | `_duplicate_invoice_errors` | 38 | duplicate detection (normalised ref + amount + fuzzy) | `levenshtein_distance` (**present**), `to_decimal` |
| 7 | `_line_price_variance` | 58 | PO/GRN price variance per line | `round_dzd`, `round_qty` |
| 8 | `_source_review_fingerprint` | 42 | hash of reviewed OCR source values | `hashlib`, `json` only |
| 9 | `_assert_document_linkable` | 14 | document state gate before linking | `DOCUMENT_LINKABLE_STATES` (**ABSENT** from workspace `intake_ocr`) |
| 10 | `_invalidate_approval_requests` | 35 | cancel open approvals on change | `conn` only |
| 11 | `_record_refused_attempt` | 12 | audit a refusal | `append_audit_event` (**present**) |

**Verdict: SHOULD INTEGRATE.** All but #9 are portable with existing helpers. They are the integrity
gates the workspace lacks, and they are what makes "the workspace checks nothing" true.

### Group C — depends on the OCR review-policy subsystem

| # | Function | Lines | Purpose | Blocking dependency |
| --- | --- | --- | --- | --- |
| 12 | `_source_invoice_mismatches` | 180 | compare invoice against reviewed OCR source | `source_value`, `normalize_invoice_ref` (present), `review_policy` data |
| 13 | `_document_review_gate` | 98 | refuse posting unless review policy satisfied | `evaluate_document_review_policy` (**PR #7 `review_policy.py`**) |
| 14 | `_refresh_document_state` | 19 | sync document state after invoice change | `_live_document_owner` (**absent**) |
| 15 | `_live_document_owner` | ~30 | resolve current owning invoice for a document | new |

**Verdict: SHOULD INTEGRATE AFTER GROUP D** (needs `review_policy` + `intake_ocr` additions).

### Group D — timbre-provenance chain

| # | Function | Lines | Blocking dependency |
| --- | --- | --- | --- |
| 16 | `_post_supplier_invoice_in_transaction` | 374 | `assert_timbre_rule_unchanged` (**ABSENT** from workspace `localization.py`), `_document_review_gate` |
| 17 | `_create_and_validate_supplier_invoice` | 248 | `inherit_timbre_provenance` (**ABSENT**), `persist_invoice_timbre_provenance` (**ABSENT**), `resolve_timbre_rule` (**ABSENT**), `resolve_vat_rates` (**ABSENT**), `validate_algerian_identifiers` (**ABSENT**) |
| 18 | `_create_reversal_request` | 276 | `inherit_timbre_provenance` (**ABSENT**) |
| 19 | `revalidate_supplier_invoice` | 323 | all of the above |

The workspace's `localization.py` is **677 lines**; PR #7's is **1,701**. **Seven** functions the
port needs do not exist here:

```
inherit_timbre_provenance        assert_timbre_rule_unchanged
persist_invoice_timbre_provenance  record_timbre_rule_version
resolve_timbre_rule              resolve_vat_rates
validate_algerian_identifiers
```

**Verdict: REQUIRES EXPERT REVIEW.** These implement `ALGERIAN_TIMBRE_PROVENANCE_v2` — statutory
stamp-duty rule versioning. Porting them changes *which statutory rule an invoice is stamped under*,
which is precisely the class of change the mission forbids me from deciding:
"do not invent or reinterpret accounting rules without authoritative project evidence or qualified
expert approval." Expert sign-off is **PENDING** (12 questions). **BLOCKED.**

### Group E — helpers that are not the 22 but are prerequisites

| Function / symbol | Defined in | In workspace? |
| --- | --- | --- |
| `_insert_invoice_lines` | procurement_stock | **absent** |
| `_RefusedAttempt` (exception) | procurement_stock | **absent** |
| `_live_document_owner` | procurement_stock | **absent** |
| `source_value` (closure) | procurement_stock | **absent** |
| `evaluate_document_review_policy` | review_policy | **absent** |
| `DOCUMENT_LINKABLE_STATES` | intake_ocr | **absent** |
| 7 × timbre functions | localization | **absent** |

## 2. Existing tests

| Suite | Covers | Present |
| --- | --- | --- |
| `test_phase33_invoice_lifecycle.py` | PR #7 lifecycle | PR #7 only |
| `test_phase47_price_variance_clearing.py` | `_line_price_variance` | PR #7 only |
| `test_phase34_ocr_duplicate_row_integrity.py` | duplicate rows | PR #7 only |
| `test_phase51_reversal_period_consistency.py` | reversal | PR #7 only |
| `test_phase29_ocr_review_policy.py` | review policy | PR #7 only |
| workspace `test_phase2x` invoice tests | workspace lifecycle | workspace |

Phase 35 will add tests for whichever groups are actually integrated.

## 3. Integration decision for this phase

| Group | Functions | Decision |
| --- | --- | --- |
| A (3) | validators | **ALREADY PRESENT** — no action; behaviour proven by execution |
| B (8) | pure validation gates | **SHOULD INTEGRATE** — portable with existing helpers |
| C (4) | review-policy gates | after `review_policy` lands (Phase 37 SEC-14) |
| D (4) | timbre-provenance chain | **REQUIRES EXPERT REVIEW — BLOCKED** |
| E | prerequisites | as needed by B/C |

**"Do not replace `procurement_stock.py` wholesale"** is therefore also the technically correct
choice, not merely a policy constraint: four of the 22 functions sit behind a statutory-rule
subsystem whose porting is blocked on expert approval, and the workspace has its own working
lifecycle (`create_and_validate_supplier_invoice`, `submit_invoice_for_approval`,
`decide_invoice_approval`, `post_supplier_invoice`, `reverse_posted_invoice`,
`create_reversal_request`) that a wholesale swap would discard.
