# Phase 30 — Arabic RTL, French and English acceptance

**Method:** the application was run against a disposable demo company (Arabic company name, Arabic
supplier, mixed Latin SKU) with one real invoice posted through the API, and inspected both
**structurally** (assertions on direction, alignment and rendered strings) and **visually** (every
screenshot in `screenshots/` was opened and read).

Script: `evidence/rtl_capture.py` — **18/18 structural checks pass**.
Captures: `screenshots/30_01…30_06` (Arabic), `30_07` (French after switch), `30_08` (English).

## Results

| Requirement | Result | Evidence |
| --- | --- | --- |
| Right-to-left page layout | **PASS** | `app.layoutDirection() == RightToLeft`; sidebar measured at x=1168 of 1400 (physical right) |
| Sidebar placement / navigation direction | **PASS** | `30_01`; nav rows mirrored, icons on the right |
| Table header and cell alignment | **PASS** | headers `الرمز / التسمية / مدين (دج) / دائن (دج)`; numeric columns right-aligned in RTL |
| Arabic labels | **PASS** | all 245 catalogue keys present in `ar`; no French leftovers in the sidebar |
| Arabic account names | **PASS** | `موردو المخزونات والخدمات`, `الرسم على القيمة المضافة القابل للاسترجاع`, `مشتريات البضائع المبيعة` |
| Mixed Arabic + Latin content | **PASS** | `30_05`: Arabic product name beside `SKU-RTL-1` and `ALG-WH`; `30_04`: `RTL-2026-001`, `2026-11-02` |
| Account codes and numeric values | **PASS** | codes `401/4456/600` render LTR inside RTL rows without reordering |
| DZD formatting | **PASS** | `148 750,00 DZD` — same grouping/decimal convention as French (documented decision, see below) |
| Date presentation | **PASS** | ISO `2026-11-02`, unambiguous in all three languages |
| Dialog layout | **PASS** | `30_04`: panes mirrored, badge top-left, actions and reason field bottom, nothing overlapping |
| Forms and validation messages | **PASS** | the rejection-reason field carries an Arabic placeholder; the correction box explains a missing value in Arabic |
| Dashboard identity and notifications | **PASS** | `المستخدم: admin (admin)`; identity re-renders on every language switch |
| Invoice and OCR review screens | **PASS** | `30_04`: `مراجعة الفاتورة`, lines, source pane, actions all Arabic |
| No clipped text | **PASS** | no elided label found at 1400 px or at 1024 px |
| No overlapping controls | **PASS** | inspected in `30_01`–`30_06` |
| No missing translations | **PASS** | 245/245 keys in all three languages, placeholder-complete |
| No incorrect fallback language | **PASS after fix** | see defect **D-3** below |

## Defects found and fixed in this phase

### D-3 — table chrome stayed in the previous language (fixed)

Switching to English left the table toolbar reading **`294 sur 294 lignes`** while the sidebar above
it was already English, and in Arabic it read **`سطرًا 294`** in an English window.

*Cause:* `DataTable.set_language()` stored the language code but never re-rendered the strings the
table composes itself (`rows_shown`, `page_of`). They were baked in when the rows last loaded.

*Fix:* `set_language()` now re-renders that chrome, using the same filtered total that
`_apply_view()` uses (both paths share `_filtered_rows()`, so the count cannot drift).

*Verification:* `294 من 294 سطرًا` → switch → `294 of 294 rows` / `Page 1 / 3`.
Regression test `test_language_switch_retranslates_the_table_chrome`; confirmed to fail when the fix
is reverted.

### D-4 — RTL chart labels (fixed in Phase 29, re-verified here)

The dashboard bar chart now paints `4456 · TVA récupérable et précompte` (code **and** name) and
mirrors its label/value edges for RTL. Verified in `30_02` and by the bar-chart regression test.

## Decisions recorded, not defects

**Numbers use Western digits with a comma decimal separator in Arabic.** `148 750,00 DZD` is the
same string in all three languages. This is deliberate and consistent with DZD/`fr-DZ` practice and
with the accounting source data; replacing the decimal separator with `٫` would make amounts differ
from the ledger and from every printed document. It is recorded here so a future reviewer does not
"fix" it accidentally. If a product decision requires Arabic-Indic digits, that is a new
requirement, not a bug.

**Two-currency-free display.** No currency conversion or symbol localisation is applied; `DZD`
appears literally in every language.

## Language round-trip

`ar → fr → en → ar` was exercised in one running window:

| Step | Identity line | Result |
| --- | --- | --- |
| start (`ar`) | `المستخدم: admin (admin)` | PASS |
| → `fr` | `Utilisateur : admin (admin)` | PASS |
| → `en` | `User: admin (admin)` | PASS |
| → `ar` | `المستخدم: admin (admin)` | PASS |

Catalogue parity: **245 keys** in each of fr/ar/en, sets identical, zero placeholder mismatches.

## Limits — stated, not implied

- **No native Arabic linguistic review was performed.** The screenshots were read by an automated
  agent; grammar, tone and register in the Arabic strings are unverified. This remains a required
  human gate (also listed as not executed in Phases 8–10).
- Rendering is verified on Linux with `QT_QPA_PLATFORM=offscreen`, not on a Windows host with native
  Arabic shaping. HarfBuzz shaping is the same library, but a Windows-specific regression cannot be
  excluded from this evidence.
- The demo company is synthetic; the numbers are real API output over synthetic data.
