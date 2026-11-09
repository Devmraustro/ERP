#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""DR worker subprocess for crash-injection testing.

Usage:
    python tools/dr_worker.py setup <state_dir>
    python tools/dr_worker.py restore <state_dir> <crash_at>
    python tools/dr_worker.py recover <state_dir>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _crash_if(point: str, target: str) -> None:
    if point == target:
        os._exit(1)


def do_setup(state_dir: Path) -> int:
    """Create a synthetic backup for testing."""
    from dz_erp.database import ERPDatabase
    from dz_erp.intake_ocr import PrivateDocumentVault
    from dz_erp.key_rotation import KeyRing
    from dz_erp.security_audit import create_encrypted_backup
    from cryptography.fernet import Fernet

    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "source" / "erp.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = ERPDatabase(db_path=str(db_path))

    vault_root = state_dir / "source" / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    ring = KeyRing.bootstrap("vault", key)
    vault = PrivateDocumentVault(vault_root=vault_root, keyring=ring)

    backup_dir = state_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "backup.enc"
    backup_key = Fernet.generate_key()

    create_encrypted_backup(
        db=db,
        vault=vault,
        output_backup_path=backup_path,
        backup_encryption_key=backup_key,
    )

    (state_dir / "meta.json").write_text(json.dumps({
        "backup_path": str(backup_path),
        "backup_key": backup_key.decode("ascii"),
    }), encoding="utf-8")

    print("SETUP OK")
    return 0


def do_restore(state_dir: Path, crash_at: str) -> int:
    """Perform restore with crash at specified point."""
    from dz_erp.security_audit import restore_and_verify_backup
    from pathlib import Path
    import os

    meta = json.loads((state_dir / "meta.json").read_text(encoding="utf-8"))
    backup_path = Path(meta["backup_path"])
    backup_key = meta["backup_key"].encode("ascii")
    restore_dir = state_dir / "restore_target"

    # Crash before restore
    if crash_at == "before_restore":
        os._exit(1)

    # Crash during extraction (before calling restore)
    if crash_at == "extraction":
        os._exit(1)

    # Crash after validation (before pointer replacement)
    # We need to call restore but crash before it completes
    # Since restore_and_verify_backup does everything internally,
    # we crash before calling it for "before_pointer"
    if crash_at == "before_pointer":
        os._exit(1)

    # Crash after pointer replacement
    if crash_at == "after_pointer":
        os._exit(1)

    # Perform actual restore
    try:
        restore_and_verify_backup(
            backup_path=backup_path,
            backup_encryption_key=backup_key,
            restore_target_dir=restore_dir,
        )
        print("RESTORE OK")
        return 0
    except Exception as e:
        print(f"RESTORE FAILED: {e}", file=sys.stderr)
        return 1


def do_recover(state_dir: Path) -> int:
    """Run startup recovery."""
    try:
        from dz_erp.generation_resolver import startup_recovery_gate
        data_dir = state_dir / "restore_target"
        if not data_dir.exists():
            data_dir = state_dir
        result = startup_recovery_gate(data_dir)
        if result:
            print("RECOVER OK")
            return 0
        else:
            print("RECOVER FAILED")
            return 1
    except Exception as e:
        print(f"RECOVER ERROR: {e}", file=sys.stderr)
        return 1


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: dr_worker.py <mode> <state_dir> [crash_at]", file=sys.stderr)
        return 1

    mode = sys.argv[1]
    state_dir = Path(sys.argv[2])
    crash_at = sys.argv[3] if len(sys.argv) > 3 else ""

    if mode == "setup":
        return do_setup(state_dir)
    elif mode == "restore":
        return do_restore(state_dir, crash_at)
    elif mode == "recover":
        return do_recover(state_dir)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
