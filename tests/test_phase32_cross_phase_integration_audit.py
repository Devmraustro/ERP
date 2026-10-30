# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 32 — independent cross-phase integration audit (Phases 29 + 30 + 31).

This module is an *audit artefact*, not a feature suite. It is written against
the verified Phase 31 head (``e4e47b3b``) and treats the three reviewed line
elements as one integrated gate:

* Phase 29 — fail-closed field-level review (``ocr_field_reviews``) enforced at
  the invoice submit/approval boundary.
* Phase 30 — the reviewed line *quantity* (``line_item_json``) and the source
  totals compared with the invoice payload.
* Phase 31 — the reviewed line *unit price* (``line_unit_price``) and the
  reviewed line *discount* (``line_discount``), with the line net re-derived
  from the decided values and compared with ``supplier_invoice_lines``.

Every test states the workflow transition it audits, and every refusal test
proves *both* halves of the contract: the workflow is refused **and** the
ledger/invoice state is unchanged. Green unit suites per phase do not establish
that the composition is correct, so the matrix below crosses the phases:

A  fully reviewed invoice with matching source evidence posts a balanced ledger
B  quantity corrected after the invoice was created           -> refused
C  price corrected after the invoice was created              -> refused
D  discount transcribed after the invoice was created         -> refused
E  invoice line modified after submission                     -> approval refused
F  source field edited after its decision                     -> stale, refused
G  missing / malformed source price evidence                  -> decided/refused
H  reordered and duplicate lines                              -> flagged, refused
I  discount range and canonical rounding boundaries           -> refused/allowed
J  concurrent submission and approval attempts                -> single winner
K  cross-company decision, verdict read and invoice creation  -> refused+audited
L  posting retries, duplicate posting and post-approval edits  -> refused
M  legacy document-less (manual) invoices                     -> unchanged
N  trial balance and reversal after a fully reviewed posting  -> balanced
O  refusal rollback: no state change, refusal audited
P  approval-time re-evaluation after an out-of-band line edit -> refused
Q  backup/restore after financial posting (version-gated)
"""

import json
import sqlite3
import sys
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import (
    ERPDatabase,
    FinancialIntegrityError,
    TenantIsolationError,
)
from dz_erp.intake_ocr import (
    PrivateDocumentVault,
    decide_extracted_field_review,
    update_extracted_field_by_human,
    upload_and_process_supplier_document,
)
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    create_reversal_request,
    decide_invoice_approval,
    decide_reversal_approval,
    modify_draft_invoice_line,
    post_reversal,
    post_supplier_invoice,
    submit_invoice_for_approval,
    submit_reversal_for_approval,
)
from dz_erp.review_policy import evaluate_document_review_policy

_USERS = [
    (1, 1, "admin_a", "admin", "AdminA#2026x"),
    (2, 1, "clerk_a", "clerk", "Clerk#2026x"),
    (3, 1, "approver_a", "approver", "Appr#2026x"),
    (4, 1, "acct_a", "accountant", "Acct#2026x"),
    (5, 1, "wh_a", "warehouse", "Wh#2026x"),
    (20, 2, "admin_b", "admin", "AdminB#2026x"),
    (21, 2, "clerk_b", "clerk", "ClerkB#2026x"),
]

_ONE_LINE_SPEC = {
    "supplier_name": "SPARTE SARL",
    "invoice_number": "FAC-2026-0001",
    "invoice_date": "2026-02-14",
    "supplier_nif": "000216001234567",
    "supplier_rc": "16/00-0991216B12",
    "supplier_ai": "16019912316",
    "payment_method": "BANK_TRANSFER",
    "amount_ht_dzd": "1000.00",
    "amount_tva_dzd": "190.00",
    "amount_timbre_dzd": "0.00",
    "amount_ttc_dzd": "1190.00",
    "lines": [
        {
            "sku": "SKU-1",
            "description": "Widgets",
            "qty": "2",
            "unit_price_dzd": "500.00",
            "tax_rate": "19",
            "line_ht_dzd": "1000.00",
        }
    ],
}

_TWO_LINE_SPEC = {
    "supplier_name": "SPARTE SARL",
    "invoice_number": "FAC-2026-0001",
    "invoice_date": "2026-02-14",
    "supplier_nif": "000216001234567",
    "supplier_rc": "16/00-0991216B12",
    "supplier_ai": "16019912316",
    "payment_method": "BANK_TRANSFER",
    "amount_ht_dzd": "1000.00",
    "amount_tva_dzd": "190.00",
    "amount_timbre_dzd": "0.00",
    "amount_ttc_dzd": "1190.00",
    "lines": [
        {
            "sku": "SKU-1",
            "description": "Anchors",
            "qty": "1",
            "unit_price_dzd": "400.00",
            "tax_rate": "19",
            "line_ht_dzd": "400.00",
        },
        {
            "sku": "SKU-2",
            "description": "Bolts",
            "qty": "1",
            "unit_price_dzd": "600.00",
            "tax_rate": "19",
            "line_ht_dzd": "600.00",
        },
    ],
}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def _make_env(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "p32.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, cid, username, role, password in _USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (uid, cid, username, role, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc) VALUES (7, 1, 'SPARTE', 'supplier', 'DZ', 'REEL',"
            " '000216001234567', '000216001234567', '16019912316', '16/00-0991216B12')"
        )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc) VALUES (8, 2, 'SPARTE B', 'supplier', 'DZ', 'REEL',"
            " '000216001234567', '000216001234567', '16019912316', '16/00-0991216B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)
    vault = PrivateDocumentVault(tmp_path / "vault")
    return db, vault


@pytest.fixture
def env(tmp_path):
    db, vault = _make_env(tmp_path)
    return db, vault, TestClient(create_erp_fastapi_app(db, vault))


def _upload(db, vault, company_id=1, user_id=2, spec=None):
    return upload_and_process_supplier_document(
        db,
        vault,
        company_id=company_id,
        user_id=user_id,
        filename="facture.pdf",
        mime_type="application/pdf",
        content=render_synthetic_invoice_pdf(spec or dict(_ONE_LINE_SPEC)),
    )


def _evaluate(db, company_id, document_id):
    with db.transaction() as conn:
        doc = dict(conn.execute("SELECT * FROM source_documents WHERE id = ?", (document_id,)).fetchone())
        fields = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ?",
                (company_id, document_id),
            ).fetchall()
        ]
        decisions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM ocr_field_reviews WHERE company_id = ? AND document_id = ?",
                (company_id, document_id),
            ).fetchall()
        ]
    return evaluate_document_review_policy(doc, fields, decisions)


def _review_all(db, company_id, document_id, user_id=2, *, discount="0.00"):
    """Complete the full Phase 29/30/31 review: header, quantity, price, discount."""
    ev = _evaluate(db, company_id, document_id)
    for fev in ev["fields"]:
        if fev["status"] == "missing":
            if fev["required"]:
                raise AssertionError(f"fixture lacks required field {fev['field_name']}")
            decide_extracted_field_review(
                db,
                company_id=company_id,
                user_id=user_id,
                document_id=document_id,
                field_name=fev["field_name"],
                decision="NOT_PRESENT",
            )
            continue
        decide_extracted_field_review(
            db,
            company_id=company_id,
            user_id=user_id,
            document_id=document_id,
            field_name=fev["field_name"],
            decision="CONFIRMED",
            expected_value=fev["value"],
        )
    for lev in ev["lines"]:
        decide_extracted_field_review(
            db,
            company_id=company_id,
            user_id=user_id,
            document_id=document_id,
            field_name="line_item_json",
            decision="CONFIRMED",
            expected_value=lev["value"],
            line_index=lev["line_index"],
        )
    for pev in ev["line_prices"]:
        decide_extracted_field_review(
            db,
            company_id=company_id,
            user_id=user_id,
            document_id=document_id,
            field_name="line_unit_price",
            decision="CONFIRMED",
            expected_value=pev["value"],
            line_index=pev["line_index"],
        )
    for dev in ev["line_discounts"]:
        if dev["value"] is not None:
            decide_extracted_field_review(
                db,
                company_id=company_id,
                user_id=user_id,
                document_id=document_id,
                field_name="line_discount",
                decision="CONFIRMED",
                expected_value=dev["value"],
                line_index=dev["line_index"],
            )
        else:
            decide_extracted_field_review(
                db,
                company_id=company_id,
                user_id=user_id,
                document_id=document_id,
                field_name="line_discount",
                decision="CORRECTED",
                value=discount,
                line_index=dev["line_index"],
            )
    final = _evaluate(db, company_id, document_id)
    assert final["satisfied"], final["blocking"]
    return final


def _invoice_line(qty, price, *, discount="0.00", rate="19", description="Widgets", po_line_id=None):
    net = (Decimal(qty) * Decimal(price) * ((Decimal("100.00") - Decimal(discount)) / Decimal("100.00"))).quantize(
        Decimal("0.01")
    )
    tva = (net * (Decimal(rate) / Decimal("100.00"))).quantize(Decimal("0.01"))
    return {
        "description": description,
        "qty": qty,
        "unit_price_dzd": price,
        "discount_pct": discount,
        "tax_rate": rate,
        "line_ht_dzd": f"{net:.2f}",
        "line_tva_dzd": f"{tva:.2f}",
        "line_ttc_dzd": f"{(net + tva):.2f}",
        "po_line_id": po_line_id,
    }


def _create_invoice(
    db,
    *,
    document_id=None,
    company_id=1,
    user_id=2,
    supplier_id=7,
    invoice_no="FAC-2026-0001",
    lines=None,
    amount_ht="1000.00",
    amount_tva="190.00",
    amount_timbre="0.00",
    amount_ttc="1190.00",
):
    if lines is None:
        lines = [_invoice_line("2", "500.00")]
    return create_and_validate_supplier_invoice(
        db=db,
        company_id=company_id,
        user_id=user_id,
        supplier_id=supplier_id,
        supplier_invoice_no=invoice_no,
        invoice_date="2026-02-14",
        lines=lines,
        amount_ht_dzd=amount_ht,
        amount_tva_dzd=amount_tva,
        amount_ttc_dzd=amount_ttc,
        amount_timbre_dzd=amount_timbre,
        document_id=document_id,
    )


def _invoice_row(db, invoice_id):
    with db.transaction() as conn:
        return dict(conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (invoice_id,)).fetchone())


def _journal_entries(db, company_id=1):
    with db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM journal_entries WHERE company_id = ? ORDER BY id", (company_id,)
            ).fetchall()
        ]


def _audit_actions(db, company_id=1):
    with db.transaction() as conn:
        return [
            r["action_type"]
            for r in conn.execute(
                "SELECT action_type FROM audit_events WHERE company_id = ? ORDER BY id", (company_id,)
            ).fetchall()
        ]


def _set_line_payload(db, document_id, mutate, *, company_id=1, line_index=0):
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id, normalized_value FROM ocr_extracted_fields WHERE company_id = ?"
            " AND document_id = ? AND field_name = 'line_item_json' AND line_index = ?",
            (company_id, document_id, line_index),
        ).fetchone()
        payload = json.loads(row["normalized_value"])
        mutate(payload)
        conn.execute(
            "UPDATE ocr_extracted_fields SET normalized_value = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
        return payload


def _reviewed_invoice(db, vault, *, spec=None, user_id=2, invoice_lines=None, invoice_no="FAC-2026-0001"):
    """Upload, fully review, and create the matching invoice. Returns (doc_id, invoice_id)."""
    document_id = _upload(db, vault, user_id=user_id, spec=spec)["document_id"]
    _review_all(db, 1, document_id, user_id=user_id)
    created = _create_invoice(db, document_id=document_id, user_id=user_id, invoice_no=invoice_no, lines=invoice_lines)
    assert created["workflow_state"] == "Validated", created["validation_errors"]
    return document_id, created["invoice_id"]


def _approve_and_post(db, invoice_id, *, submit_user=2, approve_user=3, post_user=4, key="post-1"):
    submit_invoice_for_approval(db, 1, submit_user, invoice_id)
    decide_invoice_approval(db, 1, approve_user, invoice_id, approve=True)
    return post_supplier_invoice(db, 1, post_user, invoice_id, key)


# ---------------------------------------------------------------------------
# A. The integrated happy path
# ---------------------------------------------------------------------------
def test_a_fully_reviewed_invoice_posts_a_balanced_ledger(env):
    """A source document with decided quantity, price and discount posts exactly once."""
    db, vault, _client = env
    document_id, invoice_id = _reviewed_invoice(
        db,
        vault,
        invoice_lines=[
            _invoice_line("2", "500.00", description="Widgets"),
        ],
    )

    posted = _approve_and_post(db, invoice_id)
    assert posted["workflow_state"] == "Posted"

    entries = _journal_entries(db)
    assert len(entries) == 1
    assert entries[0]["total_debit_dzd"] == entries[0]["total_credit_dzd"] == "1190.00"

    report = db.trial_balance(1)
    assert report["total_debit_dzd"] == report["total_credit_dzd"] == "1190.00"
    assert report["balanced"] is True
    assert db.verify_audit_chain(1)["valid"] is True

    inv = _invoice_row(db, invoice_id)
    assert inv["workflow_state"] == "Posted"
    assert inv["approved_payload_sha256"] == inv["payload_sha256"]
    with db.transaction() as conn:
        doc = conn.execute("SELECT workflow_state FROM source_documents WHERE id = ?", (document_id,)).fetchone()
    assert doc["workflow_state"] == "Posted"


# ---------------------------------------------------------------------------
# B/C/D. A reviewed line element changed after the invoice was created
# ---------------------------------------------------------------------------
def test_b_quantity_corrected_after_invoice_creation_blocks_submission(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(db, document_id=document_id)
    assert invoice["workflow_state"] == "Validated"

    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="line_item_json",
        decision="CORRECTED",
        value="3",
        expected_value="2.000000",
        line_index=0,
    )

    with pytest.raises(FinancialIntegrityError, match="QUANTITY_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert _invoice_row(db, invoice["invoice_id"])["workflow_state"] == "Validated"
    assert "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH" in _audit_actions(db)


def test_c_unit_price_corrected_after_invoice_creation_blocks_submission(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(db, document_id=document_id)

    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="line_unit_price",
        decision="CORRECTED",
        value="450.00",
        expected_value="500.000000",
        line_index=0,
    )

    with pytest.raises(FinancialIntegrityError, match="PRICE_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert _invoice_row(db, invoice["invoice_id"])["workflow_state"] == "Validated"


def test_d_discount_transcribed_after_invoice_creation_blocks_submission(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(db, document_id=document_id)

    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="line_discount",
        decision="CORRECTED",
        value="10.00",
        expected_value="0.00",
        line_index=0,
    )

    with pytest.raises(FinancialIntegrityError, match="DISCOUNT_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert _invoice_row(db, invoice["invoice_id"])["workflow_state"] == "Validated"


# ---------------------------------------------------------------------------
# E. Invoice modified after submission, before approval
# ---------------------------------------------------------------------------
def test_e_invoice_line_modified_after_submission_cannot_be_approved(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    assert _invoice_row(db, invoice_id)["workflow_state"] == "PendingApproval"

    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()["id"]
    modified = modify_draft_invoice_line(db, 1, 2, invoice_id, line_id, "560.00")
    assert modified["workflow_state"] == "NeedsReview"
    assert modified["approval_invalidated"] is True

    with pytest.raises(FinancialIntegrityError, match="not 'PendingApproval'"):
        decide_invoice_approval(db, 1, 3, invoice_id, approve=True)
    # A modified invoice cannot silently return to Validated: it must be re-entered.
    with pytest.raises(FinancialIntegrityError, match="must be 'Validated'"):
        submit_invoice_for_approval(db, 1, 2, invoice_id)
    assert _invoice_row(db, invoice_id)["workflow_state"] == "NeedsReview"
    assert _journal_entries(db) == []


# ---------------------------------------------------------------------------
# F. Source document edited after its decision (staleness)
# ---------------------------------------------------------------------------
def test_f_source_field_edited_after_decision_stales_and_blocks_approval(env):
    db, vault, _client = env
    document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)

    # The document is closed for decisions while it backs a pending approval...
    with pytest.raises(FinancialIntegrityError, match="FIELD_REVIEW_CLOSED"):
        decide_extracted_field_review(
            db,
            company_id=1,
            user_id=2,
            document_id=document_id,
            field_name="amount_ht_dzd",
            decision="CORRECTED",
            value="1500.00",
        )
    # ...and the legacy human-edit path is closed too.
    with pytest.raises(FinancialIntegrityError, match="FIELD_EDIT_CLOSED"):
        update_extracted_field_by_human(
            db, company_id=1, user_id=2, document_id=document_id,
            field_name="amount_ht_dzd", new_value="1500.00",
        )
    assert _invoice_row(db, invoice_id)["workflow_state"] == "PendingApproval"


def test_f2_stale_decision_blocks_submission_and_recovers_after_redecision(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(db, document_id=document_id)

    # Out-of-band edit of a stored value stales the CONFIRMED decision on it.
    update_extracted_field_by_human(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="invoice_date", new_value="2026-03-01",
    )
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    date_field = next(f for f in ev["fields"] if f["field_name"] == "invoice_date")
    assert "decision_stale" in date_field["flags"]
    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_REVIEW_UNSATISFIED"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert "INVOICE_SUBMIT_BLOCKED_UNREVIEWED" in _audit_actions(db)
    assert _invoice_row(db, invoice["invoice_id"])["workflow_state"] == "Validated"

    # Re-deciding against the new value re-opens the document, but a *decided*
    # value that disagrees with the invoice payload is still refused: the gate
    # compares values, not merely the presence of a decision.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="invoice_date", decision="CORRECTED", value="2026-03-01",
    )
    assert _evaluate(db, 1, document_id)["satisfied"] is True
    with pytest.raises(FinancialIntegrityError, match="INVOICE_DATE_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])

    # Restoring the decided value to what the paper and the invoice both say
    # lets the invoice through: the refusal is value-bound, not permanent.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="invoice_date", decision="CORRECTED", value="2026-02-14",
    )
    assert submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])["workflow_state"] == "PendingApproval"


def test_f3_decided_total_that_breaks_header_arithmetic_keeps_the_document_blocked(env):
    """A re-decided amount that no longer reconciles must not satisfy the policy."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(db, document_id=document_id)

    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="amount_ht_dzd", decision="CORRECTED", value="1500.00",
    )
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    assert ev["totals"]["inconsistent"] is True
    assert any("INCONSISTENT_EXTRACTED_TOTALS" in b for b in ev["blocking"])
    with pytest.raises(FinancialIntegrityError, match="INCONSISTENT_EXTRACTED_TOTALS"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])


# ---------------------------------------------------------------------------
# G. Missing / malformed source evidence
# ---------------------------------------------------------------------------
def test_g_a_line_without_extracted_price_cannot_be_confirmed(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _set_line_payload(db, document_id, lambda p: p.pop("unit_price_dzd", None))

    ev = _evaluate(db, 1, document_id)
    price = ev["line_prices"][0]
    assert price["status"] == "missing"
    with pytest.raises(FinancialIntegrityError, match="FIELD_VALUE_CHANGED_SINCE_READ|FIELD_NOT_EXTRACTED"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=document_id,
            field_name="line_unit_price", decision="CONFIRMED",
            expected_value="500.000000", line_index=0,
        )
    assert any("line_unit_price" in b for b in ev["blocking"])

    # The honest repair is an attributed transcription, which binds the price.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="line_unit_price", decision="CORRECTED",
        value="500.00", line_index=0,
    )
    ev = _evaluate(db, 1, document_id)
    assert ev["line_prices"][0]["value"] == "500.000000"


def test_g2_malformed_line_payload_is_refused_not_guessed(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM ocr_extracted_fields WHERE company_id = 1 AND document_id = ?"
            " AND field_name = 'line_item_json' AND line_index = 0",
            (document_id,),
        ).fetchone()
        conn.execute(
            "UPDATE ocr_extracted_fields SET normalized_value = '{not json' WHERE id = ?", (row["id"],)
        )
    with pytest.raises(FinancialIntegrityError, match="LINE_PAYLOAD_UNPARSEABLE"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=document_id,
            field_name="line_item_json", decision="CONFIRMED",
            expected_value="2", line_index=0,
        )
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False


# ---------------------------------------------------------------------------
# H. Reordered and duplicate lines
# ---------------------------------------------------------------------------
def test_h_swapped_line_order_is_flagged_never_rematched(env):
    db, vault, _client = env
    document_id = _upload(db, vault, spec=dict(_TWO_LINE_SPEC))["document_id"]
    _review_all(db, 1, document_id)
    # Same two lines, entered in the paper's reverse order.
    invoice = _create_invoice(
        db,
        document_id=document_id,
        lines=[
            _invoice_line("1", "600.00", description="Bolts"),
            _invoice_line("1", "400.00", description="Anchors"),
        ],
    )
    assert invoice["workflow_state"] == "Validated"  # arithmetic is internally consistent
    with pytest.raises(FinancialIntegrityError, match="LINE_1_(QUANTITY|PRICE)_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])


_DUP_LINE_SPEC = dict(_ONE_LINE_SPEC)
_DUP_LINE_SPEC["lines"] = [
    {"sku": "SKU-1", "description": "Widgets", "qty": "1", "unit_price_dzd": "500.00",
     "tax_rate": "19", "line_ht_dzd": "500.00"},
    {"sku": "SKU-1", "description": "Widgets", "qty": "1", "unit_price_dzd": "500.00",
     "tax_rate": "19", "line_ht_dzd": "500.00"},
]


def test_h2_duplicate_identical_source_lines_are_compared_positionally(env):
    """Two identical source rows are two rows: the invoice must carry both."""
    db, vault, _client = env
    document_id = _upload(db, vault, spec=dict(_DUP_LINE_SPEC))["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(
        db,
        document_id=document_id,
        lines=[_invoice_line("1", "500.00"), _invoice_line("1", "500.00")],
    )
    assert invoice["workflow_state"] == "Validated", invoice["validation_errors"]
    assert submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])["workflow_state"] == "PendingApproval"


def test_h3_line_count_shortfall_against_source_is_refused(env):
    """Collapsing two reviewed source rows into one invoice line is refused."""
    db, vault, _client = env
    document_id = _upload(db, vault, spec=dict(_DUP_LINE_SPEC))["document_id"]
    _review_all(db, 1, document_id)
    invoice = _create_invoice(
        db,
        document_id=document_id,
        lines=[_invoice_line("1", "500.00")],
        amount_ht="500.00",
        amount_tva="95.00",
        amount_ttc="595.00",
    )
    assert invoice["workflow_state"] == "Validated"
    with pytest.raises(FinancialIntegrityError, match="LINE_COUNT_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert _invoice_row(db, invoice["invoice_id"])["workflow_state"] == "Validated"


# ---------------------------------------------------------------------------
# I. Discount range and canonical rounding
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,error",
    [
        ("100.01", "FIELD_VALUE_DISCOUNT_OUT_OF_RANGE"),
        ("150", "FIELD_VALUE_DISCOUNT_OUT_OF_RANGE"),
        ("-1", "FIELD_VALUE_NEGATIVE_AMOUNT"),
    ],
)
def test_i_discount_out_of_range_is_refused_never_clamped(env, value, error):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    ev = _evaluate(db, 1, document_id)
    # Discount for the line is missing until transcribed; the range rule applies there.
    assert ev["line_discounts"][0]["value"] is None
    with pytest.raises(ValueError, match=error):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=document_id,
            field_name="line_discount", decision="CORRECTED", value=value, line_index=0,
        )
    assert _evaluate(db, 1, document_id)["line_discounts"][0]["value"] is None


def test_i2_discount_of_100_is_accepted_and_derives_a_zero_net(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id, discount="100.00")

    with db.transaction() as conn:
        payload = json.loads(
            conn.execute(
                "SELECT normalized_value FROM ocr_extracted_fields WHERE company_id = 1"
                " AND document_id = ? AND field_name = 'line_item_json' AND line_index = 0",
                (document_id,),
            ).fetchone()["normalized_value"]
        )
    assert payload["discount_pct"] == "100.00"
    assert payload["line_ht_dzd"] == "0.00"  # the net follows the decided 100 % discount

    # A 100 % discount makes the line net zero while the document's printed
    # header still says 1000.00 HT: the review policy reports the document
    # "satisfied" (its own header arithmetic is consistent) but no invoice can
    # satisfy both the decided line net and the decided header. The gate
    # refuses, so the contradiction is fail-closed and cannot be posted.
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is True
    assert ev["totals"]["inconsistent"] is False
    invoice = _create_invoice(db, document_id=document_id, lines=[_invoice_line("2", "500.00")])
    assert invoice["workflow_state"] == "Validated"
    with pytest.raises(FinancialIntegrityError, match="LINE_1_LINE_HT_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])


def test_i3_invoice_side_out_of_range_discount_is_unsubmittable(env):
    db, vault, _client = env
    _document_id, _invoice_id = _reviewed_invoice(db, vault)
    bad = _create_invoice(
        db,
        invoice_no="FAC-2026-0002",
        lines=[_invoice_line("2", "500.00", discount="100.50")],
        amount_ht="0.00",
        amount_tva="0.00",
        amount_ttc="0.00",
    )
    assert bad["workflow_state"] == "NeedsReview"
    assert any("DISCOUNT_OUT_OF_RANGE" in e for e in bad["validation_errors"])
    with pytest.raises(FinancialIntegrityError, match="must be 'Validated'"):
        submit_invoice_for_approval(db, 1, 2, bad["invoice_id"])


def test_i4_raw_more_precise_invoice_discount_is_refused_at_the_gate(env):
    """Documented rounding policy: the decided 2-decimal discount is authoritative."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id, discount="33.33")
    # Invoice validated against a raw 33.335 % discount: stored 33.33 with a net
    # derived from 33.335, i.e. the invoice's own columns disagree by a centime.
    net = (Decimal("2") * Decimal("500.00") * ((Decimal("100") - Decimal("33.335")) / Decimal("100"))).quantize(
        Decimal("0.01")
    )
    tva = (net * Decimal("0.19")).quantize(Decimal("0.01"))
    raw_line = {
        "description": "Widgets",
        "qty": "2",
        "unit_price_dzd": "500.00",
        "discount_pct": "33.335",
        "tax_rate": "19",
        "line_ht_dzd": f"{net:.2f}",
    }
    invoice = _create_invoice(
        db,
        document_id=document_id,
        lines=[raw_line],
        amount_ht=f"{net:.2f}",
        amount_tva=f"{tva:.2f}",
        amount_ttc=f"{(net + tva):.2f}",
    )
    assert invoice["workflow_state"] == "Validated"
    with pytest.raises(FinancialIntegrityError, match="LINE_1_LINE_HT_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])


# ---------------------------------------------------------------------------
# J. Concurrency
# ---------------------------------------------------------------------------
def test_j_concurrent_submissions_produce_one_approval_request(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)

    results = []
    barrier = threading.Barrier(2)

    def _submit():
        barrier.wait()
        try:
            submit_invoice_for_approval(db, 1, 2, invoice_id)
            results.append("ok")
        except (FinancialIntegrityError, sqlite3.OperationalError) as exc:
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=_submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1, results
    assert _invoice_row(db, invoice_id)["workflow_state"] == "PendingApproval"
    with db.transaction() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE target_id = ? AND decision = 'PENDING'",
            (invoice_id,),
        ).fetchone()["n"]
    assert pending == 1


def test_j2_concurrent_approvals_record_exactly_one_decision(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)

    results = []
    barrier = threading.Barrier(2)

    def _approve():
        barrier.wait()
        try:
            decide_invoice_approval(db, 1, 3, invoice_id, approve=True)
            results.append("ok")
        except (FinancialIntegrityError, sqlite3.OperationalError) as exc:
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=_approve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1, results
    assert _invoice_row(db, invoice_id)["workflow_state"] == "Approved"
    with db.transaction() as conn:
        decided = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE target_id = ? AND decision = 'APPROVED'",
            (invoice_id,),
        ).fetchone()["n"]
    assert decided == 1


# ---------------------------------------------------------------------------
# K. Cross-tenant boundaries
# ---------------------------------------------------------------------------
def test_k_cross_company_decision_and_verdict_read_are_refused_and_audited(env):
    db, vault, client = env
    document_id = _upload(db, vault)["document_id"]

    # Company 2's accountant cannot even see the row: the tenant scope is
    # applied in the lookup, so the refusal is a 404-shaped ValueError rather
    # than a distinguishable tenant error (no cross-tenant existence oracle).
    with pytest.raises(ValueError):
        decide_extracted_field_review(
            db, company_id=2, user_id=21, document_id=document_id,
            field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
        )
    # Tenant scoping is applied before the row is even read: company 2 sees nothing.
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM ocr_field_reviews WHERE document_id = ?", (document_id,)
            ).fetchone()["n"]
            == 0
        )

    token = client.post(
        "/api/v1/auth/token", json={"username": "clerk_b", "password": "ClerkB#2026x"}
    ).json()["access_token"]
    resp = client.get(
        f"/api/v1/documents/{document_id}/review-policy",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text
    assert "SECURITY_CROSS_COMPANY_READ_BLOCKED" in _audit_actions(db, company_id=1)

    with pytest.raises(TenantIsolationError):
        _create_invoice(db, company_id=2, user_id=21, supplier_id=7)


def test_k2_cross_company_decide_endpoint_is_refused_over_http(env):
    db, vault, client = env
    document_id = _upload(db, vault)["document_id"]
    token = client.post(
        "/api/v1/auth/token", json={"username": "clerk_b", "password": "ClerkB#2026x"}
    ).json()["access_token"]
    resp = client.post(
        f"/api/v1/documents/{document_id}/fields/decide",
        json={"field_name": "invoice_number", "decision": "CONFIRMED", "expected_value": "FAC-2026-0001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (403, 404), resp.text
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM ocr_field_reviews WHERE document_id = ?", (document_id,)
            ).fetchone()["n"]
            == 0
        )


# ---------------------------------------------------------------------------
# L. Posting idempotency and post-approval mutation
# ---------------------------------------------------------------------------
def test_l_posting_is_idempotent_and_duplicate_posting_refused(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    decide_invoice_approval(db, 1, 3, invoice_id, approve=True)

    first = post_supplier_invoice(db, 1, 4, invoice_id, "key-1")
    replay = post_supplier_invoice(db, 1, 4, invoice_id, "key-1")
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["journal_entry_id"] == first["journal_entry_id"]
    assert len(_journal_entries(db)) == 1

    with pytest.raises(FinancialIntegrityError, match="already Posted"):
        post_supplier_invoice(db, 1, 4, invoice_id, "key-2")
    # The idempotency key is bound to its request payload, so it cannot be
    # replayed against a different invoice.
    second_spec = dict(_ONE_LINE_SPEC)
    second_spec["invoice_number"] = "FAC-2026-7788"
    _document_id2, invoice_id2 = _reviewed_invoice(
        db, vault, spec=second_spec, invoice_no="FAC-2026-7788"
    )
    submit_invoice_for_approval(db, 1, 2, invoice_id2)
    decide_invoice_approval(db, 1, 3, invoice_id2, approve=True)
    with pytest.raises(FinancialIntegrityError, match="IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"):
        post_supplier_invoice(db, 1, 4, invoice_id2, "key-1")
    assert len(_journal_entries(db)) == 1


def test_l2_post_approval_payload_mutation_is_refused(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    decide_invoice_approval(db, 1, 3, invoice_id, approve=True)

    # Out-of-band mutation of the approved payload (no API path does this: the
    # point is that posting must still refuse it).
    # Simulates any post-approval re-derivation of the payload digest.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET payload_sha256 = ? WHERE id = ?",
            ("0" * 64, invoice_id),
        )
    with pytest.raises(FinancialIntegrityError, match="APPROVAL_PAYLOAD_HASH_MISMATCH"):
        post_supplier_invoice(db, 1, 4, invoice_id, "key-x")
    assert _journal_entries(db) == []


# ---------------------------------------------------------------------------
# M. Legacy document-less invoices
# ---------------------------------------------------------------------------
def test_m_documentless_invoice_keeps_working_without_a_review_gate(env):
    db, vault, _client = env
    invoice = _create_invoice(db, document_id=None)
    assert invoice["workflow_state"] == "Validated"
    _approve_and_post(db, invoice["invoice_id"])
    assert _invoice_row(db, invoice["invoice_id"])["workflow_state"] == "Posted"
    assert len(_journal_entries(db)) == 1


# ---------------------------------------------------------------------------
# N. Trial balance and reversal
# ---------------------------------------------------------------------------
def test_n_reversal_after_reviewed_posting_keeps_the_ledger_balanced(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _approve_and_post(db, invoice_id)

    requested = create_reversal_request(db, 1, 4, invoice_id, "supplier dispute", "rev-1")
    cn_id = requested["credit_note_invoice_id"]
    submit_reversal_for_approval(db, 1, 4, cn_id)
    decide_reversal_approval(db, 1, 3, cn_id, approve=True)
    posted = post_reversal(db, 1, 4, cn_id, "rev-post-1")
    assert posted["workflow_state"] == "Posted"

    report = db.trial_balance(1)
    assert report["total_debit_dzd"] == report["total_credit_dzd"]
    assert report["balanced"] is True
    with db.transaction() as conn:
        net_401 = conn.execute(
            "SELECT l.debit_dzd, l.credit_dzd FROM journal_entry_lines l"
            " JOIN journal_entries e ON e.id = l.journal_entry_id"
            " WHERE l.company_id = 1 AND l.account_code = '401' AND e.state = 'POSTED'"
        ).fetchall()
    debits = sum((Decimal(r["debit_dzd"]) for r in net_401), Decimal("0.00"))
    credits = sum((Decimal(r["credit_dzd"]) for r in net_401), Decimal("0.00"))
    assert debits == credits  # the payable is fully reversed

    # A second reversal request for the same invoice must not create a second,
    # distinct posted credit note.
    with pytest.raises(Exception):
        second = create_reversal_request(db, 1, 4, invoice_id, "second reversal", "rev-2")
        submit_reversal_for_approval(db, 1, 4, second["credit_note_invoice_id"])
        decide_reversal_approval(db, 1, 3, second["credit_note_invoice_id"], approve=True)
        post_reversal(db, 1, 4, second["credit_note_invoice_id"], "rev-post-2")
    report = db.trial_balance(1)
    assert report["total_debit_dzd"] == report["total_credit_dzd"]
    assert report["balanced"] is True


# ---------------------------------------------------------------------------
# O. Refusal rollback
# ---------------------------------------------------------------------------
def test_o_refused_submission_leaves_no_state_and_is_audited(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    invoice = _create_invoice(db, document_id=document_id)  # nothing reviewed yet
    before = _invoice_row(db, invoice["invoice_id"])

    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_REVIEW_UNSATISFIED"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])

    after = _invoice_row(db, invoice["invoice_id"])
    assert after["workflow_state"] == before["workflow_state"] == "Validated"
    assert after["payload_sha256"] == before["payload_sha256"]
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM approval_requests WHERE target_id = ?", (invoice["invoice_id"],)
            ).fetchone()["n"]
            == 0
        )
        doc_state = conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ?", (document_id,)
        ).fetchone()["workflow_state"]
    assert doc_state == "Validated"
    # The refusal itself is durable evidence (commit-then-refuse).
    assert "INVOICE_SUBMIT_BLOCKED_UNREVIEWED" in _audit_actions(db)
    assert db.verify_audit_chain(1)["valid"] is True


# ---------------------------------------------------------------------------
# P. Approval-time re-evaluation (TOCTOU)
# ---------------------------------------------------------------------------
def test_p_out_of_band_line_edit_before_approval_is_caught_at_approval(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)

    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoice_lines SET unit_price_dzd = '650.000000' WHERE invoice_id = ?",
            (invoice_id,),
        )
    with pytest.raises(FinancialIntegrityError, match="LINE_1_PRICE_SOURCE_MISMATCH"):
        decide_invoice_approval(db, 1, 3, invoice_id, approve=True)
    assert _invoice_row(db, invoice_id)["workflow_state"] == "PendingApproval"
    assert "INVOICE_APPROVAL_BLOCKED_SOURCE_MISMATCH" in _audit_actions(db)


# ---------------------------------------------------------------------------
# Q. Backup / restore after posting (environment-gated)
# ---------------------------------------------------------------------------
_RESTORE_GATE_OK = sys.version_info >= (3, 11, 17) and sys.version_info < (3, 13)


@pytest.mark.skipif(
    not _RESTORE_GATE_OK,
    reason=(
        "restore_and_verify_backup refuses to run below Python 3.11.17 by design "
        "(safe tar extraction); this audit host has "
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}. "
        "The path is exercised by CI on 3.11/3.12 and recorded as environment-blocked."
    ),
)
def test_q_backup_and_restore_after_posting_verify_the_ledger(env, tmp_path):
    """A posted, fully reviewed invoice survives backup -> isolated restore intact."""
    from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _approve_and_post(db, invoice_id)

    report = db.trial_balance(1)
    assert report["balanced"] is True
    assert report["total_debit_dzd"] == report["total_credit_dzd"] == "1190.00"

    backup_meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backup" / "erp.backup.enc",
        backup_encryption_key=vault.encryption_key,
    )
    restored = restore_and_verify_backup(
        backup_path=Path(backup_meta["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "isolated_restore",
    )
    assert restored["db_sha256_verified"] is True
    assert restored["audit_chains_valid"] is True
    assert restored["trial_balance_debit_dzd"] == "1190.00"
    assert restored["trial_balance_credit_dzd"] == "1190.00"

    # The restored database is a working deployment: the posted invoice, its
    # journal entry, its review decisions and its audit chain all survive.
    restored_db = restored["restored_db"]
    restored_report = restored_db.trial_balance(1)
    assert restored_report["balanced"] is True
    assert restored_report["total_debit_dzd"] == "1190.00"
    assert restored_db.verify_audit_chain(1)["valid"] is True
    with restored_db.transaction() as conn:
        inv = conn.execute(
            "SELECT workflow_state FROM supplier_invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        entries = conn.execute(
            "SELECT COUNT(*) AS n FROM journal_entries WHERE company_id = 1 AND state = 'POSTED'"
        ).fetchone()["n"]
        decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM ocr_field_reviews WHERE company_id = 1"
        ).fetchone()["n"]
    assert inv["workflow_state"] == "Posted"
    assert entries == 1
    assert decisions >= 6


# ---------------------------------------------------------------------------
# R. Migration safety on a pre-Phase-29 database
# ---------------------------------------------------------------------------
def test_r_pre_phase29_database_migrates_idempotently_without_data_loss(tmp_path):
    """A database without the Phase 29 column/table/trigger is upgraded in place."""
    path = tmp_path / "p32.sqlite3"  # the database _make_env created
    db, _vault = _make_env(tmp_path)
    doc, invoice_id = _reviewed_invoice(db, _vault)
    before_decisions = db.list_field_reviews(1, doc)
    assert len(before_decisions) > 6

    # (Connections are per-transaction, so no handle is open here.)

    # Simulate the pre-Phase-29 schema: no review table, no conflict column,
    # no refreshed immutability trigger.
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE ocr_field_reviews")
        conn.execute("DROP TRIGGER trg_ocr_field_preserve_raw")
        conn.execute("ALTER TABLE ocr_extracted_fields DROP COLUMN conflict_values_json")
        conn.commit()

    reopened = ERPDatabase(str(path))
    reopened_again = ERPDatabase(str(path))  # repeated execution must be a no-op
    for handle in (reopened, reopened_again):
        with handle.transaction() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(ocr_extracted_fields)").fetchall()}
            assert "conflict_values_json" in cols
            triggers = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()}
            assert "trg_ocr_field_preserve_raw" in triggers
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            assert "ocr_field_reviews" in tables
            # Legacy financial records are untouched, not rewritten.
            row = conn.execute(
                "SELECT workflow_state, amount_ht_dzd, payload_sha256 FROM supplier_invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            assert row["workflow_state"] == "Validated"
            assert row["amount_ht_dzd"] == "1000.00"
            assert row["payload_sha256"]

    # The rebuilt trigger still protects the re-added column.
    with pytest.raises(FinancialIntegrityError, match="OCR_RAW_IMMUTABILITY_VIOLATION"):
        with reopened.transaction() as conn:
            conn.execute(
                "UPDATE ocr_extracted_fields SET conflict_values_json = '[\"x\"]'"
                " WHERE company_id = 1 AND document_id = ? AND field_name = 'invoice_number'",
                (doc,),
            )
    # Decisions can be recorded again on the migrated database.
    decide_extracted_field_review(
        reopened, company_id=1, user_id=2, document_id=doc,
        field_name="amount_ht_dzd", decision="CORRECTED", value="1000.00",
    )
    assert reopened.verify_audit_chain(1)["valid"] is True


# ---------------------------------------------------------------------------
# S. P32-F1 (fixed) — non-numeric amounts are refused, never stored
# ---------------------------------------------------------------------------
def _invoice_payload(number: str, amount_ht: str) -> dict:
    return {
        "supplier_id": 7,
        "supplier_invoice_no": number,
        "invoice_date": "2026-02-14",
        "amount_ht_dzd": amount_ht,
        "amount_tva_dzd": "19.00",
        "amount_ttc_dzd": "119.00",
        "lines": [
            {"description": "x", "qty": "1", "unit_price_dzd": "100.00",
             "tax_rate": "19", "line_ht_dzd": "100.00"}
        ],
    }


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "sNaN", "nan"])
def test_s_non_numeric_amounts_are_refused_and_never_persisted(env, bad):
    """P32-F1 regression: ``NaN``/``Infinity`` never enter a money column.

    Before the fix ``Decimal('NaN')`` survived ``round_dzd`` (``quantize`` does
    not signal on a quiet NaN), so ``POST /api/v1/invoices`` answered 200 and
    stored the TEXT ``'NaN'`` in ``supplier_invoices.amount_ht_dzd``. The value
    could not be posted (the invoice stays ``NeedsReview``), but a non-numeric
    string in a financial column is a data-integrity defect with a misleading
    success response. ``to_decimal`` now refuses every non-finite value.
    """
    db, _vault, client = env
    token = client.post(
        "/api/v1/auth/token", json={"username": "clerk_a", "password": "Clerk#2026x"}
    ).json()["access_token"]
    resp = client.post(
        "/api/v1/invoices",
        headers={"Authorization": f"Bearer {token}"},
        json=_invoice_payload(f"FAC-BAD-{bad}", bad),
    )
    assert resp.status_code == 422, (bad, resp.status_code, resp.text)
    with db.transaction() as conn:
        stored = conn.execute(
            "SELECT COUNT(*) AS n FROM supplier_invoices WHERE supplier_invoice_no = ?",
            (f"FAC-BAD-{bad}",),
        ).fetchone()["n"]
    assert stored == 0


@pytest.mark.parametrize("field", ["amount_ht_dzd", "line_unit_price", "line_item_json", "line_discount"])
def test_s2_non_numeric_decision_values_are_a_422(env, field):
    """P32-F1 regression: a bad decision value is a 422, not an unhandled 500."""
    db, vault, client = env
    document_id = _upload(db, vault)["document_id"]
    token = client.post(
        "/api/v1/auth/token", json={"username": "clerk_a", "password": "Clerk#2026x"}
    ).json()["access_token"]
    body = {"field_name": field, "decision": "CORRECTED", "value": "NaN"}
    if field.startswith("line_"):
        body["line_index"] = 0
    resp = client.post(
        f"/api/v1/documents/{document_id}/fields/decide",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert resp.status_code == 422, (field, resp.status_code, resp.text)
    # And nothing was decided or rewritten.
    with db.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM ocr_field_reviews WHERE document_id = ?", (document_id,)
        ).fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# T. FINDINGS P32-F2 / P32-F3 — NeedsReview dead-end and document state drift
# ---------------------------------------------------------------------------
def test_t_a_needs_review_invoice_recovers_in_place_without_drifting_evidence(env):
    """RESOLVED in Phase 33 (was FINDING P32-F2/P32-F3, Medium).

    ``NeedsReview`` is no longer a terminal dead-end and a refused duplicate
    attempt no longer rewrites the evidence:

    (a) ``revalidate_supplier_invoice`` corrects and re-validates the *same*
        invoice identity, re-running every validation prerequisite;
    (b) re-creating the invoice behind the same paper is refused outright with
        a documented domain error instead of silently minting a duplicate;
    (c) the linked source document keeps the state its invoice relies on.

    See ``tests/test_phase33_invoice_lifecycle.py`` (P32-F2/P32-F3 matrix).
    """
    from dz_erp.procurement_stock import revalidate_supplier_invoice

    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)

    broken = _create_invoice(
        db, document_id=document_id,
        lines=[_invoice_line("2", "500.00")],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    assert broken["workflow_state"] == "NeedsReview"

    correct = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    assert correct["invoice_id"] == broken["invoice_id"]
    assert correct["workflow_state"] == "Validated"
    assert correct["payload_sha256"] != broken["payload_sha256"]

    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_ALREADY_INVOICED"):
        _create_invoice(db, document_id=document_id, lines=[_invoice_line("2", "500.00")])

    with db.transaction() as conn:
        doc_state = conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ?", (document_id,)
        ).fetchone()["workflow_state"]
        invoices = conn.execute(
            "SELECT COUNT(*) AS n FROM supplier_invoices WHERE document_id = ?", (document_id,)
        ).fetchone()["n"]
    assert doc_state == "Validated"  # the evidence follows the invoice it backs
    assert invoices == 1
    assert _journal_entries(db) == []


def test_t2_a_previous_failed_validation_never_authorizes_posting(env):
    """RESOLVED in Phase 33: only the approved payload digest can be posted."""
    from dz_erp.procurement_stock import revalidate_supplier_invoice

    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _create_invoice(
        db, document_id=document_id,
        lines=[_invoice_line("2", "500.00")],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    rejected_hash = broken["payload_sha256"]
    assert broken["workflow_state"] == "NeedsReview"
    with pytest.raises(FinancialIntegrityError, match="must be 'Validated'"):
        submit_invoice_for_approval(db, 1, 2, broken["invoice_id"])

    corrected = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    submit_invoice_for_approval(db, 1, 2, broken["invoice_id"])
    decide_invoice_approval(db, 1, 3, broken["invoice_id"], approve=True)
    with db.transaction() as conn:
        inv = conn.execute(
            "SELECT approved_payload_sha256 FROM supplier_invoices WHERE id = ?",
            (broken["invoice_id"],),
        ).fetchone()
    assert inv["approved_payload_sha256"] == corrected["payload_sha256"] != rejected_hash
    assert _journal_entries(db) == []


# ---------------------------------------------------------------------------
# U. FINDING P32-F4 — draft modification leaves the document and the approval
#    request out of step with the invoice
# ---------------------------------------------------------------------------
def test_u_draft_modification_invalidates_the_approval_and_syncs_the_document(env):
    """RESOLVED in Phase 33 (was FINDING P32-F4, Low/Medium).

    A financial modification now invalidates the approval basis explicitly: the
    request becomes ``INVALIDATED`` with a reason and an actor, the invoice and
    its source document both return to ``NeedsReview``, and the approved payload
    digest is cleared — so the pre-modification approval can never authorize the
    modified payload.

    See ``tests/test_phase33_invoice_lifecycle.py`` (P32-F4 matrix).
    """
    db, vault, _client = env
    document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()["id"]
    result = modify_draft_invoice_line(db, 1, 2, invoice_id, line_id, "560.00")
    assert result["approval_invalidated"] is True

    with db.transaction() as conn:
        inv_state = conn.execute(
            "SELECT workflow_state, approved_payload_sha256 FROM supplier_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        doc_state = conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ?", (document_id,)
        ).fetchone()["workflow_state"]
        requests = conn.execute(
            "SELECT decision, reason FROM approval_requests WHERE target_id = ? ORDER BY id",
            (invoice_id,),
        ).fetchall()
    assert inv_state["workflow_state"] == "NeedsReview"
    assert inv_state["approved_payload_sha256"] is None
    assert doc_state == "NeedsReview"  # no longer claims a pending approval
    assert [dict(r) for r in requests] == [
        {"decision": "INVALIDATED", "reason": "PAYLOAD_MODIFIED_AFTER_SUBMISSION"}
    ]


# ---------------------------------------------------------------------------
# V. Timbre (CASH) against reviewed evidence
# ---------------------------------------------------------------------------
def test_v_cash_invoice_timbre_must_be_evidenced_before_submission(env):
    """A CASH invoice above the timbre threshold needs a decided timbre value."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)  # timbre extracted as 0.00 and confirmed

    # Droit de timbre for 1190.00 paid in cash is 1 % = 11.90, so an invoice that
    # declares it cannot match the reviewed 0.00 evidence.
    invoice = create_and_validate_supplier_invoice(
        db=db, company_id=1, user_id=2, supplier_id=7,
        supplier_invoice_no="FAC-2026-0001", invoice_date="2026-02-14",
        lines=[_invoice_line("2", "500.00")],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00",
        # 1 % of 1190.00 = 11.90, above the 5.00 floor.
        amount_timbre_dzd="11.90", amount_ttc_dzd="1201.90",
        payment_method="CASH", document_id=document_id,
    )
    assert invoice["workflow_state"] == "Validated"
    with pytest.raises(FinancialIntegrityError, match="AMOUNT_TIMBRE_DZD_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])


# ---------------------------------------------------------------------------
# W. Duplicate reversal is refused (no second credit note)
# ---------------------------------------------------------------------------
def test_w_a_second_reversal_of_the_same_invoice_is_refused(env):
    """Reversing twice must not double-credit, and the refusal is a domain error.

    RESOLVED in Phase 33 (was FINDING P32-F5, Low): the second attempt is
    detected *before* any insert and refused with a documented
    ``INVOICE_ALREADY_REVERSED`` (HTTP 409) plus an attributed audit event,
    instead of surfacing an unhandled ``sqlite3.IntegrityError``. The original
    posted entry and the first credit note are left untouched.

    See ``tests/test_phase33_invoice_lifecycle.py`` (P32-F5 matrix).
    """
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _approve_and_post(db, invoice_id)
    first = create_reversal_request(db, 1, 4, invoice_id, "dispute", "rev-1")
    cn = first["credit_note_invoice_id"]
    submit_reversal_for_approval(db, 1, 4, cn)
    decide_reversal_approval(db, 1, 3, cn, approve=True)
    post_reversal(db, 1, 4, cn, "revp-1")

    with pytest.raises(FinancialIntegrityError, match="INVOICE_ALREADY_REVERSED") as refused:
        create_reversal_request(db, 1, 4, invoice_id, "again", "rev-2")
    assert not isinstance(refused.value, sqlite3.IntegrityError)

    with db.transaction() as conn:
        refunds = conn.execute(
            "SELECT COUNT(*) AS n FROM supplier_invoices WHERE invoice_type = 'IN_REFUND' AND workflow_state = 'Posted'"
        ).fetchone()["n"]
    assert refunds == 1
    assert db.trial_balance(1)["balanced"] is True
