# Technical report

Scope: commits on `arena/7f12d627-erp` from `852f389f` (previous published head) to
the final head of this workstream. Everything below is backed by a command result
recorded in `docs/FINAL_QA_REPORT.md` or by a test file named here.

---

## 1. Changes, by concern

### 1.1 Duplicate OCR extraction rows (Phase 34) — the defect that mattered

**Symptom.** With a fully reviewed document, an additional `ocr_extracted_fields`
row for the same field (`amount_ht_dzd = 5555.00`) produced `conflicting_extraction`
on the field **and** `satisfied: true`; the invoice validated and was submitted for
approval with a value nobody reviewed.

**Root cause — three independent weaknesses:**

1. `evaluate_document_review_policy` grouped rows by field name, evaluated
   `rows[0]`, appended a `FLAG_CONFLICT`, and never consulted that flag when
   computing satisfaction.
2. A review decision was keyed by `(field_name, line_index)`, so it authorised
   whichever row later appeared under that key.
3. Line-level duplicates were blocked only incidentally (as a stale-value
   mismatch), never diagnosed, and never re-checked after approval.

**Fix.**

* `review_policy.py`: duplicate groups are a first-class blocking reason
  (`DUPLICATE_EXTRACTION_ROWS:<field>`), reported with every row id; grouping is by
  page then insertion order (`_row_sort_key`), never collapsed to `rows[0]`; line
  rows are grouped by logical index so a cloned line blocks quantity, price and
  discount together; only `CONFIRMED`/`CORRECTED` may close a group.
* `intake_ocr.py` / `api.py`: every decision records
  `evidence_json = {group_size, row_ids, selected_row_id, selected_source}`
  (additive nullable column, idempotent migration). Satisfaction requires the
  binding to cover exactly the rows present; a later row re-opens the field. An
  explicit `selected_row_id` resolves a conflict and must belong to the group
  (`EXTRACTION_ROW_NOT_IN_GROUP`). An identical replay re-binds instead of
  returning a stale binding.
* `procurement_stock.py`: submission and approval shared the gate already; posting
  now re-runs it inside the posting transaction. A refusal there is recorded
  (`INVOICE_POSTING_BLOCKED_UNREVIEWED`), the approval is invalidated
  (`SOURCE_REVIEW_INVALID_AT_POSTING`), the approved digest is cleared, and the
  invoice and its document return to `NeedsReview` — otherwise an approved invoice
  with newly-duplicated evidence would be permanently unpostable, since an
  `Approved` document refuses field decisions.

**Invariants added:** flagging is not resolution; no evidence is deleted or merged;
no `UNIQUE` constraint was added, because it cannot distinguish a re-extraction
artefact from an operator error and would either fail the migration or drop rows.

**Evidence:** `tests/test_phase34_ocr_duplicate_row_integrity.py` — 19 tests,
including the 14 mandatory scenarios, plus selection scoping, flag laundering,
transcription without deletion, migration idempotency and an intake-path guard.

### 1.2 Authentication throttling and request logging (Phase 35)

`dz_erp/rate_limit.py` adds a sliding-window limiter; `api.py` applies it to
`POST /api/v1/auth/token` **before** password verification, keyed by
`(resolved client address, account)`. Properties held by tests: one account's
lockout cannot deny service to another; a successful login clears the counter; the
window expires; an untrusted peer cannot rotate `X-Forwarded-For` to escape it,
while a declared proxy still gets per-client accounting; refusals are audited;
garbage environment values fall back to the documented default rather than
disabling the control. Every response now carries `X-Request-Id` and an
`HTTP_REQUEST` record (method, path, status, duration) with passwords and tokens
provably absent. The control's honest limit — in-process, per worker — is stated
in the code, the env example and the deployment guide.

### 1.3 Effective-dated tax configuration (Phase 36)

`localization.py` gains `resolve_timbre_rule` and `resolve_vat_rates`, both
resolving for the invoice's own date, with identical semantics: effective window
decides; a configured-but-not-in-force rule raises rather than being applied; a
gap raises; no rows at all means explicitly labelled `builtin-default-v1`
constants; malformed bracket tables (including a closed final band) raise;
resolution is tenant-scoped and ignores inactive rows. `procurement_stock.py`
threads the resolved rule into line validation, header validation and the
post-edit recompute, so a 9 % line on a 2026 invoice is refused when the configured
2026 table has no 9 %. Also fixed: `E2_droit_de_timbre` was the one G50 box with no
traceability entry.

### 1.4 Deployment assets and migration matrix (Phases 37–38)

`ops/Dockerfile` (CPython 3.11.17 — the safe-restore floor, hash-locked install,
uid 10001, exec-form entrypoint, `/healthz` probe), `ops/docker-compose.yml`
(loopback publish, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, one
replica, state and logs on volumes, bootstrap behind a profile),
`ops/erp.env.example`, `.dockerignore`, and six tests that keep those properties —
including a drift gate that fails when the code reads an `ERP_*` variable the
operator documentation does not mention. `tests/test_phase38_migration_matrix.py`
(24 tests) proves convergence, idempotency, preservation, interruption recovery,
`NULL`-not-fabricated defaults, rebuild atomicity and that no migration opens its
own connection.

## 2. Test evidence

| Suite | Result |
|---|---|
| Phase 34 (duplicate rows) | 19 passed |
| Phase 35 (throttle + logging) | 16 passed |
| Phase 36 (accounting and tax) | 29 passed |
| Phase 37 (deployment assets) | 6 passed |
| Phase 38 (migration matrix) | 24 passed |
| Full suite on this host (CPython 3.11.2) | 890 passed, 20 failed, 7 skipped, 78 % |
| Full suite in CI (CPython 3.11.17 / 3.12.15) | previously 3/3 green; final head recorded in `docs/FINAL_QA_REPORT.md` |

All 21 local failures are `restore_and_verify_backup`'s interpreter floor
(< 3.11.17). The set is `diff`-identical to the pre-change baseline apart from the
deliberate Phase 35 addition, and every affected path is green in CI.

## 3. Design decisions worth recording

1. **Fail closed, never guess.** A gap in tax configuration, a stale review
   binding, a duplicate row: all stop the invoice where a human can see it. The
   alternative — substituting a default — produces a plausible wrong number.
2. **Record evidence, never delete it.** Two identical extraction rows are
   integrity evidence; merging or deleting them destroys the ability to investigate.
3. **Bind decisions to what was reviewed.** A decision keyed to a field name
   authorises the future. A decision keyed to a row set authorises a moment.
4. **State the limits of a control in the control's own documentation.** The
   per-process rate limiter, the non-persisted tax-rule label, the untested
   container image and the missing MFA are all written down where an operator will
   read them, because an undocumented limitation is indistinguishable from a bug
   at 3 a.m.
5. **Refuse rather than pretend, on labels.** `ERP_KEY_PROVIDER=kms` makes
   start-up fail unless a provider exists, rather than serving file-backed keys
   under a reassuring name.

## 4. Known limitations (tracked, not hidden)

| Limitation | Consequence | Tracked as |
|---|---|---|
| Resolved tax-rule version **is** persisted (Phase 40: `timbre_rule_version` / `_source` / `_hash` / window + snapshot, with an append-only version ledger) | Corrected in R-06 — this row previously claimed `NOT_STARTED`; a legacy invoice predating Phase 40 still reports `unavailable` and is deliberately not back-filled | `FIXED_AND_VERIFIED` |
| Rate limiting is per process | Multi-replica needs a proxy limit; the shared-SQLite backend is single-host by construction | `IMPLEMENTED_NOT_VERIFIED` (multi-replica) |
| Tax-rule changes require database access | Now an audited, segregation-of-duties administration path (`POST /api/v1/tax/timbre-rules`, `…/approve`) | `FIXED_AND_VERIFIED` (R-05) |
| Container image never built here | Deployment artefacts unproven | `IMPLEMENTED_NOT_VERIFIED` |
| No migration against production-sized data | Timing/locking unknown | `BLOCKED_EXTERNAL` |
| No MFA, no device binding | Token usable until TTL/revocation | accepted risk |
| Tax values not legally qualified | Cannot be presented as compliant | `BLOCKED_EXTERNAL` |
| Law 18-07 determinations absent | Cannot claim compliance | `BLOCKED_EXTERNAL` |

## 5. Reproducing this report

```bash
python -m venv .venv && . .venv/bin/activate
pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
ruff check dz_erp tests tools
pytest -q --cov=dz_erp
pytest tests/test_phase34_ocr_duplicate_row_integrity.py \
       tests/test_phase35_api_rate_limiting_and_request_logging.py \
       tests/test_phase36_accounting_and_tax_configuration.py \
       tests/test_phase37_deployment_assets.py \
       tests/test_phase38_migration_matrix.py -q
python tools/risk_register_counts.py
```

On CPython < 3.11.17 expect the 20 restore-path failures; they are environmental
and are refused deliberately, not skipped.
