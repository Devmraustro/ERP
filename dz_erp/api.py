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

import json
import logging
import os
import sqlite3
import time
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import (
    AuthError,
    AuthManager,
    AuthenticatedUser,
    DEFAULT_TOKEN_TTL_SECONDS,
    parse_bearer_token,
)
from .database import AuthorizationError, ERPDatabase, FinancialIntegrityError, TenantIsolationError
from .intake_ocr import (
    PrivateDocumentVault,
    decide_extracted_field_review,
    update_extracted_field_by_human,
    upload_and_process_supplier_document,
)
from .review_policy import ReviewPolicyValueError
from .net import TrustedProxyPolicy, describe_trusted_proxy_policy, trusted_proxy_policy_from_env
from .rate_limit import (
    RequestThrottle,
    describe_throttle_policy,
    format_rate_limit_scope,
    store_from_env,
)
from .request_id import sanitize_request_id
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
    evaluate_document_review_policy_secure,
    read_source_document_content_secure,
    read_source_document_file_secure,
    read_supplier_invoice_secure,
    require_role_secure,
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


class FieldDecisionInput(BaseModel):
    """One field-review decision (Phase 46, from PR #7).

    ``decision`` is CONFIRMED | CORRECTED | FLAGGED | NOT_PRESENT. CONFIRMED
    requires ``expected_value`` (the value the reviewer saw); CORRECTED
    requires ``value`` (the transcribed or corrected value); line quantities
    address the line via ``line_index``.
    """

    field_name: str
    decision: str
    value: Optional[str] = None
    expected_value: Optional[str] = None
    note: Optional[str] = None
    line_index: Optional[int] = None
    # When several extraction rows claim the same field, name the one the
    # reviewer treats as authoritative. Omitting it falls back to the row whose
    # value matches ``expected_value``; either way the decision covers every row
    # of the group or the field stays blocked.
    selected_row_id: Optional[int] = None


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


# ``docs/SECURITY_RBAC_AUDIT_AND_BACKUP_SPEC.md`` section 1: "Upload Document /
# Edit OCR Draft" is granted to clerk, approver, accountant and admin, and
# explicitly denied to warehouse, auditor, ocr_worker and treasury. The write
# endpoints enforce that matrix server-side (see ``require_role_secure``); the
# role always comes from the credential's user row.
DOCUMENT_INTAKE_ROLES = ("clerk", "approver", "accountant", "admin")

# The document upload endpoint bounds its own body while streaming (see
# dz_erp/upload.py), but every JSON endpoint - including the unauthenticated
# login endpoint - accepted a body of any size, so one request could force the
# process to buffer an arbitrary amount of memory before any rule ran. This is
# a first-line, application-level cap; it is checked from Content-Length, so a
# chunked request without that header still relies on the proxy limit and on
# the upload reader's streaming bound.
ENV_MAX_REQUEST_BYTES = "ERP_MAX_REQUEST_BYTES"
DEFAULT_MAX_JSON_REQUEST_BYTES = 2 * 1024 * 1024  # 2 MiB of JSON is ~10k invoice lines
MULTIPART_FRAMING_ALLOWANCE = 1024 * 1024  # bounds, per RFC 7578, are per-part
REQUEST_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
UPLOAD_PATH = "/api/v1/documents/upload"


def _configured_max_request_bytes(path: str) -> int:
    """Body cap for ``path``: the upload limit for uploads, else the JSON cap.

    A malformed or non-positive ``ERP_MAX_REQUEST_BYTES`` override is ignored so
    a typo can never silently disable the limit.
    """
    if path == UPLOAD_PATH:
        return configured_max_upload_bytes() + MULTIPART_FRAMING_ALLOWANCE
    raw = os.environ.get(ENV_MAX_REQUEST_BYTES)
    if raw is not None and raw.strip():
        try:
            override = int(raw.strip())
        except ValueError:
            override = 0
        if override > 0:
            return override
    return DEFAULT_MAX_JSON_REQUEST_BYTES


# Ordinary access records live here, apart from the security log.
access_log = logging.getLogger("dz_erp.access")


def create_erp_fastapi_app(
    db: ERPDatabase,
    vault: PrivateDocumentVault,
    *,
    auth_manager: Optional[AuthManager] = None,
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    trusted_proxies: Optional[TrustedProxyPolicy] = None,
    docs_enabled: bool = False,
    throttle: Optional[RequestThrottle] = None,
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
    # Phase 35 (SEC-13): request/login throttling. Defaults to the store
    # selected by ERP_RATE_LIMIT_BACKEND; an explicit throttle wins so tests and
    # a multi-worker deployment can inject one shared store.
    app.state.throttle = (
        throttle if throttle is not None else RequestThrottle(store=store_from_env(db))
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        """Baseline browser-facing hardening headers.

        The API is JSON-first, but two endpoints return operator-supplied bytes
        (document preview) and every response carries tenant-scoped financial
        data. ``nosniff`` stops a browser from re-interpreting a stored
        document or an error body as a different active type, and ``no-store``
        on authenticated responses keeps financial data out of shared caches.
        """
        if request.method in REQUEST_BODY_METHODS:
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    declared_bytes = int(declared)
                except ValueError:
                    declared_bytes = None
                if declared_bytes is not None and declared_bytes > _configured_max_request_bytes(request.url.path):
                    # Refuse before the body is read or the router binds it.
                    return Response(
                        content=f"Request body exceeds the maximum of "
                        f"{_configured_max_request_bytes(request.url.path)} bytes.",
                        status_code=413,
                        media_type="text/plain",
                        headers={"X-Content-Type-Options": "nosniff"},
                    )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.headers.get("authorization"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.middleware("http")
    async def _request_id_and_throttle(request: Request, call_next):
        """Phase 35 (SEC-15 + SEC-13): correlation id and per-client throttling.

        A client may *propose* a correlation id, but the value is written into a
        JSON log line and echoed in a response header, so only a bounded,
        printable ASCII token is adopted; anything else is replaced by a
        server-side uuid4 and never echoed (see ``dz_erp/request_id.py``).

        Throttling happens before routing so a flood costs one dictionary lookup
        rather than a database transaction.
        """
        corr_id, _supplied_id_accepted = sanitize_request_id(
            request.headers.get("X-Request-Id")
        )
        peer_ip = request.client.host if request.client else None
        client_ip = app.state.proxy_policy.resolve_client_ip(
            peer_ip, request.headers.get("X-Forwarded-For")
        )
        allowed, retry_after = app.state.throttle.request_allowed(
            client_ip or peer_ip or "unknown"
        )
        if not allowed:
            logger.log(
                "WARNING",
                "SECURITY_RATE_LIMITED",
                corr_id,
                method=request.method,
                path=request.url.path,
                client_ip=client_ip,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try again later."},
                headers={
                    "X-Request-Id": corr_id,
                    "Retry-After": str(retry_after),
                },
            )
        request.state.request_id = corr_id
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        # One record per request binds the correlation id to the outcome, so a
        # user's report of "request X failed" resolves without a search. No
        # header values, query strings or bodies are logged.
        #
        # This deliberately goes to the standard logger, NOT ``logger``: that
        # one is the append-only *security* log, and mixing ordinary access
        # records into it dilutes the evidence an auditor reads. The
        # correlation id still ties the two together.
        access_log.info(
            "HTTP_REQUEST id=%s method=%s path=%s status=%s duration_ms=%s client=%s",
            corr_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client_ip,
        )
        # Also into the structured trace (and, when configured, the durable
        # sink) so a correlation id can be followed end-to-end irrespective of
        # process restarts. Bodies, headers and credentials are never included.
        logger.log(
            "INFO",
            "HTTP_REQUEST",
            corr_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        # Always the id actually used, never the rejected client proposal.
        response.headers["X-Request-Id"] = corr_id
        return response

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

    @app.get("/readyz")
    def readyz(request: Request) -> Response:
        """Readiness: can this process actually serve work, right now?

        ``/healthz`` above is liveness - it answers "the loop is turning". This
        endpoint opens the database (integrity + write lock), checks the vault is
        writable with its key custody intact, checks free disk and reports the OCR
        subsystem, returning ``503`` when a critical check fails so a load balancer
        stops routing here instead of sending invoices into a dead volume.

        Deliberately unauthenticated (infrastructure scrapes it) and deliberately
        free of paths, key material and counts that describe customer data.
        Advisory failures - a stale backup, for instance - keep the status at
        ``degraded`` and the HTTP code at ``200``: a backup being late must not take
        the application out of service.
        """
        from .readiness import evaluate_readiness, readiness_http_status

        deep = str(request.query_params.get("deep", "")).lower() in ("1", "true", "yes")
        report = evaluate_readiness(
            db,
            vault,
            backup_dir=os.environ.get("ERP_BACKUP_DIR") or None,
            deep_ocr=deep,
        )
        status_code = readiness_http_status(report)
        body = {"status": report["status"], "checks": report["checks"]}
        return Response(
            content=json.dumps(body),
            status_code=status_code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

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
            # Readiness is scraped by infrastructure (load balancer, systemd timer)
            # before any user exists in the request, so it carries no credential. It
            # publishes check names, statuses and counters only - no path, no key
            # material, no customer data - and declaring it protected here would
            # overstate the boundary exactly as the comment above warns against.
            "readyz_readyz_get",
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
        corr_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        # The client IP recorded for a session must be evidence-based, not
        # caller-supplied: X-Forwarded-For is honoured only when the request
        # actually arrived through a configured trusted proxy.
        peer_ip = request.client.host if request.client else None
        client_ip = app.state.proxy_policy.resolve_client_ip(peer_ip, x_forwarded_for)
        # Phase 35 (SEC-13): throttle before verifying the password. A wrong
        # guess costs the server a password hash, so an unthrottled endpoint is
        # both a credential-stuffing and a denial-of-service lever. The key is
        # (client, account) so one locked-out account cannot deny service to
        # everyone else sharing an address.
        throttle = app.state.throttle
        client_key = client_ip or (peer_ip or "unknown")
        blocked, retry_after = throttle.login_blocked(client_key, body.username)
        if blocked:
            # A refusal caused by an unavailable counter is a different fact
            # from one caused by the limit, and an operator must be able to
            # tell them apart. Only the error behind *this* decision counts:
            # a historical "last error" would make every ordinary 429 look like
            # an outage for the rest of the process's life.
            current_error = getattr(throttle, "current_backend_error", None)
            if current_error:
                logger.log(
                    "ERROR",
                    "RATE_LIMIT_BACKEND_UNAVAILABLE",
                    corr_id,
                    failure_mode=throttle.failure_mode,
                    error_type=str(current_error).split(":", 1)[0],
                    username=body.username,
                    client_ip=client_key,
                )
            logger.log(
                "WARNING",
                "SECURITY_AUTH_RATE_LIMITED",
                corr_id,
                username=body.username,
                client_ip=client_key,
                retry_after_seconds=retry_after,
            )
            raise HTTPException(
                status_code=429,
                detail="Too many failed authentication attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        try:
            result = app.state.auth_manager.issue_token(
                body.username,
                body.password,
                issued_to_ip=client_ip,
                user_agent=user_agent,
            )
        except AuthError as exc:
            # Count the failure *after* the decision, so a throttled attempt is
            # recorded but cannot consume another slot.
            _allowed, retry_after = throttle.record_login_failure(client_key, body.username)
            logger.log(
                "WARNING", "AUTH_LOGIN_REJECTED", corr_id,
                code=exc.code, username=body.username,
                client_ip=client_key, retry_after_seconds=retry_after,
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers={"WWW-Authenticate": 'Bearer realm="algerian-ai-erp"'},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # A legitimate user who mistyped a few times is not left locked out.
        throttle.record_login_success(client_key, body.username)
        logger.log("INFO", "AUTH_TOKEN_ISSUED", corr_id, username=result["user"]["username"])
        return result

    @app.get("/api/v1/system/rate-limit-policy")
    def api_rate_limit_policy(
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """The active throttling policy and what it actually bounds.

        Administrators only. The description never contains a key, a username,
        a credential or a per-account counter: it reports the limits, the store
        and the honest scope of the control, so a deployment review can verify
        what the running process is doing rather than what a document says.
        """
        if auth.role != "admin":
            raise HTTPException(status_code=403, detail="Administrator role required")
        described = describe_throttle_policy(app.state.throttle)
        described["deployment_scope"] = format_rate_limit_scope(app.state.throttle)
        return {
            "company_id": auth.company_id,
            "rate_limit": described,
        }

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
        # Authorization before any bytes are parsed: a denied role must not be
        # able to make the server stream, buffer, OCR or vault a document.
        try:
            require_role_secure(
                db,
                auth.user_id,
                DOCUMENT_INTAKE_ROLES,
                action="document upload",
                target_model="source_documents",
                target_id="upload",
            )
        except AuthorizationError as exc:
            logger.log(
                "WARNING",
                "SECURITY_UNAUTHORIZED_ROLE_ACTION_BLOCKED",
                corr_id,
                role=auth.role,
                action="document upload",
            )
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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
        # Same matrix as the upload: correcting an OCR value is a document-
        # intake action, and a role denied the upload must not be able to
        # change the values a reviewer is about to approve.
        try:
            require_role_secure(
                db,
                auth.user_id,
                DOCUMENT_INTAKE_ROLES,
                action="document field correction",
                target_model="ocr_extracted_fields",
                target_id=document_id,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
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

    @app.post("/api/v1/documents/{document_id}/fields/decide")
    def api_decide_extracted_field(
        document_id: str,
        body: FieldDecisionInput,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """Record one human field-review decision (Phase 46, from PR #7).

        The decision is the enforced record: the invoice approval gate refuses
        to run while any required field of the linked document is undecided,
        flagged, or stale. Refusals - wrong role, closed document state, value
        changed since read - are audited server-side and surfaced here with
        their reason.
        """
        try:
            return decide_extracted_field_review(
                db=db,
                company_id=auth.company_id,
                user_id=auth.user_id,
                document_id=document_id,
                field_name=body.field_name,
                decision=body.decision,
                value=body.value,
                expected_value=body.expected_value,
                note=body.note,
                line_index=body.line_index,
                selected_row_id=body.selected_row_id,
            )
        except (AuthorizationError, TenantIsolationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (FinancialIntegrityError, ReviewPolicyValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/documents/{document_id}/review-policy")
    def api_get_document_review_policy(
        document_id: str,
        auth: AuthenticatedUser = Depends(_authenticate),
    ) -> Dict[str, Any]:
        """The fail-closed review-policy verdict for one document (Phase 46).

        Same evaluation the invoice submit/approval gates run: per-field status
        with honest confidence provenance, decisions bound to values,
        extracted-totals arithmetic, and the blocking reasons that currently
        keep the document from backing an approval. Cross-company attempts are
        audited by the secure wrapper.
        """
        try:
            evaluation = evaluate_document_review_policy_secure(
                db, auth.user_id, document_id
            )
        except TenantIsolationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return evaluation

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
        except ValueError as exc:
            # A limit outside 1..500 is a request-contract error. Without this
            # the clamp raised out of the handler as an unhandled 500, unlike
            # every other list endpoint, which answers 422.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        if len(username) > 64 or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in username):
            # A newline or other control character in a username forges log and
            # audit lines, and an unbounded name bloats every event that
            # attributes an action to it.
            raise HTTPException(
                status_code=422,
                detail="Username must be at most 64 characters and contain no control characters.",
            )
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
