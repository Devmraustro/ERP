"""Phase 10 - application readiness evaluation.

``/healthz`` answers "is this process alive?". It answers nothing about whether the
process can *work*: a full disk, an unwritable vault, a locked database or a missing
key all leave the process alive and useless. This module answers the second question.

Design rules:

* **No secrets, no paths in the response.** A readiness endpoint is usually reachable
  without authentication and is scraped by infrastructure; leaking the database path or
  key location is a free gift to an attacker. Checks report status, counts and
  durations only.
* **Critical vs advisory.** A failing critical check means "do not route traffic here"
  (HTTP 503). An advisory failure means "degraded, page the operator" (HTTP 200), e.g.
  a stale backup does not make the application unable to serve a request.
* **Writability is probed, not assumed.** Readiness opens the database and takes a
  write lock (`BEGIN IMMEDIATE` … `ROLLBACK`) so a read-only mount or a full disk is
  detected before the first invoice fails. The probe rolls back: it leaves no row.
* **Cheap by default.** The OCR model is several hundred megabytes of inference state;
  the default check verifies the subsystem is *importable*, and ``deep=True`` actually
  loads the engine when an operator asks for it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

READY = "ready"
DEGRADED = "degraded"
NOT_READY = "not_ready"

OK = "ok"
FAIL = "fail"
UNKNOWN = "unknown"

#: Tables whose absence means "this is not an ERP database" rather than "empty company".
CORE_TABLES = (
    "companies",
    "users",
    "partners",
    "supplier_invoices",
    "journal_entries",
    "journal_entry_lines",
    "audit_events",
)

DEFAULT_MIN_FREE_BYTES = 512 * 1024 * 1024  # 512 MiB
DEFAULT_MAX_BACKUP_AGE_SECONDS = 36 * 3600  # a daily backup may be 1.5 days old
BACKUP_SUFFIXES = (".erpbackup", ".enc", ".backup")

_OCR_CACHE: Dict[str, Any] = {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Check:
    """One readiness check."""

    name: str
    status: str
    critical: bool = True
    detail: str = ""
    values: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "critical": self.critical,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.values:
            payload["values"] = self.values
        return payload


def _connect_for_probe(db: Any) -> sqlite3.Connection:
    persistent = getattr(db, "_persistent_conn", None)
    if persistent is not None:
        return persistent
    path = getattr(db, "db_path", ":memory:")
    if path == ":memory:":
        raise sqlite3.OperationalError("in-memory database has no probe connection")
    return sqlite3.connect(path, timeout=5.0)


def check_database(db: Any) -> Check:
    """Integrity, schema presence and write-lock reachability of the database."""
    started = time.perf_counter()
    try:
        conn = _connect_for_probe(db)
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()
            integrity = (quick[0] if quick else "unknown") or "unknown"
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing = [t for t in CORE_TABLES if t not in tables]
            # Writability: take the write lock and give it straight back.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        finally:
            if conn is not getattr(db, "_persistent_conn", None):
                conn.close()
    except sqlite3.Error as exc:
        return Check(
            "database",
            FAIL,
            detail=f"sqlite error: {type(exc).__name__}",
            values={"duration_ms": round((time.perf_counter() - started) * 1000.0, 2)},
        )
    if integrity != "ok":
        return Check("database", FAIL, detail=f"quick_check returned {integrity!r}")
    if missing:
        return Check(
            "database",
            FAIL,
            detail="required tables missing",
            values={"missing_tables": missing},
        )
    return Check(
        "database",
        OK,
        values={
            "table_count": len(tables),
            "write_lock": True,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        },
    )


def _mode_of(path: Path) -> Optional[str]:
    try:
        return oct(path.stat().st_mode & 0o777)
    except OSError:
        return None


def check_vault(vault: Any) -> Check:
    """Vault directory, key custody and write access."""
    root = Path(getattr(vault, "vault_root"))
    if not root.exists():
        return Check("vault", FAIL, detail="vault directory missing")
    if not root.is_dir():
        return Check("vault", FAIL, detail="vault path is not a directory")
    try:
        probe = root / ".readiness_probe"
        probe.write_bytes(b"probe")
        probe.unlink()
    except OSError as exc:
        return Check("vault", FAIL, detail=f"vault not writable: {type(exc).__name__}")

    values: Dict[str, Any] = {"writable": True}
    key_path = Path(getattr(vault, "vault_key_path", root.parent / (root.name + ".vault.key")))
    values["key_present"] = key_path.exists()
    key_mode = _mode_of(key_path) if key_path.exists() else None
    values["key_mode"] = key_mode
    dir_mode = _mode_of(root)
    values["dir_mode"] = dir_mode

    problems = []
    if not key_path.exists():
        problems.append("vault key file missing")
    elif key_mode not in ("0o600",):
        problems.append("vault key file is not 0600")
    # A world-readable *vault directory* exposes file names only - every blob is
    # encrypted - so it is reported, not failed. The key's own permissions are the
    # critical property and are enforced above.
    try:
        validation = vault.validate_key_configuration(check_referenced_versions=False)
        values["active_key_version"] = validation.get("active_version")
    except Exception as exc:
        problems.append(f"key configuration invalid: {type(exc).__name__}")

    try:
        values["blob_count"] = sum(1 for _ in root.rglob("*.enc"))
    except OSError:
        values["blob_count"] = None

    if problems:
        return Check("vault", FAIL, detail="; ".join(problems), values=values)
    return Check("vault", OK, values=values)


def check_key_custody(vault: Any) -> Check:
    """Advisory: is the directory that holds the key files owner-only?

    V1 custody is a host-local key file (0600) inside a key directory (0700). The file
    mode is the property that protects the secret, so a loose *directory* does not stop
    the service - but it is exactly the drift an operator should hear about, which is
    why this check exists as an advisory rather than being folded into "vault ok".
    """
    root = Path(getattr(vault, "vault_root"))
    key_path = Path(getattr(vault, "vault_key_path", root.parent / (root.name + ".vault.key")))
    key_dir = key_path.parent
    mode = _mode_of(key_dir)
    values = {"key_dir_mode": mode}
    if mode is None:
        return Check("key_custody", FAIL, critical=False, detail="key directory not readable", values=values)
    if mode != "0o700":
        return Check(
            "key_custody",
            FAIL,
            critical=False,
            detail="key directory is not 0700",
            values=values,
        )
    return Check("key_custody", OK, critical=False, values=values)


def check_disk(
    path: Any,
    *,
    min_free_bytes: Optional[int] = None,
) -> Check:
    """Free space on the volume that holds the data."""
    if min_free_bytes is None:
        min_free_bytes = int(os.environ.get("ERP_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES))
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)  # type: ignore[arg-type]
    except OSError as exc:
        return Check("disk", FAIL, detail=f"disk usage unavailable: {type(exc).__name__}")
    values = {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "min_free_bytes": min_free_bytes,
        "free_percent": round(100.0 * usage.free / usage.total, 1) if usage.total else None,
    }
    if usage.free < min_free_bytes:
        return Check(
            "disk",
            FAIL,
            detail=f"free space below the {min_free_bytes} byte floor",
            values=values,
        )
    return Check("disk", OK, values=values)


def check_ocr(*, deep: bool = False) -> Check:
    """Is the OCR subsystem present - and, in deep mode, actually loadable?"""
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    if spec is None:
        return Check(
            "ocr",
            FAIL,
            detail="rapidocr_onnxruntime is not installed",
            values={"deep": deep, "engine_loaded": False},
        )
    if not deep:
        return Check("ocr", OK, values={"deep": False, "engine_loaded": False})

    now = time.monotonic()
    cached = _OCR_CACHE.get("engine")
    if cached is not None and now - _OCR_CACHE.get("loaded_at", 0.0) < 3600:
        return Check(
            "ocr",
            OK,
            values={"deep": True, "engine_loaded": True, "cached": True},
        )
    started = time.perf_counter()
    try:
        from dz_erp.intake_ocr import get_rapid_ocr

        get_rapid_ocr()
    except Exception as exc:
        return Check(
            "ocr",
            FAIL,
            detail=f"engine failed to load: {type(exc).__name__}",
            values={"deep": True, "engine_loaded": False},
        )
    _OCR_CACHE["engine"] = True
    _OCR_CACHE["loaded_at"] = now
    return Check(
        "ocr",
        OK,
        values={
            "deep": True,
            "engine_loaded": True,
            "load_ms": round((time.perf_counter() - started) * 1000.0, 2),
        },
    )


def newest_backup(backup_dir: Any) -> Optional[Path]:
    directory = Path(backup_dir)
    if not directory.is_dir():
        return None
    candidates: Iterable[Path] = (
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix in BACKUP_SUFFIXES and not p.name.startswith(".")
    )
    newest = None
    newest_mtime = -1.0
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = candidate, mtime
    return newest


def check_backup(
    backup_dir: Any,
    *,
    max_age_seconds: Optional[int] = None,
    now: Optional[float] = None,
) -> Check:
    """Advisory check: is there a recent backup artefact?"""
    if max_age_seconds is None:
        max_age_seconds = int(
            os.environ.get("ERP_BACKUP_MAX_AGE_SECONDS", DEFAULT_MAX_BACKUP_AGE_SECONDS)
        )
    if backup_dir is None:
        return Check(
            "backup",
            UNKNOWN,
            critical=False,
            detail="no backup directory configured",
        )
    latest = newest_backup(backup_dir)
    if latest is None:
        return Check(
            "backup",
            FAIL,
            critical=False,
            detail="no backup artefact found",
            values={"max_age_seconds": max_age_seconds},
        )
    reference = time.time() if now is None else now
    try:
        age = max(0.0, reference - latest.stat().st_mtime)
    except OSError as exc:
        return Check("backup", FAIL, critical=False, detail=f"stat failed: {type(exc).__name__}")
    values = {
        "age_seconds": round(age, 1),
        "max_age_seconds": max_age_seconds,
        "size_bytes": latest.stat().st_size,
    }
    if age > max_age_seconds:
        return Check("backup", FAIL, critical=False, detail="newest backup is older than the limit", values=values)
    return Check("backup", OK, critical=False, values=values)


def evaluate_readiness(
    db: Any,
    vault: Any = None,
    *,
    backup_dir: Any = None,
    min_free_bytes: Optional[int] = None,
    max_backup_age_seconds: Optional[int] = None,
    deep_ocr: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Run every readiness check and summarise.

    Returns ``{"status": ready|degraded|not_ready, "checks": {...}}``. ``not_ready``
    means at least one *critical* check failed; ``degraded`` means only advisory checks
    failed.
    """
    checks = [check_database(db)]
    if vault is not None:
        checks.append(check_vault(vault))
        checks.append(check_key_custody(vault))
        disk_target = Path(getattr(vault, "vault_root")).parent
    else:
        disk_target = Path(getattr(db, "db_path", ".")).parent
    checks.append(check_disk(disk_target, min_free_bytes=min_free_bytes))
    checks.append(check_ocr(deep=deep_ocr))
    if backup_dir is not None:
        checks.append(
            check_backup(backup_dir, max_age_seconds=max_backup_age_seconds, now=now)
        )

    critical_failures = [c.name for c in checks if c.critical and c.status != OK]
    advisory_failures = [c.name for c in checks if not c.critical and c.status not in (OK, UNKNOWN)]
    if critical_failures:
        status = NOT_READY
    elif advisory_failures:
        status = DEGRADED
    else:
        status = READY
    return {
        "status": status,
        "generated_at": _iso_now(),
        "checks": {c.name: c.as_dict() for c in checks},
        "critical_failures": critical_failures,
        "advisory_failures": advisory_failures,
    }


def readiness_http_status(report: Dict[str, Any]) -> int:
    """503 when the process cannot do its job, 200 otherwise (including degraded)."""
    return 503 if report.get("status") == NOT_READY else 200
