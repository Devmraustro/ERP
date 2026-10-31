# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 2 — Algerian SCF Localization, Conservative Migration, Tax/Timbre & G50 Engine."""

import csv
import hashlib
import json
import os
import re
import sys
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .database import AuthorizationError, ERPDatabase, TenantIsolationError
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

        # ``max_amount_dzd = NULL`` here is a *deliberate, reviewable* statement
        # that this seeded rule carries no ceiling — it is not an omitted field.
        # The spec used to advertise a 10,000.00 DZD ceiling on the top band that
        # no rule ever applied (finding F-R2-1); the ceiling is undetermined
        # until the accountant of record supplies the rule text in force. See
        # docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md §3 and docs/REMAINING_BLOCKERS.md
        # (LB-1). Setting a figure here without that document would invent law.
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

    Only ``review_state = 'ACTIVE'`` rows are candidates. A ``DRAFT`` is a
    proposal that has not been approved by a second accountant (see
    :func:`approve_timbre_rule_version`), and a draft that resolved would let an
    unreviewed rate price a real invoice.
    """
    day = _normalise_iso_date(on_date, what="invoice date")
    # Only ACTIVE rules can govern an amount. A DRAFT is a proposal awaiting its
    # second, differently-attributed approval (finding R-05); letting it resolve
    # would mean an unreviewed rate silently priced a real invoice.
    rows = conn.execute(
        """
        SELECT version_code, valid_from, valid_to, min_amount_dzd, max_amount_dzd,
               threshold_dzd, brackets_json
        FROM timbre_rules
        WHERE company_id = ? AND review_state = 'ACTIVE'
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


# ---------------------------------------------------------------------------
# Phase 40 (F-04) — immutable provenance for the stamp-duty calculation
# ---------------------------------------------------------------------------
#
# The amount was always correct for the rule in force; what was missing was the
# evidence of *which* rule that was. A posted invoice kept ``amount_timbre_dzd``
# and nothing else, so a configuration change could leave an old invoice
# unexplainable — and nothing stopped a referenced rule version from being
# rewritten in place.
#
# Three things make the calculation auditable now:
#
# 1. **A content fingerprint.** ``timbre_rule_content_hash`` hashes the rule's
#    whole effective content (version, window, minimum, maximum, threshold,
#    brackets, source). Two rules with the same code but different values are
#    different rules, and the hash says so.
# 2. **A per-invoice snapshot.** The invoice stores the version, the source, the
#    hash, the effective window and a JSON snapshot of the calculation inputs, so
#    the arithmetic can be replayed without consulting the configuration.
# 3. **An append-only ledger** (``timbre_rule_versions``) recording the content of
#    every version the moment it is first used, plus triggers that refuse to
#    update or delete a ``timbre_rules`` row referenced by a posted invoice.
#
# An invoice validated before this existed keeps ``NULL``. That is reported as
# ``unavailable`` with the reason ``LEGACY_INVOICE_WITHOUT_RULE_IDENTITY`` —
# never backfilled with today's configuration, which would be a fabricated
# historical claim.

PROVENANCE_RECORDED = "recorded"
PROVENANCE_UNAVAILABLE = "unavailable"
PROVENANCE_STAGES = ("validation", "revalidation", "draft_modification", "posting", "reversal")


def timbre_rule_content_hash(rule: TimbreRule) -> str:
    """A stable fingerprint of everything that makes this rule *this* rule."""
    payload = {
        "version_code": rule.version_code,
        "source": rule.source,
        "valid_from": rule.valid_from,
        "valid_to": rule.valid_to,
        "min_amount_dzd": rule.min_amount_dzd,
        "max_amount_dzd": rule.max_amount_dzd,
        "threshold_dzd": rule.threshold_dzd,
        "brackets": [
            {"up_to": b.get("up_to"), "rate": b.get("rate")} for b in rule.brackets
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def timbre_provenance_snapshot(
    rule: TimbreRule,
    *,
    base_amount: Any,
    payment_method: str,
    computed_amount: Any,
    stage: str,
) -> Dict[str, Any]:
    """The calculation inputs, stored on the invoice beside the result.

    Everything needed to replay the arithmetic is here: the rule content, the
    base it was applied to, the payment method that decides *whether* duty
    applies at all, and the result. ``stage`` records when the identity was
    written, so "recorded at posting" is never mistaken for "recorded at
    validation".
    """
    if stage not in PROVENANCE_STAGES:
        raise ValueError(f"UNKNOWN_PROVENANCE_STAGE:{stage}")
    return {
        "schema": "timbre-provenance-v1",
        "stage": stage,
        "rule": {
            "version_code": rule.version_code,
            "source": rule.source,
            "content_hash": timbre_rule_content_hash(rule),
            "valid_from": rule.valid_from,
            "valid_to": rule.valid_to,
            "min_amount_dzd": rule.min_amount_dzd,
            "max_amount_dzd": rule.max_amount_dzd,
            "threshold_dzd": rule.threshold_dzd,
            "brackets": rule.brackets,
        },
        "calculation": {
            "base_amount_dzd": format_dzd(base_amount),
            "payment_method": (payment_method or "").upper(),
            "amount_timbre_dzd": format_dzd(computed_amount),
        },
    }


def record_timbre_rule_version(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    rule: TimbreRule,
    now_iso: str,
    invoice_id: Optional[int] = None,
) -> None:
    """First use of a rule version writes its content to the append-only ledger.

    ``DO NOTHING`` on conflict: the ledger keeps the *first* recorded content for
    a (company, version, hash) triple, because that is the copy the invoices were
    posted against. A rule whose content later changed produces a different hash
    and therefore a new row, which is exactly the audit trail we want.
    """
    conn.execute(
        """
        INSERT INTO timbre_rule_versions (
            company_id, version_code, content_hash, source,
            valid_from, valid_to, min_amount_dzd, max_amount_dzd,
            threshold_dzd, brackets_json, first_used_at, first_used_by_invoice_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, version_code, content_hash) DO NOTHING
        """,
        (
            company_id,
            rule.version_code,
            timbre_rule_content_hash(rule),
            rule.source,
            rule.valid_from,
            rule.valid_to,
            rule.min_amount_dzd,
            rule.max_amount_dzd,
            rule.threshold_dzd,
            json.dumps(rule.brackets, sort_keys=True),
            now_iso,
            invoice_id,
        ),
    )


def persist_invoice_timbre_provenance(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    invoice_id: int,
    rule: TimbreRule,
    base_amount: Any,
    payment_method: str,
    computed_amount: Any,
    now_iso: str,
    stage: str = "validation",
) -> Dict[str, Any]:
    """Write the rule identity and the calculation snapshot onto the invoice."""
    snapshot = timbre_provenance_snapshot(
        rule,
        base_amount=base_amount,
        payment_method=payment_method,
        computed_amount=computed_amount,
        stage=stage,
    )
    record_timbre_rule_version(
        conn, company_id=company_id, rule=rule, now_iso=now_iso, invoice_id=invoice_id
    )
    conn.execute(
        """
        UPDATE supplier_invoices
        SET timbre_rule_version = ?, timbre_rule_source = ?, timbre_rule_hash = ?,
            timbre_rule_valid_from = ?, timbre_rule_valid_to = ?, timbre_snapshot_json = ?
        WHERE id = ? AND company_id = ?
        """,
        (
            rule.version_code,
            rule.source,
            snapshot["rule"]["content_hash"],
            rule.valid_from,
            rule.valid_to,
            json.dumps(snapshot, sort_keys=True),
            invoice_id,
            company_id,
        ),
    )
    return snapshot


def inherit_timbre_provenance(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    source_invoice_id: int,
    target_invoice_id: int,
) -> bool:
    """Copy the original invoice's provenance onto a reversal.

    A credit note reverses a specific calculation, so it must cite the same rule
    identity rather than whatever is configured when the credit note happens to
    be raised. Returns ``False`` when the source invoice has no provenance (a
    legacy invoice) — in that case the credit note stays ``NULL`` too, because
    the honest statement is "not recorded", not today's rule.
    """
    source = conn.execute(
        """
        SELECT timbre_rule_version, timbre_rule_source, timbre_rule_hash,
               timbre_rule_valid_from, timbre_rule_valid_to, timbre_snapshot_json
        FROM supplier_invoices WHERE id = ? AND company_id = ?
        """,
        (source_invoice_id, company_id),
    ).fetchone()
    if source is None or source["timbre_rule_version"] is None:
        return False
    snapshot = json.loads(source["timbre_snapshot_json"]) if source["timbre_snapshot_json"] else {}
    snapshot["inherited_from_invoice_id"] = int(source_invoice_id)
    snapshot["stage"] = "reversal"
    conn.execute(
        """
        UPDATE supplier_invoices
        SET timbre_rule_version = ?, timbre_rule_source = ?, timbre_rule_hash = ?,
            timbre_rule_valid_from = ?, timbre_rule_valid_to = ?, timbre_snapshot_json = ?
        WHERE id = ? AND company_id = ?
        """,
        (
            source["timbre_rule_version"],
            source["timbre_rule_source"],
            source["timbre_rule_hash"],
            source["timbre_rule_valid_from"],
            source["timbre_rule_valid_to"],
            json.dumps(snapshot, sort_keys=True),
            target_invoice_id,
            company_id,
        ),
    )
    return True


def describe_invoice_timbre_provenance(invoice_row: Any) -> Dict[str, Any]:
    """What an invoice can prove about its own stamp duty.

    Returns ``recorded`` with the identity and the snapshot, or ``unavailable``
    with a reason. It never infers an identity from the current configuration.
    """
    version = invoice_row["timbre_rule_version"] if invoice_row is not None else None
    if not version:
        return {
            "provenance": PROVENANCE_UNAVAILABLE,
            "reason": "LEGACY_INVOICE_WITHOUT_RULE_IDENTITY",
            "amount_timbre_dzd": invoice_row["amount_timbre_dzd"] if invoice_row is not None else None,
            "note": (
                "This invoice was validated before rule provenance was recorded. Its "
                "amount is stored and unchanged, but which rule produced it is unknown "
                "and is deliberately not guessed."
            ),
        }
    snapshot: Dict[str, Any] = {}
    if invoice_row["timbre_snapshot_json"]:
        try:
            snapshot = json.loads(invoice_row["timbre_snapshot_json"])
        except (TypeError, ValueError):
            snapshot = {"error": "SNAPSHOT_UNPARSEABLE"}
    return {
        "provenance": PROVENANCE_RECORDED,
        "rule_version": version,
        "rule_source": invoice_row["timbre_rule_source"],
        "rule_content_hash": invoice_row["timbre_rule_hash"],
        "valid_from": invoice_row["timbre_rule_valid_from"],
        "valid_to": invoice_row["timbre_rule_valid_to"],
        "snapshot": snapshot,
    }


def assert_timbre_rule_unchanged(
    invoice_row: Any,
    current_rule: TimbreRule,
) -> None:
    """Refuse when the rule in force now differs from the one on the invoice.

    A draft validated under one version and posted after the operator installed a
    new one must not silently move money. Legacy invoices (no recorded identity)
    are exempt: they have nothing to compare against, and refusing them would
    block work that predates the control.
    """
    recorded = invoice_row["timbre_rule_hash"] if invoice_row is not None else None
    if not recorded:
        return
    current = timbre_rule_content_hash(current_rule)
    if recorded != current:
        raise TaxRuleConfigurationError(
            "TIMBRE_RULE_CHANGED_SINCE_VALIDATION:"
            f"recorded={invoice_row['timbre_rule_version']}@{recorded[:12]},"
            f"current={current_rule.version_code}@{current[:12]}; "
            "re-validate the invoice against the current rule before posting"
        )

# ---------------------------------------------------------------------------
# Phase 46 (finding R-05) — audited timbre-rule administration
# ---------------------------------------------------------------------------
# Before this, the only writer of ``timbre_rules`` was the chart-template seed.
# Once a version had priced a posted invoice the immutability triggers correctly
# froze it, so adopting a new Finance Law meant raw SQL: no actor, no reason, no
# audit event, no authorization, and no protection against two windows silently
# overlapping. The evidence side was strong; the door was a hole in the wall.
#
# The rules implemented here:
#   * a rule is created as a DRAFT and is invisible to the resolver;
#   * it becomes ACTIVE only through a second, *different* accountant's recorded
#     approval (segregation of duties), which is compared against the rules
#     already in force for overlap;
#   * every step appends an audit event carrying actor, timestamp, version,
#     effective window, values and reason;
#   * nothing is ever deleted, and a rule referenced by a posted invoice cannot
#     be updated (enforced by trigger) or superseded (refused here first, with a
#     readable reason);
#   * no rate is ever defaulted or invented: every numeric field must be
#     supplied. Whether those supplied values are the law is the expert-
#     comptable's decision, not this module's.

TIMBRE_ADMIN_ROLES = ("accountant", "admin")
_TIMBRE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_TIMBRE_REASON_LENGTH = 500
#: Explicit policies for a new ACTIVE window that overlaps one already in force.
#: ``reject`` is the default; ``supersede`` closes the older window the day
#: before the new one starts, and is refused if a posted invoice cites it.
TIMBRE_OVERLAP_POLICIES = ("reject", "supersede")


class TimbreRuleAdministrationError(ValueError):
    """A refused administration request, with a stable machine-readable prefix."""


def _timbre_admin_user(conn: Any, company_id: int, user_id: int, action: str) -> Any:
    """Resolve the actor inside the tenant; refuse an inactive or wrong-role user."""
    user = conn.execute(
        "SELECT * FROM users WHERE id = ? AND company_id = ?", (user_id, company_id)
    ).fetchone()
    if user is None or not user["is_active"]:
        raise TenantIsolationError(
            f"User {user_id} is not an active member of company {company_id}; {action} refused."
        )
    if user["role"] not in TIMBRE_ADMIN_ROLES:
        raise AuthorizationError(
            f"TIMBRE_RULE_ADMIN_ROLE_REQUIRED: role '{user['role']}' may not {action}; "
            f"allowed roles are {TIMBRE_ADMIN_ROLES}."
        )
    return user


def _brackets_from_input(brackets: Any, version_code: str) -> List[Dict[str, Optional[str]]]:
    """Parse caller-supplied brackets, refusing anything the resolver could not use."""
    if brackets is None:
        raise TimbreRuleAdministrationError("TIMBRE_RULE_BRACKETS_REQUIRED: brackets must be supplied.")
    raw = json.dumps(brackets) if isinstance(brackets, (list, dict)) else brackets
    try:
        parsed = _parse_brackets(raw, version_code=version_code)
    except TaxRuleConfigurationError as exc:
        raise TimbreRuleAdministrationError(f"TIMBRE_RULE_BRACKETS_INVALID: {exc}") from exc
    except (TypeError, ValueError) as exc:  # malformed JSON is a caller error, not a crash
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_BRACKETS_INVALID:{version_code}: {exc}"
        ) from exc
    if not parsed:
        raise TimbreRuleAdministrationError("TIMBRE_RULE_BRACKETS_INVALID: brackets list is empty.")
    return parsed


def _validate_window(valid_from: Any, valid_to: Any) -> Tuple[str, Optional[str]]:
    """Validate the effective window, reporting every failure as an admin error.

    ``_normalise_iso_date`` raises :class:`TaxRuleConfigurationError`, which is
    right for the resolver but wrong at the administration boundary: a caller
    sending ``2027-13-01`` deserves a 422 field error, not a configuration
    failure. The message and code are preserved, only the class changes.
    """
    try:
        start = _normalise_iso_date(valid_from, what="timbre rule valid_from")
        if valid_to in (None, ""):
            return start, None
        end = _normalise_iso_date(valid_to, what="timbre rule valid_to")
    except TaxRuleConfigurationError as exc:
        raise TimbreRuleAdministrationError(f"TIMBRE_RULE_WINDOW_INVALID: {exc}") from exc
    if end <= start:
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_WINDOW_INVALID: valid_to ({end}) must be after valid_from ({start})."
        )
    return start, end


def _validate_reason(reason: Any, action: str) -> str:
    text = ("" if reason is None else str(reason)).strip()
    if not text:
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_REASON_REQUIRED: {action} must record why it was done."
        )
    if len(text) > MAX_TIMBRE_REASON_LENGTH:
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_REASON_TOO_LONG: {len(text)} > {MAX_TIMBRE_REASON_LENGTH} characters."
        )
    return text


#: The only way to declare a stamp-duty rule *deliberately uncapped*.
#:
#: A missing ``max_amount_dzd`` used to mean "no ceiling" without anyone saying
#: so, which is how the specification came to advertise a 10,000.00 DZD ceiling
#: that no rule ever applied (finding F-R2-1). A rule must now state its cap
#: position explicitly: either a decimal ceiling, or this sentinel meaning
#: "reviewed and deliberately uncapped". ``None``/omitted is refused.
TIMBRE_CAP_DECLARED_UNCAPPED = "none"


def _cap_or_refuse(value: Any) -> Optional[str]:
    """Parse an explicit cap declaration; ``None`` stored means uncapped.

    Returns the formatted ceiling, or ``None`` for a deliberate
    :data:`TIMBRE_CAP_DECLARED_UNCAPPED` declaration. An omitted value is
    refused rather than silently interpreted as "uncapped".
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise TimbreRuleAdministrationError(
            "TIMBRE_RULE_CAP_NOT_DECLARED:max_amount_dzd must be declared explicitly — either a "
            "decimal ceiling, or 'none' to record that the rule is deliberately uncapped. "
            "Omitting it would silently choose a tax treatment nobody reviewed."
        )
    if isinstance(value, str) and value.strip().lower() == TIMBRE_CAP_DECLARED_UNCAPPED:
        return None
    return _amount_or_refuse(value, "max_amount_dzd", required=True)


def _amount_or_refuse(value: Any, field_name: str, *, required: bool = True) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise TimbreRuleAdministrationError(
                f"TIMBRE_RULE_VALUE_REQUIRED:{field_name} must be supplied; no default is invented."
            )
        return None
    try:
        dec = to_decimal(str(value))
    except (ValueError, TypeError) as exc:
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_VALUE_INVALID:{field_name}={value!r} is not a decimal amount."
        ) from exc
    if dec < Decimal("0.00"):
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_VALUE_NEGATIVE:{field_name}={value!r} must not be negative."
        )
    return format_dzd(dec)


def _overlapping_active_rules(
    conn: Any, company_id: int, start: str, end: Optional[str]
) -> List[Any]:
    """Active rules whose effective window touches ``[start, end]`` (end=None = open)."""
    rows = conn.execute(
        "SELECT * FROM timbre_rules WHERE company_id = ? AND review_state = 'ACTIVE'",
        (company_id,),
    ).fetchall()
    overlapping = []
    for row in rows:
        other_start = str(row["valid_from"])
        other_end = row["valid_to"]
        if end is not None and other_start > end:
            continue
        if other_end not in (None, "") and str(other_end) < start:
            continue
        overlapping.append(row)
    return overlapping


def _posted_invoices_citing(conn: Any, company_id: int, version_code: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM supplier_invoices
        WHERE company_id = ? AND timbre_rule_version = ? AND workflow_state = 'Posted'
        """,
        (company_id, version_code),
    ).fetchone()
    return int(row["n"])


def create_timbre_rule_version(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    *,
    version_code: str,
    valid_from: str,
    valid_to: Optional[str],
    min_amount_dzd: Any,
    threshold_dzd: Any,
    brackets: Any,
    reason: str,
    max_amount_dzd: Any = None,
) -> Dict[str, Any]:
    """Create a timbre rule version as a DRAFT. It governs nothing until approved.

    Refuses a duplicate version code (the row is unique per company), an invalid
    window or bracket set, and any attempt to omit a value — there is no default
    rate here on purpose: a stamp-duty figure must come from a reviewed
    configuration, never from a convenience constant.
    """
    code = (version_code or "").strip()
    if not _TIMBRE_VERSION_RE.match(code):
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_VERSION_INVALID:{version_code!r} must match {_TIMBRE_VERSION_RE.pattern}."
        )
    start, end = _validate_window(valid_from, valid_to)
    why = _validate_reason(reason, "creating a rule version")
    parsed_brackets = _brackets_from_input(brackets, code)
    minimum = _amount_or_refuse(min_amount_dzd, "min_amount_dzd")
    threshold = _amount_or_refuse(threshold_dzd, "threshold_dzd")
    maximum = _cap_or_refuse(max_amount_dzd)
    now_iso = datetime.now(timezone.utc).isoformat()

    with db.transaction() as conn:
        user = _timbre_admin_user(conn, company_id, user_id, "create a timbre rule version")
        existing = conn.execute(
            "SELECT * FROM timbre_rules WHERE company_id = ? AND version_code = ?",
            (company_id, code),
        ).fetchone()
        if existing is not None:
            raise TimbreRuleAdministrationError(
                f"TIMBRE_RULE_VERSION_EXISTS:{code} already exists in review_state "
                f"'{existing['review_state']}'. A rule version is never rewritten; create a new one."
            )
        conn.execute(
            """
            INSERT INTO timbre_rules (
                company_id, version_code, valid_from, valid_to, min_amount_dzd,
                max_amount_dzd, threshold_dzd, brackets_json, review_state,
                reason, created_by_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?)
            """,
            (
                company_id,
                code,
                start,
                end,
                minimum,
                maximum,
                threshold,
                json.dumps(parsed_brackets, sort_keys=True),
                why,
                user_id,
                now_iso,
            ),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="TIMBRE_RULE_VERSION_CREATED",
            target_model="timbre_rules",
            target_id=code,
            after_state={
                "version_code": code,
                "review_state": "DRAFT",
                "valid_from": start,
                "valid_to": end,
                "min_amount_dzd": minimum,
                "max_amount_dzd": maximum,
                "threshold_dzd": threshold,
                "brackets": parsed_brackets,
                "reason": why,
                "actor": user["username"],
                "created_at": now_iso,
                "note": "a draft governs nothing until a second accountant approves it",
            },
        )
    return {
        "version_code": code,
        "review_state": "DRAFT",
        "valid_from": start,
        "valid_to": end,
        "min_amount_dzd": minimum,
        "max_amount_dzd": maximum,
        "threshold_dzd": threshold,
        "brackets": parsed_brackets,
        "reason": why,
        "created_by_id": user_id,
        "created_at": now_iso,
        "in_force": False,
    }


def approve_timbre_rule_version(
    db: ERPDatabase,
    company_id: int,
    user_id: int,
    *,
    version_code: str,
    reason: str,
    overlap_policy: str = "reject",
) -> Dict[str, Any]:
    """Approve a DRAFT, making it ACTIVE for its effective window.

    Three independent guards, in this order:

    1. **Authorization and segregation of duties** — the approver must be an
       active accountant/admin of *this* company and must not be the person who
       created the version. One person cannot both propose and enact a tax rate.
    2. **Overlap** — the window is compared with every ACTIVE rule. The default
       policy refuses an overlap. ``supersede`` is available only with an
       explicit choice, a reason, and only when no posted invoice cites the rule
       being closed; it narrows that rule's ``valid_to`` to the day before the
       new window instead of deleting anything.
    3. **The database's own rules** — the immutability triggers still abort an
       update of a rule referenced by a posted invoice, so a race between this
       check and a concurrent posting cannot rewrite the explanation of an
       amount already in the ledger.
    """
    if overlap_policy not in TIMBRE_OVERLAP_POLICIES:
        raise TimbreRuleAdministrationError(
            f"TIMBRE_RULE_OVERLAP_POLICY_INVALID:{overlap_policy!r} is not one of {TIMBRE_OVERLAP_POLICIES}."
        )
    code = (version_code or "").strip()
    why = _validate_reason(reason, "approving a rule version")
    now_iso = datetime.now(timezone.utc).isoformat()
    superseded: List[Dict[str, Any]] = []

    with db.transaction() as conn:
        user = _timbre_admin_user(conn, company_id, user_id, "approve a timbre rule version")
        row = conn.execute(
            "SELECT * FROM timbre_rules WHERE company_id = ? AND version_code = ?",
            (company_id, code),
        ).fetchone()
        if row is None:
            raise TenantIsolationError(
                f"TIMBRE_RULE_NOT_FOUND:{code} does not exist in company {company_id}."
            )
        if row["review_state"] != "DRAFT":
            raise TimbreRuleAdministrationError(
                f"TIMBRE_RULE_NOT_A_DRAFT:{code} is already '{row['review_state']}'; "
                "approval is a one-way transition from DRAFT."
            )
        if int(row["created_by_id"] or 0) == int(user_id):
            raise AuthorizationError(
                "TIMBRE_RULE_SOD_VIOLATION: the accountant who created a rule version cannot "
                "also approve it. A second accountant or an administrator must review it."
            )

        start = str(row["valid_from"])
        end = None if row["valid_to"] in (None, "") else str(row["valid_to"])
        overlapping = _overlapping_active_rules(conn, company_id, start, end)
        if overlapping:
            names = [str(r["version_code"]) for r in overlapping]
            if overlap_policy == "reject":
                raise TimbreRuleAdministrationError(
                    f"TIMBRE_RULE_OVERLAP:{code} ({start}..{end or 'open'}) overlaps active "
                    f"rule(s) {names}. Refusing to let two rules claim the same day; choose "
                    "an explicit overlap_policy='supersede' if the older window must be closed."
                )
            from datetime import date as _date, timedelta as _timedelta

            day_before = (_date.fromisoformat(start) - _timedelta(days=1)).isoformat()
            for other in overlapping:
                posted = _posted_invoices_citing(conn, company_id, str(other["version_code"]))
                if posted:
                    raise TimbreRuleAdministrationError(
                        f"TIMBRE_RULE_REFERENCED_BY_POSTED_INVOICE:{other['version_code']} has "
                        f"{posted} posted invoice(s); its window cannot be closed. Post a new "
                        "version starting after its own valid_to instead."
                    )
                if str(other["valid_from"]) > day_before:
                    raise TimbreRuleAdministrationError(
                        f"TIMBRE_RULE_SUPERSEDE_IMPOSSIBLE:{other['version_code']} starts "
                        f"{other['valid_from']}, after the new window's first day; "
                        "close its window manually is not supported — choose a later start date."
                    )
                conn.execute(
                    "UPDATE timbre_rules SET valid_to = ? WHERE id = ? AND company_id = ?",
                    (day_before, other["id"], company_id),
                )
                superseded.append(
                    {"version_code": str(other["version_code"]), "valid_to": day_before}
                )

        conn.execute(
            """
            UPDATE timbre_rules
            SET review_state = 'ACTIVE', reviewed_by_id = ?, reviewed_at = ?, reason = COALESCE(reason, ?)
            WHERE id = ? AND company_id = ?
            """,
            (user_id, now_iso, why, row["id"], company_id),
        )
        db.append_audit_event(
            conn=conn,
            company_id=company_id,
            actor_user_id=user_id,
            action_type="TIMBRE_RULE_VERSION_APPROVED",
            target_model="timbre_rules",
            target_id=code,
            before_state={"review_state": "DRAFT", "valid_to": row["valid_to"]},
            after_state={
                "version_code": code,
                "review_state": "ACTIVE",
                "valid_from": start,
                "valid_to": end,
                "min_amount_dzd": format_dzd(row["min_amount_dzd"]),
                "max_amount_dzd": row["max_amount_dzd"],
                "threshold_dzd": format_dzd(row["threshold_dzd"]),
                "brackets": json.loads(row["brackets_json"]),
                "reason": why,
                "reviewed_by": user["username"],
                "reviewed_at": now_iso,
                "created_by_id": row["created_by_id"],
                "superseded": superseded,
                "overlap_policy": overlap_policy,
            },
        )
    return {
        "version_code": code,
        "review_state": "ACTIVE",
        "valid_from": start,
        "valid_to": end,
        "reviewed_by_id": user_id,
        "reviewed_at": now_iso,
        "reason": why,
        "superseded": superseded,
        "in_force": True,
    }


def list_timbre_rule_versions(
    db: ERPDatabase, company_id: int, user_id: int
) -> List[Dict[str, Any]]:
    """Every rule version of this tenant, drafts included, with what it can prove."""
    with db.transaction() as conn:
        _timbre_admin_user(conn, company_id, user_id, "read the timbre rule configuration")
        rows = conn.execute(
            """
            SELECT * FROM timbre_rules WHERE company_id = ? ORDER BY valid_from DESC, id DESC
            """,
            (company_id,),
        ).fetchall()
        ledger = {
            str(r["version_code"])
            for r in conn.execute(
                "SELECT DISTINCT version_code FROM timbre_rule_versions WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        }
    described = []
    for row in rows:
        described.append(
            {
                "version_code": str(row["version_code"]),
                "review_state": str(row["review_state"]),
                "valid_from": str(row["valid_from"]),
                "valid_to": row["valid_to"],
                "min_amount_dzd": format_dzd(row["min_amount_dzd"]),
                "max_amount_dzd": row["max_amount_dzd"],
                "threshold_dzd": format_dzd(row["threshold_dzd"]),
                "brackets": json.loads(row["brackets_json"]),
                "created_by_id": row["created_by_id"],
                "created_at": row["created_at"],
                "reviewed_by_id": row["reviewed_by_id"],
                "reviewed_at": row["reviewed_at"],
                "in_force": str(row["review_state"]) == "ACTIVE",
                "used_by_posted_invoice_ledger": str(row["version_code"]) in ledger,
                "reason": row["reason"],
            }
        )
    return described


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
