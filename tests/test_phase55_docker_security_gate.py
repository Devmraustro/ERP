# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 55 - Docker image security gate (Trivy) policy enforcement."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

GATE_CODE = '''
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        report = json.load(f)
except Exception as e:
    print("FAIL: missing or malformed vulns.json: " + str(e), file=sys.stderr)
    sys.exit(1)
if not isinstance(report, dict):
    print("FAIL: vulns.json must be a JSON object", file=sys.stderr)
    sys.exit(1)
if "Results" not in report:
    print("FAIL: vulns.json missing Results key (incomplete scan)", file=sys.stderr)
    sys.exit(1)
if not isinstance(report["Results"], list):
    print("FAIL: vulns.json Results must be a list", file=sys.stderr)
    sys.exit(1)

def has_fix(v):
    fv = (v.get("FixedVersion") or "").strip()
    return bool(fv)

EXCEPTED = {
    ("CVE-2025-47273", "setuptools", "70.3.0", "Python", "HIGH"),
}

def is_excepted(vuln, target):
    vid = vuln.get("VulnerabilityID", "")
    pkg = vuln.get("PkgName", "")
    ver = vuln.get("InstalledVersion", "")
    sev = (vuln.get("Severity") or "").upper()
    key = (vid, pkg, ver, target, sev)
    return key in EXCEPTED

critical = []
fixable_high = []
unfixable_high = []
medium = []
low = []
excepted = []
for result in report["Results"]:
    if not isinstance(result, dict):
        print("FAIL: vulns.json contains a non-object Result entry (incomplete scan)", file=sys.stderr)
        sys.exit(1)
    if "Target" not in result:
        print("FAIL: vulns.json Result missing Target (incomplete scan)", file=sys.stderr)
        sys.exit(1)
    target = result.get("Target", "")
    vulns_list = result.get("Vulnerabilities")
    if vulns_list is not None and not isinstance(vulns_list, list):
        print("FAIL: vulns.json Vulnerabilities must be a list or null", file=sys.stderr)
        sys.exit(1)
    for vuln in vulns_list or []:
        if not isinstance(vuln, dict):
            print("FAIL: vulns.json contains a non-object Vulnerability entry (incomplete scan)", file=sys.stderr)
            sys.exit(1)
        if "VulnerabilityID" not in vuln or "Severity" not in vuln:
            print("FAIL: vulns.json Vulnerability missing required fields (incomplete scan)", file=sys.stderr)
            sys.exit(1)
        sev = (vuln.get("Severity") or "UNKNOWN").upper()
        if is_excepted(vuln, target):
            excepted.append((target, vuln))
            continue
        if sev == "CRITICAL":
            critical.append((target, vuln))
        elif sev == "HIGH":
            if has_fix(vuln):
                fixable_high.append((target, vuln))
            else:
                unfixable_high.append((target, vuln))
        elif sev == "MEDIUM":
            medium.append((target, vuln))
        elif sev == "LOW":
            low.append((target, vuln))

print("image scan: critical=%d high_fixable=%d high_unfixable=%d medium=%d low=%d excepted=%d" % (len(critical), len(fixable_high), len(unfixable_high), len(medium), len(low), len(excepted)))
if critical:
    print("FAIL: Critical vulnerabilities present:")
    for target, v in critical:
        print("  " + str(v.get("VulnerabilityID")) + " " + target + " -> " + str(v.get("FixedVersion")))
    sys.exit(1)
if fixable_high:
    print("FAIL: High vulnerabilities with an available fix:")
    for target, v in fixable_high:
        print("  " + str(v.get("VulnerabilityID")) + " " + target + " -> " + str(v.get("FixedVersion")))
    sys.exit(1)
print("Gate: PASS (no Critical, no fixable High). %d High without a fix, %d Medium, %d Low are reported above and retained in vulns.json/vulns.sarif." % (len(unfixable_high), len(medium), len(low)))
'''


def _run_gate(report_path: Path) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        gate_file = Path(d) / "gate.py"
        gate_file.write_text(GATE_CODE, encoding="utf-8")
        return subprocess.run([sys.executable, str(gate_file), str(report_path)], capture_output=True, text=True)


def test_unfixable_critical_fails() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [{"VulnerabilityID": "CVE-1", "Severity": "CRITICAL"}]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_fixable_critical_fails() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [{"VulnerabilityID": "CVE-2", "Severity": "CRITICAL", "FixedVersion": "1.2.3"}]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_multiple_criticals_fail() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-3", "Severity": "CRITICAL"},
        {"VulnerabilityID": "CVE-4", "Severity": "CRITICAL", "FixedVersion": "x"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_fixable_high_fails() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [{"VulnerabilityID": "CVE-5", "Severity": "HIGH", "FixedVersion": "1.0.1"}]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_unfixable_high_only_passes() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [{"VulnerabilityID": "CVE-6", "Severity": "HIGH"}]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode == 0
    assert "High without a fix" in r.stdout


def test_medium_and_low_pass_and_reported() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-7", "Severity": "MEDIUM"},
        {"VulnerabilityID": "CVE-8", "Severity": "LOW"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode == 0


def test_missing_json_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "missing.json"
        r = _run_gate(p)
    assert r.returncode != 0


def test_malformed_json_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text("{not json", encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_valid_zero_findings_passes() -> None:
    report = {"SchemaVersion": 2, "Results": []}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode == 0


def test_missing_results_key_fails_closed() -> None:
    report = {"SchemaVersion": 2}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_null_results_fails_closed() -> None:
    report = {"Results": None}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_non_list_results_fails_closed() -> None:
    report = {"Results": "not-a-list"}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_non_object_result_entry_fails_closed() -> None:
    report = {"Results": ["not-an-object"]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_result_missing_target_fails_closed() -> None:
    report = {"Results": [{"Vulnerabilities": []}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_non_list_vulnerabilities_fails_closed() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": "not-a-list"}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_non_object_vulnerability_entry_fails_closed() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": ["not-an-object"]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_vulnerability_missing_required_fields_fails_closed() -> None:
    report = {"Results": [{"Target": "t", "Vulnerabilities": [{"PkgName": "x"}]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_valid_zero_findings_with_null_vulnerabilities_passes() -> None:
    report = {"SchemaVersion": 2, "Results": [{"Target": "t", "Vulnerabilities": None}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode == 0


def test_cve_exception_applies_to_exact_scope() -> None:
    report = {"Results": [{"Target": "Python", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-2025-47273", "PkgName": "setuptools", "InstalledVersion": "70.3.0", "Severity": "HIGH", "FixedVersion": "78.1.1"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode == 0
    assert "excepted=1" in r.stdout


def test_cve_exception_does_not_apply_to_different_package() -> None:
    report = {"Results": [{"Target": "Python", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-2025-47273", "PkgName": "other-pkg", "InstalledVersion": "70.3.0", "Severity": "HIGH", "FixedVersion": "78.1.1"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_cve_exception_does_not_apply_to_different_target() -> None:
    report = {"Results": [{"Target": "other-target", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-2025-47273", "PkgName": "setuptools", "InstalledVersion": "70.3.0", "Severity": "HIGH", "FixedVersion": "78.1.1"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_cve_exception_does_not_apply_to_different_version() -> None:
    report = {"Results": [{"Target": "Python", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-2025-47273", "PkgName": "setuptools", "InstalledVersion": "99.0.0", "Severity": "HIGH", "FixedVersion": "78.1.1"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode != 0


def test_excluded_cve_remains_in_raw_report() -> None:
    # The gate excludes from evaluation only; raw report still contains it.
    report = {"Results": [{"Target": "Python", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-2025-47273", "PkgName": "setuptools", "InstalledVersion": "70.3.0", "Severity": "HIGH", "FixedVersion": "78.1.1"},
    ]}]}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        # Verify the CVE is in the raw file
        raw = json.loads(p.read_text(encoding="utf-8"))
        found = False
        for result in raw.get("Results", []):
            for vuln in result.get("Vulnerabilities", []):
                if vuln.get("VulnerabilityID") == "CVE-2025-47273":
                    found = True
        assert found, "CVE must remain in raw report"
        r = _run_gate(p)
    assert r.returncode == 0
