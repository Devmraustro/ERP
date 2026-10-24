# Release Branch Reconciliation

**Snapshot commit:** `f4200e81e534e1431afb54b2c0708405bee12830`  
**Snapshot date:** 2026-10-24  
**Repository:** `Devmraustro/ERP`

This is a point-in-time snapshot of live branch and pull-request metadata. The reconciliation work itself does not authorize a merge, deployment, or production use.

## 1. References verified

| Reference | State at snapshot commit |
|---|---|
| `main` | `0e106e05bc1328780e7a837a65b870d535d9f6d6` |
| Working branch | `arena/6c474c64-erp`; 65 commits ahead of `main`, 0 behind |
| PR #1 | Open, unmerged; head `1b63c6ea341b8d73f1fbce84148bd5425e2e8571` on `arena/f74b041f-erp` |
| PR #2 | Open, unmerged; head `becf52b74f2dabbd3ad9291bd28e2a207ce6b720` on `arena/5c240e1d-erp` |
| Working branch versus PR #2 | 45 commits ahead, 0 behind; 63 files in the compare result |
| Latest code-hardening commit in this round | `566264d686f33f37e9ab60a0b4041bbdd5fa2845` — read-only construction refuses missing audit-key sources |
| Latest regression-test commit in this round | `16f88b069bd5e9731f47b7b6ad07167beb1d99f6` — direct read-only constructor must not mint a key |
| Snapshot documentation commit | `f4200e81e534e1431afb54b2c0708405bee12830` |

The active working branch is the more complete implementation candidate, but it has not yet been selected as the canonical PR review target. Do not treat PR #2 as containing the additional 45 commits, and do not merge or rebase either branch blindly.

## 2. Engineering changes since the earlier snapshot

The active branch has additional safeguards against read-only operations creating state:

- Missing vault paths are refused even when `ERP_VAULT_KEY` is present.
- Missing/uninitialized databases and databases with incompatible schema are refused by the inspection preflight.
- Read-only database construction requires an existing audit-key source and cannot generate replacement key material.
- `ERPDatabase(read_only=True)` opens SQLite with `mode=ro`, skips schema initialization/migrations, does not change journal mode, and uses read transactions.
- CLI `status` and `monitor` use the read-only database mode.
- Audit-key rotation, activation changes, key removal and registry persistence are blocked on read-only instances.
- Preflight compares expected tables, required indexes and integrity triggers against the schema source; missing objects cause a fail-closed response rather than silent recreation.
- Regression tests cover missing paths, lost keys, direct read-only construction, unchanged DB bytes, migration refusal, rejected DDL, key-operation side effects, and missing integrity objects.

These changes have been committed to the active branch. **The new regression tests have not been run in the repository environment in this session.**

## 3. CI evidence and limits

The previously reported GitHub Actions run `39959119943` has three successful jobs:

- Key rotation, backup and recovery on Windows.
- Full test, coverage and static checks on Python 3.12.
- Full test, coverage and static checks on Python 3.11.

The prior report associates that run with code commit `4c3642af480dd072f92e4169c24cc91cc123edf5`. It is historical evidence for that tested tree, not proof that the new read-only hardening passes CI.

For the latest commits, the combined-status endpoint returned no status entries; the available workflow-run wrapper returned no runs for the latest SHA. A local checkout attempt failed because DNS resolution for `github.com` was unavailable. Thus there is no verified current full-suite or CI result for the new commits in this session.

A separate local SQLite mechanics check did confirm that a `mode=ro` connection rejects DDL, leaves the database file bytes unchanged, and detects a dropped trigger. This is a focused environment check only; it is not a substitute for the project test suite.

Previously reported results—531 tests passed, 89% coverage, 37/37 synthetic staging acceptance checks, dependency-graph and lockfile validation, Ruff, compile checks, and four systemd unit checks—remain historical results for the reported older tree. They do not validate the latest commits.

## 4. Remaining production blockers

**Verdict: `CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY`.**

The following remain blocked or operationally unverified:

- **C1/C2/C4/C5/C6:** qualified review of SCF/accounting mappings, fiscal calculations, clearance account treatment, retention and Algerian data-protection obligations.
- **S1/S3:** actual KMS/HSM or equivalent custody infrastructure and an enforced split-custody process.
- **O2:** OCR confidence calibration on representative observations.
- **O3:** Arabic recognition capability; unsupported scans must remain in manual transcription/review.
- **B2:** publisher provenance and a separately managed artifact mirror/wheelhouse.
- **D4/D7:** production-scale recovery drills and verification of the intended database topology and concurrency assumptions.
- **S4/D6:** confirmed off-host checkpoint storage, scheduler installation and working alert delivery.
- No production host, actual KMS/HSM, independent checkpoint destination, representative production-like scan corpus, or customer-specific professional approvals are evidenced.

## 5. Required next actions

1. Obtain a working local checkout or a confirmed CI run for the exact latest working-branch SHA.
2. Run the phase-20 checkpoint/monitoring tests, Ruff, compile checks, complete suite, and staging acceptance on that exact checkout.
3. Inspect all CI jobs and their actual head SHA before asserting success.
4. Review the full 45-commit delta from PR #2 and select a canonical review target only after a file-by-file comparison.
5. Keep infrastructure and professional approval items blocked until evidence or signed approval is available.
6. Obtain separate explicit authorization before merging a PR or deploying.

## 6. Scope

No pull request was merged or closed. `main` was not modified. No production deployment, production key rotation/deletion, or processing of real customer data occurred.
