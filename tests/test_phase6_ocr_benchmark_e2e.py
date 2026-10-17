# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 6 Tests — Empirical Algerian Invoice OCR Benchmark & 14-Step End-to-End Acceptance Suite."""

from pathlib import Path

from dz_erp.benchmark import render_synthetic_invoice_pdf, run_empirical_ocr_benchmark
from dz_erp.database import ERPDatabase
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
    bench_dir = Path("/home/user/ERP/benchmarks/results")
    report = run_empirical_ocr_benchmark(bench_dir)

    assert report["dataset_size_documents"] == 18
    assert report["overall_field_exact_match_rate"] >= 0.90
    assert report["table_line_association_accuracy"] >= 0.85
    assert report["expected_calibration_error_ece"] <= 0.15
    assert (bench_dir / "ground_truth_and_results.json").exists()


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
