# SPDX-License-Identifier: LGPL-3.0-only
"""PySide6 widgets for the desktop client (Phase 29 redesign).

Importing this module requires PySide6 (``pip install algerian-ai-erp[desktop]``).
Everything business-related lives in the service/API-client layer; these
widgets only present data and collect input, so the UI can be replaced or
restyled without touching accounting behaviour.

Layering
--------
``tokens``      colours, spacing, type and icon scales - the only place a
                colour is written down.
``formatting``  display-only decimal/date formatting; never recomputes.
``components``  the reusable primitives (tables, cards, banners, charts).
``shell``       sidebar + top bar + workspace chrome.
``views_*``     the screens, which compose the primitives.
``ui``          *this module*: the window and dialogs that bind the shell to
                the API client, plus the compatibility surface the Phase
                26-28 suites and ``app.py`` already import.

Every screen keeps the same discipline: call the API, render what came back,
show the server's refusal when it refuses. No screen computes a financial
figure, infers a workflow transition, or invents a value the backend did not
return.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QIODevice, QSize
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, tokens
from .api_client import ErpApiError, ErpClient
from .components import Button, Card, label
from .config import THEMES
from .i18n import LANGUAGES, tr
from .shell import AppShell
from .theme import ThemeManager
from .views_dashboard import DashboardView
from .views_data import (
    AccountsView,
    DocumentsView,
    PartnersView,
    ProductsView,
    StockView,
    TrialBalanceView,
    UsersView,
)
from .views_invoices import CreateInvoiceDialog, InvoiceReviewDialog, InvoicesView
from .views_users import CreateUserDialog
from .workers import ApiWorker

#: View registry. The order is the sidebar order and the stack order, and the
#: list index *is* the route, so screens and callers look themselves up here.
#:
#: Phase 29 groups the navigation (Operations / Accounting / Administration),
#: so the views are listed group by group. The **route identity is the key**,
#: not the position: callers resolve with ``VIEWS.index("invoices")`` and the
#: stack holds each screen under its own key. ``dashboard`` stays first, so
#: the start view and every ``_load_view(0)`` still land on it.
VIEWS = (
    # Operations
    "dashboard",
    "documents",
    "invoices",
    # Accounting
    "accounts",
    "trial_balance",
    "products",
    "stock",
    "suppliers",
    # Administration
    "users",
)

#: Views whose data is financial ledger information (RBAC: admin/accountant).
_FINANCIAL_VIEWS = ("trial_balance",)

#: Views that only an administrator may open.
_ADMIN_VIEWS = ("users",)


# ----------------------------------------------------------------------
# dialogs (same construction contracts as Phases 10/26/27/28)
# ----------------------------------------------------------------------


class LoginDialog(QWidget):
    """Credential form; calls ``on_login(username, password)`` on submit."""

    def __init__(self, lang: str, on_login: Callable[[str, str], None]) -> None:
        super().__init__()
        self._lang = lang
        self._on_login = on_login
        self.setObjectName("AuthRoot")
        self.setWindowTitle(tr(lang, "login_title"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        card = Card("", body_margins=(28, 26, 28, 26))
        card.setMaximumWidth(460)
        card.setMinimumWidth(380)
        heading = label(tr(lang, "login_title"), object_name="PageTitle")
        card.add_widget(heading)
        card.add_widget(label(tr(lang, "app_suite"), object_name="PageSubtitle", wrap=True))
        form = QFormLayout()
        form.setHorizontalSpacing(tokens.SPACING["lg"])
        form.setVerticalSpacing(tokens.SPACING["sm"])
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(label(tr(lang, "login_username"), object_name="FieldLabel"), self.username)
        form.addRow(label(tr(lang, "login_password"), object_name="FieldLabel"), self.password)
        card.add_layout(form)
        self.login_button = Button(
            tr(lang, "login_button"), variant="primary", on_click=self._submit
        )
        self.login_button.setDefault(True)
        card.add_widget(self.login_button)
        self.error_label = label("", object_name="ErrorLabel", wrap=True)
        card.add_widget(self.error_label)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.password.returnPressed.connect(self._submit)
        self.username.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        self.error_label.setText("")
        self._on_login(self.username.text(), self.password.text())

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)


class FirstRunDialog(QWidget):
    """First-install wizard: company + local admin, before anything else."""

    def __init__(
        self,
        lang: str,
        on_create: Callable[[str, str, str], None],
    ) -> None:
        super().__init__()
        self._lang = lang
        self._on_create = on_create
        self.setObjectName("AuthRoot")
        self.setWindowTitle(tr(lang, "first_run_title"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        card = Card("", body_margins=(28, 26, 28, 26))
        card.setMaximumWidth(520)
        card.setMinimumWidth(420)
        card.add_widget(label(tr(lang, "first_run_title"), object_name="PageTitle"))
        intro = label(tr(lang, "first_run_intro"), object_name="PageSubtitle", wrap=True)
        card.add_widget(intro)
        form = QFormLayout()
        form.setHorizontalSpacing(tokens.SPACING["lg"])
        form.setVerticalSpacing(tokens.SPACING["sm"])
        self.company = QLineEdit()
        self.admin_username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        for widget, key in (
            (self.company, "first_run_company"),
            (self.admin_username, "first_run_admin"),
            (self.password, "first_run_password"),
            (self.confirm, "first_run_confirm"),
        ):
            form.addRow(label(tr(lang, key), object_name="FieldLabel"), widget)
        card.add_layout(form)
        self.create_button = Button(
            tr(lang, "first_run_create"), variant="primary", on_click=self._submit
        )
        self.create_button.setDefault(True)
        card.add_widget(self.create_button)
        self.error_label = label("", object_name="ErrorLabel", wrap=True)
        card.add_widget(self.error_label)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    def _submit(self) -> None:
        if self.password.text() != self.confirm.text():
            self.error_label.setText(tr(self._lang, "first_run_mismatch"))
            return
        self.error_label.setText("")
        self._on_create(
            self.company.text(), self.admin_username.text(), self.password.text()
        )

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)


# ----------------------------------------------------------------------
# helpers kept for compatibility with earlier phases
# ----------------------------------------------------------------------


def make_table(headers: List[str]) -> QTableWidget:
    """A read-only table with sane column sizing (Phase 10 helper)."""
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def fill_table(table: QTableWidget, headers: List[str], rows: List[List[str]]) -> None:
    """Replace a table's headers and rows (Phase 10 helper)."""
    table.setRowCount(0)
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(value))


def confidence_text(lang: str, field: Dict[str, Any]) -> str:
    """Honest confidence display: absent means not measured, never a threshold.

    Delegates to :func:`dz_erp.desktop.formatting.format_confidence`, which
    prints the stored value verbatim; the schema has no review threshold, so
    none is invented here.
    """
    from .formatting import format_confidence

    return format_confidence(
        field.get("confidence"),
        calibrated=bool(field.get("confidence_calibrated")),
        lang=lang,
    )


def render_document_preview(data: bytes, media_type: str) -> Optional[QPixmap]:
    """A safe preview for images and, when QtPdf is available, PDF page 1."""
    if media_type.startswith("image/"):
        pixmap = QPixmap()
        return pixmap if pixmap.loadFromData(data) else None
    if media_type == "application/pdf":
        try:
            from PySide6.QtCore import QBuffer, QByteArray
            from PySide6.QtPdf import QPdfDocument
        except ImportError:
            return None
        try:
            doc = QPdfDocument()
            buffer = QBuffer()
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            if doc.load(buffer) != QPdfDocument.Status.Ready or doc.pageCount() < 1:
                return None
            return QPixmap.fromImage(doc.render(0, QSize(900, 1273)))
        except Exception:  # a broken preview must not break review
            return None
    return None


# ----------------------------------------------------------------------
# main window
# ----------------------------------------------------------------------


class MainWindow(QMainWindow):
    """The application shell, bound to one authenticated API client.

    Composition
    -----------
    ``AppShell`` provides the banner chrome (sidebar, top bar, workspace);
    one screen widget per entry of :data:`VIEWS` lives in ``self.stack``.

    Compatibility surface (Phase 26-28 suites and ``app.py``)
    ---------------------------------------------------------
    ``nav``                 the sidebar's ``QListWidget``: exactly one row per
                            view, in :data:`VIEWS` order.
    ``stack``               ``QStackedWidget`` whose index is the view index.
    ``<view>_table``        the underlying ``QTableWidget`` of each screen, so
                            existing assertions read real cells.
    ``dash_summary``        the dashboard's factual count summary.
    ``_load_view(i)``       load the view at index ``i`` (synchronous call
                            site; the screen fetches on its own worker).
    ``_switch_language``    rebuild every string and flip layout direction.
    ``set_session``         adopt the server-resolved principal.
    """

    def __init__(
        self,
        lang: str,
        client: ErpClient,
        mode_label: str,
        *,
        theme_manager: Optional[ThemeManager] = None,
    ) -> None:
        super().__init__()
        self._lang = lang
        self._client = client
        # The mode line is a UI string, not a frozen caption: keep the key so
        # a language switch relabels it instead of leaving one French
        # sentence in an otherwise Arabic window.
        self._mode_label = mode_label
        self._mode_key = "mode_local" if "local" in mode_label.lower() else "mode_server"
        self._mode_url = ""
        self._role = ""
        self._username = ""
        self._theme = theme_manager or ThemeManager(theme="light", language=lang)
        self._workers: List[ApiWorker] = []
        #: Re-entrancy guard: selecting a view programmatically moves the
        #: sidebar row, which would otherwise route straight back into
        #: ``_load_view`` and recurse.
        self._routing = False
        self.setWindowTitle(f"{APP_NAME} — {tr(lang, 'app_title')}")
        self.resize(1360, 860)
        self.setMinimumSize(1024, 640)

        self.shell = AppShell(self._theme, VIEWS)
        self.setCentralWidget(self.shell)
        self.nav = self.shell.sidebar.nav
        self.stack = QStackedWidget()
        self.shell.set_content(self.stack)
        self.shell.sidebar.mode_label.setText(mode_label)

        # -- screens, in VIEWS order
        self.dashboard_view = DashboardView(self._theme, client)
        self.accounts_view = AccountsView(self._theme, client)
        self.products_view = ProductsView(self._theme, client)
        self.suppliers_view = PartnersView(self._theme, client)
        self.stock_view = StockView(self._theme, client)
        self.documents_view = DocumentsView(self._theme, client)
        self.invoices_view = InvoicesView(self._theme, client)
        self.trial_balance_view = TrialBalanceView(self._theme, client)
        self.users_view = UsersView(self._theme, client)

        self._views: Dict[str, QWidget] = {
            "dashboard": self.dashboard_view,
            "accounts": self.accounts_view,
            "products": self.products_view,
            "suppliers": self.suppliers_view,
            "stock": self.stock_view,
            "documents": self.documents_view,
            "invoices": self.invoices_view,
            "trial_balance": self.trial_balance_view,
            "users": self.users_view,
        }
        for key in VIEWS:
            self.stack.addWidget(self._views[key])

        # -- compatibility attributes: unchanged names, real cells
        self.dash = self.dashboard_view
        self.dash_summary = self.dashboard_view.summary_label
        self.dash_labels: Dict[str, QLabel] = {}
        self.accounts_table = self.accounts_view.table.table
        self.products_table = self.products_view.table.table
        self.suppliers_table = self.suppliers_view.table.table
        self.stock_table = self.stock_view.table.table
        self.documents_table = self.documents_view.table.table
        self.invoices_table = self.invoices_view.table.table
        self.tb_table = self.trial_balance_view.table.table
        self.users_table = self.users_view.table.table
        self.tb_footer = self.trial_balance_view.footer
        self.upload_button = None
        self.review_button = self.invoices_view.review_button
        self.create_invoice_button = self.invoices_view.create_button
        self.users_create_button = None
        self.users_deactivate_button = None

        # Modal-less actions the screens delegate back to the window.
        self.documents_view.set_upload_handler(self._upload_document)
        self.invoices_view.set_actions(self._open_review, self._create_invoice)
        self.users_view.set_actions(self._create_user, self._deactivate_user)

        # -- shell wiring
        self.shell.viewSelected.connect(self._on_view_selected)
        self.shell.topbar.languageChanged.connect(self._switch_language)
        self.shell.topbar.themeChanged.connect(self._switch_theme)
        self.shell.topbar.signOutRequested.connect(self._sign_out)
        self.shell.topbar.alertsRequested.connect(self._show_alerts)
        self.nav.currentRowChanged.connect(self._on_nav_row)
        self._build_shortcuts()

        self.statusBar().showMessage(mode_label)
        self.retranslate()

    # ------------------------------------------------------------------
    # chrome
    # ------------------------------------------------------------------

    def _build_shortcuts(self) -> None:
        """Keyboard access to the shell (Ctrl+1..9, Ctrl+B, Ctrl+Q)."""
        for index in range(len(VIEWS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self._load_view(i))
        toggle = QShortcut(QKeySequence("Ctrl+B"), self)
        toggle.activated.connect(self.shell.sidebar.toggle_collapsed)
        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.close)

    def _on_nav_row(self, row: int) -> None:
        """Route a sidebar row change; ignore the transient -1 during rebuild."""
        if self._routing or not 0 <= row < len(VIEWS):
            return
        self._load_view(row, wait=False)

    def _on_view_selected(self, view: str) -> None:
        if self._routing or view not in VIEWS:
            return
        # Interactive navigation never blocks the GUI thread on HTTP.
        self._load_view(VIEWS.index(view), wait=False)

    def _sign_out(self) -> None:
        answer = QMessageBox.question(
            self,
            tr(self._lang, "shell_signout"),
            tr(self._lang, "shell_signout_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._client.logout()
        except ErpApiError:
            pass  # the local session is dropped either way
        self.close()

    def _show_alerts(self) -> None:
        self.shell.topbar._toggle_alerts()

    # ------------------------------------------------------------------
    # language / theme
    # ------------------------------------------------------------------

    def _switch_language(self, lang: str) -> None:
        """Apply a language: strings, layout direction, and the persisted pref."""
        if lang not in LANGUAGES:
            return
        self._lang = lang
        self._theme.apply(language=lang)
        self.shell.topbar.language_combo.blockSignals(True)
        self.shell.topbar.language_combo.setCurrentIndex(
            max(0, self.shell.topbar.language_combo.findData(lang))
        )
        self.shell.topbar.language_combo.blockSignals(False)
        self.retranslate()

    def _switch_theme(self, theme: str) -> None:
        if theme not in THEMES:
            return
        self._theme.apply(theme=theme)
        self.retranslate()

    def retranslate(self) -> None:
        lang = self._lang
        self.setWindowTitle(f"{APP_NAME} — {tr(lang, 'app_title')}")
        for key in VIEWS:
            self._views[key].retranslate()
        self.shell.retranslate()
        if self._username:
            self.shell.topbar.set_session(self._username, self._role)
        self._apply_nav_role_visibility()
        self._update_breadcrumb()
        self._retranslate_mode_label()

    def _update_breadcrumb(self) -> None:
        index = self.stack.currentIndex()
        key = VIEWS[index] if 0 <= index < len(VIEWS) else VIEWS[0]
        self.shell.topbar.set_breadcrumb(
            f"{tr(self._lang, 'app_short')} / {tr(self._lang, f'nav_{key}')}"
        )

    def _apply_nav_role_visibility(self) -> None:
        """Hide views the principal may not use - hidden, never removed.

        The role is the server-resolved principal role; nothing is inferred
        client-side. The API re-checks every call, so this only keeps a user
        from opening a screen that would refuse them anyway.
        """
        admin = self._role in ("", "admin") and self._role != ""
        for view in _ADMIN_VIEWS:
            self.shell.sidebar.set_view_hidden(view, not admin)
        financial = self._role in ("", "admin", "accountant")
        for view in _FINANCIAL_VIEWS:
            self.shell.sidebar.set_view_hidden(view, not financial)

    # ------------------------------------------------------------------
    # data loading
    # ------------------------------------------------------------------

    def _refresh_current_view(self) -> None:
        self._load_view(self.stack.currentIndex())

    def _load_view(self, index: int, *, wait: bool = True) -> None:
        """Show the view at ``index`` and load its data.

        ``wait=True`` (the default, and the Phase 27/28 contract) returns once
        the screen has rendered the server's answer, so a caller can read the
        table straight afterwards. ``wait=False`` is what interactive
        navigation uses: the fetch stays on the worker thread and the window
        never freezes on a slow round trip. Either way the HTTP call is made
        by the screen's own worker, tracked on ``_workers``.
        """
        if self._routing or not 0 <= index < len(VIEWS):
            return
        self._routing = True
        try:
            self.stack.setCurrentIndex(index)
            self.shell.sidebar.select(VIEWS[index])
            self._update_breadcrumb()
            page = self._views[VIEWS[index]]
            page.refresh()
            self._adopt_workers(page)
        finally:
            self._routing = False
        if wait:
            self._wait_for(page)

    @staticmethod
    def _wait_for(page: QWidget, timeout_s: float = 30.0) -> None:
        """Pump the event loop until ``page``'s workers have delivered.

        Queued signals from a ``QThread`` only arrive while events are being
        processed; a bounded deadline keeps a hung server from wedging the
        caller. Used by the synchronous seam, never by interactive routing.
        """
        app = QApplication.instance()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if app is not None:
                app.processEvents()
            workers = getattr(page, "_workers", [])
            if all(worker.isFinished() for worker in workers):
                if app is not None:
                    app.processEvents()
                return
            time.sleep(0.01)

    def _adopt_workers(self, page: QWidget) -> None:
        """Track a screen's workers on the window so callers can drain them.

        The Phase 27/28 harnesses wait on ``window._workers``; the redesigned
        screens own their workers, so the window mirrors them here.
        """
        own = getattr(page, "_workers", None)
        if own is not None:
            self._workers = own

    def _error(self, exc: Exception) -> None:
        QMessageBox.critical(
            self, tr(self._lang, "error_title"), tr(self._lang, "error_load", detail=str(exc))
        )

    # ------------------------------------------------------------------
    # document upload
    # ------------------------------------------------------------------

    def _upload_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr(self._lang, "nav_documents"), "", "Documents (*.pdf *.png *.jpg *.jpeg)"
        )
        if not path:
            return
        button = self.documents_view.upload_button
        if button is not None:
            button.setEnabled(False)
        self.statusBar().showMessage(tr(self._lang, "upload_in_progress"))
        worker = ApiWorker(self._client.upload_document, pathlib.Path(path), parent=self)
        self._workers = [w for w in self._workers if not w.isFinished()]
        self._workers.append(worker)
        worker.succeeded.connect(self._upload_done)
        worker.failed.connect(self._upload_done_error)
        worker.start()

    def _upload_done(self, _result: object) -> None:
        button = self.documents_view.upload_button
        if button is not None:
            button.setEnabled(True)
        self.statusBar().showMessage("")
        self._load_view(VIEWS.index("documents"))

    def _upload_done_error(self, exc: object) -> None:
        button = self.documents_view.upload_button
        if button is not None:
            button.setEnabled(True)
        self.statusBar().showMessage("")
        self._error(exc)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # invoices
    # ------------------------------------------------------------------

    def _selected_invoice_id(self) -> Optional[int]:
        return self.invoices_view.selected_invoice_id()

    def _make_review_dialog(self, invoice_id: int) -> "InvoiceReviewDialog":
        """Construction seam (also used by tests): build the review dialog.

        Only the id is needed: the dialog's own worker loads the detail
        asynchronously - important because a role denied the invoice list
        must still get the dialog and its surfaced server refusal.
        """
        return InvoiceReviewDialog(
            self._lang, self._client, {"id": invoice_id}, parent=self, theme_manager=self._theme
        )

    def _open_review(self) -> None:
        invoice_id = self._selected_invoice_id()
        if invoice_id is None:
            QMessageBox.information(
                self, tr(self._lang, "review_title"), tr(self._lang, "review_empty")
            )
            return
        dialog = self._make_review_dialog(invoice_id)
        dialog.exec()
        self._refresh_current_view()

    def _create_invoice(self) -> None:
        dialog = CreateInvoiceDialog(self._lang, self._client, parent=self)
        dialog.exec()
        if dialog.result() == QDialog.DialogCode.Accepted:
            self._load_view(VIEWS.index("invoices"))

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------

    def _selected_user_id(self) -> Optional[int]:
        return self.users_view.selected_user_id()

    def _create_user(self) -> None:
        dialog = CreateUserDialog(self._lang, self._client, parent=self)
        dialog.exec()
        if dialog.result() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage(tr(self._lang, "users_created"), 5000)
            self._load_view(VIEWS.index("users"))

    def _deactivate_user(self) -> None:
        user_id = self._selected_user_id()
        if user_id is None:
            QMessageBox.information(
                self,
                tr(self._lang, "users_deactivate"),
                tr(self._lang, "users_none_selected"),
            )
            return
        row = self.users_view.table.selected_row() or {}
        username = str(row.get("username", ""))
        answer = QMessageBox.question(
            self,
            tr(self._lang, "users_deactivate"),
            tr(self._lang, "users_deactivate_confirm", username=username),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.users_view.deactivate_button is not None:
            self.users_view.deactivate_button.setEnabled(False)
        worker = ApiWorker(self._client.deactivate_user, user_id, parent=self)
        self._workers = [w for w in self._workers if not w.isFinished()]
        self._workers.append(worker)
        worker.succeeded.connect(lambda _result: self._deactivate_done())
        worker.failed.connect(self._deactivate_failed)
        worker.start()

    def _deactivate_done(self) -> None:
        if self.users_view.deactivate_button is not None:
            self.users_view.deactivate_button.setEnabled(True)
        self.statusBar().showMessage(tr(self._lang, "users_deactivated"), 5000)
        self._load_view(VIEWS.index("users"))

    def _deactivate_failed(self, exc: object) -> None:
        if self.users_view.deactivate_button is not None:
            self.users_view.deactivate_button.setEnabled(True)
        self._error(exc)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # session
    # ------------------------------------------------------------------

    def set_session(self, session: Dict[str, Any]) -> None:
        """Adopt the server-resolved principal and its derived visibility."""
        self._role = str(session.get("role", ""))
        self._username = str(session.get("username", ""))
        self.shell.topbar.set_session(self._username, self._role)
        # The session payload carries no company name (verified: neither the
        # endpoint nor the schema exposes one), so the top bar stays without
        # one rather than inventing a label.
        self.dashboard_view.set_identity(self._username, self._role)
        self._apply_nav_role_visibility()

    def set_mode_label(self, label_text: str, *, key: str = "", url: str = "") -> None:
        self._mode_label = label_text
        if key:
            self._mode_key = key
        self._mode_url = url

    def _render_mode_label(self) -> str:
        if self._mode_key == "mode_server":
            return tr(self._lang, "mode_server", url=self._mode_url)
        return tr(self._lang, "mode_local")

    def _retranslate_mode_label(self) -> None:
        text = self._render_mode_label()
        self._mode_label = text
        self.shell.sidebar.mode_label.setText(text)
        self.statusBar().showMessage(text)


__all__ = [
    "VIEWS",
    "CreateInvoiceDialog",
    "CreateUserDialog",
    "FirstRunDialog",
    "InvoiceReviewDialog",
    "LoginDialog",
    "MainWindow",
    "confidence_text",
    "fill_table",
    "make_table",
    "render_document_preview",
]
