# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 14 — Tamper-evident rotation ledger: content, integrity, recovery, refusal.

The ledger exists because the audit chain cannot record the rotation of its own
key. These tests therefore attack the ledger the way an adversary or a bad day
would: edit a record, delete one from the middle, reorder, insert, truncate the
tail, remove the key, interrupt a run, and race two rotations. Each case asserts
that the failure is *named* - a missing key must never look like a valid ledger,
and an unverified rotation must never be recorded as a success.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from dz_erp.database import AuditTamperError, ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.key_rotation import KeyConfigurationError
from dz_erp.rotation_audit import (
    RotationLedger,
    RotationLedgerError,
    RotationLedgerTamperError,
    RotationLedgerTruncatedError,
)


def _seed_vault(root: Path, count: int = 2) -> PrivateDocumentVault:
    vault = PrivateDocumentVault(root)
    for index in range(count):
        vault.store_bytes(1, f"{index:064x}", f"SYNTHETIC DOCUMENT {index}".encode())
    return vault


def _seed_db(path: Path) -> ERPDatabase:
    from dz_erp.database import ERPDatabase as DB

    db = DB(str(path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=None,
            action_type="SYNTHETIC",
            target_model="synthetic",
            target_id="1",
            after_state={"n": 1},
        )
    return db


def _records(ledger: RotationLedger) -> list:
    return ledger.records()


def _rewrite(ledger: RotationLedger, rows: list) -> None:
    ledger.path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )


# ----------------------------------------------------------------------
# 1. Content: what a record must and must not contain
# ----------------------------------------------------------------------
def test_vault_rotation_records_start_and_completion_with_counts(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    report = vault.rotate_key()

    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    rows = _records(ledger)
    assert [row["phase"] for row in rows] == ["STARTED", "COMPLETED"]
    assert rows[0]["operation"] == "VAULT_KEY_ROTATION"
    assert rows[0]["operation_id"] == rows[1]["operation_id"] == report["ledger"]["operation_id"]
    assert rows[0]["from_version"] == 1 and rows[1]["to_version"] == 2
    assert rows[1]["counts"] == {"attempted": 2, "completed": 2, "skipped": 0, "failed": 0}
    assert rows[1]["integrity"]["verified"] is True
    assert rows[1]["retained_versions"] == [1, 2]
    assert ledger.verify()["valid"] is True


def test_audit_rotation_is_recorded_without_rewriting_history(tmp_path: Path) -> None:
    db = _seed_db(tmp_path / "erp.sqlite3")
    before = db.audit_events_by_key_version()
    report = db.rotate_audit_key()

    ledger = RotationLedger.open(tmp_path / "erp.sqlite3.rotation-ledger.jsonl")
    rows = _records(ledger)
    assert [row["phase"] for row in rows] == ["STARTED", "COMPLETED"]
    assert rows[0]["operation"] == "AUDIT_KEY_ROTATION"
    assert rows[1]["detail"]["history_rewritten"] is False
    assert rows[1]["integrity"]["verified"] is True
    # History is untouched: the same events exist under the same version.
    assert db.audit_events_by_key_version() == before == {1: 1}
    assert report["status"] == "COMPLETED_VERIFIED"


def test_records_carry_an_actor_but_never_key_material_or_plaintext(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    text = ledger.path.read_text()

    for version in vault.keyring.versions:
        material = vault.keyring.material_for(version)
        assert material.decode("ascii") not in text
        assert material.hex() not in text
    for index in range(2):
        assert f"SYNTHETIC DOCUMENT {index}" not in text

    row = _records(ledger)[0]
    assert row["actor"]["user"], "a privileged operation with no accountable actor is not auditable"
    assert {"user", "host", "pid", "source"} == set(row["actor"])
    # Tenant association is deliberately absent: rotation is deployment-global
    # and every read path of audit_events is tenant-scoped.
    assert "company_id" not in row


def test_rotation_does_not_create_events_in_any_tenant_chain(tmp_path: Path) -> None:
    """Recording a global privileged operation must not fabricate a tenant."""
    db = _seed_db(tmp_path / "erp.sqlite3")
    before = db.audit_events_by_key_version()
    db.rotate_audit_key()
    assert db.audit_events_by_key_version() == before
    with db.transaction() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]
    assert total == 1


# ----------------------------------------------------------------------
# 2. Tampering with records
# ----------------------------------------------------------------------
def test_modified_record_is_detected(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    rows = _records(ledger)
    rows[1]["counts"]["failed"] = 0  # already 0; change something meaningful instead
    rows[1]["counts"]["completed"] = 999
    _rewrite(ledger, rows)

    result = ledger.verify()
    assert result["valid"] is False
    assert result["reason"] == "RECORD_TAMPERED"
    assert result["broken_at_seq"] == 2


def test_reformatted_record_still_verifies(tmp_path: Path) -> None:
    """The MAC covers canonical JSON, so key order and spacing are not integrity.

    The file format itself is JSONL (one record per line); that is a storage
    contract, not part of the MAC. Re-serialising each record on one line with a
    different key order and padding must therefore still verify.
    """
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    rows = _records(ledger)
    ledger.path.write_text(
        "".join(json.dumps(r, sort_keys=False, separators=(" , ", " : ")) + "\n" for r in rows)
    )
    assert ledger.verify()["valid"] is True


def test_deleted_interior_record_is_detected(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    vault.rotate_key(vault.keyring.material_for(2))
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    rows = _records(ledger)
    assert len(rows) >= 3
    del rows[1]
    _rewrite(ledger, rows)

    result = ledger.verify()
    assert result["valid"] is False
    assert result["reason"] in {"SEQUENCE_GAP", "PREV_HASH_MISMATCH"}


def test_reordered_records_are_detected(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    rows = _records(ledger)
    rows[0], rows[1] = rows[1], rows[0]
    _rewrite(ledger, rows)

    result = ledger.verify()
    assert result["valid"] is False
    assert result["reason"] in {"SEQUENCE_GAP", "RECORD_TAMPERED", "PREV_HASH_MISMATCH"}


def test_inserted_record_is_detected(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    rows = _records(ledger)
    forged = dict(rows[-1])
    forged["seq"] = len(rows) + 1
    forged["phase"] = "COMPLETED"
    forged["counts"] = {"attempted": 0, "completed": 0, "skipped": 0, "failed": 0}
    forged["record_hash"] = "f" * 64
    _rewrite(ledger, [*rows, forged])

    result = ledger.verify()
    assert result["valid"] is False
    # Either failure naming is correct: the forged record breaks its own MAC and
    # also fails to link to the genuine predecessor.
    assert result["reason"] in {"RECORD_TAMPERED", "PREV_HASH_MISMATCH"}


def test_ledger_that_does_not_start_at_genesis_is_rejected(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    rows = _records(ledger)
    rows[0]["prev_hash"] = "a" * 64
    _rewrite(ledger, rows)
    assert ledger.verify()["reason"] == "PREV_HASH_MISMATCH"


def test_corrupt_json_is_reported_as_unreadable_not_valid(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    ledger.path.write_text("{not json\n", encoding="utf-8")
    result = ledger.verify()
    assert result["valid"] is False
    assert result["reason"] == "LEDGER_UNREADABLE"


# ----------------------------------------------------------------------
# 3. Tail truncation and checkpoints
# ----------------------------------------------------------------------
def test_tail_truncation_is_invisible_to_the_chain_but_caught_by_a_checkpoint(
    tmp_path: Path,
) -> None:
    """Honest limit: a hash chain cannot see its own tail disappear.

    This is why checkpoints exist. The test asserts both halves of that
    statement, so the limitation is documented by execution rather than prose.
    """
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    checkpoint = ledger.export_checkpoint(tmp_path / "checkpoint.json")
    assert checkpoint["record_count"] == 2

    rows = _records(ledger)
    _rewrite(ledger, rows[:-1])  # drop the COMPLETED record

    assert ledger.verify()["valid"] is True, "the chain genuinely cannot see its own tail vanish"
    with pytest.raises(RotationLedgerTruncatedError) as excinfo:
        ledger.verify_against_checkpoint(checkpoint)
    assert excinfo.value.expected_count == 2
    assert excinfo.value.actual_count == 1

    # The truncation also un-does the completion, so the operation now reads as
    # started-but-unfinished - a second, independent signal.
    assert ledger.verify()["open_operations"] == [rows[0]["operation_id"]]


def test_checkpoint_mismatch_at_the_same_length_is_detected(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    checkpoint = ledger.export_checkpoint()

    rows = _records(ledger)
    rows[-1]["counts"]["completed"] = 0
    _rewrite(ledger, rows)

    result = ledger.verify_against_checkpoint(checkpoint)
    assert result["valid"] is False or result["reason"] in {"RECORD_TAMPERED", "CHECKPOINT_MISMATCH"}


def test_appending_after_a_checkpoint_is_expected_and_verifies(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    checkpoint = ledger.export_checkpoint()

    vault.rotate_key(vault.keyring.material_for(2))
    result = ledger.verify_against_checkpoint(checkpoint)
    assert result["valid"] is True
    assert result["checkpoint_matched"] is True
    assert result["record_count"] > result["checkpoint_record_count"]


def test_checkpoint_refuses_to_bless_a_tampered_ledger(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    rows = _records(ledger)
    rows[0]["operation"] = "FORGED_OPERATION"
    _rewrite(ledger, rows)

    with pytest.raises(RotationLedgerTamperError):
        ledger.export_checkpoint()


# ----------------------------------------------------------------------
# 4. Missing ledger key
# ----------------------------------------------------------------------
def test_missing_ledger_key_is_reported_as_unavailable_never_valid(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    vault.rotate_key()
    # An un-rotated ledger uses the single-key layout (the registry file is only
    # written if the ledger key itself is ever versioned).
    key_file = tmp_path / "vault.rotation-ledger.rotation.key"
    assert key_file.exists()
    key_file.unlink()

    with pytest.raises(KeyConfigurationError, match="cannot be read without the key"):
        RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl", create=False)

    # Rebuilding a registry over existing records with new material must not
    # produce a valid ledger.
    fresh = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    result = fresh.verify()
    assert result["valid"] is False
    assert result["reason"] == "RECORD_TAMPERED"


def test_ledger_key_is_independent_of_vault_and_audit_keys(tmp_path: Path) -> None:
    """The property that makes the ledger survive rotation of the stores."""
    vault = _seed_vault(tmp_path / "vault")
    db = _seed_db(tmp_path / "erp.sqlite3")
    vault.rotate_key()
    db.rotate_audit_key()

    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    assert ledger.keyring.kind == "rotation"
    vault_material = {vault.keyring.material_for(v) for v in vault.keyring.versions}
    audit_material = {db.audit_keyring.material_for(v) for v in db.audit_keyring.versions}
    ledger_material = ledger.keyring.active_material
    assert ledger_material not in vault_material
    assert ledger_material not in audit_material


# ----------------------------------------------------------------------
# 5. Interruption, recovery, refusal
# ----------------------------------------------------------------------
def test_interrupted_rotation_leaves_an_open_operation_that_the_next_run_recovers(
    tmp_path: Path,
) -> None:
    vault = _seed_vault(tmp_path / "vault", count=3)
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    # A hard kill (power loss, SIGKILL) cannot write a terminal record, so the
    # durable signature of an interrupted run is a STARTED record with no
    # successor. Simulated directly, because an exception raised inside the
    # context manager is - correctly - recorded as FAILED instead.
    interrupted_id = "orphaned-operation-0001"
    ledger.append(
        operation="VAULT_KEY_ROTATION",
        phase="STARTED",
        store=f"vault:{vault.vault_root}",
        operation_id=interrupted_id,
        from_version=1,
    )
    verify = ledger.verify()
    assert verify["valid"] is True
    assert verify["open_operations"] == [interrupted_id]

    # The next run records the recovery explicitly, naming the orphan.
    report = vault.rotate_key()
    assert report["status"] == "COMPLETED_VERIFIED"
    rows = _records(ledger)
    phases = [row["phase"] for row in rows]
    assert "RECOVERY" in phases
    recovery = next(row for row in rows if row["phase"] == "RECOVERY")
    assert recovery["detail"]["interrupted_operations"] == [interrupted_id]
    # The recovery record references the old operation; the new one completes.
    assert ledger.verify()["open_operations"] == []


def test_two_concurrent_rotations_are_serialised_and_the_refusal_is_recorded(
    tmp_path: Path,
) -> None:
    vault = _seed_vault(tmp_path / "vault", count=4)
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    results: list = []
    errors: list = []

    def attempt() -> None:
        try:
            results.append(vault.rotate_key(ledger=ledger))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert len(results) + len(errors) == 2
    assert ledger.verify()["valid"] is True
    phases = [row["phase"] for row in _records(ledger)]
    # Whatever the interleaving, no operation may be left dangling.
    assert ledger.verify()["open_operations"] == []
    assert "COMPLETED" in phases
    if errors:
        assert any(row["phase"] in {"REFUSED", "FAILED"} for row in _records(ledger))


def test_a_rotation_that_fails_integrity_verification_is_not_recorded_as_verified(
    tmp_path: Path,
) -> None:
    vault = _seed_vault(tmp_path / "vault", count=2)
    # Corrupt one blob so the post-rotation sweep cannot verify everything.
    target = sorted((tmp_path / "vault").rglob("*.enc"))[0]
    raw = bytearray(target.read_bytes())
    raw[-4] ^= 0xFF
    target.write_bytes(bytes(raw))

    report = vault.rotate_key()

    assert report["verified_ok"] is False
    assert report["status"] == "COMPLETED_UNVERIFIED"
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    completed = [row for row in _records(ledger) if row["phase"] == "COMPLETED"][0]
    assert completed["integrity"]["verified"] is False
    assert completed["integrity"]["failures"] == 1
    assert completed["counts"]["failed"] == 1


def test_rotation_refused_before_it_starts_is_recorded_as_refused(tmp_path: Path) -> None:
    """A precondition refusal is audit evidence, not silence."""
    vault = _seed_vault(tmp_path / "vault")
    before = {p: p.read_bytes() for p in (tmp_path / "vault").rglob("*.enc")}

    # 999 cannot travel in the one-byte version header: a configuration refusal
    # that happens before any record is re-encrypted.
    with pytest.raises(KeyConfigurationError, match="outside the supported range"):
        vault.rotate_key(new_version=999)

    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    phases = [row["phase"] for row in _records(ledger)]
    assert phases[-1] == "REFUSED"
    assert ledger.verify()["valid"] is True
    # Nothing was migrated.
    assert {p: p.read_bytes() for p in (tmp_path / "vault").rglob("*.enc")} == before


def test_audit_rotation_refused_on_a_broken_chain_is_recorded(tmp_path: Path) -> None:
    db = _seed_db(tmp_path / "erp.sqlite3")
    # Break the chain behind the application's back.
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "erp.sqlite3"))
    try:
        conn.execute("DROP TRIGGER IF EXISTS trg_audit_no_update")
        conn.execute("UPDATE audit_events SET action_type = 'FORGED' WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AuditTamperError):
        db.rotate_audit_key()

    ledger = RotationLedger.open(tmp_path / "erp.sqlite3.rotation-ledger.jsonl")
    rows = _records(ledger)
    assert rows[-1]["phase"] == "FAILED"
    assert "AuditTamperError" in rows[-1]["detail"]["error"]


# ----------------------------------------------------------------------
# 6. Ledger write failure must not yield a silent success
# ----------------------------------------------------------------------
def test_unwritable_ledger_prevents_the_operation_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _seed_vault(tmp_path / "vault")
    before = {p: p.read_bytes() for p in (tmp_path / "vault").rglob("*.enc")}

    def explode(*args, **kwargs):
        raise RotationLedgerError("simulated ledger storage failure")

    monkeypatch.setattr(RotationLedger, "append", explode)
    with pytest.raises(RotationLedgerError, match="simulated ledger storage failure"):
        vault.rotate_key()

    # Nothing was mutated, and no key version was added.
    after = {p: p.read_bytes() for p in (tmp_path / "vault").rglob("*.enc")}
    assert after == before
    assert not (tmp_path / "vault.vault.keys.json").exists()
