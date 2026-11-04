# SPDX-License-Identifier: LGPL-3.0-only
"""Disaster recovery metrics: RPO/RTO targets and measured results."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class DRTargets:
    """Configurable RPO/RTO targets."""
    rpo_seconds: float = 86400.0  # 24 hours default
    rto_seconds: float = 3600.0   # 1 hour default

    @classmethod
    def from_env(cls) -> "DRTargets":
        rpo = float(os.environ.get("ERP_RPO_TARGET_SECONDS", "86400"))
        rto = float(os.environ.get("ERP_RTO_TARGET_SECONDS", "3600"))
        return cls(rpo_seconds=rpo, rto_seconds=rto)


@dataclass
class DRMeasurement:
    """Measured durations for a DR drill."""
    backup_start: Optional[str] = None
    backup_end: Optional[str] = None
    backup_duration_seconds: float = 0.0
    upload_duration_seconds: float = 0.0
    restore_start: Optional[str] = None
    restore_end: Optional[str] = None
    restore_duration_seconds: float = 0.0
    total_recovery_seconds: float = 0.0
    recovery_point_timestamp: Optional[str] = None
    data_loss_window_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backup_start": self.backup_start,
            "backup_end": self.backup_end,
            "backup_duration_seconds": self.backup_duration_seconds,
            "upload_duration_seconds": self.upload_duration_seconds,
            "restore_start": self.restore_start,
            "restore_end": self.restore_end,
            "restore_duration_seconds": self.restore_duration_seconds,
            "total_recovery_seconds": self.total_recovery_seconds,
            "recovery_point_timestamp": self.recovery_point_timestamp,
            "data_loss_window_seconds": self.data_loss_window_seconds,
        }


@dataclass
class DRResult:
    """Complete DR drill result with compliance assessment."""
    measurement: DRMeasurement
    targets: DRTargets
    backup_verified: bool = False
    upload_verified: bool = False
    restore_verified: bool = False
    integrity_verified: bool = False
    trial_balance_verified: bool = False
    audit_chains_verified: bool = False
    vault_verified: bool = False
    rpo_compliant: bool = False
    rto_compliant: bool = False
    overall_pass: bool = False
    limitations: list = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "measurement": self.measurement.to_dict(),
            "targets": {
                "rpo_seconds": self.targets.rpo_seconds,
                "rto_seconds": self.targets.rto_seconds,
            },
            "verification": {
                "backup_verified": self.backup_verified,
                "upload_verified": self.upload_verified,
                "restore_verified": self.restore_verified,
                "integrity_verified": self.integrity_verified,
                "trial_balance_verified": self.trial_balance_verified,
                "audit_chains_verified": self.audit_chains_verified,
                "vault_verified": self.vault_verified,
            },
            "compliance": {
                "rpo_compliant": self.rpo_compliant,
                "rto_compliant": self.rto_compliant,
                "overall_pass": self.overall_pass,
            },
            "limitations": self.limitations,
            "error": self.error,
        }

    def compute_compliance(self) -> None:
        """Compute RPO/RTO compliance from measurements."""
        # RPO: data loss window must be <= target
        self.rpo_compliant = (
            self.measurement.data_loss_window_seconds <= self.targets.rpo_seconds
            and self.measurement.data_loss_window_seconds >= 0
        )
        # RTO: total recovery time must be <= target
        self.rto_compliant = (
            self.measurement.total_recovery_seconds <= self.targets.rto_seconds
            and self.measurement.total_recovery_seconds >= 0
        )
        # Overall pass requires all verifications + compliance
        self.overall_pass = (
            self.backup_verified
            and self.restore_verified
            and self.integrity_verified
            and self.trial_balance_verified
            and self.audit_chains_verified
            and self.rpo_compliant
            and self.rto_compliant
        )


class Timer:
    """Simple timer for measuring durations."""

    def __init__(self) -> None:
        self._start: Optional[float] = None
        self._end: Optional[float] = None

    def start(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def stop(self) -> float:
        self._end = time.monotonic()
        if self._start is None:
            return 0.0
        return self._end - self._start

    @property
    def elapsed(self) -> float:
        if self._start is None:
            return 0.0
        end = self._end if self._end is not None else time.monotonic()
        return end - self._start

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
