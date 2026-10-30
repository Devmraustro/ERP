# Remaining blockers

What still stands between this repository and a defensible production
deployment — as of the independent verification of `2026-10-30` on
`8b1b56c98dd3d7c204cd64f25fea2f9a4a5df595`.

Two rules govern this list. Nothing appears as "done" without evidence produced
by someone other than its author, and nothing external is counted as done at all
until the external party has signed it.

Columns: **ID** · what is missing · who must supply it · what evidence closes it ·
where the code or claim lives.

---

## A. Engineering findings from the verification pass

| ID | Blocker | Owner | Evidence that closes it |
|---|---|---|---|
| **F-01** | Rate-limiter keys are never evicted. Measured: 5 000 distinct usernames ⇒ 5 000 retained entries, still 5 000 after the window expired; 50 unauthenticated requests created 50 entries. The key includes the caller-supplied username, so the growth is attacker-controllable. | Engineering | LRU cap or write-time sweep of expired buckets in `dz_erp/rate_limit.py`, plus a test asserting a bounded number of key buckets after N distinct usernames. |
| **F-04** | A posted invoice stores `amount_timbre_dzd` but not *which* rule produced it: no `timbre_rule_id` / `rule_version` / VAT-rate reference column exists, and `timbre_rules` has no append-only guard. | Engineering | Persist resolved rule ids + label on the invoice at posting; make the rule tables append-only (trigger) or versioned; a test that re-reads a posted invoice's rule after a configuration change. |
| **R-CONC (code side)** | The limiter is process-local (`_events: Dict[str, Deque[float]]`, self-described as `per-process (per worker; not a global bound across replicas)`), and the compose file pins `replicas: 1`. Any scaling to N workers multiplies the effective ceiling by N. | Engineering / Platform | A shared-store limiter (Redis or the database) **plus** a test that two instances share the counter, or a documented proxy-level limit that the operator configures and evidences. |
| **R-IMG** | The container image has never been built or run. No `docker`, `podman`, `nerdctl`, `buildah`, `kaniko` or `img` binary and no `/var/run/docker.sock` exists in the verification environment. | Platform | Build once in an environment with a runtime or `docker/build-push-action`; run `compose up` with the bootstrap profile; restore a verified backup into the container; rotate a key inside it; confirm `X-Forwarded-For` handling matches `ERP_TRUSTED_PROXIES`. |
| **F-05** | The migration matrix proves structural properties against *simulated* legacy shapes (modern columns dropped), not a real historical database dump. | Engineering | Replay a real pre-Phase-29 `erp.sqlite3` in staging and diff the audit chain before/after. |

## B. Security gates that no amount of code can close here

| ID | Blocker | Owner | Evidence that closes it |
|---|---|---|---|
| **R-KMS** | Audit keys are file-backed by default. No KMS/HSM custody, no key-rotation drill performed by an operator, no evidence of key-material separation from the host. | Security | A rotation executed on a real deployment with an off-host key store, with the audit chain verified before and after, and the custodian named in the record. |
| **R-PROV** | No verified publisher provenance: no signed release artefacts, no reproducible-build attestation, no SBOM attestation accepted by a consumer. | Release engineering | A signed release (Sigstore or equivalent) plus a consumer-side verification transcript. |
| **R-ALERT** | No alerting channel exists. The readiness checker and the durable security log can detect, but nobody is paged. | Operations | A configured destination (e-mail, SMS, on-call tool), a synthetic failure that produced an alert, and the receipt. |
| **R-OFFHOST** | No off-host backup. The runbook describes the procedure; no backup has been shipped off the host and restored from there. | Operations | A restore performed from off-host storage, with RTO/RPO measured against the agreed targets. |
| **R-DR** | No disaster-recovery drill, no established **RTO/RPO** numbers. | Operations | A timed drill from a clean host, with the measured recovery time and data-loss window signed off. |
| **R-CONC (ops side)** | No evidence of behaviour at production concurrency. The gate is race-safe by transaction serialisation in a single-writer SQLite deployment; N-process writers on one database file are unsupported and untested. | Operations / Engineering | Either a measured concurrency test at expected peak, or a written statement of the single-writer constraint bound into the deployment. |
| **MFA / device binding** | Not implemented. Password-only authentication, protected by throttling and lockout. | Engineering / Security | Decision record: accept the residual risk, or implement a second factor. |
| **R-OCR** | No authorised OCR corpus from the operator. Accuracy is evidenced only on synthetic and public samples, and no Algerian supplier-invoice ground truth exists in the repository. | Operator | A corpus supplied under agreement, an accuracy report on it, and the acceptable-threshold sign-off. |

## C. Accounting, tax and legal determinations

| ID | Blocker | Owner | Evidence that closes it |
|---|---|---|---|
| **R-SCF** | Thirteen tax assumptions are implemented **as configured** and have never been reviewed by an Algerian expert-comptable — TVA rates and applicability, exemption mapping, the stamp-duty brackets (1 % / 1.5 % / 2 %), the 300.00 DZD threshold and its inclusive boundary, the 5.00 DZD minimum, cash-only applicability, the duty base, per-line rounding, discount treatment, the G50 box mapping, fiscal-law effective dates, the IFU regime, and workbook retention. Enumerated in `docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md` §8.1. | Expert-comptable | A dated, signed review that names the legal reference (Finance Law article and date) for each assumption, and either confirms or corrects the implementation. |
| **R-LAW** | No Law 18-07 / ANPDP determination. Personal data is processed (usernames, client IPs, supplier identities, invoices); no registration, no DPO appointment, no retention schedule ratified by counsel. | Legal counsel | A written determination covering lawful basis, retention periods, cross-border transfer, and the breach-notification path. |
| **G50 filing** | The G50 output is a preparation workbook with traceability, not a filing. The official teletransmission format and the *précompte* treatment are outside what is implemented. | Expert-comptable / Operator | A filed return produced from this workbook and accepted by the DGI, or a written confirmation that the workbook format is the one the accountant uses. |
| **Statutory sign-off** | No statutory accounts, no audit trail accepted by an auditor, no fiscal-representative attestation. | Expert-comptable / Auditor | The signed attestation. |

## D. Pre-existing environmental limitation (not a product defect)

| ID | Blocker | Owner | Evidence that closes it |
|---|---|---|---|
| **Local interpreter floor** | On CPython 3.11.2 the safe-restore path refuses to extract by design, so 20 backup/restore tests fail locally. Measured identically at the workstream baseline `852f389f` and at the head `8b1b56c9` — same names, same causes. CI on the same head passes them on Linux `3.11`, Linux `3.12` and Windows `3.12.10`. | Environment | Run the restore suites on a supported interpreter (3.11.17+ or 3.12.10+). Already satisfied in CI; recorded so the local failure count is never mistaken for a regression. |

---

## What is *not* on this list

The Priority 0 duplicate-row integrity work, the request correlation and
credential-free logging, the effective-dated Timbre/TVA resolution mechanism, the
migration matrix, the deployment artefact properties, and the documentation set
are **not** blocked: they were verified against the exact head, with tests and
with independent probes. Their classification, and the exact commands that
reproduce it, are in `docs/FINAL_INDEPENDENT_VERIFICATION_REPORT.md`.

## Sequence if the owner authorises progress

1. F-01 and F-04 — small, self-contained, testable (engineering only).
2. R-IMG — needs a container runtime; everything else about the image is already
   asserted by `tests/test_phase37_deployment_assets.py`.
3. R-KMS / R-OFFHOST / R-DR / R-ALERT — the operational quartet; all four need a
   real deployment and a named operator.
4. R-SCF / R-LAW — the external determinations; they gate any claim of fiscal or
   legal correctness, and they are the longest lead time. Start them first even
   though they finish last.
