# Phase 9B — Accounting & Financial Integrity

**Baseline:** PR #7 head `f603e38` · executed 2026-11-09
**Logs:** `docs/release/phase9/evidence/logs/db_concurrency.log`,
`…/business_suites.log`

## 1. The invariants under test

| # | Invariant | How it is enforced | Evidence |
| --- | --- | --- | --- |
| A1 | A posted entry is balanced | schema `CHECK (total_debit_dzd = total_credit_dzd)` + service-level check | schema read (`dz_erp/database.py`), `trial_balance` refuses imbalance, drill: 11 900,00 = 11 900,00 |
| A2 | A posted entry has a poster and a timestamp | schema `CHECK` (POSTED requires `posted_by_id` + `posted_at`) | schema read |
| A3 | Posted rows are immutable | SQLite triggers on `journal_entries`/lines | schema read (triggers at the posted-entry boundary) |
| A4 | Entry numbers are unique per company | `UNIQUE(company_id, entry_number)` | schema read |
| A5 | Posting is idempotent | `Idempotency-Key` mandatory; replay returns the same entry | **executed**: first `200`, same-key replay `200` with `post_replay_same_entry=true`, other key `409`, missing key `400` |
| A6 | No duplicate posting | one POSTED entry for one invoice | **executed**: `posted_entries=1`, `posted_entries_balanced=1` |
| A7 | Reversal preserves the original | reversal is a new entry; original untouched | `test_n_reversal_after_reviewed_posting_keeps_the_ledger_balanced`, `test_f5_first_reversal_succeeds_and_the_second_is_a_domain_conflict` |
| A8 | A second reversal is refused | domain conflict, not silent replay | `test_w_a_second_reversal_of_the_same_invoice_is_refused`, `test_fr2_2_same_reversal_key_with_a_different_reason_is_refused_not_replayed` |
| A9 | Concurrent reversals yield one credit note | exactly one succeeds | `test_f5_concurrent_reversals_produce_exactly_one_credit_note` |
| A10 | Concurrency-safe approvals | one approval request, one decision | `test_j_concurrent_submissions_produce_one_approval_request`, `test_j2_concurrent_approvals_record_exactly_one_decision` |
| A11 | Cross-company financial actions are refused | tenant scope + audit | `test_k_cross_company_decision_and_verdict_read_are_refused_and_audited`, `test_k2_cross_company_decide_endpoint_is_refused_over_http`, `test_f5_cross_company_reversal_is_refused` |
| A12 | Amounts are exact decimals, never binary floats | `Decimal` + `decimal_utils` rounding policy | 207 accounting/tax tests green; `test_a_fully_reviewed_invoice_posts_a_balanced_ledger` |
| A13 | Duplicate detection is tenant-scoped and leaves originals intact | duplicate attempt does not touch the first invoice or its document | `test_f3_duplicate_attempt_leaves_document_and_original_invoice_untouched`, `test_f3_duplicate_detection_stays_tenant_scoped` |
| A14 | Trial balance reconciles to posted entries | per-account aggregate refuses imbalance | drill: `balanced=true`, debit = credit |

## 2. The concurrency drill (executed, real API calls)

Two threads create the *same* supplier invoice number simultaneously:

```
outcomes: invoice 1 → workflow_state "Validated"
          invoice 2 → workflow_state "DuplicateRejected"
validated=1  duplicate_rejected=1   invariant_ok=true
```

Note the design: the losing request does **not** receive a 500 or a torn write — it is
recorded as `DuplicateRejected`, so the audit trail shows the attempt. Exactly one
invoice is postable.

Then the full workflow on the survivor:

```
submit-approval             200
decide-approval (accountant) 200
decide-approval (creator)    403   ← segregation of duties
post (Idempotency-Key k1)    200
post (Idempotency-Key k1)    200   ← replay, same journal entry id
post (Idempotency-Key k2)    409   ← second posting refused
post (no key)                400   ← key is mandatory
POSTED entries in DB         1     ← and it is balanced
trial balance                11 900,00 debit / 11 900,00 credit, balanced
```

## 3. Gaps recorded honestly

- **No property-based (Hypothesis) test suite exists** in the tree. The requirement
  asks for property-based tests; they are **NOT EXECUTED because they do not exist**.
  The rounding/effective-date behaviour is covered by example-based tests only. Adding
  a Hypothesis generator over amounts, rates and dates is the single highest-value
  test investment on the accounting side, and it is not claimed here.
- Effective-date tax behaviour is tested through the timbre rule suite
  (`phase40` 22 passed, `phase46` 39 passed) but not fuzzed.
- No ledger export has been reconciled against an external accounting package
  (there is an Odoo test adapter in the tree, exercised only as an adapter).

## 4. Verdict

All fourteen invariants are either schema-enforced (A1–A4) or execution-verified
(A5–A14). The ledger cannot silently accept an unbalanced, double-posted, or
cross-tenant posting under the exercised conditions. Property-based coverage is
missing and is the stated residual gap.
