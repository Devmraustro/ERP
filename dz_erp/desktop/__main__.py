# SPDX-License-Identifier: LGPL-3.0-only
"""``python -m dz_erp.desktop`` → the desktop entry point."""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
