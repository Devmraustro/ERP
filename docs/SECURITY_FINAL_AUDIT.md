# Security final audit

**Scope:** the security controls implemented in this repository at commit
`42334da` (branch `arena/7f12d627-erp`, on top of `852f389f`). Everything below
was checked against the code and the test suite that ship with it — not against a
design document and not against a claim inherited from an earlier phase.

**What this audit is not:** a penetration test, a compliance certification, or an
assessment of a *deployed* system. Deployment-dependent controls (proxy headers,
TLS, off-host storage, KMS/HSM) are marked as such.

---

## 1. Control-by-control status

| # | Control | Status | Evidence |
|---|---|---|---|
| 1 | Identity derived only from a server-issued opaque bearer token; `X-User-Id`/`X-Company-Id` ignored | **Implemented, verified** | `tests/test_phase7_authentication_remediation.py` |
| 2 | Only the token hash is stored; TTL; immediate revocation | **Implemented, verified** | same |
| 3 | Password hashing with a work factor, per-user salt | **Implemented, verified** | `dz_erp/auth.py`, auth suites |
| 4 | Failed-login throttling before password verification, audited, `Retry-After` | **Implemented, verified (Phase 35)** | `tests/test_phase35_…` (16) |
| 5 | Trusted-proxy-only `X-Forwarded-For` (fail-closed) | **Implemented, verified** | `tests/test_phase35_…`, `test_phase8_…` |
| 6 | Per-request correlation id + credential-free request log | **Implemented, verified (Phase 35)** | `test_no_password_or_token_is_ever_logged` |
| 7 | RBAC across eight roles, denial audited | **Implemented, verified** | `test_phase25_…`, `test_phase28_user_admin.py` |
| 8 | Tenant isolation at the data layer, not only the API | **Implemented, verified** | `test_phase25_…`, `test_phase34_…` (`test_13`) |
| 9 | Segregation of duties: submit ≠ approve ≠ post | **Implemented, verified** | `tests/test_phase33_invoice_lifecycle.py` |
| 10 | Approval bound to a payload digest; financial edit invalidates it | **Implemented, verified** | `test_phase33_…` (P32-F4) |
| 11 | Review gate at submit, approve **and** post, with recovery instead of dead-end | **Implemented, verified (Phase 34)** | `test_10`, `test_11`, `test_12` |
| 12 | Duplicate extraction rows cannot authorise an unreviewed value | **Implemented, verified (Phase 34)** | `tests/test_phase34_…` (19) |
| 13 | Immutability of posted entries and raw OCR text (DB triggers) | **Implemented, verified** | `test_phase5_…`, immutability tests |
| 14 | Hash-chained audit log with verification | **Implemented, verified** | `test_phase11_audit_key_rotation.py` |
| 15 | Versioned keys, rotation, per-version distribution reporting | **Implemented, verified** | key rotation suites |
| 16 | Off-host rotation-ledger checkpoints with truncation detection | **Implemented, verified in-suite** | `test_phase13_…`, `docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md` |
| 17 | `kms`/`hsm` label cannot silently mean file keys | **Implemented, verified** | `test_phase17_key_providers.py` |
| 18 | Encrypted backups + verified restore in a separate process | **Implemented; CI-verified** (refused below CPython 3.11.17) | `test_phase9_process_boundary.py` |
| 19 | Upload boundary: size, type, content sniffing, generated vault names | **Implemented, verified** | `test_phase8_upload_boundary_and_ci.py` |
| 20 | API docs disabled by default; `/healthz` constant | **Implemented, verified** | `test_phase25_…` |
| 21 | Hash-pinned dependency install; graph validation | **Implemented, verified in CI** | CI steps, `tools/verify_dependency_graph.py` |
| 22 | Read-only operations hardened (no state change on failure) | **Implemented, verified** | `docs/READ_ONLY_OPERATIONS_HARDENING.md` + suite |
| 23 | Security log rotation by size/count | **Implemented, verified** | `test_phase9_durable_security_log.py` |
| 24 | AI never posts or approves; OCR confidence provenance preserved | **Implemented, verified** | OCR suites, review-policy tests |

## 2. Findings raised during this work and their disposition

| ID | Finding | Severity | Disposition |
|---|---|---|---|
| P34-F1 | A duplicate OCR extraction row was decorative: the policy raised a conflict flag that never affected satisfaction, so an unreviewed `5555.00` could be submitted for approval | Medium | **Fixed**, 19 regression tests |
| P34-F2 | `FLAG` / `NOT_PRESENT` decisions could close a duplicate group, laundering an unresolved conflict | Medium | **Fixed** (`test_16`) |
| P34-F3 | A posting-time refusal left the invoice permanently unpostable (`Approved` documents refuse field decisions) | Medium | **Fixed**: refusal is recorded, approval invalidated, invoice recovered to `NeedsReview` |
| P35-F1 | Unlimited password guesses on the token endpoint | Medium | **Fixed**, 16 tests |
| P35-F2 | Requests were unattributable; no correlation id, no access record | Low | **Fixed** |
| P36-F1 | VAT/Timbre computed from compiled constants while versioned, effective-dated configuration sat unread | Medium | **Fixed**, 29 tests |
| P36-F2 | G50 box E2 had no traceability entry | Low | **Fixed** |
| P38-F1 | Migration safety rested on an informal claim | Medium | **Fixed**: 24-test matrix |

Open, accepted (documented, with owner): no MFA; per-process rate limiting;
file-backed keys by default; no device binding; no production-scale DR evidence.

## 3. Verification commands run on this commit

```
python -m ruff check dz_erp tests tools                 -> All checks passed
python -m pytest -q --cov=dz_erp                        -> 890 passed, 20 failed; every
                                                           failure is the CPython 3.11.17
                                                           interpreter floor (identical to baseline)
python -m pytest tests/test_phase34_… tests/test_phase35_… \
                 tests/test_phase36_… tests/test_phase37_… \
                 tests/test_phase38_… -q                -> 94 passed
python tools/risk_register_counts.py                    -> consistent (39 rows)
python -m dz_erp.rotate_keys monitor …                  -> exercised by tests/staging acceptance
```

CI on the published head (`gh run list`) runs the same suites on CPython 3.11.17
and 3.12.15, where the restore paths execute and the interpreter-floor failures
do not occur. The two results describe the same code on different interpreters;
neither is "the" answer, and both are reported.

## 4. Residual risk — stated, not buried

| Risk | Why it remains | Who must close it |
|---|---|---|
| A stolen bearer token is usable until TTL or revocation | No MFA, no device binding | platform owner |
| Multi-replica login ceiling is N × limit | In-process counters | engineering / proxy owner |
| File-backed key material on the vault volume | No KMS/HSM integration | platform owner |
| Backup safety depends on CPython ≥ 3.11.17 | Upstream `tarfile` filters | deployment (pin the interpreter) |
| Untested at production scale | No real dataset available | platform owner (DR drill) |
| Tax computations unqualified legally | Requires an Algerian expert-comptable | engagement's tax signatory |

## 5. Statement

The application implements the controls in §1 and they are demonstrated by
executable tests on this commit. It is **not** certified against any framework,
it has **not** been penetration-tested, and no evidence exists that it has
survived contact with production traffic. Any statement to the contrary would be
unsupported by the artefacts in this repository.
