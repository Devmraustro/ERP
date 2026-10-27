# SPDX-License-Identifier: LGPL-3.0-only
"""PySide6 widgets for the desktop client.

Importing this module requires PySide6 (``pip install algerian-ai-erp[desktop]``).
Everything business-related lives in the service/API-client layer; these
widgets only present data and collect input, so the UI can be replaced or
restyled without touching accounting behaviour.
"""

from __future__ import annotations

import pathlib
from typing import Any, Callable, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__ as ERP_VERSION
from . import APP_NAME
from .api_client import ErpApiError, ErpClient
from .i18n import is_rtl, tr

#: View registry: key in the i18n table → (title_key, loader_method_name).
VIEWS = ("dashboard", "accounts", "products", "documents", "invoices", "trial_balance")


class LoginDialog(QWidget):
    """Credential form; calls ``on_login(username, password)`` on submit."""

    def __init__(self, lang: str, on_login: Callable[[str, str], None]) -> None:
        super().__init__()
        self._lang = lang
        self._on_login = on_login
        self.setWindowTitle(tr(lang, "login_title"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr(lang, "login_username"), self.username)
        form.addRow(tr(lang, "login_password"), self.password)
        layout.addLayout(form)
        button = QPushButton(tr(lang, "login_button"))
        button.setDefault(True)
        button.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)
        self.username.returnPressed.connect(self._submit)
        layout.addWidget(button)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)

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
        self.setWindowTitle(tr(lang, "first_run_title"))
        layout = QVBoxLayout(self)
        intro = QLabel(tr(lang, "first_run_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.company = QLineEdit()
        self.admin_username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr(lang, "first_run_company"), self.company)
        form.addRow(tr(lang, "first_run_admin"), self.admin_username)
        form.addRow(tr(lang, "first_run_password"), self.password)
        form.addRow(tr(lang, "first_run_confirm"), self.confirm)
        layout.addLayout(form)
        button = QPushButton(tr(lang, "first_run_create"))
        button.setDefault(True)
        button.clicked.connect(self._submit)
        layout.addWidget(button)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)

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


def make_table(headers: List[str]) -> QTableWidget:
    """A read-only table with sane column sizing."""
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
    table.setRowCount(0)
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(value))


class MainWindow(QMainWindow):
    """Navigation + data views for a logged-in desktop session."""

    def __init__(self, lang: str, client: ErpClient, mode_label: str) -> None:
        super().__init__()
        self._lang = lang
        self._client = client
        self.setWindowTitle(f"{APP_NAME} — {tr(lang, 'app_title')}")
        self.resize(1180, 720)

        central = QWidget()
        layout = QHBoxLayout(central)

        self.nav = QListWidget()
        self.nav.setMaximumWidth(230)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Views in VIEWS order.
        self.dash_labels: Dict[str, QLabel] = {}
        self.dash = QWidget()
        dash_form = QVBoxLayout(self.dash)
        for key in ("mode_local", "status_user", "tb_total_debit", "tb_total_credit"):
            label = QLabel("")
            self.dash_labels[key] = label
            dash_form.addWidget(label)
        self.dash_summary = QLabel("")
        self.dash_summary.setWordWrap(True)
        dash_form.addWidget(self.dash_summary)
        dash_form.addStretch(1)
        self.stack.addWidget(self.dash)

        self.accounts_table = make_table([])
        self.products_table = make_table([])
        self.documents_table = make_table([])
        self.invoices_table = make_table([])
        self.tb_table = make_table([])
        self.tb_footer = QLabel("")
        self.stack.addWidget(self.accounts_table)
        self.stack.addWidget(self.products_table)

        documents_widget = QWidget()
        doc_layout = QVBoxLayout(documents_widget)
        upload_row = QHBoxLayout()
        self.upload_button = QPushButton("")
        self.upload_button.clicked.connect(self._upload_document)
        upload_row.addWidget(self.upload_button)
        upload_row.addStretch(1)
        doc_layout.addLayout(upload_row)
        doc_layout.addWidget(self.documents_table)
        self.stack.addWidget(documents_widget)

        self.stack.addWidget(self.invoices_table)
        tb_widget = QWidget()
        tb_layout = QVBoxLayout(tb_widget)
        tb_layout.addWidget(self.tb_table)
        self.tb_footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        tb_layout.addWidget(self.tb_footer)
        self.stack.addWidget(tb_widget)

        self.nav.currentRowChanged.connect(self._on_view_changed)
        self._build_menus()
        self.statusBar().showMessage(mode_label)
        self.retranslate()

    # ------------------------------------------------------------------
    # menus / language
    # ------------------------------------------------------------------

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("")
        lang_menu = self.menuBar().addMenu("")
        help_menu = self.menuBar().addMenu("")
        quit_action = file_menu.addAction("")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        self._fr_action = lang_menu.addAction("")
        self._fr_action.triggered.connect(lambda: self._switch_language("fr"))
        self._ar_action = lang_menu.addAction("")
        self._ar_action.triggered.connect(lambda: self._switch_language("ar"))
        about_action = help_menu.addAction("")
        about_action.triggered.connect(self._show_about)
        self._file_menu = file_menu
        self._lang_menu = lang_menu
        self._help_menu = help_menu
        self._quit_action = quit_action
        self._about_action = about_action

    def _switch_language(self, lang: str) -> None:
        self._lang = lang
        app = QApplication.instance()
        if app is not None:
            direction = (
                Qt.LayoutDirection.RightToLeft if is_rtl(lang) else Qt.LayoutDirection.LeftToRight
            )
            app.setLayoutDirection(direction)
        self.retranslate()

    def retranslate(self) -> None:
        lang = self._lang
        self.setWindowTitle(f"{APP_NAME} — {tr(lang, 'app_title')}")
        self._file_menu.setTitle(tr(lang, "menu_file"))
        self._lang_menu.setTitle(tr(lang, "menu_language"))
        self._help_menu.setTitle(tr(lang, "menu_help"))
        self._quit_action.setText(tr(lang, "action_quit"))
        self._about_action.setText(tr(lang, "action_about"))
        self._fr_action.setText("Français")
        self._ar_action.setText("العربية")
        self.upload_button.setText(tr(lang, "button_upload"))
        # Nav re-population resets the current row; block the change signal so
        # clearing doesn't trigger a spurious dashboard load, restore the
        # selection, then load the current view exactly once.
        current = max(self.nav.currentRow(), 0)
        self.nav.blockSignals(True)
        self.nav.clear()
        for key in VIEWS:
            self.nav.addItem(tr(lang, f"nav_{key}"))
        self.nav.setCurrentRow(current)
        self.nav.blockSignals(False)
        self._refresh_current_view()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            tr(self._lang, "app_title"),
            tr(self._lang, "about_text", version=ERP_VERSION),
        )

    # ------------------------------------------------------------------
    # data loading
    # ------------------------------------------------------------------

    def _on_view_changed(self, index: int) -> None:
        self._load_view(index)

    def _refresh_current_view(self) -> None:
        self._load_view(self.stack.currentIndex())

    def _error(self, exc: Exception) -> None:
        QMessageBox.critical(
            self, tr(self._lang, "error_title"), tr(self._lang, "error_load", detail=str(exc))
        )

    def _load_view(self, index: int) -> None:
        key = VIEWS[index] if 0 <= index < len(VIEWS) else "dashboard"
        lang = self._lang
        try:
            if key == "dashboard":
                self._load_dashboard()
            elif key == "accounts":
                data = self._client.accounts()
                fill_table(
                    self.accounts_table,
                    [tr(lang, "col_code"), tr(lang, "col_name"), tr(lang, "col_type")],
                    [
                        [str(r.get("code", "")), str(r.get("name_fr", "")), str(r.get("account_type", ""))]
                        for r in data.get("accounts", [])
                    ],
                )
            elif key == "products":
                data = self._client.products()
                fill_table(
                    self.products_table,
                    [tr(lang, "col_sku"), tr(lang, "col_name"), tr(lang, "col_uom"), tr(lang, "col_cost")],
                    [
                        [
                            str(r.get("sku", "")),
                            str(r.get("name", "")),
                            str(r.get("uom", "")),
                            str(r.get("standard_cost_dzd", "")),
                        ]
                        for r in data.get("products", [])
                    ],
                )
            elif key == "documents":
                data = self._client.documents()
                fill_table(
                    self.documents_table,
                    [
                        tr(lang, "col_filename"),
                        tr(lang, "col_state"),
                        tr(lang, "col_size"),
                        tr(lang, "col_uploaded"),
                    ],
                    [
                        [
                            str(r.get("filename", "")),
                            str(r.get("workflow_state", "")),
                            str(r.get("byte_size", "")),
                            str(r.get("uploaded_at", "")),
                        ]
                        for r in data.get("documents", [])
                    ],
                )
            elif key == "invoices":
                data = self._client.invoices()
                fill_table(
                    self.invoices_table,
                    [
                        tr(lang, "col_invoice_no"),
                        tr(lang, "col_date"),
                        tr(lang, "col_state"),
                        tr(lang, "col_amount_ht"),
                        tr(lang, "col_amount_tva"),
                        tr(lang, "col_amount_ttc"),
                    ],
                    [
                        [
                            str(r.get("supplier_invoice_no", "")),
                            str(r.get("invoice_date", "")),
                            str(r.get("workflow_state", "")),
                            str(r.get("amount_ht_dzd", "")),
                            str(r.get("amount_tva_dzd", "")),
                            str(r.get("amount_ttc_dzd", "")),
                        ]
                        for r in data.get("invoices", [])
                    ],
                )
            elif key == "trial_balance":
                self._load_trial_balance()
        except ErpApiError as exc:
            self._error(exc)

    def _load_dashboard(self) -> None:
        lang = self._lang
        try:
            accounts = self._client.accounts(limit=500).get("count", 0)
            products = self._client.products(limit=500).get("count", 0)
            documents = self._client.documents(limit=100).get("count", 0)
            invoices = self._client.invoices(limit=100).get("count", 0)
        except ErpApiError as exc:
            self._error(exc)
            return
        try:
            session = self._client.session()
        except ErpApiError as exc:
            session = {}
            self._error(exc)
        self.dash_labels["status_user"].setText(
            tr(
                lang,
                "status_user",
                username=session.get("username", "?"),
                role=session.get("role", "?"),
            )
        )
        self.dash_summary.setText(
            f"{tr(lang, 'nav_accounts')}: {accounts} — "
            f"{tr(lang, 'nav_products')}: {products} — "
            f"{tr(lang, 'nav_documents')}: {documents} — "
            f"{tr(lang, 'nav_invoices')}: {invoices}"
        )

    def _load_trial_balance(self) -> None:
        lang = self._lang
        try:
            data = self._client.trial_balance()
        except ErpApiError as exc:
            self.tb_table.setRowCount(0)
            self.tb_footer.setText("")
            self._error(exc)
            return
        rows = data.get("rows", [])
        fill_table(
            self.tb_table,
            [
                tr(lang, "col_code"),
                tr(lang, "col_name"),
                tr(lang, "col_debit"),
                tr(lang, "col_credit"),
            ],
            [
                [
                    str(r.get("account_code", "")),
                    str(r.get("name_fr", "")),
                    str(r.get("debit_dzd", "")),
                    str(r.get("credit_dzd", "")),
                ]
                for r in rows
            ],
        )
        if rows:
            self.tb_footer.setText(
                f"{tr(lang, 'tb_total_debit')}: {data.get('total_debit_dzd', '0.00')}    "
                f"{tr(lang, 'tb_total_credit')}: {data.get('total_credit_dzd', '0.00')}    "
                f"{tr(lang, 'tb_balanced') if data.get('balanced') else ''}"
            )
        else:
            self.tb_footer.setText(tr(lang, "tb_empty"))

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def _upload_document(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr(self._lang, "nav_documents"), "", "Documents (*.pdf *.png *.jpg *.jpeg)"
        )
        if not path:
            return
        try:
            self._client.upload_document(pathlib.Path(path))
        except ErpApiError as exc:
            self._error(exc)
            return
        self._load_view(VIEWS.index("documents"))

    def set_session(self, session: Dict[str, Any]) -> None:
        self.dash_labels["status_user"].setText(
            tr(
                self._lang,
                "status_user",
                username=session.get("username", "?"),
                role=session.get("role", "?"),
            )
        )

    def set_mode_label(self, label: str) -> None:
        self.dash_labels["mode_local"].setText(label)
        self.statusBar().showMessage(label)
