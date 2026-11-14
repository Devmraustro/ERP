# Phase 62 — CI and Test Evidence Audit

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Rule applied | A green job proves the job's commands exited 0. It does not state how many tests ran. **No total below is inferred from a job conclusion.** |

## 1. What could and could not be read

| Source | Readable from this environment? |
|---|---|
| Run, job and step metadata (GitHub REST API) | Yes |
| Check annotations | Yes |
| Raw job logs (`gh run view --log`, `/actions/jobs/<id>/logs`) | **No** — the log host is outside the sandbox's network allow-list; the request ends in EOF |
| Uploaded artifacts (`reports-*`, containing JUnit XML) | **No** — same cause; downloads return 0 bytes |

Consequence: **the exact pass/fail/skip counts produced by CI itself are UNCONFIRMED.** They are
recorded as such in every table below. To close this, anyone with repository access can:

1. Open https://github.com/Devmraustro/ERP/actions/runs/43607302657
2. Download the four artifacts `reports-3.11`, `reports-3.12`, `reports-windows`, `reports-desktop`.
3. Read `tests`, `failures`, `errors`, `skipped` from the `<testsuite>` element of each JUnit file, and
   the `<skipped message=…>` reasons.
4. Record them in §3, replacing "unconfirmed".

## 2. CI run on the integration head

Run **43607302657** — event `pull_request` (PR #9) — branch `arena/63d762c8-erp` — head SHA
`5234bf2a785612e6ba9b9c31a8e0d5e700bf4635` — started 2026-11-14T14:54:34Z, completed 15:06:14Z —
conclusion `success`.

| Job | Job ID | Conclusion | Test step | Other gated steps |
|---|---|---|---|---|
| Python 3.11 (Ubuntu) | 132484609995 | success | full suite, 242 s | staging acceptance: success; risk-register consistency: success |
| Python 3.12 (Ubuntu) | 132484610034 | success | full suite, 673 s | staging acceptance and risk register: **skipped by design** (workflow gates them to 3.11) |
| Windows, Python 3.12.10 | 132484610041 | success | 10-file subset, 109 s | staging acceptance: success, 7 s |
| Desktop UI (Qt) | 132484609813 | success | Qt tests, 101 s | job is written to fail on any skipped or zero tests |

Annotations on the run: a Node.js 20 deprecation notice for actions and an `ubuntu-latest` image
notice. No test or lint annotation.

Artifacts present (not downloadable here): `reports-3.12` 14119645494, `reports-3.11` 14119160929,
`reports-windows` 14119146376, `reports-desktop` 14118516900.

Earlier runs on the same line: 43600447851 (`df546e2`, PR #10) and 43536589906 (`c03062f`) — all four
jobs `success`. Older: 43328400181 (`90f382a5`) `failure`; 43449745851 and 43338706506 `cancelled`.
Those three predate the commits under audit and were superseded by later green runs on later SHAs.

## 3. Counts

| Platform | CI's own count | Local re-run at the same SHA (`5234bf2`) |
|---|---|---|
| Python 3.11 Ubuntu | unconfirmed (logs unreachable) | CPython **3.11.2**: 985 passed, **22 failed**, 21 skipped — see §4 |
| Python 3.12 Ubuntu | unconfirmed | CPython **3.12.10**: **1007 passed, 0 failed, 21 skipped**, 272 s |
| Windows 3.12.10 | unconfirmed | Not reproducible (no Windows). The same 10 files on Linux 3.12.10: 153 passed, 3 skipped |
| Desktop Qt | unconfirmed | **Not run** — PySide6 cannot be installed here. 44 tests collect; 18 skip for the missing binding |

Local skip reasons (3.12.10, complete list): 18 × "PySide6 not installed", 3 × Windows-only tests.
Deselected: 0. Not run: the Qt tests, as stated.

Other local checks at `5234bf2`, both interpreters unless noted: staging acceptance **40/40**
(3.12.10); risk-register counts consistent; hash-lock check 53 distributions / 1317 hashes; desktop
lock 4 / 20; dependency-graph check ok; Ruff clean; `compileall` clean.

## 4. The 22 local failures on 3.11.2 are an interpreter limit, not a regression

Restore refuses to run below CPython 3.11.17 / 3.12.10 because older `tarfile` filters can be bypassed.
The sandbox's system Python is 3.11.2, so every test that performs a restore fails with that refusal.
CI's 3.11 job uses a current 3.11 patch release and is green. This is the intended behaviour and is
unchanged from Phase 59/60.

## 5. Re-run after this mission's fixes

Phases 63, 66 and 67 each found a real defect and fixed it with regression tests (four code changes in
three commits). The suite was re-run on the fixed tree:

| Tree | Interpreter | Result |
|---|---|---|
| `89a5fecede594e5e0f001fe2decad6379325964f` (release candidate) | CPython 3.12.10 | **1021 passed · 0 failed · 21 skipped (18 PySide6 not installed, 3 Windows-only)** |

CI has **not** yet run on that commit: the working branch has no pull request. Until it does, the four
CI jobs are evidence for `5234bf2` only, not for the release candidate.

## 6. Findings

| ID | Finding | Owner | Disposition |
|---|---|---|---|
| C62-1 | CI test counts cannot be confirmed from here | Release owner | Follow §1 once; paste the four JUnit totals into §3 |
| C62-2 | The 3.12 Ubuntu job does not run staging acceptance or the risk-register gate | CI owner | Accepted as designed (gated to 3.11 to avoid duplicate work); stated so nobody reads the 3.12 job as covering them |
| C62-3 | The Windows job runs a 10-file subset, not the full suite | CI owner | Open. Full-suite Windows evidence does not exist; Phase 64 cannot substitute for it |
| C62-4 | Release-candidate commit has no CI run yet | Release owner | Open a PR from `arena/7054a9f7-erp` to the integration branch; require all four jobs |
| C62-5 | Actions pinned to Node.js 20 runtime (deprecation annotation) | CI owner | Low; update action versions in a separate, reviewed change |
