# Authentication Hardening & Impersonation Remediation Specification (Phase 8)

## 1. Root Cause (`CWE-287: Improper Authentication`, `CWE-639: Insecure Direct Object Reference`)

The former `_authenticate` dependency in `dz_erp/api.py` accepted two client-supplied
headers as proof of identity:

```python
def _authenticate(
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    x_company_id: Optional[int] = Header(default=None, alias="X-Company-Id"),
) -> Dict[str, int]:
    ...
```

No credential was required. The values were only checked for *internal consistency*
(the user must exist, be active, and belong to the claimed company). Because the
claim itself came from the request, **any unauthenticated caller could impersonate
any user of any company** by editing two headers.

The `users.api_token_hash` column existed in the schema but was dead — no code path
ever issued or verified a token, so there was no credential to check against.

### Proof of concept (pre-remediation, commit `1b63c6e`)

| Request | Result | Meaning |
| :--- | :--- | :--- |
| `GET /api/v1/invoices/1` with `X-User-Id: 1`, `X-Company-Id: 1`, no credential | `404` | **Authentication passed.** `404` was returned only because invoice 1 did not exist; the caller had been authenticated as user 1. |
| Same request after remediation | `401` | `Missing Authorization header. Expected 'Authorization: Bearer <token>'.` |

The `404` versus `401` distinction is the whole vulnerability: a `404` proves the
request cleared the authentication boundary.

---

## 2. Authentication Design (`dz_erp/auth.py`)

### 2.1 Opaque server-issued bearer tokens

| Property | Value |
| :--- | :--- |
| Transport | `Authorization: Bearer <token>` (RFC 6750), scheme matched case-insensitively per RFC 7235 |
| Token format | `dzsess_` + base64url(32 CSPRNG bytes) — **256 bits** of entropy |
| Storage | **SHA-256 digest only** (`auth_tokens.token_hash`, `CHECK (length = 64)`); plaintext is returned exactly once at issuance and is unrecoverable |
| Default lifetime | 8 hours (`DEFAULT_TOKEN_TTL_SECONDS`), configurable per issuance |
| Revocation | `revoked_at` / `revocation_reason` columns; immediate and durable |
| Comparison | `hmac.compare_digest` for both password and token-digest verification |

**Why opaque tokens rather than JWTs.** The requirements demand revocation. A JWT
remains valid until it expires even after sign-out, user deactivation, or credential
compromise, unless a server-side blocklist is added — which reintroduces the same
server-side state with extra moving parts. Database-backed opaque tokens provide
immediate revocation and genuine expiry as first-class behaviour, with no key
distribution or rotation problem.

### 2.2 Verification sequence (executed on **every** request)

1. Parse `Authorization`; reject a missing header (`401 MISSING_CREDENTIAL`), a
   non-`Bearer` scheme, or an empty/whitespace-bearing token (`401 MALFORMED_CREDENTIAL`).
2. SHA-256 the presented token and look up `auth_tokens` by digest.
3. Unknown digest → `401 INVALID_CREDENTIAL`.
4. `revoked_at IS NOT NULL` → `401 REVOKED_CREDENTIAL`.
5. `expires_at <= now` → `401 EXPIRED_CREDENTIAL`.
6. Owning user missing or `is_active != 1` → `401 INACTIVE_USER`.
7. Constant-time re-confirmation of the stored digest (`hmac.compare_digest`).
8. `company_id`, `branch_id`, `role`, `username` are then read from the **server-side
   `users` row**. They are never taken from the request.

Every rejection carries `WWW-Authenticate: Bearer realm="algerian-ai-erp"`.

### 2.3 Identity headers are ignored

`X-User-Id` and `X-Company-Id` are no longer declared as endpoint parameters, so
FastAPI discards them. A valid credential plus forged headers resolves to the
principal bound to the credential — the headers cannot pivot the session. The
OpenAPI contract declares only the `BearerAuth` security scheme.

### 2.4 Password storage

`pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>` with a 16-byte random salt and
**600,000 iterations** (OWASP 2023 floor for PBKDF2-HMAC-SHA256). Verification is
constant-time. A maximum password length of 1024 characters bounds PBKDF2 work per
request.

### 2.5 Brute-force protection

Five consecutive failed sign-ins for a user lock the account for 15 minutes
(`users.failed_login_count`, `users.locked_until`). While locked, even the correct
password is refused with `401 ACCOUNT_LOCKED`. An unknown username burns the same
PBKDF2 work against a dummy hash so response timing does not distinguish
"no such user" from "wrong password".

### 2.6 Revocation triggers

| Event | Effect |
| :--- | :--- |
| `POST /api/v1/auth/revoke` | Revokes the presented token |
| `AuthManager.deactivate_user()` | Sets `is_active = 0` **and** revokes every live token |
| `AuthManager.set_password()` | Rotates the hash **and** revokes every live token |
| `AuthManager.revoke_all_tokens_for_user()` | Bulk revocation (e.g. security incident) |
| `AuthManager.purge_expired_tokens()` | Housekeeping for already-expired rows |

Because `authenticate()` re-reads `is_active` on every request, deactivation takes
effect immediately even for a token that was not explicitly revoked.

---

## 3. Schema Changes (`dz_erp/database.py`)

```sql
ALTER TABLE users ADD COLUMN password_hash TEXT;
ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN locked_until TEXT;
ALTER TABLE users ADD COLUMN password_updated_at TEXT;

CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    token_prefix TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revocation_reason TEXT,
    last_used_at TEXT,
    issued_to_ip TEXT,
    user_agent TEXT
);
CREATE INDEX idx_auth_tokens_hash ON auth_tokens(token_hash);
CREATE INDEX idx_auth_tokens_user ON auth_tokens(user_id);
CREATE INDEX idx_auth_tokens_expiry ON auth_tokens(expires_at);
```

`_migrate_schema()` applies these idempotently on every startup, so databases created
before this change are upgraded in place. `users.api_token_hash` is retained for
schema compatibility but is **deprecated and unused**; `auth_tokens` supersedes it.

---

## 4. Secondary Defect Fixed: Denial Audit Events Were Rolled Back

`read_supplier_invoice_secure()` and `read_source_document_file_secure()` appended a
`SECURITY_*_BLOCKED` audit event and then raised inside the same
`db.transaction()` block. The exception propagated through the context manager's
rollback path, **discarding the very audit record that documented the blocked
cross-tenant or unauthorized-role read**. A tamper-evident log that silently drops
security denials cannot support forensic investigation.

Both functions now record the denial, let the transaction commit, and raise
afterwards. The same pattern is applied to every authentication rejection path in
`AuthManager` (`AUTH_LOGIN_FAILED`, `AUTH_ACCOUNT_LOCKED`,
`AUTH_CREDENTIAL_REVOKED_REJECTED`, `AUTH_CREDENTIAL_EXPIRED_REJECTED`,
`AUTH_USER_INACTIVE_REJECTED`), so failed sign-ins and rejected credentials are
durable rather than rolled back.

---

## 5. API Surface Changes

| Endpoint | Auth | Notes |
| :--- | :--- | :--- |
| `GET /healthz` | Public | Unchanged |
| `POST /api/v1/auth/token` | Public (credential in body) | Username/password → bearer token; returns the plaintext token **once** |
| `GET /api/v1/auth/session` | Bearer | Returns the server-resolved principal |
| `POST /api/v1/auth/revoke` | Bearer | Revokes the presented token |
| `POST /api/v1/documents/upload` | Bearer | Was `X-User-Id`/`X-Company-Id` |
| `GET /api/v1/documents/{id}/file` | Bearer | " |
| `PATCH /api/v1/documents/{id}/fields` | Bearer | " |
| `POST /api/v1/invoices` | Bearer | " |
| `GET /api/v1/invoices/{id}` | Bearer | " |
| `POST /api/v1/invoices/{id}/submit-approval` | Bearer | " |
| `POST /api/v1/invoices/{id}/decide-approval` | Bearer | " |
| `POST /api/v1/invoices/{id}/post` | Bearer + `Idempotency-Key` | " |
| `POST /api/v1/invoices/{id}/reverse` | Bearer + `Idempotency-Key` | " |

---

## 6. Operational Requirements

1. **TLS is mandatory.** Bearer tokens are plaintext-equivalent credentials; the API
   must only be served over HTTPS. Tokens are also redacted from structured logs by
   `StructuredRedactingLogger`.
2. **Secrets provisioning.** `ERP_AUDIT_HMAC_SECRET` must be replaced with a
   deployment-specific secret; the in-repo default is for tests only.
3. **Initial credentials.** There is no bootstrap admin: the first user must be
   created through `AuthManager.create_user()` by an out-of-band provisioning step.
4. **Database backend.** Local and staging verification use SQLite with
   `BEGIN IMMEDIATE` and immutability triggers. High-concurrency multi-node
   production should target PostgreSQL 16+ with Row-Level Security and
   `REVOKE UPDATE, DELETE, TRUNCATE ON audit_events`.
5. **Known limitation.** `_audit()` cannot attribute an event to a tenant when the
   presented token is unknown (no resolvable `company_id`), so unknown-token
   rejections are logged through `StructuredRedactingLogger` rather than the
   per-tenant audit chain. Rate limiting at the reverse proxy is recommended to
   absorb credential-stuffing volume.

---

## 7. Phase 8 Additions — Upload Boundary and Forwarded-Header Trust

Phase 7 removed the *identity* trust boundary. Phase 8 closes two adjacent
boundaries that the same threat model covers.

### 7.1 Upload boundary (`dz_erp/upload.py`)

| Control | Behaviour |
| :--- | :--- |
| Size limit | 10 MiB default (`DEFAULT_MAX_UPLOAD_BYTES`), overridable with `ERP_MAX_UPLOAD_BYTES`. A malformed, zero or negative override is ignored so a typo can never disable the limit. |
| Enforcement point | Enforced **while reading**, in 64 KiB chunks. The full body is never materialised for an oversized request, and reading stops at the limit. |
| Declared `Content-Length` | Treated as an optimisation only. It is checked before reading, but the chunked loop is authoritative because the value is client-controlled. |
| Type allowlist | Extension **and** declared MIME must both be allowed **and** agree. `application/pdf`, `image/png`, `image/jpeg` / `.pdf`, `.png`, `.jpg`, `.jpeg`. |
| Content verification | Magic-byte sniffing must agree with the declared type. A renamed `MZ` executable, PNG bytes labelled `application/pdf`, and PDF bytes labelled `image/png` are all rejected. |
| Filename hygiene | Path separators (POSIX **and** Windows), traversal, NUL bytes and names over 255 bytes are rejected — `../etc/passwd.pdf` has a legal `.pdf` extension but is still refused. |
| Resource release | A `finally` block always closes the upload, removing Starlette's `SpooledTemporaryFile` spill file. Cleanup failures never mask the original error. |
| Preserved controls | EICAR / active-PDF quarantine, `PrivateDocumentVault` Fernet encryption at rest, and human review are unchanged. |
| HTTP mapping | `413` too large, `415` disallowed type, `400` inconsistent metadata, all logged through `StructuredRedactingLogger`. |
| Ordering | Authentication runs **before** the size check, so an oversized body from an unauthenticated caller yields `401`, never `413`. The size check cannot be used to probe the authentication boundary. |
| Discoverability | `GET /api/v1/upload-policy` publishes the enforced limit and allowlist publicly, so clients can size requests correctly. |

Proxy-level limits (`client_max_body_size` in nginx, and similar) are a useful
*complement* but never a replacement: they are configuration that can be absent,
misconfigured, or bypassed by an internal caller.

### 7.2 Forwarded-header trust (`dz_erp/net.py`)

`X-Forwarded-For` was previously stored verbatim in `auth_tokens.issued_to_ip`,
so any caller could choose the IP address that would later appear in audit and
forensic records. The policy is fail-closed:

| Scenario | Behaviour |
| :--- | :--- |
| No trusted proxies configured (**default**) | `X-Forwarded-For` is ignored; the direct peer address is used. |
| Peer inside a configured trusted-proxy CIDR | The chain is honoured, and the **right-most untrusted** entry wins. |
| Peer outside every trusted-proxy CIDR | The chain is discarded entirely; the direct peer address is used. |
| Caller prepends a forged entry | Does not win: the right-most untrusted address (what the trusted proxy actually observed) is used. |
| Absent / empty / whitespace / duplicate commas / ports / IPv6 brackets / non-IP | Parsed without raising; unparseable entries are skipped. |
| No peer address at all | Resolves to `unknown`; no value is invented. |
| Malformed CIDR in configuration | Dropped, so a configuration typo cannot fail open. |

**Scope of the header.** The forwarded header is used **only** to populate
`issued_to_ip`. It is never consulted for authorisation: `user_id`, `company_id`
and `role` are always derived server-side from the bearer credential. It is
therefore not a rate-limit, audit, or access-control bypass.

**Future configuration.** If this deployment is placed behind a known reverse
proxy or load balancer, set `ERP_TRUSTED_PROXIES` to that infrastructure's CIDR
range (comma or space separated). Until then the secure default is to trust
nothing, which is what the tests assert.
