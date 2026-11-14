# Phase 59B — End-to-End Operational Acceptance

**Repository:** `https://github.com/Devmraustro/ERP.git` · **Branch:** `arena/63d762c8-erp` ·
**Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`
**Environment:** isolated directory `/tmp/acc2`, synthetic data only, SQLite, loopback HTTP.
**Python:** 3.11.2 — **below the supported restore floor** (3.11.17+); see §7 for exactly what that
excludes.
**Status:** the workflows below were **executed against a running server over HTTP**, not simulated.

---

## 1. What this proves and what it does not

It proves the server, as installed by the procedure in `INSTALLATION_AND_STARTUP_ACCEPTANCE.md`,
accepts synthetic master data, validates an invoice, enforces separation of duties, refuses to post
an unreviewed or duplicate document, posts a balanced journal entry to the correct SCF accounts,
produces a balanced trial balance, and reverses by credit note — with every action audited.

It does **not** prove legal correctness of any of those entries. That is `G5`, and it is **BLOCKED**
(see `ACCOUNTING_AND_COMPLIANCE_READINESS.md`). It also does not cover restore (§7).

---

## 2. Initial state

| Item | Value |
|---|---|
| Database | `/tmp/acc2/erp.sqlite3`, created by `--bootstrap-admin` on this branch |
| Schema | 26 tables (`/readyz` → `database.values.table_count`) |
| Chart | 294 SCF accounts, 68 groups, 8 taxes |
| Users | 1 (`admin`, role `admin`) |
| Invoices | 0 |
| Journal entries | 0 |
| Audit events | 0 |

---

## 3. Scenario results

Each row states initial state, action, expected, actual, database evidence, accounting impact and
audit trail. All actions went through the documented HTTP API with a bearer token.

### 3.1 Authentication and identity

| | |
|---|---|
| **Action** | `POST /api/v1/auth/token` with the bootstrapped admin credential |
| **Expected** | `200`, an access token, no password echoed |
| **Actual** | `200`, token returned |
| **DB evidence** | `audit_events`: `AUTH_TOKEN_ISSUED` |
| **Notes** | `GET /api/v1/auth/session` resolved `user_id=1, company_id=1, role=admin` **server-side** from the token — the client cannot name its own tenant |

Anonymous access was refused: `GET /api/v1/invoices` without a token → **`401`** (`PASS`).

### 3.2 Chart of accounts

| | |
|---|---|
| **Action** | `GET /api/v1/accounts` |
| **Expected** | the seeded SCF chart |
| **Actual** | `200`, **294** accounts |
| **Regression note** | Before the §5.1 fix in the 59A report this returned **0** — the defect that blocked all downstream work |

### 3.3 Supplier (master data, Algerian identifiers)

| | |
|---|---|
| **Action** | `POST /api/v1/partners` with NIF `0012160…`, RC `16/00-…B21`, AI, NIS, address in Sétif, `tax_regime=REEL` |
| **Expected** | `200`, `partner_id` |
| **Actual** | `200`, `partner_id=2` |
| **DB evidence** | `partners` row; `audit_events` `PARTNER_CREATED` |
| **Accounting impact** | none |

Negative cases, all refused with `422` and a named reason (never a `500`, never a silent accept):

| Case | Result |
|---|---|
| `nif = "123"` | `422 Invalid NIF '123': must be 15 or 20 digits.` |
| duplicate NIF in the same company | `422 A partner with NIF … already exists in this company ('…', id=2).` |

### 3.4 Product (master data + SCF account mapping)

| | |
|---|---|
| **Action** | `POST /api/v1/products` — SKU, `standard_cost_dzd=250.00`, `is_storable=true`, `stock_account_code=30`, `purchase_account_code=601`, `clearing_account_code=380` |
| **Expected** | `200`, `product_id`, cost echoed as a decimal string |
| **Actual** | `200`, `product_id=2`, `standard_cost_dzd: "250.00"` |
| **Accounting impact** | determines the accounts used at posting |

| Case | Result |
|---|---|
| `stock_account_code = "9999"` (not in the chart) | `422 stock_account_code '9999' is not an account of this company's SCF chart.` |
| duplicate SKU | `422 An item with SKU 'ART-…' already exists (id=2).` |

> **Finding for the operator guide.** `"300"` is **not** an account code in the vendored SCF chart;
> the stock account is **`30`**. The first E2E attempt failed on precisely this and the message was
> correct. The user guides state the real codes.

### 3.5 Supplier invoice creation and validation

| | |
|---|---|
| **Action** | `POST /api/v1/invoices` — HT `10000.00`, TVA `1900.00`, timbre `0.00`, TTC `11900.00`, 40 × `250.00` at 19 % |
| **Expected** | `200`, stored with `workflow_state = Validated`, `payload_sha256` recorded |
| **Actual** | `200`, `invoice_id=2`, `workflow_state=Validated`, `payload_sha256=0c84d681…0765` |
| **DB evidence** | `supplier_invoices` row 2, `supplier_invoice_lines` row |
| **Accounting impact** | **none yet** — `Validated` is not posted |
| **Audit trail** | `INVOICE_VALIDATED_STAGED` |

The document is **staged, not posted**. That is the control the acceptance criteria require:
validation alone never creates a journal entry.

### 3.6 Refusals — bad totals and duplicates are stored but blocked

These were **not** rejected with `4xx` at creation. They were **persisted in a blocked state**, which
is stronger: the document is retained for audit, is visibly un-postable, and cannot reach the ledger.

| Case | HTTP | Stored state | Can it post? |
|---|---|---|---|
| TVA `1500.00` with TTC `11900.00` (HT+TVA ≠ TTC) | `200` | **`NeedsReview`** | no |
| same supplier + same invoice number | `200` | **`DuplicateRejected`** | no |

Posting attempts against them:

```
POST /api/v1/invoices/3/post  → 409 Cannot post invoice 3 in state 'NeedsReview'; requires 'Approved' state.
POST /api/v1/invoices/4/post  → 409 Cannot post invoice 4 in state 'DuplicateRejected'; requires 'Approved' state.
```

**This is the required behaviour**, and it is stronger than a `422` at the door. My initial script
asserted `422` and therefore recorded a false "FAIL"; the database showed the blocked states and the
`409`s above. The report records the corrected finding rather than the wrong expectation.

### 3.7 Approval — separation of duties

| | |
|---|---|
| **Action** | `POST /api/v1/invoices/2/submit-approval` |
| **Expected** | `200`, state → `PendingApproval` |
| **Actual** | `200`; state `PendingApproval` (`AUDIT: APPROVAL_REQUESTED`) |

| | |
|---|---|
| **Action** | the **creator** (admin) tries `POST /api/v1/invoices/2/decide-approval` |
| **Expected** | refused |
| **Actual** | **`403 SEGREGATION_OF_DUTIES_VIOLATION: Invoice creator cannot approve their own invoice.`** |

A second user was created (`approver1`, role `approver`), who approved successfully:

```
POST /api/v1/invoices/2/decide-approval  → 200 {'workflow_state': 'Approved', 'decision': 'APPROVED'}
audit_events: INVOICE_APPROVED
```

The approver then attempted to post and was refused on **role**, not on state:

```
POST /api/v1/invoices/2/post → 403 Role 'approver' is not authorized for this action; required one of ('accountant', 'admin').
```

So three independent gates hold: state, segregation of duties, and role.

### 3.8 Posting — journal entry, equality, SCF accounts

| | |
|---|---|
| **Action** | `POST /api/v1/invoices/2/post` as `admin`, with a mandatory `Idempotency-Key` |
| **Expected** | `200`, one balanced journal entry, `Posted` |
| **Actual** | `200` |

```
{'invoice_id': 2, 'workflow_state': 'Posted', 'journal_entry_id': 1,
 'entry_number': 'ACH/2026/00002', 'total_debit_dzd': '11900.00',
 'total_credit_dzd': '11900.00', 'idempotent_replay': False}
```

Journal lines observed:

| Account | Name (FR) | Debit | Credit |
|---|---|---|---|
| `380` | Marchandises stockées | `10000.00` | `0.00` |
| `4456` | TVA récupérable et précompte | `1900.00` | `0.00` |
| `401` | Fournisseurs de stocks et services | `0.00` | `11900.00` |
| **Total** | | **`11900.00`** | **`11900.00`** |

Debit equals credit exactly; the TVA is separated from the expense; the payable is on a supplier
liability account. Posting requires an `Idempotency-Key` — a post without it returned
`400 Missing mandatory Idempotency-Key header.`

### 3.9 Trial balance and general ledger

`GET /api/v1/accounting/trial-balance`:

```json
{"entry_state": "POSTED", "balanced": true,
 "total_debit_dzd": "11900.00", "total_credit_dzd": "11900.00",
 "rows": [ {"account_code": "380", "debit_dzd": "10000.00", "credit_dzd": "0.00"},
           {"account_code": "401", "debit_dzd": "0.00", "credit_dzd": "11900.00"},
           {"account_code": "4456", "debit_dzd": "1900.00", "credit_dzd": "0.00"} ]}
```

Balanced, and the rows are the posted entry, not a recomputation from the invoice payload.

### 3.10 Reversal by credit note

| | |
|---|---|
| **Action** | `POST /api/v1/invoices/2/reverse` with a reason, `Idempotency-Key` |
| **Expected** | a credit note and a reversal entry; the original untouched |
| **Actual** | `200` |

```json
{"original_invoice_id": 2, "credit_note_invoice_id": 5,
 "reversal_journal_entry_id": 2, "reversal_entry_number": "RAVOIR/2026/00002",
 "reason": "Erreur de saisie - extourne de test", "workflow_state": "NeedsReview"}
```

* The original invoice remains `Posted` — **contre-passation, not deletion**. No `UPDATE`/`DELETE` of
  a posted document exists on this path.
* The reversal is a **new** entry in the `RAVOIR` journal with its own sequential number.
* The credit note itself lands in `NeedsReview`, i.e. it must pass the same gates as any invoice;
  reversal is not a bypass.
* Trial balance after reversal: still balanced, `debit = credit = 11900.00` (the reversal entry is
  not yet posted, so the posted ledger is unchanged).
* Audit: `REVERSAL_REQUESTED`.

### 3.11 Audit trail

`audit_events` after all of the above: **24 rows**, including

```
AUTH_TOKEN_ISSUED (10) · INVOICE_VALIDATED_STAGED (4) · PRODUCT_CREATED (2) · PARTNER_CREATED (2)
REVERSAL_REQUESTED (1) · LOAD_CHART_TEMPLATE (1) · INVOICE_POSTED (1) · INVOICE_APPROVED (1)
AUTH_USER_CREATED (1) · APPROVAL_REQUESTED (1)
```

Every state change in the chain has an event. Refusals are audited too (the role and
segregation-of-duties refusals) — a denied attempt is evidence, not silence.

### 3.12 Backup after the workflow

`python -m dz_erp.ops_backup run` produced `erp-20261113T235818Z.erpbackup`
(`size_bytes 47692`, `sha256 4da34acb…bd52`) with a manifest; `verify` (shallow) returned
`ok: true, verified: 1, failed: 0`. **Deep verify failed** on the Python floor — see §7.

---

## 4. Unreviewed / undeclared OCR documents are never auto-posted

Holds by construction, and was observed:

* validation leaves the document in `Validated` (§3.5);
* `NeedsReview` and `DuplicateRejected` refuse posting with `409` (§3.6);
* posting requires `Approved`, which requires a separate approver (§3.7);
* the review policy gate refuses approval while any required field is undecided
  (`REQUIRED_FIELD_NOT_DECIDED:*`, exercised by `tests/test_phase29_ocr_review_policy.py`, 35 tests)
  — an OCR document cannot reach `Approved` with an unreviewed required field.

## 5. Purchasing → stock → accounting

The document chain above covers the accounting leg end-to-end on a live server. The
receipt/stock/3-way-match leg is covered by the automated suite
(`tests/test_phase4_procurement_3way_match.py`, `test_phase6_ocr_benchmark_e2e.py`) rather than by an
HTTP walk in this phase; `test_phase6_ocr_benchmark_e2e.py::test_complete_14_step_end_to_end_supplier_invoice_workflow`
is the 14-step version and **fails here only on the Python floor** (§7). No product defect is claimed
for that leg on the basis of this document.

## 6. Negative-path summary

| Attempt | Result |
|---|---|
| anonymous read | `401` |
| post without `Idempotency-Key` | `400` |
| post a `NeedsReview` document | `409` |
| post a `DuplicateRejected` document | `409` |
| creator approves own invoice | `403 SEGREGATION_OF_DUTIES_VIOLATION` |
| wrong role posts | `403` role refusal |
| invalid NIF | `422` |
| duplicate NIF | `422` |
| unknown SCF account | `422` |
| duplicate SKU | `422` |

No case produced a `500`, and no case silently succeeded.

---

## 7. Excluded from this evidence, and why

| Excluded | Reason |
|---|---|
| Deep backup verification / isolated restore | Python 3.11.2 < 3.11.17 floor → `FinancialIntegrityError` |
| 14-step OCR E2E test | same floor (`test_phase6_ocr_benchmark_e2e.py`) |
| Window/UI walk of the review dialog | covered by the 55 headless desktop tests, not by this HTTP walk |
| Sales cycle and stock movements over HTTP | covered by the automated suite; **not** re-walked here. Not claimed |

## 8. Reproduction

`/tmp/e2e.py` in the sandbox is a throwaway driver; the commands are the `curl`-equivalent calls
quoted in each row above. Every number in this document can be re-derived from the database with:

```sql
SELECT id, supplier_invoice_no, workflow_state FROM supplier_invoices;
SELECT action_type, COUNT(*) FROM audit_events GROUP BY action_type;
SELECT code, debit_dzd, credit_dzd FROM journal_entry_lines;   -- as exposed by the trial balance
```
