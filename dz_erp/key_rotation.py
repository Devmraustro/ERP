# SPDX-License-Identifier: LGPL-3.0-only
"""Versioned key rings shared by the document vault and the audit hash chain.

Why this module exists
----------------------
Both protected stores in this project were built around a single key:

* ``PrivateDocumentVault`` encrypted every document with one Fernet key and the
  ciphertext carried no record of which key produced it.
* ``ERPDatabase`` HMAC'd every audit event with one secret and the event row
  carried no record of which secret signed it.

That makes rotation impossible without first changing the stored formats,
because after a new key is installed there is nothing on the datum that says
which key it needs. This module supplies the two pieces that were missing, and
nothing else:

1. **A version registry** (:class:`KeyRing`) - an explicit, validated mapping
   from integer key version to key material, with exactly one *active* version
   used for new writes and any number of *retired* versions retained so old
   data stays readable.
2. **A self-describing blob header** (:func:`encode_versioned_blob`) - new
   ciphertext is prefixed with ``DZKV`` + one version byte, so the key a blob
   needs is readable from the blob itself.

Why the header is unambiguous
-----------------------------
A Fernet token is base64url text. The first byte it is *encoded from* is the
token version ``0x80``, which base64url renders as ``g`` - so every stored
Fernet token begins with ``gAAAAA`` (``0x67``). ``D`` is ``0x44``, so a legacy
(header-less) ciphertext can never begin with ``DZKV``. Detection therefore does
not need a database column, a migration, a heuristic or a trial decryption:
either the blob is marked, or it is legacy, and legacy blobs are by definition
version 1.

Legacy data keeps working because an unmarked blob is *defined* to be version 1
and its key is the pre-existing single key file. Nothing is re-encrypted on
upgrade.

Threat model and limits
-----------------------
* Key material is kept outside the data it protects (beside the vault directory
  and beside the database file) with mode ``0600``. That protects against
  another local account reading the key; it does **not** protect against an
  attacker who already has the service account's file access. Filesystem-level
  separation, OS access control and volume encryption remain deployment
  controls.
* Key material is never written to the application database, the security log,
  a report or a Git-tracked file. This module has no logging dependency at all,
  which is deliberate: there is no code path here that could print a key.
* A key ring is not an HSM. There is no hardware-backed wrapping, no split
  custody and no two-person rule. Single-operator compromise of the running
  service compromises the keys it can read.
* Deleting a key version is refused unless the caller explicitly acknowledges
  that backups, replicas and exports may still contain ciphertext that needs
  it (see :meth:`KeyRing.remove`).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:  # pragma: no cover - the import itself is the platform branch
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - the import itself is the platform branch
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


# On-disk key-ring format. The ``format``/``schema`` pair is validated on load
# so a future format change cannot be silently interpreted as this one.
KEYRING_FORMAT = "dz-erp-keyring"
KEYRING_SCHEMA_VERSION = 1

# Version 1 is reserved for everything written before versioning existed. A
# blob with no header is, by definition, version 1.
LEGACY_KEY_VERSION = 1

VALID_KEY_STATUSES = frozenset({"active", "retired", "compromised"})

# Self-describing ciphertext header. See the module docstring for why this can
# never collide with a legacy Fernet token.
BLOB_MAGIC = b"DZKV"

# Minimum length of a rotation-introduced HMAC secret. Legacy secrets are
# accepted as-is for backwards compatibility (see KeyRing.add_version), but a
# *new* secret must be at least this long.
MIN_HMAC_KEY_BYTES = 32

_MAX_VERSION = 255  # the version travels in a single header byte

# Windows-only: os.replace can transiently fail while another process holds the
# destination open. See _atomic_replace.
_REPLACE_ATTEMPTS = 10
_REPLACE_RETRY_SECONDS = 0.05


class KeyRingError(RuntimeError):
    """Base class for key-ring and key-configuration failures."""


class KeyConfigurationError(KeyRingError):
    """The key configuration is invalid, inconsistent or ambiguous.

    Raised for duplicate versions, duplicate key material, a ring whose active
    version is absent, a ring of the wrong kind, an unparseable ring file, or a
    caller-supplied key that disagrees with the persisted ring. Always fatal:
    guessing which of two keys is meant is exactly the silent substitution this
    module exists to prevent.
    """


class KeyVersionUnavailableError(KeyRingError):
    """A referenced key version is not available in the ring.

    Distinct from a decryption or MAC failure so an operator can tell "the key
    was lost" from "the data was tampered with" - the two need opposite
    responses, and conflating them sends an investigation in the wrong
    direction.
    """


class KeyVersionInUseError(KeyRingError):
    """A key version still has live data depending on it, so it cannot go."""


class RotationInProgressError(KeyRingError):
    """Another rotation already holds the exclusive rotation lock."""


class BlobFormatError(KeyRingError):
    """A stored ciphertext blob is structurally invalid (truncated or zero-version).

    Distinct from a decryption failure: the header itself is unusable, so no key
    selection is possible and the blob must not be treated as legacy.
    """


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class KeyEntry:
    """One version of one key. ``material`` is never serialised in the clear."""

    version: int
    material: bytes
    created_at: str
    status: str = "active"


class KeyRing:
    """Validated mapping from key version to key material.

    Invariants enforced on construction and on every mutation:

    * exactly one entry has status ``active``, and it is ``current_version``;
    * versions are unique positive integers in ``1..255``;
    * no two versions share key material (material reuse would silently remove
      the domain separation that makes a per-version MAC meaningful).
    """

    def __init__(self, kind: str, entries: Dict[int, KeyEntry], current_version: int) -> None:
        self.kind = kind
        self._entries: Dict[int, KeyEntry] = dict(entries)
        self._current_version = int(current_version)
        self._validate()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def bootstrap(cls, kind: str, material: bytes, *, version: int = LEGACY_KEY_VERSION) -> "KeyRing":
        """Build a single-version ring around a pre-existing key.

        This is the upgrade path: today's ``.vault.key`` / ``.audit.key`` becomes
        version 1 and nothing else changes. The ring is *not* written to disk
        here - a vault that never rotates keeps working exactly as before, and
        only a rotation creates a ring file.
        """
        return cls(
            kind,
            {version: KeyEntry(version=version, material=bytes(material), created_at=utcnow_iso(), status="active")},
            version,
        )

    @classmethod
    def load(cls, path: Path, kind: str) -> "KeyRing":
        path = Path(path)
        if not path.exists():
            raise KeyConfigurationError(f"Key ring file {path} does not exist.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyConfigurationError(
                f"Key ring file {path} could not be parsed as JSON ({type(exc).__name__})."
            ) from exc
        ring = cls.from_dict(raw)
        if ring.kind != kind:
            raise KeyConfigurationError(
                f"Key ring file {path} is a {ring.kind!r} key ring but a {kind!r} key ring was expected."
            )
        return ring

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "KeyRing":
        if not isinstance(raw, dict):
            raise KeyConfigurationError("Key ring payload is not a JSON object.")
        if raw.get("format") != KEYRING_FORMAT:
            raise KeyConfigurationError(
                f"Unrecognised key ring format {raw.get('format')!r}; expected {KEYRING_FORMAT!r}."
            )
        if raw.get("schema") != KEYRING_SCHEMA_VERSION:
            raise KeyConfigurationError(
                f"Unsupported key ring schema {raw.get('schema')!r}; this build understands "
                f"{KEYRING_SCHEMA_VERSION}."
            )
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind:
            raise KeyConfigurationError("Key ring is missing its 'kind' (vault or audit).")
        keys = raw.get("keys")
        if not isinstance(keys, dict) or not keys:
            raise KeyConfigurationError("Key ring contains no key versions.")
        entries: Dict[int, KeyEntry] = {}
        for raw_version, payload in keys.items():
            try:
                version = int(raw_version)
            except (TypeError, ValueError) as exc:
                raise KeyConfigurationError(f"Key ring version {raw_version!r} is not an integer.") from exc
            if not isinstance(payload, dict):
                raise KeyConfigurationError(f"Key ring entry {version} is not a JSON object.")
            try:
                material = base64.b64decode(payload["material_b64"], validate=True)
            except (KeyError, TypeError, ValueError) as exc:
                raise KeyConfigurationError(f"Key ring entry {version} has no decodable key material.") from exc
            created_at = payload.get("created_at") or utcnow_iso()
            status = payload.get("status", "retired")
            entries[version] = KeyEntry(
                version=version, material=material, created_at=str(created_at), status=str(status)
            )
        try:
            current = int(raw["current_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyConfigurationError("Key ring has no usable 'current_version'.") from exc
        return cls(kind=kind, entries=entries, current_version=current)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def current_version(self) -> int:
        return self._current_version

    @property
    def active_version(self) -> int:
        return self._current_version

    @property
    def active_material(self) -> bytes:
        return self._entries[self._current_version].material

    @property
    def versions(self) -> List[int]:
        return sorted(self._entries)

    @property
    def entries(self) -> Dict[int, KeyEntry]:
        return dict(self._entries)

    def has_version(self, version: int) -> bool:
        return int(version) in self._entries

    def entry(self, version: int) -> KeyEntry:
        entry = self._entries.get(int(version))
        if entry is None:
            raise KeyVersionUnavailableError(
                f"The {self.kind} key version {int(version)} is not present in the key ring. "
                "Refusing to substitute another key: data written with this version stays "
                "unreadable until the exact key is restored."
            )
        return entry

    def material_for(self, version: int) -> bytes:
        return self.entry(version).material

    def version_of_material(self, material: bytes) -> Optional[int]:
        for version, entry in self._entries.items():
            if entry.material == bytes(material):
                return version
        return None

    def summary(self) -> Dict[str, Any]:
        """Return version numbers and statuses only - never key material.

        This is what goes into logs, reports and backup manifests' human-readable
        fields: enough to know *which* keys a restore needs, nothing that would
        let anyone decrypt with it.
        """
        return {
            "kind": self.kind,
            "active_version": self._current_version,
            "versions": [
                {"version": v, "status": self._entries[v].status, "created_at": self._entries[v].created_at}
                for v in self.versions
            ],
        }

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def add_version(
        self,
        material: bytes,
        *,
        version: Optional[int] = None,
        activate: bool = True,
        min_bytes: int = 0,
    ) -> int:
        """Register new key material, optionally making it the active version.

        Duplicate versions and duplicate *material* are both rejected: reusing
        material under a second version number would make the per-version MAC
        domain separation meaningless and quietly weaken verification.
        """
        material = bytes(material)
        if min_bytes and len(material) < min_bytes:
            raise KeyConfigurationError(
                f"New {self.kind} key material is {len(material)} bytes; at least {min_bytes} are required."
            )
        existing = self.version_of_material(material)
        if existing is not None:
            raise KeyConfigurationError(
                f"The supplied {self.kind} key material is already registered as version {existing}. "
                "Refusing to register one key under two versions."
            )
        chosen = self._max_version() + 1 if version is None else int(version)
        if chosen < 1 or chosen > _MAX_VERSION:
            raise KeyConfigurationError(f"Key version {chosen} is outside the supported range 1..{_MAX_VERSION}.")
        if chosen in self._entries:
            raise KeyConfigurationError(f"{self.kind} key version {chosen} already exists; versions must be unique.")

        entries = dict(self._entries)
        if activate:
            for v, entry in list(entries.items()):
                if entry.status == "active":
                    entries[v] = KeyEntry(entry.version, entry.material, entry.created_at, "retired")
        entries[chosen] = KeyEntry(
            version=chosen, material=material, created_at=utcnow_iso(), status="active" if activate else "retired"
        )
        self._entries = entries
        if activate:
            self._current_version = chosen
        self._validate()
        return chosen

    def set_active(self, version: int, *, allow_compromised: bool = False) -> None:
        """Make an existing version active again (used for a documented rollback)."""
        entry = self.entry(version)
        if entry.status == "compromised" and not allow_compromised:
            raise KeyConfigurationError(
                f"{self.kind} key version {version} is marked compromised and cannot be reactivated."
            )
        entries = dict(self._entries)
        for v, other in list(entries.items()):
            entries[v] = KeyEntry(other.version, other.material, other.created_at, other.status)
            if other.status == "active":
                entries[v] = KeyEntry(other.version, other.material, other.created_at, "retired")
        entries[version] = KeyEntry(entry.version, entry.material, entry.created_at, "active")
        self._entries = entries
        self._current_version = int(version)
        self._validate()

    def mark_compromised(self, version: int) -> None:
        self._set_status(version, "compromised")

    def retire(self, version: int) -> None:
        if int(version) == self._current_version:
            raise KeyConfigurationError(
                f"{self.kind} key version {version} is active; activate another version before retiring it."
            )
        self._set_status(version, "retired")

    def remove(self, version: int, *, confirm_retained_copies: bool = False) -> None:
        """Delete a key version. Deliberately hard to do by accident.

        A version is only removable when it is not active and the caller has
        explicitly acknowledged that copies of the data outside the live store -
        backups, replicas, exports, snapshots - may still need it. Deleting a
        key is irreversible and is the only operation in this module that can
        destroy data, so it never happens implicitly.
        """
        version = int(version)
        if version == self._current_version:
            raise KeyConfigurationError(
                f"{self.kind} key version {version} is the active version and cannot be removed."
            )
        self.entry(version)  # raises KeyVersionUnavailableError when absent
        if len(self._entries) == 1:
            raise KeyConfigurationError("Refusing to remove the only remaining key version.")
        if not confirm_retained_copies:
            raise KeyConfigurationError(
                f"Refusing to remove {self.kind} key version {version} without explicit confirmation. "
                "Completing a re-encryption only proves the *live* store no longer needs this key; "
                "backups, replicas, snapshots and exports may still contain ciphertext encrypted "
                "with it, and those copies become permanently unreadable once the key is gone. "
                "Confirm that every retained copy has expired or been re-keyed first."
            )
        entries = dict(self._entries)
        del entries[version]
        self._entries = entries
        self._validate()

    def _set_status(self, version: int, status: str) -> None:
        entry = self.entry(version)
        self._entries[version] = KeyEntry(entry.version, entry.material, entry.created_at, status)
        self._validate()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": KEYRING_FORMAT,
            "schema": KEYRING_SCHEMA_VERSION,
            "kind": self.kind,
            "current_version": self._current_version,
            "keys": {
                str(v): {
                    "material_b64": base64.b64encode(self._entries[v].material).decode("ascii"),
                    "created_at": self._entries[v].created_at,
                    "status": self._entries[v].status,
                }
                for v in self.versions
            },
        }

    def save(self, path: Path) -> Path:
        """Persist the ring atomically: never a half-written key ring on disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
        atomic_write_bytes(path, payload, mode=0o600)
        return path

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _max_version(self) -> int:
        return max(self._entries) if self._entries else 0

    def _validate(self) -> None:
        if not self._entries:
            raise KeyConfigurationError(f"The {self.kind} key ring contains no versions.")
        for version, entry in self._entries.items():
            if not isinstance(version, int) or not (1 <= version <= _MAX_VERSION):
                raise KeyConfigurationError(f"Key version {version!r} is outside 1..{_MAX_VERSION}.")
            if entry.status not in VALID_KEY_STATUSES:
                raise KeyConfigurationError(
                    f"{self.kind} key version {version} has unknown status {entry.status!r}."
                )
        active = [v for v, e in self._entries.items() if e.status == "active"]
        if len(active) != 1:
            raise KeyConfigurationError(
                f"The {self.kind} key ring must have exactly one active version; found {sorted(active)}."
            )
        if self._current_version not in self._entries:
            raise KeyConfigurationError(
                f"The {self.kind} key ring's current_version {self._current_version} has no key entry."
            )
        if active[0] != self._current_version:
            raise KeyConfigurationError(
                f"The {self.kind} key ring's active version {active[0]} disagrees with "
                f"current_version {self._current_version}."
            )
        materials = {}
        for version, entry in self._entries.items():
            if not entry.material:
                raise KeyConfigurationError(f"{self.kind} key version {version} has empty key material.")
            previous = materials.get(entry.material)
            if previous is not None:
                raise KeyConfigurationError(
                    f"{self.kind} key versions {previous} and {version} share identical key material; "
                    "each version must have its own key."
                )
            materials[entry.material] = version


# ----------------------------------------------------------------------
# Self-describing ciphertext
# ----------------------------------------------------------------------
def encode_versioned_blob(version: int, payload: bytes) -> bytes:
    """Prefix *payload* with the ``DZKV`` magic and a one-byte version."""
    version = int(version)
    if not (1 <= version <= _MAX_VERSION):
        raise KeyConfigurationError(f"Cannot write key version {version}; supported range is 1..{_MAX_VERSION}.")
    return BLOB_MAGIC + bytes([version]) + bytes(payload)


def split_blob(blob: bytes) -> tuple:
    """Return ``(key_version, payload, was_legacy)`` for a stored blob.

    An unmarked blob is legacy ciphertext and is therefore version 1. A marked
    blob whose version byte is absent or zero is corrupt rather than legacy, and
    is rejected instead of being guessed at.
    """
    blob = bytes(blob)
    if blob.startswith(BLOB_MAGIC):
        if len(blob) <= len(BLOB_MAGIC):
            raise BlobFormatError("Stored blob has a key-version header but no ciphertext.")
        version = blob[len(BLOB_MAGIC)]
        if version == 0:
            raise BlobFormatError("Stored blob declares key version 0, which does not exist.")
        payload = blob[len(BLOB_MAGIC) + 1:]
        if not payload:
            raise BlobFormatError("Stored blob has a key-version header but an empty ciphertext.")
        return version, payload, False
    if not blob:
        raise BlobFormatError("Stored blob is empty.")
    return LEGACY_KEY_VERSION, blob, True


# ----------------------------------------------------------------------
# Filesystem helpers
# ----------------------------------------------------------------------
def atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Write *payload* to *path* atomically with owner-only permissions.

    A crash mid-write must never leave a truncated key ring or ciphertext: the
    bytes land in a temporary file in the same directory, are fsynced, and are
    then ``os.replace``d over the target, which is atomic on POSIX and on NTFS.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp already creates the file 0600 on every platform, so the explicit
    # mode below is a belt-and-braces measure rather than the only protection.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        _set_mode_on_fd(fd, tmp, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _set_mode_on_fd(fd: int, path: Path, mode: int) -> None:
    """Apply an owner-only mode to an open descriptor, portably.

    ``os.fchmod`` is **Unix-only** - it does not exist on Windows, where calling
    it raises ``AttributeError``. This module is on the write path for every
    vault document and every key registry, so an unguarded call here broke every
    rotation and every document write on Windows.

    On Windows the closest portable equivalent is ``os.chmod`` on the path, which
    only toggles the read-only attribute; NTFS ACLs, not POSIX mode bits, are the
    real access control there. That limitation is why the key-custody assessment
    does not treat file permissions as protection equivalent to an HSM.
    """
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:  # pragma: no branch - platform branch
        fchmod(fd, mode)
        return
    with contextlib.suppress(OSError):  # pragma: no cover - Windows
        os.chmod(path, mode)


def _atomic_replace(src: Path, dst: Path) -> None:
    """``os.replace`` with a bounded retry for Windows sharing violations.

    On POSIX ``os.replace`` cannot fail because another process has the
    destination open. On Windows it raises ``PermissionError`` while a reader
    holds the file without ``FILE_SHARE_DELETE``. Retrying briefly converts that
    transient condition into a short wait instead of a failed rotation; if the
    file stays open the error still propagates, so a genuine conflict is never
    hidden behind a silent success.
    """
    attempts = _REPLACE_ATTEMPTS if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # pragma: no cover - Windows only
            if attempt == attempts - 1:
                raise
            time.sleep(_REPLACE_RETRY_SECONDS)


def _fsync_dir(directory: Path) -> None:
    """Persist a directory entry after ``os.replace``. Not available on Windows."""
    if os.name != "posix":  # pragma: no cover - platform branch
        return
    with contextlib.suppress(OSError):
        dir_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


_ROTATION_LOCKS: Dict[str, threading.Lock] = {}
_ROTATION_LOCKS_GUARD = threading.Lock()


def _process_lock_for(lock_path: Path) -> threading.Lock:
    key = str(Path(lock_path).resolve())
    with _ROTATION_LOCKS_GUARD:
        lock = _ROTATION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _ROTATION_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def rotation_lock(lock_path: Path) -> Iterator[None]:
    """Serialise rotation runs for one store across threads and processes.

    Rotation must not run twice concurrently: two batches re-encrypting the same
    blobs would race on the same paths and could, in the worst case, interleave
    a version-1 read with a version-3 write. A non-blocking exclusive lock makes
    the second run fail immediately with an actionable error instead of
    corrupting data. The lock is advisory and file-based, so it also covers
    separate processes (a cron job plus an operator at a terminal).
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    local = _process_lock_for(lock_path)
    if not local.acquire(blocking=False):
        raise RotationInProgressError(f"A rotation for {lock_path} is already running in this process.")
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        if fcntl is not None:  # pragma: no branch - platform branch
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RotationInProgressError(
                    f"Another process already holds the rotation lock {lock_path}."
                ) from exc
        elif msvcrt is not None:  # pragma: no cover - Windows
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RotationInProgressError(
                    f"Another process already holds the rotation lock {lock_path}."
                ) from exc
        yield
    finally:
        if fd is not None:
            if fcntl is not None:  # pragma: no branch - platform branch
                with contextlib.suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                with contextlib.suppress(OSError):
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            os.close(fd)
        local.release()


class RotationJournal:
    """Append-only, secret-free record of rotation progress.

    Every line holds a store-relative blob name (for the vault that is the
    content SHA-256, which is already public), the versions involved, a status
    and a timestamp. No plaintext, no key material, no customer identifiers -
    the journal is safe to attach to an incident ticket.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields: Any) -> None:
        entry = {"timestamp": utcnow_iso()}
        entry.update(fields)
        line = json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
        with self._lock:
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)

    def lines(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with io.StringIO(self.path.read_text(encoding="utf-8")) as handle:
            for raw in handle:
                raw = raw.strip()
                if raw:
                    out.append(json.loads(raw))
        return out
