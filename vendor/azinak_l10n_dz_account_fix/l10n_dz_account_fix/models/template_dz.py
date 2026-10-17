from odoo import models
from odoo.addons.account.models.chart_template import template


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    @template("dz")
    def _get_dz_z_account_fix_template_data(self):
        return {"property_account_receivable_id": "l10n_dz_411"}

    @template("dz", "account.account")
    def _get_dz_account_fix_account_account(self):
        account_types = {
            "133": "asset_non_current",
            "134": "liability_non_current",
            "161": "liability_non_current",
            "162": "liability_non_current",
            "163": "liability_non_current",
            "164": "liability_non_current",
            "165": "liability_non_current",
            "167": "liability_non_current",
            "168": "liability_non_current",
            "169": "liability_non_current",
            "171": "liability_non_current",
            "172": "liability_non_current",
            "173": "liability_non_current",
            "178": "liability_non_current",
            "368": "asset_current",
            "425": "asset_current",
            "476": "asset_current",
            "481": "liability_current",
            "509": "liability_current",
            "519": "liability_current",
            "521": "asset_current",
            "529": "liability_current",
            "591": "asset_current",
            "594": "asset_current",
        }
        values = {
            f"l10n_dz_{code}": {"account_type": account_type}
            for code, account_type in account_types.items()
        }

        for code in ("409", "419", "421", "425"):
            values.setdefault(f"l10n_dz_{code}", {})["reconcile"] = True

        return values
