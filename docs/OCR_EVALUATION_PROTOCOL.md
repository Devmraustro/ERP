# OCR evaluation protocol: what to measure before trusting the extractor

**Purpose.** Define how OCR/extraction quality must be measured so that any change — an
Arabic recognition head, a confidence threshold, a migration to `rapidocr` 3.x — is decided
on evidence rather than on a demo. It also documents what the existing benchmark
(`docs/OCR_BENCHMARK_REPORT.md`) can and cannot support.

**No dependency change is made here.** This is the protocol and the analysis of the current
engine; swapping the engine is a separate, gated decision (§8).

---

## 1. Why a protocol, and not another accuracy number

The current report states 90.97 % overall field exact-match across 144 field instances from
18 documents. Read against a 95 % Wilson interval, that is **85.2 %–94.7 %** — the true
accuracy is plausibly below 90 %, and the report's headline is the point estimate, not the
guarantee. Likewise "supplier_rc: 100 % on 12 extracted" is 12/12 with a lower bound of
~0.74, and two documents were missed entirely.

That is not a criticism of the measurement; it is a statement about sample size. It matters
because these numbers feed a go/no-go decision about paying clerks to fix what OCR gets
wrong. The protocol below makes the uncertainty explicit and sets a sample size that can
support the claim.

---

## 2. Current engine: licensing, dependencies, and API status

Facts recorded from PyPI during this work; they are the basis for §7–§8, not opinions.

| Item | `rapidocr-onnxruntime` (in use) | `rapidocr` (successor) |
|---|---|---|
| Latest version seen | 1.4.4 | 3.10.0 |
| Licence | Apache-2.0 | Apache-2.0 |
| Python support | `<3.13, >=3.6` | (see project page) |
| Runtime deps | `opencv-python>=4.5.1.48`, `numpy<3`, `shapely!=2.0.4`, `onnxruntime>=1.7.0` | depends on `opencv-python-headless` |
| API | `RapidOCR()(np.ndarray) -> (results, elapsed)` | **different API surface** |
| Arabic recognition | Not shipped in the default bundle (`ch_PP-OCRv4_rec`) | Not a drop-in for the same reason |

Consequences, stated plainly:

1. **Licence is not an obstacle.** Both are Apache-2.0; no copyleft obligation, no attribution
   burden beyond the licence file. This removes licensing from the decision.
2. **The dependency graph is deliberately non-standard here.** `opencv-python` is substituted
   (the project pins a headless/slim variant for deployment), so `pip check` reports one
   documented, allow-listed mismatch. That is already recorded in
   `docs/DEPENDENCY_AND_LICENSE_REGISTER.md` and is not concealed by this protocol.
3. **Migration is a project, not a bump.** `rapidocr` 3.x changes the call shape and pulls
   `opencv-python-headless`; adopting it means re-running the whole evaluation in §5 against
   the new API, not editing an import.
4. **Python 3.13 is not supported** by the pinned engine (`<3.13`). That is a real ceiling on
   the runtime upgrade path and belongs in the risk register.

---

## 3. What is actually being measured (and the confound to fix first)

`extract_document_ocr()` assigns **`0.995` to every line** extracted from a digital-born PDF
by `pdfplumber` — a constant, not a measured probability. OCR lines carry the recogniser's
score for the **whole line**, and `parse_structured_invoice_text` propagates that line score
to every field matched from it.

Therefore:

* A field's `confidence` currently means *"the engine was sure about this line"*, **not**
  *"this value is correct"*. For digital PDFs it means nothing at all — it is a constant.
* The reported ECE of 0.79 % is computed over a population dominated by that constant, so it
  does **not** establish that OCR confidence is calibrated. It shows the field extractor is
  conservative about what it emits (precision 100 %, recall lower), which is a different and
  good property.
* Any rule of the form "auto-post when confidence > 0.95" would be built on a number that has
  not been shown to predict correctness.

**Prerequisite P0 for every measurement below:** report metrics separately by extraction
path (`pdfplumber` vs OCR) and by language, and stop emitting a constant confidence for
digital text — either emit `null`/"not applicable" or derive a per-field score from match
evidence (regex strictness, digit checksum, cross-foot agreement). Until then calibration is
only meaningful for the OCR path, measured on its own.

---

## 4. Dataset design

### 4.1 Strata (fixed before measurement)

| Stratum | Content | Why |
|---|---|---|
| D1 | French digital PDF | Baseline; native text |
| D2 | Bilingual FR/AR digital PDF | Native Arabic, no OCR |
| D3 | Arabic-only digital PDF | Native RTL text order and shaping |
| S1 | Clean 300 dpi scan | OCR baseline |
| S2 | Skewed (±5°) + stamp overlay | Realistic field scans |
| S3 | Thermal receipt, low contrast | Worst-case documents already in scope |
| S4 | Arabic-only scan | **The known gap** — default rec dictionary is Latin/CJK |
| S5 | Mixed FR/AR lines on one page | Direction switching mid-document |
| S6 | Degraded: JPEG q=40, 150 dpi, slight blur | Lower bound on tolerable quality |

Handwritten annotations and photographed (phone) documents are **out of scope** and must be
stated as out of scope in any accuracy claim.

### 4.2 Sample size

Per stratum and per field, to claim accuracy *p* within ±δ at 95 %:

| Claim | Required independent field instances |
|---|---|
| ±5 % at p≈0.95 | 73 |
| ±3 % at p≈0.95 | 203 |
| "no errors observed" with 95 % upper bound ≤5 % | 59 consecutive correct |

With 6 fields per document, 203 instances per stratum ≈ 34 documents per stratum ≈ 300
documents across 9 strata. The current benchmark has 3 documents per stratum; that is a
**smoke test**, and must be labelled as such.

### 4.3 Ground truth

* Two independent annotators per document; disagreements resolved by a third; record
  **inter-annotator agreement** per field (Cohen's κ). A field where annotators disagree at
  κ < 0.8 has an ambiguous definition, and its "accuracy" is not meaningful until the
  definition is fixed.
* Arabic numeric forms: record both the literal string and the normalised value
  (Arabic-Indic `٠١٢٣` → ASCII, tatweel removed, presentation forms folded). Accuracy is
  scored on the **normalised** value, with literal preservation checks separately.
* Store ground truth as JSON beside the images with a content hash, so a metric can be
  recomputed later against the same inputs. Synthetic generation (ReportLab + DejaVuSans, as
  used today) is acceptable for pipeline testing but **not** for the accuracy claim: it has no
  scanner noise, skew or compression history.

---

## 5. Metrics (all reported as point estimate + 95 % interval)

| Metric | Definition | Why it is the right one |
|---|---|---|
| Field exact-match rate | normalised value equals ground truth | What the clerk experiences |
| CER / WER | Levenshtein over characters / words, per field type | Distinguishes "wrong" from "nearly right" |
| Precision / recall / abstention | emitted-and-correct vs emitted; missed vs abstained | **Abstention is a feature**: a blank that routes to review is cheap, a wrong value is expensive |
| Net financial error | Σ\|extracted − true\| over monetary fields, in DZD | The only error that reaches the ledger |
| False-accept rate at a threshold | fraction of auto-accepted fields that are wrong | Directly sets the accounting risk |
| ECE + reliability diagram (10 bins, per path, per language) | \|accuracy − confidence\| weighted by bin | Calibration, not just accuracy |
| Latency p50/p95 | wall clock per document, OCR path only | Throughput and clerk-perceived delay |
| Marginal cost | CPU-seconds per document | Capacity planning |

**Never report a single aggregate accuracy across paths or languages.** Averaging 100 %
digital-PDF accuracy with 70 % scanned accuracy produces a number that describes nothing.

---

## 6. Calibration and threshold selection (the part that prevents bad postings)

Procedure, per extraction path and per field type:

1. Bin predictions by reported confidence (10 equal-width bins; report bin counts — bins with
   < 30 samples are not evidence).
2. In each bin compute observed accuracy and a Wilson interval.
3. Choose the auto-accept threshold as the **lowest** confidence whose lower interval bound
   still meets the target error budget (e.g. ≤ 0.5 % false accepts for monetary fields).
   Use the *lower bound*, not the point estimate: the point estimate is what fails audit.
4. Everything below the threshold routes to human review — the existing `NeedsReview` state,
   which is already mandatory before `Validated`/`Approved`, so this is a tightening of an
   existing control rather than a new workflow.
5. Re-measure after every engine, model or preprocessing change; store the threshold with the
   model version (`ocr_model_version` already exists on extracted fields).

Current state: **no threshold is derived this way**, because the confidence is not yet a
per-field signal (P0). Until it is, the safe configuration is "everything reviewed", which is
what the workflow already requires.

---

## 7. Language coverage: Arabic is the open gap

* The default recognition head (`ch_PP-OCRv4_rec`) has no Arabic characters; an Arabic-only
  scan cannot be recognised regardless of image quality. This is a **model** gap, not an
  accuracy tuning problem, and the benchmark report already says so.
* Mitigation options, in increasing cost: (a) provision an Arabic `PP-OCRv4` recognition head
  and run per-document script detection; (b) route Arabic scans to human entry; (c) move to an
  engine with multilingual recognition.
* Whichever is chosen, S4/S5/S6 strata must be measured **separately**, and an Arabic-only
  document must never be counted as "extraction succeeded" when it was actually read by a
  human.
* RTL specifics that the evaluation must cover: line-order reversal, bidi mixing of Latin
  identifiers inside Arabic text, Arabic-Indic vs Western digits, and the dotted letters that
  differ by a single glyph (ب/پ/ت/ث, د/ذ, ر/ز).

---

## 8. Decision rule for any engine change (including `rapidocr` 3.x)

A change is accepted only if **all** of the following hold, measured on the §4.2 sample:

| Gate | Requirement |
|---|---|
| Field exact-match, monetary fields | ≥ current, with the 95 % lower bound ≥ 99.0 % |
| Field exact-match, identifiers (NIF/RC/AI) | ≥ current + 5 points, lower bound > current point estimate |
| False-accept rate at the chosen threshold | ≤ 0.5 % for monetary fields |
| Arabic-only scanned stratum (S4) | measured and reported; no regression, improvement required if an Arabic head is claimed |
| Latency p95, OCR path | ≤ 2× current (1 800 ms at current 1.6 s max) |
| Licence | Apache-2.0/MIT/BSD-compatible; no copyleft in the runtime path |
| Dependency graph | conflict-free, or a documented allow-list entry with a mitigation |
| Reproducibility | the evaluation script and pinned versions are committed; results regenerate |

If a gate fails, the change is rejected and the reason recorded — including "the Arabic gap
is not closed by the new engine alone".

---

## 9. Reproducing the current numbers

* Pipeline and metrics: `dz_erp/benchmark.py` (`run_empirical_ocr_benchmark`), artifact
  `benchmarks/results/ground_truth_and_results.json`, narrative in
  `docs/OCR_BENCHMARK_REPORT.md`.
* Pinned versions: `requirements.lock.txt`; licence register:
  `docs/DEPENDENCY_AND_LICENSE_REGISTER.md`.
* The 18-document set is synthetic and regenerable; the report must keep saying so.

---

## 10. Threats to validity

| Threat | Effect | Handling |
|---|---|---|
| Synthetic documents | No scanner noise, skew or compression | Never used for the accuracy claim; §4.3 |
| Small sample | Intervals wide (18/18 → 82–100 %) | §4.2 sample sizes |
| Single machine / CI variance | `extract_document_ocr` records 0.9097 on one machine vs 0.8958 on a CI runner for the same set | Report both; set the threshold against the lower run |
| Constant `0.995` confidence | Inflates apparent calibration | P0 in §3 |
| Extractor prioritises precision | Recall low on `RC`/`AI` (12/18) | Report abstention separately; the gap is the review load |
| Arabic missing entirely | Any aggregate hides it | Strata are reported separately; §7 |
| Ground-truth ambiguity | Two reasonable readings of a field | κ per field; fix definitions before scoring |

---

## 11. What this protocol does not do

* It does not claim the current extractor is accurate enough for unattended posting — it is
  not, and the workflow already requires human validation.
* It does not change any dependency, model, or threshold in this phase.
* It does not replace the financial controls: even a perfect OCR cannot prevent a valid-looking
  invoice for a fictitious supplier. Cross-footing, 3-way match and approval remain the
  controls that matter.
