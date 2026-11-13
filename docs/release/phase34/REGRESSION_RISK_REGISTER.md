# Phase 34 — Regression risk register

Risk = (probability of harm if the change is made) × (impact). Scored before any integration, so
that the sequence in `RECOMMENDED_INTEGRATION_SEQUENCE.md` can be ordered by risk, not by taste.

| ID | Risk | Prob. | Impact | Score | Mitigation |
| --- | --- | --- | --- | --- | --- |
| **R-1** | **Uncommitted workspace is destroyed** by any `checkout`, `stash`, `clean`, `reset` or branch switch — 16 modified + 34 untracked paths, including the entire ~6,000-line desktop redesign and all Phase 29/30/34 evidence | **High** if git write-ops are attempted | **Catastrophic** — unrecoverable work | **CRITICAL** | Do not run git write operations. First action of any integration: commit the tree, or archive it (`git archive` of the index is not enough — untracked files must be copied). |
| **R-2** | SEC-15 default-deny makes a deployed instance return 400 to every request because `ERP_ALLOWED_HOSTS` was not set | **High** (it is the default) | **High** — total outage at cutover | **HIGH** | Documented in `PRODUCTION_DEPLOYMENT_GUIDE.md`; must be added to the pre-flight checklist and the deployment test must assert a known-good `Host` |
| **R-3** | SEC-14 imported without `review_policy.py` → `ImportError` at runtime on the OCR review path | Medium if cherry-picked | High — review path dead | **HIGH** | Never lift the SEC-14 hunk alone. It travels with `review_policy.py` and its tests as one unit. |
| **R-4** | SEC-13 breaks callers that create short passwords (tests, seed scripts, operator runbooks) | **Certain** in tests (2 fixtures), possible in ops | Medium | **MEDIUM** | 2-line fix in `test_phase7_authentication_remediation.py`; announce the policy to operators; the floor is 8, not 12, so most real passwords pass |
| **R-5** | Replacing the workspace's `procurement_stock.py` (1,799) with PR #7's (3,583) changes behaviour that current tests and the desktop's `views_invoices.py` depend on | Medium | High — invoice lifecycle is the product | **HIGH** | Replace only as a whole-file swap on a committed tree, then run the full suite **and** the desktop suites; expect API contract adjustments |
| **R-6** | Desktop redesign (untracked) references backend APIs that PR #7 changed | Low | Medium | **LOW-MED** | `api_client.py` is unchanged in both; verified no interface divergence. Test the GUI suite after integration. |
| **R-7** | DR generation layer (`restore_generation`, `backup_storage*`) is imported but the operational env vars are unset → startup gate behaviour changes | Medium | Medium — may refuse startup | **MEDIUM** | PR #7's commits show repeated fail-closed tuning here (7 commits). Needs its own acceptance run, not a copy. |
| **R-8** | Two divergent implementations of the same fix (`decimal_utils`, `require_role_secure`) both survive, and a later merge picks the wrong one | Low | Low | **LOW** | Explicitly choose one during integration (recommendation: keep PR #7's code, workspace's docstrings) |
| **R-9** | Restore/interpreter guard regresses if the 22 → 0 test transition is "fixed" by loosening the guard | Low | **High** — security control | **HIGH** | Never relax the guard. All 22 pass on 3.12.10; that is the correct resolution. |
| **R-10** | Test-count claim inflation: "1373 passed" quoted without the interpreter qualifier, implying the tree passes everywhere | Medium | Medium — false confidence | **MEDIUM** | Always state the interpreter. On 3.11.2 the same tree is 1340/20. |
| **R-11** | `ops/erp.env.example` gains 6 new variables; an operator copies it verbatim with placeholder secrets | Low | High | **MEDIUM** | Ensure the example contains no working secret and the guide says so |
| **R-12** | Evidence documents in the workspace (Phase 8/10) assert a `Host` allow-list that does not exist in code; a future auditor trusts them | **Already realised** | Medium | **MEDIUM** | Correct/annotate those documents when SEC-15 lands; treat prior claims as unverified until re-executed |
| **R-13** | PR #7's own vault report records `B1 OPEN … COMMIT AND PUSH WITHHELD`; treating it as closed evidence is wrong | Medium | Medium | **MEDIUM** | Quote its status line, not its title |
| **R-14** | SQLite version assumptions: workspace tests pass on the system 3.45.3 built here, but the shipped Windows runtime has a different SQLite | Low | Medium | **LOW-MED** | The Windows acceptance run must record `sqlite3.sqlite_version` |

## Risks deliberately *not* taken

| Not done | Why |
| --- | --- |
| `git merge` / `rebase` / `cherry-pick` | Explicitly forbidden; also R-1 makes it dangerous on an uncommitted tree |
| Copying `dz_erp/` wholesale from either side | Would silently drop the other side's modules and the desktop redesign |
| Relaxing the restore interpreter guard | It is the control that makes the 22 failures correct |
| Editing the workspace to match PR #7 during this phase | This phase is assessment only |

## Highest-risk single action

`git clean -fd` or `git checkout -- .` in `/home/user/ERP`.

That one command erases the entire desktop redesign, all desktop tests, and the Phase 29/30/34
evidence — none of which exists in any commit anywhere. If anything in a future step needs those,
the answer is "copy the files out first", never "clean the tree".
