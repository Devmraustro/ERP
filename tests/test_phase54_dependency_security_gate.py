# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 54 - the dependency-vulnerability CI gate (tools/security_scan_gate.py).

Round 7 closed DEP-1 (risk register P20) by upgrading every pinned package that
carried a published advisory, and turned the one-off scan into a permanent CI
gate. These tests keep the gate honest without network access:

* the policy engine must fail a report containing any advisory that is not
  covered by a valid, unexpired exception, and pass a clean report;
* an exception only counts when it names the package and the advisory (by
  canonical id or alias), carries justification/owner/dates, and has not
  expired - expired, mismatched, over-long or malformed exception state must
  fail closed;
* the committed self-check fixture must still declare a deliberately
  vulnerable pin, and the CI workflow must run the self-check that asserts the
  gate rejects it - a scanner that cannot prove it detects anything is not a
  scanner.

The end-to-end detection proof (pip-audit against the live advisory service)
runs in CI's ``dependency-scan`` job, which must exit nonzero on this fixture;
it cannot run here because unit tests have no network guarantee.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from tools import security_scan_gate as gate

REPO = Path(__file__).resolve().parent.parent


def _finding(package: str, version: str, advisory: str, aliases=(), fixes=()):
    return {
        "name": package,
        "version": version,
        "vulns": [
            {
                "id": advisory,
                "aliases": list(aliases),
                "fix_versions": list(fixes),
                "description": "synthetic",
            }
        ],
    }


def _report(*deps):
    return {"dependencies": list(deps)}


def _exception(package="jinja2", advisory="PYSEC-2021-66", aliases=(), days=30, opened_days_ago=0):
    return {
        "package": package,
        "advisory": advisory,
        "aliases": list(aliases),
        "justification": "fixture justification",
        "owner": "engineering",
        "opened": (date.today() - timedelta(days=opened_days_ago)).isoformat(),
        "expiry": (date.today() + timedelta(days=days)).isoformat(),
    }


def _normalized(*raw_entries):
    """Run the raw entries through the real loader (the tests exercise the
    same normalization the gate applies, including the fail-closed checks)."""
    root = Path(tempfile.mkdtemp(prefix="gate-test-"))
    path = root / "exceptions.json"
    path.write_text(json.dumps({"exceptions": list(raw_entries)}), encoding="utf-8")
    entries, problems = gate.load_exceptions(path)
    assert not problems, problems
    return entries


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


def test_clean_report_passes():
    report = _report({"name": "pypdf", "version": "6.21.4", "vulns": []})
    decision = gate.evaluate_report(report, [], date.today())
    assert decision["decision"] == "pass"
    assert decision["unresolved"] == 0
    assert decision["total_findings"] == 0


def test_unexcepted_finding_fails_with_disposition():
    report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    decision = gate.evaluate_report(report, [], date.today())
    assert decision["decision"] == "fail"
    assert decision["unresolved"] == 1
    item = decision["dispositions"][0]
    assert item["status"] == "unresolved"
    assert item["package"] == "jinja2"
    assert item["advisory"] == "PYSEC-2021-66"
    assert item["fix_versions"] == ["2.11.3"]


def test_valid_exception_is_recorded_not_silently_dropped():
    report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    decision = gate.evaluate_report(report, _normalized(_exception()), date.today())
    assert decision["decision"] == "pass"
    assert decision["excepted"] == 1
    item = decision["dispositions"][0]
    assert item["status"] == "excepted"
    assert item["exception_owner"] == "engineering"


def test_exception_matches_through_cve_alias():
    report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    exception = _exception(advisory="CVE-2020-28493")
    decision = gate.evaluate_report(report, _normalized(exception), date.today())
    assert decision["decision"] == "pass"


def test_expired_exception_fails_closed():
    report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    exception = _exception(days=-30, opened_days_ago=60)
    decision = gate.evaluate_report(report, _normalized(exception), date.today())
    assert decision["decision"] == "fail"
    assert decision["dispositions"][0]["status"] == "expired-exception"


def test_exception_for_another_advisory_does_not_cover_this_one():
    report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    decision = gate.evaluate_report(report, _normalized(_exception(advisory="PYSEC-9999-1")), date.today())
    assert decision["decision"] == "fail"
    assert decision["unresolved"] == 1


def test_exception_for_another_package_does_not_cover_this_one():
    report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    decision = gate.evaluate_report(report, _normalized(_exception(package="starlette")), date.today())
    assert decision["decision"] == "fail"


def test_package_name_canonicalisation_matches_installed_spellings():
    # pdfminer.six is published as "pdfminer-six" in pip-audit output; the
    # exception must match regardless of the spelling used in the entry.
    report = _report(
        _finding("pdfminer-six", "20250327", "PYSEC-2026-1", [], ["20251107"])
    )
    exception = _exception(package="pdfminer.six", advisory="PYSEC-2026-1")
    decision = gate.evaluate_report(report, _normalized(exception), date.today())
    assert decision["decision"] == "pass"


# ---------------------------------------------------------------------------
# Exception file loading (fail-closed)
# ---------------------------------------------------------------------------


def _write_exceptions(tmp_path, entries, raw=None):
    path = tmp_path / "exceptions.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(json.dumps({"exceptions": entries}), encoding="utf-8")
    return path


def test_missing_exception_file_is_reported(tmp_path):
    entries, problems = gate.load_exceptions(tmp_path / "absent.json")
    assert entries == []
    assert problems and "missing" in problems[0]


def test_malformed_json_fails_closed(tmp_path):
    path = _write_exceptions(tmp_path, None, raw="{not json")
    entries, problems = gate.load_exceptions(path)
    assert entries == []
    assert problems


def test_entry_missing_required_fields_is_rejected(tmp_path):
    path = _write_exceptions(tmp_path, [{"package": "jinja2", "advisory": "PYSEC-2021-66"}])
    entries, problems = gate.load_exceptions(path)
    assert entries == []
    assert any("justification" in p for p in problems)


def test_entry_exceeding_maximum_lifetime_is_rejected(tmp_path):
    entry = _exception(days=gate.MAX_EXCEPTION_DAYS + 1)
    path = _write_exceptions(tmp_path, [entry])
    entries, problems = gate.load_exceptions(path)
    assert entries == []
    assert any("maximum lifetime" in p for p in problems)


def test_gate_error_when_exception_state_is_broken(tmp_path, monkeypatch):
    broken = _write_exceptions(tmp_path, None, raw="{oops")
    monkeypatch.setattr(gate, "DEFAULT_EXCEPTIONS", broken)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    assert gate.decide(_report(), report_path) == gate.EXIT_GATE_ERROR


def test_decide_pass_and_fail_exit_codes(tmp_path, monkeypatch):
    empty = _write_exceptions(tmp_path, [])
    monkeypatch.setattr(gate, "DEFAULT_EXCEPTIONS", empty)

    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(_report()), encoding="utf-8")
    assert gate.decide(_report(), clean) == gate.EXIT_CLEAN
    decision = json.loads(clean.with_suffix(".json.decision.json").read_text(encoding="utf-8"))
    assert decision["decision"] == "pass"

    dirty_report = _report(
        _finding("jinja2", "2.11.0", "PYSEC-2021-66", ["CVE-2020-28493"], ["2.11.3"])
    )
    dirty = tmp_path / "dirty.json"
    dirty.write_text(json.dumps(dirty_report), encoding="utf-8")
    assert gate.decide(dirty_report, dirty) == gate.EXIT_UNRESOLVED
    decision = json.loads(dirty.with_suffix(".json.decision.json").read_text(encoding="utf-8"))
    assert decision["decision"] == "fail"
    assert decision["unresolved"] == 1


# ---------------------------------------------------------------------------
# Committed fixture and CI wiring
# ---------------------------------------------------------------------------


def test_selfcheck_fixture_declares_a_vulnerable_pin():
    fixture = REPO / "tools" / "security_scan_selfcheck" / "requirements-fixture.txt"
    text = fixture.read_text(encoding="utf-8")
    pins = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert pins == ["jinja2==2.11.0"], pins


def test_ci_workflow_runs_the_gate_and_its_selfcheck():
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "security_scan_gate.py scan" in workflow
    assert "pip-audit==" in workflow, "the scanner version must be pinned"
    assert "requirements-fixture.txt" in workflow
    assert "sca-report.json" in workflow


def test_exceptions_file_is_present_and_empty():
    data = json.loads(
        (REPO / "tools" / "security_scan_exceptions.json").read_text(encoding="utf-8")
    )
    assert data["exceptions"] == []
