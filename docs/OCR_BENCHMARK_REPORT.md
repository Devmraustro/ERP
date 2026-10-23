# Empirical OCR & Document Extraction Benchmark Report (Phase 6)

## 1. Benchmark Methodology & Dataset Provenance (`Verified`)

All metrics in this report were produced by executing `dz_erp/benchmark.py` (`run_empirical_ocr_benchmark`) against 18 generated Algerian commercial invoices (`BENCH-001` through `BENCH-018`) across 6 strata (`3` documents per stratum). Raw artifacts and the complete ground-truth/evaluation JSON manifest are saved in `/home/user/ERP/benchmarks/results/ground_truth_and_results.json`.

- **Dataset Provenance:** Synthetic, privacy-safe Algerian supplier invoices generated via `ReportLab` (`4.3.1`) and `Pillow` (`11.1.0`) using `DejaVuSans.ttf` (`UTF-8` Latin + Arabic script).
- **Extraction Engines Evaluated:**
  - `pdfplumber==0.11.6` (`pdfminer.six==20250327`) for digital-born PDFs (`S1`, `S2`, `S3`).
  - `rapidocr-onnxruntime==1.4.4` (`PP-OCRv4` ONNX detection + direction classification + recognition) for rasterized PNG scans (`S4`, `S5`, `S6`).

---

## 2. Aggregate Benchmark Metrics (`Verified`)

| Metric | Empirical Value (`18` Documents, `144` Field Instances) |
| :--- | :--- |
| **Overall Field Exact-Match Rate** | **`90.97%`** (`131 / 144` fields) |
| **Overall Character Error Rate (`CER`)** | **`11.13%`** |
| **Table Line-Item Association Accuracy** | **`100.00%`** (`18 / 18` documents matched SKU and Line HT exactly) |
| **Expected Calibration Error (`ECE`, 5 bins)** | **`1.32%`** (`0.0132`) over **59** measured confidences |
| Fields excluded from calibration | **85** — native PDF extraction measures no confidence |
| **Latency Mean (`ms / document`)** | **`817.62 ms`** |
| **Latency Median / `p50` (`ms / document`)** | **`682.05 ms`** |
| **Latency Max (`ms / document`)** | **`1,785.24 ms`** |

---

## 2.1 Correction: the earlier ECE was computed over a fabricated constant

Previous revisions reported an ECE of `0.79 %` (`0.0079`). That figure averaged over all 144
field instances, but 85 of them came from digital-born PDFs whose `confidence` was a constant
`0.995` written by the extractor — not a measurement. Averaging a perfect-accuracy constant into
a calibration metric produces a low ECE by construction, so the number described the constant,
not the engine.

The extraction path has been corrected (`confidence: null` with `confidence_kind:
not_measured` for native text; see `docs/OCR_EVALUATION_PROTOCOL.md` §3), and the benchmark now
computes ECE only over fields whose path measured a confidence. The honest figures:

| Metric | Value | Reading |
|---|---|---|
| ECE over measured confidences | `0.0132` | 59 observations, 5 bins (~12 per bin) |
| Observations excluded | 85 | no confidence existed to calibrate |
| Is calibration established? | **No** | below the 30-per-bin evidence floor (protocol §6) |

The field-accuracy numbers in §2–§4 are unaffected: they are computed from ground-truth
comparison and never depended on confidence.

---

> **Latency is host-dependent and must not be quoted as a fixed property.** These figures are the
> committed artefact (`benchmarks/results/ground_truth_and_results.json`), regenerated during the
> risk-closure round on the same 2-vCPU container used for the rotation drill. Successive runs of
> identical code have differed by more than 2x in mean OCR latency
> (ONNX Runtime/OpenCV kernel dispatch and page-cache state), so any latency claim needs its host
> and run recorded beside it. Field accuracy is the stable, checkable metric; latency is capacity
> data. The test now writes its output to a temporary directory, so a test run can no longer
> overwrite this artefact with a machine's own timings.

---

## 3. Field-Level Precision, Recall, Exact-Match & CER (`Verified`)

| Field Name | Ground-Truth Instances | Extracted Count | Exact Matches | Exact-Match Rate | Precision | Recall | CER |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `supplier_nif` (15-digit NIF) | 18 | 18 | 18 | **`100.00%`** | `100.00%` | `100.00%` | `0.00%` |
| `invoice_date` (`YYYY-MM-DD`) | 18 | 18 | 18 | **`100.00%`** | `100.00%` | `100.00%` | `0.00%` |
| `amount_ht_dzd` (`0.00 DZD` tol.) | 18 | 18 | 18 | **`100.00%`** | `100.00%` | `100.00%` | `0.00%` |
| `amount_tva_dzd` (`0.00 DZD` tol.) | 18 | 18 | 18 | **`100.00%`** | `100.00%` | `100.00%` | `0.00%` |
| `amount_ttc_dzd` (`0.00 DZD` tol.) | 18 | 18 | 18 | **`100.00%`** | `100.00%` | `100.00%` | `0.00%` |
| `invoice_number` (`FAC-2026-xxxx`) | 18 | 17 | 17 | **`94.44%`** | `100.00%` | `94.44%` | `5.56%` |
| `supplier_rc` (`16/00-...`) | 18 | 12 | 12 | **`66.67%`** | `100.00%` | `66.67%` | `33.33%` |
| `supplier_ai` (11-digit AI) | 18 | 12 | 12 | **`66.67%`** | `100.00%` | `66.67%` | `33.33%` |

---

## 4. Performance Breakdown by Document Stratum (`Verified`)

| Stratum | Document Count | Engine Used | Field Exact-Match Accuracy | Mean Latency (`ms`) |
| :--- | :---: | :--- | :---: | :---: |
| `S1_FRENCH_DIGITAL_PDF` | 3 | `pdfplumber` | **`100.00%`** | `7.56 ms` |
| `S2_BILINGUAL_FR_AR_DIGITAL_PDF` | 3 | `pdfplumber` | **`100.00%`** | `7.79 ms` |
| `S3_CASH_TIMBRE_DIGITAL_PDF` | 3 | `pdfplumber` | **`100.00%`** | `7.44 ms` |
| `S4_CLEAN_RASTER_SCAN_PNG` | 3 | `RapidOCR (PP-OCRv4)` | **`70.83%`** | `1,732.39 ms` |
| `S5_STAMPED_SKEWED_SCAN_PNG` | 3 | `RapidOCR (PP-OCRv4)` | **`100.00%`** | `1,595.22 ms` |
| `S6_THERMAL_CASH_RECEIPT_PNG` | 3 | `RapidOCR (PP-OCRv4)` | **`75.00%`** | `1,555.34 ms` |

---

## 5. Failure Analysis & Architectural Mitigations (`Verified`)

1. **Multi-Identifier Line Merging & Character Confusion in `RapidOCR` (`S4`, `S6`):**
   - When `RC` and `AI` are printed on the same horizontal line (`RC: 16/00-0991216B12 AI: 16019912316`) at smaller or un-antialiased raster resolutions, `RapidOCR`'s CTC decoder occasionally misreads the slash `/` in `16/00` as `1` (`16100`) or the `I` in `AI:` as `l` (`Al:`).
   - Because our regex extractor prioritizes **100% precision over false-positive guessing** (`precision == 1.0` across all 8 fields), any identifier that fails format validation is left blank rather than hallucinated, automatically routing the invoice to `NeedsReview` where the human clerk selects the supplier from the master `partners` directory (or edits the field via `update_extracted_field_by_human`).
2. **Scanned Arabic Recognition Limitation in Default `RapidOCR` Bundle — now routed explicitly:**
   - While `pdfplumber` extracts digital-born Arabic script (`S2_BILINGUAL_FR_AR_DIGITAL_PDF`) with **100% accuracy**, `rapidocr-onnxruntime==1.4.4` ships by default with `ch_PP-OCRv4_rec_infer.onnx` (Latin/digits/CJK dictionary). Scanned Arabic-only invoices require provisioning the `arabic_PP-OCRv4_rec` ONNX recognition head alongside the detector.
   - The pipeline no longer leaves that gap implicit. `ARABIC_RECOGNITION_HEAD_AVAILABLE = False` states
     the capability in code, and `assess_review_route` returns `manual_transcription` with reason
     `DECLARED_ARABIC_SCAN` for an image-recognised document that is Arabic-dominant or declared
     `declared_language="ar"`, and `NO_FIELDS_RECOGNISED` when the head yields nothing at all. Native
     Arabic text keeps the ordinary `standard_review` route with reason `NATIVE_ARABIC_TEXT_SUPPORTED`,
     because `pdfplumber` returns exact Unicode and there is nothing degraded about it.
   - The route, its reason, the declared language and the Arabic/Latin letter counts are stored on
     `source_documents` and recorded in the `OCR_EXTRACTION_COMPLETED` audit event, so a reviewer later
     knows whether they are verifying fields or transcribing a page. Tests:
     `tests/test_phase21_arabic_review_routing.py`.
   - This is a routing control, **not** an accuracy claim: no Arabic scan was measured, because the
     engine cannot produce a recognition for one. That stratum stays unmeasured until a head exists.
3. **Why Zero Accounting Errors Occurred Despite OCR Imperfections:**
   - Every monetary field (`amount_ht_dzd`, `amount_tva_dzd`, `amount_ttc_dzd`) achieved `100.00%` accuracy, and even if an OCR error occurs on a monetary field or invoice number, deterministic cross-foot validation (`create_and_validate_supplier_invoice`) and mandatory human review (`NeedsReview` $\rightarrow$ `Validated` $\rightarrow$ `Approved`) prevent any corrupted value from reaching the General Ledger.

---

## 6. Reproducibility of the artefacts

The synthetic corpus is now generated with ReportLab's `invariant=True`, which fixes the document
ID and timestamps. Two consecutive runs produce **byte-identical PDFs** (verified by SHA-256), so
regenerating the corpus no longer rewrites committed evidence with new `/CreationDate` values.

What remains host-dependent is the *measurement*, not the artefact: latencies vary between runs,
so the JSON's timing fields describe the run that produced them. The test suite therefore writes
its benchmark output to a temporary directory and asserts metrics, never timings
(`tests/test_phase6_ocr_benchmark_e2e.py`).
