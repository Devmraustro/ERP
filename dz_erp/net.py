# SPDX-License-Identifier: LGPL-3.0-only
"""Trusted-proxy policy for forwarded client-IP headers.

``X-Forwarded-For`` (and the ``Forwarded`` / ``X-Real-IP`` family) are ordinary
request headers: any caller can set them.  Treating them as evidence of the
client's address therefore lets a caller choose the IP that lands in the audit
trail, and would let a caller defeat any future IP-based rate limit or
access-control rule simply by editing a header.

The policy implemented here is deliberately fail-closed:

* **No trusted proxies are configured by default.**  In that state forwarded
  headers are ignored entirely and the direct peer address is used, which is the
  only value the application can actually observe.
* Forwarded headers are honoured **only** when the immediate peer is itself a
  configured trusted proxy.
* When honoured, the *right-most* address that is not a trusted proxy is used,
  so a client cannot prepend fake entries to the chain.
* Malformed, duplicated, or empty values degrade to the peer address rather
  than raising, and never influence authentication or authorisation.

Deployment note: set ``ERP_TRUSTED_PROXIES`` to the CIDR ranges of your own
load balancer / reverse proxy *only*.  Listing an untrusted range there would
hand IP spoofing straight back to the caller.
"""

import ipaddress
import os
from typing import Iterable, List, Optional, Tuple

ENV_TRUSTED_PROXIES = "ERP_TRUSTED_PROXIES"
ENV_ALLOWED_HOSTS = "ERP_ALLOWED_HOSTS"
UNKNOWN_IP = "unknown"

#: Hostnames accepted when ``ERP_ALLOWED_HOSTS`` is unset. They cover loopback
#: development, the container on a private network and the test client, and are
#: the *only* names an unconfigured deployment will answer to: a request with
#: any other ``Host`` header is refused with 400. A production deployment sets
#: its real name(s); see docs/PRODUCTION_DEPLOYMENT_GUIDE.md.
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]", "testserver")


class TrustedProxyPolicy:
    """Decides whether a forwarded client IP may be believed."""

    def __init__(self, trusted_proxies: Optional[Iterable[str]] = None) -> None:
        self._networks: List[ipaddress._BaseNetwork] = []
        self._raw: Tuple[str, ...] = tuple()
        for entry in trusted_proxies or ():
            parsed = self._parse_entry(entry)
            if parsed is not None:
                self._networks.append(parsed)
                self._raw += (str(parsed),)

    @staticmethod
    def _parse_entry(entry: str) -> Optional[ipaddress._BaseNetwork]:
        text = (entry or "").strip()
        if not text:
            return None
        try:
            if "/" in text:
                return ipaddress.ip_network(text, strict=False)
            # A bare address behaves like a /32 (or /128) network.
            return ipaddress.ip_network(f"{text}/32" if ":" not in text else f"{text}/128", strict=False)
        except ValueError:
            return None

    @property
    def trusted_proxies(self) -> Tuple[str, ...]:
        """Normalised, human-readable list of trusted ranges (may be empty)."""
        return self._raw

    def is_trusted(self, ip: Optional[str]) -> bool:
        """Whether ``ip`` falls inside a configured trusted-proxy range."""
        if not ip or not self._networks:
            return False
        try:
            address = ipaddress.ip_address(ip.strip())
        except ValueError:
            return False
        return any(address in network for network in self._networks)

    def resolve_client_ip(
        self,
        peer_ip: Optional[str],
        forwarded_for: Optional[str] = None,
    ) -> str:
        """Return the best-supported client IP for ``peer_ip``.

        Forwarded headers are consulted only when the peer is a trusted proxy.
        """
        if not peer_ip or not peer_ip.strip():
            return UNKNOWN_IP
        peer = peer_ip.strip()

        if not self.is_trusted(peer):
            # Not behind a trusted proxy: the forwarded header is untrusted
            # caller-supplied data and is discarded.
            return peer

        chain = parse_forwarded_for(forwarded_for)
        if not chain:
            return peer

        # Walk right-to-left and stop at the first address that is not itself a
        # trusted proxy, so entries a client prepends cannot win.
        for candidate in reversed(chain):
            if not self.is_trusted(candidate):
                return candidate
        return peer


def parse_forwarded_for(header_value: Optional[str]) -> List[str]:
    """Split an ``X-Forwarded-For`` value into validated IP strings.

    ``X-Forwarded-For`` is a comma-separated list where each proxy appends the
    address it received from.  Malformed entries are dropped; an all-malformed
    value yields an empty list so callers fall back to the peer address.
    """
    if not header_value or not header_value.strip():
        return []
    result: List[str] = []
    for part in header_value.split(","):
        token = part.strip()
        # Strip an optional :port suffix, which some proxies append.
        if token.startswith("["):  # IPv6 literal, possibly with a port
            token = token[1:].split("]", 1)[0]
        elif token.count(":") == 1 and token.rsplit(":", 1)[1].isdigit():
            token = token.rsplit(":", 1)[0]
        try:
            ipaddress.ip_address(token)
        except ValueError:
            continue
        result.append(token)
    return result


def trusted_proxy_policy_from_env(
    env: Optional[dict] = None,
    key: str = ENV_TRUSTED_PROXIES,
) -> TrustedProxyPolicy:
    """Build a policy from ``ERP_TRUSTED_PROXIES`` (comma/space separated CIDRs)."""
    source = os.environ if env is None else env
    raw = source.get(key) or ""
    entries = [item for item in raw.replace(",", " ").split() if item.strip()]
    return TrustedProxyPolicy(entries)


def describe_trusted_proxy_policy(policy: TrustedProxyPolicy) -> dict:
    """Machine-readable description of the active forwarded-header policy."""
    configured = policy.trusted_proxies
    return {
        "trusted_proxies": list(configured),
        "trusted_proxy_count": len(configured),
        "forwarded_headers_honoured": bool(configured),
        "default_behaviour": (
            "forwarded headers are ignored; the direct peer address is used"
            if not configured
            else "forwarded headers are honoured only for requests arriving from a trusted proxy"
        ),
        "env_override": ENV_TRUSTED_PROXIES,
    }


# ---------------------------------------------------------------------------
# Host header validation (SEC-15)
# ---------------------------------------------------------------------------
# ``Host`` is caller-controlled, and the application never validated it. Behind a
# proxy that routes by name (or a cache that keys on it) an attacker-chosen Host
# is the ingredient of cache poisoning, absolute-URL generation and — once a
# password-reset mailer exists — reset-link poisoning. The proxy SHOULD reject
# unknown names first; this is the in-process backstop that makes the answer
# independent of proxy configuration mistakes.


def parse_allowed_hosts(raw: object) -> Tuple[str, ...]:
    """Normalise a comma/space separated host list into comparable patterns.

    Entries are lower-cased, port suffixes are dropped, and a leading ``*.`` is
    preserved as a wildcard. Empty or nonsensical entries are ignored rather
    than widening the list.
    """
    if isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = str(raw or "").replace(",", " ").split()
    result: List[str] = []
    for item in items:
        candidate = item.strip().lower()
        if not candidate:
            continue
        wildcard = candidate.startswith("*.")
        if wildcard:
            candidate = candidate[2:]
        candidate = _strip_port(candidate)
        if not candidate or "/" in candidate or any(ch.isspace() for ch in candidate):
            continue
        result.append(f"*.{candidate}" if wildcard else candidate)
    return tuple(dict.fromkeys(result))


def _strip_port(host: str) -> str:
    """Drop a trailing ``:port`` from a Host value, keeping IPv6 literals intact."""
    if not host:
        return ""
    if host.startswith("["):
        closing = host.find("]")
        return host[: closing + 1] if closing != -1 else host
    if host.count(":") == 1:
        name, _, port = host.partition(":")
        if port.isdigit():
            return name
    return host


def allowed_hosts_from_env(env: Optional[dict] = None) -> Tuple[str, ...]:
    """The effective allow-list: ``ERP_ALLOWED_HOSTS`` or the loopback defaults."""
    source = os.environ if env is None else env
    parsed = parse_allowed_hosts(source.get(ENV_ALLOWED_HOSTS) or "")
    return parsed or DEFAULT_ALLOWED_HOSTS


def host_is_allowed(host_header: Optional[str], allowed: Iterable[str]) -> bool:
    """Whether a ``Host`` header value matches the allow-list.

    A missing or empty header is refused: an HTTP/1.0 client that omits ``Host``
    is not something this API supports, and “no name” must never mean “any
    name”.
    """
    host = _strip_port(str(host_header or "").strip().lower())
    if not host:
        return False
    for pattern in allowed:
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".example.dz"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


def describe_allowed_hosts(allowed: Iterable[str]) -> dict:
    """Machine-readable description of the active Host policy (for operators)."""
    entries = list(allowed)
    return {
        "allowed_hosts": entries,
        "allowed_host_count": len(entries),
        "wildcards": [entry for entry in entries if entry.startswith("*.")],
        "env_override": ENV_ALLOWED_HOSTS,
        "default_behaviour": (
            "only the loopback/test names are accepted; set ERP_ALLOWED_HOSTS to the "
            "deployment's public name(s) before exposing the API"
        ),
    }
