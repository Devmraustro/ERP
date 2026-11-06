# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 50 — F-R3-1: the WAL switch is the one start-up statement locks can kill.

A single CI failure on `9d5d112` (`test_phase49_audit_key_bootstrap_race`:
`sqlite3.OperationalError: database is locked`) was chased rather than filed as a
flake. The cause is not the audit key, not the filesystem and not test isolation:

    PRAGMA journal_mode = WAL        # dz_erp/database.py, in _create_connection

is the one statement SQLite refuses **immediately** when a peer holds a
``RESERVED`` lock — precisely what our own ``BEGIN IMMEDIATE`` migrations hold,
and what a peer still creating the schema holds. Measured on this host:

    peer holds RESERVED   -> "database is locked" in 0.00 s  (busy timeout ignored)
    peer holds EXCLUSIVE  -> waits for the lock, then succeeds

So the failure is a **start-up availability** failure on a database that is not
yet in WAL (a fresh database, or a legacy rollback-journal one). It cannot split
audit keys or lose audit events: it happens before any schema write, and the key
file publication is separately atomic (phase 49).

These tests pin the three properties the fix relies on, with stubs for the two
convergence cases (deterministic, no timing) and real processes for the
end-to-end ones.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dz_erp import database as db_module
from dz_erp.database import (
    WAL_SWITCH_ATTEMPTS,
    ERPDatabase,
    _ensure_wal_journal,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _FakeConnection:
    """A connection that answers the two pragmas from a script.

    Only the two statements the helper issues are modelled, so the test states
    exactly which sequence it is exercising instead of pretending to be SQLite.
    """

    def __init__(self, modes: list[str], switch_errors: list[Exception | None]):
        self.modes = list(modes)
        self.switch_errors = list(switch_errors)
        self.switch_calls = 0
        self.mode_reads = 0

    def execute(self, sql: str):
        if sql.strip().upper().startswith("PRAGMA JOURNAL_MODE = WAL"):
            index = min(self.switch_calls, len(self.switch_errors) - 1)
            self.switch_calls += 1
            error = self.switch_errors[index]
            if error is not None:
                raise error
            return None
        self.mode_reads += 1
        mode = self.modes[min(self.mode_reads - 1, len(self.modes) - 1)]
        return _FakeCursor(mode)


class _FakeCursor:
    def __init__(self, mode: str):
        self._mode = mode

    def fetchone(self):
        return (self._mode,)


# ---------------------------------------------------------------------------
# 1. the steady state: no switch is issued at all
# ---------------------------------------------------------------------------


def test_an_already_wal_database_never_issues_the_switch() -> None:
    """Every restart of a WAL deployment takes this path."""
    conn = _FakeConnection(modes=["wal"], switch_errors=[None])
    _ensure_wal_journal(conn)
    assert conn.switch_calls == 0, "the switch was attempted on a WAL database"


def test_a_wal_database_is_left_alone_even_if_switching_would_fail() -> None:
    conn = _FakeConnection(
        modes=["wal"], switch_errors=[sqlite3.OperationalError("database is locked")]
    )
    _ensure_wal_journal(conn)  # must not raise: nothing is attempted
    assert conn.switch_calls == 0


# ---------------------------------------------------------------------------
# 2. the contended case: a peer winning the switch is success, not failure
# ---------------------------------------------------------------------------


def test_a_peer_that_wins_the_switch_is_not_an_error() -> None:
    """We are refused, then discover the database is WAL: that is the goal met."""
    conn = _FakeConnection(
        modes=["delete", "wal"],
        switch_errors=[sqlite3.OperationalError("database is locked")],
    )
    _ensure_wal_journal(conn)
    assert conn.switch_calls == 1


def test_a_switch_that_succeeds_on_a_retry_is_not_an_error() -> None:
    """The holder releases before our budget is spent; our own switch then wins."""
    conn = _FakeConnection(
        modes=["delete", "delete"],
        switch_errors=[sqlite3.OperationalError("database is locked"), None],
    )
    _ensure_wal_journal(conn)
    assert conn.switch_calls == 2


# ---------------------------------------------------------------------------
# 3. a lock that never clears still fails loudly, and within a bounded budget
# ---------------------------------------------------------------------------


def test_a_persistent_holder_still_fails_loudly_and_bounded(monkeypatch) -> None:
    """The budget is bounded, the error is the original one, nothing is swallowed."""
    monkeypatch.setattr(db_module, "WAL_SWITCH_BACKOFF_SECONDS", 0.0)
    conn = _FakeConnection(
        modes=["delete"], switch_errors=[sqlite3.OperationalError("database is locked")]
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _ensure_wal_journal(conn)
    assert conn.switch_calls == WAL_SWITCH_ATTEMPTS, conn.switch_calls


def test_a_non_lock_error_is_raised_immediately(monkeypatch) -> None:
    """A real failure (a corrupt file, say) must not be retried as contention."""
    monkeypatch.setattr(db_module, "WAL_SWITCH_BACKOFF_SECONDS", 0.0)
    conn = _FakeConnection(
        modes=["delete"], switch_errors=[sqlite3.OperationalError("file is not a database")]
    )
    with pytest.raises(sqlite3.OperationalError, match="not a database"):
        _ensure_wal_journal(conn)
    assert conn.switch_calls == 1


# ---------------------------------------------------------------------------
# 4. end to end, with real processes
# ---------------------------------------------------------------------------

_HOLDER = """
import sqlite3, sys, time
path, hold, switch = sys.argv[1], float(sys.argv[2]), sys.argv[3] == "switch"
conn = sqlite3.connect(path, isolation_level=None)
conn.execute("BEGIN IMMEDIATE")          # RESERVED — what a migrating peer holds
print("HELD", flush=True)
time.sleep(hold)
conn.execute("ROLLBACK")
if switch:
    conn.execute("PRAGMA journal_mode = WAL")
print("DONE", flush=True)
time.sleep(1.5)
"""


def _rollback_journal_database(tmp_path: Path) -> Path:
    """A real database carrying the schema, in a non-WAL journal mode."""
    path = tmp_path / "erp.sqlite3"
    ERPDatabase(str(path))
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode = DELETE")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    conn.close()
    return path


def _hold_then(tmp_path: Path, path: Path, hold: float, switch: bool):
    return subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(path), str(hold), "switch" if switch else "stay"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_startup_converges_when_a_peer_creates_the_database(tmp_path: Path, monkeypatch) -> None:
    """The exact CI shape: a peer holds RESERVED, then wins the WAL switch.

    The budget is widened here only so the rendezvous is deterministic on a
    loaded runner; the assertion is convergence, and it cannot mask the defect —
    without the fix this call fails at 0.00 s, long before the peer releases.
    """
    monkeypatch.setattr(db_module, "WAL_SWITCH_ATTEMPTS", 400)  # 10 s of rendezvous
    path = _rollback_journal_database(tmp_path)
    peer = _hold_then(tmp_path, path, 0.15, switch=True)
    try:
        assert peer.stdout.readline().strip() == "HELD"
        reopened = ERPDatabase(str(path))  # must converge, not raise
        assert reopened.audit_keyring.active_material
    finally:
        peer.communicate(timeout=60)
    conn = sqlite3.connect(str(path), isolation_level=None)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_a_holder_that_never_releases_fails_loudly_rather_than_hanging(tmp_path: Path) -> None:
    """No infinite wait: the original error surfaces once the budget is spent."""
    path = _rollback_journal_database(tmp_path)
    peer = _hold_then(tmp_path, path, 30.0, switch=False)
    try:
        assert peer.stdout.readline().strip() == "HELD"
        started = time.perf_counter()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            ERPDatabase(str(path))
        elapsed = time.perf_counter() - started
    finally:
        peer.kill()
        peer.communicate(timeout=60)
    budget = WAL_SWITCH_ATTEMPTS * db_module.WAL_SWITCH_BACKOFF_SECONDS
    assert elapsed < budget + 10.0, f"startup waited {elapsed:.1f}s for a 30 s holder"


def test_concurrent_first_starts_all_open(tmp_path: Path) -> None:
    """The integration shape of the CI test, on a fresh database, many workers."""
    path = tmp_path / "fresh.sqlite3"
    child = (
        "import sys\n"
        "from dz_erp.database import ERPDatabase\n"
        "ERPDatabase(sys.argv[1])\n"
        "print('OPENED')\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(REPO_ROOT),
        )
        for _ in range(6)
    ]
    outcomes = [(p.communicate(timeout=180), p.returncode) for p in procs]
    failures = [
        (out.strip()[:80], err.strip().splitlines()[-1][:160] if err.strip() else "")
        for (out, err), code in outcomes
        if code != 0 or not out.strip().startswith("OPENED")
    ]
    assert failures == [], failures
    conn = sqlite3.connect(str(path), isolation_level=None)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    key_files = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".audit.key"))
    assert key_files == ["fresh.sqlite3.audit.key"], key_files
    conn.close()
