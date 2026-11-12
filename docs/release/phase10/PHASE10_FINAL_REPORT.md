# Phase 10 — Final Report

**Release:** Algerian AI ERP Platform
**Branch under test:** `arena/7557bcfb-erp` @ `4fc1364` (shallow clone)
**PR #7:** OPEN, draft, MERGEABLE, head `f603e38` — **not touched** (not merged,
not closed, not cherry-picked, source branch unmodified)
**Report date:** 2026-11-10
**Classification:** **CONDITIONALLY PRODUCTION READY / IMPLEMENTATION VERIFIED**

> This is **not** a `PRODUCTION READY` declaration. Four gates remain unverified
> or failed: expert legal sign-off, public TLS acceptance, Windows desktop GUI
> acceptance, and dependency-advisory remediation. See §6.

---

## 1. Scope delivered in Phase 10

| Requested | Delivered | Path |
| --- | --- | --- |
| Operational readiness (readiness endpoint, DB readiness, OCR health, backup automation, retention, integrity monitoring, log rotation, disk monitoring, failure alerts, recovery runbook) | implemented + drilled | `dz_erp/readiness.py`, `dz_erp/ops_backup.py`, `dz_erp/ops_alerts.py`, `dz_erp/ops_checks.py`, `ops/systemd/*`, `ops/logrotate/dz-erp`, `OPERATIONAL_READINESS_REPORT.md`, `BACKUP_AND_RECOVERY_RUNBOOK.md` |
| Executed backup + restore | executed, 16/16 PASS | `evidence/operational_drill.py`, `evidence/logs/operational_drill.{log,json}` |
| Data-retention policy without invented Algerian periods | written, all periods `PENDING` | `DATA_RETENTION_POLICY.md` |
| OpenCV decision (`-python` vs `-headless`) | decided: headless; no lock change | `DEPENDENCY_REMEDIATION_REPORT.md` |
| Dependency audit | **executed — 143 open advisories found (P0)** | `DEPENDENCY_AUDIT_FINDING.md`, `evidence/logs/pip_audit*` |
| Windows desktop acceptance | procedure + headless pre-checks; GUI `NOT EXECUTED` | `WINDOWS_DESKTOP_ACCEPTANCE.md`, `windows_acceptance.ps1` |
| Public TLS acceptance | procedure only; `NOT EXECUTED` | `PUBLIC_TLS_ACCEPTANCE.md`, `public_tls_validation.sh` |
| Expert compliance package (18 rules + 12 questions) | written, all `PENDING`, zero legal citations | `EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` |
| Repository / evidence verification | written | `REPOSITORY_AND_EVIDENCE_VERIFICATION.md` |
| Phase 9 evidence preserved | verified: 21 docs / 4 scripts / 17 logs | `docs/release/` |

Total Phase 10 documentation: 11 markdown files, 3 executable procedures
(`public_tls_validation.sh`, `windows_acceptance.ps1`, `evidence/*.py`).

## 2. Commands executed, with exit codes

All commands were run in this workspace on 2026-11-10. Logs are under
`docs/release/phase10/evidence/logs/`.

| # | Command | Result | Exit |
| --- | --- | --- | --- |
| 1 | `pytest -q` (full suite, baseline before Phase 10 changes) | `742 passed, 6 skipped, 1 warning in 119.46s` | 0 |
| 2 | `pytest -q` (final gate, after Phase 10) | **`781 passed, 6 skipped, 1 warning in 120.60s`** | **0** |
| 3 | `pytest -q tests/test_phase7_authentication_remediation.py tests/test_phase8_upload_boundary_and_ci.py tests/test_phase62_readiness.py` | `281 passed` | 0 |
| 4 | `pytest -q tests/test_phase62_readiness.py tests/test_phase63_dependency_contract.py` | `39 passed` | 0 |
| 5 | `ruff check dz_erp tests docs/release` | `All checks passed!` | 0 |
| 6 | `python -m compileall -q dz_erp tests` | no output | 0 |
| 7 | `pip check` | `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.` (known, documented) | 1 (expected) |
| 8 | `pip-audit -r requirements.lock.txt --no-deps --disable-pip` | `Found 216 known vulnerabilities in 8 packages` (143 distinct) | **1** |
| 9 | `python evidence/operational_drill.py` | 16/16 checks PASS (backup 0.007 s, restore 0.009 s, sha256 + audit chain + trial balance equal) | 0 |
| 10 | `python evidence/desktop_headless_acceptance.py` | 18 rows: 9 EXECUTED, 1 GAP, 8 NOT EXECUTED (GUI) | 0 |
| 11 | Phase 9 drills re-run from this tree (`db_concurrency_restart.py`, `dr_drill.py`, `performance_probe.py`) | all PASS; 1402.3 rps; OCR neural mean 2.14 s | 0 |
| 12 | `bash -n docs/release/phase10/public_tls_validation.sh`; no-arg run | usage printed | 2 (by design) |
| 13 | Feasibility probe on a candidate lock (§6.4) | `1 failed, 780 passed, 6 skipped in 124.94s` — only failure is the lock-mismatch assertion | 1 |

## 3. Test-suite evidence

```
$ python -m pytest -q
============ 781 passed, 6 skipped, 1 warning in 120.60s (0:02:00) =============
```

- **781 / 742 = +39 tests**, all new: readiness (`test_phase62_readiness.py`),
  backup automation (`test_phase62_backup_automation.py`), dependency contract
  (`test_phase63_dependency_contract.py`), plus additions to the Phase 7 and
  Phase 8 contract suites.
- **6 skips, all justified and enumerated:** 3 PySide6 GUI modules (no
  `libGL.so.1` in this container), 1 Windows junction test, 2 Windows
  reparse-point tests. No test is skipped to hide a failure.
- One pre-existing failure was found and fixed during the gate
  (`test_openapi_contract_no_longer_advertises_identity_headers`, which pinned
  the public-operation set): `GET /readyz` was added to the expected public list
  in both Phase 7 and Phase 8 contract tests, with a comment explaining why.
- Public surface after the change: **4 public operations**
  (`GET /healthz`, `GET /readyz`, `GET /api/v1/upload-policy`,
  `POST /api/v1/auth/token`) and **26 protected operations** (unchanged pin).

## 4. Operational readiness — executed, not asserted

The drill provisions an isolated environment with synthetic data, then performs a
real backup and a real restore:

```
[ PASS ] 1 backup archive created            (0.007 s)
[ PASS ] 2 archive encrypted                 (AES, key from env)
[ PASS ] 3 sha256 manifest matches           (re-hashed after write)
[ PASS ] 4 integrity re-verified on read     (tamper test rejected)
[ PASS ] 5 restore into a fresh directory    (0.009 s)
[ PASS ] 6 restored DB answers queries
[ PASS ] 7 audit hash chain intact after restore
[ PASS ] 8 trial balance identical pre/post restore
...                                       (16/16 PASS overall)
```

Supporting automation shipped under `ops/`: 5 systemd unit/timer pairs
(scheduled encrypted backup, integrity check, log-rotate trigger, disk watch,
failure alerting) plus `ops/logrotate/dz-erp`. Retention policy and the recovery
runbook are written; every statutory period is `PENDING` because no Algerian
legal source has been verified in this session (see §6.1).

## 5. Evidence index

`docs/release/phase10/evidence/`

| File | Contents |
| --- | --- |
| `logs/final_regression_suite.log` | the 781-passed gate run |
| `logs/baseline_suite_before_phase10.log` | the 742-passed pre-phase run |
| `logs/operational_drill.{log,json}` | 16/16 drill result |
| `logs/desktop_headless.{log,json}` | 18-row desktop matrix |
| `logs/pip_audit.log`, `pip_audit_full.json`, `pip_audit_summary.json` | vulnerability audit (§6.4) |
| `logs/pip_check.log`, `ruff_phase10.log`, `ruff_whole_tree.log`, `compileall.log` | hygiene |
| `logs/dr_drill_rerun.log`, `db_concurrency_rerun.log`, `performance_probe_rerun.log` | Phase 9 reproducibility from this tree |
| `dependency_remediation_candidate.lock.txt` | the probed candidate lock (§6.4) — **not applied** |
| `operational_drill.py`, `desktop_headless_acceptance.py` | the executed drills |

## 6. Remaining blockers

### 6.1 Expert legal sign-off — PENDING (blocking production)

`EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` lists 18 rules (VAT-01…07, TIMBRE-01…05,
G50-01…03, ACCOUNT-01/02, DATE-01) and 12 questions, each with rule id,
implementation location, test reference, the exact question to be answered, and
status `PENDING`. **No legal citation is included anywhere**, because no
independently verified Algerian source was consulted in this session. Notable
open item: Q10 — whether goods received but not invoiced belong in `380` or
`408`. Certification remains `PENDING`; nothing here may be read as a compliance
opinion.

### 6.2 Public TLS acceptance — NOT EXECUTED (blocking production)

No public hostname, no publicly-trusted certificate, no public network path in
this environment. `public_tls_validation.sh` implements 13 checks (DNS + public
IP, chain validation, chain completeness, expiry, SAN, HTTP→HTTPS, HSTS,
hostname enforcement, proxy header pass-through, Host allow-list through the
proxy, forwarded-header trust, backend isolation, readiness through the proxy)
and exits 2 without a hostname rather than pretending. See
`PUBLIC_TLS_ACCEPTANCE.md`.

### 6.3 Windows desktop GUI acceptance — NOT EXECUTED (blocking production)

`WindowsDesktop` acceptance requires a real Windows host. `windows_acceptance.ps1`
is the reproducible procedure (9 GUI rows: startup, login/logout, Arabic RTL,
FR/EN switching, session expiry, role navigation, invoice intake, OCR
correction, posting, API connectivity). Executed here: 9 headless/pre-flight
rows only. Additional **GAP**: the desktop i18n layer ships `('fr', 'ar')` only —
there is **no English locale**, so the FR/EN switching row cannot pass as
specified until English strings are added.

### 6.4 Dependency advisories — OPEN (blocking production)

`pip-audit 2.10.1` against the pinned lock reports **216 rows / 143 distinct
advisories across 8 of 50 pins**, in the libraries that parse untrusted supplier
documents:

| Package | Pinned | Distinct | Fix |
| --- | --- | --- | --- |
| pypdf | 5.4.0 | 67 | 6.21.4+ |
| pillow | 11.1.0 | 53 | 13.0.0 |
| starlette | 0.46.2 | 7 | 1.3.1 |
| cryptography | 44.0.2 | 6 | 50.0.0 |
| python-multipart | 0.0.20 | 6 | 0.0.31 |
| pdfminer.six | 20250327 | 2 | 20251230 |
| certifi | 2026.7.22 | 1 | 2026.10.18 |
| pytest (dev-only) | 8.3.5 | 1 | 9.0.3 |

**The lock was deliberately not changed in this phase.** What *was* done is a
reproducible feasibility probe: the candidate lock (9 pins, in
`evidence/dependency_remediation_candidate.lock.txt`) resolves cleanly, and the
full suite on the upgraded stack gives `1 failed, 780 passed, 6 skipped` — the
single failure being the `test_phase19_dependency_integrity` assertion that the
installed versions *do not match the declared lock*, i.e. it fails precisely
because the lock had not been updated. Application compatibility is therefore
demonstrated; the remaining work is lock bookkeeping (two new transitive pins are
required — `annotated_doc` for fastapi ≥ 0.162 and `pygments` if pytest is
bumped), hash regeneration and a dedicated regression review. Full plan and
acceptance criteria: `DEPENDENCY_AUDIT_FINDING.md` §6.

This finding supersedes the Phase 9 statement that the lock had no known
vulnerabilities; the advisory database moved, and today's result is the
authoritative one.

### 6.5 Minor / accepted

- `pip check`: `rapidocr-onnxruntime requires opencv-python` — expected by
  design, explained in `DEPENDENCY_REMEDIATION_REPORT.md` (headless wheel
  supplies the same `cv2`; zero `gl*` symbols referenced by `cv2.abi3.so`).
- 3 pre-existing `F401` findings in vendored `odoo_addons/**/__init__.py` —
  untouched vendor code.
- Baseline divergence: PR #7's tree carries 34 test modules, this branch 64; the
  Phase 9 evidence set is present in full (21 docs / 4 scripts / 17 logs).

## 7. Classification and what would change it

| Candidate classification | Verdict |
| --- | --- |
| `PRODUCTION READY` | **No** — four mandatory gates unverified/failed |
| `CONDITIONALLY PRODUCTION READY` | **Yes** — code, tests, drills and documentation are verified; deployment is gated on the items below |
| `STAGING VERIFIED` | Not claimed — no staging host was provisioned or tested here |
| `IMPLEMENTATION VERIFIED` | **Yes** — the implementation claim holds on the full suite |
| `NOT READY` | Too strong — the functional and operational evidence is green |

Path to `PRODUCTION READY` (all five required):

1. Remediate §6.4 and re-audit to exit 0 (largest item wholly within engineering
   control — needs an owner decision to proceed).
2. Run the 13-check TLS procedure on the real hostname; attach the log (§6.2).
3. Run the Windows GUI matrix on a real Windows host; fix the missing English
   locale (§6.3).
4. Obtain the expert's answers to Q1–Q12 and the 18 rule approvals; replace every
   `PENDING` retention period with a cited statutory value (§6.1).
5. Re-run `pytest -q` and the operational drill on the release commit and attach
   both logs.

## 8. Repository state

- Branch `arena/7557bcfb-erp`, base `4fc1364`. **Nothing was committed, pushed,
  merged or cherry-picked** during Phase 10. PR #7 remains OPEN/draft/MERGEABLE
  at `f603e38`, unmodified.
- Working tree: 6 modified tracked files (`dz_erp/api.py`,
  `dz_erp/decimal_utils.py`, `dz_erp/procurement_stock.py`,
  `dz_erp/security_audit.py`, `tests/test_phase7_authentication_remediation.py`,
  `tests/test_phase8_upload_boundary_and_ci.py`), plus new `dz_erp/readiness.py`,
  `dz_erp/ops_backup.py`, `dz_erp/ops_alerts.py`, `dz_erp/ops_checks.py` and the
  untracked `docs/release/`, `docs/security/` trees.

---

**Prepared by:** Arena agent (implementation, execution, evidence collection)
**Verified by:** `PENDING` — the evidence is reproducible from the commands in §2
**Approved for production by:** `PENDING`

---

## Addendum — P0 dependency remediation executed (2026-11-10)

**Everything above is preserved as the historical record of the Phase 10
assessment.** This addendum records what changed afterwards, and must be read as
the current state.

### What was closed

The §6.4 blocker was remediated under explicit authorisation. Ten pins were
raised, two transitive pins added, and the hash lock regenerated:

| | Before | After |
| --- | --- | --- |
| `pip-audit` distinct advisories | 143 (8 packages) | **0** |
| `pip-audit` exit code | 1 | **0** |
| Locked distributions / hashes | 51 / 1 277 | **53 / 1 317** |
| Functional failures attributable to the upgrade | — | **0** (failure set on this interpreter is identical before and after) |

Full detail, commands and evidence: `DEPENDENCY_REMEDIATION_REPORT.md`.
The original finding, preserved unedited with a status banner:
`DEPENDENCY_AUDIT_FINDING.md`. The OpenCV packaging decision moved to
`OPENCV_WHEEL_DECISION.md` (same content, new name — the old path now carries
the remediation report).

### What the suite shows now

```
22 failed, 799 passed, 6 skipped, 1 warning in 172.64s      # clean env, new lock, Python 3.11.2
```

Against **759 passed / 22 failed / 6 skipped** before the remediation on the
same interpreter, with an **empty set difference** between the two failure
lists. All 22 are the project's own runtime guard —
`Safe backup restore requires Python 3.11.17+ or Python 3.12.10+` — because this
sandbox only has CPython 3.11.2 and cannot obtain a supported build (GitHub
release assets are outside the sandbox egress allow-list; no dev headers for a
source build). The +40 new tests are
`tests/test_phase64_dependency_security_regression.py`.

### Classification after the addendum

- Dependency remediation: **VERIFIED** (P0 gate closed; one sub-gate — full suite
  on a supported interpreter — **NOT EXECUTED** here and required before release).
- Overall release: **unchanged — CONDITIONALLY PRODUCTION READY /
  IMPLEMENTATION VERIFIED.** The other gates in §6 are untouched by this work:
  expert sign-off (PENDING), public TLS (NOT EXECUTED), Windows GUI + English
  locale (NOT EXECUTED/GAP), and the supported-runtime suite run.

### Git state

`HEAD` remains `4fc1364` on `arena/7557bcfb-erp`. Nothing was committed, pushed,
merged or rebased; PR #7 is still OPEN at `f603e38`, unmodified.
