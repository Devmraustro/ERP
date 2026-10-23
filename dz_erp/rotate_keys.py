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

Exit codes: 0 success, 1 the operation failed, 2 invalid configuration or arguments,
3 unhealthy (monitor): a key is overdue, a rotation is unfinished, the ledger or a
retained checkpoint does not verify, custody has drifted, or an external provider was
required and is not configured.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .key_provider import FileKeyProvider
from .key_rotation import (
    KeyConfigurationError,
    KeyRingError,
    check_key_file_custody,
    describe_key_protection,
)
from .rotation_audit import (
    RotationLedger,
    RotationLedgerCheckpointError,
    RotationLedgerError,
    RotationLedgerTruncatedError,
    load_checkpoint,
)
from .database import (
    AUDIT_KEY_ENV,
    AUDIT_KEYRING_SUFFIX,
    AUDIT_KEY_SUFFIX,
    AuditKeyUnavailableError,
    AuditTamperError,
    ERPDatabase,
    FinancialIntegrityError,
)


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


class VaultNotConfiguredError(RuntimeError):
    """The deployment described by ``--vault`` does not exist.

    Raised instead of letting `PrivateDocumentVault` initialise it. Monitoring is a
    read-only operation, and constructing a vault mints a key on first use - so a
    mistyped path used to create an empty vault, a fresh key and a directory tree,
    and then report a healthy-looking v1 deployment. The one thing a monitoring
    run must never do is change the state it is measuring.
    """


class DatabaseNotConfiguredError(RuntimeError):
    """A read-only command was given a missing or uninitialised database path."""

    def __init__(self, message: str, *, reason: str = "DB_NOT_CONFIGURED") -> None:
        super().__init__(message)
        self.reason = reason


def _vault(path: str):
    from .intake_ocr import PrivateDocumentVault

    return PrivateDocumentVault(Path(path))


def _require_existing_vault(path: str):
    """Open an existing vault for read-only work, or refuse.

    A vault is considered to exist when any of its key sources does: the version
    registry, the single key file, or an explicitly supplied ``ERP_VAULT_KEY``.
    The directory alone is not enough - an empty ``vault/`` with no key is exactly
    where `PrivateDocumentVault` would silently generate one.
    """
    from .intake_ocr import VAULT_KEY_ENV, VAULT_KEYRING_SUFFIX, VAULT_KEY_SUFFIX

    root = Path(path)

    # A key source by itself does not prove the vault exists at this path. In
    # particular, deployments commonly set ERP_VAULT_KEY in the environment;
    # before this guard, a typo pointing to a missing directory passed the
    # key-source check and PrivateDocumentVault.mkdir() created a new empty
    # vault that monitoring then reported as healthy.
    if not root.is_dir():
        raise VaultNotConfiguredError(
            f"No vault is configured at {root}: the vault directory does not exist "
            "or is not a directory. Key material alone does not prove that this path "
            "belongs to a configured vault. Refusing to create or initialise a vault "
            "from a monitoring command; check --vault and restore the configured path."
        )

    has_env_key = bool(os.environ.get(VAULT_KEY_ENV))
    has_registry = (root.parent / (root.name + VAULT_KEYRING_SUFFIX)).exists()
    has_key_file = (root.parent / (root.name + VAULT_KEY_SUFFIX)).exists()
    if not (has_env_key or has_registry or has_key_file):
        raise VaultNotConfiguredError(
            f"No vault is configured at {root}: none of {root.name}{VAULT_KEY_SUFFIX}, "
            f"{root.name}{VAULT_KEYRING_SUFFIX} or {VAULT_KEY_ENV} exists. Refusing to "
            "create one from a monitoring command - check the --vault path, or initialise "
            "the deployment by serving it once."
        )
    return _vault(path)


def _require_existing_database(path: str) -> None:
    """Prove a file-backed ERP database is already initialized without writing to it.

    ERPDatabase's normal constructor creates directories, initializes schema and may
    create audit key material. Status/monitor commands must inspect an existing ERP
    store instead of turning a typo, empty file, or unrelated SQLite file into one.
    """
    candidate = Path(path)
    if path == ":memory:":
        raise DatabaseNotConfiguredError(
            "Read-only status/monitoring requires an existing file-backed ERP database; "
            "refusing to initialize an in-memory database."
        )
    if not candidate.is_file():
        raise DatabaseNotConfiguredError(
            f"No existing database file at {candidate}. Refusing to initialize a database "
            "or create audit key material from a read-only status/monitor command."
        )

    required_tables = {"companies", "users", "audit_events"}
    try:
        connection = sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise DatabaseNotConfiguredError(
            f"Cannot safely inspect database {candidate}: {exc}. Refusing to initialize or "
            "mutate this file from a read-only command.",
            reason="DB_UNREADABLE",
        ) from exc

    missing = sorted(required_tables - tables)
    if missing:
        raise DatabaseNotConfiguredError(
            f"Database file {candidate} is not an initialized ERP database; missing required "
            f"tables: {', '.join(missing)}. Refusing to create schema or audit key material "
            "from a read-only command."
        )

    # Read-only inspection deliberately does not run the application's schema
    # migrations. Verify the columns that the audit verifier must read and
    # refuse an older/incomplete schema with a diagnostic instead of silently
    # upgrading the file during a status or monitor request.
    required_columns = {
        "companies": {"id"},
        "users": {"id", "company_id"},
        "audit_events": {
            "id",
            "company_id",
            "occurred_at",
            "actor_user_id",
            "action_type",
            "target_model",
            "target_id",
            "before_json",
            "after_json",
            "hmac_key_version",
            "prev_hash",
            "event_hash",
        },
    }
    missing_columns = {}
    schema_connection = None
    try:
        schema_connection = sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True)
        for table, expected in required_columns.items():
            actual = {
                str(row[1])
                for row in schema_connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            absent = sorted(expected - actual)
            if absent:
                missing_columns[table] = absent
    except sqlite3.Error as exc:
        raise DatabaseNotConfiguredError(
            f"Cannot safely inspect schema in {candidate}: {exc}. Refusing to migrate it "
            "from a read-only command.",
            reason="DB_SCHEMA_INCOMPATIBLE",
        ) from exc
    finally:
        if schema_connection is not None:
            schema_connection.close()
    if missing_columns:
        detail = "; ".join(
            f"{table}: {', '.join(columns)}" for table, columns in sorted(missing_columns.items())
        )
        raise DatabaseNotConfiguredError(
            f"Database schema at {candidate} is not ready for read-only verification "
            f"({detail}). Run the reviewed database migration procedure separately; "
            "status/monitor will not alter the schema.",
            reason="DB_SCHEMA_INCOMPATIBLE",
        )

    # The regular ERPDatabase constructor can legitimately create the initial
    # audit key for a fresh store. That is unacceptable on a read-only path:
    # even a schema-valid, empty database with a missing key must not be silently
    # initialized by status/monitoring.
    legacy_key = candidate.with_name(candidate.name + AUDIT_KEY_SUFFIX)
    key_registry = candidate.with_name(candidate.name + AUDIT_KEYRING_SUFFIX)
    if not (
        os.environ.get(AUDIT_KEY_ENV)
        or legacy_key.is_file()
        or key_registry.is_file()
    ):
        raise DatabaseNotConfiguredError(
            f"The audit key source is missing for existing database {candidate}: expected "
            f"{legacy_key.name}, {key_registry.name}, or {AUDIT_KEY_ENV}. Refusing to generate "
            "replacement key material from a read-only status/monitor command.",
            reason="AUDIT_KEY_UNAVAILABLE",
        )

DEFAULT_MAX_KEY_AGE_DAYS = 365


def _refuse_local_checkpoint(destination: Path, *, ledger_path: Path, vault_root: Path) -> None:
    """Refuse to write a checkpoint inside the directory that holds the ledger.

    A checkpoint kept next to the ledger it protects fails at exactly the moment
    it is needed: both are lost or both are stolen together.
    """
    resolved = destination.resolve()
    for directory, what in ((ledger_path.resolve().parent, "the rotation ledger"), (Path(vault_root).resolve(), "the document vault")):
        try:
            resolved.relative_to(directory)
        except ValueError:
            continue
        raise RotationLedgerCheckpointError(
            f"Refusing to write the checkpoint to {destination}: it is inside {directory}, which holds "
            f"{what}. A checkpoint must be retained off-host (a different machine or object store) or it "
            "cannot detect the loss of the records it protects."
        )


def _verify_ledger_without_creating_keys(ledger_path: Path) -> Dict[str, Any]:
    """Verify a rotation ledger without ever generating a replacement key.

    Monitoring and checkpoint verification are read-only operations. Opening the
    ledger with ``create=True`` (the default) used to generate a *new* keyring
    when the original key was missing, which:

    * wrote key material during a read-only report;
    * made the ledger fail with ``RECORD_TAMPERED`` - a false accusation of
      deliberate modification - when the real condition was a lost key;
    * replaced the evidence that the keyring had gone missing at all.

    Key loss and tampering need different responses (restore from backup versus
    incident handling), so they must never be conflated.
    """
    try:
        return RotationLedger.open(ledger_path, create=False).verify()
    except KeyConfigurationError as exc:
        return {
            "valid": False,
            "reason": "LEDGER_KEY_UNAVAILABLE",
            "key_unavailable": True,
            "detail": str(exc),
        }


def _checkpoint_age_days(checkpoint: Dict[str, Any]) -> Optional[float]:
    """Age of a checkpoint document in days, or None when it records no timestamp."""
    exported = checkpoint.get("exported_at")
    if not exported:
        return None
    try:
        moment = datetime.fromisoformat(str(exported))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds() / 86400.0


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
        try:
            vault = _require_existing_vault(vault_path)
        except VaultNotConfiguredError as exc:
            # Reported, not raised: an operator asking "is this healthy?" gets a
            # structured answer with a reason, and the process still exits 3.
            healthy = False
            report["blocking_notes"].append(str(exc))
            report["vault"] = {"root": str(vault_path), "configured": False, "reason": "VAULT_NOT_CONFIGURED"}
            report["healthy"] = False
            return report
        ring = vault.keyring
        overdue = [
            {"version": v, "age_days": round(age, 3)}
            for v in ring.versions
            if (age := ring.age_days(v)) is not None and age > max_key_age_days
        ]
        verification = vault.verify_blobs()
        ledger_path = Path(vault.vault_root).parent / (Path(vault.vault_root).name + ".rotation-ledger.jsonl")
        ledger_state = _verify_ledger_without_creating_keys(ledger_path)
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
            # Stated explicitly so a reader can tell "configured and healthy" from
            # "configured and broken"; the not-configured branch reports False.
            "configured": True,
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
            if ledger_state.get("key_unavailable"):
                report["blocking_notes"].append(
                    "The rotation ledger key is unavailable, so the ledger cannot be verified. "
                    "Restore the key from backup; do not treat this as evidence of tampering "
                    "until the ledger can be read."
                )
            else:
                report["blocking_notes"].append(
                    f"The rotation ledger does not verify ({ledger_state.get('reason')}); treat as tampering."
                )
        report["ledger"] = ledger_state

    if db_path:
        try:
            _require_existing_database(db_path)
        except DatabaseNotConfiguredError as exc:
            healthy = False
            report["blocking_notes"].append(str(exc))
            report["audit"] = {
                "db_path": str(db_path),
                "configured": False,
                "reason": exc.reason,
            }
        else:
            db = ERPDatabase(db_path, read_only=True)
            ring = db.audit_keyring
            overdue = [
                {"version": v, "age_days": round(age, 3)}
                for v in ring.versions
                if (age := ring.age_days(v)) is not None and age > max_key_age_days
            ]
            chains = db.verify_all_audit_chains()
            ledger_path = Path(db.db_path).with_name(Path(db.db_path).name + ".rotation-ledger.jsonl")
            ledger_state = (
                _verify_ledger_without_creating_keys(ledger_path)
                if ledger_path.exists()
                else {"valid": True, "open_operations": []}
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
                if ledger_state.get("key_unavailable"):
                    report["blocking_notes"].append(
                        "The audit rotation ledger key is unavailable, so the ledger cannot be verified. "
                        "Restore the key from backup before drawing conclusions from the ledger."
                    )
                else:
                    report["blocking_notes"].append(
                        f"The audit rotation ledger does not verify ({ledger_state.get('reason')})."
                    )

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
        vault = _require_existing_vault(vault_path)
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
        _require_existing_database(db_path)
        db = ERPDatabase(db_path, read_only=True)
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
    monitor.add_argument(
        "--checkpoint-verify",
        help="Compare the ledger against a previously exported checkpoint (detects tail truncation).",
    )
    monitor.add_argument(
        "--max-checkpoint-age-days",
        type=int,
        default=None,
        help="Treat a checkpoint older than this as stale (unhealthy).",
    )

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
            if (args.checkpoint_export or args.checkpoint_verify) and not args.vault:
                parser.error("--checkpoint-export/--checkpoint-verify require --vault (the ledger is per store)")

            if args.checkpoint_export or args.checkpoint_verify:
                vault = _require_existing_vault(args.vault)
                # Validate the checkpoint document before touching the ledger: a
                # malformed or incompatible checkpoint is the actual fault, and
                # saying so is more useful than an unrelated ledger error.
                checkpoint = load_checkpoint(Path(args.checkpoint_verify)) if args.checkpoint_verify else None
                ledger = RotationLedger.open(
                    Path(vault.vault_root).parent / (Path(vault.vault_root).name + ".rotation-ledger.jsonl"),
                    create=False,
                )
                if args.checkpoint_export:
                    destination = Path(args.checkpoint_export)
                    # "Off-host" is the whole point: a checkpoint stored beside the
                    # ledger is destroyed by the same disk loss, fire, or ransomware
                    # event, and buys no truncation detection at all.
                    _refuse_local_checkpoint(destination, ledger_path=ledger.path, vault_root=vault.vault_root)
                    report["checkpoint"] = ledger.export_checkpoint(destination)
                if args.checkpoint_verify:
                    # A malformed, missing, incompatible or stale checkpoint makes the
                    # deployment unhealthy: without a trustworthy checkpoint, removal of
                    # trailing ledger records is undetectable. "Could not check" must
                    # never be reported as "checked and fine".
                    try:
                        verification = ledger.verify_against_checkpoint(checkpoint)
                        report["checkpoint_verification"] = verification
                        if not verification.get("valid"):
                            report["healthy"] = False
                            report["blocking_notes"].append(
                                "The rotation ledger does not match the retained checkpoint "
                                f"({verification.get('reason')}). Treat this as tampering."
                            )
                        age = _checkpoint_age_days(checkpoint)
                        report["checkpoint_verification"]["age_days"] = age
                        if (
                            args.max_checkpoint_age_days is not None
                            and age is not None
                            and age > args.max_checkpoint_age_days
                        ):
                            report["healthy"] = False
                            report["blocking_notes"].append(
                                f"The retained checkpoint is {age:.1f} days old "
                                f"(limit {args.max_checkpoint_age_days}); export a new one."
                            )
                    except RotationLedgerTruncatedError as exc:
                        report["checkpoint_verification"] = {
                            "valid": False,
                            "reason": "CHECKPOINT_TRUNCATED",
                            "expected_record_count": exc.expected_count,
                            "actual_record_count": exc.actual_count,
                            "detail": str(exc),
                        }
                        report["healthy"] = False
                        report["blocking_notes"].append(
                            f"Trailing ledger records are missing: {exc.expected_count} were "
                            f"checkpointed, {exc.actual_count} remain. Treat this as tampering."
                        )
            _emit(report)
            # Exit non-zero when unhealthy so a scheduler or probe notices.
            return 0 if report["healthy"] else 3

        if args.command == "status":
            if not args.db and not args.vault:
                parser.error("status requires --db and/or --vault")
            _emit(_status(args.db, args.vault))
            return 0

        if args.command == "vault-rotate":
            vault = _require_existing_vault(args.vault)
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
            _require_existing_database(args.db)
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
            _emit(_require_existing_vault(args.vault).set_active_key_version(args.version))
            return 0

        if args.command == "audit-rollback":
            _require_existing_database(args.db)
            _emit(ERPDatabase(args.db).set_active_audit_key_version(args.version))
            return 0

        if args.command == "vault-remove":
            _emit(
                _require_existing_vault(args.vault).remove_key(
                    args.version,
                    confirm_retained_copies=args.i_have_verified_retained_copies,
                )
            )
            return 0

        if args.command == "audit-remove":
            _require_existing_database(args.db)
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
    except VaultNotConfiguredError as exc:
        print(f"VAULT: {exc}", file=sys.stderr)
        return 3
    except DatabaseNotConfiguredError as exc:
        print(f"DB: {exc}", file=sys.stderr)
        return 3
    except RotationLedgerCheckpointError as exc:
        # A checkpoint that cannot be trusted is an operational failure, not a
        # reason to print a healthy report.
        print(f"CHECKPOINT: {exc}", file=sys.stderr)
        return 3
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
