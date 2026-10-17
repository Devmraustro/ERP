# SPDX-License-Identifier: LGPL-3.0-only
"""FastAPI REST API for Algerian AI ERP Platform with server-side RBAC, Tenant Isolation & Idempotency."""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from .database import AuthorizationError, AuditTamperError, ERPDatabase, FinancialIntegrityError, TenantIsolationError
from .intake_ocr import PrivateDocumentVault, update_extracted_field_by_human, upload_and_process_supplier_document
from .procurement_stock import (
    create_and_validate_supplier_invoice,
    decide_invoice_approval,
    post_supplier_invoice,
    reverse_posted_invoice,
    submit_invoice_for_approval,
)
from .security_audit import StructuredRedactingLogger, read_source_document_file_secure, read_supplier_invoice_secure


class InvoiceLineInput(BaseModel):
    po_line_id: Optional[int] = None
    product_id: Optional[int] = None
    description: str
    qty: str
    unit_price_dzd: str
    discount_pct: str = "0.00"
    tax_rate: str = "19.0"
    line_ht_dzd: Optional[str] = None


class CreateInvoiceRequest(BaseModel):
    supplier_id: int
    supplier_invoice_no: str
    invoice_date: str
    payment_method: str = "BANK_TRANSFER"
    amount_ht_dzd: str
    amount_tva_dzd: str
    amount_timbre_dzd: str = "0.00"
    amount_ttc_dzd: str
    po_id: Optional[int] = None
    grn_id: Optional[int] = None
    document_id: Optional[str] = None
    lines: List[InvoiceLineInput]


class ApprovalDecisionInput(BaseModel):
    approve: bool
    reason: str = ""


class ReversalInput(BaseModel):
    reason: str


class FieldEditInput(BaseModel):
    field_name: str
    new_value: str


def create_erp_fastapi_app(db: ERPDatabase, vault: PrivateDocumentVault) -> FastAPI:
    app = FastAPI(title="Algerian AI ERP API", version="0.1.0")
    logger = StructuredRedactingLogger()
    app.state.db = db
    app.state.vault = vault
    app.state.logger = logger

    def _authenticate(
        x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
        x_company_id: Optional[int] = Header(default=None, alias="X-Company-Id"),
    ) -> Dict[str, int]:
        if x_user_id is None or x_company_id is None:
            raise HTTPException(status_code=401, detail="Missing X-User-Id or X-Company-Id authentication headers.")
        with db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (x_user_id,)).fetchone()
            if not user or not user["is_active"]:
                raise HTTPException(status_code=401, detail="User is inactive, expired, or revoked.")
            if user["company_id"] != x_company_id:
                raise HTTPException(status_code=403, detail="Cross-company header mismatch.")
        return {"user_id": x_user_id, "company_id": x_company_id}

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"status": "ok", "service": "algerian-ai-erp"}

    @app.post("/api/v1/documents/upload")
    async def api_upload_document(
        file: UploadFile = File(...),
        force_image_ocr: bool = Form(default=False),
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        corr_id = str(uuid.uuid4())
        content = await file.read()
        try:
            res = upload_and_process_supplier_document(
                db=db,
                vault=vault,
                company_id=auth["company_id"],
                user_id=auth["user_id"],
                filename=file.filename or "upload.bin",
                mime_type=file.content_type or "application/octet-stream",
                content=content,
                force_image_ocr=force_image_ocr,
            )
            logger.log("INFO", "DOCUMENT_UPLOAD_PROCESSED", corr_id, document_id=res["document_id"], state=res["workflow_state"])
            return res
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/api/v1/documents/{document_id}/file")
    def api_get_document_file(
        document_id: str,
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            raw = read_source_document_file_secure(
                db=db,
                vault=vault,
                requesting_user_id=auth["user_id"],
                document_id=document_id,
            )
            return {"document_id": document_id, "byte_size": len(raw)}
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/v1/documents/{document_id}/fields")
    def api_edit_extracted_field(
        document_id: str,
        body: FieldEditInput,
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return update_extracted_field_by_human(
                db=db,
                company_id=auth["company_id"],
                user_id=auth["user_id"],
                document_id=document_id,
                field_name=body.field_name,
                new_value=body.new_value,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/invoices")
    def api_create_invoice(
        body: CreateInvoiceRequest,
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return create_and_validate_supplier_invoice(
                db=db,
                company_id=auth["company_id"],
                user_id=auth["user_id"],
                supplier_id=body.supplier_id,
                supplier_invoice_no=body.supplier_invoice_no,
                invoice_date=body.invoice_date,
                payment_method=body.payment_method,
                amount_ht_dzd=body.amount_ht_dzd,
                amount_tva_dzd=body.amount_tva_dzd,
                amount_timbre_dzd=body.amount_timbre_dzd,
                amount_ttc_dzd=body.amount_ttc_dzd,
                po_id=body.po_id,
                grn_id=body.grn_id,
                document_id=body.document_id,
                lines=[l.model_dump() for l in body.lines],
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, FinancialIntegrityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/invoices/{invoice_id}")
    def api_get_invoice(
        invoice_id: int,
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return read_supplier_invoice_secure(
                db=db,
                requesting_user_id=auth["user_id"],
                invoice_id=invoice_id,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{invoice_id}/submit-approval")
    def api_submit_approval(
        invoice_id: int,
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return submit_invoice_for_approval(
                db=db,
                company_id=auth["company_id"],
                user_id=auth["user_id"],
                invoice_id=invoice_id,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{invoice_id}/decide-approval")
    def api_decide_approval(
        invoice_id: int,
        body: ApprovalDecisionInput,
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return decide_invoice_approval(
                db=db,
                company_id=auth["company_id"],
                approver_user_id=auth["user_id"],
                invoice_id=invoice_id,
                approve=body.approve,
                reason=body.reason,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{invoice_id}/post")
    def api_post_invoice(
        invoice_id: int,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Missing mandatory Idempotency-Key header.")
        try:
            return post_supplier_invoice(
                db=db,
                company_id=auth["company_id"],
                accountant_user_id=auth["user_id"],
                invoice_id=invoice_id,
                idempotency_key=idempotency_key,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{invoice_id}/reverse")
    def api_reverse_invoice(
        invoice_id: int,
        body: ReversalInput,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        auth: Dict[str, int] = Depends(_authenticate),
    ) -> Dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Missing mandatory Idempotency-Key header.")
        try:
            return reverse_posted_invoice(
                db=db,
                company_id=auth["company_id"],
                accountant_user_id=auth["user_id"],
                original_invoice_id=invoice_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app
