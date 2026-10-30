# Final independent verification report

**Verifier:** independent pass performed on 2026-10-30, re-deriving every claim
from the repository, the remote, and the CI API — not from the earlier session's
summaries.

**Subject:** branch `arena/7f12d627-erp`, remote head
`8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595`.

**Nothing was merged, closed, rebased, marked ready, deployed or pushed.** The
three documents written by this pass exist only in the working tree; the branch,
the remote head and PR #7 are unchanged by it.

---

## 1. Commits, parents, branch, remote head

`git rev-parse HEAD` = `8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595`;
`git rev-parse origin/arena/7f12d627-erp` = the same SHA;
`git rev-list --left-right --count HEAD...origin/arena/7f12d627-erp` = `0  0`;
trees identical. Working tree clean before this pass.

Base of the workstream: `852f389f30176bdfedaa33fe9585784b78d76ebe`
(`origin/main` is `4fc1364a9504fab1153ea44e753f354f00023428`, untouched).

| # | Commit SHA | Parent | Subject |
|---|---|---|---|
| 1 | `09918a056c5afc3f9ae80e694a22a475b8f005cb` | `852f389f30176bdfedaa33fe9585784b78d76ebe` | fix(ocr): a duplicate extraction row can no longer authorize an unreviewed value |
| 2 | `49b9201cc1dcd257d62762dc2f35e275e461ad25` | `09918a056c5afc3f9ae80e694a22a475b8f005cb` | feat(api): throttle failed logins per account and correlate every request |
| 3 | `4eb5581f16fe1953f6bddcb2bb1b451f5b74eb4b` | `49b9201cc1dcd257d62762dc2f35e275e461ad25` | feat(accounting): resolve Timbre and TVA from the effective-dated configuration |
| 4 | `42334da20e1d2868f92300072e7a2d25586cce7a` | `4eb5581f16fe1953f6bddcb2bb1b451f5b74eb4b` | feat(ops): guard the deployment assets and prove the migration matrix |
| 5 | `edbd1d8ea410ee51a94d4b528f56918fe01ab028` | `42334da20e1d2868f92300072e7a2d25586cce7a` | docs: security, deployment, migration, accounting and handover documentation |
| 6 | `8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595` | `edbd1d8ea410ee51a94d4b528f56918fe01ab028` | docs: record the green CI run on the final head |

Every commit has exactly one parent (`git log --format='%H %P'`); the chain is
linear with no merges, no rebases and no rewritten history.

This report is delivered by a **documentation-only commit** on top of that head:
the code that was verified is exactly `8b1b56c9`, and the delivering commit
touches only `README.md` and files under `docs/`. It contains no source, test,
migration or configuration change, so the test and CI evidence in §§3–5 continues
to describe the code the branch actually ships.

## 2. Which PR contains what

Verified per commit through the commits→pulls API
(`gh api repos/Devmraustro/ERP/commits/<sha>/pulls`), not by assuming.

| Commit | PRs containing it |
|---|---|
| `09918a05` | **#7 only** |
| `49b9201c` | **#7 only** |
| `4eb5581f` | **#7 only** |
| `42334da2` | **#7 only** |
| `edbd1d8e` | **#7 only** |
| `8b1b56c9` | **#7 only** |

**Phases 35–38 and the Priority 0 fix are all in PR #7**, and only there. The
phase files are present at the remote head (`git ls-tree -r
origin/arena/7f12d627-erp`): `tests/test_phase34…38`, `dz_erp/rate_limit.py`,
`ops/Dockerfile`, `ops/docker-compose.yml`, `ops/erp.env.example`,
`.dockerignore`, and the fifteen new documents. PR #7's diffstat against its base
is 39 files, +5 865 / −78.

PR #7 is **Draft**, base `fix/phase29-invoice-discount-and-line-net-consistency`,
head `8b1b56c9`, `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`.

## 3. CI on the exact remote head

Run **41129089741** — <https://github.com/Devmraustro/ERP/actions/runs/41129089741>
— `head_sha=8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595`, event `pull_request`,
status `completed`, conclusion **success**, attempt 1.

Every job and every step was read from the jobs API (`gh api
…/actions/runs/41129089741/jobs`):

| Job | Result | Steps |
|---|---|---|
| Test, coverage and static checks (Python 3.12) | **success** | 1–10 success (checkout, toolchain, hash-verified install, dependency graph, cv2/RapidOCR import, ruff, byte-compile, **complete suite with coverage**), 11–12 **skipped** (staging acceptance, risk-register summary), 13–14 success, 27–29 post steps success |
| Test, coverage and static checks (Python 3.11) | **success** | 1–14 **all success**, including **complete suite with coverage**, **isolated synthetic staging acceptance**, **risk-register summary**; 27–29 success |
| Key rotation, backup and recovery on Windows | **success** | 1–11 all success, including **key rotation, migration, backup/restore, logging and recovery tests** and **staging acceptance on Windows**; post steps success |

Three runs on this branch are `cancelled` (`41124627918` on `09918a05`,
`41124878394` on `49b9201c`, `41125961910` on `4eb5581f`). They are superseded by
later pushes on the same branch and are **not** counted as evidence. The run on
the earlier head `edbd1d8e` (`41127176839`) is green but is likewise not used as
evidence for the current head.

**Limitation, stated plainly:** the numeric test counts inside CI live in the run
artifacts. Artifact download is blocked from this sandbox (egress is limited to
the GitHub API host; `gh run download` failed at the blob-storage URL). Therefore
the CI claim in this report is *step-level* ("the complete suite step succeeded on
`3.11` and `3.12`, each resolved to the latest patch the runner offers), not a reproduced count. The counts below come from the
runs performed locally in this pass.

## 4. Complete test suite, on this host, against a re-measured baseline

Environment: Linux, CPython **3.11.2**, pytest 8.3.5, coverage 6.0.0, ruff 0.14.0,
hash-locked dependencies (`/tmp/venv`).

```bash
# current head, clean tree
python -m pytest -q --cov=dz_erp --cov-report=term
#   -> 890 passed, 20 failed, 7 skipped, 1 warning in 198.91s ; TOTAL 78%

# baseline, measured independently in a throw-away worktree at 852f389f
git worktree add /tmp/wt_base 852f389f30176bdfedaa33fe9585784b78d76ebe
cd /tmp/wt_base && python -m pytest -q
#   -> 796 passed, 20 failed, 7 skipped, 1 warning in 162.93s
git worktree remove /tmp/wt_base --force
```

The worktree was removed afterwards; `git worktree list` shows only the repository
and `git status` is clean.

### 4.1 The 20 failures, precisely

Failure **names are identical** between baseline and head: `diff` of the sorted
`FAILED` lists is empty — no test was added to or removed from the failing set.
The causes were re-measured in this pass by re-running exactly those twenty tests
with `--tb=line`:

| Failure site | Count | Meaning |
|---|---|---|
| `security_audit.py:1093` `FinancialIntegrityError: Safe backup restore requires Python 3.11.17+ or Python 3.12.10+ (and below 3.13)` | 12 | the version gate raises before the test's own subject |
| `security_audit.py:1099` `FinancialIntegrityError: Safe backup extraction is unavailable in this Python runtime` | 1 | same gate, refused at extraction |
| `test_phase12…py:604` `AssertionError: Regex pattern did not match` | 3 | expected `Unsafe tar member path`; the runtime gate refuses first |
| `test_phase12…py:686` `AssertionError: Regex pattern did not match` | 1 | expected the hard-link refusal message; same cause |
| `test_phase9…py:229` `assert 'failed SHA-256 verification' in '<version-gate message>'` | 1 | message assertion, same cause |
| `test_phase9…py:51` `AssertionError: worker 'restore' exited 1` | 2 | the restore subprocess dies on the same gate |
| **total** | **20** | every one is the interpreter-version gate, in four shapes |

The twenty tests:

```
tests/test_phase5_security_audit_backup.py::test_audit_immutability_log_redaction_and_encrypted_backup_restore
tests/test_phase6_ocr_benchmark_e2e.py::test_complete_14_step_end_to_end_supplier_invoice_workflow
tests/test_phase8_upload_boundary_and_ci.py::test_backup_and_restore_preserve_the_audit_key
tests/test_phase8_upload_boundary_and_ci.py::test_restored_database_and_vault_are_reopenable_without_the_original_secrets
tests/test_phase9_process_boundary.py::test_incomplete_restore_is_rejected
tests/test_phase9_process_boundary.py::test_repeated_restores_are_idempotent_and_leave_a_usable_deployment
tests/test_phase9_process_boundary.py::test_restore_is_usable_from_a_fresh_process_in_a_different_directory
tests/test_phase11_audit_key_rotation.py::test_a_pre_versioning_database_restores_from_backup
tests/test_phase11_audit_key_rotation.py::test_backup_restore_preserves_every_audit_key_version
tests/test_phase11_audit_key_rotation.py::test_restore_reports_a_missing_key_version_as_unavailable
tests/test_phase12_key_migration_and_backup.py::test_backup_restores_mixed_ciphertext_into_an_isolated_environment
tests/test_phase12_key_migration_and_backup.py::test_restore_accepts_last_windows_binary_runtime_with_strict_preflight
tests/test_phase12_key_migration_and_backup.py::test_restore_of_a_tampered_registry_fails_closed
tests/test_phase12_key_migration_and_backup.py::test_restore_refuses_preexisting_hardlink_target_before_writing
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_ciphertext_that_needs_an_absent_key
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_noncanonical_tar_paths_before_writing[../evil/../dest/sub/file]
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_noncanonical_tar_paths_before_writing[../outside.txt]
tests/test_phase12_key_migration_and_backup.py::test_restore_rejects_noncanonical_tar_paths_before_writing[vault/../outside.txt]
tests/test_phase12_key_migration_and_backup.py::test_restore_requires_a_backup_key_and_does_not_leak_on_failure
tests/test_phase12_key_migration_and_backup.py::test_restore_with_a_contradicting_audit_key_is_refused
```

These are **environmental, not product** failures: the safe-restore path refuses
to extract below the patched interpreter. They are *not* skipped — the suite fails
loudly rather than pretending. They do not occur in CI, where the same code runs
on the runner's `3.11` and `3.12`.

### 4.2 The delta is fully accounted for

| New suite | Tests | Result |
|---|---|---|
| `tests/test_phase34_ocr_duplicate_row_integrity.py` | 19 | 19 passed, 4.00 s |
| `tests/test_phase35_api_rate_limiting_and_request_logging.py` | 16 | 16 passed, 2.25 s |
| `tests/test_phase36_accounting_and_tax_configuration.py` | 29 | 29 passed, 1.14 s |
| `tests/test_phase37_deployment_assets.py` | 6 | 6 passed, 0.03 s |
| `tests/test_phase38_migration_matrix.py` | 24 | 24 passed, 1.40 s |
| **total** | **94** | 796 + 94 = **890** ✔ |

No pre-existing test was deleted, skipped or had an assertion relaxed. One
assertion set was **rewritten** in `tests/test_phase9_durable_security_log.py`
(finding **F-03**, assessed in §11.1) — it is the only pre-existing test whose
body changed, and the change is stronger, not weaker.

### 4.3 Static and consistency gates

```
python -m ruff check dz_erp tests tools        -> All checks passed
python -m compileall -q dz_erp tools           -> clean
python tools/risk_register_counts.py           -> consistent (40 rows)
python tools/verify_dependency_graph.py        -> consistent
python tools/operational_readiness.py          -> exit 1 (unconfigured, as designed; every failure names its variable)
python tools/operational_readiness.py --run-checks -> exit 1 (key-status unavailable without ERP_DB_PATH/ERP_VAULT_PATH, as documented)
python -m pytest tests/test_phase22_register_and_docs_consistency.py -q -> 15 passed
```

## 5. Priority 0 — independent inspection of the implementation

Three methods were used together: (a) reading the code paths, (b) the committed
regression suite, (c) an **independent probe written from scratch for this pass**
that does not import the project's test helpers (`/tmp/verify_policy.py`, 32
assertions, **32 passed / 0 failed**). The probe derives the value each field must
be confirmed with *from the policy itself*, so it cannot accidentally encode a
canonical form the policy does not use.

| Requirement | Mechanism (code) | Independent evidence | Verdict |
|---|---|---|---|
| Duplicate **header** rows, identical values | `_row_group_ids` / `_row_sort_key` group rows per field; `len(rows) > 1` ⇒ `duplicate_unresolved` unless a decision binds to *all* ids; `blocking += DUPLICATE_EXTRACTION_ROWS:<field>` | probe `[B]` 5/5 (both ids listed, flag raised, blocked); `test_1` | **VERIFIED** |
| Duplicate **header** rows, different values | same; `rows[0]` is never adopted as "the" value for satisfaction | probe `[C]` 3/3 — the original fail-open is closed; `test_2` | **VERIFIED** |
| Duplicate **invoice-line** rows | line rows grouped by `line_index`; a shared index blocks `line_item_json[i]`, `line_unit_price[i]`, `line_discount[i]` | probe `[I]` 3/3; `test_4`; distinct indices unaffected — probe `[J]` 3/3, `test_5` | **VERIFIED** |
| Evidence-bound human decisions | `evidence_json = {row_ids, selected_row_id, group_size, selected_source}`; satisfaction requires `set(row_ids) == set(current ids)`; API exposes `selected_row_id` and refuses a row outside the group (`EXTRACTION_ROW_NOT_IN_GROUP`) | probe `[D]` 5/5, `[E]` 2/2, `[F]` 2/2; `test_6`, `test_6b`, `test_17`; `api.py:181`, `intake_ocr.py:1939-1945` | **VERIFIED** |
| FLAG behaviour | coverage counted only for `CONFIRMED`/`CORRECTED`; `FLAGGED`/`NOT_PRESENT` cannot close a group | probe `[G]` 2/2; `test_16` | **VERIFIED** |
| Changed evidence after a previous decision | a third row makes the binding stale ⇒ field re-opens | probe `[H]` 2/2; `test_9` (including idempotent replay) | **VERIFIED** |
| Submit gate | `_document_review_gate(action="SUBMIT")`, audit `INVOICE_SUBMIT_BLOCKED_UNREVIEWED`, invoice stays `Validated`, no approval row | `test_10`; gate read at `procurement_stock.py:1660` | **VERIFIED** |
| Approve gate | `action="APPROVAL"`, audit `INVOICE_APPROVAL_BLOCKED_UNREVIEWED`, invoice stays `PendingApproval`, nothing posted | `test_11`; gate at `:1774` | **VERIFIED** |
| Post gate | gate re-run **inside** the posting transaction (`action="POSTING"`, `audit=False`), denial raised after rollback as `_RefusedAttempt` (commit-then-refuse) | `test_12`; gate at `:2222`, `_refuse_posting_and_recover` at `:2114` | **VERIFIED** |
| Audit persistence after refusal | refusal event committed in its own transaction with actor, target and `invoice_returned_to`; audit chain still verifies | `test_10`, `test_12` assert the action names and `verify_audit_chain(1)["valid"] is True` | **VERIFIED** |
| Recovery from `NeedsReview` | approval `INVALIDATED` (`SOURCE_REVIEW_INVALID_AT_POSTING`), `approved_payload_sha256` cleared, invoice **and** `source_documents` returned to `NeedsReview`, then re-validate → submit → approve → post once (ledger 1190.00, balanced) | `test_12` | **VERIFIED** |
| Tenant isolation | every query in the gate and the decision service carries `company_id`; another tenant's document is not addressable | probe-independent: `test_13` asserts disjoint row-id sets and a not-found refusal; `security_audit.evaluate_document_review_policy_secure` scopes by tenant | **VERIFIED** |
| Concurrency | the gate reads fields/decisions inside the same `BEGIN IMMEDIATE` transaction that moves the invoice (`database.py:1138`), so writer serialisation removes the TOCTOU window | `test_14` (two threads; end state coherent, chain valid) | **VERIFIED** |

Not-verified nuance: the probes and tests exercise a single-process, single-writer
SQLite deployment. Multi-process writers against the same database file are not
supported by design and are not tested (see `R-CONC`, BLOCKED_EXTERNAL).

## 6. Rate limiter — deployment classification

Read directly from `dz_erp/rate_limit.py`:

* storage: `self._events: Dict[str, Deque[float]] = {}` — **process-local memory**;
* `describe_throttle_policy()` itself reports
  `"scope": "per-process (per worker; not a global bound across replicas)"`,
  `"storage": "in-memory sliding window"`;
* `dz_erp/serve.py` calls `uvicorn.run(app, …)` with no `workers=` argument ⇒ one
  process;
* `ops/docker-compose.yml` pins `deploy.replicas: 1`.

**Classification: STAGING-ONLY.** It is a real bound for the single-process
deployment shipped here. With N workers or N replicas the effective ceiling is
N × `ERP_AUTH_FAILURE_LIMIT`, and there is no shared backend, no cross-instance
coordination and no test for that case. A shared-store limiter (or a proxy-side
limit ratified by the operator) must exist **and be tested** before this control
may be described as production-grade.

### 6.1 Finding F-01 — limiter keys are never evicted (open, engineering)

`SlidingWindowLimiter._prune()` removes *timestamps* inside a key, but a key is
only pruned when it is accessed, and no sweep ever removes stale keys.

Measured in this pass:

```
5000 distinct usernames, one failure each -> len(_events) == 5000
after the 1 s window expires             -> still 5000 keys
a blocked() probe of one stale key       -> still 5000 keys
```

Reachability was confirmed against the real app, unauthenticated:

```
50 POST /api/v1/auth/token with random usernames -> all 401
limiter keys created by that caller             -> 50
```

Because the throttle key includes the caller-supplied username, an unauthenticated
client creates one permanent in-memory entry per request by varying the username.
Severity: **Medium-Low** (a few hundred bytes per key; unbounded growth,
slow-burn memory exhaustion, no eviction and no cap). Not a P0 regression — the
code is new in `49b9201c` and is not deployed anywhere — but it must be fixed
before the limiter is used in any long-running process: cap the number of tracked
keys (LRU) or sweep expired buckets on write, and add a regression test.

## 7. Request logs — credentials and personal data

Independent probe (`/tmp/verify_logs.py`) against a live app instance, driving a
successful login, a query-string request, an invalid-token request and a wrong
password:

| Check | Result |
|---|---|
| password value absent from every record | **PASS** |
| wrong-password value absent | **PASS** |
| bearer token (and its first 12 chars) absent | **PASS** |
| raw `Authorization` header absent | **PASS** |
| query-string value absent (`?search=ACME-SECRET-QUERY`) | **PASS** — only `path` is logged |
| request body absent | **PASS** |
| path recorded, query stripped | **PASS** |

Fields present across records: `method`, `path`, `status_code`, `duration_ms`,
`correlation_id`, `client_ip`, `username` (on authentication events only),
`code`/`detail` (a generic refusal message), `retry_after_seconds`, `peer_ip`.

**Personal data that *is* recorded, deliberately:** the account name and the
client IP address — necessary to make a credential-stuffing run attributable, and
retained in the durable log. This is proportionate for a security log but it is
personal data, so its retention period and disclosure belong to the
determinations in `docs/DATA_PROTECTION_COMPLIANCE.md` (**BLOCKED_EXTERNAL**).
No user-agent string is recorded at request level.

**Finding F-02 (Low, accepted-with-note):** every request is appended to the
*durable* security log when `ERP_SECURITY_LOG_PATH` is set (asserted by
`test_the_request_log_lands_in_the_durable_security_sink`). Volume is therefore
proportional to traffic. Rotation exists and is applied on each write
(`_rotate_security_log`, `ERP_SECURITY_LOG_MAX_SIZE` default 100 MiB,
`ERP_SECURITY_LOG_MAX_FILES` default 10, `security_audit.py:70-72,331`), but the
absolute disk budget and the off-host shipping of those files are operator
decisions, not code guarantees.

## 8. Effective-dated TVA and Timbre configuration

Independent probe (`/tmp/verify_tax.py`): **33 assertions, 33 passed**.

| Area | Verified behaviour |
|---|---|
| Threshold boundary | base `300.00` ⇒ `0.00` (inclusive); `300.01` ⇒ minimum `5.00` |
| Band boundaries | with the shipped default table: `30 000.00` ⇒ 300.00 (1 %), `30 000.01` ⇒ 450.00 (1.5 %), `100 000.00` ⇒ 1 500.00, `100 000.01` ⇒ 2 000.00 (2 %); with a supplied two-band table the same boundaries behave as configured |
| Floor / cap | configured minimum applies; `max_duty` caps the result |
| Payment method | `CASH` only; `BANK_TRANSFER`, `CHEQUE` and lower-case variants ⇒ `0.00` |
| Missing configuration | no rows at all ⇒ compiled constants, labelled `builtin-default-v1` (timbre) and `builtin_default` with `0.0,9.0,19.0` (VAT) |
| Date selection | invoice date selects the version: 2025-06-01 and 2025-12-31 ⇒ the 2025 rule; 2026-01-01 ⇒ the 2026 rule; the threshold follows the version |
| Future-only configuration | raises `TIMBRE_RULE_NOT_EFFECTIVE_ON_DATE` naming the configured versions |
| Invalid configuration | malformed bracket JSON ⇒ `TIMBRE_RULE_BRACKETS_INVALID`; a closed final band ⇒ same; negative rate and a non-list table are rejected by the same code path |
| VAT tables | a 2025-only rate is not allowed on a 2026 invoice; `active = 0` rows are ignored; another tenant's table is never borrowed |
| G50 traceability | box identities close (`E3B110 = 900.00`… asserted numerically), `E3B120`/`E3B130` mutually exclusive, `E2_droit_de_timbre` present **and** carrying a traceability line, `statutory_status` names the expert-comptable requirement, cross-tenant request refused |

### 8.1 Tax assumptions that require an Algerian expert-comptable's approval

None of the following is verified by any test; the tests verify only that the code
computes what is configured.

1. The TVA rate set `0 % / 9 % / 19 %` and which supplies each applies to.
2. The exemption classifications seeded (`NONE`, `EXEMPT`, `EXPORT`, `IMPORT`,
   `EU_FREE_TRADE`, `OUT_OF_SCOPE`) and the G50 box mapping implied by them.
3. The stamp-duty bracket table: 1 % up to 30 000 DZD, 1.5 % up to 100 000 DZD,
   2 % above.
4. The stamp-duty threshold of **300.00 DZD** and its inclusive boundary.
5. The minimum duty of **5.00 DZD** and the absence of a maximum.
6. That stamp duty applies to **cash payments only** (and the treatment of mixed
   or partially-cash settlements, which is not modelled).
7. That the duty base is `HT + TVA` (not `TTC` in another sense) and is rounded
   before the rate is applied.
8. Per-line rounding: line net rounded to the centime, TVA computed on the
   rounded line net, header = Σ rounded lines (not a rounded sum).
9. That TVA is charged on the discounted net, and the treatment of a discount
   granted after invoicing.
10. The G50 box definitions and tags (`E3B90`, `E3B91`, `E3B92`, `E3B100`,
    `E3B110`, `E3B120`, `E3B130`, `E2`) and the treatment of prior credit
    (*précompte*).
11. Effective dates of each fiscal-law version, and whether a backdated invoice
    must use the superseded text (the code refuses when no configured rule covers
    the date, rather than choosing).
12. The IFU regime and any special regime, which the partner model records but the
    tax engine does not differentiate.
13. Retention obligations for the generated preparatory workbook and its
    traceability list.

Owner: the engagement's expert-comptable. Required evidence: a dated, signed
review naming the legal reference (Finance Law article and date) for each item
above. Status: **BLOCKED_EXTERNAL** (`R-SCF`).

## 9. Container image — not built

Checked exhaustively: `docker`, `podman`, `nerdctl`, `buildah`, `kaniko`, `img`
are **all absent**; `/var/run/docker.sock` does not exist; `docker version` ⇒
`command not found`. The environment does not support building or running a
container image.

Status remains **IMPLEMENTED_NOT_VERIFIED**. What *is* verified is only the
artefact's declared properties, enforced by the six tests in
`tests/test_phase37_deployment_assets.py` (interpreter pinned to 3.11.17,
hash-pinned install, `USER erp` uid 10001, exec-form entrypoint, `/healthz`
healthcheck, loopback-only publish, `read_only`, `cap_drop: ALL`,
`no-new-privileges`, one replica, volumes for state and logs, bootstrap behind a
profile, `.dockerignore` excluding state and secrets, and every `ERP_*` variable
the code reads documented except the two secrets).

**Exact missing prerequisites to close this row:**

1. a container runtime or builder in the execution environment (Docker ≥ 24 with
   BuildKit, `podman`, or `buildah`), **or** a CI job with `docker/build-push-action`
   and a registry to push to;
2. a registry (or `docker save` artifact) to move the image to a host;
3. a host with durable volumes for `/var/lib/erp` and `/var/log/erp`;
4. then: `docker build -f ops/Dockerfile`, `docker compose up`, run the bootstrap
   profile, restore a verified backup into it, rotate a key inside it, and confirm
   the proxy's `X-Forwarded-For` matches `ERP_TRUSTED_PROXIES`.

None of these exist here, so nothing beyond the artefact properties is claimed.

## 10. Migration safety, backup/restore, security documentation, release gates

**Migration safety — VERIFIED (24 tests).**
`tests/test_phase38_migration_matrix.py`: 24 passed. It proves, per migrated
column and for all fourteen at once, that convergence happens; that a second open
leaves `sqlite_master` byte-identical; that row counts and the audit chain survive;
that legacy rows get `NULL` rather than a fabricated default; that a half-applied
upgrade is repaired; that the table rebuild does not use `executescript()`; and
that no migration opens its own connection.
*Limitation (finding F-05):* the "older schema" is **simulated** by dropping
modern columns from a current database, not by replaying a real historical dump.
That is a faithful proxy for the migration probes (which are structural), but a
real pre-Phase-29 database file is not in the repository and was not replayed.

**Backup/restore — VERIFIED IN CI, NOT VERIFIABLE HERE.**
The safe-restore path is refused on CPython 3.11.2 by design (measured: 20
failures, all this gate). In CI on the same commit, the Windows job step "Key
rotation, migration, backup/restore, logging and recovery tests" (3.12.10) and the
Linux "complete test suite with coverage" steps (`3.11`, `3.12`, resolved to the
runner's latest patches) all concluded
**success**. The code-level claim was also read: `restore_and_verify_backup`
accepts only `(3,11) ≥ 3.11.17` or `(3,12) ≥ 3.12.10`, extracts in a separate
process, rejects links/special members and non-canonical paths before writing, and
verifies the manifest and audit chain.

**Security documentation — VERIFIED PRESENT AND CONSISTENT.**
`docs/SECURITY_FINAL_AUDIT.md`, `docs/THREAT_MODEL.md`,
`docs/DATA_PROTECTION_COMPLIANCE.md`, `docs/INCIDENT_RESPONSE_PLAN.md`,
`docs/BACKUP_AND_RECOVERY_RUNBOOK.md`, `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` exist
at the remote head. An automated scan of all fourteen new documents plus the
README for `docs/…`, `tests/…`, `dz_erp/…`, `tools/…`, `ops/…` references found
**zero dangling paths**. A scan for unqualified claims (`certified`,
`fully compliant`, `production-ready`, `guaranteed`, `100 % secure`) returned only
negations or quoted advice — no unqualified assurance is asserted.

**Release-readiness gates — VERIFIED STRUCTURE.**
`docs/RELEASE_READINESS_MATRIX.md` has 15 machine-checked rows:
**6 Verified** (`R-AUTH`, `R-DUP`, `R-IMG`, `R-MIGR`, `R-OPS`, `R-TAXCFG`) and
**9 Blocked (external evidence required)** (`R-KMS`, `R-PROV`, `R-OFFHOST`,
`R-ALERT`, `R-DR`, `R-CONC`, `R-OCR`, `R-SCF`, `R-LAW`).
`tests/test_phase22_register_and_docs_consistency.py` → 15 passed (IDs unique,
8 columns per row, fixed status vocabulary, every blocked row names its external
dependency, cited paths exist). `tools/operational_readiness.py` → exit 1 with
actionable failures when unconfigured, as designed.

## 10.1 Findings register

| ID | Severity | Status | Finding | Remediation |
|---|---|---|---|---|
| F-01 | Medium-Low | **OPEN** | Limiter keys are never evicted; the key includes the caller-supplied username, so an unauthenticated client creates one permanent in-memory entry per request (measured: 5 000 keys retained, none released after the window expired; 50 requests → 50 keys). | Cap tracked keys (LRU) or sweep expired buckets on write; add a regression test. Until then the limiter must not run in a long-lived public-facing process. |
| F-02 | Low | **ACCEPTED-WITH-NOTE** | Every request is appended to the durable security log when `ERP_SECURITY_LOG_PATH` is set, so on-disk volume grows with traffic and the log contains usernames and client IPs. | Rotation exists (100 MiB × 10 by default); the retention period and off-host shipping are operator decisions recorded in the data-protection document. |
| F-03 | Info | **REVIEWED — no weakening** | The Phase 9 durable-log test was rewritten in `4eb5581f` from "one record per rejection" to per-event assertions. | Examined: the old assertion counted *all* records, which the new `HTTP_REQUEST` event necessarily breaks. The rewrite keeps `len(rejected) == len(rejections)` and unique correlation ids, drops the blanket `all(event == AUTH_REQUEST_REJECTED)` that no longer holds for the file, and **adds** an assertion that exactly `len(rejections)` request records exist with `status_code == 401` and that neither record type carries `company_id`. Net stricter. |
| F-04 | Medium (audit) | **OPEN** | `supplier_invoices` stores `amount_timbre_dzd` but **no** rule identity: `grep` over `database.py` shows no `timbre_rule_id`, `rule_version` or `vat_rate_id` column, and `timbre_rules` has no append-only trigger while nothing in code updates or deletes it. A posted invoice therefore cannot prove *which* configured rule produced its stamp duty; a later out-of-band edit of the configuration would silently change the traceability narrative. | Persist the resolved rule ids (and the resolved label) on the invoice at posting, and make the rule tables append-only (trigger) or immutable-by-version. This is a documentation/traceability gap, **not** a mis-computation: the amounts themselves are correct for the rule resolved at the time. |
| F-05 | Info (scope) | **ACCEPTED** | The migration matrix simulates "older" schemas by dropping modern columns; a real historical database dump is not in the repository. | Faithful for the structural properties proved (idempotency, NULL backfill, chain survival); replaying a real pre-Phase-29 dump remains a staging task. |

Neither F-01 nor F-04 blocks the Priority 0 fix under review; both should be
closed before any production deployment.

## 11. PR chain — read, not modified

| PR | Base | Head | Draft | Mergeable / state | CI on that exact head |
|---|---|---|---|---|---|
| #3 | `main` | `29645e5d` | no | MERGEABLE / CLEAN | run 40763416753 — 3/3 jobs success |
| #4 | `phase29/ocr-failclosed` | `99864bab` | **yes** | MERGEABLE / CLEAN | run 40775473917 — 3/3 success |
| #5 | `fix/phase29-source-invoice-consistency` | `c466cea7` | **yes** | MERGEABLE / CLEAN | run 40965006912 — 3/3 success |
| #6 | `fix/phase29-invoice-line-price-consistency` | `e4e47b3b` | **yes** | MERGEABLE / CLEAN | run 41064213366 — 3/3 success |
| #7 | `fix/phase29-invoice-discount-and-line-net-consistency` | `8b1b56c9` | **yes** | MERGEABLE / CLEAN | run 41129089741 — 3/3 success |

PRs #1 and #2 are already `MERGED` (historical, untouched). The chain is linear
(#3 → #4 → #5 → #6 → #7) and was not altered: no base, head, draft state, label or
title was changed by this pass.

## 12. Classification of the work under review

| Item | Classification | Basis |
|---|---|---|
| P0 duplicate-row integrity (policy, evidence binding, gates, recovery) | **VERIFIED** | 19 tests + 32 independent probe assertions + code reading |
| Login throttling (single-process) | **VERIFIED** (as implemented) / **STAGING-ONLY** (as deployable) | 16 tests, code reading; per-process storage |
| Request correlation + credential-free logging | **VERIFIED** | 16 tests + independent log probe (7/7 secret checks) |
| Effective-dated Timbre/TVA resolution | **VERIFIED** (mechanism) | 29 tests + 33 independent probe assertions |
| Legal correctness of rates, brackets, thresholds | **BLOCKED_EXTERNAL** | requires expert-comptable sign-off (§8.1) |
| Migration matrix | **VERIFIED** (simulated legacy shapes) | 24 tests; real historical dump not replayed |
| Backup/restore integrity | **VERIFIED IN CI** / not runnable locally | step-level CI evidence on `3.11`, `3.12` and Windows `3.12.10` |
| Container build and runtime behaviour | **IMPLEMENTED_NOT_VERIFIED** | no runtime available; prerequisites listed in §9 |
| Production-scale DR, concurrency, alerting | **BLOCKED_EXTERNAL** | no production data, no channel, no drill |
| KMS/HSM key custody, publisher provenance, authorised OCR corpus | **BLOCKED_EXTERNAL** | unchanged by this workstream |
| Law 18-07 / ANPDP determinations | **BLOCKED_EXTERNAL** | legal determination |
| Limiter key eviction (F-01) | **NOT_STARTED** | measured unbounded key retention |
| Persisting the resolved tax-rule version per invoice (F-04) | **NOT_STARTED** | resolved label is returned, not stored |

## 13. Reproduce this report

```bash
git rev-parse HEAD                       # 8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595
git rev-parse origin/arena/7f12d627-erp  # same
gh pr list --json number,baseRefName,headRefOid,isDraft,mergeable
gh api repos/Devmraustro/ERP/actions/runs/41129089741/jobs \
  --jq '.jobs[] | .name as $j | .steps[] | "\($j) :: \(.name) -> \(.conclusion)"'

python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
python -m ruff check dz_erp tests tools
python -m pytest -q --cov=dz_erp --cov-report=term      # 890 / 20 / 7 here
python -m pytest tests/test_phase34_ocr_duplicate_row_integrity.py \
                 tests/test_phase35_api_rate_limiting_and_request_logging.py \
                 tests/test_phase36_accounting_and_tax_configuration.py \
                 tests/test_phase37_deployment_assets.py \
                 tests/test_phase38_migration_matrix.py -q   # 94 passed

# independent probes used by this pass (not part of the repository)
PYTHONPATH=. python /tmp/verify_policy.py   # 32 passed
PYTHONPATH=. python /tmp/verify_tax.py      # 33 passed
PYTHONPATH=. python /tmp/verify_logs.py     # 7/7 secret checks
```

## 14. Conclusion

The six commits are linear, present on the remote head, and contained **only** in
PR #7 (Draft, mergeable, CI green on the exact head). Phases 35–38 and the
Priority 0 fix are in that PR.

The Priority 0 work does what it claims: duplicates cannot authorise an unreviewed
value, decisions are bound to the rows they covered, a flag cannot launder a
group, changed evidence re-opens the field, all three gates enforce the same rule,
refusals are audited, and a posting refusal is recoverable rather than terminal.
This was verified by reading the code, by an independent probe written for this
pass, and by the committed suite.

Against that, five findings are recorded (§10.1): F-01 (limiter keys never
evicted — open), F-02 (durable per-request log volume and PII — accepted with
note), F-03 (the one rewritten pre-existing test — reviewed, net stricter), F-04
(the resolved tax rule is not stored on the invoice — open) and F-05 (migration
legacy shapes are simulated — accepted). The rate limiter is staging-only until a
shared, **tested** limiter exists; the container image has never been built; and
the accounting, legal and operational gates remain external. On the evidence
available this work is **not** production-ready, and nothing in this report should
be read as saying otherwise.
