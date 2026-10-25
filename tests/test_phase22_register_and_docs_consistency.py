# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 22 — the documents that claim things must stay consistent with the code.

Two failure modes are cheap to prevent and expensive to discover in an audit:

* a hand-maintained summary that disagrees with the table it summarises, so the
  register says "12 open" while the rows say nine;
* a runbook or deployment artefact that references a command, flag or file that
  no longer exists.

These tests make both mechanical. They are deliberately narrow: they check
*internal consistency*, not that any claim is true. Correctness of the claims is
the job of the tests cited in each row.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTER = REPO / "docs" / "RISK_REGISTER.md"

STATUSES = ["Open", "Mitigated", "Accepted", "Closed"]
SEVERITIES = ["Critical", "High", "Medium", "Low"]


def _counts_module():
    spec = importlib.util.spec_from_file_location("risk_counts", REPO / "tools" / "risk_register_counts.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summary_matches_the_rows() -> None:
    module = _counts_module()
    text = REGISTER.read_text(encoding="utf-8")
    rows = module.parse_rows(text)
    assert len(rows) >= 25, f"only {len(rows)} risk rows parsed; the table format may have changed"
    assert module.render(rows) in text, (
        "the summary table no longer matches the rows; run python tools/risk_register_counts.py --write"
    )


def test_every_row_has_a_status_from_the_vocabulary() -> None:
    module = _counts_module()
    rows = module.parse_rows(REGISTER.read_text(encoding="utf-8"))
    for row in rows:
        assert row["status"] in STATUSES, row
        assert row["severity"] in SEVERITIES, row


def test_the_counts_tool_detects_drift(tmp_path: Path) -> None:
    """A checker that never fails is not a checker."""
    module = _counts_module()
    text = REGISTER.read_text(encoding="utf-8")
    rows = module.parse_rows(text)
    damaged = text.replace(module.render(rows), module.render(rows).replace("| Medium | 3", "| Medium | 99", 1))
    assert damaged != text
    assert module.render(module.parse_rows(damaged)) not in damaged


def test_every_identifier_appears_exactly_once() -> None:
    module = _counts_module()
    rows = module.parse_rows(REGISTER.read_text(encoding="utf-8"))
    identifiers = [row["id"] for row in rows]
    assert len(identifiers) == len(set(identifiers)), "a risk identifier is duplicated"


def test_no_row_claims_a_file_that_does_not_exist() -> None:
    """Evidence pointers must resolve: a dead path is a claim nobody can check."""
    text = REGISTER.read_text(encoding="utf-8")
    module = _counts_module()
    rows = module.parse_rows(text)
    paths = set()
    for row in rows:
        line = next(line for line in text.splitlines() if line.startswith(f"| {row['id']} |"))
        paths.update(re.findall(r"`([A-Za-z0-9_./-]+\.(?:py|md|jsonl|txt|yml|yaml|service|timer))`", line))
    missing = sorted(path for path in paths if not (REPO / path).exists())
    assert not missing, f"risk register cites non-existent files: {missing}"


def test_runbook_and_units_are_present_and_cross_reference() -> None:
    runbook = REPO / "docs" / "KEY_LEDGER_CHECKPOINT_RUNBOOK.md"
    assert runbook.is_file()
    text = runbook.read_text(encoding="utf-8")
    for unit in ("dz-erp-checkpoint-export.service", "dz-erp-checkpoint-verify.timer"):
        assert unit in text, f"the runbook must name {unit}"
        assert (REPO / "ops" / unit).is_file(), f"{unit} is documented but missing"

    for flag in ("--checkpoint-export", "--checkpoint-verify", "--max-checkpoint-age-days"):
        assert flag in text


def test_documented_exit_codes_match_the_cli() -> None:
    """The runbook's exit-code table is what a scheduler author reads."""
    runbook = (REPO / "docs" / "KEY_LEDGER_CHECKPOINT_RUNBOOK.md").read_text(encoding="utf-8")
    cli = (REPO / "dz_erp" / "rotate_keys.py").read_text(encoding="utf-8")
    for code in ("`0`", "`1`", "`2`", "`3`"):
        assert code in runbook
    assert "return 0 if report[\"healthy\"] else 3" in cli, "the monitor no longer exits 3 when unhealthy"
    assert "return 2" in cli and "REFUSED:" in cli


def test_risk_register_does_not_call_an_unimplemented_control_mitigated() -> None:
    """Spot-check the two controls this phase relies on are real in code."""
    rotation = (REPO / "dz_erp" / "rotation_audit.py").read_text(encoding="utf-8")
    keys = (REPO / "dz_erp" / "rotate_keys.py").read_text(encoding="utf-8")
    ocr = (REPO / "dz_erp" / "intake_ocr.py").read_text(encoding="utf-8")

    assert "def validate_checkpoint" in rotation
    assert "def load_checkpoint" in rotation
    assert "CHECKPOINT_MISMATCH" in rotation
    assert "_refuse_local_checkpoint" in keys
    assert "create=False" in keys
    assert "ARABIC_RECOGNITION_HEAD_AVAILABLE = False" in ocr
    assert "def assess_review_route" in ocr


def test_dependency_integrity_claims_are_still_supported() -> None:
    """B2 is marked mitigated; the mechanisms it names must still be in the build."""
    lock = (REPO / "requirements.lock.hashes.txt").read_text(encoding="utf-8")
    assert lock.count("--hash=sha256:") > 1000
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow.count("--require-hashes") >= 2, "both CI jobs must install with hashes"
    assert "generate_hash_lock.py --check" in workflow


def test_key_provider_platform_specific_claims_are_explicit() -> None:
    """Security docs must distinguish POSIX mode checks from Windows ACL controls."""
    provider = (REPO / "dz_erp" / "key_provider.py").read_text(encoding="utf-8")
    threat = (REPO / "docs" / "KEY_CUSTODY_THREAT_MODEL.md").read_text(encoding="utf-8")
    staging = (REPO / "docs" / "STAGING_SECURITY_CONFIGURATION.md").read_text(encoding="utf-8")

    assert "FILE_FLAG_OPEN_REPARSE_POINT" in provider
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in provider
    assert "O_NOFOLLOW" in provider
    assert "FILE_FLAG_OPEN_REPARSE_POINT" in threat
    assert "mode bits are not access control" in threat
    assert "NTFS ACL" in staging
    assert "`/docs` and `/redoc` return 404 by default" in staging
    assert "ERP_DOCS_ENABLED=true" in staging


def test_backup_restore_rejects_preexisting_linked_destination_paths() -> None:
    source = (REPO / "dz_erp" / "security_audit.py").read_text(encoding="utf-8")
    tests = (REPO / "tests" / "test_phase12_key_migration_and_backup.py").read_text(encoding="utf-8")

    assert "def _lstat_restore_path" in source
    assert "FILE_ATTRIBUTE_REPARSE_POINT" in source
    assert "st_nlink" in source
    assert "test_restore_refuses_preexisting_hardlink_target_before_writing" in tests
    assert "test_restore_refuses_existing_windows_junction_prefix" in tests


def test_counts_tool_runs_as_a_command() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "risk_register_counts.py")],
        capture_output=True, text=True, cwd=REPO, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "consistent" in result.stdout
