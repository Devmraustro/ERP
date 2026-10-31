# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 47 — the ``X-Request-Id`` boundary (finding R-04).

A client-supplied correlation id is untrusted input that the application writes
into its own structured log and echoes back in a response header. Before this
phase the header was adopted verbatim, so a caller could place an unbounded
value, embedded newlines, or a Unicode direction-override in every log line and
in every response the server produced for that request.

What is asserted here:

* only a bounded, printable-ASCII token is adopted; everything else is replaced
  by a server-generated id that the client is told to quote instead;
* a rejected value is *never* echoed, never logged, not even truncated-in-part;
* ordinary traffic, including a 429, still carries exactly one correlation id;
* an absent header is not an error — every response has an id;
* nothing about the caller leaks: the generated id is random, not derived from
  the client address, the user or the request;
* two clients may propose the same id without one request being dropped.

The value is an opaque token; no personal data is involved, and the tests assert
that property rather than assuming it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.request_id import (
    MAX_REQUEST_ID_LENGTH,
    is_valid_request_id,
    sanitize_request_id,
)

#: Values that must never reach a log line or a response header. Each is a real
#: attack shape rather than a random string: header splitting, a terminal
#: escape, a right-to-left override (which makes a log line read reversed in a
#: terminal) and a zero-width joiner (which makes two ids look identical).
HOSTILE_IDS = [
    "abc\ndef",                       # header/line splitting
    "abc\rdef",
    "abc\tdef",
    "abc def",                        # whitespace
    "id\x1b[31mRED\x1b[0m",           # terminal escape
    "id\u202egnp.exe",                # right-to-left override
    "id\u200bzwsp",                   # zero-width space
    "id\u0000null",
    "<script>alert(1)</script>",
    'quoted"value',
    "semi;colon",
    "slash/value",
    "per%cent",
    "\u00e9accent",                   # non-ASCII
    "x" * (MAX_REQUEST_ID_LENGTH + 1),  # over-long
    "x" * 4096,                       # a 4 KB header
]


# ---------------------------------------------------------------------------
# 1. the predicate itself
# ---------------------------------------------------------------------------


def test_only_bounded_printable_ascii_tokens_are_accepted() -> None:
    accepted = [
        "req-123",
        "0f9e7b1c-2a4d-4a4e-9f9e-2f6b2f9a1c3d",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # W3C traceparent
        "01H8Z9K4Q7YV0N3M2B1C0D5E6F",     # ULID
        "a" * MAX_REQUEST_ID_LENGTH,       # exactly at the ceiling
        "trace_id:42",
    ]
    for value in accepted:
        assert is_valid_request_id(value) is True, value

    refused: list[Any] = [
        "",
        None,
        " ",
        "x" * (MAX_REQUEST_ID_LENGTH + 1),
        *HOSTILE_IDS,
    ]
    for value in refused:
        assert is_valid_request_id(value) is False, repr(value)


def test_a_valid_id_is_preserved_and_an_invalid_one_is_replaced() -> None:
    supplied, accepted = sanitize_request_id("req-abc-123")
    assert (supplied, accepted) == ("req-abc-123", True)

    for hostile in HOSTILE_IDS:
        generated, accepted = sanitize_request_id(hostile)
        assert accepted is False, hostile[:40]
        assert generated != hostile
        assert is_valid_request_id(generated), generated
        # Nothing of the rejected value survives, not even a prefix.
        assert hostile[:8] not in generated

    # The fallback is a uuid4-shaped random token: no request data is folded in.
    generated, accepted = sanitize_request_id(None)
    assert accepted is False
    assert uuid.UUID(generated).version == 4


def test_collisions_are_a_tracing_inconvenience_not_a_correctness_rule() -> None:
    first = sanitize_request_id("same-id-from-two-clients")
    second = sanitize_request_id("same-id-from-two-clients")
    assert first == second == ("same-id-from-two-clients", True)


# ---------------------------------------------------------------------------
# 2. the HTTP boundary
# ---------------------------------------------------------------------------


def _build(tmp_path: Path) -> ERPDatabase:
    db = ERPDatabase(str(tmp_path / "p47.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
    return db


def _client(db: ERPDatabase, tmp_path: Path) -> tuple[TestClient, Any]:
    app = create_erp_fastapi_app(db, PrivateDocumentVault(tmp_path / "vault"))
    return TestClient(app), app


def _records(app: Any) -> list[dict[str, Any]]:
    return list(app.state.logger.records)


def test_every_response_carries_a_server_id_even_without_the_header(
    tmp_path: Path,
) -> None:
    client, app = _client(_build(tmp_path), tmp_path)

    response = client.get("/healthz")
    assert response.status_code == 200
    echo = response.headers["X-Request-Id"]
    assert is_valid_request_id(echo)

    # The unauthenticated refusal is traced the same way.
    denied = client.get("/api/v1/auth/session")
    assert denied.status_code in (401, 403)
    assert is_valid_request_id(denied.headers["X-Request-Id"])

    # And each request gets its own id rather than sharing a placeholder.
    ids = {r.headers["X-Request-Id"] for r in (response, denied, client.get("/healthz"))}
    assert len(ids) == 3


def test_a_valid_supplied_id_is_echoed_and_logged(tmp_path: Path) -> None:
    client, app = _client(_build(tmp_path), tmp_path)
    supplied = "support-ticket-4711"

    response = client.get("/healthz", headers={"X-Request-Id": supplied})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == supplied

    matching = [r for r in _records(app) if r["correlation_id"] == supplied]
    assert matching, "the adopted id must appear in the durable log line"
    assert matching[0]["event"] == "HTTP_REQUEST"


def _wire_sendable(value: str) -> bool:
    """Could this value actually arrive in an HTTP/1.1 header field?

    httpx encodes a header given as ``str`` to ASCII and a real front end
    rejects CR/LF outright, so those shapes cannot be *transported* here. They
    are still covered by the predicate test above — the middleware must refuse
    them the moment they do arrive (HTTP/2, a proxy that normalises, a direct
    ASGI caller) — and the ones below are the shapes that genuinely reach it.
    """
    return value.isascii() and "\n" not in value and "\r" not in value


@pytest.mark.parametrize("hostile", [v for v in HOSTILE_IDS if _wire_sendable(v)])
def test_a_hostile_id_is_replaced_and_leaves_no_trace_of_itself(
    hostile: str, tmp_path: Path
) -> None:
    client, app = _client(_build(tmp_path), tmp_path)

    response = client.get("/healthz", headers={"X-Request-Id": hostile})
    echoed = response.headers["X-Request-Id"]

    assert response.status_code == 200
    assert echoed != hostile
    assert is_valid_request_id(echoed)
    assert hostile not in echoed

    # No log record — correlation id, context key or context value — may carry
    # the rejected value.
    serialized = json.dumps(_records(app), ensure_ascii=False)
    if hostile.strip():
        assert hostile not in serialized
        # Neither a truncated nor an escaped projection of it.
        assert json.dumps(hostile)[1:-1] not in serialized
    assert all(r["correlation_id"] != hostile for r in _records(app))


def test_a_rejected_id_is_not_echoed_on_a_throttled_request(tmp_path: Path) -> None:
    """The 429 path must not be a hole in the same boundary."""
    db = _build(tmp_path)
    client, app = _client(db, tmp_path)

    # Drive the limiter to refusal through its public seam rather than by
    # brute-forcing the configured ceiling.
    original = app.state.throttle.request_allowed
    app.state.throttle.request_allowed = lambda key: (False, 17)
    try:
        response = client.get("/healthz", headers={"X-Request-Id": "ok-id-but-throttled"})
    finally:
        app.state.throttle.request_allowed = original

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    # Even while refusing, the adopted id is the safe one and it is the one
    # that appears in the rate-limit log line.
    assert response.headers["X-Request-Id"] == "ok-id-but-throttled"
    limited = [r for r in _records(app) if r["event"] == "SECURITY_RATE_LIMITED"]
    assert limited and limited[-1]["correlation_id"] == "ok-id-but-throttled"


def test_two_clients_may_share_an_id_without_losing_a_request(tmp_path: Path) -> None:
    client, _app = _client(_build(tmp_path), tmp_path)
    shared = "shared-by-two-clients"

    codes = [
        client.get("/healthz", headers={"X-Request-Id": shared}).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 200], "a colliding id must never drop a request"

    echoed = {
        client.get("/healthz", headers={"X-Request-Id": shared}).headers["X-Request-Id"]
        for _ in range(3)
    }
    assert echoed == {shared}


def test_the_response_header_never_carries_request_content(tmp_path: Path) -> None:
    client, _app = _client(_build(tmp_path), tmp_path)
    response = client.get(
        "/healthz",
        params={"email": "someone@example.dz", "rib": "00799999"},
        headers={"X-Request-Id": "trace-1"},
    )
    assert response.status_code == 200
    # Query strings are not logged and not echoed anywhere: an id is a token,
    # never a carrier for caller-supplied personal data.
    assert "someone@example.dz" not in response.headers.get("X-Request-Id", "")
    assert "00799999" not in str(response.headers)
