# Phase 34 — Prioritised integration matrix

Classification key: **MUST INTEGRATE** (release-blocking gap), **SHOULD INTEGRATE** (real value,
not blocking), **ALREADY PRESENT** (no action), **DO NOT INTEGRATE** (would harm), **REQUIRES EXPERT
REVIEW** (competent decision needed before it can be classified).

"Source" is always `2427fda` (PR #7) unless stated; "Destination" is the workspace branch
`arena/7557bcfb-erp`.

---

## P0 — MUST INTEGRATE

| # | Change | Source file | Destination | Risk | Required tests after |
| --- | --- | --- | --- | --- | --- |
| 1 | **SEC-15 Host allow-list** — `parse_allowed_hosts`, `_strip_port`, `allowed_hosts_from_env`, `host_is_allowed`, `describe_allowed_hosts` | `dz_erp/net.py` (+101) | `dz_erp/net.py` (append) | Low — pure addition, no existing symbol touched | `test_phase59` Host cases; a live probe asserting 400 on `Host: evil.com` and 200 on `localhost` |
| 2 | **SEC-15 middleware** — `_validate_host_header` | `dz_erp/api.py` | `dz_erp/api.py` | **Medium** — R-2: must also set `ERP_ALLOWED_HOSTS` in deployment | `test_phase59`; full API suite; desktop embedded-service smoke (loopback must still pass) |
| 3 | **SEC-13 password policy** — constants, `PasswordPolicyError`, `configured_password_min_length`, `validate_password_policy`, `_stored_iterations`, rehash-on-login, `reset_password_admin_secure` | `dz_erp/auth.py` (+225) | `dz_erp/auth.py` | Medium — R-4: 2 test fixtures need 8+ chars | `test_phase59`; `test_phase7_authentication_remediation.py` (patched); auth/authz subset (147 tests) |
| 4 | **`review_policy.py`** (prerequisite for #5) | `dz_erp/review_policy.py` | new file | **Low — verified**: it imports only `decimal_utils` and two constants from `intake_ocr`, both of which the workspace already has. I executed the import against the workspace's `intake_ocr.py` and it succeeds. | `test_phase29_ocr_review_policy.py` |
| 5 | **SEC-14 OCR override validation** | `dz_erp/intake_ocr.py` (+30) | `dz_erp/intake_ocr.py` | **Medium** (was High) — the hunk depends only on `review_policy`, which imports cleanly here. `rule_for`/`validate_corrected_value` exercised successfully against the workspace tree. | `test_phase59`; OCR/workflow suite |
| 6 | **Supplier invoice metadata validation** — `_require_supplier_invoice_no` (max 128), `_require_valid_payment_method`, `_parse_invoice_date` | `dz_erp/procurement_stock.py` | `dz_erp/procurement_stock.py` | Medium — R-5 if done as a whole-file swap | invoice lifecycle suite; desktop invoice view tests |
| 7 | **Preserve the untracked workspace** | — | — | **CRITICAL (R-1)** | commit or archive before any other step |

## P1 — SHOULD INTEGRATE

| # | Change | Source | Destination | Risk | Required tests |
| --- | --- | --- | --- | --- | --- |
| 8 | **Full `procurement_stock.py` lifecycle** — totals/lines validation, duplicate errors, source mismatch, price variance, reversal, revalidation | `dz_erp/procurement_stock.py` (3,583) | replace workspace's 1,799 | **High (R-5)** | full suite + all desktop suites; expect API adjustments |
| 9 | **Backup failure-mode tests** (disk-full, restore failure, permission denied) | `tests/test_phase60_*.py` | `tests/` | Low | those 9 tests |
| 10 | **Security selective-integration tests** | `tests/test_phase58_*.py` (55) | `tests/` | Low | those 55 |
| 11 | **SEC-13/14/15 acceptance tests** | `tests/test_phase59_*.py` (39) | `tests/` | Low | those 39 |
| 12 | **JSON request-size ceiling** (2 MiB) | `dz_erp/api.py` | `dz_erp/api.py` | Low-Med | upload boundary suite |
| 13 | **`ops/erp.env.example`** documentation of 6 new vars | `ops/erp.env.example` | same | Low | none (doc) |
| 14 | **Rate limiting** (`rate_limit.py`) | `dz_erp/rate_limit.py` | new file | Medium | `test_phase35`, `test_phase43`, `test_phase39` skipped-until-present |
| 15 | **Request-id boundary** (`request_id.py`) | `dz_erp/request_id.py` | new file | Low | `test_phase47_request_id_boundary.py` |
| 16 | **Carry workspace's `_pdfplumber_model_version` the other way** | workspace `intake_ocr.py` | PR #7 | Low | provenance test |

## P2 — REQUIRES EXPERT REVIEW

| # | Change | Source | Why it needs a human | Required tests |
| --- | --- | --- | --- | --- |
| 17 | **DR generation layer** — `restore_generation.py`, `generation_resolver.py`, startup recovery gate | PR #7 | 7+ commits of repeated fail-closed tuning; a startup gate that misjudges refuses to boot. Must be accepted as a subsystem, not a patch | `test_phase56_dr_foundations`, `test_phase55`, `test_phase48`; a manual drill |
| 18 | **Remote backup storage** — `backup_storage.py`, `backup_storage_s3.py` | PR #7 | Introduces an external dependency and credential path (`ERP_BACKUP_ENCRYPTION_KEY`); needs an ops decision | `test_phase56_remote_storage` |
| 19 | **Alerting / DR metrics** — `alerting.py`, `dr_metrics.py` | PR #7 | Operational integration; no product decision recorded | `test_phase56` |
| 20 | **Statutory accounting rules** — timbre provenance, VAT configuration | both | **No authorised Algerian expert-comptable sign-off exists** (Phase 32: 12 questions pending) | cannot be tested into existence |

## ALREADY PRESENT — no action

| # | Item | Evidence |
| --- | --- | --- |
| 21 | `require_role_secure` role gate on upload | Workspace has an identical function (AST-compared; only docstring differs) |
| 22 | Non-finite/oversized decimal refusal | Workspace `_guarded`, same `MAX_ABSOLUTE_EXPONENT = 18` |
| 23 | Hash-chained versioned audit | Both branches; workspace verified 25/25 in Phase 30 restore drill |
| 24 | Key rotation / key provider | Present in the workspace's `dz_erp/key_rotation.py`; `2427fda` does not modify these |
| 25 | Pinned, hash-locked dependencies, 0 advisories | Both, 53 pins each |
| 26 | Restore interpreter guard | Both, same message and policy |
| 27 | SCF chart bootstrap | Identical |

## DO NOT INTEGRATE

| # | Item | Reason |
| --- | --- | --- |
| 28 | **PR #7's `dz_erp/desktop/ui.py` (the 1,277-line monolith)** | Superseded by the workspace's redesign; adopting it would delete the entire Phase 29–30 desktop programme, dark theme, RTL work and accessibility fixes |
| 29 | **`#b00020` hardcoded error labels** | The exact defects D-1/D-2 identified in Phase 32 |
| 30 | **Blanket replacement of `dz_erp/`** | Would discard 11 workspace-only modules and 6 workspace-only test files |
| 31 | **Relaxing the restore guard to make 3.11.2 pass** | The guard is a security control; the 20 failures are correct |
| 32 | **The 75 documentation files from `2427fda`** as "evidence" | 83% of the commit; superseded by this phase's evidence, which was produced by execution rather than assertion |

## Priority summary

```
P0  (7 items)  SEC-13, SEC-14, SEC-15, review_policy, invoice metadata validation, preserve tree
P1  (9 items)  accounting lifecycle, 103 security tests, rate limit, request-id, env docs
P2  (4 items)  DR layer, remote storage, alerting, statutory sign-off
---  (7 items)  ALREADY PRESENT
---  (5 items)  DO NOT INTEGRATE
```

## Ordering constraint

Items 1–7 are not independent. The dependency chain is:

```
review_policy.py  ──►  SEC-14 (intake_ocr)      [verified portable: imports OK]
SEC-15 net.py     ──►  SEC-15 middleware (api.py)
SEC-13 auth.py    ──►  test fixture password fix (2 lines)
all of the above  ──►  procurement_stock validation (they raise the errors it validates)
```

`RECOMMENDED_INTEGRATION_SEQUENCE.md` orders them accordingly.
