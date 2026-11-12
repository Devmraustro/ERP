# Phase 30 — Desktop acceptance and release gate

## Classification: **CONDITIONALLY ACCEPTED**

Not `RELEASE ACCEPTED`: the brief makes a successful supported-**Windows** desktop execution a
mandatory gate, and no Windows environment exists in this sandbox (`07_WINDOWS_ACCEPTANCE_BLOCKED.md`).
Not `NOT ACCEPTED`: everything that *could* be verified here passes, the 22-failure baseline is
proven environmental, the restore drill now passes on a genuinely supported interpreter, and no
critical or high-severity defect remains unresolved in the code under review.

| Gate | Required | Result |
| --- | --- | --- |
| Zero new regressions | mandatory | **MET** — 22 failed before, 22 after, identical set |
| Supported-Windows desktop execution | mandatory | **BLOCKED** — no Windows host |
| Essential Arabic RTL workflows | mandatory | **MET** — 18/18 structural + visual review |
| No unresolved critical/high defects | mandatory | **MET** for code; P-3 (Windows) remains an open high gate |
| Isolated restore verification | mandatory | **MET** — 25/25 on CPython 3.12.10 |
| Evidence for all mandatory gates | mandatory | **PARTIAL** — everything executed is evidenced; Windows is absent, not fabricated |

---

## 1. Git baseline and final status

| | Baseline | Final |
| --- | --- | --- |
| Branch | `arena/7557bcfb-erp` | unchanged |
| HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` | **unchanged** |
| Staged | none | none |
| Tracked modified | 16 files | 16 files, same diffstat (1993+/1431−) |
| Untracked | 34 paths | 34 paths + `docs/release/phase30/` |
| Commits / pushes / merges / rebases / resets / cleans | — | **none performed** |

All Phase 29 evidence is byte-identical (`docs/release/phase29/PHASE29_DESKTOP_REDESIGN_REPORT.md`
SHA-256 `712501d1df3c34e6…`). Phase 30 wrote only under `docs/release/phase30/`.
Full snapshot: `10_FINAL_GIT_STATUS.md`.

## 2. Versions and environment

| Item | Value |
| --- | --- |
| Host OS | Debian GNU/Linux 12, kernel 6.1.158+, x86_64 |
| Python (GUI & lock-only suites) | CPython **3.11.2** |
| Python (supported-runtime suite and restore drill) | CPython **3.12.10**, built from source for this phase |
| Qt | PySide6-Essentials **6.11.2**, `QT_QPA_PLATFORM=offscreen` |
| Test/lint tooling | pytest 9.2.0, ruff 0.14.0 |
| Windows host | **none** |

Details and the exact commands: `09_ENVIRONMENT.md`.

## 3. Full-suite baseline comparison

| Environment | Passed | Failed | Skipped | Deselected |
| --- | --- | --- | --- | --- |
| GUI, CPython 3.11.2 — **baseline** (start of phase) | 838 | 22 | 3 | 1 |
| GUI, CPython 3.11.2 — **final** | **839** | **22** | 3 | 1 |
| Lock-only, CPython 3.11.2 — baseline | 813 | 22 | 20 | 0 |
| Lock-only, CPython 3.11.2 — **final** | 813 | **22** | 21 | 0 |
| **Supported runtime, CPython 3.12.10** | **835** | **0** | 21 | 0 |

- **Failure set identical** before and after, in both 3.11.2 environments: 22 = 22 = 22, verified by
  set difference, with **zero** additions and zero removals.
- The +1 pass in the GUI run is the new regression test added in this phase (D-3).
- The +1 skip is a desktop test that now skips in the lock-only environment for the same reason as
  the others (no Qt).
- **All 22 failures pass on CPython 3.12.10**, which is the proof that they are environmental.
  Full classification of all 22: `02_P0_FAILURE_BASELINE.md`.

Root cause, isolated: the safe-restore guard requires `tarfile.data_filter`, which does not exist in
CPython 3.11.2. The guard is a security control and was **not** modified.

## 4. Windows GUI acceptance

**BLOCKED — not executed.** No Windows host, VM, Wine, or mounted Windows filesystem; `apt-get`
has no `wine` package. Per the brief, no Linux screenshot or compilation output was substituted.

A 17-step acceptance procedure for a Windows operator is specified in
`07_WINDOWS_ACCEPTANCE_BLOCKED.md`.

## 5. Arabic, French and English

**Arabic RTL: 18/18 structural checks pass**, plus visual review of every Arabic screenshot.

Verified: RTL layout direction, sidebar on the physical right, mirrored table headers and alignment,
Arabic labels (245/245 keys), Arabic account names, mixed Arabic/Latin content, account codes and
numeric values, DZD amounts, ISO dates, dialog layout, form placeholders, dashboard identity,
invoice and OCR review screens. No clipping, overlap, or missing translation found.

**French and English:** round-trip `ar → fr → en → ar` in one running window with the identity line,
sidebar and table chrome all following. Catalogue parity 245×3 with zero placeholder mismatches.

**Fixed during this phase:** D-3 — table chrome stayed in the previous language (`294 sur 294 lignes`
in an English window).

**Stated limit:** no native Arabic speaker reviewed the strings. Numbers intentionally use Western
digits with a comma decimal separator in all three languages (documented decision).
Details: `03_ARABIC_RTL_ACCEPTANCE.md`.

## 6. High-DPI

Tested at `QT_SCALE_FACTOR` **1.0 / 1.25 / 1.5 / 2.0** (measured devicePixelRatio 1.0–2.0): all
**9 views** rendered at every scale with **no clipped header**. Visual check at 200 % clean.

**Not Windows display scaling.** Windows 100/125/150/200 % is **NOT EXECUTED**, and is not claimed.
One low-severity open item (D-6) is the review dialog's 940×620 minimum at 200 % on a 1366×768 panel.
Details: `04_HIGH_DPI.md`.

## 7. Accessibility

**41/41 checks pass** after fixing two real defects and one high-severity trap:

- **D-5 (high, fixed):** Tab was trapped inside every table — cell-by-cell navigation made a
  100×4 grid 400 consecutive stops and left the controls after it unreachable.
- **D-1 (high, fixed):** dark-theme button labels failed contrast (3.32:1 on primary, 2.55:1
  pressed, 3.50:1 accent, 3.22:1 danger).
- **D-2 (medium, fixed):** light-theme disabled text was 2.42:1.

Verified: keyboard navigation, sidebar arrow-key operation, tab order reaching content and cycling,
accessible labels on all visible controls, disabled-state distinction, error visibility, and
measured contrast for every real colour pairing in both themes.

**Not claimed:** no screen-reader testing was performed, focus-indicator visibility is only partially
verified, and this is **not** a WCAG conformance claim. Details: `05_ACCESSIBILITY.md`.

## 8. Restore drill

**25/25 checks pass** on CPython 3.12.10 (built from source in this sandbox, with the project's own
53 pinned dependencies). Isolated temporary data throughout; production data untouched.

Verified: encrypted backup → decrypt → extract → restore into a fresh directory; `PRAGMA
integrity_check` = ok; foreign keys clean; **trial balance balanced and equal to source
(148 750,00 DZD)**; both audit key versions preserved and both chains verify; audit-event census
identical to source; audit key file, registry and vault keys restored; the restored vault decrypts
its blob byte-identically; the source backup is unchanged; the restore is repeatable; a wrong key is
refused.

Not established: the drill ran on **Linux**, not the Windows runtime. Details:
`06_SAFE_RESTORE_DRILL.md`.

## 9. Regression and security checks

| Check | Command result |
| --- | --- |
| Phase 29 desktop suite | **29 passed** (28 previous + 1 new regression test) |
| Desktop-focused suites (26/27/28/29) | **49 passed** |
| Full suite, GUI | 22 failed / **839 passed** / 3 skipped — same 22 |
| Full suite, lock-only | 22 failed / 813 passed / 21 skipped — same 22 |
| Full suite, supported runtime | **835 passed / 0 failed** |
| Dependency integrity + contract + security regression | **72 passed** |
| Auth / authz / OCR / Arabic routing | **147 passed**, 1 known baseline restore failure (19/19 on 3.12.10) |
| Ruff (`dz_erp/`, `tests/`, Phase 30 evidence) | **All checks passed** |
| `compileall` | **clean** |

No dependency version, lockfile, or `pyproject.toml` entry was changed in this phase.

## 10. Evidence directory

`docs/release/phase30/` — 43 files:

| Path | Contents |
| --- | --- |
| `01_GIT_BASELINE.md` | branch, HEAD, working tree as found, environment |
| `02_P0_FAILURE_BASELINE.md` | the 22 failures classified, root cause, GUI vs lock-only explanation |
| `03_ARABIC_RTL_ACCEPTANCE.md` | RTL/FR/EN results, D-3, stated limits |
| `04_HIGH_DPI.md` | scaling matrix and its exact scope |
| `05_ACCESSIBILITY.md` | contrast table, D-1/D-2/D-5, keyboard results, not-verified list |
| `06_SAFE_RESTORE_DRILL.md` | drill method, 25 checks, runtime construction |
| `07_WINDOWS_ACCEPTANCE_BLOCKED.md` | why blocked + the 17-step procedure |
| `08_DEFECT_LOG.md` | D-1…D-6, tooling errors T-1…T-5, pre-existing P-1…P-4 |
| `09_ENVIRONMENT.md` | the four environments and every exact command |
| `10_FINAL_GIT_STATUS.md` | final `git status`, branch, HEAD, diffstat |
| `evidence/` | `restore_drill.py`, `rtl_capture.py`, `accessibility_probe.py`, `dpi_probe.py`, `mkstub.py`, `ffi-minimal.h`, `failed22.txt` |
| `logs/` | full-suite logs (all three environments), drill log, RTL/a11y/DPI JSON |
| `screenshots/` | 13 PNGs: Arabic RTL ×6, FR ×1, EN ×1, dark ×1, scaling ×4 |

Every screenshot corresponds to the application running in this environment. No screenshot is
simulated, re-drawn or borrowed from another platform.

## 11. Remaining defects and blockers

| ID | Severity | Item | Status |
| --- | --- | --- | --- |
| **P-3** | **High (gate)** | Windows desktop execution never performed | **BLOCKED** — requires a Windows host |
| **P-2** | Medium | No native Arabic linguistic review | **OPEN** — requires a human reviewer |
| **D-6** | Low | Review dialog 940×620 minimum at 200 % on 1366×768 | **OPEN**, unverified on real hardware |
| P-1 | Medium | Arabic amounts use Western digits / comma separator | **Accepted as designed** |
| — | — | Screen-reader / WCAG / Windows high-DPI | **NOT EXECUTED**, not claimed |
| P-4 | None | 22 tests fail on CPython 3.11.2 | **Expected** — security guard; all pass on 3.12.10 |

## 12. What Phase 30 changed in the product

Minimal, evidence-backed fixes only — no feature work, no redesign:

| File | Change |
| --- | --- |
| `dz_erp/desktop/tokens.py` | two new palette roles (`text_on_primary`, `text_on_accent`); dark hover/pressed deepened; light `text_disabled` darkened; stylesheet uses the roles instead of literal `#FFFFFF` |
| `dz_erp/desktop/components.py` | `setTabKeyNavigation(False)` on tables; `set_language()` re-renders count/page chrome; `_filtered_rows()` extracted as the single source of truth for the filtered set |
| `tests/test_phase29_desktop_redesign.py` | one regression test for the language-switch chrome (D-3) |

No dependency, lockfile, schema, API, workflow or guard was modified.
