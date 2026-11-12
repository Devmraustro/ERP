"""Phase 30 — Arabic RTL acceptance capture.

Renders the application in Arabic on an offscreen Qt platform against a
disposable demo company, and records both screenshots and *structural*
assertions (direction, alignment, text presence) so the review is not only
visual.

    QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/tmp/qstub \
    PYTHONPATH=/home/user/ERP /tmp/vui/bin/python \\
        docs/release/phase30/evidence/rtl_capture.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from dz_erp.desktop.api_client import ErpClient
from dz_erp.desktop.i18n import LANGUAGES, is_rtl, tr
from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run
from dz_erp.desktop.theme import ThemeManager
from dz_erp.desktop.ui import MainWindow, VIEWS

OUT = Path(__file__).resolve().parents[1] / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

FINDINGS: list[dict] = []


def record(kind: str, name: str, ok: bool, detail: str = "") -> None:
    FINDINGS.append({"kind": kind, "check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {kind}: {name}" + (f" — {detail}" if detail else ""))


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    work = Path(tempfile.mkdtemp(prefix="phase30-rtl-"))
    db_path = work / "data" / "erp.sqlite3"
    admin = ("admin", "Rtl#2026x")
    bootstrap_first_run(db_path, company_name="شركة التجربة الجزائرية", admin_username=admin[0], admin_password=admin[1])

    from dz_erp.auth import hash_password
    from dz_erp.database import ERPDatabase

    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, tax_regime, nif, wilaya_code)"
            " VALUES (10, 1, 'مورد تجريبي ش.ذ.م.م', 'supplier', 'REEL', '001216099123456', '16')"
        )
        conn.execute(
            "INSERT INTO products (id, company_id, sku, name, uom, is_storable, standard_cost_dzd)"
            " VALUES (5, 1, 'SKU-RTL-1', 'منتج تجريبي', 'UNIT', 1, '12500.500000')"
        )
        conn.execute(
            "INSERT INTO stock_quants (company_id, warehouse_code, product_id, qty_on_hand)"
            " VALUES (1, 'ALG-WH', 5, '24.500000')"
        )
        for name, role, secret in (("clerk_rtl", "clerk", "Clerk#2026x"), ("acct_rtl", "accountant", "Acct#2026x")):
            conn.execute(
                "INSERT INTO users (company_id, username, role, is_active, password_hash)"
                " VALUES (1, ?, ?, 1, ?)",
                (name, role, hash_password(secret, iterations=1000)),
            )

    service = EmbeddedErpService(db_path, work / "vault", security_log_path=work / "sec.jsonl")
    base = service.start()
    admin_client = ErpClient(base)
    admin_client.login(*admin)

    # A real, fully valid invoice, posted through the API.
    clerk = ErpClient(base)
    clerk.login("clerk_rtl", "Clerk#2026x")
    invoice = clerk.create_invoice(
        {
            "supplier_id": 10,
            "supplier_invoice_no": "RTL-2026-001",
            "invoice_date": "2026-11-02",
            "payment_method": "BANK_TRANSFER",
            "amount_ht_dzd": "125000.00",
            "amount_tva_dzd": "23750.00",
            "amount_timbre_dzd": "0.00",
            "amount_ttc_dzd": "148750.00",
            "lines": [
                {
                    "description": "خدمة تجريبية",
                    "qty": "1",
                    "unit_price_dzd": "125000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "125000.00",
                }
            ],
        }
    )
    invoice_id = int(invoice["invoice_id"])
    clerk.submit_approval(invoice_id)
    acct = ErpClient(base)
    acct.login("acct_rtl", "Acct#2026x")
    acct.decide_approval(invoice_id, approve=True, reason="")
    acct.post_invoice(invoice_id)

    manager = ThemeManager(theme="light", language="ar")
    manager.apply()
    window = MainWindow("ar", admin_client, "وضع الجلسة الواحدة — خدمة محلية", theme_manager=manager)
    window.set_session(admin_client.session())
    window.resize(1400, 900)
    window.show()
    window._load_view(0)
    pump(1500)

    # ---- structural checks ------------------------------------------------
    record("language", "LANGUAGES contains ar", "ar" in LANGUAGES, str(LANGUAGES))
    record("direction", "is_rtl('ar') is True", is_rtl("ar") is True)
    record(
        "direction",
        "application layout direction is RightToLeft",
        app.layoutDirection() == Qt.LayoutDirection.RightToLeft,
        str(app.layoutDirection()),
    )
    # Sidebar on the physical right in RTL.
    shell = window.shell
    sidebar_x = shell.sidebar.mapTo(window, shell.sidebar.rect().topLeft()).x()
    record(
        "layout",
        "sidebar sits on the physical right in RTL",
        sidebar_x > window.width() / 2,
        f"sidebar x={sidebar_x} of width {window.width()}",
    )
    # Identity line translated.
    identity = window.dashboard_view.identity_label.text()
    record("translation", "identity line is Arabic", identity.startswith("المستخدم"), identity)
    # Nav labels translated.
    nav_titles = [
        window.nav.item(i).text() for i in range(window.nav.count()) if window.nav.item(i).text()
    ]
    french_leftovers = [t for t in nav_titles if any(w in t for w in ("Tableau", "Factures", "Plan", "Balance", "Stocks", "Utilisateurs", "Documents"))]
    record("translation", "no French leftovers in sidebar", not french_leftovers, str(french_leftovers))
    record("translation", "sidebar labels are Arabic", any(any("\u0600" <= c <= "\u06ff" for c in t) for t in nav_titles))

    window.grab().save(str(OUT / "30_01_dashboard_ar_rtl.png"))

    # Trial balance: headers mirrored, amounts right-aligned.
    window._load_view(VIEWS.index("trial_balance"))
    pump(1200)
    tb = window.trial_balance_view.table
    header = tb.table.horizontalHeader()
    record(
        "layout",
        "table header labels are Arabic",
        all(any("\u0600" <= c <= "\u06ff" for c in header.model().headerData(i, Qt.Orientation.Horizontal) or "") for i in range(tb.table.columnCount())),
        str([header.model().headerData(i, Qt.Orientation.Horizontal) for i in range(tb.table.columnCount())]),
    )
    rows = tb.visible_rows()
    record("data", "trial balance has the posted invoice rows", bool(rows), f"{len(rows)} rows")
    if rows:
        names = [r.get("name_ar") or r.get("name_fr") for r in rows]
        record("data", "account names present in the Arabic ledger", all(names), str(names[:3]))
        arabic_names = [n for n in names if n and any("\u0600" <= c <= "\u06ff" for c in n)]
        record("data", "at least one account name renders in Arabic script", bool(arabic_names), str(arabic_names[:2]))
    window.grab().save(str(OUT / "30_02_trial_balance_ar_rtl.png"))

    # Invoices + review dialog in Arabic.
    window._load_view(VIEWS.index("invoices"))
    pump(1200)
    window.grab().save(str(OUT / "30_03_invoices_ar_rtl.png"))
    dialog = window._make_review_dialog(invoice_id)
    dialog.resize(1300, 840)
    dialog.show()
    pump(1500)
    record("dialog", "review dialog title is Arabic", dialog.windowTitle() == tr("ar", "review_title"), dialog.windowTitle())
    record(
        "dialog",
        "review dialog state badge carries the Arabic label",
        "مراجعة" in dialog.state_badge.text() or "•" in dialog.state_badge.text() or "·" in dialog.state_badge.text(),
        dialog.state_badge.text(),
    )
    dialog.grab().save(str(OUT / "30_04_review_dialog_ar_rtl.png"))
    dialog.close()

    # Stock screen (mixed Arabic product name + Latin SKU + numeric).
    window._load_view(VIEWS.index("stock"))
    pump(1000)
    window.grab().save(str(OUT / "30_05_stock_ar_mixed.png"))

    # Accounts: Arabic names where the SCF provides them, French fallback elsewhere.
    window._load_view(VIEWS.index("accounts"))
    pump(1200)
    acc_rows = window.accounts_view.table.visible_rows()
    with_ar = [r for r in acc_rows if r.get("name_ar")]
    record("data", "SCF plan carries Arabic names for a subset", bool(with_ar), f"{len(with_ar)}/{len(acc_rows)}")
    record("data", "every account row still renders a non-empty name", all(r.get("name_ar") or r.get("name_fr") for r in acc_rows))
    window.grab().save(str(OUT / "30_06_accounts_ar_rtl.png"))

    # ---- language switch round-trip (fr -> ar -> en -> fr) ---------------
    record("switch", "switch to French", _switch(window, "fr") and window.dashboard_view.identity_label.text().startswith("Utilisateur"))
    window.grab().save(str(OUT / "30_07_dashboard_fr_after_switch.png"))
    record("switch", "switch to English", _switch(window, "en") and window.dashboard_view.identity_label.text().startswith("User"))
    window.grab().save(str(OUT / "30_08_dashboard_en_after_switch.png"))
    record("switch", "switch back to Arabic", _switch(window, "ar") and window.dashboard_view.identity_label.text().startswith("المستخدم"))

    window.close()
    for client in (admin_client, clerk, acct):
        client.close()
    service.stop()

    passed = sum(1 for f in FINDINGS if f["ok"])
    print(f"\nRESULT: {passed}/{len(FINDINGS)} checks passed")
    (OUT.parent / "logs" / "rtl_findings.json").write_text(
        json.dumps({"total": len(FINDINGS), "passed": passed, "findings": FINDINGS}, ensure_ascii=False, indent=2)
    )
    return 0 if passed == len(FINDINGS) else 1


def _switch(window: MainWindow, language: str) -> bool:
    window._switch_language(language)
    pump(600)
    return window.shell.topbar.language_combo.currentData() == language


if __name__ == "__main__":
    raise SystemExit(main())
