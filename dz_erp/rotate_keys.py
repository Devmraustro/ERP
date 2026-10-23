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

from .key_provider import FileKeyProvider
from .key_rotation import (
    KeyConfigurationError,
    KeyRingError,
    check_key_file_custody,
    describe_key_protection,
)
from .rotation_audit import RotationLedger, RotationLedgerError
from .database import AuditKeyUnavailableError, AuditTamperError, ERPDatabase, FinancialIntegrityError


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _vault(path: str):
    from .intake_ocr import PrivateDocumentVault

    return PrivateDocumentVault(Path(path))


DEFAULT_MAX_KEY_AGE_DAYS = 365


def build_monitoring_report(
    *,
    db_path: Optional[str] = None,
    vault_path: Optional[str] = None,
    max_key_age_days: int = DEFAULT_MAX_KEY_AGE_DAYS,
    require_external_provider: bool = False,
) -> Dict[str, Any]:
    """Machine-readable health report for alerting on key management.

    Design intent: an operator must be able to answer "is anything overdue, is
    anything unfinished, is the audit trail of rotations intact, are the key files
    actually protected?" from a single JSON document - and a monitor must be able
    to alert on ``healthy`` without parsing prose.

    The report contains version numbers, ages, counts and custody verdicts. It
    never contains key material, plaintext, or customer identifiers.
    """
    report: Dict[str, Any] = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "max_key_age_days": max_key_age_days,
        "custody": describe_key_protection(),
        "key_provider": {
            # This build reads key material from files only. There is no KMS/HSM
            # integration, and pretending otherwise would be the exact kind of
            # unsupported claim this report exists to avoid.
            "mode": "file",
            "external_provider_configured": False,
            "satisfies_requirement": not require_external_provider,
            "sources": [],
        },
        "blocking_notes": [],
    }
    healthy = True

    if require_external_provider:
        healthy = False
        report["blocking_notes"].append(
            "An external key provider (KMS/HSM) was required, but this deployment uses file-based "
            "keys. Implement the provider integration before relying on this control."
        )

    if vault_path:
        vault = _vault(vault_path)
        ring = vault.keyring
        overdue = [
            {"version": v, "age_days": round(age, 3)}
            for v in ring.versions
            if (age := ring.age_days(v)) is not None and age > max_key_age_days
        ]
        verification = vault.verify_blobs()
        ledger = RotationLedger.open(
            Path(vault.vault_root).parent / (Path(vault.vault_root).name + ".rotation-ledger.jsonl")
        )
        ledger_state = ledger.verify()
        custody_checks = [
            check_key_file_custody(p)
            for p in (vault.vault_key_path, vault.keyring_path)
            if p.exists()
        ]
        provider_sources = [
            FileKeyProvider(p).describe()
            for p in (vault.vault_key_path, vault.keyring_path)
            if p.exists()
        ]
        report["key_provider"]["sources"].extend(provider_sources)
        report["vault"] = {
            "root": str(vault.vault_root),
            "active_version": ring.active_version,
            "versions": [{"version": v, "status": ring.entries[v].status} for v in ring.versions],
            "blobs_by_version": vault.scan_blob_versions(),
            "verification": verification,
            "keys_overdue": overdue,
            "unfinished_operations": ledger_state.get("open_operations", []),
            "key_files": custody_checks,
        }
        if verification["failed"]:
            healthy = False
            report["blocking_notes"].append(
                f"{verification['failed']} vault blob(s) do not decrypt; documents may be lost or corrupt."
            )
        if overdue:
            healthy = False
            report["blocking_notes"].append(
                f"{len(overdue)} vault key version(s) exceed the {max_key_age_days}-day rotation age."
            )
        if ledger_state.get("open_operations"):
            healthy = False
            report["blocking_notes"].append(
                "A rotation started and never finished; re-run it to complete or record the interruption."
            )
        if any(check.get("ok") is False for check in custody_checks):
            healthy = False
            report["blocking_notes"].append(
                "A key file is readable beyond its owner; restrict its permissions (chmod 600)."
            )
        if not ledger_state.get("valid"):
            healthy = False
            report["blocking_notes"].append(
                f"The rotation ledger does not verify ({ledger_state.get('reason')}); treat as tampering."
            )
        report["ledger"] = ledger_state

    if db_path:
        db = ERPDatabase(db_path)
        ring = db.audit_keyring
        overdue = [
            {"version": v, "age_days": round(age, 3)}
            for v in ring.versions
            if (age := ring.age_days(v)) is not None and age > max_key_age_days
        ]
        chains = db.verify_all_audit_chains()
        ledger_path = Path(db.db_path).with_name(Path(db.db_path).name + ".rotation-ledger.jsonl")
        ledger_state = (
            RotationLedger.open(ledger_path).verify() if ledger_path.exists() else {"valid": True, "open_operations": []}
        )
        audit_key_path = Path(str(db.db_path) + ".audit.keys.json")
        legacy_audit_key = Path(str(db.db_path) + ".audit.key")
        report["key_provider"]["sources"].extend(
            FileKeyProvider(p).describe()
            for p in (legacy_audit_key, audit_key_path)
            if p.exists()
        )
        report["audit"] = {
            "db_path": db.db_path,
            "active_version": ring.active_version,
            "versions": [{"version": v, "status": ring.entries[v].status} for v in ring.versions],
            "events_by_version": db.audit_events_by_key_version(),
            "chain_valid": chains["valid"],
            "chains": chains,
            "keys_overdue": overdue,
            "unfinished_operations": ledger_state.get("open_operations", []),
        }
        if not chains["valid"]:
            healthy = False
            report["blocking_notes"].append(
                "An audit chain does not verify "
                f"({sorted({r.get('reason') for r in chains['companies'].values() if not r['valid']})}); "
                "investigate before rotating."
            )
        if overdue:
            healthy = False
            report["blocking_notes"].append(
                f"{len(overdue)} audit key version(s) exceed the {max_key_age_days}-day rotation age."
            )
        if not ledger_state.get("valid", True):
            healthy = False
            report["blocking_notes"].append(f"The audit rotation ledger does not verify ({ledger_state.get('reason')}).")

    report["healthy"] = healthy
    return report


def _status(db_path: Optional[str], vault_path: Optional[str]) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        # Where the key material actually comes from, and what can be said about
        # it. Same data as `monitor`, minus the health verdict.
        "key_provider": {
            "mode": "file",
            "external_provider_configured": False,
            "sources": [],
        },
    }
    if vault_path:
        vault = _vault(vault_path)
        provider_sources = [
            FileKeyProvider(p).describe()
            for p in (vault.vault_key_path, vault.keyring_path)
            if p.exists()
        ]
        report["key_provider"]["sources"].extend(provider_sources)
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
        audit_key_path = Path(str(db.db_path) + ".audit.keys.json")
        legacy_audit_key = Path(str(db.db_path) + ".audit.key")
        report["key_provider"]["sources"].extend(
            FileKeyProvider(p).describe()
            for p in (legacy_audit_key, audit_key_path)
            if p.exists()
        )
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

    monitor = sub.add_parser("monitor", help="Machine-readable key-management health report (for alerting).")
    monitor.add_argument("--db")
    monitor.add_argument("--vault")
    monitor.add_argument("--max-key-age-days", type=int, default=DEFAULT_MAX_KEY_AGE_DAYS)
    monitor.add_argument(
        "--require-external-provider",
        action="store_true",
        help="Treat file-based keys as unhealthy (use where an external KMS/HSM is required).",
    )
    monitor.add_argument("--checkpoint-export", help="Write a head-hash checkpoint to this path.")

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
        if args.command == "monitor":
            if not args.db and not args.vault:
                parser.error("monitor requires --db and/or --vault")
            report = build_monitoring_report(
                db_path=args.db,
                vault_path=args.vault,
                max_key_age_days=args.max_key_age_days,
                require_external_provider=args.require_external_provider,
            )
            if args.checkpoint_export and args.vault:
                vault = _vault(args.vault)
                ledger = RotationLedger.open(
                    Path(vault.vault_root).parent / (Path(vault.vault_root).name + ".rotation-ledger.jsonl")
                )
                report["checkpoint"] = ledger.export_checkpoint(Path(args.checkpoint_export))
            _emit(report)
            # Exit non-zero when unhealthy so a scheduler or probe notices.
            return 0 if report["healthy"] else 3

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
    except (RotationLedgerError,) as exc:
        print(f"LEDGER: {exc}", file=sys.stderr)
        return 1
    except (KeyConfigurationError, KeyRingError, AuditKeyUnavailableError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
