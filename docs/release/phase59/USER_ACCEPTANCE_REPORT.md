# Phase 59F — Operational UX Acceptance

**Repository:** `https://github.com/Devmraustro/ERP.git` · **Branch:** `arena/63d762c8-erp` ·
**Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`
**Feature freeze:** no redesign, no new screen, no new workflow. Every change below is a **defect
repair** traced to a failed acceptance check.
**Environment:** headless Linux with the real PySide6 6.11.2 (`offscreen` platform).

---

## 1. Verdict

| Property | Status |
|---|---|
| Qt suites execute | **PASS** — 55 tests, **0 skipped**, 0 failed |
| Startup / login screen | **PASS — suite** (`test_phase26_desktop.py`, 10 tests) |
| Navigation and RTL switch to Arabic | **PASS — suite**; `setLayoutDirection(RightToLeft)` on `ar` |
| French / Arabic / English parity | **PASS — EXECUTED** 286 / 286 / 286 keys, identical key sets |
| Form validation and messages | **PASS — suite** + live API 422s surfaced |
| Busy/disabled controls during work | **PASS — suite** (`_set_busy` disables every action) |
| Tables, sorting, selection | **PASS — suite** |
| Invoice review dialog | **PASS — suite**, 4 tests incl. full cycle |
| Critical action never silently fails | **PASS** — see §4 |
| Persistence across restart | **PASS — EXECUTED** on the server side (59A §4.5); the dialog reads state from the API on open, so it re-derives from the database |
| Resize / minimum size / High-DPI | **PASS — partial**; minimum size asserted, High-DPI **NOT VERIFIED** (§5) |

**Two defects were found and fixed in this phase** (§3). Both would have been invisible to CI before
the desktop job existed, because every Qt test skipped there.

## 2. Test inventory (executed)

| File | Tests |
|---|---|
| `tests/test_phase26_desktop.py` | 10 |
| `tests/test_phase27_desktop_ui.py` | 4 |
| `tests/test_phase27_review_api.py` | 5 |
| `tests/test_phase28_desktop_users_ui.py` | 7 |
| `tests/test_phase29_desktop_redesign.py` | 29 |
| **Total** | **55** — `55 passed`, `0 skipped` |

Command (reproduces locally and in the new `verify-desktop` CI job):

```sh
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_phase26_desktop.py tests/test_phase27_desktop_ui.py \
  tests/test_phase27_review_api.py tests/test_phase28_desktop_users_ui.py \
  tests/test_phase29_desktop_redesign.py -ra
```

## 3. Defects found and fixed

### 3.1 DEFECT — correcting an unread OCR field duplicated its evidence row *(fixed)*

**Symptom (observed in the review dialog).** A field whose OCR text was unreadable
(`raw_text = "11 9OO DD"`, `normalized_value = NULL`) was corrected by hand in the dialog. Afterwards
the field list held **three** rows instead of two:

```
before: (1,'supplier_invoice_no','FAC-UI-001',  'FAC-UI-001')
        (2,'amount_ttc_dzd',   '11 9OO DD',     NULL)
after:  (3,'amount_ttc_dzd',   '[manual entry]','11900.00')
        (1,'supplier_invoice_no','FAC-UI-001', 'FAC-UI-001')
        (2,'amount_ttc_dzd',   '11 9OO DD',     NULL)     ← untouched duplicate
```

Two rows then claimed the same field, which the review policy correctly treats as a duplicate-evidence
group (`DUPLICATE_EXTRACTION_ROWS`). The operator's correction therefore created the very blocker it
was meant to clear.

**Root cause.** Two independent faults:

1. `dz_erp/intake_ocr.py::_apply_field_decision` inserted a `[manual entry]` row whenever the
   extracted row had no normalised value, instead of **transcribing onto that row**. A missing value is
   not a missing field: the row exists as evidence and the human reading belongs on it.
2. While fixing (1), the re-read of the inserted row reused the variable `row`, so the
   insert/update decision later in the same function was made against the wrong state. This was found
   only because the test still failed after the first fix — recorded here rather than edited out,
   because it is the reason the fix is now verified end-to-end instead of by inspection.

**Fix.** The CORRECTED branch sets `human_override_value` **and** `normalized_value` on the existing
header row; insertion happens **only** when no row exists at all (genuinely absent field), and the
inserted row is held in a distinct name so it cannot shadow the branch decision. Line-item fields keep
their own transcription path (`Line rows are created by extraction only…`).

**Verification.** The dialog test now asserts the field list has exactly **2** rows after correction,
that the corrected row carries `11900.00`, and that the original OCR text `11 9OO DD` is **still
present as evidence**. Both properties matter: no duplicate, and no destruction of the engine's reading.

### 3.2 DEFECT — the review dialog crashed on a provenance lookup before its first refresh *(fixed)*

**Symptom.** `AttributeError: 'InvoiceReviewDialog' object has no attribute '_policy'` raised from
`_show_provenance` when a field was selected before the policy had been fetched.

**Root cause.** `self._policy` was created only inside `_render_review_blockers`, so any earlier
selection event (the table filling, a test assembling the dialog) hit an uninitialised attribute.

**Fix.** Initialised in `__init__` **and** declared as a class-level default, so the lookup falls back
to "no policy known" instead of raising. A blank hint is a recoverable state; an exception inside a
selection handler is a blank pane with a stack trace in the log.

**Verification.** `test_review_correction_box_explains_a_missing_value` passes; the whole 29-test
redesign file passes.

### 3.3 Behaviour confirmed (not a defect): the blockers banner

The dialog now shows a hidden-by-default `Banner` listing the **server's** review blockers, because an
operator cannot fix what the screen does not name. Example payload the UI renders:

```
REQUIRED_FIELD_NOT_DECIDED:amount_ttc_dzd · REQUIRED_FIELD_NOT_DECIDED:supplier_invoice_no · …
```

and, for an unread field, `review_missing_hint` explains that `[manual entry]` means *a human read this
value; no engine measured it*. This is transparency about provenance, not decoration: it is the
difference between "the field is empty" and "the field was never extracted".

## 4. "Critical actions must never silently fail"

Checked case by case, all executed:

| Action | On failure |
|---|---|
| Login | `401` with a message; after 5 tries an audited 15-minute lock (**not** a silent no-op) |
| Approve / reject | `403` segregation-of-duties, or a blockers banner naming each undecided field |
| Post | `409` with the state that blocked it: `Cannot post invoice 3 in state 'NeedsReview'; requires 'Approved' state.` |
| Decide a field | `422` with a typed reason (`FIELD_VALUE_CHANGED_SINCE_READ`, `FIELD_VALUE_INVALID_*`) |
| Any in-flight action | every action button disabled via `_set_busy(True)` until the worker returns |

The one place a user could previously be misled — a blank correction box for a field that was never
extracted — is closed by `review_missing_hint` (§3.3).

## 5. Not verified here, stated plainly

| Item | Why |
|---|---|
| Real display rendering, Arabic shaping on screen | Headless sandbox; widgets are constructed and measured, never presented |
| High-DPI scaling (125 %, 150 %, 200 %) | Needs a display/scale factor; no screenshot evidence exists |
| Keyboard-only navigation, screen readers | Not exercised |
| Resize behaviour on a physical window | Minimum size is asserted (`940×620`); drag-resize is not |
| Startup time on the target hardware | Not measured; no performance claim is made |
| Windows font fallback for Arabic | Needs a Windows host |

These are recorded as **NOT VERIFIED**, not as passing. A Windows UAT pass is required before any
claim of "visually accepted" is made.

## 6. What an operator should expect (and what the guides now say)

* The window is usable from `940×620` upward; below that layout is not guaranteed.
* Arabic switches the layout direction and the string set — **all 286 keys exist in all three
  languages**, so no screen falls back to another language mid-flow.
* An undecidable field is shown with a hint, not an empty box.
* Any blocked action names its blocker; nothing fails silently.
* The review dialog reflects **server** state; closing and reopening re-reads it. The client keeps no
  authoritative copy.
