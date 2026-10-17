# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 5 Tests — RBAC, SoD, Tenant Isolation, API Bypass, Audit Immutability & Encrypted Backup/Restore."""

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import (
    AuditTamperError,
    AuthorizationError,
    ERPDatabase,
    FinancialIntegrityError,
    TenantIsolationError,
)
from dz_erp.intake_ocr import PrivateDocumentVault, upload_and_process_supplier_document
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    decide_invoice_approval,
    modify_draft_invoice_line,
    post_supplier_invoice,
    submit_invoice_for_approval,
)
from dz_erp.security_audit import (
    StructuredRedactingLogger,
    create_encrypted_backup,
    read_source_document_file_secure,
    read_supplier_invoice_secure,
    restore_and_verify_backup,
)


_TENANT_USERS = [
    (1, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (2, 1, "wh_a", "warehouse", 1, "Wh#2026x"),
    (3, 1, "approver_a", "approver", 1, "Appr#2026x"),
    (4, 1, "acct_a", "accountant", 1, "Acct#2026x"),
    (5, 1, "revoked_a", "admin", 0, "Revoked#2026x"),
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
]


def _setup_two_tenants(db: ERPDatabase) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, cid, username, role, active, password in _TENANT_USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, cid, username, role, active, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc) VALUES (10, 1, 'Supplier Alpha', 'supplier', '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)


def _login(client: TestClient, username: str, password: str) -> str:
    """Obtain a real server-issued bearer token (replaces the old forged headers)."""
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_rbac_sod_approval_invalidation_and_api_bypass_protection(tmp_path: Path) -> None:
    """Verify RBAC, Segregation of Duties, post-approval edit invalidation, and FastAPI endpoint guards."""
    db = ERPDatabase(str(tmp_path / "sec.sqlite3"))
    _setup_two_tenants(db)
    vault = PrivateDocumentVault(tmp_path / "vault")
    app = create_erp_fastapi_app(db, vault)
    client = TestClient(app)

    spec = {
        "supplier_name": "Supplier Alpha",
        "invoice_number": "FAC-SEC-001",
        "invoice_date": "2026-10-15",
        "supplier_nif": "001216099123456",
        "supplier_rc": "16/00-0991234B12",
        "supplier_ai": "16019912345",
        "payment_method": "BANK_TRANSFER",
        "amount_ht_dzd": "10000.00",
        "amount_tva_dzd": "1900.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "11900.00",
        "lines": [{"sku": "S1", "description": "Service", "qty": "1.00", "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    }
    doc = upload_and_process_supplier_document(
        db, vault, company_id=1, user_id=1, filename="sec1.pdf", mime_type="application/pdf", content=render_synthetic_invoice_pdf(spec)
    )

    inv = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=4,  # Created by acct_a (user 4) to test SoD self-approval block!
        supplier_id=10,
        supplier_invoice_no="FAC-SEC-001",
        invoice_date="2026-10-15",
        document_id=doc["document_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"description": "Service", "qty": "1", "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    inv_id = inv["invoice_id"]
    submit_invoice_for_approval(db, company_id=1, user_id=4, invoice_id=inv_id)

    # 1. Segregation of Duties: Creator (user 4) cannot approve their own invoice
    with pytest.raises(AuthorizationError, match="SEGREGATION_OF_DUTIES_VIOLATION"):
        decide_invoice_approval(db, company_id=1, approver_user_id=4, invoice_id=inv_id, approve=True)

    # 2. Warehouse user (user 2) cannot read supplier invoices or approve them
    with pytest.raises(AuthorizationError):
        read_supplier_invoice_secure(db, requesting_user_id=2, invoice_id=inv_id)

    # 3. Cross-tenant admin (user 20, company 2) cannot read Company 1's invoice or private vault file
    with pytest.raises(TenantIsolationError):
        read_supplier_invoice_secure(db, requesting_user_id=20, invoice_id=inv_id)
    with pytest.raises(TenantIsolationError):
        read_source_document_file_secure(db, vault, requesting_user_id=20, document_id=doc["document_id"])

    # 4. Test FastAPI HTTP API bypass attempts (now with real server-issued tokens)
    # Unauthenticated request -> 401
    assert client.get(f"/api/v1/invoices/{inv_id}").status_code == 401
    # Forged identity headers with no credential -> 401 (headers are no longer trusted)
    assert client.get(
        f"/api/v1/invoices/{inv_id}", headers={"X-User-Id": "1", "X-Company-Id": "1"}
    ).status_code == 401
    assert client.get(
        f"/api/v1/invoices/{inv_id}", headers={"X-User-Id": "20", "X-Company-Id": "1"}
    ).status_code == 401
    # Deactivated user (user 5) cannot obtain a token -> 401
    assert client.post(
        "/api/v1/auth/token", json={"username": "revoked_a", "password": "Revoked#2026x"}
    ).status_code == 401
    # Authenticated cross-tenant admin (user 20, company 2) reading Company 1's invoice -> 403
    token_b = _login(client, "admin_b", "AdminB#2026x")
    r_cross = client.get(
        f"/api/v1/invoices/{inv_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert r_cross.status_code == 403
    # Cross-company file access -> 403
    r_cross_file = client.get(
        f"/api/v1/documents/{doc['document_id']}/file",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r_cross_file.status_code == 403
    # Clerk attempting to post invoice via API bypass -> 403
    token_clerk = _login(client, "clerk_a", "Clerk#2026x")
    r_post_clerk = client.post(
        f"/api/v1/invoices/{inv_id}/post",
        headers={"Authorization": f"Bearer {token_clerk}", "Idempotency-Key": "bypass-key"},
    )
    assert r_post_clerk.status_code == 403

    # 5. Legitimate approval by a different authorized user (user 3), followed by a draft line edit
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=inv_id, approve=True)
    with db.transaction() as conn:
        line_id = conn.execute("SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (inv_id,)).fetchone()["id"]

    # Modifying an Approved invoice invalidates its approval and reverts to NeedsReview
    mod_res = modify_draft_invoice_line(
        db, company_id=1, user_id=1, invoice_id=inv_id, line_id=line_id, new_unit_price_dzd="12000.00"
    )
    assert mod_res["approval_invalidated"] is True
    assert mod_res["workflow_state"] == "NeedsReview"

    # Attempting to post after approval was invalidated is rejected
    with pytest.raises(FinancialIntegrityError):
        post_supplier_invoice(db, company_id=1, accountant_user_id=4, invoice_id=inv_id, idempotency_key="post-after-edit")


def test_audit_immutability_log_redaction_and_encrypted_backup_restore(tmp_path: Path) -> None:
    """Verify DB-trigger audit protection, secret/PII log redaction, and isolated backup restoration."""
    db_path = tmp_path / "prod.sqlite3"
    db = ERPDatabase(str(db_path))
    _setup_two_tenants(db)
    vault = PrivateDocumentVault(tmp_path / "prod_vault")

    spec = {
        "supplier_name": "Supplier Alpha",
        "invoice_number": "FAC-BK-001",
        "invoice_date": "2026-10-16",
        "supplier_nif": "001216099123456",
        "supplier_rc": "16/00-0991234B12",
        "supplier_ai": "16019912345",
        "payment_method": "BANK_TRANSFER",
        "amount_ht_dzd": "20000.00",
        "amount_tva_dzd": "3800.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "23800.00",
        "lines": [{"sku": "S1", "description": "Consulting", "qty": "1.00", "unit_price_dzd": "20000.00", "tax_rate": "19.0", "line_ht_dzd": "20000.00"}],
    }
    doc = upload_and_process_supplier_document(
        db, vault, company_id=1, user_id=1, filename="bk1.pdf", mime_type="application/pdf", content=render_synthetic_invoice_pdf(spec)
    )
    inv = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=10,
        supplier_invoice_no="FAC-BK-001",
        invoice_date="2026-10-16",
        document_id=doc["document_id"],
        amount_ht_dzd="20000.00",
        amount_tva_dzd="3800.00",
        amount_ttc_dzd="23800.00",
        lines=[{"description": "Consulting", "qty": "1", "unit_price_dzd": "20000.00", "tax_rate": "19.0", "line_ht_dzd": "20000.00"}],
    )
    submit_invoice_for_approval(db, company_id=1, user_id=1, invoice_id=inv["invoice_id"])
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=inv["invoice_id"], approve=True)
    post_supplier_invoice(db, company_id=1, accountant_user_id=4, invoice_id=inv["invoice_id"], idempotency_key="bk-post-1")

    # 1. Direct SQL UPDATE or DELETE on audit_events is blocked by triggers
    with pytest.raises(AuditTamperError, match="AUDIT_TAMPER_VIOLATION"):
        with db.transaction() as conn:
            conn.execute("UPDATE audit_events SET action_type = 'FORGED' WHERE id = 1")

    with pytest.raises(AuditTamperError, match="AUDIT_TAMPER_VIOLATION"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM audit_events WHERE id = 1")

    # 2. StructuredRedactingLogger strips secrets, tokens, IBANs, and NINs
    logger = StructuredRedactingLogger()
    entry = logger.log(
        "INFO",
        "AUTH_CHECK",
        "corr-123",
        Authorization="Bearer secret-jwt-token-xyz",
        password="mySuperSecretPassword",
        citizen_nin="109901601001234567",
        bank_note="Transfer to DZ5900100123456789012345 completed",
    )
    assert "secret-jwt-token-xyz" not in str(entry)
    assert "mySuperSecretPassword" not in str(entry)
    assert "109901601001234567" not in str(entry)
    assert "DZ5900100123456789012345" not in str(entry)
    assert "[REDACTED_NIN]" in str(entry)
    assert "[REDACTED_DZ_IBAN]" in str(entry)

    # 3. Encrypted Backup & Isolated Restore Verification
    backup_meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backups" / "backup.enc",
        backup_encryption_key=vault.encryption_key,
    )
    restore_report = restore_and_verify_backup(
        backup_path=Path(backup_meta["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "isolated_restore",
    )
    assert restore_report["db_sha256_verified"] is True
    assert restore_report["trial_balance_debit_dzd"] == "23800.00"
    assert restore_report["trial_balance_credit_dzd"] == "23800.00"
    assert restore_report["vault_files_verified"] == 1
    assert restore_report["audit_chains_valid"] is True
