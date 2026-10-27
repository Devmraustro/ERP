# SPDX-License-Identifier: LGPL-3.0-only
"""Embedded loopback ERP service for Mode A (single workstation).

Mode A means the accounting data lives on the user's own machine and core
operations work without internet. The installed desktop client achieves this
by starting a *private* ERP service in-process:

- bound to ``127.0.0.1`` on an **ephemeral** port (0), so nothing outside the
  workstation can reach it and no fixed port can collide or be probed;
- built by the very same ``serve.build_app`` the LAN deployment runs, so the
  embedded service inherits every verified control (migrations, audit chain,
  RBAC, token TTL, fail-closed key-provider check) rather than a parallel
  implementation;
- shut down deterministically when the client exits.

The client never speaks SQL. It talks HTTP to this service exactly like a
Mode B client talks to a company server - one code path for both modes.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import uvicorn

from ..auth import AuthManager

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def has_any_user(db_path: Path) -> bool:
    """True when the users table already contains a row (mirrors serve.py).

    A missing ``users`` table means a fresh (never-migrated) database, not an
    error - otherwise a brand-new install could never reach first-run.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            return int(cur.fetchone()[0]) > 0
        except sqlite3.OperationalError:
            return False
    finally:
        conn.close()


def bootstrap_first_run(
    db_path: Path,
    *,
    company_name: str,
    admin_username: str,
    admin_password: str,
) -> int:
    """Create the local database, the first company, and its admin user.

    Mirrors ``serve._bootstrap_admin`` except the password comes from the
    first-run dialog instead of a terminal prompt. Refuses to touch a
    database that already has users: re-running first-run must never
    overwrite an existing company's admin, and a silent overwrite would be
    an account-takeover bug.
    """
    from .config import APP_NAME  # noqa: F401 - imported for error text below

    if not company_name.strip():
        raise ValueError("Company name is required.")
    if not admin_username.strip():
        raise ValueError("Admin username is required.")
    if not admin_password:
        raise ValueError("Admin password is required.")

    # A first-run path may point anywhere (the data dir may not exist yet);
    # create it, otherwise sqlite3 cannot even open the file.
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if has_any_user(db_path):
        raise RuntimeError(
            f"The database at {db_path} already contains users. "
            "Refusing to overwrite an existing installation; log in instead."
        )

    from ..database import ERPDatabase

    db = ERPDatabase(str(db_path))  # applies schema migrations, audit keys
    conn = db._create_connection()
    try:
        cur = conn.execute(
            "INSERT INTO companies (code, name, nif, created_at) VALUES (?, ?, ?, ?)",
            (
                f"DESK-{admin_username.upper()}",
                company_name.strip(),
                None,
                _utcnow().isoformat(),
            ),
        )
        company_id = int(cur.lastrowid)
    finally:
        conn.close()

    auth = AuthManager(db)
    user = auth.create_user(
        username=admin_username.strip(),
        password=admin_password,
        role="admin",
        company_id=company_id,
        actor_user_id=None,
    )
    return int(user["user_id"])


class EmbeddedErpService:
    """A private ERP API server for this client, on the loopback interface.

    Lifecycle: construct → :meth:`start` → use ``base_url`` → :meth:`stop`.
    ``start`` returns only after ``/healthz`` answers 200, so callers can log
    in immediately.
    """

    def __init__(
        self,
        db_path: Path,
        vault_path: Path,
        *,
        host: str = "127.0.0.1",
        security_log_path: Optional[Path] = None,
    ) -> None:
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                f"The embedded service binds the loopback interface only, got host={host!r}. "
                "For multi-user access deploy Mode B (a company server), never expose this process."
            )
        self.db_path = Path(db_path)
        self.vault_path = Path(vault_path)
        self.host = host
        self.security_log_path = (
            Path(security_log_path) if security_log_path else self.db_path.parent / "security.jsonl"
        )
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._port: Optional[int] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self, *, timeout: float = 60.0) -> str:
        """Start the service and return the loopback base URL."""
        if self._thread is not None:
            raise RuntimeError("EmbeddedErpService already started.")
        if not self.db_path.parent.is_dir():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # The security log belongs to this client instance, so it is wired
        # through the environment the app factory reads - then restored,
        # because the process environment is global state and the desktop
        # process may host other components that must not inherit it.
        previous = os.environ.get("ERP_SECURITY_LOG_PATH")
        os.environ["ERP_SECURITY_LOG_PATH"] = str(self.security_log_path)
        try:
            from ..serve import build_app

            app = build_app(db_path=self.db_path, vault_path=self.vault_path)
        finally:
            if previous is None:
                os.environ.pop("ERP_SECURITY_LOG_PATH", None)
            else:
                os.environ["ERP_SECURITY_LOG_PATH"] = previous

        config = uvicorn.Config(
            app,
            host=self.host,
            port=0,  # ephemeral: no fixed port to collide with or probe
            log_level="warning",
            lifespan="on",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._server = server
        self._thread = threading.Thread(
            target=server.run, name="erp-embedded-service", daemon=True
        )
        self._thread.start()

        deadline = time.monotonic() + timeout
        while not server.started:
            if time.monotonic() > deadline:
                self.stop()
                raise TimeoutError("Embedded ERP service did not start in time.")
            if not self._thread.is_alive():
                raise RuntimeError("Embedded ERP service thread died during start-up.")
            time.sleep(0.05)

        self._port = self._bound_port(server)
        self._wait_ready(timeout=deadline - time.monotonic())
        return self.base_url

    @staticmethod
    def _bound_port(server: uvicorn.Server) -> int:
        servers = getattr(server, "servers", None)
        if not servers:
            raise RuntimeError("Embedded ERP service did not report a bound socket.")
        socket = servers[0].sockets[0]
        return int(socket.getsockname()[1])

    def _wait_ready(self, *, timeout: float) -> None:
        """Poll ``/healthz`` until the app answers, not just the socket."""
        assert self._port is not None
        deadline = time.monotonic() + max(timeout, 0.0)
        url = f"http://{self.host}:{self._port}/healthz"
        last_error: Optional[str] = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url, timeout=2.0)
                if response.status_code == 200:
                    return
                last_error = f"healthz answered {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(0.05)
        raise TimeoutError(
            f"Embedded ERP service never became healthy ({last_error or 'timeout'})."
        )

    def stop(self, *, timeout: float = 10.0) -> None:
        """Ask the server to exit its serve loop and join the thread."""
        server = self._server
        if server is not None:
            server.should_exit = True
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self._server = None
        self._port = None

    # ------------------------------------------------------------------
    # accessors
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Embedded ERP service is not running.")
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
