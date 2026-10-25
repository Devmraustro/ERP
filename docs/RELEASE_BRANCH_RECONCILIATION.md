# Release Branch Reconciliation

**Snapshot commit:** `21f93b7912f2e13023cd4dd4cf10267178a2bf82`
**Snapshot date:** 2026-10-25
**Repository:** `Devmraustro/ERP`

This is a point-in-time snapshot of live branch, pull-request and CI metadata. The reconciliation work itself does not authorize a merge, deployment, or production use.

## 1. References verified

| Reference | State at snapshot commit |
|---|---|
| `main` | `0e106e05bc1328780e7a837a65b870d535d9f6d6` |
| Working branch | `arena/6c474c64-erp`; 69 commits ahead of `main`, 0 behind; clean worktree (untracked `.venv311/` only) |
| PR #1 | Open, unmerged; head `1b63c6ea341b8d73f1fbce84148bd5425e2e8571` on `arena/f74b041f-erp` |
| PR #2 | Open, unmerged; head `becf52b74f2dabbd3ad9291bd28e2a207ce6b720` on `arena/5c240e1d-erp` |
| Working branch versus PR #2 | 49 commits ahead, 0 behind |
| HEAD of the working branch | `21f93b7912f2e13023cd4dd4cf10267178a2bf82` (`test: make dependency fixture valid on Windows`) |

The active working branch is the more complete implementation candidate, but it has not yet been selected as the canonical PR review target. Do not treat PR #2 as containing the additional commits, and do not merge or rebase either branch blindly.

## 2. CI evidence for the exact snapshot commit — **VERIFIED**

The blocker recorded by the previous snapshot ("no verified current full-suite or CI result for the new commits") is **resolved**:

- GitHub Actions run [`40053241264`](https://github.com/Devmraustro/ERP/actions/runs/40053241264) — head SHA `21f93b7912f2e13023cd4dd4cf10267178a2bf82`, branch `arena/6c474c64-erp`, started 2026-10-24T02:13:03Z — **conclusion: `success`**.
- All three jobs green: *Test, coverage and static checks (Python 3.11)*, *Test, coverage and static checks (Python 3.12)*, *Key rotation, backup and recovery on Windows* (verified via the commit `check-runs` API for the exact SHA, not from an older run).
- The CI jobs install with `--require-hashes` from `requirements.lock.hashes.txt`, run `tools/generate_hash_lock.py --check`, `tools/verify_dependency_graph.py`, the `cv2`/RapidOCR import assertion, `ruff`, `compileall`, and the complete test suite with coverage — so every step is verified for this exact tree on both platforms and both supported Python versions.

## 3. Local verification reproduced on the snapshot commit (2026-10-25)

Independently re-executed on Windows 11 / CPython 3.11.9 from a clean checkout state, confirming the CI result rather than assuming it:

- Full suite: **529 passed, 18 skipped, 0 failed** (547 collected; run twice: 560 s and 477 s). The 18 skips are platform-conditional by design — 16 POSIX-only mode-bit/fd/symlink custody tests and 2 `systemd-analyze` availability checks — and execute on the Linux CI jobs.
- Coverage: **89 %** (3,785 / 4,261 statements) on Windows.
- `ruff check dz_erp tests tools` → all checks passed; `compileall` → clean.
- `tools/verify_dependency_graph.py` → OK (53 installed distributions; only the documented `opencv-python → opencv-python-headless` substitution); `tools/generate_hash_lock.py --check` → OK (51 pinned distributions, 1277 hashes, no drift).
- `python tools/staging_acceptance.py` → **37/37 checks passed** in an isolated synthetic state directory: no journal entry from OCR, SoD refusal, independent approval, posting balanced at 23,800.00 DZD, idempotent replay, duplicate refusal, vault + audit rotation with a mixed-version chain still verifying, checkpoint export/refusal/truncation detection, interrupted-rotation recovery, isolated restore with hashes + trial balance + audit chain verified, fresh-process reopen, and no key material in logs or journals.
- `python tools/risk_register_counts.py` → register summary consistent (31 rows).

## 4. Remaining production blockers

**Verdict: `CONDITIONAL GO — ISOLATED NON-PRODUCTION STAGING ONLY`.**

All engineering-actionable Critical and High risks in `docs/RISK_REGISTER.md` are Mitigated or Closed. What remains is external to this repository:

- **C1/C2/C4/C5/C6:** qualified review of SCF/accounting mappings, fiscal calculations, clearance-account treatment, retention and Algerian data-protection obligations (Expert-Comptable / fiscaliste / DPO sign-off).
- **S1/S3:** actual KMS/HSM or equivalent custody infrastructure and an enforced split-custody process (budget and operations decision).
- **O2 (residual):** OCR confidence calibration on representative observations before any auto-accept threshold.
- **O3 (residual):** Arabic recognition head; unsupported scans remain in manual transcription/review routing.
- **B2 (residual):** publisher provenance (PEP 740/sigstore) and a separately managed artifact mirror/wheelhouse.
- **D4/D7:** production-scale recovery drills and verification of the intended database topology under real load.
- **S4/D6 (residual):** operator-side confirmation of off-host checkpoint storage, scheduler installation and working alert delivery.

No production host, actual KMS/HSM, independent checkpoint destination, representative production-like scan corpus, or customer-specific professional approvals are evidenced.

## 5. Required next actions

1. ~~Obtain a working local checkout or a confirmed CI run for the exact latest working-branch SHA.~~ **Done** — run `40053241264` green for `21f93b7` (§2), plus the independent local reproduction (§3).
2. ~~Run the phase-20 checkpoint/monitoring tests, Ruff, compile checks, complete suite, and staging acceptance on that exact checkout.~~ **Done** — §3.
3. ~~Inspect all CI jobs and their actual head SHA before asserting success.~~ **Done** — §2 (per-commit `check-runs` inspection).
4. Review the full commit delta from PR #2 and select a canonical review target only after a file-by-file comparison.
5. Keep infrastructure and professional approval items blocked until evidence or signed approval is available.
6. Obtain separate explicit authorization before merging a PR or deploying.

## 6. Scope

No pull request was merged or closed. `main` was not modified. No production deployment, production key rotation/deletion, or processing of real customer data occurred. The snapshot edits in this document describe verified state; they change no code.
