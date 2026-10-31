# PR stack review — #3 → #4 → #5 → #6 → #7

Independent, sequential review of the five-PR stack on `Devmraustro/ERP`.
Everything below was re-derived from the remote, the GitHub API and commands run
on this host during this pass. No previous session report was trusted, no PR was
merged, closed, rebased, retargeted, marked ready or deployed.

**Reviewed code heads:** `29645e5d` (#3) · `99864bab` (#4) · `c466cea7` (#5) ·
`e4e47b3b` (#6) · `c23e2d65` (#7)
**Delivery head when this review began:** `c23e2d65` (worktree clean, remote and
local tree identical).

> The review documents you are reading are themselves committed on top of
> `c23e2d65`. That commit is documentation-only, is *not* part of what was
> reviewed, and starts a new CI run that is reported separately in §9.

---

## 1. Repository state (re-derived, not inherited)

| Fact | Value |
|---|---|
| `origin/main` | `4fc1364a9504fab1153ea44e753f354f00023428` |
| Branch under review | `arena/7f12d627-erp` @ `c23e2d654fdf936cb7c0ba11e5da307ad488381d` |
| Local checkout | fast-forwarded to the remote head; `git status` clean; tree hash identical to the remote |
| Merge commits in the stack | none — every head has exactly one parent |
| Rewritten history | none observed (each PR head is a descendant of the previous head) |

Remote branch heads used by the stack:

| Branch | Head |
|---|---|
| `main` | `4fc1364a` |
| `phase29/ocr-failclosed` | `29645e5d` |
| `fix/phase29-source-invoice-consistency` | `99864bab` |
| `fix/phase29-invoice-line-price-consistency` | `c466cea7` |
| `fix/phase29-invoice-discount-and-line-net-consistency` | `e4e47b3b` |
| `arena/7f12d627-erp` | `c23e2d65` |

## 2. PR state, bases and mergeability

| PR | Title (short) | Base branch @ SHA | Head SHA | Draft | Mergeable | Files | ± | Commits |
|---|---|---|---|---|---|---|---|---|
| #3 | Phase 29 fail-closed OCR review | `main` @ `4fc1364a` | `29645e5d` | **no** | MERGEABLE / CLEAN | 20 | +2972 / −133 | 1 |
| #4 | bind approval to reviewed source | `phase29/ocr-failclosed` @ `29645e5d` | `99864bab` | **yes** | MERGEABLE / CLEAN | 2 | +185 / −2 | 5 |
| #5 | compare line unit prices | `fix/…-source-invoice-consistency` @ `99864bab` | `c466cea7` | **yes** | MERGEABLE / CLEAN | 10 | +716 / −38 | 1 |
| #6 | decide discount, derive line net | `fix/…-invoice-line-price-consistency` @ `c466cea7` | `e4e47b3b` | **yes** | MERGEABLE / CLEAN | 10 | +908 / −42 | 1 |
| #7 | integration audit + phases 33–42 | `fix/…-discount-and-line-net-consistency` @ `e4e47b3b` | `c23e2d65` | **yes** | MERGEABLE / CLEAN | 53 | +14382 / −291 | 19 |

**Base correctness — verified pairwise by ancestry, not by reading the UI:**

```
4fc1364a is ancestor of 29645e5d : YES
29645e5d is ancestor of 99864bab : YES
99864bab is ancestor of c466cea7 : YES
c466cea7 is ancestor of e4e47b3b : YES
e4e47b3b is ancestor of c23e2d65 : YES
```

Every PR's `base.sha` reported by the API equals the previous PR's head. The
expected order #3 → #4 → #5 → #6 → #7 holds exactly; **no mismatch, nothing to
stop for**.

Reviews recorded on GitHub: **0 on all five PRs**. Branch protection for `main`
could not be read with the available token (`403 Resource not accessible by
integration`); the repository rulesets endpoint returns `[]`. The merge gate
configuration is therefore **unknown**, not "absent" — see §8.

## 3. CI on the exact head of every PR

One run per head, all `pull_request`, all **`success` 3/3 jobs**:

| PR | Run | Head SHA | 3.11 | 3.12 | Windows |
|---|---|---|---|---|---|
| #3 | [40763416753](https://github.com/Devmraustro/ERP/actions/runs/40763416753) | `29645e5d` | success | success | success |
| #4 | [40775473917](https://github.com/Devmraustro/ERP/actions/runs/40775473917) | `99864bab` | success | success | success |
| #5 | [40965006912](https://github.com/Devmraustro/ERP/actions/runs/40965006912) | `c466cea7` | success | success | success |
| #6 | [41064213366](https://github.com/Devmraustro/ERP/actions/runs/41064213366) | `e4e47b3b` | success | success | success |
| #7 | [41142731746](https://github.com/Devmraustro/ERP/actions/runs/41142731746) | `c23e2d65` | success | success | success |

Each job's step list was read from the API: `Ruff`, `byte-compile`, `hash-verified
install`, `dependency graph`, `full suite with coverage`, `synthetic staging
acceptance` (3.11 and Windows), `risk-register summary`. All `success`.

**Limitation, unchanged from earlier passes:** CI logs and artefacts cannot be
downloaded from this sandbox (`results-receiver.actions.githubusercontent.com`
and the results blob host are not reachable; `gh api …/artifacts/<id>/zip`
returns that host). The CI claim here is **step-level**, not per-test-count.

## 4. Local regression evidence per PR (this host, pinned lockfile)

Each head was checked out in a throw-away worktree (`git worktree add --detach`,
removed afterwards) and the full suite run with the exact pinned dependency set
from `requirements.lock.txt` on CPython **3.11.2**.

| PR | Passed | Failed | Skipped | Collected |
|---|---|---|---|---|
| #3 | 695 | 20 | 6 | 721 |
| #4 | 698 | 20 | 6 | 724 |
| #5 | 708 | 20 | 6 | 734 |
| #6 | 719 | 20 | 6 | 745 |
| #7 | **960** | **20** | **8** | 988 |

The failure sets were compared as sorted name lists, not by count:
`diff` of each PR's failure list against #7's is **empty for all four** — the
same 20 tests fail at every head. All 20 are the safe-restore interpreter floor
(`Safe backup restore requires Python 3.11.17+ or Python 3.12.10+`), which is the
same set that fails at the pre-existing baseline. **No PR in the stack
introduces a new failing test at any point in the chain.**

Growth is monotonic and additive: +3 (#4), +10 (#5), +11 (#6), +241 (#7 —
phases 32–42 and the documentation set).

`ruff check dz_erp tests tools` on `c23e2d65`: **clean**.

## 5. Per-PR review

### PR #3 — Phase 29 fail-closed field review (`4fc1364a..29645e5d`)

**Architecture / data model.** New `ocr_field_reviews` table (attributed
decisions, `CHECK`-constrained vocabulary) and a new `conflict_values_json`
column on `ocr_extracted_fields`, added by an idempotent `_migrate_review_policy`.
The raw-extraction trigger is dropped and re-created so legacy databases protect
the new column too — `CREATE TRIGGER IF NOT EXISTS` alone would have left an old
body in place. That reasoning is correct and is the kind of migration detail that
usually gets missed.

**Authorization / tenant isolation.** Every new query is scoped by
`company_id` taken from the verified credential (`auth.company_id`), never from
the request body. `list_field_reviews` goes through `_require_company_scope`.
Cross-company attempts are audited by the secure wrapper.

**Transaction boundaries / audit.** The gate
(`_document_review_gate`) is evaluated **inside the caller's `BEGIN IMMEDIATE`**
transaction at submit and approval, so the verdict cannot race a concurrent
field edit (no TOCTOU window). Posting re-runs the same gate in the transaction
that writes the journal entry. Refusals use a documented *commit-then-refuse*
pattern (`_RefusedAttempt`) so the audit event survives the rollback of the
attempted write — the refusal is evidence, which is the right call for an
accounting system.

**Policy correctness.** Duplicate extraction groups block until a decision
covers **every** row id (`covered_ids != set(duplicate_row_ids)` → unresolved);
`FLAG`/`NOT_PRESENT` deliberately cannot launder a group. Confidence is
tri-state, `not_measured` is never treated as a score, and no configuration can
auto-accept. This matches the PR's claims; I verified it by reading
`_field_evaluation` and `evaluate_document_review_policy`, and by exercising the
duplicate path directly.

**Tests.** 24 at this head, 48 by #7. They assert business outcomes
(application state, audit rows, refused transitions), not implementation
details. Assertions on the gate are made against the real workflow, not mocks.

**Regressions.** Full suite green apart from the interpreter floor; the
"protected operations" pin in `test_phase8` was raised 26 → 30 across the stack
(new endpoints are enumerated, not ignored) — correct.

**Verdict: no blocking findings.** Two observations, both minor: the trigger
drop/re-create window (§6, F-06a) and one test that asserts on a docstring
(`test_the_shared_store_scope_is_not_overclaimed`, added in #7) rather than on
behaviour.

### PR #4 — bind approval to reviewed source values (`29645e5d..99864bab`)

Adds `_source_invoice_mismatches` and calls it only when the review is complete,
comparing invoice number (normalised), date, currency, HT/TVA/timbre/TTC,
supplier NIF (digits only) and line count/quantities. Fail-closed, audited as
`INVOICE_<ACTION>_BLOCKED_SOURCE_MISMATCH`.

Sound, and the deliberate details are the right ones: a missing optional timbre
is treated as zero *for this comparison only*, other missing totals fail closed,
an absent extracted-line set is **not** read as "invoice must have zero lines",
and an unparseable amount produces a distinct `…_UNPARSEABLE` code instead of
being skipped. Rows are read inside the caller's transaction.

**Verdict: no blocking findings.** One nit: the comparison aligns invoice lines
to source lines **by position** (`ORDER BY id` vs `page, id`); the comment
states this and a swap test pins it, so a reordering is refused rather than
silently rematched — acceptable, but it is a refusal an operator must be able to
diagnose, and the error text does name the line index.

### PR #5 — compare reviewed line unit prices (`99864bab..c466cea7`)

Makes the unit price a separately decided review element (`line_unit_price`,
6-decimal canonical form), adds `line_prices` to the evaluation, and compares it
exactly with `supplier_invoice_lines.unit_price_dzd`. A quantity decision cannot
substitute for a price decision because the decisions are keyed by a different
`field_name`. If the price-evaluation list length disagrees with the source-line
list, `LINE_PRICE_EVIDENCE_INCONSISTENT` blocks rather than guessing a mapping.

**Verdict: no blocking findings.** Nit: `if raw_source_price is None or …` after
the `to_decimal(str(None))` call is unreachable (the `TypeError` is caught
above); harmless dead condition.

### PR #6 — decide the discount, derive the line net (`c466cea7..e4e47b3b`)

Third decided element (`line_discount`, 2-decimal), invoice-side bounds enforced
(`0 ≤ d ≤ 100`, never clamped), and the line net re-derived from the *decided*
quantity/price/discount and compared exactly with `line_ht_dzd`. It also fixes a
real arithmetic bug in `modify_draft_invoice_line`, which previously recomputed
a line as `qty × pu` and silently dropped the discount.

**The one change that needed care:** `compute_invoice_payload_hash` now includes
`discount_pct`, which changes the digest algorithm. I checked the consequences
rather than assuming them:

* posting compares `payload_sha256` with **`approved_payload_sha256`**, both
  *stored* values (`procurement_stock.py`, the `APPROVAL_PAYLOAD_HASH_MISMATCH`
  check) — so an invoice approved under the old algorithm cannot silently
  mismatch itself after upgrade;
* duplicate detection does not use the hash at all — it is the
  `uq_posted_supplier_invoice` unique index on
  `(company_id, supplier_id, normalized_invoice_no, fiscal_year)`;
* every place that *recomputes* the hash does so on a modification path that
  resets the invoice to `NeedsReview` and clears the approval.

So the algorithm change is **contained**: no stored invoice becomes unpostable
or duplicately detectable. Worth stating explicitly because it would otherwise
look like a silent migration hazard.

**Verdict: no blocking findings.** Nit: the assertion
`len(rows) >= 10  # 8 header + qty + price + discount` did not go to 11 when the
discount row was added, so the count assertion no longer pins the new element
(the stronger per-field assertions in the same test do).

### PR #7 — phases 32–42 on top of the chain (`e4e47b3b..c23e2d65`)

The large one: 53 files, +14 382/−291, 19 commits, phases 33 through 42 plus the
documentation set and the deployment assets.

**Architectural / data-model changes.** Invoice lifecycle (correct/revalidate,
approval binding to review evidence, reversal credits), effective-dated tax
configuration (`timbre_rules`, `taxes`, `resolve_timbre_rule`,
`resolve_vat_rates`), stamp-duty provenance (six columns + append-only
`timbre_rule_versions` ledger + five triggers), bounded rate limiting
(`rate_limit.py`), request logging middleware, and the ops assets
(Dockerfile/compose/env example + their guard tests).

**Authorization / tenant isolation.** `revalidate_supplier_invoice` checks role
(`clerk|accountant|admin`) and tenant, and refuses every state outside
`REVISABLE_INVOICE_STATES` with an explicit reason ("an approved or posted record
is never rewritten — use a reversal credit note; a rejected invoice is
terminal"). Approval keeps the segregation-of-duties check. The rate-limit
policy endpoint is admin-only and returns no key, username or counter.
`decimal_utils.to_decimal` now refuses `NaN`/`±Infinity` at the single
conversion point — a genuine defect fix (a `NaN` previously reached a money
column as TEXT).

**Database / migration compatibility.** Additive `ALTER TABLE`s, an atomic
table rebuild for the OCR confidence migration (explicit `BEGIN IMMEDIATE`,
deliberately *not* `executescript`), and idempotent second-open behaviour. The
non-atomicity of the *sequence* is real and is documented as F-06 rather than
papered over (§6).

**Timbre immutability and provenance.** Every invoice validated under a
configured rule now stores version code, source, content hash, effective window
and a calculation snapshot; first use of a (version, hash) pair is written to an
append-only ledger; `UPDATE`/`DELETE` of a rule referenced by a **posted**
invoice aborts; a legacy invoice reports `unavailable` with the explicit note
that the producing rule is unknown and deliberately not guessed, and posting one
records the governing rule with `stage="posting"` — a fact about now, not a
retroactive claim. Reversal inherits the original provenance.

I checked the one loophole that would matter: reversal does **not** move the
original invoice out of `Posted` (there is no `Reversed` state; the credit note
is a separate `IN_REFUND` row), so the mutation guard keeps protecting the rule
after a reversal.

**Rate limiting.** Bounded keys, eviction, lazy sweep, no allocation on
read-only probes, per-client aggregate that a successful login cannot reset,
`fail_closed` default, and an honest `per-process` scope. The shared sqlite
store is a real cross-process boundary — a subprocess test proves it, and my own
probe (§6, R-02) confirms concurrent writers. No distributed claim is made and
none is implied.

**Regressions.** Full suite green apart from the interpreter floor at this head
too, including the pre-existing accounting/inventory suites (3-way match,
over-billing, immutability, trial balance).

**Verdict: no blocking findings, four non-blocking findings (R-01, R-03, R-05,
R-06) and one process finding (R-07).** Details in §6.

## 6. Findings

Severity is about consequence if shipped as-is, not about effort to fix. `R-`
IDs are stable and are referenced by the merge plan.

| ID | Severity | PR | Finding | Evidence |
|---|---|---|---|---|
| **R-01** | Medium‑low | #7 | `RequestThrottle.last_backend_error` is set on a backend error and **never cleared on success**. After one transient store error, every later *ordinary* policy refusal is audited as `RATE_LIMIT_BACKEND_UNAVAILABLE` for the remaining life of the process. The limit still fails closed (no security weakening) — but the audit record becomes wrong, and this system's own standard is that a refusal is evidence. | Probe on this host: one induced `OSError` → `last_backend_error` set; backend healthy afterwards; after 3 recorded failures the 4th `login_blocked` returns `(True, 60)` **and** `last_backend_error` is still populated, so `api._authenticate` would emit the backend-outage event for a normal limit hit. |
| **R-02** | Medium (test gap) | #7 | Concurrency is tested for the **memory** store (8 threads) and the shared sqlite store is tested **sequentially** across processes. No test exercises concurrent *writers* on the shared store, which is exactly where `BEGIN IMMEDIATE` contention would appear. | `grep`: the only `subprocess` use in `test_phase39` is the sequential visibility test. My own probe: 4 processes × 150 `hit()` on one DB → 600/600 rows stored, 0 exceptions, 0.83 s wall. The design holds at this scale; the *test* does not exist. |
| **R-03** | Low‑medium (design) | #7 | `SqliteRateLimitStore.blocked()` opens a **write** transaction (`db.transaction()` → `BEGIN IMMEDIATE`) to answer a read. Every authentication attempt therefore serialises on the application database's write lock alongside business writes. Correct (fail-closed) and fine at the shipped single-worker default; it caps throughput when the shared backend is used for the multi-worker case it exists for. | Code: `blocked()` wraps its `SELECT` in `self._db.transaction()`; `ERPDatabase.transaction()` executes `BEGIN IMMEDIATE` and holds a process-wide `RLock`. |
| **R-04** | Low | #7 | A client-supplied `X-Request-Id` is accepted verbatim (no length or charset bound) and echoed into the durable log and response header. No injection is possible (the log line is `json.dumps`-escaped), so this is log-hygiene, not a vulnerability. | `api.py` middleware; `security_audit.py` writes `json.dumps(entry)`. No test bounds the header. |
| **R-05** | Medium (operational/audit) | #7 (phase 40) | There is **no application-mediated, audited way to add a timbre rule version.** The only writer is the chart-template seed (one fixed `version_code`, `ON CONFLICT DO NOTHING`); there is no tool and no endpoint. Once a version is referenced by a posted invoice the trigger correctly forbids updating it, so a tax-law change requires **direct SQL** — which appends no audit event and cannot be attributed. Overlapping effective windows are also accepted silently (`resolve_timbre_rule` picks the latest `valid_from`). The evidence side is strong; the configuration side has no controlled door. | `grep "INSERT INTO timbre_rules"` → `localization.py` seed only; no endpoint/tool matches; `resolve_timbre_rule` sorts applicable rows by `valid_from` descending without an overlap check. |
| **R-06** | Low (docs) | #7 | Two shipped documents still claim the resolved rule label is **not** persisted on the invoice, which Phase 40 made false, and point at a tracker row that now reads DONE: `docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §7 and `docs/TECHNICAL_REPORT.md:136`. Under-claiming rather than over-claiming, but it contradicts the code and the register. | `grep`: both files; `database.py` has `timbre_rule_version`/`timbre_snapshot_json`; `PROJECT_COMPLETION_TRACKER.md` row 13b is DONE. |
| **R-07** | Informational (process) | #7 | Tests gated on CPython ≥ 3.11.17 (all backup/restore paths, including the Phase 41 round trip) **never execute on a 3.11.2 host**. A defect (`Path(report["restored_db"])` — the report returns a live `ERPDatabase`) passed a locally-green run and was caught only by CI. The fix is in `4863062` and CI is green, but the lesson stands: on this sandbox, "skipped locally" means "unverified locally". | `4863062` commit body; interpreter gate in `security_audit.restore_and_verify_backup`. |
| **F-06a** | Low (narrow window) | #7 (#3 trigger) | Part of F-06 but worth naming separately: `_migrate_review_policy` performs `DROP TRIGGER` and `CREATE TRIGGER` as two autocommitted statements on every startup. A crash between them leaves the raw-extraction immutability trigger **absent** until the next successful open. Recovery is automatic and the window is bounded by crash → restart. | Probe: fresh open → trigger present; simulated crash after `DROP` → trigger gone; next open → trigger present again. |

### What I looked for and did **not** find

Recorded because a review that only lists problems is incomplete:

* **No weakened or deleted tests.** Every test function removed in a diff is
  still defined at HEAD (relocated, not dropped). The assertions that changed got
  *stricter*: the `phase9` log test was narrowed per event and gained two new
  assertions on the request records; the `phase29` gate tests gained per-field
  decision assertions when the discount became a decided element.
* **No tenant-isolation hole** in the new endpoints: scope always derives from
  the credential.
* **No approval bypass** via the new revalidation endpoint: illegal states are
  refused with reasons, and the payload hash is recomputed so a stale validation
  can never authorise posting.
* **No silent historical rewrite:** legacy invoices are reported `unavailable`,
  never back-filled.
* **No float money path** introduced; `NaN`/`Infinity` are now refused.
* **No new external dependency** anywhere in the stack (so no new licence
  question).

## 7. Cross-stack result

The chain is **coherent and strictly additive**. Each PR closes the gap the
previous one named explicitly (source values → line prices → discount and
derived net), and #7 builds the lifecycle, tax, limiter, deployment and
documentation layers on top. The gates compose: field review (P29) → source
consistency (P30/P31) → payload-hash approval binding (P33) → posting re-check
(P34) → rule provenance (P40).

The end-to-end property that matters most — *an invoice cannot reach the ledger
on evidence nobody reviewed, or on a stamp-duty rule nobody can name* — is
enforced at three points, in the same transaction that moves the record forward.

## 8. What remains unverified or blocked

| Item | Status | Why |
|---|---|---|
| Human review of any PR | **BLOCKED** | 0 reviews recorded on #3–#7. An agent review is not a substitute for the accountable reviewer. |
| Branch-protection / required-check configuration | **BLOCKED (unknown)** | `403` on the protection endpoint with this token; rulesets empty. Somebody with admin rights must state whether merges are gated. |
| Distributed rate limiting across replicas | **NOT_STARTED** (by design) | Only a shared-file backend exists; never claimed. |
| Timbre rule configuration path with attribution (R-05) | **FIXED_AND_VERIFIED** | Was raw SQL. Now an audited, two-person administration path (DRAFT → approve, segregation of duties, overlap refused by default) at `POST /api/v1/tax/timbre-rules` and `…/{version}/approve`; `tests/test_phase46_timbre_rule_administration.py` (35 tests). See §7. |
| Production-scale behaviour (timing, contention, DR) | **BLOCKED_EXTERNAL** | Needs real data, a real host and a drill. R-02/R-03 are staging-scale observations only. |
| Container image build/run | **NOT_STARTED** | No container runtime here; the assets are property-tested, never executed. |
| Legal/accounting determinations (SCF, rates, retention, ANPDP) | **BLOCKED_EXTERNAL** | Unchanged by this review. |

## 9. CI for the review-delivery commit

The review documents were committed on `c23e2d65` as commit `359b111`. Its CI
run is **`41153210738`** (event `pull_request`, **`success` 3/3 jobs**: Linux
3.11, Linux 3.12, Windows 3.12.10). It is a documentation-only change: it does
not alter the reviewed code head `c23e2d65`, and the CI evidence cited in §3 for
each PR stands on its own run. A later documentation-only commit that records
this run ID is not separately tracked — a docs-only change cannot affect code
evidence, and tracking it would recurse.

## 10. Verdict per PR

| PR | Blocking findings | Non-blocking | Ready for explicit merge authorization |
|---|---|---|---|
| #3 | none | F-06a (informational) | Yes — lowest risk, already non-draft |
| #4 | none | — | Yes, after #3 |
| #5 | none | nit (dead condition) | Yes, after #4 |
| #6 | none | nit (count assertion) | Yes, after #5 |
| #7 | none (no data-integrity or security blocker) | R-01, R-02, R-03, R-04, R-05, R-06, R-07 | **Conditionally** — R-01 and R-05 need an explicit disposition (fix or accept in writing) before merge; see the plan's required actions |

Reading the #7 row precisely: R-01 and R-05 are **not** security or
data-integrity blockers — the limiter still fails closed, and no invoice can be
posted on a rule it cannot name. They are (R-01) an audit record that can state
the wrong cause, and (R-05) an operational gap with no controlled configuration
path. Both are the kind of thing this project has so far treated as a finding to
fix rather than to ship silently, so they are surfaced as a decision for the
authorising human, not quietly accepted here.

"Ready for authorization" means *nothing in this review forbids the merge*. It
does not mean the code is production-ready, and it does not substitute for the
human review and the external gates above.

---

## 7. Remediation of R-01 … R-07 (Phase 45)

The findings in §6 were remediated on the existing work branch
`arena/7f12d627-erp`. **Nothing was merged, retargeted, closed, rebased,
force-pushed or deployed**, and no published history was rewritten. This section
is the disposition of each finding; §6 remains as written so the review of
record is not silently edited into agreement with its own fixes.

| Finding | Disposition | Evidence |
|---|---|---|
| **R-01** stale backend-error flag | **FIXED_AND_VERIFIED** | `RequestThrottle` records errors per call (`_call_errors`, `_begin_call`); `current_backend_error` reports the live state and `backend_health()` separates current from historical failure. `describe_throttle_policy` exposes it. Tests: `test_a_backend_failure_explains_only_the_decision_it_affected`, `test_a_successful_call_clears_the_stale_error_but_not_the_history`, `test_an_ordinary_limit_rejection_is_never_reported_as_a_backend_failure`, `test_concurrent_failure_and_recovery_leave_no_thread_with_a_stale_error` (`tests/test_phase43_limiter_hardening.py`). Fail-closed behaviour is unchanged and separately asserted. |
| **R-02** no concurrent-writer test | **FIXED_AND_VERIFIED** | `tests/test_phase43_limiter_hardening.py` adds multi-process writers on one SQLite file (`…lose_no_increment`), concurrent hits at the boundary (`…allow_exactly_the_limit`), concurrent expiry/cleanup (`…stay_bounded`), a write lock held by another process (`…is_waited_out_not_dropped`), a backend interruption that fails closed and recovers, and high-cardinality bounding. Measured, recorded in the test output: `hit()` behind a held lock waits ~2036 ms and then succeeds — no lost increment, no unbounded state. |
| **R-03** `blocked()` takes a write lock | **FIXED_AND_VERIFIED** | `SqliteRateLimitStore.blocked()` reads through the new `ERPDatabase.read_probe()` instead of `BEGIN IMMEDIATE`. Measured locally: `blocked()` 0.794 ms vs `hit()` 2.331 ms sequentially; under a lock held by another process `blocked()` returns in ~1 ms while `hit()` waits ~2 s. **The claim is bounded**: `read_probe` still takes the process `RLock`, so contention is *reduced, not eliminated*, and no test asserts it is lock-free. Correctness is pinned by `test_the_read_optimisation_does_not_change_what_is_counted`, `test_the_probe_sees_committed_events_and_not_uncommitted_ones` and `test_the_probe_does_not_bypass_in_process_transaction_isolation`; the rate limit is not weakened anywhere. |
| **R-04** unbounded `X-Request-Id` | **FIXED_AND_VERIFIED** | `dz_erp/request_id.py`: `MAX_REQUEST_ID_LENGTH = 128`, `^[A-Za-z0-9._:-]+$`, `sanitize_request_id` returns a server-side uuid4 for anything else. A rejected value is never echoed and never logged (not truncated-in-part); the 429 path obeys the same rule; two clients may share an id and neither request is dropped. `tests/test_phase47_request_id_boundary.py` (19 tests) covers over-long, empty, whitespace, control, non-ASCII, RTL-override, zero-width, terminal-escape and duplicate cases, plus the "no request content in the header" property. |
| **R-05** no audited timbre rule path | **FIXED_AND_VERIFIED** (code) / **BLOCKED_EXTERNAL** (values) | DRAFT → approve path with segregation of duties (`TIMBRE_RULE_SOD_VIOLATION`), explicit overlap policy (refused by default; `supersede` may only narrow a window no posted invoice cites), one audit event per step inside the same transaction, tenant scoping and role checks enforced in the service, and three endpoints (`GET`/`POST /api/v1/tax/timbre-rules`, `POST …/{version_code}/approve`). No value is ever defaulted: an incomplete rule is refused (`TIMBRE_RULE_VALUE_REQUIRED`). `tests/test_phase46_timbre_rule_administration.py` (35 tests). The **rates themselves** remain the expert-comptable's determination — unchanged and still `BLOCKED_EXTERNAL`. |
| **R-06** stale documentation | **FIXED_AND_VERIFIED** | Three documents claimed the resolved rule identity was not persisted and tracked it as `NOT_STARTED`; Phase 40 had made that false. Corrected in `ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §7 (rewritten to describe the six persisted columns, the append-only version ledger, the immutability triggers, the posting-time re-resolution and the two real limits: legacy invoices are not back-filled, reversals inherit provenance), `TECHNICAL_REPORT.md` and `RISK_REGISTER.md`; the administration path is documented in §3.1 and in `ALGERIAN_SCF_LOCALIZATION_SPEC.md` §3. `tests/test_phase22_register_and_docs_consistency.py` still passes. |
| **R-07** interpreter gate unverified locally | **FIXED_AND_VERIFIED** (rule) / **BLOCKED_EXTERNAL** (host 3.11.2) | The gate now exists once: `security_audit.safe_restore_supported`, consumed by the library and by every gated test. The drift that caused the finding is real and is now caught: `test_phase32` carried `>= (3, 11, 17) and < (3, 13)`, which **admits 3.12.0-3.12.9 — versions on which restore correctly refuses**; CI only missed it because `setup-python` resolves the newest 3.12. `tests/test_phase48_restore_interpreter_matrix.py` (32 tests + 1 environment-gated) pins every boundary tuple, asserts 3.12.0-3.12.9 are refused, asserts the restore path raises rather than degrading on every unsupported tuple, asserts the module contains exactly one version gate and that `restore_and_verify_backup` calls the predicate, and **bans re-duplication** by scanning the test tree with an AST check. The gate itself is untouched and still fails closed; this host is CPython 3.11.2, so the round trip executes on CI (3.11/3.12) and is recorded here as environment-blocked locally, never as passing. |

### 7.1 A defect found *by* the remediation

Making the R-02 tests honest surfaced a real product defect: concurrent
`ERPDatabase` construction raced the schema migrations and failed with
`sqlite3.OperationalError: trigger trg_ocr_field_preserve_raw already exists`.
`_migrate_schema` now runs its whole sequence inside one `BEGIN IMMEDIATE`;
`_migrate_ocr_confidence` stays outside it, because it toggles
`PRAGMA foreign_keys`, which is a no-op inside a transaction. Regression test:
`test_concurrent_process_startup_does_not_race_the_schema_migrations` (five
processes starting at once). This was not in the review's finding list and would
have appeared as an intermittent production start-up failure.

### 7.2 What this closure does **not** claim

* the tax values, the G50 box mapping and the legal reading of the timbre
  brackets remain `BLOCKED_EXTERNAL` (expert-comptable sign-off);
* rate limiting is still **per process** (shared SQLite is single-host by
  construction) — no distributed claim is made;
* the restore round trip is verified on CI's 3.11/3.12, not on this sandbox's
  3.11.2;
* no container image was built here, and nothing was deployed.

### 7.3 Exact evidence

| Item | Value |
|---|---|
| Branch | `arena/7f12d627-erp` (the existing work branch; no other branch touched) |
| Base | `4fc1364a` (`main`); stack `#3` … `#7` unchanged, all still **draft**, none merged or retargeted |
| Remediation commit | `d4131c6` — R-01, R-03, R-04, R-05, R-06, R-07 rule, and the migration-race fix |
| Evidence commit | `0a46cc9` — restore round trip asserted against the real report contract; closure docs |
| Flake-or-defect commits | `a092996`, `848afee` — repeat the start-up race, report the failing child's cause |
| Defect fix | `1873f0ec` — atomic audit-key publish (+ `tests/test_phase49`) |
| CI (green, 3/3) | run **41197722914** on **`e5f91080`** — https://github.com/Devmraustro/ERP/actions/runs/41197722914 (this is the last code-bearing commit; the commit after it is documentation only and carries its own green run) |
| CI (green, 3/3, earlier) | run **41196478922** on `1873f0ec` — https://github.com/Devmraustro/ERP/actions/runs/41196478922 |
| CI (red, superseded) | run **41194325402** on `d4131c6` — 3.11 failed on the Phase 48 round trip, which is how the guessed report keys were caught; run **41194981129** on `0a46cc9` — 3.11 failed once on the start-up race, which is what led to `1873f0ec` |
| Local suite (CPython 3.11.2) | **1070 passed, 20 failed, 9 skipped**; the 20 failures are the identical interpreter-floor restore set as the pre-remediation baseline (3.11.2 < 3.11.17) |
| New tests | phase43 **17**, phase46 **35**, phase47 **19**, phase48 **32 + 1 environment-gated**, phase49 **7** — **110 passed, 1 skipped** together |
| R-07 on CI | the round trip **cannot silently skip on CI** (it fails when the interpreter cannot perform the restore), so the green 3.11 and 3.12 jobs are positive evidence that it executed on both supported lines, on real 3.11.x and 3.12.x interpreters |
| Static checks | `ruff check dz_erp tests tools` → clean; `tools/risk_register_counts.py` → consistent (40 rows); docs-consistency suite **15 passed** |

Two failures during this work were reported rather than smoothed over, because
each one was the R-07 lesson repeating:

1. the Phase 48 round trip asserted *guessed* report keys and passed locally only
   by skipping; CI 3.11 — where the path really runs — failed it. It is now
   asserted against the report's actual contract, and **on CI an unsupported
   interpreter fails the test instead of skipping it**, so a green CI job proves
   the round trip executed rather than that it was quietly dropped;
2. the R-02 start-up test failed once on CI and never locally. It was traced to
   a real non-atomic step (`_resolve_audit_secret` published an empty key file
   before writing its material) rather than dismissed as a flake — see
   `tests/test_phase49_audit_key_bootstrap_race.py`. That CI failure was **not**
   reproduced on this host (1 680 concurrent starts, zero failures), so the
   causal link is stated as the mechanism that matches, not as a reproduction.

---

## 8. Final human review preparation (independently re-verified, 2026-10-31)

Everything below was re-derived from GitHub and from the checked-out tree in a
fresh session — not carried over from the remediation report. Every claim names
its command or run.

### 8.1 State, exactly

| Item | Verified value | How |
|---|---|---|
| `07e65782` full SHA | `07e65782edc4d43b6b3952c699cb2d8d1f1769e4` | `git rev-parse` |
| Remote branch head | same SHA — **no commits exist after the reported state** | `git fetch --prune` + `git rev-parse origin/arena/7f12d627-erp` |
| Working tree | **clean**; the tree is byte-identical to that commit (0 differing paths, 0 untracked) | `git status --porcelain`; `git read-tree <sha>` + `git diff` = empty |
| `main` | `4fc1364a9504fab1153ea44e753f354f00023428` | `git rev-parse origin/main` |
| Ancestry | `4fc1364a → 29645e5d → 99864bab → c466cea7 → e4e47b3b → 07e65782`, each an ancestor of the next, **0 merge commits** | `git merge-base --is-ancestor`, `git rev-list --merges --count` |
| CI on the head | run **41198403599**, `event=pull_request`, `headSha=07e65782…`, **3/3 success** | `gh run view` |

The session note "clean tree at `07e65782`" was reproduced from scratch: the
sandbox's local ref pointed at `main` while the worktree already held the
remediated content, so the ref was restored with `update-ref` + `read-tree`
after proving by hash comparison that no file differed. No file was modified,
reset, checked out or cleaned to achieve this.

### 8.2 CI run 41198403599 — every job, every step

| Job | Conclusion | Steps |
|---|---|---|
| Test, coverage and static checks (Python 3.11) | success | 14 executed / 0 failed; **staging acceptance ran**, risk-register validation ran |
| Test, coverage and static checks (Python 3.12) | success | 14 executed; staging acceptance + risk-register steps are `if: 3.11`-only and therefore skipped **by design** |
| Key rotation, backup and recovery on Windows | success | Python **3.12.10** (last Windows binary installer), key-rotation/migration/backup/logging tests + staging acceptance |

Static check (ruff) and byte-compile pass in all three jobs. The Windows job is
the only place a 3.12.10 interpreter with the strict tar preflight runs.
Raw logs and artifacts are not retrievable from this sandbox (the signed blob
URLs return `EOF`), so per-step evidence above is the API's step list; the
authoritative pass/fail of individual tests is the job conclusion.

### 8.3 Security and data-integrity re-review (independent probes)

Each item below was re-run in this session against `07e65782`; the probe is a
throwaway script, the *property* is what the committed tests also assert.

| Area | What I did | Result |
|---|---|---|
| **Audit-key atomic publication** | Confirmed the target FS is **ext4** and that `os.link` works there, so the atomic path is the live one; published 20 keys while a reader polled every 0.5 ms | **Never observed an empty key file.** `_publish_key_file` returns the winner's bytes; permissions `0600`; no temporary left behind |
| same, no-hardlink fallback | Forced `os.link` to raise in every worker (FAT/network-mount case), 12 rounds × 8 processes | 96/96 started; **exactly one key per database**, no split brain, no adapter error |
| **Concurrent migration locking** | 15 rounds × 12 processes starting on one fresh DB | 180/180 started; **0** `already exists` / `database is locked` |
| **Rate-limiter contention (multi-process)** | 6 processes × 30 auth failures on one shared SQLite file, limit 40 | `allowed = 40` exactly, **0 over-limit**, 180 rows stored (no lost increments), no errors, `current_error = None` |
| **R-01 classification** | Induced a store `OSError`, then success, then ordinary refusals | outage → refused (fail-closed) with `current_error` set and `failure_count=1`; next success → `current_error is None` **and `failure_count` stays 1**; ordinary refusals → `current_error` still `None` |
| **R-03 read path** | Another process held `BEGIN IMMEDIATE` for 4 s | `blocked()` returned in **1.4 ms**; `hit()` waited **4 043 ms**. Read sees committed state; contention reduced, **not** eliminated (process `RLock` still taken) |
| **Tax endpoints: authz** | Clerk / accountant / other-tenant accountant / admin against all three routes | clerk **403** on create, approve **and** list; unauthenticated **401**; all three routes `Depends(_authenticate)` |
| **Tax endpoints: SoD** | Author approves own draft | **403** (`TIMBRE_RULE_SOD_VIOLATION`) — a draft stays `DRAFT` and governs nothing |
| **Tenant isolation** | Tenant-2 accountant approves tenant-1's version; tenant-2 listing | **403**; tenant 2 has zero rules. Deliberate: a missing version and another tenant's version are **indistinguishable** (403, not 404), so the endpoint does not leak cross-tenant existence |
| **Audit persistence** | After create + approve | `TIMBRE_RULE_VERSION_CREATED` (actor 1) and `TIMBRE_RULE_VERSION_APPROVED` (actor 2) present, **chain valid**, written in the same transaction as the change |
| **Historical stability** | Added `LF-2027` with different brackets, re-resolved 2026 and 2027 | 2026 still `LF-2025`, duty unchanged (`100.00`); 2027 → `LF-2027` (`600.00`). Rewrite/delete of a cited rule refused; ledger append-only; posting refuses a rule changed after validation (`test_phase40`, 22 tests) |
| **Tenant scoping in SQL** | Read every new statement in `localization.py` | Every `SELECT`/`UPDATE`/`INSERT` filters `company_id`; the role check happens **inside** the transaction (`_timbre_admin_user`), so a direct service caller is refused exactly like an HTTP client |
| **Request-id / log privacy** | 11 hostile ids through the real ASGI stack | Valid ids adopted and echoed; over-long, whitespace, `<script>`, quotes, backslash, `;`, `%`, ANSI escape — all replaced by a uuid4, **none appears in any log record**, every logged `correlation_id` is valid, and the log context carries only `method/path/status_code/duration_ms/client_ip` (no query string, headers or body) |
| **Backup/restore on supported interpreters** | Ran the suite on this host; inspected the interpreter | This host is CPython **3.11.2**, which lacks `tarfile.data_filter` **entirely** — the round trip is not merely gated, it is impossible here. Faking `version_info` does not help: the second guard refuses an interpreter with no data filter. The round trip therefore executes **only** on CI's 3.11/3.12, and the matrix test **fails (not skips) under `CI`** on an unsupported interpreter, so a green CI job is positive evidence that it ran |

### 8.4 Residual risks found in this review

These are **not** blockers and none was fixed here, because a review pass must
not introduce unverified code changes this close to authorization.

| # | Observation | Impact | Recommendation |
|---|---|---|---|
| **R-08** | `_publish_key_file` fsyncs the key file but not its **directory** after `os.link` | A machine crash in the microseconds between `link` and the next directory sync could lose the key file while audit events signed with it persist. Start-up then **fails loudly** (`AuditKeyUnavailableError`) instead of minting a replacement — an availability incident, never a silent integrity break | Add `os.open(dirname, O_RDONLY)` + `os.fsync` after the link. Low risk, but needs its own test; do it in a follow-up, not here |
| **R-09** | The no-hardlink fallback still creates the key file with `O_CREAT|O_EXCL` **before** writing its 44 bytes; readers are protected by `_read_published_key`'s 50 × 20 ms retry, not by atomicity | A reader racing a very slow first write on such a filesystem waits up to ~1 s, then fails with an actionable message. Never adopts partial material | Documented in the code; acceptable where `os.link` is unavailable |
| F-06a (informational) | The migrations drop and recreate `trg_ocr_field_preserve_raw` | Previously a window where another connection saw no trigger; now inside one `BEGIN IMMEDIATE`, so the recreated trigger becomes visible atomically with the rest of the migration | None needed; note the improvement |
| #5 nit | `if raw_source_price is None or …` at `dz_erp/procurement_stock.py:219` is unreachable (harmless dead condition) | None | Cosmetic; leave or fix separately |
| #6 nit | `assert len(rows) >= 10` at `tests/test_phase29_ocr_review_policy.py:394` did not grow to 11 with the discount row, although its comment now describes 11 | The count no longer pins the new element; the per-field assertions in the same test do | Reviewer may accept or ask for 11 |

### 8.5 Reviewer checklist, per PR

Merge order is `#3 → #4 → #5 → #6 → #7`; each PR must be green **on its own head**
(the five SHAs are verified green in §8.6).

**#3 — OCR fail-closed + field-level review** (`main` ← `29645e5d`, 20 files, +2972/−133)
- Read first: `dz_erp/review_policy.py` (new, 467 lines) — the decision model; `dz_erp/intake_ocr.py` (+582) — extraction, duplicate suppression, confidence provenance.
- High risk: **whether `NeedsReview` can ever be skipped**. Check the fail-closed path from OCR uncertainty to an unpostable invoice, and that a duplicate extraction row cannot authorize an unreviewed value (`test_phase29_ocr_review_policy.py`).
- High risk: `dz_erp/database.py` (+100) schema additions and their migration; `dz_erp/security_audit.py` (+71) — new audited events.
- Ask: does any new column default to a value that could be read as "reviewed"?

**#4 — bind approval to reviewed source values** (`29645e5d` ← `99864bab`, 2 files, +185/−2)
- Read: `dz_erp/procurement_stock.py` (approval guard) and the 32-line test addition. Small, surgical: prove the approval compares *reviewed* values, not extracted ones.
- High risk: a `NULL`/absent reviewed value must refuse, not pass.

**#5 — unit price consistency** (`99864bab` ← `c466cea7`, 10 files, +716/−38)
- Read: `dz_erp/review_policy.py`, `dz_erp/procurement_stock.py` (+203), `dz_erp/intake_ocr.py`.
- High risk: **money comparison** — exact `Decimal` equality against `supplier_invoice_lines.unit_price_dzd` with 2-decimal DZD; no tolerance, no float.
- Open nit F-06/#5 above (dead condition).

**#6 — discount + derived line net** (`c466cea7` ← `e4e47b3b`, 10 files, +908/−42)
- Read: same three modules; bounds `0 ≤ discount ≤ 100` **refused, never clamped**; line net re-derived from decided qty/price/discount.
- High risk: a clamp anywhere would silently change a monetary amount.
- Open nit: the `>= 10` count assertion (§8.4).

**#7 — phases 32–42 + this remediation** (`e4e47b3b` ← `07e65782`, 62 files, +18515/−315)
This is the PR that needs the most attention. Suggested reading order:
1. `dz_erp/rate_limit.py` (new, 816) — **R-01/R-03**; `_begin_call`/`current_backend_error`/`backend_health`, `SqliteRateLimitStore.read_probe` path, fail-closed default.
2. `dz_erp/request_id.py` (new, 78) — **R-04**; the accept/replace rule and why rejected values are never echoed.
3. `dz_erp/localization.py` (+991) — **R-05**; DRAFT→approve, SoD, overlap policy, `supersede` refusal on cited rules. Read `_timbre_admin_user` and every `company_id` predicate.
4. `dz_erp/api.py` (+332) — the three tax routes; 403/422/409 mapping; **403-not-404** for a missing version is deliberate (no cross-tenant leakage).
5. `dz_erp/database.py` (+425) — `BEGIN IMMEDIATE` around the migration sequence; **atomic audit-key publish**; `timbre_rules` review columns + append-only ledger.
6. `dz_erp/security_audit.py` (+52) — `safe_restore_supported` (R-07) and the restore gate; the interpreter floor is **not** removed.
7. `dz_erp/procurement_stock.py` (+1623) — the largest change: invoice lifecycle, provenance capture, reversal inherit.
- High risk: any new `UPDATE`/`DELETE` reachable on `timbre_rules` or `supplier_invoices.timbre_*`; any place a refusal became an allow.
- Ask specifically: **does anything in this PR weaken a rate limit, bypass the review state, or let a single person both author and activate a tax rule?** (Verified by me as "no" — see §8.3 — but this is the question the reviewer should independently answer.)

### 8.6 Per-PR CI and review state

| PR | Head SHA | CI on that exact head | Reviews | Findings open | Draft? |
|---|---|---|---|---|---|
| #3 | `29645e5d` | 3/3 success | 0 | none | **no — ready for merge** |
| #4 | `99864bab` | 3/3 success | 0 | none | yes |
| #5 | `c466cea7` | 3/3 success | 0 | nit (dead condition) | yes |
| #6 | `e4e47b3b` | 3/3 success | 0 | nit (count assertion) | yes |
| #7 | `07e65782` | 3/3 success (run 41198403599) | 0 | R-01…R-07 **closed**; R-08/R-09 recorded as residuals | yes |

All five are `MERGEABLE / CLEAN`. **Zero human reviews exist on any PR** — that
is the outstanding gate, and it cannot be satisfied by CI.

### 8.7 The retargeting requirement, and why it is not optional here

GitHub retargets a stacked PR's base **only when the base branch is deleted after
the merge** ([GitHub changelog, 2020-05-19](https://github.blog/changelog/2020-05-19-pull-request-retargeting/);
[changing a PR's base branch](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/changing-the-base-branch-of-a-pull-request)).
Two facts make the manual step mandatory in *this* repository:

1. `delete_branch_on_merge` is **`false`** (verified: `gh api repos/Devmraustro/ERP`), so merging `#3` will **not** delete `phase29/ocr-failclosed`;
2. that branch is `#4`'s base, so `#4` keeps pointing at it, and merging `#4` while it is based on that branch would land its commits **in the branch, not in `main`**.

Therefore the verified procedure is **§3 option A**: merge → retarget the next PR
to `main` → confirm the base shows `main` → merge. **Stop immediately** if, after
a merge, the next PR's base has *not* changed and it is not explicitly retargeted;
and stop if GitHub has auto-retargeted a PR to something other than `main`
(it would mean a base was deleted, which this plan forbids). Also note the
repository allows squash, merge and rebase merges: **use merge commits**. A squash
rewrites every commit in the PR, which would force a rebase of the branches above
it — and rebasing published history is forbidden by this task.

Branch protection on `main` could not be read with this token
(`403 Resource not accessible by integration`), so the plan must not assume
protection rules are absent or present: the merging human should confirm required
checks and review requirements in the repository settings before the first merge.

---

## 9. Second independent pass (2026-10-31) — evidence over reports

This section is a **fresh** review of the same five PRs, run on
`b679bc496b8053384ab40bcbd86c5db474140f42`. Nothing above was taken on trust:
every claim below was re-derived by running code on this host. Where an earlier
report and this pass disagree, **this section governs**. No PR was merged,
retargeted, closed, rebased or force-pushed to produce it, and no product code
was changed — findings are recorded, not patched, because the task authorizes
review and documentation only.

### 9.1 State and PR chain, re-verified

| Item | Value (verified this pass) |
|---|---|
| Review head | `b679bc496b8053384ab40bcbd86c5db474140f42` (= remote head, clean worktree) |
| `origin/main` | `4fc1364a9504fab1153ea44e753f354f00023428` |
| Chain | `4fc1364a → 29645e5d (#3) → 99864bab (#4) → c466cea7 (#5) → e4e47b3b (#6) → b679bc49 (#7)` |
| Bases | #3 → `main`; #4 → #3's branch; #5 → #4's; #6 → #5's; #7 → #6's (each one step, no gaps) |
| Mergeability | all five `MERGEABLE` / `CLEAN` |
| Reviews | 0 on every PR (no human approval yet — as expected) |
| CI | 3/3 success on the exact head of every PR, and on `b679bc49` (`41229742863`) |

The bases still form a strict chain, so §8.7's rule stands unchanged: merge with
merge commits, **retarget each next PR to `main` immediately after each merge**,
and stop if a base moves by itself.

### 9.2 Accounting and G50, re-derived end to end

New this pass: the full posting → declaration → reversal path was exercised with
real posted entries rather than by reading code (PO → GRN → invoice → G50 →
audited reversal → G50).

| Invariant | Probe result |
|---|---|
| Line net `round_dzd(qty × pu × (100 − disc)/100)` | `1×100.00@0 %→100.00`, `@100 %→0.00`, `3×3.333→10.00`, `7×14.285714@33.33 %→66.67` |
| Discount bounds | `100.01 %` → `-0.01` and `-0.01 %` → `100.01` are **refused** (not clamped) at creation |
| Stock posting at GRN | `31` debit 10,000.00 / `380` credit 10,000.00 (`STJ/1/00001`) |
| Invoice posting | `380` debit 10,000.00 (`E3B91_base`), `4456` debit 1,900.00 (`E3B91_balance`), `401` credit 11,900.00 |
| Clearing nets to zero at equal prices | `380` net = **0.00**; stock `31` = 10,000.00; trial balance balanced |
| G50 with a CASH timbre invoice | `E2 = 11.90`, `E3B91 = 190.00`, `E3B100 = 0.00`; `E3B110 = E3B90 + E3B91 + E3B92` ✅; payable ⇄ carry-forward mutually exclusive ✅ |
| Timbre not double-counted | the invoice books timbre to `642`/`TIMBRE_PURCHASE`; `E2` counts it once. No double count observed |
| Reversal (audited path) | credit note `AVOIR-FAC-2026-0001`, entry `RAVOIR/2026/00001`, dated **2026-10-31** (issue period), `1201.90/1201.90`, `reversal_of_invoice_id = 1` |
| G50 after reversal | February unchanged; **October** shows `E2 = -11.90`, `E3B91 = -190.00` — the credit note nets in the period it is issued, not retroactively. Trial balance balanced, audit chain valid |

The negative `E2` in October is arithmetically right for a credit note but is a
*filing-format* question (whether the DGI accepts a negative droit de timbre box
or expects it as a separate reduction line). That is a policy question, not a
code defect: **BLOCKED_EXTERNAL**, owner the accountant of record, evidence
required = the DGI G50 notice in force for the period.

### 9.3 Findings

Severity is about money, data or security impact — not diff size.

| # | Severity | Finding | Evidence |
|---|---|---|---|
| **F-R2-1** | **High** | **Timbre cap: the spec asserts a 10,000.00 DZD ceiling that the code does not apply.** `docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md:66` documents "2.0 % for base > 100,000.00 DZD (**capped at 10,000.00 DZD**)", but `_builtin_timbre_rule()` (`dz_erp/localization.py:550`) passes `max_amount_dzd=None`, and the seeded `timbre_rules` row (`localization.py:~395`) stores `max NULL`. The top bracket is therefore uncapped in code | `resolve_timbre_rule` → `max_amount_dzd: None`; base 500,000.00 → 10,000.00 accepted; **base 600,000.00 declaring the documented cap is refused** `TIMBRE_MISMATCH:declared=10000.00!=expected=12000.00_for_CASH` |
| **F-R2-2** | **Medium** | **Reversal idempotency replays without comparing the request hash.** `reverse_posted_invoice()` (`dz_erp/procurement_stock.py:2514`) and `_create_reversal_request()` (`:2727`) both compute `req_hash` (`:2504`, `:2717`) and then, on a hit, return the cached response **without** `idem["request_sha256"] != req_hash`. The same key with a *different reason* is silently reported as a successful replay. Contrast the three sites that get this right: `:822` (RECEIVE_GOODS), `:2236` (POST_INVOICE), `:3133` (POST_REVERSAL) | Second call, same key, different reason → `{'idempotent_replay': True, 'reason': 'supplier dispute'}` — the *original* reason echoed, the new request silently dropped |
| **F-R2-3** | **Medium** (pre-existing, not a stack regression) | **A within-tolerance price difference leaves a permanent balance on the stock-clearing account.** The GRN books `31 / 380` at receipt cost; the invoice books `380` at *invoice* price. When those differ and the difference is allowed by `price_tolerance_dzd`, nothing posts the variance, so `380` keeps a residual for ever | PO/receipt 1,000.00, invoice 1,010.00, tolerance 15.00 → `380` net **+100.00**; invoice 990.00 → **−100.00**. Trial balance stays balanced, so only a clearing-account review catches it. Verified **identical at base `4fc1364a`** |
| **F-R2-4** | **Low** (pre-existing) | **Negative amounts are accepted through validation and posted.** There is no positivity rule for `amount_ht/tva/ttc`, `unit_price` or `qty`; only the debit = credit check applies | A `−1,000.00` HT / `−1,000.00` TTC / 0 % invoice validated, was approved and **posted** as `600` debit `−1000.00` / `401` credit `−1000.00` |
| **F-R2-5** | **Low** (test) | **PR #6's row-count assertion is weaker than its own comment.** `tests/test_phase29_ocr_review_policy.py:394` asserts `len(rows) >= 10` while the comment describes 8 required header fields + qty + price + discount = 11, and the test actually produces **16** rows — six decisions could go missing and it would still pass | `assert 16 == 999` under a temporary probe bound |
| **F-R2-6** | **Low** (cosmetic) | PR #5's `if raw_source_price is None or …` (`dz_erp/procurement_stock.py:219`) is unreachable: `to_decimal(str(None))` raises `ValueError`, which the surrounding `except` already catches. Dead condition, no behavioural effect | `ValueError` raised before the `None` test is reached |
| **F-R2-7** | **Low** (coverage) | **`price_tolerance_dzd` has no test coverage at all** — `grep` finds zero occurrences of the parameter in `tests/`. The feature that permits F-R2-3's divergence is untested end to end | `grep -rn price_tolerance tests/*.py` → no matches |

Notes on F-R2-1: this is **not** a claim about Algerian law. The defect is that
the repository's own specification states a ceiling the repository's own code
does not apply, on a money-affecting path, with no test above a 500,000.00 base.
Fixing it either way is a one-line change, but *which* way is a decision, not a
guess:

- **If the ceiling is real** → set `max_amount_dzd` on both the built-in rule and
  the seeded row, and add a regression test at base 600,000.00 asserting
  `10,000.00` (F-R2-RT1).
- **If it is not** → delete the parenthetical at
  `docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md:66` and say plainly that the top
  bracket is uncapped, plus a test pinning 600,000.00 → 12,000.00 (F-R2-RT2).

Until the accountant of record rules on it, this is **BLOCKED_EXTERNAL** with the
evidence requirement "the effective-dated timbre rule text for the period" —
not an invented figure.

### 9.4 R-08 and R-09 — re-investigated, still open

Both were re-tested behaviourally instead of re-read. Probes used
`_resolve_audit_secret`, `_read_published_key` and `_publish_key_file` against a
real DB with a real signed audit event.

| Scenario | Observed behaviour | Verdict |
|---|---|---|
| Key file lost (R-08's crash window) with audit events on disk | `AuditKeyUnavailableError: The audit key file … is missing but … contains audit events` — **no replacement key is minted**; restoring the file makes `verify_audit_chain` valid again | Fail-closed as claimed: availability incident, not a silent integrity break |
| Empty key file (R-09's killed writer) | `KeyMaterialUnavailable: Key file … is empty` in ~0.00 s; the empty file is left untouched, never adopted or overwritten | Fail-closed |
| Reader racing a slow publisher | waits, then reads the complete 16-byte secret (`b'SLOWPUBLISHEDKEY'`) — never partial material | Matches the documented 50 × 20 ms retry |
| Stray `.tmp` left by a crash mid-publish | ignored at start-up; the real key resolves normally | Harmless orphan |

**Neither R-08 nor R-09 is closed by this pass**, and neither can be: the missing
directory `fsync` after `os.link` is still absent from `_publish_key_file`, and no
regression test pins any of the four behaviours above. Closing them requires a
fix **plus** tests (F-R2-RT4), exactly as the task requires. What this pass does
establish is that the *consequence* of R-08 is contained: the failure is loud and
non-destructive, which is why both remain Low-severity residuals rather than
blockers.

### 9.5 Tenant isolation, authorization, logging, limits

| Check | Result |
|---|---|
| List endpoints (`/invoices`, `/accounts`, `/products`, `/documents`, `/partners`, `/users`, `/tax/timbre-rules`) under tenant B | every row belongs to tenant B; no tenant-A id, no tenant-A `company_id` |
| Cross-tenant direct ids: `/api/v1/invoices/1`, `/api/v1/documents/{id}/review-policy`, `/api/v1/documents/{id}/file` | `403` for tenant B |
| Redaction | `password`, `token` → `[REDACTED]`; RIB → `[REDACTED_RIB]`; the raw RIB appears **nowhere** in the serialized record. Email and partner name are deliberately retained (not classified sensitive) |
| Rate-limiter failure mode | an invalid mode falls back to `fail_closed`, not open; `fail_open` only by explicit opt-in |

Correction to the earlier pass: the earlier `LEAK` flag on `/api/v1/partners` was
a **false positive** of a substring heuristic — tenant B has its own partner also
named `SPARTE`, `company_id: 2`. The corrected id/`company_id`-based check finds
no leak. Rate limiting remains **process-local**, as recorded in §8.3; nothing in
this pass upgrades that claim to distributed.

### 9.6 Test integrity at this exact head

`pytest tests -q` on `b679bc49` with CPython **3.11.2**: **20 failed, 1070 passed,
9 skipped** in 287.66 s — the same 20 as at base `4fc1364a` and as in
`41229742863`. One representative failure, verbatim:

```
assert 'failed SHA-256 verification' in 'Safe backup restore requires Python
    3.11.17+ or Python 3.12.10+ (and below 3.13); unsafe archive member types
    and paths are rejected.'
```

Every one of the 20 is this single cause: the host interpreter lacks
`tarfile.data_filter` (3.11.17+/3.12.10+). CI runs newer patch levels and is
green. No test was skipped, weakened or deleted to reach this state, and none of
the 20 is caused by the stack — the id list is identical at base.

### 9.7 Required fixes, with the regression tests that must accompany them

| ID | Fix | Regression test |
|---|---|---|
| F-R2-RT1/2 | Apply the timbre ceiling the spec states, **or** correct the spec (see 9.3) | `tests/test_phase27_scf_first_run.py`: base 600,000.00 CASH → timbre `10,000.00` accepted under the documented cap; a 12,000.00 declaration refused |
| F-R2-RT3 | Add the `request_sha256` comparison at `procurement_stock.py:2514` and `:2727`, raising `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH` like `:2236`/`:3133` | `tests/test_phase33_invoice_lifecycle.py`: replay with the same key **and** the same reason still returns `idempotent_replay: True`; the same key with a *different* reason raises. Template: `test_f5_first_reversal_succeeds_and_the_second_is_a_domain_conflict` |
| F-R2-RT4 | Add the directory `fsync` after `os.link` in `_publish_key_file` | `tests/test_phase12_key_migration_and_backup.py`: missing key + existing events fails loudly and mints nothing; empty key is refused untouched; a slow publisher's reader never sees a partial key |
| F-R2-RT5 | Either post the receipt-cost ↔ invoice-price variance, or document that `380` carries it | A tolerance-posted invoice (receipt 1,000.00, invoice 1,010.00) asserts the intended closing balance of `380` |
| F-R2-RT6 | Reject negative `qty`/`unit_price`/`amount_*` at creation | A negative-amount invoice is refused at `create_and_validate_supplier_invoice`, never reaches `Validated` |

Fixes F-R2-RT3 and F-R2-RT6 are contained, low-risk and independently
reviewable. F-R2-RT1/2 and F-R2-RT5 need an accounting decision first; F-R2-RT4
should land in its own PR with its own CI, per §8.4's reasoning.

### 9.8 Verdict per PR, second pass

| PR | Head | Independent verdict | Why |
|---|---|---|---|
| **#3** | `29645e5d` | **ACCEPT** | Fail-closed field review holds under the probes; approval cannot precede decisions; posting re-validates |
| **#4** | `99864bab` | **ACCEPT** | Review values are bound to the posted payload; the mismatch refusal was reproduced (`DOCUMENT_INVOICE_MISMATCH`, five specific codes) |
| **#5** | `c466cea7` | **ACCEPT with nit** | Price binding correct; F-R2-6 dead condition only |
| **#6** | `e4e47b3b` | **ACCEPT with nit** | Discount decided, never clamped, line net derived; F-R2-5 assertion looser than its comment |
| **#7** | `b679bc49` | **ACCEPT with findings** | Integrates the chain; carries F-R2-1 (High), F-R2-2 (Medium), F-R2-3 (Medium) |

The per-PR code review passes. What fails independent acceptance is **not** any
individual diff but the stack as a shipping candidate: a documented tax ceiling
the code does not apply (F-R2-1), and an idempotency guard that reports a dropped
request as success (F-R2-2).

### 9.9 Status

**CHANGES_REQUIRED.**

Reason: one **High** money-affecting defect (F-R2-1) — code and specification
disagree on a tax ceiling and no test covers bases above 500,000.00 — plus one
**Medium** idempotency defect (F-R2-2) where a replayed reversal with a different
payload is reported as a success. Both are small, isolated fixes with clear
regression tests (§9.7); neither requires reopening the PR chain or rewriting a
branch. F-R2-1's direction is **BLOCKED_EXTERNAL** until the accountant of record
supplies the rule text in force, and R-08/R-09 stay open for the same reason:
no fix and no test yet.

Green CI on all five PRs is *not* evidence of production readiness, and nothing
above should be read as such. Merging, retargeting and deploying remain with the
human reviewer.

### 9.10 Delivery commit and its CI

| Item | Value |
|---|---|
| Reviewed code head | `b679bc496b8053384ab40bcbd86c5db474140f42` — every finding, probe and test result above was produced against it |
| Delivery commit (this section) | `611ae09476995b1c1037f62efb6a5ecdf482ce0c` — **documentation only** (`docs/PR_STACK_REVIEW.md`, `docs/PROJECT_COMPLETION_TRACKER.md`, `docs/REMAINING_BLOCKERS.md`), no product code |
| CI on the delivery commit | run **`41233472971`** — `Test, coverage and static checks (Python 3.11)` ✅, `(Python 3.12)` ✅, `Key rotation, backup and recovery on Windows` ✅ (3/3) |
| Consequence for PR #7 | PR #7 is opened from this session's branch, so its head advanced `b679bc49 → 611ae09`; base, mergeability (`CLEAN`) and the chain are unchanged |

The commit boundary matters on purpose: the findings describe code exactly as it
stands at `b679bc49`, and the only thing added after that point is prose. A
reviewer can therefore verify every repro above against the same tree the PRs
contain.

---

## 10. Round 3 — targeted remediation (2026-10-31)

This section records what changed after §9, which asked for changes. Every fix
below is backed by a test that fails without it; nothing was merged, retargeted
or deployed, and no claim in §9 was quietly dropped.

Reviewed head going in: `b679bc496b8053384ab40bcbd86c5db474140f42`.
Delivery head coming out: see §10.7.

### 10.1 F-R2-2 — reversal idempotency · **FIXED**

Both `REVERSE_INVOICE` sites now compare the fingerprint, exactly as
`POST_INVOICE` (`procurement_stock.py:2236`) and `POST_REVERSAL` (`:3133`) did:

| Site | Change |
|---|---|
| `reverse_posted_invoice` (`procurement_stock.py:2514`) | `if idem["request_sha256"] != req_hash: raise FinancialIntegrityError("IDEMPOTENCY_KEY_PAYLOAD_MISMATCH")` |
| `_create_reversal_request` (`procurement_stock.py:2737`) | same guard, raised as `_RefusedAttempt` carrying `REVERSAL_ATTEMPT_REFUSED_IDEMPOTENCY_MISMATCH`, so the refusal is audited by the public wrapper *after* the attempt rolls back |

Behaviour, measured rather than asserted:

```
A. same key same reason   -> replay: True  | same credit note: True | no new rows: True
B. different reason       -> refused: IDEMPOTENCY_KEY_PAYLOAD_MISMATCH | no side effect: True
C. different invoice      -> refused: IDEMPOTENCY_KEY_PAYLOAD_MISMATCH | no side effect: True
D. direct path, same      -> replay: True  | no new rows: True
D. direct path, changed   -> refused: IDEMPOTENCY_KEY_PAYLOAD_MISMATCH | no side effect: True
HTTP: first 200 · same 200 replay=True · changed 409
audit: [('REVERSAL_ATTEMPT_REFUSED_IDEMPOTENCY_MISMATCH', 'IDEMPOTENCY_KEY_PAYLOAD_MISMATCH')]
```

Concurrency (four threads on one key): identical retries → 1 created + 3 replays,
**1** credit note; different reasons → 1 created, 3 refused, still **1** credit
note. Tests: `tests/test_phase33_invoice_lifecycle.py::test_fr2_2_*` (3).

### 10.2 F-R2-1 — the timbre ceiling · **BLOCKED_EXTERNAL, mechanism FIXED**

No figure was chosen. What changed instead is that the contradiction is gone and
cannot come back silently:

* the specification states what the code actually does (no ceiling from the
  shipped seed) and names the decision as undetermined (`docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md` §3);
* `create_timbre_rule_version` now requires an **explicit** cap declaration —
  a decimal ceiling or the sentinel `none` — and raises
  `TIMBRE_RULE_CAP_NOT_DECLARED` for an omission, so "no ceiling" can never again
  be an accident of a missing field;
* a doc/code guard test fails if the spec states a ceiling the shipped rule does
  not apply (verified: reintroducing the old sentence fails it with
  `['10,000.00']`);
* boundary tests pin 100,000.00 → 1,500.00, 100,000.01 → 2,000.00,
  500,000.00 → 10,000.00, 600,000.00 → 12,000.00, and the cap mechanism is proven
  separately with a synthetic 1,000.00 ceiling.

Decision required and owner: **LB-1** in `docs/REMAINING_BLOCKERS.md`.

### 10.3 F-R2-3 — clearing account 380 · **mechanism FIXED, policy BLOCKED_EXTERNAL**

Measured across every shape the task named (values in DZD):

| Shape | `380` before | `380` after the fix, account configured |
|---|---|---|
| exact match | 0.00 | 0.00 |
| +10/unit within tolerance | **+100.00** | **0.00** (variance +100.00 to `608`) |
| −10/unit within tolerance | **−100.00** | **0.00** (variance −100.00 to `608`) |
| beyond tolerance | refused `THREE_WAY_MATCH_PRICE_MISMATCH` | still refused |
| partial receipt + partial invoice | 0.00 | 0.00 |
| two receipts, one invoice | 0.00 | 0.00 |
| rounding-shaped line | 0.00 | 0.00 |
| reversal of a variance invoice | — | `608` nets to 0.00, `401` to 0.00 |

`products.price_variance_account_code` is nullable with **no default** — the
account is a policy choice and was not invented. With no account configured the
historical posting is preserved and the residual is named in the post result and
audited as `PRICE_VARIANCE_UNALLOCATED`, so it is never unexplained. Tests:
`tests/test_phase47_price_variance_clearing.py` (10, including the additive
migration). Decision required: **LB-2**.

### 10.4 F-R2-4 — negative amounts · **FIXED**

The domain was traced rather than assumed: non-positive **quantities** were
already refused, and negative headers were only caught indirectly through the
header-reconciliation mismatch. A negative **unit price** was not caught at all —
`qty × −price` produced a negative line that matched a negative header, so the
invoice validated, was approved and posted `600` debit −1000.00 / `401` credit
−1000.00, balanced.

Refusing it is unambiguous here because the project already (a) refuses
non-positive quantities, and (b) models a credit note as an `IN_REFUND`
document carrying positive amounts. Nothing about negative invoices is part of
the documented domain. Guards now sit at validation
(`LINE_n_PRICE_NONPOSITIVE`, `HEADER_AMOUNT_NEGATIVE`), on a correcting edit
(`LINE_PRICE_NONPOSITIVE`) and at posting (`LINE_AMOUNT_NONPOSITIVE`); zero stays
valid (a fully discounted invoice), and a test proves the legitimate credit-note
route still posts positive amounts. Tests: `test_fr2_4_*` (4).

### 10.5 F-R2-5 / F-R2-6 — the two nits · **CLOSED**

* PR #6's bound is now an exact `len(rows) == 16` plus a by-name assertion of the
  eight required header fields and the three line elements. The old `>= 10`
  pinned nothing; the exact pin fails if any decision goes missing.
* PR #5's `None` branch was **not** deleted: `_field_evaluation` legitimately
  emits `"value": None` for a decided-but-absent reading, so the branch was
  misplaced, not wrong. It now runs *before* the parse, and the refusal is
  specific: `LINE_n_PRICE_SOURCE_MISSING` / `..._DISCOUNT_SOURCE_MISSING` instead
  of `..._UNPARSEABLE`, which sends the clerk to a different repair. A genuinely
  unparseable value keeps its own code. Test: `test_fr2_6_*`.

### 10.6 Verification

| Check | Result |
|---|---|
| Targeted suites | phase33 40 passed · phase46 39 passed · phase47 10 passed · phase29 49 passed |
| Full suite (CPython 3.11.2) | **20 failed, 1092 passed, 9 skipped** in 247.60 s |
| Baseline at `2e123a9` | 20 failed, 1070 passed, 9 skipped → **+22 tests, the same 20 failures** |
| The 20 failures | every one is the interpreter gate (`requires Python 3.11.17+ or 3.12.10+`); re-ran the 20 in isolation: 20 failed, all on that message |
| `ruff check dz_erp tests tools` | **All checks passed** |
| `tools/risk_register_counts.py` | consistent, **45 rows** (5 added: P10–P14) |
| `tests/test_phase22_register_and_docs_consistency.py` | 15 passed |

No test was skipped, weakened or deleted. Three existing call sites in
`tests/test_phase46_timbre_rule_administration.py` were made **more explicit**
(`max_amount_dzd="none"`) because the production code now demands a declaration;
that strengthens, not weakens, what they assert.

### 10.7 Delivery

See §10.8 for the commit and CI of this section.

### 10.8 Delivery, CI, and one intermittent CI failure reported as-is

| Item | Value |
|---|---|
| Code changes (F-R2-2/3/4/5/6) | `9d5d112a7d0bc7a7549b809d14127b54ed46c616` |
| CI on that commit | run `41239621096` — Windows ✅, Python 3.12 ✅, **Python 3.11 ❌** |
| Failing step | `Run the complete test suite with coverage` |
| Failing test | `tests/test_phase49_audit_key_bootstrap_race.py::test_every_concurrent_worker_opens_the_same_database` |
| Error | `STARTUPCAUSE ('', 'sqlite3.OperationalError: database is locked')` |

**This is reported rather than explained away.** What is known and what is not:

* The failing test is **not** one added in this round, and neither it nor the
  start-up path it exercises was modified by `9d5d112a` — the only change in
  `database.py` is one added `PRAGMA table_info(products)` inside the migration
  transaction, plus an `ALTER` that never executes on a database created after
  this commit (the column is in the schema).
* The same test passes **6/6 at the parent commit and 6/6 at this commit** on
  this host with both cores saturated by CPU load.
* A deliberate attempt to force the race — 15 trials × 8 processes opening the
  same brand-new database simultaneously — produced **0/15** failures.
* The obvious hypothesis (that `PRAGMA journal_mode = WAL` ignores
  `busy_timeout`) is **wrong**: measured, it waits the full 30 s configured
  timeout and only then raises `database is locked`, so the failure implies some
  connection held a lock for over 30 seconds — which a handful of short-lived
  migrations should not be able to do.

So the mechanism is **not identified**, and no speculative change to locking was
made on the strength of one unreproduced failure: a blind retry around the WAL
switch would have been a guess dressed as a fix. It is recorded as follow-up
**F-R3-1** (pre-existing, intermittent, concurrent start-up), to be investigated
with the CI runner's own timing rather than invented here.

The job could not be re-run through the API (`run 41239621096 cannot be rerun`),
so a fresh run was triggered by the documentation commit that follows. Its result
is recorded in §10.9.

### 10.9 Result of the fresh run

| Item | Value |
|---|---|
| Commit | `bdbd64a84c54c12760c125fc11ffd188ff8aa8f0` (this section's docs on top of `9d5d112a`; the code is identical to `9d5d112a`) |
| CI run | **`41241542402`** |
| Jobs | `Test, coverage and static checks (Python 3.11)` ✅ · `(Python 3.12)` ✅ · `Key rotation, backup and recovery on Windows` ✅ — **3/3 success** |
| F-R3-1 | **Did not recur.** One failure, then green on the next run of the same test suite. It stays recorded as an intermittent, pre-existing, unreproduced start-up race — not as something this round fixed, and not as something it caused |

Reading the two runs together: the delivered tree is green, and the single
intermittent failure is disclosed with its evidence rather than dropped from the
record. A reviewer who wants the failure rate stated plainly: **1 failure in 2
runs of the 3.11 job for this round, on a test this round did not touch, that
passes 12/12 locally under load.**
