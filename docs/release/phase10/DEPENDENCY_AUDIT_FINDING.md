# Phase 10 — Dependency audit finding (OPEN, P0 for production)

> ## STATUS UPDATE — 2026-11-10: **REMEDIATED**
> The finding below is preserved exactly as it was written. It has since been
> closed: the pins were raised, the hash lock regenerated, and both
> `pip-audit -r requirements.lock.txt --no-deps --disable-pip` and an audit of
> the installed clean environment now report **zero known vulnerabilities
> (exit 0)**. See `DEPENDENCY_REMEDIATION_REPORT.md` for the executed
> remediation, the before/after versions and the evidence paths. The analysis in
> §3–§5 below remains the record of *how* the problem was found and why the lock
> was not changed at the time.

**Status: OPEN — blocking. No lock change was made in this phase.**
**Finding date:** 2026-11-10 (audit re-run during Phase 10 evidence collection).
**Severity:** High. Affects libraries that parse attacker-supplied documents.

This document exists because the Phase 10 audit **contradicts** the Phase 9
statement that `requirements.lock.txt` carried no known vulnerabilities. The
command, tool version and raw output are recorded here, together with a
reproduced feasibility probe. Nothing below is inferred.

---

## 1. Exact command and output

```
$ /tmp/vaudit/bin/pip-audit --version
pip-audit 2.10.1

$ /tmp/vaudit/bin/pip-audit -r requirements.lock.txt --no-deps --disable-pip
Found 216 known vulnerabilities in 8 packages
Name             Version   ID                  Fix Versions
---------------- --------- ------------------- ------------
certifi          2026.7.22 PYSEC-2026-4320     2026.10.18
cryptography     44.0.2    PYSEC-2026-2141     46.0.5
...
starlette        0.46.2    PYSEC-2026-1941     0.47.2
$ echo $?
1
```

- Raw table: `evidence/logs/pip_audit.log` (219 rows, duplicates included).
- Machine-readable: `evidence/logs/pip_audit_full.json`.
- Per-package summary: `evidence/logs/pip_audit_summary.json`.
- Advisory text sampled from the JSON (to confirm the records are real and not a
  truncated mirror artefact), e.g. `PYSEC-2026-2249` — *"An out-of-bounds write
  may be triggered when loading a specially crafted PSD image"* — and
  `PYSEC-2026-2250` — *"Pillow did not limit the amount of GZIP-compressed data
  read when decoding a FITS image … decompression bomb"*.

## 2. Counts (distinct advisories, not rows)

| Pin | Version | Distinct advisories | Fix versions reported |
| --- | --- | --- | --- |
| `pypdf` | 5.4.0 | 67 | 6.4.0 → 6.21.4 |
| `pillow` | 11.1.0 | 53 | 12.1.1 / 12.2.0 / 12.3.0 / 13.0.0 |
| `starlette` | 0.46.2 | 7 | 0.47.2 / 0.49.1 / 1.0.1 / 1.1.0 / 1.3.0 / 1.3.1 |
| `cryptography` | 44.0.2 | 6 | 46.0.5 / 46.0.6 / 48.0.1 / 49.0.0 / 50.0.0 |
| `python-multipart` | 0.0.20 | 6 | 0.0.22 → 0.0.31 |
| `pdfminer-six` | 20250327 | 2 | 20251107 / 20251230 |
| `certifi` | 2026.7.22 | 1 | 2026.10.18 |
| `pytest` | 8.3.5 | 1 | 9.0.3 |
| **Total** | 8 of 50 pins | **143** | — |

`pdfplumber==0.11.6` is not itself listed, but it **hard-pins the vulnerable
`pdfminer.six==20250327`**, so it must move with it (see §4).

## 3. Why this is material for this product

The affected packages are exactly the ones that touch hostile input in the
supplier-document pipeline:

- `pypdf` + `pdfminer.six` + `pdfplumber` — every PDF that a supplier uploads.
- `pillow` — image OCR sources (PSD/FITS/font handling are reachable via
  crafted uploads; `Pillow` is also an OCR engine dependency).
- `python-multipart` — the multipart parser sitting in front of uploads.
- `starlette` / `cryptography` / `certifi` — request handling and TLS trust.

A remote attacker who can upload a document (an unauthenticated supplier portal
or a compromised supplier account) reaches the first three directly.

## 4. What was and was not done

**Not done:** `requirements.lock.txt` and `requirements.lock.hashes.txt` are
**unchanged**. Reason: this is a cascading change (9 packages, at least two new
transitive pins), it requires hash regeneratation and a dedicated regression
review, and blind upgrades of document parsers in a production ERP should not be
smuggled into a reporting phase.

**Done — feasibility probe (reproducible, this host, 2026-11-10):**

1. Candidate lock `evidence/dependency_remediation_candidate.lock.txt`
   (byte-for-byte the current lock with only these pins changed):
   `certifi 2026.10.23`, `cryptography 50.0.2`, `fastapi 0.162.3`,
   `pdfminer.six 20260107`, `pdfplumber 0.11.10`, `pillow 13.0.0`,
   `pypdf 7.0.1`, `python-multipart 0.0.33`, `starlette 1.10.0`
   (`pytest` deliberately held at 8.3.5 — see §5).
2. **Resolution:** clean (`pip install -r` in a fresh venv, exit 0). The cascade
   only works because `pdfplumber 0.11.10` pins `pdfminer.six==20260107`;
   `pdfplumber 0.11.6` pins the vulnerable `20250327` and therefore blocks the
   fix.
3. **Compatibility probe:** a venv carrying the upgraded set ran the full suite:

   ```
   1 failed, 780 passed, 6 skipped, 1 warning in 124.94s
   FAILED tests/test_phase19_dependency_integrity.py::test_live_environment_passes
     - 'declared dependency fastapi==0.115.12 but fastapi==0.162.3 is installed'
   ```

   The **only** failure is the lock-vs-environment assertion, i.e. it fails
   *because* the lock had not been updated. Application-level compatibility is
   therefore demonstrated: 780 of 781 tests pass on the upgraded stack,
   including the OCR, upload-boundary, authentication and audit-chain suites.
4. **Blocker found for a clean `--no-deps` lock:** `fastapi 0.162.3` requires
   `annotated_doc`, which is not in the lock; installing the candidate with
   `--no-deps` (the documented install mode) produced
   `ModuleNotFoundError: No module named 'annotated_doc'`. Additionally
   `pytest 9.2.0` requires `pygments`, also unpinned. Any accepted remediation
   must add these pins and regenerate hashes for them.

## 5. `pytest` advisory — deliberate exclusion

`pytest 8.3.5` is a development/CI tool: it executes first-party test code and
is not shipped, not importable in the runtime image, and not reachable from
request handling. Upgrading it to 9.x additionally drags in `pygments` and
changes test collection defaults. Recommendation: bump it inside the same
remediation change *if* the suite stays green, but do not treat it as a
production exposure. This exclusion is recorded rather than silently dropped.

## 6. Required remediation (proposed, NOT executed)

```
# 1. New branch off main, dedicated PR (not PR #7)
# 2. Edit requirements.lock.txt:
#      certifi, cryptography, fastapi, pdfminer.six, pdfplumber, pillow,
#      pypdf, python-multipart, starlette  (versions as in §4.1)
#    + add: annotated_doc==<version>, pygments==<version>  (and any further
#      resolver-required pin), each pinned exactly
# 3. python tools/generate_hash_lock.py --write
# 4. python tools/generate_hash_lock.py --check        -> exit 0
# 5. Fresh venv: pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
# 6. python -m pytest -q                               -> 781 passed, 6 skipped
# 7. pip-audit -r requirements.lock.txt --no-deps --disable-pip  -> exit 0
# 8. Re-run the OCR benchmark and the operational drill (parse-path behaviour
#    changed: pdfplumber 0.11.6 -> 0.11.10, pypdf 5 -> 7, pillow 11 -> 13)
# 9. Record before/after in docs/release/phase10/DEPENDENCY_REMEDIATION_REPORT.md
```

**Acceptance:** step 7 exits 0 (or every residual advisory is individually
triaged and signed off), step 6 is green, step 8 shows no regression in mean OCR
latency or extraction accuracy on the synthetic corpus.

**Owner decision required:** whether to perform this now (it is the largest
remaining production blocker that is entirely within engineering control) or to
ship to staging with the advisory set accepted in writing by the security owner.

## 7. Effect on the Phase 10 classification

This finding is one of the reasons the release is classified
**CONDITIONALLY PRODUCTION READY / IMPLEMENTATION VERIFIED** and **not**
production ready. A green functional suite does not compensate for 143 open
advisories in the document-parsing path.
