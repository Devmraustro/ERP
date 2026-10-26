# Algerian AI ERP Platform (`Devmraustro/ERP`)

Production-grade Algerian *Système Comptable Financier* (SCF) localization, procure-to-pay (`PO → Goods Receipt → 3-Way Match → SoD Approval → Idempotent GL Posting`), encrypted supplier invoice intake with OCR (`pdfplumber` + `RapidOCR PP-OCRv4`), and tamper-evident audit/backup architecture.

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
