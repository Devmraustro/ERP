# Release evidence index (Phase 8 + Phase 9 + Phase 10)

Baseline for every statement in this directory: **PR #7 head `f603e38`**, CPython
3.12.10, dependencies installed with `--require-hashes`. The session branch
`arena/7557bcfb-erp` (`4fc1364`) is preserved as found — see the git reconciliation
report. Nothing here is committed or pushed.

## Top level

| Document | Contents |
| --- | --- |
| `FINAL_PRODUCTION_READINESS_REPORT.md` | **the verdict: NOT READY** (implementation verified), gates 1–10, commands, risks |
| `PHASE8_FINAL_V1_ACCEPTANCE_REPORT.md` | Phase 8 acceptance: what was executed, what was not |
| `phase9/INITIAL_REPOSITORY_BASELINE.md` | first-action baseline and divergences |

## Phase 8 (`phase8/`)

| Document | Contents |
| --- | --- |
| `GIT_RECONCILIATION_REPORT.md` | branch/HEAD/PR #7 divergence, untracked user work preserved |
| `SECURITY_CLOSURE_REPORT.md` | controls C1–C9 → tests → results (248 tests) |
| `REVERSE_PROXY_TLS_ACCEPTANCE.md` | real TLS + proxy harness: 11 PASS, 1 NOT EXECUTED, 1 OBSERVED |
| `WINDOWS_DESKTOP_ACCEPTANCE.md` | **NOT EXECUTED** + exact Windows procedure |
| `EXPERT_APPROVAL_STATUS.md` | 18 rules + 12 questions, all **PENDING** |
| `OPERATIONAL_READINESS_REPORT.md` | start/health/backup/logging/failure behaviour |
| `evidence/tls_proxy_harness.py` | self-contained TLS termination + proxy + redirect + backend |
| `evidence/logs/` | security, business and TLS run logs |

## Phase 9 (`phase9/`)

| Document | Contents |
| --- | --- |
| `SECURITY_HARDENING_REPORT.md` | boundary-by-boundary results, six residual risks |
| `ACCOUNTING_INTEGRITY_REPORT.md` | 14 invariants, idempotent posting, SoD, reversals |
| `ALGERIAN_COMPLIANCE_VALIDATION_REPORT.md` | implementation vs legal research vs approval, kept separate |
| `OCR_AI_ACCEPTANCE_REPORT.md` | 6 synthetic strata, Arabic-scan limitation, N/A items |
| `DATABASE_INTEGRITY_REPORT.md` | schema, constraints, transactions, restart, migrations |
| `DISASTER_RECOVERY_ACCEPTANCE.md` | measured backup/restore with hashes + audit chain |
| `PERFORMANCE_REPORT.md` | measured latency, throughput, memory, OCR timings |
| `DESKTOP_ACCEPTANCE_REPORT.md` | **NOT EXECUTED**, requirement-by-requirement |
| `DEPLOYMENT_READINESS_REPORT.md` | V1 architecture preserved, reference deployment |
| `OPERATIONAL_READINESS_REPORT.md` | monitoring inventory, runbook, gaps |
| `END_TO_END_ACCEPTANCE_REPORT.md` | scenarios 1–10 with persisted-state assertions |
| `evidence/dr_drill.py` | reproducible DR drill |
| `evidence/performance_probe.py` | reproducible latency/throughput probe |
| `evidence/db_concurrency_restart.py` | duplicate creation, idempotency, restart, rollback |
| `evidence/logs/` | all execution logs (full suite, ruff, compileall, pip-audit, drills) |

## Phase 10 (`phase10/`) — operational readiness, dependency audit, acceptance procedures

| Document | Contents |
| --- | --- |
| `PHASE10_FINAL_REPORT.md` | **the Phase 10 verdict: CONDITIONALLY PRODUCTION READY / IMPLEMENTATION VERIFIED**, commands, exit codes, blockers |
| `OPERATIONAL_READINESS_REPORT.md` | readiness/health contract, DB + OCR + disk checks, backup automation, log rotation, alerting |
| `BACKUP_AND_RECOVERY_RUNBOOK.md` | scheduled encrypted backups, retention, integrity monitoring, restore procedure |
| `DATA_RETENTION_POLICY.md` | every record class and period — **all statutory periods PENDING (no invented Algerian periods)** |
| `DEPENDENCY_REMEDIATION_REPORT.md` | **P0 dependency remediation**: 143 advisories → 0, pins/hashes, clean-env install, audits, regression |
| `OPENCV_WHEEL_DECISION.md` | OpenCV `-python` vs `-headless` decision with symbol evidence; lock unchanged (formerly named `DEPENDENCY_REMEDIATION_REPORT.md`) |
| `DEPENDENCY_AUDIT_FINDING.md` | original finding (now **REMEDIATED**): 143 distinct advisories across 8 pins, feasibility probe, plan |
| `EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` | 18 rules (VAT/TIMBRE/G50/ACCOUNT/DATE) + 12 questions, all **PENDING**, no legal citations |
| `WINDOWS_DESKTOP_ACCEPTANCE.md` | 9 headless rows executed, 9 GUI rows **NOT EXECUTED**, English-locale GAP, reproducible procedure |
| `PUBLIC_TLS_ACCEPTANCE.md` | **NOT EXECUTED** — 13-check procedure requiring a real hostname and public CA |
| `REPOSITORY_AND_EVIDENCE_VERIFICATION.md` | PR #7 untouched, Phase 9 evidence inventory verified (21 docs / 4 scripts / 17 logs) |
| `windows_acceptance.ps1` | the Windows acceptance script (to run on a real Windows host) |
| `public_tls_validation.sh` | the 13-check TLS validator (exit 2 without a hostname) |
| `evidence/operational_drill.py` | executed backup + restore drill (16/16 PASS) |
| `evidence/desktop_headless_acceptance.py` | executed desktop pre-flight matrix |
| `evidence/logs/` | final suite (781 passed), baseline (742), drills, pip-audit, ruff, compileall |
| `evidence/logs/dependency_remediation/` | audit after remediation (exit 0), clean-env exact match + smoke, full suite on new lock, targeted OCR/PDF/API run |

## Phase 29 (`phase29/`) — desktop redesign

| Document | Contents |
| --- | --- |
| `PHASE29_DESKTOP_REDESIGN_REPORT.md` | design system, themes, RTL, review workspace, the 12 defects the tests/screenshots found and how each was fixed, test results, what is **NOT** verified |
| `screenshots/01…20.png` | FR light, FR dark, AR RTL, EN LTR, review workspace, collapsed sidebar, 1024 px narrow |

## How to reproduce

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
python -m pytest -q
ERP_ROOT=$PWD ERP_VENV=$PWD/.venv python docs/release/phase8/evidence/tls_proxy_harness.py
ERP_ROOT=$PWD ERP_VENV=$PWD/.venv python docs/release/phase9/evidence/dr_drill.py
ERP_ROOT=$PWD ERP_VENV=$PWD/.venv python docs/release/phase9/evidence/db_concurrency_restart.py
ERP_ROOT=$PWD ERP_VENV=$PWD/.venv python docs/release/phase9/evidence/performance_probe.py
ERP_ROOT=$PWD ERP_VENV=$PWD/.venv python docs/release/phase10/evidence/operational_drill.py
ERP_ROOT=$PWD ERP_VENV=$PWD/.venv python docs/release/phase10/evidence/desktop_headless_acceptance.py
bash docs/release/phase10/public_tls_validation.sh   # exit 2: needs a real hostname
```
