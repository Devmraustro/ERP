# Phase 30 — accessibility review (practical)

## Scope warning, first

This is a **manual/mechanical review**, not a WCAG conformance audit and not a screen-reader test.
No assistive technology (NVDA, JAWS, Orca, VoiceOver) was run, because none is installed in this
environment. Nothing below should be read as a WCAG claim.

Script: `evidence/accessibility_probe.py` — **41/41 checks pass** after the fixes below.
Machine-readable results: `logs/accessibility_findings.json`.

## Contrast (measured, both themes)

Every pair measured is a pairing the stylesheet or a painter actually produces.

| Pair | Light | Dark | Threshold |
| --- | --- | --- | --- |
| text on surface | 16.49:1 | 13.97:1 | 4.5 (AA normal text) |
| muted text on surface | 5.70:1 | 6.76:1 | 4.5 |
| text on surface_alt | 15.75:1 | 13.18:1 | 4.5 |
| text on surface_raised | 16.49:1 | 12.55:1 | 4.5 |
| **primary button label** | 13.10:1 | **5.53:1** | 4.5 |
| **primary label (hover)** | 10.36:1 | **6.42:1** | 4.5 |
| **primary label (pressed)** | 15.15:1 | **7.94:1** | 4.5 |
| **accent button label** | 6.45:1 | **6.35:1** (was 3.50) | 4.5 |
| **danger button label** | 6.54:1 | **5.70:1** (was 3.22) | 4.5 |
| **disabled text on surface_alt** | **3.32:1** (was 2.42) | 3.26:1 | 3.0 |
| sidebar active label | 10.19:1 | 14.58:1 | 4.5 |

## Defects found and fixed

### D-1 — dark theme button labels failed contrast (fixed)

Three separate problems in the dark palette:

1. **`primary` used near-black text (`#0F151B`) on `#2E6CA4`** → **3.32:1**, below AA. White on the
   same blue is **5.53:1**. The palette's single `text_inverse` token could not serve both themes.
2. **`primary_hover`/`primary_pressed` lightened the fill**, which *reduced* contrast further
   (hover 4.20:1, pressed **2.55:1**). With white text, hover/pressed must deepen.
3. **`accent` and `danger` hardcoded `#FFFFFF`** while the dark palette provides `text_inverse` for
   exactly this → **3.50:1** and **3.22:1**.

*Fix:* two new palette roles, `text_on_primary` and `text_on_accent`, chosen per theme (white/white
in light; white/near-black in dark); dark `primary_hover`/`primary_pressed` deepened to `#2A6294` /
`#24547E`; the stylesheet now uses the roles instead of literal `#FFFFFF`.

### D-2 — light theme disabled text was 2.42:1 (fixed)

WCAG exempts disabled controls, but 2.42:1 is faint enough to be unreadable, and a disabled control
still has to say *which* action is unavailable. `text_disabled` changed `#98A4B0` → `#7E8B99`
(**3.32:1**), still visibly lighter than muted text (5.70:1) so "disabled" still reads as disabled.

### D-5 — Tab was trapped inside every table (fixed)

`QTableWidget` defaults to cell-by-cell tab navigation. In a 100-row, 4-column table that is **400
consecutive Tab stops** before focus can leave the grid — the search box before it and every control
after it become unreachable in practice.

*Fix:* `DataTable` now sets `setTabKeyNavigation(False)`. Tab enters the table once and leaves;
arrow keys still move the selection, which is what a reviewer actually needs when reading rows.

*Measured before:* after 12 Tab presses focus was still `QTableWidget` and never left.
*Measured after:* `NavList → SidebarToggle → IconButton → ComboBox → ComboBox → IconButton →
SearchBox → TableWidget → Button → NavList` (10 distinct stops, table entered once).

## Verified as passing

| Check | Result |
| --- | --- |
| Keyboard navigation exists | 7 tab-focusable widgets in the chrome; `Ctrl+1…9` jump to views, `Ctrl+B` toggles the sidebar, `Ctrl+Q` quits |
| Sidebar operable by keyboard | list receives focus; Down arrow moves the selection (3 → 4) |
| Tab order reaches content | search box and table are both reached by pressing Tab |
| Tab order is a cycle | focus returns to the sidebar, not to a dead end |
| Accessible control labels | 0 visible buttons/inputs lacking text, `accessibleName` or placeholder |
| Disabled state distinction | 1440 differing sampled pixels between enabled and disabled accent buttons |
| Error messages visible | load errors render in the panel (parent shown, table hidden) with the message text |
| Contrast | all measured pairs meet their threshold in both themes |

## Not verified — do not infer

| Item | Status |
| --- | --- |
| Screen-reader announcement (names, roles, states, live regions) | **NOT EXECUTED** — no AT available |
| Focus **indicator** visibility on every control | **PARTIAL** — buttons have a `:focus` border rule; the nav list and table rely on Qt's default focus rectangle, whose visibility on a dark background was not measured |
| Visible focus on the scroll areas | **NOT VERIFIED** |
| Table semantics exposed to AT (headers, row/column counts) | **NOT VERIFIED** |
| Keyboard-only completion of invoice creation in the native `QInputDialog` flows | **NOT EXECUTED** |
| WCAG 2.2 AA conformance | **NOT CLAIMED** |
