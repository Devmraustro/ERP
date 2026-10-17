from odoo import models


class AlgerianAccountingFixMigration(models.AbstractModel):
    _name = "l10n_dz_account_fix.migration"
    _description = "Algerian Accounting Fix Migration"

    _ACCOUNT_TYPE_FIXES = {
        "133": ("asset_current", "asset_non_current"),
        "134": ("liability_current", "liability_non_current"),
        "161": ("liability_current", "liability_non_current"),
        "162": ("liability_current", "liability_non_current"),
        "163": ("liability_current", "liability_non_current"),
        "164": ("liability_current", "liability_non_current"),
        "165": ("liability_current", "liability_non_current"),
        "167": ("liability_current", "liability_non_current"),
        "168": ("liability_current", "liability_non_current"),
        "169": ("liability_current", "liability_non_current"),
        "171": ("liability_current", "liability_non_current"),
        "172": ("liability_current", "liability_non_current"),
        "173": ("liability_current", "liability_non_current"),
        "178": ("liability_current", "liability_non_current"),
        "368": ("asset_non_current", "asset_current"),
        "425": ("liability_current", "asset_current"),
        "476": ("liability_current", "asset_current"),
        "481": ("asset_receivable", "liability_current"),
        "509": ("asset_current", "liability_current"),
        "519": ("asset_cash", "liability_current"),
        "521": ("asset_cash", "asset_current"),
        "529": ("asset_cash", "liability_current"),
        "591": ("asset_cash", "asset_current"),
        "594": ("asset_cash", "asset_current"),
    }
    _RECONCILIATION_ACCOUNTS = ("409", "419", "421", "425")

    def apply(self):
        companies = self.env["res.company"].sudo().search([
            ("chart_template", "=", "dz")
        ])
        for company in companies:
            self.apply_for_company(company)

    def apply_for_company(self, company):
        chart_template = self.env["account.chart.template"].sudo().with_company(company)
        for code, (official_type, corrected_type) in self._ACCOUNT_TYPE_FIXES.items():
            account = chart_template.ref(f"l10n_dz_{code}", raise_if_not_found=False)
            if account and account.account_type == official_type:
                account.account_type = corrected_type

        for code in self._RECONCILIATION_ACCOUNTS:
            account = chart_template.ref(f"l10n_dz_{code}", raise_if_not_found=False)
            if account and not account.reconcile:
                account.reconcile = True

        receivable = chart_template.ref("l10n_dz_411", raise_if_not_found=False)
        official_default = chart_template.ref("l10n_dz_413", raise_if_not_found=False)
        property_field = self.env["res.partner"]._fields[
            "property_account_receivable_id"
        ]
        current_default = property_field.get_company_dependent_fallback(
            self.env["res.partner"].with_company(company)
        )
        if receivable and official_default and current_default == official_default:
            self.env["ir.default"].sudo().set(
                "res.partner",
                "property_account_receivable_id",
                receivable.id,
                company_id=company.id,
            )
