# SPDX-License-Identifier: LGPL-3.0-only
"""Provider-neutral backup storage interface.

Defines the contract for backup storage providers (local, S3-compatible,
rsync, etc.) and implements the default local provider. Remote providers
are optional and live in separate modules.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Protocol, runtime_checkable


@dataclass(frozen=True)
class UploadResult:
    """Result of a successful backup upload."""
    remote_name: str
    local_path: str
    size_bytes: int
    sha256: str
    verified: bool


@dataclass(frozen=True)
class BackupMetadata:
    """Metadata for a stored backup."""
    name: str
    size_bytes: int
    created_at: str = ""
    sha256: str = ""
    verified: bool = False


@dataclass(frozen=True)
class VerifyResult:
    """Result of verifying a stored backup."""
    name: str
    verified: bool
    sha256: str = ""
    error: str = ""


@runtime_checkable
class BackupStorageProvider(Protocol):
    """Provider-neutral backup storage contract."""

    def upload(self, local_path: Path, remote_name: str) -> UploadResult:
        """Upload a backup. Must verify before reporting success."""
        ...

    def download(self, remote_name: str, local_path: Path) -> None:
        """Download a backup to local_path."""
        ...

    def list_backups(self) -> List[BackupMetadata]:
        """List available backups."""
        ...

    def verify(self, remote_name: str, expected_sha256: str) -> VerifyResult:
        """Verify a stored backup's integrity."""
        ...

    def delete(self, remote_name: str) -> None:
        """Delete a backup (subject to retention safeguards)."""
        ...


class LocalStorageProvider:
    """Default local-filesystem backup storage provider.

    Uses atomic writes (write to temp, then rename) and SHA-256 verification.
    """

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        """Reject path traversal in backup names."""
        if not name or "\x00" in name or "/" in name or "\\" in name:
            raise ValueError(f"Unsafe backup name: {name!r}")
        if name in (".", ".."):
            raise ValueError(f"Unsafe backup name: {name!r}")
        return name

    def upload(self, local_path: Path, remote_name: str) -> UploadResult:
        local_path = Path(local_path)
        name = self._safe_name(remote_name)
        if not local_path.is_file():
            raise FileNotFoundError(f"Local backup not found: {local_path}")
        data = local_path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        dest = self.storage_dir / name
        # Atomic write: temp file + rename
        fd, tmp = tempfile.mkstemp(dir=str(self.storage_dir), prefix=".tmp_")
        try:
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp, dest)
        except Exception:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        # Verify
        verified_data = dest.read_bytes()
        verified_sha = hashlib.sha256(verified_data).hexdigest()
        if verified_sha != sha:
            dest.unlink(missing_ok=True)
            raise IOError("Upload verification failed: SHA-256 mismatch")
        return UploadResult(
            remote_name=name,
            local_path=str(local_path),
            size_bytes=len(data),
            sha256=sha,
            verified=True,
        )

    def download(self, remote_name: str, local_path: Path) -> None:
        name = self._safe_name(remote_name)
        src = self.storage_dir / name
        if not src.is_file():
            raise FileNotFoundError(f"Backup not found: {name}")
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        # Atomic write
        fd, tmp = tempfile.mkstemp(dir=str(local_path.parent), prefix=".tmp_")
        try:
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp, local_path)
        except Exception:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def list_backups(self) -> List[BackupMetadata]:
        result = []
        for p in sorted(self.storage_dir.iterdir()):
            if p.is_file() and not p.name.startswith(".tmp_"):
                try:
                    data = p.read_bytes()
                    sha = hashlib.sha256(data).hexdigest()
                    stat = p.stat()
                    result.append(BackupMetadata(
                        name=p.name,
                        size_bytes=stat.st_size,
                        sha256=sha,
                        verified=True,
                    ))
                except OSError:
                    continue
        return result

    def verify(self, remote_name: str, expected_sha256: str) -> VerifyResult:
        name = self._safe_name(remote_name)
        path = self.storage_dir / name
        if not path.is_file():
            return VerifyResult(name=name, verified=False, error="File not found")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual == expected_sha256:
            return VerifyResult(name=name, verified=True, sha256=actual)
        return VerifyResult(name=name, verified=False, sha256=actual, error="SHA-256 mismatch")

    def delete(self, remote_name: str) -> None:
        name = self._safe_name(remote_name)
        path = self.storage_dir / name
        if path.is_file():
            path.unlink()
        elif path.exists():
            raise ValueError(f"Cannot delete non-file: {name}")


def apply_retention(
    provider: "BackupStorageProvider",
    *,
    max_count: int = 7,
    max_age_days: float = 30.0,
    min_verified: int = 1,
    dry_run: bool = False,
) -> dict:
    """Apply retention policy safely.

    Never deletes the only verified recoverable backup.
    Returns a summary of actions taken (or that would be taken in dry_run).
    """

    backups = provider.list_backups()
    # Only verified backups count as recoverable
    verified = [b for b in backups if b.verified]
    # Retention by count: sort by name (assumed chronological) and delete oldest beyond max_count
    sorted_backups = sorted(backups, key=lambda b: b.name)

    # Sort by name (assumed chronological) and delete oldest beyond max_count
    sorted_backups = sorted(backups, key=lambda b: b.name)
    # Determine which to delete: beyond max_count, excluding verified safety
    candidates = []
    if len(sorted_backups) > max_count:
        excess = len(sorted_backups) - max_count
        candidates = sorted_backups[:excess]

    # Filter: never delete if it would leave fewer than min_verified verified backups
    deleted = []
    skipped = []
    for b in candidates:
        remaining_verified = sum(1 for vb in verified if vb.name != b.name)
        if remaining_verified < min_verified:
            skipped.append(b.name)
            continue
        if not dry_run:
            try:
                provider.delete(b.name)
                deleted.append(b.name)
            except Exception:
                skipped.append(b.name)
        else:
            deleted.append(b.name)

    return {
        "total_backups": len(backups),
        "verified_backups": len(verified),
        "deleted": deleted,
        "skipped": skipped,
        "dry_run": dry_run,
    }
