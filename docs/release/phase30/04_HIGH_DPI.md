# Phase 30 — high-DPI and scaling

## What was executed

`evidence/dpi_probe.py` launches the application four times with different
`QT_SCALE_FACTOR` values and, in each run, visits **every** one of the 9 navigable views, checking
that the screen header fits its own minimum size and saving a screenshot.

| `QT_SCALE_FACTOR` | measured `devicePixelRatio` | views rendered | clipped headers | screenshot |
| --- | --- | --- | --- | --- |
| 1.00 | 1.0 | 9/9 | 0 | `screenshots/30_dpi_1_0.png` |
| 1.25 | 1.25 | 9/9 | 0 | `screenshots/30_dpi_1_25.png` |
| 1.50 | 1.5 | 9/9 | 0 | `screenshots/30_dpi_1_5.png` |
| 2.00 | 2.0 | 9/9 | 0 | `screenshots/30_dpi_2_0.png` |

Result data: `logs/dpi_results.json`. Visual inspection at 200 % (`30_dpi_2_0.png`) shows the
sidebar, table, buttons, form controls and typography all scaled proportionally with no clipping,
overlap or unreadable text.

## What this does *not* prove

**This is not Windows display-scaling acceptance.** The probe runs on Linux with Qt's `offscreen`
platform; `QT_SCALE_FACTOR` exercises Qt's own scaling machinery, which is the same layout code that
Windows uses, but it does not cover:

- Windows DPI-awareness manifests and per-monitor DPI changes;
- the Windows font stack (Segoe UI vs the Linux default), which changes text metrics;
- multi-monitor mixed-DPI dragging;
- native window chrome and the title bar at 150 %/200 %.

Windows scaling at 100 / 125 / 150 / 200 % is therefore recorded as **NOT EXECUTED** in the release
gate table, not as passed.

## Findings

No defect was found in the layout code itself at any tested scale. The only scaling-sensitive
observation is that the review dialog enforces a minimum size of 940×620 logical pixels: at 200 % on
a 1366×768 physical panel the dialog would exceed the screen and require the window manager to
scroll or resize it. That is a real constraint for low-resolution/high-scale hardware and is
recorded as **D-6 (low, unverified on real hardware)** rather than silently ignored — it could not be
tested here because no such panel is attached.
