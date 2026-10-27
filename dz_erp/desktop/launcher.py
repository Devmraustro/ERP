# SPDX-License-Identifier: LGPL-3.0-only
"""Standalone launcher for frozen (PyInstaller) desktop builds.

PyInstaller executes a *script*, not a package ``__main__``, so the frozen
entry point needs an absolute import to attach the whole ``dz_erp`` package
graph (and every dependency PyInstaller must bundle). In a checkout the
application starts with ``dz-erp`` or ``python -m dz_erp.desktop``; this
module exists so ``tools/build_desktop.py`` can produce a self-contained
executable that starts the identical code path.
"""

import sys

from dz_erp.desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
