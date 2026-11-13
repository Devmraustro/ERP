# Security Remediation Log — ERP Algeria

Assessment date: 2026-11-09 · Branch `arena/7557bcfb-erp` · HEAD
`4fc1364a9504fab1153ea44e753f354f00023428` (no commits created)

Every change below is on the working tree only. Nothing was committed, pushed or
merged, because the brief authorises changes but requires explicit authorization
for commits/pushes.

## 1. Ground rules applied

* Reproduce first, then change; the reproduction is in
  `SECURITY_TEST_EVIDENCE.md`.
* Smallest change at the narrowest chokepoint that closes the defect.
* No architecture change, no public-API removal, no test weakened, skipped or
  given a relaxed assertion, no security control disabled.
* No accounting or tax formula modified. Where a value had to be derived
  (`line_ht_dzd`), the *existing* expression was factored into one helper and
  reused verbatim.
* Rollback safety: each change is additive and independent; reverting any single
  hunk restores the previous behaviour with no data migration and no schema
  change. No database schema, key format, HMAC input or payload-hash input for
  *existing* rows was altered.

## 2. Change 1 — Server-side role enforcement for document intake (SEC-01)

**Files** `dz_erp/security_audit.py`, `dz_erp/api.py`

`dz_erp/security_audit.py:664–714` — new `require_role_secure()`:

```python
def require_role_secure(
    db: ERPDatabase,
    requesting_user_id: int,
    allowed_roles: Tuple[str, ...],
    *,
    action: str,
    target_model: str = "api",
    target_id: str = "-",
) -> Dict[str, Any]:
    ...
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (requesting_user_id,)).fetchone()
        if not user or not user["is_active"]:
            raise AuthorizationError("User is inactive or revoked.")
        if user["role"] not in allowed_roles:
            db.append_audit_event(
                conn=conn,
                company_id=int(user["company_id"]),
                actor_user_id=requesting_user_id,
                action_type="SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED",
                target_model=target_model,
                target_id=str(target_id),
                after_state={"role": user["role"], "action": action,
                             "required_roles": list(allowed_roles)},
            )
            denial = AuthorizationError(...)
    if denial is not None:
        raise denial
    return dict(user)
```

Design notes:

* The role is read from the `users` row bound to the verified credential — never
  from a header, query parameter or body field (the same rule the read paths
  already follow).
* The denial is audited *inside* the transaction and raised *after* commit, so
  the forensic record survives; this mirrors `_require_user_admin` and the
  `SECURITY_*_BLOCKED` events already used by the read wrappers. It also means a
  refused attempt is attributed to the caller's own tenant chain, not the
  target's.
* `dz_erp/api.py:153` adds the documented matrix as a constant:

```python
DOCUMENT_INTAKE_ROLES = ("clerk", "approver", "accountant", "admin")
```

* `dz_erp/api.py:450–470` (upload) and `dz_erp/api.py:557–578` (field
  correction) call the guard **before** the upload body is read / before the
  field write, map `AuthorizationError` to HTTP 403, and log a structured
  `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED` line through the redacting logger.

**Why API-level and not the service layer**: the matrix in
`docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md` is defined at the operation
boundary, and `upload_and_process_supplier_document` /
`update_extracted_field_by_human` are also called directly by internal tooling
and by existing phase-3/6/18 tests with fixture users. Enforcing at the boundary
closes the HTTP exposure without changing those internal contracts or breaking
their semantics. Cross-tenant checks remain where they were, in both layers
(defence in depth).

**State-changing risk** — none to stored data: read-only check plus an audit
append. Rollback = delete the two `try` blocks and the helper.

## 3. Change 2 — Finite/range guard on decimal conversion (SEC-02)

**File** `dz_erp/decimal_utils.py:21–75`

```python
MAX_ABSOLUTE_EXPONENT = 18

def _guarded(dec: Decimal, original: object) -> Decimal:
    if not dec.is_finite():
        raise ValueError(f"Non-finite decimal input is not permitted: {original!r}")
    if dec != 0 and dec.adjusted() > MAX_ABSOLUTE_EXPONENT:
        raise ValueError(...)
    return dec
```

Applied on the `Decimal`, `int` and `str` branches of `to_decimal`, and `bool`
is rejected before the `int` branch (it is an `int` subclass and previously
became `1.00`).

* `ValueError` is already the documented contract of `to_decimal` ("Invalid
  decimal string"), and both consumers already map it: `POST /api/v1/invoices`
  → 422 (`dz_erp/api.py:609–610`), review/decision services → the documented
  `FIELD_VALUE_INVALID_*` refusal. No new exception type was introduced.
* The bound (10^18) is far above any real DZD amount or quantity (Algeria's
  annual GDP ≈ 10^16 DZD) and below the 28-digit decimal context where
  `quantize` starts raising `InvalidOperation`, so legitimate values are
  unaffected (`1e17` still converts; see
  `test_to_decimal_still_accepts_real_amounts`).
* No stored value is rewritten; existing rows keep their strings, and
  verification of an existing `'NaN'` row (were one present from before the
  fix) would now raise `ValueError` instead of silently comparing — the correct
  fail-closed direction.

## 4. Change 3 — Request-contract errors instead of unhandled 500s (SEC-03/04/05/06)

**Files** `dz_erp/api.py`, `dz_erp/procurement_stock.py`

| Defect | Change | Location |
| :--- | :--- | :--- |
| Out-of-range `limit` on `GET /api/v1/invoices` → 500 | catch `ValueError` → 422 | `dz_erp/api.py:831–835` |
| `payment_method` reached the schema CHECK | `VALID_PAYMENT_METHODS` pre-check → `ValueError` (422) | `dz_erp/procurement_stock.py:18`, `:455–458` |
| `invoice_date` unvalidated, internal text leaked | `datetime.strptime(..., "%Y-%m-%d")`; `fiscal_year = invoice_day.year`; neutral message | `dz_erp/procurement_stock.py:459–466` |
| empty / oversized reference accepted | non-empty and ≤ `MAX_SUPPLIER_INVOICE_NO_LENGTH = 128` | `dz_erp/procurement_stock.py:19`, `:449–454` |
| omitted `line_ht_dzd` → `TypeError` | `compute_line_ht_dzd()` + `_materialise_line_ht()` before hashing | `dz_erp/procurement_stock.py:22–52`, `:468`, `:549` |

`compute_line_ht_dzd` is the pre-existing expression moved, not changed:

```python
# before (inline, twice)
expected_line_ht = round_dzd(qty * pu * ((Decimal("100.00") - disc) / Decimal("100.00")))
# after (one definition, used by the payload hash and the validation loop)
expected = round_dzd(qty_dec * pu_dec * ((Decimal("100.00") - disc_dec) / Decimal("100.00")))
```

Validation now happens **before** the payload hash and before any write, so a
rejected request leaves no partial row (verified: `supplier_invoices` count
unchanged for every rejected case in the regression suite).

**Rollback** — remove the pre-checks; `_materialise_line_ht` is the only caller
of the new helper.

## 5. Change 4 — Backup archive permissions (SEC-07)

**File** `dz_erp/security_audit.py:944–959`

```python
fd = os.open(str(output_backup_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "wb") as handle:
    handle.write(encrypted_archive)
try:
    os.chmod(output_backup_path, 0o600)
except OSError:  # pragma: no cover - non-POSIX filesystems
    pass
```

Creating at `open(2)` time removes the window in which a `chmod`-after-write
archive would be world-readable. The existing `finally: shutil.rmtree(tmp_dir)`
cleanup and the manifest format are unchanged, so restore compatibility is
untouched.

## 6. Change 5 — Request-body bound and browser hardening headers (SEC-08/10)

**File** `dz_erp/api.py:162–185`, `:231–261`

* `DEFAULT_MAX_JSON_REQUEST_BYTES = 2 MiB`; the upload path is bounded by
  `configured_max_upload_bytes() + 1 MiB` (multipart framing allowance);
  `ERP_MAX_REQUEST_BYTES` overrides the JSON cap, and a malformed/non-positive
  override is ignored rather than disabling the limit (same defensive pattern as
  `configured_max_upload_bytes`).
* The check runs in the middleware before `call_next`, so the router never binds
  or parses the body, and returns 413 with `X-Content-Type-Options: nosniff`.
* Headers added: `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`, `X-Frame-Options: DENY` on every response; `Cache-Control:
  no-store` when an `Authorization` header is present.

**Rollback** — delete the middleware; no other module depends on it.

## 7. Change 6 — Username validation (SEC-09)

**File** `dz_erp/api.py:990–1001` — reject `>64` characters and any C0/DEL
control character, returning 422. Existing usernames in the database are
untouched (no migration, no re-validation of stored rows).

## 8. Regression tests added

`tests/test_phase34_security_validation.py` — 51 tests (22.5 KB, 525 lines),
no Qt/OCR-model dependency, ~4 s runtime (`51 passed in 3.90s`).

| Finding | Tests |
| :--- | :--- |
| SEC-01 | `test_upload_is_refused_for_roles_the_rbac_matrix_denies[wh_a/auditor_a/ocr_a]`, `test_upload_is_allowed_for_roles_the_rbac_matrix_permits[clerk_a/approver_a/acct_a/admin_a]`, `test_field_correction_is_refused_for_denied_roles[wh_a/auditor_a/ocr_a]`, `test_field_correction_is_allowed_for_clerk_and_audited`, `test_document_reads_remain_tenant_isolated` |
| SEC-02 | `test_non_finite_or_oversized_amounts_are_rejected_without_a_write` (6 params), `test_to_decimal_refuses_non_finite_and_out_of_range_values` (6 params), `test_to_decimal_still_accepts_real_amounts`, `test_bool_is_not_a_decimal` |
| SEC-03 | `test_invoice_list_rejects_out_of_range_limit_with_422` (4 params), `test_invoice_list_accepts_the_documented_boundaries` |
| SEC-04 | `test_unsupported_payment_method_is_a_422_not_a_500` |
| SEC-05 | `test_line_without_declared_net_is_derived_not_a_500` |
| SEC-06 | `test_invalid_invoice_date_is_rejected_without_leaking_internals` (5 params), `test_invalid_supplier_reference_is_rejected` (3 params), `test_sql_metacharacters_are_stored_as_data`, `test_mass_assignment_fields_are_ignored` |
| SEC-07 | `test_backup_archive_is_owner_only` |
| SEC-08 | `test_oversized_json_body_is_refused_before_authentication`, `test_oversized_authenticated_json_body_is_refused` |
| SEC-09 | `test_username_with_control_characters_is_rejected` |
| SEC-10 | `test_security_headers_are_present` |
| controls | `test_api_documentation_is_disabled_by_default`, `test_no_cors_wildcard_is_served`, `test_spoofed_identity_headers_do_not_authenticate` |

Each denial test asserts both the status code **and** the absence of the side
effect (no `source_documents` row, unchanged `human_override_value`, no
`supplier_invoices` row), so a future "fix" that merely changes the response
cannot pass.

No existing test was edited. `git diff --stat` confirms only source files were
modified:

```
 dz_erp/api.py               | 122 ++++++++++++++++++++++++++++++++++++++++
 dz_erp/decimal_utils.py     |  37 +++++++++++++--
 dz_erp/procurement_stock.py |  67 +++++++++++++++++++++++-
 dz_erp/security_audit.py    |  67 +++++++++++++++++++++++-
 4 files changed, 287 insertions(+), 6 deletions(-)
 (plus new file tests/test_phase34_security_validation.py)
```

## 9. Verification after remediation

| Check | Command | Result |
| :--- | :--- | :--- |
| New tests | `.venv/bin/python -m pytest tests/test_phase34_security_validation.py -q` | 51 passed, exit 0 |
| Full suite | `.venv/bin/python -m pytest -q --junitxml=/tmp/postfix_junit.xml` | 20 failed, 722 passed, 6 skipped, exit 1 — failure set **identical** to the pre-fix baseline |
| Ruff | `.venv/bin/python -m ruff check dz_erp tests tools` | All checks passed, exit 0 |
| Compile | `.venv/bin/python -m compileall -q dz_erp tests tools` | exit 0 |
| Dependency graph | `.venv/bin/python tools/verify_dependency_graph.py` | OK, exit 0 |
| Hash lock | `.venv/bin/python tools/generate_hash_lock.py --check` | OK (51 pins / 1,277 hashes), exit 0 |
| Probe re-run | `/tmp/probe/probe_rbac.py`, `probe_input.py`, `probe_lines.py`, `probe_storage.py`, `probe_body.py` | every SEC-01…SEC-10 symptom no longer reproduces |

## 10. Explicitly not remediated

| Item | Reason |
| :--- | :--- |
| SEC-11 vulnerable dependencies | Read-only workstream; upgrade requires hash-lock regeneration and a full OCR regression pass (documented blocker) |
| SEC-12 rate limiting | Deployment/architecture control; already documented as a proxy responsibility |
| SEC-13 password blocklist | Policy decision that changes provisioning behaviour |
| SEC-14 field-value semantics | Belongs to the unmerged review-binding design (PRs #3–#6); duplicating it here would fork the design |
| SEC-15 TLS / Host validation | Deployment requirement; the app is designed to sit behind a terminating proxy |
| 20 restore-path test failures | Environment: Python 3.11.2 below the project floor; the guard is correct and must not be weakened to "make tests pass" |
