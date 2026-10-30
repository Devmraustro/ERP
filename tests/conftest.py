"""Shared test helpers and fixtures."""

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def assert_owner_only_permissions():
    """Return a callable that asserts a file has owner-only permissions."""
    def _check(path: Path) -> None:
        assert path.exists(), f"expected file does not exist: {path}"
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600, f"expected mode 0o600, got {oct(mode)} on {path}"
        else:
            assert os.access(path, os.W_OK), f"file is not writable by owner: {path}"
    return _check


@pytest.fixture
def complete_document_field_review():
    """Return a callable that completes the Phase 29 field review of one document.

    Simulates a thorough reviewer: every extracted required header field is
    CONFIRMED at exactly its current stored value, every extracted line
    quantity and line unit price is CONFIRMED, every line discount is decided
    (confirmed at its transcribed value, or transcribed as 0.00 when the
    extraction carries none), and every optional policy field the extraction
    did not find is asserted NOT_PRESENT. The document is then re-evaluated
    and the helper raises unless it is satisfied, so a caller can never
    silently proceed with a half-reviewed document. Tests that need a
    *different* decision flow (corrections, flags, staleness) do not use this
    helper.

    NOTE: CONFIRMED binds the decision to the current value — this helper is
    only valid before any concurrent edit, which holds for its callers.
    """
    from dz_erp.intake_ocr import decide_extracted_field_review
    from dz_erp.review_policy import evaluate_document_review_policy

    def _complete(db, company_id: int, document_id: str, reviewer_user_id: int):
        def evaluate():
            with db.transaction() as conn:
                doc = dict(
                    conn.execute("SELECT * FROM source_documents WHERE id = ?", (document_id,)).fetchone()
                )
            return evaluate_document_review_policy(
                doc,
                db.list_extracted_fields(company_id, document_id),
                db.list_field_reviews(company_id, document_id),
            )

        first = evaluate()
        for fev in first["fields"]:
            if fev["status"] == "missing":
                if fev["required"]:
                    raise AssertionError(
                        f"document {document_id} lacks required field {fev['field_name']}; "
                        "this helper only handles fully-extracted documents "
                        "(transcribe it explicitly in the test instead)"
                    )
                decide_extracted_field_review(
                    db,
                    company_id=company_id,
                    user_id=reviewer_user_id,
                    document_id=document_id,
                    field_name=fev["field_name"],
                    decision="NOT_PRESENT",
                )
            else:
                decide_extracted_field_review(
                    db,
                    company_id=company_id,
                    user_id=reviewer_user_id,
                    document_id=document_id,
                    field_name=fev["field_name"],
                    decision="CONFIRMED",
                    expected_value=fev["value"],
                )
        for lev in first["lines"]:
            decide_extracted_field_review(
                db,
                company_id=company_id,
                user_id=reviewer_user_id,
                document_id=document_id,
                field_name="line_item_json",
                decision="CONFIRMED",
                expected_value=lev["value"],
                line_index=lev["line_index"],
            )
        for pev in first["line_prices"]:
            decide_extracted_field_review(
                db,
                company_id=company_id,
                user_id=reviewer_user_id,
                document_id=document_id,
                field_name="line_unit_price",
                decision="CONFIRMED",
                expected_value=pev["value"],
                line_index=pev["line_index"],
            )
        for dev in first["line_discounts"]:
            # The extraction pattern emits no discount, so a line's discount is
            # missing until a human transcribes it. The synthetic fixtures
            # carry no discount column and their printed net amounts equal
            # qty × PU, so the honest transcription for an absent discount is
            # 0.00 (a discount the paper's own line totals prove absent). An
            # already-transcribed discount is confirmed at its stored value.
            if dev["value"] is not None:
                decide_extracted_field_review(
                    db,
                    company_id=company_id,
                    user_id=reviewer_user_id,
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
                    user_id=reviewer_user_id,
                    document_id=document_id,
                    field_name="line_discount",
                    decision="CORRECTED",
                    value="0.00",
                    line_index=dev["line_index"],
                )
        final = evaluate()
        assert final["satisfied"], f"field review incomplete: {final['blocking']}"
        return final

    return _complete