# SPDX-License-Identifier: LGPL-3.0-only
"""Generation-based restore activation with durable promotion journal.

Ensures that restore promotion is recoverable after interruption.
Uses a journal file to track promotion state.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional


class PromotionState(str, Enum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    PROMOTING = "PROMOTING"
    VERIFYING = "VERIFYING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    ROLLBACK = "ROLLBACK"


@dataclass
class PromotionJournal:
    """Durable journal for tracking restore promotion state."""
    journal_path: Path
    generation_id: str
    staging_dir: str
    active_dir: str
    previous_dir: str
    state: PromotionState = PromotionState.PENDING
    created_at: str = ""
    updated_at: str = ""
    error: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def save(self) -> None:
        """Persist journal to disk atomically."""
        self.updated_at = datetime.now(timezone.utc).isoformat()
        data = {
            "generation_id": self.generation_id,
            "staging_dir": self.staging_dir,
            "active_dir": self.active_dir,
            "previous_dir": self.previous_dir,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
        tmp = self.journal_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.journal_path)

    @classmethod
    def load(cls, journal_path: Path) -> Optional["PromotionJournal"]:
        """Load journal from disk, or None if not found."""
        if not journal_path.is_file():
            return None
        try:
            data = json.loads(journal_path.read_text(encoding="utf-8"))
            return cls(
                journal_path=journal_path,
                generation_id=data["generation_id"],
                staging_dir=data["staging_dir"],
                active_dir=data["active_dir"],
                previous_dir=data["previous_dir"],
                state=PromotionState(data["state"]),
                created_at=data.get("created_at", ""),
                updated_at=data.get("updated_at", ""),
                error=data.get("error", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def delete(self) -> None:
        """Remove the journal file."""
        self.journal_path.unlink(missing_ok=True)


def create_promotion_journal(
    staging_dir: Path,
    active_dir: Path,
    previous_dir: Path,
    journal_dir: Optional[Path] = None,
) -> PromotionJournal:
    """Create a new promotion journal."""
    if journal_dir is None:
        journal_dir = active_dir.parent
    journal_dir.mkdir(parents=True, exist_ok=True)
    gen_id = f"gen_{int(time.time() * 1000)}"
    journal_path = journal_dir / f".restore_journal_{gen_id}.json"
    return PromotionJournal(
        journal_path=journal_path,
        generation_id=gen_id,
        staging_dir=str(staging_dir),
        active_dir=str(active_dir),
        previous_dir=str(previous_dir),
    )


def detect_incomplete_promotions(journal_dir: Path) -> List[PromotionJournal]:
    """Find any incomplete promotion journals in the directory."""
    result = []
    if not journal_dir.is_dir():
        return result
    for p in journal_dir.glob(".restore_journal_*.json"):
        journal = PromotionJournal.load(p)
        if journal is not None and journal.state not in (PromotionState.COMPLETE, PromotionState.FAILED, PromotionState.ROLLBACK):
            result.append(journal)
    return result


def recover_promotion(journal: PromotionJournal) -> str:
    """Attempt to recover an interrupted promotion.

    Returns a description of the action taken.
    """
    staging = Path(journal.staging_dir)
    active = Path(journal.active_dir)

    if journal.state == PromotionState.PROMOTING:
        # Promotion was interrupted. Check if staging still exists.
        if staging.is_dir() and not active.exists():
            # Staging exists but active doesn't — promotion didn't complete.
            # Roll back: remove partial active, keep staging for retry.
            if active.exists():
                shutil.rmtree(active, ignore_errors=True)
            journal.state = PromotionState.FAILED
            journal.error = "Promotion interrupted; rolled back to previous state"
            journal.save()
            return "rollback: promotion interrupted, active restored"
        elif active.exists() and staging.exists():
            # Both exist — promotion may have partially completed. Verify.
            journal.state = PromotionState.VERIFYING
            journal.save()
            return "verifying: both staging and active exist"
        elif not staging.exists() and active.exists():
            # Staging gone, active exists — promotion likely completed.
            journal.state = PromotionState.COMPLETE
            journal.save()
            return "complete: promotion appears finished"
        else:
            journal.state = PromotionState.FAILED
            journal.error = "Unknown state during recovery"
            journal.save()
            return "failed: unknown state"

    if journal.state == PromotionState.VERIFYING:
        # Verification was interrupted. Re-verify.
        return "re-verifying: verification was interrupted"

    return f"no recovery needed: state={journal.state.value}"


def cleanup_promotion(journal: PromotionJournal, keep_previous: bool = False) -> None:
    """Clean up promotion artifacts.

    If keep_previous is True, the previous generation is preserved.
    """
    staging = Path(journal.staging_dir)
    previous = Path(journal.previous_dir)

    # Clean staging
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    # Clean previous if not keeping
    if not keep_previous and previous.exists():
        shutil.rmtree(previous, ignore_errors=True)

    # Remove journal
    journal.delete()
