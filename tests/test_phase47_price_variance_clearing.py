# SPDX-License-Identifier: LGPL-3.0-only
"""F-R2-3 — purchase price variances must not linger on the clearing account.

The GRN credits the stock-clearing account (``380``) at *receipt* cost; the
invoice debits the same account at *invoice* price. While a receipt and its
invoice agree, the two net out. When a price changed in between and the
difference is allowed by ``price_tolerance_dzd``, the difference used to remain
on ``380`` for ever, explained nowhere.

Nothing here decides an accounting policy: the variance account is a *product
configuration* (``products.price_variance_account_code``) and the tests use an
obviously synthetic code. What is asserted is that once an account is
configured, no unexplained residual remains, the entry still balances, and a
reversal mirrors the treatment. Without an account the historical posting is
preserved and the residual is recorded as an explained, audited amount.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path


from dz_erp.database import ERPDatabase
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    create_purchase_order,
    create_reversal_request,
    decide_invoice_approval,
    decide_reversal_approval,
    post_reversal,
    post_supplier_invoice,
    receive_goods_against_po,
    submit_invoice_for_approval,
    submit_reversal_for_approval,
)

#: Clearly synthetic. The real account is an expert-comptable decision
#: (docs/REMAINING_MATRIX R-SCF / docs/REMAINING_BLOCKERS.md LB-2).
VARIANCE_ACCOUNT = "608"


def _db(tmp_path: Path) -> ERPDatabase:
    db = ERPDatabase(str(tmp_path / "pv.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Variance Co', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (1, 1, 'clerk', 'clerk')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (2, 1, 'wh', 'warehouse')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (3, 1, 'appr', 'approver')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (4, 1, 'acct', 'accountant')")
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc) VALUES (100, 1, 'Fournisseur', 'supplier', 'DZ', 'REEL',"
            " '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
        )
        conn.execute(
            "INSERT INTO products (id, company_id, sku, name, is_storable, standard_cost_dzd,"
            " stock_account_code, clearing_account_code)"
            " VALUES (50, 1, 'STEEL', 'Bobine', 1, '1000.000000', '31', '380')"
        )
    from dz_erp.localization import load_official_odoo_chart

    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    return db


def _configure_variance(db: ERPDatabase, account: str | None = VARIANCE_ACCOUNT) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE products SET price_variance_account_code = ? WHERE id = 50", (account,))



def _buy(db: ERPDatabase, *, po_price="1000.00", receipt_qty="10", invoice_qty="10",
         invoice_price="1000.00", tolerance="0.00", grn_splits=(("10", "g1"),)):
    """PO → receipt(s) → invoice → approval → posting, returning the post result."""
    po = create_purchase_order(
        db, company_id=1, user_id=1, supplier_id=100, po_number="PO-PV",
        lines=[{"product_id": 50, "description": "Bobine", "qty_ordered": receipt_qty,
                "unit_price_dzd": po_price, "tax_rate": "19.0"}],
    )
    line_id = po["po_line_ids"][0]
    for qty, key in grn_splits:
        receive_goods_against_po(
            db, 1, 2, po["po_id"], f"GRN-{key}", [{"po_line_id": line_id, "qty_done": qty}],
            idempotency_key=key,
        )
    ht = round(Decimal(invoice_qty) * Decimal(invoice_price), 2)
    tva = round(ht * Decimal("0.19"), 2)
    inv = create_and_validate_supplier_invoice(
        db, company_id=1, user_id=1, supplier_id=100, supplier_invoice_no="FAC-PV",
        invoice_date="2026-02-14", po_id=po["po_id"], price_tolerance_dzd=tolerance,
        amount_ht_dzd=f"{ht:.2f}", amount_tva_dzd=f"{tva:.2f}", amount_ttc_dzd=f"{ht + tva:.2f}",
        lines=[{"po_line_id": line_id, "product_id": 50, "description": "Bobine",
                "qty": invoice_qty, "unit_price_dzd": invoice_price, "tax_rate": "19.0",
                "line_ht_dzd": f"{ht:.2f}"}],
    )
    assert inv["workflow_state"] == "Validated", inv["validation_errors"]
    submit_invoice_for_approval(db, 1, 1, inv["invoice_id"])
    decide_invoice_approval(db, 1, 3, inv["invoice_id"], approve=True)
    return post_supplier_invoice(db, 1, 4, inv["invoice_id"], "post-pv")


def _net(db: ERPDatabase) -> dict[str, Decimal]:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT account_code, debit_dzd, credit_dzd FROM journal_entry_lines"
        ).fetchall()
    net: dict[str, Decimal] = {}
    for row in rows:
        net[row["account_code"]] = net.get(row["account_code"], Decimal("0.00")) + (
            Decimal(row["debit_dzd"]) - Decimal(row["credit_dzd"])
        )
    return {k: v for k, v in net.items() if v != 0}


def test_fr2_3_an_exact_match_leaves_nothing_on_the_clearing_account(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _configure_variance(db)
    result = _buy(db)

    assert result["price_variances"] == []
    assert result["unallocated_price_variances"] == []
    net = _net(db)
    assert "380" not in net, net
    assert "608" not in net, net
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_3_a_within_tolerance_increase_moves_to_the_configured_account(tmp_path: Path) -> None:
    """1,000.00 received, 1,010.00 invoiced, 15.00 tolerance: the 100.00 is a variance."""
    db = _db(tmp_path)
    _configure_variance(db)
    result = _buy(db, invoice_price="1010.000000", tolerance="15.00")

    assert result["unallocated_price_variances"] == []
    variance = result["price_variances"]
    assert len(variance) == 1
    assert variance[0]["received_value_dzd"] == "10000.00"
    assert variance[0]["invoice_value_dzd"] == "10100.00"
    assert variance[0]["variance_dzd"] == "100.00"
    assert variance[0]["variance_account_code"] == VARIANCE_ACCOUNT

    net = _net(db)
    assert "380" not in net, f"clearing account still carries a residual: {net}"
    assert net["608"] == Decimal("100.00"), net
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_3_a_within_tolerance_decrease_credits_the_variances(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _configure_variance(db)
    result = _buy(db, invoice_price="990.000000", tolerance="15.00")

    assert result["price_variances"][0]["variance_dzd"] == "-100.00"
    net = _net(db)
    assert "380" not in net, net
    assert net["608"] == Decimal("-100.00"), net
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_3_an_above_tolerance_difference_is_still_refused(tmp_path: Path) -> None:
    """The variance mechanism must not become a way to bypass the 3-way match."""
    db = _db(tmp_path)
    _configure_variance(db)
    po = create_purchase_order(
        db, company_id=1, user_id=1, supplier_id=100, po_number="PO-PV",
        lines=[{"product_id": 50, "description": "Bobine", "qty_ordered": "10",
                "unit_price_dzd": "1000.00", "tax_rate": "19.0"}],
    )
    line_id = po["po_line_ids"][0]
    receive_goods_against_po(db, 1, 2, po["po_id"], "GRN-1", [{"po_line_id": line_id, "qty_done": "10"}],
                             idempotency_key="g1")
    inv = create_and_validate_supplier_invoice(
        db, company_id=1, user_id=1, supplier_id=100, supplier_invoice_no="FAC-PV",
        invoice_date="2026-02-14", po_id=po["po_id"], price_tolerance_dzd="0.00",
        amount_ht_dzd="10100.00", amount_tva_dzd="1919.00", amount_ttc_dzd="12019.00",
        lines=[{"po_line_id": line_id, "product_id": 50, "description": "Bobine", "qty": "10",
                "unit_price_dzd": "1010.000000", "tax_rate": "19.0", "line_ht_dzd": "10100.00"}],
    )
    assert inv["workflow_state"] != "Validated"
    assert any("THREE_WAY_MATCH_PRICE_MISMATCH" in e for e in inv["validation_errors"])


def test_fr2_3_partial_receipt_and_partial_invoice_produce_no_residual(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _configure_variance(db)
    result = _buy(db, receipt_qty="10", invoice_qty="6", grn_splits=(("6", "g1"),))
    assert result["price_variances"] == []
    assert "380" not in _net(db)
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_3_multiple_receipts_against_one_invoice_produce_no_residual(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _configure_variance(db)
    result = _buy(db, grn_splits=(("4", "g1"), ("6", "g2")))
    assert result["price_variances"] == []
    assert "380" not in _net(db), _net(db)
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_3_the_split_never_introduces_a_rounding_cent(tmp_path: Path) -> None:
    """The two halves of the debit must add back to the invoiced line, exactly."""
    db = _db(tmp_path)
    _configure_variance(db)
    result = _buy(db, po_price="3.333", receipt_qty="3", invoice_qty="3",
                  invoice_price="3.333000", grn_splits=(("3", "g1"),))
    received = Decimal(result["price_variances"][0]["received_value_dzd"]) if result["price_variances"] else Decimal("10.00")
    invoice_value = Decimal(result["price_variances"][0]["invoice_value_dzd"]) if result["price_variances"] else Decimal("10.00")
    variance = Decimal(result["price_variances"][0]["variance_dzd"]) if result["price_variances"] else Decimal("0.00")
    assert received + variance == invoice_value
    assert "380" not in _net(db), _net(db)
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_3_without_a_configured_account_the_residual_is_explained_and_audited(
    tmp_path: Path,
) -> None:
    """No policy configured: the historical posting stands, but never silently.

    The difference stays on ``380`` (that is today's behaviour and must not
    change without an approved account), and it is named in the post result and
    in an audit event so the balance is traceable to a decision, not a mystery.
    """
    db = _db(tmp_path)
    _configure_variance(db, account=None)
    result = _buy(db, invoice_price="1010.000000", tolerance="15.00")

    assert result["price_variances"] == []
    unallocated = result["unallocated_price_variances"]
    assert len(unallocated) == 1
    assert unallocated[0]["variance_dzd"] == "100.00"
    assert unallocated[0]["variance_account_code"] is None

    net = _net(db)
    assert net["380"] == Decimal("100.00"), net  # unchanged, and now explained

    with db.transaction() as conn:
        event = conn.execute(
            "SELECT after_json FROM audit_events WHERE action_type = 'PRICE_VARIANCE_UNALLOCATED'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    import json

    payload = json.loads(event["after_json"])
    assert payload["reason"] == "NO_PRICE_VARIANCE_ACCOUNT_CONFIGURED"
    assert payload["lines"][0]["variance_dzd"] == "100.00"
    assert db.verify_audit_chain(1)["valid"] is True


def test_fr2_3_a_reversal_mirrors_the_variance_and_nets_everything_out(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _configure_variance(db)
    posted = _buy(db, invoice_price="1010.000000", tolerance="15.00")
    assert _net(db)["608"] == Decimal("100.00")

    first = create_reversal_request(db, 1, 4, posted["invoice_id"], "supplier dispute", "rev-pv")
    note_id = first["credit_note_invoice_id"]
    submit_reversal_for_approval(db, 1, 4, note_id)
    decide_reversal_approval(db, 1, 3, note_id, approve=True)
    post_reversal(db, 1, 4, note_id, "rev-post-pv")

    net = _net(db)
    assert "608" not in net, f"the variance was not mirrored by the credit note: {net}"
    assert net.get("401", Decimal("0")) == 0
    # The goods are still in stock, so the clearing account keeps the receipt
    # credit the cancelled invoice had offset — the receipt was never reversed.
    assert net["380"] == Decimal("-10000.00"), net
    assert net["31"] == Decimal("10000.00"), net
    assert db.trial_balance(1)["balanced"] is True
    assert db.verify_audit_chain(1)["valid"] is True


def test_fr2_3_the_migration_adds_the_column_without_inventing_a_policy(tmp_path: Path) -> None:
    """A database created before the column existed gains it as NULL."""
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE products (id INTEGER PRIMARY KEY, company_id INTEGER, sku TEXT,"
        " stock_account_code TEXT, clearing_account_code TEXT NOT NULL DEFAULT '380')"
    )
    raw.execute("INSERT INTO products (id, company_id, sku) VALUES (1, 1, 'OLD')")
    raw.commit()
    raw.close()

    db = ERPDatabase(str(path))
    with db.transaction() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
        row = conn.execute("SELECT price_variance_account_code FROM products WHERE id = 1").fetchone()
    assert "price_variance_account_code" in cols
    assert row["price_variance_account_code"] is None
