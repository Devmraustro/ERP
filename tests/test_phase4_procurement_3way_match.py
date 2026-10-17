# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 4 Tests — Procurement, Stock, Three-Way Matching, Idempotency & Concurrency."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from dz_erp.database import ERPDatabase, FinancialIntegrityError
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    create_purchase_order,
    decide_invoice_approval,
    deduct_stock_concurrent_safe,
    post_supplier_invoice,
    receive_goods_against_po,
    reverse_posted_invoice,
    submit_invoice_for_approval,
)


def _seed_p2p_fixture(db: ERPDatabase) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) VALUES (1, 'DZ1', 'SARL Industriel', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (1, 1, 'clerk_ap', 'clerk')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (2, 1, 'wh_mgr', 'warehouse')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (3, 1, 'fin_approver', 'approver')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (4, 1, 'chief_acct', 'accountant')")
        conn.execute(
            """
            INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime, nif, nis, ai, rc)
            VALUES (100, 1, 'Fournisseur Acier SPA', 'supplier', 'DZ', 'REEL',
                    '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')
            """
        )
        conn.execute(
            """
            INSERT INTO products (id, company_id, sku, name, is_storable, standard_cost_dzd, stock_account_code, clearing_account_code)
            VALUES (50, 1, 'STEEL-COIL', 'Bobine Acier', 1, '1000.000000', '31', '380')
            """
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)


def test_po_partial_receipt_backorder_3way_match_and_no_double_stock_movement() -> None:
    """Verify PO -> Partial Receipt (60/100) + Backorder -> Invoice (60) -> Approval -> Post without double stock move."""
    db = ERPDatabase(":memory:")
    _seed_p2p_fixture(db)

    # 1. Create PO for 100 units @ 1,000.00 DZD
    po = create_purchase_order(
        db,
        company_id=1,
        user_id=1,
        po_number="PO-2026-0042",
        supplier_id=100,
        lines=[{"product_id": 50, "description": "Bobine Acier", "qty_ordered": "100", "unit_price_dzd": "1000.00", "tax_rate": "19.0"}],
    )
    po_line_id = po["po_line_ids"][0]

    # 2. Invoice created BEFORE receipt is held in NeedsReview (THREE_WAY_MATCH_QTY_MISMATCH)
    pre_inv = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-PRE-001",
        invoice_date="2026-10-01",
        po_id=po["po_id"],
        amount_ht_dzd="60000.00",
        amount_tva_dzd="11400.00",
        amount_ttc_dzd="71400.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine Acier", "qty": "60", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "60000.00"}],
    )
    assert pre_inv["workflow_state"] == "NeedsReview"
    assert any("THREE_WAY_MATCH_QTY_MISMATCH" in e for e in pre_inv["validation_errors"])

    # Cannot submit a NeedsReview invoice for approval
    with pytest.raises(FinancialIntegrityError):
        submit_invoice_for_approval(db, company_id=1, user_id=1, invoice_id=pre_inv["invoice_id"])

    # 3. Validate Partial Goods Receipt for 60 units (creates backorder for remaining 40)
    grn1 = receive_goods_against_po(
        db,
        company_id=1,
        user_id=2,
        po_id=po["po_id"],
        grn_number="GRN-2026-0019",
        receipt_lines=[{"po_line_id": po_line_id, "qty_done": "60"}],
        idempotency_key="grn-key-001",
    )
    assert grn1["po_state"] == "PARTIALLY_RECEIVED"
    assert grn1["backorder_grn_id"] is not None
    assert grn1["total_valuation_dzd"] == "60000.00"

    # Retrying receipt with same idempotency_key does not move stock twice
    grn1_retry = receive_goods_against_po(
        db,
        company_id=1,
        user_id=2,
        po_id=po["po_id"],
        grn_number="GRN-2026-0019",
        receipt_lines=[{"po_line_id": po_line_id, "qty_done": "60"}],
        idempotency_key="grn-key-001",
    )
    assert grn1_retry["idempotent_replay"] is True

    with db.transaction() as conn:
        quant = conn.execute("SELECT qty_on_hand FROM stock_quants WHERE product_id = 50").fetchone()
        sm_count_before = conn.execute("SELECT COUNT(*) AS c FROM stock_moves").fetchone()["c"]
        assert quant["qty_on_hand"] == "60.000000"
        assert sm_count_before == 1

    # 4. Create Supplier Invoice for the 60 received units -> Validated -> PendingApproval -> Approved -> Posted
    inv1 = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-2026 / 0089",
        invoice_date="2026-10-10",
        po_id=po["po_id"],
        grn_id=grn1["grn_id"],
        amount_ht_dzd="60000.00",
        amount_tva_dzd="11400.00",
        amount_ttc_dzd="71400.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine Acier", "qty": "60", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "60000.00"}],
    )
    assert inv1["workflow_state"] == "Validated"
    assert inv1["validation_errors"] == []

    submit_invoice_for_approval(db, company_id=1, user_id=1, invoice_id=inv1["invoice_id"])
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=inv1["invoice_id"], approve=True)

    post_res = post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=inv1["invoice_id"], idempotency_key="post-inv1-key"
    )
    assert post_res["workflow_state"] == "Posted"
    assert post_res["total_debit_dzd"] == "71400.00"
    assert post_res["total_credit_dzd"] == "71400.00"

    # Verify stock did NOT move when invoice was posted, and 380 clearing account is reconciled
    with db.transaction() as conn:
        quant_after = conn.execute("SELECT qty_on_hand FROM stock_quants WHERE product_id = 50").fetchone()
        sm_count_after = conn.execute("SELECT COUNT(*) AS c FROM stock_moves").fetchone()["c"]
        unreconciled_380 = conn.execute(
            "SELECT COUNT(*) AS c FROM journal_entry_lines WHERE account_code = '380' AND reconciled = 0"
        ).fetchone()["c"]
        assert quant_after["qty_on_hand"] == "60.000000"
        assert sm_count_after == sm_count_before == 1
        assert unreconciled_380 == 0

    # 5. Attempting to bill 50 more units before the remaining 40 units are received is blocked
    inv_over = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-2026-0095",
        invoice_date="2026-10-12",
        po_id=po["po_id"],
        amount_ht_dzd="50000.00",
        amount_tva_dzd="9500.00",
        amount_ttc_dzd="59500.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine Acier", "qty": "50", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "50000.00"}],
    )
    assert inv_over["workflow_state"] == "NeedsReview"
    assert any("THREE_WAY_MATCH_QTY_MISMATCH" in e for e in inv_over["validation_errors"])


def test_duplicate_detection_price_tax_mismatch_idempotent_retry_and_reversal() -> None:
    """Verify normalized & fuzzy duplicate detection, price/tax mismatch holds, retry safety, and credit note reversal."""
    db = ERPDatabase(":memory:")
    _seed_p2p_fixture(db)

    po = create_purchase_order(
        db,
        company_id=1,
        user_id=1,
        po_number="PO-2026-0100",
        supplier_id=100,
        lines=[{"product_id": 50, "description": "Bobine Acier", "qty_ordered": "20", "unit_price_dzd": "1000.00", "tax_rate": "19.0"}],
    )
    po_line_id = po["po_line_ids"][0]
    grn = receive_goods_against_po(
        db,
        company_id=1,
        user_id=2,
        po_id=po["po_id"],
        grn_number="GRN-2026-0100",
        receipt_lines=[{"po_line_id": po_line_id, "qty_done": "20"}],
    )

    # 1. Tax & total mismatch is flagged and held in NeedsReview
    bad_math = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-BAD-MATH",
        invoice_date="2026-10-10",
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="12500.00",  # Wrong TTC
        lines=[{"product_id": 50, "description": "Bobine", "qty": "10", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    assert bad_math["workflow_state"] == "NeedsReview"
    assert any("TOTAL_TTC_MISMATCH" in e for e in bad_math["validation_errors"])

    # 2. Price mismatch against PO is flagged and held in NeedsReview
    bad_price = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-BAD-PRICE",
        invoice_date="2026-10-10",
        po_id=po["po_id"],
        amount_ht_dzd="12000.00",
        amount_tva_dzd="2280.00",
        amount_ttc_dzd="14280.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine", "qty": "10", "unit_price_dzd": "1200.00", "tax_rate": "19.0", "line_ht_dzd": "12000.00"}],
    )
    assert bad_price["workflow_state"] == "NeedsReview"
    assert any("THREE_WAY_MATCH_PRICE_MISMATCH" in e for e in bad_price["validation_errors"])

    # 3. Valid invoice for 10 units -> Post -> Retry with same Idempotency-Key
    valid_inv = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-2026 / 0089",
        invoice_date="2026-10-10",
        po_id=po["po_id"],
        grn_id=grn["grn_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine", "qty": "10", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    submit_invoice_for_approval(db, company_id=1, user_id=1, invoice_id=valid_inv["invoice_id"])
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=valid_inv["invoice_id"], approve=True)

    p1 = post_supplier_invoice(db, company_id=1, accountant_user_id=4, invoice_id=valid_inv["invoice_id"], idempotency_key="retry-key-99")
    p2 = post_supplier_invoice(db, company_id=1, accountant_user_id=4, invoice_id=valid_inv["invoice_id"], idempotency_key="retry-key-99")
    assert p1["journal_entry_id"] == p2["journal_entry_id"]
    assert p2["idempotent_replay"] is True

    # 4. Duplicate invoice with altered formatting ("FAC 2026-0089") is rejected as DuplicateRejected
    dup_fmt = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC 2026-0089",
        invoice_date="2026-10-11",
        po_id=po["po_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine", "qty": "10", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    assert dup_fmt["workflow_state"] == "DuplicateRejected"

    # 5. Near-duplicate invoice number ("FAC-2026-008B" - 1 char OCR confusion, same amount) held in NeedsReview
    fuzzy_dup = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=100,
        supplier_invoice_no="FAC-2026-008B",
        invoice_date="2026-10-11",
        po_id=po["po_id"],
        amount_ht_dzd="10000.00",
        amount_tva_dzd="1900.00",
        amount_ttc_dzd="11900.00",
        lines=[{"po_line_id": po_line_id, "product_id": 50, "description": "Bobine", "qty": "10", "unit_price_dzd": "1000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}],
    )
    assert fuzzy_dup["workflow_state"] == "NeedsReview"
    assert any("FUZZY_DUPLICATE_SUSPECTED" in e for e in fuzzy_dup["validation_errors"])

    # 6. Reverse posted invoice via Credit Note; verify original stays POSTED and untouched
    with pytest.raises(FinancialIntegrityError, match="FINANCIAL_INTEGRITY_VIOLATION"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM journal_entries WHERE id = ?", (p1["journal_entry_id"],))

    rev = reverse_posted_invoice(
        db,
        company_id=1,
        accountant_user_id=4,
        original_invoice_id=valid_inv["invoice_id"],
        reason="Correction de prix unitaire",
        idempotency_key="rev-key-01",
    )
    assert rev["reversal_journal_entry_id"] is not None

    with db.transaction() as conn:
        orig_row = conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (valid_inv["invoice_id"],)).fetchone()
        orig_je = conn.execute("SELECT * FROM journal_entries WHERE id = ?", (p1["journal_entry_id"],)).fetchone()
        assert orig_row["workflow_state"] == "Posted"
        assert orig_row["amount_ttc_dzd"] == "11900.00"
        assert orig_je["state"] == "POSTED"
        assert orig_je["reversal_entry_id"] == rev["reversal_journal_entry_id"]


def test_concurrent_stock_updates_never_go_negative(tmp_path: Path) -> None:
    """Fire 5 concurrent threads each attempting to deduct 4 units from 10 on-hand units."""
    db_file = tmp_path / "concurrent_stock.sqlite3"
    db = ERPDatabase(str(db_file))
    _seed_p2p_fixture(db)

    po = create_purchase_order(
        db,
        company_id=1,
        user_id=1,
        po_number="PO-CONC-01",
        supplier_id=100,
        lines=[{"product_id": 50, "description": "Bobine", "qty_ordered": "10", "unit_price_dzd": "1000.00", "tax_rate": "19.0"}],
    )
    receive_goods_against_po(
        db,
        company_id=1,
        user_id=2,
        po_id=po["po_id"],
        grn_number="GRN-CONC-01",
        receipt_lines=[{"po_line_id": po["po_line_ids"][0], "qty_done": "10"}],
    )

    def _worker() -> str:
        try:
            deduct_stock_concurrent_safe(db, company_id=1, user_id=2, warehouse_code="WH-MAIN", product_id=50, qty_to_deduct="4")
            return "SUCCESS"
        except FinancialIntegrityError as exc:
            if "INSUFFICIENT_STOCK_ERROR" in str(exc):
                return "INSUFFICIENT"
            raise

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(lambda _: _worker(), range(5)))

    assert outcomes.count("SUCCESS") == 2
    assert outcomes.count("INSUFFICIENT") == 3

    with db.transaction() as conn:
        q = conn.execute("SELECT qty_on_hand FROM stock_quants WHERE product_id = 50").fetchone()
        assert q["qty_on_hand"] == "2.000000"
