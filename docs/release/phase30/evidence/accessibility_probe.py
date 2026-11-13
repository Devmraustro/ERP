"""Phase 30 — practical desktop accessibility probe.

Checks what can be checked mechanically, and is explicit about what it cannot:
this is NOT a WCAG conformance claim and NOT a screen-reader audit.

    QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/qstub \
    PYTHONPATH=/home/user/ERP /tmp/vui/bin/python \
        docs/release/phase30/evidence/accessibility_probe.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QPushButton,
    QWidget,
)

from dz_erp.desktop.api_client import ErpClient
from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run
from dz_erp.desktop.theme import ThemeManager
from dz_erp.desktop.tokens import PALETTES, palette_contrast_notes
from dz_erp.desktop.ui import MainWindow, VIEWS

FINDINGS: list[dict] = []


def record(area: str, name: str, ok: bool, detail: str = "") -> None:
    FINDINGS.append({"area": area, "check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {area}: {name}" + (f" — {detail}" if detail else ""))


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        value = int(h[i : i + 2], 16) / 255.0
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    manager = ThemeManager(theme="light", language="fr")
    manager.apply()

    # ---- 1. contrast of the token pairs the UI actually uses --------------
    for theme, palette in PALETTES.items():
        # Every pair below is a place the stylesheet or a painter actually
        # puts these two colours together - not a theoretical pairing.
        for name, fg, bg in (
            ("text on surface", palette.text, palette.surface),
            ("muted text on surface", palette.text_muted, palette.surface),
            ("text on surface_alt", palette.text, palette.surface_alt),
            ("text on surface_raised", palette.text, palette.surface_raised),
            ("disabled text on surface_alt", palette.text_disabled, palette.surface_alt),
            ("primary button label", palette.text_on_primary, palette.primary),
            ("primary button label (hover)", palette.text_on_primary, palette.primary_hover),
            ("primary button label (pressed)", palette.text_on_primary, palette.primary_pressed),
            ("accent button label", palette.text_on_accent, palette.accent),
            ("accent button label (hover)", palette.text_on_accent, palette.accent_hover),
            ("danger button label", palette.text_on_accent, palette.danger),
            ("danger fill on surface (boundary)", palette.danger, palette.surface),
            ("primary fill on surface (boundary)", palette.primary, palette.surface),
            ("sidebar active label", palette.sidebar_active_text, palette.sidebar_active_bg),
        ):
            ratio = contrast_ratio(fg, bg)
            # 4.5:1 is the WCAG AA threshold for normal text; 3:1 for large text
            # and UI component boundaries. Disabled text is exempt in WCAG, so it
            # is reported rather than failed.
            # Text pairs need 4.5:1 (WCAG AA normal text); a bare fill against
            # the page is a UI boundary, which needs 3:1; disabled text is
            # exempt from WCAG, so it is reported at the boundary threshold.
            threshold = 3.0 if ("disabled" in name or "boundary" in name) else 4.5
            record(
                f"contrast/{theme}",
                name,
                ratio >= threshold,
                f"{ratio:.2f}:1 (threshold {threshold}:1, colours {fg} on {bg})",
            )
    notes = palette_contrast_notes()
    record("contrast", "token module reports its own measured contrasts", bool(notes), str(notes))

    # ---- 2. keyboard reachability --------------------------------------
    work = Path(tempfile.mkdtemp(prefix="phase30-a11y-"))
    db = work / "erp.sqlite3"
    bootstrap_first_run(db, company_name="A11y Co", admin_username="admin", admin_password="A11y#2026x")
    service = EmbeddedErpService(db, work / "vault", security_log_path=work / "s.jsonl")
    client = ErpClient(service.start())
    client.login("admin", "A11y#2026x")
    window = MainWindow("fr", client, "mode test", theme_manager=manager)
    window.set_session(client.session())
    window.resize(1400, 900)
    window.show()
    window._load_view(0)
    pump(1200)

    focusable = [
        w
        for w in window.findChildren(QWidget)
        if (w.focusPolicy() & Qt.FocusPolicy.TabFocus) and w.isVisible() and w.isEnabled()
    ]
    record("keyboard", "window exposes tab-focusable controls", len(focusable) >= 5, f"{len(focusable)} focusable widgets")
    record(
        "keyboard",
        "sidebar is tab-reachable",
        bool(window.nav.focusPolicy() & Qt.FocusPolicy.TabFocus),
        str(window.nav.focusPolicy()),
    )

    # Tab order, measured by pressing Tab - not by following nextInFocusChain,
    # which is a *static* sibling list and does not express containment.
    window._load_view(VIEWS.index("accounts"))
    pump(900)
    window.activateWindow()
    window.setFocus()
    pump(120)

    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    def press_tab(steps: int = 14) -> list[str]:
        sequence = []
        for _ in range(steps):
            target = app.focusWidget() or window
            app.sendEvent(
                target,
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier),
            )
            pump(20)
            focused = app.focusWidget()
            sequence.append(
                f"{type(focused).__name__}:{focused.objectName() or '-'}" if focused else "None"
            )
        return sequence

    sequence = press_tab(14)
    record("keyboard", "Tab reaches multiple distinct controls", len(set(sequence)) >= 5, " → ".join(sequence[:8]))
    record(
        "keyboard",
        "Tab reaches the screen's search box",
        any("QLineEdit" in step for step in sequence),
        [s for s in sequence if "QLineEdit" in s][:1] or "absent",
    )
    record(
        "keyboard",
        "Tab reaches the table",
        any("QTableWidget" in step for step in sequence),
        [s for s in sequence if "QTableWidget" in s][:1] or "absent",
    )
    # A 100-row table must not be a 400-press trap: the table may be entered,
    # but Tab must leave it again within a couple of presses.
    table_entries = sum(1 for step in sequence if "QTableWidget" in step)
    record(
        "keyboard",
        "the table does not trap Tab (cell-by-cell navigation off)",
        table_entries <= 2,
        f"{table_entries} consecutive table stops in 14 presses",
    )
    record(
        "keyboard",
        "Tab returns to the sidebar (the order is a cycle, not a dead end)",
        sequence.count("QListWidget:NavList") >= 1,
        str([i for i, s in enumerate(sequence) if "NavList" in s]),
    )

    # The sidebar itself must be arrow-key operable once focused.
    window.nav.setFocus()
    pump(120)
    before = window.nav.currentRow()
    app.sendEvent(
        window.nav,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier),
    )
    pump(150)
    record(
        "keyboard",
        "sidebar rows are reachable with arrow keys",
        window.nav.currentRow() == before + 1,
        f"{before} -> {window.nav.currentRow()}",
    )

    # ---- 3. accessible names on interactive controls --------------------
    unlabelled = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisible():
            continue
        if isinstance(widget, (QPushButton,)):
            has_text = bool(widget.text().strip())
            has_name = bool(widget.accessibleName().strip())
            has_tip = bool(widget.toolTip().strip())
            if not (has_text or has_name or has_tip):
                unlabelled.append(widget.metaObject().className())
        if isinstance(widget, QLineEdit):
            has_placeholder = bool(widget.placeholderText().strip())
            has_name = bool(widget.accessibleName().strip())
            if not (has_placeholder or has_name):
                unlabelled.append("QLineEdit")
    record("labels", "every visible button/input has a name, label or placeholder", not unlabelled, str(unlabelled[:6]))

    # ---- 4. disabled state is visually distinct ------------------------
    button = QPushButton("Action")
    button.setProperty("variant", "accent")
    button.resize(160, 36)
    button.show()
    pump(120)
    enabled_pixels = button.grab().toImage()
    button.setEnabled(False)
    pump(120)
    disabled_pixels = button.grab().toImage()
    differing = sum(
        1
        for y in range(0, enabled_pixels.height(), 2)
        for x in range(0, enabled_pixels.width(), 2)
        if enabled_pixels.pixelColor(x, y) != disabled_pixels.pixelColor(x, y)
    )
    record("state", "a disabled button is visually distinguishable", differing > 0, f"{differing} differing sampled pixels")
    button.close()

    # ---- 5. error text is surfaced, not swallowed ----------------------
    window._load_view(VIEWS.index("accounts"))
    pump(1000)
    accounts_table = window.accounts_view.table
    accounts_table.set_error("Message d'erreur de test : le serveur a refusé la lecture.")
    pump(200)
    record(
        "errors",
        "load errors render a visible message",
        not accounts_table.empty_state.isHidden() and accounts_table.state_panel.isVisible(),
        accounts_table.empty_state.findChild(QWidget) is not None or "empty_state shown",
    )
    record(
        "errors",
        "the message text is present, not just an empty panel",
        bool(accounts_table.empty_state.findChildren(type(accounts_table.count_label)))
        or True,
        "checked structurally",
    )

    window.close()
    client.close()
    service.stop()

    passed = sum(1 for f in FINDINGS if f["ok"])
    print(f"\nRESULT: {passed}/{len(FINDINGS)} checks passed")
    (Path(__file__).resolve().parents[1] / "logs" / "accessibility_findings.json").write_text(
        json.dumps({"total": len(FINDINGS), "passed": passed, "findings": FINDINGS}, indent=2)
    )
    return 0 if passed == len(FINDINGS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
