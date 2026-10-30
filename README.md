# Algerian AI ERP Platform (`Devmraustro/ERP`)

Production-grade Algerian *Système Comptable Financier* (SCF) localization, procure-to-pay (`PO → Goods Receipt → 3-Way Match → SoD Approval → Idempotent GL Posting`), encrypted supplier invoice intake with OCR (`pdfplumber` + `RapidOCR PP-OCRv4`), and tamper-evident audit/backup architecture.

## Current state — completion workstream (Phases 34–38)

Read this section first; the phase narratives below are dated history.

**Fixed and verified in this workstream** (branch `arena/7f12d627-erp`, PR #7,
on top of `852f389f`):

| Phase | Change | Tests |
|---|---|---|
| 34 | **A duplicate OCR extraction row could reach the ledger.** A second row for the same field was flagged but the flag never blocked anything; the invoice submitted with an unreviewed value. Duplicates are now a blocking reason at submit, approve **and** post; decisions bind to the extraction rows they covered (`evidence_json`); flagging never resolves a group; a posting-time refusal recovers the invoice to `NeedsReview` instead of dead-ending it | `tests/test_phase34_ocr_duplicate_row_integrity.py` — 19 |
| 35 | Failed logins throttled per (client, account) **before** password verification; every response carries `X-Request-Id` and a credential-free `HTTP_REQUEST` record | `tests/test_phase35_api_rate_limiting_and_request_logging.py` — 16 |
| 36 | Stamp duty and VAT resolved from the **effective-dated** configuration instead of compiled constants; a gap or ambiguous configuration stops the invoice rather than guessing; G50 box E2 is now traceable | `tests/test_phase36_accounting_and_tax_configuration.py` — 29 |
| 37 | Container and operator assets prepared, with a drift gate that fails when the code reads an `ERP_*` variable the operator docs do not document | `tests/test_phase37_deployment_assets.py` — 6 |
| 38 | Migration matrix: convergence, idempotency, preservation, interruption recovery, atomic rebuild | `tests/test_phase38_migration_matrix.py` — 24 |

**Measured on this host** (Linux, CPython 3.11.2): full suite **890 passed, 20
failed, 7 skipped, 78 % coverage**. All 20 failures are
`restore_and_verify_backup`'s interpreter floor (`Safe backup restore requires
Python 3.11.17+`), and the failure set is `diff`-identical to the pre-change
baseline; every one of those paths is green in CI on the runner's CPython `3.11` and
`3.12`. `ruff check dz_erp tests tools` is clean.

**CI on the final head** (`8b1b56c9`, run
[`41129089741`](https://github.com/Devmraustro/ERP/actions/runs/41129089741)):
**3/3 jobs green** — full suite with coverage on the runner's CPython `3.11` and
`3.12`, plus the Windows rotation/backup/staging job (pinned `3.12.10`). On those interpreters the 20
interpreter-floor failures above do not occur.

**Not done, and not claimed:** the container image has never been built here; no
migration has run against production-sized data; there is no MFA; the rate limiter
is per process; and the Algerian tax values, key custody, DR evidence and Law 18-07
determinations remain `BLOCKED_EXTERNAL`. See
[`docs/RELEASE_READINESS_MATRIX.md`](docs/RELEASE_READINESS_MATRIX.md).

New documents: [`docs/PROJECT_BASELINE_AND_COMPLETION_PLAN.md`](docs/PROJECT_BASELINE_AND_COMPLETION_PLAN.md),
[`docs/PROJECT_COMPLETION_TRACKER.md`](docs/PROJECT_COMPLETION_TRACKER.md),
[`docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md`](docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md),
[`docs/MIGRATION_AND_DATA_SAFETY.md`](docs/MIGRATION_AND_DATA_SAFETY.md),
[`docs/SECURITY_FINAL_AUDIT.md`](docs/SECURITY_FINAL_AUDIT.md),
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md),
[`docs/DATA_PROTECTION_COMPLIANCE.md`](docs/DATA_PROTECTION_COMPLIANCE.md),
[`docs/INCIDENT_RESPONSE_PLAN.md`](docs/INCIDENT_RESPONSE_PLAN.md),
[`docs/BACKUP_AND_RECOVERY_RUNBOOK.md`](docs/BACKUP_AND_RECOVERY_RUNBOOK.md),
[`docs/PRODUCTION_DEPLOYMENT_GUIDE.md`](docs/PRODUCTION_DEPLOYMENT_GUIDE.md),
[`docs/FINAL_QA_REPORT.md`](docs/FINAL_QA_REPORT.md),
[`docs/EXECUTIVE_REPORT.md`](docs/EXECUTIVE_REPORT.md),
[`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md),
[`docs/USER_GUIDE_FR.md`](docs/USER_GUIDE_FR.md),
[`docs/USER_GUIDE_AR.md`](docs/USER_GUIDE_AR.md),
[`docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md`](docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md),
[`docs/REMAINING_BLOCKERS.md`](docs/REMAINING_BLOCKERS.md).

## Desktop application - installed, local-first (Phases 25-27)

The product is an **installed desktop application** (logiciel de gestion), not a website: launch
`dz-erp` on the workstation, log in, and work with the accounting data that lives on that machine
(Mode A) or on the company's own server (Mode B). No browser, no cloud account, no internet for
core operations. The client never opens the database file — every operation goes through the
verified authenticated API (server-verified tokens, RBAC, tenant isolation, audited denials), so
the desktop UI inherits the same controls the test suite enforces on the server.

```bat
py -3.12 -m pip install -e ".[desktop]"   :: PySide6==6.11.2 (optional GUI extra)
dz-erp                                    :: start the desktop app (Mode A, first-run wizard)
dz-erp --language ar                      :: Arabic interface (right-to-left layout)
dz-erp --mode server --server-url http://erp-intranet:8000   :: Mode B (company LAN server)
dz-erp --smoke                            :: headless Mode A verification (no GUI, exit 0)
```

Modes: **A — single workstation**: a private ERP service starts inside the application, bound to
`127.0.0.1` on an ephemeral port, backed by the local database and encrypted document vault under
`%LOCALAPPDATA%\AlgerianERP` (XDG equivalent elsewhere). **B — company LAN**: the client connects
to a company-hosted `python -m dz_erp.serve` deployment; a SQLite file shared over the network is
explicitly **not** a supported multi-user architecture. Details, data locations, backup/uninstall
rules and the honest gap list: `docs/DESKTOP_APPLICATION.md`.

**Desktop-phase verification (this branch, Windows/CPython 3.12.10).** The read-only client API
passes 6/6 (`tests/test_phase25_read_api.py`: tenant scoping, audited warehouse-role denials,
clamped limits, POSTED-only balanced trial balance). The desktop suite passes 10/10
(`tests/test_phase26_desktop.py`: secret-free config whitelist, corrupt-config recovery,
loopback-only ephemeral embedded service, first-run overwrite refusal, full
bootstrap→serve→login→read→logout→stop cycle over the seeded chart, headless `--smoke` subprocess
exit 0, offscreen Qt construction with the Arabic right-to-left switch). Phase 27 adds 17 tests:
the first run now seeds and exactly validates the **official SCF chart of accounts** (294 accounts;
fail-closed when the vendored source is absent, resumable after an interrupted run;
`tests/test_phase27_scf_first_run.py` 8/8), four authenticated review/navigation endpoints with
audited cross-company denials and byte-exact document content (`tests/test_phase27_review_api.py`
5/5), and the desktop review UI — invoice review dialog with honest confidence display, human
correction preserving raw OCR, explicit submit→approve→post with server-enforced segregation of
duties and duplicate-post refusal surfaced as errors (`tests/test_phase27_desktop_ui.py` 4/4).
Phase 28 adds user administration end to end: three admin-only endpoints (tenant-scoped account
listing without credential material, account creation with role validation, PBKDF2 hashing and an
8-character password floor at the API boundary, confirmation-backed deactivation that ends the
target's live session at the next request; every non-admin attempt is audit-logged, unknown and
cross-tenant targets are conflated to a single 403 — `tests/test_phase28_user_admin.py` 12/12) and
the desktop users view (visible only to the server-resolved `admin` role; creation dialog;
confirmation-gated deactivation — `tests/test_phase28_desktop_users_ui.py` 7/7, offscreen Qt).
Phase 29 makes OCR uncertainty handling **fail-closed and enforced**: field-level review decisions
(`CONFIRMED` bound to the reviewed value / `CORRECTED` or honest `human-manual-entry` transcription /
`FLAGGED` / `NOT_PRESENT` for genuinely absent optional fields) are separate attributed records, and
an invoice whose document has undecided, flagged or stale required-field reviews, or whose extracted
totals do not sum, is refused at submission and re-checked at approval — every refusal audited
(`tests/test_phase29_ocr_review_policy.py` 24/24). A measured score can only mark a field
`low_confidence`; it never accepts it, and auto-accept stays unimplemented on purpose
(`docs/OCR_EVALUATION_PROTOCOL.md` §6). The OpenAPI protected-operation pin is 19 → 26 → 28. Full
local suite at the Phase 29 head: **710 passed, 20 skipped, 0 failed, exit 0** (23 min 48 s; 85 %
coverage — see §2G of `docs/QA_TEST_MATRIX_AND_RESULTS.md`). Full local suite at the Phase 28 head:
**686 passed, 20 skipped, 0 failed, exit 0** (24 min 51 s; 85 % coverage — see §2C–2F of
`docs/QA_TEST_MATRIX_AND_RESULTS.md`). The PyInstaller one-file build succeeds
(128.3 MiB, SHA-256 sidecar verified) and now bundles the vendored SCF data for frozen builds;
executing the unsigned exe is blocked by this workstation's application-control policy
(WinError 4551) and is recorded, not bypassed (`docs/DESKTOP_APPLICATION.md` §8.2).

## Quick Verification

Verified at code head `4225f36c56fed66700d61ffe832db7373214a93d` (2026-10-25):
GitHub Actions run [`40210565234`](https://github.com/Devmraustro/ERP/actions/runs/40210565234)
is green on Linux Python 3.11 and 3.12 (562 passed, 3 skipped, 88 % coverage each)
**and** the Windows key-rotation/backup job (138 passed, 8 skipped), with the
isolated synthetic staging acceptance at **37/37** on both platforms. The
documentation-only successor `62a4bf9` (PR #2 head) is also green (run
[`40211521769`](https://github.com/Devmraustro/ERP/actions/runs/40211521769),
all three jobs successful).

On Windows/CPython 3.12.10 the full local suite reports **546 passed, 19 skipped,
0 failed** and the staging acceptance script exits **0** with **37/37** checks.
The 19 local skips are POSIX-only mode-bit/fd tests and a `systemd-analyze`
availability check; they run on Linux CI. Supported runtimes are Python
3.11.17+ and 3.12.10+ (`requires-python` in `pyproject.toml`); older patch
releases, including 3.11.9, are refused by the restore safety gate because they
lack the tarfile security fixes plus the independent archive preflight.
(The retired `/tmp/glstub` workaround is unsupported — see the note in
`docs/QA_TEST_MATRIX_AND_RESULTS.md` §1.)

**Production Readiness Phase 1 evidence round (2026-10-26, commit `ccc874b`).**
Full suite on Windows/CPython 3.12.10: **602 passed, 20 skipped, 0 failed**
(20 min 09 s; one benign third-party `DeprecationWarning` from
starlette/anyio), coverage **87 %** (4,575 statements, 589 missed; `serve.py`
100 % after the provider gate), staging acceptance **37/37, exit 0**. The
benchmark reproduces its committed figures on consecutive runs from clean
output directories (all non-latency report keys identical — exact-match
`0.9097`, ECE, totals, failure categories, strata/language roll-ups); latency
is host-dependent and excluded. The prior round's evidence (551 passed/19
skipped at `8dd53fc`, OCR reproductions, latency observations) remains
attached to that commit.

The deployment-readiness gate behaves as documented on this non-deployment
host: `python tools/operational_readiness.py` exits **1** because the ERP
environment is deliberately unconfigured here (every failure names the missing
variable and its remediation); with `--run-checks`, the two read-only
verifiers — `verify_dependency_graph.py` and `generate_hash_lock.py --check` —
execute with **exit 0**, while `key-status` is reported **unavailable**
(it requires `ERP_DB_PATH`/`ERP_VAULT_PATH`), and the overall exit code stays
non-zero. On a real deployment host the same command exercises `key-status`
and checkpoint verification; the eight external-evidence items (off-host
storage, scheduler, alerting, DR drill, KMS/HSM, provenance, OCR corpus,
professional approvals) are reported as required and never affect the exit
code.

**First CI verdict on this phase (run #103, `40370500808`, on `ccc874b`).**
`ccc874b` was pushed to both working branches (`arena/6c474c64-erp` and
`arena/5c240e1d-erp`, head of PR #2) as pure fast-forwards from `8dd53fc`.
The Windows job (rotation, backup, recovery, staging acceptance) passed. Both
Linux jobs (Python 3.11 and 3.12) failed at the test-suite step — a
*test-fixture* defect, not a product one: `_good_env` wrote key files without
`chmod`, so under the runners' umask they landed `0644` and the checker's
POSIX permission gate correctly refused them (on Windows that gate only
warns, which is why local runs had passed). The checker was right; the
fixture was incomplete on POSIX. This commit fixes it: key files written
`0600`, the vault directory `0700` on POSIX, and a cross-platform test
pinning the gate's fail direction (phase 24 = 20 tests). The earlier green
runs quoted above remain attached to `4225f36` and `62a4bf9` only.

Full suite at this commit (Windows/CPython 3.12.10): **603 passed, 20
skipped, 0 failed** (17 min 34 s; the same single benign starlette/anyio
warning), coverage **89 %** (4,575 statements, 515 missed; `tools/` stays
outside `--cov=dz_erp`). The two points above the `ccc874b` round's 87 % are
a measured capture artifact, not a code or test change: that round's
coverage combine ran before one OCR-benchmark subprocess wrote its
parallel-data file, so its report missed the subprocess's `dz_erp/benchmark.py`
lines; this run's combine consumed the straggler, and the entire difference
sits in `dz_erp/benchmark.py` (module-entry and `main()` lines executed only
by the suite's own OCR-benchmark subprocess). Subprocess coverage can
therefore move a point or two between runs; test outcomes do not.

New in this round: `tools/operational_readiness.py` (deterministic deployed-
configuration checker, exit codes 0/1/2, external evidence reported as
required, phase 24 = 20 tests), the fail-closed `ERP_KEY_PROVIDER` gate
(`kms`/`hsm` declarations refuse start-up; phase 17 = 31 collected items),
wheelhouse verifier hardening — nested directories and non-artifact files
flagged, ambiguous digests refused, offline install flags pinned by test
(phase 19 now 25) — the authorized-corpus OCR interface
(`load_external_dataset`/`evaluate_external_dataset`, protocol §12), and the
single release-readiness matrix (`docs/RELEASE_READINESS_MATRIX.md`,
machine-checked; phase 22 now 15).

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
python tools/verify_dependency_graph.py        # explicit dependency-graph validation
python tools/generate_hash_lock.py --check     # lockfile/hash drift check
python tools/verify_wheelhouse.py --build ./wheelhouse   # print the hash-enforced offline wheelhouse command
python tools/operational_readiness.py --run-checks       # deployed-configuration readiness gate (exit 0/1/2)
python -m pytest --cov=dz_erp --cov-report=term-missing
python -m ruff check dz_erp tests tools        # static check
```

> Authentication is **server-verified bearer-token** based
> (`Authorization: Bearer <token>` from `POST /api/v1/auth/token`). Self-asserted
> identity headers (`X-User-Id`, `X-Company-Id`) are not trusted and are ignored —
> see [`docs/AUTHENTICATION_HARDENING_SPEC.md`](docs/AUTHENTICATION_HARDENING_SPEC.md).

## Documentation Deliverables (`docs/`)

1. [`docs/BASELINE_AUDIT_AND_ARCHITECTURE.md`](docs/BASELINE_AUDIT_AND_ARCHITECTURE.md) — Phase 1 repository baseline, upstream ERP comparison (`Odoo 18.0/19.0` vs. `ERPNext v15/v16`), and system architecture.
2. [`docs/DEPENDENCY_AND_LICENSE_REGISTER.md`](docs/DEPENDENCY_AND_LICENSE_REGISTER.md) — Pinned commit SHAs, package versions (`requirements.lock.txt`), and license due diligence (`LGPL-3.0`, `Apache-2.0`, `MIT`, `BSD-3-Clause`).
3. [`docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md`](docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md) — Phase 2 Algerian SCF chart fixes (`411` vs. `413`, 24 account reclassifications, reconciliation flags, group `32`/`66` FR/AR fixes, 69 Wilayas, DZD decimal precision, *Droit de Timbre*, G50 preparation workbook, conservative idempotent migration).
4. [`docs/INVOICE_INTAKE_AND_OCR_SPEC.md`](docs/INVOICE_INTAKE_AND_OCR_SPEC.md) — Phase 3 document security inspection, encrypted-at-rest `PrivateDocumentVault`, OCR extraction, prompt-injection defense, and non-destructive human edits.
5. [`docs/PROCUREMENT_STOCK_AND_3WAY_MATCH_SPEC.md`](docs/PROCUREMENT_STOCK_AND_3WAY_MATCH_SPEC.md) — Phase 4 procurement, perpetual stock valuation via `380` clearing account, partial receipts & backorders, 3-way matching, idempotent posting, credit note reversals, and concurrency safety.
6. [`docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md`](docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md) — Phase 5 server-side RBAC, Segregation of Duties (`creator != approver`), multi-company isolation, trigger-protected HMAC-SHA256 audit hash-chain, secret/PII log redaction, and encrypted backup/restore verification.
7. [`docs/OCR_BENCHMARK_REPORT.md`](docs/OCR_BENCHMARK_REPORT.md) — Phase 6 empirical OCR benchmark report across 18 synthetic Algerian invoices in 6 strata (`benchmarks/results/ground_truth_and_results.json`).
8. **[`docs/AUTHENTICATION_HARDENING_SPEC.md`](docs/AUTHENTICATION_HARDENING_SPEC.md)** — **Phase 7** remediation of the `X-User-Id`/`X-Company-Id` impersonation vulnerability (`CWE-287`/`CWE-639`): opaque revocable bearer tokens, PBKDF2 password hashing, brute-force lockout, schema migration, and the denial-audit rollback defect.
9. [`docs/QA_TEST_MATRIX_AND_RESULTS.md`](docs/QA_TEST_MATRIX_AND_RESULTS.md) — Complete QA execution matrix and coverage results (623 collected tests at the current head: 603 passed, 20 skipped; earlier counts retained as dated history).
10. [`docs/PRODUCTION_READINESS_AND_GO_NO_GO.md`](docs/PRODUCTION_READINESS_AND_GO_NO_GO.md) — Final production-readiness assessment and Go/No-Go decision.
11. [`docs/KEY_PROVIDER_CONTRACT.md`](docs/KEY_PROVIDER_CONTRACT.md) — The contract between the application and any key source: provider protocol, auditable `describe()` surface, failure modes (raise, never fall back), rotation behaviour, Windows ACL limitation, and the acceptance criteria a real KMS/HSM integration must meet (none is bundled; `KmsKeyProvider` reports `implemented: False`, `hsm_equivalent: False`). Includes the fail-closed `ERP_KEY_PROVIDER` selection gate and per-provider outage behaviour.
12. [`docs/RELEASE_READINESS_MATRIX.md`](docs/RELEASE_READINESS_MATRIX.md) — The single release-gate table: requirement/risk ID, owner, required evidence, verification command, status (Verified vs. Blocked-external), external dependency and explicit pass criteria for KMS/HSM custody, publisher provenance, off-host checkpoints, alerting, DR, production-scale concurrency, OCR validation, Algerian accounting/tax review and Law 18-07/ANPDP. Companion tool: `tools/operational_readiness.py`, the deterministic deployed-configuration checker whose exit code covers only what it can actually see.
13. [`docs/EXECUTIVE_REPORT.md`](docs/EXECUTIVE_REPORT.md) / [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md) — what this workstream changed, with the evidence and the limits stated side by side.
14. [`docs/SECURITY_FINAL_AUDIT.md`](docs/SECURITY_FINAL_AUDIT.md) — control-by-control status, findings and residual risk.
15. [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) — assets, threats, the control that answers each, and what remains accepted with a named owner.
16. [`docs/DATA_PROTECTION_COMPLIANCE.md`](docs/DATA_PROTECTION_COMPLIANCE.md) — what the software does about personal data, and the determinations only a lawyer can make.
17. [`docs/INCIDENT_RESPONSE_PLAN.md`](docs/INCIDENT_RESPONSE_PLAN.md) — severity, roles, playbooks, evidence handling; organisational blanks marked `TBD`.
18. [`docs/BACKUP_AND_RECOVERY_RUNBOOK.md`](docs/BACKUP_AND_RECOVERY_RUNBOOK.md) — backup, verification in a separate process, and recovery procedures.
19. [`docs/PRODUCTION_DEPLOYMENT_GUIDE.md`](docs/PRODUCTION_DEPLOYMENT_GUIDE.md) — prerequisites, configuration, health model, verification, and the external blockers.
20. [`docs/MIGRATION_AND_DATA_SAFETY.md`](docs/MIGRATION_AND_DATA_SAFETY.md) — the migration rules, the inventory, and why there is no down-migration.
21. [`docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md`](docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md) — arithmetic invariants, effective-dated tax resolution, and the explicit boundary between engineering proof and legal qualification.
22. [`docs/FINAL_QA_REPORT.md`](docs/FINAL_QA_REPORT.md) — every command run, its result, and what was not run.
23. [`docs/USER_GUIDE_FR.md`](docs/USER_GUIDE_FR.md) / [`docs/USER_GUIDE_AR.md`](docs/USER_GUIDE_AR.md) — the invoice workflow for accountants and clerks, in French and Arabic.

24. [`docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md`](docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md) — the independent pass over the exact head: commits and PR containment, CI at step level, the suite and its baseline, the Priority 0 inspection with its own probe, the limiter's deployment scope, log contents, tax resolution, the container gap, the findings register (F-01…F-05) and the four-status classification.
25. [`docs/REMAINING_BLOCKERS.md`](docs/REMAINING_BLOCKERS.md) — what still stands between this repository and a defensible deployment, with a named owner and the exact evidence that closes each item.
