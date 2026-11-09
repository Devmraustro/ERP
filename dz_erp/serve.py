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
    """Create the first company and an admin user, returning the user id.

    **Nothing is written before the credentials are in hand.** This used to
    insert and commit the company first and then prompt for the password, so an
    operator who mistyped, hit Ctrl-C or ran the documented
    ``docker compose run --rm bootstrap`` without a TTY was left with a company
    row and no user — and every retry then died with a raw
    ``sqlite3.IntegrityError: UNIQUE constraint failed: companies.code``
    instead of an actionable message. Prompting first makes an aborted prompt a
    no-op on the database.

    The remaining failure window (the user insert after the company insert) is
    closed by a compensating delete: ``AuthManager.create_user`` opens its own
    transaction, so the two writes cannot share one (a nested transaction raises
    ``database is locked`` by design).

    An orphaned bootstrap company — the exact state the previous ordering could
    leave, and the state an existing deployment may already be in — is **reused**
    rather than treated as an error, so recovery needs no manual SQL and no
    deletion of company rows. A company that already has users means the
    deployment is bootstrapped, which is a refusal, not a crash.
    """
    from .database import ERPDatabase

    # Prompt before anything touches the disk (see the docstring).
    password = getpass.getpass(f"Password for '{username}': ")

    db = ERPDatabase(str(db_path))
    company_code = f"BOOT-{username.upper()}"

    created_company = False
    existing_users = 0
    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM companies WHERE code = ?", (company_code,)
        ).fetchone()
        if existing:
            company_id = int(existing["id"])
            existing_users = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE company_id = ?", (company_id,)
            ).fetchone()["n"]
        else:
            cur = conn.execute(
                "INSERT INTO companies (code, name, nif, created_at) VALUES (?, ?, ?, ?)",
                (company_code, company_name, None, _utcnow().isoformat()),
            )
            company_id = int(cur.lastrowid)
            created_company = True

    # Outside the transaction on purpose: the context manager rolls back on
    # Exception and closes the connection in `finally`, but a SystemExit is a
    # BaseException that would leave the rollback to close-time semantics. The
    # refusal belongs after the block, where the decision cannot interact with
    # an open transaction at all.
    if existing_users:
        raise SystemExit(
            f"Company '{company_code}' already exists with {existing_users} user(s): "
            "this database is already bootstrapped. Use the existing admin "
            "account, or pass a different --bootstrap-admin username."
        )

    auth = AuthManager(db)
    try:
        user = auth.create_user(
            username=username,
            password=password,
            role="admin",
            company_id=company_id,
            actor_user_id=None,
        )
    except Exception as exc:
        if created_company:
            # Compensate: leave no half-bootstrapped company behind.
            with db.transaction() as conn:
                conn.execute(
                    "DELETE FROM companies WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM users WHERE company_id = ?)",
                    (company_id, company_id),
                )
        raise SystemExit(
            f"Could not create admin '{username}' in company {company_id}: {exc}. "
            + (
                "The company row created by this attempt was removed; fix the "
                "cause and re-run the same command."
                if created_company
                else "The pre-existing company was left untouched; re-run with a "
                "different --bootstrap-admin username or fix the cause."
            )
        ) from exc
    return int(user["user_id"])


def build_app(
    db_path: Optional[Path] = None,
    vault_path: Optional[Path] = None,
    *,
    auth_manager: Optional[AuthManager] = None,
):
    """Construct the ASGI application from environment-configured paths."""
    from .database import ERPDatabase
    from .key_provider import configured_provider

    # Fail closed on a key-provider declaration this build cannot honour
    # (docs/KEY_PROVIDER_CONTRACT.md §7): ERP_KEY_PROVIDER=kms|hsm must stop
    # start-up here, never serve file-backed material under that label.
    configured_provider()

    if db_path is None and vault_path is None:
        # Check if env vars are set first (fail-closed)
        db_env = os.environ.get("ERP_DB_PATH")
        vault_env = os.environ.get("ERP_VAULT_PATH")
        if not db_env or not vault_env:
            db_path = _required_path("ERP_DB_PATH")
            vault_path = _required_path("ERP_VAULT_PATH")
        else:
            try:
                from .generation_resolver import resolve_active_paths
                resolved_db, resolved_vault = resolve_active_paths()
                db_path = resolved_db
                vault_path = resolved_vault
            except (ValueError, OSError):
                db_path = _required_path("ERP_DB_PATH")
                vault_path = _required_path("ERP_VAULT_PATH")
    else:
        db_path = db_path or _required_path("ERP_DB_PATH")
        vault_path = vault_path or _required_path("ERP_VAULT_PATH")

    # Startup recovery gate: detect and recover from incomplete restores
    # before any DB or Vault access.
    try:
        from .generation_resolver import startup_recovery_gate
        data_dir = Path(db_path).resolve().parent
        if not startup_recovery_gate(data_dir):
            raise RuntimeError("Startup recovery failed: cannot establish a safe active generation.")
    except RuntimeError:
        raise
    except Exception:
        pass

    db = ERPDatabase(str(db_path))
    # No encryption key is passed: the vault persists one beside itself on
    # first use (see dz_erp.intake_ocr._resolve_vault_key).  Passing a fresh
    # key here would orphan every document on the next restart.
    vault = PrivateDocumentVault(vault_path)

    # API documentation is disabled by default for security (staging/production).
    # Set ERP_DOCS_ENABLED=true for local development only.
    docs_enabled = os.environ.get("ERP_DOCS_ENABLED", "false").lower() in ("1", "true", "yes", "on")

    return create_erp_fastapi_app(
        db,
        vault,
        auth_manager=auth_manager,
        token_ttl_seconds=int(os.environ.get("ERP_TOKEN_TTL_SECONDS", "28800")),
        trusted_proxies=None,  # fail-closed default; see dz_erp/net.py
        docs_enabled=docs_enabled,
    )


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

    # SEC-15: ``proxy_headers=False`` is deliberate. Uvicorn otherwise rewrites
    # ``request.client`` from X-Forwarded-For for any peer listed in
    # FORWARDED_ALLOW_IPS (loopback by default), which would bypass the
    # application's own fail-closed policy in dz_erp/net.py and let a caller
    # choose the address that lands in the audit trail. One trust layer, one
    # source of truth: ERP_TRUSTED_PROXIES, read by the app, never by uvicorn.
    uvicorn.run(
        build_app(),
        host=args.host,
        port=args.port,
        log_level=os.environ.get("ERP_LOG_LEVEL", "info"),
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
