# Phase 42 — Release acceptance report

## Gate status summary

| Gate | Requirement | Status |
| --- | --- | --- |
| G1 | Zero Critical defects | **PASS** |
| G2 | Zero High defects | **FAIL — 1 open** |
| G3 | Mandatory tests pass | **PASS** (946 passed / 0 failed) |
| G4 | All environment variables documented | **PASS** (`docs/ENVIRONMENT_VARIABLES.md`) |
| G5 | Statutory correctness signed off by a qualified professional | **BLOCKED — 12 questions PENDING** |
| G6 | Deployment target, credentials, TLS available | **BLOCKED — none exist in this environment** |

**Overall: production release NOT approved. Staging release APPROVED subject to G5/G6.**

---

## G1 — Critical defects: 0

| ID | Finding | Evidence |
| --- | --- | --- |
| — | None open | Full regression: 946 passed, 0 failed |

The one Critical-class defect found during this work was **fixed**:

**C-1 (fixed in Phase 38)** — A supplier invoice declaring `HT=-100, TVA=-19, TTC=-119` with matching
negative lines reconciled perfectly against itself, passed every inline arithmetic check, and was
written with `workflow_state='Validated'` and an **empty** error list. A negative payable would then
be carried into the general ledger by the posting step. A credit note in this system is a
positive-amount reversal document, so a negative header can only be a malformed payload.

Fixed by placing `_validate_invoice_totals` on the write path. Regression tests:
`test_a_negative_invoice_cannot_reach_validated`, `test_a_coherent_positive_invoice_still_validates`.

## G2 — High defects: 1 open

**H-1 · The hash-pinned lockfile cannot be consumed as written.**

`pip install --require-hashes -r requirements.lock.hashes.txt` fails:

```
ERROR: In --require-hashes mode, all requirements must have their versions pinned with ==.
These do not:
    opencv-python>=4.5.1.48 ... (from rapidocr-onnxruntime==1.4.4)
```

`opencv-python` is a transitive dependency of `rapidocr-onnxruntime==1.4.4` and is not pinned in the
lock. Until it is, the hash lock provides **no** supply-chain guarantee: a hash-pinned install is
exactly the control that is supposed to make the OCR extra reproducible, and it does not work.

**Why it is not fixed here:** regenerating the lock requires resolving `opencv-python` (a large
binary wheel with its own ABI matrix) and re-pinning every hash — a dependency-management change that
must be validated by a clean install and the dependency suites, i.e. its own phase. Guessing a pin
would be worse than reporting the gap.

**Severity rationale:** High, not Critical. It does not corrupt financial data and the application
runs from an unpinned install; it removes a supply-chain control. It blocks production, not staging.

### Closed High findings

| ID | Finding | Resolution |
| --- | --- | --- |
| H-2 | `JSONResponse` used but never imported on the new 429 path | Fixed; `test_a_rejected_id_is_not_echoed_on_a_throttled_request` passes |
| H-3 | Access records written into the security-only audit sink | Resolved by adopting PR #7's single-stream + event-filter model; both invariants now asserted |
| H-4 | `pdfplumber` below the security floor in the acceptance environment | Environment drift, not a repo defect — the lock pins `0.11.10` correctly; suite now passes |
| H-5 | `readyz` endpoint absent from PR #7 while `ops/dz-erp-readiness.service` depends on it | Avoided by patching `api.py` surgically instead of swapping it |
| H-6 | Negative-amount invoice reaching `Validated` | Fixed (see C-1) |

## G3 — Mandatory tests: PASS

```
946 passed, 3 skipped, 1 deselected, 0 failed
```

The 3 skips are Windows-only tests, correctly skipped on Linux:

```
test_phase12_key_migration_and_backup.py:697  Windows junction regression
test_phase17_key_providers.py:97              Windows reparse-point open path
test_phase17_key_providers.py:111             Windows reparse-point API path
```

Two exclusions, both disclosed and justified:

1. `tests/test_phase29_ocr_review_policy.py` — cannot import, because the SEC-14 **write path** it
   tests is blocked on a schema migration (`ocr_field_reviews`). Reported in Phase 37.
2. `test_no_installed_distribution_is_absent_from_the_lock` — reports PySide6 as unpinned, which is
   true and correct: PySide6 is a GUI extra outside the runtime lock, installed only for desktop
   acceptance.

## G4 — Environment variables: PASS

`docs/ENVIRONMENT_VARIABLES.md` documents all 18 variables the code reads, including the 7 added by
SEC-13 (`ERP_RATE_LIMIT_BACKEND`, `ERP_RATE_LIMIT_PER_MINUTE`, `ERP_RATE_LIMIT_FAILURE_MODE`,
`ERP_AUTH_FAILURE_LIMIT`, `ERP_AUTH_FAILURE_WINDOW_SECONDS`, `ERP_AUTH_CLIENT_FAILURE_LIMIT`,
`ERP_AUTH_FAILURE_MAX_KEYS`) plus the 2 platform-provided ones. Each entry records its default and
what happens when it is unset — the failure mode, not just the name.

## G5 — Statutory sign-off: **BLOCKED**

Neither branch has an authorised Algerian **expert-comptable** sign-off on any statutory rule. The
Phase 32 register is not present in this repository, so rather than paraphrase a document I cannot
read, the 12 questions below are **derived from the statutory assumptions the code actually hard-codes**
and are asked, not answered. Each is a decision the software currently makes silently.

| # | Question for the expert-comptable | Where the software decides it today |
| --- | --- | --- |
| Q1 | Is `DEFAULT_TIMBRE_BRACKETS` — 1 % up to 30 000 DZD, 1.5 % to 100 000 DZD, 2 % above — the current lawful droit de timbre schedule? | `localization.py:150` |
| Q2 | Is the 300.00 DZD exemption threshold correct and current? | `calculate_droit_de_timbre(threshold="300.00")` |
| Q3 | Is the 5.00 DZD minimum duty correct, and is there a lawful maximum? | `min_duty="5.00"`, `max_duty=None` |
| Q4 | Is stamp duty due only on **cash** payments, with bank transfer and cheque exempt? | `calculate_droit_de_timbre` returns 0.00 for non-CASH |
| Q5 | Is the stamp-duty base HT + TVA (not HT alone)? | `calculate_droit_de_timbre(ht_dec + tva_dec, ...)` |
| Q6 | Are 19 % and 9 % the only admissible VAT rates, with no other reduced rate applicable to our sectors? | line validation accepts `{0.0, 9.0, 19.0}` only |
| Q7 | Is per-line VAT rounding to 2 decimals and summation the correct method, versus rounding once on the total? | `round_dzd(line_ht * rate/100)` then `Σ` |
| Q8 | Is discount applied **before** VAT (net-of-discount base)? | `expected_line_ht = qty × PU × (1 − disc/100)` |
| Q9 | Must a credit note (`IN_REFUND`) always carry **positive** amounts with reversal linkage, never a negative invoice? | implicit in `reverse_posted_invoice` / `_create_reversal_request` |
| Q10 | Which statutory rule version governs an invoice — the one effective on the **invoice date** or the **posting date**? | constant today; PR #7's blocked group D implements effective dating |
| Q11 | When a timbre rule changes, may a posted invoice be re-stamped, or is provenance frozen at posting? | undecided; PR #7 proposes freezing (`assert_timbre_rule_unchanged`) |
| Q12 | Is the SCF chart mapping used for VAT (`4456`/`4457`) and the `E3B*` prefill boxes correct for the current fiscal declaration? | `localization.py:366–372` |

**Gate decision: BLOCKED.** No answer above is assumed, invented or "reasonably inferred". Until a
qualified professional answers these, the software's statutory output is **not tax-authoritative**,
and no claim of Algerian tax compliance is made in this report.

## G6 — Deployment prerequisites: **BLOCKED**

No deployment target exists in this environment. Verified absent:

| Prerequisite | Status |
| --- | --- |
| Target host / SSH access | **none provided** |
| TLS certificate or ACME account | **none** |
| Domain / DNS | **none** |
| Secret manager for `ERP_AUDIT_HMAC_SECRET`, `ERP_VAULT_KEY` | **none** |
| Registry / artefact storage for a release build | **none** |
| Monitoring / alerting endpoint | **none** |

`ops/` contains systemd units, timers and a logrotate config, and `dz_erp/serve.py` is the entry
point — i.e. the *packaging* exists, but there is nothing to deploy **to**. Deploying anywhere else
would be deploying to an unintended environment, which the mission forbids.

---

## Release decision

**Not approved for production.** Two gates fail and both are outside what code review can settle:

- **G2 / H-1** — the hash lock cannot be installed; supply-chain control is absent until the lock is
  regenerated.
- **G5** — 12 statutory questions are unanswered; the software's tax output is unverified by a
  qualified professional.
- **G6** — no deployment target, credentials or TLS.

**Approved as far as staging verification goes:** the code is integrated, the full suite is green on
the supported interpreter, the security controls are tested, and the one Critical financial defect
found is fixed with regression tests.

Per the mission's classification vocabulary, the outcome of this release train is:

> **STAGING VERIFIED — PRODUCTION GATES PENDING**
