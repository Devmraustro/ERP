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
):
    line_ht = str((Decimal(line_qty) * Decimal(unit_price)).quantize(Decimal("0.01")))
    return create_and_validate_supplier_invoice(
        db=db, company_id=1, user_id=user_id, supplier_id=7,
        supplier_invoice_no=invoice_no, invoice_date=invoice_date,
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": line_qty,
                "unit_price_dzd": unit_price, "tax_rate": "19", "line_ht_dzd": line_ht}],
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
    assert len(rows) >= 9  # 8 required header fields + 1 line quantity (+ NOT_PRESENTs)
    # Confirmed and honest-absent only: no correction, flag or stale record.
    assert {r["decision"] for r in rows} <= {"CONFIRMED", "NOT_PRESENT"}
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
