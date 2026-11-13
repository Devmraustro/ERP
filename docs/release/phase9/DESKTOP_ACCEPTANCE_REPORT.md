# Phase 9H — Desktop / UX Acceptance

**Status: NOT EXECUTED (environment cannot run Qt).** This report is a repeat of the
Phase 8D finding with the Phase 9 requirement list mapped explicitly, so that no row is
silently skipped.

## 1. What blocks it

| Attempt | Outcome |
| --- | --- |
| Isolated venv `/tmp/vdesk` | created |
| `pip install PySide6` (hash-pinned) | **PySide6 6.12.0 installed successfully** |
| `import PySide6.QtWidgets` | **ImportError: libGL.so.1: cannot open shared object file** |
| `find / -name 'libGL.so*' -o -name 'libEGL.so*' -o -name 'libGLX*'` | no match anywhere on the image |
| `libgl` / `libglvnd` on PyPI | 404 — not obtainable from an allow-listed source |
| `mesa` on PyPI | an unrelated ABM simulation package, **not** Mesa 3D — installing it would not help and would be misleading |
| Windows host | unavailable in this sandbox |

Result of running the desktop test modules with `QT_QPA_PLATFORM=offscreen`:
**9 passed, 3 skipped**. The skips are the modules guarded by
`pytest.importorskip("PySide6.QtWidgets")`, i.e. the ones that would actually create
widgets. They are recorded as **skips** (which is what pytest reported) and the desktop
acceptance is recorded as **NOT EXECUTED** (which is what is true). The two labels are
kept separate on purpose.

## 2. Requirement-by-requirement status

| # | Requirement | Status |
| --- | --- | --- |
| 1 | Application startup | NOT EXECUTED |
| 2 | Login | NOT EXECUTED in GUI (API-side login executed: `200`, token `dzsess_…`) |
| 3 | Logout | NOT EXECUTED |
| 4 | Session expiry | NOT EXECUTED in GUI (server `expires_at` enforced and asserted) |
| 5 | RBAC navigation | NOT EXECUTED in GUI (server-side `403` for clerk shown executed) |
| 6 | Arabic UI / RTL | NOT EXECUTED — no rendering, therefore no Arabic shaping evidence |
| 7 | French / English switching | NOT EXECUTED in GUI (translation helpers covered by the 9 passing tests) |
| 8 | Invoice intake | NOT EXECUTED in GUI (API intake executed end-to-end) |
| 9 | Review + OCR correction | NOT EXECUTED in GUI (review policy: 48 tests passed) |
| 10 | Posting from the UI | NOT EXECUTED in GUI (API posting executed with approval + idempotency) |
| 11 | API connectivity failure handling | NOT EXECUTED |
| 12 | Error handling / no traceback leak | NOT EXECUTED in GUI (API error shapes asserted) |
| 13 | Auth boundaries | NOT EXECUTED in GUI (server boundary executed) |

## 3. What the 9 passing tests do prove

They exercise the non-widget layers: state model, translation catalogue, RTL helper
metadata, and the review-API client logic. That is real coverage of *logic*, and it is
not evidence of *UI acceptance*.

## 4. Exact procedure to close this gate (Windows 10/11 x64, CPython 3.12.10)

```powershell
py -3.12 -m venv C:\erp-desk
C:\erp-desk\Scripts\python -m pip install --require-hashes -r requirements.lock.hashes.txt
C:\erp-desk\Scripts\python -m pip install --require-hashes -r requirements-desktop.lock.hashes.txt
C:\erp-desk\Scripts\python -m pytest tests\test_phase26_desktop.py tests\test_phase27_desktop_ui.py `
    tests\test_phase27_review_api.py tests\test_phase28_desktop_users_ui.py -v
```

Then run the client with `ERP_API_BASE_URL=https://<proxy host>` and walk rows 1–13,
recording one screenshot or log line per row. Arabic must be checked visually: a
right-to-left layout that renders correctly in code can still clip shaped glyphs.

## 5. Consequence for the release verdict

Two possibilities, both honest:

- ship as an **API-first** V1 (server + Local File Vault + `python -m dz_erp.serve`),
  stating that the desktop client is **unverified**; or
- block release until the Windows pass above is done.

The verdict in the final report takes the first position and says
**CONDITIONALLY PRODUCTION READY** rather than implying the desktop client was accepted.
