# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 56 — policy-driven age-based retention for the security log (F-02).

The durable security log already rotates by size and file count. What it did not
have is age-based deletion, because the retention *period* is a legal
determination, not a constant this repository may invent. This phase adds the
mechanism only:

* ``ERP_SECURITY_LOG_MAX_AGE_DAYS`` enables age-based deletion of rotated files.
  Unset (the default) keeps the existing size/count behaviour unchanged, so no
  retention period is asserted without an approved schedule.
* When set, rotated files older than the configured age are removed on the next
  rotation pass; the active log file is never touched.
* Off-host shipping remains unavailable and is documented as such.

The actual period, its legal reference and the deletion trigger are the DPO's
decision (``docs/LOG_RETENTION_AND_ACCESS.md`` §4); this code only provides the
configuration hook.
"""

from __future__ import annotations

import os
import time

from dz_erp.security_audit import _prune_security_log_by_age


def test_age_based_deletion_is_a_no_op_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_SECURITY_LOG_MAX_AGE_DAYS", raising=False)
    import dz_erp.security_audit as sa

    sa._LOG_MAX_AGE_DAYS = None
    log = tmp_path / "security.jsonl"
    log.write_text("x\n", encoding="utf-8")
    rotated = tmp_path / "security.jsonl.1"
    rotated.write_text("old\n", encoding="utf-8")
    os.utime(rotated, (time.time() - 365 * 86400, time.time() - 365 * 86400))

    _prune_security_log_by_age(log)

    assert rotated.exists(), "without configuration nothing may be deleted"
    assert log.exists()


def test_age_based_deletion_removes_old_rotated_files_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_SECURITY_LOG_MAX_AGE_DAYS", "30")
    # Re-import the module-level constant by re-evaluating through the helper's
    # module global.
    import dz_erp.security_audit as sa

    sa._LOG_MAX_AGE_DAYS = 30
    log = tmp_path / "security.jsonl"
    log.write_text("x\n", encoding="utf-8")
    old_rotated = tmp_path / "security.jsonl.1"
    old_rotated.write_text("old\n", encoding="utf-8")
    os.utime(old_rotated, (time.time() - 60 * 86400, time.time() - 60 * 86400))
    fresh_rotated = tmp_path / "security.jsonl.2"
    fresh_rotated.write_text("fresh\n", encoding="utf-8")

    _prune_security_log_by_age(log)

    assert not old_rotated.exists(), "rotated files older than the age must be removed"
    assert fresh_rotated.exists(), "recently rotated files must be retained"
    assert log.exists(), "the active log file is never touched"


def test_age_based_deletion_ignores_non_rotated_files(tmp_path, monkeypatch):
    import dz_erp.security_audit as sa

    sa._LOG_MAX_AGE_DAYS = 30
    log = tmp_path / "security.jsonl"
    log.write_text("x\n", encoding="utf-8")
    unrelated = tmp_path / "security.jsonl.bak"
    unrelated.write_text("keep\n", encoding="utf-8")
    os.utime(unrelated, (time.time() - 60 * 86400, time.time() - 60 * 86400))

    _prune_security_log_by_age(log)

    assert unrelated.exists(), "only numbered rotated files are eligible"


def test_retention_age_configuration_rejects_non_positive_or_malformed_values():
    import dz_erp.security_audit as sa

    assert sa._parse_log_max_age_days(None) is None
    assert sa._parse_log_max_age_days("") is None
    assert sa._parse_log_max_age_days("1") == 1
    assert sa._parse_log_max_age_days("30") == 30

    for raw in ("0", "-1", "-30", "not-a-number", "1.5"):
        try:
            sa._parse_log_max_age_days(raw)
        except ValueError as exc:
            assert "must be a positive whole number of days" in str(exc)
        else:
            raise AssertionError(f"unsafe retention age was accepted: {raw!r}")
