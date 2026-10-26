#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-only
"""Deterministic operational readiness checker for a configured ERP deployment.

Purpose and honest scope
------------------------
This tool answers one question in a repeatable way: *is the deployment's
configuration complete and are its verification entry points available?*  It
deliberately separates two things that operators routinely conflate:

1. **Configuration presence** - the environment variables, files and
   permissions a correct deployment must have (sections ``configuration`` and
   ``permissions``).
2. **Operational functionality** - whether the verification commands themselves
   pass right now (section ``verification_commands``).  By default these are
   listed with ``executed: false``: *available* is not *passing*.  Only
   ``--run-checks`` executes the safe, read-only ones and folds their exit codes
   into the result.

A third section, ``deployment_evidence_required``, lists the controls this tool
can never verify from the deployment host - off-host checkpoint storage,
independent credentials, installed schedulers, alert delivery, disaster-recovery
drills, KMS/HSM custody, publisher provenance, a representative OCR corpus and
professional approvals.  They are reported as *required evidence*, never as
present, and they never influence the exit code.  Something this tool cannot
see is not a pass.

Exit codes
----------
* ``0`` - every configuration check passed (and, with ``--run-checks``, every
  executed verification command exited 0).
* ``1`` - at least one failure; every failure names the missing or invalid item
  and a concrete remediation.
* ``2`` - usage error (invalid arguments, e.g. a negative checkpoint age).

No network access, no writes, no key generation: the tool reads configuration
and runs the same read-only verifiers the runbooks already document.

Usage::

    python tools/operational_readiness.py                     # configuration only
    python tools/operational_readiness.py --run-checks        # + read-only verifiers
    python tools/operational_readiness.py --checkpoint p --max-checkpoint-age-days 1
    python tools/operational_readiness.py --report-json report.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

DB_ENV = "ERP_DB_PATH"
VAULT_ENV = "ERP_VAULT_PATH"
SECURITY_LOG_ENV = "ERP_SECURITY_LOG_PATH"
DOCS_ENV = "ERP_DOCS_ENABLED"
HOST_ENV = "ERP_HOST"
PROXIES_ENV = "ERP_TRUSTED_PROXIES"
VAULT_KEY_ENV = "ERP_VAULT_KEY"
AUDIT_KEY_ENV = "ERP_AUDIT_HMAC_SECRET"
VAULT_KEY_SUFFIX = ".vault.key"
AUDIT_KEY_SUFFIX = ".audit.key"

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

USAGE_EXIT = 2


@dataclass(frozen=True)
class CheckResult:
    """One configuration check with an operator-actionable outcome."""

    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str
    remediation: str = ""

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass
class VerificationCommand:
    """A documented read-only verifier the deployment must be able to run."""

    name: str
    argv: List[str]
    available: bool = True
    availability_note: str = ""
    executed: bool = False
    exit_code: Optional[int] = None
    output_tail: str = ""
    passed: Optional[bool] = None


def _tail(text: str, lines: int = 6) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _default_runner(argv: List[str], cwd: Path) -> Tuple[int, str]:
    """Run a verification command, capturing stdout+stderr together."""
    completed = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, check=False, timeout=600
    )
    return completed.returncode, _tail((completed.stdout or "") + (completed.stderr or ""))


# ----------------------------------------------------------------------
# Configuration checks (presence and shape only)
# ----------------------------------------------------------------------
def _check_database(env: Mapping[str, str]) -> CheckResult:
    raw = env.get(DB_ENV, "").strip()
    if not raw:
        return CheckResult(
            DB_ENV, "fail", "not set", f"Set {DB_ENV} to the SQLite database file path."
        )
    path = Path(raw)
    if not path.exists():
        return CheckResult(
            DB_ENV,
            "fail",
            f"configured path does not exist: {path}",
            "Point ERP_DB_PATH at the deployed database, or restore it from the "
            "latest verified backup (docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md).",
        )
    if path.is_dir():
        return CheckResult(DB_ENV, "fail", f"configured path is a directory: {path}",
                           "ERP_DB_PATH must be the SQLite database file itself.")
    return CheckResult(DB_ENV, "pass", f"database file present: {path}")


def _check_vault(env: Mapping[str, str]) -> CheckResult:
    raw = env.get(VAULT_ENV, "").strip()
    if not raw:
        return CheckResult(
            VAULT_ENV, "fail", "not set", f"Set {VAULT_ENV} to the encrypted document vault directory."
        )
    path = Path(raw)
    if not path.is_dir():
        return CheckResult(
            VAULT_ENV,
            "fail",
            f"configured vault directory does not exist: {path}",
            "Create the vault directory (it is created with mode 0700 on first use), "
            "or restore it from the latest verified backup.",
        )
    return CheckResult(VAULT_ENV, "pass", f"vault directory present: {path}")


def _check_vault_key(env: Mapping[str, str], vault_raw: str) -> CheckResult:
    from_env = env.get(VAULT_KEY_ENV, "").strip()
    key_file = Path(vault_raw).parent / (Path(vault_raw).name + VAULT_KEY_SUFFIX) if vault_raw else None
    if from_env and key_file is not None and key_file.exists():
        return CheckResult(
            VAULT_KEY_ENV,
            "warn",
            f"{VAULT_KEY_ENV} is set AND {key_file} exists; the environment value wins "
            "and the file is ignored",
            "Remove one of the two sources so exactly one is authoritative "
            "(precedence: ERP_VAULT_KEY, then the key file beside the vault).",
        )
    if from_env:
        return CheckResult(VAULT_KEY_ENV, "pass", "set in the environment")
    if key_file is not None and key_file.exists():
        return CheckResult(VAULT_KEY_ENV, "pass", f"key file present: {key_file}")
    return CheckResult(
        VAULT_KEY_ENV,
        "fail",
        "neither the environment variable nor a key file beside the vault is present",
        "Provision the vault key explicitly (secret manager -> ERP_VAULT_KEY, or the "
        "managed key file). A first use would mint a key automatically, which the "
        "staging configuration forbids beyond staging (docs/STAGING_SECURITY_CONFIGURATION.md §1).",
    )


def _check_audit_key(env: Mapping[str, str], db_raw: str) -> CheckResult:
    from_env = env.get(AUDIT_KEY_ENV, "").strip()
    key_file = Path(db_raw).with_name(Path(db_raw).name + AUDIT_KEY_SUFFIX) if db_raw else None
    if from_env and key_file is not None and key_file.exists():
        return CheckResult(
            AUDIT_KEY_ENV,
            "warn",
            f"{AUDIT_KEY_ENV} is set AND {key_file} exists; the environment value must "
            "agree with the active registry version or startup will refuse",
            "Keep exactly one authoritative source; run `python -m dz_erp.rotate_keys "
            "status` to confirm the environment value agrees with the key ring.",
        )
    if from_env:
        return CheckResult(AUDIT_KEY_ENV, "pass", "set in the environment")
    if key_file is not None and key_file.exists():
        return CheckResult(AUDIT_KEY_ENV, "pass", f"key file present: {key_file}")
    return CheckResult(
        AUDIT_KEY_ENV,
        "fail",
        "neither the environment variable nor a key file beside the database is present",
        "Provision the audit HMAC key (ERP_AUDIT_HMAC_SECRET or the managed key file); "
        "a fresh database would otherwise mint one at startup, which is a staging-only path.",
    )


def _check_security_log(env: Mapping[str, str]) -> CheckResult:
    raw = env.get(SECURITY_LOG_ENV, "").strip()
    if not raw:
        return CheckResult(
            SECURITY_LOG_ENV, "fail", "not set",
            "Set ERP_SECURITY_LOG_PATH to a durable file path; durable authentication-failure "
            "logging is a required control (docs/STAGING_SECURITY_CONFIGURATION.md §2).",
        )
    path = Path(raw)
    if not path.parent.is_dir():
        return CheckResult(
            SECURITY_LOG_ENV, "fail", f"parent directory does not exist: {path.parent}",
            f"Create {path.parent} (and mount it on durable storage) before start-up.",
        )
    if not os.access(path.parent, os.W_OK):
        return CheckResult(
            SECURITY_LOG_ENV, "fail", f"parent directory is not writable by this account: {path.parent}",
            f"Grant the service account write access to {path.parent}; an unwritable "
            "security log silently drops auth-failure records.",
        )
    return CheckResult(SECURITY_LOG_ENV, "pass", f"writable parent directory: {path.parent}")


def _check_docs_disabled(env: Mapping[str, str]) -> CheckResult:
    raw = env.get(DOCS_ENV, "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return CheckResult(DOCS_ENV, "pass", "API docs disabled (default): /docs, /redoc, /openapi.json return 404")
    return CheckResult(
        DOCS_ENV,
        "fail",
        f"{DOCS_ENV}={raw!r} exposes /docs, /redoc and /openapi.json",
        "Remove ERP_DOCS_ENABLED or set it to false; API documentation must stay off in "
        "a real deployment (docs/STAGING_SECURITY_CONFIGURATION.md §3).",
    )


def _check_bind_address(env: Mapping[str, str]) -> CheckResult:
    host = env.get(HOST_ENV, "").strip() or "127.0.0.1"
    if host.lower() in LOOPBACK_HOSTS:
        return CheckResult(HOST_ENV, "pass", f"loopback bind ({host}); not reachable off-host")
    if env.get(PROXIES_ENV, "").strip():
        return CheckResult(
            HOST_ENV, "warn", f"non-loopback bind ({host}) with ERP_TRUSTED_PROXIES configured",
            "Confirm the proxy list matches the actual reverse proxy addresses "
            "(docs/STAGING_SECURITY_CONFIGURATION.md §4.1; forwarded-header trust is fail-closed).",
        )
    return CheckResult(
        HOST_ENV,
        "fail",
        f"non-loopback bind ({host}) with no ERP_TRUSTED_PROXIES configured",
        "Bind 127.0.0.1 and front the service with a reverse proxy, or set "
        "ERP_TRUSTED_PROXIES to the exact proxy addresses; otherwise remote clients "
        "reach the service directly.",
    )


# ----------------------------------------------------------------------
# Permissions (honest about the platform difference)
# ----------------------------------------------------------------------
def _permission_results(key_files: List[Path], vault_dir: Optional[Path]) -> List[CheckResult]:
    results: List[CheckResult] = []
    import stat

    for path in key_files:
        if path is None or not path.exists():
            continue
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                results.append(
                    CheckResult(
                        f"permissions:{path.name}",
                        "fail",
                        f"mode {oct(mode)} grants access beyond the owner",
                        f"chmod 600 {path}",
                    )
                )
            else:
                results.append(CheckResult(f"permissions:{path.name}", "pass", f"mode {oct(mode)} owner-only"))
        else:
            results.append(
                CheckResult(
                    f"permissions:{path.name}",
                    "warn",
                    "Windows mode bits are not access control; custody depends on the NTFS ACL",
                    "Restrict the containing directory's ACL to the service account "
                    "(risk R8/S5; docs/KEY_PROVIDER_CONTRACT.md §6).",
                )
            )
    if vault_dir is not None and vault_dir.is_dir() and os.name == "posix":
        mode = stat.S_IMODE(vault_dir.stat().st_mode)
        if mode & 0o077:
            results.append(
                CheckResult(
                    "permissions:vault_dir", "fail", f"vault directory mode {oct(mode)} is too open",
                    f"chmod 700 {vault_dir}",
                )
            )
        else:
            results.append(CheckResult("permissions:vault_dir", "pass", f"vault directory mode {oct(mode)}"))
    return results


# ----------------------------------------------------------------------
# Checkpoint freshness and shape (integrity itself is the verify command)
# ----------------------------------------------------------------------
def _check_checkpoint(
    checkpoint: Path, max_age_days: int, env: Mapping[str, str]
) -> List[CheckResult]:
    results: List[CheckResult] = []
    if not checkpoint.exists():
        return [
            CheckResult(
                "checkpoint:exists", "fail", f"no checkpoint file at {checkpoint}",
                "Run the export command from docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md "
                "(--checkpoint-export) or investigate why the scheduler stopped writing.",
            )
        ]
    try:
        document = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [
            CheckResult(
                "checkpoint:parse", "fail", f"checkpoint is not valid JSON: {exc}",
                "The checkpoint file is corrupt; re-export a fresh checkpoint before "
                "trusting the ledger tail.",
            )
        ]
    if not isinstance(document, dict) or "head_hash" not in document:
        return [
            CheckResult(
                "checkpoint:schema", "fail", "checkpoint has no head_hash field",
                "Re-export; a checkpoint without a head hash cannot be verified "
                "against the live ledger.",
            )
        ]
    age_seconds = time.time() - checkpoint.stat().st_mtime
    age_days = age_seconds / 86400.0
    if age_days > max_age_days:
        results.append(
            CheckResult(
                "checkpoint:freshness", "fail",
                f"checkpoint is {age_days:.2f} days old (limit {max_age_days})",
                "Investigate the scheduler/exports; a stale checkpoint silently narrows "
                "the tamper-evidence window. Then re-export.",
            )
        )
    else:
        results.append(
            CheckResult("checkpoint:freshness", "pass", f"age {age_days:.3f} days <= {max_age_days}")
        )
    results.append(
        CheckResult(
            "checkpoint:integrity",
            "pass" if _checkpoint_command_ready(env) else "warn",
            "shape OK; integrity requires running the verify command against the live ledger",
            "" if _checkpoint_command_ready(env) else "Set ERP_DB_PATH/ERP_VAULT_PATH so the verify command can run.",
        )
    )
    return results


def _checkpoint_command_ready(env: Mapping[str, str]) -> bool:
    return bool(env.get(DB_ENV, "").strip()) and bool(env.get(VAULT_ENV, "").strip())


# ----------------------------------------------------------------------
# Verification commands (available vs executed vs passing)
# ----------------------------------------------------------------------
def _verification_commands(
    env: Mapping[str, str], checkpoint: Optional[Path], max_age_days: int, wheelhouse: Optional[Path]
) -> List[VerificationCommand]:
    commands: List[VerificationCommand] = []

    def add(name: str, argv: List[str], requires: bool, note: str = "") -> None:
        commands.append(VerificationCommand(name=name, argv=argv, available=requires, availability_note=note))

    add("dependency-graph", [sys.executable, str(REPO_ROOT / "tools" / "verify_dependency_graph.py")], True)
    add("hash-lock", [sys.executable, str(REPO_ROOT / "tools" / "generate_hash_lock.py"), "--check"], True)

    db, vault = env.get(DB_ENV, "").strip(), env.get(VAULT_ENV, "").strip()
    if db and vault:
        add(
            "key-status",
            [sys.executable, "-m", "dz_erp.rotate_keys", "status", "--db", db, "--vault", vault],
            True,
        )
    else:
        add("key-status", [], False, "requires ERP_DB_PATH and ERP_VAULT_PATH")

    if checkpoint is not None and db and vault:
        add(
            "checkpoint-verify",
            [
                sys.executable, "-m", "dz_erp.rotate_keys", "monitor",
                "--db", db, "--vault", vault,
                "--checkpoint-verify", str(checkpoint),
                "--max-checkpoint-age-days", str(max_age_days),
            ],
            True,
        )
    elif checkpoint is not None:
        add("checkpoint-verify", [], False, "requires ERP_DB_PATH and ERP_VAULT_PATH")

    if wheelhouse is not None:
        add(
            "wheelhouse-verify",
            [sys.executable, str(REPO_ROOT / "tools" / "verify_wheelhouse.py"), "--wheelhouse", str(wheelhouse)],
            wheelhouse.is_dir(),
            "" if wheelhouse.is_dir() else f"wheelhouse directory missing: {wheelhouse}",
        )
    return commands


# ----------------------------------------------------------------------
# Controls this tool can never verify from the deployment host
# ----------------------------------------------------------------------
_DEPLOYMENT_EVIDENCE = [
    {
        "item": "off-host checkpoint storage",
        "why": "a checkpoint copied off-host with independent credentials defeats a "
               "host-level attacker; nothing on this host can prove it exists",
        "acceptance": "a checkpoint newer than the live ledger's previous export exists "
                      "at a destination outside the host, retrievable with credentials "
                      "the host does not hold (docs/RELEASE_READINESS_MATRIX.md R-OFFHOST)",
    },
    {
        "item": "scheduler installed and running",
        "why": "the runbook defines timers/cron but installation is a deployment act",
        "acceptance": "`systemctl list-timers` (or cron) shows the export/verify units "
                      "enabled and recently fired (docs/RELEASE_READINESS_MATRIX.md R-OFFHOST)",
    },
    {
        "item": "alert delivery",
        "why": "the monitor exits 3 when unhealthy; delivery of that signal is external",
        "acceptance": "a test alarm reaches the on-call channel end-to-end "
                      "(docs/RELEASE_READINESS_MATRIX.md R-ALERT)",
    },
    {
        "item": "disaster-recovery drill at production scale",
        "why": "restore is verified at staging scale only",
        "acceptance": "a restore of the latest production backup into an isolated "
                      "environment verifies hashes, trial balance and audit chains "
                      "(docs/RELEASE_READINESS_MATRIX.md R-DR)",
    },
    {
        "item": "KMS/HSM custody (or an approved equivalent)",
        "why": "key material is file-based here; hardware-backed custody is not bundled",
        "acceptance": "docs/KEY_PROVIDER_CONTRACT.md §4 acceptance met by a real "
                      "provider, or file-based custody formally approved by the "
                      "security owner (docs/RELEASE_READINESS_MATRIX.md R-KMS)",
    },
    {
        "item": "publisher provenance (PEP 740/sigstore)",
        "why": "hashes pin bytes, not publishers",
        "acceptance": "attestation verification demonstrated for the pinned set "
                      "(docs/RELEASE_READINESS_MATRIX.md R-PROV)",
    },
    {
        "item": "representative OCR evaluation corpus",
        "why": "the bundled benchmark is synthetic; production accuracy needs a "
               "properly authorized, redacted corpus",
        "acceptance": "docs/OCR_EVALUATION_PROTOCOL.md §12 process executed on an "
                      "authorized corpus with owner sign-off "
                      "(docs/RELEASE_READINESS_MATRIX.md R-OCR)",
    },
    {
        "item": "professional approvals (accounting, tax, data protection)",
        "why": "SCF/VAT/stamp-duty mappings and Law 18-07 obligations need qualified review",
        "acceptance": "docs/ALGERIA_COMPLIANCE_GATE_CHECKLIST.md items signed off "
                      "(docs/RELEASE_READINESS_MATRIX.md R-SCF and R-LAW)",
    },
]


def evaluate(
    env: Mapping[str, str],
    *,
    repo_root: Path = REPO_ROOT,
    checkpoint: Optional[Path] = None,
    max_checkpoint_age_days: int = 1,
    wheelhouse: Optional[Path] = None,
    run_checks: bool = False,
    runner: Optional[Callable[[List[str], Path], Tuple[int, str]]] = None,
) -> Dict[str, object]:
    """Build the readiness report.  Deterministic: same inputs, same output."""
    db_raw = env.get(DB_ENV, "").strip()
    vault_raw = env.get(VAULT_ENV, "").strip()

    configuration: List[CheckResult] = [
        _check_database(env),
        _check_vault(env),
    ]
    if vault_raw:
        configuration.append(_check_vault_key(env, vault_raw))
    if db_raw:
        configuration.append(_check_audit_key(env, db_raw))
    configuration.extend(
        [
            _check_security_log(env),
            _check_docs_disabled(env),
            _check_bind_address(env),
        ]
    )

    key_files = [
        Path(vault_raw).parent / (Path(vault_raw).name + VAULT_KEY_SUFFIX) if vault_raw else None,
        Path(db_raw).with_name(Path(db_raw).name + AUDIT_KEY_SUFFIX) if db_raw else None,
    ]
    permissions = _permission_results(key_files, Path(vault_raw) if vault_raw else None)

    checkpoint_results: List[CheckResult] = []
    if checkpoint is not None:
        checkpoint_results = _check_checkpoint(checkpoint, max_checkpoint_age_days, env)

    commands = _verification_commands(env, checkpoint, max_checkpoint_age_days, wheelhouse)
    if run_checks:
        execute = runner or _default_runner
        for command in commands:
            if not command.available or not command.argv:
                continue
            command.executed = True
            try:
                command.exit_code, command.output_tail = execute(command.argv, repo_root)
            except (OSError, subprocess.SubprocessError) as exc:
                command.exit_code, command.output_tail = -1, f"{type(exc).__name__}: {exc}"
            command.passed = command.exit_code == 0

    all_results = configuration + permissions + checkpoint_results
    failed = [r for r in all_results if r.failed]
    if run_checks:
        failed.extend(
            CheckResult(f"verification:{c.name}", "fail", f"exit {c.exit_code}", _tail(c.output_tail))
            for c in commands
            if c.executed and c.passed is False
        )
    failed.extend(
        CheckResult(f"verification:{c.name}", "fail", "not available", c.availability_note)
        for c in commands
        if run_checks and not c.available
    )

    def render(check: CheckResult) -> Dict[str, str]:
        return {"status": check.status, "detail": check.detail, "remediation": check.remediation}

    report: Dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "configuration+verification" if run_checks else "configuration",
        "ready": not failed,
        "configuration": {c.name: render(c) for c in configuration},
        "permissions": {c.name: render(c) for c in permissions},
        "checkpoint": {c.name: render(c) for c in checkpoint_results},
        "verification_commands": [
            {
                "name": c.name,
                "argv": c.argv,
                "available": c.available,
                "availability_note": c.availability_note,
                "executed": c.executed,
                "exit_code": c.exit_code,
                "passed": c.passed,
                "output_tail": c.output_tail,
            }
            for c in commands
        ],
        "deployment_evidence_required": _DEPLOYMENT_EVIDENCE,
        "failures": [f"{c.name}: {c.detail} -> {c.remediation}" for c in failed],
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check deployment configuration readiness; run read-only verifiers with --run-checks."
    )
    parser.add_argument("--db", help="Override ERP_DB_PATH (does not change the environment).")
    parser.add_argument("--vault", help="Override ERP_VAULT_PATH.")
    parser.add_argument("--checkpoint", type=Path, help="Checkpoint file to check for existence, shape and freshness.")
    parser.add_argument("--max-checkpoint-age-days", type=int, default=1, help="Checkpoint freshness limit (default: 1).")
    parser.add_argument("--wheelhouse", type=Path, help="Offline wheelhouse directory to make verifiable.")
    parser.add_argument(
        "--run-checks", action="store_true",
        help="Execute the available read-only verification commands and include their exit codes.",
    )
    parser.add_argument("--report-json", type=Path, help="Also write the report as JSON.")
    args = parser.parse_args(argv)

    if args.max_checkpoint_age_days < 0:
        parser.error("--max-checkpoint-age-days must be >= 0")
        return USAGE_EXIT

    env: Dict[str, str] = dict(os.environ)
    if args.db:
        env[DB_ENV] = args.db
    if args.vault:
        env[VAULT_ENV] = args.vault

    report = evaluate(
        env,
        checkpoint=args.checkpoint,
        max_checkpoint_age_days=args.max_checkpoint_age_days,
        wheelhouse=args.wheelhouse,
        run_checks=args.run_checks,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.report_json:
        args.report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"ready: {report['ready']} "
        f"(mode: {report['mode']}; {len(report['failures'])} failure(s); "
        f"{len(report['deployment_evidence_required'])} external evidence items required, not checked here)",
        file=sys.stderr,
    )
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
