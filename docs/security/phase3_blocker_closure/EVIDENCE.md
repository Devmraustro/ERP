# Phase 3 — raw evidence log

Every command below was executed on 2026-11-09. Results are transcribed verbatim
(trimmed only where marked). Machine names: `pr7work` = preserved integration
workspace, `verify-clone` = fresh clone used for patch reproduction,
`pkgfix` = clean copy used for the packaging experiment.

---

## E1 — Preservation: workspace state at export time

```
$ git -C /tmp/pr7work rev-parse HEAD
5a3e47ddfba2b789bcff71d6438db4927f5c7986

$ git -C /tmp/pr7work status --porcelain
 M dz_erp/api.py
 M dz_erp/decimal_utils.py
 M dz_erp/procurement_stock.py
 M dz_erp/security_audit.py
 M ops/erp.env.example
?? tests/test_phase58_security_selective_integration.py

$ git -C /tmp/pr7work diff --stat
 dz_erp/api.py               | 151 ++++++++++++++++++++++++++++++++++++++++++++
 dz_erp/decimal_utils.py     |  41 +++++++++---
 dz_erp/procurement_stock.py | 119 +++++++++++++++++++++++++++++++++-
 dz_erp/security_audit.py    |  86 +++++++++++++++++++++++--
 ops/erp.env.example         |  10 +++
 5 files changed, 391 insertions(+), 16 deletions(-)
```

No deleted, renamed or binary file. The patch hash
`2dfd77697ba3e726cf2ddf4f9a34766cdf81a093037f581ab71948f177c16b7e` is identical
to the copy produced at the end of Phase 2, so the workspace did not drift.

## E2 — Patch reproduction on a fresh clone

```
$ git clone --no-tags https://github.com/Devmraustro/ERP /tmp/verify-clone
$ git -C /tmp/verify-clone checkout 5a3e47ddfba2b789bcff71d6438db4927f5c7986
BASE=5a3e47ddfba2b789bcff71d6438db4927f5c7986

$ git apply --check --verbose …/security-selective-integration.patch
Checking patch dz_erp/api.py...
Checking patch dz_erp/decimal_utils.py...
Checking patch dz_erp/procurement_stock.py...
Checking patch dz_erp/security_audit.py...
Checking patch ops/erp.env.example...
        (no error line, exit 0)

$ git apply --verbose …/security-selective-integration.patch
Applied patch dz_erp/api.py cleanly.
Applied patch dz_erp/decimal_utils.py cleanly.
Applied patch dz_erp/procurement_stock.py cleanly.
Applied patch dz_erp/security_audit.py cleanly.
Applied patch ops/erp.env.example cleanly.

$ git status --porcelain          # identical to E1
$ git diff --stat | tail -1
 5 files changed, 391 insertions(+), 16 deletions(-)
```

## E3 — Content identity

```
$ diff -r -q --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache \
        --exclude=.ruff_cache --exclude=*.pyc /tmp/pr7work /tmp/verify-clone
  (no output — identical)

$ diff <manifest pr7work> <manifest verify-clone>
MANIFEST IDENTICAL: 297 files
```

Per-file hashes: 6/6 MATCH (see `INTEGRATED_STATE_SHA256.txt`, verified with
`sha256sum -c` inside the reconstructed clone → all `OK`).

The only difference anywhere was `.ruff_cache/` in `pr7work`, a lint cache
created after the integration and excluded from the comparison.

## E4 — Behavioural identity of the reconstruction

```
$ PYTHONPATH=/tmp/verify-clone /tmp/ivenv/bin/python -m pytest \
      tests/test_phase58_security_selective_integration.py -q
================== 91 passed, 1 skipped, 2 warnings in 10.68s ==================
```

Identical to the preserved workspace's baseline result.

## E5 — Interpreter inventory (Phase 2 blocker)

```
$ /usr/bin/python3.11 -V
Python 3.11.2
$ command -v py conda mamba uv pyenv asdf    → none
$ ls /usr/bin/python3*                       → python3.11 only
$ venvs found: /tmp/pr7venv /tmp/mainvenv /tmp/ivenv  (all CPython 3.11.2)
```

CI intent (`.github/workflows/ci.yml`): test matrix `["3.11", "3.12"]`;
restore/backup job pins `python-version: "3.12.10"`; a third job uses `"3.12"`.
Container (`ops/Dockerfile`): `FROM python:3.12-slim-trixie`, explicitly *no*
`pip install .` — "the app is a source tree".

## E6 — Packaging: A/B of the same command

```
# unpatched tree (/tmp/verify-clone)
$ pip wheel . --ignore-requires-python --no-deps --no-build-isolation
  error: Multiple top-level packages discovered in a flat-layout:
         ['ops', 'dz_erp', 'vendor', 'odoo_addons'].
error: metadata-generation-failed

# patched tree (/tmp/pkgfix)
$ pip wheel . --ignore-requires-python --no-deps --no-build-isolation
  → algerian_ai_erp-0.1.0-py3-none-any.whl   (671 305 bytes)
```

`--ignore-requires-python` is required **only because this sandbox runs 3.11.2**
(see §E5); it is a front-end flag, not a change to the project metadata — the
wheel still records `Requires-Python: >=3.11.17,<3.13,!=3.12.0…`.

Wheel contents (`wheel-contents.txt`): top-level entries are exactly
`dz_erp/` (36 modules incl. 8 desktop modules, both font assets) and
`algerian_ai_erp-0.1.0.dist-info/`. `ops/`, `vendor/`, `odoo_addons/`, `tests/`,
`tools/`, `docs/`, `benchmarks/` are absent.

```
$ sha256sum /tmp/pkgdist/algerian_ai_erp-0.1.0-py3-none-any.whl
e01367536b9deb90ac7baecaf3c3373be1ef4e1c6173654ea6f5c7d670f90bc1
```

## E7 — Packaging: install verification

Wheel install into a scratch venv (`--no-deps`, dependencies supplied from the
locked venv for the probe):

```
dz_erp loaded from wheel: /tmp/pkgvenv/lib/python3.11/site-packages/dz_erp/__init__.py
font present: True  759720 bytes
resolver: …/site-packages/dz_erp/assets/fonts/DejaVuSans.ttf
PDF rendered from wheel assets: 22812 bytes, header b'%PDF-'
REPO_ROOT under wheel: /tmp/pkgvenv/lib/python3.11/site-packages
vendor present: False | AZINAK_FIX_DIR present: False
```

Editable install (PEP 660, `--no-build-isolation`):

```
Successfully built algerian-ai-erp
Successfully installed algerian-ai-erp-0.1.0
editable dz_erp: /tmp/pkgfix/dz_erp/__init__.py
font: True
version 0.1.0 | scripts ['dz-erp']
__editable__.algerian_ai_erp-0.1.0.pth
```

## E8 — Packaging: no test impact

```
$ cd /tmp/pkgfix && pytest -q
20 failed, 1204 passed, 9 skipped in 178.75s
```

Identical to the pristine `5a3e47dd` baseline (20 F / 1204 P / 9 S). The 20
failures are the interpreter-gated restore tests (`test_phase8`,
`test_phase9`, …), which fail identically without any packaging change.

## E9 — Vault (executed, POSIX)

```
$ pytest -q tests/test_phase10_vault_key_rotation.py
23 passed in 0.40s
```

## E10 — Desktop (blocked by missing optional dependency)

```
$ pytest -q tests/test_phase26_desktop.py tests/test_phase27_desktop_ui.py \
           tests/test_phase28_desktop_users_ui.py
SKIPPED [1] test_phase27_desktop_ui.py:33: could not import 'PySide6.QtWidgets': No module named 'PySide6'
SKIPPED [1] test_phase28_desktop_users_ui.py:30: could not import 'PySide6.QtWidgets': No module named 'PySide6'
SKIPPED [1] test_phase26_desktop.py:262: could not import 'PySide6.QtWidgets': No module named 'PySide6'
9 passed, 3 skipped in 2.90s
```

`PySide6==6.11.2` is an optional extra (`[project.optional-dependencies].desktop`)
and is not installed here.

## E11 — KMS and S3 status (source, not environment)

```
dz_erp/key_provider.py:389  **Not implemented.** No KMS/HSM client is bundled …
dz_erp/key_provider.py:415  "KmsKeyProvider is not implemented: this build has no KMS/HSM client."
dz_erp/backup_storage_s3.py:30   import boto3   # optional
requirements.lock.txt: (no boto3 / botocore entry)
```

## E12 — Windows gates

```
tests/test_phase12_key_migration_and_backup.py:697  @pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
tests/test_phase17_key_providers.py:97             @pytest.mark.skipif(os.name != "nt", reason="exercises the Windows reparse-point open path")
tests/test_phase17_key_providers.py:111            @pytest.mark.skipif(os.name != "nt", …)
dz_erp/security_audit.py:310                       if os.name != "nt":   (POSIX mode path)
```

## E13 — Remediation workspace left untouched

```
$ cd /home/user/ERP && sha256sum -c /tmp/before_workspace.sha256
dz_erp/api.py: OK
dz_erp/decimal_utils.py: OK
dz_erp/procurement_stock.py: OK
dz_erp/security_audit.py: OK
tests/test_phase34_security_validation.py: OK

$ git rev-parse HEAD          → 4fc1364a9504fab1153ea44e753f354f00023428
$ git rev-parse --abbrev-ref HEAD → arena/7557bcfb-erp
$ git rev-list --count main..HEAD → 0
```

New untracked material added by this phase: `docs/security/phase3_blocker_closure/`
and `docs/security/PHASE3_BLOCKER_CLOSURE_REPORT.md`. Nothing committed, nothing
pushed, PR #7 untouched.
