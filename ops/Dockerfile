# SPDX-License-Identifier: LGPL-3.0-only
#
# Container image for the Algerian AI ERP API.
#
# STATUS: prepared, not built. No container runtime exists in the environment
# where this file was written, so the image has never been built or run here; it
# is recorded as IMPLEMENTED_NOT_VERIFIED in docs/PROJECT_COMPLETION_TRACKER.md.
# The CI-verified facts it relies on are: the hash-pinned lockfile installs on
# CPython 3.11.17 (the interpreter floor that `restore_and_verify_backup`
# requires), `python -m dz_erp.serve` starts against a durable DB, and /healthz
# answers without authentication.
#
# Deliberate choices, each with a reason:
#
#  * The interpreter is 3.11.17, not "latest 3.11". The safe-extraction restore
#    path refuses to run below it, so a container on 3.11.2 would silently
#    disable backup verification. Pinning the floor is a security property.
#  * Dependencies come from requirements.lock.hashes.txt with --require-hashes
#    and --no-deps, so a substituted wheel fails the build instead of running.
#  * There is no `pip install .` and no build stage: the app is a source tree,
#    and keeping the build deterministic matters more than shaving layers.
#  * The process runs as an unprivileged user with a fixed uid/gid so a mounted
#    volume can be chowned predictably by the operator.
#  * No secret is baked in. ERP_DB_PATH, the vault and any key material are
#    mounted at run time; the image contains no database, no key and no log.
FROM python:3.11.17-slim-bookworm

# Runtime system libraries only. `libgl1`/`libglib2.0-0` are needed by the OCR
# stack (opencv headless still links libGL on some builds); they are installed
# and then the apt lists are dropped so the layer carries no package index.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# Fixed, unprivileged runtime identity.
RUN groupadd --gid 10001 erp \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin erp

WORKDIR /app

# Dependency layer first: it changes rarely, so rebuilds of source stay cheap.
COPY requirements.lock.txt requirements.lock.hashes.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir --no-deps --require-hashes \
        -r requirements.lock.hashes.txt

# Source. vendor/ is required: the SCF chart and the tax seed data are read from
# it at runtime, and a frozen deployment can override the location with
# ERP_VENDOR_DATA_DIR.
COPY dz_erp ./dz_erp
COPY vendor ./vendor
COPY tools ./tools
COPY ops ./ops
COPY pyproject.toml LICENSE ./

# Durable state lives on volumes, never in the image.
RUN mkdir -p /var/lib/erp/vault /var/log/erp \
 && chown -R erp:erp /var/lib/erp /var/log/erp

USER erp

# Documented defaults; every one of them can be overridden at run time.
ENV ERP_DB_PATH=/var/lib/erp/erp.sqlite3 \
    ERP_VAULT_PATH=/var/lib/erp/vault \
    ERP_SECURITY_LOG_PATH=/var/log/erp/security.jsonl \
    ERP_HOST=0.0.0.0 \
    ERP_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Liveness only: /healthz answers without a credential and without touching the
# database, which is what a container orchestrator should restart on. A database
# problem must not restart-loop the API; readiness for the database is the job of
# the deployment's own checks (docs/PRODUCTION_DEPLOYMENT_GUIDE.md §5).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# No shell wrapper: exec form so PID 1 is the server and SIGTERM is delivered to
# it directly, which is what makes a graceful shutdown possible at all.
ENTRYPOINT ["python", "-m", "dz_erp.serve"]
