# Phase 29 — Desktop redesign: design system, RTL, and review workspace

**Verdict: implementation verified on this branch, in this sandbox.** The desktop client is
now a themeable, bilingual (FR/AR/EN), RTL-aware application with a single widget kit, and the
invoice review workspace shows provenance and only the actions the stored workflow state allows.

Nothing in this document is a legal, accounting, or regulatory approval. Section 8 lists exactly
what is *not* verified here.

Branch: `arena/7557bcfb-erp`, forked from `main@4fc1364`. Baseline commit kept intact.
No commit or push was performed as part of this phase's work.

---

## 1. What changed

### 1.1 A design system instead of per-widget styling

| Module | Role |
| --- | --- |
| `dz_erp/desktop/tokens.py` | Qt-free `Palette` dataclass, `PALETTES` (`light`, `dark`), `build_stylesheet(theme, rtl=…)`, `build_palette(theme)`, `resolve_theme`, `palette_contrast_notes`, `STYLEABLE_OBJECTS` |
| `dz_erp/desktop/theme.py` | `apply()`: palette → stylesheet → unpolish/polish → icon-cache flush → listeners |
| `dz_erp/desktop/components.py` | the widget kit: `DataTable`, `BarChart`, `StatCard`, `Card`, `Pill`, `Banner`, `EmptyState`, `Button`, `PageHeader` |

`tokens.py` imports no Qt, so palette, spacing and direction rules are testable — and testable on a
machine where the optional `desktop` extra is not installed.

**Rule enforced by test:** no module outside `tokens.py` may hardcode a colour or call
`setStyleSheet`. `BarChart` originally kept three literal hex values; it now receives
`{"bar", "text", "grid"}` from the theme on every `set_items()` call and defaults to empty
`QColor()` rather than guessing a colour.

### 1.2 Themes and languages

- Two complete palettes (light/dark); **37** styleable object names covered by the stylesheet.
- Measured text contrast: light **16.49:1**, dark **13.97:1** (WindowText on surface).
- **245** strings × **3** languages (fr, ar, en), key- and placeholder-complete (parity checked).
- Language switch rebuilds every string, flips layout direction, and mirrors tables and the
  sidebar; the choice persists through the config whitelist.

### 1.3 The invoice review workspace

Three panes: extracted OCR fields with **provenance** (raw text, current value, confidence, origin:
`OCR` / `Vérifié`), the invoice lines, and the source-document preview with the invoice header.

Workflow actions are enabled **by stored state only** — the server stays the authority:

| Stored state | Armed actions |
| --- | --- |
| `Validated` | submit for approval |
| `PendingApproval` | approve, reject |
| `Approved` | post |
| `NeedsReview`, `Posted`, `Rejected`, `Reversed`, `DuplicateRejected` | review only |
| any unknown state | none (guessing would arm a button the server refuses) |

A disabled action carries a tooltip naming the state that disables it, so a greyed button never
looks broken.

---

## 2. Defects the new tests found — and the fixes

Every defect below was fixed **in the code**. No test was weakened to pass.

| # | Defect | Fix |
| --- | --- | --- |
| 1 | `BarChart` painted three literal hex colours, so it drifted out of both palettes | colours arrive via `set_items(items, colors)`; defaults are empty `QColor()` |
| 2 | `format_amount(None)` returned `""`, which reads as zero in a money column | returns `NO_VALUE = "—"`; money renders at exactly two decimals and is **never rounded** |
| 3 | `DataTable.set_error()` showed its message child but hid its own parent panel — the error rendered as an empty page | `state_panel` and `empty_state` both shown, `table` hidden |
| 4 | A **disabled** `primary`/`accent` button painted exactly like an armed one: `QPushButton:disabled` was declared *before* the variant rules, and Qt resolves equal specificity by source order | every variant (`primary`, `accent`, `ghost`, `danger`) now has its own `:disabled` rule after its base rule |
| 5 | `_set_busy(False)` re-enabled every button, including actions the stored state forbids | the busy toggle re-applies the state gate; before the first load it skips the gate rather than hiding everything |
| 6 | `humanize_identifier` split camelCase only, so account types printed as `Equity_unaffected`, `Asset_non_current` | snake_case is spaced too: `Equity unaffected`, `Asset non current` |
| 7 | List tables ended in a dead band: fixed `Column.width` was never adapted to the pane | spare width goes to the prose column first, then proportionally; overflow scrolls instead of eliding; no column is narrower than its content |
| 8 | In an Arabic UI the chart still plotted **every** trial-balance row, including pure-credit accounts as zero-length bars | only accounts with debit movement are plotted |
| 9 | Chart labels were bare account codes (`4456`), and the chart ignored RTL | labels read `4456 · TVA récupérable et précompte`, painted text mirrors for RTL |
| 10 | Arabic UI showed French account names although the API returns `name_ar` | per-row fallback `name_ar → name_fr`, so a partially translated plan never shows blanks |
| 11 | The dashboard identity line stayed French (`Utilisateur : admin (admin)`) after switching to Arabic | identity is stored and re-rendered on every language switch (`المستخدم: admin (admin)`) |
| 12 | The OCR correction box was blank for a field whose extraction failed, next to an enabled "Corriger…" | placeholder names the field: *Aucune valeur extraite pour « amount_ttc_dzd ».* |

Items 4, 5, 8, 9, 11 and 12 were found by **inspecting the rendered screenshots**, not by the
test suite — which is why the screenshots in `screenshots/` are regenerated and re-read at the end
of every change, not captured once.

---

## 3. Evidence

`screenshots/` — 20 PNGs, captured offscreen from a bootstrap demo company with a **real posted
invoice** (created by a clerk, approved by an approver, posted by an accountant, so segregation of
duties is exercised rather than bypassed):

| File | Shows |
| --- | --- |
| `01..08` | FR light: dashboard, accounts, suppliers, stock, invoices, trial balance, users, documents |
| `09..11` | FR dark: dashboard, accounts, invoices |
| `12..14` | AR RTL: dashboard, trial balance, stock |
| `15..16` | EN LTR: dashboard, invoices |
| `17..18` | review workspace, then a selected field with provenance |
| `19` | collapsed sidebar |
| `20` | 1024 px narrow window |

`19` shows the ledger balanced at **59 500,00 DZD** over 3 lines, with the chart plotting only the
debit accounts. `20` shows the table scrolling horizontally at 1024 px instead of eliding values.

---

## 4. Test results (executed in this sandbox)

Environment A — GUI: Python 3.11.2 + PySide6-Essentials 6.11.2, `QT_QPA_PLATFORM=offscreen`.
Environment B — lock-only: `--no-deps --require-hashes` from `requirements.lock.hashes.txt`, no Qt.

| Run | Command | Result |
| --- | --- | --- |
| Phase 29, GUI | `pytest tests/test_phase29_desktop_redesign.py -q` | **28 passed** |
| Phase 29, lock-only | same | **14 passed, 14 skipped** |
| Legacy desktop suites | `pytest tests/test_phase27_* tests/test_phase28_* -q` | **32 passed** |
| Full suite, GUI | `pytest tests/ -q` | **838 passed, 22 failed, 3 skipped** |
| Full suite, lock-only | `pytest tests/ -q` | **813 passed, 22 failed, 20 skipped** |
| Lint | `ruff check dz_erp/desktop/ tests/test_phase29_desktop_redesign.py` | clean |
| Byte-compile | `python -m compileall -q dz_erp/desktop` | clean |

The **22 failures are the same in both environments and on the untouched baseline**: every one is
`FinancialIntegrityError: Safe backup restore requires Python 3.11.17+ or 3.12.10+…`. The sandbox
interpreter is 3.11.2, so the safe-restore guard refuses to run. They are environmental, not
regressions — the counts before this work were 831/22/3 (GUI) and 812/22/14 (lock-only).

The Qt-free skip is deliberate: `tests/test_phase29_desktop_redesign.py` probes for PySide6 at
import and marks the 15 window tests `skipif`, so a machine without the `desktop` extra **skips**
them instead of erroring in fixture set-up.

---

## 5. How to reproduce

```bash
# GUI environment
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock.hashes.txt --no-deps --require-hashes
pip install "PySide6-Essentials==6.11.2" pytest pytest-cov pytest-asyncio ruff
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_phase29_desktop_redesign.py -q

# lock-only environment (no Qt): the desktop tests skip, the rest must pass
python3 -m pytest -q
```

---

## 6. Design decisions worth keeping

1. **The server is the authority; the client only avoids certain failure.** `ACTIONS_BY_STATE`
   exists so the UI never offers an action the server will refuse — not so the client can enforce
   the workflow. Segregation of duties, state transitions and posting rules remain server-side.
2. **A missing value is not a zero.** `format_amount(None)` returns `—`; `NO_VALUE` is rendered,
   not omitted, and money is never rounded to two decimals if the source has more scale.
3. **No literal colours outside `tokens.py`.** A widget that keeps a colour drifts out of the
   design system the moment a theme changes; painters take their colours as arguments.
4. **A disabled control must say why.** Tooltips carry the state that disables the action, and a
   disabled primary button is visibly disabled (see defect 4).
5. **Named metrics over invented ones.** Metrics no endpoint publishes (`UNAVAILABLE_METRICS`,
   eight of them) are labelled as unavailable in the UI rather than estimated.

---

## 7. Known limits of this evidence

- Screenshots are **offscreen renders** (`QT_QPA_PLATFORM=offscreen`) on Linux, not captures from a
  real Windows or macOS desktop. Font metrics and native window chrome will differ.
- The demo company is seeded by the capture script for illustration; the numbers on screen come
  from the API, but they are demo data, not a production ledger.
- Arabic right-to-left rendering is verified structurally (mirrored layout, translated strings,
  Arabic account names) but **not** by a native Arabic reader. No expert review is claimed.

## 8. Not verified by this phase

| Item | Status |
| --- | --- |
| Native Arabic linguistic review | **NOT EXECUTED** |
| Windows / macOS desktop acceptance | **NOT EXECUTED** (see `phase8/WINDOWS_DESKTOP_ACCEPTANCE.md`) |
| Safe backup restore on a supported interpreter (3.11.17+/3.12.10+) | **NOT EXECUTED** in this sandbox — 22 suite failures |
| High-DPI / scaled displays | **NOT EXECUTED** |
| Screen-reader accessibility audit | **NOT EXECUTED** |
| Legal/regulatory approval of anything displayed | **PENDING** — unchanged from Phases 8–10 |
