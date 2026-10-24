# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 23 — housekeeping for isolated staging acceptance artefacts."""

from __future__ import annotations

from pathlib import Path

from tools.staging_acceptance import _cleanup_default_state


def test_default_staging_cleanup_removes_state_and_checkpoint_sibling(tmp_path: Path) -> None:
    """The simulated checkpoint is deliberately outside the state directory and must be cleaned too."""
    state = tmp_path / "erp-staging-case"
    checkpoint_dir = tmp_path / "erp-staging-case-checkpoint-simulation"
    (state / "vault").mkdir(parents=True)
    (state / "vault" / "blob.enc").write_bytes(b"synthetic")
    checkpoint_dir.mkdir()
    (checkpoint_dir / "vault.rotation.checkpoint.json").write_text("{}", encoding="utf-8")

    _cleanup_default_state(state)

    assert not state.exists()
    assert not checkpoint_dir.exists()
