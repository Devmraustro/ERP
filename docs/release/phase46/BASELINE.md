# Phase 46 — Step 1: Baseline Revalidation of PR #8 and Repository Identity

**Recorded:** 2026-11-09
**Executor:** agent session on `arena/7557bcfb-erp`
**Status:** COMPLETE

This document records the state of the repository and of PR #8 **as found**, before
any Phase 46 change. Two findings materially changed the plan for this phase: the
local checkout had been reset to the base commit, and remote CI was red.

---

## 1. Repository identity

| Property | Verified value |
| --- | --- |
| Repository root | `/home/user/ERP` |
| Remote `origin` | `https://github.com/Devmraustro/ERP.git` |
| Branch | `arena/7557bcfb-erp` |
| Base commit | `4fc1364a9504fab1153ea44e753f354f00023428` (`main`) |
| Head at session start (remote) | `90f382a55758d2dcae0e3bf9c9b55cfb2ae3168c` |
| Head at session start (local) | `4fc1364` — **mismatch, see §2** |
| PR | **#8** — https://github.com/Devmraustro/ERP/pull/8 |
| PR source → base | `arena/7557bcfb-erp` → `main` |
| PR state | `OPEN`, not draft |
| Files changed by PR (at `90f382a`) | 320 files, +49033 / −1440 |

### The four integration commits

| SHA | Subject |
| --- | --- |
| `3106882` | Phase 29–35 integration (request-id, rate limiting, review policy, SEC-13/15) |
| `f48db93` | Phase 36–42 evidence documents and release acceptance report |
| `a20a478` | Lock-drift correction and dependency-graph verification tooling |
| `90f382a` | Re-verified committed tree after full toolchain rebuild (946P/3S/1D/0F) |

---

## 2. Finding A — the local checkout had been reset to the base commit

At session start `git rev-parse HEAD` returned `4fc1364` and `git status` reported
60 dirty entries and 43 untracked paths. The four integration commits were **absent
locally** while the remote branch still carried them at `90f382a`.

**Recovery, and proof that nothing was lost:**

1. `git fetch --no-tags origin '+refs/heads/*:refs/remotes/origin/*'` — recovered all
   four commits.
2. `git archive 90f382a | tar -x -C /tmp/p46cmp`, then
   `diff -rq /tmp/p46cmp /home/user/ERP --exclude=.git` → **empty output**.
   The working tree was **byte-identical** to the PR head, so the reset had moved
   only branch/index pointers, never file content.
3. Preservation snapshot taken **before** touching refs:
   `/home/user/phase46-backup/worktree_pre_restore.tar.gz` (5.75 MB).
4. `git reset --mixed 90f382a` — aligns `HEAD` and the index, leaves the worktree
   untouched. (`git reset --soft` was tried first and staged 363 spurious deletions
   because the commit's tree differed from the worktree; it was corrected with
   `--mixed`.)
5. Re-verified: `HEAD = 90f382a`, `git status --porcelain` empty, and the worktree
   diff against `90f382a` again empty.

**Conclusion:** repository identity restored with zero content loss; no Phase 29–45
evidence was modified or overwritten.

---

## 3. Finding B — remote CI was red on both Linux jobs

`gh pr checks 8` at baseline:

| Job | Result |
| --- | --- |
| Test, coverage and static checks (Python 3.11) | **fail** (26 s) |
| Test, coverage and static checks (Python 3.12) | **fail** (45 s) |
| Key rotation, backup and recovery on Windows | pass (2 m 32 s) |

Both Linux jobs failed at the step "Run the complete test suite with coverage" in
run `43328400181`. `gh run view --log`, `--log-failed` and `gh run download` all
failed with `EOF` from the artifact store, so the failure was reproduced locally
instead.

### Root cause (reproduced)

`tests/test_phase29_ocr_review_policy.py` failed **at import**:

```
ImportError: cannot import name 'decide_extracted_field_review' from 'dz_erp.intake_ocr'
```

pytest exits 2 with "Interrupted: 1 error during collection", so **no test in the
suite ran at all**. The cause is commit `3106882`, which ported the Phase 29 test
file from PR #7 without porting the module code it exercises.

This is prior-phase work, not a Phase 46 regression, and it invalidated the earlier
"946P/0F" local report as evidence of CI health: that report was produced by a run
that could not have collected this file.

**Also established:** CI installs dependencies with
`pip install --no-deps --require-hashes -r requirements.lock.hashes.txt`, which
**succeeds** (verified locally, exit 0). CI therefore never hits the H-1 resolution
defect; H-1 affects dependency-resolving installs only.

---

## 4. Tree correspondence with the 946-pass report

The workspace tree at `90f382a` corresponds exactly to the tree described by the
Phase 35 report (`docs/release/phase35/logs/full_312_final.log`, 946P/3S/1D/0F).
The report's numbers remain valid for the set of tests that existed at that time;
they are **not** evidence that the suite collects today, because the Phase 29 test
file was added afterwards by `3106882` and could not be collected.

## 5. Unexpected modifications / untracked files

None after recovery. The 60 dirty entries and 43 untracked paths observed at session
start were the expected consequence of the ref reset described in §2 (tracked files
appearing "modified" relative to the wrong base commit, plus the CI/cache artefacts
present in the sandbox). After `git reset --mixed 90f382a` the worktree was clean.

## 6. Preserved evidence

| Artefact | Path |
| --- | --- |
| Pre-restore worktree snapshot | `/home/user/phase46-backup/worktree_pre_restore.tar.gz` |
| External pre-integration backup (282/282 verified) | `/home/user/phase35-backup/` |
| Phase 35 toolchain rebuild kit | `docs/release/phase35/toolchain/` |
| Phase 35 authoritative runs | `docs/release/phase35/logs/` |
| Phase 36–45 deliverables | `docs/release/phase36..45/` |

No Phase 29–45 evidence directory was written to, moved, or deleted during Phase 46.

---

## 7. Baseline gate status (carried from Phase 42, unchanged on entry)

| Gate | Status on entry |
| --- | --- |
| G1 — integration completeness | PASS |
| G2 — dependency integrity (H-1) | **FAIL** |
| G3 — security controls | PASS |
| G4 — accounting/procurement invariants | PASS |
| G5 — Algerian statutory/accounting expert approval | **BLOCKED** |
| G6 — production deployment readiness | **BLOCKED** |
