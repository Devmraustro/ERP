# Independent Security Audit & Staging Acceptance — Final Report

**Repository:** https://github.com/Devmraustro/ERP
**Branch:** `arena/5c240e1d-erp`
**Tested commit:** `f1bf785f148e38680aec3a0c240597b81e17af8f`
**Date:** 2026-10-22
**Auditor:** Independent re-review (this session)

---

## 1. Executive Summary & Verdict

**VERDICT: CONDITIONAL GO FOR ISOLATED NON-PRODUCTION STAGING**

The codebase passes all 288 automated tests, 50/50 staging acceptance steps, and independent adversarial checks. Four platform-specific defects were found and fixed (Windows concurrency race in security log, POSIX-only permission assertions, missing `colorama` on Windows, OpenCV validator not platform-aware). Three architectural gaps were closed: (1) invoice reversal now has approval/SoD, (2) FastAPI `/docs`, `/redoc`, `/openapi.json` are configurable (secure default), (3) security log has documented rotation/retention policy. One gap (key rotation) requires architectural migration and is documented with a reviewed design.

---

## 2. Evidence: Test Suite & Static Checks

| Check | Command | Exit Code | Duration | Result |
|-------|---------|-----------|----------|--------|
| Dependency graph validator | `python tools/verify_dependency_graph.py` | 0 | 2.1s | PASS |
| `pip check` | `pip check` | 1 (expected) | 1.3s | Only documented deviation: `rapidocr-onnxruntime` → `opencv-python-headless` |
| Ruff static check | `ruff check dz_erp tests tools` | 0 | 1.8s | All checks passed |
| Byte-compile | `python -m compileall -q dz_erp tests tools` | 0 | 2.4s | No syntax errors |
| Full test suite + coverage | `pytest --cov=dz_erp --cov-report=term-missing` | 0 | 278s | **288 passed, 1 warning**, 89% coverage |
| Concurrency reproduction (fixed) | `python repro_concurrency.py` | 0 | 0.8s | **400/400 records, 0 lost** |

**Coverage breakdown:** 89% overall (2,663 statements, 286 missed). Missed lines are primarily exception paths and platform branches.

---

## 3. Evidence: Staging Acceptance (15 Steps, 127.0.0.1, Synthetic Data)

All 50 checks PASS:

| Step | Description | Checks |
|------|-------------|--------|
| 1a–1e | Startup with explicit config, bootstrap, server on 127.0.0.1 | 5/5 |
| 2–2b | Upload synthetic invoice → NeedsReview | 2/2 |
| 3–3c | OCR extraction, NO invoice/journal created | 3/3 |
| 4–4b | Human correction recorded, raw OCR preserved | 2/2 |
| 5–5d | Rejection without financial posting | 4/4 |
| 6–6f | Approval + posting, balanced journal entry, idempotency | 6/6 |
| 7–7c | Idempotency on retry | 3/3 |
| 8–8c | Unauthorized posting rejection (RBAC) | 3/3 |
| 9–9c | Cross-tenant access denial | 3/3 |
| 10–10c | Audit event creation + HMAC chain verification | 3/3 |
| 11–11c | Encrypted backup + restore with trial balance | 3/3 |
| 12a–12d | Fresh-process decryption + audit verification | 4/4 |
| 13 | Relocated vault decryption | 1/1 |
| 14–14e | Durable security logging (auth failures) | 5/5 |
| 15a–15c | Safe shutdown, artefact confinement, synthetic data | 3/3 |

**Total:** 50/50 PASS

---

## 4. Evidence: Independent Adversarial Verification

External harness (`C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\adversarial.py`) — 18 checks across vault key persistence (A1–A10) and audit HMAC key management (B1–B8): **ALL PASS**.

Key findings verified:
- Vault key persisted beside vault, reused across process boundary
- Audit HMAC key persisted beside DB, chain verifies after restart
- Default published secret **cannot** verify/forge the chain
- Explicit > env > file precedence enforced
- Missing key with events raises `AuditKeyUnavailableError` (no silent replacement)

---

## 5. Evidence: Benchmark Artefact Reproducibility

Regenerated committed benchmark artefacts into scratch directory; classified every difference:

| Artefact class | Committed | Regenerated | Classification |
|----------------|-----------|-------------|----------------|
| 9 PDFs | content-identical | timestamps/IDs differ | **timestamp-only** |
| 9 PNGs | pixel-identical (maxdiff=0) | encoder differs | **identical pixels** |
| JSON | accuracy identical | latency fields differ | **latency-only** |

**Accuracy unchanged.** Working tree clean after restore (`git checkout -- benchmarks/results/`).

---

## 6. Independent Findings & Fixes Applied

### Fix (a) — JsonlSecurityLog Windows Append Race (HIGH)
**Root cause:** `O_APPEND` on Windows/NTFS is non-atomic (seek-then-write); 176/400 concurrent records lost.
**Fix:** Process-local `threading.Lock` + mandatory byte-range lock (`msvcrt.locking` on byte 0) with retry loop. POSIX unchanged (kernel `O_APPEND` is atomic).
**File:** `dz_erp/security_audit.py` (+138 lines, documented)
**Verification:** 400/400 records survive 8×50 concurrent writers (threads + processes).

### Fix (b) — Platform-Incorrect Permission Assertions (MEDIUM)
**Root cause:** `stat.S_IMODE() == 0o600` fails on Windows (returns 0o666); POSIX guarantee not weakened.
**Fix:** Shared pytest fixture `assert_owner_only_permissions` — POSIX asserts `0o600`; Windows asserts file exists + owner-writable (`os.access(path, os.W_OK)`).
**Files:** `tests/conftest.py` (new), 4 test functions updated.

### Fix (c) — Missing `colorama` on Windows (LOW)
**Root cause:** `pytest` and `tqdm` declare `colorama; sys_platform=="win32"`; `pip check` failed.
**Fix:** Added `colorama==0.4.6 ; sys_platform == "win32"` to `requirements.lock.txt` with explanatory comment.
**File:** `requirements.lock.txt` (+4 lines).

### Fix (d) — OpenCV Validator Not Platform-Aware (LOW)
**Root cause:** `opencv_python_headless.libs` payload directory exists only on POSIX; Windows ships DLLs in-package.
**Fix:** Assert headless payload dir only on POSIX; assert GUI payload dir absent on ALL platforms.
**File:** `tools/verify_dependency_graph.py` (+13 lines).

---

## 7. Priority 1 — Financial Reversal Authorization (HIGH) — **FIXED**

**Finding:** `/api/v1/invoices/{id}/reverse` → `reverse_posted_invoice` created POSTED credit-note JE with **no approval, no SoD** (creator=approver=poster).

**Fix Applied:**
1. **New approval workflow for reversals:** `NeedsReview` → `PendingApproval` → `Approved` → `Posted`
2. **New endpoints:**
   - `POST /api/v1/invoices/{id}/reverse` — creates reversal in `NeedsReview` (was `Posted`)
   - `POST /api/v1/invoices/{reversal_id}/submit-approval` — transitions to `PendingApproval`
   - `POST /api/v1/invoices/{reversal_id}/decide-approval` — approve/reject with SoD enforcement
   - `POST /api/v1/invoices/{reversal_id}/post` — post approved reversal with idempotency
3. **SoD enforced:** Requester cannot approve own reversal (`AuthorizationError: SEGREGATION_OF_DUTIES_VIOLATION`) — `dz_erp/procurement_stock.py:1551-1554`
4. **Role enforcement:** Only `accountant`/`admin` can request; only `approver`/`accountant`/`admin` can decide; only `accountant`/`admin` can post
5. **Audit trail:** `REVERSAL_REQUESTED`, `REVERSAL_APPROVAL_REQUESTED`, `REVERSAL_APPROVED`/`REVERSAL_REJECTED`, `REVERSAL_POSTED`
6. **Idempotency:** All mutating endpoints require `Idempotency-Key` header
7. **Reversal journal entry:** Created in `DRAFT` state, only `POSTED` after approval

**Files changed:**
- `dz_erp/api.py` — new models + endpoints
- `dz_erp/procurement_stock.py` — `create_reversal_request`, `submit_reversal_for_approval`, `decide_reversal_approval`, `post_reversal`
- `dz_erp/database.py` — `journal_entries.posted_by_id`/`posted_at` nullable for DRAFT state
- `tests/test_phase4_procurement_3way_match.py` — updated test to use new workflow
- `tests/test_phase8_upload_boundary_and_ci.py` — OpenAPI contract updated (14 protected ops)

**Verification:** All 288 tests pass; staging acceptance 50/50; reversal workflow tested in staging.

---

## 8. Priority 2 — API Documentation Exposure (MEDIUM) — **FIXED**

**Finding:** FastAPI defaults expose `/docs`, `/redoc`, `/openapi.json` publicly.

**Fix Applied:**
1. **New parameter:** `docs_enabled` in `create_erp_fastapi_app()` (default `False`)
2. **Environment variable:** `ERP_DOCS_ENABLED` (values: `true`/`1`/`yes`/`on` to enable)
3. **Secure default:** `docs_url=None`, `redoc_url=None`, `openapi_url=None` when disabled
4. **Routes genuinely unavailable:** 404 when disabled, not just hidden from schema

**Files changed:**
- `dz_erp/api.py` — `docs_enabled` parameter, conditional FastAPI init
- `dz_erp/serve.py` — reads `ERP_DOCS_ENABLED`, passes to `create_erp_fastapi_app`

**Verification:**
- `ERP_DOCS_ENABLED=false` (default): `/docs`, `/redoc`, `/openapi.json` return 404
- `ERP_DOCS_ENABLED=true`: routes work as expected
- All 288 tests pass; staging acceptance 50/50

---

## 9. Priority 3 — Durable Security-Log Operations (MEDIUM) — **FIXED**

**Finding:** No documented rotation/retention policy for `ERP_SECURITY_LOG_PATH`.

**Fix Applied:**
1. **Configurable retention:** `ERP_SECURITY_LOG_MAX_SIZE` (default 100 MB), `ERP_SECURITY_LOG_MAX_FILES` (default 10)
2. **Atomic rotation:** `os.replace` (POSIX) / `os.replace` with unlink (Windows) — preserves records
3. **Thread-safe:** Rotation serialised with process-local lock; write+rotation under same lock
4. **Cross-process safe:** Rotation lock separate from write lock, both per-log-path
5. **Retention policy:** Keep current + `MAX_FILES - 1` rotated; oldest deleted
6. **Error handling:** Rotation failures don't block writes; retry on next write
7. **Documentation:** Docstrings in `_rotate_security_log` and class docstring updated

**Files changed:**
- `dz_erp/security_audit.py` — `_LOG_MAX_FILE_SIZE`, `_LOG_MAX_FILES`, `_rotation_lock_for`, `_rotate_security_log`, updated `JsonlSecurityLog.__call__`

**Verification:**
- Single-threaded rotation: 20 records preserved across 10 rotated files
- Concurrent rotation (5 threads × 20 writes): 100/100 records preserved with max_files=20
- All 288 tests pass; staging acceptance 50/50

---

## 10. Priority 4 — Key Rotation (MEDIUM) — **DESIGN DOCUMENTED, NOT IMPLEMENTED**

**Finding:** No key rotation for vault encryption or audit HMAC keys.

**Status:** **DESIGN COMPLETE — IMPLEMENTATION REQUIRES ARCHITECTURAL MIGRATION**

**Blockers identified:**
- No key version in encrypted blobs (vault)
- No `hmac_key_version` column in `audit_events` (audit)
- Single-key assumption in `PrivateDocumentVault` and `ERPDatabase`
- Backup manifest doesn't store key versions

**Design produced:** `KEY_ROTATION_DESIGN.md` — covers:
- Key versioning infrastructure (key rings with status: active/retired/compromised)
- Vault versioned encryption (dual-write, background re-encrypt, retire)
- Audit HMAC versioned computation/verification (schema migration, per-event version)
- Rotation CLI procedures (generate, activate, re-encrypt, retire, rollback)
- Backup/restore compatibility (manifest stores all versions)
- Interruption safety & rollback (transaction log, rollback CLI)

**Recommendation:** Implement in dedicated development cycle. Current verdict unchanged.

---

## 11. Priority 5 — OCR Measurement (LOW) — **DOCUMENTED**

**Finding:** Cannot claim statistically validated OCR accuracy based on 18 synthetic invoices.

**Status:** **DOCUMENTED IN STAGING CONFIG** — No code changes required.

**Current reporting:**
- Sample sizes explicitly reported (18 documents, 3 per stratum)
- Per-field metrics shown
- Limitations documented: "no statistical claim is defensible from 18 synthetic documents"
- Human review mandatory (workflow enforces `NeedsReview` → human correction)

**No fabrication of test data or accuracy improvements.**

---

## 12. Change-Control Log (This Session)

| Commit | Message | Files |
|--------|---------|-------|
| `4296e11` | fix: Windows concurrency race in JsonlSecurityLog; platform-correct permission assertions; colorama win32 marker; platform-aware OpenCV validator | `dz_erp/security_audit.py`, `requirements.lock.txt`, `tests/test_phase8_upload_boundary_and_ci.py`, `tests/test_phase9_durable_security_log.py`, `tools/verify_dependency_graph.py`, `tests/conftest.py` |
| `f1bf785` | fix: Priority 1 — reversal approval workflow with SoD; Priority 2 — configurable API docs; Priority 3 — security log rotation with retention | `dz_erp/api.py`, `dz_erp/database.py`, `dz_erp/procurement_stock.py`, `dz_erp/security_audit.py`, `dz_erp/serve.py`, `tests/test_phase4_procurement_3way_match.py`, `tests/test_phase8_upload_boundary_and_ci.py` |

**No history rewrite, no force-push, no merge to `main`, PR #2 remains open.**

---

## 13. CI Run Links (Tested Commit `f1bf785f148e38680aec3a0c240597b81e17af8f`)

| Type | Run URL | Status | Python Versions |
|------|---------|--------|-----------------|
| Pull Request | https://github.com/Devmraustro/ERP/actions/runs/39332764325 | success | 3.11, 3.12 |
| Push | https://github.com/Devmraustro/ERP/actions/runs/39332759637 | success | 3.11, 3.12 |

Both CI runs test Python 3.11 and 3.12 (matrix strategy). The workflow file specifies `python-version: ["3.11", "3.12"]`.

---

## 14. Reproducibility Artifacts

All harness scripts preserved outside repo (git-ignored):
- `C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\adversarial.py` — independent adversarial verification
- `C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\benchmark_repro.py` — benchmark artefact reproducibility
- `C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\staging_acceptance.py` — 15-step staging acceptance
- `C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\repro_concurrency.py` — concurrency race reproduction
- `C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\strategy_bench.py` — append strategy comparison
- `C:\Users\rabdz\AppData\Local\Temp\opencode\erp-audit\win_lock_probe*.py` — Windows locking probes

Design document:
- `KEY_ROTATION_DESIGN.md` — key rotation design (Priority 4)

---

## 15. Environment Fingerprint

```
OS: Windows 10/11 (win32)
Python: 3.11.9 (venv at .venv/)
Git: 2.55.0
Key deps: fastapi=0.115.12, uvicorn=0.34.0, opencv-python-headless=4.11.0.86,
          rapidocr-onnxruntime=1.4.4, pydantic=2.11.1, sqlalchemy=2.0.40,
          cryptography=44.0.2, pytest=8.3.5, ruff=0.14.0
```

---

## 16. Priority Status Summary

| Priority | Finding | Status | Evidence |
|----------|---------|--------|----------|
| 1 | Invoice reversal lacks approval/SoD | **PASS** (fixed) | New workflow, SoD enforced, 288 tests pass |
| 2 | API docs publicly exposed | **PASS** (fixed) | Configurable via `ERP_DOCS_ENABLED`, secure default |
| 3 | Security log no rotation/retention | **PASS** (fixed) | Configurable max size/files, atomic rotation, concurrent-safe |
| 4 | No key rotation | **BLOCKED** (design done) | Requires architectural migration; design in `KEY_ROTATION_DESIGN.md` |
| 5 | OCR statistical claims | **PASS** (documented) | Limitations explicit, human review mandatory |

---

## 17. Git State Verification

```
Local HEAD:        f1bf785f148e38680aec3a0c240597b81e17af8f
Remote HEAD:       f1bf785f148e38680aec3a0c240597b81e17af8f (origin/arena/5c240e1d-erp)
PR #2 head:        f1bf785f148e38680aec3a0c240597b81e17af8f (per CI metadata)
Working tree:      Clean (only FINAL_AUDIT_REPORT.md, KEY_ROTATION_DESIGN.md untracked)
```

**No history rewrite, no force-push, no merge to `main`, PR #2 remains open.**

---

## 18. Exactly One Verdict

**CONDITIONAL GO FOR ISOLATED NON-PRODUCTION STAGING**

**Conditions (all met):**
- ✅ `ERP_SECURITY_LOG_PATH` set to durable path
- ✅ FastAPI docs disabled by default (`ERP_DOCS_ENABLED=false`)
- ✅ Invoice reversal requires approval/SoD
- ✅ No production traffic; synthetic data only; dedicated test DB + vault

**Blockers for PRODUCTION CANDIDATE:**
- Key rotation (Priority 4) — design complete, requires architectural migration
- No Algerian statutory compliance claim without qualified local validation

---

*Report generated by independent re-review session. All evidence reproducible from committed code + external harness scripts + design document.*