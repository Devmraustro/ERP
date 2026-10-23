# Compliance review package

**What this is.** A single index for an external reviewer — accountant, auditor, DPO, or
security assessor — of what the system does, where the evidence is, and what remains open.
It does not restate the analysis; it points to it, so that a reviewer can verify a claim
rather than take it on trust.

**What this is not.** It is not a certification, and it is not a legal opinion. No part of
this repository establishes statutory compliance: the accounting treatment, the tax mapping
and the Law 18-07 position require licensed Algerian professionals to sign off
(`docs/PRODUCTION_READINESS_AND_GO_NO_GO.md`) and the software cannot substitute for that.

**Document control.** Package assembled against branch `arena/6c474c64-erp`; every evidence
item below names a file, a test, or a command whose result can be reproduced.

---

## 1. Review agenda (suggested, in this order)

| # | Question a reviewer asks | Where the answer is | Independently verifiable? |
|---|---|---|---|
| 1 | What is the system, in one page? | `docs/BASELINE_AUDIT_AND_ARCHITECTURE.md` | Read |
| 2 | Does the accounting framework match SCF? | `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` §1 | Yes — chart files + tests |
| 3 | Is tax computed as the law requires? | §2 (VAT, timbre, G50) | Yes — worked examples in tests |
| 4 | Are invoices and originals handled lawfully? | §3 | Yes — vault + immutability tests |
| 5 | Is personal data processed lawfully? | §4 | **No** — this is a legal determination, not a code property |
| 6 | Can the books be trusted after a change? | §1.4, `KEY_ROTATION_DESIGN.md`, `docs/KEY_CUSTODY_THREAT_MODEL.md` | Yes — hash chain, ledger, rotation tests |
| 7 | Can the data be recovered? | `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`, `docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md` §3.2 | Yes — restore drill |
| 8 | Is the extraction accurate enough to rely on? | `docs/OCR_EVALUATION_PROTOCOL.md`, `docs/OCR_BENCHMARK_REPORT.md` | Yes, within the stated limits |
| 9 | What is the residual risk? | `docs/RISK_REGISTER.md` | Read — every row cites evidence |

---

## 2. Control → obligation map

Row numbers refer to `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`, which carries the detailed
analysis and the professional sign-off owner. This table is the summary a reviewer can walk.

| Domain | Control in the software | Obligation it addresses | Status |
|---|---|---|---|
| Chart of accounts | Vendored SCF/`l10n_dz` chart, tested mappings | SCF statutory books | Implemented; **mapping needs expert sign-off** |
| Posting integrity | `CHECK (total_debit_dzd = total_credit_dzd)`, immutability triggers, `FINANCIAL_INTEGRITY_VIOLATION` | Books must balance and not be silently edited | Implemented and tested |
| Entry numbering | Per-company journal sequences, unique constraints | Statutory book sequencing | Implemented; **format confirmation needed** |
| DZD arithmetic | `Decimal` only, explicit rounding, no float in posting paths | Correct monetary representation | Implemented and tested |
| VAT / timbre | Versioned rates, bracketed *droit de timbre*, G50 tagging | Tax computation per current Finance Law | Implemented; **rates need fiscalist confirmation per period** |
| G50 output | Workbook generation, explicitly gated as *preparation only* | DGI *Série G n°50* | **Deliberately not a filing**; requires ONEC sign-off |
| Invoice intake | MIME/magic checks, quarantine, encrypted vault, SHA-256 of originals, immutable raw OCR | Retention of originals, evidentiary quality | Implemented and tested |
| Duplicate detection | Normalised + fuzzy matching, partial unique index on posted invoices | Preventing duplicate payment | Implemented and tested |
| Segregation of duties | Creator ≠ approver, approval invalidation on edit | Internal control | Implemented and tested |
| Audit trail | Append-only `audit_events`, HMAC-SHA256 hash chain, trigger-protected raw OCR, per-key-version verification | Traceability of every financial action | Implemented and tested |
| Key rotation | Versioned registries, resumable rotation, tamper-evident ledger, custody enforcement | Long-term readability and integrity | Implemented and tested (see §4) |
| Backup / restore | Encrypted archive, manifest hashes, isolated restore, chain + trial-balance verification | Recoverability | Implemented, drilled |
| Access control | Bearer tokens (digest only), RBAC, lockout, tenant scoping from the credential | Least privilege, confidentiality | Implemented and tested |
| Log hygiene | Redacting logger (secrets, RIB, identifiers), no key material in reports | Minimisation, no secret leakage | Implemented and tested |
| Personal data workflows | **Not implemented**: no processing register, no data-subject request workflow | Law 18-07 arts. on register and rights | **Open — legal/product decision** |
| ANPDP formalities | **Not implemented**: no filing artefact or reference stored | Law 18-07 registration/authorisation | **Open — external** |
| Cross-border transfer | **Undetermined**: nothing constrains hosting location | Law 18-07 transfer rules | **Open — hosting decision** |

---

## 3. Evidence index

Everything below is reproducible in this repository with the pinned environment.

| Evidence | Command / file | Latest result |
|---|---|---|
| Full test suite | `python -m pytest -q` | **418 passed** |
| Coverage | `python -m pytest --cov=dz_erp --cov-report=term` | **88 %** (3 948 statements, 457 missed) |
| Static checks | `python -m ruff check dz_erp tests tools` | clean |
| Byte-compilation | `python -m compileall -q dz_erp tests tools` | clean |
| Staging acceptance | `python tools/staging_acceptance.py` | **33/33 checks** |
| Dependency graph | `python tools/verify_dependency_graph.py` | 52 distributions match; one documented substitution |
| Rotation load/recovery drill | `python tools/rotation_scale_drill.py --documents 1000 --events 10000` | all thresholds pass (`docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md`) |
| Key custody telemetry | `python -m dz_erp.rotate_keys monitor` | JSON with `healthy`, custody verdicts, unfinished operations |
| Rotation history | `python -m dz_erp.rotate_keys status` | versions, blob distribution, ledger validity |
| CI | GitHub Actions `.github/workflows/ci.yml` | Linux 3.11 + 3.12, **plus Windows** key-rotation job |
| OCR benchmark | `dz_erp/benchmark.py` → `benchmarks/results/ground_truth_and_results.json` | see `docs/OCR_BENCHMARK_REPORT.md` and its stated limits |

Test files that carry the weight for an audit conversation:

| Concern | Test file |
|---|---|
| Accounting, 3-way match, reversals, concurrency | `tests/test_phase4_procurement_3way_match.py` |
| Audit chain, redaction, backup/restore | `tests/test_phase5_security_audit_backup.py` |
| Authentication, RBAC, tenant isolation | `tests/test_phase7_authentication_remediation.py` |
| Key rotation (vault and audit) | `tests/test_phase10_vault_key_rotation.py`, `test_phase11_audit_key_rotation.py` |
| Migration and backup compatibility | `tests/test_phase12_key_migration_and_backup.py` |
| Rotation audit ledger | `tests/test_phase14_rotation_audit_ledger.py` |
| Custody and monitoring | `tests/test_phase15_key_custody_and_monitoring.py` |
| Rotation convergence under load | `tests/test_phase16_rotation_concurrency_convergence.py` |
| Key providers (symlink/mode enforcement) | `tests/test_phase17_key_providers.py` |

---

## 4. What a reviewer should be told without being asked

Three disclosures, because discovering them later destroys more trust than stating them now.

1. **Key custody is file-based.** No HSM, no KMS, no split custody, no two-person rule. Keys
   are `0600` in a `0700` directory, the mode is enforced at read time, symlinks are refused,
   and the service reports `hsm_equivalent: False` about itself. Anyone with the service
   account's file access can read every key. Full analysis:
   `docs/KEY_CUSTODY_THREAT_MODEL.md`.
2. **The audit trail is tamper-evident, not tamper-proof.** It detects modification, deletion,
   reordering and insertion, and rotation history is separately chained with off-host
   checkpoints. An attacker holding the key and the files can rewrite history; tail truncation
   is only caught by a checkpoint. Both limits are asserted in tests rather than described as
   solved.
3. **The G50 output is a preparation workbook.** It is labelled as such in code, it is not a
   filing, and it must be reviewed by a licensed *Expert-Comptable* before any DGI submission.

---

## 5. Open items requiring external sign-off

| # | Item | Who must sign | Consequence of not signing |
|---|---|---|---|
| 1 | SCF account mapping, journal codes, rounding | Expert-Comptable (ONEC) | Books may be organised in a way a statutory audit rejects |
| 2 | VAT rates/treatment per period; *droit de timbre* brackets | Fiscaliste | Misstated tax |
| 3 | G50 box mapping and period cut-off | Fiscaliste + Expert-Comptable | Incorrect declaration |
| 4 | Clearing-account treatment (380 vs 408) | Expert-Comptable | Misclassified inventory/GRNI |
| 5 | Retention period and acceptability of encrypted originals | Expert-Comptable + DPO | Non-compliant retention |
| 6 | Lawful basis, processing register, data-subject procedures | DPO / legal counsel | Unlawful processing under 18-07 |
| 7 | ANPDP filing/authorisation | DPO / legal counsel | Unregistered processing |
| 8 | Hosting location and any transfer assessment | DPO / legal counsel + IT | Unauthorised transfer |
| 9 | Key-custody compensating controls (§4.1) | Security officer | Accepted High risk without controls |

These are tracked in `docs/RISK_REGISTER.md` with severity, evidence, owner and action; the
register is the single place where an open item is closed.

---

## 6. Review procedure (how to actually check, not just read)

```bash
# 1. Environment is reproducible
python -m pip install --no-deps -r requirements.lock.txt
python tools/verify_dependency_graph.py

# 2. The suite and the static checks
python -m ruff check dz_erp tests tools
python -m pytest -q

# 3. End-to-end behaviour on synthetic data
python tools/staging_acceptance.py            # 33/33

# 4. Rotation and recovery, with a forced crash
python tools/rotation_scale_drill.py --documents 300 --events 3000

# 5. Tamper checks: modify one byte of a stored document or an audit row and
#    confirm verification fails (tests cover both; see §3 test files)

# 6. Custody and rotation health
python -m dz_erp.rotate_keys status  --db <db> --vault <vault>
python -m dz_erp.rotate_keys monitor --db <db> --vault <vault>   # exit 3 when unhealthy
```

Sample data is synthetic; no reviewer-facing command requires production credentials, and
none prints key material.

---

## 7. Sign-off block (to be completed outside this repository)

| Role | Name | Scope of sign-off | Date | Reference |
|---|---|---|---|---|
| Expert-Comptable (ONEC) | | SCF mapping, G50, reversal treatment, retention | | |
| Fiscaliste | | VAT, timbre, G50 boxes | | |
| DPO / legal counsel | | Law 18-07, ANPDP, transfers | | |
| Security officer | | Key custody compensating controls, residual risk acceptance | | |
| Operations owner | | Rotation schedule, monitoring, restore rehearsals | | |

Until these are completed, the correct statement is: *the engineering controls are implemented
and tested; statutory compliance is not established.*
