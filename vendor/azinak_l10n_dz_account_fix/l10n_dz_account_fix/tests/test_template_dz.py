from odoo.tests.common import TransactionCase, tagged


class TestAlgerianChartTemplateFixes(TransactionCase):
    def test_default_receivable_account(self):
        template_values = self.env[
            "account.chart.template"
        ]._get_chart_template_data("dz")["template_data"]
        self.assertEqual(template_values["property_account_receivable_id"], "l10n_dz_411")

    def test_verified_account_types(self):
        account_values = self.env[
            "account.chart.template"
        ]._get_chart_template_data("dz")["account.account"]
        expected_types = {
            "133": "asset_non_current",
            "134": "liability_non_current",
            "164": "liability_non_current",
            "171": "liability_non_current",
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
        for code, expected_type in expected_types.items():
            with self.subTest(account_code=code):
                self.assertEqual(
                    account_values[f"l10n_dz_{code}"]["account_type"],
                    expected_type,
                )

    def test_reconciliation_accounts(self):
        account_values = self.env[
            "account.chart.template"
        ]._get_chart_template_data("dz")["account.account"]
        for code in ("409", "419", "421", "425"):
            with self.subTest(account_code=code):
                self.assertTrue(account_values[f"l10n_dz_{code}"]["reconcile"])


@tagged("post_install", "-at_install")
class TestAlgerianChartTemplateIntegration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Algerian Localization Integration Test",
            "country_id": cls.env.ref("base.dz").id,
        })
        cls.chart_template = cls.env["account.chart.template"].with_company(cls.company)
        cls.chart_template.try_loading(
            "dz", company=cls.company, install_demo=False
        )

    def _account(self, code):
        return self.chart_template.ref(f"l10n_dz_{code}")

    def test_chart_is_loaded_for_the_company(self):
        self.assertEqual(self.company.chart_template, "dz")
        self.assertEqual(self.company.account_fiscal_country_id, self.env.ref("base.dz"))
        self.assertTrue(self._account("411"))
        self.assertTrue(self._account("401"))

    def test_actual_default_receivable_is_account_411(self):
        default_receivable = self.env["res.partner"]._fields[
            "property_account_receivable_id"
        ].get_company_dependent_fallback(
            self.env["res.partner"].with_company(self.company)
        )
        self.assertEqual(default_receivable, self._account("411"))

    def test_actual_account_types(self):
        expected_types = {
            "133": "asset_non_current",
            "134": "liability_non_current",
            "161": "liability_non_current",
            "169": "liability_non_current",
            "171": "liability_non_current",
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
        for code, expected_type in expected_types.items():
            with self.subTest(account_code=code):
                self.assertEqual(self._account(code).account_type, expected_type)

    def test_actual_reconciliation_flags(self):
        for code in ("409", "419", "421", "425"):
            with self.subTest(account_code=code):
                self.assertTrue(self._account(code).reconcile)

    def test_non_cash_accounts_do_not_create_bank_journals(self):
        cash_accounts = self.env["account.account"].with_company(self.company).search([
            ("company_ids", "in", self.company.id),
            ("account_type", "=", "asset_cash"),
        ])
        for code in ("519", "521", "529", "591", "594"):
            with self.subTest(account_code=code):
                self.assertNotIn(self._account(code), cash_accounts)

    def test_reload_is_idempotent(self):
        account_ids_before = {
            code: self._account(code).id
            for code in ("133", "411", "425", "519", "529")
        }
        self.chart_template.try_loading(
            "dz", company=self.company, install_demo=False, force_create=False
        )
        account_ids_after = {
            code: self._account(code).id
            for code in account_ids_before
        }
        self.assertEqual(account_ids_after, account_ids_before)

    def test_existing_user_customization_is_not_overwritten(self):
        account = self._account("519")
        account.account_type = "asset_current"
        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)
        self.assertEqual(account.account_type, "asset_current")

    def test_migration_corrects_official_existing_values(self):
        account = self._account("519")
        account.account_type = "asset_cash"
        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)
        self.assertEqual(account.account_type, "liability_current")

    def test_migration_updates_official_receivable_fallback(self):
        self.env["ir.default"].set(
            "res.partner",
            "property_account_receivable_id",
            self._account("413").id,
            company_id=self.company.id,
        )
        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)
        fallback = self.env["res.partner"]._fields[
            "property_account_receivable_id"
        ].get_company_dependent_fallback(
            self.env["res.partner"].with_company(self.company)
        )
        self.assertEqual(fallback, self._account("411"))

    def test_migration_preserves_custom_receivable_fallback(self):
        custom_account = self._account("416")
        self.env["ir.default"].set(
            "res.partner",
            "property_account_receivable_id",
            custom_account.id,
            company_id=self.company.id,
        )
        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)
        fallback = self.env["res.partner"]._fields[
            "property_account_receivable_id"
        ].get_company_dependent_fallback(
            self.env["res.partner"].with_company(self.company)
        )
        self.assertEqual(fallback, custom_account)

    def test_migration_is_idempotent(self):
        migration = self.env["l10n_dz_account_fix.migration"]
        tracked_accounts = {
            code: (self._account(code).account_type, self._account(code).reconcile)
            for code in ("133", "409", "425", "519", "529")
        }
        migration.apply_for_company(self.company)
        migration.apply_for_company(self.company)
        self.assertEqual(
            {
                code: (self._account(code).account_type, self._account(code).reconcile)
                for code in tracked_accounts
            },
            tracked_accounts,
        )

    def test_non_algerian_company_is_not_modified(self):
        other_company = self.env["res.company"].create({
            "name": "Non-Algerian Migration Test",
            "country_id": self.env.ref("base.us").id,
        })
        other_company.chart_template = "generic_coa"
        self.env["l10n_dz_account_fix.migration"].apply()
        self.assertEqual(other_company.chart_template, "generic_coa")

    def test_migration_preserves_posted_entry_and_balances(self):
        journal = self.env["account.journal"].with_company(self.company).search([
            ("company_id", "=", self.company.id),
            ("type", "=", "general"),
        ], limit=1)
        account = self._account("519")
        account.account_type = "asset_cash"
        move = self.env["account.move"].with_company(self.company).create({
            "move_type": "entry",
            "company_id": self.company.id,
            "journal_id": journal.id,
            "line_ids": [
                (0, 0, {
                    "name": "Overdraft",
                    "account_id": account.id,
                    "debit": 750,
                }),
                (0, 0, {
                    "name": "Counterpart",
                    "account_id": self._account("700").id,
                    "credit": 750,
                }),
            ],
        })
        move.action_post()
        line_ids = move.line_ids.ids
        balances = {line.id: line.balance for line in move.line_ids}

        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)

        self.assertEqual(move.state, "posted")
        self.assertEqual(move.line_ids.ids, line_ids)
        self.assertEqual({line.id: line.balance for line in move.line_ids}, balances)
        self.assertEqual(sum(move.line_ids.mapped("balance")), 0)
        self.assertEqual(account.account_type, "liability_current")

    def test_archived_account_is_corrected_without_reactivation(self):
        account = self._account("594")
        account.account_type = "asset_cash"
        account.deprecated = True
        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)
        self.assertTrue(account.deprecated)
        self.assertEqual(account.account_type, "asset_current")

    def test_account_identity_and_code_are_preserved(self):
        account = self._account("425")
        original = (account.id, account.code, account.name)
        account.account_type = "liability_current"
        self.env["l10n_dz_account_fix.migration"].apply_for_company(self.company)
        self.assertEqual((account.id, account.code, account.name), original)
        self.assertEqual(account.account_type, "asset_current")
