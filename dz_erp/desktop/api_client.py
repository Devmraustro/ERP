# SPDX-License-Identifier: LGPL-3.0-only
"""HTTP client the desktop application uses for every business operation.

Design rules:

- **One transport, one authority.** The client speaks only to the verified
  REST API. It never opens the SQLite file, so it inherits server-verified
  authentication, RBAC and tenant isolation instead of reimplementing them.
- **The session token lives in memory only.** It is kept in a local variable
  of this object for the lifetime of the process and never written to disk;
  the persisted configuration whitelist (``desktop.config``) has no field
  that could hold it.
- **Errors are typed.** Every non-2xx becomes :class:`ErpApiError` carrying
  the HTTP status and the server's ``detail`` message, so the UI can show an
  actionable message instead of a traceback.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx


class ErpApiError(RuntimeError):
    """A failed API call, carrying the HTTP status and server detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


def _detail_from(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or "(no detail)"
    if isinstance(payload, dict):
        return str(payload.get("detail", payload))
    return str(payload)


class ErpClient:
    """Typed client for the ERP REST API used by both desktop modes."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/") + "/", timeout=timeout)
        self._token: Optional[str] = None  # memory only - never persisted

    # ------------------------------------------------------------------
    # request plumbing
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        if self._token is None:
            raise ErpApiError(0, "Not logged in.")
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        # Callers may pass extra headers (e.g. the posting Idempotency-Key);
        # they must merge with the authentication header, not collide with it.
        extra = kwargs.pop("headers", None) or {}
        kwargs["headers"] = {**extra, **self._headers()}
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ErpApiError(0, f"Connection to the ERP service failed: {exc}") from exc
        if response.status_code >= 400:
            raise ErpApiError(response.status_code, _detail_from(response))
        if not response.content:
            return {}
        return response.json()

    # ------------------------------------------------------------------
    # authentication
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Exchange credentials for a token; the token stays in memory."""
        try:
            response = self._client.post(
                "/api/v1/auth/token", json={"username": username, "password": password}
            )
        except httpx.HTTPError as exc:
            raise ErpApiError(0, f"Connection to the ERP service failed: {exc}") from exc
        if response.status_code >= 400:
            # 401 with the server's message (invalid credentials / revoked user).
            raise ErpApiError(response.status_code, _detail_from(response))
        payload = response.json()
        self._token = payload["access_token"]
        return payload

    def session(self) -> Dict[str, Any]:
        """Who am I (verified server-side) - used for the status bar."""
        return self._request("GET", "/api/v1/auth/session")

    def logout(self) -> None:
        """Revoke the server-side token, then drop it from memory."""
        try:
            self._request("POST", "/api/v1/auth/revoke", json={"reason": "CLIENT_SIGNOUT"})
        finally:
            self._token = None

    @property
    def is_logged_in(self) -> bool:
        return self._token is not None

    # ------------------------------------------------------------------
    # read models (Phase 25 endpoints)
    # ------------------------------------------------------------------

    def accounts(self, *, limit: int = 500) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/accounts", params={"limit": limit})

    def products(self, *, limit: int = 500) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/products", params={"limit": limit})

    def documents(self, *, limit: int = 100) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/documents", params={"limit": limit})

    def invoices(self, *, limit: int = 100) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/invoices", params={"limit": limit})

    def trial_balance(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/accounting/trial-balance")

    def invoice(self, invoice_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/api/v1/invoices/{invoice_id}")

    # ------------------------------------------------------------------
    # review and navigation reads (Phase 27 endpoints)
    # ------------------------------------------------------------------

    def partners(self, *, limit: int = 500) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/partners", params={"limit": limit})

    def stock(self, *, limit: int = 500) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/stock", params={"limit": limit})

    def document_fields(self, document_id: str, *, limit: int = 500) -> Dict[str, Any]:
        return self._request(
            "GET", f"/api/v1/documents/{document_id}/fields", params={"limit": limit}
        )

    def document_content(self, document_id: str) -> Dict[str, Any]:
        """The decrypted source bytes plus preview metadata - never JSON-parsed.

        The UI uses this for the review preview pane; the exact bytes come
        from the audited server-side document read.
        """
        try:
            response = self._client.get(
                f"/api/v1/documents/{document_id}/content", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise ErpApiError(0, f"Connection to the ERP service failed: {exc}") from exc
        if response.status_code >= 400:
            raise ErpApiError(response.status_code, _detail_from(response))
        return {
            "content": response.content,
            "media_type": response.headers.get("content-type", "application/octet-stream"),
            "filename": response.headers.get("content-disposition", ""),
        }

    def edit_extracted_field(self, document_id: str, field_name: str, new_value: str) -> Dict[str, Any]:
        """Persist a human correction; the server preserves raw OCR + confidence."""
        return self._request(
            "PATCH",
            f"/api/v1/documents/{document_id}/fields",
            json={"field_name": field_name, "new_value": new_value},
        )

    # ------------------------------------------------------------------
    # user administration (Phase 28 endpoints; administrators only)
    # ------------------------------------------------------------------

    def users(self, *, limit: int = 500) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/users", params={"limit": limit})

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        branch_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a user; the server validates role, password and uniqueness."""
        payload: Dict[str, Any] = {"username": username, "password": password, "role": role}
        if branch_id is not None:
            payload["branch_id"] = branch_id
        return self._request("POST", "/api/v1/users", json=payload)

    def deactivate_user(self, user_id: int) -> Dict[str, Any]:
        return self._request("POST", f"/api/v1/users/{user_id}/deactivate")

    # ------------------------------------------------------------------
    # document intake
    # ------------------------------------------------------------------

    def upload_document(
        self,
        file_path: Path,
        *,
        force_image_ocr: bool = False,
        declared_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {"force_image_ocr": str(bool(force_image_ocr)).lower()}
        if declared_language:
            data["declared_language"] = declared_language
        with Path(file_path).open("rb") as handle:
            files = {"file": (Path(file_path).name, handle)}
            return self._request("POST", "/api/v1/documents/upload", data=data, files=files)

    # ------------------------------------------------------------------
    # supplier invoice lifecycle
    # ------------------------------------------------------------------

    def create_invoice(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/v1/invoices", json=payload)

    def submit_approval(self, invoice_id: int) -> Dict[str, Any]:
        return self._request("POST", f"/api/v1/invoices/{invoice_id}/submit-approval")

    def decide_approval(self, invoice_id: int, *, approve: bool, reason: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/invoices/{invoice_id}/decide-approval",
            json={"approve": approve, "reason": reason},
        )

    def post_invoice(self, invoice_id: int, *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        key = idempotency_key or str(uuid.uuid4())
        return self._request(
            "POST",
            f"/api/v1/invoices/{invoice_id}/post",
            headers={"Idempotency-Key": key},
        )

    def reverse_invoice(
        self, invoice_id: int, *, reason: str, idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        key = idempotency_key or str(uuid.uuid4())
        return self._request(
            "POST",
            f"/api/v1/invoices/{invoice_id}/reverse",
            json={"reason": reason},
            headers={"Idempotency-Key": key},
        )

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ErpClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
