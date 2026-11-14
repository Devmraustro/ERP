# SPDX-License-Identifier: LGPL-3.0-only
"""Master-data dialogs: create a supplier/customer, create an item (Phase 59B).

Why these dialogs exist
-----------------------
The desktop client could read partners and items but had no way to *create*
them, and neither did the API. A freshly installed company therefore could not
name the supplier that ``POST /api/v1/invoices`` requires by foreign key: the
first real invoice was unreachable without a SQL prompt. That is a
readiness blocker, not a missing feature - it is the first thing an employee
does on day one.

Design rules, inherited from the rest of the client:

* **The server decides.** Every field is validated again by
  ``dz_erp.security_audit.create_partner_secure`` / ``create_product_secure``
  (Algerian fiscal identifiers, SCF account codes, unique keys). The dialog
  only collects text; it never invents a rule the service does not enforce.
* **Failures are visible.** A refusal is rendered as the server's own message
  in the dialog, the dialog stays open with the typed values intact, and the
  button is re-enabled - a create action can never fail silently.
* **No secrets, no tenant.** The company comes from the session token.

The module imports PySide6, so it is imported lazily by the caller and stays
out of the headless server dependency graph.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
)

from . import tokens
from .api_client import ErpClient
from .components import Button, Card, label
from .i18n import tr
from .workers import WorkerOwner


def _combo() -> QComboBox:
    return QComboBox()


class CreatePartnerDialog(QDialog, WorkerOwner):
    """Collect the fiscal identity of a supplier/customer and create it."""

    def __init__(self, lang: str, client: ErpClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._init_workers()
        self.setWindowTitle(tr(lang, "partners_create_title"))
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            tokens.SPACING["xl"], tokens.SPACING["lg"], tokens.SPACING["xl"], tokens.SPACING["lg"]
        )
        root.setSpacing(tokens.SPACING["md"])
        root.addWidget(label(tr(lang, "partners_create_title"), object_name="DialogTitle"))

        card = Card("", object_name="CardFlat", body_margins=(16, 16, 16, 16))
        form = QFormLayout()
        form.setHorizontalSpacing(tokens.SPACING["lg"])
        form.setVerticalSpacing(tokens.SPACING["sm"])

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("EURL … / SARL …")
        form.addRow(label(tr(lang, "partners_create_name"), object_name="FieldLabel"), self.name_input)

        self.type_combo = _combo()
        for value in ("supplier", "customer", "both"):
            self.type_combo.addItem(tr(lang, f"partner_type_{value}"), value)
        form.addRow(label(tr(lang, "partners_create_type"), object_name="FieldLabel"), self.type_combo)

        self.regime_combo = _combo()
        for value in ("REEL", "IFU", "EXEMPT", "FOREIGN"):
            self.regime_combo.addItem(tr(lang, f"tax_regime_{value}"), value)
        form.addRow(label(tr(lang, "partners_create_regime"), object_name="FieldLabel"), self.regime_combo)

        self.nif_input = QLineEdit()
        self.nif_input.setPlaceholderText("001216099123456")
        form.addRow(label(tr(lang, "partners_create_nif"), object_name="FieldLabel"), self.nif_input)

        self.nis_input = QLineEdit()
        form.addRow(label(tr(lang, "partners_create_nis"), object_name="FieldLabel"), self.nis_input)

        self.ai_input = QLineEdit()
        form.addRow(label(tr(lang, "partners_create_ai"), object_name="FieldLabel"), self.ai_input)

        self.rc_input = QLineEdit()
        form.addRow(label(tr(lang, "partners_create_rc"), object_name="FieldLabel"), self.rc_input)

        self.wilaya_input = QLineEdit()
        self.wilaya_input.setPlaceholderText("19")
        form.addRow(label(tr(lang, "partners_create_wilaya"), object_name="FieldLabel"), self.wilaya_input)

        card.add_layout(form)
        root.addWidget(card)

        self.error_label = label("", object_name="ErrorLabel", wrap=True)
        root.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.setSpacing(tokens.SPACING["sm"])
        actions.addStretch(1)
        actions.addWidget(
            Button(tr(lang, "button_cancel"), variant="ghost", on_click=self.reject)
        )
        self.create_button = Button(
            tr(lang, "partners_create_button"), variant="primary", on_click=self._submit
        )
        self.create_button.setDefault(True)
        actions.addWidget(self.create_button)
        root.addLayout(actions)

    def payload(self) -> dict:
        """The request body, with blank optional fields omitted."""
        body = {
            "name": self.name_input.text().strip(),
            "partner_type": str(self.type_combo.currentData()),
            "tax_regime": str(self.regime_combo.currentData()),
        }
        for key, widget in (
            ("nif", self.nif_input),
            ("nis", self.nis_input),
            ("ai", self.ai_input),
            ("rc", self.rc_input),
            ("wilaya_code", self.wilaya_input),
        ):
            text = widget.text().strip()
            if text:
                body[key] = text
        return body

    def _submit(self) -> None:
        body = self.payload()
        if not body["name"]:
            # Client-side courtesy check only; the service is authoritative.
            self.error_label.setText(tr(self._lang, "partners_create_failed", detail="—"))
            self.name_input.setFocus()
            return
        self.error_label.setText("")
        self.create_button.setEnabled(False)
        self.run_async(
            self._client.create_partner,
            body,
            on_success=lambda _result: self.accept(),
            on_error=self._failed,
        )

    def _failed(self, exc: object) -> None:
        self.create_button.setEnabled(True)
        detail = getattr(exc, "detail", str(exc))
        self.error_label.setText(tr(self._lang, "partners_create_failed", detail=detail))


class CreateProductDialog(QDialog, WorkerOwner):
    """Collect an item identity and create it."""

    def __init__(self, lang: str, client: ErpClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._init_workers()
        self.setWindowTitle(tr(lang, "products_create_title"))
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            tokens.SPACING["xl"], tokens.SPACING["lg"], tokens.SPACING["xl"], tokens.SPACING["lg"]
        )
        root.setSpacing(tokens.SPACING["md"])
        root.addWidget(label(tr(lang, "products_create_title"), object_name="DialogTitle"))

        card = Card("", object_name="CardFlat", body_margins=(16, 16, 16, 16))
        form = QFormLayout()
        form.setHorizontalSpacing(tokens.SPACING["lg"])
        form.setVerticalSpacing(tokens.SPACING["sm"])

        self.sku_input = QLineEdit()
        self.sku_input.setPlaceholderText("ART-001")
        form.addRow(label(tr(lang, "products_create_sku"), object_name="FieldLabel"), self.sku_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Papier A4")
        form.addRow(label(tr(lang, "products_create_name"), object_name="FieldLabel"), self.name_input)

        self.uom_combo = _combo()
        for value in ("UNIT", "KG", "L", "M", "M2", "M3", "HOUR", "DAY", "BOX", "PACK"):
            self.uom_combo.addItem(value, value)
        form.addRow(label(tr(lang, "products_create_uom"), object_name="FieldLabel"), self.uom_combo)

        self.cost_input = QLineEdit("0.00")
        form.addRow(label(tr(lang, "products_create_cost"), object_name="FieldLabel"), self.cost_input)

        self.storable_check = QCheckBox(tr(lang, "products_create_storable"))
        self.storable_check.setChecked(True)
        form.addRow(label("", object_name="FieldLabel"), self.storable_check)

        card.add_layout(form)
        root.addWidget(card)

        self.error_label = label("", object_name="ErrorLabel", wrap=True)
        root.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.setSpacing(tokens.SPACING["sm"])
        actions.addStretch(1)
        actions.addWidget(
            Button(tr(lang, "button_cancel"), variant="ghost", on_click=self.reject)
        )
        self.create_button = Button(
            tr(lang, "products_create_button"), variant="primary", on_click=self._submit
        )
        self.create_button.setDefault(True)
        actions.addWidget(self.create_button)
        root.addLayout(actions)

    def payload(self) -> dict:
        return {
            "sku": self.sku_input.text().strip(),
            "name": self.name_input.text().strip(),
            "uom": str(self.uom_combo.currentData()),
            "is_storable": bool(self.storable_check.isChecked()),
            "standard_cost_dzd": self.cost_input.text().strip() or "0.00",
        }

    def _submit(self) -> None:
        body = self.payload()
        if not body["sku"] or not body["name"]:
            self.error_label.setText(tr(self._lang, "products_create_failed", detail="—"))
            (self.sku_input if not body["sku"] else self.name_input).setFocus()
            return
        self.error_label.setText("")
        self.create_button.setEnabled(False)
        self.run_async(
            self._client.create_product,
            body,
            on_success=lambda _result: self.accept(),
            on_error=self._failed,
        )

    def _failed(self, exc: object) -> None:
        self.create_button.setEnabled(True)
        detail = getattr(exc, "detail", str(exc))
        self.error_label.setText(tr(self._lang, "products_create_failed", detail=detail))


__all__ = ["CreatePartnerDialog", "CreateProductDialog"]
