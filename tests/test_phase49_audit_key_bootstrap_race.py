# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 49 — concurrent audit-key bootstrap (a defect found by the R-02 work).

The concurrent-startup test added for R-02 failed **once** on CI and never
locally. Chasing it rather than calling it a flake found a real defect in the
one start-up step that was still not atomic:

    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # empty file is now visible
    ...                                                                    # <-- another process reads it here
    with os.fdopen(fd, "wb") as handle:
        handle.write(secret)

``O_EXCL`` gives "exactly one creator" but not "a readable file the moment it
exists": the name is published before the material is. A second worker booting
in that window takes the ``FileExistsError`` branch, reads the file, finds it
empty, and raises ``KeyMaterialUnavailable`` — a process that cannot start,
for no reason a human could act on. Python's buffered write makes the window
wider than it looks: nothing reaches the file until the buffer is flushed.

The fix publishes the material as a *complete* file: write to a private
temporary file, fsync, then ``os.link`` it into place (atomic, and it fails if
the name exists, so the first publisher wins and the loser adopts a file it can
actually read). These tests hold that invariant, because an invariant that only
holds "usually" is how the original failure looked.

Honesty about evidence: the CI failure itself was never reproduced on this host
(1 680 concurrent starts, zero failures), so this file does **not** claim to
reproduce it. It pins the property that failure violated, and the property is
failure-detecting by construction: an empty-but-visible key file raises
``KeyMaterialUnavailable`` immediately, which is what a reader in that window
did.
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase, _publish_key_file, _resolve_audit_secret
from dz_erp.key_provider import KeyMaterialUnavailable

REPO_ROOT = Path(__file__).resolve().parent.parent

#: How many workers boot together. Real deployments use a process pool; five is
#: enough to interleave and keeps the test quick.
WORKERS = 5
ROUNDS = 3


def _digest(material: bytes) -> str:
    return hashlib.sha256(material).hexdigest()


# ---------------------------------------------------------------------------
# 1. the invariant: a published key file is never observable empty
# ---------------------------------------------------------------------------


def test_a_reader_never_observes_a_half_published_key(tmp_path: Path) -> None:
    """Poll the key path while it is being published; it is absent or complete.

    This is the window the defect lived in. A reader that arrives before the
    publish sees nothing and creates the key itself; a reader that arrives after
    sees the whole thing. What must never happen is "exists, but empty".
    """
    key_path = tmp_path / "erp.sqlite3.audit.key"
    secret = base64.urlsafe_b64encode(os.urandom(32))
    observed: list[str] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            if key_path.exists():
                try:
                    content = key_path.read_bytes()
                except FileNotFoundError:  # pragma: no cover - replaced mid-read
                    continue
                if not content:
                    observed.append("EMPTY")
                else:
                    observed.append("complete")
            time.sleep(0.0005)

    reader = threading.Thread(target=poll, daemon=True)
    reader.start()
    try:
        for _ in range(20):
            if key_path.exists():
                key_path.unlink()
            published = _publish_key_file(key_path, secret)
            assert published == secret
            time.sleep(0.002)
    finally:
        stop.set()
        reader.join(timeout=5)

    assert "EMPTY" not in observed, (
        f"a key file was visible with no material in it: {observed[:20]}"
    )
    assert key_path.read_bytes() == secret


def test_the_winner_is_the_only_key_anyone_adopts(tmp_path: Path) -> None:
    """Concurrent publishers converge on one key; nobody overwrites the winner."""
    key_path = tmp_path / "erp.sqlite3.audit.key"
    results: list[bytes] = []
    errors: list[str] = []
    barrier = threading.Barrier(8)

    def publish() -> None:
        material = base64.urlsafe_b64encode(os.urandom(32))
        barrier.wait()
        try:
            results.append(_publish_key_file(key_path, material))
        except Exception as exc:  # recorded, then asserted
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=publish) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [], errors
    assert len(results) == 8
    # Everyone ends up with the same key, and it is the one on disk: a process
    # that adopted its own freshly generated key here would sign audit events
    # the rest of the deployment cannot verify.
    assert len({_digest(material) for material in results}) == 1
    assert results[0] == key_path.read_bytes()
    assert key_path.read_bytes()


def test_the_published_file_is_owner_only_and_leaves_no_temporary_behind(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "erp.sqlite3.audit.key"
    _publish_key_file(key_path, base64.urlsafe_b64encode(os.urandom(32)))

    if os.name == "posix":
        import stat

        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600, oct(mode)
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name != key_path.name)
    assert leftovers == [], f"temporary key material left behind: {leftovers}"


def test_a_publish_failure_does_not_leave_readable_material_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed publish must not be mistaken for a published key later."""
    key_path = tmp_path / "erp.sqlite3.audit.key"

    def failing_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated cross-device link failure")

    monkeypatch.setattr(os, "link", failing_link)
    published = _publish_key_file(key_path, b"ADOPTED-MATERIAL")
    assert published == b"ADOPTED-MATERIAL"
    assert key_path.read_bytes() == b"ADOPTED-MATERIAL"
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name != key_path.name)
    assert leftovers == [], f"temporary key material left behind: {leftovers}"


# ---------------------------------------------------------------------------
# 2. the defect at the level a user sees it: several workers booting at once
# ---------------------------------------------------------------------------

CHILD = """
from dz_erp.database import ERPDatabase
db = ERPDatabase({path!r})
assert db.verify_audit_chain(1)["valid"] is True
print("OPENED")
"""


def test_every_concurrent_worker_opens_the_same_database(tmp_path: Path) -> None:
    """Repeat the race: a lucky start-up is not evidence that it is serialised."""
    db_path = tmp_path / "race.sqlite3"
    for _round in range(ROUNDS):
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", CHILD.format(path=str(db_path))],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(REPO_ROOT),
            )
            for _ in range(WORKERS)
        ]
        outcomes = []
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            outcomes.append((out, err, proc.returncode))

        failures = [
            (out.strip()[:40], (err.strip().splitlines() or ["<none>"])[-1][:200])
            for out, err, code in outcomes
            if code != 0 or not out.strip().startswith("OPENED")
        ]
        # Raised, not asserted comparison-style: CI truncates the diff of
        # ``assert failures == []``, which is how this failure was first seen as
        # an unexplained "+ ]".
        if failures:
            raise AssertionError(f"STARTUPCAUSE {failures[0]}")

    # One key file, adopted by all of them, and the chain still opens.
    key_files = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".audit.key"))
    assert key_files == ["race.sqlite3.audit.key"], key_files
    db = ERPDatabase(str(db_path))
    assert db.verify_audit_chain(1)["valid"] is True


def test_resolving_the_secret_twice_returns_the_same_key(tmp_path: Path) -> None:
    """The restart case: the file, not a fresh random key, is authoritative."""
    db_path = tmp_path / "restart.sqlite3"
    first = _resolve_audit_secret(str(db_path), None)
    second = _resolve_audit_secret(str(db_path), None)
    assert first == second
    assert _digest(first) == _digest((tmp_path / "restart.sqlite3.audit.key").read_bytes())


def test_an_unreadable_existing_key_file_is_reported_not_replaced(
    tmp_path: Path,
) -> None:
    """A corrupt key must fail loudly: minting a new one would blame an attacker."""
    db_path = tmp_path / "corrupt.sqlite3"
    key_path = tmp_path / "corrupt.sqlite3.audit.key"
    key_path.write_bytes(b"")
    os.chmod(key_path, 0o600)

    with pytest.raises(KeyMaterialUnavailable):
        _resolve_audit_secret(str(db_path), None)
    assert key_path.read_bytes() == b"", "the existing file must not be overwritten"
