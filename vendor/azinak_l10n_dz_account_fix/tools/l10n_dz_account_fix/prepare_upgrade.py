"""Prepare a database containing an unmodified official Algerian chart.

Run through ``odoo shell --shell-file`` after installing only ``l10n_dz``.
The script deliberately stores its baseline in the database so that the
verification process runs in a separate Odoo process after module install.
"""

import json


company = env["res.company"].create({
    "name": "DZ Account Fix Upgrade Test",
    "country_id": env.ref("base.dz").id,
})
chart = env["account.chart.template"].with_company(company)
chart.try_loading("dz", company=company, install_demo=False)


def account(code):
    return chart.ref(f"l10n_dz_{code}")


partner = env["res.partner"].with_company(company).create({
    "name": "Upgrade Test Customer",
    "company_id": company.id,
})
sale_journal = env["account.journal"].with_company(company).search([
    ("company_id", "=", company.id),
    ("type", "=", "sale"),
], limit=1)

invoice = env["account.move"].with_company(company).create({
    "move_type": "out_invoice",
    "company_id": company.id,
    "partner_id": partner.id,
    "journal_id": sale_journal.id,
    "invoice_line_ids": [(0, 0, {
        "name": "Pre-installation service",
        "quantity": 1,
        "price_unit": 1000,
        "account_id": account("700").id,
    })],
})
invoice.action_post()

# A representative posted entry whose account will be reclassified by the fix.
misc_journal = env["account.journal"].with_company(company).search([
    ("company_id", "=", company.id),
    ("type", "=", "general"),
], limit=1)
move = env["account.move"].with_company(company).create({
    "move_type": "entry",
    "company_id": company.id,
    "journal_id": misc_journal.id,
    "line_ids": [
        (0, 0, {"name": "Bank overdraft", "account_id": account("519").id, "debit": 250}),
        (0, 0, {"name": "Counterpart", "account_id": account("700").id, "credit": 250}),
    ],
})
move.action_post()

# A third value represents a deliberate customer customization. The migration
# must only replace Odoo's known bad source value, never this value.
account("509").account_type = "expense"
account("594").deprecated = True

default_field = env["res.partner"]._fields["property_account_receivable_id"]
default_receivable = default_field.get_company_dependent_fallback(
    env["res.partner"].with_company(company)
)
assert default_receivable == account("413"), "The official precondition changed: expected account 413"

baseline = {
    "company_id": company.id,
    "partner_id": partner.id,
    "invoice_id": invoice.id,
    "invoice_line_ids": invoice.line_ids.ids,
    "invoice_receivable_account_id": invoice.line_ids.filtered(
        lambda line: line.account_id.account_type == "asset_receivable"
    ).account_id.id,
    "move_id": move.id,
    "move_line_ids": move.line_ids.ids,
    "move_balance": sum(move.line_ids.mapped("balance")),
    "account_ids": {code: account(code).id for code in (
        "133", "411", "413", "425", "509", "519", "594"
    )},
}
env["ir.config_parameter"].sudo().set_param(
    "l10n_dz_account_fix.upgrade_test_baseline", json.dumps(baseline)
)
env.cr.commit()
print("PREPARE_UPGRADE_OK", json.dumps(baseline, sort_keys=True))
