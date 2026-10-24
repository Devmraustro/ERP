# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 21 — Arabic script cannot be recognised, so route it explicitly (O3).

The bundled RapidOCR head is `PP-OCRv4` trained on Latin and Chinese text. It has no
Arabic characters in its dictionary, so a scanned Arabic-only invoice cannot be
recognised — not poorly, *not at all*. Before this change nothing in the pipeline
knew that: such a document was extracted by the Latin head, produced a handful of
plausible-looking Latin fragments or nothing, and entered review as an ordinary
extraction. A reviewer sees an empty form and cannot tell "the parser missed a
field" from "this engine cannot read this script".

These tests pin the routing contract:

* the capability gap is stated in code (``ARABIC_RECOGNITION_HEAD_AVAILABLE``),
  so adding a model is a deliberate act, not a default flip;
* a document that cannot be read is routed to **manual transcription**, with a
  reason that names the missing model;
* native (digital-born) Arabic is *not* penalised — `pdfplumber` returns exact
  Unicode, so those documents route to ordinary field review;
* routing never replaces human review, and never touches amounts;
* the decision is persisted and audited, so a later reviewer can tell why.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from dz_erp.benchmark import build_synthetic_algerian_benchmark_specs, render_synthetic_invoice_image
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import (
    ARABIC_RECOGNITION_HEAD,
    ARABIC_RECOGNITION_HEAD_AVAILABLE,
    EXTRACTION_ROUTES,
    REASON_DECLARED_ARABIC,
    REASON_FIELDS_EXTRACTED,
    REASON_NATIVE_ARABIC_SUPPORTED,
    REASON_NO_FIELDS,
    ROUTE_MANUAL_TRANSCRIPTION,
    ROUTE_STANDARD_REVIEW,
    PrivateDocumentVault,
    assess_review_route,
    extract_document_ocr,
    is_arabic_char,
    is_latin_letter,
    script_profile,
    upload_and_process_supplier_document,
)

ARABIC_ONLY = [
    "شركة النور للتجارة والتوزيع",
    "فاتورة رقم FC-2026-0042",
    "التاريخ: 2026-03-04",
    "المجموع دون الرسم: 120000.00 دج",
    "الرسم على القيمة المضافة: 22800.00 دج",
    "المجموع الإجمالي: 142800.00 دج",
]


def _lines(texts):
    return [(text, 0.91, [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]) for text in texts]


@pytest.fixture(scope="module")
def spec():
    return dict(build_synthetic_algerian_benchmark_specs()[0])


# ----------------------------------------------------------------------
# 1. Script measurement
# ----------------------------------------------------------------------
def test_arabic_characters_are_recognised_in_every_relevant_block() -> None:
    for char in "شركةالنور":                        # Arabic letters, no spaces
        assert is_arabic_char(char), char
    assert is_arabic_char("\u0750")                # Arabic Supplement
    assert is_arabic_char("\ufdf0")                # Presentation Forms-A
    assert not is_arabic_char("A")
    assert not is_arabic_char("é")
    assert not is_arabic_char("1")
    assert not is_arabic_char(" ")                 # digits and spaces are not letters


def test_latin_letters_are_recognised_including_accents() -> None:
    for char in "FactureÉlectricité":
        assert is_latin_letter(char), char
    assert not is_latin_letter("ش")
    assert not is_latin_letter("5")
    assert not is_latin_letter("د")


def test_script_profile_counts_and_dominance() -> None:
    profile = script_profile(_lines(ARABIC_ONLY))
    assert profile["arabic_letters"] > 90
    assert profile["latin_letters"] < 10          # "FC" and the digits only
    assert profile["arabic_dominant"] is True
    assert profile["measured_on"] == "extracted_text"

    mixed = script_profile(_lines(["FOURNISSEUR: Alpha SARL", "الشركة: النور"]))
    assert mixed["latin_letters"] > mixed["arabic_letters"]
    assert mixed["arabic_dominant"] is False

    empty = script_profile([])
    assert empty == {
        "arabic_letters": 0,
        "latin_letters": 0,
        "arabic_dominant": False,
        "measured_on": "extracted_text",
    }


# ----------------------------------------------------------------------
# 2. The capability gap is stated, not implied
# ----------------------------------------------------------------------
def test_the_arabic_head_is_declared_unavailable() -> None:
    """Turning this on must be a deliberate change, accompanied by a benchmark."""
    assert ARABIC_RECOGNITION_HEAD_AVAILABLE is False
    assert ARABIC_RECOGNITION_HEAD == "arabic_PP-OCRv4_rec"


def test_every_route_is_a_review_route() -> None:
    routes = {ROUTE_STANDARD_REVIEW, ROUTE_MANUAL_TRANSCRIPTION}
    assert set(EXTRACTION_ROUTES) == routes
    for engine in ("pdfplumber", "rapidocr-onnxruntime"):
        for fields in (0, 3):
            decision = assess_review_route(
                engine_name=engine, lines_with_meta=_lines(["x"]), extracted_field_count=fields
            )
            assert decision["route"] in routes
            assert decision["requires_human_review"] is True


# ----------------------------------------------------------------------
# 3. Routing decisions
# ----------------------------------------------------------------------
def test_native_arabic_text_is_not_downgraded() -> None:
    """pdfplumber returns exact Unicode: Arabic there is a language, not a failure."""
    decision = assess_review_route(
        engine_name="pdfplumber",
        lines_with_meta=_lines(ARABIC_ONLY),
        extracted_field_count=4,
    )
    assert decision["route"] == ROUTE_STANDARD_REVIEW
    assert decision["reason"] == REASON_NATIVE_ARABIC_SUPPORTED
    assert decision["requires_manual_transcription"] is False
    assert decision["arabic_recognition_available"] is False


def test_declared_arabic_scan_routes_to_manual_transcription() -> None:
    decision = assess_review_route(
        engine_name="rapidocr-onnxruntime",
        lines_with_meta=_lines(["| || |l| 1", "j~l~ 2026"]),
        extracted_field_count=0,
        declared_language="ar",
    )
    assert decision["route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert decision["reason"] == REASON_DECLARED_ARABIC
    assert decision["requires_manual_transcription"] is True
    assert ARABIC_RECOGNITION_HEAD in decision["detail"]
    assert decision["declared_language"] == "ar"


def test_arabic_dominant_engine_output_also_routes_to_manual() -> None:
    """If the head ever emits Arabic characters, it still cannot be trusted to have read them."""
    decision = assess_review_route(
        engine_name="rapidocr-onnxruntime",
        lines_with_meta=_lines(ARABIC_ONLY),
        extracted_field_count=2,
    )
    assert decision["route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert decision["script"]["arabic_dominant"] is True


def test_a_scan_that_yields_nothing_is_not_reported_as_a_parsing_success() -> None:
    decision = assess_review_route(
        engine_name="rapidocr-onnxruntime",
        lines_with_meta=_lines(["~~~ ~~"]),
        extracted_field_count=0,
    )
    assert decision["route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert decision["reason"] == REASON_NO_FIELDS


def test_a_recognised_latin_invoice_routes_to_standard_review() -> None:
    decision = assess_review_route(
        engine_name="pdfplumber",
        lines_with_meta=_lines(["FOURNISSEUR: Alpha SARL", "FACTURE N: FC-1"]),
        extracted_field_count=2,
    )
    assert decision["route"] == ROUTE_STANDARD_REVIEW
    assert decision["reason"] == REASON_FIELDS_EXTRACTED


def test_declared_language_is_case_and_region_tolerant() -> None:
    for declared in ("ar", "AR", "ar-DZ", "ar_001"):
        decision = assess_review_route(
            engine_name="rapidocr-onnxruntime",
            lines_with_meta=_lines([]),
            extracted_field_count=0,
            declared_language=declared,
        )
        assert decision["reason"] == REASON_DECLARED_ARABIC, declared


# ----------------------------------------------------------------------
# 4. Extraction end to end
# ----------------------------------------------------------------------
def test_extraction_of_a_declared_arabic_scan_reports_the_gap(spec) -> None:
    image = render_synthetic_invoice_image(spec)
    result = extract_document_ocr(image, "image/png", declared_language="ar")

    assert result["engine_name"] == "rapidocr-onnxruntime"
    assert result["review_route"]["route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert result["requires_manual_transcription"] is True
    # Whatever the Latin head guessed is still returned - it is evidence of what the
    # engine saw, and it is labelled as untrustworthy rather than discarded.
    assert isinstance(result["fields"], dict)
    assert result["review_route"]["arabic_recognition_available"] is False


def test_extraction_of_a_french_scan_without_declaration_is_unaffected(spec) -> None:
    image = render_synthetic_invoice_image(spec)
    result = extract_document_ocr(image, "image/png")

    assert result["review_route"]["route"] in (ROUTE_STANDARD_REVIEW, ROUTE_MANUAL_TRANSCRIPTION)
    assert result["review_route"]["reason"] != REASON_DECLARED_ARABIC
    # A well-recognised Latin invoice must not be pushed into manual transcription.
    if result["fields"]:
        assert result["review_route"]["route"] == ROUTE_STANDARD_REVIEW


def _arabic_only_pdf() -> bytes:
    """A digital PDF whose only text is Arabic."""
    from reportlab.pdfgen import canvas

    from dz_erp import benchmark

    benchmark._ensure_font()
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, invariant=True)
    # Read the module attribute: importing the name copies False before the font
    # is registered, and Helvetica cannot encode Arabic at all.
    pdf.setFont("DejaVu" if benchmark._FONT_REGISTERED else "Helvetica", 11)
    y = 780
    for line in ARABIC_ONLY:
        pdf.drawString(45, y, line)
        y -= 20
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def test_digital_arabic_invoice_is_extracted_natively() -> None:
    result = extract_document_ocr(_arabic_only_pdf(), "application/pdf")

    assert result["engine_name"] == "pdfplumber"
    assert result["review_route"]["script"]["arabic_letters"] > 50
    assert result["review_route"]["route"] == ROUTE_STANDARD_REVIEW
    assert result["review_route"]["reason"] == REASON_NATIVE_ARABIC_SUPPORTED
    # Native extraction measures no confidence, and routing must not invent one.
    assert result["confidence_kind"] == "not_measured"


# ----------------------------------------------------------------------
# 5. Persistence and audit
# ----------------------------------------------------------------------
def _seed(tmp_path: Path):
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, password_hash, role)"
            " VALUES (7, 1, 'clerk', 'x', 'clerk')"
        )
    return db, vault


def test_upload_persists_and_audits_the_route(tmp_path: Path, spec) -> None:
    db, vault = _seed(tmp_path)
    image = render_synthetic_invoice_image(spec)

    result = upload_and_process_supplier_document(
        db, vault, 1, 7, "arabic.png", "image/png", image, declared_language="ar"
    )

    assert result["extraction_route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert result["requires_manual_transcription"] is True
    assert result["workflow_state"] == "NeedsReview"

    with db.transaction() as conn:
        row = conn.execute(
            "SELECT extraction_route, route_reason, declared_language, script_arabic_letters,"
            " script_latin_letters, workflow_state FROM source_documents WHERE id = ?",
            (result["document_id"],),
        ).fetchone()
    assert row["extraction_route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert row["route_reason"] == REASON_DECLARED_ARABIC
    assert row["declared_language"] == "ar"
    assert row["script_arabic_letters"] == result["review_route"]["script"]["arabic_letters"]
    assert row["script_latin_letters"] == result["review_route"]["script"]["latin_letters"]

    with db.transaction() as conn:
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT action_type, after_json FROM audit_events"
                " WHERE company_id = 1 AND action_type = 'OCR_EXTRACTION_COMPLETED'"
                " ORDER BY id DESC"
            ).fetchall()
        ]
    assert events, "extraction must be audited"
    after = events[0]["after_json"]
    if isinstance(after, str):
        after = json.loads(after)
    assert after["extraction_route"] == ROUTE_MANUAL_TRANSCRIPTION
    assert after["requires_manual_transcription"] is True
    assert after["arabic_recognition_available"] is False


def test_routing_does_not_create_an_invoice_or_move_the_workflow_forward(tmp_path: Path, spec) -> None:
    """An Arabic scan must not gain any shortcut into the books."""
    db, vault = _seed(tmp_path)
    image = render_synthetic_invoice_image(spec)
    result = upload_and_process_supplier_document(
        db, vault, 1, 7, "arabic.png", "image/png", image, declared_language="ar"
    )
    with db.transaction() as conn:
        invoices = conn.execute("SELECT COUNT(*) AS n FROM supplier_invoices").fetchone()["n"]
        documents = conn.execute(
            "SELECT workflow_state FROM source_documents WHERE id = ?", (result["document_id"],)
        ).fetchone()
    assert invoices == 0
    assert documents["workflow_state"] == "NeedsReview"


def test_a_route_value_outside_the_vocabulary_is_rejected_by_the_schema(tmp_path: Path) -> None:
    db, _vault = _seed(tmp_path)
    with pytest.raises(Exception):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size,"
                " file_sha256, vault_path, security_status, workflow_state, uploaded_by_id,"
                " uploaded_at, extraction_route) VALUES ('x', 1, 'f', 'image/png', 10,"
                " 'h', 'p', 'CLEAN', 'NeedsReview', 7, '2026-01-01', 'auto_accepted')"
            )


def test_routing_migration_is_idempotent_and_leaves_old_rows_unknown(tmp_path: Path) -> None:
    path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, password_hash, role)"
            " VALUES (7, 1, 'clerk', 'x', 'clerk')"
        )
        conn.execute(
            "INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size,"
            " file_sha256, vault_path, security_status, workflow_state, uploaded_by_id, uploaded_at)"
            " VALUES ('legacy', 1, 'f', 'image/png', 10, 'h', 'p', 'CLEAN', 'NeedsReview', 7, '2026-01-01')"
        )

    reopened = ERPDatabase(str(path))  # runs the migration again
    with reopened.transaction() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(source_documents)")}
        row = conn.execute("SELECT extraction_route, route_reason FROM source_documents WHERE id = 'legacy'").fetchone()
    assert {"extraction_route", "route_reason", "declared_language"} <= columns
    assert row["extraction_route"] is None, "a document predating routing must not claim a route"
    assert row["route_reason"] is None


# ----------------------------------------------------------------------
# 6. The API entry point carries the declaration through
# ----------------------------------------------------------------------
def test_api_upload_accepts_a_declared_language_and_reports_the_route(tmp_path: Path, spec) -> None:
    """The route must be visible to the client that uploaded the document."""
    from fastapi.testclient import TestClient

    from dz_erp.auth import hash_password
    from dz_erp.serve import build_app

    state = tmp_path / "state"
    state.mkdir()
    app = build_app(db_path=state / "erp.sqlite3", vault_path=state / "vault")
    db = app.state.db
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, password_hash, role)"
            " VALUES (7, 1, 'clerk', ?, 'clerk')",
            (hash_password("Clerk#2026x", iterations=1000),),
        )
    client = TestClient(app)
    token = client.post("/api/v1/auth/token", json={"username": "clerk", "password": "Clerk#2026x"})
    assert token.status_code == 200, token.text

    image = render_synthetic_invoice_image(spec)
    response = client.post(
        "/api/v1/documents/upload",
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        files={"file": ("arabic.png", image, "image/png")},
        data={"declared_language": "ar"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workflow_state"] == "NeedsReview"
    assert body["extraction_route"] == "manual_transcription"
    assert body["requires_manual_transcription"] is True
    assert body["review_route"]["reason"] == "DECLARED_ARABIC_SCAN"
