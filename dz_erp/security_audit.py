# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 5 — Security, RBAC, Tenant Isolation, Secret-Redacting Logger, and Encrypted Backup/Restore."""

import base64
import os
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tarfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet

from .database import (
    AUDIT_KEY_SUFFIX,
    AuthorizationError,
    ERPDatabase,
    FinancialIntegrityError,
    TenantIsolationError,
)
from .decimal_utils import format_dzd, to_decimal
from .intake_ocr import VAULT_KEY_SUFFIX, PrivateDocumentVault

SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "api_token",
    "Authorization",
    "authorization",
    "encryption_key",
    "hmac_secret",
    "bank_rib",
    "raw_text",
}

SENSITIVE_VALUE_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]+", re.IGNORECASE), "[REDACTED_TOKEN]"),
    (re.compile(r"\bDZ[0-9]{2}[0-9A-Z]{16,24}\b"), "[REDACTED_DZ_IBAN]"),
    (re.compile(r"\b[0-9]{18}\b"), "[REDACTED_NIN]"),
    (re.compile(r"\b[0-9]{20}\b"), "[REDACTED_RIB]"),
]


class StructuredRedactingLogger:
    """Structured JSON logger that redacts credentials, tokens, bank RIBs, and invoice text."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def _redact_value(self, key: str, val: Any) -> Any:
        if key.lower() in {k.lower() for k in SENSITIVE_KEYS}:
            return "[REDACTED]"
        if isinstance(val, dict):
            return {k: self._redact_value(k, v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._redact_value(key, item) for item in val]
        if isinstance(val, str):
            sanitized = val
            for pat, repl in SENSITIVE_VALUE_PATTERNS:
                sanitized = pat.sub(repl, sanitized)
            return sanitized
        return val

    def log(self, level: str, event: str, correlation_id: str, **context: Any) -> Dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "event": event,
            "correlation_id": correlation_id,
            "context": {k: self._redact_value(k, v) for k, v in context.items()},
        }
        self.records.append(entry)
        return entry


def read_supplier_invoice_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    invoice_id: int,
) -> Dict[str, Any]:
    """Read a supplier invoice enforcing authentication, RBAC, and strict company isolation.

    Denials are recorded in the append-only audit log *before* the error is
    raised. Raising from inside the transaction would roll the audit event back
    with it, leaving a blocked cross-tenant or unauthorized-role read with no
    forensic trace, so the denial is raised only after the transaction commits.
    """
    denial: Optional[Exception] = None
    result: Optional[Dict[str, Any]] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("Authentication failed: user is inactive or revoked.")
        else:
            inv = conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (invoice_id,)).fetchone()
            if not inv:
                denial = ValueError(f"Invoice {invoice_id} does not exist.")
            elif inv["company_id"] != user["company_id"]:
                db.append_audit_event(
                    conn=conn,
                    company_id=inv["company_id"],
                    actor_user_id=requesting_user_id,
                    action_type="SECURITY_CROSS_COMPANY_READ_BLOCKED",
                    target_model="supplier_invoices",
                    target_id=str(invoice_id),
                    after_state={"attacker_company_id": user["company_id"], "target_company_id": inv["company_id"]},
                )
                denial = TenantIsolationError("Cross-company read access denied.")
            elif user["role"] == "warehouse":
                db.append_audit_event(
                    conn=conn,
                    company_id=inv["company_id"],
                    actor_user_id=requesting_user_id,
                    action_type="SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED",
                    target_model="supplier_invoices",
                    target_id=str(invoice_id),
                    after_state={"role": user["role"]},
                )
                denial = AuthorizationError("Warehouse role is not permitted to read financial invoices.")
            else:
                lines = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
                    ).fetchall()
                ]
                result = dict(inv)
                result["lines"] = lines

    if denial is not None:
        raise denial
    assert result is not None
    return result


def read_source_document_file_secure(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    requesting_user_id: int,
    document_id: str,
) -> bytes:
    """Read raw decrypted source document bytes with company-isolation and audit logging.

    As with :func:`read_supplier_invoice_secure`, denials are audited and
    committed before the error is raised so the blocked attempt survives.
    """
    denial: Optional[Exception] = None
    raw_bytes: Optional[bytes] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        else:
            doc = conn.execute("SELECT * FROM source_documents WHERE id = ?", (document_id,)).fetchone()
            if not doc:
                denial = ValueError("Source document not found.")
            elif doc["company_id"] != user["company_id"]:
                db.append_audit_event(
                    conn=conn,
                    company_id=doc["company_id"],
                    actor_user_id=requesting_user_id,
                    action_type="SECURITY_CROSS_COMPANY_FILE_ACCESS_BLOCKED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={"attacker_company_id": user["company_id"], "target_company_id": doc["company_id"]},
                )
                denial = TenantIsolationError("Cross-company file access denied.")
            else:
                raw_bytes = vault.read_bytes(
                    company_id=doc["company_id"],
                    requesting_company_id=user["company_id"],
                    vault_path=doc["vault_path"],
                )
                db.append_audit_event(
                    conn=conn,
                    company_id=doc["company_id"],
                    actor_user_id=requesting_user_id,
                    action_type="DOCUMENT_FILE_ACCESSED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={"byte_size": len(raw_bytes)},
                )

    if denial is not None:
        raise denial
    assert raw_bytes is not None
    return raw_bytes


def create_encrypted_backup(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    output_backup_path: Path,
    backup_encryption_key: bytes,
) -> Dict[str, Any]:
    """Create a consistent SQLite online backup + vault archive encrypted with Fernet and a SHA-256 manifest."""
    output_backup_path = Path(output_backup_path)
    output_backup_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_backup_path.parent / f".tmp_backup_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        db_snap_path = tmp_dir / "erp_snapshot.sqlite3"
        src_conn = db._persistent_conn if db._persistent_conn is not None else sqlite3.connect(db.db_path)
        dst_conn = sqlite3.connect(str(db_snap_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            if db._persistent_conn is None:
                src_conn.close()

        db_sha256 = hashlib.sha256(db_snap_path.read_bytes()).hexdigest()

        vault_copy_dir = tmp_dir / "vault"
        vault_hashes: Dict[str, str] = {}
        if vault.vault_root.exists():
            shutil.copytree(vault.vault_root, vault_copy_dir)
            for p in sorted(vault_copy_dir.rglob("*.enc")):
                rel = str(p.relative_to(vault_copy_dir))
                vault_hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            vault_copy_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "db_sha256": db_sha256,
            "vault_file_hashes": vault_hashes,
            "vault_encryption_key_b64": base64.b64encode(vault.encryption_key).decode("ascii"),
            # The audit chain is only verifiable with the key that wrote it, so a
            # backup that cannot restore that key cannot prove its own integrity.
            # It travels inside the Fernet-encrypted archive, i.e. under exactly
            # the same protection as the vault key above.
            "audit_hmac_secret_b64": base64.b64encode(db._hmac_secret).decode("ascii"),
        }
        (tmp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
            tar.add(db_snap_path, arcname="erp_snapshot.sqlite3")
            tar.add(tmp_dir / "manifest.json", arcname="manifest.json")
            tar.add(vault_copy_dir, arcname="vault")

        fernet = Fernet(backup_encryption_key)
        encrypted_archive = fernet.encrypt(tar_buf.getvalue())
        output_backup_path.write_bytes(encrypted_archive)

        return {
            "backup_path": str(output_backup_path),
            "encrypted_size_bytes": len(encrypted_archive),
            "db_sha256": db_sha256,
            "vault_file_count": len(vault_hashes),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_key_file(key_path: Path, secret: bytes) -> None:
    """Write a secret to disk with owner-only permissions, replacing any stale value."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secret)


def restore_and_verify_backup(
    backup_path: Path,
    backup_encryption_key: bytes,
    restore_target_dir: Path,
    hmac_secret: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Decrypt, unpack, and verify a backup archive in an isolated target directory."""
    restore_target_dir = Path(restore_target_dir)
    restore_target_dir.mkdir(parents=True, exist_ok=True)

    fernet = Fernet(backup_encryption_key)
    decrypted_tar = fernet.decrypt(Path(backup_path).read_bytes())

    with tarfile.open(fileobj=io.BytesIO(decrypted_tar), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                raise FinancialIntegrityError(f"Unsafe tar member path detected: {member.name}")
        tar.extractall(path=restore_target_dir)

    manifest = json.loads((restore_target_dir / "manifest.json").read_text(encoding="utf-8"))
    restored_db_path = restore_target_dir / "erp_snapshot.sqlite3"
    actual_db_sha256 = hashlib.sha256(restored_db_path.read_bytes()).hexdigest()
    if actual_db_sha256 != manifest["db_sha256"]:
        raise FinancialIntegrityError("Restored database SHA-256 mismatch!")

    restored_vault_dir = restore_target_dir / "vault"
    for rel_path, expected_sha in manifest["vault_file_hashes"].items():
        fp = restored_vault_dir / rel_path
        if not fp.exists() or hashlib.sha256(fp.read_bytes()).hexdigest() != expected_sha:
            raise FinancialIntegrityError(f"Restored vault blob {rel_path} failed SHA-256 verification!")

    # An explicit secret wins; otherwise take the one the backup was written
    # with.  Archives created before the key was carried have no such field, so
    # fall back to whatever ERPDatabase would resolve on its own.
    audit_secret = (
        hmac_secret
        or base64.b64decode(manifest["audit_hmac_secret_b64"])
        if manifest.get("audit_hmac_secret_b64")
        else hmac_secret
    )
    vault_secret = base64.b64decode(manifest["vault_encryption_key_b64"])

    # Persist both keys beside the restored artefacts.  Without this the
    # restore only works through the objects returned here: reopening the
    # restored database in a fresh process - which is the entire point of a
    # restore - resolves a brand-new key, so every audit hash reads as
    # EVENT_HASH_TAMPERED and every stored document raises InvalidToken.  The
    # keys are already inside the Fernet-encrypted archive, so writing them to
    # the freshly-restored 0600 directory adds no new exposure.
    if audit_secret:
        _write_key_file(Path(str(restored_db_path) + AUDIT_KEY_SUFFIX), audit_secret)
    _write_key_file(
        restored_vault_dir.parent / (restored_vault_dir.name + VAULT_KEY_SUFFIX),
        vault_secret,
    )

    restored_db = ERPDatabase(db_path=str(restored_db_path), hmac_secret=audit_secret)
    restored_vault = PrivateDocumentVault(vault_root=restored_vault_dir, encryption_key=vault_secret)

    with restored_db.transaction() as conn:
        companies = [r["id"] for r in conn.execute("SELECT id FROM companies").fetchall()]
        posted_lines = conn.execute(
            """
            SELECT jel.debit_dzd, jel.credit_dzd
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE je.state = 'POSTED'
            """
        ).fetchall()

    total_debit = sum((to_decimal(r["debit_dzd"]) for r in posted_lines), Decimal("0.00"))
    total_credit = sum((to_decimal(r["credit_dzd"]) for r in posted_lines), Decimal("0.00"))
    if total_debit != total_credit:
        raise FinancialIntegrityError("Restored database trial balance is out of balance!")

    audit_verifications = {cid: restored_db.verify_audit_chain(cid) for cid in companies}
    if not all(v["valid"] for v in audit_verifications.values()):
        raise FinancialIntegrityError(f"Restored audit chain verification failed: {audit_verifications}")

    return {
        "restored_db": restored_db,
        "restored_vault": restored_vault,
        "db_sha256_verified": True,
        "vault_files_verified": len(manifest["vault_file_hashes"]),
        "trial_balance_debit_dzd": format_dzd(total_debit),
        "trial_balance_credit_dzd": format_dzd(total_credit),
        "audit_chains_valid": True,
    }
