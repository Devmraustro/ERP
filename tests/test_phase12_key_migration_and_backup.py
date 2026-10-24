# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 12 — Upgrade, rollback, backup and restore safety for key versioning.

The question these tests answer is the one that decides whether the migration
may be run at all: *after this change, can an existing deployment still read
everything it could read before, restore from the backups it already has, and
come back from a failed rotation without losing a single record?*

The representative legacy fixture is built the way a real pre-change deployment
looks: schema without ``hmac_key_version``, a single ``.audit.key`` file, a
single ``.vault.key`` file, and vault blobs stored as raw Fernet tokens.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet, InvalidToken

from dz_erp.database import (
    AUDIT_KEY_SUFFIX,
    AUDIT_KEYRING_SUFFIX,
    ERPDatabase,
    FinancialIntegrityError,
)
import dz_erp.security_audit as security_audit_module
from dz_erp.intake_ocr import VAULT_KEYRING_SUFFIX, PrivateDocumentVault
from dz_erp.key_rotation import BLOB_MAGIC, KeyConfigurationError, KeyRing
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup

LEGACY_DOCS = {
    "one": b"LEGACY SYNTHETIC INVOICE ONE",
    "two": b"LEGACY SYNTHETIC INVOICE TWO",
    "three": b"LEGACY SYNTHETIC INVOICE THREE",
}


def _legacy_sha(tag: str) -> str:
    return (tag.encode().hex() * 64)[:64]


def _build_legacy_deployment(root: Path) -> dict:
    """Create the artefacts a pre-key-versioning deployment would have on disk."""
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "erp.sqlite3"
    vault_root = root / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    db = ERPDatabase(str(db_path))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Legacy Synthetic Company', 'DZ', '2026-01-01')"
        )
    for index in range(3):
        with db.transaction() as conn:
            db.append_audit_event(
                conn,
                company_id=1,
                actor_user_id=None,
                action_type=f"LEGACY_EVENT_{index}",
                target_model="legacy",
                target_id=str(index),
                after_state={"index": index},
            )

    legacy_vault_key = Fernet.generate_key()
    vault_paths = {}
    fernet = Fernet(legacy_vault_key)
    for tag, content in LEGACY_DOCS.items():
        dest = vault_root / "company_1" / f"{_legacy_sha(tag)}.enc"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fernet.encrypt(content))  # raw Fernet token: no header
        vault_paths[tag] = dest

    # Rewind the database to the pre-versioning schema and write the single key
    # file the old build created.
    audit_key = db._hmac_secret
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("ALTER TABLE audit_events DROP COLUMN hmac_key_version")
        conn.commit()
    finally:
        conn.close()
    # The pre-versioning code created both files via os.open(..., 0o600); a real
    # legacy deployment therefore has owner-only keys, and the fixture must
    # reproduce that rather than a laxer state that never shipped.
    for name, material in (
        (("erp.sqlite3" + AUDIT_KEY_SUFFIX), audit_key),
        (("vault" + ".vault.key"), legacy_vault_key),
    ):
        fd = os.open(str(root / name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(material)

    assert not (root / ("erp.sqlite3" + AUDIT_KEYRING_SUFFIX)).exists()
    assert not (root / ("vault" + VAULT_KEYRING_SUFFIX)).exists()
    return {
        "root": root,
        "db_path": db_path,
        "vault_root": vault_root,
        "vault_paths": vault_paths,
        "audit_key": audit_key,
        "vault_key": legacy_vault_key,
    }


def _snapshot(paths: list) -> dict:
    return {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


# ----------------------------------------------------------------------
# 1. Upgrade in place
# ----------------------------------------------------------------------
def test_legacy_deployment_upgrades_without_rewriting_anything(tmp_path: Path) -> None:
    legacy = _build_legacy_deployment(tmp_path / "legacy")
    vault_root = legacy["vault_root"]
    before = _snapshot([*legacy["vault_paths"].values(), legacy["db_path"]])

    # Open with the new build, exactly as a restart would.
    db = ERPDatabase(str(legacy["db_path"]))
    vault = PrivateDocumentVault(vault_root)

    assert db.audit_events_by_key_version() == {1: 3}
    assert db.verify_audit_chain(1)["valid"] is True
    assert db.audit_keyring.active_version == 1
    assert vault.active_key_version == 1
    assert vault.scan_blob_versions() == {1: 3}
    for tag, content in LEGACY_DOCS.items():
        assert vault.read_bytes(1, 1, str(legacy["vault_paths"][tag])) == content

    # Nothing was re-encrypted and no registry was created merely by starting up.
    assert _snapshot(list(legacy["vault_paths"].values())) == {
        str(p): before[str(p)] for p in legacy["vault_paths"].values()
    }
    assert not (tmp_path / "legacy" / ("erp.sqlite3" + AUDIT_KEYRING_SUFFIX)).exists()
    assert not (tmp_path / "legacy" / ("vault" + VAULT_KEYRING_SUFFIX)).exists()

    # The audit table gains one column and keeps every row and hash intact.
    conn = sqlite3.connect(str(legacy["db_path"]))
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(audit_events)")}
        assert "hmac_key_version" in columns
        assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 3
    finally:
        conn.close()

    # Upgrading twice is a no-op.
    again = ERPDatabase(str(legacy["db_path"]))
    assert again.audit_events_by_key_version() == {1: 3}
    assert again.verify_audit_chain(1)["valid"] is True


def test_empty_database_and_empty_vault_start_cleanly(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "fresh.sqlite3"))
    vault = PrivateDocumentVault(tmp_path / "fresh_vault")

    assert db.audit_events_by_key_version() == {}
    assert db.audit_keyring.active_version == 1
    assert vault.active_key_version == 1
    assert vault.scan_blob_versions() == {}
    assert vault.verify_blobs() == {"checked": 0, "failed": 0, "by_version": {}, "errors": []}

    # Rotation on a brand-new store is well-defined, not a special case.
    assert vault.rotate_key()["verified_ok"] is True
    assert db.rotate_audit_key()["chain_valid_after"] is True


def test_legacy_deployment_can_rotate_both_keys_and_keep_reading(tmp_path: Path) -> None:
    legacy = _build_legacy_deployment(tmp_path / "legacy")
    db = ERPDatabase(str(legacy["db_path"]))
    vault = PrivateDocumentVault(legacy["vault_root"])

    vault_report = vault.rotate_key()
    audit_report = db.rotate_audit_key()

    assert vault_report["completed"] == 3 and vault_report["verified_ok"] is True
    assert audit_report["chain_valid_after"] is True

    # New events are signed with the new version; the old chain still verifies.
    with db.transaction() as conn:
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=None,
            action_type="POST_ROTATION_EVENT",
            target_model="legacy",
            target_id="99",
            after_state={"rotated": True},
        )
    assert db.audit_events_by_key_version() == {1: 3, 2: 1}
    assert db.verify_audit_chain(1)["valid"] is True

    # Old and new documents are both readable.
    assert vault.read_bytes(1, 1, str(legacy["vault_paths"]["one"])) == LEGACY_DOCS["one"]
    fresh = vault.store_bytes(1, "f" * 64, b"POST ROTATION DOCUMENT")
    assert vault.read_bytes(1, 1, fresh) == b"POST ROTATION DOCUMENT"


# ----------------------------------------------------------------------
# 2. Rollback
# ----------------------------------------------------------------------
def test_vault_rollback_restores_the_previous_active_key_safely(tmp_path: Path) -> None:
    vault = PrivateDocumentVault(tmp_path / "vault")
    stored = vault.store_bytes(1, "a" * 64, b"SYNTHETIC")
    vault.rotate_key()
    rotated = vault.store_bytes(1, "b" * 64, b"ROTATED")

    rollback = vault.set_active_key_version(1)

    assert rollback["active_version"] == 1
    assert rollback["retained_versions"] == [1, 2]
    # Both existing documents were written (or re-encrypted) under version 2.
    assert rollback["blobs_by_version"] == {2: 2}
    assert rollback["verified"]["failed"] == 0
    # Both the pre-rotation and post-rotation documents remain readable.
    assert vault.read_bytes(1, 1, stored) == b"SYNTHETIC"
    assert vault.read_bytes(1, 1, rotated) == b"ROTATED"
    # New writes go back to version 1 without touching the version-2 blob.
    assert vault.store_bytes(1, "c" * 64, b"AFTER ROLLBACK").endswith(".enc")
    assert vault.scan_blob_versions() == {1: 1, 2: 2}


def test_audit_rollback_never_rewrites_history(tmp_path: Path) -> None:
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=None,
            action_type="BEFORE",
            target_model="synthetic",
            target_id="1",
            after_state={"n": 1},
        )
    db.rotate_audit_key()
    conn = sqlite3.connect(str(tmp_path / "erp.sqlite3"))
    try:
        hashes_before = [r[0] for r in conn.execute("SELECT event_hash FROM audit_events ORDER BY id")]
    finally:
        conn.close()

    assert db.set_active_audit_key_version(1)["chain_valid"] is True
    conn = sqlite3.connect(str(tmp_path / "erp.sqlite3"))
    try:
        hashes_after = [r[0] for r in conn.execute("SELECT event_hash FROM audit_events ORDER BY id")]
    finally:
        conn.close()
    assert hashes_before == hashes_after


# ----------------------------------------------------------------------
# 3. Backup metadata and restore
# ----------------------------------------------------------------------
def _deployment_with_partial_rotation(root: Path) -> dict:
    """A store where old and new ciphertext coexist - the realistic restore case."""
    root.mkdir(parents=True, exist_ok=True)
    db = ERPDatabase(str(root / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
    with db.transaction() as conn:
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=None,
            action_type="PRE_ROTATION",
            target_model="synthetic",
            target_id="1",
            after_state={"n": 1},
        )

    vault = PrivateDocumentVault(root / "vault")
    paths = {tag: vault.store_bytes(1, _legacy_sha(tag), content) for tag, content in LEGACY_DOCS.items()}

    # Interrupt the vault rotation after the first record, then rotate the audit
    # key fully: the archive then needs *both* versions of both keys.
    original = PrivateDocumentVault._rotate_one
    calls = {"n": 0}

    def fail_after_one(self, path, target_version, max_retries, journal, report):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("simulated interruption")
        return original(self, path, target_version, max_retries, journal, report)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(PrivateDocumentVault, "_rotate_one", fail_after_one)
    with pytest.raises(RuntimeError):
        vault.rotate_key()
    monkeypatch.undo()
    db.rotate_audit_key()
    with db.transaction() as conn:
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=None,
            action_type="POST_ROTATION",
            target_model="synthetic",
            target_id="2",
            after_state={"n": 2},
        )
    return {"db": db, "vault": vault, "paths": paths, "root": root}


def test_backup_metadata_names_every_required_key_version_without_material(
    tmp_path: Path,
) -> None:
    deployment = _deployment_with_partial_rotation(tmp_path / "src")
    db, vault = deployment["db"], deployment["vault"]
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backups" / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )

    assert [entry["version"] for entry in meta["vault_key_versions"]] == [1, 2]
    assert [entry["version"] for entry in meta["audit_key_versions"]] == [1, 2]

    serialised = json.dumps(meta)
    for version in (1, 2):
        assert vault.keyring.material_for(version).decode() not in serialised
        assert db.audit_keyring.material_for(version).hex() not in serialised
    assert "material_b64" not in serialised
    assert "keyring" not in serialised

    # Inside the encrypted archive the manifest does carry the registries -
    # that is what makes the restore complete - but only under the archive's
    # own encryption, never in the clear.
    archive = Fernet(vault.encryption_key).decrypt(Path(meta["backup_path"]).read_bytes())
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
    assert manifest["vault_key_versions"]["active_version"] == 2
    assert manifest["audit_key_versions"]["active_version"] == 2
    assert "vault_keyring_b64" in manifest and "audit_keyring_b64" in manifest
    assert manifest["db_sha256"] == meta["db_sha256"]


def test_backup_restores_mixed_ciphertext_into_an_isolated_environment(tmp_path: Path) -> None:
    deployment = _deployment_with_partial_rotation(tmp_path / "src")
    db, vault, paths = deployment["db"], deployment["vault"], deployment["paths"]
    # Only the ciphertext is compared byte-for-byte. SQLite legitimately rewrites
    # its own file header while serving an online backup, so comparing database
    # *file* bytes would test the storage engine, not isolation; the source
    # records are checked logically below instead.
    source_snapshot = _snapshot(list(paths.values()))

    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "backups" / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )
    report = restore_and_verify_backup(
        backup_path=Path(meta["backup_path"]),
        backup_encryption_key=vault.encryption_key,
        restore_target_dir=tmp_path / "isolated",
    )

    assert report["db_sha256_verified"] is True
    assert report["vault_files_verified"] == 3
    assert report["audit_chains_valid"] is True

    restored_vault = report["restored_vault"]
    assert restored_vault.keyring.versions == [1, 2]
    for tag, content in LEGACY_DOCS.items():
        assert restored_vault.read_bytes(1, 1, paths[tag]) == content
    restored_db = report["restored_db"]
    assert restored_db.audit_keyring.versions == [1, 2]
    assert restored_db.audit_events_by_key_version() == {1: 1, 2: 1}
    assert restored_db.verify_audit_chain(1)["valid"] is True

    # The restore is isolated: no source blob changed, and the source data is intact.
    assert _snapshot(list(paths.values())) == source_snapshot
    assert db.verify_audit_chain(1)["valid"] is True
    assert db.audit_events_by_key_version() == {1: 1, 2: 1}

    # The restored artifacts reopen with no secrets supplied, in a new object.
    out = tmp_path / "isolated"
    reopened_vault = PrivateDocumentVault(out / "vault")
    assert reopened_vault.read_bytes(1, 1, paths["one"]) == LEGACY_DOCS["one"]
    reopened_db = ERPDatabase(str(out / "erp_snapshot.sqlite3"))
    assert reopened_db.verify_audit_chain(1)["valid"] is True
    assert reopened_db.audit_keyring.versions == [1, 2]


def test_restore_of_a_tampered_registry_fails_closed(tmp_path: Path) -> None:
    deployment = _deployment_with_partial_rotation(tmp_path / "src")
    db, vault = deployment["db"], deployment["vault"]
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )

    # Rebuild the archive with a registry whose active version points at a key
    # that is not present: the restore must refuse rather than silently drop
    # the historical version.
    key = vault.encryption_key
    archive = Fernet(key).decrypt(Path(meta["backup_path"]).read_bytes())
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    manifest = json.loads(members["manifest.json"])
    ring = json.loads(base64.b64decode(manifest["vault_keyring_b64"]))
    ring["current_version"] = 42
    manifest["vault_keyring_b64"] = base64.b64encode(json.dumps(ring).encode()).decode()
    members["manifest.json"] = json.dumps(manifest).encode()

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    tampered = tmp_path / "tampered.enc"
    tampered.write_bytes(Fernet(key).encrypt(out.getvalue()))

    with pytest.raises(KeyConfigurationError, match="no key entry"):
        restore_and_verify_backup(
            backup_path=tampered,
            backup_encryption_key=key,
            restore_target_dir=tmp_path / "should-not-matter",
        )


def test_restore_rejects_ciphertext_that_needs_an_absent_key(tmp_path: Path) -> None:
    """A registry stripped of a version must not 'succeed' by dropping records."""
    deployment = _deployment_with_partial_rotation(tmp_path / "src")
    db, vault = deployment["db"], deployment["vault"]
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )

    key = vault.encryption_key
    archive = Fernet(key).decrypt(Path(meta["backup_path"]).read_bytes())
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    manifest = json.loads(members["manifest.json"])
    ring = json.loads(base64.b64decode(manifest["vault_keyring_b64"]))
    del ring["keys"]["1"]
    manifest["vault_keyring_b64"] = base64.b64encode(json.dumps(ring).encode()).decode()
    members["manifest.json"] = json.dumps(manifest).encode()

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    stripped = tmp_path / "stripped.enc"
    stripped.write_bytes(Fernet(key).encrypt(out.getvalue()))

    # The vault registry was the one stripped, so this is the vault's own
    # "key unavailable" class - not a decryption error and not a tamper report.
    from dz_erp.intake_ocr import VaultKeyUnavailableError

    with pytest.raises(VaultKeyUnavailableError, match=r"version\(s\) \[1\]"):
        restore_and_verify_backup(
            backup_path=stripped,
            backup_encryption_key=key,
            restore_target_dir=tmp_path / "restored",
        )


# ----------------------------------------------------------------------
# 4. Migration must never discard a record
# ----------------------------------------------------------------------
def test_rotation_never_discards_or_rewrites_an_undecryptable_record(tmp_path: Path) -> None:
    vault = PrivateDocumentVault(tmp_path / "vault")
    good = Path(vault.store_bytes(1, "a" * 64, b"GOOD DOCUMENT"))
    corrupt = Path(vault.store_bytes(1, "b" * 64, b"SOON TO BE CORRUPT"))
    raw = bytearray(corrupt.read_bytes())
    raw[-3] ^= 0xFF
    corrupt.write_bytes(bytes(raw))
    corrupt_snapshot = corrupt.read_bytes()

    report = vault.rotate_key()

    assert report["completed"] == 1
    assert report["failed"] == 1
    assert report["errors"][0]["blob"] == str(corrupt.relative_to(vault.vault_root))
    assert report["errors"][0]["reason"] == "InvalidToken"
    # The unreadable record is still on disk, byte-identical, and still reported.
    assert corrupt.exists()
    assert corrupt.read_bytes() == corrupt_snapshot
    verification = vault.verify_blobs()
    assert verification["checked"] == 1 and verification["failed"] == 1
    assert vault.read_bytes(1, 1, str(good)) == b"GOOD DOCUMENT"


def test_rotation_does_not_touch_files_outside_the_vault(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault = PrivateDocumentVault(root)
    vault.store_bytes(1, "a" * 64, b"DOC")
    outside = tmp_path / "unrelated.enc"
    outside.write_bytes(b"NOT A VAULT BLOB")
    snapshot = outside.read_bytes()

    vault.rotate_key()

    assert outside.read_bytes() == snapshot
    assert not list(root.rglob("*.tmp")), "a temporary file was left behind"
    assert not list(root.parent.glob(".*.tmp"))


def test_corrupt_blob_header_does_not_prevent_startup_or_rotation(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault = PrivateDocumentVault(root)
    vault.store_bytes(1, "a" * 64, b"DOC")
    broken = root / "company_1" / f"{'9' * 64}.enc"
    broken.write_bytes(BLOB_MAGIC)

    # Start-up is not blocked by a structurally broken blob...
    restarted = PrivateDocumentVault(root)
    assert restarted.verify_blobs()["failed"] == 1
    # ...and rotation reports it without removing it.
    report = restarted.rotate_key()
    assert report["failed"] == 1
    assert broken.exists()




@pytest.mark.parametrize(
    "runtime",
    [(3, 11, 16), (3, 12, 14), (3, 13, 0)],
)
def test_restore_refuses_unpatched_python_tarfile_runtime(tmp_path: Path, monkeypatch, runtime) -> None:
    """A data filter existing on disk is not enough if its stdlib bypasses are unpatched."""
    monkeypatch.setattr(security_audit_module, "sys", SimpleNamespace(version_info=runtime))
    target = tmp_path / "restore-target"

    with pytest.raises(FinancialIntegrityError, match="security-patched Python runtime"):
        security_audit_module.restore_and_verify_backup(
            backup_path=tmp_path / "not-read.enc",
            backup_encryption_key=Fernet.generate_key(),
            restore_target_dir=target,
        )

    assert not target.exists(), "runtime validation must happen before any restore filesystem changes"


def test_restore_rejects_tar_symlink_escape(tmp_path: Path) -> None:
    """A safe-looking member name must not redirect a later write outside restore_target_dir."""
    key = Fernet.generate_key()
    archive = io.BytesIO()
    payload = b"SHOULD NEVER BE WRITTEN OUTSIDE THE RESTORE DIRECTORY"
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        manifest = tarfile.TarInfo("manifest.json")
        manifest.size = 2
        tar.addfile(manifest, io.BytesIO(b"{}"))

        vault_dir = tarfile.TarInfo("vault")
        vault_dir.type = tarfile.DIRTYPE
        tar.addfile(vault_dir)

        escape = tarfile.TarInfo("vault/escape")
        escape.type = tarfile.SYMTYPE
        escape.linkname = "../../outside"
        tar.addfile(escape)

        child = tarfile.TarInfo("vault/escape/payload.txt")
        child.size = len(payload)
        tar.addfile(child, io.BytesIO(payload))

    backup = tmp_path / "unsafe-symlink-backup.enc"
    backup.write_bytes(Fernet(key).encrypt(archive.getvalue()))
    restore_target = tmp_path / "restore-target"

    with pytest.raises(FinancialIntegrityError, match="unsafe|invalid|refused"):
        restore_and_verify_backup(
            backup_path=backup,
            backup_encryption_key=key,
            restore_target_dir=restore_target,
        )

    assert not (tmp_path / "outside" / "payload.txt").exists()
    assert not restore_target.exists(), "failed preflight must not leave a partial restore directory"

def test_restore_requires_a_backup_key_and_does_not_leak_on_failure(tmp_path: Path) -> None:
    deployment = _deployment_with_partial_rotation(tmp_path / "src")
    db, vault = deployment["db"], deployment["vault"]
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )
    target = tmp_path / "isolated_failed"

    with pytest.raises(InvalidToken):
        restore_and_verify_backup(
            backup_path=Path(meta["backup_path"]),
            backup_encryption_key=Fernet.generate_key(),
            restore_target_dir=target,
        )
    # A failed restore must not leave a usable-looking mixture behind.
    assert not (target / "manifest.json").exists()
    assert not (target / "erp_snapshot.sqlite3").exists()


def test_restore_with_a_contradicting_audit_key_is_refused(tmp_path: Path) -> None:
    """An operator-supplied key that contradicts the archive is an error, not a preference."""
    deployment = _deployment_with_partial_rotation(tmp_path / "src")
    db, vault = deployment["db"], deployment["vault"]
    meta = create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=tmp_path / "erp.enc",
        backup_encryption_key=vault.encryption_key,
    )

    with pytest.raises(KeyConfigurationError, match="not the active key recorded in the backup"):
        restore_and_verify_backup(
            backup_path=Path(meta["backup_path"]),
            backup_encryption_key=vault.encryption_key,
            restore_target_dir=tmp_path / "refused",
            hmac_secret=b"an-unrelated-audit-secret-of-sufficient-length",
        )


# ----------------------------------------------------------------------
# 5. The operator command line
# ----------------------------------------------------------------------
def _cli(*args: str) -> tuple:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "dz_erp.rotate_keys", *args],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=300,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    return proc.returncode, payload, proc.stderr


def test_operator_cli_reports_status_and_honours_dry_run(tmp_path: Path) -> None:
    vault = PrivateDocumentVault(tmp_path / "vault")
    stored = vault.store_bytes(1, "a" * 64, b"SYNTHETIC DOCUMENT")
    before = Path(stored).read_bytes()
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        db.append_audit_event(
            conn,
            company_id=1,
            actor_user_id=None,
            action_type="CLI_TEST",
            target_model="synthetic",
            target_id="1",
            after_state={"n": 1},
        )

    code, status, _err = _cli("status", "--db", str(tmp_path / "erp.sqlite3"), "--vault", str(tmp_path / "vault"))
    assert code == 0
    assert status["vault"]["active_version"] == 1
    assert status["vault"]["verification"]["failed"] == 0
    assert status["audit"]["chains"]["valid"] is True
    assert "material_b64" not in json.dumps(status)

    code, dry, _err = _cli("vault-rotate", "--vault", str(tmp_path / "vault"), "--dry-run")
    assert code == 0 and dry["dry_run"] is True and dry["would_activate_version"] == 2
    # A dry run must not have changed anything.
    assert Path(stored).read_bytes() == before
    assert not (tmp_path / "vault.vault.keys.json").exists()

    code, rotated, _err = _cli("vault-rotate", "--vault", str(tmp_path / "vault"))
    assert code == 0 and rotated["verified_ok"] is True and rotated["active_version"] == 2

    code, audit_dry, _err = _cli("audit-rotate", "--db", str(tmp_path / "erp.sqlite3"), "--dry-run")
    assert code == 0 and audit_dry["would_activate_version"] == 2
    assert not (tmp_path / "erp.sqlite3.audit.keys.json").exists()

    code, audit, _err = _cli("audit-rotate", "--db", str(tmp_path / "erp.sqlite3"))
    assert code == 0 and audit["chain_valid_after"] is True and audit["retained_versions"] == [1, 2]


def test_operator_cli_requires_explicit_acknowledgement_to_delete_a_key(tmp_path: Path) -> None:
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC DOCUMENT")
    vault.rotate_key()

    code, payload, err = _cli("vault-remove", "--vault", str(tmp_path / "vault"), "--version", "1")
    assert code == 2 and payload is None
    assert "without explicit confirmation" in err
    assert KeyRing.load(tmp_path / "vault.vault.keys.json", "vault").versions == [1, 2]

    code, removed, _err = _cli(
        "vault-remove",
        "--vault",
        str(tmp_path / "vault"),
        "--version",
        "1",
        "--i-have-verified-retained-copies",
    )
    assert code == 0 and removed["removed_version"] == 1
    assert KeyRing.load(tmp_path / "vault.vault.keys.json", "vault").versions == [2]


def test_operator_cli_refuses_to_rotate_when_a_key_is_missing(tmp_path: Path) -> None:
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, "a" * 64, b"SYNTHETIC DOCUMENT")
    vault.rotate_key()
    # A legacy blob still encrypted with version 1, as an un-migrated document
    # would be: removing version 1 would orphan it.
    legacy_key = vault.keyring.material_for(1)
    (tmp_path / "vault" / "company_1" / f"{'b' * 64}.enc").write_bytes(
        Fernet(legacy_key).encrypt(b"SECOND DOCUMENT")
    )

    # Strip version 1 from the registry while ciphertext still needs it: the CLI
    # must refuse and leave the registry alone.
    ring_path = tmp_path / "vault.vault.keys.json"
    payload = json.loads(ring_path.read_text())
    del payload["keys"]["1"]
    ring_path.write_text(json.dumps(payload))
    snapshot = ring_path.read_text()

    code, _out, err = _cli("vault-rotate", "--vault", str(tmp_path / "vault"))
    assert code == 2
    assert "version" in err.lower()
    assert ring_path.read_text() == snapshot, "the CLI modified the registry before refusing"
