"""Workstream D - durable security logging without invented tenant attribution.

``audit_events.company_id`` is ``NOT NULL`` and tenant-scoped, so a request that
failed *authentication* cannot be written there: the caller never proved who
they are, which is exactly why it was refused.  Attributing it anyway would
either blame the wrong tenant or fabricate an attribution that never existed.

``JsonlSecurityLog`` is the durable sink that records those rejections with no
company identifier at all.  Without it, ``AUTH_REQUEST_REJECTED`` went to an
in-memory list and every record was lost on restart - so a credential-stuffing
run left no durable trace.
"""

from __future__ import annotations

import json
import stat
import threading
from typing import Any, Dict, List

import pytest

from dz_erp.security_audit import JsonlSecurityLog, StructuredRedactingLogger


def test_rejections_are_written_to_a_durable_file_with_no_tenant_attribution(tmp_path, monkeypatch):
    """The whole point: the record must outlive the process and invent no company."""
    monkeypatch.setenv("ERP_SECURITY_LOG_PATH", str(tmp_path / "security.jsonl"))

    from dz_erp.api import create_erp_fastapi_app
    from dz_erp.database import ERPDatabase
    from dz_erp.intake_ocr import PrivateDocumentVault
    from fastapi.testclient import TestClient

    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    app = create_erp_fastapi_app(db, PrivateDocumentVault(tmp_path / "vault"))
    client = TestClient(app)

    rejections = [
        {},
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Basic zm9vOmJhcg=="},
        {"Authorization": "bearer"},
    ]
    for headers in rejections:
        client.get("/api/v1/auth/session", headers=headers)

    log_file = tmp_path / "security.jsonl"
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]

    assert len(records) == len(rejections)
    assert all(r["event"] == "AUTH_REQUEST_REJECTED" for r in records)
    # One correlation id per rejection: the record is individually traceable.
    assert len({r["correlation_id"] for r in records}) == len(records)
    # No tenant identifier anywhere - not even a null one.
    for record in records:
        assert "company_id" not in json.dumps(record)
        assert record["context"]["path"] == "/api/v1/auth/session"
        assert "peer_ip" in record["context"]
    # The presented credential must never reach the log.
    assert "not-a-real-token" not in log_file.read_text()
    # Owner-only from creation, never tightened after the fact.
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600


def test_a_failing_sink_is_not_swallowed():
    """A silent logging failure is indistinguishable from no logging at all."""
    def boom(_entry):
        raise OSError("disk full")

    logger = StructuredRedactingLogger(sink=boom)
    with pytest.raises(OSError, match="disk full"):
        logger.log("WARNING", "AUTH_REQUEST_REJECTED", "cid", code="X")
    # The in-memory record is still kept, so nothing is lost twice over.
    assert len(logger.records) == 1


def test_a_short_write_is_detected(tmp_path, monkeypatch):
    """A truncated record would corrupt the log without failing."""
    sink = JsonlSecurityLog(tmp_path / "s.jsonl")
    monkeypatch.setattr("dz_erp.security_audit.os.write", lambda fd, data: len(data) - 5)
    with pytest.raises(OSError, match="short"):
        sink({"event": "AUTH_REQUEST_REJECTED"})


def test_concurrent_writers_do_not_lose_or_interleave_records(tmp_path):
    """O_APPEND plus sub-PIPE_BUF lines: 400 records in, 400 valid records out."""
    sink = JsonlSecurityLog(tmp_path / "c.jsonl")

    def worker(worker_id: int) -> None:
        for i in range(50):
            sink({"event": "AUTH_REQUEST_REJECTED", "worker": worker_id, "i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = [line for line in (tmp_path / "c.jsonl").read_text().splitlines() if line.strip()]
    assert len(lines) == 400, f"lost {400 - len(lines)} records"
    assert all(json.loads(line) for line in lines), "a record was interleaved or truncated"


def test_redaction_is_applied_before_the_sink_sees_the_record():
    seen: List[Dict[str, Any]] = []
    logger = StructuredRedactingLogger(sink=seen.append)
    logger.log("WARNING", "X", "c", authorization="Bearer supersecrettoken")
    assert "supersecrettoken" not in json.dumps(seen[0])


def test_no_sink_configured_means_in_memory_only():
    """The default is the test configuration and must stay side-effect free."""
    logger = StructuredRedactingLogger()
    logger.log("WARNING", "X", "c")
    assert len(logger.records) == 1
