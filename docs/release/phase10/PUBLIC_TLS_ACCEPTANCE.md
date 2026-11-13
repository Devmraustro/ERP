# Phase 10 — Public TLS acceptance procedure

**Status: NOT EXECUTED — no public hostname and no publicly-trusted certificate
exist in this environment. Therefore *no* TLS or HTTPS claim is made for this
release.**

This document is a procedure, not a result. It says exactly what must be
demonstrated on the deployment host, how it is demonstrated, and what evidence is
retained. Every row is `NOT EXECUTED` until someone runs it against a real
hostname and pastes the log.

---

## 1. Why nothing is claimed

| Prerequisite | Needed for | Available here? |
| --- | --- | --- |
| A real public DNS name controlled by the operator | DNS, SAN, Host allow-listing, redirects | **No** |
| A certificate issued by a public CA (Let's Encrypt, or a commercial CA) | chain validation, expiry, SAN, HSTS | **No** |
| A public-facing reverse proxy in front of the app | proxy headers, redirect, HSTS, isolation | **No** |
| Network path from the public internet to that proxy | everything above | **No** |

The sandbox has loopback only. A self-signed certificate or `localhost` proof
would demonstrate nothing about a public deployment, so none was produced and no
`PRODUCTION READY` claim depends on one.

## 2. The executable procedure

`docs/release/phase10/public_tls_validation.sh` implements thirteen checks and
refuses to guess: it takes the hostname as an argument, and with no arguments it
prints usage and exits 2.

```
$ bash docs/release/phase10/public_tls_validation.sh
usage: public_tls_validation.sh <public-hostname> [port]
$ echo $?
2
```

Run on the deployment host (or any host with a clean path to it):

```
ERP_BACKEND_HOST=127.0.0.1 ERP_BACKEND_PORT=8000 \
  bash docs/release/phase10/public_tls_validation.sh erp.example.dz 443 \
  | tee "evidence/logs/public_tls_$(date -u +%Y%m%dT%H%M%SZ).log"
```

Exit codes: `0` all checks pass, `1` at least one check failed, `2` bad usage.
Every check writes `[ PASS ]`, `[ FAIL ]` or `[ NOTE ]` to the log, and raw
`openssl s_client` / `curl` output is kept beside it so the result can be re-read
without re-running.

## 3. What each check proves

| # | Check | Proves | Failure means |
| --- | --- | --- | --- |
| 1 | DNS resolution, and 1b public address | the name exists and resolves to a routable address, not loopback/RFC1918 | the name is wrong or split-horizon DNS is misconfigured |
| 2 | Chain validation with SNI + hostname | a default client trusts the chain without `-k` or a custom CA | the chain is incomplete or the CA is private — **not** public TLS |
| 3 | Chain completeness (≥2 certs served) | intermediates are sent, so older clients and some mobile stacks work | clients with thin trust stores fail |
| 4 | Expiry (fails if expired, notes inside 30 days) | renewal is under control | outage risk at the next rotation |
| 5 | SAN covers the hostname | modern clients match SAN, not CN | browsers reject even if `openssl` passes |
| 6 | `http://` → `https://` redirect (301/302/307/308) | users cannot silently stay on plaintext | credentials can traverse in clear |
| 7 | `Strict-Transport-Security` with `max-age` | the edge asserts HTTPS-only to browsers | downgrade window on first visit / MITM |
| 8 | Hostname enforcement (mismatched name refused) | verification is real, not a catch-all cert | the "TLS" is decorative |
| 9 | Proxy header pass-through | the app's headers survive the hop | app-level controls are being rewritten |
| 10 | Host allow-list through the proxy (spoofed `Host` → 400) | `ERP_ALLOWED_HOSTS` still applies at the edge | Host-header attacks reach the app |
| 11 | Forwarded-header trust (`X-Forwarded-For` from an untrusted peer) | a client cannot forge its own source address | audit logs and rate limits become forgeable |
| 12 | Backend isolation (app port not publicly reachable) | only the proxy is exposed | the proxy's TLS is bypassable on the backend port |
| 13 | Readiness through the proxy (`/readyz`) | the deployed stack answers as ready | the edge is up but the app is not |

## 4. Application-side controls this depends on

These are implemented in the codebase and covered by the regression suite; the
TLS procedure verifies that they survive the proxy hop:

| Control | Location |
| --- | --- |
| Host allow-listing (`ERP_ALLOWED_HOSTS`, default deny of unknown hosts) | `dz_erp/api.py` (trusted-host middleware) |
| Trusted-proxy handling (`ERP_TRUSTED_PROXIES`) | `dz_erp/api.py` |
| Readiness probe `GET /readyz` (DB + OCR + disk) | `dz_erp/readiness.py`, `dz_erp/api.py` |
| Health probe `GET /healthz` | `dz_erp/api.py` |

The three probes are part of the pinned public OpenAPI set (4 public operations,
26 protected), asserted by `tests/test_phase7_authentication_remediation.py` and
`tests/test_phase8_upload_boundary_and_ci.py`.

## 5. Acceptance record (to be filled on the deployment host)

| Field | Value |
| --- | --- |
| Hostname | `PENDING` |
| Certificate issuer (CA) | `PENDING` |
| Not before / not after | `PENDING` |
| Checks passed / total | `PENDING` (0 / 13 executed) |
| Log path | `PENDING` |
| Executed by / date | `PENDING` |
| Result | **NOT EXECUTED** |

Sign-off: `__________________` (operations) `__________________` (security)

## 6. Consequences for the classification

Public TLS acceptance is one of the four gates that keep this release at
**CONDITIONALLY PRODUCTION READY / IMPLEMENTATION VERIFIED**. It cannot be waived
by code: nothing in the repository can substitute for a real hostname, a real
public CA and a real network path.
