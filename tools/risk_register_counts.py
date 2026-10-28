#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Derive the risk-register summary table from the rows themselves.

The summary in `docs/RISK_REGISTER.md` is a *view* of the rows, not an
independent statement of fact. Hand-editing it is how a register ends up
claiming "12 open" while the table lists nine, so this script parses the rows,
recomputes the counts, and either reports a mismatch or rewrites the table.

Usage:
    python tools/risk_register_counts.py            # check, exit 1 on drift
    python tools/risk_register_counts.py --write    # rewrite the summary table
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REGISTER = Path(__file__).resolve().parent.parent / "docs" / "RISK_REGISTER.md"
SEVERITIES = ["Critical", "High", "Medium", "Low"]
STATUSES = ["Open", "Mitigated", "Accepted", "Closed"]


def parse_rows(text: str) -> List[Dict[str, str]]:
    """Extract the 9-column risk rows (id, risk, severity, likelihood, ... status)."""
    rows: List[Dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 9:
            continue
        ident = cells[0]
        if not re.fullmatch(r"[A-Z]{1,2}\d{1,2}", ident):
            continue
        status_text = cells[-1].replace("*", "").strip()
        severity_cell = cells[2].replace("*", "").strip()
        # Some rows qualify the severity ("High if present"); the word that appears
        # is the registered severity, and the qualification stays in the row.
        severity = next((name for name in SEVERITIES if name in severity_cell), "")
        # The status vocabulary is exact: the first word of the last cell decides,
        # so "(mitigation detail)" additions cannot invent a sixth status.
        status = next((name for name in STATUSES if status_text.startswith(name)), None)
        if status is None:
            raise SystemExit(f"{ident}: unrecognised status {status_text!r}")
        if severity not in SEVERITIES:
            raise SystemExit(f"{ident}: unrecognised severity {cells[2]!r}")
        rows.append({"id": ident, "severity": severity, "status": status})
    if not rows:
        raise SystemExit("no risk rows found; the table format changed")
    return rows


def counts(rows: List[Dict[str, str]]) -> Dict[Tuple[str, str], List[str]]:
    table: Dict[Tuple[str, str], List[str]] = {(s, t): [] for s in SEVERITIES for t in STATUSES}
    for row in rows:
        table[(row["severity"], row["status"])].append(row["id"])
    return table


def render(rows: List[Dict[str, str]]) -> str:
    table = counts(rows)
    lines = [
        "| Severity | **Open** | Mitigated | Accepted | Closed | Total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for severity in SEVERITIES:
        cells = [severity]
        for status in STATUSES:
            members = table[(severity, status)]
            if not members:
                cells.append("0")
            else:
                cells.append(f"{len(members)} ({', '.join(members)})")
        total = sum(len(table[(severity, status)]) for status in STATUSES)
        cells.append(str(total))
        lines.append("| " + " | ".join(cells) + " |")
    totals = [sum(len(table[(severity, status)]) for severity in SEVERITIES) for status in STATUSES]
    lines.append("| **Total** | " + " | ".join(f"**{n}**" for n in totals) + f" | **{len(rows)}** |")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Rewrite the summary table in place.")
    args = parser.parse_args(argv)

    text = REGISTER.read_text(encoding="utf-8")
    rows = parse_rows(text)
    expected = render(rows)

    # Anchor on the summary heading: the severity legend earlier in the document
    # also begins a table with "| Severity |", and slicing at the first match would
    # replace the legend and everything up to the summary with the counts table.
    summary = text.index("## 2. Summary")
    start = text.index("| Severity |", summary)
    end = text.index("\n", text.index("| **Total** |", start))
    current = text[start:end]
    if current == expected:
        print(f"risk register summary is consistent ({len(rows)} rows)")
        return 0

    if args.write:
        REGISTER.write_text(text[:start] + expected + text[end:], encoding="utf-8")
        print(f"risk register summary rewritten ({len(rows)} rows)")
        return 0

    print("risk register summary is stale; run with --write. Expected:", file=sys.stderr)
    print(expected, file=sys.stderr)
    print("\nFound:", file=sys.stderr)
    print(current, file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
