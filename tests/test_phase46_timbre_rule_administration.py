# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 46 — audited timbre rule administration (finding R-05).

Before this phase the only writer of ``timbre_rules`` was the chart-template
seed. Once a version had priced a posted invoice, the immutability triggers
correctly froze it, so adopting a new Finance Law meant editing the database by
hand: no actor, no reason, no audit event, no authorization, and nothing to stop
two effective windows quietly overlapping. The evidence side of F-04 was strong;
the door was a hole in the wall.

What these tests hold the new path to:

* a rule is created as a **DRAFT** and governs nothing — the resolver ignores it
  until a second, *different* accountant approves it (segregation of duties);
* approval is where overlap is decided: refused by default, and
  ``overlap_policy="supersede"`` may only narrow a window that no posted invoice
  cites (the trigger is the backstop, the check gives the readable reason);
* every step appends an audit event carrying actor, time, version, window,
  values and reason, and the chain still verifies;
* authorization and tenant isolation are enforced at the service, not the
  endpoint, so a direct caller cannot bypass them;
* no value is ever defaulted: an incomplete rule is refused rather than filled
  in with a number nobody chose — the tax values themselves remain the
  expert-comptable's determination, not this module's.

The historical property that must never regress: a posted invoice stays exactly
as it was, whatever is configured afterwards.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import (
    AuthorizationError,
    ERPDatabase,
    TenantIsolationError,
)
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.localization import (
    TaxRuleConfigurationError,
    TimbreRuleAdministrationError,
    approve_timbre_rule_version,
    create_timbre_rule_version,
    list_timbre_rule_versions,
    resolve_timbre_rule,
)

PASSWORD = "Admin#2026x"

#: A deliberately simple, clearly-fake bracket table. It is *test data*, never a
#: claim about Algerian law: the real values are BLOCKED_EXTERNAL pending an
#: expert-comptable review (see docs/RELEASE_READINESS_MATRIX.md, R-SCF).
BRACKETS = [
    {"up_to": "10000", "rate": "0.01"},
    {"up_to": None, "rate": "0.005"},
]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _build(tmp_path: Path) -> tuple[ERPDatabase, dict[str, int]]:
    """One company, an accountant, a second accountant, an admin and a clerk."""
    db = ERPDatabase(str(tmp_path / "p46.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (2, 'DZ2', 'Tenant Beta', 'DZ', 'dz', '2026-01-01')"
        )
        # The chart-template seed installs this rule; the fixture reproduces it so
        # the resolver has a real ACTIVE row to fall back on.
        conn.execute(
            """
            INSERT INTO timbre_rules (company_id, version_code, valid_from, valid_to,
                                      min_amount_dzd, threshold_dzd, brackets_json)
            VALUES (1, 'LF-2025-CASH-TIMBRE', '2025-01-01', '2026-12-31', '5.00', '300.00', ?)
            """,
            (json.dumps(BRACKETS),),
        )
        people = [
            (1, 1, "comptable_a", "accountant"),
            (2, 1, "comptable_b", "accountant"),
            (3, 1, "admin_a", "admin"),
            (4, 1, "clerk_a", "clerk"),
            (5, 2, "comptable_z", "accountant"),
        ]
        for uid, cid, name, role in people:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (uid, cid, name, role, hash_password(PASSWORD, iterations=1000)),
            )
    with db.transaction() as conn:
        for cid in (1, 2):
            conn.execute(
                "INSERT INTO partners (id, company_id, name, partner_type, nif) "
                "VALUES (?, ?, 'Supplier', 'supplier', ?)",
                (cid, cid, f"NIF{cid:013d}"),
            )
    return db, {"creator": 1, "approver": 2, "admin": 3, "clerk": 4, "other_tenant": 5}


def _create(db: ERPDatabase, ids: dict[str, int], **overrides) -> dict:
    kwargs = dict(
        version_code="LF-2027-CASH",
        valid_from="2027-01-01",
        valid_to=None,
        min_amount_dzd="5.00",
        threshold_dzd="300.00",
        brackets=BRACKETS,
        reason="Finance Law 2027, art. 12 — bracket table reviewed by the tax signatory",
    )
    kwargs.update(overrides)
    return create_timbre_rule_version(db, 1, ids["creator"], **kwargs)


def _approve(db: ERPDatabase, ids: dict[str, int], version_code: str = "LF-2027-CASH", **overrides):
    kwargs = dict(reason="Second review: brackets match the published text", overlap_policy="reject")
    kwargs.update(overrides)
    return approve_timbre_rule_version(db, 1, ids["approver"], version_code=version_code, **kwargs)


def _audit(db: ERPDatabase, action: str) -> list[dict]:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE company_id = 1 AND action_type = ? ORDER BY id",
            (action,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------


def test_a_new_rule_is_a_draft_and_governs_nothing(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    created = _create(db, ids)

    assert created["review_state"] == "DRAFT"
    assert created["in_force"] is False

    # The resolver still uses the seeded rule inside its own window, and the
    # proposal's window is not in force at all.
    with db.transaction() as conn:
        resolved = resolve_timbre_rule(conn, 1, "2026-06-01")
        with pytest.raises(TaxRuleConfigurationError, match="TIMBRE_RULE_NOT_EFFECTIVE_ON_DATE"):
            resolve_timbre_rule(conn, 1, "2027-06-01")
    assert resolved.version_code == "LF-2025-CASH-TIMBRE", "a draft must never govern an amount"


def test_creation_records_actor_window_values_and_reason(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids, valid_to="2027-12-31")

    events = _audit(db, "TIMBRE_RULE_VERSION_CREATED")
    assert len(events) == 1
    state = json.loads(events[0]["after_json"])
    assert state["version_code"] == "LF-2027-CASH"
    assert state["review_state"] == "DRAFT"
    assert state["valid_from"] == "2027-01-01"
    assert state["valid_to"] == "2027-12-31"
    assert state["min_amount_dzd"] == "5.00"
    assert state["threshold_dzd"] == "300.00"
    assert state["brackets"] == BRACKETS
    assert "Finance Law 2027" in state["reason"]
    assert state["actor"] == "comptable_a"
    assert events[0]["actor_user_id"] == ids["creator"]
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_a_rule_version_is_never_recreated_under_the_same_code(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids)
    with pytest.raises(TimbreRuleAdministrationError, match="TIMBRE_RULE_VERSION_EXISTS"):
        _create(db, ids, reason="a second attempt that must not overwrite the first")


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"version_code": ""}, "TIMBRE_RULE_VERSION_INVALID"),
        ({"version_code": "has spaces"}, "TIMBRE_RULE_VERSION_INVALID"),
        ({"valid_from": "2027-13-01"}, "TIMBRE_RULE_WINDOW_INVALID"),
        ({"valid_from": "2027-12-31", "valid_to": "2027-01-01"}, "TIMBRE_RULE_WINDOW_INVALID"),
        ({"reason": "   "}, "TIMBRE_RULE_REASON_REQUIRED"),
        ({"min_amount_dzd": None}, "TIMBRE_RULE_VALUE_REQUIRED:min_amount_dzd"),
        ({"threshold_dzd": None}, "TIMBRE_RULE_VALUE_REQUIRED:threshold_dzd"),
        ({"min_amount_dzd": "-1.00"}, "TIMBRE_RULE_VALUE_NEGATIVE"),
        ({"min_amount_dzd": "five dinars"}, "TIMBRE_RULE_VALUE_INVALID"),
        ({"brackets": []}, "TIMBRE_RULE_BRACKETS"),
        ({"brackets": "not json"}, "TIMBRE_RULE_BRACKETS"),
    ],
)
def test_an_incomplete_or_invalid_rule_is_refused_not_defaulted(
    tmp_path: Path, overrides: dict, expected: str
) -> None:
    """No field has a convenience default: a missing tax value is a refusal."""
    db, ids = _build(tmp_path)
    with pytest.raises((TimbreRuleAdministrationError, ValueError), match=expected):
        _create(db, ids, **overrides)
    assert _audit(db, "TIMBRE_RULE_VERSION_CREATED") == [], "a refused creation audits nothing"


def test_no_brackets_are_ever_invented_by_the_service(tmp_path: Path) -> None:
    """The values are the caller's, and the caller is accountable for them."""
    db, ids = _build(tmp_path)
    own = [{"up_to": "5000", "rate": "0.02"}, {"up_to": None, "rate": "0.01"}]
    created = _create(db, ids, brackets=own)
    assert created["brackets"] == own, "the service must store exactly what it was given"
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT brackets_json FROM timbre_rules WHERE version_code = 'LF-2027-CASH'"
        ).fetchone()
    assert json.loads(row["brackets_json"]) == own


# ---------------------------------------------------------------------------
# approval: authorization, segregation of duties, activation
# ---------------------------------------------------------------------------


def test_activation_requires_a_second_identity_and_then_governs_the_window(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids)
    with pytest.raises(AuthorizationError, match="TIMBRE_RULE_SOD_VIOLATION"):
        approve_timbre_rule_version(
            db, 1, ids["creator"], version_code="LF-2027-CASH", reason="self approval attempt",
        )
    assert _audit(db, "TIMBRE_RULE_VERSION_APPROVED") == []

    approved = _approve(db, ids)
    assert approved["review_state"] == "ACTIVE"
    assert approved["reviewed_by_id"] == ids["approver"]

    with db.transaction() as conn:
        in_window = resolve_timbre_rule(conn, 1, "2027-06-01")
        before_window = resolve_timbre_rule(conn, 1, "2026-06-01")
    assert in_window.version_code == "LF-2027-CASH"
    assert in_window.source == "configured"
    assert before_window.version_code == "LF-2025-CASH-TIMBRE", (
        "a rule must never apply to a date before its own window"
    )


def test_approval_is_a_one_way_transition(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids)
    _approve(db, ids)
    with pytest.raises(TimbreRuleAdministrationError, match="TIMBRE_RULE_NOT_A_DRAFT"):
        _approve(db, ids, reason="approving twice must not rewrite the first decision")


def test_approval_audit_carries_the_decision_and_the_supersession(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids)
    _approve(db, ids)

    events = _audit(db, "TIMBRE_RULE_VERSION_APPROVED")
    assert len(events) == 1
    state = json.loads(events[0]["after_json"])
    assert state["review_state"] == "ACTIVE"
    assert state["reviewed_by"] == "comptable_b"
    assert state["created_by_id"] == ids["creator"]
    assert state["brackets"] == BRACKETS
    assert state["overlap_policy"] == "reject"
    assert "Second review" in state["reason"]
    assert db.verify_audit_chain(company_id=1)["valid"] is True


def test_only_accountants_and_admins_may_administer_rules(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    with pytest.raises(AuthorizationError, match="TIMBRE_RULE_ADMIN_ROLE_REQUIRED"):
        create_timbre_rule_version(
            db, 1, ids["clerk"], version_code="X-1", valid_from="2027-01-01", valid_to=None,
            min_amount_dzd="5.00", threshold_dzd="300.00", brackets=BRACKETS, reason="clerk attempt",
        )
    # A draft created properly still cannot be approved by a clerk.
    _create(db, ids)
    with pytest.raises(AuthorizationError, match="TIMBRE_RULE_ADMIN_ROLE_REQUIRED"):
        approve_timbre_rule_version(db, 1, ids["clerk"], version_code="LF-2027-CASH", reason="clerk")


def test_administration_is_tenant_scoped(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids)

    # The other tenant's accountant cannot see, approve or re-create it.
    assert list_timbre_rule_versions(db, 2, ids["other_tenant"]) == []
    with pytest.raises(TenantIsolationError, match="TIMBRE_RULE_NOT_FOUND"):
        approve_timbre_rule_version(
            db, 2, ids["other_tenant"], version_code="LF-2027-CASH", reason="cross tenant"
        )
    with pytest.raises(TenantIsolationError):
        create_timbre_rule_version(
            db, 2, ids["creator"], version_code="X-2", valid_from="2027-01-01", valid_to=None,
            min_amount_dzd="5.00", threshold_dzd="300.00", brackets=BRACKETS, reason="foreign user",
        )
    # The same code in another company is a separate row, not a conflict.
    other = create_timbre_rule_version(
        db, 2, ids["other_tenant"], version_code="LF-2027-CASH", valid_from="2027-01-01",
        valid_to=None, min_amount_dzd="6.00", threshold_dzd="400.00", brackets=BRACKETS,
        reason="Tenant Beta's own 2027 rule",
    )
    assert other["version_code"] == "LF-2027-CASH"


# ---------------------------------------------------------------------------
# overlap policy
# ---------------------------------------------------------------------------


def test_an_overlapping_window_is_refused_by_default(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    # Bring a 2027 rule into force (no overlap with the bounded 2025 one)...
    _create(db, ids, version_code="LF-2027-A", valid_from="2027-01-01")
    _approve(db, ids, version_code="LF-2027-A")

    # ...then propose a second one that claims part of the same period.
    _create(db, ids, version_code="LF-2027-B", valid_from="2027-06-01")
    with pytest.raises(TimbreRuleAdministrationError, match="TIMBRE_RULE_OVERLAP"):
        _approve(db, ids, version_code="LF-2027-B")

    # Refused approval leaves the proposal a draft and changes nothing in force.
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT review_state FROM timbre_rules WHERE version_code = 'LF-2027-B'"
        ).fetchone()
        in_force = resolve_timbre_rule(conn, 1, "2027-06-01")
    assert row["review_state"] == "DRAFT"
    assert in_force.version_code == "LF-2027-A", "the rule already in force still governs"


def test_supersede_closes_the_older_window_explicitly(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    # A rule in force from 2027-01-01, then a correction that must take over mid-year.
    _create(db, ids, version_code="LF-2027-A", valid_from="2027-01-01")
    _approve(db, ids, version_code="LF-2027-A")
    _create(db, ids, version_code="LF-2027-B", valid_from="2027-07-01")
    approved = _approve(
        db, ids, version_code="LF-2027-B", overlap_policy="supersede",
        reason="Corrected bracket table applies from 1 July",
    )
    assert approved["superseded"] == [{"version_code": "LF-2027-A", "valid_to": "2027-06-30"}]

    with db.transaction() as conn:
        first_half = resolve_timbre_rule(conn, 1, "2027-03-01")
        second_half = resolve_timbre_rule(conn, 1, "2027-08-01")
        before_2027 = resolve_timbre_rule(conn, 1, "2026-12-31")
    assert first_half.version_code == "LF-2027-A", "the closed window still governs its own days"
    assert second_half.version_code == "LF-2027-B"
    assert before_2027.version_code == "LF-2025-CASH-TIMBRE"
    # And no day is claimed by two rules.
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT version_code, valid_from, valid_to FROM timbre_rules "
            "WHERE company_id = 1 AND review_state = 'ACTIVE' ORDER BY valid_from"
        ).fetchall()
    assert [(r["version_code"], r["valid_from"], r["valid_to"]) for r in rows] == [
        ("LF-2025-CASH-TIMBRE", "2025-01-01", "2026-12-31"),
        ("LF-2027-A", "2027-01-01", "2027-06-30"),
        ("LF-2027-B", "2027-07-01", None),
    ]


def test_an_unknown_overlap_policy_is_refused(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids)
    with pytest.raises(TimbreRuleAdministrationError, match="TIMBRE_RULE_OVERLAP_POLICY_INVALID"):
        _approve(db, ids, overlap_policy="ignore")


def test_a_rule_cited_by_a_posted_invoice_cannot_be_superseded(tmp_path: Path) -> None:
    """The trigger is the backstop; the service gives the readable reason first."""
    db, ids = _build(tmp_path)
    # A posted invoice citing the seeded rule. Inserted directly: this test is
    # about rule administration, and posting an invoice end to end is covered by
    # the lifecycle suites.
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, supplier_id, supplier_invoice_no, normalized_invoice_no,
                invoice_date, payment_method, currency_code, amount_ht_dzd, amount_tva_dzd,
                amount_timbre_dzd, amount_ttc_dzd, workflow_state, payload_sha256,
                created_by_id, invoice_type, fiscal_year, timbre_rule_version, created_at
            ) VALUES (1, 1, 'INV-1', 'INV-1', '2026-03-01', 'CASH', 'DZD', '100.00', '19.00',
                      '5.00', '124.00', 'Posted', 'deadbeef', 1, 'IN_INVOICE', 2026, 'LF-2025-CASH-TIMBRE', '2026-01-01T00:00:00+00:00')
            """
        )
    # A rule that would take over part of the cited rule's own window.
    _create(db, ids, version_code="LF-2026-B", valid_from="2026-06-01")
    with pytest.raises(
        TimbreRuleAdministrationError, match="TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE"
    ):
        _approve(db, ids, version_code="LF-2026-B", overlap_policy="supersede")

    # Nothing moved: the cited rule still covers 2026 and the new one is a draft.
    with db.transaction() as conn:
        assert resolve_timbre_rule(conn, 1, "2026-06-01").version_code == "LF-2025-CASH-TIMBRE"
        state = conn.execute(
            "SELECT review_state FROM timbre_rules WHERE version_code = 'LF-2026-B'"
        ).fetchone()["review_state"]
    assert state == "DRAFT"


def test_the_database_itself_refuses_to_rewrite_a_cited_rule(tmp_path: Path) -> None:
    """Defence in depth: even raw SQL cannot rewrite the explanation of a posting."""
    db, ids = _build(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, supplier_id, supplier_invoice_no, normalized_invoice_no,
                invoice_date, payment_method, currency_code, amount_ht_dzd, amount_tva_dzd,
                amount_timbre_dzd, amount_ttc_dzd, workflow_state, payload_sha256,
                created_by_id, invoice_type, fiscal_year, timbre_rule_version, created_at
            ) VALUES (1, 1, 'INV-2', 'INV-2', '2026-04-01', 'CASH', 'DZD', '100.00', '19.00',
                      '5.00', '124.00', 'Posted', 'deadbeee', 1, 'IN_INVOICE', 2026,
                      'LF-2025-CASH-TIMBRE', '2026-01-01T00:00:00+00:00')
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE"):
        with db.transaction() as conn:
            conn.execute("UPDATE timbre_rules SET min_amount_dzd = '9.00' WHERE company_id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM timbre_rules WHERE company_id = 1")


# ---------------------------------------------------------------------------
# history: a posted invoice never moves
# ---------------------------------------------------------------------------


def test_a_new_rule_never_changes_an_existing_invoice(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, supplier_id, supplier_invoice_no, normalized_invoice_no,
                invoice_date, payment_method, currency_code, amount_ht_dzd, amount_tva_dzd,
                amount_timbre_dzd, amount_ttc_dzd, workflow_state, payload_sha256,
                created_by_id, invoice_type, fiscal_year, timbre_rule_version,
                timbre_rule_source, timbre_rule_hash, timbre_snapshot_json, created_at
            ) VALUES (1, 1, 'INV-3', 'INV-3', '2026-05-01', 'CASH', 'DZD', '100.00', '19.00',
                      '5.00', '124.00', 'Posted', 'deadbeef3', 1, 'IN_INVOICE', 2026,
                      'LF-2025-CASH-TIMBRE', 'configured', 'hash-of-the-2025-rule', '{"stage": "validation"}',
                      '2026-01-01T00:00:00+00:00')
            """
        )

    def snapshot() -> tuple:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT amount_timbre_dzd, timbre_rule_version, timbre_rule_hash, "
                "timbre_snapshot_json FROM supplier_invoices WHERE supplier_invoice_no = 'INV-3'"
            ).fetchone()
        return tuple(row)

    before = snapshot()
    _create(db, ids, version_code="LF-2028-A", valid_from="2028-01-01")
    _approve(
        db, ids, version_code="LF-2028-A", overlap_policy="supersede",
        reason="2028 law replaces the 2025 table",
    )
    assert snapshot() == before, "configuring a new rule must not touch a stored invoice"

    # The 2026 invoice still resolves to the rule that produced it.
    with db.transaction() as conn:
        assert resolve_timbre_rule(conn, 1, "2026-05-01").version_code == "LF-2025-CASH-TIMBRE"


def test_the_listing_shows_drafts_and_which_versions_priced_a_posting(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids, version_code="LF-2027-DRAFT")
    _create(db, ids, version_code="LF-2027-LIVE", valid_from="2027-01-01")
    _approve(db, ids, version_code="LF-2027-LIVE", reason="activate")

    listed = {r["version_code"]: r for r in list_timbre_rule_versions(db, 1, ids["approver"])}
    assert listed["LF-2027-DRAFT"]["in_force"] is False
    assert listed["LF-2027-DRAFT"]["reviewed_by_id"] is None
    assert listed["LF-2027-LIVE"]["in_force"] is True
    assert listed["LF-2027-LIVE"]["reviewed_by_id"] == ids["approver"]
    assert listed["LF-2025-CASH-TIMBRE"]["in_force"] is True


# ---------------------------------------------------------------------------
# concurrency
# ---------------------------------------------------------------------------


def test_two_concurrent_creations_of_the_same_version_produce_exactly_one_rule(
    tmp_path: Path,
) -> None:
    db, ids = _build(tmp_path)
    outcomes: list[str] = []

    def attempt() -> None:
        try:
            create_timbre_rule_version(
                db, 1, ids["creator"], version_code="LF-RACE", valid_from="2027-01-01",
                valid_to=None, min_amount_dzd="5.00", threshold_dzd="300.00",
                brackets=BRACKETS, reason="concurrent creation",
            )
            outcomes.append("created")
        except TimbreRuleAdministrationError as exc:
            outcomes.append(str(exc))

    import threading

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["created", outcomes[1]] or outcomes.count("created") == 1
    assert outcomes.count("created") == 1, outcomes
    assert any("TIMBRE_RULE_VERSION_EXISTS" in o for o in outcomes), outcomes
    with db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM timbre_rules WHERE company_id = 1 AND version_code = 'LF-RACE'"
        ).fetchone()["n"]
    assert count == 1


def test_concurrent_approvals_activate_exactly_once(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    _create(db, ids, version_code="LF-APPROVE-RACE", valid_from="2027-01-01")
    results: list[str] = []

    def attempt() -> None:
        try:
            approve_timbre_rule_version(
                db, 1, ids["approver"], version_code="LF-APPROVE-RACE",
                reason="concurrent approval", overlap_policy="supersede",
            )
            results.append("approved")
        except TimbreRuleAdministrationError as exc:
            results.append(str(exc))

    import threading

    threads = [threading.Thread(target=attempt) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("approved") == 1, results
    events = _audit(db, "TIMBRE_RULE_VERSION_APPROVED")
    assert len(events) == 1, "one approval, one audit event"


# ---------------------------------------------------------------------------
# over HTTP
# ---------------------------------------------------------------------------


def _client(db: ERPDatabase, tmp_path: Path) -> TestClient:
    app = create_erp_fastapi_app(db, PrivateDocumentVault(tmp_path / "vault"))
    return TestClient(app)


def _token(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/token", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_the_endpoints_enforce_role_sod_and_validation(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    client = _client(db, tmp_path)

    payload = {
        "version_code": "LF-2029-CASH",
        "valid_from": "2029-01-01",
        "min_amount_dzd": "5.00",
        "threshold_dzd": "300.00",
        "brackets": BRACKETS,
        "reason": "Finance Law 2029",
    }

    clerk = _token(client, "clerk_a")
    assert client.post("/api/v1/tax/timbre-rules", json=payload, headers=clerk).status_code == 403

    creator = _token(client, "comptable_a")
    created = client.post("/api/v1/tax/timbre-rules", json=payload, headers=creator)
    assert created.status_code == 201, created.text
    assert created.json()["review_state"] == "DRAFT"

    # Self-approval is refused with 403 (authorization), not 422.
    assert client.post(
        "/api/v1/tax/timbre-rules/LF-2029-CASH/approve",
        json={"reason": "self"},
        headers=creator,
    ).status_code == 403

    # An incomplete rule is 422 and never stored.
    bad = dict(payload, version_code="LF-2030", threshold_dzd="")
    assert client.post("/api/v1/tax/timbre-rules", json=bad, headers=creator).status_code == 422

    approver = _token(client, "comptable_b")
    approved = client.post(
        "/api/v1/tax/timbre-rules/LF-2029-CASH/approve",
        json={"reason": "second review", "overlap_policy": "supersede"},
        headers=approver,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "ACTIVE"

    listing = client.get("/api/v1/tax/timbre-rules", headers=approver)
    assert listing.status_code == 200
    codes = {r["version_code"] for r in listing.json()["rules"]}
    assert {"LF-2029-CASH", "LF-2025-CASH-TIMBRE"} <= codes


def test_an_overlap_over_http_is_a_conflict_not_a_bad_request(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    client = _client(db, tmp_path)
    creator = _token(client, "comptable_a")
    approver = _token(client, "comptable_b")

    payload = {
        "version_code": "LF-2031-CASH",
        "valid_from": "2031-01-01",
        "min_amount_dzd": "5.00",
        "threshold_dzd": "300.00",
        "brackets": BRACKETS,
        "reason": "Finance Law 2031",
    }
    assert client.post("/api/v1/tax/timbre-rules", json=payload, headers=creator).status_code == 201
    assert client.post(
        "/api/v1/tax/timbre-rules/LF-2031-CASH/approve",
        json={"reason": "activate the 2031 table"},
        headers=approver,
    ).status_code == 200

    # A second rule claiming part of the same year: well formed, but a conflict.
    second = dict(payload, version_code="LF-2031-CORRECTION", valid_from="2031-07-01")
    assert client.post("/api/v1/tax/timbre-rules", json=second, headers=creator).status_code == 201
    response = client.post(
        "/api/v1/tax/timbre-rules/LF-2031-CORRECTION/approve",
        json={"reason": "approve without closing the old window"},
        headers=approver,
    )
    assert response.status_code == 409, response.text
    assert "TIMBRE_RULE_OVERLAP" in response.json()["detail"]


def test_administration_requires_authentication(tmp_path: Path) -> None:
    db, ids = _build(tmp_path)
    client = _client(db, tmp_path)
    assert client.get("/api/v1/tax/timbre-rules").status_code == 401
    assert client.post("/api/v1/tax/timbre-rules", json={}).status_code == 401


def test_a_cited_rule_cannot_be_deleted_through_any_administration_path(
    tmp_path: Path,
) -> None:
    """There is no delete operation, and the database refuses one anyway."""
    db, ids = _build(tmp_path)
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO supplier_invoices (
                company_id, supplier_id, supplier_invoice_no, normalized_invoice_no,
                invoice_date, payment_method, currency_code, amount_ht_dzd, amount_tva_dzd,
                amount_timbre_dzd, amount_ttc_dzd, workflow_state, payload_sha256,
                created_by_id, invoice_type, fiscal_year, timbre_rule_version, created_at
            ) VALUES (1, 1, 'INV-4', 'INV-4', '2026-07-01', 'CASH', 'DZD', '100.00', '19.00',
                      '5.00', '124.00', 'Posted', 'deadbeef4', 1, 'IN_INVOICE', 2026, 'LF-2025-CASH-TIMBRE', '2026-01-01T00:00:00+00:00')
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM timbre_rules WHERE company_id = 1 AND version_code = 'LF-2025-CASH-TIMBRE'"
            )
    # And the service exposes no deletion at all.
    import dz_erp.localization as localization

    assert not [n for n in dir(localization) if "delete" in n.lower() and "timbre" in n.lower()]


def test_creating_and_approving_is_idempotent_against_a_replayed_request(tmp_path: Path) -> None:
    """A replayed creation is refused; a replayed approval is refused. No duplicates."""
    db, ids = _build(tmp_path)
    _create(db, ids, version_code="LF-REPLAY")
    with pytest.raises(TimbreRuleAdministrationError):
        _create(db, ids, version_code="LF-REPLAY")
    _approve(db, ids, version_code="LF-REPLAY")
    with pytest.raises(TimbreRuleAdministrationError):
        _approve(db, ids, version_code="LF-REPLAY")
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM timbre_rules WHERE version_code = 'LF-REPLAY'"
        ).fetchone()
    assert rows["n"] == 1


def test_a_legacy_database_gains_active_rules_and_drafts_stay_ignored(tmp_path: Path) -> None:
    """The migration marks existing configuration ACTIVE, which is what it was."""
    path = str(tmp_path / "legacy.sqlite3")
    db = ERPDatabase(path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at) "
            "VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO timbre_rules (company_id, version_code, valid_from, valid_to, "
            "brackets_json) VALUES (1, 'PRE-EXISTING', '2025-01-01', NULL, ?)",
            (json.dumps(BRACKETS),),
        )
    # Simulate a database written before the review-state columns existed.
    raw = sqlite3.connect(path)
    for column in ("review_state", "reason", "created_by_id", "created_at",
                   "reviewed_by_id", "reviewed_at"):
        try:
            raw.execute(f"ALTER TABLE timbre_rules DROP COLUMN {column}")
        except sqlite3.OperationalError:
            pass
    raw.commit()
    raw.close()

    reopened = ERPDatabase(path)
    with reopened.transaction() as conn:
        row = conn.execute("SELECT * FROM timbre_rules WHERE version_code = 'PRE-EXISTING'").fetchone()
        assert row["review_state"] == "ACTIVE", "existing configuration stays in force"
        assert row["reviewed_by_id"] is None, "and no reviewer is invented for it"
        assert resolve_timbre_rule(conn, 1, "2026-01-01").version_code == "PRE-EXISTING"
