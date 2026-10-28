{
    "name": "Algeria - Accounting Fixes",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Verified accounting fixes for the official Algerian localization",
    "description": (
        "Verified corrections for Odoo's official Algerian accounting localization.\n\n"
        "The module corrects the default customer receivable account, verified "
        "account types, non-cash financial classifications, and reconciliation "
        "settings. It does not add tax returns or new taxes."
    ),
    "author": "Abdelaziz Bouchaba",
    "maintainer": "Abdelaziz Bouchaba",
    "website": "https://github.com/azinak/l10n-dz-account-fix",
    "support": "azidev84@gmail.com",
    "license": "LGPL-3",
    "depends": ["l10n_dz"],
    "data": [],
    "images": [
        "static/description/banner.png",
        "static/description/main_screenshot.png",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
    "application": False,
}
