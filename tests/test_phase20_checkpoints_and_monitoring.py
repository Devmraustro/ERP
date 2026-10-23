# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 20 — Off-host checkpoints (S4) and operational monitoring (D6).

A hash chain proves the records it contains are internally consistent. It cannot
prove that records were not *removed from the end*: a truncated ledger is a valid
prefix, so it verifies cleanly. The only defence is a head hash retained outside
the system being checked.

That defence is worthless if an unusable checkpoint is treated as "nothing to
compare". These tests were written against the previous behaviour, where:

* an empty dict, a missing record count, a different ledger format and a newer
  schema all returned ``valid: True`` — the operator was told truncation
  detection was in force when it was not;
* a non-integer count raised ``ValueError`` and a negative one raised
  ``IndexError``, which say nothing about the actual problem.

The monitoring half asserts the other operational contract: `monitor` reports
overdue keys, unfinished rotations, custody drift, provider limits and checkpoint
state, exits 3 when unhealthy, never prints key material, and is read-only.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dz_erp.database import ERPDatabase
from dz_erp.key_rotation import KeyConfigurationError
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.rotate_keys import _verify_ledger_without_creating_keys, build_monitoring_report, main
from dz_erp.rotation_audit import (
    LEDGER_FORMAT,
    LEDGER_GENESIS_HASH,
    LEDGER_SCHEMA_VERSION,
    RotationLedger,
    RotationLedgerCheckpointError,
    RotationLedgerError,
    RotationLedgerTruncatedError,
    load_checkpoint,
    validate_checkpoint,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="mode/fd semantics are POSIX")


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def _ledger(tmp_path: Path, operations: int = 2) -> RotationLedger:
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    for index in range(operations):
        operation_id = f"op-{index}"
        ledger.append(operation="VAULT_KEY_ROTATION", phase="STARTED", store="vault", operation_id=operation_id)
        ledger.append(
            operation="VAULT_KEY_ROTATION", phase="COMPLETED", store="vault",
            operation_id=operation_id, counts={"completed": 1},
        )
    return ledger


def _deployment(tmp_path: Path) -> tuple:
    vault = PrivateDocumentVault(tmp_path / "vault")
    for index in range(2):
        vault.store_bytes(1, f"{index:064x}", f"DOC {index}".encode())
    db = ERPDatabase(str(tmp_path / "erp.sqlite3"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO companies (id, code, name, country_code, created_at)"
            " VALUES (1, 'DZ1', 'Synthetic', 'DZ', '2026-01-01')"
        )
        db.append_audit_event(
            conn, company_id=1, actor_user_id=None, action_type="T",
            target_model="m", target_id="1", after_state={},
        )
    return vault, db


# ----------------------------------------------------------------------
# 1. Honest checkpoints
# ----------------------------------------------------------------------
def test_valid_checkpoint_matches_and_reports_growth(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.export_checkpoint(tmp_path / "cp.json")
    loaded = load_checkpoint(tmp_path / "cp.json")

    result = ledger.verify_against_checkpoint(loaded)
    assert result["valid"] is True
    assert result["checkpoint_matched"] is True
    assert result["records_appended_since_checkpoint"] == 0

    # Growth after the checkpoint is normal and must not be flagged.
    ledger.append(operation="VAULT_KEY_ROTATION", phase="STARTED", store="vault", operation_id="later")
    grown = ledger.verify_against_checkpoint(loaded)
    assert grown["checkpoint_matched"] is True
    assert grown["records_appended_since_checkpoint"] == 1


def test_repeated_export_and_verification_is_stable(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    first = ledger.export_checkpoint(tmp_path / "cp1.json")
    second = ledger.export_checkpoint(tmp_path / "cp2.json")
    assert first["head_hash"] == second["head_hash"]
    assert first["record_count"] == second["record_count"]

    for _ in range(3):
        assert ledger.verify_against_checkpoint(load_checkpoint(tmp_path / "cp1.json"))["valid"] is True
        assert ledger.verify_against_checkpoint(load_checkpoint(tmp_path / "cp2.json"))["valid"] is True


# ----------------------------------------------------------------------
# 2. Ledger states the checkpoint must catch
# ----------------------------------------------------------------------
def test_tail_truncation_after_the_checkpoint_is_detected(tmp_path: Path) -> None:
    """The case no hash chain can catch on its own."""
    ledger = _ledger(tmp_path, operations=3)
    checkpoint = ledger.export_checkpoint(tmp_path / "cp.json")
    path = tmp_path / "vault.rotation-ledger.jsonl"

    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-2]) + "\n")  # drop the last two records

    with pytest.raises(RotationLedgerTruncatedError) as excinfo:
        ledger.verify_against_checkpoint(checkpoint)
    assert excinfo.value.expected_count == 6
    assert excinfo.value.actual_count == 4

    # And the surviving prefix still verifies - which is exactly why the
    # checkpoint is needed.
    assert ledger.verify()["valid"] is True


def test_modified_record_after_the_checkpoint_is_detected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    checkpoint = ledger.export_checkpoint(tmp_path / "cp.json")
    path = tmp_path / "vault.rotation-ledger.jsonl"

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[-1]["counts"] = {"completed": 999}
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))

    result = ledger.verify_against_checkpoint(checkpoint)
    assert result["valid"] is False
    assert result["reason"] == "RECORD_TAMPERED"


def test_deleted_middle_record_is_detected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, operations=3)
    checkpoint = ledger.export_checkpoint(tmp_path / "cp.json")
    path = tmp_path / "vault.rotation-ledger.jsonl"
    lines = path.read_text().splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n")

    result = ledger.verify_against_checkpoint(checkpoint)
    assert result["valid"] is False
    # Removing a middle record breaks continuity, which the chain itself detects;
    # the checkpoint is not what catches this one.
    assert result["reason"] in {"SEQUENCE_GAP", "PREV_HASH_MISMATCH"}
    assert ledger.verify()["valid"] is False


def test_reordered_records_are_detected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    checkpoint = ledger.export_checkpoint(tmp_path / "cp.json")
    path = tmp_path / "vault.rotation-ledger.jsonl"
    lines = path.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n")

    result = ledger.verify_against_checkpoint(checkpoint)
    assert result["valid"] is False
    assert result["reason"] in {"SEQUENCE_GAP", "PREV_HASH_MISMATCH"}


# ----------------------------------------------------------------------
# 3. Checkpoint documents that cannot be trusted
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "document,expected",
    [
        ({}, "empty or not a mapping"),
        ([], "empty or not a mapping"),
        ("not-a-dict", "empty or not a mapping"),
        ({"format": LEDGER_FORMAT, "schema": LEDGER_SCHEMA_VERSION, "head_hash": "0" * 64},
         "missing required field"),
        ({"schema": 1, "record_count": 1, "head_hash": "0" * 64}, "missing required field"),
        ({"format": "some-other-ledger", "schema": 1, "record_count": 1, "head_hash": "0" * 64},
         "produced by a different ledger"),
        ({"format": LEDGER_FORMAT, "schema": 99, "record_count": 1, "head_hash": "0" * 64},
         "this build reads schema"),
        ({"format": LEDGER_FORMAT, "schema": 1, "record_count": "two", "head_hash": "0" * 64},
         "not an integer"),
        ({"format": LEDGER_FORMAT, "schema": 1, "record_count": -5, "head_hash": "0" * 64},
         "negative"),
        ({"format": LEDGER_FORMAT, "schema": 1, "record_count": 1, "head_hash": "not-a-hash"},
         "64-character lowercase hex"),
        ({"format": LEDGER_FORMAT, "schema": 1, "record_count": 0, "head_hash": "a" * 64},
         "genesis hash"),
    ],
)
def test_untrustworthy_checkpoints_are_rejected(document, expected: str) -> None:
    with pytest.raises(RotationLedgerCheckpointError, match=expected):
        validate_checkpoint(document)


def test_verify_against_checkpoint_validates_before_comparing(tmp_path: Path) -> None:
    """Previously an empty document returned valid=True."""
    ledger = _ledger(tmp_path)
    with pytest.raises(RotationLedgerCheckpointError):
        ledger.verify_against_checkpoint({})


def test_missing_checkpoint_file_names_the_consequence(tmp_path: Path) -> None:
    with pytest.raises(RotationLedgerCheckpointError, match="cannot be detected"):
        load_checkpoint(tmp_path / "absent.json")


def test_malformed_checkpoint_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cp.json"
    path.write_text("{not json")
    with pytest.raises(RotationLedgerCheckpointError, match="not valid JSON"):
        load_checkpoint(path)


def test_checkpoint_of_an_empty_ledger_uses_the_genesis_hash(tmp_path: Path) -> None:
    ledger = RotationLedger.open(tmp_path / "empty.rotation-ledger.jsonl")
    checkpoint = ledger.export_checkpoint(tmp_path / "cp.json")
    assert checkpoint["record_count"] == 0
    assert checkpoint["head_hash"] == LEDGER_GENESIS_HASH
    assert ledger.verify_against_checkpoint(load_checkpoint(tmp_path / "cp.json"))["checkpoint_matched"] is True


# ----------------------------------------------------------------------
# 4. Checkpoint with an unavailable verification key
# ----------------------------------------------------------------------
def test_checkpoint_refused_when_the_ledger_key_is_missing(tmp_path: Path, capsys) -> None:
    """'Cannot verify' must not be reported as 'verified, here is a checkpoint'."""
    # The vault must exist, otherwise the deployment guard fires first and this
    # test would be asserting the wrong refusal (fixture defect, not app defect).
    _deployment(tmp_path)
    _ledger(tmp_path)
    for name in ("vault.rotation-ledger.rotation.key", "vault.rotation-ledger.jsonl.rotation.keys.json"):
        candidate = tmp_path / name
        if candidate.exists():
            candidate.unlink()

    with pytest.raises(KeyConfigurationError, match="restore the key"):
        RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl", create=False)

    code = main(["monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-export", str(tmp_path / "cp.json")])
    assert code == 2, "a refused checkpoint export is a configuration error, not a success"
    assert "REFUSED" in capsys.readouterr().err
    assert not (tmp_path / "cp.json").exists(), "a checkpoint must not be written from an unverifiable ledger"


def test_checkpoint_export_refused_when_a_key_version_is_unavailable(tmp_path: Path) -> None:
    """A missing *version* (not the whole keyring) must also block export."""
    ledger = _ledger(tmp_path)
    registry = tmp_path / "vault.rotation-ledger.jsonl.rotation.keys.json"
    ledger.keyring.add_version(b"k" * 32)
    ledger.keyring.save(registry)
    ledger.append(operation="VAULT_KEY_ROTATION", phase="COMPLETED", store="vault", operation_id="rot")

    # Destroy version 2, keeping a valid keyring with version 1 active: the records
    # signed with v2 can no longer be checked, which is not the same as tampering.
    payload = json.loads(registry.read_text())
    del payload["keys"]["2"]
    payload["keys"]["1"]["status"] = "active"
    payload["current_version"] = 1
    registry.write_text(json.dumps(payload))

    reopened = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl", create=False)
    state = reopened.verify()
    assert state["valid"] is False
    assert state["reason"] == "LEDGER_KEY_UNAVAILABLE"
    assert state["missing_key_version"] == 2

    with pytest.raises(RotationLedgerError, match="unavailable"):
        reopened.export_checkpoint(tmp_path / "cp.json")
    assert not (tmp_path / "cp.json").exists()


def test_read_only_verification_never_generates_a_replacement_key(tmp_path: Path) -> None:
    """A monitor that regenerates a lost key destroys the evidence and blames the operator."""
    _ledger(tmp_path)
    key = tmp_path / "vault.rotation-ledger.rotation.key"
    key.unlink()

    state = _verify_ledger_without_creating_keys(tmp_path / "vault.rotation-ledger.jsonl")

    assert state["valid"] is False
    assert state["key_unavailable"] is True
    assert state["reason"] == "LEDGER_KEY_UNAVAILABLE"
    assert not key.exists(), "verification must not write key material"


# ----------------------------------------------------------------------
# 4b. The scheduling artefacts must match the CLI they call
# ----------------------------------------------------------------------
OPS = Path(__file__).resolve().parent.parent / "ops"


def _parse_unit(path: Path) -> dict:
    """Parse a unit file, joining backslash continuations as systemd does.

    systemd.syntax: "Lines ending in a backslash are concatenated with the
    following line while reading and the backslash is replaced by a space
    character." A parser that ignores this rejects valid files and accepts
    values that only look complete on their first line.
    """
    directives: dict = {}
    section = None
    pending = ""
    for raw in path.read_text().splitlines():
        assert "\t" not in raw, f"{path.name}: tabs are not valid in unit files"
        line = raw.strip()
        if pending:
            # A comment block after a continuation is ignored; the continued value
            # resumes at the next real line.
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            line = pending + " " + line
            pending = ""
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            directives.setdefault(section, [])
            continue
        assert section, f"{path.name}: directive outside any section: {line!r}"
        key, _, value = line.partition("=")
        if value.rstrip().endswith("\\"):
            pending = (key.strip() + "=" + value.rstrip()[:-1]).rstrip()
            continue
        directives[section].append((key.strip(), value.strip()))
    assert not pending, f"{path.name}: a continuation line is never completed"
    return directives


def test_the_unit_parser_joins_continuations_like_systemd(tmp_path: Path) -> None:
    """Guard the guard: a naive parser once made a valid unit look broken."""
    unit = tmp_path / "x.service"
    unit.write_text(
        "[Service]\n"
        "ExecStart=/bin/echo one \\\n"
        "    two \\\n"
        "# ignored comment after a continuation\n"
        "    three\n"
    )
    parsed = _parse_unit(unit)
    assert parsed["Service"] == [("ExecStart", "/bin/echo one two three")]


def test_ops_units_exist_and_parse() -> None:
    expected = {
        "dz-erp-checkpoint-export.service": "Service",
        "dz-erp-checkpoint-export.timer": "Timer",
        "dz-erp-checkpoint-verify.service": "Service",
        "dz-erp-checkpoint-verify.timer": "Timer",
    }
    for name, section in expected.items():
        path = OPS / name
        assert path.is_file(), f"{name} is referenced by the runbook but missing"
        assert section in _parse_unit(path), f"{name} has no [{section}] section"


def test_timers_point_at_services_that_export_and_verify() -> None:
    for timer_name, service_name, flag in (
        ("dz-erp-checkpoint-export.timer", "dz-erp-checkpoint-export.service", "--checkpoint-export"),
        ("dz-erp-checkpoint-verify.timer", "dz-erp-checkpoint-verify.service", "--checkpoint-verify"),
    ):
        timer = _parse_unit(OPS / timer_name)
        units = [value for key, value in timer["Timer"] if key == "Unit"]
        assert units == [service_name], f"{timer_name} must drive {service_name}"

        service = _parse_unit(OPS / service_name)
        commands = " ".join(value for key, value in service["Service"] if key == "ExecStart")
        environment: dict = {}
        for key, value in service["Service"]:
            if key == "Environment":
                name, _, setting = value.partition("=")
                environment[name] = setting
        assert "dz_erp.rotate_keys monitor" in commands, service_name
        assert flag in commands, f"{service_name} must call {flag}"
        # A checkpoint inside the state directory would be destroyed with the ledger,
        # so the configured destination must sit outside it.
        destination = environment.get("CHECKPOINT_DIR", "")
        state = environment.get("ERP_STATE", "")
        assert destination, f"{service_name} must declare CHECKPOINT_DIR"
        assert state and not destination.startswith(state), (
            f"{service_name} writes checkpoints inside ERP_STATE={state!r}; that is not off-host storage"
        )


def _systemd_analyze() -> str | None:
    return shutil.which("systemd-analyze")


@pytest.mark.skipif(_systemd_analyze() is None, reason="systemd-analyze is not installed on this platform")
def test_units_pass_systemd_analyze_verify(tmp_path: Path) -> None:
    """Validate with systemd's own parser, not only with ours.

    `systemd-analyze verify` exits 0 even when it reports unknown keys, so the exit
    code alone proves nothing: the assertion is that it produced no output at all.
    """
    staged = tmp_path / "units"
    staged.mkdir()
    for unit in sorted(OPS.glob("*.service")) + sorted(OPS.glob("*.timer")):
        (staged / unit.name).write_bytes(unit.read_bytes())

    result = subprocess.run(
        [_systemd_analyze(), "verify", *[str(staged / unit.name) for unit in sorted(staged.iterdir())]],
        capture_output=True, text=True, check=False,
    )
    # A misspelt directive is reported as "Unknown key ... ignoring" and is *not*
    # fatal to `verify`, so it must be treated as a failure here. This assertion
    # previously filtered those lines out, which made the test pass on a unit that
    # systemd was silently ignoring a directive in - caught by mutation testing.
    combined = (result.stdout + result.stderr).strip()
    complaints = [
        line for line in combined.splitlines()
        if "Unknown key" in line or "Unknown section" in line
    ]
    assert not complaints, "systemd-analyze rejected the units:\n" + combined


def test_the_systemd_checker_would_catch_a_broken_unit(tmp_path: Path) -> None:
    """Guard the guard: if this passes trivially, the check above proves nothing."""
    if _systemd_analyze() is None:
        pytest.skip("systemd-analyze is not installed on this platform")
    broken = tmp_path / "broken.service"
    broken.write_text("[Service]\nNotARealDirective=yes\n")
    result = subprocess.run(
        [_systemd_analyze(), "verify", str(broken)], capture_output=True, text=True, check=False
    )
    assert "NotARealDirective" in (result.stdout + result.stderr)


def test_units_do_not_expand_unbraced_variables() -> None:
    for path in OPS.glob("*.service"):
        for section, entries in _parse_unit(path).items():
            for key, value in entries:
                if "$" in value:
                    assert "${" in value, f"{path.name}:{key} uses $VAR; systemd needs ${{VAR}}"


def test_checkpoint_cli_flags_exist() -> None:
    """The runbook and units are useless if the flags were renamed."""
    import subprocess
    import sys

    help_text = subprocess.run(
        [sys.executable, "-m", "dz_erp.rotate_keys", "monitor", "--help"],
        capture_output=True, text=True, cwd=OPS.parent, check=False,
    )
    assert help_text.returncode == 0, help_text.stderr
    for flag in ("--checkpoint-export", "--checkpoint-verify", "--max-checkpoint-age-days"):
        assert flag in help_text.stdout, f"{flag} is documented but not implemented"


# ----------------------------------------------------------------------
# 5. The monitor: contract, exit codes, and read-only behaviour
# ----------------------------------------------------------------------
def test_monitor_is_healthy_and_reports_provider_limits(tmp_path: Path, capsys) -> None:
    vault, db = _deployment(tmp_path)
    vault.rotate_key()

    code = main(["monitor", "--db", str(tmp_path / "erp.sqlite3"), "--vault", str(tmp_path / "vault")])
    out = capsys.readouterr().out
    report = json.loads(out)

    assert code == 0
    assert report["healthy"] is True
    assert report["custody"]["equivalent_to_hsm"] is False
    assert report["key_provider"]["mode"] == "file"
    assert report["vault"]["keys_overdue"] == []
    assert report["vault"]["unfinished_operations"] == []


def test_monitor_exits_three_when_a_key_is_overdue(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    payload = json.loads((tmp_path / "vault.vault.keys.json").read_text())
    for entry in payload["keys"].values():
        entry["created_at"] = "2024-01-01T00:00:00+00:00"
    (tmp_path / "vault.vault.keys.json").write_text(json.dumps(payload))
    os.chmod(tmp_path / "vault.vault.keys.json", 0o600)

    code = main(["monitor", "--vault", str(tmp_path / "vault"), "--max-key-age-days", "365"])
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["healthy"] is False
    assert report["vault"]["keys_overdue"]
    assert any("rotation age" in note for note in report["blocking_notes"])


def test_monitor_exits_three_when_a_rotation_never_finished(tmp_path: Path, capsys) -> None:
    _vault, _db = _deployment(tmp_path)
    ledger = RotationLedger.open(tmp_path / "vault.rotation-ledger.jsonl")
    ledger.append(operation="VAULT_KEY_ROTATION", phase="STARTED", store="vault", operation_id="killed")

    code = main(["monitor", "--vault", str(tmp_path / "vault")])
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["vault"]["unfinished_operations"] == ["killed"]


def test_monitor_reports_a_missing_key_file_as_unhealthy(tmp_path: Path, capsys) -> None:
    _vault, _db = _deployment(tmp_path)
    (tmp_path / "vault.vault.key").unlink()

    code = main(["monitor", "--vault", str(tmp_path / "vault")])
    captured = capsys.readouterr()
    assert code != 0
    assert "Traceback" not in captured.err, "a missing key must be reported, not crash"


def test_monitor_reports_a_lost_ledger_key_as_key_loss_not_tampering(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    key = tmp_path / "vault.rotation-ledger.rotation.key"
    key.unlink()

    code = main(["monitor", "--vault", str(tmp_path / "vault")])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["healthy"] is False
    assert report["ledger"]["reason"] == "LEDGER_KEY_UNAVAILABLE"
    assert any("key is unavailable" in note for note in report["blocking_notes"])
    assert not any("treat as tampering" in note for note in report["blocking_notes"])
    assert not key.exists(), "a read-only report must not recreate the key"


def test_monitor_does_not_initialise_a_missing_deployment(tmp_path: Path, capsys) -> None:
    """A mistyped --vault must be reported, never turned into a fresh vault.

    Constructing `PrivateDocumentVault` mints a key on first use. Monitoring used to
    do that, so a typo produced a new key, a new directory and a healthy-looking v1
    deployment - a read-only command silently writing key material.
    """
    missing = tmp_path / "typo" / "vault"

    code = main(["monitor", "--vault", str(missing)])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["healthy"] is False
    assert report["vault"]["configured"] is False
    assert report["vault"]["reason"] == "VAULT_NOT_CONFIGURED"
    assert any("No vault is configured" in note for note in report["blocking_notes"])
    assert not missing.parent.exists(), "monitoring created a directory tree"
    assert not list(tmp_path.rglob("*.vault.key")) and not list(tmp_path.rglob("*.vault.keys.json"))


def test_monitor_refuses_missing_vault_even_when_environment_key_is_set(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """An env key must not make a mistyped, absent vault look healthy."""
    from cryptography.fernet import Fernet

    missing = tmp_path / "typo" / "vault"
    monkeypatch.setenv("ERP_VAULT_KEY", Fernet.generate_key().decode("ascii"))

    code = main(["monitor", "--vault", str(missing)])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["healthy"] is False
    assert report["vault"]["configured"] is False
    assert report["vault"]["reason"] == "VAULT_NOT_CONFIGURED"
    assert not missing.exists(), "monitoring created a missing vault despite the environment key"
    assert not missing.parent.exists(), "monitoring created the parent directory tree"
    assert not list(tmp_path.rglob("*.vault.key"))
    assert not list(tmp_path.rglob("*.vault.keys.json"))


def test_monitor_refuses_missing_or_uninitialised_database_without_writing(tmp_path: Path, capsys, monkeypatch) -> None:
    """Monitoring must not create a DB/schema/key after a typo or empty file."""
    monkeypatch.delenv("ERP_AUDIT_HMAC_SECRET", raising=False)
    missing = tmp_path / "missing-parent" / "erp.sqlite3"

    code = main(["monitor", "--db", str(missing)])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["healthy"] is False
    assert report["audit"]["configured"] is False
    assert report["audit"]["reason"] == "DB_NOT_CONFIGURED"
    assert not missing.exists()
    assert not missing.parent.exists()
    assert not Path(str(missing) + ".audit.key").exists()
    assert not Path(str(missing) + ".audit.keys.json").exists()

    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    code = main(["monitor", "--db", str(empty)])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["healthy"] is False
    assert report["audit"]["configured"] is False
    assert report["audit"]["reason"] == "DB_NOT_CONFIGURED"
    assert empty.read_bytes() == b"", "monitoring initialized an existing empty database file"
    assert not Path(str(empty) + ".audit.key").exists()
    assert not Path(str(empty) + ".audit.keys.json").exists()


def test_monitor_does_not_regenerate_a_missing_audit_key_for_an_empty_database(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """An empty DB with a lost key is not permission for a read-only command to mint one."""
    monkeypatch.delenv("ERP_AUDIT_HMAC_SECRET", raising=False)
    db_path = tmp_path / "erp.sqlite3"
    ERPDatabase(str(db_path))
    key_path = Path(str(db_path) + ".audit.key")
    assert key_path.exists()
    key_path.unlink()

    code = main(["monitor", "--db", str(db_path)])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["healthy"] is False
    assert report["audit"]["configured"] is False
    assert report["audit"]["reason"] == "AUDIT_KEY_UNAVAILABLE"
    assert not key_path.exists(), "monitoring recreated a missing audit key"


@pytest.mark.parametrize(
    "operation_args",
    [
        ["vault-rotate", "--dry-run"],
        ["vault-rollback", "--version", "1"],
        ["vault-remove", "--version", "1", "--i-have-verified-retained-copies"],
    ],
)
def test_vault_key_operations_refuse_missing_store_without_initializing(
    tmp_path: Path, capsys, monkeypatch, operation_args: list[str]
) -> None:
    """Even dry-run/rollback/removal must not turn a typo into a new vault."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ERP_VAULT_KEY", Fernet.generate_key().decode("ascii"))
    missing = tmp_path / "typo" / "vault"
    command = [operation_args[0], "--vault", str(missing), *operation_args[1:]]

    code = main(command)
    captured = capsys.readouterr()

    assert code == 3
    assert "VAULT:" in captured.err
    assert not missing.exists()
    assert not missing.parent.exists()
    assert not list(tmp_path.rglob("*.vault.key"))
    assert not list(tmp_path.rglob("*.vault.keys.json"))


@pytest.mark.parametrize(
    "operation_args",
    [
        ["audit-rotate", "--dry-run"],
        ["audit-rollback", "--version", "1"],
        ["audit-remove", "--version", "1", "--i-have-verified-retained-copies"],
    ],
)
def test_audit_key_operations_refuse_missing_store_without_initializing(
    tmp_path: Path, capsys, monkeypatch, operation_args: list[str]
) -> None:
    """Key commands must not create a database/schema/key after a path typo."""
    monkeypatch.delenv("ERP_AUDIT_HMAC_SECRET", raising=False)
    missing = tmp_path / "typo" / "erp.sqlite3"
    command = [operation_args[0], "--db", str(missing), *operation_args[1:]]

    code = main(command)
    captured = capsys.readouterr()

    assert code == 3
    assert "DB:" in captured.err
    assert not missing.exists()
    assert not missing.parent.exists()
    assert not Path(str(missing) + ".audit.key").exists()
    assert not Path(str(missing) + ".audit.keys.json").exists()


def test_status_refuses_a_missing_database_without_initializing_it(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "typo" / "erp.sqlite3"

    code = main(["status", "--db", str(missing)])
    captured = capsys.readouterr()

    assert code == 3
    assert "DB:" in captured.err
    assert not missing.exists()
    assert not missing.parent.exists()
    assert not Path(str(missing) + ".audit.key").exists()


def test_status_refuses_a_missing_vault_even_when_environment_key_is_set(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from cryptography.fernet import Fernet

    missing = tmp_path / "typo" / "vault"
    monkeypatch.setenv("ERP_VAULT_KEY", Fernet.generate_key().decode("ascii"))

    code = main(["status", "--vault", str(missing)])
    captured = capsys.readouterr()

    assert code == 3
    assert "VAULT:" in captured.err
    assert not missing.exists()
    assert not missing.parent.exists()
    assert not list(tmp_path.rglob("*.vault.key"))
    assert not list(tmp_path.rglob("*.vault.keys.json"))


def test_monitor_refuses_an_empty_vault_directory_without_a_key(tmp_path: Path, capsys) -> None:
    """The directory existing is not the same as a deployment being configured."""
    root = tmp_path / "vault"
    root.mkdir()

    code = main(["monitor", "--vault", str(root)])
    report = json.loads(capsys.readouterr().out)

    assert code == 3
    assert report["vault"]["configured"] is False
    assert not (tmp_path / "vault.vault.key").exists(), "a key was minted from a monitoring run"


def test_monitor_still_works_on_a_configured_deployment(tmp_path: Path) -> None:
    """The guard must not break the normal case."""
    vault, _db = _deployment(tmp_path)
    report = build_monitoring_report(vault_path=str(tmp_path / "vault"))
    assert report["vault"]["configured"] is not False
    assert "root" in report["vault"]


def test_checkpoint_export_refuses_a_missing_deployment(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "typo" / "vault"
    destination = tmp_path.parent / (tmp_path.name + "-offhost") / "cp.json"

    code = main(["monitor", "--vault", str(missing), "--checkpoint-export", str(destination)])
    captured = capsys.readouterr()

    assert code == 3
    assert "VAULT:" in captured.err
    assert not destination.exists()
    assert not missing.parent.exists()


def test_monitor_does_not_mutate_key_state(tmp_path: Path) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    before = {
        name: (tmp_path / name).read_bytes()
        for name in ("vault.vault.keys.json", "vault.vault.key")
    }
    for _ in range(3):
        build_monitoring_report(vault_path=str(tmp_path / "vault"))
    after = {
        name: (tmp_path / name).read_bytes()
        for name in ("vault.vault.keys.json", "vault.vault.key")
    }
    assert before == after, "monitoring must be read-only"
    assert vault.active_key_version == 2


def test_monitor_output_contains_no_key_material(tmp_path: Path, capsys) -> None:
    vault, db = _deployment(tmp_path)
    vault.rotate_key()
    db.rotate_audit_key()

    main(["monitor", "--db", str(tmp_path / "erp.sqlite3"), "--vault", str(tmp_path / "vault")])
    out = capsys.readouterr().out

    for version in vault.keyring.versions:
        assert vault.keyring.material_for(version).decode() not in out
    for version in db.audit_keyring.versions:
        assert db.audit_keyring.material_for(version).hex() not in out
        assert db.audit_keyring.material_for(version).decode(errors="ignore") not in out
    assert "material_b64" not in out


# ----------------------------------------------------------------------
# 6. Checkpoint integration through the CLI
# ----------------------------------------------------------------------
def test_cli_export_then_verify_round_trip(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    checkpoint_path = tmp_path.parent / (tmp_path.name + "-offhost") / "cp.json"

    code = main([
        "monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-export", str(checkpoint_path),
    ])
    capsys.readouterr()
    assert code == 0
    assert checkpoint_path.exists()

    code = main([
        "monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-verify", str(checkpoint_path),
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["checkpoint_verification"]["checkpoint_matched"] is True


def test_cli_reports_truncation_as_unhealthy(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    checkpoint_path = tmp_path.parent / (tmp_path.name + "-offhost") / "cp.json"
    main(["monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-export", str(checkpoint_path)])
    capsys.readouterr()

    ledger_path = tmp_path / "vault.rotation-ledger.jsonl"
    lines = ledger_path.read_text().splitlines()
    ledger_path.write_text("\n".join(lines[:-1]) + "\n")

    code = main([
        "monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-verify", str(checkpoint_path),
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["healthy"] is False
    assert report["checkpoint_verification"]["reason"] == "CHECKPOINT_TRUNCATED"
    assert any("missing" in note.lower() for note in report["blocking_notes"])


@pytest.mark.parametrize("location", ["beside", "inside_vault"])
def test_cli_refuses_to_write_a_checkpoint_on_the_host_it_protects(tmp_path: Path, capsys, location: str) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    destination = (tmp_path / "cp.json") if location == "beside" else (tmp_path / "vault" / "cp.json")

    code = main(["monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-export", str(destination)])

    assert code == 3
    assert "Refusing to write the checkpoint" in capsys.readouterr().err
    assert not destination.exists()


def test_cli_accepts_a_checkpoint_outside_the_ledger_directory(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    destination = tmp_path.parent / (tmp_path.name + "-elsewhere") / "cp.json"

    code = main(["monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-export", str(destination)])
    capsys.readouterr()
    assert code == 0
    assert destination.exists()


def test_cli_refuses_a_malformed_checkpoint_with_a_clear_message(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "something-else"}')

    code = main(["monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-verify", str(bad)])
    captured = capsys.readouterr()
    assert code == 3
    assert "CHECKPOINT:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_flags_a_stale_checkpoint(tmp_path: Path, capsys) -> None:
    vault, _db = _deployment(tmp_path)
    vault.rotate_key()
    checkpoint_path = tmp_path.parent / (tmp_path.name + "-offhost") / "cp.json"
    main(["monitor", "--vault", str(tmp_path / "vault"), "--checkpoint-export", str(checkpoint_path)])
    capsys.readouterr()

    document = json.loads(checkpoint_path.read_text())
    document["exported_at"] = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    checkpoint_path.write_text(json.dumps(document))

    code = main([
        "monitor", "--vault", str(tmp_path / "vault"),
        "--checkpoint-verify", str(checkpoint_path), "--max-checkpoint-age-days", "90",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert any("days old" in note for note in report["blocking_notes"])
    assert report["checkpoint_verification"]["age_days"] > 90


def test_cli_requires_a_vault_for_checkpoint_operations(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["monitor", "--db", str(tmp_path / "erp.sqlite3"), "--checkpoint-export", str(tmp_path / "cp.json")])


@posix_only
def test_exported_checkpoint_is_owner_only(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    path = tmp_path / "cp.json"
    ledger.export_checkpoint(path)
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_checkpoint_does_not_contain_key_material(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    checkpoint = ledger.export_checkpoint(tmp_path / "cp.json")
    rendered = json.dumps(checkpoint)
    for version in ledger.keyring.versions:
        assert ledger.keyring.material_for(version).decode() not in rendered
        assert ledger.keyring.material_for(version).hex() not in rendered
