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
