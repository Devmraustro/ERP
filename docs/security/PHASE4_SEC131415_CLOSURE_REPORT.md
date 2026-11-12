# Phase 4 — Closure of SEC-13, SEC-14 and SEC-15

**Date:** 2026-11-09
**Scope:** implement and verify the three findings that the selective-integration phase reviewed but deliberately left unimplemented, **without disturbing the preserved security package**, and with **AWS KMS and MinIO/S3 confirmed out of V1 scope**.

| Finding | Phase-3 decision | Phase-4 result |
|---|---|---|
| **SEC-13** password policy | reviewed, not implemented | **CLOSED** — one policy chokepoint for every credential path, lifecycle endpoints, rehash-on-login |
| **SEC-14** OCR override validation | reviewed, not implemented | **CLOSED** — the legacy write path now validates against the field's value class |
| **SEC-15** TLS / Host / proxy | reviewed, not implemented | **CLOSED (in-process part)** — Host validation is enforced, uvicorn's proxy-header layer is disabled, deployment docs rewritten; TLS termination remains the proxy's job by design |
| AWS KMS / HSM | not implemented in this build | **OUT OF V1** — documented, refused at start-up, not attempted |
| MinIO / S3 backup storage | needs out-of-lock `boto3` | **OUT OF V1** — documented, not attempted |

**Status line for the release:** `SEC-13/14/15 CLOSED — INTEGRATION STILL UNCOMMITTED, ENVIRONMENT BLOCKERS UNCHANGED (B1, B3, B4 remain)`.

This is not a production-readiness statement, and it does not change the Phase-3 verdict on the *release*: the integrated tree is still uncommitted, still unreviewed by a human, and still unable to run its restore matrices on a supported interpreter.

---

## 1. Method — how the preserved work was protected

* **Nothing in the preserved package was touched.** `/home/user/integration-preservation/` verifies clean before and after (`sha256sum -c SHA256SUMS` → 5/5 `OK`), including `security-selective-integration.patch` (`2dfd7769…`) and its untracked test module (`527bcc99…`).
* The closure was built in a **separate disposable workspace** (`/tmp/sec1315`): fresh clone of the authoritative remote at `5a3e47ddfba2b789bcff71d6438db4927f5c7986`, then the preserved patch applied, then the SEC-13/14/15 work layered on top. The original integration workspace `/tmp/pr7work` was never opened for writing.
* Packaging work from Phase 3 stays **separate**: no `pyproject.toml` change is part of this patch.
* No commit, no push, no merge, no history rewrite; PR #7 remains open, draft and unmerged.

Two artefact routes, both reproduced end-to-end (§3):

| Route | Patch | Applies to |
|---|---|---|
| **Layered (preferred)** | `phase4-sec13-14-15-delta.patch` | `5a3e47dd` + `security-selective-integration.patch` |
| **One-shot** | `full-security-state-from-5a3e47dd.patch` | a clean `5a3e47dd` (supersedes the preserved patch; contains it) |

Artefacts live in `/home/user/integration-preservation/sec13-14-15/` and a copy travels with this report in `docs/security/phase4_closure/` (`SCOPE.md` maps each file to its finding).

---

## 2. What was implemented

### 2.1 SEC-13 — password policy and credential lifecycle

**Design:** one validator, one place, every path. `dz_erp.auth.validate_password_policy` is now called by `AuthManager.create_user` (which the admin API, `create_user_admin_secure` and the CLI `--bootstrap-admin` all funnel through) and by `AuthManager.set_password` (self-service change and administrative reset). The old route-local `len(password) < 8` check in `POST /api/v1/users` is gone; the route now surfaces the validator's stable code.

| Control | Implementation | Rationale |
|---|---|---|
| Minimum length | `MIN_PASSWORD_LENGTH = 8`, configurable up to 64 via `ERP_PASSWORD_MIN_LENGTH`, **never below 8** | 8 is the existing documented contract; 12 is recommended in the guide and enforced where the operator configures it. Raising the floor retroactively would lock existing users out. |
| Maximum length | 1024 characters | bounds PBKDF2 work per request (DoS ceiling) |
| Composition rules | **none** | NIST SP 800-63B favours length; class rules push users to predictable substitutions |
| Identity collision | rejects a password equal (case-insensitively) to the username or company name | first-guess territory |
| Weak values | small built-in list (`password`, `12345678`, `motdepasse`, …) | offline floor, no external dependency, no user data |
| Whitespace-only | rejected | a "password" of spaces is not a secret |
| Hashing | unchanged: PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte salt | no migration needed for existing credentials |
| **Rehash-on-login** | a stored hash below the current iteration count is re-hashed on the first successful login, audited `AUTH_PASSWORD_REHASHED` | moving the iteration floor later stops being a flag day |
| **Self-service change** | `POST /api/v1/auth/password` → `AuthManager.change_own_password` | requires the *current* password, so a stolen token cannot take over the credential; revokes **all** tokens including the caller's |
| **Administrative reset** | `POST /api/v1/users/{id}/password-reset` → `security_audit.reset_password_admin_secure` | admin role + same company; cross-tenant and unknown id are conflated (404); revokes the target's tokens; audit attributes the action to the acting admin with `mode: administrative_reset` |

Refused self-service attempts are audited (`AUTH_PASSWORD_CHANGE_REJECTED`) — the record is written in the same transaction and raised **after** it commits, so the evidence is not rolled back with the write it refused.

An honest correction to the Phase-3 review: it stated there was "no service function" for password change. `AuthManager.set_password` did exist — it rotated the credential and revoked tokens — but it had **no endpoint, no current-password verification and no policy**. The gap was the lifecycle and the enforcement, not the primitive; this phase reuses that primitive rather than adding a second one.

### 2.2 SEC-14 — OCR override validation

`dz_erp.intake_ocr.update_extracted_field_by_human` (the legacy `PATCH /api/v1/documents/{id}/fields` path) now validates the value **before** the transaction that writes it, using the exact validator the decision path uses (`review_policy.validate_corrected_value` with `rule_for(field_name)`, falling back to `TEXT_FALLBACK_RULE` for ungoverned fields).

* A rejected value writes **nothing**: `human_override_value` and `normalized_value` are untouched, and the refusal is audited as `SECURITY_FIELD_EDIT_REJECTED` (field, value class, reason truncated to 200 chars).
* A valid value is stored in the **canonical form** the policy defines (`7500.5` → `7500.50`), so the two write paths can no longer disagree about what a "correct" value looks like.
* Bounds now in force on this path: amounts ≥ 0 and < 10¹⁸ (SEC-02 magnitude guard), discount ≤ 100 with no clamping, quantity > 0, real ISO dates, DZD only, NIF/NIS/AI/RC patterns, bounded free text (≤ 256 chars, no newline).
* The API maps the refusal to **422** (`FIELD_VALUE_REJECTED:…`), which is what the decision path already returned.

### 2.3 SEC-15 — Host validation and one proxy-trust layer

| Change | Where | Why |
|---|---|---|
| `Host` header validation with an allow-list (`ERP_ALLOWED_HOSTS`), loopback/test defaults, `*.example.dz` patterns, port and IPv6 handling, missing header refused | `dz_erp/net.py` + middleware in `dz_erp/api.py` | `Host` is caller-controlled; it is the ingredient of cache poisoning, absolute-URL confusion and (once a mailer exists) reset-link poisoning. The proxy should reject unknown names; this is the backstop that does not depend on proxy configuration |
| 400 + `X-Request-Id` on refusal, logged as `SECURITY_HOST_HEADER_REJECTED` (offending value bounded, never used for routing or attribution) | `api.py`, `security_audit.JsonlSecurityLog` | refusals must be visible in the incident trail |
| **`proxy_headers=False`** passed to `uvicorn.run` | `dz_erp/serve.py` | uvicorn's default (`proxy_headers=True`, `forwarded_allow_ips` defaulting to loopback) rewrites `request.client` from `X-Forwarded-For` *before* `net.TrustedProxyPolicy` sees it — a second, undocumented trust layer that silently defeats the fail-closed policy |
| Host policy readable from the running process (`host_policy` in `GET /api/v1/system/rate-limit-policy`) | `api.py` | an operator must be able to verify what the process enforces, not what the file says |
| Deployment documentation: TLS/Host/proxy rules, `FORWARDED_ALLOW_IPS` warning, required proxy headers, "no cookies, so no CSRF surface" | `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` §6.1, `ops/erp.env.example` | the control is only real if the deployment matches it |

`ERP_TRUSTED_PROXIES` behaviour is unchanged: still fail-closed, still right-to-left, still no trust by default. What changed is that nothing *else* can trust the header behind its back.

### 2.4 V1 scope — AWS KMS and MinIO/S3 are excluded (by decision, in writing)

* `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` §6.3 states both capabilities as **not supported in V1**, with the reason for each, and names the supported configuration (local key custody + local backup storage).
* `ops/erp.env.example` carries the same statement where an operator reads configuration.
* No code enables or implies either capability: `KmsKeyProvider` still refuses at start-up, and no `boto3` dependency was added to the lock.
* Consequence recorded for the release notes: anyone who enables `ERP_KEY_PROVIDER=kms|hsm` or configures S3 backups is running an untested configuration, not a supported one.

---

## 3. Verification

### 3.1 New acceptance suite

`tests/test_phase59_sec13_14_15_closure.py` — **45 tests**, covering the rule rather than the wording:

```
45 passed in 5.32s
```

It asserts, among others: the policy matrix (floor, overlong, whitespace-only, identity collision, weak list, env override clamping); that `create_user` **at service level** refuses a short password; that the CLI bootstrap refuses one **and leaves no half-bootstrapped company**; self-service change requires the current password, revokes every token, audits the refusal and the rotation; administrative reset is role- and tenant-scoped, revokes the target's tokens and attributes the audit to the admin; rehash-on-login upgrades a 1 000-iteration hash to 600 000 **once** and is audited; the override path refuses seven invalid values with nothing written and an audit record, normalises a valid amount, and keeps the bounded text fallback; Host matching is exact/case-insensitive/wildcarded/port-aware and a missing header is refused; an unknown Host yields 400 with a request id and a security-log event; an explicit allow-list replaces the defaults; the wildcard does not match its bare domain; `proxy_headers=False` is present; the new env vars are documented.

### 3.2 Full suite — no new failures

| Run | Result |
|---|---|
| Baseline (integrated tree, before this phase) | 20 failed / **1295** passed / 10 skipped |
| After SEC-13/14/15 | 20 failed / **1340** passed / 10 skipped |
| Difference | +45 passed (exactly the new module) |
| Failure-set comparison (junit) | **new failures: []** · no-longer-failing: [] |

The 20 failures remain the interpreter-gated restore matrices (`test_phase5/6/8/9/11/12/…`) that fail identically on the pristine tree on CPython 3.11.2 (blocker **B1**, unchanged). No test was skipped to obtain this result.

### 3.3 Before/after probe (the gaps existed, and are gone)

`before-after-probe.py` runs on both trees unchanged:

```
=== pre-change tree (integrated, preserved patch only) ===
SEC-13 OLD-BEHAVIOUR: a 1-character password was accepted
SEC-14 OLD-BEHAVIOUR: stored '-1.00' as a human correction
SEC-15 OLD-BEHAVIOUR: Host: evil.example accepted (200)
SEC-15 OLD-BEHAVIOUR: uvicorn starts with its proxy_headers=True default

=== closure tree ===
SEC-13 FIXED: refused (PasswordPolicyError)
SEC-14 FIXED: refused (FinancialIntegrityError)
SEC-15 FIXED: Host refused (400)
SEC-15 FIXED: proxy_headers=False is explicit
```

### 3.4 Reproduction from the artefacts

| Route | Apply result | Tree comparison | Tests in the reconstruction |
|---|---|---|---|
| Layered (preserved patch → delta) | `git apply --check` clean, applied cleanly | **identical** to the working tree (all files, caches excluded) | 45 passed; probe reports FIXED |
| One-shot (cumulative patch on a clean `5a3e47dd`) | applied cleanly | **identical** | — |

### 3.5 Static checks

```
$ ruff check dz_erp tests
All checks passed!
$ python -m compileall -q dz_erp tests
(no output)
```

---

## 4. Test-file changes — disclosed in full

Three edits to existing test files were required. None weakens an assertion, none skips anything, and each is justified by the behaviour change it accompanies.

| File | Change | Why it is not "changing tests to get green" |
|---|---|---|
| `tests/test_phase7_authentication_remediation.py` | `"P#2026x"` → `"Prov#2026x"` (2 occurrences) | The literal is **7 characters** — a password the newly centralised policy refuses. These tests are about idempotent migration and role validation, not about the length floor; their assertions are untouched. The gap this exposes is real: before this phase, `AuthManager.create_user` accepted a 7-character password while the API required 8. |
| `tests/test_phase8_upload_boundary_and_ci.py` | protected-operation pin `33` → `35`, with a comment naming the two new endpoints | The pin exists to catch *accidental* surface changes. This change is deliberate: two authenticated endpoints were added. The public-operation list (the dangerous direction) is unchanged and still asserted exactly. |
| `tests/test_phase8_upload_boundary_and_ci.py` | (no other change) | `test_create_rejects_short_password` in phase 28 keeps asserting a 422 containing "8"; the policy error keeps a human-readable `detail` string and carries its machine code in `X-Error-Code` precisely so the existing client contract did not have to change. |

---

## 5. Behaviour changes a client or operator must know

| Change | Impact | Action |
|---|---|---|
| `POST /api/v1/auth/password` (new) | self-service rotation; revokes **all** tokens, response `reauthenticate: true` | desktop/client must re-login after a change |
| `POST /api/v1/users/{id}/password-reset` (new) | admin-only; 404 for foreign/unknown ids | admin tooling |
| `POST /api/v1/users` with a weak password | still 422; `detail` remains a string; stable code in `X-Error-Code` | clients may branch on the header |
| `PATCH /documents/{id}/fields` with an invalid value | now **422** (`FIELD_VALUE_REJECTED`) instead of silently storing it | clients previously relying on storing arbitrary text in a typed field must use a valid value (or the text fallback for ungoverned fields) |
| Any request with an unknown `Host` | now **400** | production must set `ERP_ALLOWED_HOSTS`; the proxy must forward the public name |
| New env vars | `ERP_ALLOWED_HOSTS`, `ERP_PASSWORD_MIN_LENGTH` | documented in `ops/erp.env.example`; the drift gate (`test_phase37`) passes |
| Diagnostic payload | `GET /api/v1/system/rate-limit-policy` now also returns `host_policy` | additive; existing keys unchanged |

---

## 6. What is still open (unchanged or newly recorded)

| ID | Blocker | Status after this phase |
|---|---|---|
| **B1** | No supported interpreter (≥ 3.11.17 / ≥ 3.12.10) in the build environment | **OPEN** — 20 restore tests still fail/skip for that reason; the new host/password tests do not depend on it |
| **B2** | Wheel flat-layout failure | **FIXED IN ISOLATION** (Phase 3 patch, still separate, not part of this change) |
| **B3** | Integration + this closure are uncommitted | **OPEN** — delivered as patches; human review and commit happen outside this session |
| **B4** | Windows / desktop UI / Docker / MinIO / KMS unvalidated | **OPEN, narrower** — KMS and MinIO are now out of V1 by decision (§2.4) rather than pending validation |
| **B5** | SEC-13/14/15 decisions | **CLOSED** (this phase) |
| **B6** | "10 user modifications" unidentified | **OPEN** — repository owner action |
| **B7** | Upload service has no in-process role gate | **ACCEPTED RESIDUAL** (unchanged) |
| **B8** | Chunked-body cap is proxy responsibility | **DOCUMENTED** (guide §6.1 item 4) |
| **B9** | S3 backup needs out-of-lock `boto3` | **CLOSED BY SCOPE** — S3 is out of V1 |
| **B10 (new)** | No self-service password *recovery* (email reset) | **BY DESIGN for V1** — an administrator resets; a mailer-based flow needs its own threat model |

Residual risk this phase does **not** remove: no MFA, no breach-corpus check, no per-account lockout tuning beyond 5/15 min, an operator who forgets `ERP_ALLOWED_HOSTS` gets a hard 400 for every non-loopback request (deliberate fail-closed, documented), and TLS quality is still whatever the proxy provides.

---

## 7. Artefacts and checksums

Stored in `/home/user/integration-preservation/sec13-14-15/` and copied to `docs/security/phase4_closure/`:

| Artefact | Bytes | SHA-256 |
|---|---|---|
| `phase4-sec13-14-15-delta.patch` (layered route) | 42 547 | `8f50b6c000cdec0e0f1f8969bed24abcb69c533a047a6f80e73f2c4e0258968b` |
| `full-security-state-from-5a3e47dd.patch` (one-shot) | 71 285 | `a5d04b8e1165d1b6a76c8bb11cd16f2532e23f65517f7cd4078fceaaf6f05a57` |
| `sec13-password-policy-core.patch` (review split) | 21 662 | `bd633650b37a24c5e29f1a2a002976ce5d93d235811035fc8421867e8bed3507` |
| `sec14-ocr-override-validation.patch` (standalone) | 1 974 | `84d78ea1d3d47e535fc05c11826a9b436320b3997848863c7bf3b903ead7c863` |
| `sec15-host-and-proxy-core.patch` (review split) | 5 750 | `41e4520cd132c72362a90020b999210d86b6ad7e3ab1389c1ee359c2030b8080` |
| `untracked-files/tests/test_phase59_sec13_14_15_closure.py` | — | `715874623e641224cbcc02034f8f1658a5625e7280940943066a82cc740310ec` |
| `before-after-probe.py` | 2 847 | `32f590725f1283ba12a6053d1a4ffbfdd8aaa740f9a1cf1fe7e071f314eb2a1b` |

Files touched by the closure (10 tracked + 1 new):

```
docs/PRODUCTION_DEPLOYMENT_GUIDE.md          §6.1 / §6.2 / §6.3
dz_erp/api.py                                2 endpoints, policy on create, Host middleware, host_policy
dz_erp/auth.py                               policy, lifecycle, rehash-on-login
dz_erp/intake_ocr.py                         override validation
dz_erp/net.py                                Host allow-list primitives
dz_erp/security_audit.py                     reset_password_admin_secure
dz_erp/serve.py                              proxy_headers=False
ops/erp.env.example                          ERP_ALLOWED_HOSTS, ERP_PASSWORD_MIN_LENGTH, V1 scope
tests/test_phase7_authentication_remediation.py   fixture compliance (§4)
tests/test_phase8_upload_boundary_and_ci.py       contract pin 33 -> 35 (§4)
tests/test_phase59_sec13_14_15_closure.py    NEW — 45 tests
```

Preserved package (untouched, re-verified before and after):

```
security-selective-integration.patch            2dfd77697ba3e726cf2ddf4f9a34766cdf81a093037f581ab71948f177c16b7e
untracked-files/tests/test_phase58_…py          527bcc991f9f088adaa5766a822fc7306cca625f5227863c5ade3d5d05492837
packaging-flat-layout.patch                     a73eacc3d1a9b8d2a56afbe05ed7361ea3843668251fb5e1c87f7af5d4141c4d
```

### Git state (verified, unchanged)

```
/home/user/ERP      HEAD 4fc1364a9504fab1153ea44e753f354f00023428  branch arena/7557bcfb-erp  0 commits ahead of main
/tmp/pr7work        HEAD 5a3e47ddfba2b789bcff71d6438db4927f5c7986  detached, preserved patch only, no commits
/tmp/sec1315        HEAD 5a3e47dd…  preserved patch + SEC-13/14/15 closure, no commits
/tmp/verify1315     HEAD 5a3e47dd…  reconstructed from delta patch (identical, 45 tests pass)
/tmp/verify_full    HEAD 5a3e47dd…  reconstructed from cumulative patch (identical)
PR #7               open, draft, unmerged — head 5a3e47ddfba2b789bcff71d6438db4927f5c7986
```

No commit, no push, no merge, no history rewrite, no destructive Git command, no workspace deleted, no virtual environment modified, no database or Docker volume touched.

---

## 8. Final status

**`SEC-13/14/15 CLOSED AND VERIFIED — RELEASE BLOCKERS REMAIN (B1, B3, B4).`**

* The three findings are implemented, tested (45 new acceptance tests), and proven to close four concrete behaviours that were demonstrably present on the pre-change tree.
* The full suite shows **no new failures** and the same 20 interpreter-gated failures as before; nothing was skipped or weakened to achieve that.
* The preserved integration is untouched and still verifies byte-for-byte; the closure is available both as a layer on top of it and as a single cumulative patch, and both routes were reproduced to an identical tree.
* AWS KMS and MinIO/S3 are **out of V1 by decision**, stated in the deployment guide and the env example, with no code or dependency change that would imply otherwise.
* The integration is still uncommitted and unreviewed, and the environment still cannot run the restore matrices — so this is not a production-readiness claim.
