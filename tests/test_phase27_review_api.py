# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 27 — review and navigation read API (fields, content, partners, stock).

What this phase must prove for the desktop review screens:

1. The four new reads are authenticated and tenant-scoped like the Phase 25
   listings: scope from the verified credential, limits clamped, unscoped
   data-layer calls refused.
2. OCR field rows carry everything a reviewer judges a field with — raw
   text (immutable), current value, honest confidence metadata, human
   override — and never the internal geometry (``bbox_json``).
3. A cross-company attempt on a document is refused 403 and audited
   (``SECURITY_CROSS_COMPANY_READ_BLOCKED``), exactly like the file read.
4. Document content returns the exact stored bytes with the stored MIME
   type and original filename - warehouse staff included, since documents
   are the warehouse domain.
5. The human field-correction PATCH preserves raw text and confidence and
   audit-logs the edit - the correction workflow the UI calls.
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase, TenantIsolationError
from dz_erp.intake_ocr import PrivateDocumentVault

_TENANT_USERS = [
    (1, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (5, 1, "wh_a", "warehouse", 1, "Wh#2026x"),
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
]

_DOC_BYTES = b"%PDF-1.4\n% synthetic phase-27 fixture - not a real invoice\n1 0 obj\nendobj\n"
_SHA = hashlib.sha256(_DOC_BYTES).hexdigest()


def _setup_two_tenants(db: ERPDatabase, vault: PrivateDocumentVault) -> None:
    vault_path = vault.store_bytes(1, _SHA, _DOC_BYTES)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, cid, username, role, active, password in _TENANT_USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, cid, username, role, active, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, tax_regime, nif, wilaya_code) "
            "VALUES (10, 1, 'Supplier Alpha', 'supplier', 'REEL', '001216099123456', '16')"
        )
        conn.execute(
            "INSERT INTO products (id, company_id, sku, name, uom, is_storable, standard_cost_dzd) "
            "VALUES (5, 1, 'SKU-A1', 'Product Alpha', 'UNIT', 1, '1500.000000')"
        )
        conn.execute(
            "INSERT INTO stock_quants (company_id, warehouse_code, product_id, qty_on_hand) "
            "VALUES (1, 'ALG-WH', 5, '12.000000')"
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, "
            "page_count, file_sha256, vault_path, security_status, workflow_state, "
            "uploaded_by_id, uploaded_at) "
            "VALUES ('doc-1', 1, 'facture-alpha.pdf', 'application/pdf', ?, 1, ?, ?, "
            "'CLEAN', 'NeedsReview', 1, '2026-10-20T10:00:00+00:00')",
            (len(_DOC_BYTES), _SHA, vault_path),
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, "
            "page_count, file_sha256, vault_path, security_status, workflow_state, "
            "uploaded_by_id, uploaded_at) "
            "VALUES ('doc-b', 2, 'beta.pdf', 'application/pdf', 64, 1, ?, "
            "'vault/company_2/beta.enc', 'CLEAN', 'Uploaded', 20, '2026-10-20T10:00:00+00:00')",
            ("b" * 64,),
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index, "
            "field_name, raw_text, normalized_value, confidence, confidence_kind, "
            "confidence_calibrated, bbox_json, ocr_model_version, created_at) "
            "VALUES (1, 'doc-1', 1, 0, 'supplier_invoice_no', 'FAC-2026-0117', 'FAC-2026-0117', "
            "0.42, 'engine_line', 0, '{}', 'synthetic-ocr-1', '2026-10-20T10:00:01+00:00')"
        )
        conn.execute(
            "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index, "
            "field_name, raw_text, normalized_value, confidence, confidence_kind, "
            "confidence_calibrated, bbox_json, ocr_model_version, created_at) "
            "VALUES (1, 'doc-1', 1, 1, 'amount_ttc_dzd', '1S 7S0 DD', NULL, "
            "NULL, 'not_measured', 0, '{}', 'synthetic-ocr-1', '2026-10-20T10:00:01+00:00')"
        )
        # A decoy field of the other tenant: must never leak through doc-1 reads.
        conn.execute(
            "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index, "
            "field_name, raw_text, normalized_value, confidence, confidence_kind, "
            "confidence_calibrated, bbox_json, ocr_model_version, created_at) "
            "VALUES (2, 'doc-b', 1, 0, 'secret_field', 'beta-secret', 'beta-secret', "
            "0.9, 'engine_line', 0, '{}', 'synthetic-ocr-1', '2026-10-20T10:00:01+00:00')"
        )


def _make_client(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "review.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    _setup_two_tenants(db, vault)
    return db, TestClient(create_erp_fastapi_app(db, vault))


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _audit_rows(db_path: Path, action_type: str) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM audit_events WHERE action_type = ?", (action_type,)
            ).fetchall()
        ]
    finally:
        conn.close()


# ----------------------------------------------------------------------
# authentication and tenant scoping
# ----------------------------------------------------------------------


def test_review_reads_require_authentication(tmp_path: Path) -> None:
    _, client = _make_client(tmp_path)
    for url in (
        "/api/v1/partners",
        "/api/v1/stock",
        "/api/v1/documents/doc-1/fields",
        "/api/v1/documents/doc-1/content",
    ):
        resp = client.get(url)
        assert resp.status_code == 401, (url, resp.status_code, resp.text)


def test_partners_and_stock_are_tenant_scoped_and_clamped(tmp_path: Path) -> None:
    db, client = _make_client(tmp_path)
    ha = {"Authorization": f"Bearer {_login(client, 'clerk_a', 'Clerk#2026x')}"}
    hb = {"Authorization": f"Bearer {_login(client, 'admin_b', 'AdminB#2026x')}"}

    partners_a = client.get("/api/v1/partners", headers=ha).json()
    assert [p["name"] for p in partners_a["partners"]] == ["Supplier Alpha"]
    assert partners_a["partners"][0]["nif"] == "001216099123456"
    partners_b = client.get("/api/v1/partners", headers=hb).json()
    assert partners_b["partners"] == []

    stock_a = client.get("/api/v1/stock", headers=ha).json()
    assert stock_a["count"] == 1
    row = stock_a["stock"][0]
    assert (row["warehouse_code"], row["sku"], row["qty_on_hand"]) == ("ALG-WH", "SKU-A1", "12.000000")
    stock_b = client.get("/api/v1/stock", headers=hb).json()
    assert stock_b["stock"] == []

    # Server-side clamp: the limit is never trusted from the client.
    assert client.get("/api/v1/partners", headers={**ha, "x": "1"}, params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/stock", headers=ha, params={"limit": 10_000}).status_code == 422
    # The document fields read clamps too - and a bad limit is a 422
    # contract error, never a mistaken 404 for the document.
    assert client.get("/api/v1/documents/doc-1/fields", headers=ha, params={"limit": 0}).status_code == 422

    # Unscoped reads are a tenant-isolation bug at the data layer too.
    for call in (
        lambda: db.list_partners(None),
        lambda: db.list_stock_quants(None),
        lambda: db.list_extracted_fields(None, "doc-1"),
    ):
        with pytest.raises(TenantIsolationError):
            call()


def test_document_fields_are_tenant_scoped_and_geometry_free(tmp_path: Path) -> None:
    db, client = _make_client(tmp_path)
    db_path = Path(db.db_path)
    ha = {"Authorization": f"Bearer {_login(client, 'clerk_a', 'Clerk#2026x')}"}
    hb = {"Authorization": f"Bearer {_login(client, 'admin_b', 'AdminB#2026x')}"}

    fields = client.get("/api/v1/documents/doc-1/fields", headers=ha)
    assert fields.status_code == 200
    payload = fields.json()
    assert payload["count"] == 2
    names = [f["field_name"] for f in payload["fields"]]
    assert names == ["supplier_invoice_no", "amount_ttc_dzd"]  # review order
    low = payload["fields"][1]
    assert low["raw_text"] == "1S 7S0 DD"
    assert low["normalized_value"] is None
    assert low["confidence"] is None
    assert low["confidence_kind"] == "not_measured"
    good = payload["fields"][0]
    assert good["confidence"] == pytest.approx(0.42)
    assert good["confidence_kind"] == "engine_line"
    # Geometry stays internal.
    assert all("bbox_json" not in f for f in payload["fields"])

    # The other tenant's decoy field must not leak.
    assert all(f["raw_text"] != "beta-secret" for f in payload["fields"])

    # Cross-company read: 403, audited, committed before the raise.
    denied = client.get("/api/v1/documents/doc-1/fields", headers=hb)
    assert denied.status_code == 403
    events = _audit_rows(db_path, "SECURITY_CROSS_COMPANY_READ_BLOCKED")
    assert any(e["target_model"] == "source_documents" and e["target_id"] == "doc-1" for e in events)

    # Unknown document: 404, and it is not mistaken for a cross-company hit.
    missing = client.get("/api/v1/documents/no-such-doc/fields", headers=ha)
    assert missing.status_code == 404


def test_document_content_returns_exact_bytes_and_metadata(tmp_path: Path) -> None:
    db, client = _make_client(tmp_path)
    db_path = Path(db.db_path)
    ha = {"Authorization": f"Bearer {_login(client, 'wh_a', 'Wh#2026x')}"}
    hb = {"Authorization": f"Bearer {_login(client, 'admin_b', 'AdminB#2026x')}"}

    resp = client.get("/api/v1/documents/doc-1/content", headers=ha)
    assert resp.status_code == 200
    assert resp.content == _DOC_BYTES  # exact bytes, no transformation
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "facture-alpha.pdf" in resp.headers["content-disposition"]
    # Warehouse staff may read source documents: it is their domain...
    fields = client.get("/api/v1/documents/doc-1/fields", headers=ha)
    assert fields.status_code == 200
    partners = client.get("/api/v1/partners", headers=ha)
    assert partners.status_code == 200
    stock = client.get("/api/v1/stock", headers=ha)
    assert stock.status_code == 200
    # ...but financial invoice reads stay denied (navigation must not bypass).
    invoices = client.get("/api/v1/invoices", headers=ha)
    assert invoices.status_code == 403

    # Every successful content read is audit-logged like any file read.
    events = _audit_rows(db_path, "DOCUMENT_FILE_ACCESSED")
    assert any(e["target_id"] == "doc-1" for e in events)

    # Cross-company content read: 403 + audited; unknown document: 404.
    assert client.get("/api/v1/documents/doc-1/content", headers=hb).status_code == 403
    assert (
        _audit_rows(db_path, "SECURITY_CROSS_COMPANY_FILE_ACCESS_BLOCKED") != []
    )
    assert client.get("/api/v1/documents/no-such-doc/content", headers=ha).status_code == 404


# ----------------------------------------------------------------------
# the human correction workflow the review UI calls
# ----------------------------------------------------------------------


def test_field_correction_preserves_raw_and_confidence_and_audits(tmp_path: Path) -> None:
    db, client = _make_client(tmp_path)
    db_path = Path(db.db_path)
    ha = {"Authorization": f"Bearer {_login(client, 'clerk_a', 'Clerk#2026x')}"}

    edit = client.patch(
        "/api/v1/documents/doc-1/fields",
        headers=ha,
        json={"field_name": "amount_ttc_dzd", "new_value": "1750.00"},
    )
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert body["human_override_value"] == "1750.00"

    # The review read now shows the override while raw text and confidence
    # are untouched - the reviewer can always see what OCR originally said.
    payload = client.get("/api/v1/documents/doc-1/fields", headers=ha).json()
    field = next(f for f in payload["fields"] if f["field_name"] == "amount_ttc_dzd")
    assert field["raw_text"] == "1S 7S0 DD"
    assert field["confidence"] is None
    assert field["confidence_kind"] == "not_measured"
    assert field["normalized_value"] == "1750.00"
    assert field["human_override_value"] == "1750.00"
    assert field["edited_by_id"] == 1
    assert field["edited_at"] is not None

    # The edit is audited (the target is the field row, model-scoped); the
    # other tenant's decoy is unreachable.
    events = _audit_rows(db_path, "OCR_HUMAN_FIELD_EDIT")
    assert any(e["target_model"] == "ocr_extracted_fields" for e in events)

    unknown = client.patch(
        "/api/v1/documents/doc-1/fields",
        headers=ha,
        json={"field_name": "no_such_field", "new_value": "x"},
    )
    assert unknown.status_code == 404
