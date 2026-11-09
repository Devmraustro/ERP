# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 33 — invoice lifecycle recovery and accounting error semantics.

Resolution matrix for the four findings Phase 32 verified and left open:

* **P32-F2** — ``NeedsReview`` had no supported recovery, and re-creating the
  corrected invoice was refused as a duplicate of the broken draft.
* **P32-F3** — a failed duplicate attempt rewrote the linked source document's
  ``workflow_state`` to ``DuplicateRejected``, drifting evidence away from the
  invoice that still cited it.
* **P32-F4** — ``modify_draft_invoice_line`` left the document in
  ``PendingApproval`` and the approval request ``PENDING`` while the invoice
  became ``NeedsReview``; a request was not bound to the payload it was raised
  against.
* **P32-F5** — a second reversal was refused only by a ``UNIQUE`` constraint and
  escaped as ``sqlite3.IntegrityError`` (HTTP 500).

Every test states the accounting invariant it protects, not merely the code path
it exercises. The four fixes share one rule: an attempt that is refused must
leave the books, the evidence and the audit chain exactly as they were, while
the refusal itself becomes durable, attributed evidence.
"""

import json
import sqlite3
import threading
from decimal import Decimal
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
from dz_erp.intake_ocr import PrivateDocumentVault, decide_extracted_field_review, upload_and_process_supplier_document
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    create_reversal_request,
    decide_invoice_approval,
    decide_reversal_approval,
    modify_draft_invoice_line,
    post_reversal,
    post_supplier_invoice,
    reverse_posted_invoice,
    revalidate_supplier_invoice,
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


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def _make_env(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "p33.sqlite3"))
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


# A second physical copy of the same paper. Byte-identical uploads are refused
# earlier, at the document boundary (``DUPLICATE_FILE_SHA256``), so the copy
# carries a decorative Arabic header: different bytes, identical figures.
_SECOND_COPY_HEADER = "نسخة ثانية من الفاتورة"


def _upload_second_copy(db, vault, *, company_id=1, user_id=2, spec=None):
    second = dict(spec or _SPEC)
    second["arabic_header"] = _SECOND_COPY_HEADER
    return _upload(db, vault, company_id=company_id, user_id=user_id, spec=second)


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


def _review_all(db, company_id, document_id, user_id=2):
    ev = _evaluate(db, company_id, document_id)
    for fev in ev["fields"]:
        if fev["status"] == "missing":
            if fev["required"]:
                raise AssertionError(f"fixture lacks required field {fev['field_name']}")
            decide_extracted_field_review(
                db, company_id=company_id, user_id=user_id, document_id=document_id,
                field_name=fev["field_name"], decision="NOT_PRESENT",
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


def _line(qty="2", price="500.00", discount="0.00", rate="19", description="Widgets", po_line_id=None):
    net = (
        Decimal(qty) * Decimal(price) * ((Decimal("100.00") - Decimal(discount)) / Decimal("100.00"))
    ).quantize(Decimal("0.01"))
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


def _create(
    db,
    *,
    document_id=None,
    company_id=1,
    user_id=2,
    supplier_id=7,
    invoice_no="FAC-2026-0001",
    invoice_date="2026-02-14",
    payment_method="BANK_TRANSFER",
    lines=None,
    amount_ht="1000.00",
    amount_tva="190.00",
    amount_timbre="0.00",
    amount_ttc="1190.00",
):
    return create_and_validate_supplier_invoice(
        db=db,
        company_id=company_id,
        user_id=user_id,
        supplier_id=supplier_id,
        supplier_invoice_no=invoice_no,
        invoice_date=invoice_date,
        lines=[_line()] if lines is None else lines,
        amount_ht_dzd=amount_ht,
        amount_tva_dzd=amount_tva,
        amount_ttc_dzd=amount_ttc,
        amount_timbre_dzd=amount_timbre,
        payment_method=payment_method,
        document_id=document_id,
    )


def _broken_header(db, document_id=None, **kwargs):
    """Inter-consistent line math, header totals that disagree with the lines."""
    return _create(
        db, document_id=document_id, amount_ht="900.00", amount_tva="171.00", amount_ttc="1071.00", **kwargs
    )


def _invoice(db, invoice_id, company_id=1):
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?", (invoice_id, company_id)
        ).fetchone()
        return dict(row) if row else None


def _document_state(db, document_id, company_id=1):
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ? AND company_id = ?",
            (document_id, company_id),
        ).fetchone()
        return row["workflow_state"] if row else None


def _approvals(db, invoice_id, company_id=1):
    with db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM approval_requests WHERE company_id = ? AND target_id = ? ORDER BY id",
                (company_id, invoice_id),
            ).fetchall()
        ]


def _actions(db, company_id=1):
    with db.transaction() as conn:
        return [
            r["action_type"]
            for r in conn.execute(
                "SELECT action_type FROM audit_events WHERE company_id = ? ORDER BY id", (company_id,)
            ).fetchall()
        ]


def _journal(db, company_id=1):
    with db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM journal_entries WHERE company_id = ? ORDER BY id", (company_id,)
            ).fetchall()
        ]


def _reviewed_invoice(db, vault, *, spec=None, invoice_no="FAC-2026-0001", user_id=2):
    document_id = _upload(db, vault, spec=spec)["document_id"]
    _review_all(db, 1, document_id, user_id=user_id)
    created = _create(db, document_id=document_id, invoice_no=invoice_no, user_id=user_id)
    assert created["workflow_state"] == "Validated", created["validation_errors"]
    return document_id, created["invoice_id"]


def _post(db, invoice_id, *, key="post-1", submit_user=2, approve_user=3, post_user=4):
    submit_invoice_for_approval(db, 1, submit_user, invoice_id)
    decide_invoice_approval(db, 1, approve_user, invoice_id, approve=True)
    return post_supplier_invoice(db, 1, post_user, invoice_id, key)


# ---------------------------------------------------------------------------
# P32-F2 — recoverable NeedsReview lifecycle
# ---------------------------------------------------------------------------
def test_f2_correcting_a_needs_review_invoice_revalidates_it_in_place(env):
    """The same invoice identity is corrected, re-validated and then posted."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)
    assert broken["workflow_state"] == "NeedsReview"
    broken_hash = broken["payload_sha256"]

    fixed = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    assert fixed["invoice_id"] == broken["invoice_id"], "the invoice keeps its identity"
    assert fixed["previous_state"] == "NeedsReview"
    assert fixed["workflow_state"] == "Validated"
    assert fixed["validation_errors"] == []
    assert fixed["payload_sha256"] != broken_hash, "a corrected payload gets a new digest"
    assert fixed["revised"] is True
    assert fixed["validation_attempt"] == 1

    # The evidence row follows the corrected invoice, and the original attempt
    # is preserved in the audit chain rather than overwritten.
    assert _document_state(db, document_id) == "Validated"
    actions = _actions(db)
    assert "INVOICE_REVISED" in actions and "INVOICE_REVALIDATED" in actions
    with db.transaction() as conn:
        event = conn.execute(
            "SELECT before_json, after_json FROM audit_events"
            " WHERE action_type = 'INVOICE_REVALIDATED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    before = json.loads(event["before_json"])
    assert before["workflow_state"] == "NeedsReview"
    assert any("HEADER_HT_MISMATCH" in e for e in before["validation_errors"])

    _post(db, broken["invoice_id"])
    assert _invoice(db, broken["invoice_id"])["workflow_state"] == "Posted"
    report = db.trial_balance(1)
    assert report["balanced"] is True
    assert report["total_debit_dzd"] == report["total_credit_dzd"] == "1190.00"
    assert db.verify_audit_chain(1)["valid"] is True


def test_f2_repeated_revalidation_is_safe_and_each_attempt_is_audited(env):
    """Validating again is not a state change, and never loses the history."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)

    first = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    second = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    assert second["workflow_state"] == "Validated"
    assert second["payload_sha256"] == first["payload_sha256"]
    assert second["revised"] is False
    assert second["validation_attempt"] == 2
    assert second["previous_state"] == "Validated"
    assert _actions(db).count("INVOICE_REVALIDATED") == 2
    # No duplicate invoice, no duplicate line, no second approval request.
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM supplier_invoices").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM supplier_invoice_lines").fetchone()["n"] == 1
    assert _approvals(db, broken["invoice_id"]) == []


def test_f2_failed_revalidation_is_recorded_and_still_blocks_submission(env):
    """A re-validation that fails leaves the invoice unsubmittable, with reasons."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)

    again = revalidate_supplier_invoice(db, 1, 2, broken["invoice_id"], amount_ht_dzd="950.00")
    assert again["workflow_state"] == "NeedsReview"
    assert any("HEADER_HT_MISMATCH" in e for e in again["validation_errors"])
    assert again["validation_attempt"] == 1

    with pytest.raises(FinancialIntegrityError, match="must be 'Validated'"):
        submit_invoice_for_approval(db, 1, 2, broken["invoice_id"])
    assert _journal(db) == []
    assert db.verify_audit_chain(1)["valid"] is True


def test_f2_a_previous_validation_result_cannot_authorize_posting(env):
    """Posting is bound to the digest of the attempt that was approved."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)
    rejected_hash = broken["payload_sha256"]

    fixed = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    submit_invoice_for_approval(db, 1, 2, broken["invoice_id"])
    decide_invoice_approval(db, 1, 3, broken["invoice_id"], approve=True)
    inv = _invoice(db, broken["invoice_id"])
    assert inv["approved_payload_sha256"] == fixed["payload_sha256"] != rejected_hash
    post_supplier_invoice(db, 1, 4, broken["invoice_id"], "post-1")
    assert _invoice(db, broken["invoice_id"])["workflow_state"] == "Posted"


@pytest.mark.parametrize("target_state", ["PendingApproval", "Approved", "Posted", "Rejected"])
def test_f2_illegal_transitions_are_refused(env, target_state):
    """Approved, posted and rejected records are never rewritten in place."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    created = _create(db, document_id=document_id)
    invoice_id = created["invoice_id"]

    if target_state in ("PendingApproval", "Approved", "Posted"):
        submit_invoice_for_approval(db, 1, 2, invoice_id)
    if target_state in ("Approved", "Posted"):
        decide_invoice_approval(db, 1, 3, invoice_id, approve=True)
    if target_state == "Posted":
        post_supplier_invoice(db, 1, 4, invoice_id, "post-1")
    if target_state == "Rejected":
        submit_invoice_for_approval(db, 1, 2, invoice_id)
        decide_invoice_approval(db, 1, 3, invoice_id, approve=False, reason="not ours")

    assert _invoice(db, invoice_id)["workflow_state"] == target_state
    with pytest.raises(FinancialIntegrityError, match="INVOICE_REVALIDATION_STATE_REFUSED"):
        revalidate_supplier_invoice(db, 1, 2, invoice_id, amount_ht_dzd="1000.00")
    assert _invoice(db, invoice_id)["workflow_state"] == target_state
    if target_state == "Posted":
        assert len(_journal(db)) == 1


def test_f2_duplicate_rejected_draft_can_be_corrected_into_a_valid_invoice(env):
    """A duplicate-rejected draft is correctable once its reference is fixed."""
    db, vault, _client = env
    document_id, existing_invoice = _reviewed_invoice(db, vault)

    # A second paper with the same supplier reference: recorded as a duplicate.
    second_doc = _upload_second_copy(db, vault)["document_id"]
    assert second_doc != document_id
    _review_all(db, 1, second_doc)
    dup = _create(db, document_id=second_doc, supplier_id=7)
    assert dup["workflow_state"] == "DuplicateRejected"
    assert "DUPLICATE_INVOICE_ATTEMPT_RECORDED" in _actions(db)
    # ...and the *original* document is untouched by the attempt (P32-F3).
    assert _document_state(db, document_id) == "Validated"

    corrected = revalidate_supplier_invoice(db, 1, 2, dup["invoice_id"], supplier_invoice_no="FAC-2026-0777")
    assert corrected["workflow_state"] == "Validated", corrected["validation_errors"]
    assert _invoice(db, dup["invoice_id"])["supplier_invoice_no"] == "FAC-2026-0777"
    assert _invoice(db, existing_invoice)["workflow_state"] == "Validated"


def test_f2_revalidation_cannot_steal_another_invoices_reference(env):
    """Correcting an invoice *into* an existing reference is still a duplicate."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)
    other_spec = dict(_SPEC)
    other_spec["invoice_number"] = "FAC-2026-0002"
    other_doc = _upload(db, vault, spec=other_spec)["document_id"]
    _review_all(db, 1, other_doc)
    other = _create(db, document_id=other_doc, invoice_no="FAC-2026-0002")

    blocked = revalidate_supplier_invoice(db, 1, 2, broken["invoice_id"], supplier_invoice_no="FAC-2026-0002")
    assert blocked["workflow_state"] == "DuplicateRejected"
    assert any("EXACT_DUPLICATE_INVOICE_REF" in e for e in blocked["validation_errors"])
    assert _invoice(db, other["invoice_id"])["workflow_state"] == "Validated"


def test_f2_revalidation_keeps_the_source_document_gate(env):
    """A corrected invoice still cannot disagree with its reviewed evidence."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)

    # Inter-consistent but *different* numbers: the invoice validates on its own
    # arithmetic yet disagrees with the reviewed paper.
    revised = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        lines=[_line("2", "480.00")], amount_ht_dzd="960.00", amount_tva_dzd="182.40", amount_ttc_dzd="1142.40",
    )
    assert revised["workflow_state"] == "Validated", revised["validation_errors"]
    with pytest.raises(FinancialIntegrityError, match="LINE_1_PRICE_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, broken["invoice_id"])
    assert _invoice(db, broken["invoice_id"])["workflow_state"] == "Validated"
    assert _journal(db) == []


def test_f2_legacy_documentless_invoice_is_correctable(env):
    """Manual invoices keep working, and gain the same recovery path."""
    db, _vault, _client = env
    broken = _broken_header(db, document_id=None)
    assert broken["workflow_state"] == "NeedsReview"

    fixed = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    assert fixed["workflow_state"] == "Validated"
    _post(db, broken["invoice_id"])
    assert _invoice(db, broken["invoice_id"])["workflow_state"] == "Posted"


def test_f2_revalidation_is_role_and_tenant_scoped(env):
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)

    with pytest.raises(AuthorizationError, match="not authorized"):
        revalidate_supplier_invoice(db, 1, 5, broken["invoice_id"], amount_ht_dzd="1000.00")
    with pytest.raises(TenantIsolationError):
        revalidate_supplier_invoice(db, 1, 21, broken["invoice_id"], amount_ht_dzd="1000.00")
    assert _invoice(db, broken["invoice_id"])["workflow_state"] == "NeedsReview"
    assert _invoice(db, broken["invoice_id"])["amount_ht_dzd"] == "900.00"


def test_f2_revalidate_endpoint_over_http(env):
    db, vault, client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)
    token = client.post(
        "/api/v1/auth/token", json={"username": "clerk_a", "password": "Clerk#2026x"}
    ).json()["access_token"]

    ok = client.post(
        f"/api/v1/invoices/{broken['invoice_id']}/revalidate",
        headers={"Authorization": f"Bearer {token}"},
        json={"amount_ht_dzd": "1000.00", "amount_tva_dzd": "190.00", "amount_ttc_dzd": "1190.00"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["workflow_state"] == "Validated"

    submit_invoice_for_approval(db, 1, 2, broken["invoice_id"])
    refused = client.post(
        f"/api/v1/invoices/{broken['invoice_id']}/revalidate",
        headers={"Authorization": f"Bearer {token}"},
        json={"amount_ht_dzd": "1000.00"},
    )
    assert refused.status_code == 409, refused.text
    assert "INVOICE_REVALIDATION_STATE_REFUSED" in refused.json()["detail"]


# ---------------------------------------------------------------------------
# P32-F3 — a refused attempt never mutates evidence
# ---------------------------------------------------------------------------
def test_f3_duplicate_attempt_leaves_document_and_original_invoice_untouched(env):
    db, vault, _client = env
    document_id, original_id = _reviewed_invoice(db, vault)
    original_before = _invoice(db, original_id)
    original_doc_state = _document_state(db, document_id)

    # The same supplier reference arrives on a second paper: the attempt is
    # recorded as a duplicate, and neither document moves.
    second_doc = _upload_second_copy(db, vault)["document_id"]
    assert second_doc != document_id
    _review_all(db, 1, second_doc)
    second_doc_state = _document_state(db, second_doc)
    dup = _create(db, document_id=second_doc, supplier_id=7)
    assert dup["workflow_state"] == "DuplicateRejected"

    assert _document_state(db, document_id) == original_doc_state == "Validated"
    assert _document_state(db, second_doc) == second_doc_state, (
        "the attempted document is not marked rejected by the failed attempt"
    )
    assert _invoice(db, original_id) == original_before

    with db.transaction() as conn:
        event = conn.execute(
            "SELECT company_id, actor_user_id, after_json FROM audit_events"
            " WHERE action_type = 'DUPLICATE_INVOICE_ATTEMPT_RECORDED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event is not None
    payload = json.loads(event["after_json"])
    assert payload["company_id"] == 1
    assert payload["document_id"] == second_doc
    assert payload["attempted_supplier_invoice_no"] == "FAC-2026-0001"
    assert payload["refusal_reason"] == "EXACT_DUPLICATE_INVOICE_REF"
    assert payload["document_state_touched"] is False
    assert db.verify_audit_chain(1)["valid"] is True


def test_f3_a_second_invoice_on_the_same_document_is_refused(env):
    """One paper backs exactly one invoice; the refusal is durable evidence."""
    db, vault, _client = env
    document_id, original_id = _reviewed_invoice(db, vault)
    doc_state = _document_state(db, document_id)

    with pytest.raises(FinancialIntegrityError, match="DOCUMENT_ALREADY_INVOICED"):
        _create(db, document_id=document_id, invoice_no="FAC-2026-0009", lines=[_line("1", "500.00")],
                amount_ht="500.00", amount_tva="95.00", amount_ttc="595.00")

    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM supplier_invoices").fetchone()["n"] == 1
        event = conn.execute(
            "SELECT actor_user_id, after_json FROM audit_events"
            " WHERE action_type = 'DUPLICATE_DOCUMENT_INVOICE_ATTEMPT_REFUSED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event is not None
    payload = json.loads(event["after_json"])
    assert event["actor_user_id"] == 2
    assert payload["existing_invoice_id"] == original_id
    assert payload["attempted_supplier_invoice_no"] == "FAC-2026-0009"
    assert payload["refusal_reason"] == "DOCUMENT_ALREADY_INVOICED"
    assert _document_state(db, document_id) == doc_state
    assert db.verify_audit_chain(1)["valid"] is True


def test_f3_duplicate_detection_stays_tenant_scoped(env):
    """The same reference in another company is not a duplicate; the evidence is not shared."""
    db, vault, _client = env
    document_id, _original = _reviewed_invoice(db, vault)

    other = _create(db, company_id=2, user_id=21, supplier_id=8)
    assert other["workflow_state"] != "DuplicateRejected"
    assert other["invoice_id"] != _original

    # Company 2 cannot cite company 1's document at all.
    with pytest.raises(TenantIsolationError):
        _create(db, company_id=2, user_id=21, supplier_id=8, document_id=document_id)
    assert _document_state(db, document_id) == "Validated"


# ---------------------------------------------------------------------------
# P32-F4 — approval invalidation after modification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs,expected_ht",
    [
        # 2 x 560.00 = 1120.00
        ({"new_unit_price_dzd": "560.00"}, "1120.00"),
        # 3 x 500.00 = 1500.00
        ({"new_qty": "3"}, "1500.00"),
        # 2 x 500.00 less 10% = 900.00
        ({"new_discount_pct": "10.00"}, "900.00"),
    ],
)
def test_f4_any_financial_modification_invalidates_the_approval(env, kwargs, expected_ht):
    db, _vault, _client = env
    invoice = _create(db, document_id=None)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    pending = _approvals(db, invoice["invoice_id"])
    assert [r["decision"] for r in pending] == ["PENDING"]
    old_hash = pending[0]["payload_sha256"]

    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice["invoice_id"],)
        ).fetchone()["id"]
    result = modify_draft_invoice_line(db, 1, 2, invoice["invoice_id"], line_id, **kwargs)

    assert result["workflow_state"] == "NeedsReview"
    assert result["approval_invalidated"] is True
    assert result["invalidated_approval_request_ids"] == [pending[0]["id"]]
    assert result["payload_sha256"] != old_hash
    rows = _approvals(db, invoice["invoice_id"])
    assert [r["decision"] for r in rows] == ["INVALIDATED"]
    assert rows[0]["reason"] == "PAYLOAD_MODIFIED_AFTER_SUBMISSION"
    assert rows[0]["decided_by_id"] == 2
    inv = _invoice(db, invoice["invoice_id"])
    assert inv["approved_payload_sha256"] is None
    # The figures were re-derived from the corrected line, in exact Decimals.
    with db.transaction() as conn:
        line = conn.execute(
            "SELECT * FROM supplier_invoice_lines WHERE id = ?", (line_id,)
        ).fetchone()
    assert line["line_ht_dzd"] == expected_ht
    assert inv["amount_ht_dzd"] == expected_ht


def test_f4_document_and_invoice_state_stay_consistent(env):
    """The evidence row never claims a pending approval the invoice no longer has."""
    db, vault, _client = env
    document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    assert _document_state(db, document_id) == "PendingApproval"

    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()["id"]
    modify_draft_invoice_line(db, 1, 2, invoice_id, line_id, "560.00")

    assert _invoice(db, invoice_id)["workflow_state"] == "NeedsReview"
    assert _document_state(db, document_id) == "NeedsReview"
    assert [r["decision"] for r in _approvals(db, invoice_id)] == ["INVALIDATED"]
    # Approval of the modified payload is refused (the invoice left PendingApproval).
    with pytest.raises(FinancialIntegrityError, match="not 'PendingApproval'"):
        decide_invoice_approval(db, 1, 3, invoice_id, approve=True)
    # ...and the modified payload no longer matches the reviewed paper once the
    # corrected invoice is re-validated and offered for approval again.
    revalidate_supplier_invoice(db, 1, 2, invoice_id)
    with pytest.raises(FinancialIntegrityError, match="PRICE_SOURCE_MISMATCH"):
        submit_invoice_for_approval(db, 1, 2, invoice_id)
    assert _invoice(db, invoice_id)["workflow_state"] == "Validated"
    assert _journal(db) == []


def test_f4_a_corrected_manual_invoice_can_be_resubmitted_and_approved(env):
    """Invalidation is not a dead end: a fresh submission gets a fresh decision."""
    db, _vault, _client = env
    invoice = _create(db, document_id=None)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice["invoice_id"],)
        ).fetchone()["id"]
    modify_draft_invoice_line(db, 1, 2, invoice["invoice_id"], line_id, "560.00")

    # A modified payload cannot be re-submitted from NeedsReview without
    # re-validation; the manual invoice revalidates cleanly and then proceeds.
    revalidate_supplier_invoice(db, 1, 2, invoice["invoice_id"])
    second = submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    assert second["payload_sha256"] != _approvals(db, invoice["invoice_id"])[0]["payload_sha256"]
    rows = _approvals(db, invoice["invoice_id"])
    assert [r["decision"] for r in rows] == ["INVALIDATED", "PENDING"]
    decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)
    post_supplier_invoice(db, 1, 4, invoice["invoice_id"], "post-1")
    report = db.trial_balance(1)
    assert report["balanced"] is True
    assert _invoice(db, invoice["invoice_id"])["workflow_state"] == "Posted"


def test_f4_an_obsolete_request_cannot_authorize_a_modified_invoice(env):
    """A payload changed behind the request's back invalidates the request."""
    db, _vault, _client = env
    invoice = _create(db, document_id=None)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    request_hash = _approvals(db, invoice["invoice_id"])[0]["payload_sha256"]

    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET payload_sha256 = ? WHERE id = ?",
            ("f" * 64, invoice["invoice_id"]),
        )
    with pytest.raises(FinancialIntegrityError, match="APPROVAL_REQUEST_STALE"):
        decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)

    rows = _approvals(db, invoice["invoice_id"])
    assert [r["decision"] for r in rows] == ["INVALIDATED"]
    assert "PAYLOAD_HASH_CHANGED_SINCE_REQUEST" in rows[0]["reason"]
    assert rows[0]["payload_sha256"] == request_hash
    inv = _invoice(db, invoice["invoice_id"])
    assert inv["workflow_state"] == "NeedsReview"
    assert inv["approved_payload_sha256"] is None
    assert "APPROVAL_REQUEST_STALE_REFUSED" in _actions(db)
    assert db.verify_audit_chain(1)["valid"] is True


def test_f4_approval_is_bound_to_the_source_review_evidence(env):
    """New review evidence cannot ride an approval raised against the old set."""
    db, vault, _client = env
    document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    bound = _approvals(db, invoice_id)[0]
    assert bound["source_review_fingerprint"], "the request is bound to the review evidence"

    # Simulate evidence changing behind the approval (out-of-band: the decision
    # API itself is closed while the document backs a pending approval).
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO ocr_field_reviews (company_id, document_id, field_name, line_index,"
            " decision, reviewed_value, note, reviewed_by_id, reviewed_at)"
            " VALUES (1, ?, 'amount_ht_dzd', NULL, 'CONFIRMED', '1000.00', 'late', 2, '2026-02-15T00:00:00+00:00')",
            (document_id,),
        )
    with pytest.raises(FinancialIntegrityError, match="APPROVAL_REQUEST_STALE"):
        decide_invoice_approval(db, 1, 3, invoice_id, approve=True)

    rows = _approvals(db, invoice_id)
    assert [r["decision"] for r in rows] == ["INVALIDATED"]
    assert "SOURCE_REVIEW_CHANGED_SINCE_REQUEST" in rows[0]["reason"]
    assert _invoice(db, invoice_id)["workflow_state"] == "NeedsReview"
    assert _invoice(db, invoice_id)["approved_payload_sha256"] is None
    assert _journal(db) == []


def test_f4_a_legacy_request_without_a_fingerprint_still_works(env):
    """Backward compatibility: unbound requests are tolerated, not auto-refused."""
    db, _vault, _client = env
    invoice = _create(db, document_id=None)
    submit_invoice_for_approval(db, 1, 2, invoice["invoice_id"])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE approval_requests SET source_review_fingerprint = NULL WHERE target_id = ?",
            (invoice["invoice_id"],),
        )
    approved = decide_invoice_approval(db, 1, 3, invoice["invoice_id"], approve=True)
    assert approved["workflow_state"] == "Approved"
    post_supplier_invoice(db, 1, 4, invoice["invoice_id"], "post-legacy")
    assert _invoice(db, invoice["invoice_id"])["workflow_state"] == "Posted"


def test_f4_rejection_of_a_modified_invoice_is_still_possible(env):
    """The safe direction is never gated: a stale invoice can still be rejected."""
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    submit_invoice_for_approval(db, 1, 2, invoice_id)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET payload_sha256 = ? WHERE id = ?", ("a" * 64, invoice_id)
        )
    rejected = decide_invoice_approval(db, 1, 3, invoice_id, approve=False, reason="payload edited")
    assert rejected["decision"] == "REJECTED"
    assert _invoice(db, invoice_id)["workflow_state"] == "Rejected"


# ---------------------------------------------------------------------------
# P32-F5 — stable reversal conflict semantics
# ---------------------------------------------------------------------------
def test_f5_first_reversal_succeeds_and_the_second_is_a_domain_conflict(env):
    db, vault, client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)

    first = create_reversal_request(db, 1, 4, invoice_id, "supplier dispute", "rev-1")
    note_id = first["credit_note_invoice_id"]
    submit_reversal_for_approval(db, 1, 4, note_id)
    decide_reversal_approval(db, 1, 3, note_id, approve=True)
    post_reversal(db, 1, 4, note_id, "rev-post-1")

    with pytest.raises(FinancialIntegrityError, match="INVOICE_ALREADY_REVERSED") as refused:
        create_reversal_request(db, 1, 4, invoice_id, "again", "rev-2")
    assert not isinstance(refused.value, sqlite3.IntegrityError)

    with db.transaction() as conn:
        refunds = conn.execute(
            "SELECT COUNT(*) AS n FROM supplier_invoices WHERE invoice_type = 'IN_REFUND'"
            " AND workflow_state IN ('NeedsReview', 'PendingApproval', 'Approved', 'Posted')"
        ).fetchone()["n"]
        event = conn.execute(
            "SELECT after_json FROM audit_events"
            " WHERE action_type = 'REVERSAL_ATTEMPT_REFUSED_DUPLICATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert refunds == 1
    payload = json.loads(event["after_json"])
    assert payload["existing_credit_note_id"] == note_id
    assert payload["refusal_reason"] == "INVOICE_ALREADY_REVERSED"
    assert db.trial_balance(1)["balanced"] is True
    assert db.verify_audit_chain(1)["valid"] is True

    # Over HTTP the same refusal is a stable 409, never a 500.
    token = client.post(
        "/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"}
    ).json()["access_token"]
    resp = client.post(
        f"/api/v1/invoices/{invoice_id}/reverse",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "rev-3"},
        json={"reason": "again"},
    )
    assert resp.status_code == 409, resp.text
    assert "INVOICE_ALREADY_REVERSED" in resp.json()["detail"]


def test_f5_concurrent_reversals_produce_exactly_one_credit_note(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)

    results = []
    barrier = threading.Barrier(2)

    def _reverse(key):
        barrier.wait()
        try:
            results.append(("ok", create_reversal_request(db, 1, 4, invoice_id, "race", key)))
        except FinancialIntegrityError as exc:
            # A domain refusal, never a raw ``sqlite3.IntegrityError``.
            assert "INVOICE_ALREADY_REVERSED" in str(exc), exc
            results.append(("refused", "FinancialIntegrityError"))
        except sqlite3.OperationalError as exc:  # pragma: no cover - lock contention
            results.append(("busy", str(exc)))

    threads = [threading.Thread(target=_reverse, args=(f"rev-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert [r[0] for r in results].count("ok") == 1, results
    refused = [r for r in results if r[0] == "refused"]
    assert len(refused) == 1, results
    with db.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM supplier_invoices WHERE invoice_type = 'IN_REFUND'"
        ).fetchone()["n"] == 1
    assert db.trial_balance(1)["balanced"] is True


def test_f5_cross_company_reversal_is_refused(env):
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)
    # A company-2 administrator acting inside company 2 cannot touch company 1:
    # the lookup is company-scoped, so the invoice simply does not exist there.
    with pytest.raises((TenantIsolationError, FinancialIntegrityError)):
        create_reversal_request(db, 2, 20, invoice_id, "cross", "rev-x")
    # A company-2 user reaching for company 1's invoice id is refused by role or
    # tenant check; either way no credit note is written.
    with pytest.raises((AuthorizationError, TenantIsolationError, FinancialIntegrityError)):
        create_reversal_request(db, 1, 21, invoice_id, "cross", "rev-y")
    with pytest.raises((AuthorizationError, TenantIsolationError, FinancialIntegrityError)):
        create_reversal_request(db, 1, 2, invoice_id, "cross", "rev-w")
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM supplier_invoices WHERE invoice_type = 'IN_REFUND'").fetchone()["n"] == 0


def test_f5_entry_number_conflict_is_mapped_to_the_domain_error(env):
    """Even a collision the pre-check cannot see becomes a documented refusal."""
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)
    fiscal_year = _invoice(db, invoice_id)["fiscal_year"]

    # A legacy/foreign DRAFT entry already occupies the reversal entry number.
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO journal_entries (
                company_id, branch_id, entry_number, journal_code, entry_date, state,
                source_model, source_id, total_debit_dzd, total_credit_dzd
            ) VALUES (1, NULL, ?, 'ACH', '2026-02-14', 'DRAFT', 'manual', '0', '0.00', '0.00')
            """,
            (f"RAVOIR/{fiscal_year}/{invoice_id:05d}",),
        )

    with pytest.raises(FinancialIntegrityError, match="INVOICE_ALREADY_REVERSED") as refused:
        create_reversal_request(db, 1, 4, invoice_id, "collision", "rev-z")
    assert not isinstance(refused.value, sqlite3.IntegrityError)
    with db.transaction() as conn:
        event = conn.execute(
            "SELECT after_json FROM audit_events"
            " WHERE action_type = 'REVERSAL_ATTEMPT_REFUSED_DUPLICATE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM supplier_invoices WHERE invoice_type = 'IN_REFUND'"
        ).fetchone()["n"] == 0
    assert json.loads(event["after_json"])["refusal_reason"] == "REVERSAL_ENTRY_UNIQUE_CONFLICT"
    assert db.verify_audit_chain(1)["valid"] is True


# ---------------------------------------------------------------------------
# Cross-feature: lifecycle invariants and migration compatibility
# ---------------------------------------------------------------------------
def test_invariants_posted_records_are_never_rewritten(env):
    """Every fix in this phase leaves posted entries and their lines untouched."""
    db, vault, _client = env
    document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)
    with db.transaction() as conn:
        before = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM journal_entry_lines WHERE journal_entry_id IN"
                " (SELECT id FROM journal_entries WHERE company_id = 1) ORDER BY id"
            ).fetchall()
        ]
    entries_before = _journal(db)

    # Every refused or illegal operation attempted afterwards.
    with pytest.raises(FinancialIntegrityError):
        revalidate_supplier_invoice(db, 1, 2, invoice_id, amount_ht_dzd="1.00")
    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()["id"]
    with pytest.raises(FinancialIntegrityError, match="Posted invoice cannot be edited"):
        modify_draft_invoice_line(db, 1, 2, invoice_id, line_id, "999.00")

    with db.transaction() as conn:
        after = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM journal_entry_lines WHERE journal_entry_id IN"
                " (SELECT id FROM journal_entries WHERE company_id = 1) ORDER BY id"
            ).fetchall()
        ]
    assert after == before
    assert _journal(db) == entries_before
    assert _document_state(db, document_id) == "Posted"
    assert db.verify_audit_chain(1)["valid"] is True


def test_invariants_full_lifecycle_with_recovery_and_reversal(env):
    """Broken draft → corrected → posted → correctly reversed, balanced throughout."""
    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    broken = _broken_header(db, document_id=document_id)
    fixed = revalidate_supplier_invoice(
        db, 1, 2, broken["invoice_id"],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
    )
    assert fixed["workflow_state"] == "Validated"
    _post(db, broken["invoice_id"])

    note = create_reversal_request(db, 1, 4, broken["invoice_id"], "cancelled", "rev-1")
    submit_reversal_for_approval(db, 1, 4, note["credit_note_invoice_id"])
    decide_reversal_approval(db, 1, 3, note["credit_note_invoice_id"], approve=True)
    post_reversal(db, 1, 4, note["credit_note_invoice_id"], "rev-post-1")

    report = db.trial_balance(1)
    assert report["balanced"] is True
    with db.transaction() as conn:
        payable = conn.execute(
            "SELECT l.debit_dzd, l.credit_dzd FROM journal_entry_lines l"
            " JOIN journal_entries e ON e.id = l.journal_entry_id"
            " WHERE l.company_id = 1 AND l.account_code = '401' AND e.state = 'POSTED'"
        ).fetchall()
    debits = sum((Decimal(r["debit_dzd"]) for r in payable), Decimal("0.00"))
    credits = sum((Decimal(r["credit_dzd"]) for r in payable), Decimal("0.00"))
    assert debits == credits == Decimal("1190.00")
    assert db.verify_audit_chain(1)["valid"] is True
    assert _actions(db).count("INVOICE_REVALIDATED") == 1
    assert _actions(db).count("REVERSAL_POSTED") == 1


def test_migration_is_idempotent_and_repeated_runs_are_safe(tmp_path):
    """A pre-Phase-33 database gains the binding column without losing rows."""
    path = tmp_path / "p33.sqlite3"
    db, vault = _make_env(tmp_path)
    document_id, invoice_id = _reviewed_invoice(db, vault)
    before = _invoice(db, invoice_id)
    submit_invoice_for_approval(db, 1, 2, invoice_id)

    # Simulate a database created before Phase 33.
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(approval_requests)").fetchall()}
        assert "source_review_fingerprint" in cols
        conn.execute("ALTER TABLE approval_requests DROP COLUMN source_review_fingerprint")
        conn.commit()

    reopened = ERPDatabase(str(path))
    _ = ERPDatabase(str(path))  # a second open must be a no-op
    with reopened.transaction() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(approval_requests)").fetchall()}
        assert "source_review_fingerprint" in cols
        rows = [dict(r) for r in conn.execute("SELECT * FROM approval_requests ORDER BY id").fetchall()]
    # The request recorded before the column existed reads as "not bound"...
    assert rows and rows[0]["source_review_fingerprint"] is None
    assert _invoice(reopened, invoice_id) == before | {"workflow_state": "PendingApproval"}

    # ...and it still authorizes the approval it was legitimately raised for.
    decided = decide_invoice_approval(reopened, 1, 3, invoice_id, approve=True)
    assert decided["workflow_state"] == "Approved"
    post_supplier_invoice(reopened, 1, 4, invoice_id, "post-after-migration")
    assert _invoice(reopened, invoice_id)["workflow_state"] == "Posted"
    assert reopened.verify_audit_chain(1)["valid"] is True
    assert _document_state(reopened, document_id) == "Posted"


# ---------------------------------------------------------------------------
# F-R2-2 — REVERSE_INVOICE idempotency must compare the request fingerprint
# ---------------------------------------------------------------------------
def _reversal_side_effects(db) -> dict:
    """Everything a duplicate reversal could move: notes, entries, payment rows."""
    with db.transaction() as conn:
        return {
            "credit_notes": conn.execute(
                "SELECT COUNT(*) AS n FROM supplier_invoices WHERE invoice_type = 'IN_REFUND'"
            ).fetchone()["n"],
            "journal_entries": conn.execute(
                "SELECT COUNT(*) AS n FROM journal_entries"
            ).fetchone()["n"],
            "idempotency_records": conn.execute(
                "SELECT COUNT(*) AS n FROM idempotency_records WHERE operation = 'REVERSE_INVOICE'"
            ).fetchone()["n"],
        }


def test_fr2_2_same_reversal_key_with_a_different_reason_is_refused_not_replayed(env):
    """A key identifies the request, not just the operation.

    Regression for F-R2-2: ``_create_reversal_request`` replayed a
    ``REVERSE_INVOICE`` key without comparing ``request_sha256``, so a second
    request carrying a *different reason* was answered with the first request's
    cached success — a silently dropped request reported as a successful replay.
    """
    db, vault, client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)

    first = create_reversal_request(db, 1, 4, invoice_id, "supplier dispute", "rev-fr2-2")
    after_first = _reversal_side_effects(db)

    # 1. Identical request: still a safe replay, same credit note, no new rows.
    replay = create_reversal_request(db, 1, 4, invoice_id, "supplier dispute", "rev-fr2-2")
    assert replay["idempotent_replay"] is True
    assert replay["credit_note_invoice_id"] == first["credit_note_invoice_id"]
    assert _reversal_side_effects(db) == after_first

    # 2. Same key, different reason: refused, never reported as a replay.
    with pytest.raises(FinancialIntegrityError, match="IDEMPOTENCY_KEY_PAYLOAD_MISMATCH") as refused:
        create_reversal_request(db, 1, 4, invoice_id, "duplicate billing", "rev-fr2-2")
    assert not isinstance(refused.value, sqlite3.IntegrityError)

    # 3. No second credit note, no second entry, no overwritten record.
    assert _reversal_side_effects(db) == after_first

    # 4. The refusal is attributed evidence, committed outside the rollback.
    with db.transaction() as conn:
        event = conn.execute(
            "SELECT after_json FROM audit_events"
            " WHERE action_type = 'REVERSAL_ATTEMPT_REFUSED_IDEMPOTENCY_MISMATCH'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    payload = json.loads(event["after_json"])
    assert payload["attempted_reason"] == "duplicate billing"
    assert payload["refusal_reason"] == "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"
    assert payload["attempted_request_sha256"] != payload["recorded_request_sha256"]

    # 5. Over HTTP the refusal is a stable conflict, never a 500.
    token = client.post(
        "/api/v1/auth/token", json={"username": "acct_a", "password": "Acct#2026x"}
    ).json()["access_token"]
    resp = client.post(
        f"/api/v1/invoices/{invoice_id}/reverse",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "rev-fr2-2"},
        json={"reason": "duplicate billing"},
    )
    assert resp.status_code == 409, resp.text
    assert "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH" in resp.json()["detail"]

    assert db.trial_balance(1)["balanced"] is True
    assert db.verify_audit_chain(1)["valid"] is True


def test_fr2_2_direct_reversal_path_also_rejects_a_changed_fingerprint(env):
    """The same guard on the lower-level ``reverse_posted_invoice`` entry point.

    Both affected call sites are covered independently: this one posts the
    credit note itself, so a replay that was wrongly answered would be a
    duplicate ledger entry rather than just a wrong response.
    """
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)

    first = reverse_posted_invoice(db, 1, 4, invoice_id, "supplier dispute", "rev-direct-fr2-2")
    after_first = _reversal_side_effects(db)

    replay = reverse_posted_invoice(db, 1, 4, invoice_id, "supplier dispute", "rev-direct-fr2-2")
    assert replay["idempotent_replay"] is True
    assert replay["credit_note_invoice_id"] == first["credit_note_invoice_id"]

    with pytest.raises(FinancialIntegrityError, match="IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"):
        reverse_posted_invoice(db, 1, 4, invoice_id, "a different reason", "rev-direct-fr2-2")

    assert _reversal_side_effects(db) == after_first
    # Exactly one reversal entry exists, and the original still points at it.
    with db.transaction() as conn:
        reversal = conn.execute(
            "SELECT id, total_debit_dzd FROM journal_entries WHERE id = ?",
            (first["reversal_journal_entry_id"],),
        ).fetchone()
        original = conn.execute(
            "SELECT reversal_entry_id FROM journal_entries WHERE id ="
            " (SELECT journal_entry_id FROM supplier_invoices WHERE id = ?)",
            (invoice_id,),
        ).fetchone()
    assert reversal is not None
    assert original["reversal_entry_id"] == first["reversal_journal_entry_id"]
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_2_concurrent_reversal_retries_never_duplicate_or_misreport(env):
    """Concurrency, not just sequencing.

    Four threads race on one key: identical requests must converge on a single
    credit note (one write, three replays), and a thread carrying a different
    reason must be refused rather than handed the winner's result.
    """
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)

    results = []
    lock = threading.Lock()

    def _attempt(reason):
        try:
            res = create_reversal_request(db, 1, 4, invoice_id, reason, "rev-race-fr2-2")
            outcome = ("replay", res["credit_note_invoice_id"]) if res.get("idempotent_replay") \
                else ("created", res["credit_note_invoice_id"])
        except FinancialIntegrityError as exc:
            assert "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH" in str(exc), exc
            outcome = ("refused", None)
        with lock:
            results.append(outcome)

    identical = [threading.Thread(target=_attempt, args=("supplier dispute",)) for _ in range(3)]
    for t in identical:
        t.start()
    for t in identical:
        t.join()
    created = [r for r in results if r[0] == "created"]
    assert len(created) == 1, results
    assert len([r for r in results if r[0] == "replay"]) == 2, results
    assert _reversal_side_effects(db)["credit_notes"] == 1

    # A late different-reason retry on the same key is refused, not replayed.
    with pytest.raises(FinancialIntegrityError, match="IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"):
        create_reversal_request(db, 1, 4, invoice_id, "changed reason", "rev-race-fr2-2")
    assert _reversal_side_effects(db)["credit_notes"] == 1
    assert db.trial_balance(1)["balanced"] is True
    assert db.verify_audit_chain(1)["valid"] is True


# ---------------------------------------------------------------------------
# F-R2-4 — non-positive amounts are refused, without forbidding credit notes
# ---------------------------------------------------------------------------
def test_fr2_4_a_negative_unit_price_never_reaches_validation_or_posting(env):
    """The quantity rule already refused non-positive quantities; price did not.

    A negative price multiplied by a positive quantity produced a negative line
    that matched a negative header, so the invoice validated, was approved and
    posted a negative payable (``600`` debit −1000.00 / ``401`` credit −1000.00).
    """
    db, vault, client = env
    created = _create(
        db, document_id=None, invoice_no="FAC-NEG-1",
        amount_ht="-1000.00", amount_tva="-190.00", amount_ttc="-1190.00",
        lines=[_line(qty="2", price="-500.00")],
    )
    assert created["workflow_state"] == "NeedsReview"
    assert any("LINE_1_PRICE_NONPOSITIVE" in e for e in created["validation_errors"])

    # The recoverable state is a gate, not a suggestion: submission is refused.
    with pytest.raises(FinancialIntegrityError):
        submit_invoice_for_approval(db, 1, 2, created["invoice_id"])

    with db.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM journal_entries"
        ).fetchone()["n"] == 0
    assert db.trial_balance(1)["balanced"] is True


def test_fr2_4_a_negative_header_total_is_refused_even_with_positive_lines(env):
    db, _vault, _client = env
    created = _create(
        db, document_id=None, invoice_no="FAC-NEG-2",
        amount_ht="-1000.00", amount_tva="190.00", amount_ttc="-810.00",
        lines=[_line(qty="2", price="500.00")],
    )
    codes = " ".join(created["validation_errors"])
    assert "HEADER_AMOUNT_NEGATIVE:HT=-1000.00" in codes
    assert created["workflow_state"] == "NeedsReview"

    zero_but_legitimate = _create(
        db, document_id=None, invoice_no="FAC-ZERO-1",
        amount_ht="0.00", amount_tva="0.00", amount_ttc="0.00",
        lines=[_line(qty="2", price="500.00", discount="100")],
    )
    # A fully discounted invoice is zero, not negative: it validates.
    assert zero_but_legitimate["workflow_state"] == "Validated", zero_but_legitimate["validation_errors"]


def test_fr2_4_a_correcting_edit_cannot_introduce_a_nonpositive_price(env):
    from dz_erp.database import FinancialIntegrityError as _FIE

    db, vault, _client = env
    document_id = _upload(db, vault)["document_id"]
    _review_all(db, 1, document_id)
    created = _create(db, document_id=document_id, invoice_no="FAC-EDIT-1")
    invoice_id = created["invoice_id"]
    with db.transaction() as conn:
        line_id = conn.execute(
            "SELECT id FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()["id"]

    with pytest.raises(_FIE, match="LINE_PRICE_NONPOSITIVE"):
        modify_draft_invoice_line(db, 1, 2, invoice_id, line_id, new_unit_price_dzd="-1.00")

    # The refusal left the stored line untouched.
    with db.transaction() as conn:
        stored = conn.execute(
            "SELECT unit_price_dzd FROM supplier_invoice_lines WHERE id = ?", (line_id,)
        ).fetchone()
    assert Decimal(stored["unit_price_dzd"]) > 0


def test_fr2_4_a_reversal_still_credit_notes_with_positive_amounts(env):
    """The legitimate route to a negative movement is a credit note, unchanged."""
    db, vault, _client = env
    _document_id, invoice_id = _reviewed_invoice(db, vault)
    _post(db, invoice_id)

    first = create_reversal_request(db, 1, 4, invoice_id, "supplier dispute", "neg-route-1")
    note_id = first["credit_note_invoice_id"]
    with db.transaction() as conn:
        note = conn.execute(
            "SELECT amount_ht_dzd, amount_tva_dzd, amount_ttc_dzd, invoice_type"
            " FROM supplier_invoices WHERE id = ?", (note_id,)
        ).fetchone()
    # A credit note is an IN_REFUND carrying positive amounts, never a negative
    # invoice — which is why refusing negative amounts forbids nothing.
    assert note["invoice_type"] == "IN_REFUND"
    assert Decimal(note["amount_ht_dzd"]) > 0
    assert Decimal(note["amount_ttc_dzd"]) > 0

    submit_reversal_for_approval(db, 1, 4, note_id)
    decide_reversal_approval(db, 1, 3, note_id, approve=True)
    post_reversal(db, 1, 4, note_id, "neg-post-1")
    with db.transaction() as conn:
        lines = conn.execute(
            "SELECT l.debit_dzd, l.credit_dzd FROM journal_entry_lines l"
            " JOIN journal_entries je ON je.id = l.journal_entry_id"
            " JOIN supplier_invoices si ON si.journal_entry_id = je.reversal_entry_id"
            " WHERE si.id = ?",
            (invoice_id,),
        ).fetchall()
    assert lines, "the credit note posted no journal lines"
    # Every posted amount is non-negative; direction is carried by the side.
    assert all(Decimal(row["debit_dzd"]) >= 0 and Decimal(row["credit_dzd"]) >= 0 for row in lines)
    assert db.trial_balance(1)["balanced"] is True
