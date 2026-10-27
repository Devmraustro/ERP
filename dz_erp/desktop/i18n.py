# SPDX-License-Identifier: LGPL-3.0-only
"""French and Arabic user-facing strings for the desktop client.

Algerian businesses work in French and Arabic (often both in the same
office), so the interface ships with both from day one and the layout
direction flips for Arabic. This module is Qt-free: it is a plain string
table plus an RTL predicate, testable on headless CI.
"""

from __future__ import annotations

from typing import Dict

LANGUAGES = ("fr", "ar")
_RTL_LANGUAGES = ("ar",)

_STRINGS: Dict[str, Dict[str, str]] = {
    "fr": {
        "app_title": "ERP Algérien — Gestion commerciale et comptable",
        "menu_file": "&Fichier",
        "menu_language": "&Langue",
        "menu_help": "&Aide",
        "action_quit": "&Quitter",
        "action_about": "À &propos",
        "login_title": "Connexion",
        "login_username": "Nom d'utilisateur",
        "login_password": "Mot de passe",
        "login_button": "Se connecter",
        "login_failed": "Connexion refusée : {detail}",
        "first_run_title": "Première installation",
        "first_run_intro": (
            "Bienvenue. Créez votre société et le compte administrateur local. "
            "Les données restent sur ce poste (Mode poste unique)."
        ),
        "first_run_company": "Nom de la société",
        "first_run_admin": "Identifiant administrateur",
        "first_run_password": "Mot de passe administrateur",
        "first_run_confirm": "Confirmer le mot de passe",
        "first_run_create": "Créer la société",
        "first_run_mismatch": "Les mots de passe ne correspondent pas.",
        "first_run_failed": "Initialisation impossible : {detail}",
        "mode_local": "Mode poste unique — service local (127.0.0.1)",
        "mode_server": "Mode réseau — serveur {url}",
        "status_user": "Utilisateur : {username} ({role})",
        "nav_dashboard": "Tableau de bord",
        "nav_accounts": "Plan comptable",
        "nav_products": "Articles",
        "nav_documents": "Documents",
        "nav_invoices": "Factures fournisseurs",
        "nav_trial_balance": "Balance générale",
        "col_code": "Code",
        "col_name": "Intitulé",
        "col_type": "Type",
        "col_sku": "Référence",
        "col_uom": "Unité",
        "col_cost": "Coût standard (DZD)",
        "col_filename": "Fichier",
        "col_state": "État",
        "col_uploaded": "Téléversé le",
        "col_size": "Taille (o)",
        "col_invoice_no": "N° facture",
        "col_supplier": "Fournisseur",
        "col_date": "Date",
        "col_amount_ht": "Montant HT (DZD)",
        "col_amount_tva": "TVA (DZD)",
        "col_amount_ttc": "Montant TTC (DZD)",
        "col_debit": "Débit (DZD)",
        "col_credit": "Crédit (DZD)",
        "tb_total_debit": "Total débit",
        "tb_total_credit": "Total crédit",
        "tb_balanced": "La balance est équilibrée.",
        "tb_empty": "Aucune écriture validée (POSTED) pour le moment.",
        "button_refresh": "Actualiser",
        "button_logout": "Déconnexion",
        "button_upload": "Téléverser un document…",
        "error_title": "Erreur",
        "error_load": "Chargement impossible : {detail}",
        "connecting": "Connexion au service ERP…",
        "about_text": (
            "ERP Algérien — logiciel de gestion local.\n"
            "Version {version}\n\n"
            "Les données comptables sont stockées localement et chiffrées. "
            "La balance et les écritures restent soumises à la validation d'un "
            "comptable agréé."
        ),
    },
    "ar": {
        "app_title": "نظام تخطيط موارد المؤسسات الجزائري — التسيير التجاري والمحاسبي",
        "menu_file": "&ملف",
        "menu_language": "&اللغة",
        "menu_help": "&مساعدة",
        "action_quit": "&خروج",
        "action_about": "ح&ول",
        "login_title": "تسجيل الدخول",
        "login_username": "اسم المستخدم",
        "login_password": "كلمة المرور",
        "login_button": "دخول",
        "login_failed": "تم رفض الدخول: {detail}",
        "first_run_title": "التثبيت الأول",
        "first_run_intro": (
            "مرحبًا. أنشئ شركتك وحساب المدير المحلي. "
            "تبقى البيانات على هذا الجهاز (وضع المحطة الواحدة)."
        ),
        "first_run_company": "اسم الشركة",
        "first_run_admin": "معرّف المدير",
        "first_run_password": "كلمة مرور المدير",
        "first_run_confirm": "تأكيد كلمة المرور",
        "first_run_create": "إنشاء الشركة",
        "first_run_mismatch": "كلمتا المرور غير متطابقتين.",
        "first_run_failed": "فشل التهيئة: {detail}",
        "mode_local": "وضع المحطة الواحدة — خدمة محلية (127.0.0.1)",
        "mode_server": "وضع الشبكة — خادم {url}",
        "status_user": "المستخدم: {username} ({role})",
        "nav_dashboard": "لوحة القيادة",
        "nav_accounts": "دليل الحسابات",
        "nav_products": "المقالات",
        "nav_documents": "المستندات",
        "nav_invoices": "فواتير الموردين",
        "nav_trial_balance": "ميزان المراجعة",
        "col_code": "الرمز",
        "col_name": "التسمية",
        "col_type": "النوع",
        "col_sku": "المرجع",
        "col_uom": "الوحدة",
        "col_cost": "التكلفة المعيارية (دج)",
        "col_filename": "الملف",
        "col_state": "الحالة",
        "col_uploaded": "تاريخ الإيداع",
        "col_size": "الحجم (بايت)",
        "col_invoice_no": "رقم الفاتورة",
        "col_supplier": "المورد",
        "col_date": "التاريخ",
        "col_amount_ht": "المبلغ دون ضريبة (دج)",
        "col_amount_tva": "الضريبة (دج)",
        "col_amount_ttc": "المبلغ الإجمالي (دج)",
        "col_debit": "مدين (دج)",
        "col_credit": "دائن (دج)",
        "tb_total_debit": "مجموع المدين",
        "tb_total_credit": "مجموع الدائن",
        "tb_balanced": "الميزان متوازن.",
        "tb_empty": "لا توجد قيود معتمدة (POSTED) حاليًا.",
        "button_refresh": "تحديث",
        "button_logout": "تسجيل الخروج",
        "button_upload": "إيداع مستند…",
        "error_title": "خطأ",
        "error_load": "تعذّر التحميل: {detail}",
        "connecting": "جارٍ الاتصال بخدمة النظام…",
        "about_text": (
            "نظام ERP جزائري — برنامج تسيير محلي.\n"
            "الإصدار {version}\n\n"
            "تُخزَّن البيانات المحاسبية محليًا ومشفَّرة. وتبقى الميزان والقيود "
            "خاضعة لمصادقة محاسب معتمد."
        ),
    },
}


def available_languages() -> tuple:
    return LANGUAGES


def is_rtl(language: str) -> bool:
    """True when the language needs a right-to-left layout."""
    return language in _RTL_LANGUAGES


def tr(language: str, key: str, **kwargs) -> str:
    """Look up a string; fall back to French, then to the key itself."""
    text = _STRINGS.get(language, {}).get(key) or _STRINGS["fr"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
