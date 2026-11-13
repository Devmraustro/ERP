# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 35 / 36 — behavioural tests for the integrity gates integrated from PR #7.

These assert what each gate *does*, not that it exists. Each test builds a
disposable database in a temp directory, so nothing here touches a real
deployment, and each one is written to fail if the gate is removed.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

from dz_erp.database import ERPDatabase
from dz_erp.desktop.service import bootstrap_first_run

import dz_erp.procurement_stock as ps


@pytest.fixture()
def env():
    """Isolated company + supplier + admin user in a throwaway database."""
    tmp = tempfile.mkdtemp(prefix="p35_gates_")
    path = os.path.join(tmp, "erp.db")
    info = bootstrap_first_run(
        path, company_name="Gates SARL", admin_username="admin", admin_password="Str0ng#Pass1"
    )
    cid = info["company_id"]
    db = ERPDatabase(path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO partners (company_id, name, partner_type) VALUES (?,?,?)",
            (cid, "Supplier One", "supplier"),
        )
        cur = conn.execute("SELECT id FROM partners WHERE company_id=? LIMIT 1", (cid,))
        sid = cur.fetchone()[0]
        cur = conn.execute("SELECT id FROM users WHERE company_id=? LIMIT 1", (cid,))
        uid = cur.fetchone()[0]
    return {"db": db, "cid": cid, "sid": sid, "uid": uid, "path": path}


# --------------------------------------------------------------------------
# _validate_invoice_totals — header/timbre/TTC reconciliation
# --------------------------------------------------------------------------


def test_totals_accept_a_coherent_bank_transfer_invoice():
    errs = ps._validate_invoice_totals(
        ht_dec=Decimal("100.00"),
        tva_dec=Decimal("19.00"),
        timbre_dec=Decimal("0.00"),
        ttc_dec=Decimal("119.00"),
        payment_method="BANK_TRANSFER",
        sum_line_ht=Decimal("100.00"),
        sum_line_tva=Decimal("19.00"),
    )
    assert errs == []


def test_totals_reject_header_ht_that_disagrees_with_the_lines():
    errs = ps._validate_invoice_totals(
        ht_dec=Decimal("100.00"),
        tva_dec=Decimal("19.00"),
        timbre_dec=Decimal("0.00"),
        ttc_dec=Decimal("119.00"),
        payment_method="BANK_TRANSFER",
        sum_line_ht=Decimal("90.00"),
        sum_line_tva=Decimal("19.00"),
    )
    assert any(e.startswith("HEADER_HT_MISMATCH") for e in errs)


def test_totals_reject_negative_header_amounts():
    errs = ps._validate_invoice_totals(
        ht_dec=Decimal("-1.00"),
        tva_dec=Decimal("0.00"),
        timbre_dec=Decimal("0.00"),
        ttc_dec=Decimal("-1.00"),
        payment_method="BANK_TRANSFER",
        sum_line_ht=Decimal("-1.00"),
        sum_line_tva=Decimal("0.00"),
    )
    assert any(e.startswith("HEADER_AMOUNT_NEGATIVE") for e in errs)


def test_totals_reject_a_wrong_stamp_duty_for_cash():
    """Cash above the threshold attracts stamp duty; declaring zero must fail."""
    errs = ps._validate_invoice_totals(
        ht_dec=Decimal("100000.00"),
        tva_dec=Decimal("19000.00"),
        timbre_dec=Decimal("0.00"),
        ttc_dec=Decimal("119000.00"),
        payment_method="CASH",
        sum_line_ht=Decimal("100000.00"),
        sum_line_tva=Decimal("19000.00"),
    )
    assert any(e.startswith("TIMBRE_MISMATCH") for e in errs)


def test_totals_reject_ttc_that_is_not_ht_plus_tva_plus_timbre():
    errs = ps._validate_invoice_totals(
        ht_dec=Decimal("100.00"),
        tva_dec=Decimal("19.00"),
        timbre_dec=Decimal("0.00"),
        ttc_dec=Decimal("120.00"),
        payment_method="BANK_TRANSFER",
        sum_line_ht=Decimal("100.00"),
        sum_line_tva=Decimal("19.00"),
    )
    assert any(e.startswith("TOTAL_TTC_MISMATCH") for e in errs)


# --------------------------------------------------------------------------
# _duplicate_invoice_errors — duplicate detection
# --------------------------------------------------------------------------


def test_duplicate_detection_flags_a_repeated_reference_and_amount(env):
    db, cid, sid = env["db"], env["cid"], env["sid"]
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO supplier_invoices
               (company_id, supplier_id, supplier_invoice_no, normalized_invoice_no,
                invoice_date, fiscal_year, amount_ht_dzd, amount_tva_dzd,
                amount_ttc_dzd, amount_timbre_dzd, payment_method, workflow_state,
                payload_sha256, created_by_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, sid, "INV-777", "INV777", "2026-03-01", 2026,
             "100.00", "19.00", "119.00", "0.00", "BANK_TRANSFER", "Posted",
             "a" * 64, env["uid"], "2026-03-01T00:00:00+00:00"),
        )
        errs, is_hard = ps._duplicate_invoice_errors(
            conn, company_id=cid, supplier_id=sid, fiscal_year=2026,
            norm_ref="INV777", ttc_dec=Decimal("119.00"),
        )
    assert errs, "a second invoice with the same reference and amount must be reported"


def test_duplicate_detection_is_quiet_for_a_new_reference(env):
    db, cid, sid = env["db"], env["cid"], env["sid"]
    with db.transaction() as conn:
        errs, _is_hard = ps._duplicate_invoice_errors(
            conn, company_id=cid, supplier_id=sid, fiscal_year=2026,
            norm_ref="NEWREF001", ttc_dec=Decimal("250.00"),
        )
    assert errs == []


# --------------------------------------------------------------------------
# _invalidate_approval_requests — stale approvals must not survive an edit
# --------------------------------------------------------------------------


def test_invalidating_approvals_closes_every_open_request(env):
    db, cid = env["db"], env["cid"]
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO supplier_invoices
               (company_id, supplier_id, supplier_invoice_no, normalized_invoice_no,
                invoice_date, fiscal_year, amount_ht_dzd, amount_tva_dzd,
                amount_ttc_dzd, amount_timbre_dzd, payment_method, workflow_state,
                payload_sha256, created_by_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, env["sid"], "INV-900", "INV900", "2026-04-01", 2026,
             "10.00", "1.90", "11.90", "0.00", "BANK_TRANSFER", "PendingApproval",
             "b" * 64, env["uid"], "2026-04-01T00:00:00+00:00"),
        )
        inv_id = conn.execute(
            "SELECT id FROM supplier_invoices WHERE supplier_invoice_no='INV-900'"
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO approval_requests
               (company_id, target_model, target_id, payload_sha256,
                requested_by_id, requested_at, decision)
               VALUES (?,?,?,?,?,?,?)""",
            (cid, "supplier_invoices", inv_id, "c" * 64,
             env["uid"], "2026-04-01T00:00:00+00:00", "PENDING"),
        )
        ps._invalidate_approval_requests(
            conn, cid, inv_id,
            reason="invoice edited after approval was requested",
            actor_user_id=env["uid"],
            now_iso="2026-04-02T00:00:00+00:00",
        )
        rows = conn.execute(
            "SELECT decision FROM approval_requests WHERE company_id=? AND target_id=?",
            (cid, inv_id),
        ).fetchall()
    assert rows, "the approval request must still exist, merely closed"
    assert all(r[0] == "INVALIDATED" for r in rows), "no request may stay open after invalidation"


# --------------------------------------------------------------------------
# _record_refused_attempt — refusals are audit evidence
# --------------------------------------------------------------------------


def test_a_refused_attempt_is_written_to_the_audit_log(env):
    db, cid = env["db"], env["cid"]
    refusal = ps._RefusedAttempt(
        "posting refused for test",
        action_type="SUPPLIER_INVOICE_POST_REFUSED",
        target_model="supplier_invoice",
        target_id="12345",
        after_state={"reason": "test"},
    )
    ps._record_refused_attempt(db, cid, env["uid"], refusal)

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT action_type FROM audit_events WHERE company_id=? AND action_type=?",
            (cid, "SUPPLIER_INVOICE_POST_REFUSED"),
        ).fetchall()
    assert rows, "a refusal must leave an audit event behind"


# --------------------------------------------------------------------------
# The three validators stay enforced (they were already inline; prove it)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_date", ["2026-99-99", "2026-13-45", "", "not-a-date", "2026-02-30"])
def test_invalid_invoice_dates_are_refused(env, bad_date):
    with pytest.raises(ValueError):
        ps.create_and_validate_supplier_invoice(
            env["db"], company_id=env["cid"], user_id=env["uid"], supplier_id=env["sid"],
            supplier_invoice_no="INV-D1", invoice_date=bad_date,
            amount_ht_dzd="100.00", amount_tva_dzd="19.00", amount_ttc_dzd="119.00",
            lines=[{"qty": "1", "unit_price_dzd": "100.00", "description": "x", "vat_rate": "19"}],
        )


def test_an_oversized_supplier_reference_is_refused(env):
    with pytest.raises(ValueError, match="supplier_invoice_no exceeds"):
        ps.create_and_validate_supplier_invoice(
            env["db"], company_id=env["cid"], user_id=env["uid"], supplier_id=env["sid"],
            supplier_invoice_no="A" * 200, invoice_date="2026-05-01",
            amount_ht_dzd="100.00", amount_tva_dzd="19.00", amount_ttc_dzd="119.00",
            lines=[{"qty": "1", "unit_price_dzd": "100.00", "description": "x", "vat_rate": "19"}],
        )


def test_an_unsupported_payment_method_is_refused(env):
    with pytest.raises(ValueError, match="not supported"):
        ps.create_and_validate_supplier_invoice(
            env["db"], company_id=env["cid"], user_id=env["uid"], supplier_id=env["sid"],
            supplier_invoice_no="INV-D2", invoice_date="2026-05-01",
            payment_method="CRYPTO",
            amount_ht_dzd="100.00", amount_tva_dzd="19.00", amount_ttc_dzd="119.00",
            lines=[{"qty": "1", "unit_price_dzd": "100.00", "description": "x", "vat_rate": "19"}],
        )


# --------------------------------------------------------------------------
# Phase 38 — the totals gate is on the live write path, not just callable
# --------------------------------------------------------------------------


def test_a_negative_invoice_cannot_reach_validated(env):
    """Before Phase 38 this reached ``Validated`` with an empty error list.

    A request declaring HT=-100, TVA=-19, TTC=-119 reconciles perfectly against
    its own negative lines, so every arithmetic check passed and a negative
    payable was written. A credit note is a positive-amount reversal document,
    so a negative header can only be a malformed payload.
    """
    with env["db"].transaction() as conn:
        conn.execute(
            "UPDATE partners SET tax_regime='IFU' WHERE company_id=? AND id=?",
            (env["cid"], env["sid"]),
        )
    invoice = ps.create_and_validate_supplier_invoice(
        env["db"], company_id=env["cid"], user_id=env["uid"], supplier_id=env["sid"],
        supplier_invoice_no="NEG-1", invoice_date="2026-11-02",
        payment_method="BANK_TRANSFER",
        amount_ht_dzd="-100.00", amount_tva_dzd="-19.00", amount_ttc_dzd="-119.00",
        lines=[{"qty": "1", "unit_price_dzd": "-100.00",
                "description": "x", "tax_rate": "19.0"}],
    )
    assert invoice["workflow_state"] != "Validated", (
        "a negative-amount invoice must never be accepted as validated"
    )
    errors = invoice.get("validation_errors") or []
    assert any("HEADER_AMOUNT_NEGATIVE" in str(e) for e in errors), errors


def test_a_coherent_positive_invoice_still_validates(env):
    """The new gate must not reject the ordinary case."""
    with env["db"].transaction() as conn:
        conn.execute(
            "UPDATE partners SET tax_regime='IFU' WHERE company_id=? AND id=?",
            (env["cid"], env["sid"]),
        )
    invoice = ps.create_and_validate_supplier_invoice(
        env["db"], company_id=env["cid"], user_id=env["uid"], supplier_id=env["sid"],
        supplier_invoice_no="POS-1", invoice_date="2026-11-02",
        payment_method="BANK_TRANSFER",
        amount_ht_dzd="100.00", amount_tva_dzd="19.00", amount_ttc_dzd="119.00",
        lines=[{"qty": "1", "unit_price_dzd": "100.00",
                "description": "x", "tax_rate": "19.0"}],
    )
    errors = invoice.get("validation_errors") or []
    assert not any("HEADER_AMOUNT_NEGATIVE" in str(e) for e in errors), errors
