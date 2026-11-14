# Phase 67 — End-to-End Business Acceptance

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| How | A real server process (`python -m dz_erp.serve`, CPython 3.12.10, loopback), driven over HTTP with real logins. Purchase orders, goods receipts and the stock deduction have no HTTP route and were called at library level against the same database — each such step says so. |
| Data | **Synthetic only**: `SARL Synthetique Acceptation`, supplier `SARL Fournisseur Synthetique`, item `SYN-001`, six users. No real company data. |
| Checks | **96 recorded; 94 pass.** The two that do not: one is a real finding (W67-3), one is an error in the check itself (the workbook label it looked for was misspelled; the workbook is correctly labelled). |
| Evidence | `evidence/acceptance_checks.json` — every check with expected and actual, session tokens redacted |
| Defects found | **Two, both fixed with regression tests** (§3). Before the fix, workflow 17 failed. |

Workflow numbers are this report's own. The mission's list of 25 was not available verbatim to this
session, so the table covers the business cycle of an accounting ERP and marks everything not
executed as NOT TESTED rather than leaving it out.

## 1. Summary

| # | Workflow | Result |
|---|---|---|
| 1 | Company initialisation, first admin, login, sessions | **PASS** |
| 2 | Chart of accounts | **PASS** |
| 3 | Fiscal year and period setup | NOT TESTED — not implemented |
| 4 | Customer master data | **PASS** (record only; nothing uses it) |
| 5 | Supplier master data | **PASS** |
| 6 | Item / warehouse master data | **PASS** |
| 7 | Purchase invoice entry, duplicate control, three-way match | **PASS** |
| 8 | Approval, segregation of duties, posting, idempotency | **PASS** |
| 9 | Supplier balance | **PASS** (derived from the ledger; there is no supplier-statement report) |
| 10 | Purchase order and goods receipt | **PASS** at library level — **no HTTP route** |
| 11 | Sales invoice | NOT TESTED — outside version 1 |
| 12 | Customer receipt | NOT TESTED — outside version 1 |
| 13 | Supplier payment | NOT TESTED — not implemented |
| 14 | Stock issue / adjustment | **FAIL** — finding W67-3 |
| 15 | Bank and cash, reconciliation | NOT TESTED — not implemented |
| 16 | Document upload, OCR, review | **PARTIAL** — upload and encrypted storage pass; field review not exercised here |
| 17 | Credit note / reversal | **PASS after fix** (failed before — W67-1, W67-2) |
| 18 | Trial balance | **PASS** |
| 19 | Journal and general ledger | **PARTIAL** — entries unique and balanced; no ledger report exists |
| 20 | Period close and reopen | NOT TESTED — not implemented |
| 21 | Stamp duty (timbre) | **PASS** as software behaviour; legal correctness is G5 |
| 22 | G50 preparation | **PASS** as a preparation workbook; it is not a filing |
| 23 | Imports / customs | NOT TESTED — outside version 1 |
| 24 | Audit trail and immutability | **PASS** |
| 25 | Backup, restore, key rotation | **PASS** — see Phase 66 drill |

**15 PASS · 2 PARTIAL · 1 FAIL · 7 NOT TESTED.** "PASS" means the software did what it is designed to
do. It is not a statement that the design is legally correct.

## 2. Workflows

### 1 — Company initialisation and access

| | |
|---|---|
| Preconditions | Empty data directory; `ERP_PASSWORD_MIN_LENGTH=12` |
| Steps | Bootstrap admin; start server; log in; create clerk, approver, accountant, warehouse, auditor; negative probes; deactivate a user; log out |
| Expected | One company; server-side sessions; least privilege |
| Actual | Database created `0600`. Login 200; session resolves company and role server-side. Anonymous 401; wrong password 401; unknown `Host` 400; 10-character password 422; clerk managing users 403. Deactivated user's live token 401; admin deactivating self 409; revoked token 401. |
| Invariants | Exactly one company row; no secret in the security log |
| Result | **PASS** (19 checks) |

### 2 — Chart of accounts

| | |
|---|---|
| Steps | `GET /api/v1/accounts` |
| Actual | 294 accounts; `30`, `380`, `401`, `408`, `411`, `4456`, `642` present |
| Result | **PASS**. Whether the chart is the right one is P10-Q6. |

### 4, 5, 6 — Master data

| | |
|---|---|
| Steps | Create supplier with NIF/NIS/AI/RC, a customer record, a storable item |
| Actual | Created. Invalid NIF 422 "must be 15 or 20 digits"; duplicate NIF 422 naming the existing partner; auditor creating a partner 403; unknown account on an item 422; duplicate SKU 422. |
| Result | **PASS** |

### 10 — Purchase order and goods receipt (library level)

| | |
|---|---|
| Steps | PO 40 × 250,00; receive 40; replay the receipt; PO 10 × 300,00; receive 6; try to receive 9 more |
| Actual | Receipt valued 10 000,00, entry `STJ/1/00001` (Dr `30` / Cr `380`). Replay: `idempotent_replay`, no row added. Partial receipt → `PARTIALLY_RECEIVED` with a backorder. Over-receipt refused: "9.000000 exceeds remaining ordered quantity 4.000000". Auditor creating a PO refused. Stock over HTTP: 40. |
| Result | **PASS** for the logic. **There is no HTTP route for purchase orders or receipts**, so neither the API nor the desktop client can perform this workflow; an operator cannot do it without a developer. |

### 7 — Purchase invoice entry

| | |
|---|---|
| Steps | Enter invoice `FA-SYN-2026-001` matching PO and receipt; enter it again; enter it as `fa syn/2026-001`; enter one with wrong VAT that bills goods already billed |
| Actual | First: `Validated`, no errors. Second and third: `DuplicateRejected`. Fourth: `NeedsReview` with `HEADER_TVA_MISMATCH` and `THREE_WAY_MATCH_QTY_MISMATCH`. Upload of a synthetic PDF: stored, `security_status: CLEAN`; the vault blob is not a readable PDF. |
| Note | Rejections answer **HTTP 200** with the state in the body, and each error appears twice (S63-6). |
| Result | **PASS** |

### 8 — Approval and posting

| | |
|---|---|
| Steps | Post before approval; submit; clerk approves; approver approves; approver posts; post without a key; post; replay; post with a new key; submit and post the invalid invoice |
| Expected | Only an approved invoice posts, once, by an accountant, approved by someone else |
| Actual | 409 before approval · clerk approve 403 · approver post 403 · missing `Idempotency-Key` 400 · post 200 → `ACH/2026/00001`, 11 900,00 = 11 900,00 · same key: same entry, `idempotent_replay` · new key: 409 "already Posted" · invalid invoice: submit 422, post 409. Creator approving their own invoice: 403 `SEGREGATION_OF_DUTIES_VIOLATION`. |
| Invariants | Exactly one entry and three lines added by the post; nothing added by replays or refusals |
| Result | **PASS** (18 checks) |

### 9 — Supplier balance

| | |
|---|---|
| Actual | Posted lines on `401` for the supplier: 11 900,00 after the first invoice, 14 063,42 after the second, 2 163,42 after the reversal |
| Result | **PASS**. No supplier statement or ageing report exists; the figure was computed from the ledger. |

### 21 — Stamp duty

| | |
|---|---|
| Steps | Cash invoice 1 800,00 + 342,00 VAT declaring no duty; the same declaring 21,42 |
| Actual | First held: `TIMBRE_MISMATCH:declared=0.00!=expected=21.42_for_CASH`. Second posts: Dr `380` 1 800,00 · Dr `4456` 342,00 · Dr `642` 21,42 · Cr `401` 2 163,42. |
| Result | **PASS** as behaviour. Rate, base and account are P10-Q1/Q2 — unanswered. |

### 14 — Stock issue

| | |
|---|---|
| Steps | Deduct 5 units (library helper `deduct_stock_concurrent_safe`); try to deduct 1 000 |
| Actual | On-hand 46 → 41; over-deduction refused. **No `stock_moves` row and no journal entry were written.** On-hand (41) no longer equals receipts minus issues in `stock_moves` (46), and account `30` still carries the value of 46 units. An audit event is recorded. |
| Result | **FAIL** — finding W67-3 |

### 17 — Credit note / reversal

| | |
|---|---|
| Preconditions | Invoice 1 posted |
| Steps | Clerk requests reversal; accountant requests; replay; second request; submit; requester approves; approver approves; post; replay |
| Expected | A second person approves; the original is untouched; the ledger nets out |
| Actual **before the fix** | Request 200, credit note in `NeedsReview` with a `DRAFT` entry. Then submit **422**, approve **422**, post **409** — the credit note could never be completed. A second request answered **HTTP 500**. |
| Actual **after the fix** | Clerk 403 · request 200 · replay returns the same credit note · second request **409** "already has a reversal (credit note 7, state 'NeedsReview')" · submit 200 · requester approving 403 segregation of duties · approver 200 · post 200 `RAVOIR/2026/00001` · replay idempotent. |
| Invariants | Books unchanged while the credit note is unapproved; after posting the supplier balance drops by exactly 11 900,00; the original invoice is still `Posted` with its three lines intact |
| Result | **PASS after fix** (12 checks) |
| Not tested | Partial reversal (not implemented). Whether the reversed quantity becomes billable again on the purchase order. Reversal of a cash invoice with stamp duty. |

### 18, 19 — Trial balance and journal

| Account | Debit | Credit |
|---|---|---|
| `30` Stocks de marchandises | 11 800,00 | 0,00 |
| `380` | 11 800,00 | 21 800,00 |
| `401` | 11 900,00 | 14 063,42 |
| `4456` | 2 242,00 | 1 900,00 |
| `642` | 21,42 | 0,00 |
| **Total** | **37 763,42** | **37 763,42** |

Every one of the five entries balances on its own; entry numbers are unique: `STJ/1/00001`,
`ACH/2026/00001`, `STJ/1/00002`, `ACH/2026/00006`, `RAVOIR/2026/00001`. The auditor role can read the
trial balance. **PASS** for the trial balance.

Two things for the expert, not defects of arithmetic: purchase entry numbers follow the invoice id and
are **not gapless** (`00001`, then `00006`); and after the reversal `380` carries a 10 000,00 credit —
goods received and no longer invoiced. There is no general-ledger or journal report, hence PARTIAL
for 19.

### 22 — G50 preparation

| | |
|---|---|
| Actual | Workbook generated for `2026-11` with 3 traced lines, status `PREPARATORY_WORKBOOK_REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF`. A request on behalf of another company raises `TenantIsolationError`. |
| Result | **PASS** as a preparation aid. Library level only; box mapping is P10-Q3. |

### 24 — Audit trail and immutability

| | |
|---|---|
| Steps | With direct SQL on the database file: change and delete a posted line, a posted entry, an audit event. Verify the chain. |
| Actual | All six refused by trigger (`FINANCIAL_INTEGRITY_VIOLATION`, `AUDIT_TAMPER_VIOLATION`). Chain valid. Security log: 71 records, no password or token. |
| Result | **PASS** |

### 25 — Backup, restore, key rotation

See `docs/release/phase66/RECOVERY_DRILL_REPORT.md`. **PASS** on synthetic data.

## 3. Defects

| ID | Defect | Severity | Status |
|---|---|---|---|
| W67-1 | **A credit note could not be approved or posted over HTTP.** The reversal handlers were registered on routes whose path templates were identical to the invoice routes; the router always chose the invoice handlers, which refused the credit note. The reversal entry stayed `DRAFT` forever while the "reverse" call reported success. The desktop client uses the same URLs. The existing tests drove approval and posting of a credit note by calling the functions directly; over HTTP only the reversal *request* and its authentication were tested, so nothing caught it. | **High** — an advertised accounting workflow did not work | **Fixed** `c4c48a1`. The three invoice routes dispatch credit notes to the existing reversal functions. No accounting rule changed. |
| W67-2 | A second reversal request for the same invoice returned HTTP 500 (unhandled unique-constraint error). No data was damaged. | Medium | **Fixed**, same commit: refused with 409 before anything is written. |
| W67-3 | **The stock deduction helper changes the quantity on hand without a stock move and without an accounting entry.** | High if it were reachable; **it is not reachable** from the API or the desktop client | **Open — product owner + Expert-Comptable.** Not fixed: recording an issue needs a valuation rule, which is an accounting decision. Until then the helper must not be exposed. |
| W67-4 | One invoice can have only one credit note, ever. If that credit note is **rejected**, the invoice can no longer be reversed. | Medium | **Open — product owner + expert** (P10-Q8). Behaviour unchanged; it was previously hidden behind the HTTP 500. |
| W67-5 | Purchase orders and goods receipts have no HTTP route. | Scope gap | **Open — product owner.** Three-way match is only usable if a developer creates the order and receipt. |

Regression tests: `tests/test_phase67_reversal_over_http.py` — 5 tests, of which 4 fail without the
fix (full approval cycle, rejection, clean refusal of a second request, and "no workflow path is
registered twice"); the fifth confirms another company cannot drive the credit note.
`tests/test_phase8_upload_boundary_and_ci.py` pinned 31 protected operations in the OpenAPI document;
three of those were the unreachable duplicates, so the pin is now 28 with the reason recorded. No URL
was removed.

## 4. Essential-workflow verdict

The supplier-side cycle — master data, invoice, approval, posting, reversal, trial balance, audit,
backup and restore — passes on synthetic data after the fixes. Sales, payments, bank, period close and
imports do not exist. Whether that scope is enough for a first production use is a business decision
that has not been made in writing.
