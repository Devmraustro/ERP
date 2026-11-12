# Security Closure Report — Phase 8 (controls C1–C9)

**Baseline:** PR #7 head `f603e38` · **CPython 3.12.10** · hash-pinned lock
**Executed:** 2026-11-09 · **Evidence:** `docs/release/phase8/evidence/logs/security_suites.log`

Every control below is backed by an **executed** test. No control is closed on code
review alone.

## 1. The executable control matrix

New module: `tests/test_phase61_security_controls.py` — 20 tests, 11 controls.

| Control | Requirement | Test(s) | Result |
| --- | --- | --- | --- |
| C1 | Authentication is enforced | `test_unauthenticated_access_is_rejected`, `test_a_forged_token_is_rejected` | PASS |
| C2 | Multi-tenant isolation is enforced | `test_cross_tenant_reads_are_refused`, `test_tenant_scoped_document_access_is_refused` | PASS |
| C3 | Rate limiting is real (429 + `Retry-After`) | `test_repeated_failures_return_429_with_retry_after` | PASS |
| C4 | Request-size limit is enforced (413) | `test_oversized_json_is_rejected_with_413`, `test_a_normal_sized_request_still_succeeds` | PASS |
| C5 | Client identity cannot be spoofed via `X-Forwarded-For` | `test_forwarded_for_cannot_rotate_client_identity` | PASS |
| C5b | Operator policy surface reports host + limits | `test_operator_policy_surface_reports_host_and_limits` | PASS |
| C6 | Trusted proxies are explicit; empty config fails **closed** | `test_trusted_proxy_policy_defaults_to_fail_closed` | PASS |
| C7 | Forwarded headers honoured only from a trusted peer | `test_forwarded_headers_honoured_only_from_trusted_peer` | PASS |
| C8 | Secrets never reach the security log | `test_credentials_never_reach_the_security_log` | PASS |
| C9 | Host header validated; hardening headers present | `test_host_header_injection_is_rejected`, `test_security_headers_are_present` | PASS |

## 2. Observed contracts (asserted, not assumed)

- **413:** status `413`, body `{"detail":"Request body exceeds the maximum of 4096 bytes."}`.
  A normal-sized login on the same app still returns `200`.
- **429:** appears on the 3rd consecutive failure with a numeric `Retry-After`; a
  different account is unaffected (the bucket is per client **and** per account — it
  is not a global lockout). The body echoes neither the password nor the username.
- **`/api/v1/system/rate-limit-policy`** returns exactly
  `{company_id, host_policy, rate_limit}`. `host_policy.allowed_hosts` =
  `localhost, 127.0.0.1, [::1], testserver`. There is **no** `trusted_proxy_policy`
  key in this response; trusted-proxy behaviour is asserted on `TrustedProxyPolicy`
  directly.
- **Hardening headers** on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `X-Request-Id`.
- **Env knobs verified present:** `ERP_AUTH_FAILURE_LIMIT`,
  `ERP_AUTH_FAILURE_WINDOW_SECONDS`, `ERP_AUTH_CLIENT_FAILURE_LIMIT`,
  `ERP_AUTH_FAILURE_MAX_KEYS`, `ERP_RATE_LIMIT_PER_MINUTE`,
  `ERP_RATE_LIMIT_BACKEND`, `ERP_RATE_LIMIT_FAILURE_MODE`,
  `ERP_MAX_REQUEST_BYTES`, `ERP_ALLOWED_HOSTS`, `ERP_TRUSTED_PROXIES`.

## 3. Known inconsistency (recorded, not papered over)

The 413 response carries **no `X-Error-Code` header**, while several other error
paths do. This is a response-contract inconsistency of low severity: clients cannot
classify the 413 by header, only by status code. The first version of the C4 test
asserted the header and failed; the assertion was corrected to the real contract and
the discrepancy is recorded here rather than converted into a silent pass.

## 4. Full security suite bundle (executed)

| Module | Tests | Result |
| --- | --- | --- |
| `test_phase35_api_rate_limiting_and_request_logging` | 16 | passed |
| `test_phase39_bounded_rate_limiting` | 32 | passed |
| `test_phase43_limiter_hardening` | 17 | passed |
| `test_phase47_request_id_boundary` | 19 | passed |
| `test_phase57_ipv6_aggregation_and_trusted_proxy` | 7 | passed |
| `test_phase58_security_selective_integration` | 92 | passed |
| `test_phase59_sec13_14_15_closure` | 45 | passed |
| `test_phase61_security_controls` | 20 | passed |
| **Total** | **248** | **all passed** |

Command: `/tmp/run9.sh tests/test_phase35… tests/test_phase61_security_controls.py`
(see `evidence/logs/security_suites.log` for the per-module summary lines).

## 5. What is *not* closed by this report

- **TLS and reverse-proxy behaviour** — see `REVERSE_PROXY_TLS_ACCEPTANCE.md`;
  public-CA validation is **NOT EXECUTED**.
- **Log redaction under a real log shipper** — verified at the application log
  handler only, not through an external aggregator.
- **Penetration testing, third-party audit, and dependency-CVE acceptance** are
  outside what a test suite can assert; `pip-audit` results are in the Phase 9
  security report.
- A green security suite is **not** a security certification. It demonstrates that
  these eleven controls behave as specified on these inputs.
