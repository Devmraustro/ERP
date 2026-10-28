#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Turn a JUnit XML report into actionable CI failure output.

A failing test buried in a multi-thousand-line log is not actionable, and a
truncated or unreadable log means a red build tells you nothing about what to
fix.  This script converts the machine-readable JUnit report into:

* one GitHub Actions ``::error::`` annotation per failing or erroring test, so
  the failures appear in the run's annotation list and are retrievable through
  the REST API rather than only in the raw log;
* a Markdown summary written to ``$GITHUB_STEP_SUMMARY`` when that variable is
  set, so the run page shows the counts and the failing test names directly.

Annotations are capped because GitHub drops annotations beyond the per-step
limit; the summary always reports the full count.

Usage::

    python tools/ci_report_failures.py junit.xml

Exit status is ``0`` when the report parses and there is nothing to report, and
``1`` when failures or errors are present, so the CI step fails with a message
that names the broken tests.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple

# GitHub keeps a limited number of annotations per step; anything beyond this is
# silently dropped, so report the cap explicitly rather than losing the signal.
MAX_ANNOTATIONS = 40
MAX_MESSAGE_CHARS = 1400


def _clean(text: str) -> str:
    """Collapse a traceback into a single annotation-safe line."""
    flattened = " ".join((text or "").split())
    if len(flattened) > MAX_MESSAGE_CHARS:
        flattened = flattened[: MAX_MESSAGE_CHARS - 3] + "..."
    return flattened.replace("|", "\\|")


def _first_assertion(text: str) -> str:
    """Pick the most informative line out of a pytest traceback."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("E ") or line.startswith("E\t"):
            return _clean(line[1:].strip())
    for line in reversed(lines):
        if line.startswith(">"):
            return _clean(line)
    return _clean(lines[-1]) if lines else "no message"


def _detail(text: str) -> str:
    """A longer, still single-line digest of a failure.

    A bare ``KeyError: 'invoice_number'`` does not say which side moved, so the
    annotation also carries the failing source line and the tail of the
    traceback, where pytest's assertion rewriting puts the useful context.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    parts: List[str] = []

    for line in reversed(lines):
        if line.startswith("E ") or line.startswith("E\t"):
            parts.append(line[1:].strip())
            break

    for line in reversed(lines):
        if line.startswith(">"):
            parts.append(f"at {line.lstrip('> ').strip()}")
            break

    # Keep the last few traceback frames so the failing call site is visible.
    frames = [line for line in lines if line.startswith(("tests/", "dz_erp/", "tools/"))]
    if frames:
        parts.append(f"frames: {' | '.join(frames[-3:])}")

    if not parts:
        parts.append(_clean(lines[-1]) if lines else "no message")
    return _clean(" :: ".join(parts))


def collect_failures(report: Path) -> Tuple[List[Tuple[str, str]], dict]:
    """Return ([(test id, message)], counts) parsed from a JUnit XML report."""
    tree = ET.parse(report)  # raises on malformed XML - surfaced by the caller
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    failures: List[Tuple[str, str]] = []
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": "0"}
    for suite in suites:
        counts["tests"] += int(suite.get("tests", 0) or 0)
        counts["failures"] += int(suite.get("failures", 0) or 0)
        counts["errors"] += int(suite.get("errors", 0) or 0)
        counts["skipped"] += int(suite.get("skipped", 0) or 0)

        for case in suite.iter("testcase"):
            identifier = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
            for problem in case:
                if problem.tag in ("failure", "error"):
                    failures.append((identifier, _detail(problem.text or "")))
                    break

    if not counts["time"] or counts["time"] == "0":
        try:
            counts["time"] = f"{float(root.get('time', 0) or 0):.2f}"
        except ValueError:
            counts["time"] = "0"
    return failures, counts


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <junit.xml>", file=sys.stderr)
        return 2

    report = Path(argv[1])
    if not report.is_file():
        print(f"::error::JUnit report not found: {report}", file=sys.stderr)
        return 2

    try:
        failures, counts = collect_failures(report)
    except ET.ParseError as exc:
        print(f"::error::Could not parse JUnit report {report}: {exc}", file=sys.stderr)
        return 2

    print(
        f"Test report: {counts['tests']} tests, {counts['failures']} failures, "
        f"{counts['errors']} errors, {counts['skipped']} skipped, {counts['time']}s"
    )

    if not failures:
        print("No failing or erroring tests reported.")
        _write_summary(counts, [])
        return 0

    reported = failures[:MAX_ANNOTATIONS]
    for identifier, message in reported:
        print(f"::error::FAILED {identifier} — {message}")

    if len(failures) > len(reported):
        print(
            f"::warning::{len(failures) - len(reported)} further failing tests are not annotated "
            f"(GitHub caps annotations per step at {MAX_ANNOTATIONS}). See the JUnit report artifact."
        )

    _write_summary(counts, failures)
    return 1


def _write_summary(counts: dict, failures: List[Tuple[str, str]]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [
        "### Test results",
        "",
        f"- Tests: **{counts['tests']}**",
        f"- Failures: **{counts['failures']}**",
        f"- Errors: **{counts['errors']}**",
        f"- Skipped: **{counts['skipped']}**",
        f"- Duration: **{counts['time']}s**",
        "",
    ]
    if failures:
        lines += [f"#### {len(failures)} failing test(s)", "", "| Test | First assertion |", "| :--- | :--- |"]
        lines += [f"| `{identifier}` | {message} |" for identifier, message in failures]
        lines.append("")
    else:
        lines += ["All tests passed.", ""]
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:  # pragma: no cover - the summary is best-effort
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
