#!/usr/bin/env python
# SPDX-License-Identifier: LGPL-3.0-only
"""Reproducible synthetic load and recovery drill for key rotation and restore.

Purpose
-------
Answers, with measurements rather than adjectives: how long does rotating N
documents take, how long does verifying an audit chain of M events take, how long
does backup/restore take for that data, what does it cost in memory and storage,
and — the part that is usually skipped — what happens when the rotator is killed
at 10 %, 50 % and 90 % and then resumed?

What this does NOT do
---------------------
It does not produce production-scale claims. The workload is synthetic, generated
locally, on one machine, against a local filesystem and an SQLite file. The
thresholds it evaluates are derived from the *measured* staging numbers and an
explicitly stated reference workload (§ "Reference workload" in
docs/KEY_CUSTODY_AND_ROTATION_OPERATIONS.md). A small fixture says nothing about
a 200 000-document vault; the report therefore prints the measured points and
the extrapolation basis instead of a headline number.

Usage
-----
    python tools/rotation_scale_drill.py --documents 200 --events 2000
    python tools/rotation_scale_drill.py --documents 1000 --events 10000 --json out.json

Every artefact stays inside a temporary directory (``--keep`` to retain it).
No credential, no real document, no customer data is used; document bodies are
generated pseudo-random bytes and blob names are derived hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.security_audit import create_encrypted_backup, restore_and_verify_backup


def _rss_kb() -> int:
    """Peak RSS of this process, in KiB (POSIX ``ru_maxrss`` is KiB)."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    value = int(usage.ru_maxrss)
    if platform.system() == "Darwin":  # pragma: no cover - macOS reports bytes
        return value // 1024
    if platform.system() == "Windows":  # pragma: no cover - Windows reports bytes
        return value // 1024
    return value


def _dir_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _now() -> float:
    return time.perf_counter()


def _timed(fn, *args, **kwargs) -> tuple:
    start = _now()
    result = fn(*args, **kwargs)
    return result, _now() - start


# ----------------------------------------------------------------------
# Fixture generation
# ----------------------------------------------------------------------
def build_fixture(root: Path, documents: int, events: int, companies: int = 2) -> Dict[str, Any]:
    """Create a synthetic deployment: one vault, one database, N documents, M events."""
    root.mkdir(parents=True, exist_ok=True)
    vault = PrivateDocumentVault(root / "vault")
    db = ERPDatabase(str(root / "erp.sqlite3"))

    with db.transaction() as conn:
        for company_id in range(1, companies + 1):
            conn.execute(
                "INSERT INTO companies (id, code, name, country_code, created_at)"
                " VALUES (?, ?, ?, 'DZ', '2026-01-01')",
                (company_id, f"DZ{company_id}", f"Synthetic Company {company_id}"),
            )
            conn.execute(
                "INSERT INTO users (id, company_id, username, role)"
                " VALUES (?, ?, ?, 'accountant')",
                (company_id, company_id, f"drill-accountant-{company_id}"),
            )

    # Posted, balanced entries. Without these the restored trial balance is
    # trivially 0.00/0.00 and the restore check proves nothing about the books.
    entry_start = _now()
    for company_id in range(1, companies + 1):
        for index in range(5):
            amount = f"{1000 + index}.00"
            with db.transaction() as conn:
                cursor = conn.execute(
                    """INSERT INTO journal_entries (
                           company_id, entry_number, journal_code, entry_date, state,
                           source_model, source_id, total_debit_dzd, total_credit_dzd,
                           posted_by_id, posted_at
                       ) VALUES (?, ?, 'OD', '2026-01-15', 'POSTED', 'synthetic_drill', ?, ?, ?, ?, '2026-01-15T10:00:00Z')""",
                    (company_id, f"DRILL-{company_id}-{index}", f"drill-{index}", amount, amount, company_id),
                )
                entry_id = cursor.lastrowid
                conn.executemany(
                    "INSERT INTO journal_entry_lines (company_id, journal_entry_id, account_code, label, debit_dzd, credit_dzd)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (company_id, entry_id, "512", "Bank", amount, "0.00"),
                        (company_id, entry_id, "701", "Sales", "0.00", amount),
                    ],
                )
    entry_seconds = _now() - entry_start

    # Documents: deterministic pseudo-random bodies, each unique.
    doc_start = _now()
    for index in range(documents):
        company_id = 1 + (index % companies)
        body = hashlib.sha256(f"document-{index}".encode()).digest() * 32  # ~1 KiB
        digest = hashlib.sha256(body).hexdigest()
        vault.store_bytes(company_id, digest, body)
    doc_seconds = _now() - doc_start

    # Audit events, distributed across companies so tenant scoping is exercised.
    event_start = _now()
    batch = 200
    for offset in range(0, events, batch):
        with db.transaction() as conn:
            for index in range(offset, min(offset + batch, events)):
                company_id = 1 + (index % companies)
                db.append_audit_event(
                    conn,
                    company_id=company_id,
                    actor_user_id=None,
                    action_type=f"SYNTHETIC_LOAD_EVENT_{index}",
                    target_model="synthetic_load",
                    target_id=str(index),
                    after_state={"index": index, "amount_dzd": f"{index % 1000}.00"},
                )
    event_seconds = _now() - event_start

    blobs = list((root / "vault").rglob("*.enc"))
    return {
        "root": root,
        "vault": vault,
        "db": db,
        "documents": len(blobs),
        "events": events,
        "companies": companies,
        "write_seconds": {"documents": doc_seconds, "events": event_seconds, "journal_entries": entry_seconds},
        "vault_bytes": _dir_bytes(root / "vault"),
        "db_bytes": (root / "erp.sqlite3").stat().st_size,
    }


# ----------------------------------------------------------------------
# Measurements
# ----------------------------------------------------------------------
def measure_verification(fixture: Dict[str, Any]) -> Dict[str, Any]:
    vault, db = fixture["vault"], fixture["db"]
    blobs, blob_seconds = _timed(vault.verify_blobs)
    chains, chain_seconds = _timed(db.verify_all_audit_chains)
    return {
        "vault_verify_seconds": blob_seconds,
        "vault_blobs_checked": blobs["checked"],
        "vault_blobs_failed": blobs["failed"],
        "audit_verify_seconds": chain_seconds,
        "audit_chain_valid": chains["valid"],
        "audit_events_checked": sum(c["checked_count"] for c in chains["companies"].values()),
    }


def measure_rotation(fixture: Dict[str, Any]) -> Dict[str, Any]:
    vault, db = fixture["vault"], fixture["db"]
    docs = fixture["documents"]

    vault_report, vault_seconds = _timed(vault.rotate_key)
    audit_report, audit_seconds = _timed(db.rotate_audit_key)

    return {
        "vault_rotation_seconds": vault_seconds,
        "vault_documents_per_second": round(docs / vault_seconds, 2) if vault_seconds else None,
        "vault_attempted": vault_report["attempted"],
        "vault_completed": vault_report["completed"],
        "vault_failed": vault_report["failed"],
        "vault_verified": vault_report["verified_ok"],
        "vault_status": vault_report["status"],
        "audit_rotation_seconds": audit_seconds,
        "audit_chain_valid_after": audit_report["chain_valid_after"],
        "audit_status": audit_report["status"],
    }


def measure_backup_restore(fixture: Dict[str, Any], state: Path, key: Optional[bytes] = None) -> Dict[str, Any]:
    vault, db = fixture["vault"], fixture["db"]
    key = key or vault.encryption_key
    backup_path = state / "backups" / "drill.enc"

    meta, backup_seconds = _timed(
        create_encrypted_backup,
        db=db,
        vault=vault,
        output_backup_path=backup_path,
        backup_encryption_key=key,
    )
    restore_dir = state / "restored"
    report, restore_seconds = _timed(
        restore_and_verify_backup,
        backup_path=backup_path,
        backup_encryption_key=key,
        restore_target_dir=restore_dir,
    )
    return {
        "backup_name": Path(meta["backup_path"]).name if "backup_path" in meta else backup_path.name,
        "backup_seconds": backup_seconds,
        "backup_bytes": backup_path.stat().st_size,
        "restore_seconds": restore_seconds,
        "restore_db_sha256_verified": report["db_sha256_verified"],
        "restore_vault_files_verified": report["vault_files_verified"],
        "restore_audit_chains_valid": report["audit_chains_valid"],
        "restore_trial_balance": [report["trial_balance_debit_dzd"], report["trial_balance_credit_dzd"]],
        "restore_target": str(restore_dir),
    }


def measure_interruption_recovery(state: Path, documents: int, kill_at: float) -> Dict[str, Any]:
    """Kill a rotation in a **separate process** at ~``kill_at`` of the documents.

    Using a real subprocess is the point: an in-process exception would be caught
    and recorded as FAILED, which is not what a power loss or SIGKILL does. The
    orphaned operation must still be recoverable, and the vault must still be
    fully readable.
    """
    fixture = build_fixture(state / f"interrupt-{int(kill_at * 100)}", documents, events=50, companies=1)
    vault_root = fixture["vault"].vault_root
    limit = max(1, int(documents * kill_at))

    script = f"""
import sys, time
sys.path.insert(0, {str(REPO_ROOT)!r})
from pathlib import Path
from dz_erp.intake_ocr import PrivateDocumentVault

vault = PrivateDocumentVault(Path({str(vault_root)!r}))
original = PrivateDocumentVault._rotate_one
count = {{"n": 0}}

def stopping(self, path, target_version, max_retries, journal, report):
    count["n"] += 1
    if count["n"] > {limit}:
        # Hard exit: no exception handling, no terminal ledger record.
        import os
        os._exit(137)
    return original(self, path, target_version, max_retries, journal, report)

PrivateDocumentVault._rotate_one = stopping
vault.rotate_key()
"""
    start = _now()
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=1800)
    kill_seconds = _now() - start
    if proc.returncode != 137:
        raise RuntimeError(
            f"the interruptor did not die as intended (exit {proc.returncode}): {proc.stderr[-400:]}"
        )

    # Everything must still be readable, and both keys must still be present.
    vault = PrivateDocumentVault(vault_root)
    verification, verify_seconds = _timed(vault.verify_blobs)
    versions = vault.scan_blob_versions()

    from dz_erp.rotation_audit import RotationLedger

    ledger = RotationLedger.open(vault_root.parent / (vault_root.name + ".rotation-ledger.jsonl"))
    ledger_state = ledger.verify()
    open_before = list(ledger_state["open_operations"])

    # Resume with the key the interrupted run activated; it must not add a third.
    new_key = vault.keyring.material_for(max(vault.keyring.versions))
    resume_start = _now()
    report = vault.rotate_key(new_key)
    resume_seconds = _now() - resume_start
    ledger_after = ledger.verify()

    return {
        "kill_at_fraction": kill_at,
        "interruptor_exit": proc.returncode,
        "kill_phase_seconds": kill_seconds,
        "documents_target": documents,
        "blobs_after_kill": versions,
        "readable_after_kill": verification["failed"] == 0,
        "verify_after_kill_seconds": verify_seconds,
        "open_operations_after_kill": open_before,
        "resume_seconds": resume_seconds,
        "resume_completed": report["completed"],
        "resume_skipped_already_current": report["skipped_already_current"],
        "resume_verified": report["verified_ok"],
        "final_versions": report and sorted(vault.keyring.versions),
        "converged": not {v for v in vault.scan_blob_versions() if v != vault.active_key_version},
        "open_operations_after_resume": ledger_after["open_operations"],
        "ledger_valid": ledger_after["valid"],
    }


def measure_concurrent_updates(state: Path, documents: int, writers: int = 4) -> Dict[str, Any]:
    """Rotate while other threads append new documents.

    New documents are content-addressed, so they land at new paths and must never
    be lost or corrupted by a concurrent rotation.
    """
    fixture = build_fixture(state / "concurrent", documents, events=20, companies=1)
    vault = fixture["vault"]
    written: List[str] = []
    errors: List[str] = []
    stop = threading.Event()

    def writer(index: int) -> None:
        attempt = 0
        while not stop.is_set():
            body = f"CONCURRENT-{index}-{attempt}".encode() * 8
            digest = hashlib.sha256(body).hexdigest()
            try:
                written.append(vault.store_bytes(1, digest, body))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            attempt += 1

    threads = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(writers)]
    for thread in threads:
        thread.start()
    report, rotation_seconds = _timed(vault.rotate_key)
    stop.set()
    for thread in threads:
        thread.join(timeout=60)

    verification = vault.verify_blobs()
    readable = sum(1 for path in written if Path(path).exists())
    # Decryptability is not convergence: a blob written between the version scan
    # and the key switch could stay on the old version forever while still
    # opening fine. Rotation is only finished when nothing references the old key.
    final_versions = vault.scan_blob_versions()
    stray = {version: count for version, count in final_versions.items() if version != vault.active_key_version}
    return {
        "rotation_seconds": rotation_seconds,
        "writers": writers,
        "documents_written_concurrently": len(written),
        "documents_still_readable": readable,
        "writer_errors": len(errors),
        "sample_errors": errors[:3],
        "blobs_total": verification["checked"] + verification["failed"],
        "blobs_failed": verification["failed"],
        "rotation_completed": report["completed"],
        "rotation_failed": report["failed"],
        "rotation_verified": report["verified_ok"],
        "final_versions": final_versions,
        "converged": not stray,
        "stray_versions": stray,
    }


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------
REFERENCE_WORKLOAD = {
    "description": (
        "Reference point for interpreting the measurements below - a stated "
        "assumption, not a production claim."
    ),
    "documents": 20_000,
    "events": 200_000,
    "companies": 10,
    "document_mean_bytes": 1024,
    "vault_storage_gb": 0.02,
    "rotation_window_minutes": 60,
    "targets": {
        # Derived from the measured throughput on this machine with a 10x safety
        # margin for slower storage; see docs/KEY_CUSTODY_AND_ROTATION_OPERATIONS.md.
        "vault_rotation_documents_per_second_min": 20.0,
        "audit_rotation_seconds_max": 60.0,
        "audit_verify_events_per_second_min": 500.0,
        "restore_seconds_max": 900.0,
        "interruption_recovery_max_seconds": 900.0,
    },
}


def evaluate_thresholds(results: Dict[str, Any]) -> Dict[str, Any]:
    targets = REFERENCE_WORKLOAD["targets"]
    checks = {
        "vault_rotation_throughput": {
            "measured": results["rotation"]["vault_documents_per_second"],
            "threshold": targets["vault_rotation_documents_per_second_min"],
            "unit": "documents/second",
        },
        "audit_rotation_seconds": {
            "measured": results["rotation"]["audit_rotation_seconds"],
            "threshold": targets["audit_rotation_seconds_max"],
            "unit": "seconds (max)",
        },
        "restore_seconds": {
            "measured": results["backup_restore"]["restore_seconds"],
            "threshold": targets["restore_seconds_max"],
            "unit": "seconds (max)",
        },
    }
    for name, spec in checks.items():
        is_max = "max" in spec["unit"]
        spec["pass"] = (
            spec["measured"] is not None
            and (spec["measured"] <= spec["threshold"] if is_max else spec["measured"] >= spec["threshold"])
        )
    events_per_second = results["verification"]["audit_events_checked"] / max(
        results["verification"]["audit_verify_seconds"], 1e-9
    )
    checks["audit_verify_throughput"] = {
        "measured": round(events_per_second, 2),
        "threshold": targets["audit_verify_events_per_second_min"],
        "unit": "events/second",
        "pass": events_per_second >= targets["audit_verify_events_per_second_min"],
    }
    concurrency = results["concurrency"]
    checks["concurrent_write_convergence"] = {
        "measured": concurrency["documents_still_readable"],
        "threshold": concurrency["documents_written_concurrently"],
        "unit": "documents readable (min)",
        "pass": concurrency["converged"]
        and concurrency["writer_errors"] == 0
        and concurrency["documents_still_readable"] == concurrency["documents_written_concurrently"]
        and concurrency["blobs_failed"] == 0,
    }
    for row in results["interruptions"]:
        checks[f"recovery_at_{int(row['kill_at_fraction'] * 100)}pct"] = {
            "measured": row["resume_seconds"],
            "threshold": targets["interruption_recovery_max_seconds"],
            "unit": "seconds (max)",
            "pass": row["readable_after_kill"] and row["resume_verified"] and row["converged"],
        }
    return {"reference_workload": REFERENCE_WORKLOAD, "checks": checks, "all_passed": all(c["pass"] for c in checks.values())}


def run(documents: int, events: int, state: Path, interruptions: List[float]) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "filesystem_root": str(state),
        },
        "workload": {"documents": documents, "events": events},
    }

    fixture = build_fixture(state / "main", documents, events)
    results["workload"]["documents_created"] = fixture["documents"]
    results["workload"]["fixture_seconds"] = fixture["write_seconds"]
    results["storage"] = {"vault_bytes": fixture["vault_bytes"], "db_bytes": fixture["db_bytes"]}
    results["memory_kb_after_fixture"] = _rss_kb()

    results["verification"] = measure_verification(fixture)
    results["memory_kb_after_verify"] = _rss_kb()

    results["rotation"] = measure_rotation(fixture)
    results["memory_kb_after_rotation"] = _rss_kb()

    results["backup_restore"] = measure_backup_restore(fixture, state / "main_backup")
    results["memory_kb_after_restore"] = _rss_kb()

    results["interruptions"] = [
        measure_interruption_recovery(state, documents, fraction) for fraction in interruptions
    ]
    results["concurrency"] = measure_concurrent_updates(state, max(20, documents // 5))

    results["thresholds"] = evaluate_thresholds(results)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic load and recovery drill for key rotation.")
    parser.add_argument("--documents", type=int, default=200)
    parser.add_argument("--events", type=int, default=2000)
    parser.add_argument("--state", help="Working directory (must be empty or absent).")
    parser.add_argument("--keep", action="store_true", help="Keep the working directory.")
    parser.add_argument("--json", dest="json_out", help="Write the full result document here.")
    parser.add_argument(
        "--interrupt-at",
        default="0.1,0.5,0.9",
        help="Fractions of the document set at which to kill and resume (comma-separated).",
    )
    args = parser.parse_args(argv)

    state = Path(args.state).resolve() if args.state else Path(tempfile.mkdtemp(prefix="erp-drill-"))
    if args.state and state.exists() and any(state.iterdir()):
        print(f"refusing to run: {state} is not empty", file=sys.stderr)
        return 2
    state.mkdir(parents=True, exist_ok=True)

    fractions = [float(item) for item in args.interrupt_at.split(",") if item.strip()]
    print(f"synthetic drill: {args.documents} documents, {args.events} events")
    print(f"state: {state}\n")

    try:
        results = run(args.documents, args.events, state, fractions)
    except Exception:
        import traceback

        traceback.print_exc()
        print("\ndrill FAILED", file=sys.stderr)
        return 1

    print(json.dumps({k: v for k, v in results.items() if k != "thresholds"}, indent=2, default=str)[:6000])
    print("\n=== threshold evaluation ===")
    for name, spec in results["thresholds"]["checks"].items():
        status = "PASS" if spec["pass"] else "FAIL"
        print(f"[{status}] {name}: measured={spec['measured']} {spec['unit']} threshold={spec['threshold']}")
    print(f"\nall thresholds passed: {results['thresholds']['all_passed']}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, default=str))
        print(f"full report: {args.json_out}")
    if not args.keep and not args.state:
        shutil.rmtree(state, ignore_errors=True)
    else:
        (state / "drill-results.json").write_text(json.dumps(results, indent=2, default=str))
        print(f"artefacts kept in {state}")

    return 0 if results["thresholds"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
