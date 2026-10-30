# Phase 32 — independent integration, security and accounting audit

**Scope.** Phases 29, 30 and 31 as one integrated financial-document workflow:
source document → OCR extraction → extracted-field persistence → human review →
`CONFIRMED`/`CORRECTED` decisions → staleness detection → invoice creation →
submission → approval → journal posting → trial balance → audit-chain
verification.

**Nature of this phase.** Audit and verification only. One reproducible defect
was found and fixed (P32-F1); the other four findings are reported with a
recommended remediation and no code change, because each needs a product
decision rather than a mechanical correction (§9). No file under `docs/` that
records an external gate was converted into a claim of completion.

> **Phase 33 addendum.** The four findings this report left open (P32-F2 … P32-F5)
> were subsequently reproduced and remediated on a dedicated Phase 33 branch
> stacked on this report's verified head. The finding texts in §8 are preserved
> **exactly as they were raised in Phase 32** and describe the Phase 32 state;
> §14 records the remediation, the state-machine changes, the tests that now
> assert the resolved behaviour, and the evidence. §11 (external production
> gates) is unchanged — nothing in Phase 33 closes an external gate.

**Method.** Every claim below is either (a) a command run on this host against
the verified Phase 31 tree, with its exit code, or (b) a GitHub API fact about
the exact published SHA. Nothing is asserted from a phase's own test report. The
cross-phase matrix lives in
[`tests/test_phase32_cross_phase_integration_audit.py`](../tests/test_phase32_cross_phase_integration_audit.py)
(44 tests, sections A–W) so each statement about behaviour can be re-run.

---

## 1. Verified repository baseline

| Item | Value | How verified |
|---|---|---|
| Repository | `Devmraustro/ERP` | `git remote -v`, `gh pr list` |
| `main` head | `4fc1364a9504fab1153ea44e753f354f00023428` ("Merge pull request #2") | `git rev-parse origin/main` |
| Stated Phase 31 head | `e4e47b3bf4376e804f17b23e63c86f7fe384056c` | `gh pr view 6 --json headRefOid` |
| PR #6 head ref | `fix/phase29-invoice-discount-and-line-net-consistency` @ `e4e47b3b…` | `refs/audit/pr6` fetched from `refs/heads/…` |
| SHA unchanged? | **Yes.** The published Phase 31 SHA matches the stated value exactly. | — |
| Audit branch | `arena/7f12d627-erp`, fast-forwarded from `4fc1364` to `e4e47b3` (no merge commit, no rewrite, `main` untouched) | `git merge --ff-only refs/audit/pr6` |
| Working tree at audit start | clean (`nothing to commit`) | `git status` |

Ancestry was verified explicitly rather than inferred from the PR list:

```
main -> pr3 OK    (29645e5d, "feat(ocr): fail-closed field-level review …")
pr3  -> pr4 OK    (99864bab, 5 commits)
pr4  -> pr5 OK    (c466cea7, 1 commit)
pr5  -> pr6 OK    (e4e47b3b, 1 commit)
git rev-list --count origin/main..refs/audit/pr6  ->  8
```

`git diff --stat main..pr6`: 20 files, +4700/−134; one new module
(`dz_erp/review_policy.py`, 547 lines), no schema migration file (the schema is
embedded in `dz_erp/database.py`).

## 2. PR dependency chain status

| PR | Base | Head | Head SHA | Draft | Mergeable / state | CI on head SHA |
|---|---|---|---|---|---|---|
| #3 Phase 29 fail-closed review | `main` | `phase29/ocr-failclosed` | `29645e5d` | no (open) | `MERGEABLE` / `CLEAN`, `OPEN` | run 40763416753 — 3/3 jobs pass |
| #4 Source→invoice values | `phase29/ocr-failclosed` | `fix/phase29-source-invoice-consistency` | `99864bab` | **yes** | `MERGEABLE` / `CLEAN`, `OPEN` | run 40775473917 — 3/3 pass |
| #5 Line unit price | `fix/phase29-source-invoice-consistency` | `fix/phase29-invoice-line-price-consistency` | `c466cea7` | **yes** | `MERGEABLE` / `CLEAN`, `OPEN` | run 40965006912 — 3/3 pass |
| #6 Line discount + derived net | `fix/phase29-invoice-line-price-consistency` | `fix/phase29-invoice-discount-and-line-net-consistency` | `e4e47b3b` | **yes** | `MERGEABLE` / `CLEAN`, `OPEN` | run **41064213366** — 3/3 pass |

The chain is linear and additive: each PR's base is the previous PR's head, so
merging in order #3 → #4 → #5 → #6 is the only order that preserves ancestors.
**No PR was merged, closed, rebased, force-pushed or retargeted during this
audit**, and `main` was not modified.

CI evidence for the exact published SHA (`e4e47b3b`, run 41064213366):

* *Test, coverage and static checks (Python 3.11)* — every step success,
  including **"Run the complete test suite with coverage"**, **"Run isolated
  synthetic staging acceptance"** and **"Validate risk-register summary"**.
* *Test, coverage and static checks (Python 3.12)* — full suite success
  (staging/register steps skipped by design for the second matrix leg).
* *Key rotation, backup and recovery on Windows* — rotation/migration/
  backup-restore/recovery tests and Windows staging acceptance success.

## 3. End-to-end workflow audit

Each transition was traced in the code *and* exercised. The authoritative source
of each value and the transaction boundary that guards it:

| # | Transition | Authoritative data | Transaction | Validation enforced | Tenant scope | Audit event | Failure behaviour |
|---|---|---|---|---|---|---|---|
| 1 | upload → OCR → field rows | raw file + engine output (`ocr_extracted_fields`), immutable under trigger | one `BEGIN IMMEDIATE` for persistence; OCR outside the lock | file security scan, SHA-256 duplicate, size/type bounds | `company_id` from the credential; vault path per company | `OCR_EXTRACTION_COMPLETED`, `DUPLICATE_DOCUMENT_REJECTED` | nothing persisted; document row created only after the scan |
| 2 | extraction → human decision | decision row (`ocr_field_reviews`) bound to the *exact* value | one `BEGIN IMMEDIATE` that reads the evidence and writes the decision | role (`clerk/approver/accountant/admin`), document state ∈ {NeedsReview, Validated}, value-class validation, expected-value equality | document + user must share `company_id` | `OCR_FIELD_REVIEW_DECIDED`; refusals `SECURITY_FIELD_REVIEW_BLOCKED` | refusal → rollback, decision not written; replay is idempotent |
| 3 | decision → staleness | recomputed on every evaluation from stored rows (no cache) | read inside the gate's transaction | `decision_value == current value` else `decision_stale`; `NOT_PRESENT` invalidated when a value appears | same | — (computed) | stale ⇒ document unsatisfied ⇒ invoice cannot move |
| 4 | invoice creation | typed payload + `payload_sha256` over canonical line/header values | one `BEGIN IMMEDIATE` | per-line arithmetic, TVA ∈ {0, 9, 19}, discount ∈ [0, 100], header HT/TVA, timbre, TTC, duplicate invoice number, 3-way match | supplier, PO line, document all re-checked in `company_id` | `INVOICE_VALIDATED_STAGED` | any error ⇒ `NeedsReview`, unsubmittable |
| 5 | creation → document link | `source_documents.workflow_state` is driven by the invoice | same transaction | document must be in `DOCUMENT_LINKABLE_STATES` before it is cited | `id`+`company_id` lookup | (in event 4) | not linkable ⇒ `DOCUMENT_LINK_STATE_REFUSED` |
| 6 | submit | reviewed evidence + invoice payload | one `BEGIN IMMEDIATE`; gate reads evidence and writes state | review policy satisfied **and** `_source_invoice_mismatches` empty | gate re-reads document/fields/decisions under `company_id` | `APPROVAL_REQUESTED`, or `INVOICE_SUBMIT_BLOCKED_UNREVIEWED` / `…_SOURCE_MISMATCH` (commit-then-refuse) | invoice stays `Validated`, no approval row |
| 7 | approve | same evidence, re-evaluated | one `BEGIN IMMEDIATE` | same gate again + SoD (`creator ≠ approver`) + state must be `PendingApproval` | same | `INVOICE_APPROVED` / `_BLOCKED_*` | stays `PendingApproval`; no decision written |
| 8 | post | approved payload digest | one `BEGIN IMMEDIATE` | state `Approved`, `payload_sha256 == approved_payload_sha256`, idempotency key bound to request hash, 3-way re-check, debits == credits | invoice, PO line, product lookups by `company_id` | `INVOICE_POSTED` | no journal entry, no `qty_invoiced` change |
| 9 | post → trial balance | `journal_entry_lines` of `POSTED` entries only | read snapshot | aggregate refused if `Σdebit ≠ Σcredit` | per company | — | report refused, not silently unbalanced |
| 10 | any → audit chain | `audit_events` HMAC chain per key version | append-only trigger | `verify_audit_chain` re-verifies every event and continuity | per company | — | `valid: false` / `AUDIT_KEY_UNAVAILABLE` |

**Stale/modified-value bypass analysis.** The three reviewed line elements are
all compared at *both* submit and approval, from rows read inside the same
`IMMEDIATE` transaction that moves the invoice: quantity
(`line_item_json`), unit price (`line_unit_price`), discount (`line_discount`),
plus the line count, and the net re-derived as
`round_dzd(qty × PU × (100 − discount)/100)` from the *decided* values (never
from the payload's derived `line_ht_dzd`). Tests B, C, D, F, F2, P and T exercise
each of these paths and confirm the refusal, the unchanged invoice state and the
audit row.

Two residual gaps found (§9): the *decision* path cannot be used after the
document leaves review (deliberate), but the *invoice* can still be modified by
`modify_draft_invoice_line`, which moves the invoice to `NeedsReview` while the
document and the approval request stay where they were (P32-F4).

## 4. Financial integrity findings

| Check | Result | Evidence |
|---|---|---|
| Decimal-only arithmetic, floats refused | **Pass** — `to_decimal` raises `FloatMonetaryError` on a binary `float`, and no monetary path converts through `float` (the only `float()` uses in the codebase read OCR confidence scores, which are not money) | `dz_erp/decimal_utils.py`; `grep -n "float(" dz_erp/*.py` |
| Non-finite values (`NaN`, `±Infinity`) | **Defect found and fixed** (P32-F1) — they survived `quantize` and were persisted | tests `test_s_*`, `test_s2_*` |
| Quantity / price precision | **Pass** — both canonicalised to 6 dp everywhere (decision, payload rewrite, invoice column, gate) | tests A, B, C, G |
| Discount range 0–100 inclusive, never clamped | **Pass** — decision path refuses `<0` (`FIELD_VALUE_NEGATIVE_AMOUNT`) and `>100` (`FIELD_VALUE_DISCOUNT_OUT_OF_RANGE`); invoice creation refuses out-of-range as `LINE_n_DISCOUNT_OUT_OF_RANGE` and leaves the invoice unsubmittable | tests `test_i_*` |
| Discount canonical form / rounding | **Pass with a documented boundary** — decisions bind the 2-decimal form; an invoice validated against a raw 3-decimal discount is refused at the gate (`LINE_n_LINE_HT_SOURCE_MISMATCH`) because its stored discount and its stored net disagree. Fail-closed and documented; recorded as the intended behaviour, not a defect | `test_i4` |
| Line HT re-derivation | **Pass** — identical formula (`round_dzd(qty·PU·(1−d/100))`) in decision rewrite, invoice validation, gate and draft modification | `test_a`, `test_i4`, `test_u` |
| Header HT / TVA / TTC reconciliation | **Pass** — `Σline_ht == header_ht`, `Σline_tva == header_tva`, `ttc == ht+tva+timbre` | `test_a`, `test_i3`, `test_v` |
| Timbre | **Pass as implemented** — non-cash = 0.00; cash above 300.00 DZD uses the configured brackets with a 5.00 floor; an invoice whose timbre disagrees with the reviewed evidence is refused (`AMOUNT_TIMBRE_DZD_SOURCE_MISMATCH`) | `test_v` |
| Journal-entry balancing | **Pass** — `Σdebit == Σcredit` checked before insert, enforced again by `CHECK (total_debit_dzd = total_credit_dzd)` | `test_a`, `test_n` |
| Posting idempotency | **Pass** — replay returns the cached response, a new key on a posted invoice is refused, a key bound to another payload is refused | `test_l` |
| Duplicate supplier invoice prevention | **Pass** — exact normalised number + fuzzy (Levenshtein ≤ 2 within 1.00 DZD) at creation; partial `UNIQUE` index for posted `IN_INVOICE` rows | `test_t`, schema |
| Trial balance integrity | **Pass** — POSTED only, per-account Decimal aggregation, refused if unbalanced | `test_a`, `test_n` |
| Reversal / correction | **Pass on amounts, weak on refusal shape** — a reversal mirrors the original lines, keeps the payable netting to zero and the ledger balanced; a second reversal of the same invoice is blocked only by `UNIQUE(company_id, entry_number)` and surfaces as an unhandled `sqlite3.IntegrityError` (P32-F5) | `test_n`, `test_w` |

**Algerian accounting rules — external review required (not changed here).**
The timbre brackets (`1 % ≤ 30 000`, `1.5 % ≤ 100 000`, `2 %` above, floor
5.00 DZD, threshold 300.00 DZD), the admissible TVA rates `{0, 9, 19}` and the
G50 tags are hard-coded defaults in `dz_erp/localization.py`. This audit did not
alter them; their currency and completeness is a matter for **qualified Algerian
SCF and tax professional review** (see §12).

## 5. Security and data integrity findings

| Area | Finding | Evidence |
|---|---|---|
| Cross-tenant access | **No bypass found.** Every write path resolves the tenant from the credential and re-checks the target row's `company_id`; a cross-company document lookup fails closed as *not found* (no existence oracle) and the read endpoint audits `SECURITY_CROSS_COMPANY_READ_BLOCKED` | `test_k`, `test_k2`, `evaluate_document_review_policy_secure` |
| Authentication / authorisation | Decision endpoint requires an active user with a review-capable role; posting requires `accountant/admin`; SoD enforced on invoice *and* reversal approval, and again by a table-level `CHECK (decision != 'APPROVED' OR decided_by_id != requested_by_id)` | `test_a`, `test_j2`, schema |
| API input validation | Pydantic models type amounts as `str`; **P32-F1** was the one real gap (non-finite strings) and is now refused | `test_s*` |
| SQL isolation / TOCTOU | Every gate runs inside the same `BEGIN IMMEDIATE` that mutates state; decisions re-read the evidence they judge. Concurrent submit/approve attempts produce exactly one winner | `test_j`, `test_j2`, `test_p` |
| Stale decision / payload-hash binding | Decisions bind to the reviewed value; the approved-payload digest now covers `discount_pct` (Phase 31); posting refuses a digest mismatch | `test_f2`, `test_l2` |
| Audit-chain integrity | Append-only triggers block `UPDATE`/`DELETE`; `verify_audit_chain` returns `valid: true` after the full matrix, including every refusal | `test_a`, `test_o`, `test_r` |
| Key rotation / recovery | Out of scope of this chain; verified by the existing rotation suites and the Windows CI job on the exact SHA | CI run 41064213366 |
| Backup / restore | Not executable on this host (`Python 3.11.2` < the 3.11.17 safe-extraction floor). CI on the exact SHA runs and passes both the POSIX restore suite and the Windows backup/recovery job | §11 |
| Sensitive document access | Vault blob path is company-scoped and the read path audits cross-company attempts; no plaintext document bytes in transit through the API layer | `test_k`, `intake_ocr.PrivateDocumentVault` |
| Logging / information disclosure | Refusal details are bounded (`[:20]`) and audited; no key material or password reaches the audit payloads (`SENSITIVE_KEYS` redaction) | `test_o`, `security_audit` |
| Migration safety | Idempotent, additive; a simulated pre-Phase-29 database re-gains the column, the review table and the refreshed trigger with **zero row loss**, and the migrated trigger still refuses raw-column rewrites | `test_r` |

## 6. Migration and backward compatibility

* **No new migration was introduced.** The Phase 29 change set adds one nullable
  column (`ocr_extracted_fields.conflict_values_json`), one table
  (`ocr_field_reviews`) with an index, and re-creates
  `trg_ocr_field_preserve_raw` (`DROP`+`CREATE`, because `CREATE TRIGGER IF NOT
  EXISTS` would leave the old body). Phase 30/31 add **no schema at all**:
  `ocr_field_reviews.field_name` is free `TEXT`, so `line_unit_price` and
  `line_discount` are new *values*, not new columns.
* **Repeated execution is safe** — reopening the database twice re-runs the
  migration as a no-op (`test_r`).
* **Legacy financial records are not rewritten** — the same test asserts an
  existing `Validated` invoice keeps its state, amounts and payload digest.
* **Document-less / manual invoices are preserved** and skip the review gate
  entirely (`test_m`).
* **Integrity hashes** include the new financial field: `compute_invoice_payload_hash`
  now binds `discount_pct` (Phase 31), so a discount edit changes the approved
  digest and posting refuses it (`test_l2`).
* **Backup/restore compatibility** could not be executed here (§11); CI on the
  exact SHA covers it.

## 7. Cross-phase test matrix and results

`tests/test_phase32_cross_phase_integration_audit.py` — **44 tests, 43 passed, 1
skipped (environment), 0 failed, 7.08 s**.

| Section | Scenario | Expected | Result |
|---|---|---|---|
| A | fully reviewed invoice, matching evidence → post | posted once, ledger balanced, trial balance balanced, chain valid, document `Posted` | pass |
| B | quantity corrected after invoice creation | `LINE_1_QUANTITY_SOURCE_MISMATCH`, invoice stays `Validated`, refusal audited | pass |
| C | price corrected after invoice creation | `LINE_1_PRICE_SOURCE_MISMATCH`, unchanged state | pass |
| D | discount transcribed after invoice creation | `LINE_1_DISCOUNT_SOURCE_MISMATCH`, unchanged state | pass |
| E | invoice line modified after submission | approval refused (not `PendingApproval`), re-submit refused (not `Validated`), no journal entry | pass |
| F | source field edited after its decision | decisions closed once submitted (`FIELD_REVIEW_CLOSED`/`FIELD_EDIT_CLOSED`) | pass |
| F2 | stale decision then re-decision | stale ⇒ refuse; decided value disagreeing with the invoice ⇒ refuse (`INVOICE_DATE_SOURCE_MISMATCH`); restored value ⇒ submit succeeds | pass |
| F3 | re-decided total breaks header arithmetic | `INCONSISTENT_EXTRACTED_TOTALS`, refused | pass |
| G | line with no extracted price / malformed payload | `CONFIRMED` refused, transcription repairs; malformed JSON ⇒ `LINE_PAYLOAD_UNPARSEABLE` | pass |
| G2 | (same test group) malformed JSON not guessed | document unsatisfied | pass |
| H | swapped line order | positional mismatch flagged, never rematched | pass |
| H2/H3 | duplicate identical source rows | both rows required; collapsing to one ⇒ `LINE_COUNT_SOURCE_MISMATCH` | pass |
| I | discount `100.01`, `150`, `-1` | refused with the specific reason code, nothing stored | pass (3 params) |
| I2 | discount `100.00` | accepted, net re-derived to `0.00`; the resulting contradiction with the printed header is refused at the gate | pass |
| I3 | invoice-side discount `100.50` | `NeedsReview`, unsubmittable | pass |
| I4 | raw 3-decimal invoice discount vs canonical decision | `LINE_1_LINE_HT_SOURCE_MISMATCH` (documented rounding policy) | pass |
| J/J2 | concurrent submit / concurrent approve | exactly one winner, one approval row, one decision | pass |
| K/K2 | cross-company decision, verdict read, invoice creation | refused (403/404 shape), nothing written, cross-company read audited | pass |
| L | posting retries / duplicate posting / key reuse | replay idempotent, second key refused, key bound to payload | pass |
| L2 | payload mutated after approval | `APPROVAL_PAYLOAD_HASH_MISMATCH`, no journal entry | pass |
| M | legacy document-less invoice | full flow without a review gate | pass |
| N | reversal after reviewed posting | balanced ledger, payable netted to zero, no second credit note | pass |
| O | refused submission | no state change, no approval row, refusal audited, chain valid | pass |
| P | out-of-band line edit between submit and approve | `LINE_1_PRICE_SOURCE_MISMATCH` at approval, audited | pass |
| Q | backup + isolated restore after posting | posted invoice, journal entry and review decisions survive; restored ledger balances at 1190.00 and the restored audit chain verifies | **skipped on this host** (§10) — executed in CI |
| R | pre-Phase-29 database migration | column/table/trigger restored, no row loss, repeat run is a no-op, trigger still enforced | pass |
| S/S2 | non-numeric amounts and decision values | 422, nothing persisted (P32-F1 regression, 5 + 4 params) | pass |
| T | `NeedsReview` recovery path | **P32-F2/F3 regression** (Phase 33) — corrected in place to `Validated`, duplicate re-creation refused `DOCUMENT_ALREADY_INVOICED`, document follows its invoice | pass |
| T2 | previous failed validation authorizes posting? | **P32-F2 regression** (Phase 33) — approved digest is the corrected one, never the rejected one | pass |
| U | draft modification state drift | **P32-F4 regression** (Phase 33) — request `INVALIDATED` (`PAYLOAD_MODIFIED_AFTER_SUBMISSION`), invoice and document both `NeedsReview`, approved digest cleared | pass |
| V | CASH invoice timbre vs reviewed evidence | `AMOUNT_TIMBRE_DZD_SOURCE_MISMATCH` | pass |
| W | second reversal of the same invoice | **P32-F5 regression** (Phase 33) — `INVOICE_ALREADY_REVERSED` domain refusal, one posted credit note, original entry untouched | pass |

Sections T, U and W originally *characterised* the open findings; Phase 33
converted them into resolution assertions (`T2` was added) rather than deleting
or skipping them. The findings-as-found behaviour remains reproducible from the
Phase 32 tree at `9092e42a`; the Phase 33 matrix
[`tests/test_phase33_invoice_lifecycle.py`](../tests/test_phase33_invoice_lifecycle.py)
now carries the detailed evidence. File totals after Phase 33: **45 collected,
44 passed, 1 skipped (Q — interpreter floor), 0 failed**.

No existing test was weakened, deleted or skipped, and no assertion was relaxed
to obtain these results.

## 8. Defect list

### P32-F1 — Non-finite amounts are accepted and persisted in money columns · **Medium** · **FIXED**

* **Where:** `dz_erp/decimal_utils.py::to_decimal` (root cause),
  `dz_erp/procurement_stock.py::create_and_validate_supplier_invoice`
  (`round_dzd(...)` of header amounts),
  `dz_erp/intake_ocr.py::_apply_field_decision` via
  `dz_erp/review_policy.py::validate_corrected_value`.
* **Reproduction (before the fix):**
  `POST /api/v1/invoices` with `amount_ht_dzd: "NaN"` → **200 OK**; the row
  stores `amount_ht_dzd = 'NaN'` (TEXT) with state `NeedsReview`.
  `POST /api/v1/documents/{id}/fields/decide` with
  `{"field_name":"amount_ht_dzd","decision":"CORRECTED","value":"NaN"}` →
  **500 Internal Server Error** (`decimal.InvalidOperation` escaping the
  documented `ReviewPolicyValueError` contract).
* **Cause:** `Decimal('NaN')` is a *quiet* NaN and propagates through
  `quantize()`, so `round_dzd`/`format_dzd` silently return/render it;
  `Decimal('Infinity')` fails later at `quantize`, outside every `except
  ValueError`.
* **Expected:** a 422 with a machine-readable reason; nothing stored.
* **Actual (before):** 200 + poisoned row; or an unhandled 500.
* **Impact:** data quality in a financial table (non-numeric amounts, a
  misleading success response, unhandled exceptions in the API). **Not** a
  posting bypass: such an invoice can never reach `Validated` (the header
  mismatch always trips), so it can never be submitted, approved or posted.
* **Remediation applied (minimal):** one guard `_require_finite()` in
  `to_decimal`, the single conversion point every arithmetic helper uses.
  `NaN`, `sNaN`, `Infinity` and `-Infinity` now raise `ValueError`, which every
  caller already handles (422 at the API, `FIELD_VALUE_INVALID_AMOUNT` at the
  decision service) — fail-closed with no behavioural change for finite values.
* **Regression tests:** `test_s_non_numeric_amounts_are_refused_and_never_persisted`
  (5 params, asserts 422 *and* zero rows written) and
  `test_s2_non_numeric_decision_values_are_a_422` (4 params, asserts 422 and no
  decision written).

### P32-F2 — `NeedsReview` is a terminal dead-end · **Medium** · reported, not fixed

**Status after Phase 33:** RESOLVED — `revalidate_supplier_invoice` (service) and `POST /api/v1/invoices/{invoice_id}/revalidate` (API) correct and re-validate the same invoice identity. See §14.1.

* **Where:** `dz_erp/procurement_stock.py` (no function re-validates an existing
  invoice; the only writer of `Validated` is
  `create_and_validate_supplier_invoice`), `dz_erp/api.py` (no correction
  endpoint), duplicate detection in `create_and_validate_supplier_invoice`.
* **Reproduction:** create an invoice whose header disagrees with its lines
  (e.g. header HT 900.00 vs lines 1000.00) → `NeedsReview`. Re-create the same
  invoice with the *corrected* header → `DuplicateRejected`
  (`EXACT_DUPLICATE_INVOICE_REF_OF_ID_<broken>`), because duplicate scanning
  excludes only `Rejected`/`DuplicateRejected`.
* **Expected:** a documented correction path (re-validate after edit, or a
  superseding invoice that replaces the broken draft).
* **Actual:** the paper invoice can never be booked through the product.
* **Impact:** operational (a mis-keyed invoice needs out-of-band SQL);
  no incorrect posting occurs.
* **Recommended remediation:** add an explicit `revalidate_supplier_invoice`
  service that recomputes validation + `payload_sha256`, keeps the audit trail,
  and is limited to `NeedsReview`/`DuplicateRejected` drafts; or let an
  authorised duplicate override supersede a known-broken draft.
* **Regression test required:** "broken draft → corrected draft reaches
  `Validated` → posts exactly once".

### P32-F3 — A failed duplicate attempt rewrites the linked document's state · **Medium** · reported, not fixed

**Status after Phase 33:** RESOLVED — a refused attempt never writes the document's state, and a second live invoice on one document is refused before any insert. See §14.2.

* **Where:** `create_and_validate_supplier_invoice`, the tail
  `UPDATE source_documents SET workflow_state = ?` (the *new* invoice's state).
* **Reproduction:** upload + fully review document D, create invoice A
  (`Validated`) citing D, then attempt invoice B with the same supplier number
  and a wrong header → B is `DuplicateRejected` **and D becomes
  `DuplicateRejected`** although A is still a valid invoice that cites it.
* **Expected:** the document state should follow the invoice that owns it, and a
  rejected attempt should not mutate evidence at all.
* **Actual:** the evidence row drifts to a state in which field decisions are
  closed (`FIELD_REVIEW_CLOSED`), while invoice A remains `Validated`.
* **Impact:** the document lifecycle no longer describes reality; repair of a
  later-stale decision on that document becomes impossible; the audit trail
  shows a "duplicate-rejected" paper backing a valid invoice.
* **Recommended remediation:** only link/refresh the document from an invoice
  that actually becomes the document's owner (`Validated`/`NeedsReview`), and
  never from a `DuplicateRejected`/`Rejected` attempt.
* **Regression test required:** duplicate attempt leaves the document state and
  the citing invoice untouched.

### P32-F4 — Draft modification desynchronises the document and orphans the approval · **Low/Medium** · reported, not fixed

**Status after Phase 33:** RESOLVED — every financial modification invalidates the approval basis explicitly and syncs the document; requests are bound to the payload digest and source-review fingerprint. See §14.3.

* **Where:** `dz_erp/procurement_stock.py::modify_draft_invoice_line`.
* **Reproduction:** submit a reviewed invoice (`PendingApproval`), then modify a
  line price. Invoice → `NeedsReview`; linked document stays `PendingApproval`;
  `approval_requests` keeps a `PENDING` row (the invalidation statement only
  matches `decision='APPROVED'`).
* **Expected:** the document mirrors the invoice and the abandoned approval is
  `INVALIDATED` with a reason.
* **Actual:** document state drift + an orphan pending approval on an invoice
  that can no longer be approved.
* **Impact:** misleading audit evidence (`PendingApproval` document with no
  pending approval; a `PENDING` request for a `NeedsReview` invoice). No money
  effect: approval and posting are both refused.
* **Recommended remediation:** in the same transaction, set the document to
  `NeedsReview` and mark the pending request `INVALIDATED`
  (`reason='PAYLOAD_MODIFIED_AFTER_SUBMISSION'`).
* **Regression test required:** after modification, document state == invoice
  state and no `PENDING` approval row remains.

### P32-F5 — A second reversal is refused only by a raw database constraint · **Low** · reported, not fixed

**Status after Phase 33:** RESOLVED — the conflict is detected before the insert and refused as `INVOICE_ALREADY_REVERSED` (HTTP 409) with an attributed audit event; the residual unique-constraint race is mapped to the same error. See §14.4.

* **Where:** `dz_erp/procurement_stock.py::create_reversal_request` (no
  "already reversed" check), `dz_erp/api.py` reverse endpoint (maps only
  `AuthorizationError`/`TenantIsolationError`/`FinancialIntegrityError`).
* **Reproduction:** reverse a posted invoice; reverse it again with a different
  `Idempotency-Key` → `sqlite3.IntegrityError: UNIQUE constraint failed:
  journal_entries.company_id, journal_entries.entry_number` → HTTP 500.
* **Expected:** a business refusal ("invoice already reversed by credit note
  #N") with no second credit note.
* **Actual:** the guarantee exists (no double credit; ledger stays balanced) but
  the caller sees an unhandled 500 and the platform logs a stack trace.
* **Impact:** availability/observability only; **no** double-crediting was
  possible in the exercised paths.
* **Recommended remediation:** check for an existing `IN_REFUND` request
  (`NeedsReview`/`PendingApproval`/`Approved`/`Posted`) for the invoice before
  creating another, and map the residual constraint violation to a 409.
* **Regression test required:** second reversal returns a documented refusal and
  creates exactly one credit note.

## 9. Fixes implemented

One fix, P32-F1, because it is the only finding with a reproducible defect and a
local, unambiguous correction:

```
dz_erp/decimal_utils.py                                    +24 −7   (_require_finite guard in to_decimal)
tests/test_phase32_cross_phase_integration_audit.py         new audit matrix (44 tests)
docs/PHASE32_INDEPENDENT_INTEGRATION_AUDIT.md               this report
docs/RISK_REGISTER.md                                       +5 rows (P1–P5), summary refreshed by the tool
tests/test_phase22_register_and_docs_consistency.py          drift probe made content-derived (see below)
```

`tests/test_phase22_register_and_docs_consistency.py::test_the_counts_tool_detects_drift`
corrupted a hard-coded summary cell (`| Medium | 3`). Adding the five register
rows changed that count, so the probe would have silently stopped corrupting
anything and passed vacuously. The probe now derives the cell it corrupts from
the rendered summary; it still asserts that the checker detects the drift, and
it keeps doing so as the register grows. No assertion was removed or relaxed.

No Algerian accounting or tax rule was changed. Findings P32-F2/F3/F4/F5 are
deliberately left unimplemented: each changes a documented lifecycle or API
contract and needs an owner decision (§8 lists the minimal remediation and the
regression test each will need).

## 10. Verification evidence (this host)

Environment: Linux, Python **3.11.2**, pytest 8.3.5, coverage 6.0.0,
`requirements.lock.hashes.txt` installed with `--require-hashes`; PySide6 absent
(2 desktop modules skipped by `importorskip`).

| Command | Exit | Result |
|---|---|---|
| `pytest tests/test_phase32_cross_phase_integration_audit.py` | 0 | **43 passed, 1 skipped**, 7.08 s |
| `pytest tests/test_phase29_ocr_review_policy.py` | 0 | 48 passed, 7.52 s |
| `pytest --cov=dz_erp --cov-report=term` (complete suite, final tree) | 1 | **762 passed, 20 failed (all environment), 7 skipped, 0 xfailed**, 190.83 s; coverage **77 %** (6 921 statements, 1 611 missed) |
| `pytest` before the P32-F1 fix (baseline of the chain) | 1 | 754 passed, 20 failed (same cause), 7 skipped, 2 xfailed, 200.28 s |
| `pytest tests/test_phase22_register_and_docs_consistency.py` | 0 | 15 passed |
| `python -m ruff check dz_erp tests tools` | 0 | All checks passed |
| `python -m compileall -q dz_erp tests tools` | 0 | clean |
| `python tools/verify_dependency_graph.py` | 0 | 54 distributions; OpenCV substitution is the only documented deviation |
| `python tools/generate_hash_lock.py --check` | 0 | 51 pins, 1 277 hashes, no drift |
| `cv2` + `RapidOCR` import assertion | 0 | `cv2 4.11.0`, RapidOCR OK |
| `python tools/staging_acceptance.py` | 1 | **38/40**; the 2 failures are the restore gate below |
| `git diff --check` | 0 | no whitespace errors |

**The 20 failures are one environment cause, not 20 defects.** Every one raises
the same refusal from `dz_erp/security_audit.py::restore_and_verify_backup`:

> `Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13)`

This host's interpreter is 3.11.2, so the safe-`tarfile` preflight declines to
run — the control working as designed, on an interpreter the project does not
support. Affected: `test_phase11_audit_key_rotation.py` (3),
`test_phase12_key_migration_and_backup.py` (10),
`test_phase5_security_audit_backup.py` (1),
`test_phase6_ocr_benchmark_e2e.py` (1),
`test_phase8_upload_boundary_and_ci.py` (2),
`test_phase9_process_boundary.py` (3). The same applies to the 2 staging checks
(`restore: isolated restore verifies hashes, trial balance and audit chain`,
`restart: a new OS process reads the restored data`, the latter failing because
the restore it reads never happened).

The first Phase 32 commit was also run through CI on its exact SHA
(`2083b37e`, run 41075387888): the Windows job and the 3.12 job were green, and
the 3.11 job failed **only** inside this audit's own new test file — `test_q`
asserted a trial-balance key (`total_debit`) that the read model does not
return, a mistake the local skip had hidden. The test was corrected to exercise
the real backup → restore → reopen path (`erp_snapshot.sqlite3`,
`restored["restored_db"]`) and re-published; the CI run for the corrected commit
is recorded in §2/§13. This is the reason the environment-gated test is written
against documented return values rather than asserts that only pass locally.

Consequently coverage on this host is 77 % rather than the 85 % reported for the
PR chain in CI: the restore-heavy modules are not exercised. This is an
**environment limitation, reported as such**, and the corresponding evidence
comes from the verified CI run on the exact SHA (§2), where the full suite, the
staging acceptance (40/40) and the Windows backup/recovery job all pass.

## 11. External production gates (unchanged, still blocked)

Reconciled against `docs/RELEASE_READINESS_MATRIX.md`, `docs/RISK_REGISTER.md`,
`docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`, `docs/OCR_EVALUATION_PROTOCOL.md`.
Nothing in this audit closes any of them, and no procedure was upgraded to a
claim of operational completion.

| Gate | Status after this audit | Why it cannot be closed here |
|---|---|---|
| KMS/HSM (or formally approved equivalent) key custody | **Blocked** | a funding/infrastructure decision; `describe()` still reports `hsm_equivalent: False` |
| Publisher provenance verification for dependencies | **Blocked** | needs a mirror/provenance attestation outside this sandbox; hash-lock only proves artifact integrity |
| Independent off-host backup + scheduler evidence | **Blocked** | needs real infrastructure; local backup/restore also needs a supported interpreter |
| Real alert delivery | **Blocked** | no production endpoint or channel in scope |
| Production-scale disaster recovery | **Blocked** | requires production-scale data and a drill |
| Production-scale concurrency validation | **Blocked** | SQLite `BEGIN IMMEDIATE` serialisation is verified functionally (`test_j`/`test_j2`), not at production volume |
| Authorised Arabic/French OCR evaluation corpus | **Blocked** | needs an authorised corpus; `ARABIC_RECOGNITION_HEAD_AVAILABLE = False` |
| Qualified Algerian SCF + tax professional sign-off | **Blocked** | includes the timbre brackets/threshold/floor, admissible TVA rates, G50 tags and the treatment of `IN_REFUND` credit notes in the trial balance — **this audit changed none of them** |
| Law 18-07 / ANPDP compliance evidence | **Blocked** | needs legal evidence (retention, registry, DPIA), not code |

## 12. Recommendation

**READY FOR HUMAN REVIEW** — of the PR chain #3 → #6 as an integrated workflow,
with the five findings below as review input, and **production deployment still
BLOCKED** by the external gates in §11.

Basis:

* The verified baseline is exact (`e4e47b3b`), the chain is linear, all four PRs
  are mergeable/clean and CI is green on the exact published SHA.
* No Critical defect: no financial corruption reachable through the API, no
  cross-tenant access, no audit-chain bypass.
* No High defect: every refused workflow left the ledger, the invoice state and
  the audit chain intact (`test_o`, `test_l`, `test_n`, `test_p`).
* One Medium defect (P32-F1) was reproducible and is fixed with a regression
  test; two further Medium findings (P32-F2, P32-F3) and two Low/Medium ones
  (P32-F4, P32-F5) are documented with reproductions and minimal remediations.
* Evidence gaps are named, not glossed: the backup/restore and staging checks
  could not run on this host because of the interpreter floor, and rest on the
  CI run for the same commit.

A green CI pipeline does not establish production readiness: the arithmetic of
the ledger, the fail-closed review gate and the tenant boundaries are verified;
the custody, provenance, scale and legal gates are not, and remain open.

## 13. Published commit and pull request

| Item | Value |
|---|---|
| Phase 32 code + tests | `5b3020a501a6e01970b883ee5372205402d75898` (`arena/7f12d627-erp`) — `dz_erp/decimal_utils.py`, the audit matrix, the register rows, the Phase 22 probe |
| Phase 32 report | `9092e42a831e1312b87ed3258ed772fee5528a7c` (docs-only: this file and section 13) |
| Parent | `e4e47b3bf4376e804f17b23e63c86f7fe384056c` (Phase 31 head, unchanged) |
| Pull request | **#7** `audit(phase32): independent integration audit of the Phases 29-31 chain` — **Draft** |
| Base | `fix/phase29-invoice-discount-and-line-net-consistency` (PR #6 head), so the chain #3 → #4 → #5 → #6 → #7 stays additive and no existing PR is rewritten |
| State | `MERGEABLE` / `CLEAN`, Draft (must stay Draft pending human review) |
| CI on `5b3020a5` | run **41076741380** — 3/3 jobs pass: 3.11 full suite + staging acceptance + risk-register validation (17 steps), 3.12 full suite (15 steps, 2 by design skipped), Windows rotation/backup + staging (14 steps) |
| CI on `9092e42a` | run **41078086085** — 3/3 jobs pass (same step sets) |

Each commit on this branch was verified by CI for its own exact SHA; the two
runs above are the ones that carry the audit's code/tests and this report. A
later documentation-only commit cannot invalidate the code verification, because
it changes no file under `dz_erp/` or `tests/`.

Nothing was merged, closed, rebased or force-pushed; `main` is untouched and no
file was deleted. PRs #3–#6 remain exactly as they were found.


---

## 14. Phase 33 remediation of P32-F2 … P32-F5

Appended after the Phase 32 publication. §1–§13 above are left as published;
this section is the record of the remediation that followed it.

**Baseline.** The Phase 32 head `2a2b9b14de0cf792356488d645f20e5c4d4ad565`
(Phase 32 code, tests and this report) was restored and verified byte-identical
to `origin/arena/7f12d627-erp` before any change. All four findings were
reproduced on that tree first, with the exact evidence recorded in §8 — the
reproductions are the baseline, not the summaries.

**Scope discipline.** Minimal and backward compatible:

* no posted journal entry, journal line or approval decision is rewritten;
* no validation, tax or timbre rule is changed, and no Algerian SCF/VAT/G50
  behaviour is touched — the same helper functions are called as before;
* legacy/manual (document-less) invoices keep working, and pre-existing approval
  rows keep authorising what they legitimately authorised (§14.3);
* exactly **one** additive, nullable schema column was needed (§14.6), because
  F4 cannot bind a request to a review version that was never recorded.

### 14.1 P32-F2 — recoverable `NeedsReview` lifecycle

* **New service:** `dz_erp/procurement_stock.py::revalidate_supplier_invoice`
  (public API `POST /api/v1/invoices/{invoice_id}/revalidate`, roles
  `clerk`/`accountant`/`admin`, company-scoped, 409 for a refused transition,
  422 for a malformed amount).
* **Transition:** `NeedsReview | DuplicateRejected | Validated` →
  `Validated | NeedsReview | DuplicateRejected`. `PendingApproval`, `Approved`,
  `Posted` and `Rejected` are refused with
  `INVOICE_REVALIDATION_STATE_REFUSED`; credit notes are refused with
  `INVOICE_REVALIDATION_NOT_APPLICABLE` (a credit note follows the reversal
  workflow, it is never corrected in place). `Validated` is correctable so that
  re-validation is idempotent and a draft corrected before submission keeps its
  identity.
* **Identity is preserved:** the same `supplier_invoices` row is corrected, so
  the paper keeps one invoice and one audit trail. Duplicate creation is
  explicitly *not* the repair path — a second invoice citing an owned document
  is refused (§14.2).
* **Every prerequisite re-runs** from the corrected payload: Algerian identifier
  validation, tenant-scoped duplicate detection **excluding the invoice itself**,
  per-line arithmetic/TVA/discount bounds/3-way match, header HT/TVA
  reconciliation, the timbre rule, the TTC total, the document link state, and
  the submission-time reviewed-source consistency gate.
* **A rejected validation result never authorises posting:** the payload digest
  is recomputed and `approved_payload_sha256` is cleared, so only the digest that
  was validated *and* approved can be posted (`test_f2_a_previous_validation_result_cannot_authorize_posting`,
  `test_t2_*`).
* **History is preserved:** each attempt appends `INVOICE_REVISED` (before/after
  payload) and `INVOICE_REVALIDATED` (previous state, previous
  `validation_errors`, previous digest, attempt number), so a failed attempt is
  never overwritten.
* **Tests:** `test_f2_*` (12 cases) — correction in place, repeated/failed
  re-validation, four illegal transitions, duplicate-rejected recovery,
  reference theft refused, source gate still enforced, document-less legacy
  invoice, role/tenant scoping, HTTP 200/409.

### 14.2 P32-F3 — a refused attempt never mutates source evidence

* **No document write on refusal:** the tail `UPDATE source_documents` is now
  `_refresh_document_state`, called only when the attempt actually becomes the
  document's owner (`Validated`/`NeedsReview`), never from a
  `DuplicateRejected` attempt.
* **One paper, one invoice:** `_assert_document_linkable` +
  `_live_document_owner` refuse a second *live* invoice on a document already
  owned by one (`DOCUMENT_ALREADY_INVOICED`, HTTP 409) before any insert, so the
  original invoice↔document relationship cannot be contested.
* **Independent refusal record:** the duplicate is still stored (it is evidence
  of the attempt) and audited twice — `DUPLICATE_INVOICE_ATTEMPT_RECORDED` on the
  invoice and `DUPLICATE_DOCUMENT_INVOICE_ATTEMPT_REFUSED` on the document —
  naming company, actor, document, existing invoice id and state, attempted
  reference and amount, and the reason, with
  `document_state_touched: false`. Both are committed **after** the attempt's
  rollback (`_RefusedAttempt` + `_record_refused_attempt`), so a refusal is
  durable even though nothing it tried to write survives.
* **Tenant scoping:** every lookup is `company_id`-scoped; a cross-company
  document is a `TenantIsolationError`.
* **Tests:** `test_f3_*` — evidence untouched, same-document refusal, tenant
  scope; `test_t_*` in the Phase 32 matrix.

### 14.3 P32-F4 — approval invalidation after modification

* **Invalidation is explicit, not cosmetic:** `_invalidate_approval_requests`
  turns every `PENDING`/`APPROVED` request of the invoice into `INVALIDATED`
  with `reason='PAYLOAD_MODIFIED_AFTER_SUBMISSION'`, `decided_by_id` = the acting
  user and `decided_at`, inside the same transaction as the modification.
* **State stays consistent:** the invoice returns to `NeedsReview`, the source
  document follows it (`_refresh_document_state`), and
  `approved_payload_sha256` is cleared; `approved_by_id`/`approved_at` are kept
  as historical attribution only. No orphan `PENDING` row can exist.
* **Requests are bound to the payload:** `submit_invoice_for_approval` stores
  `payload_sha256` and `source_review_fingerprint` on the request, and
  supersedes older pending requests. At decision time a stale request is refused
  (`APPROVAL_REQUEST_STALE_REFUSED`): it is marked `INVALIDATED` and the invoice
  returns to the recoverable `NeedsReview` rather than becoming un-editable.
* **Backward compatibility:** a request with a `NULL` fingerprint (created before
  this column existed) is tolerated and still authorises the payload it was
  raised for (`test_f4_a_legacy_request_without_a_fingerprint_still_works`).
  Rejection is never gated: a stale invoice can always be rejected.
* **Tests:** `test_f4_*` (8 cases) — quantity/price/discount parameters, exact
  re-derived line net and header totals, document/invoice consistency,
  re-submit + fresh approval, stale payload, stale evidence, legacy `NULL`
  fingerprint, rejection not gated.

### 14.4 P32-F5 — stable reversal conflict semantics

* **Detect before inserting:** `create_reversal_request` checks for an existing
  live `IN_REFUND` (`NeedsReview`/`PendingApproval`/`Approved`/`Posted`) for the
  invoice and refuses with `FinancialIntegrityError: INVOICE_ALREADY_REVERSED`
  (HTTP 409). The idempotent replay of the *same* idempotency key is still
  answered from the idempotency record first, unchanged.
* **The refusal is attributed evidence:** `REVERSAL_ATTEMPT_REFUSED_DUPLICATE`
  is written in its own transaction after the rollback, naming the existing
  credit note, the attempted key, actor and reason.
* **Never a 500:** the residual race — a unique-constraint collision the
  pre-check cannot see — is caught and mapped to the same domain refusal with
  `refusal_reason='REVERSAL_ENTRY_UNIQUE_CONFLICT'`.
* **Relations preserved:** the original posted journal entry and the first credit
  note keep their existing source-model/source-id relationship; no second
  reversal, no second credit.
* **Tests:** `test_f5_*` (5 cases) — first/second reversal, a two-thread
  `threading.Barrier` race producing exactly one credit note, cross-company
  refusal, entry-number collision mapped to the domain error, HTTP 409; plus
  `test_w_*` in the Phase 32 matrix.

### 14.5 State machines after Phase 33

| From | Trigger | To | Refusal |
|---|---|---|---|
| `NeedsReview` / `DuplicateRejected` / `Validated` | `revalidate_supplier_invoice` (all prerequisites pass) | `Validated` | `INVOICE_REVALIDATION_STATE_REFUSED` for `PendingApproval`/`Approved`/`Posted`/`Rejected`; `INVOICE_REVALIDATION_NOT_APPLICABLE` for `IN_REFUND` |
| `NeedsReview` / `DuplicateRejected` / `Validated` | `revalidate_supplier_invoice` (errors remain) | `NeedsReview` | — |
| `NeedsReview` / `DuplicateRejected` / `Validated` | `revalidate_supplier_invoice` (correction collides with another live invoice) | `DuplicateRejected` | — |
| `Validated` | `submit_invoice_for_approval` | `PendingApproval` (older pending requests superseded) | must be `Validated` |
| `PendingApproval` | `decide_invoice_approval(approve=True)` | `Approved` | stale request → `INVALIDATED`, invoice back to `NeedsReview` |
| `PendingApproval` | `decide_invoice_approval(approve=False)` | `Rejected` (terminal) | — |
| `PendingApproval` / `Approved` / `NeedsReview` | `modify_draft_invoice_line` | `NeedsReview`, approved digest cleared, requests `INVALIDATED` | `Posted` cannot be edited |
| `Approved` | `post_supplier_invoice` | `Posted` | approved digest must equal the payload digest |
| `Posted` | `create_reversal_request` | credit note created | `INVOICE_ALREADY_REVERSED` if one already exists |

The linked source document mirrors the invoice (`Validated`/`NeedsReview`/
`PendingApproval`/`Approved`/`Posted`) and is never written by a refused attempt.

### 14.6 Schema change

One additive, nullable column, applied idempotently by
`dz_erp/database.py::_migrate_approval_binding` (called from the existing
migration chain, guarded by a `PRAGMA table_info` check):

```
approval_requests.source_review_fingerprint TEXT NULL
```

No row is rewritten, no default is invented, and a database created before
Phase 33 migrates in place (`test_migration_is_idempotent_and_repeated_runs_are_safe`,
including a `DROP COLUMN` round trip that simulates the older schema).

### 14.7 Verification evidence (this host)

Environment: Linux, Python **3.11.2**, pytest 8.3.5, coverage 6.0.0, ruff 0.14.0,
`requirements.lock.hashes.txt` installed with `--no-deps --require-hashes`.
`2a2b9b1` baseline reproduced all four findings before any edit.

| Command | Exit | Result |
|---|---|---|
| `pytest tests/test_phase33_invoice_lifecycle.py -q` | 0 | **33 passed**, 4.96 s |
| `pytest tests/test_phase32_cross_phase_integration_audit.py tests/test_phase29_ocr_review_policy.py -q` | 0 | **92 passed, 1 skipped**, 12.02 s |
| `pytest tests/test_phase8_upload_boundary_and_ci.py -q` | 1 | 204 passed, **2 failed** — both the interpreter floor (`Safe backup restore requires Python 3.11.17+`), environment, not application |
| `pytest -q --cov=dz_erp --cov-report=term-missing` | 1 | **796 passed, 20 failed, 7 skipped**, 162.05 s; coverage **77 %** (7,116 statements, 1,623 missed) — every failure is the same interpreter floor (`security_audit.py` restore gate), no application failure |
| `pytest tests/test_phase22_register_and_docs_consistency.py -q` | 0 | 15 passed |
| `ruff check dz_erp tests tools` | 0 | All checks passed |
| `python -m compileall -q dz_erp tests tools` | 0 | clean |
| `python tools/verify_dependency_graph.py` | 0 | environment matches the declared graph |
| `python tools/generate_hash_lock.py --check` | 0 | 51 pinned distributions, 1,277 hashes, no drift |
| `cv2` / `RapidOCR` import assertion | 0 | `cv2 4.11.0`, `RapidOCR import OK` |
| `python tools/staging_acceptance.py` | 1 | **38/40** — the two failures are the restore check and its dependent restart check, both the same interpreter floor |
| `python tools/risk_register_counts.py` | 0 | 36 rows, summary consistent |
| `git diff --check` | 0 | no whitespace errors |
| secret scan over the full diff and the new test module | 1 (no matches) | no key material, token or password introduced |

The interpreter floor (`Python 3.11.17+` / `3.12.10+`, below 3.13) is a real
environment limit of this host, not an application defect; those restore paths
are exercised by CI on the exact published SHA (§14.9). They are **not**
reported here as passes.

### 14.8 Not changed, not claimed

* No Algerian accounting, SCF, TVA, timbre or G50 rule was modified; the
  professional review gate (§11) remains **Blocked**.
* No external production gate in §11 changed status: KMS/HSM custody, publisher
  provenance, off-host checkpoints, alert delivery, production-scale DR and
  concurrency, the authorised OCR corpus and Law 18-07 evidence all remain
  **Blocked** pending their external owners.
* Production-scale concurrency is still not validated; the Phase 33 race test
  proves *exactly-one* credit note under a two-thread barrier on an isolated
  SQLite database, which is a functional result, not a volume result.
