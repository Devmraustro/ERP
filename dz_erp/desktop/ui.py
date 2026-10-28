# SPDX-License-Identifier: LGPL-3.0-only
"""PySide6 widgets for the desktop client.

Importing this module requires PySide6 (``pip install algerian-ai-erp[desktop]``).
Everything business-related lives in the service/API-client layer; these
widgets only present data and collect input, so the UI can be replaced or
restyled without touching accounting behaviour.
"""

from __future__ import annotations

import pathlib
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QIODevice, QSize, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
VIEWS = (
    "dashboard",
    "accounts",
    "products",
    "suppliers",
    "stock",
    "documents",
    "invoices",
    "trial_balance",
)


class ApiWorker(QThread):
    """Run one client call off the GUI thread; report via signals.

    Long operations (document upload with OCR processing, invoice actions)
    must never block the interface: the work happens in this worker thread
    and the result comes back as a queued signal on the GUI thread. Errors
    are normalized to :class:`ErpApiError` so every handler shows one kind
    of actionable message.
    """

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent: Any = None, **kwargs: Any) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # executed on the worker thread
        try:
            result = self._fn(*self._args, **self._kwargs)
        except ErpApiError as exc:
            self.failed.emit(exc)
        except Exception as exc:  # the UI surfaces, never crashes
            self.failed.emit(ErpApiError(0, str(exc)))
        else:
            self.succeeded.emit(result)


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


def confidence_text(lang: str, field: Dict[str, Any]) -> str:
    """Honest confidence display: absent means not measured, never a threshold.

    The database stores a nullable confidence with an explicit ``kind``; the
    review screen presents exactly what was measured and nothing invented -
    no review threshold exists in the schema, so none is displayed.
    """
    value = field.get("confidence")
    if value is None:
        return tr(lang, "review_not_measured")
    suffix = tr(lang, "review_calibrated") if field.get("confidence_calibrated") else ""
    return f"{float(value):.2f}{suffix}"


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


class InvoiceReviewDialog(QDialog):
    """Invoice review: extraction detail, correction, approval, posting.

    All business rules stay on the service: this dialog calls the verified
    API (detail read, field correction, approval decisions, posting) and
    surfaces the server's decisions - including authorization refusals,
    validation failures and duplicate-protection conflicts - as readable
    errors. Raw OCR text and confidence are displayed exactly as stored;
    corrections create a human override without ever overwriting them.
    """

    def __init__(
        self,
        lang: str,
        client: ErpClient,
        invoice_row: Dict[str, Any],
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._invoice_id = int(invoice_row["id"])
        self._detail: Dict[str, Any] = {}
        self._workers: List[ApiWorker] = []
        self.setWindowTitle(tr(lang, "review_title"))
        self.resize(980, 640)
        layout = QVBoxLayout(self)

        self.state_badge = QLabel("")
        self.state_badge.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.state_badge)

        header_form = QFormLayout()
        self.header_labels: Dict[str, QLabel] = {}
        for key in ("col_supplier", "col_invoice_no", "col_date", "col_amount_ht", "col_amount_tva", "col_amount_ttc"):
            label = QLabel("")
            self.header_labels[key] = label
            header_form.addRow(tr(lang, key), label)
        layout.addLayout(header_form)

        fields_title = QLabel(tr(lang, "review_fields"))
        layout.addWidget(fields_title)
        self.fields_table = make_table(
            [
                tr(lang, "review_field"),
                tr(lang, "review_raw_text"),
                tr(lang, "review_value"),
                tr(lang, "review_confidence"),
                tr(lang, "review_override"),
            ]
        )
        self.fields_table.selectRow(0)
        layout.addWidget(self.fields_table, 1)

        correction_row = QHBoxLayout()
        self.correction_edit = QLineEdit()
        self.correction_button = QPushButton(tr(lang, "review_correct_button"))
        self.correction_button.clicked.connect(self._correct_selected_field)
        correction_row.addWidget(self.correction_edit, 1)
        correction_row.addWidget(self.correction_button)
        layout.addLayout(correction_row)

        lines_title = QLabel(tr(lang, "review_lines"))
        layout.addWidget(lines_title)
        self.lines_table = make_table(
            [
                tr(lang, "col_name"),
                tr(lang, "col_qty"),
                tr(lang, "col_cost"),
            ]
        )
        layout.addWidget(self.lines_table, 1)

        self.preview_label = QLabel(tr(lang, "review_empty"))
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(160)
        self.preview_label.setStyleSheet("border: 1px solid #c0c0c0; background: #fafafa;")
        layout.addWidget(self.preview_label)

        reason_row = QHBoxLayout()
        reason_row.addWidget(QLabel(tr(lang, "decision_reason")))
        self.reason_edit = QLineEdit()
        reason_row.addWidget(self.reason_edit, 1)
        layout.addLayout(reason_row)

        buttons = QHBoxLayout()
        self.submit_button = QPushButton(tr(lang, "submit_approval"))
        self.submit_button.clicked.connect(self._submit_approval)
        self.approve_button = QPushButton(tr(lang, "approve_button"))
        self.approve_button.clicked.connect(lambda: self._decide(True))
        self.reject_button = QPushButton(tr(lang, "reject_button"))
        self.reject_button.clicked.connect(lambda: self._decide(False))
        self.post_button = QPushButton(tr(lang, "post_button"))
        self.post_button.clicked.connect(self._post_invoice)
        for button in (self.submit_button, self.approve_button, self.reject_button, self.post_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------

    def _track(self, worker: ApiWorker) -> ApiWorker:
        self._workers = [w for w in self._workers if w.isFinished() is False]
        self._workers.append(worker)
        return worker

    def _set_busy(self, busy: bool) -> None:
        for button in (self.correction_button, self.submit_button, self.approve_button, self.reject_button, self.post_button):
            button.setEnabled(not busy)

    def refresh(self) -> None:
        """Load detail + fields + document state + preview off the GUI thread.

        The document id is only known from the invoice detail, so the worker
        chains the reads: detail first, then everything document-scoped.
        """
        self._set_busy(True)

        def load() -> Dict[str, Any]:
            detail = self._client.invoice(self._invoice_id)
            document_id = detail.get("document_id")
            partner_names = {
                int(p["id"]): str(p.get("name", ""))
                for p in self._client.partners(limit=500).get("partners", [])
                if p.get("id") is not None
            }
            fields: Dict[str, Any] = {"fields": [], "count": 0}
            doc_state: Optional[str] = None
            preview: Optional[Dict[str, Any]] = None
            if document_id:
                try:
                    fields = self._client.document_fields(str(document_id))
                    for row in self._client.documents(limit=500).get("documents", []):
                        if row.get("id") == str(document_id):
                            doc_state = str(row.get("workflow_state", ""))
                            break
                    preview = self._client.document_content(str(document_id))
                except ErpApiError as exc:
                    if exc.status_code in (401, 403):
                        raise
                    preview = {"error": exc.detail}
            return {
                "detail": detail,
                "fields": fields,
                "doc_state": doc_state,
                "preview": preview,
                "partner_names": partner_names,
            }

        worker = self._track(ApiWorker(load, parent=self))
        worker.succeeded.connect(self._apply_loaded)
        worker.failed.connect(self._show_action_error)
        worker.start()

    def _document_id(self) -> Optional[str]:
        value = self._detail.get("document_id")
        return str(value) if value else None

    def _apply_loaded(self, data: Dict[str, Any]) -> None:
        lang = self._lang
        self._detail = data["detail"]
        invoice = self._detail.get("invoice", self._detail)
        partner_names = data.get("partner_names") or {}
        try:
            supplier_label = partner_names.get(int(invoice.get("supplier_id")))
        except (TypeError, ValueError):
            supplier_label = None
        state = data.get("doc_state")
        if not state:
            state = str(invoice.get("workflow_state", ""))
        self.state_badge.setText(tr(lang, "review_state", state=state))
        self.header_labels["col_supplier"].setText(supplier_label or str(invoice.get("supplier_id", "")))
        self.header_labels["col_invoice_no"].setText(str(invoice.get("supplier_invoice_no", "")))
        self.header_labels["col_date"].setText(str(invoice.get("invoice_date", "")))
        self.header_labels["col_amount_ht"].setText(str(invoice.get("amount_ht_dzd", "")))
        self.header_labels["col_amount_tva"].setText(str(invoice.get("amount_tva_dzd", "")))
        self.header_labels["col_amount_ttc"].setText(str(invoice.get("amount_ttc_dzd", "")))

        fill_table(
            self.fields_table,
            [
                tr(lang, "review_field"),
                tr(lang, "review_raw_text"),
                tr(lang, "review_value"),
                tr(lang, "review_confidence"),
                tr(lang, "review_override"),
            ],
            [
                [
                    str(f.get("field_name", "")),
                    str(f.get("raw_text", "")),
                    str(f.get("human_override_value") or f.get("normalized_value") or ""),
                    confidence_text(lang, f),
                    tr(lang, "review_edited_by", editor=str(f.get("edited_by_id") or "—"), at=str(f.get("edited_at") or "—"))
                    if f.get("human_override_value")
                    else tr(lang, "review_no_override"),
                ]
                for f in data["fields"].get("fields", [])
            ],
        )
        if not data["fields"].get("fields"):
            self.fields_table.setRowCount(0)

        fill_table(
            self.lines_table,
            [tr(lang, "col_name"), tr(lang, "col_qty"), tr(lang, "col_cost")],
            [
                [
                    str(line.get("description", "")),
                    str(line.get("qty", "")),
                    str(line.get("unit_price_dzd", "")),
                ]
                for line in self._detail.get("lines", [])
            ],
        )

        preview = data.get("preview")
        if preview is None:
            self.preview_label.setText(tr(lang, "doc_preview_none"))
        elif preview.get("error"):
            self.preview_label.setText(
                tr(lang, "doc_preview_failed", detail=str(preview["error"]))
            )
        else:
            pixmap = render_document_preview(preview["content"], str(preview["media_type"]))
            if pixmap is None:
                self.preview_label.setText(
                    tr(lang, "doc_preview_unsupported", media=str(preview["media_type"]))
                )
            else:
                self.preview_label.setPixmap(
                    pixmap.scaled(
                        self.preview_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self._set_busy(False)

    # ------------------------------------------------------------------
    # actions (each runs off the GUI thread; errors are surfaced)
    # ------------------------------------------------------------------

    def _show_action_error(self, exc: object) -> None:
        self._set_busy(False)
        QMessageBox.critical(
            self,
            tr(self._lang, "error_title"),
            tr(self._lang, "action_failed", detail=str(exc)),
        )

    def _require_selected_field(self) -> Optional[str]:
        selected = self.fields_table.selectedItems()
        row = selected[0].row() if selected else (0 if self.fields_table.rowCount() else -1)
        if row < 0:
            QMessageBox.information(self, tr(self._lang, "review_title"), tr(self._lang, "review_empty"))
            return None
        item = self.fields_table.item(row, 0)
        return str(item.text()) if item else None

    def _correct_selected_field(self) -> None:
        field_name = self._require_selected_field()
        if not field_name:
            return
        new_value = self.correction_edit.text().strip()
        if not new_value:
            QMessageBox.information(
                self, tr(self._lang, "review_title"), tr(self._lang, "review_edit_empty")
            )
            return
        document_id = self._document_id()
        if not document_id:
            QMessageBox.information(self, tr(self._lang, "review_title"), tr(self._lang, "doc_preview_none"))
            return
        self._set_busy(True)
        worker = self._track(
            ApiWorker(self._client.edit_extracted_field, document_id, field_name, new_value, parent=self)
        )
        worker.succeeded.connect(lambda _result: self.refresh())
        worker.failed.connect(self._show_action_error)
        worker.start()

    def _submit_approval(self) -> None:
        self._set_busy(True)
        worker = self._track(ApiWorker(self._client.submit_approval, self._invoice_id, parent=self))
        worker.succeeded.connect(lambda _result: self.refresh())
        worker.failed.connect(self._show_action_error)
        worker.start()

    def _decide(self, approve: bool) -> None:
        reason = self.reason_edit.text().strip()
        if not approve and not reason:
            QMessageBox.information(
                self, tr(self._lang, "review_title"), tr(self._lang, "decision_reason")
            )
            return
        self._set_busy(True)
        worker = self._track(
            ApiWorker(
                self._client.decide_approval,
                self._invoice_id,
                approve=approve,
                reason=reason,
                parent=self,
            )
        )
        worker.succeeded.connect(lambda _result: self.refresh())
        worker.failed.connect(self._show_action_error)
        worker.start()

    def _post_invoice(self) -> None:
        self._set_busy(True)
        worker = self._track(ApiWorker(self._client.post_invoice, self._invoice_id, parent=self))
        worker.succeeded.connect(lambda _result: self.refresh())
        worker.failed.connect(self._show_action_error)
        worker.start()


class CreateInvoiceDialog(QDialog):
    """Header-amount supplier invoice creation (single-line MVP).

    Totals are validated by the service; this form only collects them. The
    supplier list comes from the tenant-scoped partners read.
    """

    def __init__(self, lang: str, client: ErpClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._workers: List[ApiWorker] = []
        self.setWindowTitle(tr(lang, "invoice_create_title"))
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.supplier_combo = QComboBox()
        suppliers = [
            p
            for p in client.partners().get("partners", [])
            if p.get("partner_type") in ("supplier", "both")
        ]
        for p in suppliers:
            self.supplier_combo.addItem(
                f"{p.get('name', '')} ({p.get('nif') or '—'})", int(p.get("id"))
            )
        form.addRow(tr(lang, "invoice_create_supplier"), self.supplier_combo)

        self.invoice_no = QLineEdit()
        form.addRow(tr(lang, "invoice_create_no"), self.invoice_no)
        self.invoice_date = QLineEdit()
        form.addRow(tr(lang, "invoice_create_date"), self.invoice_date)
        self.payment_combo = QComboBox()
        for method in ("BANK_TRANSFER", "CASH", "CHEQUE"):
            self.payment_combo.addItem(method, method)
        form.addRow(tr(lang, "invoice_create_payment"), self.payment_combo)
        self.amount_ht = QLineEdit()
        form.addRow(tr(lang, "invoice_create_ht"), self.amount_ht)
        self.amount_tva = QLineEdit()
        form.addRow(tr(lang, "invoice_create_tva"), self.amount_tva)
        self.amount_timbre = QLineEdit("0.00")
        form.addRow(tr(lang, "invoice_create_timbre"), self.amount_timbre)
        self.amount_ttc = QLineEdit()
        form.addRow(tr(lang, "invoice_create_ttc"), self.amount_ttc)
        layout.addLayout(form)

        self.create_button = QPushButton(tr(lang, "invoice_create_button"))
        self.create_button.setDefault(True)
        self.create_button.clicked.connect(self._submit)
        layout.addWidget(self.create_button)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)
        if self.supplier_combo.count() == 0:
            self.error_label.setText(tr(lang, "review_empty"))

    def _submit(self) -> None:
        supplier_id = self.supplier_combo.currentData()
        if supplier_id is None:
            self.error_label.setText(tr(self._lang, "review_empty"))
            return
        amount_ht = self.amount_ht.text().strip()
        payload = {
            "supplier_id": int(supplier_id),
            "supplier_invoice_no": self.invoice_no.text().strip(),
            "invoice_date": self.invoice_date.text().strip(),
            "payment_method": str(self.payment_combo.currentData()),
            "amount_ht_dzd": amount_ht,
            "amount_tva_dzd": self.amount_tva.text().strip(),
            "amount_timbre_dzd": self.amount_timbre.text().strip() or "0.00",
            "amount_ttc_dzd": self.amount_ttc.text().strip(),
            # Single-line MVP: the header HT as one validated line, matching
            # the invoice-line contract the service already verifies.
            "lines": [
                {
                    "description": self.invoice_no.text().strip() or "Achat",
                    "qty": "1",
                    "unit_price_dzd": amount_ht,
                    "tax_rate": "19.0",
                    "line_ht_dzd": amount_ht,
                }
            ],
        }
        self.create_button.setEnabled(False)
        worker = ApiWorker(self._client.create_invoice, payload, parent=self)
        self._workers.append(worker)
        worker.succeeded.connect(lambda _result: self.accept())
        worker.failed.connect(self._on_failed)
        worker.start()

    def _on_failed(self, exc: object) -> None:
        self.create_button.setEnabled(True)
        self.error_label.setText(
            tr(self._lang, "invoice_create_failed", detail=str(exc))
        )


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
        self.suppliers_table = make_table([])
        self.stock_table = make_table([])
        self.documents_table = make_table([])
        self.invoices_table = make_table([])
        self.tb_table = make_table([])
        self.tb_footer = QLabel("")
        self.stack.addWidget(self.accounts_table)
        self.stack.addWidget(self.products_table)
        self.stack.addWidget(self.suppliers_table)
        self.stack.addWidget(self.stock_table)

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

        invoices_widget = QWidget()
        invoice_layout = QVBoxLayout(invoices_widget)
        invoice_row = QHBoxLayout()
        self.review_button = QPushButton("")
        self.review_button.clicked.connect(self._open_review)
        self.create_invoice_button = QPushButton("")
        self.create_invoice_button.clicked.connect(self._create_invoice)
        invoice_row.addWidget(self.review_button)
        invoice_row.addWidget(self.create_invoice_button)
        invoice_row.addStretch(1)
        invoice_layout.addLayout(invoice_row)
        invoice_layout.addWidget(self.invoices_table)
        self.stack.addWidget(invoices_widget)

        tb_widget = QWidget()
        tb_layout = QVBoxLayout(tb_widget)
        tb_layout.addWidget(self.tb_table)
        self.tb_footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        tb_layout.addWidget(self.tb_footer)
        self.stack.addWidget(tb_widget)

        # Row → invoice id for the review dialog; filled by the loader.
        self._invoice_ids: List[int] = []
        self._workers: List[ApiWorker] = []
        self.invoices_table.cellDoubleClicked.connect(lambda r, _c: self._open_review())

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
        self.review_button.setText(tr(lang, "review_button"))
        self.create_invoice_button.setText(tr(lang, "invoice_create"))
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
            elif key == "suppliers":
                data = self._client.partners()
                fill_table(
                    self.suppliers_table,
                    [
                        tr(lang, "col_name"),
                        tr(lang, "col_partner_type"),
                        tr(lang, "col_tax_regime"),
                        tr(lang, "col_nif"),
                        tr(lang, "col_wilaya"),
                    ],
                    [
                        [
                            str(r.get("name", "")),
                            str(r.get("partner_type", "")),
                            str(r.get("tax_regime", "")),
                            str(r.get("nif") or ""),
                            str(r.get("wilaya_code") or ""),
                        ]
                        for r in data.get("partners", [])
                    ],
                )
            elif key == "stock":
                data = self._client.stock()
                fill_table(
                    self.stock_table,
                    [
                        tr(lang, "col_warehouse"),
                        tr(lang, "col_sku"),
                        tr(lang, "col_product"),
                        tr(lang, "col_uom"),
                        tr(lang, "col_qty"),
                    ],
                    [
                        [
                            str(r.get("warehouse_code", "")),
                            str(r.get("sku", "")),
                            str(r.get("product_name", "")),
                            str(r.get("uom", "")),
                            str(r.get("qty_on_hand", "")),
                        ]
                        for r in data.get("stock", [])
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
                self._invoice_ids = [int(r["id"]) for r in data.get("invoices", []) if r.get("id") is not None]
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
            partners = self._client.partners(limit=500).get("count", 0)
            stock = self._client.stock(limit=500).get("count", 0)
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
            f"{tr(lang, 'nav_suppliers')}: {partners} — "
            f"{tr(lang, 'nav_stock')}: {stock} — "
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
        # Upload (with server-side OCR processing) runs off the GUI thread so
        # the window stays responsive; the button is disabled meanwhile.
        self.upload_button.setEnabled(False)
        self.statusBar().showMessage(tr(self._lang, "upload_in_progress"))
        worker = self._track(ApiWorker(self._client.upload_document, pathlib.Path(path), parent=self))
        worker.succeeded.connect(self._upload_done)
        worker.failed.connect(self._upload_done_error)
        worker.start()

    def _upload_done(self, _result: object) -> None:
        self.upload_button.setEnabled(True)
        self.statusBar().showMessage("")
        self._load_view(VIEWS.index("documents"))

    def _upload_done_error(self, exc: object) -> None:
        self.upload_button.setEnabled(True)
        self.statusBar().showMessage("")
        self._error(exc)

    def _track(self, worker: ApiWorker) -> ApiWorker:
        """Keep worker references alive; Qt threads are garbage collected."""
        self._workers = [w for w in self._workers if w.isFinished() is False]
        self._workers.append(worker)
        return worker

    def _selected_invoice_id(self) -> Optional[int]:
        selected = self.invoices_table.selectedItems()
        row = selected[0].row() if selected else -1
        if 0 <= row < len(self._invoice_ids):
            return self._invoice_ids[row]
        return None

    def _make_review_dialog(self, invoice_id: int) -> "InvoiceReviewDialog":
        """Construction seam (also used by tests): build the review dialog.

        Only the id is needed: the dialog's own worker loads the detail
        asynchronously - important because a role denied the invoice list
        must still get the dialog and its surfaced server refusal.
        """
        return InvoiceReviewDialog(self._lang, self._client, {"id": invoice_id}, parent=self)

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
