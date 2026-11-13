# Phase 52 — Dependency Reproducibility and Authoritative Baseline Report

**Recorded:** 2026-11-13 (UTC)
**Repository root:** `/home/user/ERP`
**Branch:** `arena/63d762c8-erp`
**Baseline HEAD on entry:** `d73ae5a577ecaf200218a782cd43090018cca3df`
**Status:** **PASS — Reproducible clean CPython 3.12.10 environment verified (`969 passed / 0 failed`, `40/40` staging, `0` advisories)**

---

## 0. Authoritative Baseline Verification (Reconciliation Before Phase 52)

Per Section 0 of the mission, the repository and remote state were inspected independently before relying on any historical report:

| Item | Verified Current State (`/home/user/ERP` + `origin`) | Historical Reference Noted in Mission | Reconciliation |
| --- | --- | --- | --- |
| **Repository root** | `/home/user/ERP` (Linux x86_64) | `C:\Users\rabzd\Documents\ERP` | Historical reference came from a separate offline Windows clone that was never pushed to `origin`. No commits or evidence from that clone are mixed here. |
| **Current branch** | `arena/63d762c8-erp` (active session branch) | `arena/7557bcfb-erp` | `arena/63d762c8-erp` was branched directly from `arena/7557bcfb-erp` @ `098483a1f02df0308f3889c93714efab5d13b550`. |
| **Exact HEAD SHA on entry** | `d73ae5a577ecaf200218a782cd43090018cca3df` | `4abd2a4cc19b46d9d3c9f50dd8466eb041cba130` | `4abd2a4` does not exist on `origin` (`git ls-remote origin` verified). `d73ae5a` is the verified GitHub tip of `arena/63d762c8-erp`. |
| **Working-tree status on entry** | Clean (`0` modified, `0` untracked after aligning ref reset to `origin/arena/63d762c8-erp`) | — | Verified with `git status` and byte-for-byte `diff` before `git reset --mixed`. |
| **Local & remote branches** | `main` (`4fc1364`), `arena/7557bcfb-erp` (`098483a`), `arena/63d762c8-erp` (`d73ae5a`), `arena/7f12d627-erp` (`f603e38`, PR #7 draft), plus Phase 29 stack (`29645e5`..`e4e47b3`) and merged PRs #1/#2 | — | Inspected via `git ls-remote origin` across all heads and pull refs. |
| **PR #8 status** | `OPEN` (`arena/7557bcfb-erp` → `main` @ `098483a`, **not merged into `main`**) | Not merged into `main` | Matches; CI run `43450664678` (`3/3 SUCCESS`). |
| **PR #9 status** | `OPEN` (`arena/63d762c8-erp` → `arena/7557bcfb-erp` @ `d73ae5a`) | Merged into integration branch | Still `OPEN` on GitHub on entry; CI run `43457667432` (`3/3 SUCCESS`). |
| **Original expert questions** | Present in `docs/release/phase8/EXPERT_APPROVAL_STATUS.md` and `docs/release/phase10/EXPERT_COMPLIANCE_REVIEW_PACKAGE.md` (18 rules + 12 questions) | `docs/compliance/EXPERT_REVIEW_QUESTIONS.md` | Path `docs/compliance/EXPERT_REVIEW_QUESTIONS.md` existed only on the unpushed Windows clone; canonical in-repo Phase 8/10 & Phase 46 registers are preserved and cross-referenced in Phase 55. |
| **Existing release directories** | `docs/release/{phase8,phase9,phase10,phase29,phase30,phase34,phase35,phase36,phase37,phase38,phase39,phase40,phase41,phase42,phase44,phase45,phase46}` + `docs/security/` | `phase51` | Phases 47–51 were on the unpushed Windows clone; all Phases 8–46 in-repo evidence is intact. |
| **External backups / user files** | `/home/user` contains only `.bash_logout`, `.bashrc`, `.profile`, and `ERP/`; all durable evidence is tracked inside Git | — | Verified via `ls -la /home/user`. |

---

## 1. Dependency Manifest Inventory

All dependency declarations, lockfiles, verifiers, and CI workflows in the repository were inspected:

| Path | Role | Pin / Hash Summary |
| --- | --- | --- |
| `pyproject.toml` | Primary PEP 621 project manifest (`requires-python >=3.11.17,<3.13,!=3.12.0..!=3.12.9`) | `15` runtime dependencies + `4` `dev` dependencies + `1` optional `desktop` dependency (`PySide6==6.11.2`), all exact `==` pins |
| `requirements.lock.txt` | Complete transitive closure lockfile (runtime + `dev`) | `53` exact `==` pins (`52` cross-platform + `colorama==0.4.6 ; sys_platform == "win32"`) |
| `requirements.lock.hashes.txt` | Generated SHA-256 artifact lockfile (`tools/generate_hash_lock.py`) | `53` pinned distributions, **`1,317` unique SHA-256 digests** covering all PyPI wheels + sdists across Linux/Windows/macOS |
| `requirements.txt` | Legacy flat requirements filename | **Absent by design** — `pyproject.toml`, `requirements.lock.txt`, and `requirements.lock.hashes.txt` are the sole authoritative manifests |
| `tools/generate_hash_lock.py` | Hash-lock generator & drift verifier (`--write` / `--check`) | Queries PyPI JSON API and verifies zero drift between `requirements.lock.txt` and `requirements.lock.hashes.txt` |
| `tools/verify_dependency_graph.py` | Strict offline dependency-graph & OpenCV payload verifier | Verifies all `pyproject.toml` runtime pins, all transitive specifiers, single allow-listed substitution, headless `cv2` payload, and runtime imports |
| `tools/verify_wheelhouse.py` | Offline wheelhouse SHA-256 verifier against `requirements.lock.hashes.txt` | Present and unit-tested (`tests/test_phase19_dependency_integrity.py`) |
| `.github/workflows/ci.yml` | GitHub Actions CI matrix (Ubuntu Python 3.11, Ubuntu Python 3.12, Windows Python 3.12.10) | Runs `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt`, `generate_hash_lock.py --check`, and `verify_dependency_graph.py` on all 3 jobs |

**Note on hash count (`1,317` vs historical `1,304`):** In this repository's committed `requirements.lock.hashes.txt`, the 53 pinned distributions carry **1,317 unique SHA-256 digests** (expanded when `annotated-doc==0.0.6`, `pygments==2.21.0`, `pytest==9.2.0`, and `cryptography==50.0.2` were security-remediated in Phase 10). Every one of the 1,317 digests was verified live against `https://pypi.org/pypi/<name>/<version>/json`.

---

## 2. Clean Supported Runtime & Isolated Environment Reproduction

Because the container's system Python (`/usr/bin/python3.11`) is CPython `3.11.2` — below the project's `>=3.11.17` / `>=3.12.10` security floor for tar extraction — a clean **CPython `3.12.10`** toolchain was compiled in `/tmp/py312` (outside the Git worktree) using `docs/release/phase35/toolchain/build_env.sh` with `zlib 1.3.1`, `OpenSSL 3.0.20`, `SQLite 3.40.1`, `libffi 8`, and `tarfile.data_filter == True`.

### Exact commands and exit codes

| # | Step | Command | Exit Code | Result |
| --- | --- | --- | --- | --- |
| 1 | Create fresh isolated venv | `/tmp/py312/bin/python3 -m venv /tmp/venv_phase52` | `0` | Clean Python `3.12.10` venv |
| 2 | Upgrade installer | `/tmp/venv_phase52/bin/python -m pip install --upgrade pip` | `0` | `pip 26.3` |
| 3 | Hash-verified lockfile install | `/tmp/venv_phase52/bin/python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | **`0`** | All `53` pinned distributions verified by SHA-256 and installed (`54` total with `pip`) |
| 4 | Verify all 53 pins & 1,317 hashes against PyPI | `/tmp/venv_phase52/bin/python tools/generate_hash_lock.py --check` | **`0`** | `OK - 53 pinned distributions, 1317 hashes, no drift from requirements.lock.txt.` |
| 5 | Verify dependency graph & OpenCV substitution | `/tmp/venv_phase52/bin/python tools/verify_dependency_graph.py` | **`0`** | `OK - the environment matches the declared dependency graph.` (`54` distributions; only deviation is `opencv-python -> opencv-python-headless==4.11.0.86`) |
| 6 | Run `pip check` | `/tmp/venv_phase52/bin/python -m pip check` | `1` *(expected)* | Single line: `rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.` (zero other unsatisfied requirements) |
| 7 | Vulnerability audit (lockfile) | `pip-audit -r requirements.lock.txt --no-deps --disable-pip --vulnerability-service pypi` | **`0`** | `No known vulnerabilities found` (`pip-audit 2.10.1`) |
| 8 | Vulnerability audit (live venv) | `PIPAPI_PYTHON_LOCATION=/tmp/venv_phase52/bin/python pip-audit --vulnerability-service pypi` | **`0`** | `No known vulnerabilities found` |
| 9 | Full test suite in clean 3.12.10 venv | `/tmp/venv_phase52/bin/python -m pytest -ra` | **`0`** | **`969 passed, 21 skipped, 0 failed, 43 subtests passed`** (`192.11s`) |
| 10 | Synthetic end-to-end staging acceptance | `/tmp/venv_phase52/bin/python tools/staging_acceptance.py` | **`0`** | **`40/40 checks passed`** (including isolated backup restore and fresh-process verification) |

---

## 3. Verification of `opencv-python-headless` Substitution Across All Manifests

| File | Verified Declaration | Consistency |
| --- | --- | --- |
| `pyproject.toml` | `"opencv-python-headless==4.11.0.86"` (`opencv-python` absent) | Consistent |
| `requirements.lock.txt` | `opencv-python-headless==4.11.0.86` (`opencv-python` absent; substitution documented in header) | Consistent |
| `requirements.lock.hashes.txt` | `opencv-python-headless==4.11.0.86` with 27 PyPI SHA-256 digests (`opencv-python` absent) | Consistent |
| `tools/verify_dependency_graph.py` | `ALLOWED_SUBSTITUTIONS = {"opencv-python": "opencv-python-headless"}`; asserts `opencv_python.libs` GUI payload directory is absent | Consistent |
| `.github/workflows/ci.yml` | Installs with `--no-deps --require-hashes`, runs `verify_dependency_graph.py`, and asserts `cv2.__version__.startswith("4.11.")` + `RapidOCR` import without `libgl1` | Consistent |

---

## 4. Audit of Undeclared, Duplicated, Conflicting, or Unused Dependencies

An AST import scan across `dz_erp/`, `tests/`, and `tools/` combined with `importlib.metadata` reverse-dependency graph analysis produced the following exact findings:

1. **Undeclared dependencies:** **0.** Every root package in `requirements.lock.txt` (`19` direct runtime + `dev` packages, plus Windows-marker `colorama==0.4.6` required by `pytest`/`tqdm` on `win32`) is explicitly declared in `pyproject.toml` at the identical version (`0` version mismatches).
2. **Duplicated or conflicting distributions:** **0.** Only `opencv-python-headless==4.11.0.86` provides `cv2`; `opencv-python` (GUI) is not installed. Every installed distribution satisfies all version specifiers of its dependents.
3. **Dynamically loaded runtime/test packages (active, not unused):**
   - `python-multipart==0.0.33`: loaded dynamically by Starlette/FastAPI multipart upload parsing (`dz_erp/upload.py`, `dz_erp/api.py`, `tests/test_phase64_dependency_security_regression.py`).
   - `pytest-cov==6.0.0` & `pytest-asyncio==1.3.0`: loaded via `pytest` entry-point plugin discovery (`pyproject.toml` `asyncio_mode = "auto"`, CI `--cov=dz_erp`).
   - `ruff==0.14.0`: invoked as a CLI static analyzer (`python -m ruff check dz_erp tests tools`).
4. **Declared packages with no current application call sites (retained intentionally):**
   - `SQLAlchemy==2.0.40` (and its transitive `greenlet==3.5.6`): `dz_erp` uses stdlib `sqlite3` directly (`dz_erp/database.py`), so `dz_erp/` has no `import sqlalchemy` call site; however, `tools/verify_dependency_graph.py:74` (`REQUIRED_IMPORTS`) explicitly asserts `sqlalchemy` is importable, and `docs/DEPENDENCY_AND_LICENSE_REGISTER.md` registers it. Retained per mission rule (*"Do not remove dependencies based only on textual search"*).
   - `arabic-reshaper==3.0.0` and `python-bidi==0.6.6`: declared in `pyproject.toml` for Arabic text shaping/BiDi support (`LGPL-3.0` / `MIT`), zero advisories; retained for Arabic report/PDF export compatibility without altering runtime behavior.
5. **Optional `desktop` extra (`PySide6==6.11.2`):** Intentionally excluded from the headless server lockfile (`requirements.lock.txt`) so minimal Linux servers and containers require zero X11/OpenGL/Qt system libraries. When absent, the 18 Qt widget tests skip cleanly via `importorskip` / skip markers while Qt-free desktop modules (`config`, `i18n`, `api_client`, `service`, `formatting`, `tokens`) remain tested.

---

## 5. Unresolved Dependency Risks

| Risk ID | Description | Severity | Status & Mitigation |
| --- | --- | --- | --- |
| **H-1 (Upstream metadata)** | `rapidocr-onnxruntime==1.4.4` declares `opencv-python>=4.5.1.48`, causing `pip check` (and `pip install --require-hashes` without `--no-deps`) to report `opencv-python` missing. | Low (operational) | **Mitigated & Verified.** Standard lockfile installation (`pip install --no-deps --require-hashes -r requirements.lock.hashes.txt`) is 100% deterministic, exits `0`, and `tools/verify_dependency_graph.py` enforces the exact graph on every CI run. |
| **R-PROV (`RISK_REGISTER.md` B2)** | SHA-256 hashes pin artifact bytes on PyPI but do not verify PEP 740 / Sigstore publisher attestations. | Medium (external) | Tracked in `docs/RELEASE_READINESS_MATRIX.md` (`R-PROV`). Offline wheelhouse verification is supported via `tools/verify_wheelhouse.py`. |
| **Starlette `TestClient` deprecation warning** | `httpx==0.28.1` with `starlette==1.10.0` emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` during test runs. | Informational | Test-only warning; zero runtime impact. |
