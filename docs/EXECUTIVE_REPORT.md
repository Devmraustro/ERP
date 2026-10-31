# Executive report

**Subject:** completion of the engineering workstream on the Algerian ERP
repository, branch `arena/7f12d627-erp`.

**One paragraph.** Every defect this workstream found in the engineering-controlled
surface is fixed and covered by a regression test, and the full suite shows no
regression against the baseline. The system is therefore **further from being
unsafe** than it was — but it is **not ready for production**, and no code change
in this repository can make it so: key custody, production-scale disaster
recovery, alert delivery, a qualified Algerian tax review and data-protection
determinations remain outside engineering's reach. Both halves of that sentence
are deliberate.

---

## 1. What was wrong, in business terms

| Problem | Business consequence | Now |
|---|---|---|
| A supplier invoice could be **duplicated in the extraction layer** — two values for the same amount — and the software would quietly take the first one and let the invoice through for approval | A value nobody reviewed could have reached the accounts. This is the single most serious issue found | **Fixed.** A duplicate is a blocking condition at submission, approval *and* posting; the reviewer must state which row is authoritative, and the decision records exactly which rows were examined |
| Signing a "flag, I'm unsure" decision could be mistaken for resolving the ambiguity | Uncertainty laundered into approval | **Fixed.** Flagging is an observation, never a resolution |
| If the problem surfaced only at posting time, the invoice became **permanently unpostable** | A dead end an accountant could not escape without database surgery | **Fixed.** The refusal is recorded, the approval is invalidated, and the invoice returns to review with the reason |
| Passwords could be guessed **without limit** on the login endpoint | Credential stuffing and a cheap denial-of-service | **Fixed.** Throttled per account and per client, before the password is even checked, with the refusal audited |
| VAT and stamp duty were computed from constants compiled into the code, while the versioned, dated tax configuration **sat unread** | A reviewed rule change would not take effect; nobody could say which rule a posted figure used | **Fixed.** Tax is resolved for the invoice's own date from configuration; a gap or an ambiguous configuration stops the invoice instead of guessing |
| The safety of in-place database upgrades rested on a written assertion, not on evidence | An unattended upgrade runs on the only copy of the data | **Fixed.** A 24-case matrix proves convergence, idempotency, preservation and interruption recovery |
| There was no way to run or check the system as a container | Deployment depended on improvised steps | **Prepared, not proven.** A Dockerfile and compose reference exist and are guarded by tests; they have never been built here |

## 2. What that means commercially

* The invoice-to-ledger path now has a defensible control story: every financial
  figure is either reviewed by a named person or blocked, and the audit trail
  records which extraction rows each human decision actually covered.
* Tax behaviour is now *configurable and dated* rather than hard-coded — which is
  what a serious accounting product needs, and also what makes legal review
  possible, because there is now something concrete to review.
* Operational risk is reduced but not eliminated: a single misconfigured
  environment variable (an over-broad trusted-proxy range, a disabled log path)
  can still weaken the deployment, which is why the operator checklist exists.

## 3. What cannot be claimed yet

| Claim someone might want to make | Why it cannot be made |
|---|---|
| "Production ready" | No production-scale test, no DR drill, no alert ever delivered to a human |
| "Compliant" | No legal or tax sign-off; Law 18-07 determinations are unwritten |
| "Secure" (unqualified) | No penetration test; several accepted risks (no MFA, no device binding) are documented |
| "Tax calculations are correct" | The *mechanism* is now correct and tested; the *values* require an Algerian expert-comptable |
| "Deployed" | Nothing was deployed. Preparing deployment artefacts is not deploying |

## 4. Decisions the owner must make

1. **Tax sign-off.** Engage a qualified Algerian expert-comptable to review the
   seeded rates, thresholds and brackets, and to record the legal reference and
   effective date behind each. Nothing else unlocks the tax rows.
2. **Key custody.** Decide between file-backed keys (works today, weaker) and a
   KMS/HSM (requires integration work). The software refuses to *pretend* a label
   means a provider.
3. **Deployment shape.** One process per host is the only shape the current rate
   limiter and SQLite writer support. Horizontal scaling is a design change.
4. **Data-protection posture.** Retention periods, erasure handling, hosting
   location and notification duties need legal input; the software deliberately
   refuses to invent them.

## 5. Recommendation

Continue to a **controlled pilot** with real users on a single host, using the
operator checklist, with the tax configuration reviewed beforehand and one tested
restore performed before go-live. Do not describe the system as production-ready
until the external rows in `docs/RELEASE_READINESS_MATRIX.md` have evidence behind
them. Every one of those rows names what evidence would close it.
