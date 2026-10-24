"""Queue the correction module for uninstall in a dedicated process."""

module = env["ir.module.module"].search([
    ("name", "=", "l10n_dz_account_fix"),
    ("state", "=", "installed"),
], limit=1)
assert module, "l10n_dz_account_fix is not installed"
module.button_uninstall()
env.cr.commit()
print("UNINSTALL_QUEUED_OK")
