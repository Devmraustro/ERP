# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 28 — desktop user administration UI.

What this phase must prove (offscreen Qt against the real embedded service):

1. The users view is reachable for an administrator and lists the tenant's
   accounts (never credential material), with the role resolved from the
   server session; the nav row is hidden for every non-admin role.
2. Creating a user through the dialog really drives the authorized API: the
   created account can log in, and the creation is audited with the acting
   admin as actor. Local validation (blank username, short password) refuses
   the form without any API call; server refusals (duplicate username) are
   surfaced in the dialog instead of vanishing.
3. Deactivation requires confirmation, takes effect for a live session
   immediately (the token is rejected at the next request) and is audited.

All data is synthetic.
"""

import os
import sqlite3
import time
from pathlib import Path

import pytest

from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from dz_erp.desktop import ui as ui_module
from dz_erp.desktop.api_client import ErpApiError, ErpClient
from dz_erp.desktop.ui import VIEWS, CreateUserDialog, MainWindow

_ADMIN = ("admin", "Smoke#2026x")
_CLERK = ("temp_ui", "Clerk#2026x")
_NEW_USER = ("clerk_ui", "ClerkUI#2026x")


class _MessageBoxStandIn:
    """QMessageBox stands in: modal dialogs would hang the offscreen test.

    ``critical``/``information`` are recorded; ``question`` answers from
    ``answer`` (default Yes) so the deactivation confirmation proceeds.
    """

    def __init__(self) -> None:
        self.criticals: list = []
        self.infos: list = []

    def install(self, monkeypatch: pytest.MonkeyPatch, answer: int = QMessageBox.StandardButton.Yes) -> None:
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
        monkeypatch.setattr(ui_module.QMessageBox, "question", lambda *a, **k: answer)


def _drain_workers(holder: object, timeout_s: float = 30.0) -> None:
    """Process events until every API worker of ``holder`` has delivered."""
    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        workers = getattr(holder, "_workers", [])
        if not workers:
            return
        if all(w.isFinished() for w in workers):
            app.processEvents()
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
                "SELECT actor_user_id, action_type, target_model, target_id "
                "FROM audit_events WHERE action_type = ?",
                (action_type,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _service_with_fixtures(tmp_path: Path):
    """Bootstrap + a seeded clerk + embedded loopback service."""
    db_path = tmp_path / "data" / "erp.sqlite3"
    bootstrap_first_run(
        db_path,
        company_name="Société Fictive SARL",
        admin_username=_ADMIN[0],
        admin_password=_ADMIN[1],
    )
    from dz_erp.auth import hash_password
    from dz_erp.database import ERPDatabase
    from dz_erp.intake_ocr import PrivateDocumentVault

    PrivateDocumentVault(tmp_path / "data" / "vault")
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (7, 1, 'temp_ui', 'clerk', 1, ?)",
            (hash_password(_CLERK[1], iterations=1000),),
        )
    service = EmbeddedErpService(
        db_path, tmp_path / "data" / "vault", security_log_path=tmp_path / "logs" / "security.jsonl"
    )
    return db_path, service


def _row_texts(table, row: int) -> list:
    return [table.item(row, c).text() for c in range(table.columnCount())]


def _find_user_row(window: MainWindow, username: str) -> int:
    for row in range(window.users_table.rowCount()):
        if window.users_table.item(row, 0).text() == username:
            return row
    raise AssertionError(f"user '{username}' not listed in the users view")


# ----------------------------------------------------------------------
# visibility and listing
# ----------------------------------------------------------------------


def test_users_view_visible_for_admin_and_lists_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _MessageBoxStandIn()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        window = MainWindow("fr", client, "test mode")
        # Before the session role is known, the view is hidden by default.
        assert window.nav.isRowHidden(VIEWS.index("users"))
        window.set_session(client.session())  # mirrors app.py's login flow
        assert not window.nav.isRowHidden(VIEWS.index("users"))
        assert window.nav.item(VIEWS.index("users")).text() == "Utilisateurs"

        window._load_view(VIEWS.index("users"))
        usernames = sorted(
            _row_texts(window.users_table, r)[0] for r in range(window.users_table.rowCount())
        )
        assert usernames == ["admin", "temp_ui"]
        clerk_row = _find_user_row(window, "temp_ui")
        cells = _row_texts(window.users_table, clerk_row)
        assert cells[1] == "clerk"
        assert cells[2] == "actif"
        # The table never carries credential material.
        for r in range(window.users_table.rowCount()):
            assert "hash" not in " ".join(_row_texts(window.users_table, r)).lower()

        # AR labels resolve for the same view.
        window._switch_language("ar")
        assert window.nav.item(VIEWS.index("users")).text() == "المستخدمون"
        window._switch_language("fr")
        client.close()
    finally:
        service.stop()


def test_users_view_hidden_for_non_admin(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_CLERK[0], _CLERK[1])
        window = MainWindow("fr", client, "test mode")
        window.set_session(client.session())
        assert window.nav.isRowHidden(VIEWS.index("users"))
        client.close()
    finally:
        service.stop()


# ----------------------------------------------------------------------
# creation dialog
# ----------------------------------------------------------------------


def _fill_create_dialog(dialog: CreateUserDialog, username: str, password: str, role: str) -> None:
    dialog.username.setText(username)
    dialog.password.setText(password)
    dialog.role_combo.setCurrentIndex(dialog.role_combo.findData(role))
    dialog._submit()


def test_create_user_via_dialog_then_created_account_logs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _MessageBoxStandIn()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        base = service.start()
        client = ErpClient(base)
        client.login(_ADMIN[0], _ADMIN[1])
        window = MainWindow("fr", client, "test mode")
        window.set_session(client.session())

        def fake_exec(self: CreateUserDialog) -> int:
            _fill_create_dialog(self, _NEW_USER[0], _NEW_USER[1], "clerk")
            _drain_workers(self)
            return self.result()

        monkeypatch.setattr(CreateUserDialog, "exec", fake_exec)
        window._create_user()
        _drain_workers(window)

        # The new account is listed as active...
        row = _find_user_row(window, _NEW_USER[0])
        assert _row_texts(window.users_table, row)[2] == "actif"
        # ...really holds a working credential over HTTP...
        fresh = ErpClient(base)
        fresh.login(_NEW_USER[0], _NEW_USER[1])
        fresh.close()
        # ...and the creation is audited with the acting admin as actor.
        events = _audit(db_path, "AUTH_USER_CREATED")
        assert any(e["actor_user_id"] == 1 for e in events)
        client.close()
    finally:
        service.stop()


def test_create_dialog_local_validation_makes_no_api_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _MessageBoxStandIn()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        dialog = CreateUserDialog("fr", client)

        dialog.username.setText("")
        dialog.password.setText("ClerkUI#2026x")
        dialog._submit()
        assert "requis" in dialog.error_label.text()
        assert dialog._workers == []  # refused locally, no API call

        dialog.username.setText("clerk_ui")
        dialog.password.setText("ClerkUI")  # 7 characters
        dialog._submit()
        assert "8 caractères" in dialog.error_label.text()
        assert dialog._workers == []
        client.close()
    finally:
        service.stop()


def test_create_dialog_surfaces_server_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _MessageBoxStandIn()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        dialog = CreateUserDialog("fr", client)

        # The seeded clerk's username is already taken -> server 409.
        _fill_create_dialog(dialog, _CLERK[0], "ClerkUI#2026x", "clerk")
        _drain_workers(dialog)
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.created_user is None
        assert "Création impossible" in dialog.error_label.text()
        assert "HTTP 409" in dialog.error_label.text()
        client.close()
    finally:
        service.stop()


# ----------------------------------------------------------------------
# deactivation
# ----------------------------------------------------------------------


def test_deactivation_confirmation_takes_effect_and_is_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _MessageBoxStandIn()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        base = service.start()
        client = ErpClient(base)
        client.login(_ADMIN[0], _ADMIN[1])
        window = MainWindow("fr", client, "test mode")
        window.set_session(client.session())
        window._load_view(VIEWS.index("users"))

        # The target holds a live session: it must die at the next request.
        clerk_client = ErpClient(base)
        clerk_client.login(_CLERK[0], _CLERK[1])
        assert clerk_client._token

        window.users_table.selectRow(_find_user_row(window, _CLERK[0]))
        window._deactivate_user()  # the patched QMessageBox answers Yes
        _drain_workers(window)

        cells = _row_texts(window.users_table, _find_user_row(window, _CLERK[0]))
        assert cells[2] == "désactivé"

        # The live token is rejected at the next request.
        with pytest.raises(ErpApiError) as refused:
            clerk_client.session()
        assert refused.value.status_code == 401

        # ...and the deactivation is audited with the acting admin as actor.
        events = _audit(db_path, "AUTH_USER_DEACTIVATED")
        assert any(e["actor_user_id"] == 1 for e in events)
        assert not recorder.criticals, recorder.criticals
        client.close()
    finally:
        service.stop()


def test_deactivation_without_selection_shows_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    QApplication.instance() or QApplication([])
    recorder = _MessageBoxStandIn()
    recorder.install(monkeypatch)
    db_path, service = _service_with_fixtures(tmp_path)
    try:
        client = ErpClient(service.start())
        client.login(_ADMIN[0], _ADMIN[1])
        window = MainWindow("fr", client, "test mode")
        window.set_session(client.session())
        window._load_view(VIEWS.index("users"))

        window._deactivate_user()  # nothing selected
        assert len(recorder.infos) == 1, recorder.infos
        client.close()
    finally:
        service.stop()
