# Phase 34 — Accounting and functional comparison

## 0. The finding that reframes this whole assessment

Earlier phases compared these two branches as "a security fork vs a desktop fork". That framing is
**wrong**. Measuring `dz_erp/procurement_stock.py`, the module that carries the invoice lifecycle and
posting invariants:

| | Lines |
| --- | --- |
| Workspace (`arena/7557bcfb-erp`) | **1,799** |
| PR #7 (`2427fda`) | **3,583** |
| Shared ancestor (`5a3e47dd`) | 3,583 |

PR #7's version is the ancestor's, plus 117 lines of SEC work on top. The **workspace has
approximately half the accounting module**, and it is *not* a fork of the same code — it is an
earlier, smaller generation.

Functions present in PR #7's `procurement_stock.py` and **entirely absent** from the workspace:

```
_post_supplier_invoice_in_transaction   _create_and_validate_supplier_invoice
_validate_invoice_lines                 _validate_invoice_totals
_duplicate_invoice_errors               _source_invoice_mismatches
_line_price_variance                    _source_review_fingerprint
_assert_document_linkable               _document_review_gate
_invalidate_approval_requests           _refresh_document_state
_refuse_posting_and_recover             _record_refused_attempt
_create_reversal_request                _require_supplier_invoice_no
_require_valid_payment_method           _parse_invoice_date
revalidate_supplier_invoice
```

These are not cosmetic. `_validate_invoice_totals`, `_validate_invoice_lines`,
`_duplicate_invoice_errors` and `_source_invoice_mismatches` are the *integrity gates* on what can be
posted. `revalidate_supplier_invoice` is the re-check path. `_create_reversal_request` is
reversal/credit-note handling.

**Therefore: "are there accounting regressions?" is the wrong question.** The workspace is not a
regressed version of PR #7's accounting — it is a *strict predecessor*. Nothing was broken; a large
amount was never received. Any release built on this workspace ships a materially thinner accounting
engine than PR #7 describes.

---

## 1. SCF rules (plan comptable)

Both branches carry the same SCF chart bootstrap (`294` accounts, verified in my Phase 30 evidence:
`40x` suppliers, `4456` TVA récupérable, `6xx` charges). No divergence found: the chart module is
byte-identical between `HEAD` and `2427fda` where it exists in both.

**Verdict: NO DIVERGENCE.**

---

## 2. Journal entries and posting invariants

| Invariant | Workspace | PR #7 |
| --- | --- | --- |
| Balance enforced (Σ debit = Σ credit) | yes | yes |
| Integer/Decimal money, no floats | yes (`decimal_utils`) | yes (`decimal_utils`) |
| Non-finite / oversized refusal | **yes** (`_guarded`, MAX_ABSOLUTE_EXPONENT=18) | **yes** (`_require_supported_decimal`, same constant) |
| Duplicate-invoice detection | **partial** | `_duplicate_invoice_errors` — full |
| Source-vs-invoice line reconciliation | **absent** | `_source_invoice_mismatches`, `_line_price_variance` |
| Total validation | **partial** | `_validate_invoice_totals` |
| Reversal / credit note | **absent** | `_create_reversal_request` |
| Re-validation after edit | **absent** | `revalidate_supplier_invoice` |
| Posting state machine | yes (`Validated → PendingApproval → Posted`) | yes, plus recovery (`_refuse_posting_and_recover`) |

Note the decimal fix is **the same fix implemented twice** — identical `MAX_ABSOLUTE_EXPONENT = 18`,
identical rationale comment ("Algeria's annual GDP ~10**16 DZD"), different helper name
(`_guarded` vs `_require_supported_decimal`). Convergent, not conflicting: either can be kept.

**Verdict: PR #7 is materially stronger on posting integrity. Not a regression in the workspace —
an absence.**

---

## 3. Trial balance

Both compute debit/credit totals from the same journal table with the same SCF account grouping. My
Phase 30 drill verified the workspace trial balance balances exactly and equals the source
(148,750.00 DZD) across a restore. PR #7 adds no trial-balance change. The PR's phase-39/47/51 tests
(`reversal_period_consistency`, `price_variance_clearing`, `timbre_rule_provenance`) exercise paths
this workspace does not have.

**Verdict: NO DIVERGENCE IN WHAT EXISTS; PR #7 COVERS MORE.**

---

## 4. Inventory valuation

`procurement_stock.py` is the shared module and the workspace's copy is the smaller generation. The
PR adds `_line_price_variance` and price-variance clearing accounts (phase 47 tests). The workspace
has basic weighted-average receipt handling but no variance clearing.

**Verdict: PR #7 stronger; no regression.**

---

## 5. Invoice lifecycle

Workspace states: `NeedsReview` → `Validated` → `PendingApproval` → `Posted`, with a `Reversed`
notion absent. PR #7 has the same states **plus** explicit reversal, revalidation, refusal-recording
and a document-linkability gate.

Both use the same separation-of-duties rule (clerk creates → approver approves → accountant posts →
admin can override), which my Phase 30 API evidence exercised successfully on the workspace.

**Verdict: compatible interfaces, PR #7 superset.**

---

## 6. OCR extraction and validation

| Capability | Workspace | PR #7 |
| --- | --- | --- |
| pdfplumber / text extraction | yes, provenance now read from installed metadata (workspace fix) | yes, hardcoded version string |
| Field extraction + confidence | yes | yes |
| Field-level review policy (`review_policy.py`) | **absent** | **present** |
| Corrected-value validation (SEC-14) | **absent** | **present** |
| Line discount / price / net reconciliation | **absent** | present (phase 29 branches) |
| Duplicate-row integrity | **absent** | present (phase 34 tests) |

Interesting inversion: on **provenance** the *workspace* is better. My earlier fix replaced a
hardcoded `"pdfplumber-0.11.6+pdfminer"` string with a live read of `importlib.metadata`, so the
audit trail cannot lie after a parser upgrade. PR #7 still has the hardcoded string. That is a small
but genuine workspace advantage worth preserving.

**Verdict: PR #7 stronger overall; one workspace improvement to carry across.**

---

## 7. Fraud detection

Neither branch has a dedicated fraud module. PR #7's duplicate detection, source-mismatch detection
and price-variance checks are the closest thing, and it has substantially more of them.

**Verdict: PR #7 stronger.**

---

## 8. Tax calculations

Workspace: VAT (`TVA`) lines, timbre handling at the invoice level. PR #7: same, plus
`test_phase40_timbre_rule_provenance` and `test_phase36_accounting_and_tax_configuration`, and a
timbre-rule administration surface (phase 46).

**Critical caveat carried from Phase 32/33:** neither branch has an authorised Algerian
**expert-comptable** sign-off on any statutory rule. The Phase 32 register records 12 questions
pending. This assessment does not change that, and no number in either branch should be treated as
tax-authoritative until it does.

**Verdict: PR #7 has more machinery and still no statutory authority.**

---

## 9. Supplier and customer workflows

Shared base. PR #7 adds supplier-invoice-number length/format validation
(`MAX_SUPPLIER_INVOICE_NO_LENGTH=128`), payment-method validation against a CHECK constraint, and
robust invoice-date parsing (`_parse_invoice_date`, replacing `int(invoice_date[:4])` which never
looked past the year). The workspace lacks all three — meaning the workspace will accept
`supplier_invoice_no` of any length and an `invoice_date` like `2026-99-99`, storing whatever the
substring parse yields.

That is a **real data-integrity defect in the workspace**, not a missing nicety.

**Verdict: PR #7 materially stronger. This is a SHOULD INTEGRATE of small, self-contained code.**

---

## 10. Data migration compatibility

Both use the same `ERPDatabase` schema lineage and the same forward-migration mechanism. PR #7 adds
`test_phase38_migration_matrix` and `test_phase55_migration_atomicity_and_recovery`, and DR
generation tracking (`restore_generation.py`).

Migration direction: workspace → PR #7 is **forward and expected** (PR #7 has every migration the
workspace has, being a descendant). PR #7 → workspace is **not supported** — the workspace would meet
tables and columns it does not know. This matters for the integration strategy: the workspace's data
can be carried onto PR #7's schema, not the reverse.

**Verdict: ONE-WAY COMPATIBLE (workspace → PR #7).**

---

## 11. API and desktop integration

**API** — same FastAPI application, same routers. PR #7's `api.py` is larger (+298 lines) and
superset-compatible: the workspace's 163 added API lines (from earlier phases) do not conflict
textually with PR #7's hunks.

**Desktop** — decisive asymmetry:

> **PR #7's 107 commits do not touch `dz_erp/desktop/` at all.**
> `git diff HEAD 2427fda -- dz_erp/desktop/` → *empty*.

Every desktop file in the workspace is either the old `ui.py` (which PR #7 also has) or a redesigned
module (`tokens.py`, `theme.py`, `components.py`, `shell.py`, `formatting.py`, `icons.py`,
`views_*.py`, `workers.py`) that **PR #7 does not have and never had**. The desktop redesign is
entirely this workspace's work and is **untracked in git** (`??` in `git status`).

Consequence: the desktop layer and the PR #7 backend layer are **disjoint**. They do not conflict;
they do not know about each other. Integration is a matter of putting the redesigned desktop on top
of the stronger backend, not of reconciling two competing UIs.

**Verdict: NO CONFLICT — and no alternative. If you want the redesigned desktop, this workspace is
the only source.**

---

## 12. Duplicated and incompatible implementations

| Item | Nature | Action |
| --- | --- | --- |
| `decimal_utils` non-finite guard | duplicated fix, different name | keep either; prefer PR #7's name for readability |
| `require_role_secure` | duplicated fix, identical body | keep either; keep workspace docstring |
| `_pdfplumber_model_version` | **workspace only** improvement | **carry to PR #7** |
| Desktop redesign (6,000 lines) | **workspace only** | **carry to PR #7** |
| `review_policy` / line reconciliation | **PR #7 only** | **carry to workspace** (large) |
| SEC-13 / SEC-14 / SEC-15 | **PR #7 only** | **carry to workspace** |
| DR generation / remote storage / alerting | **PR #7 only** | expert review needed |
| `procurement_stock` lifecycle | PR #7 superset | replace, do not merge |

No **incompatible interface** was found in either direction: no function signature that exists in
both with divergent meaning, and no API contract that changed shape.

## 13. Direct answers

**Are there accounting or data-integrity regressions?**
**No regression, but a substantial absence.** The workspace is not a damaged version of PR #7's
accounting; it is an earlier, ~50%-smaller generation of `procurement_stock.py` missing total/line
validation, source reconciliation, reversal, revalidation and reversal-period handling. Plus two
concrete workspace-only defects: unvalidated `supplier_invoice_no` length and naive `invoice_date`
parsing, both fixed in PR #7.
