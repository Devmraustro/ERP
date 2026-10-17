# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 2 Tests — Algerian SCF Localization, Upstream Odoo/Azinak Suite & Migration Integrity."""

from decimal import Decimal
import pytest

from dz_erp.database import ERPDatabase, FinancialIntegrityError, TenantIsolationError
from dz_erp.decimal_utils import FloatMonetaryError, format_dzd, round_dzd, to_decimal
from dz_erp.localization import (
    ACCOUNT_TYPE_FIXES,
    GROUP_TRANSLATION_FIXES,
    RECONCILIATION_ACCOUNTS,
    apply_algerian_localization_fixes,
    calculate_droit_de_timbre,
    compute_invoice_totals,
    generate_g50_preparation_workbook,
    inspect_upstream_odoo_l10n_dz,
    load_official_odoo_chart,
    validate_algerian_identifiers,
)
from dz_erp.odoo_test_adapter import install_odoo_shim_and_load_modules

# Install Odoo 18 shim so upstream azinak/l10n-dz-account-fix unittest classes run under pytest
install_odoo_shim_and_load_modules()
from l10n_dz_account_fix.tests.test_template_dz import (  # noqa: E402
    TestAlgerianChartTemplateFixes,
    TestAlgerianChartTemplateIntegration,
)


def test_upstream_odoo_18_defects_verified_from_source() -> None:
    """Verify directly from vendored odoo/odoo 18.0 files that reported defects exist in upstream."""
    report = inspect_upstream_odoo_l10n_dz()
    assert report["official_default_receivable_xml_id"] == "l10n_dz_413"
    assert report["total_accounts"] == 294
    assert report["wilaya_count"] == 69

    # Non-cash accounts misclassified as asset_cash in official Odoo 18.0
    for code in ("519", "521", "529", "591", "594"):
        assert report["accounts_by_code"][code]["account_type"] == "asset_cash"

    # Account 481 (Provisions, current liabilities) misclassified as asset_receivable
    assert report["accounts_by_code"]["481"]["account_type"] == "asset_receivable"

    # No Arabic account column in upstream CSV
    assert report["accounts_by_code"]["411"]["has_arabic"] is False

    # Orphaned tax report turnover boxes E3B32 and E3B33
    assert "E3B32" in report["orphaned_turnover_boxes"]
    assert "E3B33" in report["orphaned_turnover_boxes"]

    # French translation defects on groups 32 and 66
    assert report["groups_by_code"]["32"]["name_fr"] == "Pertes de valeur sur Autres approvisionnements"
    assert report["groups_by_code"]["66"]["name_fr"] == "Charges financières différées"


def test_conservative_migration_preserves_customizations_posted_entries_and_archived_state() -> None:
    """Verify conservative migration rules, idempotency, multi-company isolation, and posted JE preservation."""
    db = ERPDatabase(":memory:")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, default_receivable_code, created_at) VALUES (1, 'DZ1', 'SARL Alger', 'DZ', 'dz', '413', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, default_receivable_code, created_at) VALUES (2, 'US1', 'US Corp', 'US', 'generic_coa', '1200', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role) VALUES (1, 1, 'admin_dz', 'admin')"
        )

    # Load unpatched official Odoo 18 chart on Company 1
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=False, actor_user_id=1)

    with db.transaction() as conn:
        # 1. Simulate a historical posted entry on 413 and 519 before migration
        conn.execute(
            """
            INSERT INTO journal_entries (
                id, company_id, entry_number, journal_code, entry_date, state,
                source_model, source_id, total_debit_dzd, total_credit_dzd, posted_by_id, posted_at
            ) VALUES (100, 1, 'HIST/2025/0001', 'OD', '2025-12-15', 'POSTED', 'legacy', '1', '750.00', '750.00', 1, '2025-12-15')
            """
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd) VALUES (1, 100, '413', 'Legacy receivable', '750.00', '0.00')"
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd) VALUES (1, 100, '700', 'Legacy revenue', '0.00', '750.00')"
        )

        # 2. Simulate deliberate user customization on account 509 and archiving of account 594
        conn.execute(
            "UPDATE accounts SET account_type = 'equity', is_custom_type = 1 WHERE company_id = 1 AND code = '509'"
        )
        conn.execute(
            "UPDATE accounts SET active = 0 WHERE company_id = 1 AND code = '594'"
        )

    # Run migration pass 1 on Company 1 and Company 2
    mig1 = apply_algerian_localization_fixes(db, company_id=1, actor_user_id=1)
    mig_us = apply_algerian_localization_fixes(db, company_id=2, actor_user_id=1)

    assert mig1["receivable_updated"] is True
    assert mig_us["skipped_non_dz"] is True

    with db.transaction() as conn:
        c1 = conn.execute("SELECT * FROM companies WHERE id = 1").fetchone()
        c2 = conn.execute("SELECT * FROM companies WHERE id = 2").fetchone()
        assert c1["default_receivable_code"] == "411"
        assert c2["default_receivable_code"] == "1200"

        # User customization on 509 preserved
        a509 = conn.execute("SELECT * FROM accounts WHERE company_id = 1 AND code = '509'").fetchone()
        assert a509["account_type"] == "equity"

        # Archived account 594 has corrected type but remains active == 0
        a594 = conn.execute("SELECT * FROM accounts WHERE company_id = 1 AND code = '594'").fetchone()
        assert a594["account_type"] == "asset_current"
        assert a594["active"] == 0

        # Non-cash accounts no longer asset_cash
        for code, (_, expected_type) in ACCOUNT_TYPE_FIXES.items():
            if code == "509":
                continue
            row = conn.execute("SELECT * FROM accounts WHERE company_id = 1 AND code = ?", (code,)).fetchone()
            assert row["account_type"] == expected_type

        # Reconciliation flags enabled
        for code in RECONCILIATION_ACCOUNTS:
            row = conn.execute("SELECT * FROM accounts WHERE company_id = 1 AND code = ?", (code,)).fetchone()
            assert row["reconcile"] == 1

        # Arabic labels & French group fixes applied
        a411 = conn.execute("SELECT * FROM accounts WHERE company_id = 1 AND code = '411'").fetchone()
        assert a411["name_ar"] == "الزبائن"
        g32 = conn.execute("SELECT * FROM account_groups WHERE company_id = 1 AND code_prefix = '32'").fetchone()
        assert g32["name_fr"] == GROUP_TRANSLATION_FIXES["32"]["corrected_fr"]

        # Historical posted entry is completely untouched
        je = conn.execute("SELECT * FROM journal_entries WHERE id = 100").fetchone()
        jelines = conn.execute("SELECT * FROM journal_entry_lines WHERE journal_entry_id = 100 ORDER BY id").fetchall()
        assert je["state"] == "POSTED"
        assert jelines[0]["account_code"] == "413"
        assert jelines[0]["debit_dzd"] == "750.00"
        assert jelines[1]["credit_dzd"] == "750.00"

    # Run migration pass 2 to prove idempotency (zero new updates)
    mig2 = apply_algerian_localization_fixes(db, company_id=1, actor_user_id=1)
    assert mig2["receivable_updated"] is False
    assert mig2["updated_account_types"] == {}
    assert mig2["updated_reconcile_codes"] == []
    assert db.verify_audit_chain(1)["valid"] is True


def test_decimal_precision_timbre_fiscal_and_g50_workbook() -> None:
    """Verify rejection of binary floats, exact DZD rounding, Droit de Timbre, and G50 workbook aggregation."""
    with pytest.raises(FloatMonetaryError):
        to_decimal(19.99)

    # Bank transfer has 0.00 DZD stamp duty; Cash has graduated stamp duty
    assert calculate_droit_de_timbre("25000.00", payment_method="BANK_TRANSFER") == Decimal("0.00")
    assert calculate_droit_de_timbre("250.00", payment_method="CASH") == Decimal("0.00")  # <= 300 threshold
    assert calculate_droit_de_timbre("400.00", payment_method="CASH") == Decimal("5.00")  # min 5.00 DZD
    assert calculate_droit_de_timbre("20000.00", payment_method="CASH") == Decimal("200.00")  # 1%
    assert calculate_droit_de_timbre("50000.00", payment_method="CASH") == Decimal("750.00")  # 1.5%
    assert calculate_droit_de_timbre("150000.00", payment_method="CASH") == Decimal("3000.00")  # 2%

    totals = compute_invoice_totals(
        lines=[
            {"description": "Laptop", "qty": "2", "unit_price_dzd": "50000.00", "tax_rate": "19.0"},
            {"description": "Book", "qty": "10", "unit_price_dzd": "1000.00", "tax_rate": "9.0"},
        ],
        payment_method="CASH",
    )
    assert totals["amount_ht_dzd"] == "110000.00"
    assert totals["amount_tva_dzd"] == "19900.00"
    # Base for timbre = 129,900.00 DZD (> 100,000 => 2% = 2,598.00 DZD)
    assert totals["amount_timbre_dzd"] == "2598.00"
    assert totals["amount_ttc_dzd"] == "132498.00"

    # Validate Algerian identifier formats
    assert validate_algerian_identifiers(
        nif="001216099123456", nis="099116010123456", ai="16019912345", nin="109901601001234567"
    ) == []
    assert len(validate_algerian_identifiers(nif="123", nis="abc", ai="99")) == 3

    # Test G50 workbook aggregation and tenant isolation
    db = ERPDatabase(":memory:")
    with db.transaction() as conn:
        conn.execute("INSERT INTO companies (id, code, name, created_at) VALUES (1, 'C1', 'Co 1', '2026-01-01')")
        conn.execute("INSERT INTO companies (id, code, name, created_at) VALUES (2, 'C2', 'Co 2', '2026-01-01')")
        conn.execute("INSERT INTO users (id, company_id, username, role) VALUES (1, 1, 'u1', 'accountant')")
        conn.execute(
            """
            INSERT INTO journal_entries (
                id, company_id, entry_number, journal_code, entry_date, state,
                source_model, source_id, total_debit_dzd, total_credit_dzd, posted_by_id, posted_at
            ) VALUES (1, 1, 'VTE/2026/001', 'VTE', '2026-10-05', 'POSTED', 'sale', '1', '119000.00', '119000.00', 1, '2026-10-05')
            """
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd) VALUES (1, 1, '411', 'Client', '119000.00', '0.00')"
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd, g50_tag) VALUES (1, 1, '700', 'Sale', '0.00', '100000.00', 'E3B21_taxable')"
        )
        conn.execute(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd, g50_tag) VALUES (1, 1, '4457', 'TVA 19%', '0.00', '19000.00', 'E3B21_balance')"
        )

    g50 = generate_g50_preparation_workbook(db, company_id=1, requesting_company_id=1, period_prefix="2026-10", prior_vat_credit_dzd="4000.00")
    assert g50["boxes"]["E3B100_total_collected_vat_c"] == "19000.00"
    assert g50["boxes"]["E3B90_prior_credit_precompte"] == "4000.00"
    assert g50["boxes"]["E3B120_vat_payable"] == "15000.00"
    assert "REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF" in g50["statutory_status"]

    with pytest.raises(TenantIsolationError):
        generate_g50_preparation_workbook(db, company_id=1, requesting_company_id=2, period_prefix="2026-10")
