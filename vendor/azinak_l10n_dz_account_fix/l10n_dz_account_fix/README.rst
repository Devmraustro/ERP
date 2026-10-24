Algeria - Accounting Fixes for Odoo 18
=======================================

This module applies verified, non-destructive corrections to Odoo 18's
official Algerian accounting localization (``l10n_dz``).

Scope
-----

* Account 411 is used as the default customer receivable account.
* Verified account types are corrected for new Algerian charts.
* Existing Algerian companies are migrated only when an account still uses
  the original official value. User customizations are preserved.
* Reconciliation is enabled on verified advance and salary payable accounts.

The module does not add taxes or tax returns. It is free software under
LGPL-3. The repository README contains installation, migration and test details.

Languages
---------

English is the source language. Arabic and French translations are included.

License
-------

LGPL-3
