# SPDX-License-Identifier: LGPL-3.0-only
"""Desktop entry point (``dz-erp`` / ``python -m dz_erp.desktop``).

Two ways to run:

- **``dz-erp --smoke``** - a full headless verification of Mode A: bootstrap a
  first-run database in an isolated directory, start the embedded loopback
  service, log in over HTTP, read every list and the trial balance, then shut
  down. Prints a JSON summary and exits 0 on success. It is the CI-runnable
  proof that "the installed application starts and works" without Qt.
- **``dz-erp``** - the graphical application (requires the ``desktop`` extra:
  PySide6). Mode A starts a private loopback service; Mode B connects to the
  company server given by ``--server-url``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

from . import APP_NAME, __version__
from .api_client import ErpApiError, ErpClient
from .config import DesktopConfig, load_config, save_config
from .i18n import is_rtl, tr
from .service import EmbeddedErpService, bootstrap_first_run, has_any_user

# Synthetic first-run credentials for --smoke. They exist only inside the
# throwaway directory created for the run; they are not production secrets
# and must never be presented as such.
_SMOKE_COMPANY = "Société Fictive SARL"
_SMOKE_USER = "admin"
_SMOKE_PASSWORD = "Smoke#2026x"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dz-erp",
        description=f"{APP_NAME} — Algerian local-first ERP desktop application.",
    )
    parser.add_argument("--mode", choices=("local", "server"), help="Operating mode override.")
    parser.add_argument("--server-url", help="Company server URL (Mode B) override.")
    parser.add_argument("--data-dir", help="Data directory override (config, db, logs).")
    parser.add_argument("--language", choices=("fr", "ar"), help="Interface language override.")
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Force the Qt offscreen platform (headless environments/tests).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Headless Mode A verification: bootstrap, serve, log in, read, stop.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.offscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.smoke:
        return run_smoke(Path(args.data_dir) if args.data_dir else None)
    return run_gui(args)


# ----------------------------------------------------------------------
# smoke: headless start → login → read → stop
# ----------------------------------------------------------------------


def run_smoke(data_dir: Optional[Path] = None) -> int:
    created_here = data_dir is None
    data = data_dir or Path(tempfile.mkdtemp(prefix="dz-erp-smoke-"))
    db_path = data / "data" / "erp.sqlite3"
    vault_path = data / "data" / "vault"
    service: Optional[EmbeddedErpService] = None
    client: Optional[ErpClient] = None
    try:
        try:
            bootstrap_first_run(
                db_path,
                company_name=_SMOKE_COMPANY,
                admin_username=_SMOKE_USER,
                admin_password=_SMOKE_PASSWORD,
            )
            service = EmbeddedErpService(
                db_path, vault_path, security_log_path=data / "logs" / "security.jsonl"
            )
            base_url = service.start()
            client = ErpClient(base_url)
            client.login(_SMOKE_USER, _SMOKE_PASSWORD)
            session = client.session()
            payload: dict = {
                "status": "ok",
                "version": __version__,
                "mode": "local",
                "base_url": base_url,
                "port": service.port,
                "data_dir": str(data),
                "user": session.get("username"),
                "role": session.get("role"),
                "accounts": client.accounts().get("count", 0),
                "products": client.products().get("count", 0),
                "documents": client.documents().get("count", 0),
                "invoices": client.invoices().get("count", 0),
                "trial_balance_rows": 0,
            }
            try:
                tb = client.trial_balance()
                payload["trial_balance_rows"] = len(tb.get("rows", []))
                payload["total_debit_dzd"] = tb.get("total_debit_dzd")
            except ErpApiError as exc:
                # An empty ledger has no POSTED entries yet - that is a valid
                # state, not a smoke failure. Anything else is reported.
                if exc.status_code != 409:
                    raise
        except Exception as exc:  # report, don't traceback
            print(
                json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
            )
            return 1
        finally:
            if client is not None:
                client.close()
            if service is not None:
                service.stop()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        if created_here:
            shutil.rmtree(data, ignore_errors=True)


# ----------------------------------------------------------------------
# graphical application
# ----------------------------------------------------------------------


def _apply_overrides(config: DesktopConfig, args: argparse.Namespace) -> None:
    changed = False
    if args.language and args.language != config.language:
        config.language = args.language
        changed = True
    if args.mode and args.mode != config.mode:
        config.mode = args.mode
        changed = True
    if args.server_url and args.server_url != config.server_url:
        config.server_url = args.server_url
        changed = True
    if changed:
        config.validate()
        save_config(config)


def run_gui(args: argparse.Namespace) -> int:
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print(
            f"{APP_NAME} requires the desktop extra (PySide6).\n"
            "Install it with:\n"
            "    pip install algerian-ai-erp[desktop]\n"
            "or directly:\n"
            "    pip install PySide6==6.11.2",
            file=sys.stderr,
        )
        return 2

    from .ui import FirstRunDialog, LoginDialog, MainWindow

    config = load_config(args.data_dir)
    _apply_overrides(config, args)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    lang = config.language
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if is_rtl(lang) else Qt.LayoutDirection.LeftToRight
    )

    service: Optional[EmbeddedErpService] = None
    client: Optional[ErpClient] = None

    try:
        if config.mode == "local":
            mode_label = tr(lang, "mode_local")
            service = EmbeddedErpService(
                Path(config.db_path),
                Path(config.vault_path),
                security_log_path=Path(config.security_log_path),
            )
            base_url = service.start()
            client = ErpClient(base_url)
            fresh_install = not has_any_user(Path(config.db_path))
        else:
            mode_label = tr(lang, "mode_server", url=config.server_url)
            service = None
            client = ErpClient(config.server_url)
            fresh_install = False
    except Exception as exc:  # service start-up failures must be readable
        QMessageBox.critical(None, tr(lang, "error_title"), str(exc))
        return 1

    windows: dict = {}

    def open_main(session: dict) -> None:
        window = MainWindow(lang, client, mode_label)
        window.set_session(session)
        window.show()
        windows["main"] = window
        if "login" in windows:
            windows["login"].hide()
        if "first_run" in windows:
            windows["first_run"].hide()

    def handle_login(username: str, password: str) -> None:
        assert client is not None
        try:
            client.login(username, password)
            session = client.session()
        except ErpApiError as exc:
            windows["login"].show_error(tr(lang, "login_failed", detail=exc.detail))
            return
        open_main(session)

    def handle_first_run(company: str, username: str, password: str) -> None:
        try:
            bootstrap_first_run(
                Path(config.db_path),
                company_name=company,
                admin_username=username,
                admin_password=password,
            )
        except Exception as exc:
            windows["first_run"].show_error(tr(lang, "first_run_failed", detail=str(exc)))
            return
        handle_login(username, password)

    if fresh_install:
        first_run = FirstRunDialog(lang, handle_first_run)
        windows["first_run"] = first_run
        first_run.show()
    else:
        login = LoginDialog(lang, handle_login)
        windows["login"] = login
        login.show()

    def cleanup() -> None:
        if client is not None:
            try:
                if client.is_logged_in:
                    client.logout()
            except ErpApiError:
                pass  # best effort on the way out
            client.close()
        if service is not None:
            service.stop()

    app.aboutToQuit.connect(cleanup)
    exit_code = app.exec()
    return int(exit_code)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
