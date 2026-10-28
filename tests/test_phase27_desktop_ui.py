# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 27 — desktop UI wiring for review, correction and approval.

What this phase must prove (offscreen Qt against the real embedded service):

1. The navigation gains the suppliers and stock views, in both languages,
   with the RTL switch intact.
2. The invoice review dialog really drives the verified workflow over HTTP:
   it shows the document state, the OCR fields with honest confidence
   display, and it corrects a field through the authorized API (raw OCR and
   confidence preserved, audit event written).
3. Approval and posting are explicit UI actions wired to the API; the
   ledger shows the posted entries in the trial balance, and a duplicate
   post is refused by the server and surfaced as a readable error.
4. An unauthorized role gets the server's refusal surfaced as an error
   (never a silent empty state), and an unknown invoice shows the 404 error
   state.
5. The dashboard reflects the new views' counts.

All data is synthetic (synthetic OCR fixtures, synthetic partners/invoices).
"""

import os
import sqlite3
import time
from pathlib import Path

import pytest

from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QDialog

from dz_erp.desktop.api_client import ErpClient
from dz_erp.desktop.ui import VIEWS, InvoiceReviewDialog, MainWindow

_ADMIN = ("admin", "Smoke#2026x")
_DOC_BYTES = b"%PDF-1.4\n% synthetic phase-27 UI fixture\n"
_SHA = "c" * 64


def _drain_until_idle(dialog: QDialog, timeout_s: float = 30.0) -> None:
    """Process events until every API worker has finished and delivered."""
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        workers = getattr(dialog, "_workers", [])
        busy = any(not w.isFinished() for w in workers)
        if not busy and dialog.correction_button.isEnabled():
            app.processEvents()
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for API workers to finish")


def _audit(db_path: Path, action_type: str) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT action_type, target_model, target_id FROM audit_events WHERE action_type = ?",
                (action_type,),
            ).fetchall()
        ]
    finally:
        conn.close()


class _DialogRecorder:
    """QMessageBox stands in: modal dialogs would hang the offscreen test."""

    def __init__(self) -> None:
        self.criticals: list = []
        self.infos: list = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dz_erp.desktop import ui as ui_module

        monkeypatch.setattr(
            ui_module.QMessageBox,
            "critical",
            lambda *a, **k: self.criticals.append((a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else "")),
        )
        monkeypatch.setattr(
            ui_module.QMessageBox,
            "information",
            lambda *a, **k: self.infos.append((a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else "")),
        )


def _service_with_fixtures(tmp_path: Path):
    """Bootstrap + synthetic tenant data + embedded loopback service."""
    db_path = tmp_path / "data" / "erp.sqlite3"
    bootstrap_first_run(
        db_path,
        company_name="Société Fictive SARL",
        admin_username=_ADMIN[0],
        admin_password=_ADMIN[1],
    )
    from dz_erp.database import ERPDatabase
    from dz_erp.intake_ocr import PrivateDocumentVault

    vault = PrivateDocumentVault(tmp_path / "data" / "vault")
    blob_path = vault.store_bytes(1, _SHA, _DOC_BYTES)
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        from dz_erp.auth import hash_password

        # A warehouse user (invoice reads denied) and an approver (SoD: the
        # admin who creates invoices cannot approve them).
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (7, 1, 'wh_ui', 'warehouse', 1, ?)",
            (hash_password("Wh#2026x", iterations=1000),),
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (8, 1, 'appr_ui', 'approver', 1, ?)",
            (hash_password("Appr#2026x", iterations=1000),),
        )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, tax_regime, nif, wilaya_code) "
            "VALUES (10, 1, 'Fournisseur Synthétique', 'supplier', 'REEL', '001216099123456', '16')"
        )
        conn.execute(
            "INSERT INTO products (id, company_id, sku, name, uom, is_storable, standard_cost_dzd) "
            "VALUES (5, 1, 'SKU-U1', 'Article Synthétique', 'UNIT', 1, '10000.000000')"
        )
        conn.execute(
            "INSERT INTO stock_quants (company_id, warehouse_code, product_id, qty_on_hand) "
            "VALUES (1, 'ALG-WH', 5, '3.000000')"
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, "
            "page_count, file_sha256, vault_path, security_status, workflow_state, "
            "uploaded_by_id, uploaded_at) "
            "VALUES ('doc-1', 1, 'facture-syn.pdf', 'application/pdf', ?, 1, ?, ?, "
            "'CLEAN', 'NeedsReview', 1, '2026-10-27T10:00:00+00:00')",
            (len(_DOC_BYTES), _SHA, blob_path),
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index, "
            "field_name, raw_text, normalized_value, confidence, confidence_kind, "
            "confidence_calibrated, bbox_json, ocr_model_version, created_at) "
            "VALUES (1, 'doc-1', 1, 0, 'supplier_invoice_no', 'FAC-UI-001', 'FAC-UI-001', "
            "0.42, 'engine_line', 0, '{}', 'synthetic-ocr-1', '2026-10-27T10:00:01+00:00')"
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index, "
            "field_name, raw_text, normalized_value, confidence, confidence_kind, "
            "confidence_calibrated, bbox_json, ocr_model_version, created_at) "
            "VALUES (1, 'doc-1', 1, 1, 'amount_ttc_dzd', '11 9OO DD', NULL, "
            "NULL, 'not_measured', 0, '{}', 'synthetic-ocr-1', '2026-10-27T10:00:01+00:00')"
        )
    service = EmbeddedErpService(
        db_path, tmp_path / "data" / "vault", security_log_path=tmp_path / "logs" / "security.jsonl"
    )
    return db_path, service


def _create_invoice_via_api(client: ErpClient) -> int:
    result = client.create_invoice(
        {
            "supplier_id": 10,
            "supplier_invoice_no": "FAC-UI-001",
            "invoice_date": "2026-10-20",
            "payment_method": "BANK_TRANSFER",
            "amount_ht_dzd": "10000.00",
            "amount_tva_dzd": "1900.00",
            "amount_timbre_dzd": "0.00",
            "amount_ttc_dzd": "11900.00",
            "document_id": "doc-1",
            "lines": [
                {
                    "description": "Article Synthétique",
                    "qty": "1",
                    "unit_price_dzd": "10000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "10000.00",
                }
            ],
        }
    )
    return int(result["invoice_id"])


# ----------------------------------------------------------------------
# navigation, i18n and RTL with the new views
# ----------------------------------------------------------------------


def test_new_views_present_and_rtl_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    from PySide6.QtCore import Qt

    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        window = MainWindow("fr", client, "test mode")
        assert window.nav.count() == len(VIEWS)
        assert "suppliers" in VIEWS and "stock" in VIEWS

        # The suppliers view lists the synthetic partner; stock lists the quant.
        window._load_view(VIEWS.index("suppliers"))
        assert window.suppliers_table.rowCount() == 1
        assert window.suppliers_table.item(0, 0).text() == "Fournisseur Synthétique"
        window._load_view(VIEWS.index("stock"))
        assert window.stock_table.rowCount() == 1
        assert window.stock_table.item(0, 0).text() == "ALG-WH"

        # Dashboard reflects all six counts.
        window._load_view(0)
        summary = window.dash_summary.text()
        assert "Fournisseurs" in summary and "Stocks" in summary

        # RTL switch still works with the enlarged view set.
        window._switch_language("ar")
        assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
        assert window.nav.item(VIEWS.index("suppliers")).text() == "الموردون والعملاء"
        window._switch_language("fr")
        assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
        client.close()
    finally:
        service.stop()


# ----------------------------------------------------------------------
# review dialog: load, correct, approve, post, duplicate refusal
# ----------------------------------------------------------------------


def test_review_dialog_full_cycle_with_correction_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _DialogRecorder()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        invoice_id = _create_invoice_via_api(client)

        window = MainWindow("fr", client, "test mode")
        dialog = window._make_review_dialog(invoice_id)
        _drain_until_idle(dialog)

        # Loaded: the document state badge (documents track the invoice
        # workflow) and the fields with honest confidence display.
        assert "Validated" in dialog.state_badge.text()
        assert dialog.fields_table.rowCount() == 2
        # amount_ttc_dzd: no confidence measured — displayed, never invented.
        assert dialog.fields_table.item(1, 3).text() == "non mesurée"
        # supplier_invoice_no: engine line confidence shown as measured.
        assert dialog.fields_table.item(0, 3).text().startswith("0.42")
        assert dialog.fields_table.item(0, 1).text() == "FAC-UI-001"  # raw OCR

        # Correction through the authorized API, on the uncertain field.
        dialog.fields_table.selectRow(1)
        dialog.correction_edit.setText("11900.00")
        dialog._correct_selected_field()
        _drain_until_idle(dialog)
        row = [dialog.fields_table.item(1, c).text() for c in range(dialog.fields_table.columnCount())]
        assert row[4] != "—"  # an override is now displayed
        assert row[1] == "11 9OO DD"  # raw OCR text is preserved
        assert row[2] == "11900.00"  # corrected current value
        events = _audit(db_path, "OCR_HUMAN_FIELD_EDIT")
        assert any(e["target_model"] == "ocr_extracted_fields" for e in events)

        # Explicit submit → approve → post, each an explicit UI action.
        # Segregation of duties is a server control the UI inherits: the
        # creator (admin) cannot approve their own invoice, so approval is
        # done by a separate approver user through the same dialog.
        dialog._submit_approval()
        _drain_until_idle(dialog)
        states = {i["id"]: i["workflow_state"] for i in client.invoices()["invoices"]}
        assert states[invoice_id] == "PendingApproval"
        assert "PendingApproval" in dialog.state_badge.text()

        client_approver = ErpClient(service.base_url)
        client_approver.login("appr_ui", "Appr#2026x")
        dialog_approver = InvoiceReviewDialog("fr", client_approver, {"id": invoice_id})
        _drain_until_idle(dialog_approver)
        dialog_approver._decide(True)
        _drain_until_idle(dialog_approver)
        states = {i["id"]: i["workflow_state"] for i in client.invoices()["invoices"]}
        assert states[invoice_id] == "Approved"

        dialog._post_invoice()
        _drain_until_idle(dialog)
        states = {i["id"]: i["workflow_state"] for i in client.invoices()["invoices"]}
        assert states[invoice_id] == "Posted", f"post failed, criticals={recorder.criticals}"
        trial = client.trial_balance()
        assert trial["balanced"] is True
        assert any(r["account_code"] == "401" for r in trial["rows"])
        # Duplicate posting is refused by the server (idempotency + state
        # machine) and surfaced as an error, not silently ignored.
        dialog._post_invoice()
        _drain_until_idle(dialog)
        assert len(recorder.criticals) == 1
        posted_again = {i["id"]: i["workflow_state"] for i in client.invoices()["invoices"]}
        assert posted_again[invoice_id] == "Posted"
        client.close()
    finally:
        service.stop()


def _rows_count(db_path: Path, sql: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def test_review_dialog_surfaces_unauthorized_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _DialogRecorder()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        # Warehouse role: financial invoice reads are denied server-side.
        admin_client = ErpClient(service.start())
        admin_client.login(_ADMIN[0], _ADMIN[1])
        client = ErpClient(service.base_url)
        client.login("wh_ui", "Wh#2026x")
        window = MainWindow("fr", client, "test mode")
        # The dashboard already surfaced its own 403 for the invoice list;
        # measure only what the review dialog reports from here on.
        recorder.criticals.clear()
        invoice_id = _create_invoice_via_api(admin_client)
        dialog = window._make_review_dialog(invoice_id)
        _drain_until_idle(dialog)
        # The server's 403 surfaces as a readable error; nothing loads.
        assert len(recorder.criticals) == 1
        assert "403" in str(recorder.criticals[0][1])
        assert dialog.fields_table.rowCount() == 0
        events = _audit(db_path, "SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED")
        assert any(e["target_model"] == "supplier_invoices" for e in events)
        client.close()
    finally:
        service.stop()


def test_review_dialog_surfaces_unknown_invoice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _DialogRecorder()
    recorder.install(monkeypatch)
    _db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        window = MainWindow("fr", client, "test mode")
        dialog = window._make_review_dialog(424242)
        _drain_until_idle(dialog)
        assert len(recorder.criticals) == 1
        assert "404" in str(recorder.criticals[0][1])
        client.close()
    finally:
        service.stop()
