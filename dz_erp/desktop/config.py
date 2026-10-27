# SPDX-License-Identifier: LGPL-3.0-only
"""Local configuration and data locations for the desktop client.

Rules this module enforces:

- **No secrets on disk.** The configuration file contains paths and
  preferences only. Passwords and session tokens never enter it (the session
  token lives in the client's memory for the lifetime of the process), and
  :func:`save_config` refuses to persist anything outside its whitelist.
- **Known locations.** Data lives under the per-user application-data
  directory (``%LOCALAPPDATA%\\AlgerianERP`` on Windows, XDG data home on
  POSIX) so backups and uninstall documentation can name real paths.
- **Never crash on a corrupt file.** A damaged configuration is set aside
  and defaults are restored; a broken preferences file must not take the
  accounting data with it.

This module is Qt-free so it can be tested on headless CI.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional

from . import APP_NAME

LANGUAGES = ("fr", "ar")
MODES = ("local", "server")

#: Fields that may be persisted. Anything else is dropped on save, so a
#: secret that someone stuffs into the config object cannot reach the disk.
_PERSISTED_FIELDS = (
    "language",
    "mode",
    "server_url",
    "db_path",
    "vault_path",
    "log_path",
    "security_log_path",
    "log_level",
)


def default_data_dir() -> Path:
    """Per-user application-data directory for :data:`APP_NAME`."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


@dataclass
class DesktopConfig:
    """Everything the client needs to start. Deliberately free of secrets."""

    language: str = "fr"
    mode: str = "local"
    server_url: str = ""
    db_path: str = ""
    vault_path: str = ""
    log_path: str = ""
    security_log_path: str = ""
    log_level: str = "INFO"
    data_dir: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.data_dir:
            self.data_dir = str(default_data_dir())
        if not self.db_path:
            self.db_path = str(Path(self.data_dir) / "data" / "erp.sqlite3")
        if not self.vault_path:
            self.vault_path = str(Path(self.data_dir) / "data" / "vault")
        if not self.log_path:
            self.log_path = str(Path(self.data_dir) / "logs" / "desktop.log")
        if not self.security_log_path:
            self.security_log_path = str(Path(self.data_dir) / "logs" / "security.jsonl")

    # -- validation ------------------------------------------------------

    def validate(self) -> None:
        """Raise ``ValueError`` with an actionable message on bad values."""
        if self.language not in LANGUAGES:
            raise ValueError(f"language must be one of {LANGUAGES}, got {self.language!r}")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.mode == "server" and not self.server_url:
            raise ValueError(
                "mode 'server' requires server_url - the address of the company ERP server."
            )
        if self.server_url and not self.server_url.startswith(("http://", "https://")):
            raise ValueError(
                "server_url must start with http:// or https:// (LAN deployment), "
                f"got {self.server_url!r}"
            )
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"log_level {self.log_level!r} is not a known level")
        # Mode A needs somewhere durable to put the database and vault.
        if self.mode == "local" and (not self.db_path or not self.vault_path):
            raise ValueError("mode 'local' requires db_path and vault_path.")

    # -- persistence -----------------------------------------------------

    def to_persistable_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return {key: data[key] for key in _PERSISTED_FIELDS}


def config_path(data_dir: Optional[str] = None) -> Path:
    base = Path(data_dir) if data_dir else default_data_dir()
    return base / "config.json"


def load_config(data_dir: Optional[str] = None) -> DesktopConfig:
    """Load the configuration, falling back to safe defaults.

    A missing or unreadable file yields defaults; a corrupt file is moved
    aside (kept for forensics) and defaults are returned. The application
    then behaves like a fresh install - the accounting database itself is
    never touched by this recovery path.
    """
    path = config_path(data_dir)
    config = DesktopConfig(data_dir=str(path.parent))
    if not path.is_file():
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a JSON object")
    except (OSError, ValueError):
        aside = path.with_name(path.name + f".corrupt-{os.getpid()}")
        try:
            path.replace(aside)
        except OSError:
            pass
        return config
    known = {f.name for f in fields(DesktopConfig)}
    for key, value in raw.items():
        if key in known and isinstance(value, str):
            setattr(config, key, value)
    config.data_dir = str(path.parent)
    return config


def save_config(config: DesktopConfig, data_dir: Optional[str] = None) -> Path:
    """Persist the whitelist of fields atomically; refuse unknown/secret keys.

    The whitelist is the control: even if a future field accidentally
    collects a token, :func:`save_config` will not write it unless it is
    added here deliberately.
    """
    config.validate()
    target = config_path(data_dir or config.data_dir)
    payload = config.to_persistable_dict()
    if set(payload) != set(_PERSISTED_FIELDS):  # pragma: no cover - structural invariant
        raise ValueError("configuration payload drifted from the persisted whitelist")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(target.parent), prefix=".config-", suffix=".tmp", delete=False
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, target)
    except BaseException:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    if os.name == "posix":
        os.chmod(target, 0o600)
    return target
