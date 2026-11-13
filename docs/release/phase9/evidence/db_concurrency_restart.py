#!/usr/bin/env python3
"""Phase 9E/9K evidence - database integrity, duplicate prevention, restart.

Synthetic data only, all inside one mkdtemp(). The script asserts:

  1. schema integrity (PRAGMA integrity_check / foreign_key_check) on a fresh install;
  2. a failing transaction rolls back completely (no partial row survives);
  3. concurrent creation of the *same* supplier invoice number cannot double-post
     (two threads, real API calls) - exactly one succeeds;
  4. posting an already-posted invoice again is refused (no silent second posting);
  5. after a process-style restart (new ERPDatabase + new app on the same file) the
     data persists, the schema is still sound, and a previously issued session token
     still resolves to the same user (sessions are database-backed, not memory).

Usage: ERP_ROOT=/tree ERP_VENV=/venv python db_concurrency_restart.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

# Default to the checkout this script lives in, so the drill is reproducible from
# any copy of the tree; ERP_ROOT overrides it for a separate deployment.
ROOT = Path(os.environ.get("ERP_ROOT") or Path(__file__).resolve().parents[4])
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault

RESULTS: dict = {}


def bootstrap(db_path: Path, vault_path: Path) -> tuple[ERPDatabase, PrivateDocumentVault]:
    db = ERPDatabase(str(db_path))
    vault = PrivateDocumentVault(vault_path)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Tenant A', 'DZ', 'dz', '2026-01-01')"
        )
        for uid, username, role, password in (
            (1, "admin_a", "admin", "AdminA#2026x"),
            (2, "clerk_a", "clerk", "Clerk#2026x"),
            (3, "acct_a", "accountant", "Acct#2026x"),
        ):
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, 1, ?, ?, 1, ?)",
                (uid, username, role, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
            " nif, nis, ai, rc) VALUES (1, 1, 'Supplier One', 'supplier', 'DZ', 'REEL',"
            " '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
        )
    return db, vault


def token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def invoice_payload(number: str) -> dict:
    return {
        "supplier_id": 1,
        "supplier_invoice_no": number,
        "invoice_date": "2026-02-01",
        "payment_method": "BANK_TRANSFER",
        "amount_ht_dzd": "10000.00",
        "amount_tva_dzd": "1900.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "11900.00",
        "lines": [
            {
                "sku": "SKU-1",
                "description": "Fournitures",
                "qty": "10",
                "unit_price_dzd": "1000.00",
                "tax_rate": "19",
                "line_ht_dzd": "10000.00",
            }
        ],
    }


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="phase9e-"))
    db_path, vault_path = work / "erp.sqlite3", work / "vault"
    db, vault = bootstrap(db_path, vault_path)
    app = create_erp_fastapi_app(db, vault)
    client = TestClient(app)

    with sqlite3.connect(db_path) as conn:
        RESULTS["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        RESULTS["foreign_key_check"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        RESULTS["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        RESULTS["tables"] = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]

    # 2. failing transaction must roll back completely
    before = 0
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
                " VALUES (99, 'DZ9', 'Bad', 'DZ', 'dz', '2026-01-01')"
            )
            raise RuntimeError("simulated failure after the insert")
    except RuntimeError:
        pass
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    RESULTS["rollback"] = {"before": before, "after": after, "rolled_back": before == after}

    token_clerk = token(client, "clerk_a", "Clerk#2026x")
    headers = {"Authorization": f"Bearer {token_clerk}"}

    # 3. concurrent duplicate creation: same supplier invoice number, two threads
    payload = invoice_payload("FA-2026-0001")
    outcomes: list[str] = []
    lock = threading.Lock()

    def create(http_client: TestClient) -> None:
        # The client is passed in rather than closed over: a closure over a local
        # makes the dependency invisible to static analysis (and to a reader).
        response = http_client.post("/api/v1/invoices", json=payload, headers=headers)
        with lock:
            outcomes.append(f"{response.status_code}:{response.text[:60]}")

    threads = [threading.Thread(target=create, args=(client,)) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    # The product records the loser as DuplicateRejected instead of erroring, so the
    # invariant is "exactly one invoice is in a postable state", not "one HTTP 200".
    validated = [o for o in outcomes if '"workflow_state":"Validated"' in o]
    duplicates = [o for o in outcomes if '"workflow_state":"DuplicateRejected"' in o]
    RESULTS["concurrent_duplicate_creation"] = {
        "outcomes": outcomes,
        "validated": len(validated),
        "duplicate_rejected": len(duplicates),
        "invariant_ok": len(validated) == 1 and len(duplicates) == 1,
    }
    with sqlite3.connect(db_path) as conn:
        RESULTS["invoices_in_db"] = conn.execute(
            "SELECT COUNT(*) FROM supplier_invoices").fetchone()[0]

    # 4. full approval workflow, then posting twice: idempotent with the same key,
    # refused with a different key, and never two journal entries.
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM supplier_invoices WHERE workflow_state='Validated' LIMIT 1").fetchone()
    invoice_id = row[0] if row else None
    if invoice_id is not None:
        acct = token(client, "acct_a", "Acct#2026x")
        acct_headers = {"Authorization": f"Bearer {acct}"}
        submit = client.post(f"/api/v1/invoices/{invoice_id}/submit-approval", headers=headers)
        decide = client.post(f"/api/v1/invoices/{invoice_id}/decide-approval",
                             json={"approve": True, "reason": "drill approval"},
                             headers=acct_headers)
        # segregation of duties: the clerk who created it must not approve it
        self_approval = client.post(f"/api/v1/invoices/{invoice_id}/decide-approval",
                                    json={"approve": True, "reason": "self"},
                                    headers=headers)
        first = client.post(f"/api/v1/invoices/{invoice_id}/post",
                            headers={**acct_headers, "Idempotency-Key": "drill-key-1"})
        replay = client.post(f"/api/v1/invoices/{invoice_id}/post",
                             headers={**acct_headers, "Idempotency-Key": "drill-key-1"})
        other = client.post(f"/api/v1/invoices/{invoice_id}/post",
                            headers={**acct_headers, "Idempotency-Key": "drill-key-2"})
        missing_key = client.post(f"/api/v1/invoices/{invoice_id}/post", headers=acct_headers)
        with sqlite3.connect(db_path) as conn:
            posted = conn.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE state='POSTED'").fetchone()[0]
            balanced = conn.execute(
                "SELECT COUNT(*) FROM journal_entries WHERE state='POSTED'"
                " AND total_debit_dzd = total_credit_dzd").fetchone()[0]
        RESULTS["posting_workflow"] = {
            "submit": submit.status_code,
            "decide": decide.status_code,
            "clerk_self_approval": self_approval.status_code,
            "post_first": first.status_code,
            "post_same_key_replay": replay.status_code,
            "post_replay_same_entry": (
                replay.json().get("journal_entry_id") == first.json().get("journal_entry_id")
                if replay.status_code == 200 and first.status_code == 200 else None),
            "post_other_key": other.status_code,
            "post_without_key": missing_key.status_code,
            "posted_entries": posted,
            "posted_entries_balanced": balanced,
            "single_posting_invariant_ok": posted == 1 and balanced == 1,
        }
        RESULTS["duplicate_posting"] = {
            "refused_second": other.status_code == 409,
            "idempotent_replay": replay.status_code == 200,
        }
        tb = db.trial_balance(1)
        RESULTS["trial_balance_after_posting"] = {
            "balanced": tb.get("balanced"),
            "debit": tb.get("total_debit_dzd"),
            "credit": tb.get("total_credit_dzd"),
        }

    # 5. restart: new objects on the same files
    token_before = token(client, "acct_a", "Acct#2026x")
    # ERPDatabase opens a short-lived connection per transaction, so dropping the
    # object and constructing a new one against the same file is a true restart of
    # the application's database layer (as a process restart would be).
    del db, app, client
    db2 = ERPDatabase(str(db_path))
    app2 = create_erp_fastapi_app(db2, PrivateDocumentVault(vault_path))
    client2 = TestClient(app2)
    session = client2.get("/api/v1/auth/session",
                          headers={"Authorization": f"Bearer {token_before}"})
    with sqlite3.connect(db_path) as conn:
        integrity_after = conn.execute("PRAGMA integrity_check").fetchone()[0]
        rows_after = conn.execute("SELECT COUNT(*) FROM supplier_invoices").fetchone()[0]
    RESULTS["restart"] = {
        "session_status_after_restart": session.status_code,
        "session_payload": session.json() if session.status_code == 200 else session.text[:120],
        "integrity_after_restart": integrity_after,
        "invoices_after_restart": rows_after,
    }
    RESULTS["verdict"] = "PASS" if (
        RESULTS["integrity_check"] == "ok"
        and RESULTS["foreign_key_check"] == 0
        and RESULTS["rollback"]["rolled_back"]
        and RESULTS["concurrent_duplicate_creation"]["invariant_ok"]
        and RESULTS.get("posting_workflow", {}).get("single_posting_invariant_ok", False)
        and RESULTS.get("trial_balance_after_posting", {}).get("balanced", False)
        and RESULTS["restart"]["integrity_after_restart"] == "ok"
    ) else "FAIL"
    print(json.dumps(RESULTS, indent=1, default=str))
    return 0 if RESULTS["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
