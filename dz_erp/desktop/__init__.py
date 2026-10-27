# SPDX-License-Identifier: LGPL-3.0-only
"""Installed desktop client for the Algerian AI ERP (Phase 26).

The product direction is an installed, local-first desktop application -
logiciel de gestion - not a website. This package implements that client:

- :mod:`dz_erp.desktop.config`   local configuration and data locations
- :mod:`dz_erp.desktop.service`  embedded loopback service (Mode A) lifecycle
- :mod:`dz_erp.desktop.api_client` typed HTTP client for the verified API
- :mod:`dz_erp.desktop.i18n`     French/Arabic strings and RTL handling
- :mod:`dz_erp.desktop.ui`       PySide6 widgets (imported lazily)
- :mod:`dz_erp.desktop.app`      entry point (``python -m dz_erp.desktop``)

Two operating modes share one client:

* **Mode A - single workstation**: the client starts a private ERP service
  bound to ``127.0.0.1`` on an ephemeral port, backed by a local encrypted
  database and document vault. Core operations work without internet.
* **Mode B - company LAN**: the client connects to a company-hosted ERP
  server; no local service is started.

The client never speaks SQL directly. Every business operation goes through
the same authenticated, audited API the test suite exercises, so the desktop
UI inherits the server-verified authentication and tenant isolation instead
of reimplementing them.

PySide6 is an *optional* dependency (``pip install algerian-ai-erp[desktop]``);
the modules in this package that do not render a UI stay importable without
it, which keeps the core test matrix green on a headless CI.
"""

__version__ = "0.1.0"

APP_NAME = "AlgerianERP"
ORG_NAME = "DZ-ERP"
