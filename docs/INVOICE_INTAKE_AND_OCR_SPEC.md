# Supplier Invoice Intake and OCR Specification (Phase 3)

## 1. End-to-End Document & Invoice State Machine (`Verified`)

Every uploaded supplier invoice transitions through a strict, database-constrained state machine (`source_documents.workflow_state` and `supplier_invoices.workflow_state`):

```text
Uploaded
  └──► SecurityChecked
         ├──► Rejected           (Malware EICAR, PDF /JavaScript or /Launch active content)
         ├──► DuplicateRejected  (Exact SHA-256 file duplicate within the same company)
         └──► Processing
                ├──► Failed      (Unparseable or corrupted document)
                └──► NeedsReview (Extracted draft awaiting human review, or validation/3-way mismatch)
                       └──► Validated (All arithmetic, tax, identifier, duplicate & 3-way checks pass)
                              └──► PendingApproval
                                     ├──► Rejected (Approver rejected with mandatory reason)
                                     ├──► NeedsReview (If draft is edited after submission/approval)
                                     └──► Approved (Authorized human approver != creator)
                                            └──► Posted (Idempotent GL journal entry posted)
```

---

## 2. File Intake, Security Inspection & Private Vault (`dz_erp/intake_ocr.py`)

1. **MIME & Magic-Byte Verification (`inspect_uploaded_file_security`):**
   - Supported MIME types: `application/pdf` (`%PDF-`), `image/png` (`\x89PNG\r\n\x1a\n`), `image/jpeg` (`\xff\xd8\xff`).
   - Any mismatch between declared `mime_type` and binary magic bytes raises `ValueError` immediately.
   - Files exceeding `MAX_UPLOAD_BYTES` (`15 MB`) or empty files are rejected before OCR execution.
2. **Malware & Active-Content Quarantine:**
   - Scans raw bytes for EICAR test signatures and active PDF exploit payloads (`/JavaScript`, `/JS`, `/Launch`, `/OpenAction`, `/AA`, `/EmbeddedFiles`, `/RichMedia`).
   - Infected or active-content files are quarantined in `workflow_state = 'Rejected'`, `security_status = 'QUARANTINED'`, and logged in `audit_events` (`DOCUMENT_QUARANTINED`).
3. **Encrypted-at-Rest Private Document Vault (`PrivateDocumentVault`):**
   - Stores source files outside any public web root in content-addressed, company-partitioned paths (`vault/company_{id}/{sha256[:2]}/{sha256}.enc`).
   - Encrypts every document payload at rest using `cryptography.fernet.Fernet` (`AES-128-CBC` + `HMAC-SHA256`).
   - Enforces SHA-256 integrity checks on both write and read.

---

## 3. OCR & Structured Field Extraction Pipeline

1. **Hybrid Extraction Engine (`extract_document_ocr`):**
   - **Digital-born PDFs:** Extracted via `pdfplumber==0.11.6` with word-level bounding boxes (`[x0, top, x1, bottom]`) and full UTF-8 French and Arabic script fidelity.
   - **Scanned PDFs & Raster Images (`PNG` / `JPEG`):** Rendered at `200 DPI` via `pypdfium2==4.30.1` (if PDF) and processed via `rapidocr-onnxruntime==1.4.4` (`PP-OCRv4` ONNX detection, direction classification, and recognition models).
2. **Extracted Fields & Provenance (`ocr_extracted_fields`):**
   - Header fields: `supplier_name`, `supplier_nif`, `supplier_nis`, `supplier_ai`, `supplier_rc`, `invoice_number`, `invoice_date`, `due_date`, `po_number`, `grn_number`, `payment_method`, `currency_code`, `amount_ht_dzd`, `amount_tva_dzd`, `amount_timbre_dzd`, `amount_ttc_dzd`.
   - Line items (`ocr_extracted_lines`): `sku`, `description`, `qty`, `unit_price_dzd`, `tax_rate`, `line_ht_dzd`, `confidence`, `bbox_json`.
   - Every field stores `page_number`, `raw_text`, `normalized_value`, `confidence`, `bbox_json`, and `ocr_model_version`.
3. **Non-Destructive Human Review (`update_extracted_field_by_human`):**
   - When a human reviewer edits an extracted value, the update writes to `human_override_value`, `edited_by_id`, and `edited_at`.
   - SQLite trigger `trg_ocr_field_preserve_raw` blocks any SQL `UPDATE` that attempts to alter `raw_text`, `confidence`, or `ocr_model_version` (`OCR_RAW_IMMUTABILITY_VIOLATION`).
4. **Script Coverage & Review Routing (`assess_review_route`):**
   - The bundled recognition head is `PP-OCRv4` trained on Latin and Chinese text, so Arabic
     script cannot be recognised from an image — a model gap, stated in code as
     `ARABIC_RECOGNITION_HEAD_AVAILABLE = False`.
   - Each document is therefore routed explicitly. `standard_review` (verify the extracted
     fields) for native text, including native Arabic, and for a successful Latin recognition;
     `manual_transcription` (start from an empty form) when an image-recognised document is
     Arabic-dominant, declared Arabic via the `declared_language` upload field, or yielded no
     recognisable field at all.
   - `source_documents` records `extraction_route`, `route_reason`, `declared_language` and the
     Arabic/Latin letter counts; the same facts are in the `OCR_EXTRACTION_COMPLETED` audit
     event and in the upload response. Review remains mandatory on every route.
5. **Prompt-Injection Defense:**
   - All OCR text is scanned against adversarial patterns (`IGNORE PREVIOUS INSTRUCTIONS`, `SYSTEM PROMPT`, `APPROVE THIS INVOICE`, `<script`, `DROP TABLE`, etc.).
   - Matches are logged as `PROMPT_INJECTION_DETECTED:*` warnings and treated strictly as inert data strings; no extracted text is ever interpolated into executable SQL, shell commands, or autonomous tool-calling agents.
