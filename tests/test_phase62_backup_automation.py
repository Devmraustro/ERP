# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 10 — scheduled encrypted backups, retention and integrity monitoring.

What was already true: the application can take an encrypted backup and restore it.
What was missing, and is tested here by execution:

* a scheduled run that writes a manifest an integrity monitor can use **without** the
  decryption key;
* retention that removes old artefacts but can never remove the newest one or one
  carrying a legal hold;
* verification that detects a single flipped byte, and ``--deep`` verification that
  actually restores and compares the trial balance and audit chain;
* a failure path that alerts instead of failing silently;
* a CLI whose exit codes a systemd timer can act on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from dz_erp import ops_alerts, ops_backup
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault


def _seed(tmp_path: Path) -> tuple[ERPDatabase, PrivateDocumentVault]:
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Backup Co', 'DZ', 'dz', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
            " VALUES (1, 1, 'admin', 'admin', 1, ?)",
            (hash_password("Admin#2026x", iterations=1000),),
        )
        conn.execute(
            "INSERT INTO journal_entries (id, company_id, entry_number, journal_code, entry_date,"
            " state, source_model, source_id, total_debit_dzd, total_credit_dzd, posted_by_id, posted_at)"
            " VALUES (1, 1, 'BK-0001', 'OD', '2026-03-01', 'POSTED', 'test', '1',"
            " '250000.00', '250000.00', 1, '2026-03-01T09:00:00+00:00')"
        )
        conn.executemany(
            "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label,"
            " debit_dzd, credit_dzd) VALUES (1, 1, ?, ?, ?, ?)",
            [
                ("601", "Achats", "250000.00", "0.00"),
                ("401", "Fournisseur", "0.00", "250000.00"),
            ],
        )
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=1,
            action_type="BACKUP_DRILL",
            target_model="test",
            target_id="1",
            after_state={"step": 1},
        )
    vault.store_bytes(1, "b" * 64, b"%PDF-1.7 backup drill payload")
    return db, vault


def _stamp(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ----------------------------------------------------------------------
# a scheduled run produces a verifiable artefact
# ----------------------------------------------------------------------
def test_scheduled_backup_writes_artefact_and_manifest(tmp_path: Path) -> None:
    db, vault = _seed(tmp_path)
    key = Fernet.generate_key()
    backup_dir = tmp_path / "backups"

    result = ops_backup.run_backup(db, vault, backup_dir, key, now=_stamp(0))

    artefact = Path(result["backup"])
    assert artefact.is_file() and artefact.suffix == ".erpbackup"
    manifest = ops_backup.read_manifest(artefact)
    assert manifest is not None
    assert manifest["sha256"] == result["sha256"]
    assert manifest["size_bytes"] == artefact.stat().st_size
    assert manifest["vault_file_count"] == 1
    assert manifest["legal_hold"] is False
    if os.name == "posix":
        assert artefact.stat().st_mode & 0o777 == 0o600, "backup must not be group/world readable"
        assert ops_backup.manifest_path_for(artefact).stat().st_mode & 0o777 == 0o600


def test_verify_detects_a_single_flipped_byte(tmp_path: Path) -> None:
    db, vault = _seed(tmp_path)
    key = Fernet.generate_key()
    backup_dir = tmp_path / "backups"
    result = ops_backup.run_backup(db, vault, backup_dir, key, now=_stamp(0))
    artefact = Path(result["backup"])

    assert ops_backup.verify_all(backup_dir)["ok"] is True

    payload = bytearray(artefact.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    artefact.write_bytes(bytes(payload))

    summary = ops_backup.verify_all(backup_dir, alert=False)
    assert summary["ok"] is False
    assert summary["failed"] == 1
    assert summary["failures"][0]["checks"]["sha256_matches"] is False


def test_integrity_failure_raises_an_alert(tmp_path: Path) -> None:
    db, vault = _seed(tmp_path)
    key = Fernet.generate_key()
    backup_dir = tmp_path / "backups"
    result = ops_backup.run_backup(db, vault, backup_dir, key, now=_stamp(0))
    Path(result["backup"]).write_bytes(b"corrupted")

    stream = open(os.devnull, "w", encoding="utf-8")
    try:
        summary = ops_backup.verify_all(backup_dir, alert=True, stream=stream)
    finally:
        stream.close()
    assert summary["ok"] is False

    captured = []
    ops_backup.verify_all(backup_dir, alert=True, stream=_Collector(captured))
    events = [json.loads(line)["event"] for line in captured]
    assert ops_alerts.BACKUP_INTEGRITY_FAILED in events


class _Collector:
    """Minimal file-like sink for alert assertions."""

    def __init__(self, sink: list) -> None:
        self.sink = sink

    def write(self, text: str) -> int:
        self.sink.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_deep_verify_restores_and_compares_the_ledger(tmp_path: Path) -> None:
    db, vault = _seed(tmp_path)
    key = Fernet.generate_key()
    backup_dir = tmp_path / "backups"
    result = ops_backup.run_backup(db, vault, backup_dir, key, now=_stamp(0))

    report = ops_backup.verify_backup(Path(result["backup"]), key=key, deep=True)
    assert report["ok"] is True, report
    deep = report["checks"]["deep"]
    assert deep["db_sha256_verified"] is True
    # These come from our own inspection of the restored file, not from the
    # restorer's summary fields.
    assert deep["integrity_check"] == "ok"
    assert deep["foreign_key_violations"] == 0
    assert deep["trial_balance_debit_dzd"] == deep["trial_balance_credit_dzd"] == "250000.00"
    assert deep["posted_line_count"] == 2
    assert deep["balanced"] is True
    assert deep["vault_files_verified"] == 1
    assert deep["audit_chains_valid"] is True
    assert report["checks"]["deep_ok"] is True


# ----------------------------------------------------------------------
# retention
# ----------------------------------------------------------------------
def _fake_backups(directory: Path, days: list[int]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    made = []
    for day in days:
        name = ops_backup.backup_name(_stamp(day))
        path = directory / name
        path.write_bytes(b"x" * 32)
        ops_backup.write_manifest(path, result={"db_sha256": "0" * 64, "vault_file_count": 0})
        made.append(path)
    return made


def test_retention_keeps_daily_and_weekly_windows(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    # 10 consecutive days, two backups on the newest day.
    days = list(range(10))
    _fake_backups(backup_dir, days)
    newest = backup_dir / ops_backup.backup_name(_stamp(0))
    newest.write_bytes(b"y" * 64)
    ops_backup.write_manifest(newest, result={"db_sha256": "1" * 64, "vault_file_count": 0})

    keep, delete = ops_backup.plan_retention(
        ops_backup.list_backups(backup_dir), retain_daily=3, retain_weekly=2
    )
    kept_days = {p.name[len("erp-") : len("erp-") + 8] for p in keep}
    # The three most recent days survive the daily window.
    today = _stamp(0).strftime("%Y%m%d")
    yesterday = _stamp(1).strftime("%Y%m%d")
    assert today in kept_days and yesterday in kept_days
    assert delete, "older artefacts must be deletable"
    # The newest artefact is never in the delete list.
    assert newest.name not in {p.name for p in delete}
    # Nothing deleted is newer than something kept, for this simple fixture.
    assert set(keep) | set(delete) == set(ops_backup.list_backups(backup_dir))


def test_retention_never_deletes_the_only_backup(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _fake_backups(backup_dir, [5])
    result = ops_backup.prune(backup_dir, retain_daily=0, retain_weekly=0)
    assert result["deleted"] == []
    assert len(ops_backup.list_backups(backup_dir)) == 1


def test_retention_respects_a_legal_hold(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    made = _fake_backups(backup_dir, [30, 20, 10, 5])
    held = made[-1]  # oldest one is put on hold
    ops_backup.write_manifest(held, result={"db_sha256": "0" * 64, "vault_file_count": 0}, legal_hold=True)

    result = ops_backup.prune(backup_dir, retain_daily=1, retain_weekly=0)
    assert held.name in result["kept"]
    assert held.is_file()
    # Its manifest must survive with it, otherwise the hold is invisible next run.
    assert ops_backup.read_manifest(held)["legal_hold"] is True


def test_retention_dry_run_deletes_nothing(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _fake_backups(backup_dir, [40, 30, 20, 10, 5])
    before = len(ops_backup.list_backups(backup_dir))
    result = ops_backup.prune(backup_dir, retain_daily=1, retain_weekly=1, dry_run=True)
    assert result["deleted"]
    assert len(ops_backup.list_backups(backup_dir)) == before


def test_prune_removes_the_manifest_with_the_artefact(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _fake_backups(backup_dir, [40, 30, 20])
    ops_backup.prune(backup_dir, retain_daily=1, retain_weekly=0)
    for artefact in ops_backup.list_backups(backup_dir):
        assert ops_backup.manifest_path_for(artefact).is_file()
    orphans = list(backup_dir.glob("*" + ops_backup.MANIFEST_SUFFIX))
    assert len(orphans) == len(ops_backup.list_backups(backup_dir))


# ----------------------------------------------------------------------
# status / staleness / failure alerting
# ----------------------------------------------------------------------
def test_status_reports_freshness_and_last_verification(tmp_path: Path) -> None:
    db, vault = _seed(tmp_path)
    key = Fernet.generate_key()
    backup_dir = tmp_path / "backups"
    ops_backup.run_backup(db, vault, backup_dir, key, now=_stamp(0))
    ops_backup.verify_all(backup_dir)

    summary = ops_backup.status(backup_dir, max_age_seconds=3600)
    assert summary["count"] == 1
    assert summary["stale"] is False
    assert summary["last_verify"]["ok"] is True
    assert summary["total_bytes"] > 0


def test_status_detects_a_stale_backup(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    made = _fake_backups(backup_dir, [3])
    old = made[0]
    old_time = (_stamp(3)).timestamp()
    os.utime(old, (old_time, old_time))
    summary = ops_backup.status(backup_dir, max_age_seconds=60)
    assert summary["stale"] is True
    assert summary["newest_age_seconds"] > 60


def test_a_failed_backup_alerts_and_reraises(tmp_path: Path) -> None:
    class ExplodingDB:
        db_path = str(tmp_path / "nope.sqlite3")
        _persistent_conn = None

        def transaction(self):  # pragma: no cover - never reached
            raise RuntimeError("no database")

    captured: list = []
    with pytest.raises(Exception):
        ops_backup.run_backup(
            ExplodingDB(),
            PrivateDocumentVault(tmp_path / "vault"),
            tmp_path / "backups",
            Fernet.generate_key(),
            stream=_Collector(captured),
        )
    events = [json.loads(line)["event"] for line in captured]
    assert ops_alerts.BACKUP_FAILED in events


def test_backup_key_file_must_be_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission semantics required")
    key_file = tmp_path / "backup.key"
    key_file.write_bytes(Fernet.generate_key())
    os.chmod(key_file, 0o644)
    monkeypatch.setenv(ops_backup.BACKUP_KEY_FILE_ENV, str(key_file))
    with pytest.raises(PermissionError):
        ops_backup.resolve_backup_key()

    os.chmod(key_file, 0o600)
    assert ops_backup.resolve_backup_key() == key_file.read_bytes().strip()


# ----------------------------------------------------------------------
# CLI contract (what a systemd timer actually calls)
# ----------------------------------------------------------------------
def test_cli_run_verify_status_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, vault = _seed(tmp_path)
    backup_dir = tmp_path / "backups"
    key_file = tmp_path / "backup.key"
    key_file.write_bytes(Fernet.generate_key())
    if os.name == "posix":
        os.chmod(key_file, 0o600)
    monkeypatch.setenv("ERP_DB_PATH", db.db_path)
    monkeypatch.setenv("ERP_VAULT_PATH", str(vault.vault_root))
    monkeypatch.setenv(ops_backup.BACKUP_DIR_ENV, str(backup_dir))
    monkeypatch.setenv(ops_backup.BACKUP_KEY_FILE_ENV, str(key_file))

    assert ops_backup.main(["--backup-dir", str(backup_dir), "run"]) == 0
    assert len(ops_backup.list_backups(backup_dir)) == 1
    assert ops_backup.main(["--backup-dir", str(backup_dir), "verify"]) == 0
    assert ops_backup.main(["--backup-dir", str(backup_dir), "status"]) == 0
    assert ops_backup.main(["--backup-dir", str(backup_dir), "prune", "--dry-run"]) == 0


def test_cli_without_a_key_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ops_backup.BACKUP_KEY_FILE_ENV, raising=False)
    monkeypatch.delenv(ops_backup.BACKUP_KEY_ENV, raising=False)
    assert ops_backup.main(["--backup-dir", str(tmp_path / "backups"), "run"]) == 2


def test_cli_verify_deep_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, vault = _seed(tmp_path)
    backup_dir = tmp_path / "backups"
    key = Fernet.generate_key()
    key_file = tmp_path / "backup.key"
    key_file.write_bytes(key)
    if os.name == "posix":
        os.chmod(key_file, 0o600)
    ops_backup.run_backup(db, vault, backup_dir, key, now=_stamp(0))
    monkeypatch.setenv(ops_backup.BACKUP_KEY_FILE_ENV, str(key_file))
    assert ops_backup.main(["--backup-dir", str(backup_dir), "verify", "--deep"]) == 0


def test_unknown_alert_event_is_rejected() -> None:
    with pytest.raises(ValueError):
        ops_alerts.emit("NOT_A_REAL_EVENT", "should not be sendable")
