# SPDX-License-Identifier: LGPL-3.0-only
"""Tamper-evident ledger for privileged key operations.

Trust boundary and why this is not built on the audit chain
============================================================
The obvious place to record a key rotation is ``audit_events``. It is the wrong
place, for three structural reasons:

1. **Circular dependency.** ``audit_events`` is integrity-protected by the very
   HMAC key that an audit rotation replaces. If the record of a rotation lives
   inside the chain that the rotation governs, then the moment the chain cannot
   be verified - which is exactly when an operator most needs the history - the
   rotation history becomes unreadable too. The same trap appears in the refusal
   path: a rotation is refused when the chain does not verify, and that refusal
   could not then be recorded.
2. **No tenant.** ``audit_events.company_id`` is ``NOT NULL`` and every read path
   is tenant-scoped. Key rotation is deployment-global: it belongs to no single
   company. Attributing it to an arbitrary tenant would fabricate an association
   that does not exist, and would hide a global event inside one tenant's chain.
3. **Scope of compromise.** Losing the audit key must not blind the record of
   the operations performed on that key.

So rotation records live in a **separate append-only ledger with its own
independent key material**, supplied by the same ``KeyRing`` machinery under a
distinct kind (``"rotation"``). The ledger key is never the vault key and never
the audit signing key; rotating either store therefore leaves the ledger
verifiable, and losing either store's key leaves the ledger readable.

What this design can and cannot detect
--------------------------------------
The ledger is an HMAC-SHA256 hash chain::

    record_hash = HMAC(ledger_key, seq | recorded_at | operation_id | operation
                       | phase | actor | store | versions | counts | integrity
                       | prev_hash)

* **Detects:** modification of any field, deletion of an interior record,
  reordering, insertion, and a broken chain start (a ledger that does not begin
  at sequence 1 and a zero hash).
* **Does NOT detect on its own:** removal of *trailing* records. No hash chain
  can - there is no later record to point at the missing ones. That is why the
  ledger exposes ``export_checkpoint()`` and ``verify_against_checkpoint()``: an
  operator stores the head hash and count off-host, and a later verification can
  then prove that the tail has not been truncated. Without an external
  checkpoint, tail truncation is invisible, and this document says so instead of
  implying full tamper-proofness.
* **Does NOT protect against:** an attacker who holds the ledger key. It is a
  local key like the others (see the custody assessment), not an HSM.

What is recorded
----------------
Operation identifier; operation type; phase; store; key versions only (never
material); counts of attempted/completed/failed/skipped records; integrity
verification outcome; actor identity where it is reliably available (OS user,
host, pid - recorded because a privileged operation without an accountable actor
is not auditable); and interruption/recovery phases. No plaintext, no customer
identifiers, no key material.

Failure behaviour
-----------------
A rotation whose ledger record cannot be written is **not** reported as
successful. ``begin()`` fails before the operation mutates anything, and
``finish()`` failing after a successful migration raises ``RotationLedgerError``
that names the counts, so the operator learns both that the data moved and that
the run is unauditable - rather than receiving a silent success.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .key_provider import FileKeyProvider
from .key_rotation import (
    KeyConfigurationError,
    KeyRing,
    KeyVersionUnavailableError,
    atomic_write_bytes,
    rotation_lock,
)

LEDGER_FORMAT = "dz-erp-rotation-ledger"
LEDGER_SCHEMA_VERSION = 1
LEDGER_GENESIS_HASH = "0" * 64

LEDGER_KEYRING_SUFFIX = ".rotation.keys.json"
LEDGER_KEY_SUFFIX = ".rotation.key"
LEDGER_LOCK_SUFFIX = ".rotation.lock"

# Phases that describe a finished operation. RECOVERED closes an operation that
# a crash left open, so a recovered interruption stops alerting.
TERMINAL_PHASES = frozenset({"COMPLETED", "FAILED", "REFUSED", "RECOVERED"})


class RotationLedgerError(RuntimeError):
    """The ledger could not be written, or does not verify."""


class RotationLedgerTamperError(RotationLedgerError):
    """The ledger failed integrity verification."""


class RotationLedgerCheckpointError(RotationLedgerError):
    """A checkpoint document is missing, malformed, or from an incompatible version.

    Distinct from truncation and from tampering: this is an *artifact* problem.
    An unreadable or empty checkpoint file used to be treated as "nothing to
    compare", which reported success - the operator believed truncation detection
    was in force when it was not.
    """


class RotationLedgerTruncatedError(RotationLedgerError):
    """The ledger is shorter than an externally retained checkpoint."""

    def __init__(self, message: str, *, expected_count: int, actual_count: int) -> None:
        super().__init__(message)
        self.expected_count = expected_count
        self.actual_count = actual_count


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(payload: Dict[str, Any]) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, UTF-8.

    The MAC is computed over this encoding so that a stored record cannot be
    changed by reformatting JSON (key order, spacing, escaping) without breaking
    verification.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def resolve_actor(source: str = "cli") -> Dict[str, Any]:
    """Best-effort, non-secret operator identity.

    Recorded because "which privileged operation ran" is only half the question;
    "on whose authority" is the other half. It deliberately contains no
    credential, token or personal data beyond the OS account name that performed
    the action.
    """
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return {"user": user, "host": host, "pid": os.getpid(), "source": source}


@dataclass
class LedgerRecord:
    seq: int
    recorded_at: str
    operation_id: str
    operation: str
    phase: str
    store: str
    actor: Dict[str, Any]
    from_version: Optional[int] = None
    to_version: Optional[int] = None
    retained_versions: List[int] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    integrity: Dict[str, Any] = field(default_factory=dict)
    detail: Dict[str, Any] = field(default_factory=dict)
    key_version: int = 1
    prev_hash: str = LEDGER_GENESIS_HASH
    record_hash: str = ""

    def body(self) -> Dict[str, Any]:
        """Every signed field, excluding the MAC itself."""
        return {
            "seq": self.seq,
            "recorded_at": self.recorded_at,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "phase": self.phase,
            "store": self.store,
            "actor": self.actor,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "retained_versions": self.retained_versions,
            "counts": self.counts,
            "integrity": self.integrity,
            "detail": self.detail,
            "key_version": self.key_version,
            "prev_hash": self.prev_hash,
        }

    def to_dict(self) -> Dict[str, Any]:
        body = self.body()
        body["record_hash"] = self.record_hash
        return body


CHECKPOINT_REQUIRED_FIELDS = ("format", "schema", "record_count", "head_hash")


def validate_checkpoint(checkpoint: Any) -> Dict[str, Any]:
    """Validate a checkpoint document, or raise ``RotationLedgerCheckpointError``.

    Fail-closed on purpose. Every one of these states previously passed silently:

    * an empty or missing document (nothing to compare -> "valid");
    * a document from a different ledger format or a newer schema;
    * a non-integer or negative record count (which crashed with ValueError or
      IndexError instead of saying what was wrong);
    * a head hash that is not a 64-hex digest.

    A checkpoint exists to be trustworthy evidence of a past state, so an
    untrustworthy one must stop the verification rather than be skipped.
    """
    if not isinstance(checkpoint, dict) or not checkpoint:
        raise RotationLedgerCheckpointError("The checkpoint document is empty or not a mapping.")

    missing = [field for field in CHECKPOINT_REQUIRED_FIELDS if checkpoint.get(field) in (None, "")]
    if missing:
        raise RotationLedgerCheckpointError(
            f"The checkpoint is missing required field(s): {', '.join(missing)}. "
            "An incomplete checkpoint cannot prove anything about the ledger's past state."
        )

    if checkpoint["format"] != LEDGER_FORMAT:
        raise RotationLedgerCheckpointError(
            f"The checkpoint format is {checkpoint['format']!r}, expected {LEDGER_FORMAT!r}. "
            "It was produced by a different ledger and must not be compared with this one."
        )

    schema = checkpoint["schema"]
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise RotationLedgerCheckpointError(f"The checkpoint schema {schema!r} is not an integer.")
    if schema != LEDGER_SCHEMA_VERSION:
        raise RotationLedgerCheckpointError(
            f"The checkpoint schema is {schema}, but this build reads schema {LEDGER_SCHEMA_VERSION}. "
            "Re-export the checkpoint with the current version rather than guessing at compatibility."
        )

    count = checkpoint["record_count"]
    if not isinstance(count, int) or isinstance(count, bool):
        raise RotationLedgerCheckpointError(f"The checkpoint record_count {count!r} is not an integer.")
    if count < 0:
        raise RotationLedgerCheckpointError(f"The checkpoint record_count {count} is negative.")

    head = checkpoint["head_hash"]
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{64}", head):
        raise RotationLedgerCheckpointError(
            "The checkpoint head_hash is not a 64-character lowercase hex digest."
        )
    if count == 0 and head != LEDGER_GENESIS_HASH:
        raise RotationLedgerCheckpointError(
            "A checkpoint with zero records must carry the genesis hash."
        )
    return checkpoint


def load_checkpoint(path: Path) -> Dict[str, Any]:
    """Read and validate a checkpoint file. Never returns an unvalidated document."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RotationLedgerCheckpointError(
            f"The checkpoint {path} does not exist. Without it, removal of trailing ledger "
            "records cannot be detected."
        ) from exc
    except OSError as exc:
        raise RotationLedgerCheckpointError(f"The checkpoint {path} cannot be read: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RotationLedgerCheckpointError(
            f"The checkpoint {path} is not valid JSON: {exc.msg} at line {exc.lineno}."
        ) from exc
    validate_checkpoint(document)
    document.setdefault("path", str(path))
    return document


class RotationLedger:
    """Append-only, HMAC-chained record of privileged key operations."""

    def __init__(self, path: Path, keyring: KeyRing, *, keyring_path: Optional[Path] = None) -> None:
        self.path = Path(path)
        self.keyring = keyring
        self.keyring_path = keyring_path
        self.lock_path = self.path.parent / (self.path.name + LEDGER_LOCK_SUFFIX)
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def open(
        cls,
        path: Path,
        *,
        keyring_path: Optional[Path] = None,
        explicit_key: Optional[bytes] = None,
        create: bool = True,
    ) -> "RotationLedger":
        """Open (or initialise) the ledger beside its own key registry.

        Deliberately independent of the vault and audit key material: this is the
        property that stops a rotation from hiding its own record.
        """
        path = Path(path)
        keyring_path = Path(keyring_path) if keyring_path else path.with_name(path.name + LEDGER_KEYRING_SUFFIX)
        key_file = path.with_name(path.stem + LEDGER_KEY_SUFFIX)

        if keyring_path.exists():
            ring = KeyRing.load(keyring_path, "rotation")
        elif key_file.exists():
            ring = KeyRing.bootstrap("rotation", FileKeyProvider(key_file).read())
        elif explicit_key is not None:
            ring = KeyRing.bootstrap("rotation", explicit_key)
        elif create:
            ring = KeyRing.bootstrap("rotation", _generate_ledger_key(key_file))
        else:
            raise KeyConfigurationError(
                f"No rotation ledger key found beside {path}. A ledger cannot be read without the key "
                "that signed it; restore the key rather than creating a new one over existing records."
            )

        if path.exists() and not keyring_path.exists():
            # Records exist under the legacy single-key layout: keep using that
            # key so previously written records remain verifiable.
            ring = KeyRing.bootstrap("rotation", FileKeyProvider(key_file).read())
        return cls(path, ring, keyring_path=keyring_path)

    @classmethod
    def for_store(cls, store_path: Path) -> "RotationLedger":
        """Default ledger location for a deployment, derived from a store path."""
        store_path = Path(store_path)
        return cls.open(store_path.with_name(store_path.name + ".rotation-ledger.jsonl"))

    def describe(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "keyring_path": str(self.keyring_path) if self.keyring_path else None,
            "format": LEDGER_FORMAT,
            "key_versions": self.keyring.versions,
            "active_key_version": self.keyring.active_version,
            "records": len(self.records()) if self.path.exists() else 0,
            "protection": "HMAC-SHA256 chain, independent key, file permissions only",
        }

    # ------------------------------------------------------------------
    # Reading and verification
    # ------------------------------------------------------------------
    def records(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except ValueError as exc:
                raise RotationLedgerTamperError(
                    f"Ledger record {len(out) + 1} in {self.path} is not valid JSON; the ledger has been "
                    "corrupted or edited outside this application."
                ) from exc
        return out

    def _mac(self, body: Dict[str, Any], key_version: int) -> str:
        import hashlib
        import hmac as _hmac

        try:
            material = self.keyring.material_for(key_version)
        except KeyVersionUnavailableError as exc:
            raise RotationLedgerError(
                f"The rotation ledger needs key version {key_version}, which is not available. "
                "Refusing to report the ledger as verified."
            ) from exc
        return _hmac.new(material, _canonical(body), hashlib.sha256).hexdigest()

    def verify(self) -> Dict[str, Any]:
        """Verify continuity and every MAC. Never returns valid=True on doubt."""
        import hmac as _hmac

        try:
            rows = self.records()
        except RotationLedgerTamperError as exc:
            return {
                "valid": False,
                "reason": "LEDGER_UNREADABLE",
                "detail": str(exc),
                "checked_count": 0,
                "record_count": 0,
            }

        expected_prev = LEDGER_GENESIS_HASH
        for index, row in enumerate(rows):
            seq = row.get("seq")
            if seq != index + 1:
                return {
                    "valid": False,
                    "reason": "SEQUENCE_GAP",
                    "expected_seq": index + 1,
                    "found_seq": seq,
                    "checked_count": index,
                    "record_count": len(rows),
                }
            if row.get("prev_hash") != expected_prev:
                return {
                    "valid": False,
                    "reason": "PREV_HASH_MISMATCH",
                    "broken_at_seq": seq,
                    "checked_count": index,
                    "record_count": len(rows),
                }
            key_version = int(row.get("key_version", 1))
            body = {k: v for k, v in row.items() if k != "record_hash"}
            try:
                recomputed = self._mac(body, key_version)
            except RotationLedgerError as exc:
                return {
                    "valid": False,
                    "reason": "LEDGER_KEY_UNAVAILABLE",
                    "key_unavailable": True,
                    "missing_key_version": key_version,
                    "detail": str(exc),
                    "checked_count": index,
                    "record_count": len(rows),
                }
            if not _hmac.compare_digest(recomputed, str(row.get("record_hash", ""))):
                return {
                    "valid": False,
                    "reason": "RECORD_TAMPERED",
                    "broken_at_seq": seq,
                    "checked_count": index,
                    "record_count": len(rows),
                }
            expected_prev = row["record_hash"]

        return {
            "valid": True,
            "checked_count": len(rows),
            "record_count": len(rows),
            "head_hash": expected_prev,
            "open_operations": sorted(self._open_operations(rows)),
        }

    @staticmethod
    def _open_operations(rows: List[Dict[str, Any]]) -> set:
        """Operation ids that started but never reached a terminal phase.

        This is the interruption signal: a rotation that was killed mid-run has a
        STARTED record and no COMPLETED/FAILED record.
        """
        started = {r["operation_id"] for r in rows if r.get("phase") == "STARTED"}
        finished = {r["operation_id"] for r in rows if r.get("phase") in TERMINAL_PHASES}
        return started - finished

    # ------------------------------------------------------------------
    # Checkpoints (tail-truncation evidence)
    # ------------------------------------------------------------------
    def export_checkpoint(self, destination: Optional[Path] = None) -> Dict[str, Any]:
        """Produce a head-hash checkpoint for off-host retention.

        Retain this outside the machine that holds the ledger. Comparing a later
        verification against it is the only way to detect removal of trailing
        records, which no hash chain can detect by itself.
        """
        result = self.verify()
        if not result.get("valid"):
            # A missing key means the ledger cannot be *checked*, which is not the
            # same as it being wrong; keep the two distinguishable for an operator.
            if result.get("key_unavailable"):
                raise RotationLedgerError(
                    "Refusing to checkpoint: the ledger key version "
                    f"{result.get('missing_key_version')} is unavailable, so the ledger cannot be "
                    "verified. Restore the key before exporting a checkpoint."
                )
            raise RotationLedgerTamperError(
                f"Refusing to checkpoint a ledger that does not verify: {result.get('reason')}"
            )
        rows = self.records()
        checkpoint = {
            "format": LEDGER_FORMAT,
            "schema": LEDGER_SCHEMA_VERSION,
            "exported_at": _utcnow(),
            "record_count": len(rows),
            "head_hash": result["head_hash"],
            "last_recorded_at": rows[-1]["recorded_at"] if rows else None,
        }
        if destination is not None:
            atomic_write_bytes(Path(destination), json.dumps(checkpoint, indent=2, sort_keys=True).encode() + b"\n")
            checkpoint["path"] = str(destination)
        return checkpoint

    def verify_against_checkpoint(self, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        """Verify the ledger and prove it has not been truncated since *checkpoint*.

        A shorter ledger than the checkpoint, or a different hash at the
        checkpoint's head position, is reported as truncation/tampering. A longer
        ledger is expected and correct: new operations append.
        """
        validate_checkpoint(checkpoint)
        result = self.verify()
        if not result.get("valid"):
            return result
        expected_count = int(checkpoint["record_count"])
        expected_head = checkpoint["head_hash"]
        rows = self.records()
        if len(rows) < expected_count:
            raise RotationLedgerTruncatedError(
                f"The rotation ledger holds {len(rows)} records but a retained checkpoint recorded "
                f"{expected_count}. Trailing records have been removed; treat this as tampering.",
                expected_count=expected_count,
                actual_count=len(rows),
            )
        if expected_count and rows[expected_count - 1]["record_hash"] != expected_head:
            return {
                **result,
                "valid": False,
                "reason": "CHECKPOINT_MISMATCH",
                "detail": "The record at the checkpointed position no longer matches the retained head hash.",
            }
        appended_since = len(rows) - expected_count
        return {
            **result,
            "checkpoint_matched": True,
            "checkpoint_record_count": expected_count,
            "checkpoint_exported_at": checkpoint.get("exported_at"),
            # Growth is normal; a large number means the off-host checkpoint is
            # stale and the truncation-detection window is wider than intended.
            "records_appended_since_checkpoint": appended_since,
        }

    # ------------------------------------------------------------------
    # Appending
    # ------------------------------------------------------------------
    def append(
        self,
        *,
        operation: str,
        phase: str,
        store: str,
        actor: Optional[Dict[str, Any]] = None,
        operation_id: Optional[str] = None,
        from_version: Optional[int] = None,
        to_version: Optional[int] = None,
        retained_versions: Optional[List[int]] = None,
        counts: Optional[Dict[str, int]] = None,
        integrity: Optional[Dict[str, Any]] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one record atomically. Raises rather than silently not recording."""
        with self._write_lock, rotation_lock(self.lock_path):
            rows = self.records()
            previous = rows[-1] if rows else None
            record = LedgerRecord(
                seq=len(rows) + 1,
                recorded_at=_utcnow(),
                operation_id=operation_id or str(uuid.uuid4()),
                operation=operation,
                phase=phase,
                store=store,
                actor=actor or resolve_actor(),
                from_version=from_version,
                to_version=to_version,
                retained_versions=list(retained_versions or []),
                counts=dict(counts or {}),
                integrity=dict(integrity or {}),
                detail=dict(detail or {}),
                key_version=self.keyring.active_version,
                prev_hash=previous["record_hash"] if previous else LEDGER_GENESIS_HASH,
            )
            record.record_hash = self._mac(record.body(), record.key_version)
            line = json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False) + "\n"
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    written = os.write(fd, line.encode("utf-8"))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as exc:
                raise RotationLedgerError(
                    f"Could not append to the rotation ledger {self.path}: {exc}. A privileged key "
                    "operation that cannot be recorded must not be reported as successful."
                ) from exc
            if written != len(line.encode("utf-8")):
                raise RotationLedgerError(
                    f"Short write to the rotation ledger {self.path}; the record may be truncated."
                )
            # Re-read through the verifier: a record that does not verify must
            # never be treated as evidence that the operation happened.
            check = self.verify()
            if not check.get("valid"):
                raise RotationLedgerTamperError(
                    f"The rotation ledger failed its own post-append verification "
                    f"({check.get('reason')}); not reporting the operation as recorded."
                )
            return record.to_dict()


def _generate_ledger_key(key_file: Path) -> bytes:
    """Create the ledger key on first use, 0600, without clobbering a racing one."""
    import base64

    secret = base64.urlsafe_b64encode(os.urandom(32))
    key_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return FileKeyProvider(key_file).read()
    with os.fdopen(fd, "wb") as handle:
        handle.write(secret)
    return secret


# ----------------------------------------------------------------------
# High-level operation wrapper
# ----------------------------------------------------------------------
class RecordedOperation:
    """Context manager that guarantees STARTED and a terminal phase.

    Using a context manager rather than paired calls is deliberate: an exception
    anywhere inside the operation still produces a FAILED record, so an
    interrupted or crashed rotation is visible as a started-but-unfinished
    operation rather than disappearing.
    """

    def __init__(
        self,
        ledger: RotationLedger,
        *,
        operation: str,
        store: str,
        from_version: Optional[int] = None,
        to_version: Optional[int] = None,
        actor: Optional[Dict[str, Any]] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.ledger = ledger
        self.operation = operation
        self.store = store
        self.from_version = from_version
        self.to_version = to_version
        self.actor = actor
        self.detail = detail or {}
        self.operation_id = str(uuid.uuid4())
        self._started = False
        self._summary: Dict[str, Any] = {}

    def __enter__(self) -> "RecordedOperation":
        # Surface an interrupted predecessor before starting. A STARTED record
        # with no terminal record is the durable signal that a privileged
        # operation was killed mid-run; recording the recovery makes that
        # visible instead of leaving an orphan that only a manual inspection of
        # the ledger would reveal.
        try:
            interrupted = [
                row["operation_id"]
                for row in self.ledger.records()
                if row.get("phase") == "STARTED" and row.get("store") == self.store
            ]
        except RotationLedgerError:
            interrupted = []
        if interrupted:
            # Close each orphan explicitly. Without this the operation stays
            # "open" forever and the interruption signal becomes permanent noise,
            # which is the same as having no signal at all.
            for interrupted_id in interrupted:
                self.ledger.append(
                    operation=self.operation,
                    phase="RECOVERED",
                    store=self.store,
                    actor=self.actor,
                    operation_id=interrupted_id,
                    detail={"recovered_by": self.operation_id},
                )
            self.ledger.append(
                operation=self.operation,
                phase="RECOVERY",
                store=self.store,
                actor=self.actor,
                operation_id=self.operation_id,
                from_version=self.from_version,
                to_version=self.to_version,
                detail={**self.detail, "interrupted_operations": interrupted},
            )

        # Written before anything is mutated: an operation that cannot be
        # recorded must not begin.
        self.ledger.append(
            operation=self.operation,
            phase="STARTED",
            store=self.store,
            actor=self.actor,
            operation_id=self.operation_id,
            from_version=self.from_version,
            to_version=self.to_version,
            detail=self.detail,
        )
        self._started = True
        return self

    def record_summary(self, **summary: Any) -> None:
        self._summary = summary

    def note_target_version(self, version: int) -> None:
        """Record the target version once the operation has chosen one.

        The target is not known when the operation starts (the vault mints new
        key material inside the run), so it is attached before the terminal
        record is written rather than guessed at the start.
        """
        self.to_version = int(version)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            # A refusal is distinguishable from a mid-flight failure: a refusal
            # means a precondition (key availability, configuration, a chain that
            # does not verify) stopped the operation before it changed data.
            refusal = isinstance(exc, (KeyConfigurationError, KeyVersionUnavailableError))
            self.ledger.append(
                operation=self.operation,
                phase="REFUSED" if refusal else "FAILED",
                store=self.store,
                actor=self.actor,
                operation_id=self.operation_id,
                from_version=self.from_version,
                to_version=self.to_version,
                counts=self._summary.get("counts", {}),
                integrity=self._summary.get("integrity", {}),
                detail={**self.detail, "error": f"{exc_type.__name__}: {exc}"[:500]},
            )
            return False
        self.ledger.append(
            operation=self.operation,
            phase="COMPLETED",
            store=self.store,
            actor=self.actor,
            operation_id=self.operation_id,
            from_version=self.from_version,
            to_version=self.to_version,
            retained_versions=self._summary.get("retained_versions"),
            counts=self._summary.get("counts", {}),
            integrity=self._summary.get("integrity", {}),
            detail=self._summary.get("detail", self.detail),
        )
        return False
