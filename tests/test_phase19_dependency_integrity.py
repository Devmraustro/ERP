# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 19 — Dependency and artifact integrity (Risk B2).

Two distinct weaknesses are covered, because "the dependencies are pinned" is not
the same as "the dependency system can be trusted":

1. **Artifact integrity.** `requirements.lock.txt` pinned versions but not bytes,
   so any index, mirror or DNS answer could serve a different wheel under the
   same version. `requirements.lock.hashes.txt` pins digests and CI installs with
   `--require-hashes`.
2. **Validator testability.** `tools/verify_dependency_graph.py` was itself
   untested: a supply-chain check that has never been observed to fail is an
   assumption, not a control. Its decision logic is now injectable, so these
   tests can assert that it *rejects* a wrong version, a missing package, an
   unsatisfied specifier and an unparseable requirement.

No network access is required by this file: the PyPI metadata is not re-fetched,
only the consistency of the committed lockfiles and the validator's rules are
checked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from tools import generate_hash_lock, verify_wheelhouse
from tools.verify_dependency_graph import DistributionRecord, _failures

LOCKFILE = REPO_ROOT / "requirements.lock.txt"
HASH_LOCKFILE = REPO_ROOT / "requirements.lock.hashes.txt"


# ----------------------------------------------------------------------
# 1. The validator must actually fail when it should
# ----------------------------------------------------------------------
def _clean_environment():
    """A minimal synthetic environment that passes every rule."""
    return {
        "declared": {"demo": "1.0"},
        "distributions": [
            DistributionRecord("demo", "1.0"),
            DistributionRecord("dep", "2.0"),
            # Keep the synthetic environment valid on Windows too, where this
            # marker is active and the validator correctly checks the package.
            DistributionRecord("winonly", "1.0"),
            DistributionRecord(
                "consumer", "3.0", ("demo>=1.0", "dep==2.0", 'winonly ; sys_platform == "win32"')
            ),
            DistributionRecord("rapidocr-onnxruntime", "1.4.4", ("opencv-python>=4.5.1.48",)),
            DistributionRecord("opencv-python-headless", "4.11.0.86"),
        ],
    }


def test_validator_accepts_a_consistent_environment() -> None:
    problems = _failures(**_clean_environment(), check_payload=False, check_imports=False)
    assert problems == []


def test_validator_rejects_a_wrong_installed_version() -> None:
    env = _clean_environment()
    env["declared"] = {"demo": "1.1"}
    problems = _failures(**env, check_payload=False, check_imports=False)
    assert any("declared dependency demo==1.1" in p and "demo==1.0 is installed" in p for p in problems)


def test_validator_rejects_a_missing_declared_package() -> None:
    env = _clean_environment()
    env["declared"] = {"demo": "1.0", "absent": "9.9"}
    problems = _failures(**env, check_payload=False, check_imports=False)
    assert any("absent==9.9 is not installed" in p for p in problems)


def test_validator_rejects_an_unsatisfied_specifier() -> None:
    env = _clean_environment()
    env["distributions"] = [
        DistributionRecord("demo", "1.0"),
        DistributionRecord("dep", "1.0"),  # consumer requires dep==2.0
        DistributionRecord("consumer", "3.0", ("dep==2.0",)),
    ]
    problems = _failures(**env, check_payload=False, check_imports=False)
    assert any("consumer requires dep==2.0" in p and "dep==1.0 is installed" in p for p in problems)


def test_validator_rejects_an_unparseable_requirement() -> None:
    """Bad upstream metadata must fail loudly, not be skipped silently."""
    env = _clean_environment()
    env["distributions"] = [DistributionRecord("broken", "1.0", ("this is not a requirement >>>",))]
    problems = _failures(**env, check_payload=False, check_imports=False)
    assert any("unparseable requirement" in p for p in problems)


def test_platform_markers_are_honoured() -> None:
    """A Windows-only requirement must not fail validation on Linux, and vice versa."""
    env = _clean_environment()
    env["distributions"] = [
        DistributionRecord("demo", "1.0"),
        DistributionRecord("winonly", "1.0"),
        DistributionRecord("pkg", "1.0", ('winonly==1.0 ; sys_platform == "win32"',)),
    ]
    assert _failures(**env, check_payload=False, check_imports=False) == []


def test_only_the_documented_substitution_is_allow_listed() -> None:
    """Any *other* substituted GUI/alternative package must be rejected."""
    env = _clean_environment()
    env["distributions"] = [
        DistributionRecord("demo", "1.0"),
        DistributionRecord("rapidocr-onnxruntime", "1.4.4", ("some-other-gui-package>=1.0",)),
    ]
    problems = _failures(**env, check_payload=False, check_imports=False)
    assert any("some-other-gui-package" in p for p in problems)

    # The documented one still passes, with a compatible version.
    env["distributions"] = [
        DistributionRecord("demo", "1.0"),
        DistributionRecord("rapidocr-onnxruntime", "1.4.4", ("opencv-python>=4.5.1.48",)),
        DistributionRecord("opencv-python-headless", "4.11.0.86"),
    ]
    assert _failures(**env, check_payload=False, check_imports=False) == []


def test_substitution_must_still_satisfy_the_declared_specifier() -> None:
    env = _clean_environment()
    env["distributions"] = [
        DistributionRecord("demo", "1.0"),
        DistributionRecord("rapidocr-onnxruntime", "1.4.4", ("opencv-python>=5.0",)),
        DistributionRecord("opencv-python-headless", "4.11.0.86"),
    ]
    problems = _failures(**env, check_payload=False, check_imports=False)
    assert any("does not satisfy the specifier" in p for p in problems)


def test_live_environment_passes() -> None:
    """The real environment must still satisfy the graph (the control is not vacuous)."""
    assert _failures() == []


# ----------------------------------------------------------------------
# 2. The lockfiles must agree with each other and with pyproject
# ----------------------------------------------------------------------
def test_hash_lockfile_covers_exactly_the_version_pins() -> None:
    pins = generate_hash_lock.read_pins(LOCKFILE)
    text = HASH_LOCKFILE.read_text(encoding="utf-8")
    entries = generate_hash_lock.summarise(text)

    assert {f"{name}=={version}" for name, version in pins} == set(entries)
    assert all(count > 0 for count in entries.values()), "a pin without any hash is unenforced"


def test_hash_lockfile_check_mode_passes() -> None:
    """`generate_hash_lock.py --check` is what CI runs; assert it is clean and non-zero on drift."""
    assert generate_hash_lock.check(generate_hash_lock.read_pins(LOCKFILE)) == 0


def test_every_pin_in_the_hash_lockfile_is_parsed_by_pip() -> None:
    """A block whose continuation lines lack a backslash silently enforces one hash.

    This regression is why the format is asserted structurally: with the wrong
    continuation syntax pip reports "has --hash but no requirement" and keeps a
    single digest, so the lockfile *looks* hash-pinned while almost nothing is.
    """
    text = HASH_LOCKFILE.read_text(encoding="utf-8")
    requirement_lines = [line for line in text.splitlines() if re.match(r"^[A-Za-z0-9_.\-]+==", line)]
    assert requirement_lines, "no pinned requirements found"
    for line in requirement_lines:
        assert line.rstrip().endswith("\\"), f"requirement line must continue onto its hashes: {line!r}"

    # Inside a block, every hash line except the last must end with a backslash.
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", text)
    checked = 0
    for block in blocks:
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if not lines or not re.match(r"^[A-Za-z0-9_.\-]+==", lines[0]):
            continue
        checked += 1
        for line in lines[:-1]:
            assert line.rstrip().endswith("\\"), f"missing continuation backslash in: {line!r}"
        assert lines[-1].strip().startswith("--hash="), "a block must end with a hash line"
    assert checked >= len(requirement_lines)


def test_hash_lockfile_uses_sha256_only() -> None:
    """sha256 is the only algorithm pip verifies by default and the only one we pin."""
    digests = re.findall(r"--hash=([a-z0-9]+):", HASH_LOCKFILE.read_text(encoding="utf-8"))
    assert digests
    assert set(digests) == {"sha256"}
    for digest in re.findall(r"--hash=sha256:([0-9a-f]+)", HASH_LOCKFILE.read_text(encoding="utf-8")):
        assert len(digest) == 64


def test_pyproject_declarations_are_all_pinned_in_the_lockfile() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)==([^"]+)"', text, re.MULTILINE)
    assert declared, "no pinned declarations found in pyproject.toml"
    pins = dict(generate_hash_lock.read_pins(LOCKFILE))
    lowered = {name.lower().replace("_", "-"): version for name, version in pins.items()}
    for name, version in declared:
        key = name.lower().replace("_", "-")
        assert key in lowered, f"{name} is declared in pyproject.toml but absent from the lockfile"
        assert lowered[key] == version, f"{name}=={version} declared but {lowered[key]} locked"


def test_supported_python_versions_are_declared_and_consistent() -> None:
    """The runtime floor must match the CI matrix, and the OCR engine must support it."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requires = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    assert requires, "requires-python is not declared"
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    supported = SpecifierSet(requires.group(1).strip())
    # Python 3.11.17 contains the current 3.11 tarfile fixes. On Windows,
    # 3.12.10 is the last binary installer; custom preflight rejects links and
    # path-traversal forms also fixed in 3.12.15's source-only security release.
    assert Version("3.11.17") in supported
    assert Version("3.12.10") in supported
    assert Version("3.11.16") not in supported
    assert Version("3.12.9") not in supported
    assert Version("3.13.0") not in supported

    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    versions = set(re.findall(r'python-version:\s*\[([^\]]+)\]', workflow))
    flat = {v.strip().strip('"').strip("'") for group in versions for v in group.split(",")}
    assert {"3.11", "3.12"} <= flat, f"CI does not cover the declared floor: {flat}"

    # Windows pins 3.12.10 because it is the last 3.12 binary installer. The
    # restore path must reject links, special members, and noncanonical paths itself.
    win_setup = re.search(
        r"Set up Python 3\.12\.10.*?python-version:\s*\"([^\"]+)\"",
        workflow,
        re.S,
    )
    assert win_setup, "Windows CI must pin a security-patched Python 3.12 release"
    assert win_setup.group(1) == "3.12.10"

    # rapidocr-onnxruntime declares <3.13; the pin must not be presented as 3.13-ready.
    import importlib.metadata as metadata

    requires_python = metadata.metadata("rapidocr-onnxruntime").get("Requires-Python", "")
    assert "<3.13" in requires_python.replace(" ", ""), (
        "if the OCR engine's Python ceiling changed, revisit Risk O5"
    )


# ----------------------------------------------------------------------
# 4. Offline wheelhouse builder/verifier (Risk B2 residual closure)
# ----------------------------------------------------------------------
def _digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_wheelhouse_pins_parser_joins_continuations_and_requires_hashes() -> None:
    text = (
        "# comment line, ignored\n"
        "demo==1.0 \\\n"
        "    --hash=sha256:" + "a" * 64 + " \\\n"
        "    --hash=sha256:" + "b" * 64 + "\n"
        "other==2.0 --hash=sha256:" + "c" * 64 + "\n"
    )
    pins = verify_wheelhouse.parse_pins(text)
    assert pins == {
        ("demo", "1.0"): ["a" * 64, "b" * 64],
        ("other", "2.0"): ["c" * 64],
    }
    with pytest.raises(SystemExit, match="no sha256 hashes"):
        verify_wheelhouse.parse_pins("demo==1.0\n")


def test_wheelhouse_verify_accepts_pinned_and_rejects_tampered(tmp_path: Path) -> None:
    payload = b"demo-pinned-bytes"
    digest = _digest(payload)
    lock = tmp_path / "hashes.txt"
    lock.write_text(f"demo==1.0 --hash=sha256:{digest}\nghost==9.9 --hash=sha256:{'e' * 64}\n")

    wheelhouse = tmp_path / "wh"
    wheelhouse.mkdir()
    (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(payload)
    # Correct name/version but bytes that are not in the lockfile.
    (wheelhouse / "tampered-1.0-py3-none-any.whl").write_bytes(b"tampered")

    result = verify_wheelhouse.verify_wheelhouse(wheelhouse, verify_wheelhouse.parse_pins(lock.read_text()))
    assert not result.ok
    assert result.unknown_artifacts == ["tampered-1.0-py3-none-any.whl"]
    assert result.missing_pins == [("ghost", "9.9")]
    assert result.covered[("demo", "1.0")] == ["demo-1.0-py3-none-any.whl"]


def test_wheelhouse_verify_rejects_a_hash_recorded_for_another_distribution(tmp_path: Path) -> None:
    payload = b"demo-pinned-bytes"
    digest = _digest(payload)
    lock = tmp_path / "hashes.txt"
    lock.write_text(f"demo==1.0 --hash=sha256:{digest}\n")

    wheelhouse = tmp_path / "wh"
    wheelhouse.mkdir()
    # The bytes are pinned for demo==1.0, but the file claims to be other-2.0.
    (wheelhouse / "other-2.0-py3-none-any.whl").write_bytes(payload)

    result = verify_wheelhouse.verify_wheelhouse(wheelhouse, verify_wheelhouse.parse_pins(lock.read_text()))
    assert not result.ok
    assert result.mismatched_artifacts == ["other-2.0-py3-none-any.whl"]
    assert result.missing_pins == [("demo", "1.0")]


def test_wheelhouse_build_command_pins_hashes_and_wheels_only() -> None:
    command = verify_wheelhouse.build_command(Path("wh"))
    assert "--require-hashes" in command
    assert "--only-binary=:all:" in command
    assert "--no-deps" in command
    assert str(HASH_LOCKFILE) in command
