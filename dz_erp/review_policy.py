# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 29 — fail-closed field-level review policy for OCR-extracted documents.

The policy answers one question, and answers it from stored data only: *which
parts of an extracted document still need a human decision before the invoice
that cites it may be approved?* It is deliberately conservative in direction:

* An unavailable confidence is never treated as a measured one. ``not_measured``
  (native PDF text) is its own status — never low, never high, and never a
  fabricated score.
* A measured engine score is never proof of correctness. Below the field's
  threshold it is flagged ``low_confidence``; at or above it the field is merely
  ``measured`` — review is required either way. No configuration of this module
  can auto-accept a field: satisfaction always requires an explicit human
  decision record.
* Thresholds are review-effort knobs, not false-accept guarantees. Until the
  calibration run described in ``docs/OCR_EVALUATION_PROTOCOL.md`` §6 exists on
  representative data, they mark "harder scrutiny", not measured risk. The
  protocol's auto-accept path stays unimplemented on purpose.

Aggregation is fail-closed: any unmet required field, any flagged field, any
stale decision, or any arithmetic inconsistency among the extracted totals makes
the whole document unsatisfied — one bad field drags the document, never the
reverse. Patterns (per-field status roll-up, totals recomputed and compared, a
read-only extraction with separate correction records) follow prior art studied
before implementation (docling's low-grade percentile idea, invoice2data's
tax-total cross-check — which this code *inverts*: a mismatch routes to review
instead of warn-and-continue, and ERPNext/Odoo's server-side recomputation).
No code was vendored from any of them (see the phase-29 research record).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .decimal_utils import format_dzd, round_dzd, round_qty, to_decimal
from .intake_ocr import (
    CONFIDENCE_NOT_MEASURED,
    ROUTE_MANUAL_TRANSCRIPTION,
)

# Reviewer decisions. The vocabulary is closed: the database CHECK enforces it.
DECISION_CONFIRMED = "CONFIRMED"  # the extracted (current) value matches the paper
DECISION_CORRECTED = "CORRECTED"  # the paper says otherwise; value replaced (or transcribed)
DECISION_FLAGGED = "FLAGGED"  # cannot be verified (illegible/ambiguous) — blocks approval
DECISION_NOT_PRESENT = "NOT_PRESENT"  # the field legitimately does not exist on the document
FIELD_DECISIONS = (DECISION_CONFIRMED, DECISION_CORRECTED, DECISION_FLAGGED, DECISION_NOT_PRESENT)

DECISIONS_REQUIRING_VALUE = (DECISION_CORRECTED,)
DECISIONS_SATISFYING_WITH_VALUE = (DECISION_CONFIRMED, DECISION_CORRECTED)

LINE_ITEM_FIELD = "line_item_json"

# Per-field statuses.
STATUS_MISSING = "missing"
STATUS_PRESENT = "present"

# Field flags (a field can carry several).
FLAG_UNMEASURED = "confidence_unmeasured"
FLAG_LOW_CONFIDENCE = "low_confidence"
FLAG_CONFLICT = "conflicting_extraction"
FLAG_OVERRIDDEN = "human_override"
FLAG_STALE = "decision_stale"
FLAG_NOT_PRESENT_STALE = "value_appeared_after_not_present"

# Manual-entry provenance for values a human transcribed onto a missing field.
HUMAN_ENTRY_MODEL = "human-manual-entry"
HUMAN_ENTRY_RAW = "[manual entry]"

VALUE_CLASS_IDENTIFIER = "identifier"
VALUE_CLASS_TEXT = "text"
VALUE_CLASS_DATE = "date"
VALUE_CLASS_CURRENCY = "currency"
VALUE_CLASS_AMOUNT = "amount"
VALUE_CLASS_QUANTITY = "quantity"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ReviewPolicyValueError(ValueError):
    """A client-contract or policy violation of a field-review request.

    Subclasses :class:`ValueError` so every existing service-level caller
    keeps working unchanged; the API layer maps this class to 422 while a
    plain ``ValueError`` (document not found in the tenant) stays a 404.
    """


@dataclass(frozen=True)
class FieldRule:
    """Review rule for one header field.

    ``low_confidence_threshold`` only routes harder scrutiny at review time; it
    is never an acceptance test — every field still requires a human decision.
    ``required`` fields cannot be asserted NOT_PRESENT: a critical field that is
    missing must be transcribed (CORRECTED) or the document stays blocked.
    """

    field_name: str
    value_class: str
    required: bool
    low_confidence_threshold: float

    @property
    def allow_not_present(self) -> bool:
        return not self.required


# The configurable core of the policy. Changing a threshold or the required set
# is a code change that goes through review and CI — not a runtime toggle — so
# the rules an approval relied on are always recoverable from git history.
REVIEW_POLICY: Tuple[FieldRule, ...] = (
    FieldRule("invoice_number", VALUE_CLASS_IDENTIFIER, True, 0.80),
    FieldRule("supplier_name", VALUE_CLASS_TEXT, True, 0.80),
    FieldRule("supplier_nif", VALUE_CLASS_IDENTIFIER, True, 0.90),
    FieldRule("supplier_nis", VALUE_CLASS_IDENTIFIER, False, 0.90),
    FieldRule("supplier_rc", VALUE_CLASS_IDENTIFIER, False, 0.90),
    FieldRule("supplier_ai", VALUE_CLASS_IDENTIFIER, False, 0.90),
    FieldRule("invoice_date", VALUE_CLASS_DATE, True, 0.85),
    FieldRule("due_date", VALUE_CLASS_DATE, False, 0.85),
    FieldRule("currency_code", VALUE_CLASS_CURRENCY, True, 0.90),
    FieldRule("amount_ht_dzd", VALUE_CLASS_AMOUNT, True, 0.90),
    FieldRule("amount_tva_dzd", VALUE_CLASS_AMOUNT, True, 0.90),
    FieldRule("amount_timbre_dzd", VALUE_CLASS_AMOUNT, False, 0.90),
    FieldRule("amount_ttc_dzd", VALUE_CLASS_AMOUNT, True, 0.90),
)

# Line items: three elements of every extracted line row are reviewable — the
# quantity, the unit price and the discount. A document with no extracted
# lines imposes no line decisions — the invoice's own lines are typed and
# validated separately.
LINE_QTY_RULE = FieldRule(LINE_ITEM_FIELD, VALUE_CLASS_QUANTITY, True, 0.85)

# The per-line unit price is its own decided value: without an explicit price
# decision, a misread or mistyped price could ride to posting behind a
# correctly reviewed quantity. Decisions bind to the canonical 6-decimal form
# that invoice and purchase-order lines store (``unit_price_dzd`` is
# quantized exactly like a quantity everywhere it is persisted).
LINE_PRICE_FIELD = "line_unit_price"
LINE_PRICE_RULE = FieldRule(LINE_PRICE_FIELD, VALUE_CLASS_AMOUNT, True, 0.85)

# The per-line discount is the third decided line element. The invoice stores
# ``discount_pct`` as a first-class column and pins every line's net amount to
# ``qty × PU × (1 − discount/100)`` — without a decision on it, a mistyped
# discount would be the one line input nobody vouched for. The extraction
# pattern emits no discount, so a decision binds either to a value written by
# an earlier attributed transcription (CORRECTED) or the line is transcribed;
# the canonical form is the 2-decimal string ``format_dzd`` produces, which is
# exactly the form the invoice column stores.
LINE_DISCOUNT_FIELD = "line_discount"
LINE_DISCOUNT_RULE = FieldRule(LINE_DISCOUNT_FIELD, VALUE_CLASS_AMOUNT, True, 0.85)

_RULES_BY_NAME = {rule.field_name: rule for rule in REVIEW_POLICY}

# Identifier shapes mirror the extraction patterns in intake_ocr; a corrected
# value that cannot have been read off an Algerian invoice is refused, not stored.
_NIF_RE = re.compile(r"^\d{15}(?:\d{5})?$")
_NIS_RE = re.compile(r"^\d{15}$")
_AI_RE = re.compile(r"^\d{11}$")
_RC_RE = re.compile(r"^\d{2}/\d{2}-[0-9A-Z]{7,12}$")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def rule_for(field_name: str) -> Optional[FieldRule]:
    if field_name == LINE_ITEM_FIELD:
        return LINE_QTY_RULE
    if field_name == LINE_PRICE_FIELD:
        return LINE_PRICE_RULE
    if field_name == LINE_DISCOUNT_FIELD:
        return LINE_DISCOUNT_RULE
    return _RULES_BY_NAME.get(field_name)


# Bounded-text fallback for transcription of fields outside the critical policy
# (e.g. payment_method): validated for shape, not correlated with any gate.
TEXT_FALLBACK_RULE = FieldRule("*", VALUE_CLASS_TEXT, False, 1.0)


def validate_corrected_value(rule: FieldRule, value: str) -> str:
    """Validate a human-supplied value for the rule's class; return the normalized form.

    Raises ``ValueError`` with a stable machine-readable prefix on any invalid
    input — a value that cannot be what the paper says is never stored.
    """
    if value is None:
        raise ValueError("FIELD_VALUE_REQUIRED")
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError("FIELD_VALUE_REQUIRED")
    vclass = rule.value_class
    if vclass == VALUE_CLASS_AMOUNT:
        try:
            dec = to_decimal(cleaned)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"FIELD_VALUE_INVALID_AMOUNT:{cleaned!r}") from exc
        if dec < Decimal("0.00"):
            raise ValueError(f"FIELD_VALUE_NEGATIVE_AMOUNT:{cleaned!r}")
        if rule.field_name == LINE_PRICE_FIELD:
            # A unit price is stored 6-decimal in invoice and PO lines; the
            # decision binds to that canonical form so a corrected price never
            # disagrees with the payload it was written into.
            return f"{round_qty(dec):.6f}"
        if rule.field_name == LINE_DISCOUNT_FIELD:
            # Business rule: a discount is a percentage in [0, 100] inclusive;
            # a negative value is refused above, anything above 100 is refused
            # here. No value is ever clamped into range: a discount that
            # cannot appear on the paper is not stored as something else.
            if dec > Decimal("100.00"):
                raise ValueError(f"FIELD_VALUE_DISCOUNT_OUT_OF_RANGE:{cleaned!r}")
            return format_dzd(dec)
        return format_dzd(dec)
    if vclass == VALUE_CLASS_QUANTITY:
        try:
            dec = to_decimal(cleaned)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"FIELD_VALUE_INVALID_QUANTITY:{cleaned!r}") from exc
        if dec <= Decimal("0.000000"):
            raise ValueError(f"FIELD_VALUE_NONPOSITIVE_QUANTITY:{cleaned!r}")
        return f"{round_qty(dec):.6f}"
    if vclass == VALUE_CLASS_DATE:
        if not _ISO_DATE_RE.match(cleaned):
            raise ValueError(f"FIELD_VALUE_INVALID_DATE:{cleaned!r}")
        try:
            date.fromisoformat(cleaned)
        except ValueError as exc:
            raise ValueError(f"FIELD_VALUE_INVALID_DATE:{cleaned!r}") from exc
        return cleaned
    if vclass == VALUE_CLASS_CURRENCY:
        if cleaned.upper() != "DZD":
            # One currency is supported end-to-end; pretending otherwise would
            # post DZD amounts under a foreign code.
            raise ValueError(f"FIELD_VALUE_UNSUPPORTED_CURRENCY:{cleaned!r}")
        return cleaned.upper()
    if vclass == VALUE_CLASS_IDENTIFIER:
        if rule.field_name == "supplier_nif" and not _NIF_RE.match(cleaned):
            raise ValueError(f"FIELD_VALUE_INVALID_NIF:{cleaned!r}")
        if rule.field_name == "supplier_nis" and not _NIS_RE.match(cleaned):
            raise ValueError(f"FIELD_VALUE_INVALID_NIS:{cleaned!r}")
        if rule.field_name == "supplier_ai" and not _AI_RE.match(cleaned):
            raise ValueError(f"FIELD_VALUE_INVALID_AI:{cleaned!r}")
        if rule.field_name == "supplier_rc" and not _RC_RE.match(cleaned):
            raise ValueError(f"FIELD_VALUE_INVALID_RC:{cleaned!r}")
        if rule.field_name == "invoice_number":
            if len(cleaned) > 64 or any(ch.isspace() and ch != " " for ch in cleaned):
                raise ValueError(f"FIELD_VALUE_INVALID_INVOICE_NUMBER:{cleaned!r}")
        return cleaned
    # VALUE_CLASS_TEXT and anything unrecognized: bounded free text.
    if len(cleaned) > 256 or "\n" in cleaned:
        raise ValueError(f"FIELD_VALUE_INVALID_TEXT:{cleaned[:64]!r}")
    return cleaned


def _latest_decision(
    decisions_by_key: Dict[Tuple[str, Optional[int]], List[Dict[str, Any]]],
    field_name: str,
    line_index: Optional[int],
) -> Optional[Dict[str, Any]]:
    bucket = decisions_by_key.get((field_name, line_index))
    if not bucket:
        return None
    return max(bucket, key=lambda d: int(d["id"]))


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_amount(value: Any) -> Optional[Decimal]:
    if _is_missing(value):
        return None
    try:
        return round_dzd(to_decimal(str(value)))
    except (ValueError, TypeError):
        return None


def _line_payload(field_row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse one line-item row's JSON; return (payload, error_reason)."""
    raw = field_row.get("normalized_value")
    if _is_missing(raw):
        return None, "line_payload_missing"
    try:
        data = json.loads(str(raw))
    except (ValueError, TypeError):
        return None, "line_payload_unparseable"
    if not isinstance(data, dict):
        return None, "line_payload_not_object"
    return data, None


def _field_evaluation(
    rule: FieldRule,
    field_row: Optional[Dict[str, Any]],
    latest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate one header field (or one reviewable line element when ``rule`` is a line rule)."""
    line_index = None if field_row is None else field_row.get("line_index")
    if rule in (LINE_QTY_RULE, LINE_PRICE_RULE, LINE_DISCOUNT_RULE) and field_row is not None:
        payload, _parse_error = _line_payload(field_row)
        if rule is LINE_QTY_RULE:
            payload_key = "qty"
        elif rule is LINE_DISCOUNT_RULE:
            payload_key = "discount_pct"
        else:
            payload_key = "unit_price_dzd"
        value = None if payload is None else payload.get(payload_key)
        if value in (None, ""):
            value = None
        else:
            # Compare in the canonical form the decision service stores: the
            # 6-decimal quantity/price form, or the 2-decimal discount string
            # the invoice column holds. A discount that was never transcribed
            # is missing — it is never defaulted to zero, which would let an
            # unreviewed discount through.
            try:
                if rule is LINE_DISCOUNT_RULE:
                    value = format_dzd(to_decimal(str(value)))
                else:
                    value = f"{round_qty(to_decimal(str(value))):.6f}"
            except (ValueError, TypeError):
                value = None
        missing = value is None
    else:
        value = None if field_row is None else field_row.get("normalized_value")
        missing = _is_missing(value)

    flags: List[str] = []
    confidence: Optional[float] = None
    confidence_kind: Optional[str] = None
    overridden = False
    if field_row is not None:
        confidence = field_row.get("confidence")
        confidence_kind = field_row.get("confidence_kind")
        overridden = field_row.get("human_override_value") not in (None, "")
        # Unavailable ≠ measured: only a measured score can be low.
        if confidence is None or confidence_kind == CONFIDENCE_NOT_MEASURED:
            flags.append(FLAG_UNMEASURED)
        elif float(confidence) < rule.low_confidence_threshold:
            flags.append(FLAG_LOW_CONFIDENCE)
        conflicts = field_row.get("conflict_values_json")
        if conflicts:
            try:
                if json.loads(conflicts):
                    flags.append(FLAG_CONFLICT)
            except (ValueError, TypeError):
                flags.append(FLAG_CONFLICT)
        if overridden:
            flags.append(FLAG_OVERRIDDEN)

    decision_state: Optional[str] = None
    decided_value: Optional[str] = None
    satisfied = False
    if latest is not None:
        decision_state = latest["decision"]
        decided_value = latest["reviewed_value"]
        if decision_state == DECISION_FLAGGED:
            satisfied = False  # an open flag blocks until superseded
        elif decision_state == DECISION_NOT_PRESENT:
            # Honest only while the field is still absent; a value that appears
            # afterwards (re-extraction, edit) invalidates the assertion.
            satisfied = missing
            if not missing:
                flags.append(FLAG_NOT_PRESENT_STALE)
        else:  # CONFIRMED / CORRECTED — bound to the value it was made on
            satisfied = (not missing) and decided_value is not None and str(decided_value) == str(value)
            if not satisfied:
                flags.append(FLAG_STALE)

    return {
        "field_name": rule.field_name,
        "line_index": line_index,
        "value": None if missing else value,
        "required": rule.required,
        "status": STATUS_MISSING if missing else STATUS_PRESENT,
        "flags": flags,
        "confidence": None if confidence is None else round(float(confidence), 4),
        "confidence_kind": confidence_kind,
        "decision": decision_state,
        "decision_value": decided_value,
        "satisfied": satisfied,
    }


def evaluate_document_review_policy(
    document: Dict[str, Any],
    fields: List[Dict[str, Any]],
    decisions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate the field-level review policy for one document.

    ``document`` is a ``source_documents`` row, ``fields`` the document's
    ``ocr_extracted_fields`` rows and ``decisions`` its ``ocr_field_reviews``
    rows — all as dicts. Everything is recomputed from stored data; nothing is
    cached or trusted from a previous evaluation.
    """
    decisions_by_key: Dict[Tuple[str, Optional[int]], List[Dict[str, Any]]] = {}
    for d in decisions:
        key = (d["field_name"], d.get("line_index"))
        decisions_by_key.setdefault(key, []).append(d)

    rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for f in fields:
        rows_by_name.setdefault(f["field_name"], []).append(f)

    field_evaluations: List[Dict[str, Any]] = []
    unmet_required: List[str] = []
    flagged: List[str] = []

    for rule in REVIEW_POLICY:
        row = rows_by_name.get(rule.field_name)
        # Multiple rows for one header field cannot happen through the intake
        # path (first match wins; later different values become conflicts), but
        # never silently pick one: treat extra rows as a conflict.
        field_row = row[0] if row else None
        latest = _latest_decision(decisions_by_key, rule.field_name, None)
        ev = _field_evaluation(rule, field_row, latest)
        if row and len(row) > 1:
            ev["flags"].append(FLAG_CONFLICT)
        if not ev["satisfied"]:
            if ev["decision"] == DECISION_FLAGGED or FLAG_STALE in ev["flags"] or FLAG_NOT_PRESENT_STALE in ev["flags"]:
                flagged.append(rule.field_name)
            elif rule.required:
                unmet_required.append(rule.field_name)
        field_evaluations.append(ev)

    line_rows = sorted(rows_by_name.get(LINE_ITEM_FIELD, []), key=lambda r: r.get("line_index") or 0)
    line_evaluations: List[Dict[str, Any]] = []
    for line_row in line_rows:
        latest = _latest_decision(decisions_by_key, LINE_ITEM_FIELD, line_row.get("line_index"))
        ev = _field_evaluation(LINE_QTY_RULE, line_row, latest)
        line_evaluations.append(ev)
        if not ev["satisfied"]:
            if ev["decision"] == DECISION_FLAGGED or FLAG_STALE in ev["flags"]:
                flagged.append(f"{LINE_ITEM_FIELD}[{line_row.get('line_index')}]")
            else:
                unmet_required.append(f"{LINE_ITEM_FIELD}[{line_row.get('line_index')}]")

    # Same rows, second reviewable element: the unit price. Its decisions are
    # stored under ``line_unit_price`` keyed by the same line index, so a
    # quantity decision can never stand in for a price decision.
    line_price_evaluations: List[Dict[str, Any]] = []
    for line_row in line_rows:
        latest = _latest_decision(decisions_by_key, LINE_PRICE_FIELD, line_row.get("line_index"))
        ev = _field_evaluation(LINE_PRICE_RULE, line_row, latest)
        line_price_evaluations.append(ev)
        if not ev["satisfied"]:
            if ev["decision"] == DECISION_FLAGGED or FLAG_STALE in ev["flags"]:
                flagged.append(f"{LINE_PRICE_FIELD}[{line_row.get('line_index')}]")
            else:
                unmet_required.append(f"{LINE_PRICE_FIELD}[{line_row.get('line_index')}]")

    # Same rows, third reviewable element: the line discount. Its decisions
    # are stored under ``line_discount`` keyed by the same line index, so a
    # quantity or price decision can never stand in for a discount decision.
    line_discount_evaluations: List[Dict[str, Any]] = []
    for line_row in line_rows:
        latest = _latest_decision(decisions_by_key, LINE_DISCOUNT_FIELD, line_row.get("line_index"))
        ev = _field_evaluation(LINE_DISCOUNT_RULE, line_row, latest)
        line_discount_evaluations.append(ev)
        if not ev["satisfied"]:
            if ev["decision"] == DECISION_FLAGGED or FLAG_STALE in ev["flags"]:
                flagged.append(f"{LINE_DISCOUNT_FIELD}[{line_row.get('line_index')}]")
            else:
                unmet_required.append(f"{LINE_DISCOUNT_FIELD}[{line_row.get('line_index')}]")

    totals = _evaluate_totals(rows_by_name)

    satisfied = (
        not unmet_required
        and not flagged
        and not totals["inconsistent"]
    )

    blocking: List[str] = []
    blocking.extend(f"REQUIRED_FIELD_NOT_DECIDED:{name}" for name in unmet_required)
    blocking.extend(f"FIELD_FLAGGED:{name}" for name in flagged)
    if totals["inconsistent"]:
        blocking.append(f"INCONSISTENT_EXTRACTED_TOTALS:{totals['detail']}")

    return {
        "document_id": document.get("id"),
        "extraction_route": document.get("extraction_route"),
        "route_reason": document.get("route_reason"),
        "requires_manual_transcription": document.get("extraction_route") == ROUTE_MANUAL_TRANSCRIPTION,
        "fields": field_evaluations,
        "lines": line_evaluations,
        "line_prices": line_price_evaluations,
        "line_discounts": line_discount_evaluations,
        "totals": totals,
        "unmet_required": unmet_required,
        "flagged": flagged,
        "satisfied": satisfied,
        "blocking": blocking,
    }


def _evaluate_totals(rows_by_name: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Arithmetic consistency of the *extracted* header totals, from current values.

    Recomputed from components and compared — never trusted from either side
    alone. Timbre absent is treated as 0.00 with an explicit note, because a
    missing timbre line is the one benign explanation for a 2-decimal gap; the
    reviewer can clear the flag by transcribing the timbre (or correcting the
    misread amount). Any inconsistency blocks approval: the paper and the
    extraction disagree about money.
    """
    ht = _parse_amount(_current_value(rows_by_name, "amount_ht_dzd"))
    tva = _parse_amount(_current_value(rows_by_name, "amount_tva_dzd"))
    timbre = _parse_amount(_current_value(rows_by_name, "amount_timbre_dzd"))
    ttc = _parse_amount(_current_value(rows_by_name, "amount_ttc_dzd"))

    missing_parts = [name for name, v in (("ht", ht), ("tva", tva), ("ttc", ttc)) if v is None]
    if missing_parts:
        return {
            "inconsistent": False,
            "evaluable": False,
            "detail": f"TOTALS_NOT_EVALUABLE:missing={'+'.join(missing_parts)}",
            "timbre_present": timbre is not None,
        }

    expected = round_dzd(ht + tva + (timbre if timbre is not None else Decimal("0.00")))
    consistent = expected == ttc
    detail = (
        f"ht={ht:.2f}+tva={tva:.2f}+timbre={timbre if timbre is not None else Decimal('0.00'):.2f}"
        f"={expected:.2f} vs ttc={ttc:.2f}"
    )
    if not consistent and timbre is None:
        detail += " (timbre not extracted; transcribe it or correct the misread amount)"
    return {
        "inconsistent": not consistent,
        "evaluable": True,
        "detail": detail,
        "timbre_present": timbre is not None,
    }


def _current_value(rows_by_name: Dict[str, List[Dict[str, Any]]], field_name: str) -> Any:
    row = rows_by_name.get(field_name)
    return None if not row else row[0].get("normalized_value")
