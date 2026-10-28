# SPDX-License-Identifier: LGPL-3.0-only
"""FastAPI REST API for Algerian AI ERP Platform with server-verified authentication.

Authentication
--------------
Every protected endpoint authenticates a **server-issued opaque bearer token**
presented in the standard ``Authorization: Bearer <token>`` header
(see :mod:`dz_erp.auth`).

The previous implementation accepted ``X-User-Id`` / ``X-Company-Id`` request
headers as proof of identity. Because those values were supplied by the client
and never checked against any credential, any caller could impersonate any user
of any company by editing two headers. Those headers are **no longer read at
all**: identity, company, and role are derived exclusively from the server-side
record bound to the presented credential.
"""

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

from .auth import (
    AuthError,
    AuthManager,
    AuthenticatedUser,
    DEFAULT_TOKEN_TTL_SECONDS,
    parse_bearer_token,
)
from .database import AuthorizationError, ERPDatabase, FinancialIntegrityError, TenantIsolationError
from .intake_ocr import PrivateDocumentVault, update_extracted_field_by_human, upload_and_process_supplier_document
from .net import TrustedProxyPolicy, describe_trusted_proxy_policy, trusted_proxy_policy_from_env
from .procurement_stock import (
    create_and_validate_supplier_invoice,
    create_reversal_request,
    decide_invoice_approval,
    decide_reversal_approval,
    post_reversal,
    post_supplier_invoice,
    submit_invoice_for_approval,
    submit_reversal_for_approval,
)
from .security_audit import (
    JsonlSecurityLog,
    StructuredRedactingLogger,
    create_user_admin_secure,
    deactivate_user_secure,
    list_extracted_fields_secure,
    list_partners_secure,
    list_stock_quants_secure,
    list_supplier_invoices_secure,
    list_users_secure,
    read_source_document_content_secure,
    read_source_document_file_secure,
    read_supplier_invoice_secure,
    trial_balance_secure,
)
from .upload import (
    UnsupportedMediaTypeError,
    UploadTooLargeError,
    UploadValidationError,
    close_upload,
    configured_max_upload_bytes,
    describe_upload_policy,
    read_bounded_upload,
    validate_content_signature,
    validate_upload_metadata,
)


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


class ReversalApprovalDecisionInput(BaseModel):
    approve: bool
    reason: str = ""


class FieldEditInput(BaseModel):
    field_name: str
    new_value: str


class UserCreateInput(BaseModel):
    username: str
    password: str
    role: str
    branch_id: Optional[int] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class RevokeRequest(BaseModel):
    reason: str = "USER_LOGOUT"


def create_erp_fastapi_app(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    *,
    auth_manager: Optional[AuthManager] = None,
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    trusted_proxies: Optional[TrustedProxyPolicy] = None,
    docs_enabled: bool = False,
) -> FastAPI:
    # Secure default: docs disabled unless explicitly enabled for local development
    docs_url = "/docs" if docs_enabled else None
    redoc_url = "/redoc" if docs_enabled else None
    openapi_url = "/openapi.json" if docs_enabled else None

    app = FastAPI(
        title="Algerian AI ERP API",
        version="0.2.0",
        description=(
            "Authenticated with a server-issued opaque bearer token sent as "
            "`Authorization: Bearer <token>`. Obtain one from `POST /api/v1/auth/token`. "
            "Identity headers (`X-User-Id`, `X-Company-Id`) are not trusted and are ignored."
        ),
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    # Durable sink for security-relevant events that cannot be attributed to a
    # tenant (see JsonlSecurityLog).  Configured by ERP_SECURITY_LOG_PATH; when
    # unset the logger is in-memory only, which is the test configuration and
    # must never be the production one.
    security_log_path = os.environ.get("ERP_SECURITY_LOG_PATH")
    security_sink = JsonlSecurityLog(Path(security_log_path)) if security_log_path else None
    logger = StructuredRedactingLogger(sink=security_sink)
    app.state.db = db
    app.state.vault = vault
    app.state.logger = logger
    app.state.auth_manager = auth_manager or AuthManager(db, token_ttl_seconds=token_ttl_seconds)
    # Forwarded-header trust is fail-closed: unless trusted proxies are
    # explicitly configured, X-Forwarded-For is ignored and the direct peer
    # address is used.  See dz_erp/net.py.
    app.state.proxy_policy = trusted_proxies or trusted_proxy_policy_from_env()

    def _authenticate(
        request: Request,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> AuthenticatedUser:
        """Resolve the caller's identity from a verified server-side credential.

        Company and role are read from the ``users`` row that owns the token.
        No client-supplied identity header participates in this decision.
        """
        try:
            return app.state.auth_manager.authenticate(authorization)
        except AuthError as exc:
            # Peer address, never a client-supplied identity: an unauthenticated
            # request has no trustworthy tenant, so the only attributable facts
            # are where it came from and why it was refused.
            peer_ip = request.client.host if request.client else None
            logger.log(
                "WARNING",
                "AUTH_REQUEST_REJECTED",
                str(uuid.uuid4()),
                peer_ip=peer_ip,
                path=request.url.path,
                method=request.method,
                code=exc.code,
                detail=exc.detail,
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers={"WWW-Authenticate": 'Bearer realm="algerian-ai-erp"'},
            ) from exc

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"status": "ok", "service": "algerian-ai-erp"}

    @app.get("/api/v1/upload-policy")
    def api_upload_policy() -> Dict[str, Any]:
        """Public, non-sensitive description of the enforced upload boundary.

        Lets clients size their requests correctly and lets operators confirm
        the active limit without reading the deployment configuration.
        """
        return {
            "upload": describe_upload_policy(configured_max_upload_bytes()),
            "forwarded_headers": describe_trusted_proxy_policy(app.state.proxy_policy),
        }

    # Publish the bearer security scheme in the OpenAPI contract so the
    # authentication boundary is machine-discoverable. Health stays public.
    def _custom_openapi() -> Dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            openapi_version="3.1.0",
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "opaque session token",
                "description": (
                    "Opaque bearer session token issued by POST /api/v1/auth/token; "
                    "send it as 'Authorization: Bearer <token>'. Self-asserted identity "
                    "headers (X-User-Id, X-Company-Id) are not accepted."
                ),
            }
        }
        # Declare the authentication boundary explicitly rather than letting it
        # follow from the route decorators.  The public set is the set the
        # implementation actually serves without a credential: the health probe,
        # the login endpoint that *issues* credentials, and the upload-policy
        # description that is deliberately non-sensitive (it publishes the
        # enforced limit so a client can size its request correctly).
        #
        # Declaring a route public when it is in fact protected is the dangerous
        # direction; the reverse - as this was for /api/v1/upload-policy and
        # /api/v1/auth/token - makes the contract overstate the boundary, which
        # misleads generated clients and anyone auditing from the schema alone.
        public_operations = {
            "healthz_healthz_get",
            "api_issue_token_api_v1_auth_token_post",
            "api_upload_policy_api_v1_upload_policy_get",
        }
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict) and operation.get("operationId") not in public_operations:
                    operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]

    # ------------------------------------------------------------------
    # Authentication boundary
    # ------------------------------------------------------------------

    @app.post("/api/v1/auth/token")
    async def api_issue_token(
        body: LoginRequest,
        request: Request,
        x_forwarded_for: Optional[str] = Header(default=None, alias="X-Forwarded-For"),
        user_agent: Optional[str] = Header(default=None, alias="User-Agent"),
    ) -> Dict[str, Any]:
        """Exchange a username/password for an opaque bearer session token."""
        corr_id = str(uuid.uuid4())
        # The client IP recorded for a session must be evidence-based, not
        # caller-supplied: X-Forwarded-For is honoured only when the request
        # actually arrived through a configured trusted proxy.
        peer_ip = request.client.host if request.client else None
        client_ip = app.state.proxy_policy.resolve_client_ip(peer_ip, x_forwarded_for)
        try:
            result = app.state.auth_manager.issue_token(
                body.username,
                body.password,
                issued_to_ip=client_ip,
                user_agent=user_agent,
            )
        except AuthError as exc:
            logger.log("WARNING", "AUTH_LOGIN_REJECTED", corr_id, code=exc.code, username=body.username)
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers={"WWW-Authenticate": 'Bearer realm="algerian-ai-erp"'},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.log("INFO", "AUTH_TOKEN_ISSUED", corr_id, username=result["user"]["username"])
        return result

    @app.get("/api/v1/auth/session")
    def api_current_session(auth: AuthenticatedUser = Depends(_authenticate)) -> Dict[str, Any]:
        """Return the server-resolved principal for the presented credential."""
        return {
            "user_id": auth.user_id,
            "company_id": auth.company_id,
            "branch_id": auth.branch_id,
            "username": auth.username,
            "role": auth.role,
            "token_id": auth.token_id,
            "token_prefix": auth.token_prefix,
            "expires_at": auth.expires_at,
        }

    @app.post("/api/v1/auth/revoke")
    def api_revoke_token(
        body: RevokeRequest,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, Any]:
        """Revoke the presented token (sign-out). Revocation is immediate."""
        try:
            token = parse_bearer_token(authorization)
        except AuthError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers={"WWW-Authenticate": 'Bearer realm="algerian-ai-erp"'},
            ) from exc
        revoked = app.state.auth_manager.revoke_token(token, reason=body.reason)
        if not revoked:
            raise HTTPException(status_code=401, detail="Authentication credential is invalid or already revoked.")
        logger.log("INFO", "AUTH_TOKEN_REVOKED", str(uuid.uuid4()), reason=body.reason)
        return {"revoked": True, "reason": body.reason}

    # ------------------------------------------------------------------
    # Protected resources
    # ------------------------------------------------------------------

    @app.post("/api/v1/documents/upload")
    async def api_upload_document(
        file: UploadFile = File(...),
        force_image_ocr: bool = Form(default=False),
        # The supplier's known document language ("ar" for an Arabic-only issuer).
        # It only selects the review route: when an Arabic document must be
        # recognised from an image, the response says manual transcription is
        # required because no Arabic recognition head is provisioned.
        declared_language: Optional[str] = Form(default=None),
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        corr_id = str(uuid.uuid4())
        content: Optional[bytes] = None
        try:
            # 1. Type allowlist: filename extension and declared MIME must both be
            #    allowed and must agree.  Neither is trusted on its own.
            declared_mime = validate_upload_metadata(file.filename, file.content_type)

            # 2. Bounded, incremental read.  The limit is enforced *while*
            #    reading, so an oversized body is never fully materialised and
            #    no OCR, vault write, or business record can be created for it.
            #    The declared Content-Length is an optimisation only - the
            #    chunked loop is authoritative because it is client-controlled.
            try:
                content = await read_bounded_upload(file, declared_size=file.size)
            except UploadTooLargeError as exc:
                logger.log(
                    "WARNING",
                    "DOCUMENT_UPLOAD_REJECTED_TOO_LARGE",
                    corr_id,
                    limit_bytes=exc.limit,
                    declared_size=exc.declared_size,
                    user_id=auth.user_id,
                    company_id=auth.company_id,
                )
                raise HTTPException(status_code=413, detail=str(exc)) from exc

            # 3. Content-based signature check, independent of the declared label.
            validate_content_signature(content, declared_mime)

            res = upload_and_process_supplier_document(
                db=db,
                vault=vault,
                company_id=auth.company_id,
                user_id=auth.user_id,
                filename=file.filename or "upload.bin",
                mime_type=declared_mime,
                content=content,
                force_image_ocr=force_image_ocr,
                declared_language=declared_language,
            )
            logger.log(
                "INFO",
                "DOCUMENT_UPLOAD_PROCESSED",
                corr_id,
                document_id=res["document_id"],
                state=res["workflow_state"],
                extraction_route=res.get("extraction_route"),
            )
            return res
        except (UnsupportedMediaTypeError, UploadValidationError) as exc:
            # 415 for a disallowed type, 400 for internally inconsistent metadata.
            status = 415 if isinstance(exc, UnsupportedMediaTypeError) else 400
            logger.log("WARNING", "DOCUMENT_UPLOAD_REJECTED_TYPE", corr_id, reason=str(exc))
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        finally:
            # Always release the upload.  Starlette backs uploads with a
            # SpooledTemporaryFile that spills to disk past 1 MiB, so closing it
            # is what removes any partial temporary artifact.
            await close_upload(file)
            content = None

    @app.get("/api/v1/documents/{document_id}/file")
    def api_get_document_file(
        document_id: str,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            raw = read_source_document_file_secure(
                db=db,
                vault=vault,
                requesting_user_id=auth.user_id,
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
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return update_extracted_field_by_human(
                db=db,
                company_id=auth.company_id,
                user_id=auth.user_id,
                document_id=document_id,
                field_name=body.field_name,
                new_value=body.new_value,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/invoices")
    def api_create_invoice(
        body: CreateInvoiceRequest,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return create_and_validate_supplier_invoice(
                db=db,
                company_id=auth.company_id,
                user_id=auth.user_id,
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
                lines=[line.model_dump() for line in body.lines],
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, FinancialIntegrityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/invoices/{invoice_id}")
    def api_get_invoice(
        invoice_id: int,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return read_supplier_invoice_secure(
                db=db,
                requesting_user_id=auth.user_id,
                invoice_id=invoice_id,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{invoice_id}/submit-approval")
    def api_submit_approval(
        invoice_id: int,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return submit_invoice_for_approval(
                db=db,
                company_id=auth.company_id,
                user_id=auth.user_id,
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
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return decide_invoice_approval(
                db=db,
                company_id=auth.company_id,
                approver_user_id=auth.user_id,
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
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Missing mandatory Idempotency-Key header.")
        try:
            return post_supplier_invoice(
                db=db,
                company_id=auth.company_id,
                accountant_user_id=auth.user_id,
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
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Missing mandatory Idempotency-Key header.")
        try:
            return create_reversal_request(
                db=db,
                company_id=auth.company_id,
                accountant_user_id=auth.user_id,
                original_invoice_id=invoice_id,
                reason=body.reason,
                idempotency_key=idempotency_key,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{reversal_invoice_id}/submit-approval")
    def api_submit_reversal_approval(
        reversal_invoice_id: int,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return submit_reversal_for_approval(
                db=db,
                company_id=auth.company_id,
                user_id=auth.user_id,
                reversal_invoice_id=reversal_invoice_id,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{reversal_invoice_id}/decide-approval")
    def api_decide_reversal_approval(
        reversal_invoice_id: int,
        body: ReversalApprovalDecisionInput,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        try:
            return decide_reversal_approval(
                db=db,
                company_id=auth.company_id,
                approver_user_id=auth.user_id,
                reversal_invoice_id=reversal_invoice_id,
                approve=body.approve,
                reason=body.reason,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/invoices/{reversal_invoice_id}/post")
    def api_post_reversal(
        reversal_invoice_id: int,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Missing mandatory Idempotency-Key header.")
        try:
            return post_reversal(
                db=db,
                company_id=auth.company_id,
                accountant_user_id=auth.user_id,
                reversal_invoice_id=reversal_invoice_id,
                idempotency_key=idempotency_key,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Read-only listing and reporting endpoints (Phase 25)
    #
    # These exist so an installed client application can present real data
    # without speaking ad-hoc SQL. Scope comes from the verified credential,
    # every listing is bounded by a clamped limit, and the two financial
    # surfaces reuse the exact RBAC of the per-invoice read (warehouse role
    # denied, denial audited) so a list view is never a bypass.
    # ------------------------------------------------------------------

    @app.get("/api/v1/accounts")
    def api_list_accounts(
        limit: int = 500,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """SCF chart rows for the caller's company, ordered by account code."""
        try:
            rows = db.list_accounts(auth.company_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "accounts": rows, "count": len(rows)}

    @app.get("/api/v1/products")
    def api_list_products(
        limit: int = 500,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Product catalogue rows for the caller's company, ordered by SKU."""
        try:
            rows = db.list_products(auth.company_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "products": rows, "count": len(rows)}

    @app.get("/api/v1/documents")
    def api_list_documents(
        limit: int = 100,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Uploaded source documents for the caller's company, newest first.

        Metadata only - vault paths and digests stay internal; contents are
        read exclusively through the audited per-document secure path.
        """
        try:
            rows = db.list_source_documents(auth.company_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "documents": rows, "count": len(rows)}

    @app.get("/api/v1/invoices")
    def api_list_invoices(
        limit: int = 100,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Supplier invoices for the caller's company, newest first.

        Financial data: same role rule as the per-invoice read - the warehouse
        role is refused and the refusal is audit-logged.
        """
        try:
            rows = list_supplier_invoices_secure(db, auth.user_id, limit=limit)
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "invoices": rows, "count": len(rows)}

    @app.get("/api/v1/accounting/trial-balance")
    def api_trial_balance(
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Per-account POSTED debit/credit totals; refuses an unbalanced ledger."""
        try:
            return trial_balance_secure(db, auth.user_id)
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FinancialIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Read-only listing endpoints for the review and navigation screens
    # (Phase 27). Same contract as the Phase 25 listings: scope derived
    # from the verified credential, limits clamped server-side, denials
    # audited by the secure wrappers.
    # ------------------------------------------------------------------

    @app.get("/api/v1/partners")
    def api_list_partners(
        limit: int = 500,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Partner master data (suppliers/customers) for the caller's company."""
        try:
            rows = list_partners_secure(db, auth.user_id, limit=limit)
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "partners": rows, "count": len(rows)}

    @app.get("/api/v1/stock")
    def api_list_stock(
        limit: int = 500,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Per-warehouse on-hand stock for the caller's company."""
        try:
            rows = list_stock_quants_secure(db, auth.user_id, limit=limit)
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "stock": rows, "count": len(rows)}

    @app.get("/api/v1/documents/{document_id}/fields")
    def api_list_document_fields(
        document_id: str,
        limit: int = 500,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """OCR-extracted fields of one document, in review order.

        Raw OCR text, current values, honest confidence metadata and human
        overrides - everything a reviewer needs; geometry (bbox) stays
        internal. Cross-company attempts are audited by the secure wrapper.
        """
        # Clamp first through the same single-source helper every read model
        # uses: a bad limit is a 422 contract error, never a mistaken 404 for
        # the document (the secure wrapper re-clamps as defense in depth; its
        # ValueError means "not found").
        try:
            db._read_limit(limit, "list_extracted_fields")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            rows = list_extracted_fields_secure(
                db, auth.user_id, document_id, limit=limit
            )
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "document_id": document_id, "fields": rows, "count": len(rows)}

    @app.get("/api/v1/documents/{document_id}/content")
    def api_get_document_content(
        document_id: str,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Response:
        """The decrypted source document bytes, for the review preview pane.

        Reuses the audited per-document secure path unchanged - the access is
        audit-logged exactly like every other document read. The response
        carries the stored MIME type and original filename so the client can
        preview (image) or hand off (PDF) without guessing.
        """
        try:
            raw, doc = read_source_document_content_secure(
                db=db,
                vault=vault,
                requesting_user_id=auth.user_id,
                document_id=document_id,
            )
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        filename = doc["filename"]
        quoted = filename.replace('"', "'")
        return Response(
            content=raw,
            media_type=doc["mime_type"],
            headers={"Content-Disposition": f'inline; filename="{quoted}"'},
        )

    # ------------------------------------------------------------------
    # User administration (Phase 28). Administrators only: the guard in
    # security_audit derives the role from the verified credential and
    # audits every non-admin attempt. Unknown/cross-tenant targets are
    # conflated on purpose ("not found in target company") so responses
    # never leak whether a foreign user id exists.
    # ------------------------------------------------------------------

    @app.get("/api/v1/users")
    def api_list_users(
        limit: int = 500,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """User accounts of the caller's company; administrators only.

        The listing never includes credential material (password or token
        hashes are excluded by the read model).
        """
        try:
            rows = list_users_secure(db, auth.user_id, limit=limit)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"company_id": auth.company_id, "users": rows, "count": len(rows)}

    @app.post("/api/v1/users")
    def api_create_user(
        body: UserCreateInput,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Create a user in the caller's company; administrators only.

        The verified credential mechanics (role validation, PBKDF2 hashing,
        ``AUTH_USER_CREATED`` audit event attributed to the acting admin)
        are delegated to ``AuthManager.create_user``. The password must have
        at least 8 characters (NIST SP 800-63B floor for a new credential at
        an API boundary); uniqueness of the username is enforced by the
        schema and surfaced as 409.
        """
        username = body.username.strip()
        if not username:
            raise HTTPException(status_code=422, detail="Username is required.")
        if len(body.password) < 8:
            raise HTTPException(
                status_code=422,
                detail="Password must be at least 8 characters long.",
            )
        try:
            user = create_user_admin_secure(
                db,
                auth.user_id,
                app.state.auth_manager,
                username=username,
                password=body.password,
                role=body.role,
                branch_id=body.branch_id,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Username '{username}' already exists.",
            ) from exc
        return {"company_id": auth.company_id, "user": user}

    @app.post("/api/v1/users/{user_id}/deactivate")
    def api_deactivate_user(
        user_id: int,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Deactivate a user; administrators only, never their own account.

        The deactivated account's tokens are rejected by ``authenticate``
        from the moment ``is_active`` flips, so live sessions end at the
        next request. Self-deactivation and deactivating an already
        inactive user are conflicts (409).
        """
        try:
            result = deactivate_user_secure(db, auth.user_id, user_id)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"company_id": auth.company_id, **result}

    return app
