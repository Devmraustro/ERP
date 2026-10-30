# SPDX-License-Identifier: LGPL-3.0-only
"""In-process request throttling for the HTTP surface.

Why this exists
---------------
The API had no bound on repeated failures. An attacker holding a valid username
could submit unlimited password guesses to ``POST /api/v1/auth/token``; the only
cost of a wrong guess was a 401. Password hashing makes each attempt expensive
*for the server*, which turns an unbounded endpoint into a denial-of-service
lever as much as a credential-stuffing one.

What this is — and is not
-------------------------
It is a **sliding-window counter held in process memory**. That is honest and
useful for the single-process deployment this repository ships, and it is
deliberately not dressed up as more:

* it is per worker. With ``--workers N`` (or several containers behind a proxy)
  each process keeps its own counters, so the effective limit is ``N × limit``.
  A shared store (Redis and friends) would be needed for an exact global bound;
  this repository has no such dependency and does not pretend to.
* it is not a substitute for a reverse-proxy rate limit, an account lockout
  policy, or MFA. Those remain deployment/integration decisions.

The two rules it does enforce, both fail-closed:

1. **Authentication failures** — after ``ERP_AUTH_FAILURE_LIMIT`` failed attempts
   for one ``(client, username)`` inside ``ERP_AUTH_FAILURE_WINDOW_SECONDS``,
   further attempts are refused with 429 and ``Retry-After`` *before* the
   password is verified, so a locked-out attacker cannot keep burning CPU. A
   successful authentication clears that key's counter, so a legitimate user who
   mistypes a few times is not locked out.
2. **Overall request rate** — an optional per-client ceiling
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
from collections import deque
from typing import Deque, Dict, Optional, Tuple

#: Default failed-attempt ceiling per (client, username) inside the window.
DEFAULT_AUTH_FAILURE_LIMIT = 10
#: Default window for failed attempts, in seconds.
DEFAULT_AUTH_FAILURE_WINDOW = 300.0
#: Default overall per-client request ceiling per minute (0 disables the rule).
DEFAULT_REQUESTS_PER_MINUTE = 0

AUTH_FAILURE_LIMIT_ENV = "ERP_AUTH_FAILURE_LIMIT"
AUTH_FAILURE_WINDOW_ENV = "ERP_AUTH_FAILURE_WINDOW_SECONDS"
REQUESTS_PER_MINUTE_ENV = "ERP_RATE_LIMIT_PER_MINUTE"


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


class SlidingWindowLimiter:
    """A thread-safe sliding-window counter keyed by an opaque string.

    ``hit()`` records an event and reports whether the key is now over its
    limit; ``retry_after()`` reports how long until the oldest event leaves the
    window, which is what a caller should put in ``Retry-After``.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = int(limit)
        self.window_seconds = float(window_seconds)
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    # -- internals ---------------------------------------------------------
    def _prune(self, key: str, now: float) -> Deque[float]:
        bucket = self._events.get(key)
        if bucket is None:
            bucket = deque()
            self._events[key] = bucket
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    # -- public API --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def count(self, key: str) -> int:
        """Events currently inside the window for ``key``."""
        now = time.monotonic()
        with self._lock:
            return len(self._prune(key, now)) if self.enabled else 0

    def blocked(self, key: str) -> Tuple[bool, int]:
        """Is ``key`` over its limit? Returns ``(blocked, retry_after_seconds)``."""
        if not self.enabled:
            return False, 0
        now = time.monotonic()
        with self._lock:
            bucket = self._prune(key, now)
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
        now = time.monotonic()
        with self._lock:
            bucket = self._prune(key, now)
            allowed = len(bucket) < self.limit
            bucket.append(now)
            retry = 0 if allowed else max(1, int((self.window_seconds - (now - bucket[0])) + 0.999))
        return allowed, retry

    def reset(self, key: str) -> None:
        """Forget a key entirely (used when a login succeeds)."""
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class RequestThrottle:
    """The two rules the API enforces, wired from the environment.

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
    ) -> None:
        self.auth_failures = SlidingWindowLimiter(
            _env_int(AUTH_FAILURE_LIMIT_ENV, DEFAULT_AUTH_FAILURE_LIMIT)
            if auth_failure_limit is None
            else auth_failure_limit,
            _env_float(AUTH_FAILURE_WINDOW_ENV, DEFAULT_AUTH_FAILURE_WINDOW)
            if auth_failure_window is None
            else auth_failure_window,
        )
        self.requests = SlidingWindowLimiter(
            _env_int(REQUESTS_PER_MINUTE_ENV, DEFAULT_REQUESTS_PER_MINUTE)
            if requests_per_minute is None
            else requests_per_minute,
            60.0,
        )

    # -- authentication ----------------------------------------------------
    @staticmethod
    def login_key(client: str, username: str) -> str:
        """Throttle per client *and* account, so one lockout cannot deny
        service to every user of the system."""
        return f"login|{client}|{(username or '').strip().lower()}"

    def login_blocked(self, client: str, username: str) -> Tuple[bool, int]:
        return self.auth_failures.blocked(self.login_key(client, username))

    def record_login_failure(self, client: str, username: str) -> Tuple[bool, int]:
        return self.auth_failures.hit(self.login_key(client, username))

    def record_login_success(self, client: str, username: str) -> None:
        self.auth_failures.reset(self.login_key(client, username))

    # -- overall -----------------------------------------------------------
    def request_allowed(self, client: str) -> Tuple[bool, int]:
        return self.requests.hit(f"req|{client}")


_DEFAULT_THROTTLE = RequestThrottle()


def default_throttle() -> RequestThrottle:
    """The process-wide throttle instance (env-configured at first import)."""
    return _DEFAULT_THROTTLE


def describe_throttle_policy(throttle: Optional[RequestThrottle] = None) -> Dict[str, object]:
    """A secret-free description of the active policy, for operators."""
    t = throttle or _DEFAULT_THROTTLE
    return {
        "auth_failure_limit": t.auth_failures.limit,
        "auth_failure_window_seconds": t.auth_failures.window_seconds,
        "auth_failure_enabled": t.auth_failures.enabled,
        "requests_per_minute": t.requests.limit,
        "request_rate_enabled": t.requests.enabled,
        "scope": "per-process (per worker; not a global bound across replicas)",
        "storage": "in-memory sliding window",
    }
