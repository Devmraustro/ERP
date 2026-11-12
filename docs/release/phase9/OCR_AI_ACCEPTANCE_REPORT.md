# Phase 9D — OCR / AI Pipeline Acceptance

**Baseline:** PR #7 head `f603e38` · executed 2026-11-09 · synthetic documents only

## 1. The trust model the pipeline must satisfy

| Requirement | Enforced by | Evidence |
| --- | --- | --- |
| Documents are untrusted input | upload boundary: MIME allow-list, size cap, magic-byte check, extension never trusted | `test_phase8_upload_boundary_and_ci` (82 tests, passed) |
| No code execution from a document | PDF text layer parsed as data; no macro, no embedded-script, no `eval`; raster path is pure inference | code review + 82 boundary tests — **no test executes an embedded script because the parser never evaluates one** |
| OCR output is **not** a financial authorisation | extraction writes a *draft*; posting needs human review + approval + accountant role | `test_phase29_ocr_review_policy` (48), `test_phase33_invoice_lifecycle` (40) |
| AI cannot post without authorisation | the posting endpoint requires an accountant principal + `Idempotency-Key`; a machine extraction has no credential | executed drill: `clerk_self_approval → 403`, `post → 200` only for the accountant |
| Originals are preserved | vault keeps the untouched source; extraction is a separate record | `test_phase34_ocr_duplicate_row_integrity` (19), vault tests |
| Extraction ↔ correction traceability | per-field provenance, corrected values keep the original next to them | `test_phase18_ocr_confidence_provenance` (19) |

## 2. Documents exercised

The benchmark generator (`dz_erp/benchmark.py`) builds six Algerian-invoice strata,
three documents each, and `test_phase6_ocr_benchmark_e2e` runs the **real** engine over
all of them:

| Stratum | Characteristic | Covers the requirement |
| --- | --- | --- |
| S1 | French digital PDF | French, native text |
| S2 | Bilingual **French + Arabic** digital PDF | Arabic + mixed |
| S3 | Cash invoice with droit de timbre | timbre field extraction |
| S4 | Clean raster scan (PNG) | image OCR path |
| S5 | **Stamped, skewed (0.8°) and blurred (0.3)** scan | rotated / blurred / poor quality |
| S6 | Thermal cash receipt | low-quality capture |

Additional executed coverage: `test_phase21_arabic_review_routing` (19),
`test_phase29_ocr_review_policy` (48), `test_phase18` (19), `test_phase34` (19),
`test_phase3_invoice_intake` (2). **201 OCR-related tests, all passed.**

## 3. Honest limits of the OCR layer

1. **Arabic in a raster image is not recognised — by design.** The bundled recognition
   head cannot read Arabic script; a document declared Arabic that must be recognised
   from an image is **routed to manual transcription** and flagged for human entry.
   Arabic in a *digital* PDF text layer is extracted correctly. This is a product
   decision that keeps the system honest instead of guessing; it must be stated to
   users as "Arabic scans need manual entry".
2. **Confidence is not always measured.** The extractor distinguishes
   "confidence not measured" from "measured confidence"; a downstream consumer must not
   treat the absence of a score as high confidence.
3. **No adversarial prompt-injection corpus.** The pipeline does not send document text
   to an LLM for instruction — extraction is deterministic parsing + OCR — so classic
   prompt injection has **no surface** in V1. There is, however, **no executed test
   proving that claim for a future LLM-backed extractor**, and none is claimed.
   Requirement *"prompt-injection resistance"* is therefore: **N/A for V1
   architecture, NOT EXECUTED as a test.**
4. **No hallucination test suite.** Deterministic extraction cannot hallucinate a
   *value* out of nothing, but it can mis-associate fields; that is caught by
   reconciliation (header HT/TVA/TTC, duplicate detection) and human review, not by a
   dedicated hallucination test. **NOT EXECUTED as a named test.**
5. **Conflicting totals / missing fields** are handled by the review policy (a missing
   timbre is explicitly allowed; a conflicting total is refused) — covered by
   phase29/phase33 rather than by a dedicated adversarial module.
6. **Duplicate documents** are detected and the attempt is recorded as
   `DuplicateRejected` (executed in the Phase 9B drill).

## 4. Security of the document path (measured)

- Upload cap enforced with `413` and no partial write (Phase 8C/9A).
- Manifest-based external dataset loader refuses path traversal, duplicate ids,
  unsupported types, and missing ground truth/authorisation fields
  (`test_phase6_ocr_benchmark_e2e`, 6 tests).
- Vault stores encrypted blobs; the key file is `0600` and its directory `0700`
  (V1 custody model, exercised by the rotation/custody suites).

## 5. Throughput (measured, Phase 9G)

| Path | Mean per page | Notes |
| --- | --- | --- |
| Native PDF text extraction | **0.01 s** | digital invoices |
| Raster → RapidOCR PP-OCRv4 inference | **2.29 s** | scans; measured on the synthetic S4 page |

## 6. Verdict

The pipeline satisfies every requirement that V1's deterministic architecture puts in
scope, with 201 executed tests and two requirements explicitly marked N/A or NOT
EXECUTED (prompt injection, hallucination) rather than silently assumed. Arabic image
recognition is deliberately replaced by human transcription and must be communicated
as such.
