# SPDX-License-Identifier: LGPL-3.0-only
"""Build hook: ship the reviewed Algerian SCF chart data inside the package.

Why this file exists
--------------------
``dz_erp.localization`` seeds every company from the *official* vendored Odoo
18 ``l10n_dz`` template plus the reviewed fix/wilaya add-ons under ``vendor/``.
It deliberately refuses to invent substitute account mappings, so a build that
omits those files produces an installation that starts, accepts a login and
then fails at the first company localization. That was the Phase 59A defect:
``pip install algerian-ai-erp`` (the way an operator installs the product)
produced a wheel containing ``dz_erp/`` but no chart data.

Two alternatives were measured and rejected on this build:

* ``[tool.setuptools.data-files]`` - installs relative to the *interpreter
  prefix* (``<venv>/dz_erp/vendor/...``), outside ``site-packages``, so the
  loader cannot find it.
* A repository symlink ``dz_erp/vendor -> ../vendor`` - not representable in a
  wheel, and it would make the checkout resolve two different directories
  depending on how the tool lints it.

So the build copies exactly the reviewed chart files into ``dz_erp/vendor`` in
the build tree, preserving the ``vendor/<repo>/...`` layout the loader walks.
``[tool.setuptools.package-data]`` then includes them. Nothing is synthesized
and no third-party ``.py`` module is imported by the application: the chart is
consumed as data (``docs/DEPENDENCY_AND_LICENSE_REGISTER.md``).

Development installs are unaffected: ``pip install -e .`` resolves
``vendor_data_dir()`` to the checkout, and ``ERP_VENDOR_DATA_DIR`` still
overrides both.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

REPO_ROOT = Path(__file__).resolve().parent

#: (source directory, destination below ``dz_erp/vendor``). Each pair mirrors
#: the ``vendor/<repo>/...`` shape ``dz_erp.localization`` walks, and lists the
#: exact files the loader requires - no wildcard sweep of third-party code.
CHART_DATA: tuple[tuple[str, str], ...] = (
    ("vendor/odoo_18_l10n_dz/models", "odoo_18_l10n_dz/models"),
    ("vendor/odoo_18_l10n_dz/data", "odoo_18_l10n_dz/data"),
    ("vendor/odoo_18_l10n_dz/data/template", "odoo_18_l10n_dz/data/template"),
    (
        "vendor/taghrastech_odoo_l10n_dz/tt_l10n_dz_state/data",
        "taghrastech_odoo_l10n_dz/tt_l10n_dz_state/data",
    ),
)

ALLOWED_SUFFIXES = (".csv", ".xml", ".py")


class build_py(_build_py):
    """``build_py`` that stages the SCF chart inside the package."""

    def run(self) -> None:  # pragma: no cover - exercised by the packaging check
        super().run()
        target_root = Path(self.build_lib) / "dz_erp" / "vendor"
        staged = 0
        for source_rel, destination_rel in CHART_DATA:
            source = REPO_ROOT / source_rel
            if not source.is_dir():
                raise SystemExit(
                    f"Chart data directory is missing: {source}. A release "
                    "build cannot produce an installable wheel without the "
                    "reviewed Algerian SCF data."
                )
            destination = target_root / destination_rel
            destination.mkdir(parents=True, exist_ok=True)
            for entry in sorted(source.iterdir()):
                if not entry.is_file() or entry.suffix not in ALLOWED_SUFFIXES:
                    continue
                if entry.name == "__init__.py" and "models" not in destination_rel:
                    continue
                shutil.copy2(entry, destination / entry.name)
                staged += 1
        if staged == 0:  # pragma: no cover - defensive
            raise SystemExit("No Algerian SCF chart data was staged into the build.")


setup(cmdclass={"build_py": build_py})
