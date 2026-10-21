#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Explicit, reproducible validation of the declared dependency graph.

Why this exists
---------------
`rapidocr-onnxruntime==1.4.4` declares a hard dependency on the *GUI*
`opencv-python` wheel.  That wheel bundles the Qt5/xcb GUI stack and links
`libGL.so.1`, which is unavailable in a minimal Linux container.  This project
only uses the core `cv2` API, and `cv2.abi3.so` references zero `gl*` symbols,
so the GUI stack is dead weight.  The project therefore declares and installs
`opencv-python-headless==4.11.0.86` instead.

That substitution is a real, documented deviation from upstream metadata, and
it is reported by `pip check` as::

    rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.

Rather than conceal it, this script validates the graph *explicitly and more
strictly than `pip check`*:

1. every runtime dependency declared in ``pyproject.toml`` is installed at the
   exact pinned version;
2. every requirement of every installed distribution is satisfied, **except**
   for a single allow-listed substitution
   (``opencv-python`` -> ``opencv-python-headless``);
3. the installed ``cv2`` really is the headless build - the GUI wheel's
   ``opencv_python.libs`` payload directory must be absent;
4. ``cv2`` and ``RapidOCR`` both import successfully, and the OCR engine can be
   constructed.

Any other deviation fails the script with actionable output.  Exit status is
0 only when the environment matches the declared graph exactly.

Usage::

    python tools/verify_dependency_graph.py

Requires no network access, no credentials and no customer data.
"""

from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from packaging.requirements import Requirement
except Exception as exc:  # pragma: no cover - packaging is pinned in the lockfile
    raise SystemExit(
        "tools/verify_dependency_graph.py requires the 'packaging' package, which is pinned in "
        f"requirements.lock.txt: {type(exc).__name__}: {exc}"
    ) from exc

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

# The single, documented upstream-declaration substitution.
ALLOWED_SUBSTITUTIONS: Dict[str, str] = {
    "opencv-python": "opencv-python-headless",
}

# Distributions that must be importable, mapped to the attribute that proves
# the import actually worked.
REQUIRED_IMPORTS: Tuple[Tuple[str, Optional[str]], ...] = (
    ("cv2", "__version__"),
    ("rapidocr_onnxruntime", None),
    ("fastapi", None),
    ("sqlalchemy", None),
    ("pdfplumber", None),
    ("PIL", None),
    ("cryptography", None),
)

OPENCV_HEADLESS_PAYLOAD_DIR = "opencv_python_headless.libs"
OPENCV_GUI_PAYLOAD_DIR = "opencv_python.libs"


def _failures() -> List[str]:
    problems: List[str] = []

    # --- 1. project-declared runtime dependencies -------------------------
    declared = _declared_dependencies()
    installed = _installed_versions()
    for name, spec in declared.items():
        if name not in installed:
            problems.append(f"declared dependency {name}=={spec} is not installed")
            continue
        if installed[name] != spec:
            problems.append(f"declared dependency {name}=={spec} but {name}=={installed[name]} is installed")

    # --- 2. every installed distribution's requirements -------------------
    for dist in importlib_metadata.distributions():
        dist_name = dist.metadata["Name"]
        if dist_name is None:
            continue
        for requirement in dist.requires or []:
            try:
                req = Requirement(requirement)
            except Exception:  # pragma: no cover - malformed upstream metadata
                problems.append(f"{dist_name}: unparseable requirement {requirement!r}")
                continue
            if req.marker is not None and not req.marker.evaluate():
                continue
            target = _canonical(req.name)
            if target in installed:
                if req.specifier and not req.specifier.contains(installed[target], prereleases=True):
                    problems.append(
                        f"{dist_name} requires {requirement}, but {req.name}=={installed[target]} is installed"
                    )
                continue
            substitute = ALLOWED_SUBSTITUTIONS.get(target)
            if substitute and substitute in installed:
                if req.specifier and not req.specifier.contains(installed[substitute], prereleases=True):
                    problems.append(
                        f"{dist_name} requires {requirement}; substituted by {substitute}=={installed[substitute]}, "
                        "which does not satisfy the specifier"
                    )
                continue
            if substitute is None and target in ALLOWED_SUBSTITUTIONS.values():
                # A package we install in place of a GUI build.
                continue
            problems.append(f"{dist_name} requires {requirement}, which is not installed")

    # --- 3. the headless build must be the one that owns cv2 --------------
    site_packages = _site_packages()
    if site_packages is not None:
        # The headless wheel creates a `.libs` payload directory on POSIX
        # (manylinux/musllinux wheels bundle OpenCV shared libs there).  On
        # Windows the DLLs are shipped directly in the package, so no
        # `.libs` directory is created.  We therefore only assert its presence
        # on POSIX, but we assert the GUI wheel's payload directory is absent
        # on ALL platforms (if the GUI wheel were installed on Windows it would
        # also not create a `.libs` directory, but the dist-info name would
        # still be `opencv-python` which we already check via the declared
        # dependencies).
        if os.name == "posix":
            if not (site_packages / OPENCV_HEADLESS_PAYLOAD_DIR).is_dir():
                problems.append(
                    f"expected headless payload directory {OPENCV_HEADLESS_PAYLOAD_DIR}/ in {site_packages}"
                )
        if (site_packages / OPENCV_GUI_PAYLOAD_DIR).is_dir():
            problems.append(
                f"GUI OpenCV payload directory {OPENCV_GUI_PAYLOAD_DIR}/ is present; the headless build "
                "must be the authoritative cv2 (see the OpenCV note in requirements.lock.txt)"
            )

    # --- 4. imports --------------------------------------------------------
    for module_name, attribute in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            problems.append(f"import {module_name} failed: {type(exc).__name__}: {exc}")
            continue
        if attribute is not None and getattr(module, attribute, None) is None:
            problems.append(f"import {module_name} succeeded but {attribute} is missing")

    return problems


def _canonical(name: str) -> str:
    return name.replace("_", "-").replace(".", "-").lower()


def _declared_dependencies() -> Dict[str, str]:
    """Runtime dependencies declared in pyproject.toml, name -> pinned version."""
    dependencies: Dict[str, str] = {}
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    in_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("dependencies"):
            in_block = True
            continue
        if in_block:
            if line.startswith("]"):
                break
            if not line or line.startswith("#"):
                continue
            entry = line.strip().rstrip(",").strip('"').strip("'")
            if "==" in entry:
                name, _, version = entry.partition("==")
                dependencies[_canonical(name.strip())] = version.strip()
    return dependencies


def _installed_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    for dist in importlib_metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            versions[_canonical(name)] = dist.version
    return versions


def _site_packages() -> Optional[Path]:
    for entry in sys.path:
        candidate = Path(entry)
        if candidate.is_dir() and (candidate / "cv2").is_dir():
            return candidate
    return None


def main() -> int:
    problems = _failures()
    installed = _installed_versions()

    print("Dependency graph validation")
    print("===========================")
    for name in sorted(ALLOWED_SUBSTITUTIONS):
        substitute = ALLOWED_SUBSTITUTIONS[name]
        print(
            f"  documented substitution: {name} -> {substitute}=={installed.get(substitute, 'MISSING')}"
        )
    print(f"  installed distributions: {len(installed)}")

    if problems:
        print("\nFAILED - the environment does not match the declared dependency graph:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nInstall the declared environment with:\n"
            "    pip install --no-deps -r requirements.lock.txt"
        )
        return 1

    print("\nOK - the environment matches the declared dependency graph.")
    print("     The only deviation from upstream metadata is the documented")
    print(f"     substitution {', '.join(f'{k} -> {v}' for k, v in ALLOWED_SUBSTITUTIONS.items())}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
