"""Verify installation over an existing, used Algerian database."""

import json


baseline = json.loads(env["ir.config_parameter"].sudo().get_param(
    "l10n_dz_account_fix.upgrade_test_baseline"
))
company = env["res.company"].browse(baseline["company_id"]).exists()
assert company and company.chart_template == "dz"
chart = env["account.chart.template"].with_company(company)


def account(code):
    return chart.ref(f"l10n_dz_{code}")


expected_types = {
    "133": "asset_non_current",
    "425": "asset_current",
    "519": "liability_current",
}
for code, expected in expected_types.items():
    assert account(code).id == baseline["account_ids"][code]
    assert account(code).account_type == expected, (code, account(code).account_type)

assert account("509").account_type == "expense", "Customer customization was overwritten"
assert account("594").deprecated, "Archived account was unexpectedly reactivated"

invoice = env["account.move"].browse(baseline["invoice_id"])
move = env["account.move"].browse(baseline["move_id"])
assert invoice.state == move.state == "posted"
assert invoice.line_ids.ids == baseline["invoice_line_ids"]
assert move.line_ids.ids == baseline["move_line_ids"]
assert sum(move.line_ids.mapped("balance")) == baseline["move_balance"] == 0
old_receivable = invoice.line_ids.filtered(
    lambda line: line.account_id.account_type == "asset_receivable"
).account_id
assert old_receivable.id == baseline["invoice_receivable_account_id"] == account("413").id

default_field = env["res.partner"]._fields["property_account_receivable_id"]
assert default_field.get_company_dependent_fallback(
    env["res.partner"].with_company(company)
) == account("411")

# New business documents must use the corrected default; historical posted
# documents remain untouched.
partner = env["res.partner"].with_company(company).create({
    "name": "Post-installation Customer",
    "company_id": company.id,
})
assert partner.property_account_receivable_id == account("411")

# The pre-existing generic company in the same database remains outside scope.
foreign = env["res.company"].search([
    ("id", "!=", company.id),
    ("chart_template", "!=", "dz"),
], limit=1)
assert foreign
foreign_chart = foreign.chart_template
env["l10n_dz_account_fix.migration"].apply()
assert foreign.chart_template == foreign_chart

# Re-running the migration must be harmless.
snapshot = {
    code: (account(code).id, account(code).account_type, account(code).reconcile, account(code).deprecated)
    for code in ("133", "411", "425", "509", "519", "594")
}
env["l10n_dz_account_fix.migration"].apply()
assert snapshot == {
    code: (account(code).id, account(code).account_type, account(code).reconcile, account(code).deprecated)
    for code in snapshot
}
env.cr.commit()
print("VERIFY_UPGRADE_OK", json.dumps(snapshot, sort_keys=True))
