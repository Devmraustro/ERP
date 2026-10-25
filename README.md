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

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
python tools/verify_dependency_graph.py        # explicit dependency-graph validation
python tools/generate_hash_lock.py --check     # lockfile/hash drift check
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
9. [`docs/QA_TEST_MATRIX_AND_RESULTS.md`](docs/QA_TEST_MATRIX_AND_RESULTS.md) — Complete QA execution matrix and coverage results (565 collected tests at the consolidated head; earlier counts retained as dated history).
10. [`docs/PRODUCTION_READINESS_AND_GO_NO_GO.md`](docs/PRODUCTION_READINESS_AND_GO_NO_GO.md) — Final production-readiness assessment and Go/No-Go decision.
