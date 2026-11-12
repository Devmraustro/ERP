# SPDX-License-Identifier: LGPL-3.0-only
"""Operational dashboard (Phase 29).

What this screen is allowed to show, and why
--------------------------------------------
Every figure on this page comes from one of two verified endpoints:

``GET /api/v1/accounting/trial-balance``
    Exact ``Decimal`` debit/credit totals over **POSTED** journal entries,
    computed by ``ERPDatabase.trial_balance`` and refused by the server if the
    ledger does not balance. This is the only ledger arithmetic in the product
    and the dashboard presents its output verbatim.

``GET /api/v1/{accounts,products,partners,stock,documents,invoices}``
    Row counts and the newest rows, read through the RBAC-wrapped list
    endpoints (the warehouse role is refused financial reads and audited).

What the brief asks for that the API does **not** expose - total sales, total
purchases, cash and bank balances, receivables, payables, inventory valuation,
low-stock alerts and period comparisons - is rendered in an explicit
"not available in the current API" panel. It is not approximated from the
trial balance, not derived from invoice headers, and not estimated: a
receivables figure invented in a presentation layer is a financial defect,
not a placeholder. When an endpoint publishes one of those metrics, the row
turns into a real tile in one place - :data:`UNAVAILABLE_METRICS`.

The debit-by-account chart plots the trial balance rows exactly as returned
and only scales a value to a pixel width.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from . import tokens
from .components import (
    BarChart,
    Banner,
    Card,
    Column,
    DataTable,
    ScrollPage,
    StatCard,
    label,
    muted,
    section_title,
)
from .formatting import (
    format_amount,
    format_count,
    format_date,
    format_row_count,
    humanize_identifier,
    localized_name,
)
from .i18n import tr
from .workers import WorkerOwner

#: Metrics the brief asks for that no endpoint publishes today. Kept as data
#: so the panel and the (future) tiles cannot disagree about what is missing.
UNAVAILABLE_METRICS: Tuple[str, ...] = (
    "dash_metric_sales",
    "dash_metric_purchases",
    "dash_metric_cash",
    "dash_metric_receivables",
    "dash_metric_payables",
    "dash_metric_inventory",
    "dash_metric_low_stock",
    "dash_metric_comparison",
)


def _chart_label(row: Dict[str, Any], name_of) -> str:
    """``401 · Fournisseurs`` - a code alone names nothing to an operator."""
    code = str(row.get("account_code", "") or "")
    name = name_of(row)
    if code and name:
        return f"{code} · {name}"
    return code or name


class DashboardView(ScrollPage, WorkerOwner):
    """Read-only operational overview built from verified API reads."""

    def __init__(self, theme_manager, client) -> None:
        super().__init__()
        self._theme = theme_manager
        self._client = client
        self._init_workers()
        self._loaded_once = False
        #: Last verified counts, kept so a language switch can re-render the
        #: summary without refetching (and without showing stale words).
        self._counts: Optional[Dict[str, int]] = None
        #: (username, role) as resolved by the server; empty until sign-in.
        self._identity: Tuple[str, str] = ("", "")

        outer = self.layout_

        self.page_header = QWidget()
        header_layout = QVBoxLayout(self.page_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(tokens.SPACING["xxs"])
        self.title_label = label("", object_name="PageTitle")
        self.subtitle_label = label("", object_name="PageSubtitle", wrap=True)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(self.subtitle_label)
        self.identity_label = label("", object_name="Muted")
        header_layout.addWidget(self.identity_label)
        self.summary_label = label("", object_name="Muted", wrap=True)
        header_layout.addWidget(self.summary_label)
        outer.addWidget(self.page_header)

        # -- tiles: counts (reference data) -----------------------------
        self.tiles_card = Card("")
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(tokens.SPACING["md"])
        tiles.setVerticalSpacing(tokens.SPACING["md"])
        self.tile_accounts = StatCard("", icon=self._theme.pixmap("accounts", "primary", 16))
        self.tile_products = StatCard("", icon=self._theme.pixmap("products", "primary", 16))
        self.tile_partners = StatCard("", icon=self._theme.pixmap("partners", "primary", 16))
        self.tile_stock = StatCard("", icon=self._theme.pixmap("stock", "primary", 16))
        for index, tile in enumerate(
            (self.tile_accounts, self.tile_products, self.tile_partners, self.tile_stock)
        ):
            tiles.addWidget(tile, 0, index)
        for index in range(4):
            tiles.setColumnStretch(index, 1)
        self.tiles_card.add_layout(tiles)
        outer.addWidget(self.tiles_card)

        # -- ledger -----------------------------------------------------
        self.ledger_card = Card("")
        ledger_row = QHBoxLayout()
        ledger_row.setSpacing(tokens.SPACING["md"])
        self.stat_debit = StatCard("", icon=self._theme.pixmap("coins", "success", 16))
        self.stat_credit = StatCard("", icon=self._theme.pixmap("coins", "info", 16))
        self.stat_state = StatCard("", icon=self._theme.pixmap("shield", "primary", 16))
        for tile in (self.stat_debit, self.stat_credit, self.stat_state):
            ledger_row.addWidget(tile, 1)
        self.ledger_card.add_layout(ledger_row)
        self.ledger_banner = Banner("", "info")
        self.ledger_banner.setVisible(False)
        self.ledger_card.add_widget(self.ledger_banner)
        outer.addWidget(self.ledger_card)

        # -- chart ------------------------------------------------------
        self.chart_card = Card("")
        self.chart = BarChart()
        self.chart.setMinimumHeight(200)
        self.chart_card.add_widget(self.chart, 1)
        outer.addWidget(self.chart_card)

        # -- recent invoices / documents --------------------------------
        self.recent_card = Card("")
        recent_row = QHBoxLayout()
        recent_row.setSpacing(tokens.SPACING["md"])
        self.invoices_table = DataTable(
            [
                Column("supplier_invoice_no", "", width=140),
                Column("invoice_date", "", width=100),
                Column("workflow_state", "", width=130, formatter=humanize_identifier),
                Column("amount_ttc_dzd", "", align="right", width=130, formatter=lambda v: format_amount(v, currency="DZD")),
            ],
            page_size=5,
        )
        self.invoices_table.set_compact(True)
        self.documents_table = DataTable(
            [
                Column("filename", "", width=200),
                Column("workflow_state", "", width=120, formatter=humanize_identifier),
                Column("uploaded_at", "", width=140, formatter=format_date),
            ],
            page_size=5,
        )
        self.documents_table.set_compact(True)
        invoices_column = QVBoxLayout()
        invoices_column.setSpacing(tokens.SPACING["xs"])
        self.invoices_heading = section_title("")
        self.invoices_count = muted("")
        invoices_column.addWidget(self.invoices_heading)
        invoices_column.addWidget(self.invoices_count)
        invoices_column.addWidget(self.invoices_table, 1)
        documents_column = QVBoxLayout()
        documents_column.setSpacing(tokens.SPACING["xs"])
        self.documents_heading = section_title("")
        self.documents_count = muted("")
        documents_column.addWidget(self.documents_heading)
        documents_column.addWidget(self.documents_count)
        documents_column.addWidget(self.documents_table, 1)
        recent_row.addLayout(invoices_column, 1)
        recent_row.addLayout(documents_column, 1)
        self.recent_card.add_layout(recent_row)
        outer.addWidget(self.recent_card)

        # -- honest "not available" panel -------------------------------
        self.availability_card = Card("")
        self.availability_body = label("", object_name="Muted", wrap=True)
        self.availability_card.add_widget(self.availability_body)
        self.availability_grid = QGridLayout()
        self.availability_grid.setHorizontalSpacing(tokens.SPACING["md"])
        self.availability_grid.setVerticalSpacing(tokens.SPACING["xs"])
        self.availability_card.add_layout(self.availability_grid)
        self._availability_labels: List[Tuple[Any, Any]] = []
        for index, key in enumerate(UNAVAILABLE_METRICS):
            name = label("", object_name="FieldLabel")
            status = label("", object_name="Muted")
            self.availability_grid.addWidget(name, index // 2, (index % 2) * 2)
            self.availability_grid.addWidget(status, index // 2, (index % 2) * 2 + 1)
            self._availability_labels.append((name, status))
        outer.addWidget(self.availability_card)

        self.source_note = muted("")
        outer.addWidget(self.source_note)
        outer.addStretch(1)

        self.retranslate()

    # -- i18n ----------------------------------------------------------

    def retranslate(self) -> None:
        language = self._theme.language
        self.title_label.setText(tr(language, "nav_dashboard"))
        self.subtitle_label.setText(tr(language, "page_dashboard_sub"))
        self.tiles_card.set_heading(tr(language, "dash_reference_title"))
        for tile, key in (
            (self.tile_accounts, "dash_tile_accounts"),
            (self.tile_products, "dash_tile_products"),
            (self.tile_partners, "dash_tile_partners"),
            (self.tile_stock, "dash_tile_stock"),
        ):
            tile.label.setText(tr(language, key))
        self.ledger_card.set_heading(tr(language, "dash_ledger_title"))
        self.stat_debit.label.setText(tr(language, "dash_ledger_debit"))
        self.stat_credit.label.setText(tr(language, "dash_ledger_credit"))
        self.stat_state.label.setText(tr(language, "dash_ledger_balanced"))
        self.chart_card.set_heading(tr(language, "chart_ledger_title"))
        self.chart.set_empty_text(tr(language, "chart_empty"))
        # The card owns the section title; the two columns are labelled under
        # it, so the invoices column must not repeat the card heading verbatim.
        self.recent_card.set_heading(tr(language, "dash_recent_title"))
        self.invoices_heading.setText(tr(language, "dash_recent_invoices"))
        self.documents_heading.setText(tr(language, "dash_recent_documents"))
        self.invoices_table.set_placeholders(
            tr(language, "shell_search"), tr(language, "state_empty_invoices")
        )
        self.documents_table.set_placeholders(
            tr(language, "shell_search"), tr(language, "state_empty_documents")
        )
        self.invoices_table.set_language(language)
        self.documents_table.set_language(language)
        self.availability_card.set_heading(tr(language, "dash_availability_title"))
        self.availability_body.setText(tr(language, "dash_availability_body"))
        for (name_label, status_label), key in zip(self._availability_labels, UNAVAILABLE_METRICS):
            name_label.setText(tr(language, key))
            status_label.setText(tr(language, "dash_unavailable_metric"))
        self.source_note.setText(tr(language, "dash_source_note"))
        self._render_identity()
        self._render_summary()
        self.invoices_table.set_direction(self._theme.rtl)
        self.documents_table.set_direction(self._theme.rtl)
        self.chart.set_direction(self._theme.rtl)

    # -- loading -------------------------------------------------------

    def refresh(self) -> None:
        """Fetch everything in one worker, then render; errors are explicit."""
        if not self._client.is_logged_in:
            return
        language = self._theme.language
        for tile in (
            self.tile_accounts,
            self.tile_products,
            self.tile_partners,
            self.tile_stock,
            self.stat_debit,
            self.stat_credit,
            self.stat_state,
        ):
            tile.set_value("…")
        self.invoices_table.set_loading(tr(language, "loading"))
        self.documents_table.set_loading(tr(language, "loading"))
        self.summary_label.setText(tr(language, "loading"))
        self.run_async(self._load, on_success=self._apply, on_error=self._failed)

    def _render_summary(self) -> None:
        """The per-view counts, in the current language, or nothing yet."""
        if self._counts is None:
            self.summary_label.setText("")
            return
        language = self._theme.language
        self.summary_label.setText(
            " — ".join(
                f"{tr(language, f'nav_{key}')}: {format_count(count)}"
                for key, count in self._counts.items()
            )
        )

    def set_identity(self, username: str, role: str) -> None:
        """Show the server-resolved principal (never a client-side guess)."""
        self._identity = (username, role)
        self._render_identity()

    def _render_identity(self) -> None:
        """Render the identity line in the current language.

        Kept separate from ``set_identity`` so a language switch can re-render
        it: the principal is charged once, the sentence around it is not
        frozen in the language it happened to be charged in.
        """
        username, role = self._identity
        if not username:
            self.identity_label.setText("")
            return
        self.identity_label.setText(
            tr(self._theme.language, "status_user", username=username, role=role)
        )

    def _load(self) -> Dict[str, Any]:
        client = self._client
        data: Dict[str, Any] = {
            "accounts": client.accounts(limit=500),
            "products": client.products(limit=500),
            "partners": client.partners(limit=500),
            "stock": client.stock(limit=500),
            "documents": client.documents(limit=20),
            "invoices": client.invoices(limit=20),
        }
        try:
            data["trial_balance"] = client.trial_balance()
        except Exception as exc:  # the ledger is the only optional read here
            data["trial_balance"] = None
            data["trial_balance_error"] = str(exc)
        try:
            data["session"] = client.session()
        except Exception:
            data["session"] = {}
        return data

    def _failed(self, exc: object) -> None:
        self.tile_accounts.set_value("—")
        self.stat_debit.set_value("—")
        self.invoices_table.set_error(tr(self._theme.language, "state_load_failed", detail=str(exc)))
        self.documents_table.set_error(tr(self._theme.language, "state_load_failed", detail=str(exc)))

    def _apply(self, data: Dict[str, Any]) -> None:
        language = self._theme.language
        self._loaded_once = True

        accounts = data.get("accounts") or {}
        products = data.get("products") or {}
        partners = data.get("partners") or {}
        stock = data.get("stock") or {}
        documents = data.get("documents") or {}
        invoices = data.get("invoices") or {}

        self.tile_accounts.set_value(format_count(accounts.get("count", 0)))
        self.tile_products.set_value(format_count(products.get("count", 0)))
        self.tile_partners.set_value(format_count(partners.get("count", 0)))
        self.tile_stock.set_value(format_count(stock.get("count", 0)))

        self._counts = {
            "accounts": int(accounts.get("count", 0) or 0),
            "products": int(products.get("count", 0) or 0),
            "suppliers": int(partners.get("count", 0) or 0),
            "stock": int(stock.get("count", 0) or 0),
            "documents": int(documents.get("count", 0) or 0),
            "invoices": int(invoices.get("count", 0) or 0),
        }
        self._render_summary()

        # -- ledger
        trial = data.get("trial_balance")
        if trial is None:
            self.stat_debit.set_value("—", state="warning")
            self.stat_credit.set_value("—", state="warning")
            self.stat_state.set_value("—", state="warning")
            self.ledger_banner.set_text(
                tr(language, "state_load_failed", detail=str(data.get("trial_balance_error", "")))
            )
            self.ledger_banner.setVisible(True)
            self.chart.set_items([], {})
        else:
            rows = trial.get("rows") or []
            self.ledger_banner.setVisible(False)
            self.stat_debit.set_value(
                format_amount(trial.get("total_debit_dzd"), currency="DZD"), state="neutral"
            )
            self.stat_credit.set_value(
                format_amount(trial.get("total_credit_dzd"), currency="DZD"), state="neutral"
            )
            balanced = bool(trial.get("balanced"))
            self.stat_state.set_value(
                tr(language, "tb_verdict_ok") if balanced else tr(language, "tb_verdict_ko"),
                caption=(
                    (tr(language, "dash_ledger_balanced") if balanced else tr(language, "tb_unbalanced"))
                    + " · "
                    + tr(language, "rows_shown", shown=format_count(len(rows)), total=format_count(len(rows)))
                ),
                state="success" if balanced else "danger",
            )
            # A trial-balance row exists for every account the ledger touched,
            # including pure-credit ones. Those have no debit to plot, so they
            # must not occupy a zero-length bar in a "debit by account" chart.
            debit_rows = [row for row in rows if self._to_float(row.get("debit_dzd")) > 0]
            if debit_rows:
                name_of = localized_name(self._theme.language)
                self.chart.set_items(
                    [
                        (
                            _chart_label(row, name_of),
                            format_amount(row.get("debit_dzd")),
                            self._to_float(row.get("debit_dzd")),
                        )
                        for row in debit_rows[:8]
                    ],
                    {
                        "bar": self._theme.color("primary"),
                        "text": self._theme.color("text_muted"),
                        "grid": self._theme.color("divider"),
                    },
                )
            else:
                self.chart.set_items([], {})

        # -- recent
        invoice_rows = list((invoices.get("invoices") or [])[:5])
        document_rows = list((documents.get("documents") or [])[:5])
        self.invoices_table.set_rows(invoice_rows)
        self.documents_table.set_rows(document_rows)
        self.invoices_count.setText(
            tr(language, "rows_counted", count=format_row_count(int(invoices.get("count", 0) or 0), language))
        )
        self.documents_count.setText(
            tr(language, "rows_counted", count=format_row_count(int(documents.get("count", 0) or 0), language))
        )

    @staticmethod
    def _to_float(value: Any) -> float:
        """Scale-only conversion for the bar chart.

        The displayed text is the untouched decimal string; this float exists
        solely to compute a pixel width and is never shown or stored.
        """
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0


__all__ = ["DashboardView", "UNAVAILABLE_METRICS"]
