# Final Audit & Readiness Report — Key Rotation and Release Readiness

**Repository:** https://github.com/Devmraustro/ERP
**Branch:** `arena/6c474c64-erp`
**Initial HEAD of the historical audit snapshot:** `becf52b74f2dabbd3ad9291bd28e2a207ce6b720` (head of PR #2, CI green)
**Final code HEAD of that historical snapshot (CI-verified):** `d823b83f79f1c0e2195768ab635f684a24b52479`
**Historical snapshot verification date:** 2026-10-22/23 (UTC)
**Environment used for that historical local evidence:** Linux x86-64, CPython 3.11.2, SQLite 3.40.1,
cryptography 44.0.2, pytest 8.3.5, ruff 0.14.0, venv at `/home/user/venv` (outside the repo).
**Target of the earlier audit:** Windows 11 / CPython 3.11.9 — so this session re-verified the
claims on a *different* platform rather than trusting them.

---


## Latest verification addendum — 2026-10-25

This addendum supersedes earlier verification figures for the current review head; the dated audit snapshot and older addenda remain historical.

**Exact code/PR head:** `4225f36c56fed66700d61ffe832db7373214a93d`  
**GitHub Actions:** [run 40210565234](https://github.com/Devmraustro/ERP/actions/runs/40210565234) — all three jobs successful.

- Linux Python 3.11.17: **562 passed, 3 skipped, 0 failed**; 88% coverage (4,403 statements, 523 missed).
- Linux Python 3.12.15: **562 passed, 3 skipped, 0 failed**; 88% coverage (4,403 statements, 523 missed).
- Windows Python 3.12.10 platform-sensitive suite: **138 passed, 8 skipped, 0 failed**.
- Synthetic isolated staging acceptance: **37/37 on Linux and 37/37 on Windows**. The checkpoint-path test uses a same-host sibling directory and does not prove off-host durability.
- Risk-register summary validation: passed; 31 rows.
- Hash-verified install, dependency graph, OpenCV/RapidOCR import, Ruff and byte-compilation gates passed as configured in the CI matrix.

The final migration-hardening change validates the rebuilt OCR table with `PRAGMA foreign_key_check` before removing the legacy table. If a legacy record references a missing company, source document or user, the migration raises and rolls back, preserving the original table. Regression tests prove (a) normal legacy migration, (b) idempotency, (c) rollback after a failed data copy and (d) rollback on orphan references. The first attempt on predecessor `9eb402d` revealed two old success fixtures that lacked parent rows; the fixtures were corrected at `4225f36`, and the exact-head suite is green.

**Release verdict remains:** CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY. CI and synthetic tests do not replace KMS/HSM custody, genuinely off-host checkpoints and alerting, representative OCR validation, production-scale recovery drills, or qualified Algerian accounting and legal sign-offs.


## Previous verification addendum — 2026-10-24 (historical)

This addendum records the follow-up verification; the dated audit snapshot and command table below remain historical.

**Exact code head:** 1c3e84408f6314789bf381d867f151a39f1ce893  
**GitHub Actions:** [run 40064379357](https://github.com/Devmraustro/ERP/actions/runs/40064379357) — all three jobs successful.

- Linux Python 3.11: 548 passed, 0 failed, 0 errors, 0 skipped; 89% coverage (4,269 statements, 458 missed).
- Linux Python 3.12: 548 passed, 0 failed, 0 errors, 0 skipped; 89% coverage (4,269 statements, 458 missed).
- Windows key rotation, migration, backup/restore, logging and recovery job: success.
- The regression test test_confidence_migration_rolls_back_if_copy_fails passed on Python 3.11 and 3.12. It forces the OCR confidence migration copy stage to fail and proves rollback leaves the original table and row intact.
- Ruff, compileall, dependency graph validation and hash-lock drift checking passed in exact-head CI.

The user's local Windows run is on predecessor 21f93b7912f2e13023cd4dd4cf10267178a2bf82: 529 passed, 18 platform-specific skips, 0 failed, 89% coverage. Phase 16 passed 4/4; phase 20 passed 65 with 3 platform-specific skips. The isolated synthetic staging acceptance script passed 37/37 at that predecessor. The atomic OCR migration change is the only code delta from that local run and is covered by the exact-head CI regression and full suite; the standalone staging acceptance script itself has not been rerun on 1c3e844.

**Release verdict remains unchanged:** CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY. CI and synthetic tests do not replace KMS/HSM custody, off-host checkpoint/alerting verification, representative OCR validation, production-scale recovery drills, or qualified Algerian accounting and legal sign-offs.

## 1. Executive summary and verdict

**VERDICT: CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY.**

The two architectural blockers recorded by the previous audit — no vault encryption-key rotation and
no audit HMAC-key rotation — are now **implemented, integrated and tested**, with the two mechanisms
kept separate as required:

* **Vault:** ciphertext is self-describing (`DZKV` + version byte), a key registry holds every
  version, and `rotate_key()` re-encrypts blob by blob with optimistic concurrency, round-trip
  content verification, a resume path and a secret-free journal.
* **Audit:** `audit_events` records the key version that signed each row, verification selects the
  per-row key, and rotation signs *new* events with a new version while preserving every historical
  hash unchanged. A missing key is reported as `AUDIT_KEY_UNAVAILABLE`, never as `valid: true` and
  never as tampering.
* **Backup/restore:** archives now carry every key version (inside the encrypted archive) plus a
  material-free version summary, and a restore of mixed-version data is verified end to end.

The suite result of 531 passed and the 37/37 staging acceptance cited here belong to the earlier audit run and are retained as historical evidence. For the latest code-head results, use the verification addendum at the top of this report; it distinguishes exact-head CI evidence from the predecessor Windows and staging run.

This round added three things the first delivery lacked: a tamper-evident record of the rotation
operations themselves (§11.2), enforced key custody at read time rather than a documented
convention (§11.3), and a reproducible load/interruption drill that immediately found a
rotation-convergence defect which unit tests had missed (§11.4).

Nothing here is a legal or accounting approval. Five categories of blocker remain (§7), all
documented: key custody that is file-based and exportable, rotation that is operator-invoked with
no scheduler, qualified Algerian accounting/tax review, Law 18-07/ANPDP formalities, and the
absence of a drill on production-scale data. The dependency deviation remains documented and
low-risk, and the audit-completeness gap is closed by the rotation ledger.

---

## 2. Verified repository state

| Item | Value | How verified |
|---|---|---|
| Branch | `arena/6c474c64-erp` | `git branch --show-current` |
| Initial HEAD | `becf52b74f2dabbd3ad9291bd28e2a207ce6b720` | `git rev-parse HEAD`; remote branch commit |
| Final code HEAD | `1be15250af822a62db8e62a3fc1d95e7b212d755` (this report's last edit is documentation-only) | `git log --oneline -1` |
| Working tree | Clean at commit time | `git status --short` |
| Remote | `origin/arena/6c474c64-erp` pushed and tracking | `git push -u`, `git log origin/...` |
| PR #1 | **OPEN, untouched, not merged** | `gh pr list` |
| PR #2 | **OPEN, untouched, not merged**; head still `arena/5c240e1d-erp` @ `becf52b` | `gh pr view 2` |
| CI for initial HEAD | Run `39870180714` — success (3.11, 3.12) | `gh pr checks 2` |
| CI for final code HEAD | Run [`39931381080`](https://github.com/Devmraustro/ERP/actions/runs/39931381080) — **success** on 3.11 (1 m 20 s), 3.12 (8 m 56 s) and **Windows** (13 m 52 s) | `gh run watch … --exit-status`; `gh api …/actions/runs/39931381080/jobs` |
| Deployments | None performed | — |

**Important scoping fact:** this work lives on the session branch `arena/6c474c64-erp`, **not** on
PR #2's branch. PR #1 and PR #2 were neither merged, modified nor rebased. This report is committed
on top of `1be1525` and changes no code.

### 2.1 Phase 0 reconciliation — the checkout did not match the prompt

The prompt described a working tree at `becf52b` on `arena/5c240e1d-erp`. In reality the sandbox
checkout was `main` at `0e106e05…` containing a 6-byte `README.md`. The implementation lived only on
the two remote `arena/*` branches. Reconciliation steps, all non-destructive:

1. `git fetch origin 'refs/heads/*:refs/remotes/origin/*'`.
2. Established that `arena/f74b041f-erp` (PR #1 head, `1b63c6e`) is an **ancestor** of
   `arena/5c240e1d-erp` (PR #2 head), i.e. PR #2 already contains PR #1's work.
3. Fast-forwarded the session branch to `origin/arena/5c240e1d-erp` (`--ff-only`). No force-push, no
   history rewrite, no branch switch.
4. Confirmed PR #2, CI state and the working tree before changing any file.

---

## 3. Evidence map of the pre-change implementation (Phase 0 findings)

| Concern | Pre-change state | Where |
|---|---|---|
| Vault ciphertext | Raw Fernet token, **no key identifier** | `dz_erp/intake_ocr.py` |
| Vault key | Single key file `<vault>.vault.key`, mode 0600, or `ERP_VAULT_KEY` | same |
| Audit MAC | HMAC-SHA256 over `company|occurred_at|actor|action|model|target|before|after|prev_hash` | `dz_erp/database.py` |
| Audit key | Single file `<db>.audit.key`, `ERP_AUDIT_HMAC_SECRET`, or (in-memory only) a published default | same |
| Audit schema | `audit_events` had **no key-version column**; append-only via triggers | `SCHEMA_SQL` |
| Backup | Fernet archive of SQLite snapshot + vault + manifest carrying **one** vault key and **one** audit key | `dz_erp/security_audit.py` |
| Migrations | Idempotent `PRAGMA table_info` column additions in `_migrate_schema` | `dz_erp/database.py` |
| Restore | Wrote both legacy key files beside restored artefacts | `dz_erp/security_audit.py` |
| Rotation | **None** for either key (the previous design document described an unimplemented plan) | `KEY_ROTATION_DESIGN.md` |

Baseline reproduced on this checkout before any change: `288 passed`, ruff clean, compile clean,
dependency validator clean, `pip check` exit 1 with exactly the one documented deviation.

---

## 4. Completed phases

### Phase 1 — Vault encryption-key rotation — **IMPLEMENTED AND TESTED**

*Files:* `dz_erp/key_rotation.py` (new, 311 statements), `dz_erp/intake_ocr.py`,
`tests/test_phase10_vault_key_rotation.py` (new, 23 tests), `.gitignore`.
*Commit:* `41992c7`.

* Format: `"DZKV" | version byte | Fernet token`; unmarked ⇒ legacy ⇒ version 1. The discriminator
  cannot collide with a Fernet token, which always begins with `g` (`0x67`).
* Registry `<vault>.vault.keys.json` (0600, atomic replace); exactly one active version; duplicate
  material across versions rejected; every retained key parsed as Fernet at start-up.
* `rotate_key()`: pre-scan → fail closed if any blob needs an absent version → persist registry →
  per-blob decrypt/re-encrypt/round-trip-verify → atomic replace only if unchanged → full
  post-sweep verification → reconciliation report (`attempted/completed/skipped_*/failed/errors`).
* Guarantees tested: legacy decryption; wrong key (`InvalidToken`); corrupt ciphertext; missing
  historical key at start-up **and** on read; unknown version; invalid/duplicate versions;
  forced interruption; resume without a third version; forced concurrent-update race (writer's
  bytes survive); lock contention; tenant isolation; key-removal guards; no plaintext or key
  material in the journal.

*Result:* 23/23 tests pass. Vault module coverage 92 %; `key_rotation.py` 86 %.

### Phase 2 — Audit HMAC-key rotation — **IMPLEMENTED AND TESTED** (kept separate from Phase 1)

*Files:* `dz_erp/database.py`, `dz_erp/security_audit.py` (registry carry-through for backup/restore),
`tests/test_phase11_audit_key_rotation.py` (new, 25 tests).
*Commit:* `2a8ea6b`.

* Schema: idempotent `ALTER TABLE audit_events ADD COLUMN hmac_key_version INTEGER NOT NULL
  DEFAULT 1`; the MAC input is **unchanged**, so no historical hash is recomputed.
* Verification selects the key recorded per row and distinguishes `EVENT_HASH_TAMPERED`,
  `PREV_HASH_MISMATCH` and `AUDIT_KEY_UNAVAILABLE` (with `missing_key_versions`).
* Start-up fails closed when an event needs an absent version.
* Rotation refuses to run on an unverifiable chain (tamper ⇒ `AuditTamperError`, lost key ⇒
  `AuditKeyUnavailableError`), persists the registry atomically before switching, and reports both
  verdicts. Rollback reactivates an older version without rewriting history.
* Tested: legacy-schema migration; mixed-version chain; modified/deleted/reordered/inserted events;
  missing and wrong keys; failed registry write leaves everything untouched; resume; rollback;
  removal guards; backup/restore of version metadata; verification from a **separate OS process**.

*Result:* 25/25 tests pass. `database.py` coverage 93 %.

### Phase 3 — Migration, backup/restore and rollback safety — **IMPLEMENTED AND TESTED**

*Files:* `dz_erp/security_audit.py`, `tests/test_phase12_key_migration_and_backup.py` (new, 17
tests). *Commit:* `7ba79c0` (+ assertion fix `ebd8db6`).

* A representative legacy deployment (pre-versioning schema, single key files, unversioned blobs) is
  upgraded **without rewriting anything** — verified byte-identical ciphertext and identical audit
  hashes; the migration is idempotent across restarts.
* Backup manifest carries all key versions (inside the encrypted archive) plus a material-free
  version summary returned to the caller.
* Restore of mixed-version ciphertext into an isolated directory verifies SHA-256, trial balance
  and every audit chain; restores both registries and reopens with no secrets supplied.
* Restore refuses when a required version is absent (`VaultKeyUnavailableError` /
  `AuditKeyUnavailableError`), when a registry is tampered with (`KeyConfigurationError`), and when
  an operator-supplied key contradicts the archive — with preflight validation **before** writing
  artefacts.
* Undecryptable records are never discarded or rewritten: they stay byte-identical, are reported as
  `failed`, and the rest of the store migrates.

*Result:* 17/17 tests pass.

### Phase 4 — Security regression audit — **RE-VERIFIED AGAINST CURRENT CODE; NEW TESTS ADDED**

*Files:* `tests/test_phase13_key_rotation_regression.py` (new, 6 tests).

Re-checked rather than assumed (existing suite plus new assertions): spoofed `X-User-Id` /
`X-Company-Id` headers are ignored (401); bearer tokens are server-issued, hashed at rest, expiring
and revocable; password verification + lockout tested; tenant scoping server-side; upload bounds
and content validation; fail-closed proxy trust; redacting logger; docs disabled by default with
`ERP_DOCS_ENABLED` opt-in; SoD on approval and reversal; idempotent posting; OCR cannot post.
New assertions specific to this change: **no API route exposes key rotation**; key material never
reaches the security log; rotation/lock/journal/keyring files are `0600`; registries are never
written inside the vault; a rotation cannot be used to bypass duplicate-posting or RBAC.

*Result:* 6/6 new tests pass; the pre-existing security tests (Phases 7–9) still pass unchanged.

### Phase 5 — Accounting, stock and OCR invariants — **RE-VERIFIED; ROTATION SHOWN NOT TO DISTURB THEM**

`test_phase13` posts a real invoice through approval and posting, then rotates **both** keys and
asserts: journal entries and lines unchanged, trials balance at 23 800.00 DZD, approval records and
invoice state unchanged, audit chain valid across two key versions, encrypted source document still
readable. Duplicate-posting and role checks still refuse after rotation. The existing Phase 2–6
suites (localization, 3-way match, procurement/stock, OCR intake, benchmark) pass unchanged.

*Not claimed:* Algerian legal compliance of any accounting or tax treatment. See §7 and
`docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`.

### Phase 6 — Dependencies and reproducible builds — **VERIFIED; ONE DEVIATION RETAINED AND EXPLAINED**

* `python tools/verify_dependency_graph.py` → **exit 0** (52 distributions; the only deviation is
  the allow-listed substitution).
* `python -m pip check` → **exit 1**, exactly one line:
  `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.`
* Evidence gathered from PyPI metadata this session: `rapidocr-onnxruntime` **1.4.4 is the latest
  release under that name** and hard-requires the GUI `opencv-python>=4.5.1.48`, so no version of
  the pinned package removes the conflict. The upstream successor package **`rapidocr` 3.10.0
  declares `opencv-python-headless`** — i.e. upstream itself adopted the headless build, which is
  evidence that the project's substitution is the sanctioned direction rather than a local hack.
* Decision: keep the tested minimal correction (headless wheel + explicit graph validator +
  `--no-deps` lockfile install). A move to `rapidocr` 3.x is a major-version migration (new
  transitive dependencies `omegaconf`, `requests`, `colorlog`; API changes) and is **not** done
  here. Recorded as a follow-up in §7.
* Python support range verified from metadata: `requires-python <3.13,>=3.6` upstream and
  `>=3.11` in `pyproject.toml` → CI matrix 3.11/3.12 is correct.

### Phase 7 — Algerian compliance gates — **CHECKLIST CREATED; NOTHING APPROVED**

*File:* `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`.
Per item: software evidence, outstanding professional question, responsible reviewer, required
evidence, and whether it blocks production. Covers SCF mapping, journal codes, precision, clearing
accounts **380 vs 408**, VAT, **stamp duty brackets**, **G50 aggregation**, invoicing mentions,
retention, Law 18-07 (register, data-subject rights, minimisation), **ANPDP formalities**,
cross-border hosting, and key custody. Every gate is marked open; the sign-off block is blank.

### Phase 8 — Staging validation — **EXECUTED (isolated, synthetic)**

*File:* `tools/staging_acceptance.py` (new, reproducible, `--dry-run`-safe state directory).
Built the **real** application through `dz_erp.serve.build_app` on 127.0.0.1 semantics in a fresh
temporary directory with `ERP_DOCS_ENABLED=false`, an explicit durable security-log path, and no
`ERP_VAULT_KEY` / `ERP_AUDIT_HMAC_SECRET` set:
startup → docs 404 → fail-closed proxy trust → keys beside artefacts → auth (incl. spoofed-header
rejection) → synthetic PDF upload → OCR → no financial record created → invoice → submit → SoD
refusal → independent approval → posting → balanced 23 800.00 DZD → idempotent replay → duplicate
refusal → audit verification → **vault rotation** → **audit rotation** → mixed-version chain →
**interrupted rotation + resume** → backup → **isolated restore** → **fresh OS process reads the
restored data** → no key material in logs/journals.

*Result:* **33/33 checks passed** (`exit 0`) at the time of that run; the tool now runs
37/37 checks after four ledger-checkpoint steps were added. No production host, credential, or customer data was
involved. This is **not** a production-equivalent environment.

### Phase 9 — Documentation and PR accuracy — **UPDATED**

* `KEY_ROTATION_DESIGN.md` — rewritten from "design, not implemented" to the implemented design,
  with the vault and audit mechanisms in separate sections, formats, threat model, limits and
  operational procedures.
* `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md` — new runbook: preconditions, rotation, resume,
  failure table keyed by exact exception names, rollback, key-deletion policy, backup-vs-key restore
  distinction, post-rotation checklist.
* `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` — new.
* `docs/STAGING_SECURITY_CONFIGURATION.md` — corrected stale statements (rotation now exists; the
  security log now rotates; docs exposure is now env-controlled).
* `.gitignore` — ignores `*.keys.json`, `*.rotation.jsonl`, `*.rotation.lock` so key material and
  operator journals cannot be committed by accident.
* `README.md`-level accuracy: no claim in this report is derived from the previous report; each is
  from a command run this session.
* **PR descriptions were not edited.** PR #2 belongs to a different branch and its description
  describes that branch's state; this session's work is not part of PR #2, so rewriting its
  description would misrepresent it. The accurate record for this change is this report and the
  commit messages on `arena/6c474c64-erp`.

### Phase 10 — Final verification and change control — **DONE** (see §5 and §6)

---

## 5. Security and data integrity

| Property | Status | Evidence |
|---|---|---|
| Vault rotation implemented | **Yes** | `rotate_key`, 23 tests, staging check |
| Audit HMAC rotation implemented | **Yes** | `rotate_audit_key`, 25 tests, staging check |
| Historical data readable with its own key | **Yes** | legacy-blob test; pre-versioning schema test; mixed-version chain |
| Missing key never silently replaced | **Yes** | start-up + read paths raise `VaultKeyUnavailableError` / `AuditKeyUnavailableError`; existing process-boundary tests still pass |
| Interruption recovery | **Yes** | forced-crash tests; resume without a new version; journal shows no `rotation_finished` |
| No false "migrated" state | **Yes** | contended/skipped/failed are reported separately from `completed` |
| Backup/restore keeps encrypted records recoverable | **Yes** | mixed-version restore; reopen with no secrets; fresh-process verification |
| Tampering still detected after rotation | **Yes** | modified/deleted/reordered/inserted tests |
| Missing key ≠ tamper | **Yes** | `AUDIT_KEY_UNAVAILABLE` vs `EVENT_HASH_TAMPERED`; distinct exception classes |
| No secrets in logs/reports | **Yes** | assertions on security log, both journals, CLI output and backup metadata |
| Tenant boundaries preserved | **Yes** | cross-company read denied before and after rotation |
| Financial invariants preserved | **Yes** | posted entries/approvals/invoices unchanged; trial balance balanced |

**Residual risk, stated plainly:** key material is protected by file permissions (`0600`), not by
an HSM or KMS. An attacker with the service account's file access can read every key. Vault
rotation uses optimistic concurrency only (no blob-level distributed lock). Rotation is not recorded
in the tenant audit chain (it is recorded in the rotation journal) because `audit_events.company_id`
is `NOT NULL` and a global key operation has no trustworthy tenant to attribute.

---

## 6. Quality verification — historical snapshot (exact commands, exit codes, results)

Run in `/home/user/ERP` with `/home/user/venv/bin/python` (CPython 3.11.2):

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `python tools/verify_dependency_graph.py` | 0 | 52 distributions match the declared graph; only the documented substitution |
| 2 | `python -m pip check` | 1 (expected) | `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.` — documented, allow-listed, not concealed |
| 3 | `python -m ruff check dz_erp tests tools` | 0 | All checks passed |
| 4 | `python -m compileall -q dz_erp tests tools` | 0 | No syntax errors |
| 5 | `python -m pytest --cov=dz_erp --cov-report=term -q` | 0 | **531 passed** (94.03 s without coverage, 109.57 s with); total coverage **89 %** (4162 statements, 452 missed) |
| 6 | `python tools/staging_acceptance.py` | 0 | **37/37 checks passed** (isolated, synthetic), including the four ledger-checkpoint checks |
| 7 | CI run `39931381080` on `1be15250af…` | 0 | success — Python 3.11 (1 m 20 s), Python 3.12 (8 m 56 s), **Windows** key rotation/backup suite (13 m 52 s) |

Coverage of the touched modules: `key_rotation.py` 86 %, `database.py` 93 %, `intake_ocr.py` 92 %,
`security_audit.py` 70 %, `serve.py` 100 %. New modules: `rotation_audit.py` and
`key_provider.py` are covered by phases 14–17 (31 tests) — note that the `KmsKeyProvider`
refusal path and the Windows branches of `FileKeyProvider` are exercised as documented
limitations, not as working code paths.

**Stability:** the full suite was run on every commit of this round and on both Linux Python
versions plus Windows in CI (run `39931381080`). One flaky assertion from the earlier round was
found and **fixed rather than retried** (`ebd8db6`): it compared the SQLite file byte-for-byte
across an online backup, which SQLite may legitimately rewrite; it now compares ciphertext and
checks the source records logically. Two tests in this round were rewritten for the same reason
rather than loosened: the legacy-key fixture now reproduces the real `0600` mode instead of a
laxer one that never shipped, and the custody tests assert the control rather than a specific
`oct()` string.

**Known non-failures:** the single pytest warning is an upstream Starlette `DeprecationWarning`
about `anyio.abc.BlockingPortal`, unrelated to this change.

**Not executed, and why:** no container build (no Dockerfile in the repository); no third-party
penetration test; no drill on production-scale data, real backup media or real scanner output
(the synthetic drill is in `docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md`, and §5 of that document
lists what it does not prove). Windows crash-consistency under power loss is not tested — the
suite runs on `windows-latest`, but a real fsync/power-loss test needs hardware we do not have.

---

## 7. Outstanding blockers

| # | Issue | Evidence | Risk | Required action | Blocks staging? | Blocks production? |
|---|---|---|---|---|---|---|
| 1 | Key material protected by filesystem permissions only (no HSM/KMS, no split custody) | `key_rotation.py` stores keys as `0600` files; §5 | High | Select and document key custody (KMS/HSM or equivalent operational control) | No | **Yes** |
| 2 | Rotation is operator-invoked; no scheduler | `dz_erp/rotate_keys.py` is manual. **Alerting now exists**: `monitor` reports overdue keys, unfinished operations, ledger validity and custody drift, exiting 3 when unhealthy | Medium | Schedule `monitor` and alert on its exit code; define the rotation cadence | No | **Yes** (cadence is procedural) |
| 3 | Rotation is not an event in the tenant audit chain | §5, design §5.2 | Medium | Rotation is now recorded in a **separate tamper-evident ledger** (`dz_erp/rotation_audit.py`, own key, HMAC chain, off-host checkpoints) because `audit_events.company_id` is `NOT NULL` and a global operation has no honest tenant. Not integrated into the tenant chain, by design. Residual: the ledger key sits beside the ledger (§11.3) | No | No, provided checkpoints are exported off-host |
| 4 | Algerian accounting/tax treatment unvalidated | `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md` — every gate open | High | Qualified review of SCF mapping, **380 vs 408**, VAT, **stamp duty**, **G50**, invoicing; signed sign-off block | No | **Yes** |
| 5 | Law 18-07 / ANPDP obligations unaddressed in software and process | No processing register, no data-subject-rights workflow, no filing evidence, no cross-border decision | **High** | DPO/legal: register, rights procedure, ANPDP formalities, hosting decision | No | **Yes** |
| 6 | `pip check` exits 1 on the documented OpenCV substitution | §6 row 2; PyPI metadata evidence | Low | Accept-and-document (current), or migrate to `rapidocr` 3.x in a dedicated change | No | No (documented) |
| 7 | OCR accuracy not statistically established (18 synthetic documents) | `docs/OCR_BENCHMARK_REPORT.md` | High *for unattended posting* | Keep human review mandatory; build a powered benchmark on real, consented data | No | No, while human review is enforced |
| 8 | Optimistic concurrency only during vault rotation | design §2.5 | Low | Run rotation in a maintenance window; document the window | No | No (procedure) |
| 9 | No drill on production-scale data, real backup media or real scanner output | A reproducible synthetic drill now exists (`tools/rotation_scale_drill.py`, results in `docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md`); it is 1 000 documents on local storage, not production data | Medium | Execute one drill on a copy of production-scale data before go-live | No | **Yes** |

**What is *not* a blocker any more:** vault key rotation, audit HMAC rotation, historical
verification, interruption recovery, backup/restore of versioned keys, API docs exposure, security
log rotation, reversal approval/SoD. Each is implemented and has executable evidence.

---

## 8. Classification of every claim in this report

**Implemented and tested:** vault rotation; audit rotation; per-event key versions; legacy
ciphertext/schema compatibility; interruption + resume; concurrency guard; key-removal guards;
backup with all key versions; restore with verification; failure classification; tenant isolation;
financial invariants across rotation; API surface unchanged; docs disabled by default; security-log
redaction.

**Implemented, incompletely tested:** very large vaults (resumability is tested functionally and on
synthetic data, not on production data); Windows `fsync` semantics under power loss (the rotation
suite runs on `windows-latest` in CI, but a crash-consistency test on real hardware is out of
scope).

**Implemented in this round (previously "designed but not implemented"):** a tamper-evident record
of rotation events (separate ledger with an independent key, §11.2); enforced key custody at read
time with symlink refusal (§11.3); machine-readable rotation health monitoring (§11.1).

**Designed but not implemented:** automated/scheduled rotation; HSM/KMS key custody; Law 18-07
register and rights workflows; business-record retention and erasure policy.

**Blocked by missing credentials/infrastructure/approvals:** production-scale drill (needs
production-like data and storage); HSM/KMS (infrastructure + budget); ANPDP filing (external
approval); accounting/tax sign-off (qualified professionals).

**Requiring external legal/professional validation:** all of §7 items 4 and 5, plus the FEC/SCF,
VAT, stamp-duty and G50 mapping questions enumerated in the compliance checklist.

---

## 9. Exactly one verdict

**CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY**

Conditions met for staging: keys are supplied through files/environment with no silent fallback;
API docs are disabled by default; rotation and restore are verified; synthetic data only; dedicated
database and vault; the security log has a durable path and rotation policy.

Conditions **not** met for production: §7 items 1, 2, 4, 5 and 9 (item 3 was closed by
the rotation ledger, §11.2).

---

## 10. Next actions, in priority order

1. **Legal/accounting:** commission the qualified Algerian review in
   `docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md`; resolve the **380 vs 408** clearing-account question
   and the stamp-duty/G50 mapping first — they touch posting logic.
2. **Data protection:** produce the Law 18-07 processing register, data-subject-rights procedure and
   ANPDP filing plan; decide hosting (cross-border implications).
3. **Key custody:** select KMS/HSM-backed custody or an equivalent documented control, and define
   the rotation schedule with monitoring. *Progress:* custody is now enforced at read time, symlinked
   keys are refused, and `monitor` reports custody drift and overdue keys
   (`docs/KEY_CUSTODY_THREAT_MODEL.md`). The KMS/HSM decision itself is still external.
4. ~~**Audit completeness:** design a global, non-tenant audit stream so rotation operations are
   themselves tamper-evident.~~ **Done** as a separate rotation ledger with an independent key, and
   it records refusals and interruptions, not just successes (§11.2).
5. **Operational drill:** run one rotation + backup + restore on a production-scale copy, timing
   each phase and confirming `verified_ok` and `chain_valid_after`.
6. **Dependency follow-up:** evaluate `rapidocr` 3.x (already headless) as a planned major-version
   migration; until then keep the documented substitution and the graph validator.
7. ~~**Scale testing:** benchmark rotation over a realistically sized vault; confirm the resume path
   under a forced kill.~~ **Done for synthetic data** (§11.4); production-scale data remains item 9.
8. ~~**Windows verification:** run the rotation paths on Windows.~~ **Done** — a `verify-windows` CI
   job runs the rotation/backup suite on `windows-latest`, and it found a real defect (`os.fchmod`,
   POSIX-only, which broke every key write on Windows).

---

## 11. Post-implementation risk-closure round

The sections above describe the implementation as first delivered. This round attacked the
risks that deliverable left open — not by describing them again, but by changing what the code
does and measuring the result. Every claim below has an executable artefact.

### 11.1 What changed

| Area | Change | Why it was needed |
|---|---|---|
| Windows correctness | `os.fchmod` (POSIX-only) removed from the key write path; `_set_mode_on_fd` falls back to `chmod`; `os.replace` retried only on Windows `PermissionError` | Key rotation raised `AttributeError` on Windows — every key write failed there. CI now proves it on `windows-latest` |
| Rotation history | `dz_erp/rotation_audit.py`: append-only HMAC-SHA256 ledger with its own key ring, recording operation, phase, actor, key versions, counts, integrity outcome and interruption/recovery | Rotation was recorded only in a per-store journal; a privileged operation left no tamper-evident trace, and a refusal could not be recorded at all |
| Key custody | `dz_erp/key_provider.py`: `O_NOFOLLOW` reads, mode verified on the open descriptor, `0700` key directory, symlinks refused, KMS provider honestly unimplemented | Key reads were eight independent `read_bytes()` calls; a symlinked key was followed silently |
| Rotation convergence | Writes re-checked against the active key version; rotation converges on observed version headers; `COMPLETED_VERIFIED` requires convergence | A rotation could report success while documents still needed the retired key — silent data loss on the next key retirement |
| Monitoring | `rotate_keys monitor` emits health JSON (overdue keys, unfinished rotations, ledger validity, custody verdicts, provider mode) and exits 3 when unhealthy | "Rotate on a schedule" had no detection mechanism; there was nothing to alert on |

### 11.2 The rotation ledger, and why it is not the audit chain

`audit_events.company_id` is `NOT NULL` and every read is tenant-scoped, while rotation is a
deployment-global operation with no honest tenant to attribute it to. Worse, recording the
rotation of the audit key *inside* the audit chain makes the record depend on the key being
rotated: lose that key and the record of its own rotation becomes unreadable, and a rotation
refused *because* the chain is broken could not be recorded anywhere.

The ledger therefore has independent key material, its own chain (`dz-erp-rotation-ledger` v1,
genesis `"0"*64`, MAC over canonical JSON), and these properties, each asserted in
`tests/test_phase14_rotation_audit_ledger.py` (23 tests):

* a refused or failed rotation is recorded (`REFUSED` / `FAILED`), with the exception classified
  rather than string-matched;
* an interrupted operation is a `STARTED` with no terminal record; the next run emits
  `RECOVERED` + `RECOVERY`, so the signal cannot become permanent noise;
* modification, deletion, reordering and insertion are all detected;
* **tail truncation is undetectable by the chain alone** — only an off-host checkpoint
  (`export_checkpoint` / `verify_against_checkpoint`) catches it. The test asserts this
  limitation instead of claiming full tamper-proofness;
* a missing ledger key yields `LEDGER_KEY_UNAVAILABLE`, never "valid".

### 11.3 Custody: what is enforced, what is only claimed

Enforced at read time for the vault key, the audit key, both registries and the ledger key:
owner-only mode (checked on the descriptor actually read), no symlinks, `0700` directories.

Not claimed: `describe_key_protection()` reports `equivalent_to_hsm: False`, and
`KmsKeyProvider.read()` raises rather than returning a locally generated key. The residual risk
— exportable keys, the running process holding them, no split custody — is stated in
`docs/KEY_CUSTODY_THREAT_MODEL.md` rather than implied away.

### 11.4 Measured behaviour

`tools/rotation_scale_drill.py` produces the numbers in
`docs/KEY_ROTATION_SCALE_AND_DRILL_RESULTS.md`. On a 2-vCPU container, 1 000 documents and
10 000 audit events:

* rotation 1.45 s (≈690 documents/s, flat from 200 to 1 000 documents), audit rotation 0.18 s;
* chain verification 0.09 s; backup 0.41 s; restore 0.28 s with manifest hashes, audit chains
  and a balanced trial balance (10 020.00 / 10 020.00) verified;
* the rotator killed with `os._exit(137)` at 10 %, 50 % and 90 %: documents readable throughout,
  the ledger showed the orphan, and resume converged in 0.28–1.40 s;
* 2 031 documents written by four threads during a rotation: all readable, zero strays;
* peak RSS grew ~6 MB across a 5× data increase, so rotation does not buffer the corpus.

**The drill found a real defect** (§11.1, convergence): three documents stayed on the old key
after a rotation reported `COMPLETED_VERIFIED`. Unit tests had not caught it because "every blob
decrypts" is true for a blob on the old key. That is the argument for a reproducible drill
rather than a one-off benchmark.

### 11.5 What this round did not fix

* No HSM/KMS; no split custody or two-person rule (infrastructure and budget).
* No scheduler: `monitor` detects an overdue key, nothing rotates it.
* Arabic-only scanned documents still cannot be recognised by the default model head
  (`docs/OCR_EVALUATION_PROTOCOL.md` §7); digital-born Arabic is unaffected.
* Field confidence is still the line score (constant `0.995` for digital PDFs), so no auto-accept
  threshold can be honestly calibrated yet (protocol §3, prerequisite P0).
* No production-scale drill, no third-party penetration test, no real-data benchmark.

The consolidated view of all of the above, with severity, evidence, owner and action per item, is
`docs/RISK_REGISTER.md`; the reviewer-facing index is `docs/COMPLIANCE_REVIEW_PACKAGE.md`.

---

*Every number in this report comes from a command executed against commit
`d823b83f79…`; the CI result is the run for that exact SHA. Nothing in this report is inferred from
the previous audit.*
