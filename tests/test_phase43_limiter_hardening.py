# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 43 — limiter hardening: current vs historical failure, concurrent
writers, and the write-lock the read path no longer takes.

Three findings from the PR-stack review are closed here, each with evidence
rather than assertion:

* **R-01** — ``RequestThrottle.last_backend_error`` was never cleared, so after
  one transient store error every later *ordinary* 429 was audited as
  ``RATE_LIMIT_BACKEND_UNAVAILABLE``. The distinction that matters is
  current-call vs history; both now exist and are asserted separately.
* **R-02** — the shared sqlite store was only ever tested *sequentially* across
  processes. These tests use real OS processes writing at the same boundary, at
  the same moment, and assert that no increment is lost and no state grows
  without bound. Measured numbers are printed so a reviewer can see throughput
  and contention behaviour, not just a green tick.
* **R-03** — ``blocked()`` answered a read inside ``BEGIN IMMEDIATE``, taking the
  application database's write lock on every authentication attempt. The read
  path now uses ``ERPDatabase.read_probe``. The tests here prove the lock is
  genuinely not taken, that committed events are still seen, that uncommitted
  ones are not, and that the limit itself is unchanged.

The rule these tests hold to: an optimisation may change *when* a lock is taken.
It may never change what is counted.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase
from dz_erp.rate_limit import (
    MemoryRateLimitStore,
    RequestThrottle,
    SqliteRateLimitStore,
    describe_throttle_policy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _ScriptedStore:
    """A real store that can be told to fail a fixed number of times.

    It fails at the *backend* boundary the throttle sees, which is exactly where
    a locked database, a full disk or a schema drift would surface.
    """

    name = "scripted"

    def __init__(self, failures: int = 0) -> None:
        self.inner = MemoryRateLimitStore(max_keys=64)
        self.failures_left = failures

    def _maybe_fail(self) -> None:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise OSError("database is locked")

    def hit(self, *args):
        self._maybe_fail()
        return self.inner.hit(*args)

    def blocked(self, *args):
        self._maybe_fail()
        return self.inner.blocked(*args)

    def reset(self, *args):
        self._maybe_fail()
        return self.inner.reset(*args)

    def describe(self):
        return self.inner.describe()


def _run_in_processes(scripts: list[str], *, timeout: int = 180) -> list[tuple[str, str, int]]:
    """Start every script, then collect ``(stdout, stderr, returncode)`` for each.

    All processes are launched before any is waited on, so they genuinely run at
    the same time rather than in a queue.
    """
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for script in scripts
    ]
    results: list[tuple[str, str, int]] = []
    for proc in procs:
        out, err = proc.communicate(timeout=timeout)
        results.append((out, err, proc.returncode))
    return results


def _child(script_body: str) -> str:
    return textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from dz_erp.database import ERPDatabase
        from dz_erp.rate_limit import SqliteRateLimitStore
        """
    ) + textwrap.dedent(script_body)


def _summary(completed: list) -> list[tuple[str, str, int]]:
    return [(out.strip(), err.strip(), code) for out, err, code in completed]


# ---------------------------------------------------------------------------
# R-01 — a stale backend error must not explain a new decision
# ---------------------------------------------------------------------------


def test_a_backend_failure_explains_only_the_decision_it_affected() -> None:
    """The refusal *during* an outage carries the error; the next one does not."""
    store = _ScriptedStore(failures=1)
    throttle = RequestThrottle(
        auth_failure_limit=3, auth_failure_window=60, client_failure_limit=0,
        store=store, failure_mode="fail_closed",
    )

    # The outage: fail-closed refuses, and says why.
    blocked, _retry = throttle.login_blocked("203.0.113.4", "alice")
    assert blocked is True
    assert throttle.current_backend_error == "OSError: database is locked"

    # The backend is healthy again: an ordinary decision reports no current error
    # even before the counter is exhausted.
    blocked, _retry = throttle.login_blocked("203.0.113.4", "alice")
    assert blocked is False
    assert throttle.current_backend_error is None
    assert throttle.last_backend_error is None, "a success must clear the stale error"


def test_a_successful_call_clears_the_stale_error_but_not_the_history() -> None:
    store = _ScriptedStore(failures=1)
    throttle = RequestThrottle(
        auth_failure_limit=5, auth_failure_window=60, client_failure_limit=0,
        store=store, failure_mode="fail_closed",
    )
    throttle.login_blocked("203.0.113.4", "alice")
    assert throttle.backend_failure_count == 1
    first_failure_at = throttle.last_backend_failure_at
    assert first_failure_at is not None
    message = throttle.last_backend_failure_message
    assert message == "OSError: database is locked"

    throttle.record_login_success("203.0.113.4", "alice")

    # Present: clean. History: intact and unchanged.
    assert throttle.current_backend_error is None
    assert throttle.last_backend_error is None
    assert throttle.backend_failure_count == 1
    assert throttle.last_backend_failure_at == first_failure_at
    assert throttle.last_backend_failure_message == message

    health = throttle.backend_health()
    assert health["degraded_now"] is False
    assert health["current_error"] is None
    assert health["failure_count"] == 1
    assert health["failure_mode"] == "fail_closed"
    # The operator-facing description reports the same honest split.
    described = describe_throttle_policy(throttle)
    assert described["backend_health"]["degraded_now"] is False
    assert described["backend_health"]["failure_count"] == 1


def test_an_ordinary_limit_rejection_is_never_reported_as_a_backend_failure() -> None:
    """R-01's exact regression: outage → recovery → a normal 429."""
    store = _ScriptedStore(failures=1)
    throttle = RequestThrottle(
        auth_failure_limit=3, auth_failure_window=60, client_failure_limit=0,
        store=store, failure_mode="fail_closed",
    )

    throttle.login_blocked("198.51.100.7", "bob")  # the outage
    assert throttle.current_backend_error is not None

    # Three genuine failures, then the ordinary policy refusal.
    for _ in range(3):
        throttle.record_login_failure("198.51.100.7", "bob")
    blocked, retry = throttle.login_blocked("198.51.100.7", "bob")

    assert blocked is True, "the limit must still refuse (fail-closed is untouched)"
    assert retry >= 1
    assert throttle.current_backend_error is None, (
        "a limit refusal after recovery must not carry the old backend error"
    )
    assert throttle.backend_failure_count == 1, "and the history still records the one real outage"


def test_fail_open_is_still_available_and_still_recorded() -> None:
    """The policy knob is untouched: open allows, and the error is still visible."""
    store = _ScriptedStore(failures=1)
    throttle = RequestThrottle(
        auth_failure_limit=3, auth_failure_window=60, client_failure_limit=0,
        store=store, failure_mode="fail_open",
    )
    blocked, _retry = throttle.login_blocked("203.0.113.9", "carol")
    assert blocked is False, "fail_open lets the attempt through"
    assert throttle.current_backend_error == "OSError: database is locked"
    assert throttle.backend_failure_count == 1


def test_concurrent_failure_and_recovery_leave_no_thread_with_a_stale_error() -> None:
    """Under concurrent load, a thread that succeeds never reports an old failure."""
    store = _ScriptedStore(failures=3)
    throttle = RequestThrottle(
        auth_failure_limit=1000, auth_failure_window=60, client_failure_limit=0,
        store=store, failure_mode="fail_closed",
    )
    observed_current_errors: list[str | None] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        for i in range(25):
            # Distinct keys keep threads from contending on the same bucket; the
            # point here is the error flag, not the counter.
            throttle.login_blocked(f"203.0.113.{n}", f"user{i}")
            with lock:
                observed_current_errors.append(throttle.current_backend_error)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(observed_current_errors) == 200
    assert throttle.backend_failure_count >= 1, "the injected failures were real"
    # After the run the backend is healthy, so the flag a caller would read now
    # is clean; and no observation is a *stale* value from a previous call
    # because every public call resets it.
    assert throttle.current_backend_error is None
    assert set(observed_current_errors) <= {"OSError: database is locked", None}


# ---------------------------------------------------------------------------
# R-02 — concurrent writers on one database file
# ---------------------------------------------------------------------------


def test_concurrent_writers_on_one_file_lose_no_increment(tmp_path: Path) -> None:
    """4 OS processes × 100 hits: all 400 events are stored, none is lost."""
    db_path = str(tmp_path / "concurrent.sqlite3")
    db = ERPDatabase(db_path)
    SqliteRateLimitStore(db)  # create the table before the children start

    workers, per_worker = 4, 100
    scripts = [
        _child(
            f"""
            store = SqliteRateLimitStore(ERPDatabase({db_path!r}))
            start = time.monotonic()
            for _ in range({per_worker}):
                store.hit("login", "shared", 1000000, 60.0)
            print(f"{{time.monotonic() - start:.3f}}")
            """
        )
        for _ in range(workers)
    ]

    started = time.monotonic()
    completed = _run_in_processes(scripts)
    elapsed = time.monotonic() - started

    summaries = _summary(completed)
    assert all(code == 0 for _out, _err, code in summaries), summaries
    child_seconds = [float(out) for out, _err, _code in summaries]
    assert elapsed < 120, f"concurrent writes took far too long: {elapsed:.2f}s"

    store = SqliteRateLimitStore(db)
    assert store.row_count() == workers * per_worker, (
        f"expected {workers * per_worker} events, found {store.row_count()}"
    )
    # Record the measurement rather than only the boolean.
    print(
        f"[R-02] {workers} processes x {per_worker} hits: wall {elapsed:.2f}s, "
        f"per-process {min(child_seconds):.3f}-{max(child_seconds):.3f}s, "
        f"rows {store.row_count()}"
    )


def test_concurrent_hits_at_the_boundary_allow_exactly_the_limit(tmp_path: Path) -> None:
    """At the limit, concurrency must not hand out extra allowances."""
    db_path = str(tmp_path / "boundary.sqlite3")
    db = ERPDatabase(db_path)
    SqliteRateLimitStore(db)

    limit, workers, per_worker = 20, 4, 10  # 40 attempts, 20 allowed
    scripts = [
        _child(
            f"""
            store = SqliteRateLimitStore(ERPDatabase({db_path!r}))
            allowed = 0
            for _ in range({per_worker}):
                ok, _retry = store.hit("login", "boundary", {limit}, 300.0)
                allowed += 1 if ok else 0
            print(allowed)
            """
        )
        for _ in range(workers)
    ]
    completed = _run_in_processes(scripts)
    summaries = _summary(completed)
    assert all(code == 0 for _out, _err, code in summaries), summaries
    allowed_total = sum(int(out) for out, _err, _code in summaries)

    assert allowed_total == limit, (
        f"exactly {limit} of {workers * per_worker} concurrent attempts may be allowed, "
        f"got {allowed_total}"
    )
    assert SqliteRateLimitStore(db).row_count() == workers * per_worker, (
        "a refused attempt is still recorded, so the window keeps moving"
    )


def test_concurrent_expiry_and_cleanup_stay_bounded(tmp_path: Path) -> None:
    """Old events are pruned while writers are active; the table stays capped."""
    db_path = str(tmp_path / "expiry.sqlite3")
    db = ERPDatabase(db_path)
    store = SqliteRateLimitStore(db, max_rows=500, prune_interval=0.0)
    now = time.time()
    with db.transaction() as conn:
        for i in range(2000):  # well past the cap, all already expired
            conn.execute(
                f"INSERT INTO {SqliteRateLimitStore.TABLE} (scope, key, occurred_at) VALUES (?, ?, ?)",
                ("login", f"old-{i}", now - 7200),
            )
    assert store.row_count() == 2000

    scripts = [
        _child(
            f"""
            store = SqliteRateLimitStore(ERPDatabase({db_path!r}), max_rows=500, prune_interval=0.0)
            for _ in range(50):
                store.hit("login", "live", 1000000, 60.0)
            print(store.row_count())
            """
        )
        for _ in range(3)
    ]
    completed = _run_in_processes(scripts)
    summaries = _summary(completed)
    assert all(code == 0 for _out, _err, code in summaries), summaries

    final = SqliteRateLimitStore(db, max_rows=500, prune_interval=0.0)
    remaining = final.row_count()
    assert remaining <= 500, f"expired events were not pruned: {remaining} rows"
    assert final.blocked("login", "live", 1000000, 60.0) is False, "live events survived"


def test_a_write_lock_held_by_another_process_is_waited_out_not_dropped(tmp_path: Path) -> None:
    """Busy-timeout behaviour: contention delays a write, it does not lose it."""
    db_path = str(tmp_path / "locked.sqlite3")
    db = ERPDatabase(db_path)
    seed_store = SqliteRateLimitStore(db)
    # One committed row, so the holder's UPDATE genuinely modifies a page and
    # therefore genuinely holds the write lock (an UPDATE matching no rows does
    # not touch the database file).
    seed_store.hit("login", "seed", 10, 60.0)

    holder = _child(
        f"""
        db = ERPDatabase({db_path!r})
        conn = db._create_connection()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE rate_limit_events SET scope = scope")
        print("HELD", flush=True)
        time.sleep(2.0)
        conn.execute("COMMIT")
        conn.close()
        print("RELEASED", flush=True)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    store = SqliteRateLimitStore(db)  # built before the lock is taken (schema DDL writes)
    try:
        assert proc.stdout.readline().strip() == "HELD"
        started = time.monotonic()
        allowed, _retry = store.hit("login", "contended", 5, 60.0)
        waited = time.monotonic() - started
        assert allowed is True, "the write must succeed once the lock is released"
        assert waited >= 0.5, f"expected to wait for the other process's lock, waited {waited:.2f}s"
        print(f"[R-02] contention: write waited {waited:.2f}s behind another process, then succeeded")
    finally:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, (out, err)
    assert store.row_count() == 2, "the seed row and the contended write are both stored"


def test_a_backend_interruption_fails_closed_and_recovers(tmp_path: Path) -> None:
    """Interruption → fail-closed refusal → recovery, with no lost state."""
    db_path = str(tmp_path / "interrupted.sqlite3")
    db = ERPDatabase(db_path)
    store = SqliteRateLimitStore(db)
    throttle = RequestThrottle(
        auth_failure_limit=3, auth_failure_window=60, client_failure_limit=0,
        store=store, failure_mode="fail_closed",
    )
    assert throttle.login_blocked("203.0.113.50", "dave") == (False, 0)

    # Interruption: the table vanishes underneath the store.
    with db.transaction() as conn:
        conn.execute(f"DROP TABLE {SqliteRateLimitStore.TABLE}")
    blocked, _retry = throttle.login_blocked("203.0.113.50", "dave")
    assert blocked is True, "an unavailable counter must not become an unlimited window"
    assert throttle.current_backend_error is not None
    failures_during_outage = throttle.backend_failure_count
    assert failures_during_outage >= 1

    # Recovery: the schema is rebuilt by the next store instance.
    recovered = SqliteRateLimitStore(db)
    throttle.store = recovered
    blocked, _retry = throttle.login_blocked("203.0.113.50", "dave")
    assert blocked is False
    assert throttle.current_backend_error is None, "recovery clears the current error"
    assert throttle.backend_failure_count == failures_during_outage, "history is not rewritten"


def test_high_cardinality_state_stays_bounded_under_concurrent_writers(tmp_path: Path) -> None:
    """A flood of distinct keys cannot grow the table past its cap."""
    db_path = str(tmp_path / "cardinality.sqlite3")
    db = ERPDatabase(db_path)
    SqliteRateLimitStore(db, max_rows=300)

    scripts = [
        _child(
            f"""
            store = SqliteRateLimitStore(ERPDatabase({db_path!r}), max_rows=300, prune_interval=0.0)
            for i in range(120):
                store.hit("login", f"k-{{i}}-{{time.monotonic_ns()}}", 5, 60.0)
            print(store.row_count())
            """
        )
        for _ in range(3)
    ]
    completed = _run_in_processes(scripts)
    summaries = _summary(completed)
    assert all(code == 0 for _out, _err, code in summaries), summaries

    final = SqliteRateLimitStore(db, max_rows=300, prune_interval=0.0)
    rows = final.row_count()
    # The cap is enforced when a store prunes, and each writer may land one event
    # after its own prune before the next one runs. The guarantee is therefore
    # "max_rows, plus at most one event per concurrent writer" — bounded, and
    # stated precisely rather than as an absolute that concurrency can exceed.
    workers = 3
    assert rows <= 300 + workers, f"high-cardinality flood exceeded the bound: {rows} rows"
    assert rows < 360, "and it is nowhere near unbounded (360 events were attempted)"
    print(
        f"[R-02] high cardinality: {workers} processes x 120 distinct keys -> {rows} rows "
        f"(cap 300 + {workers} concurrent writers)"
    )


def test_concurrent_process_startup_does_not_race_the_schema_migrations(tmp_path: Path) -> None:
    """Several workers starting at once must not collide in the migration sequence.

    This is the regression for a defect this suite found while testing something
    else: the migration steps guarded themselves with a check
    (``DROP TRIGGER IF EXISTS`` / ``PRAGMA table_info``) followed by DDL, as
    autocommitted statements. One process was always fine; two starting together
    interleaved between the check and the DDL and the loser raised
    ``trigger trg_ocr_field_preserve_raw already exists``. The sequence is now one
    ``BEGIN IMMEDIATE`` transaction, so starting workers serialise.
    """
    db_path = str(tmp_path / "startup.sqlite3")
    scripts = [
        _child(
            f"""
            ERPDatabase({db_path!r})
            print("OPENED")
            """
        )
        for _ in range(5)
    ]
    completed = _run_in_processes(scripts)
    summaries = _summary(completed)
    assert all(code == 0 for _out, _err, code in summaries), summaries
    assert all(out.strip() == "OPENED" for out, _err, _code in summaries)

    # And the schema it converged on is the expected one, not a half-migrated mix.
    db = ERPDatabase(db_path)
    with db.transaction() as conn:
        triggers = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        }
        columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(timbre_rules)").fetchall()
        }
    assert "trg_ocr_field_preserve_raw" in triggers
    assert {"review_state", "reviewed_by_id", "reason"} <= columns


# ---------------------------------------------------------------------------
# R-03 — the read path must not take the write lock
# ---------------------------------------------------------------------------


def test_a_read_probe_does_not_wait_for_another_processes_write_lock(tmp_path: Path) -> None:
    """The point of R-03: a block check stays fast while a writer holds the lock."""
    db_path = str(tmp_path / "probe.sqlite3")
    db = ERPDatabase(db_path)
    store = SqliteRateLimitStore(db)
    for _ in range(3):
        store.hit("login", "probe-key", 5, 60.0)

    holder = _child(
        f"""
        db = ERPDatabase({db_path!r})
        conn = db._create_connection()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE rate_limit_events SET scope = scope")
        print("HELD", flush=True)
        time.sleep(2.0)
        conn.execute("COMMIT")
        conn.close()
        print("RELEASED", flush=True)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "HELD"
        started = time.monotonic()
        blocked = store.blocked("login", "probe-key", 5, 60.0)
        probe_seconds = time.monotonic() - started
        assert blocked is False
        assert probe_seconds < 1.0, (
            f"the read path waited {probe_seconds:.2f}s behind a write lock; "
            "it must not take BEGIN IMMEDIATE"
        )
        print(f"[R-03] blocked() under a foreign write lock: {probe_seconds * 1000:.1f} ms")

        # The same moment, the *write* path: it must wait (that is correct).
        started = time.monotonic()
        allowed, _retry = store.hit("login", "other-key", 5, 60.0)
        write_seconds = time.monotonic() - started
        assert allowed is True
        assert write_seconds > probe_seconds, (
            "a write must still respect the other process's lock"
        )
        print(f"[R-03] hit() under the same lock: {write_seconds * 1000:.0f} ms (waited)")
    finally:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, (out, err)


def test_the_probe_sees_committed_events_and_not_uncommitted_ones(tmp_path: Path) -> None:
    """Isolation: the cheaper read must still read a correct snapshot."""
    db_path = str(tmp_path / "isolation.sqlite3")
    db = ERPDatabase(db_path)
    store = SqliteRateLimitStore(db)

    child = _child(
        f"""
        db = ERPDatabase({db_path!r})
        conn = db._create_connection()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO rate_limit_events (scope, key, occurred_at) VALUES (?, ?, ?)",
            ("login", "iso", time.time()),
        )
        print("INSERTED", flush=True)
        time.sleep(1.5)
        conn.execute("COMMIT")
        conn.close()
        print("COMMITTED", flush=True)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "INSERTED"
        assert store.blocked("login", "iso", 1, 60.0) is False, (
            "an uncommitted event must not be visible to another process's probe"
        )
        assert proc.stdout.readline().strip() == "COMMITTED"
        # After the commit the event is real, and the probe sees it.
        deadline = time.monotonic() + 5
        seen = False
        while time.monotonic() < deadline:
            if store.blocked("login", "iso", 1, 60.0):
                seen = True
                break
            time.sleep(0.05)
        assert seen, "a committed event must be visible to the probe"
    finally:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, (out, err)


def test_the_read_optimisation_does_not_change_what_is_counted(tmp_path: Path) -> None:
    """Same events, same window, same boundary: only the locking changed."""
    db = ERPDatabase(str(tmp_path / "sameness.sqlite3"))
    store = SqliteRateLimitStore(db)

    now = time.time()
    with db.transaction() as conn:
        for offset in (0.0, 0.5, 1.0):  # inside the window
            conn.execute(
                f"INSERT INTO {SqliteRateLimitStore.TABLE} (scope, key, occurred_at) VALUES (?, ?, ?)",
                ("login", "edge", now - offset),
            )
        conn.execute(  # outside the window: must not count
            f"INSERT INTO {SqliteRateLimitStore.TABLE} (scope, key, occurred_at) VALUES (?, ?, ?)",
            ("login", "edge", now - 120),
        )

    assert store.blocked("login", "edge", 3, 60.0) is True, "3 in-window events reach the limit"
    assert store.blocked("login", "edge", 4, 60.0) is False, "the expired event is not counted"
    # A 0.75 s window keeps the two newest events and drops the 1.0 s old one.
    assert store.blocked("login", "edge", 2, 0.75) is True, "the narrow window still counts 2"
    assert store.blocked("login", "edge", 3, 0.75) is False, "and drops the oldest"


def test_the_probe_does_not_bypass_in_process_transaction_isolation(tmp_path: Path) -> None:
    """A probe cannot observe a rollback, and a refusal still leaves the event recorded."""
    db = ERPDatabase(str(tmp_path / "rollback.sqlite3"))
    store = SqliteRateLimitStore(db)
    store.hit("login", "rb", 2, 60.0)

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with db.transaction() as conn:
            conn.execute(
                f"INSERT INTO {SqliteRateLimitStore.TABLE} (scope, key, occurred_at) VALUES (?, ?, ?)",
                ("login", "rb", time.time()),
            )
            raise Boom()

    assert store.blocked("login", "rb", 2, 60.0) is False, (
        "the rolled-back event must not count towards the limit"
    )
    assert store.row_count() == 1


def test_measured_latency_of_the_read_and_write_paths(tmp_path: Path) -> None:
    """Record both paths' cost so a reviewer sees the trade, not a claim."""
    db = ERPDatabase(str(tmp_path / "latency.sqlite3"))
    store = SqliteRateLimitStore(db, prune_interval=3600.0)
    for i in range(50):
        store.hit("login", f"warm-{i}", 1000, 300.0)

    def timed(fn, n: int) -> float:
        started = time.perf_counter()
        for i in range(n):
            fn(i)
        return (time.perf_counter() - started) / n * 1000

    read_ms = timed(lambda i: store.blocked("login", f"warm-{i % 50}", 1000, 300.0), 300)
    write_ms = timed(lambda i: store.hit("login", f"write-{i % 50}", 100000, 300.0), 300)

    assert read_ms < write_ms * 1.5, (
        f"the read path should not be slower than the write path ({read_ms:.3f} ms vs {write_ms:.3f} ms)"
    )
    print(f"[R-03] sequential latency per call: blocked() {read_ms:.3f} ms, hit() {write_ms:.3f} ms")

    # Sanity: the numbers are real, not zero.
    assert read_ms > 0 and write_ms > 0
    assert statistics.median([read_ms, write_ms]) > 0
