#!/usr/bin/env python3
"""Phase 9F - isolated disaster-recovery drill with measured RPO/RTO.

Everything happens in a temporary directory: a synthetic database + vault are
created, a posted journal entry is written, an encrypted backup is taken, and the
backup is restored into a *separate* directory. The restored copy is then treated
as the object under test:

  * database hash must match the manifest recorded at backup time;
  * every vault file must verify against its recorded SHA-256;
  * the trial balance must be balanced and must equal the source;
  * the audit chain must verify;
  * SQLite integrity_check / foreign_key_check must return ok;
  * measured durations give the RTO numbers quoted in the report.

No production data, key or backup is touched: paths are all under one mkdtemp().

Usage: ERP_ROOT=/path/to/tree ERP_VENV=/path/to/venv python dr_drill.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Default to the checkout this script lives in, so the drill is reproducible from
# any copy of the tree; ERP_ROOT overrides it for a separate deployment.
ROOT = Path(os.environ.get("ERP_ROOT") or Path(__file__).resolve().parents[4])
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet

from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp import security_audit
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup


def safe_restore_supported() -> bool:
    """Is this interpreter allowed to restore?

    Newer trees export ``security_audit.safe_restore_supported``; the branch this drill
    is also run against predates that helper, so the same policy (CPython 3.11.17+ or
    3.12.10+, as documented in ``security_audit``) is applied inline when it is absent.
    """
    helper = getattr(security_audit, "safe_restore_supported", None)
    if callable(helper):
        return bool(helper())
    version = tuple(sys.version_info[:3])
    return (version[:2] == (3, 11) and version >= (3, 11, 17)) or (
        version[:2] == (3, 12) and version >= (3, 12, 10)
    )

RESULT: dict = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_source(root: Path) -> tuple[ERPDatabase, PrivateDocumentVault]:
    db = ERPDatabase(str(root / "source.sqlite3"))
    vault = PrivateDocumentVault(root / "source-vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Drill Co', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
            " VALUES (1, 1, 'admin', 'admin', 1, ?)",
            (hash_password("Drill#2026x", iterations=1000),),
        )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc) VALUES (1, 1, 'Drill Supplier', 'supplier', 'DZ', 'REEL',"
            " '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
        )
        # A balanced, posted entry: 1 234 567,00 DZD debit / credit.
        conn.execute(
            "INSERT INTO journal_entries (id, company_id, entry_number, journal_code, entry_date,"
            " state, source_model, source_id, total_debit_dzd, total_credit_dzd, posted_by_id, posted_at)"
            " VALUES (1, 1, 'DR-0001', 'OD', '2026-02-01', 'POSTED', 'drill', '1',"
            " '1234567.00', '1234567.00', 1, '2026-02-01T10:00:00+00:00')"
        )
        conn.executemany(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label,"
            " debit_dzd, credit_dzd, g50_tag) VALUES (1, 1, ?, ?, ?, ?, ?)",
            [
                ("601", "Achats de marchandises", "1234567.00", "0.00", "G50-ACH"),
                ("401", "Fournisseur Drill Supplier", "0.00", "1234567.00", "G50-FRS"),
            ],
        )
    # Real audit events, so the restored chain has content to verify (an empty
    # chain is trivially "valid" and would prove nothing).
    with db.transaction() as conn:
        for index in range(1, 4):
            db.append_audit_event(
                conn,
                company_id=1,
                actor_user_id=1,
                action_type=f"DRILL_ACTION_{index}",
                target_model="drill",
                target_id=str(index),
                after_state={"step": index, "amount_dzd": "1234567.00"},
                correlation_id=f"drill-{index}",
            )
    # A document in the vault, so the restore has file-vault content to verify.
    payload = b"%PDF-1.7 drill invoice payload " + b"X" * 512
    vault.store_bytes(1, hashlib.sha256(payload).hexdigest(), payload)
    return db, vault


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="phase9f-"))
    started = time.perf_counter()
    print(json.dumps({"event": "drill_start", "at": now_iso(), "workdir": str(work)}))

    db, vault = build_source(work)
    print(json.dumps({"event": "source_ready",
                      "audit_chain": db.verify_audit_chain(1),
                      "trial_balance": db.trial_balance(1)}))

    last_write = time.time()
    key = Fernet.generate_key()
    backup_path = work / "backups" / "drill.enc"
    t0 = time.perf_counter()
    create_encrypted_backup(db, vault, backup_path, backup_encryption_key=key)
    backup_seconds = time.perf_counter() - t0
    backup_size = backup_path.stat().st_size
    RESULT["backup_seconds"] = round(backup_seconds, 3)
    RESULT["backup_bytes"] = backup_size
    RESULT["rpo_seconds"] = round(time.time() - last_write, 3)
    print(json.dumps({"event": "backup_done", "seconds": round(backup_seconds, 3),
                      "bytes": backup_size, "path": str(backup_path)}))

    restore_dir = work / "restored"
    t1 = time.perf_counter()
    report = restore_and_verify_backup(
        backup_path=backup_path,
        backup_encryption_key=key,
        restore_target_dir=restore_dir,
    )
    restore_seconds = time.perf_counter() - t1
    RESULT["restore_seconds"] = round(restore_seconds, 3)
    print(json.dumps({"event": "restore_done", "seconds": round(restore_seconds, 3),
                      "report_keys": sorted(report),
                      "restored_db_path": str(getattr(report.get("restored_db"), "db_path",
                                                      report.get("restored_db"))),
                      "restored_vault_path": str(getattr(report.get("restored_vault"),
                                                         "vault_root", report.get("restored_vault")))}))

    # Treat the restored copy as the deployment under test.
    restored = report["restored_db"]
    restored_vault = report["restored_vault"]
    restored_vault_path = Path(getattr(restored_vault, "vault_root", restored_vault))
    with restored.transaction() as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()

    source_tb = db.trial_balance(1)
    restored_tb = restored.trial_balance(1)
    source_chain = db.verify_audit_chain(1)
    restored_chain = restored.verify_audit_chain(1)

    vault_files = sorted(p for p in restored_vault_path.rglob("*.enc"))
    hashes_ok = []
    for path in vault_files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes_ok.append({"file": path.name, "sha256": digest, "bytes": path.stat().st_size})

    checks = {
        "interpreter_supported": safe_restore_supported(),
        "sqlite_integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "db_sha256_verified": report.get("db_sha256_verified"),
        "vault_files_verified": report.get("vault_files_verified"),
        "restored_vault_files_on_disk": len(vault_files),
        "trial_balance_source": source_tb,
        "trial_balance_restored": restored_tb,
        "trial_balance_equal": source_tb == restored_tb,
        "audit_chain_source": source_chain,
        "audit_chain_restored": restored_chain,
        "audit_chain_equal": source_chain == restored_chain,
        "generation_id": report.get("generation_id"),
    }
    RESULT["checks"] = checks
    RESULT["vault_hashes"] = hashes_ok
    RESULT["total_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps({"event": "verification", **checks}, default=str))

    critical = [
        checks["interpreter_supported"] is True,
        checks["sqlite_integrity_check"] == "ok",
        checks["foreign_key_violations"] == 0,
        checks["db_sha256_verified"] is True,
        checks["vault_files_verified"] == checks["restored_vault_files_on_disk"],
        checks["trial_balance_equal"] is True,
        checks["audit_chain_equal"] is True,
        checks["audit_chain_restored"]["valid"] is True,
        checks["audit_chain_restored"]["checked_count"] > 0,
    ]
    verdict = "PASS" if all(critical) else "FAIL"
    RESULT["verdict"] = verdict
    print(json.dumps({"event": "drill_summary", "verdict": verdict,
                      "measured": {k: RESULT[k] for k in ("backup_seconds", "restore_seconds",
                                                          "backup_bytes", "rpo_seconds",
                                                          "total_seconds")}}))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
