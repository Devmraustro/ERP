# ERP Algeria — Phase 2: Selective Security Integration Report

**Document:** `docs/security/SECURITY_SELECTIVE_INTEGRATION_REPORT.md`
**Prepared:** 2026-11-09
**Integration workspace:** disposable clone `/tmp/pr7work`, detached at `5a3e47ddfba2b789bcff71d6438db4927f5c7986`
**Remediation workspace (untouched):** `arena/7557bcfb-erp` @ `4fc1364a9504fab1153ea44e753f354f00023428`
**Decision:** **SELECTIVE INTEGRATION PARTIAL — BLOCKERS REMAIN** (§12)

---

## 0. Executive summary

The security remediation was **not** merged. Nine confirmed-missing findings were ported to PR #7's code by hand, one fix at a time, and each one is covered by new regression tests whose discrimination was measured against the unmodified target.

| | Result |
|---|---|
| Findings integrated | **9** (SEC-01 upload half, SEC-02 remainder, SEC-03, SEC-04, SEC-05, SEC-06, SEC-07, SEC-08, SEC-09, SEC-10) |
| Findings deliberately **not** ported | SEC-11, SEC-12 (already fixed upstream — verified), SEC-13/SEC-14/SEC-15 (policy/deployment, out of scope) |
| Files changed in the target | **5 tracked + 1 new test module** (+391 / −16) |
| New regression tests | **92 cases from 55 test functions**, 91 pass / 1 skipped |
| Remediation's own 51 tests on the integrated tree | **51 passed, 0 failed** (was 27 failed / 24 passed on the unmodified target) |
| Full suite, unmodified target | 20 failed / **1204 passed** / 9 skipped (1233 collected) |
| Full suite, integrated target | 20 failed / **1295 passed** / 10 skipped (1325 collected) — **identical failure set, zero new failures** |
| Regressions introduced | **none** (one was introduced mid-work — an undocumented env var — and closed before the final run) |
| Cannot be closed here | interpreter floor (20 tests), wheel build (pre-existing), commit/push (not authorised) |

The one thing that changed since the reconciliation audit: the 27 upstream test modules previously "unavailable locally" **were available in this phase** (full PR #7 tree) and **all ran**. They are no longer a blocker.

---

## 1. Source of truth verified before any change

### 1.1 Target verification (STOP condition checked first)

```
git ls-remote https://github.com/Devmraustro/ERP refs/heads/arena/7f12d627-erp
5a3e47ddfba2b789bcff71d6438db4927f5c7986    refs/heads/arena/7f12d627-erp
4fc1364a9504fab1153ea44e753f354f00023428    refs/heads/main
```

The authoritative remote head of `arena/7f12d627-erp` **equals** the SHA in the brief. Proceed (no reconstruction from an approximate SHA, no assumption that local `main` is equivalent — `main` is `4fc1364a`, which is 106 commits behind).

Isolation used, in full:

| Workspace | Path | State | Purpose |
|---|---|---|---|
| Remediation (source of truth) | `/home/user/ERP` | branch `arena/7557bcfb-erp` @ `4fc1364a`, 4 M + 2 ??, **byte-identical before and after** (§13) | untouched |
| Target clone | `/tmp/pr7work` | detached @ `5a3e47dd`, no commits, 5 M + 1 ?? | integration |
| Target baseline worktree | `/tmp/pristine` | detached @ `5a3e47dd`, adds only a measurement shim | A/B evidence |

No fetch, checkout, reset, clean, stash, rebase, merge or cherry-pick was performed in `/home/user/ERP`; nothing was committed or pushed anywhere.

### 1.2 Environment

| Item | Value |
|---|---|
| Interpreter available | CPython **3.11.2** |
| Project floor (`pyproject.toml`, identical in both trees) | `>=3.11.17,<3.13` excluding 3.12.0–3.12.9 |
| Attempt to obtain a supported interpreter | `python-build-standalone` release asset for CPython 3.12.16 → **download blocked** (`release-assets.githubusercontent.com` is not an allowlisted host). No apt/pyenv/uv/conda available. |
| Consequence | 20 restore/backup tests **cannot** run (`fail-closed` interpreter gate). This is stated as *blocked*, never as pass, and the fail-closed gate was **not** lowered. |
| Runtime used | `/tmp/ivenv`, installed from the target's own `requirements.lock.txt` (`--no-deps`): fastapi 0.133.0, starlette 1.3.1, pytest 9.0.3 |

---

## 2. TASK A — Fix-by-fix integration matrix (SEC-01 … SEC-15)

Legend for the **Class** column: `UPSTREAM` = already fixed upstream · `MISSING` = absent upstream · `PARTIAL` = upstream fixed only part of it · `ENV` = requires an environment/decision, not a code port · `N-A` = not applicable to this integration.

| ID | Class (verified) | Upstream implementation (file · symbol) | Remediation implementation | Existing upstream regression tests | New tests (this phase) | Public interface change | Accounting / inventory change | Action taken |
|---|---|---|---|---|---|---|---|---|
| **SEC-01**<br>intake RBAC | **PARTIAL** → integrated | upload: `dz_erp/api.py` · `api_upload_document` (L585) — **no role check**, only tenant verification in `intake_ocr.upload_and_process_supplier_document`.<br>field edit: `dz_erp/intake_ocr.py` · `update_extracted_field_by_human` L1593, matrix `REVIEW_CAPABLE_ROLES` L188, check L1620 | `security_audit.require_role_secure` + endpoint gates (`api_upload_document`, `api_edit_extracted_field`) | field-edit denial: `tests/test_phase29_ocr_review_policy.py` (warehouse cannot review); **upload: none** (`test_phase8_upload_boundary_and_ci.py` covers size/type only) | `test_roles_the_matrix_permits_can_upload` (4), `test_roles_the_matrix_denies_cannot_upload` (4), `test_refused_upload_creates_no_document_row` (2), `test_refused_upload_leaves_no_orphaned_vault_file` (2), `test_refused_upload_is_audited`, `test_upload_requires_authentication`, `test_spoofed_identity_headers_do_not_authorize_upload` (4), `test_field_review_guard_is_unchanged_upstream` | **No** — same route, same multipart contract; only a 403 on previously-200 requests | **No** | **Upload half integrated**; field-edit half deliberately left to upstream's single enforcement point |
| **SEC-02**<br>decimal domain | **PARTIAL** → remainder integrated | `dz_erp/decimal_utils.py` · `_require_finite` L21–34 (non-finite only); `to_decimal` L36. **Gap proven:** `to_decimal(10**19)` was accepted (int path unguarded) and `format_dzd("1e400")` raised `decimal.InvalidOperation` → unhandled 500; `to_decimal(True)` → `1.00` | `decimal_utils._guarded` + `MAX_ABSOLUTE_EXPONENT` + bool rejection | `tests/test_phase32_cross_phase_integration_audit.py` (NaN/Infinity round-trip); remediation's 51-test module (10 of its cases) | `test_unusable_line_ht_is_a_422_not_a_500` (8 params), `test_derivation_rounds_like_the_validator`, plus the reference module's SEC-02 cases (all green) | **Behavioural**: `to_decimal` now raises `ValueError`/`TypeError` for out-of-range and boolean inputs (documented contract of the module) | **No** — only refuses values that could never be money | **Gap closed inside the existing single guard** (`_require_finite` → `_require_supported_decimal`); **no parallel helper, no duplicate logic** |
| **SEC-03**<br>list `limit` | **MISSING** → integrated | `api.py` · `api_list_invoices` L1032 — the secure lister clamps 1…500 by raising `ValueError`, which escaped as 500 | `except ValueError → 422` in `api_list_invoices` | `tests/test_phase25_read_api.py` (accounts endpoint answers 422 — the idiom this matches) | `test_invoice_list_rejects_out_of_range_limit_with_422` (4), `test_invoice_list_accepts_the_documented_boundaries` (2) | **No** (400-class refinement only) | No | Integrated |
| **SEC-04**<br>payment method | **MISSING** → integrated | `procurement_stock._create_and_validate_supplier_invoice` L1139 — value reached the `CHECK` constraint → `sqlite3.IntegrityError` → 500 | `VALID_PAYMENT_METHODS` + pre-write validation | none (no upstream test sends an unsupported method) | `test_unsupported_payment_method_is_a_422_not_a_500`, `test_every_supported_payment_method_is_accepted` (3) | **No** | No — allowlist mirrors the schema constraint exactly | Integrated |
| **SEC-05**<br>payload digest | **PARTIAL** → remainder integrated | `procurement_stock.py:539` uses `round_dzd(line.get("line_ht_dzd", expected_line_ht))`, **but** `compute_invoice_payload_hash` L69 still formats `line["line_ht_dzd"]` (and L2416/L3159 subscript it) → omitted amount became `KeyError` instead of `TypeError` | `compute_line_ht_dzd` + `_materialise_line_ht` | none | `test_omitted_line_ht_is_derived_not_a_500`, `test_explicit_null_line_ht_is_derived_the_same_way`, `test_omitted_and_explicit_payloads_produce_the_same_digest`, `test_mismatched_line_ht_still_reaches_validation`, `test_discount_is_applied_before_derivation`, `test_missing_qty_is_a_request_error`, `test_refused_invoice_leaves_no_accounting_or_stock_effect`, `test_correction_path_validates_the_date_and_derives_a_missing_line_ht` | **No** (request model unchanged; `line_ht_dzd` stays optional) | **Yes, deliberately**: the digest of a payload that omits the line net now equals the digest of the same payload stating the derived value. No existing invoice's stored hash changes (the function only fills an input that previously could not succeed) | Integrated: materialisation runs **before** the hash, in **both** the create and the revalidate paths |
| **SEC-06**<br>date / reference | **MISSING** → integrated | `procurement_stock.py` · `fiscal_year = int(invoice_date[:4])` at L1161 (create) and L1452 (revalidate); no reference validation | `_parse_invoice_date`, `_require_supplier_invoice_no` | none | `test_invalid_invoice_date_is_a_422_without_internal_text` (5), `test_invalid_supplier_reference_is_a_422` (3), `test_maximum_reference_length_is_accepted`, `test_fiscal_year_still_comes_from_the_invoice_date`, `test_correction_path_validates_the_date_and_derives_a_missing_line_ht` | **No** | **Yes, but invariant-preserving**: `fiscal_year` is still the calendar year of the invoice date (asserted); only unparseable dates stop producing 500s. No tax rule, no amount, no rounding touched | Integrated in both paths |
| **SEC-07**<br>backup custody | **MISSING** → integrated (**and extended**: new sub-finding) | `security_audit.py` · `create_encrypted_backup` L974; archive written with `write_bytes` L1054 → process umask (0644). **New finding, verified:** the staging directory `.tmp_backup_*` held the **plaintext** SQLite snapshot and a `manifest.json` containing `vault_encryption_key_b64`, `audit_hmac_secret_b64` and both key rings, all created with the process umask | `os.open(0o600)` / `chmod` on the archive | `assert_owner_only_permissions` fixture exists and is applied to **key files** (`test_phase8`, `test_phase10`, `test_phase13`, `test_phase9`) — the archive was never covered | `test_backup_archive_is_owner_only`, `test_backup_leaves_no_staging_directory`, `test_backup_publishes_exactly_one_file`, `test_staging_directory_and_manifest_are_private`, `test_failed_backup_writes_nothing_and_cleans_up`, `test_backup_round_trips_and_verifies_the_chain` (skipped: interpreter gate) | **No** | No | Integrated via the project's existing `atomic_write_bytes` (mkstemp 0600 → fsync → `os.replace` → dir fsync → cleanup), staging dir 0700, manifest 0600 |
| **SEC-08**<br>request body | **MISSING** → integrated | `api.py` — no `Content-Length` handling anywhere (`rate_limit.py` and `api.py` grepped: zero matches) | `_security_headers` middleware + `_configured_max_request_bytes` | none | `test_body_below_the_limit_is_processed`, `test_body_exactly_at_the_limit_is_not_refused`, `test_body_above_the_limit_is_refused`, `test_oversized_body_is_refused_before_authentication`, `test_oversized_body_has_no_side_effects`, `test_oversized_body_is_logged`, `test_upload_path_limit_includes_multipart_framing`, `test_malformed_override_cannot_disable_the_cap` (4), `test_chunked_body_without_content_length_is_proxys_responsibility`, `test_body_limit_short_circuits_before_the_route` | **Yes, additive**: a new 413 response and a new documented operator variable `ERP_MAX_REQUEST_BYTES` | No | Integrated (declared-length cap, 2 MiB JSON / upload-limit + 1 MiB multipart) |
| **SEC-09**<br>username | **MISSING** → integrated | `api.py` · `api_create_user` L1283 — only non-empty + password length | control-character / 64-char rule | none | `test_username_with_control_characters_or_excessive_length_is_refused` (4), `test_ordinary_usernames_are_still_accepted` (2) | **No** | No | Integrated |
| **SEC-10**<br>hardening headers | **MISSING** → integrated | `api.py` — its only `@app.middleware("http")` (L302) is logging + rate limiting; no security headers | `_security_headers` middleware | none | `test_headers_on_a_successful_public_response`, `test_headers_on_401`, `test_headers_on_403`, `test_headers_on_413`, `test_headers_on_404`, `test_headers_on_429_and_the_outer_layer_owns_them`, `test_authenticated_responses_are_not_cacheable` | **Yes, additive**: 3 response headers + `Cache-Control: no-store` on authenticated responses | No | Integrated (no CSP — documented reason in the code) |
| **SEC-11**<br>dependencies | **UPSTREAM** → not ported | `pyproject.toml` + `requirements.lock.txt` (53 pins / 1304 hashes) | remediation lock (51 pins) | `test_phase19_dependency_integrity`, `test_phase54_dependency_security_gate`, `test_phase55_docker_security_gate` | — | No | No | **Verified only** (§7) |
| **SEC-12**<br>rate limiting | **UPSTREAM** → not ported | `dz_erp/rate_limit.py`, `_log_request` middleware (`api.py` L302), 429 + `Retry-After`, `/api/v1/rate-limit/policy` | remediation had none (accepted residual) | `test_phase35_api_rate_limiting_and_request_logging`, `tests/test_phase39_bounded_rate_limiting.py`, `tests/test_phase43_limiter_hardening.py`, `tests/test_phase47_request_id_boundary.py` | exercised indirectly by `test_headers_on_429_and_the_outer_layer_owns_them`, `test_body_limit_does_not_consume_a_rate_limit_token` | No | No | **Verified only** |
| **SEC-13**<br>password policy | **ENV / decision** | `api.py` length-only rule (both trees) | same | `tests/test_phase28_user_admin.py` | — | would change the credential contract | No | **Not integrated** — a policy decision, not a defect port |
| **SEC-14**<br>OCR value validation | **MISSING (deferred)** | `intake_ocr.update_extracted_field_by_human` writes `new_value` into `normalized_value` with no type/format check | remediation also deferred it | `tests/test_phase29_ocr_review_policy.py` (review binding narrows the window: a decided field is closed) | — | would change review semantics | possible | **Not integrated** — out of the requested scope; stays documented as open |
| **SEC-15**<br>Host/TLS | **ENV** | `dz_erp/net.py` trusted-proxy policy upstream; no `TrustedHost`/TLS in code | none | `tests/test_phase57_ipv6_aggregation_and_trusted_proxy.py` | — | no | no | **Not integrated** — deployment requirement (TLS termination, Host validation at the proxy) |

### 2.1 What was deliberately *not* duplicated

* **SEC-02 non-finite rejection** and **SEC-12 rate limiting** exist upstream. Direct inspection (`decimal_utils.py:21-34`, `rate_limit.py`, `api.py:302`) plus execution (the remediation's 11 non-finite cases pass unmodified on the target) prove they are implemented, so nothing was copied.
* **Field-edit RBAC** exists upstream (`REVIEW_CAPABLE_ROLES`, `intake_ocr.py:1620`). No second guard was added: two enforcement points would emit two different refusal descriptions for one policy row and could double-audit.
* **The upload service** (`intake_ocr.upload_and_process_supplier_document`) was **not** given a role gate. Twelve upstream test modules call it directly, and in-process callers already hold the database handle; the documented matrix governs the **API boundary**, which is where the fix was applied. This is recorded as a deliberate residual (§11).

---

## 3. Files changed in the target (exact)

`git -C /tmp/pr7work diff --stat`:

```
 dz_erp/api.py               | 151 ++++++++++++++++++++++++++++++++++++++++++++
 dz_erp/decimal_utils.py     |  41 +++++++++---
 dz_erp/procurement_stock.py | 119 +++++++++++++++++++++++++++++++++-
 dz_erp/security_audit.py    |  86 +++++++++++++++++++++++--
 ops/erp.env.example         |  10 +++
 5 files changed, 391 insertions(+), 16 deletions(-)
```

plus the new, untracked test module:

```
 tests/test_phase58_security_selective_integration.py   (851 lines, 55 test functions, 92 cases)
```

Verified anchors in the integrated tree:

| File | Symbol | Line |
|---|---|---|
| `dz_erp/api.py` | `DOCUMENT_INTAKE_ROLES = REVIEW_CAPABLE_ROLES` (reuses upstream constant) | 249 |
| | `SECURITY_RESPONSE_HEADERS` | 272 |
| | `_configured_max_request_bytes(path)` | 279 |
| | `_body_limit_and_security_headers` (`@app.middleware("http")`, registered after `_log_request`) | 429 |
| | `SECURITY_REQUEST_BODY_LIMIT_EXCEEDED` log event | 447 |
| | `require_role_secure(...)` call in `api_upload_document` | 717 |
| | invoice-list `except ValueError → 422` | 1182 |
| | username control-character / length rule | 1442 |
| `dz_erp/decimal_utils.py` | `MAX_ABSOLUTE_EXPONENT = 18` | 27 |
| | `_require_supported_decimal` (was `_require_finite`) | 30 |
| | `isinstance(value, bool)` refusal | 61 |
| | guarded `int` path | 68 |
| `dz_erp/procurement_stock.py` | `VALID_PAYMENT_METHODS` | 44 |
| | `_require_supplier_invoice_no` | 48 |
| | `_parse_invoice_date` | 59 |
| | `_require_valid_payment_method` | 76 |
| | `compute_line_ht_dzd` | 85 |
| | `_materialise_line_ht` | 101 |
| | create path: `lines = _materialise_line_ht(lines)` → date → payment → `fiscal_year` | 1259–1264 |
| | revalidate path: `fiscal_year = _parse_invoice_date(new_date).year`, `lines = _materialise_line_ht(lines)` | 1562, 1591 |
| `dz_erp/security_audit.py` | `require_role_secure` | 808 |
| | staging dir `os.makedirs(..., 0o700)` + `chmod` | 1050–1052 |
| | manifest via `atomic_write_bytes` | 1112 |
| | archive via `atomic_write_bytes(..., mode=0o600)` | 1132 |
| `ops/erp.env.example` | `ERP_MAX_REQUEST_BYTES` documented (drift gate) | +10 |

Machine-applicable artefacts, exported for review (the integration is **not** committed — §13):

* `docs/security/selective_integration/phase58_selective_integration.patch` — the complete `git diff` (575 lines)
* `docs/security/selective_integration/phase58_security_selective_integration_test.py` — the new test module (851 lines)

---

## 4. TASK B — the four `api.py` conflicts, resolved individually

All four were re-authored against the upstream text. No "accept ours"/"accept theirs" was used, and no endpoint contract changed.

### C-1 · Import block

* **Upstream behaviour:** imports `net`, `request_id`, `rate_limit`, `review_policy`; `security_audit` exports 12 names.
* **Remediation behaviour:** needs `require_role_secure` and the intake matrix.
* **Resolution:** one added name in the `security_audit` import (`require_role_secure`) and one added name in the `intake_ocr` import (`REVIEW_CAPABLE_ROLES`). Nothing upstream removed or reordered. The matrix is **not** re-declared: `DOCUMENT_INTAKE_ROLES = REVIEW_CAPABLE_ROLES` binds the new gate to the constant upstream already trusts.

### C-2 · `api_upload_document`

* **Upstream behaviour:** resolves `file`, `force_image_ocr`, `declared_language`, then authenticates; validates the MIME/extension pair, streams the body with `read_bounded_upload` (closing it in `finally`), validates the content signature, and forwards all of it — including `declared_language` and `branch_id` — to `upload_and_process_supplier_document`.
* **Remediation behaviour:** role gate before any byte is read.
* **Resolution:** the gate is inserted as **step 0 inside the existing `try`**, so the existing `finally: await close_upload(file)` still runs and the spooled upload is released on refusal. Everything upstream added (`declared_language`, `force_image_ocr`, streaming bound, signature check, 413/415/400/403 mapping) is preserved verbatim. A refusal is 403 with the audited `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED` event.

### C-3 · invoice-list `limit`

* **Upstream behaviour:** `api_list_invoices` (L1032) catches `AuthorizationError`/`TenantIsolationError` → 403, and lets the lister's `ValueError` escape as 500.
* **Remediation behaviour:** map that `ValueError` to 422.
* **Resolution:** one `except ValueError → 422` branch **appended after** the upstream 403 branch. No upstream branch reordered, no lister change, and it matches the read-API idiom (`GET /api/v1/accounts?limit=0` → 422).

### C-4 · `api_create_user`

* **Upstream behaviour:** strips the name, refuses empty, enforces the password floor, delegates to `create_user_admin_secure`, maps `AuthorizationError`→403, `ValueError`→422, `IntegrityError`→409.
* **Remediation behaviour:** control-character and length rule.
* **Resolution:** the rule is inserted **between** the empty-name check and the password check; the delegation and all four exception mappings are untouched.

### C-5 (semantic, not textual) · the field-edit path

Upstream enforces the matrix inside `update_extracted_field_by_human`. The remediation's endpoint guard would duplicate it. **Resolution: not ported**, with a regression test (`test_field_review_guard_is_unchanged_upstream`) that pins the behaviour to upstream's own guard.

---

## 5. TASK C — function-signature compatibility

### 5.1 `procurement_stock.py` — public/private split and `_RefusedAttempt`

| Concern | Finding | Handling |
|---|---|---|
| Public API | upstream `create_and_validate_supplier_invoice` (L1084) is a **wrapper** delegating to `_create_and_validate_supplier_invoice` (L1139) and carrying `price_tolerance_dzd` / `branch_id` | **untouched** — signature, defaults and delegation unchanged |
| Where the new validation lives | inside the **private** function, before the payload digest and before any transaction | create path L1259–1264 |
| Refusal machinery | `_RefusedAttempt` is a `FinancialIntegrityError` subclass; the wrapper's `except _RefusedAttempt: _record_refused_attempt(...); raise` is the only handler | the new checks raise plain `ValueError`, so they bypass that handler and reach the endpoint's existing `except (ValueError, FinancialIntegrityError) → 422` (`api.py:787`) — **verified by execution**: 422 with the documented detail, and the duplicate-document refusal tests still pass |
| Correction path | `revalidate_supplier_invoice` had the **same** two defects (date slice, key subscript) | same helpers applied at L1562/L1591; a regression test covers it |

### 5.2 `decimal_utils.py` — one guard, extended (no duplicate logic)

Upstream has exactly one conversion choke point. The integration **renames that function** (`_require_finite` → `_require_supported_decimal`) and adds two rules inside it: `adjusted() > 18` and `bool`. The `int` path — previously unguarded entirely — now goes through it too.

Verified domain behaviour after the change:

| Input | Result |
|---|---|
| `"NaN"`, `"sNaN"`, `"Infinity"`, `"-Infinity"` | `ValueError` (unchanged from upstream) |
| `"abc"`, `""` | `ValueError` (unchanged) |
| `"1e400"`, `"9"*60`, `10**19`, `10**30` | `ValueError` — **new**; previously `InvalidOperation` → HTTP 500 |
| `True` / `False` | `TypeError` — **new**; previously silently `1.00` |
| `"10000.00"`, `Decimal("11900.00")`, `0` | accepted (unchanged) |
| product overflow, e.g. `1e17 × 1e17 = 1e34` | `ValueError` (the guard fires at the conversion point of the product, so an oversized intermediate value is a 422, not a 500) |

### 5.3 `security_audit.py` — optional backup key and fail-closed custody

| Concern | Finding | Handling |
|---|---|---|
| Signature | upstream: `create_encrypted_backup(db, vault, output_backup_path, backup_encryption_key=None)`, resolving the key inside when omitted (L974–982) | **unchanged**; both call styles work (explicit key in the new tests, env resolution untouched) |
| Key absent | `resolve_backup_encryption_key()` still raises — the integration did not touch that path | **fail-closed retained**; no silent downgrade |
| Integrity verification | manifest fields (`db_sha256`, `vault_file_hashes`, key-version summaries) and the restore preflight are untouched | **unchanged** |
| New hardening | archive 0600 **atomically**; staging dir 0700; manifest 0600 | via `atomic_write_bytes` (mkstemp 0600 → fsync → `os.replace` → dir fsync → cleanup on failure); no plaintext file is published next to the backup |
| Windows | `atomic_write_bytes` uses `_set_mode_on_fd` + `os.replace` (NTFS-atomic) and the existing project pattern for synthetic mode bits | no new platform-specific code path |

---

## 6. TASK E — middleware execution order (defined, proven)

### 6.1 The defined order

FastAPI/Starlette wrap the stack in reverse registration order, so the **most recently registered** `http` middleware is the **outermost**. The target registers `_log_request` (request id, rate limiting, structured logging) first and `_body_limit_and_security_headers` second, so the order, outermost → innermost, is:

```
ServerErrorMiddleware
  └─ _body_limit_and_security_headers   ← NEW: Content-Length cap → 413; hardening headers on every response
       └─ _log_request                  ← upstream: X-Request-Id, 429 + Retry-After, HTTP_REQUEST logging
            └─ ExceptionMiddleware
                 └─ router → endpoint (auth dependency → SEC-01 role gate → handler)
```

Consequences, by design:

1. **Request-size rejection runs first**, before authentication, before rate accounting and before the router binds anything.
2. **Rate-limit rejection (429) still carries the hardening headers**, because it returns through the outer layer (proven, see below).
3. **Request id is identical in both layers**: both derive it from `X-Request-Id` through `sanitize_request_id`, so a 413 quotes the same id the inner layer would have used; an invalid value is replaced by a server uuid and never echoed.
4. **No duplicate headers**: the outer layer uses `setdefault` everywhere and the inner layer's `X-Request-Id` assignment wins.
5. **413 is logged** by the outer layer itself (`SECURITY_REQUEST_BODY_LIMIT_EXCEEDED`), since the inner logger never sees the request.

### 6.2 Proofs (not assumptions)

| Claim | Test | Observed |
|---|---|---|
| 413 short-circuits before the route | `test_body_limit_short_circuits_before_the_route` | `POST /healthz` (a 405 path) with an oversized body → **413** |
| 413 is produced **outside** the limiter | `test_body_limit_does_not_consume_a_rate_limit_token` | with `requests_per_minute=1`: oversized → 413, then `GET /healthz` → 200, then → 429 (the 413 consumed no token) |
| 429 (inner) is decorated by the outer layer | `test_headers_on_429_and_the_outer_layer_owns_them` | 429 carries `nosniff`/`no-referrer`/`DENY`, exactly one `X-Request-Id` equal to the supplied one, plus inner `Retry-After` |
| 413 precedes authentication | `test_oversized_body_is_refused_before_authentication` | unauthenticated 3 MiB token request → 413, not 401/422 |
| No duplicate headers anywhere | `_assert_hardened` asserts `get_list(name) == [value]` on 200/401/403/404/413/429 | single values |
| Request id on every outcome | `test_request_id_is_echoed_on_rejections`, `test_invalid_request_id_is_replaced_not_echoed` | supplied valid id echoed on 413; control characters replaced |

**Documented limitation (asserted, not hidden):** the cap reads the **declared** `Content-Length`. `test_chunked_body_without_content_length_is_proxys_responsibility` drives the raw ASGI app with no `Content-Length` and pins the behaviour: the request reaches the application (no silent drop, no half-read), where the per-endpoint bounds still apply; the stream itself must be bounded by the reverse proxy. This is stated in `ops/erp.env.example` next to the new variable.

---

## 7. TASK F — dependency reconciliation (SEC-11)

Verified against the target's own lock, not copied from the remediation.

| Package | Target pin | Advisory status (pip-audit on the target env) |
|---|---|---|
| fastapi | **0.133.0** | clean |
| starlette | **1.3.1** | clean (7 advisories on the remediation's 0.46.2, max fix 1.3.1) |
| python-multipart | **0.0.31** | clean (6 advisories on 0.0.20, max fix 0.0.31) |
| pypdf | **6.21.4** | clean (67 advisories on 5.4.0, max fix 6.21.4) |
| pillow | **13.0.0** | clean (53 advisories on 11.1.0) |
| pdfminer.six | **20251230** | clean (2 advisories on 20250327) |
| cryptography | 50.0.0 | clean (6 advisories on 44.0.2) |
| certifi | 2026.10.18 | clean (equals the reported fix version) |
| pytest | 9.0.3 | clean (1 advisory on 8.3.5) |

* **Hash coverage:** `tools/generate_hash_lock.py --check` → `OK - 53 pinned distributions, 1304 hashes, no drift from requirements.lock.txt`.
* **Python compatibility:** `requires-python = ">=3.11.17,<3.13,!=3.12.0..!=3.12.9"` — unchanged by this integration; every test in this report ran on 3.11.2 (below the floor), which is exactly why 20 restore tests are blocked.
* **Bundled tooling, classified separately:** `pip 23.0.1` (7 advisories) and `setuptools 66.1.1` (4 advisories). **Neither is in either lock file**; they are the interpreter's `venv` defaults, they are not imported by the application at runtime, and they are install/build-time only. `wheel` was installed locally solely to attempt the packaging check (§8.6) and is likewise not a project dependency.
* **No framework upgrade was performed, and no pin was copied from the remediation workspace.** The remediation's lock was never applied here.
* **Result: no vulnerable *application* dependency remains. SEC-11 is resolved by the target and requires nothing from this integration.**

---

## 8. TASK G — testing, with exact counts

Every command below ran in `/tmp/pr7work` (integrated) or `/tmp/pristine` (unmodified target, same venv, same interpreter).

### 8.1 New security regression suite (the integration's own evidence)

```
tests/test_phase58_security_selective_integration.py
collected 92 · passed 91 · failed 0 · skipped 1 · exit 0     (8.70 s)
```

The single skip is `test_backup_round_trips_and_verifies_the_chain` — guarded by `safe_restore_supported()`, i.e. blocked by the interpreter floor, **not** a pass.

### 8.2 Does the suite actually discriminate? (measured, not claimed)

The same module was executed against the **unmodified** target. As published it cannot even import there (`ImportError: cannot import name 'DEFAULT_MAX_JSON_REQUEST_BYTES'`); with a measurement-only import shim in the disposable worktree:

```
60 failed · 31 passed · 1 skipped
```

Every one of the 31 pre-integration passes is a control, not a hidden gap:

* **Positive controls** (must work before and after): the 4 permitted upload roles, ordinary usernames (2), the 2 list-limit boundaries, `test_upload_requires_authentication`, `test_field_review_guard_is_unchanged_upstream`, `test_backup_leaves_no_staging_directory`, `test_backup_publishes_exactly_one_file`, `test_failed_backup_writes_nothing_and_cleans_up`, `test_body_below_the_limit_is_processed`, `test_body_exactly_at_the_limit_is_not_refused`, `test_chunked_body_without_content_length_is_proxys_responsibility`, `test_malformed_override_cannot_disable_the_cap` (4 — no cap existed, so no override could be ignored), `test_derivation_rounds_like_the_validator`, `test_omitted_and_explicit_payloads_produce_the_same_digest` (unit-level).
* **Already-fixed upstream sub-cases**: `test_unusable_line_ht_is_a_422_not_a_500[NaN|sNaN|Infinity|-Infinity|abc|<empty>]` — 6 of 8 params, because upstream's non-finite guard already catches them. The two that fail pre-integration are `1e400` and 60 digits — precisely the magnitude gap that was closed.
* **Invariants that must hold in both states**: `test_refused_invoice_leaves_no_accounting_or_stock_effect`, `test_mismatched_line_ht_still_reaches_validation`, `test_missing_qty_is_a_request_error`.

### 8.3 The remediation's own 51 tests, run against the target

| Tree | Result |
|---|---|
| Unmodified target `5a3e47dd` | **27 failed / 24 passed** (reconciliation baseline) |
| Integrated target | **51 passed / 0 failed** |

The 27 that failed on the raw target are exactly the findings this phase integrated (plus upstream's partial cases).

### 8.4 Full suite, A/B

| | Collected | Passed | Failed | Skipped | Exit |
|---|---|---|---|---|---|
| Unmodified target | 1233 | **1204** | 20 | 9 | 1 |
| Integrated target | 1325 | **1295** | 20 | 10 | 1 |

* **Failure sets are byte-identical** (compared test-by-test from the two JUnit XMLs): `identical failure set: True`, `new failures: []`, `fixed: []`.
* Passing delta: **+91** = the 91 new tests. Skipped delta: **+1** = the guarded restore round-trip.
* The 20 failures are the interpreter gate (`safe backup restore requires Python 3.11.17+ or Python 3.12.10+`) in `test_phase11`, `test_phase12`, `test_phase5`, `test_phase6`, `test_phase8`, `test_phase9`. **Environment-blocked, not failed** — reported as such, and the gate was not touched.

### 8.5 Targeted groups (from the integrated JUnit)

| Group | Modules | Passed | Failed | Skipped |
|---|---|---|---|---|
| Procurement / inventory / 3-way match | phase4, phase33, phase34, phase47 (price variance) | 76 | 0 | 0 |
| Accounting / tax / timbre / periods | phase2, phase36, phase40, phase46, phase51 | 96 | 0 | 0 |
| Audit / backup / keys / DR | phase5, phase9, phase10, phase14, phase49, phase52, phase56 (2) | 89 | 1 | 0 |
| Upload / security / OCR review | phase7, phase8, phase29, **phase58 (new)** | 403 | 2 | 1 |
| Rate limiting / RBAC / request id | phase35, phase39, phase43, phase57 | 72 | 0 | 0 |
| Read API / users / review API | phase25, phase27 (review), phase28 (users), phase47 (request id) | 42 | 0 | 0 |
| Desktop | phase26, phase27 (UI), phase28 (UI) | 9 | 0 | 1 |

(The failures inside "audit/backup" and "upload/security" are the same interpreter-gated restore tests counted above.)

### 8.6 Static, packaging and smoke checks

| Check | Command | Result |
|---|---|---|
| Ruff (changed area) | `ruff check dz_erp/ tests/test_phase58_…py` | **All checks passed** (exit 0) |
| Ruff (repository) | `ruff check .` | 2 findings, **identical on the unmodified target** (`odoo_addons` explicit re-export) → **no new lint debt** |
| Compile | `python -m compileall -q dz_erp tests/…` | exit 0 |
| Dependency graph | `python tools/verify_dependency_graph.py` | `OK — the environment matches the declared dependency graph` (54 distributions) |
| Lock integrity | `python tools/generate_hash_lock.py --check` | `OK — 53 pins, 1304 hashes, no drift` |
| Wheel build | `pip wheel . --no-build-isolation` | **FAILS — pre-existing, reproduced identically on the unmodified target**: `error: Multiple top-level packages discovered in a flat-layout: ['ops', 'dz_erp', 'vendor', 'odoo_addons']`. Not caused by this integration; a packaging-configuration blocker (§11). |
| Smoke | standalone script: boot app → `/healthz` (hardened) → login → upload (200) → warehouse upload (403) → invoice list (200) → 3 MiB unauthenticated body (413) → `/docs` (404, disabled) | `SMOKE OK` |

### 8.7 Not executed (explicit)

* The 20 interpreter-gated restore/backup tests (plus the 1 skipped new restore test) — **no supported interpreter obtainable here**.
* 3 Windows-only tests and 3 desktop/PySide6 tests — no Windows host, PySide6 not installed.
* Docker/Compose, MinIO, AWS KMS — none available; nothing about them is claimed.
* No test was edited to obtain a green result. Two test expectations were corrected during development, both wrong rather than inconvenient: an authenticated 403 was expected *not* to carry `Cache-Control: no-store` (it correctly does), and `verify_audit_chain` was read as `["ok"]` when its contract is `["valid"]`. Both corrections are visible in the development history of the module and neither weakens an assertion.

---

## 9. TASK H — accounting and data-integrity regression

| Property | Evidence |
|---|---|
| Invoice line arithmetic (HT/TVA/TTC), discount allocation | `test_discount_is_applied_before_derivation` (10 % discount → 9000.00 HT, 1710.00 TVA, 10710.00 TTC, state `Validated`); `test_derivation_rounds_like_the_validator` (100.005 → 100.01, half-up at 2 dp); phase4/phase33/phase47 groups: 76 passed |
| Totals stored unchanged | `test_valid_invoice_keeps_exact_totals` asserts `10000.00 / 1900.00 / 0.00 / 11900.00` and `Validated` |
| Timbre rules | untouched; `phase36`, `phase40`, `phase46` pass (96 passed in the accounting group) |
| Journal balance / posting | no code path changed; `phase6` end-to-end fails only at its restore step (interpreter); posting/reversal tests pass (`phase51`, `phase33`) |
| Stock movements / 3-way match | untouched (`_validate_invoice_lines` semantics unchanged — only the *input* is materialised); `phase4`, `phase47` pass |
| Idempotency | `test_phase32`/`phase33` idempotency assertions pass; the payload-digest change only affects payloads that previously could not succeed |
| Audit chain | `test_refused_upload_is_audited` asserts `verify_audit_chain(1)["valid"] is True` after a refusal; audit-chain tests across phase5/14/49/52 pass |
| Fiscal year (duplicate key) | `test_fiscal_year_still_comes_from_the_invoice_date` asserts `2026` after the date-parsing change |
| No partial writes on refusal | `test_refused_invoice_leaves_no_accounting_or_stock_effect` (invoices, lines, journal, stock all 0), `test_oversized_body_has_no_side_effects`, `test_refused_upload_creates_no_document_row` |

No Algerian tax rule, rate, bracket, rounding mode or legal interpretation was modified. Nothing here constitutes an expert-comptable approval.

---

## 10. Documentation changes

Updated **in the target tree** (drift gate requires it):

* `ops/erp.env.example` — documents `ERP_MAX_REQUEST_BYTES` (purpose, default, fail-closed override handling, multipart allowance, and the chunked-body/proxy caveat). `test_phase37_deployment_assets` passes again; it had **failed** after the first integration pass, which is how the omission was caught.

Deliberately **not** edited: the six remediation reports in `docs/security/` and the remediation's own test module. They describe the *remediation workspace*, and this phase must leave that workspace byte-identical (§13). Their superseded statements are corrected here instead:

| Superseded statement | Corrected statement (evidence) |
|---|---|
| Register/residual risks: **SEC-11 open** ("known-vulnerable pinned dependencies") | **Resolved by the target's own lock** — 8 application distributions, 143 advisories → 0 (§7). Only bundled `pip`/`setuptools` (not in any lock, install-time only) remain flagged. |
| Register/residual risks: **SEC-12 accepted residual** ("no in-application rate limiting") | **Implemented upstream** — `dz_erp/rate_limit.py`, 429 + `Retry-After`, `/api/v1/rate-limit/policy`, per-client and per-username failure windows (`rate_limit.py` env policy, tests phase35/39/43/47). |
| SEC-14 "unvalidated OCR correction value" | **Still open, unchanged** — upstream writes `new_value` unvalidated; the phase-29 review binding narrows the window to pre-decision fields only. |
| SEC-15 "no Host validation / TLS" | **Partially addressed upstream** (`dz_erp/net.py` trusted-proxy policy, X-Forwarded-For handling, phase57); TLS termination and `TrustedHost` remain a **deployment** requirement. |
| Test evidence: "27 upstream modules unavailable" | **No longer true** — all 27 were available in this phase and ran (§8.4). |

---

## 11. Remaining blockers, residuals and decisions

| # | Item | Class | Detail |
|---|---|---|---|
| **B1** | Interpreter floor | **ENV** | 20 restore/backup tests + 1 new restore test cannot run on CPython 3.11.2; the project requires 3.11.17+/3.12.10+, and no supported interpreter could be obtained (release CDN not allowlisted). Fail-closed gate left intact. SEC-07's *restore* half therefore has no end-to-end evidence in this environment. |
| **B2** | Wheel build fails | **PRE-EXISTING** | `Multiple top-level packages discovered in a flat-layout` — identical on the unmodified target. Packaging config (`[tool.setuptools] packages`) needs an explicit declaration; not part of a security integration, and must not be "fixed" silently. |
| **B3** | Integration is uncommitted | **BY MANDATE** | No commit/push was authorised. The change exists as `docs/security/selective_integration/phase58_selective_integration.patch` + the test module, applied to a disposable clone. Reviewing it as a branch is the next step (a human action). |
| **B4** | Platform coverage | **ENV** | 3 Windows-only tests, 3 desktop/PySide6 tests, Docker/Compose/MinIO/KMS: not executed. |
| **B5** | SEC-13 / SEC-14 / SEC-15 | **DECISION** | Password policy, OCR value validation, TLS/Host policy — deliberately not changed here (policy and deployment, not defect ports). |
| **B6** | "10 user modifications" | **UNRESOLVED** | Still not identifiable in any accessible workspace; unchanged from the reconciliation audit. |
| **B7** | Upload service has no role gate | **ACCEPTED RESIDUAL** | The matrix is enforced at the API boundary (where every external caller enters); in-process callers keep their contract. Revisit only with a decision that changes the internal service contract. |
| **B8** | Chunked bodies | **DOCUMENTED LIMIT** | The application cap reads `Content-Length`; a chunked body is bounded by the proxy. Asserted by a test and stated in the operator env file. |

---

## 12. Final decision

### **SELECTIVE INTEGRATION PARTIAL — BLOCKERS REMAIN**

**Why not "COMPLETE":** the selective work is finished and green (9 findings integrated, 92 new tests, 51/51 reference tests, zero regressions, identical failure set), but three of the blockers are outside this phase's authority or environment — the interpreter floor (B1), the pre-existing packaging failure that prevents a build artefact (B2), and the fact that the result is an uncommitted patch by mandate (B3). Declaring the integration "complete" would imply a reviewable, buildable, fully-exercised change set, which is not what exists yet.

**Why not "REJECTED":** every fix is evidence-backed, the target's own suite shows no new failure, no test was weakened, no fail-closed behaviour was relaxed, and nothing upstream was duplicated. The remaining gaps are environmental, pre-existing, or explicitly out of scope.

---

## 13. TASK — final Git validation (exact before/after)

### 13.1 Targets

| Object | Before | After |
|---|---|---|
| PR #7 state | `open`, draft, unmerged | **`open`, draft, unmerged** (`merged: false`, `mergeable: true`) |
| PR #7 head | `5a3e47ddfba2b789bcff71d6438db4927f5c7986` | **`5a3e47dd…`** (unchanged) |
| PR #7 base | `e4e47b3bf4376e804f17b23e63c86f7fe384056c` | **`e4e47b3b…`** (unchanged) |
| `ls-remote` head of `arena/7f12d627-erp` | `5a3e47dd…` | **`5a3e47dd…`** |
| `ls-remote` `main` | `4fc1364a…` | **`4fc1364a…`** |

### 13.2 Remediation workspace (must be untouched)

```
$ sha256sum -c /tmp/before_workspace.sha256
dz_erp/api.py: OK
dz_erp/decimal_utils.py: OK
dz_erp/procurement_stock.py: OK
dz_erp/security_audit.py: OK
tests/test_phase34_security_validation.py: OK

$ git rev-parse HEAD          → 4fc1364a9504fab1153ea44e753f354f00023428   (unchanged)
$ git branch --show-current   → arena/7557bcfb-erp                          (unchanged)
$ git rev-list --count main..HEAD → 0                                       (no commits created)
$ git status --porcelain
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
?? docs/security/
?? tests/test_phase34_security_validation.py
```

Only **additions** were made inside the already-untracked `docs/security/`: this report and the two `selective_integration/` artefacts. No user change was discarded, no file overwritten, no unrelated branch touched.

### 13.3 Integration workspace

```
$ git -C /tmp/pr7work rev-parse HEAD   → 5a3e47ddfba2b789bcff71d6438db4927f5c7986
$ git -C /tmp/pr7work status --porcelain
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
 M ops/erp.env.example
?? tests/test_phase58_security_selective_integration.py
$ git -C /tmp/pr7work log --oneline -1 → 5a3e47d feat(dr): add crash point hooks …
```

Detached HEAD at the target SHA, **no commits, no pushes**, nothing written back to the remote. No production data, database, or Docker volume was touched (no Docker is available at all).

### 13.4 Changed-file list (the integration, complete)

```
dz_erp/api.py                                              (+151)
dz_erp/decimal_utils.py                                    (+41 / −16)
dz_erp/procurement_stock.py                                (+119)
dz_erp/security_audit.py                                   (+86)
ops/erp.env.example                                        (+10)
tests/test_phase58_security_selective_integration.py       (new, 851 lines, 92 cases)
```

*End of report. Nothing was committed, pushed, merged or cherry-picked; no application file in the remediation workspace was modified.*
