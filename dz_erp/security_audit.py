# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 5 — Security, RBAC, Tenant Isolation, Secret-Redacting Logger, and Encrypted Backup/Restore."""

import base64
import contextlib
import os
import hashlib
import io
import json
import re
import shutil
import sys
import sqlite3
import stat
import tarfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterator, List, Optional, Tuple

from cryptography.fernet import Fernet

from .localization import describe_invoice_timbre_provenance
from .database import (
    AUDIT_KEY_SUFFIX,
    AUDIT_KEYRING_SUFFIX,
    AUDIT_KEY_ENV,
    AuditKeyUnavailableError,
    AuthorizationError,
    ERPDatabase,
    FinancialIntegrityError,
    TenantIsolationError,
)
from .key_rotation import KeyConfigurationError, KeyRing
from .decimal_utils import format_dzd, to_decimal
from .intake_ocr import VAULT_KEY_ENV, VAULT_KEY_SUFFIX, VAULT_KEYRING_SUFFIX, PrivateDocumentVault
from .review_policy import evaluate_document_review_policy

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

try:  # pragma: no cover - the import itself is the platform branch
    import msvcrt
except ImportError:  # pragma: no cover - not reachable on Windows
    msvcrt = None  # type: ignore[assignment]

# How long a writer waits for a contended cross-process lock before giving up.
_LOG_LOCK_TIMEOUT_SECONDS = 30.0

# Security log rotation configuration (can be overridden via environment)
# Max file size in bytes before rotation (default: 100 MB)
_LOG_MAX_FILE_SIZE = int(os.environ.get("ERP_SECURITY_LOG_MAX_SIZE", "104857600"))
# Number of rotated files to retain (default: 10)
_LOG_MAX_FILES = int(os.environ.get("ERP_SECURITY_LOG_MAX_FILES", "10"))

# Process-local write locks, keyed by the resolved log path, so that two
# ``JsonlSecurityLog`` instances aimed at the same file still share one lock.
_LOG_WRITE_LOCKS: Dict[str, threading.Lock] = {}
_LOG_WRITE_LOCKS_GUARD = threading.Lock()

# Rotation locks (separate from write locks to avoid deadlock)
_LOG_ROTATION_LOCKS: Dict[str, threading.Lock] = {}
_LOG_ROTATION_LOCKS_GUARD = threading.Lock()


def _rotation_lock_for(path: Path) -> threading.Lock:
    """Return the process-local lock that serialises rotation of *path*."""
    key = str(Path(path).resolve())
    with _LOG_ROTATION_LOCKS_GUARD:
        lock = _LOG_ROTATION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOG_ROTATION_LOCKS[key] = lock
        return lock


def _rotate_security_log(path: Path) -> None:
    """Rotate the security log file if it exceeds the configured maximum size.

    Rotation is performed atomically using os.replace (POSIX) or MoveFileEx
    (Windows) to avoid losing records. The rotation is serialised with a
    process-local lock to prevent concurrent rotations.

    Retention policy: keep the current file plus _LOG_MAX_FILES - 1 rotated files.
    """
    # Check if rotation is needed
    try:
        if path.stat().st_size < _LOG_MAX_FILE_SIZE:
            return
    except OSError:
        return  # File doesn't exist or can't be accessed

    # Acquire rotation lock for this specific log file
    rot_lock = _rotation_lock_for(path)
    with rot_lock:
        # Re-check size after acquiring lock (another thread/process may have rotated)
        try:
            if path.stat().st_size < _LOG_MAX_FILE_SIZE:
                return
        except OSError:
            return

        # Determine rotation sequence numbers
        # Find the highest existing rotation number
        max_num = -1
        for rotated in path.parent.glob(f"{path.name}.*"):
            try:
                suffix = rotated.name[len(path.name) + 1:]
                if suffix.isdigit():
                    max_num = max(max_num, int(suffix))
            except (ValueError, IndexError):
                continue

        # Remove oldest file if we've reached the retention limit
        if max_num >= _LOG_MAX_FILES - 1:
            oldest = path.parent / f"{path.name}.{max_num}"
            try:
                oldest.unlink()
            except OSError:
                pass  # Best effort

        # Rotate existing files down by one number (highest first to avoid overwrites)
        for num in range(max_num, 0, -1):
            src = path.parent / f"{path.name}.{num}"
            dst = path.parent / f"{path.name}.{num + 1}"
            if src.exists():
                try:
                    if os.name == "nt":
                        # Windows: use os.replace which uses MoveFileEx internally
                        # We need to ensure the destination doesn't exist
                        if dst.exists():
                            dst.unlink()
                    os.replace(src, dst)
                except OSError:
                    pass  # Best effort

        # Rotate current log to .1
        dst = path.parent / f"{path.name}.1"
        try:
            if os.name == "nt" and dst.exists():
                dst.unlink()
            os.replace(path, dst)
        except OSError:
            return  # Rotation failed, will retry on next write

        # Create new empty log file with owner-only permissions
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)


def _write_lock_for(path: Path) -> threading.Lock:
    """Return the process-local lock that serialises appends to *path*.

    Keyed by the resolved path rather than by instance so that a second
    instance pointed at the same file - a reloaded application, a second
    FastAPI app, a test fixture - cannot interleave with the first.
    """
    key = str(Path(path).resolve())
    with _LOG_WRITE_LOCKS_GUARD:
        lock = _LOG_WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOG_WRITE_LOCKS[key] = lock
        return lock


def _lock_log_byte(fd: int) -> None:
    """Take a mandatory byte-range lock over byte 0 of the open log file.

    ``msvcrt.locking`` locks relative to the *current file position*, and
    ``O_APPEND`` moves that position to the end of the file after every write,
    so the position is reset before each lock attempt.

    ``LK_NBLCK`` fails immediately when the region is already locked rather
    than blocking, so the acquisition is retried for a bounded time.
    """
    deadline = time.monotonic() + _LOG_LOCK_TIMEOUT_SECONDS
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[union-attr]
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.001)


def _unlock_log_byte(fd: int) -> None:
    """Release the byte-range lock taken by :func:`_lock_log_byte`."""
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[union-attr]


@contextlib.contextmanager
def _cross_process_append_lock(fd: int) -> Iterator[None]:
    """Serialise appends to an open log file across processes.

    POSIX
    -----
    ``O_APPEND`` is atomic there: the kernel positions every write at the
    end of the file and performs the offset update and the write as a single
    operation, so concurrent writers - in the same process or in different
    ones - can never overwrite one another.  Nothing is added on this
    platform.

    Windows
    -------
    The C runtime implements ``O_APPEND`` as "seek to end, then write", which
    is a two-step, non-atomic operation.  Two writers can resolve the same
    end offset and overwrite each other's bytes.  Measured on this
    repository before this lock existed, 176 of 400 concurrently written
    records were silently lost and one record was left corrupt.

    Windows has no atomic-append file mode reachable through ``os.open``;
    the atomic primitive is ``CreateFileW(FILE_APPEND_DATA)``.  The
    equivalent that the standard library does expose is a mandatory
    byte-range lock (``msvcrt.locking`` -> ``LockFile``) over one byte of
    the file.  Locking byte 0 is safe because the append itself always
    targets the end of the file, and a byte-range lock is enforced against
    every other handle, including handles held by the same process.
    """
    if os.name != "nt":
        yield
        return
    _lock_log_byte(fd)
    try:
        yield
    finally:
        _unlock_log_byte(fd)


class JsonlSecurityLog:
    """Durable, append-only sink for security-relevant events.

    Why a separate sink instead of ``audit_events``
    -----------------------------------------------
    ``audit_events.company_id`` is ``NOT NULL`` and every read path is
    tenant-scoped.  A request that failed *authentication* has no trustworthy
    tenant to attribute it to - the caller has not proven who they are, which is
    exactly why it was rejected.  Writing a guessed or defaulted ``company_id``
    would either attribute a foreign tenant's failure to the wrong company or
    fabricate attribution the request never established.

    So this sink deliberately records **no company identifier**.  It is a
    security log, not a financial audit log, and the two must not be conflated:
    an unauthenticated flood is operationally critical and financially
    unattributable at the same time.

    Failure mode
    ------------
    A write failure propagates rather than being swallowed.  Silently dropping
    security-relevant rejection records is the specific gap this class closes:
    without it, a credential-stuffing run leaves no durable trace and the
    operator learns about it only when something else breaks.

    Durability of the append itself
    -------------------------------
    Losing a rejection record is exactly as bad as never writing it, so the
    append is made atomic against concurrent writers on every platform.  The
    platform differences are real and are not papered over:

    * POSIX: ``O_APPEND`` already guarantees that concurrent writers cannot
      overwrite one another, because the kernel performs the seek-to-end and
      the write as one atomic operation.
    * Windows: the C runtime implements ``O_APPEND`` as a non-atomic
      seek-then-write, so writers really do clobber each other.  A
      process-local lock plus a mandatory byte-range lock over the file is
      therefore taken around every write.  See
      :func:`_cross_process_append_lock`.

    The guarantee this class actually offers is: *every record handed to it
    is written exactly once, in full, and is not interleaved with another
    record* - for threads and for processes, on every supported platform.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            # Create with owner-only permissions from the outset, never a
            # world-readable default that is tightened afterwards.
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        self._lock = _write_lock_for(self.path)

    def __call__(self, entry: Dict[str, Any]) -> None:
        line = json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
        payload = line.encode("utf-8")
        # O_APPEND: the kernel positions each write at the end of the file, so
        # concurrent workers cannot overwrite one another's records.  Lines are
        # kept well under PIPE_BUF, which makes each write effectively atomic.
        #
        # That sentence only holds on POSIX.  On Windows the same flag is a
        # non-atomic seek-then-write, so the append is additionally serialised
        # by a process-local lock and by a mandatory byte-range lock on the
        # file.  Without them, concurrent writers silently lost records: 176
        # of 400 were lost on this repository before the fix.
        with self._lock:
            fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                with _cross_process_append_lock(fd):
                    written = os.write(fd, payload)
            finally:
                os.close(fd)
            if written != len(payload):
                raise OSError(
                    f"Security log write to {self.path} was short "
                    f"({written} of {len(payload)} bytes); the record may be truncated."
                )
            # Check if rotation is needed after successful write (within the lock
            # to avoid race with concurrent writers).
            _rotate_security_log(self.path)


class StructuredRedactingLogger:
    """Structured JSON logger that redacts credentials, tokens, bank RIBs, and invoice text.

    ``sink`` receives every record so security-relevant events can outlive the
    process.  Without one, ``records`` is an in-memory list and everything
    logged is lost on restart.
    """

    def __init__(self, sink: Optional[Any] = None) -> None:
        self.records: List[Dict[str, Any]] = []
        self._sink = sink

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
        if self._sink is not None:
            # Deliberately not wrapped in a try/except: a sink failure must not
            # be swallowed, or the durable log silently stops being durable -
            # which is the exact gap this closes.
            self._sink(entry)
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
                # Phase 40 (F-04): an invoice can say which stamp-duty rule
                # produced its tax, or say that it cannot. It is never inferred
                # from the configuration in force today.
                result["timbre_provenance"] = describe_invoice_timbre_provenance(inv)

    if denial is not None:
        raise denial
    assert result is not None
    return result


def read_source_document_content_secure(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    requesting_user_id: int,
    document_id: str,
) -> "Tuple[bytes, Dict[str, Any]]":
    """Read decrypted source bytes plus the tenant-scoped metadata row.

    Company-isolation and audit logging are identical to the file read:
    denials are audited and committed before the error is raised so the
    blocked attempt survives. The metadata row (filename, MIME type) lets a
    preview surface label what it shows without a second tenant check.
    """
    denial: Optional[Exception] = None
    raw_bytes: Optional[bytes] = None
    doc_meta: Optional[Dict[str, Any]] = None

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
                doc_meta = dict(doc)

    if denial is not None:
        raise denial
    assert raw_bytes is not None and doc_meta is not None
    return raw_bytes, doc_meta


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
    raw_bytes, _doc_meta = read_source_document_content_secure(
        db=db,
        vault=vault,
        requesting_user_id=requesting_user_id,
        document_id=document_id,
    )
    return raw_bytes


def list_supplier_invoices_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    *,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List supplier invoices under the same RBAC as the per-invoice read.

    The company scope is derived from the verified ``users`` row, never from a
    caller argument, and the warehouse-role denial is audited (and committed)
    exactly like the single-invoice read before the error is raised - a list
    view must not become a way to read financial data a detail view denies.
    """
    denial: Optional[Exception] = None
    scoped_company_id: Optional[int] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        elif user["role"] == "warehouse":
            db.append_audit_event(
                conn=conn,
                company_id=user["company_id"],
                actor_user_id=requesting_user_id,
                action_type="SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED",
                target_model="supplier_invoices",
                target_id="list",
                after_state={"role": user["role"]},
            )
            denial = AuthorizationError("Warehouse role is not permitted to read financial invoices.")
        else:
            scoped_company_id = int(user["company_id"])

    if denial is not None:
        raise denial
    assert scoped_company_id is not None
    return db.list_supplier_invoices(scoped_company_id, limit=limit)


def list_extracted_fields_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    document_id: str,
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """OCR field rows of one document, under the document-file RBAC.

    Source documents are the warehouse domain, so any active user of the
    tenant may review them; the company scope is derived from the verified
    ``users`` row, and a cross-company attempt is audited (and committed)
    exactly like the per-document file read before the error is raised.
    The limit is clamped up front through the same single-source helper
    every read model uses, so a bad limit stays a contract (422) error at
    the API layer and can never be mistaken for the document's 404.
    """
    db._read_limit(limit, "list_extracted_fields")
    denial: Optional[Exception] = None
    scoped_company_id: Optional[int] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        else:
            doc = conn.execute(
                "SELECT company_id FROM source_documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not doc:
                denial = ValueError("Source document not found.")
            elif doc["company_id"] != user["company_id"]:
                db.append_audit_event(
                    conn=conn,
                    company_id=doc["company_id"],
                    actor_user_id=requesting_user_id,
                    action_type="SECURITY_CROSS_COMPANY_READ_BLOCKED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={
                        "attacker_company_id": user["company_id"],
                        "target_company_id": doc["company_id"],
                    },
                )
                denial = TenantIsolationError("Cross-company read access denied.")
            else:
                scoped_company_id = int(user["company_id"])

    if denial is not None:
        raise denial
    assert scoped_company_id is not None
    return db.list_extracted_fields(scoped_company_id, document_id, limit=limit)


def evaluate_document_review_policy_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    document_id: str,
) -> Dict[str, Any]:
    """The Phase 29 review-policy verdict for one document, under document RBAC.

    Everything the verdict depends on (document row, extracted fields, review
    decisions) is read inside one transaction scoped by the verified
    credential — the same evidence the invoice-approval gate will later judge,
    so a reviewer sees exactly what will be enforced. A cross-company attempt
    is audited (and committed) exactly like the per-document file read before
    the error is raised.
    """
    denial: Optional[Exception] = None
    evaluation: Optional[Dict[str, Any]] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        else:
            doc = conn.execute(
                "SELECT company_id FROM source_documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not doc:
                denial = ValueError("Source document not found.")
            elif doc["company_id"] != user["company_id"]:
                db.append_audit_event(
                    conn=conn,
                    company_id=doc["company_id"],
                    actor_user_id=requesting_user_id,
                    action_type="SECURITY_CROSS_COMPANY_READ_BLOCKED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={
                        "attacker_company_id": user["company_id"],
                        "target_company_id": doc["company_id"],
                    },
                )
                denial = TenantIsolationError("Cross-company read access denied.")
            else:
                company_id = int(user["company_id"])
                doc_row = dict(
                    conn.execute(
                        "SELECT * FROM source_documents WHERE id = ?", (document_id,)
                    ).fetchone()
                )
                fields = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ?",
                        (company_id, document_id),
                    ).fetchall()
                ]
                decisions = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM ocr_field_reviews WHERE company_id = ? AND document_id = ?",
                        (company_id, document_id),
                    ).fetchall()
                ]
                evaluation = evaluate_document_review_policy(doc_row, fields, decisions)

    if denial is not None:
        raise denial
    assert evaluation is not None
    return evaluation


def list_partners_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Partner master data for the requester's own company, any active user.

    Master data is operational, not ledger-financial, so no role is denied;
    the scope still comes only from the verified credential.
    """
    denial: Optional[Exception] = None
    scoped_company_id: Optional[int] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        else:
            scoped_company_id = int(user["company_id"])

    if denial is not None:
        raise denial
    assert scoped_company_id is not None
    return db.list_partners(scoped_company_id, limit=limit)


def list_stock_quants_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """On-hand stock for the requester's own company, any active user.

    Stock is the warehouse domain - the one role that must never be blocked
    from it - so any active user of the tenant may list it.
    """
    denial: Optional[Exception] = None
    scoped_company_id: Optional[int] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        else:
            scoped_company_id = int(user["company_id"])

    if denial is not None:
        raise denial
    assert scoped_company_id is not None
    return db.list_stock_quants(scoped_company_id, limit=limit)


def _require_user_admin(db: ERPDatabase, requesting_user_id: int) -> Dict[str, Any]:
    """Guard shared by every user-administration operation: active admin only.

    A non-admin attempt is audited and committed before the refusal is
    raised, so the blocked attempt survives in the append-only chain exactly
    like every other authorization denial (the transaction context manager
    rolls back on an in-scope raise, hence the post-commit raise here).
    """
    denial: Optional[AuthorizationError] = None
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            raise AuthorizationError("User is inactive or revoked.")
        if user["role"] != "admin":
            db.append_audit_event(
                conn=conn,
                company_id=user["company_id"],
                actor_user_id=requesting_user_id,
                action_type="SECURITY_USER_ADMIN_ACCESS_BLOCKED",
                target_model="users",
                target_id="list",
                after_state={"role": user["role"]},
            )
            denial = AuthorizationError("User administration is restricted to administrators.")
    if denial is not None:
        raise denial
    return dict(user)


def list_users_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    *,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """User accounts of the requester's company, administrators only.

    The scope is derived from the verified credential; the read model itself
    never exposes credential material (password or token hashes).
    """
    admin = _require_user_admin(db, requesting_user_id)
    return db.list_users(int(admin["company_id"]), limit=limit)


def create_user_admin_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    auth_manager: Any,
    *,
    username: str,
    password: str,
    role: str,
    branch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a user in the admin's own company, under the admin guard.

    Delegates the verified mechanics (role validation, PBKDF2 hashing, the
    ``AUTH_USER_CREATED`` audit event with the acting admin as ``actor``) to
    :class:`~dz_erp.auth.AuthManager.create_user` — no parallel
    implementation of credential handling.
    """
    admin = _require_user_admin(db, requesting_user_id)
    user = auth_manager.create_user(
        username=username,
        password=password,
        role=role,
        company_id=int(admin["company_id"]),
        branch_id=branch_id,
        actor_user_id=int(admin["id"]),
    )
    return user


def deactivate_user_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    target_user_id: int,
) -> Dict[str, Any]:
    """Deactivate a user of the admin's own company, administrators only.

    Refuses to deactivate the acting admin themself: the last active admin
    must never be able to lock everyone out of an installed workstation (and
    an admin deactivating themself mid-session is always that case here).
    The deactivated account's tokens are rejected by ``authenticate`` from
    the moment ``is_active`` flips (``AUTH_USER_INACTIVE_REJECTED``), so the
    change takes effect for live sessions immediately. The deactivation is
    itself an audited event.
    """
    admin = _require_user_admin(db, requesting_user_id)
    if target_user_id == int(admin["id"]):
        raise ValueError("An administrator cannot deactivate their own account.")

    with db.transaction() as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE id = ? AND company_id = ?",
            (target_user_id, int(admin["company_id"])),
        ).fetchone()
        if not target:
            raise TenantIsolationError("User not found in target company.")
        if not target["is_active"]:
            raise ValueError(f"User '{target['username']}' is already deactivated.")
        conn.execute(
            "UPDATE users SET is_active = 0 WHERE id = ? AND company_id = ?",
            (target_user_id, int(admin["company_id"])),
        )
        db.append_audit_event(
            conn=conn,
            company_id=int(admin["company_id"]),
            actor_user_id=int(admin["id"]),
            action_type="AUTH_USER_DEACTIVATED",
            target_model="users",
            target_id=str(target_user_id),
            before_state={"is_active": 1, "username": target["username"], "role": target["role"]},
            after_state={"is_active": 0},
        )
    return {"user_id": target_user_id, "username": target["username"], "is_active": 0}


def trial_balance_secure(db: ERPDatabase, requesting_user_id: int) -> Dict[str, Any]:
    """Trial balance under the same RBAC as invoice reads.

    The ledger is financial data: the warehouse role is denied with an audited
    event, and the report is computed for the requester's own company only.
    """
    denial: Optional[Exception] = None
    scoped_company_id: Optional[int] = None

    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            denial = AuthorizationError("User is inactive or revoked.")
        elif user["role"] == "warehouse":
            db.append_audit_event(
                conn=conn,
                company_id=user["company_id"],
                actor_user_id=requesting_user_id,
                action_type="SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED",
                target_model="journal_entries",
                target_id="trial_balance",
                after_state={"role": user["role"]},
            )
            denial = AuthorizationError("Warehouse role is not permitted to read the ledger.")
        else:
            scoped_company_id = int(user["company_id"])

    if denial is not None:
        raise denial
    assert scoped_company_id is not None
    return db.trial_balance(scoped_company_id)


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

        # A restore must reproduce *every* key version the archived ciphertext
        # and audit rows depend on - not just the currently active one. Rotating
        # after this backup was taken moves the active version on, and a
        # manifest that only carried "the key" would then restore artifacts it
        # could no longer read.
        vault_ring = vault.keyring
        audit_ring = db.audit_keyring
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "db_sha256": db_sha256,
            "vault_file_hashes": vault_hashes,
            # Human-readable requirement statement: which versions, and which
            # one is active. Contains no key material, so it is safe to surface
            # in an operator-facing report or restore preflight check.
            "vault_key_versions": vault_ring.summary(),
            "audit_key_versions": audit_ring.summary(),
            # Version-1 material is what the legacy single-key fields mean; see
            # the note on the key registry in dz_erp/key_rotation.py.
            "vault_encryption_key_b64": base64.b64encode(vault.encryption_key).decode("ascii"),
            # The audit chain is only verifiable with the keys that wrote it, so
            # a backup that cannot restore them cannot prove its own integrity.
            # The rings travel inside the Fernet-encrypted archive, i.e. under
            # exactly the same protection as the vault key above; nothing here
            # is exposed in clear.
            "audit_hmac_secret_b64": base64.b64encode(db._hmac_secret).decode("ascii"),
            "vault_keyring_b64": base64.b64encode(
                json.dumps(vault_ring.to_dict(), sort_keys=True).encode("utf-8")
            ).decode("ascii"),
            "audit_keyring_b64": base64.b64encode(
                json.dumps(audit_ring.to_dict(), sort_keys=True).encode("utf-8")
            ).decode("ascii"),
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
            # Version numbers only - no key material. An operator can tell from
            # the backup alone which keys a restore will require.
            "vault_key_versions": vault_ring.summary()["versions"],
            "audit_key_versions": audit_ring.summary()["versions"],
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _keyring_from_manifest(
    manifest: Dict[str, Any],
    ring_field: str,
    legacy_field: str,
    *,
    kind: str,
    explicit: Optional[bytes],
) -> KeyRing:
    """Rebuild a key registry from a backup, tolerating pre-registry archives.

    A backup written before versioning existed carries one key and no registry.
    That key is version 1 by definition, so those archives stay restorable -
    they are simply never rotatable until the next backup is taken.
    """
    encoded = manifest.get(ring_field)
    if encoded:
        try:
            payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise FinancialIntegrityError(f"Backup field {ring_field} is not a valid key registry.") from exc
        ring = KeyRing.from_dict(payload)
        if ring.kind != kind:
            raise FinancialIntegrityError(
                f"Backup field {ring_field} holds a {ring.kind!r} key registry but a {kind!r} registry is required."
            )
        if explicit is not None and bytes(explicit) != ring.active_material:
            raise KeyConfigurationError(
                f"The supplied {kind} key is not the active key recorded in the backup; refusing to guess."
            )
        # Preflight the environment too, and do it here rather than in the
        # constructors below: those run *after* the restore has already written
        # key files into the target directory, so a configuration conflict would
        # leave a half-restored directory behind.
        env_var = VAULT_KEY_ENV if kind == "vault" else AUDIT_KEY_ENV
        from_env = os.environ.get(env_var)
        if from_env and from_env.encode("utf-8") != ring.active_material:
            raise KeyConfigurationError(
                f"{env_var} disagrees with the active {kind} key recorded in the backup. "
                "Refusing to restore an archive that would not be readable by this configuration."
            )
        return ring

    if manifest.get(legacy_field):
        legacy = base64.b64decode(manifest[legacy_field])
    elif explicit is not None:
        legacy = bytes(explicit)
    else:
        raise FinancialIntegrityError(
            f"The backup carries no {kind} key material, so its encrypted artefacts could not be restored."
        )
    return KeyRing.bootstrap(kind, legacy)


def _write_key_file(key_path: Path, secret: bytes) -> None:
    """Write a secret to disk with owner-only permissions, replacing any stale value."""
    from .key_provider import FileKeyProvider

    FileKeyProvider(key_path).write(secret)


def _lstat_restore_path(path: Path) -> Optional[os.stat_result]:
    """Inspect an existing restore path component without following links.

    Path.is_symlink does not cover every Windows reparse point, and checking
    only an archive's members does not protect against a hard-linked file already
    present in the destination. Return lstat metadata so the caller can also
    refuse to truncate a regular file whose inode is shared outside the target.
    """
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FinancialIntegrityError(
            f"Cannot safely inspect restore target path component {path}: {exc}"
        ) from exc

    is_reparse_point = os.name == "nt" and bool(
        int(getattr(metadata, "st_file_attributes", 0)) & 0x0400  # FILE_ATTRIBUTE_REPARSE_POINT
    )
    if stat.S_ISLNK(metadata.st_mode) or is_reparse_point:
        raise FinancialIntegrityError(
            f"Restore target contains a symbolic link or reparse point: {path}"
        )
    return metadata


#: The interpreter families whose ``tarfile`` extraction filter is known to be
#: complete enough for a safe restore, and the first patched release of each.
#: 3.11 below 3.11.17 and 3.12 below 3.12.10 carry the link-fallback and
#: leave-then-return bypasses; 3.13+ is not supported by the pinned OCR
#: dependency, so it is deliberately excluded rather than silently allowed.
SAFE_RESTORE_MINIMUMS: Dict[Tuple[int, int], Tuple[int, int, int]] = {
    (3, 11): (3, 11, 17),
    (3, 12): (3, 12, 10),
}
SAFE_RESTORE_MAX_EXCLUSIVE: Tuple[int, int, int] = (3, 13, 0)
SAFE_RESTORE_UNSUPPORTED_MESSAGE = (
    "Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13); "
    "unsafe archive member types and paths are rejected."
)


def safe_restore_supported(version_info: Any = None) -> bool:
    """Is a *safe* backup restore possible on this interpreter?

    The one predicate. Both the library and every test that gates a
    backup/restore assertion call this, so the matrix cannot drift: before
    Phase 45 the tests carried three hand-written copies of the rule, and one of
    them (``>= (3, 11, 17) and < (3, 13)``) wrongly accepted 3.12.0-3.12.9 —
    versions on which the restore correctly refuses. That copy was skipped by CI
    only because CI resolves the newest 3.12.
    """
    version = tuple((version_info if version_info is not None else sys.version_info))[:3]
    if version >= SAFE_RESTORE_MAX_EXCLUSIVE:
        return False
    minimum = SAFE_RESTORE_MINIMUMS.get(tuple(version[:2]))
    if minimum is None:
        return False
    return version >= minimum


def restore_and_verify_backup(
    backup_path: Path,
    backup_encryption_key: bytes,
    restore_target_dir: Path,
    hmac_secret: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Decrypt, unpack, and verify a backup archive in an isolated target directory."""
    restore_target_dir = Path(restore_target_dir)
    _lstat_restore_path(restore_target_dir)
    restore_target_dir = restore_target_dir.resolve()
    target_existed = restore_target_dir.exists()
    if target_existed and not restore_target_dir.is_dir():
        raise FinancialIntegrityError(f"Restore target is not a directory: {restore_target_dir}")

    # The data filter is used as one boundary, not the only boundary.
    # Python 3.11 releases after 3.11.17 are source-only; Python 3.12.15 is also
    # source-only, while 3.12.10 is the last Windows binary installer. For 3.12.10+
    # we therefore independently reject every link/special member and every
    # non-canonical/escaping path before extraction; this blocks the tarfile
    # link-fallback and leave-then-return path bypasses even on older filter code.
    if not safe_restore_supported():
        raise FinancialIntegrityError(SAFE_RESTORE_UNSUPPORTED_MESSAGE)
    safe_data_filter = getattr(tarfile, "data_filter", None)
    if safe_data_filter is None:
        raise FinancialIntegrityError(
            "Safe backup extraction is unavailable in this Python runtime. "
            "Use a supported, security-patched Python 3.11 or 3.12 release."
        )

    fernet = Fernet(backup_encryption_key)
    decrypted_tar = fernet.decrypt(Path(backup_path).read_bytes())

    try:
        with tarfile.open(fileobj=io.BytesIO(decrypted_tar), mode="r:gz") as tar:
            members = tar.getmembers()
            # Preflight every member before writing any of them. This prevents a
            # late symlink/filter failure from leaving earlier archive entries in
            # the target directory.
            root = restore_target_dir.resolve()
            archive_types: Dict[tuple[str, ...], str] = {}
            for member in members:
                name = member.name
                # Tar archives use POSIX separators. Reject Windows separators,
                # drive/UNC forms, ADS colons, NULs, empty components, and dot
                # segments before any member reaches the filesystem.
                if not name or "\x00" in name or "\\" in name:
                    raise FinancialIntegrityError(f"Unsafe tar member path detected: {name!r}")
                posix_name = PurePosixPath(name)
                windows_name = PureWindowsPath(name)
                if (
                    posix_name.is_absolute()
                    or windows_name.is_absolute()
                    or windows_name.drive
                    or name.startswith("/")
                ):
                    raise FinancialIntegrityError(f"Unsafe tar member path detected: {name!r}")
                canonical_name = name[:-1] if member.isdir() and name.endswith("/") else name
                parts = canonical_name.split("/")
                if (
                    not canonical_name
                    or any(part in ("", ".", "..") for part in parts)
                    or any(":" in part for part in parts)
                ):
                    raise FinancialIntegrityError(f"Unsafe tar member path detected: {name!r}")

                # Backups generated by this application need only ordinary
                # directories and regular files. Reject symlinks, hardlinks,
                # devices, FIFOs, and every other special type; this removes
                # link-target and link-fallback attack surfaces entirely.
                if not (member.isdir() or member.isfile()):
                    raise FinancialIntegrityError(
                        f"Backup archive contains a disallowed link or special member: {name!r}"
                    )

                # Reject duplicate names and file/directory prefix conflicts
                # inside the archive before creating any filesystem entries.
                key = tuple(parts)
                if key in archive_types:
                    raise FinancialIntegrityError(f"Duplicate tar member path detected: {name!r}")
                for prefix_length in range(1, len(key)):
                    if archive_types.get(key[:prefix_length]) == "file":
                        raise FinancialIntegrityError(
                            f"Tar member has a regular file as a parent path: {name!r}"
                        )
                if member.isfile() and any(
                    existing[: len(key)] == key for existing in archive_types
                ):
                    raise FinancialIntegrityError(
                        f"Tar member would replace a parent directory with a file: {name!r}"
                    )
                archive_types[key] = "directory" if member.isdir() else "file"

                # Existing symlinks within the destination can redirect a
                # seemingly safe child path. Check the whole existing prefix as
                # well as the final target before writing anything.
                current = root
                for index, part in enumerate(parts):
                    current = current / part
                    current_info = _lstat_restore_path(current)
                    if current_info is None:
                        continue

                    if index < len(parts) - 1:
                        if not stat.S_ISDIR(current_info.st_mode):
                            raise FinancialIntegrityError(
                                f"Restore path component is not a directory: {current}"
                            )
                        continue

                    if member.isdir():
                        if not stat.S_ISDIR(current_info.st_mode):
                            raise FinancialIntegrityError(
                                f"Directory archive member conflicts with existing file: {current}"
                            )
                    elif member.isfile():
                        if stat.S_ISDIR(current_info.st_mode):
                            raise FinancialIntegrityError(
                                f"File archive member conflicts with existing directory: {current}"
                            )
                        if not stat.S_ISREG(current_info.st_mode):
                            raise FinancialIntegrityError(
                                f"Refusing to overwrite a non-regular restore target: {current}"
                            )
                        if int(getattr(current_info, "st_nlink", 1)) > 1:
                            raise FinancialIntegrityError(
                                f"Refusing to overwrite a pre-existing hard-linked file in restore target: {current}"
                            )
                try:
                    filtered = safe_data_filter(member, str(root))
                    if filtered is None:
                        raise FinancialIntegrityError(
                            f"Backup archive member was refused by the safe extraction filter: {name!r}"
                        )
                except FinancialIntegrityError:
                    raise
                except (tarfile.TarError, OSError) as exc:
                    raise FinancialIntegrityError(
                        f"Backup archive contains an unsafe tar entry; extraction was refused: {exc}"
                    ) from exc

            restore_target_dir.mkdir(parents=True, exist_ok=True)
            tar.extractall(path=restore_target_dir, filter=safe_data_filter)
    except FinancialIntegrityError:
        raise
    except (tarfile.TarError, OSError) as exc:
        if not target_existed:
            shutil.rmtree(restore_target_dir, ignore_errors=True)
        raise FinancialIntegrityError(
            f"Backup archive contains an unsafe or invalid tar entry; extraction was refused: {exc}"
        ) from exc

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

    # Restore every key version the archive needs.  An explicit secret still
    # wins, but it can only be the *active* key of the archived registry -
    # historical versions must come from the archive itself or the restore is
    # incomplete by construction.
    audit_ring = _keyring_from_manifest(
        manifest, "audit_keyring_b64", "audit_hmac_secret_b64", kind="audit", explicit=hmac_secret
    )
    vault_ring = _keyring_from_manifest(
        manifest, "vault_keyring_b64", "vault_encryption_key_b64", kind="vault", explicit=None
    )

    # Persist the registries and the version-1 key files beside the restored
    # artefacts.  Without this the restore only works through the objects
    # returned here: reopening the restored database in a fresh process - which
    # is the entire point of a restore - resolves the one legacy key, so every
    # audit hash reads as EVENT_HASH_TAMPERED and every stored document raises
    # InvalidToken.  The material is already inside the Fernet-encrypted
    # archive, so writing it to the freshly-restored 0600 directory adds no new
    # exposure.
    audit_keyring_path = Path(str(restored_db_path) + AUDIT_KEYRING_SUFFIX)
    audit_ring.save(audit_keyring_path)
    vault_ring.save(restored_vault_dir.parent / (restored_vault_dir.name + VAULT_KEYRING_SUFFIX))

    # The legacy single-key files keep meaning "version 1", so a deployment
    # whose registry is later lost still resolves the historical key rather
    # than a newer one that cannot read the oldest ciphertext.
    _write_key_file(
        Path(str(restored_db_path) + AUDIT_KEY_SUFFIX),
        audit_ring.material_for(1) if audit_ring.has_version(1) else audit_ring.active_material,
    )
    _write_key_file(
        restored_vault_dir.parent / (restored_vault_dir.name + VAULT_KEY_SUFFIX),
        vault_ring.material_for(1) if vault_ring.has_version(1) else vault_ring.active_material,
    )

    restored_db = ERPDatabase(db_path=str(restored_db_path), hmac_secret=audit_ring.active_material)
    restored_vault = PrivateDocumentVault(vault_root=restored_vault_dir, keyring=vault_ring)

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
        unavailable = sorted(
            {v for r in audit_verifications.values() for v in r.get("missing_key_versions", [])}
        )
        if unavailable:
            # A lost key is not evidence of tampering. Reporting it as such
            # would send the operator hunting for an intrusion that never
            # happened while the real cause (an unrestored key version) went
            # unmentioned.
            raise AuditKeyUnavailableError(
                f"The restored audit chain needs key version(s) {unavailable} that are not available. "
                "Restore those key versions before treating the chain as verified."
            )
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
