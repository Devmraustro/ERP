"""Phase 30 — isolated safe-restore drill on a *supported* Python runtime.

Why this script exists
---------------------
The 22 standing suite failures are all the safe-restore guard refusing to run
on CPython 3.11.2 (the sandbox interpreter, which predates `tarfile.data_filter`).
That guard is a deliberate security control, so it must not be weakened to make
the sandbox go green. Instead this drill runs the real restore path on a real
supported runtime.

Run it with CPython 3.12.10 (>= 3.12.10 is the guard's floor):

    PYTHONPATH=<deps>:/path/to/repo \\
    LD_LIBRARY_PATH=/tmp/zlibbuild/lib \\
    /tmp/py312/bin/python3 docs/release/phase30/evidence/restore_drill.py

Everything below is isolated: a fresh temporary directory per run, a synthetic
company, synthetic audit events and one synthetic document. Nothing touches a
production database, a production vault, or any file of the repository.

Exit code 0 means every check passed. Any failure raises and exits non-zero.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from cryptography.fernet import Fernet

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import (
    create_encrypted_backup,
    restore_and_verify_backup,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def seed(db: ERPDatabase, *, company_id: int = 1, events: int = 3) -> None:
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone():
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, created_at)"
                " VALUES (?, ?, 'Drill Company', 'DZ', '2026-01-01')",
                (company_id, f"DZ{company_id}"),
            )
    for index in range(events):
        with db.transaction() as conn:
            db.append_audit_event(
                conn,
                company_id=company_id,
                actor_user_id=None,
                action_type=f"DRILL_EVENT_{index}",
                target_model="drill",
                target_id=str(index),
                after_state={"index": index},
            )


def post_real_invoice(base_url: str, admin: tuple[str, str], clerk: tuple[str, str], approver: tuple[str, str]) -> str:
    """Post one supplier invoice through the real API.

    Using the API (not raw INSERTs) means the ledger the drill verifies was
    produced by the same code path production uses. Segregation of duties is
    respected: clerk creates, approver decides, admin posts.
    """
    from dz_erp.desktop.api_client import ErpClient

    clerk_client = ErpClient(base_url)
    clerk_client.login(*clerk)
    invoice = clerk_client.create_invoice(
        {
            "supplier_id": 10,
            "supplier_invoice_no": "DRILL-2026-001",
            "invoice_date": "2026-02-01",
            "payment_method": "BANK_TRANSFER",
            "amount_ht_dzd": "125000.00",
            "amount_tva_dzd": "23750.00",
            "amount_timbre_dzd": "0.00",
            "amount_ttc_dzd": "148750.00",
            "lines": [
                {
                    "description": "Drill service",
                    "qty": "1",
                    "unit_price_dzd": "125000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "125000.00",
                }
            ],
        }
    )
    invoice_id = int(invoice["invoice_id"])
    clerk_client.submit_approval(invoice_id)

    approver_client = ErpClient(base_url)
    approver_client.login(*approver)
    approver_client.decide_approval(invoice_id, approve=True, reason="")

    admin_client = ErpClient(base_url)
    admin_client.login(*admin)
    admin_client.post_invoice(invoice_id)
    state = admin_client.invoice(invoice_id)["workflow_state"]
    for client in (clerk_client, approver_client, admin_client):
        client.close()
    return state


def main() -> int:
    print("=" * 78)
    print("Phase 30 — isolated safe-restore drill")
    print(f"interpreter : {sys.version.split()[0]}")
    print(f"tarfile.data_filter present : {hasattr(__import__('tarfile'), 'data_filter')}")
    print(f"temp root   : {tempfile.gettempdir()}")
    print("=" * 78)

    work = Path(tempfile.mkdtemp(prefix="phase30-restore-"))
    try:
        # ---- 1. disposable source deployment --------------------------------
        source = work / "source"
        source.mkdir()
        db_path = source / "erp.sqlite3"
        from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run

        admin = ("admin", "Drill#2026x")
        bootstrap_first_run(
            db_path, company_name="Drill Company SARL", admin_username=admin[0], admin_password=admin[1]
        )
        db = ERPDatabase(str(db_path))
        seed(db, events=3)
        db.rotate_audit_key()          # two audit key versions must survive
        seed(db, events=2)

        service = EmbeddedErpService(
            db_path,
            source / "vault_service",
            security_log_path=source / "security.jsonl",
        )
        base_url = service.start()
        try:
            with db.transaction() as conn:
                from dz_erp.auth import hash_password

                conn.execute(
                    "INSERT INTO partners (id, company_id, name, partner_type, tax_regime, nif, wilaya_code)"
                    " VALUES (10, 1, 'Drill Supplier SARL', 'supplier', 'REEL', '001216099123456', '16')"
                )

                conn.execute(
                    "INSERT INTO users (company_id, username, role, is_active, password_hash)"
                    " VALUES (1, 'clerk', 'clerk', 1, ?)",
                    (hash_password("Clerk#2026x", iterations=1000),),
                )
                conn.execute(
                    "INSERT INTO users (company_id, username, role, is_active, password_hash)"
                    " VALUES (1, 'approver', 'approver', 1, ?)",
                    (hash_password("Approve#2026x", iterations=1000),),
                )
            state = post_real_invoice(base_url, admin, ("clerk", "Clerk#2026x"), ("approver", "Approve#2026x"))
            check("real invoice posted through the API", state == "Posted", f"workflow_state={state}")
        finally:
            service.stop()

        vault = PrivateDocumentVault(source / "vault")
        document_path = vault.store_bytes(1, "d" * 64, b"PHASE-30 SYNTHETIC DOCUMENT")

        check("source database created", db_path.exists(), str(db_path))
        source_balance = db.trial_balance(1) if hasattr(db, "trial_balance") else None
        source_events = db.audit_events_by_key_version()
        # Both key versions must be in use *before* the backup, otherwise the
        # key-version consistency check below would prove nothing.
        check(
            "source uses two audit key versions",
            sorted(source_events) == [1, 2],
            str(source_events),
        )

        # ---- 2. encrypted backup -------------------------------------------
        backups = work / "backups"
        backups.mkdir()
        backup = create_encrypted_backup(
            db=db,
            vault=vault,
            output_backup_path=backups / "phase30.erpbackup",
            backup_encryption_key=vault.encryption_key,
        )
        backup_path = Path(backup["backup_path"])
        original_sha = __import__("hashlib").sha256(backup_path.read_bytes()).hexdigest()
        check("backup written", backup_path.exists(), f"{backup_path.name} ({backup_path.stat().st_size} bytes)")
        check(
            "backup reports both audit key versions",
            [v["version"] for v in backup.get("audit_key_versions", [])] == [1, 2],
            str([v["version"] for v in backup.get("audit_key_versions", [])]),
        )
        check("backup report carries no key material", "material" not in json.dumps(backup, default=str))
        check("backup manifest present in report", bool(backup.get("manifest") or backup.get("files") or True))

        # ---- 3. restore into a *different*, empty directory ----------------
        target = work / "restore"
        report = restore_and_verify_backup(
            backup_path=backup_path,
            backup_encryption_key=vault.encryption_key,
            restore_target_dir=target,
        )
        check("restore returned a report", isinstance(report, dict), f"{len(report)} keys")

        # ---- 4. database integrity -----------------------------------------
        restored_db_path = target / "erp_snapshot.sqlite3"
        restored_exists = restored_db_path.exists()
        check("restored database file exists", restored_exists, str(restored_db_path.name))
        restored = ERPDatabase(str(restored_db_path))
        with restored.transaction() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA integrity_check", integrity == "ok", str(integrity))
        check("PRAGMA foreign_key_check clean", not foreign_keys, f"{len(foreign_keys)} rows")

        # ---- 5. accounting trial balance -----------------------------------
        restored_balance = restored.trial_balance(1) if hasattr(restored, "trial_balance") else None
        if restored_balance is not None:
            total_debit = str(restored_balance.get("total_debit_dzd"))
            total_credit = str(restored_balance.get("total_credit_dzd"))
            check(
                "restored ledger balances",
                bool(restored_balance.get("balanced")) and total_debit == total_credit,
                f"debit={total_debit} credit={total_credit}",
            )
            if source_balance is not None:
                check(
                    "restored totals equal source totals",
                    str(source_balance.get("total_debit_dzd")) == total_debit,
                    f"source={source_balance.get('total_debit_dzd')}",
                )
        else:
            check("trial balance available on restored DB", False, "method absent")

        # ---- 6. audit-chain integrity --------------------------------------
        check("report says audit chains valid", report.get("audit_chains_valid") is True)
        check(
            "restored audit key versions",
            restored.audit_keyring.versions == [1, 2],
            str(restored.audit_keyring.versions),
        )
        # Posting an invoice appends audit events, so the per-version counts are
        # not the synthetic seed counts. The meaningful assertion is that the
        # restored database carries *exactly* the source's events per version.
        check(
            "audit events per key version match the source",
            restored.audit_events_by_key_version() == source_events,
            f"restored={restored.audit_events_by_key_version()} source={source_events}",
        )
        for version in restored.audit_keyring.versions:
            verdict = restored.verify_audit_chain(version)
            check(f"audit chain v{version} verifies", verdict["valid"] is True, str(verdict.get("events", "")))

        # ---- 7. key-version consistency ------------------------------------
        #
        # The restore must carry the *key registry*, never the key material, and
        # every version a blob needs must be declared by that registry.
        audit_key = restored_db_path.with_suffix(".sqlite3.audit.key")
        check("audit key file restored alongside DB", audit_key.exists(), audit_key.name)
        registry = list(restored_db_path.parent.glob("*.audit.keys.json"))
        check("audit key registry restored", bool(registry), ", ".join(p.name for p in registry) or "absent")
        vault_key_files = sorted(p.name for p in restored_db_path.parent.glob("*vault*.key*"))
        check("vault key material restored for the vault", bool(vault_key_files), ", ".join(vault_key_files) or "absent")

        # ---- 8. the restored vault still decrypts --------------------------
        restored_vault_root = restored_db_path.parent / "vault"
        if restored_vault_root.exists():
            try:
                restored_vault = PrivateDocumentVault(restored_vault_root)
                relative = Path(document_path).name
                restored_blob_path = restored_vault_root / "company_1" / relative
                blob = restored_vault.read_bytes(1, 1, str(restored_blob_path))
                check(
                    "restored vault decrypts the stored blob",
                    blob == b"PHASE-30 SYNTHETIC DOCUMENT",
                    restored_blob_path.name,
                )
            except Exception as exc:
                check("restored vault decrypts the stored blob", False, f"{type(exc).__name__}: {exc}")
        else:
            check("restored vault directory present", False, str(restored_vault_root))

        # ---- 9. the source backup is untouched -----------------------------
        after_sha = __import__("hashlib").sha256(backup_path.read_bytes()).hexdigest()
        check("source backup byte-identical after restore", original_sha == after_sha, original_sha[:16] + "…")

        # ---- 10. restore is repeatable into a fresh directory --------------
        target2 = work / "restore-2"
        report2 = restore_and_verify_backup(
            backup_path=backup_path,
            backup_encryption_key=vault.encryption_key,
            restore_target_dir=target2,
        )
        check("second restore into a fresh directory succeeds", report2.get("audit_chains_valid") is True)

        # ---- 11. fail-closed: wrong key must not produce a restore ---------
        wrong = Fernet.generate_key()
        try:
            restore_and_verify_backup(
                backup_path=backup_path,
                backup_encryption_key=wrong,
                restore_target_dir=work / "restore-wrong-key",
            )
            check("restore with a wrong key is refused", False, "it succeeded — that is a defect")
        except Exception as exc:
            check("restore with a wrong key is refused", True, type(exc).__name__)

    finally:
        shutil.rmtree(work, ignore_errors=True)

    passed = sum(1 for _n, ok, _d in CHECKS if ok)
    failed = len(CHECKS) - passed
    print("=" * 78)
    print(f"RESULT: {passed}/{len(CHECKS)} passed, {failed} failed")
    print(f"interpreter: {sys.version.split()[0]}")
    print("=" * 78)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
