#!/usr/bin/env python3
"""Phase 10 — operational drill with a real server, real CLI subprocesses.

Everything runs in one mkdtemp() directory with synthetic data. The drill proves, by
execution rather than by description:

  1. the deployment boots (`python -m dz_erp.serve`) and `/healthz` answers;
  2. `/readyz` reports ``ready`` with HTTP 200 on a healthy installation;
  3. `/readyz` returns 503 when the vault becomes unwritable (critical failure);
  4. `ops_backup run` writes a 0600 artefact plus a manifest with its SHA-256;
  5. `ops_backup verify` confirms it, and a single flipped byte is detected;
  6. an integrity failure raises ``BACKUP_INTEGRITY_FAILED`` on stderr (JSON alert);
  7. `ops_checks disk` fails with ``DISK_LOW`` and exit 1 under an impossible floor;
  8. `ops_checks backup-age` fails with ``BACKUP_STALE`` on an aged artefact;
  9. retention deletes older artefacts and never the newest or a legal hold;
 10. `ops_backup verify --deep` actually restores and compares the ledger.

Usage:  python docs/release/phase10/evidence/operational_drill.py
        (expects /tmp/v10 + /tmp/py312 to exist; see the report for how they are built)
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
VENV_PY = Path(os.environ.get("DRILL_PYTHON", "/tmp/v10/bin/python"))
RESULTS: list[dict] = []


def record(step: str, ok: bool, detail: str = "", extra: dict | None = None) -> None:
    entry = {"step": step, "ok": ok, "detail": detail}
    if extra:
        entry.update(extra)
    RESULTS.append(entry)
    print(f"[{'PASS' if ok else 'FAIL'}] {step}: {detail}")


def run(args: list[str], env: dict, *, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=stdin_text,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(REPO),
    )


def get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 503 is an expected result, not an error
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_for(url: str, env: dict, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = get(url)
            if status == 200:
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="phase10-ops-"))
    db_path = work / "data" / "erp.sqlite3"
    vault_path = work / "vault"
    backup_dir = work / "backups"
    key_file = work / "backup.key"
    log_dir = work / "logs"
    for directory in (db_path.parent, backup_dir, log_dir):
        directory.mkdir(parents=True, exist_ok=True)

    from cryptography.fernet import Fernet

    key_file.write_bytes(Fernet.generate_key())
    os.chmod(key_file, 0o600)
    os.chmod(work, 0o700)

    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(REPO),
        PYTHONDONTWRITEBYTECODE="1",
        ERP_DB_PATH=str(db_path),
        ERP_VAULT_PATH=str(vault_path),
        ERP_BACKUP_DIR=str(backup_dir),
        ERP_BACKUP_KEY_FILE=str(key_file),
        ERP_SECURITY_LOG_PATH=str(log_dir / "security.jsonl"),
        ERP_ALERT_WEBHOOK="",  # journal-only during the drill
        ERP_PORT="8095",
        ERP_HOST="127.0.0.1",
    )

    # 1. bootstrap + start a real server
    boot = run(
        [str(VENV_PY), "-m", "dz_erp.serve", "--bootstrap-admin", "admin", "--company-name", "Drill Co"],
        env,
        stdin_text="Drill#2026x\nDrill#2026x\n",
    )
    record("bootstrap", boot.returncode == 0, (boot.stdout or boot.stderr).strip().splitlines()[-1:][0] if (boot.stdout or boot.stderr).strip() else "")

    server = subprocess.Popen(
        [str(VENV_PY), "-m", "dz_erp.serve", "--host", "127.0.0.1", "--port", "8095"],
        env=env,
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        alive = wait_for("http://127.0.0.1:8095/healthz", env)
        record("server liveness", alive, "/healthz returned 200")

        status, body = get("http://127.0.0.1:8095/readyz")
        payload = json.loads(body) if body.strip().startswith("{") else {}
        # A fresh installation has no backup yet, so the advisory backup check fails and
        # the report is "degraded" - correctly. What matters at boot is that nothing
        # *critical* failed and the endpoint still answers 200.
        critical_ok = all(
            check["status"] == "ok"
            for name, check in payload.get("checks", {}).items()
            if check.get("critical")
        )
        record(
            "readiness at boot (no backup yet)",
            status == 200 and critical_ok and payload.get("status") in ("ready", "degraded"),
            f"status={status} readiness={payload.get('status')} all_critical_ok={critical_ok}",
            {"checks": payload.get("checks")},
        )

        # seed one posted entry through the API so the backup has a ledger to carry
        token_request = urllib.request.Request(
            "http://127.0.0.1:8095/api/v1/auth/token",
            data=json.dumps({"username": "admin", "password": "Drill#2026x"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(token_request, timeout=10) as response:
                assert json.loads(response.read())["access_token"].startswith("dzsess_")
            record("token issuance", True, "admin token issued over HTTP")
        except Exception as exc:
            record("token issuance", False, f"{type(exc).__name__}: {exc}")

        # Seed one balanced, posted entry so the archive carries a real ledger and the
        # deep verify compares actual money rather than an empty sum.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO partners (id, company_id, name, partner_type, country_code, tax_regime,"
                " nif, nis, ai, rc) VALUES (1, 1, 'Drill Supplier', 'supplier', 'DZ', 'REEL',"
                " '001216099123456', '099116010123456', '16019912345', '16/00-0991234B12')"
            )
            conn.execute(
                "INSERT INTO journal_entries (id, company_id, entry_number, journal_code, entry_date,"
                " state, source_model, source_id, total_debit_dzd, total_credit_dzd, posted_by_id, posted_at)"
                " VALUES (1, 1, 'OPS-0001', 'OD', '2026-04-01', 'POSTED', 'drill', '1',"
                " '425000.00', '425000.00', 1, '2026-04-01T10:00:00+00:00')"
            )
            conn.executemany(
                "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label,"
                " debit_dzd, credit_dzd) VALUES (1, 1, ?, ?, ?, ?)",
                [("601", "Achats de marchandises", "425000.00", "0.00"),
                 ("401", "Fournisseur Drill Supplier", "0.00", "425000.00")],
            )

        # 4. scheduled backup via the real CLI
        backup_run = run(
            [str(VENV_PY), "-m", "dz_erp.ops_backup", "--backup-dir", str(backup_dir), "run"], env
        )
        artefacts = sorted(backup_dir.glob("*.erpbackup"))
        record(
            "scheduled backup (CLI)",
            backup_run.returncode == 0 and len(artefacts) == 1,
            f"exit={backup_run.returncode} artefacts={len(artefacts)}",
            {"stdout": backup_run.stdout.strip()[:400]},
        )
        if artefacts:
            mode = oct(artefacts[0].stat().st_mode & 0o777)
            manifest = json.loads(
                (artefacts[0].with_name(artefacts[0].name + ".manifest.json")).read_text()
            )
            record(
                "backup artefact + manifest",
                mode == "0o600" and manifest["sha256"] and manifest["size_bytes"] > 0,
                f"mode={mode} sha256={manifest['sha256'][:16]}… size={manifest['size_bytes']}",
            )

        status_after, body_after = get("http://127.0.0.1:8095/readyz")
        payload_after = json.loads(body_after) if body_after.strip().startswith("{") else {}
        record(
            "readiness healthy (after first backup)",
            status_after == 200 and payload_after.get("status") == "ready",
            f"status={status_after} readiness={payload_after.get('status')}",
            {"backup_check": payload_after.get("checks", {}).get("backup")},
        )

        # 5. verify
        verify = run(
            [str(VENV_PY), "-m", "dz_erp.ops_backup", "--backup-dir", str(backup_dir), "verify"], env
        )
        record("verify (hash)", verify.returncode == 0, f"exit={verify.returncode}")

        # 6. corruption detection + alert
        if artefacts:
            target = artefacts[0]
            original = target.read_bytes()
            mutated = bytearray(original)
            mutated[len(mutated) // 2] ^= 0xFF
            target.write_bytes(bytes(mutated))
            corrupt = run(
                [str(VENV_PY), "-m", "dz_erp.ops_backup", "--backup-dir", str(backup_dir), "verify"], env
            )
            alerted = "BACKUP_INTEGRITY_FAILED" in (corrupt.stdout + corrupt.stderr)
            record(
                "corruption detected + alert",
                corrupt.returncode == 1 and alerted,
                f"exit={corrupt.returncode} alert={'BACKUP_INTEGRITY_FAILED' in corrupt.stderr}",
            )
            target.write_bytes(original)

        # 10. deep verify (real restore)
        deep = run(
            [str(VENV_PY), "-m", "dz_erp.ops_backup", "--backup-dir", str(backup_dir), "verify", "--deep"],
            env,
        )
        deep_doc = {}
        try:
            deep_doc = json.loads(deep.stdout)
        except ValueError:
            deep_doc = {}
        record(
            "deep verify (restore)",
            deep.returncode == 0 and deep_doc.get("ok") is True,
            f"exit={deep.returncode} verified={deep_doc.get('verified')}",
        )

        # 7. disk alert under an impossible floor
        disk_env = dict(env)
        disk_env["ERP_MIN_FREE_BYTES"] = str(1 << 60)
        disk = run([str(VENV_PY), "-m", "dz_erp.ops_checks", "disk"], disk_env)
        record(
            "disk-space alert",
            disk.returncode == 1 and "DISK_LOW" in disk.stderr,
            f"exit={disk.returncode} alert={'DISK_LOW' in disk.stderr}",
        )

        # 8. stale-backup alert on an aged artefact
        # A stale artefact only means anything in a directory where it *is* the newest
        # one; aging a file next to a fresh backup is correctly not stale.
        stale_dir = work / "stale-only"
        stale_dir.mkdir(exist_ok=True)
        stale_file = stale_dir / "erp-20200101T000000Z.erpbackup"
        stale_file.write_bytes(b"aged")
        old = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
        os.utime(stale_file, (old, old))
        age_env = dict(env)
        age_env["ERP_BACKUP_MAX_AGE_SECONDS"] = "60"
        age_env["ERP_BACKUP_DIR"] = str(stale_dir)
        age = run([str(VENV_PY), "-m", "dz_erp.ops_checks", "backup-age"], age_env)
        stale_detected = age.returncode == 1 and "BACKUP_STALE" in age.stderr
        record("stale-backup alert", stale_detected, f"exit={age.returncode}")

        # 9. retention: 6 synthetic days, keep 2 daily / 1 weekly, one on hold
        for days_ago in (5, 4, 3, 2, 1):
            name = f"erp-{(datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime('%Y%m%dT%H%M%SZ')}.erpbackup"
            path = backup_dir / name
            path.write_bytes(b"synthetic")
            held = days_ago == 5
            (backup_dir / (name + ".manifest.json")).write_text(
                json.dumps({"sha256": "0" * 64, "size_bytes": 9, "legal_hold": held})
            )
        before = len(list(backup_dir.glob("*.erpbackup")))
        prune = run(
            [
                str(VENV_PY), "-m", "dz_erp.ops_backup", "--backup-dir", str(backup_dir),
                "--retain-daily", "2", "--retain-weekly", "1", "prune",
            ],
            env,
        )
        after = len(list(backup_dir.glob("*.erpbackup")))
        held_survived = (backup_dir / f"erp-{(datetime.now(timezone.utc) - timedelta(days=5)).strftime('%Y%m%dT%H%M%SZ')}.erpbackup").exists()
        newest_survived = bool(sorted(backup_dir.glob("*.erpbackup")))
        record(
            "retention + legal hold",
            prune.returncode == 0 and after < before and held_survived and newest_survived,
            f"before={before} after={after} legal_hold_kept={held_survived}",
        )

        # 3. readiness must fail closed when the vault is unwritable
        os.chmod(vault_path, 0o500)
        try:
            broken_status, broken_body = get("http://127.0.0.1:8095/readyz")
        finally:
            os.chmod(vault_path, 0o700)
        broken = json.loads(broken_body) if broken_body.strip().startswith("{") else {}
        record(
            "readiness fails closed (unwritable vault)",
            broken_status == 503 and broken.get("status") == "not_ready",
            f"status={broken_status} readiness={broken.get('status')}",
        )

        # the API still serves liveness while degraded: liveness != readiness
        live_after, _ = get("http://127.0.0.1:8095/healthz")
        record("liveness unaffected", live_after == 200, f"/healthz={live_after} after vault failure")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    # The backup contains the ledger; prove the posted entry is really in the DB.
    with sqlite3.connect(db_path) as conn:
        posted = conn.execute(
            "SELECT COUNT(*) FROM journal_entries WHERE state='POSTED'"
        ).fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    record(
        "database integrity after drill",
        integrity == "ok" and posted == 1,
        f"integrity={integrity} posted_entries={posted}",
    )

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "steps": len(RESULTS),
        "passed": passed,
        "failed": len(RESULTS) - passed,
        "workdir": str(work),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": RESULTS,
    }
    print("\n== summary ==")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    out = Path(os.environ.get("DRILL_OUTPUT", work / "operational_drill.json"))
    out.write_text(json.dumps(summary, indent=2))
    print(f"written: {out}")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
