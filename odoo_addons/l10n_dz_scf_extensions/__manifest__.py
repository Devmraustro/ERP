# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Algeria - SCF Extensions, Party Identifiers, Timbre & G50 Prep",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Modular extensions over l10n_dz and l10n_dz_account_fix for NIF/NIS/RC/AI, Timbre Fiscal, and G50 draft workbooks",
    "description": (
        "Extends Odoo's Algerian localization (`l10n_dz`) and `l10n_dz_account_fix` with:\n"
        "- Corrected French account group labels (Group 32 and Group 66) and Arabic SCF labels.\n"
        "- Validated Algerian legal identifiers (NIF, NIS, RC, AI, NIN) on partners and companies.\n"
        "- Effective-date versioned Droit de Timbre rules for cash invoices.\n"
        "- Versioned G50 preparation workbook model (explicitly gated pending ONEC accountant sign-off)."
    ),
    "author": "Algerian AI ERP Engineering",
    "license": "LGPL-3",
    "depends": [
        "l10n_dz",
        "l10n_dz_account_fix",
        "tt_l10n_dz_state",
    ],
    "data": [],
    "installable": True,
    "auto_install": False,
    "application": False,
}
