#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Reproducible dependency-vulnerability gate for the pinned dependency set.

Why this exists
---------------
Round 7 (risk register P20 / DEP-1) ran the first SCA scan of the hash-pinned
dependency set and upgraded every package with a published advisory. A one-off
upgrade does not stay clean: new advisories accumulate against the same pins,
silently, between releases. This gate makes the scan a CI precondition instead
of a periodic manual exercise.

What it does
------------
``scan`` mode runs a pinned ``pip-audit`` executable against a requirements
file (by default the committed, fully hash-pinned ``requirements.lock.hashes.txt``
with ``--no-deps --disable-pip``, so the audit covers exactly the declared
transitive closure and nothing the local environment happens to contain), then
applies the documented exception policy to the machine-readable report:

* every reported advisory must either be fixed at the pinned version (pip-audit
  only reports advisories that affect the pinned version) or covered by a
  valid, unexpired exception in ``tools/security_scan_exceptions.json``;
* an exception is valid only if it names the package, an advisory identifier
  (canonical id or alias), a justification, an owner and an expiry date, and
  ``today <= expiry``. Expired, mismatched or empty exceptions are ignored and
  reported - never silently honoured;
* the decision (exit status, per-finding disposition) is written next to the
  report as ``<report>.decision.json`` so CI artifacts carry the evidence;
* exit codes: ``0`` clean or fully excepted, ``1`` unresolved applicable
  advisories, ``2`` gate error (missing tool, malformed report, unreadable
  exception file). A gate error must fail the CI step: an unscanned tree is
  not a passing tree.

The severity policy is deliberately simple: zero unresolved advisories in the
runtime dependency set. pip-audit reports only advisories with a known affected
range; there is no severity threshold to game and no way to "accidentally"
ignore a transitive dependency, because the scanned file enumerates the whole
closure. A clean scan is necessary, not sufficient: it proves no *known*
advisory applies; it says nothing about undisclosed vulnerabilities.

Exception process
-----------------
1. Open a pull request adding an entry to ``tools/security_scan_exceptions.json``
   with: package, advisory (canonical id), aliases, justification (why the fix
   cannot be taken now), owner (role responsible), ``opened`` and ``expiry``
   (ISO dates; expiry at most 90 days after ``opened``).
2. The CI gate honours the exception until ``expiry`` inclusive, then starts
   failing again - renewal requires a fresh, reviewed entry.
3. Every exception is listed in ``docs/DEPENDENCY_AND_LICENSE_REGISTER.md``
   section 4.5 with the same identifiers, so the register stays the source of
   truth.

Usage::

    python tools/security_scan_gate.py scan
    python tools/security_scan_gate.py scan --requirements <file> --report <out.json>
    python tools/security_scan_gate.py evaluate --report <out.json>

``scan`` requires a ``pip-audit`` executable (found on ``PATH`` or given via
``--pip-audit``) and network access to the vulnerability service. ``evaluate``
is offline: it re-applies the policy to an existing report, which is what the
unit tests exercise without network access.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REQUIREMENTS = REPO_ROOT / "requirements.lock.hashes.txt"
DEFAULT_EXCEPTIONS = REPO_ROOT / "tools" / "security_scan_exceptions.json"
DEFAULT_PIP_AUDIT_VERSION = "2.10.1"

EXIT_CLEAN = 0
EXIT_UNRESOLVED = 1
EXIT_GATE_ERROR = 2

# The maximum lifetime of one exception entry, in days. Long-lived exceptions
# are how a real vulnerability becomes permanent background noise; the point
# of the limit is to force an explicit re-review rather than an evergreen
# suppression.
MAX_EXCEPTION_DAYS = 90


def _canonical(name: str) -> str:
    return name.replace("_", "-").replace(".", "-").lower()


def load_exceptions(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read the exception file; return (entries, problems).

    Malformed entries are reported as problems and skipped - fail closed, not
    crashed: a typo in one entry must not disable the whole gate.
    """
    problems: List[str] = []
    if not path.exists():
        return [], [f"exception file missing: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"exception file unreadable: {path}: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        return [], [f"exception file must be an object with an 'exceptions' list: {path}"]
    entries: List[Dict[str, Any]] = []
    required = ("package", "advisory", "justification", "owner", "opened", "expiry")
    for index, raw in enumerate(data["exceptions"]):
        if not isinstance(raw, dict):
            problems.append(f"exception #{index} is not an object")
            continue
        missing = [key for key in required if not str(raw.get(key) or "").strip()]
        if missing:
            problems.append(f"exception #{index} is missing {', '.join(missing)}")
            continue
        try:
            expiry = date.fromisoformat(str(raw["expiry"]).strip())
            opened = date.fromisoformat(str(raw["opened"]).strip())
        except ValueError as exc:
            problems.append(f"exception #{index} has an invalid ISO date: {exc}")
            continue
        if (expiry - opened).days > MAX_EXCEPTION_DAYS:
            problems.append(
                f"exception #{index} ({raw['advisory']}) exceeds the "
                f"{MAX_EXCEPTION_DAYS}-day maximum lifetime"
            )
            continue
        if expiry < opened:
            problems.append(f"exception #{index} expires before it opens")
            continue
        entry = dict(raw)
        entry["_package"] = _canonical(str(raw["package"]))
        entry["_advisory_ids"] = {
            str(raw["advisory"]).strip(),
            *(str(a).strip() for a in raw.get("aliases", []) or []),
        }
        entry["_expiry"] = expiry
        entries.append(entry)
    return entries, problems


def evaluate_report(
    report: Dict[str, Any],
    exceptions: List[Dict[str, Any]],
    today: date,
) -> Dict[str, Any]:
    """Apply the exception policy to a pip-audit JSON report.

    Returns a machine-readable decision dictionary; see module docstring for
    the schema and the policy rationale.
    """
    dispositions: List[Dict[str, Any]] = []
    for dep in report.get("dependencies", []):
        package = str(dep.get("name") or "")
        version = str(dep.get("version") or "")
        for vuln in dep.get("vulns", []):
            ids = [str(vuln.get("id") or "").strip()]
            ids.extend(str(a).strip() for a in vuln.get("aliases", []) or [])
            fix_versions = [str(f) for f in vuln.get("fix_versions", []) or []]
            matched = None
            for entry in exceptions:
                if entry["_package"] != _canonical(package):
                    continue
                if entry["_advisory_ids"] & set(ids):
                    if today <= entry["_expiry"]:
                        matched = entry
                        break
                    # An expired match is recorded as expired, not silently
                    # ignored: the report must show why the advisory failed.
                    dispositions.append(
                        {
                            "package": package,
                            "version": version,
                            "advisory": vuln.get("id"),
                            "aliases": vuln.get("aliases", []),
                            "fix_versions": fix_versions,
                            "status": "expired-exception",
                            "exception_expiry": str(entry["_expiry"]),
                        }
                    )
                    matched = "expired"
                    break
            if matched is not None and matched != "expired":
                dispositions.append(
                    {
                        "package": package,
                        "version": version,
                        "advisory": vuln.get("id"),
                        "aliases": vuln.get("aliases", []),
                        "fix_versions": fix_versions,
                        "status": "excepted",
                        "exception_owner": matched.get("owner"),
                        "exception_expiry": str(matched["_expiry"]),
                    }
                )
                continue
            if matched is not None:  # expired exception, already recorded
                continue
            dispositions.append(
                {
                    "package": package,
                    "version": version,
                    "advisory": vuln.get("id"),
                    "aliases": vuln.get("aliases", []),
                    "fix_versions": fix_versions,
                    "status": "unresolved",
                }
            )
    unresolved = [d for d in dispositions if d["status"] != "excepted"]
    excepted = [d for d in dispositions if d["status"] == "excepted"]
    return {
        "scanned_packages": len(report.get("dependencies", [])),
        "total_findings": len(dispositions),
        "excepted": len(excepted),
        "unresolved": len(unresolved),
        "dispositions": dispositions,
        "policy": (
            "zero unresolved advisories against the pinned dependency set; "
            "exceptions require package + advisory + justification + owner + "
            f"expiry (max {MAX_EXCEPTION_DAYS} days) and are enforced by expiry date"
        ),
        "decision": "pass" if not unresolved else "fail",
    }


def find_pip_audit(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    from shutil import which

    found = which("pip-audit")
    if not found:
        raise SystemExit(
            "pip-audit executable not found on PATH; install the pinned "
            f"scanner with: pip install pip-audit=={DEFAULT_PIP_AUDIT_VERSION}"
        )
    return found


def run_scan(requirements: Path, report_path: Path, pip_audit: str) -> int:
    """Run pip-audit against the requirements file and evaluate the policy."""
    command = [
        pip_audit,
        "--requirement",
        str(requirements),
        "--no-deps",
        "--disable-pip",
        "--format",
        "json",
        "--output",
        str(report_path),
    ]
    print("scan command:", " ".join(command), flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode not in (0, 1):
        print(
            f"pip-audit exited {completed.returncode} (2 = usage error); treating as a gate error",
            file=sys.stderr,
        )
        return EXIT_GATE_ERROR
    try:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"scan report unreadable: {report_path}: {exc}", file=sys.stderr)
        return EXIT_GATE_ERROR
    return decide(report, report_path)


def decide(report: Dict[str, Any], report_path: Path) -> int:
    entries, problems = load_exceptions(DEFAULT_EXCEPTIONS)
    for problem in problems:
        print(f"exception-file problem (fail closed): {problem}", file=sys.stderr)
    if problems:
        return EXIT_GATE_ERROR
    decision = evaluate_report(report, entries, date.today())
    decision_path = report_path.with_suffix(report_path.suffix + ".decision.json")
    decision_path.write_text(
        json.dumps(decision, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"scanned distributions: {decision['scanned_packages']}; "
        f"findings: {decision['total_findings']} "
        f"(excepted: {decision['excepted']}, unresolved: {decision['unresolved']})"
    )
    for item in decision["dispositions"]:
        print(
            f"  {item['status']}: {item['package']}=={item['version']} "
            f"{item['advisory']} fix_versions={item['fix_versions']}"
        )
    print(f"decision: {decision['decision']} (written {decision_path.name})")
    return EXIT_CLEAN if decision["decision"] == "pass" else EXIT_UNRESOLVED


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    scan = sub.add_parser("scan", help="run pip-audit and apply the policy")
    scan.add_argument(
        "--requirements",
        type=Path,
        default=DEFAULT_REQUIREMENTS,
        help="requirements file to audit (default: the hash-pinned lockfile)",
    )
    scan.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "sca-report.json",
        help="where to write the pip-audit JSON report",
    )
    scan.add_argument(
        "--pip-audit",
        default=None,
        help="explicit path to the pip-audit executable (default: PATH lookup)",
    )

    evaluate = sub.add_parser(
        "evaluate", help="apply the policy to an existing pip-audit JSON report (offline)"
    )
    evaluate.add_argument("--report", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.mode == "scan":
        return run_scan(args.requirements, args.report, find_pip_audit(args.pip_audit))
    try:
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"report unreadable: {args.report}: {exc}", file=sys.stderr)
        return EXIT_GATE_ERROR
    return decide(report, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
