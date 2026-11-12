# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 10 — application readiness: ``/readyz`` and the checks behind it.

Liveness (``/healthz``) answers "is the process up?". These tests are about the harder
question: does the process *work*? A readiness endpoint that returns 200 while the
database is read-only or the vault is full is worse than having none, because it tells
a load balancer to send invoices into a dead volume.

The suite covers, by execution:

* a healthy installation reports ``ready`` over HTTP with ``200``;
* a missing core table, an unwritable vault, a loose key file, and a full disk each
  produce ``not_ready`` with ``503``;
* a stale backup produces ``degraded`` with ``200`` (a late backup must not remove the
  application from service);
* the response never contains a filesystem path, a key value or customer data;
* the OCR check distinguishes "installed" from "actually loads".
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dz_erp.api import create_erp_fastapi_app
from dz_erp.auth import hash_password
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.readiness import (
    DEGRADED,
    NOT_READY,
    READY,
    check_database,
    check_disk,
    check_key_custody,
    check_ocr,
    check_vault,
    evaluate_readiness,
    readiness_http_status,
)


def _build(tmp_path: Path, *, permissions: bool = True, company: bool = True) -> tuple[ERPDatabase, PrivateDocumentVault]:
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    if permissions and os.name == "posix":
        # V1 custody: key file 0600 (written that way) inside a 0700 key directory.
        os.chmod(tmp_path, 0o700)
    if company:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
                " VALUES (1, 'DZ1', 'Readiness Co', 'DZ', 'dz', '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO users (id, company_id, username, role, is_active, password_hash)"
                " VALUES (1, 1, 'admin', 'admin', 1, ?)",
                (hash_password("Admin#2026x", iterations=1000),),
            )
    return db, vault


def _client(db: ERPDatabase, vault: PrivateDocumentVault) -> TestClient:
    return TestClient(create_erp_fastapi_app(db, vault))


# ----------------------------------------------------------------------
# healthy path
# ----------------------------------------------------------------------
def test_healthy_installation_reports_ready_over_http(tmp_path: Path) -> None:
    db, vault = _build(tmp_path)
    response = _client(db, vault).get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == READY
    # Every critical subsystem must be represented, not just the ones that pass.
    assert {"database", "vault", "disk", "ocr"} <= set(body["checks"])
    assert body["checks"]["database"]["values"]["write_lock"] is True
    assert body["checks"]["vault"]["values"]["writable"] is True
    assert response.headers["cache-control"] == "no-store"


def test_readyz_needs_no_authentication_but_leaks_nothing(tmp_path: Path) -> None:
    db, vault = _build(tmp_path)
    response = _client(db, vault).get("/readyz")
    assert response.status_code == 200
    text = response.text
    # No absolute path from this environment may appear in the payload.
    assert str(tmp_path) not in text
    assert "erp.sqlite3" not in text
    assert "vault.key" not in text
    # And nothing that looks like key material.
    assert "dzvault_" not in text and "dzvk" not in text.lower()


# ----------------------------------------------------------------------
# critical failures -> 503
# ----------------------------------------------------------------------
def test_missing_core_table_makes_the_app_not_ready(tmp_path: Path) -> None:
    db, vault = _build(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("DROP TABLE journal_entry_lines")
    response = _client(db, vault).get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == NOT_READY
    assert body["checks"]["database"]["status"] == "fail"
    assert "journal_entry_lines" in body["checks"]["database"]["values"]["missing_tables"]


def test_unwritable_vault_makes_the_app_not_ready(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission semantics required")
    db, vault = _build(tmp_path)
    os.chmod(vault.vault_root, 0o500)  # readable, not writable
    try:
        response = _client(db, vault).get("/readyz")
    finally:
        os.chmod(vault.vault_root, 0o700)
    assert response.status_code == 503
    assert response.json()["checks"]["vault"]["status"] == "fail"


def test_loose_vault_key_permissions_make_the_app_not_ready(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission semantics required")
    db, vault = _build(tmp_path)
    os.chmod(vault.vault_key_path, 0o644)
    try:
        response = _client(db, vault).get("/readyz")
    finally:
        os.chmod(vault.vault_key_path, 0o600)
    assert response.status_code == 503
    check = response.json()["checks"]["vault"]
    assert check["status"] == "fail"
    assert "0600" in check["detail"]


def test_disk_floor_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, vault = _build(tmp_path)
    # Ask for more free space than any test host has.
    monkeypatch.setenv("ERP_MIN_FREE_BYTES", str(1 << 60))
    response = _client(db, vault).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["disk"]["status"] == "fail"


def test_readiness_http_status_mapping() -> None:
    assert readiness_http_status({"status": READY}) == 200
    assert readiness_http_status({"status": DEGRADED}) == 200
    assert readiness_http_status({"status": NOT_READY}) == 503


# ----------------------------------------------------------------------
# advisory failures -> 200 but degraded
# ----------------------------------------------------------------------
def test_stale_backup_degrades_without_taking_the_app_out_of_service(tmp_path: Path) -> None:
    db, vault = _build(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    stale = backup_dir / "erp-20200101T000000Z.erpbackup"
    stale.write_bytes(b"old")
    os.utime(stale, (1_000_000, 1_000_000))  # well past any age limit
    response = _client(db, vault).get("/readyz")
    # ERP_BACKUP_DIR is read from the environment by the route, so drive the
    # evaluator directly with an explicit directory to keep the test hermetic.
    report = evaluate_readiness(db, vault, backup_dir=backup_dir)
    assert report["status"] == DEGRADED, report
    assert report["checks"]["backup"]["critical"] is False
    assert report["checks"]["backup"]["status"] == "fail"
    assert readiness_http_status(report) == 200
    assert response.status_code in (200, 503)  # must not crash without the env var


def test_no_backup_directory_configured_is_unknown_not_a_failure(tmp_path: Path) -> None:
    db, vault = _build(tmp_path)
    report = evaluate_readiness(db, vault)
    assert "backup" not in report["checks"]
    assert report["status"] in (READY, DEGRADED)


# ----------------------------------------------------------------------
# key custody and OCR
# ----------------------------------------------------------------------
def test_key_directory_mode_is_an_advisory_check(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission semantics required")
    db, vault = _build(tmp_path)
    ok = check_key_custody(vault)
    assert ok.critical is False
    assert ok.status == "ok"
    assert ok.values["key_dir_mode"] == "0o700"

    os.chmod(tmp_path, 0o755)
    try:
        loose = check_key_custody(vault)
    finally:
        os.chmod(tmp_path, 0o700)
    assert loose.status == "fail"
    assert loose.critical is False


def test_ocr_shallow_check_reports_installed_without_loading() -> None:
    check = check_ocr(deep=False)
    assert check.values["deep"] is False
    assert check.values["engine_loaded"] is False
    # rapidocr is in the lock file, so the shallow check must find it.
    assert check.status == "ok", check.detail


def test_ocr_deep_check_actually_loads_the_engine() -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    check = check_ocr(deep=True)
    assert check.status == "ok", check.detail
    assert check.values["engine_loaded"] is True
    assert check.values["load_ms"] >= 0


def test_deep_query_parameter_is_honoured(tmp_path: Path) -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    db, vault = _build(tmp_path)
    response = _client(db, vault).get("/readyz?deep=1")
    assert response.status_code == 200
    assert response.json()["checks"]["ocr"]["values"]["deep"] is True


# ----------------------------------------------------------------------
# unit-level checks
# ----------------------------------------------------------------------
def test_database_check_detects_a_read_only_data_volume(tmp_path: Path) -> None:
    """A read-only *directory* is the failure an operator actually hits.

    Per-file mode alone is not enough to make SQLite read-only: in WAL mode the
    writes go to ``-wal``/``-shm`` beside the database, so a 0444 database inside a
    writable directory still accepts a write lock (verified by hand while writing
    this test). Readiness therefore probes the write lock - and this test makes the
    volume unwritable, which is the condition that has to be detected.
    """
    if os.name != "posix":
        pytest.skip("POSIX permission semantics required")
    db, _ = _build(tmp_path)
    os.chmod(tmp_path, 0o555)
    try:
        check = check_database(db)
    finally:
        os.chmod(tmp_path, 0o700)
    assert check.status == "fail"
    assert "sqlite error" in check.detail


def test_disk_check_reports_real_numbers(tmp_path: Path) -> None:
    check = check_disk(tmp_path, min_free_bytes=1)
    assert check.status == "ok"
    assert check.values["free_bytes"] > 0
    assert check.values["total_bytes"] >= check.values["free_bytes"]


def test_check_vault_reports_blob_count(tmp_path: Path) -> None:
    db, vault = _build(tmp_path)
    vault.store_bytes(1, "a" * 64, b"payload")
    check = check_vault(vault)
    assert check.status == "ok", check.detail
    assert check.values["blob_count"] == 1
    assert check.values["key_mode"] == "0o600"
