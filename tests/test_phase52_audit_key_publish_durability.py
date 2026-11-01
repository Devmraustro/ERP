# SPDX-License-Identifier: LGPL-3.0-only
"""R-08 — publishing the audit key must survive a crash, and never split.

Round 2 recorded two residuals around the first key publication and required a
verified fix **and** regression tests before either could close. This module
supplies both for R-08 (the missing directory sync after the key file is linked
into place) and pins the behaviours a reviewer would want before trusting it:

1. the directory that receives the key entry is fsynced, not just the file
   (without the fix, a crash could keep the audit events and lose the key);
2. a second process adopts the published key instead of minting a rival one;
3. a missing key file with audit events on disk fails loudly and mints nothing;
4. an empty key file is never adopted and never overwritten.

R-09 (the ``O_CREAT|O_EXCL`` fallback used only where ``os.link`` is
unavailable) stays open: it is inherent to creating-if-absent without hard
links, its reader-side window is bounded and documented, and closing it would
need a filesystem-specific guarantee this project cannot test.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase, _publish_key_file
from dz_erp.key_provider import KeyMaterialUnavailable


def _key_of(db: ERPDatabase) -> bytes:
    return db._audit_ring.active().secret


def test_r08_the_directory_receiving_the_key_is_fsynced(tmp_path: Path, monkeypatch) -> None:
    """The fix itself: a *directory* fd is fsynced after the key is linked.

    ``os.fsync`` is wrapped rather than replaced, so the real syscalls still
    happen and the real code path runs; the wrapper only records what was synced
    and whether it was a directory.
    """
    if os.name == "nt":  # pragma: no cover - Windows CI job has no directory fsync
        pytest.skip("Windows exposes no portable directory fsync")

    synced: list[tuple[bool, int]] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        try:
            mode = os.fstat(fd).st_mode
        except OSError:
            mode = 0
        synced.append((stat.S_ISDIR(mode), fd))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    key_dir = tmp_path / "vault"
    key_dir.mkdir()
    key_path = key_dir / "audit.key"
    secret = _publish_key_file(key_path, b"k" * 44)

    assert secret == b"k" * 44
    assert any(is_dir for is_dir, _ in synced), (
        "the key file was fsynced but its directory was not: a crash between the "
        f"link and the next directory sync could lose the key (synced={synced})"
    )
    # The file sync must still happen too — the fix is additive.
    assert any(not is_dir for is_dir, _ in synced), synced


def test_r08_a_concurrent_publisher_adopts_the_winner_instead_of_splitting(tmp_path: Path) -> None:
    """The loser of the publish race reads the winner's key: never two keys."""
    key_path = tmp_path / "audit.key"
    first = _publish_key_file(key_path, b"a" * 44)
    second = _publish_key_file(key_path, b"b" * 44)

    assert first == second == b"a" * 44
    assert key_path.read_bytes() == b"a" * 44
    assert not list(tmp_path.glob("*.tmp")), "the temporary key file must be cleaned up"


def test_r08_a_lost_key_with_events_fails_loudly_and_mints_nothing(tmp_path: Path) -> None:
    """R-08's consequence, pinned: availability incident, never silent re-keying."""
    db_path = tmp_path / "erp.sqlite3"
    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Key Co', 'DZ', 'dz', '2026-01-01')"
        )
    key_path = tmp_path / "erp.sqlite3.audit.key"
    assert key_path.exists()

    # A genuine, correctly hash-chained event, so "the chain is valid again"
    # means what it says once the key is restored.
    with db.transaction() as conn:
        db.append_audit_event(
            conn, 1, None, "TEST_EVENT", "test", "1", {"note": "needs the key"}
        )
    original = key_path.read_bytes()
    key_path.unlink()

    from dz_erp.security_audit import AuditKeyUnavailableError

    with pytest.raises(AuditKeyUnavailableError):
        ERPDatabase(str(db_path))
    assert not key_path.exists(), "a lost key must never be replaced by a fresh one"

    key_path.write_bytes(original)
    key_path.chmod(0o600)  # restoring the bytes must also restore custody
    reopened = ERPDatabase(str(db_path))
    assert reopened.verify_audit_chain(1)["valid"] is True


def test_r08_an_empty_key_file_is_neither_adopted_nor_overwritten(tmp_path: Path) -> None:
    key_path = tmp_path / "empty.key"
    key_path.write_bytes(b"")
    key_path.chmod(0o600)  # custody is checked before content, by design

    with pytest.raises(KeyMaterialUnavailable):
        _publish_key_file(key_path, b"z" * 44)
    assert key_path.read_bytes() == b"", "an unreadable key must not be overwritten"
