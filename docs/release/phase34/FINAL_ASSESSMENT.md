# Phase 34 — Final assessment

**Assessment only. No code was integrated or modified.** The workspace was read; PR #7 was exported
with `git archive` to `/tmp/pr7` and run there. No branch was switched, no reset/clean/checkout/merge/
rebase/cherry-pick/commit/push was performed, and PR #7 was not touched.

---

## Classification

# **INTEGRATION REQUIRED — neither branch is releasable alone**

- **PR #7 (`2427fda` / `f603e38`) is the stronger backend.** It holds all three security closures
  (SEC-13, SEC-14, SEC-15), 103 security tests, and roughly twice the accounting engine. It **lacks
  the entire desktop application** — not a degraded version, none at all.
- **This workspace is the only source of the desktop.** ~6,000 lines of redesigned PySide6 UI, the
  RTL work, the accessibility fixes, the dark theme, and the Phase 29/30 evidence exist nowhere
  else. It **lacks SEC-13/14/15 and about half of `procurement_stock.py`.**
- **They are complementary, not competing.** Module sets are disjoint; PR #7 never entered
  `dz_erp/desktop/`.

The unified candidate is: **PR #7's backend + this workspace's desktop.** That is a real integration
project, not a merge, and it cannot be reached by copying a directory.

---

## The seven questions, answered

### 1. Which branch contains the strongest security implementation?

**PR #7, unambiguously.**

| Control | Workspace | PR #7 |
| --- | --- | --- |
| SEC-15 Host allow-list | **absent** | present, executed, correct |
| SEC-13 password policy | **absent** (a 1-char password is accepted today) | present, tested |
| SEC-14 OCR override validation | **absent** | present |
| Role gate on upload | present | present (identical) |
| Audit chain | present | present |
| Decimal guards | present | present (same constant) |
| Security tests | 0 of the 103 | 103 |

I verified the PR's three claims by execution, not by reading its report: Host validation rejects
`evil.com`, `localhost.evil.com`, `127.0.0.1.nip.io`, empty and `None`, and accepts all four
loopback/test forms plus port-suffixed variants; the password floor of 8 cannot be configured down;
`review_policy` refuses an empty required field.

**But the margin is narrower than the commit size suggests.** 16,448 insertions are **974 lines of
product code across 9 files** (5.9%); 83% of the commit is documentation. Credit the substance, not
the diff.

### 2. Which branch contains the most complete and tested desktop application?

**This workspace, and it is not close.**

PR #7's desktop is `ui.py`, 1,277 lines, the pre-Phase-29 monolith with hardcoded `#b00020` error
labels and no dark theme — the exact defects the Phase 32 audit found. `git diff HEAD 2427fda --
dz_erp/desktop/` is **empty**: 107 commits, zero desktop work.

The workspace has `tokens.py` (950), `components.py` (1,148), `shell.py` (625), 4 view modules, a
theme manager, formatting and icons — plus 49 passing desktop tests, 18/18 RTL checks, 41/41
accessibility checks and 4/4 DPI checks. **None of it is committed.**

The one place PR #7 wins on tests overall (1373 vs 835) is because it has 62 test files against 37 —
and I confirmed every one of those 1373 passes on CPython 3.12.10.

### 3. Are there accounting or data-integrity regressions?

**No regressions. Substantial absences. Two concrete workspace defects.**

The workspace's `procurement_stock.py` is **1,799 lines**; PR #7's is **3,583** — and PR #7's is the
*ancestor's* size plus 117 lines, not a fork. The workspace is an earlier, roughly half-size
generation. Missing entirely: `_validate_invoice_totals`, `_validate_invoice_lines`,
`_duplicate_invoice_errors`, `_source_invoice_mismatches`, `_line_price_variance`,
`_create_reversal_request`, `revalidate_supplier_invoice`, `_refuse_posting_and_recover`.

So the workspace does not *wrongly* compute — it does not *check* what PR #7 checks. That is a
different and more serious release risk than a regression, because nothing fails loudly.

Two defects that are genuinely present and fixed in PR #7:

- `supplier_invoice_no` has **no length bound** here (PR #7: 128 chars).
- `_parse_invoice_date` here is `int(invoice_date[:4])`, which **accepts `2026-99-99`** — it never
  looks past the year.

Against that, the workspace has one accounting-adjacent improvement PR #7 lacks:
`_pdfplumber_model_version` reads the installed parser version instead of hardcoding
`"pdfplumber-0.11.6+pdfminer"`, so the audit provenance cannot go stale.

### 4. Which security improvements must be preserved?

Five, in dependency order:

1. **SEC-15** `dz_erp/net.py` allow-list + `dz_erp/api.py` middleware. *With a deployment caveat:*
   the default answer set is loopback-only, so an unconfigured production deploy returns **400 to
   every request**. Fail-closed is correct, but it must be in the runbook.
2. **SEC-13** `dz_erp/auth.py` password policy + rehash-on-login. Breaks 2 test fixtures only.
3. **`review_policy.py`** — the prerequisite, and I verified it imports cleanly against this
   workspace's `intake_ocr.py`, so it is portable now.
4. **SEC-14** the 30-line override validation in `intake_ocr.py`.
5. **Invoice metadata validation** — three small helpers that close a real integrity hole.

Preserve also, from this side: the **provenance fix**, and the **entire desktop layer**.

### 5. Can the two codebases be integrated incrementally?

**Yes.** This is the strongest finding of the phase.

- Module sets are **disjoint** — 9 modules only in PR #7, 11 only here, zero name collisions.
- PR #7 **never touched** `dz_erp/desktop/`, so the redesign cannot conflict.
- The five overlapping files have **non-overlapping hunks**; where both fixed the same bug
  (`decimal_utils`, `require_role_secure`) the fixes are functionally identical — convergent, not
  contradictory.
- `review_policy.py` — the module I expected to be the blocker — **imports successfully** against the
  current workspace tree, which I verified by execution.

The one hard constraint: **`procurement_stock.py` is a whole-file decision**, not a patch series. It
differs by 1,784 lines of base content.

### 6. What is the recommended authoritative release-candidate architecture?

```
   ┌──────────────────────────────────────────────────────────────┐
   │  Desktop: this workspace's redesign (~6,000 lines, PySide6)  │  ← only exists here
   │  tokens / theme / components / shell / views / workers       │
   └───────────────────────────┬──────────────────────────────────┘
                               │ httpx → loopback (unchanged api_client.py)
   ┌───────────────────────────▼──────────────────────────────────┐
   │  Backend: PR #7's dz_erp/ (38 modules)                       │  ← only complete there
   │  SEC-13/14/15 · review_policy · full invoice lifecycle       │
   │  rate_limit · request_id · DR generation · audit chain       │
   └───────────────────────────┬──────────────────────────────────┘
                               │
   ┌───────────────────────────▼──────────────────────────────────┐
   │  SQLite schema: PR #7's lineage (forward-compatible)         │
   └──────────────────────────────────────────────────────────────┘
```

**Base the unified candidate on PR #7, and lift the desktop onto it.** Not the reverse: the workspace
would require importing 9 modules and ~1,784 lines of accounting, while PR #7 requires lifting 11
files that have no dependency on its changes.

Keep from this workspace, explicitly: `dz_erp/desktop/**` (all 11 modules), `test_phase29_desktop_redesign.py`,
the `_pdfplumber_model_version` fix, and `docs/release/phase29` + `phase30` + `phase34`.

Do **not** carry PR #7's `desktop/ui.py`, its `#b00020` labels, or its 75 documentation files.

### 7. What tests must be run after integration?

**Blocking:**

1. Full suite on **CPython 3.12.10** → expect **0 failed** (PR #7's 1373 plus the workspace's
   desktop tests, minus superseded ones).
2. Full suite on **3.11.2** → expect **only** the 22 restore-guard failures. Any 23rd is a
   regression.
3. **Desktop suites** (26/27/28/29) → 49 passed.
4. **Phase 30 gates** → RTL 18/18, accessibility 41/41, DPI 4/4, restore drill 25/25.
5. **SEC acceptance**: phase 58 (55), 59 (39), 60 (9) → all pass.
6. **Live Host probe**: `Host: evil.com` → 400, `Host: localhost:PORT` → 200.
7. **Password policy probe**: 7 chars refused, `ERP_PASSWORD_MIN_LENGTH=4` clamps to 8.
8. **ruff + compileall** clean; dependency integrity 72 passed, 0 advisories.
9. **Financial invariant**: post an invoice, confirm trial balance balances and restore preserves it
   byte-identically (the Phase 30 drill, re-run on the merged tree).

**Cannot be closed by testing:**

10. **Windows GUI acceptance** — still BLOCKED, no Windows host.
11. **Expert-comptable sign-off** — 12 statutory questions still PENDING.
12. **Windows high-DPI** at 100/125/150/200 % — unverified in situ.

## What this phase changed

Nothing. `docs/release/phase34/` was created; no file outside it was written; the working tree's
16 modified and 34 untracked paths are exactly as found; no Phase 29–33 evidence was touched.

## The risk that outranks all the others

**The workspace branch has zero commits.** Every desktop module, every test, every phase of evidence
lives only as uncommitted files on disk. A single `git clean -fd` or `git checkout -- .` destroys
work that exists in no commit, on no remote, and in no reflog.

Whatever is decided next, **preserving that tree is step zero** — and it is the one action that does
not need an architecture decision first.
