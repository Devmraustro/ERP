# SPDX-License-Identifier: LGPL-3.0-only
"""Application shell for the desktop client (Phase 29).

The shell is the frame every screen lives in:

    +----------------+--------------------------------------------------+
    |                |  top bar: breadcrumb · company · alerts · lang   |
    |   sidebar      |           theme · user                           |
    |   (collapsible)+--------------------------------------------------+
    |   nav groups   |                                                  |
    |   brand/mode   |  workspace (page header + scrollable content)     |
    +----------------+--------------------------------------------------+

Design decisions worth stating:

* **The navigation list keeps one row per view, in :data:`VIEWS` order.**
  Phase 25-28 mapped a nav row to a stack page by index and hid the users row
  for non-admins; the redesign keeps that contract so no route or permission
  behaviour changes, and adds *group labels* as disabled, non-selectable rows
  drawn through a delegate. Collapsing hides the labels and the text, never
  the rows themselves.
* **The language and theme selectors are real controls** wired to the
  application: choosing a language reloads the catalogue and flips the layout
  direction; choosing a theme re-applies the palettes and the icons. Both are
  persisted through ``desktop.config`` (whitelist, no secrets).
* **The alert bell reports what the data actually says** - documents awaiting
  review and invoices awaiting a decision, both counted from the verified API
  reads. When there is nothing to report it says so instead of inventing a
  "0 new notifications" badge.
* **The user chip is the server-resolved session principal**, never a name
  the client typed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import tokens
from .components import IconButton, NotificationList, label
from .i18n import LANGUAGE_NAMES, LANGUAGES, tr

#: Item-data role carrying the heading text painted *above* a view row.
_GROUP_LABEL_ROLE = Qt.ItemDataRole.UserRole + 41

#: Pixels reserved above a row when it opens a navigation group.
_GROUP_LABEL_HEIGHT = 26


class _NavDelegate(QStyledItemDelegate):
    """Paints sidebar rows in both directions without literal colours.

    A row may open a group: the heading is painted inside the row's upper
    band and the entry itself sits below it. Keeping the heading *inside* a
    view row is what lets the list hold exactly one row per view, so the
    nav widget's row index stays equal to the view index - the contract the
    Phase 26-28 tests (and the previous QListWidget) rely on.
    """

    def __init__(self, theme_manager, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme_manager

    def _direction(self) -> Qt.AlignmentFlag:
        return Qt.AlignmentFlag.AlignRight if self._theme.rtl else Qt.AlignmentFlag.AlignLeft

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        group_label = index.data(_GROUP_LABEL_ROLE) or ""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        pad = 12

        if group_label:
            font = QFont(option.font)
            font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
            font.setBold(True)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
            painter.setFont(font)
            painter.setPen(QColor(self._theme.color("sidebar_text_muted")))
            painter.drawText(
                rect.adjusted(pad, 6, -pad, -(rect.height() - _GROUP_LABEL_HEIGHT)),
                Qt.AlignmentFlag.AlignTop | self._direction(),
                group_label,
            )
            rect = rect.adjusted(0, _GROUP_LABEL_HEIGHT, 0, 0)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._theme.color("sidebar_active_bg")))
            painter.drawRoundedRect(
                rect.adjusted(6, 1, -6, -1), tokens.RADIUS["md"], tokens.RADIUS["md"]
            )
            if selected:
                painter.setBrush(QColor(self._theme.color("accent")))
                width = 3
                x = rect.right() - width - 7 if self._theme.rtl else rect.left() + 7
                painter.drawRoundedRect(x, rect.top() + 7, width, rect.height() - 14, 1.5, 1.5)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        icon_w = tokens.ICON_SIZE["md"]
        available = rect.width() - pad * 2 - icon_w - 8
        if icon is not None and not icon.isNull():
            pixmap = icon.pixmap(QSize(icon_w, icon_w))
            icon_x = rect.right() - pad - icon_w if self._theme.rtl else rect.left() + pad
            painter.drawPixmap(icon_x, rect.center().y() - icon_w // 2, pixmap)
            text_rect = (
                rect.adjusted(pad, 0, -(pad + icon_w + 8), 0)
                if self._theme.rtl
                else rect.adjusted(pad + icon_w + 8, 0, -pad, 0)
            )
        else:
            text_rect = rect.adjusted(pad, 0, -pad, 0)
        font = QFont(option.font)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(
            QColor(
                self._theme.color(
                    "sidebar_active_text" if (selected or hovered) else "sidebar_text"
                )
            )
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | self._direction(),
            painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(available, 20)),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index):
        hint = super().sizeHint(option, index)
        height = 36
        if index.data(_GROUP_LABEL_ROLE):
            height += _GROUP_LABEL_HEIGHT
        hint.setHeight(height)
        return hint


#: View key -> icon name. The view keys are routing identifiers ("suppliers"),
#: the icon set is domain vocabulary ("partners"); the alias keeps both spelled
#: the way they belong without renaming a route.
_VIEW_ICONS: Dict[str, str] = {
    "dashboard": "dashboard",
    "accounts": "accounts",
    "products": "products",
    "suppliers": "partners",
    "stock": "stock",
    "documents": "documents",
    "invoices": "invoices",
    "trial_balance": "trial_balance",
    "users": "users",
}


class Sidebar(QFrame):
    """Collapsible navigation rail with grouped entries and a brand block.

    **Row contract.** The list holds exactly one row per view, in
    :data:`VIEWS` order, so ``nav.item(i)`` and ``nav.isRowHidden(i)`` refer to
    ``VIEWS[i]``. Group headings are painted by the delegate on the first row
    of each group instead of occupying rows of their own: a heading row would
    shift every index and silently break route→view mapping.
    """

    viewSelected = Signal(int)
    toggleRequested = Signal()

    def __init__(self, theme_manager, views: Sequence[str]) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self._theme = theme_manager
        self._views = list(views)
        self._collapsed = False
        self._row_for_view: Dict[str, int] = {}
        #: Views hidden for the current role; kept so a re-population (language
        #: or theme switch) does not resurrect a row the role may not see.
        self._hidden_views: Dict[str, bool] = {}
        self.setMinimumWidth(tokens.SIDEBAR_WIDTH)
        self.setMaximumWidth(tokens.SIDEBAR_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(14, 16, 10, 12)
        brand_row.setSpacing(10)
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(28, 28)
        self.brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_icon.setPixmap(theme_manager.pixmap("logo", "sidebar_text", 26))
        brand_row.addWidget(self.brand_icon, 0)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.brand_title = label(tr(theme_manager.language, "app_short"), object_name="BrandMark")
        self.brand_sub = label(tr(theme_manager.language, "app_suite"), object_name="BrandSub")
        self.brand_sub.setWordWrap(True)
        brand_text.addWidget(self.brand_title)
        brand_text.addWidget(self.brand_sub)
        brand_row.addLayout(brand_text, 1)
        self.brand_block = QWidget()
        self.brand_block.setLayout(brand_row)
        layout.addWidget(self.brand_block)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setItemDelegate(_NavDelegate(theme_manager, self.nav))
        self.nav.setUniformItemSizes(False)
        self.nav.setMouseTracking(True)
        self.nav.setIconSize(QSize(tokens.ICON_SIZE["md"], tokens.ICON_SIZE["md"]))
        layout.addWidget(self.nav, 1)

        footer = QWidget()
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(10, 8, 10, 12)
        footer_row.setSpacing(8)
        self.mode_label = label("", object_name="BrandSub")
        self.mode_label.setWordWrap(True)
        footer_row.addWidget(self.mode_label, 1)
        self.collapse_button = QPushButton()
        self.collapse_button.setObjectName("SidebarToggle")
        self.collapse_button.setFixedSize(28, 28)
        self.collapse_button.clicked.connect(lambda: self.toggleRequested.emit())
        footer_row.addWidget(self.collapse_button, 0)
        self.footer = footer
        layout.addWidget(footer)

        self.populate(theme_manager.language)

    # -- population ----------------------------------------------------

    def groups(self, language: str) -> List[Tuple[str, Tuple[str, ...]]]:
        """``(group_key, view_keys)``; the group heading precedes its views.

        The group view lists must stay contiguous in ``VIEWS`` order: the
        sidebar holds one row per view in registry order, so a group split
        across the registry would put a heading in the wrong place.
        """
        return [
            ("nav_group_operations", ("dashboard", "documents", "invoices")),
            ("nav_group_accounting", ("accounts", "trial_balance", "products", "stock", "suppliers")),
            ("nav_group_admin", ("users",)),
        ]

    def populate(self, language: str) -> None:
        """Rebuild one row per view, preserving the selection by view key."""
        current = self.current_view()
        self.nav.blockSignals(True)
        self.nav.clear()
        self._row_for_view = {}
        for group_key, view_keys in self.groups(language):
            for position, view in enumerate(view_keys):
                if view not in self._views:
                    continue
                item = QListWidgetItem()
                item.setText(tr(language, f"nav_{view}"))
                item.setIcon(
                    self._theme.icon(
                        _VIEW_ICONS.get(view, "dashboard"), "sidebar_text", tokens.ICON_SIZE["md"]
                    )
                )
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(tr(language, f"nav_{view}"))
                if position == 0:
                    item.setData(_GROUP_LABEL_ROLE, tr(language, group_key))
                self._row_for_view[view] = self.nav.count()
                self.nav.addItem(item)
        self.nav.blockSignals(False)
        target = current if current in self._row_for_view else self._views[0]
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(self._row_for_view[target])
        self.nav.blockSignals(False)
        self._sync_collapsed()

    def current_view(self) -> Optional[str]:
        row = self.nav.currentRow()
        for view, view_row in self._row_for_view.items():
            if view_row == row:
                return view
        return None

    def select(self, view: str) -> None:
        row = self._row_for_view.get(view)
        if row is not None and row != self.nav.currentRow():
            self.nav.setCurrentRow(row)

    def row_for_view(self, view: str) -> int:
        return self._row_for_view.get(view, -1)

    def set_view_hidden(self, view: str, hidden: bool) -> None:
        """Hide (never remove) a view's row, so indices stay stable."""
        self._hidden_views[view] = hidden
        row = self._row_for_view.get(view)
        if row is not None:
            self.nav.setRowHidden(row, hidden)

    def is_view_hidden(self, view: str) -> bool:
        row = self._row_for_view.get(view)
        return self.nav.isRowHidden(row) if row is not None else True

    # -- collapse ------------------------------------------------------

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._sync_collapsed()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._sync_collapsed()

    def _sync_collapsed(self) -> None:
        language = self._theme.language
        width = tokens.SIDEBAR_COLLAPSED_WIDTH if self._collapsed else tokens.SIDEBAR_WIDTH
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        self.brand_block.setVisible(not self._collapsed)
        self.mode_label.setVisible(not self._collapsed)
        for view, row in self._row_for_view.items():
            self.nav.setRowHidden(row, self._hidden_views.get(view, False))
        # Collapsed rows drop the in-row group heading and keep the icon only.
        self._render_group_labels(language)
        self.nav.doItemsLayout()
        self.collapse_button.setToolTip(
            tr(language, "shell_expand" if self._collapsed else "shell_collapse")
        )
        self.collapse_button.setText("»" if self._collapsed else "«")

    def _render_group_labels(self, language: str) -> None:
        """(Re)apply the in-row headings, honouring the collapsed state."""
        for group_key, view_keys in self.groups(language):
            for position, view in enumerate(view_keys):
                row = self._row_for_view.get(view)
                if row is None:
                    continue
                item = self.nav.item(row)
                if item is None:
                    continue
                item.setData(
                    _GROUP_LABEL_ROLE,
                    None if (self._collapsed or position > 0) else tr(language, group_key),
                )
                item.setText(tr(language, f"nav_{view}"))
                item.setToolTip(tr(language, f"nav_{view}"))

    def retranslate(self, language: str) -> None:
        """Language switch: relabel rows in place (selection and routes stay)."""
        self.brand_title.setText(tr(language, "app_short"))
        self.brand_sub.setText(tr(language, "app_suite"))
        self._render_group_labels(language)
        self.nav.doItemsLayout()
        self._sync_collapsed()

    def remember_hidden_views(self) -> None:
        self._hidden_views = {
            view: self.nav.isRowHidden(row) for view, row in self._row_for_view.items()
        }


class TopBar(QFrame):
    """Breadcrumb, company identity, alerts, language, theme and user chip."""

    themeChanged = Signal(str)
    languageChanged = Signal(str)
    signOutRequested = Signal()
    alertsRequested = Signal()

    def __init__(self, theme_manager) -> None:
        super().__init__()
        self.setObjectName("TopBar")
        self.setFixedHeight(tokens.TOPBAR_HEIGHT)
        self._theme = theme_manager
        self._company = ""
        self._username = ""
        self._role = ""
        self._alerts: List[Tuple[str, str]] = []
        self._alert_popup: Optional[QFrame] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(10)

        self.breadcrumb = label("", object_name="Breadcrumb")
        layout.addWidget(self.breadcrumb, 0)

        self.company_label = label("", object_name="CompanyName")
        layout.addWidget(self.company_label, 0)
        layout.addStretch(1)

        # -- alerts
        self.alert_button = QToolButton()
        self.alert_button.setObjectName("IconButton")
        self.alert_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.alert_button.setFixedSize(32, 32)
        self.alert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alert_button.clicked.connect(self._toggle_alerts)
        layout.addWidget(self.alert_button, 0)

        self._divider(layout)

        # -- language
        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(112)
        for code in LANGUAGES:
            self.language_combo.addItem(LANGUAGE_NAMES[code], code)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(theme_manager.language)))
        self.language_combo.currentIndexChanged.connect(self._on_language)
        layout.addWidget(self.language_combo, 0)

        # -- theme
        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(104)
        self.theme_combo.currentIndexChanged.connect(self._on_theme)
        layout.addWidget(self.theme_combo, 0)

        self._divider(layout)

        self.user_label = label("", object_name="UserChip")
        layout.addWidget(self.user_label, 0)
        self.sign_out_button = IconButton(
            self._theme.icon("logout", "text_muted", tokens.ICON_SIZE["md"]),
            tooltip=tr(theme_manager.language, "shell_signout"),
            on_click=lambda: self.signOutRequested.emit(),
        )
        layout.addWidget(self.sign_out_button, 0)

        self.retranslate()

    def _divider(self, layout: QHBoxLayout) -> None:
        divider = QFrame()
        divider.setObjectName("TopBarDivider")
        divider.setFixedWidth(1)
        divider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout.addWidget(divider, 0)

    # -- state ---------------------------------------------------------

    def set_company(self, company: str) -> None:
        self._company = company
        self.company_label.setText(company)
        self.company_label.setVisible(bool(company))

    def set_session(self, username: str, role: str) -> None:
        self._username = username
        self._role = role
        language = self._theme.language
        self.user_label.setText(tr(language, "user_chip", username=username, role=role))
        self.user_label.setVisible(bool(username))

    def set_breadcrumb(self, text: str) -> None:
        self.breadcrumb.setText(text)

    def set_alerts(self, alerts: Sequence[Tuple[str, str]]) -> None:
        self._alerts = list(alerts)
        self.alert_button.setToolTip(
            tr(self._theme.language, "shell_notifications") + (f" ({len(self._alerts)})" if self._alerts else "")
        )
        self._refresh_alert_icon()
        if self._alert_popup is not None and self._alert_popup.isVisible():
            self._render_alerts()

    def alert_count(self) -> int:
        return len(self._alerts)

    def _refresh_alert_icon(self) -> None:
        role = "accent" if self._alerts else "text_muted"
        self.alert_button.setIcon(self._theme.icon("bell", role, tokens.ICON_SIZE["lg"]))

    def _render_alerts(self) -> None:
        if self._alert_popup is None:
            return
        panel = self._alert_popup.findChild(NotificationList)
        if panel is not None:
            self.retranslate_popup(panel)

    def retranslate_popup(self, panel: NotificationList) -> None:
        language = self._theme.language
        panel.set_items(
            tr(language, "shell_notifications_title"),
            self._alerts,
            tr(language, "shell_notifications_empty"),
        )

    def _toggle_alerts(self) -> None:
        if self._alert_popup is not None and self._alert_popup.isVisible():
            self._alert_popup.hide()
            return
        if self._alert_popup is None:
            popup = QFrame(self, Qt.WindowType.Popup)
            popup.setObjectName("Card")
            popup.setMinimumWidth(320)
            popup.setMaximumWidth(420)
            outer = QVBoxLayout(popup)
            outer.setContentsMargins(0, 0, 0, 0)
            self._alert_panel = NotificationList()
            outer.addWidget(self._alert_panel)
            self._alert_popup = popup
        self.retranslate_popup(self._alert_panel)
        point = self.alert_button.mapToGlobal(self.alert_button.rect().bottomLeft())
        self._alert_popup.move(point)
        self._alert_popup.show()

    # -- selectors -----------------------------------------------------

    def _on_language(self, _index: int) -> None:
        code = str(self.language_combo.currentData() or "")
        if code and code != self._theme.language:
            self.languageChanged.emit(code)

    def _on_theme(self, _index: int) -> None:
        code = str(self.theme_combo.currentData() or "")
        if code and code != self._theme.theme:
            self.themeChanged.emit(code)

    # -- i18n ----------------------------------------------------------

    def retranslate(self) -> None:
        language = self._theme.language
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        self.theme_combo.addItem(tr(language, "theme_light"), "light")
        self.theme_combo.addItem(tr(language, "theme_dark"), "dark")
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self._theme.theme)))
        self.theme_combo.blockSignals(False)
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(language)))
        self.language_combo.blockSignals(False)
        self.alert_button.setToolTip(tr(language, "shell_notifications"))
        self.sign_out_button.setToolTip(tr(language, "shell_signout"))
        if self._username:
            self.set_session(self._username, self._role)


class AppShell(QWidget):
    """Sidebar + top bar + workspace, wired together.

    ``MainWindow`` owns one of these. It exposes the pieces the screens need
    and never contains business logic itself.
    """

    viewSelected = Signal(str)

    def __init__(self, theme_manager, views: Sequence[str]) -> None:
        super().__init__()
        self.setObjectName("AppRoot")
        self._theme = theme_manager
        self._views = list(views)
        self.sidebar = Sidebar(theme_manager, self._views)
        self.topbar = TopBar(theme_manager)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar, 0)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.topbar, 0)
        self.workspace = QFrame()
        self.workspace.setObjectName("Workspace")
        self.workspace_layout = QVBoxLayout(self.workspace)
        self.workspace_layout.setContentsMargins(
            tokens.SPACING["xxl"], tokens.SPACING["lg"], tokens.SPACING["xxl"], tokens.SPACING["lg"]
        )
        self.workspace_layout.setSpacing(tokens.SPACING["lg"])
        right.addWidget(self.workspace, 1)
        root.addLayout(right, 1)

        self.sidebar.nav.currentRowChanged.connect(self._on_row_changed)
        self.sidebar.toggleRequested.connect(self.sidebar.toggle_collapsed)

    def _on_row_changed(self, _row: int) -> None:
        view = self.sidebar.current_view()
        if view:
            self.viewSelected.emit(view)

    def set_content(self, widget: QWidget) -> None:
        """Replace the workspace content with ``widget``."""
        while self.workspace_layout.count():
            item = self.workspace_layout.takeAt(0)
            old = item.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        self.workspace_layout.addWidget(widget, 1)

    def retranslate(self) -> None:
        self.sidebar.remember_hidden_views()
        self.sidebar.populate(self._theme.language)
        self.topbar.retranslate()
        self.sidebar.brand_title.setText(tr(self._theme.language, "app_short"))
        self.sidebar.brand_sub.setText(tr(self._theme.language, "app_suite"))


__all__ = ["AppShell", "Sidebar", "TopBar"]
