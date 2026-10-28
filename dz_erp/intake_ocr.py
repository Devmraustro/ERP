# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 3 — Supplier Invoice Intake, Security Checks, OCR Extraction & Deterministic Validation."""

import hashlib
import io
import json
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal
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

from .database import ERPDatabase, AuthorizationError, FinancialIntegrityError, TenantIsolationError
from .decimal_utils import format_dzd, round_dzd, round_qty, to_decimal
from .rotation_audit import RecordedOperation, RotationLedger
from .key_provider import FileKeyProvider
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
# Bounded: with sustained concurrent writes convergence may need a second pass;
# an unbounded loop would let a busy vault stall a rotation indefinitely.
# ----------------------------------------------------------------------
# Confidence provenance
# ----------------------------------------------------------------------
# A value stored on an extracted field must say what it *is*, not merely how
# large it is. Two kinds exist, and they are not interchangeable:
#
#   ENGINE_LINE  the recognition engine scored the whole text line the field was
#                matched from. It describes the line, not the field value: a
#                perfectly recognised line can still yield a mis-parsed amount,
#                and a badly recognised line can yield a correct one by luck.
#   NOT_MEASURED native PDF text extraction produces exact characters, so no
#                recognition confidence exists. Previously this path stored a
#                constant 0.995, which was fabricated certainty and made any
#                calibration or auto-accept threshold meaningless.
#
# Neither is a calibrated field-level probability. `confidence_calibrated` is
# therefore always False until a labelled corpus and a calibration analysis
# exist (docs/OCR_EVALUATION_PROTOCOL.md §6).
CONFIDENCE_ENGINE_LINE = "engine_line"
CONFIDENCE_NOT_MEASURED = "not_measured"

CONFIDENCE_SCOPE_LINE = "line"
CONFIDENCE_SCOPE_UNAVAILABLE = "unavailable"

CONFIDENCE_BASELINE = {
    CONFIDENCE_ENGINE_LINE: (
        "Recognition engine score for the text line the value was matched from. "
        "It is not a calibrated probability that the field value is correct."
    ),
    CONFIDENCE_NOT_MEASURED: (
        "Not measured. The text came from native PDF extraction, which yields exact "
        "characters but produces no recognition confidence."
    ),
}

# ---------------------------------------------------------------------------
# Script coverage and review routing
# ---------------------------------------------------------------------------
# The bundled recognition head is PP-OCRv4 trained on Latin and Chinese text. It
# cannot read Arabic script. Nothing in this module can make it do so: a missing
# model is a capability gap, not a tuning problem, and the only honest response
# is to route those documents to a human transcription path *explicitly* rather
# than let them produce plausible-looking Latin garbage and call it extraction.
ARABIC_RECOGNITION_HEAD_AVAILABLE = False
RECOGNITION_HEAD = "ppocrv4-latin-chinese"
ARABIC_RECOGNITION_HEAD = "arabic_PP-OCRv4_rec"

ROUTE_STANDARD_REVIEW = "standard_review"
ROUTE_MANUAL_TRANSCRIPTION = "manual_transcription"
EXTRACTION_ROUTES = (ROUTE_STANDARD_REVIEW, ROUTE_MANUAL_TRANSCRIPTION)

REASON_FIELDS_EXTRACTED = "FIELDS_EXTRACTED"
REASON_NATIVE_ARABIC_SUPPORTED = "NATIVE_ARABIC_TEXT_SUPPORTED"
REASON_NO_FIELDS = "NO_FIELDS_RECOGNISED"
REASON_DECLARED_ARABIC = "DECLARED_ARABIC_SCAN"

# ---------------------------------------------------------------------------
# Phase 29 — field review and decisions
# ---------------------------------------------------------------------------
# Who may act as a reviewer on extracted fields, and while the document is in
# which workflow states. Both are policy, not UI: the checks live in the
# service functions and are audited server-side, so a desktop build cannot
# widen them. ``warehouse``/``treasury``/``auditor`` are deliberately excluded
# (stock floor, cash desk, read-only); ``ocr_worker`` is a pipeline identity.
REVIEW_CAPABLE_ROLES = ("clerk", "approver", "accountant", "admin")

# Field decisions and field edits are possible only while the document is
# waiting for review or validated-but-not-yet-submitted. Once an approval
# exists (PendingApproval/Approved/Posted), the document is evidence for a
# decided invoice: changing its values afterwards would desynchronise the
# paper trail from the approval that relied on it, so it is refused.
FIELD_EDITABLE_STATES = ("NeedsReview", "Validated")

# A new draft invoice may cite a document only while it is still in review —
# linking a document that already backs an approval (PendingApproval/Approved/
# Posted) would overwrite that state and desynchronise the evidence.
DOCUMENT_LINKABLE_STATES = ("NeedsReview", "Validated")

# A document is treated as Arabic-script when Arabic letters outnumber Latin ones.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
)
_ARABIC_PRESENTATION_MIN = 0x0600


def is_arabic_char(char: str) -> bool:
    """True when *char* is a letter in an Arabic script block."""
    code = ord(char)
    if not char.isalpha():
        return False
    return any(low <= code <= high for low, high in _ARABIC_RANGES)


def is_latin_letter(char: str) -> bool:
    """True when *char* is a letter in the Latin script (accented forms included)."""
    if not char.isalpha() or is_arabic_char(char):
        return False
    try:
        return "LATIN" in unicodedata.name(char)
    except ValueError:  # pragma: no cover - unnamed code points
        return False


def script_profile(lines_with_meta: List[Tuple[str, Optional[float], List[List[float]]]]) -> Dict[str, Any]:
    """Count Arabic and Latin letters in the extracted text.

    This measures the *text we ended up with*, which is not necessarily the
    language of the page. For native PDF extraction those are the same thing.
    For a scanned page recognised by a head that cannot read Arabic they are
    not, and the ASCII-only result is itself evidence of failure rather than of
    a Latin document - which is why the counts are reported as an observation
    and never used as the sole basis for claiming a document is in French.
    """
    text = "\n".join(item[0] for item in lines_with_meta)
    arabic = sum(1 for char in text if is_arabic_char(char))
    latin = sum(1 for char in text if is_latin_letter(char))
    return {
        "arabic_letters": arabic,
        "latin_letters": latin,
        "arabic_dominant": arabic > latin,
        "measured_on": "extracted_text",
    }


def assess_review_route(
    *,
    engine_name: str,
    lines_with_meta: List[Tuple[str, Optional[float], List[List[float]]]],
    extracted_field_count: int,
    declared_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Decide whether a human should *verify fields* or *transcribe the page*.

    Route is always a human-review route: extraction never authorises posting.
    The distinction is what the reviewer is asked to do, and it is recorded so
    that an Arabic-only scan is never reported as a normal extraction with no
    fields, which reads like a parsing bug rather than an unsupported script.
    """
    profile = script_profile(lines_with_meta)
    native_text = engine_name == "pdfplumber"
    declared_arabic = (declared_language or "").strip().lower().startswith("ar")

    if native_text and profile["arabic_dominant"]:
        route, reason = ROUTE_STANDARD_REVIEW, REASON_NATIVE_ARABIC_SUPPORTED
        detail = (
            "The text layer is Arabic and was read natively, so the fields are as "
            "trustworthy as for any other language; review them normally."
        )
    elif not native_text and declared_arabic:
        route, reason = ROUTE_MANUAL_TRANSCRIPTION, REASON_DECLARED_ARABIC
        detail = (
            "The document was declared Arabic and was recognised from an image. The "
            f"bundled {RECOGNITION_HEAD} head cannot read Arabic script (no {ARABIC_RECOGNITION_HEAD} "
            "model is provisioned), so no field value from this document is evidence of "
            "what the invoice says."
        )
    elif not native_text and profile["arabic_dominant"]:
        route, reason = ROUTE_MANUAL_TRANSCRIPTION, REASON_DECLARED_ARABIC
        detail = (
            "Arabic script dominates the recognised text and it came from the image "
            f"engine, whose {RECOGNITION_HEAD} head cannot read Arabic (no "
            f"{ARABIC_RECOGNITION_HEAD} model is provisioned). Treat every extracted "
            "value as unreliable."
        )
    elif extracted_field_count == 0:
        route, reason = ROUTE_MANUAL_TRANSCRIPTION, REASON_NO_FIELDS
        detail = (
            "No invoice field could be recognised. This is the expected result for an "
            "Arabic-only scan, and also for a poor scan of any language; transcribe the "
            "document manually rather than re-running extraction."
        )
    else:
        route, reason = ROUTE_STANDARD_REVIEW, REASON_FIELDS_EXTRACTED
        detail = "Fields were extracted and must be verified by a human before validation."

    return {
        "route": route,
        "reason": reason,
        "detail": detail,
        "requires_human_review": True,
        # Manual transcription is the stronger requirement: the reviewer starts
        # from an empty form, not from extracted values.
        "requires_manual_transcription": route == ROUTE_MANUAL_TRANSCRIPTION,
        "script": profile,
        "recognition_head": RECOGNITION_HEAD if not native_text else "pdfplumber-native-text",
        "arabic_recognition_available": ARABIC_RECOGNITION_HEAD_AVAILABLE,
        "arabic_recognition_head": ARABIC_RECOGNITION_HEAD,
        "declared_language": declared_language,
    }


CONVERGENCE_PASSES = 3

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
        # Enforced, not assumed: a key readable by another local account is a
        # missing control, and the deployment must learn that at start-up.
        return FileKeyProvider(key_path).read()

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
        return FileKeyProvider(key_path).read()
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
            legacy = FileKeyProvider(self.vault_key_path).read()
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
            # Close the rotation window. Reading the active version and writing
            # the ciphertext are not atomic, so a rotation that completes in
            # between leaves this blob on the *previous* key at a path the
            # rotation has already scanned past - it would survive as a stray
            # version forever, and retiring the old key would silently orphan
            # this document. Re-encrypt if the active key moved under us.
            current = self._keyring.active_version
            if current != version:
                atomic_write_bytes(
                    dest,
                    encode_versioned_blob(current, self._fernet_for(current).encrypt(content)),
                    mode=0o600,
                )
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
        ledger: Optional[RotationLedger] = None,
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

        # The rotation is recorded in a ledger that has its OWN key material, so
        # the record of rotating this vault cannot become unreadable as a result
        # of rotating this vault. See dz_erp/rotation_audit.py.
        ledger = ledger or RotationLedger.for_store(self.vault_root)

        # The record is opened before the rotation lock is taken so that a
        # refused attempt (lock contention, unreadable ciphertext) is itself
        # recorded. A privileged operation that was attempted and denied is
        # audit evidence, not noise.
        with RecordedOperation(
            ledger,
            operation="VAULT_KEY_ROTATION",
            store=f"vault:{self.vault_root}",
            from_version=ring.active_version,
            detail={"resumed": resume_version is not None},
        ) as recorded, rotation_lock(self.rotation_lock_path):
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
            recorded.note_target_version(target_version)

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

            # Converge rather than assume. One pass over a snapshot of the
            # directory is not enough: a document written concurrently can land
            # on the previous version at a path this pass already visited, and
            # it would then survive at the old version indefinitely. Each pass
            # re-scans the actual version headers, so the loop ends on observed
            # convergence, not on the belief that one pass was complete.
            converged = False
            visited: set = set()
            # A document whose ciphertext is damaged - or needs a key version that
            # is missing - cannot converge by being retried. Retrying it on every
            # pass would also count one damaged document as three failures and
            # make the report disagree with the data. It is recorded once and
            # then left alone; verify_blobs() still reports it.
            unretryable: set = set()
            for pass_index in range(1, CONVERGENCE_PASSES + 1):
                for path in sorted(self.vault_root.rglob("*.enc")):
                    if path in unretryable:
                        continue
                    if path not in visited:
                        # Count documents, not passes: a stray picked up in a
                        # later pass is the same document, not a new one.
                        visited.add(path)
                        report["attempted"] += 1
                    failures_before = report["failed"]
                    self._rotate_one(path, target_version, max_retries, journal, report)
                    if report["failed"] > failures_before:
                        unretryable.add(path)
                strays = {
                    version: count
                    for version, count in self.scan_blob_versions().items()
                    if version != target_version
                }
                report["convergence_passes"] = pass_index
                report["stray_versions"] = {str(k): v for k, v in strays.items()}
                if not strays:
                    converged = True
                    break
                journal.record(
                    event="rotation_convergence_pass",
                    pass_index=pass_index,
                    target_version=target_version,
                    strays={str(k): v for k, v in strays.items()},
                )
            report["converged"] = converged

            verification = self.verify_blobs()
            report["verification"] = verification
            report["verified_ok"] = verification["failed"] == 0
            # A rotation is only reported as successful when the data it moved
            # still decrypts *and* nothing is left on an older key. A deployment
            # that retired the old key on "verified" alone would orphan every
            # stray blob, so the unreachable case must never read as success.
            report["status"] = (
                "COMPLETED_VERIFIED"
                if report["verified_ok"] and converged
                else "COMPLETED_UNVERIFIED"
            )
            journal.record(
                event="rotation_finished",
                target_version=target_version,
                completed=report["completed"],
                skipped=report["skipped_already_current"] + report["skipped_concurrent"],
                failed=report["failed"],
                verification_failures=verification["failed"],
                converged=report["converged"],
            )
            recorded.record_summary(
                counts={
                    "attempted": report["attempted"],
                    "completed": report["completed"],
                    "skipped": report["skipped_already_current"] + report["skipped_concurrent"],
                    "failed": report["failed"],
                },
                integrity={
                    "verified": report["verified_ok"],
                    "checked": verification["checked"],
                    "failures": verification["failed"],
                },
                retained_versions=list(self._keyring.versions),
                detail={
                    "resumed": report["resumed"],
                    "journal": str(journal.path),
                    "converged": report["converged"],
                    "convergence_passes": report.get("convergence_passes"),
                    "stray_versions": report.get("stray_versions", {}),
                },
            )
            report["ledger"] = {
                "path": str(ledger.path),
                "operation_id": recorded.operation_id,
            }
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


def _confidence_payload(value: Optional[float], kind: str) -> Dict[str, Any]:
    """Every confidence field carries its provenance alongside its value.

    Returning a bare float gives a reader no way to tell a measured engine score
    from a fabricated constant - which is exactly how an unsupported number ends
    up in a report or an auto-accept rule.
    """
    return {
        "confidence": None if value is None else round(float(value), 4),
        "confidence_kind": kind,
        "confidence_scope": (
            CONFIDENCE_SCOPE_UNAVAILABLE if kind == CONFIDENCE_NOT_MEASURED else CONFIDENCE_SCOPE_LINE
        ),
        # Deliberately constant False: nothing in this codebase has measured
        # field-level calibration, so no caller may infer it from a value.
        "confidence_calibrated": False,
        "confidence_baseline": CONFIDENCE_BASELINE[kind],
    }


def parse_structured_invoice_text(
    lines_with_meta: List[Tuple[str, Optional[float], List[List[float]]]],
    *,
    confidence_kind: str = CONFIDENCE_ENGINE_LINE,
) -> Dict[str, Any]:
    """Extract structured Algerian invoice fields and line items from OCR/PDF lines.

    ``confidence_kind`` states where the per-line score came from: a recogniser
    score (``engine_line``) or nothing at all (``not_measured``). It is a required
    decision by the caller rather than a guessed default inside the parser, so a
    new extraction path cannot silently inherit the wrong meaning.
    """
    if confidence_kind not in (CONFIDENCE_ENGINE_LINE, CONFIDENCE_NOT_MEASURED):
        raise ValueError(f"Unknown confidence kind {confidence_kind!r}")
    full_text = "\n".join(item[0] for item in lines_with_meta)
    injection_warnings = detect_prompt_injection(full_text)

    fields: Dict[str, Dict[str, Any]] = {}
    parsed_lines: List[Dict[str, Any]] = []

    def _record_field(
        name: str, raw: str, norm: str, conf: Optional[float], bbox: List[List[float]]
    ) -> None:
        if name not in fields:
            fields[name] = {
                "raw_text": raw,
                "normalized_value": norm,
                "bbox": bbox,
                # No human decision exists at extraction time, and the workflow
                # requires review regardless of any score, so this is factual
                # rather than a policy that could drift.
                "requires_human_review": True,
                **_confidence_payload(conf, confidence_kind),
            }
            return
        # Phase 29: a later match for a field we already recorded is no longer
        # dropped silently. An identical value is a harmless repeat; a
        # *different* value is a contradiction on the page — the reviewer must
        # see it, so it is kept as a conflict beside the first reading.
        first = fields[name]
        if norm in (None, "") or norm == first["normalized_value"]:
            return
        fields[name].setdefault("_conflicts", []).append(
            {"raw_text": raw, "normalized_value": norm}
        )

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
                    "bbox": bbox,
                    "requires_human_review": True,
                    **_confidence_payload(conf, confidence_kind),
                }
            )

    # Freeze the collected conflicts into their stored form before returning:
    # a JSON array per field (None when the page agreed with itself). This is
    # extraction product — the trigger keeps it immutable after the fact.
    for meta in fields.values():
        conflicts = meta.pop("_conflicts", None)
        meta["conflict_values_json"] = json.dumps(conflicts, ensure_ascii=False) if conflicts else None

    return {
        "fields": fields,
        "lines": parsed_lines,
        "prompt_injection_warnings": injection_warnings,
    }


def extract_document_ocr(
    content: bytes,
    mime_type: str,
    force_image_ocr: bool = False,
    declared_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Run PDF native text extraction or RapidOCR PP-OCRv4 neural network inference.

    ``declared_language`` is the supplier's known document language (``"ar"`` for an
    Arabic-only issuer). It is used only for review routing: when a document is
    declared Arabic and must be recognised from an image, the result is routed to
    manual transcription because the bundled head cannot read Arabic script.
    """
    t0 = time.perf_counter()
    lines_with_meta: List[Tuple[str, Optional[float], List[List[float]]]] = []
    engine_name = "rapidocr-onnxruntime"
    model_version = "rapidocr-1.4.4-ppocrv4"
    # Until a path proves otherwise, no confidence has been measured.
    confidence_kind = CONFIDENCE_NOT_MEASURED

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
                            # Native extraction returns characters, not a
                            # recognition decision, so there is no confidence to
                            # report. This used to be a constant 0.995.
                            lines_with_meta.append((raw_line.strip(), None, bbox))

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
                    confidence_kind = CONFIDENCE_ENGINE_LINE

    latency_ms = (time.perf_counter() - t0) * 1000.0
    parsed = parse_structured_invoice_text(lines_with_meta, confidence_kind=confidence_kind)
    review_route = assess_review_route(
        engine_name=engine_name,
        lines_with_meta=lines_with_meta,
        extracted_field_count=len(parsed["fields"]),
        declared_language=declared_language,
    )
    return {
        "engine_name": engine_name,
        "model_version": model_version,
        "confidence_kind": confidence_kind,
        "latency_ms": round(latency_ms, 2),
        "review_route": review_route,
        "requires_manual_transcription": review_route["requires_manual_transcription"],
        "raw_lines": [
            {
                "text": txt,
                "confidence": None if conf is None else round(conf, 4),
                "confidence_kind": confidence_kind,
                "confidence_scope": (
                    CONFIDENCE_SCOPE_UNAVAILABLE
                    if confidence_kind == CONFIDENCE_NOT_MEASURED
                    else CONFIDENCE_SCOPE_LINE
                ),
                "confidence_calibrated": False,
                "bbox": bbox,
            }
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
    declared_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest a supplier invoice document through Uploaded -> SecurityChecked -> Processing -> Extracted.

    ``declared_language`` is recorded and used for review routing; it never changes
    what is extracted or any amount read from the document.
    """
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
    ocr_result = extract_document_ocr(
        content, mime_type, force_image_ocr=force_image_ocr, declared_language=declared_language
    )
    routing = ocr_result["review_route"]

    with db.transaction() as conn:
        for fname, fmeta in ocr_result["fields"].items():
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, confidence_kind, confidence_scope,
                    confidence_calibrated, bbox_json, conflict_values_json, ocr_model_version, created_at
                ) VALUES (?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    doc_id,
                    fname,
                    fmeta["raw_text"],
                    fmeta["normalized_value"],
                    # Stored as NULL when nothing was measured: the column means
                    # "the confidence that was measured", not "a number".
                    fmeta["confidence"],
                    fmeta["confidence_kind"],
                    fmeta["confidence_scope"],
                    1 if fmeta["confidence_calibrated"] else 0,
                    json.dumps(fmeta["bbox"]),
                    fmeta.get("conflict_values_json"),
                    ocr_result["model_version"],
                    now_iso,
                ),
            )

        for idx, litem in enumerate(ocr_result["lines"]):
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, confidence_kind, confidence_scope,
                    confidence_calibrated, bbox_json, conflict_values_json, ocr_model_version, created_at
                ) VALUES (?, ?, 1, ?, 'line_item_json', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    company_id,
                    doc_id,
                    idx,
                    json.dumps(litem, ensure_ascii=False),
                    json.dumps(litem, ensure_ascii=False),
                    litem["confidence"],
                    litem["confidence_kind"],
                    litem["confidence_scope"],
                    1 if litem["confidence_calibrated"] else 0,
                    json.dumps(litem["bbox"]),
                    ocr_result["model_version"],
                    now_iso,
                ),
            )

        next_state = "NeedsReview"
        conn.execute(
            """
            UPDATE source_documents
            SET workflow_state = ?, ocr_engine = ?, ocr_model_version = ?, ocr_latency_ms = ?,
                extraction_route = ?, route_reason = ?, declared_language = ?,
                script_arabic_letters = ?, script_latin_letters = ?
            WHERE id = ?
            """,
            (
                next_state,
                ocr_result["engine_name"],
                ocr_result["model_version"],
                ocr_result["latency_ms"],
                routing["route"],
                routing["reason"],
                routing["declared_language"],
                routing["script"]["arabic_letters"],
                routing["script"]["latin_letters"],
                doc_id,
            ),
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
                "confidence_kind": ocr_result["confidence_kind"],
                "confidence_calibrated": False,
                "extraction_route": routing["route"],
                "route_reason": routing["reason"],
                "requires_manual_transcription": routing["requires_manual_transcription"],
                "arabic_letters": routing["script"]["arabic_letters"],
                "arabic_recognition_available": routing["arabic_recognition_available"],
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
        "review_route": routing,
        "extraction_route": routing["route"],
        "route_reason": routing["reason"],
        "requires_manual_transcription": routing["requires_manual_transcription"],
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
    """Record a human edit on an extracted field without overwriting raw_text or confidence.

    Phase 29 hardening: the editor must be an active review-capable user of the
    target company, and the document must still be in a review-editable state
    (``NeedsReview`` or ``Validated``) — once an approval exists, the document
    is evidence for a decided invoice and its values are closed. Refusals are
    audited before they are raised.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    denial: Optional[Exception] = None
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or not user["is_active"]:
            raise AuthorizationError("Invalid or inactive user credentials.")
        if user["company_id"] != company_id:
            raise TenantIsolationError(
                f"Tenant isolation violation: user {user_id} (company {user['company_id']}) "
                f"cannot access company {company_id}."
            )
        if user["role"] not in REVIEW_CAPABLE_ROLES:
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="SECURITY_FIELD_EDIT_BLOCKED",
                target_model="source_documents",
                target_id="review",
                after_state={"role": user["role"], "required_roles": list(REVIEW_CAPABLE_ROLES)},
            )
            denial = AuthorizationError(
                f"Role '{user['role']}' is not authorized to review extracted fields; "
                f"required one of {REVIEW_CAPABLE_ROLES}."
            )
        else:
            doc = conn.execute(
                "SELECT workflow_state FROM source_documents WHERE id = ? AND company_id = ?",
                (document_id, company_id),
            ).fetchone()
            if not doc:
                raise ValueError(f"Source document {document_id} not found in company {company_id}.")
            if doc["workflow_state"] not in FIELD_EDITABLE_STATES:
                db.append_audit_event(
                    conn=conn,
                    company_id=company_id,
                    actor_user_id=user_id,
                    action_type="SECURITY_FIELD_EDIT_BLOCKED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={
                        "workflow_state": doc["workflow_state"],
                        "editable_states": list(FIELD_EDITABLE_STATES),
                    },
                )
                denial = FinancialIntegrityError(
                    f"FIELD_EDIT_CLOSED: document state '{doc['workflow_state']}' does not "
                    f"accept field edits; editable states are {FIELD_EDITABLE_STATES}."
                )
            else:
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
                updated = conn.execute(
                    "SELECT * FROM ocr_extracted_fields WHERE id = ?", (row["id"],)
                ).fetchone()
                db.append_audit_event(
                    conn=conn,
                    company_id=company_id,
                    actor_user_id=user_id,
                    action_type="OCR_HUMAN_FIELD_EDIT",
                    target_model="ocr_extracted_fields",
                    target_id=str(row["id"]),
                    before_state={"normalized_value": row["normalized_value"], "raw_text": row["raw_text"]},
                    after_state={
                        "human_override_value": new_value,
                        "raw_text": updated["raw_text"],
                        "edited_by": dict(user)["username"],
                    },
                )
                return dict(updated)
    if denial is not None:
        raise denial
    raise AssertionError("unreachable: denial or return must have happened")


def decide_extracted_field_review(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    document_id: str,
    field_name: str,
    decision: str,
    *,
    value: Optional[str] = None,
    expected_value: Optional[str] = None,
    note: Optional[str] = None,
    line_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Record one human review decision on an extracted field (Phase 29).

    This is the record that turns "review required" from advisory into
    enforced: :mod:`dz_erp.review_policy` computes document satisfaction from
    these rows, and the invoice approval gate refuses to run while any required
    field is undecided, flagged, or stale.

    Decision semantics (closed vocabulary):

    * ``CONFIRMED`` — the current value matches the paper. Requires
      ``expected_value``: the reviewer states what they saw, and a value that
      changed since their read is refused instead of silently confirmed.
    * ``CORRECTED`` — the paper says otherwise. Supplies ``value``, which is
      validated against the field's class before anything is stored. On an
      existing field this writes the override exactly like
      ``update_extracted_field_by_human`` (raw text and confidence provenance
      stay immutable). On a *missing* field this is the manual-transcription
      entry point: it creates the row with honest provenance
      (``human-manual-entry``, no measured confidence) rather than fabricating
      an engine reading. On a line row it rewrites the quantity and recomputes
      the line amount.
    * ``FLAGGED`` — cannot be verified. Blocks the document until superseded.
    * ``NOT_PRESENT`` — the field legitimately does not exist on the document
      (optional fields only; a required critical field must be transcribed,
      never asserted away). Becomes stale if a value appears afterwards.

    Idempotency and concurrency: the whole decision runs inside one IMMEDIATE
    transaction with the policy's data; an identical repeat decision returns
    the recorded one with ``idempotent_replay`` instead of writing a duplicate,
    and a concurrent conflicting edit is caught by the expected-value check.
    """
    from .review_policy import (
        DECISIONS_REQUIRING_VALUE,
        FIELD_DECISIONS,
        LINE_ITEM_FIELD,
        ReviewPolicyValueError,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    if decision not in FIELD_DECISIONS:
        raise ReviewPolicyValueError(
            f"Unknown review decision {decision!r}; expected one of {FIELD_DECISIONS}."
        )
    if field_name == LINE_ITEM_FIELD and line_index is None:
        raise ReviewPolicyValueError("A line-item decision requires line_index.")
    if field_name != LINE_ITEM_FIELD and line_index is not None:
        raise ReviewPolicyValueError("line_index is only valid for line-item decisions.")
    if decision in DECISIONS_REQUIRING_VALUE and value in (None, ""):
        raise ReviewPolicyValueError(f"{decision} requires a value.")
    if note is not None and (len(note) > 256 or "\n" in note):
        raise ReviewPolicyValueError("Review note must be a single line of at most 256 characters.")

    denial: Optional[Exception] = None
    result: Optional[Dict[str, Any]] = None
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or not user["is_active"]:
            raise AuthorizationError("Invalid or inactive user credentials.")
        if user["company_id"] != company_id:
            raise TenantIsolationError(
                f"Tenant isolation violation: user {user_id} (company {user['company_id']}) "
                f"cannot access company {company_id}."
            )
        if user["role"] not in REVIEW_CAPABLE_ROLES:
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="SECURITY_FIELD_REVIEW_BLOCKED",
                target_model="source_documents",
                target_id="review",
                after_state={"role": user["role"], "required_roles": list(REVIEW_CAPABLE_ROLES)},
            )
            denial = AuthorizationError(
                f"Role '{user['role']}' is not authorized to review extracted fields; "
                f"required one of {REVIEW_CAPABLE_ROLES}."
            )
        else:
            doc = conn.execute(
                "SELECT * FROM source_documents WHERE id = ? AND company_id = ?",
                (document_id, company_id),
            ).fetchone()
            if not doc:
                raise ValueError(f"Source document {document_id} not found in company {company_id}.")
            if doc["workflow_state"] not in FIELD_EDITABLE_STATES:
                db.append_audit_event(
                    conn=conn,
                    company_id=company_id,
                    actor_user_id=user_id,
                    action_type="SECURITY_FIELD_REVIEW_BLOCKED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={
                        "workflow_state": doc["workflow_state"],
                        "decidable_states": list(FIELD_EDITABLE_STATES),
                    },
                )
                denial = FinancialIntegrityError(
                    f"FIELD_REVIEW_CLOSED: document state '{doc['workflow_state']}' does not "
                    f"accept field decisions; decidable states are {FIELD_EDITABLE_STATES}."
                )
            else:
                result = _apply_field_decision(
                    conn,
                    db=db,
                    company_id=company_id,
                    user=dict(user),
                    document_id=document_id,
                    field_name=field_name,
                    line_index=line_index,
                    decision=decision,
                    value=value,
                    expected_value=expected_value,
                    note=note,
                    now_iso=now_iso,
                )
    if denial is not None:
        raise denial
    assert result is not None
    return result


def _apply_field_decision(
    conn: Any,
    *,
    db: ERPDatabase,
    company_id: int,
    user: Any,
    document_id: str,
    field_name: str,
    line_index: Optional[int],
    decision: str,
    value: Optional[str],
    expected_value: Optional[str],
    note: Optional[str],
    now_iso: str,
) -> Dict[str, Any]:
    """Apply one validated decision inside the caller's transaction."""
    from .review_policy import (
        DECISION_CONFIRMED,
        DECISION_CORRECTED,
        DECISION_FLAGGED,
        HUMAN_ENTRY_MODEL,
        HUMAN_ENTRY_RAW,
        LINE_ITEM_FIELD,
        ReviewPolicyValueError,
        TEXT_FALLBACK_RULE,
        rule_for,
        validate_corrected_value,
    )

    user_id = int(user["id"])
    is_line = field_name == LINE_ITEM_FIELD
    if is_line:
        row = conn.execute(
            """
            SELECT * FROM ocr_extracted_fields
            WHERE company_id = ? AND document_id = ? AND field_name = ? AND line_index = ?
            """,
            (company_id, document_id, field_name, line_index),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM ocr_extracted_fields
            WHERE company_id = ? AND document_id = ? AND field_name = ?
              AND line_index IS NULL
            """,
            (company_id, document_id, field_name),
        ).fetchone()

    rule = rule_for(field_name)
    current_value: Optional[str] = None
    if row is not None:
        current_value = row["normalized_value"]
        if is_line:
            # Quantities are compared in the canonical 6-decimal form.
            try:
                payload = json.loads(str(current_value))
                current_value = f"{round_qty(to_decimal(str(payload.get('qty')))):.6f}"
            except (ValueError, TypeError, KeyError):
                raise FinancialIntegrityError(
                    "LINE_PAYLOAD_UNPARSEABLE: the line item JSON cannot be reviewed as-is; "
                    "flag it and rebuild the line on the invoice."
                ) from None

    if decision == DECISION_CONFIRMED:
        if row is None:
            raise ReviewPolicyValueError(
                f"FIELD_NOT_EXTRACTED:{field_name} not found on document {document_id}; "
                "a missing field is transcribed (CORRECTED) or asserted NOT_PRESENT."
            )
        if expected_value is None:
            raise ReviewPolicyValueError(
                "CONFIRMED requires expected_value: state the value you reviewed."
            )
        if is_line:
            seen = _canonical_line_qty(expected_value)
            if seen != current_value:
                raise FinancialIntegrityError(
                    f"FIELD_VALUE_CHANGED_SINCE_READ: expected {expected_value!r}, "
                    f"current {current_value!r}; re-read the field and decide again."
                )
        elif str(expected_value) != str(current_value):
            raise FinancialIntegrityError(
                f"FIELD_VALUE_CHANGED_SINCE_READ: expected {expected_value!r}, "
                f"current {current_value!r}; re-read the field and decide again."
            )
        reviewed_value = current_value
    elif decision == DECISION_CORRECTED:
        assert value not in (None, "")
        try:
            reviewed_value = validate_corrected_value(
                rule if rule is not None else TEXT_FALLBACK_RULE, str(value)
            )
        except ValueError as exc:
            raise ReviewPolicyValueError(str(exc)) from exc
        if row is None:
            if is_line:
                raise ReviewPolicyValueError(
                    "Line rows are created by extraction only; transcribe the line on the invoice."
                )
            # Manual transcription entry point: create the field row with honest
            # provenance — a human read the value, no engine measured it. This is
            # how an Arabic-only document (or any unread field) becomes decidable
            # without fabricating an engine reading.
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, confidence_kind, confidence_scope,
                    confidence_calibrated, bbox_json, conflict_values_json,
                    ocr_model_version, human_override_value, edited_by_id, edited_at, created_at
                ) VALUES (?, ?, 1, NULL, ?, ?, ?, NULL, 'not_measured', 'unavailable',
                          0, '[]', NULL, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    document_id,
                    field_name,
                    HUMAN_ENTRY_RAW,
                    reviewed_value,
                    HUMAN_ENTRY_MODEL,
                    reviewed_value,
                    user_id,
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute(
                "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ? AND field_name = ?",
                (company_id, document_id, field_name),
            ).fetchone()
        elif is_line:
            _rewrite_line_quantity(conn, row, reviewed_value)
            conn.execute(
                "UPDATE ocr_extracted_fields SET edited_by_id = ?, edited_at = ? WHERE id = ?",
                (user_id, now_iso, row["id"]),
            )
        else:
            if expected_value is not None and str(expected_value) != str(current_value):
                raise FinancialIntegrityError(
                    f"FIELD_VALUE_CHANGED_SINCE_READ: expected {expected_value!r}, "
                    f"current {current_value!r}; re-read the field and correct again."
                )
            conn.execute(
                """
                UPDATE ocr_extracted_fields
                SET human_override_value = ?, normalized_value = ?, edited_by_id = ?, edited_at = ?
                WHERE id = ?
                """,
                (reviewed_value, reviewed_value, user_id, now_iso, row["id"]),
            )
    elif decision == DECISION_FLAGGED:
        if rule is None:
            raise ReviewPolicyValueError(
                f"FIELD_NOT_IN_POLICY:{field_name} is not a policy field; a flag on it would "
                "never gate anything, so it is refused. Transcribe it (CORRECTED) instead."
            )
        reviewed_value = current_value
    else:  # DECISION_NOT_PRESENT
        if is_line:
            raise ReviewPolicyValueError(
                "NOT_PRESENT is not valid for line items; FLAG the line instead."
            )
        if rule is None:
            raise ReviewPolicyValueError(
                f"FIELD_NOT_IN_POLICY:{field_name} is not a policy field; asserting it absent "
                "is meaningless outside the policy, so it is refused."
            )
        if row is not None and current_value not in (None, ""):
            raise FinancialIntegrityError(
                f"FIELD_IS_PRESENT:{field_name} has a stored value; assert NOT_PRESENT only "
                "for fields the document genuinely lacks."
            )
        if rule.required:
            raise ReviewPolicyValueError(
                f"FIELD_REQUIRED_CANNOT_BE_ABSENT:{field_name} is a required critical field; "
                "transcribe it (CORRECTED) instead of asserting it absent."
            )
        reviewed_value = None

    # Idempotency: an identical repeat of the current latest decision is a
    # replay of that decision, not a new event.
    latest = conn.execute(
        """
        SELECT * FROM ocr_field_reviews
        WHERE company_id = ? AND document_id = ? AND field_name = ? AND line_index IS ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (company_id, document_id, field_name, line_index),
    ).fetchone()
    if (
        latest is not None
        and latest["decision"] == decision
        and latest["reviewed_value"] == reviewed_value
        and latest["reviewed_by_id"] == user_id
    ):
        return {
            "document_id": document_id,
            "field_name": field_name,
            "line_index": line_index,
            "decision": decision,
            "reviewed_value": reviewed_value,
            "reviewed_by_id": user_id,
            "reviewed_at": latest["reviewed_at"],
            "note": latest["note"],
            "idempotent_replay": True,
        }

    cur = conn.execute(
        """
        INSERT INTO ocr_field_reviews (
            company_id, document_id, field_name, line_index, decision,
            reviewed_value, note, reviewed_by_id, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (company_id, document_id, field_name, line_index, decision, reviewed_value, note, user_id, now_iso),
    )
    decision_id = int(cur.lastrowid)
    db.append_audit_event(
        conn=conn,
        company_id=company_id,
        actor_user_id=user_id,
        action_type="OCR_FIELD_REVIEW_DECIDED",
        target_model="ocr_field_reviews",
        target_id=str(decision_id),
        before_state={
            "field_name": field_name,
            "line_index": line_index,
            "value": current_value,
            "previous_decision": None if latest is None else latest["decision"],
        },
        after_state={
            "decision": decision,
            "reviewed_value": reviewed_value,
            "note": note,
            "reviewed_by": user["username"],
        },
    )
    return {
        "document_id": document_id,
        "field_name": field_name,
        "line_index": line_index,
        "decision": decision,
        "reviewed_value": reviewed_value,
        "reviewed_by_id": user_id,
        "reviewed_at": now_iso,
        "note": note,
        "idempotent_replay": False,
    }


def _canonical_line_qty(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return f"{round_qty(to_decimal(str(value))):.6f}"
    except (ValueError, TypeError):
        return None


def _rewrite_line_quantity(conn: Any, row: Any, new_qty: str) -> None:
    """Rewrite one line row's quantity and recompute its HT amount in place.

    The line JSON is extraction output, so the correction keeps every other
    reading untouched and recomputes the line amount from the corrected
    quantity exactly like invoice validation does (qty × PU × discount).
    """
    payload = json.loads(str(row["normalized_value"]))
    qty = to_decimal(new_qty)
    unit_price = to_decimal(payload.get("unit_price_dzd", "0"))
    discount = to_decimal(payload.get("discount_pct", "0.00"))
    line_ht = round_dzd(qty * unit_price * ((Decimal("100.00") - discount) / Decimal("100.00")))
    payload["qty"] = f"{round_qty(qty):.6f}"
    payload["line_ht_dzd"] = format_dzd(line_ht)
    conn.execute(
        "UPDATE ocr_extracted_fields SET normalized_value = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), row["id"]),
    )
