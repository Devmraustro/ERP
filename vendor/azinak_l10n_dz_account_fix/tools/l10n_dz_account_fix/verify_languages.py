"""Verify installed addon metadata through real Arabic and French contexts."""

module = env["ir.module.module"].search([("name", "=", "l10n_dz_account_fix")], limit=1)
assert module.state == "installed"

expected = {
    "ar_001": (
        "الجزائر - تصحيحات المحاسبة",
        "تصحيحات محاسبية مدققة للتوطين الجزائري الرسمي",
    ),
    "fr_FR": (
        "Algérie - Correctifs comptables",
        "Correctifs comptables vérifiés pour la localisation algérienne officielle",
    ),
}
for lang, (name, summary) in expected.items():
    language = env["res.lang"].with_context(active_test=False).search([("code", "=", lang)], limit=1)
    assert language and language.active, f"Language is not active: {lang}"
    translated = module.with_context(lang=lang)
    assert translated.shortdesc == name, (lang, translated.shortdesc)
    assert translated.summary == summary, (lang, translated.summary)

source = module.with_context(lang="en_US")
assert source.shortdesc == "Algeria - Accounting Fixes"
assert source.summary == "Verified accounting fixes for the official Algerian localization"
print("VERIFY_LANGUAGES_OK")
