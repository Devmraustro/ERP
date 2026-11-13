# Phase 45 — Final handover report

## Classification

> **STAGING VERIFIED — PRODUCTION GATES PENDING**

The integration is complete and verified against a green full suite. Production release is not
approved, and the two reasons are both outside what code review can settle: one open dependency-supply
finding, and twelve unanswered Algerian statutory questions. Nothing was deployed, because no
deployment target exists in this environment.

## What was done

| Phase | Deliverable | Outcome |
| --- | --- | --- |
| 35 | `STEP0_WORKSPACE_PRESERVATION.md` | **PASS** — 282/282 files backed up and hash-verified |
| 36 | `INTEGRATION_MATRIX.md`, `PROCUREMENT_FUNCTION_INVENTORY.md`, `OVERLAP_REVIEW.md` | **PASS** — 11 overlapping files (not 5), function-level integration decided |
| 37 | `SECURITY_INTEGRATION_REPORT.md` | **PASS** — SEC-13 and SEC-15 integrated; SEC-14 evaluator integrated, write path BLOCKED |
| 38 | `ACCOUNTING_PROCUREMENT_INTEGRATION.md` | **PASS** — one Critical financial defect found and fixed |
| 39 | `DESKTOP_API_ACCEPTANCE.md` | **PASS** — 69 desktop + 263 API tests; RTL proven at the Qt layout level |
| 40 | `DEPENDENCY_DATABASE_REPORT.md` | **PASS with one open High** (H-1) |
| 41 | `FULL_REGRESSION_REPORT.md` | **PASS** — 946 passed, 0 failed, 3 skipped |
| 42 | `RELEASE_ACCEPTANCE_REPORT.md` | **BLOCKED** on G2 (one High) and G5 (statutory sign-off) |
| 44 | `DEPLOYMENT_AND_ROLLBACK_REPORT.md` | **BLOCKED** — no target, credentials or TLS |
| 45 | this file | — |

## Commits on `arena/7557bcfb-erp`

```
3106882  Phase 35-41: integrate SEC-13/14/15 and PR #7 integrity gates    (57 files)
f48db93  Phase 35-45: release evidence, environment reference, phase deliverables   (261 files)
4fc1364  Merge pull request #2 ... (base)
```

Staging was selective — `git add` per path, never `-A`. No force-push, no reset, no history rewrite.

## Findings, ordered by what they should cost you

### Fixed: Critical financial-integrity defect

A supplier invoice declaring `HT=-100, TVA=-19, TTC=-119` with matching negative lines reconciled
against its own negative numbers, passed every inline arithmetic check, and was written with
`workflow_state='Validated'` and an **empty** error list. The posting step would have carried a
negative payable into the general ledger. Fixed and covered by two tests.

This was found by *executing* the write path, not by reading it — which is also why the two defects
Phase 34 reported turned out not to exist. See "Corrections" below.

### Open, High: H-1 the hash lock cannot be installed

`pip install --require-hashes -r requirements.lock.hashes.txt` fails because `opencv-python`, a
transitive dependency of `rapidocr-onnxruntime==1.4.4`, is not pinned. The hash lock therefore
provides no supply-chain guarantee for the OCR extra. Fixing it means regenerating the lock and
validating a clean install — its own piece of work. Reported, not guessed at.

### Blocked: G5 twelve statutory questions

No Algerian **expert-comptable** has signed off any statutory rule, and the Phase 32 register is not
in this repository. Rather than paraphrase a document I could not read, Phase 42 lists the twelve
questions **derived from the assumptions the code actually hard-codes** (stamp-duty brackets, the
300 DZD threshold, the 5 DZD minimum, cash-only duty, HT+TVA as base, the 19 %/9 % rate set, per-line
vs total rounding, discount-before-VAT ordering, credit-note sign, rule-version dating, re-stamping,
and the SCF/`E3B*` mapping) — asked, never answered.

The consequence is concrete: **no number this system produces should be treated as
tax-authoritative yet.**

### Blocked: G6 no deployment target

No host, SSH access, TLS certificate, domain, secret manager, artefact registry or monitoring
endpoint exists here. Phase 44 records the prerequisites, a reviewable procedure and the rollback
plan — and states plainly which deployment claims are **not** made.

## Corrections to the prior assessment

Recorded explicitly because earlier documents referenced them:

1. **Phase 34's two data-integrity defects do not exist in this tree.** "Unbounded
   `supplier_invoice_no`" and "`int_parse_invoice_date[:4]` accepts `2026-99-99`" describe `main`.
   The working tree already enforced both. Proven by execution against a real database.
2. **Eleven files overlap with PR #7, not five.**
3. **`require_role_secure` has no optional user parameter** in either tree — signatures are
   identical, and runtime behaviour was verified: `clerk` allowed, `warehouse` denied, `admin`
   allowed.
4. **PR #7's `api.py` has no `/readyz`**, which `ops/dz-erp-readiness.service` depends on. A
   wholesale swap would have silently broken deployment readiness; `api.py` was patched surgically.

## What remains, in dependency order

1. **Regenerate the hash lock** (closes H-1) — pin `opencv-python` and every transitive dependency,
   then prove a clean `--require-hashes` install.
2. **Obtain the twelve statutory answers** (closes G5) — a qualified professional, not code review.
3. **Integrate the timbre-provenance group** — blocked on (2), because it decides which statute an
   invoice is stamped under.
4. **Integrate the SEC-14 write path** — needs the `ocr_field_reviews` schema change, then
   `tests/test_phase29_ocr_review_policy.py` (2,261 lines) becomes runnable.
5. **Supply a deployment target and secrets** (closes G6) — then execute Phase 44's procedure.
6. **Windows-native desktop smoke test** — still NOT EXECUTED; this environment is Linux.

## Honest limits of this report

- Windows desktop behaviour, WCAG and screen-reader conformance, and visual screenshot comparison
  were **not** tested. No claim is made.
- The GUI was exercised on the `offscreen` Qt platform only.
- PR #7 remains a **draft** and its base is `fix/phase29-invoice-discount-and-line-net-consistency`,
  not `main`; it sits atop a 5-deep stack (#3→#7). Nothing was merged, and no PR was opened from this
  branch.
- The backup at `/home/user/phase35-backup` (282 files, hash-verified) remains the independent copy of
  the pre-integration state.
