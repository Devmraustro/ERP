# SPDX-License-Identifier: LGPL-3.0-only
import re
from odoo import api, fields, models
from odoo.exceptions import ValidationError

NIF_RE = re.compile(r"^[0-9]{15}([0-9]{5})?$")
NIS_RE = re.compile(r"^[0-9]{15}$")
AI_RE = re.compile(r"^[0-9]{11}$")
NIN_RE = re.compile(r"^[0-9]{18}$")

GROUP_TRANSLATION_FIXES = {
    "32": {
        "name@fr": "Autres approvisionnements",
        "name@ar": "تموينات أخرى",
    },
    "66": {
        "name@fr": "Charges financières",
        "name@ar": "أعباء مالية",
    },
}


class ResPartnerAlgerianIdent(models.Model):
    _inherit = "res.partner"

    l10n_dz_nif = fields.Char(string="NIF (Numéro d'Identification Fiscale)", size=20, index=True)
    l10n_dz_nis = fields.Char(string="NIS (Numéro d'Identification Statistique)", size=15)
    l10n_dz_ai = fields.Char(string="AI (Article d'Imposition)", size=11)
    l10n_dz_rc = fields.Char(string="RC (Registre du Commerce)", size=32)
    l10n_dz_nin = fields.Char(string="NIN (Numéro d'Identification Nationale)", size=18)
    l10n_dz_tax_regime = fields.Selection(
        selection=[
            ("REEL", "Régime du Réel"),
            ("IFU", "Impôt Forfaitaire Unique (IFU)"),
            ("EXEMPT", "Exonéré"),
            ("FOREIGN", "Étranger / Non-résident"),
        ],
        string="Algerian Tax Regime",
        default="REEL",
    )

    @api.constrains("l10n_dz_nif", "l10n_dz_nis", "l10n_dz_ai", "l10n_dz_nin")
    def _check_algerian_identifiers(self):
        for rec in self:
            if rec.l10n_dz_nif and not NIF_RE.match(rec.l10n_dz_nif.strip()):
                raise ValidationError("Invalid Algerian NIF: must be 15 or 20 digits.")
            if rec.l10n_dz_nis and not NIS_RE.match(rec.l10n_dz_nis.strip()):
                raise ValidationError("Invalid Algerian NIS: must be exactly 15 digits.")
            if rec.l10n_dz_ai and not AI_RE.match(rec.l10n_dz_ai.strip()):
                raise ValidationError("Invalid Algerian Article d'Imposition (AI): must be 11 digits.")
            if rec.l10n_dz_nin and not NIN_RE.match(rec.l10n_dz_nin.strip()):
                raise ValidationError("Invalid Algerian NIN: must be 18 digits.")
