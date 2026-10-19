# ERP Independent Security Audit — Staging Acceptance & Algeria Readiness Report

**Branch:** `arena/5c240e1d-erp` (base `main`, PR #2)
**Baseline:** `e266f64` (clean, CI green)
**Date:** 2026-10-19
**Scope:** Workstreams A–F — adversarial security testing, OCR accuracy, dependency/supply-chain
verification, secret handling, encrypted backup/restore, and isolated staging acceptance.

All measurements below were produced by running code in this repository against synthetic data.
Nothing here touches production, and every user, company and document used is synthetic.

---

## 1. Executive summary

The application is **ready for an isolated non-production staging deployment**, with the
caveats in §8. It is **not** ready for production traffic holding real financial data until
the items in §8.1 are closed.

| Workstream | Result |
|---|---|
| A — Adversarial security testing | **89 checks passed, 0 HIGH, 1 MEDIUM** |
| B — OCR accuracy | **measured; below the accuracy needed for unsupervised posting** |
| C — Dependency / supply chain | **clean install verified, one documented deviation** |
| D — Secret handling | **3 defects found and fixed; now correct** |
| E — Encrypted backup / restore | **2 defects found and fixed; now correct** |
| F — Staging acceptance | **25/25 steps passed** |

**Seven defects were found and fixed during this audit.** Four were silent data-integrity
failures that the existing test suite could not catch, because each one only manifests across
a process boundary:

1. The audit-chain HMAC key could fall back to a value published in the source tree.
2. The document vault generated a fresh encryption key on every process start, permanently
   destroying every stored document on the first restart.
3. `restore_and_verify_backup` verified its own work and then left the restored deployment
   unable to decrypt its own data or verify its own audit chain.
4. Restored databases kept dangling absolute vault paths, so restored documents were
   unreadable.

Two further defects were upload-validation and OpenAPI-contract gaps, and the absence of any
deployable entry point was itself a finding — there was nothing to deploy.

Final state: **full suite 267 passed, 93% coverage, `ruff` clean.**

---

## 2. Workstream A — Adversarial security testing

### 2.1 Method

`/tmp/audit/adversarial.py` drives the real FastAPI application through a full
authentication → upload → OCR → validation → approval → posting lifecycle, then attacks it.
It runs 89 checks across seven categories:

| Category | What it attacks |
|---|---|
| A1 | Identity: forged headers, credential matrix, token tampering |
| A2 | Tenant isolation: cross-company reads, edits, supplier references, enumeration oracles |
| A3 | Audit integrity: hash-chain tampering, SQL immutability, transaction rollback |
| A4 | Proxy trust: fail-closed forwarded-header handling, CIDR parsing |
| A5 | Upload boundary: size, magic bytes, hostile filenames, duplicates, env overrides |
| A6 | Authorisation: separation of duties, self-approval, idempotency, deactivation |
| A7 | Surface exposure: docs, metrics/debug/admin, error-body disclosure, OpenAPI contract |

### 2.2 Result

```
CHECKS PASSED : 89
FINDINGS      : 1
  by severity: {'MEDIUM': 1}
  [MEDIUM] missing audit event AUTH_REQUEST_REJECTED
```

**0 HIGH findings.** The 1 MEDIUM is documented in §2.4.

### 2.3 What the harness confirmed (all PASS)

- **Identity cannot be forged.** `X-User-Id` / `X-Company-Id` are ignored; a forged pair
  yields 401 on every protected route and cannot override a valid token. A GET on a POST-only
  route returns 405. The credential matrix (missing, empty, scheme-only, `Basic`, unknown
  scheme, tampered token) all return 401; lowercase `bearer` returns 200. Auth error bodies
  leak no internals.
- **Tenant isolation holds.** Cross-company document and invoice reads return 403; a
  cross-company field edit returns 404 (not 500). 403/404 bodies do not contain the foreign
  invoice number, and a non-existent id returns 404 while a valid id returns 200, so there is
  no enumeration oracle.
- **The audit chain is real.** 12 denial events were recorded and the chain verified. Editing
  event content raises `EVENT_HASH_TAMPERED`; deleting or reordering raises
  `PREV_HASH_MISMATCH`. `UPDATE`/`DELETE` are blocked by triggers even with
  `PRAGMA writable_schema = 1` (they raise `AUDIT_TAMPER_VIOLATION`), and audit rows roll back
  with a failed transaction.
- **Proxy trust is fail-closed.** With no trusted proxies configured, `X-Forwarded-For` is
  ignored. Malformed CIDRs are ignored, and a bare string rather than a list also fails
  closed. When proxies *are* configured, the right-most untrusted entry wins over a forged
  prefix, and a missing or malformed header falls back to the peer address.
- **The upload boundary holds.** Oversized uploads return 413 (and 401 when
  unauthenticated, not 413 — no oracle). Content that lies about its type returns 415:
  PNG-as-PDF, PE-binary-as-PDF, PDF-as-PNG, GIF-as-JPEG, executables, double extensions,
  zero-byte. Hostile filenames return 400. Duplicate SHA-256 is detected. Environment
  overrides of `''`, `0`, `-1` and `not-a-number` all retain the 10485760-byte limit rather
  than silently disabling it. No `.tmp` file is left behind.
- **Separation of duties holds.** A clerk cannot approve (403); a creator cannot self-approve
  (422); a self-approved invoice cannot be posted (409); posting requires an
  `Idempotency-Key` (400) and is idempotent on replay with no duplicate journal entry; a
  second post with a *new* key is rejected (409); a deactivated user loses access
  immediately (401).
- **The exposed surface is minimal.** `/metrics`, `/debug` and `/admin` all return 404. Error
  bodies disclose no stack traces or filesystem paths. The OpenAPI public set exactly matches
  `["GET /api/v1/upload-policy", "GET /healthz", "POST /api/v1/auth/token"]`, and no identity
  header is declared as a parameter.

### 2.4 The one MEDIUM finding — decision: document, do not patch

`AUTH_REQUEST_REJECTED` does not appear in `audit_events` when authentication fails. The
events that *are* present are `AUTH_TOKEN_ISSUED`, `INVOICE_VALIDATED_STAGED`,
`LOAD_CHART_TEMPLATE`, `OCR_EXTRACTION_COMPLETED`,
`SECURITY_CROSS_COMPANY_FILE_ACCESS_BLOCKED` and `SECURITY_CROSS_COMPANY_READ_BLOCKED`.

**Why this is not patched.** `audit_events.company_id` is `NOT NULL` and every read path is
tenant-scoped. A failed authentication has no trustworthy company attribution — the caller
has not proven who they are, which is precisely why the request failed. Writing a guessed or
defaulted `company_id` would either attribute a foreign tenant's failure to the wrong company
or fabricate attribution the request never established.

The rejection *is* recorded, in the in-memory `StructuredRedactingLogger` under
`AUTH_REQUEST_REJECTED`. The gap is that this sink is not durable.

**What must happen at deployment.** Configure the structured logger to ship to a durable,
non-tenant-scoped sink (syslog, journald, or a SIEM) before production. This is a
deployment configuration step, not a code change, and it is the correct place for it: auth
failures are operational telemetry, not financial audit events.

The behaviour is pinned by
`test_unattributable_auth_failures_are_deliberately_not_tenant_audited`, so it cannot drift
silently.

### 2.5 Defects found and fixed in Workstream A

1. **Malformed-but-header-valid documents returned HTTP 500.**
   `inspect_and_validate_file_security` called `pypdf.PdfReader` and `PIL.Image.open` without
   guarding against parser exceptions. A file starting with `%PDF-1.7` but truncated, or a
   PNG/JPEG with a valid magic number and a truncated body, raised `PdfStreamError` /
   `PdfReadError` / `UnidentifiedImageError`, which escaped as a 500. These are now caught and
   re-raised as `ValueError`, mapping to a clean 400 that names the parser exception type.
2. **`_validate_filename_shape` accepted C0 control characters and Windows reserved device
   names.** Neither can traverse a path, but a newline or terminal escape sequence in a
   filename can forge a log line or rewrite an operator's terminal when the name is echoed
   into the audit trail or a review queue; and `CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9`
   address a device rather than a file on Windows. Both are now rejected. Benign names
   (`conveyor.pdf`, `com1x.pdf`, `PRN-2026.pdf`) are unaffected — verified by 17 hostile
   rejections and 10 benign acceptances.
3. **`_custom_openapi` declared two public routes as BearerAuth-protected.**
   `/api/v1/upload-policy` and `/api/v1/auth/token` were marked protected, overstating the
   authentication boundary. The single `healthz` exemption was replaced with an explicit
   three-entry `public_operations` set. The dangerous direction is declaring a *protected*
   route public; the reverse merely misleads generated clients and anyone auditing from the
   schema alone — but it is still a contract defect and is now fixed.

---

## 3. Workstream B — OCR accuracy

### 3.1 Method

`run_empirical_ocr_benchmark` processed 18 synthetic Algerian commercial invoices generated
with ReportLab and Pillow, with a ground-truth manifest. Six strata were covered: three
digital-PDF strata (French, bilingual FR/AR, stamped/skewed) and three raster strata (clean
raster, stamped/skewed raster, thermal receipt).

### 3.2 Measured results

| Metric | Value |
|---|---|
| Overall field exact-match | **0.9097** |
| Character Error Rate (CER) | 0.1113 |
| Expected Calibration Error (ECE) | 0.0079 |
| Table line association | 1.0 |
| Latency p50 / mean / max | 674 ms / 770 ms / 1954 ms |
| Dataset size | 18 (synthetic, authorised) |

**Per-field exact-match:**

| Field | Exact-match | Precision |
|---|---|---|
| `amount_ht_dzd`, `amount_tva_dzd`, `amount_ttc_dzd` | 1.0 | 1.0 |
| `invoice_date` | 1.0 | 1.0 |
| `supplier_nif` | 1.0 | 1.0 |
| `invoice_number` | 0.9444 | 1.0 |
| `supplier_ai` | **0.6667** | 1.0 |
| `supplier_rc` | **0.6667** | 1.0 |

**Precision is 1.0 on every field.** The failure mode is *abstention*, not error: when the
extractor does not recognise a field it declines to emit a value rather than emitting a wrong
one. This is the correct failure direction for a financial system — an abstained field routes
to human review, whereas a wrong amount posts to the ledger.

**Per-stratum:**

| Stratum | Type | Accuracy | Mean latency |
|---|---|---|---|
| S1 French digital PDF | digital | 1.0 | 7.6 ms |
| S2 Bilingual FR/AR digital PDF | digital | 1.0 | 8.3 ms |
| S3 (digital, third) | digital | 1.0 | ~8 ms |
| S4 clean raster | raster | **0.7083** | 1552.84 ms |
| S5 stamped / skewed | raster | 1.0 | 1467.26 ms |
| S6 thermal receipt | raster | **0.75** | 1331.61 ms |

Digital PDFs are extracted at ~8 ms with perfect accuracy. Raster paths take 1.3–1.6 s and
lose accuracy. `supplier_rc` and `supplier_ai` are the weakest fields throughout, which is
expected: they are alphanumeric registration identifiers with the least regular structure.

### 3.3 Robustness probe — precision held, recall collapsed

A separate probe applied controlled degradations with PIL to 6 raster documents × 8 fields:

| Condition | Precision | Recall |
|---|---|---|
| clean | 1.000 | 0.729 |
| skew 3° | 1.000 | 0.958 |
| skew 8° | 1.000 | 0.667 |
| blur light | 1.000 | 1.000 |
| blur heavy | 1.000 | **0.333** |
| low resolution 50% | 1.000 | 1.000 |
| JPEG quality 40 | 1.000 | 0.833 |

**Precision is 1.000 in every condition.** This is the most important single result in
Workstream B: under every degradation tested, the extractor never produced a wrong value.
Recall degraded — heavily under blur — but a missed field is caught by human review, while a
wrong field is not.

### 3.4 Assessment and honest limits

**No statistical claim is defensible from this data.** The dataset is 18 documents with one
image per specification, and the robustness probe has a single sample per cell. The erratic
recall pattern (blur_light 1.000 but blur_heavy 0.333, while low_res_50 is also 1.000) is
consistent with sampling noise at n=1, not with a real ordering of degradation severity.
Reporting these numbers as accuracy claims would be overstating the evidence.

**What can be said:** the extractor never produced an incorrect field value in any measured
condition; abstention is the failure mode; digital PDFs are handled perfectly and cheaply;
and `supplier_ai` / `supplier_rc` on raster documents are the known weak point.

**What cannot be said:** any confidence interval, any comparison between degradation
conditions, or any claim that the system is accurate enough for a given document population.

### 3.5 Required before OCR output can post without review

1. **A statistically powered benchmark.** At minimum 100 documents per stratum, with
   pre-registered pass/fail thresholds, so a recall number carries a confidence interval.
2. **A degradation matrix with n ≥ 30 per cell.** The current probe is a smoke test, not a
   measurement.
3. **A real-document sample.** Synthetic invoices are rendered from a template and are
   therefore cleaner than the real thing — skewed scans, stamps, handwriting, and
   multi-currency formats are all under-represented.
4. **A French-language and Arabic-script sample.** The bilingual stratum exists but is small.

**Interim control that is already in place:** OCR intake creates no financial posting. The
staging acceptance run confirmed this directly — after a successful OCR extraction,
`supplier_invoices = 0` and `journal_entries = 0`. Every extracted field must be confirmed by
a human before an invoice enters the approval workflow. This control must not be removed
until §3.5 is satisfied.

---

## 4. Workstream C — Dependency and supply-chain verification

### 4.1 Clean-install verification

`tools/verify_dependency_graph.py` exited **0**:

> OK — the environment matches the declared dependency graph. The only deviation from
> upstream metadata is the documented substitution opencv-python → opencv-python-headless.

The environment is a fresh venv (Python 3.11.2) built with
`pip install --no-deps -r requirements.lock.txt`, so the dependency graph is pinned rather
than resolved. 52 installed distributions.

`pip check` reports exactly one line:

```
rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed
```

### 4.2 Assessment of the one deviation

The deviation is **intentional and correct**, not a concealed break. `opencv-python` pulls in
`libGL.so.1` and a large binary wheel; a headless server has no display and no need for the
GUI bindings. `opencv-python-headless` provides the same `cv2` module without them.

Verified live: `cv2` imports at version **4.11.0**, RapidOCR instantiates successfully, and
both `opencv_python.libs` and `libGL.so.1` are confirmed absent from the environment — which
is the point. The `pip check` complaint is an upstream metadata artifact: `rapidocr-onnxruntime`
declares a hard dependency on the exact distribution name rather than on "a working cv2".

**No action required.** This should be recorded in the deployment documentation so the next
operator does not "fix" it by installing `opencv-python` and reintroducing a GUI dependency
into a headless container.

### 4.3 Residual supply-chain risk

The lock file is honoured with `--no-deps`, which means transitive dependencies are *not*
validated by the resolver. `verify_dependency_graph.py` compensates by checking the installed
set against the declared graph, and it passes. This is the correct arrangement for
reproducible builds, but it means **the lock file is only as good as the last time it was
regenerated**. It should be regenerated on a schedule, not only on demand.

---

## 5. Workstream D — Secret handling

### 5.1 Defect: a published default audit key

`ERPDatabase` resolved its audit-chain HMAC secret as:

```python
hmac_secret or os.environ.get("ERP_AUDIT_HMAC_SECRET", "dz-erp-default-test-hmac-key-change-in-prod")
```

Any file-backed deployment that omitted `ERP_AUDIT_HMAC_SECRET` chained `audit_events` with a
key printed in the source tree. Anyone who has read this repository could then forge an
arbitrary audit chain — silently, with no error and no warning.

`_resolve_audit_secret` now applies, most to least trusted:

1. A secret passed explicitly by the caller.
2. `ERP_AUDIT_HMAC_SECRET` from the environment.
3. A key file beside a file-backed database, generated on first use with mode `0600`.
4. The documented test default — **for `:memory:` databases only**.

A file-backed database can no longer reach case 4. Generating the key on first use makes the
secure path the default path rather than a deployment step someone can forget.

### 5.2 Verification — six properties, all correct

| Property | Result |
|---|---|
| File-backed DB secret ≠ published default | PASS |
| Key file created at `<dbname>.audit.key` with mode `0o600` | PASS |
| 44-character urlsafe-base64 key | PASS |
| Secret stable across reopen | PASS |
| `:memory:` still uses the documented default (tests rely on it) | PASS |
| Explicit argument and environment variable both honoured | PASS |
| 8 racing thread initialisations converge on one key (`O_EXCL`) | PASS |

### 5.3 Defect: the vault regenerated its key every process

`PrivateDocumentVault.__init__` called `Fernet.generate_key()` whenever `encryption_key` was
`None`, so the key lived only in memory. The next process started with a different one and
every document already in the vault raised `InvalidToken` — **permanently, with no error at
the time of storage**.

This is the most serious defect found in the audit. It is invisible in testing because every
test builds its vault in the same process that wrote to it, and it destroys data rather than
merely exposing it.

`_resolve_vault_key` now applies: explicit argument → `ERP_VAULT_KEY` → a `<name>.vault.key`
file beside the vault directory (mode `0600`, `O_EXCL`) → and never a silent fresh key. A
malformed key raises `ValueError` rather than silently minting a new one, because a silent new
key is exactly the failure mode.

Verified: key file created beside the vault at `0600`; document readable after a simulated
restart; key stable across restarts; explicit and environment sources honoured; the key file
is **not** inside the encrypted blob directory; an invalid key fails loudly; and cross-company
reads are still denied.

### 5.4 Backup secret carriage

`create_encrypted_backup` stores `audit_hmac_secret_b64` in the manifest alongside
`vault_encryption_key_b64`. Both are inside the archive, which is itself Fernet-encrypted, so
this adds no new exposure — and without it a restored database would resolve a different key
and every chain would fail verification.

---

## 6. Workstream E — Encrypted backup and restore

### 6.1 Defect: restore verified its own work, then left a broken deployment

`restore_and_verify_backup` passed the audit and vault secrets in memory, so it verified the
restore, reported `audit_chains_valid: True` — and wrote **no key to disk**.

Reopening the restored database, which is the entire point of a restore, resolved a
brand-new key. The audit chain then read as `EVENT_HASH_TAMPERED` and every document raised
`InvalidToken`. Reproduced directly:

```
restored db exists      : True
audit key beside it     : False
chain after reopen      : {'valid': False, 'checked_count': 0,
                           'broken_at_id': 1, 'reason': 'EVENT_HASH_TAMPERED'}
report said chains valid: True
```

Both keys are now written to the restored directory with owner-only permissions. After the
fix, reopening with **no secrets supplied** verifies the chain and decrypts the documents.

### 6.2 Defect: restored documents kept dangling absolute paths

`source_documents.vault_path` stores an absolute filesystem path. After a restore into a
different directory — or simply moving the vault — every one of those paths dangled and the
documents became unreadable through no fault of the data.

`read_bytes` now re-anchors by blob name under the current vault root when the stored path is
not beneath it. The blob is named by its content SHA-256 and is unique per company, and
`Path.name` cannot contain a separator, so the result is still confined to the company
directory. **The traversal guard is unchanged**: `/etc/passwd`, `../../../etc/passwd`, and
paths under another company's directory all still raise `TenantIsolationError` — covered by
regression tests.

### 6.3 Verified restore properties

| Property | Result |
|---|---|
| Audit key written to restored dir at `0600` | PASS |
| Vault key written to restored dir at `0600` | PASS |
| Chain verifies on reopen with **no** secrets supplied | PASS |
| Document decrypts on reopen with **no** secrets supplied | PASS |
| Stale absolute vault path re-anchors correctly | PASS |
| Path traversal still blocked after the fix | PASS |
| Cross-company read still denied after the fix | PASS |

---

## 7. Workstream F — Staging acceptance

### 7.1 The entry point did not exist

There was no ASGI entry point in the repository. `create_erp_fastapi_app(...)` was the only
app factory, and `PrivateDocumentVault(vault_root)` generated a throwaway Fernet key when no
key was supplied, so a naive entry point would have produced a server that could not read its
own documents after a restart. **Nothing in the repository was deployable as written.**

`dz_erp/serve.py` now:

- Builds the application from `ERP_DB_PATH` and `ERP_VAULT_PATH`, both required — the
  defaults are deliberately not baked in so a fresh deployment cannot accidentally run
  against a throwaway file.
- Refuses to start against a userless database, with instructions, instead of serving an API
  where every request returns 401 for no stated reason.
- Provides `--bootstrap-admin` to create the first company and admin.
- Resolves the module-level `app` lazily (PEP 562), so `import dz_erp.serve` stays cheap and
  testable while `uvicorn dz_erp.serve:app` still fails loudly when unconfigured.

Verified end-to-end: the bootstrap CLI created an admin, and a subsequent start bound and
served. `serve.py` is at 89% test coverage.

### 7.2 Acceptance run — 25/25 steps passed

Run against a **real uvicorn server bound to loopback only**, with synthetic users, two
synthetic companies, and synthetic documents. `ERP_TRUSTED_PROXIES` was deliberately left
unset so the fail-closed default was exercised.

```
  [PASS] 1.  clean test database initialised
  [PASS] 2.  server bound to loopback only
  [PASS] 3.  synthetic users authenticate
  [PASS] 3b. server-derived principal is correct
  [PASS] 3c. forged identity headers cannot override a valid token
  [PASS] 4.  synthetic supplier invoice uploaded
  [PASS] 5.  intake validated and OCR ran
  [PASS] 6.  document retrievable by its owner
  [PASS] 6b. OCR intake created NO financial posting
  [PASS] 7.  clerk created a staged invoice
  [PASS] 7b. unapproved invoice cannot be posted
  [PASS] 7c. accountant submitted for approval
  [PASS] 7d. approver approved
  [PASS] 7e. approved invoice posted
  [PASS] 8.  posted journal entries balance
  [PASS] 8b. audit trail records the workflow
  [PASS] 8c. audit hash chain verifies
  [PASS] 9.  repeated post is idempotent
  [PASS] 10. cross-company read denied
  [PASS] 10b. cross-company document read denied
  [PASS] 11. oversized upload rejected with 413
  [PASS] 11b. disallowed content rejected with 415
  [PASS] 12. encrypted backup created and verified
  [PASS] 12b. restore verified in isolation
  [PASS] 13. post-restore consistency checks pass

STAGING ACCEPTANCE: 25/25 steps passed, 0 failed
```

Step 8 is the core financial control: 1 posted entry, debit 142 800.00 DZD = credit
142 800.00 DZD. Step 6b is the core OCR control: after a successful extraction,
`supplier_invoices = 0` and `journal_entries = 0`. Step 13 exercises the restore fixes from
§6 directly — the chain verifies after restore.

---

## 8. Blocking items before production

### 8.1 Must be closed before real financial data

1. **Configure a durable, non-tenant-scoped sink for `AUTH_REQUEST_REJECTED`** (§2.4). This is
   a deployment configuration step, not a code change. Without it, authentication failures
   leave no durable trace, and credential-stuffing attempts would be invisible after a
   restart.
2. **Close the OCR accuracy gap** (§3.5) before removing human review from the OCR intake
   path. The current 0.9097 exact-match is measured on 18 synthetic documents and cannot
   support a statistical claim. The existing control — no financial posting from OCR — must
   remain until a powered benchmark exists.
3. **Disable `/docs` and `/redoc` in production.** They are reachable and disclose the full
   API surface. This is a deployment configuration step.
4. **Set `ERP_AUDIT_HMAC_SECRET` explicitly** rather than relying on the generated key file,
   and back the key up. If the key file is lost, the audit chain becomes unverifiable — which
   is correct fail-closed behaviour, but it is a permanent loss.
5. **Back up the vault key file** with the same care as the database. Losing it means every
   stored document is permanently unreadable.

### 8.2 Recommended

6. Regenerate `requirements.lock.txt` on a schedule (§4.3).
7. Run the adversarial harness and the staging acceptance script as CI gates, so the seven
   defects fixed here cannot silently return.
8. Record the `opencv-python` → `opencv-python-headless` substitution in the deployment
   documentation (§4.2) so it is not "fixed" back.

---

## 9. Verification summary

| Check | Result |
|---|---|
| Full test suite | **267 passed, 1 warning, 93% coverage** (2373 stmts, 159 missed) |
| `ruff check dz_erp/ tests/` | **All checks passed** |
| Adversarial harness | **89 checks passed, 0 HIGH, 1 MEDIUM (documented)** |
| Staging acceptance | **25/25 steps passed, 0 failed** |
| Dependency graph | **exit 0, one documented deviation** |
| Python | 3.11.2 |

Per-module coverage: `net.py` 97%, `upload.py` 96%, `odoo_test_adapter.py` 96%,
`intake_ocr.py` 93%, `localization.py` 93%, `security_audit.py` 90%,
`procurement_stock.py` 90%, `serve.py` 89%.

All work is committed on `arena/5c240e1d-erp` and pushed. No `main`, PR #1 or PR #2
modification occurred, and no destructive git operation was performed.

---

## Appendix — claims that are *not* made

Recorded explicitly so this report is not over-read:

- **No OCR accuracy claim with a confidence interval.** n = 18 synthetic documents, one image
  per specification. The per-stratum and per-degradation numbers describe what was measured,
  not what is true of the document population.
- **No ordering claim between degradation conditions.** blur_light at 1.000 recall and
  blur_heavy at 0.333, with low_res_50 also at 1.000, is not a plausible severity ordering —
  it is sampling noise at n = 1 per cell.
- **No production-readiness claim.** §8.1 lists what must be closed first.
- **No claim about behaviour on real scanned Algerian invoices.** All documents were
  synthetic.
