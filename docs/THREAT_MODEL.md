# Threat model

**Method.** Every threat below is stated as *who* can do *what* to *which asset*,
then answered with the control that exists **in this repository**, the test or CI
run that demonstrates it, and — where the control depends on the deployment —
what remains unverified. A control that only exists in a design document is
listed under "not mitigated here", never in the mitigated columns.

**Assets, in the order a compromise hurts:**

| # | Asset | Where it lives |
|---|---|---|
| A1 | The audit chain (who did what, tamper-evident) | `audit_events`, hash-chained with an HMAC key |
| A2 | Posted financial records and journal entries | `journal_entries`, `journal_entry_lines`, invoices |
| A3 | Source documents (supplier invoices, OCR text) | encrypted vault + `ocr_extracted_fields` |
| A4 | Credentials and session tokens | `users.password_hash`, `auth_tokens` (hash only) |
| A5 | Key material | vault key file / rotation ledger / key provider |
| A6 | Tenant boundaries (one company must not see another) | every query carries `company_id` |

---

## 1. Threats from an unauthenticated network attacker

| ID | Threat | Control | Evidence | Residual |
|---|---|---|---|---|
| T1 | Forge identity with `X-User-Id` / `X-Company-Id` | Headers are not read at all; identity is derived only from a server-issued opaque token | `tests/test_phase7_authentication_remediation.py` | — |
| T2 | Guess passwords indefinitely | Failed logins throttled per (client, account) **before** password verification, audited, `Retry-After` | `tests/test_phase35_…` (16 tests) | Limiter is per process; a multi-replica deployment must also limit at the proxy |
| T3 | Bypass the throttle by rotating `X-Forwarded-For` | Forwarded headers are ignored unless the peer is inside a declared trusted-proxy CIDR | `test_forwarded_for_cannot_escape_the_limit_without_a_trusted_proxy` | An operator who sets `ERP_TRUSTED_PROXIES` too broadly re-opens this — configuration, not code |
| T4 | Replay or steal a session token | Opaque random token; only its hash is stored; TTL; immediate revocation; per-session IP and user-agent recorded | `tests/test_phase7_…`, `test_phase28_user_admin.py` | A token stolen from a client machine is valid until TTL/revocation — no device binding |
| T5 | Upload a malicious file (zip bomb, polyglot, path traversal) | Content sniffing, size limit, PDF-only intake, vault filename is generated (never derived from user input) | `tests/test_phase8_upload_boundary_and_ci.py`, `test_phase9_process_boundary.py` | Parser-level exploits in the OCR/PDF stack are out of scope (third-party) |
| T6 | Enumerate endpoints / read docs | `/docs`, `/redoc`, `/openapi.json` 404 unless explicitly enabled; `/healthz` returns a constant | `test_phase25_read_api.py` | — |
| T7 | Denial of service by request volume | Optional per-client ceiling; body-size limit; single-process SQLite | `test_global_ceiling_is_off_by_default_and_enforces_when_configured` | **Not a load-level defence.** Real DoS protection belongs at the proxy/CDN; stated plainly rather than implied |

## 2. Threats from an authenticated insider (the real risk in an ERP)

| ID | Threat | Control | Evidence |
|---|---|---|---|
| T8 | Clerk approves and posts their own invoice | Segregation of duties: submit / approve / post are distinct role-gated actions; approval is bound to the payload digest | `tests/test_phase33_invoice_lifecycle.py` |
| T9 | Approver approves one payload, then the amounts change | Editing a financially approved invoice invalidates the approval (`INVALIDATED`), clears the approved digest and returns it to `NeedsReview` | `test_phase33_…` (P32-F4) |
| T10 | Warehouseman reads financial data via a list endpoint | Warehouse-role denial is enforced on list **and** detail views, and the refusal is audited | `test_phase25_read_api.py` |
| T11 | Reviewer "confirms" a value that was never actually reviewed | Decisions bound to the extraction rows they covered; `FLAG`/`NOT_PRESENT` cannot close a duplicate group; AI never posts or approves | `tests/test_phase34_…` (19 tests) |
| T12 | Reach the ledger with an unreviewed OCR value | The review gate runs at submit, approve **and** post; a refusal at post time recovers the invoice to `NeedsReview` instead of dead-ending it | `test_10`, `test_11`, `test_12` |
| T13 | Cross-tenant read/write by editing an id | Every data-layer operation takes `company_id` and is refused on mismatch (`TenantIsolationError`); API-derived identity only | `test_phase25_…`, `test_phase34_…` (`test_13`), `test_phase27_review_api.py` |
| T14 | Edit or delete a posted entry, or rewrite raw OCR text | Immutability triggers: posted journal entries and raw OCR fields cannot be updated | `test_phase5_security_audit_backup.py`, `OCR_RAW_IMMUTABILITY_VIOLATION` |
| T15 | Cover tracks by rewriting the audit log | Hash-chained events; verification detects a modified or removed row; chain state survives backup/restore | `test_phase11_audit_key_rotation.py`, `test_phase5_…` |

## 3. Threats to key material and audit continuity

| ID | Threat | Control | Evidence | Residual |
|---|---|---|---|---|
| T16 | Key compromise without detection | Versioned keys, rotation with per-version distribution reporting, ledger checkpoints exported off-host | `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`, `dz_erp/rotate_keys.py status/rotate/monitor` | Off-host delivery is an operator duty; unverified here |
| T17 | Truncate the rotation ledger | Checkpoint comparison (`dz-erp-checkpoint-verify.timer`) fails on a mismatched head or a stale checkpoint | `docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md` | Requires the checkpoint to actually leave the host |
| T18 | Silently fall back to file keys under a `kms`/`hsm` label | `ERP_KEY_PROVIDER=kms\|hsm` makes start-up **refuse** unless a real provider is supplied | `test_phase17_key_providers.py`, `docs/KEY_PROVIDER_CONTRACT.md` | A real KMS/HSM remains `BLOCKED_EXTERNAL` |
| T19 | Tampered or substituted dependency | Hash-pinned lockfile installed with `--require-hashes --no-deps`; graph validated | CI step + `tools/verify_dependency_graph.py` | Does not vet the publisher, only the artifact |
| T20 | A backup that cannot be verified | Restore runs in a **separate process**, rejects unsafe archive members, and requires the interpreter floor | `test_phase9_process_boundary.py` (CI), `test_phase8_…` | Below CPython 3.11.17 the safe path refuses — a deliberate refusal, not a defect |
| T21 | Security log silently stops being durable | A sink failure is not swallowed; the log is written with owner-only permissions and rotated by size | `test_a_failing_sink_is_not_swallowed` | Log shipping off-host is external |

## 4. What is explicitly out of scope here

* Host, container-runtime, hypervisor and network compromise. The application
  cannot defend its own kernel.
* Physical access to the vault volume.
* A malicious authenticated administrator: they can read what the system holds.
  The audit chain makes their actions visible afterwards; it does not prevent them.
* Parser memory-safety bugs inside the OCR/PDF dependencies.
* Legal qualification of tax computations (see
  `docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §6).

## 5. Threats accepted with a named owner

| Threat | Why accepted for now | Owner |
|---|---|---|
| No MFA | Deployment decision, not implemented; a stolen token is usable within its TTL | platform owner |
| No shared-store rate limiter | Would need an external dependency; documented as per-process | engineering |
| No device binding for tokens | Out of scope for bearer sessions | engineering |
| File-backed keys by default | KMS/HSM integration is `BLOCKED_EXTERNAL` | platform owner |
