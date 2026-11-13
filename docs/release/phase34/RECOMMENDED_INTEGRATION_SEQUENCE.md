# Phase 34 — Recommended integration sequence

**Nothing in this document has been executed.** It is a plan, produced read-only, and it starts only
after explicit approval.

## Step 0 — Preserve the untracked tree (BLOCKING, risk R-1)

The workspace branch has **0 commits**. `git rev-list --count 2427fda..HEAD` → 0, and
`git status` reports 16 modified + 34 untracked paths. The entire desktop redesign and all evidence
exist only on disk.

Before anything else:

```bash
cd /home/user/ERP
git archive --format=tar -o /tmp/workspace-preserve/modified.tar $(git write-tree)  # tracked
tar czf /tmp/workspace-preserve/untracked.tar.gz $(git ls-files --others --exclude-standard)
# plus every modified tracked file:  git diff > /tmp/workspace-preserve/modified.patch
```

…then commit the tree on the working branch. **Do not** use `git stash`, `git clean`, `git checkout`,
`git reset` or a branch switch to do this — R-1 explains why.

**Exit criterion:** `git status --porcelain` is empty and the three artefacts above exist.

## Step 1 — Prove the current tree is green (baseline)

Run and record, on both interpreters:

| Run | Command | Expected |
| --- | --- | --- |
| Workspace, 3.11.2 | `/tmp/vui/bin/python -m pytest tests/ -q` | 838 passed / 22 failed / 3 skipped (22 = restore guard) |
| Workspace, 3.12.10 | `/tmp/py312/bin/python3 -m pytest tests/ -q` | 835 passed / 0 failed / 20 skipped |
| Desktop | `tests/test_phase2{6,7,8,9}_*.py` | 49 passed |
| Phase 30 evidence | `restore_drill.py`, `rtl_capture.py`, `accessibility_probe.py`, `dpi_probe.py` | 25/25, 18/18, 41/41, 4/4 |

**Exit criterion:** failure sets match the documented baseline exactly.

## Step 2 — SEC-15 (lowest risk, highest value)

Two files, pure addition.

1. Append the five Host functions to `dz_erp/net.py` (+101 lines, no existing symbol touched).
2. Add `_validate_host_header` and `allowed_hosts` to `app.state` in `dz_erp/api.py`.

**Watch:** the desktop embedded service binds `127.0.0.1` on an ephemeral port (verified:
`service.py` `_LOOPBACK_HOSTS`), and `127.0.0.1` is in `DEFAULT_ALLOWED_HOSTS`, so the desktop must
keep working. `testserver` is also allowed, so `TestClient` keeps working.

**Required tests:** `test_phase59_sec13_14_15_closure.py` Host cases; a live probe asserting
`Host: evil.com` → 400 and `Host: localhost:PORT` → 200; the full API suite; one desktop
embedded-service smoke test.

**Exit criterion:** 400 on hostile Host, 200 on loopback, desktop suite unchanged.

## Step 3 — SEC-13 (small, bounded)

1. `dz_erp/auth.py` (+225).
2. Patch the **2** short passwords in `tests/test_phase7_authentication_remediation.py`
   (`P#2026x` → `Prov#2026x`).

**Watch:** R-4 — the policy floor is 8, configurable upward, never downward. Verify
`ERP_PASSWORD_MIN_LENGTH=4` still yields 8.

**Required tests:** phase 59; the auth/authz subset (147 tests); a manual check that an existing
600k-iteration hash is accepted and rehashed.

**Exit criterion:** 147 auth tests pass; a 7-char password is refused with `PASSWORD_TOO_SHORT`.

## Step 4 — SEC-14 with `review_policy` (medium, now de-risked)

**Measured finding that changes the order:** `review_policy.py` imports only
`decimal_utils` and two constants from `intake_ocr` — both present in the workspace. I executed
`from dz_erp import review_policy` against the workspace tree and it **succeeds**, and
`rule_for`/`validate_corrected_value` behave correctly (`''` → `FIELD_VALUE_REQUIRED`).

So this is a genuine two-file change, not a subsystem import:

1. Add `dz_erp/review_policy.py`.
2. Apply the 30-line SEC-14 hunk to `update_extracted_field_by_human`.
3. Add `tests/test_phase29_ocr_review_policy.py`.

**Watch:** `intake_ocr.py` is 1,612 lines here vs 2,365 in PR #7. `review_policy` imports cleanly
today, but any *other* Phase 29 consumer (line reconciliation, duplicate rows) needs the larger
`intake_ocr` — that is Step 7, not this step.

**Exit criterion:** OCR/workflow suites green; a rejected override writes `SECURITY_FIELD_EDIT_REJECTED`
and stores nothing.

## Step 5 — Invoice metadata validation (small, self-contained)

Three helpers into `dz_erp/procurement_stock.py`: `_require_supplier_invoice_no` (≤128),
`_require_valid_payment_method`, `_parse_invoice_date`.

**Watch:** `_parse_invoice_date` replaces `int(invoice_date[:4])`, which currently accepts `2026-99-99`.
Confirm no existing fixture supplies a malformed date that was previously (wrongly) accepted.

**Exit criterion:** invoice lifecycle suite green; `2026-99-99` and a 200-char invoice number are
refused.

## Step 6 — Re-baseline; decide on the accounting engine (**decision gate**)

At this point the workspace has SEC-13/14/15 plus metadata validation, on its own 1,799-line
accounting module.

**This is the fork in the road.** Options:

| Option | Get | Give up | Recommend |
| --- | --- | --- | --- |
| **A. Stop here** | Secure enough for staging; desktop intact | No totals validation, no reversal, no source reconciliation | Only if the release scope excludes those |
| **B. Adopt PR #7's `procurement_stock.py`** | Full posting integrity | Requires re-running every invoice test and re-checking `views_invoices.py` against a changed API | **Yes, before production** |
| **C. Full merge of PR #7 then re-apply the desktop** | Everything | Large re-verification; the desktop must be re-integrated onto a new backend | The endgame, see Step 8 |

**Do not decide this by preference.** Decide it by release scope: if the product must post invoices
with reversal and source reconciliation, Option A is not shippable.

## Step 7 — Remaining PR #7 backend modules (REQUIRES EXPERT REVIEW)

One at a time, each with its own acceptance run:

1. `request_id.py` — lowest risk, clean boundary.
2. `rate_limit.py` — introduces `ERP_RATE_LIMIT_BACKEND`; verify the in-process default.
3. `review_policy` line reconciliation + larger `intake_ocr` — brings phase 29 OCR tests.
4. DR generation layer — **expert review**. 7 commits of fail-closed tuning; a startup gate that
   misjudges refuses to boot. Test with RPO/RTO env vars set and unset.
5. Remote backup storage / alerting — **expert review**; external credential path.

**Exit criterion per module:** its own test file green, plus no change to the workspace's failure set.

## Step 8 — Desktop re-integration (only for Option C)

The desktop is the workspace's unique asset and PR #7 never touched `dz_erp/desktop/`. If the
backend is replaced wholesale, re-run:

- 49 desktop tests
- Phase 30 gates: RTL 18/18, accessibility 41/41, DPI 4/4, restore drill 25/25
- A visual pass, because `views_invoices.py` renders fields that a changed API may return differently

## Step 9 — Final acceptance

| Gate | Command |
| --- | --- |
| Full suite, 3.12.10 | expect **0 failed** |
| Full suite, 3.11.2 | expect exactly the 22 restore-guard failures |
| Desktop suites | 49 passed |
| Phase 30 evidence | all four scripts at their documented counts |
| ruff / compileall | clean |
| Dependency integrity | 72 passed, 0 advisories |
| Windows GUI | **still BLOCKED** — no Windows host |
| Expert-comptable sign-off | **still PENDING** — 12 questions |

## Why this order

1. **Preserve first** — R-1 is the only unrecoverable risk.
2. **Baseline second** — without it, no later change can be attributed.
3. **SEC-15 before SEC-13** — SEC-15 is a pure addition with zero call-site changes; SEC-13 touches
   a credential path every test uses. Lower-risk work first keeps the failure set attributable.
4. **SEC-14 after SEC-13** — both touch user-controlled input, but SEC-14's dependency
   (`review_policy`) is now proven portable, so it can follow immediately.
5. **Metadata validation before the accounting swap** — it is three helpers, and it makes the later
   whole-file swap smaller and better tested.
6. **Accounting swap after a decision gate** — it is the largest behavioural change and must be a
   conscious scope decision, not an accident.
7. **Desktop last** — it depends on the backend's API shape, so re-integrating it before the backend
   settles would mean doing it twice.

## Estimated surface per step

| Step | Files touched | Lines | New tests | Blocking risk |
| --- | --- | --- | --- | --- |
| 0 | 0 (git only) | 0 | 0 | R-1 |
| 2 | 2 | ~110 | use existing | R-2 (deployment) |
| 3 | 2 | ~227 | use existing | R-4 |
| 4 | 3 | ~250 | 1 file | — |
| 5 | 1 | ~60 | use existing | — |
| 6 | 1 | +1,784 | existing | **R-5** |
| 7 | 5+ | ~1,500 | 6 files | R-7, R-8 |
| 8 | 0 code | 0 | re-run | R-6 |

Total for a production-grade candidate: roughly **2,000 lines of backend change** plus
**103 adopted security tests**, on top of the ~6,000-line desktop that already exists here.
