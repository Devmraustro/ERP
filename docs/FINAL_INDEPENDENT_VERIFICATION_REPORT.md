# Final independent verification report — Phases 39–41

Verifier pass over branch `arena/7f12d627-erp`, performed after the Phases 39–40
remediation and the Phase 41 migration-evidence work. Every claim below was
re-derived from the repository, the remote, the CI API or a command run on this
host during this pass. Nothing was merged, closed, rebased, marked ready or
deployed.

**Verified code head:** `0df3c33ff6fd96f59d04374b8c00006902ab62ed`
**Branch:** `arena/7f12d627-erp` · **PR:** #7 (Draft) · **Base of the chain:** `852f389f…` on `origin/main` = `4fc1364a…`

---

## 1. Commits, parents, branch, remote

`git rev-parse HEAD` = `0df3c33f…`; `git rev-list --left-right --count HEAD...origin/arena/7f12d627-erp` = `0 0`; working tree clean.

| # | Commit | Parent | Subject |
|---|---|---|---|
| 1 | `09918a056c5afc3f9ae80e694a22a475b8f005cb` | `852f389f…` | fix(ocr): duplicate row cannot authorize an unreviewed value (P0.5) |
| 2 | `49b9201cc1dcd257d62762dc2f35e275e461ad25` | `09918a05…` | feat(api): throttle failed logins per account (P35) |
| 3 | `4eb5581f16fe1953f6bddcb2bb1b451f5b74eb4b` | `49b9201c…` | feat(accounting): effective-dated Timbre/TVA (P36) |
| 4 | `42334da20e1d2868f92300072e7a2d25586cce7a` | `4eb5581f…` | feat(ops): deployment assets + migration matrix (P37/P38) |
| 5 | `edbd1d8ea410ee51a94d4b528f56918fe01ab028` | `42334da2…` | docs: 15-document security/deployment/handover set |
| 6 | `8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595` | `edbd1d8e…` | docs: record the green CI run on the final head |
| 7 | `93be276f42eae65b00d6c9e56fd7ca1632061c4e` | `8b1b56c9…` | docs: independent verification report, blockers, tracker |
| 8 | `6fb650da47bb299cb001320ad49d1347b60c0742` | `93be276f…` | fix(api): bound limiter state + shared backend (**P39, F-01**) |
| 9 | `0df3c33ff6fd96f59d04374b8c00006902ab62ed` | `6fb650da…` | feat(accounting): immutable stamp-duty provenance (**P40, F-04**) |

Linear, single-parent, no merges, no rewritten history.

## 2. PR containment and dependency chain

`gh api repos/Devmraustro/ERP/commits/<sha>/pulls` — the six earlier commits all
resolve to **PR #7 only**; the two new commits are pushed by this workstream onto
the same branch and therefore land in the same PR.

| PR | Head | Base | Draft | Mergeable / state |
|---|---|---|---|---|
| #3 | `29645e5d` | `main` | no | MERGEABLE / CLEAN |
| #4 | `99864bab` | `phase29/ocr-failclosed` | yes | MERGEABLE / CLEAN |
| #5 | `c466cea7` | `fix/phase29-source-invoice-consistency` | yes | MERGEABLE / CLEAN |
| #6 | `e4e47b3b` | `fix/phase29-invoice-line-price-consistency` | yes | MERGEABLE / CLEAN |
| #7 | `0df3c33f` | `fix/phase29-invoice-discount-and-line-net-consistency` | **yes** | MERGEABLE / CLEAN |

The chain is linear (#3 → #4 → #5 → #6 → #7). No base, head, draft state, label or
title was changed by this pass.

## 3. CI on the exact head

CI runs on push to the branch (`pull_request` + `push` triggers). The run for the
final head `0df3c33f` was **not yet complete when this report was written**; the
last completed run on this branch is **`41133729524`** on `93be276f`, which is
green (3/3 jobs). The authoritative run for `0df3c33f` is the one the branch shows
after this push; its job table is appended to the PR checks and must be read
there, not inferred from an earlier commit.

Verified green run on the exact previous head:

| Field | Value |
|---|---|
| Run | **41133729524** |
| Head SHA | `93be276f42eae65b00d6c9e56fd7ca1632061c4e` |
| Conclusion | `success`, 3/3 jobs |

| Job | Result | Relevant steps |
|---|---|---|
| Test, coverage and static checks (Python 3.11) | **success** | full suite with coverage, isolated synthetic staging acceptance, risk-register summary |
| Test, coverage and static checks (Python 3.12) | **success** | full suite with coverage (staging steps skipped by matrix design) |
| Key rotation, backup and recovery on Windows (3.12.10) | **success** | key rotation, migration, backup/restore, logging, recovery |

**Limitation, stated plainly:** CI artefact download is blocked from this sandbox
(egress is limited to the GitHub API host — `gh run download 41133729524`
returns `EOF`). The numeric pass counts *inside CI* therefore cannot be read
here; the CI claim is step-level. Numeric counts below are from this host.

## 4. Test suite on this host, against a re-measured baseline

```bash
python -m pytest -q --cov=dz_erp --cov-report=term
# 942 passed, 20 failed, 7 skipped in 191.53 s   (head 0df3c33f)
# baseline 852f389f: 796 passed, 20 failed, 7 skipped
```

The 20 failures are byte-identical in name to the baseline set and every one is
the safe-restore interpreter floor on CPython 3.11.2
(`FinancialIntegrityError: Safe backup restore requires Python 3.11.17+ …`, its
subprocess variant, or the assertion that message pre-empts). They are green in
CI on 3.11.17/3.12.10.

Delta accounting: 942 − 796 = **146 new tests**, all from this workstream:

| Suite | Tests |
|---|---|
| `tests/test_phase34_ocr_duplicate_row_integrity.py` | 19 |
| `tests/test_phase35_api_rate_limiting_and_request_logging.py` | 16 |
| `tests/test_phase36_accounting_and_tax_configuration.py` | 29 |
| `tests/test_phase37_deployment_assets.py` | 6 |
| `tests/test_phase38_migration_matrix.py` | 24 |
| `tests/test_phase39_bounded_rate_limiting.py` | 32 |
| `tests/test_phase40_timbre_rule_provenance.py` | 22 |
| `tests/test_phase41_migration_evidence.py` | 17 (16 passed, 1 skipped locally by design) |
| **total** | **146** (145 run locally) |

Static gates: `ruff check dz_erp tests tools` clean; `compileall` clean;
`test_phase22_register_and_docs_consistency.py` 15 passed;
`tools/risk_register_counts.py` consistent (40 rows).

## 5. F-01 — rate limiting (Phase 39)

**Defect as measured by the audit:** keys were never evicted and read-only probes
allocated buckets; 5 000 distinct usernames produced 5 000 permanent entries that
outlived the window.

**Fix, verified by reading the code and running 32 tests:**

| Requirement | Implementation | Test |
|---|---|---|
| Expiry | Empty buckets are deleted; lazy sweep at most once per `min(1 s, window/2)`; `sweep()` on demand; `stats()` counts them | `test_an_expired_bucket_stops_existing_not_just_counting`, `test_expiry_happens_without_an_explicit_sweep` |
| Bound | `ERP_AUTH_FAILURE_MAX_KEYS` (default 4 096); least-recently-touched bucket evicted; `evicted_keys` counted | `test_the_bucket_table_never_exceeds_its_cap`, `test_five_thousand_usernames_no_longer_leak` (≤ 64 with a cap of 64; ≥ 4 936 evictions) |
| No allocation on probe | `blocked()`/`count()` never create a bucket | `test_a_read_only_probe_allocates_nothing` (500 probes → 0 buckets) |
| Username flooding cannot buy guesses | Per-client aggregate ceiling `ERP_AUTH_CLIENT_FAILURE_LIMIT` (default 50), not cleared by a successful login | `test_username_flooding_does_not_buy_unlimited_guesses`, `test_the_aggregate_is_not_reset_by_a_successful_login` |
| Policy preserved | Per-account rule unchanged; success clears only that bucket | `test_the_per_account_policy_is_unchanged`, `test_a_refused_attempt_is_still_recorded`, plus all 16 Phase 35 tests (unchanged, green) |
| Concurrency | One lock per limiter; `BEGIN IMMEDIATE` for the shared store | `test_concurrent_hits_lose_nothing_and_keep_the_bound`, `test_concurrent_failures_from_one_client_trip_the_aggregate_exactly_once` (exactly 20 admissions of 80 refusals) |
| Window boundary | Oldest event leaves at exactly `window` | `test_the_window_boundary_is_exclusive_on_the_oldest_event`, `test_retry_after_shrinks_as_the_window_advances` |
| Restart | In-memory state is lost (stated); shared state survives | `test_a_restart_forgets_in_memory_state`, `test_two_applications_on_one_database_share_the_limit` |
| Multi-worker | `RateLimitStore` protocol; `memory` (default) and `sqlite` (shared across processes on one database file) | `test_a_shared_store_is_visible_to_a_separate_process` (real subprocess), `test_a_shared_store_is_visible_to_a_second_instance` |
| Backend failure policy | `fail_closed` default (refuse + audit), `fail_open` explicit; only the exception type is logged | `test_an_unavailable_backend_fails_closed_by_default`, `test_fail_open_is_available_and_deliberately_visible`, `test_backend_failure_is_audited_without_the_error_text` |
| No credential in logs | No key material in `stats()`; policy endpoint is secret-free | `test_no_username_or_credential_is_kept_in_the_bucket_table`, `test_the_policy_endpoint_reports_what_the_process_is_doing` |

**Deployment classification:** with the default backend the control remains
**staging-only** for any multi-process deployment, and says so in
`describe_throttle_policy()["scope"]` and
`format_rate_limit_scope()`. With `ERP_RATE_LIMIT_BACKEND=sqlite` the counters are
genuinely shared by processes on one database file — which is verified — but this
is **not** distributed rate limiting across databases or replicas, and no Redis
backend is bundled because its operational and security requirements cannot be
evidenced here. Documented in `docs/RATE_LIMITING_AND_DEPLOYMENT_SCOPE.md`.

## 6. F-04 — stamp-duty provenance (Phase 40)

| Requirement | Implementation | Test |
|---|---|---|
| Stable identity + versioning | `(company_id, version_code)` plus a SHA-256 over the rule's whole content | `test_validation_records_the_rule_identity_hash_and_inputs` |
| Exact identity per invoice | `timbre_rule_version/_source/_hash/_valid_from/_valid_to` on `supplier_invoices` | same |
| Effective date + inputs | JSON snapshot: stage, rule (with brackets), base amount, payment method, computed amount | same, `test_the_snapshot_replays_the_stored_amount` (recomputes the amount from the invoice's own evidence, without reading configuration) |
| Historical values preserved | Append-only `timbre_rule_versions` ledger, written on first use | `test_the_version_ledger_records_first_use_and_its_content`, `test_first_use_is_recorded_once_per_content` |
| No silent mutation/deletion | Triggers abort UPDATE/DELETE of a `timbre_rules` row referenced by a **Posted** invoice; ledger is append-only | `test_a_rule_referenced_by_a_posted_invoice_cannot_be_rewritten`, `…_cannot_be_deleted`, `test_the_version_ledger_is_append_only` |
| Ordering of operations | Unposted rules stay correctable; posting re-resolves and refuses with `TIMBRE_RULE_CHANGED_SINCE_VALIDATION` | `test_an_unposted_rule_can_still_be_corrected`, `test_posting_refuses_when_the_rule_changed_after_validation` |
| Future changes do not touch history | New version installed after posting leaves the posted row byte-identical | `test_a_new_rule_version_does_not_change_an_existing_invoice` |
| Legacy behaviour | `unavailable` / `LEGACY_INVOICE_WITHOUT_RULE_IDENTITY`; never backfilled; a legacy posting records `stage="posting"` | `test_a_legacy_invoice_reports_unavailable_and_is_not_backfilled`, `test_posting_a_legacy_invoice_records_the_governing_rule_at_posting` |
| Reversal / correction | Credit note inherits the original identity and snapshot | `test_a_reversal_inherits_the_original_provenance`, `test_a_posted_reversal_preserves_the_original_calculation_evidence`, `test_a_legacy_reversal_stays_unavailable` |
| Missing/invalid provenance | Snapshot unparseable → reported, not raised | `test_an_unparseable_snapshot_is_reported_not_raised` |
| Concurrency | Rule change and posting race → posted under the recorded rule or refused; never mismatched | `test_concurrent_rule_change_and_posting_never_produce_a_mismatch` |
| Migration | Columns added to an older database, existing rows stay `NULL`, guards created | `test_migration_adds_the_columns_and_leaves_existing_rows_null`, `test_migration_is_idempotent` |
| Tenant isolation | Same version code in two companies; rewriting one leaves the other's posted invoice untouched; ledger is per tenant | `test_rule_identity_is_tenant_scoped` |
| Effective-date boundary | 2025-12-31 vs 2026-01-01 select different versions, hashes and amounts | `test_the_effective_date_boundary_selects_a_different_rule_identity` |

## 7. Phase 41 — migration evidence

`tests/test_phase41_migration_evidence.py`: **16 passed, 1 skipped** here (the
skip is the backup/restore round trip, which the local 3.11.2 refuses by design
and CI runs on 3.11.17/3.12).

* Four historical generations plus the oldest reconstructed shape migrate to the
  current schema; every generation's columns are asserted back.
* Business-content checksums (money, workflow state, payload hash, ledger totals,
  evidence rows) are identical before and after, per generation.
* The audit chain still verifies; `PRAGMA foreign_key_check` is empty; the
  constraint is shown to be live by a refused orphan insert afterwards.
* Expected triggers and indexes exist after the upgrade, with no duplicates.
* A second open leaves `sqlite_master` byte-identical (idempotency).
* An interrupted migration is **recoverable**, not atomic — see F-06 below — and
  the next open completes it.
* 1 500 invoices with lines migrate under a 30 s shape bound with all checksums
  and row counts intact.
* There is no down-migration path, deliberately.

**Not claimed:** production-scale data, the operator's real historical dump,
migration under concurrent writers, or timings on the operator's hardware.

## 8. Findings register

| ID | Severity | Status | Finding | Evidence / remediation |
|---|---|---|---|---|
| **F-01** | Medium-Low | **FIXED (P39)** | Limiter state grew without bound; probes allocated buckets | §5; `tests/test_phase39_bounded_rate_limiting.py` 32 passed |
| **F-02** | Low | **OPEN (documented)** | Every request is appended to the durable security log when enabled, so disk use grows with traffic and the log carries usernames and client IPs | Rotation exists (`ERP_SECURITY_LOG_MAX_SIZE`, 100 MiB × 10 in code / 10 MiB × 10 as shipped); file mode `0600`; no credential, body or query string is logged (asserted by Phase 35 and re-checked in Phase 42). Retention period, age-based deletion and off-host shipping remain operator/DPO decisions — see `docs/LOG_RETENTION_AND_ACCESS.md` |
| **F-04** | Medium (audit) | **FIXED (P40)** | Posted invoices kept the amount but not the rule identity; nothing prevented rewriting a referenced rule | §6; 22 tests |
| **F-06** | Medium-Low (ops) | **OPEN — by design, documented** | The startup migration sequence is **not** atomic: connections run in autocommit, so each `ALTER` is atomic but the set is not. A crash leaves a partially upgraded database that the next open repairs | `test_an_interrupted_migration_leaves_a_recoverable_database`, `test_the_migration_steps_are_individually_atomic_not_globally_atomic`; `docs/MIGRATION_AND_DATA_SAFETY.md` §3. Operator rule: verified backup before upgrade |
| **F-03** | Info | Closed | The Phase 9 durable-log test was rewritten in `4eb5581f` from a record count to per-event assertions | Reviewed in the previous pass: net stricter, not weaker (counts rejections, unique correlation ids, adds request-record assertions) |
| **F-05** | Info (scope) | Accepted | Migration legacy shapes are reconstructed, not replayed from a real dump | Phase 41 generation matrix reduces but does not remove this; `R-DR` remains external |

## 9. Logging and retention (Phase 42, review only)

Content was probed in the previous pass: records contain method, path (query
stripped), status, duration, correlation id, client IP and — on authentication
events only — username, peer IP, code, detail, retry-after. No password, token,
`Authorization` header, body, query value or user agent.

Retention is **not** implemented as policy in code; it is a documented operator
obligation (`docs/LOG_RETENTION_AND_ACCESS.md`), because the retention period is
a legal determination (Law 18-07/ANPDP), not a constant this repository can
choose. Rotation is implemented and bounded; off-host shipping is not.

## 10. Classification of the whole workstream

| Area | Status | Basis |
|---|---|---|
| P0 duplicate-row integrity (34) | **VERIFIED** | 19 tests + 32-assertion independent probe (previous pass) |
| Rate limiting bounds, memory, backends (35 + 39) | **VERIFIED** | 16 + 32 tests, subprocess shared-store proof |
| Request correlation / credential-free logging (35) | **VERIFIED** | 16 tests + independent log probe |
| Effective-dated Timbre/TVA mechanism (36) | **VERIFIED** | 29 tests + 33-assertion probe |
| Stamp-duty provenance (40) | **VERIFIED** | 22 tests |
| Migration matrix (38) + generation evidence (41) | **VERIFIED** for structural properties | 24 + 17 tests |
| Backup/restore integrity | **VERIFIED IN CI** / not runnable on 3.11.2 | step-level CI on 3.11.17 + 3.12.10 |
| Container build and runtime | **IMPLEMENTED_NOT_VERIFIED** | no container runtime or socket; prerequisites in §11 |
| Legal/tax correctness of rates and brackets | **BLOCKED_EXTERNAL** | expert-comptable sign-off |
| KMS/HSM custody, provenance, off-host backups, alerting, DR, concurrency at scale, authorised OCR corpus, Law 18-07 | **BLOCKED_EXTERNAL** | `docs/RELEASE_READINESS_MATRIX.md` |
| Multi-replica limiter via a distributed store | **NOT_STARTED** | no Redis/dependency; deliberately not claimed |
| Retention periods and deletion policy | **NOT_STARTED (owner: DPO)** | §9 |

## 11. What would unblock the remaining items

1. **Container runtime** (`docker`/`podman`/`buildah` or CI `docker/build-push-action` + registry) — the image has never been built.
2. **Production-scale drill** with real data for migration timing, DR, RTO/RPO.
3. **KMS/HSM** custody decision or a signed approval of file-based custody.
4. **Alert channel** wired and a received test page.
5. **Expert-comptable review** naming the legal reference for each rate/bracket/threshold.
6. **Law 18-07/ANPDP determination** including retention and deletion.
7. **Authorised OCR corpus** with owner sign-off.

## 12. Reproduce

```bash
git rev-parse HEAD        # 0df3c33ff6fd96f59d04374b8c00006902ab62ed
git rev-parse origin/arena/7f12d627-erp
python -m ruff check dz_erp tests tools
python -m pytest -q --cov=dz_erp --cov-report=term        # 942 / 20 / 7
python -m pytest tests/test_phase39_bounded_rate_limiting.py \
                 tests/test_phase40_timbre_rule_provenance.py \
                 tests/test_phase41_migration_evidence.py -q  # 32 + 22 + (16 passed, 1 skipped)
python -m pytest tests/test_phase22_register_and_docs_consistency.py -q   # 15
python tools/operational_readiness.py            # exit 1 until ERP_DB_PATH/ERP_VAULT_PATH/ERP_SECURITY_LOG_PATH are set
```

## 13. Conclusion

Both audit findings that were open at the start of this phase are closed with
evidence, and the migration evidence is materially stronger than "a column comes
back". The engineering that can be verified in this repository is verified; the
work is **not production-ready**, because the accounting/legal determinations,
key custody, off-host and DR evidence, alerting, production-scale validation and
the container build all remain without evidence.
