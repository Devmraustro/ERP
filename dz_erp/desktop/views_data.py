# SPDX-License-Identifier: LGPL-3.0-only
"""List and report screens (Phase 29).

Each screen follows the same four-part shape:

    PageHeader   title · subtitle · breadcrumb · contextual actions
    summary      a short, factual line (row count, ledger totals)
    DataTable    search · filter · sort · paging · empty / loading / error
    footer       source note

Nothing here computes an accounting value. Amounts are rendered by
:mod:`dz_erp.desktop.formatting`, which only groups the digits of the exact
decimal strings the backend produced. Sorting and filtering are client-side
over rows already fetched from the RBAC-wrapped endpoints, so the server stays
the single authority on *what* the rows are and the client only decides *how*
to present them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import tokens
from .components import (
    Banner,
    Button,
    Column,
    DataTable,
    PageHeader,
    StatCard,
    label,
    muted,
)
from .formatting import (
    format_amount,
    format_datetime,
    format_quantity,
    format_row_count,
    humanize_identifier,
    localized_name,
)
from .i18n import tr
from .workers import WorkerOwner


def _money(value: Any) -> str:
    return format_amount(value, currency="DZD")


class _ListScreen(QWidget, WorkerOwner):
    """Shared behaviour: load one endpoint, render it, surface failures."""

    #: endpoint method name on ``ErpClient``
    endpoint = ""
    #: catalogue key for the (resource-specific) empty state
    empty_key = ""
    #: catalogue keys for the state badge, option list
    title_key = ""
    subtitle_key = ""

    def __init__(self, theme_manager, client) -> None:
        super().__init__()
        self._theme = theme_manager
        self._client = client
        self._init_workers()
        self._payload_key = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            tokens.SPACING["xxl"], tokens.SPACING["xl"], tokens.SPACING["xxl"], tokens.SPACING["lg"]
        )
        outer.setSpacing(tokens.SPACING["md"])

        self.header = PageHeader("")
        outer.addWidget(self.header)

        self.summary = label("", object_name="Muted")
        outer.addWidget(self.summary)

        self.banner = Banner("", "warning")
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        self.table = self._build_table()
        outer.addWidget(self.table, 1)

        self.footer = muted("")
        outer.addWidget(self.footer)

        self.retranslate()

    # -- subclasses ----------------------------------------------------

    def _build_table(self) -> DataTable:  # pragma: no cover - overridden
        raise NotImplementedError

    def _columns(self, language: str) -> List[Column]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _payload_rows(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(payload.get(self._payload_key) or [])

    # -- i18n ----------------------------------------------------------

    def retranslate(self) -> None:
        language = self._theme.language
        self.header.set_heading(tr(language, self.title_key), tr(language, self.subtitle_key))
        self._rebuild_columns(language)
        self.table.set_placeholders(tr(language, "shell_search"), tr(language, self.empty_key))
        self.table.set_language(language)
        self.table.set_direction(self._theme.rtl)
        self.footer.setText(self._footer_text(language))

    def _footer_text(self, language: str) -> str:
        return tr(language, "dash_source_note")

    def _rebuild_columns(self, language: str) -> None:
        columns = self._columns(language)
        self.table.table.setColumnCount(len(columns))
        self.table.table.setHorizontalHeaderLabels([column.header for column in columns])
        self.table._columns = columns
        self.table._apply_view()

    # -- loading -------------------------------------------------------

    def refresh(self) -> None:
        if not self._client.is_logged_in:
            return
        self.table.set_loading(tr(self._theme.language, "loading"))
        self.run_async(
            getattr(self._client, self.endpoint),
            on_success=self._apply,
            on_error=self._failed,
        )

    def _apply(self, payload: Dict[str, Any]) -> None:
        rows = self._payload_rows(payload or {})
        self.table.set_rows(rows)
        self.banner.setVisible(False)
        self.summary.setText(self._summary_text(payload or {}, len(rows)))

    def _summary_text(self, payload: Dict[str, Any], row_count: int) -> str:
        language = self._theme.language
        return tr(language, "rows_counted", count=format_row_count(row_count, language))

    def _failed(self, exc: object) -> None:
        language = self._theme.language
        message = tr(language, "state_load_failed", detail=str(exc))
        self.table.set_error(message)
        self.summary.setText("")


# ----------------------------------------------------------------------
# chart of accounts
# ----------------------------------------------------------------------


class AccountsView(_ListScreen):
    endpoint = "accounts"
    title_key = "nav_accounts"
    subtitle_key = "page_accounts_sub"
    empty_key = "state_empty_accounts"

    def __init__(self, theme_manager, client) -> None:
        super().__init__(theme_manager, client)
        self._payload_key = "accounts"

    def _build_table(self) -> DataTable:
        table = DataTable([], search_keys=("code", "name_fr", "name_ar", "account_type"))
        table.filter.setVisible(False)
        return table

    def _columns(self, language: str) -> List[Column]:
        return [
            Column("code", tr(language, "col_code"), width=110, mono=True),
            Column(
                "name_fr",
                tr(language, "col_name"),
                row_formatter=localized_name(language),
                width=360,
            ),
            Column("account_type", tr(language, "col_type"), width=170, formatter=humanize_identifier),
        ]

    def _summary_text(self, payload: Dict[str, Any], row_count: int) -> str:
        language = self._theme.language
        return tr(language, "rows_counted", count=format_row_count(row_count, language))


# ----------------------------------------------------------------------
# products
# ----------------------------------------------------------------------


class ProductsView(_ListScreen):
    endpoint = "products"
    title_key = "nav_products"
    subtitle_key = "page_products_sub"
    empty_key = "state_empty_products"

    def __init__(self, theme_manager, client) -> None:
        super().__init__(theme_manager, client)
        self._payload_key = "products"

    def _build_table(self) -> DataTable:
        table = DataTable([], search_keys=("sku", "name", "uom"))
        table.filter.setVisible(False)
        return table

    def _columns(self, language: str) -> List[Column]:
        return [
            Column("sku", tr(language, "col_sku"), width=150, mono=True),
            Column("name", tr(language, "col_name"), width=380),
            Column("uom", tr(language, "col_uom"), width=90),
            Column("standard_cost_dzd", tr(language, "col_cost"), align="right", width=160, formatter=_money),
        ]


# ----------------------------------------------------------------------
# partners
# ----------------------------------------------------------------------


class PartnersView(_ListScreen):
    endpoint = "partners"
    title_key = "nav_suppliers"
    subtitle_key = "page_suppliers_sub"
    empty_key = "state_empty_partners"

    def __init__(self, theme_manager, client) -> None:
        super().__init__(theme_manager, client)
        self._payload_key = "partners"

    def _build_table(self) -> DataTable:
        table = DataTable([], search_keys=("name", "nif", "wilaya_code"), filter_key="partner_type")
        table.filter.setMinimumWidth(160)
        return table

    def _columns(self, language: str) -> List[Column]:
        return [
            Column("name", tr(language, "col_name"), width=340),
            Column("partner_type", tr(language, "col_partner_type"), width=120, formatter=humanize_identifier),
            Column("tax_regime", tr(language, "col_tax_regime"), width=140, formatter=humanize_identifier),
            Column("nif", tr(language, "col_nif"), width=180, mono=True),
            Column("wilaya_code", tr(language, "col_wilaya"), width=90, align="center"),
        ]

    def _rebuild_columns(self, language: str) -> None:
        super()._rebuild_columns(language)
        # Filter options are values from the data, labelled generically so the
        # UI never invents a partner taxonomy the backend did not send.
        current = self.table.filter.currentData()
        self.table.filter.blockSignals(True)
        self.table.filter.clear()
        self.table.filter.addItem(tr(language, "button_filter_all"), "")
        for value in ("supplier", "customer", "both"):
            self.table.filter.addItem(humanize_identifier(value), value)
        index = self.table.filter.findData(current) if current else 0
        self.table.filter.setCurrentIndex(max(0, index))
        self.table.filter.blockSignals(False)


# ----------------------------------------------------------------------
# stock
# ----------------------------------------------------------------------


class StockView(_ListScreen):
    endpoint = "stock"
    title_key = "nav_stock"
    subtitle_key = "page_stock_sub"
    empty_key = "state_empty_stock"

    def __init__(self, theme_manager, client) -> None:
        super().__init__(theme_manager, client)
        self._payload_key = "stock"

    def _build_table(self) -> DataTable:
        table = DataTable(
            [],
            search_keys=("sku", "product_name", "warehouse_code"),
            filter_key="warehouse_code",
        )
        table.filter.setMinimumWidth(170)
        return table

    def _columns(self, language: str) -> List[Column]:
        return [
            Column("warehouse_code", tr(language, "col_warehouse"), width=140, mono=True),
            Column("sku", tr(language, "col_sku"), width=150, mono=True),
            Column("product_name", tr(language, "col_product"), width=320),
            Column("uom", tr(language, "col_uom"), width=90),
            Column("qty_on_hand", tr(language, "col_qty"), align="right", width=140, formatter=format_quantity),
        ]

    def _apply(self, payload: Dict[str, Any]) -> None:
        super()._apply(payload)
        # Warehouse filter options come from the rows themselves.
        warehouses = sorted({str(row.get("warehouse_code")) for row in (payload.get("stock") or [])})
        current = self.table.filter.currentData()
        self.table.filter.blockSignals(True)
        self.table.filter.clear()
        self.table.filter.addItem(tr(self._theme.language, "button_filter_all"), "")
        for warehouse in warehouses:
            self.table.filter.addItem(warehouse, warehouse)
        index = self.table.filter.findData(current) if current else 0
        self.table.filter.setCurrentIndex(max(0, index))
        self.table.filter.blockSignals(False)


# ----------------------------------------------------------------------
# documents
# ----------------------------------------------------------------------


class DocumentsView(_ListScreen):
    endpoint = "documents"
    upload_button: Optional[Button] = None
    title_key = "nav_documents"
    subtitle_key = "page_documents_sub"
    empty_key = "state_empty_documents"

    def __init__(self, theme_manager, client) -> None:
        super().__init__(theme_manager, client)
        self._payload_key = "documents"
        self._on_upload = None

    def set_upload_handler(self, handler) -> None:
        """Wire the page-level upload action (owned by MainWindow)."""
        self._on_upload = handler
        self.upload_button = Button(
            tr(self._theme.language, "button_upload"),
            variant="primary",
            icon=self._theme.icon("upload", "text_inverse", tokens.ICON_SIZE["md"]),
            on_click=handler,
        )
        self.header.add_action(self.upload_button)

    def _build_table(self) -> DataTable:
        table = DataTable(
            [],
            search_keys=("filename", "media_type", "workflow_state"),
            filter_key="workflow_state",
        )
        table.filter.setMinimumWidth(170)
        return table

    def _columns(self, language: str) -> List[Column]:
        return [
            Column("filename", tr(language, "col_filename"), width=320),
            Column("mime_type", tr(language, "col_type"), width=150, mono=True),
            Column("byte_size", tr(language, "col_size"), align="right", width=110),
            Column("workflow_state", tr(language, "col_state"), width=140, formatter=humanize_identifier),
            Column("uploaded_at", tr(language, "col_uploaded"), width=150, formatter=format_datetime),
        ]

    def _apply(self, payload: Dict[str, Any]) -> None:
        super()._apply(payload)
        states = sorted({str(row.get("workflow_state")) for row in (payload.get("documents") or []) if row.get("workflow_state")})
        current = self.table.filter.currentData()
        self.table.filter.blockSignals(True)
        self.table.filter.clear()
        self.table.filter.addItem(tr(self._theme.language, "button_filter_all"), "")
        for state in states:
            self.table.filter.addItem(humanize_identifier(state), state)
        index = self.table.filter.findData(current) if current else 0
        self.table.filter.setCurrentIndex(max(0, index))
        self.table.filter.blockSignals(False)

    def retranslate(self) -> None:
        super().retranslate()
        if self.upload_button is not None:
            self.upload_button.setText(tr(self._theme.language, "button_upload"))


# ----------------------------------------------------------------------
# trial balance
# ----------------------------------------------------------------------


class TrialBalanceView(QWidget, WorkerOwner):
    """The ledger report, with debit/credit totals rendered verbatim."""

    def __init__(self, theme_manager, client) -> None:
        super().__init__()
        self._theme = theme_manager
        self._client = client
        self._init_workers()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            tokens.SPACING["xxl"], tokens.SPACING["xl"], tokens.SPACING["xxl"], tokens.SPACING["lg"]
        )
        outer.setSpacing(tokens.SPACING["md"])

        self.header = PageHeader("")
        self.refresh_button = Button(
            "",
            icon=theme_manager.icon("refresh", "text", tokens.ICON_SIZE["md"]),
            on_click=self.refresh,
        )
        self.header.add_action(self.refresh_button)
        outer.addWidget(self.header)

        totals = QHBoxLayout()
        totals.setSpacing(tokens.SPACING["md"])
        self.stat_debit = StatCard("", icon=theme_manager.pixmap("coins", "success", 16))
        self.stat_credit = StatCard("", icon=theme_manager.pixmap("coins", "info", 16))
        self.stat_balanced = StatCard("", icon=theme_manager.pixmap("shield", "primary", 16))
        for tile in (self.stat_debit, self.stat_credit, self.stat_balanced):
            totals.addWidget(tile, 1)
        outer.addLayout(totals)

        self.banner = Banner("", "info")
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        self.table = DataTable([], search_keys=("account_code", "name_fr", "name_ar"))
        self.table.filter.setVisible(False)
        outer.addWidget(self.table, 1)

        self.footer = muted("")
        outer.addWidget(self.footer)

        self.retranslate()

    def retranslate(self) -> None:
        language = self._theme.language
        self.header.set_heading(tr(language, "nav_trial_balance"), tr(language, "page_tb_sub"))
        self.refresh_button.setText(tr(language, "button_refresh"))
        self.stat_debit.label.setText(tr(language, "tb_total_debit"))
        self.stat_credit.label.setText(tr(language, "tb_total_credit"))
        self.stat_balanced.label.setText(tr(language, "dash_ledger_balanced"))
        columns = [
            Column("account_code", tr(language, "col_code"), width=110, mono=True),
            Column(
                "name_fr",
                tr(language, "col_name"),
                width=400,
                row_formatter=localized_name(language),
            ),
            Column("debit_dzd", tr(language, "col_debit"), align="right", width=170, formatter=_money),
            Column("credit_dzd", tr(language, "col_credit"), align="right", width=170, formatter=_money),
        ]
        self.table.table.setColumnCount(len(columns))
        self.table.table.setHorizontalHeaderLabels([column.header for column in columns])
        self.table._columns = columns
        self.table.set_placeholders(tr(language, "shell_search"), tr(language, "tb_empty"))
        self.table.set_language(language)
        self.table.set_direction(self._theme.rtl)
        self.footer.setText(tr(language, "dash_source_note"))

    def refresh(self) -> None:
        if not self._client.is_logged_in:
            return
        self.table.set_loading(tr(self._theme.language, "loading"))
        self.run_async(self._client.trial_balance, on_success=self._apply, on_error=self._failed)

    def _apply(self, payload: Dict[str, Any]) -> None:
        language = self._theme.language
        rows = list(payload.get("rows") or [])
        self.table.set_rows(rows)
        self.stat_debit.set_value(format_amount(payload.get("total_debit_dzd"), currency="DZD"))
        self.stat_credit.set_value(format_amount(payload.get("total_credit_dzd"), currency="DZD"))
        balanced = bool(payload.get("balanced"))
        # The tile label already names the check, so the value is the verdict
        # as a short word - a full sentence overflows the card and repeats the
        # label underneath it.
        self.stat_balanced.set_value(
            tr(language, "tb_verdict_ok") if balanced else tr(language, "tb_verdict_ko"),
            caption=tr(language, "tb_balanced") if balanced else tr(language, "tb_unbalanced"),
            state="success" if balanced else "danger",
        )
        # No banner for the empty ledger: the table already says "no posted
        # entries yet" in its own empty state. Two copies of one sentence on
        # one screen is noise, not emphasis. The banner is reserved for a
        # failure the table cannot express.
        self.banner.setVisible(False)

    def _failed(self, exc: object) -> None:
        language = self._theme.language
        message = tr(language, "state_load_failed", detail=str(exc))
        self.table.set_error(message, on_retry=self.refresh)
        self.stat_debit.set_value("—", state="warning")
        self.stat_credit.set_value("—", state="warning")
        self.stat_balanced.set_value("—", state="warning")
        self.banner.set_text(message)
        self.banner.setVisible(True)


# ----------------------------------------------------------------------
# users
# ----------------------------------------------------------------------


class UsersView(_ListScreen):
    endpoint = "users"
    create_button: Optional[Button] = None
    deactivate_button: Optional[Button] = None
    title_key = "nav_users"
    subtitle_key = "page_users_sub"
    empty_key = "state_empty_users"

    def __init__(self, theme_manager, client) -> None:
        super().__init__(theme_manager, client)
        self._payload_key = "users"
        self._user_ids: List[int] = []

    def set_actions(self, on_create, on_deactivate) -> None:
        language = self._theme.language
        self.create_button = Button(
            tr(language, "users_create"),
            variant="primary",
            icon=self._theme.icon("plus", "text_inverse", tokens.ICON_SIZE["md"]),
            on_click=on_create,
        )
        self.deactivate_button = Button(
            tr(language, "users_deactivate"),
            variant="ghost",
            on_click=on_deactivate,
        )
        self.header.add_action(self.deactivate_button)
        self.header.add_action(self.create_button)

    def _build_table(self) -> DataTable:
        table = DataTable([], search_keys=("username", "role"), filter_key="is_active")
        table.filter.setMinimumWidth(160)
        table.filter.addItem("", "")
        table.on_selection(lambda _row: None)
        return table

    def _columns(self, language: str) -> List[Column]:
        return [
            Column("username", tr(language, "col_username"), width=240, mono=True),
            # Roles are authorization identifiers, not prose: shown verbatim,
            # never title-cased or translated.
            Column("role", tr(language, "col_role"), width=140, mono=True),
            # Column key is ``status_text``, a display proxy computed in
            # ``_apply`` - the raw ``is_active`` flag is never overwritten.
            Column("status_text", tr(language, "col_status"), width=140),
            Column("password_updated_at", tr(language, "col_password_updated"), width=170, formatter=format_datetime),
        ]

    def _apply(self, payload: Dict[str, Any]) -> None:
        self._user_ids = [
            int(row["id"]) for row in (payload.get("users") or []) if row.get("id") is not None
        ]
        language = self._theme.language
        self.table.filter.blockSignals(True)
        self.table.filter.clear()
        self.table.filter.addItem(tr(language, "button_filter_all"), "")
        self.table.filter.addItem(tr(language, "users_active"), "1")
        self.table.filter.addItem(tr(language, "users_inactive"), "0")
        self.table.filter.blockSignals(False)
        normalized = []
        for row in payload.get("users") or []:
            item = dict(row)
            # Display proxy for the filter/status column. The raw boolean stays
            # on ``_is_active`` so the formatter is not confused by "0"/"1".
            item["status_text"] = (
                tr(language, "users_active") if row.get("is_active") else tr(language, "users_inactive")
            )
            normalized.append(item)
        self.table.set_rows(normalized)
        self.banner.setVisible(False)
        self.summary.setText(
            tr(language, "rows_counted", count=format_row_count(len(normalized), language))
        )

    def selected_user_id(self) -> Optional[int]:
        row = self.table.selected_row()
        if row is None:
            return None
        try:
            return int(row.get("id"))
        except (TypeError, ValueError):
            return None

    def retranslate(self) -> None:
        super().retranslate()
        language = self._theme.language
        for row in self.table._rows:  # same dicts the table renders
            row["status_text"] = (
                tr(language, "users_active") if row.get("is_active") else tr(language, "users_inactive")
            )
        self.table._apply_view()
        if self.create_button is not None:
            self.create_button.setText(tr(language, "users_create"))
        if self.deactivate_button is not None:
            self.deactivate_button.setText(tr(language, "users_deactivate"))


__all__ = [
    "AccountsView",
    "DocumentsView",
    "PartnersView",
    "ProductsView",
    "StockView",
    "TrialBalanceView",
    "UsersView",
]
