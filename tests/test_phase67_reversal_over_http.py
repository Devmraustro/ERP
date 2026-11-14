# SPDX-License-Identifier: LGPL-3.0-only
"""Release-closure phase 67 — a credit note can complete its life over HTTP.

Two defects were found by walking the reversal workflow against a running
server, not by reading the code:

1. **A credit note could never be approved or posted through the API.** The
   reversal handlers were registered on a second set of routes with the same
   path templates as the ordinary invoice routes. The router dispatches to the
   first registration, so ``submit-approval`` / ``decide-approval`` / ``post``
   on a credit note reached the *invoice* functions, which refused it
   (``must be 'Validated'``). The reversal journal entry stayed ``DRAFT``
   forever and the supplier ledger was never corrected - while the "reverse"
   call itself answered 200.

2. **A second reversal request for the same invoice answered HTTP 500.** The
   only thing stopping it was the UNIQUE constraint on the journal entry number,
   surfacing as an unhandled ``sqlite3.IntegrityError``.

Everything here goes through the real FastAPI application and real
server-issued tokens. No accounting rule is changed: the reversal state machine
and its entries are exactly what the library already implemented.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import AuthManager, hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.localization import load_official_odoo_chart

FAST_ITERATIONS = 1000

# (user_id, company_id, username, role, password)
USERS = [
    (1, 1, "clerk_a", "clerk", "Clerk#2026x"),
    (3, 1, "approver_a", "approver", "Appr#2026x"),
    (4, 1, "acct_a", "accountant", "Acct#2026x"),
    (6, 1, "admin_a", "admin", "Adm#2026x"),
    (20, 2, "acct_b", "accountant", "AcctB#2026x"),
    (21, 2, "approver_b", "approver", "ApprB#2026x"),
]

INVOICE = {
    "supplier_id": 10,
    "supplier_invoice_no": "FAC-REV-001",
    "invoice_date": "2026-10-15",
    "payment_method": "BANK_TRANSFER",
    "amount_ht_dzd": "10000.00",
    "amount_tva_dzd": "1900.00",
    "amount_timbre_dzd": "0.00",
    "amount_ttc_dzd": "11900.00",
    "lines": [
        {
            "description": "Service",
            "qty": "1",
            "unit_price_dzd": "10000.00",
            "tax_rate": "19.0",
            "line_ht_dzd": "10000.00",
        }
    ],
}


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    db = ERPDatabase(str(tmp_path / "reversal.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        for cid, code, name in ((1, "DZ1", "Tenant Alpha"), (2, "DZ2", "Tenant Beta")):
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
                " VALUES (?, ?, ?, 'DZ', 'dz', '2026-01-01')",
                (cid, code, name),
            )
        for uid, cid, username, role, password in USERS:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (uid, cid, username, role, hash_password(password, iterations=FAST_ITERATIONS)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc)"
            " VALUES (10, 1, 'Supplier Alpha', 'supplier', '001216099123456',"
            " '099116010123456', '16019912345', '16/00-0991234B12')"
        )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)
    auth = AuthManager(db, pbkdf2_iterations=FAST_ITERATIONS)
    client = TestClient(create_erp_fastapi_app(db, vault, auth_manager=auth))
    return SimpleNamespace(db=db, client=client, db_path=str(tmp_path / "reversal.sqlite3"))


def _bearer(client: TestClient, username: str, password: str) -> Dict[str, str]:
    resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _rows(env: SimpleNamespace, sql: str, *args):
    conn = sqlite3.connect(env.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _posted_invoice(env: SimpleNamespace) -> SimpleNamespace:
    """Create, approve and post one supplier invoice entirely over HTTP."""
    c = env.client
    clerk = _bearer(c, "clerk_a", "Clerk#2026x")
    approver = _bearer(c, "approver_a", "Appr#2026x")
    acct = _bearer(c, "acct_a", "Acct#2026x")
    created = c.post("/api/v1/invoices", json=INVOICE, headers=clerk)
    assert created.status_code == 200, created.text
    assert created.json()["workflow_state"] == "Validated", created.text
    inv_id = created.json()["invoice_id"]
    assert c.post(f"/api/v1/invoices/{inv_id}/submit-approval", headers=clerk).status_code == 200
    assert (
        c.post(f"/api/v1/invoices/{inv_id}/decide-approval", json={"approve": True}, headers=approver).status_code
        == 200
    )
    posted = c.post(
        f"/api/v1/invoices/{inv_id}/post", headers={**acct, "Idempotency-Key": "post-original"}
    )
    assert posted.status_code == 200, posted.text
    return SimpleNamespace(invoice_id=inv_id, clerk=clerk, approver=approver, acct=acct)


def _request_reversal(env: SimpleNamespace, ctx: SimpleNamespace, key: str = "reverse-1") -> int:
    resp = env.client.post(
        f"/api/v1/invoices/{ctx.invoice_id}/reverse",
        json={"reason": "Erreur de saisie"},
        headers={**ctx.acct, "Idempotency-Key": key},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workflow_state"] == "NeedsReview"
    return resp.json()["credit_note_invoice_id"]


def test_a_credit_note_is_submitted_approved_and_posted_over_http(env: SimpleNamespace) -> None:
    c = env.client
    ctx = _posted_invoice(env)
    original_before = _rows(env, "SELECT * FROM supplier_invoices WHERE id = ?", ctx.invoice_id)[0]
    original_lines_before = _rows(
        env, "SELECT * FROM journal_entry_lines WHERE journal_entry_id = ? ORDER BY id", original_before["journal_entry_id"]
    )
    cn_id = _request_reversal(env, ctx)

    # While the credit note is unapproved its entry is DRAFT and the books are unchanged.
    tb = c.get("/api/v1/accounting/trial-balance", headers=ctx.acct).json()
    assert tb["total_debit_dzd"] == "11900.00" and tb["balanced"] is True

    submitted = c.post(f"/api/v1/invoices/{cn_id}/submit-approval", headers=ctx.acct)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["workflow_state"] == "PendingApproval"

    # Segregation of duties: the requester may not approve their own reversal.
    own = c.post(f"/api/v1/invoices/{cn_id}/decide-approval", json={"approve": True}, headers=ctx.acct)
    assert own.status_code == 403, own.text
    assert "SEGREGATION_OF_DUTIES_VIOLATION" in own.text

    approved = c.post(f"/api/v1/invoices/{cn_id}/decide-approval", json={"approve": True}, headers=ctx.approver)
    assert approved.status_code == 200, approved.text
    assert approved.json()["workflow_state"] == "Approved"

    # Posting keeps its guards: role, then the mandatory idempotency key.
    assert (
        c.post(f"/api/v1/invoices/{cn_id}/post", headers={**ctx.approver, "Idempotency-Key": "x"}).status_code == 403
    )
    assert c.post(f"/api/v1/invoices/{cn_id}/post", headers=ctx.acct).status_code == 400

    key = {**ctx.acct, "Idempotency-Key": "post-reversal"}
    posted = c.post(f"/api/v1/invoices/{cn_id}/post", headers=key)
    assert posted.status_code == 200, posted.text
    assert posted.json()["workflow_state"] == "Posted"
    assert posted.json()["idempotent_replay"] is False

    entries_after_post = _rows(env, "SELECT COUNT(*) AS n FROM journal_entries")[0]["n"]
    replay = c.post(f"/api/v1/invoices/{cn_id}/post", headers=key)
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
    again = c.post(f"/api/v1/invoices/{cn_id}/post", headers={**ctx.acct, "Idempotency-Key": "another"})
    assert again.status_code == 409, again.text
    assert _rows(env, "SELECT COUNT(*) AS n FROM journal_entries")[0]["n"] == entries_after_post

    # The reversal entry is now POSTED and mirrors the original line for line.
    cn = _rows(env, "SELECT * FROM supplier_invoices WHERE id = ?", cn_id)[0]
    rev_je = _rows(env, "SELECT * FROM journal_entries WHERE id = ?", cn["journal_entry_id"])[0]
    assert rev_je["state"] == "POSTED"
    rev_lines = _rows(
        env, "SELECT account_code, debit_dzd, credit_dzd FROM journal_entry_lines WHERE journal_entry_id = ? ORDER BY id",
        rev_je["id"],
    )
    assert [(ln["account_code"], ln["credit_dzd"], ln["debit_dzd"]) for ln in rev_lines] == [
        (ln["account_code"], ln["debit_dzd"], ln["credit_dzd"]) for ln in original_lines_before
    ]

    # Every account nets to zero and the ledger still balances.
    tb = c.get("/api/v1/accounting/trial-balance", headers=ctx.acct).json()
    assert tb["balanced"] is True
    assert tb["total_debit_dzd"] == tb["total_credit_dzd"] == "23800.00"
    for row in tb["rows"]:
        assert row["debit_dzd"] == row["credit_dzd"], row

    # The original invoice's amounts, entry and lines are untouched: the history is preserved.
    original_after = _rows(env, "SELECT * FROM supplier_invoices WHERE id = ?", ctx.invoice_id)[0]
    for column in ("amount_ht_dzd", "amount_tva_dzd", "amount_ttc_dzd", "journal_entry_id", "payload_sha256",
                   "supplier_invoice_no", "posted_at", "posted_by_id"):
        assert original_after[column] == original_before[column], column
    assert _rows(
        env, "SELECT * FROM journal_entry_lines WHERE journal_entry_id = ? ORDER BY id", original_before["journal_entry_id"]
    ) == original_lines_before
    assert env.db.verify_audit_chain(1)["valid"] is True


def test_a_rejected_credit_note_posts_nothing(env: SimpleNamespace) -> None:
    c = env.client
    ctx = _posted_invoice(env)
    cn_id = _request_reversal(env, ctx)
    assert c.post(f"/api/v1/invoices/{cn_id}/submit-approval", headers=ctx.acct).status_code == 200

    rejected = c.post(
        f"/api/v1/invoices/{cn_id}/decide-approval", json={"approve": False, "reason": "non justifie"}, headers=ctx.approver
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["workflow_state"] == "Rejected"

    refused = c.post(f"/api/v1/invoices/{cn_id}/post", headers={**ctx.acct, "Idempotency-Key": "post-rejected"})
    assert refused.status_code == 409, refused.text
    tb = c.get("/api/v1/accounting/trial-balance", headers=ctx.acct).json()
    assert tb["total_debit_dzd"] == "11900.00" and tb["balanced"] is True


def test_a_second_reversal_request_is_refused_cleanly_and_writes_nothing(env: SimpleNamespace) -> None:
    c = env.client
    ctx = _posted_invoice(env)
    cn_id = _request_reversal(env, ctx, key="reverse-1")
    tables = ("supplier_invoices", "journal_entries", "journal_entry_lines", "idempotency_records")
    before = {t: _rows(env, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables}

    second = c.post(
        f"/api/v1/invoices/{ctx.invoice_id}/reverse",
        json={"reason": "encore"},
        headers={**ctx.acct, "Idempotency-Key": "reverse-2"},
    )

    assert second.status_code == 409, second.text  # was an unhandled IntegrityError -> 500
    assert f"credit note {cn_id}" in second.json()["detail"]
    assert {t: _rows(env, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables} == before

    # The original request is still replayable under its own key.
    replay = c.post(
        f"/api/v1/invoices/{ctx.invoice_id}/reverse",
        json={"reason": "Erreur de saisie"},
        headers={**ctx.acct, "Idempotency-Key": "reverse-1"},
    )
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True


def test_another_tenant_cannot_drive_a_credit_note(env: SimpleNamespace) -> None:
    c = env.client
    ctx = _posted_invoice(env)
    cn_id = _request_reversal(env, ctx)
    acct_b = _bearer(c, "acct_b", "AcctB#2026x")
    approver_b = _bearer(c, "approver_b", "ApprB#2026x")

    assert c.post(f"/api/v1/invoices/{cn_id}/submit-approval", headers=acct_b).status_code in (403, 404, 422)
    assert (
        c.post(f"/api/v1/invoices/{cn_id}/decide-approval", json={"approve": True}, headers=approver_b).status_code
        in (403, 404, 422)
    )
    assert (
        c.post(f"/api/v1/invoices/{cn_id}/post", headers={**acct_b, "Idempotency-Key": "foreign"}).status_code
        in (403, 404, 409)
    )
    assert _rows(env, "SELECT workflow_state FROM supplier_invoices WHERE id = ?", cn_id)[0]["workflow_state"] == "NeedsReview"


def test_each_workflow_path_is_registered_exactly_once(env: SimpleNamespace) -> None:
    """A duplicate registration is silently unreachable - the shape of the original defect."""
    seen: Dict[tuple, int] = {}
    for route in env.client.app.routes:
        for method in getattr(route, "methods", None) or ():
            # ``{invoice_id}`` and ``{reversal_invoice_id}`` are the same template
            # to the router: compare paths with the parameter names erased.
            key = (method, re.sub(r"\{[^}]*\}", "{}", getattr(route, "path", "")))
            seen[key] = seen.get(key, 0) + 1
    assert {k: n for k, n in seen.items() if n > 1} == {}
