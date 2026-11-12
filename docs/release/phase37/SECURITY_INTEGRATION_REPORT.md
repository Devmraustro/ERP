# Phase 37 — SEC-13 / SEC-14 / SEC-15 security integration report

## Summary

| Control | Status | Evidence |
| --- | --- | --- |
| **SEC-15** request-id boundary | **INTEGRATED** | 33 tests passing |
| **SEC-13** rate limiting | **INTEGRATED** | 18 + 8 tests passing |
| **SEC-14** review policy (evaluator) | **INTEGRATED** — evaluator only | verified by direct execution |
| **SEC-14** review policy (write path) | **NOT INTEGRATED — BLOCKED** | needs 9 functions + a schema table |

Full suite: **22 failed / 922 passed / 3 skipped**, failure set byte-identical to baseline.
**Zero regressions.** Baseline was 22 failed / 839 passed; +83 net passing tests.

## SEC-15 — request-id boundary

`dz_erp/request_id.py` (78 lines) ported verbatim; self-contained (only `re`, `uuid`).

A client may propose `X-Request-Id`, but the value is written into a JSON log line and echoed in a
response header, so only a bounded printable ASCII token is adopted. Executed:

```
uuid4           -> VALID          empty        -> REJECTED
req-123         -> VALID          newline      -> REJECTED
too long (>128) -> REJECTED       RTL override -> REJECTED
                                  space        -> REJECTED
                                  unicode      -> REJECTED
```

`sanitize_request_id` always returns `(id, accepted)`: a rejected proposal is replaced by a
server-side uuid4 and **never echoed**. Verified:

```
sanitize(None)          -> ('16a8ec19-…', False)   # generated
sanitize('req-123')     -> ('req-123',     True)   # adopted
sanitize('evil\ndef')   -> ('bba554f3-…', False)   # regenerated, original discarded
```

Wired as `_request_id_and_throttle` middleware in `dz_erp/api.py`: every response — including the
429 path — carries `X-Request-Id`, and the adopted id is what appears in the log line.

## SEC-13 — rate limiting

`dz_erp/rate_limit.py` (816 lines) ported verbatim; self-contained (stdlib only).

Executed, `limit=3`, `window=60s`:

```
attempt 1: allowed=True   retry_after=0s
attempt 2: allowed=True   retry_after=0s
attempt 3: allowed=True   retry_after=0s
attempt 4: allowed=False  retry_after=60s
attempt 5: allowed=False  retry_after=60s
different key unaffected: True
read-only probe (no event): True
after reset, allowed again: True
```

Note for reviewers: `hit()` returns **`(allowed, retry_after)`**, while `blocked()` returns
**`(blocked, retry_after)`**. The names are asymmetric by design — `hit()` records an event and
reports whether it was allowed, `blocked()` is a read-only probe. Misreading either inverts the
control, so both call sites were checked against the docstrings.

### Wiring applied to `dz_erp/api.py`

1. `RequestThrottle`, `describe_throttle_policy`, `format_rate_limit_scope`, `store_from_env`
   imported; `throttle: Optional[RequestThrottle]` added to `create_erp_fastapi_app`.
2. `app.state.throttle` defaults to `RequestThrottle(store=store_from_env(db))`.
3. Per-request throttling in middleware, before routing, so a flood costs one dictionary lookup
   rather than a reachable handler.
4. **Login throttling**, keyed on `(client_ip, username)` so one locked-out account cannot deny
   service to everyone behind the same address. Order is deliberate:
   `login_blocked` → verify password → `record_login_failure` / `record_login_success`, so a
   throttled attempt is recorded but cannot consume another slot, and a legitimate user who
   mistyped is unlocked on success.
5. `GET /api/v1/system/rate-limit-policy` — admin-only, returns the active policy and its honest
   scope. No key, username, credential or per-account counter is exposed.

## SEC-14 — review policy

`dz_erp/review_policy.py` (705 lines) ported verbatim; depends only on `decimal_utils`.

### Evaluator behaviour, executed

`REVIEW_POLICY` requires 8 fields: `invoice_number`, `supplier_name`, `supplier_nif`,
`invoice_date`, `currency_code`, `amount_ht_dzd`, `amount_tva_dzd`, `amount_ttc_dzd`.

| Scenario | `satisfied` | Blocking |
| --- | --- | --- |
| all 8 `CONFIRMED` with matching `reviewed_value` | **True** | none |
| no decisions at all | **False** | 8 × `REQUIRED_FIELD_NOT_DECIDED:*` |
| one required field `FLAGGED` | **False** | `FIELD_FLAGGED:invoice_number` |
| extracted value edited **after** confirmation | **False** | `FIELD_FLAGGED:invoice_number` (`decision_stale`) |

The last row is the important one: a `CONFIRMED` decision is bound to the value it was made on
(`reviewed_value` must equal the extracted value), so a later edit **visibly stales** the decision
instead of silently inheriting its authority. The policy is fail-closed on every axis.

### Why the write path is NOT integrated

`decide_extracted_field_review` and its 8 helpers (`_apply_field_decision`,
`_decision_current_value`, `_canonical_line_{price,qty,discount}`,
`_rewrite_line_{quantity,unit_price,discount}`) do not exist in the workspace's `intake_ocr.py`
(35 functions; PR #7 has 43). Integrating them needs:

1. the `ocr_field_reviews` table plus indexes (schema migration), and
2. the `ocr_extracted_fields` review-decision linkage,

which together change **what evidence an approval is bound to**. That is a data-model change, not a
code port, and it is the same table whose absence forced `_source_review_fingerprint` to be rejected
in Phase 36.

Integrating the evaluator alone is safe and useful: it is a pure function over rows the caller
supplies, so it can be used by Phase 38 validation without altering the schema. **The write path
stays BLOCKED** and is reported as such rather than half-wired.

`tests/test_phase29_ocr_review_policy.py` (2261 lines) is therefore **excluded from the run** — it
cannot even import without those functions. It is dropped from the suite rather than deleted, and its
absence is counted so it cannot be mistaken for passing coverage.

## Two real bugs the tests caught

Both were introduced by my own wiring and found only because the ported tests were run:

| Bug | Symptom | Fix |
| --- | --- | --- |
| `JSONResponse` used but never imported | `NameError` on the 429 path — the throttle would have crashed exactly when it was needed | `from fastapi.responses import JSONResponse` |
| Access log written into the security sink | Phase 9 invariant "the durable security file holds only rejection evidence" broken | aligned with PR #7: access records are durable, and Phase 9 asserts the security subset by event, not by count |

The second is worth recording as a design decision rather than a bug fix: a *durable access log* and a
*security-only evidence file* are different requirements, and PR #7 resolved the conflict by keeping
one durable stream and filtering by event. The same resolution is adopted here, and the Phase 9 test
now proves both properties at once — each rejection durably recorded with its own correlation id,
**and** the accompanying request records carrying no body, header or credential.

## Not weakened

- `require_role_secure` is unchanged; runtime proof (clerk allowed, warehouse denied, admin allowed)
  is in `phase36/INTEGRATION_MATRIX.md`.
- The `nosniff` / `no-referrer` / `X-Frame-Options: DENY` / `no-store` headers and the 413
  body-size guard are untouched.
- `readyz` is preserved. PR #7's `api.py` has **no `/readyz`**, while `ops/dz-erp-readiness.service`
  documents that it depends on it — a wholesale swap of `api.py` would have silently broken
  deployment readiness. This is why `api.py` was patched surgically instead.
- No test was deleted. The one pinned count that legitimately changed (26 → 27 protected operations,
  because a new protected endpoint exists) was updated with a comment explaining why.
