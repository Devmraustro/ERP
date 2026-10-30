# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 4 — Procurement, Multi-Warehouse Stock, Three-Way Matching, Approval & Idempotent GL Posting."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .database import AuthorizationError, ERPDatabase, FinancialIntegrityError, TenantIsolationError
from .decimal_utils import format_dzd, round_dzd, round_qty, to_decimal
from .intake_ocr import DOCUMENT_LINKABLE_STATES, levenshtein_distance, normalize_invoice_ref
from .localization import calculate_droit_de_timbre, validate_algerian_identifiers
from .review_policy import evaluate_document_review_policy


# Phase 33 (P32-F2): the only invoice states a correction may start from. A
# posted or approved record is never rewritten — that is what the reversal
# credit note is for — and a rejected invoice is terminal by design.
# ``Validated`` is included so that re-validation is idempotent and a draft
# corrected before submission keeps its identity instead of needing a new
# invoice; nothing downstream has been approved or posted at that point.
REVISABLE_INVOICE_STATES = ("NeedsReview", "DuplicateRejected", "Validated")

# States that do not represent a live claim on a source document. A rejected
# attempt is still stored (its refusal is evidence) but it neither owns the
# document nor is it treated as a competing invoice.
NON_LIVE_INVOICE_STATES = ("Rejected", "DuplicateRejected")


def compute_invoice_payload_hash(
    supplier_id: int,
    supplier_invoice_no: str,
    invoice_date: str,
    payment_method: str,
    amount_ht_dzd: str,
    amount_tva_dzd: str,
    amount_timbre_dzd: str,
    amount_ttc_dzd: str,
    lines: List[Dict[str, Any]],
) -> str:
    canonical = {
        "supplier_id": supplier_id,
        "supplier_invoice_no": supplier_invoice_no.strip(),
        "invoice_date": invoice_date.strip(),
        "payment_method": payment_method.strip().upper(),
        "amount_ht_dzd": format_dzd(amount_ht_dzd),
        "amount_tva_dzd": format_dzd(amount_tva_dzd),
        "amount_timbre_dzd": format_dzd(amount_timbre_dzd),
        "amount_ttc_dzd": format_dzd(amount_ttc_dzd),
        "lines": [
            {
                "product_id": line.get("product_id"),
                "po_line_id": line.get("po_line_id"),
                "qty": f"{round_qty(line['qty']):.6f}",
                "unit_price_dzd": f"{round_qty(line['unit_price_dzd']):.6f}",
                "discount_pct": format_dzd(line.get("discount_pct", "0.00")),
                "tax_rate": f"{to_decimal(line.get('tax_rate', '19.0')):.1f}",
                "line_ht_dzd": format_dzd(line["line_ht_dzd"]),
            }
            for line in lines
        ],
    }
    raw = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _check_user_and_tenant(conn: Any, company_id: int, user_id: int, allowed_roles: Tuple[str, ...]) -> Any:
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or not user["is_active"]:
        raise AuthorizationError("Invalid or inactive user credentials.")
    if user["company_id"] != company_id:
        raise TenantIsolationError(
            f"Tenant isolation violation: user {user_id} (company {user['company_id']}) cannot access company {company_id}."
        )
    if user["role"] not in allowed_roles:
        raise AuthorizationError(
            f"Role '{user['role']}' is not authorized for this action; required one of {allowed_roles}."
        )
    return user


def _source_invoice_mismatches(
    conn: Any,
    company_id: int,
    invoice: Any,
    evaluation: Dict[str, Any],
) -> List[str]:
    """Compare the reviewed source values with the financial invoice payload.

    Review completeness alone is insufficient: a perfectly reviewed source
    document must not authorize a different invoice payload. This comparison
    is deliberately performed from the current rows in the caller's IMMEDIATE
    transaction and is repeated at submit and approval. All three reviewable
    line elements — quantity, unit price and discount — are compared against
    the invoice's lines in their canonical forms (6-decimal quantity/price,
    2-decimal discount), with exact equality, and each line's net amount is
    re-derived from the decided values and compared with the invoice line:
    the price and discount the reviewer bound to the paper are the price and
    discount the invoice must carry, and the net must follow from them.
    """
    fields = {item["field_name"]: item for item in evaluation["fields"]}
    mismatches: List[str] = []

    def source_value(name: str) -> Optional[str]:
        item = fields.get(name)
        return None if item is None or item["status"] == "missing" else item["value"]

    source_number = source_value("invoice_number")
    if source_number is None or normalize_invoice_ref(source_number) != normalize_invoice_ref(
        str(invoice["supplier_invoice_no"])
    ):
        mismatches.append("INVOICE_NUMBER_SOURCE_MISMATCH")

    source_date = source_value("invoice_date")
    if source_date is None or str(source_date).strip() != str(invoice["invoice_date"]).strip():
        mismatches.append("INVOICE_DATE_SOURCE_MISMATCH")

    source_currency = source_value("currency_code")
    if source_currency is None or str(source_currency).strip().upper() != str(invoice["currency_code"]).strip().upper():
        mismatches.append("CURRENCY_SOURCE_MISMATCH")

    for field_name, invoice_column in (
        ("amount_ht_dzd", "amount_ht_dzd"),
        ("amount_tva_dzd", "amount_tva_dzd"),
        ("amount_timbre_dzd", "amount_timbre_dzd"),
        ("amount_ttc_dzd", "amount_ttc_dzd"),
    ):
        raw_source = source_value(field_name)
        # A missing timbre is explicitly permitted by the review policy and is
        # equivalent to zero only for this comparison. Other missing totals
        # fail closed rather than silently defaulting.
        if raw_source is None and field_name == "amount_timbre_dzd":
            raw_source = "0.00"
        try:
            if raw_source is None or round_dzd(raw_source) != round_dzd(invoice[invoice_column]):
                mismatches.append(f"{field_name.upper()}_SOURCE_MISMATCH")
        except (ValueError, TypeError, ArithmeticError):
            mismatches.append(f"{field_name.upper()}_SOURCE_UNPARSEABLE")

    supplier = conn.execute(
        "SELECT nif FROM partners WHERE id = ? AND company_id = ?",
        (invoice["supplier_id"], company_id),
    ).fetchone()
    source_nif = source_value("supplier_nif")
    if (
        supplier is None
        or not supplier["nif"]
        or source_nif is None
        or "".join(ch for ch in str(source_nif) if ch.isdigit())
        != "".join(ch for ch in str(supplier["nif"]) if ch.isdigit())
    ):
        mismatches.append("SUPPLIER_NIF_SOURCE_MISMATCH")

    source_lines = evaluation["lines"]
    # The review policy intentionally allows documents with no extracted line
    # items: the invoice lines are then entered by a human and there is no
    # source line evidence to compare. Do not interpret "no extracted lines"
    # as "the invoice must have zero lines". When source lines exist, their
    # count, quantities and unit prices must match exactly.
    #
    # Line association invariant: invoice lines are matched to source lines by
    # row order — source lines sorted by their extraction ``line_index``,
    # invoice lines by insertion ``id``. The invoice line table stores no
    # supplier SKU to join on, so entering the invoice's lines in a different
    # order than the paper's rows is itself a mismatch and is flagged
    # positionally, never silently rematched (the swap regression test pins
    # this).
    if source_lines:
        invoice_lines = conn.execute(
            "SELECT qty, unit_price_dzd, discount_pct, line_ht_dzd FROM supplier_invoice_lines"
            " WHERE company_id = ? AND invoice_id = ? ORDER BY id",
            (company_id, invoice["id"]),
        ).fetchall()
        if len(source_lines) != len(invoice_lines):
            mismatches.append("LINE_COUNT_SOURCE_MISMATCH")
        else:
            line_prices = evaluation.get("line_prices") or []
            if len(line_prices) != len(source_lines):
                # The evaluator emits one price evaluation per extracted line
                # row; a divergence means a corrupted evaluation. Refuse to
                # compare rather than guess which price belongs to which line.
                mismatches.append("LINE_PRICE_EVIDENCE_INCONSISTENT")
            line_discounts = evaluation.get("line_discounts") or []
            if len(line_discounts) != len(source_lines):
                mismatches.append("LINE_DISCOUNT_EVIDENCE_INCONSISTENT")
            for index, (source_line, invoice_line, price_eval, discount_eval) in enumerate(
                zip(source_lines, invoice_lines, line_prices, line_discounts), start=1
            ):
                try:
                    source_qty = round_qty(to_decimal(str(source_line["value"])))
                    invoice_qty = round_qty(to_decimal(str(invoice_line["qty"])))
                except (ValueError, TypeError, ArithmeticError):
                    mismatches.append(f"LINE_{index}_QUANTITY_SOURCE_UNPARSEABLE")
                    continue
                if source_qty != invoice_qty:
                    mismatches.append(f"LINE_{index}_QUANTITY_SOURCE_MISMATCH")
                # zip truncates to the shortest, so a short price/discount
                # list can only shrink the compared prefix — the count
                # divergence was already flagged above, which blocks
                # regardless.
                raw_source_price = price_eval["value"]
                try:
                    source_price = round_qty(to_decimal(str(raw_source_price)))
                    invoice_price = round_qty(to_decimal(str(invoice_line["unit_price_dzd"])))
                except (ValueError, TypeError, ArithmeticError):
                    mismatches.append(f"LINE_{index}_PRICE_SOURCE_UNPARSEABLE")
                    continue
                if raw_source_price is None or source_price != invoice_price:
                    mismatches.append(f"LINE_{index}_PRICE_SOURCE_MISMATCH")
                # The decided discount is the third binding: exact 2-decimal
                # equality with the invoice line's first-class discount
                # column. An invoice discount is never clamped or defaulted:
                # an unparseable or missing reading is refused, not treated
                # as zero.
                raw_source_discount = discount_eval["value"]
                try:
                    source_discount = round_dzd(to_decimal(str(raw_source_discount)))
                    invoice_discount = round_dzd(to_decimal(str(invoice_line["discount_pct"])))
                except (ValueError, TypeError, ArithmeticError):
                    mismatches.append(f"LINE_{index}_DISCOUNT_SOURCE_UNPARSEABLE")
                    continue
                if raw_source_discount is None or source_discount != invoice_discount:
                    mismatches.append(f"LINE_{index}_DISCOUNT_SOURCE_MISMATCH")
                # Line-net comparison, only where the representations are
                # semantically equivalent: both sides mean the same
                # ``qty × PU × (1 − discount/100)`` rounded to 2 decimals
                # (``round_dzd``, ROUND_HALF_UP). The source net is derived
                # from the *decided* quantity, price and discount — never
                # from the payload's stored ``line_ht_dzd`` reading, which is
                # a derived value nobody decided. Documented rounding policy:
                # the decided 2-decimal discount is authoritative; an invoice
                # whose net was validated against a raw discount with more
                # precision than the canonical form may be flagged here by up
                # to a centime — the honest repair is entering the invoice
                # with the canonical 2-decimal discount.
                derived_source_ht = round_dzd(
                    source_qty * source_price * ((Decimal("100.00") - source_discount) / Decimal("100.00"))
                )
                try:
                    invoice_ht = round_dzd(to_decimal(str(invoice_line["line_ht_dzd"])))
                except (ValueError, TypeError, ArithmeticError):
                    mismatches.append(f"LINE_{index}_LINE_HT_SOURCE_UNPARSEABLE")
                    continue
                if derived_source_ht != invoice_ht:
                    mismatches.append(
                        f"LINE_{index}_LINE_HT_SOURCE_MISMATCH:derived={derived_source_ht:.2f}"
                        f"!=invoiced={invoice_ht:.2f}"
                    )

    return mismatches


class _RefusedAttempt(FinancialIntegrityError):
    """A refusal whose audit record must outlive the rollback of the attempt.

    ``db.transaction()`` rolls back everything written inside the failed
    attempt, including an audit event appended there. A refusal is itself
    evidence an auditor needs — who tried, against which invoice, and why it
    was refused — so the event is carried on the exception and written by the
    public function in its own transaction after the rollback.
    """

    def __init__(
        self,
        message: str,
        *,
        action_type: str,
        target_model: str,
        target_id: str,
        after_state: Dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.audit_action_type = action_type
        self.audit_target_model = target_model
        self.audit_target_id = target_id
        self.audit_after_state = after_state


def _record_refused_attempt(db: ERPDatabase, company_id: int, user_id: int, refusal: _RefusedAttempt) -> None:
    """Commit the audit record of a refused write (never the write itself)."""
    with db.transaction() as conn:
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type=refusal.audit_action_type,
            target_model=refusal.audit_target_model,
            target_id=refusal.audit_target_id,
            after_state=refusal.audit_after_state,
        )


def _assert_document_linkable(conn: Any, company_id: int, document_id: str) -> None:
    """A draft may cite a source document only while that document is in review."""
    linked_doc = conn.execute(
        "SELECT workflow_state FROM source_documents WHERE id = ? AND company_id = ?",
        (document_id, company_id),
    ).fetchone()
    if not linked_doc:
        raise TenantIsolationError("Source document does not belong to target company.")
    if linked_doc["workflow_state"] not in DOCUMENT_LINKABLE_STATES:
        raise FinancialIntegrityError(
            f"DOCUMENT_LINK_STATE_REFUSED: document {document_id} is in state "
            f"'{linked_doc['workflow_state']}'; a draft invoice may only cite a "
            f"document in one of {DOCUMENT_LINKABLE_STATES}."
        )


def _live_document_owner(
    conn: Any, company_id: int, document_id: str, *, exclude_invoice_id: Optional[int] = None
) -> Any:
    """The invoice that currently owns a source document, or ``None``."""
    placeholders = ",".join("?" for _ in NON_LIVE_INVOICE_STATES)
    return conn.execute(
        f"""
        SELECT id, workflow_state, supplier_invoice_no FROM supplier_invoices
        WHERE company_id = ? AND document_id = ?
          AND workflow_state NOT IN ({placeholders})
          AND (? IS NULL OR id != ?)
        ORDER BY id
        LIMIT 1
        """,
        (company_id, document_id, *NON_LIVE_INVOICE_STATES, exclude_invoice_id, exclude_invoice_id),
    ).fetchone()


def _refresh_document_state(
    conn: Any, company_id: int, document_id: Optional[str], workflow_state: str, *, invoice_id: int
) -> bool:
    """Drive a source document's state from the invoice that owns it.

    Phase 33 (P32-F3): the evidence row follows its *owner*. When another live
    invoice already cites the document, this call leaves the document alone
    instead of overwriting the state that invoice relies on.
    """
    if not document_id:
        return False
    owner = _live_document_owner(conn, company_id, document_id)
    if owner is not None and int(owner["id"]) != int(invoice_id):
        return False
    conn.execute(
        "UPDATE source_documents SET workflow_state = ? WHERE id = ? AND company_id = ?",
        (workflow_state, document_id, company_id),
    )
    return True


def _source_review_fingerprint(conn: Any, company_id: int, document_id: Optional[str]) -> Optional[str]:
    """Digest of the review evidence an approval request is bound to.

    Phase 33 (P32-F4): an approval must decide the same invoice payload against
    the *same* reviewed evidence. The digest covers the document identity and
    every decision row (id, field, decided value, reviewer, timestamp) — not the
    document's workflow state, which legitimately moves with the invoice
    between submission and approval.
    """
    if not document_id:
        return None
    doc = conn.execute(
        "SELECT id FROM source_documents WHERE id = ? AND company_id = ?",
        (document_id, company_id),
    ).fetchone()
    if doc is None:
        return None
    rows = conn.execute(
        """
        SELECT id, field_name, line_index, decision, reviewed_value, reviewed_by_id, reviewed_at
        FROM ocr_field_reviews
        WHERE company_id = ? AND document_id = ?
        ORDER BY id
        """,
        (company_id, document_id),
    ).fetchall()
    canonical = {
        "document_id": doc["id"],
        "decisions": [
            [
                int(r["id"]),
                r["field_name"],
                r["line_index"],
                r["decision"],
                r["reviewed_value"],
                int(r["reviewed_by_id"]),
                r["reviewed_at"],
            ]
            for r in rows
        ],
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def _invalidate_approval_requests(
    conn: Any,
    company_id: int,
    invoice_id: int,
    *,
    reason: str,
    actor_user_id: int,
    now_iso: str,
    target_model: str = "supplier_invoices",
    include_approved: bool = True,
) -> List[int]:
    """Explicitly invalidate the approvals a modified payload can no longer rely on."""
    states = ("PENDING", "APPROVED") if include_approved else ("PENDING",)
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"""
        SELECT id FROM approval_requests
        WHERE company_id = ? AND target_model = ? AND target_id = ?
          AND decision IN ({placeholders})
        ORDER BY id
        """,
        (company_id, target_model, invoice_id, *states),
    ).fetchall()
    request_ids = [int(r["id"]) for r in rows]
    if request_ids:
        marks = ",".join("?" for _ in request_ids)
        conn.execute(
            f"""
            UPDATE approval_requests
            SET decision = 'INVALIDATED', decided_by_id = ?, decided_at = ?, reason = ?
            WHERE id IN ({marks})
            """,
            (actor_user_id, now_iso, reason, *request_ids),
        )
    return request_ids


def _duplicate_invoice_errors(
    conn: Any,
    *,
    company_id: int,
    supplier_id: int,
    fiscal_year: int,
    norm_ref: str,
    ttc_dec: Decimal,
    exclude_invoice_id: Optional[int] = None,
) -> Tuple[List[str], bool]:
    """Exact and fuzzy duplicate detection, scoped to the tenant.

    ``exclude_invoice_id`` lets a *re-validation* of an existing invoice ignore
    itself, so correcting a draft is never mistaken for re-creating it.
    """
    existing_invs = conn.execute(
        """
        SELECT id, supplier_invoice_no, normalized_invoice_no, amount_ttc_dzd, workflow_state
        FROM supplier_invoices
        WHERE company_id = ? AND supplier_id = ? AND fiscal_year = ?
          AND workflow_state NOT IN ('Rejected', 'DuplicateRejected')
          AND invoice_type = 'IN_INVOICE'
          AND (? IS NULL OR id != ?)
        """,
        (company_id, supplier_id, fiscal_year, exclude_invoice_id, exclude_invoice_id),
    ).fetchall()

    errors: List[str] = []
    is_exact_dup = False
    for ex in existing_invs:
        if ex["normalized_invoice_no"] == norm_ref:
            is_exact_dup = True
            errors.append(f"EXACT_DUPLICATE_INVOICE_REF_OF_ID_{ex['id']}")
            break
        dist = levenshtein_distance(ex["normalized_invoice_no"], norm_ref)
        if dist <= 2 and abs(to_decimal(ex["amount_ttc_dzd"]) - ttc_dec) <= Decimal("1.00"):
            errors.append(f"FUZZY_DUPLICATE_SUSPECTED:existing='{ex['supplier_invoice_no']}',dist={dist}")
    return errors, is_exact_dup


def _validate_invoice_lines(
    conn: Any,
    *,
    company_id: int,
    lines: List[Dict[str, Any]],
    price_tolerance_dzd: str,
) -> Tuple[List[str], List[Dict[str, Any]], Decimal, Decimal]:
    """Per-line arithmetic, Algerian TVA rates, discount bounds and 3-way match."""
    errors: List[str] = []
    prepared: List[Dict[str, Any]] = []
    sum_line_ht = Decimal("0.00")
    sum_line_tva = Decimal("0.00")

    for idx, line in enumerate(lines):
        qty = round_qty(line["qty"])
        pu = round_qty(line["unit_price_dzd"])
        disc = to_decimal(line.get("discount_pct", "0.00"))
        rate = to_decimal(line.get("tax_rate", "19.0"))
        if rate not in (Decimal("0.0"), Decimal("9.0"), Decimal("19.0")):
            errors.append(f"LINE_{idx + 1}_INVALID_ALGERIAN_TVA_RATE:{rate}")
        # Documented business rule (Phase 31): a discount is a percentage in
        # [0, 100] inclusive. A negative or >100 discount is refused as a
        # validation error — never clamped into range — so the invoice stays in
        # NeedsReview and cannot be submitted.
        if disc < Decimal("0.00") or disc > Decimal("100.00"):
            errors.append(f"LINE_{idx + 1}_DISCOUNT_OUT_OF_RANGE:{disc}")
        if qty <= Decimal("0.000000"):
            errors.append(f"LINE_{idx + 1}_QTY_NONPOSITIVE:{qty}")

        expected_line_ht = round_dzd(qty * pu * ((Decimal("100.00") - disc) / Decimal("100.00")))
        declared_line_ht = round_dzd(line.get("line_ht_dzd", expected_line_ht))
        if declared_line_ht != expected_line_ht:
            errors.append(
                f"LINE_{idx + 1}_HT_MATH_MISMATCH:declared={declared_line_ht:.2f}!=expected={expected_line_ht:.2f}"
            )

        line_tva = round_dzd(declared_line_ht * (rate / Decimal("100.00")))
        line_ttc = round_dzd(declared_line_ht + line_tva)
        sum_line_ht = round_dzd(sum_line_ht + declared_line_ht)
        sum_line_tva = round_dzd(sum_line_tva + line_tva)

        po_line_id = line.get("po_line_id")
        if po_line_id is not None:
            po_line = conn.execute(
                "SELECT * FROM purchase_order_lines WHERE id = ? AND company_id = ?",
                (po_line_id, company_id),
            ).fetchone()
            if not po_line:
                errors.append(f"LINE_{idx + 1}_PO_LINE_NOT_FOUND:{po_line_id}")
            else:
                rem_receivable_to_bill = round_qty(
                    to_decimal(po_line["qty_received"]) - to_decimal(po_line["qty_invoiced"])
                )
                if qty > rem_receivable_to_bill:
                    errors.append(
                        f"THREE_WAY_MATCH_QTY_MISMATCH:line={idx + 1},billed={qty:.6f}>unbilled_received={rem_receivable_to_bill:.6f}"
                    )
                po_pu = round_qty(po_line["unit_price_dzd"])
                if abs(pu - po_pu) > to_decimal(price_tolerance_dzd):
                    errors.append(
                        f"THREE_WAY_MATCH_PRICE_MISMATCH:line={idx + 1},invoice_pu={pu:.6f}!=po_pu={po_pu:.6f}"
                    )

        prepared.append(
            {
                "po_line_id": po_line_id,
                "product_id": line.get("product_id"),
                "description": line.get("description", f"Line {idx + 1}"),
                "qty": f"{qty:.6f}",
                "unit_price_dzd": f"{pu:.6f}",
                "discount_pct": format_dzd(disc),
                "tax_rate": f"{rate:.1f}",
                "line_ht_dzd": format_dzd(declared_line_ht),
                "line_tva_dzd": format_dzd(line_tva),
                "line_ttc_dzd": format_dzd(line_ttc),
            }
        )
    return errors, prepared, sum_line_ht, sum_line_tva


def _validate_invoice_totals(
    *,
    ht_dec: Decimal,
    tva_dec: Decimal,
    timbre_dec: Decimal,
    ttc_dec: Decimal,
    payment_method: str,
    sum_line_ht: Decimal,
    sum_line_tva: Decimal,
) -> List[str]:
    """Header HT/TVA/TTC reconciliation and the Algerian timbre rule."""
    errors: List[str] = []
    if sum_line_ht != ht_dec:
        errors.append(f"HEADER_HT_MISMATCH:sum_lines={sum_line_ht:.2f}!=header_ht={ht_dec:.2f}")
    if sum_line_tva != tva_dec:
        errors.append(f"HEADER_TVA_MISMATCH:sum_tva={sum_line_tva:.2f}!=header_tva={tva_dec:.2f}")
    expected_timbre = calculate_droit_de_timbre(ht_dec + tva_dec, payment_method=payment_method)
    if timbre_dec != expected_timbre:
        errors.append(
            f"TIMBRE_MISMATCH:declared={timbre_dec:.2f}!=expected={expected_timbre:.2f}_for_{payment_method}"
        )
    expected_ttc = round_dzd(ht_dec + tva_dec + timbre_dec)
    if ttc_dec != expected_ttc:
        errors.append(f"TOTAL_TTC_MISMATCH:declared={ttc_dec:.2f}!=expected={expected_ttc:.2f}")
    return errors


def _insert_invoice_lines(conn: Any, company_id: int, invoice_id: int, prepared_lines: List[Dict[str, Any]]) -> None:
    for pl in prepared_lines:
        conn.execute(
            """
            INSERT INTO supplier_invoice_lines (
                company_id, invoice_id, po_line_id, product_id, description,
                qty, unit_price_dzd, discount_pct, tax_rate,
                line_ht_dzd, line_tva_dzd, line_ttc_dzd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                invoice_id,
                pl["po_line_id"],
                pl["product_id"],
                pl["description"],
                pl["qty"],
                pl["unit_price_dzd"],
                pl["discount_pct"],
                pl["tax_rate"],
                pl["line_ht_dzd"],
                pl["line_tva_dzd"],
                pl["line_ttc_dzd"],
            ),
        )


def _document_review_gate(
    conn: Any,
    db: ERPDatabase,
    company_id: int,
    invoice: Any,
    actor_user_id: int,
    *,
    action: str,
    audit: bool = True,
) -> Optional[FinancialIntegrityError]:
    """Phase 29 fail-closed gate: the linked document's field review must hold.

    Evaluates :func:`dz_erp.review_policy.evaluate_document_review_policy`
    from the rows visible inside the caller's IMMEDIATE transaction, so the
    verdict cannot race a concurrent field edit or decision (TOCTOU-safe: the
    same lock that will move the invoice forward also reads the evidence the
    gate judges). Invoices without a linked document pass untouched — their
    inputs were typed by a human at creation time, and deterministic
    validation, SoD and payload-hash binding still apply.

    Returns ``None`` when satisfied; otherwise audits the blocked attempt
    (committed with the caller's transaction via the commit-then-refuse
    pattern) and returns the error the caller must raise after the
    transaction body completes.
    """
    document_id = invoice["document_id"]
    if not document_id:
        return None
    doc = conn.execute(
        "SELECT * FROM source_documents WHERE id = ? AND company_id = ?",
        (document_id, company_id),
    ).fetchone()
    if not doc:
        return FinancialIntegrityError(
            f"DOCUMENT_REVIEW_UNSATISFIED: linked document {document_id} not found in company {company_id}."
        )
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
    evaluation = evaluate_document_review_policy(dict(doc), fields, decisions)
    if evaluation["satisfied"]:
        source_mismatches = _source_invoice_mismatches(conn, company_id, invoice, evaluation)
        if source_mismatches:
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=actor_user_id,
                action_type=f"INVOICE_{action}_BLOCKED_SOURCE_MISMATCH",
                target_model="supplier_invoices",
                target_id=str(invoice["id"]),
                after_state={
                    "document_id": document_id,
                    "mismatches": source_mismatches[:20],
                    "mismatches_truncated": len(source_mismatches) > 20,
                },
            )
            return FinancialIntegrityError(
                "DOCUMENT_INVOICE_MISMATCH: the invoice payload differs from its reviewed source document — "
                + "; ".join(source_mismatches[:20])
            )
        return None
    blocking = evaluation["blocking"]
    audit_state = {
        "document_id": document_id,
        "document_state": doc["workflow_state"],
        "blocking": blocking[:20],
        "blocking_truncated": len(blocking) > 20,
    }
    if audit:
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=actor_user_id,
            action_type=f"INVOICE_{action}_BLOCKED_UNREVIEWED",
            target_model="supplier_invoices",
            target_id=str(invoice["id"]),
            after_state=audit_state,
        )
    # Returned rather than raised: the caller decides, so that a caller which
    # must roll its transaction back can record the refusal durably afterwards.
    denial = FinancialIntegrityError(
        "DOCUMENT_REVIEW_UNSATISFIED: invoice cannot proceed while its source document "
        "has undecided, flagged or stale field reviews — " + "; ".join(blocking[:20])
    )
    denial.audit_action_type = f"INVOICE_{action}_BLOCKED_UNREVIEWED"
    denial.audit_after_state = audit_state
    return denial


def create_purchase_order(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    po_number: str,
    supplier_id: int,
    lines: List[Dict[str, Any]],
    branch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Create and confirm a Purchase Order with decimal-validated quantities and prices."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("clerk", "approver", "accountant", "admin"))
        sup = conn.execute("SELECT * FROM partners WHERE id = ? AND company_id = ?", (supplier_id, company_id)).fetchone()
        if not sup:
            raise TenantIsolationError("Supplier does not exist in target company.")

        cur = conn.execute(
            """
            INSERT INTO purchase_orders (
                company_id, branch_id, po_number, supplier_id, currency_code,
                state, created_by_id, created_at
            ) VALUES (?, ?, ?, ?, 'DZD', 'CONFIRMED', ?, ?)
            """,
            (company_id, branch_id, po_number, supplier_id, user_id, now_iso),
        )
        po_id = int(cur.lastrowid)
        po_line_ids: List[int] = []
        for line in lines:
            prod = conn.execute(
                "SELECT * FROM products WHERE id = ? AND company_id = ?",
                (line["product_id"], company_id),
            ).fetchone()
            if not prod:
                raise TenantIsolationError("Product does not belong to target company.")
            qty = round_qty(line["qty_ordered"])
            price = round_qty(line["unit_price_dzd"])
            tax_rate = to_decimal(line.get("tax_rate", "19.0"))
            lcur = conn.execute(
                """
                INSERT INTO purchase_order_lines (
                    company_id, po_id, product_id, description,
                    qty_ordered, qty_received, qty_invoiced, unit_price_dzd, tax_rate
                ) VALUES (?, ?, ?, ?, ?, '0.000000', '0.000000', ?, ?)
                """,
                (company_id, po_id, prod["id"], line.get("description", prod["name"]), f"{qty:.6f}", f"{price:.6f}", f"{tax_rate:.1f}"),
            )
            po_line_ids.append(int(lcur.lastrowid))

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="PO_CONFIRMED",
            target_model="purchase_orders",
            target_id=str(po_id),
            after_state={"po_number": po_number, "supplier_id": supplier_id, "line_ids": po_line_ids},
        )
        return {"po_id": po_id, "po_number": po_number, "po_line_ids": po_line_ids, "state": "CONFIRMED"}


def receive_goods_against_po(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    po_id: int,
    grn_number: str,
    receipt_lines: List[Dict[str, Any]],
    warehouse_code: str = "WH-MAIN",
    idempotency_key: Optional[str] = None,
    branch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate a physical Goods Receipt (or Return), update Stock Quants, post Stock Clearing JE, and create Backorder if partial."""
    now_iso = datetime.now(timezone.utc).isoformat()
    req_hash = hashlib.sha256(
        json.dumps({"po_id": po_id, "grn_number": grn_number, "lines": receipt_lines}, sort_keys=True).encode("utf-8")
    ).hexdigest()

    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("warehouse", "accountant", "admin"))
        if idempotency_key:
            idem = conn.execute(
                "SELECT * FROM idempotency_records WHERE company_id = ? AND idempotency_key = ? AND operation = 'RECEIVE_GOODS'",
                (company_id, idempotency_key),
            ).fetchone()
            if idem:
                if idem["request_sha256"] != req_hash:
                    raise FinancialIntegrityError("Idempotency key reused with different payload.")
                cached = json.loads(idem["response_json"])
                cached["idempotent_replay"] = True
                return cached

        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ? AND company_id = ?", (po_id, company_id)).fetchone()
        if not po:
            raise TenantIsolationError("Purchase order not found in company.")

        cur = conn.execute(
            """
            INSERT INTO goods_receipts (
                company_id, branch_id, grn_number, po_id, warehouse_code,
                picking_type, state, validated_by_id, validated_at
            ) VALUES (?, ?, ?, ?, ?, 'IN', 'DONE', ?, ?)
            """,
            (company_id, branch_id, grn_number, po_id, warehouse_code, user_id, now_iso),
        )
        grn_id = int(cur.lastrowid)

        total_valuation = Decimal("0.00")
        stock_je_lines: List[Dict[str, Any]] = []

        for rline in receipt_lines:
            po_line = conn.execute(
                "SELECT * FROM purchase_order_lines WHERE id = ? AND po_id = ? AND company_id = ?",
                (rline["po_line_id"], po_id, company_id),
            ).fetchone()
            if not po_line:
                raise FinancialIntegrityError(f"PO line {rline['po_line_id']} not found on PO {po_id}.")
            prod = conn.execute("SELECT * FROM products WHERE id = ?", (po_line["product_id"],)).fetchone()

            qty_done = round_qty(rline["qty_done"])
            if qty_done <= Decimal("0"):
                raise FinancialIntegrityError("Receipt quantity must be strictly positive.")

            already_rec = round_qty(po_line["qty_received"])
            ordered = round_qty(po_line["qty_ordered"])
            if already_rec + qty_done > ordered:
                raise FinancialIntegrityError(
                    f"Receipt quantity {qty_done} exceeds remaining ordered quantity {ordered - already_rec}."
                )

            unit_cost = round_qty(po_line["unit_price_dzd"])
            line_val = round_dzd(qty_done * unit_cost)
            total_valuation = round_dzd(total_valuation + line_val)

            conn.execute(
                """
                INSERT INTO goods_receipt_lines (company_id, grn_id, po_line_id, product_id, qty_done, unit_cost_dzd)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (company_id, grn_id, po_line["id"], prod["id"], f"{qty_done:.6f}", f"{unit_cost:.6f}"),
            )

            new_rec_qty = round_qty(already_rec + qty_done)
            conn.execute(
                "UPDATE purchase_order_lines SET qty_received = ? WHERE id = ?",
                (f"{new_rec_qty:.6f}", po_line["id"]),
            )

            if prod["is_storable"]:
                quant = conn.execute(
                    "SELECT * FROM stock_quants WHERE company_id = ? AND warehouse_code = ? AND product_id = ?",
                    (company_id, warehouse_code, prod["id"]),
                ).fetchone()
                if quant:
                    updated_q = round_qty(to_decimal(quant["qty_on_hand"]) + qty_done)
                    conn.execute(
                        "UPDATE stock_quants SET qty_on_hand = ? WHERE id = ?",
                        (f"{updated_q:.6f}", quant["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO stock_quants (company_id, warehouse_code, product_id, qty_on_hand) VALUES (?, ?, ?, ?)",
                        (company_id, warehouse_code, prod["id"], f"{qty_done:.6f}"),
                    )

                conn.execute(
                    """
                    INSERT INTO stock_moves (
                        company_id, grn_id, product_id, warehouse_code, direction,
                        qty, unit_cost_dzd, total_valuation_dzd, created_at
                    ) VALUES (?, ?, ?, ?, 'IN', ?, ?, ?, ?)
                    """,
                    (company_id, grn_id, prod["id"], warehouse_code, f"{qty_done:.6f}", f"{unit_cost:.6f}", format_dzd(line_val), now_iso),
                )
                stock_je_lines.append(
                    {
                        "account_code": prod["stock_account_code"],
                        "label": f"Stock Receipt {grn_number} - {prod['sku']}",
                        "debit_dzd": format_dzd(line_val),
                        "credit_dzd": "0.00",
                    }
                )
                stock_je_lines.append(
                    {
                        "account_code": prod["clearing_account_code"],
                        "label": f"Stock Clearing (Achats stockés) {grn_number} - {prod['sku']}",
                        "debit_dzd": "0.00",
                        "credit_dzd": format_dzd(line_val),
                    }
                )

        # Post Stock Valuation Journal Entry
        je_id: Optional[int] = None
        if stock_je_lines:
            entry_no = f"STJ/{company_id}/{grn_id:05d}"
            jcur = conn.execute(
                """
                INSERT INTO journal_entries (
                    company_id, branch_id, entry_number, journal_code, entry_date,
                    state, source_model, source_id, total_debit_dzd, total_credit_dzd,
                    posted_by_id, posted_at
                ) VALUES (?, ?, ?, 'STK', ?, 'POSTED', 'goods_receipts', ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    branch_id,
                    entry_no,
                    now_iso[:10],
                    str(grn_id),
                    format_dzd(total_valuation),
                    format_dzd(total_valuation),
                    user_id,
                    now_iso,
                ),
            )
            je_id = int(jcur.lastrowid)
            for jl in stock_je_lines:
                conn.execute(
                    """
                    INSERT INTO journal_entry_lines (
                        company_id, journal_entry_id, account_code, partner_id,
                        label, debit_dzd, credit_dzd
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (company_id, je_id, jl["account_code"], po["supplier_id"], jl["label"], jl["debit_dzd"], jl["credit_dzd"]),
                )
            conn.execute("UPDATE goods_receipts SET journal_entry_id = ? WHERE id = ?", (je_id, grn_id))

        # Check if PO is fully received or partially received (create backorder draft if partial)
        all_po_lines = conn.execute("SELECT * FROM purchase_order_lines WHERE po_id = ?", (po_id,)).fetchall()
        all_received = all(round_qty(r["qty_received"]) >= round_qty(r["qty_ordered"]) for r in all_po_lines)
        po_state = "RECEIVED" if all_received else "PARTIALLY_RECEIVED"
        conn.execute("UPDATE purchase_orders SET state = ? WHERE id = ?", (po_state, po_id))

        backorder_grn_id: Optional[int] = None
        if not all_received:
            bo_cur = conn.execute(
                """
                INSERT INTO goods_receipts (
                    company_id, branch_id, grn_number, po_id, warehouse_code,
                    picking_type, backorder_of_id, state
                ) VALUES (?, ?, ?, ?, ?, 'IN', ?, 'DRAFT')
                """,
                (company_id, branch_id, f"{grn_number}-BO", po_id, warehouse_code, grn_id),
            )
            backorder_grn_id = int(bo_cur.lastrowid)

        result = {
            "grn_id": grn_id,
            "grn_number": grn_number,
            "po_state": po_state,
            "stock_journal_entry_id": je_id,
            "total_valuation_dzd": format_dzd(total_valuation),
            "backorder_grn_id": backorder_grn_id,
            "idempotent_replay": False,
        }
        if idempotency_key:
            conn.execute(
                """
                INSERT INTO idempotency_records (company_id, idempotency_key, operation, request_sha256, status, response_json, created_at)
                VALUES (?, ?, 'RECEIVE_GOODS', ?, 'COMPLETED', ?, ?)
                """,
                (company_id, idempotency_key, req_hash, json.dumps(result), now_iso),
            )

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="GOODS_RECEIPT_VALIDATED",
            target_model="goods_receipts",
            target_id=str(grn_id),
            after_state=result,
        )
        return result


def deduct_stock_concurrent_safe(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    warehouse_code: str,
    product_id: int,
    qty_to_deduct: Any,
) -> Dict[str, Any]:
    """Atomically deduct stock under IMMEDIATE transaction lock, preventing negative stock under concurrency."""
    qty_dec = round_qty(qty_to_deduct)
    if qty_dec <= Decimal("0"):
        raise FinancialIntegrityError("Deduction quantity must be positive.")

    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("warehouse", "accountant", "admin"))
        quant = conn.execute(
            "SELECT * FROM stock_quants WHERE company_id = ? AND warehouse_code = ? AND product_id = ?",
            (company_id, warehouse_code, product_id),
        ).fetchone()
        available = round_qty(quant["qty_on_hand"]) if quant else Decimal("0.000000")
        if available < qty_dec:
            raise FinancialIntegrityError(
                f"INSUFFICIENT_STOCK_ERROR: available={available:.6f} < requested={qty_dec:.6f}"
            )
        remaining = round_qty(available - qty_dec)
        conn.execute(
            "UPDATE stock_quants SET qty_on_hand = ? WHERE id = ?",
            (f"{remaining:.6f}", quant["id"]),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="STOCK_DEDUCTION",
            target_model="stock_quants",
            target_id=str(quant["id"]),
            before_state={"qty_on_hand": f"{available:.6f}"},
            after_state={"qty_on_hand": f"{remaining:.6f}", "deducted": f"{qty_dec:.6f}"},
        )
        return {"product_id": product_id, "warehouse_code": warehouse_code, "qty_on_hand": f"{remaining:.6f}"}


def create_and_validate_supplier_invoice(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    supplier_id: int,
    supplier_invoice_no: str,
    invoice_date: str,
    lines: List[Dict[str, Any]],
    amount_ht_dzd: Any,
    amount_tva_dzd: Any,
    amount_ttc_dzd: Any,
    amount_timbre_dzd: Any = "0.00",
    payment_method: str = "BANK_TRANSFER",
    po_id: Optional[int] = None,
    grn_id: Optional[int] = None,
    document_id: Optional[str] = None,
    price_tolerance_dzd: str = "0.00",
    branch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a supplier invoice draft and run deterministic validation + 3-way matching.

    Phase 33 (P32-F3): a refused attempt is evidence, not a side effect. A
    second invoice citing a source document that a live invoice already owns is
    refused outright (one paper, one invoice) and, in every case, a rejected
    attempt no longer writes the document's ``workflow_state`` — the original
    invoice and the evidence it relies on are left exactly as they were. The
    refusal's audit record is committed in its own transaction, after the
    attempt's rollback, so it survives; the attempted actor, company, document,
    invoice reference and reason are all recorded.
    """
    try:
        return _create_and_validate_supplier_invoice(
            db=db,
            company_id=company_id,
            user_id=user_id,
            supplier_id=supplier_id,
            supplier_invoice_no=supplier_invoice_no,
            invoice_date=invoice_date,
            lines=lines,
            amount_ht_dzd=amount_ht_dzd,
            amount_tva_dzd=amount_tva_dzd,
            amount_ttc_dzd=amount_ttc_dzd,
            amount_timbre_dzd=amount_timbre_dzd,
            payment_method=payment_method,
            po_id=po_id,
            grn_id=grn_id,
            document_id=document_id,
            price_tolerance_dzd=price_tolerance_dzd,
            branch_id=branch_id,
        )
    except _RefusedAttempt as refusal:
        _record_refused_attempt(db, company_id, user_id, refusal)
        raise


def _create_and_validate_supplier_invoice(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    supplier_id: int,
    supplier_invoice_no: str,
    invoice_date: str,
    lines: List[Dict[str, Any]],
    amount_ht_dzd: Any,
    amount_tva_dzd: Any,
    amount_ttc_dzd: Any,
    amount_timbre_dzd: Any = "0.00",
    payment_method: str = "BANK_TRANSFER",
    po_id: Optional[int] = None,
    grn_id: Optional[int] = None,
    document_id: Optional[str] = None,
    price_tolerance_dzd: str = "0.00",
    branch_id: Optional[int] = None,
) -> Dict[str, Any]:
    """The creating transaction (see the public wrapper for the refusal contract)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    norm_ref = normalize_invoice_ref(supplier_invoice_no)
    fiscal_year = int(invoice_date[:4])

    ht_dec = round_dzd(amount_ht_dzd)
    tva_dec = round_dzd(amount_tva_dzd)
    timbre_dec = round_dzd(amount_timbre_dzd)
    ttc_dec = round_dzd(amount_ttc_dzd)

    payload_hash = compute_invoice_payload_hash(
        supplier_id=supplier_id,
        supplier_invoice_no=supplier_invoice_no,
        invoice_date=invoice_date,
        payment_method=payment_method,
        amount_ht_dzd=format_dzd(ht_dec),
        amount_tva_dzd=format_dzd(tva_dec),
        amount_timbre_dzd=format_dzd(timbre_dec),
        amount_ttc_dzd=format_dzd(ttc_dec),
        lines=lines,
    )

    validation_errors: List[str] = []

    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("clerk", "accountant", "admin"))
        sup = conn.execute(
            "SELECT * FROM partners WHERE id = ? AND company_id = ?",
            (supplier_id, company_id),
        ).fetchone()
        if not sup:
            raise TenantIsolationError("Supplier does not belong to target company.")

        # Phase 29: a draft may cite a source document only while that document
        # is still in review. Phase 33: one paper backs at most one live invoice
        # — citing a document another invoice already owns would let a second
        # invoice ride the same evidence (and, before this fix, overwrite the
        # state that invoice relies on).
        if document_id:
            _assert_document_linkable(conn, company_id, document_id)
            owner = _live_document_owner(conn, company_id, document_id)
            if owner is not None:
                raise _RefusedAttempt(
                    f"DOCUMENT_ALREADY_INVOICED: source document {document_id} is already cited by "
                    f"invoice {owner['id']} ('{owner['supplier_invoice_no']}', state "
                    f"'{owner['workflow_state']}'). One paper backs one invoice: correct that "
                    f"invoice with POST /api/v1/invoices/{{id}}/revalidate instead of creating a "
                    f"second one.",
                    action_type="DUPLICATE_DOCUMENT_INVOICE_ATTEMPT_REFUSED",
                    target_model="source_documents",
                    target_id=document_id,
                    after_state={
                        "company_id": company_id,
                        "document_id": document_id,
                        "existing_invoice_id": int(owner["id"]),
                        "existing_invoice_state": owner["workflow_state"],
                        "attempted_supplier_invoice_no": supplier_invoice_no,
                        "attempted_supplier_id": supplier_id,
                        "attempted_amount_ttc_dzd": format_dzd(ttc_dec),
                        "refusal_reason": "DOCUMENT_ALREADY_INVOICED",
                    },
                )

        # 1. Validate Algerian supplier identifiers if domestic REEL supplier
        if sup["country_code"] == "DZ" and sup["tax_regime"] == "REEL":
            if not sup["nif"]:
                validation_errors.append("SUPPLIER_MISSING_NIF")
            else:
                id_errs = validate_algerian_identifiers(
                    nif=sup["nif"], nis=sup["nis"], ai=sup["ai"], rc=sup["rc"]
                )
                validation_errors.extend(id_errs)

        # 2. Check exact normalized duplicate & fuzzy near-duplicate
        duplicate_errors, is_exact_dup = _duplicate_invoice_errors(
            conn,
            company_id=company_id,
            supplier_id=supplier_id,
            fiscal_year=fiscal_year,
            norm_ref=norm_ref,
            ttc_dec=ttc_dec,
        )
        validation_errors.extend(duplicate_errors)

        # 3. Validate line arithmetic, tax rates, Timbre, and 3-way match against PO/Receipt
        line_errors, prepared_lines, sum_line_ht, sum_line_tva = _validate_invoice_lines(
            conn, company_id=company_id, lines=lines, price_tolerance_dzd=price_tolerance_dzd
        )
        validation_errors.extend(line_errors)
        validation_errors.extend(
            _validate_invoice_totals(
                ht_dec=ht_dec,
                tva_dec=tva_dec,
                timbre_dec=timbre_dec,
                ttc_dec=ttc_dec,
                payment_method=payment_method,
                sum_line_ht=sum_line_ht,
                sum_line_tva=sum_line_tva,
            )
        )

        if is_exact_dup:
            wf_state = "DuplicateRejected"
        elif validation_errors:
            wf_state = "NeedsReview"
        else:
            wf_state = "Validated"

        cur = conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, branch_id, document_id, supplier_id, po_id, grn_id,
                invoice_type, supplier_invoice_no, normalized_invoice_no, fiscal_year,
                invoice_date, currency_code, payment_method,
                amount_ht_dzd, amount_tva_dzd, amount_timbre_dzd, amount_ttc_dzd,
                workflow_state, validation_errors_json, payload_sha256,
                created_by_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'IN_INVOICE', ?, ?, ?, ?, 'DZD', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                branch_id,
                document_id,
                supplier_id,
                po_id,
                grn_id,
                supplier_invoice_no,
                norm_ref,
                fiscal_year,
                invoice_date,
                payment_method.upper(),
                format_dzd(ht_dec),
                format_dzd(tva_dec),
                format_dzd(timbre_dec),
                format_dzd(ttc_dec),
                wf_state,
                json.dumps(validation_errors),
                payload_hash,
                user_id,
                now_iso,
            ),
        )
        inv_id = int(cur.lastrowid)

        _insert_invoice_lines(conn, company_id, inv_id, prepared_lines)

        # Phase 33 (P32-F3): a refused attempt never writes the document state.
        if document_id and wf_state != "DuplicateRejected":
            _refresh_document_state(conn, company_id, document_id, wf_state, invoice_id=inv_id)

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="INVOICE_VALIDATED_STAGED",
            target_model="supplier_invoices",
            target_id=str(inv_id),
            after_state={
                "workflow_state": wf_state,
                "validation_errors": validation_errors,
                "payload_sha256": payload_hash,
            },
        )
        if wf_state == "DuplicateRejected":
            # The attempt is recorded as its own refusal, independent of the
            # document it cited and of the invoice it duplicates.
            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="DUPLICATE_INVOICE_ATTEMPT_RECORDED",
                target_model="supplier_invoices",
                target_id=str(inv_id),
                after_state={
                    "company_id": company_id,
                    "document_id": document_id,
                    "attempted_supplier_invoice_no": supplier_invoice_no,
                    "attempted_supplier_id": supplier_id,
                    "attempted_amount_ttc_dzd": format_dzd(ttc_dec),
                    "validation_errors": validation_errors,
                    "document_state_touched": False,
                    "refusal_reason": "EXACT_DUPLICATE_INVOICE_REF",
                },
            )
        return {
            "invoice_id": inv_id,
            "workflow_state": wf_state,
            "validation_errors": validation_errors,
            "payload_sha256": payload_hash,
        }


def revalidate_supplier_invoice(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    invoice_id: int,
    *,
    lines: Optional[List[Dict[str, Any]]] = None,
    supplier_invoice_no: Optional[str] = None,
    invoice_date: Optional[str] = None,
    payment_method: Optional[str] = None,
    amount_ht_dzd: Any = None,
    amount_tva_dzd: Any = None,
    amount_timbre_dzd: Any = None,
    amount_ttc_dzd: Any = None,
    price_tolerance_dzd: str = "0.00",
) -> Dict[str, Any]:
    """Correct and re-validate a rejected-or-unvalidated draft in place (P32-F2).

    The lifecycle this establishes, on the *same* invoice identity:

    ``NeedsReview | DuplicateRejected`` --(correct + re-validate)-->
    ``Validated | NeedsReview | DuplicateRejected``

    Every prerequisite is re-run from the corrected payload, exactly as
    creation runs it: Algerian identifier checks, tenant-scoped duplicate
    detection (ignoring this invoice itself, so a correction is never mistaken
    for a re-creation), per-line arithmetic/TVA/discount bounds/3-way match,
    header HT/TVA reconciliation, the timbre rule, the TTC total, and the
    document's link state. The payload hash is recomputed, so a previously
    rejected validation result can never authorize posting: an approval can
    only ever be granted against the hash that was validated *and* approved.

    Illegal transitions are refused rather than ignored. ``PendingApproval``,
    ``Approved`` and ``Posted`` are not correctable here — an approved or posted
    record is never rewritten (the reversal credit note is the accounting
    remedy); ``Rejected`` is terminal. Each attempt appends its own audit event
    carrying the previous validation result, so the history of failed validation
    attempts is preserved rather than overwritten.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("clerk", "accountant", "admin"))
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Invoice not found in target company.")

        previous_state = inv["workflow_state"]
        if previous_state not in REVISABLE_INVOICE_STATES:
            raise FinancialIntegrityError(
                f"INVOICE_REVALIDATION_STATE_REFUSED: invoice {invoice_id} is in state "
                f"'{previous_state}'; only {REVISABLE_INVOICE_STATES} may be corrected and "
                "re-validated. An approved or posted record is never rewritten — use a reversal "
                "credit note — and a rejected invoice is terminal."
            )
        if inv["invoice_type"] != "IN_INVOICE":
            raise FinancialIntegrityError(
                "INVOICE_REVALIDATION_NOT_APPLICABLE: a credit note follows the reversal "
                "workflow, not invoice re-validation."
            )

        sup = conn.execute(
            "SELECT * FROM partners WHERE id = ? AND company_id = ?",
            (inv["supplier_id"], company_id),
        ).fetchone()
        if not sup:
            raise TenantIsolationError("Supplier does not belong to target company.")

        new_no = supplier_invoice_no if supplier_invoice_no is not None else inv["supplier_invoice_no"]
        new_date = invoice_date if invoice_date is not None else inv["invoice_date"]
        new_method = (
            payment_method if payment_method is not None else inv["payment_method"]
        ).upper()
        if new_method not in ("CASH", "BANK_TRANSFER", "CHEQUE"):
            raise ValueError(f"UNSUPPORTED_PAYMENT_METHOD:{new_method}")
        norm_ref = normalize_invoice_ref(new_no)
        fiscal_year = int(new_date[:4])

        if lines is None:
            # No new line set supplied: re-validate the stored lines as they are.
            stored_lines = conn.execute(
                "SELECT * FROM supplier_invoice_lines WHERE company_id = ? AND invoice_id = ? ORDER BY id",
                (company_id, invoice_id),
            ).fetchall()
            lines = [
                {
                    "po_line_id": row["po_line_id"],
                    "product_id": row["product_id"],
                    "description": row["description"],
                    "qty": row["qty"],
                    "unit_price_dzd": row["unit_price_dzd"],
                    "discount_pct": row["discount_pct"],
                    "tax_rate": row["tax_rate"],
                    "line_ht_dzd": row["line_ht_dzd"],
                }
                for row in stored_lines
            ]
        if not lines:
            raise FinancialIntegrityError(
                "INVOICE_HAS_NO_LINES: an invoice without lines cannot be validated."
            )

        ht_dec = round_dzd(amount_ht_dzd if amount_ht_dzd is not None else inv["amount_ht_dzd"])
        tva_dec = round_dzd(amount_tva_dzd if amount_tva_dzd is not None else inv["amount_tva_dzd"])
        timbre_dec = round_dzd(
            amount_timbre_dzd if amount_timbre_dzd is not None else inv["amount_timbre_dzd"]
        )
        ttc_dec = round_dzd(amount_ttc_dzd if amount_ttc_dzd is not None else inv["amount_ttc_dzd"])

        payload_hash = compute_invoice_payload_hash(
            supplier_id=inv["supplier_id"],
            supplier_invoice_no=new_no,
            invoice_date=new_date,
            payment_method=new_method,
            amount_ht_dzd=format_dzd(ht_dec),
            amount_tva_dzd=format_dzd(tva_dec),
            amount_timbre_dzd=format_dzd(timbre_dec),
            amount_ttc_dzd=format_dzd(ttc_dec),
            lines=lines,
        )

        validation_errors: List[str] = []
        if sup["country_code"] == "DZ" and sup["tax_regime"] == "REEL":
            if not sup["nif"]:
                validation_errors.append("SUPPLIER_MISSING_NIF")
            else:
                validation_errors.extend(
                    validate_algerian_identifiers(
                        nif=sup["nif"], nis=sup["nis"], ai=sup["ai"], rc=sup["rc"]
                    )
                )

        duplicate_errors, is_exact_dup = _duplicate_invoice_errors(
            conn,
            company_id=company_id,
            supplier_id=inv["supplier_id"],
            fiscal_year=fiscal_year,
            norm_ref=norm_ref,
            ttc_dec=ttc_dec,
            exclude_invoice_id=invoice_id,
        )
        validation_errors.extend(duplicate_errors)

        line_errors, prepared_lines, sum_line_ht, sum_line_tva = _validate_invoice_lines(
            conn, company_id=company_id, lines=lines, price_tolerance_dzd=price_tolerance_dzd
        )
        validation_errors.extend(line_errors)
        validation_errors.extend(
            _validate_invoice_totals(
                ht_dec=ht_dec,
                tva_dec=tva_dec,
                timbre_dec=timbre_dec,
                ttc_dec=ttc_dec,
                payment_method=new_method,
                sum_line_ht=sum_line_ht,
                sum_line_tva=sum_line_tva,
            )
        )

        if inv["document_id"]:
            _assert_document_linkable(conn, company_id, inv["document_id"])

        if is_exact_dup:
            wf_state = "DuplicateRejected"
        elif validation_errors:
            wf_state = "NeedsReview"
        else:
            wf_state = "Validated"

        # The line set is replaced, not patched: a correction is a new validated
        # payload, and any stale line-level state must not survive it. The state
        # guard above guarantees the invoice never posted, so no journal entry
        # can be affected.
        conn.execute(
            "DELETE FROM supplier_invoice_lines WHERE company_id = ? AND invoice_id = ?",
            (company_id, invoice_id),
        )
        _insert_invoice_lines(conn, company_id, invoice_id, prepared_lines)

        conn.execute(
            """
            UPDATE supplier_invoices
            SET supplier_invoice_no = ?, normalized_invoice_no = ?, fiscal_year = ?,
                invoice_date = ?, payment_method = ?,
                amount_ht_dzd = ?, amount_tva_dzd = ?, amount_timbre_dzd = ?, amount_ttc_dzd = ?,
                workflow_state = ?, validation_errors_json = ?, payload_sha256 = ?,
                approved_payload_sha256 = NULL
            WHERE id = ? AND company_id = ?
            """,
            (
                new_no,
                norm_ref,
                fiscal_year,
                new_date,
                new_method,
                format_dzd(ht_dec),
                format_dzd(tva_dec),
                format_dzd(timbre_dec),
                format_dzd(ttc_dec),
                wf_state,
                json.dumps(validation_errors),
                payload_hash,
                invoice_id,
                company_id,
            ),
        )

        invalidated = _invalidate_approval_requests(
            conn,
            company_id,
            invoice_id,
            reason="PAYLOAD_REVALIDATED",
            actor_user_id=user_id,
            now_iso=now_iso,
        )
        if inv["document_id"]:
            _refresh_document_state(
                conn, company_id, inv["document_id"], wf_state, invoice_id=invoice_id
            )

        previous_errors = json.loads(inv["validation_errors_json"] or "[]")
        revision_count = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM audit_events
                WHERE company_id = ? AND action_type = 'INVOICE_REVALIDATED' AND target_id = ?
                """,
                (company_id, str(invoice_id)),
            ).fetchone()["n"]
        ) + 1

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="INVOICE_REVISED",
            target_model="supplier_invoices",
            target_id=str(invoice_id),
            before_state={
                "supplier_invoice_no": inv["supplier_invoice_no"],
                "invoice_date": inv["invoice_date"],
                "payment_method": inv["payment_method"],
                "amount_ht_dzd": inv["amount_ht_dzd"],
                "amount_tva_dzd": inv["amount_tva_dzd"],
                "amount_timbre_dzd": inv["amount_timbre_dzd"],
                "amount_ttc_dzd": inv["amount_ttc_dzd"],
                "payload_sha256": inv["payload_sha256"],
                "workflow_state": previous_state,
            },
            after_state={
                "supplier_invoice_no": new_no,
                "invoice_date": new_date,
                "payment_method": new_method,
                "amount_ht_dzd": format_dzd(ht_dec),
                "amount_tva_dzd": format_dzd(tva_dec),
                "amount_timbre_dzd": format_dzd(timbre_dec),
                "amount_ttc_dzd": format_dzd(ttc_dec),
                "payload_sha256": payload_hash,
                "line_count": len(prepared_lines),
            },
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="INVOICE_REVALIDATED",
            target_model="supplier_invoices",
            target_id=str(invoice_id),
            before_state={
                "workflow_state": previous_state,
                "validation_errors": previous_errors,
                "payload_sha256": inv["payload_sha256"],
                "validation_attempt": revision_count - 1,
            },
            after_state={
                "workflow_state": wf_state,
                "validation_errors": validation_errors,
                "payload_sha256": payload_hash,
                "validation_attempt": revision_count,
                "invalidated_approval_request_ids": invalidated,
                "document_id": inv["document_id"],
            },
        )
        return {
            "invoice_id": invoice_id,
            "previous_state": previous_state,
            "workflow_state": wf_state,
            "validation_errors": validation_errors,
            "payload_sha256": payload_hash,
            "revised": payload_hash != inv["payload_sha256"],
            "validation_attempt": revision_count,
            "invalidated_approval_request_ids": invalidated,
        }


def submit_invoice_for_approval(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    invoice_id: int,
) -> Dict[str, Any]:
    """Transition a Validated invoice to PendingApproval.

    Phase 29: refused while the linked document's field review is not
    satisfied (see :func:`_document_review_gate`). Phase 33 (P32-F4): the
    request is bound to the exact payload hash *and* to a digest of the review
    evidence it was raised against, so a later modification cannot be approved
    by an earlier decision. The refusal is audited and committed before the
    error is raised, hence the denial-variable structure.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    denial: Optional[Exception] = None
    result: Optional[Dict[str, Any]] = None
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("clerk", "accountant", "admin"))
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Invoice not found in target company.")
        if inv["workflow_state"] != "Validated":
            raise FinancialIntegrityError(
                f"Cannot request approval for invoice in state '{inv['workflow_state']}'; must be 'Validated'."
            )

        denial = _document_review_gate(
            conn, db, company_id, inv, user_id, action="SUBMIT"
        )
        if denial is None:
            fingerprint = _source_review_fingerprint(conn, company_id, inv["document_id"])
            # A previous request for an older payload (or older evidence) is
            # superseded, never left pending alongside the new one.
            superseded = _invalidate_approval_requests(
                conn,
                company_id,
                invoice_id,
                reason="SUPERSEDED_BY_NEW_SUBMISSION",
                actor_user_id=user_id,
                now_iso=now_iso,
                include_approved=False,
            )
            cur = conn.execute(
                """
                INSERT INTO approval_requests (
                    company_id, target_model, target_id, payload_sha256,
                    source_review_fingerprint, requested_by_id, requested_at, decision
                ) VALUES (?, 'supplier_invoices', ?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    company_id,
                    invoice_id,
                    inv["payload_sha256"],
                    fingerprint,
                    inv["created_by_id"],
                    now_iso,
                ),
            )
            req_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE supplier_invoices SET workflow_state = 'PendingApproval' WHERE id = ?",
                (invoice_id,),
            )
            _refresh_document_state(
                conn, company_id, inv["document_id"], "PendingApproval", invoice_id=invoice_id
            )

            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=user_id,
                action_type="APPROVAL_REQUESTED",
                target_model="supplier_invoices",
                target_id=str(invoice_id),
                after_state={
                    "approval_request_id": req_id,
                    "workflow_state": "PendingApproval",
                    "payload_sha256": inv["payload_sha256"],
                    "source_review_fingerprint": fingerprint,
                    "superseded_request_ids": superseded,
                },
            )
            result = {
                "invoice_id": invoice_id,
                "approval_request_id": req_id,
                "workflow_state": "PendingApproval",
                "payload_sha256": inv["payload_sha256"],
                "superseded_request_ids": superseded,
            }
    if denial is not None:
        raise denial
    assert result is not None
    return result


def decide_invoice_approval(
    db: ERPDatabase,
    company_id: int,
    approver_user_id: int,
    invoice_id: int,
    approve: bool,
    reason: str = "",
) -> Dict[str, Any]:
    """Approve or reject a PendingApproval invoice with strict Segregation of Duties enforcement.

    Phase 29: an *approval* is additionally refused while the linked document's
    field review is not satisfied — re-checked here inside the same IMMEDIATE
    transaction, so a document that was satisfied at submit time but disturbed
    since cannot ride an approval through. Rejection (the safe direction) is
    never gated. The refusal is audited and committed before it is raised.

    Phase 33 (P32-F4): the pending request must still describe *this* invoice
    payload and the same review evidence. A request raised against an older
    payload (an invoice modified after submission) or older evidence is
    invalidated and the approval is refused; the invoice returns to
    ``NeedsReview`` so the corrected payload can be re-validated and submitted
    again rather than being stranded. An obsolete request can therefore never
    authorize a modified invoice.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    denial: Optional[Exception] = None
    result: Optional[Dict[str, Any]] = None
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, approver_user_id, ("approver", "accountant", "admin"))
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Invoice not found in target company.")
        if inv["workflow_state"] != "PendingApproval":
            raise FinancialIntegrityError(
                f"Invoice {invoice_id} is in state '{inv['workflow_state']}', not 'PendingApproval'."
            )
        if approve and inv["created_by_id"] == approver_user_id:
            raise AuthorizationError(
                "SEGREGATION_OF_DUTIES_VIOLATION: Invoice creator cannot approve their own invoice."
            )

        if approve:
            denial = _document_review_gate(
                conn, db, company_id, inv, approver_user_id, action="APPROVAL"
            )

        req = conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE company_id = ? AND target_model = 'supplier_invoices' AND target_id = ? AND decision = 'PENDING'
            ORDER BY id DESC LIMIT 1
            """,
            (company_id, invoice_id),
        ).fetchone()
        if not req:
            raise FinancialIntegrityError("No pending approval request found.")

        if approve and denial is None:
            stale_reasons: List[str] = []
            if req["payload_sha256"] != inv["payload_sha256"]:
                stale_reasons.append("PAYLOAD_HASH_CHANGED_SINCE_REQUEST")
            bound_fingerprint = (
                req["source_review_fingerprint"] if "source_review_fingerprint" in req.keys() else None
            )
            if bound_fingerprint is not None:
                current_fingerprint = _source_review_fingerprint(conn, company_id, inv["document_id"])
                if current_fingerprint != bound_fingerprint:
                    stale_reasons.append("SOURCE_REVIEW_CHANGED_SINCE_REQUEST")
            if stale_reasons:
                conn.execute(
                    """
                    UPDATE approval_requests
                    SET decision = 'INVALIDATED', decided_by_id = ?, decided_at = ?, reason = ?
                    WHERE id = ?
                    """,
                    (
                        approver_user_id,
                        now_iso,
                        "STALE_APPROVAL_REQUEST:" + ",".join(stale_reasons),
                        req["id"],
                    ),
                )
                # Recovery, not a dead end: the payload can be re-validated and
                # submitted again (P32-F2), so a stale request never strands the
                # invoice in PendingApproval with no legal exit.
                conn.execute(
                    """
                    UPDATE supplier_invoices
                    SET workflow_state = 'NeedsReview', approved_payload_sha256 = NULL
                    WHERE id = ?
                    """,
                    (invoice_id,),
                )
                _refresh_document_state(
                    conn, company_id, inv["document_id"], "NeedsReview", invoice_id=invoice_id
                )
                db.append_audit_event(
                    conn=conn,
                    company_id=company_id,
                    actor_user_id=approver_user_id,
                    action_type="APPROVAL_REQUEST_STALE_REFUSED",
                    target_model="supplier_invoices",
                    target_id=str(invoice_id),
                    after_state={
                        "approval_request_id": int(req["id"]),
                        "stale_reasons": stale_reasons,
                        "request_payload_sha256": req["payload_sha256"],
                        "current_payload_sha256": inv["payload_sha256"],
                        "workflow_state": "NeedsReview",
                    },
                )
                denial = FinancialIntegrityError(
                    "APPROVAL_REQUEST_STALE: the approval request was raised against a different "
                    "invoice payload or different review evidence — "
                    + "; ".join(stale_reasons)
                    + ". The request has been invalidated and the invoice returned to NeedsReview; "
                    "re-validate and submit it again."
                )

        if denial is None:
            decision_str = "APPROVED" if approve else "REJECTED"
            new_wf_state = "Approved" if approve else "Rejected"

            conn.execute(
                """
                UPDATE approval_requests
                SET decision = ?, decided_by_id = ?, decided_at = ?, reason = ?
                WHERE id = ?
                """,
                (decision_str, approver_user_id, now_iso, reason, req["id"]),
            )
            conn.execute(
                """
                UPDATE supplier_invoices
                SET workflow_state = ?,
                    approved_payload_sha256 = ?,
                    approved_by_id = ?,
                    approved_at = ?
                WHERE id = ?
                """,
                (
                    new_wf_state,
                    inv["payload_sha256"] if approve else None,
                    approver_user_id if approve else None,
                    now_iso if approve else None,
                    invoice_id,
                ),
            )
            if inv["document_id"]:
                conn.execute(
                    "UPDATE source_documents SET workflow_state = ?, rejection_reason = ? WHERE id = ?",
                    (new_wf_state, None if approve else reason, inv["document_id"]),
                )

            db.append_audit_event(
                conn=conn,
                company_id=company_id,
                actor_user_id=approver_user_id,
                action_type="INVOICE_APPROVED" if approve else "INVOICE_REJECTED",
                target_model="supplier_invoices",
                target_id=str(invoice_id),
                after_state={
                    "workflow_state": new_wf_state,
                    "approved_payload_sha256": inv["payload_sha256"] if approve else None,
                    "reason": reason,
                },
            )
            result = {"invoice_id": invoice_id, "workflow_state": new_wf_state, "decision": decision_str}
    if denial is not None:
        raise denial
    assert result is not None
    return result


def modify_draft_invoice_line(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    invoice_id: int,
    line_id: int,
    new_unit_price_dzd: Any = None,
    *,
    new_qty: Any = None,
    new_discount_pct: Any = None,
) -> Dict[str, Any]:
    """Correct an unposted invoice line and invalidate every approval it voids.

    Quantity, unit price and discount are each independently correctable; the
    line's net, its TVA, the header totals and the timbre are re-derived from
    the corrected values with the same exact-Decimal rules validation uses, and
    the payload digest is recomputed.

    Phase 33 (P32-F4): a financial modification invalidates the approval basis
    *explicitly*. Every pending or approved request for this invoice becomes
    ``INVALIDATED`` (with the reason and the acting user recorded), the approved
    payload digest is cleared, the invoice returns to ``NeedsReview`` and the
    source document follows it — the invoice state and the evidence state can no
    longer disagree, and no orphaned ``PENDING`` request is left behind. A
    fresh approval therefore requires a fresh submission of the corrected
    payload. ``approved_by_id`` / ``approved_at`` are retained as historical
    attribution; the operative digest is cleared.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    if new_unit_price_dzd is None and new_qty is None and new_discount_pct is None:
        raise ValueError(
            "Nothing to modify: supply at least one of new_unit_price_dzd, new_qty, new_discount_pct."
        )
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("clerk", "accountant", "admin"))
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Invoice not found.")
        if inv["workflow_state"] == "Posted":
            raise FinancialIntegrityError("Posted invoice cannot be edited; use a reversal credit note.")

        line = conn.execute(
            "SELECT * FROM supplier_invoice_lines WHERE id = ? AND invoice_id = ? AND company_id = ?",
            (line_id, invoice_id, company_id),
        ).fetchone()
        if not line:
            raise ValueError("Invoice line not found.")

        qty = round_qty(line["qty"] if new_qty is None else new_qty)
        pu = round_qty(line["unit_price_dzd"] if new_unit_price_dzd is None else new_unit_price_dzd)
        disc = to_decimal(line["discount_pct"] if new_discount_pct is None else new_discount_pct)
        rate = to_decimal(line["tax_rate"])
        if qty <= Decimal("0.000000"):
            raise FinancialIntegrityError(f"LINE_QTY_NONPOSITIVE:{qty}")
        if disc < Decimal("0.00") or disc > Decimal("100.00"):
            raise FinancialIntegrityError(f"LINE_DISCOUNT_OUT_OF_RANGE:{disc}")

        # The line's persisted discount is part of the net-amount formula; a
        # price edit must re-derive the line amount from it, not silently drop
        # the discount the line (and the payload hash) carries.
        new_ht = round_dzd(qty * pu * ((Decimal("100.00") - disc) / Decimal("100.00")))
        new_tva = round_dzd(new_ht * (rate / Decimal("100.00")))
        new_ttc = round_dzd(new_ht + new_tva)

        conn.execute(
            """
            UPDATE supplier_invoice_lines
            SET qty = ?, unit_price_dzd = ?, discount_pct = ?,
                line_ht_dzd = ?, line_tva_dzd = ?, line_ttc_dzd = ?
            WHERE id = ? AND company_id = ?
            """,
            (
                f"{qty:.6f}",
                f"{pu:.6f}",
                format_dzd(disc),
                format_dzd(new_ht),
                format_dzd(new_tva),
                format_dzd(new_ttc),
                line_id,
                company_id,
            ),
        )

        all_lines = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM supplier_invoice_lines WHERE company_id = ? AND invoice_id = ? ORDER BY id",
                (company_id, invoice_id),
            ).fetchall()
        ]
        tot_ht = sum((to_decimal(r["line_ht_dzd"]) for r in all_lines), Decimal("0.00"))
        tot_tva = sum((to_decimal(r["line_tva_dzd"]) for r in all_lines), Decimal("0.00"))
        timbre = calculate_droit_de_timbre(tot_ht + tot_tva, payment_method=inv["payment_method"])
        tot_ttc = round_dzd(tot_ht + tot_tva + timbre)

        new_hash = compute_invoice_payload_hash(
            supplier_id=inv["supplier_id"],
            supplier_invoice_no=inv["supplier_invoice_no"],
            invoice_date=inv["invoice_date"],
            payment_method=inv["payment_method"],
            amount_ht_dzd=format_dzd(tot_ht),
            amount_tva_dzd=format_dzd(tot_tva),
            amount_timbre_dzd=format_dzd(timbre),
            amount_ttc_dzd=format_dzd(tot_ttc),
            lines=all_lines,
        )

        invalidated = _invalidate_approval_requests(
            conn,
            company_id,
            invoice_id,
            reason="PAYLOAD_MODIFIED_AFTER_SUBMISSION",
            actor_user_id=user_id,
            now_iso=now_iso,
        )
        conn.execute(
            """
            UPDATE supplier_invoices
            SET amount_ht_dzd = ?, amount_tva_dzd = ?, amount_timbre_dzd = ?, amount_ttc_dzd = ?,
                payload_sha256 = ?, approved_payload_sha256 = NULL, workflow_state = 'NeedsReview'
            WHERE id = ? AND company_id = ?
            """,
            (
                format_dzd(tot_ht),
                format_dzd(tot_tva),
                format_dzd(timbre),
                format_dzd(tot_ttc),
                new_hash,
                invoice_id,
                company_id,
            ),
        )
        document_synced = _refresh_document_state(
            conn, company_id, inv["document_id"], "NeedsReview", invoice_id=invoice_id
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="INVOICE_DRAFT_MODIFIED_APPROVAL_INVALIDATED",
            target_model="supplier_invoices",
            target_id=str(invoice_id),
            before_state={
                "payload_sha256": inv["payload_sha256"],
                "workflow_state": inv["workflow_state"],
                "approved_payload_sha256": inv["approved_payload_sha256"],
                "line": {
                    "id": int(line["id"]),
                    "qty": line["qty"],
                    "unit_price_dzd": line["unit_price_dzd"],
                    "discount_pct": line["discount_pct"],
                    "line_ht_dzd": line["line_ht_dzd"],
                },
            },
            after_state={
                "payload_sha256": new_hash,
                "workflow_state": "NeedsReview",
                "approved_payload_sha256": None,
                "line": {
                    "id": int(line["id"]),
                    "qty": f"{qty:.6f}",
                    "unit_price_dzd": f"{pu:.6f}",
                    "discount_pct": format_dzd(disc),
                    "line_ht_dzd": format_dzd(new_ht),
                },
                "invalidated_approval_request_ids": invalidated,
                "source_document_state_synced": document_synced,
            },
        )
        return {
            "invoice_id": invoice_id,
            "payload_sha256": new_hash,
            "workflow_state": "NeedsReview",
            "approval_invalidated": inv["workflow_state"] in ("PendingApproval", "Approved"),
            "invalidated_approval_request_ids": invalidated,
            "source_document_state_synced": document_synced,
        }


def post_supplier_invoice(
    db: ERPDatabase,
    company_id: int,
    accountant_user_id: int,
    invoice_id: int,
    idempotency_key: str,
) -> Dict[str, Any]:
    """Atomically post an Approved supplier invoice to the General Ledger with idempotency & zero stock movement."""
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("idempotency_key is mandatory for financial posting.")

    now_iso = datetime.now(timezone.utc).isoformat()
    req_hash = hashlib.sha256(f"{company_id}:{invoice_id}:POST".encode("utf-8")).hexdigest()
    try:
        return _post_supplier_invoice_in_transaction(
            db, company_id, accountant_user_id, invoice_id, idempotency_key, req_hash, now_iso
        )
    except _RefusedAttempt as refusal:
        _refuse_posting_and_recover(db, company_id, accountant_user_id, invoice_id, refusal, now_iso)
        raise refusal from None


def _refuse_posting_and_recover(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    invoice_id: int,
    refusal: _RefusedAttempt,
    now_iso: str,
) -> None:
    """Record a posting refusal durably and leave the invoice repairable.

    A review that turns invalid between approval and posting must not strand the
    invoice: once it is ``Approved`` the source document refuses field decisions
    (``FIELD_REVIEW_CLOSED``), so refusing the posting alone would leave the
    invoice permanently unpostable with nobody able to resolve the evidence.
    The refused attempt is therefore followed, in one transaction, by the same
    recoverable transition Phase 33 defined for a stale approval: the approval
    basis is invalidated with a reason and an actor, the approved digest is
    cleared, and the invoice and its document both return to ``NeedsReview``.
    Nothing is posted and the ledger is untouched; the repaired invoice has to
    be re-validated, re-submitted and re-approved by the normal workflow.
    """
    with db.transaction() as conn:
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()
        if inv is not None and inv["workflow_state"] == "Approved":
            _invalidate_approval_requests(
                conn,
                company_id,
                invoice_id,
                reason="SOURCE_REVIEW_INVALID_AT_POSTING",
                actor_user_id=user_id,
                now_iso=now_iso,
            )
            conn.execute(
                """
                UPDATE supplier_invoices
                SET workflow_state = 'NeedsReview', approved_payload_sha256 = NULL
                WHERE id = ? AND company_id = ?
                """,
                (invoice_id, company_id),
            )
            _refresh_document_state(
                conn, company_id, inv["document_id"], "NeedsReview", invoice_id=invoice_id
            )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type=refusal.audit_action_type,
            target_model=refusal.audit_target_model,
            target_id=refusal.audit_target_id,
            after_state={**refusal.audit_after_state, "invoice_returned_to": "NeedsReview"},
        )


def _post_supplier_invoice_in_transaction(
    db: ERPDatabase,
    company_id: int,
    accountant_user_id: int,
    invoice_id: int,
    idempotency_key: str,
    req_hash: str,
    now_iso: str,
) -> Dict[str, Any]:
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, accountant_user_id, ("accountant", "admin"))

        idem = conn.execute(
            """
            SELECT * FROM idempotency_records
            WHERE company_id = ? AND idempotency_key = ? AND operation = 'POST_INVOICE'
            """,
            (company_id, idempotency_key),
        ).fetchone()
        if idem:
            if idem["request_sha256"] != req_hash:
                raise FinancialIntegrityError("IDEMPOTENCY_KEY_PAYLOAD_MISMATCH")
            cached = json.loads(idem["response_json"])
            cached["idempotent_replay"] = True
            return cached

        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Invoice not found in target company.")

        if inv["workflow_state"] == "Posted":
            raise FinancialIntegrityError(f"Invoice {invoice_id} is already Posted.")
        if inv["workflow_state"] != "Approved":
            raise FinancialIntegrityError(
                f"Cannot post invoice {invoice_id} in state '{inv['workflow_state']}'; requires 'Approved' state."
            )
        if not inv["approved_payload_sha256"] or inv["payload_sha256"] != inv["approved_payload_sha256"]:
            raise FinancialIntegrityError(
                "APPROVAL_PAYLOAD_HASH_MISMATCH: Invoice was modified after approval; re-validation and approval required."
            )

        # Phase 34 defence in depth: the review gate is re-run at posting, in the
        # same IMMEDIATE transaction that writes the journal entry. An extraction
        # row that appeared (or a decision that went stale) between approval and
        # posting cannot therefore reach the ledger — posting enforces the same
        # fail-closed rule as submission and approval, not a weaker one. The
        # denial is raised *after* the transaction commits its audit event, so
        # the refusal is durable evidence (commit-then-refuse).
        post_gate_denial = _document_review_gate(
            conn, db, company_id, inv, accountant_user_id, action="POSTING", audit=False
        )
        if post_gate_denial is not None:
            # Leave the transaction by rolling it back, then record the refusal
            # in its own transaction: nothing of the posting attempt survives,
            # but the refusal does.
            raise _RefusedAttempt(
                str(post_gate_denial),
                action_type=post_gate_denial.audit_action_type,
                target_model="supplier_invoices",
                target_id=str(invoice_id),
                after_state=post_gate_denial.audit_after_state,
            )

        lines = conn.execute("SELECT * FROM supplier_invoice_lines WHERE invoice_id = ?", (invoice_id,)).fetchall()
        je_lines: List[Dict[str, Any]] = []

        # Re-verify 3-way match at posting time under transaction lock and update qty_invoiced
        for line in lines:
            qty = round_qty(line["qty"])
            line_ht = round_dzd(line["line_ht_dzd"])
            debit_acct = "600"
            if line["product_id"]:
                prod = conn.execute("SELECT * FROM products WHERE id = ?", (line["product_id"],)).fetchone()
                if prod and prod["is_storable"]:
                    debit_acct = prod["clearing_account_code"]  # 380 Achats stockés clearing account
                elif prod:
                    debit_acct = prod["expense_account_code"]

            if line["po_line_id"]:
                po_line = conn.execute(
                    "SELECT * FROM purchase_order_lines WHERE id = ? AND company_id = ?",
                    (line["po_line_id"], company_id),
                ).fetchone()
                if not po_line:
                    raise FinancialIntegrityError("Linked PO line missing.")
                new_invoiced = round_qty(to_decimal(po_line["qty_invoiced"]) + qty)
                if new_invoiced > round_qty(po_line["qty_received"]):
                    raise FinancialIntegrityError(
                        f"OVER_BILLING_NOT_RECEIVED: cumulative invoiced {new_invoiced:.6f} > received {po_line['qty_received']}"
                    )
                conn.execute(
                    "UPDATE purchase_order_lines SET qty_invoiced = ? WHERE id = ?",
                    (f"{new_invoiced:.6f}", po_line["id"]),
                )

            je_lines.append(
                {
                    "account_code": debit_acct,
                    "label": f"Bill {inv['supplier_invoice_no']} - {line['description']}",
                    "debit_dzd": format_dzd(line_ht),
                    "credit_dzd": "0.00",
                    "g50_tag": "E3B91_base",
                }
            )

        tva_dec = round_dzd(inv["amount_tva_dzd"])
        if tva_dec > Decimal("0.00"):
            je_lines.append(
                {
                    "account_code": "4456",
                    "label": f"TVA récupérable - {inv['supplier_invoice_no']}",
                    "debit_dzd": format_dzd(tva_dec),
                    "credit_dzd": "0.00",
                    "g50_tag": "E3B91_balance",
                }
            )

        timbre_dec = round_dzd(inv["amount_timbre_dzd"])
        if timbre_dec > Decimal("0.00"):
            je_lines.append(
                {
                    "account_code": "642",
                    "label": f"Droit de timbre espèces - {inv['supplier_invoice_no']}",
                    "debit_dzd": format_dzd(timbre_dec),
                    "credit_dzd": "0.00",
                    "g50_tag": "TIMBRE_PURCHASE",
                }
            )

        ttc_dec = round_dzd(inv["amount_ttc_dzd"])
        je_lines.append(
            {
                "account_code": "401",
                "label": f"Fournisseur - {inv['supplier_invoice_no']}",
                "debit_dzd": "0.00",
                "credit_dzd": format_dzd(ttc_dec),
                "g50_tag": None,
            }
        )

        total_debit = sum((to_decimal(j["debit_dzd"]) for j in je_lines), Decimal("0.00"))
        total_credit = sum((to_decimal(j["credit_dzd"]) for j in je_lines), Decimal("0.00"))
        if total_debit != total_credit:
            raise FinancialIntegrityError(f"Unbalanced journal entry: debit={total_debit} != credit={total_credit}")

        entry_no = f"ACH/{inv['fiscal_year']}/{invoice_id:05d}"
        jcur = conn.execute(
            """
            INSERT INTO journal_entries (
                company_id, branch_id, entry_number, journal_code, entry_date,
                state, source_model, source_id, source_document_id,
                total_debit_dzd, total_credit_dzd, posted_by_id, posted_at
            ) VALUES (?, ?, ?, 'ACH', ?, 'POSTED', 'supplier_invoices', ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                inv["branch_id"],
                entry_no,
                inv["invoice_date"],
                str(invoice_id),
                inv["document_id"],
                format_dzd(total_debit),
                format_dzd(total_credit),
                accountant_user_id,
                now_iso,
            ),
        )
        je_id = int(jcur.lastrowid)

        for jl in je_lines:
            conn.execute(
                """
                INSERT INTO journal_entry_lines (
                    company_id, journal_entry_id, account_code, partner_id,
                    label, debit_dzd, credit_dzd, g50_tag, reconciled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    je_id,
                    jl["account_code"],
                    inv["supplier_id"],
                    jl["label"],
                    jl["debit_dzd"],
                    jl["credit_dzd"],
                    jl["g50_tag"],
                    1 if jl["account_code"] == "380" and inv["grn_id"] else 0,
                ),
            )

        # Reconcile matching 380 clearing lines on the receipt's stock journal entry
        if inv["grn_id"]:
            grn = conn.execute("SELECT journal_entry_id FROM goods_receipts WHERE id = ?", (inv["grn_id"],)).fetchone()
            if grn and grn["journal_entry_id"]:
                conn.execute(
                    """
                    UPDATE journal_entry_lines
                    SET reconciled = 1, reconciliation_ref = ?
                    WHERE journal_entry_id = ? AND account_code = '380'
                    """,
                    (f"MATCH-INV-{invoice_id}", grn["journal_entry_id"]),
                )

        conn.execute(
            """
            UPDATE supplier_invoices
            SET workflow_state = 'Posted', posted_by_id = ?, posted_at = ?, journal_entry_id = ?
            WHERE id = ?
            """,
            (accountant_user_id, now_iso, je_id, invoice_id),
        )
        if inv["document_id"]:
            _refresh_document_state(
                conn, company_id, inv["document_id"], "Posted", invoice_id=invoice_id
            )

        result = {
            "invoice_id": invoice_id,
            "workflow_state": "Posted",
            "journal_entry_id": je_id,
            "entry_number": entry_no,
            "total_debit_dzd": format_dzd(total_debit),
            "total_credit_dzd": format_dzd(total_credit),
            "idempotent_replay": False,
        }
        conn.execute(
            """
            INSERT INTO idempotency_records (company_id, idempotency_key, operation, request_sha256, status, response_json, created_at)
            VALUES (?, ?, 'POST_INVOICE', ?, 'COMPLETED', ?, ?)
            """,
            (company_id, idempotency_key, req_hash, json.dumps(result), now_iso),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=accountant_user_id,
            action_type="INVOICE_POSTED",
            target_model="supplier_invoices",
            target_id=str(invoice_id),
            after_state=result,
        )
        return result


def reverse_posted_invoice(
    db: ERPDatabase,
    company_id: int,
    accountant_user_id: int,
    original_invoice_id: int,
    reason: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    """Create and post a Credit Note (Facture d'Avoir) reversing a posted invoice while preserving the original untouched."""
    now_iso = datetime.now(timezone.utc).isoformat()
    req_hash = hashlib.sha256(f"{company_id}:{original_invoice_id}:REVERSE:{reason}".encode("utf-8")).hexdigest()

    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, accountant_user_id, ("accountant", "admin"))
        idem = conn.execute(
            "SELECT * FROM idempotency_records WHERE company_id = ? AND idempotency_key = ? AND operation = 'REVERSE_INVOICE'",
            (company_id, idempotency_key),
        ).fetchone()
        if idem:
            cached = json.loads(idem["response_json"])
            cached["idempotent_replay"] = True
            return cached

        orig = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (original_invoice_id, company_id),
        ).fetchone()
        if not orig or orig["workflow_state"] != "Posted":
            raise FinancialIntegrityError("Only a Posted invoice can be reversed.")

        orig_je = conn.execute("SELECT * FROM journal_entries WHERE id = ?", (orig["journal_entry_id"],)).fetchone()
        orig_je_lines = conn.execute(
            "SELECT * FROM journal_entry_lines WHERE journal_entry_id = ?",
            (orig["journal_entry_id"],),
        ).fetchall()

        rev_entry_no = f"RAVOIR/{orig['fiscal_year']}/{original_invoice_id:05d}"
        jcur = conn.execute(
            """
            INSERT INTO journal_entries (
                company_id, branch_id, entry_number, journal_code, entry_date,
                state, source_model, source_id, source_document_id,
                total_debit_dzd, total_credit_dzd, reversal_entry_id,
                posted_by_id, posted_at
            ) VALUES (?, ?, ?, 'ACH', ?, 'POSTED', 'supplier_invoices_reversal', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                orig["branch_id"],
                rev_entry_no,
                now_iso[:10],
                str(original_invoice_id),
                orig["document_id"],
                orig_je["total_credit_dzd"],
                orig_je["total_debit_dzd"],
                orig_je["id"],
                accountant_user_id,
                now_iso,
            ),
        )
        rev_je_id = int(jcur.lastrowid)

        for ol in orig_je_lines:
            conn.execute(
                """
                INSERT INTO journal_entry_lines (
                    company_id, journal_entry_id, account_code, partner_id,
                    label, debit_dzd, credit_dzd, g50_tag, reconciled, reconciliation_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    company_id,
                    rev_je_id,
                    ol["account_code"],
                    ol["partner_id"],
                    f"REVERSAL ({reason}): {ol['label']}",
                    ol["credit_dzd"],
                    ol["debit_dzd"],
                    ol["g50_tag"],
                    f"REV-JE-{orig_je['id']}",
                ),
            )

        # Link original journal entry to its reversal entry (permitted by trigger)
        conn.execute(
            "UPDATE journal_entries SET reversal_entry_id = ? WHERE id = ?",
            (rev_je_id, orig_je["id"]),
        )

        # Create posted credit note record linked to original invoice
        cn_no = f"AVOIR-{orig['supplier_invoice_no']}"
        ic = conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, branch_id, document_id, supplier_id, po_id, grn_id,
                invoice_type, supplier_invoice_no, normalized_invoice_no, fiscal_year,
                invoice_date, currency_code, payment_method,
                amount_ht_dzd, amount_tva_dzd, amount_timbre_dzd, amount_ttc_dzd,
                workflow_state, payload_sha256, approved_payload_sha256,
                created_by_id, approved_by_id, approved_at, posted_by_id, posted_at,
                journal_entry_id, reversal_of_invoice_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'IN_REFUND', ?, ?, ?, ?, 'DZD', ?, ?, ?, ?, ?, 'Posted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                orig["branch_id"],
                orig["document_id"],
                orig["supplier_id"],
                orig["po_id"],
                orig["grn_id"],
                cn_no,
                normalize_invoice_ref(cn_no),
                orig["fiscal_year"],
                now_iso[:10],
                orig["payment_method"],
                orig["amount_ht_dzd"],
                orig["amount_tva_dzd"],
                orig["amount_timbre_dzd"],
                orig["amount_ttc_dzd"],
                orig["payload_sha256"],
                orig["payload_sha256"],
                accountant_user_id,
                accountant_user_id,
                now_iso,
                accountant_user_id,
                now_iso,
                rev_je_id,
                original_invoice_id,
                now_iso,
            ),
        )
        cn_id = int(ic.lastrowid)

        # Decrement po_line.qty_invoiced so a replacement bill can be matched
        orig_lines = conn.execute(
            "SELECT * FROM supplier_invoice_lines WHERE invoice_id = ?",
            (original_invoice_id,),
        ).fetchall()
        for line in orig_lines:
            if line["po_line_id"]:
                pol = conn.execute("SELECT * FROM purchase_order_lines WHERE id = ?", (line["po_line_id"],)).fetchone()
                if pol:
                    updated_inv_qty = max(Decimal("0.000000"), round_qty(to_decimal(pol["qty_invoiced"]) - to_decimal(line["qty"])))
                    conn.execute(
                        "UPDATE purchase_order_lines SET qty_invoiced = ? WHERE id = ?",
                        (f"{updated_inv_qty:.6f}", pol["id"]),
                    )

        result = {
            "original_invoice_id": original_invoice_id,
            "credit_note_invoice_id": cn_id,
            "reversal_journal_entry_id": rev_je_id,
            "reversal_entry_number": rev_entry_no,
            "reason": reason,
            "idempotent_replay": False,
        }
        conn.execute(
            """
            INSERT INTO idempotency_records (company_id, idempotency_key, operation, request_sha256, status, response_json, created_at)
            VALUES (?, ?, 'REVERSE_INVOICE', ?, 'COMPLETED', ?, ?)
            """,
            (company_id, idempotency_key, req_hash, json.dumps(result), now_iso),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=accountant_user_id,
            action_type="INVOICE_REVERSED_BY_CREDIT_NOTE",
            target_model="supplier_invoices",
            target_id=str(original_invoice_id),
            after_state=result,
        )
        return result


def create_reversal_request(
    db: ERPDatabase,
    company_id: int,
    accountant_user_id: int,
    original_invoice_id: int,
    reason: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    """Create a Credit Note (Facture d'Avoir) reversal request in NeedsReview state.

    The reversal must go through approval workflow before posting.

    Phase 33 (P32-F5): reversing an invoice twice would double-credit the
    payable, and the only thing preventing it used to be the
    ``UNIQUE(company_id, entry_number)`` constraint — which leaked as an
    unhandled ``sqlite3.IntegrityError`` (HTTP 500). The conflict is now a
    domain refusal: an existing live credit note for the invoice is detected
    *before* any insert, the refusal is audited with its actor and reason, and
    the residual constraint violation (a concurrent attempt that slipped past
    the check) is mapped to the same stable error instead of escaping.
    """
    try:
        return _create_reversal_request(
            db, company_id, accountant_user_id, original_invoice_id, reason, idempotency_key
        )
    except _RefusedAttempt as refusal:
        _record_refused_attempt(db, company_id, accountant_user_id, refusal)
        raise


def _create_reversal_request(
    db: ERPDatabase,
    company_id: int,
    accountant_user_id: int,
    original_invoice_id: int,
    reason: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    """The creating transaction (see the public wrapper for the refusal contract)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    req_hash = hashlib.sha256(f"{company_id}:{original_invoice_id}:REVERSE:{reason}".encode("utf-8")).hexdigest()

    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, accountant_user_id, ("accountant", "admin"))
        idem = conn.execute(
            "SELECT * FROM idempotency_records WHERE company_id = ? AND idempotency_key = ? AND operation = 'REVERSE_INVOICE'",
            (company_id, idempotency_key),
        ).fetchone()
        if idem:
            cached = json.loads(idem["response_json"])
            cached["idempotent_replay"] = True
            return cached

        orig = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
            (original_invoice_id, company_id),
        ).fetchone()
        if not orig or orig["workflow_state"] != "Posted":
            raise FinancialIntegrityError("Only a Posted invoice can be reversed.")

        # P32-F5: refuse a second reversal of the same invoice before touching
        # the ledger. A rejected/discarded credit note does not block a retry.
        existing_reversal = conn.execute(
            """
            SELECT id, workflow_state, supplier_invoice_no
            FROM supplier_invoices
            WHERE company_id = ? AND invoice_type = 'IN_REFUND' AND reversal_of_invoice_id = ?
              AND workflow_state NOT IN ('Rejected', 'DuplicateRejected')
            ORDER BY id DESC
            LIMIT 1
            """,
            (company_id, original_invoice_id),
        ).fetchone()
        if existing_reversal is not None:
            raise _RefusedAttempt(
                f"INVOICE_ALREADY_REVERSED: invoice {original_invoice_id} already has credit note "
                f"{existing_reversal['id']} ('{existing_reversal['supplier_invoice_no']}') in state "
                f"'{existing_reversal['workflow_state']}'. A second reversal would credit the "
                "payable twice.",
                action_type="REVERSAL_ATTEMPT_REFUSED_DUPLICATE",
                target_model="supplier_invoices",
                target_id=str(original_invoice_id),
                after_state={
                    "company_id": company_id,
                    "original_invoice_id": original_invoice_id,
                    "existing_credit_note_id": int(existing_reversal["id"]),
                    "existing_credit_note_state": existing_reversal["workflow_state"],
                    "existing_credit_note_number": existing_reversal["supplier_invoice_no"],
                    "attempted_reason": reason,
                    "refusal_reason": "INVOICE_ALREADY_REVERSED",
                },
            )

        orig_je = conn.execute("SELECT * FROM journal_entries WHERE id = ?", (orig["journal_entry_id"],)).fetchone()
        orig_je_lines = conn.execute(
            "SELECT * FROM journal_entry_lines WHERE journal_entry_id = ?",
            (orig["journal_entry_id"],),
        ).fetchall()

        rev_entry_no = f"RAVOIR/{orig['fiscal_year']}/{original_invoice_id:05d}"
        # Create journal entry in DRAFT state (will be POSTED after approval)
        try:
            jcur = conn.execute(
                """
                INSERT INTO journal_entries (
                    company_id, branch_id, entry_number, journal_code, entry_date,
                    state, source_model, source_id, source_document_id,
                    total_debit_dzd, total_credit_dzd, reversal_entry_id,
                    posted_by_id, posted_at
                ) VALUES (?, ?, ?, 'ACH', ?, 'DRAFT', 'supplier_invoices_reversal', ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    company_id,
                    orig["branch_id"],
                    rev_entry_no,
                    now_iso[:10],
                    str(original_invoice_id),
                    orig["document_id"],
                    orig_je["total_credit_dzd"],
                    orig_je["total_debit_dzd"],
                    orig_je["id"],
                ),
            )
        except sqlite3.IntegrityError as exc:
            # A concurrent attempt won the race between the check above and this
            # insert. Same domain refusal, never a leaked driver error.
            raise _RefusedAttempt(
                f"INVOICE_ALREADY_REVERSED: a reversal entry ('{rev_entry_no}') for invoice "
                f"{original_invoice_id} was created concurrently.",
                action_type="REVERSAL_ATTEMPT_REFUSED_DUPLICATE",
                target_model="supplier_invoices",
                target_id=str(original_invoice_id),
                after_state={
                    "company_id": company_id,
                    "original_invoice_id": original_invoice_id,
                    "attempted_entry_number": rev_entry_no,
                    "attempted_reason": reason,
                    "refusal_reason": "REVERSAL_ENTRY_UNIQUE_CONFLICT",
                    "driver_error": str(exc),
                },
            ) from exc
        rev_je_id = int(jcur.lastrowid)

        for ol in orig_je_lines:
            conn.execute(
                """
                INSERT INTO journal_entry_lines (
                    company_id, journal_entry_id, account_code, partner_id,
                    label, debit_dzd, credit_dzd, g50_tag, reconciled, reconciliation_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    company_id,
                    rev_je_id,
                    ol["account_code"],
                    ol["partner_id"],
                    f"REVERSAL ({reason}): {ol['label']}",
                    ol["credit_dzd"],
                    ol["debit_dzd"],
                    ol["g50_tag"],
                    f"REV-JE-{orig_je['id']}",
                ),
            )

        # Link original journal entry to its reversal entry (permitted by trigger)
        conn.execute(
            "UPDATE journal_entries SET reversal_entry_id = ? WHERE id = ?",
            (rev_je_id, orig_je["id"]),
        )

        # Create credit note record in NeedsReview state
        cn_no = f"AVOIR-{orig['supplier_invoice_no']}"
        ic = conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, branch_id, document_id, supplier_id, po_id, grn_id,
                invoice_type, supplier_invoice_no, normalized_invoice_no, fiscal_year,
                invoice_date, currency_code, payment_method,
                amount_ht_dzd, amount_tva_dzd, amount_timbre_dzd, amount_ttc_dzd,
                workflow_state, payload_sha256, approved_payload_sha256,
                created_by_id, approved_by_id, approved_at, posted_by_id, posted_at,
                journal_entry_id, reversal_of_invoice_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'IN_REFUND', ?, ?, ?, ?, 'DZD', ?, ?, ?, ?, ?, 'NeedsReview', ?, NULL, ?, NULL, NULL, NULL, NULL, ?, ?, ?)
            """,
            (
                company_id,
                orig["branch_id"],
                orig["document_id"],
                orig["supplier_id"],
                orig["po_id"],
                orig["grn_id"],
                cn_no,
                normalize_invoice_ref(cn_no),
                orig["fiscal_year"],
                now_iso[:10],
                orig["payment_method"],
                orig["amount_ht_dzd"],
                orig["amount_tva_dzd"],
                orig["amount_timbre_dzd"],
                orig["amount_ttc_dzd"],
                orig["payload_sha256"],
                accountant_user_id,
                rev_je_id,
                original_invoice_id,
                now_iso,
            ),
        )
        cn_id = int(ic.lastrowid)

        # Copy invoice lines to the credit note
        orig_lines = conn.execute(
            "SELECT * FROM supplier_invoice_lines WHERE invoice_id = ? ORDER BY id",
            (original_invoice_id,),
        ).fetchall()
        for line in orig_lines:
            conn.execute(
                """
                INSERT INTO supplier_invoice_lines (
                    company_id, invoice_id, po_line_id, product_id, description,
                    qty, unit_price_dzd, discount_pct, tax_rate,
                    line_ht_dzd, line_tva_dzd, line_ttc_dzd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    cn_id,
                    line["po_line_id"],
                    line["product_id"],
                    line["description"],
                    line["qty"],
                    line["unit_price_dzd"],
                    line["discount_pct"],
                    line["tax_rate"],
                    line["line_ht_dzd"],
                    line["line_tva_dzd"],
                    line["line_ttc_dzd"],
                ),
            )

        result = {
            "original_invoice_id": original_invoice_id,
            "credit_note_invoice_id": cn_id,
            "reversal_journal_entry_id": rev_je_id,
            "reversal_entry_number": rev_entry_no,
            "reason": reason,
            "workflow_state": "NeedsReview",
            "idempotent_replay": False,
        }
        conn.execute(
            """
            INSERT INTO idempotency_records (company_id, idempotency_key, operation, request_sha256, status, response_json, created_at)
            VALUES (?, ?, 'REVERSE_INVOICE', ?, 'COMPLETED', ?, ?)
            """,
            (company_id, idempotency_key, req_hash, json.dumps(result), now_iso),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=accountant_user_id,
            action_type="REVERSAL_REQUESTED",
            target_model="supplier_invoices",
            target_id=str(cn_id),
            after_state={"reversal_request": result, "original_invoice_id": original_invoice_id},
        )
        return result


def submit_reversal_for_approval(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    reversal_invoice_id: int,
) -> Dict[str, Any]:
    """Transition a Validated reversal to PendingApproval."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, user_id, ("accountant", "admin"))
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ? AND invoice_type = 'IN_REFUND'",
            (reversal_invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Reversal invoice not found in target company.")
        if inv["workflow_state"] != "NeedsReview":
            raise FinancialIntegrityError(
                f"Cannot request approval for reversal in state '{inv['workflow_state']}'; must be 'NeedsReview'."
            )

        _invalidate_approval_requests(
            conn,
            company_id,
            reversal_invoice_id,
            reason="SUPERSEDED_BY_NEW_SUBMISSION",
            actor_user_id=user_id,
            now_iso=now_iso,
            target_model="supplier_invoices_reversal",
            include_approved=False,
        )
        cur = conn.execute(
            """
            INSERT INTO approval_requests (
                company_id, target_model, target_id, payload_sha256,
                source_review_fingerprint, requested_by_id, requested_at, decision
            ) VALUES (?, 'supplier_invoices_reversal', ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                company_id,
                reversal_invoice_id,
                inv["payload_sha256"],
                _source_review_fingerprint(conn, company_id, inv["document_id"]),
                inv["created_by_id"],
                now_iso,
            ),
        )
        req_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE supplier_invoices SET workflow_state = 'PendingApproval' WHERE id = ?",
            (reversal_invoice_id,),
        )
        _refresh_document_state(
            conn, company_id, inv["document_id"], "PendingApproval", invoice_id=reversal_invoice_id
        )

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="REVERSAL_APPROVAL_REQUESTED",
            target_model="supplier_invoices_reversal",
            target_id=str(reversal_invoice_id),
            after_state={"approval_request_id": req_id, "workflow_state": "PendingApproval"},
        )
        return {"reversal_invoice_id": reversal_invoice_id, "approval_request_id": req_id, "workflow_state": "PendingApproval"}


def decide_reversal_approval(
    db: ERPDatabase,
    company_id: int,
    approver_user_id: int,
    reversal_invoice_id: int,
    approve: bool,
    reason: str = "",
) -> Dict[str, Any]:
    """Approve or reject a PendingApproval reversal with strict Segregation of Duties enforcement."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, approver_user_id, ("approver", "accountant", "admin"))
        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ? AND invoice_type = 'IN_REFUND'",
            (reversal_invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Reversal invoice not found in target company.")
        if inv["workflow_state"] != "PendingApproval":
            raise FinancialIntegrityError(
                f"Reversal {reversal_invoice_id} is in state '{inv['workflow_state']}', not 'PendingApproval'."
            )
        if approve and inv["created_by_id"] == approver_user_id:
            raise AuthorizationError(
                "SEGREGATION_OF_DUTIES_VIOLATION: Reversal requester cannot approve their own reversal."
            )

        req = conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE company_id = ? AND target_model = 'supplier_invoices_reversal' AND target_id = ? AND decision = 'PENDING'
            ORDER BY id DESC LIMIT 1
            """,
            (company_id, reversal_invoice_id),
        ).fetchone()
        if not req:
            raise FinancialIntegrityError("No pending approval request found for reversal.")

        decision_str = "APPROVED" if approve else "REJECTED"
        new_wf_state = "Approved" if approve else "Rejected"

        conn.execute(
            """
            UPDATE approval_requests
            SET decision = ?, decided_by_id = ?, decided_at = ?, reason = ?
            WHERE id = ?
            """,
            (decision_str, approver_user_id, now_iso, reason, req["id"]),
        )
        conn.execute(
            """
            UPDATE supplier_invoices
            SET workflow_state = ?,
                approved_payload_sha256 = ?,
                approved_by_id = ?,
                approved_at = ?
            WHERE id = ?
            """,
            (
                new_wf_state,
                inv["payload_sha256"] if approve else None,
                approver_user_id if approve else None,
                now_iso if approve else None,
                reversal_invoice_id,
            ),
        )
        if inv["document_id"]:
            conn.execute(
                "UPDATE source_documents SET workflow_state = ?, rejection_reason = ? WHERE id = ?",
                (new_wf_state, None if approve else reason, inv["document_id"]),
            )

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=approver_user_id,
            action_type="REVERSAL_APPROVED" if approve else "REVERSAL_REJECTED",
            target_model="supplier_invoices_reversal",
            target_id=str(reversal_invoice_id),
            after_state={
                "workflow_state": new_wf_state,
                "approved_payload_sha256": inv["payload_sha256"] if approve else None,
                "reason": reason,
            },
        )
        return {"reversal_invoice_id": reversal_invoice_id, "workflow_state": new_wf_state, "decision": decision_str}


def post_reversal(
    db: ERPDatabase,
    company_id: int,
    accountant_user_id: int,
    reversal_invoice_id: int,
    idempotency_key: str,
) -> Dict[str, Any]:
    """Atomically post an Approved reversal to the General Ledger with idempotency."""
    if not idempotency_key or not idempotency_key.strip():
        raise ValueError("idempotency_key is mandatory for financial posting.")

    now_iso = datetime.now(timezone.utc).isoformat()
    req_hash = hashlib.sha256(f"{company_id}:{reversal_invoice_id}:POST_REVERSAL".encode("utf-8")).hexdigest()

    with db.transaction() as conn:
        _check_user_and_tenant(conn, company_id, accountant_user_id, ("accountant", "admin"))

        idem = conn.execute(
            """
            SELECT * FROM idempotency_records
            WHERE company_id = ? AND idempotency_key = ? AND operation = 'POST_REVERSAL'
            """,
            (company_id, idempotency_key),
        ).fetchone()
        if idem:
            if idem["request_sha256"] != req_hash:
                raise FinancialIntegrityError("IDEMPOTENCY_KEY_PAYLOAD_MISMATCH")
            cached = json.loads(idem["response_json"])
            cached["idempotent_replay"] = True
            return cached

        inv = conn.execute(
            "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ? AND invoice_type = 'IN_REFUND'",
            (reversal_invoice_id, company_id),
        ).fetchone()
        if not inv:
            raise TenantIsolationError("Reversal invoice not found in target company.")

        if inv["workflow_state"] == "Posted":
            raise FinancialIntegrityError(f"Reversal {reversal_invoice_id} is already Posted.")
        if inv["workflow_state"] != "Approved":
            raise FinancialIntegrityError(
                f"Cannot post reversal {reversal_invoice_id} in state '{inv['workflow_state']}'; requires 'Approved' state."
            )
        if not inv["approved_payload_sha256"] or inv["payload_sha256"] != inv["approved_payload_sha256"]:
            raise FinancialIntegrityError(
                "APPROVAL_PAYLOAD_HASH_MISMATCH: Reversal was modified after approval; re-validation and approval required."
            )

        # Get the associated journal entry (should be in DRAFT state)
        rev_je = conn.execute(
            "SELECT * FROM journal_entries WHERE id = ? AND company_id = ?",
            (inv["journal_entry_id"], company_id),
        ).fetchone()
        if not rev_je:
            raise FinancialIntegrityError("Reversal journal entry not found.")
        if rev_je["state"] != "DRAFT":
            raise FinancialIntegrityError(f"Reversal journal entry is in state '{rev_je['state']}', expected 'DRAFT'.")

        # Update journal entry to POSTED state
        conn.execute(
            """
            UPDATE journal_entries
            SET state = 'POSTED', posted_by_id = ?, posted_at = ?
            WHERE id = ?
            """,
            (accountant_user_id, now_iso, rev_je["id"]),
        )

        # Decrement po_line.qty_invoiced so a replacement bill can be matched
        orig_lines = conn.execute(
            "SELECT * FROM supplier_invoice_lines WHERE invoice_id = ?",
            (inv["reversal_of_invoice_id"],),
        ).fetchall()
        for line in orig_lines:
            if line["po_line_id"]:
                pol = conn.execute("SELECT * FROM purchase_order_lines WHERE id = ?", (line["po_line_id"],)).fetchone()
                if pol:
                    updated_inv_qty = max(Decimal("0.000000"), round_qty(to_decimal(pol["qty_invoiced"]) - to_decimal(line["qty"])))
                    conn.execute(
                        "UPDATE purchase_order_lines SET qty_invoiced = ? WHERE id = ?",
                        (f"{updated_inv_qty:.6f}", pol["id"]),
                    )

        # Update reversal invoice to Posted state
        conn.execute(
            """
            UPDATE supplier_invoices
            SET workflow_state = 'Posted', posted_by_id = ?, posted_at = ?
            WHERE id = ?
            """,
            (accountant_user_id, now_iso, reversal_invoice_id),
        )

        result = {
            "reversal_invoice_id": reversal_invoice_id,
            "reversal_journal_entry_id": rev_je["id"],
            "reversal_entry_number": rev_je["entry_number"],
            "original_invoice_id": inv["reversal_of_invoice_id"],
            "reason": None,  # reason is in the approval request
            "workflow_state": "Posted",
            "idempotent_replay": False,
        }
        conn.execute(
            """
            INSERT INTO idempotency_records (company_id, idempotency_key, operation, request_sha256, status, response_json, created_at)
            VALUES (?, ?, 'POST_REVERSAL', ?, 'COMPLETED', ?, ?)
            """,
            (company_id, idempotency_key, req_hash, json.dumps(result), now_iso),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=accountant_user_id,
            action_type="REVERSAL_POSTED",
            target_model="supplier_invoices_reversal",
            target_id=str(reversal_invoice_id),
            after_state=result,
        )
        return result
