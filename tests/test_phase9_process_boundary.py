"""Workstream C - persistence across process boundaries.

Every test in this module crosses at least one process boundary: a separate OS
process performs one half of the operation and exits before the other half runs.
A same-process test cannot catch a key that is regenerated per instantiation,
which is precisely how the vault key defect and the restore defect both survived
the previous suite.

The workers live in ``tests/process_boundary_workers.py``.  Synthetic data only:
no real customer or financial data is involved anywhere in this module.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKERS = REPO_ROOT / "tests" / "process_boundary_workers.py"

DOC_A_SHA = "a" * 64
DOC_B_SHA = "b" * 64
DOC_A = b"SYNTHETIC INVOICE PDF BYTES - company 1"
DOC_B = b"SYNTHETIC RECEIPT BYTES - company 1"


def run_worker(command: str, state: Path, env: Dict[str, str] | None = None) -> Dict[str, Any]:
    """Run one worker in a fresh interpreter and return its parsed JSON output."""
    child_env = dict(os.environ)
    child_env.pop("ERP_VAULT_KEY", None)
    child_env.pop("ERP_AUDIT_HMAC_SECRET", None)
    child_env.pop("ERP_TRUSTED_PROXIES", None)
    if env:
        child_env.update(env)

    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", command, "--state", str(state)],
        cwd=str(REPO_ROOT),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"worker '{command}' exited {proc.returncode}\n"
        f"stdout: {proc.stdout[-3000:]}\nstderr: {proc.stderr[-3000:]}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


# ----------------------------------------------------------------------
# 1. Documents survive a restart
# ----------------------------------------------------------------------
def test_documents_written_in_one_process_decrypt_in_a_fresh_process(state: Path) -> None:
    """The defect: a new Fernet key per instantiation orphaned every document."""
    written = run_worker("write", state)

    # A separate process - the writer has already exited - reopens the vault.
    read = run_worker("read-documents", state)

    import base64

    assert base64.b64decode(read["documents"][DOC_A_SHA]) == DOC_A
    assert base64.b64decode(read["documents"][DOC_B_SHA]) == DOC_B
    assert read["key_supplied_explicitly"] is False, "the reader must not be handed the key"

    # The key must live on disk, not in the writer's memory.
    assert Path(written["vault_key_file"]).exists()
    assert Path(written["audit_key_file"]).exists()


def test_audit_events_written_in_one_process_verify_in_a_fresh_process(state: Path) -> None:
    """The defect: a new HMAC key per open made every chain read as tampered."""
    run_worker("write", state)
    verified = run_worker("verify-chain", state)

    assert verified["audit_events"] == 2
    assert verified["chain"]["valid"] is True, verified["chain"]


# ----------------------------------------------------------------------
# 2. Restore produces a deployment a fresh process can actually use
# ----------------------------------------------------------------------
def test_restore_is_usable_from_a_fresh_process_in_a_different_directory(state: Path) -> None:
    """Restore -> terminate -> reopen, with no secrets supplied to the third process."""
    run_worker("write", state)
    run_worker("seed-source-documents", state)
    run_worker("backup", state)
    restored = run_worker("restore", state)

    # The restore target is a *different* directory from the live one.
    assert Path(restored["restored_db"]).parent != state / "vault"
    assert restored["audit_chains_valid"] is True
    assert restored["vault_files_verified"] == 2

    reopened = run_worker("reopen-restored", state)

    assert reopened["chain"]["valid"] is True, reopened["chain"]
    # Both documents decrypt, resolved through the ORIGINAL absolute paths that
    # the restored database still records - none of which point at the restored
    # directory.
    assert len(reopened["documents"]) == 2
    restored_vault = Path(restored["restored_vault"])
    for original_path, payload_b64 in reopened["documents"].items():
        assert restored_vault not in Path(original_path).parents, (
            f"the stored path was rewritten to the restore target instead of being "
            f"re-anchored: {original_path}"
        )
        assert base64.b64decode(payload_b64) in (DOC_A, DOC_B), (
            f"{original_path} did not decrypt to a known document"
        )


# ----------------------------------------------------------------------
# 3. Missing, corrupt and wrong keys must fail loudly - never silently repair
# ----------------------------------------------------------------------
def test_missing_vault_key_fails_loudly_and_writes_no_replacement(state: Path) -> None:
    """A lost key must not be silently replaced while documents still exist."""
    written = run_worker("write", state)
    key_file = Path(written["vault_key_file"])
    assert key_file.exists()
    key_file.unlink()

    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", "read-documents", "--state", str(state)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode != 0, "a fresh process silently accepted a missing vault key"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["__error__"] == "VaultKeyUnavailableError", payload
    assert "Refusing to generate a replacement key" in payload["message"]

    # The decisive check: no replacement key was written, so the failure is
    # durable rather than a one-off.
    assert not key_file.exists(), "a replacement key was silently generated"


def test_missing_audit_key_fails_loudly_and_writes_no_replacement(state: Path) -> None:
    """Same rule for the audit key: no silent regeneration, no disguised tamper report."""
    written = run_worker("write", state)
    key_file = Path(written["audit_key_file"])
    assert key_file.exists()
    key_file.unlink()

    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", "verify-chain", "--state", str(state)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode != 0, "a fresh process silently accepted a missing audit key"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["__error__"] == "AuditKeyUnavailableError", payload
    assert not key_file.exists(), "a replacement audit key was silently generated"


def test_corrupted_vault_key_fails_loudly(state: Path) -> None:
    """A key file that exists but is not a Fernet key must not be ignored."""
    written = run_worker("write", state)
    Path(written["vault_key_file"]).write_bytes(b"this-is-not-a-fernet-key")

    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", "read-documents", "--state", str(state)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    # Fernet rejects a malformed key at construction, before any decryption.
    assert payload["__error__"] == "ValueError", payload
    assert "Fernet key must be 32 url-safe base64-encoded bytes" in payload["message"]


def test_wrong_vault_key_cannot_decrypt(state: Path) -> None:
    """A valid but different key must fail, not silently re-encrypt."""
    from cryptography.fernet import Fernet

    run_worker("write", state)
    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", "read-documents", "--state", str(state)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
        env=dict(os.environ, ERP_VAULT_KEY=Fernet.generate_key().decode()),
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["__error__"] == "InvalidToken", payload


def test_empty_vault_still_generates_a_first_key(state: Path) -> None:
    """The guard must not break legitimate first use."""
    result = run_worker("init-empty-vault", state)
    assert result["key_exists"] is True, "an empty vault failed to mint its first key"

    # And a document stored by a *later* process is readable by a third one,
    # proving the minted key is really the one in use rather than a per-process
    # throwaway.
    run_worker("write", state)
    read = run_worker("read-documents", state)
    assert base64.b64decode(read["documents"][DOC_A_SHA]) == DOC_A


# ----------------------------------------------------------------------
# 4. Incomplete restoration is rejected
# ----------------------------------------------------------------------
def test_incomplete_restore_is_rejected(state: Path) -> None:
    """A backup missing a vault blob must fail, not restore half the data."""
    run_worker("write", state)
    run_worker("seed-source-documents", state)
    run_worker("backup", state)
    tampered = run_worker("tamper-backup", state)
    assert len(tampered["dropped_blobs"]) == 2, tampered

    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", "restore", "--state", str(state)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode != 0, "restore accepted an archive with a missing vault blob"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["__error__"] == "FinancialIntegrityError", payload
    assert "failed SHA-256 verification" in payload["message"]


# ----------------------------------------------------------------------
# 5. Repeated restores are idempotent
# ----------------------------------------------------------------------
def test_repeated_restores_are_idempotent_and_leave_a_usable_deployment(state: Path) -> None:
    run_worker("write", state)
    run_worker("seed-source-documents", state)
    run_worker("backup", state)

    first = run_worker("restore", state)
    chain_after_first = run_worker("reopen-restored", state)["chain"]

    # A second restore into the same directory must not corrupt anything.
    second = run_worker("restore", state)
    chain_after_second = run_worker("reopen-restored", state)["chain"]

    assert first["vault_files_verified"] == second["vault_files_verified"] == 2
    assert chain_after_first["valid"] is True and chain_after_second["valid"] is True
    assert chain_after_first["checked_count"] == chain_after_second["checked_count"]

    # And the restored deployment is still readable by yet another process.
    final = run_worker("reopen-restored", state)
    assert len(final["documents"]) == 2


def test_restore_rejects_a_tampered_database_payload(state: Path) -> None:
    """Flipping a byte in the encrypted archive must not pass SHA-256 verification."""
    run_worker("write", state)
    run_worker("seed-source-documents", state)
    run_worker("backup", state)

    backup = state / "backups" / "erp.enc"
    raw = bytearray(backup.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    backup.write_bytes(bytes(raw))

    proc = subprocess.run(
        [sys.executable, "-m", "tests.process_boundary_workers", "restore", "--state", str(state)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["__error__"] in {"InvalidToken", "FinancialIntegrityError"}, payload


# ----------------------------------------------------------------------
# 6. The vault key is never written inside the encrypted blob directory
# ----------------------------------------------------------------------
def test_vault_key_is_not_stored_among_the_blobs_it_protects(state: Path) -> None:
    run_worker("write", state)
    leaked = list((state / "vault").rglob("*.vault.key"))
    assert leaked == [], f"the vault key was written inside the blob directory: {leaked}"


# ----------------------------------------------------------------------
# 7. The deployment entry point fails safely on misconfiguration
# ----------------------------------------------------------------------
def _run_serve(script: str, state: Path, extra_env: Dict[str, str] | None = None):
    """Run a snippet against ``dz_erp.serve`` in a clean subprocess."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"ERP_DB_PATH", "ERP_VAULT_PATH", "ERP_TRUSTED_PROXIES",
                        "ERP_VAULT_KEY", "ERP_AUDIT_HMAC_SECRET"}}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["ERP_DB_PATH"] = str(state / "erp.sqlite3")
    env["ERP_VAULT_PATH"] = str(state / "vault")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=600,
    )


def test_entry_point_refuses_to_build_without_durable_paths(state: Path) -> None:
    """A deployment must fail loudly, never silently run against a throwaway file."""
    missing_db = _run_serve(
        "import dz_erp.serve as s; print(s.app)", state,
        {"ERP_DB_PATH": "", "ERP_VAULT_PATH": str(state / "vault")},
    )
    assert missing_db.returncode != 0
    assert "ERP_DB_PATH is not set" in missing_db.stderr

    missing_vault = _run_serve(
        "import dz_erp.serve as s; print(s.app)", state,
        {"ERP_DB_PATH": str(state / "erp.sqlite3"), "ERP_VAULT_PATH": ""},
    )
    assert missing_vault.returncode != 0
    assert "ERP_VAULT_PATH is not set" in missing_vault.stderr


def test_entry_point_builds_the_app_when_configured(state: Path) -> None:
    proc = _run_serve("import dz_erp.serve as s; print(type(s.app).__name__)", state)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "FastAPI"


def test_entry_point_refuses_to_start_a_userless_database_without_a_traceback(state: Path) -> None:
    """Regression: a fresh database crashed with 'no such table: users'.

    ``main()`` queried the database before ERPDatabase had created the schema,
    so a brand-new deployment died on an unhandled OperationalError instead of
    printing the actionable instructions it was written to print.
    """
    proc = _run_serve(
        "import sys; sys.argv = ['serve', '--host', '127.0.0.1', '--port', '0'];"
        "raise SystemExit(__import__('dz_erp.serve', fromlist=['main']).main())",
        state,
    )
    assert proc.returncode == 2, f"expected the documented refusal, got {proc.returncode}"
    assert "No users exist" in proc.stderr
    assert "--bootstrap-admin" in proc.stderr
    assert "Traceback" not in proc.stderr, "a fresh database still crashes with a traceback"
