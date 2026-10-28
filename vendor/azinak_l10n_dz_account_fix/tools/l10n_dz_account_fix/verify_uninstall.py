"""Verify that uninstalling code does not remove or reverse accounting data."""

import json


baseline = json.loads(env["ir.config_parameter"].sudo().get_param(
    "l10n_dz_account_fix.upgrade_test_baseline"
))
module = env["ir.module.module"].search([("name", "=", "l10n_dz_account_fix")], limit=1)
assert module.state == "uninstalled"
company = env["res.company"].browse(baseline["company_id"]).exists()
chart = env["account.chart.template"].with_company(company)
account_519 = chart.ref("l10n_dz_519")
invoice = env["account.move"].browse(baseline["invoice_id"]).exists()
move = env["account.move"].browse(baseline["move_id"]).exists()
assert company and invoice and move
assert invoice.state == move.state == "posted"
assert invoice.line_ids.ids == baseline["invoice_line_ids"]
assert move.line_ids.ids == baseline["move_line_ids"]
assert sum(move.line_ids.mapped("balance")) == 0
assert account_519.id == baseline["account_ids"]["519"]
assert account_519.account_type == "liability_current", "Uninstall unexpectedly reversed data"
print("VERIFY_UNINSTALL_OK")
