# Windows Desktop Acceptance — Phase 8D

**Status: NOT EXECUTED (environment blocked).** No claim of desktop acceptance is made.

## 1. What was attempted

The desktop client is PySide6/Qt. The plan was to run the three desktop test modules
in an isolated environment and then exercise the acceptance checklist by hand.

| Step | Result |
| --- | --- |
| Isolated venv for the desktop stack | created (`/tmp/vdesk`) |
| `pip install PySide6` (hash-pinned, isolated) | **success — PySide6 6.12.0** |
| Import `PySide6.QtWidgets` | **ImportError: libGL.so.1: cannot open shared object file** |
| Desktop tests with `QT_QPA_PLATFORM=offscreen` | **9 passed, 3 skipped** |
| Locate `libGL.so*` / `libEGL.so*` / `libGLX*` on the image | none present anywhere (`find /`) |
| Install the OpenGL runtime from an allow-listed source | **impossible** — `libgl` and `libglvnd` are 404 on PyPI; `mesa` on PyPI is an unrelated ABM package, not Mesa 3D |
| Windows host | **unavailable** in this sandbox |

The three skips are exactly the modules that call
`pytest.importorskip("PySide6.QtWidgets")`: `test_phase26_desktop`,
`test_phase27_review_api`, `test_phase28_…`. They are **skips caused by a missing
native library**, and they are reported as skips of the *test run* while the desktop
acceptance itself is reported as **NOT EXECUTED**. They are not counted as passes and
are not relabelled.

## 2. What the passing 9 tests do and do not prove

They prove that the non-GUI logic around the desktop client (state, view-model,
translation and RTL helpers that do not touch `QtWidgets`) behaves as specified. They
prove **nothing** about window creation, rendering, Arabic shaping, or input handling.

## 3. Exact procedure to complete this acceptance on Windows

Prerequisites: Windows 10/11 x64, CPython 3.12.10, network access to PyPI.

```powershell
py -3.12 -m venv C:\erp-desk
C:\erp-desk\Scripts\python -m pip install --require-hashes -r requirements.lock.hashes.txt
C:\erp-desk\Scripts\python -m pip install --require-hashes -r requirements-desktop.lock.hashes.txt
C:\erp-desk\Scripts\python -m pytest tests\test_phase26_desktop.py tests\test_phase27_review_api.py tests\test_phase28_*.py -v
```

Then, with the API running (`python -m dz_erp.serve`) and `ERP_API_BASE_URL` pointing
at the HTTPS proxy from `REVERSE_PROXY_TLS_ACCEPTANCE.md`, walk the checklist and
record a screenshot or log line per row:

| # | Check | Expected |
| --- | --- | --- |
| 1 | Application startup | window opens, `/healthz` green in the status bar |
| 2 | Login (valid) | token obtained, role shown |
| 3 | Login (invalid) | error message, no token, no stack trace |
| 4 | Logout | token cleared; previous screen inaccessible |
| 5 | Session expiry | after `expires_at`, next action re-prompts for login (no silent reuse) |
| 6 | RBAC navigation | clerk cannot see accountant-only posting actions; direct call refused |
| 7 | Arabic UI, RTL | labels right-aligned, direction RTL, no clipped glyphs |
| 8 | French / English switching | all visible strings change; no untranslated key leaks |
| 9 | Invoice intake (PDF/photo) | document uploaded to the vault, fields extracted |
| 10 | Review screen | extracted fields editable, originals viewable |
| 11 | OCR correction | correction persisted, linked to the original document |
| 12 | Posting | allowed only for an authorised role, requires approval |
| 13 | API connectivity failure | "server unreachable" state, no crash, retry available |
| 14 | Server-side error handling | message shown, request id visible, no traceback |
| 15 | Auth boundary | an expired/forged token produces a re-login prompt, not a crash |

## 4. Why this is not a blocker for the Phase 8 verdict

The desktop client is not on the critical path for the server-side V1 deployment
documented in the V1 architecture (source-tree deploy, `python -m dz_erp.serve`,
Local File Vault). It **is** a blocker for any statement of the form "the desktop
client is accepted". The Phase 8 verdict therefore excludes desktop acceptance
explicitly rather than assuming it.
