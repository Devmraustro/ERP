# Phase 30 — Git baseline (recorded before any action)

Captured at the start of Phase 30, before any command that writes to the workspace.

## Identity

| Field | Value |
| --- | --- |
| Branch | `arena/7557bcfb-erp` |
| HEAD SHA | `4fc1364a9504fab1153ea44e753f354f00023428` |
| HEAD subject | `Merge pull request #2 from Devmraustro/arena/5c240e1d-erp` |
| Upstream | none configured (no remote tracking branch) |
| Merge base with `main` | `4fc1364` — the branch was forked from this exact commit |

## Working tree

**Nothing staged.** 16 tracked files modified, 34 untracked paths, 0 deletions.

### Modified (tracked)

```
 dz_erp/api.py                                   |  163 +++
 dz_erp/decimal_utils.py                         |   37 +-
 dz_erp/desktop/app.py                           |   49 +-
 dz_erp/desktop/config.py                        |   11 +-
 dz_erp/desktop/i18n.py                          | 1170 +++++++++++++-----
 dz_erp/desktop/ui.py                            | 1437 ++++++++---------------
 dz_erp/intake_ocr.py                            |   26 +-
 dz_erp/procurement_stock.py                     |   67 +-
 dz_erp/security_audit.py                        |   67 +-
 pyproject.toml                                  |   19 +-
 requirements.lock.hashes.txt                    |  306 ++---
 requirements.lock.txt                           |   42 +-
 tests/test_phase19_dependency_integrity.py      |    5 +-
 tests/test_phase26_desktop.py                   |    9 +-
 tests/test_phase7_authentication_remediation.py |    9 +-
 tests/test_phase8_upload_boundary_and_ci.py     |    7 +-
 16 files changed, 1993 insertions(+), 1431 deletions(-)
```

### Untracked

```
docs/release/            docs/security/
dz_erp/desktop/{components,formatting,icons,shell,theme,tokens,
                views_dashboard,views_data,views_invoices,views_users,workers}.py
dz_erp/{ops_alerts,ops_backup,ops_checks,readiness}.py
ops/{dz-erp-backup,dz-erp-backup-verify,dz-erp-disk-check,dz-erp-ocr-check,
     dz-erp-readiness}.{service,timer}   ops/logrotate/
tests/test_phase29_desktop_redesign.py
tests/test_phase34_security_validation.py
tests/test_phase62_backup_automation.py
tests/test_phase62_readiness.py
tests/test_phase63_dependency_contract.py
tests/test_phase64_dependency_security_regression.py
```

## Interpretation

The working tree carries the accumulated work of Phases 8–29 on top of `4fc1364`: the desktop
redesign, the operational-readiness modules, the dependency remediation, and their evidence
directories. **This is the user's work and it is preserved as found.** Phase 30 adds files only
under `docs/release/phase30/` and makes no commit, push, merge, rebase, reset, clean, or branch
change.

## Environment (this sandbox)

| Field | Value |
| --- | --- |
| OS | Debian GNU/Linux 12 (bookworm), kernel 6.1.158+, x86_64 |
| System Python | CPython **3.11.2** (main, Apr 8 2026) [GCC 12.2.0] |
| Display | headless; `QT_QPA_PLATFORM=offscreen` |
| Windows host | **none available in this sandbox** |

## Phase 29 evidence — preserved, not modified

`docs/release/phase29/` contains `PHASE29_DESKTOP_REDESIGN_REPORT.md` and
`screenshots/01…20.png` (20 files, 1.9 MB). Phase 30 writes only to `docs/release/phase30/`;
no Phase 29 file is opened for writing.
