#!/usr/bin/env python
# SPDX-License-Identifier: LGPL-3.0-only
"""Isolated, synthetic staging acceptance run for the Algerian AI ERP.

What this is
------------
An executable acceptance script, not a test suite replacement. It builds the
**real deployment application** through ``dz_erp.serve.build_app`` (the same
entry point ``uvicorn dz_erp.serve:app`` uses), then exercises the documented
end-to-end workflow against an isolated temporary directory:

    startup -> auth -> upload -> OCR -> human create -> field review -> approve -> post
            -> audit verification -> vault rotation -> audit rotation
            -> interrupted rotation + resume -> backup -> isolated restore
            -> verification in a fresh OS process

Every input is synthetic. Primary ERP state is kept under ``--state`` (a fresh temporary
directory by default). The checkpoint-path check creates a sibling directory on
the **same host** to verify path separation from the live ledger and vault; it does
not simulate off-host availability, independent credentials, or durable remote
retention. No production host, credential, or customer record is touched, and no
key material is printed.

Usage::

    python tools/staging_acceptance.py [--state DIR] [--keep]

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

RESULTS: List[Dict[str, Any]] = []
PASS = "PASS"
FAIL = "FAIL"


def check(name: str, fn: Callable[[], Any]) -> Any:
    """Run one acceptance step, recording an objective result."""
    try:
        detail = fn()
        RESULTS.append({"check": name, "result": PASS, "detail": detail})
        print(f"[{PASS}] {name}" + (f" — {detail}" if detail else ""))
        return detail
    except Exception as exc:
        RESULTS.append({"check": name, "result": FAIL, "detail": f"{type(exc).__name__}: {exc}"})
        print(f"[{FAIL}] {name} — {type(exc).__name__}: {exc}")
        return None


def _seed_tenant(db, *, company_id: int, code: str, name: str, users: List[tuple], supplier: int) -> None:
    from dz_erp.auth import hash_password

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (?, ?, ?, 'DZ', 'dz', '2026-01-01')",
            (company_id, code, name),
        )
        for user_id, username, role, password in users:
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (?, ?, ?, ?, 1, ?)",
                (user_id, company_id, username, role, hash_password(password, iterations=1000)),
            )
        conn.execute(
            "INSERT INTO partners (id, company_id, name, partner_type, nif, nis, ai, rc)"
            " VALUES (?, ?, ?, 'supplier', '001216099123456', '099116010123456',"
            " '16019912345', '16/00-0991234B12')",
            (supplier, company_id, f"{name} Supplier"),
        )


INVOICE = {
    "supplier_name": "Synthetic Supplier",
    "invoice_number": "FAC-STG-001",
    "invoice_date": "2026-10-16",
    "supplier_nif": "001216099123456",
    "supplier_rc": "16/00-0991234B12",
    "supplier_ai": "16019912345",
    "payment_method": "BANK_TRANSFER",
    "amount_ht_dzd": "20000.00",
    "amount_tva_dzd": "3800.00",
    "amount_timbre_dzd": "0.00",
    "amount_ttc_dzd": "23800.00",
    "lines": [
        {
            "sku": "S1",
            "description": "Consulting",
            "qty": "1.00",
            "unit_price_dzd": "20000.00",
            "tax_rate": "19.0",
            "line_ht_dzd": "20000.00",
        }
    ],
}


def run(state: Path) -> int:
    db_path = state / "erp.sqlite3"
    vault_path = state / "vault"
    security_log = state / "security.jsonl"
    os.environ.update(
        {
            "ERP_DB_PATH": str(db_path),
            "ERP_VAULT_PATH": str(vault_path),
            "ERP_SECURITY_LOG_PATH": str(security_log),
            # Secure default, asserted below.
            "ERP_DOCS_ENABLED": "false",
        }
    )
    os.environ.pop("ERP_TRUSTED_PROXIES", None)
    os.environ.pop("ERP_VAULT_KEY", None)
    os.environ.pop("ERP_AUDIT_HMAC_SECRET", None)

    from fastapi.testclient import TestClient

    from dz_erp.benchmark import render_synthetic_invoice_pdf
    from dz_erp.localization import load_official_odoo_chart
    from dz_erp.serve import build_app

    # ---------------------------------------------------------------- startup
    app = build_app(db_path=db_path, vault_path=vault_path)
    db, vault = app.state.db, app.state.vault
    client = TestClient(app)

    check("startup: health endpoint answers", lambda: _assert(client.get("/healthz").json()["status"] == "ok"))
    check(
        "startup: API documentation is disabled",
        lambda: _assert(all(client.get(p).status_code == 404 for p in ("/docs", "/redoc", "/openapi.json"))),
    )
    check(
        "startup: proxy trust fails closed",
        lambda: _assert(len(app.state.proxy_policy.trusted_proxies) == 0),
    )
    check(
        "startup: keys created beside the artefacts, not inside the vault",
        lambda: _assert(
            (state / "vault.vault.key").exists()
            and (state / "erp.sqlite3.audit.key").exists()
            and not list(vault_path.rglob("*.vault.key"))
        ),
    )

    # ------------------------------------------------------------------ seed
    _seed_tenant(
        db,
        company_id=1,
        code="DZ1",
        name="Synthetic Staging Company",
        users=[
            (1, "clerk", "clerk", "Clerk#2026x"),
            (3, "approver", "approver", "Appr#2026x"),
            (4, "accountant", "accountant", "Acct#2026x"),
        ],
        supplier=10,
    )
    _seed_tenant(
        db,
        company_id=2,
        code="DZ2",
        name="Synthetic Other Tenant",
        users=[(20, "other_admin", "admin", "Other#2026x")],
        supplier=20,
    )
    load_official_odoo_chart(db, company_id=1, apply_fixes_immediately=True, actor_user_id=4)
    load_official_odoo_chart(db, company_id=2, apply_fixes_immediately=True, actor_user_id=20)
    check("seed: synthetic SCF chart loaded for two tenants", lambda: _assert(True))

    # ------------------------------------------------------------------ auth
    def token(username: str, password: str) -> str:
        resp = client.post("/api/v1/auth/token", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        value = resp.json()["access_token"]
        assert value.startswith("dzsess_") and len(value) > 20
        return value

    def token_issued() -> str:
        """Return a print-safe description: never the token itself.

        Acceptance output is attached to tickets and pasted into reports, so a
        live credential must not appear in it even though it is synthetic.
        """
        token("clerk", "Clerk#2026x")
        return "token issued (value deliberately not printed)"

    check("auth: clerk obtains a server-issued bearer token", token_issued)
    clerk = token("clerk", "Clerk#2026x")
    approver = token("approver", "Appr#2026x")
    accountant = token("accountant", "Acct#2026x")
    other = token("other_admin", "Other#2026x")

    check(
        "auth: wrong password is rejected",
        lambda: _assert(
            client.post("/api/v1/auth/token", json={"username": "clerk", "password": "wrong"}).status_code == 401
        ),
    )
    check(
        "auth: spoofed identity headers are not trusted",
        lambda: _assert(
            client.get("/api/v1/invoices/1", headers={"X-User-Id": "4", "X-Company-Id": "1"}).status_code == 401
        ),
    )

    # ---------------------------------------------------------------- upload
    pdf = render_synthetic_invoice_pdf(INVOICE)
    upload = client.post(
        "/api/v1/documents/upload",
        headers={"Authorization": f"Bearer {clerk}"},
        files={"file": ("synthetic-staging.pdf", pdf, "application/pdf")},
    )
    check(
        "upload: synthetic invoice accepted and routed to human review",
        lambda: _assert(
            upload.status_code == 200 and upload.json()["workflow_state"] == "NeedsReview", upload.text
        ),
    )
    document = upload.json()

    def no_financial_effect() -> bool:
        with db.transaction() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS n FROM supplier_invoices").fetchone()["n"]
            journals = conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()["n"]
        return _assert(invoices == 0 and journals == 0, f"invoices={invoices} journals={journals}")

    check("ocr: extraction creates no invoice and no journal entry", no_financial_effect)
    check(
        "ocr: raw OCR output is preserved with provenance",
        lambda: _assert(_raw_count(db, "ocr_extracted_fields", document["document_id"]) > 0),
    )

    # ---------------------------------------------------------------- invoice
    created = client.post(
        "/api/v1/invoices",
        headers={"Authorization": f"Bearer {clerk}"},
        json={
            "supplier_id": 10,
            "supplier_invoice_no": "FAC-STG-001",
            "invoice_date": "2026-10-16",
            "amount_ht_dzd": "20000.00",
            "amount_tva_dzd": "3800.00",
            "amount_ttc_dzd": "23800.00",
            "document_id": document["document_id"],
            "lines": [
                {
                    "description": "Consulting",
                    "qty": "1",
                    "unit_price_dzd": "20000.00",
                    "tax_rate": "19.0",
                    "line_ht_dzd": "20000.00",
                }
            ],
        },
    )
    check("invoice: created and validated", lambda: _assert(created.status_code == 200, created.text))
    invoice_id = created.json()["invoice_id"]

    check(
        "rbac: a clerk cannot post an invoice",
        lambda: _assert(
            client.post(
                f"/api/v1/invoices/{invoice_id}/post",
                headers={"Authorization": f"Bearer {clerk}", "Idempotency-Key": "stg-clerk"},
            ).status_code
            == 403
        ),
    )
    check(
        "isolation: another tenant cannot read the invoice",
        lambda: _assert(
            client.get(
                f"/api/v1/invoices/{invoice_id}", headers={"Authorization": f"Bearer {other}"}
            ).status_code
            == 403
        ),
    )
    check(
        "review: submission is refused while the document review is incomplete",
        lambda: _assert(
            client.post(
                f"/api/v1/invoices/{invoice_id}/submit-approval",
                headers={"Authorization": f"Bearer {clerk}"},
            ).status_code
            == 422
        ),
    )

    # ------------------------------------------------------------ field review
    # Phase 29: the field review is enforced, not advisory. The reviewer
    # completes it through the documented API: every extracted required field
    # (and every extracted line quantity and line unit price) is CONFIRMED at
    # exactly its current value, and every optional field the extraction did
    # not find is asserted absent.
    verdict = client.get(
        f"/api/v1/documents/{document['document_id']}/review-policy",
        headers={"Authorization": f"Bearer {clerk}"},
    )
    check(
        "review: the review-policy verdict is readable and unsatisfied before decisions",
        lambda: _assert(
            verdict.status_code == 200 and verdict.json()["satisfied"] is False, verdict.text
        ),
    )

    def complete_review() -> bool:
        body = verdict.json()
        for fev in body["fields"]:
            if fev["satisfied"]:
                continue
            if fev["value"] is not None:
                decision = {
                    "field_name": fev["field_name"],
                    "decision": "CONFIRMED",
                    "expected_value": fev["value"],
                }
            elif not fev["required"]:
                decision = {"field_name": fev["field_name"], "decision": "NOT_PRESENT"}
            else:
                return _assert(
                    False,
                    f"required field {fev['field_name']} was not extracted; the staging"
                    " fixture must stay fully extracted (transcription path is covered by"
                    " the phase-29 test suite)",
                )
            resp = client.post(
                f"/api/v1/documents/{document['document_id']}/fields/decide",
                headers={"Authorization": f"Bearer {clerk}"},
                json=decision,
            )
            if resp.status_code != 200:
                return _assert(False, resp.text)
        for lev in body["lines"]:
            resp = client.post(
                f"/api/v1/documents/{document['document_id']}/fields/decide",
                headers={"Authorization": f"Bearer {clerk}"},
                json={
                    "field_name": "line_item_json",
                    "decision": "CONFIRMED",
                    "expected_value": lev["value"],
                    "line_index": lev["line_index"],
                },
            )
            if resp.status_code != 200:
                return _assert(False, resp.text)
        for pev in body["line_prices"]:
            resp = client.post(
                f"/api/v1/documents/{document['document_id']}/fields/decide",
                headers={"Authorization": f"Bearer {clerk}"},
                json={
                    "field_name": "line_unit_price",
                    "decision": "CONFIRMED",
                    "expected_value": pev["value"],
                    "line_index": pev["line_index"],
                },
            )
            if resp.status_code != 200:
                return _assert(False, resp.text)
        after = client.get(
            f"/api/v1/documents/{document['document_id']}/review-policy",
            headers={"Authorization": f"Bearer {clerk}"},
        )
        return _assert(
            after.status_code == 200 and after.json()["satisfied"] is True, after.text
        )

    check("review: field decisions satisfy the review policy", complete_review)
    check(
        "workflow: submitted for approval",
        lambda: _assert(
            client.post(
                f"/api/v1/invoices/{invoice_id}/submit-approval",
                headers={"Authorization": f"Bearer {clerk}", "Idempotency-Key": "stg-submit"},
            ).status_code
            == 200
        ),
    )
    check(
        "workflow: requester cannot approve their own invoice (segregation of duties)",
        lambda: _assert(
            client.post(
                f"/api/v1/invoices/{invoice_id}/decide-approval",
                headers={"Authorization": f"Bearer {clerk}", "Idempotency-Key": "stg-sod"},
                json={"approve": True, "reason": "self-approval attempt"},
            ).status_code
            == 403
        ),
    )
    check(
        "workflow: independent approver approves",
        lambda: _assert(
            client.post(
                f"/api/v1/invoices/{invoice_id}/decide-approval",
                headers={"Authorization": f"Bearer {approver}", "Idempotency-Key": "stg-approve"},
                json={"approve": True, "reason": "synthetic review"},
            ).status_code
            == 200
        ),
    )
    posted = client.post(
        f"/api/v1/invoices/{invoice_id}/post",
        headers={"Authorization": f"Bearer {accountant}", "Idempotency-Key": "stg-post"},
    )
    check("workflow: accountant posts the approved invoice", lambda: _assert(posted.status_code == 200, posted.text))
    check(
        "accounting: posted journal entry is balanced at 23800.00 DZD",
        lambda: _assert(_trial_balance(db) == (23800.0, 23800.0)),
    )
    replay = client.post(
        f"/api/v1/invoices/{invoice_id}/post",
        headers={"Authorization": f"Bearer {accountant}", "Idempotency-Key": "stg-post"},
    )
    check(
        "accounting: posting is idempotent for the same key",
        lambda: _assert(replay.status_code == 200 and replay.json()["journal_entry_id"] == posted.json()["journal_entry_id"]),
    )
    check(
        "accounting: a second posting of the same invoice is refused",
        lambda: _assert(
            client.post(
                f"/api/v1/invoices/{invoice_id}/post",
                headers={"Authorization": f"Bearer {accountant}", "Idempotency-Key": "stg-post-2"},
            ).status_code
            in (409, 422)
        ),
    )

    # ----------------------------------------------------------------- audit
    check("audit: chain verifies before rotation", lambda: _assert(db.verify_audit_chain(1)["valid"] is True))
    events_before = db.audit_events_by_key_version()

    # -------------------------------------------------------------- rotation
    vault_report = check(
        "vault rotation: every document re-encrypted and verified",
        lambda: _assert_report(vault.rotate_key(), lambda r: r["verified_ok"] and r["failed"] == 0),
    )
    audit_report = check(
        "audit rotation: historical chain still verifies",
        lambda: _assert_report(
            db.rotate_audit_key(), lambda r: r["chain_valid_before"] and r["chain_valid_after"]
        ),
    )
    check(
        "rotation: old and new key versions are both retained",
        lambda: _assert(vault.keyring.versions == [1, 2] and db.audit_keyring.versions == [1, 2]),
    )
    check(
        "rotation: documents remain readable after rotation",
        lambda: _assert(
            vault.read_bytes(1, 1, _doc_vault_path(db, document["document_id"])) == pdf
        ),
    )
    with db.transaction() as conn:
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=4,
            action_type="STAGING_POST_ROTATION_EVENT",
            target_model="synthetic",
            target_id="1",
            after_state={"probe": True},
        )
    check(
        "rotation: a chain spanning two key versions verifies",
        lambda: _assert(db.verify_audit_chain(1)["key_versions"].get("2") == 1),
    )

    # ------------------------ ledger checkpoint path separation (same-host simulation)
    from dz_erp.rotation_audit import (
        RotationLedger,
        RotationLedgerCheckpointError,
        RotationLedgerTruncatedError,
        load_checkpoint,
    )
    from dz_erp.rotate_keys import _refuse_local_checkpoint

    ledger_path = state / "vault.rotation-ledger.jsonl"
    ledger = RotationLedger.open(ledger_path, create=False)
    checkpoint_simulation_dir = state.parent / (state.name + "-checkpoint-simulation")
    checkpoint_simulation_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_simulation_dir / "vault.rotation.checkpoint.json"

    # check() records any return value as PASS, so a control that fails to fire
    # must raise rather than return False.
    def checkpoint_exported() -> bool:
        _refuse_local_checkpoint(checkpoint_path, ledger_path=ledger_path, vault_root=vault_path)
        exported = ledger.export_checkpoint(checkpoint_path)
        _assert(exported["record_count"] >= 1 and exported["head_hash"], exported)
        return load_checkpoint(checkpoint_path)["head_hash"] == exported["head_hash"]

    check("ledger: checkpoint path is separate from live state (same-host simulation)", checkpoint_exported)
    check(
        "ledger: the retained checkpoint matches the live ledger",
        lambda: _assert(ledger.verify_against_checkpoint(load_checkpoint(checkpoint_path))["checkpoint_matched"]),
    )

    def local_checkpoint_refused() -> bool:
        try:
            _refuse_local_checkpoint(state / "local-checkpoint.json", ledger_path=ledger_path, vault_root=vault_path)
        except RotationLedgerCheckpointError:
            return _assert(not (state / "local-checkpoint.json").exists())
        raise AssertionError("a checkpoint beside the ledger was accepted; it would be lost with the ledger")

    check("ledger: a checkpoint kept beside the ledger is refused", local_checkpoint_refused)

    def truncation_detected() -> bool:
        """Prove the control by removing the newest record, then restoring it."""
        original = ledger_path.read_bytes()
        try:
            lines = original.splitlines(keepends=True)
            ledger_path.write_bytes(b"".join(lines[:-1]))
            try:
                ledger.verify_against_checkpoint(load_checkpoint(checkpoint_path))
            except RotationLedgerTruncatedError:
                return True
            raise AssertionError("truncating the ledger went undetected")
        finally:
            ledger_path.write_bytes(original)

    check("ledger: removing the newest record is detected by the checkpoint", truncation_detected)

    # ------------------------------------------- interrupted rotation + resume
    check("recovery: interrupted rotation leaves every document readable", lambda: _interrupt_and_resume(vault))
    check(
        "recovery: resumption does not introduce a third key version",
        lambda: _assert(vault.keyring.versions == [1, 2], vault.keyring.versions),
    )

    # ------------------------------------------------------- backup / restore
    from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

    backup = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=state / "backups" / "staging.enc",
        backup_encryption_key=vault.encryption_key,
    )
    check(
        "backup: manifest names the required key versions only",
        lambda: _assert(
            [v["version"] for v in backup["vault_key_versions"]] == [1, 2]
            and [v["version"] for v in backup["audit_key_versions"]] == [1, 2]
            and "material" not in json.dumps(backup)
        ),
    )
    restore = check(
        "restore: isolated restore verifies hashes, trial balance and audit chain",
        lambda: _assert_report(
            restore_and_verify_backup(
                backup_path=Path(backup["backup_path"]),
                backup_encryption_key=vault.encryption_key,
                restore_target_dir=state / "restored",
            ),
            lambda r: r["db_sha256_verified"] and r["audit_chains_valid"],
        ),
    )

    # ------------------------------------------------- fresh process boundary
    check("restart: a new OS process reads the restored data", lambda: _fresh_process_verification(state))

    # -------------------------------------------------- secrets in artefacts
    check("logs: no key material in the security log or rotation journals", lambda: _no_secrets(state, db, vault))

    # ---------------------------------------------------------------- summary
    failed = [r for r in RESULTS if r["result"] != PASS]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:")
        for row in failed:
            print(f"  - {row['check']}: {row['detail']}")
        return 1
    assert vault_report and audit_report and restore is not None
    assert events_before
    return 0


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _assert(condition: Any, detail: Any = "") -> bool:
    if not condition:
        raise AssertionError(f"condition not met {detail}")
    return True


def _assert_report(report: Dict[str, Any], predicate: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
    if not predicate(report):
        raise AssertionError(f"unexpected report: {json.dumps(report, default=str)[:400]}")
    return report


def _raw_count(db, table: str, document_id: str) -> int:
    with db.transaction() as conn:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE document_id = ?", (document_id,)
            ).fetchone()["n"]
        )


def _trial_balance(db) -> tuple:
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT SUM(CAST(jel.debit_dzd AS REAL)) AS debit,
                   SUM(CAST(jel.credit_dzd AS REAL)) AS credit
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE je.state = 'POSTED'
            """
        ).fetchone()
    return (row["debit"], row["credit"])


def _doc_vault_path(db, document_id: str) -> str:
    with db.transaction() as conn:
        return conn.execute(
            "SELECT vault_path FROM source_documents WHERE id = ?", (document_id,)
        ).fetchone()["vault_path"]


def _interrupt_and_resume(vault) -> bool:
    """Force a genuine partial rotation, then resume it exactly as an operator would."""
    from unittest.mock import patch

    # Add a third document so there is something left to migrate after the
    # forced interruption.
    vault.store_bytes(1, "f" * 64, b"SYNTHETIC DOCUMENT ADDED BEFORE INTERRUPTION")
    from dz_erp.intake_ocr import PrivateDocumentVault

    original = PrivateDocumentVault._rotate_one
    calls = {"n": 0}

    def fail_after_one(self, path, target_version, max_retries, journal, report):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("simulated operator interruption")
        return original(self, path, target_version, max_retries, journal, report)

    new_key = vault.keyring.material_for(2)
    with patch.object(PrivateDocumentVault, "_rotate_one", fail_after_one):
        try:
            vault.rotate_key(new_key)
        except RuntimeError:
            pass
        else:  # pragma: no cover - the interruption must actually happen
            raise AssertionError("the simulated interruption did not occur")

    verification = vault.verify_blobs()
    if verification["failed"]:
        raise AssertionError(f"an interruption left unreadable documents: {verification}")
    resumed = vault.rotate_key(new_key)
    if not (resumed["resumed"] and resumed["verified_ok"]):
        raise AssertionError(f"resumption failed: {json.dumps(resumed, default=str)[:400]}")
    return True


def _fresh_process_verification(state: Path) -> bool:
    restored = state / "restored"
    script = (
        "import json, sys\n"
        "from dz_erp.database import ERPDatabase\n"
        "from dz_erp.intake_ocr import PrivateDocumentVault\n"
        "db = ERPDatabase(sys.argv[1])\n"
        "vault = PrivateDocumentVault(sys.argv[2])\n"
        "blobs = sorted(__import__('pathlib').Path(sys.argv[2]).rglob('*.enc'))\n"
        "contents = [vault.read_bytes(1, 1, str(b)) for b in blobs]\n"
        "print(json.dumps({'chain_valid': db.verify_audit_chain(1)['valid'],\n"
        "                  'documents': len(contents),\n"
        "                  'audit_versions': db.audit_keyring.versions}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(restored / "erp_snapshot.sqlite3"), str(restored / "vault")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        env={k: v for k, v in os.environ.items() if not k.startswith("ERP_")},
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip().splitlines()[-1] if proc.stderr else "child failed")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if not (payload["chain_valid"] and payload["documents"] > 0 and payload["audit_versions"] == [1, 2]):
        raise AssertionError(f"fresh process disagreed: {payload}")
    return True


def _no_secrets(state: Path, db, vault) -> bool:
    artefacts = [
        state / "security.jsonl",
        *state.parent.glob(f"{state.name}/**/*rotation.jsonl"),
        # The privileged-operation ledger is a separate artefact with its own
        # key; it must be scanned for material like every other log.
        *state.parent.glob(f"{state.name}/**/*rotation-ledger.jsonl"),
    ]
    text = "\n".join(p.read_text(errors="ignore") for p in artefacts if p.exists())
    for version in vault.keyring.versions:
        material = vault.keyring.material_for(version)
        if material.decode("ascii") in text or material.hex() in text:
            raise AssertionError(f"vault key version {version} appears in a log artefact")
    for version in db.audit_keyring.versions:
        material = db.audit_keyring.material_for(version)
        if material.hex() in text:
            raise AssertionError(f"audit key version {version} appears in a log artefact")
    return True


def _cleanup_default_state(state: Path) -> None:
    """Remove default temporary state and the same-host checkpoint simulation directory."""
    shutil.rmtree(state, ignore_errors=True)
    shutil.rmtree(state.parent / f"{state.name}-checkpoint-simulation", ignore_errors=True)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated synthetic staging acceptance run.")
    parser.add_argument("--state", help="Directory to use (must be empty or absent).")
    parser.add_argument("--keep", action="store_true", help="Keep the state directory afterwards.")
    args = parser.parse_args(argv)

    if args.state:
        state = Path(args.state).resolve()
        if state.exists() and any(state.iterdir()):
            print(f"refusing to run: {state} is not empty", file=sys.stderr)
            return 2
        state.mkdir(parents=True, exist_ok=True)
    else:
        state = Path(tempfile.mkdtemp(prefix="erp-staging-"))

    print(f"staging acceptance — isolated state: {state}")
    print("synthetic data only; no production host or credential is used")
    print("checkpoint path separation uses a same-host sibling directory; no off-host durability is claimed\n")
    try:
        status = run(state)
    except Exception:
        traceback.print_exc()
        print("\nstaging acceptance aborted with an unexpected error")
        status = 1
    finally:
        if not args.keep and not args.state:
            _cleanup_default_state(state)

    summary = state / "acceptance-summary.json"
    if state.exists() and args.keep:
        summary.write_text(json.dumps(RESULTS, indent=2))
        print(f"\nresults written to {summary}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
