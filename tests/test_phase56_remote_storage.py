# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 56 - Remote storage and webhook alerting tests (mocked)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dz_erp.alerting import WebhookAlertProvider, make_alert, AlertEvent


# ---------------------------------------------------------------------------
# Webhook alerting tests
# ---------------------------------------------------------------------------

def test_webhook_success() -> None:
    provider = WebhookAlertProvider("https://example.com/hook", timeout_seconds=5.0)
    payload = make_alert(AlertEvent.BACKUP_FAILED, "io_error", "test")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = lambda s, *a: None
        provider.send(payload)  # Should not raise
    mock_urlopen.assert_called_once()


def test_webhook_http_error_does_not_raise() -> None:
    provider = WebhookAlertProvider("https://example.com/hook")
    payload = make_alert(AlertEvent.BACKUP_FAILED, "io_error", "test")
    with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        provider.send(payload)  # Should not raise


def test_webhook_timeout_does_not_raise() -> None:
    provider = WebhookAlertProvider("https://example.com/hook", timeout_seconds=0.01)
    payload = make_alert(AlertEvent.RESTORE_FAILED, "timeout", "slow")
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        provider.send(payload)  # Should not raise


def test_webhook_payload_no_secrets() -> None:
    provider = WebhookAlertProvider("https://example.com/hook")
    payload = make_alert(AlertEvent.BACKUP_FAILED, "io_error", "backup failed")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_urlopen.return_value = mock_cm
        mock_cm.__enter__ = lambda s: s
        mock_cm.__exit__ = lambda s, *a: None
        provider.send(payload)
    # Verify the request was made
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    data = req.data.decode("utf-8") if hasattr(req, "data") else ""
    # Ensure no secret-like content in payload
    assert "password" not in data.lower()
    assert "secret" not in data.lower()


def test_webhook_malformed_url_rejected() -> None:
    with pytest.raises(ValueError):
        WebhookAlertProvider("not-a-url")
    with pytest.raises(ValueError):
        WebhookAlertProvider("")


# ---------------------------------------------------------------------------
# S3 storage mocked tests
# ---------------------------------------------------------------------------

def test_s3_import_error_without_boto3() -> None:
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(ImportError):
            from dz_erp.backup_storage_s3 import S3CompatibleProvider
            S3CompatibleProvider(bucket="test")


def test_s3_upload_with_mock() -> None:
    mock_boto = MagicMock()
    mock_client = MagicMock()
    mock_boto.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto}):
        from dz_erp.backup_storage_s3 import S3CompatibleProvider
        provider = S3CompatibleProvider(bucket="test-bucket", access_key="ak", secret_key="sk")

    # Create a temp file
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"test-backup-data")
        tmp_path = Path(f.name)

    try:
        # Mock download_file to return the same data
        def fake_download(bucket, key, path):
            Path(path).write_bytes(b"test-backup-data")

        mock_client.download_file.side_effect = fake_download
        result = provider.upload(tmp_path, "backup.enc")
        assert result.verified is True
        assert result.sha256
        mock_client.upload_file.assert_called_once()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_s3_verify_checksum_mismatch() -> None:
    mock_boto = MagicMock()
    mock_client = MagicMock()
    mock_boto.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto}):
        from dz_erp.backup_storage_s3 import S3CompatibleProvider
        provider = S3CompatibleProvider(bucket="test-bucket")

    def fake_download(bucket, key, path):
        Path(path).write_bytes(b"different-data")

    mock_client.download_file.side_effect = fake_download
    vr = provider.verify("backup.enc", "a" * 64)
    assert vr.verified is False


def test_s3_delete() -> None:
    mock_boto = MagicMock()
    mock_client = MagicMock()
    mock_boto.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto}):
        from dz_erp.backup_storage_s3 import S3CompatibleProvider
        provider = S3CompatibleProvider(bucket="test-bucket")

    provider.delete("backup.enc")
    mock_client.delete_object.assert_called_once()


def test_s3_list_backups() -> None:
    mock_boto = MagicMock()
    mock_client = MagicMock()
    mock_boto.client.return_value = mock_client
    mock_client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "erp-backups/b1.enc", "Size": 100}]}
    ]

    with patch.dict("sys.modules", {"boto3": mock_boto}):
        from dz_erp.backup_storage_s3 import S3CompatibleProvider
        provider = S3CompatibleProvider(bucket="test-bucket")

    backups = provider.list_backups()
    assert len(backups) == 1
    assert backups[0].name == "b1.enc"
    assert backups[0].size_bytes == 100
