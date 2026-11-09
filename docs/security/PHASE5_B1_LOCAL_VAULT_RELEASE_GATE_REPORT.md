# Phase 5 — B1, Local File Vault verification and the release gate

**Status line for the release:**

```
B1 OPEN — SECURITY TREE VERIFIED ON CPython 3.11.2, RESTORE MATRIX NOT EXECUTED,
COMMIT AND PUSH WITHHELD
```

Date: 2026-11-09 · Author: automated verification pass · Scope: verification only,
no new product code was written in this phase.

This phase was asked to do five things: close environment blocker **B1** (a
supported Python interpreter so the restore matrix can run), verify the
**Local File Vault**, re-verify the **SEC-13/14/15** closures, produce a green
**quality gate** record, and then **commit and push** the integrated security
work to PR #7's head branch.

Four of the five were done. **B1 could not be closed**: this sandbox cannot
obtain a Python 3.11.17+ or 3.12.10+ interpreter (§3). Because the push was
authorised *conditional on the mandatory gates passing*, and the restore matrix
is one of those gates, **nothing was committed and nothing was pushed** (§11).
The verified tree is ready and needs exactly one command per step once an
interpreter is available.

---

## 1. The tree that was verified

| Item | Value |
| :--- | :--- |
| Base commit | `5a3e47ddfba2b789bcff71d6438db4927f5c7986` (head of PR #7, `arena/7f12d627-erp`) |
| Source of the checkout | fresh `git clone https://github.com/Devmraustro/ERP` — the clone re-resolved to the same commit, so the remote has not moved |
| Cumulative security patch | `phase4_closure/full-security-state-from-5a3e47dd.patch`, `sha256 a5d04b8e1165d1b6a76c8bb11cd16f2532e23f65517f7cd4078fceaaf6f05a57`, applied with `git apply --check` clean |
| New acceptance tests | `test_phase58_security_selective_integration.py` `527bcc99…92837`, `test_phase59_sec13_14_15_closure.py` `71587462…310ec` — both match the checksums recorded in `phase3_blocker_closure/` and `phase4_closure/` |
| Resulting work tree | 10 tracked files modified + 2 tracked test files adapted (§9) + 2 new test files; `1057` insertions / `40` deletions across the 12 tracked files, plus `1510` lines of new tests |
| Documentation carried in the tree | `docs/security/` (untracked at this base): the Phase-1…5 reports, `SECURITY_RESIDUAL_RISKS.md` updated for Phase 4 closures and B1 (§10), and this report with `phase5_release_gate/` |
| Commits in the tree | **none** — deliberately left uncommitted so the push remains a single reviewed action |

Both phase-test checksums matching the preserved copies is what makes this tree
the *same* tree that produced the Phase-3 and Phase-4 evidence, rather than a
look-alike.

## 2. Gate results

| # | Gate | Result | Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Full suite on a supported interpreter | **BLOCKED — B1** (§3) | `logs/full_suite_311.xml` |
| 2 | Restore matrix / key-rotation restore / Vault backup-restore E2E | **NOT EXECUTED — B1** (§3) | `logs/restore_failure_reasons.log` |
| 3 | Targeted security suites (SEC-13/14/15, vault, auth, RBAC, deployment) | **PASS** — 335 passed, 4 skipped | `logs/targeted_suites.log` |
| 4 | Local File Vault (encryption at rest, permissions, atomicity, isolation, rotation) | **PASS** (§5) | `logs/vault_probe.log` |
| 5 | Audit-log redaction and durable sink | **PASS** (§5.4) | `logs/redaction_probe.log` |
| 6 | API / auth / Host-boundary smoke | **PASS** (§6) | `logs/api_smoke.log` |
| 7 | Dependency graph | **PASS** — 56 distributions, 1304 pinned hashes | `logs/quality_gates.log` |
| 8 | `ruff` | **PASS** — all checks passed | `logs/quality_gates.log` |
| 9 | Byte-compilation of `dz_erp`, `tests`, `tools` | **PASS** | `logs/quality_gates.log` |
| 10 | Secret scan (source, keys, hardcoded credentials) | **PASS** | `logs/secret_scan.log` |
| 11 | Packaging build | **FAILS on the verified tree (pre-existing B2)**; builds with the separate Phase-3 patch (§8) | `logs/packaging.log` |
| 12 | No weakened assertions, no added skips | **PASS** (§9) | `logs/tree_consistency.log` |

Gate 1 is the only gate that blocks release. Gates 2 and 11 are recorded as
open blockers, not as passes.

## 3. B1 — why the restore matrix still cannot run

### 3.1 The gate itself (it is not a test artefact, it is product behaviour)

`dz_erp.security_audit` refuses to restore a backup on an interpreter that lacks
the archive-handling security fixes:

```python
SAFE_RESTORE_MINIMUMS = {(3, 11): (3, 11, 17), (3, 12): (3, 12, 10)}
SAFE_RESTORE_MAX_EXCLUSIVE = (3, 13, 0)
```

Measured behaviour (`logs/interpreter_gate.log`):

| Interpreter | `safe_restore_supported` |
| :--- | :--- |
| 3.11.2 | `False` |
| 3.11.16 | `False` |
| 3.11.17 | `True` |
| 3.12.9 | `False` |
| 3.12.10 | `True` |

Message raised on refusal:

> `Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13); unsafe archive member types and paths are rejected.`

This is the correct behaviour: the affected archive-member handling is a
CPython-level fix, so a restore on 3.11.2 would be genuinely unsafe. The gate is
therefore **verified working**, and simultaneously the reason the matrix cannot
execute here.

### 3.2 The 20 failures are the gate, not defects

The full suite was run with the failing set re-run under `--tb=line` to attribute
every failure instead of assuming it (`logs/restore_failure_reasons.log`). All 20
failures carry the same root cause, raised at `dz_erp/security_audit.py:1368`:

```
dz_erp.database.FinancialIntegrityError: Safe backup restore requires
Python 3.11.17+ or Python 3.12.10+ (and below 3.13); unsafe archive member
types and paths are rejected.
```

Two of the phase-12 cases report the gate through an assertion on the message
text instead of the exception type; same cause, same line. They are gated
**by design** and are not fixable by editing code — only by running on a
supported interpreter.

Distribution: phase 11 (3), phase 12 (10), phase 5 (1), phase 6 (1), phase 8 (2),
phase 9 (3).

### 3.3 Why no suitable interpreter could be obtained here

| Route | Outcome |
| :--- | :--- |
| `uv python install 3.12` (installed `uv` 0.13.7 into an isolated venv, as authorised) | `client error (Connect) invalid peer certificate: UnknownIssuer` — the download host `release-assets.githubusercontent.com` is TLS-intercepted by the sandbox egress policy |
| `uv python install --native-tls` | `tls handshake eof` — same block, different failure surface |
| Direct `curl` of the same asset | `OpenSSL SSL_connect: SSL_ERROR_SYSCALL` |
| `python.org` / Debian mirror (`deb.debian.org`) | unreachable (`000`) → no `apt` route, no `--enable-optimizations` source tarball |
| CPython source build from any locally available tree | impossible: `pkg-config` and all dev headers (`zlib`, `openssl`, `sqlite3`, `ffi`, `lzma`, `bzlib`, `readline`, `ncurses`) are absent; only `gcc 12.2.0`/`make`/`ld` are present |
| `pypi.org` | reachable — but no PyPI wheel ships a real CPython runtime (`python-bin` 0.0.3 is a 3.5 kB decoy). Installing an interpreter from an unreviewed package would be a supply-chain risk, so it was **not** done |

`uv python list --only-installed` shows only `cpython-3.11.2`. Everything else in
the run — the lockfile-resolution with `--require-hashes`, the venv, the suite —
used that 3.11.2 venv.

### 3.4 What closes B1

Any one of:

1. allow-list `release-assets.githubusercontent.com` (and
   `objects.githubusercontent.com`) for this sandbox — then
   `uv python install 3.12` succeeds and gate 1/2 run unchanged;
2. provide a base image with CPython 3.12.10+, or 3.11.17+;
3. accept B1 as an open blocker and run the matrix in repository CI, which
   already targets 3.11/3.12, before promotion.

## 4. Suite results

| Run | Result |
| :--- | :--- |
| Full suite, CPython 3.11.2, hash-pinned lockfile | **20 failed / 1340 passed / 10 skipped** in 245 s (`logs/full_suite_311.xml`) — identical to the historical baseline for this interpreter; no new failure |
| Failing set re-run for attribution | 20 failed / 274 passed / 1 skipped in 81 s, all 20 traced to the gate (§3.2) |
| Targeted security suites | **335 passed / 4 skipped** in 61 s (`logs/targeted_suites.log`): phase 59 (SEC-13/14/15, 45 tests), phase 58, phase 10 vault, phase 7 auth, phase 28 user admin, phase 37 deployment assets, phase 39 rate limiting, phase 48 interpreter matrix, phase 13 key-rotation regression, phase 17 key providers |

The 10 skips are unchanged from the baseline and none was added by this work
(`logs/skipped_tests.txt`): two whole desktop-UI modules (no PySide6), three
Windows-only path/handle cases, and five restore-integrity scenarios that
self-skip because the interpreter is below the floor — among them
`test_phase48_restore_interpreter_matrix::test_a_real_backup_restore_round_trip_is_verified_on_this_interpreter`
and `test_phase58_security_selective_integration::test_backup_round_trips_and_verifies_the_chain`.
Those two are **not** evidence of anything having passed; they are part of B1.

## 5. Local File Vault — verification

### 5.1 Runtime probe (`logs/vault_probe.log`, probes in `phase5_release_gate/probes/`)

| Property | Measured |
| :--- | :--- |
| Blob permissions | `0600` |
| Key-file permissions | `0600` (created owner-only, never tightened afterwards) |
| Blob directory / vault root permissions | `0755` — already recorded in `SECURITY_RESIDUAL_RISKS.md` §4; ciphertext and key material stay unreadable, but directory structure and per-company existence are listable |
| Encryption at rest | plaintext marker absent from the stored blob; blob begins with the versioned header `DZKV` |
| Atomic write / crash safety | no partial or temporary files left behind after 17 successful stores |
| Tenant isolation | cross-company read raises `TenantIsolationError` |
| Tamper detection | one flipped ciphertext byte → `InvalidToken`, never wrong plaintext |
| Integrity sweep | `verify_blobs()` → `{checked: 16, failed: 0, by_version: {2: 16}}` after rotation |
| Concurrency | 16 concurrent stores from 16 threads → 16 distinct blobs, 0 errors, all readable |
| Key rotation | `rotate_key()` `v1 → v2`: all 16 blobs re-encrypted and re-headered, all still readable, journal and keyring written beside the vault root |
| Permission denied | read-only vault root → `PermissionError` (fail closed, no silent data loss) |
| Disk-full | **not executed** — the sandbox has no way to mount a small filesystem or apply a quota to a normal user; recorded as unverified rather than inferred |

### 5.2 Code review points that the probe cannot prove

* `store_bytes` writes atomically and then re-reads the active key version; if a
  rotation completed between the two, the blob is re-encrypted so a document can
  never be stranded on a retired key.
* `read_bytes` resolves the path and refuses anything outside
  `<vault>/company_<id>/`, so a poisoned `vault_path` (or a vault moved to a new
  directory) cannot be used to read arbitrary files.
* A missing historical key raises `VaultKeyUnavailableError` rather than
  "decrypting" with whatever key is active — a wrong key must never look like a
  successful read.
* Broken blob headers are skipped by `scan_blob_versions()` and reported by
  `verify_blobs()`; a corrupt blob cannot stall service start-up, and cannot be
  re-interpreted as another key version.

### 5.3 Restore path

Vault backup/restore through the encrypted chain is gated by the same interpreter
check as the rest of the archive work, so its end-to-end path is **unexecuted**
here (B1). What *is* verified is the vault's own write/read/rotate behaviour on a
supported-permission filesystem, and the archive-safety code paths that the
phase-12 tests exercise up to the gate.

### 5.4 Audit log redaction (independent of the failing phase-5 test)

`test_phase5_…audit_immutability_log_redaction_and_encrypted_backup_restore` is
one of the 20 gated failures, so redaction was verified separately instead of
being reported as "covered by a passing test" (`logs/redaction_probe.log`):

* durable sink file created `0600`;
* `password`, `token`, `authorization`, `bank_rib` and `raw_text` values →
  `[REDACTED]`;
* a 18-digit NIN and a `DZ…` IBAN inside a free-text note → pattern-redacted;
* the record itself survives with its operational fields (`event`,
  `correlation_id`, `username`, `client_ip`).

## 6. SEC-13 / SEC-14 / SEC-15 re-verified on this tree

Behavioural probe (`logs/api_smoke.log`) against an app built with
`create_erp_fastapi_app` and a bootstrap admin:

| Probe | Result |
| :--- | :--- |
| `/healthz` | `200 {"status":"ok","service":"algerian-ai-erp"}` |
| `/openapi.json` | `404` (schema not exposed) |
| `GET /api/v1/users` without a token | `401` |
| `POST /api/v1/auth/token` valid credentials | `200`, `dzsess_…` session token |
| `POST /api/v1/auth/token` wrong password | `401` |
| `GET /api/v1/auth/session` with token | `200` |
| `POST /api/v1/auth/password` with a 5-character new password (**SEC-13**) | `422` |
| `GET /api/v1/system/rate-limit-policy` | `200`; host allow-list `['localhost','127.0.0.1','[::1]','testserver']` (**SEC-15**) |
| `GET /healthz` with `Host: evil.example` (**SEC-15**) | `400` |
| `GET /api/v1/upload-policy` (public by design) | `200` |

Plus the phase-59 acceptance suite: 45 tests, all passing, covering the policy
chokepoint, the rotation/reset endpoints, rehash-on-login, the OCR override
validation (**SEC-14**) and the proxy-header/Host boundary.

## 7. Static and quality gates

| Check | Result |
| :--- | :--- |
| `ruff check dz_erp tests tools` | `All checks passed!` |
| `python -m compileall dz_erp tests tools` | OK |
| `tools/verify_dependency_graph.py` | OK — 56 installed distributions match the declared graph; only deviation is the documented `opencv-python → opencv-python-headless` substitution |
| Lock consistency | 56 pinned requirements, 1304 hashes in `requirements.lock.hashes.txt` |
| Forbidden cloud SDKs in code | none — no `boto3`/`botocore`/MinIO client imported in runtime code or pinned in requirements |
| Committed key material | none (no `BEGIN … PRIVATE KEY` in the repository) |
| Hardcoded credentials in `dz_erp` | none matching the credential literal patterns |

One documentation, not code, inconsistency is worth recording: `docs/DISASTER_RECOVERY.md`
describes S3-compatible off-host storage (MinIO/R2/B2) as an *optional* path that
would require `boto3`. No such dependency exists and none is installed; the
document is describing an operator-side integration, but a reader could take it
as shipped functionality. It is listed in §10 rather than silently rewritten,
because rewriting the DR document is outside this phase's authorisation.

## 8. Packaging (B2, unchanged and disclosed)

`pip wheel .` on the verified tree fails before any file is built:

```
error: Multiple top-level packages discovered in a flat-layout:
['ops', 'dz_erp', 'vendor', 'odoo_addons']
```

This is pre-existing B2 and is **not** part of the security change set. With the
separate, preserved `phase3_blocker_closure/packaging-flat-layout.patch`
(`sha256 a73eacc3…41c4d`) applied to a *scratch copy* of the same tree, the build
succeeds:

* wheel: `algerian_ai_erp-0.1.0-py3-none-any.phase5-rebuild.whl`, 683 kB, 44 entries;
* only one importable top-level package: `dz_erp` (+ `dist-info`);
* bundled assets travel with the package: `dz_erp/assets/fonts/DejaVuSans.ttf` and its copyright file.

Two disclosures. First, a wheel is not byte-reproducible (zip timestamps), so
this rebuild's hash `973924d5…ef9b1b` does **not** equal the hash recorded for the
Phase-3 wheel, `e0136753…f90bc1`; the Phase-3 artefact itself is still missing
from `phase3_blocker_closure/` and its `SHA256SUMS` entry therefore still fails.
The rebuild is evidence that the patch works, not a recovery of that file.
Second, the wheel is a packaging artefact, **not** a deployable standalone
product: it contains no interpreter, no service unit, no configuration and no
database migration; deployment is still the documented source-tree/ops route.

## 9. Test-file changes, and the no-weakening check

Two pre-existing test files are touched by the change set. Both changes are
mechanical consequences of the new behaviour and are disclosed in full:

| File | Change | Why it is not a weakening |
| :--- | :--- | :--- |
| `tests/test_phase7_authentication_remediation.py` | two fixture passwords `"P#2026x"` (7 chars) → `"Prov#2026x"` (9 chars) | SEC-13 enforces an 8-character floor; the old literals are now *correctly* rejected. The assertions they feed (`create_user` succeeds; unknown role raises) are unchanged |
| `tests/test_phase8_upload_boundary_and_ci.py` | protected-operation count pin `33` → `35`, with a comment naming the two new SEC-13 endpoints | the pin stays an equality check on the exact set of protected operations; the added comment explains the delta. No assertion became `>=` and no case stopped being checked |

Mechanical checks over the cumulative patch
(`logs/tree_consistency.log`):

* removed `assert` lines: **1** — the `33` count pin above, replaced by the same
  equality assertion with the new exact number;
* added `pytest.skip` / `pytest.mark.skip` lines: **0**;
* total skips in the full run: **10**, identical to the pre-change baseline.

## 10. What remains open

| ID | Item | State after this phase |
| :--- | :--- | :--- |
| B1 | Supported interpreter for the restore matrix | **OPEN** — blocked by sandbox egress (§3). Options in §3.4 |
| B2 | Flat-layout packaging build | **OPEN (fix preserved, not applied)** — §8 |
| B3 | Staging host / container / volume | **OPEN** — never reachable from this environment; staging acceptance procedure still unexecuted |
| B4 | Live AWS KMS / HSM | **OPEN by decision** — out of V1 scope; build refuses `ERP_KEY_PROVIDER=kms|hsm` |
| — | Vault directories `0755` | **Recorded** (not new): same observation as `SECURITY_RESIDUAL_RISKS.md` §4; blobs and keys are `0600`. Tightening to `0750`/`0700` is a hardening option for shared hosts |
| — | `DISASTER_RECOVERY.md` S3/MinIO paragraph | **Recorded**: documentation describes an optional operator integration that does not exist in code; no dependency shipped |
| — | `tests/test_phase34_security_validation.py` | exists only in the session workspace as an untracked file, **not** in the cumulative patch, so it was not part of this verification run |
| — | `/home/user/integration-preservation/` | **absent** in this sandbox; only the in-repository copies under `docs/security/phase3_blocker_closure/` and `phase4_closure/` are verifiable, and the Phase-3 wheel is missing from those (§8) |
| — | Disk-full behaviour of the vault | **Not executed** (§5.1) |

## 11. Commit and push — withheld, and exactly what remains to do

The authorisation for this phase was conditional: commit and push **only if**
every mandatory gate passed. Gate 1 (full suite on a supported interpreter) and
gate 2 (restore matrix) did not pass, because they cannot run here (§3).
Therefore:

* **no commit was created** in the verified tree;
* **no push was attempted**, to `arena/7f12d627-erp` or anywhere else;
* PR #7 is untouched: still open, still draft, still at
  `5a3e47ddfba2b789bcff71d6438db4927f5c7986`, not merged.

The push was also authorised to **PR #7's head branch** (`arena/7f12d627-erp`),
not to this session's branch: the session branch sits on `main`
(`4fc1364a…`), which does not even contain `review_policy.py` or `rate_limit.py`,
so SEC-13/14/15 cannot be represented there. That decision stands.

Once B1 is closed, the remaining work is two commands and one review:

```bash
# in the verified tree (base 5a3e47dd + cumulative patch + 2 new tests)
git add -A
git commit -F - <<'MSG'
security: close SEC-13/14/15 and integrate the selective security package

Applies the preserved full security state on top of 5a3e47dd and adds the
SEC-13 password policy/lifecycle, SEC-14 OCR override validation and
SEC-15 Host/proxy boundary, with their acceptance suites (phase 58, 59).

Verified: 1340 passed / 20 interpreter-gated / 10 skipped on CPython 3.11.2;
335 passed targeted; ruff, compileall, dependency graph, vault and redaction
probes green. Restore matrix still pending a 3.11.17+/3.12.10+ interpreter.
MSG
git push origin HEAD:arena/7f12d627-erp
```

The commit must also carry `docs/security/PHASE5_B1_LOCAL_VAULT_RELEASE_GATE_REPORT.md`
and `docs/security/phase5_release_gate/`, and the gate result must be re-run and
recorded on the new interpreter before the push (a push on the strength of a
3.11.2 run would misrepresent the restore matrix).

## 12. Reproduction

```bash
# 1. tree
git clone https://github.com/Devmraustro/ERP /tmp/gate && cd /tmp/gate
git checkout 5a3e47ddfba2b789bcff71d6438db4927f5c7986
git apply /path/to/docs/security/phase4_closure/full-security-state-from-5a3e47dd.patch
cp /path/to/phase58.py /path/to/phase59.py tests/

# 2. environment (hash-pinned, no resolver drift)
python3.11 -m venv /tmp/ivenv && /tmp/ivenv/bin/pip install -U pip
/tmp/ivenv/bin/pip install --no-deps --require-hashes -r requirements.lock.hashes.txt

# 3. gates
/tmp/ivenv/bin/python -m pytest -q -p no:cacheprovider --junitxml=full.xml tests
/tmp/ivenv/bin/python -m pytest -q tests/test_phase59_sec13_14_15_closure.py \
    tests/test_phase58_security_selective_integration.py tests/test_phase10_vault_key_rotation.py \
    tests/test_phase7_authentication_remediation.py tests/test_phase28_user_admin.py \
    tests/test_phase37_deployment_assets.py tests/test_phase48_restore_interpreter_matrix.py \
    tests/test_phase39_bounded_rate_limiting.py
/tmp/ivenv/bin/python -m ruff check dz_erp tests tools
/tmp/ivenv/bin/python tools/verify_dependency_graph.py
/tmp/ivenv/bin/python docs/security/phase5_release_gate/probes/vault_probe.py
/tmp/ivenv/bin/python docs/security/phase5_release_gate/probes/redaction_probe.py
/tmp/ivenv/bin/python docs/security/phase5_release_gate/probes/smoke.py

# 4. interpreter floor (B1)
/tmp/ivenv/bin/python -c "
from dz_erp.security_audit import safe_restore_supported as s
print([(v, s(v)) for v in [(3,11,2),(3,11,16),(3,11,17),(3,12,9),(3,12,10)]])"
```

## 13. Artefact index

All paths relative to `docs/security/`.

| Path | Contents |
| :--- | :--- |
| `PHASE5_B1_LOCAL_VAULT_RELEASE_GATE_REPORT.md` | this report |
| `phase5_release_gate/logs/full_suite_311.xml` | JUnit XML of the full 3.11.2 run (1413 cases) |
| `phase5_release_gate/logs/failing_tests.txt` / `skipped_tests.txt` | the 20 gated failures and the 10 skips, one per line |
| `phase5_release_gate/logs/restore_failure_reasons.log` | per-failure root cause, all pointing at the interpreter gate |
| `phase5_release_gate/logs/interpreter_gate.log` | `safe_restore_supported` matrix |
| `phase5_release_gate/logs/b1_environment.log` | interpreter-install attempts and their exact errors |
| `phase5_release_gate/logs/targeted_suites.log` | 335-passed targeted run |
| `phase5_release_gate/logs/quality_gates.log` | ruff / compileall / dependency graph / lock counts |
| `phase5_release_gate/logs/packaging.log` | flat-layout failure and patched build |
| `phase5_release_gate/logs/vault_probe.log` | vault runtime measurements |
| `phase5_release_gate/logs/redaction_probe.log` | audit redaction measurements |
| `phase5_release_gate/logs/api_smoke.log` | API/auth/Host smoke results |
| `phase5_release_gate/logs/secret_scan.log` | forbidden SDKs, key material, credential literals |
| `phase5_release_gate/logs/tree_consistency.log` | changed files, assert/skip accounting, environment |
| `phase5_release_gate/probes/*.py` | the four probe scripts, runnable as-is |
| `phase5_release_gate/wheel-rebuild-sha256.txt` | the rebuild's hash and exact rebuild command; the wheel itself is **not** committed (binaries are not stored in this repository) |
| `phase5_release_gate/wheel-contents.phase5-rebuild.txt` | its 44 entries |
| `phase5_release_gate/SHA256SUMS` | checksums of every file above |
