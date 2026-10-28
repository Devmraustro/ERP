"""Subprocess workers for the process-boundary persistence tests.

Why this file exists
--------------------
A test that writes an encrypted document and reads it back in the same process
proves nothing about persistence.  ``Fernet.generate_key()`` used to be called on
every ``PrivateDocumentVault`` instantiation, which means every such test passed
while every real restart destroyed the data.  The same blind spot hid the fact
that ``restore_and_verify_backup`` verified its own work and then left no key on
disk for the next process to find.

So the persistence claims are tested the way they will actually fail: a separate
OS process performs each half of the operation and exits before the other half
runs.  Each worker is invoked as::

    python -m tests.process_boundary_workers <command> --state <dir>

and prints exactly one JSON object on stdout.  Exit status is 0 on success and
non-zero on a deliberate failure, so the parent test can assert on either.

These workers deliberately do NOT catch and repair anything.  If a key is
missing, corrupt or wrong, the worker is expected to fail - silently minting a
replacement key is the exact defect being guarded against.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

DOC_A_SHA = "a" * 64
DOC_B_SHA = "b" * 64
DOC_A = b"SYNTHETIC INVOICE PDF BYTES - company 1"
DOC_B = b"SYNTHETIC RECEIPT BYTES - company 1"


def _paths(state: Path) -> Dict[str, Path]:
    return {
        "db": state / "erp.sqlite3",
        "vault": state / "vault",
        "backup": state / "backups" / "erp.enc",
        "restore": state / "restored",
    }


def _seed_company(db: ERPDatabase) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'SARL Synthetic (tenant 1)', 'DZ', '2026-01-01')"
        )


def _append_audit_event(db: ERPDatabase, action: str, target: str) -> None:
    with db.transaction() as conn:
        db.append_audit_event(
            conn=conn,
            company_id=1,
            actor_user_id=None,
            action_type=action,
            target_model="probe",
            target_id=target,
            after_state={"synthetic": True, "target": target},
        )


def cmd_write(state: Path) -> Dict[str, Any]:
    """First process: create the database, store documents, append audit events."""
    p = _paths(state)
    db = ERPDatabase(str(p["db"]))
    _seed_company(db)
    vault = PrivateDocumentVault(p["vault"])
    stored = {
        "doc_a": vault.store_bytes(1, DOC_A_SHA, DOC_A),
        "doc_b": vault.store_bytes(1, DOC_B_SHA, DOC_B),
    }
    _append_audit_event(db, "DOCUMENT_STORED", "doc_a")
    _append_audit_event(db, "DOCUMENT_STORED", "doc_b")
    return {
        "stored_paths": stored,
        "vault_key_file": str(p["vault"].parent / (p["vault"].name + ".vault.key")),
        "audit_key_file": str(p["db"]) + ".audit.key",
    }


def cmd_read_documents(state: Path) -> Dict[str, Any]:
    """Second process: reopen the vault with no secrets supplied and decrypt."""
    p = _paths(state)
    vault = PrivateDocumentVault(p["vault"])
    docs = {}
    for sha in (DOC_A_SHA, DOC_B_SHA):
        blob = str(p["vault"] / "company_1" / f"{sha}.enc")
        docs[sha] = base64.b64encode(vault.read_bytes(1, 1, blob)).decode()
    return {"documents": docs, "key_supplied_explicitly": False}


def cmd_verify_chain(state: Path) -> Dict[str, Any]:
    """Second process: reopen the database with no secrets and verify the chain."""
    p = _paths(state)
    db = ERPDatabase(str(p["db"]))
    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]
    return {"audit_events": count, "chain": db.verify_audit_chain(company_id=1)}


def cmd_backup(state: Path) -> Dict[str, Any]:
    """Create an encrypted backup of the live database and vault."""
    p = _paths(state)
    db = ERPDatabase(str(p["db"]))
    vault = PrivateDocumentVault(p["vault"])
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=p["backup"],
        backup_encryption_key=vault.encryption_key,
    )
    return {"backup_path": str(meta["backup_path"]), "db_sha256": meta["db_sha256"]}


def cmd_restore(state: Path) -> Dict[str, Any]:
    """Restore the backup into a *different* directory, then exit."""
    p = _paths(state)
    report = restore_and_verify_backup(
        backup_path=p["backup"],
        backup_encryption_key=PrivateDocumentVault(p["vault"]).encryption_key,
        restore_target_dir=p["restore"],
    )
    return {
        "audit_chains_valid": report["audit_chains_valid"],
        "vault_files_verified": report["vault_files_verified"],
        "restored_db": str(p["restore"] / "erp_snapshot.sqlite3"),
        "restored_vault": str(p["restore"] / "vault"),
    }


def cmd_reopen_restored(state: Path) -> Dict[str, Any]:
    """Third process: open the restored artefacts with no secrets supplied.

    This is the check the original code failed: the restore passed its keys in
    memory, so a fresh process resolved a brand-new key and the chain read as
    EVENT_HASH_TAMPERED.
    """
    p = _paths(state)
    restored_db = p["restore"] / "erp_snapshot.sqlite3"
    restored_vault = p["restore"] / "vault"

    db = ERPDatabase(str(restored_db))
    chain = db.verify_audit_chain(company_id=1)

    vault = PrivateDocumentVault(restored_vault)
    # The database still records the *original* absolute paths.  Resolving them
    # is what proves the relocation fix works.
    with db.transaction() as conn:
        rows = conn.execute("SELECT vault_path FROM source_documents").fetchall()
    docs = {}
    for row in rows:
        original_path = row["vault_path"]
        docs[original_path] = base64.b64encode(vault.read_bytes(1, 1, original_path)).decode()
    return {"chain": chain, "documents": docs, "restored_db": str(restored_db)}


def cmd_seed_source_documents(state: Path) -> Dict[str, Any]:
    """Record the original (absolute) vault paths in ``source_documents``."""
    p = _paths(state)
    db = ERPDatabase(str(p["db"]))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
            " VALUES (7, 1, 'synthetic_clerk', 'clerk', 1, 'x')"
        )
        for index, sha in enumerate((DOC_A_SHA, DOC_B_SHA), start=1):
            conn.execute(
                "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size,"
                " page_count, file_sha256, vault_path, encrypted_at_rest, security_status,"
                " workflow_state, uploaded_by_id, uploaded_at)"
                " VALUES (?, 1, ?, 'application/pdf', ?, 1, ?, ?, 1, 'CLEAN', 'NeedsReview',"
                " 7, '2026-01-01')",
                (
                    index,
                    f"invoice-{sha[:8]}.pdf",
                    len(DOC_A),
                    sha,
                    str(p["vault"] / "company_1" / f"{sha}.enc"),
                ),
            )
    return {"seeded": True}


def cmd_init_empty_vault(state: Path) -> Dict[str, Any]:
    """First use: an empty vault must still mint a key."""
    p = _paths(state)
    PrivateDocumentVault(p["vault"])
    key_file = p["vault"].parent / (p["vault"].name + ".vault.key")
    return {"key_file": str(key_file), "key_exists": key_file.exists()}


def cmd_check_keys(state: Path) -> Dict[str, Any]:
    """Report which key files exist - used by the missing/corrupt key tests."""
    p = _paths(state)
    return {
        "vault_key_exists": (p["vault"].parent / (p["vault"].name + ".vault.key")).exists(),
        "audit_key_exists": (Path(str(p["db"])) .with_name(p["db"].name + ".audit.key")).exists(),
    }


def cmd_tamper_backup(state: Path) -> Dict[str, Any]:
    """Remove one vault blob from the archive to simulate an incomplete restore."""
    import io
    import tarfile

    from cryptography.fernet import Fernet

    p = _paths(state)
    key = PrivateDocumentVault(p["vault"]).encryption_key
    tar_bytes = Fernet(key).decrypt(p["backup"].read_bytes())

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        members = tar.getmembers()
        kept = [m for m in members if not m.name.endswith(".enc")]
        dropped = [m.name for m in members if m.name.endswith(".enc")]
        buffers = {m.name: tar.extractfile(m).read() for m in kept if tar.extractfile(m)}

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in buffers.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    p["backup"].write_bytes(Fernet(key).encrypt(out.getvalue()))
    return {"dropped_blobs": dropped, "remaining": sorted(buffers)}


COMMANDS = {
    "write": cmd_write,
    "read-documents": cmd_read_documents,
    "verify-chain": cmd_verify_chain,
    "backup": cmd_backup,
    "restore": cmd_restore,
    "reopen-restored": cmd_reopen_restored,
    "seed-source-documents": cmd_seed_source_documents,
    "check-keys": cmd_check_keys,
    "init-empty-vault": cmd_init_empty_vault,
    "tamper-backup": cmd_tamper_backup,
}


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--state", required=True, help="Directory holding the synthetic fixtures")
    args = parser.parse_args(argv)

    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    try:
        result = COMMANDS[args.command](state)
    except Exception as exc:  # reported to the parent as JSON
        print(json.dumps({"__error__": type(exc).__name__, "message": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
