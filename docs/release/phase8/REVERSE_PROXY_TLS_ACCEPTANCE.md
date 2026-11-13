# Reverse Proxy & TLS Acceptance — Phase 8C

**Status:** EXECUTED against a real TLS edge and a real uvicorn backend
**Harness:** `docs/release/phase8/evidence/tls_proxy_harness.py`
**Log:** `docs/release/phase8/evidence/logs/tls_harness.log`
**Topology (all on localhost):** self-signed CA → TLS-terminating proxy `:8443`
→ HTTP→HTTPS redirect `:8080` → uvicorn backend `:8099`

## 1. Why a harness rather than a staging server

No staging environment and no publicly trusted certificate exist for this project,
and obtaining either is an external action. The harness creates its own CA, its own
certificate, its own proxy and its own redirector, and drives a **real** backend
process. That proves the proxy contract without a public DNS name, while marking the
one thing it genuinely cannot prove — public trust — as NOT EXECUTED.

## 2. Results

| # | Check | Result | Detail |
| --- | --- | --- | --- |
| 1 | HTTPS request via reverse proxy | **PASS** | `200`, `{"status":"ok",…}` |
| 2 | HSTS present on the TLS edge | **PASS** | `max-age=31536000; includeSubDomains` — emitted by the **harness edge**, not the application |
| 3 | Self-signed cert rejected by default trust store | **PASS** | `SSLCertVerificationError: unable to get local issuer certificate` |
| 4 | Certificate valid against the **public** trust store | **NOT EXECUTED** | requires a publicly trusted certificate; a self-signed CA cannot prove it |
| 5 | Plaintext to the TLS port | **PASS** | `ConnectionResetError` |
| 6 | HTTP → HTTPS redirect | **PASS** | `301 → https://localhost:8443/healthz` (edge behaviour; the app does not redirect) |
| 7 | Host header injection through the proxy | **PASS** | `400` |
| 8 | Allow-listed Host accepted | **PASS** | `200` |
| 9 | Token issuance over HTTPS | **PASS** | `200`, prefix `dzsess_…` |
| 10 | Authenticated call over HTTPS | **PASS** | `200` on `/api/v1/auth/session` |
| 11 | `X-Forwarded-For` rotation bypasses the limiter | **PASS** | refused: `[401, 401, 401, 429, 429, 429]` |
| 12 | `X-Forwarded-Proto` whitelists a bad Host | **PASS** | `400` — spoofed header does not help a direct attacker |
| 13 | Direct peer reaches the backend | **OBSERVED** | `200` — the backend port is reachable if exposed; **the deployment must not publish it** |

Summary: **11 PASS, 1 NOT EXECUTED, 1 OBSERVED.**

Configuration used, exactly as an operator would set it:

```
ERP_ALLOWED_HOSTS=localhost,127.0.0.1
ERP_TRUSTED_PROXIES=127.0.0.1
```

## 3. Interpretation of the two non-PASS rows

**Public-CA validation (NOT EXECUTED).** The application never validates the
certificate — the client does. A browser or `requests`/`httpx` client with the public
store will accept a publicly issued certificate for the real hostname by
construction, but *this environment cannot demonstrate that*. It is the one external
action left: deploy behind a certificate issued for the real hostname and re-run
check 4. Instructions are in §5.

**Direct backend reach (OBSERVED).** The backend answers plain HTTP on `:8099`. When
the proxy is the only listener bound publicly and the backend binds `127.0.0.1`, this
is unreachable from outside. The harness deliberately binds the backend on loopback
and reaches it from loopback, which is why the check succeeded. **This is a
deployment invariant, not an application defect** — but it stays on the acceptance
list because a misconfigured deployment that publishes `:8099` silently loses TLS.

## 4. What the application does and does not do

| Concern | Owner | Evidence |
| --- | --- | --- |
| TLS termination | reverse proxy | checks 1, 5, 6 |
| HSTS | **edge** (not the app) | check 2 — the app emits no HSTS header by design |
| Host validation | **app** (`ERP_ALLOWED_HOSTS`) | checks 7, 8 |
| Client-identity trust | **app** (`ERP_TRUSTED_PROXIES`) | checks 11, 12 |
| Session/auth over TLS | **app** | checks 9, 10 |

## 5. Remaining external action (public-CA validation)

1. Issue/obtain a certificate for the real hostname (ACME or CA).
2. Terminate TLS at the proxy; keep `ERP_ALLOWED_HOSTS=<real hostname>` and
   `ERP_TRUSTED_PROXIES=<proxy IP>`; bind the backend to `127.0.0.1` only.
3. `curl --fail https://<real host>/healthz` from a machine with the default store —
   success here is the missing evidence.
4. Re-run `python docs/release/phase8/evidence/tls_proxy_harness.py` with
   `ERP_HARNESS_CERT=<path>` to keep checks 1–13 in the record alongside it.

Until step 3 is executed, this report does **not** claim public-TLS acceptance.
