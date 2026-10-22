# SPDX-License-Identifier: LGPL-3.0-only
"""Operator command line for key rotation, inspection and verification.

    python -m dz_erp.rotate_keys status        --db ERP_DB_PATH --vault ERP_VAULT_PATH
    python -m dz_erp.rotate_keys vault-rotate  --vault ERP_VAULT_PATH [--dry-run]
    python -m dz_erp.rotate_keys audit-rotate  --db ERP_DB_PATH [--dry-run]
    python -m dz_erp.rotate_keys vault-rollback --vault ERP_VAULT_PATH --version 1
    python -m dz_erp.rotate_keys audit-rollback --db ERP_DB_PATH --version 1
    python -m dz_erp.rotate_keys vault-remove  --vault ERP_VAULT_PATH --version 1 \\
        --i-have-verified-retained-copies
    python -m dz_erp.rotate_keys audit-remove  --db ERP_DB_PATH --version 1 \\
        --i-have-verified-retained-copies

Every subcommand prints a JSON object containing version numbers, statuses, counts and paths -
**never key material**. Deleting a version requires the explicit
``--i-have-verified-retained-copies`` acknowledgement, because backups, replicas and exports may
still contain ciphertext that needs it. ``--dry-run`` reports what would happen (including a full
decryptability sweep) and changes nothing.

Exit codes: 0 success, 1 the operation failed, 2 invalid configuration or arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .key_rotation import KeyConfigurationError, KeyRingError
from .database import AuditKeyUnavailableError, AuditTamperError, ERPDatabase, FinancialIntegrityError


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _vault(path: str):
    from .intake_ocr import PrivateDocumentVault

    return PrivateDocumentVault(Path(path))


def _status(db_path: Optional[str], vault_path: Optional[str]) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    if vault_path:
        vault = _vault(vault_path)
        report["vault"] = {
            "root": str(vault.vault_root),
            "keyring_path": str(vault.keyring_path),
            "registry_present": vault.keyring_path.exists(),
            **vault.keyring.summary(),
            "blobs_by_version": vault.scan_blob_versions(),
            "verification": vault.verify_blobs(),
        }
    if db_path:
        db = ERPDatabase(db_path)
        report["audit"] = {
            "db_path": db.db_path,
            "keyring_path": str(db.audit_keyring_path),
            "registry_present": bool(db.audit_keyring_path and db.audit_keyring_path.exists()),
            **db.audit_keyring.summary(),
            "events_by_version": db.audit_events_by_key_version(),
            "chains": db.verify_all_audit_chains(),
        }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dz_erp.rotate_keys", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Report key versions, data distribution and verification.")
    status.add_argument("--db")
    status.add_argument("--vault")

    for name, help_text, needs in (
        ("vault-rotate", "Activate a new vault key and re-encrypt every document.", "vault"),
        ("audit-rotate", "Activate a new audit signing key; history is never rewritten.", "db"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(f"--{needs}", required=True)
        p.add_argument("--dry-run", action="store_true", help="Report what would change; change nothing.")

    for name, help_text, needs in (
        ("vault-rollback", "Make an existing vault key version active again.", "vault"),
        ("audit-rollback", "Make an existing audit key version active again.", "db"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(f"--{needs}", required=True)
        p.add_argument("--version", type=int, required=True)

    for name, help_text, needs in (
        ("vault-remove", "Delete a vault key version that no ciphertext needs.", "vault"),
        ("audit-remove", "Delete an audit key version that no event needs.", "db"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(f"--{needs}", required=True)
        p.add_argument("--version", type=int, required=True)
        p.add_argument(
            "--i-have-verified-retained-copies",
            action="store_true",
            help="Acknowledge that backups/replicas/exports may still require this key.",
        )

    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            if not args.db and not args.vault:
                parser.error("status requires --db and/or --vault")
            _emit(_status(args.db, args.vault))
            return 0

        if args.command == "vault-rotate":
            vault = _vault(args.vault)
            if args.dry_run:
                referenced = vault.scan_blob_versions()
                _emit(
                    {
                        "dry_run": True,
                        "vault_root": str(vault.vault_root),
                        "active_version": vault.keyring.active_version,
                        "blobs_by_version": referenced,
                        "verification": vault.verify_blobs(),
                        "would_activate_version": vault.keyring.active_version + 1,
                        "would_attempt": sum(referenced.values()),
                    }
                )
                return 0
            report = vault.rotate_key()
            _emit(report)
            return 0 if report["verified_ok"] else 1

        if args.command == "audit-rotate":
            db = ERPDatabase(args.db)
            if args.dry_run:
                _emit(
                    {
                        "dry_run": True,
                        "db_path": db.db_path,
                        "active_version": db.active_audit_key_version,
                        "events_by_version": db.audit_events_by_key_version(),
                        "would_activate_version": db.active_audit_key_version + 1,
                        "chain_now": db.verify_all_audit_chains()["valid"],
                    }
                )
                return 0
            report = db.rotate_audit_key()
            _emit(report)
            return 0 if report["chain_valid_after"] else 1

        if args.command == "vault-rollback":
            _emit(_vault(args.vault).set_active_key_version(args.version))
            return 0

        if args.command == "audit-rollback":
            _emit(ERPDatabase(args.db).set_active_audit_key_version(args.version))
            return 0

        if args.command == "vault-remove":
            _emit(
                _vault(args.vault).remove_key(
                    args.version,
                    confirm_retained_copies=args.i_have_verified_retained_copies,
                )
            )
            return 0

        if args.command == "audit-remove":
            _emit(
                ERPDatabase(args.db).remove_audit_key_version(
                    args.version,
                    confirm_retained_copies=args.i_have_verified_retained_copies,
                )
            )
            return 0

        parser.error(f"unknown command {args.command!r}")
        return 2

    except (AuditTamperError, FinancialIntegrityError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except (KeyConfigurationError, KeyRingError, AuditKeyUnavailableError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
