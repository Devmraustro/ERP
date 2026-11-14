# Release & Security Evidence Index

**Repository:** `/home/user/ERP` (`https://github.com/Devmraustro/ERP.git`)  
**Active Branch:** `arena/63d762c8-erp`  
**Audit Date:** `2026-11-13` (UTC)

---

## 1. Preservation Policy

Per repository release governance:
- **Zero historical release or security evidence files have been deleted.** Every phase directory (`phase8` through `phase56` + `final` and `docs/security/*`) is preserved in Git for audit traceability, hash-manifest (`SHA256SUMS`) verification, and cross-branch provenance.
- Reports whose measurements or conclusions were overtaken by subsequent engineering integrations (e.g., Phase 38 accounting/OCR integration, Phase 46 dependency hash-lock & G6 audit, Phase 52 reproducible `--require-hashes` verification, Phase 53 `ERP_ALLOWED_HOSTS` / SEC-13 password-policy & rehashing closure) are explicitly marked **`Superseded`** below with a pointer to the current authoritative artifact rather than removed.

---

## 2. Authoritative Current Release Package (Phases 52–58)

| Path | Status | Scope & Role |
|---|---|---|
| `docs/release/RELEASE_EVIDENCE_INDEX.md` | **Current** | Master index of all current, superseded, and historically preserved release/security artifacts |
| `docs/release/phase52/DEPENDENCY_REPRODUCIBILITY_REPORT.md` | **Current** | Authoritative baseline (`Section 0`) + reproducible `--require-hashes` environment verification (`53` pinned packages, `1,317` SHA-256 digests, `pip-audit` `0` CVEs, `opencv-python-headless` verification) |
| `docs/release/phase53/REPOSITORY_CLEANUP_REPORT.md` | **Current** | Full repository file audit (`547` tracked files), transient cache cleanup, packaging/linting hygiene, 22-function Phase 34/36 preservation proof, and SEC-15 (`ERP_ALLOWED_HOSTS`) + SEC-13 (`validate_password_policy` & PBKDF2 rehashing) closure |
| `docs/release/phase54/FINAL_REGRESSION_REPORT.md` | **Current** | Comprehensive functional, accounting, procurement, inventory, OCR, security, and backup/restore regression across CPython 3.12.10 and GitHub Actions CI |
| `docs/release/phase55/EXPERT_APPROVAL_PACKAGE.md` | **Current** | Consolidated Algerian Chartered Accountant (`Expert-Comptable`) & Tax Advisor review package anchored to the canonical in-repo compliance questions (Gate **G5: `BLOCKED`** pending external human sign-off) |
| `docs/release/phase56/PRODUCTION_DEPLOYMENT_RUNBOOK.md` | **Current** | 16-area production infrastructure readiness assessment and deployment/rollback runbook (Gate **G6: `BLOCKED`** pending target production host, domain, TLS, and offsite custody) |
| `docs/release/final/ERP_PRODUCTION_RELEASE_HANDOVER.md` | **Current** | Final executive & engineering release handover, gate matrix (`G1`–`G6`), PR reconciliation status, and authoritative release classification |

---

## 3. Historical `docs/release/` Artifacts (Preserved)

| Path | File Count | Classification | Superseding / Companion Artifact & Notes |
|---|---:|---|---|
| `docs/release/README.md` | 1 | **Historically Preserved** | Original release-directory guide |
| `docs/release/PHASE8_FINAL_V1_ACCEPTANCE_REPORT.md` | 1 | **Superseded** | Superseded by `docs/release/final/ERP_PRODUCTION_RELEASE_HANDOVER.md` |
| `docs/release/FINAL_PRODUCTION_READINESS_REPORT.md` | 1 | **Superseded** | Phase 10 summary; superseded by `docs/release/final/ERP_PRODUCTION_RELEASE_HANDOVER.md` |
| `docs/release/phase8/` | 10 | **Historically Preserved** (reports `Superseded`; `evidence/tls_proxy_harness.py` **Current** executable harness) | Contains `tls_proxy_harness.py` (used live in Phases 53–54 to verify `ERP_ALLOWED_HOSTS` HTTP `400` and proxy TLS boundary) plus historical Phase 8 status reports (`EXPERT_APPROVAL_STATUS.md`, `REVERSE_PROXY_TLS_ACCEPTANCE.md`, `WINDOWS_DESKTOP_ACCEPTANCE.md`) |
| `docs/release/phase9/` | 29 | **Historically Preserved** (`Superseded` readiness verdicts) | Early accounting, compliance, disaster-recovery, and process-boundary acceptance logs |
| `docs/release/phase10/` | 42 | **Historically Preserved** (`BACKUP_AND_RECOVERY_RUNBOOK.md` & `DATA_RETENTION_POLICY.md` remain active operational references; `EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` consolidated into Phase 55; dependency reports superseded by Phase 52) | Key operational runbooks and key-rotation / readiness evidence |
| `docs/release/phase29/` | 21 | **Historically Preserved** | Desktop PySide6 UI light/dark FR/AR visual screenshots (`01_dashboard`–`10_dark` PNGs) and desktop redesign acceptance evidence |
| `docs/release/phase30/` | 43 | **Historically Preserved** | Arabic RTL rendering, High-DPI, accessibility, safe-restore drill (`06_SAFE_RESTORE_DRILL.md`), and cross-platform UI evidence |
| `docs/release/phase34/` | 8 | **Superseded** | Pre-integration branch comparison (`arena/7557bcfb-erp` vs PR #7 `2427fda`); superseded by Phase 36B (`PROCUREMENT_FUNCTION_INVENTORY.md`), Phase 38, Phase 46, and Phase 53 |
| `docs/release/phase35/` | 17 | **Historically Preserved** | Integration step logs and `toolchain/build_env.sh` CPython `3.12.10` reproducible build script |
| `docs/release/phase36/` | 3 | **Historically Preserved** | `INTEGRATION_MATRIX.md`, `OVERLAP_REVIEW.md`, and `PROCUREMENT_FUNCTION_INVENTORY.md` (authoritative AST inventory of the 22 PR #7 procurement/accounting functions) |
| `docs/release/phase37/` | 1 | **Superseded** | `SECURITY_INTEGRATION_REPORT.md` (Phase 35/37 security port; superseded by Phase 53 which completed SEC-15 `ERP_ALLOWED_HOSTS` and SEC-13 unified password policy + rehashing) |
| `docs/release/phase38/` | 1 | **Historically Preserved** | `ACCOUNTING_PROCUREMENT_INTEGRATION.md` documenting Group B & Group C (`review_policy.py`, `_document_review_gate`, `_validate_invoice_lines`, `_validate_invoice_totals`, `_duplicate_invoice_errors`, `_source_invoice_mismatches`) integration |
| `docs/release/phase39/` | 1 | **Historically Preserved** | `DESKTOP_API_ACCEPTANCE.md` |
| `docs/release/phase40/` | 1 | **Superseded** | `DEPENDENCY_DATABASE_REPORT.md`; superseded by `docs/release/phase52/DEPENDENCY_REPRODUCIBILITY_REPORT.md` |
| `docs/release/phase41/` | 1 | **Superseded** | `FULL_REGRESSION_REPORT.md`; superseded by `docs/release/phase54/FINAL_REGRESSION_REPORT.md` |
| `docs/release/phase42/` | 1 | **Superseded** | `RELEASE_ACCEPTANCE_REPORT.md`; superseded by `docs/release/final/ERP_PRODUCTION_RELEASE_HANDOVER.md` |
| `docs/release/phase44/` | 1 | **Superseded** | `DEPLOYMENT_AND_ROLLBACK_REPORT.md`; superseded by `docs/release/phase56/PRODUCTION_DEPLOYMENT_RUNBOOK.md` |
| `docs/release/phase45/` | 1 | **Superseded** | `FINAL_HANDOVER_REPORT.md`; superseded by `docs/release/final/ERP_PRODUCTION_RELEASE_HANDOVER.md` |
| `docs/release/phase46/` | 13 | **Superseded** (historical baseline for Phases 52–58) | `BASELINE.md`, `H1_DEPENDENCY_HASH_LOCK.md`, `ALGERIAN_EXPERT_APPROVAL_REGISTER.md`, `G6_DEPLOYMENT_READINESS.md`, `FINAL_REGRESSION.md`, and `FINAL_DECISION_AND_HANDOVER.md` (`d73ae5a`); superseded by Phases 52–58 |

---

## 4. Historical `docs/security/` Artifacts (Integrity-Pinned & Preserved)

> **Integrity Note:** `docs/security/phase3_blocker_closure/SHA256SUMS`, `docs/security/phase4_closure/SHA256SUMS`, `docs/security/phase5_ci_312/SHA256SUMS`, and `docs/security/phase5_release_gate/SHA256SUMS` pin exact SHA-256 digests of their evidence files and are verified by automated tests (`tests/test_phase64_dependency_security_regression.py`). None of these files may be reformatted or modified.

| Path | File Count | Classification | Notes |
|---|---:|---|---|
| `docs/security/SECURITY_ASSESSMENT_REPORT.md` | 1 | **Historically Preserved** | Original comprehensive security assessment (`SEC-01` through `SEC-18`) |
| `docs/security/SECURITY_FINDINGS_REGISTER.md` | 1 | **Historically Preserved** | Master security findings register; enforced for consistency by `tests/test_phase22_register_and_docs_consistency.py` |
| `docs/security/SECURITY_RECONCILIATION_REPORT.md` | 1 | **Historically Preserved** | Cross-branch security reconciliation record |
| `docs/security/SECURITY_REMEDIATION_LOG.md` | 1 | **Historically Preserved** | Chronological security remediation log |
| `docs/security/SECURITY_RESIDUAL_RISKS.md` | 1 | **Historically Preserved** | Residual risk register (deployment-perimeter items carried into Phase 56 G6 runbook) |
| `docs/security/SECURITY_SELECTIVE_INTEGRATION_REPORT.md` | 1 | **Historically Preserved** | Phase 3 selective security integration report |
| `docs/security/SECURITY_TEST_EVIDENCE.md` | 1 | **Historically Preserved** | Security test mapping |
| `docs/security/SECURITY_VALIDATION_SUMMARY.md` | 1 | **Historically Preserved** | Security validation summary |
| `docs/security/PACKAGING_DECISION_V1.md` | 1 | **Historically Preserved** | Flat-layout packaging record (completed on active branch in Phase 53 `pyproject.toml`) |
| `docs/security/PHASE3_BLOCKER_CLOSURE_REPORT.md` & `phase3_blocker_closure/` | 8 | **Historically Preserved** (`SHA256SUMS`-pinned) | Blocker closure patches & wheel verification artifacts |
| `docs/security/PHASE4_SEC131415_CLOSURE_REPORT.md` & `phase4_closure/` | 10 | **Historically Preserved** (`SHA256SUMS`-pinned) | Reference patch archive for SEC-13, SEC-14, SEC-15 (now active on `arena/63d762c8-erp` via Phase 35 + Phase 53) |
| `docs/security/PHASE5_B1_LOCAL_VAULT_RELEASE_GATE_REPORT.md` & `phase5_release_gate/` | 22 | **Historically Preserved** (`SHA256SUMS`-pinned) | Local vault release gate logs & JUnit XML |
| `docs/security/PHASE5_CI_312_EXECUTION_REPORT.md` & `phase5_ci_312/` | 27 | **Historically Preserved** (`SHA256SUMS`-pinned) | Python 3.12 CI & smoke execution evidence |
| `docs/security/selective_integration/` | 2 | **Historically Preserved** | Selective integration patch & reference test |

---

## 5. Authoritative Compliance & Governance Documents Outside `docs/release/`

| Path | Classification | Notes |
|---|---|---|
| `docs/COMPLIANCE_REVIEW_PACKAGE.md` | **Current** (Canonical Source) | Original in-repo compliance review dossier (`§8` Questions `1`–`9` for External Algerian Expert) |
| `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` | **Current** (Canonical Source) | Statutory & accounting compliance gate checklist (`§1`–`§8`) |
| `docs/ENVIRONMENT_VARIABLES.md` | **Current** | Authoritative runtime environment variable reference (updated in Phase 53 with `ERP_ALLOWED_HOSTS` and `ERP_PASSWORD_MIN_LENGTH`) |
| `docs/compliance/EXPERT_REVIEW_QUESTIONS.md` | **Absent in Repository** | Verified absent across all commits and all remote refs (`origin/main`, `origin/arena/*`) in `Devmraustro/ERP`; originated in unpushed local workspace `C:\Users\rabzd\Documents\ERP`. Not fabricated; Phase 55 uses the canonical tracked compliance sources above. |
