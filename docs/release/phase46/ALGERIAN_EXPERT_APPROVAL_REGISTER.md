# Phase 46 — Step 3: Algerian Statutory/Accounting Expert Approval Register (G5)

**Gate:** G5 — Algerian statutory and accounting expert approval
**Status: BLOCKED**
**Recorded:** 2026-11-09

---

## 1. What this document is, and what it is not

This is a **question register**, not an approval. It lists every Algerian statutory
or accounting decision the software currently makes **silently**, so that a
qualified professional can confirm or correct each one.

**No question in this document is answered.** The "expert response" column is
intentionally empty. A response field that the software author fills in would be an
invented approval, so it is left blank.

### Provenance of the 12 questions — important

The Phase 42 report states the original Phase 32 register is **not present in this
repository**. The twelve questions below are therefore **reconstructed from the
statutory assumptions the code actually hard-codes** (§5 verifies each code
location against the current tree). They are:

- **NOT authoritative** — they are not a transcription of an approved document.
- **Labelled reconstructed** — as required, rather than passed off as the register.
- **Asked, not answered** — each is a decision the code already makes.

If the original Phase 32 register is located, it supersedes this table.

---

## 2. Gate decision

> **G5: BLOCKED.**

No authorised Algerian `expert-comptable` (or equivalent qualified professional) has
reviewed, confirmed, or signed any of these rules. Consequently:

- The software's statutory output is **not tax-authoritative**.
- **No claim of Algerian tax or accounting compliance is made** anywhere in Phase 46.
- Per the Phase 46 mission, no merge or deployment may proceed under a *production*
  classification that requires this gate. The work continues on everything that does
  not depend on it.

---

## 3. Register format

Each entry records, per the Phase 46 requirement:

| Field | Meaning |
| --- | --- |
| **ID** | Stable question identifier (Q1…Q12) |
| **Encoded assumption** | What the software asserts as fact, in plain language |
| **Code location** | Exact file and line where it is decided |
| **Business impact** | What a wrong answer would do to real money or filings |
| **Source / reference** | Any authority cited in the codebase, or **NONE** |
| **Expert must confirm or correct** | The precise question put to the professional |
| **Expert response** | *Left blank — for the expert* |
| **Name / capacity / date / evidence** | *Left blank — for the signatory* |

**Verification note.** Every code location below was re-checked against the current
tree (`3d112e1`) for this document; line numbers reflect that commit.

---

## 4. The twelve questions

### Q1 — Stamp-duty bracket schedule

| | |
| --- | --- |
| **Encoded assumption** | Droit de timbre is 1 % up to 30 000 DZD, 1.5 % from 30 000 to 100 000 DZD, and 2 % above 100 000 DZD. |
| **Code location** | `dz_erp/localization.py:150–154` (`DEFAULT_TIMBRE_BRACKETS`) |
| **Business impact** | Misstates the duty on every cash-paid invoice — a direct, per-transaction tax error that also propagates into the payable and the journal entry. |
| **Source / reference** | **NONE in repository.** Hard-coded literal; no citation of a legal text. |
| **Expert must confirm or correct** | Is this the current lawful bracket schedule? If not, what are the correct thresholds and rates, and from which effective date? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q2 — 300.00 DZD exemption threshold

| | |
| --- | --- |
| **Encoded assumption** | No duty is due when the base is ≤ 300.00 DZD (`if base <= threshold: return 0.00`). |
| **Code location** | `dz_erp/localization.py:515` (default `threshold="300.00"`), `:524–525` |
| **Business impact** | Changes whether small cash invoices bear duty at all. Boundary is inclusive (`<=`), so exactly 300.00 DZD is exempt. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is 300.00 DZD the correct current threshold, and is the comparison inclusive (≤) or exclusive (<) at the boundary? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q3 — 5.00 DZD minimum duty and the absence of a maximum

| | |
| --- | --- |
| **Encoded assumption** | Duty is never less than 5.00 DZD (`min_duty="5.00"`), and there is no cap (`max_duty=None`). |
| **Code location** | `dz_erp/localization.py:513–514`, applied at `:536–543` |
| **Business impact** | A minimum raises the duty on small bases; an absent maximum means large cash invoices carry unlimited duty. Both directly affect tax payable. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is the 5.00 DZD minimum correct, and is there a lawful maximum duty? If a maximum exists, what is its value? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q4 — Duty applies only to cash payments

| | |
| --- | --- |
| **Encoded assumption** | Duty is charged only when `payment_method == "CASH"`; every other method returns 0.00. |
| **Code location** | `dz_erp/localization.py:522–523` |
| **Business impact** | Exempts all bank transfers and cheques from duty. If those methods are in fact dutiable, every non-cash invoice is under-taxed. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is duty due only on cash payments? Are bank transfer, cheque, and any other method the software accepts genuinely exempt? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q5 — Duty base is HT + TVA

| | |
| --- | --- |
| **Encoded assumption** | The duty base is the tax-inclusive amount (net HT **plus** TVA), not HT alone. |
| **Code location** | `calculate_droit_de_timbre(ht_dec + tva_dec, ...)` — `dz_erp/procurement_stock.py:2076–2078` |
| **Business impact** | Using the TTC base rather than HT systematically increases duty by roughly the VAT rate. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is the lawful base HT + TVA, HT alone, or something else? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q6 — Only 19 %, 9 % and 0 % VAT rates are admissible

| | |
| --- | --- |
| **Encoded assumption** | A line's tax rate must be one of `{0.0, 9.0, 19.0}`; anything else is a validation error `INVALID_ALGERIAN_TVA_RATE`. |
| **Code location** | `dz_erp/procurement_stock.py:1962–1973` (rate whitelist `allowed_rates`); seeded rates `dz_erp/localization.py:366–375` |
| **Business impact** | An invoice legitimately using another rate would be refused (blocking a real payable) — or, if the whitelist is too broad, a wrong rate would be accepted. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Are 19 %, 9 % and 0 % the only admissible rates for the sectors this system serves? Are there reduced rates we must support? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q7 — Per-line VAT rounding, then summation

| | |
| --- | --- |
| **Encoded assumption** | VAT is computed and rounded to 2 decimals **per line**, then summed; it is not computed once on the total. |
| **Code location** | `round_dzd(line_ht * rate / 100)` per line, then `Σ` — `dz_erp/procurement_stock.py` line loop, `:1955–1985` region |
| **Business impact** | Differs from total-level rounding by up to a few centimes per line. Repeated across many invoices this is a genuine reconciliation difference with filed returns. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is per-line rounding then summation the correct statutory method, or must VAT be rounded once on the invoice total? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q8 — Discount applied before VAT

| | |
| --- | --- |
| **Encoded assumption** | The taxable base is net of discount: `qty × PU × (1 − discount%/100)`, with VAT computed on that reduced base. |
| **Code location** | `compute_line_ht_dzd` — `dz_erp/procurement_stock.py:27–40` |
| **Business impact** | Determines whether VAT is charged on the gross or the discounted amount — a per-invoice VAT difference. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is a commercial discount deductible from the VAT base, and is "discount before VAT" the correct treatment? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q9 — Credit notes carry positive amounts only

| | |
| --- | --- |
| **Encoded assumption** | A reversal is a positive-amount `IN_REFUND` document linked to the original; a negative invoice is refused. |
| **Code location** | `dz_erp/procurement_stock.py:643` (negative-amount refusal), `:1377` and `:1554` (`'IN_REFUND'` inserts) |
| **Business impact** | Negative invoices would corrupt totals and the ledger; positive-only credit notes with linkage preserve traceability. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Must a credit note always carry positive amounts with explicit reversal linkage, and never a negative invoice? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q10 — Which date governs the applicable statutory rule?

| | |
| --- | --- |
| **Encoded assumption** | **Today: the compiled-in constants always govern.** The effective-dated rule mechanism exists in the schema (`timbre_rules` with `valid_from`/`valid_to`, seeded `LF-2025-CASH-TIMBRE` from 2025-01-01) but **is not consulted** — see the finding below. |
| **Code location** | Schema: `dz_erp/database.py:160` (`timbre_rules`), seed `dz_erp/localization.py:388–396`. Call site passes neither rule nor rates: `dz_erp/procurement_stock.py:646–655` |
| **Business impact** | When a rate or bracket changes, invoices dated after the change would still be computed under the old constants — or, once effective dating is wired, under whichever date the system picks. Choosing invoice date vs posting date changes the tax on invoices straddling a change. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Which rule version governs an invoice — the one in force on the **invoice date** or the one in force on the **posting date**? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q11 — May a posted invoice be re-stamped after a rule change?

| | |
| --- | --- |
| **Encoded assumption** | **Undecided in code.** There is no `assert_timbre_rule_unchanged`; a posted invoice's duty is not re-derived, and no provenance is frozen. |
| **Code location** | Absent by inspection — `grep` for `assert_timbre_rule_unchanged` / `timbre_rule` freezing returns no implementation |
| **Business impact** | If duty may not change after posting, the system must freeze provenance and refuse recalculation. If it may, restatement rules apply. Today neither is enforced. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | When a timbre rule changes, may an already-posted invoice be re-stamped, or must its provenance be frozen at posting? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

### Q12 — SCF chart mapping and E3B prefill tags

| | |
| --- | --- |
| **Encoded assumption** | Collected VAT sits in `4457`, deductible VAT in `4456`, and declaration boxes are tagged `E3B21`, `E3B11`, `E3B91`, `E3B92` (plus `_exempt` / `_export` variants). |
| **Code location** | `dz_erp/localization.py:366–375` (tax seeds with `acct_code` and `E3B*` tags) |
| **Business impact** | A wrong account or box mapping misstates the VAT return and the balance sheet, even when every individual invoice is arithmetically correct. |
| **Source / reference** | **NONE in repository.** |
| **Expert must confirm or correct** | Is the SCF mapping (`4456`/`4457`) and the `E3B*` box tagging correct for the current fiscal declaration? |
| **Expert response** | |
| **Expert name / capacity / date / approval evidence** | |

---

## 5. Finding recorded during preparation: effective dating is not wired

While verifying Q10 and Q11 against the current tree, a documentation defect was
found and is recorded here rather than fixed silently:

- `dz_erp/procurement_stock.py:1954` and `:2053–2056` state that callers resolve
  rates/rule via **`dz_erp.localization.resolve_vat_rates`** and
  **`dz_erp.localization.resolve_timbre_rule`**.
- **Neither function exists.** `grep -rn "def resolve_vat_rates\|def resolve_timbre_rule"`
  across the repository returns zero definitions.
- The live call site (`:646–655`) passes **neither** `timbre_rule` nor `vat_rates`,
  so `rule_kwargs` is `{}` and `calculate_droit_de_timbre` always uses
  `DEFAULT_TIMBRE_BRACKETS` and the default threshold/minimum.

**Consequence for G5:** the *stated* statutory behaviour (a configurable,
effective-dated rule version) is not the *actual* behaviour (compiled-in
constants). The expert is asked to rule on the constants as they are actually
applied — answering Q1–Q3 against a rule engine that does not yet consult the
`timbre_rules` table would be answering the wrong question.

**This is a correctness/documentation gap, not a security or accounting-invariant
breach.** It is recorded as an owner action (§7). Fixing it requires knowing the
answer to Q10 (which date governs), which is precisely what G5 must supply — so it
cannot be resolved before the expert rules.

---

## 6. Additional Phase 42 assumptions reviewed

Beyond Q1–Q12, the following Phase 42 assumptions were checked for statutory
content and are **covered by the register above** rather than duplicated:

| Phase 42 assumption | Covered by |
| --- | --- |
| Identifier formats: NIF 15/20 digits, NIS 15, AI 11, NIN 18, RC 6–32 (`localization.py:144–148`) | Structural, not a tax rate. **Not raised as a statutory question** — noted so the omission is deliberate, not an oversight. |
| Timbre seeded version code `LF-2025-CASH-TIMBRE` | Q1, Q10 |
| VAT rate whitelist `{0, 9, 19}` | Q6 |
| Discount-before-VAT | Q8 |
| Negative-amount refusal on headers | Q9 |
| Trial-balance and ledger arithmetic | G4 (accounting invariants), already PASS — outside statutory scope |

**Explicitly not claimed:** that this register is complete. It covers the twelve
reconstructed questions and the assumptions the code visibly hard-codes. A
professional review may identify further rules; additions are welcome and should be
appended with the same fields.

---

## 7. Owner actions to unblock G5

1. **Engage a qualified Algerian expert-comptable** (or a tax adviser authorised to
   opine on droit de timbre and TVA).
2. Provide them this register, plus read access to the cited code paths.
3. Obtain written responses for Q1–Q12, and record for each: **name, professional
   capacity, date, and the evidence of approval** (signed letter, engagement
   reference, or equivalent verifiable artefact).
4. Update this document **only** with the professional's verbatim answers — never a
   paraphrase authored by the project.
5. Resolve Q10 first, then implement effective dating (§5) against that answer.
6. Re-run the accounting/statutory test suite and re-issue G5.

### What is explicitly NOT done here

- No question is answered on the expert's behalf.
- No "reasonable inference" or generic tax knowledge is used to fill a gap.
- No approval is fabricated; the response and signature fields are empty by design.
- No claim of compliance is made.
