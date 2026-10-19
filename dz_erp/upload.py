# SPDX-License-Identifier: LGPL-3.0-only
"""Bounded document-upload boundary for the supplier-invoice intake API.

The upload endpoint previously did an unbounded ``await file.read()``, which
let a single request pull an arbitrarily large body into memory before any
size rule was applied.  This module provides the application-level control:

* a documented, configurable maximum upload size with a conservative default;
* incremental chunked reading that enforces the limit *while* reading, so the
  full body is never materialised for an oversized request;
* an allowlist of permitted file types, cross-checked between the filename
  extension, the client-declared MIME type, and the actual magic bytes;
* helpers to release resources and remove partial temporary artifacts when a
  request is rejected.

Proxy-level limits (for example ``client_max_body_size`` in nginx) are a useful
*complement* but are never a replacement: they are configuration that can be
absent, misconfigured, or bypassed by an internal caller, so the application
must enforce its own bound regardless.
"""

import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

# Conservative default for this initial ERP release: 10 MiB.  A supplier
# invoice is a small PDF or a scanned page image; 10 MiB already accommodates
# a 20-page colour scan at 300 dpi.  Override with ERP_MAX_UPLOAD_BYTES.
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Read granularity.  Small enough that an oversized request is abandoned after
# at most one extra chunk beyond the limit, large enough to stay efficient.
UPLOAD_CHUNK_BYTES = 64 * 1024

# Only document types the intake pipeline can actually process.
ALLOWED_MIME_TYPES: FrozenSet[str] = frozenset({"application/pdf", "image/png", "image/jpeg"})
ALLOWED_EXTENSIONS: FrozenSet[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg"})

# Windows reserved device names.  A stem matching one of these addresses a
# device rather than a file on Windows, so accepting it would break a
# Windows-hosted deployment even though it is harmless on Linux.
WINDOWS_RESERVED_NAMES: FrozenSet[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

# Extension -> the single MIME type that extension is permitted to claim.
EXTENSION_TO_MIME: Dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Magic-byte signatures, used to confirm the *content* rather than the label.
MAGIC_SIGNATURES: Dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}

ENV_MAX_UPLOAD_BYTES = "ERP_MAX_UPLOAD_BYTES"


class UploadTooLargeError(Exception):
    """Raised when a request body exceeds the configured upload limit."""

    def __init__(self, declared_size: Optional[int], limit: int) -> None:
        self.declared_size = declared_size
        self.limit = limit
        detail = (
            f"Uploaded document exceeds the maximum allowed size of {limit} bytes."
            if declared_size is None
            else f"Uploaded document ({declared_size} bytes) exceeds the maximum allowed size of {limit} bytes."
        )
        super().__init__(detail)


class UnsupportedMediaTypeError(Exception):
    """Raised when the filename, declared MIME type, or content is not permitted."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class UploadValidationError(Exception):
    """Raised when upload metadata is inconsistent (for example a lying filename)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def configured_max_upload_bytes() -> int:
    """Resolve the effective upload limit from ``ERP_MAX_UPLOAD_BYTES``.

    A malformed or non-positive override is ignored so a typo can never silently
    disable the limit.
    """
    raw = os.environ.get(ENV_MAX_UPLOAD_BYTES)
    if raw is None or not raw.strip():
        return DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_MAX_UPLOAD_BYTES
    return value if value > 0 else DEFAULT_MAX_UPLOAD_BYTES


def extension_of(filename: Optional[str]) -> str:
    """Lower-cased extension of ``filename``, or ``''`` when there is none."""
    if not filename:
        return ""
    return Path(filename).suffix.lower()


def _validate_filename_shape(filename: Optional[str]) -> str:
    """Reject filenames that are empty, contain path separators, or traverse.

    The extension allowlist alone is not enough: ``../etc/passwd.pdf`` has a
    perfectly legal ``.pdf`` extension, and a name like ``invoice.pdf/../../../x``
    or one containing a NUL byte can defeat naive downstream path handling.
    """
    if not filename or not filename.strip():
        raise UploadValidationError("A non-empty upload filename is required.")

    candidate = filename.strip()
    # Reject both POSIX and Windows path separators: a name like
    # "..\\..\\invoice.pdf" is harmless on Linux but a traversal on Windows,
    # and the extension allowlist alone would happily accept it.
    if "/" in candidate or "\\" in candidate:
        raise UploadValidationError(
            "Uploaded filename must not contain path separators or directory traversal."
        )
    if candidate != Path(candidate).name:
        raise UploadValidationError(
            "Uploaded filename must not contain path separators or directory traversal."
        )
    if candidate in {".", ".."} or "\x00" in candidate:
        raise UploadValidationError("Uploaded filename is not a valid document name.")
    # Reject the remaining C0 control characters and DEL.  A newline or a
    # terminal escape sequence in a filename cannot traverse a path, but it can
    # forge a log line or rewrite an operator's terminal when the name is
    # echoed into the audit trail or a review queue.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        raise UploadValidationError("Uploaded filename must not contain control characters.")
    # Reject Windows reserved device names.  The code already refuses Windows
    # path separators because a name like "..\\..\\invoice.pdf" is harmless on
    # Linux but a traversal on Windows; the same forward-compatibility applies
    # to CON/PRN/AUX/NUL/COM1-9/LPT1-9, which on Windows address a device
    # rather than a file.
    stem = candidate.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        raise UploadValidationError("Uploaded filename must not be a reserved device name.")
    if len(candidate) > 255:
        raise UploadValidationError("Uploaded filename is too long (limit 255 bytes).")
    return candidate


def sniff_mime_type(content: bytes) -> Optional[str]:
    """Identify the document type from its leading magic bytes.

    Returns ``None`` when the content matches no supported signature, so the
    caller never has to trust a client-supplied label.
    """
    if not content:
        return None
    for mime, signature in MAGIC_SIGNATURES.items():
        if content.startswith(signature):
            return mime
    return None


def validate_upload_metadata(filename: Optional[str], declared_mime: Optional[str]) -> str:
    """Validate the filename extension and declared MIME type against the allowlist.

    Both must be present, both must be allowed, and they must agree with each
    other.  Returns the normalised declared MIME type.
    """
    candidate = _validate_filename_shape(filename)

    extension = extension_of(candidate)
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedMediaTypeError(
            f"Unsupported file extension '{extension or '(none)'}'. Permitted: {sorted(ALLOWED_EXTENSIONS)}."
        )

    if not declared_mime or not declared_mime.strip():
        raise UploadValidationError("A non-empty Content-Type is required for the uploaded document.")

    mime = declared_mime.split(";", 1)[0].strip().lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise UnsupportedMediaTypeError(
            f"Unsupported media type '{mime}'. Permitted: {sorted(ALLOWED_MIME_TYPES)}."
        )

    expected = EXTENSION_TO_MIME[extension]
    if mime != expected:
        raise UploadValidationError(
            f"Filename extension '{extension}' does not match declared media type '{mime}' "
            f"(expected '{expected}')."
        )
    return mime


def validate_content_signature(content: bytes, declared_mime: str) -> str:
    """Confirm the actual bytes match the declared document type.

    This is the content-based half of the type check: the existing
    ``inspect_and_validate_file_security`` re-validates per branch, and this
    guard makes the boundary itself independent of the declared label.
    """
    sniffed = sniff_mime_type(content)
    if sniffed is None:
        raise UnsupportedMediaTypeError(
            "Uploaded content does not match any supported document signature "
            f"({sorted(MAGIC_SIGNATURES)})."
        )
    if sniffed != declared_mime:
        raise UnsupportedMediaTypeError(
            f"Declared media type '{declared_mime}' does not match the actual content signature '{sniffed}'."
        )
    return sniffed


async def read_bounded_upload(
    file: Any,
    max_bytes: Optional[int] = None,
    *,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
    declared_size: Optional[int] = None,
) -> bytes:
    """Read an upload incrementally, enforcing ``max_bytes`` *while* reading.

    The body is never fully materialised: as soon as the accumulated size
    exceeds the limit, reading stops, the partial buffer is released, and
    :class:`UploadTooLargeError` is raised.  The caller remains responsible for
    closing ``file`` (see :func:`close_upload`).
    """
    limit = configured_max_upload_bytes() if max_bytes is None else int(max_bytes)
    if limit <= 0:
        raise ValueError("max_bytes must be a positive integer.")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be a positive integer.")

    # A declared Content-Length larger than the limit is rejected before any
    # bytes are read.  This is an optimisation only: the chunked loop below is
    # the authoritative check, because the declared size is client-controlled.
    if declared_size is not None and declared_size > limit:
        raise UploadTooLargeError(declared_size, limit)

    chunks: List[bytes] = []
    total = 0
    try:
        while True:
            chunk = await file.read(chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                # Stop reading immediately and drop what we already hold.
                del chunks[:]
                raise UploadTooLargeError(total, limit)
            chunks.append(chunk)
    except BaseException:
        del chunks[:]
        raise
    return b"".join(chunks)


async def close_upload(file: Any) -> None:
    """Close an ``UploadFile`` in a ``finally``-safe manner.

    Starlette's multipart parser backs uploads with a ``SpooledTemporaryFile``
    that rolls over to a real temporary file once the body exceeds 1 MiB, so
    closing it is what actually removes any on-disk partial artifact.
    """
    if file is None:
        return
    close = getattr(file, "close", None)
    if close is None:
        return
    try:
        result = close()
        if hasattr(result, "__await__"):
            await result
    except Exception:  # pragma: no cover - defensive: closing must never mask the real error
        pass


def cleanup_partial_upload(path: Optional[Path]) -> bool:
    """Remove a partially written temporary artifact, ignoring a missing file."""
    if path is None:
        return False
    try:
        target = Path(path)
        if target.is_file():
            target.unlink()
            return True
    except OSError:
        return False
    return False


def describe_upload_policy(max_bytes: Optional[int] = None) -> Dict[str, Any]:
    """Machine-readable description of the enforced upload policy."""
    limit = configured_max_upload_bytes() if max_bytes is None else int(max_bytes)
    return {
        "max_upload_bytes": limit,
        "chunk_bytes": UPLOAD_CHUNK_BYTES,
        "allowed_mime_types": sorted(ALLOWED_MIME_TYPES),
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
        "enforced_by": "application (chunked read); proxy limits are complementary only",
        "env_override": ENV_MAX_UPLOAD_BYTES,
    }


def upload_file_is_closed(file: Any) -> bool:
    """Whether the underlying binary stream of an ``UploadFile`` is closed."""
    inner = getattr(file, "file", None)
    if inner is None:
        return False
    return bool(getattr(inner, "closed", False))
