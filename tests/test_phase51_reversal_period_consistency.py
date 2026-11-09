# SPDX-License-Identifier: LGPL-3.0-only
"""F-R5-1 — a reversal's entry number and fiscal year must agree with its date.

A reversal is booked in the period it is issued (the entry is dated *today*).
Until this round the entry number and the credit note's ``fiscal_year`` were
taken from the **original** invoice instead, which produced records that
contradicted themselves:

* an entry numbered ``RAVOIR/2025/00001`` carrying ``entry_date`` 2026-11-01,
  so an auditor reconciling the 2025 journal by number prefix pulls in a 2026
  entry, and the 2026 ledger contains an entry numbered for the prior year;
* a credit note whose ``invoice_date`` was 2026-11-01 while its ``fiscal_year``
  said 2025 — and ``fiscal_year`` is what the duplicate-invoice unique index
  (``company_id, supplier_id, normalized_invoice_no, fiscal_year``) and every
  year-grouped report key on.

The invariant asserted here holds under **either** dating policy: whichever
period a reversal is booked in, the number's year and the date's year agree,
and the credit note's ``fiscal_year`` equals the year of its own
``invoice_date``. No accounting policy is chosen by this module — it only
requires the record not to disagree with itself.
"""

from __future__ import annotations

import datetime
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


def _db(tmp_path: Path) -> ERPDatabase:
    db = ERPDatabase(str(tmp_path / "period.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Period Co', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, name, role in ((1, "clerk", "clerk"), (2, "wh", "warehouse"),
                                (3, "appr", "approver"), (4, "acct", "accountant")):
            conn.execute(
                "INSERT INTO users (id, company_id, username, role) VALUES (?, 1, ?, ?)",
                (uid, name, role),
            )
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


def _posted_invoice(db: ERPDatabase, invoice_date: str) -> int:
    po = create_purchase_order(
        db, company_id=1, user_id=1, supplier_id=100, po_number="PO-PERIOD",
        lines=[{"product_id": 50, "description": "Bobine", "qty_ordered": "10",
                "unit_price_dzd": "1000.00", "tax_rate": "19.0"}],
    )
    line_id = po["po_line_ids"][0]
    receive_goods_against_po(
        db, 1, 2, po["po_id"], "GRN-PERIOD",
        [{"po_line_id": line_id, "qty_done": "10"}], idempotency_key="g-period",
    )
    inv = create_and_validate_supplier_invoice(
        db, company_id=1, user_id=1, supplier_id=100, supplier_invoice_no="FAC-PERIOD",
        invoice_date=invoice_date, po_id=po["po_id"],
        amount_ht_dzd="10000.00", amount_tva_dzd="1900.00", amount_ttc_dzd="11900.00",
        lines=[{"po_line_id": line_id, "product_id": 50, "description": "Bobine",
                "qty": "10", "unit_price_dzd": "1000.000000", "tax_rate": "19.0",
                "line_ht_dzd": "10000.00"}],
    )
    assert inv["workflow_state"] == "Validated", inv["validation_errors"]
    submit_invoice_for_approval(db, 1, 1, inv["invoice_id"])
    decide_invoice_approval(db, 1, 3, inv["invoice_id"], approve=True)
    post_supplier_invoice(db, 1, 4, inv["invoice_id"], "post-period")
    return inv["invoice_id"]


def _reverse(db: ERPDatabase, invoice_id: int, key: str) -> dict:
    requested = create_reversal_request(db, 1, 4, invoice_id, "correction", key)
    note_id = requested["credit_note_invoice_id"]
    submit_reversal_for_approval(db, 1, 4, note_id)
    decide_reversal_approval(db, 1, 3, note_id, approve=True)
    return post_reversal(db, 1, 4, note_id, f"post-{key}")


def _reversal_records(db: ERPDatabase, invoice_id: int) -> tuple[dict, dict]:
    with db.transaction() as conn:
        entry = conn.execute(
            "SELECT entry_number, entry_date FROM journal_entries"
            " WHERE source_model = 'supplier_invoices_reversal' AND source_id = ?",
            (str(invoice_id),),
        ).fetchone()
        note = conn.execute(
            "SELECT invoice_date, fiscal_year FROM supplier_invoices"
            " WHERE reversal_of_invoice_id = ?",
            (invoice_id,),
        ).fetchone()
    return dict(entry), dict(note)


def test_fr5_1_a_prior_year_invoice_reversed_now_numbers_the_entry_for_its_own_date(
    tmp_path: Path,
) -> None:
    """The prior-year case that used to produce ``RAVOIR/2025/…`` dated 2026."""
    db = _db(tmp_path)
    invoice_id = _posted_invoice(db, "2025-06-15")
    _reverse(db, invoice_id, "rev-prior-year")

    entry, note = _reversal_records(db, invoice_id)
    number_year = int(entry["entry_number"].split("/")[1])
    date_year = int(entry["entry_date"][:4])
    assert number_year == date_year, (entry, "entry number year must match its own date")
    assert note["fiscal_year"] == int(note["invoice_date"][:4]), (
        note,
        "a credit note's fiscal_year must match the year of its own invoice_date",
    )
    # The original invoice is untouched: still 2025, still ACH/2025/00001.
    with db.transaction() as conn:
        original = conn.execute(
            "SELECT fiscal_year, invoice_date FROM supplier_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
    assert original["fiscal_year"] == 2025
    assert original["invoice_date"] == "2025-06-15"


def test_fr5_1_the_number_year_follows_the_issuing_year_not_the_original(
    tmp_path: Path,
) -> None:
    """A same-period reversal keeps the current-year number; nothing regressed."""
    db = _db(tmp_path)
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    invoice_id = _posted_invoice(db, today)
    _reverse(db, invoice_id, "rev-same-year")

    entry, note = _reversal_records(db, invoice_id)
    assert entry["entry_number"] == f"RAVOIR/{today[:4]}/{invoice_id:05d}"
    assert entry["entry_date"] == today
    assert note["fiscal_year"] == int(today[:4])
    assert note["invoice_date"] == today


def test_fr5_1_the_request_path_writes_the_same_consistent_draft(tmp_path: Path) -> None:
    """The DRAFT entry created by the request must carry the same invariant.

    The reversal request inserts its own DRAFT journal entry before any
    approval happens; both writers derived the year from the original invoice,
    so both are covered here rather than only the posting path.
    """
    db = _db(tmp_path)
    invoice_id = _posted_invoice(db, "2025-06-15")
    requested = create_reversal_request(db, 1, 4, invoice_id, "correction", "rev-draft")

    with db.transaction() as conn:
        draft = conn.execute(
            "SELECT entry_number, entry_date FROM journal_entries WHERE id = ?",
            (requested["reversal_journal_entry_id"],),
        ).fetchone()
    assert int(draft["entry_number"].split("/")[1]) == int(draft["entry_date"][:4]), dict(draft)
