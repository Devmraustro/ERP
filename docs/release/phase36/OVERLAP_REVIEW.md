# Phase 36C — Overlap review: PR #7 against the working tree

Scope: the 11 files changed by both PR #7 (`f603e38`) and the uncommitted working tree. Phase 34
recorded five; the measured intersection is eleven. Every verdict below comes from executing the
code, not from reading a diff.

## Method

1. `git diff --name-only $(git merge-base main f603e38) f603e38` → 185 files PR #7 touches.
2. `git status --porcelain` → 16 files the working tree modifies.
3. Intersection → 11 files.
4. For each: compare line counts across `main` / working tree / PR #7, diff the content, then
   **execute** the behaviour in question against a disposable database.

## Verdicts

| # | File | Overlap type | Conflict? | Action |
| --- | --- | --- | --- | --- |
| 1 | `dz_erp/security_audit.py` | PR #7 adds rate-limit/request-id audit | **No** | `require_role_secure` identical; runtime authz proven |
| 2 | `dz_erp/decimal_utils.py` | helper renamed `_guarded`→`_require_supported_decimal` | **No** | behaviour identical; keep workspace name |
| 3 | `dz_erp/api.py` | PR #7 adds SEC-13/14/15 endpoints | additive | Phase 37 |
| 4 | `dz_erp/intake_ocr.py` | PR #7 adds review-decision wiring | additive | Phase 37 (SEC-14) |
| 5 | `dz_erp/procurement_stock.py` | 22 functions | **YES — partial** | Phase 36B / Phase 38 |
| 6 | `pyproject.toml` | pins | no | verify `requires-python` unchanged |
| 7 | `requirements.lock.txt` | pins | no | verify hashes |
| 8 | `requirements.lock.hashes.txt` | pins | no | verify hashes |
| 9 | `tests/test_phase19_dependency_integrity.py` | expectations | no | suite green |
| 10 | `tests/test_phase7_authentication_remediation.py` | expectations | no | suite green |
| 11 | `tests/test_phase8_upload_boundary_and_ci.py` | expectations | no | suite green |

## The one real conflict: `procurement_stock.py`

The workspace has its own working invoice lifecycle (17 module-level functions including
`create_and_validate_supplier_invoice`, `submit_invoice_for_approval`, `decide_invoice_approval`,
`post_supplier_invoice`, `reverse_posted_invoice`, `create_reversal_request`). PR #7 replaces this
with a 3,583-line module carrying 22 additional functions.

A wholesale swap is both forbidden by the mission and technically wrong: four of those 22 functions
(groups C and D in `PROCUREMENT_FUNCTION_INVENTORY.md`) depend on a statutory timbre-provenance
subsystem (`resolve_timbre_rule`, `inherit_timbre_provenance`, `persist_invoice_timbre_provenance`,
`assert_timbre_rule_unchanged`, seven functions absent from the workspace's 677-line
`localization.py`) that decides *which statutory stamp-duty rule an invoice is stamped under*. That
is a legal/accounting determination pending expert sign-off, so it cannot be integrated by code
review alone.

Resolution adopted: **function-level selective port**, in dependency order, never a module swap.

### Ported this phase (Group B)

`_RefusedAttempt`, `_validate_invoice_lines`, `_validate_invoice_totals`,
`_duplicate_invoice_errors`, `_line_price_variance`, `_invalidate_approval_requests`,
`_record_refused_attempt`.

Appended under a clearly marked block so a reviewer can see exactly what came from PR #7. They are
**not yet called from the live write path** — wiring changes behaviour for existing callers and is a
separate tested step (Phase 38).

### Rejected this phase

`_source_review_fingerprint` — queries `ocr_field_reviews(company_id, document_id, field_name,
line_index, decision, reviewed_value, reviewed_by_id, reviewed_at)`, a table the workspace schema
does not define. Porting it requires a schema migration **plus** the review-decision subsystem
(SEC-14). Rejecting it rather than adding a table to the production schema on my own authority is the
correct call: a new OCR-decision table changes what evidence an approval is bound to.

### Deferred, blocked

Group C (4 functions, review-policy) → Phase 37 SEC-14.
Group D (4 functions, timbre provenance) → **REQUIRES EXPERT REVIEW**, 12 questions PENDING.

## Signature discrepancies found during porting

These were caught by the tests, not by inspection, which is why the tests were written first:

| Function | Phase 34 / assumption | Actual PR #7 signature |
| --- | --- | --- |
| `_duplicate_invoice_errors` | `(conn, cid, sid, year, ref, ttc)` → `list` | keyword-only → **`Tuple[List[str], bool]`** |
| `_invalidate_approval_requests` | positional 7 args, `target_model="supplier_invoice"` | keyword-only (`reason=`, `actor_user_id=`, `now_iso=`), default **`"supplier_invoices"`** (plural) |
| `_source_review_fingerprint` | pure hash over `ocr_extracted_fields` | queries `ocr_field_reviews`, returns `Optional[str]` |

Two of those would have silently corrupted data if wired in on the assumed signature: the triple
returned by `_duplicate_invoice_errors` is truthy whenever the *list* is non-empty, so
`if _duplicate_invoice_errors(...)` would reject every invoice if misused as a boolean, and the
singular/plural `target_model` differs from every other call site in the module.

## Schema drift between the two trees

Found while writing the behavioural tests — the workspace schema differs from PR #7 in ways that
matter for integration:

| Object | Workspace | PR #7 |
| --- | --- | --- |
| `approval_requests.status` | column is **`decision`** | `decision` |
| `approval_requests.target_id` | `INTEGER` | `INTEGER` |
| `ocr_field_reviews` | **absent** | present (Phase 29) |
| `ocr_extracted_fields` review columns | **absent** | via `ocr_field_reviews` |
| `source_documents.file_sha256` | `file_sha256` | `file_sha256` |

Anything ported that touches approval state must use `decision`, not `status`. The integrated
`_invalidate_approval_requests` already does. This is recorded because a reviewer reading PR #7's
tests would assume `status`.
