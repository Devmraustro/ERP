# Residual Risks — ERP Algeria

Assessment date: 2026-11-09 · Branch `arena/7557bcfb-erp` · HEAD
`4fc1364a9504fab1153ea44e753f354f00023428`

This register lists what remains after remediation. An item is listed here
because it is real, known and **unfixed or unfixable within this authorized
scope** — not because it was overlooked. Nothing in this document upgrades an
untested control to "verified".

## 1. Open risks carried forward by decision

| ID | Risk | Severity | Why it remains open | Owner action |
| :--- | :--- | :--- | :--- | :--- |
| SEC-11 | Dependency advisories | Low (re-measured) | The pins named in earlier revisions (`starlette 0.46.2`, `cryptography 44.0.2`, `pillow 11.1.0`, `pypdf 5.4.0`, `pdfminer.six 20250327`, `python-multipart 0.0.20`, `certifi 2026.7.22`, `setuptools 66.1.1`) no longer exist in the lockfile; it now pins `starlette 1.3.1`, `cryptography 50.0.0`, `pillow 13.0.0`, `pypdf 6.21.4`, `pdfminer.six 20251230`, `python-multipart 0.0.31`, `certifi 2026.10.18`. All 53 locked packages were checked on 2026-11-09 against the GitHub Advisory Database: **0 matches**; the same detector flags **8/8** of the old pins, so the clean result is not vacuous | Repository CI must still run `pip-audit`/OSV as a blocking gate (that endpoint is unreachable from the verification sandbox) |
| SEC-12 | No in-application rate limiting (per-account lockout only) | Low | Architecture: the project documents this as a proxy responsibility (`docs/AUTHENTICATION_HARDENING_SPEC.md` §6.5) | Configure `limit_req`/WAF at the proxy; alert on `AUTH_ACCOUNT_LOCKED` |
| SEC-13 | ~~Password policy is length-only (≥8); `password`, `12345678` accepted~~ | **CLOSED** (Phase 4) | One policy chokepoint now serves every credential path, including breached/common-password refusal and the rotation/reset endpoints; see `PHASE4_SEC131415_CLOSURE_REPORT.md` | Re-verify in CI: `tests/test_phase59_sec13_14_15_closure.py` |
| SEC-14 | ~~OCR field `new_value` stored without type/range validation~~ | **CLOSED** (Phase 4) | The legacy write path validates against the field's value class before storing; see `PHASE4_SEC131415_CLOSURE_REPORT.md` | Keep the phase-59 acceptance tests blocking |
| SEC-15 | ~~No app-level Host validation or TLS; uvicorn serves plain HTTP~~ | **CLOSED (in-process part)** (Phase 4) | `Host` allow-list enforced, uvicorn's proxy-header layer disabled explicitly; TLS termination remains the proxy's job by design | Enforce TLS/HSTS at the edge (unchanged deployment action) |

## 2. Risks introduced or shaped by the remediation itself

| Risk | Assessment |
| :--- | :--- |
| Role guard could deny a legitimate internal workflow | The allow-list is exactly the documented matrix (`clerk`, `approver`, `accountant`, `admin`), and positive-control tests cover all four. Internal service-level callers (`upload_and_process_supplier_document`, `update_extracted_field_by_human`) are deliberately unchanged, so batch/desktop-style flows that bypass HTTP keep working |
| `to_decimal` now rejects magnitudes ≥ 10^18 | No legitimate DZD amount or quantity approaches 10^18; the existing suite (which includes precision and rounding boundary tests) passes unchanged |
| Body cap could reject a legitimate large invoice payload | 2 MiB of JSON is roughly 10,000 invoice lines; `ERP_MAX_REQUEST_BYTES` raises it if a real workflow needs more. Only `Content-Length` is inspected |
| `line_ht_dzd` now derived when omitted | The derivation is the pre-existing `expected_line_ht` expression, not a new formula; a caller that *does* declare the amount is still compared against it and flagged on mismatch |
| Derived line net changes the payload hash for invoices created **without** a declared line net | Only affects newly created rows; the hash is stored and compared against the stored value at approval, never recomputed from a different input, so no existing approval binding is invalidated |
| Backup `0600` could break a backup pipeline that reads as another user | The archive is written by the same process that creates it; a restore runs as the service user. If an external collector needs group read, it must be granted explicitly (e.g. a dedicated shared group) rather than by leaving the file world-readable |

## 3. Environment-caused gaps (must be closed elsewhere before promotion)

| Gap | Consequence | Required action |
| :--- | :--- | :--- |
| ~~Python 3.11.2 < declared floor 3.11.17~~ | **CLOSED** — CPython 3.12.10 was built and the full matrix ran (1373 passed / 0 failed); the 20 gated tests now pass | See §3.1 and `PHASE5_CI_312_EXECUTION_REPORT.md` |
| No staging host/container/volume reachable | Authentication boundary, RBAC and tenant isolation were verified against the **code**, not the running `ops-erp-1` deployment | Execute the staging acceptance procedure (`tools/staging_acceptance.py`) against the real staging endpoint |
| No AWS credentials / test CMK | AWS KMS live verification is **NOT VERIFIED**; the build intentionally refuses `ERP_KEY_PROVIDER=kms\|hsm` | Integrate a real provider before any claim of hardware-backed custody; until then this build's custody is file-based |
| No Docker available | Container image / OS package CVE gate not run here | Repository CI performs that gate; keep it blocking |
| B2 packaging (flat-layout) | `pip wheel .` cannot build until the preserved Phase-3 patch is merged | V1 deploys from the source tree by decision (`PACKAGING_DECISION_V1.md`); merge the packaging fix before any wheel-based delivery |
| No PySide6 | Desktop UI end-to-end paths untested here | Desktop acceptance on a workstation image |

### 3.1 B1 — interpreter floor: **CLOSED** (2026-11-09)

`safe_restore_supported` requires `(3,11,17)` or `(3,12,10)` and refuses `>= 3.13`.
On 3.11.2 it correctly refused, and all 20 failing tests carried that single root
cause (`dz_erp/security_audit.py:1368`) — they were gated, not broken.

No downloadable interpreter was reachable, so CPython **3.12.10** was built from
the upstream tag (`codeload.github.com/python/cpython`) with OpenSSL 3.0.15,
zlib 1.3.1, SQLite 3.53.4 and libffi 3.4.6 headers, and the lockfile was installed
with `--require-hashes`. On that interpreter:

* full suite: **1373 passed, 0 failed, 6 skipped**;
* the 20 previously gated tests: **20 passed**;
* restore matrix, key-rotation restore, isolated restore, trial-balance
  verification, audit-chain integrity and vault backup/restore E2E: all executed.

Evidence: `docs/security/PHASE5_CI_312_EXECUTION_REPORT.md`,
`docs/security/phase5_ci_312/`. The 3.11.2 blocker evidence is kept unchanged in
`docs/security/phase5_release_gate/` for audit continuity.

### 3.2 B2 — packaging: open, with an explicit V1 decision

`pip wheel .` still fails on the flat-layout error; the preserved Phase-3 patch
fixes it but remains a separate change. **V1 ships from the source tree**, and the
wheel is not offered as a deployment artefact — it carries no `ops/` assets, no
lockfile and no server entry point. See `docs/security/PACKAGING_DECISION_V1.md`.

## 4. Threat-model risks that no code change in scope can remove

1. **Insider with database + key-file access.** The audit chain is HMAC-based
   with keys stored beside the database (or in `ERP_AUDIT_HMAC_SECRET`). An
   operator who can read *both* the database and the key material can forge a
   chain. Mitigation is custody separation (KMS/HSM, off-host checkpoints), not
   application code — the project's `docs/KEY_CUSTODY_THREAT_MODEL.md` states
   this, and the checkpoint/off-host timer in `ops/` exists for that reason.
2. **Compromise of the backup key.** `ERP_BACKUP_ENCRYPTION_KEY` protects an
   archive that intentionally contains the vault and audit key rings. Rotation
   and custody of that single secret is therefore a high-value control.
3. **Compromised trusted proxy.** `ERP_TRUSTED_PROXIES` is honoured as an
   allow-list; misconfiguring it (listing a wide range) hands IP spoofing back
   to the caller for `issued_to_ip` and any future IP-based rule.
4. **Tenant data on a shared host.** The vault root directory is `0755`; blobs
   are `0600` and encrypted, so another local user cannot read ciphertext *or*
   key material, but they can see the directory structure and per-company
   existence. Tightening the root to `0750` is a hardening option for shared
   hosts.
5. **OCR as a trust boundary.** Even with human review, a reviewer can be
   socially engineered into approving a value the engine mis-read. The project's
   confidence/provenance model (`docs/OCR_EVALUATION_PROTOCOL.md`,
   phase-18/21 tests) is the mitigation; it is a process control, not an
   application guarantee.

## 5. Accepted false positives (do not re-report)

* Reading `/etc/passwd` via a poisoned `vault_path` — refused
  (`TenantIsolationError`); FP-01.
* Filename newline log injection — the multipart parser cannot deliver a raw
  newline; the percent-encoded name is stored as inert text; FP-02.

## 6. Residual-risk statement

With SEC-01…SEC-10 remediated and regression-tested, the exploitable core of the
application boundary (authentication, tenant isolation, RBAC, upload, vault,
audit chain, key handling) holds under the tests that could be executed here.
The remaining exposure is concentrated in **dependency versions** (SEC-11), in
**deployment configuration** (SEC-12, SEC-15), and in the **unexecuted DR/staging
verification**. Those three are the reasons the overall classification is
`SECURITY VALIDATION PASSED WITH RESIDUAL RISKS` rather than an unqualified pass.

## 7. Observations recorded during the Phase-5 execution

| Observation | Assessment | Action |
| :--- | :--- | :--- |
| A restore that fails because the disk is full reports "Backup archive contains an unsafe or invalid tar entry … [Errno 27] File too large" | Behaviour is correct (refuses, writes nothing, leaves the archive intact) but the message points the operator at the archive's contents instead of at exhausted storage | Reword the extraction wrapper to distinguish write failure from archive corruption — its own reviewed change |
| Vault root and per-company directories are `0755` while blobs and key material are `0600` | Already recorded in §4.4; unchanged by this phase | Tighten to `0750`/`0700` on shared hosts |
| No dedicated ENOSPC test for a direct `store_bytes` inside an existing vault | The backup path's write-exhaustion case is covered (phase 60); the vault's own store path is not | Add a phase-60 case when next touching the vault |
| The Phase-3 wheel artefact recorded in `phase3_blocker_closure/SHA256SUMS` is still absent, and wheels are not byte-reproducible, so a rebuild cannot restore that hash | Known; the rebuild is evidence that the packaging patch works, not a recovery of the lost file | Re-attach the original artefact or regenerate the checksum line with a review note |
