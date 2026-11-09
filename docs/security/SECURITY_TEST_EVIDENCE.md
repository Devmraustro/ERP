# Security Test Evidence — ERP Algeria

Assessment date: 2026-11-09 · Branch `arena/7557bcfb-erp` · HEAD
`4fc1364a9504fab1153ea44e753f354f00023428`

All evidence below was produced in this session. Nothing here is reconstructed
or inferred: each block is the (sanitised) output of a command that was run, or a
precise source reference. No credential, token, key or secret value is
reproduced — tokens appear truncated or redacted.

## 1. Test environment

| Item | Value |
| :--- | :--- |
| Repository | `/home/user/ERP` (clone of `Devmraustro/ERP`) |
| Interpreter | `.venv/bin/python` → Python 3.11.2 |
| Dependency install | `.venv/bin/pip install --no-deps -r requirements.lock.txt` (51 packages, exit 0) |
| Application under test | `dz_erp.api:create_erp_fastapi_app` with a disposable `ERPDatabase` + `PrivateDocumentVault` |
| Fixtures | Synthetic only: two companies, seven roles, one synthetic `%PDF-1.4` document, one synthetic supplier, invoices created per case |
| Probe locations | `/tmp/probe/probe_auth.py`, `probe_rbac.py`, `probe_input.py`, `probe_lines.py`, `probe_trace.py`, `probe_upload_audit.py`, `probe_storage.py`, `probe_body.py` (outside the repository, not committed) |
| Network | No request left the sandbox except `pip`/`pip-audit` package and advisory metadata fetches (`pypi.org`, `files.pythonhosted.org`) |

## 2. OWASP mapping (structure only — not a certification)

ASVS 4.0.3 and OWASP API Security Top 10 (2023) identifiers are used to organise
the workstreams; they are not a compliance claim.

| Test / control | ASVS 4.0.3 | API Top 10 (2023) | Result |
| :--- | :--- | :--- | :--- |
| Credential required on all protected routes; 401 shape | V2.1.1, V2.2.1, V3.1.1 | API2 | PASS |
| Token entropy, storage as digest, revocation, expiry | V3.2.1, V3.2.2, V3.3.1, V3.5.1 | API2 | PASS |
| Password hashing (PBKDF2 600k, salt, constant time) | V2.4.1, V2.4.3, V6.2.1 | API2 | PASS |
| Brute-force lockout and audit | V2.2.1, V7.2.1 | API2 | PASS (no IP limiter: SEC-12) |
| Identity-header spoofing | V2.10.4, V4.1.1 | API2, API5 | PASS (401/403) |
| Role matrix on invoice lifecycle | V4.1.1, V4.1.3, V4.2.1 | API1, API5 | PASS |
| Role matrix on document intake | V4.1.1, V4.1.3, V8.2.2 | API5 | **FAIL → fixed (SEC-01)** |
| Segregation of duties (creator ≠ approver) | V4.1.3, V4.2.1 | API6 | PASS |
| Tenant isolation on object reads/writes | V4.2.1, V8.3.2 | API1 | PASS |
| Tenant identifier cannot be overridden in body/query/header | V4.2.1, V5.5.4 | API1 | PASS |
| Mass assignment | V2.10.4, V5.5.4 | API3 | PASS |
| SQL metacharacters / parameterised access | V5.3.4, V5.3.5 | API8 | PASS |
| Numeric input bounds and finiteness | V5.1.3, V5.5.1 | API6 | **FAIL → fixed (SEC-02)** |
| Enum/date/reference validation, no internal error text | V5.1.3, V5.1.4, V7.4.1 | API8 | **FAIL → fixed (SEC-03/04/06)** |
| Upload type/size/content boundary, quarantine, cleanup | V5.2.2, V12.1.1, V12.2.1, V12.3.1, V12.5.1 | API4, API8 | PASS (RBAC gap: SEC-01) |
| Vault path confinement and key file permissions | V12.3.1, V8.3.1, V6.4.1 | API8 | PASS |
| Backup archive permissions | V8.3.1 | API8 | **FAIL → fixed (SEC-07)** |
| Audit append-only + HMAC chain + fail-closed on lost key | V7.3.1, V7.3.3, V7.2.2 | API9 | PASS |
| Encryption at rest, key rotation, key-version handling | V6.1.1, V6.2.2, V6.4.1 | API8 | PASS (KMS: NOT VERIFIED) |
| Unbounded request body | V5.1.1, V12.1.1 | API4 | **FAIL → fixed (SEC-08)** |
| Rate limiting | V2.2.1, V13.1.1 | API4 | FAIL → accepted residual (SEC-12) |
| Security headers / docs exposure / CORS | V14.4.1, V14.4.2, V14.4.3, V14.2.4 | API8 | **FAIL → fixed (SEC-10)**; docs/CORS already correct |
| Host header / TLS assumptions | V14.2.1, V9.1.1 | API8 | Deployment requirement (SEC-15) |
| Dependency inventory and advisories | V14.2.1, V14.3.1 | API9, API10 | **FAIL → documented (SEC-11)** |
| Secret scanning | V6.4.1 | API8 | PASS |

## 3. Authentication and session evidence

`/tmp/probe/probe_auth.py` (synthetic company 1; users `clerk_a`, `admin_a`,
inactive `off_a`):

```
=== 1. Credential handling
  valid                  -> 200 {"access_token":"dzsess_ZMrf...", "token_type":"bearer", ...}
  wrong password         -> 401 {"detail":"Invalid username or password."}
  unknown user           -> 401 {"detail":"Invalid username or password."}
  inactive user          -> 401 {"detail":"User account is inactive or revoked."}
  empty password         -> 401 {"detail":"Invalid username or password."}
  sql injection creds    -> 401 {"detail":"Invalid username or password."}
  huge password (5000)   -> 401 {"detail":"Invalid username or password."}

=== 2. Token integrity / expiry / revocation
  token prefix: dzsess_bDELmOf  len: 50
  session                -> 200
  no header              -> 401 Missing Authorization header ...
  empty bearer           -> 401 Malformed Authorization header ...
  wrong scheme (Basic)   -> 401 Malformed Authorization header ...
  tampered token         -> 401 Authentication credential is invalid.
  token + trailing space -> 200   (whitespace stripped per RFC 7235)
  two tokens             -> 401 Malformed Authorization header ...
  lowercase scheme       -> 200   (scheme is case-insensitive per RFC 7235)
  expired token          -> 401 Authentication credential has expired.
  revoke                 -> 200 {"revoked":true,"reason":"USER_LOGOUT"}
  reuse after revoke     -> 401 Authentication credential has been revoked.

=== 3. Lockout / brute force
  attempt 1..5 -> 401 Invalid username or password.
  attempt 6    -> 401 Account is temporarily locked due to repeated failed sign-in attempts.
  attempt 7    -> 401 Account is temporarily locked ...
  correct password while locked -> 401 Account is temporarily locked ...
  user row: {'failed_login_count': 0, 'locked_until': '2026-11-09T17:08:24+00:00'}
  audit events: AUTH_TOKEN_ISSUED, AUTH_LOGIN_FAILED ×5, AUTH_ACCOUNT_LOCKED,
                AUTH_LOGIN_REJECTED_ACCOUNT_LOCKED ×3, AUTH_CREDENTIAL_EXPIRED_REJECTED,
                AUTH_TOKEN_REVOKED, AUTH_CREDENTIAL_REVOKED_REJECTED, AUTH_LOGIN_REJECTED_INACTIVE
```

Interpretation: no credential oracle (unknown user and wrong password are
indistinguishable); lockout is server-side, audited, and not bypassable with the
correct password; revocation is immediate. The `user row` line shows the counter
reset to 0 *after* locking, which is the documented behaviour (the lock, not the
counter, is authoritative).

## 4. Suite execution (exact counts and exit codes)

```
$ .venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=/tmp/baseline_junit.xml
collected 695 items / 2 skipped
...
FAILED <20 tests, listed below>
20 failed, 671 passed, 6 skipped, 1 warning in 134.93s (0:02:14)
EXIT=1

$ .venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=/tmp/postfix_junit.xml
collected 746 items / 2 skipped
...
20 failed, 722 passed, 6 skipped, 1 warning in 136.67s (0:02:16)
EXIT=1

$ diff <(grep ^FAILED baseline | sort) <(grep ^FAILED postfix | sort)
(no output)

$ .venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=/tmp/final_junit.xml
collected 746 items / 2 skipped
20 failed, 722 passed, 6 skipped, 1 warning in 138.33s (0:02:18)
EXIT=1
$ diff <(grep ^FAILED baseline | sort) <(grep ^FAILED final | sort)
(no output)      # final confirmation run, executed after the report documents were added
```

Collected 746 = 695 pre-existing + 51 new. Passed 722 = 671 + 51. The failing
set is unchanged, i.e. **no regression was introduced**.

The 20 failures (all `NOT EXECUTED ON THIS HOST`, one cause):

```
tests/test_phase11_audit_key_rotation.py::test_a_pre_versioning_database_restores_from_backup
tests/test_phase11_audit_key_rotation.py::test_backup_restore_preserves_every_audit_key_version
tests/test_phase11_audit_key_rotation.py::test_restore_reports_a_missing_key_version_as_unavailable
tests/test_phase12_key_migration_and_backup.py::test_backup_restores_mixed_ciphertext_into_an_isolated_environment
tests/test_phase12_key_migration_and_backup.py::test_restore_of_a_tampered_registry_fails_closed
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_ciphertext_that_needs_an_absent_key
tests/test_phase12_key_migration_and_backup.py::test_restore_accepts_last_windows_binary_runtime_with_strict_preflight
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_noncanonical_tar_paths_before_writing[../outside.txt]
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_noncanonical_tar_paths_before_writing[vault/../outside.txt]
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_noncanonical_tar_paths_before_writing[../evil/../dest/sub/file]
tests/test_phase12_key_migration_and_backup.py::test_restore_refuses_preexisting_hardlink_target_before_writing
tests/test_phase12_key_migration_and_backup.py::test_restore_requires_a_backup_key_and_does_not_leak_on_failure
tests/test_phase12_key_migration_and_backup.py::test_restore_with_a_contradicting_audit_key_is_refused
tests/test_phase5_security_audit_backup.py::test_audit_immutability_log_redaction_and_encrypted_backup_restore
tests/test_phase6_ocr_benchmark_e2e.py::test_complete_14_step_end_to_end_supplier_invoice_workflow
tests/test_phase8_upload_boundary_and_ci.py::test_backup_and_restore_preserve_the_audit_key
tests/test_phase8_upload_boundary_and_ci.py::test_restored_database_and_vault_are_reopenable_without_the_original_secrets
tests/test_phase9_process_boundary.py::test_restore_is_usable_from_a_fresh_process_in_a_different_directory
tests/test_phase9_process_boundary.py::test_incomplete_restore_is_rejected
tests/test_phase9_process_boundary.py::test_repeated_restores_are_idempotent_and_leave_a_usable_deployment
```

Root cause (identical for all 20):

```
tests/test_phase11_audit_key_rotation.py:133: in test_a_pre_versioning_database_restores_from_backup
    report = restore_and_verify_backup(
dz_erp/security_audit.py:1087: in restore_and_verify_backup
    raise FinancialIntegrityError(
E   dz_erp.database.FinancialIntegrityError: Safe backup restore requires Python
    3.11.17+ or Python 3.12.10+ (and below 3.13); unsafe archive member types and
    paths are rejected.
```

New tests, run alone:

```
$ .venv/bin/python -m pytest tests/test_phase34_security_validation.py -q
51 passed, 1 warning in 3.90s      (exit 0)
```

## 5. Static and supply-chain evidence

```
$ .venv/bin/python -m ruff check dz_erp tests tools
All checks passed!                                   (exit 0)

$ .venv/bin/python -m compileall -q dz_erp tests tools
                                                     (exit 0)

$ .venv/bin/python tools/verify_dependency_graph.py
Dependency graph validation
  documented substitution: opencv-python -> opencv-python-headless==4.11.0.86
  installed distributions: 52
OK - the environment matches the declared dependency graph.     (exit 0)

$ .venv/bin/python tools/generate_hash_lock.py --check
OK - 51 pinned distributions, 1277 hashes, no drift from requirements.lock.txt.   (exit 0)

$ grep -rnE "(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|aws_secret_access_key|api[_-]?key\s*=\s*['\"])" \
      --include=*.py --include=*.toml --include=*.yml dz_erp tools ops .github
(no matches)

$ grep -rn "dz-erp-default" dz_erp/*.py
dz_erp/database.py:561: DEFAULT_AUDIT_HMAC_SECRET = b"dz-erp-default-test-hmac-key-change-in-prod"
dz_erp/database.py:661:     return DEFAULT_AUDIT_HMAC_SECRET
```

The one hard-coded secret is documented as "public by construction" and is
reachable **only** for `:memory:` databases (`_resolve_audit_secret` returns it
on the final line, after the file-backed branches); a file-backed database with
existing audit events and no key file raises `AuditKeyUnavailableError` instead
(verified below).

Dependency advisories: see `SECURITY_FINDINGS_REGISTER.md` SEC-11 for the full
`pip-audit` output and applicability triage (9 affected runtime/tooling
packages; `cryptography` advisories not reachable from the Fernet-only usage).

## 6. Workstream C — multi-tenant isolation evidence

`/tmp/probe/probe_rbac.py` §3 and `/tmp/probe/probe_input.py` §E, tenant-2 admin
against tenant-1 objects:

```
GET   /api/v1/documents/doc-1/fields   -> 403 {"detail":"Cross-company read access denied."}
GET   /api/v1/documents/doc-1/content  -> 403 {"detail":"Cross-company file access denied."}
GET   /api/v1/documents/doc-1/file     -> 403 {"detail":"Cross-company file access denied."}
PATCH /api/v1/documents/doc-1/fields   -> 404 {"detail":"Field amount_ttc_dzd not found on document doc-1"}
GET   /api/v1/invoices/500             -> 403 {"detail":"Cross-company read access denied."}
POST  /api/v1/invoices/500/decide-approval -> 403 {"detail":"Invoice not found in target company."}
POST  /api/v1/invoices/500/submit-approval -> 403 {"detail":"Invoice not found in target company."}
POST  /api/v1/invoices/500/post            -> 403 {"detail":"Invoice not found in target company."}
```

Notes on interpretation, to avoid over-claiming:

* The `PATCH` 404 is not a bypass: the write is scoped by
  `company_id` from the credential, so a foreign document simply has no matching
  row; the response is identical whether the document exists or not (no
  existence oracle).
* Denials on reads are written to the **target** tenant's audit chain
  (`SECURITY_CROSS_COMPANY_READ_BLOCKED`,
  `SECURITY_CROSS_COMPANY_FILE_ACCESS_BLOCKED`) and committed before the error is
  raised — confirmed by code review of
  `dz_erp/security_audit.py:376–495` and by the audit rows present after the
  probe.
* `company_id` supplied as a query parameter
  (`/api/v1/invoices?company_id=2`, `/api/v1/accounts?company_id=2`) has no
  effect: responses stay scoped to `company_id: 1` from the token.
* Audit records and backups have **no HTTP surface at all** in this build
  (the 30 routes registered in `dz_erp/api.py` contain no audit-read or
  backup route; `grep -n "audit" dz_erp/api.py` matches only imports and
  comments), so
  "tenant A cannot access tenant B's audit records/backups" is satisfied by
  absence of an endpoint, which is reported as N/A rather than as a tested pass.
* Background jobs: this revision has no in-process per-tenant job runner
  (`grep -rn "BackgroundTasks|celery|schedule" dz_erp/` → none); the systemd
  timer in `ops/dz-erp-checkpoint-export.{service,timer}` performs a global
  rotation-ledger checkpoint and carries no tenant data. Reported as N/A with
  the evidence, not as PASS.

## 7. Workstream D/E — API and input validation evidence

Before/after (see the findings register for full tables). Selected lines:

```
POST /api/v1/invoices with {"company_id":2,"workflow_state":"Posted","payload_sha256":"deadbeef","id":999}
  before -> 200; stored company_id=1, workflow_state=Validated, payload_sha256=<computed>
  after  -> 200; identical (mass assignment still ignored)

supplier_invoice_no = "FAC-1'; DROP TABLE users; --"
  -> 200; the users table count is unchanged (the repository test asserts it
     equals the fixture count of 8); the stored reference is the literal string

GET /api/v1/invoices?limit=100000
  before -> 500 (uncaught ValueError)
  after  -> 422 {"detail":"list_supplier_invoices limit must be between 1 and 500, got 100000."}
```

## 8. Workstream F — upload boundary evidence

`/tmp/probe/probe_upload_audit.py` (clerk token, synthetic files):

```
traversal posix            -> 400 Uploaded filename must not contain path separators or directory traversal.
traversal windows          -> 400 (same)
reserved device (CON.pdf)  -> 400 Uploaded filename must not be a reserved device name.
300-char name              -> 400 Uploaded filename is too long (limit 255 bytes).
unicode arabic name        -> 200 workflow_state=NeedsReview / DuplicateRejected
extension/mime mismatch    -> 400 Filename extension '.pdf' does not match declared media type 'image/png'
pdf bytes declared png     -> 415 Declared media type 'image/png' does not match the actual content signature 'application/pdf'
png bytes declared pdf     -> 415 (inverse)
empty file                 -> 415 Uploaded content does not match any supported document signature
header-only pdf            -> 400 not a structurally valid PDF (PdfStreamError)
exe disguised as pdf       -> 415 unsupported signature
html disguised as pdf      -> 415 unsupported signature
EICAR inside a PDF         -> QUARANTINED {'findings': ['MALWARE_EICAR_SIGNATURE_DETECTED']}
PDF containing /JavaScript -> 200 security_status=QUARANTINED, workflow_state=Rejected
no extension / .exe        -> 415 Unsupported file extension
ERP_MAX_UPLOAD_BYTES=4096, 22 KB upload -> 413 Uploaded document (22812 bytes) exceeds the maximum allowed size of 4096 bytes.

vault blobs: company_1/<sha>.enc, company_1/<sha>.enc      (all under company_1)
leftover spool files: none
document states: [('NeedsReview','CLEAN'), ('DuplicateRejected','CLEAN'), ('Rejected','QUARANTINED')]
any Approved/Posted document: []
```

Interpretation: a failed/quarantined OCR intake cannot produce an approved
invoice — the document itself is `Rejected` and no `supplier_invoices` row is
created. Temporary upload artifacts are removed by `close_upload` in the
endpoint's `finally` (no spool files remained).

## 9. Workstream G/H/I — storage, audit and key evidence

```
$ /tmp/probe/probe_storage.py
1. Key file permissions
    vault.vault.key      exists: True mode: 0o600
    p7.sqlite3.audit.key exists: True mode: 0o600
    vault root mode: 0o755          (directory listing only; blobs are 0o600)
2. Vault path traversal via a poisoned source_documents.vault_path
    doc-t (/etc/passwd)                  -> TenantIsolationError: Vault path traversal or cross-company path detected.
    doc-u (<vault>/../../etc/passwd)     -> TenantIsolationError: Vault path traversal or cross-company path detected.
3. EICAR *inside* a valid PDF header     -> {'security_status': 'QUARANTINED', 'findings': ['MALWARE_EICAR_SIGNATURE_DETECTED']}
4. Backup file permissions
    before -> mode: 0o644 | world-readable: True | group-readable: True
    after  -> mode: 0o600 | world-readable: False | group-readable: False
    tmp dirs left: []            encrypted (no plaintext key material): True
```

Audit immutability (`/tmp/probe/probe_upload_audit.py` §4):

```
UPDATE audit_events SET action_type='X' WHERE id=1
  -> blocked: AUDIT_TAMPER_VIOLATION: audit_events is append-only; UPDATE is prohibited.
DELETE FROM audit_events WHERE id=1
  -> blocked: AUDIT_TAMPER_VIOLATION: audit_events is append-only; DELETE is prohibited.
verify_audit_chain(1) -> {'valid': True, 'event_count': 4}
```

Tamper-detection path: a disposable **copy** of the database had
`trg_audit_no_update` dropped and one `after_json` rewritten; reopening it
produced:

```
dz_erp.database.AuditKeyUnavailableError: The audit key file
.../tampered.sqlite3.audit.key is missing but .../tampered.sqlite3 already
contains audit events. Refusing to generate a replacement key: doing so would
make every existing chain fail verification and report tampering.
```

i.e. the fail-closed key-resolution guard fires before any tamper verdict — the
correct direction (a lost key is never reported as tampering). Chain-level
tamper detection itself is exercised by the repository's existing
`tests/test_phase5_security_audit_backup.py` and
`tests/test_phase11_audit_key_rotation.py` suites (the latter's restore cases
are among the 20 interpreter-blocked tests).

Key-provider contract:

```
ERP_KEY_PROVIDER=None   -> None
ERP_KEY_PROVIDER=''     -> None
ERP_KEY_PROVIDER=file   -> 'file'
ERP_KEY_PROVIDER=FILE   -> 'file'
ERP_KEY_PROVIDER=kms    -> KeyConfigurationError: ERP_KEY_PROVIDER='kms' cannot be honoured ...
ERP_KEY_PROVIDER=hsm    -> KeyConfigurationError: ... cannot be honoured ...
ERP_KEY_PROVIDER=vault  -> KeyConfigurationError: ... cannot be honoured ...

PrivateDocumentVault(..., encryption_key=b"not-a-valid-fernet-key")
  -> refused: ValueError (Fernet key must be 32 url-safe base64-encoded bytes)
```

## 10. Workstream K — HTTP configuration evidence

```
GET /healthz           -> 200 {"status":"ok","service":"algerian-ai-erp"}
GET /docs              -> 404
GET /openapi.json       -> 404
GET /redoc             -> 404
GET /api/v1/upload-policy -> 200 (deliberately public, non-sensitive)
OPTIONS /api/v1/invoices (Origin: https://evil.example) -> 405, ACAO=None
security headers on /healthz: ['x-content-type-options', 'x-frame-options']
authenticated /api/v1/invoices headers: nosniff, X-Frame-Options: DENY, Cache-Control: no-store
spoofed identity headers, no token -> 401 Missing Authorization header ...
warehouse token + X-User-Id/X-Company-Id/X-Role admin -> 403 Warehouse role is not permitted to read financial invoices.
3 MB JSON body to /api/v1/auth/token -> 413 Request body exceeds the maximum of 2097152 bytes.
```

## 11. What this evidence does **not** show

* No staging deployment was reachable, so nothing here validates the running
  `ops-erp-1` container, the `127.0.0.1:18002` endpoint, the
  `ops_erp-state-staging` volume, or a real MinIO/PostgreSQL backend.
* No AWS KMS/HSM operation was performed (no credentials, no CMK); the provider
  contract was verified only in its fail-closed direction.
* No restore of a real backup was executed (interpreter floor), so DR integrity,
  restored-chain verification and archive path-traversal defences are
  NOT EXECUTED on this host.
* No conclusion is drawn about TLS, HSTS or Host-header enforcement, which are
  deployment concerns (SEC-15).
