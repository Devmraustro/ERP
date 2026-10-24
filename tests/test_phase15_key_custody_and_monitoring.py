# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 15 — Key custody enforcement and rotation monitoring.

Two claims are tested here, and neither is allowed to be aspirational:

1. **File permissions are enforced, not assumed.** A key file readable by another
   local account is refused at start-up with an actionable message, rather than
   silently used. The check is honest about what it can verify on each platform.
2. **Rotation can be *monitored*.** An operator needs to see data points -
   overdue keys, interruption state, "the key provider is not configured" - that
   are machine-readable so they can be alerted on. A status report that only
   exists inside an operator's head is not a control.

No test here claims that `0600` is HSM-equivalent. The module reports
``equivalent_to_hsm: False`` and asserts it.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.key_rotation import (
    KeyCustodyError,
    KeyRing,
    check_key_file_custody,
    describe_key_protection,
    require_key_file_custody,
)
from dz_erp.rotation_audit import RotationLedger

posix_only = pytest.mark.skipif(os.name != "posix", reason="mode-bit custody is a POSIX property")


def _loosen(path: Path) -> None:
    os.chmod(path, 0o644)


# ----------------------------------------------------------------------
# 1. The custody primitive
# ----------------------------------------------------------------------
@posix_only
def test_owner_only_file_passes_and_group_readable_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "secret.key"
    path.write_bytes(b"x" * 44)
    os.chmod(path, 0o600)

    ok = check_key_file_custody(path)
    assert ok["ok"] is True and ok["mode"] == "0o600"
    require_key_file_custody(path)  # must not raise

    for bad_mode in (0o640, 0o604, 0o644, 0o660, 0o666, 0o400):
        os.chmod(path, bad_mode)
        result = check_key_file_custody(path)
        assert result["ok"] is (bad_mode in (0o400,)), (bad_mode, result)
        if bad_mode != 0o400:
            with pytest.raises(KeyCustodyError, match="grants access beyond the owner"):
                require_key_file_custody(path)


@posix_only
def test_custody_error_names_the_remediation(tmp_path: Path) -> None:
    path = tmp_path / "secret.key"
    path.write_bytes(b"x")
    os.chmod(path, 0o644)
    with pytest.raises(KeyCustodyError) as excinfo:
        require_key_file_custody(path)
    message = str(excinfo.value)
    assert "chmod 600" in message
    assert "provides no confidentiality" in message


def test_protection_report_never_claims_hsm_equivalence() -> None:
    """The honest limit must be machine-readable, not just prose."""
    report = describe_key_protection()
    assert report["equivalent_to_hsm"] is False
    assert report["platform"] in {"posix", "windows"}
    if os.name == "posix":
        assert report["enforced"] is True
        assert report["mechanism"] == "file mode bits"
    else:  # pragma: no cover - Windows
        assert report["enforced"] is False, "Windows mode bits must not be presented as enforcement"


# ----------------------------------------------------------------------
# 2. Enforcement at start-up for every key-bearing file
# ----------------------------------------------------------------------
@posix_only
def test_vault_refuses_to_open_with_a_world_readable_key_file(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    PrivateDocumentVault(vault_root).store_bytes(1, "a" * 64, b"SYNTHETIC")
    key_file = tmp_path / "vault.vault.key"
    _loosen(key_file)

    with pytest.raises(KeyCustodyError, match="vault.vault.key"):
        PrivateDocumentVault(vault_root)

    os.chmod(key_file, 0o600)
    assert PrivateDocumentVault(vault_root).scan_blob_versions() == {1: 1}


@posix_only
def test_vault_refuses_a_loose_key_registry_after_rotation(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    vault = PrivateDocumentVault(vault_root)
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC")
    vault.rotate_key()
    ring_path = tmp_path / "vault.vault.keys.json"
    assert check_key_file_custody(ring_path)["ok"] is True

    _loosen(ring_path)
    with pytest.raises(KeyCustodyError, match="keys.json"):
        PrivateDocumentVault(vault_root)

    os.chmod(ring_path, 0o600)
    assert PrivateDocumentVault(vault_root).active_key_version == 2


@posix_only
def test_audit_store_refuses_a_loose_key_file(tmp_path: Path) -> None:
    db_path = tmp_path / "erp.sqlite3"
    ERPDatabase(str(db_path))
    key_file = tmp_path / ("erp.sqlite3.audit.key")
    _loosen(key_file)

    with pytest.raises(KeyCustodyError, match="audit.key"):
        ERPDatabase(str(db_path))

    os.chmod(key_file, 0o600)
    assert ERPDatabase(str(db_path)).verify_all_audit_chains()["valid"] is True


@posix_only
def test_rotation_ledger_refuses_a_loose_key_file(tmp_path: Path) -> None:
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC")
    vault.rotate_key()

    ledger_key = tmp_path / "vault.rotation-ledger.rotation.key"
    assert ledger_key.exists()
    _loosen(ledger_key)
    with pytest.raises(KeyCustodyError, match="rotation.key"):
        RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    os.chmod(ledger_key, 0o600)
    assert RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl").verify()["valid"] is True


@posix_only
def test_keys_created_by_the_application_are_never_group_or_world_readable(tmp_path: Path) -> None:
    """The control has to hold for files this code creates, not only for ones it reads."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC")
    vault.rotate_key()
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        db.append_audit_event(
            conn, company_id=1, actor_user_id=None, action_type="T",
            target_model="m", target_id="1", after_state={},
        )
    db.rotate_audit_key()
    RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")

    key_files = [
        tmp_path / "vault.vault.key",
        tmp_path / "vault.vault.keys.json",
        tmp_path / "erp.sqlite3.audit.key",
        tmp_path / "erp.sqlite3.audit.keys.json",
        tmp_path / "vault.rotation-ledger.rotation.key",
    ]
    for path in key_files:
        assert path.exists(), path
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, f"{path} is {oct(mode)}"
        assert check_key_file_custody(path)["ok"] is True


# ----------------------------------------------------------------------
# 3. Monitoring surface (what an operator can alert on)
# ----------------------------------------------------------------------
def _deployment(tmp_path: Path) -> tuple:
    vault = PrivateDocumentVault(tmp_path / "vault")
    for index in range(3):
        vault.store_bytes(1, f"{index:064x}", f"DOC {index}".encode())
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        db.append_audit_event(
            conn, company_id=1, actor_user_id=None, action_type="T",
            target_model="m", target_id="1", after_state={},
        )
    return vault, db


def test_monitoring_report_exposes_freshness_interruptions_and_custody(tmp_path: Path) -> None:
    vault, db = _deployment(tmp_path)
    vault.rotate_key()
    db.rotate_audit_key()

    from dz_erp.rotate_keys import build_monitoring_report

    report = build_monitoring_report(db_path=str(tmp_path / "erp.sqlite3"), vault_path=str(tmp_path / "vault"))

    assert report["vault"]["active_version"] == 2
    assert report["vault"]["keys_overdue"] == []
    assert report["vault"]["unfinished_operations"] == []
    assert report["audit"]["active_version"] == 2
    assert report["audit"]["chain_valid"] is True
    assert report["custody"]["equivalent_to_hsm"] is False
    # Machine-readable verdict for alerting.
    assert report["healthy"] is True
    # And still no key material anywhere in it.
    for version in vault.keyring.versions:
        assert vault.keyring.material_for(version).decode() not in str(report)
    for version in db.audit_keyring.versions:
        assert db.audit_keyring.material_for(version).hex() not in str(report)


def test_monitoring_flags_an_overdue_key(tmp_path: Path) -> None:
    vault, db = _deployment(tmp_path)
    vault.rotate_key()

    from dz_erp.rotate_keys import build_monitoring_report

    # Age the key past the threshold and confirm the report says so.
    ring = KeyRing.load(tmp_path / "vault.vault.keys.json", "vault")
    payload = __import__("json").loads((tmp_path / "vault.vault.keys.json").read_text())
    for entry in payload["keys"].values():
        entry["created_at"] = "2024-01-01T00:00:00+00:00"
    (tmp_path / "vault.vault.keys.json").write_text(__import__("json").dumps(payload))
    os.chmod(tmp_path / "vault.vault.keys.json", 0o600)
    assert ring.active_version == 2

    report = build_monitoring_report(
        db_path=str(tmp_path / "erp.sqlite3"),
        vault_path=str(tmp_path / "vault"),
        max_key_age_days=365,
    )
    assert report["vault"]["keys_overdue"], "an overdue key must be reported, not inferred from a timestamp"
    assert report["healthy"] is False


def test_monitoring_flags_an_unfinished_rotation(tmp_path: Path) -> None:
    vault, db = _deployment(tmp_path)
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    ledger.append(
        operation="VAULT_KEY_ROTATION",
        phase="STARTED",
        store=f"vault:{vault.vault_root}",
        operation_id="killed-mid-run",
        from_version=1,
    )

    from dz_erp.rotate_keys import build_monitoring_report

    report = build_monitoring_report(db_path=str(tmp_path / "erp.sqlite3"), vault_path=str(tmp_path / "vault"))
    assert report["vault"]["unfinished_operations"] == ["killed-mid-run"]
    assert report["healthy"] is False


def test_monitoring_reports_an_unreadable_ledger_as_unhealthy(tmp_path: Path) -> None:
    vault, db = _deployment(tmp_path)
    vault.rotate_key()
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    rows = ledger.records()
    rows[0]["phase"] = "TAMPERED"
    ledger.path.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows))

    from dz_erp.rotate_keys import build_monitoring_report

    report = build_monitoring_report(db_path=str(tmp_path / "erp.sqlite3"), vault_path=str(tmp_path / "vault"))
    assert report["ledger"]["valid"] is False
    assert report["healthy"] is False


def test_missing_key_provider_configuration_is_reported_not_hidden(tmp_path: Path) -> None:
    """Failure behaviour when an external key provider is expected but absent."""
    vault, db = _deployment(tmp_path)

    from dz_erp.rotate_keys import build_monitoring_report

    report = build_monitoring_report(
        db_path=str(tmp_path / "erp.sqlite3"),
        vault_path=str(tmp_path / "vault"),
        require_external_provider=True,
    )
    assert report["key_provider"]["mode"] == "file"
    assert report["key_provider"]["satisfies_requirement"] is False
    assert report["healthy"] is False
    assert any("provider" in note.lower() for note in report["blocking_notes"])
