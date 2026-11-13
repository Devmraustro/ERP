# Packaging decision for ERP Algeria V1 (B2)

Decision date: 2026-11-09 · Decision owner: release engineering · Status: **DECIDED —
V1 ships from the source tree; the wheel is not a V1 delivery artefact.**

This document closes the "review B2 and document the packaging decision" condition
of the Phase-5 release gate. It records what was measured, what was decided, and
what would have to change before a wheel could be called deployable.

## 1. The defect (B2) — unchanged and still present

`pip wheel .` on the integrated tree fails before any file is written:

```
error: Multiple top-level packages discovered in a flat-layout:
['ops', 'dz_erp', 'vendor', 'odoo_addons']
```

Root cause: `pyproject.toml` declares a setuptools build with no explicit package
discovery, so setuptools' flat-layout auto-discovery sees four top-level
directories and refuses to guess. Reproduced on CPython 3.12.10 with build
isolation enabled (`phase5_ci_312/logs/packaging.log`).

## 2. The preserved fix works (and is deliberately not merged here)

`docs/security/phase3_blocker_closure/packaging-flat-layout.patch`
(`sha256 a73eacc3…41c4d`) pins the package list explicitly. Applied to a scratch
copy of the same tree, the build succeeds:

| Property | Measured |
| :--- | :--- |
| Wheel | `algerian_ai_erp-0.1.0-py3-none-any.whl`, 683 553 bytes, 44 entries |
| Top-level contents | `dz_erp` + `algerian_ai_erp-0.1.0.dist-info` — exactly one importable package |
| Bundled data | `dz_erp/assets/fonts/DejaVuSans.ttf` and its copyright file |
| Console script | `dz-erp = dz_erp.desktop.app:main` — **the desktop app, not the API server** |
| `Requires-Python` | `>=3.11.17,<3.13,!=3.12.0…!=3.12.9` (the same floor the restore gate enforces) |

The fix remains a separate, reviewable change. It is not part of the SEC-13/14/15
security commit, because a packaging change and a security change should be
reviewed and reverted independently.

## 3. Why the wheel is not a deployable package

A wheel that installs `dz_erp` is not a deployment of this product. It carries:

* **no operational assets** — no `ops/` (systemd unit, environment sample),
  no `tools/` (backup/restore/acceptance scripts), no `docs/`, and **no
  `requirements.lock*`**. A host installing only the wheel cannot create a
  reproducible environment, cannot install the service unit, and cannot run the
  supported backup/restore tooling.
* **no server entry point** — the only script is the desktop UI. There is no
  `dz-erp-serve`; the documented server command is `python -m dz_erp.serve`,
  which expects a source/work tree with `ops/` beside it.
* **no migrations or operator runbooks** — schema changes ship in the source
  tree, and the migration/verify procedures are documents in `docs/`.

Measuring it is the point: the artefact is a Python library + desktop bundle, and
presenting it as "the ERP, packaged" would mislead an operator into a deployment
that cannot be backed up, restored, or verified.

## 4. Decision

**V1 is deployed from the source tree only.** This is an accepted constraint, not
an oversight:

* the supported route is the one documented in `docs/PRODUCTION_DEPLOYMENT_GUIDE.md`
  and `ops/` — checkout at the release commit, hash-pinned `pip install`, run
  `python -m dz_erp.serve`;
* that route was executed end-to-end on the target interpreter for this release
  (CPython 3.12.10): bootstrap, start, health, token, session, Host rejection —
  see `phase5_ci_312/logs/deployment_smoke.log`;
* **the wheel is not offered as a V1 artefact**, is not attached to the release,
  and is not referenced by any deployment instruction;
* B2 stays open as a packaging task. Closing it requires: merge the flat-layout
  fix, add an API console script, publish the ops assets alongside the wheel (or
  a system package), and re-run the deployment smoke test from the installed
  artefact rather than from the source tree.

## 5. What this decision does not claim

* It does not claim the wheel is broken — it builds, and its metadata is correct.
* It does not claim source-tree deployment is safer than a packaged one; it is
  simply the only route that has been verified, and the only one that ships the
  operational assets.
* It does not close B2. A packaging defect that stops `pip wheel` from producing
  a distributable is still a defect, and it is still on the list in
  `SECURITY_RESIDUAL_RISKS.md` §3.
