# SPDX-License-Identifier: LGPL-3.0-only
"""Request throttling for the HTTP surface.

Why this exists
---------------
The API had no bound on repeated failures. An attacker holding a valid username
could submit unlimited password guesses to ``POST /api/v1/auth/token``; the only
cost of a wrong guess was a 401. Password hashing makes each attempt expensive
*for the server*, which turns an unbounded endpoint into a denial-of-service
lever as much as a credential-stuffing one.

Phase 39 — bounded state
------------------------
The first implementation kept one bucket per ``(client, account)`` and never
removed them. The key contains the *username*, which the caller chooses, so an
unauthenticated client could create permanent state at will: 5 000 random
usernames produced 5 000 retained buckets, and they survived the window. That is
a slow memory-exhaustion lever against a long-running process.

The limiter now has explicit lifetime semantics:

* **expiry** — a bucket is empty once every event in it is older than the
  window, and `sweep()` drops empty buckets (cheap, and also run lazily at most
  once per sweep interval while the table is in use);
* **a hard cap** — ``max_keys`` buckets at most. If a new key arrives when the
  table is full, the least-recently-touched bucket is evicted (`evicted_keys`
  counts it), so memory is bounded no matter what the caller sends;
* **read-only lookups never allocate**. ``blocked()`` is a pure read: the old
  implementation created a bucket for every probed key, which doubled the
  amplification;
* **a second dimension** — a per-client aggregate counter with bounded
  cardinality. Evicting a per-account bucket under flood only forgets that
  account's history; the aggregate keeps counting every failure for the client,
  so flooding usernames does not buy unlimited guesses.

None of this is attacker-controlled beyond the cardinality argument: the client
key comes from the trusted-proxy policy (see :mod:`dz_erp.net`), so
``X-Forwarded-For`` cannot rotate the aggregate bucket unless a proxy is
explicitly trusted.

What this is — and is not
-------------------------
By default it is a **sliding-window counter held in process memory**. That is
honest and useful for the single-process deployment this repository ships, and it
is deliberately not dressed up as more:

* it is per worker. With ``--workers N`` (or several containers behind a proxy)
  each process keeps its own counters, so the effective limit is ``N × limit``.
  That is why the default deployment is one worker, and why the scope is
  reported as ``per-process`` in :func:`describe_throttle_policy`.

A shared store is available for deployments that must run several processes
against **one** database file: set ``ERP_RATE_LIMIT_BACKEND=sqlite`` and the
counters live in the application database, inside the same transaction
guarantees as the rest of the data (see :class:`SqliteRateLimitStore`). It is
shared across *processes on one database*, which is a real boundary but not a
distributed one: replicas with separate database files, or a database the
application only reaches through a service, are out of scope and are not
claimed. No Redis backend is bundled — the dependency, the server and its
operational/security requirements (authentication, TLS, failover, backup of
transient state) cannot be verified in this repository, so shipping one would be
an unevidenced claim rather than a control.

The rules it enforces, all fail-closed:

1. **Authentication failures** — after ``ERP_AUTH_FAILURE_LIMIT`` failed attempts
   for one ``(client, username)`` inside ``ERP_AUTH_FAILURE_WINDOW_SECONDS``,
   further attempts are refused with 429 and ``Retry-After`` *before* the
   password is verified, so a locked-out attacker cannot keep burning CPU. A
   successful authentication clears that key's counter, so a legitimate user who
   mistypes a few times is not locked out.
2. **Per-client aggregate** — ``ERP_AUTH_CLIENT_FAILURE_LIMIT`` failures from one
   client, across *all* usernames, inside the same window. This is the bound
   that survives username flooding. Defaults to five times rule 1; a successful
   login does **not** clear it, because otherwise one valid account would reset
   an attacker's aggregate at will.
3. **Overall request rate** — an optional per-client ceiling
   (``ERP_RATE_LIMIT_PER_MINUTE``, 0 = disabled) for deployments that cannot put
   a limiter in front of the application.

Throttling is audited (``SECURITY_AUTH_RATE_LIMITED`` / ``SECURITY_RATE_LIMITED``)
so the refusal is evidence, not a silent drop. Neither the username's password
nor the presented token is ever recorded.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Protocol, Tuple

#: Default failed-attempt ceiling per (client, username) inside the window.
DEFAULT_AUTH_FAILURE_LIMIT = 10
#: Default window for failed attempts, in seconds.
DEFAULT_AUTH_FAILURE_WINDOW = 300.0
#: Default overall per-client request ceiling per minute (0 disables the rule).
DEFAULT_REQUESTS_PER_MINUTE = 0
#: Default per-client aggregate failure ceiling (across all usernames).
DEFAULT_CLIENT_FAILURE_LIMIT = 50
#: Default number of buckets one limiter may hold before evicting the oldest.
DEFAULT_MAX_KEYS = 4096

AUTH_FAILURE_LIMIT_ENV = "ERP_AUTH_FAILURE_LIMIT"
AUTH_FAILURE_WINDOW_ENV = "ERP_AUTH_FAILURE_WINDOW_SECONDS"
CLIENT_FAILURE_LIMIT_ENV = "ERP_AUTH_CLIENT_FAILURE_LIMIT"
AUTH_MAX_KEYS_ENV = "ERP_AUTH_FAILURE_MAX_KEYS"
REQUESTS_PER_MINUTE_ENV = "ERP_RATE_LIMIT_PER_MINUTE"
BACKEND_ENV = "ERP_RATE_LIMIT_BACKEND"
FAILURE_MODE_ENV = "ERP_RATE_LIMIT_FAILURE_MODE"

#: How a failure *inside a shared backend* is treated. Refusing authentication
#: when the counter cannot be consulted is the fail-closed choice: an
#: unavailable counter must not become an unlimited guessing window. The price
#: is availability, which is why the mode is explicit, logged, and reported by
#: the readiness checker rather than hidden.
FAILURE_MODES = ("fail_closed", "fail_open")
DEFAULT_FAILURE_MODE = "fail_closed"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_float(name: str, default: float, *, minimum: float = 1.0) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_choice(name: str, default: str, allowed: Iterable[str]) -> str:
    raw = (os.environ.get(name) or "").strip().lower()
    return raw if raw in tuple(allowed) else default


class SlidingWindowLimiter:
    """A thread-safe, **bounded** sliding-window counter keyed by an opaque string.

    ``hit()`` records an event and reports whether the key is now over its
    limit; ``retry_after()`` reports how long until the oldest event leaves the
    window, which is what a caller should put in ``Retry-After``.

    Bounds
    ------
    * At most ``max_keys`` buckets. A new key evicts the least-recently-touched
      bucket once the table is full (after first dropping empty ones).
    * A bucket disappears as soon as it is empty: `sweep()` removes every bucket
      whose newest event is outside the window, and the limiter sweeps lazily at
      most once per ``sweep_interval`` seconds.
    * ``blocked()`` never creates a bucket, so probing keys costs nothing.

    ``stats()`` exposes the counters an operator needs to see these bounds
    working (tracked keys, evictions, expired buckets) without naming any key.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Optional[Any] = None,
        sweep_interval: Optional[float] = None,
    ) -> None:
        self.limit = int(limit)
        self.window_seconds = float(window_seconds)
        self.max_keys = max(1, int(max_keys))
        self._clock = clock or time.monotonic
        #: Lazily drop empty buckets at most this often. Bounded by the window so
        #: a short window cleans up quickly and a long one does not scan per hit.
        self._sweep_interval = (
            max(0.0, min(1.0, self.window_seconds / 2.0))
            if sweep_interval is None
            else max(0.0, float(sweep_interval))
        )
        self._events: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._last_sweep = 0.0
        self._evicted_keys = 0
        self._expired_keys = 0
        self._sweeps = 0

    # -- internals ---------------------------------------------------------
    def _prune_bucket(self, bucket: Deque[float], now: float) -> Deque[float]:
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def _sweep_locked(self, now: float) -> None:
        dead: List[str] = []
        for key, bucket in self._events.items():
            self._prune_bucket(bucket, now)
            if not bucket:
                dead.append(key)
        for key in dead:
            del self._events[key]
            self._expired_keys += 1
        self._sweeps += 1
        self._last_sweep = now

    def _maybe_sweep_locked(self, now: float) -> None:
        if now - self._last_sweep >= self._sweep_interval:
            self._sweep_locked(now)

    def _bucket_for_write(self, key: str, now: float) -> Deque[float]:
        """Return the bucket for ``key``, creating it within the bounds."""
        bucket = self._events.get(key)
        if bucket is not None:
            self._events.move_to_end(key)
            return bucket
        if len(self._events) >= self.max_keys:
            # First try to reclaim empty buckets; only evict live history if the
            # table is genuinely full of in-window keys.
            self._sweep_locked(now)
            while len(self._events) >= self.max_keys:
                self._events.popitem(last=False)
                self._evicted_keys += 1
        bucket = deque()
        self._events[key] = bucket
        return bucket

    # -- public API --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def count(self, key: str) -> int:
        """Events currently inside the window for ``key`` (never allocates)."""
        if not self.enabled:
            return 0
        now = self._clock()
        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                return 0
            return len(self._prune_bucket(bucket, now))

    def blocked(self, key: str) -> Tuple[bool, int]:
        """Is ``key`` over its limit? Returns ``(blocked, retry_after_seconds)``.

        A read-only probe: an unknown key is simply not blocked, and nothing is
        stored for it.
        """
        if not self.enabled:
            return False, 0
        now = self._clock()
        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                return False, 0
            self._prune_bucket(bucket, now)
            if not bucket:
                return False, 0
            if len(bucket) < self.limit:
                return False, 0
            retry = self.window_seconds - (now - bucket[0])
        return True, max(1, int(retry + 0.999))

    def hit(self, key: str) -> Tuple[bool, int]:
        """Record an event; returns ``(allowed, retry_after_seconds)``.

        The event is recorded even when it is refused, so a client that keeps
        hammering a throttled endpoint cannot out-wait the window by continuing.
        """
        if not self.enabled:
            return True, 0
        now = self._clock()
        with self._lock:
            bucket = self._bucket_for_write(key, now)
            self._prune_bucket(bucket, now)
            allowed = len(bucket) < self.limit
            bucket.append(now)
            retry = 0 if allowed else max(1, int((self.window_seconds - (now - bucket[0])) + 0.999))
            self._maybe_sweep_locked(now)
        return allowed, retry

    def reset(self, key: str) -> None:
        """Forget a key entirely (used when a login succeeds)."""
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def sweep(self) -> int:
        """Drop every empty bucket now. Returns how many were dropped."""
        now = self._clock()
        with self._lock:
            before = len(self._events)
            self._sweep_locked(now)
            return before - len(self._events)

    def stats(self) -> Dict[str, Any]:
        """Bounded-state observability. Never includes a key or a value."""
        with self._lock:
            return {
                "tracked_keys": len(self._events),
                "max_keys": self.max_keys,
                "evicted_keys": self._evicted_keys,
                "expired_keys": self._expired_keys,
                "sweeps": self._sweeps,
            }


class RateLimitStore(Protocol):
    """The minimum a shared counter store must provide.

    ``memory`` is the default (per process); ``sqlite`` shares counters between
    processes that use one database file. Any other implementation must keep the
    same semantics: refuse past the limit, report a non-zero retry, and never
    retain an identifier beyond the window.
    """

    name: str

    def hit(self, scope: str, key: str, limit: int, window_seconds: float) -> Tuple[bool, int]:
        ...

    def blocked(self, scope: str, key: str, limit: int, window_seconds: float) -> bool:
        ...

    def reset(self, scope: str, key: str) -> None:
        ...

    def describe(self) -> Dict[str, Any]:
        ...


class MemoryRateLimitStore:
    """Default store: bounded in-process buckets, one per (scope, key)."""

    name = "memory"

    def __init__(self, *, max_keys: int = DEFAULT_MAX_KEYS) -> None:
        self._limiters: Dict[str, SlidingWindowLimiter] = {}
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def _limiter(self, scope: str, limit: int, window_seconds: float) -> SlidingWindowLimiter:
        with self._lock:
            limiter = self._limiters.get(scope)
            if limiter is None:
                limiter = SlidingWindowLimiter(
                    limit, window_seconds, max_keys=self._max_keys
                )
                self._limiters[scope] = limiter
            return limiter

    def hit(self, scope: str, key: str, limit: int, window_seconds: float) -> Tuple[bool, int]:
        return self._limiter(scope, limit, window_seconds).hit(key)

    def blocked(self, scope: str, key: str, limit: int, window_seconds: float) -> bool:
        return self._limiter(scope, limit, window_seconds).blocked(key)[0]

    def reset(self, scope: str, key: str) -> None:
        with self._lock:
            limiter = self._limiters.get(scope)
        if limiter is not None:
            limiter.reset(key)

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            scopes = {scope: lim.stats() for scope, lim in self._limiters.items()}
        return {
            "backend": self.name,
            "storage": "in-memory sliding window (bounded, swept)",
            "scope": "per-process (per worker; not a global bound across replicas)",
            "max_keys_per_scope": self._max_keys,
            "scopes": scopes,
        }


class SqliteRateLimitStore:
    """Shared counters in the application database.

    Why this and not Redis
    ----------------------
    The repository already ships a SQLite database with an explicit transaction
    boundary (`BEGIN IMMEDIATE`) that every writer takes. Counters stored there
    are shared by *every process that opens the same database file* — which is
    exactly the multi-worker case (`uvicorn --workers N`, several containers on
    one volume) — with no new dependency, no new credential, no new network
    service and no new failure domain.

    What it is not: it does not coordinate replicas that have separate database
    files, and it is not a substitute for a proxy-side limit at the edge. Those
    remain deployment decisions, and neither is claimed here.

    Failure policy
    --------------
    A store error (locked database, disk full, schema drift) surfaces to
    :class:`RequestThrottle`, which applies ``ERP_RATE_LIMIT_FAILURE_MODE``:
    ``fail_closed`` (default) refuses the authentication attempt and logs
    ``RATE_LIMIT_BACKEND_UNAVAILABLE``; ``fail_open`` allows it and logs the same
    event. Closed is the default because an unavailable counter must not silently
    become an unlimited guessing window; open exists for operators who would
    rather keep the service available than bounded, and choosing it is recorded.

    Retention
    ---------
    Events older than the widest window are deleted (at most once per second),
    and the table is capped at ``max_rows`` so a flood cannot grow it without
    bound. Both operations run inside the same transaction as the insert.

    The cap is enforced *when a process prunes*, so under concurrent writers the
    table can stand at ``max_rows`` plus at most one event per writer that
    inserted after its own prune. That is the precise guarantee, and Phase 43
    measures it (``tests/test_phase43_limiter_hardening.py``): it is bounded, it
    is not an absolute, and no caller should treat ``max_rows`` as a hard row
    count at every instant.
    """

    name = "sqlite"
    TABLE = "rate_limit_events"

    def __init__(self, db: Any, *, max_rows: int = 200_000, prune_interval: float = 1.0) -> None:
        self._db = db
        self._max_rows = max(1, int(max_rows))
        self._prune_interval = max(0.0, float(prune_interval))
        self._last_prune = 0.0
        self._lock = threading.Lock()
        self._ensure_schema()

    # -- schema ------------------------------------------------------------
    def _ensure_schema(self) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope       TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_scope_key "
                f"ON {self.TABLE}(scope, key, occurred_at)"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self.TABLE}_occurred "
                f"ON {self.TABLE}(occurred_at)"
            )

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _retry_after(oldest: float, window_seconds: float, now: float) -> int:
        return max(1, int((window_seconds - (now - oldest)) + 0.999))

    def _prune(self, conn: Any, now: float, window_seconds: float) -> None:
        conn.execute(
            f"DELETE FROM {self.TABLE} WHERE occurred_at <= ?", (now - window_seconds,)
        )
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {self.TABLE}").fetchone()
        excess = int(row["n"]) - self._max_rows
        if excess > 0:
            conn.execute(
                f"DELETE FROM {self.TABLE} WHERE id IN "
                f"(SELECT id FROM {self.TABLE} ORDER BY id ASC LIMIT ?)",
                (excess,),
            )
        self._last_prune = now

    # -- protocol ----------------------------------------------------------
    def hit(self, scope: str, key: str, limit: int, window_seconds: float) -> Tuple[bool, int]:
        now = time.time()
        with self._db.transaction() as conn:
            if now - self._last_prune >= self._prune_interval:
                self._prune(conn, now, max(window_seconds, 60.0))
            row = conn.execute(
                f"SELECT COUNT(*) AS n, MIN(occurred_at) AS oldest FROM {self.TABLE} "
                f"WHERE scope = ? AND key = ? AND occurred_at > ?",
                (scope, key, now - window_seconds),
            ).fetchone()
            count = int(row["n"])
            allowed = count < limit
            conn.execute(
                f"INSERT INTO {self.TABLE} (scope, key, occurred_at) VALUES (?, ?, ?)",
                (scope, key, now),
            )
            oldest = row["oldest"] if row["oldest"] is not None else now
        return allowed, 0 if allowed else self._retry_after(float(oldest), window_seconds, now)

    def blocked(self, scope: str, key: str, limit: int, window_seconds: float) -> bool:
        """Is this key over its limit? A read, and nothing more.

        The original implementation answered this question inside
        ``db.transaction()``, i.e. inside ``BEGIN IMMEDIATE`` — a read that
        queued behind, and held, the application database's write lock on every
        authentication attempt (finding R-03). It now runs through
        ``ERPDatabase.read_probe``: one autocommit SELECT on a read snapshot,
        still serialised with in-process transactions by the same ``RLock``.

        This changes *when* the lock is taken, never what is counted: the same
        committed events, the same window, the same ``>= limit`` test. ``hit()``
        still records events inside a write transaction, so a block decision can
        never be based on an event that was rolled back.
        """
        now = time.time()
        sql = (
            f"SELECT COUNT(*) AS n FROM {self.TABLE} "
            f"WHERE scope = ? AND key = ? AND occurred_at > ?"
        )
        params = (scope, key, now - window_seconds)
        probe = getattr(self._db, "read_probe", None)
        if probe is not None:
            row = probe(sql, params)
        else:  # pragma: no cover - a store may be built on a minimal fake
            with self._db.transaction() as conn:
                row = conn.execute(sql, params).fetchone()
        return int(row["n"]) >= limit

    def reset(self, scope: str, key: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                f"DELETE FROM {self.TABLE} WHERE scope = ? AND key = ?", (scope, key)
            )

    def row_count(self) -> int:
        with self._db.transaction() as conn:
            return int(conn.execute(f"SELECT COUNT(*) AS n FROM {self.TABLE}").fetchone()["n"])

    def describe(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "storage": f"shared table {self.TABLE} in the application database",
            "scope": (
                "shared by every process using this database file; not a "
                "distributed bound across separate databases or replicas"
            ),
            "max_rows": self._max_rows,
            "prune_interval_seconds": self._prune_interval,
        }


def store_from_env(db: Any = None, *, max_keys: int = DEFAULT_MAX_KEYS) -> RateLimitStore:
    """Build the configured store: ``memory`` (default) or ``sqlite``."""
    backend = _env_choice(BACKEND_ENV, "memory", ("memory", "sqlite"))
    if backend == "sqlite":
        if db is None:
            raise ValueError("ERP_RATE_LIMIT_BACKEND=sqlite requires a database")
        return SqliteRateLimitStore(db)
    return MemoryRateLimitStore(max_keys=max_keys)


class RequestThrottle:
    """The rules the API enforces, wired from the environment.

    ``client_key`` is resolved by the API from the trusted-proxy policy, so an
    ``X-Forwarded-For`` header can only influence it when the operator has
    explicitly declared a trusted proxy (see :mod:`dz_erp.net`).
    """

    def __init__(
        self,
        *,
        auth_failure_limit: Optional[int] = None,
        auth_failure_window: Optional[float] = None,
        requests_per_minute: Optional[int] = None,
        client_failure_limit: Optional[int] = None,
        max_keys: Optional[int] = None,
        store: Optional[RateLimitStore] = None,
        failure_mode: Optional[str] = None,
    ) -> None:
        self.auth_limit = (
            _env_int(AUTH_FAILURE_LIMIT_ENV, DEFAULT_AUTH_FAILURE_LIMIT)
            if auth_failure_limit is None
            else int(auth_failure_limit)
        )
        self.auth_window = (
            _env_float(AUTH_FAILURE_WINDOW_ENV, DEFAULT_AUTH_FAILURE_WINDOW)
            if auth_failure_window is None
            else float(auth_failure_window)
        )
        self.client_limit = (
            _env_int(CLIENT_FAILURE_LIMIT_ENV, DEFAULT_CLIENT_FAILURE_LIMIT)
            if client_failure_limit is None
            else int(client_failure_limit)
        )
        self.max_keys = (
            _env_int(AUTH_MAX_KEYS_ENV, DEFAULT_MAX_KEYS, minimum=1)
            if max_keys is None
            else max(1, int(max_keys))
        )
        self.failure_mode = (
            _env_choice(FAILURE_MODE_ENV, DEFAULT_FAILURE_MODE, FAILURE_MODES)
            if failure_mode is None
            else (failure_mode if failure_mode in FAILURE_MODES else DEFAULT_FAILURE_MODE)
        )
        self.store: RateLimitStore = store or MemoryRateLimitStore(max_keys=self.max_keys)
        self.requests = SlidingWindowLimiter(
            _env_int(REQUESTS_PER_MINUTE_ENV, DEFAULT_REQUESTS_PER_MINUTE)
            if requests_per_minute is None
            else requests_per_minute,
            60.0,
            max_keys=self.max_keys,
        )
        #: The error raised by the *most recent* backend call, or ``None`` when
        #: that call succeeded. Cleared on every success, so a caller that reads
        #: it can never attribute an old outage to a new decision.
        self.last_backend_error: Optional[str] = None
        #: Errors raised during the public call currently in progress. Reset at
        #: the start of every public method; see :attr:`current_backend_error`.
        self._call_errors: List[str] = []
        #: Historical record, kept deliberately separate from the two above:
        #: how many backend failures have ever been seen, when the last one was,
        #: and what it said. An operator investigating a past incident needs the
        #: history; a decision needs only the present.
        self.backend_failure_count = 0
        self.last_backend_failure_at: Optional[float] = None
        self.last_backend_failure_message: Optional[str] = None

    # -- compatibility accessors ------------------------------------------
    @property
    def auth_failures(self) -> Any:
        """The per-account limiter, when the store is in memory.

        Kept so operators and tests can inspect the bucket table directly; a
        shared store has no such object, and says so.
        """
        if isinstance(self.store, MemoryRateLimitStore):
            return self.store._limiter("login", self.auth_limit, self.auth_window)
        return _SharedStoreView(self.store, "login", self.auth_limit, self.auth_window)

    # -- keys --------------------------------------------------------------
    @staticmethod
    def login_key(client: str, username: str) -> str:
        """Throttle per client *and* account, so one lockout cannot deny
        service to every user of the system."""
        return f"login|{client}|{(username or '').strip().lower()}"

    @staticmethod
    def client_key(client: str) -> str:
        """The aggregate key: one bucket per client, whatever the username."""
        return f"client|{client}"

    # -- authentication ----------------------------------------------------
    @property
    def current_backend_error(self) -> Optional[str]:
        """The backend error behind the decision currently being made, if any.

        This is the only thing a caller may use to explain a refusal. It is
        ``None`` for a refusal the *policy* produced, even if the backend has
        failed at some earlier point in this process's life — that distinction
        was the defect this property exists to remove: before it, a single
        transient error made every later ordinary 429 report
        ``RATE_LIMIT_BACKEND_UNAVAILABLE``.
        """
        return self._call_errors[0] if self._call_errors else None

    def backend_health(self) -> Dict[str, Any]:
        """Current-vs-historical backend state, without any key or username."""
        return {
            "current_error": self.current_backend_error,
            "degraded_now": self.current_backend_error is not None,
            "failure_count": self.backend_failure_count,
            "last_failure_at": self.last_backend_failure_at,
            "last_failure_message": self.last_backend_failure_message,
            "failure_mode": self.failure_mode,
        }

    def _begin_call(self) -> None:
        """Start a public operation: nothing has failed *for this call* yet."""
        self._call_errors = []

    def _call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        try:
            result = getattr(self.store, operation)(*args, **kwargs)
        except Exception as exc:  # the policy is below, not here
            message = f"{type(exc).__name__}: {exc}"
            # Current call, latest call, and history are three different facts.
            self._call_errors.append(message)
            self.last_backend_error = message
            self.backend_failure_count += 1
            self.last_backend_failure_at = time.time()
            self.last_backend_failure_message = message
            if self.failure_mode == "fail_open":
                return (True, 0) if operation == "hit" else False
            return (False, 60) if operation == "hit" else True
        # A success clears the stale error; the counters above keep the history.
        self.last_backend_error = None
        return result

    def login_blocked(self, client: str, username: str) -> Tuple[bool, int]:
        self._begin_call()
        per_account = self._call(
            "blocked", "login", self.login_key(client, username), self.auth_limit, self.auth_window
        )
        per_client = self.client_limit > 0 and bool(
            self._call(
                "blocked", "client", self.client_key(client), self.client_limit, self.auth_window
            )
        )
        if per_client:
            return True, max(1, int(self.auth_window))
        return bool(per_account), max(1, int(self.auth_window)) if per_account else 0

    def record_login_failure(self, client: str, username: str) -> Tuple[bool, int]:
        self._begin_call()
        allowed, retry = self._call(
            "hit", "login", self.login_key(client, username), self.auth_limit, self.auth_window
        )
        if self.client_limit > 0:
            client_allowed, client_retry = self._call(
                "hit", "client", self.client_key(client), self.client_limit, self.auth_window
            )
            if not client_allowed:
                return False, client_retry
        return bool(allowed), retry

    def record_login_success(self, client: str, username: str) -> None:
        # Only the per-account bucket is cleared. Clearing the per-client
        # aggregate would let an attacker who owns one valid account reset the
        # flood counter between credential-stuffing runs.
        self._begin_call()
        self._call("reset", "login", self.login_key(client, username))

    # -- overall -----------------------------------------------------------
    def request_allowed(self, client: str) -> Tuple[bool, int]:
        self._begin_call()
        allowed, retry = self.requests.hit(f"req|{client}")
        return allowed, retry


class _SharedStoreView:
    """Read-only stand-in for ``auth_failures`` when the store is shared."""

    def __init__(self, store: RateLimitStore, scope: str, limit: int, window: float) -> None:
        self._store, self._scope, self.limit, self.window_seconds = store, scope, limit, window

    def count(self, key: str) -> int:  # pragma: no cover - diagnostics only
        raise NotImplementedError(
            "per-key counts are not exposed by a shared store; see describe_throttle_policy()"
        )

    def stats(self) -> Dict[str, Any]:
        return self._store.describe()


_DEFAULT_THROTTLE = RequestThrottle()


def default_throttle() -> RequestThrottle:
    """The process-wide throttle instance (env-configured at first import)."""
    return _DEFAULT_THROTTLE


def describe_throttle_policy(throttle: Optional[RequestThrottle] = None) -> Dict[str, object]:
    """A secret-free description of the active policy, for operators."""
    t = throttle or _DEFAULT_THROTTLE
    described = {
        "auth_failure_limit": t.auth_limit,
        "auth_failure_window_seconds": t.auth_window,
        "auth_failure_enabled": t.auth_limit > 0,
        "client_failure_limit": t.client_limit,
        "requests_per_minute": t.requests.limit,
        "request_rate_enabled": t.requests.enabled,
        "failure_mode": t.failure_mode,
        "backend": t.store.describe(),
    }
    described["scope"] = described["backend"]["scope"]  # type: ignore[index]
    described["storage"] = described["backend"]["storage"]  # type: ignore[index]
    described["backend_health"] = t.backend_health()
    described["bounded"] = {
        "max_keys_per_scope": t.max_keys,
        "eviction": "least-recently-touched bucket",
        "read_only_probes_allocate": False,
    }
    return described


def format_rate_limit_scope(throttle: Optional[RequestThrottle] = None) -> str:
    """One line for logs and the readiness report: what this limiter really is."""
    t = throttle or _DEFAULT_THROTTLE
    if isinstance(t.store, SqliteRateLimitStore):
        return "shared (one database file); not distributed across databases"
    return "per-process (per worker); staging-only unless a shared backend is configured"


__all__ = [
    "AUTH_FAILURE_LIMIT_ENV",
    "AUTH_FAILURE_WINDOW_ENV",
    "AUTH_MAX_KEYS_ENV",
    "BACKEND_ENV",
    "CLIENT_FAILURE_LIMIT_ENV",
    "DEFAULT_AUTH_FAILURE_LIMIT",
    "DEFAULT_AUTH_FAILURE_WINDOW",
    "DEFAULT_CLIENT_FAILURE_LIMIT",
    "DEFAULT_MAX_KEYS",
    "FAILURE_MODE_ENV",
    "MemoryRateLimitStore",
    "RateLimitStore",
    "RequestThrottle",
    "SlidingWindowLimiter",
    "SqliteRateLimitStore",
    "default_throttle",
    "describe_throttle_policy",
    "format_rate_limit_scope",
    "store_from_env",
]
