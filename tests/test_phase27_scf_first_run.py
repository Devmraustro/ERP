# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 27 — SCF chart of accounts initialization at first run.

What this phase must prove:

1. **First run seeds the official chart**: ``bootstrap_first_run`` loads the
   vendored Odoo 18.0 ``l10n_dz`` chart (never an invented substitute),
   validates it exactly (account count, key accounts 401/411/380/642,
   company defaults 411/401, the 8 standard taxes, the timbre rule), writes
   the ``LOAD_CHART_TEMPLATE`` audit event, then creates the admin.
2. **Missing bundled data fails closed**: with the vendor source absent, a
   ``SetupBlockerError`` is raised *before any database write* — no half
   database, no silent substitution — and the remediation (the
   ``ERP_VENDOR_DATA_DIR`` override) is named in the message.
3. **An interrupted first run resumes**: a loader failure (which rolls back
   atomically, audit event included) is compensated by removing the
   unpinned company; a failure *after* the audit event committed leaves a
   resumable state that a retry continues from — the same company id, the
   idempotent chart reload, no duplicate rows.
4. **The chart load is idempotent**: reloading the chart neither duplicates
   accounts nor corrupts the audit chain.

All data is synthetic; the chart source itself is the vendored official
Odoo template (MPL-2.0) already reviewed in the dependency register.
"""

import sqlite3
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase
from dz_erp.desktop.service import SetupBlockerError, bootstrap_first_run, has_any_user
from dz_erp.localization import inspect_upstream_odoo_l10n_dz, load_official_odoo_chart

_ADMIN = ("admin", "Smoke#2026x")


def _bootstrap(db_path: Path, *, company_name: str = "Société Fictive SARL") -> dict:
    return bootstrap_first_run(
        db_path,
        company_name=company_name,
        admin_username=_ADMIN[0],
        admin_password=_ADMIN[1],
    )


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 1. first run seeds the official chart and validates it
# ----------------------------------------------------------------------


def test_first_run_seeds_official_chart_exactly(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "erp.sqlite3"
    result = _bootstrap(db_path)

    upstream = inspect_upstream_odoo_l10n_dz()
    expected_accounts = len(upstream["accounts_by_code"])
    assert result["accounts_loaded"] == expected_accounts
    assert expected_accounts >= 50  # a real chart, not a stub

    assert len(_rows(db_path, "SELECT id FROM users")) == 1
    companies = _rows(
        db_path, "SELECT id, default_receivable_code, default_payable_code FROM companies"
    )
    assert len(companies) == 1
    assert companies[0]["id"] == result["company_id"]
    assert companies[0]["default_receivable_code"] == "411"
    assert companies[0]["default_payable_code"] == "401"

    accounts = _rows(
        db_path, "SELECT code FROM accounts WHERE company_id = ?", (result["company_id"],)
    )
    assert len(accounts) == expected_accounts  # exact: no drift, no duplicates
    assert {a["code"] for a in accounts} >= {"401", "411", "380", "642"}

    # The vendored CSV already defines the perpetual-inventory pair, so the
    # loader's defensive extra inserts are no-ops here — the exact count
    # above is the pin that catches any source change.
    assert "380" in upstream["accounts_by_code"]
    assert "642" in upstream["accounts_by_code"]

    taxes = _rows(db_path, "SELECT xml_id FROM taxes WHERE company_id = ?", (result["company_id"],))
    assert len(taxes) == 8

    timbre = _rows(
        db_path,
        "SELECT version_code FROM timbre_rules WHERE company_id = ?",
        (result["company_id"],),
    )
    assert [t["version_code"] for t in timbre] == ["LF-2025-CASH-TIMBRE"]

    events = _rows(
        db_path,
        "SELECT actor_user_id, action_type, target_id FROM audit_events "
        "WHERE action_type = 'LOAD_CHART_TEMPLATE'",
    )
    assert len(events) == 1
    assert events[0]["actor_user_id"] is None  # system bootstrap, not a user
    assert events[0]["target_id"] == str(result["company_id"])


def test_repeated_first_run_after_success_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "erp.sqlite3"
    first = _bootstrap(db_path)
    with pytest.raises(RuntimeError, match="already contains users"):
        _bootstrap(db_path, company_name="Autre Société")
    # Nothing changed: still one company, one user, one chart.
    assert len(_rows(db_path, "SELECT id FROM companies")) == 1
    assert len(_rows(db_path, "SELECT id FROM users")) == 1
    assert (
        len(
            _rows(
                db_path,
                "SELECT id FROM accounts WHERE company_id = ?",
                (first["company_id"],),
            )
        )
        == first["accounts_loaded"]
    )
    assert has_any_user(db_path) is True


# ----------------------------------------------------------------------
# 2. missing bundled data fails closed before any write
# ----------------------------------------------------------------------


def test_missing_vendor_data_blocks_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dz_erp.localization as localization

    db_path = tmp_path / "data" / "erp.sqlite3"
    with monkeypatch.context() as m:
        # bootstrap_first_run resolves the vendor dir through
        # dz_erp.localization at call time, so this patch intercepts it.
        m.setattr(
            localization, "vendor_data_dir", lambda: tmp_path / "absent-vendor", raising=True
        )
        with pytest.raises(SetupBlockerError) as blocker:
            _bootstrap(db_path)
        # The remediation must be actionable, not a bare "not found".
        assert "ERP_VENDOR_DATA_DIR" in str(blocker.value)
        # Fail closed: no database file, therefore zero rows of anything.
        assert not db_path.exists()

    # With the source restored (the real vendored data), the very same
    # first run succeeds.
    result = _bootstrap(db_path)
    assert result["accounts_loaded"] >= 50
    assert db_path.exists()


# ----------------------------------------------------------------------
# 3. interrupted first run: loader failure, admin failure, resume
# ----------------------------------------------------------------------


def test_loader_failure_rolls_back_to_clean_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dz_erp.localization as localization

    db_path = tmp_path / "data" / "erp.sqlite3"

    def broken_loader(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("simulated chart loader failure")

    with monkeypatch.context() as m:
        m.setattr(localization, "load_official_odoo_chart", broken_loader, raising=True)
        with pytest.raises(RuntimeError, match="simulated chart loader failure"):
            _bootstrap(db_path)

    # The loader is atomic (its audit event rolls back with it), so the
    # compensation can remove the unpinned company: the database is left
    # with zero users, zero companies, zero chart rows.
    assert len(_rows(db_path, "SELECT id FROM users")) == 0
    assert len(_rows(db_path, "SELECT id FROM companies")) == 0
    assert len(_rows(db_path, "SELECT id FROM accounts")) == 0
    assert len(_rows(db_path, "SELECT id FROM account_groups")) == 0
    assert len(_rows(db_path, "SELECT id FROM taxes")) == 0
    assert len(_rows(db_path, "SELECT id FROM timbre_rules")) == 0

    # The very same first run then succeeds from a clean state.
    result = _bootstrap(db_path)
    assert result["accounts_loaded"] >= 50


def test_admin_failure_leaves_resumable_state_and_retry_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dz_erp.auth import AuthManager

    db_path = tmp_path / "data" / "erp.sqlite3"

    def failing_create(self: AuthManager, **kwargs: object) -> dict:
        raise RuntimeError("simulated admin creation failure")

    with monkeypatch.context() as m:
        m.setattr(AuthManager, "create_user", failing_create, raising=True)
        with pytest.raises(RuntimeError, match="simulated admin creation failure"):
            _bootstrap(db_path)

    # The chart commit audit-pinned the company, so it cannot be deleted
    # without tampering: the state is *resumable* instead — chart present,
    # no users, and a retry continues on the same company.
    assert has_any_user(db_path) is False
    companies = _rows(db_path, "SELECT id FROM companies")
    assert len(companies) == 1
    pinned_company_id = companies[0]["id"]
    assert (
        len(
            _rows(
                db_path,
                "SELECT id FROM accounts WHERE company_id = ?",
                (pinned_company_id,),
            )
        )
        >= 50
    )
    assert len(_rows(db_path, "SELECT id FROM users")) == 0

    result = _bootstrap(db_path)
    assert result["company_id"] == pinned_company_id  # resumed, not duplicated
    assert len(_rows(db_path, "SELECT id FROM companies")) == 1
    assert len(_rows(db_path, "SELECT id FROM users")) == 1
    assert (
        len(
            _rows(
                db_path,
                "SELECT id FROM accounts WHERE company_id = ?",
                (pinned_company_id,),
            )
        )
        == result["accounts_loaded"]
    )


def test_multiple_orphaned_companies_fail_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "erp.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        for i in (1, 2):
            conn.execute(
                "INSERT INTO companies (code, name, nif, created_at) VALUES (?, ?, NULL, ?)",
                (f"ORPH-{i}", f"Orpheline {i}", "2026-01-01T00:00:00+00:00"),
            )
    with pytest.raises(SetupBlockerError, match="partially initialized companies"):
        _bootstrap(db_path)


# ----------------------------------------------------------------------
# 4. the chart load itself is idempotent
# ----------------------------------------------------------------------


def test_double_chart_load_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "erp.sqlite3"
    result = _bootstrap(db_path)
    company_id = result["company_id"]

    load_official_odoo_chart(ERPDatabase(str(db_path)), company_id, apply_fixes_immediately=True)

    assert (
        len(
            _rows(
                db_path,
                "SELECT id FROM accounts WHERE company_id = ?",
                (company_id,),
            )
        )
        == result["accounts_loaded"]
    )
    assert len(_rows(db_path, "SELECT id FROM taxes WHERE company_id = ?", (company_id,))) == 8
    events = _rows(
        db_path,
        "SELECT id FROM audit_events WHERE action_type = 'LOAD_CHART_TEMPLATE' ORDER BY id",
    )
    assert len(events) == 2  # both loads are honest, audited facts
    chain = ERPDatabase(str(db_path)).verify_audit_chain(company_id)
    assert chain["valid"] is True


# ----------------------------------------------------------------------
# 5. frozen-resource resolution (what is testable without a frozen build)
# ----------------------------------------------------------------------


def test_vendor_data_dir_resolution_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override wins; a frozen build resolves its bundled copy."""
    import sys

    import dz_erp.localization as localization

    # 1. An explicit override always wins - a packager who moves the data
    #    must say so.
    override = tmp_path / "override-vendor"
    override.mkdir()
    monkeypatch.setenv(localization.VENDOR_DATA_DIR_ENV, str(override))
    assert localization.vendor_data_dir() == override

    # 2. Frozen build: the bundled vendor tree next to the executable payload.
    monkeypatch.delenv(localization.VENDOR_DATA_DIR_ENV, raising=False)
    bundled_root = tmp_path / "frozen-payload"
    (bundled_root / "vendor").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled_root), raising=False)
    assert localization.vendor_data_dir() == bundled_root / "vendor"

    # 3. Frozen but the data was not bundled: the checkout copy keeps
    #    development runs working; a genuinely missing source then fails
    #    closed in the pre-flight check with the documented remediation.
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty-payload"), raising=False)
    assert localization.vendor_data_dir() == localization.REPO_ROOT / "vendor"
