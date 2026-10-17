# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 3 — Supplier Invoice Intake, Security Checks, OCR Extraction & Deterministic Validation."""

import hashlib
import io
import json
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pdfplumber
import pypdf
import pypdfium2 as pdfium
from cryptography.fernet import Fernet
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

from .database import AuthorizationError, ERPDatabase, FinancialIntegrityError, TenantIsolationError
from .decimal_utils import format_dzd, round_dzd, to_decimal
from .localization import calculate_droit_de_timbre, validate_algerian_identifiers

EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
DANGEROUS_PDF_TOKENS = (b"/JavaScript", b"/JS", b"/Launch", b"/RichMedia")

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+instruction\s*:", re.IGNORECASE),
    re.compile(r"mark\s+invoice\s+as\s+approved", re.IGNORECASE),
    re.compile(r"change\s+(supplier\s+)?(iban|rib|bank)", re.IGNORECASE),
    re.compile(r"auto[_\-\s]?post", re.IGNORECASE),
]

_RAPID_OCR_SINGLETON: Optional[RapidOCR] = None


def get_rapid_ocr() -> RapidOCR:
    global _RAPID_OCR_SINGLETON
    if _RAPID_OCR_SINGLETON is None:
        _RAPID_OCR_SINGLETON = RapidOCR()
    return _RAPID_OCR_SINGLETON


def normalize_invoice_ref(raw_ref: str) -> str:
    """Normalize an invoice number for duplicate detection (uppercase alphanumeric only)."""
    return re.sub(r"[^A-Z0-9]", "", raw_ref.upper())


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


class PrivateDocumentVault:
    """Encrypted-at-rest, company-isolated private document storage."""

    def __init__(self, vault_root: Path, encryption_key: Optional[bytes] = None) -> None:
        self.vault_root = Path(vault_root)
        self.vault_root.mkdir(parents=True, exist_ok=True)
        self.encryption_key = encryption_key or Fernet.generate_key()
        self._fernet = Fernet(self.encryption_key)

    def store_bytes(self, company_id: int, file_sha256: str, content: bytes) -> str:
        comp_dir = self.vault_root / f"company_{company_id}"
        comp_dir.mkdir(parents=True, exist_ok=True)
        dest = comp_dir / f"{file_sha256}.enc"
        if not dest.exists():
            encrypted = self._fernet.encrypt(content)
            dest.write_bytes(encrypted)
        return str(dest)

    def read_bytes(self, company_id: int, requesting_company_id: int, vault_path: str) -> bytes:
        if company_id != requesting_company_id:
            raise TenantIsolationError(
                f"Cross-company file access denied: company {requesting_company_id} cannot read company {company_id} vault."
            )
        p = Path(vault_path).resolve()
        expected_dir = (self.vault_root / f"company_{company_id}").resolve()
        if expected_dir not in p.parents:
            raise TenantIsolationError("Vault path traversal or cross-company path detected.")
        encrypted = p.read_bytes()
        return self._fernet.decrypt(encrypted)


def inspect_and_validate_file_security(
    content: bytes,
    declared_mime: str,
    max_bytes: int = 10 * 1024 * 1024,
    max_pages: int = 20,
    max_dimension_px: int = 6000,
) -> Dict[str, Any]:
    """Validate file size, magic bytes, page/dimension limits, and malware/active-script signatures."""
    if not content:
        raise ValueError("Uploaded file is empty.")
    if len(content) > max_bytes:
        raise ValueError(f"File size {len(content)} exceeds maximum allowed {max_bytes} bytes.")

    findings: List[str] = []
    if EICAR_SIGNATURE in content:
        return {
            "security_status": "QUARANTINED",
            "page_count": 1,
            "findings": ["MALWARE_EICAR_SIGNATURE_DETECTED"],
        }

    if declared_mime == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("MIME type mismatch: declared application/pdf but missing %PDF- signature.")
        for token in DANGEROUS_PDF_TOKENS:
            if token in content:
                findings.append(f"PDF_ACTIVE_CONTENT_{token.decode('ascii', errors='ignore').strip('/')}")
        if findings:
            return {
                "security_status": "QUARANTINED",
                "page_count": 1,
                "findings": findings,
            }
        reader = pypdf.PdfReader(io.BytesIO(content))
        page_count = len(reader.pages)
        if page_count < 1 or page_count > max_pages:
            raise ValueError(f"PDF page count {page_count} outside allowed range [1, {max_pages}].")
        return {"security_status": "CLEAN", "page_count": page_count, "findings": []}

    elif declared_mime in ("image/png", "image/jpeg"):
        if declared_mime == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("MIME type mismatch: declared image/png but invalid PNG magic bytes.")
        if declared_mime == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
            raise ValueError("MIME type mismatch: declared image/jpeg but invalid JPEG magic bytes.")
        img = Image.open(io.BytesIO(content))
        w, h = img.size
        if w > max_dimension_px or h > max_dimension_px:
            raise ValueError(f"Image dimensions {w}x{h} exceed limit {max_dimension_px}px.")
        return {"security_status": "CLEAN", "page_count": 1, "findings": []}

    else:
        raise ValueError(f"Unsupported MIME type: {declared_mime}")


def detect_prompt_injection(text: str) -> List[str]:
    """Scan extracted text for embedded adversarial prompt-injection instructions."""
    hits: List[str] = []
    for pat in PROMPT_INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(f"PROMPT_INJECTION_PATTERN:{m.group(0)}")
    return hits


def parse_structured_invoice_text(
    lines_with_meta: List[Tuple[str, float, List[List[float]]]],
) -> Dict[str, Any]:
    """Extract structured Algerian invoice fields and line items from OCR/PDF lines."""
    full_text = "\n".join(item[0] for item in lines_with_meta)
    injection_warnings = detect_prompt_injection(full_text)

    fields: Dict[str, Dict[str, Any]] = {}
    parsed_lines: List[Dict[str, Any]] = []

    def _record_field(name: str, raw: str, norm: str, conf: float, bbox: List[List[float]]) -> None:
        if name not in fields:
            fields[name] = {
                "raw_text": raw,
                "normalized_value": norm,
                "confidence": round(float(conf), 4),
                "bbox": bbox,
            }

    for text_line, conf, bbox in lines_with_meta:
        # Ignore prompt injection sentences so they can never pollute structured fields
        if detect_prompt_injection(text_line):
            continue

        # Supplier Name
        m_sup = re.search(r"(?:FOURNISSEUR|SUPPLIER|VENDEUR|شركة|المورد)\s*[:\-]\s*([^\n|]+)", text_line, re.IGNORECASE)
        if m_sup:
            val = m_sup.group(1).strip()
            _record_field("supplier_name", text_line, val, conf, bbox)

        # NIF
        m_nif = re.search(r"NIF\s*[:\-]?\s*([0-9]{15}(?:[0-9]{5})?)", text_line, re.IGNORECASE)
        if m_nif:
            _record_field("supplier_nif", m_nif.group(0), m_nif.group(1), conf, bbox)

        # NIS
        m_nis = re.search(r"NIS\s*[:\-]?\s*([0-9]{15})", text_line, re.IGNORECASE)
        if m_nis:
            _record_field("supplier_nis", m_nis.group(0), m_nis.group(1), conf, bbox)

        # AI
        m_ai = re.search(r"(?:AI|ARTICLE)\s*[:\-]?\s*([0-9]{11})", text_line, re.IGNORECASE)
        if m_ai:
            _record_field("supplier_ai", m_ai.group(0), m_ai.group(1), conf, bbox)

        # RC (stop before AI/NIS/NIF when OCR strips spaces)
        m_rc = re.search(
            r"RC\s*[:\-]?\s*([0-9]{2}/[0-9]{2}[\-][0-9A-Z]{7,12}?)(?=\s*(?:AI|ARTICLE|NIS|NIF)|$)",
            text_line,
            re.IGNORECASE,
        )
        if m_rc:
            _record_field("supplier_rc", m_rc.group(0), m_rc.group(1), conf, bbox)

        # Invoice number (handles both spaced PDF text and spaceless RapidOCR text)
        m_inv = re.search(
            r"(?:FACTURE\s*N[°o:]?|INVOICE\s*NO|N[°o]\s*FACTURE|فاتورة\s*رقم)\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{3,28}?)(?=\s*(?:DATE|DU|NIF|PO|تاريخ)|$)",
            text_line,
            re.IGNORECASE,
        )
        if m_inv:
            raw_inv = m_inv.group(1).strip()
            _record_field("invoice_number", m_inv.group(0), raw_inv, conf, bbox)

        # Invoice Date
        m_date = re.search(r"(?:DATE(?:\s*FACTURE)?|تاريخ)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})", text_line, re.IGNORECASE)
        if m_date:
            d_str = m_date.group(1)
            if "/" in d_str:
                dd, mm, yyyy = d_str.split("/")
                d_str = f"{yyyy}-{mm}-{dd}"
            _record_field("invoice_date", m_date.group(0), d_str, conf, bbox)

        # Due Date
        m_due = re.search(r"(?:ECHEANCE|DUE\s*DATE)\s*[:\-]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text_line, re.IGNORECASE)
        if m_due:
            _record_field("due_date", m_due.group(0), m_due.group(1), conf, bbox)

        # PO Reference
        m_po = re.search(r"(?:PO|BC|COMMANDE)\s*[:\-]?\s*(PO-[0-9]{4}-[0-9]{3,6})", text_line, re.IGNORECASE)
        if m_po:
            _record_field("po_number", m_po.group(0), m_po.group(1).upper(), conf, bbox)

        # GRN Reference
        m_grn = re.search(r"(?:BL|BR|GRN|RECEPTION)\s*[:\-]?\s*(GRN-[0-9]{4}-[0-9]{3,6})", text_line, re.IGNORECASE)
        if m_grn:
            _record_field("grn_number", m_grn.group(0), m_grn.group(1).upper(), conf, bbox)

        # Payment Method
        if re.search(r"(?:PAIEMENT|MODE)\s*[:\-]?\s*(?:ESPECES|ESPÈCES|CASH|نقدا)", text_line, re.IGNORECASE):
            _record_field("payment_method", text_line, "CASH", conf, bbox)
        elif re.search(r"(?:PAIEMENT|MODE)\s*[:\-]?\s*(?:VIREMENT|BANK_TRANSFER)", text_line, re.IGNORECASE):
            _record_field("payment_method", text_line, "BANK_TRANSFER", conf, bbox)
        elif re.search(r"(?:PAIEMENT|MODE)\s*[:\-]?\s*(?:CHEQUE|CHÈQUE)", text_line, re.IGNORECASE):
            _record_field("payment_method", text_line, "CHEQUE", conf, bbox)

        # Currency
        if re.search(r"(DZD|DA|د\.ج)", text_line):
            _record_field("currency_code", "DZD", "DZD", conf, bbox)

        # Totals: Total HT, TVA, Timbre, Total TTC
        m_ht = re.search(r"TOTAL\s*HT\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_ht:
            _record_field("amount_ht_dzd", m_ht.group(0), format_dzd(m_ht.group(1)), conf, bbox)

        m_tva = re.search(r"(?:TOTAL\s*)?TVA(?:\s*(?:19|9)\s*%)?\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_tva and "ITEM" not in text_line.upper():
            _record_field("amount_tva_dzd", m_tva.group(0), format_dzd(m_tva.group(1)), conf, bbox)

        m_timbre = re.search(r"(?:DROIT\s*DE\s*)?TIMBRE\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_timbre:
            _record_field("amount_timbre_dzd", m_timbre.group(0), format_dzd(m_timbre.group(1)), conf, bbox)

        m_ttc = re.search(r"TOTAL\s*TTC\s*[:\-]?\s*([0-9]+(?:\.[0-9]{2})?)", text_line, re.IGNORECASE)
        if m_ttc:
            _record_field("amount_ttc_dzd", m_ttc.group(0), format_dzd(m_ttc.group(1)), conf, bbox)

        # Line items format (supports both spaced PDF and spaceless RapidOCR output)
        m_line = re.search(
            r"ITEM\s*([A-Z0-9]+-[0-9]+)\s*(.+?)\s*QTY[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*PU[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*TVA[:=]\s*([0-9]+(?:\.[0-9]+)?)%?\s*HT[:=]\s*([0-9]+(?:\.[0-9]+)?)",
            text_line,
            re.IGNORECASE,
        )
        if m_line:
            parsed_lines.append(
                {
                    "sku": m_line.group(1).strip().upper(),
                    "description": m_line.group(2).strip(),
                    "qty": m_line.group(3),
                    "unit_price_dzd": m_line.group(4),
                    "tax_rate": m_line.group(5),
                    "line_ht_dzd": format_dzd(m_line.group(6)),
                    "confidence": round(float(conf), 4),
                    "bbox": bbox,
                }
            )

    return {
        "fields": fields,
        "lines": parsed_lines,
        "prompt_injection_warnings": injection_warnings,
    }


def extract_document_ocr(content: bytes, mime_type: str, force_image_ocr: bool = False) -> Dict[str, Any]:
    """Run PDF native text extraction or RapidOCR PP-OCRv4 neural network inference."""
    t0 = time.perf_counter()
    lines_with_meta: List[Tuple[str, float, List[List[float]]]] = []
    engine_name = "rapidocr-onnxruntime"
    model_version = "rapidocr-1.4.4-ppocrv4"

    if mime_type == "application/pdf" and not force_image_ocr:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                txt = page.extract_text() or ""
                if txt.strip():
                    engine_name = "pdfplumber"
                    model_version = "pdfplumber-0.11.6+pdfminer"
                    for line_idx, raw_line in enumerate(txt.splitlines()):
                        if raw_line.strip():
                            bbox = [[0.0, float(line_idx * 15)], [500.0, float(line_idx * 15)], [500.0, float((line_idx + 1) * 15)], [0.0, float((line_idx + 1) * 15)]]
                            lines_with_meta.append((raw_line.strip(), 0.995, bbox))

    if not lines_with_meta:
        ocr = get_rapid_ocr()
        if mime_type == "application/pdf":
            pdf_doc = pdfium.PdfDocument(content)
            pil_images = [page.render(scale=2.0).to_pil() for page in pdf_doc]
        else:
            pil_images = [Image.open(io.BytesIO(content)).convert("RGB")]

        for pil_img in pil_images:
            res, _ = ocr(np.array(pil_img))
            if res:
                for box, text, score in res:
                    bbox_coords = [[float(pt[0]), float(pt[1])] for pt in box]
                    lines_with_meta.append((str(text), float(score), bbox_coords))

    latency_ms = (time.perf_counter() - t0) * 1000.0
    parsed = parse_structured_invoice_text(lines_with_meta)
    return {
        "engine_name": engine_name,
        "model_version": model_version,
        "latency_ms": round(latency_ms, 2),
        "raw_lines": [
            {"text": txt, "confidence": round(conf, 4), "bbox": bbox}
            for txt, conf, bbox in lines_with_meta
        ],
        "fields": parsed["fields"],
        "lines": parsed["lines"],
        "prompt_injection_warnings": parsed["prompt_injection_warnings"],
    }


def upload_and_process_supplier_document(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    company_id: int,
    user_id: int,
    filename: str,
    mime_type: str,
    content: bytes,
    branch_id: Optional[int] = None,
    force_image_ocr: bool = False,
) -> Dict[str, Any]:
    """Ingest a supplier invoice document through Uploaded -> SecurityChecked -> Processing -> Extracted."""
    file_sha256 = hashlib.sha256(content).hexdigest()
    doc_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    # Check exact SHA-256 content duplicate within the same company
    with db.transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user or user["company_id"] != company_id:
            raise TenantIsolationError("User does not belong to target company.")

        existing = conn.execute(
            """
            SELECT id, workflow_state FROM source_documents
            WHERE company_id = ? AND file_sha256 = ? AND workflow_state != 'DuplicateRejected'
            """,
            (company_id, file_sha256),
        ).fetchone()

        sec = inspect_and_validate_file_security(content, mime_type)
        vault_path = vault.store_bytes(company_id, file_sha256, content)

        if sec["security_status"] == "QUARANTINED":
            conn.execute(
                """
                INSERT INTO source_documents (
                    id, company_id, branch_id, filename, mime_type, byte_size, page_count,
                    file_sha256, vault_path, encrypted_at_rest, security_status, security_findings,
                    workflow_state, rejection_reason, uploaded_by_id, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'QUARANTINED', ?, 'Rejected', ?, ?, ?)
                """,
                (
                    doc_id,
                    company_id,
                    branch_id,
                    filename,
                    mime_type,
                    len(content),
                    sec["page_count"],
                    file_sha256,
                    vault_path,
                    json.dumps(sec["findings"]),
                    "SECURITY_QUARANTINE:" + ",".join(sec["findings"]),
                    user_id,
                    now_iso,
                ),
            )
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="DOCUMENT_QUARANTINED",
                target_model="source_documents",
                target_id=doc_id,
                after_state={"filename": filename, "findings": sec["findings"]},
            )
            return {
                "document_id": doc_id,
                "workflow_state": "Rejected",
                "security_status": "QUARANTINED",
                "rejection_reason": "SECURITY_QUARANTINE:" + ",".join(sec["findings"]),
            }

        if existing:
            conn.execute(
                """
                INSERT INTO source_documents (
                    id, company_id, branch_id, filename, mime_type, byte_size, page_count,
                    file_sha256, vault_path, encrypted_at_rest, security_status, security_findings,
                    workflow_state, rejection_reason, uploaded_by_id, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'CLEAN', '[]', 'DuplicateRejected', ?, ?, ?)
                """,
                (
                    doc_id,
                    company_id,
                    branch_id,
                    filename,
                    mime_type,
                    len(content),
                    sec["page_count"],
                    file_sha256,
                    vault_path,
                    f"DUPLICATE_FILE_SHA256_OF_{existing['id']}",
                    user_id,
                    now_iso,
                ),
            )
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="DUPLICATE_DOCUMENT_REJECTED",
                target_model="source_documents",
                target_id=doc_id,
                after_state={"file_sha256": file_sha256, "original_document_id": existing["id"]},
            )
            return {
                "document_id": doc_id,
                "workflow_state": "DuplicateRejected",
                "security_status": "CLEAN",
                "rejection_reason": f"DUPLICATE_FILE_SHA256_OF_{existing['id']}",
            }

        # State 1 -> 2 -> 3: Uploaded -> SecurityChecked -> Processing
        conn.execute(
            """
            INSERT INTO source_documents (
                id, company_id, branch_id, filename, mime_type, byte_size, page_count,
                file_sha256, vault_path, encrypted_at_rest, security_status, security_findings,
                workflow_state, uploaded_by_id, uploaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'CLEAN', '[]', 'Processing', ?, ?)
            """,
            (doc_id, company_id, branch_id, filename, mime_type, len(content), sec["page_count"], file_sha256, vault_path, user_id, now_iso),
        )

    # Execute OCR outside the DB lock, then persist extracted fields atomically
    ocr_result = extract_document_ocr(content, mime_type, force_image_ocr=force_image_ocr)

    with db.transaction() as conn:
        for fname, fmeta in ocr_result["fields"].items():
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, bbox_json, ocr_model_version, created_at
                ) VALUES (?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    doc_id,
                    fname,
                    fmeta["raw_text"],
                    fmeta["normalized_value"],
                    fmeta["confidence"],
                    json.dumps(fmeta["bbox"]),
                    ocr_result["model_version"],
                    now_iso,
                ),
            )

        for idx, litem in enumerate(ocr_result["lines"]):
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, bbox_json, ocr_model_version, created_at
                ) VALUES (?, ?, 1, ?, 'line_item_json', ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    doc_id,
                    idx,
                    json.dumps(litem, ensure_ascii=False),
                    json.dumps(litem, ensure_ascii=False),
                    litem["confidence"],
                    json.dumps(litem["bbox"]),
                    ocr_result["model_version"],
                    now_iso,
                ),
            )

        next_state = "NeedsReview"
        conn.execute(
            """
            UPDATE source_documents
            SET workflow_state = ?, ocr_engine = ?, ocr_model_version = ?, ocr_latency_ms = ?
            WHERE id = ?
            """,
            (next_state, ocr_result["engine_name"], ocr_result["model_version"], ocr_result["latency_ms"], doc_id),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="OCR_EXTRACTION_COMPLETED",
            target_model="source_documents",
            target_id=doc_id,
            after_state={
                "workflow_state": next_state,
                "ocr_engine": ocr_result["engine_name"],
                "ocr_model_version": ocr_result["model_version"],
                "extracted_field_count": len(ocr_result["fields"]),
                "extracted_line_count": len(ocr_result["lines"]),
                "prompt_injection_warnings": ocr_result["prompt_injection_warnings"],
            },
        )

    return {
        "document_id": doc_id,
        "workflow_state": next_state,
        "security_status": "CLEAN",
        "ocr_engine": ocr_result["engine_name"],
        "ocr_model_version": ocr_result["model_version"],
        "latency_ms": ocr_result["latency_ms"],
        "fields": ocr_result["fields"],
        "lines": ocr_result["lines"],
        "prompt_injection_warnings": ocr_result["prompt_injection_warnings"],
    }


def update_extracted_field_by_human(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    document_id: str,
    field_name: str,
    new_value: str,
) -> Dict[str, Any]:
    """Record a human edit on an extracted field without overwriting raw_text or confidence."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ? AND field_name = ?",
            (company_id, document_id, field_name),
        ).fetchone()
        if not row:
            raise ValueError(f"Field {field_name} not found on document {document_id}")

        conn.execute(
            """
            UPDATE ocr_extracted_fields
            SET human_override_value = ?, normalized_value = ?, edited_by_id = ?, edited_at = ?
            WHERE id = ?
            """,
            (new_value, new_value, user_id, now_iso, row["id"]),
        )
        updated = conn.execute("SELECT * FROM ocr_extracted_fields WHERE id = ?", (row["id"],)).fetchone()
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="OCR_HUMAN_FIELD_EDIT",
            target_model="ocr_extracted_fields",
            target_id=str(row["id"]),
            before_state={"normalized_value": row["normalized_value"], "raw_text": row["raw_text"]},
            after_state={"human_override_value": new_value, "raw_text": updated["raw_text"]},
        )
        return dict(updated)
