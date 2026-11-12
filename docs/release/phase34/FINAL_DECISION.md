# Phase 34 — Final decision

## Decision

# **INTEGRATION REQUIRED**

Neither branch may be released as a production ERP candidate on its own.

| Candidate | Security | Accounting | Desktop | Verdict |
| --- | --- | --- | --- | --- |
| **PR #7** (`2427fda` / `f603e38`) | **strong** (SEC-13/14/15, 103 tests) | **strong** (3,583-line lifecycle) | **absent** (monolith only, superseded) | Not releasable — no desktop |
| **This workspace** (`arena/7557bcfb-erp`) | **weak** (no SEC-13/14/15) | **thin** (1,799-line, no totals/reversal/reconciliation) | **strong** (6,000 lines, 49 tests, RTL/a11y/DPI) | Not releasable — security and accounting gaps |
| **Unified: PR #7 backend + this desktop** | strong | strong | strong | **Recommended release candidate** |

## Why not one of the standard classifications

**Not `RELEASE ACCEPTED`:** the workspace accepts a 1-character password, has no Host-header
validation, has no OCR override validation, and cannot validate invoice totals or produce a reversal.
Phase 32's `STAGING VERIFIED` classification was correct *for the desktop scope it covered*; it did
not evaluate the backend gaps this phase found.

**Not `NOT ACCEPTED`:** nothing here is broken. Every test that runs, passes (835/0 on a supported
interpreter; 1373/0 for PR #7). The gaps are absences, not defects, and each has a known, small,
tested remedy.

**Not `BLOCKED`:** nothing is blocked by environment for the integration work itself. Two gates remain
blocked — Windows GUI and expert-comptable sign-off — and they block *release*, not *integration*.

## Immediate actions, in order

| # | Action | Why it is first |
| --- | --- | --- |
| **0** | **Commit or archive the workspace tree** | 0 commits; 34 untracked paths including all desktop code. Only unrecoverable risk in the register. |
| 1 | Baseline all suites on both interpreters | Without it, later changes cannot be attributed |
| 2 | Integrate SEC-15 | Pure addition, no call-site changes, highest security value per line |
| 3 | Integrate SEC-13 | Bounded; 2-line test fix |
| 4 | Integrate `review_policy` + SEC-14 | Verified portable to this workspace |
| 5 | Integrate invoice metadata validation | Closes `2026-99-99` and unbounded invoice numbers |
| 6 | **Decision gate:** adopt PR #7's `procurement_stock.py`? | Scope decision, not a technical one |
| 7 | DR / rate-limit / request-id, one at a time | Expert review required |

No action before step 0. See `RECOMMENDED_INTEGRATION_SEQUENCE.md`.

## What must never be done

| Never | Because |
| --- | --- |
| `git clean`, `checkout -- .`, `reset --hard`, `stash` on this tree | Destroys work that exists in no commit (R-1) |
| Adopt PR #7's `dz_erp/desktop/ui.py` | Deletes the entire desktop programme and reintroduces D-1/D-2 |
| Copy `dz_erp/` wholesale from either side | Drops 11 modules or 9 modules and 753 lines of intake either way |
| Relax the restore interpreter guard | It is what makes the 22 failures correct |
| Treat `2427fda` as "16,448 lines of security" | 974 lines are code; 83% is documentation |
| Quote "1373 passed" without the interpreter | On 3.11.2 the same tree is 1340/20 |

## Confidence

| Finding | Basis | Confidence |
| --- | --- | --- |
| SEC-13/14/15 absent from workspace | `grep` = 0 in all three files; executed PR versions | **Certain** |
| PR #7 suite 1373/0 on 3.12.10 | ran it on a CPython 3.12.10 I built, with `_sqlite3`/`_ssl`/`_hashlib`/`_ctypes` | **Certain** |
| Workspace procurement_stock is ~half-size | 1,799 vs 3,583 lines; 22 named functions absent | **Certain** |
| PR #7 never touched `dz_erp/desktop/` | `git diff HEAD 2427fda -- dz_erp/desktop/` empty | **Certain** |
| `review_policy` imports against this tree | executed it | **Certain** |
| Workspace branch has 0 commits | `git rev-list --count 2427fda..HEAD` = 0 | **Certain** |
| Module sets disjoint | `comm` both ways | **Certain** |
| Integration is incremental | disjoint modules, non-overlapping hunks | **High** |
| Step 6 (accounting swap) is low-risk | not attempted; 1,784-line change | **Unverified — assess at that step** |

## Open gates that this phase does not close

| Gate | Status |
| --- | --- |
| Windows GUI acceptance | **BLOCKED** — no Windows host |
| Windows display scaling 100–200 % | **NOT EXECUTED** |
| Native Arabic linguistic review | **OPEN** — requires a human |
| Expert-comptable sign-off (12 questions) | **PENDING** |
| D-6 (dialog 940×620 at 200 % on 1366×768) | **OPEN**, unverified on real hardware |
| Production TLS / domain / KMS | **NOT CONFIGURED** (outside V1) |

## One sentence

**PR #7 has the security and the accounting; this workspace has the desktop; neither can ship alone,
and the only irreversible mistake available is to lose the half that was never committed.**
