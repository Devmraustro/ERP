# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 55 - Docker image security gate (Trivy) policy enforcement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run_gate(report_path: Path) -> subprocess.CompletedProcess:
    code = r"""
import json, sys
try:
    with open(%r, encoding="utf-8") as f:
        report = json.load(f)
except Exception as e:
    print(f"FAIL: missing or malformed vulns.json: {e}", file=sys.stderr)
    sys.exit(1)
if not isinstance(report, dict):
    print("FAIL: vulns.json must be a JSON object", file=sys.stderr)
    sys.exit(1)

def has_fix(v):
    fv = (v.get("FixedVersion") or "").strip()
    return bool(fv)

critical = []
fixable_high = []
unfixable_high = []
medium = []
low = []
for result in report.get("Results", []) or []:
    if not isinstance(result, dict):
        continue
    target = result.get("Target", "")
    for vuln in result.get("Vulnerabilities") or []:
        if not isinstance(vuln, dict):
            continue
        sev = (vuln.get("Severity") or "UNKNOWN").upper()
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

print(f"image scan: critical={len(critical)} high_fixable={len(fixable_high)} high_unfixable={len(unfixable_high)} medium={len(medium)} low={len(low)}")
if critical:
    print("FAIL: Critical vulnerabilities present:")
    for target, v in critical:
        print(f"  {v.get('VulnerabilityID')} {target} -> {v.get('FixedVersion')}")
    sys.exit(1)
if fixable_high:
    print("FAIL: High vulnerabilities with an available fix:")
    for target, v in fixable_high:
        print(f"  {v.get('VulnerabilityID')} {target} -> {v.get('FixedVersion')}")
    sys.exit(1)
print("Gate: PASS (no Critical, no fixable High). %d High without a fix, %d Medium, %d Low are reported above and retained in vulns.json/vulns.sarif." % (len(unfixable_high), len(medium), len(low)))
""" % str(report_path).replace("\\", "\\\\")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


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


def test_empty_results_passes_but_structure_ok() -> None:
    report = {"Results": []}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "vulns.json"
        p.write_text(json.dumps(report), encoding="utf-8")
        r = _run_gate(p)
    assert r.returncode == 0
