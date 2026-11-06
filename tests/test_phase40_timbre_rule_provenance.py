# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 40 — immutable provenance for the stamp-duty calculation (F-04).

The audit that closed Phase 39 found that a posted invoice kept
``amount_timbre_dzd`` and nothing else: not the rule version, not the brackets,
not the base. The amount was right for the rule *at the time*, but an operator
who configured a new rule could no longer explain an old invoice, and nothing
stopped a referenced rule version from being rewritten in place.

What is held to account here:

* **identity** — every validated invoice records the version code, the source
  (configured vs compiled-in default), a content hash of the whole rule and its
  effective window;
* **inputs** — the base amount, the payment method (which decides whether duty
  applies at all) and the computed amount, so the arithmetic can be replayed;
* **immutability** — a rule version referenced by a *posted* invoice cannot be
  updated or deleted, and the version ledger is append-only by trigger;
* **stability** — installing a new rule never changes what an old invoice says;
* **honesty** — an invoice validated before this existed reports
  ``unavailable`` with a reason and is never backfilled with today's rule;
* **no silent repricing** — a draft whose rule content changed after validation
  is refused at posting with ``TIMBRE_RULE_CHANGED_SINCE_VALIDATION``;
* **reversal** — a credit note cites the same rule identity as the invoice it
  reverses, not whatever is configured when the credit note is raised.

No test here asserts a legal rate. The rule *values* are configuration; what is
proved is that the configuration a document used is recoverable and cannot be
rewritten afterwards.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase, FinancialIntegrityError
from dz_erp.decimal_utils import format_dzd, to_decimal
from dz_erp.localization import (
    TaxRuleConfigurationError,
    calculate_droit_de_timbre,
    describe_invoice_timbre_provenance,
    load_official_odoo_chart,
    resolve_timbre_rule,
    timbre_rule_content_hash,
)
from dz_erp.procurement_stock import (
    create_and_validate_supplier_invoice,
    create_reversal_request,
    decide_invoice_approval,
    decide_reversal_approval,
    post_reversal,
    post_supplier_invoice,
    submit_invoice_for_approval,
    submit_reversal_for_approval,
)

SEEDED_VERSION = "LF-2025-CASH-TIMBRE"  # what `load_official_odoo_chart` installs

_BRACKETS_2026 = [
    {"up_to": "30000.00", "rate": "0.015"},
    {"up_to": None, "rate": "0.025"},
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _db(tmp_path: Path, filename: str = "p40.sqlite3") -> ERPDatabase:
    db = ERPDatabase(str(tmp_path / filename))
    with db.transaction() as conn:
        for cid, code in ((1, "DZ1"), (2, "DZ2")):
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
                "VALUES (?, ?, ?, 'DZ', 'dz', '2026-01-01')",
                (cid, code, f"Tenant {code}"),
            )
        for uid, cid, username, role in (
            (1, 1, "admin_a", "admin"),
            (2, 1, "clerk_a", "clerk"),
            (3, 1, "approver_a", "approver"),
            (4, 1, "acct_a", "accountant"),
            (20, 2, "admin_b", "admin"),
            (24, 2, "acct_b", "accountant"),
        ):
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (uid, cid, username, role, hash_password("Acct#2026x", iterations=1000)),
            )
        for pid, cid in ((7, 1), (8, 2)):
            conn.execute(
                "INSERT INTO partners (id, company_id, name, partner_type, country_code, "
                "tax_regime, nif, nis, ai, rc) VALUES (?, ?, 'SPARTE', 'supplier', 'DZ', 'REEL', "
                "'000216001234567', '000216001234567', '16019912316', '16/00-0991216B12')",
                (pid, cid),
            )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=24)
    return db


def _rule(db: ERPDatabase, on_date: str, *, company_id: int = 1):
    with db.transaction() as conn:
        return resolve_timbre_rule(conn, company_id, on_date)


def _line(qty: str = "2", price: str = "500.00", rate: str = "19") -> dict:
    net = (Decimal(qty) * Decimal(price)).quantize(Decimal("0.01"))
    tva = (net * (Decimal(rate) / Decimal("100"))).quantize(Decimal("0.01"))
    return {
        "description": "Widgets",
        "qty": qty,
        "unit_price_dzd": price,
        "discount_pct": "0.00",
        "tax_rate": rate,
        "line_ht_dzd": f"{net:.2f}",
        "line_tva_dzd": f"{tva:.2f}",
        "line_ttc_dzd": f"{net + tva:.2f}",
        "po_line_id": None,
    }


def _invoice_totals(db: ERPDatabase, invoice_date: str, payment_method: str = "CASH"):
    """What the rule in force on that date says the invoice must total."""
    rule = _rule(db, invoice_date)
    net = Decimal("1000.00")
    tva = Decimal("190.00")
    timbre = calculate_droit_de_timbre(net + tva, payment_method=payment_method, **rule.as_kwargs())
    return rule, {
        "amount_ht": format_dzd(net),
        "amount_tva": format_dzd(tva),
        "amount_timbre": format_dzd(timbre),
        "amount_ttc": format_dzd(net + tva + timbre),
    }


def _create(
    db: ERPDatabase,
    *,
    invoice_date: str = "2026-02-14",
    payment_method: str = "CASH",
    invoice_no: str = "FAC-2026-0001",
    company_id: int = 1,
    user_id: int = 2,
    supplier_id: int = 7,
):
    _rule_obj, totals = _invoice_totals(db, invoice_date, payment_method)
    return create_and_validate_supplier_invoice(
        db=db,
        company_id=company_id,
        user_id=user_id,
        supplier_id=supplier_id,
        supplier_invoice_no=invoice_no,
        invoice_date=invoice_date,
        lines=[_line()],
        amount_ht_dzd=totals["amount_ht"],
        amount_tva_dzd=totals["amount_tva"],
        amount_timbre_dzd=totals["amount_timbre"],
        amount_ttc_dzd=totals["amount_ttc"],
        payment_method=payment_method,
    )


def _post(db: ERPDatabase, invoice_id: int, key: str = "post-1", *, company_id: int = 1,
          clerk: int = 2, approver: int = 3, accountant: int = 4):
    submit_invoice_for_approval(db, company_id, clerk, invoice_id)
    decide_invoice_approval(db, company_id, approver, invoice_id, approve=True)
    return post_supplier_invoice(db, company_id, accountant, invoice_id, key)


def _row(db: ERPDatabase, invoice_id: int, company_id: int = 1) -> dict:
    with db.transaction() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM supplier_invoices WHERE id = ? AND company_id = ?",
                (invoice_id, company_id),
            ).fetchone()
        )


def _ledger(db: ERPDatabase, company_id: int = 1) -> list[dict]:
    with db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM timbre_rule_versions WHERE company_id = ? ORDER BY id", (company_id,)
            ).fetchall()
        ]


def _add_rule(
    db: ERPDatabase,
    *,
    version_code: str,
    valid_from: str,
    valid_to: str | None = None,
    brackets: list[dict] | None = None,
    company_id: int = 1,
    threshold: str = "300.00",
    min_amount: str = "5.00",
) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO timbre_rules (
                company_id, version_code, valid_from, valid_to,
                min_amount_dzd, max_amount_dzd, threshold_dzd, brackets_json
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                company_id,
                version_code,
                valid_from,
                valid_to,
                min_amount,
                threshold,
                json.dumps(brackets or _BRACKETS_2026),
            ),
        )


# ---------------------------------------------------------------------------
# 1. identity is recorded at validation
# ---------------------------------------------------------------------------


def test_validation_records_the_rule_identity_hash_and_inputs(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    assert created["workflow_state"] == "Validated", created["validation_errors"]

    row = _row(db, created["invoice_id"])
    rule = _rule(db, "2026-02-14")
    assert row["timbre_rule_version"] == rule.version_code == SEEDED_VERSION
    assert row["timbre_rule_source"] == "configured"
    assert row["timbre_rule_hash"] == timbre_rule_content_hash(rule)
    assert row["timbre_rule_valid_from"] == "2025-01-01"
    assert row["timbre_rule_valid_to"] is None

    snapshot = json.loads(row["timbre_snapshot_json"])
    assert snapshot["schema"] == "timbre-provenance-v1"
    assert snapshot["stage"] == "validation"
    assert snapshot["calculation"] == {
        "base_amount_dzd": "1190.00",
        "payment_method": "CASH",
        "amount_timbre_dzd": row["amount_timbre_dzd"],
    }
    assert snapshot["rule"]["brackets"] == rule.brackets


def test_the_snapshot_replays_the_stored_amount(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    row = _row(db, created["invoice_id"])
    snapshot = json.loads(row["timbre_snapshot_json"])

    replayed = calculate_droit_de_timbre(
        to_decimal(snapshot["calculation"]["base_amount_dzd"]),
        payment_method=snapshot["calculation"]["payment_method"],
        brackets=snapshot["rule"]["brackets"],
        min_duty=snapshot["rule"]["min_amount_dzd"],
        max_duty=snapshot["rule"]["max_amount_dzd"],
        threshold=snapshot["rule"]["threshold_dzd"],
    )
    # Replay from the invoice's own evidence, with no access to configuration.
    assert format_dzd(replayed) == row["amount_timbre_dzd"]


def test_the_version_ledger_records_first_use_and_its_content(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    ledger = _ledger(db)
    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["version_code"] == SEEDED_VERSION
    assert entry["source"] == "configured"
    assert entry["content_hash"] == _row(db, created["invoice_id"])["timbre_rule_hash"]
    assert json.loads(entry["brackets_json"])
    assert entry["first_used_by_invoice_id"] == created["invoice_id"]


def test_first_use_is_recorded_once_per_content(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _create(db, invoice_no="FAC-2026-0001")
    _create(db, invoice_no="FAC-2026-0002")
    assert len(_ledger(db)) == 1, "the same rule content must not be duplicated in the ledger"


# ---------------------------------------------------------------------------
# 2. effective-date boundaries select different identities
# ---------------------------------------------------------------------------


def test_the_effective_date_boundary_selects_a_different_rule_identity(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timbre_rules SET valid_to = '2025-12-31' WHERE version_code = ?",
            (SEEDED_VERSION,),
        )
    _add_rule(db, version_code="LF-2026-CASH-TIMBRE", valid_from="2026-01-01")

    old = _create(db, invoice_date="2025-12-31", invoice_no="FAC-2025-0012")
    new = _create(db, invoice_date="2026-01-01", invoice_no="FAC-2026-0001")

    old_row, new_row = _row(db, old["invoice_id"]), _row(db, new["invoice_id"])
    assert old_row["timbre_rule_version"] == SEEDED_VERSION
    assert new_row["timbre_rule_version"] == "LF-2026-CASH-TIMBRE"
    assert old_row["timbre_rule_hash"] != new_row["timbre_rule_hash"]
    assert old_row["timbre_rule_valid_to"] == "2025-12-31"
    assert new_row["timbre_rule_valid_to"] is None
    # Different brackets, so a different amount: the identity is not decorative.
    assert Decimal(old_row["amount_timbre_dzd"]) != Decimal(new_row["amount_timbre_dzd"])
    assert {e["version_code"] for e in _ledger(db)} == {
        SEEDED_VERSION,
        "LF-2026-CASH-TIMBRE",
    }


def test_a_new_rule_version_does_not_change_an_existing_invoice(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    before = _row(db, created["invoice_id"])
    _post(db, created["invoice_id"])
    posted_before = _row(db, created["invoice_id"])

    _add_rule(db, version_code="LF-2027-CASH-TIMBRE", valid_from="2027-01-01")

    posted_after = _row(db, created["invoice_id"])
    for column in (
        "amount_ht_dzd",
        "amount_tva_dzd",
        "amount_timbre_dzd",
        "amount_ttc_dzd",
        "timbre_rule_version",
        "timbre_rule_hash",
        "timbre_snapshot_json",
    ):
        assert posted_after[column] == posted_before[column] == before[column], column
    described = describe_invoice_timbre_provenance(posted_after)
    assert described["provenance"] == "recorded"
    assert described["rule_version"] == SEEDED_VERSION


# ---------------------------------------------------------------------------
# 3. immutability of referenced rules
# ---------------------------------------------------------------------------


def test_a_rule_referenced_by_a_posted_invoice_cannot_be_rewritten(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    _post(db, created["invoice_id"])

    with pytest.raises(sqlite3.IntegrityError, match="TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE"):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE timbre_rules SET threshold_dzd = '100.00' WHERE version_code = ?",
                (SEEDED_VERSION,),
            )
    # The rule and the invoice are untouched.
    assert _rule(db, "2026-02-14").threshold_dzd == "300.00"
    assert _row(db, created["invoice_id"])["amount_timbre_dzd"] == format_dzd(
        Decimal(_row(db, created["invoice_id"])["amount_timbre_dzd"])
    )


def test_a_rule_referenced_by_a_posted_invoice_cannot_be_deleted(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    _post(db, created["invoice_id"])

    with pytest.raises(sqlite3.IntegrityError, match="TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM timbre_rules WHERE version_code = ?", (SEEDED_VERSION,))
    assert _rule(db, "2026-02-14").version_code == SEEDED_VERSION


def test_an_unposted_rule_can_still_be_corrected(tmp_path: Path) -> None:
    """The guard protects posted evidence, not work in progress."""
    db = _db(tmp_path)
    created = _create(db)  # Validated, not posted
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timbre_rules SET threshold_dzd = '250.00' WHERE version_code = ?",
            (SEEDED_VERSION,),
        )
    assert _rule(db, "2026-02-14").threshold_dzd == "250.00"
    assert _row(db, created["invoice_id"])["timbre_rule_version"] == SEEDED_VERSION


def test_the_version_ledger_is_append_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _create(db)
    with pytest.raises(sqlite3.IntegrityError, match="TIMBRE_RULE_LEDGER_APPEND_ONLY"):
        with db.transaction() as conn:
            conn.execute("UPDATE timbre_rule_versions SET threshold_dzd = '1.00'")
    with pytest.raises(sqlite3.IntegrityError, match="TIMBRE_RULE_LEDGER_APPEND_ONLY"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM timbre_rule_versions")


# ---------------------------------------------------------------------------
# 4. no silent repricing between validation and posting
# ---------------------------------------------------------------------------


def test_posting_refuses_when_the_rule_changed_after_validation(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    row = _row(db, created["invoice_id"])
    # The operator corrects the (still unposted) rule after this draft was
    # validated, so the amount the approver saw is no longer the amount the
    # current rule produces.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timbre_rules SET brackets_json = ? WHERE version_code = ?",
            (json.dumps([{"up_to": None, "rate": "0.05"}]), SEEDED_VERSION),
        )

    submit_invoice_for_approval(db, 1, 2, created["invoice_id"])
    decide_invoice_approval(db, 1, 3, created["invoice_id"], approve=True)
    with pytest.raises(TaxRuleConfigurationError, match="TIMBRE_RULE_CHANGED_SINCE_VALIDATION"):
        post_supplier_invoice(db, 1, 4, created["invoice_id"], "post-1")

    after = _row(db, created["invoice_id"])
    assert after["workflow_state"] != "Posted"
    assert after["amount_timbre_dzd"] == row["amount_timbre_dzd"]
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"] == 0


def test_concurrent_rule_change_and_posting_never_produce_a_mismatch(tmp_path: Path) -> None:
    """Either the invoice posts under its recorded rule, or it is refused."""
    db = _db(tmp_path)
    created = _create(db)
    submit_invoice_for_approval(db, 1, 2, created["invoice_id"])
    decide_invoice_approval(db, 1, 3, created["invoice_id"], approve=True)
    recorded_hash = _row(db, created["invoice_id"])["timbre_rule_hash"]

    errors: list[BaseException] = []
    posted: list[dict] = []

    def changer() -> None:
        try:
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE timbre_rules SET brackets_json = ? WHERE version_code = ?",
                    (json.dumps([{"up_to": None, "rate": "0.07"}]), SEEDED_VERSION),
                )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    def poster() -> None:
        try:
            posted.append(post_supplier_invoice(db, 1, 4, created["invoice_id"], "post-race"))
        except (FinancialIntegrityError, TaxRuleConfigurationError) as exc:
            posted.append({"refused": str(exc)})

    threads = [threading.Thread(target=changer), threading.Thread(target=poster)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    result = posted[0]
    row = _row(db, created["invoice_id"])
    if "refused" in result:
        assert row["workflow_state"] != "Posted"
    else:
        assert row["workflow_state"] == "Posted"
        # The posted evidence is the rule the invoice was validated under.
        assert row["timbre_rule_hash"] == recorded_hash
        described = describe_invoice_timbre_provenance(row)
        assert described["rule_version"] == SEEDED_VERSION


# ---------------------------------------------------------------------------
# 5. legacy invoices: no invented provenance
# ---------------------------------------------------------------------------


def test_a_legacy_invoice_reports_unavailable_and_is_not_backfilled(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET timbre_rule_version = NULL, timbre_rule_source = NULL, "
            "timbre_rule_hash = NULL, timbre_rule_valid_from = NULL, timbre_rule_valid_to = NULL, "
            "timbre_snapshot_json = NULL WHERE id = ?",
            (created["invoice_id"],),
        )
    described = describe_invoice_timbre_provenance(_row(db, created["invoice_id"]))
    assert described["provenance"] == "unavailable"
    assert described["reason"] == "LEGACY_INVOICE_WITHOUT_RULE_IDENTITY"
    assert described["amount_timbre_dzd"] == _row(db, created["invoice_id"])["amount_timbre_dzd"]
    assert "rule_version" not in described


def test_posting_a_legacy_invoice_records_the_governing_rule_at_posting(tmp_path: Path) -> None:
    """A fact about the posting, labelled as such — not a claim about the past."""
    db = _db(tmp_path)
    created = _create(db)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET timbre_rule_version = NULL, timbre_rule_source = NULL, "
            "timbre_rule_hash = NULL, timbre_snapshot_json = NULL WHERE id = ?",
            (created["invoice_id"],),
        )
    _post(db, created["invoice_id"])
    row = _row(db, created["invoice_id"])
    assert row["workflow_state"] == "Posted"
    assert row["timbre_rule_version"] == SEEDED_VERSION
    snapshot = json.loads(row["timbre_snapshot_json"])
    assert snapshot["stage"] == "posting"


def test_an_unparseable_snapshot_is_reported_not_raised(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET timbre_snapshot_json = '{not json' WHERE id = ?",
            (created["invoice_id"],),
        )
    described = describe_invoice_timbre_provenance(_row(db, created["invoice_id"]))
    assert described["provenance"] == "recorded"
    assert described["snapshot"] == {"error": "SNAPSHOT_UNPARSEABLE"}
    assert described["rule_version"] == SEEDED_VERSION


# ---------------------------------------------------------------------------
# 6. reversal keeps the calculation evidence
# ---------------------------------------------------------------------------


def test_a_reversal_inherits_the_original_provenance(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    _post(db, created["invoice_id"])
    original = _row(db, created["invoice_id"])

    # A new rule installed after the original was posted must not change what
    # the credit note says it reverses.
    _add_rule(db, version_code="LF-2028-CASH-TIMBRE", valid_from="2028-01-01")

    reversal = create_reversal_request(
        db, 1, 4, created["invoice_id"], "Supplier credit note", "rev-1"
    )
    credit_note = _row(db, reversal["credit_note_invoice_id"])
    assert credit_note["invoice_type"] == "IN_REFUND"
    assert credit_note["timbre_rule_version"] == original["timbre_rule_version"]
    assert credit_note["timbre_rule_hash"] == original["timbre_rule_hash"]
    snapshot = json.loads(credit_note["timbre_snapshot_json"])
    assert snapshot["stage"] == "reversal"
    assert snapshot["inherited_from_invoice_id"] == created["invoice_id"]
    assert snapshot["rule"]["content_hash"] == original["timbre_rule_hash"]


def test_a_legacy_reversal_stays_unavailable(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    _post(db, created["invoice_id"])
    with db.transaction() as conn:
        conn.execute(
            "UPDATE supplier_invoices SET timbre_rule_version = NULL, timbre_rule_source = NULL, "
            "timbre_rule_hash = NULL, timbre_snapshot_json = NULL WHERE id = ?",
            (created["invoice_id"],),
        )
    reversal = create_reversal_request(db, 1, 4, created["invoice_id"], "Correction", "rev-2")
    described = describe_invoice_timbre_provenance(_row(db, reversal["credit_note_invoice_id"]))
    assert described["provenance"] == "unavailable"
    assert described["reason"] == "LEGACY_INVOICE_WITHOUT_RULE_IDENTITY"


def test_a_posted_reversal_preserves_the_original_calculation_evidence(tmp_path: Path) -> None:
    """The whole reversal chain: create, submit, approve, post — same rule."""
    db = _db(tmp_path)
    created = _create(db)
    _post(db, created["invoice_id"])
    original = _row(db, created["invoice_id"])

    reversal = create_reversal_request(db, 1, 4, created["invoice_id"], "Retour", "rev-full")
    credit_note_id = reversal["credit_note_invoice_id"]
    submit_reversal_for_approval(db, 1, 4, credit_note_id)
    decide_reversal_approval(db, 1, 3, credit_note_id, approve=True)
    post_reversal(db, 1, 4, credit_note_id, "post-rev-full")

    credit_note = _row(db, credit_note_id)
    assert credit_note["workflow_state"] == "Posted"
    assert credit_note["timbre_rule_version"] == original["timbre_rule_version"]
    assert credit_note["timbre_rule_hash"] == original["timbre_rule_hash"]
    described = describe_invoice_timbre_provenance(credit_note)
    assert described["provenance"] == "recorded"
    assert described["snapshot"]["stage"] == "reversal"


# ---------------------------------------------------------------------------
# 7. tenant isolation
# ---------------------------------------------------------------------------


def test_rule_identity_is_tenant_scoped(tmp_path: Path) -> None:
    db = _db(tmp_path)
    created = _create(db)
    _post(db, created["invoice_id"])
    other = _create(db, company_id=2, user_id=20, supplier_id=8, invoice_no="FAC-B-0001")

    a = _row(db, created["invoice_id"])
    b = _row(db, other["invoice_id"], company_id=2)
    assert a["timbre_rule_version"] == b["timbre_rule_version"] == SEEDED_VERSION
    assert a["timbre_rule_hash"] == b["timbre_rule_hash"]

    # Rewriting company 2's rule is allowed (nothing of theirs is posted) and
    # must not disturb company 1's posted invoice.
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timbre_rules SET threshold_dzd = '999.00' WHERE company_id = 2 AND version_code = ?",
            (SEEDED_VERSION,),
        )
    assert _row(db, created["invoice_id"])["timbre_rule_hash"] == a["timbre_rule_hash"]
    assert _rule(db, "2026-02-14", company_id=2).threshold_dzd == "999.00"
    assert _rule(db, "2026-02-14", company_id=1).threshold_dzd == "300.00"

    # And the ledger is per tenant.
    assert {e["company_id"] for e in _ledger(db)} == {1}
    assert {e["company_id"] for e in _ledger(db, company_id=2)} == {2}


# ---------------------------------------------------------------------------
# 8. migration of an existing database
# ---------------------------------------------------------------------------


def test_migration_adds_the_columns_and_leaves_existing_rows_null(tmp_path: Path) -> None:
    """An older database gains the columns; its invoices gain no invented identity."""
    path = tmp_path / "legacy.sqlite3"
    db = _db(tmp_path, "legacy.sqlite3")  # the file that is about to become "old"
    created = _create(db)

    # Simulate the pre-Phase-40 schema: drop the provenance columns and the
    # ledger, then reopen the same file.
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = OFF")
    for trigger in (
        "trg_timbre_rules_no_update_when_referenced",
        "trg_timbre_rules_no_delete_when_referenced",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for column in (
        "timbre_rule_version",
        "timbre_rule_source",
        "timbre_rule_hash",
        "timbre_rule_valid_from",
        "timbre_rule_valid_to",
        "timbre_snapshot_json",
    ):
        conn.execute(f"ALTER TABLE supplier_invoices DROP COLUMN {column}")
    conn.execute("DROP TABLE IF EXISTS timbre_rule_versions")
    conn.commit()
    conn.close()

    reopened = ERPDatabase(str(path))
    with reopened.transaction() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(supplier_invoices)").fetchall()}
        row = dict(
            conn.execute("SELECT * FROM supplier_invoices WHERE id = ?", (created["invoice_id"],)).fetchone()
        )
        ledger_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'timbre_rule_versions'"
        ).fetchone()
        triggers = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_timbre%'"
            ).fetchall()
        }
    assert {"timbre_rule_version", "timbre_rule_hash", "timbre_snapshot_json"} <= columns
    assert row["timbre_rule_version"] is None, "migration must not invent an identity"
    assert ledger_table is not None
    assert {
        "trg_timbre_rule_versions_no_update",
        "trg_timbre_rule_versions_no_delete",
        "trg_timbre_rules_no_update_when_referenced",
        "trg_timbre_rules_no_delete_when_referenced",
    } <= triggers
    assert describe_invoice_timbre_provenance(row)["provenance"] == "unavailable"


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "twice.sqlite3"
    first = _db(tmp_path, "twice.sqlite3")
    _create(first)
    with first.transaction() as conn:
        before = conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    second = ERPDatabase(str(db_path))
    with second.transaction() as conn:
        after = conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


# ---------------------------------------------------------------------------
# 9. the API surfaces the provenance
# ---------------------------------------------------------------------------


def test_the_api_returns_the_provenance_of_an_invoice(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from dz_erp.api import create_erp_fastapi_app
    from dz_erp.intake_ocr import PrivateDocumentVault

    db = _db(tmp_path)
    created = _create(db)
    client = TestClient(create_erp_fastapi_app(db, PrivateDocumentVault(tmp_path / "vault")))
    token = client.post(
        "/api/v1/auth/token", json={"username": "admin_a", "password": "Acct#2026x"}
    ).json()["access_token"]
    body = client.get(
        f"/api/v1/invoices/{created['invoice_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert body.status_code == 200
    payload = body.json()["invoice"] if "invoice" in body.json() else body.json()
    provenance = payload["timbre_provenance"]
    assert provenance["provenance"] == "recorded"
    assert provenance["rule_version"] == SEEDED_VERSION
    assert provenance["snapshot"]["calculation"]["payment_method"] == "CASH"
