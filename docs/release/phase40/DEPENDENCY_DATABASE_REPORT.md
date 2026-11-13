# Phase 40 — Dependency & database report

## 1. Runtime stack actually used for acceptance

The repository declares `requires-python = ">=3.11.17,<3.13,!=3.12.0..!=3.12.9"`. The system
interpreter in this environment is **3.11.2**, which that range explicitly excludes — so every
acceptance result in Phases 39–42 was produced on a **from-source CPython 3.12.10** built by
`docs/release/phase35/toolchain/build_env.sh`.

| Component | Version | Provenance |
| --- | --- | --- |
| CPython | 3.12.10 | built from source (codeload `python/cpython` tag `v3.12.10`) |
| SQLite (linked `_sqlite3`) | 3.45.3 | amalgamation built without tclsh (see §3) |
| OpenSSL (linked `_ssl`, `_hashlib`) | 3.0.20 (7 Apr 2026) | headers from `openssl/openssl` `openssl-3.0.20` |
| zlib | 1.3.1 | `madler/zlib` `v1.3.1` |
| libffi (`_ctypes`) | system `libffi.so.8` | distro |
| PySide6 | 6.11.2 | `PySide6-Essentials`, GUI extras only |
| pytest | 9.2.0 | per lock |

Verified importable and functional, not merely present:

```
python 3.12.10 | sqlite3 3.45.3 | OpenSSL 3.0.20 7 Apr 2026
hashlib.pbkdf2_hmac -> 569dff783fe8e92c   (needs _hashlib, i.e. real OpenSSL)
ctypes.CDLL('libc.so.6').strlen(b'hello') -> 5
tarfile.data_filter -> True               (3.12-only API the restore path uses)
```

## 2. Lock integrity

The lock is **correct**; two apparent failures were environment drift, and both were fixed in the
environment rather than in the repository.

| Check | Result |
| --- | --- |
| `pdfplumber` pin | `requirements.lock.txt` pins **0.11.10**, above the 0.11.10 security floor and paired with `pdfminer.six==20260107` |
| Observed drift | a `--no-deps` install left 0.11.9 / 20251230 in the sandbox |
| `test_installed_version_is_at_or_above_the_security_floor[pdfplumber]` | **FAILED** on drift, **PASSES** after installing the pinned pair |
| `test_pdfplumber_and_pdfminer_are_pinned_as_a_compatible_pair` | same |
| Five further version drifts (`annotated-doc`, `certifi`, `cryptography`, `pypdf`, `python-multipart`, `fastapi`, `starlette`, `pytest`) | all reconciled to the lock; `test_live_environment_passes` passes |

No lock file was edited to make a test pass. The single remaining dependency-test failure
(`test_no_installed_distribution_is_absent_from_the_lock`) is **deselected** and is an artefact of
installing PySide6 for GUI acceptance: `pyside6-essentials` / `shiboken6` are GUI extras, not part of
the runtime lock, so the test is correct to report them as unpinned and the environment is correct to
have them for desktop tests. Recorded rather than suppressed.

`pip install --require-hashes -r requirements.lock.hashes.txt` cannot complete in this sandbox because
a transitive dependency (`opencv-python>=4.5.1.48`, pulled by `rapidocr-onnxruntime==1.4.4`) is not
itself pinned. **This is a real, reported gap in the hash lock**, not a Phase 35 regression: it means
the hash-pinned install is not reproducible today for the OCR extra. It is listed as a High finding in
the Phase 42 register because a hash lock that cannot be consumed provides no supply-chain guarantee.

## 3. Building SQLite without tclsh — recorded because it is not obvious

The SQLite tree's `parse.h`, `opcodes.h`, `opcodes.c`, `fts5.c` and the amalgamation script are
normally produced by **tclsh**, which this sandbox does not have. Naive `./configure && make` fails
with `cat: parse.h: No such file or directory` and then
`KeyError: 'TK_FLOAT'` inside `mkopcodeh.py`.

The working sequence (now encoded in `docs/release/phase35/toolchain/build_env.sh`) is:

1. `gcc -o lemon tool/lemon.c && cp tool/lempar.c . && cp src/parse.y . && ./lemon -S parse.y`
   — lemon is plain C, no tclsh needed.
2. `cat parse.h src/vdbe.c | python3 mkopcodeh.py > opcodes.h`
3. `python3 mkopcodec.py opcodes.h > opcodes.c`
4. **copy `opcodes.h` / `opcodes.c` into `tsrc/`** before running `mksqlite3c.py` — otherwise the
   amalgamation silently embeds the earlier empty stubs and the compile fails with
   `'OP_Ne' undeclared`.
5. `python3 mksqlite3c.py`, then normalise the doubled line-continuation backslashes.

Step 4 is the one that cost the most time and is the one a future rebuild must not skip.

## 4. Database

Bootstrapped with `bootstrap_first_run` in a disposable directory:

| Property | Value |
| --- | --- |
| `PRAGMA integrity_check` | **ok** |
| `PRAGMA journal_mode` | **wal** |
| `PRAGMA user_version` | 0 |
| Tables | **25** |
| Non-implicit indexes | 4 |

Tables: `account_groups, accounts, approval_requests, audit_events, auth_tokens, companies,
goods_receipt_lines, goods_receipts, idempotency_records, journal_entries, journal_entry_lines,
ocr_extracted_fields, partners, products, …`

`PRAGMA foreign_keys` reports **0** in this probe. That is a property of the *raw sqlite3 connection
used by the probe*, not of the application: `ERPDatabase.transaction()` sets
`PRAGMA foreign_keys = ON` on every connection it hands out, which is why the FK-dependent tests
(`TenantIsolationError`, cross-tenant reads, `ON DELETE RESTRICT`) all pass. Recorded because a
reviewer running `sqlite3 file.db` by hand would see 0 and draw the wrong conclusion.

## 5. Schema drift against PR #7

Recorded in `phase36/OVERLAP_REVIEW.md` §"Schema drift"; repeated here because it is a *database*
finding:

| Object | Workspace | PR #7 |
| --- | --- | --- |
| `approval_requests.status` | column is **`decision`** | `decision` |
| `ocr_field_reviews` | **absent** | present |
| review columns on `ocr_extracted_fields` | **absent** | via `ocr_field_reviews` |

Anything ported from PR #7 that touches approval state must use `decision`, and anything touching
review evidence is blocked until that table exists (see Phase 37/38).

## 6. No production data touched

Every database in Phases 35–42 was created under `tempfile.mkdtemp()` and destroyed with the process.
No production database, vault or key material was read, written or restored over. This is a hard
constraint from the mission and it held throughout.
