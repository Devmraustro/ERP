# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 17 — Key providers: what they enforce and what they admit.

The point of these tests is not "the abstraction exists". It is that the claims a
risk register will repeat are executable:

* a key file readable beyond its owner is refused, not used;
* a symlinked key is refused, so replacing a link cannot redirect the key the
  service loads;
* the mode is checked on the descriptor actually read, not on a path that could
  be swapped;
* every provider reports `hsm_equivalent: False` unless it genuinely keeps key
  material inside a boundary - and the KMS provider, which is not implemented,
  refuses to supply material instead of quietly generating some.
"""

from __future__ import annotations

import base64
import os
import stat

import pytest
from cryptography.fernet import Fernet

from dz_erp.key_provider import (
    EnvironmentKeyProvider,
    FileKeyProvider,
    KeyMaterialUnavailable,
    KeyProvider,
    KmsKeyProvider,
    ensure_key_directory,
)
from dz_erp.key_rotation import KeyConfigurationError, KeyCustodyError

posix_only = pytest.mark.skipif(os.name != "posix", reason="mode bits and symlinks are POSIX semantics")


def _key_file(tmp_path, material: bytes | None = None, mode: int = 0o600):
    path = tmp_path / "vault.vault.key"
    path.write_bytes(material or Fernet.generate_key())
    os.chmod(path, mode)
    return path


# ----------------------------------------------------------------------
# FileKeyProvider
# ----------------------------------------------------------------------
def test_reads_owner_only_key_and_round_trips(tmp_path) -> None:
    material = Fernet.generate_key()
    provider = FileKeyProvider(_key_file(tmp_path, material))
    assert provider.read() == material
    assert provider.read() == material, "a second read must not fail or differ"


@posix_only
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660, 0o666])
def test_refuses_group_or_world_accessible_key(tmp_path, mode: int) -> None:
    provider = FileKeyProvider(_key_file(tmp_path, mode=mode))
    with pytest.raises(KeyCustodyError, match="grants access beyond the owner"):
        provider.read()


def test_missing_and_empty_keys_are_distinguishable_from_weak_ones(tmp_path) -> None:
    missing = FileKeyProvider(tmp_path / "nope.key")
    with pytest.raises(KeyMaterialUnavailable, match="does not exist"):
        missing.read()

    empty = tmp_path / "empty.key"
    empty.write_bytes(b"   \n")
    os.chmod(empty, 0o600)
    with pytest.raises(KeyMaterialUnavailable, match="empty"):
        FileKeyProvider(empty).read()


@posix_only
def test_refuses_to_follow_a_symlinked_key(tmp_path) -> None:
    """A symlink can be repointed by anyone who can write the directory."""
    real = _key_file(tmp_path, Fernet.generate_key())
    link = tmp_path / "link.key"
    link.symlink_to(real)

    with pytest.raises(KeyConfigurationError, match="symlink"):
        FileKeyProvider(link).read()

    # A symlink to a *world-readable* file must not slip through either.
    loose = tmp_path / "loose.key"
    loose.write_bytes(Fernet.generate_key())
    os.chmod(loose, 0o644)
    link.unlink()
    link.symlink_to(loose)
    with pytest.raises(KeyConfigurationError, match="symlink"):
        FileKeyProvider(link).read()


@pytest.mark.skipif(os.name != "nt", reason="exercises the Windows reparse-point open path")
def test_windows_provider_refuses_to_follow_a_symlinked_key(tmp_path) -> None:
    """Windows must inspect the reparse point itself, not the file it targets."""
    real = _key_file(tmp_path, Fernet.generate_key())
    link = tmp_path / "windows-link.key"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"the Windows runner cannot create a file symlink: {exc}")

    with pytest.raises(KeyConfigurationError, match="reparse point"):
        FileKeyProvider(link).read()


@posix_only
def test_mode_is_checked_on_the_descriptor_not_the_path(tmp_path, monkeypatch) -> None:
    """Swap the file between any path check and the read; the read still decides."""
    path = _key_file(tmp_path, Fernet.generate_key())
    provider = FileKeyProvider(path)

    # Simulate a path-based check passing, then the file being replaced by a
    # permissive one immediately before the read.
    real_open = os.open

    def swapping_open(target, flags, *args, **kwargs):
        if str(target) == str(path):
            os.chmod(path, 0o644)
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises(KeyCustodyError, match="grants access beyond the owner"):
        provider.read()


def test_reload_picks_up_rotated_material(tmp_path) -> None:
    path = _key_file(tmp_path, Fernet.generate_key())
    provider = FileKeyProvider(path)
    first = provider.read()

    second = Fernet.generate_key()
    path.write_bytes(second)
    os.chmod(path, 0o600)
    assert provider.read() == first, "the cached material is what a running service uses"

    provider.reload()
    assert provider.read() == second


def test_write_creates_owner_only_file_and_directory(tmp_path) -> None:
    target = tmp_path / "keys" / "audit.key"
    material = Fernet.generate_key()
    FileKeyProvider(target).write(material)

    assert target.read_bytes() == material
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "keys").stat().st_mode) & 0o077 == 0


@posix_only
def test_ensure_key_directory_tightens_an_open_directory(tmp_path) -> None:
    directory = tmp_path / "keys"
    directory.mkdir()
    os.chmod(directory, 0o755)
    ensure_key_directory(directory)
    assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0


def test_describe_is_auditable_and_contains_no_key_material(tmp_path) -> None:
    material = Fernet.generate_key()
    description = FileKeyProvider(_key_file(tmp_path, material)).describe()

    assert description["provider"] == "file"
    assert description["hsm_equivalent"] is False
    assert description["backupable"] is True
    assert description["key_material_exportable"] is True
    assert description["exists"] is True
    assert description["is_symlink"] is False
    # The whole point: metadata, never material.
    assert material.decode() not in str(description)
    assert material.hex() not in str(description)
    if os.name == "posix":
        assert description["mode"] == "0o600"
        assert description["owner_uid"] == os.getuid()


def test_describe_of_a_missing_file_says_so_rather_than_raising(tmp_path) -> None:
    description = FileKeyProvider(tmp_path / "absent.key").describe()
    assert description["exists"] is False
    assert description["hsm_equivalent"] is False


# ----------------------------------------------------------------------
# EnvironmentKeyProvider
# ----------------------------------------------------------------------
def test_environment_provider_reads_and_does_not_claim_to_be_more(tmp_path, monkeypatch) -> None:
    material = Fernet.generate_key()
    monkeypatch.setenv("DZ_ERP_TEST_KEY", base64.urlsafe_b64encode(material).decode())

    provider = EnvironmentKeyProvider("DZ_ERP_TEST_KEY")
    assert provider.read() == material

    description = provider.describe()
    assert description["provider"] == "environment"
    assert description["hsm_equivalent"] is False
    assert description["backupable"] is False
    assert description["exists"] is True
    assert description["note"] and "not stronger" in description["note"]


def test_environment_provider_refuses_absent_or_malformed_secret(monkeypatch) -> None:
    monkeypatch.delenv("DZ_ERP_TEST_MISSING", raising=False)
    with pytest.raises(KeyMaterialUnavailable, match="not set or empty"):
        EnvironmentKeyProvider("DZ_ERP_TEST_MISSING").read()

    monkeypatch.setenv("DZ_ERP_TEST_BAD", "!!!not-base64!!!")
    with pytest.raises(KeyConfigurationError, match="not valid base64"):
        EnvironmentKeyProvider("DZ_ERP_TEST_BAD").read()


def test_environment_provider_is_read_only(monkeypatch) -> None:
    monkeypatch.setenv("DZ_ERP_TEST_RO", base64.urlsafe_b64encode(Fernet.generate_key()).decode())
    with pytest.raises(KeyConfigurationError, match="cannot be persisted"):
        EnvironmentKeyProvider("DZ_ERP_TEST_RO").write(b"x")


# ----------------------------------------------------------------------
# KmsKeyProvider — the honest gap
# ----------------------------------------------------------------------
def test_kms_provider_is_not_implemented_and_says_so() -> None:
    provider = KmsKeyProvider("projects/x/locations/dz/keyRings/erp/cryptoKeys/vault")
    with pytest.raises(NotImplementedError, match="not implemented"):
        provider.read()
    description = provider.describe()
    assert description["implemented"] is False
    assert description["hsm_equivalent"] is False
    assert description["key_material_exportable"] is False
    assert "KMS/HSM" in description["note"]


def test_fake_implementations_must_not_claim_hsm_equivalence_silently() -> None:
    """The protocol is structural; the *claim* is checked, not just the shape."""
    for provider in (FileKeyProvider(__import__("pathlib").Path("/nonexistent.key")),
                     EnvironmentKeyProvider("ANY")):
        assert isinstance(provider, KeyProvider)
        assert provider.describe()["hsm_equivalent"] is False
