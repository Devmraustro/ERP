from . import models


def post_init_hook(env):
    """Apply only non-destructive fixes to already loaded Algerian companies."""
    env["l10n_dz_account_fix.migration"].apply()
