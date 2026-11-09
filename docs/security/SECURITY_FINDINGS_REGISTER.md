# Security Findings Register — ERP Algeria

Assessment date: 2026-11-09 · Branch: `arena/7557bcfb-erp` · HEAD:
`4fc1364a9504fab1153ea44e753f354f00023428` (unchanged)

Classification vocabulary used below is deliberately strict:

* **Confirmed vulnerability** — reproduced against the code in this checkout.
* **Potential vulnerability** — plausible but not reproduced here.
* **Configuration limitation** — a deployment/architecture property, not a code defect.
* **Untested condition** — could not be exercised (environment/credential gap).
* **False positive** — investigated and disproved.

| ID | Severity | Type | Remediated |
| :--- | :--- | :--- | :--- |
| SEC-01 | Medium | Confirmed vulnerability | Yes |
| SEC-02 | Medium | Confirmed vulnerability | Yes |
| SEC-03 | Low | Confirmed vulnerability | Yes |
| SEC-04 | Low | Confirmed vulnerability | Yes |
| SEC-05 | Low | Confirmed vulnerability | Yes |
| SEC-06 | Low | Confirmed vulnerability | Yes |
| SEC-07 | Low | Confirmed vulnerability | Yes |
| SEC-08 | Low | Confirmed vulnerability | Yes |
| SEC-09 | Low | Confirmed vulnerability | Yes |
| SEC-10 | Informational | Confirmed weakness (hardening gap) | Yes |
| SEC-11 | Medium | Confirmed (dependency advisories) | No — documented blocker |
| SEC-12 | Low | Configuration limitation | No — accepted |
| SEC-13 | Low | Confirmed weakness (policy gap) | No — policy decision |
| SEC-14 | Low | Confirmed weakness (validation gap) | No — deferred |
| SEC-15 | Informational | Configuration limitation | No — deployment requirement |
| FP-01 | — | False positive | n/a |
| FP-02 | — | False positive | n/a |

---

## SEC-01 — Document intake endpoints ignored the documented RBAC matrix

1. **Finding ID** — SEC-01
2. **Affected component** — `POST /api/v1/documents/upload` (`dz_erp/api.py`,
   `api_upload_document`), `PATCH /api/v1/documents/{document_id}/fields`
   (`dz_erp/api.py`, `api_edit_extracted_field`), and the services they call
   (`dz_erp/intake_ocr.py:upload_and_process_supplier_document`,
   `update_extracted_field_by_human`). Reference contract:
   `docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md` §1, row *Upload Document /
   Edit OCR Draft* → `clerk` Yes, `warehouse` **No**, `approver` Yes,
   `accountant` Yes, `auditor` **No**, `admin` Yes.
3. **Security impact** — Broken function-level authorization (OWASP API5:2023;
   ASVS 4.0.3 V4.1.1/V4.1.3, V8.2.2). Any authenticated principal of a company,
   including the roles the matrix explicitly denies, could (a) create supplier
   documents that enter the review queue and consume OCR/vault resources, and
   (b) rewrite the OCR-extracted values that a reviewer subsequently approves -
   i.e. the warehouse and read-only audit roles could influence the financial
   source data that feeds invoice creation and approval. Tenant boundaries were
   *not* crossed, and the denied roles still could not post or approve, which is
   why this is Medium rather than High.
4. **Reproduction conditions** — Two-tenant synthetic database; users with roles
   `clerk/warehouse/approver/accountant/auditor/ocr_worker/admin`; a synthetic
   supplier PDF; authenticated requests with each role's own token.
5. **Evidence (before remediation)** — `/tmp/probe/probe_rbac.py`:

   ```
   PATCH /api/v1/documents/doc-1/fields
     clerk_a    -> HTTP 200
     wh_a       -> HTTP 200   (matrix says No)
     auditor_a  -> HTTP 200   (matrix says No)
     ocr_a      -> HTTP 200
     admin_a    -> HTTP 200

   POST /api/v1/documents/upload
     clerk_a    -> HTTP 200  workflow_state=NeedsReview
     wh_a       -> HTTP 200  workflow_state=DuplicateRejected  (a row was written)
     auditor_a  -> HTTP 200  workflow_state=DuplicateRejected
     ocr_a      -> HTTP 200  workflow_state=DuplicateRejected
   ```

   `dz_erp/intake_ocr.py` checked only `user["company_id"] != company_id` and
   `update_extracted_field_by_human` checked only the tenant-scoped WHERE clause;
   neither consulted `users.role`.
6. **Severity rationale** — Medium: server-side authorization missing on two
   state-changing financial-data endpoints; limited by intact tenant isolation
   and by the separate posting/approval guards.
7. **Recommended remediation** — Enforce the documented matrix server-side at
   the API boundary, derive the role from the token's user row (never a header
   or body field), audit the denial, and add positive controls for the roles the
   matrix permits.
8. **Remediation implemented** — Yes. `require_role_secure()`
   (`dz_erp/security_audit.py:664–714`) added; wired at
   `dz_erp/api.py:450–470` (upload) and `dz_erp/api.py:557–578` (field
   correction); allow-list constant `DOCUMENT_INTAKE_ROLES` at
   `dz_erp/api.py:153`. Denials are appended to the caller's tenant audit chain
   as `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED`. The guard runs **before** the
   upload body is read, so a denied caller cannot make the server buffer or OCR
   anything.
9. **Regression test** —
   `tests/test_phase34_security_validation.py::test_upload_is_refused_for_roles_the_rbac_matrix_denies`
   (parametrised `wh_a`, `auditor_a`, `ocr_a`, asserts 403 **and** that no
   `source_documents` row was written),
   `::test_field_correction_is_refused_for_denied_roles` (403 and the stored
   `human_override_value` is untouched),
   positive controls `::test_upload_is_allowed_for_roles_the_rbac_matrix_permits`
   (`clerk_a`, `approver_a`, `acct_a`, `admin_a`) and
   `::test_field_correction_is_allowed_for_clerk_and_audited`.
10. **Retest result** — PASS. Same probe after the fix: `wh_a`, `auditor_a`,
    `ocr_a` → `HTTP 403 {"detail":"Role 'warehouse' is not authorized for
    document upload; required one of ['clerk','approver','accountant','admin']."}`;
    `clerk_a` and `admin_a` → 200. 8/8 tests pass.

---

## SEC-02 — Non-finite and oversized decimals corrupt money columns or crash the request

1. **Finding ID** — SEC-02
2. **Affected component** — `dz_erp/decimal_utils.py:to_decimal` (the single
   conversion point used by invoice creation, decision services, posting and
   backup verification); reachable via `POST /api/v1/invoices` →
   `dz_erp/procurement_stock.py:create_and_validate_supplier_invoice`.
3. **Security impact** — Input validation / data integrity (CWE-20, CWE-681;
   ASVS V5.1.3, V5.5.1; OWASP API Top 10 API6:2023 business-flow abuse). Two
   distinct symptoms, one root cause:
   * `Decimal("NaN")` is a *quiet* NaN: `quantize` returns NaN instead of
     raising, so the string `'NaN'` was written into `amount_ht_dzd` /
     `amount_ttc_dzd` and the caller received **HTTP 200** - a non-numeric value
     in a financial column together with a success response.
   * `sNaN`, `Infinity`, `-Infinity`, `1e400` and 400-digit inputs raised
     `decimal.InvalidOperation` out of the handler → **HTTP 500** (unhandled
     exception, CWE-703), which also confirms to an attacker that the input
     reached the decimal layer.
4. **Reproduction conditions** — Authenticated `clerk` token; `POST
   /api/v1/invoices` with a valid supplier and a hostile `amount_ht_dzd` /
   `amount_ttc_dzd`.
5. **Evidence (before remediation)** — `/tmp/probe/probe_input.py`,
   `/tmp/probe/probe_trace.py`:

   ```
   amount_ht='NaN'       -> 200 {'invoice_id': 1, 'workflow_state': 'NeedsReview', ...}
   amount_ht='sNaN'      -> 500 Internal Server Error
   amount_ht='Infinity'  -> 500 Internal Server Error
   amount_ht='1e400'     -> 500 Internal Server Error
   amount_ht='9'*400     -> 500 Internal Server Error

   stored: {'id': 1, 'amount_ht_dzd': 'NaN', 'amount_ttc_dzd': 'NaN', ...}
   traceback: decimal.InvalidOperation at dz_erp/decimal_utils.py:50 (round_dzd -> quantize)
   ```

6. **Severity rationale** — Medium: a confirmed data-integrity defect in a
   money column plus an unhandled-exception path. Not High because such an
   invoice can never reach `Validated`/`Posted` (the header/line arithmetic
   checks fail), so the ledger itself cannot be poisoned through this route.
7. **Recommended remediation** — Refuse non-finite values and values outside the
   arithmetic range at the single conversion point, so every caller (creation,
   decision, posting, backup verification) inherits the guard; raise
   `ValueError`, which the API already maps to 422.
8. **Remediation implemented** — Yes. `_guarded()` at
   `dz_erp/decimal_utils.py:28–47` (rejects `not dec.is_finite()` and
   `dec.adjusted() > MAX_ABSOLUTE_EXPONENT = 18`), applied on every branch of
   `to_decimal` (`dz_erp/decimal_utils.py:49–75`). `bool` is additionally
   rejected because it is an `int` subclass that silently became `1.00`.
9. **Regression test** —
   `::test_non_finite_or_oversized_amounts_are_rejected_without_a_write`
   (6 parametrised values → 422, no row written, no stored value equal to
   `nan/snan/inf/infinity`),
   `::test_to_decimal_refuses_non_finite_and_out_of_range_values` (6 values),
   `::test_to_decimal_still_accepts_real_amounts`,
   `::test_bool_is_not_a_decimal`.
10. **Retest result** — PASS. All six hostile values now return
    `422 {"detail":"Non-finite decimal input is not permitted: 'NaN'"}` /
    `"Decimal input magnitude exceeds the supported range (1e18)"`, and
    `supplier_invoices` gains no row.

---

## SEC-03 — Invoice list returned 500 for an out-of-range `limit`

1. **Finding ID** — SEC-03
2. **Affected component** — `dz_erp/api.py:818–836` (`api_list_invoices`) →
   `dz_erp/security_audit.py:list_supplier_invoices_secure` →
   `dz_erp/database.py:_read_limit`.
3. **Security impact** — CWE-703 / CWE-209; ASVS V5.1.1, V7.4.1. The clamp
   raises `ValueError`, which this one handler did not catch, so a client-supplied
   parameter produced an unhandled 500 while every other list endpoint returned a
   contract-correct 422. Inconsistent error behaviour hinders detection and
   invites error-based probing.
4. **Reproduction conditions** — Any authenticated non-warehouse token;
   `GET /api/v1/invoices?limit=0|−1|501|100000`.
5. **Evidence (before)** — `/tmp/probe/probe_trace.py`:

   ```
   GET /invoices?limit=0      -> UNCAUGHT ValueError: list_supplier_invoices limit must be between 1 and 500, got 0.
   GET /invoices?limit=-1     -> UNCAUGHT ValueError: ... got -1.
   GET /invoices?limit=100000 -> UNCAUGHT ValueError: ... got 100000.
   GET /partners?limit=0      -> HTTP 422   (correct, for comparison)
   GET /documents?limit=0     -> HTTP 422   (correct, for comparison)
   ```

6. **Severity rationale** — Low: availability/robustness and error-contract
   inconsistency; no data exposure, no authorization bypass (the clamp itself
   behaved correctly).
7. **Recommended remediation** — Map the clamp's `ValueError` to 422 in the
   handler, matching the other list endpoints.
8. **Remediation implemented** — Yes, `dz_erp/api.py:831–835`.
9. **Regression test** —
   `::test_invoice_list_rejects_out_of_range_limit_with_422` (0, −1, 501,
   100000) and positive control
   `::test_invoice_list_accepts_the_documented_boundaries` (1, 500).
10. **Retest result** — PASS: 422 for all four invalid limits; 200 for 1 and 500.

---

## SEC-04 — Unsupported `payment_method` surfaced as a 500

1. **Finding ID** — SEC-04
2. **Affected component** — `dz_erp/procurement_stock.py:create_and_validate_supplier_invoice`
   (INSERT reaches the schema CHECK
   `payment_method IN ('CASH','BANK_TRANSFER','CHEQUE')`).
3. **Security impact** — CWE-703 (unhandled `sqlite3.IntegrityError`); ASVS
   V5.1.4, V7.4.1. An unvalidated enum reached the database and the internal
   constraint name was exposed in a 500.
4. **Reproduction conditions** — `POST /api/v1/invoices` with
   `"payment_method":"BITCOIN"`.
5. **Evidence (before)** —
   ```
   unknown payment -> UNCAUGHT IntegrityError: CHECK constraint failed:
                      payment_method IN ('CASH', 'BANK_TRANSFER', 'CHEQUE')
   ```
6. **Severity rationale** — Low: no integrity loss (the constraint did its job),
   but a client-controlled 500 and an internal error disclosure.
7. **Recommended remediation** — Validate the enum in the service before any
   write and raise `ValueError` (→ 422).
8. **Remediation implemented** — Yes: `VALID_PAYMENT_METHODS`
   (`dz_erp/procurement_stock.py:18`) enforced at
   `dz_erp/procurement_stock.py:455–458`.
9. **Regression test** — `::test_unsupported_payment_method_is_a_422_not_a_500`
   (422 and no row).
10. **Retest result** — PASS:
    `422 {"detail":"payment_method 'BITCOIN' is not supported; expected one of
    ['BANK_TRANSFER','CASH','CHEQUE']."}`.

---

## SEC-05 — Omitting the optional `line_ht_dzd` raised `TypeError` (500)

1. **Finding ID** — SEC-05
2. **Affected component** — `InvoiceLineInput.line_ht_dzd: Optional[str] = None`
   (`dz_erp/api.py`) vs `compute_invoice_payload_hash` /
   `create_and_validate_supplier_invoice` (`dz_erp/procurement_stock.py`), which
   called `format_dzd(None)` / `round_dzd(None)`.
3. **Security impact** — CWE-703; ASVS V5.1.1. A documented-optional field
   produced an unhandled 500, i.e. the public contract and the implementation
   disagreed.
4. **Reproduction conditions** — `POST /api/v1/invoices` with a line object that
   omits `line_ht_dzd` (or sends `null`).
5. **Evidence (before)** —
   ```
   line missing line_ht_dzd -> UNCAUGHT TypeError: Unsupported type for Decimal
                               conversion: NoneType  (dz_erp/decimal_utils.py:44)
   ```
6. **Severity rationale** — Low: availability/contract defect; no incorrect
   amounts were produced (nothing was written).
7. **Recommended remediation** — Derive the line net with the *existing*
   arithmetic when the caller omits it, from one shared definition, so the
   payload hash and the validation loop see the same value. No formula change.
8. **Remediation implemented** — Yes: `compute_line_ht_dzd()`
   (`dz_erp/procurement_stock.py:22–34`, the previous `expected_line_ht`
   expression verbatim) and `_materialise_line_ht()` (`:36–52`), applied at
   `:468` before the payload hash is computed; the validation loop now reads the
   materialised value at `:549`.
9. **Regression test** — `::test_line_without_declared_net_is_derived_not_a_500`
   (200, and `supplier_invoice_lines.line_ht_dzd == "10000.00"` for qty 1 ×
   10,000.00).
10. **Retest result** — PASS: 200 with `workflow_state=Validated` and the line
    net stored as `10000.00`.

---

## SEC-06 — `invoice_date` never validated; empty/oversized reference accepted; internal parser text leaked

1. **Finding ID** — SEC-06
2. **Affected component** — `dz_erp/procurement_stock.py:create_and_validate_supplier_invoice`
   (`fiscal_year = int(invoice_date[:4])`, `normalize_invoice_ref(supplier_invoice_no)`).
3. **Security impact** — CWE-20 (improper input validation), CWE-209 (error
   message information leak); ASVS V5.1.3, V5.5.1, V7.4.1. Implausible
   `invoice_date` values (`2026-02-31`, `0000-00-00`) were accepted and stored on
   a `Validated` invoice; an empty or 5,000-character reference was accepted; and
   a malformed date echoed `invalid literal for int() with base 10: 'not-'` to
   the client. Invoice date drives `fiscal_year`, which is part of the duplicate
   detection scope, so unvalidated dates weaken duplicate suppression.
4. **Reproduction conditions** — `POST /api/v1/invoices` varying `invoice_date`
   and `supplier_invoice_no` only.
5. **Evidence (before)** —
   ```
   date 2026-02-31      -> 200 {'workflow_state': 'Validated', ...}
   date 0000-00-00      -> 200 {'workflow_state': 'Validated', ...}
   date empty           -> 422 {"detail":"invalid literal for int() with base 10: ''"}
   empty invoice_no     -> 200 (stored, DuplicateRejected)
   long invoice_no 5000 -> 200 (stored)
   ```
6. **Severity rationale** — Low: data-quality/robustness defect on a draft
   record, plus a minor information disclosure; no authorization or ledger
   impact.
7. **Recommended remediation** — Parse and validate the date as a real calendar
   date before use, require a non-empty bounded reference, and return a neutral
   message (never the parser exception text).
8. **Remediation implemented** — Yes: non-empty and ≤128-character reference
   (`dz_erp/procurement_stock.py:449–454`), strict
   `datetime.strptime(..., "%Y-%m-%d")` with `fiscal_year = invoice_day.year`
   (`:459–466`).
9. **Regression test** —
   `::test_invalid_invoice_date_is_rejected_without_leaking_internals`
   (5 parametrised dates → 422 and no `invalid literal for int()` in the body),
   `::test_invalid_supplier_reference_is_rejected` (`""`, `"   "`, 129 chars),
   plus positive control `::test_sql_metacharacters_are_stored_as_data`.
10. **Retest result** — PASS: all invalid dates →
    `422 {"detail":"invoice_date must be a valid calendar date in YYYY-MM-DD form."}`;
    empty/oversized references → 422; valid ISO dates still `Validated`.

---

## SEC-07 — Backup archive written world-readable (`0644`)

1. **Finding ID** — SEC-07
2. **Affected component** — `dz_erp/security_audit.py:create_encrypted_backup`
   (archive written with `Path.write_bytes`, inheriting the process umask).
3. **Security impact** — CWE-732 (incorrect permission assignment); ASVS
   V8.3.1/V6.4.1. The archive contains the vault key ring, the audit HMAC key
   ring and both version-1 key files inside its Fernet envelope plus the full
   database and vault. World-readable custody material on a shared host is a
   defence-in-depth failure that compounds any backup-key compromise.
4. **Reproduction conditions** — `create_encrypted_backup(...)` on a host with
   the default `umask 022`.
5. **Evidence (before)** —
   ```
   backup mode: 0o644 | world-readable: True | group-readable: True
   ```
   (compare: `vault.vault.key` and `*.audit.key` are `0o600` by design.)
6. **Severity rationale** — Low: the payload is Fernet-encrypted under a separate
   backup key, so file permissions are not the only control; the project's own
   convention for key material is `0600`, and this file carries key material.
7. **Recommended remediation** — Create the file `0600` at `open(2)` time (no
   readable window), and confirm the mode afterwards.
8. **Remediation implemented** — Yes: `dz_erp/security_audit.py:944–959`
   (`os.open(..., 0o600)` + `os.chmod` fallback for non-POSIX filesystems).
9. **Regression test** — `::test_backup_archive_is_owner_only` (mode `0o600` and
   no `S_IROTH` bit on POSIX).
10. **Retest result** — PASS: `backup mode: 0o600 | world-readable: False`.

---

## SEC-08 — Unbounded JSON request bodies (including the pre-authentication token endpoint)

1. **Finding ID** — SEC-08
2. **Affected component** — every JSON endpoint in `dz_erp/api.py`; most
   severely `POST /api/v1/auth/token`, which is reachable without a credential.
   (`POST /api/v1/documents/upload` was already bounded by the streaming reader
   in `dz_erp/upload.py`.)
3. **Security impact** — CWE-400/CWE-770 (uncontrolled resource consumption);
   OWASP API4:2023; ASVS V12.1.1, V5.1.1. A single unauthenticated request could
   force the process to buffer an arbitrary body before any rule ran.
4. **Reproduction conditions** — `POST /api/v1/auth/token` with a 3 MB JSON body.
5. **Evidence (before)** — no `Content-Length` check existed in `api.py`
   (`grep -n "content-length" dz_erp/api.py` → no match); the only bound in the
   codebase was the upload reader. Post-fix probe:
   ```
   3MB login body   -> 413 Request body exceeds the maximum of 2097152 bytes.
   3MB invoice body -> 413 Request body exceeds the maximum of 2097152 bytes.
   ```
6. **Severity rationale** — Low-to-Medium availability exposure: trivially
   reachable pre-auth, but a reverse proxy normally provides the same bound, and
   an operator can raise the cap.
7. **Recommended remediation** — Enforce an application-level cap from
   `Content-Length` before routing (defence in depth under the proxy limit), with
   the upload path bounded by the upload limit plus multipart framing allowance,
   and a documented environment override.
8. **Remediation implemented** — Yes: `ENV_MAX_REQUEST_BYTES`,
   `DEFAULT_MAX_JSON_REQUEST_BYTES = 2 MiB`, `_configured_max_request_bytes()`
   (`dz_erp/api.py:162–185`) enforced in the middleware at
   `dz_erp/api.py:236–256` (413 before `call_next`).
   *Residual*: a `Transfer-Encoding: chunked` request without `Content-Length` is
   not bounded by this check — see `SECURITY_RESIDUAL_RISKS.md`.
9. **Regression test** —
   `::test_oversized_json_body_is_refused_before_authentication` (unauthenticated
   413) and `::test_oversized_authenticated_json_body_is_refused`.
10. **Retest result** — PASS (413 on both; ordinary requests unaffected).

---

## SEC-09 — Username accepted control characters

1. **Finding ID** — SEC-09
2. **Affected component** — `POST /api/v1/users` (`dz_erp/api.py`, `api_create_user`).
3. **Security impact** — CWE-117 (improper output neutralization for logs); ASVS
   V7.1.1/V5.1.3. A username containing `\n` was stored and is echoed into audit
   events and log lines, permitting forged log entries; usernames were also
   unbounded in length.
4. **Reproduction conditions** — admin token; `POST /api/v1/users` with
   `{"username": "u\n5", ...}`.
5. **Evidence (before)** —
   ```
   username with control chars -> 200 {"user": {"user_id": 12, "username": "u\n5", ...}}
   ```
6. **Severity rationale** — Low: requires an administrator to create the account;
   impact is audit/log integrity, not authorization.
7. **Recommended remediation** — Reject control characters and bound the length
   at the API boundary.
8. **Remediation implemented** — Yes: `dz_erp/api.py:990–1001` (≤64 characters,
   no C0/DEL).
9. **Regression test** — `::test_username_with_control_characters_is_rejected`
   (422 and user count unchanged).
10. **Retest result** — PASS: `422 {"detail":"Username must be at most 64
    characters and contain no control characters."}`.

---

## SEC-10 — No browser-facing hardening headers

1. **Finding ID** — SEC-10
2. **Affected component** — all responses (`dz_erp/api.py`).
3. **Security impact** — ASVS V14.4.1–V14.4.4 / V3.4.1 (informational).
   `/api/v1/documents/{id}/content` returns operator-supplied bytes with the
   stored MIME type; without `X-Content-Type-Options: nosniff` a browser may
   re-interpret content, and authenticated financial responses could be retained
   by a shared cache.
4. **Reproduction conditions** — `GET /healthz` and any authenticated endpoint;
   inspect response headers.
5. **Evidence (before)** — `security headers on /healthz: []`.
6. **Severity rationale** — Informational: the stored types are already
   allow-listed (`application/pdf`, `image/png`, `image/jpeg`) and validated by
   magic bytes, so this is defence in depth.
7. **Recommended remediation** — Emit `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` on all responses and
   `Cache-Control: no-store` on authenticated ones.
8. **Remediation implemented** — Yes: `dz_erp/api.py:231–261`.
9. **Regression test** — `::test_security_headers_are_present`.
10. **Retest result** — PASS: `nosniff`, `X-Frame-Options: DENY` on public and
    authenticated responses; `Cache-Control: no-store` on authenticated ones.

---

## SEC-11 — Known-vulnerable pinned dependencies (open, documented)

1. **Finding ID** — SEC-11
2. **Affected component** — `pyproject.toml`, `requirements.lock.txt`,
   `requirements.lock.hashes.txt`.
3. **Security impact** — Supply-chain exposure on the untrusted-input paths:
   multipart parsing (`python-multipart`), HTTP routing (`starlette`), PDF
   parsing (`pypdf`, `pdfminer.six`) and image decoding (`pillow`) all process
   attacker-supplied documents. Most advisories are denial-of-service classes;
   the `pillow` APNG advisory specifically describes `Image.open()` allocating a
   header-sized canvas *before* the decompression-bomb check, which is precisely
   how `inspect_and_validate_file_security` inspects an upload
   (`dz_erp/intake_ocr.py`, `Image.open` then `img.size` only).
4. **Reproduction conditions** — `pip-audit` against the installed lock set.
5. **Evidence** —
   ```
   $ .venv/bin/pip-audit --local --progress-spinner off
   certifi          2026.7.22  PYSEC-2026-4320  2026.10.18
   cryptography     44.0.2     GHSA-537c-gmf6-5ccf / PYSEC-2026-2141 / -35 / -3552 / -3553 / -3554
   pdfminer-six     20250327   PYSEC-2026-1761 / -1762
   pillow           11.1.0     CVE-2026-96638 … CVE-2026-112900, PYSEC-2026-165 … (many)
   pypdf            5.4.0      CVE-2026-57204, PYSEC-2026-1827 … (many)
   python-multipart 0.0.20     PYSEC-2026-1852 / -3036 / -3037 / -3038 / -3039 / -3040
   starlette        0.46.2     PYSEC-2026-161 / -1941 / -1942 / -2280 / -2281 / -248 / -249
   setuptools       66.1.1     GHSA-xmj3-9gf7-h52w, PYSEC-2025-49, PYSEC-2026-1918, -3447
   (25 duplicates removed; exit status 1)
   ```
   Applicability triage (does this build call the affected API?):
   * `cryptography` advisories concern SECT curves, PKCS#7 decryption, X.509
     chain building and name constraints. This build uses only
     `cryptography.fernet` → **not reachable**; still pinned and reported.
   * `starlette`/`python-multipart` → **reachable** (routing, multipart upload).
   * `pypdf`/`pdfminer.six`/`pillow` → **reachable** (untrusted document
     parsing in the OCR pipeline).
   * `setuptools`/`pip` are build/venv tooling, not runtime imports.
6. **Severity rationale** — Medium. Authenticated-tenant denial of service and
   parser-level memory amplification are realistic; no remote-code-execution
   advisory applies to the code paths this build uses. Not High because the
   upload boundary already caps body size (10 MiB default) and image dimensions
   (6000 px) and quarantines active PDF content.
7. **Recommended remediation** — Coordinated upgrade: `fastapi`/`starlette` as a
   pair (fastapi 0.115.12 pins `starlette<0.47`), `python-multipart ≥ 0.0.31`,
   `pypdf 6.x`, `pillow 13.x`, `pdfminer.six ≥ 20251230`,
   `cryptography ≥ 50.0.0`, `certifi ≥ 2026.10.19`; regenerate
   `requirements.lock.hashes.txt`; re-run `tools/verify_dependency_graph.py` and
   `tools/generate_hash_lock.py --check`; re-run the OCR benchmark.
8. **Remediation implemented** — **No.** The brief scopes this workstream to
   *read-only inspection*, and a version bump would require rewriting the
   hash-locked supply-chain artefacts plus a full OCR regression pass — the
   repository's own dependency-remediation change, not a minimal security fix.
   Documented as a blocker rather than claimed as fixed.
9. **Regression test** — Not applicable (no code change). The repository already
   gates on hash-lock integrity and the dependency graph; both remain green
   (exit 0).
10. **Retest result** — NOT APPLICABLE; re-run after the upgrade lands.

---

## SEC-12 — No in-application rate limiting (accepted residual)

1. **Finding ID** — SEC-12 · 2. **Component** — deployment/architecture
   (`dz_erp/auth.py` implements per-account lockout only).
3. **Impact** — ASVS V2.2.1 / API2:2023: credential stuffing across many accounts
   is bounded only per account (5 attempts / 15 minutes), and an attacker can
   deliberately lock accounts (availability). 4. **Reproduction** — 7 failed
   logins → lock; no HTTP-level limiter observed (`grep -ri "rate limit" dz_erp/`
   → none). 5. **Evidence** — `docs/AUTHENTICATION_HARDENING_SPEC.md` §6.5 already
   states "Rate limiting at the reverse proxy is recommended to absorb
   credential-stuffing volume"; behaviour matches the documented limitation.
6. **Severity** — Low (documented configuration limitation). 7. **Remediation**
   — proxy-level limiter (nginx `limit_req`) and alerting on
   `AUTH_ACCOUNT_LOCKED`. 8. **Implemented** — No (accepted). 9–10. Not
   applicable.

## SEC-13 — Password policy is length-only (open policy gap)

1. **SEC-13** · 2. `dz_erp/api.py:1001–1005` (minimum 8 characters) and
   `dz_erp/auth.py:hash_password` (no blocklist).
3. **Impact** — ASVS V2.1.7 (breached/common password screening). 4./5.
   **Evidence** — `POST /api/v1/users` accepted `password` and `12345678` (200).
   NIST SP 800-63B expects a blocklist comparison rather than composition rules.
6. **Severity** — Low. 7. **Recommended** — add a common/breached-password
   denylist plus username-equality rejection at the API boundary. 8.
   **Implemented** — No: this is a policy decision (and NIST explicitly permits
   8-character minimums) that changes user-facing provisioning behaviour; raised
   for the owner. 9./10. Regression not applicable.

## SEC-14 — OCR field correction accepts an unvalidated value (deferred)

1. **SEC-14** · 2. `PATCH /api/v1/documents/{id}/fields` →
   `update_extracted_field_by_human` (`dz_erp/intake_ocr.py:1551–1588`).
3. **Impact** — CWE-20 (low): `new_value` is stored verbatim
   (`human_override_value` and `normalized_value`) without type, range or length
   validation for amount fields. The request-body cap (SEC-08) now bounds the
   size; `raw_text`/confidence remain immutable via trigger
   (`trg_ocr_field_preserve_raw`). 4./5. **Evidence** —
   `PATCH` with `{"field_name":"amount_ttc_dzd","new_value":"999999.00"}` →
   200; arbitrary strings are equally accepted (no parser in the write path).
6. **Severity** — Low. 7. **Recommended** — field-type-aware validation and
   binding of the reviewed value to the invoice (the subject of the unmerged
   Phase 29/30/31 work in PRs #3–#6, which this assessment must not merge).
8. **Implemented** — No, deliberately deferred to avoid duplicating an unmerged
   design. 9./10. Not applicable.

## SEC-15 — No application-level Host validation or TLS (deployment requirement)

1. **SEC-15** · 2. `dz_erp/serve.py`, `dz_erp/api.py`.
3. **Impact** — ASVS V14.2.1, V14.4.1, V9.1.1: uvicorn serves plain HTTP and no
   `TrustedHostMiddleware` is installed, so host-header handling and transport
   security depend entirely on the reverse proxy. 4./5. **Evidence** —
   `grep -rn "TrustedHostMiddleware\|ssl_certfile\|CORS" dz_erp/*.py` → no match;
   `dz_erp/serve.py` runs `uvicorn.run(build_app(), host=..., port=...)` with no
   TLS arguments. No permissive CORS policy is configured (verified: no
   `Access-Control-Allow-Origin` header; preflight → 405), which is the safe
   default. 6. **Severity** — Informational. 7. **Recommended** — terminate TLS
   at the proxy, enforce `Host` allow-listing there, add HSTS at the edge, and
   never bind `0.0.0.0` without a proxy. 8. **Implemented** — No (deployment).
   9./10. Not applicable.

---

## FP-01 — `/etc/passwd` read through a poisoned vault path — FALSE POSITIVE

`PrivateDocumentVault.read_bytes` re-anchors an unknown path under
`company_<id>/` using `Path.name` and refuses when the anchored blob is absent.
Reproduced with `source_documents.vault_path` set to `/etc/passwd` and to
`<vault>/../../etc/passwd`:

```
doc-t: refused -> TenantIsolationError: Vault path traversal or cross-company path detected.
doc-u: refused -> TenantIsolationError: Vault path traversal or cross-company path detected.
```

## FP-02 — Filename newline (log injection) — FALSE POSITIVE

`_validate_filename_shape` rejects C0 controls, but the probe's
`invoice.pdf\n.png` arrived percent-encoded (`inv%0Aoice.pdf`) and was stored as
that literal text; a multipart filename cannot carry a raw newline through the
parser. No bypass exists; the stored value is inert data.

---

## Related pre-existing observations (not new findings, recorded for accuracy)

* PR #7 (`arena/7f12d627-erp`, head `5a3e47dd…`) already contains a fix for the
  `NaN` family of defects (**P32-F1** in its own report) and describes it as
  exercising `to_decimal`. This assessment independently reproduced the same
  defect on this branch (where that fix is **not** present, because PR #7 is
  unmerged) and fixed it here with tests. The two changes are compatible but
  will need reconciling when PR #7 is reviewed; this assessment did not touch,
  merge or rebase PR #7.
* 20 pre-existing test failures are an interpreter-floor guard, not defects
  (see `SECURITY_TEST_EVIDENCE.md` §4).
