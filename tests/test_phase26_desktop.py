# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 26 — installed desktop client.

What this phase must prove:

1. **Configuration is safe by construction**: only whitelisted (secret-free)
   fields persist, a corrupt preferences file recovers to defaults without
   destroying data, and invalid values are refused at start-up.
2. **Mode A really works**: an embedded loopback service (ephemeral port,
   127.0.0.1 only) built from the same ``serve.build_app`` as a deployment
   boots, first-run creates exactly one company + admin and refuses to
   overwrite an existing installation, the client logs in over HTTP, reads
   the lists and the trial balance, the security-log environment variable is
   restored after app construction, and shutdown is clean.
3. **Errors are typed and actionable** at the client boundary.
4. **The GUI really constructs** (when PySide6 is installed, offscreen) and
   switches to right-to-left layout for Arabic.
5. **``dz-erp --smoke``** is a reproducible headless start→login→read→stop
   run that exits 0 — the CI-runnable proxy for "the installed app starts".
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.desktop import APP_NAME, __version__ as DESKTOP_VERSION
from dz_erp.desktop.api_client import ErpApiError, ErpClient
from dz_erp.desktop.config import (
    DesktopConfig,
    _PERSISTED_FIELDS,
    config_path,
    load_config,
    save_config,
)
from dz_erp.desktop.i18n import is_rtl, tr
from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run, has_any_user

REPO_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_PASSWORD = "Smoke#2026x"


# ----------------------------------------------------------------------
# configuration (Qt-free)
# ----------------------------------------------------------------------


def test_config_persists_whitelist_only(tmp_path: Path) -> None:
    """The on-disk payload is exactly the whitelist - no room for secrets."""
    config = DesktopConfig(
        data_dir=str(tmp_path),
        language="ar",
        mode="server",
        server_url="http://erp.ma-societe.lan:8000",
    )
    path = save_config(config)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == set(_PERSISTED_FIELDS)
    blob = json.dumps(payload).lower()
    assert "password" not in blob
    assert "token" not in blob
    loaded = load_config(str(tmp_path))
    assert loaded.language == "ar"
    assert loaded.mode == "server"
    assert loaded.server_url == "http://erp.ma-societe.lan:8000"


def test_corrupt_config_recovers_to_defaults(tmp_path: Path) -> None:
    """A damaged preferences file is set aside; defaults come back."""
    path = config_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{definitely not json", encoding="utf-8")
    config = load_config(str(tmp_path))
    assert config.language == "fr"
    assert list(path.parent.glob("config.json.corrupt-*")), "corrupt file was silently deleted"


def test_config_validation_refuses_bad_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DesktopConfig(data_dir=str(tmp_path), language="en").validate()
    with pytest.raises(ValueError):
        DesktopConfig(data_dir=str(tmp_path), mode="server", server_url="").validate()
    with pytest.raises(ValueError):
        DesktopConfig(data_dir=str(tmp_path), mode="server", server_url="ftp://x").validate()
    # Defaults must always be a valid, startable configuration.
    DesktopConfig().validate()


# ----------------------------------------------------------------------
# i18n (Qt-free)
# ----------------------------------------------------------------------


def test_i18n_covers_both_languages_and_rtl() -> None:
    assert is_rtl("ar") and not is_rtl("fr")
    for key in ("app_title", "login_title", "nav_trial_balance", "tb_total_debit"):
        assert tr("fr", key) and tr("fr", key) != key
        assert tr("ar", key) and tr("ar", key) != key
    # Missing key falls back to the key itself, never a crash.
    assert tr("fr", "no_such_key") == "no_such_key"
    # Unknown language falls back to French.
    assert tr("es", "login_title") == tr("fr", "login_title")


def test_desktop_package_version_is_declared() -> None:
    assert APP_NAME == "AlgerianERP"
    assert DESKTOP_VERSION


# ----------------------------------------------------------------------
# Mode A: embedded loopback service (Qt-free)
# ----------------------------------------------------------------------


def test_embedded_service_refuses_non_loopback_bind(tmp_path: Path) -> None:
    """A LAN-visible bind from the embedded service is a design violation."""
    with pytest.raises(ValueError, match="loopback"):
        EmbeddedErpService(
            tmp_path / "db" / "erp.sqlite3", tmp_path / "vault", host="0.0.0.0"
        )


def _direct_bootstrap(db_path: Path) -> None:
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (code, name, nif, created_at) VALUES ('DZ1', 'Existant SARL', NULL, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (company_id, username, role, is_active, password_hash) "
            "VALUES (1, 'existing', 'admin', 1, ?)",
            (hash_password("Existing#2026x", iterations=1000),),
        )


def test_first_run_refuses_existing_installation(tmp_path: Path) -> None:
    """Re-running first-run must never overwrite an existing admin."""
    db_path = tmp_path / "erp.sqlite3"
    _direct_bootstrap(db_path)
    with pytest.raises(RuntimeError, match="already contains users"):
        bootstrap_first_run(
            db_path,
            company_name="Nouvelle SARL",
            admin_username="attacker",
            admin_password="Attacker#2026x",
        )
    assert has_any_user(db_path)
    raw = sqlite3.connect(str(db_path))
    try:
        usernames = {row[0] for row in raw.execute("SELECT username FROM users").fetchall()}
    finally:
        raw.close()
    assert "attacker" not in usernames

def test_mode_a_full_cycle_start_login_read_stop(tmp_path: Path) -> None:
    """The real Mode A loop: bootstrap → serve → login → read → stop."""
    db_path = tmp_path / "data" / "erp.sqlite3"
    vault_path = tmp_path / "data" / "vault"
    security_log = tmp_path / "logs" / "security.jsonl"

    user = bootstrap_first_run(
        db_path,
        company_name="Société Fictive SARL",
        admin_username="admin",
        admin_password=_SMOKE_PASSWORD,
    )
    assert user >= 1

    # The security-log environment variable is process-global; the service
    # must restore whatever was there after constructing the app.
    sentinel = "sentinel-security-log-path"
    os.environ["ERP_SECURITY_LOG_PATH"] = sentinel
    service = EmbeddedErpService(db_path, vault_path, security_log_path=security_log)
    try:
        base_url = service.start()
        assert os.environ.get("ERP_SECURITY_LOG_PATH") == sentinel
        assert base_url.startswith("http://127.0.0.1:")
        assert service.port > 0  # ephemeral, not a fixed port

        client = ErpClient(base_url)
        # Without a token every call is refused locally with a typed error.
        with pytest.raises(ErpApiError, match="Not logged in"):
            client.accounts()
        # Wrong credentials are refused by the server with 401.
        with pytest.raises(ErpApiError) as wrong:
            client.login("admin", "Wrong#2026x")
        assert wrong.value.status_code == 401
        # Correct credentials: full read cycle.
        client.login("admin", _SMOKE_PASSWORD)
        session = client.session()
        assert session["username"] == "admin"
        assert session["role"] == "admin"
        # A fresh first-run database is empty: lists exist and answer, empty.
        assert client.accounts()["accounts"] == []
        assert client.products()["products"] == []
        assert client.documents()["documents"] == []
        assert client.invoices()["invoices"] == []
        trial = client.trial_balance()
        assert trial["balanced"] is True
        assert trial["rows"] == []
        assert trial["total_debit_dzd"] == "0.00"
        client.logout()
        client.close()
    finally:
        service.stop()
        os.environ.pop("ERP_SECURITY_LOG_PATH", None)
    with pytest.raises(RuntimeError, match="not running"):
        service.port  # the service reports itself stopped


# ----------------------------------------------------------------------
# dz-erp --smoke: the headless "installed app starts" proof
# ----------------------------------------------------------------------


def test_smoke_command_full_cycle(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    data_dir = tmp_path / "smoke-data"
    proc = subprocess.run(
        [sys.executable, "-m", "dz_erp.desktop", "--smoke", "--data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["mode"] == "local"
    assert payload["user"] == "admin"
    assert payload["port"] > 0
    assert payload["base_url"].startswith("http://127.0.0.1:")
    # The data dir was user-provided, so it must survive as evidence.
    assert (data_dir / "data" / "erp.sqlite3").is_file()


# ----------------------------------------------------------------------
# GUI (PySide6 optional; offscreen)
# ----------------------------------------------------------------------


def _qt_app():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_mainwindow_constructs_and_switches_to_rtl(tmp_path: Path) -> None:
    """The real window builds against a live embedded service; AR flips RTL."""
    app = _qt_app()
    from PySide6.QtCore import Qt

    from dz_erp.desktop.ui import MainWindow
    from dz_erp.desktop.ui import VIEWS

    db_path = tmp_path / "data" / "erp.sqlite3"
    bootstrap_first_run(
        db_path,
        company_name="Société Fictive SARL",
        admin_username="admin",
        admin_password=_SMOKE_PASSWORD,
    )
    service = EmbeddedErpService(
        db_path, tmp_path / "data" / "vault", security_log_path=tmp_path / "logs" / "security.jsonl"
    )
    try:
        client = ErpClient(service.start())
        client.login("admin", _SMOKE_PASSWORD)
        window = MainWindow("fr", client, "test mode")
        assert window.nav.count() == len(VIEWS)
        # Switch to Arabic → right-to-left layout at application level.
        window._switch_language("ar")
        assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
        # And back to French → left-to-right again.
        window._switch_language("fr")
        assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
        client.close()
    finally:
        service.stop()
