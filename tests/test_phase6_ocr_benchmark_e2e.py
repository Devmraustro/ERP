# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 6 Tests — Empirical Algerian Invoice OCR Benchmark & 14-Step End-to-End Acceptance Suite."""

from pathlib import Path
from typing import Any, Dict

import pytest

from dz_erp.benchmark import (
    ExternalDatasetError,
    build_synthetic_algerian_benchmark_specs,
    evaluate_external_dataset,
    load_external_dataset,
    render_synthetic_invoice_pdf,
    run_empirical_ocr_benchmark,
)
from dz_erp.database import ERPDatabase
from dz_erp.decimal_utils import round_dzd, to_decimal
from dz_erp.intake_ocr import (
    PrivateDocumentVault,
    update_extracted_field_by_human,
    upload_and_process_supplier_document,
)
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    create_purchase_order,
    decide_invoice_approval,
    post_supplier_invoice,
    receive_goods_against_po,
    submit_invoice_for_approval,
)
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup


def test_empirical_ocr_benchmark_across_6_algerian_invoice_strata(tmp_path: Path) -> None:
    """Run the 18-document synthetic Algerian invoice benchmark and verify field & line metrics."""
    # Resolve the benchmark output directory from this file, never from an
    # absolute path: the previous hardcoded path only existed on the machine the
    # benchmark was authored on, so the test failed with PermissionError on any
    # other host.
    # Write to the test's own directory, never to the tracked `benchmarks/results`.
    # The benchmark records wall-clock latencies, which vary by host; running it
    # in place rewrote the committed evidence (PDFs and the results JSON) on every
    # test run, so a developer could commit their machine's timings as if they were
    # the recorded run. The committed artefact stays the reference; this test
    # asserts the metrics, not the timings.
    bench_dir = tmp_path / "benchmarks" / "results"
    report = run_empirical_ocr_benchmark(bench_dir)

    assert report["dataset_size_documents"] == 18

    # Thresholds are floors with a deliberate margin below the measured value,
    # not tight bounds on it.  ONNX Runtime, OpenCV and PDFium all dispatch SIMD
    # kernels at runtime based on the host CPU, so a borderline glyph can be
    # recognised differently on a different machine even with fully pinned
    # thread counts.  The spread observed before the thread counts were pinned
    # was 0.8958 (CI runner) against 0.9097 (authoring machine) - about one
    # field in 198 - so the floor sits roughly six fields below the measured
    # value.  A genuine regression (broken rendering, broken parser, wrong
    # model) moves this metric by tens of points, not by single fields.
    assert report["overall_field_exact_match_rate"] >= 0.88
    assert report["table_line_association_accuracy"] >= 0.85
    assert report["expected_calibration_error_ece"] <= 0.15
    assert (bench_dir / "ground_truth_and_results.json").exists()

    # --- arithmetic / tax consistency of the extracted totals ---------------
    # The extraction itself is already asserted above; this asserts that the
    # *reported consistency figures* are internally coherent and that the
    # ground-truth invoices balance (a broken fixture would otherwise make a
    # 0% consistency rate look like an extraction problem).
    consistency = report["arithmetic_consistency"]
    assert consistency["documents"] == 18
    assert 0 <= consistency["headers_consistent"] <= consistency["headers_evaluated"] <= 18
    assert 0 <= consistency["line_sums_consistent"] <= consistency["line_sums_evaluated"] <= 18
    # Every synthetic ground-truth document must balance: HT + TVA + timbre == TTC.
    for spec in build_synthetic_algerian_benchmark_specs():
        expected = (
            to_decimal(spec["amount_ht_dzd"])
            + to_decimal(spec["amount_tva_dzd"])
            + to_decimal(spec["amount_timbre_dzd"])
        )
        assert round_dzd(expected) == round_dzd(to_decimal(spec["amount_ttc_dzd"])), spec["doc_id"]

    # --- failure categories --------------------------------------------------
    failures = report["failure_categories"]
    per_field = report["by_field"]
    assert (
        failures["missing"]
        == sum(f["failures"]["missing"] for f in per_field.values())
    )
    assert (
        failures["mismatch"]
        == sum(f["failures"]["mismatch"] for f in per_field.values())
    )
    total_fields = sum(f["total"] for f in per_field.values())
    assert failures["missing"] + failures["mismatch"] == total_fields - sum(
        f["exact_match"] for f in per_field.values()
    )

    # --- language roll-up ----------------------------------------------------
    by_language = report["by_language"]
    assert sum(group["docs"] for group in by_language.values()) == 18
    assert {"french_digital", "bilingual_fr_ar", "french_raster"} == set(by_language)
    # The Arabic-only scanned stratum is deliberately absent, and the report
    # must say so rather than presenting a measured 0%.
    assert "arabic" not in by_language
    assert "arabic" in report["language_coverage_note"].lower()


def test_complete_14_step_end_to_end_supplier_invoice_workflow(tmp_path: Path) -> None:
    """Execute the mandatory 14-step end-to-end scenario from file upload to isolated backup/restore."""
    db_file = tmp_path / "e2e_erp.sqlite3"
    db = ERPDatabase(str(db_file))
    vault = PrivateDocumentVault(tmp_path / "e2e_vault")

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) VALUES (1, 'DZ1', 'SARL Constantine Tech', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (1, 1, 'clerk_e2e', 'clerk')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (2, 1, 'wh_e2e', 'warehouse')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (3, 1, 'approver_e2e', 'approver')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (4, 1, 'accountant_e2e', 'accountant')")
        conn.execute(
            """
            INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc)
            VALUES (500, 1, 'SARL Atlas Equipement', 'supplier',
                    '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')
            """
        )
        conn.execute(
            """
            INSERT INTO products (id, company_id, sku, name, is_storable, standard_cost_dzd, stock_account_code, clearing_account_code)
            VALUES (700, 1, 'SRV-RACK-01', 'Baie Serveur 42U', 1, '25000.000000', '31', '380')
            """
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)

    # Pre-requisite P2P setup: PO (4 units @ 25,000.00 DZD) and physical Goods Receipt (4 units)
    po = create_purchase_order(
        db,
        company_id=1,
        user_id=1,
        po_number="PO-2026-0500",
        supplier_id=500,
        lines=[{"product_id": 700, "description": "Baie Serveur 42U", "qty_ordered": "4", "unit_price_dzd": "25000.00", "tax_rate": "19.0"}],
    )
    po_line_id = po["po_line_ids"][0]
    grn = receive_goods_against_po(
        db,
        company_id=1,
        user_id=2,
        po_id=po["po_id"],
        grn_number="GRN-2026-0500",
        receipt_lines=[{"po_line_id": po_line_id, "qty_done": "4"}],
    )

    # Step 1, 2, 3: Upload supplier invoice file -> MIME/security checks -> OCR extraction
    pdf_bytes = render_synthetic_invoice_pdf(
        {
            "supplier_name": "SARL Atlas Equipement",
            "arabic_header": "شركة أطلس للتجهيز - فاتورة",
            "invoice_number": "FAC-2026-050O",  # Deliberate OCR 'O' typo to be corrected in Step 5
            "invoice_date": "2026-10-17",
            "supplier_nif": "001216099123456",
            "supplier_rc": "16/00-0991234B12",
            "supplier_ai": "16019912345",
            "payment_method": "BANK_TRANSFER",
            "po_number": "PO-2026-0500",
            "grn_number": "GRN-2026-0500",
            "amount_ht_dzd": "100000.00",
            "amount_tva_dzd": "19000.00",
            "amount_timbre_dzd": "0.00",
            "amount_ttc_dzd": "119000.00",
            "lines": [
                {
                    "sku": "SRV-RACK-01",
                    "description": "Baie Serveur 42U",
                    "qty": "4.00",
                    "unit_price_dzd": "25000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "100000.00",
                }
            ],
        }
    )
    doc = upload_and_process_supplier_document(
        db, vault, company_id=1, user_id=1, filename="facture_500.pdf", mime_type="application/pdf", content=pdf_bytes
    )
    assert doc["security_status"] == "CLEAN"
    assert doc["workflow_state"] == "NeedsReview"

    # Step 5: Edit one extracted field as a human reviewer while preserving raw OCR data
    edited_field = update_extracted_field_by_human(
        db,
        company_id=1,
        user_id=1,
        document_id=doc["document_id"],
        field_name="invoice_number",
        new_value="FAC-2026-0500",
    )
    assert edited_field["human_override_value"] == "FAC-2026-0500"
    assert "FAC-2026-050O" in edited_field["raw_text"]

    # Step 4, 6, 7: Create supplier invoice & run deterministic validation + 3-way match against PO & GRN
    inv = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=500,
        supplier_invoice_no=edited_field["normalized_value"],
        invoice_date=doc["fields"]["invoice_date"]["normalized_value"],
        payment_method="BANK_TRANSFER",
        amount_ht_dzd=doc["fields"]["amount_ht_dzd"]["normalized_value"],
        amount_tva_dzd=doc["fields"]["amount_tva_dzd"]["normalized_value"],
        amount_timbre_dzd="0.00",
        amount_ttc_dzd=doc["fields"]["amount_ttc_dzd"]["normalized_value"],
        po_id=po["po_id"],
        grn_id=grn["grn_id"],
        document_id=doc["document_id"],
        lines=[
            {
                "po_line_id": po_line_id,
                "product_id": 700,
                "description": "Baie Serveur 42U",
                "qty": "4",
                "unit_price_dzd": "25000.00",
                "tax_rate": "19.0",
                "line_ht_dzd": "100000.00",
            }
        ],
    )
    assert inv["workflow_state"] == "Validated"
    assert inv["validation_errors"] == []

    # Step 8: Submit for human approval
    sub = submit_invoice_for_approval(db, company_id=1, user_id=1, invoice_id=inv["invoice_id"])
    assert sub["workflow_state"] == "PendingApproval"

    # Step 9: Approve with an authorized role (approver_e2e != clerk_e2e)
    app_res = decide_invoice_approval(
        db, company_id=1, approver_user_id=3, invoice_id=inv["invoice_id"], approve=True, reason="3-way match verified"
    )
    assert app_res["workflow_state"] == "Approved"

    # Step 10: Post accounting entries idempotently
    post_res = post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=inv["invoice_id"], idempotency_key="e2e-post-500"
    )
    assert post_res["workflow_state"] == "Posted"
    assert post_res["total_debit_dzd"] == "119000.00"
    assert post_res["total_credit_dzd"] == "119000.00"

    # Step 11: Verify stock did not move twice (still 4 units, 1 stock move) and 380 clearing is reconciled
    with db.transaction() as conn:
        q = conn.execute("SELECT qty_on_hand FROM stock_quants WHERE product_id = 700").fetchone()
        sm_c = conn.execute("SELECT COUNT(*) AS c FROM stock_moves").fetchone()["c"]
        unrec_380 = conn.execute(
            "SELECT COUNT(*) AS c FROM journal_entry_lines WHERE account_code = '380' AND reconciled = 0"
        ).fetchone()["c"]
        assert q["qty_on_hand"] == "4.000000"
        assert sm_c == 1
        assert unrec_380 == 0

    # Step 12: Verify audit trail integrity
    audit_check = db.verify_audit_chain(company_id=1)
    assert audit_check["valid"] is True
    assert audit_check["checked_count"] >= 8

    # Step 13: Attempt to re-upload and re-post the same invoice and confirm duplicate protection
    dup_upload = upload_and_process_supplier_document(
        db, vault, company_id=1, user_id=1, filename="facture_500_dup.pdf", mime_type="application/pdf", content=pdf_bytes
    )
    assert dup_upload["workflow_state"] == "DuplicateRejected"

    re_post = post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=inv["invoice_id"], idempotency_key="e2e-post-500"
    )
    assert re_post["idempotent_replay"] is True
    assert re_post["journal_entry_id"] == post_res["journal_entry_id"]

    # Step 14: Back up and restore the resulting state in an isolated test environment and confirm integrity
    bk = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backups" / "e2e_backup.enc",
        backup_encryption_key=vault.encryption_key,
    )
    restored = restore_and_verify_backup(
        backup_path=Path(bk["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "e2e_restored",
    )
    assert restored["db_sha256_verified"] is True
    assert restored["vault_files_verified"] == 1
    assert restored["audit_chains_valid"] is True
    # Total trial balance across GRN (100,000.00) + Invoice (119,000.00) = 219,000.00 DZD
    assert restored["trial_balance_debit_dzd"] == "219000.00"
    assert restored["trial_balance_credit_dzd"] == "219000.00"


def test_ocr_engine_is_configured_for_deterministic_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OCR engine must run single-threaded so results do not depend on the host.

    ONNX Runtime defaults to one thread per core.  Parallel floating-point
    reductions then execute in a core-count-dependent order, so the same image
    can yield a different recognised character on a different machine - which
    makes the measured accuracy a property of the host rather than of the code.
    The options are asserted on the arguments actually handed to the engine,
    because an ONNX Runtime session does not expose its thread counts.
    """
    import cv2

    from dz_erp import intake_ocr

    captured: Dict[str, Any] = {}

    class _RecordingEngine:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(intake_ocr, "_RAPID_OCR_SINGLETON", None)
    monkeypatch.setattr(intake_ocr, "RapidOCR", _RecordingEngine)

    engine = intake_ocr.get_rapid_ocr()
    assert engine is not None
    assert captured == intake_ocr.DETERMINISTIC_INFERENCE_OPTIONS
    assert captured["intra_op_num_threads"] == 1
    assert captured["inter_op_num_threads"] == 1

    # OpenCV's own parallel loops are pinned for the same reason.
    assert cv2.getNumThreads() == 1


def test_ocr_engine_actually_loads_and_recognises_text() -> None:
    """The pinned engine must still load its models and produce recognised lines."""
    from dz_erp.benchmark import render_synthetic_invoice_image
    from dz_erp.intake_ocr import extract_document_ocr, get_rapid_ocr

    assert get_rapid_ocr() is not None

    png_bytes = render_synthetic_invoice_image(
        {
            "supplier_name": "Supplier Alpha",
            "invoice_number": "FAC-2026-0900",
            "invoice_date": "2026-10-15",
            "supplier_nif": "001216099123456",
            "supplier_rc": "16/00-0991234B12",
            "supplier_ai": "16019912345",
            "payment_method": "BANK_TRANSFER",
            "amount_ht_dzd": "10000.00",
            "amount_tva_dzd": "1900.00",
            "amount_timbre_dzd": "0.00",
            "amount_ttc_dzd": "11900.00",
            "lines": [
                {
                    "sku": "S1",
                    "description": "Service",
                    "qty": "1.00",
                    "unit_price_dzd": "10000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "10000.00",
                }
            ],
        }
    )
    result = extract_document_ocr(png_bytes, "image/png")
    assert result["engine_name"] == "rapidocr-onnxruntime"
    assert result["raw_lines"], "the OCR engine produced no recognised lines"


# ----------------------------------------------------------------------
# Externally authorized (real-world) dataset interface (protocol §12)
# ----------------------------------------------------------------------

_EXTERNAL_AUTH = {
    "authorized_by": "DPO (test fixture)",
    "authorization_date": "2026-01-31",
    "source_description": "fixture corpus standing in for an authorized supplier set",
    "redaction_status": "supplier names redacted before assembly",
    "access_control": "read restricted to the evaluation service account",
}


def _write_external_manifest(
    tmp_path: Path, documents: list, authorization: dict | None = None
) -> Path:
    manifest = {
        "authorization": dict(authorization if authorization is not None else _EXTERNAL_AUTH),
        "documents": documents,
    }
    manifest_path = tmp_path / "manifest.json"
    import json

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def _external_entry(doc_id: str = "INV-1", path: str = "corpus/INV-1.png") -> dict:
    return {
        "doc_id": doc_id,
        "path": path,
        "mime_type": "image/png",
        "stratum": "supplier_group_a",
        "invoice_number": "FAC-2026-0001",
        "supplier_nif": "001216099123456",
        "supplier_rc": "16/00-0991234B12",
        "supplier_ai": "16019912345",
        "invoice_date": "2026-01-15",
        "amount_ht_dzd": "10000.00",
        "amount_tva_dzd": "1900.00",
        "amount_ttc_dzd": "11900.00",
    }


def test_external_dataset_loader_accepts_a_complete_authorized_manifest(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INV-1.png").write_bytes(b"fake-png")
    manifest = _write_external_manifest(tmp_path, [_external_entry()])

    dataset = load_external_dataset(manifest)
    assert dataset["authorization"]["authorized_by"] == _EXTERNAL_AUTH["authorized_by"]
    assert len(dataset["documents"]) == 1
    resolved = dataset["documents"][0]
    assert resolved["doc_id"] == "INV-1"
    assert resolved["path"] == str((corpus / "INV-1.png").resolve())
    assert resolved["invoice_number"] == "FAC-2026-0001"


def test_external_dataset_loader_refuses_missing_or_invalid_manifests(tmp_path: Path) -> None:
    with pytest.raises(ExternalDatasetError, match="manifest not found"):
        load_external_dataset(tmp_path / "absent.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExternalDatasetError, match="not valid JSON"):
        load_external_dataset(broken)

    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(ExternalDatasetError, match="JSON object"):
        load_external_dataset(empty)

    bad_date = _write_external_manifest(
        tmp_path, [_external_entry()],
        {**_EXTERNAL_AUTH, "authorization_date": "31/01/2026"},
    )
    with pytest.raises(ExternalDatasetError, match="ISO date"):
        load_external_dataset(bad_date)


@pytest.mark.parametrize(
    "field_name",
    ["authorized_by", "authorization_date", "source_description", "redaction_status", "access_control"],
)
def test_external_dataset_requires_every_authorization_field(tmp_path: Path, field_name: str) -> None:
    """A corpus that cannot state its custody must not be evaluated."""
    authorization = dict(_EXTERNAL_AUTH)
    del authorization[field_name]
    manifest = _write_external_manifest(tmp_path, [_external_entry()], authorization)
    with pytest.raises(ExternalDatasetError, match=field_name):
        load_external_dataset(manifest)


@pytest.mark.parametrize(
    "field_name", ["invoice_number", "supplier_nif", "amount_ttc_dzd", "invoice_date"]
)
def test_external_dataset_requires_ground_truth_for_every_document(tmp_path: Path, field_name: str) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INV-1.png").write_bytes(b"fake-png")
    entry = _external_entry()
    del entry[field_name]
    manifest = _write_external_manifest(tmp_path, [entry])
    with pytest.raises(ExternalDatasetError, match=field_name):
        load_external_dataset(manifest)


def test_external_dataset_refuses_paths_that_escape_the_manifest_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"fake")
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "INV-1.png").write_bytes(b"fake")

    for bad_path in ("../outside.png", "secret/../../outside.png", "corpus/missing.png", str(outside)):
        manifest = _write_external_manifest(tmp_path, [_external_entry(path=bad_path)])
        with pytest.raises(ExternalDatasetError, match="manifest directory"):
            load_external_dataset(manifest)


def test_external_dataset_refuses_duplicate_ids_unsupported_types_and_empty_lists(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "INV-1.png").write_bytes(b"fake")

    duplicate = _write_external_manifest(tmp_path, [_external_entry(), _external_entry("INV-1")])
    with pytest.raises(ExternalDatasetError, match="duplicate doc_id"):
        load_external_dataset(duplicate)

    bad_mime = _write_external_manifest(
        tmp_path, [_external_entry(path="corpus/INV-1.png", doc_id="A")]
    )
    import json as _json

    payload = _json.loads(bad_mime.read_text(encoding="utf-8"))
    payload["documents"][0]["mime_type"] = "image/tiff"
    bad_mime.write_text(_json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExternalDatasetError, match="mime_type"):
        load_external_dataset(bad_mime)

    empty_docs = tmp_path / "empty_docs.json"
    empty_docs.write_text(_json.dumps({"authorization": _EXTERNAL_AUTH, "documents": []}), encoding="utf-8")
    with pytest.raises(ExternalDatasetError, match="non-empty 'documents'"):
        load_external_dataset(empty_docs)


def test_external_dataset_evaluation_runs_the_real_engine_and_carries_the_authorization(
    tmp_path: Path,
) -> None:
    """The loader is not decoration: an authorized corpus scores end-to-end.

    The documents here are synthetic stand-ins; what is under test is the
    *pipeline* - manifest custody statement in, per-field scores out, engine
    identical to the synthetic benchmark's.
    """
    import json

    from dz_erp.benchmark import render_synthetic_invoice_image

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    spec = {
        "supplier_name": "Supplier Alpha",
        "invoice_number": "FAC-2026-0001",
        "invoice_date": "2026-01-15",
        "supplier_nif": "001216099123456",
        "supplier_rc": "16/00-0991234B12",
        "supplier_ai": "16019912345",
        "payment_method": "BANK_TRANSFER",
        "amount_ht_dzd": "10000.00",
        "amount_tva_dzd": "1900.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "11900.00",
        "lines": [
            {"sku": "S1", "description": "Service", "qty": "1.00",
             "unit_price_dzd": "10000.00", "tax_rate": "19.0", "line_ht_dzd": "10000.00"}
        ],
    }
    (corpus / "INV-1.png").write_bytes(render_synthetic_invoice_image(spec))
    spec2 = dict(spec, invoice_number="FAC-2026-0002")
    (corpus / "INV-2.png").write_bytes(render_synthetic_invoice_image(spec2))

    entry = _external_entry()
    entry2 = _external_entry("INV-2", "corpus/INV-2.png")
    entry2["invoice_number"] = "FAC-2026-0002"
    manifest = _write_external_manifest(tmp_path, [entry, entry2])

    report = evaluate_external_dataset(manifest, tmp_path / "results")
    assert report["dataset_size_documents"] == 2
    assert report["authorization"] == _EXTERNAL_AUTH
    total = sum(f["total"] for f in report["by_field"].values())
    assert total == 16  # 2 documents x 8 tracked fields
    accounted = total - sum(
        f["exact_match"] for f in report["by_field"].values()
    )
    failures = report["failure_categories"]
    assert failures["missing"] + failures["mismatch"] == accounted
    assert 0.0 <= report["overall_field_exact_match_rate"] <= 1.0
    assert "human review" in report["human_approval_note"].lower()
    assert (tmp_path / "results" / "external_dataset_results.json").exists()
    on_disk = json.loads(
        (tmp_path / "results" / "external_dataset_results.json").read_text(encoding="utf-8")
    )
    assert on_disk["authorization"] == _EXTERNAL_AUTH


# ----------------------------------------------------------------------
# Reproducibility: the benchmark is a measurement instrument
# ----------------------------------------------------------------------
def test_benchmark_report_is_reproducible_from_a_clean_output_directory(tmp_path: Path) -> None:
    """Two clean-directory runs must agree on every non-latency report key.

    The harness is deterministic by construction (pinned threads, vendored
    font, fixed seeds nowhere relied upon); this test *observes* it. Latency
    keys are excluded because wall-clock time is a property of the host, not
    of the pipeline - exactly which keys may vary is itself pinned here, so a
    future code change cannot quietly make accuracy host-dependent.
    """
    run_a = run_empirical_ocr_benchmark(tmp_path / "run_a")
    run_b = run_empirical_ocr_benchmark(tmp_path / "run_b")

    def strip_latency(report: Dict[str, Any]) -> Dict[str, Any]:
        trimmed = {k: v for k, v in report.items() if not k.startswith("latency_")}
        trimmed["by_stratum"] = {
            name: {k: v for k, v in stats.items() if k != "mean_latency_ms"}
            for name, stats in report["by_stratum"].items()
        }
        trimmed["by_language"] = {
            name: {k: v for k, v in stats.items() if k != "mean_latency_ms"}
            for name, stats in report["by_language"].items()
        }
        return trimmed

    assert strip_latency(run_a) == strip_latency(run_b)
    # The stripped keys are exactly the timing set: nothing else may drift.
    assert {"latency_mean_ms", "latency_p50_ms", "latency_max_ms"} <= set(run_a)
    assert all("mean_latency_ms" in stats for stats in run_a["by_stratum"].values())
