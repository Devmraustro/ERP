# Rate limiting: semantics, bounds and deployment scope

What the throttle in `dz_erp/rate_limit.py` actually guarantees, where those
guarantees stop, and how each one is verified. Evidence: the independent audit in
`docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md` §6 (finding **F-01**), the fix and
its tests in `tests/test_phase39_bounded_rate_limiting.py` (32 tests), and the
pre-existing policy tests in `tests/test_phase35_api_rate_limiting_and_request_logging.py`
(16 tests).

---

## 1. The defect this closes

The Phase 35 limiter kept one bucket per `(client, account)` and **never removed
one**. Because the key contains the username — a value the caller chooses — an
unauthenticated client could create state at will. The audit measured:

| Experiment | Before (Phase 35) | After (Phase 39) |
|---|---|---|
| 5 000 distinct usernames, one failure each | 5 000 buckets retained | ≤ `ERP_AUTH_FAILURE_MAX_KEYS` (default 4 096); measured 64 with a cap of 64 |
| … after the window expired | still 5 000 | dropped by the lazy sweep |
| 50 unauthenticated requests with random usernames | 50 buckets | bounded as above |
| `blocked()` on an unseen key | **allocated a bucket** | allocates nothing |

The read-only allocation was the same defect with a bigger amplifier: a probe is
cheaper than a login, and every probe used to create a permanent entry.

## 2. Expiry semantics

* A bucket holds the timestamps of events **inside** the window
  (`now - occurred_at <= window`). An event exactly one window old has left it.
* A bucket is empty when it holds no in-window events, and **an empty bucket is
  deleted**, not kept.
* Deletion happens through `sweep()`, which is also called lazily at most once per
  `min(1 s, window/2)` while the limiter is in use. Expiry therefore happens in a
  running process without a maintenance thread.
* `stats()` reports `expired_keys`, `evicted_keys`, `tracked_keys` and `sweeps` —
  counters only, never a key.

## 3. Bounds

* **Cap.** At most `ERP_AUTH_FAILURE_MAX_KEYS` buckets per limiter (default
  4 096). A new key first triggers a sweep; if the table is still full, the
  **least-recently-touched** bucket is evicted and `evicted_keys` is incremented.
* **Chosen trade-off.** Eviction can forget an attacker's own history, so memory
  cannot be used as a lever *and* the limit stays effective only for keys that
  survive. That is why the second dimension exists (below): the per-client
  aggregate has cardinality bounded by the number of clients, not by attacker
  input.
* **Shared backend.** `SqliteRateLimitStore` additionally caps its table at
  200 000 rows and deletes events older than the widest window, at most once per
  second, inside the same transaction as the insert.

## 4. The three rules

| Rule | Key | Default | Cleared by |
|---|---|---|---|
| Failed authentication per account | `login|client|username` | 10 per 300 s | a successful login for that account |
| Failed authentication per client (across all accounts) | `client|client` | 50 per 300 s (`0` disables) | nothing but the window |
| Overall request rate | `req|client` | 0 (disabled) per 60 s | the window |

The aggregate is **not** cleared by a successful login. Otherwise an attacker who
owns one valid account could reset the flood counter between credential-stuffing
runs. A refusal records the event anyway, so hammering cannot out-wait the window.

Throttling is audited: `SECURITY_AUTH_RATE_LIMITED` (endpoint),
`SECURITY_RATE_LIMITED` (middleware). Neither event contains a password, a token
or a request body.

## 5. Concurrency

Each limiter is guarded by one `threading.Lock`; `hit()` prunes, decides and
appends inside that lock, so two threads cannot both consume the last slot. The
shared store serialises through the database's `BEGIN IMMEDIATE` transaction,
which is the same boundary every other writer takes.

Verified: 8 threads × 500 hits keep the table under its cap and never duplicate an
event; 8 threads × 10 failures against a client limit of 20 record exactly 20
admissions and 60 refusals.

## 6. Restart behaviour

| Backend | After a process restart |
|---|---|
| `memory` (default) | **All counters are lost.** An attacker can out-wait a restart; a deploy therefore resets the throttle. This is stated, not hidden. |
| `sqlite` | Counters survive, because they live in the database. Restarting the API does not clear a lockout. |

## 7. Multi-worker and multi-instance

* `memory` is **per process**. With `uvicorn --workers N` or several replicas the
  effective ceiling is `N × limit`, and an attacker who spreads requests across
  workers gets N times the budget. `describe_throttle_policy()` says so in the
  `scope` field, and `format_rate_limit_scope()` returns
  `per-process (per worker); staging-only unless a shared backend is configured`.
* `ERP_RATE_LIMIT_BACKEND=sqlite` makes the counters **shared by every process
  that opens the same database file** — the usual multi-worker case on one host
  or one volume. Verified two ways: two applications over one database share a
  counter, and a **separate OS process** sees the parent's events and its own are
  seen back.
* Neither backend is **distributed**. Replicas with separate database files, or a
  database reached through a service, are out of scope. No Redis implementation
  is bundled: the dependency, the server, its authentication/TLS/failover
  requirements and its backup story cannot be verified in this repository, so
  claiming one would be an unevidenced control. The `RateLimitStore` protocol is
  the seam where such a backend would go, and any implementation must keep the
  same semantics (refuse past the limit, non-zero retry, no unbounded retention).

**Classification.** With the default backend the control is **staging-only** for
any deployment that is not a single process. With `sqlite` it is a real shared
bound for a single-database deployment, and still not a distributed one. A
proxy-side limit remains the recommendation for anything at the edge.

## 8. Backend failure policy

`ERP_RATE_LIMIT_FAILURE_MODE`:

* **`fail_closed` (default).** If the store raises — locked database, disk full,
  schema drift — the authentication attempt is **refused** with 429 and
  `Retry-After: 60`, and `RATE_LIMIT_BACKEND_UNAVAILABLE` is logged with
  `error_type` and `failure_mode`.
  *Why closed:* an unavailable counter must not silently become an unlimited
  guessing window. The cost is availability, and that cost is taken deliberately.
* **`fail_open`.** The attempt proceeds and the same event is logged, so the
  choice is visible in the log and in the policy endpoint rather than implicit.
  *When it is legitimate:* a deployment behind an independent edge limiter that
  prefers availability of the login endpoint over the local bound.

Neither mode hides the failure, and the error text is never written to the log —
only the exception type — so an internal path or query cannot leak through it.

## 9. What an operator can check

```bash
# policy, live, as an administrator (no keys, no usernames in the response)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/system/rate-limit-policy

# the whole control, both backends
python -m pytest tests/test_phase39_bounded_rate_limiting.py -q      # 32
python -m pytest tests/test_phase35_api_rate_limiting_and_request_logging.py -q  # 16
```

The policy endpoint reports `auth_failure_limit`, the aggregate limit, `bounded.*`,
`failure_mode`, the backend description and `deployment_scope`, so a review reads
what the running process does rather than what a document claims.

## 10. Residual limitations

1. Per-process accuracy under eviction: a bucket can be evicted while an attacker
   is still inside his window. The aggregate is the compensating bound; both
   belong to the same client, which the trusted-proxy policy fixes.
2. `sqlite` puts limiter rows in the ledger database. They are pruned and capped,
   but they are not ledger records and must not be backed up as if they were;
   losing them on restore only shortens a lockout.
3. No IPv6 prefix aggregation: an attacker with a /64 rotates addresses and gets
   a new aggregate per address. A proxy or edge limiter is the answer; this is not
   implemented here and is not claimed.
4. Multi-replica deployments sharing one *database service* (not one file) are
   outside the tested boundary.
