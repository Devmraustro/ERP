#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Build and verify an offline wheelhouse for the hash-pinned dependency set.

Risk register B2 (residual): the lockfiles pin *bytes* (`--require-hashes`),
but no internal mirror or offline wheelhouse was provisioned, and nothing could
verify one. This tool closes the verifiable half of that gap:

* ``--build DIR`` prints (and, with ``--execute``, runs) the exact
  network-facing command an operator runs on a *trusted* workstation. pip
  itself verifies every downloaded artifact against
  ``requirements.lock.hashes.txt`` while downloading, so a poisoned index
  cannot silently substitute bytes.
* ``--wheelhouse DIR`` verifies an existing wheelhouse **offline**: every file
  must hash to a recorded sha256 for its pinned distribution *and* be named
  for that exact distribution and version; every pinned distribution must be
  present at least once. Anything else - an unknown artifact, a tampered
  file, a name/version mismatch, a missing pin - fails with exit 1.

The tool deliberately does **not** weaken the hash gate, does not fetch
anything itself in verify mode, and does not assert publisher provenance:
PEP 740/sigstore attestations remain a documented open item (B2). It verifies
that a prepared wheelhouse contains exactly the pinned bytes, which is the
precondition for an air-gapped install with::

    pip install --no-deps --no-index --find-links <wheelhouse> \
        --require-hashes -r requirements.lock.hashes.txt

Usage::

    python tools/verify_wheelhouse.py --build  ./wheelhouse          # print command
    python tools/verify_wheelhouse.py --build  ./wheelhouse --execute
    python tools/verify_wheelhouse.py --wheelhouse ./wheelhouse     # offline verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements.lock.txt"
HASH_LOCKFILE = REPO_ROOT / "requirements.lock.hashes.txt"

_SHA256_RE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")
_ENTRY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s]+)\s*(.*)$")
_ARTIFACT_SUFFIXES = (".whl", ".tar.gz", ".zip")


def _canonical(name: str) -> str:
    """PEP 503 normalisation, used for lockfile names."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _filename_variants(name: str) -> Tuple[str, ...]:
    """Filename spellings a pinned distribution may appear under.

    Wheels and sdists replace ``-`` with ``_`` in the distribution name and may
    keep or lose dots, so both the canonical and the raw lowercase spellings
    are accepted prefixes.
    """
    lowered = name.lower()
    return (f"{_canonical(name)}-", f"{lowered}-", f"{lowered.replace('-', '_')}-")


def parse_pins(hashes_text: str) -> Dict[Tuple[str, str], List[str]]:
    """Parse a hash-pinned lockfile into ``{(canonical_name, version): [sha256...]}``.

    Continuation lines (``\\`` at end of line) are joined first, exactly like
    pip's parser; comment lines are skipped. Every pinned entry must carry at
    least one sha256 digest, otherwise the gate cannot be enforced and the
    tool refuses rather than guessing.
    """
    entries: List[str] = []
    pending = ""
    for raw in hashes_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += (" " if pending else "") + line
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        entries.append(pending)
        pending = ""
    if pending:
        entries.append(pending)

    pins: Dict[Tuple[str, str], List[str]] = {}
    for entry in entries:
        match = _ENTRY_RE.match(entry)
        if not match:
            raise SystemExit(f"REFUSED: unparseable lockfile entry: {entry[:80]!r}")
        name, version, rest = match.groups()
        digests = [digest.lower() for digest in _SHA256_RE.findall(rest)]
        if not digests:
            raise SystemExit(f"REFUSED: {name}=={version} has no sha256 hashes recorded")
        pins[(_canonical(name), version)] = digests
    if not pins:
        raise SystemExit("REFUSED: no pinned distributions found in the hash lockfile")
    return pins


@dataclass
class WheelhouseResult:
    """Outcome of an offline wheelhouse verification."""

    files_checked: int = 0
    covered: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)
    unknown_artifacts: List[str] = field(default_factory=list)
    mismatched_artifacts: List[str] = field(default_factory=list)
    missing_pins: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unknown_artifacts or self.mismatched_artifacts or self.missing_pins)

    def summary(self) -> List[str]:
        lines = [
            f"wheelhouse files checked: {self.files_checked}",
            f"pinned distributions covered: {len(self.covered)}",
        ]
        if self.unknown_artifacts:
            lines.append("UNKNOWN ARTIFACTS (not in the hash lockfile):")
            lines.extend(f"  - {name}" for name in self.unknown_artifacts)
        if self.mismatched_artifacts:
            lines.append("NAME/VERSION MISMATCHES (hash is pinned for another distribution):")
            lines.extend(f"  - {name}" for name in self.mismatched_artifacts)
        if self.missing_pins:
            lines.append("MISSING PINS (no artifact in the wheelhouse):")
            lines.extend(f"  - {name}=={version}" for name, version in self.missing_pins)
        if self.ok:
            lines.append("OK - the wheelhouse contains only hash-pinned artifacts for every pin.")
        else:
            lines.append("FAIL - fix the artefacts above before an air-gapped install.")
        return lines


def _file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_wheelhouse(wheelhouse: Path, pins: Dict[Tuple[str, str], List[str]]) -> WheelhouseResult:
    """Verify every artifact in *wheelhouse* against the pinned digests.

    A file is accepted only when its sha256 is recorded for exactly one pinned
    distribution **and** its file name identifies that distribution and version.
    A hash recorded for a different distribution, or a file whose bytes are not
    in the lockfile at all, is a failure - the wheelhouse may contain only
    pinned bytes.
    """
    if not wheelhouse.is_dir():
        raise SystemExit(f"REFUSED: wheelhouse directory does not exist: {wheelhouse}")

    by_digest: Dict[str, Tuple[str, str]] = {}
    for (name, version), digests in pins.items():
        for digest in digests:
            if digest in by_digest and by_digest[digest] != (name, version):
                raise SystemExit(
                    f"REFUSED: sha256 {digest} is recorded for both "
                    f"{by_digest[digest]} and {(name, version)}; the hash lockfile is ambiguous"
                )
            by_digest[digest] = (name, version)

    result = WheelhouseResult()
    for path in sorted(wheelhouse.iterdir()):
        if not path.is_file():
            continue
        result.files_checked += 1
        stem = path.name.lower()
        for suffix in _ARTIFACT_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        digest = _file_sha256(path)
        pin = by_digest.get(digest)
        if pin is None:
            result.unknown_artifacts.append(path.name)
            continue
        name, version = pin
        if not any(stem.startswith(prefix + version.lower()) for prefix in _filename_variants(name)):
            result.mismatched_artifacts.append(path.name)
            continue
        result.covered.setdefault(pin, []).append(path.name)

    result.missing_pins = sorted(
        (name, version) for (name, version) in pins if (name, version) not in result.covered
    )
    return result


def build_command(wheelhouse: Path) -> List[str]:
    """The network-facing command an operator runs on a trusted workstation.

    ``pip download`` resolves only artifacts whose digests are listed in the
    hash lockfile and fails on any other bytes, so a poisoned index cannot
    smuggle a substitute. ``--only-binary :all:`` keeps the wheelhouse to
    wheels (no on-host compilation, no sdist source execution).
    """
    return [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--only-binary=:all:",
        "--require-hashes",
        "--dest",
        str(wheelhouse),
        "-r",
        str(HASH_LOCKFILE),
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", metavar="DIR", type=Path, help="Print the pip download command for DIR.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="With --build, actually run the download (network access required).",
    )
    parser.add_argument(
        "--wheelhouse",
        metavar="DIR",
        type=Path,
        help="Verify an existing wheelhouse offline against the hash lockfile.",
    )
    parser.add_argument(
        "--lockfile",
        type=Path,
        default=HASH_LOCKFILE,
        help="Hash lockfile to verify against (default: requirements.lock.hashes.txt).",
    )
    parser.add_argument("--report-json", type=Path, help="Also write the verification result as JSON.")
    args = parser.parse_args(argv)

    if args.build:
        command = build_command(args.build)
        print(" ".join(command))
        if args.execute:
            completed = subprocess.run(command, check=False)
            return completed.returncode
        return 0

    if args.wheelhouse is None:
        parser.error("one of --wheelhouse or --build is required")

    pins = parse_pins(args.lockfile.read_text(encoding="utf-8"))
    result = verify_wheelhouse(args.wheelhouse, pins)
    for line in result.summary():
        print(line)
    if args.report_json:
        args.report_json.write_text(
            json.dumps(
                {
                    "files_checked": result.files_checked,
                    "covered": {f"{n}=={v}": files for (n, v), files in result.covered.items()},
                    "unknown_artifacts": result.unknown_artifacts,
                    "mismatched_artifacts": result.mismatched_artifacts,
                    "missing_pins": [f"{n}=={v}" for n, v in result.missing_pins],
                    "ok": result.ok,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
