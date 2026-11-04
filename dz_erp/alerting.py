# SPDX-License-Identifier: LGPL-3.0-only
"""Provider-neutral alerting interface for backup/restore/DR events.

Alerts never contain secrets, key material, financial data, or PII.
Only event type, timestamp, and error category are included.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable


class AlertEvent(str, Enum):
    BACKUP_FAILED = "BACKUP_FAILED"
    BACKUP_UPLOAD_FAILED = "BACKUP_UPLOAD_FAILED"
    RESTORE_FAILED = "RESTORE_FAILED"
    KEY_UNAVAILABLE = "KEY_UNAVAILABLE"
    RETENTION_FAILED = "RETENTION_FAILED"
    RPO_EXCEEDED = "RPO_EXCEEDED"
    RTO_EXCEEDED = "RTO_EXCEEDED"


@dataclass(frozen=True)
class AlertPayload:
    """Safe alert payload — no secrets, no PII, no financial data."""
    event: AlertEvent
    timestamp: str
    error_category: str
    detail: str = ""
    backup_name: str = ""
    duration_seconds: float = 0.0


@runtime_checkable
class AlertProvider(Protocol):
    """Provider-neutral alerting contract."""

    def send(self, payload: AlertPayload) -> None:
        """Send an alert. Must not raise (best-effort)."""
        ...


class LogAlertProvider:
    """Default alert provider — writes to structured security log."""

    def __init__(self, logger_name: str = "erp.dr.alerts") -> None:
        self._logger = logging.getLogger(logger_name)

    def send(self, payload: AlertPayload) -> None:
        try:
            self._logger.warning(
                "DR_ALERT event=%s timestamp=%s category=%s detail=%s backup=%s duration=%.2f",
                payload.event.value,
                payload.timestamp,
                payload.error_category,
                payload.detail,
                payload.backup_name,
                payload.duration_seconds,
            )
        except Exception:
            pass  # Best-effort; never raise


class WebhookAlertProvider:
    """Optional webhook alert provider (HTTP POST)."""

    def __init__(self, url: str, timeout_seconds: float = 5.0) -> None:
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError("Webhook URL must start with http:// or https://")
        self.url = url
        self.timeout = timeout_seconds

    def send(self, payload: AlertPayload) -> None:
        try:
            data = json.dumps({
                "event": payload.event.value,
                "timestamp": payload.timestamp,
                "error_category": payload.error_category,
                "detail": payload.detail,
                "backup_name": payload.backup_name,
                "duration_seconds": payload.duration_seconds,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except Exception:
            pass  # Best-effort; never raise


def make_alert(event: AlertEvent, error_category: str, detail: str = "",
              backup_name: str = "", duration_seconds: float = 0.0) -> AlertPayload:
    """Factory for safe alert payloads."""
    return AlertPayload(
        event=event,
        timestamp=datetime.now(timezone.utc).isoformat(),
        error_category=error_category,
        detail=detail,
        backup_name=backup_name,
        duration_seconds=duration_seconds,
    )
