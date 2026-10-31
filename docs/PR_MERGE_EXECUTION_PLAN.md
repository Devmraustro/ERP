# PR merge execution plan — #3 → #4 → #5 → #6 → #7

Prepared from the review in `docs/PR_STACK_REVIEW.md`. **Nothing here has been
executed.** Every step below requires explicit authorization; this document is a
plan, not a record of actions taken.

Guiding rule: a green CI run is a precondition, not an authorization. Two items
in §4 have no evidence available to me at all (human review, branch protection)
and must be supplied by a responsible human before the first merge.

---

## 1. The order, and why it is the order

```
main (4fc1364a)
  └─ #3 29645e5d  phase29/ocr-failclosed                        ← ready (not draft)
      └─ #4 99864bab  fix/phase29-source-invoice-consistency     ← draft
          └─ #5 c466cea7  fix/…-line-price-consistency          ← draft
              └─ #6 e4e47b3b  fix/…-discount-and-line-net-…     ← draft
                  └─ #7 c23e2d65  arena/7f12d627-erp            ← draft
```

Verified by ancestry, not by the UI: each head is a strict descendant of the
previous head, and each PR's reported `base.sha` equals the previous PR's head.
The order #3 → #4 → #5 → #6 → #7 is the only order that preserves this
relationship. **No mismatch was found; nothing in Step 4 of the brief stops.**

## 2. Per-PR execution table

| Field | #3 | #4 | #5 | #6 | #7 |
|---|---|---|---|---|---|
| PR number | 3 | 4 | 5 | 6 | 7 |
| Base branch | `main` | `phase29/ocr-failclosed` | `fix/phase29-source-invoice-consistency` | `fix/phase29-invoice-line-price-consistency` | `fix/phase29-invoice-discount-and-line-net-consistency` |
| Base SHA (verified ancestor) | `4fc1364a` | `29645e5d` | `99864bab` | `c466cea7` | `e4e47b3b` |
| Head SHA | `29645e5d` | `99864bab` | `c466cea7` | `e4e47b3b` | `c23e2d65` |
| CI run (exact head) | [40763416753](https://github.com/Devmraustro/ERP/actions/runs/40763416753) | [40775473917](https://github.com/Devmraustro/ERP/actions/runs/40775473917) | [40965006912](https://github.com/Devmraustro/ERP/actions/runs/40965006912) | [41064213366](https://github.com/Devmraustro/ERP/actions/runs/41064213366) | [41142731746](https://github.com/Devmraustro/ERP/actions/runs/41142731746) |
| CI result | success 3/3 | success 3/3 | success 3/3 | success 3/3 | success 3/3 |
| Local suite at head (3.11.2) | 695/20/6 | 698/20/6 | 708/20/6 | 719/20/6 | 960/20/8 |
| New failures vs previous PR | none | none | none | none | none |
| Draft | **no** | yes | yes | yes | yes |
| Mergeable / state | MERGEABLE / CLEAN | MERGEABLE / CLEAN | MERGEABLE / CLEAN | MERGEABLE / CLEAN | MERGEABLE / CLEAN |
| Reviews recorded | 0 | 0 | 0 | 0 | 0 |
| Review findings | none blocking | none | none (nit) | none (nit) | none blocking; R-01, R-02, R-03, R-04, R-05, R-06, R-07 |
| Safe to merge after the previous PR? | **Yes** (first) | **Yes**, after `#3` is in `main` **and** its base is retargeted (§3) | **Yes**, after `#4` | **Yes**, after `#5` | **Yes**, after `#6`; R-01…R-07 are closed (§8.1) and human review is the remaining gate |

## 3. The one mechanical trap: base branches do not follow the merge

GitHub retargets a PR's base **only when the base branch is deleted**. Every
branch in this chain still exists, so after `#3` is merged into `main`:

* `#4` is still based on `phase29/ocr-failclosed`. Merging it would land its
  commits in **that branch**, not in `main`;
* the same holds for `#5`, `#6` and `#7` — a naive "merge each PR into its stated
  base" run would leave `main` with **only PR #3's** content.

Two viable procedures:

**A. Sequential with retargeting (recommended).** After each merge, retarget the
next PR to `main`, confirm the ancestry relationship is now `main` → head (it
will be, because the heads are linearly ordered), then merge. Retargeting is a PR
metadata change and needs the same authorization as the merge.

| Step | Action | Effect |
|---|---|---|
| A1 | Merge `#3` into `main` | `main` = merge of `29645e5d` |
| A2 | Retarget `#4` to `main` | base becomes the new `main` head |
| A3 | Merge `#4` into `main` | `main` gains `29645e5d..99864bab` |
| A4–A7 | Repeat retarget + merge for `#5`, `#6`, `#7` | `main` ends at `c23e2d65`'s content |

Because each head descends from the previous, every step after A1 is a
fast-forward-able merge — no conflict resolution should ever be required. **If a
conflict appears, stop:** it means the ancestry assumption broke and the chain
must be re-reviewed rather than resolved by hand.

**B. Single landing (not recommended).** Merge `#3`, then retarget only `#7` to
`main` and merge it, leaving #4–#6 open. This lands the same code in one step but
loses the per-PR review granularity the chain was built for and leaves three
stale PRs pointing at branches already contained in `main`. Only choose this if
the reviewers explicitly decide the intermediate PRs carry no separate value.

**Do not** delete the base branches to force GitHub's automatic retargeting: the
branches are the review record and deletion is destructive.

### 3.1 Why the manual step is mandatory in *this* repository (verified 2026-10-31)

GitHub's automatic retargeting only fires when the merged PR's **head branch is
deleted**. Verified facts:

* `delete_branch_on_merge` is **`false`** (`gh api repos/Devmraustro/ERP`) — the
  merge will not delete `phase29/ocr-failclosed`;
* `allow_merge_commit`, `allow_squash_merge` and `allow_rebase_merge` are all
  `true`, so the merge *strategy* is an operator choice, not a repository rule;
* branch protection on `main` is **not readable** with the automation token
  (`403 Resource not accessible by integration`) — do not assume either way;
  confirm required checks/reviews in repository settings first.

Consequences, stated plainly:

1. **Retargeting is required after every merge**, not a convenience. If it is
   skipped, the next PR merges into the previous *branch* and `main` never
   receives the change — the exact failure mode §3 describes.
2. **Use a merge commit, never a squash.** A squash rewrites each commit, so the
   branches stacked above it no longer share history and would need a rebase —
   and rebasing published history is forbidden by this task. Rebase-merge has
   the same defect for the same reason.
3. **Stop condition.** After a merge, if the next PR's base is not `main` and has
   not been explicitly retargeted, stop and report. If GitHub has retargeted a PR
   to anything other than `main`, stop: it means a base branch was deleted, which
   this plan forbids.

## 4. Required actions before the first merge

These are gates, not suggestions. Each needs a named human, not an agent.

| # | Gate | Current evidence | Owner |
|---|---|---|---|
| G1 | **Human review recorded** on `#3` (and ideally each subsequent PR) | **0 reviews on all five PRs** | accountable reviewer |
| G2 | **Branch protection / required checks confirmed** | **unknown** — `403` reading protection with the available token, rulesets `[]` | repo admin |
| G3 | **Explicit authorization to merge** | not given (this pass is review-only) | product/engineering owner |
| G4 | **Draft state lifted** on `#4`–`#7` at their turn | four PRs are draft; a draft cannot merge | PR author / owner |
| G5 | **Disposition of R-01** (stale `last_backend_error` mislabels refusals) | **FIXED — closed**; `current_backend_error`/`backend_health()`, 17 tests in `tests/test_phase43_limiter_hardening.py` | engineering owner |
| G6 | **Disposition of R-05** (no audited path to add a timbre rule version) | **FIXED — closed** (code `FIXED_AND_VERIFIED`); the tax *values* remain subject to the external expert-comptable sign-off, unchanged by this fix | engineering + accounting owner |
| G7 | **R-06 documentation correction** (two docs still say the rule label is not persisted) | **FIXED — closed**; §7 of `ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` rewritten, `TECHNICAL_REPORT.md` and `RISK_REGISTER.md` corrected | engineering |

Optional but cheap, recommended before merging #7: add the missing concurrent
**writer** test for the shared sqlite limiter (R-02) and a bound on the
client-supplied `X-Request-Id` (R-04). Neither blocks.

## 5. CI refresh policy

A merge must be authorised against CI **on the exact head being merged** — the
runs in §2 are exactly that, and retargeting does not change a head SHA, so those
runs stay valid through §3's procedure.

Two cases where a re-run is required:

1. **The head changes** (any new commit pushed to a reviewed branch) — the §2 run
   is then stale evidence; re-run and re-read the job table before merging.
2. **The review-delivery commit.** These documents were committed on top of
   `c23e2d65` as `359b111`, which advances PR #7's head to a new SHA. Its run
   **`41153210738`** is `success` 3/3. It **supersedes none of the code
   evidence** (documentation only), but the reviewer must confirm the PR's actual
   head has a green run before merging #7, because that head is what merges.

## 6. What must never happen in this sequence

* No merge, close, rebase, retarget, ready-mark, deploy or force-push without a
  separate, explicit authorization for that specific action.
* No `--admin`, no bypassing a required check, no merging on a red or stale run.
* No squash that would separate a PR's commits from the tests that justify them
  without the reviewers' agreement — the commit messages carry the finding IDs
  that later documents cite.
* No history rewrite to "tidy" the chain: the pairwise ancestry is the thing that
  makes this plan safe.
* No claim of production readiness at any point. Merging is not releasing; every
  external gate in `docs/RELEASE_READINESS_MATRIX.md` and
  `docs/REMAINING_BLOCKERS.md` stays exactly as it is.

## 7. Post-merge verification (after the final merge, still not a release)

1. `git log --oneline main` shows the expected sequence, no merge surprises.
2. CI on the `push` to `main` (the workflow runs again on the protected branch) is
   green.
3. `python -m pytest -q --cov=dz_erp` on `main` reproduces the interpreter-floor
   set only.
4. The tracker's review-status row is updated to reflect the merge that actually
   happened, with SHAs and run IDs.

## 8. Stop condition

This plan is complete and **deliberately unexecuted**. The stack is reviewed,
the order is verified, and every remaining gate is named with an owner. Work
stops here pending explicit authorization.

---

## 8. Remediation result (Phase 45) — the gate table is now closed

G5, G6 and G7 above were the three items that stood between the stack and an
explicit merge authorization. They are closed on `arena/7f12d627-erp`; §2–§7 are
left as written so the plan of record is not retro-edited.

| Gate | Was | Now | Evidence |
|---|---|---|---|
| **G5** R-01 | finding open | **closed** | `dz_erp/rate_limit.py`: per-call error record, `current_backend_error`, `backend_health()`; `tests/test_phase43_limiter_hardening.py` (17 tests) |
| **G6** R-05 | finding open | **closed** (code) | DRAFT → two-person approve, explicit overlap policy, audit per step, three endpoints; `tests/test_phase46_timbre_rule_administration.py` (35 tests). The tax *values* stay `BLOCKED_EXTERNAL` — this gate never covered them |
| **G7** R-06 | finding open | **closed** | `ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §7 rewritten; `TECHNICAL_REPORT.md`, `RISK_REGISTER.md`, `ALGERIAN_SCF_LOCALIZATION_SPEC.md` corrected |
| (R-02) concurrent writers | not a gate, needed for honesty | **closed** | multi-process writer, boundary, cleanup, lock-contention, interruption and high-cardinality tests; latency recorded |
| (R-03) `blocked()` write lock | not a gate | **closed, bounded claim** | `read_probe()`; 0.794 ms read vs 2.331 ms write; contention reduced, **not** eliminated |
| (R-04) `X-Request-Id` | not a gate | **closed** | `dz_erp/request_id.py` + 19 tests |
| (R-07) interpreter gate | not a gate | **closed** (rule) / **BLOCKED_EXTERNAL** (host) | one predicate, matrix-parameterised tests, AST guard against a second copy |
| **human review** | not a gate in §4, but a hard gate in practice | **OUTSTANDING** — 0 reviews on all five PRs | §8.4 checklist in `docs/PR_STACK_REVIEW.md` |

### 8.1 Ordering is unchanged

This section changes *what* is left, not *how* it is applied. The order in §4
still holds exactly: `#3` → retarget `#4` → `#5` → `#6` → `#7`, one at a time,
each after the previous is in `main`, each with its own green CI on its own head.
**No step in this plan has been executed**: nothing was merged, retargeted,
closed, rebased, marked ready or deployed, and no branch was force-pushed.

### 8.2 Required before authorization (unchanged in substance, now unblocked)

1. Human review of PR #7 (draft, 0 reviews). The remediation is *evidence for*
   that review, not a substitute for it.
2. Confirmation that the finding classification in
   `docs/PR_STACK_REVIEW.md` §7 is accepted — in particular that R-05's code fix
   does not imply the tax values are legally qualified.
3. The external gates in `docs/REMAINING_BLOCKERS.md` remain exactly as listed;
   none of them was closed by this remediation.

### 8.3 Where the evidence lives

* per-finding disposition: `docs/PR_STACK_REVIEW.md` §7
* exact commit SHAs, CI run URLs and test counts: `docs/PR_STACK_REVIEW.md` §7.3
* the start-up defects the concurrency work uncovered:
  `tests/test_phase49_audit_key_bootstrap_race.py` and
  `tests/test_phase43_limiter_hardening.py::test_concurrent_process_startup_does_not_race_the_schema_migrations`

Executing any row of this plan still requires an explicit merge authorization
from the repository owner. Nothing has been executed.
