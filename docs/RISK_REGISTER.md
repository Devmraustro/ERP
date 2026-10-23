# Risk register

**Purpose.** One place where every known risk has a severity, an evidence pointer, an owner
and an action. A risk without an owner is a rumour; a risk without evidence cannot be
reviewed; a risk without an action is a decision nobody made.

**Status vocabulary.** `Open` (no mitigation beyond what is stated), `Mitigated` (a control is
implemented and tested; residual risk remains), `Accepted` (known, owned, and consciously
carried — with the acceptance recorded), `Closed`.

**Severity.** Likelihood × impact, judged for **production use with real Algerian financial
data**. For a staging deployment with synthetic data, most High items drop to Low; that is
why the go/no-go record distinguishes the two.

| Severity | Meaning |
|---|---|
| **Critical** | Data loss, undetectable falsification of the books, or a legal breach that stops the deployment |
| **High** | Loss of confidentiality of the full document corpus, materially wrong declarations, or unrecoverable outage |
| **Medium** | Materially degraded assurance, operational friction, or a control that can be bypassed by an insider with effort |
| **Low** | Cosmetic, documented deviation, or an inconvenience with a workaround |

---

## 1. Register

### Security and key custody

| ID | Risk | Sev | Likelihood | Evidence | Control in place | Owner | Action | Status |
|---|---|---|---|---|---|---|---|---|
| S1 | Key material is a readable file: an attacker with the service account's file access obtains every vault, audit and ledger key | **High** | Medium | `docs/KEY_CUSTODY_THREAT_MODEL.md` §R1-R2; `dz_erp/key_provider.py` `describe()` returns `hsm_equivalent: False` | `0600` files, `0700` directory, mode checked on the open descriptor, symlink refused, custody drift reported by `monitor` | Security officer | Fund KMS/HSM (wrap/unwrap boundary) or document acceptance with compensating controls; keep `monitor` alerting | **Open** (accepted for staging only) |
| S2 | The running process holds the active key in memory; a compromise (RCE, dependency, SSRF) yields all data and the ability to forge history | **High** | Medium | Threat model §R2 | Network isolation, least privilege, patching (deployment) | Security officer + Ops | Restrict egress, run without shell, EDR; rotate after any suspected compromise | Accepted (inherent) |
| S3 | No split custody / two-person rule: a single operator can rotate, retire and remove keys | **High** | Low–Medium | `dz_erp/rotate_keys.py` `*-remove` requires confirmation only; ledger records actor | Ledger records actor/host/pid; refusal path recorded | Operations owner | Two-person procedure via change management; consider approval token in the CLI | **Open** (accepted for staging only) |
| S4 | Rotation history can be rewritten by someone holding the ledger key; tail truncation is invisible to the chain | Medium | Low | `dz_erp/rotation_audit.py` module docstring and truncation test; threat model §R7 | HMAC chain, off-host checkpoints (`--checkpoint-export`), `CHECKPOINT_MISMATCH` reason | Operations owner | Schedule checkpoint exports off-host; alert on `LEDGER_UNREADABLE`/`CHECKPOINT_MISMATCH` | Mitigated |
| S5 | Windows: mode bits are not access control, so the custody check is skipped | Medium | High (on Windows) | `describe_key_protection()` returns `enforced: False`; `docs/STAGING_SECURITY_CONFIGURATION.md` | Report states the limitation instead of claiming a control; CI runs the rotation suite on Windows | Ops + Security officer | Set explicit ACL on the key directory (`icacls /inheritance:r`), verify with `monitor` | Mitigated (procedural) |
| S6 | Cross-tenant read or write through the API | **High** if present | Low | `tests/test_phase7_authentication_remediation.py`, `tests/test_phase5_security_audit_backup.py` | Tenant resolved from the credential, server-side scoping, cross-company vault path check | Engineering | Keep the isolation tests mandatory in CI | Mitigated |
| S7 | Audit chain falsification that verifies as valid | **Critical** if present | Low | `verify_all_audit_chains`, tamper tests (modified/deleted/reordered/inserted) | HMAC-SHA256 chained per company with per-version keys | Engineering | None further in scope; a KMS-held key would raise the bar (S1) | Mitigated |

### Accounting, tax and compliance

| ID | Risk | Sev | Likelihood | Evidence | Control in place | Owner | Action | Status |
|---|---|---|---|---|---|---|---|---|
| C1 | SCF mapping, journal codes or rounding differ from statutory expectation | **High** | Medium | `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` §1 (all rows open) | Vendored chart, tested mappings, `Decimal` arithmetic | Expert-Comptable (ONEC) | Signed mapping table and reversal policy | **Open** |
| C2 | VAT rates, *droit de timbre* brackets or G50 box mapping wrong for the period | **High** | Medium | Checklist §2 | Versioned rates (`LF-2025-CASH-TIMBRE`), bracketed calculation, G50 tagging | Fiscaliste | Rate table approval with effective dates; box-by-box reconciliation of one real period | **Open** |
| C3 | G50 workbook mistaken for a filing | **High** | Low | `generate_g50_preparation_workbook` emits `PREPARATION_WORKBOOK_ONLY__REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF` | Explicit gate in code and in output | Expert-Comptable | Professional review before any DGI submission | Mitigated |
| C4 | Clearing-account ambiguity (380 vs 408) misclassifies inventory/GRNI | Medium | Medium | Checklist §1.5 | Both accounts exist; default documented | Expert-Comptable | Written treatment decision per transaction type | **Open** |
| C5 | Retention period unapproved; encrypted originals may not satisfy an auditor | Medium | Medium | Checklist §3.3 | Encrypted vault, SHA-256 of originals, immutable raw OCR | Expert-Comptable + DPO | Approve retention policy | **Open** |
| C6 | Law 18-07: no processing register, no data-subject workflow, no ANPDP filing, no transfer assessment | **High** | High | Checklist §4 (4.1–4.6 all open) | Redacting logger, RBAC, audit trail | DPO / legal counsel | Register, rights procedure, ANPDP formalities, hosting decision | **Open** |
| C7 | Duplicate supplier invoice paid twice | **High** if present | Low | Normalised + fuzzy matching; partial unique index `uq_posted_supplier_invoice`; tests | Duplicate detection before posting | Engineering | None further; monitor rejected-duplicate volume | Mitigated |

### Data integrity and operations

| ID | Risk | Sev | Likelihood | Evidence | Control in place | Owner | Action | Status |
|---|---|---|---|---|---|---|---|---|
| D1 | A rotation declares success while documents still need the retired key | **Critical** if present | Was reproducible | Found by the drill: 3 stray blobs at `COMPLETED_VERIFIED`; `tests/test_phase16_rotation_concurrency_convergence.py`; `docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md` §6 | Write re-checked against active version; rotation converges on observed versions; `COMPLETED_VERIFIED` requires convergence; `stray_versions` reported | Engineering | Closed with regression test that fails on the old code path | Closed |
| D2 | Rotation interrupted by a crash leaves documents unreadable, or leaves no trace | **High** if present | Medium | Killed-process drill at 10/50/90 %; ledger orphan `STARTED` → `RECOVERED` | Registry written before data; resumable; ledger records interruption; readable throughout | Engineering + Ops | Keep the drill in the release checklist | Mitigated |
| D3 | Backup restore succeeds partially (files restored, books unbalanced or chain broken) | **High** | Low | `restore_and_verify_backup` verifies hashes, chains and trial balance; drill §3.2 (10 020.00 balanced) | Manifest SHA-256, per-blob hashes, chain verification, trial balance | Operations owner | Rehearse quarterly on production-scale data (see D4) | Mitigated |
| D4 | No drill on production-scale data, real backup media or real scanner output | Medium | High | `docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md` §5 | Synthetic drill only, thresholds 10×-derived | Operations owner | One drill on a copy of production before go-live | **Open** |
| D5 | Backups still need retired key versions; premature removal is silent data loss | **High** | Low | `vault-remove`/`audit-remove` refusal tests; threat model §R4 | Refusal without `confirm_retained_copies`; ledger records the refusal | Operations owner | Document retention of key versions ≥ backup retention | Mitigated |
| D6 | Rotation is operator-invoked with no scheduler | Medium | High | `dz_erp/rotate_keys.py`; `monitor --max-key-age-days` | Health report exits 3 when a key is overdue | Operations owner | Schedule `monitor`, alert on exit code, record rotation dates | **Open** |
| D7 | Postgres/SQLite concurrency assumptions break under real load | Medium | Medium | Concurrency tests, retained, 5-thread contention test | Optimistic concurrency, transactions | Engineering | Maintenance window for rotation; load test on real storage (D4) | Accepted |
| D8 | Process crash mid-write leaves a truncated blob | **High** if present | Low | `atomic_write_bytes` + rename; tests | Atomic replace, no in-place rewrite | Engineering | None further | Mitigated |

### OCR and data quality

| ID | Risk | Sev | Likelihood | Evidence | Control in place | Owner | Action | Status |
|---|---|---|---|---|---|---|---|---|
| O1 | OCR accuracy insufficient for unattended posting | **High** | n/a today | `docs/OCR_EVALUATION_PROTOCOL.md` §1, §4.2; 90.97 % at n=144 → 85.2–94.7 % | Human review mandatory before `Validated`/`Approved` | Product owner | Keep review mandatory; run the powered evaluation before considering automation | Mitigated (by workflow) |
| O2 | Field confidence is the line score (and a constant `0.995` for digital PDFs), so no threshold can be calibrated honestly | Medium | Certain | `extract_document_ocr` line ~1017; protocol §3 | No auto-accept rule is configured | Engineering | Emit per-field evidence or `null`; then calibrate per protocol §6 | **Open** |
| O3 | Arabic-only scans cannot be recognised by the default recognition head | **High** | Certain | `docs/OCR_BENCHMARK_REPORT.md` §5.2; protocol §7 | Arabic digital PDFs work (native text); scanned Arabic routes to manual entry | Product owner | Provision an Arabic `PP-OCRv4` head or route to human entry explicitly | **Open** |
| O4 | Engine migration (`rapidocr` 3.x) is treated as a bump | Medium | Medium | Protocol §2 (API differs, `opencv-python-headless`) | Pinned engine; protocol gates any change | Engineering | Re-run the full evaluation against the new API before adopting | Accepted |
| O5 | Python 3.13 cannot run the pinned OCR engine (`<3.13`) | Low | Certain (when 3.13 is required) | PyPI metadata recorded in `FINAL_AUDIT_REPORT.md` | CI covers 3.11/3.12 | Engineering | Plan the migration with O4, or pin the runtime | Accepted |

### Supply chain and build

| ID | Risk | Sev | Likelihood | Evidence | Control in place | Owner | Action | Status |
|---|---|---|---|---|---|---|---|---|
| B1 | `pip check` exits 1 on the documented OpenCV substitution | Low | Certain | `requirements.lock.txt` §comments; `tools/verify_dependency_graph.py` | Substitution documented, allow-listed, verified by the graph tool | Engineering | Keep the allow-list current; revisit with O4 | Accepted |
| B2 | A dependency is compromised or becomes unavailable | Medium | Low | `docs/DEPENDENCY_AND_LICENSE_REGISTER.md` | Locked versions, licence register, no post-install scripts | Engineering | Vendor or mirror wheels for the deployment; verify hashes in CI | **Open** |
| B3 | CI passes on Linux but the deployment is Windows (or vice versa) | Medium | Medium | `.github/workflows/ci.yml` `verify-windows` job | Rotation/backup suite runs on Windows in CI | Engineering | Do not weaken the Windows job to make it fast | Mitigated |
| B4 | Secrets reach logs, reports or a crash dump | **High** if present | Low | Redaction tests; `monitor`/`status` contain no key material; drill exits no material | `StructuredRedactingLogger`, assertions on CLI output | Engineering | Keep the "no material in output" assertions next to any new report | Mitigated |

---

## 2. Summary

Counted from the rows above; the register is the source of truth, not this table.

| Severity | **Open** | Mitigated | Accepted | Closed | Total |
|---|---:|---:|---:|---:|---:|
| Critical | 0 | 1 (S7) | 0 | 1 (D1) | 2 |
| High | 6 (S1, S3, C1, C2, C6, O3) | 9 | 1 (S2) | 0 | 16 |
| Medium | 6 (C4, C5, D4, D6, O2, B2) | 3 (S4, S5, B3) | 2 (D7, O4) | 0 | 11 |
| Low | 0 | 0 | 2 (O5, B1) | 0 | 2 |
| **Total** | **12** | **13** | **5** | **1** | **31** |

The shape is the point:

* **Every Critical item is closed or mitigated.** D1 (a rotation reporting success while
  documents still needed the retired key) was found by the drill and closed with a test that
  fails on the old code path.
* **No High item is waiting on code.** Of the six High *Open* items, three are external
  professional sign-offs (C1, C2, C6), one is a language model that must be provisioned (O3),
  and two are key-custody decisions needing infrastructure and budget (S1, S3).
* **S2 is accepted as inherent.** A running process must hold the active key; no amount of
  application code changes that, which is why it is stated rather than papered over.

---

## 3. How to keep this honest

* Every row names evidence that can be re-run. A row whose evidence cannot be reproduced must
  be downgraded to an assertion.
* When a control is added, the row moves to `Mitigated` **and** a test is added that fails
  without it — D1 is the model: the drill found it, the test locks it.
* When a risk is accepted, the acceptance is recorded here with the owner's name; a verbal
  acceptance is not an acceptance.
* This register is reviewed whenever `monitor` reports `healthy: false`, after any incident,
  and before every go/no-go decision.
