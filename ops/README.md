# Operational assets

Everything in this directory is **prepared, not deployed**. Nothing here has been
run against a real host or a container runtime from within this repository's
development environment; each file states what it does and does not prove.

| File | Purpose | Verified how |
|---|---|---|
| `Dockerfile` | Single-image API runtime: CPython 3.11.17 (the interpreter floor the safe restore path requires), hash-pinned dependencies, unprivileged uid 10001, exec-form entrypoint, `/healthz` healthcheck | **Not built here** — no container runtime available. Syntax and intent reviewed; the underlying install command is the one CI runs |
| `docker-compose.yml` | Single-host reference stack: loopback-published port, two volumes (state, logs), read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, one replica by design | **Not started here** — YAML parses; behaviour unverified |
| `erp.env.example` | Every environment variable an operator must decide, with the documented default and the reasoning for each | Checked against `dz_erp/serve.py`, `dz_erp/api.py`, `dz_erp/net.py`, `dz_erp/rate_limit.py` |
| `dz-erp-checkpoint-*.{service,timer}` | Off-host rotation-ledger checkpoint export and verification | Documented in `docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md` |

## Why one replica

The rate limiter is in-process state and the SQLite writer is a single writer.
Running two replicas of this image would multiply the effective login ceiling and
invite write contention, so `deploy.replicas: 1` is a correctness statement, not
a default someone forgot to raise. Horizontal scaling is a design change (shared
limiter, server database), not a compose tweak.

## The gap this directory cannot close

A Dockerfile is not a deployment. Before any of this is used for real, a platform
owner must build the image, run the stack, restore a backup into it, rotate a key
in it, confirm the proxy actually sets `X-Forwarded-For` and that
`ERP_TRUSTED_PROXIES` matches it, and check that the security log reaches
off-host storage. Those steps are listed in
`docs/PRODUCTION_DEPLOYMENT_GUIDE.md` §7 and remain **unverified here**.
