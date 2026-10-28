# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 24 - deterministic operational readiness checker (tools/operational_readiness.py).

The checker's job is to answer, repeatably and with actionable diagnostics,
whether a deployment's *configuration* is complete and whether its documented
*verification entry points* are available - and to keep those two things
separate, because "the config keys exist" and "the verifiers pass" are
different facts.  These tests pin both halves, the exit-code contract
(0 ready / 1 not ready / 2 usage error), and the rule that controls this tool
cannot observe (off-host storage, alerting, schedulers) are reported as
required evidence and never as satisfied.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from tools import operational_readiness


def _good_env(tmp_path: Path) -> dict:
    """A complete, minimal deployment configuration inside tmp_path.

    Idempotent: calling it twice in one test must not raise, so a test can
    rebuild a clean environment after mutating its first copy. Key files are
    written owner-only (0600) and the vault directory 0700, because the
    checker's POSIX permission gate refuses anything the service account's
    peers could read - exactly like ``FileKeyProvider`` does at read time. A
    fixture that handed the checker a 0644 key file would be a fixture bug,
    not a checker bug.
    """
    db = tmp_path / "erp.sqlite3"
    db.write_bytes(b"SQLite format 3\x00")  # existence is all the checker inspects
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    vault_key = vault.parent / (vault.name + ".vault.key")
    vault_key.write_bytes(b"k" * 32)
    os.chmod(vault_key, 0o600)
    audit_key = db.parent / (db.name + ".audit.key")
    audit_key.write_bytes(b"a" * 32)
    os.chmod(audit_key, 0o600)
    if os.name == "posix":
        vault.chmod(0o700)
    return {
        "ERP_DB_PATH": str(db),
        "ERP_VAULT_PATH": str(vault),
        "ERP_SECURITY_LOG_PATH": str(log_dir / "security.jsonl"),
        "ERP_DOCS_ENABLED": "",
        "ERP_HOST": "",
    }


# ----------------------------------------------------------------------
# 1. Configuration presence, with actionable failures
# ----------------------------------------------------------------------
def test_complete_configuration_is_ready(tmp_path: Path) -> None:
    report = operational_readiness.evaluate(_good_env(tmp_path))
    assert report["ready"] is True
    assert report["failures"] == []
    assert report["mode"] == "configuration"


def test_missing_database_and_vault_fail_with_named_remediation(tmp_path: Path) -> None:
    report = operational_readiness.evaluate({k: "" for k in _good_env(tmp_path)})
    assert report["ready"] is False
    failures = report["failures"]
    joined = "\n".join(failures)
    assert "ERP_DB_PATH" in joined and "ERP_VAULT_PATH" in joined
    # Every failure must be actionable: a named remediation after the arrow.
    assert failures and all("->" in f for f in failures)

    # A configured-but-missing database must point at the recovery path.
    env = _good_env(tmp_path)
    env["ERP_DB_PATH"] = str(tmp_path / "gone.sqlite3")
    report = operational_readiness.evaluate(env)
    assert any("restore" in f.lower() for f in report["failures"])


def test_nonexistent_database_path_names_the_path(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    env["ERP_DB_PATH"] = str(tmp_path / "missing.sqlite3")
    report = operational_readiness.evaluate(env)
    assert report["ready"] is False
    assert any("missing.sqlite3" in f for f in report["failures"])
    assert any("backup" in f.lower() for f in report["failures"])


def test_vault_key_resolved_from_exactly_one_source(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    # Both sources present: warn (one is redundant), never silently ignored.
    env["ERP_VAULT_KEY"] = "from-environment"
    report = operational_readiness.evaluate(env)
    assert report["ready"] is True
    assert report["configuration"]["ERP_VAULT_KEY"]["status"] == "warn"

    # Neither source: a hard failure - first-use key minting is a staging-only path.
    vault = Path(env["ERP_VAULT_PATH"])
    (vault.parent / (vault.name + ".vault.key")).unlink()
    del env["ERP_VAULT_KEY"]
    report = operational_readiness.evaluate(env)
    assert report["ready"] is False
    assert "ERP_VAULT_KEY" in "\n".join(report["failures"])


def test_api_docs_enabled_is_a_failure(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    env["ERP_DOCS_ENABLED"] = "true"
    report = operational_readiness.evaluate(env)
    assert report["ready"] is False
    assert "ERP_DOCS_ENABLED" in "\n".join(report["failures"])
    assert "STAGING_SECURITY_CONFIGURATION" in report["configuration"]["ERP_DOCS_ENABLED"]["remediation"]


def test_non_loopback_bind_requires_trusted_proxies(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    env["ERP_HOST"] = "0.0.0.0"
    report = operational_readiness.evaluate(env)
    assert report["ready"] is False

    env["ERP_TRUSTED_PROXIES"] = "10.0.0.1"
    report = operational_readiness.evaluate(env)
    assert report["ready"] is True
    assert report["configuration"]["ERP_HOST"]["status"] == "warn"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode-bit enforcement only")
def test_unwritable_security_log_parent_fails(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    log_dir = Path(env["ERP_SECURITY_LOG_PATH"]).parent
    log_dir.chmod(0o555)
    try:
        report = operational_readiness.evaluate(env)
        assert report["ready"] is False
        assert "ERP_SECURITY_LOG_PATH" in "\n".join(report["failures"])
    finally:
        log_dir.chmod(0o755)


def test_posix_permission_gate_refuses_peer_readable_key_files(tmp_path, monkeypatch) -> None:
    """The POSIX mode gate must fail a peer-readable key file, naming the fix.

    The checker module's ``os`` binding is replaced with a posix-declaring shim
    (everything else delegated to the real module) so the gate - otherwise only
    a warn on Windows - is pinned cross-platform; on Linux CI the same gate is
    additionally exercised for real by every test that builds a complete
    configuration through ``_good_env`` (whose key files are 0600 for exactly
    this reason).
    """

    class _PosixOS:
        name = "posix"

        def __getattr__(self, item: str):
            return getattr(os, item)

    env = _good_env(tmp_path)
    audit_key = Path(env["ERP_DB_PATH"]).with_name(Path(env["ERP_DB_PATH"]).name + ".audit.key")
    monkeypatch.setattr(operational_readiness, "os", _PosixOS())
    audit_key.chmod(0o664)
    report = operational_readiness.evaluate(env)
    check = report["permissions"]["permissions:erp.sqlite3.audit.key"]
    assert check["status"] == "fail"
    assert "chmod 600" in check["remediation"]


# ----------------------------------------------------------------------
# 2. Checkpoint freshness and shape (integrity is the verify command's job)
# ----------------------------------------------------------------------
def test_fresh_checkpoint_passes_and_stale_checkpoint_fails(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"head_hash": "ab" * 32, "records": 1}), encoding="utf-8")

    report = operational_readiness.evaluate(_good_env(tmp_path), checkpoint=checkpoint, max_checkpoint_age_days=1)
    assert report["checkpoint"]["checkpoint:freshness"]["status"] == "pass"

    # A two-day-old checkpoint with a one-day limit must fail, naming the age.
    old = checkpoint.stat().st_mtime - 2.5 * 86400
    os.utime(checkpoint, (old, old))
    report = operational_readiness.evaluate(_good_env(tmp_path), checkpoint=checkpoint, max_checkpoint_age_days=1)
    assert report["ready"] is False
    assert report["checkpoint"]["checkpoint:freshness"]["status"] == "fail"
    assert "days old" in report["checkpoint"]["checkpoint:freshness"]["detail"]


def test_malformed_and_schemaless_checkpoints_are_rejected(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    report = operational_readiness.evaluate(env, checkpoint=broken)
    assert report["checkpoint"]["checkpoint:parse"]["status"] == "fail"

    schemaless = tmp_path / "schemaless.json"
    schemaless.write_text(json.dumps({"records": 3}), encoding="utf-8")
    report = operational_readiness.evaluate(env, checkpoint=schemaless)
    assert report["checkpoint"]["checkpoint:schema"]["status"] == "fail"


def test_missing_checkpoint_file_names_the_export_command(tmp_path: Path) -> None:
    report = operational_readiness.evaluate(
        _good_env(tmp_path), checkpoint=tmp_path / "absent.json"
    )
    assert report["checkpoint"]["checkpoint:exists"]["status"] == "fail"
    assert "--checkpoint-export" in report["checkpoint"]["checkpoint:exists"]["remediation"]


def test_checkpoint_verify_command_carries_the_runbook_flags(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"head_hash": "ab" * 32}), encoding="utf-8")
    commands = operational_readiness._verification_commands(_good_env(tmp_path), checkpoint, 1, None)
    verify = next(c for c in commands if c.name == "checkpoint-verify")
    assert "--checkpoint-verify" in verify.argv
    assert "--max-checkpoint-age-days" in verify.argv
    assert "monitor" in verify.argv


# ----------------------------------------------------------------------
# 3. Presence vs functionality: commands listed, executed only on request
# ----------------------------------------------------------------------
def test_verification_commands_are_listed_but_not_executed_by_default(tmp_path: Path) -> None:
    report = operational_readiness.evaluate(_good_env(tmp_path))
    names = {c["name"] for c in report["verification_commands"]}
    assert {"dependency-graph", "hash-lock", "key-status"} <= names
    for command in report["verification_commands"]:
        assert command["executed"] is False
        assert command["passed"] is None


def test_run_checks_executes_safe_commands_and_folds_exit_codes(tmp_path: Path) -> None:
    calls = []

    def runner(argv, cwd):
        calls.append(argv)
        return 0, "ok"

    env = _good_env(tmp_path)
    report = operational_readiness.evaluate(env, run_checks=True, runner=runner)
    executed = {c["name"] for c in report["verification_commands"] if c["executed"]}
    assert {"dependency-graph", "hash-lock", "key-status"} <= executed
    assert calls, "the injected runner must have been used, not real subprocesses"
    assert report["ready"] is True


def test_failed_verification_is_a_nonzero_exit_with_actionable_output(tmp_path: Path) -> None:
    def runner(argv, cwd):
        # Fail only the hash-lock drift check, exactly like a real drift would.
        if "generate_hash_lock.py" in argv[-2]:
            return 1, "DRIFT: requirements.lock.hashes.txt disagrees with requirements.lock.txt"
        return 0, "ok"

    report = operational_readiness.evaluate(_good_env(tmp_path), run_checks=True, runner=runner)
    assert report["ready"] is False
    failure = next(f for f in report["failures"] if "verification:hash-lock" in f)
    assert "DRIFT" in failure


def test_unavailable_command_fails_only_when_checks_are_requested(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    env["ERP_DB_PATH"] = ""
    env["ERP_VAULT_PATH"] = ""
    report = operational_readiness.evaluate(env)
    key_status = next(c for c in report["verification_commands"] if c["name"] == "key-status")
    assert key_status["available"] is False
    assert report["ready"] is False  # the missing DB/VAULT config already fails

    # With a different config, an unavailable wheelhouse path is a named failure.
    env2 = _good_env(tmp_path)
    report = operational_readiness.evaluate(env2, wheelhouse=tmp_path / "no-such-wh", run_checks=True)
    assert any("verification:wheelhouse-verify" in f for f in report["failures"])
    assert "no-such-wh" in "\n".join(report["failures"])


# ----------------------------------------------------------------------
# 4. Exit-code contract and evidence honesty
# ----------------------------------------------------------------------
def test_usage_error_exits_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        operational_readiness.main(["--max-checkpoint-age-days", "-1"])
    assert excinfo.value.code == 2


def test_main_exit_codes_0_and_1(tmp_path: Path, monkeypatch, capsys) -> None:
    # Not ready: the configured database path does not exist and the vault is unset.
    assert operational_readiness.main(["--db", str(tmp_path / "nope.sqlite3")]) == 1

    # Ready: a complete configuration supplied through the environment.
    for key, value in _good_env(tmp_path).items():
        if value:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    assert operational_readiness.main([]) == 0
    capsys.readouterr()


def test_deployment_evidence_is_reported_as_required_never_as_present(tmp_path: Path) -> None:
    report = operational_readiness.evaluate(_good_env(tmp_path))
    items = {entry["item"] for entry in report["deployment_evidence_required"]}
    assert {
        "off-host checkpoint storage",
        "scheduler installed and running",
        "alert delivery",
        "disaster-recovery drill at production scale",
        "KMS/HSM custody (or an approved equivalent)",
        "publisher provenance (PEP 740/sigstore)",
        "representative OCR evaluation corpus",
        "professional approvals (accounting, tax, data protection)",
    } <= items
    for entry in report["deployment_evidence_required"]:
        # Evidence items are requirements, not results: no status field, no pass.
        assert set(entry) == {"item", "why", "acceptance"}


def test_windows_permission_checks_are_honest_about_acls(tmp_path: Path) -> None:
    env = _good_env(tmp_path)
    report = operational_readiness.evaluate(env)
    if os.name == "nt":
        key_perm = report["permissions"]["permissions:erp.sqlite3.audit.key"]
        assert key_perm["status"] == "warn"
        assert "NTFS ACL" in key_perm["detail"] or "NTFS ACL" in key_perm["remediation"]
