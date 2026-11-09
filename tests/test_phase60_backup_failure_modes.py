# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 60 - backup/restore failure modes on a supported interpreter.

Why this module exists
----------------------
Until Phase 5 every restore test in this repository was *skipped* on the sandbox
interpreter (3.11.2 < the 3.11.17 floor), so the failure paths of the encrypted
backup pipeline had never actually been executed. Running them on 3.12.10
proved the happy paths; this module covers the three failure modes that a
production operator will eventually hit and that no happy-path test can cover:

  * **Disk full** - the filesystem refuses a write half-way through a backup or
    a restore. The requirement is *fail closed*: no half-written archive is left
    where an operator could mistake it for a complete backup, no partially
    promoted restore directory is left behind, and the source database is never
    modified.
  * **Permission denied** - the output or target directory is not writable by
    the service user. The error must be raised, not swallowed, and nothing may
    be written into the directory that refused the write.
  * **Damaged / wrong archive** - truncated ciphertext, a flipped byte, or the
    wrong key. Nothing may be decrypted or promoted and the archive itself must
    survive untouched for a later, correct attempt.

How "disk full" is simulated without touching real data
------------------------------------------------------
``RLIMIT_FSIZE`` is set to a small value in a forked child process; every write
past the limit then fails with ``EFBIG`` exactly like a full filesystem fails
with ``ENOSPC``, and ``SIGXFSZ`` is ignored so the process sees an ``OSError``
instead of being killed. The child works only inside the test's ``tmp_path``,
so the simulation cannot touch the repository, the real database, or any vault.
This is the standard, non-destructive way to exercise the ENOSPC path; it does
not fill the host filesystem.

The assertions are made from the *parent* process, on the filesystem state the
child left behind, so a child that dies silently cannot make a test pass.
"""

from __future__ import annotations

import hashlib
import os
import resource
import signal
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import (
    create_encrypted_backup,
    restore_and_verify_backup,
    safe_restore_supported,
)

pytestmark = pytest.mark.skipif(
    not safe_restore_supported(),
    reason="restore requires CPython 3.11.17+ / 3.12.10+",
)

POSIX_ONLY = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="RLIMIT_FSIZE/SIGXFSZ disk-full simulation is POSIX-only",
)

# Small enough that the very first archive write cannot succeed.
TINY_FILE_LIMIT = 4096


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
    return SimpleNamespace(db=db, vault=vault, tmp_path=tmp_path)


def _make_backup(env: SimpleNamespace, name: str = "backup.enc") -> tuple[Path, bytes]:
    key = Fernet.generate_key()
    path = env.tmp_path / "out" / name
    create_encrypted_backup(env.db, env.vault, path, backup_encryption_key=key)
    return path, key


def _run_in_child_with_file_limit(limit: int, action, report_path: Path) -> int:
    """Run ``action`` in a forked child whose writes may not exceed ``limit``.

    Returns the child's exit status: 0 = the action unexpectedly succeeded,
    1 = it raised (the expected, controlled failure; the exception type is
    written to ``report_path``), negative = it died from a signal.

    A raised exception and not a killed process is the point: the pipeline must
    surface the write failure to its caller, not die mid-archive.
    """
    pid = os.fork()
    if pid == 0:  # pragma: no cover - executed in the child
        try:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
            action()
        except BaseException as exc:  # the type is recorded, not swallowed
            try:
                report_path.write_text(f"{type(exc).__module__}.{type(exc).__name__}: {exc}")
            finally:
                os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def _db_is_intact(env: SimpleNamespace) -> bool:
    try:
        with env.db.transaction() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()
        return row["n"] == 1
    except Exception:
        return False


@POSIX_ONLY
def test_disk_full_during_backup_fails_closed(env: SimpleNamespace) -> None:
    target = env.tmp_path / "out_full" / "backup.enc"
    key = Fernet.generate_key()

    report = env.tmp_path / "backup_full_error.txt"
    status = _run_in_child_with_file_limit(
        TINY_FILE_LIMIT,
        lambda: create_encrypted_backup(env.db, env.vault, target, backup_encryption_key=key),
        report,
    )

    assert status == 1, f"expected a raised error, child exited {status}"
    assert report.exists() and report.read_text().strip(), "the write failure was swallowed"
    # Nothing that looks like a usable backup may survive a failed write.
    assert not target.exists(), "a truncated archive was left where an operator would find it"
    leftovers = [p.name for p in target.parent.iterdir()] if target.parent.exists() else []
    assert leftovers == [], f"staging artefacts survived the failure: {leftovers}"
    # And the source deployment is untouched.
    assert _db_is_intact(env)


@POSIX_ONLY
def test_disk_full_during_restore_fails_closed(env: SimpleNamespace) -> None:
    backup, key = _make_backup(env)
    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    restored_dir = env.tmp_path / "restored_full"

    report = env.tmp_path / "restore_full_error.txt"
    status = _run_in_child_with_file_limit(
        TINY_FILE_LIMIT,
        lambda: restore_and_verify_backup(
            backup_path=backup,
            backup_encryption_key=key,
            restore_target_dir=restored_dir,
        ),
        report,
    )

    assert status == 1, f"expected a raised error, child exited {status}"
    assert report.exists() and report.read_text().strip(), "the write failure was swallowed"
    # The archive must survive a failed restore byte-for-byte, otherwise the
    # operator's only copy is destroyed by the attempt to read it.
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == before
    # No promoted deployment may exist: a restore that failed half-way must not
    # leave a directory that a later start-up would treat as complete.
    promoted = restored_dir / "erp.sqlite3"
    assert not promoted.exists(), "a partially written database was left in the restore target"
    assert _db_is_intact(env)


def test_backup_output_directory_permission_denied_is_raised(env: SimpleNamespace) -> None:
    out_dir = env.tmp_path / "out_ro"
    out_dir.mkdir()
    os.chmod(out_dir, 0o500)
    try:
        with pytest.raises(OSError) as excinfo:
            create_encrypted_backup(
                env.db,
                env.vault,
                out_dir / "backup.enc",
                backup_encryption_key=Fernet.generate_key(),
            )
        assert isinstance(excinfo.value, (PermissionError, OSError))
        assert [p.name for p in out_dir.iterdir()] == []
        assert not (out_dir / "backup.enc").exists()
    finally:
        os.chmod(out_dir, 0o700)


def test_restore_target_permission_denied_is_refused(env: SimpleNamespace) -> None:
    backup, key = _make_backup(env)
    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    target = env.tmp_path / "restore_ro"
    target.mkdir()
    os.chmod(target, 0o500)
    try:
        with pytest.raises(OSError):
            restore_and_verify_backup(
                backup_path=backup,
                backup_encryption_key=key,
                restore_target_dir=target / "deploy",
            )
    finally:
        os.chmod(target, 0o700)
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == before
    assert not (target / "deploy" / "erp.sqlite3").exists()
    assert _db_is_intact(env)


def test_truncated_backup_is_refused(env: SimpleNamespace) -> None:
    backup, key = _make_backup(env)
    truncated = env.tmp_path / "truncated.enc"
    truncated.write_bytes(backup.read_bytes()[: len(backup.read_bytes()) // 2])

    with pytest.raises(Exception):
        restore_and_verify_backup(
            backup_path=truncated,
            backup_encryption_key=key,
            restore_target_dir=env.tmp_path / "restore_truncated",
        )
    assert not (env.tmp_path / "restore_truncated" / "erp.sqlite3").exists()
    assert _db_is_intact(env)


def test_corrupted_ciphertext_is_refused(env: SimpleNamespace) -> None:
    backup, key = _make_backup(env)
    payload = bytearray(backup.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    corrupted = env.tmp_path / "corrupted.enc"
    corrupted.write_bytes(bytes(payload))

    with pytest.raises(Exception):
        restore_and_verify_backup(
            backup_path=corrupted,
            backup_encryption_key=key,
            restore_target_dir=env.tmp_path / "restore_corrupt",
        )
    assert not (env.tmp_path / "restore_corrupt" / "erp.sqlite3").exists()
    assert _db_is_intact(env)


def test_wrong_key_is_refused(env: SimpleNamespace) -> None:
    backup, _ = _make_backup(env)
    with pytest.raises(Exception):
        restore_and_verify_backup(
            backup_path=backup,
            backup_encryption_key=Fernet.generate_key(),
            restore_target_dir=env.tmp_path / "restore_wrongkey",
        )
    assert not (env.tmp_path / "restore_wrongkey" / "erp.sqlite3").exists()
    assert _db_is_intact(env)


@POSIX_ONLY
def test_failed_backup_never_leaves_group_or_world_readable_staging(
    env: SimpleNamespace,
) -> None:
    """A failed write must not leave the key-bearing staging directory behind."""
    target = env.tmp_path / "out_mode" / "backup.enc"
    key = Fernet.generate_key()
    status = _run_in_child_with_file_limit(
        TINY_FILE_LIMIT,
        lambda: create_encrypted_backup(env.db, env.vault, target, backup_encryption_key=key),
        env.tmp_path / "mode_error.txt",
    )
    assert status == 1
    suspicious = []
    if target.parent.exists():
        for path in target.parent.iterdir():
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if mode & 0o077 and path.name.startswith(".tmp_backup"):
                suspicious.append((path.name, oct(mode)))
    assert suspicious == []


def test_the_same_backup_succeeds_without_the_file_limit(env: SimpleNamespace) -> None:
    """Control for the disk-full simulation.

    Without the limit the identical call must succeed, otherwise the failures
    above would prove nothing about write exhaustion - only that the fixture was
    broken. This test is what makes the two ``*_full_*`` tests meaningful.
    """
    backup, key = _make_backup(env, name="control.enc")
    assert backup.exists() and backup.stat().st_size > 0
    report = restore_and_verify_backup(
        backup_path=backup,
        backup_encryption_key=key,
        restore_target_dir=env.tmp_path / "restore_control",
    )
    assert report["db_sha256_verified"] is True
