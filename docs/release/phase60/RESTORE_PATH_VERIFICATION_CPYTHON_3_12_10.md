# Phase 60 — Restore path executed on CPython 3.12.10

**Feature freeze:** still in force. This phase changes **no application code, no test and no CI
file**. It executes what phase 59 had to report as `NOT EXECUTED` because the sandbox interpreter
(3.11.2) is below the restore floor, and records the result.

| | |
|---|---|
| Code under test | commit `c03062f0093876996eef9e98bb93726d850ea2cf` (phase 59), unmodified |
| Date | 2026-11-14 |
| Interpreter | CPython **3.12.10**, built from source in the sandbox (§1) |
| Platform | Debian 12 (bookworm), x86-64, GCC 12.2.0 |

## 1. How a compliant interpreter was obtained

Phase 59 recorded four failed attempts (release assets, `pbs_installer`, apt, source build without
headers). The source build is possible after all, because `codeload.github.com` *source archives* are
reachable even though release assets are not, and only the **headers** were missing — the shared
libraries are already in the image.

| Piece | Source | Used as |
|---|---|---|
| CPython | `python/cpython` tag `v3.12.10` | built with `./configure && make && make install` into `/tmp` |
| `zlib.h`, `zconf.h` | `madler/zlib` tag `v1.2.13` | headers only; linked to the system `libz.so.1` (1.2.13) |
| `openssl/*.h` | `openssl/openssl` tag `openssl-3.0.20`, `./Configure && make build_generated` | headers only; linked to the system `libssl.so.3` / `libcrypto.so.3` (3.0.20) |
| `sqlite3.h` | `rhuijben/sqlite-amalgamation` tag `3.40.1` | header only; linked to the system `libsqlite3.so.0` (3.40.1) |
| `ffi.h`, `ffitarget.h` | `libffi/libffi` tag `v3.4.4`, `ffi.h.in` substituted for x86-64 | headers only; linked to the system `libffi.so.8` (3.4.4) |

Each header version was chosen to equal the version of the shared library already installed.

```
3.12.10 (main, Nov 14 2026) [GCC 12.2.0]   OpenSSL 3.0.20 7 Apr 2026   SQLite 3.40.1
```

**Limits of this interpreter — stated, not hidden:**

* It is a local, non-optimised source build. It is **not** the python.org Windows installer and not
  the `actions/setup-python` binary that CI uses.
* Optional modules not built (no headers): `_bz2`, `_lzma`, `_uuid`, `_curses`, `_dbm`, `_gdbm`,
  `_tkinter`, `readline`, `nis`. Nothing in `dz_erp` or its locked dependencies needed them — the
  whole suite collected and ran.
* Nothing was installed into the repository or system paths; the build lives under `/tmp` and is
  discarded with the sandbox.

## 2. Dependency installation — same commands as CI

| Check | Result |
|---|---|
| `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | installed, every hash verified |
| `python tools/generate_hash_lock.py --check` | **OK** — 53 pins, 1317 hashes, no drift |
| `python tools/verify_dependency_graph.py` | **OK** (documented `opencv-python` → headless substitution only) |
| `import cv2` / `from rapidocr_onnxruntime import RapidOCR` | **OK** — `cv2 4.11.0` |
| `python -m ruff check dz_erp tests tools` | **All checks passed** |
| `python -m compileall -q dz_erp tests tools` | **clean** |
| `python tools/generate_hash_lock.py --desktop --check` | **OK** — 4 pins, 20 hashes |

## 3. Full suite on 3.12.10

```
python -m pytest -q -ra
=========== 1007 passed, 21 skipped, 1 warning in 246.14s (0:04:06) ============
```

| | 3.11.2 (phase 59) | 3.12.10 (this run) |
|---|---|---|
| Failed | **22** — all the interpreter guard | **0** |
| Passed | 1012 | 1007 |
| Skipped | 3 (Windows-only) | 21 = 3 Windows-only + 18 Qt (§5) |

The lower pass count is entirely the Qt skips (two Qt modules skip at import and count once each).
**No test fails on a supported interpreter.** The single warning is Starlette's `httpx`
deprecation notice.

The restore-path tests phase 59 listed as unexecuted, by name, in this run:

| Test | Result |
|---|---|
| `test_a_pre_versioning_database_restores_from_backup` | PASS |
| `test_restore_with_a_contradicting_audit_key_is_refused` | PASS |
| `test_restore_reports_a_missing_key_version_as_unavailable` | PASS |
| `test_restore_rejects_noncanonical_tar_paths_before_writing[../outside.txt]` | PASS |
| `test_restore_rejects_noncanonical_tar_paths_before_writing[vault/../outside.txt]` | PASS |
| `test_restore_rejects_noncanonical_tar_paths_before_writing[../evil/../dest/sub/file]` | PASS |
| `test_restore_refuses_preexisting_hardlink_target_before_writing` | PASS |
| `test_repeated_restores_are_idempotent_and_leave_a_usable_deployment` | PASS |

Also executed and green for the first time in a sandbox session: `tests/test_phase6_ocr_benchmark_e2e.py`
(the 14-step OCR E2E), the key-rotation suites (phases 10–16) and the key-migration suite (phase 12).

## 4. Staging acceptance and a hand-run restore drill

**`python tools/staging_acceptance.py` → `40/40 checks passed`** (phase 59: 38/40). The two that were
red are now:

```
[PASS] restore: isolated restore verifies hashes, trial balance and audit chain
       db_sha256_verified: True, vault_files_verified: 2,
       trial_balance_debit_dzd: 23800.00, trial_balance_credit_dzd: 23800.00, audit_chains_valid: True
[PASS] restart: a new OS process reads the restored data
```

**CLI drill**, on a freshly bootstrapped deployment (`serve --bootstrap-admin`, SCF chart loaded),
with `ERP_BACKUP_KEY_FILE` set to a `0o600` key outside the backup directory:

```
python -m dz_erp.ops_backup run            -> erp-20261114T125627Z.erpbackup, 39608 bytes, manifest written
python -m dz_erp.ops_backup verify --deep  -> {"deep": true, "verified": 1, "failed": 0, "failures": [], "ok": true}
```

Archive, manifest and `verify-status.json` are `-rw-------`.

This is a **software** drill on synthetic data on one host. It is not a disaster-recovery test: no
off-host copy was restored and **no RPO/RTO figure is derived from it**.

## 5. What is still NOT EXECUTED

* **Desktop (Qt) suites on 3.12.10.** PySide6 6.11.2 installed from the hash lock, but this image has
  no `libGL.so.1` / `libEGL` / `libxkbcommon` / `libdbus-1`, and they cannot be installed (no apt
  mirror). The 18 Qt tests therefore **skipped** here. CI's `verify-desktop` job installs those
  libraries and forbids skips; it is the authoritative run.
* **Windows** — install, service, reparse-point tests (3 skips), UAT with screenshots, High-DPI.
* **CI on the candidate SHA** — not asserted by this document.
* **`pip-audit`** — not re-run in this phase; the lockfiles are byte-identical to phase 59.

## 6. Effect on the gates

| Gate | Phase 59 | Now |
|---|---|---|
| `G1` Build & test integrity | PASS, "the only reds are the interpreter floor" | **PASS — 0 red on 3.12.10** |
| `G4` Data protection (software behaviour) | PASS with a caveat (restore not executed) | **PASS** — restore, deep verify, key migration and tar-hardening executed on Linux; Windows restore still unexecuted |
| `G5` Algerian expert sign-off | BLOCKED | **BLOCKED** — unchanged, `Q1`–`Q12` PENDING |
| `G6` Production infrastructure | BLOCKED | **BLOCKED** — unchanged |

**Classification is unchanged: `INTEGRATION COMPLETE — RELEASE BLOCKED`.** The third blocker phase 59
named (restore path unverified) is closed for Linux; `G5` and `G6` remain and neither can be closed
from inside this repository.

## 7. Reproduce

```bash
B=/tmp/b; P=$B/dep; L=/usr/lib/x86_64-linux-gnu; mkdir -p $P/include $P/lib; cd $B
get() { curl -sSL "https://codeload.github.com/$1/tar.gz/refs/tags/$2" | tar xz; }
get python/cpython v3.12.10; get madler/zlib v1.2.13; get openssl/openssl openssl-3.0.20
get rhuijben/sqlite-amalgamation 3.40.1; get libffi/libffi v3.4.4
cp zlib-1.2.13/{zlib,zconf}.h sqlite-amalgamation-3.40.1/sqlite3{,ext}.h libffi-3.4.4/src/x86/ffitarget.h $P/include/
sed -e 's/@VERSION@/3.4.4/;s/@TARGET@/X86_64/;s/@HAVE_LONG_DOUBLE@/1/;s/@FFI_EXEC_TRAMPOLINE_TABLE@/0/' \
    libffi-3.4.4/include/ffi.h.in > $P/include/ffi.h
(cd openssl-openssl-3.0.20 && ./Configure linux-x86_64 && make build_generated && cp -r include/openssl $P/include/)
for l in sqlite3.so.0 z.so.1 ssl.so.3 crypto.so.3 ffi.so.8; do ln -sf $L/lib$l $P/lib/lib${l%%.so*}.so; done
(cd cpython-3.12.10 && CPPFLAGS=-I$P/include LDFLAGS=-L$P/lib LIBFFI_CFLAGS=-I$P/include LIBFFI_LIBS="-L$P/lib -lffi" \
    ./configure --prefix=$B/py312 --with-openssl=$P --with-openssl-rpath=no --without-ensurepip && make -j3 && make install)
$B/py312/bin/python3 -m venv --without-pip $B/v312
W=$(ls cpython-3.12.10/Lib/ensurepip/_bundled/pip-*.whl); $B/v312/bin/python $W/pip install $W
cd <repo>; $B/v312/bin/python -m pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
$B/v312/bin/python -m pytest -q -ra && $B/v312/bin/python tools/staging_acceptance.py
```

The header versions must match the shared libraries in the image; check with
`strings libcrypto.so.3 | grep "OpenSSL 3"` and `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"`.

*This document is not a legal opinion, a certification, or approval by any Algerian authority or
professional.*
