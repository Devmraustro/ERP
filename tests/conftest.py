"""Shared test helpers and fixtures."""

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture
def assert_owner_only_permissions():
    """Return a callable that asserts a file has owner-only permissions."""
    def _check(path: Path) -> None:
        assert path.exists(), f"expected file does not exist: {path}"
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600, f"expected mode 0o600, got {oct(mode)} on {path}"
        else:
            assert os.access(path, os.W_OK), f"file is not writable by owner: {path}"
    return _check