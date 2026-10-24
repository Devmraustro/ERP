# ERP — Independent Security Review & Staging Readiness

**Final report** · branch `arena/5c240e1d-erp` · PR #2 (OPEN, **not merged**)

---

## 1. Verified branch, local SHA, remote SHA, working-tree state

| Item | Value |
|---|---|
| Branch | `arena/5c240e1d-erp` |
| Local HEAD | `179dc85b81552615a4ef246b9bc2ee08653a7a39` |
| Remote HEAD (`origin/arena/5c240e1d-erp`) | `179dc85b81552615a4ef246b9bc2ee08653a7a39` |
| `origin/main` | `0e106e05bc1328780e7a837a65b870d535d9f6d6` (untouched) |
| Working tree | **clean** (`git status --porcelain` empty) |
| PR #2 | OPEN, MERGEABLE, head `179dc85`, base `main` — **not merged** |
| Commits ahead of `main` | 17 |

### 1.1 Repository discrepancy found at the start — resolved, reported

The reported SHA `ec8f649` was **not** the local HEAD when this session began. The checkout had
been re-provisioned as a shallow clone of `main` at `0e106e05` ("Initial commit"), while the
workspace files had been restored independently of git — so the entire source tree appeared as
*untracked* and `AUDIT_READINESS_REPORT.md` was present but uncommitted.

**Evidence gathered before any change:**

```
remote arena/5c240e1d-erp = ec8f649b...   (correct, all prior work intact)
local  HEAD               = 0e106e05...   (grafted "Initial commit")
origin/main               = 0e106e05...   (grafted)
```

**Resolution (non-destructive):** a temporary index file was used to compare the working tree
against `ec8f649` **without touching the repository's real state**. That comparison proved the
working tree was byte-identical to `ec8f649` except for 10 regenerated benchmark artifacts
(9 PDFs + `ground_truth_and_results.json`, differing only in run-dependent latency values and
embedded timestamps). No file was missing and nothing extra was present.

The branch ref and index were then moved to `ec8f649` with `git reset` (mixed), which
**does not touch working-tree files**, and the 10 regenerated artifacts were restored to their
committed state. Nothing was deleted, reset, overwritten or cleaned to force agreement: the
recovery moved refs only, and the file-content check was performed read-only beforehand.

**No work was lost.** All prior commits remain on the remote, and `main` was never modified.

---

## 2. The seven reported defects — source location, root cause, fix, regression test

### 2.1 Persistent vault encryption keys

| | |
|---|---|
| **Source** | `dz_erp/intake_ocr.py:107-146` (`_resolve_vault_key`), `:149-153` (`__init__`) |
| **Root cause** | `PrivateDocumentVault.__init__` called `Fernet.generate_key()` whenever `encryption_key` was `None`. The key lived only in memory, so the next process started with a different one and every stored document raised `InvalidToken` — permanently, with no error at the time of storage. |
| **Fix** | Precedence: explicit argument → `ERP_VAULT_KEY` → a `<name>.vault.key` file beside the vault directory (mode `0600`, `O_EXCL` so racing initialisers converge) → never a silent fresh key. A malformed key raises `ValueError`. |
| **Regression test** | `tests/test_phase8_upload_boundary_and_ci.py::test_vault_key_is_persisted_so_documents_survive_a_restart`, `::test_vault_key_is_not_stored_inside_the_encrypted_directory`, `::test_vault_key_precedence_and_failure_mode` |
| **Process-boundary test** | `tests/test_phase9_process_boundary.py::test_documents_written_in_one_process_decrypt_in_a_fresh_process` |
| **Verified** | Keys survive restart; never silently regenerated for an existing vault; missing/invalid keys fail safely. |

**New finding (defect 8):** the original fix was incomplete. If the key file was *deleted* while
blobs existed, the vault silently generated a replacement and kept operating — writes succeeded
under the new key while every existing document became unreadable, so nothing downstream
noticed. Now raises `VaultKeyUnavailableError`. Verified in a separate process, including that
**no replacement key file is written**.

### 2.2 Audit HMAC key management

| | |
|---|---|
| **Source** | `dz_erp/database.py:520-585` (`_resolve_audit_secret`), `:586-590` (`__init__`) |
| **Root cause** | `hmac_secret or os.environ.get("ERP_AUDIT_HMAC_SECRET", <published default>)`. Any file-backed deployment that omitted the variable chained `audit_events` with a key printed in the source tree. |
| **Fix** | Explicit → `ERP_AUDIT_HMAC_SECRET` → a `<dbname>.audit.key` file (mode `0600`, `O_EXCL`) → the published default **for `:memory:` only**. A file-backed database cannot reach the default. |
| **Regression test** | `tests/test_phase8_upload_boundary_and_ci.py::test_file_backed_database_never_uses_the_published_default_key`, `::test_audit_key_is_generated_beside_the_database_with_owner_only_permissions`, `::test_audit_key_precedence_is_explicit_then_environment_then_file`, `::test_audit_chain_still_verifies_after_restart_with_a_generated_key` |
| **Verified** | No published or hard-coded fallback secret can be used in a file-backed deployment. |

**New finding (defect 9):** losing the key file made every chain verify as `EVENT_HASH_TAMPERED`,
blaming an attacker for a lost key. Now raises `AuditKeyUnavailableError` at open time.

### 2.3 Backup restoration

| | |
|---|---|
| **Source** | `dz_erp/security_audit.py:266-363` (`restore_and_verify_backup`), `:267-273` (`_write_key_file`) |
| **Root cause** | The restore passed the audit and vault secrets **in memory**, so it verified its own work, reported `audit_chains_valid: True`, and wrote no key to disk. Reopening the restored database resolved a brand-new key. |
| **Fix** | Both keys are written to the restored directory with mode `0600` before the database and vault are opened. |
| **Regression test** | `tests/test_phase8_upload_boundary_and_ci.py::test_restored_database_and_vault_are_reopenable_without_the_original_secrets` |
| **Process-boundary test** | `tests/test_phase9_process_boundary.py::test_restore_is_usable_from_a_fresh_process_in_a_different_directory` |
| **Verified** | Restore → terminate → new process → reopen → chain verifies and documents decrypt, with **no secrets supplied**. |

### 2.4 Vault path relocation

| | |
|---|---|
| **Source** | `dz_erp/intake_ocr.py:163-183` (`read_bytes`) |
| **Root cause** | `source_documents.vault_path` stores an **absolute** path, so after a restore into a different directory every reference dangled. |
| **Fix** | Re-anchor by blob name (the content SHA-256, unique per company) under the current vault root when the stored path is not beneath it. `Path.name` cannot contain a separator, so the result stays confined to the company directory. |
| **Regression test** | `tests/test_phase8_upload_boundary_and_ci.py::test_vault_read_reanchors_a_stale_absolute_path` |
| **Verified** | Traversal (`/etc/passwd`, `../../../etc/passwd`, another company's directory) still raises `TenantIsolationError`; cross-company reads still denied. |

### 2.5 Malformed document handling

| | |
|---|---|
| **Source** | `dz_erp/intake_ocr.py:215-232` (`inspect_and_validate_file_security`) |
| **Root cause** | `pypdf.PdfReader` and `PIL.Image.open` were called without guarding parser exceptions. `PdfStreamError` / `PdfReadError` / `UnidentifiedImageError` do not derive from `ValueError`, so they escaped as HTTP 500. |
| **Fix** | Wrapped in `try/except Exception`, re-raised as `ValueError` naming the parser exception type, mapping to a clean 400. |
| **Regression test** | the 6-case `MALFORMED_DOCUMENTS` matrix in `tests/test_phase8_upload_boundary_and_ci.py` |
| **Verified** | Truncated PDF → 400 `(PdfStreamError)`; truncated PNG/JPEG → 400 `(UnidentifiedImageError)`; valid PDF → 200. No internal error disclosed. |

### 2.6 Hostile filenames

| | |
|---|---|
| **Source** | `dz_erp/upload.py:39-46` (`WINDOWS_RESERVED_NAMES`), `:120-156` (`_validate_filename_shape`) |
| **Root cause** | C0 control characters and Windows reserved device names were accepted. |
| **Fix** | Both rejected. `WINDOWS_RESERVED_NAMES` = `CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`. |
| **Regression test** | `HOSTILE_FILENAMES` (13 parametrised) and `BENIGN_FILENAMES` (9) in `tests/test_phase8_upload_boundary_and_ci.py` |
| **Verified** | 17 hostile rejections / 10 benign acceptances. |

**Verified nuance:** control characters in a filename are percent-encoded by the HTTP multipart
layer (`in\nvoice.pdf` arrives as the literal text `in%0Avoice.pdf`), so the endpoint sees an
already-sanitised name. The security property that matters is that **no control character
reaches storage** — asserted directly in
`test_upload_never_stores_a_filename_with_control_characters`, not inferred from a status code.

### 2.7 OpenAPI authentication declarations

| | |
|---|---|
| **Source** | `dz_erp/api.py:196-217` (`_custom_openapi`) |
| **Root cause** | `/api/v1/upload-policy` and `/api/v1/auth/token` were declared `BearerAuth`-protected, overstating the boundary. |
| **Fix** | An explicit three-entry `public_operations` set; public operations omit the `security` key entirely. |
| **Regression test** | `test_openapi_contract_declares_exactly_the_public_operations`, `test_openapi_contract_declares_no_identity_headers_as_parameters` |
| **Verified** | The public set is exactly `["GET /api/v1/upload-policy", "GET /healthz", "POST /api/v1/auth/token"]`; 11 other routes declare `BearerAuth`; **and runtime enforcement matches** — every protected route returns 401 without a token. |

### 2.8 ASGI entry point (`dz_erp/serve.py`)

| | |
|---|---|
| **Source** | `dz_erp/serve.py` (whole module); `:124-146` (`main`) |
| **Verified** | Starts a loopback-only server from `ERP_DB_PATH` / `ERP_VAULT_PATH`; refuses to start against a userless database with exit 2 and actionable instructions; `/metrics`, `/debug`, `/admin` all 404; protected routes 401 without a token; vault and audit key files created at `0600`; clean shutdown. |
| **Regression test** | `tests/test_phase9_process_boundary.py::test_entry_point_refuses_to_build_without_durable_paths`, `::test_entry_point_builds_the_app_when_configured`, `::test_entry_point_refuses_to_start_a_userless_database_without_a_traceback` |

**New finding (defect 10):** `main()` queried the database before `ERPDatabase` had created the
schema, so a fresh deployment died on an unhandled `no such table: users` traceback. Fixed by
opening the database through `ERPDatabase` first. `serve.py` is now at **100% coverage**.

---

## 3. Process-boundary test results

`tests/test_phase9_process_boundary.py` — **15 passed**. Each half of every persistence
operation runs in a separate OS process; the writer has exited before the reader starts.

| Test | What it proves |
|---|---|
| `test_documents_written_in_one_process_decrypt_in_a_fresh_process` | Documents written by process A decrypt in process B with no key supplied |
| `test_audit_events_written_in_one_process_verify_in_a_fresh_process` | The chain written by process A verifies in process B |
| `test_restore_is_usable_from_a_fresh_process_in_a_different_directory` | Restore → terminate → reopen: chain verifies and both documents decrypt via the **original** absolute paths |
| `test_missing_vault_key_fails_loudly_and_writes_no_replacement` | Refuses, and writes **no** replacement key |
| `test_missing_audit_key_fails_loudly_and_writes_no_replacement` | Refuses, and writes no replacement key |
| `test_corrupted_vault_key_fails_loudly` | `ValueError` at construction, before any decryption |
| `test_wrong_vault_key_cannot_decrypt` | A valid but different key yields `InvalidToken` |
| `test_empty_vault_still_generates_a_first_key` | Legitimate first use still works |
| `test_incomplete_restore_is_rejected` | A missing vault blob → `FinancialIntegrityError` |
| `test_repeated_restores_are_idempotent_and_leave_a_usable_deployment` | Three restores, chain valid throughout |
| `test_restore_rejects_a_tampered_database_payload` | A flipped byte is rejected |
| `test_vault_key_is_not_stored_among_the_blobs_it_protects` | The key is never inside the encrypted directory |
| `test_entry_point_refuses_to_build_without_durable_paths` | Missing `ERP_DB_PATH` / `ERP_VAULT_PATH` → exit 1 with a clear message |
| `test_entry_point_builds_the_app_when_configured` | Both set → `FastAPI` |
| `test_entry_point_refuses_to_start_a_userless_database_without_a_traceback` | Fresh DB → exit 2, guidance, **no traceback** |

`tests/test_phase9_durable_security_log.py` — **6 passed**: durable file with no tenant
attribution; a failing sink is not swallowed; short writes detected; 400 concurrent records
neither lost nor interleaved; redaction applied before the sink; no sink configured means
in-memory only.

**No failed verification was repaired by silently generating a replacement key or disabling an
integrity check.** Where a key was missing, corrupt or wrong, the expected outcome was a loud
failure — and the tests assert that no replacement key file was written.

---

## 4. Clean installation, full suite, coverage, lint, CI

### 4.1 Clean installation

```
python3 -m venv /tmp/erp-audit                     # Python 3.11.2
/tmp/erp-audit/bin/python -m pip install --no-deps -r requirements.lock.txt   # exit 0
```

52 installed distributions, all at the pinned versions.

| Check | Command | Exit | Result |
|---|---|---|---|
| Dependency graph | `python tools/verify_dependency_graph.py` | **0** | "OK — the environment matches the declared dependency graph. The only deviation from upstream metadata is the documented substitution opencv-python -> opencv-python-headless." |
| `pip check` | `python -m pip check` | **1** | Exactly one line: `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.` |

The single `pip check` deviation is **intentional and correct**, not a concealed break:
`opencv-python` pulls in `libGL.so.1` and the Qt5 GUI stack, which a headless server has no use
for. `opencv-python-headless` provides the identical `cv2` module without them. Verified live:
`cv2` 4.11.0 imports, RapidOCR instantiates, and both `opencv_python.libs` and `libGL.so.1`
are confirmed absent.

### 4.2 Full suite, coverage, lint, build

| Check | Command | Result |
|---|---|---|
| Test suite + coverage | `python -m pytest -q -p no:cacheprovider --cov=dz_erp --cov-report=term` | **288 passed**, 1 warning, **94%** (2420 stmts, 156 missed), 59.0s |
| Lint | `python -m ruff check dz_erp tests tools` | **All checks passed!** |
| Byte-compile | `python -m compileall -q dz_erp tests tools` | exit 0 |
| Adversarial re-verification | `python /tmp/audit/adversarial.py` | **53 checks passed, 0 findings** |
| Staging acceptance | `python /tmp/audit/staging_acceptance.py` | **25/25 steps passed, 0 failed** |

Per-module coverage: `serve.py` 100%, `net.py` 97%, `auth.py` 97%, `benchmark.py` 98%,
`upload.py` 96%, `odoo_test_adapter.py` 96%, `database.py` 92%, `security_audit.py` 92%,
`intake_ocr.py` 93%, `localization.py` 93%, `procurement_stock.py` 90%, `decimal_utils.py` 80%.

### 4.3 CI runs for this exact commit

Both runs are for `179dc85b81552615a4ef246b9bc2ee08653a7a39` and both concluded **success**:

- **push:** https://github.com/Devmraustro/ERP/actions/runs/39332759637 (7m36s)
- **pull_request:** https://github.com/Devmraustro/ERP/actions/runs/39332764325 (5m47s)

The earlier runs visible in the Actions tab validate earlier commits (`ec8f649` and before) and
are **not** cited as evidence for this one. CI performs no deployment and touches no production
resource.

---

## 5. Remaining findings

| # | Finding | Severity | Remediation |
|---|---|---|---|
| 11 | **OCR accuracy is not statistically established.** 0.9097 overall field exact-match on 18 synthetic documents; `supplier_ai` and `supplier_rc` at 0.667. One image per specification, one sample per degradation cell. | **HIGH for unattended posting** | Keep human review mandatory. Build a powered benchmark (≥100 documents/stratum, n ≥ 30 per degradation cell) with pre-registered thresholds, on real scanned Algerian invoices including French and Arabic script. |
| 12 | **No key rotation mechanism.** | Medium (production) | Plan rotation as a migration with a maintenance window; validate on a copy. Re-chaining the audit log invalidates it. |
| 13 | **No security-log rotation.** | Low | `logrotate`. The log contains no financial data, so retention can be short. |
| 14 | **`/docs` and `/redoc` are reachable and not parameterised by an env var.** | Medium (production) | Disable in `dz_erp/serve.py` before a production build, or place them behind authentication that is not the application's own bearer-token scheme. |
| 15 | **`source_documents.vault_path` stores an absolute path.** | Low | Mitigated by re-anchoring on read; a relative path would be cleaner. |
| 16 | **A lost key file is permanent.** | Medium (operational) | Correct fail-closed behaviour, but the audit chain becomes unverifiable and every document unreadable. Back both key files with the same care as the database. |
| 17 | **`requirements.lock.txt` is honoured with `--no-deps`,** so transitive dependencies are not resolver-validated. | Low | `verify_dependency_graph.py` compensates and passes. Regenerate the lock file on a schedule. |

**Non-finding, retained deliberately:** `AUTH_REQUEST_REJECTED` does not appear in
`audit_events`. `audit_events.company_id` is `NOT NULL` and tenant-scoped, and an
unauthenticated request has no trustworthy tenant to attribute it to. Inventing one would
either blame the wrong tenant or fabricate attribution. The rejections **are** durably recorded
by `JsonlSecurityLog`, which records no company identifier at all.

---

## 6. Staging-readiness matrix

| Requirement | Status | Evidence |
|---|---|---|
| Explicit key provisioning and secure storage | **PASS** | Precedence verified; key files `0600`; never inside the blob directory |
| Keys survive process restarts | **PASS** | `test_documents_written_in_one_process_decrypt_in_a_fresh_process` |
| Missing / invalid / wrong keys fail safely | **PASS** | 5 dedicated tests, incl. "no replacement key written" |
| Durable authentication-failure logging | **PASS** | `JsonlSecurityLog` + 6 tests; failure propagates, short writes detected |
| No invented tenant attribution | **PASS** | Asserted: no `company_id` in any security record |
| `/docs` and `/redoc` disabled in production | **N/A for staging / OPEN for production** | Reachable today (correct for staging); must be disabled before production |
| Loopback-only staging by default | **PASS** | `ERP_HOST` defaults to `127.0.0.1`; off-loopback connection refused |
| Fail-closed forwarded-header trust | **PASS** | Verified: forged `X-Forwarded-For` ignored with no trusted proxies |
| No real customer or financial data | **PASS** | Synthetic users, companies, partners, NIF/NIS/AI/RC and documents throughout |
| Backup restoration tested independently of the writer | **PASS** | Restore → terminate → fresh process, no secrets supplied |
| Human approval mandatory before posting | **PASS** | After OCR: `supplier_invoices=0`, `journal_entries=0`; clerk cannot post (403); self-approval blocked (422) |
| Clean install from the locked dependency set | **PASS** | exit 0; dependency validator exit 0 |
| Full suite, coverage, lint, build | **PASS** | 288 passed, 94%, ruff clean, compileall exit 0 |
| CI green on the verified commit | **PASS** | Two runs, both success |
| Statutory Algerian accounting / tax compliance | **NOT CLAIMED** | Requires qualified local review |

---

## 7. Verdict

> ## CONDITIONAL GO FOR ISOLATED NON-PRODUCTION STAGING

**Conditions** (all are configuration steps, not code defects):

1. Set `ERP_SECURITY_LOG_PATH` to a durable path. Without it, every authentication rejection is
   lost on restart — which is the exact gap this review closed.
2. Provision `ERP_AUDIT_HMAC_SECRET` and `ERP_VAULT_KEY` explicitly from a secret manager, and
   back both up.
3. Keep `ERP_HOST=127.0.0.1` and `ERP_TRUSTED_PROXIES` unset unless a proxy fronts the box.
4. Keep human approval mandatory before any financial posting. Do not remove it on the strength
   of the current OCR numbers.
5. Seed only synthetic or properly anonymised data.

**Not production-ready.** The blocking items are findings 11–14 above: the OCR benchmark gap,
key rotation, security-log rotation, and disabling `/docs` and `/redoc`. None of these are code
defects; all are deployment work that must be completed and re-verified before production
traffic holds real financial data.

---

## Appendix — independently verified vs. taken from earlier reports

**Independently re-verified in this session** (by reading source and running code): all seven
previously reported fixes, their regression tests, the dependency graph and clean install, the
full suite and coverage, lint, byte-compilation, the adversarial result, the staging acceptance
result, and the CI runs.

**Corrected or extended from earlier reports:**

- The earlier report stated the vault key fix was complete. It was not: a *missing* key file was
  still silently replaced (finding 8). Now fixed.
- The earlier report stated a lost audit key was acceptable because verification failed loudly.
  It failed with a **misleading** `EVENT_HASH_TAMPERED` that blames an attacker (finding 9).
  Now named correctly.
- The earlier report did not mention that `dz_erp/serve.py` crashed on a fresh database
  (finding 10). Now fixed.
- The earlier report documented `AUTH_REQUEST_REJECTED` as an open MEDIUM. It is now closed by
  `JsonlSecurityLog`; the tenant-isolation rationale is unchanged and still correct.
- The earlier report's OCR numbers are reproduced exactly (0.9097 / 0.1113 / 0.0079 / 1.0) but
  are **not** upgraded into accuracy claims — see finding 11.
- The earlier report's claim that control characters in filenames are rejected is refined: the
  HTTP layer percent-encodes them, and the invariant actually asserted is that none reach
  storage.

**Not independently verifiable from this repository:** any claim of statutory Algerian
accounting or tax compliance, and any claim about OCR behaviour on real scanned Algerian
invoices.
