# Phase 3 — Integration Preservation, Environment Recovery and Release-Blocker Closure

**Date:** 2026-11-09
**Role:** Principal Release Engineer / Python Packaging Specialist / AppSec Lead
**Subject:** PR #7 (`arena/7f12d627-erp` @ `5a3e47ddfba2b789bcff71d6438db4927f5c7986`) — selective security integration
**Starting status:** SELECTIVE INTEGRATION PARTIAL — BLOCKERS REMAIN (Phase 2)
**Final status:** **PRESERVED — BLOCKERS REMAIN**

> This is **not** a production-readiness statement. The integration is preserved,
> reproducible and independently re-verified, but it is still uncommitted and
> unreviewed, it has never run on a supported interpreter, and four of the five
> remaining release blockers (B1, B3, B4, B5) are untouched by design because
> they need an environment or a decision that this one cannot supply.

---

## 0. Executive summary

| # | Objective | Outcome |
|---|---|---|
| 1 | Preserve the integration result (highest priority) | **DONE** — patch + untracked file + checksums + baseline + reproduction report, in `/home/user/integration-preservation/` (see §1) |
| 2 | Reproduce the integrated state exactly | **DONE** — patch applies cleanly to a fresh clone of `5a3e47dd`; 297-file manifest identical; 6/6 file hashes identical; 91P/1S reproduced |
| 3 | Recover a supported Python (≥3.11.17 / 3.12.10+) | **BLOCKED** — only CPython 3.11.2 exists; no version manager; download not authorised |
| 4 | Wheel build failure (`Multiple top-level packages … flat-layout`) | **ROOT-CAUSED + SMALLEST SAFE FIX VERIFIED IN ISOLATION**; kept separate from the security patch, not applied to either workspace |
| 5 | SEC-13 / SEC-14 / SEC-15 review | **DONE** — evidence-based reviews with explicit recommendations; **nothing implemented** |
| 6 | Validation plan for Windows / desktop / Docker / KMS / MinIO | **PARTIAL** — vault executed (23 passed); desktop blocked by missing PySide6; Windows/Docker/MinIO not executable here; **AWS KMS is not implemented in this build at all** |
| 7 | This report | **DELIVERED** |

Five things a reviewer must not infer from this document:

1. The 20 failing tests are **not** new and **not** packaging-related; they are interpreter-gated restore tests that fail identically on pristine `5a3e47dd`.
2. The wheel now builds **in a sandbox with an unsupported interpreter** only because `--ignore-requires-python` was passed. Nobody has yet built it on a supported interpreter.
3. `91 passed, 1 skipped` is **not** `92 passed`. The skip is the restore round-trip that the interpreter gate refuses.
4. AWS KMS validation is not "delayed for credentials": `KmsKeyProvider` **raises not-implemented** — the feature does not exist in this build.
5. Nothing here authorises a merge. PR #7 is open, draft and unmerged; the integration exists only as a working tree and a patch.

---

## 1. Phase 1 — Preservation of the integration result

### 1.1 Location (outside both workspaces, as required)

```
/home/user/integration-preservation/
├── security-selective-integration.patch          28 498 B   575 lines
├── untracked-files/
│   └── tests/test_phase58_security_selective_integration.py   37 342 B   851 lines
├── packaging-flat-layout.patch                    1 617 B   (Phase 3 packaging fix, SEPARATE)
├── SHA256SUMS                                    (artefact checksums, `sha256sum -c` clean)
├── INTEGRATED_STATE_SHA256.txt                   (per-file hashes of the integrated state)
├── INTEGRATION_BASELINE.md                       (what was integrated, exact hashes, how to apply)
├── PATCH_REPRODUCTION_REPORT.md                  (clean-room reproduction evidence)
├── wheel-contents.txt                            (manifest of the built wheel)
└── algerian_ai_erp-0.1.0-py3-none-any.whl        671 305 B   (packaging experiment artefact)
```

A copy of the integration patch, the untracked module, the packaging patch and the
checksum files is also stored in-repo under `docs/security/phase3_blocker_closure/`
so the release evidence travels with the report.

### 1.2 What the preserved change is

| Kind | Count | Files |
|---|---|---|
| Modified | 5 | `dz_erp/api.py`, `dz_erp/decimal_utils.py`, `dz_erp/procurement_stock.py`, `dz_erp/security_audit.py`, `ops/erp.env.example` |
| New (untracked) | 1 | `tests/test_phase58_security_selective_integration.py` (92 cases) |
| Deleted / renamed / binary | 0 | — |
| Net delta | +391 / −16 | |

Findings carried: SEC-01 (upload half), SEC-02 (magnitude + boolean remainder), SEC-03, SEC-04, SEC-05, SEC-06, SEC-07 (+ new staging-directory sub-finding), SEC-08, SEC-09, SEC-10.
Not carried because already upstream: SEC-11, SEC-12. Deferred to review: SEC-13, SEC-14, SEC-15.

### 1.3 Checksums (authoritative copy: `SHA256SUMS`)

| Artefact | SHA-256 |
|---|---|
| `security-selective-integration.patch` | `2dfd77697ba3e726cf2ddf4f9a34766cdf81a093037f581ab71948f177c16b7e` |
| `untracked-files/tests/test_phase58_security_selective_integration.py` | `527bcc991f9f088adaa5766a822fc7306cca625f5227863c5ade3d5d05492837` |
| `packaging-flat-layout.patch` | `a73eacc3d1a9b8d2a56afbe05ed7361ea3843668251fb5e1c87f7af5d4141c4d` |
| `algerian_ai_erp-0.1.0-py3-none-any.whl` | `e01367536b9deb90ac7baecaf3c3373be1ef4e1c6173654ea6f5c7d670f90bc1` |
| `wheel-contents.txt` | `4ccfb5eab30b49b644581b0c20a0cede3201e8be7a0ec39a16b480cb5bf1843b` |

### 1.4 Integrated-state file hashes (authoritative copy: `INTEGRATED_STATE_SHA256.txt`)

| File | SHA-256 |
|---|---|
| `dz_erp/api.py` | `d991ff8006f44c69117cfc54d236ea41d4b1400cd063c8352c1c1f605f96d093` |
| `dz_erp/decimal_utils.py` | `f8af67b83fac671572dad7877c187d409c1578e9b9c8ba55e6ad22af72c41db2` |
| `dz_erp/procurement_stock.py` | `12803bd3ddd82869e8bcb41bd0130fa3e2ba23a2902450722d797960c6b17566` |
| `dz_erp/security_audit.py` | `9eeacb5164b71eb087ef2b96ee5f29c89217379b5fda8f80ae5a7707f2605792` |
| `ops/erp.env.example` | `734dec80d5d723dba4398e092bcefc9117e77a7389f246e727108db7804b76d4` |
| `tests/test_phase58_security_selective_integration.py` | `527bcc991f9f088adaa5766a822fc7306cca625f5227863c5ade3d5d05492837` |

`sha256sum -c` of both files passes: `SHA256SUMS` inside the preservation directory, and
`INTEGRATED_STATE_SHA256.txt` inside the reconstruction (§2).

---

## 2. Phase 1b — Patch reproduction: did it rebuild the exact state?

**Yes — byte-for-byte, and behaviourally.** Full evidence: `PATCH_REPRODUCTION_REPORT.md`; raw log: `phase3_blocker_closure/EVIDENCE.md` §E2–E4.

Procedure: **fresh clone from the authoritative remote** (not a copy of either workspace) → `git checkout 5a3e47dd…` → `git apply --check` → `git apply` → copy untracked file.

| Check | Result |
|---|---|
| `git apply --check` | exit 0, no rejects, no fuzz, no `--3way` |
| `git apply` | all five files "applied cleanly" |
| `git status --porcelain` after apply | identical file set/order to `/tmp/pr7work` |
| `git diff --stat` | +391 / −16 — identical |
| `diff -r` (VCS/caches excluded) | no differences |
| Full-tree SHA-256 manifest | **297 / 297 files identical** |
| Per-file hashes of the six integration files | **6 / 6 match** |
| New test module, run on the reconstruction | **91 passed, 1 skipped, 0 failed** (10.68 s) |

The only delta anywhere was `.ruff_cache/` in the source workspace — a lint cache produced *after* the integration and excluded from the comparison. **No STOP condition was triggered.**

---

## 3. Phase 2 — Supported-Python recovery

### 3.1 What the project requires

* `pyproject.toml`: `requires-python = ">=3.11.17,<3.13,!=3.12.0,…,!=3.12.9"` (+ `dz_erp.security_audit.SAFE_RESTORE_MINIMUMS`).
* `tests/test_phase48_restore_interpreter_matrix.py`: the predicate is tested over its whole version space; 3.11.17 is a **security floor** (safe-extraction restore refuses below it), not a convenience.
* CI: test matrix `["3.11", "3.12"]`; the restore/backup job pins **3.12.10**; `ops/Dockerfile` pins `python:3.12-slim-trixie`.

### 3.2 What this environment has

| Candidate | Result |
|---|---|
| `/usr/bin/python3.11` | CPython **3.11.2** — below the 3.11.17 floor |
| Other `python3.x` binaries | none on disk |
| `py` launcher (Windows) | absent — no Windows host in this sandbox at all |
| `conda` / `mamba` / `uv` / `pyenv` / `asdf` | none installed |
| Existing venvs | `/tmp/pr7venv`, `/tmp/mainvenv`, `/tmp/ivenv` — all built on 3.11.2 |
| Download of a standalone interpreter | not attempted: **unauthorised**, and the release CDN is not reachable from this sandbox |

### 3.3 Blocker statement (unchanged from Phase 2, now closed as a *documented* blocker)

> **B1 — No supported interpreter exists in the build environment.** Every runtime
> claim in Phases 2 and 3 is therefore made on CPython 3.11.2, which the project
> itself refuses for restore. 20 tests fail and 1 skips for that reason alone.

### 3.4 Restore-path test results

| Suite | Status |
|---|---|
| Interpreter-gated restore tests (`test_phase8`, `test_phase9`, phase12/17/41/48 restore cases) | **NOT EXECUTED** — they fail/skip by design below 3.11.17. Counted as failures, never as passes. |
| `test_phase48_restore_interpreter_matrix.py` (predicate over the full version space) | passed — the *rule* is verified even though the *path* cannot run |
| Phase 58 suite on the reconstruction | 91 P / 1 S — the skip is the restore round-trip |
| Full suite, integrated tree | 20 F / 1295 P / 10 S (identical failure set to pristine) |
| Full suite, pristine `5a3e47dd` | 20 F / 1204 P / 9 S |
| Local File Vault (POSIX) | **executed**: `test_phase10_vault_key_rotation.py` → 23 passed |

### 3.5 Safest installation route (recommended, **not executed** — needs authorisation)

Preferred, in order:

1. **`uv python install 3.12.10`** — pin the exact CI version. `uv` verifies the
   download against the checksums published in the python-build-standalone
   release metadata; no root, no compiler, installs under `~/.local/share/uv`.
   Then: `/tmp/py312/bin/python -m venv /tmp/ivenv312` and install
   `requirements.lock.hashes.txt` with `--require-hashes`.
2. **python-build-standalone tarball** (`install_only` build for
   `x86_64-unknown-linux-gnu`, 3.12.10), verified against the release's
   `SHA256SUMS` **before** extraction, installed to a fresh prefix.
3. **Build from source** (`pyenv install 3.12.10`) — requires `build-essential`,
   `libssl-dev`, `zlib1g-dev`, `libffi-dev`; acceptable but slowest and needs
   root for the toolchain.
4. **Do not** use Debian's `python3.11` to "satisfy" the floor, and do not
   lower `requires-python` to make a build pass — the floor is a security control.

Verification once installed (exact commands, to be run by whoever has the interpreter):

```bash
/tmp/ivenv312/bin/python -V                       # must be 3.12.10 (or >=3.11.17 / >=3.12.10)
/tmp/ivenv312/bin/python -m pytest -q             # expect 0 failed
/tmp/ivenv312/bin/python -m pytest -q tests/test_phase48_restore_interpreter_matrix.py
# then the phase-58 suite: expect 92 passed, 0 skipped
```

**Never record these as passed until they have actually been run.**

---

## 4. Phase 3 — Wheel build failure: analysis and smallest safe fix

### 4.1 Root cause

`pyproject.toml` declares a PEP 621 project but **no setuptools package configuration**. With `build-backend = setuptools.build_meta` and a flat layout, setuptools falls back to auto-discovery and aborts:

```
error: Multiple top-level packages discovered in a flat-layout: ['ops', 'dz_erp', 'vendor', 'odoo_addons'].
```

Notably `tests/`, `tools/`, `docs/` and `benchmarks/` are **not** in that list: setuptools excludes those names by default. The four reported entries are what discovery believes to be packages:

| Directory | `__init__.py`? | Importable? | What it actually is |
|---|---|---|---|
| `dz_erp/` | yes (+ `dz_erp/desktop/__init__.py`) | **yes** | the application package |
| `odoo_addons/l10n_dz_scf_extensions/` | yes at depth 2, **no** at `odoo_addons/` | no | an Odoo addon tree, loaded by Odoo from an addons path |
| `ops/` | no | no | deployment assets (Dockerfile, systemd units, env example) |
| `vendor/azinak_l10n_dz_account_fix/` | — | no | vendored third-party Odoo module *sources* |

**Is setuptools auto-discovery the cause?** Yes — and the defect is *pre-existing*: the identical failure occurs on pristine `5a3e47dd`, i.e. it has nothing to do with the security integration.

**Why nobody noticed:** nothing in the project builds a wheel. CI installs from `requirements.lock.hashes.txt` (`--no-deps --require-hashes`) and runs pytest with `pythonpath = ["."]`; `ops/Dockerfile` states explicitly *"There is no `pip install .` and no build stage: the app is a source tree"*. The packaging metadata was never exercised.

### 4.2 Package data that must be preserved

`dz_erp/assets/fonts/DejaVuSans.ttf` (759 720 B) + `DejaVuSans.COPYRIGHT` are resolved at runtime by `dz_erp/benchmark.py` (`BUNDLED_FONT_DIR = Path(__file__).parent / "assets" / "fonts"`). Without `package-data`, a wheel would silently fall back to a system font or fail to synthesise documents. This is the one piece of *data* the wheel must carry; there are no SQL/template resources inside `dz_erp`.

### 4.3 The smallest safe correction (kept separate from the security change)

`packaging-flat-layout.patch` — **23 added lines, `pyproject.toml` only, no code touched**:

```toml
[tool.setuptools.packages.find]
include = ["dz_erp*"]            # explicit; ops/, vendor/, odoo_addons/ can never match

[tool.setuptools.package-data]
dz_erp = ["assets/fonts/*"]      # the vendored font benchmark.py resolves at runtime
```

Why this is the smallest safe option:

* It states intent instead of enumerating exclusions — `ops`/`vendor`/`odoo_addons` are excluded *because they are not packages*, not because they are listed.
* It keeps `dz_erp.desktop` (the `dz-erp` console script target) by using `dz_erp*` rather than a single name.
* It adds no new build requirement and changes no version, dependency, governance or `requires-python` value.

**Impact understood before implementation**: the experiment was run on a *separate clean clone* (`/tmp/pkgfix`, no security patch), so packaging and security evidence never mix.

### 4.4 Verification matrix (executed)

| Check | Before | After |
|---|---|---|
| `pip wheel .` | fails (flat-layout) | **succeeds** (671 305 B wheel) |
| Wheel top-level content | n/a | exactly `dz_erp/` + `*.dist-info/` — 36 modules, 8 `desktop/` files, 2 font assets |
| Leakage of `ops`/`vendor`/`odoo_addons`/`tests`/`tools`/`docs`/`benchmarks` | n/a | **none** |
| `Requires-Python` in METADATA | — | unchanged: `>=3.11.17,<3.13,!=3.12.0…` |
| Console script | — | `dz-erp = dz_erp.desktop.app:main` preserved |
| Extras | — | `desktop`, `dev` preserved |
| Import from wheel | — | `dz_erp`, `dz_erp.desktop` import |
| Font present in wheel | — | 759 720 B, resolver picks it |
| PDF synthesis from wheel assets | — | 22 812-byte `%PDF-` document rendered |
| `pip install -e .` (PEP 660) | — | succeeds; `__editable__` .pth + finder; script registered |
| Full test suite on the patched tree | 20 F / 1204 P / 9 S | **20 F / 1204 P / 9 S** — no change |

### 4.5 Two caveats that must not be lost

1. **The wheel is a metadata artefact, not a supported deployment.** `dz_erp/localization.py` and `dz_erp/odoo_test_adapter.py` resolve `vendor/…` through `REPO_ROOT = Path(__file__).parent.parent`. Inside a wheel install this resolves to `site-packages`, where `vendor/` does not exist — verified: `vendor present: False`, `AZINAK_FIX_DIR present: False`. Localisation/Odoo-integration features therefore require the **source tree** (`PYTHONPATH=.`), exactly as the Dockerfile already does. The patch documents this in a comment rather than pretending a wheel is deployable.
2. The wheel was built with `--ignore-requires-python` **because the sandbox runs 3.11.2**. On a supported interpreter the plain command is expected to work, but that has not been observed. Do not claim "wheel build verified on a supported interpreter" until it is.

### 4.6 Recommended follow-ups (not implemented)

* Decide whether `odoo_addons/l10n_dz_scf_extensions` and `vendor/azinak_…` must be *distributed with the app* (they are read from the repository, so yes for the current deployment model — which is the source tree, not a wheel). If they must ship in an artefact, make that an explicit `package-data`/sdist decision, not auto-discovery.
* Add a CI job that builds the wheel and asserts the font asset is inside it — the current CI cannot catch a regression in packaging metadata.
* `dz_erp/backup_storage_s3.py` imports `boto3` optionally, but `boto3` is **not in `requirements.lock.txt`**; the hash-locked install cannot satisfy the S3 feature. Either pin it as an extra with hashes or state in the deployment guide that S3 backups require an out-of-lock install (see §8).

---

## 5. Phase 4a — SEC-13: password policy review

### 5.1 What the code does today (evidence, not assumption)

| Aspect | Current behaviour | Where |
|---|---|---|
| Hashing | PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte CSPRNG salt, per-user | `auth.py:PBKDF2_ITERATIONS`, `hash_password` |
| Comparison | `hmac.compare_digest`; unknown username verified against a real dummy hash to equalise timing | `verify_password`, `_dummy_password_hash` |
| Minimum length | **8 characters, enforced only at `POST /api/v1/users`** | `api.py:1450` |
| Maximum length | **1024 characters** (bounds PBKDF2 work per request); `verify_password` rejects >4096 bytes | `MAX_PASSWORD_LENGTH` |
| Complexity rules | none (no composition, no expiry, no history) | — |
| Login throttling | sliding-window limiter keyed on *(client IP, username)*, checked **before** the password is verified; `429` + `Retry-After`; backend failure is fail-closed | `api.py:573-655`, `rate_limit.py` |
| Account lockout | 5 failed logins → 15-minute lock; counter resets on success and when the lock is set | `MAX_FAILED_LOGINS`, `_register_failed_login` |
| Password **change** | **no endpoint and no service function** | — |
| Password **reset / recovery** | **none** (no email, no admin reset, no self-service) | — |
| Credential stuffing resistance | per-(IP, account) limiting + lockout + constant-time verify + generic error messages | as above |
| Session credentials | opaque 256-bit bearer tokens, SHA-256 stored, 8 h TTL, revocable, server-side identity | `auth.py` |
| MFA | none | — |

### 5.2 Assessment

The **hashing and login-abuse controls are sound for this build**: 600 k PBKDF2-HMAC-SHA256 with per-user salts is the OWASP floor for PBKDF2 and remains acceptable; timing equalisation for unknown users, fail-closed throttling and a per-account lockout cover the classic stuffing/brute-force paths. Two real gaps:

* **G-13.1 — the minimum length is asserted in exactly one place.** `AuthManager.create_user` accepts any non-empty password: a provisioning script, CLI bootstrap (`serve.py --bootstrap-admin`) or future service path can create a 1-character password. The policy is a route decorator, not a property of credential creation.
* **G-13.2 — there is no lifecycle.** No change-password, no reset, no expiry. An operator whose password is exposed cannot rotate it in-product; their only route is admin deactivation and re-creation, which loses attributions attached to the user row.

Secondary observations (not defects for a first release): no breach-list check, no MFA, no rehash-on-login migration hook.

### 5.3 Recommended policy (recommendation only — **nothing implemented**)

1. **Centralise the policy in `dz_erp/auth.py`** as `validate_password_policy(password) -> None` and call it from `hash_password`'s callers (`AuthManager.create_user`, the CLI bootstrap, any future reset). Keep the API-layer 422 mapping. That turns G-13.1 from "one route remembered" into "the only way to create a credential".
2. **Minimum 12 characters** for new credentials (NIST SP 800-63B rev.4 favours length over composition; 8 is a floor, not a target), **maximum 1024** unchanged, **no composition rules**, **Unicode allowed and NFC-normalised before hashing**.
3. **Reject obvious weak values** (the username, the company name, a small built-in blocklist) — cheap, offline, no external dependency.
4. **Ship at least one lifecycle path** before production use: an admin-initiated `POST /api/v1/users/{id}/password-reset` that mints a single-use, short-TTL reset secret (audited, with `revoke_all_tokens_for_user` on success). Self-service email reset can wait.
5. **Rehash-on-login**: on a successful `verify_password`, if the stored iteration count is below the current constant, re-hash with the current parameters and audit `AUTH_PASSWORD_REHASHED`. This is how the iteration floor moves later without a flag day — and it must be added *before* anyone raises it.
6. Keep the 8 h token TTL and revocation as they are.

### 5.4 Migration implications

* **Existing hashes remain valid** — the format string carries `algorithm$iterations$salt$digest`, so raising the default iteration count does not invalidate stored credentials; they upgrade on next successful login (item 5).
* **A 12-character minimum cannot be applied retroactively** without locking users out. Apply it at creation/change/reset only; do not re-validate existing passwords at login.
* **`bootstrap-admin`** must go through the same validator; otherwise the first credential in a deployment is the weakest one.
* No schema change is required for items 1–3, 5–6. Item 4 wants a small table (or a column pair) for single-use reset secrets — a migration, therefore a release-notes item.
* No change to `requires-python`, dependencies or lockfile.

---

## 6. Phase 4b — SEC-14: OCR override validation matrix

### 6.1 The override paths (all of them)

| # | Path | Entry point | Reaches |
|---|---|---|---|
| P1 | `PATCH /api/v1/documents/{id}/fields` (body `FieldEditInput`) | `update_extracted_field_by_human` (`intake_ocr.py:1593`) | writes `human_override_value` **and** `normalized_value` |
| P2 | `POST /api/v1/documents/{id}/fields/decide` with `decision=CORRECTED` on an **existing** row | `decide_extracted_field_review` → `_apply_field_decision` | validated value → `human_override_value` + `normalized_value` |
| P3 | same endpoint, `CORRECTED` on a **missing** field | as P2, manual-transcription branch | creates the row with `HUMAN_ENTRY_MODEL`, no fabricated confidence |
| P4 | same endpoint, line decisions (`line_item_json` qty, `line_unit_price`, `line_discount`) | as P2, line branch | rewrites quantity/price/discount and **recomputes the line amount** |
| P5 | direct service calls (tests, scripts) | the same two functions | as above |

There is no other writer of `human_override_value`. The extraction writer never overwrites it (raw text and confidence stay immutable), which is what makes the override evidence rather than a rewrite.

### 6.2 Gates that apply to *every* path

| Gate | P1 | P2–P4 |
|---|---|---|
| Authenticated principal resolved server-side (bearer token) | yes | yes |
| Tenant isolation (`user.company_id == document.company_id`) | yes (raises `TenantIsolationError` → 403) | yes |
| Role allowlist `REVIEW_CAPABLE_ROLES = (clerk, approver, accountant, admin)` | yes, denial audited `SECURITY_FIELD_EDIT_BLOCKED` | yes |
| Document state allowlist `FIELD_EDITABLE_STATES = (NeedsReview, Validated)` | yes, denial audited | yes |
| Row must belong to the document/tenant | yes | yes, plus `EXTRACTION_ROW_NOT_IN_GROUP` for cross-group row selection |
| **Value validated against the field's class before storage** | **NO** | **yes** (`validate_corrected_value`) |
| Decision recorded as formal review evidence (`ocr_field_reviews`) | no | yes |
| Audit event with before/after state | yes (`OCR_HUMAN_FIELD_EDIT`) | yes |

### 6.3 Field-specific validation (`review_policy.validate_corrected_value`, P2–P4)

| Value class | Fields | Rule | Failure code |
|---|---|---|---|
| `amount` | `amount_ht_dzd`, `amount_tva_dzd`, `amount_timbre_dzd`, `amount_ttc_dzd`, `line_unit_price`, `line_discount` | decimal parse via `to_decimal` (**adds SEC-02's magnitude guard: \|x\| < 10¹⁸**); **≥ 0**; discount additionally **≤ 100**; no clamping — out-of-range values are refused, never silently altered; unit price stored 6-dp, money 2-dp | `FIELD_VALUE_INVALID_AMOUNT`, `FIELD_VALUE_NEGATIVE_AMOUNT`, `FIELD_VALUE_DISCOUNT_OUT_OF_RANGE` |
| `quantity` | `line_item_json` qty | decimal parse, **strictly > 0**, 6-dp | `FIELD_VALUE_NONPOSITIVE_QUANTITY` |
| `date` | `invoice_date`, `due_date` | `YYYY-MM-DD` **and** a real calendar date | `FIELD_VALUE_INVALID_DATE` |
| `currency` | `currency_code` | **DZD only** (single-currency system; a foreign code would post DZD amounts under a lie) | `FIELD_VALUE_UNSUPPORTED_CURRENCY` |
| `identifier` | `invoice_number` (≤64, no control chars), `supplier_nif` (`^\d{15}(\d{5})?$`), `supplier_nis` (`^\d{15}$`), `supplier_ai` (`^\d{11}$`), `supplier_rc` (`^\d{2}/\d{2}-[0-9A-Z]{7,12}$`) | per-field regex | `FIELD_VALUE_INVALID_{NIF,NIS,AI,RC,INVOICE_NUMBER}` |
| `text` (fallback) | fields outside the critical policy (e.g. `payment_method`) | ≤256 chars, no newline | `FIELD_VALUE_INVALID_TEXT` |
| any | all | empty/`None` refused | `FIELD_VALUE_REQUIRED` |

Numeric **upper bounds**: money/price/quantity are bounded only by the SEC-02 magnitude guard (10¹⁸) — there is no per-invoice ceiling. That is a deliberate consequence of the decimal-safety fix, and a business-limit rule (e.g. refuse an OCR-corrected `amount_ttc_dzd` above a configurable threshold) is a **new policy decision**, not a bug fix.

### 6.4 Influence on accounting values, and the controls that still apply

* The **invoice amounts are not taken from the OCR record**: `POST /api/v1/invoices` receives `amount_ht_dzd`/`amount_tva_dzd`/`amount_ttc_dzd`/lines in the request body and validates them through the invoice/procurement validators. OCR overrides therefore influence *what can be approved with which document*, not the posted numbers directly.
* Line overrides **do** recompute the line amount (`qty × PU × (1 − discount/100)`).
* Stored header totals are **recomputed and compared** on every evaluation (`_evaluate_totals`: `ht + tva + timbre == ttc`, 2-dp); a mismatch is a blocking reason, and a value that cannot be parsed makes the totals "not evaluable", which also blocks.
* The approval gate refuses while any required field is undecided, `FLAGGED`, **stale** (value changed after the decision), `NOT_PRESENT` but later populated, or part of an unresolved **duplicate-row** group (`procurement_stock.py` → `evaluate_document_review_policy`).

### 6.5 Finding

**G-14.1 — P1 bypasses value-class validation.** An authorised reviewer can `PATCH` any string into a monetary or date field; the row is written and only *later* rejected (or not) by the review policy. Consequences today are contained — flagging + stale-decision + totals checks + the approval gate mean an unparseable amount cannot silently reach an approval — but the write path stores data the system knows to be invalid, and `normalized_value` is overwritten, so downstream consumers that read it without going through the policy see garbage. **This is a correctness/observability defect with a security flavour, not an exploitable injection** (tenant, role and state gates all still apply).

**Recommended (not implemented):** route P1 through `validate_corrected_value(rule_for(field_name) or TEXT_FALLBACK_RULE, value)`, or deprecate P1 in favour of P2 and have P1 emit a deprecation header. Either way a test must assert that an out-of-class value yields 422 on *both* write paths.

---

## 7. Phase 4c — SEC-15: TLS / Host / trusted-proxy review

### 7.1 Deployment topology the code assumes

* The app serves **plain HTTP** on `ERP_HOST:ERP_PORT` (`dz_erp/serve.py` → `uvicorn.run(...)`), default `127.0.0.1:8000`; the container sets `ERP_HOST=0.0.0.0`.
* TLS termination is a **reverse proxy on the same host** (nginx/Caddy/HAProxy) — stated in `ops/Dockerfile`, `ops/docker-compose.yml` ("no TLS termination and no certificate handling (a proxy in front does it)") and `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` (§"Terminate TLS; redirect HTTP to HTTPS").
* No in-app HTTPS, no HSTS header, no `Secure`/`HttpOnly` cookies — **there are no cookies at all**: credentials are `Authorization: Bearer` tokens.

### 7.2 What is implemented well

`dz_erp/net.py` implements a genuinely fail-closed trusted-proxy policy:

* **No trusted proxies by default** → forwarded headers are ignored entirely and the direct peer address is used.
* A forwarded header is honoured **only** if the immediate peer is inside a configured CIDR (`ERP_TRUSTED_PROXIES`).
* The chain is walked **right-to-left**, stopping at the first non-trusted address, so prepended entries cannot win.
* Malformed/duplicate/empty values degrade to the peer address; forwarded data never influences authentication or authorisation.
* The login limiter keys on that resolved address, so it cannot be defeated by editing a header — provided the policy is not mis-configured.

### 7.3 Gaps

| # | Gap | Detail |
|---|---|---|
| G-15.1 | **No Host-header validation in the application** | No `TrustedHostMiddleware` and no equivalent. Any `Host:` is accepted. Behind a proxy that routes by `Host`, this is a cache-poisoning / absolute-URL / password-reset-link-poisoning ingredient (there is no reset flow today, which limits the blast radius but does not remove the class). |
| G-15.2 | **Two independent proxy-trust layers, only one configured** | `uvicorn.run(...)` uses **`proxy_headers=True`** with `forwarded_allow_ips` defaulting to `127.0.0.1` (or `$FORWARDED_ALLOW_IPS`). If a proxy reaches the app from loopback (sidecar / same network namespace), uvicorn rewrites `request.client.host` from `X-Forwarded-For` **before** `net.py` sees it — the application's own policy is then bypassed and its "peer" is really attacker-supplied data. `FORWARDED_ALLOW_IPS` is not documented anywhere in `ops/` or `docs/`. |
| G-15.3 | **No HSTS / no HTTPS redirect in-app** | Acceptable *only* if the proxy is the documented place for it; the deployment guide says so, but the guide does not say what the proxy must set (HSTS, `X-Content-Type-Options`, `X-Frame-Options`/CSP, request-size cap). |
| G-15.4 | **Chunked-body cap is a proxy responsibility** (carried from Phase 2, B8) | `Content-Length`-less bodies are bounded by the proxy, not the app. |

### 7.4 Secure deployment recommendation (**no configuration changed by this phase**)

1. **TLS: terminate at the proxy; the app stays plain HTTP on a private interface.** Redirect 80→443, HSTS `max-age=31536000; includeSubDomains` after the first successful deployment.
2. **Never trust proxies indiscriminately.** `ERP_TRUSTED_PROXIES` must list **only** the proxy's own address/CIDR (e.g. `10.0.0.10/32`), never `0.0.0.0/0`, never `::/0`. Leave it **unset** when there is no proxy — the default already ignores forwarded headers, which is the safe state.
3. **Reconcile the uvicorn layer explicitly.** Set `FORWARDED_ALLOW_IPS` to the same single proxy address, or to empty, and never `*`. The simplest defensible configuration is: uvicorn trusts nothing (`FORWARDED_ALLOW_IPS=""`), the app trusts exactly the proxy (`ERP_TRUSTED_PROXIES=<proxy>/32`) — one layer, one source of truth, and the audit log records what the application can actually observe.
4. **Host allowlisting belongs at the proxy** (single canonical hostname, reject others) **and** should be mirrored in the app once `TrustedHostMiddleware` is added (allowlist from configuration, fail-closed default in production; `localhost/127.0.0.1` only in development).
5. **Proxy-enforced headers to require**: HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (or CSP `frame-ancestors 'none'`), a request-body ceiling (`client_max_body_size`) consistent with `ERP_MAX_REQUEST_BYTES`, and rate limiting as *defence in depth* behind the in-app limiter.
6. **Cookies are not used** — keep it that way; a bearer token in an `Authorization` header is not exposed to CSRF. If a cookie ever carries a session, it must be `Secure; HttpOnly; SameSite=Strict` **and** the CSRF story must be re-reviewed.
7. **Do not enable `--proxy-headers`/`FORWARDED_ALLOW_IPS` "just in case"**: in this codebase the forwarded header is *evidence*, and the audit trail is only as good as that evidence.

---

## 8. Phase 5 — Validation plan and what was actually executed

| Area | Can this environment run it? | Executed | Evidence |
|---|---|---|---|
| **Local File Vault** (POSIX) | yes | **YES** — `test_phase10_vault_key_rotation.py`: 23 passed; vault suites pass inside the full run | §3.4, EVIDENCE E9 |
| **Windows file permissions / backups** | no — POSIX sandbox, no Windows host, no `py` launcher | **NO** | 3 tests gated on `os.name == "nt"` (phase12 junction regression, phase17 reparse-point paths); `security_audit.py:310` has the POSIX branch. **Not executed, not claimed.** |
| **Desktop UI / navigation** | partly — Qt-free modules only | **PARTIAL** | 9 Qt-free desktop tests passed; 3 UI tests skipped: `No module named 'PySide6'` (`PySide6==6.11.2` is an optional extra). No window was ever rendered here. |
| **Docker image & runtime** | no — no container runtime | **NO** | Dockerfile/compose reviewed as source only; `ops/docker-compose.yml` says "prepared, not run". |
| **AWS KMS with an isolated test CMK** | **not possible — feature absent** | **NO** | `key_provider.py:389` "*Not implemented. No KMS/HSM client is bundled*"; declaring `kms`/`hsm` is refused at start-up. This is a product gap, not a credential gap. |
| **MinIO / S3 with disposable storage** | no — no `boto3` in the lock, no object store, no network path | **NO** | `backup_storage_s3.py` imports `boto3` lazily; `requirements.lock.txt` has no boto3/botocore. Executing it requires an out-of-lock install (see §4.6) **plus** a live bucket. |

Plan to complete the gaps (owner-actionable, in priority order):

1. Provision a supported interpreter (§3.5) → run the restore/backup matrices and the phase-58 suite → the 20 failures and the skip must disappear; only then can B1 close.
2. Windows runner (or a Windows workstation): permissions on the DB/vault/backup files, junction/reparse-point rejection, backup creation across a path change, restore into a fresh directory.
3. `pip install '.[desktop]'` on a supported interpreter with a display (or `QT_QPA_PLATFORM=offscreen`) → phase26/27/28 UI suites.
4. Build the image from `ops/Dockerfile` and run the compose stack; verify the unprivileged UID, no secret in layers, `/healthz`, and the proxy/`FORWARDED_ALLOW_IPS` combination of §7.4.
5. MinIO container + `boto3` pinned with hashes (or the S3 feature explicitly marked unsupported).
6. KMS: needs an implementation decision first — a real client (`boto3 kms`/agent) with an isolated CMK in a sandbox account. Until then `KmsKeyProvider` must keep refusing; a "KMS" label over file-backed material would be a false claim.

---

## 9. Remaining blockers

| ID | Blocker | Phase-3 status | Next action |
|---|---|---|---|
| **B1** | No supported interpreter (≥3.11.17 / ≥3.12.10) | **OPEN** — 20 tests fail, 1 skips, wheel needs `--ignore-requires-python` | install per §3.5 (authorisation needed), re-run |
| **B2** | Wheel build failure (flat-layout) | **ROOT-CAUSED + FIX VERIFIED IN ISOLATION** | review `packaging-flat-layout.patch`, apply as its **own** change + CI job (§4.6) |
| **B3** | Integration is uncommitted (patch-only) | **MITIGATED** — preserved, reproducible, hashed | human review of the patch, then commit/PR **outside this session** |
| **B4** | Windows / desktop / Docker / MinIO / KMS unvalidated | **OPEN** (partially executed: vault, Qt-free desktop) | §8 plan |
| **B5** | SEC-13/14/15 decisions | **REVIEWED, DECIDED-NOT-IMPLEMENTED** | product/AppSec decision on §5.3, §6.5, §7.4 |
| **B6** | "10 user modifications" still unidentified | **OPEN** | repository owner must enumerate them; no further reconciliation is possible from here |
| **B7** | Upload service has no in-process role gate | **ACCEPTED RESIDUAL** (from Phase 2) | owner acceptance in writing, or add the gate |
| **B8** | Chunked-body cap is proxy responsibility | **DOCUMENTED** | require the proxy ceiling (§7.4 item 5) |
| **B9 (new)** | `backup_storage_s3` depends on `boto3`, which is not in the hash-locked requirements | **OPEN** | pin as a hashed extra, or remove the S3 claim from the deployment guide |

---

## 10. Changed files, hashes, and Git state

### 10.1 Files added by this phase (in the remediation workspace, uncommitted)

| Path | Purpose |
|---|---|
| `docs/security/PHASE3_BLOCKER_CLOSURE_REPORT.md` | this report |
| `docs/security/phase3_blocker_closure/EVIDENCE.md` | raw command output log |
| `docs/security/phase3_blocker_closure/security-selective-integration.patch` | copy of the preserved integration patch |
| `docs/security/phase3_blocker_closure/untracked-files/tests/test_phase58_security_selective_integration.py` | copy of the new test module |
| `docs/security/phase3_blocker_closure/packaging-flat-layout.patch` | the separate packaging correction |
| `docs/security/phase3_blocker_closure/SHA256SUMS` | artefact checksums |
| `docs/security/phase3_blocker_closure/INTEGRATED_STATE_SHA256.txt` | per-file hashes of the integrated state |
| `docs/security/phase3_blocker_closure/wheel-contents.txt` | wheel manifest |

No tracked file in `/home/user/ERP` was modified by this phase; `sha256sum -c` over the five pre-existing tracked changes returns 5/5 `OK` (EVIDENCE E13).

### 10.2 Git state (verified, unchanged)

```
/home/user/ERP      HEAD 4fc1364a9504fab1153ea44e753f354f00023428   branch arena/7557bcfb-erp   0 commits ahead of main
/tmp/pr7work        HEAD 5a3e47ddfba2b789bcff71d6438db4927f5c7986   detached, 5 M + 1 ?? , no commits
/tmp/pristine       HEAD 5a3e47ddfba2b789bcff71d6438db4927f5c7986   detached, 1 measurement shim
/tmp/verify-clone   HEAD 5a3e47ddfba2b789bcff71d6438db4927f5c7986   + preserved patch (reconstruction)
/tmp/pkgfix         HEAD 5a3e47ddfba2b789bcff71d6438db4927f5c7986   + packaging patch only
PR #7               open, draft, unmerged, head 5a3e47dd…, base e4e47b3b…
```

No commit, no push, no merge, no history rewrite, no destructive Git command, no workspace deleted, no venv modified, no database or volume touched.

---

## 11. Final status

**PRESERVED — BLOCKERS REMAIN**

* The integration result is **preserved, checksummed and exactly reproducible** from a fresh clone of PR #7's head; the reconstruction is byte-identical (297/297 files) and reproduces the same test outcome (91P/1S).
* The **wheel build failure is understood and the smallest safe correction is verified in isolation**, kept strictly separate from the security change.
* **SEC-13/14/15 are reviewed with concrete recommendations and one genuine gap each** (no central password policy/lifecycle; the legacy PATCH override skips value-class validation; no Host validation and an undocumented second proxy-trust layer in uvicorn) — none implemented, because implementing them on assumptions was explicitly out of scope.
* **Three release blockers remain hard**: no supported interpreter in the environment (B1), the integration is still uncommitted and unreviewed (B3), and Windows/desktop/Docker/MinIO/KMS validation has not been executed — with KMS not even implemented in this build (B4).

This is **not** a production-readiness statement, and no unexecuted test is reported as passing.
