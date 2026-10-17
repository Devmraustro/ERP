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
| **Expected Calibration Error (`ECE`, 5 bins)** | **`0.79%`** (`0.0079`) |
| **Latency Mean (`ms / document`)** | **`756.79 ms`** |
| **Latency Median / `p50` (`ms / document`)** | **`675.17 ms`** |
| **Latency Max (`ms / document`)** | **`1,599.22 ms`** |

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
| `S1_FRENCH_DIGITAL_PDF` | 3 | `pdfplumber` | **`100.00%`** | `31.11 ms` |
| `S2_BILINGUAL_FR_AR_DIGITAL_PDF` | 3 | `pdfplumber` | **`100.00%`** | `30.33 ms` |
| `S3_CASH_TIMBRE_DIGITAL_PDF` | 3 | `pdfplumber` | **`100.00%`** | `29.10 ms` |
| `S4_CLEAN_RASTER_SCAN_PNG` | 3 | `RapidOCR (PP-OCRv4)` | **`70.83%`** | `1,558.52 ms` |
| `S5_STAMPED_SKEWED_SCAN_PNG` | 3 | `RapidOCR (PP-OCRv4)` | **`100.00%`** | `1,497.07 ms` |
| `S6_THERMAL_CASH_RECEIPT_PNG` | 3 | `RapidOCR (PP-OCRv4)` | **`75.00%`** | `1,394.63 ms` |

---

## 5. Failure Analysis & Architectural Mitigations (`Verified`)

1. **Multi-Identifier Line Merging & Character Confusion in `RapidOCR` (`S4`, `S6`):**
   - When `RC` and `AI` are printed on the same horizontal line (`RC: 16/00-0991216B12 AI: 16019912316`) at smaller or un-antialiased raster resolutions, `RapidOCR`'s CTC decoder occasionally misreads the slash `/` in `16/00` as `1` (`16100`) or the `I` in `AI:` as `l` (`Al:`).
   - Because our regex extractor prioritizes **100% precision over false-positive guessing** (`precision == 1.0` across all 8 fields), any identifier that fails format validation is left blank rather than hallucinated, automatically routing the invoice to `NeedsReview` where the human clerk selects the supplier from the master `partners` directory (or edits the field via `update_extracted_field_by_human`).
2. **Scanned Arabic Recognition Limitation in Default `RapidOCR` Bundle:**
   - While `pdfplumber` extracts digital-born Arabic script (`S2_BILINGUAL_FR_AR_DIGITAL_PDF`) with **100% accuracy**, `rapidocr-onnxruntime==1.4.4` ships by default with `ch_PP-OCRv4_rec_infer.onnx` (Latin/digits/CJK dictionary). Scanned Arabic-only invoices require provisioning the `arabic_PP-OCRv4_rec` ONNX recognition head alongside the detector.
3. **Why Zero Accounting Errors Occurred Despite OCR Imperfections:**
   - Every monetary field (`amount_ht_dzd`, `amount_tva_dzd`, `amount_ttc_dzd`) achieved `100.00%` accuracy, and even if an OCR error occurs on a monetary field or invoice number, deterministic cross-foot validation (`create_and_validate_supplier_invoice`) and mandatory human review (`NeedsReview` $\rightarrow$ `Validated` $\rightarrow$ `Approved`) prevent any corrupted value from reaching the General Ledger.
