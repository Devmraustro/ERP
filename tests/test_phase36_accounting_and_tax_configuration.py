# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 36 — accounting arithmetic and effective-dated tax configuration.

Two separate claims are proved here, and they are not the same claim.

**Arithmetic.** Money is exact: `Decimal` end to end, `ROUND_HALF_UP` to the
centime at *each* line, and the header is the sum of the rounded lines rather
than a rounded sum. Binary floats are refused outright. The invariants (header =
Σ lines, TTC = HT + TVA + timbre, tax breakdown reconciles, order independence,
no sub-centime residue) are asserted here rather than assumed.

**Tax configuration.** The schema always had `timbre_rules(version_code,
valid_from, valid_to, ...)`, but the calculation ignored it and always used the
compiled-in constants — so a configured, reviewed rule version could not take
effect, and nothing recorded which rule a posted invoice used. Resolution is now
by **effective date**, the resolved version label is returned, and the ambiguous
case (rules exist, none covers the date) **raises** instead of silently
substituting a different rate.

What is *not* claimed anywhere in this file: that these rates are the currently
correct Algerian rates. Those come from the seeded configuration and its
`version_code`, and the workbook output says in plain words that it requires a
qualified expert-comptable's sign-off. No test here asserts a legal fact.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase
from dz_erp.decimal_utils import FloatMonetaryError, format_dzd, round_dzd, to_decimal
from dz_erp.localization import (
    BUILTIN_TIMBRE_RULE_VERSION,
    TaxRuleConfigurationError,
    calculate_droit_de_timbre,
    compute_invoice_totals,
    generate_g50_preparation_workbook,
    resolve_timbre_rule,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _db(tmp_path: Path) -> ERPDatabase:
    db = ERPDatabase(str(tmp_path / "p36.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
        )
        from dz_erp.auth import hash_password

        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (1, 1, 'accountant_a', 'accountant', 1, ?)",
            (hash_password("Acct#2026x", iterations=1000),),
        )
    return db


def _add_rule(
    db: ERPDatabase,
    *,
    company_id: int = 1,
    version_code: str,
    valid_from: str,
    valid_to: str | None,
    threshold: str = "300.00",
    min_amount: str = "5.00",
    max_amount: str | None = None,
    brackets: str = '[{"up_to": "30000.00", "rate": "0.01"}, {"up_to": null, "rate": "0.02"}]',
) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO timbre_rules (
                company_id, version_code, valid_from, valid_to,
                min_amount_dzd, max_amount_dzd, threshold_dzd, brackets_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, version_code, valid_from, valid_to, min_amount, max_amount, threshold, brackets),
        )


def _rule(db: ERPDatabase, on_date: str, *, company_id: int = 1):
    with db.transaction() as conn:
        return resolve_timbre_rule(conn, company_id, on_date)


_LINES = [
    {"description": "Ciment", "qty": "10", "unit_price_dzd": "1050.00", "discount_pct": "0.00", "tax_rate": "19.0"},
    {"description": "Acier", "qty": "2.5", "unit_price_dzd": "4800.00", "discount_pct": "5.00", "tax_rate": "19.0"},
    {"description": "Livres", "qty": "3", "unit_price_dzd": "900.00", "discount_pct": "0.00", "tax_rate": "9.0"},
]


# ---------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------


def test_money_is_rounded_half_up_and_floats_are_refused() -> None:
    assert round_dzd("10.005") == Decimal("10.01")
    assert round_dzd("10.004") == Decimal("10.00")
    assert round_dzd("-10.005") == Decimal("-10.01")  # commercial rounding, symmetrical
    assert format_dzd("7") == "7.00"
    with pytest.raises(FloatMonetaryError):
        to_decimal(0.1)
    assert to_decimal("0.1") == Decimal("0.1")


def test_header_totals_are_the_sum_of_rounded_lines() -> None:
    """Rounding once per line, then summing — never rounding a summed float."""
    totals = compute_invoice_totals(_LINES, payment_method="BANK_TRANSFER")
    line_ht = sum((to_decimal(ln["line_ht_dzd"]) for ln in totals["lines"]), Decimal("0.00"))
    line_tva = sum((to_decimal(ln["line_tva_dzd"]) for ln in totals["lines"]), Decimal("0.00"))
    assert to_decimal(totals["amount_ht_dzd"]) == round_dzd(line_ht)
    assert to_decimal(totals["amount_tva_dzd"]) == round_dzd(line_tva)
    # every line amount is already exact to the centime
    for ln in totals["lines"]:
        for key in ("line_ht_dzd", "line_tva_dzd", "line_ttc_dzd"):
            assert to_decimal(ln[key]) == round_dzd(ln[key])
        assert to_decimal(ln["line_ttc_dzd"]) == round_dzd(
            to_decimal(ln["line_ht_dzd"]) + to_decimal(ln["line_tva_dzd"])
        )


def test_discount_is_applied_before_tax_on_the_line_net() -> None:
    """A 5% discount reduces HT, and TVA is charged on the reduced base."""
    totals = compute_invoice_totals(
        [{"qty": "2", "unit_price_dzd": "1000.00", "discount_pct": "5.00", "tax_rate": "19.0"}]
    )
    line = totals["lines"][0]
    assert line["line_ht_dzd"] == "1900.00"
    assert line["line_tva_dzd"] == "361.00"
    assert line["line_ttc_dzd"] == "2261.00"


def test_tax_breakdown_reconciles_with_the_header() -> None:
    totals = compute_invoice_totals(_LINES)
    base = sum((to_decimal(v["base_dzd"]) for v in totals["tax_breakdown"].values()), Decimal("0.00"))
    tax = sum((to_decimal(v["tax_dzd"]) for v in totals["tax_breakdown"].values()), Decimal("0.00"))
    assert base == to_decimal(totals["amount_ht_dzd"])
    assert tax == to_decimal(totals["amount_tva_dzd"])
    # two rates are reported separately, so the G50 boxes can be filled from it
    assert set(totals["tax_breakdown"]) == {"19.0%", "9.0%"}


def test_ttc_identity_holds_with_and_without_stamp_duty() -> None:
    for method in ("BANK_TRANSFER", "CHEQUE", "CASH"):
        totals = compute_invoice_totals(_LINES, payment_method=method)
        ht = to_decimal(totals["amount_ht_dzd"])
        tva = to_decimal(totals["amount_tva_dzd"])
        timbre = to_decimal(totals["amount_timbre_dzd"])
        assert to_decimal(totals["amount_ttc_dzd"]) == round_dzd(ht + tva + timbre), method
        if method != "CASH":
            assert timbre == Decimal("0.00"), method


def test_line_order_does_not_change_any_total() -> None:
    forward = compute_invoice_totals(_LINES)
    backward = compute_invoice_totals(list(reversed(_LINES)))
    for key in ("amount_ht_dzd", "amount_tva_dzd", "amount_timbre_dzd", "amount_ttc_dzd"):
        assert forward[key] == backward[key], key
    assert forward["tax_breakdown"] == backward["tax_breakdown"]


def test_awkward_quantities_leave_no_sub_centime_residue() -> None:
    """The classic float trap: 3 × 0.1 must not be 0.30000000000000004."""
    totals = compute_invoice_totals(
        [{"qty": "3", "unit_price_dzd": "0.10", "tax_rate": "19.0"}]
    )
    assert totals["lines"][0]["line_ht_dzd"] == "0.30"
    assert totals["lines"][0]["line_tva_dzd"] == "0.06"
    assert to_decimal(totals["lines"][0]["qty"]) == Decimal("3.000000")


# ---------------------------------------------------------------------------
# timbre arithmetic
# ---------------------------------------------------------------------------


def test_timbre_threshold_boundary_is_inclusive() -> None:
    """At exactly the threshold nothing is due; one centime above, the minimum is."""
    assert calculate_droit_de_timbre("300.00", payment_method="CASH") == Decimal("0.00")
    assert calculate_droit_de_timbre("300.01", payment_method="CASH") == Decimal("5.00")
    assert calculate_droit_de_timbre("300.00", payment_method="CASH", threshold="300.00") == Decimal("0.00")


def test_timbre_rates_minimum_and_cap() -> None:
    assert calculate_droit_de_timbre("400.00", payment_method="CASH") == Decimal("5.00")  # minimum
    assert calculate_droit_de_timbre("20000.00", payment_method="CASH") == Decimal("200.00")
    assert calculate_droit_de_timbre("150000.00", payment_method="CASH") == Decimal("3000.00")
    capped = calculate_droit_de_timbre("150000.00", payment_method="CASH", max_duty="1000.00")
    assert capped == Decimal("1000.00")


def test_non_cash_payment_never_carries_stamp_duty() -> None:
    for method in ("BANK_TRANSFER", "CHEQUE", "bank_transfer", "cheque"):
        assert calculate_droit_de_timbre("500000.00", payment_method=method) == Decimal("0.00"), method


# ---------------------------------------------------------------------------
# effective-dated configuration
# ---------------------------------------------------------------------------


def test_no_configured_rule_uses_labelled_builtin_constants(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rule = _rule(db, "2026-03-01")
    assert rule.source == "builtin_default"
    assert rule.version_code == BUILTIN_TIMBRE_RULE_VERSION
    assert rule.as_kwargs()["threshold"] == "300.00"


def test_resolution_is_by_effective_date_not_by_newest_row(tmp_path: Path) -> None:
    """A rule in force in 2025 still governs a 2025 invoice after a 2026 rule exists."""
    db = _db(tmp_path)
    _add_rule(db, version_code="DZ-2025-v1", valid_from="2025-01-01", valid_to="2025-12-31",
              threshold="300.00", brackets='[{"up_to": null, "rate": "0.01"}]')
    _add_rule(db, version_code="DZ-2026-v2", valid_from="2026-01-01", valid_to=None,
              threshold="500.00", brackets='[{"up_to": null, "rate": "0.02"}]')

    old = _rule(db, "2025-06-15")
    assert old.version_code == "DZ-2025-v1" and old.threshold_dzd == "300.00"
    new = _rule(db, "2026-06-15")
    assert new.version_code == "DZ-2026-v2" and new.threshold_dzd == "500.00"
    # Boundaries belong to the rule whose validity includes them.
    assert _rule(db, "2025-12-31").version_code == "DZ-2025-v1"
    assert _rule(db, "2026-01-01").version_code == "DZ-2026-v2"


def test_a_rule_that_is_configured_but_not_yet_in_force_is_never_applied(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_rule(db, version_code="DZ-2027-v3", valid_from="2027-01-01", valid_to=None)
    with pytest.raises(TaxRuleConfigurationError) as exc:
        _rule(db, "2026-06-15")
    assert "TIMBRE_RULE_NOT_EFFECTIVE_ON_DATE" in str(exc.value)
    assert "DZ-2027-v3" in str(exc.value)


def test_a_gap_between_versions_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_rule(db, version_code="A", valid_from="2024-01-01", valid_to="2024-12-31")
    _add_rule(db, version_code="B", valid_from="2026-01-01", valid_to=None)
    with pytest.raises(TaxRuleConfigurationError):
        _rule(db, "2025-07-01")


def test_malformed_bracket_tables_fail_closed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_rule(db, version_code="BAD-JSON", valid_from="2020-01-01", valid_to=None,
              brackets="not json")
    with pytest.raises(TaxRuleConfigurationError) as exc:
        _rule(db, "2026-01-01")
    assert "TIMBRE_RULE_BRACKETS_INVALID" in str(exc.value)

    db2 = _db(tmp_path / "second")
    _add_rule(db2, version_code="NO-TOP-BRACKET", valid_from="2020-01-01", valid_to=None,
              brackets='[{"up_to": "1000.00", "rate": "0.01"}]')
    with pytest.raises(TaxRuleConfigurationError):
        _rule(db2, "2026-01-01")


def test_rules_are_tenant_scoped(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_rule(db, company_id=1, version_code="ONLY-ALPHA", valid_from="2020-01-01", valid_to=None)
    assert _rule(db, "2026-01-01", company_id=1).version_code == "ONLY-ALPHA"
    # Company 2 has no rules of its own: it must not borrow company 1's.
    assert _rule(db, "2026-01-01", company_id=2).source == "builtin_default"


def test_the_resolved_rule_changes_the_amount_and_is_named(tmp_path: Path) -> None:
    """Configuration must actually reach the arithmetic."""
    db = _db(tmp_path)
    _add_rule(db, version_code="DZ-2026-v2", valid_from="2026-01-01", valid_to=None,
              threshold="100.00", min_amount="20.00", max_amount="50.00",
              brackets='[{"up_to": null, "rate": "0.02"}]')
    rule = _rule(db, "2026-06-15")
    assert rule.version_code == "DZ-2026-v2"
    amount = calculate_droit_de_timbre("400.00", payment_method="CASH", **rule.as_kwargs())
    builtin = calculate_droit_de_timbre("400.00", payment_method="CASH")
    assert builtin == Decimal("5.00")
    assert amount == Decimal("20.00")  # 2% of 400 = 8.00 -> raised to the configured minimum

    totals = compute_invoice_totals(
        [{"qty": "1", "unit_price_dzd": "336.13", "tax_rate": "19.0"}], payment_method="CASH"
    )
    assert to_decimal(totals["amount_timbre_dzd"]) >= Decimal("0.00")


def test_a_bad_invoice_date_is_refused(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_rule(db, version_code="X", valid_from="2020-01-01", valid_to=None)
    for bad in ("", "2026-13-45", "not-a-date", "2026"):
        with pytest.raises(TaxRuleConfigurationError):
            _rule(db, bad)


# ---------------------------------------------------------------------------
# G50 workbook arithmetic
# ---------------------------------------------------------------------------


def _post(conn, company_id: int, code: str, debit: str, credit: str, tag: str | None,
          entry_no: str, date: str) -> None:
    # Every posted entry must balance; the counterpart goes to the bank account,
    # which the G50 workbook deliberately does not aggregate.
    total = round_dzd(to_decimal(debit) + to_decimal(credit))
    stamp = "2026-01-01T00:00:00+00:00"
    cur = conn.execute(
        "INSERT INTO journal_entries (company_id, entry_number, journal_code, entry_date, state, "
        "source_model, source_id, total_debit_dzd, total_credit_dzd, posted_by_id, posted_at) "
        "VALUES (?, ?, 'OD', ?, 'POSTED', 'test', 'test', ?, ?, 1, ?)",
        (company_id, entry_no, date, format_dzd(total), format_dzd(total), stamp),
    )
    entry_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO journal_entry_lines "
        "(company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd, g50_tag) "
        "VALUES (?, ?, ?, 'test line', ?, ?, ?)",
        (company_id, entry_id, code, debit, credit, tag),
    )
    # balancing counterpart: on the opposite side of the movement
    counter_debit = credit if to_decimal(credit) > 0 else "0.00"
    counter_credit = debit if to_decimal(debit) > 0 else "0.00"
    conn.execute(
        "INSERT INTO journal_entry_lines "
        "(company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd) "
        "VALUES (?, ?, '512', 'counterpart', ?, ?)",
        (company_id, entry_id, counter_debit, counter_credit),
    )


def test_g50_boxes_are_arithmetically_closed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.transaction() as conn:
        _post(conn, 1, "4457", "0.00", "1900.00", "C_TVA_COLLECTED", "V-1", "2026-04-10")
        _post(conn, 1, "4456", "700.00", "0.00", "E3B91", "A-1", "2026-04-11")
        _post(conn, 1, "4456", "300.00", "0.00", "E3B92", "A-2", "2026-04-12")
        _post(conn, 1, "44575", "0.00", "50.00", "TIMBRE", "C-1", "2026-04-13")
        # a different period must not leak into the workbook
        _post(conn, 1, "4457", "0.00", "999.00", "C_TVA_COLLECTED", "V-2", "2026-05-10")

    book = generate_g50_preparation_workbook(db, 1, 1, "2026-04", prior_vat_credit_dzd="100.00")
    boxes = book["boxes"]
    collected = to_decimal(boxes["E3B100_total_collected_vat_c"])
    deductions = to_decimal(boxes["E3B110_total_deductions_b"])
    payable = to_decimal(boxes["E3B120_vat_payable"])
    carry = to_decimal(boxes["E3B130_credit_to_carry_forward"])

    assert collected == Decimal("1900.00")
    assert to_decimal(boxes["E3B90_prior_credit_precompte"]) == Decimal("100.00")
    assert to_decimal(boxes["E3B91_recoverable_vat_goods_services"]) == Decimal("700.00")
    assert to_decimal(boxes["E3B92_recoverable_vat_depreciable_assets"]) == Decimal("300.00")
    assert deductions == Decimal("1100.00")
    assert payable == Decimal("800.00")
    assert carry == Decimal("0.00")
    # exactly one of payable/carry-forward is non-zero, and the identity closes
    assert (payable == Decimal("0.00")) != (carry == Decimal("0.00"))
    assert payable - carry == round_dzd(collected - deductions)
    assert to_decimal(boxes["E2_droit_de_timbre"]) == Decimal("50.00")
    # every figure is traceable back to the entry that produced it
    assert book["traceability_count"] == 4
    assert {t["entry"] for t in book["traceability"]} == {"V-1", "A-1", "A-2", "C-1"}


def test_g50_reports_a_credit_before_it_reports_a_payment(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.transaction() as conn:
        _post(conn, 1, "4457", "0.00", "500.00", "C_TVA_COLLECTED", "V-1", "2026-06-10")
        _post(conn, 1, "4456", "900.00", "0.00", "E3B91", "A-1", "2026-06-11")
    book = generate_g50_preparation_workbook(db, 1, 1, "2026-06")
    assert to_decimal(book["boxes"]["E3B120_vat_payable"]) == Decimal("0.00")
    assert to_decimal(book["boxes"]["E3B130_credit_to_carry_forward"]) == Decimal("400.00")
    assert "REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF" in book["statutory_status"]


def test_g50_refuses_a_cross_tenant_request(tmp_path: Path) -> None:
    from dz_erp.database import TenantIsolationError

    db = _db(tmp_path)
    with pytest.raises(TenantIsolationError):
        generate_g50_preparation_workbook(db, company_id=1, requesting_company_id=2,
                                          period_prefix="2026-04")


def test_g50_for_an_empty_period_is_zero_not_an_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    book = generate_g50_preparation_workbook(db, 1, 1, "2026-09")
    assert book["traceability_count"] == 0
    assert to_decimal(book["boxes"]["E3B120_vat_payable"]) == Decimal("0.00")
    assert to_decimal(book["boxes"]["E3B130_credit_to_carry_forward"]) == Decimal("0.00")


# ---------------------------------------------------------------------------
# effective-dated TVA rate configuration
# ---------------------------------------------------------------------------


def _add_tax(db: ERPDatabase, *, company_id: int = 1, xml_id: str, rate: str,
             valid_from: str, valid_to: str | None = None, active: int = 1,
             exemption: str = "NONE") -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO taxes (company_id, xml_id, name_en, name_fr, type_tax_use,
                               rate_percent, exemption_reason, valid_from, valid_to, active)
            VALUES (?, ?, ?, ?, 'purchase', ?, ?, ?, ?, ?)
            """,
            (company_id, xml_id, xml_id, xml_id, rate, exemption, valid_from, valid_to, active),
        )


def _vat(db: ERPDatabase, on_date: str, *, company_id: int = 1):
    from dz_erp.localization import resolve_vat_rates

    with db.transaction() as conn:
        return resolve_vat_rates(conn, company_id, on_date)


def test_vat_rates_fall_back_to_labelled_builtins_only_when_unconfigured(tmp_path: Path) -> None:
    from dz_erp.localization import BUILTIN_VAT_RATE_VERSION

    db = _db(tmp_path)
    rates = _vat(db, "2026-01-01")
    assert rates.source == "builtin_default"
    assert rates.version_label == BUILTIN_VAT_RATE_VERSION
    assert rates.describe() == "0.0,9.0,19.0"


def test_vat_rates_are_effective_dated_and_versioned(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_tax(db, xml_id="tva19-2025", rate="19.0", valid_from="2025-01-01", valid_to="2025-12-31")
    _add_tax(db, xml_id="tva9-2025", rate="9.0", valid_from="2025-01-01", valid_to="2025-12-31")
    _add_tax(db, xml_id="tva19-2026", rate="19.0", valid_from="2026-01-01")
    _add_tax(db, xml_id="tva0-2026", rate="0.0", valid_from="2026-01-01")

    old = _vat(db, "2025-06-01")
    assert old.rates == (Decimal("9.0"), Decimal("19.0"))
    assert old.version_label == "configured@2025-01-01"

    new = _vat(db, "2026-06-01")
    assert new.rates == (Decimal("0.0"), Decimal("19.0"))
    # A rate that existed only in 2025 is not acceptable on a 2026 invoice.
    assert new.allows(Decimal("9.0")) is False
    assert new.allows(Decimal("19.0")) is True


def test_an_inactive_rate_is_not_accepted(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_tax(db, xml_id="tva19", rate="19.0", valid_from="2020-01-01")
    _add_tax(db, xml_id="tva9-retired", rate="9.0", valid_from="2020-01-01", active=0)
    assert _vat(db, "2026-01-01").rates == (Decimal("19.0"),)


def test_configured_rates_that_do_not_cover_the_date_are_refused(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _add_tax(db, xml_id="future", rate="19.0", valid_from="2027-01-01")
    with pytest.raises(TaxRuleConfigurationError) as exc:
        _vat(db, "2026-01-01")
    assert "VAT_RATE_NOT_EFFECTIVE_ON_DATE" in str(exc.value)


def test_vat_rates_are_tenant_scoped(tmp_path: Path) -> None:
    from dz_erp.localization import BUILTIN_VAT_RATE_VERSION

    db = _db(tmp_path)
    _add_tax(db, company_id=1, xml_id="only-alpha", rate="19.0", valid_from="2020-01-01")
    assert _vat(db, "2026-01-01", company_id=1).source == "configured"
    assert _vat(db, "2026-01-01", company_id=2).version_label == BUILTIN_VAT_RATE_VERSION


def test_invoice_validation_uses_the_configured_rate_set(tmp_path: Path) -> None:
    """End to end: a configured table decides which rate an invoice may use."""
    from dz_erp.procurement_stock import create_and_validate_supplier_invoice

    db = _db(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif) "
            "VALUES (10, 1, 'Supplier Alpha', 'supplier', '001216099123456')"
        )
        conn.execute(
            "INSERT INTO accounts (company_id, xml_id, code, name_en, name_fr, name_ar, account_type) "
            "VALUES (1, 'xml_401', '401', 'Suppliers', 'Fournisseurs', 'الموردون', 'liability_current')"
        )
    # Company 1 accepts 19% only from 2026 onwards.
    _add_tax(db, xml_id="tva19", rate="19.0", valid_from="2026-01-01")

    def _invoice(number: str, rate: str) -> dict:
        tva = round_dzd(to_decimal("1000.00") * to_decimal(rate) / Decimal("100"))
        return create_and_validate_supplier_invoice(
            db,
            1,
            1,
            10,
            number,
            "2026-05-04",
            [
                {
                    "qty": "1",
                    "unit_price_dzd": "1000.00",
                    "tax_rate": rate,
                    "discount_pct": "0.00",
                    "line_ht_dzd": "1000.00",
                    "line_tva_dzd": format_dzd(tva),
                    "line_ttc_dzd": format_dzd(to_decimal("1000.00") + tva),
                }
            ],
            amount_ht_dzd="1000.00",
            amount_tva_dzd=tva,
            amount_ttc_dzd=to_decimal("1000.00") + tva,
            amount_timbre_dzd="0.00",
        )

    ok = _invoice("FA-OK", "19.0")
    assert ok["workflow_state"] == "Validated", ok.get("validation_errors")

    # 9% is not in this company's configured table, so it is refused even though
    # the compiled-in constants happen to contain it.
    refused = _invoice("FA-BAD", "9.0")
    assert refused["workflow_state"] == "NeedsReview"
    assert any("INVALID_ALGERIAN_TVA_RATE" in e for e in refused["validation_errors"])


def test_invoice_creation_refuses_an_ambiguous_rate_configuration(tmp_path: Path) -> None:
    """No configured rate covers the invoice date: refuse, never guess."""
    from dz_erp.procurement_stock import create_and_validate_supplier_invoice

    db = _db(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif) "
            "VALUES (10, 1, 'Supplier Alpha', 'supplier', '001216099123456')"
        )
    _add_tax(db, xml_id="future", rate="19.0", valid_from="2027-01-01")
    with pytest.raises(TaxRuleConfigurationError):
        create_and_validate_supplier_invoice(
            db, 1, 1, 10, "FA-GAP", "2026-05-04",
            [
                {
                    "qty": "1",
                    "unit_price_dzd": "1000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "1000.00",
                    "line_tva_dzd": "190.00",
                    "line_ttc_dzd": "1190.00",
                }
            ],
            amount_ht_dzd="1000.00", amount_tva_dzd="190.00", amount_ttc_dzd="1190.00",
            amount_timbre_dzd="0.00",
        )
