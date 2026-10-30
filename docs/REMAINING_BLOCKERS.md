# Remaining blockers

What still stands between this repository and a defensible production
deployment — as of the verification of `0df3c33ff6fd96f59d04374b8c00006902ab62ed`
(branch `arena/7f12d627-erp`, PR #7 Draft).

Rule for this list: nothing is "done" because code exists, and nothing external
is done until the external party signs it.

---

## A. Engineering items still open

| ID | Item | Owner | Evidence that closes it |
|---|---|---|---|
| **F-02** | Durable request log grows with traffic and carries usernames + client IPs. Rotation exists (100 MiB × 10 in code, 10 MiB × 10 as shipped); retention *period*, age-based deletion, in-app access audit and off-host shipping do not. Reviewed in `docs/LOG_RETENTION_AND_ACCESS.md` (content verified credential-free, file mode `0600`). | Operations + DPO | A written retention schedule (with the legal reference) applied in configuration, plus evidence of off-host shipping or a documented decision not to ship. |
| **F-06** | Startup migrations are not one atomic unit (autocommit connections): each `ALTER` is atomic, the sequence is not. A crash leaves a partially upgraded database that the next open repairs. | Engineering | Either wrap the sequence in a single transaction (requires refactoring `_migrate_ocr_confidence`, which already manages its own), or keep the current contract and evidence the operator procedure (backup → upgrade → verify) in a drill. |
| **Multi-replica limiter** | Counters are per process by default, or shared across processes on **one** database file with `ERP_RATE_LIMIT_BACKEND=sqlite`. No distributed backend exists. | Engineering / Platform | A `RateLimitStore` implementation backed by a shared service **and** a test proving two instances share a counter, or an edge/proxy limit configured and evidenced by the operator. |
| **IPv6 aggregation** | The limiter keys on the address; an attacker with a /64 rotates addresses and obtains a new aggregate per address. | Engineering | Prefix-level keying (e.g. /64) plus tests, or an edge limiter that does it. |
| **Container image** | `ops/Dockerfile` and `ops/docker-compose.yml` are asserted by tests but the image has **never been built or run** — no `docker`, `podman`, `nerdctl`, `buildah`, `kaniko`, `img` or `/var/run/docker.sock` in the verification environment. | Platform | Build in an environment with a runtime; run compose with the bootstrap profile; restore a verified backup into the container; rotate a key inside it; confirm `X-Forwarded-For` handling matches `ERP_TRUSTED_PROXIES`. |
| **MFA / device binding** | Not implemented; password-only with throttling and lockout. | Engineering / Security | Decision record accepting the residual risk, or a second factor. |
| **Local interpreter floor** | CPython 3.11.2 refuses the safe-restore path by design, so 20 backup/restore tests fail locally on this host. Same names at baseline `852f389f`. CI on 3.11.17/3.12.10 passes them. | Environment | Run on 3.11.17+ or 3.12.10+ (already satisfied in CI). Not a product defect. |

## B. Security and operations evidence

| ID | Item | Owner | Evidence that closes it |
|---|---|---|---|
| **R-KMS** | Audit keys are file-backed by default; no KMS/HSM custody, no operator-performed rotation drill. | Security | Rotation executed on a real deployment with an off-host key store, chain verified before/after, custodian named. |
| **R-PROV** | No publisher attestation (PEP 740/Sigstore) verified for the pinned dependency set. | Release engineering | Attestation verification on the artefacts actually installed, or a recorded residual-risk acceptance. |
| **R-OFFHOST** | No evidence of a ledger checkpoint exported off-host under independent credentials. | Operations | Scheduler installed and firing, and an off-host checkpoint retrievable without host credentials. |
| **R-ALERT** | No alert channel. Detection exists; nobody is paged. | Operations | A synthetic failure delivered end-to-end through the real channel, with the receipt. |
| **R-DR** | No disaster-recovery drill, no established RTO/RPO. | Operations | Timed restore from the latest production backup into an isolated environment; db/vault hashes, trial balance and audit chains verified; measured RTO. |
| **R-CONC** | No concurrency evidence at production scale. | Operations / Engineering | A recorded run at target scale with WAL/busy-timeout/lock metrics inside documented bounds, or a written single-writer constraint bound into the deployment. |
| **R-OCR** | No authorised real-world corpus. | Operator (corpus owner) | Manifested, authorisation-signed corpus evaluated per `OCR_EVALUATION_PROTOCOL.md` §12, with the accuracy sign-off. |

## C. Accounting, tax and legal

| ID | Item | Owner | Evidence that closes it |
|---|---|---|---|
| **R-SCF** | Thirteen tax assumptions are implemented **as configured** and have never been reviewed by an Algerian expert-comptable: TVA rate set and applicability, exemption mapping, stamp-duty brackets, the 300.00 DZD threshold (inclusive), the 5.00 DZD minimum, cash-only applicability, the duty base, per-line rounding, discount treatment, G50 box mapping, fiscal-law effective dates, the IFU regime, workbook retention. Enumerated in `docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md` §8.1 of the previous pass and in `docs/ACCOUNTING_VALIDATION_AND_TAX_ASSUMPTIONS.md`. | Expert-comptable | A dated, signed review naming the legal reference (Finance Law article and date) for each item, confirming or correcting the implementation. |
| **R-LAW** | No Law 18-07 / ANPDP determination: no processing register, DPO appointment, retention schedule or transfer assessment. | Legal counsel / DPO | Written determination covering lawful basis, retention, transfers and breach notification. |
| **G50 filing** | The G50 output is a preparation workbook with traceability, not a filing. | Expert-comptable | A filed return produced from the workbook, or written confirmation that the format matches what the accountant uses. |
| **Statutory sign-off** | No statutory accounts, auditor-accepted trail, or fiscal-representative attestation. | Expert-comptable / Auditor | The signed attestation. |

## What is *not* on this list

The duplicate-row integrity work, the bounded limiter (including its shared
backend), the effective-dated tax resolution, the immutable stamp-duty provenance,
the migration matrix and generation evidence, the deployment artefact properties
and the documentation set are verified against the exact head and are not blocked.
Their classification and reproduction commands are in
`docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md`.

## Suggested order

1. **F-06 + F-02** — small, engineering-only, closes the last two code findings.
2. **Container build** — needs a runtime; everything else about the image is asserted.
3. **Alerting / off-host / DR / concurrency** — one real deployment and a named operator.
4. **R-SCF / R-LAW** — longest lead time; start first even though they finish last.
