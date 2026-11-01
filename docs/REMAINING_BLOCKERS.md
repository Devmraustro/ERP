# Remaining blockers

What still stands between this repository and a defensible production
deployment — as of the verification of `0df3c33ff6fd96f59d04374b8c00006902ab62ed`
(branch `arena/7f12d627-erp`, PR #7 Draft).

Rule for this list: nothing is "done" because code exists, and nothing external
is done until the external party signs it.

---

## A. Engineering items still open

| ID | Item | Owner | Evidence that closes it |
|---|---|---|---|
| **F-02** | Durable request log grows with traffic and carries usernames + client IPs. Rotation exists (100 MiB × 10 in code, 10 MiB × 10 as shipped); retention *period*, age-based deletion, in-app access audit and off-host shipping do not. Reviewed in `docs/LOG_RETENTION_AND_ACCESS.md` (content verified credential-free, file mode `0600`). | Operations + DPO | A written retention schedule (with the legal reference) applied in configuration, plus evidence of off-host shipping or a documented decision not to ship. |
| **F-06** | Startup migrations are not one atomic unit (autocommit connections): each `ALTER` is atomic, the sequence is not. A crash leaves a partially upgraded database that the next open repairs. | Engineering | Either wrap the sequence in a single transaction (requires refactoring `_migrate_ocr_confidence`, which already manages its own), or keep the current contract and evidence the operator procedure (backup → upgrade → verify) in a drill. |
| **Multi-replica limiter** | Counters are per process by default, or shared across processes on **one** database file with `ERP_RATE_LIMIT_BACKEND=sqlite`. No distributed backend exists. | Engineering / Platform | A `RateLimitStore` implementation backed by a shared service **and** a test proving two instances share a counter, or an edge/proxy limit configured and evidenced by the operator. |
| **IPv6 aggregation** | The limiter keys on the address; an attacker with a /64 rotates addresses and obtains a new aggregate per address. | Engineering | Prefix-level keying (e.g. /64) plus tests, or an edge limiter that does it. |
| **Container image** | `ops/Dockerfile` and `ops/docker-compose.yml` are asserted by tests but the image has **never been built or run** — no `docker`, `podman`, `nerdctl`, `buildah`, `kaniko`, `img` or `/var/run/docker.sock` in the verification environment. | Platform | Build in an environment with a runtime; run compose with the bootstrap profile; restore a verified backup into the container; rotate a key inside it; confirm `X-Forwarded-For` handling matches `ERP_TRUSTED_PROXIES`. |
| **MFA / device binding** | Not implemented; password-only with throttling and lockout. | Engineering / Security | Decision record accepting the residual risk, or a second factor. |
| **Local interpreter floor** | CPython 3.11.2 refuses the safe-restore path by design, so 20 backup/restore tests fail locally on this host. Same names at baseline `852f389f`. CI on 3.11.17/3.12.10 passes them. | Environment | Run on 3.11.17+ or 3.12.10+ (already satisfied in CI). Not a product defect. |

## B. Security and operations evidence

| ID | Item | Owner | Evidence that closes it |
|---|---|---|---|
| **R-KMS** | Audit keys are file-backed by default; no KMS/HSM custody, no operator-performed rotation drill. | Security | Rotation executed on a real deployment with an off-host key store, chain verified before/after, custodian named. |
| **R-PROV** | No publisher attestation (PEP 740/Sigstore) verified for the pinned dependency set. | Release engineering | Attestation verification on the artefacts actually installed, or a recorded residual-risk acceptance. |
| **R-OFFHOST** | No evidence of a ledger checkpoint exported off-host under independent credentials. | Operations | Scheduler installed and firing, and an off-host checkpoint retrievable without host credentials. |
| **R-ALERT** | No alert channel. Detection exists; nobody is paged. | Operations | A synthetic failure delivered end-to-end through the real channel, with the receipt. |
| **R-DR** | No disaster-recovery drill, no established RTO/RPO. | Operations | Timed restore from the latest production backup into an isolated environment; db/vault hashes, trial balance and audit chains verified; measured RTO. |
| **R-CONC** | No concurrency evidence at production scale. | Operations / Engineering | A recorded run at target scale with WAL/busy-timeout/lock metrics inside documented bounds, or a written single-writer constraint bound into the deployment. |
| **R-OCR** | No authorised real-world corpus. | Operator (corpus owner) | Manifested, authorisation-signed corpus evaluated per `OCR_EVALUATION_PROTOCOL.md` §12, with the accuracy sign-off. |

## C. Accounting, tax and legal

| ID | Item | Owner | Evidence that closes it |
|---|---|---|---|
| **R-SCF** | Thirteen tax assumptions are implemented **as configured** and have never been reviewed by an Algerian expert-comptable: TVA rate set and applicability, exemption mapping, stamp-duty brackets, the 300.00 DZD threshold (inclusive), the 5.00 DZD minimum, cash-only applicability, the duty base, per-line rounding, discount treatment, G50 box mapping, fiscal-law effective dates, the IFU regime, workbook retention. Enumerated in `docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md` §8.1 of the previous pass and in `docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md`. | Expert-comptable | A dated, signed review naming the legal reference (Finance Law article and date) for each item, confirming or correcting the implementation. |
| **R-LAW** | No Law 18-07 / ANPDP determination: no processing register, DPO appointment, retention schedule or transfer assessment. | Legal counsel / DPO | Written determination covering lawful basis, retention, transfers and breach notification. |
| **G50 filing** | The G50 output is a preparation workbook with traceability, not a filing. | Expert-comptable | A filed return produced from the workbook, or written confirmation that the format matches what the accountant uses. |
| **Statutory sign-off** | No statutory accounts, auditor-accepted trail, or fiscal-representative attestation. | Expert-comptable / Auditor | The signed attestation. |

## What is *not* on this list

The duplicate-row integrity work, the bounded limiter (including its shared
backend), the effective-dated tax resolution, the immutable stamp-duty provenance,
the migration matrix and generation evidence, the deployment artefact properties
and the documentation set are verified against the exact head and are not blocked.
Their classification and reproduction commands are in
`docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md`.

## Suggested order

1. **F-06 + F-02** — small, engineering-only, closes the last two code findings.
2. **Container build** — needs a runtime; everything else about the image is asserted.
3. **Alerting / off-host / DR / concurrency** — one real deployment and a named operator.
4. **R-SCF / R-LAW** — longest lead time; start first even though they finish last.

---

## Remediation note (Phase 45, 2026-10-31)

The seven findings from the independent review (R-01 … R-07) are **no longer
blockers**; their disposition is in `docs/PR_STACK_REVIEW.md` §7 and their code
and test evidence is committed on `arena/7f12d627-erp`. Nothing in this file was
cleared by that work, and two rows are worth restating because a reader could
otherwise mistake a code fix for an external one:

| Row | Still blocked on | Why the code fix does not clear it |
|---|---|---|
| Tax values (timbre brackets, TVA rates) | expert-comptable sign-off | R-05 built the *door* for a new rule version. It deliberately has no default value, so it adds no legal claim; whether the seeded brackets are the law's is still a professional determination. |
| Safe backup/restore verification | a 3.11.17+ / 3.12.10+ interpreter with real data | R-07 fixed the *rule* and made it testable across its whole version space, and CI runs the round trip on both supported lines. This sandbox is 3.11.2, where restore correctly refuses; production-sized restore timing and a restore drill remain external. |

Everything else in this document — container build, off-host backup, alerting,
DR drill, concurrency at production scale, Law 18-07 retention — is unchanged.

---

## Human review gate (2026-10-31)

The engineering work is complete and independently re-verified; what remains
before any merge is **human review**, which no amount of green CI satisfies:

* **0 reviews** exist on PRs #3–#7 (`gh pr list`), and #4–#7 are still drafts;
* four of the five are blocked on nothing else — the per-PR checklist, the
  high-risk paths and the exact review questions are in
  `docs/PR_STACK_REVIEW.md` §8.5;
* the merge itself is mechanically gated: `delete_branch_on_merge=false`, so each
  merge must be followed by an explicit retarget of the next PR to `main`, and
  squash/rebase merges must not be used (they would force a rebase).

Two residuals found during this review are recorded and **not** fixed here, on
purpose: R-08 (no directory fsync after the audit-key link — a crash could lose
the key file, and start-up then fails loudly rather than minting a replacement)
and R-09 (the no-hardlink publish window, protected by a reader retry). Neither
weakens a guarantee that is claimed; both belong in a follow-up PR with its own
test, not in a review pass.

Nothing in this file was cleared by the remediation.

---

## Second review pass (2026-10-31) — two new blockers

The second pass (`docs/PR_STACK_REVIEW.md` §9, `docs/PROJECT_COMPLETION_TRACKER.md`
§S10) reproduced two defects that the round-1 package did not record. Neither is
cleared by any amount of green CI, and neither is fixed here — the pass was
authorized to review and document, not to change product code.

| ID | Blocker | Blocked on | Evidence required to clear |
|---|---|---|---|
| **F-R2-1** (High) | The specification states the 2 % timbre band is capped at **10,000.00 DZD** (`docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md:66`) while the code applies no ceiling: `_builtin_timbre_rule()` (`dz_erp/localization.py:550`) and the seeded `timbre_rules` row both leave `max_amount_dzd` unset. A 600,000.00 base declaring the documented cap is **refused** (`TIMBRE_MISMATCH: declared=10000.00 != expected=12000.00`) | **BLOCKED_EXTERNAL** — the accountant of record must state whether the ceiling exists in the rule text in force for the period. The figure must not be invented by the engineering side | The effective-dated timbre rule text, then either a one-line `max_amount_dzd` change with a 600,000.00-base test, or deletion of the parenthetical and a test pinning 600,000.00 → 12,000.00 |
| **F-R2-2** (Medium) | Reverse-invoice idempotency replays on key alone: `reverse_posted_invoice()` (`dz_erp/procurement_stock.py:2514`) and `_create_reversal_request()` (`:2727`) skip the `request_sha256` comparison that `:2236` and `:3133` perform, so a different reason on the same key is reported as `idempotent_replay: True` instead of raising `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH` | engineering — no external input needed | The two comparisons added, plus a phase-33 regression test: same key + same reason replays; same key + different reason raises |

Two further items from the same pass stay open as non-blocking findings: **F-R2-3**
(tolerance price differences leave a permanent balance on clearing account `380`;
pre-existing, identical at base) and **F-R2-4** (negative amounts validate and
post). **R-08 and R-09 remain open** — this pass confirmed the failure modes are
contained and loud, but the directory `fsync` is still missing and no test pins
the behaviour, so they close only with a fix plus tests.

Everything recorded above under "Human review gate" still applies: **0 of 5 PRs**
carry a human review, and `NOT_PRODUCTION_READY` stands for the external reasons
in this file independently of the two blockers above.

---

## Round 3 (2026-10-31) — what the remediation fixed, and the two decisions it cannot take

The four findings below were reproduced, fixed in code and pinned by regression
tests. Two of them surfaced a question that is **not** an engineering question:
it needs the accountant of record, and nothing in this repository may answer it
by choosing a number. They are recorded here as **LB-1** and **LB-2**.

### LB-1 — Is the top stamp-duty band capped, and at what figure?

**Status: `BLOCKED_EXTERNAL`.** Owner: the accountant of record (expert-comptable)
of the deploying company. Required evidence: the *droit de timbre* rule text in
force for the invoice period (Code du Timbre / the Finance Law article that sets
the band), stated as a ceiling in DZD or explicitly as "no ceiling".

**What was wrong.** The specification asserted a **10,000.00 DZD** ceiling on the
2 % band while both the compiled-in rule and the seeded `LF-2025-CASH-TIMBRE` row
stored `max_amount_dzd = NULL`. Nothing had ever *decided* the rule was uncapped;
the field was simply omitted, and the prose was never checked against the code.
Consequence: a 500,000.00 DZD cash base produced exactly 10,000.00 (2 %), a
600,000.00 base produced 12,000.00, and an invoice that declared the documented
10,000.00 ceiling was **refused** with `TIMBRE_MISMATCH`.

**What was done instead of guessing.**

1. The specification no longer states a ceiling it cannot source: it records that
   the shipped seed applies **none**, that the ceiling is **UNDETERMINED**, and
   why. This is a statement about our code, not a claim about Algerian law.
2. A rule version can no longer acquire a cap position by omission. The audited
   administration path requires `max_amount_dzd` to be **declared**: a decimal
   ceiling, or the explicit sentinel `none` meaning "reviewed and deliberately
   uncapped". Anything else — including `NULL` and `""` — raises
   `TIMBRE_RULE_CAP_NOT_DECLARED`. This is the "no undocumented defaults" design:
   the question is forced in front of a human with the authority to answer it.
3. A **doc/code guard test** reads the specification and fails if it ever states
   a ceiling the shipped rule does not apply. Verified to fail when the old
   sentence is reintroduced.
4. The disputed band is pinned by boundary tests at 100,000.00, 100,000.01,
   500,000.00, 500,000.01 and 600,000.00 DZD, so today's behaviour cannot drift
   while the decision is pending.

**What happens when the answer arrives.** If the band is capped at some figure,
an authorised user creates a new rule version through `POST /api/v1/tax/timbre-rules`
with that `max_amount_dzd`, a second user approves it, and the effective-dated
resolution applies it from then on. **No code change is needed**, invoices
already posted keep their recorded provenance, and the cap mechanism itself is
proven by `test_fr2_1_a_declared_cap_is_honoured_at_its_boundary` using a
synthetic figure. If the answer is "uncapped", the specification sentence is
already correct and nothing changes.

### LB-2 — Which account carries a purchase price variance?

**Status: `BLOCKED_EXTERNAL`.** Owner: the accountant of record. Required
evidence: the account code (SCF) to which a receipt-cost ↔ invoice-price
difference is booked, and the policy choice between booking the variance on the
invoice and revaluing the stock.

**What was wrong.** The GRN credits the stock-clearing account (`380`) at receipt
cost; the invoice debits the same account at invoice price. A difference allowed
by `price_tolerance_dzd` was posted nowhere, so `380` kept a permanent residual:
1,000.00 received against 1,010.00 invoiced left **+100.00** on `380` for ever,
and 990.00 left **−100.00**. Totals still balanced, so only a clearing-account
review would ever notice.

**What was done instead of inventing a policy.**

1. `products.price_variance_account_code` — nullable, no default, additive
   migration. **No account code was chosen for you**; there is no compiled-in
   variance account anywhere in the code.
2. When a product has one, the invoice debit is split: the clearing account gets
   the *receipt* value (so the GRN nets to 0.00) and the variance account gets
   the difference, on the debit side when invoiced above receipt and the credit
   side when below. The split is exact — the two halves always add back to the
   invoiced line, with no rounding cent of its own.
3. When a product has none, the historical posting is preserved unchanged **but
   the residual is no longer anonymous**: the post result carries
   `unallocated_price_variances` and an audit event
   `PRICE_VARIANCE_UNALLOCATED` names the amount, the line and the reason
   (`NO_PRICE_VARIANCE_ACCOUNT_CONFIGURED`).
4. Ten regression tests cover the exact match, both directions, above-tolerance
   refusal, partial receipt + partial invoice, multiple receipts against one
   invoice, rounding, the unconfigured case and the reversal mirror.

**Not decided here, on purpose:** whether the variance belongs on a purchase
variance account, a cost-of-goods adjustment, or a stock revaluation; and whether
the tolerance itself (`price_tolerance_dzd`) is a control the business wants.

### Fixed and verified in this round (no external dependency)

| Finding | Fix | Tests |
|---|---|---|
| **F-R2-2** reversal idempotency | `request_sha256` compared at both `REVERSE_INVOICE` sites; mismatch → `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH`, audited as `REVERSAL_ATTEMPT_REFUSED_IDEMPOTENCY_MISMATCH` | `tests/test_phase33_invoice_lifecycle.py` — 3 tests (both paths, changed reason, changed invoice, concurrency) |
| **F-R2-4** negative amounts | non-positive unit price and negative header totals refused at validation, on correcting edits, and again at posting | `tests/test_phase33_invoice_lifecycle.py` — 4 tests |
| **F-R2-5** weak row assertion | exact 16-row pin plus required-field coverage by name | `tests/test_phase29_ocr_review_policy.py` |
| **F-R2-6** dead `None` branch | `None` tested before parsing; distinct `..._SOURCE_MISSING` codes | `tests/test_phase29_ocr_review_policy.py::test_fr2_6_*` |

**R-08 and R-09 remain open and unchanged.** This round was authorized to fix
F-R2-2, prepare F-R2-1, investigate F-R2-3/F-R2-4 and fix the two nits; the
audit-key publish residuals were out of scope, are still un-fixed, and still have
no regression test. Their recorded consequence stands: a lost key file fails
loudly (`AuditKeyUnavailableError`) and never mints a replacement.

Nothing in this file was cleared by the remediation. `NOT_PRODUCTION_READY` still
applies for the external reasons above and for the deployment items that predate
this round.

## Round 4 (2026-10-31) — one finding closed by identifying it, two new ones fixed

**Nothing in this file was cleared.** LB-1 and LB-2 still need the accountant of
record, and this round chose no figure and no account code. What changed is that
the intermittent start-up failure from Round 3 now has a **named cause and a
regression test**, and re-reviewing Round 3's own changes found two defects in
them — both fixed.

### F-R3-1 — closed as engineering, with the cause shown

Round 3 recorded this as unreproduced. It is reproducible, deterministically:

* `PRAGMA journal_mode = WAL` fails **immediately** with `database is locked`
  while a peer holds `RESERVED` (which is what this codebase's own
  `BEGIN IMMEDIATE` transactions hold). It waits the busy timeout only under
  `EXCLUSIVE` — the case Round 3 measured, which is why the earlier conclusion was
  wrong.
* The consequence was measured, not assumed: **availability only**. A contended
  start-up fails to open the database; it never splits the audit key, loses an
  audit event or leaves a half-applied migration. After the peer releases, the key
  file is byte-identical, the chain is valid and the key version is unchanged.

The fix is bounded and does not hide anything: no switch is issued when the
database is already WAL, a switch lost to a peer counts as convergence, and the
original error is re-raised when a 0.5 s budget runs out. Report the exact
evidence in `docs/PR_STACK_REVIEW.md` §11.1.

**What this does *not* claim:** that concurrent start-up is now free. A start-up
that overlaps another process's write transaction may still have to wait, or fail
loudly if the write lasts longer than the budget. That is SQLite behaving
correctly, and the operational answer is to start one process at a time (or use
one writer), not to widen a timeout.

### F-R3-2 — Round 3's variance split used binary floats. Fixed.

`CAST(... AS REAL)` summed receipt lines in floating point: 1,000 units at
1.000125 DZD became 1000.1249999999999 → 1000.12 against the GRN's 1000.13, so a
posting at *identical* prices invented a 0.01 variance and left −0.01 on `380`.
Now summed per line in `Decimal` with `round_dzd`; two tests fail on the old code.

### F-R3-3 — a variance account outside the chart was posted silently. Fixed.

Choosing the account is still LB-2. Pointing the configuration at an account that
does not exist in the company's chart is a different thing, and it was accepted:
the ledger cited an unresolvable code and the trial balance still balanced. It now
fails closed with `PRICE_VARIANCE_ACCOUNT_UNKNOWN` before any entry is written.
No account was chosen, and a cleared configuration keeps the documented
unallocated behaviour.

### Still blocking, unchanged

| Item | Status | Owner |
|---|---|---|
| **LB-1** — is the top stamp-duty band capped, and at what figure? | `BLOCKED_EXTERNAL` | Accountant of record |
| **LB-2** — which SCF account carries a purchase price variance, and which policy? | `BLOCKED_EXTERNAL` | Accountant of record |
| SCF mapping / journal-code sign-off (C1) | `BLOCKED_EXTERNAL` | Expert-Comptable (ONEC) |
| Legal and fiscal review (C2, C6) | `BLOCKED_EXTERNAL` | Counsel / accountant |
| Key custody beyond a readable file (S1, S3) | `BLOCKED_EXTERNAL` (budget) | Security officer |
| **R-08 / R-09** audit-key publish residuals | **Open**, untouched | Engineering |
| Human review of PRs #3–#7 | **0 of 5** | Reviewer |

**Status: `BLOCKED_EXTERNAL` + `NOT_PRODUCTION_READY`.** Code-side
`CHANGES_REQUIRED` is cleared; nothing was merged, retargeted or deployed.

## Round 5 (2026-11-01) — final audit: one defect fixed, R-08 closed, one new question

### Fixed in this round

**F-R5-1 — a reversal contradicted itself about its own period.** Reversing a
prior-year invoice produced an entry numbered `RAVOIR/2025/00001` **dated
2026-11-01**, and a credit note whose `invoice_date` was 2026 while its
`fiscal_year` said 2025 — the value the duplicate-invoice unique index and every
year-grouped report key on. Number, date and `fiscal_year` now derive from the
single date the function writes, so they agree under any dating policy. Fixed at
both writers; 3 regression tests, 2 of which fail on the old code.

**R-08 — CLOSED.** The key publish path fsynced the file but not the directory
that received the link, so a crash could keep the audit events and lose the key.
`_fsync_directory()` now runs after both publish paths; the regression test wraps
`os.fsync`, inspects the descriptor and fails if the call is removed. Round 2's
rule — no closure without a verified fix **and** regression tests — is satisfied.

**R-09 — remains open** with its rationale: the `O_CREAT|O_EXCL` fallback exists
only where hard links are unavailable, its reader-side window is bounded and
fails loudly, and the obvious "fix" (`os.replace`) would reintroduce the
split-key race that `O_EXCL` prevents.

### LB-3 — should TVA be rounded per line or on the invoice total?

**Status: `BLOCKED_EXTERNAL`.** Owner: the accountant of record. Required
evidence: the rounding convention the *droit fiscal* expects when an invoice has
several lines, i.e. whether TVA is computed per line and summed, or on the
invoice's total base.

**What the code does.** Rounding is per line by documented design
(`docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md` §1), and the header TVA must
equal the sum of the line values. An invoice that declares the invoice-level
figure is refused with `HEADER_TVA_MISMATCH`. A sweep of 1–100 centimes × 3 lines
shows the two conventions disagree in **67 of 100** cases, so this is not a
theoretical edge: a supplier using the other convention produces an invoice that
this system cannot accept as declared.

**Why it is not simply a bug.** The refusal is loud and the declared figures are
never altered — a reviewer sees exactly which value failed. Which convention is
correct is a tax question, not an engineering one, and silently accepting either
figure would mean importing an unreviewed tax policy. Recorded, not changed.

### Unchanged and still blocking

| Item | Status | Owner |
|---|---|---|
| LB-1 stamp-duty cap | `BLOCKED_EXTERNAL` | Accountant of record |
| LB-2 SCF variance account and policy | `BLOCKED_EXTERNAL` | Accountant of record |
| LB-3 TVA rounding convention | `BLOCKED_EXTERNAL` (**new**) | Accountant of record |
| SCF mapping / journal sign-off (C1), legal and fiscal review (C2, C6) | `BLOCKED_EXTERNAL` | Expert-Comptable / counsel |
| Key custody beyond a readable file (S1, S3) | `BLOCKED_EXTERNAL` (budget) | Security officer |
| Container image not built by CI; restore unverified on real volumes | **Not verified** | Engineering / ops |
| Human review of PRs #3–#7 | **0 of 5** | Reviewer |

**Status: `BLOCKED_EXTERNAL` + `NOT_PRODUCTION_READY`.** No code-side change is
outstanding. Nothing merged, retargeted or deployed.

## Round 6 (2026-11-01) — production readiness gate: one defect fixed, a dependency blocker found

No legal, tax or SCF decision was taken in this round. What changed: the first step of
the deployment runbook no longer breaks after an interrupted password prompt
(**F-R6-1**), and the lockfile was scanned for advisories for the first time
(**DEP-1**, a release blocker).

### Fixed this round — F-R6-1

`python -m dz_erp.serve --bootstrap-admin admin` inserted and **committed the company
before prompting for the password**. An aborted prompt (mistyped, Ctrl-C, or the
documented `docker compose run --rm bootstrap` without a TTY) left a company with no
user and no audit event, and every retry died with
`sqlite3.IntegrityError: UNIQUE constraint failed: companies.code`. Recovery needed
manual SQL against a financial database before its first use.

Now: the password is read before anything is written; an orphaned bootstrap company is
reused so a broken deployment recovers by re-running the same command; an
already-bootstrapped company is a documented refusal; and a failed user insert removes
only the company that attempt created. `tests/test_phase53_bootstrap_admin_atomicity.py`
— 6 tests, all 6 fail on the old ordering.

### New blocker — DEP-1: pinned dependencies carry published advisories

**Status: `OPEN` — release blocker. Owner: engineering, with a security reviewer's
sign-off on the resulting matrix.** Evidence and the reproduction command are in
`docs/DEPENDENCY_AND_LICENSE_REGISTER.md` §4.4.

`pip-audit 2.10.1` against `requirements.lock.hashes.txt`: **8 of 51 packages** have
advisories with fixes available — `pypdf` 5.4.0 (103), `pillow` 11.1.0 (69),
`starlette` 0.46.2 (14), `python-multipart` 0.0.20 (12), `cryptography` 44.0.2 (10),
`pdfminer.six` (4), `certifi` (2), `pytest` 8.3.5 (2, test-only).

**Why it blocks.** `pypdf`, `pdfminer.six`, `pillow` and `python-multipart` parse
third-party supplier documents — the system's primary input, supplied by untrusted
parties. `starlette` fronts every request and `cryptography` protects the vault and the
audit chain. These are externally reachable, not insider-only.

**What is missing:** upgraded pins plus a regenerated hash lockfile, a full matrix run
(including the Windows key-rotation job and the interpreter-gated restore tests), and
an advisory scan wired into CI so this cannot accumulate again. It was *not* done here:
moving `pypdf` 5→6, `pillow` 11→13 and `starlette` 0.46→1.x is a substantial change
that a release candidate should not absorb un-reviewed at its final gate, and a partial
upgrade would leave the same exposure with a false sense of progress.

### New blocker — OPS-1: the container image has never been built

**Status: `NOT_VERIFIED`.** No container runtime exists in this environment and none can
be installed (no `docker`/`podman`/`buildah`; the Debian mirrors are unreachable from
the sandbox's network allowlist). **No build is claimed.** What was verified statically
and by executing the same entrypoint is listed in `docs/PR_STACK_REVIEW.md` §13.3: the
interpreter pin matches the code's safe-restore floor, the install command is the
CI-proven one, every `ENV` name is read by the code, the build context contains no
secret material, and startup/health/SIGTERM/restart behaviour was executed against a
real file-backed database.

Still owed by whoever owns the platform: the image build itself, an image CVE scan of
`python:3.11.17-slim-bookworm`, a layer secret scan, and the compose stack coming up.

### New blocker — OPS-2: RTO/RPO and a real restore drill

**Status: `OPEN`.** Backup creation, manifest contents and interrupted-backup safety
were verified here; restore is interpreter-gated (this sandbox runs 3.11.2) and is
evidenced only by a green CI job that fails loudly — not skips — below 3.11.17. The
runbook's own rule stands: **RTO/RPO must not be quoted until a drill runs on the target
host**, because a synthetic restore cannot produce a production recovery objective.

### Blocker matrix — decision, evidence, owner

| ID | Decision required | Evidence currently available | Exact missing artefact | Responsible reviewer | Technically ready once approved? |
|---|---|---|---|---|---|
| **LB-1** | Is the top *droit de timbre* band capped, and at what figure in DZD (or explicitly "no ceiling")? | Spec states the shipped seed applies **no** cap and that the ceiling is UNDETERMINED; `max_amount_dzd` cannot default to NULL (`TIMBRE_RULE_CAP_NOT_DECLARED`); boundaries pinned at 100,000.00 / 100,000.01 / 500,000.00 / 600,000.00 | The Finance Law / Code du Timbre article in force for the invoice period, stated as a ceiling or as "uncapped" | Accountant of record (expert-comptable) | **Yes** — a new rule version through the audited two-person path; no code change |
| **LB-2** | Which SCF account carries a purchase price variance, and is the policy "book on invoice" or "revalue stock"? | `products.price_variance_account_code` is nullable with **no** compiled-in or seeded default; split is exact and tested both directions; unknown codes refused (`PRICE_VARIANCE_ACCOUNT_UNKNOWN`); unconfigured residuals are named and audited | A written SCF account code for the variance **and** the policy choice | Accountant of record | **Yes** — set the product column per policy; mechanism and tests exist |
| **LB-3** | Is TVA rounded **per line** (and the header must equal the sum) or on the **invoice total**? | Per-line rule implemented and documented; invoice-level declarations are refused with `HEADER_TVA_MISMATCH`; measured divergence in **67 of 100** small-value cases | The convention the tax administration expects for multi-line invoices | Accountant of record | **Yes, conditionally** — if "invoice total", the change is a rounding-policy change plus test updates, not a redesign |
| **C1** | Does the SCF mapping, journal-code choice and rounding match statutory expectation? | Vendored chart; tested mappings; `Decimal` throughout; trial balance and journal balance checks in every posting path | Signed mapping table and reversal policy from the expert-comptable | Expert-Comptable (ONEC) | **Yes** — mapping is data in `vendor/`, not hard-coded logic |
| **C2** | Are the VAT rates, timbre brackets and G50 box mapping correct for the period? | Versioned rate rules; effective-dated resolution; per-invoice provenance (`timbre_rule_version` / `_source`) retained through reversal; G50 tags emitted | Dated rate schedule and G50 box specification for each open period | Expert-Comptable / tax adviser | **Yes** — rates are configuration versions, not constants |
| **C6** | Law 18-07: processing register, data-subject workflow, ANPDP filing, transfer assessment | Encryption at rest for documents; audit chain; redaction of NIN/RIB/IBAN/tokens in logs; retention documented in `docs/DATA_PROTECTION_COMPLIANCE.md` | ANPDP filing/registration evidence, a signed processing register, and a data-subject request procedure with an owner | Legal counsel / DPO | **No** — partially software, mostly organisational; the technical controls exist, the legal artefacts do not |
| **S1** | Key material is a readable file: is a KMS/HSM funded, or is the residual accepted in writing? | `0600` on the open descriptor, custody-drift detection, symlink/reparse refusal, atomic publish with directory fsync (R-08 closed), `describe()` reports `hsm_equivalent: False`; `ERP_KEY_PROVIDER=kms|hsm` fails closed unless a provider is supplied | A funded KMS/HSM, or a recorded acceptance by the security owner | Security officer | **Yes for KMS** — the provider contract and fail-closed switch exist; the investment does not |
| **S3** | Is split custody / two-person control required for rotate–retire–remove? | Rotation is audited and versioned; `vault-rollback` / `audit-rollback` exist; a single operator can currently perform all of it | A policy decision plus an approval-workflow requirement | Security officer | **No** — needs an approval workflow; today the audit trail records who did it, which is detection, not prevention |

**Status: `BLOCKED_EXTERNAL` for LB-1/LB-2/LB-3/C1/C2/C6/S1/S3 · `OPEN` for DEP-1,
OPS-1, OPS-2 · `NOT_PRODUCTION_READY` overall.** Nothing merged, retargeted or deployed.
