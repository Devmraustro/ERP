# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 29 — fail-closed OCR uncertainty handling and field-level human review.

What this phase must prove:

* An extracted document's field review is *enforced*, not advisory: an invoice
  whose linked document has undecided, flagged, stale, or inconsistent field
  state cannot be submitted or approved, and every blocked attempt is audited.
* Unavailable confidence is never treated as a measured one, and no confidence
  value — however high — ever accepts a field on its own: satisfaction always
  requires an explicit, attributed decision bound to the exact value reviewed.
* A required critical field that the extraction missed must be transcribed by
  a human (with honest ``human-manual-entry`` provenance); it can never be
  asserted absent. Optional fields can.
* Arithmetic inconsistency among the *extracted* totals blocks the document
  even when every field was confirmed against its (mis)read value.
* Decisions are idempotent on exact replay, refused on concurrency
  (value-changed-since-read), closed once the document backs an approval, and
  restricted server-side by role and tenant — every refusal audited.
* Legacy invoices without a linked document pass through untouched, and a
  pre-phase-29 database is migrated without losing a single stored row.
"""

import io
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import (
    AuthorizationError,
    ERPDatabase,
    FinancialIntegrityError,
    TenantIsolationError,
)
from dz_erp.intake_ocr import (
    PrivateDocumentVault,
    decide_extracted_field_review,
    parse_structured_invoice_text,
    update_extracted_field_by_human,
    upload_and_process_supplier_document,
)
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    decide_invoice_approval,
    post_supplier_invoice,
    submit_invoice_for_approval,
)
from dz_erp.review_policy import (
    DECISION_CORRECTED,
    DECISION_FLAGGED,
    DECISION_NOT_PRESENT,
    ReviewPolicyValueError,
    evaluate_document_review_policy,
)

_REQUIRED_HEADER_FIELDS = (
    "invoice_number",
    "supplier_name",
    "supplier_nif",
    "invoice_date",
    "currency_code",
    "amount_ht_dzd",
    "amount_tva_dzd",
    "amount_ttc_dzd",
)

_USERS = [
    (1, 1, "admin_a", "admin", 1, "AdminA#2026x"),
    (2, 1, "clerk_a", "clerk", 1, "Clerk#2026x"),
    (3, 1, "approver_a", "approver", 1, "Appr#2026x"),
    (4, 1, "acct_a", "accountant", 1, "Acct#2026x"),
    (5, 1, "wh_a", "warehouse", 1, "Wh#2026x"),
    (6, 1, "auditor_a", "auditor", 1, "Aud#2026x"),
    (20, 2, "admin_b", "admin", 1, "AdminB#2026x"),
    (21, 2, "clerk_b", "clerk", 1, "ClerkB#2026x"),
]

_SPEC = {
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
        for uid, cid, username, role, active, password in _USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, cid, username, role, active, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc)"
            " VALUES (7, 1, 'SPARTE', 'supplier', 'DZ', 'REEL', '000216001234567',"
            " '000216001234567', '16019912316', '16/00-0991216B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)


def _make_env(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "p29.sqlite3"))
    _setup_two_tenants(db)
    vault = PrivateDocumentVault(tmp_path / "vault")
    client = TestClient(create_erp_fastapi_app(db, vault))
    return db, vault, client


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _upload_doc(db: ERPDatabase, vault: PrivateDocumentVault, content: bytes, *, user_id: int = 2):
    return upload_and_process_supplier_document(
        db, vault, company_id=1, user_id=user_id,
        filename="facture.pdf", mime_type="application/pdf", content=content,
    )


def _upload_standard_doc(db: ERPDatabase, vault: PrivateDocumentVault, *, user_id: int = 2):
    return _upload_doc(db, vault, render_synthetic_invoice_pdf(_SPEC), user_id=user_id)


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
        {"sku": "SKU-1", "description": "Anchors", "qty": "1", "unit_price_dzd": "400.00",
         "tax_rate": "19", "line_ht_dzd": "400.00"},
        {"sku": "SKU-2", "description": "Bolts", "qty": "1", "unit_price_dzd": "600.00",
         "tax_rate": "19", "line_ht_dzd": "600.00"},
    ],
}


def _upload_two_line_doc(db: ERPDatabase, vault: PrivateDocumentVault, *, user_id: int = 2):
    return _upload_doc(db, vault, render_synthetic_invoice_pdf(_TWO_LINE_SPEC), user_id=user_id)


_DUP_LINE_SPEC = {
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
        {"sku": "SKU-1", "description": "Anchors", "qty": "1", "unit_price_dzd": "500.00",
         "tax_rate": "19", "line_ht_dzd": "500.00"},
        {"sku": "SKU-1", "description": "Anchors", "qty": "1", "unit_price_dzd": "500.00",
         "tax_rate": "19", "line_ht_dzd": "500.00"},
    ],
}


def _upload_dup_line_doc(db: ERPDatabase, vault: PrivateDocumentVault, *, user_id: int = 2):
    return _upload_doc(db, vault, render_synthetic_invoice_pdf(_DUP_LINE_SPEC), user_id=user_id)


def _set_line_payload(db: ERPDatabase, document_id: str, mutate) -> dict:
    """Rewrite the first line row's JSON payload out-of-band (extraction is otherwise immutable)."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id, normalized_value FROM ocr_extracted_fields WHERE company_id = 1"
            " AND document_id = ? AND field_name = 'line_item_json' AND line_index = 0",
            (document_id,),
        ).fetchone()
        payload = json.loads(row["normalized_value"])
        mutate(payload)
        conn.execute(
            "UPDATE ocr_extracted_fields SET normalized_value = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
    return payload


def _make_invoice(
    db: ERPDatabase,
    document_id=None,
    *,
    invoice_no: str = "FAC-2026-0001",
    invoice_date: str = "2026-02-14",
    line_qty: str = "2",
    unit_price: str = "500.00",
    amount_ht: str = "1000.00",
    amount_tva: str = "190.00",
    amount_ttc: str = "1190.00",
    user_id: int = 2,
    lines=None,
):
    if lines is None:
        line_ht = str((Decimal(line_qty) * Decimal(unit_price)).quantize(Decimal("0.01")))
        lines = [{"sku": "SKU-1", "description": "Widgets", "qty": line_qty,
                  "unit_price_dzd": unit_price, "tax_rate": "19", "line_ht_dzd": line_ht}]
    return create_and_validate_supplier_invoice(
        db=db, company_id=1, user_id=user_id, supplier_id=7,
        supplier_invoice_no=invoice_no, invoice_date=invoice_date,
        lines=lines,
        amount_ht_dzd=amount_ht, amount_tva_dzd=amount_tva, amount_ttc_dzd=amount_ttc,
        document_id=document_id,
    )


def _evaluate(db: ERPDatabase, company_id: int, document_id: str) -> dict:
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


def _confirm_extracted(db: ERPDatabase, company_id: int, document_id: str, user_id: int = 2) -> dict:
    """Confirm every extracted required field and line; NOT_PRESENT the missing optional ones."""
    ev = _evaluate(db, company_id, document_id)
    for fev in ev["fields"]:
        if fev["status"] == "missing":
            if not fev["required"]:
                decide_extracted_field_review(
                    db, company_id=company_id, user_id=user_id, document_id=document_id,
                    field_name=fev["field_name"], decision=DECISION_NOT_PRESENT,
                )
            continue
        decide_extracted_field_review(
            db, company_id=company_id, user_id=user_id, document_id=document_id,
            field_name=fev["field_name"], decision="CONFIRMED", expected_value=fev["value"],
        )
    for lev in ev["lines"]:
        decide_extracted_field_review(
            db, company_id=company_id, user_id=user_id, document_id=document_id,
            field_name="line_item_json", decision="CONFIRMED",
            expected_value=lev["value"], line_index=lev["line_index"],
        )
    for pev in ev["line_prices"]:
        decide_extracted_field_review(
            db, company_id=company_id, user_id=user_id, document_id=document_id,
            field_name="line_unit_price", decision="CONFIRMED",
            expected_value=pev["value"], line_index=pev["line_index"],
        )
    for dev in ev["line_discounts"]:
        # The extraction emits no discount; the synthetic fixture lines carry
        # no discount column and their printed nets equal qty × PU, so 0.00 is
        # the honest transcription for an absent discount. A discount already
        # on the payload (an earlier transcription) is confirmed as read.
        if dev["value"] is not None:
            decide_extracted_field_review(
                db, company_id=company_id, user_id=user_id, document_id=document_id,
                field_name="line_discount", decision="CONFIRMED",
                expected_value=dev["value"], line_index=dev["line_index"],
            )
        else:
            decide_extracted_field_review(
                db, company_id=company_id, user_id=user_id, document_id=document_id,
                field_name="line_discount", decision="CORRECTED",
                value="0.00", line_index=dev["line_index"],
            )
    final = _evaluate(db, company_id, document_id)
    assert final["satisfied"], final["blocking"]
    return final


def _blank_pdf() -> bytes:
    """A valid one-page PDF whose text matches no invoice field pattern.

    Extraction therefore finds zero fields and routes the document to manual
    transcription (REASON_NO_FIELDS) — the same starting point an Arabic-only
    scan reaches — without invoking the image OCR engine.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=True)
    c.setFont("Helvetica", 11)
    c.drawString(45, 780, "BON DE LIVRAISON SANS DONNEES FACTURE")
    c.save()
    return buf.getvalue()


def _audit_actions(db: ERPDatabase) -> list:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT actor_user_id, action_type, target_model, target_id FROM audit_events ORDER BY id"
        ).fetchall()
    return [(r["actor_user_id"], r["action_type"], r["target_model"], r["target_id"]) for r in rows]


# ---------------------------------------------------------------------------
# The gate: submission and approval are refused while review is unsatisfied
# ---------------------------------------------------------------------------


def test_unreviewed_document_blocks_submission_and_the_block_is_audited(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    assert invoice["workflow_state"] == "Validated"

    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_REVIEW_UNSATISFIED"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    blocks = [a for a in _audit_actions(db) if a[1] == "INVOICE_SUBMIT_BLOCKED_UNREVIEWED"]
    assert len(blocks) == 1, blocks
    assert blocks[0][0] == 2  # the clerk who attempted it
    # The refusal survives in the chain (commit-then-refuse), which stays valid.
    assert db.verify_audit_chain(company_id=1)["valid"] is True
    with db.transaction() as conn:
        state = conn.execute(
            "SELECT workflow_state FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)
        ).fetchone()["workflow_state"]
    assert state == "Validated"  # nothing moved


def test_field_review_satisfies_the_gate_and_approval_rechecks_it(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])

    evaluation = _confirm_extracted(db, 1, doc["document_id"])
    assert evaluation["satisfied"]
    # The satisfying decisions are attributed and bound to values.
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT field_name, decision, reviewed_value, reviewed_by_id FROM ocr_field_reviews"
            " WHERE document_id = ? ORDER BY id",
            (doc["document_id"],),
        ).fetchall()
    # Exact, not a floor: 8 required header fields + line qty + price + discount
    # = 11 decided rows, plus the 5 fields the extraction records as missing and
    # the clerk therefore decides NOT_PRESENT (F-R2-5: the bound used to be
    # ``>= 10`` while the comment claimed 11, so it pinned nothing).
    assert len(rows) == 16, [(r["field_name"], r["decision"]) for r in rows]
    decided_fields = {r["field_name"] for r in rows}
    assert {"invoice_number", "supplier_name", "supplier_nif", "invoice_date", "currency_code",
            "amount_ht_dzd", "amount_tva_dzd", "amount_ttc_dzd"} <= decided_fields
    assert {"line_item_json", "line_unit_price", "line_discount"} <= decided_fields
    # Header fields and extracted line readings are confirmed or honestly
    # absent only — no correction, flag or stale record. Line discounts are
    # the one legitimate CORRECTED flow here: extraction never emits them, so
    # each line's 0.00 is an attributed transcription (the fixture's printed
    # nets equal qty × PU, proving the discount absent on the paper).
    non_discount_rows = [r for r in rows if r["field_name"] != "line_discount"]
    discount_rows = [r for r in rows if r["field_name"] == "line_discount"]
    assert {r["decision"] for r in non_discount_rows} <= {"CONFIRMED", "NOT_PRESENT"}
    assert {r["decision"] for r in discount_rows} == {"CORRECTED"}
    assert all(r["reviewed_value"] == "0.00" for r in discount_rows)
    assert all(r["reviewed_by_id"] == 2 for r in rows)
    assert next(r for r in rows if r["field_name"] == "amount_ttc_dzd")["reviewed_value"] == "1190.00"

    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"
    approved = decide_invoice_approval(
        db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True
    )
    assert approved["workflow_state"] == "Approved"


def test_approval_recheck_refuses_a_document_disturbed_after_submit(tmp_path: Path) -> None:
    """The approval gate re-evaluates; an out-of-band flag cannot ride through.

    Once the document backs a submitted approval, decisions are closed, so the
    only way its review state can change is an out-of-band write (a support
    tool, a migration bug, a manual SQL session). The gate is the backstop
    that catches exactly that: the approver's attempt is refused and audited,
    while rejection — the safe direction — stays ungated.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"

    # Out-of-band disturbance: a flag appears after submission.
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ocr_field_reviews (
                company_id, document_id, field_name, line_index, decision,
                reviewed_value, note, reviewed_by_id, reviewed_at
            ) VALUES (1, ?, 'supplier_rc', NULL, 'FLAGGED', NULL, 'illegible stamp', 3, ?)
            """,
            (doc["document_id"], now_iso),
        )
    with pytest.raises(FinancialIntegrityError, match="FIELD_FLAGGED:supplier_rc"):
        decide_invoice_approval(
            db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True
        )
    blocks = [a for a in _audit_actions(db) if a[1] == "INVOICE_APPROVAL_BLOCKED_UNREVIEWED"]
    assert len(blocks) == 1, blocks
    assert blocks[0][0] == 3  # the approver who was refused
    # The safe direction is never gated.
    decided = decide_invoice_approval(
        db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=False,
        reason="flag appeared after submit",
    )
    assert decided["workflow_state"] == "Rejected"


def test_approval_rechecks_invoice_payload_against_reviewed_source(tmp_path: Path) -> None:
    """A payload altered after submission must be blocked at approval too."""
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    submitted = submit_invoice_for_approval(
        db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"]
    )
    assert submitted["workflow_state"] == "PendingApproval"

    # Simulate an out-of-band payload change after the submit-time check.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET amount_ht_dzd = ? WHERE company_id = ? AND id = ?",
            ("2000.00", 1, invoice["invoice_id"]),
        )

    with pytest.raises(FinancialIntegrityError, match="AMOUNT_HT_DZD_SOURCE_MISMATCH"):
        decide_invoice_approval(
            db, company_id=1, approver_user_id=3,
            invoice_id=invoice["invoice_id"], approve=True,
        )

    assert any(
        action[1] == "INVOICE_APPROVAL_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_reviewed_source_invoice_number_must_match_invoice_payload(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"], invoice_no="FAC-2026-9999")
    _confirm_extracted(db, 1, doc["document_id"])

    with pytest.raises(FinancialIntegrityError, match="INVOICE_NUMBER_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    assert any(
        action[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_reviewed_source_amounts_and_line_quantity_must_match_invoice(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    # This payload is internally consistent (2,000 + 19% TVA = 2,380),
    # but it does not match the reviewed source (1,000 + 190 = 1,190; qty 2).
    invoice = _make_invoice(
        db, doc["document_id"], line_qty="4", unit_price="500.00",
        amount_ht="2000.00", amount_tva="380.00", amount_ttc="2380.00",
    )
    _confirm_extracted(db, 1, doc["document_id"])

    with pytest.raises(FinancialIntegrityError, match="AMOUNT_HT_DZD_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    assert any(
        action[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_reviewed_source_line_unit_price_must_match_invoice(tmp_path: Path) -> None:
    """A matching line quantity does not license a different unit price.

    2 × 625.00 = 1250.00 (+19% TVA) is internally consistent, but the reviewed
    source line says 2 × 500.00 — only the per-line price comparison tells this
    apart from a legitimate invoice.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(
        db, doc["document_id"], line_qty="2", unit_price="625.00",
        amount_ht="1250.00", amount_tva="237.50", amount_ttc="1487.50",
    )
    _confirm_extracted(db, 1, doc["document_id"])

    with pytest.raises(FinancialIntegrityError, match="LINE_1_PRICE_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    assert any(
        action[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_swapped_invoice_line_prices_are_flagged_not_silently_matched(tmp_path: Path) -> None:
    """Row order is the documented association invariant, and it is enforced.

    The invoice line table stores no supplier SKU to join on, so source and
    invoice lines are compared positionally. A payload whose prices are
    transposed must be flagged on both rows — not silently matched as an
    unordered set of (qty, price) pairs.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_two_line_doc(db, vault)
    # Same lines, transposed prices: 600.00 + 400.00 sums to the same header
    # totals the source shows (1000.00 / 190.00 / 1190.00), and both
    # quantities still match row-by-row — only the order is wrong.
    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[
            {"sku": "SKU-1", "description": "Anchors", "qty": "1",
             "unit_price_dzd": "600.00", "tax_rate": "19", "line_ht_dzd": "600.00"},
            {"sku": "SKU-2", "description": "Bolts", "qty": "1",
             "unit_price_dzd": "400.00", "tax_rate": "19", "line_ht_dzd": "400.00"},
        ],
    )
    _confirm_extracted(db, 1, doc["document_id"])

    with pytest.raises(FinancialIntegrityError) as excinfo:
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    message = str(excinfo.value)
    assert "LINE_1_PRICE_SOURCE_MISMATCH" in message
    assert "LINE_2_PRICE_SOURCE_MISMATCH" in message
    # Quantities matched row-by-row; nothing else was flagged.
    assert "QUANTITY_SOURCE_MISMATCH" not in message
    assert any(
        action[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )


def _drop_price(payload: dict) -> None:
    payload.pop("unit_price_dzd", None)


def _garble_price(payload: dict) -> None:
    payload["unit_price_dzd"] = "N/A"


@pytest.mark.parametrize("mutate", [_drop_price, _garble_price], ids=["absent", "malformed"])
def test_missing_or_malformed_source_price_blocks_until_transcribed(
    tmp_path: Path, mutate
) -> None:
    """Price evidence that is absent or unparseable is fail-closed, and the
    reviewer's only repair is an attributed transcription (CORRECTED)."""
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    _set_line_payload(db, doc["document_id"], mutate)

    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is False
    assert "REQUIRED_FIELD_NOT_DECIDED:line_unit_price[0]" in evaluation["blocking"]

    # A price that is not on file cannot be confirmed — there is no value to
    # bind to; the reviewer must transcribe what the paper shows.
    with pytest.raises(FinancialIntegrityError, match="FIELD_VALUE_CHANGED_SINCE_READ"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_unit_price", decision="CONFIRMED",
            expected_value="500.00", line_index=0,
        )
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision=DECISION_CORRECTED,
        value="500.00", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["unit_price_dzd"] == "500.000000"
    assert payload["line_ht_dzd"] == "1000.00"  # 2 × 500.00, recomputed

    # The repaired evidence satisfies the policy and the matching invoice flows.
    _confirm_extracted(db, 1, doc["document_id"])
    invoice = _make_invoice(db, doc["document_id"])
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"


def test_invoice_line_price_tampered_after_submit_blocks_approval(tmp_path: Path) -> None:
    """The approval gate rechecks line prices: a payload price changed between
    submission and approval cannot ride through."""
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    submitted = submit_invoice_for_approval(
        db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"]
    )
    assert submitted["workflow_state"] == "PendingApproval"

    # Out-of-band change of the invoice line's price after the submit-time check.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoice_lines SET unit_price_dzd = ? WHERE invoice_id = ?",
            ("600.000000", invoice["invoice_id"]),
        )

    with pytest.raises(FinancialIntegrityError, match="LINE_1_PRICE_SOURCE_MISMATCH"):
        decide_invoice_approval(
            db, company_id=1, approver_user_id=3,
            invoice_id=invoice["invoice_id"], approve=True,
        )

    assert any(
        action[1] == "INVOICE_APPROVAL_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_invoice_line_price_unparseable_blocks_submission(tmp_path: Path) -> None:
    """A malformed invoice-side price cannot ride through either direction.

    The invoice schema keeps ``unit_price_dzd`` as TEXT; if an out-of-band write
    leaves it unparseable, the exact comparison must refuse to guess and fail
    closed (`LINE_1_PRICE_SOURCE_UNPARSEABLE`), never silently skip the price.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoice_lines SET unit_price_dzd = ? WHERE invoice_id = ?",
            ("n/a", invoice["invoice_id"]),
        )

    with pytest.raises(FinancialIntegrityError, match="LINE_1_PRICE_SOURCE_UNPARSEABLE"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert any(
        action[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH"
        for action in _audit_actions(db)
    )


def test_source_price_changed_after_decision_blocks_and_rebinds_fail_closed(tmp_path: Path) -> None:
    """A source price edited after the reviewer bound it cannot be pushed through.

    The bound decision goes stale (the evidence the reviewer confirmed no longer
    exists), the gate refuses on the review itself, a CORRECTED transcription
    re-binds to the current payload value, and the previously matching invoice
    is then refused on price — fail closed in both directions.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    # Out-of-band change of the source price after the binding decision.
    _set_line_payload(db, doc["document_id"], lambda p: p.update({"unit_price_dzd": "555.00"}))

    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is False
    assert "FIELD_FLAGGED:line_unit_price[0]" in evaluation["blocking"]
    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_REVIEW_UNSATISFIED"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # Re-bind: the transcription is bound to the *current* payload value and
    # re-satisfies the review — but the invoice still says 500.00, so the
    # consistency gate now refuses on price.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision=DECISION_CORRECTED,
        value="555.00", line_index=0,
    )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is True
    with pytest.raises(FinancialIntegrityError, match="LINE_1_PRICE_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])


def test_duplicate_identical_lines_match_positionally(tmp_path: Path) -> None:
    """Ambiguous identities (two identical rows) resolve deterministically.

    No supplier SKU joins source and invoice lines, so identical adjacent rows
    are matched positionally: both are decided, both match, and the invoice
    flows — there is no unordered-set rematching to hide a difference.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_dup_line_doc(db, vault)
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert len(evaluation["lines"]) == 2 and len(evaluation["line_prices"]) == 2

    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[
            {"sku": "SKU-1", "description": "Anchors", "qty": "1",
             "unit_price_dzd": "500.00", "tax_rate": "19", "line_ht_dzd": "500.00"},
            {"sku": "SKU-1", "description": "Anchors", "qty": "1",
             "unit_price_dzd": "500.00", "tax_rate": "19", "line_ht_dzd": "500.00"},
        ],
    )
    _confirm_extracted(db, 1, doc["document_id"])
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"


def test_line_price_decision_binds_canonically_and_correction_recomputes(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)

    # "500.00" extracted, canonical "500.000000": the reviewer states either form.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision="CONFIRMED",
        expected_value="500.00", line_index=0,
    )
    # A price decision alone does not satisfy the document: the quantity of
    # the same line is a separate, still-undecided element.
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is False
    assert "REQUIRED_FIELD_NOT_DECIDED:line_item_json[0]" in evaluation["blocking"]

    # NOT_PRESENT is not available for line elements — the row exists.
    with pytest.raises(ReviewPolicyValueError, match="NOT_PRESENT is not valid for line items"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_unit_price", decision=DECISION_NOT_PRESENT, line_index=0,
        )

    # Bind the quantity too, so the price correction below proves that it
    # does not stale the quantity decision of the same line.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_item_json", decision="CONFIRMED",
        expected_value="2.000000", line_index=0,
    )

    # Correction rewrites the price and recomputes the line amount; the
    # already-bound quantity decision is not staled by a price correction.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision=DECISION_CORRECTED, value="600", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["unit_price_dzd"] == "600.000000"
    assert payload["line_ht_dzd"] == "1200.00"  # 2 × 600.00, recomputed
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["lines"][0]["satisfied"] is True
    assert evaluation["line_prices"][0]["satisfied"] is True

    # French decimal input lands on the same canonical value...
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision=DECISION_CORRECTED, value="600,50", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["unit_price_dzd"] == "600.500000"
    assert payload["line_ht_dzd"] == "1201.00"  # 2 × 600.50, recomputed
    # ...and the decision binds to the canonical form, idempotently.
    first = decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision="CONFIRMED",
        expected_value="600.500000", line_index=0,
    )
    assert first["idempotent_replay"] is False
    replay = decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision="CONFIRMED",
        expected_value="600.500000", line_index=0,
    )
    assert replay["idempotent_replay"] is True

    # Zero and negative prices follow the existing amount-class rules: a
    # negative transcription is refused outright; zero binds canonically and
    # the recomputed line amount follows.
    with pytest.raises(ReviewPolicyValueError, match="FIELD_VALUE_NEGATIVE_AMOUNT"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_unit_price", decision=DECISION_CORRECTED,
            value="-1", line_index=0,
        )
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_unit_price", decision=DECISION_CORRECTED,
        value="0", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["unit_price_dzd"] == "0.000000"
    assert payload["line_ht_dzd"] == "0.00"  # 2 × 0.00, recomputed


def test_sub_centime_price_identity_is_enforced_at_six_decimals(tmp_path: Path) -> None:
    """The paper's price is compared exactly, never rounded into agreement.

    A source price bound at sub-centime precision (a misread third decimal the
    reviewer confirmed as read) is a *different price* from the centime price
    on any internally-consistent invoice — flagged, never silently rounded.
    The reviewer's repair is to transcribe the printed centime price.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    # The extraction read three decimals (500.004); the reviewer confirms it
    # as read — the binding carries the full precision.
    _set_line_payload(db, doc["document_id"], lambda p: p.update({"unit_price_dzd": "500.004"}))
    _confirm_extracted(db, 1, doc["document_id"])

    # Header-identical invoice at the centime price (2 × 500.00): every total,
    # quantity and the invoice number match; only the exact price comparison
    # separates it.
    invoice = _make_invoice(db, doc["document_id"])
    with pytest.raises(FinancialIntegrityError) as excinfo:
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert "LINE_1_PRICE_SOURCE_MISMATCH" in str(excinfo.value)
    assert "AMOUNT_HT_DZD_SOURCE_MISMATCH" not in str(excinfo.value)

    # A second reviewer corrects the misread to the printed centime price:
    # the payload and its line amount become consistent with the paper, and
    # the same invoice then flows.
    decide_extracted_field_review(
        db, company_id=1, user_id=4, document_id=doc["document_id"],
        field_name="line_unit_price", decision=DECISION_CORRECTED, value="500.00", line_index=0,
    )
    _confirm_extracted(db, 1, doc["document_id"])
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"


# ---------------------------------------------------------------------------
# Phase 31: the decided discount and the derived line net
# ---------------------------------------------------------------------------


def _decide_discounted_review(db: ERPDatabase, document_id: str, *, discount: str = "10.00",
                              ht: str = "900.00", tva: str = "171.00", ttc: str = "1071.00") -> dict:
    """Complete a review where the paper shows a discount the extraction missed.

    The reviewer transcribes the line discount, corrects the header totals to
    the discounted values (extraction read the gross header), and confirms
    everything else at its current value. For ``_SPEC`` (2 × 500.00, 10 %
    discount): HT 900.00, TVA 171.00, TTC 1071.00 (BANK_TRANSFER → timbre 0).
    """
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="amount_ht_dzd", decision=DECISION_CORRECTED, value=ht,
    )
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="amount_tva_dzd", decision=DECISION_CORRECTED, value=tva,
    )
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="amount_ttc_dzd", decision=DECISION_CORRECTED, value=ttc,
    )
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=document_id,
        field_name="line_discount", decision=DECISION_CORRECTED, value=discount, line_index=0,
    )
    return _confirm_extracted(db, 1, document_id)


def test_reviewed_discount_and_line_net_match_flows(tmp_path: Path) -> None:
    """A discounted paper, fully decided, authorizes exactly that invoice.

    The line discount is transcribed (10 %), the header totals corrected to
    the discounted values, and the invoice carries discount_pct 10.00 with the
    net 2 × 500.00 × 0.90 = 900.00 — submit and approval both pass the gate:
    discount equality and the derived line net agree exactly.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    evaluation = _decide_discounted_review(db, doc["document_id"])
    assert evaluation["satisfied"]
    assert evaluation["line_discounts"][0]["value"] == "10.00"
    assert evaluation["line_discounts"][0]["satisfied"] is True

    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.00",
                "tax_rate": "19", "line_ht_dzd": "900.00"}],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    assert invoice["workflow_state"] == "Validated"
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"
    approved = decide_invoice_approval(
        db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True
    )
    assert approved["workflow_state"] == "Approved"


def test_mismatched_source_discount_blocks_submission(tmp_path: Path) -> None:
    """A decided discount the invoice ignores is flagged, and the net with it.

    The paper shows a 10 % discount; the invoice is entered without one. Both
    the discount comparison and the derived net (900.00 from the decided
    values vs the invoiced 1000.00) are flagged — audited, chain valid.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    evaluation = _decide_discounted_review(db, doc["document_id"])
    assert evaluation["satisfied"]

    # The invoice matches the gross extraction (no discount anywhere), which
    # is exactly the payload the extraction's raw header totals would accept.
    invoice = _make_invoice(db, doc["document_id"])
    assert invoice["workflow_state"] == "Validated"
    with pytest.raises(FinancialIntegrityError) as excinfo:
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    message = str(excinfo.value)
    assert "DOCUMENT_INVOICE_MISMATCH" in message
    assert "LINE_1_DISCOUNT_SOURCE_MISMATCH" in message
    assert "LINE_1_LINE_HT_SOURCE_MISMATCH:derived=900.00!=invoiced=1000.00" in message
    blocks = [a for a in _audit_actions(db) if a[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH"]
    assert len(blocks) == 1 and blocks[0][0] == 2
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_line_net_rounding_boundary_authoritative_discount(tmp_path: Path) -> None:
    """The decided 2-decimal discount is authoritative for the derived net.

    An invoice entered with a raw discount of more precision than the invoice
    column stores (10.004 → stored 10.00) passes its own HT math against the
    raw value, but the gate re-derives the net from the decided 2-decimal
    discount and flags the centime difference — the honest repair is entering
    the invoice with the canonical 2-decimal discount. No discount mismatch
    fires: the stored discount equals the decided one exactly.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    # 2 × 500.00 with raw 10.004 % → net 899.96 (raw-derived); TVA 170.99.
    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.004",
                "tax_rate": "19", "line_ht_dzd": "899.96"}],
        amount_ht="899.96", amount_tva="170.99", amount_ttc="1070.95",
    )
    assert invoice["workflow_state"] == "Validated"
    _decide_discounted_review(db, doc["document_id"], discount="10.004",
                              ht="899.96", tva="170.99", ttc="1070.95")
    with pytest.raises(FinancialIntegrityError) as excinfo:
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    message = str(excinfo.value)
    assert "LINE_1_LINE_HT_SOURCE_MISMATCH:derived=900.00!=invoiced=899.96" in message
    assert "LINE_1_DISCOUNT_SOURCE_MISMATCH" not in message


def test_discount_decision_boundaries_are_refused_or_bound_exactly(tmp_path: Path) -> None:
    """Percent boundaries: [0, 100] inclusive binds canonically; outside is refused.

    A negative transcription is refused (`FIELD_VALUE_NEGATIVE_AMOUNT`), so is
    anything above 100 (`FIELD_VALUE_DISCOUNT_OUT_OF_RANGE`); French decimals
    land on the canonical 2-decimal form; 100.00 binds and re-derives the net
    to 0.00. No value is ever clamped.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)

    with pytest.raises(ReviewPolicyValueError, match="FIELD_VALUE_NEGATIVE_AMOUNT"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_discount", decision=DECISION_CORRECTED, value="-5", line_index=0,
        )
    with pytest.raises(ReviewPolicyValueError, match="FIELD_VALUE_DISCOUNT_OUT_OF_RANGE"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_discount", decision=DECISION_CORRECTED, value="100.01", line_index=0,
        )
    # French decimal input binds canonically and recomputes the net.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_discount", decision=DECISION_CORRECTED, value="12,5", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["discount_pct"] == "12.50"
    assert payload["line_ht_dzd"] == "875.00"  # 2 × 500.00 × (1 − 12.5 %)
    # Idempotent replay of the same transcription.
    replay = decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_discount", decision=DECISION_CORRECTED, value="12.50", line_index=0,
    )
    assert replay["idempotent_replay"] is True
    # The inclusive upper boundary binds and zeroes the net.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_discount", decision=DECISION_CORRECTED, value="100", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["discount_pct"] == "100.00"
    assert payload["line_ht_dzd"] == "0.00"
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["line_discounts"][0]["satisfied"] is True


def test_invoice_creation_refuses_out_of_range_discount(tmp_path: Path) -> None:
    """The documented rule also holds at the financial record: no clamping.

    An invoice line with a negative or >100 discount lands in NeedsReview with
    ``LINE_1_DISCOUNT_OUT_OF_RANGE`` and cannot be submitted — even when its
    declared net was made arithmetically consistent with the invalid rate.
    """
    db, _vault, _ = _make_env(tmp_path)
    # −5 % would *raise* the net: 2 × 500.00 × 1.05 = 1050.00.
    invoice = _make_invoice(
        db,
        invoice_no="FAC-2026-0002",
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "-5.00",
                "tax_rate": "19", "line_ht_dzd": "1050.00"}],
        amount_ht="1050.00", amount_tva="199.50", amount_ttc="1249.50",
    )
    assert invoice["workflow_state"] == "NeedsReview"
    assert any(e.startswith("LINE_1_DISCOUNT_OUT_OF_RANGE:") for e in invoice["validation_errors"])
    with pytest.raises(FinancialIntegrityError, match="must be 'Validated'"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # >100 % would *negate* the net: 2 × 500.00 × (1 − 1.5) = −500.00.
    invoice = _make_invoice(
        db,
        invoice_no="FAC-2026-0003",
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "150.00",
                "tax_rate": "19", "line_ht_dzd": "-500.00"}],
        amount_ht="-500.00", amount_tva="-95.00", amount_ttc="-595.00",
    )
    assert invoice["workflow_state"] == "NeedsReview"
    assert any(e.startswith("LINE_1_DISCOUNT_OUT_OF_RANGE:") for e in invoice["validation_errors"])


def test_undecided_discount_blocks_until_transcribed(tmp_path: Path) -> None:
    """A fully-confirmed quantity and price do not license an undecided discount.

    With every decision made except the line discount, the document stays
    unsatisfied (`REQUIRED_FIELD_NOT_DECIDED:line_discount[0]`) and the
    invoice is refused; transcribing the honest 0.00 satisfies the review and
    the same invoice flows.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])

    ev = _evaluate(db, 1, doc["document_id"])
    for fev in ev["fields"]:
        if fev["status"] == "missing":
            if not fev["required"]:
                decide_extracted_field_review(
                    db, company_id=1, user_id=2, document_id=doc["document_id"],
                    field_name=fev["field_name"], decision=DECISION_NOT_PRESENT,
                )
            continue
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=fev["field_name"], decision="CONFIRMED", expected_value=fev["value"],
        )
    for lev in ev["lines"]:
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_item_json", decision="CONFIRMED",
            expected_value=lev["value"], line_index=lev["line_index"],
        )
    for pev in ev["line_prices"]:
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_unit_price", decision="CONFIRMED",
            expected_value=pev["value"], line_index=pev["line_index"],
        )
    # The discount is deliberately left undecided.
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is False
    assert "REQUIRED_FIELD_NOT_DECIDED:line_discount[0]" in evaluation["blocking"]
    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_REVIEW_UNSATISFIED"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # The honest transcription closes the gap: the paper's net equals
    # qty × PU, proving the discount absent.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_discount", decision=DECISION_CORRECTED, value="0.00", line_index=0,
    )
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"


def test_absent_or_malformed_payload_discount_routes_to_transcription(tmp_path: Path) -> None:
    """A discount that is not (correctly) on file cannot be confirmed.

    Extraction never emits a discount, and an out-of-band malformed one is
    just as unreviewable: CONFIRMED is refused in both cases — the evidence
    the reviewer claims to have read is not there — and a CORRECTED
    transcription is the only repair.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)

    # Absent (extraction never wrote it): confirmed-at-zero is refused...
    with pytest.raises(FinancialIntegrityError, match="FIELD_VALUE_CHANGED_SINCE_READ"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_discount", decision="CONFIRMED",
            expected_value="0.00", line_index=0,
        )
    # ...and a malformed out-of-band write is treated exactly the same.
    _set_line_payload(db, doc["document_id"], lambda p: p.update({"discount_pct": "abc"}))
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["line_discounts"][0]["status"] == "missing"
    with pytest.raises(FinancialIntegrityError, match="FIELD_VALUE_CHANGED_SINCE_READ"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_discount", decision="CONFIRMED",
            expected_value="0.00", line_index=0,
        )
    # Transcription repairs both: the payload carries the canonical value and
    # the review binds to it.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_discount", decision=DECISION_CORRECTED, value="0.00", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["discount_pct"] == "0.00"
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["line_discounts"][0]["satisfied"] is True


def test_source_discount_changed_after_decision_blocks_and_rebinds(tmp_path: Path) -> None:
    """A source discount edited after the reviewer bound it cannot ride through.

    The bound decision goes stale, the gate refuses on the review itself, a
    CORRECTED transcription re-binds to the current payload value (rewriting
    the derived net), and the previously matching invoice is then refused on
    both the discount and the net — fail closed in both directions.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    _decide_discounted_review(db, doc["document_id"])  # 10 % decided, headers 900/171/1071
    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.00",
                "tax_rate": "19", "line_ht_dzd": "900.00"}],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )

    # Out-of-band change of the source discount after the binding decision.
    _set_line_payload(db, doc["document_id"], lambda p: p.update({"discount_pct": "20.00"}))
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is False
    assert "FIELD_FLAGGED:line_discount[0]" in evaluation["blocking"]
    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_REVIEW_UNSATISFIED"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # Re-bind to the current payload value; the derived net follows the
    # corrected discount (2 × 500.00 × 0.80 = 800.00) without staling the
    # quantity or price decisions.
    decide_extracted_field_review(
        db, company_id=1, user_id=4, document_id=doc["document_id"],
        field_name="line_discount", decision=DECISION_CORRECTED, value="20.00", line_index=0,
    )
    payload = json.loads(
        next(
            f for f in db.list_extracted_fields(1, doc["document_id"])
            if f["field_name"] == "line_item_json" and f["line_index"] == 0
        )["normalized_value"]
    )
    assert payload["discount_pct"] == "20.00"
    assert payload["line_ht_dzd"] == "800.00"
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is True
    assert evaluation["lines"][0]["satisfied"] is True
    assert evaluation["line_prices"][0]["satisfied"] is True
    # The still-10 % invoice now differs from the re-bound source on both the
    # discount and the net.
    with pytest.raises(FinancialIntegrityError) as excinfo:
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    message = str(excinfo.value)
    assert "LINE_1_DISCOUNT_SOURCE_MISMATCH" in message
    assert "LINE_1_LINE_HT_SOURCE_MISMATCH:derived=800.00!=invoiced=900.00" in message
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_invoice_discount_tampered_after_submit_blocks_approval(tmp_path: Path) -> None:
    """An invoice discount changed after submission is caught at approval."""
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    _decide_discounted_review(db, doc["document_id"])
    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.00",
                "tax_rate": "19", "line_ht_dzd": "900.00"}],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"

    # Out-of-band tamper: the invoice's discount drops back to zero (its net
    # stays at the discounted 900.00, so the payload is internally unlike
    # anything its own math would produce).
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoice_lines SET discount_pct = '0.00' WHERE invoice_id = ?",
            (invoice["invoice_id"],),
        )
    with pytest.raises(FinancialIntegrityError) as excinfo:
        decide_invoice_approval(
            db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True
        )
    message = str(excinfo.value)
    assert "LINE_1_DISCOUNT_SOURCE_MISMATCH" in message
    assert "INVOICE_APPROVAL_BLOCKED_SOURCE_MISMATCH" in [
        a[1] for a in _audit_actions(db)
    ]
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_invoice_discount_or_net_unparseable_blocks_submission(tmp_path: Path) -> None:
    """Unparseable invoice-side discount or net is refused, never skipped.

    ``supplier_invoice_lines`` keeps both as TEXT; an out-of-band write that
    leaves either unreadable must fail closed (`LINE_1_DISCOUNT_SOURCE_UNPARSEABLE`
    / `LINE_1_LINE_HT_SOURCE_UNPARSEABLE`) instead of silently comparing
    nothing.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    _decide_discounted_review(db, doc["document_id"])
    invoice = _make_invoice(
        db, doc["document_id"],
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.00",
                "tax_rate": "19", "line_ht_dzd": "900.00"}],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoice_lines SET discount_pct = 'n/a' WHERE invoice_id = ?",
            (invoice["invoice_id"],),
        )
    with pytest.raises(FinancialIntegrityError, match="LINE_1_DISCOUNT_SOURCE_UNPARSEABLE"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # Restore the discount, corrupt the net instead.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoice_lines SET discount_pct = '10.00', line_ht_dzd = 'oops'"
            " WHERE invoice_id = ?",
            (invoice["invoice_id"],),
        )
    with pytest.raises(FinancialIntegrityError, match="LINE_1_LINE_HT_SOURCE_UNPARSEABLE"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert any(
        a[1] == "INVOICE_SUBMIT_BLOCKED_SOURCE_MISMATCH" for a in _audit_actions(db)
    )


def test_legacy_discounted_invoice_recomputes_and_posts(tmp_path: Path) -> None:
    """Document-less discounted invoices: deterministic recompute, no gate.

    The typed path carries discounts too: approve → post works on a discounted
    invoice, the payload hash binds the discount, and a price edit re-derives
    the net from the persisted discount (never drops it), landing the invoice
    back in NeedsReview like any approval-invalidating edit.
    """
    db, _vault, _ = _make_env(tmp_path)
    invoice = _make_invoice(
        db,
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.00",
                "tax_rate": "19", "line_ht_dzd": "900.00"}],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    assert invoice["workflow_state"] == "Validated"

    # The payload hash binds the discount: same payload without it hashes
    # differently.
    from dz_erp.procurement_stock import compute_invoice_payload_hash

    base_line = {"sku": "SKU-1", "qty": "2", "unit_price_dzd": "500.00",
                 "tax_rate": "19", "line_ht_dzd": "900.00"}
    assert compute_invoice_payload_hash(
        supplier_id=7, supplier_invoice_no="FAC-2026-0001", invoice_date="2026-02-14",
        payment_method="BANK_TRANSFER", amount_ht_dzd="900.00", amount_tva_dzd="171.00",
        amount_timbre_dzd="0.00", amount_ttc_dzd="1071.00",
        lines=[{**base_line, "discount_pct": "10.00"}],
    ) != compute_invoice_payload_hash(
        supplier_id=7, supplier_invoice_no="FAC-2026-0001", invoice_date="2026-02-14",
        payment_method="BANK_TRANSFER", amount_ht_dzd="900.00", amount_tva_dzd="171.00",
        amount_timbre_dzd="0.00", amount_ttc_dzd="1071.00",
        lines=[{**base_line, "discount_pct": "0.00"}],
    )

    # Approve and post the discounted invoice; the trial balance stays intact.
    submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True)
    posted = post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=invoice["invoice_id"], idempotency_key="disc-1"
    )
    assert posted["workflow_state"] == "Posted"
    assert db.verify_audit_chain(company_id=1)["valid"] is True

    # A price edit on a second discounted draft re-derives the net WITH the
    # persisted discount: 2 × 550.00 × 0.90 = 990.00 — not the
    # discount-dropping 1100.00 — and the edit returns the invoice to
    # NeedsReview (approval-invalidating edits always do).
    from dz_erp.procurement_stock import modify_draft_invoice_line

    invoice = _make_invoice(
        db,
        invoice_no="FAC-2026-0004",
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "discount_pct": "10.00",
                "tax_rate": "19", "line_ht_dzd": "900.00"}],
        amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00",
    )
    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?",
            (invoice["invoice_id"],),
        ).fetchone()["id"]
    modified = modify_draft_invoice_line(
        db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"],
        line_id=line_id, new_unit_price_dzd="550.00",
    )
    assert modified["workflow_state"] == "NeedsReview"
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT discount_pct, line_ht_dzd FROM supplier_invoice_lines WHERE id = ?",
            (line_id,),
        ).fetchone()
    assert row["discount_pct"] == "10.00"
    assert row["line_ht_dzd"] == "990.00"


def test_legacy_invoice_without_document_is_unaffected(tmp_path: Path) -> None:
    """The fail-closed gate must not slow the fully-typed, document-less flow."""
    db, _vault, _ = _make_env(tmp_path)
    invoice = _make_invoice(db)
    submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True)
    posted = post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=invoice["invoice_id"], idempotency_key="legacy-1"
    )
    assert posted["workflow_state"] == "Posted"


# ---------------------------------------------------------------------------
# Fail-closed field semantics: missing, unmeasured, low-confidence, conflicts
# ---------------------------------------------------------------------------


def test_required_field_cannot_be_asserted_absent_but_optional_can(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)  # synthetic spec has no timbre line -> timbre absent
    present = {f["field_name"] for f in db.list_extracted_fields(1, doc["document_id"])}
    assert "amount_timbre_dzd" not in present

    # Optional and genuinely absent: honest NOT_PRESENT is accepted.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="amount_timbre_dzd", decision=DECISION_NOT_PRESENT,
    )
    # Required but absent (nothing to assert away): refused.
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM ocr_extracted_fields WHERE company_id = 1 AND document_id = ?"
            " AND field_name = 'supplier_nif'",
            (doc["document_id"],),
        )
    with pytest.raises(ValueError, match="FIELD_REQUIRED_CANNOT_BE_ABSENT"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="supplier_nif", decision=DECISION_NOT_PRESENT,
        )


def test_manual_transcription_route_is_satisfiable_with_honest_provenance(tmp_path: Path) -> None:
    """The Arabic-only/no-fields route ends in a postable invoice via transcription.

    Every transcribed value is a *human* entry: ``human-manual-entry`` model,
    raw text "[manual entry]", no measured confidence — never a fabricated
    engine reading.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_doc(db, vault, _blank_pdf())
    assert doc["extraction_route"] == "manual_transcription"
    assert doc["fields"] == {}
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["requires_manual_transcription"] is True
    assert evaluation["satisfied"] is False
    assert len(evaluation["unmet_required"]) == len(_REQUIRED_HEADER_FIELDS)

    transcribed = {
        "invoice_number": "FAC-AR-2026-77",
        "supplier_name": "مؤسسة النور",
        "supplier_nif": "000216001234567",
        "invoice_date": "2026-02-20",
        "currency_code": "DZD",
        "amount_ht_dzd": "500.00",
        "amount_tva_dzd": "95.00",
        "amount_ttc_dzd": "595.00",
    }
    for field_name, value in transcribed.items():
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=field_name, decision=DECISION_CORRECTED, value=value,
        )
    for optional in ("supplier_nis", "supplier_rc", "supplier_ai", "amount_timbre_dzd", "due_date"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=optional, decision=DECISION_NOT_PRESENT,
        )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"], evaluation["blocking"]

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_extracted_fields WHERE document_id = ? AND field_name = 'supplier_name'",
            (doc["document_id"],),
        ).fetchone()
    assert row["raw_text"] == "[manual entry]"
    assert row["confidence"] is None
    assert row["confidence_kind"] == "not_measured"
    assert row["confidence_scope"] == "unavailable"
    assert row["confidence_calibrated"] == 0
    assert row["ocr_model_version"] == "human-manual-entry"
    assert row["human_override_value"] == "مؤسسة النور"

    # And the invoice lifecycle runs on the transcribed document.
    invoice = _make_invoice(
        db, doc["document_id"], invoice_no="FAC-AR-2026-77",
        invoice_date="2026-02-20", line_qty="1",
        amount_ht="500.00", amount_tva="95.00", amount_ttc="595.00",
    )
    submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    approved = decide_invoice_approval(
        db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True
    )
    assert approved["workflow_state"] == "Approved"


def test_low_confidence_is_flagged_but_still_requires_a_decision(tmp_path: Path) -> None:
    """A measured 0.31 marks the field for harder scrutiny; it never accepts it.

    The measured row is inserted directly to simulate what the OCR path stores
    (the immutability trigger forbids retro-fitting a score onto a stored row);
    the provenance triple is exactly what intake writes for a scanned line.
    """
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_doc(db, vault, _blank_pdf())
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ocr_extracted_fields (
                company_id, document_id, page_number, line_index, field_name,
                raw_text, normalized_value, confidence, confidence_kind, confidence_scope,
                confidence_calibrated, bbox_json, ocr_model_version, created_at
            ) VALUES (1, ?, 1, NULL, 'supplier_rc', '16/00-0991216B12', '16/00-0991216B12',
                      0.31, 'engine_line', 'line', 0, '[]', 'rapidocr-1.4.4-ppocrv4', ?)
            """,
            (doc["document_id"], now_iso),
        )

    evaluation = _evaluate(db, 1, doc["document_id"])
    rc = next(f for f in evaluation["fields"] if f["field_name"] == "supplier_rc")
    assert rc["confidence"] == 0.31
    assert rc["confidence_kind"] == "engine_line"
    assert "low_confidence" in rc["flags"]
    # Below threshold and above it alike, the field still needs a decision.
    assert evaluation["satisfied"] is False

    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="supplier_rc", decision="CONFIRMED", expected_value="16/00-0991216B12",
    )
    for field_name, value in {
        "invoice_number": "FAC-LC-1", "supplier_name": "SPARTE SARL",
        "supplier_nif": "000216001234567", "invoice_date": "2026-02-20",
        "currency_code": "DZD", "amount_ht_dzd": "500.00", "amount_tva_dzd": "95.00",
        "amount_ttc_dzd": "595.00",
    }.items():
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=field_name, decision=DECISION_CORRECTED, value=value,
        )
    for optional in ("supplier_nis", "supplier_ai", "amount_timbre_dzd", "due_date"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=optional, decision=DECISION_NOT_PRESENT,
        )
    assert _evaluate(db, 1, doc["document_id"])["satisfied"]
    # The measured score and its provenance were never touched by the decision.
    row = next(
        f for f in db.list_extracted_fields(1, doc["document_id"]) if f["field_name"] == "supplier_rc"
    )
    assert row["confidence"] == 0.31 and row["confidence_kind"] == "engine_line"


def test_conflicting_extraction_is_recorded_not_dropped(tmp_path: Path) -> None:
    """A second, different reading of the same field is evidence, not noise.

    Unit level: the parser keeps the first reading and records the later
    different one as a conflict. Document level: the conflict is flagged,
    a decision on the current value still satisfies the gate, and the
    conflicting reading stays visible beside the confirmed one.
    """
    lines = [
        ("TOTAL HT: 1000.00 DZD", None, [[0.0, 0.0], [500.0, 0.0], [500.0, 15.0], [0.0, 15.0]]),
        ("TOTAL TTC: 1190.00 DZD", None, [[0.0, 30.0], [500.0, 30.0], [500.0, 45.0], [0.0, 45.0]]),
        ("TOTAL TTC: 1250.00 DZD", None, [[0.0, 60.0], [500.0, 60.0], [500.0, 75.0], [0.0, 75.0]]),
    ]
    parsed = parse_structured_invoice_text(lines, confidence_kind="not_measured")
    ttc = parsed["fields"]["amount_ttc_dzd"]
    assert ttc["normalized_value"] == "1190.00"
    conflicts = json.loads(ttc["conflict_values_json"])
    assert conflicts == [{"raw_text": "TOTAL TTC: 1250.00", "normalized_value": "1250.00"}]

    # Document level: the same field with a stored conflict.
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_doc(db, vault, _blank_pdf())
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ocr_extracted_fields (
                company_id, document_id, page_number, line_index, field_name,
                raw_text, normalized_value, confidence, confidence_kind, confidence_scope,
                confidence_calibrated, bbox_json, conflict_values_json, ocr_model_version, created_at
            ) VALUES (1, ?, 1, NULL, 'amount_ttc_dzd', 'TOTAL TTC: 1190.00 DZD', '1190.00',
                      NULL, 'not_measured', 'unavailable', 0, '[]',
                      '[{"raw_text": "TOTAL TTC: 1250.00 DZD", "normalized_value": "1250.00"}]',
                      'pdfplumber-0.11.6+pdfminer', ?)
            """,
            (doc["document_id"], now_iso),
        )
    evaluation = _evaluate(db, 1, doc["document_id"])
    ttc_eval = next(f for f in evaluation["fields"] if f["field_name"] == "amount_ttc_dzd")
    assert "conflicting_extraction" in ttc_eval["flags"]

    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="amount_ttc_dzd", decision="CONFIRMED", expected_value="1190.00",
    )
    for field_name, value in {
        "invoice_number": "FAC-CF-1", "supplier_name": "SPARTE SARL",
        "supplier_nif": "000216001234567", "invoice_date": "2026-02-20",
        "currency_code": "DZD", "amount_ht_dzd": "1000.00", "amount_tva_dzd": "190.00",
    }.items():
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=field_name, decision=DECISION_CORRECTED, value=value,
        )
    for optional in ("supplier_nis", "supplier_rc", "supplier_ai", "amount_timbre_dzd", "due_date"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=optional, decision=DECISION_NOT_PRESENT,
        )
    assert _evaluate(db, 1, doc["document_id"])["satisfied"]
    # The conflicting reading is still there for the auditor.
    row = next(
        f for f in db.list_extracted_fields(1, doc["document_id"]) if f["field_name"] == "amount_ttc_dzd"
    )
    assert "1250.00" in row["conflict_values_json"]


def test_end_to_end_conflicting_totals_reach_the_database(tmp_path: Path) -> None:
    """A rendered page with two different TTC totals stores the contradiction."""
    db, vault, _ = _make_env(tmp_path)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=True)
    c.setFont("Helvetica", 11)
    c.drawString(45, 780, "TOTAL HT: 1000.00 DZD")
    c.drawString(45, 760, "TOTAL TVA: 190.00 DZD")
    c.drawString(45, 740, "TOTAL TTC: 1190.00 DZD")
    c.drawString(45, 700, "TOTAL TTC: 1250.00 DZD")
    c.save()
    doc = _upload_doc(db, vault, buf.getvalue())
    row = next(
        f for f in db.list_extracted_fields(1, doc["document_id"]) if f["field_name"] == "amount_ttc_dzd"
    )
    assert row["normalized_value"] == "1190.00"
    assert "1250.00" in row["conflict_values_json"]


# ---------------------------------------------------------------------------
# Extracted-totals arithmetic
# ---------------------------------------------------------------------------


def test_inconsistent_extracted_totals_block_even_when_every_field_was_confirmed(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    # A confident misread: the human edits TTC to a value that no longer sums.
    update_extracted_field_by_human(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="amount_ttc_dzd", new_value="1200.00",
    )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["totals"]["inconsistent"] is True
    assert any(b.startswith("INCONSISTENT_EXTRACTED_TOTALS") for b in evaluation["blocking"])
    with pytest.raises(FinancialIntegrityError, match="INCONSISTENT_EXTRACTED_TOTALS"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # Correcting the value back re-satisfies both the arithmetic and the
    # (now stale) decision, because CORRECTED re-binds the decision.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="amount_ttc_dzd", decision=DECISION_CORRECTED, value="1190.00",
    )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["totals"]["inconsistent"] is False
    assert evaluation["satisfied"] is True
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"


def test_totals_rounding_boundary_and_the_timbre_note(tmp_path: Path) -> None:
    """Exact 2-decimal sums pass; the missing timbre is a named, benign gap."""
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_doc(db, vault, _blank_pdf())
    for field_name, value in {
        "invoice_number": "FAC-RND-1", "supplier_name": "SPARTE SARL",
        "supplier_nif": "000216001234567", "invoice_date": "2026-02-20",
        "currency_code": "DZD", "amount_ht_dzd": "33.33", "amount_tva_dzd": "6.33",
        "amount_ttc_dzd": "39.66",
    }.items():
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=field_name, decision=DECISION_CORRECTED, value=value,
        )
    for optional in ("supplier_nis", "supplier_rc", "supplier_ai", "amount_timbre_dzd", "due_date"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=optional, decision=DECISION_NOT_PRESENT,
        )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["totals"]["evaluable"] is True
    assert evaluation["totals"]["inconsistent"] is False
    assert evaluation["totals"]["timbre_present"] is False
    assert evaluation["satisfied"] is True


def test_transcribed_timbre_closes_the_gap_only_when_it_sums(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_doc(db, vault, _blank_pdf())
    for field_name, value in {
        "invoice_number": "FAC-TB-1", "supplier_name": "SPARTE SARL",
        "supplier_nif": "000216001234567", "invoice_date": "2026-02-20",
        "currency_code": "DZD", "amount_ht_dzd": "1000.00", "amount_tva_dzd": "190.00",
        "amount_ttc_dzd": "1190.00",
    }.items():
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=field_name, decision=DECISION_CORRECTED, value=value,
        )
    for optional in ("supplier_nis", "supplier_rc", "supplier_ai", "due_date"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name=optional, decision=DECISION_NOT_PRESENT,
        )
    assert _evaluate(db, 1, doc["document_id"])["satisfied"]

    # A transcribed timbre of 10.00 breaks the sum: the gap was not the timbre.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="amount_timbre_dzd", decision=DECISION_CORRECTED, value="10.00",
    )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["totals"]["timbre_present"] is True
    assert evaluation["totals"]["inconsistent"] is True
    assert evaluation["satisfied"] is False


# ---------------------------------------------------------------------------
# Decisions: staleness, flags, lines, idempotency, concurrency
# ---------------------------------------------------------------------------


def test_decision_bound_to_value_goes_stale_after_an_edit(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    update_extracted_field_by_human(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="supplier_name", new_value="SPARTE SARL EURL",
    )
    evaluation = _evaluate(db, 1, doc["document_id"])
    name_eval = next(f for f in evaluation["fields"] if f["field_name"] == "supplier_name")
    assert "decision_stale" in name_eval["flags"]
    assert name_eval["satisfied"] is False
    assert "FIELD_FLAGGED:supplier_name" in evaluation["blocking"]

    with pytest.raises(FinancialIntegrityError, match="FIELD_FLAGGED:supplier_name"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="supplier_name", decision=DECISION_CORRECTED, value="SPARTE SARL EURL",
    )
    assert _evaluate(db, 1, doc["document_id"])["satisfied"]


def test_flagged_field_blocks_until_superseded(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="supplier_nif", decision=DECISION_FLAGGED, note="stamp overlaps digits",
    )
    evaluation = _evaluate(db, 1, doc["document_id"])
    assert evaluation["satisfied"] is False
    assert "FIELD_FLAGGED:supplier_nif" in evaluation["blocking"]
    with pytest.raises(FinancialIntegrityError, match="FIELD_FLAGGED:supplier_nif"):
        submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])

    # Superseding with CONFIRMED on the exact current value clears the flag.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="supplier_nif", decision="CONFIRMED", expected_value="000216001234567",
    )
    assert _evaluate(db, 1, doc["document_id"])["satisfied"]
    submitted = submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    assert submitted["workflow_state"] == "PendingApproval"


def test_line_quantity_decision_binds_canonically_and_correction_recomputes(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)

    # "2" extracted, canonical "2.000000": the reviewer states either form.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_item_json", decision="CONFIRMED", expected_value="2", line_index=0,
    )
    with pytest.raises(FinancialIntegrityError, match="FIELD_VALUE_CHANGED_SINCE_READ"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="line_item_json", decision="CONFIRMED", expected_value="7", line_index=0,
        )

    # Correction rewrites the quantity and recomputes the line amount.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_item_json", decision=DECISION_CORRECTED, value="3", line_index=0,
    )
    row = next(
        f for f in db.list_extracted_fields(1, doc["document_id"])
        if f["field_name"] == "line_item_json" and f["line_index"] == 0
    )
    payload = json.loads(row["normalized_value"])
    assert payload["qty"] == "3.000000"
    assert payload["line_ht_dzd"] == "1500.00"
    # The decision is bound to the corrected value; the prior CONFIRMED and
    # this new one are distinct events, and re-confirming it again replays.
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="line_item_json", decision="CONFIRMED", expected_value="3.000000", line_index=0,
    )
    with db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM ocr_field_reviews WHERE document_id = ?"
            " AND field_name = 'line_item_json' AND decision = 'CONFIRMED'",
            (doc["document_id"],),
        ).fetchone()["c"]
    assert count == 2


def test_decisions_are_idempotent_and_concurrency_is_refused(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)

    first = decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
    )
    assert first["idempotent_replay"] is False
    replay = decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
    )
    assert replay["idempotent_replay"] is True
    assert replay["reviewed_at"] == first["reviewed_at"]

    # The same decision from a *different* reviewer is a new record, not a replay.
    second = decide_extracted_field_review(
        db, company_id=1, user_id=4, document_id=doc["document_id"],
        field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
    )
    assert second["idempotent_replay"] is False

    # A concurrent value change between the reviewer's read and their confirm
    # is refused, writes nothing, and leaves the chain intact.
    update_extracted_field_by_human(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="invoice_number", new_value="FAC-2026-0001-B",
    )
    with pytest.raises(FinancialIntegrityError, match="FIELD_VALUE_CHANGED_SINCE_READ"):
        decide_extracted_field_review(
            db, company_id=1, user_id=4, document_id=doc["document_id"],
            field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
        )
    with db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM ocr_field_reviews WHERE document_id = ?",
            (doc["document_id"],),
        ).fetchone()["c"]
        value = conn.execute(
            "SELECT normalized_value FROM ocr_extracted_fields WHERE document_id = ?"
            " AND field_name = 'invoice_number'",
            (doc["document_id"],),
        ).fetchone()["normalized_value"]
    assert count == 2  # exactly the two committed decisions
    assert value == "FAC-2026-0001-B"
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_every_decision_and_denial_is_audited_with_identity(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id=doc["document_id"],
        field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
        note="matches paper",
    )
    with db.transaction() as conn:
        events = conn.execute(
            "SELECT actor_user_id, action_type, target_model FROM audit_events"
            " WHERE action_type = 'OCR_FIELD_REVIEW_DECIDED'"
        ).fetchall()
    assert len(events) == 1
    assert events[0]["actor_user_id"] == 2
    assert events[0]["target_model"] == "ocr_field_reviews"
    assert db.verify_audit_chain(company_id=1)["valid"] is True


# ---------------------------------------------------------------------------
# Role, state and tenant gates
# ---------------------------------------------------------------------------


def test_role_and_state_gates_on_decisions_and_edits_are_audited(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])

    # A warehouse role cannot review fields (audited, committed, then refused).
    with pytest.raises(AuthorizationError, match="not authorized to review"):
        decide_extracted_field_review(
            db, company_id=1, user_id=5, document_id=doc["document_id"],
            field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
        )
    with pytest.raises(AuthorizationError, match="not authorized to review"):
        update_extracted_field_by_human(
            db, company_id=1, user_id=5, document_id=doc["document_id"],
            field_name="invoice_number", new_value="X",
        )
    blocks = [a for a in _audit_actions(db) if a[1] == "SECURITY_FIELD_REVIEW_BLOCKED"]
    assert blocks and blocks[0][0] == 5
    edit_blocks = [a for a in _audit_actions(db) if a[1] == "SECURITY_FIELD_EDIT_BLOCKED"]
    assert edit_blocks and edit_blocks[0][0] == 5
    assert db.verify_audit_chain(company_id=1)["valid"] is True

    # Decisions stay open only while the document is NeedsReview/Validated.
    _confirm_extracted(db, 1, doc["document_id"])
    submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    with pytest.raises(FinancialIntegrityError, match="FIELD_REVIEW_CLOSED"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="supplier_name", decision="CONFIRMED", expected_value="SPARTE SARL",
        )
    with pytest.raises(FinancialIntegrityError, match="FIELD_EDIT_CLOSED"):
        update_extracted_field_by_human(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="supplier_name", new_value="Anything",
        )
    state_blocks = [
        a for a in _audit_actions(db)
        if a[1] in ("SECURITY_FIELD_REVIEW_BLOCKED", "SECURITY_FIELD_EDIT_BLOCKED")
        and a[2] == "source_documents" and a[3] == doc["document_id"]
    ]
    assert len(state_blocks) == 2, state_blocks


def test_inactive_user_cannot_decide(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    with db.transaction() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = 2")
    with pytest.raises(AuthorizationError, match="inactive"):
        decide_extracted_field_review(
            db, company_id=1, user_id=2, document_id=doc["document_id"],
            field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
        )


def test_cross_tenant_decisions_are_impossible_and_reads_are_audited(tmp_path: Path) -> None:
    from dz_erp.security_audit import evaluate_document_review_policy_secure

    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)

    # Tenant 2's clerk cannot even load the document, let alone decide on it.
    with pytest.raises((TenantIsolationError, ValueError)):
        decide_extracted_field_review(
            db, company_id=2, user_id=21, document_id=doc["document_id"],
            field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-2026-0001",
        )
    with pytest.raises(TenantIsolationError, match="Cross-company"):
        evaluate_document_review_policy_secure(db, 21, doc["document_id"])
    blocks = [a for a in _audit_actions(db) if a[1] == "SECURITY_CROSS_COMPANY_READ_BLOCKED"]
    assert len(blocks) == 1, blocks
    assert db.verify_audit_chain(company_id=2)["valid"] is True

    # The tenant's own data is untouched.
    with db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM ocr_field_reviews WHERE document_id = ?",
            (doc["document_id"],),
        ).fetchone()["c"]
    assert count == 0


# ---------------------------------------------------------------------------
# Document-link whitelist and migration
# ---------------------------------------------------------------------------


def test_document_link_state_whitelist(tmp_path: Path) -> None:
    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])  # doc: NeedsReview -> Validated
    _confirm_extracted(db, 1, doc["document_id"])
    submit_invoice_for_approval(db, company_id=1, user_id=2, invoice_id=invoice["invoice_id"])
    # doc is now PendingApproval: a fresh draft may not cite it.
    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_LINK_STATE_REFUSED"):
        _make_invoice(db, doc["document_id"], invoice_no="FAC-2026-0002")
    # ...and the original document state was never clobbered.
    with db.transaction() as conn:
        state = conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ?", (doc["document_id"],)
        ).fetchone()["workflow_state"]
    assert state == "PendingApproval"


def test_migration_upgrades_legacy_database_preserving_rows(tmp_path: Path) -> None:
    """A pre-phase-29 database gains the column, table and trigger; data survives.

    The legacy file is a *real* current-schema database with the phase-29
    objects removed, so the full migration chain runs exactly as it would on
    a production database that predates this phase — not against a hand-written
    minimal schema that might diverge from what the migrations assume.
    """
    legacy_path = tmp_path / "legacy.sqlite3"
    ERPDatabase(str(legacy_path))  # creates the current schema, then closes

    conn = sqlite3.connect(str(legacy_path))
    conn.row_factory = sqlite3.Row
    # Strip the phase-29 objects: the decisions table, and the conflict column
    # plus the trigger that protects it (SQLite refuses DROP COLUMN while a
    # trigger references the column, so the trigger goes first).
    conn.execute("DROP TRIGGER IF EXISTS trg_ocr_field_preserve_raw")
    conn.execute("ALTER TABLE ocr_extracted_fields DROP COLUMN conflict_values_json")
    conn.execute("DROP TABLE ocr_field_reviews")
    # A legacy tenant with one extracted document awaiting review.
    conn.execute(
        "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
        " VALUES (1, 'DZ1', 'Legacy', 'DZ', 'dz', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
        " VALUES (2, 1, 'clerk', 'clerk', 1, 'legacy-hash')"
    )
    conn.execute(
        "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, page_count,"
        " file_sha256, vault_path, security_status, workflow_state, uploaded_by_id, uploaded_at)"
        " VALUES ('doc-1', 1, 'old.pdf', 'application/pdf', 10, 1, 'legacy-sha', 'vault/old',"
        " 'CLEAN', 'NeedsReview', 2, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO ocr_extracted_fields (company_id, document_id, page_number, line_index,"
        " field_name, raw_text, normalized_value, confidence, confidence_kind, confidence_scope,"
        " confidence_calibrated, bbox_json, ocr_model_version, created_at)"
        " VALUES (1, 'doc-1', 1, NULL, 'invoice_number', 'FAC-1', 'FAC-1', NULL,"
        " 'not_measured', 'unavailable', 0, '[]', 'pdfplumber', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    # Re-opening runs the (idempotent) migrations over the legacy file.
    db = ERPDatabase(str(legacy_path))
    with db.transaction() as c2:
        ef_cols = {r["name"] for r in c2.execute("PRAGMA table_info(ocr_extracted_fields)").fetchall()}
        assert "conflict_values_json" in ef_cols
        tables = {
            r["name"]
            for r in c2.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "ocr_field_reviews" in tables
        row = c2.execute(
            "SELECT field_name, raw_text, confidence FROM ocr_extracted_fields WHERE document_id = 'doc-1'"
        ).fetchone()
        assert (row["field_name"], row["raw_text"], row["confidence"]) == ("invoice_number", "FAC-1", None)
    # The refreshed trigger also protects the new column. Probed on a raw
    # connection: RAISE(ABORT) would otherwise abort the enclosing
    # db.transaction() context rather than stay a contained refusal.
    probe = sqlite3.connect(str(legacy_path))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="OCR_RAW_IMMUTABILITY_VIOLATION"):
            probe.execute(
                "UPDATE ocr_extracted_fields SET conflict_values_json = '[]' WHERE document_id = 'doc-1'"
            )
    finally:
        probe.close()
    # Decisions work on the migrated database.
    decision = decide_extracted_field_review(
        db, company_id=1, user_id=2, document_id="doc-1",
        field_name="invoice_number", decision="CONFIRMED", expected_value="FAC-1",
    )
    assert decision["decision"] == "CONFIRMED"


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_api_decide_endpoint_contract(tmp_path: Path) -> None:
    db, vault, client = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    clerk = _bearer(_login(client, "clerk_a", "Clerk#2026x"))
    auditor = _bearer(_login(client, "auditor_a", "Aud#2026x"))
    other = _bearer(_login(client, "clerk_b", "ClerkB#2026x"))

    path = f"/api/v1/documents/{doc['document_id']}/fields/decide"
    assert client.post(path, json={"field_name": "invoice_number", "decision": "CONFIRMED"}).status_code == 401
    assert client.post(
        path, json={"field_name": "invoice_number", "decision": "CONFIRMED", "expected_value": "FAC-2026-0001"},
        headers=auditor,
    ).status_code == 403

    ok = client.post(
        path,
        json={"field_name": "invoice_number", "decision": "CONFIRMED", "expected_value": "FAC-2026-0001"},
        headers=clerk,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["decision"] == "CONFIRMED"
    replay = client.post(
        path,
        json={"field_name": "invoice_number", "decision": "CONFIRMED", "expected_value": "FAC-2026-0001"},
        headers=clerk,
    )
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True

    # Contract violations are 422 with the stable reason prefix, not 500s.
    assert client.post(path, json={"field_name": "invoice_number", "decision": "MAYBE"}, headers=clerk).status_code == 422
    assert client.post(
        path, json={"field_name": "amount_ttc_dzd", "decision": "CORRECTED", "value": "not-a-number"},
        headers=clerk,
    ).status_code == 422
    assert client.post(
        path, json={"field_name": "amount_ttc_dzd", "decision": "NOT_PRESENT"}, headers=clerk,
    ).status_code == 422
    # Unknown field: transcribing it is allowed (bounded text), flagging is not.
    assert client.post(
        path, json={"field_name": "made_up_field", "decision": "FLAGGED"}, headers=clerk,
    ).status_code == 422
    # A foreign document is indistinguishable from a missing one.
    assert client.post(
        path, json={"field_name": "invoice_number", "decision": "CONFIRMED", "expected_value": "x"},
        headers=other,
    ).status_code == 404


def test_api_review_policy_endpoint(tmp_path: Path) -> None:
    db, vault, client = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    clerk = _bearer(_login(client, "clerk_a", "Clerk#2026x"))
    other = _bearer(_login(client, "clerk_b", "ClerkB#2026x"))

    path = f"/api/v1/documents/{doc['document_id']}/review-policy"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=other).status_code == 403
    assert client.get("/api/v1/documents/nope/review-policy", headers=clerk).status_code == 404

    body = client.get(path, headers=clerk).json()
    assert body["satisfied"] is False
    assert set(body["unmet_required"]) >= set(_REQUIRED_HEADER_FIELDS)
    assert body["totals"]["evaluable"] is True
    assert all(
        {"field_name", "status", "flags", "decision", "satisfied"} <= set(f) for f in body["fields"]
    )
    # The cross-company read attempt was audited in the *victim's* chain.
    blocks = [a for a in _audit_actions(db) if a[1] == "SECURITY_CROSS_COMPANY_READ_BLOCKED"]
    assert len(blocks) == 1


# ---------------------------------------------------------------------------
# F-R2-6 — a missing reading is reported as missing, not as "unparseable"
# ---------------------------------------------------------------------------
def test_fr2_6_a_missing_decided_price_or_discount_is_its_own_refusal(tmp_path: Path) -> None:
    """The old ``None`` test sat *after* the parse, so it could never fire.

    ``to_decimal(str(None))`` raises, so a decided-but-absent reading was
    reported as ``..._UNPARSEABLE`` and the ``None`` branch was dead code. The
    refusal is now specific, which matters because the two mean different
    things to the clerk: a missing reading is re-decided, an unparseable one is
    re-transcribed.
    """
    from dz_erp.procurement_stock import _source_invoice_mismatches

    db, vault, _ = _make_env(tmp_path)
    doc = _upload_standard_doc(db, vault)
    invoice = _make_invoice(db, doc["document_id"])
    _confirm_extracted(db, 1, doc["document_id"])

    evaluation = _evaluate(db, 1, doc["document_id"])
    with db.transaction() as conn:
        invoice_row = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)
        ).fetchone()

    def _mismatches(ev):
        with db.transaction() as conn:
            return _source_invoice_mismatches(conn, 1, invoice_row, ev)

    # Baseline: the confirmed evidence matches.
    assert _mismatches(evaluation) == []

    # A decided-but-absent price reading.
    broken_price = {
        **evaluation,
        "line_prices": [
            {**p, "value": None} if i == 0 else p for i, p in enumerate(evaluation["line_prices"])
        ],
    }
    price_mismatches = _mismatches(broken_price)
    assert "LINE_1_PRICE_SOURCE_MISSING" in price_mismatches, price_mismatches
    assert not any("PRICE_SOURCE_UNPARSEABLE" in m for m in price_mismatches), price_mismatches

    # A decided-but-absent discount reading.
    broken_discount = {
        **evaluation,
        "line_discounts": [
            {**d, "value": None} if i == 0 else d for i, d in enumerate(evaluation["line_discounts"])
        ],
    }
    discount_mismatches = _mismatches(broken_discount)
    assert "LINE_1_DISCOUNT_SOURCE_MISSING" in discount_mismatches, discount_mismatches
    assert not any("DISCOUNT_SOURCE_UNPARSEABLE" in m for m in discount_mismatches), discount_mismatches

    # A genuinely unparseable reading keeps its own, different code.
    garbage = {
        **evaluation,
        "line_prices": [
            {**p, "value": "not-a-number"} if i == 0 else p
            for i, p in enumerate(evaluation["line_prices"])
        ],
    }
    assert "LINE_1_PRICE_SOURCE_UNPARSEABLE" in _mismatches(garbage)
