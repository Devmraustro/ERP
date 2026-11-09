# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 35 — authentication throttling and request logging.

The API accepted unlimited password guesses: a wrong password cost a 401 and
nothing else. Hashing is deliberately expensive, so that endpoint was both a
credential-stuffing target and a denial-of-service lever. It is now throttled
per ``(client, account)`` before the password is verified, and every request is
correlated and logged.

What these tests hold this implementation to:

* the limit is enforced **before** password verification, and it is audited;
* it is keyed per account, so one attacker cannot lock every user out;
* a successful login clears the counter (mistyping is not a lockout);
* the refusal carries ``Retry-After`` and the clock can move on (no permanent
  lockout);
* a request id is issued/echoed and logged, and **no password or token** ever
  reaches the log;
* the optional global ceiling, when enabled, is a *request* limit that still
  lets an unthrottled client in;
* the client key honours the trusted-proxy policy — ``X-Forwarded-For`` cannot
  be used to escape a limit unless a proxy is explicitly trusted.

The last point is the security-relevant one: a limiter that keys on a
caller-supplied header is trivially bypassed by varying it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.net import TrustedProxyPolicy
from dz_erp.rate_limit import (
    RequestThrottle,
    SlidingWindowLimiter,
    describe_throttle_policy,
)

PASSWORD = "Admin#2026x"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path, *, throttle: RequestThrottle | None = None,
              trusted_proxies: TrustedProxyPolicy | None = None,
              security_log: Path | None = None):
    db = ERPDatabase(str(tmp_path / "p35.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (1, 1, 'admin_a', 'admin', 1, ?)",
            (hash_password(PASSWORD, iterations=1000),),
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (2, 1, 'clerk_a', 'clerk', 1, ?)",
            (hash_password(PASSWORD, iterations=1000),),
        )
    if security_log is not None:
        import os

        os.environ["ERP_SECURITY_LOG_PATH"] = str(security_log)
    try:
        app = create_erp_fastapi_app(
            db,
            PrivateDocumentVault(tmp_path / "vault"),
            throttle=throttle,
            trusted_proxies=trusted_proxies,
        )
    finally:
        if security_log is not None:
            import os

            os.environ.pop("ERP_SECURITY_LOG_PATH", None)
    return db, app


def _login(client: TestClient, username: str, password: str, headers=None):
    return client.post(
        "/api/v1/auth/token",
        json={"username": username, "password": password},
        headers=headers or {},
    )


def _events(app) -> list[dict]:
    """Structured log records, flattened so assertions read as ``event``/``path``."""
    flattened = []
    for record in app.state.logger.records:
        entry = {k: v for k, v in record.items() if k != "context"}
        entry.update(record.get("context") or {})
        flattened.append(entry)
    return flattened


# ---------------------------------------------------------------------------
# the limiter primitive
# ---------------------------------------------------------------------------


def test_sliding_window_counts_and_expires_fail_closed() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=0.15)
    assert limiter.hit("k") == (True, 0)
    assert limiter.hit("k") == (True, 0)
    allowed, retry = limiter.hit("k")
    assert allowed is False and retry >= 1
    assert limiter.blocked("k")[0] is True
    assert limiter.count("k") == 3  # the refused event is still recorded
    # The window genuinely moves: this is not a permanent lockout.
    import time

    time.sleep(0.2)
    assert limiter.blocked("k")[0] is False
    assert limiter.hit("k")[0] is True


def test_a_disabled_limiter_never_interferes() -> None:
    limiter = SlidingWindowLimiter(limit=0, window_seconds=60)
    assert limiter.enabled is False
    assert [limiter.hit("k")[0] for _ in range(50)] == [True] * 50
    assert limiter.blocked("k") == (False, 0)


def test_login_key_is_case_and_space_insensitive_per_account() -> None:
    """`Admin ` and `admin` must not be two independent budgets."""
    assert RequestThrottle.login_key("1.2.3.4", " Admin ") == RequestThrottle.login_key(
        "1.2.3.4", "admin"
    )
    assert RequestThrottle.login_key("1.2.3.4", "admin") != RequestThrottle.login_key(
        "5.6.7.8", "admin"
    )


def test_policy_description_is_secret_free() -> None:
    described = describe_throttle_policy(RequestThrottle())
    assert described["auth_failure_limit"] == 10
    assert described["auth_failure_enabled"] is True
    assert "in-memory" in str(described["storage"])
    assert "worker" in str(described["scope"])
    blob = json.dumps(described)
    assert "password" not in blob and "token" not in blob


# ---------------------------------------------------------------------------
# enforcement at the endpoint
# ---------------------------------------------------------------------------


def test_repeated_wrong_passwords_are_throttled_and_audited(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=3))
    client = TestClient(app)

    statuses = [_login(client, "admin_a", "Wrong#1").status_code for _ in range(3)]
    assert statuses == [401, 401, 401], statuses

    refused = _login(client, "admin_a", PASSWORD)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert "Bearer" not in refused.text and "realm" not in refused.headers

    codes = [e["event"] for e in _events(app)]
    assert codes.count("AUTH_LOGIN_REJECTED") == 3
    assert "SECURITY_AUTH_RATE_LIMITED" in codes
    # The correct password is *not* verified once throttled: that is the point.
    assert "AUTH_TOKEN_ISSUED" not in codes


def test_throttling_one_account_does_not_deny_service_to_another(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=2))
    client = TestClient(app)
    for _ in range(2):
        assert _login(client, "admin_a", "Wrong#1").status_code == 401
    assert _login(client, "admin_a", PASSWORD).status_code == 429
    ok = _login(client, "clerk_a", PASSWORD)
    assert ok.status_code == 200 and ok.json()["user"]["username"] == "clerk_a"


def test_a_successful_login_clears_the_counter(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=3))
    client = TestClient(app)
    for _ in range(2):
        assert _login(client, "admin_a", "Wrong#1").status_code == 401
    assert _login(client, "admin_a", PASSWORD).status_code == 200
    # Budget restored: two more mistypes are tolerated again.
    for _ in range(2):
        assert _login(client, "admin_a", "Wrong#2").status_code == 401
    assert _login(client, "admin_a", PASSWORD).status_code == 200


def test_the_throttle_expires_so_no_one_is_locked_out_forever(tmp_path: Path) -> None:
    _, app = _make_app(
        tmp_path, throttle=RequestThrottle(auth_failure_limit=2, auth_failure_window=0.2)
    )
    client = TestClient(app)
    for _ in range(2):
        assert _login(client, "admin_a", "Wrong#1").status_code == 401
    assert _login(client, "admin_a", PASSWORD).status_code == 429
    import time

    time.sleep(0.3)
    assert _login(client, "admin_a", PASSWORD).status_code == 200


def test_forwarded_for_cannot_escape_the_limit_without_a_trusted_proxy(tmp_path: Path) -> None:
    """The bypass that matters: keying on a caller-controlled header."""
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=2))
    # A direct (untrusted) peer: nothing may make the forwarded header win.
    client = TestClient(app, client=("198.51.100.4", 50000))
    for i in range(2):
        assert _login(
            client, "admin_a", "Wrong#1", headers={"X-Forwarded-For": f"203.0.113.{i}"}
        ).status_code == 401
    spoofed = _login(
        client, "admin_a", PASSWORD, headers={"X-Forwarded-For": "198.51.100.77"}
    )
    assert spoofed.status_code == 429


def test_a_trusted_proxy_addresses_clients_separately(tmp_path: Path) -> None:
    """Behind a declared proxy the forwarded address *is* the client identity."""
    policy = TrustedProxyPolicy(["127.0.0.1", "::1"])
    _, app = _make_app(
        tmp_path, throttle=RequestThrottle(auth_failure_limit=2), trusted_proxies=policy
    )
    # The peer must itself be in a trusted range, exactly as a real proxy would be.
    client = TestClient(app, client=("127.0.0.1", 50000))
    for _ in range(2):
        assert _login(
            client, "admin_a", "Wrong#1", headers={"X-Forwarded-For": "203.0.113.9"}
        ).status_code == 401
    assert _login(
        client, "admin_a", PASSWORD, headers={"X-Forwarded-For": "203.0.113.9"}
    ).status_code == 429
    # A different real client is not penalised for another client's failures.
    assert _login(
        client, "admin_a", PASSWORD, headers={"X-Forwarded-For": "203.0.113.10"}
    ).status_code == 200


# ---------------------------------------------------------------------------
# request logging
# ---------------------------------------------------------------------------


def test_every_response_carries_a_correlation_id(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=10))
    client = TestClient(app)
    supplied = _login(client, "admin_a", PASSWORD, headers={"X-Request-Id": "corr-123"})
    assert supplied.status_code == 200
    assert supplied.headers["X-Request-Id"] == "corr-123"

    generated = client.get("/api/v1/auth/session")
    assert generated.status_code == 401
    assert len(generated.headers["X-Request-Id"]) == 36  # a uuid4


def test_request_log_records_path_status_and_duration(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=10))
    client = TestClient(app)
    assert _login(client, "admin_a", PASSWORD).status_code == 200
    assert client.get("/api/v1/auth/session").status_code == 401

    logged = [e for e in _events(app) if e["event"] == "HTTP_REQUEST"]
    paths = [(e["method"], e["path"], e["status_code"]) for e in logged]
    assert ("POST", "/api/v1/auth/token", 200) in paths
    assert ("GET", "/api/v1/auth/session", 401) in paths
    assert all(isinstance(e["duration_ms"], float) for e in logged)
    assert all(e["correlation_id"] for e in logged)


def test_no_password_or_token_is_ever_logged(tmp_path: Path) -> None:
    """The log is evidence, not a credential leak."""
    _, app = _make_app(tmp_path, throttle=RequestThrottle(auth_failure_limit=10))
    client = TestClient(app)
    issued = _login(client, "admin_a", PASSWORD)
    token = issued.json()["access_token"]
    _login(client, "admin_a", "Wrong#1")
    client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"})

    rendered = json.dumps(_events(app))
    assert PASSWORD not in rendered
    assert "Wrong#1" not in rendered
    assert token not in rendered
    assert token[:12] not in rendered


def test_the_request_log_lands_in_the_durable_security_sink(tmp_path: Path) -> None:
    sink_path = tmp_path / "security.jsonl"
    _, app = _make_app(
        tmp_path, throttle=RequestThrottle(auth_failure_limit=10), security_log=sink_path
    )
    client = TestClient(app)
    assert _login(client, "admin_a", PASSWORD).status_code == 200
    lines = [json.loads(line) for line in sink_path.read_text().splitlines() if line.strip()]
    assert any(rec.get("event") == "HTTP_REQUEST" for rec in lines)
    assert PASSWORD not in sink_path.read_text()


# ---------------------------------------------------------------------------
# optional global ceiling
# ---------------------------------------------------------------------------


def test_global_ceiling_is_off_by_default_and_enforces_when_configured(tmp_path: Path) -> None:
    _, app = _make_app(tmp_path, throttle=RequestThrottle(requests_per_minute=0))
    client = TestClient(app)
    for _ in range(40):
        assert client.get("/api/v1/auth/session").status_code == 401

    _, limited = _make_app(
        tmp_path / "limited", throttle=RequestThrottle(requests_per_minute=5)
    )
    limited_client = TestClient(limited)
    statuses = [limited_client.get("/api/v1/auth/session").status_code for _ in range(7)]
    assert statuses[:5] == [401] * 5
    assert statuses[5:] == [429, 429]
    throttled = [e for e in _events(limited) if e["event"] == "SECURITY_RATE_LIMITED"]
    assert throttled and all(e["path"] == "/api/v1/auth/session" for e in throttled)


def test_throttle_defaults_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERP_AUTH_FAILURE_LIMIT", "4")
    monkeypatch.setenv("ERP_AUTH_FAILURE_WINDOW_SECONDS", "90")
    monkeypatch.setenv("ERP_RATE_LIMIT_PER_MINUTE", "600")
    throttle = RequestThrottle()
    assert throttle.auth_failures.limit == 4
    assert throttle.auth_failures.window_seconds == 90.0
    assert throttle.requests.limit == 600

    monkeypatch.setenv("ERP_AUTH_FAILURE_LIMIT", "not-a-number")
    monkeypatch.setenv("ERP_AUTH_FAILURE_WINDOW_SECONDS", "-5")
    monkeypatch.setenv("ERP_RATE_LIMIT_PER_MINUTE", "-1")
    sane = RequestThrottle()
    assert sane.auth_failures.limit == 10  # documented default, not garbage
    assert sane.auth_failures.window_seconds == 300.0
    assert sane.requests.limit == 0
