# SPDX-License-Identifier: LGPL-3.0-only
"""Optional S3-compatible backup storage provider.

Requires boto3 (optional dependency). Supports MinIO, Cloudflare R2,
Backblaze B2, and other S3-compatible services.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from .backup_storage import BackupMetadata, UploadResult, VerifyResult


class S3CompatibleProvider:
    """S3-compatible backup storage (optional, requires boto3)."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: Optional[str] = None,
        region: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        prefix: str = "erp-backups/",
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for S3-compatible storage. "
                "Install with: pip install boto3"
            )
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        session_kwargs = {}
        if access_key and secret_key:
            session_kwargs["aws_access_key_id"] = access_key
            session_kwargs["aws_secret_access_key"] = secret_key
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            **session_kwargs,
        )

    def _key(self, name: str) -> str:
        return self.prefix + name

    def upload(self, local_path: Path, remote_name: str) -> UploadResult:
        local_path = Path(local_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"Local backup not found: {local_path}")
        data = local_path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        key = self._key(remote_name)
        self._client.upload_file(str(local_path), self.bucket, key)
        # Verify: re-download and check hash
        import tempfile
        tmp = Path(tempfile.mktemp())
        try:
            self._client.download_file(self.bucket, key, str(tmp))
            downloaded = tmp.read_bytes()
            if hashlib.sha256(downloaded).hexdigest() != sha:
                self._client.delete_object(Bucket=self.bucket, Key=key)
                raise IOError("Upload verification failed: SHA-256 mismatch after upload")
        finally:
            tmp.unlink(missing_ok=True)
        return UploadResult(
            remote_name=remote_name,
            local_path=str(local_path),
            size_bytes=len(data),
            sha256=sha,
            verified=True,
        )

    def download(self, remote_name: str, local_path: Path) -> None:
        key = self._key(remote_name)
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(local_path))

    def list_backups(self) -> List[BackupMetadata]:
        result = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(self.prefix):]
                if name:
                    result.append(BackupMetadata(
                        name=name,
                        size_bytes=obj["Size"],
                        verified=False,
                    ))
        return result

    def verify(self, remote_name: str, expected_sha256: str) -> VerifyResult:
        key = self._key(remote_name)
        import tempfile
        tmp = Path(tempfile.mktemp())
        try:
            self._client.download_file(self.bucket, key, str(tmp))
            data = tmp.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual == expected_sha256:
                return VerifyResult(name=remote_name, verified=True, sha256=actual)
            return VerifyResult(name=remote_name, verified=False, sha256=actual, error="SHA-256 mismatch")
        except Exception as e:
            return VerifyResult(name=remote_name, verified=False, error=str(e))
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, remote_name: str) -> None:
        key = self._key(remote_name)
        self._client.delete_object(Bucket=self.bucket, Key=key)
