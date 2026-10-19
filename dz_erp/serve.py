"""Deployment entry point for the Algerian AI ERP API.

Running this module starts a real HTTP server against a durable database and
document vault.  Everything the process needs is read from the environment, so
the same code path can be used in staging and production:

    ERP_DB_PATH=/var/lib/erp/erp.sqlite3 \\
    ERP_VAULT_PATH=/var/lib/erp/vault \\
    ERP_HOST=0.0.0.0 ERP_PORT=8000 \\
    python -m dz_erp.serve

The interesting property of this module is that it refuses to run with an
empty database.  Creating companies, users and tokens requires a bootstrapped
database; pointing a deployment at a fresh SQLite file yields an API where every
request returns 401 and no obvious reason why.  ``--bootstrap-admin`` creates
the first company and admin so that the very first login is possible.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .api import create_erp_fastapi_app
from .auth import AuthManager
from .intake_ocr import PrivateDocumentVault


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _required_path(env_name: str, default: Optional[str] = None) -> Path:
    value = os.environ.get(env_name) or default
    if not value:
        raise SystemExit(
            f"{env_name} is not set. Point it at a durable location - the "
            "defaults are intentionally not baked in so a fresh deployment "
            "cannot accidentally run against a throwaway file."
        )
    return Path(value)


def _has_any_user(db_path: Path) -> bool:
    """True when the users table already contains a row."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT COUNT(*) FROM users")
        return int(cur.fetchone()[0]) > 0
    finally:
        conn.close()


def _bootstrap_admin(db_path: Path, username: str, company_name: str) -> int:
    """Create the first company and an admin user, returning the user id."""
    from .database import ERPDatabase

    db = ERPDatabase(str(db_path))
    conn = db._create_connection()
    try:
        cur = conn.execute(
            "INSERT INTO companies (code, name, nif, created_at) VALUES (?, ?, ?, ?)",
            (f"BOOT-{username.upper()}", company_name, None, _utcnow().isoformat()),
        )
        company_id = int(cur.lastrowid)
    finally:
        conn.close()

    auth = AuthManager(db)
    user = auth.create_user(
        username=username,
        password=getpass.getpass(f"Password for '{username}': "),
        role="admin",
        company_id=company_id,
        actor_user_id=None,
    )
    return int(user["user_id"])


def build_app(
    db_path: Optional[Path] = None,
    vault_path: Optional[Path] = None,
    *,
    auth_manager: Optional[AuthManager] = None,
):
    """Construct the ASGI application from environment-configured paths."""
    from .database import ERPDatabase

    db_path = db_path or _required_path("ERP_DB_PATH")
    vault_path = vault_path or _required_path("ERP_VAULT_PATH")

    db = ERPDatabase(str(db_path))
    # No encryption key is passed: the vault persists one beside itself on
    # first use (see dz_erp.intake_ocr._resolve_vault_key).  Passing a fresh
    # key here would orphan every document on the next restart.
    vault = PrivateDocumentVault(vault_path)

    return create_erp_fastapi_app(
        db,
        vault,
        auth_manager=auth_manager,
        token_ttl_seconds=int(os.environ.get("ERP_TOKEN_TTL_SECONDS", "28800")),
        trusted_proxies=None,  # fail-closed default; see dz_erp/net.py
    )


# ``uvicorn dz_erp.serve:app`` resolves this attribute at start-up, so the app
# is built on first access rather than at import time.  That keeps
# ``import dz_erp.serve`` usable from tests and tooling, while a real server
# still fails loudly - and only then - when the environment is unconfigured.
def __getattr__(name: str):
    if name == "app":
        return build_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ERP API server.")
    parser.add_argument(
        "--bootstrap-admin",
        metavar="USERNAME",
        help="Create the first company and admin user, then exit.",
    )
    parser.add_argument(
        "--company-name",
        default="Primary Company",
        help="Company name used with --bootstrap-admin.",
    )
    parser.add_argument("--host", default=os.environ.get("ERP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ERP_PORT", "8000")))
    args = parser.parse_args(argv)

    db_path = _required_path("ERP_DB_PATH")

    # Open the database through ERPDatabase before touching it directly.  A
    # brand-new deployment points at a file that does not exist yet, and
    # querying it with a bare sqlite3 connection raises "no such table: users"
    # as an unhandled traceback - which is the opposite of the clean,
    # actionable failure this entry point is supposed to produce.  ERPDatabase
    # also applies the schema migrations, so the file is usable afterwards.
    from .database import ERPDatabase

    ERPDatabase(str(db_path))

    if args.bootstrap_admin:
        user_id = _bootstrap_admin(db_path, args.bootstrap_admin, args.company_name)
        print(f"Created admin '{args.bootstrap_admin}' (id={user_id}) in {db_path}")
        return 0

    if not _has_any_user(db_path):
        print(
            f"No users exist in {db_path}. Create one first:\n"
            f"  ERP_DB_PATH={db_path} python -m dz_erp.serve "
            f"--bootstrap-admin <username> --company-name '<company>'",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    uvicorn.run(
        build_app(), host=args.host, port=args.port, log_level=os.environ.get("ERP_LOG_LEVEL", "info")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
