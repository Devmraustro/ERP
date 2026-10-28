#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Reproducible desktop packaging: PyInstaller one-file build + SHA-256 sidecar.

Usage::

    python tools/build_desktop.py                 # build dist/AlgerianERP.exe + .sha256
    python tools/build_desktop.py --smoke         # additionally run the built exe with --smoke
    python tools/build_desktop.py --check SHA256HEX dist/AlgerianERP.exe

This is *packaging* infrastructure, not a runtime dependency: PyInstaller is a
build tool installed only on the packaging machine (never added to the project
dependency graph that CI validates). The desktop application itself runs from
a checkout with ``pip install -e ".[desktop]"``.

Integrity model: the build produces a SHA-256 sidecar next to the artifact.
Recipients verify with ``--check`` (or ``certutil -hashfile``) before running.
This proves artifact integrity, not publisher identity - unsigned-installer
distribution is recorded as an outstanding item in
``docs/DESKTOP_APPLICATION.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
BUILD_NAME = "AlgerianERP"

# uvicorn resolves its loop/protocol/lifespan implementations dynamically, so
# PyInstaller's static analysis cannot see them; without these the frozen exe
# starts and then dies on the first bind. Everything else (PySide6, onnxruntime,
# opencv) is imported statically and found on its own.
UVICORN_HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(artifact: Path, expected: str) -> int:
    actual = _sha256(artifact)
    if actual.lower() != expected.lower():
        print(f"MISMATCH: {artifact.name}\n  expected {expected}\n  actual   {actual}")
        return 1
    print(f"OK {artifact.name} sha256={actual}")
    return 0


def build(*, smoke: bool) -> int:
    try:
        import PyInstaller.__main__  # noqa: F401 - presence check only
    except ImportError:
        print(
            "PyInstaller is not installed on this packaging machine.\n"
            "Install the build tool (not a project dependency):\n"
            "    pip install pyinstaller\n",
            file=sys.stderr,
        )
        return 2

    # A script, not the package __main__: PyInstaller executes a plain file,
    # so relative imports (``from .app import main``) cannot resolve and the
    # frozen exe dies with "attempted relative import with no known parent
    # package" - and worse, the tiny script-only analysis bundles almost
    # nothing. launcher.py uses absolute imports, pulling the full graph.
    entry = REPO_ROOT / "dz_erp" / "desktop" / "launcher.py"
    if not entry.is_file():
        print(f"Entry point missing: {entry}", file=sys.stderr)
        return 2

    args = [
        str(entry),
        "--noconfirm",
        "--clean",
        "--onefile",
        f"--name={BUILD_NAME}",
        f"--paths={REPO_ROOT}",
        # Keep the generated .spec inside the ignored build/ directory so a
        # build never litters the repository root with a byproduct.
        f"--specpath={REPO_ROOT / 'build' / 'spec'}",
        # The API doc surface is disabled in the app itself; keep the icon
        # default and console window for a business application: errors must
        # be visible, and --smoke output must be capturable.
        "--console",
        # Bundle the vendored official SCF chart source next to the payload
        # (sys._MEIPASS/vendor): dz_erp.localization.vendor_data_dir() is
        # frozen-build aware and refuses to invent a substitute chart when
        # the data is absent. ~1.6 MB, MPL-2.0 data — see the dependency and
        # licence register.
        f"--add-data={REPO_ROOT / 'vendor'}{os.pathsep}vendor",
    ]
    for hidden in UVICORN_HIDDEN_IMPORTS:
        args.append(f"--hidden-import={hidden}")

    print("Building (this can take several minutes on first run)...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", *args],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    if result.returncode != 0:
        print(f"PyInstaller failed with exit code {result.returncode}.", file=sys.stderr)
        return result.returncode

    artifact = DIST_DIR / f"{BUILD_NAME}.exe"
    if not artifact.is_file():
        print(f"Expected artifact not found: {artifact}", file=sys.stderr)
        return 2
    digest = _sha256(artifact)
    sidecar = artifact.with_suffix(".exe.sha256")
    sidecar.write_text(f"{digest}  {artifact.name}\n", encoding="ascii")
    size_mib = artifact.stat().st_size / (1024 * 1024)
    print(f"Built {artifact} ({size_mib:.1f} MiB)")
    print(f"SHA-256 {digest}")
    print(f"Wrote {sidecar}")

    if smoke:
        print("Running the packaged exe with --smoke (headless Mode A check)...")
        proc = subprocess.run([str(artifact), "--smoke"], timeout=300)
        if proc.returncode != 0:
            print("Packaged smoke run FAILED.", file=sys.stderr)
            return proc.returncode
        print("Packaged smoke run OK.")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--smoke", action="store_true", help="Run the built exe with --smoke after build.")
    parser.add_argument("--check", nargs=2, metavar=("SHA256", "FILE"), help="Verify an artifact checksum.")
    args = parser.parse_args(argv)
    if args.check:
        return check(Path(args.check[1]), args.check[0])
    return build(smoke=args.smoke)


if __name__ == "__main__":
    raise SystemExit(main())
