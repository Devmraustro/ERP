# ERP Operational Readiness Report — Phase 59

**Feature freeze:** in force for the whole phase. No module, screen, workflow or AI capability was
added. Every code change below is a defect repair traced to a failed acceptance check.

---

## 1. Exact repository, branch and HEAD

| | |
|---|---|
| Repository | `https://github.com/Devmraustro/ERP.git` |
| Branch | `arena/63d762c8-erp` |
| Base commit of the branch | `098483a1f02df0308f3889c93714efab5d13b550` |
| Phase-59 work | committed on top of that base on this branch (§11–§12) |
| Pull request | [#9](https://github.com/Devmraustro/ERP/pull/9) — OPEN |

The evidence in every phase-59 document was collected at the base commit **plus** the phase-59
changes described in §2, on this branch. No document cites a commit it did not run against.

## 2. Feature-freeze confirmation

Every change is listed with the defect that forced it. There is no change without one.

| # | Change | Defect it repairs |
|---|---|---|
| 1 | `dz_erp/serve.py` — `_bootstrap_admin` loads the SCF chart; credential validated before any DB write; audit-aware compensation | A bootstrapped deployment had **0 accounts**; no invoice could ever post |
| 2 | `dz_erp/intake_ocr.py` — a corrected transcription updates the existing header row instead of inserting a duplicate | Correcting an unread field created a duplicate-evidence group that blocked approval |
| 3 | `dz_erp/desktop/views_invoices.py` — `_policy` initialised (instance + class default); blockers banner; `missing_hint` | `AttributeError` mid-dialog; an operator could not see *why* submission was refused |
| 4 | `dz_erp/ops_backup.py` — `verify` fails closed when the manifest is missing | An unverifiable artefact reported `ok: true` |
| 5 | `requirements-desktop.lock.txt` + `.hashes.txt` + generator `--desktop` | Two documented install procedures referenced a file that **did not exist** |
| 6 | `.github/workflows/ci.yml` — `verify-desktop` job | All Qt tests **skipped** in CI; green by omission |
| 7 | `dz_erp/net.py`, `serve.py` — `ERP_ALLOWED_HOSTS`, host allow-list, unsafe-bind refusal | A LAN deployment answered `400` while `/healthz` said `200` |
| 8 | `dz_erp/api.py`, `views_masterdata.py` — partner/product creation | The desktop had no way to enter the master data every workflow needs |
| 9 | 6 regression tests + 4 pin updates | So the above cannot silently regress |

## 3. Clean install and startup — `INSTALLATION_AND_STARTUP_ACCEPTANCE.md`

* Clean venv install from the hash lockfiles: **PASS** (53 runtime + 4 desktop pins).
* Bootstrap → **294 accounts, 68 groups, 8 taxes**; `/healthz` `200`, `/readyz` `200`,
  `table_count 26`, vault key `0o600`.
* Required-path enforcement: start-up **refuses** without `ERP_DB_PATH` / `ERP_VAULT_PATH`.
* Bootstrap idempotence: second run refuses with "this database is already bootstrapped".
* **Windows: NOT EXECUTED** (no host). Procedure recorded; CI's `verify-windows` job covers the
  platform-sensitive surface on 3.12.10.
* **CPython 3.12.10: NOT EXECUTED here** (unobtainable, §5).

## 4. End-to-end operational acceptance — `END_TO_END_OPERATIONAL_ACCEPTANCE.md`

Executed against a live server over HTTP with synthetic data:

* master data with real Algerian identifier validation (NIF/RC/AI/NIS), 4 refusal paths proven;
* invoice validated → **staged, not posted**;
* unbalanced totals and duplicate invoice numbers → persisted as **`NeedsReview` / `DuplicateRejected`**
  and **refused at posting with `409`**;
* approval blocked by **`SEGREGATION_OF_DUTIES_VIOLATION`** for the creator;
* posting produced `ACH/2026/00002`: **debit 11 900,00 = credit 11 900,00** on accounts
  `380` / `4456` / `401`;
* trial balance balanced, rows are the posted entry;
* reversal produced a credit note **and** `RAVOIR/2026/00002`, original untouched;
* **24 audit events**, including the refusals;
* backup created; **deep verify blocked by the Python floor** (§5).

**Not covered by this walk:** the sales cycle and stock movements over HTTP (covered by the automated
suite, not re-walked), and the 14-step OCR E2E (floor-blocked).

## 5. The one environmental fact that limits this report

This sandbox has **CPython 3.11.2**. The restore path requires **3.11.17+ / 3.12.10+** and refuses to
run below that. Consequences, all stated as NOT EXECUTED rather than PASS:

* **22 of 1,037 tests fail, all with that exact guard** (`FinancialIntegrityError` /
  `deep: "restore failed: FinancialIntegrityError"`).
* Staging acceptance is **38/40**; both failures are the same guard.
* Restore, deep verify, key-migration and the 14-step E2E were **not** exercised here.

Python 3.12.10 could not be obtained: GitHub **release assets** are unreachable (curl exit 35), no apt
mirrors, and no build headers for a source build. CI runs 3.11/3.12/3.12.10 and is the authoritative
run for those 22.

**Full suite in this sandbox: 22 failed, 1012 passed, 3 skipped.**
The 3 skips are declared Windows-only tests.

## 6. Accounting and Algerian compliance — `ACCOUNTING_AND_COMPLIANCE_READINESS.md`

* Chart, VAT (19/9/0), stamp duty, rounding (`ROUND_HALF_UP`, `Decimal` only), balanced entries,
  reversal and trial balance are implemented and tested.
* Stamp duty observed live: threshold `300,00` inclusive, floor `5,00`, brackets 1 % / 1,5 % / 2 %,
  **cash only**, base = TTC.
* **`G5` is BLOCKED. `Q1`–`Q12` are PENDING and reproduced unchanged.** No legal approval is claimed,
  no question is answered, and no test result is presented as a legal opinion.
* `TVA` rates, timbre brackets, G50 line mapping and the `380`/`408` GR/IR choice are **all** still
  subject to expert determination.

## 7. Security and reliability — `SECURITY_AND_RELIABILITY_ACCEPTANCE.md`

| Control | Result |
|---|---|
| Anonymous access | `401` |
| Identity server-side only | no `X-User-Id` header in the OpenAPI contract |
| Session revocation | works; re-use → `401 Authentication credential has been revoked.` |
| Login lockout | 5 failures → 15-minute lock, audited |
| RBAC | non-admin user administration → `403`; wrong role posting → `403` |
| Segregation of duties | creator approval → `403` |
| Host allow-list | unknown `Host` → `400`; configurable; unsafe bind **refuses to start** |
| Docs surface | `/docs` and `/openapi.json` → `404` |
| Secrets in logs | issued token and password **absent** |
| `pip-audit` | **0 known vulnerabilities** |
| Security suites | 247 passed |

**Operational caveat recorded:** lockout is per account, so an unauthenticated attacker can lock the
only admin out for 15 minutes. Mitigation documented (second admin account); behaviour unchanged
because the control is deliberate.

## 8. Backup and recovery — `DATA_PROTECTION_AND_RECOVERY_ACCEPTANCE.md`

* Archive is **Fernet-encrypted** (`gAAAAA…`, no SQLite/tar header), archive + manifest + status all
  `0600`.
* Manifest carries digests and key-version descriptors, **no key material, no customer data**.
* One flipped byte → `ok: false`.
* **Defect fixed:** an artefact with **no manifest** reported `ok: true`; it now fails closed with
  `MANIFEST_MISSING`, with a regression test.
* **Restore and deep verify: NOT EXECUTED** (floor). No RPO/RTO figure is quoted anywhere — none was
  measured.

## 9. Windows

**NOT EXECUTED.** No Windows host or runner in this session. The documented procedure is corrected
(the desktop lockfile now exists and the flags are right), and CI's `verify-windows` job runs the
platform-sensitive tests on 3.12.10. Every Windows-specific claim in this report is a *procedure*, not
a result.

## 10. Windows UAT / user acceptance — `USER_ACCEPTANCE_REPORT.md`

* **55 desktop tests, 0 skipped, 0 failed** (real PySide6 6.11.2, offscreen).
* i18n parity **286 / 286 / 286** across fr/ar/en; Arabic switches layout to RTL.
* **Two defects fixed:** duplicated evidence row on correction; `_policy` `AttributeError`.
* **NOT VERIFIED:** real display rendering, Arabic shaping on screen, High-DPI scaling, keyboard-only
  navigation, screen-reader use, drag-resize. No screenshots exist and none are fabricated.

## 11. Documentation and installer — `DOCUMENTATION_ACCEPTANCE.md`

* `USER_GUIDE_FR.md`, `USER_GUIDE_AR.md`, `ADMINISTRATION_AND_RECOVERY.md` written **against shipped
  strings** and executed behaviour; a table in the acceptance record maps each claim to its source.
* Features that do **not** exist are explicitly declared absent (withholding tax, Law 18-07 rights
  workflow, automatic G50 filing, customer invoicing).
* Two documented commands could not be executed (restore paths) and are **labelled in place**.
* No installer/MSI exists; the declared distribution is wheel + venv, and the Windows procedure is
  unexecuted.
* Arabic/French guides have **not** had native-speaker review.

## 12. Clean build and regression — `FINAL_REGRESSION_AND_RELEASE_CANDIDATE.md`

| Gate | Result |
|---|---|
| `ruff check dz_erp tests tools` | **PASS** |
| `compileall` | **PASS** |
| `verify_dependency_graph.py` | **PASS** |
| `generate_hash_lock.py --check` | **PASS** — 53 pins / 1 317 hashes |
| `generate_hash_lock.py --desktop --check` | **PASS** — 4 pins / 20 hashes |
| `pip-audit` | **PASS** — 0 advisories |
| `pip check` | 1 documented substitution (`opencv-python` → headless) |
| Full suite | **22 failed / 1012 passed / 3 skipped** — all 22 are the Python floor |
| Staging acceptance | **38 / 40** — same floor |
| Desktop suites | **55 passed / 0 skipped** |

## 13. CI on the exact candidate SHA

See §15. The authoritative result for the 22 floor-blocked tests is the CI run on
this branch's head commit, on CPython 3.11/3.12 and Windows 3.12.10. This report does **not**
claim CI passed; it states where the result is.

## 14. Pull request and merge

* **PR #9** — `OPEN`, head `arena/63d762c8-erp`, **MERGEABLE**.
* Branch protection and required reviews were **not** bypassed, and **no force-push** was used.
* **Merge decision: NOT MERGED.** Two gates block it (`G5`, `G6`), and the merge rule this mission
  sets is "merge only if the rules and gates permit". They do not. See §16.

## 15. Gate status `G1`–`G6`

| Gate | Status | Basis |
|---|---|---|
| `G1` Build & test integrity | **PASS** | ruff, compileall, dependency graph, lockfiles, `pip-audit` all green; the only reds are the interpreter floor |
| `G2` Accounting integrity (software behaviour) | **PASS** | balanced entries, immutability triggers, reversal by credit note, trial-balance parity |
| `G3` Security controls | **PASS** | §7, 247 security tests, live probes |
| `G4` Data protection (software behaviour) | **PASS with a caveat** | encryption, manifest, tamper detection, fail-closed fix; restore NOT EXECUTED here |
| `G5` Algerian expert sign-off | **BLOCKED** | `Q1`–`Q12` PENDING; no review engaged |
| `G6` Production infrastructure | **BLOCKED** | no host, domain, TLS, proxy, KMS/HSM, offsite storage or monitoring exists |

## 16. Production infrastructure status (`G6`)

**BLOCKED.** Nothing was provisioned — no paid resource was created and no credential was requested.
Verified as **absent**, not as failing: no host, no domain, no certificate, no reverse proxy, no
secrets manager, no offsite backup target, no monitoring/alerting, no recovery access, no
deploy/rollback pipeline. The vendor connector for Vercel is available in this conversation but is
**not** a suitable host for this application: it is SQLite + a POSIX-mode vault on local disk, which a
serverless platform cannot satisfy. Deploying there would be a defect, not a deployment.

Runbook: `docs/release/phase56/PRODUCTION_DEPLOYMENT_RUNBOOK.md`.

## 17. Owner actions to reach a higher classification

Ordered, and none of them is something this session can do:

1. **Engage a licensed Algerian `Expert-Comptable` / `Fiscaliste`.** Hand over
   `docs/release/phase55/EXPERT_APPROVAL_PACKAGE.md`. Obtain written determinations on `Q1`–`Q12`.
   Apply any resulting parameter changes and re-run the suite. **This alone unblocks `G5`.**
2. **Provide production infrastructure** (host inside Algeria, domain, TLS, proxy, secrets manager,
   KMS/HSM if key custody requires it, offsite encrypted backups, monitoring). Then run the deployment
   runbook and a real restore drill. **This alone unblocks `G6`.**
3. **Run the suite on CPython 3.12.10** (or 3.11.17+) and confirm the 22 restore-path tests pass — the
   only thing standing between "22 red" and "0 red" here.
4. **Windows UAT**: install, log in, switch to Arabic, review and correct a field, submit, approve as a
   second user, post, reverse — and **screenshot each step**. Also check High-DPI at 150 %.
5. **Decide the GR/IR account** (`380` vs `408`) with the expert and, if needed, change the product
   default — currently `380`.
6. **Keep two administrative accounts** and set `ERP_PASSWORD_MIN_LENGTH=12`.
7. **Take one backup with `ERP_BACKUP_KEY_FILE` set, then a deep verify on a supported interpreter.**
8. **Do not describe this system as tax-compliant or production-ready** until 1 and 2 are closed.

## 18. Final classification

# `INTEGRATION COMPLETE — RELEASE BLOCKED`

**Why not higher.** Three independent blockers, none self-closable:
`G5` (no Algerian expert sign-off; `Q1`–`Q12` PENDING), `G6` (no production infrastructure), and a
restore path unverified in this environment (22 tests red on the interpreter floor, not on a defect).

**Why not lower.** No acceptance failure was found in the software's own behaviour. Every workflow that
could run, ran, and the defects found along the way (empty chart on bootstrap, duplicate OCR evidence
row, dialog crash, fail-open backup verification, a missing documented lockfile, Qt tests skipping in
CI) are fixed, pinned by regression tests, and documented.

**What would change the classification.** `G5` + `G6` closed and restore verified on a supported
interpreter → `PRODUCTION READY — VERIFIED` becomes arguable, and only then. Closing `G5` and `G6`
while restore remains unverified yields `STAGING READY — PRODUCTION INFRASTRUCTURE PENDING` at best.

**This classification is not a legal opinion, not a certification, and not approval by any Algerian
authority or professional.**
