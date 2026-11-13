# SPDX-License-Identifier: LGPL-3.0-only
"""Vector icon set for the desktop client (Phase 29).

A consistent icon system replaces the text symbols the first client used
(``»``, ``+``, ``✓``). Icons are declared once as small SVG path bodies and
drawn through ``QSvgRenderer`` into a ``QPixmap`` at the requested size and
colour, so:

* the same glyph looks identical in every view, at every DPI;
* a theme change re-colours every icon instead of shipping two asset sets;
* there are no binary assets to keep in sync with the repository.

Strokes are drawn with ``currentColor`` replaced by the caller's colour, so an
icon on the sidebar and the same icon in a dialog can differ without a second
file. The path data is public and part of the visual contract: a test asserts
every icon referenced by the UI exists and parses.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

#: 24x24 viewBox, 1.75 stroke, round caps - one visual language.
_STROKE_PATH = (
    '<path d="{d}" fill="none" stroke="{color}" stroke-width="1.75" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
)

#: Icons drawn as filled shapes (status dots, etc.).
_FILL_PATH = '<path d="{d}" fill="{color}"/>'

_ICONS: Dict[str, Tuple[str, str]] = {
    # -- brand ---------------------------------------------------------
    # A ledger sheet inside a rounded square: the product mark, drawn with the
    # same path grammar as every other icon so it re-colours with the theme.
    "logo": (_STROKE_PATH, "M4 4h16v16H4zM8 8h8M8 12h8M8 16h5"),
    # -- navigation ----------------------------------------------------
    "dashboard": (_STROKE_PATH, "M3 13h8V3H3v10Zm10 8h8V11h-8v10ZM3 21h8v-5H3v5ZM13 8h8V3h-8v5Z"),
    "ledger": (_STROKE_PATH, "M4 5h16M4 12h16M4 19h10M4 5v14"),
    "accounts": (_STROKE_PATH, "M4 20V8l8-4 8 4v12M9 20v-6h6v6M2 20h20"),
    "products": (_STROKE_PATH, "M12 3 3 7.5v9L12 21l9-4.5v-9L12 3Zm0 0v18M3 7.5 12 12l9-4.5"),
    "stock": (_STROKE_PATH, "M3 8h18M3 16h18M7 4v4M17 4v4M7 16v4M17 16v4M3 4h18v16H3z"),
    "partners": (_STROKE_PATH, "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7 0a3 3 0 1 0 0-6M2 21v-2a5 5 0 0 1 5-5h4a5 5 0 0 1 5 5v2M17 14h1a4 4 0 0 1 4 4v3"),
    "documents": (_STROKE_PATH, "M7 3h7l4 4v14H7zM14 3v4h4M10 12h6M10 16h6"),
    "invoices": (_STROKE_PATH, "M6 3h12v18l-3-2-3 2-3-2-3 2V3ZM9 8h6M9 12h6M9 16h3"),
    "users": (_STROKE_PATH, "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21v-2a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v2"),
    "trial_balance": (_STROKE_PATH, "M12 3v18M5 7h9M5 17h14M4 7h1v10H4M19 12h1v5h-1"),
    "settings": (_STROKE_PATH, "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8-3a8 8 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a8 8 0 0 0-2-1.2L15.2 3H8.8l-.4 2.5a8 8 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A8 8 0 0 0 4 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a8 8 0 0 0 2 1.2l.4 2.5h6.4l.4-2.5a8 8 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2Z"),
    # -- actions -------------------------------------------------------
    "search": (_STROKE_PATH, "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35"),
    "filter": (_STROKE_PATH, "M3 5h18l-7 8v6l-4 2v-8L3 5Z"),
    "sort": (_STROKE_PATH, "M8 4v16M4 8l4-4 4 4M16 20V4M12 16l4 4 4-4"),
    "refresh": (_STROKE_PATH, "M21 12a9 9 0 1 1-3-6.7M21 4v5h-5"),
    "upload": (_STROKE_PATH, "M12 16V4M7 9l5-5 5 5M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"),
    "plus": (_STROKE_PATH, "M12 5v14M5 12h14"),
    "review": (_STROKE_PATH, "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"),
    "check": (_STROKE_PATH, "M4 12.5 9 17.5 20 6.5"),
    "close": (_STROKE_PATH, "M6 6l12 12M18 6 6 18"),
    "warning": (_STROKE_PATH, "M12 3 2.5 20h19L12 3Zm0 6v5M12 17h.01"),
    "info": (_STROKE_PATH, "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13h.01M11 12h1v5h1"),
    "lock": (_STROKE_PATH, "M6 10h12v11H6zM9 10V7a3 3 0 0 1 6 0v3M12 15v2"),
    "logout": (_STROKE_PATH, "M15 4h4v16h-4M11 8l-4 4 4 4M7 12h10"),
    "bell": (_STROKE_PATH, "M6 9a6 6 0 1 1 12 0c0 4 1.5 5 1.5 5h-15S6 13 6 9Zm4 9a2 2 0 0 0 4 0"),
    "globe": (_STROKE_PATH, "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM3.5 9h17M3.5 15h17M12 3c2.5 2.5 2.5 15 0 18M12 3C9.5 5.5 9.5 18.5 12 21"),
    "sun": (_STROKE_PATH, "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"),
    "moon": (_STROKE_PATH, "M21 13.2A9 9 0 1 1 10.8 3a7 7 0 1 0 10.2 10.2Z"),
    "chevron_left": (_STROKE_PATH, "M15 5l-7 7 7 7"),
    "chevron_right": (_STROKE_PATH, "M9 5l7 7-7 7"),
    "chevron_down": (_STROKE_PATH, "M5 9l7 7 7-7"),
    "menu": (_STROKE_PATH, "M3 6h18M3 12h18M3 18h18"),
    "arrow_up": (_STROKE_PATH, "M12 19V5M6 11l6-6 6 6"),
    "arrow_down": (_STROKE_PATH, "M12 5v14M6 13l6 6 6-6"),
    "download": (_STROKE_PATH, "M12 4v12M7 11l5 5 5-5M4 20h16"),
    "external": (_STROKE_PATH, "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"),
    "link": (_STROKE_PATH, "M10 14a4 4 0 0 0 6 .5l3-3a4 4 0 0 0-6-6l-1.5 1.5M14 10a4 4 0 0 0-6-.5l-3 3a4 4 0 0 0 6 6L12.5 17"),
    "calendar": (_STROKE_PATH, "M4 6h16v15H4zM8 3v5M16 3v5M4 11h16"),
    "receipt": (_STROKE_PATH, "M5 3h14v18l-2.5-1.5L14 21l-2-1.5L10 21l-2.5-1.5L5 21V3ZM8 8h8M8 12h8M8 16h4"),
    "bank": (_STROKE_PATH, "M3 10 12 4l9 6M5 10v9M9 10v9M15 10v9M19 10v9M3 21h18"),
    "coins": (_STROKE_PATH, "M9 12a6 6 0 1 0 0-12 6 6 0 0 0 0 12Zm9 9a6 6 0 1 0 0-12 6 6 0 0 0 0 12ZM4 3c1.5 1 4 1.5 5 1.5S12.5 4 14 3"),
    "warehouse": (_STROKE_PATH, "M3 9 12 4l9 5v12H3V9ZM7 21v-7h10v7M7 17h10"),
    "chart_bar": (_STROKE_PATH, "M4 20V10M10 20V4M16 20v-7M22 20H2"),
    "chart_line": (_STROKE_PATH, "M3 17l5-6 4 3 5-7 4 4"),
    "clock": (_STROKE_PATH, "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2"),
    "shield": (_STROKE_PATH, "M12 21s8-3.5 8-9V6l-8-3-8 3v6c0 5.5 8 9 8 9Z"),
    "dot": (_FILL_PATH, "M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0"),
    "empty_box": (_STROKE_PATH, "M3 8l9-5 9 5v8l-9 5-9-5V8Zm0 0 9 5 9-5M12 13v8"),
}


def available_icons() -> Iterable[str]:
    """Every icon name the UI may reference."""
    return tuple(sorted(_ICONS))


def has_icon(name: str) -> bool:
    return name in _ICONS


def icon_svg(name: str, color: str = "#15202B") -> str:
    """Return the raw SVG document for ``name`` coloured ``color``.

    Raises ``KeyError`` for an unknown name: a typo in a view must fail loudly
    in the test suite rather than render a blank square.
    """
    try:
        template, path = _ICONS[name]
    except KeyError as exc:
        raise KeyError(f"unknown icon {name!r}; have {sorted(_ICONS)}") from exc
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="24" height="24">{template.format(d=path, color=color)}</svg>'
    )


def icon_pixmap(name: str, color: str, size: int = 16):  # pragma: no cover - needs Qt
    """Render ``name`` to a device-pixel-ratio aware ``QPixmap``.

    Requires PySide6. Kept here (next to the path data) so the drawing code
    and the geometry cannot drift apart.
    """
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(icon_svg(name, color).encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def icon(name: str, color: str, size: int = 16):  # pragma: no cover - needs Qt
    """``QIcon`` for buttons and list items."""
    from PySide6.QtGui import QIcon

    return QIcon(icon_pixmap(name, color, size))


__all__ = ["available_icons", "has_icon", "icon", "icon_pixmap", "icon_svg"]
