#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Generate and verify a hash-pinned lockfile from `requirements.lock.txt`.

Why this exists
---------------
`requirements.lock.txt` pins versions but not artifacts. A version pin says
"install SQLAlchemy 2.0.40"; it does not say *which bytes* are SQLAlchemy 2.0.40.
Anyone able to serve a different wheel under that version - a compromised index,
a misconfigured internal mirror, a hijacked DNS answer - satisfies the pin.
Hash pinning closes that gap: pip refuses any artifact whose digest is not in the
lockfile.

How the digests are obtained
----------------------------
Every digest is read from the PyPI JSON API for the exact pinned version, and
**every** distribution file for that version is included (wheels for all
platforms and Python versions, plus the sdist). That is deliberate rather than
lazy: the lockfile is shared by Linux CI, Windows CI and developer machines, and
a per-platform hash set would either break one of them or force per-platform
lockfiles. Including all authentic files for the pinned version still rejects
anything that is not a genuine release of that version - which is the threat
being addressed.

What this does NOT defend against
---------------------------------
* A compromised PyPI account publishing a *different* version: prevented by the
  version pins and by review, not by hashes.
* A malicious first publication of the exact pinned version that the digests were
  generated from - hashes pin bytes, they do not vet them. Provenance (who
  published, and when) requires sigstore/PEP 740 attestations or manual review,
  which this project does not yet perform. Stated in the register rather than
  implied.
* Anything outside the lockfile: `pip install --no-deps` means the graph is fixed,
  but a developer installing extra packages by hand is not covered.

Usage::

    python tools/generate_hash_lock.py --write      # regenerate the lockfile
    python tools/generate_hash_lock.py --check      # verify it is current

Requires network access to pypi.org and nothing else. No credentials, no
customer data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements.lock.txt"
HASH_LOCKFILE = REPO_ROOT / "requirements.lock.hashes.txt"
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"

HEADER = """\
# Hash-pinned lockfile for the Algerian AI ERP Platform.
#
# GENERATED FILE - do not edit by hand. Regenerate with:
#     python tools/generate_hash_lock.py --write
#
# Install with:
#     pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
#
# Sources: requirements.lock.txt (versions) + the PyPI JSON API (digests).
# Every authentic distribution file for each pinned version is listed (all
# platforms and Python versions, plus sdists), so the same lockfile is valid on
# Linux, Windows and macOS. As with requirements.lock.txt, installation uses
# --no-deps because the transitive closure is enumerated here.
#
# What this enforces: the installed artifact is byte-identical to a file PyPI
# published for that exact version. It defends against a poisoned index, a
# tampered mirror or a substituted wheel. It does NOT vet the publisher: a
# malicious genuine release of the pinned version would still match.
#
# The documented opencv-python -> opencv-python-headless substitution is
# explained in requirements.lock.txt; it is unchanged by hash pinning.

"""


def read_pins(path: Path) -> List[Tuple[str, str]]:
    pins: List[Tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)\s*(;.*)?$", line)
        if not match:
            raise SystemExit(f"unparseable pin in {path.name}: {line!r}")
        pins.append((match.group(1), match.group(2)))
    return pins


def fetch_digests(name: str, version: str) -> List[str]:
    url = PYPI_JSON.format(name=name, version=version)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{name}=={version} not found on PyPI (HTTP {exc.code})") from exc
    except Exception as exc:  # network failure must not be mistaken for "no hashes"
        raise SystemExit(f"could not fetch {url}: {type(exc).__name__}: {exc}") from exc

    digests = sorted({item["digests"]["sha256"] for item in payload.get("urls", [])})
    if not digests:
        raise SystemExit(f"{name}=={version} has no distribution files on PyPI")
    return digests


def build(pins: List[Tuple[str, str]]) -> str:
    lines = [HEADER]
    for name, version in sorted(pins, key=lambda item: item[0].lower()):
        digests = fetch_digests(name, version)
        # pip only continues a requirement when EVERY line but the last ends with
        # a backslash. A block whose hash lines lack it parses as "one hash for
        # the requirement" plus a pile of ignored lines - the lockfile looks
        # right, enforces almost nothing, and fails on the second artifact.
        hashes = [f"    --hash=sha256:{digest}" for digest in digests]
        body = " \\\n".join(hashes)
        lines.append(f"{name}=={version} \\\n{body}\n")
    return "".join(lines)


def summarise(text: str) -> Dict[str, int]:
    """Name/version -> number of hashes, from a rendered lockfile."""
    entries: Dict[str, int] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+==[^\s\\]+)", stripped)
        if match:
            current = match.group(1)
            entries[current] = entries.get(current, 0)
            continue
        if stripped.startswith("--hash=sha256:") and current:
            entries[current] += 1
    return entries


def check(pins: List[Tuple[str, str]]) -> int:
    if not HASH_LOCKFILE.exists():
        print(f"MISSING: {HASH_LOCKFILE.name} does not exist")
        return 1
    text = HASH_LOCKFILE.read_text(encoding="utf-8")
    entries = summarise(text)
    expected = {f"{name}=={version}" for name, version in pins}
    problems: List[str] = []

    for missing in sorted(expected - set(entries)):
        problems.append(f"{missing} is pinned in {LOCKFILE.name} but absent from {HASH_LOCKFILE.name}")
    for extra in sorted(set(entries) - expected):
        problems.append(f"{extra} is hashed in {HASH_LOCKFILE.name} but not pinned in {LOCKFILE.name}")
    for name, count in sorted(entries.items()):
        if count == 0:
            problems.append(f"{name} has no hashes")

    if problems:
        print("FAILED - the hash lockfile does not match the version lockfile:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nRegenerate with: python tools/generate_hash_lock.py --write")
        return 1

    print(f"OK - {len(entries)} pinned distributions, "
          f"{sum(entries.values())} hashes, no drift from {LOCKFILE.name}.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the hash-pinned lockfile.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="Regenerate requirements.lock.hashes.txt")
    group.add_argument("--check", action="store_true", help="Verify the hash lockfile matches the version pins")
    args = parser.parse_args(argv)

    pins = read_pins(LOCKFILE)
    if args.check:
        return check(pins)

    print(f"Fetching digests for {len(pins)} pinned distributions from PyPI...")
    text = build(pins)
    HASH_LOCKFILE.write_text(text, encoding="utf-8")
    entries = summarise(text)
    print(f"Wrote {HASH_LOCKFILE.name}: {len(entries)} distributions, {sum(entries.values())} hashes.")
    for name, count in sorted(entries.items()):
        if count == 0:
            print(f"ERROR: {name} produced no hashes", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
