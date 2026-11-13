# Phase 36A — Integration matrix

Baseline for every comparison in this document is the working tree at
`arena/7557bcfb-erp` / `4fc1364`, branch HEAD, with all 16 modified + 266 untracked files in place.

## 1. Repository topology (verified, not assumed)

| Ref | Commit | vs `main` | PR |
| --- | --- | --- | --- |
| `main` | `4fc1364` | — | — |
| `phase29/ocr-failclosed` | `2427fda` | +1 | #3 |
| `fix/phase29-source-invoice-consistency` | | +6 | #4 |
| `fix/phase29-invoice-line-price-consistency` | | +7 | #5 |
| `fix/phase29-invoice-discount-and-line-net-consistency` | | +8 | #6 |
| `arena/7f12d627-erp` | `f603e38` | +108 | #7 (**DRAFT**) |

All four lower branches are strict ancestors of `f603e38`. PR #7's head commit is therefore a
**superset** of every branch beneath it, which is why exporting `f603e38` is a valid stand-in for
"the PR #7 content" in every comparison below.

## 2. Changed-file classification

Working tree: **16 modified, 266 untracked, 0 staged.**

| Category | Files | Notes |
| --- | --- | --- |
| **Core ERP logic** (modified) | `dz_erp/{api,decimal_utils,intake_ocr,procurement_stock,security_audit}.py` | 5 files, all in the PR #7 overlap set |
| **Desktop UI** (modified) | `dz_erp/desktop/{app,config,i18n,ui}.py` | 4 files, PR #7 does not touch these paths' logic |
| **Packaging** (modified) | `pyproject.toml`, `requirements.lock{.txt,.hashes.txt}` | version pins |
| **Tests** (modified) | `tests/test_phase{19,26,7,8}_*.py` | 4 files |
| **Ops modules** (untracked) | `dz_erp/{ops_alerts,ops_backup,ops_checks,readiness}.py` | new backend services |
| **Ops units** (untracked) | `ops/dz-erp-{backup,backup-verify,disk-check,ocr-check,readiness}.{service,timer}`, `ops/logrotate/` | systemd deployment |
| **Desktop modules** (untracked) | 11 files under `dz_erp/desktop/` | e.g. first-run, restore, focus |
| **Release evidence** (untracked) | `docs/release/phase{29..35}/`, `docs/security/` | read-only evidence, never rewritten |
| **New tests** (untracked) | `tests/test_phase{29_desktop_redesign,34_security_validation,62_backup_automation,62_readiness,63_dependency_contract,64_dependency_security_regression}.py` | 6 files |

## 3. File overlap with PR #7 — **11 files, not 5**

Phase 34 recorded five overlapping files. Measured against `git diff --name-only <merge-base> f603e38`
(185 files changed by PR #7), the true intersection with the 16 workspace-modified files is:

| File | WS lines | PR7 lines | main lines | Verdict |
| --- | --- | --- | --- | --- |
| `dz_erp/api.py` | 1090 | 1640 | 927 | PR #7 adds SEC-13/14/15 endpoints + rate limit |
| `dz_erp/decimal_utils.py` | 92 | 98 | 61 | **No conflict — same logic, renamed helper** |
| `dz_erp/intake_ocr.py` | 1612 | 2365 | 1588 | PR #7 adds review-decision tables/wiring |
| `dz_erp/procurement_stock.py` | 1799→2085 | 3583 | 1799 | 22 functions; see PHASE36B |
| `dz_erp/security_audit.py` | 1310 | 1682 | 1245 | PR #7 adds rate-limit + request-id audit |
| `pyproject.toml` | — | — | — | lock/version pins |
| `requirements.lock.txt` | — | — | — | pins |
| `requirements.lock.hashes.txt` | — | — | — | pins |
| `tests/test_phase19_dependency_integrity.py` | — | — | — | test expectations |
| `tests/test_phase7_authentication_remediation.py` | — | — | — | test expectations |
| `tests/test_phase8_upload_boundary_and_ci.py` | — | — | — | test expectations |

`procurement_stock.py` is counted once; it is both in "core ERP" and the overlap set.

## 4. The five overlapping files Phase 34 named

### 4.1 `require_role_secure` optional-user-param concern — **REFUTED at runtime**

Phase 34 asked whether PR #7's `require_role_secure` might accept an optional user parameter that
would weaken authorization. Signature comparison:

```python
# workspace, security_audit.py:664          # PR #7, security_audit.py
def require_role_secure(                     def require_role_secure(
    db: ERPDatabase,                             db: ERPDatabase,
    requesting_user_id: int,                     requesting_user_id: int,   # required, positional
    allowed_roles: Tuple[str, ...],              allowed_roles: Tuple[str, ...],
    *,                                           *,
    action: str,                                 action: str,
    target_model: str = "api",                   target_model: str = "api",
    target_id: str = "-",                        target_id: str = "-",
) -> Dict[str, Any]:                         ) -> Dict[str, Any]:
```

**Identical. There is no optional user parameter in either version.** The role is read from the
`users` row bound to the verified credential in both, never from the request.

The mission required verifying runtime behaviour rather than trusting the signature. Executed against
a disposable database:

```
=== require_role_secure runtime behaviour (workspace) ===
  clerk may upload                   ALLOWED (returned user clerk1)
  warehouse may NOT upload           DENIED  AuthorizationError
  admin may upload                   ALLOWED (returned user admin)
```

The DENY path is live: `warehouse` — a role the RBAC matrix explicitly denies — is refused with
`AuthorizationError`. **No authorization weakening. Gate PASS.**

### 4.2 `decimal_utils.py` — renamed helper, no semantic conflict

PR #7 renames `_guarded` → `_require_supported_decimal`; the workspace keeps `_guarded`. Executed
behaviour on the workspace:

```
  NaN        REFUSED  ValueError: Non-finite decimal input is not permitted
  sNaN       REFUSED  ValueError: Non-finite decimal input is not permitted
  Infinity   REFUSED  ValueError: Non-finite decimal input is not permitted
  1E+19      REFUSED  ValueError: Decimal input magnitude exceeds the supported range
  1E+18      ACCEPTED -> 1E+18
  1.5 (float) REFUSED  FloatMonetaryError: Binary float is prohibited
MAX_ABSOLUTE_EXPONENT = 18
```

Same bounds, same exception types, same constant. **No integration work required.**

### 4.3–4.5 `api.py`, `intake_ocr.py`, `procurement_stock.py`

PR #7 is a strict superset in each. Integration is additive and is tracked in
`PHASE36B` (function inventory) and the Phase 37 security report.

## 5. Targeted regression tests written this phase

`tests/test_phase35_integrity_gates.py` — 16 tests, all passing:

| Area | Tests |
| --- | --- |
| `_validate_invoice_totals` | coherent accept, HT mismatch, negative header, wrong cash stamp duty, TTC mismatch |
| `_duplicate_invoice_errors` | repeated ref+amount reported, new ref quiet |
| `_invalidate_approval_requests` | open request becomes `INVALIDATED` |
| `_record_refused_attempt` | refusal leaves an audit event |
| inline validators | 5 bad dates, oversized ref, unsupported payment method |

## 6. Integration result

| Item | Outcome |
| --- | --- |
| `_RefusedAttempt` + 6 gates added to `procurement_stock.py` | **INTEGRATED** |
| `_source_review_fingerprint` | **REJECTED** — needs `ocr_field_reviews` table absent from workspace schema |
| Full suite before | 22 failed / 839 passed / 3 skipped |
| Full suite after | **22 failed / 855 passed / 3 skipped** |
| New failures introduced | **0** (failure set byte-identical to baseline) |
| New tests added | 16, all passing |

The 22 pre-existing failures are the documented Phase 30 guard suite (they fail because this sandbox
runs Python 3.11.2, which `pyproject.toml` declares unsupported; the guard correctly refuses). They
are **not** caused by integration and must not be "fixed" by relaxing the guard.
