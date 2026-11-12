# PHASE 8 — Final V1 Acceptance Report

**Baseline:** PR #7 head `f603e38` · CPython 3.12.10 · hash-pinned lock
**Executed:** 2026-11-09 · **Verdict at Phase 8: NOT PRODUCTION READY**

This report is deliberately harsher than a "green suite" summary. It states what was
executed, what was not, and therefore what may be claimed.

## 1. Deliverables produced

| Document | Status |
| --- | --- |
| `phase8/GIT_RECONCILIATION_REPORT.md` | complete |
| `phase8/SECURITY_CLOSURE_REPORT.md` | complete — 248 tests, controls C1–C9 |
| `phase8/REVERSE_PROXY_TLS_ACCEPTANCE.md` | complete — 11 PASS, 1 NOT EXECUTED, 1 OBSERVED |
| `phase8/WINDOWS_DESKTOP_ACCEPTANCE.md` | complete — **NOT EXECUTED** |
| `phase8/EXPERT_APPROVAL_STATUS.md` | complete — 18 rules + 12 questions, all **PENDING** |
| `phase8/OPERATIONAL_READINESS_REPORT.md` | complete |
| `phase8/evidence/` | harness, logs |

`docs/release/phase7_release_evidence/` — **absent from both the PR tree and the local
branch**; there is no Phase 7 evidence set to inspect. Recorded, not invented.

## 2. What was executed and what it shows

| Requirement | Evidence | Result |
| --- | --- | --- |
| Rate limiting is real | phase35/39/43/57 + phase61 (74 + 20 tests) | 429 on the 3rd failure with `Retry-After`, per client+account |
| Request-size limit enforced | phase61 C4 | `413`, nothing persisted, normal request still `200` |
| Unspoofable client identity | phase61 C5, harness check 11 | XFF rotation does not reset the limiter |
| Explicit trusted proxies, empty = fail closed | phase61 C6/C7 | verified |
| No secrets in logs | phase61 C8 | verified |
| AuthN enforced | phase61 C1 | `401` for absent/forged tokens |
| Tenant isolation | phase61 C2 + phase32/33/59 | `403`, audited |
| TLS / proxy / Host / XFF / XFP / HSTS | harness, 13 checks | 11 PASS, public-CA **NOT EXECUTED** |
| Desktop acceptance | — | **NOT EXECUTED** (no `libGL.so.1`, no Windows) |
| Expert-comptable approval | — | **PENDING** (18 rules, 12 questions) |

Baseline regression: **1373 passed, 6 skipped, 0 failed** (245.05 s) before the new
security module; the Phase 8 security module adds 20 more passing tests.

## 3. The honest verdict

**NOT PRODUCTION READY at Phase 8.** Reasons, in order of weight:

1. **No expert-comptable approval.** For Algerian accounting software this is not a
   formality; the tax and chart rules are unsigned, so nothing may be called compliant.
2. **No public-CA TLS validation** in any real deployment.
3. **Desktop client unverified.**
4. **No operational backing** (scheduled encrypted backups, retention, readiness probe,
   log rotation).
5. **No retention policy for accounting records** (the compliance checklist records this
   as an open item).

The technical layer is in good shape — rate limiting, size limits, host validation,
trusted-proxy handling, tenant isolation, ledger balance and immutability are all real
and executed. That is exactly the point: **the code being solid is not the same thing
as the system being ready**, and Phase 8's job is to say so.

## 4. What Phase 9 must add

See `docs/release/phase9/` — accounting invariants under concurrency, Algerian
compliance separated into implementation vs law vs approval, OCR/AI trust boundaries,
database and migration integrity, measured DR, measured performance, deployment assets,
operational runbook, and the ten E2E scenarios with persisted-state checks.

## 5. Reproduction

```bash
git clone https://github.com/Devmraustro/ERP && git checkout f603e38
python3.12 -m venv .venv && . .venv/bin/activate
pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
python -m pytest -q                 # full suite
python docs/release/phase8/evidence/tls_proxy_harness.py   # TLS/proxy checks
```
