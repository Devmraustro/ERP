# SPDX-License-Identifier: LGPL-3.0-only
"""Reusable widgets for the desktop client (Phase 29).

One place for every visual primitive the screens compose from: page headers,
statistic cards, buttons with a semantic variant, pills, banners, the data
table with search/filter/sort/empty/loading/error states, the empty-state
panel, the sparkline-style bar chart and the notification popover.

Design rules encoded here:

* **No literal colours.** A widget asks the :class:`~dz_erp.desktop.theme.ThemeManager`
  for a role (``"text_muted"``, ``"success"``) or lets the stylesheet do it.
* **No business logic.** These widgets render values they are given. They
  never sum, average or derive a financial figure.
* **Every visible action works.** A button is created with a callable; there
  is no "decorative" button in this module and no placeholder handler.
* **Honest states.** ``DataTable`` can be *loading*, *empty*, *filtered-empty*
  or *error*, and it says which - an empty table never silently looks like a
  broken one.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import tokens
from .formatting import format_count
from .i18n import tr

#: Column alignment, resolved per direction at paint time.
LEFT, RIGHT, CENTER = "left", "right", "center"


def _align(where: str, rtl: bool) -> Qt.AlignmentFlag:
    """Physical alignment for a logical one, mirrored for RTL."""
    horizontal = {
        CENTER: Qt.AlignmentFlag.AlignHCenter,
        RIGHT: Qt.AlignmentFlag.AlignRight if not rtl else Qt.AlignmentFlag.AlignLeft,
        LEFT: Qt.AlignmentFlag.AlignLeft if not rtl else Qt.AlignmentFlag.AlignRight,
    }[where]
    return horizontal | Qt.AlignmentFlag.AlignVCenter


# ----------------------------------------------------------------------
# small building blocks
# ----------------------------------------------------------------------


def label(text: str = "", *, object_name: str = "", wrap: bool = False) -> QLabel:
    """A ``QLabel`` that participates in the stylesheet by object name."""
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    return widget


def title(text: str) -> QLabel:
    return label(text, object_name="PageTitle")


def subtitle(text: str) -> QLabel:
    return label(text, object_name="PageSubtitle", wrap=True)


def section_title(text: str) -> QLabel:
    return label(text, object_name="SectionTitle")


def muted(text: str = "") -> QLabel:
    return label(text, object_name="Muted")


class Button(QPushButton):
    """A push button with a semantic variant and an optional icon.

    ``variant`` is one of ``default``, ``primary``, ``accent``, ``danger``,
    ``ghost``. The variant is exposed as a Qt property so the stylesheet
    picks it up; no colour is set from Python.
    """

    def __init__(
        self,
        text: str,
        *,
        variant: str = "default",
        icon=None,
        on_click: Optional[Callable[[], None]] = None,
        tooltip: str = "",
    ) -> None:
        super().__init__(text)
        self.setProperty("variant", variant)
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(tokens.ICON_SIZE["md"], tokens.ICON_SIZE["md"]))
        if on_click is not None:
            self.clicked.connect(lambda _checked=False: on_click())
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class IconButton(QPushButton):
    """A square, borderless button that carries only an icon."""

    def __init__(
        self,
        icon,
        *,
        tooltip: str,
        on_click: Optional[Callable[[], None]] = None,
        size: int = tokens.ICON_SIZE["lg"],
        object_name: str = "IconButton",
    ) -> None:
        super().__init__("")
        self.setObjectName(object_name)
        self.setIcon(icon)
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size + 12, size + 12)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if on_click is not None:
            self.clicked.connect(lambda _checked=False: on_click())


class Pill(QLabel):
    """A compact status pill: ``success``, ``warning``, ``danger``, ``info``."""

    def __init__(self, text: str = "", state: str = "neutral") -> None:
        super().__init__(text)
        self.setObjectName("Pill")
        self.set_state(state)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)


class Banner(QFrame):
    """An inline message: ``info``, ``success``, ``warning`` or ``danger``."""

    def __init__(self, text: str = "", state: str = "info") -> None:
        super().__init__()
        self.setObjectName("Banner")
        self.setProperty("state", state)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACING["md"], tokens.SPACING["sm"], tokens.SPACING["md"], tokens.SPACING["sm"]
        )
        layout.setSpacing(tokens.SPACING["sm"])
        self._icon = QLabel()
        self._icon.setFixedSize(tokens.ICON_SIZE["md"], tokens.ICON_SIZE["md"])
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._text = label(text, object_name="BannerText", wrap=True)
        layout.addWidget(self._text, 1)

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def text(self) -> str:
        return self._text.text()

    def set_icon_pixmap(self, pixmap) -> None:
        self._icon.setPixmap(pixmap)


class Card(QFrame):
    """A surface panel with a title row and a body layout."""

    def __init__(
        self,
        heading: str = "",
        *,
        object_name: str = "Card",
        body_margins: Tuple[int, int, int, int] = (16, 16, 16, 16),
    ) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(*body_margins)
        self._outer.setSpacing(tokens.SPACING["sm"])
        self._heading_row = QHBoxLayout()
        self._heading_row.setSpacing(tokens.SPACING["sm"])
        self.heading_label = section_title(heading)
        self._heading_row.addWidget(self.heading_label, 1)
        if heading:
            self._outer.addLayout(self._heading_row)
        self.body = QVBoxLayout()
        self.body.setSpacing(tokens.SPACING["sm"])
        self._outer.addLayout(self.body)

    def set_heading(self, text: str) -> None:
        if not self.heading_label.isVisible() and not self.heading_label.text():
            self._outer.insertLayout(0, self._heading_row)
        self.heading_label.setText(text)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.body.addWidget(widget, stretch)

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def add_action(self, widget: QWidget) -> None:
        """Put a small action (link-style button) at the right of the heading."""
        self._heading_row.addWidget(widget, 0)


class StatCard(QFrame):
    """One headline figure: value, label, caption, optional state colour.

    The value is text supplied by the caller - it is never computed here, so
    a tile cannot drift from the ledger it came from.
    """

    def __init__(
        self,
        stat_label: str,
        value: str = "—",
        *,
        caption: str = "",
        state: str = "neutral",
        icon=None,
    ) -> None:
        super().__init__()
        self.setObjectName("StatCard")
        self.setProperty("state", state)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACING["lg"], tokens.SPACING["md"], tokens.SPACING["lg"], tokens.SPACING["md"]
        )
        layout.setSpacing(tokens.SPACING["xxs"])
        head = QHBoxLayout()
        head.setSpacing(tokens.SPACING["xs"])
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(tokens.ICON_SIZE["md"], tokens.ICON_SIZE["md"])
        if icon is not None:
            self.icon_label.setPixmap(icon)
        head.addWidget(self.icon_label, 0)
        self.label = label(stat_label, object_name="StatLabel")
        head.addWidget(self.label, 1)
        layout.addLayout(head)
        self.value = label(value, object_name="StatValue", wrap=True)
        layout.addWidget(self.value)
        self.caption = label(caption, object_name="StatCaption", wrap=True)
        layout.addWidget(self.caption)

    def set_value(self, value: str, *, caption: str = "", state: Optional[str] = None) -> None:
        self.value.setText(value)
        if caption:
            self.caption.setText(caption)
        if state:
            self.setProperty("state", state)
            style = self.style()
            if style is not None:
                style.unpolish(self)
                style.polish(self)


class EmptyState(QWidget):
    """The truthful empty state: a title, an explanation, an optional action."""

    def __init__(
        self,
        heading: str,
        body: str = "",
        *,
        icon=None,
        action_text: str = "",
        on_action: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACING["xxl"], tokens.SPACING["xxxl"], tokens.SPACING["xxl"], tokens.SPACING["xxxl"]
        )
        layout.setSpacing(tokens.SPACING["sm"])
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon is not None:
            icon_label = QLabel()
            icon_label.setPixmap(icon)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_label)
        self.title = label(heading, object_name="EmptyStateTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.body = label(body, object_name="EmptyStateBody", wrap=True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setVisible(bool(body))
        layout.addWidget(self.body)
        self.action: Optional[Button] = None
        if action_text and on_action is not None:
            self.action = Button(action_text, variant="primary", on_click=on_action)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.action)
            row.addStretch(1)
            layout.addLayout(row)

    def set_text(self, heading: str, body: str = "") -> None:
        self.title.setText(heading)
        self.body.setText(body)
        self.body.setVisible(bool(body))


class PageHeader(QWidget):
    """Page title, subtitle and a right-aligned contextual action area."""

    def __init__(self, heading: str, subheading: str = "") -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACING["xs"])
        self.breadcrumb = label("", object_name="Breadcrumb")
        outer.addWidget(self.breadcrumb)
        row = QHBoxLayout()
        row.setSpacing(tokens.SPACING["md"])
        column = QVBoxLayout()
        column.setSpacing(tokens.SPACING["xxs"])
        self.heading = title(heading)
        column.addWidget(self.heading)
        self.subheading = subtitle(subheading)
        self.subheading.setVisible(bool(subheading))
        column.addWidget(self.subheading)
        row.addLayout(column, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(tokens.SPACING["sm"])
        row.addLayout(self.actions, 0)
        outer.addLayout(row)

    def set_heading(self, heading: str, subheading: Optional[str] = None) -> None:
        self.heading.setText(heading)
        if subheading is not None:
            self.subheading.setText(subheading)
            self.subheading.setVisible(bool(subheading))

    def set_breadcrumb(self, text: str) -> None:
        self.breadcrumb.setText(text)
        self.breadcrumb.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


# ----------------------------------------------------------------------
# data table
# ----------------------------------------------------------------------


class Column:
    """A table column: header key, alignment, optional width and formatter."""

    __slots__ = ("key", "header", "align", "width", "formatter", "row_formatter", "mono")

    def __init__(
        self,
        key: str,
        header: str,
        *,
        align: str = LEFT,
        width: int = 0,
        formatter: Optional[Callable[[Any], str]] = None,
        row_formatter: Optional[Callable[[Dict[str, Any]], str]] = None,
        mono: bool = False,
    ) -> None:
        self.key = key
        self.header = header
        self.align = align
        self.width = width
        #: ``formatter`` sees one cell's value; ``row_formatter`` sees the whole
        #: row, for cells whose text depends on more than one field (a name
        #: picked from ``name_ar``/``name_fr`` by UI language, say).
        self.formatter = formatter
        self.row_formatter = row_formatter
        self.mono = mono


PAGE_SIZE_CHOICES: Tuple[int, ...] = (50, 100, 200, 500)


class DataTable(QWidget):
    """A read-only table with search, filter, sort, paging and honest states.

    States are mutually exclusive and always communicated:

    * **loading** - a message, the table dimmed;
    * **error** - the server's detail, with a retry action if one was given;
    * **empty** - the resource genuinely has no rows (resource-specific text);
    * **no match** - rows exist but the search/filter hides them all.

    Sorting and searching are client-side over the rows already fetched from
    the verified API; the page size is bounded so a large tenant still renders
    quickly. Nothing here computes a business value.
    """

    rowActivated = Signal(int)  # index into the *visible* rows

    def __init__(
        self,
        columns: Sequence[Column],
        *,
        search_keys: Sequence[str] = (),
        filter_key: str = "",
        filter_values: Optional[Sequence[Tuple[str, str]]] = None,
        empty_title: str = "",
        empty_body: str = "",
        page_size: int = 100,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._rows: List[Dict[str, Any]] = []
        self._visible: List[Dict[str, Any]] = []
        self._sort_column: Optional[int] = None
        self._sort_desc = False
        # ``search``/``filter`` are the public names (screens show, hide and
        # populate them); the underscored ones are kept for the earlier code
        # paths and tests that already reference them.
        self._search = QLineEdit()
        self.search = self._search
        self._filter = QComboBox()
        self.filter = self._filter
        self._page = 0
        self._page_size = page_size
        self._rtl = False
        self._empty_title = empty_title
        self._empty_body = empty_body
        self._search_keys = list(search_keys)
        self._filter_key = filter_key

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACING["sm"])

        # ---- toolbar
        self.toolbar = QWidget()
        toolbar = QHBoxLayout(self.toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(tokens.SPACING["sm"])
        self._search.setObjectName("SearchBox")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        self._search.setMinimumWidth(220)
        self._search.setMaximumWidth(320)
        toolbar.addWidget(self._search, 0)
        self._filter.setMinimumWidth(150)
        self._filter.setMaximumWidth(220)
        self._filter.setVisible(bool(filter_values))
        if filter_values:
            self._filter.addItem("", "")
            for value, caption in filter_values:
                self._filter.addItem(caption, value)
        self._filter.currentIndexChanged.connect(self._on_filter)
        toolbar.addWidget(self._filter, 0)
        self._toolbar_extra = QHBoxLayout()
        self._toolbar_extra.setSpacing(tokens.SPACING["sm"])
        toolbar.addLayout(self._toolbar_extra, 0)
        toolbar.addStretch(1)
        self.count_label = muted("")
        toolbar.addWidget(self.count_label, 0)
        outer.addWidget(self.toolbar)

        # ---- table
        self.table = QTableWidget()
        self.table.setColumnCount(len(self._columns))
        self.table.setHorizontalHeaderLabels([column.header for column in self._columns])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Tab must leave the table, not walk its cells. With cell-navigation
        # enabled every cell of a 100-row table is a Tab stop, so a keyboard
        # user needs hundreds of presses to reach the control after it - and
        # the rows are not editable, so there is nothing to tab *between*.
        # Arrow keys still move the selection (the reviewer's actual need).
        self.table.setTabKeyNavigation(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # No last-section stretch: every column has a declared width and the
        # table is wide enough for them, so a "stretched" last column only
        # wasted space (or, when it did not fit, hid it behind a scrollbar).
        header.setStretchLastSection(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.table.verticalHeader().setDefaultSectionSize(tokens.ROW_HEIGHT)
        header.setFixedHeight(tokens.HEADER_HEIGHT)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.itemSelectionChanged.connect(self._emit_selection)
        self.table.viewport().installEventFilter(self)
        outer.addWidget(self.table, 1)

        # ---- states
        self.state_panel = QWidget()
        state_layout = QVBoxLayout(self.state_panel)
        state_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_state = EmptyState("", "")
        state_layout.addWidget(self.empty_state)
        self.state_panel.setVisible(False)
        outer.addWidget(self.state_panel, 1)

        # ---- footer
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(tokens.SPACING["sm"])
        self.page_label = muted("")
        footer.addWidget(self.page_label, 0)
        footer.addStretch(1)
        self.prev_button = Button("", variant="ghost", on_click=self._go_previous)
        self.next_button = Button("", variant="ghost", on_click=self._go_next)
        footer.addWidget(self.prev_button, 0)
        footer.addWidget(self.next_button, 0)
        self.footer.setVisible(False)
        outer.addWidget(self.footer)

        self._selection_callback: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None
        self._retry: Optional[Callable[[], None]] = None
        #: Compact tables are embedded in a card (recent invoices, review
        #: lines): they show rows and states but no search/filter/paging, and
        #: ``_apply_view`` must not re-show the toolbar behind the caller.
        self._compact = False
        #: When set, columns size themselves to their measured content between
        #: these bounds after every fill, and the last column takes the slack.
        self._auto_widths: Optional[Tuple[int, int]] = None
        self._last_fit_width = -1

    # -- configuration -------------------------------------------------

    def add_toolbar_widget(self, widget: QWidget) -> None:
        self._toolbar_extra.addWidget(widget)

    def on_selection(self, callback: Callable[[Optional[Dict[str, Any]]], None]) -> None:
        self._selection_callback = callback

    def set_auto_widths(self, min_width: int = 72, max_width: int = 320) -> None:
        """Measure every column's content and fit it, instead of guessing.

        Hard-coded widths elide real values - an invoice number, a NIF, a
        confidence - exactly the fields a reviewer must read in full. This
        measures the widest rendered cell (plus the header) and clamps it, so
        columns are as wide as their data and no wider.
        """
        self._auto_widths = (min_width, max_width)
        self._fit_widths()

    def eventFilter(self, watched, event) -> bool:
        """Re-fit whenever the *viewport* resizes.

        The table sits in a splitter, so the pane - not this widget - changes
        width. Measuring before that happens produces widths for a viewport
        that no longer exists, which is what elided the review columns.
        """
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_widths()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        """Re-fit on resize: measured widths need a realised viewport."""
        super().resizeEvent(event)
        self._fit_widths()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # First layout: the viewport finally has a width, so measure again -
        # queued, because the viewport is not final until the event loop has
        # laid this widget out.
        QTimer.singleShot(0, self._fit_widths)

    def _fit_widths(self) -> None:
        """Size every column to its content, then spend the spare room well.

        Measured from the model's own size hints (``resizeColumnsToContents``
        can consult a stale viewport and, offscreen, leave widths untouched).
        A column is never narrower than its content needs - an elided account
        name, invoice number, NIF or amount is a read error waiting to happen.

        Spare room is not left as dead space at the end of the table, and it
        does not go to an arbitrary column: it goes to the *prose* column (the
        one the view declared widest - an account name, a description). Charts
        and id columns stay as wide as their values.
        """
        if self.table.columnCount() == 0:
            return
        if not self.table.isVisible() or self.table.viewport().width() <= 1:
            # Nothing to distribute yet; showEvent/resizeEvent will call back.
            return
        columns = list(self._columns)
        header = self.table.horizontalHeader()
        measured = []
        for index in range(self.table.columnCount()):
            text_width = self.table.sizeHintForColumn(index)
            header_width = header.sectionSizeHint(index)
            measured.append(max(text_width, header_width) + 20)

        if self._auto_widths is not None:
            low, high = self._auto_widths
            widths = [max(low, min(width, high)) for width in measured]
            stretch_index = len(widths) - 1
        else:
            # Declared widths are both floor and ceiling: they are the space
            # each column was designed for. Missing ones fall back to 120px.
            widths = [
                max(measured[index], column.width or 120) for index, column in enumerate(columns)
            ]
            declared = [(column.width or 0, index) for index, column in enumerate(columns)]
            stretch_index = max(declared, key=lambda pair: pair[0])[1] if declared else 0

        viewport = self.table.viewport().width()
        self._last_fit_width = viewport
        spare = viewport - sum(widths)
        if spare > 0 and self._auto_widths is None:
            # Spare room goes to the prose column first, but not past a
            # readable measure.
            grow = min(spare, max(0, 520 - widths[stretch_index]))
            widths[stretch_index] += grow
            spare -= grow
        if spare > 0:
            # Whatever is left (a capped prose column, or every column in a
            # measured table) is shared in proportion to current width, so the
            # table fills its pane instead of ending in a dead band.
            base = sum(widths) or 1
            extra = [int(spare * width / base) for width in widths]
            for index, amount in enumerate(extra):
                widths[index] += amount
            widths[stretch_index] += spare - sum(extra)
        elif spare < 0:
            # Not enough room: columns keep what their content needs and the
            # table scrolls horizontally. Completeness wins over fitting.
            widths = [min(width + 24, 520) for width in widths]
        for index, width in enumerate(widths):
            self.table.setColumnWidth(index, width)

    def set_compact(self, compact: bool = True) -> None:
        """Embedded mode: no search, filter, count or paging chrome."""
        self._compact = compact
        if compact:
            self.toolbar.setVisible(False)
            self.footer.setVisible(False)
        self._apply_view()

    def set_placeholder_title(self, title: str) -> None:
        """The resource-specific empty-state title (e.g. "no OCR fields")."""
        self._empty_title = title

    def set_placeholders(self, search: str, empty_title: str = "", empty_body: str = "") -> None:
        self._search.setPlaceholderText(search)
        if empty_title:
            self._empty_title = empty_title
        if empty_body:
            self._empty_body = empty_body

    def selected_row(self) -> Optional[Dict[str, Any]]:
        indexes = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not indexes:
            return None
        row = indexes[0].row()
        if 0 <= row < len(self._visible):
            return self._visible[row]
        return None

    def visible_rows(self) -> List[Dict[str, Any]]:
        return list(self._visible)

    # -- table-surface proxies -----------------------------------------
    # ``DataTable`` *is* a table plus the states and toolbar around it, so it
    # forwards the read/selection API of the widget it wraps. Callers (and the
    # Phase 27 review-workspace assertions) can therefore read cells and
    # select rows without reaching through ``.table``, and screen code keeps
    # one object to hold. Mutating calls stay on ``.table``.

    def rowCount(self) -> int:
        return self.table.rowCount()

    def columnCount(self) -> int:
        return self.table.columnCount()

    def item(self, row: int, column: int):
        """The ``QTableWidgetItem`` at a visible row (or ``None``)."""
        return self.table.item(row, column)

    def selectRow(self, row: int) -> None:
        self.table.selectRow(row)

    def selectedItems(self):
        return self.table.selectedItems()

    # -- states --------------------------------------------------------

    def set_loading(self, message: str) -> None:
        self._retry = None
        self.table.setEnabled(False)
        self.count_label.setText(message)
        self.empty_state.set_text(message, "")
        self.empty_state.setVisible(True)
        self.state_panel.setVisible(False)

    def set_error(self, message: str, *, on_retry: Optional[Callable[[], None]] = None) -> None:
        self._rows = []
        self._visible = []
        self.table.setRowCount(0)
        self.table.setEnabled(True)
        self.count_label.setText("")
        # Same parent/child rule as the empty state: the message lives inside
        # ``state_panel``, so both must be shown or neither renders.
        self.empty_state.set_text(tr(self._lang(), "error_title"), message)
        self.empty_state.setVisible(True)
        self.state_panel.setVisible(True)
        self.table.setVisible(False)
        self._retry = on_retry

    # -- data ----------------------------------------------------------

    def set_rows(self, rows: Iterable[Dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]
        self._page = 0
        self.table.setEnabled(True)
        self._apply_view()

    def _lang(self) -> str:
        return getattr(self, "_language", "fr")

    def set_language(self, language: str) -> None:
        """Adopt a language and re-render the strings this widget owns.

        Storing the code is not enough: the row count and the page indicator
        are composed *here* (``rows_shown`` / ``page_of``), so a language
        switch that only records the code leaves them frozen in the previous
        language - which is exactly how "294 sur 294 lignes" ended up in an
        English window while the sidebar above it was already English.
        """
        self._language = language
        self._render_chrome()

    def _render_chrome(self) -> None:
        """Re-render the count and page strings for the current language.

        Uses the same filtered total ``_apply_view`` uses, so the number does
        not change when only the language does.
        """
        if not self._rows:
            self.count_label.setText("")
            return
        total = len(self._filtered_rows())
        self.count_label.setText(
            tr(self._lang(), "rows_shown", shown=format_count(total), total=format_count(len(self._rows)))
        )
        pages = max(1, (total + self._page_size - 1) // self._page_size)
        if pages > 1:
            self.page_label.setText(tr(self._lang(), "page_of", page=self._page + 1, pages=pages))

    def _filtered_rows(self) -> List[Dict[str, Any]]:
        """Rows matching the current search text and filter, in display order."""
        needle = self._search.text().strip().casefold()
        rows = [row for row in self._rows if self._matches(row, needle) and self._passed_filter(row)]
        if self._sort_column is not None:
            key = self._sort_key
            try:
                rows.sort(key=key, reverse=self._sort_desc)
            except TypeError:
                rows.sort(
                    key=lambda r: str(r.get(self._columns[self._sort_column].key, "")),
                    reverse=self._sort_desc,
                )
        return rows

    def set_direction(self, rtl: bool) -> None:
        self._rtl = rtl
        for row in range(self.table.rowCount()):
            for column_index, column in enumerate(self._columns):
                item = self.table.item(row, column_index)
                if item is not None:
                    item.setTextAlignment(_align(column.align, rtl))

    # -- filtering -----------------------------------------------------

    def _matches(self, row: Dict[str, Any], needle: str) -> bool:
        if not needle:
            return True
        for key in self._search_keys:
            value = row.get(key)
            if value is None:
                continue
            if needle in str(value).casefold():
                return True
        return False

    def _passed_filter(self, row: Dict[str, Any]) -> bool:
        wanted = self._filter.currentData()
        if not wanted or not self._filter_key:
            return True
        return str(row.get(self._filter_key, "")) == str(wanted)

    def _on_search(self, _text: str) -> None:
        self._page = 0
        self._apply_view()

    def _on_filter(self, _index: int) -> None:
        self._page = 0
        self._apply_view()

    def _on_header_clicked(self, index: int) -> None:
        if self._sort_column == index:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column = index
            self._sort_desc = False
        self._apply_view()

    def _sort_key(self, row: Dict[str, Any]):
        if self._sort_column is None:
            return 0
        column = self._columns[self._sort_column]
        raw = row.get(column.key)
        if raw is None:
            return ""
        text = str(raw)
        try:
            return (0, float(text.replace(",", ".")))
        except (TypeError, ValueError):
            return (1, text.casefold())

    def _apply_view(self) -> None:
        rows = self._filtered_rows()
        total = len(rows)
        pages = max(1, (total + self._page_size - 1) // self._page_size)
        self._page = min(self._page, pages - 1)
        start = self._page * self._page_size
        self._visible = rows[start : start + self._page_size]
        self._fill()

        self.footer.setVisible(pages > 1 and not self._compact)
        if pages > 1:
            self.page_label.setText(tr(self._lang(), "page_of", page=self._page + 1, pages=pages))
            self.prev_button.setEnabled(self._page > 0)
            self.next_button.setEnabled(self._page < pages - 1)
        if self._rows:
            self.count_label.setText(
                tr(self._lang(), "rows_shown", shown=format_count(total), total=format_count(len(self._rows)))
            )
        else:
            self.count_label.setText("")

        # An empty (or filtered-to-nothing) table hides the search/filter row:
        # a lone search box over a blank page reads as a broken control.
        # Search is still reachable - the table reappears as soon as rows
        # arrive, and the empty state explains why there are none.
        self.toolbar.setVisible(bool(self._rows) and not self._compact)
        # The empty state lives INSIDE ``state_panel``; showing it without
        # showing its parent renders nothing at all. One bool decides both.
        show_empty = not rows
        if show_empty:
            if self._rows:
                self.empty_state.set_text(tr(self._lang(), "state_no_match"), "")
            else:
                self.empty_state.set_text(self._empty_title, self._empty_body)
        self.empty_state.setVisible(show_empty)
        self.state_panel.setVisible(show_empty)
        self.table.setVisible(bool(rows))
        if self._auto_widths is not None:
            QTimer.singleShot(0, self._fit_widths)

    def _fill(self) -> None:
        self.table.setRowCount(len(self._visible))
        for row_index, row in enumerate(self._visible):
            for column_index, column in enumerate(self._columns):
                raw = row.get(column.key)
                if column.row_formatter is not None:
                    text = column.row_formatter(row)
                elif column.formatter is not None:
                    text = column.formatter(raw)
                else:
                    text = "" if raw is None else str(raw)
                item = QTableWidgetItem(text)
                item.setTextAlignment(_align(column.align, self._rtl))
                if column.mono:
                    font = QFont(item.font())
                    font.setStyleHint(QFont.StyleHint.Monospace)
                    item.setFont(font)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_index, column_index, item)
        for index, column in enumerate(self._columns):
            if column.width:
                self.table.setColumnWidth(index, column.width)
        self._fit_widths()

    def _go_previous(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._apply_view()

    def _go_next(self) -> None:
        self._page += 1
        self._apply_view()

    # -- selection -----------------------------------------------------

    def _emit_selection(self) -> None:
        if self._selection_callback is not None:
            self._selection_callback(self.selected_row())

    def _on_double_click(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._visible):
            self.rowActivated.emit(row)


# ----------------------------------------------------------------------
# chart
# ----------------------------------------------------------------------


class BarChart(QWidget):
    """A small horizontal bar chart painted with QPainter.

    Deliberately dependency-free (no QtCharts): the dashboard needs one
    honest visual - relative debit per account - and every value it draws is
    passed in already computed by the backend report. It never derives a
    number; it only scales a value to a pixel width.
    """

    def __init__(self, *, empty_text: str = "", rtl: bool = False) -> None:
        super().__init__()
        self._items: List[Tuple[str, str, float]] = []
        self._empty_text = empty_text
        #: Mapped, not inherited: the chart paints its own text, so it must be
        #: told the direction on the same language switch as every table.
        self._rtl = rtl
        # No colours of its own: they are handed in by the theme layer on every
        # ``set_items`` call. A widget that keeps a literal colour drifts out of
        # the design system the moment the theme changes.
        self._colors: Dict[str, str] = {}
        self._bar_color = QColor()
        self._text_color = QColor()
        self._grid_color = QColor()
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_items(self, items: Iterable[Tuple[str, str, float]], colors: Dict[str, str]) -> None:
        """``items`` are ``(label, value_text, value)`` - all pre-formatted.

        ``colors`` must come from the theme layer (``bar``, ``text``, ``grid``);
        a missing role leaves that element unpainted rather than guessing a
        colour from a different design system.
        """
        self._items = list(items)
        self._colors = dict(colors)
        self._bar_color = QColor(colors.get("bar", ""))
        self._text_color = QColor(colors.get("text", ""))
        self._grid_color = QColor(colors.get("grid", ""))
        self.update()

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.update()

    def set_direction(self, rtl: bool) -> None:
        """Mirror the painted label/value edges for a right-to-left language."""
        self._rtl = rtl
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(8, 8, -8, -8)
        if not self._items:
            painter.setPen(QPen(self._text_color))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._empty_text)
            painter.end()
            return
        maximum = max((value for _label, _text, value in self._items), default=0.0) or 1.0
        row_height = max(18, rect.height() // max(len(self._items), 1))
        # The label carries "code · name"; a column of bare account codes is
        # unreadable without the ledger beside it. The label column takes a
        # fixed share of the width so the bar keeps most of it.
        label_width = max(96, int(rect.width() * 0.34))
        value_width = 108
        bar_left = rect.left() + label_width
        bar_width = max(1, rect.width() - label_width - value_width)
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 0.5))
        painter.setFont(font)
        for index, (item_label, value_text, value) in enumerate(self._items):
            top = rect.top() + index * row_height
            painter.setPen(QPen(self._text_color))
            painter.drawText(
                QRectish(rect.left(), top, label_width - 6, row_height),
                Qt.AlignmentFlag.AlignVCenter
                | (
                    Qt.AlignmentFlag.AlignRight
                    if self._rtl
                    else Qt.AlignmentFlag.AlignLeft
                ),
                painter.fontMetrics().elidedText(
                    item_label, Qt.TextElideMode.ElideRight, max(1, label_width - 6)
                ),
            )
            width = int(bar_width * (value / maximum)) if maximum else 0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._bar_color)
            painter.drawRoundedRect(
                QRectish(bar_left, top + row_height // 2 - 6, max(width, 2), 12), 3, 3
            )
            painter.setPen(QPen(self._text_color))
            painter.drawText(
                QRectish(rect.left() + rect.width() - value_width, top, value_width - 4, row_height),
                Qt.AlignmentFlag.AlignVCenter
                | (
                    Qt.AlignmentFlag.AlignLeft
                    if self._rtl
                    else Qt.AlignmentFlag.AlignRight
                ),
                value_text,
            )
        painter.setPen(QPen(self._grid_color))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.end()


def QRectish(x: int, y: int, w: int, h: int):
    """``QRect`` without importing it at module scope (keeps import cost flat)."""
    from PySide6.QtCore import QRect

    return QRect(x, y, max(1, w), max(1, h))


# ----------------------------------------------------------------------
# notification popover
# ----------------------------------------------------------------------


class NotificationList(QWidget):
    """The alerts panel behind the top-bar bell: a list, or an honest empty."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACING["md"], tokens.SPACING["md"], tokens.SPACING["md"], tokens.SPACING["md"])
        layout.setSpacing(tokens.SPACING["sm"])
        self._heading = section_title("")
        layout.addWidget(self._heading)
        self._body = QVBoxLayout()
        self._body.setSpacing(tokens.SPACING["xs"])
        layout.addLayout(self._body)
        self._empty = muted("")
        self._empty.setWordWrap(True)
        layout.addWidget(self._empty)

    def set_items(self, heading: str, items: Iterable[Tuple[str, str]], empty_text: str) -> None:
        self._heading.setText(heading)
        while self._body.count():
            item = self._body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rows = list(items)
        self._empty.setVisible(not rows)
        self._empty.setText(empty_text)
        for severity, text in rows:
            pill = Pill("", state=severity)
            pill.setText("●")
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(tokens.SPACING["sm"])
            row_layout.addWidget(pill, 0)
            message = label(text, wrap=True)
            row_layout.addWidget(message, 1)
            self._body.addWidget(row)


class ScrollPage(QWidget):
    """A page whose content scrolls vertically (dashboard, long forms)."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.content = QWidget()
        self.content.setObjectName("Page")
        self.layout_ = QVBoxLayout(self.content)
        self.layout_.setContentsMargins(
            tokens.SPACING["xxl"], tokens.SPACING["xl"], tokens.SPACING["xxl"], tokens.SPACING["xxl"]
        )
        self.layout_.setSpacing(tokens.SPACING["lg"])
        self.scroll.setWidget(self.content)


__all__ = [
    "CENTER",
    "LEFT",
    "RIGHT",
    "PAGE_SIZE_CHOICES",
    "Banner",
    "BarChart",
    "Button",
    "Card",
    "Column",
    "DataTable",
    "EmptyState",
    "IconButton",
    "NotificationList",
    "PageHeader",
    "Pill",
    "ScrollPage",
    "StatCard",
    "label",
    "muted",
    "section_title",
    "subtitle",
    "title",
]
