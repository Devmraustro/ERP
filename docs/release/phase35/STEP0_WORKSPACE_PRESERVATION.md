# Phase 35 — Step 0: environment identity and workspace preservation

**Status: PRESERVATION VERIFIED. No staging, no commit, no code change performed.**

## 1. Environment identity

| Item | Value |
| --- | --- |
| `pwd` | `/home/user/ERP` |
| `git rev-parse --show-toplevel` | `/home/user/ERP` |
| Branch | `arena/7557bcfb-erp` |
| HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` |
| Remote `origin` | `https://github.com/Devmraustro/ERP.git` (fetch + push) |
| Python (system) | 3.11.2 — **below `requires-python`** (`>=3.11.17,<3.13`) |
| Supported interpreter | CPython **3.12.10**, built from source for this phase |
| OS | Debian GNU/Linux 12 (bookworm), kernel 6.1.158+ x86_64 |
| Active venv | none system-wide; purpose-built venvs listed below |
| Privileges | unprivileged user, no package manager, no Windows host |

`pyproject.toml` declares `requires-python = ">=3.11.17,<3.13,!=3.12.0..!=3.12.9"`. The system
interpreter (3.11.2) is therefore **not** a supported runtime, and the 22 baseline failures on it are
correct behaviour of the restore guard, not defects.

### Configured test commands

```bash
# supported runtime
LD_LIBRARY_PATH=/tmp/zlibbuild/lib /tmp/py312/bin/python3 -m pytest tests/ -q
# GUI suites (Qt offscreen)
QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/qstub PYTHONPATH=/home/user/ERP /tmp/vui/bin/python -m pytest tests/ -q
# static
/tmp/vui/bin/python -m ruff check dz_erp/ tests/
/tmp/vui/bin/python -m compileall -q dz_erp tests
```

### Deployment commands present in the repository

Systemd units under `ops/` (backup, backup-verify, checkpoint-export, checkpoint-verify, disk-check,
ocr-check, readiness), `ops/logrotate/`, plus `dz_erp/serve.py` as the application entry point.
**No deployment target is configured in this environment** — no host, no credentials, no TLS
material, no domain. Deployment is therefore out of scope for execution (§ Phase 44) and will be
reported BLOCKED with prerequisites.

## 2. Object identity (verified locally AND against the remote)

Objects were **absent** from this clone at start (fresh container) and were fetched from
`origin`. Each was then re-verified:

| Object | SHA | Verified |
| --- | --- | --- |
| `2427fda` | `2427fda1c28d1b5047c622325e3b4f7221995ee3` | present, 2026-11-09 20:46 |
| `f603e38` | `f603e38206f938808997354ec2045d893ba47be7` | present, 2026-11-09 21:49 |
| `5a3e47dd` | `5a3e47ddfba2b789bcff71d6438db4927f5c7986` | present, 2026-11-07 00:53 |
| `4fc1364` (HEAD, base of this branch) | `4fc1364a9504fab1153ea44e753f354f00023428` | present |
| PR #7 head branch | `arena/7f12d627-erp` → `f603e38` | matches `git ls-remote` |

### ⚠ Branch-topology finding that changes the merge plan

**PR #7's base is not `main`.** Verified from the GitHub API:

| Field | Value |
| --- | --- |
| PR #7 base | `fix/phase29-invoice-discount-and-line-net-consistency` |
| PR #7 head | `arena/7f12d627-erp` |
| State | OPEN, **DRAFT** |
| Mergeable | MERGEABLE |
| URL | https://github.com/Devmraustro/ERP/pull/7 |

PR #7 sits on top of a **five-deep stack**, every layer of which is a strict ancestor of `f603e38`:

```
main (4fc1364)
 └─ phase29/ocr-failclosed                       (+1 commit, PR #3)
     └─ fix/phase29-source-invoice-consistency  (+6, PR #4)
         └─ fix/phase29-invoice-line-price-consistency (+7, PR #5)
             └─ fix/phase29-invoice-discount-and-line-net-consistency (+8, PR #6)
                 └─ arena/7f12d627-erp      (+108, PR #7)
```

Consequences recorded now, because they govern Phase 43/44:

1. "Merge PR #7" alone does **not** deliver the integrated code to `main`; PRs #3–#6 must land first.
2. PR #7 is a **draft**, so it cannot be merged until marked ready for review.
3. This branch (`arena/7557bcfb-erp`) is based on `main` and contains **0 commits**; all of its work
   is uncommitted. Its final destination must be decided against this stack, not against `main`
   alone.

No ambiguity remains about *which* codebases are present: both are verified and match Phase 34's
records.

## 3. Workspace state as found

| | Count |
| --- | --- |
| Modified tracked files | **16** |
| Untracked paths (porcelain entries) | **34** directories/entries |
| Untracked **files** (recursive) | **266** |
| Staged | **0** |

Untracked composition: 156 files under `docs/release/`, 78 under `docs/security/`, 11 desktop
modules, 6 test files, 10 systemd units + `ops/logrotate/`, 4 ops modules (`ops_alerts.py`,
`ops_backup.py`, `ops_checks.py`, `readiness.py`).

**Every file is accounted for.** No anomalies, no unexpected binaries, no databases, no virtual
environments, no credential files. The only "secret"-named path is
`docs/security/phase5_release_gate/logs/secret_scan.log`, which is a scan *report*, not a secret.

## 4. External backup (outside the Git repository)

Location: **`/home/user/phase35-backup`** (11 MB, outside `/home/user/ERP`)

| Artefact | sha256 |
| --- | --- |
| `untracked/untracked_files.tar` (266 files, paths preserved) | `955ccb9119d480aa928a5929da01b06664f533b4a021e8dddf20b7f6baf8c815` |
| `evidence/docs_release.tar.gz` | `3c42baa4d33a43d1663672bc7b74baff9af370a2423c0596de2d65c8722f40d3` |
| `evidence/docs_security.tar.gz` | `6332dde16ce7c2eb979f906c39fde4c4cbd9d67329110c79460b7f4815865ebb` |
| `meta/modified_tracked.diff` (16 files) | `09ae338a91766c794fdeabc6f80cc89774557a491ab80e0b3cf0e9cdf34e2d2c` |
| `meta/staged.diff` (empty — nothing was staged) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Also captured: `meta/modified_file_list.txt`, `meta/untracked_file_list.txt`,
`meta/git_status_porcelain.txt`, `meta/repo_identity.txt`, and full copies of all 16 modified
tracked files under `tracked_mods/`.

## 5. Backup verification (restore + hash compare)

Restored into a **separate temporary directory** (`/tmp/p35verify`) by extracting
`untracked_files.tar` and overlaying `tracked_mods/`, then comparing every manifest entry:

```
MANIFEST.sha256 entries : 282
restored files          : 282
VERIFIED OK             : 282
BAD / MISSING           : 0
```

A first verification pass reported 15 false "MISSING" rows; the cause was the **manifest's own
column width truncating long paths**, not the backup. The manifest was regenerated in
`sha256␠␠size␠␠path` form (path last, never truncated) and the re-run reported 282/282 clean. The
defect was in the report format, and it is recorded here rather than quietly fixed.

## 6. Evidence preservation

`docs/release/phase8, phase9, phase10, phase29, phase30, phase34` are untouched and additionally
archived to the backup. `docs/release/phase29/PHASE29_DESKTOP_REDESIGN_REPORT.md` retains
SHA-256 `712501d1df3c34e6…`. **STATUS: PRESERVED — 282/282 files verified.**

## 7. Step 0 gate result

| STOP condition | Result |
| --- | --- |
| Backup cannot be verified | **PASS** — 282/282 |
| Repository or target branch ambiguous | **PASS** — remote verified; base topology resolved (see §2) |
| A user modification cannot be accounted for | **PASS** — 16 modified + 266 untracked, all enumerated |
| The two codebases are not the expected codebases | **PASS** — SHAs match Phase 34 records |

**Proceeding to Phase 36.**
