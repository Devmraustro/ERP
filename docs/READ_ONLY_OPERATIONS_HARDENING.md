# Read-Only Operations and Key-Command Path Hardening

**Observed:** 2026-10-24  
**Repository:** `Devmraustro/ERP`  
**Working branch:** `arena/6c474c64-erp`  
**Scope:** Prevent inspection, dry-run, and key-management commands from silently creating a replacement store when given a missing, empty, or incorrectly selected path.

## 1. Defects identified

### Vault path

`PrivateDocumentVault.__init__()` creates the vault root directory. Before the guard, `monitor --vault <mistyped-path>` and `status --vault <mistyped-path>` could reach this constructor when `ERP_VAULT_KEY` was set, creating a directory at the mistaken location and treating the resulting empty vault as a configured deployment.

### Database path and audit key

`ERPDatabase.__init__()` can create parent directories, initialize schema, and generate the initial file-backed audit key. Before the guard, `monitor --db <missing-path>`, `status --db <missing-path>`, and `audit-rotate --db <missing-path> --dry-run` could therefore create state while intending to inspect or simulate an operation.

A related case was an initialized but otherwise empty database whose audit key file had been lost. Since no audit rows existed yet, normal initialization could generate a replacement key. A read-only command must not decide to mint key material.

There was also a broader read-only defect: `ERPDatabase` ran idempotent schema/migration work on construction and opened write-capable connections with `BEGIN IMMEDIATE`. Preflight guards alone do not make inspections read-only for an existing but partially migrated database. The inspection path must use SQLite's read-only URI mode and refuse incompatible schema rather than auto-migrate it.

## 2. Changes committed to the working branch

| Commit | Change |
|---|---|
| `542200bafe52b035800542eae5aee8a94b5ddc97` | Require the vault root to exist before the read-only vault guard accepts it, including when `ERP_VAULT_KEY` is present. |
| `8723275851d7ffdd61b88dad3d582b7a392ec526` | Add a regression test for monitoring with an environment key and a missing vault. |
| `c491d75498cd2320fb13b65b8284700a29d56822` | Add read-only database preflight validation for file existence, minimum ERP schema, and an existing audit-key source; guard database inspection paths. |
| `2dd01dc45bf07bdc2f43ad08e97215a5656bdde0` | Add regression tests for missing/uninitialized databases and missing vaults in `status`. |
| `b86844f04bbee3c64c687ea4ce8edf2d9605e233` | Refuse read-only DB inspection when neither an audit key environment variable, legacy key file, nor key registry exists. |
| `db5b6ae55fde7c0aba2e07fb44f02fbda2132c75` | Add a regression test proving a missing audit key is not regenerated during monitoring an empty initialized DB. |
| `33b45c9c947a66ff1edc82d96292f649bde5bb03` | Require an existing vault/database before key rotate, dry-run, rollback, and removal commands proceed. |
| `7a2da47e012ae76f7b979279ca8277fda96e5a60` | Add parameterized negative-control tests for vault and audit key commands against missing stores. |
| `4ec522bf91a7569fb5dd62f705bab63066992c30` | Add `ERPDatabase(read_only=True)`: skip schema initialization/migrations, use a SQLite `mode=ro` URI, and avoid changing journal mode. |
| `39278d1cc58a600030d66822fdb4ade858440d7b` | Route CLI `monitor` and `status` database inspection through the read-only connection mode. |
| `9cc7ffec338d69d657cfa7723e94b43d16444e09` and `f4d026ddb91589d99049ff0cca19464dfc583bd5` | Validate the audit schema required for verification and correct the schema probe to use a live read-only SQLite connection. |
| `2790fd1561bdf97995d398238c9dfb3a4400b3c3` and `d8babd72e13359c076ad20601dac4f38a8bec4e4` | Block audit-key rotation/rollback/removal and key-registry persistence when a database instance is in read-only mode; retain the method docstring. |
| `3aad70c532193fec0d358da7f6f605a3992babd2`, `796227da28e4a0c67f97bc3a1074f6ad12185402`, and `ed56bd8a8e8eb0e702043099b9a3a5a87a609963` | Add checks for unchanged DB bytes, rejection of schema migration during monitoring, SQLite-enforced write refusal, and no key/ledger/lock side effects from key mutators. |
| `f4d1c203144aa212e42169d2812237dda8399f37` and `053d8f34ba2eeef4de6c6842a4e504d570b4320c` | Validate expected tables, indexes and triggers using `SCHEMA_SQL`; fix the Python regex patterns used for that inventory. |
| `86c025ae120d114dfeee458a3d81f44c502876e7` | Add a regression test that monitoring refuses to report healthy when an integrity trigger or uniqueness index has been removed, without recreating it. |

Implementation uses the project's existing vault, database, key-provider, and CLI design. It introduces no new dependency and does not alter a production store or key.

## 3. Intended invariants

1. `monitor` and `status` never create a missing vault or database.
2. An environment key is not evidence that a vault directory exists at the supplied path.
3. An empty or unrelated SQLite file is not treated as an initialized ERP database.
4. A read-only DB command does not mint an audit key when all key sources are missing.
5. A missing or invalid store causes a non-zero refusal with an actionable diagnostic.
6. `--dry-run`, rollback, and key-removal commands refuse a missing store before invoking constructors that can initialize it.
7. `status` and `monitor` use SQLite-enforced read-only connections; they do not run schema migrations or change journal mode.
8. Incompatible/legacy schema, missing tables, missing integrity triggers, or missing required indexes are reported for separate migration/repair rather than silently altered during inspection.
9. A read-only database instance rejects both SQL writes and audit-key registry mutations before creating journals, ledgers, or locks.
10. Existing data and keys are preserved; recovery of missing key material remains an operator recovery task.

## 4. Regression coverage added

The current feature branch contains tests for:

- Monitoring a missing vault while `ERP_VAULT_KEY` is set.
- Monitoring a missing database and an existing but empty SQLite file without creating schema or key files.
- Refusing to regenerate an audit key that is missing from an existing empty database.
- Refusing `status` on a missing database or vault.
- Refusing vault rotate/dry-run, rollback, and removal operations on a missing vault.
- Refusing audit rotate/dry-run, rollback, and removal operations on a missing database.
- Proving a read-only database instance rejects DDL writes and refuses audit-key rotation, rollback, and removal without changing the DB or creating side-effect files.
- Proving successful `status` and `monitor` leave a correctly initialized database's bytes unchanged.
- Proving an incompatible legacy schema is reported and not automatically migrated during monitoring.
- Proving that removal of an audit immutability trigger or posted-supplier-invoice uniqueness index makes monitoring fail closed without recreating the object.

These tests have been committed but **their execution results are not yet verified in this environment**.

## 5. Required verification before calling this fix complete

Run these checks from a clean checkout of the latest `arena/6c474c64-erp` tip:

```bash
python -m pytest -q tests/test_phase20_checkpoints_and_monitoring.py
python -m ruff check dz_erp/database.py dz_erp/rotate_keys.py tests/test_phase20_checkpoints_and_monitoring.py
python -m compileall -q dz_erp tests tools
python -m pytest -q --cov=dz_erp --cov-report=term-missing
python tools/staging_acceptance.py
```

Then inspect GitHub Actions for the **exact current commit SHA** and confirm all supported Linux Python jobs and the Windows key-rotation/recovery job are green. A historical successful run does not verify these later changes.

The working container could not clone the repository because DNS resolution for `github.com` failed. The available GitHub status response also returned no status entries for the new commit, so this document deliberately does not claim that local tests or CI have passed.

## 6. Release effect

This is security hardening in the authorized feature branch only. It does not authorize merge, deployment, real customer data, production key changes, or a change to the release verdict.

**Verdict remains: `CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY`.**
