# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 57 — IPv6 aggregation investigation and trusted-proxy contract tests.

The rate limiter keys on the client IP returned by ``TrustedProxyPolicy``. IPv6
clients rotate addresses inside a /64 prefix, so a naive per-address key lets an
attacker obtain a fresh aggregate per address. This module pins the *current*
contract so any change to client-identity semantics is detected:

* IPv6 addresses are compared **verbatim today**: two addresses in the same /64
  are two distinct clients. Prefix-level (/64) aggregation is a candidate
  mitigation but it changes client identity semantics and is **not** implemented
  — there is no production evidence of IPv6 rotation abuse, and collapsing a /64
  would also collapse legitimate users behind carrier-grade NAT64. The decision
  is recorded as an open, documented item (see the note below), not as a silent
  default.
* Trusted-proxy handling and spoofed ``X-Forwarded-For`` rejection are fail-closed
  and unchanged.

If /64 aggregation is ever adopted, it must be (a) behind an explicit opt-in
configuration, (b) covered by these tests being updated in the same commit, and
(c) accompanied by a migration note for operators whose existing counters key on
the full address.
"""

from __future__ import annotations

import ipaddress

from dz_erp.net import TrustedProxyPolicy, parse_forwarded_for


def test_ipv4_addresses_are_distinct_clients() -> None:
    policy = TrustedProxyPolicy()
    assert policy.resolve_client_ip("198.51.100.1") == "198.51.100.1"
    assert policy.resolve_client_ip("198.51.100.2") == "198.51.100.2"
    assert policy.resolve_client_ip("198.51.100.1") != policy.resolve_client_ip("198.51.100.2")


def test_ipv6_addresses_in_one_slash64_are_distinct_today() -> None:
    """Current contract: no /64 aggregation. Two addresses in one prefix are two
    clients. This pins the behaviour so a future aggregation change is explicit."""
    a = "2001:db8:abcd:1234::1"
    b = "2001:db8:abcd:1234::2"
    assert ipaddress.ip_address(a) in ipaddress.ip_network("2001:db8:abcd:1234::/64")
    assert ipaddress.ip_address(b) in ipaddress.ip_network("2001:db8:abcd:1234::/64")
    policy = TrustedProxyPolicy()
    assert policy.resolve_client_ip(a) == a
    assert policy.resolve_client_ip(b) == b
    assert policy.resolve_client_ip(a) != policy.resolve_client_ip(b)


def test_ipv6_addresses_in_different_slash64_are_distinct() -> None:
    a = "2001:db8:abcd:1234::1"
    b = "2001:db8:abcd:5678::1"
    policy = TrustedProxyPolicy()
    assert policy.resolve_client_ip(a) != policy.resolve_client_ip(b)


def test_trusted_proxy_ipv6_is_honoured() -> None:
    policy = TrustedProxyPolicy(["::1"])
    assert policy.is_trusted("::1")
    assert policy.resolve_client_ip("::1", "2001:db8::9") == "2001:db8::9"


def test_spoofed_forwarded_for_is_ignored_for_untrusted_peer() -> None:
    policy = TrustedProxyPolicy(["127.0.0.1"])
    # Peer is not trusted -> the forwarded header is caller-supplied and discarded.
    assert policy.resolve_client_ip("198.51.100.7", "1.2.3.4") == "198.51.100.7"


def test_parse_forwarded_for_handles_ipv6_literals() -> None:
    chain = parse_forwarded_for("2001:db8::1, 2001:db8::2")
    assert chain == ["2001:db8::1", "2001:db8::2"]
    # bracketed literal with port
    assert parse_forwarded_for("[2001:db8::1]:8080") == ["2001:db8::1"]


def test_malformed_forwarded_for_degrades_to_peer() -> None:
    policy = TrustedProxyPolicy(["127.0.0.1"])
    # all-malformed -> empty chain -> falls back to peer
    assert policy.resolve_client_ip("127.0.0.1", "not-an-ip, also-bad") == "127.0.0.1"
    assert parse_forwarded_for("garbage") == []
