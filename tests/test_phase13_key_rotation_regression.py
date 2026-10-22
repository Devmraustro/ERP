# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 13 — Security and business-invariant regression guard for key rotation.

Key management is easy to get right in isolation and wrong in context: a
rotation that quietly changes a posted journal entry, drops an approval, exposes
an administrative endpoint, or writes a secret into the security log would pass
every unit test in the two preceding files. This module checks the invariants
that must survive a rotation, against the current code rather than against a
checklist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.benchmark import render_synthetic_invoice_pdf
from dz_erp.database import AuthorizationError, ERPDatabase, FinancialIntegrityError
from dz_erp.intake_ocr import VAULT_KEY_SUFFIX, PrivateDocumentVault
from dz_erp.localization import load_official_odoo_chart
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    decide_invoice_approval,
    post_supplier_invoice,
    submit_invoice_for_approval,
)
from dz_erp.security_audit import JsonlSecurityLog, StructuredRedactingLogger


INVOICE_SPEC = {
    "supplier_name": "Synthetic Supplier",
    "invoice_number": "FAC-ROT-001",
    "invoice_date": "2026-10-16",
    "supplier_nif": "001216099123456",
    "supplier_rc": "16/00-0991234B12",
    "supplier_ai": "16019912345",
    "payment_method": "BANK_TRANSFER",
    "amount_ht_dzd": "20000.00",
    "amount_tva_dzd": "3800.00",
    "amount_timbre_dzd": "0.00",
    "amount_ttc_dzd": "23800.00",
    "lines": [
        {
            "sku": "S1",
            "description": "Consulting",
            "qty": "1.00",
            "unit_price_dzd": "20000.00",
            "tax_rate": "19.0",
            "line_ht_dzd": "20000.00",
        }
    ],
}


def _deployment(tmp_path: Path) -> tuple:
    """Database + vault + one posted, approved invoice, using synthetic data only."""
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic Company', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, username, role in (
            (1, "clerk", "clerk"),
            (3, "approver", "approver"),
            (4, "accountant", "accountant"),
        ):
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, 1, ?, ?, 1, ?)",
                (uid, username, role, hash_password(f"{username}#2026x", iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc)"
            " VALUES (10, 1, 'Synthetic Supplier', 'supplier', '001216099123456',"
            " '099116010123456', '16019912345', '16/00-0991234B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)

    vault = PrivateDocumentVault(tmp_path / "vault")
    from dz_erp.intake_ocr import upload_and_process_supplier_document

    document = upload_and_process_supplier_document(
        db,
        vault,
        company_id=1,
        user_id=1,
        filename="synthetic-rot.pdf",
        mime_type="application/pdf",
        content=render_synthetic_invoice_pdf(INVOICE_SPEC),
    )
    invoice = create_and_validate_supplier_invoice(
        db,
        company_id=1,
        user_id=1,
        supplier_id=10,
        supplier_invoice_no="FAC-ROT-001",
        invoice_date="2026-10-16",
        document_id=document["document_id"],
        amount_ht_dzd="20000.00",
        amount_tva_dzd="3800.00",
        amount_ttc_dzd="23800.00",
        lines=[
            {
                "description": "Consulting",
                "qty": "1",
                "unit_price_dzd": "20000.00",
                "tax_rate": "19.0",
                "line_ht_dzd": "20000.00",
            }
        ],
    )
    submit_invoice_for_approval(db, company_id=1, user_id=1, invoice_id=invoice["invoice_id"])
    decide_invoice_approval(db, company_id=1, approver_user_id=3, invoice_id=invoice["invoice_id"], approve=True)
    posted = post_supplier_invoice(
        db, company_id=1, accountant_user_id=4, invoice_id=invoice["invoice_id"], idempotency_key="rot-post-1"
    )
    return db, vault, document, invoice, posted


def _journal_state(db: ERPDatabase) -> list:
    with db.transaction() as conn:
        entries = [
            dict(r) for r in conn.execute("SELECT * FROM journal_entries ORDER BY id").fetchall()
        ]
        lines = [
            dict(r) for r in conn.execute("SELECT * FROM journal_entry_lines ORDER BY id").fetchall()
        ]
    return [{"entries": entries, "lines": lines}]


def test_rotation_preserves_posted_accounting_and_audit_evidence(tmp_path: Path) -> None:
    """The decisive invariant: rotating keys changes no financial fact.

    A key is a physical carrier, not an accounting event. If rotation altered a
    posted journal entry, the trial balance, an approval record, or the audit
    chain, the system would no longer be auditable - so this is asserted on the
    rows themselves, not through an API response.
    """
    db, vault, document, invoice, posted = _deployment(tmp_path)
    assert posted["journal_entry_id"]
    before_journals = _journal_state(db)

    with db.transaction() as conn:
        balance_before = conn.execute(
            """
            SELECT
                SUM(CAST(jel.debit_dzd AS REAL)) AS debit,
                SUM(CAST(jel.credit_dzd AS REAL)) AS credit
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE je.state = 'POSTED'
            """
        ).fetchone()
        approvals_before = [
            dict(r) for r in conn.execute("SELECT * FROM approval_requests ORDER BY id").fetchall()
        ]
        invoice_before = dict(
            conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)).fetchone()
        )
    assert balance_before["debit"] == balance_before["credit"] == 23800.0

    vault_report = vault.rotate_key()
    audit_report = db.rotate_audit_key()

    assert vault_report["verified_ok"] is True
    assert audit_report["chain_valid_before"] is True and audit_report["chain_valid_after"] is True

    # One further event, signed under the new version, so the chain genuinely
    # spans both keys rather than merely having both keys available.
    with db.transaction() as conn:
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=4,
            action_type="POST_ROTATION_PROBE",
            target_model="synthetic",
            target_id="1",
            after_state={"probe": True},
        )

    with db.transaction() as conn:
        balance_after = conn.execute(
            """
            SELECT
                SUM(CAST(jel.debit_dzd AS REAL)) AS debit,
                SUM(CAST(jel.credit_dzd AS REAL)) AS credit
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE je.state = 'POSTED'
            """
        ).fetchone()
        approvals_after = [
            dict(r) for r in conn.execute("SELECT * FROM approval_requests ORDER BY id").fetchall()
        ]
        invoice_after = dict(
            conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (invoice["invoice_id"],)).fetchone()
        )

    assert balance_after["debit"] == balance_after["credit"] == 23800.0
    assert _journal_state(db) == before_journals, "rotation modified journal entries or lines"
    assert approvals_after == approvals_before, "rotation modified approval records"
    assert invoice_after == invoice_before, "rotation modified an invoice"
    assert invoice_after["workflow_state"] == "Posted"

    # The audit chain now spans two key versions and still verifies.
    chain = db.verify_audit_chain(1)
    assert chain["valid"] is True
    assert set(chain["key_versions"]) == {"1", "2"}
    assert chain["key_versions"]["2"] == 1

    # The encrypted source document is still retrievable through the vault.
    raw = vault.read_bytes(1, 1, _doc_path(db, document))
    assert raw.startswith(b"%PDF-")


def _doc_path(db: ERPDatabase, document: dict) -> str:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT vault_path FROM source_documents WHERE id = ?", (document["document_id"],)
        ).fetchone()
    return row["vault_path"]


def test_rotation_does_not_grant_financial_authority(tmp_path: Path) -> None:
    """Rotation must not become a way around role checks or idempotency."""
    db, _vault, _document, invoice, _posted = _deployment(tmp_path)
    db.rotate_audit_key()

    # Re-posting the same invoice is still refused as a duplicate.
    with pytest.raises(FinancialIntegrityError):
        post_supplier_invoice(
            db, company_id=1, accountant_user_id=4, invoice_id=invoice["invoice_id"], idempotency_key="rot-post-2"
        )
    # A non-accountant is still refused.
    with pytest.raises(AuthorizationError):
        post_supplier_invoice(
            db, company_id=1, accountant_user_id=1, invoice_id=invoice["invoice_id"], idempotency_key="rot-post-3"
        )


def test_no_api_route_exposes_key_rotation(tmp_path: Path) -> None:
    """Rotation is an operator action on the host filesystem, not a remote endpoint.

    An HTTP rotation endpoint would let a stolen session token destroy the
    deployment's ability to read its own data. Rotation must therefore stay
    outside the authenticated API surface.
    """
    db = ERPDatabase(str(tmp_path / "api.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    app = create_erp_fastapi_app(db, vault)

    paths = sorted(app.openapi()["paths"])
    assert not [p for p in paths if "rotate" in p.lower() or "key" in p.lower()]
    for route in app.routes:
        name = getattr(route, "path", "")
        assert "rotate" not in name.lower()
        assert "keys" not in name.lower()


def test_api_documentation_is_disabled_by_default(tmp_path: Path) -> None:
    """The secure default must survive this change."""
    db = ERPDatabase(str(tmp_path / "api.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    app = create_erp_fastapi_app(db, vault)
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path

    enabled = create_erp_fastapi_app(db, vault, docs_enabled=True)
    assert TestClient(enabled).get("/openapi.json").status_code == 200


def test_key_material_never_reaches_the_security_log(tmp_path: Path) -> None:
    """The durable security sink must stay free of secrets, including new ones."""
    log_path = tmp_path / "security.jsonl"
    sink = JsonlSecurityLog(log_path)
    logger = StructuredRedactingLogger(sink=sink)

    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC")
    vault.rotate_key()
    db.rotate_audit_key()

    # Even if a caller mistakenly logs the registries, the redaction layer and
    # the rotation journal must not carry material.
    logger.log(
        "INFO",
        "ROTATION_COMPLETE",
        "corr-rot",
        encryption_key=vault.keyring.material_for(2).decode(),
        hmac_secret=db.audit_keyring.material_for(2).hex(),
        detail={"vault_keyring": vault.keyring.to_dict()},
    )

    log_text = log_path.read_text()
    journal_text = vault.rotation_journal_path.read_text()
    for version in (1, 2):
        vault_material = vault.keyring.material_for(version).decode()
        audit_material = db.audit_keyring.material_for(version).hex()
        assert vault_material not in log_text
        assert audit_material not in log_text
        assert vault_material not in journal_text
        assert audit_material not in journal_text
    assert "[REDACTED]" in log_text
    # The audit journal, too, names versions only.
    audit_journal = Path(db.audit_keyring_path).parent / "erp.sqlite3.audit.rotation.jsonl"
    for line in audit_journal.read_text().splitlines():
        assert set(json.loads(line)) <= {
            "timestamp",
            "event",
            "previous_version",
            "target_version",
            "resumed",
            "chain_valid",
        }


def test_rotation_files_are_owner_only(tmp_path: Path, assert_owner_only_permissions) -> None:
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC")
    vault.rotate_key()
    db.rotate_audit_key()

    assert_owner_only_permissions(vault.keyring_path)
    assert_owner_only_permissions(vault.rotation_journal_path)
    assert_owner_only_permissions(Path(db.audit_keyring_path))
    assert_owner_only_permissions(tmp_path / ("vault" + VAULT_KEY_SUFFIX))
    # Registries never sit inside the directory whose contents they protect.
    assert list((tmp_path / "vault").rglob("*.keys.json")) == []
