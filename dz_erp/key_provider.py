# SPDX-License-Identifier: LGPL-3.0-only
"""Where key material comes from, and what can honestly be said about it.

The codebase reads its keys from files. That is a real limitation, not an
implementation detail: a `0600` file protects the key from other local accounts
and from nothing else - not from root, not from the service account itself, not
from a backup that copies the file, and not from a memory dump of a running
process. This module makes the choice explicit and *inspectable* rather than
implicit, so that:

* every key read goes through one place, which checks custody and refuses
  symlinks;
* what the deployment actually has can be reported (`describe()`), including the
  blunt `hsm_equivalent: False`, so no risk register can quietly assume more;
* substituting a real KMS/HSM later is a matter of providing another
  implementation of the same protocol, not of finding every `read_bytes()`.

Providers
---------
``FileKeyProvider``
    Key material in a file on the local filesystem. POSIX opens with ``O_NOFOLLOW``;
    Windows opens the reparse-point object with ``FILE_FLAG_OPEN_REPARSE_POINT`` and
    refuses it before transferring the same handle to Python. POSIX mode checks are
    made on the opened descriptor; Windows ACLs must be configured separately.

``EnvironmentKeyProvider``
    Key material from an environment variable. Useful for containers and CI
    where the orchestrator injects secrets. It is *not* more secure than a file:
    environment variables leak into crash dumps, `/proc/<pid>/environ` for the
    same user, and child processes. It is offered because it is a real deployment
    pattern, and documented as such.

``KmsKeyProvider``
    The production target: a protocol, deliberately not implemented. There is no
    HSM or KMS integration in this build, and pretending otherwise by shipping a
    stub that returns a locally-generated key would be worse than the honest
    absence. See ``docs/KEY_CUSTODY_THREAT_MODEL.md`` for what a real
    implementation must provide.

Nothing here claims to protect key material in memory: a running process holds the
active key by necessity, and that is stated in the threat model.
"""

from __future__ import annotations

import base64
import binascii
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .key_rotation import (
    KeyConfigurationError,
    KeyCustodyError,
    atomic_write_bytes,
)

KEY_DIR_MODE = 0o700
_KEY_FILE_MODE = 0o600


class KeyMaterialUnavailable(KeyConfigurationError):
    """The provider cannot supply the key material it was asked for."""


@runtime_checkable
class KeyProvider(Protocol):
    """Minimal interface a key source must satisfy.

    Deliberately narrow. A provider returns bytes for the *current* key and can
    describe itself; it does not enumerate versions (that is the ``KeyRing``'s
    job) and it does not decide which version is active.
    """

    def read(self) -> bytes: ...

    def describe(self) -> Dict[str, Any]: ...

    def reload(self) -> None: ...


def _open_windows_file_no_follow(path: Path) -> int:
    """Open a Windows file without following a symlink or other reparse point.

    Windows does not expose POSIX O_NOFOLLOW. FILE_FLAG_OPEN_REPARSE_POINT opens
    the link object itself, and FILE_ATTRIBUTE_TAG_INFO is inspected on that same
    handle before it is transferred to a Python file descriptor. Refuse every
    reparse point rather than attempting to follow or interpret it.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    file_attribute_tag_info_class = 9  # FILE_INFO_BY_HANDLE_CLASS.FileAttributeTagInfo

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        str(path),
        generic_read,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid_handle_value = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid_handle_value:
        error = ctypes.get_last_error()
        message = ctypes.FormatError(error).strip()
        if error in {2, 3}:  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
            raise FileNotFoundError(error, message, str(path))
        raise OSError(error, f"CreateFileW failed: {message}", str(path))

    try:
        info = FileAttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            file_attribute_tag_info_class,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            message = ctypes.FormatError(error).strip()
            raise OSError(error, f"GetFileInformationByHandleEx failed: {message}", str(path))

        if info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise KeyConfigurationError(
                f"Refusing to read key material through a Windows reparse point: {path}."
            )

        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        handle = None  # The file descriptor now owns the Windows handle.
        return descriptor
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)


class FileKeyProvider:
    """Key material from an owner-only file, refusing symlinks/reparse points."""

    def __init__(self, path: Path, *, check_custody: bool = True) -> None:
        self.path = Path(path)
        self._check_custody = check_custody
        self._cached: Optional[bytes] = None
        self._last_error: Optional[str] = None

    # -- reading -------------------------------------------------------
    def read(self) -> bytes:
        if self._cached is not None:
            return self._cached
        try:
            if os.name == "nt":
                fd = _open_windows_file_no_follow(self.path)
            else:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(str(self.path), flags)
        except FileNotFoundError as exc:
            raise KeyMaterialUnavailable(f"Key file {self.path} does not exist.") from exc
        except KeyConfigurationError:
            raise
        except OSError as exc:
            if getattr(exc, "errno", None) in {40, 62}:  # ELOOP (Linux) / EMLINK-ish (macOS)
                raise KeyConfigurationError(
                    f"Refusing to read key material through a symlink: {self.path}. "
                    "A symlinked key can be redirected by anyone who can replace the link."
                ) from exc
            raise KeyMaterialUnavailable(f"Cannot open key file {self.path}: {exc}") from exc

        try:
            # Verify the mode of the descriptor we are about to read, not of a
            # path that could be swapped in between. This closes the TOCTOU gap
            # between "the file was 0600 when checked" and "the file we read".
            st = os.fstat(fd)
            if stat.S_ISLNK(st.st_mode):  # pragma: no cover - kernel refuses first
                raise KeyConfigurationError(f"Refusing to read key material through a symlink: {self.path}")
            if self._check_custody and os.name == "posix":
                mode = stat.S_IMODE(st.st_mode)
                if mode & 0o077:
                    raise KeyCustodyError(
                        f"Refusing to use the key file {self.path}: mode {oct(mode)} grants access "
                        f"beyond the owner. Restrict it with `chmod 600 {self.path}`."
                    )
            with os.fdopen(fd, "rb") as handle:
                fd = -1  # fdopen owns it now
                material = handle.read().strip()
        finally:
            if fd >= 0:  # pragma: no cover - only when an error occurred above
                os.close(fd)

        if not material:
            raise KeyMaterialUnavailable(f"Key file {self.path} is empty.")
        self._cached = material
        self._last_error = None
        return material

    def reload(self) -> None:
        """Forget the cached material so the next read sees a rotation on disk."""
        self._cached = None

    # -- writing -------------------------------------------------------
    def write(self, material: bytes) -> None:
        """Atomically write key material with owner-only permissions."""
        ensure_key_directory(self.path.parent)
        atomic_write_bytes(self.path, material, mode=_KEY_FILE_MODE)
        self._cached = material

    # -- description ---------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """What this provider is, in terms an auditor can check.

        Contains no key material - only its location and the metadata around it.
        """
        info: Dict[str, Any] = {
            "provider": "file",
            "path": str(self.path),
            "exists": self.path.exists(),
            "is_symlink": self.path.is_symlink(),
            "backupable": True,
            "hsm_equivalent": False,
            "custody_checked": self._check_custody,
            "key_material_exportable": True,
        }
        try:
            st = self.path.lstat()
        except OSError:
            return info
        info["mode"] = oct(stat.S_IMODE(st.st_mode))
        info["size_bytes"] = st.st_size
        if os.name == "posix":
            info["owner_uid"] = st.st_uid
            info["owner_gid"] = st.st_gid
            info["mode_enforced"] = True
        else:  # pragma: no cover - Windows
            info["mode_enforced"] = False
            info["note"] = "mode bits are not access control on this platform; restrict the directory ACL"
        return info


class EnvironmentKeyProvider:
    """Key material from an environment variable.

    Offered because secret-injection is a common deployment pattern, not because
    it is stronger than a file. An environment variable is visible to the same
    account via ``/proc/<pid>/environ``, is inherited by child processes, and
    commonly lands in crash dumps and orchestration logs.
    """

    def __init__(self, variable: str, *, encoding: str = "base64") -> None:
        self.variable = variable
        self.encoding = encoding
        self._cached: Optional[bytes] = None

    def read(self) -> bytes:
        if self._cached is not None:
            return self._cached
        raw = os.environ.get(self.variable)
        if not raw:
            raise KeyMaterialUnavailable(
                f"Environment variable {self.variable} is not set or empty. "
                "The service must not fall back to a generated key: existing data would become unreadable."
            )
        if self.encoding == "base64":
            try:
                material = base64.urlsafe_b64decode(raw)
            except (binascii.Error, ValueError) as exc:
                raise KeyConfigurationError(
                    f"{self.variable} is not valid base64; key material must be a base64-encoded Fernet key."
                ) from exc
        elif self.encoding == "hex":
            try:
                material = bytes.fromhex(raw)
            except ValueError as exc:
                raise KeyConfigurationError(f"{self.variable} is not valid hex.") from exc
        else:  # pragma: no cover - guarded by callers
            raise KeyConfigurationError(f"Unsupported key material encoding {self.encoding!r}.")
        if not material:
            raise KeyMaterialUnavailable(f"{self.variable} decoded to empty key material.")
        self._cached = material
        return material

    def reload(self, environ: Optional[Dict[str, str]] = None) -> None:
        self._cached = None
        if environ is not None:
            if environ.get(self.variable):
                os.environ[self.variable] = environ[self.variable]
            else:
                os.environ.pop(self.variable, None)

    def write(self, material: bytes) -> None:  # pragma: no cover - not a write target
        raise KeyConfigurationError(
            f"Key material cannot be persisted to the environment by this process ({self.variable}). "
            "Write it through the orchestrator's secret store instead."
        )

    def describe(self) -> Dict[str, Any]:
        present = bool(os.environ.get(self.variable))
        return {
            "provider": "environment",
            "variable": self.variable,
            "encoding": self.encoding,
            "exists": present,
            "is_symlink": False,
            "backupable": False,
            "hsm_equivalent": False,
            "custody_checked": False,
            "key_material_exportable": True,
            "note": (
                "Environment variables are visible to the same account via /proc and are "
                "inherited by child processes; this is not stronger than an owner-only file."
            ),
        }


class KmsKeyProvider:
    """Production target: key material held by a KMS or HSM.

    **Not implemented.** No KMS/HSM client is bundled, and no cloud SDK is a
    dependency of this project. This class exists so that the gap is a documented
    interface rather than a vague aspiration, and so that ``describe()`` can
    state truthfully that hardware-backed custody is absent.

    A real implementation must satisfy, at minimum:

    * the key never leaves the service boundary in plaintext for normal
      operations (``hsm_equivalent: True`` requires wrap/unwrap or encrypt/decrypt
      inside the boundary, not a downloadable key);
    * *every* stored object remains decryptable after rotation, which means a
      per-version key reference is resolvable by the boundary;
    * rotation is itself authorised (two-person or policy-bound) and logged by
      the boundary, not only by this application;
    * key deletion is impossible without an explicit, audited, delayed process.

    Until one of these exists, ``describe()`` must keep returning
    ``hsm_equivalent: False``; a claim of hardware-backed custody that a
    `read_bytes()` call contradicts is worse than no claim.
    """

    def __init__(self, key_id: str) -> None:
        self.key_id = key_id

    def read(self) -> bytes:
        raise NotImplementedError(
            "KmsKeyProvider is not implemented: this build has no KMS/HSM client. "
            "Key material is file-based (see FileKeyProvider); the residual risk is "
            "documented in docs/KEY_CUSTODY_THREAT_MODEL.md."
        )

    def reload(self) -> None:
        return None

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": "kms",
            "key_id": self.key_id,
            "implemented": False,
            "backupable": False,
            "hsm_equivalent": False,
            "custody_checked": False,
            "key_material_exportable": False,
            "note": "No KMS/HSM integration is bundled; this provider cannot supply key material.",
        }


def ensure_key_directory(directory: Path) -> Path:
    """Create a key directory that other local accounts cannot traverse.

    ``0700`` on the directory matters as much as ``0600`` on the files: a
    world-executable directory lets another account enumerate names, and a
    world-readable one lets it copy files it cannot open - enough to mount an
    offline attack at leisure.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        current = stat.S_IMODE(directory.stat().st_mode)
        if current & 0o077:
            # Tighten rather than refuse: a directory that is too open is a
            # misconfiguration to fix in place, and the files inside it are the
            # keys this call is about to write.
            os.chmod(directory, current & 0o700 | KEY_DIR_MODE & 0o700)
    return directory


def provider_for(path: Path) -> FileKeyProvider:
    """The provider used for a given key file path."""
    return FileKeyProvider(path)
