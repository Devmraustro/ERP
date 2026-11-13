#!/usr/bin/env python3
"""Phase 10 — headless desktop-client acceptance (everything that does not need a screen).

The GUI itself cannot run in this environment (no ``libGL.so.1``, no Windows), but a
large part of the desktop client is *not* the GUI: the embedded loopback service, the
API client, the first-run bootstrap, the session lifecycle and the translation
catalogue. This script exercises that part for real and reports, for each acceptance
row, whether it was EXECUTED or NOT EXECUTED — so the report can be precise instead of
saying "desktop: untested".

Usage: python docs/release/phase10/evidence/desktop_headless_acceptance.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from dz_erp.desktop import i18n
from dz_erp.desktop.api_client import ErpApiError, ErpClient
from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run

RESULTS: list[dict] = []


def record(row: str, status: str, detail: str = "", extra: dict | None = None) -> None:
    entry = {"row": row, "status": status, "detail": detail}
    if extra:
        entry.update(extra)
    RESULTS.append(entry)
    print(f"[{status:<13}] {row}: {detail}")


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="dz-desktop-acc-"))
    db_path = data / "erp.sqlite3"
    vault_path = data / "vault"
    security_log = data / "security.jsonl"
    os.environ["ERP_SECURITY_LOG_PATH"] = str(security_log)

    # --- startup: first-run bootstrap of an embedded service (Mode A)
    try:
        bootstrap_first_run(db_path, company_name="Desktop Acceptance Co",
                            admin_username="admin", admin_password="Desktop#2026x")
        service = EmbeddedErpService(db_path, vault_path, security_log_path=security_log)
        base_url = service.start()
        record("startup (embedded service, Mode A)", "EXECUTED", f"bound {base_url}")
    except Exception as exc:
        record("startup (embedded service, Mode A)", "FAILED", f"{type(exc).__name__}: {exc}")
        print(json.dumps({"results": RESULTS}, indent=2))
        return 1

    client = ErpClient(base_url)
    try:
        # --- login
        try:
            login_response = client.login("admin", "Desktop#2026x")
            token = login_response.get("access_token", "")
            record("login", "EXECUTED" if token else "FAILED",
                   f"token_prefix={token[:12]}… keys={sorted(login_response)}")
        except ErpApiError as exc:
            record("login", "FAILED", f"{exc.status_code}: {exc.detail}")
            raise

        # --- API connectivity + authenticated reads
        accounts = client.accounts()
        balance = client.trial_balance()
        record("API connectivity (authenticated reads)", "EXECUTED",
               f"accounts={len(accounts.get('items', accounts.get('accounts', [])))} "
               f"trial_balance_debit={balance.get('total_debit_dzd')}")

        # --- session identity is server-derived
        current = client.session()
        record("session identity", "EXECUTED",
               f"user_id={current.get('user_id')} company_id={current.get('company_id')} "
               f"role={current.get('role')}")

        # --- logout invalidates the token server-side (proved with the stale token)
        stale_token = client._token
        client.logout()
        probe = ErpClient(base_url)
        probe._token = stale_token  # replay the token the server should now refuse
        try:
            probe.session()
            record("logout invalidates the session", "FAILED",
                   "the revoked token is still accepted by the server")
        except ErpApiError as exc:
            record("logout invalidates the session", "EXECUTED",
                   f"revoked token replayed -> HTTP {exc.status_code} {exc.detail[:40]}")
        record("client drops the token on logout", "EXECUTED" if not client.is_logged_in else "FAILED",
               f"is_logged_in={client.is_logged_in}")

        # --- bad credentials are typed errors, not crashes
        fresh = ErpClient(base_url)
        try:
            fresh.login("admin", "wrong-password")
            record("invalid credentials", "FAILED", "bad password was accepted")
        except ErpApiError as exc:
            record("invalid credentials", "EXECUTED", f"typed error {exc.status_code}")

        # --- translations: every language carries the same keys, and Arabic is RTL
        languages = i18n.available_languages()
        catalogue = getattr(i18n, "_STRINGS", {})
        keys_by_language = {lang: set(catalogue.get(lang, {})) for lang in languages}
        union = set().union(*keys_by_language.values()) if keys_by_language else set()
        missing = {lang: sorted(union - keys) for lang, keys in keys_by_language.items() if union - keys}
        record(
            "translation catalogue completeness",
            "EXECUTED" if union and not missing else "GAP",
            f"languages={list(languages)} keys={len(union)} "
            f"missing_per_language={ {k: len(v) for k, v in missing.items()} or 'none'}",
            {"missing_keys": missing},
        )

        rtl = {lang: i18n.is_rtl(lang) for lang in languages}
        record("RTL metadata (Arabic)", "EXECUTED" if rtl.get("ar") is True else "FAILED",
               f"is_rtl per language: {rtl}")

        # --- the brief asks for French/English switching; English does not exist here
        record(
            "English language support",
            "GAP" if "en" not in languages else "EXECUTED",
            f"available_languages()={list(languages)} - English is not implemented, "
            f"so 'FR/EN switching' cannot be accepted",
        )

        # --- rows that genuinely need a screen
        for row in (
            "application window construction",
            "Arabic RTL rendering (shaped glyphs, alignment)",
            "French/English switching in the running UI",
            "session expiry prompt in the UI",
            "role-based navigation visibility",
            "invoice intake through the UI",
            "OCR correction in the review screen",
            "invoice posting from the UI",
            "error surfacing in the UI (no traceback)",
        ):
            record(row, "NOT EXECUTED", "requires a Qt display (libGL.so.1 absent) or Windows")
    finally:
        service.stop()

    performed = [r for r in RESULTS if r["status"] == "EXECUTED"]
    summary = {
        "rows": len(RESULTS),
        "executed": len(performed),
        "not_executed": len([r for r in RESULTS if r["status"] == "NOT EXECUTED"]),
        "failed": len([r for r in RESULTS if r["status"] == "FAILED"]),
        "results": RESULTS,
    }
    print("\n== summary ==")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    out = Path(os.environ.get("DRILL_OUTPUT", data / "desktop_headless_acceptance.json"))
    out.write_text(json.dumps(summary, indent=2))
    print(f"written: {out}")
    return 0 if summary["failed"] == 0 else 1


def _has_key(language: str, key: str) -> bool:
    try:
        return i18n.tr(language, key) != key
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
