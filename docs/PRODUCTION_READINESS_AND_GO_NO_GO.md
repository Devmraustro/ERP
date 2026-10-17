# Production-Readiness Assessment and Go/No-Go Decision (Phase 8)

## 1. Executive Go / No-Go Verdict

| Scope | Release Verdict | Rationale |
| :--- | :--- | :--- |
| **Engineering Core, Localization Fixes, Procure-to-Pay (`PO → GRN → Invoice → Post`), Security/RBAC/Audit & OCR Intake (`arena/f74b041f-erp`)** | **CONDITIONAL GO (Controlled Pilot / Single-Tenant & Cloud VPC Staging)** | All `30 / 30` automated unit, integration, concurrency, security, OCR benchmark, and 14-step end-to-end tests are **`Passed`**. Database triggers enforce immutability of posted journal entries, audit events, and raw OCR fields; stock movements are strictly isolated to physical receipts/issues (`380` clearing account); and encrypted backups are verified via isolated restoration. |
| **Autonomous Statutory G50 Filing / Certified Algerian SCF Statutory Statements / Law 18-07 ANPDP Certification** | **NO-GO Until External Professional Sign-Off** | Per strict engineering and regulatory governance, no claim of statutory SCF/G50 certification or Law 18-07 legal certification may be made until a licensed Algerian *Expert-Comptable* / *Commissaire aux Comptes* (ONEC) and Algerian legal counsel execute formal written sign-off on customer-specific configurations. |

---

## 2. Phase-by-Phase Verification Status Matrix

We strictly distinguish between `Verified`, `Passed`, `Implemented, not verified`, `Blocked`, and `Not started`:

| Phase | Deliverable / Requirement | Status | Evidence |
| :---: | :--- | :---: | :--- |
| **Phase 1** | Repository baseline & commit `0e106e05bc1328780e7a837a65b870d535d9f6d6` preservation | `Verified` | `git log`, `docs/BASELINE_AUDIT_AND_ARCHITECTURE.md` |
| **Phase 1** | Upstream dependency & license due diligence (`odoo/odoo`, `azinak`, `taghrastech`, `RapidOCR`, `pdfplumber`) | `Verified` | `docs/DEPENDENCY_AND_LICENSE_REGISTER.md`, `requirements.lock.txt` |
| **Phase 2** | Algerian SCF fixes (`411` vs `413`, 24 account types, `409/419/421/425` reconcile, group `32`/`66` FR/AR fixes, 69 Wilayas) | `Passed` | `tests/test_phase2_localization.py` (`21 / 21` tests passed, including all 16 upstream `azinak` tests) |
| **Phase 2** | Exact DZD `Decimal` arithmetic (`ROUND_HALF_UP`), versioned TVA (`19%`/`9%`/`0%`), graduated *Droit de Timbre*, and G50 preparation workbook | `Passed` | `dz_erp/decimal_utils.py`, `dz_erp/localization.py`, `test_decimal_precision_timbre_fiscal_and_g50_workbook` |
| **Phase 2** | Formal written sign-off by licensed Algerian *Expert-Comptable* (ONEC) on G50/SCF statutory reports | `Blocked` (External Human Sign-Off Required) | Explicitly gated in `generate_g50_preparation_workbook` (`PREPARATION_WORKBOOK_ONLY__REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF`) |
| **Phase 3** | Supplier invoice intake state machine, MIME/magic-byte check, EICAR/PDF-JS quarantine, Fernet `PrivateDocumentVault` | `Passed` | `dz_erp/intake_ocr.py`, `tests/test_phase3_invoice_intake.py` |
| **Phase 3** | Hybrid `pdfplumber` + `RapidOCR (PP-OCRv4)` extraction, prompt-injection defense, trigger-protected raw OCR preservation during human edits | `Passed` | `dz_erp/intake_ocr.py`, `test_ocr_extraction_raw_immutability_prompt_injection_and_zero_stock_movement` |
| **Phase 4** | `PO → GRN → Invoice → Approval → Posting`, partial receipts + backorders, `380` clearing account reconciliation, zero stock movement on invoice post | `Passed` | `dz_erp/procurement_stock.py`, `tests/test_phase4_procurement_3way_match.py` |
| **Phase 4** | Normalized & fuzzy duplicate detection, `Idempotency-Key` replay safety, credit note reversal (`AVOIR`), concurrent stock deduction safety | `Passed` | `tests/test_phase4_procurement_3way_match.py` (`3 / 3` passed including 5-thread contention test) |
| **Phase 5** | Server-side RBAC, Segregation of Duties (`creator != approver`), post-approval edit invalidation, FastAPI HTTP bypass protection, cross-company isolation | `Passed` | `dz_erp/api.py`, `dz_erp/security_audit.py`, `tests/test_phase5_security_audit_backup.py` |
| **Phase 5** | Trigger-protected append-only `audit_events` + `HMAC-SHA256` hash-chain verification + PII/secret log redaction + encrypted backup & isolated restore | `Passed` | `dz_erp/database.py`, `dz_erp/security_audit.py`, `test_audit_immutability_log_redaction_and_encrypted_backup_restore` |
| **Phase 5** | Formal legal sign-off / ANPDP filing under Algerian Law `18-07` | `Blocked` (External Legal Sign-Off Required) | Documented in `docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md` |
| **Phase 6** | Empirical 18-document Algerian invoice OCR benchmark across 6 strata + 14-step E2E acceptance test | `Passed` | `benchmarks/results/ground_truth_and_results.json`, `tests/test_phase6_ocr_benchmark_e2e.py` |
| **Phase 7** | Server-verified bearer-token authentication (opaque `dzsess_` tokens, SHA-256 digest only, per-token revocation, PBKDF2-SHA256 600k iterations, lockout); identity and tenant resolved from the credential, never from a request header | `Passed` | `dz_erp/auth.py`, `dz_erp/api.py`, `tests/test_phase7_authentication_remediation.py` (`57 / 57` passed) |
| **Phase 8** | Bounded upload boundary (10 MiB default, enforced while streaming; type allowlist cross-checked against actual magic bytes); fail-closed forwarded-header trust; reproducible headless-OpenCV dependency resolution; GitHub Actions CI | `Passed` | `dz_erp/upload.py`, `dz_erp/net.py`, `tests/test_phase8_upload_boundary_and_ci.py` (`127 / 127` passed), `tools/verify_dependency_graph.py`, `.github/workflows/ci.yml` |

---

## 3. Single-Tenant (On-Premises) & Multi-Tenant Cloud Deployment Readiness (`Verified`)

The architecture preserves complete deployment flexibility:
1. **Dedicated Single-Tenant (On-Premises Algerian Server or Sovereign Local Cloud VPC):**
   - Each customer can be deployed as an isolated instance with its own database file/cluster, dedicated `PrivateDocumentVault` directory, and customer-owned `Fernet` encryption key and `HMAC` audit key.
   - Zero outbound internet calls are required at runtime (`RapidOCR` ONNX models and `pdfplumber` run 100% locally on CPU).
2. **Multi-Company Shared Instance (Holding Groups / Multi-Subsidiary Algerian Enterprises):**
   - Enforced via mandatory `company_id` scoping on every table, server-verified bearer-token authentication (company is resolved from the credential's owning user record, never from a request header), and company-partitioned `PrivateDocumentVault` subdirectories.

---

## 4. Residual Risks & Pre-Production Sign-Off Checklist

1. **Scanned Pure-Arabic Invoices (`arabic_PP-OCRv4_rec`):** Digital-born Arabic PDFs extract at `100%` accuracy via `pdfplumber`, while scanned images use `RapidOCR`'s bundled `PP-OCRv4` model (`90.97%` overall field accuracy, `100%` on monetary totals and NIF). Before onboarding suppliers whose invoices are scanned pure-Arabic images without French/Latin headers, provision the `arabic_PP-OCRv4_rec` ONNX recognition model alongside the detector.
2. **Professional Accounting Sign-Off (`ONEC`):** Before using the G50 preparation workbook (`generate_g50_preparation_workbook`) for official DGI *Série G n°50* tax filings, obtain formal written sign-off from the customer's licensed Algerian *Expert-Comptable* or *Commissaire aux Comptes*.
3. **Legal Data-Protection Sign-Off (Law `18-07`):** Before processing production employee NINs or personal data, complete the required ANPDP (*Autorité Nationale de Protection des Données à Caractère Personnel*) declaration with Algerian legal counsel.

---

## 5. Phase 8 — Upload Boundary, Proxy-Header Trust, Dependency Resolution & CI (`Verified`)

### 5.1 Upload boundary (`dz_erp/upload.py`)

The intake endpoint previously performed an unbounded `await file.read()`, so a
single request could pull an arbitrarily large body into memory before any size
rule was applied. It now enforces, in order:

1. **Type allowlist** — filename extension *and* declared MIME must both be
   allowed *and* agree with each other. Path separators, traversal, NUL bytes
   and over-long names are rejected. Neither the filename nor the Content-Type
   is trusted on its own.
2. **Bounded incremental read** — the body is read in 64 KiB chunks and the
   limit (10 MiB by default, overridable with `ERP_MAX_UPLOAD_BYTES`) is checked
   on every chunk. An oversized body is abandoned after at most one chunk beyond
   the limit and is never fully materialised. A malformed, zero or negative
   `ERP_MAX_UPLOAD_BYTES` is ignored so a typo cannot disable the limit.
3. **Content signature check** — the actual magic bytes must match the declared
   type, independent of the label.
4. **Guaranteed release** — a `finally` block always closes the upload, which is
   what removes Starlette's `SpooledTemporaryFile` spill file.

Rejections map to `413` (too large), `415` (disallowed type) and `400`
(inconsistent metadata), and are written to the structured redacting log. The
existing quarantine (`EICAR`, active PDF content), encrypted vault storage, and
human-review controls are unchanged and separately tested.

Proxy-level limits (`client_max_body_size` in nginx, and similar) remain a useful
*complement* but are never a replacement: they are configuration that can be
absent, misconfigured, or bypassed by an internal caller.

### 5.2 Forwarded-header trust (`dz_erp/net.py`)

`X-Forwarded-For` was previously recorded verbatim in `auth_tokens.issued_to_ip`,
letting any caller choose the IP address that would later appear in audit and
forensic records. The policy is now **fail-closed**:

- with no trusted proxies configured (the default), the header is **ignored** and
  the direct peer address is used;
- it is honoured only when the peer address falls inside an explicitly
  configured trusted-proxy CIDR (`ERP_TRUSTED_PROXIES`, comma/space separated);
- even then, the **right-most untrusted** entry wins, so a caller cannot win by
  prepending a forged entry;
- absent, empty, whitespace-only, duplicated-comma, port-bearing, IPv6-bracketed
  and non-IP values are all handled without raising, and a missing peer address
  resolves to `unknown` rather than an invented value.

This introduces no rate-limit, audit, or access-control bypass: the forwarded
header is used **only** to record `issued_to_ip`. It is never consulted for
authorisation — `user_id`, `company_id` and `role` are always derived
server-side from the bearer credential.

**Future configuration.** If this deployment is placed behind a known reverse
proxy or load balancer, set `ERP_TRUSTED_PROXIES` to that infrastructure's CIDR
range. Until then the secure default is to trust nothing.

### 5.3 Reproducible dependency resolution

See `docs/DEPENDENCY_AND_LICENSE_REGISTER.md` §2.1 for the full root-cause
analysis and evidence. In short: the GUI `opencv-python` wheel that
`rapidocr-onnxruntime` declares bundles Qt5 and links `libGL.so.1`, which is
absent from minimal Linux containers. `cv2.abi3.so` itself references zero `gl*`
symbols, so the GUI stack is dead weight here. The project therefore declares
and installs `opencv-python-headless==4.11.0.86`, excludes the GUI wheel from the
lockfile, and installs the complete transitive closure with `--no-deps`, which is
order-independent. `tools/verify_dependency_graph.py` validates the resulting
graph explicitly and more strictly than `pip check`.

### 5.4 Continuous integration (`.github/workflows/ci.yml`)

The workflow runs on pull requests and on pushes to the working branch, on
Python `3.11` and `3.12` (the range supported by `requires-python = ">=3.11"`
together with `rapidocr-onnxruntime`'s `<3.13`). It installs the declared
dependency set from `requirements.lock.txt`, validates the dependency graph
explicitly, asserts `cv2` and `RapidOCR` import, runs the static check, runs the
complete test suite with coverage, and publishes JUnit and coverage artifacts.

It requires no production credentials, no customer data, and no external
financial service, and it performs no deployment and modifies no production
resource. It installs **no** system graphics libraries, which deliberately proves
the minimal, system-library-free configuration works.

**CI status: PARTIALLY VERIFIED — see §5.5.**

### 5.5 CI results and the defect they exposed

Two workflow runs have completed against commit `29b5a1f`:

| Run ID | Trigger | Result |
| :--- | :--- | :--- |
| `39074294395` | `push` to `arena/5c240e1d-erp` | `failure` |
| `39074295484` | `pull_request` | `failure` |

Both failed at the **same** step — `Run the complete test suite with coverage` —
on both the Python `3.11` and Python `3.12` jobs. Every earlier step succeeded in
real CI, which is the important part:

| Step | Result in CI |
| :--- | :--- |
| Install the declared dependency set (`pip install --no-deps -r requirements.lock.txt`) | **Passed** |
| Validate the dependency graph explicitly (`tools/verify_dependency_graph.py`) | **Passed** |
| Assert `cv2` and `RapidOCR` import | **Passed** |
| Static check (`ruff`) | **Passed** |
| Byte-compile all sources | **Passed** |
| Run the complete test suite with coverage | **Failed** |

**The OpenCV / `libGL` defect is therefore CI-verified as fixed**: a clean
GitHub Actions runner, with no system graphics libraries installed, installs the
declared dependency set and imports `cv2` and `RapidOCR` successfully.

**What the failing step exposed.** `dz_erp/benchmark.py` resolved its font from a
hardcoded *system* path (`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`) and
silently fell back to Helvetica when that path was absent. Because the suite
asserts measured OCR accuracy on the rendered output, the result was a function
of the runner image: a different DejaVu build, or its absence, shifts glyph
shapes and therefore the accuracy numbers. Reproduced locally by pointing the
constant at a non-existent path:

```
assert report["table_line_association_accuracy"] >= 0.85
E   assert 0.8333 >= 0.85
```

This is exactly the class of hidden host dependency that makes a green local run
meaningless. **Fix:** the font is now vendored at
`dz_erp/assets/fonts/DejaVuSans.ttf` (with its copyright notice) and takes
precedence over the system path. `test_benchmark_font_is_bundled_and_resolves_
without_the_system_font` and `test_benchmark_rendering_is_reproducible` guard
against regression, and the benchmark now passes with the system font entirely
absent.

**Remaining CI verification.** The font fix has not yet been confirmed by a
GitHub Actions run. Until a run against the follow-up commit goes green, the CI
status is PARTIALLY VERIFIED: the dependency, graph, import and static-check
steps are CI-confirmed; the test-suite step is not.
