# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 25 — read-only listing and reporting API for installed clients.

The desktop client presents real data through these endpoints instead of
ad-hoc SQL. What this phase must prove: every listing is authenticated,
scoped to the caller's company, bounded by a clamped limit, and the two
financial surfaces (invoice list, trial balance) enforce the same
warehouse-role denial as the per-invoice read - with the refusal recorded in
the audit log - so a list view is never a way around a detail view's
controls. The trial balance reports POSTED entries only and refuses an
unbalanced ledger.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase, TenantIsolationError
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    decide_invoice_approval,
    post_supplier_invoice,
    submit_invoice_for_approval,
)

_TENANT_USERS = [
    (1, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (3, 1, "approver_a", "approver", 1, "Appr#2026x"),
    (4, 1, "acct_a", "accountant", 1, "Acct#2026x"),
    (5, 1, "wh_a", "warehouse", 1, "Wh#2026x"),
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
]


def _setup_two_tenants(db: ERPDatabase) -> None:
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
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc) "
            "VALUES (10, 1, 'Supplier Alpha', 'supplier', '001216099123456', "
            "'099116010123456', '16019912345', '16/00-0991234B12')"
        )
        conn.execute(
            "INSERT INTO accounts (company_id, xml_id, code, name_en, name_fr, name_ar, account_type) "
            "VALUES (1, 'xml_401', '401', 'Suppliers', 'Fournisseurs', 'الموردون', 'liability_current')"
        )
        conn.execute(
            "INSERT INTO accounts (company_id, xml_id, code, name_en, name_fr, name_ar, account_type) "
            "VALUES (2, 'xml_401', '401', 'Suppliers B', 'Fournisseurs B', 'الموردون', 'liability_current')"
        )
        conn.execute(
            "INSERT INTO products (company_id, sku, name, uom, is_storable, standard_cost_dzd) "
            "VALUES (1, 'SKU-A1', 'Product Alpha', 'UNIT', 1, '1500.000000')"
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, "
            "page_count, file_sha256, vault_path, security_status, workflow_state, "
            "uploaded_by_id, uploaded_at) "
            "VALUES ('doc-1', 1, 'inv.pdf', 'application/pdf', 1024, 1, "
            "'" + "a" * 64 + "', 'vault/doc-1.bin', 'CLEAN', 'Uploaded', 1, '2026-10-20T10:00:00+00:00')"
        )


def _make_client(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "read.sqlite3"))
    _setup_two_tenants(db)
    vault = PrivateDocumentVault(tmp_path / "vault")
    return db, TestClient(create_erp_fastapi_app(db, vault))


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


_LIST_ENDPOINTS = [
    "/api/v1/accounts",
    "/api/v1/products",
    "/api/v1/documents",
    "/api/v1/invoices",
    "/api/v1/accounting/trial-balance",
]


def test_read_endpoints_require_authentication(tmp_path: Path) -> None:
    """No listing leaks without a server-issued credential."""
    _, client = _make_client(tmp_path)
    for url in _LIST_ENDPOINTS:
        resp = client.get(url)
        assert resp.status_code == 401, (url, resp.status_code, resp.text)


def test_reference_lists_are_tenant_scoped(tmp_path: Path) -> None:
    """Accounts, products and documents show only the caller's company rows."""
    db, client = _make_client(tmp_path)
    ha = {"Authorization": f"Bearer {_login(client, 'acct_a', 'Acct#2026x')}"}
    hb = {"Authorization": f"Bearer {_login(client, 'admin_b', 'AdminB#2026x')}"}

    acc_a = client.get("/api/v1/accounts", headers=ha).json()
    acc_b = client.get("/api/v1/accounts", headers=hb).json()
    assert [r["code"] for r in acc_a["accounts"]] == ["401"]
    assert [r["name_fr"] for r in acc_a["accounts"]] == ["Fournisseurs"]
    assert [r["name_fr"] for r in acc_b["accounts"]] == ["Fournisseurs B"]

    prod_a = client.get("/api/v1/products", headers=ha).json()
    prod_b = client.get("/api/v1/products", headers=hb).json()
    assert [r["sku"] for r in prod_a["products"]] == ["SKU-A1"]
    assert prod_b["products"] == []

    docs_a = client.get("/api/v1/documents", headers=ha).json()
    docs_b = client.get("/api/v1/documents", headers=hb).json()
    assert [d["id"] for d in docs_a["documents"]] == ["doc-1"]
    assert docs_b["documents"] == []
    # Custody fields stay internal: contents are read via the audited
    # per-document secure path, not a list view.
    assert "vault_path" not in docs_a["documents"][0]
    assert "file_sha256" not in docs_a["documents"][0]


def test_unscoped_reads_are_refused_at_the_data_layer(tmp_path: Path) -> None:
    """A missing company scope is a tenant-isolation bug, not an empty list."""
    db, _ = _make_client(tmp_path)
    for call in (
        lambda: db.list_accounts(None),
        lambda: db.list_products(None),
        lambda: db.list_source_documents(None),
        lambda: db.list_supplier_invoices(None),
        lambda: db.trial_balance(None),
    ):
        try:
            call()
        except TenantIsolationError:
            pass
        else:  # pragma: no cover - assertion message below explains
            raise AssertionError("Unscoped read did not raise TenantIsolationError")


def test_financial_lists_refuse_warehouse_role_with_audited_denial(tmp_path: Path) -> None:
    """The warehouse role gets 403 on invoice list and trial balance, audited."""
    db, client = _make_client(tmp_path)
    wh = {"Authorization": f"Bearer {_login(client, 'wh_a', 'Wh#2026x')}"}
    inv = client.get("/api/v1/invoices", headers=wh)
    tb = client.get("/api/v1/accounting/trial-balance", headers=wh)
    assert inv.status_code == 403, inv.text
    assert tb.status_code == 403, tb.text
    with db.transaction() as conn:
        events = conn.execute(
            "SELECT target_model, target_id FROM audit_events "
            "WHERE action_type = 'SECURITY_UNAUTHORIZED_ROLE_READ_BLOCKED'"
        ).fetchall()
    targets = {(e["target_model"], e["target_id"]) for e in events}
    assert ("supplier_invoices", "list") in targets
    assert ("journal_entries", "trial_balance") in targets


def _post_one_invoice(db: ERPDatabase) -> int:
    """Drive the full verified lifecycle: create → submit → approve → post."""
    inv = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=4,
        supplier_id=10,
        supplier_invoice_no="FAC-R25-001",
        invoice_date="2026-10-15",
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[
            {
                "description": "Prestation",
                "qty": "1",
                "unit_price_dzd": "10000.00",
                "tax_rate": "19.0",
                "line_ht_dzd": "10000.00",
            }
        ],
    )
    inv_id = inv["invoice_id"]
    submit_invoice_for_approval(db, company_id=1, user_id=4, invoice_id=inv_id)
    decide_invoice_approval(
        db, company_id=1, approver_user_id=3, invoice_id=inv_id, approve=True, reason="ok"
    )
    post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=inv_id, idempotency_key="IDEM-R25-1"
    )
    return inv_id


def test_invoice_list_and_trial_balance_scoped_balanced_and_posted_only(tmp_path: Path) -> None:
    """Posted invoices are visible to the owner company only; drafts stay out."""
    db, client = _make_client(tmp_path)
    ha = {"Authorization": f"Bearer {_login(client, 'acct_a', 'Acct#2026x')}"}
    hb = {"Authorization": f"Bearer {_login(client, 'admin_b', 'AdminB#2026x')}"}

    # Before posting: nothing on either side.
    assert client.get("/api/v1/invoices", headers=ha).json()["invoices"] == []
    assert client.get("/api/v1/invoices", headers=hb).json()["invoices"] == []

    inv_id = _post_one_invoice(db)

    # A balanced DRAFT entry that must NOT appear in the trial balance.
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO journal_entries (id, company_id, entry_number, journal_code, "
            "entry_date, state, source_model, source_id, total_debit_dzd, total_credit_dzd) "
            "VALUES (900, 1, 'DRAFT-1', 'MISC', '2026-10-20', 'DRAFT', 'manual', 'manual-1', "
            "'500.00', '500.00')"
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, "
            "label, debit_dzd, credit_dzd) VALUES (1, 900, '699', 'Draft line', '500.00', '0.00')"
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, "
            "label, debit_dzd, credit_dzd) VALUES (1, 900, '499', 'Draft line', '0.00', '500.00')"
        )

    list_a = client.get("/api/v1/invoices", headers=ha).json()
    list_b = client.get("/api/v1/invoices", headers=hb).json()
    assert [i["id"] for i in list_a["invoices"]] == [inv_id]
    assert list_a["invoices"][0]["workflow_state"] == "Posted"
    assert list_b["invoices"] == []

    tb_a = client.get("/api/v1/accounting/trial-balance", headers=ha).json()
    tb_b = client.get("/api/v1/accounting/trial-balance", headers=hb).json()
    assert tb_a["entry_state"] == "POSTED"
    assert tb_a["balanced"] is True
    assert tb_a["total_debit_dzd"] == tb_a["total_credit_dzd"] == "11900.00"
    # The DRAFT entry's 500.00 lines must be excluded.
    assert all("699" != r["account_code"] and "499" != r["account_code"] for r in tb_a["rows"])
    assert tb_b["rows"] == [] and tb_b["total_debit_dzd"] == "0.00"


def test_list_limit_is_clamped(tmp_path: Path) -> None:
    """Limits are validated server-side: junk, zero and oversized are refused."""
    _, client = _make_client(tmp_path)
    ha = {"Authorization": f"Bearer {_login(client, 'acct_a', 'Acct#2026x')}"}
    assert client.get("/api/v1/accounts?limit=0", headers=ha).status_code == 422
    assert client.get("/api/v1/accounts?limit=501", headers=ha).status_code == 422
    assert client.get("/api/v1/accounts?limit=abc", headers=ha).status_code == 422
    one = client.get("/api/v1/accounts?limit=1", headers=ha).json()
    assert one["count"] == 1
