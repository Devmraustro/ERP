# Project baseline and completion plan

**Repository:** `Devmraustro/ERP` · **Branch:** `arena/7f12d627-erp` ·
**Baseline commit:** `852f389f30176bdfedaa33fe9585784b78d76ebe`

This document reconstructs the *actual* state of the repository at the start of
this work, records what is verified versus assumed, and defines the safe
execution order for the remaining work. Every claim here is either a command
result on this host or a GitHub API fact; nothing is taken from an earlier
report without re-verification.

---

## 1. How this baseline was established

| Step | Command | Result |
|---|---|---|
| Fetch remote state | `git fetch origin` | `origin/arena/7f12d627-erp` = `852f389f`; `origin/main` = `4fc1364` |
| Working tree | `git status --porcelain` | 20 modified + 4 untracked paths — a re-cloned sandbox carrying the published work uncommitted |
| Byte-equality of that work | `git write-tree` vs `git rev-parse origin/…^{tree}` | `def29e72…` **identical** — no local edit was lost or invented |
| Restore the branch pointer | `git merge --ff-only origin/arena/7f12d627-erp` | HEAD `852f389f`, clean tree (no reset, no history rewrite) |
| Toolchain | `python -m venv`; `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | Python **3.11.2**, pytest 8.3.5, coverage 6.0.0, ruff 0.14.0, `cv2` 4.11.0, RapidOCR import OK |
| Full suite | `pytest -q --cov=dz_erp` | **796 passed, 20 failed, 7 skipped**, 187 s, **77 %** coverage |

The 20 failures were then, and remain, **one environment cause**: `restore_and_verify_backup`
refuses to run below Python 3.11.17 (safe tar extraction). That gate is a security
control, not a bug; it is not bypassed locally. Those paths run in CI on the exact
published SHA (run **41093527978**, 3/3 jobs green). The failure set was captured
before and after this session's changes and compared with `diff`: **identical**.

The historical report of "812 passed / 0 failed / 20 skipped / 86 %" corresponds to
**CI** (Python 3.11.17 + 3.12.15, where the restore paths execute and the 20 skips
are platform-specific) rather than to this host. Both numbers are honest; they
describe different environments, and §5 records which is which.

## 2. PR dependency graph (verified with `gh`)

| PR | Base | Head SHA | Draft | State |
|---|---|---|---|---|
| #3 Phase 29 fail-closed review | `main` (`4fc1364`) | `29645e5d` | no | OPEN |
| #4 Source→invoice values | `phase29/ocr-failclosed` | `99864bab` | yes | OPEN |
| #5 Line unit price | `fix/phase29-source-invoice-consistency` | `c466cea7` | yes | OPEN |
| #6 Line discount + derived net | `fix/phase29-invoice-line-price-consistency` | `e4e47b3b` | yes | OPEN |
| #7 Phase 32 audit + Phase 33 remediation | `fix/phase29-invoice-discount-and-line-net-consistency` | `852f389f` | yes | OPEN |

Linear and additive: each base is the previous head, so the only order that
preserves ancestry is #3 → #4 → #5 → #6 → #7. Nothing in this work merges, closes,
rebases or un-drafts any of them; `main` is untouched.

> The session branch is fixed (`arena/7f12d627-erp`) and PR #7 already targets the
> Phase 32 head, so continuation work is appended to PR #7. GitHub rejects a second
> PR for the same head/base pair.

## 3. Implemented capability (verified by tests, not by documentation)

| Area | Where | Coverage |
|---|---|---|
| Authentication, RBAC, tenant isolation | `dz_erp/auth.py`, `dz_erp/api.py` | Phases 5–8, 28 suites |
| OCR intake, provenance, review policy | `dz_erp/intake_ocr.py`, `dz_erp/review_policy.py` | Phase 29 (48) + Phase 34 (19) |
| Source→invoice consistency, discounts, line net | `dz_erp/procurement_stock.py` | Phases 30–31 (Phase 29 suite) |
| Invoice lifecycle, approvals, posting, reversal | `dz_erp/procurement_stock.py` | Phases 32–33 (77) |
| SCF chart, TVA/timbre, G50 workbook | `dz_erp/localization.py` | Phase 27 + staging acceptance |
| Audit chain, key rotation, backups, checkpoints | `dz_erp/security_audit.py`, `key_*.py`, `rotate_keys.py` | Phases 2, 11–14, 20 |
| Desktop client (PySide6) | `dz_erp/desktop/` | Phases 26–28 (skipped where Qt is absent) |
| Staging acceptance | `tools/staging_acceptance.py` | 40 checks (38/40 locally — interpreter floor) |

## 4. Open defects at this baseline

| ID | Severity | Status at baseline | Status now |
|---|---|---|---|
| P32-F1 non-finite amounts | Medium | Fixed in Phase 32 | Fixed |
| P32-F2 `NeedsReview` dead-end | Medium | Fixed in Phase 33 | Fixed |
| P32-F3 evidence drift on duplicate attempt | Medium | Fixed in Phase 33 | Fixed |
| P32-F4 approval not invalidated on edit | Low/Med | Fixed in Phase 33 | Fixed |
| P32-F5 reversal 500 | Low | Fixed in Phase 33 | Fixed |
| **P34-F1 duplicate extraction rows fail open** | **Medium** | **Open at baseline (not previously reported)** | **Fixed in Phase 34** |

### P34-F1 — duplicate extraction rows authorized an unreviewed value (Medium)

**Reproduced on `852f389f` before any change:** with a fully reviewed document, an
extra `amount_ht_dzd` row carrying `5555.00` was inserted. The policy grouped rows
by field name, evaluated `rows[0]`, appended `conflicting_extraction` to the flags
and **never consulted that flag when computing satisfaction**. Result: the field
reported `satisfied: true`, the invoice validated, and
`submit_invoice_for_approval` returned `PendingApproval` — a value nobody reviewed
was on a path to the ledger.

Three distinct weaknesses were confirmed and are now closed:

1. `FLAG_CONFLICT` (and therefore `FLAG_DUPLICATE_ROWS`) did not affect
   satisfaction, so extra rows were decorative.
2. A review decision was keyed only by `(field_name, line_index)`, so it
   authorised *any* row that later appeared under that key.
3. Line-level duplicates were blocked only by accident (as a stale-value
   mismatch), never diagnosed, and never re-checked after approval.

See `tests/test_phase34_ocr_duplicate_row_integrity.py` (19 tests) and
`docs/QA_TEST_MATRIX_AND_RESULTS.md` §2I.

## 5. Test baseline

| Environment | Result | Notes |
|---|---|---|
| **This host** (Linux, CPython 3.11.2) | **815 passed, 20 failed, 7 skipped**, 214 s, 77 % | The 20 failures are all `restore_and_verify_backup`'s interpreter floor |
| **CI** (run 41093527978, head `852f389f`) | 3/3 jobs success | Python 3.11.17 + 3.12.15 full suite with staging and risk-register validation; Windows 3.12.10 rotation/backup suite |

Neither number is "the" result: they answer different questions on different
interpreters. The honest statement is *815 tests pass on this host, and the only
failures here are paths this interpreter is not permitted to run, which are green
in CI on the same commit.*

## 6. External blockers (unchanged by any code work)

KMS/HSM key custody · publisher provenance attestation · off-host checkpoints with
scheduler evidence · real alert delivery · production-scale DR and concurrency ·
authorised OCR corpus · **qualified Algerian SCF/tax professional sign-off** ·
Law 18-07 / ANPDP evidence. See `docs/RELEASE_READINESS_MATRIX.md` — every row
remains **Blocked (external evidence required)**, and no code change in this
repository can close one.

## 7. Safe execution order for the remaining work

1. **Duplicate-row integrity** (P34-F1) — fail-closed before anything else touches
   financial state. *Done in Phase 34.*
2. **Re-verify Phases 29–33** against the new policy (the review policy is a shared
   dependency of every later gate). *Done: 815 tests, identical failure set.*
3. **Schema/migration safety** — an additive nullable column only; verify idempotent
   migration and legacy-row behaviour. *Done in Phase 34 (`evidence_json`).*
4. **Accounting-domain validation** — arithmetic, rounding, period behaviour, and a
   written statement of what the tax layer *does not* claim without professional
   review.
5. **API and integration hardening** — the error taxonomy, idempotency and
   OpenAPI pin are already enforced by tests; rate limiting and request logging
   remain.
6. **Production infrastructure preparation** — container build, compose, health
   checks, environment template, backup/restore and DR runbooks. Prepared, never
   deployed.
7. **Documentation and handover** — every document must describe what the tests
   actually prove.

Each step commits only verified changes, on this branch only, with the evidence
recorded in `docs/PROJECT_COMPLETION_TRACKER.md`.
