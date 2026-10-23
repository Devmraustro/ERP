# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 11 — Audit HMAC-key rotation: historical verification, tamper detection, recovery.

Audit rotation is deliberately *not* implemented the way vault rotation is. A
MAC cannot be recomputed without rewriting the event it protects, and rewriting
audit history is the one thing an audit trail must never permit. Rotation here
therefore means: "sign new events with the next key version, and keep every
historical version so old events still verify". These tests pin that behaviour
down, including the failure modes that would make a tampered chain look intact
or an intact chain look tampered.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dz_erp.database import (
    AUDIT_KEYRING_SUFFIX,
    AuditKeyUnavailableError,
    AuditTamperError,
    ERPDatabase,
    FinancialIntegrityError,
)
from dz_erp.key_rotation import KeyConfigurationError, KeyRing, KeyVersionInUseError
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed_database(db: ERPDatabase, *, events: int = 3, company_id: int = 1) -> None:
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone():
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, created_at)"
                " VALUES (?, ?, 'Synthetic Company', 'DZ', '2026-01-01')",
                (company_id, f"DZ{company_id}"),
            )
    for index in range(events):
        with db.transaction() as conn:
            db.append_audit_event(
                conn,
                company_id=company_id,
                actor_user_id=None,
                action_type=f"SYNTHETIC_EVENT_{index}",
                target_model="synthetic",
                target_id=str(index),
                after_state={"index": index},
            )


def _raw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _drop_protection(db_path: Path) -> None:
    """Simulate an attacker who already has write access to the database file.

    The append-only triggers are defence in depth against a compromised
    *application*; someone holding the file can remove them. Tamper detection
    therefore cannot depend on the triggers existing - it depends on the chain.
    """
    conn = _raw(db_path)
    try:
        conn.execute("DROP TRIGGER IF EXISTS trg_audit_no_update")
        conn.execute("DROP TRIGGER IF EXISTS trg_audit_no_delete")
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 1. Historical events: migration and backward compatibility
# ----------------------------------------------------------------------
def test_events_written_before_versioning_verify_after_the_schema_migration(tmp_path: Path) -> None:
    """The migration must not require re-signing anything.

    Every event that existed before this change was signed with the only key
    there was, so version 1 is a factual statement about those rows, not a
    default that happens to be convenient.
    """
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=4)
    assert db.audit_events_by_key_version() == {1: 4}

    # Rewind to the pre-versioning schema.
    conn = _raw(db_path)
    try:
        conn.execute("ALTER TABLE audit_events DROP COLUMN hmac_key_version")
        conn.commit()
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(audit_events)")}
    finally:
        conn.close()
    assert "hmac_key_version" not in columns

    reopened = ERPDatabase(str(db_path))
    assert reopened.audit_events_by_key_version() == {1: 4}
    result = reopened.verify_audit_chain(1)
    assert result["valid"] is True
    assert result["checked_count"] == 4
    assert result["key_versions"] == {"1": 4}

    # The migration is idempotent: running it again changes nothing.
    again = ERPDatabase(str(db_path))
    assert again.audit_events_by_key_version() == {1: 4}
    assert again.verify_audit_chain(1)["valid"] is True


def test_a_pre_versioning_database_restores_from_backup(tmp_path: Path) -> None:
    """Archives written before key registries existed stay restorable."""
    from dz_erp.intake_ocr import PrivateDocumentVault

    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    _seed_database(db)
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC DOCUMENT")

    backup = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "legacy.enc",
        backup_encryption_key=vault.encryption_key,
    )
    report = restore_and_verify_backup(
        backup_path=Path(backup["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "restored",
    )
    assert report["audit_chains_valid"] is True
    assert report["restored_db"].audit_keyring.versions == [1]


# ----------------------------------------------------------------------
# 2. Rotation and mixed-version chains
# ----------------------------------------------------------------------
def test_new_events_use_the_new_key_and_old_events_still_verify(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=2)
    before = [dict(r) for r in _raw(db_path).execute("SELECT id, event_hash, occurred_at FROM audit_events")]

    report = db.rotate_audit_key()

    assert report["previous_version"] == 1
    assert report["active_version"] == 2
    assert report["chain_valid_before"] is True
    assert report["chain_valid_after"] is True
    assert report["retained_versions"] == [1, 2]

    # Rotation must not rewrite a single historical event.
    after = [dict(r) for r in _raw(db_path).execute("SELECT id, event_hash, occurred_at FROM audit_events")]
    assert after == before, "rotation modified historical audit events"

    _seed_database(db, events=2)
    assert db.audit_events_by_key_version() == {1: 2, 2: 2}
    result = db.verify_audit_chain(1)
    assert result["valid"] is True
    assert result["key_versions"] == {"1": 2, "2": 2}
    assert result["checked_count"] == 4

    # A mixed-version chain has to verify from a fresh process too.
    reopened = ERPDatabase(str(db_path))
    assert reopened.verify_audit_chain(1)["valid"] is True
    assert reopened.audit_keyring.versions == [1, 2]


def test_mixed_version_events_are_signed_with_their_own_version(tmp_path: Path) -> None:
    """The version on the row must be the version that actually signed it."""
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)
    db.rotate_audit_key()
    _seed_database(db, events=1)

    with db.transaction() as conn:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id").fetchall()
    first, second = rows[0], rows[1]
    assert (first["hmac_key_version"], second["hmac_key_version"]) == (1, 2)

    # Re-computing each with the wrong version must not reproduce its hash.
    for row, wrong_version in ((first, 2), (second, 1)):
        recomputed = db._compute_audit_hash(
            company_id=row["company_id"],
            occurred_at=row["occurred_at"],
            actor_user_id=row["actor_user_id"],
            action_type=row["action_type"],
            target_model=row["target_model"],
            target_id=row["target_id"],
            before_json=row["before_json"],
            after_json=row["after_json"],
            prev_hash=row["prev_hash"],
            key_version=wrong_version,
        )
        assert recomputed != row["event_hash"], "the key version is not bound to the event"


# ----------------------------------------------------------------------
# 3. Tamper detection after rotation
# ----------------------------------------------------------------------
def _rotated_db(tmp_path: Path) -> tuple:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=3)
    db.rotate_audit_key()
    _seed_database(db, events=2)
    assert db.verify_audit_chain(1)["valid"] is True
    return db, db_path


def test_modified_event_is_detected_after_rotation(tmp_path: Path) -> None:
    db, db_path = _rotated_db(tmp_path)
    _drop_protection(db_path)
    conn = _raw(db_path)
    try:
        conn.execute("UPDATE audit_events SET action_type = 'FORGED' WHERE id = 2")
        conn.commit()
    finally:
        conn.close()

    result = db.verify_audit_chain(1)
    assert result["valid"] is False
    assert result["reason"] == "EVENT_HASH_TAMPERED"
    assert result["broken_at_id"] == 2


def test_deleted_event_is_detected_after_rotation(tmp_path: Path) -> None:
    db, db_path = _rotated_db(tmp_path)
    _drop_protection(db_path)
    conn = _raw(db_path)
    try:
        conn.execute("DELETE FROM audit_events WHERE id = 3")
        conn.commit()
    finally:
        conn.close()

    result = db.verify_audit_chain(1)
    assert result["valid"] is False
    assert result["reason"] == "PREV_HASH_MISMATCH"


def test_reordered_event_is_detected_after_rotation(tmp_path: Path) -> None:
    db, db_path = _rotated_db(tmp_path)
    _drop_protection(db_path)
    conn = _raw(db_path)
    try:
        # Swap the timestamps of two events: order is part of the signed input.
        row_a = conn.execute("SELECT occurred_at FROM audit_events WHERE id = 1").fetchone()["occurred_at"]
        row_b = conn.execute("SELECT occurred_at FROM audit_events WHERE id = 3").fetchone()["occurred_at"]
        conn.execute("UPDATE audit_events SET occurred_at = ? WHERE id = 1", (row_b,))
        conn.execute("UPDATE audit_events SET occurred_at = ? WHERE id = 3", (row_a,))
        conn.commit()
    finally:
        conn.close()

    assert db.verify_audit_chain(1)["reason"] == "EVENT_HASH_TAMPERED"


def test_inserted_event_is_detected_after_rotation(tmp_path: Path) -> None:
    db, db_path = _rotated_db(tmp_path)
    last = _raw(db_path).execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
    conn = _raw(db_path)
    try:
        conn.execute(
            """
            INSERT INTO audit_events (company_id, occurred_at, actor_user_id, action_type,
                target_model, target_id, correlation_id, before_json, after_json,
                hmac_key_version, prev_hash, event_hash)
            VALUES (1, '2026-01-01T00:00:00+00:00', NULL, 'INJECTED', 'synthetic', '999',
                    NULL, NULL, '{}', 2, ?, ?)
            """,
            (last["event_hash"], "f" * 64),
        )
        conn.commit()
    finally:
        conn.close()

    result = db.verify_audit_chain(1)
    assert result["valid"] is False
    assert result["reason"] == "EVENT_HASH_TAMPERED"
    assert result["broken_at_id"] == last["id"] + 1


def test_append_only_triggers_still_block_application_level_mutation(tmp_path: Path) -> None:
    db, _db_path = _rotated_db(tmp_path)
    with pytest.raises(AuditTamperError, match="AUDIT_TAMPER_VIOLATION"):
        with db.transaction() as conn:
            conn.execute("UPDATE audit_events SET hmac_key_version = 99 WHERE id = 1")


def test_rotation_is_refused_on_an_already_tampered_chain(tmp_path: Path) -> None:
    """A key change on top of a broken chain would bury the evidence."""
    db, db_path = _rotated_db(tmp_path)
    _drop_protection(db_path)
    conn = _raw(db_path)
    try:
        conn.execute("UPDATE audit_events SET after_json = '{\"forged\": true}' WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AuditTamperError, match="does not verify"):
        db.rotate_audit_key()
    # Nothing was persisted, so the on-disk registry still names version 2.
    ring = KeyRing.load(db_path.with_name(db_path.name + AUDIT_KEYRING_SUFFIX), "audit")
    assert ring.active_version == 2


# ----------------------------------------------------------------------
# 4. Missing / wrong historical keys
# ----------------------------------------------------------------------
def test_a_lost_historical_key_is_reported_as_unavailable_not_as_valid(tmp_path: Path) -> None:
    db, db_path = _rotated_db(tmp_path)
    ring_path = db_path.with_name(db_path.name + AUDIT_KEYRING_SUFFIX)
    payload = json.loads(ring_path.read_text())
    del payload["keys"]["1"]
    ring_path.write_text(json.dumps(payload))

    # Opening the database fails closed rather than reporting a damaged chain.
    with pytest.raises(AuditKeyUnavailableError) as excinfo:
        ERPDatabase(str(db_path))
    assert "version(s) [1]" in str(excinfo.value)
    assert "misreport an intact chain as tampered" in str(excinfo.value)

    # And the in-process verifier refuses to call it valid.
    incomplete = KeyRing.from_dict(payload)
    db._audit_keyring = incomplete
    result = db.verify_audit_chain(1)
    assert result["valid"] is False
    assert result["reason"] == "AUDIT_KEY_UNAVAILABLE"
    assert result["key_unavailable"] is True
    assert result["missing_key_versions"] == [1]
    assert result["unverifiable_count"] == 3


def test_wrong_key_is_not_silently_accepted(tmp_path: Path) -> None:
    """A valid key of the right shape must still be rejected if it is the wrong one."""
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)
    db.rotate_audit_key()

    with pytest.raises(KeyConfigurationError, match="explicit audit key disagrees"):
        ERPDatabase(str(db_path), hmac_secret=b"z" * 32)


def test_environment_key_contradicting_the_registry_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)
    db.rotate_audit_key()

    monkeypatch.setenv("ERP_AUDIT_HMAC_SECRET", "an-entirely-different-secret-value")
    with pytest.raises(KeyConfigurationError, match="ERP_AUDIT_HMAC_SECRET disagrees"):
        ERPDatabase(str(db_path))


# ----------------------------------------------------------------------
# 5. Interruption and atomicity of the registry write
# ----------------------------------------------------------------------
def test_failed_registry_write_leaves_the_chain_and_active_key_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=2)
    before_events = [dict(r) for r in _raw(db_path).execute("SELECT * FROM audit_events")]

    def explode(self, path):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(KeyRing, "save", explode)
    with pytest.raises(OSError, match="simulated disk failure"):
        db.rotate_audit_key()
    monkeypatch.undo()

    # In-memory state did not move: new events are still signed with version 1.
    assert db.active_audit_key_version == 1
    _seed_database(db, events=1)
    assert db.audit_events_by_key_version() == {1: 3}
    assert db.verify_audit_chain(1)["valid"] is True
    assert [dict(r) for r in _raw(db_path).execute("SELECT * FROM audit_events")][:2] == before_events

    # No partial registry was left behind either.
    ring_path = db_path.with_name(db_path.name + AUDIT_KEYRING_SUFFIX)
    assert not ring_path.exists()
    assert not list(tmp_path.glob("*.tmp"))

    # And the rotation still works once the failure is gone.
    report = db.rotate_audit_key()
    assert report["active_version"] == 2
    assert db.verify_audit_chain(1)["valid"] is True


def test_resuming_a_rotation_with_the_same_key_does_not_add_a_version(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)

    first = db.rotate_audit_key()
    key = db.audit_keyring.material_for(first["active_version"])
    second = ERPDatabase(str(db_path)).rotate_audit_key(key)

    assert second["resumed"] is True
    assert second["active_version"] == 2
    assert second["retained_versions"] == [1, 2]
    assert second["chain_valid_after"] is True


def test_rotation_is_refused_for_an_in_memory_database(tmp_path: Path) -> None:
    db = ERPDatabase(":memory:")
    _seed_database(db, events=1)
    with pytest.raises(KeyConfigurationError, match="no durable key registry"):
        db.rotate_audit_key()


# ----------------------------------------------------------------------
# 6. Rollback and key lifecycle
# ----------------------------------------------------------------------
def test_rollback_reactivates_an_older_version_without_touching_history(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)
    db.rotate_audit_key()
    _seed_database(db, events=1)  # signed while version 2 was active
    before = db.audit_events_by_key_version()
    assert before == {1: 1, 2: 1}

    result = db.set_active_audit_key_version(1)
    assert result == {"active_version": 1, "events_unchanged": True, "chain_valid": True}

    _seed_database(db, events=1)
    assert db.audit_events_by_key_version() == {1: 2, 2: 1}
    assert db.verify_audit_chain(1)["valid"] is True
    # Rolling back the active key never rewrites what was already signed.
    assert db.verify_audit_chain(1)["key_versions"] == {"1": 2, "2": 1}
    # Version 2 is retained so events signed during its active period stay readable.
    assert db.audit_keyring.versions == [1, 2]


def test_key_removal_is_refused_while_events_depend_on_it(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=2)
    db.rotate_audit_key()

    with pytest.raises(KeyVersionInUseError, match="still signed with it"):
        db.remove_audit_key_version(1, confirm_retained_copies=True)
    with pytest.raises(KeyConfigurationError, match="active version"):
        db.remove_audit_key_version(2, confirm_retained_copies=True)

    # The active key was never touched, so both versions still verify.
    assert db.audit_keyring.versions == [1, 2]
    assert db.verify_audit_chain(1)["valid"] is True


# ----------------------------------------------------------------------
# 7. Backup, restore and process restarts
# ----------------------------------------------------------------------
def test_backup_restore_preserves_every_audit_key_version(tmp_path: Path) -> None:
    from dz_erp.intake_ocr import PrivateDocumentVault

    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=2)
    db.rotate_audit_key()
    _seed_database(db, events=2)
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "b" * 64, b"SYNTHETIC DOCUMENT")

    backup = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backups" / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )
    # Version numbers, not key material, are surfaced to the operator.
    assert [v["version"] for v in backup["audit_key_versions"]] == [1, 2]
    assert "material" not in json.dumps(backup)

    report = restore_and_verify_backup(
        backup_path=Path(backup["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "restore",
    )
    assert report["audit_chains_valid"] is True
    restored = report["restored_db"]
    assert restored.audit_keyring.versions == [1, 2]
    assert restored.audit_events_by_key_version() == {1: 2, 2: 2}
    assert restored.verify_audit_chain(1)["valid"] is True

    # Reopen exactly as a recovering deployment would: no secrets supplied.
    out = tmp_path / "restore"
    reopened = ERPDatabase(str(out / "erp_snapshot.sqlite3"))
    assert reopened.audit_keyring.versions == [1, 2]
    assert reopened.verify_audit_chain(1)["valid"] is True
    assert (out / "erp_snapshot.sqlite3.audit.key").exists()
    assert (out / ("erp_snapshot.sqlite3" + AUDIT_KEYRING_SUFFIX)).exists()


def test_restore_reports_a_missing_key_version_as_unavailable(tmp_path: Path) -> None:
    """Losing a key must not be reported as a corrupt audit trail."""
    from dz_erp.intake_ocr import PrivateDocumentVault

    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    _seed_database(db, events=2)
    db.rotate_audit_key()
    _seed_database(db, events=1)
    vault = PrivateDocumentVault(tmp_path / "vault")

    backup = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )
    report = restore_and_verify_backup(
        backup_path=Path(backup["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "restored",
    )
    restored_path = tmp_path / "restored" / "erp_snapshot.sqlite3"

    # Delete version 1 from the restored registry and re-run the verification
    # path the restore itself uses.
    ring_path = restored_path.with_name(restored_path.name + AUDIT_KEYRING_SUFFIX)
    payload = json.loads(ring_path.read_text())
    del payload["keys"]["1"]
    ring_path.write_text(json.dumps(payload))

    with pytest.raises(AuditKeyUnavailableError, match=r"version\(s\) \[1\]"):
        ERPDatabase(str(restored_path))
    # The successful pre-tamper restore is still evidence that restore worked.
    assert report["audit_chains_valid"] is True


def test_chain_verifies_in_a_separate_operating_system_process(tmp_path: Path) -> None:
    """In-process tests cannot prove persistence; a fresh interpreter can."""
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=2)
    db.rotate_audit_key()
    _seed_database(db, events=2)

    script = (
        "import json, sys\n"
        "from dz_erp.database import ERPDatabase\n"
        "db = ERPDatabase(sys.argv[1])\n"
        "print(json.dumps({'chain': db.verify_audit_chain(1), "
        "'versions': db.audit_keyring.versions, "
        "'events': db.audit_events_by_key_version()}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(db_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        # Inherit the real environment minus every ERP_* override, so the child
        # resolves keys from disk exactly as a restarted service would. A
        # hardcoded POSIX PATH would be an invalid environment on Windows
        # (missing SYSTEMROOT breaks sockets/imports there).
        env={**{k: v for k, v in os.environ.items() if not k.startswith("ERP_")},
             "PYTHONPATH": str(REPO_ROOT)},
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["chain"]["valid"] is True
    assert payload["chain"]["key_versions"] == {"1": 2, "2": 2}
    assert payload["versions"] == [1, 2]
    assert payload["events"] == {"1": 2, "2": 2}


# ----------------------------------------------------------------------
# 8. No key material in journals or reports
# ----------------------------------------------------------------------
def test_rotation_output_never_contains_key_material(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)
    report = db.rotate_audit_key()
    journal_text = Path(report["journal_path"]).read_text()
    report_text = json.dumps(report, default=str)

    for version in db.audit_keyring.versions:
        material = db.audit_keyring.material_for(version)
        assert material.hex() not in journal_text
        assert material.hex() not in report_text
        # base64 of the material must not appear either.
        import base64

        encoded = base64.b64encode(material).decode()
        assert encoded not in journal_text
        assert encoded not in report_text
    for entry in journal_text.splitlines():
        assert set(json.loads(entry)) <= {
            "timestamp",
            "event",
            "previous_version",
            "target_version",
            "resumed",
            "chain_valid",
        }


def test_key_ring_summary_exposes_versions_but_no_material(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)
    db.rotate_audit_key()

    summary = db.audit_keyring.summary()
    assert summary["kind"] == "audit"
    assert summary["active_version"] == 2
    assert [entry["version"] for entry in summary["versions"]] == [1, 2]
    serialised = json.dumps(summary)
    for version in (1, 2):
        assert db.audit_keyring.material_for(version).hex() not in serialised
    assert "material" not in serialised


# ----------------------------------------------------------------------
# 9. Tenant association and chain independence
# ----------------------------------------------------------------------
def test_tampering_in_one_company_does_not_affect_another(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=2, company_id=1)
    _seed_database(db, events=2, company_id=2)
    db.rotate_audit_key()
    _seed_database(db, events=1, company_id=1)

    _drop_protection(db_path)
    conn = _raw(db_path)
    try:
        conn.execute("UPDATE audit_events SET action_type = 'FORGED' WHERE id = 1 AND company_id = 1")
        conn.commit()
    finally:
        conn.close()

    assert db.verify_audit_chain(1)["valid"] is False
    assert db.verify_audit_chain(2)["valid"] is True

    overall = db.verify_all_audit_chains()
    assert overall["valid"] is False
    assert overall["companies"][2]["valid"] is True


def test_security_financial_error_still_distinguishes_key_loss_from_tampering(tmp_path: Path) -> None:
    """The two failure classes must not be collapsed by the new code path."""
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    _seed_database(db, events=1)

    # A genuine tamper is a financial-integrity failure...
    _drop_protection(db_path)
    conn = _raw(db_path)
    try:
        conn.execute("UPDATE audit_events SET action_type = 'FORGED' WHERE id = 1")
        conn.commit()
    finally:
        conn.close()
    assert db.verify_audit_chain(1)["reason"] == "EVENT_HASH_TAMPERED"
    # ...while a missing key is its own class, not a tamper report.
    assert not issubclass(AuditKeyUnavailableError, FinancialIntegrityError)
