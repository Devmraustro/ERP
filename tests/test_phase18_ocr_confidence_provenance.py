# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 18 — Confidence provenance for extracted invoice fields.

The defect this file locks down: every extracted field carried a `confidence`
float, and for digital-born PDFs that float was the constant `0.995` — a number
nobody measured. It was then persisted to a `NOT NULL` column, averaged into a
calibration metric, and available to any future auto-accept rule as if it meant
"this value is probably right".

What is asserted here:

* a value is only reported when the extraction path actually measured one;
* the provenance (`engine_line` vs `not_measured`) travels with the value, in the
  API, in the database and in the benchmark;
* calibration is computed over measured confidences only, and is `None` rather
  than `0.0` when nothing was measured;
* human edit, raw-text preservation and the raw-immutability trigger still hold;
* and — the part that matters most — **no confidence value can authorise
  posting**. Validation, approval, segregation of duties and the posted-state
  gate remain the only path to the ledger, whatever the score says.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import (
    CONFIDENCE_ENGINE_LINE,
    CONFIDENCE_NOT_MEASURED,
    CONFIDENCE_SCOPE_LINE,
    CONFIDENCE_SCOPE_UNAVAILABLE,
    extract_document_ocr,
    parse_structured_invoice_text,
    update_extracted_field_by_human,
    upload_and_process_supplier_document,
)
from dz_erp.intake_ocr import PrivateDocumentVault


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def digital_pdf(lines: list[str]) -> bytes:
    """A PDF with real text objects, so pdfplumber can extract native text."""
    buffer = io.BytesIO()
    doc = canvas.Canvas(buffer, pagesize=(595, 842))
    y = 800
    for line in lines:
        doc.drawString(30, y, line)
        y -= 16
    doc.showPage()
    doc.save()
    return buffer.getvalue()


INVOICE_LINES = [
    "FACTURE N° FAC-2026-0001",
    "Fournisseur: SPARTE INDUSTRIE SARL",
    "NIF: 000216001234567",
    "RC: 16/00-0991216B12",
    "Date: 2026-02-14",
    "Montant HT: 1000.00 DZD",
    "TVA 19%: 190.00 DZD",
    "Montant TTC: 1190.00 DZD",
]


# ----------------------------------------------------------------------
# 1. Provenance at the extraction boundary
# ----------------------------------------------------------------------
def test_digital_pdf_reports_no_confidence_instead_of_a_constant() -> None:
    """The regression itself: native text must not produce a fabricated score."""
    result = extract_document_ocr(digital_pdf(INVOICE_LINES), "application/pdf")

    assert result["engine_name"] == "pdfplumber"
    assert result["confidence_kind"] == CONFIDENCE_NOT_MEASURED
    assert result["fields"], "the fixture must actually extract fields"

    for name, field in result["fields"].items():
        assert field["confidence"] is None, f"{name} invented a confidence"
        assert field["confidence_kind"] == CONFIDENCE_NOT_MEASURED
        assert field["confidence_scope"] == CONFIDENCE_SCOPE_UNAVAILABLE
        assert field["confidence_calibrated"] is False

    # 0.995 was the specific fabricated value; it must not appear anywhere.
    assert "0.995" not in str(result["fields"])
    for raw in result["raw_lines"]:
        assert raw["confidence"] is None
        assert raw["confidence_kind"] == CONFIDENCE_NOT_MEASURED


def test_ocr_path_reports_the_engine_score_with_its_scope() -> None:
    """A real engine score is kept, but labelled as a line score, not a field probability."""
    result = extract_document_ocr(digital_pdf(INVOICE_LINES), "application/pdf", force_image_ocr=True)

    assert result["engine_name"] == "rapidocr-onnxruntime"
    assert result["confidence_kind"] == CONFIDENCE_ENGINE_LINE
    assert result["fields"]

    for name, field in result["fields"].items():
        assert field["confidence"] is not None, f"{name} lost a measured score"
        assert 0.0 <= field["confidence"] <= 1.0
        assert field["confidence_kind"] == CONFIDENCE_ENGINE_LINE
        assert field["confidence_scope"] == CONFIDENCE_SCOPE_LINE
        # The score belongs to the line; nothing measured the field.
        assert field["confidence_calibrated"] is False
        assert "not a calibrated probability" in field["confidence_baseline"]


def test_every_field_states_that_review_is_required() -> None:
    for forced in (False, True):
        result = extract_document_ocr(digital_pdf(INVOICE_LINES), "application/pdf", force_image_ocr=forced)
        for name, field in result["fields"].items():
            assert field["requires_human_review"] is True, name


# ----------------------------------------------------------------------
# 2. Parser-level behaviour (no engine involved)
# ----------------------------------------------------------------------
def test_parser_rejects_an_unknown_confidence_kind() -> None:
    with pytest.raises(ValueError, match="Unknown confidence kind"):
        parse_structured_invoice_text([("NIF: 000216001234567", 0.9, [])], confidence_kind="measured_probability")


def test_parser_propagates_none_for_every_field_and_line() -> None:
    meta = [
        ("ITEM SKU-1 Widgets QTY: 2 PU: 100.00 TVA: 19% HT: 200.00", None, []),
        ("TOTAL HT: 200.00", None, []),
        ("TOTAL TTC: 238.00", None, []),
    ]
    parsed = parse_structured_invoice_text(meta, confidence_kind=CONFIDENCE_NOT_MEASURED)

    assert parsed["fields"]["amount_ht_dzd"]["confidence"] is None
    assert parsed["fields"]["amount_ttc_dzd"]["confidence"] is None
    assert parsed["lines"], "line items must be parsed"
    assert parsed["lines"][0]["confidence"] is None
    assert parsed["lines"][0]["confidence_kind"] == CONFIDENCE_NOT_MEASURED


def test_parser_keeps_measured_values_and_marks_them_uncalibrated() -> None:
    meta = [("NIF: 000216001234567", 0.8123, [])]
    parsed = parse_structured_invoice_text(meta, confidence_kind=CONFIDENCE_ENGINE_LINE)
    field = parsed["fields"]["supplier_nif"]
    assert field["confidence"] == 0.8123
    assert field["confidence_kind"] == CONFIDENCE_ENGINE_LINE
    assert field["confidence_calibrated"] is False


def test_confidence_propagation_is_from_the_matched_line_only() -> None:
    """A field takes the score of the line it came from — not of a neighbouring line."""
    meta = [
        ("NIF: 000216001234567", 0.41, []),
        ("RC: 16/00-0991216B12", 0.93, []),
    ]
    parsed = parse_structured_invoice_text(meta)
    assert parsed["fields"]["supplier_nif"]["confidence"] == 0.41
    assert parsed["fields"]["supplier_rc"]["confidence"] == 0.93


def test_malformed_confidence_is_rejected_rather_than_coerced() -> None:
    """A non-numeric score is a bug in the caller, not something to round to 0."""
    with pytest.raises((TypeError, ValueError)):
        parse_structured_invoice_text([("NIF: 000216001234567", "high", [])])


# ----------------------------------------------------------------------
# 3. Persistence, immutability and human correction
# ----------------------------------------------------------------------
def _deployment(tmp_path: Path) -> tuple:
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role) VALUES (10, 1, 'clerk', 'clerk')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role) VALUES (11, 1, 'acct', 'accountant')"
        )
    return db, vault


def test_database_stores_null_when_nothing_was_measured(tmp_path: Path) -> None:
    db, vault = _deployment(tmp_path)
    result = upload_and_process_supplier_document(
        db=db, vault=vault, company_id=1, user_id=10,
        filename="invoice.pdf", mime_type="application/pdf",
        content=digital_pdf(INVOICE_LINES),
    )
    assert result["workflow_state"] == "NeedsReview"

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT field_name, confidence, confidence_kind, confidence_scope, confidence_calibrated"
            " FROM ocr_extracted_fields WHERE document_id = ? AND field_name != 'line_item_json'",
            (result["document_id"],),
        ).fetchall()

    assert rows
    for row in rows:
        assert row["confidence"] is None
        assert row["confidence_kind"] == CONFIDENCE_NOT_MEASURED
        assert row["confidence_scope"] == CONFIDENCE_SCOPE_UNAVAILABLE
        assert row["confidence_calibrated"] == 0


def test_ocr_scan_stores_the_line_score_with_provenance(tmp_path: Path) -> None:
    db, vault = _deployment(tmp_path)
    result = upload_and_process_supplier_document(
        db=db, vault=vault, company_id=1, user_id=10,
        filename="scan.pdf", mime_type="application/pdf",
        content=digital_pdf(INVOICE_LINES), force_image_ocr=True,
    )
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT confidence, confidence_kind, confidence_scope"
            " FROM ocr_extracted_fields WHERE document_id = ? AND field_name != 'line_item_json'",
            (result["document_id"],),
        ).fetchall()

    assert rows
    for row in rows:
        assert row["confidence"] is not None
        assert row["confidence_kind"] == CONFIDENCE_ENGINE_LINE
        assert row["confidence_scope"] == CONFIDENCE_SCOPE_LINE


def test_human_edit_preserves_raw_text_and_confidence_provenance(tmp_path: Path) -> None:
    db, vault = _deployment(tmp_path)
    result = upload_and_process_supplier_document(
        db=db, vault=vault, company_id=1, user_id=10,
        filename="invoice.pdf", mime_type="application/pdf",
        content=digital_pdf(INVOICE_LINES), force_image_ocr=True,
    )
    # A scanned fixture: OCR output varies, so pick a field that was extracted.
    assert result["fields"]
    field_name = next(iter(result["fields"]))
    before = result["fields"][field_name]

    updated = update_extracted_field_by_human(
        db, company_id=1, user_id=10, document_id=result["document_id"],
        field_name=field_name, new_value="OVERRIDDEN",
    )

    assert updated["raw_text"] == before["raw_text"]
    assert updated["confidence"] == before["confidence"]
    assert updated["confidence_kind"] == before["confidence_kind"]
    assert updated["human_override_value"] == "OVERRIDDEN"
    assert updated["edited_by_id"] == 10


def test_raw_confidence_and_provenance_cannot_be_overwritten(tmp_path: Path) -> None:
    """The trigger must guard provenance too, or a NULL could be turned into a 'score'."""
    db, vault = _deployment(tmp_path)
    result = upload_and_process_supplier_document(
        db=db, vault=vault, company_id=1, user_id=10,
        filename="invoice.pdf", mime_type="application/pdf",
        content=digital_pdf(INVOICE_LINES),
    )
    from dz_erp.database import FinancialIntegrityError

    with pytest.raises(FinancialIntegrityError, match="OCR_RAW_IMMUTABILITY_VIOLATION"):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE ocr_extracted_fields SET confidence = 0.99 WHERE document_id = ?",
                (result["document_id"],),
            )
    with pytest.raises(FinancialIntegrityError, match="OCR_RAW_IMMUTABILITY_VIOLATION"):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE ocr_extracted_fields SET confidence_kind = 'engine_line' WHERE document_id = ?",
                (result["document_id"],),
            )


# ----------------------------------------------------------------------
# 4. Legacy database migration
# ----------------------------------------------------------------------
LEGACY_OCR_TABLE = """
CREATE TABLE ocr_extracted_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 1,
    line_index INTEGER,
    field_name TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    normalized_value TEXT,
    confidence REAL NOT NULL,
    bbox_json TEXT NOT NULL,
    ocr_model_version TEXT NOT NULL,
    human_override_value TEXT,
    edited_by_id INTEGER,
    edited_at TEXT,
    created_at TEXT NOT NULL
);
"""


def test_legacy_rows_are_relabelled_by_their_actual_extraction_path(tmp_path: Path) -> None:
    """A pre-existing database must not keep a fabricated 0.995 as if it were data."""
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript(LEGACY_OCR_TABLE)
    conn.executemany(
        "INSERT INTO ocr_extracted_fields (company_id, document_id, field_name, raw_text,"
        " normalized_value, confidence, bbox_json, ocr_model_version, created_at)"
        " VALUES (1, ?, 'amount_ht_dzd', 'Montant HT: 1000.00', '1000.00', ?, '[]', 'v', '2026-01-01')",
        [("doc-native", 0.995), ("doc-scan", 0.97)],
    )
    conn.executescript(
        """
        CREATE TABLE source_documents (
            id TEXT PRIMARY KEY, company_id INTEGER, ocr_engine TEXT
        );
        INSERT INTO source_documents (id, company_id, ocr_engine) VALUES ('doc-native', 1, 'pdfplumber');
        INSERT INTO source_documents (id, company_id, ocr_engine) VALUES ('doc-scan', 1, 'rapidocr-onnxruntime');
        """
    )
    conn.commit()
    conn.close()

    ERPDatabase(str(path))  # runs the migration

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    rows = {
        row["document_id"]: dict(row)
        for row in conn.execute("SELECT * FROM ocr_extracted_fields").fetchall()
    }
    conn.close()

    native = rows["doc-native"]
    assert native["confidence"] is None, "the fabricated constant survived the migration"
    assert native["confidence_kind"] == CONFIDENCE_NOT_MEASURED
    assert native["confidence_scope"] == CONFIDENCE_SCOPE_UNAVAILABLE
    # Raw evidence is untouched.
    assert native["raw_text"] == "Montant HT: 1000.00"
    assert native["normalized_value"] == "1000.00"

    scan = rows["doc-scan"]
    assert scan["confidence"] == 0.97
    # Honest about what we cannot verify for pre-migration rows.
    assert scan["confidence_kind"] == "legacy_line_score"
    assert scan["confidence_scope"] == CONFIDENCE_SCOPE_LINE
    for row in rows.values():
        assert row["confidence_calibrated"] == 0


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy2.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript(LEGACY_OCR_TABLE)
    conn.execute(
        "INSERT INTO ocr_extracted_fields (company_id, document_id, field_name, raw_text,"
        " normalized_value, confidence, bbox_json, ocr_model_version, created_at)"
        " VALUES (1, 'doc', 'x', 'raw', 'raw', 0.5, '[]', 'v', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    ERPDatabase(str(path))
    ERPDatabase(str(path))

    conn = sqlite3.connect(str(path))
    rows = conn.execute("SELECT confidence FROM ocr_extracted_fields").fetchall()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ocr_extracted_fields)")}
    conn.close()
    assert len(rows) == 1, "the rebuild duplicated or dropped a row"
    assert "confidence_kind" in cols


def test_confidence_migration_rolls_back_if_copy_fails(tmp_path: Path) -> None:
    """A failed table rebuild must not strand the live table under a legacy name."""
    path = tmp_path / "legacy-failing-migration.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript(LEGACY_OCR_TABLE)
    # Legacy schema allowed this value; the new schema correctly rejects it.
    # It forces the INSERT ... SELECT stage to fail after the table rename/create.
    conn.execute(
        "INSERT INTO ocr_extracted_fields (company_id, document_id, field_name, raw_text,"
        " normalized_value, confidence, bbox_json, ocr_model_version, created_at)"
        " VALUES (1, 'doc-scan', 'amount_ht_dzd', 'raw amount', 'raw amount', 1.5, '[]', 'v', '2026-01-01')"
    )
    conn.executescript(
        """
        CREATE TABLE source_documents (
            id TEXT PRIMARY KEY, company_id INTEGER, ocr_engine TEXT
        );
        INSERT INTO source_documents (id, company_id, ocr_engine)
        VALUES ('doc-scan', 1, 'rapidocr-onnxruntime');
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        ERPDatabase(str(path))

    conn = sqlite3.connect(str(path))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ocr_extracted_fields)")}
    row = conn.execute(
        "SELECT confidence, raw_text FROM ocr_extracted_fields WHERE document_id = 'doc-scan'"
    ).fetchone()
    conn.close()

    assert "ocr_extracted_fields" in tables, "the original live table must survive a failed migration"
    assert "ocr_extracted_fields_legacy" not in tables, "a rollback must undo the table rename"
    assert "confidence_kind" not in columns, "the old schema must remain intact after rollback"
    assert row == (1.5, "raw amount"), "the original row must remain intact after rollback"


# ----------------------------------------------------------------------
# 5. A confidence value can never authorise posting
# ----------------------------------------------------------------------
def test_confidence_alone_cannot_reach_the_ledger(tmp_path: Path) -> None:
    """A perfect-looking extraction still cannot post: the gate is approval, not a score.

    This is the property that makes the whole confidence discussion safe. No code
    path reads a confidence value to decide whether to post; `post_supplier_invoice`
    requires the `Approved` state, which requires a different user (segregation of
    duties). The test therefore asserts the *behaviour*: an invoice whose fields
    were extracted with high scores, fully valid, still cannot reach the ledger
    without approval.
    """
    import dz_erp.procurement_stock as ps

    db, vault = _deployment(tmp_path)
    result = upload_and_process_supplier_document(
        db=db, vault=vault, company_id=1, user_id=10,
        filename="invoice.pdf", mime_type="application/pdf",
        content=digital_pdf(INVOICE_LINES), force_image_ocr=True,
    )
    # Every extracted field carries a real engine score from the OCR path.
    assert result["fields"]
    assert all(f["confidence"] is not None for f in result["fields"].values())

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code,"
            " tax_regime, nif, nis, ai, rc)"
            " VALUES (7, 1, 'SPARTE', 'supplier', 'DZ', 'REEL', '000216001234567',"
            " '000216001234567', '16019912316', '16/00-0991216B12')"
        )

    invoice = ps.create_and_validate_supplier_invoice(
        db=db, company_id=1, user_id=10, supplier_id=7,
        supplier_invoice_no="FAC-2026-0001", invoice_date="2026-02-14",
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "tax_rate": "19", "line_ht_dzd": "1000.00"}],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
        document_id=result["document_id"],
    )
    # Deterministic validation passed, so the invoice is Validated - not Posted.
    assert invoice["workflow_state"] == "Validated"
    assert invoice["validation_errors"] == []

    with pytest.raises(ps.FinancialIntegrityError, match="requires 'Approved' state"):
        ps.post_supplier_invoice(
            db=db, company_id=1, accountant_user_id=11,
            invoice_id=invoice["invoice_id"], idempotency_key="confidence-cannot-post",
        )

    # And nothing reached the books.
    with db.transaction() as conn:
        posted = conn.execute(
            "SELECT COUNT(*) AS n FROM journal_entries WHERE source_id = ?",
            (str(invoice["invoice_id"]),),
        ).fetchone()["n"]
    assert posted == 0


def test_inconsistent_totals_still_force_review(tmp_path: Path) -> None:
    """Confidence provenance must not have disturbed deterministic validation."""
    import dz_erp.procurement_stock as ps

    db, _vault = _deployment(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime, nif, nis, ai, rc)"
            " VALUES (8, 1, 'ACME', 'supplier', 'DZ', 'REEL', '000216001234567', '000216001234567', '16019912316', '16/00-0991216B12')"
        )
    invoice = ps.create_and_validate_supplier_invoice(
        db=db, company_id=1, user_id=10, supplier_id=8,
        supplier_invoice_no="FAC-2026-0002", invoice_date="2026-02-14",
        lines=[{"sku": "SKU-1", "description": "Widgets", "qty": "2",
                "unit_price_dzd": "500.00", "tax_rate": "19", "line_ht_dzd": "1000.00"}],
        amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="9999.00",
    )
    assert invoice["workflow_state"] == "NeedsReview"
    assert invoice["validation_errors"]


# ----------------------------------------------------------------------
# 6. The benchmark must not manufacture a calibration number
# ----------------------------------------------------------------------
def test_calibration_is_computed_over_measured_confidences_only(tmp_path: Path) -> None:
    """ECE over a fabricated constant was the misleading metric this phase removes.

    The benchmark mixes digital PDFs (never measured) with scans (measured). The
    ECE must report its own observation count and the number of excluded fields,
    so it can never again be read as a statement about all 144 fields.
    """
    from dz_erp.benchmark import run_empirical_ocr_benchmark

    report = run_empirical_ocr_benchmark(tmp_path / "bench")

    assert report["expected_calibration_error_ece"] is not None
    assert report["calibration_observations"] > 0
    assert report["calibration_excluded_no_confidence"] > 0, (
        "digital-PDF fields have no measured confidence and must be excluded, not imputed"
    )
    assert report["calibration_observations"] + report["calibration_excluded_no_confidence"] == (
        report["dataset_size_documents"] * len(report["by_field"])
    )
    # Sanity: the note explains the exclusion rather than merely labelling a number.
    assert "measured" in report["calibration_note"].lower()
    assert "excluded" in report["calibration_note"].lower()
