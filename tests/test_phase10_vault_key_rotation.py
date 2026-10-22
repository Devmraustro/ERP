# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 10 — Vault encryption-key rotation: legacy compatibility, safety, recovery.

Every test here uses synthetic fixtures only. The properties under test are the
ones an operator would rely on during a real incident, so each one is written to
fail loudly rather than to confirm that the happy path works:

* old ciphertext written before versioning existed must still decrypt;
* new ciphertext must state which key version it needs;
* rotation must preserve the exact plaintext, including the record identity;
* an interruption must leave *everything* readable and be resumable;
* a missing historical key must stop the service rather than silently orphaning
  documents, and must never be replaced on the operator's behalf;
* a key that data still depends on must not be deletable.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from dz_erp.intake_ocr import (
    VAULT_KEYRING_SUFFIX,
    VaultKeyUnavailableError,
    PrivateDocumentVault,
)
from dz_erp.database import TenantIsolationError
from dz_erp.key_rotation import (
    BLOB_MAGIC,
    KeyConfigurationError,
    KeyRing,
    KeyVersionInUseError,
    encode_versioned_blob,
    split_blob,
)


DOCUMENTS = {
    "a": b"SYNTHETIC INVOICE A - not a real customer document",
    "1": b"SYNTHETIC RECEIPT B",
    "cafe": b"SYNTHETIC DELIVERY NOTE C \x00\x01 binary bytes",
    "9": b"x" * 5000,
}


def _sha(tag: str) -> str:
    # Deterministic, obviously synthetic blob names. The vault treats the name
    # as the content hash; rotation must preserve it rather than re-derive it.
    return (tag.encode().hex() * 64)[:64]


def _seed_vault(root: Path, count: int = 4) -> tuple:
    vault = PrivateDocumentVault(root)
    paths = {}
    for tag, content in list(DOCUMENTS.items())[:count]:
        paths[tag] = vault.store_bytes(company_id=1, file_sha256=_sha(tag), content=content)
    return vault, paths


def _keyring_file(root: Path) -> Path:
    return root.parent / (root.name + VAULT_KEYRING_SUFFIX)


def _write_legacy_blob(vault: PrivateDocumentVault, tag: str, content: bytes) -> Path:
    """Write a pre-versioning blob exactly as the old code did: raw Fernet token."""
    dest = Path(vault.store_bytes(1, _sha(tag), b"placeholder"))
    dest.write_bytes(Fernet(vault.encryption_key).encrypt(content))
    return dest


# ----------------------------------------------------------------------
# 1. Format: legacy read compatibility and explicit version metadata
# ----------------------------------------------------------------------
def test_legacy_unversioned_ciphertext_still_decrypts(tmp_path: Path) -> None:
    """Ciphertext written before versioning must keep working unchanged.

    This is the compatibility guarantee the whole migration rests on: no
    document is rewritten on upgrade, so a vault full of legacy blobs must be
    fully readable by the new code.
    """
    vault = PrivateDocumentVault(tmp_path / "vault")
    dest = _write_legacy_blob(vault, "a", DOCUMENTS["a"])

    raw = dest.read_bytes()
    assert not raw.startswith(BLOB_MAGIC), "fixture is not actually legacy ciphertext"
    version, _payload, was_legacy = split_blob(raw)
    assert (version, was_legacy) == (1, True)

    restarted = PrivateDocumentVault(tmp_path / "vault")
    assert restarted.read_bytes(1, 1, str(dest)) == DOCUMENTS["a"]
    assert restarted.scan_blob_versions() == {1: 1}


def test_new_ciphertext_records_its_key_version(tmp_path: Path) -> None:
    """New blobs must be self-describing; otherwise rotation cannot resolve keys."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    stored = Path(vault.store_bytes(1, _sha("a"), DOCUMENTS["a"]))

    raw = stored.read_bytes()
    assert raw.startswith(BLOB_MAGIC + bytes([1])), "new blob carries no version metadata"
    version, payload, was_legacy = split_blob(raw)
    assert (version, was_legacy) == (1, False)
    assert Fernet(vault.encryption_key).decrypt(payload) == DOCUMENTS["a"]
    # The plaintext must never be recoverable from the stored bytes.
    assert DOCUMENTS["a"] not in raw


def test_blob_version_prefix_cannot_be_confused_with_a_fernet_token(tmp_path: Path) -> None:
    """The format discriminator has to be unambiguous, not merely unlikely.

    Fernet tokens always begin with the byte ``0x80``; the magic begins with
    ``0x44``. A legacy token therefore can never be mistaken for a versioned
    blob, so no trial decryption or heuristic is involved.
    """
    token = Fernet(Fernet.generate_key()).encrypt(b"x")
    assert token.startswith(b"gAAAAA"), "every Fernet token is base64url of a 0x80 version byte"
    assert BLOB_MAGIC[0] == 0x44 != token[0]
    assert split_blob(token)[2] is True


def test_structurally_broken_blob_is_reported_not_guessed(tmp_path: Path) -> None:
    """A zero/torn version header is corruption, not 'legacy'."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    stored = Path(vault.store_bytes(1, _sha("a"), DOCUMENTS["a"]))
    stored.write_bytes(BLOB_MAGIC + b"\x00" + b"garbage")

    report = vault.verify_blobs()
    assert report["failed"] == 1
    assert report["checked"] == 0
    assert report["errors"][0]["reason"] == "BlobFormatError"


# ----------------------------------------------------------------------
# 2. Rotation across multiple records
# ----------------------------------------------------------------------
def test_rotation_migrates_every_record_and_preserves_content(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=4)
    before = {tag: Path(p).read_bytes() for tag, p in paths.items()}

    report = vault.rotate_key()

    assert report["previous_version"] == 1
    assert report["active_version"] == 2
    assert report["attempted"] == 4
    assert report["completed"] == 4
    assert report["failed"] == 0
    assert report["verified_ok"] is True
    assert report["verification"] == {"checked": 4, "failed": 0, "by_version": {"2": 4}, "errors": []}

    # Same logical content, same record identity, different ciphertext.
    for tag, stored in paths.items():
        assert Path(stored).name == f"{_sha(tag)}.enc", "record identity changed during rotation"
        assert Path(stored).read_bytes() != before[tag], "blob was not actually re-encrypted"
        assert vault.read_bytes(1, 1, stored) == DOCUMENTS[tag]
    assert vault.scan_blob_versions() == {2: 4}

    # The old key is retained: rotation is reversible, deletion is separate.
    assert vault.keyring.versions == [1, 2]
    assert vault.keyring.has_version(1)
    assert _keyring_file(root).exists()


def test_rotated_vault_survives_a_restart_without_the_old_key_in_memory(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root)
    vault.rotate_key()

    # Fresh object: the registry on disk has to carry both versions.
    restarted = PrivateDocumentVault(root)
    assert restarted.encryption_key == vault.encryption_key
    assert restarted.active_key_version == 2
    assert restarted.keyring.versions == [1, 2]
    for tag, stored in paths.items():
        assert restarted.read_bytes(1, 1, stored) == DOCUMENTS[tag]


def test_keyring_file_is_owner_only(tmp_path: Path, assert_owner_only_permissions) -> None:
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=1)
    vault.rotate_key()
    target = _keyring_file(root)
    assert_owner_only_permissions(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # The key material must not sit among the ciphertext it protects.
    assert list(root.rglob("*" + VAULT_KEYRING_SUFFIX)) == []


# ----------------------------------------------------------------------
# 3. Failure modes: wrong key, corrupt ciphertext, missing versions
# ----------------------------------------------------------------------
def test_wrong_key_is_still_rejected(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=1)
    wrong = PrivateDocumentVault(root, encryption_key=Fernet.generate_key())
    with pytest.raises(InvalidToken):
        wrong.read_bytes(1, 1, paths["a"])


def test_corrupted_ciphertext_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=1)
    stored = Path(paths["a"])
    raw = bytearray(stored.read_bytes())
    raw[-5] ^= 0xFF
    stored.write_bytes(bytes(raw))

    with pytest.raises(InvalidToken):
        vault.read_bytes(1, 1, str(stored))
    assert vault.verify_blobs()["failed"] == 1


def test_missing_historical_key_stops_the_vault_from_opening(tmp_path: Path) -> None:
    """A lost key version must be fatal, and must never be regenerated."""
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=2)
    # Simulate an interruption that leaves version 1 blobs behind...
    original = PrivateDocumentVault._rotate_one
    calls = {"n": 0}

    def fail_after_one(self, path, target_version, max_retries, journal, report):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("simulated crash")
        return original(self, path, target_version, max_retries, journal, report)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(PrivateDocumentVault, "_rotate_one", fail_after_one)
    with pytest.raises(RuntimeError):
        vault.rotate_key()
    monkeypatch.undo()
    assert vault.scan_blob_versions() == {1: 1, 2: 1}

    # ...then lose version 1 entirely.
    ring_file = _keyring_file(root)
    payload = json.loads(ring_file.read_text())
    del payload["keys"]["1"]
    ring_file.write_text(json.dumps(payload))

    with pytest.raises(VaultKeyUnavailableError) as excinfo:
        PrivateDocumentVault(root)
    message = str(excinfo.value)
    assert "version(s) [1]" in message
    assert "Restore the missing key version" in message
    # No replacement key may have been written in the meantime.
    assert json.loads(ring_file.read_text())["current_version"] == 2
    assert "1" not in json.loads(ring_file.read_text())["keys"]


def test_blob_with_an_unknown_version_raises_key_unavailable(tmp_path: Path) -> None:
    """An unknown version is a lost key, not a decryption failure."""
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=1)
    orphan = root / "company_1" / f"{_sha('z')}.enc"
    orphan.write_bytes(encode_versioned_blob(7, Fernet(Fernet.generate_key()).encrypt(b"ORPHAN")))

    with pytest.raises(VaultKeyUnavailableError) as excinfo:
        vault.read_bytes(1, 1, str(orphan))
    assert "version 7" in str(excinfo.value)
    # Reading it must not have changed it.
    assert orphan.read_bytes().startswith(BLOB_MAGIC + b"\x07")

    # And opening a vault that contains it fails closed at start-up.
    with pytest.raises(VaultKeyUnavailableError):
        PrivateDocumentVault(root)


def test_configuration_disagreement_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once a registry exists it is authoritative; a contradicting key is an error."""
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=1)
    vault.rotate_key()

    with pytest.raises(KeyConfigurationError, match="explicit vault key disagrees"):
        PrivateDocumentVault(root, encryption_key=Fernet.generate_key())

    monkeypatch.setenv("ERP_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(KeyConfigurationError, match="ERP_VAULT_KEY disagrees"):
        PrivateDocumentVault(root)


# ----------------------------------------------------------------------
# 4. Key-ring validation
# ----------------------------------------------------------------------
def test_duplicate_and_invalid_key_versions_are_rejected(tmp_path: Path) -> None:
    ring = KeyRing.bootstrap("vault", Fernet.generate_key())
    second = Fernet.generate_key()

    with pytest.raises(KeyConfigurationError, match="already exists"):
        ring.add_version(second, version=1)
    with pytest.raises(KeyConfigurationError, match="already registered as version 1"):
        ring.add_version(ring.material_for(1))
    with pytest.raises(KeyConfigurationError, match="outside the supported range"):
        ring.add_version(second, version=0)
    with pytest.raises(KeyConfigurationError, match="outside the supported range"):
        ring.add_version(second, version=256)

    assert ring.add_version(second) == 2
    assert ring.versions == [1, 2]
    assert ring.active_version == 2
    # Exactly one active version, always.
    assert [e.version for e in ring.entries.values() if e.status == "active"] == [2]


def test_keyring_file_of_the_wrong_kind_is_rejected(tmp_path: Path) -> None:
    ring = KeyRing.bootstrap("audit", b"a" * 32)
    path = tmp_path / "ring.json"
    ring.save(path)
    with pytest.raises(KeyConfigurationError, match="expected"):
        KeyRing.load(path, "vault")


def test_tampered_keyring_file_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=1)
    vault.rotate_key()

    ring_file = _keyring_file(root)
    payload = json.loads(ring_file.read_text())
    payload["current_version"] = 99
    ring_file.write_text(json.dumps(payload))
    with pytest.raises(KeyConfigurationError, match="no key entry"):
        PrivateDocumentVault(root)

    ring_file.write_text("{not json")
    with pytest.raises(KeyConfigurationError, match="could not be parsed"):
        PrivateDocumentVault(root)


# ----------------------------------------------------------------------
# 5. Interruption and resumption
# ----------------------------------------------------------------------
def test_interrupted_rotation_leaves_everything_readable_and_is_resumable(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=4)
    original = PrivateDocumentVault._rotate_one
    calls = {"n": 0}

    def fail_after_two(self, path, target_version, max_retries, journal, report):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("simulated crash between records")
        return original(self, path, target_version, max_retries, journal, report)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(PrivateDocumentVault, "_rotate_one", fail_after_two)
    with pytest.raises(RuntimeError, match="simulated crash"):
        vault.rotate_key()
    monkeypatch.undo()

    # Half-migrated, but nothing is lost and nothing is falsely marked done.
    assert vault.scan_blob_versions() == {1: 2, 2: 2}
    assert vault.verify_blobs()["failed"] == 0
    for tag, stored in paths.items():
        assert vault.read_bytes(1, 1, stored) == DOCUMENTS[tag]

    journal = vault.rotation_journal_path.read_text()
    assert '"rotation_started"' in journal
    assert '"rotation_finished"' not in journal, "an interrupted run must not record completion"
    assert journal.count('"blob_reencrypted"') == 2

    new_key = vault.keyring.material_for(2)
    resumed = PrivateDocumentVault(root).rotate_key(new_key)

    assert resumed["resumed"] is True
    assert resumed["active_version"] == 2, "resuming must not invent a third version"
    assert resumed["completed"] == 2
    assert resumed["skipped_already_current"] == 2
    assert resumed["verified_ok"] is True
    assert vault.keyring.versions == [1, 2]
    for tag, stored in paths.items():
        assert PrivateDocumentVault(root).read_bytes(1, 1, stored) == DOCUMENTS[tag]


def test_rotation_is_idempotent_when_nothing_needs_migrating(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=2)
    vault.rotate_key()

    again = PrivateDocumentVault(root).rotate_key(vault.keyring.material_for(2))
    assert again["completed"] == 0
    assert again["skipped_already_current"] == 2
    assert again["verified_ok"] is True


# ----------------------------------------------------------------------
# 6. Concurrency
# ----------------------------------------------------------------------
def test_concurrent_update_during_rotation_is_never_overwritten(tmp_path: Path) -> None:
    """A writer that changes a blob mid-rotation must not lose its update.

    The rotation captures the blob's identity before reading and re-checks it
    before the atomic replace. Here the race is forced: the first replace sees a
    changed file, must back off, then re-read and rotate the *new* content - so
    the concurrent writer's bytes survive.
    """
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=3)
    target = Path(paths["a"])
    concurrent_content = b"CONCURRENTLY UPDATED DOCUMENT"
    original = PrivateDocumentVault._replace_if_unchanged
    raced = {"done": False}

    def racing_replace(path: Path, blob: bytes, before) -> bool:
        if path == target and not raced["done"]:
            raced["done"] = True
            # Another writer lands its update between our read and our replace.
            # Version 1 ciphertext, encrypted with the *version 1* key: the
            # active key has already moved to version 2 by this point.
            path.write_bytes(
                encode_versioned_blob(1, Fernet(vault.keyring.material_for(1)).encrypt(concurrent_content))
            )
            assert original(path, blob, before) is False, "a concurrent write was silently overwritten"
            return False
        return original(path, blob, before)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(PrivateDocumentVault, "_replace_if_unchanged", staticmethod(racing_replace))
    report = vault.rotate_key()
    monkeypatch.undo()

    assert raced["done"] is True, "the race was never exercised"
    assert report["skipped_concurrent"] == 0, "the contended blob should have been retried, not skipped"
    assert report["completed"] == 3
    assert report["failed"] == 0
    assert report["verified_ok"] is True
    # The concurrent writer's content is what survived - not ours.
    assert vault.read_bytes(1, 1, str(target)) == concurrent_content
    assert vault.scan_blob_versions() == {2: 3}
    assert '"blob_contended"' in vault.rotation_journal_path.read_text()


def test_rotation_lock_prevents_a_second_concurrent_run(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=1)

    from dz_erp.key_rotation import RotationInProgressError, rotation_lock

    with rotation_lock(vault.rotation_lock_path):
        with pytest.raises(RotationInProgressError):
            PrivateDocumentVault(root).rotate_key()
    # The lock is released afterwards, so a normal rotation still works.
    assert vault.rotate_key()["verified_ok"] is True


# ----------------------------------------------------------------------
# 7. Tenant isolation and key lifecycle
# ----------------------------------------------------------------------
def test_rotation_does_not_weaken_tenant_isolation(tmp_path: Path) -> None:
    vault, paths = _seed_vault(tmp_path / "vault", count=1)
    vault.store_bytes(company_id=2, file_sha256=_sha("b"), content=b"OTHER TENANT")
    vault.rotate_key()

    with pytest.raises(TenantIsolationError):
        vault.read_bytes(1, 2, paths["a"])
    with pytest.raises(TenantIsolationError):
        vault.read_bytes(1, 1, str(tmp_path / "vault" / "company_2" / f"{_sha('b')}.enc"))


def test_key_removal_is_refused_while_ciphertext_still_depends_on_it(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=2)
    original = PrivateDocumentVault._rotate_one
    calls = {"n": 0}

    def fail_after_one(self, path, target_version, max_retries, journal, report):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("simulated crash")
        return original(self, path, target_version, max_retries, journal, report)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(PrivateDocumentVault, "_rotate_one", fail_after_one)
    with pytest.raises(RuntimeError):
        vault.rotate_key()
    monkeypatch.undo()

    with pytest.raises(KeyVersionInUseError, match="still encrypted with it"):
        vault.remove_key(1, confirm_retained_copies=True)

    # The active version is never removable, whatever the caller says.
    with pytest.raises(KeyConfigurationError, match="active version"):
        vault.remove_key(2, confirm_retained_copies=True)

    # Finish the job, then removal is *possible* but still requires an explicit
    # acknowledgement that backups may hold old ciphertext.
    vault.rotate_key(vault.keyring.material_for(2))
    for stored in paths.values():
        assert vault.read_bytes(1, 1, stored) in DOCUMENTS.values()
    assert vault.scan_blob_versions() == {2: 2}
    with pytest.raises(KeyConfigurationError, match="without explicit confirmation"):
        vault.remove_key(1)
    assert vault.remove_key(1, confirm_retained_copies=True)["removed_version"] == 1
    assert vault.keyring.versions == [2]
    # Old blobs are gone, so the vault still verifies.
    assert vault.verify_blobs()["failed"] == 0


def test_retired_key_still_decrypts_but_is_not_used_for_new_writes(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    vault, paths = _seed_vault(root, count=1)
    vault.rotate_key()

    # Retire version 1 only after the data that used it has been migrated.
    for path in root.rglob("*.enc"):
        path.write_bytes(path.read_bytes())  # no-op: keeps the assertion explicit
    assert vault.retire_key(1) == {"retired_version": 1, "active_version": 2}

    fresh = vault.store_bytes(1, _sha("b"), b"NEW DOCUMENT")
    assert split_blob(Path(fresh).read_bytes())[0] == 2
    assert vault.read_bytes(1, 1, paths["a"]) == DOCUMENTS["a"]

    with pytest.raises(KeyConfigurationError, match="active"):
        vault.retire_key(2)


# ----------------------------------------------------------------------
# 8. No secrets in logs or journals
# ----------------------------------------------------------------------
def test_rotation_journal_contains_no_plaintext_or_key_material(tmp_path: Path) -> None:
    """The journal is written to be attachable to an incident ticket."""
    root = tmp_path / "vault"
    vault, _ = _seed_vault(root, count=2)
    report = vault.rotate_key()

    journal_text = vault.rotation_journal_path.read_text()
    assert json.loads(report and journal_text.splitlines()[0])  # journal is valid JSONL
    for version in (1, 2):
        material = vault.keyring.material_for(version)
        assert material.decode("ascii") not in journal_text
        assert material.hex() not in journal_text
    for content in DOCUMENTS.values():
        assert content.decode("latin-1") not in journal_text
    for secret in ("SYNTHETIC", "INVOICE", "receipt"):
        assert secret.lower() not in journal_text.lower()
    # Only blob names, versions, statuses and timestamps are recorded.
    assert set(json.loads(journal_text.splitlines()[0])) <= {
        "timestamp",
        "event",
        "blob",
        "from_version",
        "to_version",
        "previous_version",
        "target_version",
        "resumed",
        "blobs_by_version",
        "completed",
        "skipped",
        "failed",
        "verification_failures",
        "attempt",
        "reason",
    }
