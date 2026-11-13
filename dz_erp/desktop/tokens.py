# SPDX-License-Identifier: LGPL-3.0-only
"""Design tokens and the application stylesheet (Phase 29).

Why this module exists
----------------------
The Phase 25-28 client styled itself with six inline ``setStyleSheet`` calls
scattered through the widgets. A colour changed in one dialog and not in
another, and a dark theme was impossible without editing every view. This
module is the single source of truth for the visual system:

* **Tokens** - colour, spacing, radius, type scale, icon size - as plain
  Python values with names that describe intent (``"surface"``,
  ``"text_muted"``, ``"danger"``), never appearance.
* **Palettes** - one :class:`Palette` per theme (``light``, ``dark``). A view
  never names a colour; it names a *role*, and the role resolves through the
  palette that is currently active.
* **Stylesheet** - :func:`build_stylesheet` renders the QSS once, from the
  active palette and the active layout direction, and the application sets it
  on ``QApplication``. Dialogs and tables inherit it; nothing is styled
  locally.

The module is deliberately **Qt-free**. It can be imported, unit-tested and
linted on the headless CI matrix where PySide6 is not installed, which keeps
the design system covered by the same suite as the rest of the code.

Direction matters
-----------------
Qt's stylesheet language has no logical properties: ``border-left`` stays on
the physical left even when the layout is right-to-left. The selected
navigation row and the accent rules therefore take the direction as an
argument and emit the mirrored edge for Arabic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

# ----------------------------------------------------------------------
# Scales (theme independent)
# ----------------------------------------------------------------------

#: 4-pixel base grid. Every margin, padding and gap in the UI is one of these.
SPACING: Mapping[str, int] = {
    "xxs": 2,
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 20,
    "xxl": 24,
    "xxxl": 32,
}

RADIUS: Mapping[str, int] = {"sm": 4, "md": 6, "lg": 10, "pill": 999}

#: Type scale in points. Enterprise density: body text is 10-11 pt so a
#: 1280x800 window shows a useful number of rows without zooming out.
FONT_SIZE: Mapping[str, int] = {
    "caption": 9,
    "small": 10,
    "body": 10,
    "label": 10,
    "subtitle": 11,
    "title": 14,
    "page": 16,
    "stat": 20,
}

ICON_SIZE: Mapping[str, int] = {"sm": 14, "md": 16, "lg": 20, "xl": 28}

SIDEBAR_WIDTH = 232
SIDEBAR_COLLAPSED_WIDTH = 64
TOPBAR_HEIGHT = 56
ROW_HEIGHT = 30
HEADER_HEIGHT = 34

#: Font stacks. The first family that exists on the machine wins; Segoe UI is
#: the Windows default, Noto/DejaVu cover Linux, and the Arabic stack asks for
#: a Naskh-capable face before falling back to the same families.
FONT_FAMILIES_LTR: Tuple[str, ...] = ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", "sans-serif")
FONT_FAMILIES_RTL: Tuple[str, ...] = (
    "Segoe UI",
    "Noto Naskh Arabic",
    "Noto Sans Arabic",
    "DejaVu Sans",
    "sans-serif",
)
MONO_FAMILIES: Tuple[str, ...] = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace")


# ----------------------------------------------------------------------
# Palettes
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Palette:
    """One theme.

    Role names are *semantic*: a widget asks for ``surface`` or ``danger``,
    never for ``#ffffff``. Swapping the palette re-themes the whole product.
    """

    name: str
    # structure
    background: str
    surface: str
    surface_alt: str
    surface_raised: str
    border: str
    border_strong: str
    divider: str
    # text
    text: str
    text_muted: str
    text_inverse: str
    text_disabled: str
    #: Foreground for text painted *on* ``primary`` (a filled primary button,
    #: a selected nav row). Separate from ``text_inverse`` because a brand
    #: colour can sit on either side of the light/dark crossover: white on the
    #: light theme's navy, near-black on the dark theme's blue.
    text_on_primary: str
    #: Foreground for text on ``accent``/``danger`` fills.
    text_on_accent: str
    # brand
    primary: str
    primary_hover: str
    primary_pressed: str
    primary_soft: str
    accent: str
    accent_hover: str
    accent_soft: str
    # states
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    info: str
    info_soft: str
    # chrome
    sidebar_bg: str
    sidebar_text: str
    sidebar_text_muted: str
    sidebar_active_bg: str
    sidebar_active_text: str
    topbar_bg: str
    selection_bg: str
    selection_text: str
    row_alt: str
    grid: str
    shadow: str

    def color(self, role: str) -> str:
        """Resolve a semantic role name to a hex colour."""
        try:
            return getattr(self, role)  # type: ignore[no-any-return]
        except AttributeError as exc:  # pragma: no cover - programming error
            raise KeyError(f"unknown colour role {role!r}") from exc


LIGHT = Palette(
    name="light",
    background="#F3F5F7",
    surface="#FFFFFF",
    surface_alt="#F8FAFB",
    surface_raised="#FFFFFF",
    border="#D5DBE2",
    border_strong="#B9C2CC",
    divider="#E4E9EE",
    text="#15202B",
    text_muted="#5B6875",
    text_inverse="#FFFFFF",
    # 3.32:1 on surface_alt. WCAG exempts disabled controls, but 2.42:1 (the
    # previous value) was faint enough to be unreadable for low-vision users,
    # and a disabled control still has to say *which* action is unavailable.
    text_disabled="#7E8B99",
    text_on_primary="#FFFFFF",
    text_on_accent="#FFFFFF",
    primary="#16324F",
    primary_hover="#1E4266",
    primary_pressed="#102740",
    primary_soft="#E7EDF4",
    accent="#B3282D",
    accent_hover="#961F24",
    accent_soft="#F7E7E8",
    success="#1B6B45",
    success_soft="#E4F2EA",
    warning="#8A5A00",
    warning_soft="#FBF0D9",
    danger="#B3261E",
    danger_soft="#FBE9E7",
    info="#1B5E9E",
    info_soft="#E6F0FA",
    sidebar_bg="#16324F",
    sidebar_text="#E9EEF4",
    sidebar_text_muted="#A9B8C8",
    sidebar_active_bg="#1F4368",
    sidebar_active_text="#FFFFFF",
    topbar_bg="#FFFFFF",
    selection_bg="#DCE8F4",
    selection_text="#10233A",
    row_alt="#F7F9FB",
    grid="#E1E6EC",
    shadow="#0F1B26",
)

DARK = Palette(
    name="dark",
    background="#11161C",
    surface="#171E26",
    surface_alt="#1B232D",
    surface_raised="#1E2732",
    border="#2B3745",
    border_strong="#3C4A5A",
    divider="#232E3A",
    text="#E4EBF2",
    text_muted="#98A6B5",
    text_inverse="#0F151B",
    text_disabled="#66737F",
    text_on_primary="#FFFFFF",
    text_on_accent="#0F151B",
    primary="#2E6CA4",
    # Hover/pressed deepen the fill: with white text on a #2E6CA4 base,
    # lightening would *reduce* contrast (measured: 5.53 -> 3.77 -> 2.20).
    primary_hover="#2A6294",
    primary_pressed="#24547E",
    primary_soft="#1B2A3A",
    accent="#E0605F",
    accent_hover="#EC7B7A",
    accent_soft="#33201F",
    success="#4BB287",
    success_soft="#16291F",
    warning="#D8A13C",
    warning_soft="#2C2415",
    danger="#E5695F",
    danger_soft="#331B18",
    info="#5AA0DC",
    info_soft="#16232F",
    sidebar_bg="#0D1218",
    sidebar_text="#DCE5EE",
    sidebar_text_muted="#8C9BAA",
    sidebar_active_bg="#1D2A38",
    sidebar_active_text="#FFFFFF",
    topbar_bg="#151C24",
    selection_bg="#22364B",
    selection_text="#EAF1F7",
    row_alt="#1A222B",
    grid="#26313D",
    shadow="#000000",
)

PALETTES: Mapping[str, Palette] = {"light": LIGHT, "dark": DARK}
THEMES: Tuple[str, ...] = tuple(PALETTES)


def get_palette(theme: str) -> Palette:
    """Palette for ``theme``; unknown names fall back to light, never crash."""
    return PALETTES.get(theme, LIGHT)


def resolve_theme(theme: str) -> str:
    """Normalised theme name (``light`` unless a known dark value is given)."""
    value = (theme or "").strip().lower()
    return value if value in PALETTES else "light"


# ----------------------------------------------------------------------
# Stylesheet
# ----------------------------------------------------------------------


def build_palette(theme: str = "light"):
    """The QPalette matching ``theme``.

    The stylesheet styles everything this project draws, but Qt still paints
    a handful of things from the *palette* rather than QSS: text in widgets
    created after the stylesheet was installed, placeholder text, disabled
    text, selection colours, and native-drawn editors. Without this, a dialog
    opened while the dark theme is active renders black-on-white. Both
    sources are derived from the same :class:`Palette`, so they cannot drift.

    Imported lazily: this module stays Qt-free at import time so the design
    tokens can be inspected (and tested) without a Qt install.
    """
    from PySide6.QtGui import QColor, QPalette

    p = get_palette(theme)

    def c(value: str) -> "QColor":
        return QColor(value)

    palette = QPalette()
    roles = QPalette.ColorRole
    groups = (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    )
    for group in groups:
        disabled = group == QPalette.ColorGroup.Disabled
        window = p.surface_alt if not disabled else p.surface_alt
        palette.setColor(group, roles.Window, c(window))
        palette.setColor(group, roles.WindowText, c(p.text_disabled if disabled else p.text))
        palette.setColor(group, roles.Base, c(p.surface))
        palette.setColor(group, roles.AlternateBase, c(p.row_alt))
        palette.setColor(group, roles.Text, c(p.text_disabled if disabled else p.text))
        palette.setColor(group, roles.PlaceholderText, c(p.text_muted))
        palette.setColor(group, roles.Button, c(p.surface))
        palette.setColor(group, roles.ButtonText, c(p.text_disabled if disabled else p.text))
        palette.setColor(group, roles.ToolTipBase, c(p.surface_raised))
        palette.setColor(group, roles.ToolTipText, c(p.text))
        palette.setColor(group, roles.Highlight, c(p.selection_bg))
        palette.setColor(group, roles.HighlightedText, c(p.selection_text))
        palette.setColor(group, roles.Light, c(p.surface_raised))
        palette.setColor(group, roles.Mid, c(p.border))
        palette.setColor(group, roles.Dark, c(p.border_strong))
        palette.setColor(group, roles.Shadow, c(p.shadow))
        palette.setColor(group, roles.Link, c(p.primary))
        palette.setColor(group, roles.LinkVisited, c(p.primary_pressed))
    return palette


def _families(names: Tuple[str, ...]) -> str:
    return ", ".join(f'"{name}"' for name in names)


def build_stylesheet(theme: str = "light", *, rtl: bool = False) -> str:
    """Render the full application stylesheet.

    Every selector uses an object name or a Qt class, so a view only has to
    give its widget the right ``setObjectName`` and it inherits the system.
    ``rtl`` mirrors the accent edges that QSS cannot mirror by itself.
    """
    p = get_palette(theme)
    f = FONT_SIZE
    fam = _families(FONT_FAMILIES_RTL if rtl else FONT_FAMILIES_LTR)
    mono = _families(MONO_FAMILIES)
    nav_edge = "border-right" if rtl else "border-left"
    text_align = "right" if rtl else "left"

    return f"""
/* ---------- base ---------- */
QWidget {{
    color: {p.text};
    font-family: {fam};
    font-size: {f['body']}pt;
}}
QMainWindow, QWidget#AppRoot, QDialog {{
    background: {p.background};
}}
QWidget#Page {{
    background: {p.background};
}}
QToolTip {{
    background: {p.surface_raised};
    color: {p.text};
    border: 1px solid {p.border};
    padding: 4px 6px;
}}
QLabel {{
    background: transparent;
}}
QLabel#PageTitle {{
    font-size: {f['page']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel#PageSubtitle {{
    font-size: {f['small']}pt;
    color: {p.text_muted};
}}
QLabel#SectionTitle {{
    font-size: {f['subtitle']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel#FieldLabel {{
    font-size: {f['small']}pt;
    color: {p.text_muted};
}}
QLabel#FieldValue {{
    font-size: {f['body']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel#FieldValueMono {{
    font-family: {mono};
    font-size: {f['body']}pt;
    color: {p.text};
}}
QLabel#Muted {{
    color: {p.text_muted};
    font-size: {f['small']}pt;
}}
QLabel#ErrorLabel {{
    color: {p.danger};
    font-size: {f['small']}pt;
}}
QLabel#StatValue {{
    font-size: {f['stat']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel#StatLabel {{
    font-size: {f['small']}pt;
    color: {p.text_muted};
}}
QLabel#StatCaption {{
    font-size: {f['caption']}pt;
    color: {p.text_muted};
}}
QLabel#Breadcrumb {{
    font-size: {f['caption']}pt;
    color: {p.text_muted};
}}
QLabel#EmptyStateTitle {{
    font-size: {f['subtitle']}pt;
    font-weight: 600;
    color: {p.text};
}}
QLabel#EmptyStateBody {{
    font-size: {f['small']}pt;
    color: {p.text_muted};
}}

/* ---------- shell ---------- */
QFrame#Sidebar {{
    background: {p.sidebar_bg};
    border: none;
}}
QLabel#BrandMark {{
    color: {p.sidebar_active_text};
    font-size: {f['subtitle']}pt;
    font-weight: 700;
}}
QLabel#BrandSub {{
    color: {p.sidebar_text_muted};
    font-size: {f['caption']}pt;
}}
QListWidget#NavList {{
    background: transparent;
    border: none;
    outline: 0;
    padding: 4px 6px;
}}
QListWidget#NavList::item {{
    color: {p.sidebar_text};
    padding: 8px 10px;
    margin: 1px 0;
    border-radius: {RADIUS['md']}px;
    {nav_edge}: 3px solid transparent;
}}
QListWidget#NavList::item:hover {{
    background: {p.sidebar_active_bg};
    color: {p.sidebar_active_text};
}}
QListWidget#NavList::item:selected {{
    background: {p.sidebar_active_bg};
    color: {p.sidebar_active_text};
    font-weight: 600;
    {nav_edge}: 3px solid {p.accent};
}}
QFrame#TopBar {{
    background: {p.topbar_bg};
    border: none;
    border-bottom: 1px solid {p.border};
}}
QFrame#TopBarDivider {{
    background: {p.divider};
    max-width: 1px;
    min-width: 1px;
}}
QLabel#CompanyName {{
    font-size: {f['subtitle']}pt;
    font-weight: 600;
}}
QLabel#UserChip {{
    font-size: {f['small']}pt;
    color: {p.text_muted};
}}
QFrame#Workspace {{
    background: {p.background};
    border: none;
}}

/* ---------- containers ---------- */
QFrame#Card {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['lg']}px;
}}
QFrame#CardFlat {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
}}
QFrame#StatCard {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS['lg']}px;
}}
QFrame#StatCard[state="success"] {{ border-{text_align}: 3px solid {p.success}; }}
QFrame#StatCard[state="warning"] {{ border-{text_align}: 3px solid {p.warning}; }}
QFrame#StatCard[state="danger"]  {{ border-{text_align}: 3px solid {p.danger}; }}
QFrame#StatCard[state="info"]    {{ border-{text_align}: 3px solid {p.info}; }}
QFrame#StatCard[state="accent"]  {{ border-{text_align}: 3px solid {p.accent}; }}
QFrame#StatCard[state="neutral"] {{ border-{text_align}: 3px solid {p.border_strong}; }}
QFrame#Divider {{
    background: {p.divider};
    max-height: 1px;
    min-height: 1px;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS['md']}px;
    padding: 6px 12px;
    min-height: 16px;
}}
QPushButton:hover {{
    background: {p.surface_alt};
    border-color: {p.primary};
}}
QPushButton:pressed {{
    background: {p.primary_soft};
}}
QPushButton:disabled {{
    color: {p.text_disabled};
    border-color: {p.border};
    background: {p.surface_alt};
}}
QPushButton:focus {{
    border-color: {p.primary};
}}
QPushButton[variant="primary"] {{
    background: {p.primary};
    color: {p.text_on_primary};
    border: 1px solid {p.primary};
    font-weight: 600;
}}
/* Qt resolves equal-specificity rules by source order, so every variant
   needs its own :disabled rule *after* its base rule - otherwise a disabled
   primary/accent button paints exactly like an armed one. */
QPushButton[variant="primary"]:disabled {{
    background: {p.surface_alt};
    color: {p.text_disabled};
    border-color: {p.border};
}}
QPushButton[variant="primary"]:hover {{
    background: {p.primary_hover};
    border-color: {p.primary_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background: {p.primary_pressed};
}}
QPushButton[variant="danger"] {{
    background: {p.danger};
    color: {p.text_on_accent};
    border: 1px solid {p.danger};
    font-weight: 600;
}}
QPushButton[variant="danger"]:hover {{
    background: {p.accent_hover};
}}
QPushButton[variant="danger"]:disabled {{
    background: {p.surface_alt};
    color: {p.text_disabled};
    border-color: {p.border};
}}
QPushButton[variant="accent"] {{
    background: {p.accent};
    color: {p.text_on_accent};
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QPushButton[variant="accent"]:hover {{
    background: {p.accent_hover};
}}
QPushButton[variant="accent"]:disabled {{
    background: {p.surface_alt};
    color: {p.text_disabled};
    border-color: {p.border};
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    border: 1px solid transparent;
    color: {p.text};
}}
QPushButton[variant="ghost"]:hover {{
    background: {p.surface_alt};
    border-color: {p.border};
}}
QPushButton[variant="ghost"]:disabled {{
    color: {p.text_disabled};
    border-color: transparent;
}}
QPushButton#IconButton, QToolButton#IconButton {{
    background: transparent;
    border: none;
    padding: 4px;
    border-radius: {RADIUS['sm']}px;
}}
QPushButton#IconButton:hover, QToolButton#IconButton:hover {{
    background: {p.primary_soft};
}}
QPushButton#SidebarToggle {{
    background: transparent;
    border: none;
    padding: 6px;
    border-radius: {RADIUS['sm']}px;
}}
QPushButton#SidebarToggle:hover {{
    background: {p.sidebar_active_bg};
}}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS['md']}px;
    padding: 5px 8px;
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_text};
    min-height: 18px;
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {p.primary};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background: {p.surface_alt};
    color: {p.text_disabled};
}}
QLineEdit#SearchBox {{
    padding: {SPACING['xs']}px {SPACING['sm']}px;
    background: {p.surface};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: 1px solid {p.border};
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_text};
    outline: 0;
}}
QCheckBox {{
    spacing: 6px;
}}

/* ---------- tables ---------- */
QTableWidget {{
    background: {p.surface};
    alternate-background-color: {p.row_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
    gridline-color: {p.grid};
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_text};
    outline: 0;
}}
QTableWidget::item {{
    padding: 4px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {p.selection_bg};
    color: {p.selection_text};
}}
QHeaderView::section {{
    background: {p.surface_alt};
    color: {p.text_muted};
    border: none;
    border-bottom: 1px solid {p.border};
    border-{text_align}: 0;
    padding: 6px 8px;
    font-size: {f['small']}pt;
    font-weight: 600;
}}
QHeaderView::section:hover {{
    background: {p.primary_soft};
    color: {p.text};
}}
QTableCornerButton::section {{
    background: {p.surface_alt};
    border: none;
}}

/* ---------- scrolling ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.text_muted};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
    background: {p.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    padding: 7px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: {f['small']}pt;
}}
QTabBar::tab:selected {{
    color: {p.text};
    border-bottom: 2px solid {p.accent};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {p.text};
}}

/* ---------- pills / badges ---------- */
QLabel#Pill {{
    border-radius: {RADIUS['sm']}px;
    padding: 2px 8px;
    font-size: {f['caption']}pt;
    font-weight: 600;
}}
QLabel#Pill[state="neutral"] {{ background: {p.surface_alt}; color: {p.text_muted}; border: 1px solid {p.border}; }}
QLabel#Pill[state="success"] {{ background: {p.success_soft}; color: {p.success}; }}
QLabel#Pill[state="warning"] {{ background: {p.warning_soft}; color: {p.warning}; }}
QLabel#Pill[state="danger"]  {{ background: {p.danger_soft}; color: {p.danger}; }}
QLabel#Pill[state="info"]    {{ background: {p.info_soft}; color: {p.info}; }}
QLabel#Pill[state="accent"]  {{ background: {p.accent_soft}; color: {p.accent}; }}

/* ---------- banners ---------- */
QFrame#Banner {{
    border-radius: {RADIUS['md']}px;
    padding: 8px 10px;
}}
QFrame#Banner[state="info"]    {{ background: {p.info_soft}; border: 1px solid {p.info}; }}
QFrame#Banner[state="success"] {{ background: {p.success_soft}; border: 1px solid {p.success}; }}
QFrame#Banner[state="warning"] {{ background: {p.warning_soft}; border: 1px solid {p.warning}; }}
QFrame#Banner[state="danger"]  {{ background: {p.danger_soft}; border: 1px solid {p.danger}; }}
QLabel#BannerText {{ background: transparent; font-size: {f['small']}pt; }}

/* ---------- menus ---------- */
QMenuBar {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
    padding: 2px 4px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 10px;
    border-radius: {RADIUS['sm']}px;
}}
QMenuBar::item:selected {{
    background: {p.primary_soft};
}}
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px;
    border-radius: {RADIUS['sm']}px;
}}
QMenu::item:selected {{
    background: {p.selection_bg};
    color: {p.selection_text};
}}
QMenu::separator {{
    height: 1px;
    background: {p.divider};
    margin: 4px 8px;
}}

/* ---------- status bar ---------- */
QStatusBar {{
    background: {p.surface};
    border-top: 1px solid {p.border};
    color: {p.text_muted};
    font-size: {f['small']}pt;
}}
QStatusBar::item {{
    border: none;
}}

/* ---------- dialog chrome ---------- */
QDialog QLabel#DialogTitle {{
    font-size: {f['title']}pt;
    font-weight: 600;
}}
QFrame#DialogHeader {{
    background: {p.surface};
    border-bottom: 1px solid {p.border};
}}
QFrame#DialogFooter {{
    background: {p.surface};
    border-top: 1px solid {p.border};
}}
QGroupBox {{
    border: 1px solid {p.border};
    border-radius: {RADIUS['md']}px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {p.text_muted};
}}
QProgressBar {{
    background: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {RADIUS['sm']}px;
    text-align: center;
    height: 8px;
}}
QProgressBar::chunk {{
    background: {p.primary};
    border-radius: {RADIUS['sm']}px;
}}
"""


#: Object names a view may use to opt into a style. Kept as data so the
#: Phase 29 test can assert that every styled selector is intentional and
#: that no view hardcodes a colour.
STYLEABLE_OBJECTS: Tuple[str, ...] = (
    "AppRoot",
    "Page",
    "Sidebar",
    "TopBar",
    "Workspace",
    "NavList",
    "BrandMark",
    "BrandSub",
    "PageTitle",
    "PageSubtitle",
    "SectionTitle",
    "FieldLabel",
    "FieldValue",
    "FieldValueMono",
    "Muted",
    "ErrorLabel",
    "StatValue",
    "StatLabel",
    "StatCaption",
    "Card",
    "CardFlat",
    "StatCard",
    "Divider",
    "IconButton",
    "SidebarToggle",
    "SearchBox",
    "Pill",
    "Banner",
    "BannerText",
    "CompanyName",
    "UserChip",
    "Breadcrumb",
    "EmptyStateTitle",
    "EmptyStateBody",
    "DialogTitle",
    "DialogHeader",
    "DialogFooter",
)


def palette_contrast_notes() -> Dict[str, float]:
    """Rough relative-luminance contrast of text on surface, per theme.

    Used by the Phase 29 test to assert the palettes stay readable; it is a
    spot check on the two most important pairs, not a WCAG audit tool.
    """

    def luminance(hex_color: str) -> float:
        value = hex_color.lstrip("#")
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(a: str, b: str) -> float:
        la, lb = luminance(a), luminance(b)
        high, low = max(la, lb), min(la, lb)
        return (high + 0.05) / (low + 0.05)

    return {
        f"{name}_text_on_surface": ratio(p.text, p.surface)
        for name, p in PALETTES.items()
    }


__all__ = [
    "DARK",
    "FONT_FAMILIES_LTR",
    "FONT_FAMILIES_RTL",
    "FONT_SIZE",
    "ICON_SIZE",
    "LIGHT",
    "PALETTES",
    "Palette",
    "RADIUS",
    "ROW_HEIGHT",
    "SIDEBAR_COLLAPSED_WIDTH",
    "SIDEBAR_WIDTH",
    "SPACING",
    "STYLEABLE_OBJECTS",
    "THEMES",
    "TOPBAR_HEIGHT",
    "build_stylesheet",
    "get_palette",
    "palette_contrast_notes",
    "resolve_theme",
]
