# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 2 — Algerian SCF Localization, Conservative Migration, Tax/Timbre & G50 Engine."""

import csv
import json
import os
import re
import sys
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .database import ERPDatabase, TenantIsolationError
from .decimal_utils import format_dzd, round_dzd, to_decimal

REPO_ROOT = Path(__file__).resolve().parent.parent
ODOO_L10N_DZ_DIR = REPO_ROOT / "vendor" / "odoo_18_l10n_dz"
AZINAK_FIX_DIR = REPO_ROOT / "vendor" / "azinak_l10n_dz_account_fix" / "l10n_dz_account_fix"
TAGHRAS_STATE_XML = (
    REPO_ROOT / "vendor" / "taghrastech_odoo_l10n_dz" / "tt_l10n_dz_state" / "data" / "res_country_state.xml"
)

#: Environment override for the vendored SCF source data. Set by a packager
#: (or an operator with a reviewed data copy) when the checkout-relative
#: location does not apply, e.g. inside frozen PyInstaller builds.
VENDOR_DATA_DIR_ENV = "ERP_VENDOR_DATA_DIR"


def vendor_data_dir() -> Path:
    """Resolve the vendored official chart source data, frozen-build aware.

    Resolution order (explicit, documented, testable):

    1. ``ERP_VENDOR_DATA_DIR`` - explicit override; a packager who moves the
       data must say so rather than rely on search paths.
    2. Frozen builds (PyInstaller): the data directory bundled next to the
       executable payload (``sys._MEIPASS``); ``tools/build_desktop.py``
       bundles ``vendor/`` with ``--add-data``.
    3. The repository checkout location - the behaviour every existing test
       and deployment relies on.

    The official Odoo 18.0 ``l10n_dz`` template files are MPL-2.0 (Odoo's
    module licence), used here as data under that licence; the fix and
    wilaya add-ons are the vendored third-party repositories reviewed in
    ``docs/DEPENDENCY_AND_LICENSE_REGISTER.md``. This function only locates
    them - it never invents account mappings.
    """
    override = os.environ.get(VENDOR_DATA_DIR_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "vendor"
            if candidate.is_dir():
                return candidate
    return REPO_ROOT / "vendor"

# Verified account-type fixes from azinak/l10n-dz-account-fix/models/migration.py
ACCOUNT_TYPE_FIXES: Dict[str, Tuple[str, str]] = {
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

RECONCILIATION_ACCOUNTS: Tuple[str, ...] = ("409", "419", "421", "425")

# Verified French translation bugs in Odoo 18/19 account.group-dz.csv
GROUP_TRANSLATION_FIXES: Dict[str, Dict[str, str]] = {
    "32": {
        "official_fr": "Pertes de valeur sur Autres approvisionnements",
        "corrected_fr": "Autres approvisionnements",
        "name_ar": "تموينات أخرى",
    },
    "66": {
        "official_fr": "Charges financières différées",
        "corrected_fr": "Charges financières",
        "name_ar": "أعباء مالية",
    },
}

# Core Arabic SCF labels for key accounts
ARABIC_SCF_ACCOUNT_LABELS: Dict[str, str] = {
    "101": "رأس المال الصادر أو رأس المال الاجتماعي",
    "133": "الضرائب المؤجلة - أصول",
    "134": "الضرائب المؤجلة - خصوم",
    "164": "الاقتراضات لدى مؤسسات القرض",
    "30": "مخزونات البضائع",
    "31": "المواد الأولية واللوازم",
    "32": "تموينات أخرى",
    "368": "أصول غير جارية محتفظ بها برسم البيع",
    "380": "المشتريات المخزنة",
    "401": "موردو المخزونات والخدمات",
    "408": "موردون - فواتير لم تصل بعد",
    "409": "الموردون المدينون: التسبيقات والدفعات على الحساب",
    "411": "الزبائن",
    "412": "الزبائن: أوراق القبض (نقاط البيع)",
    "413": "الزبائن: أوراق القبض",
    "419": "الزبائن الدائنون: التسبيقات المستلمة",
    "421": "المستخدمون - الأجور المستحقة",
    "425": "المستخدمون - التسبيقات والدفعات الممنوحة",
    "4455": "الرسم على القيمة المضافة الواجب دفعه",
    "4456": "الرسم على القيمة المضافة القابل للاسترجاع",
    "4457": "الرسم على القيمة المضافة المحصل",
    "476": "النفقات رهن التخصيص",
    "481": "المؤونات - الخصوم الجارية",
    "509": "التسديدات الباقية الواجب القيام بها عن القيم المنقولة للتوظيف غير المسددة",
    "512": "البنوك - الحسابات الجارية",
    "519": "المساهمات البنكية الجارية",
    "521": "الأدوات المالية المشتقة - أصول",
    "529": "الأدوات المالية المشتقة - خصوم",
    "53": "الصندوق",
    "591": "خسائر القيمة عن القيم المودعة في البنوك والمؤسسات المالية",
    "594": "خسائر القيمة عن الوكالات المالية للتسبيقات والاعتمادات",
    "600": "مشتريات البضائع المبيعة",
    "601": "المواد الأولية المستهلكة",
    "603": "تغيرات المخزونات",
    "642": "الضرائب والرسوم غير المسترجعة على رقم الأعمال وحقوق الطابع",
    "700": "المبيعات من البضائع",
}

NIF_REGEX = re.compile(r"^[0-9]{15}([0-9]{5})?$")
NIS_REGEX = re.compile(r"^[0-9]{15}$")
AI_REGEX = re.compile(r"^[0-9]{11}$")
NIN_REGEX = re.compile(r"^[0-9]{18}$")
RC_REGEX = re.compile(r"^[0-9A-Z/\-\s]{6,32}$")

DEFAULT_TIMBRE_BRACKETS = [
    {"up_to": "30000.00", "rate": "0.01"},
    {"up_to": "100000.00", "rate": "0.015"},
    {"up_to": None, "rate": "0.02"},
]


def validate_algerian_identifiers(
    nif: Optional[str] = None,
    nis: Optional[str] = None,
    ai: Optional[str] = None,
    rc: Optional[str] = None,
    nin: Optional[str] = None,
) -> List[str]:
    """Validate Algerian fiscal/commercial identifiers and return any error messages."""
    errors: List[str] = []
    if nif is not None and nif != "":
        if not NIF_REGEX.match(nif.strip()):
            errors.append(f"Invalid NIF '{nif}': must be 15 or 20 digits.")
    if nis is not None and nis != "":
        if not NIS_REGEX.match(nis.strip()):
            errors.append(f"Invalid NIS '{nis}': must be 15 digits.")
    if ai is not None and ai != "":
        if not AI_REGEX.match(ai.strip()):
            errors.append(f"Invalid AI '{ai}': must be 11 digits.")
    if nin is not None and nin != "":
        if not NIN_REGEX.match(nin.strip()):
            errors.append(f"Invalid NIN '{nin}': must be 18 digits.")
    if rc is not None and rc != "":
        if not RC_REGEX.match(rc.strip().upper()):
            errors.append(f"Invalid RC '{rc}': must be 6-32 alphanumeric characters.")
    return errors


def inspect_upstream_odoo_l10n_dz() -> Dict[str, Any]:
    """Directly parse the vendored official Odoo 18.0 l10n_dz files to produce verified evidence."""
    vendor = vendor_data_dir()
    odoo_dir = vendor / "odoo_18_l10n_dz"
    taghras_state_xml = (
        vendor / "taghrastech_odoo_l10n_dz" / "tt_l10n_dz_state" / "data" / "res_country_state.xml"
    )
    missing = [
        str(relative)
        for relative in (
            odoo_dir / "models" / "template_dz.py",
            odoo_dir / "data" / "template" / "account.account-dz.csv",
            odoo_dir / "data" / "template" / "account.group-dz.csv",
            odoo_dir / "data" / "template" / "account.tax-dz.csv",
            odoo_dir / "data" / "tax_report.xml",
            taghras_state_xml,
        )
        if not relative.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "The official chart source data is incomplete or missing under "
            f"{vendor}. Missing: {missing}. For a checkout this data lives in "
            "vendor/; for a packaged application set the ERP_VENDOR_DATA_DIR "
            "environment variable to the directory containing the reviewed "
            "vendor data. No substitute chart is ever invented."
        )
    template_py = (odoo_dir / "models" / "template_dz.py").read_text(encoding="utf-8")
    receivable_match = re.search(r"'property_account_receivable_id':\s*'([^']+)'", template_py)
    official_receivable = receivable_match.group(1) if receivable_match else None

    accounts_by_code: Dict[str, Dict[str, Any]] = {}
    with (odoo_dir / "data" / "template" / "account.account-dz.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            code = row["code"].strip()
            accounts_by_code[code] = {
                "id": row["id"].strip(),
                "code": code,
                "name_en": row["name"].strip(),
                "name_fr": row["name@fr"].strip(),
                "account_type": row["account_type"].strip(),
                "reconcile": row["reconcile"].strip() == "True",
                "has_arabic": "name@ar" in row and bool(row.get("name@ar")),
            }

    groups_by_code: Dict[str, Dict[str, str]] = {}
    with (odoo_dir / "data" / "template" / "account.group-dz.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            prefix = row["code_prefix_start"].strip()
            groups_by_code[prefix] = {
                "id": row["id"].strip(),
                "name_en": row["name"].strip(),
                "name_fr": row["name@fr"].strip(),
            }

    # Inspect tax tags in account.tax-dz.csv vs tax_report.xml
    tax_csv_text = (odoo_dir / "data" / "template" / "account.tax-dz.csv").read_text(encoding="utf-8")
    tax_report_xml = (odoo_dir / "data" / "tax_report.xml").read_text(encoding="utf-8")
    referenced_in_report = sorted(set(re.findall(r"E3B[0-9]{2}", tax_report_xml)))
    defined_in_tax_csv = sorted(set(re.findall(r"E3B[0-9]{2}", tax_csv_text)))
    orphaned_report_boxes = [
        box for box in referenced_in_report if box.startswith(("E3B1", "E3B2", "E3B3")) and box not in defined_in_tax_csv
    ]

    # Parse 69 Wilayas from TaghrasTech XML
    tree = ET.parse(taghras_state_xml)
    wilayas = []
    for rec in tree.findall(".//record[@model='res.country.state']"):
        name_el = rec.find("field[@name='name']")
        code_el = rec.find("field[@name='code']")
        if name_el is not None and code_el is not None:
            wilayas.append({"code": code_el.text, "name": name_el.text})

    return {
        "official_default_receivable_xml_id": official_receivable,
        "total_accounts": len(accounts_by_code),
        "accounts_by_code": accounts_by_code,
        "groups_by_code": groups_by_code,
        "referenced_in_report": referenced_in_report,
        "defined_in_tax_csv": defined_in_tax_csv,
        "orphaned_turnover_boxes": orphaned_report_boxes,
        "wilaya_count": len(wilayas),
        "wilayas": wilayas,
    }


def load_official_odoo_chart(
    db: ERPDatabase,
    company_id: int,
    apply_fixes_immediately: bool = False,
    actor_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Load the official Odoo 18.0 Algerian chart of accounts for a company.

    If apply_fixes_immediately is False, loads the unpatched official Odoo 18.0 values
    so that existing-company migration behavior can be tested deterministically.
    """
    upstream = inspect_upstream_odoo_l10n_dz()
    with db.transaction() as conn:
        comp = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not comp:
            raise ValueError(f"Company id={company_id} not found.")

        default_rec = "411" if apply_fixes_immediately else "413"
        conn.execute(
            "UPDATE companies SET default_receivable_code = ?, default_payable_code = '401' WHERE id = ?",
            (default_rec, company_id),
        )

        for code, acct in upstream["accounts_by_code"].items():
            acct_type = acct["account_type"]
            reconcile = 1 if acct["reconcile"] else 0
            if apply_fixes_immediately:
                if code in ACCOUNT_TYPE_FIXES:
                    acct_type = ACCOUNT_TYPE_FIXES[code][1]
                if code in RECONCILIATION_ACCOUNTS:
                    reconcile = 1
            name_ar = ARABIC_SCF_ACCOUNT_LABELS.get(code)
            conn.execute(
                """
                INSERT INTO accounts (
                    company_id, xml_id, code, name_en, name_fr, name_ar,
                    account_type, reconcile, active, is_custom_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                ON CONFLICT(company_id, code) DO NOTHING
                """,
                (
                    company_id,
                    acct["id"],
                    code,
                    acct["name_en"],
                    acct["name_fr"],
                    name_ar,
                    acct_type,
                    reconcile,
                ),
            )

        # Ensure essential perpetual inventory clearing account 380 exists
        conn.execute(
            """
            INSERT INTO accounts (
                company_id, xml_id, code, name_en, name_fr, name_ar,
                account_type, reconcile, active, is_custom_type
            ) VALUES (?, 'l10n_dz_380', '380', 'Stored purchases (Clearing)', 'Achats stockés (Compte transitoire)',
                      'المشتريات المخزنة', 'liability_current', 1, 1, 0)
            ON CONFLICT(company_id, code) DO NOTHING
            """,
            (company_id,),
        )
        conn.execute(
            """
            INSERT INTO accounts (
                company_id, xml_id, code, name_en, name_fr, name_ar,
                account_type, reconcile, active, is_custom_type
            ) VALUES (?, 'l10n_dz_642', '642', 'Non-recoverable turnover taxes & Stamp duty',
                      'Impôts et taxes non récupérables et droits de timbre',
                      'الضرائب والرسوم غير المسترجعة وحقوق الطابع', 'expense', 0, 1, 0)
            ON CONFLICT(company_id, code) DO NOTHING
            """,
            (company_id,),
        )

        for prefix, grp in upstream["groups_by_code"].items():
            name_fr = grp["name_fr"]
            name_ar = None
            if apply_fixes_immediately and prefix in GROUP_TRANSLATION_FIXES:
                name_fr = GROUP_TRANSLATION_FIXES[prefix]["corrected_fr"]
                name_ar = GROUP_TRANSLATION_FIXES[prefix]["name_ar"]
            conn.execute(
                """
                INSERT INTO account_groups (company_id, xml_id, code_prefix, name_en, name_fr, name_ar)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, xml_id) DO NOTHING
                """,
                (company_id, grp["id"], prefix, grp["name_en"], name_fr, name_ar),
            )

        # Seed standard Algerian VAT taxes & versioned Timbre rule
        standard_taxes = [
            ("l10n_dz_vat_sale_19_prod", "19% Goods Production", "19% Production de biens", "sale", "19.0", "4457", "NONE", "E3B21_taxable", "E3B21_balance"),
            ("l10n_dz_vat_sale_9_g", "9% Goods", "9% Biens", "sale", "9.0", "4457", "NONE", "E3B11_taxable", "E3B11_balance"),
            ("l10n_dz_vat_sale_0_exempt", "0% Domestic Exemption", "0% Exonération nationale", "sale", "0.0", None, "EXEMPT", "E3B21_exempt", None),
            ("l10n_dz_vat_sale_0_export", "0% Export Exemption", "0% Exonération exportation", "sale", "0.0", None, "EXPORT", "E3B21_export", None),
            ("l10n_dz_vat_purchase_19", "19% Purchase Goods & Services", "19% Achats biens et services", "purchase", "19.0", "4456", "NONE", "E3B91_base", "E3B91_balance"),
            ("l10n_dz_vat_purchase_9", "9% Purchase Goods & Services", "9% Achats biens et services", "purchase", "9.0", "4456", "NONE", "E3B91_base", "E3B91_balance"),
            ("l10n_dz_vat_purchase_19_depr", "19% Purchase Depreciable Assets", "19% Achats biens amortissables", "purchase", "19.0", "4456", "NONE", "E3B92_base", "E3B92_balance"),
            ("l10n_dz_vat_purchase_0_exempt", "0% Exempt Purchase", "0% Achat exonéré", "purchase", "0.0", None, "EXEMPT", None, None),
        ]
        for xml_id, name_en, name_fr, use, rate, acct_code, reason, b_tag, t_tag in standard_taxes:
            conn.execute(
                """
                INSERT INTO taxes (
                    company_id, xml_id, name_en, name_fr, type_tax_use, rate_percent,
                    tax_account_code, exemption_reason, g50_base_tag, g50_tax_tag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, xml_id) DO NOTHING
                """,
                (company_id, xml_id, name_en, name_fr, use, rate, acct_code, reason, b_tag, t_tag),
            )

        conn.execute(
            """
            INSERT INTO timbre_rules (
                company_id, version_code, valid_from, valid_to,
                min_amount_dzd, max_amount_dzd, threshold_dzd, brackets_json
            ) VALUES (?, 'LF-2025-CASH-TIMBRE', '2025-01-01', NULL, '5.00', NULL, '300.00', ?)
            ON CONFLICT(company_id, version_code) DO NOTHING
            """,
            (company_id, json.dumps(DEFAULT_TIMBRE_BRACKETS)),
        )

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=actor_user_id,
            action_type="LOAD_CHART_TEMPLATE",
            target_model="companies",
            target_id=str(company_id),
            after_state={"apply_fixes_immediately": apply_fixes_immediately, "default_receivable_code": default_rec},
        )

    return {"company_id": company_id, "default_receivable_code": default_rec}


def apply_algerian_localization_fixes(
    db: ERPDatabase,
    company_id: int,
    actor_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Conservatively and idempotently migrate an Algerian company (matching azinak/l10n-dz-account-fix).

    Rules enforced:
    1. Only companies with country_code == 'DZ' and chart_template == 'dz' are modified.
    2. default_receivable_code is changed from '413' to '411' ONLY if it is currently '413'.
    3. Account types are changed ONLY if they match Odoo's known buggy official_type and is_custom_type == 0.
    4. Reconciliation flags on 409, 419, 421, 425 are enabled if currently 0.
    5. Archived accounts (active == 0) have their type fixed WITHOUT reactivating them.
    6. Historical posted journal entries are NEVER touched.
    7. Every change is logged to audit_events.
    """
    changes: Dict[str, Any] = {
        "company_id": company_id,
        "skipped_non_dz": False,
        "receivable_updated": False,
        "updated_account_types": {},
        "updated_reconcile_codes": [],
        "updated_groups": [],
    }
    with db.transaction() as conn:
        comp = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        if not comp:
            raise ValueError(f"Company id={company_id} not found.")
        if comp["country_code"] != "DZ" or comp["chart_template"] != "dz":
            changes["skipped_non_dz"] = True
            return changes

        if comp["default_receivable_code"] == "413":
            conn.execute(
                "UPDATE companies SET default_receivable_code = '411' WHERE id = ?",
                (company_id,),
            )
            changes["receivable_updated"] = True

        for code, (official_type, corrected_type) in ACCOUNT_TYPE_FIXES.items():
            acct = conn.execute(
                "SELECT * FROM accounts WHERE company_id = ? AND code = ?",
                (company_id, code),
            ).fetchone()
            if acct and acct["account_type"] == official_type and acct["is_custom_type"] == 0:
                conn.execute(
                    "UPDATE accounts SET account_type = ? WHERE id = ?",
                    (corrected_type, acct["id"]),
                )
                changes["updated_account_types"][code] = {
                    "from": official_type,
                    "to": corrected_type,
                    "active_preserved": bool(acct["active"]),
                }

        for code in RECONCILIATION_ACCOUNTS:
            acct = conn.execute(
                "SELECT * FROM accounts WHERE company_id = ? AND code = ?",
                (company_id, code),
            ).fetchone()
            if acct and acct["reconcile"] == 0:
                conn.execute(
                    "UPDATE accounts SET reconcile = 1 WHERE id = ?",
                    (acct["id"],),
                )
                changes["updated_reconcile_codes"].append(code)

        for code, ar_label in ARABIC_SCF_ACCOUNT_LABELS.items():
            conn.execute(
                "UPDATE accounts SET name_ar = ? WHERE company_id = ? AND code = ? AND name_ar IS NULL",
                (ar_label, company_id, code),
            )

        for prefix, fix in GROUP_TRANSLATION_FIXES.items():
            grp = conn.execute(
                "SELECT * FROM account_groups WHERE company_id = ? AND code_prefix = ?",
                (company_id, prefix),
            ).fetchone()
            if grp and grp["name_fr"] == fix["official_fr"]:
                conn.execute(
                    "UPDATE account_groups SET name_fr = ?, name_ar = ? WHERE id = ?",
                    (fix["corrected_fr"], fix["name_ar"], grp["id"]),
                )
                changes["updated_groups"].append(prefix)

        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=actor_user_id,
            action_type="MIGRATE_L10N_DZ_FIX",
            target_model="companies",
            target_id=str(company_id),
            after_state=changes,
        )

    return changes


class TaxRuleConfigurationError(Exception):
    """The tax configuration cannot answer the question being asked.

    Raises instead of guessing: a missing or ambiguous rule would silently
    produce a different amount of tax than the law (and the operator) expects.
    """


#: Version label recorded when no configured rule exists and the compiled-in
#: constants are used. It is deliberately *not* a plausible legal version code,
#: so a stored document can always be told apart from a configured one.
BUILTIN_TIMBRE_RULE_VERSION = "builtin-default-v1"


@dataclass(frozen=True)
class TimbreRule:
    """One effective-dated Timbre rule, as resolved for a specific date."""

    version_code: str
    valid_from: str
    valid_to: Optional[str]
    min_amount_dzd: str
    max_amount_dzd: Optional[str]
    threshold_dzd: str
    brackets: List[Dict[str, Optional[str]]] = field(default_factory=list)
    source: str = "configured"  # "configured" | "builtin_default"

    def as_kwargs(self) -> Dict[str, Any]:
        """Keyword arguments for :func:`calculate_droit_de_timbre`."""
        return {
            "brackets": self.brackets,
            "min_duty": self.min_amount_dzd,
            "max_duty": self.max_amount_dzd,
            "threshold": self.threshold_dzd,
        }


def _builtin_timbre_rule() -> TimbreRule:
    return TimbreRule(
        version_code=BUILTIN_TIMBRE_RULE_VERSION,
        valid_from="1970-01-01",
        valid_to=None,
        min_amount_dzd="5.00",
        max_amount_dzd=None,
        threshold_dzd="300.00",
        brackets=[dict(b) for b in DEFAULT_TIMBRE_BRACKETS],
        source="builtin_default",
    )


def _normalise_iso_date(value: str, *, what: str) -> str:
    """Accept ``YYYY-MM-DD`` or a full ISO timestamp; compare on the date part."""
    text = (value or "").strip()
    if len(text) < 10:
        raise TaxRuleConfigurationError(f"{what} must be an ISO date, got {value!r}")
    day = text[:10]
    try:
        from datetime import date as _date

        _date.fromisoformat(day)
    except ValueError as exc:
        raise TaxRuleConfigurationError(f"{what} must be an ISO date, got {value!r}") from exc
    return day


def _parse_brackets(raw: Any, *, version_code: str) -> List[Dict[str, Optional[str]]]:
    """Validate a stored bracket table. Malformed configuration fails closed."""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise TaxRuleConfigurationError(
            f"TIMBRE_RULE_BRACKETS_INVALID:{version_code}: not JSON"
        ) from exc
    if not isinstance(parsed, list) or not parsed:
        raise TaxRuleConfigurationError(
            f"TIMBRE_RULE_BRACKETS_INVALID:{version_code}: expected a non-empty list"
        )
    brackets: List[Dict[str, Optional[str]]] = []
    for entry in parsed:
        if not isinstance(entry, dict) or "rate" not in entry:
            raise TaxRuleConfigurationError(
                f"TIMBRE_RULE_BRACKETS_INVALID:{version_code}: entry without a rate"
            )
        up_to = entry.get("up_to")
        rate = to_decimal(entry["rate"])
        if rate < 0:
            raise TaxRuleConfigurationError(
                f"TIMBRE_RULE_BRACKETS_INVALID:{version_code}: negative rate"
            )
        if up_to is not None:
            to_decimal(up_to)  # must be a valid amount
        brackets.append({"up_to": None if up_to is None else str(up_to), "rate": str(rate)})
    if brackets[-1]["up_to"] is not None:
        # The last bracket must be open-ended, or a large invoice would silently
        # fall back to the 1% default instead of reaching the top rate.
        raise TaxRuleConfigurationError(
            f"TIMBRE_RULE_BRACKETS_INVALID:{version_code}: last bracket must be open-ended"
        )
    return brackets


def resolve_timbre_rule(
    conn: sqlite3.Connection,
    company_id: int,
    on_date: str,
) -> TimbreRule:
    """Return the Timbre rule in force for ``company_id`` on ``on_date``.

    Selection is by effective date, not by "the newest row": a rule that is
    configured but not yet in force must never be applied to an earlier
    document, and a superseded rule must still govern the documents it covered.

    * No configured rows at all -> the compiled-in constants, labelled
      ``builtin-default-v1`` so every stored document says which was used.
    * Rows exist but none covers the date -> :class:`TaxRuleConfigurationError`.
      This is the ambiguous case an operator must fix; guessing here would post
      a different amount of stamp duty than the law requires.
    """
    day = _normalise_iso_date(on_date, what="invoice date")
    rows = conn.execute(
        """
        SELECT version_code, valid_from, valid_to, min_amount_dzd, max_amount_dzd,
               threshold_dzd, brackets_json
        FROM timbre_rules
        WHERE company_id = ?
        ORDER BY valid_from DESC, id DESC
        """,
        (company_id,),
    ).fetchall()
    if not rows:
        return _builtin_timbre_rule()

    applicable = []
    for row in rows:
        valid_from = _normalise_iso_date(row["valid_from"], what="timbre rule valid_from")
        valid_to_raw = row["valid_to"]
        if valid_from > day:
            continue
        if valid_to_raw:
            if _normalise_iso_date(valid_to_raw, what="timbre rule valid_to") < day:
                continue
        applicable.append((valid_from, row))
    if not applicable:
        versions = ", ".join(sorted(str(r["version_code"]) for r in rows))
        raise TaxRuleConfigurationError(
            f"TIMBRE_RULE_NOT_EFFECTIVE_ON_DATE:company={company_id},date={day},configured={versions}"
        )

    applicable.sort(key=lambda pair: pair[0], reverse=True)
    _, chosen = applicable[0]
    return TimbreRule(
        version_code=str(chosen["version_code"]),
        valid_from=str(chosen["valid_from"]),
        valid_to=None if chosen["valid_to"] is None else str(chosen["valid_to"]),
        min_amount_dzd=format_dzd(chosen["min_amount_dzd"]),
        max_amount_dzd=None
        if chosen["max_amount_dzd"] in (None, "")
        else format_dzd(chosen["max_amount_dzd"]),
        threshold_dzd=format_dzd(chosen["threshold_dzd"]),
        brackets=_parse_brackets(chosen["brackets_json"], version_code=str(chosen["version_code"])),
        source="configured",
    )


#: The TVA rates this build was written against. Used only when a company has
#: no configured rate rows at all; the label is deliberately not a legal version.
BUILTIN_VAT_RATE_VERSION = "builtin-default-v1"
BUILTIN_VAT_RATES: Tuple[Decimal, ...] = (Decimal("0.0"), Decimal("9.0"), Decimal("19.0"))


@dataclass(frozen=True)
class VatRateSet:
    """The set of TVA rates available to a company on a given date."""

    version_label: str
    rates: Tuple[Decimal, ...]
    source: str = "configured"  # "configured" | "builtin_default"

    def allows(self, rate: Decimal) -> bool:
        return to_decimal(rate) in self.rates

    def describe(self) -> str:
        return ",".join(f"{r:.1f}" for r in self.rates)


def resolve_vat_rates(
    conn: sqlite3.Connection,
    company_id: int,
    on_date: str,
) -> VatRateSet:
    """Return the TVA rates configured as in force for ``company_id`` on ``on_date``.

    Mirrors :func:`resolve_timbre_rule`: effective dates decide, not "whatever
    rows exist". A rate whose validity window does not cover the invoice date
    must not be accepted on that invoice, and a company with rate rows none of
    which is in force raises rather than silently falling back to built-in
    constants (that would validate an invoice at a rate nobody configured).

    Exemptions are configuration too: a row with ``exemption_reason != 'NONE'``
    contributes its (normally 0) rate and is otherwise not special-cased here.
    """
    day = _normalise_iso_date(on_date, what="invoice date")
    rows = conn.execute(
        """
        SELECT xml_id, rate_percent, valid_from, valid_to, active, exemption_reason
        FROM taxes
        WHERE company_id = ?
        ORDER BY valid_from DESC, id DESC
        """,
        (company_id,),
    ).fetchall()
    if not rows:
        return VatRateSet(BUILTIN_VAT_RATE_VERSION, BUILTIN_VAT_RATES, source="builtin_default")

    in_force: List[Any] = []
    for row in rows:
        if not int(row["active"] or 0):
            continue
        valid_from = _normalise_iso_date(row["valid_from"], what="tax valid_from")
        if valid_from > day:
            continue
        if row["valid_to"]:
            if _normalise_iso_date(row["valid_to"], what="tax valid_to") < day:
                continue
        in_force.append(row)
    if not in_force:
        labels = ", ".join(sorted(str(r["xml_id"]) for r in rows))
        raise TaxRuleConfigurationError(
            f"VAT_RATE_NOT_EFFECTIVE_ON_DATE:company={company_id},date={day},configured={labels}"
        )
    rates = tuple(sorted({to_decimal(row["rate_percent"]) for row in in_force}))
    # The label names the configuration generation: the date the newest in-force
    # rows became effective. It deliberately does not name a single row, because
    # several rows typically share one effective date and picking one of them
    # would make the label depend on insert order.
    latest_from = max(str(row["valid_from"])[:10] for row in in_force)
    label = f"configured@{latest_from}"
    return VatRateSet(label, rates, source="configured")


def timbre_rule_from_company_date(
    db: "ERPDatabase", company_id: int, on_date: str
) -> TimbreRule:
    """Resolve the rule with the database's own connection (read-only query)."""
    with db.transaction() as conn:
        return resolve_timbre_rule(conn, company_id, on_date)


def calculate_droit_de_timbre(
    amount_ht_plus_tva: Any,
    payment_method: str,
    brackets: Optional[List[Dict[str, Optional[str]]]] = None,
    min_duty: str = "5.00",
    max_duty: Optional[str] = None,
    threshold: str = "300.00",
) -> Decimal:
    """Compute Algerian Droit de Timbre for CASH payments using exact Decimal arithmetic.

    Non-cash payment methods (BANK_TRANSFER, CHEQUE) always incur 0.00 DZD stamp duty.
    """
    base = round_dzd(amount_ht_plus_tva)
    if payment_method.upper() != "CASH":
        return Decimal("0.00")
    if base <= to_decimal(threshold):
        return Decimal("0.00")

    active_brackets = brackets or DEFAULT_TIMBRE_BRACKETS
    selected_rate = Decimal("0.01")
    for b in active_brackets:
        up_to = b.get("up_to")
        if up_to is None or base <= to_decimal(up_to):
            selected_rate = to_decimal(b["rate"])
            break

    duty = round_dzd(base * selected_rate)
    min_dec = round_dzd(min_duty)
    if duty < min_dec:
        duty = min_dec
    if max_duty is not None:
        max_dec = round_dzd(max_duty)
        if duty > max_dec:
            duty = max_dec
    return duty


def compute_invoice_totals(
    lines: List[Dict[str, Any]],
    payment_method: str = "BANK_TRANSFER",
) -> Dict[str, Any]:
    """Deterministic decimal calculation of invoice lines, tax breakdown, Timbre, and grand total."""
    total_ht = Decimal("0.00")
    total_tva = Decimal("0.00")
    tax_breakdown: Dict[str, Dict[str, Decimal]] = {}
    computed_lines: List[Dict[str, str]] = []

    for line in lines:
        qty = to_decimal(line["qty"])
        unit_price = to_decimal(line["unit_price_dzd"])
        discount_pct = to_decimal(line.get("discount_pct", "0.00"))
        tax_rate = to_decimal(line.get("tax_rate", "19.0"))

        gross = qty * unit_price
        discount_factor = (Decimal("100.00") - discount_pct) / Decimal("100.00")
        line_ht = round_dzd(gross * discount_factor)
        line_tva = round_dzd(line_ht * (tax_rate / Decimal("100.00")))
        line_ttc = round_dzd(line_ht + line_tva)

        total_ht = round_dzd(total_ht + line_ht)
        total_tva = round_dzd(total_tva + line_tva)

        rate_key = f"{tax_rate:.1f}%"
        if rate_key not in tax_breakdown:
            tax_breakdown[rate_key] = {"base_dzd": Decimal("0.00"), "tax_dzd": Decimal("0.00")}
        tax_breakdown[rate_key]["base_dzd"] = round_dzd(tax_breakdown[rate_key]["base_dzd"] + line_ht)
        tax_breakdown[rate_key]["tax_dzd"] = round_dzd(tax_breakdown[rate_key]["tax_dzd"] + line_tva)

        computed_lines.append(
            {
                "description": str(line.get("description", "Item")),
                "qty": f"{qty:.6f}",
                "unit_price_dzd": f"{unit_price:.6f}",
                "discount_pct": format_dzd(discount_pct),
                "tax_rate": f"{tax_rate:.1f}",
                "line_ht_dzd": format_dzd(line_ht),
                "line_tva_dzd": format_dzd(line_tva),
                "line_ttc_dzd": format_dzd(line_ttc),
            }
        )

    timbre = calculate_droit_de_timbre(total_ht + total_tva, payment_method=payment_method)
    total_ttc = round_dzd(total_ht + total_tva + timbre)

    return {
        "amount_ht_dzd": format_dzd(total_ht),
        "amount_tva_dzd": format_dzd(total_tva),
        "amount_timbre_dzd": format_dzd(timbre),
        "amount_ttc_dzd": format_dzd(total_ttc),
        "tax_breakdown": {
            k: {"base_dzd": format_dzd(v["base_dzd"]), "tax_dzd": format_dzd(v["tax_dzd"])}
            for k, v in tax_breakdown.items()
        },
        "lines": computed_lines,
    }


def generate_g50_preparation_workbook(
    db: ERPDatabase,
    company_id: int,
    requesting_company_id: int,
    period_prefix: str,
    prior_vat_credit_dzd: str = "0.00",
) -> Dict[str, Any]:
    """Generate a traceable G50 preparation workbook from posted journal entries for a given YYYY-MM period."""
    if company_id != requesting_company_id:
        raise TenantIsolationError("Cross-company G50 workbook access is prohibited.")

    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT jel.*, je.entry_number, je.entry_date
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE jel.company_id = ?
              AND je.state = 'POSTED'
              AND substr(je.entry_date, 1, 7) = ?
            """,
            (company_id, period_prefix),
        ).fetchall()

    collected_vat = Decimal("0.00")
    recoverable_vat_goods = Decimal("0.00")
    recoverable_vat_assets = Decimal("0.00")
    timbre_total = Decimal("0.00")
    trace_lines: List[Dict[str, Any]] = []

    for r in rows:
        debit = to_decimal(r["debit_dzd"])
        credit = to_decimal(r["credit_dzd"])
        acct = r["account_code"]
        tag = r["g50_tag"] or ""
        if acct == "4457":
            collected_vat = round_dzd(collected_vat + (credit - debit))
            trace_lines.append({"entry": r["entry_number"], "box": "C_TVA_COLLECTED", "amount": format_dzd(credit - debit)})
        elif acct == "4456":
            net_rec = round_dzd(debit - credit)
            if "E3B92" in tag:
                recoverable_vat_assets = round_dzd(recoverable_vat_assets + net_rec)
                trace_lines.append({"entry": r["entry_number"], "box": "E3B92_TVA_ASSETS", "amount": format_dzd(net_rec)})
            else:
                recoverable_vat_goods = round_dzd(recoverable_vat_goods + net_rec)
                trace_lines.append({"entry": r["entry_number"], "box": "E3B91_TVA_GOODS_SERVICES", "amount": format_dzd(net_rec)})
        elif acct in ("44575", "642") and "TIMBRE" in tag:
            amt = round_dzd((credit - debit) if acct == "44575" else (debit - credit))
            timbre_total = round_dzd(timbre_total + amt)
            # Box E2 is traceable like every other box: without this line the
            # workbook showed a stamp-duty total with no entry behind it.
            trace_lines.append({"entry": r["entry_number"], "box": "E2_DROIT_DE_TIMBRE", "amount": format_dzd(amt)})

    precompte = round_dzd(prior_vat_credit_dzd)
    total_deductions_b = round_dzd(precompte + recoverable_vat_goods + recoverable_vat_assets)
    vat_payable_e3b120 = max(Decimal("0.00"), round_dzd(collected_vat - total_deductions_b))
    vat_carryforward_e3b130 = max(Decimal("0.00"), round_dzd(total_deductions_b - collected_vat))

    return {
        "company_id": company_id,
        "period": period_prefix,
        "statutory_status": "PREPARATORY_WORKBOOK_REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF",
        "boxes": {
            "E3B90_prior_credit_precompte": format_dzd(precompte),
            "E3B91_recoverable_vat_goods_services": format_dzd(recoverable_vat_goods),
            "E3B92_recoverable_vat_depreciable_assets": format_dzd(recoverable_vat_assets),
            "E3B100_total_collected_vat_c": format_dzd(collected_vat),
            "E3B110_total_deductions_b": format_dzd(total_deductions_b),
            "E3B120_vat_payable": format_dzd(vat_payable_e3b120),
            "E3B130_credit_to_carry_forward": format_dzd(vat_carryforward_e3b130),
            "E2_droit_de_timbre": format_dzd(timbre_total),
        },
        "traceability_count": len(trace_lines),
        "traceability": trace_lines,
    }
