# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 34 — duplicate extraction rows must never be silently collapsed.

Phase 29 made the field review *enforced*: no invoice may proceed while a
required field is undecided, flagged or stale. That guarantee rested on one
assumption — that a logical field is represented by exactly one row in
``ocr_extracted_fields``. Nothing enforced it. The policy grouped rows by field
name, evaluated ``rows[0]``, appended ``conflicting_extraction`` to the flags and
then **never consulted that flag for satisfaction**, so a second row with a
different amount was accepted, validated and submitted for approval.

This matrix pins the corrected behaviour:

* a duplicate group is its own blocking reason (``DUPLICATE_EXTRACTION_ROWS``)
  and is reported with the row ids, never silently reduced to the first row;
* the reviewer resolves the group explicitly — the decision records every row id
  and the row selected as authoritative, so a decision made over one row can
  never authorise another that appeared later;
* a recorded alternate reading (``conflict_values_json``, produced at intake)
  stays a separate, still-enforced concept from duplicate *database* rows;
* submission, approval and posting all enforce the same rule, because they share
  one gate — and posting re-runs it;
* tenant isolation is preserved, and the additive evidence column migrates
  in place without touching a stored row.
"""

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    BLOCK_DUPLICATE_ROWS,
    DECISION_CORRECTED,
    DECISION_FLAGGED,
    FLAG_DUPLICATE_ROWS,
    evaluate_document_review_policy,
)

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

_USERS = [
    (1, 1, "admin_a", "admin", "AdminA#2026x"),
    (2, 1, "clerk_a", "clerk", "Clerk#2026x"),
    (3, 1, "approver_a", "approver", "Appr#2026x"),
    (4, 1, "acct_a", "accountant", "Acct#2026x"),
    (20, 2, "admin_b", "admin", "AdminB#2026x"),
    (21, 2, "clerk_b", "clerk", "ClerkB#2026x"),
]


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def _make_env(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "p34.sqlite3"))
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
        for pid, cid in ((7, 1), (8, 2)):
            conn.execute(
                "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
                " nif, nis, ai, rc) VALUES (?, ?, 'SPARTE', 'supplier', 'DZ', 'REEL',"
                " '000216001234567', '000216001234567', '16019912316', '16/00-0991216B12')",
                (pid, cid),
            )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)
    return db, PrivateDocumentVault(tmp_path / "vault")


@pytest.fixture
def env(tmp_path):
    db, vault = _make_env(tmp_path)
    return db, vault, TestClient(create_erp_fastapi_app(db, vault))


def _upload(db, vault, *, company_id=1, user_id=2, spec=None):
    return upload_and_process_supplier_document(
        db,
        vault,
        company_id=company_id,
        user_id=user_id,
        filename="facture.pdf",
        mime_type="application/pdf",
        content=render_synthetic_invoice_pdf(dict(spec or _SPEC)),
    )


def _evaluate(db, company_id=1, document_id=None):
    with db.transaction() as conn:
        doc = dict(
            conn.execute(
                "SELECT * FROM source_documents WHERE id = ? AND company_id = ?",
                (document_id, company_id),
            ).fetchone()
        )
        fields = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ? ORDER BY id",
                (company_id, document_id),
            ).fetchall()
        ]
        decisions = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM ocr_field_reviews WHERE company_id = ? AND document_id = ? ORDER BY id",
                (company_id, document_id),
            ).fetchall()
        ]
    return evaluate_document_review_policy(doc, fields, decisions)


def _field_ids(db, document_id, field_name, *, line_index=None, company_id=1):
    with db.transaction() as conn:
        if line_index is None:
            rows = conn.execute(
                "SELECT id FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ?"
                " AND field_name = ? AND line_index IS NULL ORDER BY page_number, id",
                (company_id, document_id, field_name),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ?"
                " AND field_name = ? AND line_index = ? ORDER BY page_number, id",
                (company_id, document_id, field_name, line_index),
            ).fetchall()
    return [int(r["id"]) for r in rows]


def _duplicate_header_row(
    db,
    document_id,
    field_name,
    value,
    *,
    page_number=1,
    company_id=1,
    raw_text=None,
):
    """Copy an extraction row and give the copy a different value (an out-of-band
    double insert: operator error, a re-extraction, or a restore merge)."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ?"
            " AND field_name = ? AND line_index IS NULL ORDER BY id LIMIT 1",
            (company_id, document_id, field_name),
        ).fetchone()
        assert row is not None, f"no extracted row for {field_name}"
        cur = conn.execute(
            """
            INSERT INTO ocr_extracted_fields (
                company_id, document_id, page_number, line_index, field_name, raw_text,
                normalized_value, confidence, confidence_kind, confidence_scope,
                confidence_calibrated, bbox_json, conflict_values_json, ocr_model_version, created_at
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, NULL, 'not_measured', 'unavailable', 0, ?, NULL,
                      'duplicate-row-probe', '2026-01-01T00:00:00+00:00')
            """,
            (
                company_id,
                document_id,
                page_number,
                field_name,
                raw_text if raw_text is not None else f"{field_name}: {value}",
                value,
                row["bbox_json"],
            ),
        )
        return int(cur.lastrowid)


def _duplicate_line_row(db, document_id, line_index, mutate, *, page_number=1, company_id=1):
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM ocr_extracted_fields WHERE company_id = ? AND document_id = ?"
            " AND field_name = 'line_item_json' AND line_index = ? ORDER BY id LIMIT 1",
            (company_id, document_id, line_index),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["normalized_value"])
        mutate(payload)
        cur = conn.execute(
            """
            INSERT INTO ocr_extracted_fields (
                company_id, document_id, page_number, line_index, field_name, raw_text,
                normalized_value, confidence, confidence_kind, confidence_scope,
                confidence_calibrated, bbox_json, conflict_values_json, ocr_model_version, created_at
            ) VALUES (?, ?, ?, ?, 'line_item_json', ?, ?, NULL, 'not_measured', 'unavailable', 0, ?,
                      NULL, 'duplicate-row-probe', '2026-01-01T00:00:00+00:00')
            """,
            (
                company_id,
                document_id,
                page_number,
                line_index,
                row["raw_text"],
                json.dumps(payload, ensure_ascii=False),
                row["bbox_json"],
            ),
        )
        return int(cur.lastrowid)


def _confirm_all(db, company_id, document_id, user_id=2, *, skip=()):
    """Confirm every currently evaluated field and line element except ``skip``."""
    ev = _evaluate(db, company_id, document_id)
    for fev in ev["fields"]:
        if fev["field_name"] in skip:
            continue
        if fev["status"] == "missing":
            if not fev["required"]:
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
    for group, name in (
        (ev["lines"], "line_item_json"),
        (ev["line_prices"], "line_unit_price"),
        (ev["line_discounts"], "line_discount"),
    ):
        for lev in group:
            if name in skip:
                continue
            if lev["value"] is None:
                # A discount is never defaulted to zero by the system; the
                # reviewer transcribes the honest 0.00 when the paper has none.
                if name == "line_discount":
                    decide_extracted_field_review(
                        db,
                        company_id=company_id,
                        user_id=user_id,
                        document_id=document_id,
                        field_name=name,
                        decision=DECISION_CORRECTED,
                        value="0.00",
                        line_index=lev["line_index"],
                    )
                continue
            decide_extracted_field_review(
                db,
                company_id=company_id,
                user_id=user_id,
                document_id=document_id,
                field_name=name,
                decision="CONFIRMED",
                expected_value=lev["value"],
                line_index=lev["line_index"],
            )
    return _evaluate(db, company_id, document_id)


def _invoice(db, document_id=None, **kwargs):
    params = dict(
        db=db,
        company_id=1,
        user_id=2,
        supplier_id=7,
        supplier_invoice_no="FAC-2026-0001",
        invoice_date="2026-02-14",
        lines=[
            {
                "description": "Widgets",
                "qty": "2",
                "unit_price_dzd": "500.00",
                "discount_pct": "0.00",
                "tax_rate": "19",
                "line_ht_dzd": "1000.00",
            }
        ],
        amount_ht_dzd="1000.00",
        amount_tva_dzd="190.00",
        amount_ttc_dzd="1190.00",
        document_id=document_id,
    )
    params.update(kwargs)
    return create_and_validate_supplier_invoice(**params)


def _blocking_of(ev):
    return [b for b in ev["blocking"] if b.startswith(BLOCK_DUPLICATE_ROWS)]


def _actions(db, company_id=1):
    with db.transaction() as conn:
        return [
            r["action_type"]
            for r in conn.execute(
                "SELECT action_type FROM audit_events WHERE company_id = ? ORDER BY id", (company_id,)
            ).fetchall()
        ]


# ---------------------------------------------------------------------------
# 1–3. Duplicate header rows
# ---------------------------------------------------------------------------
def test_1_duplicate_header_rows_with_identical_values_still_block(env):
    """Identical duplicates are record-integrity evidence, not a clean field.

    Silently accepting them would mean "one of these two rows is redundant, and
    nobody said which". The reviewer resolves the group; the policy never does.
    """
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    clean = _confirm_all(db, 1, document_id)
    assert clean["satisfied"] is True

    second = _duplicate_header_row(db, document_id, "amount_ht_dzd", "1000.00")
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    assert _blocking_of(ev) == [f"{BLOCK_DUPLICATE_ROWS}:amount_ht_dzd"]

    amount = next(f for f in ev["fields"] if f["field_name"] == "amount_ht_dzd")
    assert amount["duplicate_row_ids"] == _field_ids(db, document_id, "amount_ht_dzd")
    assert second in amount["duplicate_row_ids"]
    assert FLAG_DUPLICATE_ROWS in amount["flags"]
    assert amount["duplicate_unresolved"] is True
    assert amount["satisfied"] is False

    # The decision made before the duplicate appeared does not cover it.
    with db.transaction() as conn:
        ids = conn.execute(
            "SELECT reviewed_value, evidence_json FROM ocr_field_reviews"
            " WHERE document_id = ? AND field_name = 'amount_ht_dzd' ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
    assert json.loads(ids["evidence_json"])["row_ids"] != amount["duplicate_row_ids"]


def test_2_duplicate_header_rows_with_different_values_block_until_resolved(env):
    """The regression: a conflicting duplicate used to validate and submit."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)

    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00", raw_text="TOTAL HT: 5555.00")
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    assert f"{BLOCK_DUPLICATE_ROWS}:amount_ht_dzd" in ev["blocking"]

    # The invoice still validates on its own arithmetic — the *document* gate is
    # what refuses, and it refuses at submission.
    invoice = _invoice(db, document_id)
    assert invoice["workflow_state"] == "Validated"
    with pytest.raises(FinancialIntegrityError, match=BLOCK_DUPLICATE_ROWS):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert "INVOICE_SUBMIT_BLOCKED_UNREVIEWED" in _actions(db)

    # Nothing reached the ledger.
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"] == 0


def test_3_duplicate_rows_on_different_pages_block_and_are_all_named(env):
    """Page scope does not make a duplicate benign.

    Two pages of one document can legitimately repeat a field only when the
    reviewer says which occurrence governs the invoice. Both rows are listed, so
    the reviewer decides with the evidence in front of them.
    """
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)

    page_two = _duplicate_header_row(db, document_id, "amount_tva_dzd", "190.00", page_number=2)
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    tva = next(f for f in ev["fields"] if f["field_name"] == "amount_tva_dzd")
    assert page_two in tva["duplicate_row_ids"]
    with db.transaction() as conn:
        pages = {
            int(r["id"]): int(r["page_number"])
            for r in conn.execute(
                "SELECT id, page_number FROM ocr_extracted_fields WHERE document_id = ?"
                " AND field_name = 'amount_tva_dzd'",
                (document_id,),
            ).fetchall()
        }
    assert {pages[i] for i in tva["duplicate_row_ids"]} == {1, 2}

    # Resolution names the page-1 row explicitly and the group is closed.
    resolved = decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_tva_dzd",
        decision="CONFIRMED",
        expected_value="190.00",
        selected_row_id=_field_ids(db, document_id, "amount_tva_dzd")[0],
    )
    assert resolved["evidence"]["group_size"] == 2
    after = _evaluate(db, 1, document_id)
    assert after["satisfied"] is True, after["blocking"]


# ---------------------------------------------------------------------------
# 4–6. Duplicate line rows and resolution
# ---------------------------------------------------------------------------
def test_4_duplicate_line_rows_with_the_same_index_block_every_element(env):
    """A duplicate line payload blocks quantity, price *and* discount."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)

    _duplicate_line_row(db, document_id, 0, lambda p: p.update(qty="99"))
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    assert f"{BLOCK_DUPLICATE_ROWS}:line_item_json[0]" in ev["blocking"]
    for element in (ev["lines"], ev["line_prices"], ev["line_discounts"]):
        assert element[0]["duplicate_unresolved"] is True
        assert element[0]["duplicate_row_ids"] == _field_ids(
            db, document_id, "line_item_json", line_index=0
        )
    # The displayed value follows the deterministic first row, never the clone.
    assert ev["lines"][0]["value"] == "2.000000"

    invoice = _invoice(db, document_id)
    with pytest.raises(FinancialIntegrityError, match=BLOCK_DUPLICATE_ROWS):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])


def test_5_distinct_line_indices_are_not_duplicates(env):
    """Two real lines remain two lines — this fix must not collapse them."""
    db, vault, _client = env
    spec = dict(_SPEC)
    spec["lines"] = [
        {"sku": "SKU-1", "description": "Anchors", "qty": "1", "unit_price_dzd": "400.00",
         "tax_rate": "19", "line_ht_dzd": "400.00"},
        {"sku": "SKU-2", "description": "Bolts", "qty": "1", "unit_price_dzd": "600.00",
         "tax_rate": "19", "line_ht_dzd": "600.00"},
    ]
    document_id = _upload(db, vault, spec=spec)["document_id"]
    ev = _evaluate(db, 1, document_id)
    assert len({r["line_index"] for r in ev["lines"]}) == 2
    assert all(r["duplicate_row_ids"] == [] for r in ev["lines"])
    assert not _blocking_of(ev)

    final = _confirm_all(db, 1, document_id)
    assert final["satisfied"] is True, final["blocking"]


def test_6_explicit_reviewer_resolution_records_identity_value_and_evidence(env):
    """The resolution is attributed, bound to the row set, and re-verifiable."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    first_id = _field_ids(db, document_id, "amount_ht_dzd")[0]
    second_id = _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")

    result = decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision="CONFIRMED",
        expected_value="1000.00",
        selected_row_id=first_id,
        note="second row is a duplicated scan line; the paper says 1000.00",
    )
    evidence = result["evidence"]
    assert evidence["row_ids"] == [first_id, second_id]
    assert evidence["selected_row_id"] == first_id
    assert evidence["selected_source"] == "human_selection"
    assert evidence["group_size"] == 2
    assert result["reviewed_by_id"] == 2

    with db.transaction() as conn:
        stored = conn.execute(
            "SELECT * FROM ocr_field_reviews WHERE document_id = ? AND field_name = 'amount_ht_dzd'"
            " ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        event = conn.execute(
            "SELECT actor_user_id, after_json AS after_state_json FROM audit_events"
            " WHERE action_type = 'OCR_FIELD_REVIEW_DECIDED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert stored["reviewed_by_id"] == 2
    assert stored["reviewed_at"] and stored["note"].startswith("second row")
    assert json.loads(stored["evidence_json"]) == evidence
    assert event["actor_user_id"] == 2
    audit_evidence = json.loads(event["after_state_json"])["evidence"]
    assert audit_evidence["row_ids"] == [first_id, second_id]

    ev = _evaluate(db, 1, document_id)
    amount = next(f for f in ev["fields"] if f["field_name"] == "amount_ht_dzd")
    assert amount["duplicate_resolved"] is True
    assert amount["value"] == "1000.00"
    assert amount["row_id"] == first_id, "the selected row is what the invoice is compared against"
    assert ev["satisfied"] is True, ev["blocking"]


def test_6b_selection_cannot_name_another_documents_row(env):
    """An explicit selection must belong to the group it claims to resolve."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    other_document = _upload(db, vault, spec={**_SPEC, "invoice_number": "FAC-2026-0002"})[
        "document_id"
    ]
    _confirm_all(db, 1, document_id)
    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")
    foreign_id = _field_ids(db, other_document, "amount_tva_dzd")[0]

    with pytest.raises(FinancialIntegrityError, match="EXTRACTION_ROW_NOT_IN_GROUP"):
        decide_extracted_field_review(
            db,
            company_id=1,
            user_id=2,
            document_id=document_id,
            field_name="amount_ht_dzd",
            decision="CONFIRMED",
            expected_value="1000.00",
            selected_row_id=foreign_id,
        )
    assert _evaluate(db, 1, document_id)["satisfied"] is False


# ---------------------------------------------------------------------------
# 7–9. Unresolved duplicates block; recorded alternates still work; staleness
# ---------------------------------------------------------------------------
def test_7_unresolved_duplicates_block_every_required_field_type(env):
    """Required money, identity and date fields all block on a duplicate."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)

    _duplicate_header_row(db, document_id, "invoice_number", "FAC-2026-9ZZZ")
    _duplicate_header_row(db, document_id, "invoice_date", "2026-03-01")
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    assert sorted(_blocking_of(ev)) == [
        f"{BLOCK_DUPLICATE_ROWS}:invoice_date",
        f"{BLOCK_DUPLICATE_ROWS}:invoice_number",
    ]
    # Blocking never appears twice for the same field, and never as a plain
    # "not decided" (the reviewer needs the right diagnosis).
    assert not any(b.startswith("REQUIRED_FIELD_NOT_DECIDED:invoice_") for b in ev["blocking"])


def test_8_recorded_alternate_reading_is_a_distinct_concept(env):
    """``conflict_values_json`` is intake-recorded evidence, not a duplicate row.

    It keeps its Phase 29 behaviour — flagged, resolved by the reviewer's
    decision — while a duplicate *row* is blocked until the group is resolved.
    The two never mask each other.
    """
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)

    # ``conflict_values_json`` is extraction-time evidence and is immutable
    # afterwards (the raw-immutability trigger refuses an UPDATE), so it is
    # recorded the way intake records it: on the row, at insert. The field used
    # here is optional and does not participate in the header arithmetic.
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO ocr_extracted_fields (
                company_id, document_id, page_number, line_index, field_name, raw_text,
                normalized_value, confidence, confidence_kind, confidence_scope,
                confidence_calibrated, bbox_json, conflict_values_json, ocr_model_version, created_at
            ) VALUES (1, ?, 1, NULL, 'due_date', 'ECHEANCE: 2026-03-01',
                      '2026-03-01', NULL, 'not_measured', 'unavailable', 0, '[]',
                      ?, 'conflict-probe', '2026-01-01T00:00:00+00:00')
            """,
            (
                document_id,
                json.dumps([{"raw_text": "ECHEANCE: 2026-03-15", "normalized_value": "2026-03-15"}]),
            ),
        )

    ev = _evaluate(db, 1, document_id)
    amount = next(f for f in ev["fields"] if f["field_name"] == "due_date")
    assert "conflicting_extraction" in amount["flags"]
    assert amount["duplicate_row_ids"] == [], "an alternate reading is not a duplicate row"
    assert not _blocking_of(ev)
    # One row, one decision: the alternate reading is disclosed, not counted.
    assert len(_field_ids(db, document_id, "due_date")) == 1

    # The reviewer resolves it the Phase 29 way — by deciding on the value shown.
    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="due_date",
        decision="CONFIRMED",
        expected_value="2026-03-01",
        note="due date confirmed; the second reading was a duplicated scan line",
    )
    assert _evaluate(db, 1, document_id)["satisfied"] is True

    # A duplicate *row* on top of an alternate reading still blocks: the two
    # concepts are independent and neither masks the other.
    _duplicate_header_row(db, document_id, "due_date", "2026-03-15")
    both = _evaluate(db, 1, document_id)
    assert both["satisfied"] is False
    assert f"{BLOCK_DUPLICATE_ROWS}:due_date" in both["blocking"]


def test_9_a_row_appearing_after_resolution_stales_the_decision(env):
    """The binding is to the evidence *set*, so a later row re-opens the field."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    first = _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")
    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision="CONFIRMED",
        expected_value="1000.00",
        selected_row_id=_field_ids(db, document_id, "amount_ht_dzd")[0],
    )
    assert _evaluate(db, 1, document_id)["satisfied"] is True

    third = _duplicate_header_row(db, document_id, "amount_ht_dzd", "777.00")
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is False
    assert f"{BLOCK_DUPLICATE_ROWS}:amount_ht_dzd" in ev["blocking"]
    amount = next(f for f in ev["fields"] if f["field_name"] == "amount_ht_dzd")
    assert third in amount["duplicate_row_ids"] and first in amount["duplicate_row_ids"]
    assert amount["duplicate_resolved"] is False

    # Re-resolving over the full set is possible (no dead-end), and idempotent
    # only once the evidence no longer changes.
    resolved = decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision="CONFIRMED",
        expected_value="1000.00",
        selected_row_id=_field_ids(db, document_id, "amount_ht_dzd")[0],
    )
    assert resolved["evidence"]["group_size"] == 3
    assert _evaluate(db, 1, document_id)["satisfied"] is True
    replay = decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision="CONFIRMED",
        expected_value="1000.00",
        selected_row_id=_field_ids(db, document_id, "amount_ht_dzd")[0],
    )
    assert replay["idempotent_replay"] is True


# ---------------------------------------------------------------------------
# 10–12. Submission, approval and posting enforce the same rule
# ---------------------------------------------------------------------------
def test_10_submission_is_blocked_and_the_refusal_is_audited(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    invoice = _invoice(db, document_id)
    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")

    with pytest.raises(FinancialIntegrityError, match=BLOCK_DUPLICATE_ROWS):
        submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert "INVOICE_SUBMIT_BLOCKED_UNREVIEWED" in _actions(db)
    with db.transaction() as conn:
        state = conn.execute(
            "SELECT workflow_state FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)
        ).fetchone()["workflow_state"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE target_id = ?", (invoice["invoice_id"],)
        ).fetchone()["n"]
    assert state == "Validated" and pending == 0


def test_11_approval_is_blocked_when_a_duplicate_appears_after_submission(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    invoice = _invoice(db, document_id)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])

    _duplicate_header_row(db, document_id, "amount_ttc_dzd", "9999.00")
    with pytest.raises(FinancialIntegrityError, match=BLOCK_DUPLICATE_ROWS):
        decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)
    assert "INVOICE_APPROVAL_BLOCKED_UNREVIEWED" in _actions(db)
    with db.transaction() as conn:
        assert conn.execute(
            "SELECT workflow_state FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)
        ).fetchone()["workflow_state"] == "PendingApproval"
        assert conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"] == 0


def test_12_posting_re_runs_the_gate_so_a_late_duplicate_cannot_reach_the_ledger(env):
    """Approval happens, then the evidence changes: posting must still refuse."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    invoice = _invoice(db, document_id)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)

    # Evidence changes *after* approval: the invoice payload itself is untouched,
    # so the approved-digest check alone would let this through.
    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")
    with pytest.raises(FinancialIntegrityError, match=BLOCK_DUPLICATE_ROWS):
        post_supplier_invoice(db, 1, 4, invoice["invoice_id"], "post-1")
    assert "INVOICE_POSTING_BLOCKED_UNREVIEWED" in _actions(db)
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"] == 0
        inv_row = conn.execute(
            "SELECT workflow_state, approved_payload_sha256 FROM supplier_invoices WHERE id = ?",
            (invoice["invoice_id"],),
        ).fetchone()
        requests = [
            dict(r)
            for r in conn.execute(
                "SELECT decision, reason FROM approval_requests WHERE target_id = ?",
                (invoice["invoice_id"],),
            ).fetchall()
        ]
    # No dead-end: the approval is invalidated and the invoice returns to the
    # repairable state, with the document following it.
    assert inv_row["workflow_state"] == "NeedsReview"
    assert inv_row["approved_payload_sha256"] is None
    assert requests == [
        {"decision": "INVALIDATED", "reason": "SOURCE_REVIEW_INVALID_AT_POSTING"}
    ]
    assert _evaluate(db, 1, document_id)["satisfied"] is False
    with db.transaction() as conn:
        assert conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ?", (document_id,)
        ).fetchone()["workflow_state"] == "NeedsReview"
    assert db.verify_audit_chain(1)["valid"] is True

    # The reviewer can now see and resolve the evidence, and the normal
    # workflow (re-validate → submit → approve → post) completes once.
    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision="CONFIRMED",
        expected_value="1000.00",
        selected_row_id=_field_ids(db, document_id, "amount_ht_dzd")[0],
    )
    from dz_erp.procurement_stock import revalidate_supplier_invoice

    revalidated = revalidate_supplier_invoice(db, 1, 2, invoice["invoice_id"])
    assert revalidated["workflow_state"] == "Validated"
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)
    posted = post_supplier_invoice(db, 1, 4, invoice["invoice_id"], "post-1")
    assert posted["journal_entry_id"]
    report = db.trial_balance(1)
    assert report["balanced"] is True
    assert report["total_debit_dzd"] == report["total_credit_dzd"] == "1190.00"


# ---------------------------------------------------------------------------
# 13–14. Tenant isolation and concurrency
# ---------------------------------------------------------------------------
def test_13_duplicate_evidence_is_tenant_scoped(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    company_b_document = _upload(db, vault, company_id=2, user_id=21)["document_id"]
    _confirm_all(db, 1, document_id)
    _duplicate_header_row(db, document_id, "amount_tva_dzd", "190.00")

    # Company 2 cannot decide on company 1's document, duplicates or not. The
    # document lookup is company-scoped, so for tenant 2 the evidence simply does
    # not exist — it is never readable "because it has duplicates".
    with pytest.raises((TenantIsolationError, AuthorizationError, ValueError)) as denied:
        decide_extracted_field_review(
            db,
            company_id=2,
            user_id=21,
            document_id=document_id,
            field_name="amount_tva_dzd",
            decision="CONFIRMED",
            expected_value="190.00",
        )
    assert "not found in company 2" in str(denied.value) or isinstance(
        denied.value, (TenantIsolationError, AuthorizationError)
    )
    # A company-2 duplicate on its own document is invisible to company 1.
    _duplicate_header_row(db, company_b_document, "amount_tva_dzd", "190.00", company_id=2)
    a_blocking = _evaluate(db, 1, document_id)
    b_blocking = _evaluate(db, 2, company_b_document)
    assert a_blocking["document_id"] == document_id
    assert b_blocking["document_id"] == company_b_document
    assert all(
        f["field_name"] != "amount_tva_dzd" or f["duplicate_row_ids"] != []
        for f in a_blocking["fields"]
    )
    # Each company's duplicate set is its own.
    a_tva = next(f for f in a_blocking["fields"] if f["field_name"] == "amount_tva_dzd")
    b_tva = next(f for f in b_blocking["fields"] if f["field_name"] == "amount_tva_dzd")
    assert set(a_tva["duplicate_row_ids"]).isdisjoint(b_tva["duplicate_row_ids"])


def test_14_concurrent_review_and_submission_cannot_race_past_the_gate(env):
    """A submission racing a duplicate-resolution decision sees one state or the other.

    SQLite ``BEGIN IMMEDIATE`` serialises the two, so the outcome is always a
    coherent pair: (blocked, unresolved) or (submitted, resolved) — never a
    submission that ran against evidence the decision had not yet covered.
    """
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    first = _field_ids(db, document_id, "amount_ht_dzd")[0]
    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")
    invoice = _invoice(db, document_id)

    results = {}
    barrier = threading.Barrier(2)

    def _submit():
        barrier.wait()
        try:
            submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
            results["submit"] = "ok"
        except FinancialIntegrityError as exc:
            results["submit"] = "blocked" if BLOCK_DUPLICATE_ROWS in str(exc) else f"other:{exc}"
        except sqlite3.OperationalError as exc:  # pragma: no cover - lock contention
            results["submit"] = f"busy:{exc}"

    def _resolve():
        barrier.wait()
        try:
            decide_extracted_field_review(
                db,
                company_id=1,
                user_id=4,
                document_id=document_id,
                field_name="amount_ht_dzd",
                decision="CONFIRMED",
                expected_value="1000.00",
                selected_row_id=first,
            )
            results["resolve"] = "ok"
        except (FinancialIntegrityError, AuthorizationError) as exc:
            results["resolve"] = f"refused:{exc}"

    threads = [threading.Thread(target=_submit), threading.Thread(target=_resolve)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["resolve"] == "ok", results
    assert results["submit"] in {"ok", "blocked"}, results

    final = _evaluate(db, 1, document_id)
    assert final["satisfied"] is True, final["blocking"]
    with db.transaction() as conn:
        state = conn.execute(
            "SELECT workflow_state FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)
        ).fetchone()["workflow_state"]
        requests = conn.execute(
            "SELECT COUNT(*) AS n FROM approval_requests WHERE target_id = ?",
            (invoice["invoice_id"],),
        ).fetchone()["n"]
    # Coherent final state either way — never a half-written approval.
    assert (state, requests) in {("PendingApproval", 1), ("Validated", 0)}
    assert db.verify_audit_chain(1)["valid"] is True


# ---------------------------------------------------------------------------
# Schema compatibility
# ---------------------------------------------------------------------------
def test_15_evidence_column_migrates_idempotently_and_keeps_legacy_decisions(tmp_path):
    """A pre-Phase-34 database migrates in place; legacy decisions keep working."""
    db, vault = _make_env(tmp_path)
    path = tmp_path / "p34.sqlite3"
    document_id = _upload(db, vault)["document_id"]
    final = _confirm_all(db, 1, document_id)
    assert final["satisfied"] is True

    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(ocr_field_reviews)").fetchall()}
        assert "evidence_json" in cols
        conn.execute("ALTER TABLE ocr_field_reviews DROP COLUMN evidence_json")
        conn.commit()

    reopened = ERPDatabase(str(path))
    _ = ERPDatabase(str(path))  # a second open is a no-op
    with reopened.transaction() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ocr_field_reviews)").fetchall()}
        assert "evidence_json" in cols
        rows = [dict(r) for r in conn.execute("SELECT * FROM ocr_field_reviews").fetchall()]
    assert rows and all(r["evidence_json"] is None for r in rows)

    # Legacy NULL bindings describe the single row that existed: still satisfied.
    legacy_eval = _evaluate(reopened, 1, document_id)
    assert legacy_eval["satisfied"] is True, legacy_eval["blocking"]

    # A duplicate appearing later is *not* covered by a legacy binding.
    _duplicate_header_row(reopened, document_id, "amount_ht_dzd", "5555.00")
    assert _evaluate(reopened, 1, document_id)["satisfied"] is False

    # A decision recorded without the column still reads, and a new one binds.
    with reopened.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM ocr_field_reviews WHERE evidence_json IS NOT NULL"
        ).fetchone()["n"] == 0
    decide_extracted_field_review(
        reopened,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision="CONFIRMED",
        expected_value="1000.00",
    )
    assert _evaluate(reopened, 1, document_id)["satisfied"] is True


def test_16_a_flagged_duplicate_cannot_be_confirmed_into_satisfaction(env):
    """FLAG then CONFIRM does not launder an unresolved group.

    The flag is not a resolution mechanism: only a decision whose evidence covers
    every row closes the group.
    """
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")

    decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision=DECISION_FLAGGED,
        note="two different totals on file",
    )
    flagged = _evaluate(db, 1, document_id)
    assert flagged["satisfied"] is False
    assert f"{BLOCK_DUPLICATE_ROWS}:amount_ht_dzd" in flagged["blocking"]

    with db.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM ocr_extracted_fields WHERE document_id = ?"
            " AND field_name = 'amount_ht_dzd'",
            (document_id,),
        ).fetchone()["n"] == 2, "resolving never deletes extraction evidence"


def test_17_transcription_resolves_the_group_without_deleting_rows(env):
    """A human transcription supersedes the group; both rows stay on file."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _confirm_all(db, 1, document_id)
    ids = _field_ids(db, document_id, "amount_ht_dzd")
    _duplicate_header_row(db, document_id, "amount_ht_dzd", "5555.00")

    result = decide_extracted_field_review(
        db,
        company_id=1,
        user_id=2,
        document_id=document_id,
        field_name="amount_ht_dzd",
        decision=DECISION_CORRECTED,
        value="1000.00",
        note="re-read against the paper copy",
    )
    assert result["evidence"]["selected_source"] == "transcription"
    assert result["evidence"]["group_size"] == 2
    ev = _evaluate(db, 1, document_id)
    assert ev["satisfied"] is True, ev["blocking"]
    amount = next(f for f in ev["fields"] if f["field_name"] == "amount_ht_dzd")
    assert amount["value"] == "1000.00"
    assert amount["row_id"] == ids[0]

    with db.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM ocr_extracted_fields WHERE document_id = ?"
            " AND field_name = 'amount_ht_dzd'",
            (document_id,),
        ).fetchone()["n"] == 2
        raw_texts = [
            r["raw_text"]
            for r in conn.execute(
                "SELECT raw_text FROM ocr_extracted_fields WHERE document_id = ?"
                " AND field_name = 'amount_ht_dzd' ORDER BY id",
                (document_id,),
            ).fetchall()
        ]
    assert any("5555" in t for t in raw_texts), "the duplicate's raw evidence is preserved"

    # And the resolved invoice posts once, balanced.
    invoice = _invoice(db, document_id)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)
    post_supplier_invoice(db, 1, 4, invoice["invoice_id"], "post-1")
    assert db.trial_balance(1)["balanced"] is True
    assert db.verify_audit_chain(1)["valid"] is True


def test_18_json_extraction_never_duplicates_a_field(env):
    """Guard the intake path itself: one logical field, one row, one decision."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT field_name, COUNT(*) AS n FROM ocr_extracted_fields"
            " WHERE document_id = ? AND line_index IS NULL GROUP BY field_name",
            (document_id,),
        ).fetchall()
        line_rows = conn.execute(
            "SELECT line_index, COUNT(*) AS n FROM ocr_extracted_fields"
            " WHERE document_id = ? AND field_name = 'line_item_json' GROUP BY line_index",
            (document_id,),
        ).fetchall()
    assert rows and all(r["n"] == 1 for r in rows), [dict(r) for r in rows]
    assert line_rows and all(r["n"] == 1 for r in line_rows), [dict(r) for r in line_rows]
    ev = _evaluate(db, 1, document_id)
    assert ev["duplicate_rows"] == []
    assert all(f["duplicate_row_ids"] == [] for f in ev["fields"])
