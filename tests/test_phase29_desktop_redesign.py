# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 29 - desktop UI redesign: design system, shell, themes, RTL.

What this phase must prove (offscreen Qt against the real embedded service,
plus a Qt-free half that runs everywhere):

1. **Design system is a single source of truth.** Every colour lives in
   ``tokens``; the two palettes are complete and readable; no view module
   carries a literal colour or calls ``setStyleSheet`` with one.
2. **Localization is complete.** ``fr``/``ar``/``en`` have identical key
   sets and identical named placeholders, technical identifiers are never
   translated, and unknown workflow states pass through verbatim.
3. **The shell preserves the routing contract.** One navigable row per view
   in ``VIEWS`` order, so ``nav.item(i)``/``isRowHidden(i)`` still address
   ``VIEWS[i]``; group headings are painted, not rows; the users view stays
   hidden for non-admins and reappears for an admin after ``set_session``.
4. **Themes are real and persisted.** Switching light→dark changes the
   rendered pixels (not just a flag) and both ``theme`` and ``language``
   round-trip through the existing ``config`` whitelist.
5. **RTL is a layout change, not a label change.** Arabic flips the
   application direction and the sidebar mirrors; French/English restore
   LTR.
6. **Data screens show the server's data.** The tables render the rows the
   API returned (amounts formatted, never recomputed), the empty states are
   honest, and a refused read surfaces the server's status rather than an
   empty table.
7. **Nothing is fabricated.** The dashboard declares the metrics the API
   does not publish, and no screen invents a figure.

The rendering assertions run on the offscreen platform; they prove layout,
palette application and data binding, not the visual quality of a real
HiDPI display. That distinction is stated in the phase report.
"""

import os
import re
import time
from pathlib import Path

import pytest

from dz_erp.desktop import formatting, i18n, tokens

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The Qt-free half of this suite must run anywhere; the window half needs the
# optional desktop extra. Declared here (not inside a helper) so a machine
# without PySide6 skips those tests instead of erroring in fixture set-up.
try:  # explicit is clearer than a context manager here
    import PySide6.QtWidgets  # noqa: F401

    HAS_QT = True
except ImportError:  # pragma: no cover - exercised on lock-only environments
    HAS_QT = False

requires_qt = pytest.mark.skipif(not HAS_QT, reason="PySide6 desktop extra not installed")

_ADMIN = ("admin", "Smoke#2026x")


# ----------------------------------------------------------------------
# 1. design system (Qt-free)
# ----------------------------------------------------------------------


def test_tokens_define_two_complete_palettes() -> None:
    """Both themes fill every colour role; neither is a partial palette."""
    fields = set(tokens.LIGHT.__dataclass_fields__) - {"name"}
    for name, palette in tokens.PALETTES.items():
        missing = [field for field in fields if not getattr(palette, field)]
        assert not missing, f"{name} theme is missing {missing}"
    assert set(tokens.PALETTES) == {"light", "dark"}
    assert tokens.resolve_theme("dark") == "dark"
    assert tokens.resolve_theme("nonsense") == "light"  # never crashes a start-up
    assert tokens.resolve_theme("") == "light"


def test_palette_contrast_is_readable_in_both_themes() -> None:
    """Body text on surface stays well above the 4.5:1 AA floor."""
    notes = tokens.palette_contrast_notes()
    for theme, ratio in notes.items():
        assert ratio >= 7.0, f"{theme} text-on-surface contrast is only {ratio:.2f}:1"


def test_stylesheet_styles_every_declared_object() -> None:
    """Each object name the components use has a rule in the stylesheet."""
    for theme in ("light", "dark"):
        sheet = tokens.build_stylesheet(theme)
        for object_name in tokens.STYLEABLE_OBJECTS:
            assert f"#{object_name}" in sheet, f"{object_name} has no {theme} rule"
        # And the sheet really is per-theme, not one rendered twice.
    light, dark = tokens.build_stylesheet("light"), tokens.build_stylesheet("dark")
    assert light != dark


def test_rtl_stylesheet_mirrors_its_edge_rules() -> None:
    """The direction-sensitive rules flip rather than being duplicated."""
    ltr = tokens.build_stylesheet("light", rtl=False)
    rtl = tokens.build_stylesheet("light", rtl=True)
    assert "border-left" in ltr and "border-right" in rtl
    assert ltr != rtl


def test_no_view_hardcodes_a_colour_or_a_stylesheet() -> None:
    """Colours come from tokens; a view must not carry a literal hex value.

    Comments and docstrings may name a colour in prose; the check looks at
    string literals that reach a widget.
    """
    desktop = Path(__file__).resolve().parents[1] / "dz_erp" / "desktop"
    hex_pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    offenders = {}
    for path in sorted(desktop.glob("*.py")):
        if path.name in {"tokens.py", "theme.py", "icons.py"}:
            continue  # these three *are* the colour system
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            match = hex_pattern.search(line)
            if match:
                offenders.setdefault(path.name, []).append((number, match.group(0)))
    assert offenders == {}, f"hardcoded colours outside the design system: {offenders}"


def test_only_the_theme_layer_touches_set_stylesheet() -> None:
    """A screen may not restyle itself behind the design system's back."""
    desktop = Path(__file__).resolve().parents[1] / "dz_erp" / "desktop"
    for path in sorted(desktop.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if "setStyleSheet(" in line and not line.strip().startswith("#"):
                assert path.name in {"theme.py", "tokens.py"}, (
                    f"{path.name}:{number} calls setStyleSheet; use the theme layer"
                )


def test_formatting_never_recomputes_a_financial_value() -> None:
    """Display grouping only: the digits the server sent are the digits shown."""
    assert formatting.format_amount("11900.00", currency="DZD") == "11\u00a0900,00 DZD"
    assert formatting.format_amount("-11900.00", currency="DZD") == "-11\u00a0900,00 DZD"
    assert formatting.format_amount(None) == "—"
    assert formatting.format_amount("not-a-number") == "not-a-number"  # visible, not mangled
    # No rounding is invented: the exact scale the API returned survives.
    assert formatting.format_amount("0.005") == "0,005"
    assert formatting.format_quantity("3.000000") == "3"
    assert formatting.format_quantity("0.25") == "0,25"


def test_row_count_inflects_in_every_language() -> None:
    assert formatting.format_row_count(1, "fr") == "1 ligne"
    assert formatting.format_row_count(2, "fr") == "2 lignes"
    assert formatting.format_row_count(1, "en") == "1 row"
    assert formatting.format_row_count(0, "en") == "0 rows"


# ----------------------------------------------------------------------
# 2. localization
# ----------------------------------------------------------------------


def test_catalogues_are_key_and_placeholder_complete() -> None:
    french = i18n._STRINGS["fr"]
    for language in ("ar", "en"):
        other = i18n._STRINGS[language]
        assert set(other) == set(french), f"{language} key set drifted"
        for key, text in french.items():
            assert i18n.placeholders(text) == i18n.placeholders(other[key]), (
                f"{language}:{key} placeholders differ"
            )


def test_languages_include_french_arabic_and_english() -> None:
    assert i18n.LANGUAGES == ("fr", "ar", "en")
    assert i18n.is_rtl("ar") and not i18n.is_rtl("fr") and not i18n.is_rtl("en")


def test_state_labels_translate_known_states_and_pass_through_unknown() -> None:
    assert i18n.state_label("fr", "Validated") == "Validée"
    assert i18n.state_label("en", "Validated") == "Validated"
    assert i18n.state_label("ar", "PendingApproval") == "قيد الانتظار"
    # A state the catalogue has never seen is shown verbatim, never guessed.
    assert i18n.state_label("fr", "BrandNewState") == "BrandNewState"


def test_technical_identifiers_are_not_translated() -> None:
    """Account codes, SKUs and role names must survive a language switch."""
    for language in ("fr", "ar", "en"):
        assert i18n.tr(language, "col_code")  # a heading exists...
    # ...and the identifier itself is never routed through the catalogue.
    assert formatting.humanize_identifier("PendingApproval") == "Pending approval"


# ----------------------------------------------------------------------
# 3-6. the live window
# ----------------------------------------------------------------------


def _service(tmp_path: Path):
    from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run

    db_path = tmp_path / "data" / "erp.sqlite3"
    bootstrap_first_run(
        db_path,
        company_name="Société Fictive SARL",
        admin_username=_ADMIN[0],
        admin_password=_ADMIN[1],
    )
    from dz_erp.auth import hash_password
    from dz_erp.database import ERPDatabase

    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash) "
            "VALUES (7, 1, 'clerk_p29', 'clerk', 1, ?)",
            (hash_password("Clerk#2026x", iterations=1000),),
        )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, tax_regime, nif, wilaya_code) "
            "VALUES (10, 1, 'Fournisseur Fictif', 'supplier', 'REEL', '001216099123456', '16')"
        )
        conn.execute(
            "INSERT INTO products (id, company_id, sku, name, uom, is_storable, standard_cost_dzd) "
            "VALUES (5, 1, 'SKU-P29', 'Article Fictif', 'UNIT', 1, '12500.500000')"
        )
        conn.execute(
            "INSERT INTO stock_quants (company_id, warehouse_code, product_id, qty_on_hand) "
            "VALUES (1, 'ALG-WH', 5, '3.000000')"
        )
    service = EmbeddedErpService(
        db_path, tmp_path / "data" / "vault", security_log_path=tmp_path / "logs" / "security.jsonl"
    )
    return db_path, service


def _window(service, *, language: str = "fr", theme: str = "light"):
    if not HAS_QT:  # pragma: no cover - the marker skips these tests
        pytest.skip("PySide6 desktop extra not installed")
    from PySide6.QtWidgets import QApplication

    from dz_erp.desktop.api_client import ErpClient
    from dz_erp.desktop.theme import ThemeManager
    from dz_erp.desktop.ui import MainWindow

    QApplication.instance() or QApplication([])
    client = ErpClient(service.start())
    client.login(_ADMIN[0], _ADMIN[1])
    manager = ThemeManager(theme=theme, language=language)
    manager.apply()
    window = MainWindow(language, client, "test mode", theme_manager=manager)
    window.set_session(client.session())
    window.resize(1400, 900)
    window.show()
    return window, client, manager


def _pump(seconds: float = 0.2, *, until=None) -> None:
    """Process events for ``seconds``, or until ``until()`` turns true."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            app.processEvents()
            return
        time.sleep(0.01)


@requires_qt
def test_navigation_rows_match_the_view_registry(tmp_path: Path) -> None:
    """One navigable row per view, in registry order - the Phase 26 contract."""
    _db, service = _service(tmp_path)
    try:
        window, client, _manager = _window(service)
        from dz_erp.desktop.ui import VIEWS

        assert window.nav.count() == len(VIEWS)
        for index, key in enumerate(VIEWS):
            assert window.nav.item(index).text() == i18n.tr("fr", f"nav_{key}")
            assert not window.nav.item(index).data(tokens_role()) is True
        assert window.stack.count() == len(VIEWS)
        assert VIEWS[0] == "dashboard"  # the start view never moves
        client.close()
    finally:
        service.stop()


def tokens_role() -> int:
    """The delegate's group-label role, read from the shell module.

    Kept as a helper so the test names the contract ("headings are not rows")
    without importing a private constant at module import time.
    """
    from dz_erp.desktop.shell import _GROUP_LABEL_ROLE

    return _GROUP_LABEL_ROLE


@requires_qt
def test_group_headings_are_painted_not_rows(tmp_path: Path) -> None:
    """Operations/Accounting/Administration label the first row of each group."""
    _db, service = _service(tmp_path)
    try:
        window, client, _manager = _window(service)
        role = tokens_role()
        labels = [window.nav.item(i).data(role) for i in range(window.nav.count())]
        assert labels.count(None) == 6  # nine views, three of them open a group
        assert "OPÉRATIONS" in labels and "COMPTABILITÉ" in labels
        assert "ADMINISTRATION" in labels
        client.close()
    finally:
        service.stop()


@requires_qt
def test_role_visibility_hides_and_restores_the_admin_view(tmp_path: Path) -> None:
    """Non-admin: users row hidden. Admin: shown again. Never removed."""
    from dz_erp.desktop.ui import VIEWS

    _db, service = _service(tmp_path)
    try:
        window, client, _manager = _window(service)
        admin_row = VIEWS.index("users")
        assert not window.nav.isRowHidden(admin_row)
        window._role = "clerk"
        window._apply_nav_role_visibility()
        assert window.nav.isRowHidden(admin_row)
        assert window.nav.count() == len(VIEWS)  # hidden, not deleted
        window.set_session(client.session())
        assert not window.nav.isRowHidden(admin_row)
        client.close()
    finally:
        service.stop()


@requires_qt
def test_screens_render_the_servers_rows(tmp_path: Path) -> None:
    """Partner, product and stock tables show what the API returned."""
    from dz_erp.desktop.ui import VIEWS

    _db, service = _service(tmp_path)
    try:
        window, client, _manager = _window(service)
        window._load_view(VIEWS.index("suppliers"))
        assert window.suppliers_table.rowCount() == 1
        assert window.suppliers_table.item(0, 0).text() == "Fournisseur Fictif"
        assert window.suppliers_table.item(0, 3).text() == "001216099123456"

        window._load_view(VIEWS.index("products"))
        assert window.products_table.item(0, 0).text() == "SKU-P29"
        # The amount is grouped for reading and never re-rounded here.
        assert window.products_table.item(0, 3).text() == "12\u00a0500,50 DZD"

        window._load_view(VIEWS.index("stock"))
        assert window.stock_table.item(0, 0).text() == "ALG-WH"
        assert window.stock_table.item(0, 4).text() == "3"
        client.close()
    finally:
        service.stop()


@requires_qt
def test_dashboard_uses_real_counts_and_declares_what_the_api_lacks(tmp_path: Path) -> None:
    """Counts come from the endpoints; unavailable metrics are named as such."""
    from dz_erp.desktop.views_dashboard import UNAVAILABLE_METRICS

    _db, service = _service(tmp_path)
    try:
        window, client, _manager = _window(service)
        window._load_view(0)
        summary = window.dash_summary.text()
        assert "Fournisseurs & clients: 1" in summary
        assert "Articles: 1" in summary
        assert "Stocks: 1" in summary
        # A tile value is the server's count, not a number invented here.
        assert window.dashboard_view.tile_products.value.text() == "1"
        # And the metrics no endpoint publishes are declared, in the UI.
        assert len(UNAVAILABLE_METRICS) == 8
        for (name_label, status_label) in window.dashboard_view._availability_labels:
            assert name_label.text() and status_label.text()
        client.close()
    finally:
        service.stop()


@requires_qt
def test_dashboard_chart_plots_only_accounts_with_debit_movement(tmp_path: Path) -> None:
    """A pure-credit account must not occupy a zero-length bar.

    Posting a real invoice yields trial-balance rows for both a debit account
    (600) and a pure-credit account (401). The "debit by account" chart must
    show only the account that actually carries debit movement.
    """
    from dz_erp.desktop.api_client import ErpClient

    _db, service = _service(tmp_path)
    try:
        window, client, _manager = _window(service)
        # Segregation of duties is enforced server-side: a clerk creates the
        # invoice, an admin decides and posts it.
        clerk = ErpClient(service.base_url)
        clerk.login("clerk_p29", "Clerk#2026x")
        acct = ErpClient(service.base_url)
        acct.login(_ADMIN[0], _ADMIN[1])
        created = clerk.create_invoice(
            {
                "supplier_id": 10,
                "supplier_invoice_no": "FAC-P29-001",
                "invoice_date": "2026-10-20",
                "payment_method": "BANK_TRANSFER",
                "amount_ht_dzd": "50000.00",
                "amount_tva_dzd": "9500.00",
                "amount_timbre_dzd": "0.00",
                "amount_ttc_dzd": "59500.00",
                "lines": [
                    {
                        "description": "Prestation",
                        "qty": "1",
                        "unit_price_dzd": "50000.00",
                        "tax_rate": "19.0",
                        "line_ht_dzd": "50000.00",
                    }
                ],
            }
        )
        invoice_id = int(created["invoice_id"])
        clerk.submit_approval(invoice_id)
        acct.decide_approval(invoice_id, approve=True, reason="")
        acct.post_invoice(invoice_id)

        window._load_view(0)
        _pump(1.5, until=lambda: bool(window.dashboard_view.chart._items))
        items = window.dashboard_view.chart._items
        assert items, "the posted invoice should produce chart rows"
        assert all(value > 0 for _label, _text, value in items), items
        # A bare account code names nothing: the label carries code and name.
        for label_text, _value_text, _value in items:
            code, _, name = label_text.partition(" · ")
            assert code and name, label_text
        # The credit-only payables account is present in the ledger, absent here.
        trial_rows = client.trial_balance()["rows"]
        credit_only = [
            str(row["account_code"]) for row in trial_rows if float(row["debit_dzd"]) == 0
        ]
        assert credit_only, "fixture must contain a pure-credit account"
        for code in credit_only:
            assert code not in [label for label, _text, _value in items]
        clerk.close()
        acct.close()
        client.close()
    finally:
        service.stop()


# ----------------------------------------------------------------------
# 4. themes are applied, not merely recorded
# ----------------------------------------------------------------------


def _lightest_pixel(widget) -> int:
    """Brightest pixel in a widget's render (text is the bright part)."""
    image = widget.grab().toImage()
    best = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            best = max(best, image.pixelColor(x, y).lightness())
    return best


def _darkest_pixel(widget) -> int:
    image = widget.grab().toImage()
    worst = 255
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            worst = min(worst, image.pixelColor(x, y).lightness())
    return worst


@requires_qt
def test_review_actions_stay_gated_through_a_busy_cycle() -> None:
    """The workflow state, not the busy flag, decides which actions are armed.

    Clearing the busy flag re-enables every button; the state gate must win
    afterwards, otherwise a refresh or a failed action silently re-arms
    actions the server would refuse.
    """
    from dz_erp.desktop.views_invoices import ACTIONS_BY_STATE, InvoiceReviewDialog

    # The widgets _set_busy touches, plus the gate's own controls.
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

    QApplication.instance() or QApplication([])
    dialog = InvoiceReviewDialog.__new__(InvoiceReviewDialog)

    dialog.correction_button = QPushButton()
    dialog.submit_button = QPushButton()
    dialog.approve_button = QPushButton()
    dialog.reject_button = QPushButton()
    dialog.post_button = QPushButton()
    dialog.correction_edit = QLineEdit()
    dialog.reason_edit = QLineEdit()
    dialog._lang = "fr"

    for state, expected in (
        ("Validated", {"submit"}),
        ("PendingApproval", {"approve", "reject"}),
        ("Approved", {"post"}),
    ):
        dialog._state = state
        dialog._set_busy(True)
        assert not any(
            button.isEnabled()
            for button in (
                dialog.submit_button,
                dialog.approve_button,
                dialog.reject_button,
                dialog.post_button,
            )
        ), state
        dialog._set_busy(False)
        armed = {
            action
            for action, button in (
                ("submit", dialog.submit_button),
                ("approve", dialog.approve_button),
                ("reject", dialog.reject_button),
                ("post", dialog.post_button),
            )
            if button.isEnabled()
        }
        assert armed == expected, f"{state}: armed {armed}"
        assert armed <= set(ACTIONS_BY_STATE[state]), state


@requires_qt
def test_review_correction_box_explains_a_missing_value() -> None:
    """A field with no extracted value must say so, not render a blank box."""
    from PySide6.QtWidgets import QApplication, QLineEdit

    from dz_erp.desktop.theme import ThemeManager
    from dz_erp.desktop.views_invoices import InvoiceReviewDialog

    QApplication.instance() or QApplication([])
    manager = ThemeManager(theme="light", language="fr")
    manager.apply()

    dialog = InvoiceReviewDialog.__new__(InvoiceReviewDialog)
    from dz_erp.desktop.components import label

    dialog.provenance_label = label("")
    dialog.correction_edit = QLineEdit()
    # Phase 59F added a hint that names the field when extraction produced no
    # value at all; this test assembles the dialog by hand, so it must supply
    # every widget the provenance renderer touches.
    dialog.missing_hint = label("")
    dialog._policy = {}
    dialog._theme = manager
    dialog._lang = "fr"

    # Selected field with a value: prefilled, normal placeholder.
    dialog._show_provenance(
        {"field_name": "supplier_nif", "current_value": "001216099123456", "origin": "OCR",
         "provenance": "—"}
    )
    assert dialog.correction_edit.text() == "001216099123456"
    assert "supplier_nif" not in dialog.correction_edit.placeholderText()

    # Selected field that failed extraction: box names the field, no blank.
    dialog._show_provenance(
        {"field_name": "amount_ttc_dzd", "current_value": "", "origin": "OCR", "provenance": "—"}
    )
    assert dialog.correction_edit.text() == ""
    assert "amount_ttc_dzd" in dialog.correction_edit.placeholderText()

    # Nothing selected: generic placeholder, no stale text.
    dialog._show_provenance(None)
    assert dialog.correction_edit.text() == ""
    assert dialog.correction_edit.placeholderText() == "Valeur actuelle"


@requires_qt
def test_identity_line_follows_the_language_switch(tmp_path: Path) -> None:
    """The principal line is a sentence, not a frozen caption.

    Switching to Arabic used to leave one French line ("Utilisateur : ...")
    stranded in an RTL window; the identity is re-rendered on every switch.
    """
    _db, service = _service(tmp_path)
    try:
        window, client, manager = _window(service)
        window._load_view(0)
        _pump(1.0, until=lambda: bool(window.dashboard_view.identity_label.text()))
        french = window.dashboard_view.identity_label.text()
        assert french.startswith("Utilisateur :"), french

        window._switch_language("ar")
        _pump(0.5)
        arabic = window.dashboard_view.identity_label.text()
        assert arabic.startswith("المستخدم:"), arabic
        assert "Utilisateur" not in arabic
        client.close()
    finally:
        service.stop()


@requires_qt
def test_language_switch_retranslates_the_table_chrome(tmp_path: Path) -> None:
    """The row count and page indicator must not stay in the old language.

    They are composed by ``DataTable`` itself, so a switch that only recorded
    the language code left an English window showing "294 sur 294 lignes".
    """
    from PySide6.QtWidgets import QApplication

    from dz_erp.desktop.components import Column, DataTable
    from dz_erp.desktop.theme import ThemeManager

    QApplication.instance() or QApplication([])
    ThemeManager(theme="light", language="ar").apply()

    table = DataTable([Column("code", "Code", width=120)], search_keys=("code",), page_size=2)
    table.set_language("ar")
    table.set_rows([{"code": f"10{i}"} for i in range(5)])
    _pump(0.2)
    assert "سطرًا" in table.count_label.text(), table.count_label.text()
    assert "الصفحة" in table.page_label.text(), table.page_label.text()

    table.set_language("en")
    _pump(0.2)
    assert table.count_label.text() == "5 of 5 rows", table.count_label.text()
    assert table.page_label.text() == "Page 1 / 3", table.page_label.text()

    table.set_language("fr")
    _pump(0.2)
    assert "lignes" in table.count_label.text(), table.count_label.text()
    assert "Page 1 / 3" in table.page_label.text(), table.page_label.text()

    # The count is the *filtered* total, and switching language must not change it.
    table.search.setText("101")
    _pump(0.2)
    before = table.count_label.text()
    table.set_language("en")
    _pump(0.2)
    assert " of " in table.count_label.text(), table.count_label.text()
    assert "1" in before
    table.close()


def test_account_names_follow_the_ui_language_and_never_go_blank() -> None:
    """Arabic UI shows the Arabic name where it exists, French otherwise.

    Only a subset of the SCF plan carries an Arabic name, so the choice is
    per row: a missing translation must fall back, never render an empty cell
    in a ledger.
    """
    from dz_erp.desktop.formatting import localized_name

    row_ar = {"name_fr": "Impôts différés actif", "name_ar": "الضرائب المؤجلة - أصول"}
    row_fr_only = {"name_fr": "Compte de l'exploitant", "name_ar": None}
    row_en = {"name_fr": "Capital", "name_en": "Capital", "name_ar": "رأس المال"}

    arabic = localized_name("ar")
    assert arabic(row_ar) == "الضرائب المؤجلة - أصول"
    # No Arabic name on this row: the French label, not an empty cell.
    assert arabic(row_fr_only) == "Compte de l'exploitant"
    assert localized_name("fr")(row_ar) == "Impôts différés actif"
    assert localized_name("en")(row_en) == "Capital"
    assert localized_name("ar")({}) == ""


@requires_qt
def test_table_spends_spare_width_on_the_prose_column() -> None:
    """A wide pane must not leave dead space; the prose column absorbs it.

    Fixed widths used to end in the middle of the pane with a blank band to
    the right. Now the surplus widens the one column that carries prose (the
    widest declared one) and the identifier columns keep their measured size.
    """
    from PySide6.QtWidgets import QApplication

    from dz_erp.desktop.components import Column, DataTable
    from dz_erp.desktop.theme import ThemeManager

    QApplication.instance() or QApplication([])
    ThemeManager(theme="light", language="fr").apply()

    columns = [
        Column("code", "Code", width=110, mono=True),
        Column("name_fr", "Intitulé", width=360),
        Column("account_type", "Type", width=170),
    ]
    table = DataTable(columns, search_keys=("code",))
    # Same seam ``_ListScreen._rebuild_columns`` uses when the language changes.
    table.table.setColumnCount(len(columns))
    table.table.setHorizontalHeaderLabels([column.header for column in columns])
    table._columns = columns
    table._apply_view()
    table.set_rows(
        [
            {"code": "101", "name_fr": "Capital émis ou capital social ou fonds de dotation", "account_type": "Equity"},
            {"code": "133", "name_fr": "Impôts différés actif", "account_type": "Asset_non_current"},
        ]
    )
    table.resize(1200, 400)
    table.show()
    _pump(0.3)

    header = table.table.horizontalHeader()
    widths = [header.sectionSize(i) for i in range(3)]
    total = sum(widths)
    viewport = table.table.viewport().width()
    # No dead band: the columns fill the pane (only the vertical scrollbar
    # and a pixel of frame rounding may remain).
    assert total >= viewport - 20, (widths, viewport)
    # The prose column absorbed the surplus first and stays the widest; the
    # identifier columns only take a proportional share of what is left.
    assert widths[1] > 700, widths
    assert widths[1] == max(widths), widths
    assert widths[0] < widths[1] / 3, widths
    assert widths[2] < widths[1] / 2, widths
    table.close()


@requires_qt
def test_disabled_variant_buttons_do_not_look_armed(tmp_path: Path) -> None:
    """A disabled primary/accent button must paint differently from an enabled one.

    Qt resolves equal-specificity stylesheet rules by source order, so a
    ``[variant="accent"]`` rule declared after ``QPushButton:disabled`` used to
    repaint disabled buttons exactly like armed ones. Every variant therefore
    carries its own ``:disabled`` rule.
    """
    from PySide6.QtWidgets import QApplication, QPushButton

    from dz_erp.desktop.theme import ThemeManager

    QApplication.instance() or QApplication([])
    manager = ThemeManager(theme="light", language="fr")
    manager.apply()

    for variant in ("primary", "accent", "danger", "ghost"):
        button = QPushButton("Action")
        button.setProperty("variant", variant)
        button.resize(160, 36)
        button.show()
        _pump(0.1)
        enabled = button.grab().toImage()
        button.setEnabled(False)
        _pump(0.1)
        disabled = button.grab().toImage()

        differing = sum(
            1
            for y in range(0, enabled.height(), 2)
            for x in range(0, enabled.width(), 2)
            if enabled.pixelColor(x, y) != disabled.pixelColor(x, y)
        )
        assert differing > 0, f"variant={variant}: disabled state is invisible"
        button.close()


@requires_qt
def test_theme_switch_changes_rendered_pixels(tmp_path: Path) -> None:
    """Dark really paints dark: the same tile renders inverted colours.

    This is the regression test for the defect found during the phase: the
    stylesheet alone left widgets created after the switch painting on the
    OS palette (black text on a white card, in a dark window).
    """
    _db, service = _service(tmp_path)
    try:
        window, client, manager = _window(service, theme="light")
        window._load_view(0)
        _pump(0.3)
        tile = window.dashboard_view.stat_debit
        light_text = _darkest_pixel(tile)  # dark text on a light card
        assert light_text < 80

        window._switch_theme("dark")
        _pump(0.3)
        assert manager.theme == "dark"
        dark_text = _lightest_pixel(tile)  # light text on a dark card
        assert dark_text > 150, f"dark theme painted light text at {dark_text}"
        client.close()
    finally:
        service.stop()


def test_theme_and_language_persist_through_the_config_whitelist(tmp_path: Path) -> None:
    """The two preferences round-trip via ``config`` (the existing mechanism)."""
    from dz_erp.desktop.config import load_config, save_config

    config = load_config(str(tmp_path))
    config.language = "en"
    config.theme = "dark"
    config.validate()
    save_config(config)
    reloaded = load_config(str(tmp_path))
    assert (reloaded.language, reloaded.theme) == ("en", "dark")


@requires_qt
def test_language_switch_rebuilds_strings_and_mirrors_the_sidebar(tmp_path: Path) -> None:
    """AR: strings change, direction flips, identifiers stay put."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from dz_erp.desktop.ui import VIEWS

    _db, service = _service(tmp_path)
    try:
        window, client, manager = _window(service)
        window._switch_language("ar")
        _pump(0.2)
        app = QApplication.instance()
        assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
        assert coordinator_rtl(manager) is True
        assert window.nav.item(VIEWS.index("users")).text() == "المستخدمون"
        assert window.nav.item(VIEWS.index("accounts")).text() == "دليل الحسابات"
        # The mode caption follows the UI language instead of staying French.
        assert "poste de travail" not in window.shell.sidebar.mode_label.text()

        window._switch_language("en")
        _pump(0.2)
        assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
        assert window.nav.item(VIEWS.index("users")).text() == "Users"
        client.close()
    finally:
        service.stop()


def coordinator_rtl(manager) -> bool:
    return bool(manager.rtl)


# ----------------------------------------------------------------------
# 7. honest failures and never a fabricated figure
# ----------------------------------------------------------------------


@requires_qt
def test_refused_read_surfaces_the_server_status(tmp_path: Path) -> None:
    """A refused read is displayed as a refusal, never as an empty table.

    The refusal is produced by revoking the session token: the screen then
    gets a real 401 from the server, which is what any role-denied read looks
    like to the client.
    """
    from PySide6.QtWidgets import QApplication

    from dz_erp.desktop.api_client import ErpClient
    from dz_erp.desktop.theme import ThemeManager
    from dz_erp.desktop.ui import VIEWS, MainWindow

    _db, service = _service(tmp_path)
    try:
        QApplication.instance() or QApplication([])
        admin = ErpClient(service.start())
        admin.login(_ADMIN[0], _ADMIN[1])
        clerk = ErpClient(service.base_url)
        clerk.login("clerk_p29", "Clerk#2026x")
        manager = ThemeManager(theme="light", language="fr")
        manager.apply()
        window = MainWindow("fr", clerk, "test mode", theme_manager=manager)
        window.set_session(clerk.session())
        window._load_view(VIEWS.index("trial_balance"))
        _pump(0.5)
        assert window.tb_table.rowCount() == 0  # an empty ledger, honestly empty

        # Revoke the session server-side, then reload: the read is refused.
        clerk._token = "revoked-token"
        window._load_view(VIEWS.index("trial_balance"))
        # Wait for the *text*: visibility only follows once Qt has laid the
        # banner out, which is after the worker delivers the refusal.
        _pump(
            5.0,
            until=lambda: "401" in window.trial_balance_view.banner.text(),
        )
        _pump(0.2)
        # ``isHidden`` (an explicit hide flag) is the assertable bit: this
        # window is never shown, so ``isVisible`` stays false for every child
        # regardless of the banner's own state.
        assert not window.trial_balance_view.banner.isHidden()
        assert "401" in window.trial_balance_view.banner.text()
        assert window.tb_table.rowCount() == 0  # nothing invented to fill it
        clerk.close()
        admin.close()
    finally:
        service.stop()
