# Algeria Accounting Fixes for Odoo 18

`l10n_dz_account_fix` corrects verified accounting defects in Odoo's official
Algerian localization, `l10n_dz`. It intentionally remains separate from tax
declarations such as G50.

| | |
|---|---|
| Odoo | 18.0 |
| Technical name | `l10n_dz_account_fix` |
| Dependency | `l10n_dz` |
| Languages | English source; Arabic and French translations |
| License | LGPL-3 — free and open source |

## Corrected behavior

- Account 411 replaces 413 as the default customer receivable account.
- Verified account types are corrected for deferred taxes, financing,
  employee advances, suspense items and financial instruments.
- Non-cash accounts are no longer classified as bank or cash accounts.
- Reconciliation is enabled for selected advances and salary accounts.
- Existing companies are migrated conservatively: known Odoo source values are
  corrected while third-party and user customizations are preserved.

## Repository layout

The repository root is the addons path and contains one distributable addon:

```text
l10n_dz_account_fix/
├── __manifest__.py
├── models/
├── tests/
├── i18n/
└── static/description/
```

## Installation

```bash
git clone https://github.com/azinak/l10n-dz-account-fix.git
odoo -d DATABASE \
  --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/path/to/l10n-dz-account-fix \
  -i l10n_dz_account_fix --stop-after-init
```

Back up the database and filestore before installing on an existing system.
Historical posted entries are not rewritten.

## Migration safety

The migration selects only `dz` companies, resolves company-specific accounts,
changes types only while they equal Odoo's known incorrect values, changes the
receivable fallback only while it remains 413, and is idempotent. Uninstalling
the addon does not delete accounts or entries and does not reverse stored
corrections.

## Tests

```bash
odoo -d DATABASE \
  --addons-path=/usr/lib/python3/dist-packages/odoo/addons,. \
  -u l10n_dz_account_fix --test-enable \
  --test-tags=/l10n_dz_account_fix --stop-after-init
```

Coverage includes new charts, existing-company upgrades, posted entries,
balances, archived accounts, user defaults, multiple companies, idempotency,
uninstall and real Arabic/French metadata loading.

See the [Arabic test report](docs/تقرير_اختبارات_l10n_dz_account_fix.md) and
[accounting audit matrix](docs/مصفوفة_تدقيق_تصحيحات_l10n_dz_account_fix.md).

## Accounting evidence policy

Source facts, technical conclusions and unresolved accounting judgments are
kept distinct. Changes should cite an official Algerian source with edition and
page number. Mixed-nature accounts were excluded rather than force-classified.

## License

LGPL-3. Free to use, study, modify and redistribute under the license terms.
