# SPDX-License-Identifier: LGPL-3.0-only
"""Generation-based restore activation with durable promotion journal.

Ensures that restore promotion is recoverable after interruption.
Uses a journal file to track promotion state.
"""

from __future__ import annotations

import json
import os
import time
try:
    import fcntl
except ImportError:
    fcntl = None
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
        tmp.write_text(__import__("json").dumps(data, indent=2), encoding="utf-8")
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
    gen_id = "gen_%d" % int(time.time() * 1000)
    journal_path = journal_dir / (".restore_journal_" + gen_id + ".json")
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


class RestoreLock:
    """Cross-process restore lock using OS-level file locking."""

    def __init__(self, lock_path: Path, timeout_seconds: float = 30.0):
        self.lock_path = lock_path
        self.timeout = timeout_seconds
        self._fd = None

    def acquire(self) -> bool:
        """Acquire the lock. Returns True on success, False on timeout."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.lock_path, "w")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    if fcntl is not None:
                        fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        # Windows: use msvcrt (already handled above)
                        pass
                return True
            except (IOError, OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    self._fd.close()
                    self._fd = None
                    return False
                time.sleep(0.1)

    def release(self) -> None:
        """Release the lock."""
        if self._fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    if fcntl is not None:
                        fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except (IOError, OSError):
                pass
            self._fd.close()
            self._fd = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError("Could not acquire restore lock within %s seconds" % self.timeout)
        return self

    def __exit__(self, *args):
        self.release()


def recover_promotion(journal: PromotionJournal) -> str:
    """Attempt to recover an interrupted promotion.

    Performs actual filesystem operations to complete or roll back.
    Returns a description of the action taken.
    """
    import shutil
    staging = Path(journal.staging_dir)
    active = Path(journal.active_dir)
    previous = Path(journal.previous_dir)

    if journal.state in (PromotionState.COMPLETE,):
        return "no recovery needed: already complete"

    if journal.state in (PromotionState.FAILED, PromotionState.ROLLBACK):
        # Clean up staging if it exists
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        return "no recovery needed: state=%s" % journal.state.value

    if journal.state == PromotionState.PENDING or journal.state == PromotionState.VALIDATING:
        # Validation was interrupted. Clean up staging, keep active.
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        journal.state = PromotionState.FAILED
        journal.error = "Validation interrupted; staging cleaned up"
        journal.save()
        return "rollback: validation interrupted, staging cleaned"

    if journal.state == PromotionState.PROMOTING:
        # Promotion was interrupted. Determine current state.
        if staging.is_dir() and not active.exists():
            # Staging exists but active doesn't — promotion didn't start.
            # Keep staging for retry, mark as pending.
            journal.state = PromotionState.PENDING
            journal.error = "Promotion not started; staging preserved for retry"
            journal.save()
            return "pending: promotion not started, staging preserved"
        elif active.exists() and staging.exists():
            # Both exist — promotion was interrupted between renames.
            # Verify active; if valid, keep it and clean up staging.
            try:
                import sqlite3
                conn = sqlite3.connect(str(active / "erp_snapshot.sqlite3"))
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if integrity and integrity[0] == "ok":
                    # Active is valid — keep it, clean up staging
                    shutil.rmtree(staging, ignore_errors=True)
                    journal.state = PromotionState.COMPLETE
                    journal.save()
                    return "complete: active verified valid, staging cleaned"
                else:
                    # Active is invalid — roll back to previous
                    if previous.exists():
                        if active.exists():
                            shutil.rmtree(active, ignore_errors=True)
                        os.rename(previous, active)
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
                    journal.state = PromotionState.ROLLBACK
                    journal.error = "Active invalid; rolled back to previous"
                    journal.save()
                    return "rollback: active invalid, restored previous"
            except Exception as exc:
                # Can't verify — fail closed
                journal.state = PromotionState.FAILED
                journal.error = "Verification failed during recovery: %s" % type(exc).__name__
                journal.save()
                return "failed: verification error"
        elif not staging.exists() and active.exists():
            # Staging gone, active exists — promotion likely completed.
            # Verify active before marking complete.
            try:
                import sqlite3
                conn = sqlite3.connect(str(active / "erp_snapshot.sqlite3"))
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if integrity and integrity[0] == "ok":
                    journal.state = PromotionState.COMPLETE
                    journal.save()
                    return "complete: promotion finished and verified"
                else:
                    # Active is invalid — try previous
                    if previous.exists():
                        if active.exists():
                            shutil.rmtree(active, ignore_errors=True)
                        os.rename(previous, active)
                        journal.state = PromotionState.ROLLBACK
                        journal.error = "Active invalid after promotion; rolled back"
                        journal.save()
                        return "rollback: active invalid, restored previous"
                    else:
                        journal.state = PromotionState.FAILED
                        journal.error = "Active invalid and no previous generation"
                        journal.save()
                        return "failed: no recoverable generation"
            except Exception as exc:
                journal.state = PromotionState.FAILED
                journal.error = "Verification failed: %s" % type(exc).__name__
                journal.save()
                return "failed: verification error"
        else:
            # Neither staging nor active exists
            if previous.exists():
                os.rename(previous, active)
                journal.state = PromotionState.ROLLBACK
                journal.error = "Neither staging nor active existed; restored previous"
                journal.save()
                return "rollback: restored previous generation"
            else:
                journal.state = PromotionState.FAILED
                journal.error = "No recoverable generation found"
                journal.save()
                return "failed: no recoverable generation"

    if journal.state == PromotionState.VERIFYING:
        # Verification was interrupted. Re-verify active.
        if active.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(active / "erp_snapshot.sqlite3"))
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if integrity and integrity[0] == "ok":
                    journal.state = PromotionState.COMPLETE
                    journal.save()
                    return "complete: verification succeeded on retry"
                else:
                    if previous.exists():
                        if active.exists():
                            import shutil
                            shutil.rmtree(active, ignore_errors=True)
                        os.rename(previous, active)
                        journal.state = PromotionState.ROLLBACK
                        journal.error = "Verification failed on retry; rolled back"
                        journal.save()
                        return "rollback: verification failed, restored previous"
                    journal.state = PromotionState.FAILED
                    journal.error = "Verification failed and no previous generation"
                    journal.save()
                    return "failed: verification failed, no rollback available"
            except Exception as exc:
                journal.state = PromotionState.FAILED
                journal.error = "Verification error: %s" % type(exc).__name__
                journal.save()
                return "failed: verification error"
        else:
            if previous.exists():
                os.rename(previous, active)
                journal.state = PromotionState.ROLLBACK
                journal.error = "Active missing during verification; restored previous"
                journal.save()
                return "rollback: active missing, restored previous"
            journal.state = PromotionState.FAILED
            journal.error = "Active missing and no previous generation"
            journal.save()
            return "failed: no recoverable generation"

    return "no recovery needed: state=%s" % journal.state.value


def cleanup_promotion(journal: PromotionJournal, keep_previous: bool = False) -> None:
    """Clean up promotion artifacts.

    If keep_previous is True, the previous generation is preserved.
    """
    staging = Path(journal.staging_dir)
    previous = Path(journal.previous_dir)

    # Clean staging
    if staging.exists():
        import shutil
        shutil.rmtree(staging, ignore_errors=True)

    # Clean previous if not keeping
    if not keep_previous and previous.exists():
        import shutil
        shutil.rmtree(previous, ignore_errors=True)

    # Remove journal
    journal.delete()
