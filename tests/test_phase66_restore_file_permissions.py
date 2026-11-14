# SPDX-License-Identifier: LGPL-3.0-only
"""Release-closure phase 66 — a restore must not leave key material readable.

Found by the recovery drill, by listing what a restore actually wrote: the
restored database and ``manifest.json`` were ``0644`` regardless of the
operator's umask, because the tar ``data`` filter keeps the read bits stored in
the archive. The manifest carries every audit and vault key version.

The key store tightens the restore directory to ``0700`` once the keys are
written, so the exposure was bounded - but the files were readable while the
archive was being extracted and verified, and the documented promotion step
("move the restored files into place") carried ``0644`` onto the live database.

POSIX only: Windows mode bits do not express ACLs (risk S5).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

pytestmark = [
    pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows relies on NTFS ACLs (risk S5)"),
    pytest.mark.skipif(
        not ((3, 11, 17) <= sys.version_info[:3] < (3, 12) or (3, 12, 10) <= sys.version_info[:3] < (3, 13)),
        reason="restore is refused below the security-patched interpreter floor",
    ),
]


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture()
def backup(tmp_path: Path):
    """One encrypted backup of a tiny company that owns one vault document."""
    (tmp_path / "live").mkdir()
    db = ERPDatabase(str(tmp_path / "live" / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "live" / "vault")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, chart_template, created_at)"
            " VALUES (1, 'DZ1', 'Tenant Alpha', 'DZ', 'dz', '2026-01-01')"
        )
    content = b"%PDF-1.4 synthetic"
    vault.store_bytes(1, hashlib.sha256(content).hexdigest(), content)
    key = Fernet.generate_key()
    artefact = tmp_path / "backups" / "one.erpbackup"
    create_encrypted_backup(db, vault, artefact, key)
    return artefact, key


@pytest.fixture()
def permissive_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def test_everything_a_restore_writes_is_owner_only(tmp_path: Path, backup, permissive_umask) -> None:
    artefact, key = backup
    target = tmp_path / "restore"

    restore_and_verify_backup(artefact, key, target)

    assert _mode(target) == 0o700
    exposed = {}
    for root, dirs, files in os.walk(target):
        for name in dirs + files:
            path = Path(root) / name
            if _mode(path) & 0o077:
                exposed[str(path.relative_to(target))] = oct(_mode(path))
    assert exposed == {}
    # The manifest is the reason this matters: it holds the key registries.
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert "audit_keyring_b64" in manifest and "vault_keyring_b64" in manifest
    assert _mode(target / "manifest.json") == 0o600
    assert _mode(target / "erp_snapshot.sqlite3") == 0o600


def test_restoring_into_a_prepared_group_readable_directory_is_private_too(
    tmp_path: Path, backup, permissive_umask
) -> None:
    artefact, key = backup
    target = tmp_path / "prepared"
    target.mkdir()
    os.chmod(target, 0o750)

    restore_and_verify_backup(artefact, key, target)

    assert _mode(target) == 0o700
    assert _mode(target / "manifest.json") == 0o600
    assert _mode(target / "erp_snapshot.sqlite3") == 0o600


def test_the_restored_copy_still_verifies_after_tightening(tmp_path: Path, backup, permissive_umask) -> None:
    artefact, key = backup
    report = restore_and_verify_backup(artefact, key, tmp_path / "restore")

    assert report["db_sha256_verified"] is True
    assert report["vault_files_verified"] == 1
    assert report["audit_chains_valid"] is True
    # A fresh process can reopen it using only what the restore left on disk.
    reopened = ERPDatabase(str(tmp_path / "restore" / "erp_snapshot.sqlite3"))
    assert reopened.verify_audit_chain(1)["valid"] is True


def test_the_backup_staging_directory_is_owner_only(tmp_path: Path, monkeypatch, permissive_umask) -> None:
    """While a backup is assembled its plaintext snapshot and key manifest sit in a staging directory."""
    import dz_erp.security_audit as security_audit

    (tmp_path / "live").mkdir()
    db = ERPDatabase(str(tmp_path / "live" / "erp.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "live" / "vault")
    content = b"%PDF-1.4 synthetic"
    vault.store_bytes(1, hashlib.sha256(content).hexdigest(), content)
    seen = {}
    real_copytree = security_audit.shutil.copytree

    def recording_copytree(src, dst, *args, **kwargs):
        # copytree recurses through this same name; only the outermost call
        # is the copy into the staging directory.
        seen.setdefault("staging_mode", _mode(Path(dst).parent))
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(security_audit.shutil, "copytree", recording_copytree)
    create_encrypted_backup(db, vault, tmp_path / "backups" / "one.erpbackup", Fernet.generate_key())

    assert seen["staging_mode"] == 0o700
    assert list((tmp_path / "backups").glob(".tmp_backup_*")) == []  # and it is removed afterwards
