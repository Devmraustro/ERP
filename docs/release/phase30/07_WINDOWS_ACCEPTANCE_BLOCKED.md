# Phase 30 — Windows desktop acceptance: **BLOCKED**

## Status

**BLOCKED — not executed. No Windows result is claimed anywhere in this phase.**

The Phase 30 brief requires *actual application execution* on a supported Windows environment, and
explicitly forbids substituting compilation or screenshots from another platform. That substitution
was not performed.

## What was checked, and what it showed

| Probe | Result |
| --- | --- |
| Kernel / platform | `Linux e2b.local 6.1.158+ x86_64` — Debian GNU/Linux 12 |
| Windows filesystem mount (`/mnt/c`) | absent |
| `wine` / `wine64` | not installed |
| `apt-get install wine` (dry run) | `E: Unable to locate package wine` |
| Any Windows VM or remote host | none provided |
| Outbound access to a Windows CI runner | not available in this sandbox |

There is no path from this environment to a Windows GUI session.

## Consequence for the release decision

Windows desktop execution is a **mandatory gate** in the brief ("Successful supported-Windows
desktop execution"). It is unverified, so **`RELEASE ACCEPTED` cannot be declared** regardless of how
clean the Linux results are. The classification in `10_FINAL_RELEASE_REPORT.md` follows from that.

## Exactly what a Windows acceptance run must do

Run the following on a supported Windows host (Windows 10/11, Python 3.12.10 or 3.11.17+, the
`desktop` extra installed), against a **disposable** database and vault — never production data.

1. **Startup** — launch `dz_erp.desktop.app`; expect the main window, no console traceback.
2. **First run** — with an empty database path, expect the first-run bootstrap flow; create the
   company and admin; confirm the SCF chart (294 accounts) loads.
3. **Login** — wrong password is refused with a visible message; correct password signs in and the
   identity line shows `Utilisateur : <user> (<role>)`.
4. **Dashboard** — tiles show real counts; the ledger section shows totals or the empty state.
5. **Navigation** — visit all 9 views; no crash, no blank pane, header and table present.
6. **Resizing** — drag the window to 1024×700 and to full screen; no clipped controls.
7. **Dialogs** — open the invoice review dialog and the user-administration dialogs; close each;
   the parent window remains usable.
8. **Form validation** — submit the new-invoice form empty; expect field-level refusal, no crash.
9. **Search and filtering** — type into the list search; the row count updates and the message
   switches to "no match" for a miss.
10. **User administration** — create a user, deactivate it, confirm both are reflected after refresh.
11. **Role-based visibility** — sign in as a clerk; confirm the Users view is hidden and admin
    actions are unavailable.
12. **Invoice creation and review** — create, submit for approval, approve as a second user, post as
    an accountant; confirm the state badge follows the workflow and only the allowed actions are
    armed at each step.
13. **OCR correction** — select an extracted field and correct it; confirm the origin changes to
    "Vérifié" and the correction survives a refresh.
14. **Accounting and inventory views** — trial balance balances; stock and supplier screens list rows.
15. **Shutdown and restart** — close the app, relaunch, confirm the session state and the persisted
    theme/language preference.
16. **High DPI** — repeat steps 4–8 at Windows scaling 100 %, 125 %, 150 % and 200 %.
17. **Arabic RTL** — switch to العربية and repeat steps 4–8, 12 and 13.

Capture a screenshot per step and record: Windows build, Python version, display scaling, and the
exact commands used. Any deviation is a finding and must be logged.

## Prior art in this repository

`docs/release/phase8/WINDOWS_DESKTOP_ACCEPTANCE.md` and
`docs/release/phase10/WINDOWS_DESKTOP_ACCEPTANCE.md` already recorded this gate as NOT EXECUTED and
carry a PowerShell script (`phase10/windows_acceptance.ps1`). Phase 30 does not supersede them; it
re-confirms that the gate is still open.
