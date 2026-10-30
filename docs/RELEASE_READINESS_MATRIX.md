# Release readiness matrix

One table that says, for every requirement that stands between this build and a
production release: what the requirement is, which risk it answers, who owns
it, what evidence is required, how to verify what *is* verifiable today, the
current status, what it externally depends on, and the explicit pass criteria.

The rule this matrix enforces on itself: **a documented procedure is not
operational completion.** Rows whose evidence can only exist outside this
repository (a real KMS, a real off-host copy, a real drill, a qualified
review) stay blocked until that evidence is supplied by its owner. Everything
verified locally is cited to a command that actually exists and a file that
actually ships.

Status vocabulary (fixed, machine-checked in
`tests/test_phase22_register_and_docs_consistency.py`):

* **Verified** — the evidence exists *in this repository* and the verification
  command passes on the current commit.
* **Blocked (external evidence required)** — no local evidence can ever be
  sufficient; the named external owner must supply it.

Related evidence: `docs/RISK_REGISTER.md` (risk IDs below),
`tools/operational_readiness.py` (deployed-configuration checker),
`docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md`, `docs/KEY_PROVIDER_CONTRACT.md`,
`docs/DEPENDENCY_AND_LICENSE_REGISTER.md`, `docs/OCR_EVALUATION_PROTOCOL.md`,
`docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`,
`docs/STAGING_SECURITY_CONFIGURATION.md`.

## 1. The matrix

| ID | Requirement (risk) | Owner | Required evidence | Verification command / acceptance procedure | Status | External dependency | Pass criteria |
|---|---|---|---|---|---|---|---|
| R-KMS | Key custody is hardware-backed or an approved equivalent (S1, S3; `KEY_PROVIDER_CONTRACT.md` §4) | Security owner (expert-comptable / DPO) with the infrastructure operator | A real KMS/HSM integration meeting §4 acceptance 1–5, **or** a signed approval of file-based custody with compensating controls | `ERP_KEY_PROVIDER` must resolve fail-closed: `python -m pytest tests/test_phase17_key_providers.py -q` (kms/hsm declarations refuse start-up); deployed custody: `python tools/operational_readiness.py --run-checks` | **Blocked (external evidence required)** | Procurement/integration of an actual KMS/HSM, or a formal custody decision | §4 items 1–5 demonstrated against the real boundary with `hsm_equivalent: True` in `describe()`, or a signed file-based-custody approval naming compensating controls |
| R-PROV | Publisher authenticity for pinned dependencies, beyond byte hashes (B2) | Dependency owner (release engineer) | PEP 740 / Sigstore attestation verification demonstrated for the pinned set, or an accepted residual-risk decision | Hash integrity (today's verified half): `python tools/verify_wheelhouse.py --wheelhouse <dir>` offline; `python tools/generate_hash_lock.py --check` | **Blocked (external evidence required)** | Attestation verification tooling/apply a real mirror; no fake provenance is shipped | Every pinned distribution's publisher attestation verified on the artifacts actually installed, or a recorded acceptance of the residual risk |
| R-OFFHOST | Ledger checkpoints exported off-host on a schedule, under independent credentials (D6) | Operations owner (infrastructure) | Proof of an installed scheduler (timer/cron) and an off-host copy retrievable with credentials the host does not hold | Local preconditions only: `python tools/operational_readiness.py --checkpoint <f> --max-checkpoint-age-days 1`; live verify: `python -m dz_erp.rotate_keys monitor --db <db> --vault <v> --checkpoint-verify <f> --max-checkpoint-age-days <n>` (exit 0 = healthy, 3 = unhealthy) | **Blocked (external evidence required)** | systemd timer/cron installation; off-host storage provisioned | `systemctl list-timers` (or cron) shows the units recently fired **and** an off-host checkpoint newer than the previous export is retrievable without host credentials |
| R-ALERT | Monitor failures reach a responsible human (D6) | Operations owner with the on-call rota | A test alarm delivered end-to-end through the real channel (monitor exit 3 → page/message received) | Local half: monitor exit-code contract, `python -m pytest tests/test_phase20_checkpoints_and_monitoring.py -q`; delivery: exercise the real alert path once and record it | **Blocked (external evidence required)** | Alert channel (mail/IM/pager) wired to the scheduler's onFailure / wrapper | A deliberately unhealthy ledger causes a received alert naming the host and the failing check |
| R-DR | Disaster recovery verified at production scale on real media (D3, D4) | Operations owner | A restore of the latest production backup into an isolated environment, verifying hashes, trial balance and audit chains | The restore *procedure* is tested at staging scale: `python -m pytest tests/test_phase12_key_migration_and_backup.py -q`; production drill is external | **Blocked (external evidence required)** | Production-scale data; isolated recovery environment; time window | A recorded drill restores the latest production backup with db/vault hashes verified, balanced trial balance, valid audit chains — and the measured RTO meets the stated objective |
| R-CONC | Concurrency behaviour validated at production scale (D7) | Application owner with operations | A load/concurrency run on production-scale data with the documented failure-rate and deadlock observations | In-repo: `python -m pytest tests/test_phase4_procurement_3way_match.py tests/test_phase16_rotation_concurrency_convergence.py -q` (concurrent stock updates; 5-thread rotation contention); production-scale run is external | **Blocked (external evidence required)** | Production-scale dataset; realistic concurrent client mix | A recorded run at target scale with WAL, busy-timeout and lock-failure metrics within the documented bounds; no lost update or chain break |
| R-OCR | Extraction validated on a real-world, authorized corpus (O1, O3) | Application owner + data protection owner (corpus authorization) | An authorized corpus per `OCR_EVALUATION_PROTOCOL.md` §12 (manifest with authorization/redaction/access control), evaluated, signed off | Interface verified: `python -m pytest tests/test_phase6_ocr_benchmark_e2e.py -q` (loader refuses unauthorized manifests; evaluation runs the real engine) and `python -m pytest tests/test_phase29_ocr_review_policy.py -q` (the fail-closed field review that gates posting, including per-line quantity, unit-price and discount review and the reviewed-source/invoice-payload consistency gate with derived line-net comparison); synthetic reproducibility: two-run clean-dir equality of all non-latency keys | **Blocked (external evidence required)** | Authorized, redacted real-world corpus with owner sign-off | A `evaluate_external_dataset` report over the authorized corpus, with per-field exact match/CER, missing/mismatch categories and arithmetic consistency at §4.2 sample sizes, signed off by the corpus owner; human approval step unchanged |
| R-SCF | SCF chart, VAT/timbre/G50 mappings reviewed by a qualified Algerian professional (C1, C2, C3) | Engagement owner (expert-comptable) | A written professional review of the chart, rates and G50 workbook against the statute/current LF | Checklist self-tests: `python tools/staging_acceptance.py` (exit 0, 40 checks) includes the rate/timbre/G50 invariants; the professional review itself is external | **Blocked (external evidence required)** | Engaged expert-comptable review cycle | A dated, signed review accepting the SCF mapping, VAT/timbre brackets and the G50 *preparation-workbook* (not-a-filing) designation |
| R-LAW | Law 18-07 / ANPDP obligations discharged (C6) | Data protection owner (DPO) | Processing register, data-subject workflow, ANPDP authorization/filing as applicable, transfer assessment | Checklist structure is in-repo: `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` §4; the filings themselves are external | **Blocked (external evidence required)** | ANPDP filings; DPO register; legal advice as needed | Register complete, data-subject workflow demonstrable end-to-end, ANPDP authorization obtained where required, transfers assessed and documented |
| R-OPS | Deployed configuration is complete and its verification entry points work, at every release gate (S5, D6; local) | Release engineer | The operational readiness checker run against the deployment's actual environment | `python tools/operational_readiness.py --run-checks [--checkpoint <f> --max-checkpoint-age-days <n>] --report-json report.json`; exit 0 = configuration ready; exit 1 names every failure and its remediation | **Verified** | None for the checker itself; scheduler/alerting evidence is R-OFFHOST/R-ALERT | Exit code 0 on the deployment host, every failure item actionable, and the checker's *external evidence* section (never exit-code-affecting) acknowledged rather than claimed |

## 2. What "Verified" is allowed to mean here

Only R-OPS is locally verified, and only in its honest scope: it verifies
*configuration presence* and that the *verification commands exist and are
runnable*, plus checkpoint shape/freshness when a checkpoint is supplied. It
does not verify off-host storage, scheduler installation, alert delivery,
DR drills, KMS custody, publisher provenance, a real corpus, or professional
approval — those are the other nine rows, and they stay blocked until their
owners supply evidence. The checker itself reports this split
(`deployment_evidence_required`) and its exit code ignores what it cannot see.

## 3. Keeping the matrix honest

* `tests/test_phase22_register_and_docs_consistency.py` machine-checks: IDs
  unique, every cited repository path exists, statuses drawn only from the
  vocabulary above, every blocked row names its external dependency, and the
  R-* IDs cited by `tools/operational_readiness.py` resolve to rows in this
  table.
* When an external owner supplies evidence, the row's **Status** changes only
  with a citation to that evidence (where it is stored, who signed it, when) —
  never by rewording a procedure into a claim.
