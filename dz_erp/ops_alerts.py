"""Phase 10 - operational alerting for backup, disk and integrity events.

The project's DR design has always named the events that must page a human; until now
there was no dispatcher for them on this branch. This module is deliberately small and
dependency-free: one function, two providers (stderr JSON and an HTTP webhook), and a
hard rule that a payload never contains a secret or a customer's data.

Why stderr rather than a log file: the process that raises these alerts is usually a
systemd ``oneshot`` unit, so stderr already lands in the journal (a durable, rotated
sink). Writing our own file would create a second, unrotated one.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

#: Events an operator must be able to alert on. Values are stable identifiers.
BACKUP_FAILED = "BACKUP_FAILED"
BACKUP_STALE = "BACKUP_STALE"
BACKUP_INTEGRITY_FAILED = "BACKUP_INTEGRITY_FAILED"
RETENTION_FAILED = "RETENTION_FAILED"
DISK_LOW = "DISK_LOW"
READINESS_FAILED = "READINESS_FAILED"
OCR_UNAVAILABLE = "OCR_UNAVAILABLE"

ALL_EVENTS = (
    BACKUP_FAILED,
    BACKUP_STALE,
    BACKUP_INTEGRITY_FAILED,
    RETENTION_FAILED,
    DISK_LOW,
    READINESS_FAILED,
    OCR_UNAVAILABLE,
)

WEBHOOK_ENV = "ERP_ALERT_WEBHOOK"
WEBHOOK_TIMEOUT_SECONDS = 5.0


def build_payload(
    event: str,
    detail: str = "",
    *,
    fields: Optional[Dict[str, Any]] = None,
    severity: str = "error",
) -> Dict[str, Any]:
    """Assemble an alert payload.

    ``detail`` is written by us and is never built from user input; ``fields`` may only
    carry counters and statuses. Nothing in the payload is a credential, a document, or
    a person's data - the webhook may be a third-party service.
    """
    payload: Dict[str, Any] = {
        "event": event,
        "severity": severity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
        "service": "algerian-ai-erp",
    }
    if fields:
        payload["fields"] = fields
    return payload


def _post_webhook(url: str, payload: Dict[str, Any]) -> bool:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "dz-erp-ops/phase10"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def emit(
    event: str,
    detail: str = "",
    *,
    fields: Optional[Dict[str, Any]] = None,
    severity: str = "error",
    stream: Any = None,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Send one alert to every configured sink and return a delivery report.

    A failed webhook delivery is reported in the result but never raised: losing the
    alert channel must not also lose the backup that was being reported.
    """
    if event not in ALL_EVENTS:
        raise ValueError(f"unknown alert event: {event!r}")
    payload = build_payload(event, detail, fields=fields, severity=severity)
    sink = stream if stream is not None else sys.stderr
    line = json.dumps(payload, sort_keys=True)
    delivered = {"stderr": False, "webhook": False}
    try:
        sink.write(line + "\n")
        sink.flush()
        delivered["stderr"] = True
    except (OSError, ValueError):
        delivered["stderr"] = False

    url = webhook_url if webhook_url is not None else os.environ.get(WEBHOOK_ENV, "")
    if url:
        delivered["webhook"] = _post_webhook(url, payload)
    return {"payload": payload, "delivered": delivered, "webhook_configured": bool(url)}
