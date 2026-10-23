# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 3 — Supplier Invoice Intake, Security Checks, OCR Extraction & Deterministic Validation."""

import hashlib
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pdfplumber
import pypdf
import pypdfium2 as pdfium
import cv2
from cryptography.fernet import Fernet
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from .database import ERPDatabase, TenantIsolationError
from .decimal_utils import format_dzd
from .key_rotation import (
    BLOB_MAGIC,
    LEGACY_KEY_VERSION,
    BlobFormatError,
    KeyConfigurationError,
    KeyRing,
    KeyVersionInUseError,
    KeyVersionUnavailableError,
    RotationJournal,
    _atomic_replace,
    atomic_write_bytes,
    encode_versioned_blob,
    rotation_lock,
    split_blob,
)

EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
DANGEROUS_PDF_TOKENS = (b"/JavaScript", b"/JS", b"/Launch", b"/RichMedia")

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+instruction\s*:", re.IGNORECASE),
    re.compile(r"mark\s+invoice\s+as\s+approved", re.IGNORECASE),
    re.compile(r"change\s+(supplier\s+)?(iban|rib|bank)", re.IGNORECASE),
    re.compile(r"auto[_\-\s]?post", re.IGNORECASE),
]

_RAPID_OCR_SINGLETON: Optional[RapidOCR] = None


# Deterministic inference settings.  See get_rapid_ocr() for why these matter.
DETERMINISTIC_INFERENCE_OPTIONS: Dict[str, int] = {
    "intra_op_num_threads": 1,
    "inter_op_num_threads": 1,
}


def get_rapid_ocr() -> RapidOCR:
    """Return the process-wide RapidOCR engine.

    ONNX Runtime defaults to ``intra_op_num_threads = -1``, i.e. one thread per
    core.  Parallel floating-point reductions then run in a core-count-dependent
    order, so the same image can yield slightly different logits - and therefore
    a different recognised character - on a different machine.  That makes the
    measured OCR accuracy a property of the host rather than of the code: the
    benchmark scored ``0.9097`` on one machine and ``0.8958`` on a CI runner with
    no code change between them.

    Pinning both thread counts to ``1`` fixes the reduction order, so inference
    is bit-identical everywhere.  The cost is throughput, which is irrelevant
    here: the engine is used for single-document intake, not batch processing.
    """
    global _RAPID_OCR_SINGLETON
    if _RAPID_OCR_SINGLETON is None:
        # OpenCV's own parallel loops are the same class of problem: with the
        # default thread count, `resize` / `cvtColor` inside RapidOCR's
        # preprocessing split work across cores and the per-pixel rounding can
        # differ.  One thread makes them order-stable as well.
        try:
            cv2.setNumThreads(1)
            cv2.setRNGSeed(0)
        except Exception:  # pragma: no cover - defensive: never block startup on this
            pass
        _RAPID_OCR_SINGLETON = RapidOCR(**DETERMINISTIC_INFERENCE_OPTIONS)
    return _RAPID_OCR_SINGLETON


def normalize_invoice_ref(raw_ref: str) -> str:
    """Normalize an invoice number for duplicate detection (uppercase alphanumeric only)."""
    return re.sub(r"[^A-Z0-9]", "", raw_ref.upper())


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


# Suffix of the per-vault key file, written beside the vault directory.
# This file is the *version 1* (legacy) key. It remains the bootstrap key of a
# vault that has never been rotated.
VAULT_KEY_SUFFIX = ".vault.key"

# Version registry, written beside the vault directory only once a rotation has
# happened. When it exists it is authoritative for every key version.
VAULT_KEYRING_SUFFIX = ".vault.keys.json"

# Rotation progress journal and exclusive rotation lock, both beside the vault.
VAULT_ROTATION_JOURNAL_SUFFIX = ".vault.rotation.jsonl"
VAULT_ROTATION_LOCK_SUFFIX = ".vault.rotation.lock"

# Environment variable that supplies the vault key in a real deployment.
VAULT_KEY_ENV = "ERP_VAULT_KEY"


class VaultKeyUnavailableError(KeyVersionUnavailableError):
    """Raised when the vault key is missing while encrypted documents still exist.

    Distinct from a decryption failure so an operator can tell "the key was
    lost" from "this blob is corrupt" - the two need opposite responses.
    """


def _resolve_vault_key(vault_root: Path, explicit: Optional[bytes]) -> bytes:
    """Resolve the Fernet key that encrypts documents at rest.

    Precedence: explicit argument, then ``ERP_VAULT_KEY``, then a key file beside
    the vault directory (generated on first use, mode ``0600``).

    Generating a fresh key per instantiation - the previous behaviour - looks
    harmless and is actively destructive: the key lives only in memory, so the
    next process starts with a different one and every document already in the
    vault becomes permanently undecryptable (``InvalidToken``).  Nothing fails
    loudly; the data is simply gone.  Persisting the key beside the vault makes
    restarts safe while keeping the key out of the encrypted blob directory
    itself.
    """
    if explicit:
        return explicit
    from_env = os.environ.get(VAULT_KEY_ENV)
    if from_env:
        return from_env.encode("utf-8")

    root = Path(vault_root)
    key_path = root.parent / (root.name + VAULT_KEY_SUFFIX)
    if key_path.exists():
        return key_path.read_bytes().strip()

    # The key file is gone but the vault may still hold blobs.  Generating a
    # replacement here is the silent-data-loss case: reads of the existing
    # blobs would raise InvalidToken with nothing pointing at the real cause,
    # and - worse - *writes* would succeed under the new key, so nothing
    # downstream would ever notice the loss either.  Fail loudly instead, and
    # only mint a key when the vault is genuinely empty (first use).
    if any(root.rglob("*.enc")):
        raise VaultKeyUnavailableError(
            f"The document vault key file {key_path} is missing but "
            f"{root} still contains encrypted documents. Refusing to generate a "
            "replacement key: doing so would silently orphan every existing "
            "document. Restore the key file, or set ERP_VAULT_KEY."
        )

    secret = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_EXCL so a racing second process adopts the first one's key rather
        # than replacing it and orphaning the vault.
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return key_path.read_bytes().strip()
    with os.fdopen(fd, "wb") as handle:
        handle.write(secret)
    return secret


class PrivateDocumentVault:
    """Encrypted-at-rest, company-isolated private document storage.

    Key versioning
    --------------
    Every blob written by this build starts with ``DZKV`` + one version byte, so
    the blob itself states which key decrypts it (see
    :mod:`dz_erp.key_rotation`). A blob without that header is legacy ciphertext
    written before versioning existed and is, by definition, version 1.

    The version registry (``<vault>.vault.keys.json``) is created by the first
    rotation. Until then the vault behaves exactly as it always has: one key in
    ``<vault>.vault.key``, which is version 1. That is what makes the upgrade
    backward compatible - no document is rewritten and no key is replaced on
    upgrade.

    A missing *historical* key is fatal, not repairable: reads of blobs that
    need it raise :class:`VaultKeyUnavailableError` instead of "decrypting" with
    whatever key happens to be active.
    """

    def __init__(
        self,
        vault_root: Path,
        encryption_key: Optional[bytes] = None,
        keyring: Optional[KeyRing] = None,
    ) -> None:
        self.vault_root = Path(vault_root)
        self.vault_root.mkdir(parents=True, exist_ok=True)
        self.vault_key_path = self.vault_root.parent / (self.vault_root.name + VAULT_KEY_SUFFIX)
        self.keyring_path = self.vault_root.parent / (self.vault_root.name + VAULT_KEYRING_SUFFIX)
        self.rotation_journal_path = self.vault_root.parent / (
            self.vault_root.name + VAULT_ROTATION_JOURNAL_SUFFIX
        )
        self.rotation_lock_path = self.vault_root.parent / (self.vault_root.name + VAULT_ROTATION_LOCK_SUFFIX)

        if keyring is not None:
            self._keyring = keyring
            self._keyring_persisted = True
        elif self.keyring_path.exists():
            self._keyring = KeyRing.load(self.keyring_path, "vault")
            self._keyring_persisted = True
        else:
            # Un-rotated vault: the pre-existing single key is version 1 and no
            # registry file is written. Upgrading therefore changes no data.
            self._keyring = KeyRing.bootstrap(
                "vault", _resolve_vault_key(self.vault_root, encryption_key)
            )
            self._keyring_persisted = False

        if self._keyring_persisted:
            self._assert_sources_agree(encryption_key)

        self._fernets: Dict[int, Fernet] = {}
        self._refresh_active_key()
        self.validate_key_configuration()

    # ------------------------------------------------------------------
    # Key configuration
    # ------------------------------------------------------------------
    @property
    def keyring(self) -> KeyRing:
        return self._keyring

    @property
    def active_key_version(self) -> int:
        return self._keyring.active_version

    def _refresh_active_key(self) -> None:
        """Rebuild the cached key material and validate every retained version."""
        self.encryption_key = self._keyring.active_material
        self._fernet = Fernet(self.encryption_key)
        # Eagerly construct a Fernet for every retained version so a corrupted
        # historical key surfaces at start-up rather than during an incident.
        # Fernet() raises ValueError for malformed material, which is the same
        # failure the previous single-key implementation produced.
        self._fernets = {version: Fernet(self._keyring.material_for(version)) for version in self._keyring.versions}

    def _assert_sources_agree(self, explicit: Optional[bytes]) -> None:
        """Refuse to run when a supplied key contradicts the persisted registry.

        Precedence cannot resolve this one: an explicit key and a registry may
        not disagree about which key is active, because silently preferring
        either would write new documents under a key the other half of the
        deployment cannot read.
        """
        active = self._keyring.active_material
        if explicit is not None and bytes(explicit) != active:
            raise KeyConfigurationError(
                f"The explicit vault key disagrees with the active key (version "
                f"{self._keyring.active_version}) in {self.keyring_path}. Refusing to guess which key "
                "is authoritative."
            )
        from_env = os.environ.get(VAULT_KEY_ENV)
        if from_env and from_env.encode("utf-8") != active:
            raise KeyConfigurationError(
                f"{VAULT_KEY_ENV} disagrees with the active key (version {self._keyring.active_version}) in "
                f"{self.keyring_path}. Once a key registry exists it is authoritative; update or remove "
                "the environment variable."
            )
        if self.vault_key_path.exists() and self._keyring.has_version(LEGACY_KEY_VERSION):
            legacy = self.vault_key_path.read_bytes().strip()
            if legacy != self._keyring.material_for(LEGACY_KEY_VERSION):
                raise KeyConfigurationError(
                    f"{self.vault_key_path} does not match version {LEGACY_KEY_VERSION} of the key registry "
                    f"{self.keyring_path}. One of the two is stale; refusing to pick one."
                )

    def validate_key_configuration(self, *, check_referenced_versions: bool = True) -> Dict[str, Any]:
        """Fail closed unless every key needed by stored data is present and valid."""
        for version in self._keyring.versions:
            Fernet(self._keyring.material_for(version))
        report: Dict[str, Any] = {"active_version": self._keyring.active_version, "versions": list(self._keyring.versions)}
        if check_referenced_versions:
            referenced = self.scan_blob_versions()
            missing = sorted(set(referenced) - set(self._keyring.versions))
            report["blobs_by_version"] = referenced
            if missing:
                raise VaultKeyUnavailableError(
                    f"{sum(referenced[v] for v in missing)} document(s) in {self.vault_root} require "
                    f"vault key version(s) {missing}, which are absent from {self.keyring_path}. "
                    "Restore the missing key version before starting the service; substituting another "
                    "key would permanently orphan those documents."
                )
        return report

    def _fernet_for(self, version: int) -> Fernet:
        fernet = self._fernets.get(version)
        if fernet is None:
            try:
                fernet = Fernet(self._keyring.material_for(version))
            except KeyVersionUnavailableError as exc:
                raise VaultKeyUnavailableError(str(exc)) from exc
            self._fernets[version] = fernet
        return fernet

    # ------------------------------------------------------------------
    # Blob storage
    # ------------------------------------------------------------------
    def _blob_version_of_file(self, path: Path) -> int:
        """Read only the header of a stored blob. Never decrypts anything."""
        with open(path, "rb") as handle:
            head = handle.read(len(BLOB_MAGIC) + 1)
        if head.startswith(BLOB_MAGIC):
            if len(head) <= len(BLOB_MAGIC):
                raise BlobFormatError(f"Blob {path.name} has a truncated key-version header.")
            version = head[len(BLOB_MAGIC)]
            if version == 0:
                raise BlobFormatError(f"Blob {path.name} declares key version 0.")
            return version
        return LEGACY_KEY_VERSION

    def scan_blob_versions(self) -> Dict[int, int]:
        """Count stored blobs by the key version their header requires."""
        counts: Dict[int, int] = {}
        for path in sorted(self.vault_root.rglob("*.enc")):
            try:
                version = self._blob_version_of_file(path)
            except (OSError, BlobFormatError):
                # A structurally broken blob is reported by verify_blobs(); it
                # must not stop the service from starting, and it must never be
                # re-interpreted as a different version here.
                continue
            counts[version] = counts.get(version, 0) + 1
        return counts

    def store_bytes(self, company_id: int, file_sha256: str, content: bytes) -> str:
        comp_dir = self.vault_root / f"company_{company_id}"
        comp_dir.mkdir(parents=True, exist_ok=True)
        dest = comp_dir / f"{file_sha256}.enc"
        if not dest.exists():
            version = self._keyring.active_version
            blob = encode_versioned_blob(version, self._fernet_for(version).encrypt(content))
            # Atomic: a crash mid-write can no longer leave a truncated blob
            # that would fail to decrypt forever with no explanation.
            atomic_write_bytes(dest, blob, mode=0o600)
        return str(dest)

    def read_bytes(self, company_id: int, requesting_company_id: int, vault_path: str) -> bytes:
        if company_id != requesting_company_id:
            raise TenantIsolationError(
                f"Cross-company file access denied: company {requesting_company_id} cannot read company {company_id} vault."
            )
        p = Path(vault_path).resolve()
        expected_dir = (self.vault_root / f"company_{company_id}").resolve()
        if expected_dir not in p.parents:
            # ``source_documents.vault_path`` holds the absolute path the blob
            # was written at.  Restore into a different directory - or simply
            # move the vault - and every one of those paths dangles, so the
            # document becomes unreadable through no fault of the data.  The
            # blob name is the content SHA-256 and is unique per company, so
            # re-anchor it under the current root.  ``Path.name`` cannot contain
            # a separator, so the result is still confined to ``expected_dir``.
            anchored = expected_dir / p.name
            if not anchored.exists():
                raise TenantIsolationError("Vault path traversal or cross-company path detected.")
            p = anchored
        version, payload, _legacy = split_blob(p.read_bytes())
        # A wrong key still raises InvalidToken; a *missing* key version raises
        # VaultKeyUnavailableError. Keeping those distinct is the difference
        # between "someone tampered" and "the key was lost".
        return self._fernet_for(version).decrypt(payload)

    def verify_blobs(self) -> Dict[str, Any]:
        """Decrypt every stored blob and report per-version counts and failures."""
        report: Dict[str, Any] = {"checked": 0, "failed": 0, "by_version": {}, "errors": []}
        for path in sorted(self.vault_root.rglob("*.enc")):
            rel = str(path.relative_to(self.vault_root))
            try:
                version, payload, _legacy = split_blob(path.read_bytes())
                self._fernet_for(version).decrypt(payload)
            except Exception as exc:
                report["failed"] += 1
                report["errors"].append({"blob": rel, "reason": type(exc).__name__})
                continue
            report["checked"] += 1
            report["by_version"][str(version)] = report["by_version"].get(str(version), 0) + 1
        return report

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------
    def rotate_key(
        self,
        new_key: Optional[bytes] = None,
        *,
        new_version: Optional[int] = None,
        journal_path: Optional[Path] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Re-encrypt every blob with a new active key, safely and resumably.

        The registry is written *before* the first blob is touched, so an
        interruption at any point leaves both the old and the new key available
        and every blob readable. Re-running with the same ``new_key`` resumes
        the interrupted migration instead of inventing yet another version.

        Concurrency is optimistic: each blob's identity and timestamps are
        captured before it is read and re-checked before the atomic replace, so
        a concurrent writer's update is never silently overwritten. A contended
        blob is re-read and retried; one that keeps changing is reported as
        skipped rather than forced.
        """
        ring = self._keyring
        if new_key is not None:
            new_key = bytes(new_key)
            Fernet(new_key)  # ValueError for malformed material, before anything changes
            resume_version = ring.version_of_material(new_key)
        else:
            new_key = Fernet.generate_key()
            resume_version = None

        with rotation_lock(self.rotation_lock_path):
            journal = RotationJournal(journal_path or self.rotation_journal_path)
            referenced = self.scan_blob_versions()
            missing = sorted(set(referenced) - set(ring.versions))
            if missing:
                raise VaultKeyUnavailableError(
                    f"Cannot rotate: existing documents require missing key version(s) {missing}. "
                    "Rotation must not run while any ciphertext is unreadable."
                )

            working = KeyRing.from_dict(ring.to_dict())
            previous_version = working.active_version
            if resume_version is not None:
                target_version = resume_version
                if working.active_version != target_version:
                    working.set_active(target_version)
            else:
                target_version = working.add_version(new_key, version=new_version)

            # Persist first, swap in memory only after the write succeeded: if
            # this fails, nothing has been re-encrypted and the running process
            # keeps using the key the registry still names.
            working.save(self.keyring_path)
            self._keyring = working
            self._keyring_persisted = True
            self._refresh_active_key()

            journal.record(
                event="rotation_started",
                previous_version=previous_version,
                target_version=target_version,
                resumed=resume_version is not None,
                blobs_by_version={str(k): v for k, v in referenced.items()},
            )

            report: Dict[str, Any] = {
                "vault_root": str(self.vault_root),
                "keyring_path": str(self.keyring_path),
                "journal_path": str(journal.path),
                "previous_version": previous_version,
                "active_version": target_version,
                "resumed": resume_version is not None,
                "attempted": 0,
                "completed": 0,
                "skipped_already_current": 0,
                "skipped_concurrent": 0,
                "skipped_missing": 0,
                "failed": 0,
                "errors": [],
            }

            for path in sorted(self.vault_root.rglob("*.enc")):
                report["attempted"] += 1
                self._rotate_one(path, target_version, max_retries, journal, report)

            verification = self.verify_blobs()
            report["verification"] = verification
            report["verified_ok"] = verification["failed"] == 0
            journal.record(
                event="rotation_finished",
                target_version=target_version,
                completed=report["completed"],
                skipped=report["skipped_already_current"] + report["skipped_concurrent"],
                failed=report["failed"],
                verification_failures=verification["failed"],
            )
            return report

    def _rotate_one(
        self,
        path: Path,
        target_version: int,
        max_retries: int,
        journal: RotationJournal,
        report: Dict[str, Any],
    ) -> None:
        rel = str(path.relative_to(self.vault_root))
        for attempt in range(max_retries + 1):
            try:
                before = path.stat()
                raw = path.read_bytes()
            except FileNotFoundError:
                # Deleted by a concurrent actor. Nothing to migrate, and it is
                # certainly not "completed".
                report["skipped_missing"] += 1
                journal.record(event="blob_missing", blob=rel)
                return
            try:
                version, payload, _legacy = split_blob(raw)
            except BlobFormatError:
                report["failed"] += 1
                report["errors"].append({"blob": rel, "reason": "BlobFormatError"})
                journal.record(event="blob_failed", blob=rel, reason="BlobFormatError")
                return
            if version == target_version:
                report["skipped_already_current"] += 1
                return
            try:
                plaintext = self._fernet_for(version).decrypt(payload)
                blob = encode_versioned_blob(
                    target_version, self._fernet_for(target_version).encrypt(plaintext)
                )
                # Prove the migrated ciphertext round-trips to the identical
                # plaintext before it is allowed to replace the original.
                if self._fernet_for(target_version).decrypt(split_blob(blob)[1]) != plaintext:
                    raise BlobFormatError("re-encrypted blob did not round-trip")
            except (KeyVersionUnavailableError, BlobFormatError) as exc:
                report["failed"] += 1
                report["errors"].append({"blob": rel, "reason": type(exc).__name__, "from_version": version})
                journal.record(
                    event="blob_failed", blob=rel, from_version=version, reason=type(exc).__name__
                )
                return
            except Exception as exc:
                report["failed"] += 1
                report["errors"].append({"blob": rel, "reason": type(exc).__name__, "from_version": version})
                journal.record(
                    event="blob_failed", blob=rel, from_version=version, reason=type(exc).__name__
                )
                return

            if self._replace_if_unchanged(path, blob, before):
                report["completed"] += 1
                journal.record(event="blob_reencrypted", blob=rel, from_version=version, to_version=target_version)
                return
            journal.record(event="blob_contended", blob=rel, attempt=attempt + 1)

        report["skipped_concurrent"] += 1
        journal.record(event="blob_skipped_concurrent", blob=rel)

    @staticmethod
    def _replace_if_unchanged(path: Path, blob: bytes, before: os.stat_result) -> bool:
        """Atomically replace *path* only if nothing else has written it since."""
        tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        atomic_write_bytes(tmp, blob, mode=0o600)
        try:
            current = path.stat()
        except FileNotFoundError:
            tmp.unlink(missing_ok=True)
            return False
        # st_ino is meaningful on POSIX; on NTFS Python fills it from the file
        # index, but the comparison never *relies* on it alone - mtime_ns and
        # size are checked too, so a zero inode cannot mask a concurrent write.
        unchanged = (current.st_ino, current.st_mtime_ns, current.st_size) == (
            before.st_ino,
            before.st_mtime_ns,
            before.st_size,
        )
        if not unchanged:
            tmp.unlink(missing_ok=True)
            return False
        try:
            # Bounded retry: on Windows os.replace fails while a reader holds
            # the destination without FILE_SHARE_DELETE.
            _atomic_replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        return True

    # ------------------------------------------------------------------
    # Key lifecycle
    # ------------------------------------------------------------------
    def set_active_key_version(self, version: int) -> Dict[str, Any]:
        """Documented rollback: write new documents with an existing version again.

        This is the safe reverse of a rotation. It never deletes anything and
        never rewrites existing blobs, so documents already written under the
        newer version stay readable - which is what distinguishes a rollback
        from a data-loss event.
        """
        working = KeyRing.from_dict(self._keyring.to_dict())
        working.set_active(version)
        working.save(self.keyring_path)
        self._keyring = working
        self._refresh_active_key()
        return {
            "active_version": working.active_version,
            "retained_versions": list(working.versions),
            "blobs_by_version": self.scan_blob_versions(),
            "verified": self.verify_blobs(),
        }

    def retire_key(self, version: int) -> Dict[str, Any]:
        """Mark a version retired: still usable to decrypt, never for new writes."""
        working = KeyRing.from_dict(self._keyring.to_dict())
        working.retire(version)
        working.save(self.keyring_path)
        self._keyring = working
        self._refresh_active_key()
        return {"retired_version": int(version), "active_version": self._keyring.active_version}

    def remove_key(self, version: int, *, confirm_retained_copies: bool = False) -> Dict[str, Any]:
        """Delete a key version - refused while any blob still needs it."""
        version = int(version)
        # Checked first so the operator gets the *precise* reason: an active key
        # is never removable, no matter what the data currently looks like.
        if version == self._keyring.active_version:
            raise KeyConfigurationError(
                f"Vault key version {version} is the active version and cannot be removed."
            )
        referenced = self.scan_blob_versions()
        if referenced.get(version):
            raise KeyVersionInUseError(
                f"Refusing to remove vault key version {version}: {referenced[version]} document(s) in "
                f"{self.vault_root} are still encrypted with it. Re-encrypt them first, then verify."
            )
        working = KeyRing.from_dict(self._keyring.to_dict())
        working.remove(version, confirm_retained_copies=confirm_retained_copies)
        working.save(self.keyring_path)
        self._keyring = working
        self._refresh_active_key()
        return {"removed_version": version, "active_version": self._keyring.active_version}


def inspect_and_validate_file_security(
    content: bytes,
    declared_mime: str,
    max_bytes: int = 10 * 1024 * 1024,
    max_pages: int = 20,
    max_dimension_px: int = 6000,
) -> Dict[str, Any]:
    """Validate file size, magic bytes, page/dimension limits, and malware/active-script signatures."""
    if not content:
        raise ValueError("Uploaded file is empty.")
    if len(content) > max_bytes:
        raise ValueError(f"File size {len(content)} exceeds maximum allowed {max_bytes} bytes.")

    findings: List[str] = []
    if EICAR_SIGNATURE in content:
        return {
            "security_status": "QUARANTINED",
            "page_count": 1,
            "findings": ["MALWARE_EICAR_SIGNATURE_DETECTED"],
        }

    if declared_mime == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("MIME type mismatch: declared application/pdf but missing %PDF- signature.")
        for token in DANGEROUS_PDF_TOKENS:
            if token in content:
                findings.append(f"PDF_ACTIVE_CONTENT_{token.decode('ascii', errors='ignore').strip('/')}")
        if findings:
            return {
                "security_status": "QUARANTINED",
                "page_count": 1,
                "findings": findings,
            }
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            page_count = len(reader.pages)
        except Exception as exc:
            # pypdf raises PyPdfError subclasses (PdfReadError, PdfStreamError,
            # PdfReadError, ...) on structurally invalid input, and none of them
            # derive from ValueError.  Left unhandled they escape this security
            # boundary as a 500, which both denies service to the caller and
            # tells an attacker their payload reached the parser.  The breadth
            # is deliberate: this is an untrusted-input boundary, so *any*
            # parser failure must become a clean rejection.
            raise ValueError(
                f"Declared application/pdf but the file is not a structurally valid PDF ({type(exc).__name__})."
            ) from exc
        if page_count < 1 or page_count > max_pages:
            raise ValueError(f"PDF page count {page_count} outside allowed range [1, {max_pages}].")
        return {"security_status": "CLEAN", "page_count": page_count, "findings": []}

    elif declared_mime in ("image/png", "image/jpeg"):
        if declared_mime == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("MIME type mismatch: declared image/png but invalid PNG magic bytes.")
        if declared_mime == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
            raise ValueError("MIME type mismatch: declared image/jpeg but invalid JPEG magic bytes.")
        try:
            img = Image.open(io.BytesIO(content))
            w, h = img.size
        except Exception as exc:
            # Same rationale as the PDF branch: a header-valid but structurally
            # broken image must be rejected, not crash the request.
            raise ValueError(
                f"Declared image but the file is not a structurally valid image ({type(exc).__name__})."
            ) from exc
        if w > max_dimension_px or h > max_dimension_px:
            raise ValueError(f"Image dimensions {w}x{h} exceed limit {max_dimension_px}px.")
        return {"security_status": "CLEAN", "page_count": 1, "findings": []}

    else:
        raise ValueError(f"Unsupported MIME type: {declared_mime}")


def detect_prompt_injection(text: str) -> List[str]:
    """Scan extracted text for embedded adversarial prompt-injection instructions."""
    hits: List[str] = []
    for pat in PROMPT_INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(f"PROMPT_INJECTION_PATTERN:{m.group(0)}")
    return hits


def parse_structured_invoice_text(
    lines_with_meta: List[Tuple[str, float, List[List[float]]]],
) -> Dict[str, Any]:
    """Extract structured Algerian invoice fields and line items from OCR/PDF lines."""
    full_text = "\n".join(item[0] for item in lines_with_meta)
    injection_warnings = detect_prompt_injection(full_text)

    fields: Dict[str, Dict[str, Any]] = {}
    parsed_lines: List[Dict[str, Any]] = []

    def _record_field(name: str, raw: str, norm: str, conf: float, bbox: List[List[float]]) -> None:
        if name not in fields:
            fields[name] = {
                "raw_text": raw,
                "normalized_value": norm,
                "confidence": round(float(conf), 4),
                "bbox": bbox,
            }

    for text_line, conf, bbox in lines_with_meta:
        # Ignore prompt injection sentences so they can never pollute structured fields
        if detect_prompt_injection(text_line):
            continue

        # Supplier Name
        m_sup = re.search(r"(?:FOURNISSEUR|SUPPLIER|VENDEUR|شركة|المورد)\s*[:\-]\s*([^\n|]+)", text_line, re.IGNORECASE)
        if m_sup:
            val = m_sup.group(1).strip()
            _record_field("supplier_name", text_line, val, conf, bbox)

        # NIF
        m_nif = re.search(r"NIF\s*[:\-]?\s*([0-9]{15}(?:[0-9]{5})?)", text_line, re.IGNORECASE)
        if m_nif:
            _record_field("supplier_nif", m_nif.group(0), m_nif.group(1), conf, bbox)

        # NIS
        m_nis = re.search(r"NIS\s*[:\-]?\s*([0-9]{15})", text_line, re.IGNORECASE)
        if m_nis:
            _record_field("supplier_nis", m_nis.group(0), m_nis.group(1), conf, bbox)

        # AI
        m_ai = re.search(r"(?:AI|ARTICLE)\s*[:\-]?\s*([0-9]{11})", text_line, re.IGNORECASE)
        if m_ai:
            _record_field("supplier_ai", m_ai.group(0), m_ai.group(1), conf, bbox)

        # RC (stop before AI/NIS/NIF when OCR strips spaces)
        m_rc = re.search(
            r"RC\s*[:\-]?\s*([0-9]{2}/[0-9]{2}[\-][0-9A-Z]{7,12}?)(?=\s*(?:AI|ARTICLE|NIS|NIF)|$)",
            text_line,
            re.IGNORECASE,
        )
        if m_rc:
            _record_field("supplier_rc", m_rc.group(0), m_rc.group(1), conf, bbox)

        # Invoice number (handles both spaced PDF text and spaceless RapidOCR text)
        m_inv = re.search(
            r"(?:FACTURE\s*N[°o:]?|INVOICE\s*NO|N[°o]\s*FACTURE|فاتورة\s*رقم)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{3,28}?)(?=\s*(?:DATE|DU|NIF|PO|تاريخ)|$)",
            text_line,
            re.IGNORECASE,
        )
        if m_inv:
            raw_inv = m_inv.group(1).strip()
            _record_field("invoice_number", m_inv.group(0), raw_inv, conf, bbox)

        # Invoice Date
        m_date = re.search(r"(?:DATE(?:\s*FACTURE)?|تاريخ)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})", text_line, re.IGNORECASE)
        if m_date:
            d_str = m_date.group(1)
            if "/" in d_str:
                dd, mm, yyyy = d_str.split("/")
                d_str = f"{yyyy}-{mm}-{dd}"
            _record_field("invoice_date", m_date.group(0), d_str, conf, bbox)

        # Due Date
        m_due = re.search(r"(?:ECHEANCE|DUE\s*DATE)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text_line, re.IGNORECASE)
        if m_due:
            _record_field("due_date", m_due.group(0), m_due.group(1), conf, bbox)

        # PO Reference
        m_po = re.search(r"(?:PO|BC|COMMANDE)\s*[:\-]?\s*(PO-[0-9]{4}-[0-9]{3,6})", text_line, re.IGNORECASE)
        if m_po:
            _record_field("po_number", m_po.group(0), m_po.group(1).upper(), conf, bbox)

        # GRN Reference
        m_grn = re.search(r"(?:BL|BR|GRN|RECEPTION)\s*[:\-]?\s*(GRN-[0-9]{4}-[0-9]{3,6})", text_line, re.IGNORECASE)
        if m_grn:
            _record_field("grn_number", m_grn.group(0), m_grn.group(1).upper(), conf, bbox)

        # Payment Method
        if re.search(r"(?:PAIEMENT|MODE)\s*[:\-]?\s*(?:ESPECES|ESPÈCES|CASH|نقدا)", text_line, re.IGNORECASE):
            _record_field("payment_method", text_line, "CASH", conf, bbox)
        elif re.search(r"(?:PAIEMENT|MODE)\s*[:\-]?\s*(?:VIREMENT|BANK_TRANSFER)", text_line, re.IGNORECASE):
            _record_field("payment_method", text_line, "BANK_TRANSFER", conf, bbox)
        elif re.search(r"(?:PAIEMENT|MODE)\s*[:\-]?\s*(?:CHEQUE|CHÈQUE)", text_line, re.IGNORECASE):
            _record_field("payment_method", text_line, "CHEQUE", conf, bbox)

        # Currency
        if re.search(r"(DZD|DA|د\.ج)", text_line):
            _record_field("currency_code", "DZD", "DZD", conf, bbox)

        # Totals: Total HT, TVA, Timbre, Total TTC
        m_ht = re.search(r"TOTAL\s*HT\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_ht:
            _record_field("amount_ht_dzd", m_ht.group(0), format_dzd(m_ht.group(1)), conf, bbox)

        m_tva = re.search(r"(?:TOTAL\s*)?TVA(?:\s*(?:19|9)\s*%)?\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_tva and "ITEM" not in text_line.upper():
            _record_field("amount_tva_dzd", m_tva.group(0), format_dzd(m_tva.group(1)), conf, bbox)

        m_timbre = re.search(r"(?:DROIT\s*DE\s*)?TIMBRE\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_timbre:
            _record_field("amount_timbre_dzd", m_timbre.group(0), format_dzd(m_timbre.group(1)), conf, bbox)

        m_ttc = re.search(r"TOTAL\s*TTC\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_ttc:
            _record_field("amount_ttc_dzd", m_ttc.group(0), format_dzd(m_ttc.group(1)), conf, bbox)

        # Line items format (supports both spaced PDF and spaceless RapidOCR output)
        m_line = re.search(
            r"ITEM\s*([A-Z0-9]+-[0-9]+)\s*(.+?)\s*QTY[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*PU[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*TVA[:=]\s*([0-9]+(?:\.[0-9]+)?)%?\s*HT[:=]\s*([0-9]+(?:\.[0-9]+)?)",
            text_line,
            re.IGNORECASE,
        )
        if m_line:
            parsed_lines.append(
                {
                    "sku": m_line.group(1).strip().upper(),
                    "description": m_line.group(2).strip(),
                    "qty": m_line.group(3),
                    "unit_price_dzd": m_line.group(4),
                    "tax_rate": m_line.group(5),
                    "line_ht_dzd": format_dzd(m_line.group(6)),
                    "confidence": round(float(conf), 4),
                    "bbox": bbox,
                }
            )

    return {
        "fields": fields,
        "lines": parsed_lines,
        "prompt_injection_warnings": injection_warnings,
    }


def extract_document_ocr(content: bytes, mime_type: str, force_image_ocr: bool = False) -> Dict[str, Any]:
    """Run PDF native text extraction or RapidOCR PP-OCRv4 neural network inference."""
    t0 = time.perf_counter()
    lines_with_meta: List[Tuple[str, float, List[List[float]]]] = []
    engine_name = "rapidocr-onnxruntime"
    model_version = "rapidocr-1.4.4-ppocrv4"

    if mime_type == "application/pdf" and not force_image_ocr:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                txt = page.extract_text() or ""
                if txt.strip():
                    engine_name = "pdfplumber"
                    model_version = "pdfplumber-0.11.6+pdfminer"
                    for line_idx, raw_line in enumerate(txt.splitlines()):
                        if raw_line.strip():
                            bbox = [[0.0, float(line_idx * 15)], [500.0, float(line_idx * 15)], [500.0, float((line_idx + 1) * 15)], [0.0, float((line_idx + 1) * 15)]]
                            lines_with_meta.append((raw_line.strip(), 0.995, bbox))

    if not lines_with_meta:
        ocr = get_rapid_ocr()
        if mime_type == "application/pdf":
            pdf_doc = pdfium.PdfDocument(content)
            pil_images = [page.render(scale=2.0).to_pil() for page in pdf_doc]
        else:
            pil_images = [Image.open(io.BytesIO(content)).convert("RGB")]

        for pil_img in pil_images:
            res, _ = ocr(np.array(pil_img))
            if res:
                for box, text, score in res:
                    bbox_coords = [[float(pt[0]), float(pt[1])] for pt in box]
                    lines_with_meta.append((str(text), float(score), bbox_coords))

    latency_ms = (time.perf_counter() - t0) * 1000.0
    parsed = parse_structured_invoice_text(lines_with_meta)
    return {
        "engine_name": engine_name,
        "model_version": model_version,
        "latency_ms": round(latency_ms, 2),
        "raw_lines": [
            {"text": txt, "confidence": round(conf, 4), "bbox": bbox}
            for txt, conf, bbox in lines_with_meta
        ],
        "fields": parsed["fields"],
        "lines": parsed["lines"],
        "prompt_injection_warnings": parsed["prompt_injection_warnings"],
    }


def upload_and_process_supplier_document(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    company_id: int,
    user_id: int,
    filename: str,
    mime_type: str,
    content: bytes,
    branch_id: Optional[int] = None,
    force_image_ocr: bool = False,
) -> Dict[str, Any]:
    """Ingest a supplier invoice document through Uploaded -> SecurityChecked -> Processing -> Extracted."""
    file_sha256 = hashlib.sha256(content).hexdigest()
    doc_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    # Check exact SHA-256 content duplicate within the same company
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["company_id"] != company_id:
            raise TenantIsolationError("User does not belong to target company.")

        existing = conn.execute(
            """
            SELECT id, workflow_state FROM source_documents
            WHERE company_id = ? AND file_sha256 = ? AND workflow_state != 'DuplicateRejected'
            """,
            (company_id, file_sha256),
        ).fetchone()

        sec = inspect_and_validate_file_security(content, mime_type)
        vault_path = vault.store_bytes(company_id, file_sha256, content)

        if sec["security_status"] == "QUARANTINED":
            conn.execute(
                """
                INSERT INTO source_documents (
                    id, company_id, branch_id, filename, mime_type, byte_size, page_count,
                    file_sha256, vault_path, encrypted_at_rest, security_status, security_findings,
                    workflow_state, rejection_reason, uploaded_by_id, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'QUARANTINED', ?, 'Rejected', ?, ?, ?)
                """,
                (
                    doc_id,
                    company_id,
                    branch_id,
                    filename,
                    mime_type,
                    len(content),
                    sec["page_count"],
                    file_sha256,
                    vault_path,
                    json.dumps(sec["findings"]),
                    "SECURITY_QUARANTINE:" + ",".join(sec["findings"]),
                    user_id,
                    now_iso,
                ),
            )
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="DOCUMENT_QUARANTINED",
                target_model="source_documents",
                target_id=doc_id,
                after_state={"filename": filename, "findings": sec["findings"]},
            )
            return {
                "document_id": doc_id,
                "workflow_state": "Rejected",
                "security_status": "QUARANTINED",
                "rejection_reason": "SECURITY_QUARANTINE:" + ",".join(sec["findings"]),
            }

        if existing:
            conn.execute(
                """
                INSERT INTO source_documents (
                    id, company_id, branch_id, filename, mime_type, byte_size, page_count,
                    file_sha256, vault_path, encrypted_at_rest, security_status, security_findings,
                    workflow_state, rejection_reason, uploaded_by_id, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'CLEAN', '[]', 'DuplicateRejected', ?, ?, ?)
                """,
                (
                    doc_id,
                    company_id,
                    branch_id,
                    filename,
                    mime_type,
                    len(content),
                    sec["page_count"],
                    file_sha256,
                    vault_path,
                    f"DUPLICATE_FILE_SHA256_OF_{existing['id']}",
                    user_id,
                    now_iso,
                ),
            )
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="DUPLICATE_DOCUMENT_REJECTED",
                target_model="source_documents",
                target_id=doc_id,
                after_state={"file_sha256": file_sha256, "original_document_id": existing["id"]},
            )
            return {
                "document_id": doc_id,
                "workflow_state": "DuplicateRejected",
                "security_status": "CLEAN",
                "rejection_reason": f"DUPLICATE_FILE_SHA256_OF_{existing['id']}",
            }

        # State 1 -> 2 -> 3: Uploaded -> SecurityChecked -> Processing
        conn.execute(
            """
            INSERT INTO source_documents (
                id, company_id, branch_id, filename, mime_type, byte_size, page_count,
                file_sha256, vault_path, encrypted_at_rest, security_status, security_findings,
                workflow_state, uploaded_by_id, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'CLEAN', '[]', 'Processing', ?, ?)
            """,
            (doc_id, company_id, branch_id, filename, mime_type, len(content), sec["page_count"], file_sha256, vault_path, user_id, now_iso),
        )

    # Execute OCR outside the DB lock, then persist extracted fields atomically
    ocr_result = extract_document_ocr(content, mime_type, force_image_ocr=force_image_ocr)

    with db.transaction() as conn:
        for fname, fmeta in ocr_result["fields"].items():
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, bbox_json, ocr_model_version, created_at
                ) VALUES (?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    doc_id,
                    fname,
                    fmeta["raw_text"],
                    fmeta["normalized_value"],
                    fmeta["confidence"],
                    json.dumps(fmeta["bbox"]),
                    ocr_result["model_version"],
                    now_iso,
                ),
            )

        for idx, litem in enumerate(ocr_result["lines"]):
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, bbox_json, ocr_model_version, created_at
                ) VALUES (?, ?, 1, ?, 'line_item_json', ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    doc_id,
                    idx,
                    json.dumps(litem, ensure_ascii=False),
                    json.dumps(litem, ensure_ascii=False),
                    litem["confidence"],
                    json.dumps(litem["bbox"]),
                    ocr_result["model_version"],
                    now_iso,
                ),
            )

        next_state = "NeedsReview"
        conn.execute(
            """
            UPDATE source_documents
            SET workflow_state = ?, ocr_engine = ?, ocr_model_version = ?, ocr_latency_ms = ?
            WHERE id = ?
            """,
            (next_state, ocr_result["engine_name"], ocr_result["model_version"], ocr_result["latency_ms"], doc_id),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="OCR_EXTRACTION_COMPLETED",
            target_model="source_documents",
            target_id=doc_id,
            after_state={
                "workflow_state": next_state,
                "ocr_engine": ocr_result["engine_name"],
                "ocr_model_version": ocr_result["model_version"],
                "extracted_field_count": len(ocr_result["fields"]),
                "extracted_line_count": len(ocr_result["lines"]),
                "prompt_injection_warnings": ocr_result["prompt_injection_warnings"],
            },
        )

    return {
        "document_id": doc_id,
        "workflow_state": next_state,
        "security_status": "CLEAN",
        "ocr_engine": ocr_result["engine_name"],
        "ocr_model_version": ocr_result["model_version"],
        "latency_ms": ocr_result["latency_ms"],
        "fields": ocr_result["fields"],
        "lines": ocr_result["lines"],
        "prompt_injection_warnings": ocr_result["prompt_injection_warnings"],
    }


def update_extracted_field_by_human(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    document_id: str,
    field_name: str,
    new_value: str,
) -> Dict[str, Any]:
    """Record a human edit on an extracted field without overwriting raw_text or confidence."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ? AND field_name = ?",
            (company_id, document_id, field_name),
        ).fetchone()
        if not row:
            raise ValueError(f"Field {field_name} not found on document {document_id}")

        conn.execute(
            """
            UPDATE ocr_extracted_fields
            SET human_override_value = ?, normalized_value = ?, edited_by_id = ?, edited_at = ?
            WHERE id = ?
            """,
            (new_value, new_value, user_id, now_iso, row["id"]),
        )
        updated = conn.execute("SELECT * FROM ocr_extracted_fields WHERE id = ?", (row["id"],)).fetchone()
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="OCR_HUMAN_FIELD_EDIT",
            target_model="ocr_extracted_fields",
            target_id=str(row["id"]),
            before_state={"normalized_value": row["normalized_value"], "raw_text": row["raw_text"]},
            after_state={"human_override_value": new_value, "raw_text": updated["raw_text"]},
        )
        return dict(updated)
