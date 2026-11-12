# Phase 10 — Windows Desktop Acceptance

**Status: PARTIALLY EXECUTED — GUI rows NOT EXECUTED.**
Executed here (headless, no screen needed): startup, login, session identity, logout
revocation, invalid credentials, API connectivity, translation catalogue, RTL metadata.
Not executed: everything that needs a rendered window, plus one **finding**: English is
not implemented.

Evidence: `docs/release/phase10/evidence/desktop_headless_acceptance.py` →
`evidence/logs/desktop_headless.log` (8 EXECUTED, 1 GAP, 9 NOT EXECUTED, 0 FAILED) and
`dz_erp.desktop --smoke` (exit 0).

## 1. Why the GUI cannot run in this environment

| Attempt | Outcome |
| --- | --- |
| `pip install PySide6==6.11.2` (the pin in `pyproject.toml`) | installs |
| `import PySide6.QtWidgets` | `ImportError: libGL.so.1: cannot open shared object file` |
| `find / -name 'libGL.so*'` | nothing on the image |
| `libgl` / `libglvnd` on PyPI | 404 (not obtainable from an allow-listed source) |
| Windows host | none in this sandbox |

`QT_QPA_PLATFORM=offscreen` does not help: the failure is the native library, not the
display server.

## 2. What was actually executed (headless)

`docs/release/phase10/evidence/desktop_headless_acceptance.py` boots the desktop
client's **embedded Mode A service** — the same `serve.build_app` a deployment uses — on
an ephemeral loopback port and drives the real `ErpClient`:

| Row | Status | Observed |
| --- | --- | --- |
| Startup (embedded service, Mode A) | **EXECUTED** | bound `http://127.0.0.1:<ephemeral>`, first run created 294 SCF accounts |
| Login | **EXECUTED** | `dzsess_…` token; response keys `access_token, expires_at, expires_in, token_id, token_type, user` |
| API connectivity (authenticated reads) | **EXECUTED** | accounts list + trial balance returned |
| Session identity | **EXECUTED** | `user_id=1 company_id=1 role=admin`, server-derived |
| Logout invalidates the session | **EXECUTED** | revoked token replayed → **HTTP 401** `Authentication credential has been revoked` |
| Client drops the token | **EXECUTED** | `is_logged_in=False` |
| Invalid credentials | **EXECUTED** | typed `ErpApiError` 401, no traceback |
| Translation catalogue completeness | **EXECUTED** | 126 keys, `fr` = `ar`, **no missing key** in either |
| RTL metadata | **EXECUTED** | `is_rtl('ar')=True`, `is_rtl('fr')=False` |
| **English language support** | **GAP** | `available_languages() == ('fr', 'ar')` — **English does not exist** |

Plus `python -m dz_erp.desktop --smoke`: **exit 0** — headless start → login → read →
stop, printing `accounts=294`, `role=admin`.

### The English finding, stated plainly

The brief asks for "French/English switching". The client ships **French and Arabic**
only: the catalogue has two languages and no `en` entries. Therefore:

- "FR/AR switching" is implemented (metadata verified, rendering not).
- **"FR/EN switching" cannot be accepted — English is not implemented.**
- Adding English is a translation task plus a catalogue entry (`i18n._STRINGS["en"]`,
  126 keys), not a code change; it is **not** done here because inventing translations
  would be worse than declaring the gap.

## 3. Rows that require a human at a screen (NOT EXECUTED)

| # | Row | Why it needs a screen |
| --- | --- | --- |
| 1 | Window construction | a `QMainWindow` must be created and shown |
| 2 | Arabic RTL rendering | shaped glyphs, alignment, clipping are visual properties |
| 3 | Language switching in the running UI | strings must change after a live switch |
| 4 | Session expiry prompt | the UI must re-prompt, not silently reuse |
| 5 | Role-based navigation | menus must hide accountant-only actions for a clerk |
| 6 | Invoice intake through the UI | file dialog, upload, progress |
| 7 | OCR correction in the review screen | human edits a field and sees the original |
| 8 | Invoice posting from the UI | approval + posting buttons, confirmation |
| 9 | Error surfacing | server down → actionable message, no traceback |

## 4. Reproducible Windows procedure

Prerequisites: Windows 10/11 x64, CPython **3.12.10** (V1 baseline), network to PyPI.

```powershell
git clone https://github.com/Devmraustro/ERP C:\erp
cd C:\erp
powershell -ExecutionPolicy Bypass -File docs\release\phase10\windows_acceptance.ps1
```

The script (`windows_acceptance.ps1`) does, without asking:

1. verifies CPython 3.12;
2. creates an isolated venv and installs `requirements.lock.hashes.txt` with
   `--require-hashes` (no unverified wheels);
3. installs `PySide6==6.11.2` (the pin in `pyproject.toml`);
4. runs the three desktop test modules (they **stop skipping** once Qt works);
5. runs `dz_erp.desktop --smoke`;
6. builds the real window **offscreen** and switches it to RTL;
7. prints the nine manual rows and writes `acceptance.json` into `%TEMP%`.

Manual rows are recorded in that JSON as `MANUAL`, so a reviewer can see what a human
must still confirm.

## 5. What this report does not claim

It does **not** claim desktop acceptance. It claims: the client's non-GUI path works on
Linux today; the GUI is untested because the environment cannot load the toolkit; and
English is missing. Desktop acceptance remains **NOT EXECUTED** for the GUI rows, with
a one-command procedure to close it.
