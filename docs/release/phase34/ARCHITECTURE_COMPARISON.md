# Phase 34 — Architecture comparison

## 1. Branch topology (measured)

```
4fc1364 (main, = workspace base) ──────────────── 107 commits ────────────────► f603e38 (PR #7 head)
                                                                                  │
                                                                              2427fda (security)
                                                                                  │
                                                                              5a3e47dd
```

- `git merge-base HEAD 2427fda` → **`4fc1364`**
- `git rev-list --count HEAD..2427fda` → **107** (PR #7 is ahead)
- `git rev-list --count 2427fda..HEAD` → **0** (the branch has **no commits**; all its work is in
  the working tree)

**This is the single most important architectural fact.** The workspace branch has never committed.
All of its work — the entire desktop redesign, the Phase 29/30 evidence, the dependency
remediation — exists only as **16 modified tracked files and 34 untracked paths**. `2427fda` is a
descendant of `main`, so every commit on it is a *sibling* of this work, not a competitor.

`f603e38` is `2427fda` plus exactly one docs commit (`docs(security): record the repository CI run`).

## 2. Python package structure

| | Workspace | PR #7 |
| --- | --- | --- |
| `dz_erp/` modules tracked in git | 29 | **38** |
| Untracked in `dz_erp/desktop/` | **9 modules** | — |
| Test files | 37 (6 untracked) | **62** |

Modules in PR #7 that the workspace does not have at all:

```
alerting.py  backup_storage.py  backup_storage_s3.py  dr_metrics.py
generation_resolver.py  rate_limit.py  request_id.py
restore_generation.py  review_policy.py
```

Modules in the workspace that PR #7 does not have:

```
desktop/tokens.py  desktop/theme.py  desktop/components.py  desktop/shell.py
desktop/formatting.py  desktop/icons.py  desktop/views_dashboard.py
desktop/views_data.py  desktop/views_invoices.py  desktop/views_users.py
desktop/workers.py
```

**The two sets are disjoint.** No module name collides. This is the best possible starting position
for integration: nothing has to be reconciled, only combined.

## 3. Desktop architecture and PySide6

| | Workspace | PR #7 |
| --- | --- | --- |
| `dz_erp/desktop/ui.py` | 760 lines (shell after redesign) | 1,277 lines (monolith) |
| Design tokens | `tokens.py` 950 lines, 2 palettes | none |
| Theming | `theme.py` 139 lines, QSS from tokens | inline `setStyleSheet("color: #b00020;")` ×4 |
| Shared widgets | `components.py` 1,148 lines | none |
| Views | 4 modules, 2,000+ lines | inline in `ui.py` |
| i18n | 245 keys × fr/ar/en, RTL-aware | present in `i18n.py` |
| Accessibility | contrast-checked palettes, tab-order fixed | Qt defaults |

PR #7's desktop is the *pre-Phase-29* application. It has `#b00020` error text hardcoded in four
places and no dark theme — which is exactly what the Phase 32 audit described, and which stops being
true the moment the workspace's desktop is used.

**PySide6 integration:** identical dependency (`PySide6-Essentials==6.11.2` in both lockfiles),
identical `QT_QPA_PLATFORM` handling, identical embedded-service (`service.py`) loopback design. The
workspace's `api_client.py` is unchanged from `main`, which PR #7 also has.

**Verdict: the desktop layers are compatible by construction because PR #7 never entered that
directory.**

## 4. Database schema and migrations

Both use `dz_erp/database.py` with the same migration mechanism and SQLite backend. PR #7's schema is
a superset: it carries forward migrations for rate limiting, request IDs, DR generation pointers and
review fingerprints.

| Direction | Supported? |
| --- | --- |
| workspace DB → PR #7 | **yes** — forward migrations exist |
| PR #7 DB → workspace | **no** — unknown tables/columns |

Both require SQLite ≥ 3.35 for partial indexes and JSON functions; I built 3.45.3 for the PR run and
the workspace's existing evidence already assumed a modern SQLite.

## 5. Service boundaries

```
              ┌─────────────────────────┐
   desktop ──►│  FastAPI app (api.py)   │──► ERPDatabase (SQLite)
   (httpx)    │  auth / rbac / audit    │──► PrivateDocumentVault
              └─────────────────────────┘        │
                                                 └── key_rotation / key_provider
```

Identical on both branches. PR #7 adds middleware layers (host validation, request-id, rate limit)
*inside* the same boundary; the workspace adds none. No boundary is redefined, so adding these
middleware layers to the workspace's app does not require an architectural change.

## 6. Configuration and environment variables

PR #7 introduces: `ERP_ALLOWED_HOSTS`, `ERP_PASSWORD_MIN_LENGTH`, `ERP_RATE_LIMIT_BACKEND`,
`ERP_RPO_TARGET_SECONDS`, `ERP_RTO_TARGET_SECONDS`, `ERP_BACKUP_ENCRYPTION_KEY` (documented in
`ops/erp.env.example`, +38 lines).

The workspace documents: `ERP_DB_PATH`, `ERP_VAULT_PATH`, `ERP_ALLOWED_HOSTS` (in Phase 8/10
*evidence documents* only — **not implemented in code**), `ERP_TRUSTED_PROXIES`.

**Discrepancy worth flagging:** the workspace's Phase 8 and Phase 10 evidence documents assert that
`ERP_ALLOWED_HOSTS` is enforced ("default deny of unknown hosts", "trusted-host middleware" in
`dz_erp/api.py`). **It is not.** There is no such middleware in the workspace's `api.py`. Those
documents describe an intent that was never landed here. This is exactly the class of error the
Phase 32 audit was built to catch, and it argues for treating prior evidence claims as unverified
until re-executed.

## 7. Dependency versions

| | Workspace | PR #7 |
| --- | --- | --- |
| Lockfile | `requirements.lock.hashes.txt` | `requirements.lock.hashes.txt` |
| Pinned packages | 53 | 53 |
| Hash-pinned install | yes | yes |
| Advisories | 0 | 0 |
| PySide6 | 6.11.2 | 6.11.2 |
| Python floor | 3.11.2 (tested), 3.11.17/3.12.10 for restore | same guard |

**No dependency divergence.** Both apply the same guard
(`Safe backup restore requires Python 3.11.17+ or Python 3.12.10+`).

## 8. Test infrastructure

| | Workspace | PR #7 |
| --- | --- | --- |
| Test files | 37 | 62 |
| Tests (3.11.2) | 838 passed / 22 failed / 3 skipped | 1340 passed / 20 failed / 19 skipped |
| Tests (3.12.10) | 835 passed / 0 failed / 20 skipped | **1373 passed / 0 failed / 6 skipped** |
| Desktop GUI tests | ~49 | none |

Both use `pytest` + `pytest-asyncio` with `asyncio_mode = auto`. The workspace adds GUI suites PR #7
cannot run (no Qt).

### Verification environment built for this assessment

To run PR #7's suite honestly I had to construct a genuinely supported interpreter, because the
sandbox has no 3.12.10 and no package manager. Recorded for reproducibility:

| Component | How obtained |
| --- | --- |
| CPython 3.12.10 | built from `codeload.github.com/python/cpython` tag `v3.12.10` |
| zlib | built from `madler/zlib` `v1.3.1` |
| sqlite3 **3.45.3** | amalgamation **generated without tclsh** by porting `mkopcodeh.tcl`, `mkopcodec.tcl`, `mksqlite3c.tcl` and `mksqlite3h.tcl` to Python; compiled and linked as `_sqlite3` |
| `_ssl` | compiled against OpenSSL 3.0.20 headers (`make build_generated`) + system `libssl.so.3` |
| `_hashlib` | compiled against the same OpenSSL (this is what provides `hashlib.pbkdf2_hmac`) |
| `_ctypes` | compiled against system `libffi.so.8` with a **minimal `ffi.h`**; `ffi_abi` must be `WIN64=1, UNIX64=2` and the legacy `ffi_type_s*` aliases supplied locally |
| project deps | the PR's own 53-pin lockfile, `--require-hashes` |

No application code was modified to make any of this work. This matters: the "1373 passed" claim was
verified on a *real* supported runtime, not accepted on faith.

## 9. Packaging and deployment

Both build a wheel; PR #7 adds `docs/security/PACKAGING_DECISION_V1.md` and records **B2 open — the
wheel is not a V1 delivery artefact**. Both ship a Dockerfile; PR #7's has CVE-driven purges
(`python3-setuptools`, `libxml2`).

## 10. Can the two implementations coexist safely?

**Yes — and unusually cleanly**, because:

1. The module sets are **disjoint** (no filename collides).
2. PR #7 **never touched `dz_erp/desktop/`**, so the redesign cannot conflict with it.
3. The five overlapping files (`api.py`, `decimal_utils.py`, `intake_ocr.py`,
   `procurement_stock.py`, `security_audit.py`) have **non-overlapping hunks** where both changed
   them — verified by hunk-header comparison — with the caveat that `procurement_stock.py` differs
   by 1,799 lines of *base* content, not by conflicting edits.
4. No dependency, schema or interface divergence was found.

The risk is not conflict. The risk is that the workspace's work is **uncommitted**, so a naive
`git checkout`/`stash`/`clean` anywhere near this tree destroys it. That is why this phase was run
read-only and why the recommended sequence starts with preserving the tree.
