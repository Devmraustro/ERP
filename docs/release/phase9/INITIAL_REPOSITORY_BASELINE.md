# Initial Repository Baseline — Phase 9 (execution start)

Recorded: 2026-11-09 (UTC) · Recorded by: execution agent · Method: read-only inspection of
the actual checkout; no reset, clean, checkout-over, stash, rebase or merge was performed.

This document exists because **the baseline stated in the Phase-8/9 brief does not match
the repository that is actually reachable from this environment**. The differences are
material, so they are recorded before any work is done, as the brief requires.

---

## 1. The repository as it actually is

| Item | Measured value |
| :--- | :--- |
| Path | `/home/user/ERP` (Linux sandbox checkout of `https://github.com/Devmraustro/ERP.git`) |
| Branch | `arena/7557bcfb-erp` |
| HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` — *"Merge pull request #2 from Devmraustro/arena/5c240e1d-erp"* |
| Clone type | **shallow** (`.git/shallow` contains `4fc1364a…`, `git rev-list --count HEAD` = 1) |
| Tracked files | 226 |
| Test modules | 33 |
| Workflow | `.github/workflows/ci.yml` |
| `requires-python` | `>=3.11.17,<3.13,!=3.12.0…!=3.12.9` |

### 1.1 Working tree (user work — preserved, untouched)

```
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
?? docs/security/
?? tests/test_phase34_security_validation.py
```

Nothing in this working tree was modified, staged, stashed or removed by this session.
The files above are the *only* uncommitted work present in this environment.

### 1.2 Remote branches

```
origin/HEAD -> origin/main
origin/main
```

`git ls-remote` shows one branch ref (`refs/heads/main` at `4fc1364a…`) plus the PR head
branch `refs/heads/arena/7f12d627-erp` (visible through the pull request, not through the
default fetch refspec).

### 1.3 Pull request #7

| Field | Value |
| :--- | :--- |
| URL | https://github.com/Devmraustro/ERP/pull/7 |
| State | **OPEN**, **draft**, not merged |
| Head branch | `arena/7f12d627-erp` @ **`f603e38206f938808997354ec2045d893ba47be7`** |
| Base branch | `fix/phase29-invoice-discount-and-line-net-consistency` |
| Commits / files | 100 commits, 179 files |
| Mergeable | `MERGEABLE` |

The PR head tree contains 377 tracked files, 64 test modules, and the modules the
security work depends on (`dz_erp/review_policy.py`, `dz_erp/rate_limit.py`,
`dz_erp/net.py`), plus `docs/security/`.

### 1.4 CI on the PR head

| Run | Commit | Result |
| :--- | :--- | :--- |
| [42696335206](https://github.com/Devmraustro/ERP/actions/runs/42696335206) | `2427fda` | **all 5 jobs success** (Py 3.11, Py 3.12, Windows 3.12.10 restore, pip-audit, Docker build/SBOM/trivy) |
| [42706194201](https://github.com/Devmraustro/ERP/actions/runs/42706194201) | `f603e38` | **all 5 jobs success** |

## 2. Divergence from the brief's stated baseline

| Expected by the brief | Actually measured here | Impact |
| :--- | :--- | :--- |
| Repository `C:\Users\rabdz\Documents\ERP` | Linux sandbox checkout of the same GitHub remote | The Windows working copy is **not reachable** from here |
| Branch `round7-dep-remediation` | Branch does not exist — locally or on the remote | Nothing to reconcile against; the branch is not published |
| HEAD `5a3e47ddfba2b789bcff71d6438db4927f5c7986` | Local HEAD is `4fc1364a…`; `5a3e47dd` is an ancestor of the PR #7 head, which has since advanced to `f603e38` | Any execution must pick a tree deliberately — see §4 |
| PR #7 open and unmerged | Correct: open, **draft**, unmerged | Confirmed, untouched |
| User-modified `dz_erp/database.py`, `dz_erp/desktop/ui.py`, `dz_erp/key_provider.py`, `tools/dr_worker.py`, `pyproject.toml`, `tests/test_phase17_key_providers.py`, `tests/test_phase27_desktop_ui.py` | **None of these files is modified in this checkout** | Those edits exist only on the user's machine. They cannot be verified, tested or preserved from here, and nothing in this environment can have lost them |
| `docs/release/` structure, `phase8/` deliverables, `docs/release/phase7_release_evidence/` | **`docs/release/` does not exist** — not on `main` (`4fc1364a`), not on PR head (`f603e38`), not anywhere in either tree | The Phase-8 audit artefacts (`GIT_RECONCILIATION_REPORT.md`, the 0-byte `SECURITY_CLOSURE_REPORT.md`, the five missing deliverables) are **absent**, not merely incomplete. They must be *created* from real execution, not "completed" |
| Phase-8 findings described as outstanding | No Phase-8 evidence exists to inherit | Phase 8 is executed in this session against the actual code (§5) |

Verification commands (reproducible):

```bash
gh api repos/Devmraustro/ERP/contents/docs/release?ref=f603e38206f938808997354ec2045d893ba47be7   # 404
gh api repos/Devmraustro/ERP/contents/docs?ref=f603e38206f938808997354ec2045d893ba47be7 | jq -r '.[].name'  # no "release"
git ls-remote --heads https://github.com/Devmraustro/ERP   # no round7-dep-remediation
```

## 3. Ignored files

`git status --ignored` in this checkout reports only build/runtime noise
(`__pycache__/`, `.pytest_cache/`); no database, key, backup or secret file is present in
either tree, so no production artefact can be touched by this work.

## 4. Tree chosen for execution, and why

All Phase-8/9 execution runs against a **fresh clone of PR #7's head** (`f603e38`) at
`/tmp/gate9`:

* it is the only tree that contains the integrated security state (`review_policy`,
  `rate_limit`, Host/proxy boundary) and the 64-test suite;
* `main` (`4fc1364a`) lacks those modules, so the security controls the brief asks to
  verify do not exist there — verifying them on `main` would be verifying nothing;
* the user's working tree at `/home/user/ERP` is never written to, so no user work can be
  disturbed.

The user's uncommitted files in `/home/user/ERP` remain exactly as found; this session
adds only documentation under `docs/release/` there, plus its own evidence directory.

## 5. Execution environment

| Component | Value |
| :--- | :--- |
| OS | Linux 6.1 (sandbox), 2 vCPU |
| Interpreter (target) | **CPython 3.12.10**, built from `codeload.github.com/python/cpython` tag `v3.12.10` because the standalone bundles and `python.org` are blocked by this environment's egress policy |
| Native deps | OpenSSL 3.0.15, zlib 1.3.1, SQLite 3.53.4 (amalgamation), libffi 3.4.6 headers + system `libffi.so.8` |
| Dependencies | `requirements.lock.hashes.txt` installed with `--no-deps --require-hashes` |
| Why not GitHub Actions directly | Running a workflow requires a push, and the brief forbids pushing without authorisation. CI **has** however run on the two commits above (see §1.4) and those runs are green |

## 6. What this baseline does not claim

* It does not claim the user's Windows-side edits are correct, tested or even consistent —
  they are simply not visible here.
* It does not claim `round7-dep-remediation` is stale; it claims it does not exist on the
  remote, so it cannot be inspected.
* It does not treat the green CI runs as Phase-8/9 evidence: those runs predate this
  session's Phase-8/9 work and cover the security commit only.
