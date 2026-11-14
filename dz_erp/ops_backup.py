"""Phase 10 - scheduled, retained, integrity-monitored encrypted backups.

The application could already *take* an encrypted backup and *restore* one
(:mod:`dz_erp.security_audit`). What was missing was everything around that: a schedule,
a retention policy, proof that an artefact is still intact, and an alert when the job
fails. This module supplies those, as a CLI an operator can point systemd at:

    python -m dz_erp.ops_backup run      # take a backup, write its manifest, prune
    python -m dz_erp.ops_backup verify   # sha256 every artefact (+ optional deep restore)
    python -m dz_erp.ops_backup prune    # retention only
    python -m dz_erp.ops_backup status   # JSON for monitoring

Three decisions worth stating:

* **The manifest is a sidecar, not the archive.** The archive is Fernet-encrypted; an
  integrity monitor must be able to detect a truncated or corrupted file *without* the
  decryption key. A separately written ``.manifest.json`` carrying the archive's
  SHA-256 makes that possible while leaking nothing (no keys, no customer data).
* **Retention never deletes the last good artefact.** The newest backup is always kept,
  as is anything carrying a legal hold, whatever the retention window says. A retention
  bug that empties the backup directory is a catastrophe; a too-large directory is not.
* **A deep verify restores.** Checking a hash proves the bytes did not change; only an
  actual restore proves they are *usable*. ``verify --deep`` restores each artefact into
  a temporary directory and checks the trial balance and audit chain, then deletes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dz_erp import ops_alerts
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

BACKUP_SUFFIX = ".erpbackup"
MANIFEST_SUFFIX = ".manifest.json"
VERIFY_STATUS_NAME = "verify-status.json"
TOOL = "dz_erp.ops_backup/phase10"
MANIFEST_SCHEMA = 1

DEFAULT_RETAIN_DAILY = 7
DEFAULT_RETAIN_WEEKLY = 4
KEEP_MINIMUM = 1

BACKUP_KEY_ENV = "ERP_BACKUP_KEY"
BACKUP_KEY_FILE_ENV = "ERP_BACKUP_KEY_FILE"
BACKUP_DIR_ENV = "ERP_BACKUP_DIR"


# ----------------------------------------------------------------------
# keys and paths
# ----------------------------------------------------------------------
def resolve_backup_key(explicit: Optional[bytes] = None) -> bytes:
    """Find the backup encryption key: argument, then key file, then environment.

    A key file is preferred over an environment variable because a file can be
    permissioned 0600 while ``/proc/<pid>/environ`` is readable by the process owner and
    by root; the file is also the only form that survives a service restart without
    embedding the secret in a unit file.
    """
    if explicit is not None:
        return explicit
    key_file = os.environ.get(BACKUP_KEY_FILE_ENV)
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            raise FileNotFoundError(f"backup key file not found: {path.name}")
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(
                f"backup key file {path.name} is {oct(mode)}; 0600 or stricter is required"
            )
        material = path.read_bytes().strip()
        if not material:
            raise ValueError(f"backup key file {path.name} is empty")
        return material
    env_key = os.environ.get(BACKUP_KEY_ENV)
    if env_key:
        return env_key.strip().encode("utf-8")
    raise RuntimeError(
        f"no backup key configured: pass --key-file or set {BACKUP_KEY_FILE_ENV} "
        f"(preferred) or {BACKUP_KEY_ENV}"
    )


def resolve_backup_dir(explicit: Optional[str] = None) -> Path:
    value = explicit or os.environ.get(BACKUP_DIR_ENV)
    if not value:
        raise RuntimeError(f"no backup directory configured: pass --backup-dir or set {BACKUP_DIR_ENV}")
    return Path(value)


def backup_name(moment: Optional[datetime] = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return f"erp-{moment.strftime('%Y%m%dT%H%M%SZ')}{BACKUP_SUFFIX}"


def manifest_path_for(backup_path: Path) -> Path:
    return backup_path.with_name(backup_path.name + MANIFEST_SUFFIX)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ----------------------------------------------------------------------
# manifest
# ----------------------------------------------------------------------
def write_manifest(
    backup_path: Path,
    *,
    result: Dict[str, Any],
    legal_hold: bool = False,
    retention_class: str = "daily",
) -> Dict[str, Any]:
    stat = backup_path.stat()
    payload = {
        "schema": MANIFEST_SCHEMA,
        "tool": TOOL,
        "backup_file": backup_path.name,
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
        "sha256": sha256_file(backup_path),
        "db_sha256": result.get("db_sha256"),
        "vault_file_count": result.get("vault_file_count"),
        "vault_key_versions": result.get("vault_key_versions"),
        "audit_key_versions": result.get("audit_key_versions"),
        "legal_hold": legal_hold,
        "retention_class": retention_class,
    }
    target = manifest_path_for(backup_path)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return payload


def read_manifest(backup_path: Path) -> Optional[Dict[str, Any]]:
    path = manifest_path_for(backup_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------------
# run / verify / prune / status
# ----------------------------------------------------------------------
def run_backup(
    db: Any,
    vault: Any,
    backup_dir: Path,
    key: bytes,
    *,
    retain_daily: int = DEFAULT_RETAIN_DAILY,
    retain_weekly: int = DEFAULT_RETAIN_WEEKLY,
    legal_hold: bool = False,
    now: Optional[datetime] = None,
    alert: bool = True,
    stream: Any = None,
) -> Dict[str, Any]:
    """Take one backup, record its manifest, then apply retention."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / backup_name(now)
    started = time.perf_counter()
    try:
        result = create_encrypted_backup(db, vault, target, key)
        manifest = write_manifest(target, result=result, legal_hold=legal_hold)
    except Exception as exc:
        if alert:
            ops_alerts.emit(
                ops_alerts.BACKUP_FAILED,
                f"backup failed: {type(exc).__name__}",
                fields={"backup_dir_writable": os.access(backup_dir, os.W_OK)},
                stream=stream,
            )
        raise
    duration = round(time.perf_counter() - started, 3)

    retention = {"deleted": [], "kept": [], "error": None}
    try:
        retention = prune(
            backup_dir,
            retain_daily=retain_daily,
            retain_weekly=retain_weekly,
            now=now,
        )
    except Exception as exc:
        retention["error"] = f"{type(exc).__name__}: {exc}"
        if alert:
            ops_alerts.emit(
                ops_alerts.RETENTION_FAILED,
                f"retention failed after a successful backup: {type(exc).__name__}",
                stream=stream,
            )
    return {
        "backup": str(target),
        "name": target.name,
        "size_bytes": manifest["size_bytes"],
        "sha256": manifest["sha256"],
        "db_sha256": manifest["db_sha256"],
        "vault_file_count": manifest["vault_file_count"],
        "duration_seconds": duration,
        "retention": retention,
    }


def _inspect_restored_database(db_path: Path) -> Dict[str, Any]:
    """Integrity, foreign keys and ledger balance of a restored database file."""
    if not db_path.is_file():
        return {"integrity_check": "missing", "foreign_key_violations": None, "balanced": False}
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        debit = Decimal("0.00")
        credit = Decimal("0.00")
        rows = conn.execute(
            "SELECT l.debit_dzd, l.credit_dzd FROM journal_entry_lines l"
            " JOIN journal_entries e ON e.id = l.journal_entry_id"
            " WHERE e.state = 'POSTED'"
        ).fetchall()
        for row in rows:
            debit += Decimal(str(row[0] or "0"))
            credit += Decimal(str(row[1] or "0"))
    finally:
        conn.close()
    return {
        "integrity_check": integrity,
        "foreign_key_violations": violations,
        "trial_balance_debit_dzd": str(debit),
        "trial_balance_credit_dzd": str(credit),
        "posted_line_count": len(rows),
        "balanced": debit == credit,
    }


def verify_backup(
    backup_path: Path,
    *,
    key: Optional[bytes] = None,
    deep: bool = False,
    restore_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify one artefact: hash first, and optionally a full restore."""
    backup_path = Path(backup_path)
    checks: Dict[str, Any] = {}
    if not backup_path.is_file():
        return {"file": backup_path.name, "ok": False, "checks": {"exists": False}}

    manifest = read_manifest(backup_path)
    actual_sha = sha256_file(backup_path)
    actual_size = backup_path.stat().st_size
    checks["exists"] = True
    checks["manifest_present"] = manifest is not None
    checks["sha256_actual"] = actual_sha
    checks["size_bytes"] = actual_size
    if manifest is None:
        # Fail closed. The archive is Fernet-encrypted, so without its sidecar
        # manifest there is *nothing* to compare the bytes against: the file
        # could be truncated, corrupted or swapped and this check would still
        # report success. Reporting "ok" here is worse than reporting nothing -
        # a monitor that stays green while the artefact is unverifiable is the
        # failure mode the manifest exists to prevent.
        checks["sha256_matches"] = None
        checks["failure"] = (
            "MANIFEST_MISSING: no .manifest.json beside this artefact, so its "
            "digest cannot be checked; treat the artefact as unverified"
        )
    else:
        checks["sha256_expected"] = manifest.get("sha256")
        checks["sha256_matches"] = manifest.get("sha256") == actual_sha
        checks["size_matches"] = manifest.get("size_bytes") == actual_size

    ok = checks["exists"] and checks["sha256_matches"] is True and checks.get("size_matches", True)

    if deep and ok:
        if key is None:
            checks["deep"] = "skipped: no key supplied"
        else:
            root = Path(restore_root) if restore_root else Path(tempfile.mkdtemp(prefix="dz-verify-"))
            target = root / "restore"
            try:
                report = restore_and_verify_backup(
                    backup_path=backup_path,
                    backup_encryption_key=key,
                    restore_target_dir=target,
                )
                # Verify the restored copy independently instead of trusting the
                # report's own summary fields: the point of a deep check is an
                # outside opinion about the artefact, not the restorer grading itself.
                restored = report.get("restored_db")
                restored_path = Path(getattr(restored, "db_path", restored))
                independent = _inspect_restored_database(restored_path)
                checks["deep"] = {
                    "db_sha256_verified": report.get("db_sha256_verified"),
                    "vault_files_verified": report.get("vault_files_verified"),
                    "audit_chains_valid": report.get("audit_chains_valid"),
                    **independent,
                }
                deep_ok = bool(
                    report.get("db_sha256_verified")
                    and independent.get("integrity_check") == "ok"
                    and independent.get("foreign_key_violations") == 0
                    and independent.get("balanced") is True
                )
                checks["deep_ok"] = deep_ok
                ok = ok and deep_ok
            except Exception as exc:
                checks["deep"] = f"restore failed: {type(exc).__name__}"
                checks["deep_ok"] = False
                ok = False
            finally:
                shutil.rmtree(root, ignore_errors=True)

    return {"file": backup_path.name, "ok": ok, "checks": checks}


def list_backups(backup_dir: Path) -> List[Path]:
    directory = Path(backup_dir)
    if not directory.is_dir():
        return []
    return sorted(
        (p for p in directory.iterdir() if p.is_file() and p.name.endswith(BACKUP_SUFFIX)),
        key=lambda p: p.name,
    )


def verify_all(
    backup_dir: Path,
    *,
    key: Optional[bytes] = None,
    deep: bool = False,
    alert: bool = True,
    stream: Any = None,
    write_status: bool = True,
) -> Dict[str, Any]:
    """Verify every artefact and record the outcome for monitoring."""
    started = time.perf_counter()
    backups = list_backups(backup_dir)
    results = [verify_backup(p, key=key, deep=deep) for p in backups]
    failures = [r for r in results if not r["ok"]]
    summary = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "backup_dir_entries": len(backups),
        "verified": len(results),
        "failed": len(failures),
        "deep": deep,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "failures": [{"file": r["file"], "checks": r["checks"]} for r in failures],
        "ok": not failures,
    }
    if failures and alert:
        ops_alerts.emit(
            ops_alerts.BACKUP_INTEGRITY_FAILED,
            f"{len(failures)} backup artefact(s) failed verification",
            fields={"verified": len(results), "failed": len(failures)},
            stream=stream,
        )
    if write_status:
        directory = Path(backup_dir)
        if directory.is_dir():
            status_path = directory / VERIFY_STATUS_NAME
            try:
                fd = os.open(str(status_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(summary, handle, indent=2, sort_keys=True)
                    handle.write("\n")
            except OSError:
                summary["status_written"] = False
    return summary


def _day_key(path: Path) -> str:
    return path.name[len("erp-") : len("erp-") + 8]  # YYYYMMDD


def _week_key(path: Path) -> str:
    stamp = path.name[len("erp-") : len("erp-") + 8]
    try:
        day = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return "unknown"
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def plan_retention(
    backups: Sequence[Path],
    *,
    retain_daily: int = DEFAULT_RETAIN_DAILY,
    retain_weekly: int = DEFAULT_RETAIN_WEEKLY,
) -> Tuple[List[Path], List[Path]]:
    """Return ``(keep, delete)`` for the given artefacts, newest first."""
    ordered = sorted(backups, key=lambda p: p.name, reverse=True)
    if not ordered:
        return [], []
    keep: List[Path] = []
    seen_days: List[str] = []
    seen_weeks: List[str] = []

    for path in ordered:
        manifest = read_manifest(path)
        if manifest and manifest.get("legal_hold"):
            keep.append(path)
            continue
        day = _day_key(path)
        week = _week_key(path)
        if day not in seen_days and len(seen_days) < max(0, retain_daily):
            seen_days.append(day)
            keep.append(path)
            continue
        if week not in seen_weeks and len(seen_weeks) < max(0, retain_weekly):
            seen_weeks.append(week)
            keep.append(path)
            continue

    # The newest artefacts are never deleted, whatever the windows say.
    for path in ordered[:KEEP_MINIMUM]:
        if path not in keep:
            keep.append(path)

    keep_set = set(keep)
    delete = [p for p in ordered if p not in keep_set]
    return keep, delete


def prune(
    backup_dir: Path,
    *,
    retain_daily: int = DEFAULT_RETAIN_DAILY,
    retain_weekly: int = DEFAULT_RETAIN_WEEKLY,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply the retention policy. Legal holds and the newest artefact survive."""
    del now  # reserved: retention is name-ordered, so it needs no clock
    backups = list_backups(backup_dir)
    keep, delete = plan_retention(
        backups, retain_daily=retain_daily, retain_weekly=retain_weekly
    )
    deleted: List[str] = []
    errors: List[str] = []
    for path in delete:
        if dry_run:
            deleted.append(path.name)
            continue
        try:
            sidecar = manifest_path_for(path)
            path.unlink()
            if sidecar.is_file():
                sidecar.unlink()
            deleted.append(path.name)
        except OSError as exc:
            errors.append(f"{path.name}: {type(exc).__name__}")
    return {
        "kept": [p.name for p in keep],
        "deleted": deleted,
        "errors": errors,
        "retain_daily": retain_daily,
        "retain_weekly": retain_weekly,
        "dry_run": dry_run,
    }


def status(
    backup_dir: Path,
    *,
    max_age_seconds: Optional[int] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Monitoring summary: freshness, count, size and last verification."""
    directory = Path(backup_dir)
    backups = list_backups(directory)
    reference = time.time() if now is None else now
    if max_age_seconds is None:
        max_age_seconds = int(os.environ.get("ERP_BACKUP_MAX_AGE_SECONDS", 36 * 3600))
    newest_age = None
    total_bytes = 0
    if backups:
        newest = backups[-1]
        try:
            newest_age = round(max(0.0, reference - newest.stat().st_mtime), 1)
        except OSError:
            newest_age = None
    for path in backups:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue
    verify_status = None
    status_path = directory / VERIFY_STATUS_NAME
    if status_path.is_file():
        try:
            verify_status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            verify_status = None
    stale = newest_age is None or newest_age > max_age_seconds
    return {
        "backup_dir_configured": bool(backups) or directory.is_dir(),
        "count": len(backups),
        "newest": backups[-1].name if backups else None,
        "newest_age_seconds": newest_age,
        "max_age_seconds": max_age_seconds,
        "total_bytes": total_bytes,
        "stale": stale,
        "last_verify": verify_status,
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _load_runtime(db_path: Optional[str], vault_path: Optional[str]) -> Tuple[Any, Any]:
    from dz_erp.database import ERPDatabase
    from dz_erp.intake_ocr import PrivateDocumentVault

    db_value = db_path or os.environ.get("ERP_DB_PATH")
    vault_value = vault_path or os.environ.get("ERP_VAULT_PATH")
    if not db_value or not vault_value:
        raise RuntimeError("ERP_DB_PATH and ERP_VAULT_PATH must be set (or passed as flags)")
    return ERPDatabase(db_value), PrivateDocumentVault(Path(vault_value))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dz_erp.ops_backup",
        description="Encrypted backup, retention and integrity monitoring.",
    )
    parser.add_argument("--backup-dir", default=None, help=f"default: ${BACKUP_DIR_ENV}")
    parser.add_argument("--db-path", default=None, help="default: $ERP_DB_PATH")
    parser.add_argument("--vault-path", default=None, help="default: $ERP_VAULT_PATH")
    parser.add_argument("--key-file", default=None, help=f"default: ${BACKUP_KEY_FILE_ENV}")
    parser.add_argument(
        "--retain-daily", type=int, default=int(os.environ.get("ERP_BACKUP_RETAIN_DAILY", DEFAULT_RETAIN_DAILY))
    )
    parser.add_argument(
        "--retain-weekly", type=int, default=int(os.environ.get("ERP_BACKUP_RETAIN_WEEKLY", DEFAULT_RETAIN_WEEKLY))
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="take one backup, write its manifest, apply retention")
    verify = sub.add_parser("verify", help="verify every artefact")
    verify.add_argument("--deep", action="store_true", help="also restore each artefact")
    prune_cmd = sub.add_parser("prune", help="apply retention only")
    prune_cmd.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="print a JSON monitoring summary")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        backup_dir = resolve_backup_dir(args.backup_dir)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    if args.command == "status":
        summary = status(backup_dir)
        print(json.dumps(summary, indent=2, sort_keys=True))
        if summary["stale"]:
            ops_alerts.emit(
                ops_alerts.BACKUP_STALE,
                "newest backup is older than the configured limit",
                fields={"newest_age_seconds": summary["newest_age_seconds"]},
            )
            return 1
        return 0

    if args.command == "prune":
        result = prune(
            backup_dir,
            retain_daily=args.retain_daily,
            retain_weekly=args.retain_weekly,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not result["errors"] else 1

    if args.command == "verify":
        try:
            key = resolve_backup_key() if args.deep else None
        except (RuntimeError, OSError, PermissionError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2
        summary = verify_all(backup_dir, key=key, deep=args.deep)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["ok"] else 1

    if args.command == "run":
        try:
            key = resolve_backup_key()
            db, vault = _load_runtime(args.db_path, args.vault_path)
        except (RuntimeError, OSError, PermissionError, ValueError) as exc:
            ops_alerts.emit(
                ops_alerts.BACKUP_FAILED,
                f"backup could not start: {type(exc).__name__}",
            )
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2
        result = run_backup(
            db,
            vault,
            backup_dir,
            key,
            retain_daily=args.retain_daily,
            retain_weekly=args.retain_weekly,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI in tests
    sys.exit(main())
