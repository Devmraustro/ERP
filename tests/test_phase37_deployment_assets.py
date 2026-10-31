# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 37 — the operational assets are guarded like code.

A Dockerfile, a compose file and an environment example are load-bearing: a
weakened one becomes a production incident that no unit test would ever catch.
None of them can be *executed* in this environment (there is no container
runtime), so what is verified here is exactly what can honestly be verified:

* the files exist, parse, and keep the security properties they claim
  (non-root, hash-pinned install, dropped capabilities, read-only root
  filesystem, loopback-published port, single replica);
* the interpreter is pinned to the floor the safe-restore path requires;
* the image contains no secret and no database;
* every ``ERP_*`` variable the code reads is either documented in the environment
  example or explicitly listed as a secret that must come from elsewhere.

That last check is the drift gate: a new environment variable that nobody
documents is an operator surprise at 2 a.m., and this test fails instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
OPS = REPO / "ops"
DOCKERFILE = OPS / "Dockerfile"
COMPOSE = OPS / "docker-compose.yml"
ENV_EXAMPLE = OPS / "erp.env.example"

#: Variables that must never appear in a committed environment file; they are
#: secrets and belong in the deployment's secret store.
SECRET_VARS = {"ERP_VAULT_KEY", "ERP_AUDIT_HMAC_SECRET"}


def _dockerfile() -> str:
    assert DOCKERFILE.is_file(), "ops/Dockerfile is missing"
    return DOCKERFILE.read_text()


def _compose() -> dict:
    assert COMPOSE.is_file(), "ops/docker-compose.yml is missing"
    parsed = yaml.safe_load(COMPOSE.read_text())
    assert isinstance(parsed, dict)
    return parsed


def test_image_is_pinned_to_the_interpreter_floor_and_hash_locked() -> None:
    text = _dockerfile()
    # 3.11.17 is not cosmetic: restore_and_verify_backup refuses to run below it,
    # so a container on an earlier patch silently loses backup verification.
    assert "FROM python:3.11.17" in text
    assert "--require-hashes" in text and "requirements.lock.hashes.txt" in text
    assert "--no-deps" in text  # the lockfile enumerates the whole closure


def test_image_runs_unprivileged_and_bakes_in_no_secret_state() -> None:
    text = _dockerfile()
    assert re.search(r"USER\s+erp", text), "the image must not run as root"
    assert "useradd --uid 10001" in text
    assert "--no-create-home" in text
    # No credentials, keys or database in the image.
    for forbidden in ("ENV ERP_VAULT_KEY", "ENV ERP_AUDIT_HMAC_SECRET", "COPY erp.sqlite3"):
        assert forbidden not in text
    # Exec form so PID 1 receives SIGTERM directly.
    assert 'ENTRYPOINT ["python", "-m", "dz_erp.serve"]' in text
    assert "HEALTHCHECK" in text and "/healthz" in text


def test_stack_keeps_the_security_properties_it_claims() -> None:
    compose = _compose()
    service = compose["services"]["erp"]
    # The API is reachable only through the host's TLS proxy.
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert any(str(t).startswith("/tmp") for t in service["tmpfs"])
    # Per-process limiter + single SQLite writer: one replica is a correctness
    # statement, not a default to be raised casually.
    assert service["deploy"]["replicas"] == 1
    # State and logs are volumes, never the image.
    mounts = {v.split(":")[1] for v in service["volumes"]}  # volume:path
    assert "/var/lib/erp" in mounts and "/var/log/erp" in mounts
    # Bootstrap is opt-in and creates an administrator deliberately.
    assert compose["services"]["bootstrap"]["profiles"] == ["bootstrap"]


def test_every_environment_variable_the_code_reads_is_documented() -> None:
    """The drift gate: new knob in code, missing from the operator's list."""
    used: set[str] = set()
    for path in sorted((REPO / "dz_erp").rglob("*.py")):
        used.update(re.findall(r'"(ERP_[A-Z0-9_]+)"', path.read_text()))
    documented = set(re.findall(r"ERP_[A-Z0-9_]+", ENV_EXAMPLE.read_text()))
    missing = used - documented - SECRET_VARS
    assert not missing, f"undocumented environment variables: {sorted(missing)}"
    # And the secrets must stay out of the committed file.
    example_text = ENV_EXAMPLE.read_text()
    for secret in SECRET_VARS:
        for line in example_text.splitlines():
            assert not re.match(rf"\s*{secret}\s*=\s*\S", line), (
                f"{secret} must not be given a value in a committed file"
            )


def test_env_example_states_the_rate_limiting_bounds() -> None:
    text = ENV_EXAMPLE.read_text()
    for name in ("ERP_AUTH_FAILURE_LIMIT", "ERP_AUTH_FAILURE_WINDOW_SECONDS",
                 "ERP_RATE_LIMIT_PER_MINUTE"):
        assert name in text
    # The honest scope of the control is stated where an operator will read it.
    assert "per-process" in text or "per process" in text


def test_dockerignore_keeps_state_and_secrets_out_of_the_build_context() -> None:
    dockerignore = REPO / ".dockerignore"
    assert dockerignore.is_file(), (
        "a build context without .dockerignore would copy databases, logs, key "
        "registries and the operator env file into the image"
    )
    lines = {line.strip() for line in dockerignore.read_text().splitlines()}
    for required in (".git", "*.sqlite3", "*.keys.json", "ops/erp.env", "*.rotation.jsonl"):
        assert required in lines, f".dockerignore must exclude {required}"
