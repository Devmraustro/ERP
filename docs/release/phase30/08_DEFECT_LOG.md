# Phase 30 — defect log

Every defect below was found **in this phase**. Each was fixed in the product code (never by
weakening a test), and each fix has a verification attached. Severity is assessed for a release
decision, not for a bug tracker.

| ID | Severity | Area | Defect | Fix | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- |
| **D-1** | **High** | Accessibility / theming | Dark theme filled-button labels failed contrast: near-black text on `#2E6CA4` = **3.32:1**; hover **4.20:1**; pressed **2.55:1**; `accent` and `danger` hardcoded `#FFFFFF` over a dark accent = **3.50:1** / **3.22:1** | New palette roles `text_on_primary` / `text_on_accent` chosen per theme; dark hover/pressed deepened so they *raise* contrast; stylesheet uses roles, no literal `#FFFFFF` | `05_ACCESSIBILITY.md` table; probe 41/41 | **FIXED** |
| **D-2** | Medium | Accessibility / theming | Light theme disabled text was **2.42:1**, faint enough to be unreadable | `text_disabled` `#98A4B0` → `#7E8B99` (**3.32:1**), still lighter than muted text | probe contrast row | **FIXED** |
| **D-3** | Medium | i18n | Switching language left the table toolbar in the old language: English window showed `294 sur 294 lignes`; Arabic window showed `سطرًا 294` | `DataTable.set_language()` re-renders the chrome it composes; count and page strings share `_filtered_rows()` with the view so the number cannot drift | `test_language_switch_retranslates_the_table_chrome` (confirmed to fail when reverted) | **FIXED** |
| **D-5** | **High** | Accessibility | Tab was trapped in every table: `QTableWidget` defaults to cell-by-cell tab navigation, so a 100×4 table is 400 consecutive Tab stops and every control after it is unreachable | `DataTable.setTabKeyNavigation(False)`; arrow keys still move selection | before: 12 Tabs never left the table; after: 10 distinct stops, table entered once | **FIXED** |
| **D-6** | **Low** | High-DPI | The invoice review dialog has a hard minimum of 940×620 logical px; at 200 % on a 1366×768 panel it exceeds the screen | **Not fixed** — no such panel exists to test on, and changing the minimum blind risks breaking the three-pane layout | Reported only | **OPEN (unverified)** |

## Defects found in this phase's own tooling (not in the product)

These were mistakes in the Phase 30 probe scripts, caught before any conclusion was drawn from them.
They are recorded because a reviewer must be able to see that the evidence was corrected rather than
bent.

| # | What was wrong | Effect if not caught |
| --- | --- | --- |
| T-1 | `dpi_probe.py` child process never constructed a `QApplication` | all four scale runs would have reported "0 screens, no clipping" — a false pass |
| T-2 | Accessibility probe walked `nextInFocusChain` from the window | reported "Tab reaches only 6 controls", implying unreachable content that is in fact reachable; the real defect (D-5) was found only after switching to pressing actual Tab keys |
| T-3 | `restore_drill.py` asserted hardcoded audit-event counts | would have raised a false failure, because posting an invoice legitimately appends audit events; corrected to compare restored-vs-source censuses |
| T-4 | `restore_drill.py` called `PrivateDocumentVault.read_bytes` with the wrong signature | false failure on the vault-decryption check |
| T-5 | Phase 30 full-suite runs on 3.12.10 initially showed 3 failures | the cause was the test harness overwriting `PYTHONPATH` for subprocess tests, not the product; resolved by exposing deps through a `.pth` file instead of `PYTHONPATH` |

## Pre-existing defects re-confirmed (carried from earlier phases, unchanged)

| ID | Severity | Item | Status |
| --- | --- | --- | --- |
| P-1 | Medium | `formatting.format_amount` uses a Western comma decimal separator in Arabic; no Arabic-Indic digit option | **Accepted as designed** — see the decision note in `03_ARABIC_RTL_ACCEPTANCE.md` |
| P-2 | Medium | No native Arabic linguistic review has ever been performed | **OPEN** — requires a human reviewer |
| P-3 | **High** | Windows desktop execution never performed (any phase) | **BLOCKED** — `07_WINDOWS_ACCEPTANCE_BLOCKED.md` |
| P-4 | None (by design) | 22 suite tests fail on CPython 3.11.2 | **EXPECTED** — security guard; all 22 pass on 3.12.10 |

## Severity summary

- **Critical:** none.
- **High:** three — D-1 and D-5 (fixed in this phase) and P-3 (Windows gate, blocked).
- **Medium:** D-2, D-3 (fixed), P-1 (accepted), P-2 (open, human gate).
- **Low:** D-6 (open, unverified).
