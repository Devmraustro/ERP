# SPDX-License-Identifier: LGPL-3.0-only
"""User administration dialog (Phase 29).

Same controls, same server authority as Phase 28: the role list comes from
``dz_erp.auth.VALID_ROLES``, the 8-character floor is restated as a hint while
the API remains the only validator, and a server refusal (duplicate username,
unknown role, a non-admin caller) lands in the dialog's error line instead of
vanishing. The redesign changes the presentation - a card, a field grid, a
primary/secondary action pair - and nothing about the contract.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout

from ..auth import VALID_ROLES
from . import tokens
from .api_client import ErpClient
from .components import Button, Card, label
from .i18n import tr
from .workers import WorkerOwner


class CreateUserDialog(QDialog, WorkerOwner):
    """Administrator form to create a user account in the tenant."""

    def __init__(self, lang: str, client: ErpClient, parent: Any = None) -> None:
        super().__init__(parent)
        self._lang = lang
        self._client = client
        self._init_workers()
        self.created_user: Optional[Dict[str, Any]] = None
        self.setWindowTitle(tr(lang, "users_create_title"))
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            tokens.SPACING["xl"], tokens.SPACING["lg"], tokens.SPACING["xl"], tokens.SPACING["lg"]
        )
        root.setSpacing(tokens.SPACING["md"])
        root.addWidget(label(tr(lang, "users_create_title"), object_name="DialogTitle"))

        card = Card("", object_name="CardFlat", body_margins=(16, 16, 16, 16))
        form = QFormLayout()
        form.setHorizontalSpacing(tokens.SPACING["lg"])
        form.setVerticalSpacing(tokens.SPACING["sm"])
        self.username = QLineEdit()
        form.addRow(label(tr(lang, "col_username"), object_name="FieldLabel"), self.username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(label(tr(lang, "users_field_password"), object_name="FieldLabel"), self.password)
        from PySide6.QtWidgets import QComboBox

        self.role_combo = QComboBox()
        for role in VALID_ROLES:
            self.role_combo.addItem(role, role)
        form.addRow(label(tr(lang, "col_role"), object_name="FieldLabel"), self.role_combo)
        card.add_layout(form)
        hint = label(tr(lang, "users_password_hint"), object_name="Muted", wrap=True)
        card.add_widget(hint)
        root.addWidget(card)

        self.error_label = label("", object_name="ErrorLabel", wrap=True)
        root.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.setSpacing(tokens.SPACING["sm"])
        actions.addStretch(1)
        actions.addWidget(Button(tr(lang, "button_cancel"), variant="ghost", on_click=self.reject))
        self.create_button = Button(
            tr(lang, "users_create_button"), variant="primary", on_click=self._submit
        )
        self.create_button.setDefault(True)
        actions.addWidget(self.create_button)
        root.addLayout(actions)

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username:
            self.error_label.setText(tr(self._lang, "users_username_required"))
            return
        if len(password) < 8:
            self.error_label.setText(tr(self._lang, "users_password_short"))
            return
        self.create_button.setEnabled(False)
        self.run_async(
            self._client.create_user,
            username,
            password,
            str(self.role_combo.currentData()),
            on_success=self._on_created,
            on_error=self._on_failed,
        )

    def _on_created(self, result: object) -> None:
        self.created_user = dict(result) if isinstance(result, dict) else {}
        self.accept()

    def _on_failed(self, exc: object) -> None:
        self.create_button.setEnabled(True)
        self.error_label.setText(tr(self._lang, "users_create_failed", detail=str(exc)))


__all__ = ["CreateUserDialog"]
