# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 16 — Rotation convergence under concurrent writes.

A rotation that reports success while some documents still need the *previous*
key is a data-loss trap, not a cosmetic bug: the whole point of rotating is to be
able to retire the old key, and retiring it would render every stray document
unreadable. "Every blob decrypts" does not prove convergence - a blob on the old
key decrypts perfectly well.

Two windows are covered:

1. ``store_bytes`` reads the active key version, then encrypts and writes. A
   rotation can complete inside that window, so the write is re-checked.
2. A single pass over a directory listing cannot see a document that appears
   behind it. Rotation therefore re-scans the version headers and repeats until
   nothing references an old version, and reports failure to converge instead of
   claiming success.
"""

from __future__ import annotations

import hashlib
import threading

from dz_erp import intake_ocr
from dz_erp.intake_ocr import PrivateDocumentVault


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _stray_versions(vault: PrivateDocumentVault) -> dict:
    return {
        version: count
        for version, count in vault.scan_blob_versions().items()
        if version != vault.active_key_version
    }


def test_blob_written_while_the_key_is_swapped_does_not_stay_on_the_old_version(
    tmp_path, monkeypatch
) -> None:
    """The narrow window: version read, rotation completes, then the write lands."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, _digest("first"), b"FIRST")

    original_encode = intake_ocr.encode_versioned_blob
    rotated = {"done": False}

    def encode_then_rotate(version, token):
        blob = original_encode(version, token)
        if not rotated["done"]:
            rotated["done"] = True
            # Rotate *after* the version was chosen but before the bytes land at
            # their final path - exactly the interleaving a concurrent rotation
            # produces, made deterministic.
            vault.rotate_key()
        return blob

    monkeypatch.setattr(intake_ocr, "encode_versioned_blob", encode_then_rotate)
    vault.store_bytes(1, _digest("second"), b"SECOND")

    assert rotated["done"] is True, "the rotation was not exercised"
    assert _stray_versions(vault) == {}, "a document was left requiring the old key"
    assert vault.verify_blobs()["failed"] == 0


def test_rotation_converges_rather_than_trusting_one_pass(tmp_path, monkeypatch) -> None:
    """A document appearing behind the scan is picked up, not abandoned."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    for index in range(6):
        vault.store_bytes(1, _digest(f"doc-{index}"), f"DOC {index}".encode())

    original_rotate_one = PrivateDocumentVault._rotate_one
    injected = {"done": False}

    def rotate_one_then_inject(self, path, target_version, max_retries, journal, report):
        original_rotate_one(self, path, target_version, max_retries, journal, report)
        if not injected["done"]:
            injected["done"] = True
            # Written after the directory listing was materialized, so the first
            # pass cannot see it.
            digest = _digest("injected")
            destination = self.vault_root / "company_1" / f"{digest}.enc"
            from dz_erp.key_rotation import atomic_write_bytes

            stale = intake_ocr.encode_versioned_blob(
                1, self._fernet_for(1).encrypt(b"INJECTED")
            )
            atomic_write_bytes(destination, stale, mode=0o600)

    monkeypatch.setattr(PrivateDocumentVault, "_rotate_one", rotate_one_then_inject)
    report = vault.rotate_key()

    assert injected["done"] is True
    assert report["converged"] is True, report.get("stray_versions")
    assert report["convergence_passes"] >= 2, "the second pass is what catches the late document"
    assert report["status"] == "COMPLETED_VERIFIED"
    assert _stray_versions(vault) == {}
    assert vault.verify_blobs()["failed"] == 0


def test_non_convergence_is_reported_as_failure_not_success(tmp_path, monkeypatch) -> None:
    """If convergence cannot be reached, the rotation must not read as verified."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    vault.store_bytes(1, _digest("only"), b"ONLY")

    original_rotate_one = PrivateDocumentVault._rotate_one

    def rotate_one_never_converges(self, path, target_version, max_retries, journal, report):
        original_rotate_one(self, path, target_version, max_retries, journal, report)
        # Keep re-creating a stale blob after every pass, like a writer that is
        # permanently stuck on the old key.
        straggler = self.vault_root / "company_1" / f"{_digest('straggler')}.enc"
        from dz_erp.key_rotation import atomic_write_bytes

        atomic_write_bytes(
            straggler, intake_ocr.encode_versioned_blob(1, self._fernet_for(1).encrypt(b"X")), mode=0o600
        )

    monkeypatch.setattr(PrivateDocumentVault, "_rotate_one", rotate_one_never_converges)
    report = vault.rotate_key()

    assert report["converged"] is False
    assert report["status"] == "COMPLETED_UNVERIFIED"
    assert report["convergence_passes"] == intake_ocr.CONVERGENCE_PASSES, "the loop is bounded"


def test_rotation_under_sustained_concurrent_writes_still_converges(tmp_path) -> None:
    """The property, end to end, with real threads rather than an injected hook."""
    vault = PrivateDocumentVault(tmp_path / "vault")
    for index in range(30):
        vault.store_bytes(1, _digest(f"seed-{index}"), f"SEED {index}".encode())

    written: list = []
    errors: list = []
    stop = threading.Event()

    def writer(worker: int) -> None:
        counter = 0
        while not stop.is_set():
            body = f"W{worker}-{counter}".encode()
            try:
                written.append(vault.store_bytes(1, hashlib.sha256(body).hexdigest(), body))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            counter += 1

    threads = [threading.Thread(target=writer, args=(worker,), daemon=True) for worker in range(4)]
    for thread in threads:
        thread.start()
    try:
        report = vault.rotate_key()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=60)

    assert errors == []
    assert report["converged"] is True, report.get("stray_versions")
    assert report["status"] == "COMPLETED_VERIFIED"
    assert _stray_versions(vault) == {}
    assert vault.verify_blobs()["failed"] == 0
    assert all(__import__("pathlib").Path(path).exists() for path in written)
