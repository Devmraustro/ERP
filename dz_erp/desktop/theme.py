# SPDX-License-Identifier: LGPL-3.0-only
"""Theme application for a live Qt application (Phase 29).

``tokens`` owns the *design*; this module owns the *application*. It knows how
to put a palette and a direction onto a running ``QApplication`` and how to
propagate the change to widgets that were built before the switch:

* the application stylesheet is replaced wholesale (never patched), so no
  stale colour can survive a theme change;
* the matching ``QPalette`` is installed as well, because Qt paints
  placeholders, disabled text and widgets created after the switch from the
  palette rather than from QSS;
* ``polish()`` is re-run on every widget so property selectors
  (``QFrame#StatCard[state="warning"]``) are re-evaluated;
* icons that were rendered with the old palette colour are re-requested.

Colours reach widgets through the stylesheet and through this module only -
no view calls ``setStyleSheet`` with a literal colour.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

from . import icons, tokens


class ThemeManager:
    """Holds the active theme and direction, and applies them to Qt.

    The manager is intentionally tiny and stateful-free beyond two strings: it
    is created once by :func:`dz_erp.desktop.app.run_gui` and passed to the
    windows and dialogs that need to re-theme themselves.
    """

    def __init__(self, theme: str = "light", language: str = "fr") -> None:
        self._theme = tokens.resolve_theme(theme)
        self._language = language
        self._icon_cache: Dict[tuple, object] = {}
        self._listeners: list[Callable[[], None]] = []

    # -- state ---------------------------------------------------------

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def language(self) -> str:
        return self._language

    @property
    def palette(self) -> tokens.Palette:
        return tokens.get_palette(self._theme)

    @property
    def rtl(self) -> bool:
        from .i18n import is_rtl

        return is_rtl(self._language)

    def stylesheet(self) -> str:
        return tokens.build_stylesheet(self._theme, rtl=self.rtl)

    def color(self, role: str) -> str:
        return self.palette.color(role)

    # -- icons ---------------------------------------------------------

    def icon(self, name: str, role: str = "text", size: int = 16):  # pragma: no cover - Qt
        """``QIcon`` for ``name`` in the colour of ``role``, cached per theme."""
        key = (name, role, size, self._theme)
        if key not in self._icon_cache:
            self._icon_cache[key] = icons.icon(name, self.color(role), size)
        return self._icon_cache[key]

    def pixmap(self, name: str, role: str = "text", size: int = 16):  # pragma: no cover - Qt
        return icons.icon_pixmap(name, self.color(role), size)

    # -- application ---------------------------------------------------

    def apply(self, *, language: Optional[str] = None, theme: Optional[str] = None) -> None:  # pragma: no cover - Qt
        """Push the current theme + direction onto the running application."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        if language is not None:
            self._language = language
        if theme is not None:
            self._theme = tokens.resolve_theme(theme)
        app = QApplication.instance()
        if app is None:
            return
        app.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if self.rtl else Qt.LayoutDirection.LeftToRight
        )
        # Palette first, then stylesheet: the stylesheet is what most widgets
        # are drawn from, but Qt falls back to the palette for placeholders,
        # disabled text and widgets constructed after this call. Installing
        # both keeps a dialog opened later from rendering in OS colours.
        app.setPalette(tokens.build_palette(self._theme))
        app.setStyleSheet(self.stylesheet())
        self._icon_cache.clear()
        for widget in app.allWidgets():
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
            widget.update()
        for listener in list(self._listeners):
            listener()

    def subscribe(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    # -- persistence ---------------------------------------------------

    def persisted(self) -> Dict[str, str]:
        """The two preferences this manager owns, for the config whitelist."""
        return {"theme": self._theme, "language": self._language}


def apply_theme(widget, theme: str, *, rtl: bool = False) -> None:  # pragma: no cover - Qt
    """Apply the stylesheet to one widget subtree (used by standalone dialogs)."""
    if widget is not None:
        widget.setStyleSheet(tokens.build_stylesheet(theme, rtl=rtl))


def icon_names_for_views(views: Iterable[str]) -> Dict[str, str]:
    """Map view keys to icon names, so nav and views cannot disagree."""
    return {view: view for view in views}


__all__ = ["ThemeManager", "apply_theme", "icon_names_for_views"]
