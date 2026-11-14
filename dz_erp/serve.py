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
from typing import Any, Optional

from .api import create_erp_fastapi_app
from .auth import AuthManager
from .intake_ocr import PrivateDocumentVault
from .net import allowed_hosts_from_env, validate_allowed_hosts_for_bind


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


def _positive_int_env(env_name: str, default: int) -> int:
    """Read a positive integer setting, failing with an actionable message.

    ``int(os.environ[...])`` on a typo raises a bare ``ValueError`` traceback
    that names neither the variable nor the file the operator edited, and a
    negative value used to reach the auth layer and abort there.  A startup
    setting that cannot be honoured must say which setting it is.
    """
    raw = os.environ.get(env_name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise SystemExit(
            f"{env_name}={raw!r} is not an integer. Set it to a positive whole "
            f"number (default {default})."
        ) from None
    if value <= 0:
        raise SystemExit(
            f"{env_name}={value} must be a positive whole number "
            f"(default {default})."
        )
    return value


def _has_any_user(db_path: Path) -> bool:
    """True when the users table already contains a row."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT COUNT(*) FROM users")
        return int(cur.fetchone()[0]) > 0
    finally:
        conn.close()


def _discard_unbootstrapped_company(db: Any, company_id: int) -> bool:
    """Undo a company created by this run whose provisioning then failed.

    Returns ``True`` when the company was fully removed, ``False`` when it had to
    stay behind (see below).

    ``--bootstrap-admin`` is meant to be atomic, but "remove the company" is not
    always possible once the SCF chart has been loaded:

    * ``accounts``, ``account_groups``, ``taxes`` and ``timbre_rules`` reference
      ``companies`` with ``ON DELETE RESTRICT``, so they are deleted first;
    * ``load_official_odoo_chart`` writes an ``audit_events`` row
      (``LOAD_CHART_TEMPLATE``), and that table carries
      ``trg_audit_no_delete`` - deleting audit history is exactly what the
      product exists to prevent, and it will not be worked around here.

    So when the chart load already happened, the company row is kept and the
    caller reports the exact state. A traceable half-built company is a better
    failure than an untraceable database.
    """
    with db.transaction() as conn:
        for table in ("accounts", "account_groups", "taxes", "timbre_rules"):
            conn.execute(f"DELETE FROM {table} WHERE company_id = ?", (company_id,))
        audit_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE company_id = ?", (company_id,)
        ).fetchone()["n"]
        if audit_rows:
            # The chart load was audited; the audit row cannot be deleted. Leave
            # the (now empty) company in place and let the caller say so.
            return False
        conn.execute(
            "DELETE FROM companies WHERE id = ? AND NOT EXISTS "
            "(SELECT 1 FROM users WHERE company_id = ?)",
            (company_id, company_id),
        )
    return True


def _bootstrap_admin(db_path: Path, username: str, company_name: str) -> int:
    """Create the first company and an admin user, returning the user id.

    Prompts for the credential before writing to disk so an aborted prompt
    leaves no orphaned company row, and compensates if user creation fails.
    """
    from .database import ERPDatabase

    password = getpass.getpass(f"Password for '{username}': ")
    # Validate the credential before touching the database. The policy check is
    # pure - it needs no connection - so a rejected password must not create a
    # company, load a chart, or write an audit row that then cannot be undone.
    from .auth import PasswordPolicyError, validate_password_policy

    try:
        validate_password_policy(password, username=username)
    except PasswordPolicyError as exc:
        # Nothing has been written: no company, no chart, no audit row. Report
        # through SystemExit like every other bootstrap refusal so the CLI
        # contract stays "one exit code, one message".
        raise SystemExit(f"Cannot bootstrap admin '{username}': {exc}") from exc
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
            existing_users = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE company_id = ?", (company_id,)
                ).fetchone()["n"]
            )
        else:
            cur = conn.execute(
                "INSERT INTO companies (code, name, nif, created_at) VALUES (?, ?, ?, ?)",
                (company_code, company_name, None, _utcnow().isoformat()),
            )
            company_id = int(cur.lastrowid)
            created_company = True

    if existing_users:
        raise SystemExit(
            f"Company '{company_code}' already exists with {existing_users} user(s): "
            "this database is already bootstrapped."
        )

    # A company without a chart of accounts cannot record a single invoice:
    # posting needs account codes, and every master-data write validates against
    # the SCF. The desktop bootstrap (dz_erp/desktop/service.py) loads the chart
    # as step 3 of its sequence; this CLI path did not, so a deployment created
    # with --bootstrap-admin started with an empty `accounts` table and the first
    # supplier/product/invoice write failed with "is not an account of this
    # company's SCF chart". Load it here, before any user can log in.
    try:
        from .localization import load_official_odoo_chart

        load_official_odoo_chart(db, company_id, apply_fixes_immediately=True)
    except Exception as exc:  # pragma: no cover - defensive
        note = ""
        if created_company:
            removed = _discard_unbootstrapped_company(db, company_id)
            note = (
                " The partial company was removed."
                if removed
                else f" Company {company_id} was left in place because its chart load is "
                "already recorded in the audit trail."
            )
        raise SystemExit(
            f"Could not load the SCF chart of accounts for company {company_id}: {exc}."
            f"{note} Fix the cause and re-run."
        ) from exc

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
        note = ""
        if created_company:
            removed = _discard_unbootstrapped_company(db, company_id)
            note = (
                " The partial company was removed."
                if removed
                else f" Company {company_id} was left in place with no user: its chart load is "
                "already recorded in the audit trail, which cannot be deleted."
            )
        raise SystemExit(
            f"Could not create admin '{username}' in company {company_id}: {exc}.{note}"
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

    db_path = db_path or _required_path("ERP_DB_PATH")
    vault_path = vault_path or _required_path("ERP_VAULT_PATH")

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
        token_ttl_seconds=_positive_int_env("ERP_TOKEN_TTL_SECONDS", 28800),
        trusted_proxies=None,  # fail-closed default; see dz_erp/net.py
        docs_enabled=docs_enabled,
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
    parser.add_argument("--port", type=int, default=_positive_int_env("ERP_PORT", 8000))
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

    # Operational acceptance (Phase 59A): a listener that is reachable from
    # other machines must have at least one hostname other machines may use.
    # Without this the process starts, reports itself healthy, and answers
    # every LAN request with 400 - see dz_erp.net.validate_allowed_hosts_for_bind.
    validate_allowed_hosts_for_bind(args.host, allowed_hosts_from_env())

    import uvicorn

    # SEC-15: ``proxy_headers=False`` prevents uvicorn from rewriting
    # ``request.client`` from X-Forwarded-For for loopback peers before
    # dz_erp/net.py enforces ERP_TRUSTED_PROXIES.
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
