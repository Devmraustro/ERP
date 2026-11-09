# ERP Algeria — Security Remediation Reconciliation & Git Provenance Audit

**Document type:** read-only reconciliation and provenance audit (no remediation applied, no merge performed)
**Prepared:** 2026-11-09
**Local branch:** `arena/7557bcfb-erp` @ `4fc1364a9504fab1153ea44e753f354f00023428`
**Compared against:** pull request #7 (`arena/7f12d627-erp`, head `5a3e47ddfba2b789bcff71d6438db4927f5c7986`)
**Final status:** **READY WITH BLOCKERS** (see §9)

---

## 0. Scope, method, evidence, and limits

### 0.1 Mandate

The security remediation from the previous phase (findings SEC‑01 … SEC‑15, 51 tests, six reports) exists only as **uncommitted working‑tree changes** on this branch. In parallel, pull request #7 carries 106 commits of upstream work on the same code base. This audit answers three questions without changing anything:

1. Where do the two lines of work actually stand relative to each other (Git provenance)?
2. What did the remediation change, exactly, and does each fix and each test really address its vulnerability?
3. What will break, conflict, or silently regress when the two are brought together — and in what order should that be done?

### 0.2 Strict read‑only discipline

No checkout, switch, reset, clean, stash, rebase, merge, cherry‑pick, commit, push, fetch, or dependency upgrade was performed. No file in the repository was modified except the creation of this report. No branch, PR, database, Docker resource, or user file was touched. No conflict was resolved: every overlap is *described*, never reconciled.

### 0.3 Evidence actually used

| Evidence | How obtained | Where it lives |
|---|---|---|
| Local branch, HEAD, worktree, refs, object availability | `git branch --show-current`, `git rev-parse`, `git worktree list`, `git for-each-ref`, `git cat-file -t`, `git status --porcelain`, `git diff --stat` | this repository (read‑only) |
| PR #7 state, head/base SHAs, commit count, mergeability, file set | `gh pr view 7`, `gh api repos/Devmraustro/ERP/pulls/7`, `gh api compare/4fc1364a...5a3e47dd`, `gh api contents/<path>?ref=5a3e47dd` | ephemeral `/tmp` fetches |
| PR #7 source tree (full working copy for compatibility testing) | `https://codeload.github.com/Devmraustro/ERP/tar.gz/5a3e47dd…` extracted to `/tmp/pr7/ERP-5a3e47dd…/` | `/tmp` only, not in this repo, not persisted |
| PR #7 runtime | fresh venv `/tmp/pr7venv` installed from **PR #7's own** `requirements.lock.txt` (53 pins) — verified `fastapi 0.133.0`, `starlette 1.3.1`, `pytest 9.0.3` | `/tmp` only |
| Local runtime | fresh venv `/tmp/mainvenv` installed from **this branch's** `requirements.lock.txt` (51 pins) — `fastapi 0.115.12`, `pytest 8.3.5` | `/tmp` only |
| Pre‑fix behaviour (test efficacy) | reverse‑applied this branch's own `git diff` to a disposable copy (`/tmp/revert`), then ran the 51 tests there | `/tmp` only |
| Full suite on this branch | `pytest -q --junitxml=/tmp/recon_junit.xml` → **20 failed, 722 passed, 6 skipped, 748 collected, 119.86 s** | `/tmp` only |
| Dependency advisories | `pip-audit --local --format json` (2.10.1) against both venvs | `/tmp/pipaudit2.json`, `/tmp/pipaudit_pr7.json` |

**Interpreter for every execution in this audit:** CPython **3.11.2** (the only interpreter available in this environment). This is below the project's own floor and is the direct cause of the 20 failures (§5).

### 0.4 Engineering limits of the evidence

* PR #7's objects are **not** in this clone. Everything about PR #7 comes from the GitHub API and from the tag tarball, both read‑only copies.
* PR #7 was **not** rebuilt as a merged branch; it was tested as an extracted export with its own pinned dependencies. That is sufficient to answer "does this fix / this test still apply upstream?" but it is **not** a substitute for a real integration run.
* No Docker, no MinIO, no AWS KMS, no staging host, no PySide6 in this environment (§5.4). Any conclusion that depends on them remains **NOT VERIFIED**.
* Where this document says a behaviour is "fixed upstream", it means *the code path in `5a3e47dd` satisfies the test's assertion when executed locally*, nothing more.

---

## 1. Git provenance

### 1.1 Local repository state

| Item | Value |
|---|---|
| Current branch | `arena/7557bcfb-erp` |
| HEAD | `4fc1364a9504fab1153ea44e753f354f00023428` |
| `refs/heads/main` | `4fc1364a9504fab1153ea44e753f354f00023428` (identical) |
| `refs/remotes/origin/main` | `4fc1364a9504fab1153ea44e753f354f00023428` (identical) |
| Worktrees | exactly one: `/home/user/ERP` |
| Commits created by this work | **none** |
| Working tree | 4 modified files + 2 untracked paths, nothing else |

```
 M dz_erp/api.py                 | 122 ++++++++++++++++++   (combined +/−)
 M dz_erp/decimal_utils.py       |  37 ++++++++++++--
 M dz_erp/procurement_stock.py   |  67 ++++++++++++++++++++-
 M dz_erp/security_audit.py      |  67 ++++++++++++++++++++-
 4 files changed, 287 insertions(+), 6 deletions(-)

?? docs/security/                                  (6 reports, 124 KB)
?? tests/test_phase34_security_validation.py       (525 lines, 51 tests)
```

**Provenance conclusion for the remediation:** it is *entirely uncommitted*. `HEAD` is byte‑for‑byte the commit that `main` and `origin/main` point at. Nothing in the remediation can be identified by SHA, and nothing has been published.

### 1.2 Pull request #7

| Item | Value |
|---|---|
| State | `OPEN`, **draft** (`draft: true` in REST / `isDraft` reported by `gh pr view`) |
| Head | `5a3e47ddfba2b789bcff71d6438db4927f5c7986` on branch `arena/7f12d627-erp` |
| Head commit | parent `6193d9fa…`, subject `feat(dr): add crash point hooks to restore_and_verify_backup for subprocess testing`, dated 2026‑11‑06T23:53:55Z |
| Base | `e4e47b3bf4376e804f17b23e63c86f7fe384056c` on branch **`fix/phase29-invoice-discount-and-line-net-consistency`** — *not* `main` |
| Mergeability | `mergeable: true`, `mergeable_state: clean` |
| Size | 95 files, **+26 970 / −527**; REST reports 98 commits, `compare` reports 106 |
| Relation to `main` | 106 commits **ahead**, **0 behind** |

The base being a feature branch rather than `main` matters for integration planning: PR #7 is a stacked PR. Its diff is expressed against `e4e47b3b`, which itself is not present locally either.

### 1.3 Local object availability

```
git cat-file -t 5a3e47ddfba2b789bcff71d6438db4927f5c7986  →  could not get object info
git cat-file -t e4e47b3bf4376e804f17b23e63c86f7fe384056c  →  could not get object info
git cat-file -t 4fc1364a9504fab1153ea44e753f354f00023428  →  commit
```

PR #7's head commit **and its base commit are absent from this clone**. The `arena/7f12d627-erp` branch has never been fetched here. Fetching was deliberately not performed (read‑only phase); all PR #7 source was obtained through the contents API / tag tarball instead.

### 1.4 Ancestry, merge base, and the shape of the divergence

* `GET /repos/Devmraustro/ERP/compare/4fc1364a…5a3e47dd…` → `status: ahead`, `ahead_by: 106`, `behind_by: 0`.
* **Merge base = `4fc1364a9504fab1153ea44e753f354f00023428` = this branch's HEAD.**
* Therefore: local HEAD is a **strict ancestor** of PR #7's head; PR #7 is a **strict superset** of every committed commit available here. There is no committed divergence in either direction — the *only* divergence is the uncommitted remediation.
* Confidence: high. Both directions were confirmed (compare API `behind_by: 0`, and `git merge-base --is-ancestor HEAD <pr-head>` is moot because the object is absent, so the API result is the authority).

**Commit-count discrepancy (reported, not reconciled):** the PR object reports `commits: 98`; the compare endpoint reports `ahead_by: 106`. The two numbers count different things (commits on the PR branch reachable from head and not from *its base* `e4e47b3b`, versus commits reachable from head and not from *local HEAD*). It does not affect any conclusion in this report, but any future CI or release note that quotes "98 commits" should be corrected to the compare figure when describing the distance from `main`.

### 1.5 Changes present locally but absent from PR #7

| Local artefact | Present in PR #7 (`5a3e47dd`) |
|---|---|
| `dz_erp/api.py` (+122) | file exists, **content differs**; every added symbol absent (see §3) |
| `dz_erp/decimal_utils.py` (+37/−? ) | file exists; `_guarded`, `MAX_ABSOLUTE_EXPONENT`, bool rejection **absent** |
| `dz_erp/procurement_stock.py` (+67) | file exists; `VALID_PAYMENT_METHODS`, `compute_line_ht_dzd`, `_materialise_line_ht` **absent** |
| `dz_erp/security_audit.py` (+67) | file exists; `require_role_secure` **absent**; backup `0600` fix **absent** |
| `tests/test_phase34_security_validation.py` (51 tests) | **absent**; PR #7 has an unrelated `tests/test_phase34_ocr_duplicate_row_integrity.py` (same phase number, different file) |
| `docs/security/` (6 reports, 124 KB) | **absent** — PR #7 has no `docs/security/` directory at all |
| `pyproject.toml`, `requirements.lock.txt`, `requirements.lock.hashes.txt` | **unmodified locally**; PR #7 *does* change `pyproject.toml` and `requirements.lock.txt` (53 pins vs 51) |

### 1.6 The "10 previously reported user modifications"

**Not identifiable in this clone.** At the start of the previous phase the working tree was clean apart from the security work; `git status --porcelain` today shows exactly the six entries in §1.1 and no other local modification exists anywhere in the tree, no other branch, and no other worktree holds them (§1.1). The only other accessible surface is the GitHub API, and the only unmerged content available there is PR #7's 106 commits. The honest conclusion is:

* the 10 modifications are **not present in this workspace**;
* the most plausible origin is PR #7 (the only reachable line of development that touches the same files);
* if they were made in a separate workspace (staging host, developer machine), they are **unverifiable from here** and must be re‑supplied or re‑identified before integration.

This is a blocker input to §9, not a blocker by itself.

---

## 2. Security fix inventory

All statements below come from the actual working‑tree diff (`git diff`, 421 lines, 4 files, +287/−6) and from executing the tests, not from the earlier executive summary.

### 2.1 Change map (verified anchors)

| File | Symbol (exact) | Nature of change |
|---|---|---|
| `dz_erp/security_audit.py` | **new** `require_role_secure(db, requesting_user_id, allowed_roles, *, action, target_model="api", target_id="-")` — inserted after `list_stock_quants_secure`, before `_require_user_admin` | server‑side role gate; role read from the `users` row of the verified credential; denial written as `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED` inside the caller's tenant chain, then `AuthorizationError` raised |
| `dz_erp/security_audit.py` | `create_encrypted_backup` | `output_backup_path.write_bytes(encrypted_archive)` → `os.open(..., 0o600)` + `os.fdopen` write + best‑effort `os.chmod(0o600)` |
| `dz_erp/decimal_utils.py` | **new** `MAX_ABSOLUTE_EXPONENT = 18`, **new** `_guarded(dec, original)`; `to_decimal` | non‑finite rejection, magnitude cap `adjusted() > 18`, explicit `bool` rejection; guards applied on the `Decimal`, `int` and `str` paths |
| `dz_erp/procurement_stock.py` | **new** `VALID_PAYMENT_METHODS`, `MAX_SUPPLIER_INVOICE_NO_LENGTH = 128`, `compute_line_ht_dzd()`, `_materialise_line_ht()`; `create_and_validate_supplier_invoice` | request‑shape validation before any write (empty/oversized `supplier_invoice_no`, `payment_method` allowlist, `datetime.strptime("%Y-%m-%d")`), `fiscal_year = invoice_day.year` replaces `int(invoice_date[:4])`, `lines = _materialise_line_ht(lines)` and `declared_line_ht = round_dzd(line["line_ht_dzd"])` replaces the `round_dzd(None)` path |
| `dz_erp/api.py` | **new** module constants `DOCUMENT_INTAKE_ROLES`, `ENV_MAX_REQUEST_BYTES`, `DEFAULT_MAX_JSON_REQUEST_BYTES` (2 MiB), `MULTIPART_FRAMING_ALLOWANCE` (1 MiB), `REQUEST_BODY_METHODS`, `UPLOAD_PATH`; **new** `_configured_max_request_bytes(path)` | body‑size policy |
| `dz_erp/api.py` | **new** `@app.middleware("http") _security_headers` inside `create_erp_fastapi_app` | declared `Content-Length` > cap → `413` before body read; then `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, and `Cache-Control: no-store` on authenticated responses |
| `dz_erp/api.py` | `api_upload_document` | `require_role_secure(..., DOCUMENT_INTAKE_ROLES, action="document upload")` **before any byte is parsed**; 403 + `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED` |
| `dz_erp/api.py` | `api_edit_extracted_field` | same matrix, `action="document field correction"` |
| `dz_erp/api.py` | `api_list_invoices` | `except ValueError → 422` (the secure lister clamps `limit` to 1…500 by raising `ValueError`, which escaped as 500) |
| `dz_erp/api.py` | `api_create_user` | username ≤ 64 chars and no `ord(ch) < 0x20` / `0x7F` → 422 |

### 2.2 Finding‑by‑finding inventory

"Irrefutable pre‑fix" = the test fails when this branch's diff is reverse‑applied to a disposable copy of the same tree (`/tmp/revert`). "PR #7" = the same test executed against `5a3e47dd` with PR #7's pinned runtime.

| ID | Fix (file · symbol) | Original vulnerability | Correction | Tests | Genuine? (pre‑fix result) | PR #7 status |
|---|---|---|---|---|---|---|
| **SEC‑01** (upload half) | `api.py` · `api_upload_document` + `security_audit.py` · `require_role_secure` | any authenticated principal of a tenant, including the roles the RBAC spec explicitly denies, could create documents and make the server stream, buffer, OCR and vault a file | role read from the credential's `users` row, checked **before** the first byte is read; denial audited and committed | `test_upload_is_refused_for_roles_the_rbac_matrix_denies[wh_a, auditor_a, ocr_a]` (3), `test_upload_is_allowed_for_roles_the_rbac_matrix_permits[clerk_a, approver_a, acct_a, admin_a]` (4) | 3 negative tests **fail** pre‑fix; 4 positive controls pass both before and after | **STILL NEEDED** — PR #7's `api_upload_document` (L585) has no role check; all 3 denial tests fail there |
| **SEC‑01** (field‑edit half) | `api.py` · `api_edit_extracted_field` | a denied role could change the values a reviewer is about to approve | same matrix at the endpoint | `test_field_correction_is_refused_for_denied_roles[wh_a, auditor_a, ocr_a]` (3), `test_field_correction_is_allowed_for_clerk_and_audited` (1) | 3 negative tests **fail** pre‑fix; positive control passes | **ALREADY FIXED UPSTREAM, differently** — `intake_ocr.py:1593 update_extracted_field_by_human` enforces `REVIEW_CAPABLE_ROLES` (`intake_ocr.py:188`, check at `:1620`); all 3 denial tests pass on PR #7 |
| **SEC‑02** | `decimal_utils.py` · `_guarded` + `to_decimal` | `Decimal('NaN')` is *quiet*: `quantize` returned NaN and HTTP **200** wrote it into a TEXT money column; `sNaN` / `Infinity` / huge magnitudes raised `InvalidOperation` → unhandled 500; `bool` silently became `1.00` | single guarded conversion point, non‑finite + `adjusted() > 18` + bool rejection, for every caller | `test_non_finite_or_oversized_amounts_are_rejected_without_a_write` (6 params: NaN, sNaN, Infinity, −Infinity, `1e400`, 400‑digit), `test_to_decimal_refuses_non_finite_and_out_of_range_values` (6: same + `10**30`), `test_bool_is_not_a_decimal`, `test_to_decimal_still_accepts_real_amounts` | **10 negative tests fail** pre‑fix; the "still accepts real amounts" control passes | **PARTIAL** — PR #7 has `_require_finite` (L21‑34) which rejects non‑finite on both paths, so 8 of the 10 pass; **no magnitude cap and no bool rejection** → `1e400`, 400‑digit, `10**30`, `bool` still fail |
| **SEC‑03** | `api.py` · `api_list_invoices` | `limit` outside 1…500 raised out of the handler as an unhandled 500, unlike every other list endpoint (which answers 422) | `except ValueError → 422` | `test_invoice_list_rejects_out_of_range_limit_with_422[0, -1, 501, 100000]`, `test_invoice_list_accepts_the_documented_boundaries` | 4 negative tests **fail** pre‑fix; boundary control passes | **STILL NEEDED** — PR #7's `api_list_invoices` (L1032) still has only the `AuthorizationError/TenantIsolationError` branch; all 4 fail upstream |
| **SEC‑04** | `procurement_stock.py` · `VALID_PAYMENT_METHODS` + pre‑write validation | an unsupported `payment_method` reached the `CHECK` constraint, producing `sqlite3.IntegrityError` → 500 | allowlist validation before the write → clean 422 | `test_unsupported_payment_method_is_a_422_not_a_500` | **fails** pre‑fix; on PR #7 the observed failure is literally `sqlite3.IntegrityError: CHECK constraint failed: payment_method IN ('CASH','BANK_TRANSFER','CHEQUE')` | **STILL NEEDED** |
| **SEC‑05** | `procurement_stock.py` · `_materialise_line_ht` / `compute_line_ht_dzd` | omitting the optional `line_ht_dzd` reached `round_dzd(None)` → `TypeError` → 500 | one shared derivation rule fills the value before validation | `test_line_without_declared_net_is_derived_not_a_500` | **fails** pre‑fix | **STILL NEEDED, and only half‑fixed upstream** — `procurement_stock.py:539` became `round_dzd(line.get("line_ht_dzd", expected_line_ht))`, but `compute_invoice_payload_hash` still does `format_dzd(line["line_ht_dzd"])` (L69), so an omitted key is now a `KeyError` instead of a `TypeError` (confirmed: the test fails on PR #7) |
| **SEC‑06** | `procurement_stock.py` · date/reference validation | `int(invoice_date[:4])` with `""` or garbage → `TypeError`/`ValueError` escaping as 500 with internal parser text; empty or 511‑char reference accepted | `datetime.strptime(str(invoice_date).strip(), "%Y-%m-%d")` → clean message; reference required and ≤ 128 chars | `test_invalid_invoice_date_is_rejected_without_leaking_internals[not-a-date, 2026-13-45, 2026-02-31, 0000-00-00, ""]` (5), `test_invalid_supplier_reference_is_rejected["", "   ", 129×F]` (3) | all 8 **fail** pre‑fix | **STILL NEEDED** — `fiscal_year = int(invoice_date[:4])` still at `procurement_stock.py:1161` (and `:1452` in the date‑change path); all 8 fail upstream |
| **SEC‑07** | `security_audit.py` · `create_encrypted_backup` | the encrypted backup (custody material for the vault and audit key rings) was written with the process umask — `0644` on a default Linux install | created `0600` via `os.open(..., 0o600)` (no readable window), `chmod` as belt‑and‑braces | `test_backup_archive_is_owner_only` | **fails** pre‑fix | **STILL NEEDED** — `security_audit.py:1054` is still `output_backup_path.write_bytes(encrypted_archive)` |
| **SEC‑08** | `api.py` · `_security_headers` middleware + `_configured_max_request_bytes` | every JSON endpoint, including the unauthenticated `/api/v1/auth/token`, buffered a body of any size before any rule ran | declared `Content-Length` above 2 MiB (JSON) / upload limit + 1 MiB (multipart) → **413 before authentication**; malformed env override ignored (fail‑closed) | `test_oversized_json_body_is_refused_before_authentication`, `test_oversized_authenticated_json_body_is_refused` | both **fail** pre‑fix | **STILL NEEDED** — PR #7 has no `content-length` handling anywhere (`rate_limit.py` and `api.py` were grepped: zero matches) |
| **SEC‑09** | `api.py` · `api_create_user` | a newline or other control character in a username forged log and audit lines; unbounded length bloated every attributed event | ≤ 64 chars, no `ord < 0x20` / `0x7F` | `test_username_with_control_characters_is_rejected` | **fails** pre‑fix | **STILL NEEDED** — PR #7's `api_create_user` (L1283) validates only non‑empty and password length |
| **SEC‑10** | `api.py` · `_security_headers` middleware | no browser‑facing hardening headers at all | `nosniff`, `no-referrer`, `DENY`, `no-store` on authenticated responses | `test_security_headers_are_present`, plus controls `test_api_documentation_is_disabled_by_default`, `test_no_cors_wildcard_is_served`, `test_spoofed_identity_headers_do_not_authenticate` | negative test **fails** pre‑fix; the three controls pass everywhere | **STILL NEEDED** — no security‑header middleware in PR #7 (its only `@app.middleware("http")` is at `api.py:302`, request logging + rate limiting) |

### 2.3 Positive controls (must keep passing, and do)

`test_document_reads_remain_tenant_isolated`, `test_sql_metacharacters_are_stored_as_data`, `test_mass_assignment_fields_are_ignored`, `test_api_documentation_is_disabled_by_default`, `test_no_cors_wildcard_is_served`, `test_spoofed_identity_headers_do_not_authenticate`, `test_upload_is_allowed_for_roles_the_rbac_matrix_permits` (4), `test_field_correction_is_allowed_for_clerk_and_audited`, `test_to_decimal_still_accepts_real_amounts`, `test_invoice_list_accepts_the_documented_boundaries` — **13 tests**, all passing before and after the fix, and all passing on PR #7.

### 2.4 Does the suite genuinely exercise the vulnerabilities?

The 51 tests were executed against the **pre‑fix** code (disposable copy of this tree with the remediation reverse‑applied; the only edit to the copy was an import fallback, because the published module hard‑imports `DEFAULT_MAX_JSON_REQUEST_BYTES`, which by definition does not exist before the fix):

```
38 failed, 13 passed
```

* **All 38 non‑control tests fail without the remediation.**
* **All 13 tests that pass without the remediation are positive controls** — not one negative test passes spuriously.
* Therefore no test in the module can be "passing for the wrong reason", and no finding is supported solely by a test that would also pass on vulnerable code.

The same module against **PR #7** (with a one‑line import fallback in the `/tmp` copy; the unmodified file cannot even import there):

```
27 failed, 24 passed
```

* 24 passing = 13 positive controls + **11 tests whose vulnerability PR #7 already closed** (3 field‑edit denials, 4 non‑finite invoice payloads, 4 non‑finite `to_decimal` inputs).
* 27 failing = the remediation work that is genuinely still outstanding upstream (§2.2).
* **As published, the test file cannot run against PR #7 at all**: `ImportError: cannot import name 'DEFAULT_MAX_JSON_REQUEST_BYTES' from 'dz_erp.api'` at collection. This is an integration defect to fix (§3.6), not a test failure.

---

## 3. Compatibility matrix vs PR #7

### 3.1 Structural differences that matter

| Dimension | This branch (`4fc1364a`) | PR #7 (`5a3e47dd`) | Consequence |
|---|---|---|---|
| `dz_erp/api.py` | 1 049 lines | 1 347 lines, route bodies moved by ~+180 lines; new imports `net`, `request_id`, `rate_limit`, `review_policy` | every hunk anchor of the remediation has moved; **textual conflicts in 4 of the 6 api hunks** |
| `dz_erp/security_audit.py` | 1 310 lines | 1 563 lines | `require_role_secure` insertion point moved; `create_encrypted_backup` signature and body changed |
| `dz_erp/procurement_stock.py` | implementation inline in the public function | **public/private split**: `create_and_validate_supplier_invoice` (L1084) is a thin wrapper delegating to `_create_and_validate_supplier_invoice` (L1139), plus `_RefusedAttempt` (L275) / `_record_refused_attempt` | the SEC‑04/05/06 patch must be **retargeted** into the private function; the public function must **not** be patched |
| `dz_erp/decimal_utils.py` | 92 lines after the patch (61 before), `to_decimal` unguarded originally | 77 lines, `_require_finite` already present | SEC‑02 must be re‑expressed **inside** `_require_finite`, not added as a parallel `_guarded` |
| New modules upstream | — | `dz_erp/net.py`, `dz_erp/request_id.py`, `dz_erp/rate_limit.py`, `dz_erp/review_policy.py` | SEC‑12 rate limiting is already implemented upstream; SEC‑15 (Host validation) partially addressed by the `net`/trusted‑proxy policy |
| Tests | 32 modules (incl. the new one) | 62 files / 60 phase modules (phase29 … phase57) | 27 upstream test modules are unavailable locally; they must run as the acceptance gate for the ported patches |
| Docs | 6 untracked `docs/security/*` | **no `docs/security/` directory** | docs merge cleanly, but nothing in PR #7 references them |

### 3.2 Conflict classification per hunk

| Patch hunk | Target in PR #7 | Classification |
|---|---|---|
| `api.py` import of `require_role_secure` | `api.py:80` `from .security_audit import (...)` (12 names upstream) | **textual conflict**, trivial |
| `api.py` module constants block | after `RevokeRequest` class | **textual conflict**, trivial (same region moved) |
| `api.py` `_security_headers` middleware | `api.py:302` is PR #7's only `@app.middleware("http")` | **semantic conflict**: adding a second `http` middleware requires an explicit ordering decision (see §6‑C2) |
| `api.py` `api_upload_document` guard | `api.py:585` handler, new form field `declared_language` (L592, passed at L633) | **textual conflict**; guard must go **after** the `_authenticate` dependency resolves and **before** the first body read |
| `api.py` `api_edit_extracted_field` guard | `api.py:681`; upstream already enforces the same matrix in `intake_ocr.py:1620` | **semantic duplication** — keep only one enforcement point (§6‑C3) |
| `api.py` `api_list_invoices` `except ValueError` | `api.py:1032`, `try` at L1040‑1043 | **clean insert**; matches the upstream precedent `GET /api/v1/accounts?limit=0|501|abc → 422` (`tests/test_phase25_read_api.py:255‑257`) |
| `api.py` `api_create_user` username rule | `api.py:1283`, after the empty‑username check | **clean insert**; upstream keeps the length‑only password rule (SEC‑13 still open there) |
| `decimal_utils.py` `_guarded` + `to_decimal` | `decimal_utils.py:21‑34 _require_finite`, `to_decimal` L36 | **textual + semantic conflict**: same function, two competing guard helpers |
| `procurement_stock.py` imports/constants + helpers | after `procurement_stock.py` imports (L12‑16 upstream) | **textual conflict**, trivial |
| `procurement_stock.py` request‑shape validation | `_create_and_validate_supplier_invoice` L1139, body from L1157 | **semantic conflict**: the patch's anchor `fiscal_year = int(invoice_date[:4])` now sits at L1161 inside the *private* function, and the wrapper's `except _RefusedAttempt` must not be bypassed |
| `procurement_stock.py` `_materialise_line_ht` call + `line["line_ht_dzd"]` | L539 upstream already uses `.get(...)` | **semantic conflict**: upstream's fix is incomplete (`compute_invoice_payload_hash` L69 still subscripts) — the materialisation must be applied **before** the hash call, not just before the loop |
| `security_audit.py` `require_role_secure` | after `list_stock_quants_secure` | **clean insert**; `ERPDatabase.append_audit_event` signature is **identical** upstream (`database.py:1671`, same 10 parameters) → the helper is portable as written |
| `security_audit.py` backup `0600` | `security_audit.py:1054`, inside `create_encrypted_backup` whose signature changed (`backup_encryption_key=None` + late resolution, L974‑982) | **textual conflict** (3‑line context), semantically **clean** |
| `tests/test_phase34_security_validation.py` | absent upstream | **clean add**, but needs the import fix (§3.6) and a full run against upstream code |
| `docs/security/*.md` | absent upstream | **clean add** |

### 3.3 Changed signatures and API contracts

| Symbol | This branch | PR #7 | Integration impact |
|---|---|---|---|
| `upload_and_process_supplier_document` | `(db, vault, company_id, user_id, filename, mime_type, content)` | `(…, branch_id=None, force_image_ocr=False, declared_language=None)` (`intake_ocr.py:1339`) | the endpoint must forward the new form field; the SEC‑01 guard belongs in the **endpoint**, so the signature change is harmless — but any test or script that calls the service directly still works (defaults) |
| `create_and_validate_supplier_invoice` | implementation, `(…, amount_timbre_dzd="0.00", payment_method="BANK_TRANSFER", po_id=None, grn_id=None, document_id=None)` | wrapper with **two extra** parameters `price_tolerance_dzd="0.00"`, `branch_id=None` (`:1084`), delegating to `_create_and_validate_supplier_invoice` (`:1139`) | SEC‑04/05/06 patches must target the private function; the wrapper's refusal machinery must be preserved |
| `update_extracted_field_by_human` | `(db, company_id, user_id, document_id, field_name, new_value)` + 3‑way role check upstream | same signature | no patch needed for the service; the endpoint guard is already redundant upstream |
| `create_encrypted_backup` | `(db, vault, output_backup_path, backup_encryption_key: bytes)` — required | `(…, backup_encryption_key=None)` — optional, resolved inside (`:978‑982`) | callers passing the key are unaffected; the `0600` fix must be applied to the new body |
| `api_upload_document` contract | multipart `file` only | adds optional form field `declared_language` (`:592`) | new *optional* field, backwards compatible |
| New endpoints upstream | — | `/api/v1/rate-limit/policy`, request‑id echo, review‑policy endpoints | SEC‑12 is **already delivered upstream**; no patch required here |
| Error contract | 403 / 413 / 422 as described above | 403 / 422 already used; **no 413** anywhere | introducing 413 is new but additive |
| Audit event names | new `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED` (already the upstream idiom) | same name used by `intake_ocr.py:1620‑1632` and `security_audit.py` read guards | consistent — but a duplicated enforcement point writes the event twice in different actions (§6‑C3) |

### 3.4 Schema / migration differences

None for the remediation: every change is application‑level (validation, headers, file mode, role gate). PR #7 does add schema and modules, but `ERPDatabase.append_audit_event` — the only persistence API the new helper uses — has an **identical signature** in both trees (`database.py:1090` local vs `:1671` PR #7), so no migration is implied by `require_role_secure`.

### 3.5 Auth / RBAC differences

* PR #7 already enforces the intake matrix on **field review** (`REVIEW_CAPABLE_ROLES`, `intake_ocr.py:188`, `:1620`, `:1784`) and on financial reads; it does **not** enforce it on **upload**.
* PR #7 adds rate limiting, request correlation IDs, trusted‑proxy handling — none of which exist here. The remediation's `_security_headers` middleware is the only header/body topology change, and it must coexist with PR #7's single `http` middleware.
* The remediation's `DOCUMENT_INTAKE_ROLES` **duplicates** `REVIEW_CAPABLE_ROLES` with the same four values. Two constants for one policy row is a divergence risk (they can drift).

### 3.6 Accounting / inventory side effects

* `fiscal_year` derivation changes from a string slice to a parsed date (`invoice_day.year`). For every *valid* `YYYY-MM-DD` input the result is identical; for invalid input the failure becomes a 422 instead of a 500. No postings, no amounts, no tax rule changes.
* `_materialise_line_ht` changes **which value is hashed** for an invoice whose `line_ht_dzd` is omitted: previously a `TypeError`; upstream, a `KeyError`; after the fix, the derived amount enters `compute_invoice_payload_hash`. This is a deliberate **payload‑hash** semantic: no existing invoice's hash changes (the function only fills a previously impossible input), but any external cache or comparison of "hash of the request as sent" would need to know it.
* `to_decimal` now rejects `bool` and magnitudes above `10**18`. This is a **global** behaviour change for every caller (API, decision service, backup verifier). It is the intended fix, but it is the single highest‑risk change for upstream code that might pass a bool or a large sentinel. See §6‑C4.
* The payment‑method allowlist must stay exactly the three values of the schema `CHECK` (`CASH`, `BANK_TRANSFER`, `CHEQUE`); a future migration that adds a method must update both.

### 3.7 Tests relying on details unavailable in PR #7

1. **Import‑level**: `from dz_erp.api import DEFAULT_MAX_JSON_REQUEST_BYTES` — hard failure upstream (verified). Port as a local constant or add the constant in the same patch.
2. **Constants**: `MAX_ABSOLUTE_EXPONENT`, `VALID_PAYMENT_METHODS`, `compute_line_ht_dzd` are imported/used by the test module only indirectly (through behaviour), so they survive; but the test asserting `bool` rejection depends on a semantic PR #7 does not have.
3. **Fixtures**: the module seeds users with fixed ids `1‑7` and `20` and builds the app with `create_erp_fastapi_app(db, vault, auth_manager=…)` — compatible (24/51 tests executed successfully), but PR #7's own conftest has additional fixtures (`complete_document_field_review`) that the new module does not use; if the module is folded into PR #7's suite, prefer PR #7's fixtures.
4. **Phase numbering**: PR #7 already uses `phase34` for `test_phase34_ocr_duplicate_row_integrity.py`. Two `phase34` modules with different purposes will confuse the phase register and any docs‑consistency test (`tests/test_phase22_register_and_docs_consistency.py`). Rename on integration.
5. **20 restore‑path tests** in this suite fail on this interpreter (§5) — they are not available evidence in either tree until the interpreter floor is met.

---

## 4. Dependency advisory inventory (SEC‑11)

**Method:** `pip-audit --local --format json` (2.10.1) against a venv installed from *this branch's* `requirements.lock.txt` (51 pins), and against a venv installed from *PR #7's* `requirements.lock.txt` (53 pins). `pip` and `setuptools` are **not pinned in either lock file**; they are the venv's bundled tooling and are reported separately.

### 4.1 This branch's lock — 8 application dependencies, 143 advisories

| Package | Installed | Advisories | Representative IDs | Minimum fix versions | Direct / transitive | Reachability |
|---|---|---|---|---|---|---|
| `pillow` | 11.1.0 | **53** | `CVE-2026-25990`, `CVE-2026-42310`, `CVE-2026-42311`, `CVE-2026-55379`, `CVE-2026-59205`, `GHSA-cfh3-3jmp-rvhc` … | 12.1.1 → 13.0.0 | transitive (pdfplumber / OCR image path) | **High** — every uploaded image/PDF raster goes through it |
| `pypdf` | 5.4.0 | **67** | `CVE-2025-55197`, `CVE-2026-22690`, `CVE-2026-41168`, `CVE-2026-119203`, `CVE-2026-119205`, `GHSA-5849-834g-hvw5` … | 6.0.0 → 6.21.4 | direct | **High** — parser for untrusted supplier PDFs, some issues are infinite loops / memory growth (DoS) |
| `starlette` | 0.46.2 | 7 | `CVE-2025-54121`, `CVE-2025-62727`, `CVE-2026-48710` (Host header), `CVE-2026-48817`, `CVE-2026-48818`, `CVE-2026-54282`, `CVE-2026-54283` | 0.47.2 → 1.3.1 | transitive (fastapi) | **High** — HTTP layer; `CVE-2026-48710` is the Host‑header reconstruction issue that overlaps SEC‑15 |
| `python-multipart` | 0.0.20 | 6 | `CVE-2026-24486` (path traversal), `CVE-2026-40347`, `CVE-2026-42561`, `CVE-2026-53538`, `CVE-2026-53539`, `CVE-2026-53540` | 0.0.22 → 0.0.31 | direct (multipart upload parsing) | **High** — unauthenticated multipart parsing on the upload endpoint |
| `cryptography` | 44.0.2 | 6 | `CVE-2026-26007`, `CVE-2026-34073`, `CVE-2026-69247`, `CVE-2026-69248`, `CVE-2026-69249`, `GHSA-537c-gmf6-5ccf` | 46.0.5 → 50.0.0 | direct (Fernet, vault, audit HMAC) | **Medium** — the paths used here are symmetric Fernet/HMAC, not the affected X.509/PKCS7 APIs; the wheel‑bundled OpenSSL advisory is relevant to TLS termination |
| `pdfminer.six` | 20250327 | 2 | `CVE-2025-64512` (pickle RCE via malicious PDF), `CVE-2025-70559` | 20251107 → 20251230 | transitive (pdfplumber) | **High** — the RCE is exactly the upload threat model |
| `certifi` | 2026.7.22 | 1 | `PYSEC-2026-4320` / `GHSA-6q48-6c7w-9gw2` | **2026.10.18** | transitive (httpx) | Low — outbound TLS trust store; fix version reported by pip‑audit is exactly `2026.10.18` |
| `pytest` | 8.3.5 | 1 | `CVE-2025-71176` / `GHSA-6w46-j5rx-g56g` | 9.0.3 | dev dependency | Low — local `/tmp/pytest-of-{user}` predictability, test host only |

Environment tooling, **not in either lock**: `pip 23.0.1` (7 advisories), `setuptools 66.1.1` (4 advisories, `CVE-2024-6345`, `CVE-2025-47273`, `CVE-2026-59890`, `GHSA-xmj3-9gf7-h52w`). These come from `python3 -m venv` defaults; they are build/install‑time only and are not shipped with the application.

### 4.2 PR #7's lock — the advisories are gone

| Package | This branch | PR #7 | Advisory coverage |
|---|---|---|---|
| `fastapi` | 0.115.12 | **0.133.0** | (no advisory in either) |
| `starlette` | 0.46.2 | **1.3.1** | all 7 fixed (1.3.1 is the max fix version) |
| `python-multipart` | 0.0.20 | **0.0.31** | all 6 fixed |
| `pypdf` | 5.4.0 | **6.21.4** | all 67 fixed |
| `pillow` | 11.1.0 | **13.0.0** | all 53 fixed |
| `pdfminer.six` | 20250327 | **20251230** | both fixed |
| `cryptography` | 44.0.2 | **50.0.0** | all 6 fixed |
| `certifi` | 2026.7.22 | **2026.10.18** | fixed (equals the reported fix version) |
| `pytest` | 8.3.5 | **9.0.3** | fixed |
| `pdfplumber` | 0.11.6 | 0.11.9 | — |
| `httpx`, `uvicorn`, `SQLAlchemy` | 0.28.1 / 0.34.0 / 2.0.40 | identical | — |

Result of `pip-audit --local` on PR #7's runtime: **only `pip` and `setuptools` (the venv's bundled tooling) remain**, i.e. **zero application advisories**. PR #7 therefore **resolves SEC‑11 in full**.

### 4.3 What that means for this branch

* `pyproject.toml` and `requirements.lock.txt` are **unmodified** on this branch (`git status` confirms). **SEC‑11 cannot be closed by this branch's own changes** — it is closable only by adopting PR #7's dependency set (or an equivalent bump on a supported interpreter).
* `requires-python` is **identical** in both trees: `>=3.11.17,<3.13,!=3.12.0…!=3.12.9`. So the dependency work does not change the interpreter floor, and the floor problem (§5) stays.
* Expected lock impact of adoption: `requirements.lock.txt` 51 → 53 pins; `requirements.lock.hashes.txt` (110 KB) must be **regenerated** with the project's own `generate_hash_lock` tool on a supported interpreter; `fastapi`/`starlette` jump two minor/major series, so **every route's middleware topology and error behaviour must be re‑verified** (§6‑C2).
* `certifi 2026.10.18` is the pip‑audit fix version, but the advisory text says the eMudhra roots were removed *in* 2026.10.18 and recommends 2026.10.19+. Both trees would be pinned to the boundary version; if a later audit re‑flags it, bump rather than lower.

---

## 5. Test environment

### 5.1 Interpreter

| Item | Value |
|---|---|
| Interpreter actually available | CPython **3.11.2** (`main, Apr 8 2026, GCC 12.2.0`) |
| Project floor (`pyproject.toml`, unchanged in both trees) | `>=3.11.17,<3.13` excluding 3.12.0–3.12.9 |
| Supported targets | 3.11.17+ and 3.12.10+ (and below 3.13) |

### 5.2 Full suite on this branch (reproduction of the phase‑1 baseline)

```
pytest -q --junitxml=/tmp/recon_junit.xml
748 collected → 20 failed, 722 passed, 6 skipped, 1 warning in 119.86 s
```

The result is **identical** to the phase‑1 baseline and to the post‑fix run: the remediation changed no other outcome.

### 5.3 Why exactly 20 tests fail — blocked, not broken

All 20 failures are the **same fail‑closed interpreter gate**:

```
dz_erp/security_audit.py:1087: in restore_and_verify_backup
E  dz_erp.database.FinancialIntegrityError:
   Safe backup restore requires Python 3.11.17+ or Python 3.12.10+
   (and below 3.13); unsafe archive member types and paths are rejected.
```

The gate exists because `tarfile`'s safe‑extraction behaviour for the archive member types used here (`filter="data"` semantics, hardlink/symlink path rejection) is only dependable on patched interpreters. On 3.11.2 the library **refuses to restore at all** — which is the correct, fail‑closed behaviour, not a defect.

| Failing module | Tests | Why |
|---|---|---|
| `tests/test_phase11_audit_key_rotation.py` | 3 | restore path gated |
| `tests/test_phase12_key_migration_and_backup.py` | 10 | restore/tampered‑archive path gated |
| `tests/test_phase5_security_audit_backup.py` | 1 | restore path gated |
| `tests/test_phase6_ocr_benchmark_e2e.py` | 1 | the 14‑step end‑to‑end workflow includes backup + restore (line 290) → gated |
| `tests/test_phase8_upload_boundary_and_ci.py` | 2 | backup/restore assertions inside those tests |
| `tests/test_phase9_process_boundary.py` | 3 | restore from a fresh subprocess / idempotent restore |

**Classification:** *blocked by environment* (interpreter below the project's own floor). They are **not** regressions, **not** flaky, and **not** evidence of any remediation defect. Equally, they are **not** passing tests and must not be counted as such anywhere.

### 5.4 Skipped (6) — and what is unavailable

| Test | Reason |
|---|---|
| `tests.test_phase27_desktop_ui` | collection skipped (module level) |
| `tests.test_phase28_desktop_users_ui` | collection skipped (module level) |
| `tests.test_phase26_desktop::test_mainwindow_constructs_and_switches_to_rtl` | `could not import 'PySide6.QtWidgets'` |
| `tests.test_phase12_key_migration_and_backup::test_restore_refuses_existing_windows_junction_prefix` | Windows‑only |
| `tests.test_phase17_key_providers::test_windows_provider_refuses_to_follow_a_symlinked_key` | Windows reparse‑point path |
| `tests.test_phase17_key_providers::test_windows_provider_rejects_reparse_handle_without_symlink_privilege` | Windows reparse‑point API |

Unavailable in this environment, therefore **NOT VERIFIED**:

* **Docker / Compose** — `docker: command not found`; no container, staging, or compose‑based test can run. The project's staging/service checks remain unexercised here.
* **Network listeners** — ports 18002 / 18003 / 8000 closed; no live deployment reachable.
* **MinIO / S3** — not present; object‑storage paths untested.
* **AWS KMS** — no credentials, no test CMK; live verification stays **NOT VERIFIED** (Phase‑1 position unchanged).
* **PySide6** — absent, so the three desktop tests cannot run.
* **Second interpreter** — no 3.11.17+ or 3.12.10+ available, so no test can be re‑run under a supported Python *in this sandbox*.

### 5.5 Which tests can be re‑run under a supported Python

**All 748.** Under CPython 3.12.10+ (or 3.11.17+) the 20 restore‑path tests are expected to execute and, per phase‑1 evidence, pass; the remaining 722 already pass here. That run is a **precondition** for integration, not an optional extra: until it happens, the backup/restore surface — which the SEC‑07 fix touches — is untested end‑to‑end.

---

## 6. Potential conflicts (integration order matters)

**C1 — `api.py`: four of six hunks are textual conflicts.** Route bodies moved by ~+180 lines and the import block gained `net`, `request_id`, `rate_limit`, `review_policy`. The hunks that touch `api_upload_document` (L585), `api_edit_extracted_field` (L681), `api_list_invoices` (L1032) and `api_create_user` (L1283) cannot be applied by context; they must be re‑authored against the upstream text. **No automatic resolution.**

**C2 — a second `@app.middleware("http")` has no defined order.** PR #7 has exactly one (L302: request logging + rate limiting). Starlette makes the **last added** middleware the outermost, so a naive append places `_security_headers` *outside* the logger: 413 responses would not be request‑logged, and header hardening would not be applied to 429 responses produced by the rate limiter. Requirements to settle explicitly: (a) hardening headers on **every** response including 413/429; (b) body cap evaluated before authentication but **logged**; (c) rate limiting still first for abusive clients; (d) PR #7's `request_id` still echoed on 413/429.

**C3 — duplicated RBAC enforcement on field edits.** PR #7 already refuses denied roles inside `update_extracted_field_by_human` (`intake_ocr.py:1620`), auditing `SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED` with `required_roles`. Keeping the endpoint guard as well produces two different refusal descriptions for the same policy row and, if the order is ever swapped, a double audit decision. Recommended: **drop the endpoint guard**, keep the service check, and keep the test (it passes either way — verified on PR #7). The **upload** guard has no upstream equivalent and must be added.

**C4 — `decimal_utils.py`: two guard helpers.** PR #7's `_require_finite` (L21‑34) already rejects non‑finite on both the `Decimal` and `str` paths but has **no magnitude cap** and **no bool rejection**. The remediation adds a parallel `_guarded`. If both land, one conversion point has two policies. Recommended: extend `_require_finite` with `adjusted() > 18`, add the bool branch, and delete `_guarded`. Risk to watch: rejecting `bool` is a **global** semantic change; PR #7's 27 new test modules have never run against it.

**C5 — `procurement_stock.py` public/private split.** The patch targets `create_and_validate_supplier_invoice`, which upstream is a **wrapper** (`:1084`) around `_create_and_validate_supplier_invoice` (`:1139`) with `_RefusedAttempt` handling. The validation must go inside the **private** function, and the wrapper's `except _RefusedAttempt: _record_refused_attempt(...); raise` must be left intact. Good news, verified: `_RefusedAttempt` is a `FinancialIntegrityError` subclass only, so a `ValueError` raised by the new validation propagates untouched to the handler's existing `except (ValueError, FinancialIntegrityError) → 422` (`api.py:787`).

**C6 — SEC‑05 is half‑fixed upstream, in a way that turns a 500 into a different 500.** `procurement_stock.py:539` now uses `line.get("line_ht_dzd", expected_line_ht)`, but `compute_invoice_payload_hash` still does `format_dzd(line["line_ht_dzd"])` (L69) and two further sites subscript the key (L2416, L3159). Materialisation must therefore run **before the hash is computed**, or the `TypeError` merely becomes a `KeyError`. Any port that copies only the L539 idiom will appear to work and still return 500.

**C7 — `security_audit.py`: backup fix against a changed function.** `create_encrypted_backup` upstream (L974) makes `backup_encryption_key` optional and resolves it inside; the docstring now sits *after* that branch (L982). The three‑line context of the `0600` hunk no longer matches. Semantically trivial, textually incompatible.

**C8 — body cap vs the existing upload bound.** This branch caps the upload path at `configured_max_upload_bytes() + 1 MiB` (RFC 7578 per‑part framing). PR #7's upload reader (`dz_erp/upload.py`, `read_bounded_upload`) already enforces the real limit and closes the file on failure; PR #7's test asserts `413` when `ERP_MAX_UPLOAD_BYTES = len(content) − 1`. With the new middleware, the same request now trips the reader (still 413) but a *slightly* larger one trips the middleware first, with a different body/headers and no `upload_file_is_closed` observation. Both are 413, but a test asserting *which* layer answered would be brittle. Also: the cap reads only `Content-Length`; a chunked request bypasses it — that must be documented as proxy‑dependent, not silently assumed covered.

**C9 — username rule may collide with upstream user administration.** PR #7's `api_create_user` (L1283) and its `tests/test_phase28_user_admin.py` use short synthetic names, so no collision is visible; the 64‑character cap is nonetheless new and should be checked against the desktop user screens (three of those tests are skipped here).

**C10 — dependency adoption is a code‑plus‑lock change, not a version bump.** Going from `fastapi 0.115` / `starlette 0.46` to `0.133` / `1.3.1` changes middleware internals, `request.form()` limits (`max_fields`, `max_part_size` in 1.3.1) and Host/path handling. The 413/429 topology (C2) and every multipart path must be re‑verified under the new pair. `requirements.lock.hashes.txt` must be regenerated by the project's own tooling.

**C11 — SEC‑12 and SEC‑11 are no longer "open risks" of the merged product.** PR #7 ships `dz_erp/rate_limit.py` + 429 + `Retry-After` + `/api/v1/rate-limit/policy` (SEC‑12 implemented) and a clean advisory set (SEC‑11 resolved). The six `docs/security/` reports describe them as open/accepted. **After integration those two entries must be rewritten**, or the documentation set becomes actively misleading.

**C12 — SEC‑14 narrows.** PR #7's `update_extracted_field_by_human` still writes `human_override_value` and `normalized_value` without type/format validation, so SEC‑14 stays open — but PR #7's phase‑29 review binding closes values behind a decided invoice, so the unvalidated window is limited to pre‑decision fields. The residual‑risk wording must be updated to say exactly that.

**C13 — phase‑34 naming collision.** Two distinct modules would both claim phase 34. The docs/register consistency test (`tests/test_phase22_register_and_docs_consistency.py`) passes today and must keep passing after the rename.

---

## 7. Recommended integration sequence

Nothing below is executed in this phase. The sequence is ordered so that every step is verifiable, and so that no step depends on an unresolved conflict.

1. **Close the evidence gaps first.** Confirm with the owner whether the "10 user modifications" exist in a workspace that can be provided; if not, proceed on the explicit assumption that they do not exist. Record the decision.
2. **Establish a supported interpreter.** Provision CPython 3.12.10+ (or 3.11.17+) and re‑run the full suite on this branch: expect `742 passed / 6 skipped / 0 failed`. This converts the 20 restore‑dependent results from *blocked* to *evidence*, and it is a precondition for trusting the SEC‑07 port.
3. **Snapshot, don't merge.** Preserve the remediation as a reviewable patch series (`git diff` as it stands + the new test module + `docs/security/`), so it can be re‑authored file by file rather than applied blindly.
4. **Create an integration branch from PR #7's head** (`5a3e47dd`), never from local `main`. PR #7 is 106 commits ahead and clean against its own base; starting anywhere else re‑imports 26 970 lines of drift.
5. **Port `decimal_utils.py` first** (smallest, most semantic): extend `_require_finite` with the magnitude cap, add the bool branch, drop the parallel helper. Run the unmodified test module's SEC‑02 group against it.
6. **Port `procurement_stock.py`** into `_create_and_validate_supplier_invoice`, preserving the wrapper's `_RefusedAttempt` path, and **materialise `line_ht_dzd` before the payload hash** (C6). Replace the L539 idiom accordingly. Run the SEC‑04/05/06 groups.
7. **Add `require_role_secure` to `security_audit.py`** (portable as‑is — `append_audit_event` is unchanged) plus the `0600` backup write in the *new* `create_encrypted_backup` body. Run the SEC‑07 group.
8. **Re‑author the `api.py` hunks** against upstream text in this order: constants → `_security_headers` middleware (with step 9's ordering decision) → upload guard (new policy, keep) → drop the redundant field‑edit guard (C3) → `api_list_invoices` 422 → `api_create_user` username rule. Run the SEC‑01 (upload half), SEC‑03, SEC‑08, SEC‑09, SEC‑10 groups after each hunk, not at the end.
9. **Settle middleware order explicitly** (C2): define which `http` middleware is outermost, assert 413/429 both carry the hardening headers and a request id, and add a test for that topology — this is new behaviour with no upstream precedent.
10. **Adopt PR #7's dependency set wholesale** (`pyproject.toml`, `requirements.lock.txt`), regenerate `requirements.lock.hashes.txt` with the project's tooling, then re‑run `pip-audit` (expect only `pip`/`setuptools` bundled‑tooling entries) and re‑run the full suite under the new `fastapi`/`starlette` pair.
11. **Fold the tests in properly**: fix the module's `DEFAULT_MAX_JSON_REQUEST_BYTES` import (C‑§3.7‑1), rename it off phase 34, prefer PR #7's fixtures, and run it against the integrated tree. Only then re‑run the 27 upstream phase‑29…57 modules as the regression gate.
12. **Rewrite the affected documentation**: SEC‑11 → *resolved by the dependency adoption*; SEC‑12 → *implemented upstream (rate limiting, 429, policy endpoint)*; SEC‑14 → *narrowed by phase‑29 review binding*; SEC‑15 → *partially addressed by upstream `net`/trusted‑proxy policy, TLS still a deployment requirement*. Update the validation summary and the reconciliation status.
13. **Only after 1–12: run the real integration** (merge of PR #7, or a rebased port) with the 20 restore tests green and no new failures in the 27 upstream modules.

**Hard constraints for whoever executes it:** no `git reset --hard`, `clean`, `stash`, history rewrite or force push; no automatic conflict resolution; no test edits to make results green; no weakening of fail‑closed gates; no database/volume destruction; no live KMS/MinIO claims without credentials; and no "PASS" for anything that did not actually run.

---

## 8. Files affected by a future integration

### 8.1 Files this branch would contribute / modify

| Path | Δ | Note |
|---|---|---|
| `dz_erp/api.py` | modified (+122) | **4 textual conflicts**; middleware ordering decision required |
| `dz_erp/decimal_utils.py` | modified (+37/−6 region) | merge into `_require_finite`; delete `_guarded` |
| `dz_erp/procurement_stock.py` | modified (+67) | retarget to `_create_and_validate_supplier_invoice` |
| `dz_erp/security_audit.py` | modified (+67) | `require_role_secure` clean; backup hunk re‑authored |
| `tests/test_phase34_security_validation.py` | **new** (525 lines, 51 tests) | rename; drop hard import; 27/51 currently fail on PR #7 by design |
| `docs/security/SECURITY_ASSESSMENT_REPORT.md` | new (22 080 B) | absent upstream |
| `docs/security/SECURITY_FINDINGS_REGISTER.md` | new (32 563 B) | **must be rewritten for SEC‑11/12/14/15 after integration** |
| `docs/security/SECURITY_REMEDIATION_LOG.md` | new (13 214 B) | — |
| `docs/security/SECURITY_TEST_EVIDENCE.md` | new (21 605 B) | add the integration run |
| `docs/security/SECURITY_RESIDUAL_RISKS.md` | new (7 651 B) | **SEC‑11/SEC‑12 entries superseded** |
| `docs/security/SECURITY_VALIDATION_SUMMARY.md` | new (12 586 B) | status must change |
| `docs/security/SECURITY_RECONCILIATION_REPORT.md` | new (this file) | audit artefact |

### 8.2 Files an integration must take from PR #7 (unchanged here)

| Path | Why |
|---|---|
| `pyproject.toml`, `requirements.lock.txt`, `requirements.lock.hashes.txt` | SEC‑11 resolution (53 vs 51 pins; hashes must be regenerated) |
| `dz_erp/rate_limit.py`, `dz_erp/request_id.py`, `dz_erp/net.py`, `dz_erp/review_policy.py` | new upstream modules the merged code will import (SEC‑12, SEC‑15, phase‑29 review) |
| `dz_erp/intake_ocr.py`, `dz_erp/database.py`, `dz_erp/upload.py`, `dz_erp/localization.py` | upstream changes the ported patches must be compatible with (signatures cited in §3.3) |
| `tests/test_phase29…test_phase57` (27 modules) | the regression gate for the ported patches; **absent locally** |

### 8.3 Files that would be touched by the ported patches inside PR #7's tree

`dz_erp/api.py`, `dz_erp/decimal_utils.py`, `dz_erp/procurement_stock.py`, `dz_erp/security_audit.py`, `tests/test_phase34_security_validation.py` (renamed), `docs/security/*` (7 files) — i.e. **the same 5 code/test files plus documentation**, with all references recomputed against `5a3e47dd` line numbers.

---

## 9. Final status

### **READY WITH BLOCKERS**

The remediation itself is sound, is proven by execution rather than assertion (38/38 negative tests fail without it, all 13 controls pass with and without it), and remains almost entirely applicable: **seven of the ten fixed findings and two halves of an eighth are still missing from PR #7**. The provenance situation is unambiguous (local HEAD is a strict ancestor of PR #7's head, merge base = local HEAD, nothing committed locally). Nothing here requires abandoning the work.

It is **not** ready for a merge today, because of these blockers:

| # | Blocker | Why it stops integration |
|---|---|---|
| **B1** | **Uncommitted work with no provenance** — the entire remediation exists only as a working‑tree diff; the "10 user modifications" are not identifiable in any accessible workspace | the changes cannot be reviewed, cherry‑picked, or reverted by SHA; re‑supply or formally declare them non‑existent |
| **B2** | **Four textual conflicts in `api.py`** plus changed signatures in `procurement_stock.py` (public/private split), `decimal_utils.py` (`_require_finite`) and `security_audit.py` (`create_encrypted_backup`) | automatic application is impossible; each hunk must be re‑authored (no auto conflict resolution is permitted) |
| **B3** | **Undefined middleware topology** — the remediation introduces a second `@app.middleware("http")`; PR #7 has one (logging + rate limiting) | header coverage of 413/429 and request‑id propagation must be *decided and tested*, not inherited |
| **B4** | **20 test results are environment‑blocked** — 20 restore/backup tests fail closed on CPython 3.11.2, and the SEC‑07 fix touches exactly that surface | the backup‑permission fix has never been exercised end‑to‑end; a supported interpreter (3.11.17+/3.12.10+) is a precondition |
| **B5** | **Dependency adoption is a framework jump** — `fastapi 0.115 → 0.133`, `starlette 0.46 → 1.3.1` (SEC‑11 requires it; this branch cannot fix SEC‑11 by itself) | multipart, form limits, Host/path handling and middleware internals all change; needs its own verification pass and lock‑hash regeneration |
| **B6** | **27 upstream test modules are unavailable locally** (phase 29–57) | the real acceptance gate for the ported patches cannot be executed in this workspace |
| **B7** | **Documentation drift** — SEC‑11 and SEC‑12 are described as open/accepted in the reports but are resolved/implemented in PR #7; SEC‑14 and SEC‑15 change shape | merging without rewriting the register and the summary would publish inaccurate security statements |

**Not safe to integrate** would be the wrong verdict: no finding is unsound, no test is spurious, no security control would be weakened, and no automatic resolution is required. **Ready for controlled integration** would be the wrong verdict too: B1–B7 must be cleared, in the order given in §7.

*End of report. No code, test, dependency, branch, PR, database, or Docker resource was modified in the preparation of this document.*
