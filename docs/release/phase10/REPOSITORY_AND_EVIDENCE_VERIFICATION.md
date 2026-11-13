# Phase 10 — Repository and Evidence Verification

**Executed:** 2026-11-10 · first action of Phase 10 (before any modification)

## 1. Repository state

| Item | Value |
| --- | --- |
| Working repository | `/home/user/ERP` |
| Current branch | `arena/7557bcfb-erp` (status: **active**) |
| HEAD | `4fc1364` — *"Merge pull request #2 from Devmraustro/arena/5c240e1d-erp"* |
| Other local branch | `main` @ `4fc1364` (identical commit) |
| Remote branches visible | `origin/main` @ `4fc1364`, `origin/HEAD → origin/main` |
| Clone | **shallow** — the local history stops at `4fc1364` |
| Pull requests acting on this tree | none |

`git branch -a` shows only `arena/7557bcfb-erp`, `main` and `origin/main`. There is no
local branch named `round7-dep-remediation` (the historical baseline named in the Phase
8 brief) and no Phase-9-specific branch: **the Phase 9 work never created a branch or a
commit** — it lives as uncommitted files under `docs/release/`, exactly as the Phase 9
constraints required. That is the "Phase 9 evidence branch": *this* branch, uncommitted.

## 2. Pull request #7 — unchanged

```
gh pr view 7 --json number,state,isDraft,mergeable,headRefName,headRefOid
{"number":7,"state":"OPEN","isDraft":true,"mergeable":"MERGEABLE",
 "headRefName":"arena/7f12d627-erp",
 "headRefOid":"f603e38206f938808997354ec2045d893ba47be7"}
```

PR #7 is **OPEN, draft, MERGEABLE**, head `f603e38` on `arena/7f12d627-erp`. Phase 10 did
not merge, close, convert, comment on, or push to it, and did not touch its source
branch. No commit, cherry-pick, or push was performed anywhere.

## 3. Phase 9 evidence — verified file by file

The Phase 9 package claimed **21 documents, 4 reproducible scripts, 17 evidence logs**.
Recounted now:

| Location | Documents | Scripts | Logs |
| --- | --- | --- | --- |
| `docs/release/` (top level) | 3 (`README.md`, `PHASE8_FINAL_V1_ACCEPTANCE_REPORT.md`, `FINAL_PRODUCTION_READINESS_REPORT.md`) | 0 | 0 |
| `docs/release/phase8/` | 6 | 1 (`evidence/tls_proxy_harness.py`) | 3 |
| `docs/release/phase9/` | 12 | 3 (`evidence/dr_drill.py`, `performance_probe.py`, `db_concurrency_restart.py`) | 14 |
| **Total** | **21** | **4** | **17** |

**The claim holds exactly.** Verified by `find … -name '*.md' | wc -l` (21), `-name
'*.py'` (4), `-name '*.log'` (17).

## 4. A material divergence found at the start of Phase 10

The Phase 9 execution environment lived in `/tmp` and **did not survive**: `/tmp/gate9`
(the clone of PR #7 head), `/tmp/py312` (CPython 3.12.10), `/tmp/v9` (the test venv) and
`/tmp/v10`/`/tmp/vdesk` are all gone. Only the workspace — and therefore the evidence
copied into `docs/release/` — persisted.

Consequences, handled explicitly:

1. **The Phase 9 evidence is intact and is the record of what was run.** Its logs are in
   `docs/release/phase8|9/evidence/logs/`.
2. **The PR #7 head tree (`f603e38`) is not reachable from this branch** (`git cat-file
   -t f603e38` → *not a valid object name*, shallow clone). It was not re-fetched:
   Phase 10 changes code, and code must be changed on the branch that is delivered, not
   on a detached copy of a PR head.
3. **Phase 10 therefore executes on the session branch `4fc1364` + the user's
   uncommitted work**, and its baseline differs from Phase 9's: this tree has **34 test
   modules** (Phase 9 measured 64 on the PR head) and 18 top-level `dz_erp` modules
   (the PR head additionally has `rate_limit.py`). Numbers from Phase 9 and Phase 10 are
   **not directly comparable** and are labelled with their baseline everywhere.
4. The environment was rebuilt from allow-listed sources: CPython 3.12.10 compiled from
   the `codeload` tarball, OpenSSL 3.0.15, zlib 1.3.1, the SQLite 3.53.4 amalgamation,
   and — because the image has no `libffi` headers and no `autoconf` — an `ffi.h`
   generated from libffi's own `ffi.h.in` (4 documented placeholder substitutions)
   linked against the distribution's `libffi.so.8`. `_ctypes`, `_ssl`, `sqlite3` and
   `zlib` were all import-verified before any test ran.

## 5. Working tree at the start (preserved, untouched)

```
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
?? docs/release/
?? docs/security/
?? tests/test_phase34_security_validation.py
```

The four modifications (+287/−6 lines, all additive) and the two untracked paths are the
user's. `docs/security/` and `tests/test_phase34_security_validation.py` were not read
into any decision, edited, moved or deleted. `docs/release/` is this work.

## 6. Everything Phase 10 added (all untracked, nothing committed)

| Kind | Paths |
| --- | --- |
| Readiness | `dz_erp/readiness.py` |
| Backup automation | `dz_erp/ops_backup.py`, `dz_erp/ops_alerts.py`, `dz_erp/ops_checks.py` |
| Route + contract | `dz_erp/api.py` (additive `/readyz` and its OpenAPI public declaration) |
| Tests | `tests/test_phase62_readiness.py`, `tests/test_phase62_backup_automation.py`, `tests/test_phase63_dependency_contract.py` |
| Test contract update | `tests/test_phase8_upload_boundary_and_ci.py` (public-route set now includes `/readyz`) |
| Ops assets | `ops/dz-erp-backup{,-verify}.{service,timer}`, `ops/dz-erp-readiness.{service,timer}`, `ops/dz-erp-disk-check.{service,timer}`, `ops/dz-erp-ocr-check.{service,timer}`, `ops/logrotate/dz-erp` |
| Evidence | `docs/release/phase10/evidence/{operational_drill.py,desktop_headless_acceptance.py}` + logs |
| Documents | `docs/release/phase10/*.md` (this file and 8 others) |

No existing file was deleted, renamed or reverted. The two edits to existing files are
additive and commented in place:
`dz_erp/api.py` gains one route and one entry in the OpenAPI public set;
`tests/test_phase8_upload_boundary_and_ci.py` gains `/readyz` in the two places where
the public set is enumerated.

## 7. Reproducibility of this verification

```bash
git -C /home/user/ERP status --porcelain
git -C /home/user/ERP branch -a --verbose
git -C /home/user/ERP log --oneline -1
gh pr view 7 --json number,state,isDraft,mergeable,headRefName,headRefOid
find docs/release -name '*.md' | wc -l   # 21 (pre-Phase 10)
find docs/release -name '*.py' | wc -l   # 4
find docs/release -name '*.log' | wc -l  # 17
```
