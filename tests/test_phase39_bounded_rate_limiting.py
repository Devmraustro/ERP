# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 39 — bounded limiter state, and what the control actually covers.

The audit that closed Phase 38 found the limiter grew without bound: the bucket
key contains the *username*, which the caller chooses, and expired buckets were
never removed. Five thousand random usernames left five thousand buckets behind,
and they outlived the window. That is a memory-exhaustion lever in a long-running
process, owned by an unauthenticated caller.

This suite holds the fix to its claims rather than to its intentions:

* a bucket stops existing once the window has passed — not merely "is ignored";
* the table cannot exceed its cap, and eviction is observable;
* a read-only probe allocates nothing, so probing is free;
* flooding usernames does not buy unlimited guesses, because the per-client
  aggregate keeps counting and is bounded by the number of clients;
* concurrency neither loses an event nor corrupts a bucket;
* a restart forgets in-memory state (that is the honest limit of the default
  backend) and a shared database backend does not;
* two independent stores over one database agree, which is what "multi-worker"
  means for this deployment;
* a backend failure is fail-closed and audited, and the fail-open alternative is
  explicit, logged and never silent;
* the existing per-account policy is unchanged, and no credential reaches a log.

Nothing here claims *distributed* rate limiting. The scope strings are asserted
too: a process-local limiter must say so.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.rate_limit import (
    DEFAULT_MAX_KEYS,
    MemoryRateLimitStore,
    RequestThrottle,
    SlidingWindowLimiter,
    SqliteRateLimitStore,
    describe_throttle_policy,
    format_rate_limit_scope,
    store_from_env,
)

PASSWORD = "Admin#2026x"
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _app(tmp_path: Path, *, throttle: RequestThrottle | None = None, security_log=None):
    db = ERPDatabase(str(tmp_path / "p39.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        for user_id, username, role in (
            (1, "admin_a", "admin"),
            (2, "clerk_a", "clerk"),
        ):
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, 1, ?, ?, 1, ?)",
                (user_id, username, role, hash_password(PASSWORD, iterations=1000)),
            )
    import os

    if security_log is not None:
        os.environ["ERP_SECURITY_LOG_PATH"] = str(security_log)
    try:
        app = create_erp_fastapi_app(db, PrivateDocumentVault(tmp_path / "vault"), throttle=throttle)
    finally:
        if security_log is not None:
            os.environ.pop("ERP_SECURITY_LOG_PATH", None)
    return db, app


def _login(client: TestClient, username: str, password: str):
    return client.post("/api/v1/auth/token", json={"username": username, "password": password})


def _events(app) -> list[dict]:
    flattened = []
    for record in app.state.logger.records:
        entry = {k: v for k, v in record.items() if k != "context"}
        entry.update(record.get("context") or {})
        flattened.append(entry)
    return flattened


def _probe(throttle: RequestThrottle, client: str, n: int) -> int:
    """Feed ``n`` distinct usernames and report how many buckets exist."""
    for i in range(n):
        throttle.record_login_failure(client, f"user{i}@example.test")
    return len(throttle.auth_failures._events)


# ---------------------------------------------------------------------------
# 1. expiration semantics
# ---------------------------------------------------------------------------


def test_an_expired_bucket_stops_existing_not_just_counting() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, clock=clock)

    limiter.hit("k")
    limiter.hit("k")
    assert limiter.count("k") == 2
    assert limiter.stats()["tracked_keys"] == 1

    clock.advance(61)
    # The events are gone from the window ...
    assert limiter.count("k") == 0
    # ... and the bucket itself is gone, which is the actual fix: the old
    # implementation kept the empty deque forever.
    assert limiter.sweep() == 1
    assert limiter.stats()["tracked_keys"] == 0
    assert limiter.stats()["expired_keys"] == 1


def test_expiry_happens_without_an_explicit_sweep() -> None:
    """Lazy sweeping is what makes expiry true in a real process."""
    clock = _FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=10, clock=clock)

    for i in range(50):
        limiter.hit(f"k{i}")
    assert limiter.stats()["tracked_keys"] == 50

    clock.advance(11)
    limiter.hit("fresh")  # any hit may sweep, at most once per interval
    assert limiter.stats()["tracked_keys"] == 1
    assert limiter.stats()["expired_keys"] >= 49


def test_a_read_only_probe_allocates_nothing() -> None:
    """The amplification that the audit measured: blocked() used to create state."""
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60)
    for i in range(500):
        assert limiter.blocked(f"probe{i}") == (False, 0)
    assert limiter.stats()["tracked_keys"] == 0
    assert limiter.count("probe0") == 0
    assert limiter.stats()["tracked_keys"] == 0


# ---------------------------------------------------------------------------
# 2. bounds under high cardinality
# ---------------------------------------------------------------------------


def test_the_bucket_table_never_exceeds_its_cap() -> None:
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60, max_keys=100)
    for i in range(5_000):
        limiter.hit(f"k{i}")
        assert len(limiter._events) <= 100
    stats = limiter.stats()
    assert stats["tracked_keys"] == 100
    assert stats["evicted_keys"] == 4_900


def test_five_thousand_usernames_no_longer_leak() -> None:
    """The audit's own experiment, as a regression test."""
    throttle = RequestThrottle(
        auth_failure_limit=10, auth_failure_window=300, max_keys=64, client_failure_limit=0
    )
    tracked = _probe(throttle, "203.0.113.9", 5_000)
    assert tracked <= 64, f"{tracked} buckets retained"
    assert throttle.auth_failures.stats()["evicted_keys"] >= 5_000 - 64


def test_eviction_is_least_recently_touched_not_random() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowLimiter(limit=5, window_seconds=60, max_keys=3, clock=clock)

    limiter.hit("a")
    clock.advance(1)
    limiter.hit("b")
    clock.advance(1)
    limiter.hit("c")
    clock.advance(1)
    limiter.hit("a")  # touch a, so b is now the oldest
    clock.advance(1)
    limiter.hit("d")  # evicts b

    assert limiter.count("b") == 0
    assert limiter.count("a") == 2
    assert limiter.count("c") == 1
    assert limiter.count("d") == 1


def test_state_bounds_hold_under_a_mixed_flood() -> None:
    """Distinct usernames *and* distinct clients, both attacker-chosen."""
    throttle = RequestThrottle(
        auth_failure_limit=5, auth_failure_window=300, max_keys=32, client_failure_limit=4
    )
    for client in range(200):
        for user in range(10):
            throttle.record_login_failure(f"198.51.100.{client}", f"ghost{user}")
    for scope, limiter in throttle.store._limiters.items():
        assert len(limiter._events) <= 32, (scope, len(limiter._events))


# ---------------------------------------------------------------------------
# 3. the aggregate dimension is what survives eviction
# ---------------------------------------------------------------------------


def test_username_flooding_does_not_buy_unlimited_guesses() -> None:
    """Per-account buckets are evicted under flood; the per-client one is not."""
    throttle = RequestThrottle(
        auth_failure_limit=2, auth_failure_window=60, client_failure_limit=10, max_keys=8
    )
    client = "203.0.113.50"
    statuses = [
        throttle.record_login_failure(client, f"victim{i}")[0] for i in range(12)
    ]
    assert statuses[:10] == [True] * 10
    assert statuses[10:] == [False, False]
    # Even for a username never seen before, the client is out of budget.
    assert throttle.login_blocked(client, "brand-new-name")[0] is True
    # A different client is unaffected.
    assert throttle.login_blocked("203.0.113.51", "brand-new-name")[0] is False


def test_the_aggregate_is_not_reset_by_a_successful_login() -> None:
    """Otherwise one valid account would reset an attacker's flood counter."""
    throttle = RequestThrottle(
        auth_failure_limit=3, auth_failure_window=60, client_failure_limit=4
    )
    client = "203.0.113.60"
    for i in range(4):
        throttle.record_login_failure(client, f"ghost{i}")
    throttle.record_login_success(client, "admin_a")
    assert throttle.login_blocked(client, "admin_a")[0] is True


def test_the_per_account_policy_is_unchanged() -> None:
    throttle = RequestThrottle(auth_failure_limit=3, auth_failure_window=60)
    client = "203.0.113.61"
    assert [throttle.record_login_failure(client, " admin ")[0] for _ in range(3)] == [
        True,
        True,
        True,
    ]
    assert throttle.login_blocked(client, "ADMIN")[0] is True
    # A different account from the same client still has its own budget.
    assert throttle.login_blocked(client, "clerk_a")[0] is False


def test_a_refused_attempt_is_still_recorded() -> None:
    """Hammering must not let a caller out-wait the window."""
    throttle = RequestThrottle(auth_failure_limit=2, auth_failure_window=60)
    for _ in range(6):
        throttle.record_login_failure("203.0.113.62", "admin_a")
    assert throttle.auth_failures.count(RequestThrottle.login_key("203.0.113.62", "admin_a")) == 6
    assert throttle.login_blocked("203.0.113.62", "admin_a")[0] is True


# ---------------------------------------------------------------------------
# 4. window boundaries
# ---------------------------------------------------------------------------


def test_the_window_boundary_is_exclusive_on_the_oldest_event() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowLimiter(limit=2, window_seconds=10, clock=clock)
    limiter.hit("k")
    clock.advance(10.0)  # exactly the window: the first event has left it
    assert limiter.count("k") == 0
    assert limiter.hit("k")[0] is True

    clock.advance(9.999)  # still inside
    assert limiter.count("k") == 1
    clock.advance(0.001)  # now outside
    assert limiter.count("k") == 0


def test_retry_after_shrinks_as_the_window_advances() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60, clock=clock)
    assert limiter.hit("k") == (True, 0)
    allowed, first = limiter.hit("k")
    assert allowed is False and 59 <= first <= 60
    clock.advance(30)
    allowed, second = limiter.hit("k")
    assert allowed is False and second < first


# ---------------------------------------------------------------------------
# 5. concurrency
# ---------------------------------------------------------------------------


def test_concurrent_hits_lose_nothing_and_keep_the_bound() -> None:
    limiter = SlidingWindowLimiter(limit=10_000, window_seconds=60, max_keys=64)
    errors: list[BaseException] = []

    def worker(offset: int) -> None:
        try:
            for i in range(500):
                limiter.hit(f"k{(offset + i) % 200}")
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(limiter._events) <= 64
    total_events = sum(len(b) for b in limiter._events.values())
    assert total_events <= 4_000  # eviction may drop history, never duplicate it


def test_concurrent_failures_from_one_client_trip_the_aggregate_exactly_once() -> None:
    throttle = RequestThrottle(
        auth_failure_limit=1_000, auth_failure_window=60, client_failure_limit=20
    )
    results: list[bool] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        for _ in range(10):
            allowed, _ = throttle.record_login_failure("203.0.113.70", f"user{n}")
            with lock:
                results.append(allowed)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 80
    assert results.count(True) == 20, "the aggregate must count every refusal exactly once"


# ---------------------------------------------------------------------------
# 6. restart and multi-worker semantics
# ---------------------------------------------------------------------------


def test_the_default_store_is_process_local_and_says_so() -> None:
    description = describe_throttle_policy(RequestThrottle())
    assert "in-memory" in description["storage"]
    assert "per-process" in description["scope"]
    assert "worker" in description["scope"]
    assert "not a global bound" in description["scope"]
    assert "staging-only" in format_rate_limit_scope(RequestThrottle())


def test_a_restart_forgets_in_memory_state() -> None:
    """The honest limit of the default backend: new process, new counters."""
    throttle = RequestThrottle(auth_failure_limit=2, auth_failure_window=60)
    for _ in range(3):
        throttle.record_login_failure("203.0.113.80", "admin_a")
    assert throttle.login_blocked("203.0.113.80", "admin_a")[0] is True

    restarted = RequestThrottle(auth_failure_limit=2, auth_failure_window=60)
    assert restarted.login_blocked("203.0.113.80", "admin_a")[0] is False


def test_a_shared_store_is_visible_to_a_second_instance(tmp_path: Path) -> None:
    """Two applications over one database = the multi-worker case."""
    db_path = str(tmp_path / "shared.sqlite3")
    db_a = ERPDatabase(db_path)
    db_b = ERPDatabase(db_path)

    store_a = SqliteRateLimitStore(db_a)
    store_b = SqliteRateLimitStore(db_b)

    for _ in range(3):
        store_a.hit("login", "k", 5, 60.0)
    # A different process, a different connection: the same counter.
    assert store_b.blocked("login", "k", 5, 60.0) is False
    store_b.hit("login", "k", 5, 60.0)
    store_b.hit("login", "k", 5, 60.0)
    assert store_a.blocked("login", "k", 5, 60.0) is True
    # And a reset is shared too.
    store_a.reset("login", "k")
    assert store_b.blocked("login", "k", 5, 60.0) is False


def test_a_shared_store_is_visible_to_a_separate_process(tmp_path: Path) -> None:
    """The real test of "shared": another OS process, not another object."""
    db_path = str(tmp_path / "proc.sqlite3")
    db = ERPDatabase(db_path)
    store = SqliteRateLimitStore(db)
    for _ in range(4):
        store.hit("login", "shared-key", 5, 60.0)
    assert store.blocked("login", "shared-key", 5, 60.0) is False

    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from dz_erp.database import ERPDatabase
        from dz_erp.rate_limit import SqliteRateLimitStore

        db = ERPDatabase({db_path!r})
        store = SqliteRateLimitStore(db)
        blocked = store.blocked("login", "shared-key", 5, 60.0)
        store.hit("login", "shared-key", 5, 60.0)
        print("BLOCKED" if blocked else "OPEN")
        """
    )
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "OPEN"  # 4 events, limit 5
    # The child's fifth event is now visible to the parent.
    assert store.blocked("login", "shared-key", 5, 60.0) is True


def test_the_shared_store_scope_is_not_overclaimed() -> None:
    description = SqliteRateLimitStore.__doc__ or ""
    assert "not a distributed" in description or "does not coordinate" in description
    store = SqliteRateLimitStore.__new__(SqliteRateLimitStore)  # no database needed
    described = store.describe() if hasattr(store, "_db") else None
    # describe() is asserted on a real instance in the test below; here we only
    # pin that no distributed guarantee is implied by the class name or docs.
    assert described is None or "not a distributed bound" in described["scope"]


def test_the_shared_store_reports_its_real_scope(tmp_path: Path) -> None:
    store = SqliteRateLimitStore(ERPDatabase(str(tmp_path / "scope.sqlite3")))
    described = store.describe()
    assert described["backend"] == "sqlite"
    assert "not a distributed bound" in described["scope"]
    assert "database" in described["storage"]


def test_store_selection_is_explicit_and_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_RATE_LIMIT_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="requires a database"):
        store_from_env(None)
    monkeypatch.setenv("ERP_RATE_LIMIT_BACKEND", "redis")  # not bundled
    assert isinstance(store_from_env(None), MemoryRateLimitStore)


def test_two_applications_on_one_database_share_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ERP_RATE_LIMIT_BACKEND=sqlite` is the documented multi-worker story."""
    monkeypatch.setenv("ERP_RATE_LIMIT_BACKEND", "sqlite")
    db_path = tmp_path / "workers.sqlite3"
    db_a = ERPDatabase(str(db_path))
    with db_a.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (1, 1, 'admin_a', 'admin', 1, ?)",
            (hash_password(PASSWORD, iterations=1000),),
        )
    db_b = ERPDatabase(str(db_path))

    app_a = create_erp_fastapi_app(db_a, PrivateDocumentVault(tmp_path / "vault_a"))
    app_b = create_erp_fastapi_app(db_b, PrivateDocumentVault(tmp_path / "vault_b"))
    assert isinstance(app_a.state.throttle.store, SqliteRateLimitStore)
    # Two processes would read the same environment; the limit is a property of
    # the policy, so set it identically on both.
    app_a.state.throttle.auth_limit = 3
    app_b.state.throttle.auth_limit = 3
    app_a.state.throttle.client_limit = 0
    app_b.state.throttle.client_limit = 0

    client_a = TestClient(app_a)
    client_b = TestClient(app_b)
    for _ in range(3):
        assert _login(client_a, "admin_a", "Wrong#1").status_code == 401
    # The second worker sees the first worker's failures.
    assert _login(client_b, "admin_a", PASSWORD).status_code == 429
    assert "not distributed" in format_rate_limit_scope(app_b.state.throttle)


# ---------------------------------------------------------------------------
# 7. backend failure policy — explicit, justified, both directions
# ---------------------------------------------------------------------------


class _BrokenStore:
    name = "broken"

    def __init__(self) -> None:
        self.calls = 0

    def hit(self, *args, **kwargs):
        self.calls += 1
        raise sqlite3.OperationalError("database is locked")

    def blocked(self, *args, **kwargs):
        self.calls += 1
        raise sqlite3.OperationalError("database is locked")

    def reset(self, *args, **kwargs):
        self.calls += 1
        raise sqlite3.OperationalError("database is locked")

    def describe(self):
        return {"backend": "broken", "storage": "none", "scope": "none"}


def test_an_unavailable_backend_fails_closed_by_default() -> None:
    throttle = RequestThrottle(store=_BrokenStore())
    assert throttle.failure_mode == "fail_closed"
    blocked, retry = throttle.login_blocked("203.0.113.90", "admin_a")
    assert blocked is True and retry > 0
    # And a failure cannot be recorded as "allowed" either.
    allowed, _ = throttle.record_login_failure("203.0.113.90", "admin_a")
    assert allowed is False
    assert "OperationalError" in (throttle.last_backend_error or "")


def test_fail_open_is_available_and_deliberately_visible() -> None:
    throttle = RequestThrottle(store=_BrokenStore(), failure_mode="fail_open")
    assert throttle.login_blocked("203.0.113.91", "admin_a") == (False, 0)
    assert throttle.record_login_failure("203.0.113.91", "admin_a")[0] is True
    assert throttle.last_backend_error is not None
    assert describe_throttle_policy(throttle)["failure_mode"] == "fail_open"


def test_an_unknown_failure_mode_falls_back_to_closed() -> None:
    throttle = RequestThrottle(store=_BrokenStore(), failure_mode="ignore")
    assert throttle.failure_mode == "fail_closed"
    assert throttle.login_blocked("203.0.113.92", "admin_a")[0] is True


def test_backend_failure_is_audited_without_the_error_text(tmp_path: Path) -> None:
    _, app = _app(tmp_path, throttle=RequestThrottle(store=_BrokenStore()))
    client = TestClient(app)
    refused = _login(client, "admin_a", PASSWORD)
    assert refused.status_code == 429
    events = [e for e in _events(app) if e["event"] == "RATE_LIMIT_BACKEND_UNAVAILABLE"]
    assert events and events[0]["error_type"] == "OperationalError"
    assert events[0]["failure_mode"] == "fail_closed"
    rendered = json.dumps(_events(app))
    assert "database is locked" not in rendered  # the message stays out of the log
    assert PASSWORD not in rendered


# ---------------------------------------------------------------------------
# 8. no regression at the endpoint, and nothing sensitive in the log
# ---------------------------------------------------------------------------


def test_login_throttling_still_works_end_to_end(tmp_path: Path) -> None:
    _, app = _app(tmp_path, throttle=RequestThrottle(auth_failure_limit=3))
    client = TestClient(app)
    assert [_login(client, "admin_a", "Wrong#1").status_code for _ in range(3)] == [401] * 3
    refused = _login(client, "admin_a", PASSWORD)
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) >= 1
    # One account refused, another unaffected (per-account policy intact).
    assert _login(client, "clerk_a", PASSWORD).status_code == 200


def test_a_username_flood_at_the_endpoint_stays_bounded(tmp_path: Path) -> None:
    throttle = RequestThrottle(
        auth_failure_limit=10, auth_failure_window=300, max_keys=16, client_failure_limit=0
    )
    _, app = _app(tmp_path, throttle=throttle)
    client = TestClient(app)
    for i in range(120):
        assert _login(client, f"ghost{i}", "Wrong#1").status_code == 401
    assert len(throttle.auth_failures._events) <= 16


def test_no_username_or_credential_is_kept_in_the_bucket_table() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    limiter.hit("login|203.0.113.5|alice")
    stats = limiter.stats()
    rendered = json.dumps(stats)
    assert "alice" not in rendered and "203.0.113.5" not in rendered
    assert set(stats) == {
        "tracked_keys",
        "max_keys",
        "evicted_keys",
        "expired_keys",
        "sweeps",
    }


def test_the_policy_endpoint_reports_what_the_process_is_doing(tmp_path: Path) -> None:
    _, app = _app(tmp_path, throttle=RequestThrottle(auth_failure_limit=4, max_keys=32))
    client = TestClient(app)
    token = _login(client, "admin_a", PASSWORD).json()["access_token"]
    described = client.get(
        "/api/v1/system/rate-limit-policy", headers={"Authorization": f"Bearer {token}"}
    )
    assert described.status_code == 200
    payload = described.json()["rate_limit"]
    assert payload["auth_failure_limit"] == 4
    assert payload["bounded"]["max_keys_per_scope"] == 32
    assert "per-process" in payload["deployment_scope"]
    assert "staging-only" in payload["deployment_scope"]
    assert "alice" not in json.dumps(payload)

    # A non-administrator cannot read the policy inventory.
    clerk_token = _login(client, "clerk_a", PASSWORD).json()["access_token"]
    assert (
        client.get(
            "/api/v1/system/rate-limit-policy",
            headers={"Authorization": f"Bearer {clerk_token}"},
        ).status_code
        == 403
    )


def test_the_default_cap_is_the_documented_one() -> None:
    assert DEFAULT_MAX_KEYS == 4096
    assert SlidingWindowLimiter(limit=1, window_seconds=1).max_keys == DEFAULT_MAX_KEYS
