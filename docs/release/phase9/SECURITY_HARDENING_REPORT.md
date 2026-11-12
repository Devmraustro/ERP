# Phase 9A — Security Hardening Verification

**Baseline:** PR #7 head `f603e38` · CPython 3.12.10 · executed 2026-11-09
**Related:** `docs/release/phase8/SECURITY_CLOSURE_REPORT.md` (controls C1–C9)

## 1. Purpose

Phase 8 proved the eleven named controls. Phase 9A asks the harder question: *where
can the boundary still be crossed?* — and records each answer either as an executed
test or as an explicit open risk.

## 2. Executed evidence

| Area | Executed | Result |
| --- | --- | --- |
| Control matrix C1–C9 (20 tests) | `tests/test_phase61_security_controls.py` | 20 passed |
| Rate-limit suites (phase35/39/43/57) | 74 tests | passed |
| Request-id boundary (phase47) | 19 tests | passed |
| Selective integration (phase58) | 92 tests | passed |
| SEC-13/14/15 closure (phase59) | 45 tests | passed |
| TLS/proxy acceptance (real proxy + backend) | 13 checks | 11 PASS, 1 NOT EXECUTED, 1 OBSERVED |
| Dependency audit | `pip-audit` on the pinned lock | see `FINAL_PRODUCTION_READINESS_REPORT.md` |

Total security tests executed across the tree: **248** in the security bundle plus the
whole-suite run (see Phase 8 §4 and the final gate).

## 3. Boundary-by-boundary result

| Boundary | Probe | Outcome |
| --- | --- | --- |
| Authentication | no token, malformed token, forged signature, expired | `401`, no data returned |
| Authorisation | clerk calling an accountant-only action | `403` (executed in the workflow drill: `clerk_self_approval=403`) |
| Tenant isolation | company A reading company B's invoice/decision/document | `403`, audited; cross-company reversal also refused |
| Client identity | `X-Forwarded-For` rotation from an untrusted peer | ignored; rotating the header does not reset the limiter (`[401,401,401,429,429,429]`) |
| Host header | `Host: evil.example` | `400` even with a spoofed `X-Forwarded-Proto` |
| Body size | 8 KiB body against a 4 KiB cap | `413`, nothing persisted |
| Availability | 3 failed logins | `429` + `Retry-After`, other accounts unaffected |
| Limiter backend failure | forced backend failure | **fail closed** |
| Proxy trust | empty `ERP_TRUSTED_PROXIES` | forwarded headers ignored (fail closed) |
| Secrets in logs | login success/failure logging | no password, no token, no username echoed in the limiter body |
| Response hardening | every response | `nosniff`, `DENY`, `no-referrer`, `X-Request-Id` |

## 4. Risks that remain open (admitted, not hidden)

1. **Backend port exposure is a deployment property.** The app answers on whatever
   interface it is bound to. If an operator binds `0.0.0.0` and publishes the port,
   TLS is bypassed. Mitigation is deployment discipline + the runbook check in
   Phase 9J; the application cannot enforce it.
2. **No public-CA TLS validation.** NOT EXECUTED (see Phase 8C).
3. **413 lacks a machine-readable error header.** Contract inconsistency, low severity.
4. **`/healthz` is liveness only** — a wedged database still reports healthy.
5. **No WAF / no rate limit at the edge.** The application limiter is the only one; a
   proxy-level limiter is an operational addition.
6. **No third-party penetration test.** Nothing in this report substitutes for one.

## 5. Verdict

The application-level security boundary **behaves as specified under adversarial
input** for every control listed, with the six residual risks above recorded. This is
*implementation verified*, not *security certified*.
