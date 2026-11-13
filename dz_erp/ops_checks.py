"""Phase 10 - cron/systemd-facing operational checks with meaningful exit codes.

Every check prints one JSON document on stdout (so a log collector can index it) and
exits ``0`` when healthy, ``1`` when the check failed, and ``2`` when the check could
not run at all (bad configuration). The distinction matters: a monitoring system must
not treat "misconfigured" as "healthy".

    python -m dz_erp.ops_checks readiness [--deep-ocr]
    python -m dz_erp.ops_checks disk
    python -m dz_erp.ops_checks backup-age
    python -m dz_erp.ops_checks ocr

Alerts are raised only for failures, and only once per invocation - a check that runs
every five minutes must not flood a webhook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from dz_erp import ops_alerts
from dz_erp.readiness import (
    NOT_READY,
    check_backup,
    check_disk,
    check_ocr,
    evaluate_readiness,
)


def _load_runtime() -> tuple[Any, Any]:
    from dz_erp.database import ERPDatabase
    from dz_erp.intake_ocr import PrivateDocumentVault

    db_path = os.environ.get("ERP_DB_PATH")
    vault_path = os.environ.get("ERP_VAULT_PATH")
    if not db_path or not vault_path:
        raise RuntimeError("ERP_DB_PATH and ERP_VAULT_PATH must be set")
    return ERPDatabase(db_path), PrivateDocumentVault(Path(vault_path))


def _emit(document: dict) -> None:
    print(json.dumps(document, indent=2, sort_keys=True))


def cmd_readiness(args: argparse.Namespace) -> int:
    try:
        db, vault = _load_runtime()
    except (RuntimeError, OSError) as exc:
        _emit({"check": "readiness", "status": "error", "detail": str(exc)})
        return 2
    report = evaluate_readiness(
        db,
        vault,
        backup_dir=os.environ.get("ERP_BACKUP_DIR"),
        deep_ocr=args.deep_ocr,
    )
    _emit({"check": "readiness", **report})
    if report["status"] == NOT_READY:
        ops_alerts.emit(
            ops_alerts.READINESS_FAILED,
            "application readiness check failed",
            fields={"critical_failures": report["critical_failures"]},
        )
        return 1
    return 0


def cmd_disk(_: argparse.Namespace) -> int:
    db_path = os.environ.get("ERP_DB_PATH")
    vault_path = os.environ.get("ERP_VAULT_PATH")
    target = vault_path or db_path
    if not target:
        _emit({"check": "disk", "status": "error", "detail": "no ERP_DB_PATH/ERP_VAULT_PATH"})
        return 2
    check = check_disk(Path(target).parent)
    _emit({"check": "disk", **check.as_dict()})
    if check.status != "ok":
        ops_alerts.emit(
            ops_alerts.DISK_LOW,
            check.detail or "free space below the configured floor",
            fields=check.values,
        )
        return 1
    return 0


def cmd_backup_age(_: argparse.Namespace) -> int:
    backup_dir = os.environ.get("ERP_BACKUP_DIR")
    if not backup_dir:
        _emit({"check": "backup-age", "status": "error", "detail": "ERP_BACKUP_DIR is not set"})
        return 2
    check = check_backup(backup_dir)
    _emit({"check": "backup-age", **check.as_dict()})
    if check.status != "ok":
        ops_alerts.emit(
            ops_alerts.BACKUP_STALE,
            check.detail or "no recent backup artefact",
            fields=check.values,
        )
        return 1
    return 0


def cmd_ocr(_: argparse.Namespace) -> int:
    check = check_ocr(deep=True)
    _emit({"check": "ocr", **check.as_dict()})
    if check.status != "ok":
        ops_alerts.emit(
            ops_alerts.OCR_UNAVAILABLE,
            check.detail or "OCR engine unavailable",
            fields=check.values,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dz_erp.ops_checks",
        description="Operational checks with cron-friendly exit codes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    readiness = sub.add_parser("readiness", help="full readiness evaluation")
    readiness.add_argument("--deep-ocr", action="store_true", help="actually load the OCR model")
    sub.add_parser("disk", help="free-space check")
    sub.add_parser("backup-age", help="is the newest backup recent?")
    sub.add_parser("ocr", help="load the OCR engine and report")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "readiness": cmd_readiness,
        "disk": cmd_disk,
        "backup-age": cmd_backup_age,
        "ocr": cmd_ocr,
    }
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choices
        parser.error("unknown command")
        return 2
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - exercised via the CLI in tests
    sys.exit(main())
