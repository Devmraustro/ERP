# Staging Security Configuration

**Scope:** isolated, non-production staging deployments of the Algerian AI ERP Platform.
**Status:** verified by running the configuration checks below, not by inspection alone.
**Last verified:** 2026-10-19, branch `arena/5c240e1d-erp`.

This document is operational. Every item states what to configure, why, and what happens if it
is skipped. Where a claim is verified, the evidence is named; where it is not, that is stated.

---

## 0. What this deployment is not

- **Not production.** It holds no real customer, supplier, employee or financial data.
- **Not publicly reachable.** It binds to loopback by default (§4).
- **Not a compliance attestation.** Nothing in this repository or this document constitutes
  statutory Algerian accounting or tax compliance. The Algerian *Système Comptable Financier*
  (SCF) chart, *Droit de Timbre*, G50 preparation and NIF/NIS/AI/RC handling are implemented
  from the specification in [`docs/ALGERIAN_SCF_LOCALIZATION_SPEC.md`](ALGERIAN_SCF_LOCALIZATION_SPEC.md),
  but **compliance requires review by a qualified local accountant or tax adviser**. No claim of
  statutory compliance is made or implied here.

---

## 1. Explicit key provisioning and secure storage

Three independent secrets protect three different things. They are deliberately not shared.

| Secret | Environment variable | Key file (auto-generated) | Protects |
|---|---|---|---|
| Audit chain HMAC key (version 1) | `ERP_AUDIT_HMAC_SECRET` | `<dbname>.audit.key` | The tamper-evidence of `audit_events` |
| Vault encryption key (version 1) | `ERP_VAULT_KEY` | `<vaultname>.vault.key` | Every stored document at rest |
| Audit key registry (all versions) | — (file only) | `<dbname>.audit.keys.json` | Created by the first audit-key rotation |
| Vault key registry (all versions) | — (file only) | `<vaultname>.vault.keys.json` | Created by the first vault-key rotation |
| Backup encryption key | *(no default)* | — | The encrypted backup archive |

### 1.1 Precedence (audit and vault keys)

1. An explicit argument passed by the caller.
2. The environment variable.
3. A key file generated beside the artefact on first use, mode `0600`, created with `O_EXCL`
   so racing initialisers converge on one key.
4. The published source-tree default — **for `:memory:` databases only**.

A file-backed deployment can never reach step 4. The default is printed in this repository, so
falling back to it would let anyone who has read the source forge an arbitrary audit chain,
silently, with no error and no warning.

### 1.2 What the auto-generated key file is for

Generating the key on first use makes the secure path the **default** path rather than a
deployment step someone can forget. It is not a substitute for explicit provisioning in
production — see §1.4.

### 1.3 Verified properties

| Property | Status |
|---|---|
| File-backed DB secret ≠ the published default | verified |
| Key file created with mode `0o600` | verified |
| Secret stable across reopen | verified |
| `:memory:` still uses the documented default (tests rely on it) | verified |
| Explicit argument and environment variable both honoured | verified |
| 8 racing thread initialisations converge on one key (`O_EXCL`) | verified |
| Vault key is **not** written inside the encrypted blob directory | verified |
| A malformed key raises rather than silently minting a new one | verified |

### 1.4 Required for anything beyond staging

Set `ERP_AUDIT_HMAC_SECRET` and `ERP_VAULT_KEY` explicitly, from a secret manager, and:

- **Back both key files up with the same care as the database.** Losing the vault key means
  every stored document is permanently unreadable; losing the audit key means the chain can
  never be verified again. Both are correct fail-closed behaviour and both are permanent.
- **Rotate deliberately, with a verified backup first.** Rotation is now implemented
  (`PrivateDocumentVault.rotate_key` / `ERPDatabase.rotate_audit_key`) and is additive: the new
  key version is activated while every historical version is retained, so no record becomes
  unreadable at any point. Old keys are never deleted automatically - see
  `KEY_ROTATION_DESIGN.md` and `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`. Rotation is still an
  operator action taken on a validated copy during a maintenance window; it is not exposed over
  the API.
- **Never commit a key.** `git ls-files | grep -E '\.(audit|vault)\.key$'` returns nothing, and
  must keep returning nothing.

### 1.5 Missing and corrupt keys fail loudly

Verified in separate processes:

| Scenario | Behaviour |
|---|---|
| Vault key file missing **and** blobs present | `VaultKeyUnavailableError`; **no replacement key is written** |
| Vault key file missing, vault empty | a first key is generated (legitimate first use) |
| Audit key file missing **and** events present | `AuditKeyUnavailableError`; no replacement written |
| Audit key file missing, fresh database | a first key is generated |
| Key file present but not a valid Fernet key | `ValueError` at construction, before any decryption |
| Valid but **wrong** key supplied | `InvalidToken` on decrypt; the chain reports `EVENT_HASH_TAMPERED` |

This matters because the earlier behaviour was worse than an error: a missing vault key was
silently replaced, so the vault kept accepting writes under the new key while every existing
document became permanently unreadable — and nothing downstream noticed, because the writes
kept succeeding.

---

## 2. Durable authentication-failure logging

### 2.1 The gap this closes

`AUTH_REQUEST_REJECTED` used to reach only an in-memory list, so **every rejection record died
with the process**. A credential-stuffing run against a staging box would have left no durable
trace, and the operator would have learned about it only when something else broke.

### 2.2 Why it is not in `audit_events`

`audit_events.company_id` is `NOT NULL` and every read path is tenant-scoped, so one tenant can
never read another's audit trail. A request that failed *authentication* has no trustworthy
tenant to attribute it to — the caller never proved who they are, which is exactly why it was
refused. Writing a guessed or defaulted `company_id` would either blame the wrong tenant or
fabricate an attribution that never existed.

**No tenant attribution is invented.** The security log records no company identifier at all.

### 2.3 Configuration

```bash
ERP_SECURITY_LOG_PATH=/var/log/erp/security.jsonl
```

The file is created with mode `0600` at creation time, never tightened afterwards. Records are
one JSON object per line, written with `O_APPEND` so concurrent workers cannot overwrite one
another.

### 2.4 What each record contains

`timestamp`, `level`, `event` (`AUTH_REQUEST_REJECTED`), `correlation_id`, and a context of
`peer_ip`, `path`, `method`, `code`, `detail`. **No `company_id`, no user id, no credential.**

Credentials are redacted before the record is written: the presented `Authorization` header
never reaches the log (verified by asserting the literal token is absent from the file).

### 2.5 Failure modes — stated explicitly

| Failure | Behaviour | Consequence |
|---|---|---|
| Sink path unwritable / disk full | the error **propagates**; the request fails | Requests fail rather than records being silently dropped. A security log that silently stops being durable is indistinguishable from no security log. |
| Short write detected | `OSError` raised | A truncated record cannot corrupt the log unnoticed. |
| `ERP_SECURITY_LOG_PATH` unset | in-memory logger only | **All rejection records are lost on restart.** This is the test configuration and must never be the staging configuration. |
| Log file grows without bound | no rotation is built in | Rotate with `logrotate` or an equivalent. The log contains no financial data, so retention can be short. |

**Staging requirement:** set `ERP_SECURITY_LOG_PATH`, and confirm it is non-empty after the
first rejected request. A staging box with no durable security log is not exercising the
configuration production will run.

---

## 3. API documentation exposure

The application disables Swagger UI and ReDoc by default through the application builder used by
`dz_erp.serve`. For local development only, `ERP_DOCS_ENABLED=true` can enable them explicitly.
Do not expose API documentation from a staging or production service unless a separate access
control protects it; the application's own bearer-token scheme is not a substitute for an
independent gate around the docs surface.

- **Default / staging / production:** `/docs` and `/redoc` should return 404.
- **Local development:** set `ERP_DOCS_ENABLED=true` only for the period in which the docs are needed.
- `/metrics`, `/debug` and `/admin` are not implemented and should return 404.

---

## 4. Loopback-only binding by default

`ERP_HOST` defaults to `127.0.0.1`. Verified: a server started with the default binds loopback
only, and a connection attempt to the host's non-loopback address is refused.

```bash
ERP_HOST=127.0.0.1 ERP_PORT=8000
```

Only set `ERP_HOST=0.0.0.0` behind a reverse proxy that terminates TLS. If you do, you **must**
also configure trusted proxies (§5) — otherwise the application cannot see the real client IP
and every audit and security record will attribute requests to the proxy.

### 4.1 Forwarded-header trust is fail-closed

`X-Forwarded-For` is ignored unless trusted proxies are explicitly configured. Verified: with
no trusted proxies, a forged `X-Forwarded-For` has no effect; malformed CIDRs are ignored; and
a bare string instead of a list also fails closed (it iterates the string's characters, none of
which parse as a network).

```
ERP_TRUSTED_PROXIES=10.0.0.0/8,127.0.0.1
```

---

## 5. No real customer or financial data

Everything in the test suite, the adversarial harness and the staging acceptance run is
synthetic: invented companies (`SARL Atlas`, `SARL Aurès`), invented users, invented NIF/NIS/AI/
RC values, and documents generated in-memory. No production credential, customer record or
financial figure appears anywhere in the repository.

Before a staging deployment is populated:

- Seed **only** synthetic or properly anonymised data.
- Confirm the vault contains nothing derived from a real invoice image.
- Confirm `ERP_SECURITY_LOG_PATH` does not point at a log that already holds production data.

---

## 6. Backup restoration tested independently of the writer

The restore is verified **in a separate OS process** from the one that created the backup, with
no secrets supplied to it. This is the check that caught the original defect: the restore used
to verify its own work in memory and then leave no key on disk, so the restored deployment could
not decrypt its own documents or verify its own audit chain.

Verified:

| Property | Status |
|---|---|
| Audit key written to the restored directory at `0600` | verified |
| Vault key written to the restored directory at `0600` | verified |
| Chain verifies on reopen with **no** secrets supplied | verified |
| Documents decrypt on reopen with **no** secrets supplied | verified |
| Stale absolute vault paths re-anchor correctly | verified |
| Path traversal still blocked after the fix | verified |
| Cross-company read still denied after the fix | verified |
| Incomplete restore (missing vault blob) rejected | verified |
| Tampered archive rejected | verified |
| Repeated restores are idempotent | verified |

**Operational rule:** never validate a backup only by restoring it in the process that wrote
it. Restore into a scratch directory from a fresh process and confirm both the chain and a
document decrypt.

---

## 7. Human approval remains mandatory before financial posting

**This control is load-bearing and must not be removed.**

Verified end-to-end in the staging acceptance run: after a successful OCR extraction of a
supplier invoice, `supplier_invoices = 0` and `journal_entries = 0`. OCR intake creates **no**
financial posting. Every extracted field must be confirmed by a human before an invoice enters
the approval workflow.

The measured OCR accuracy is **0.9097 overall field exact-match on 18 synthetic documents**,
with `supplier_ai` and `supplier_rc` at 0.667. That sample is far too small to support a
statistical claim, which is precisely why human review stays mandatory. Removing it requires a
powered benchmark (≥100 documents per stratum) first — see
[`AUDIT_READINESS_REPORT.md`](../AUDIT_READINESS_REPORT.md) §3.5.

The workflow itself enforces separation of duties, verified at runtime:

| Control | Result |
|---|---|
| A clerk cannot post an invoice | 403 |
| A creator cannot self-approve | 422 |
| An unapproved invoice cannot be posted | 409 |
| Posting requires an `Idempotency-Key` | 400 without one |
| Replaying the same key is idempotent | no duplicate journal entry |
| A **new** key cannot re-post | 409 |
| A deactivated user loses access immediately | 401 |

---

## 7.1 Supply-chain integrity of the installed software

Staging runs whatever the installation step produced, so the integrity of that step is part of
the security configuration rather than an implementation detail.

* **Install only from the hash lockfile.** Both CI jobs run
  `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt`. A management or
  staging host should do the same; `requirements.lock.txt` alone pins versions, not artifacts.
* **51 distributions, 1 277 SHA-256 digests, sha256 only.** Every authentic file for each pinned
  version is listed, so the same lockfile is valid on Linux and Windows.
* **Drift is detected, not assumed.** `python tools/generate_hash_lock.py --check` runs in CI and
  fails if the hash lockfile and the version lockfile disagree; 19 tests in
  `tests/test_phase19_dependency_integrity.py` assert the same, plus the validator rules and
  the offline-wheelhouse builder/verifier.
* **The documented OpenCV substitution stays visible.** `pip check` reports one line about
  `opencv-python`; `tools/verify_dependency_graph.py` allow-lists exactly that substitution and
  fails on any other.
* **Verified by execution:** a clean install from the hash lockfile succeeded, the suite as it
  stood at that commit passed inside that environment (435 tests; the repository suite has since
  grown to 531), and an install with one altered digest was rejected with exit 1.

**Not in place:** no offline wheelhouse or internal mirror is *provisioned* here — nothing in
this repository ships one. What now exists is the repeatable procedure and its verifier:
`tools/verify_wheelhouse.py` prints the hash-enforced build command
(`pip download --require-hashes --only-binary=:all:`), verifies an existing wheelhouse offline
(hash + name/version per file, exit 1 on any unknown/tampered/mismatched artifact or missing
pin), and documents the air-gapped install command (`--no-index --find-links`).
Publisher provenance (sigstore/PEP 740) is still not verified and remains an accepted residual
risk (B2); see `docs/DEPENDENCY_AND_LICENSE_REGISTER.md` §4.2–4.3.

---

## 8. Staging checklist

Run these before calling a staging box ready:

```bash
# 1. Explicit keys, from a secret manager (not the auto-generated files)
export ERP_AUDIT_HMAC_SECRET=...
export ERP_VAULT_KEY=...

# 2. Durable security logging
export ERP_SECURITY_LOG_PATH=/var/log/erp/security.jsonl

# 3. Loopback only, no trusted proxies unless a proxy fronts it
export ERP_HOST=127.0.0.1 ERP_PORT=8000

# 4. Start
python -m dz_erp.serve --bootstrap-admin <username> --company-name '<company>'
python -m dz_erp.serve
```

Then confirm:

- [ ] `/healthz` returns 200 and the server is **not** reachable off-loopback.
- [ ] A request with no token returns 401, and a line appears in `$ERP_SECURITY_LOG_PATH`.
- [ ] On POSIX, the vault and audit key files are mode `0600` and their directory is `0700`. On Windows, set and verify an explicit NTFS ACL limited to the service account; `chmod` mode bits are not an access-control boundary.
- [ ] `/docs` and `/redoc` return 404 by default. For local development only, set `ERP_DOCS_ENABLED=true` when explicitly needed; do not expose the docs on a staging or production service without separate authentication.
- [ ] An encrypted backup restores into a scratch directory **from a separate process**, and a
      document decrypts and the chain verifies.
- [ ] No real customer or financial data is present.
- [ ] A human still has to approve before anything posts.
- [ ] Dependencies were installed from `requirements.lock.hashes.txt` with `--require-hashes`, and
      `python tools/generate_hash_lock.py --check` passes.
- [ ] `python tools/verify_dependency_graph.py` passes, and its only reported deviation is the
      documented OpenCV substitution.

---

## 9. Known gaps

| Gap | Severity | Remediation |
|---|---|---|
| Key rotation requires operator filesystem access; no scheduled/automatic rotation | Medium for production | Run the documented rotation on a schedule with a verified backup; see the recovery guide. |
| Key material is file-based, not HSM-backed | Medium for production | POSIX mode `0600` is enforced; Windows mode bits are not access control and the service-account ACL must be set and verified separately. No KMS/HSM is implemented. |
| `source_documents.vault_path` stores an absolute path | Low | Mitigated by re-anchoring on read; a relative path would be cleaner. |
| OCR accuracy not statistically established | **High for unattended posting** | Keep human review mandatory; build a powered benchmark (`docs/OCR_EVALUATION_PROTOCOL.md`). |
| No publisher provenance check (hash pinning pins bytes, not who published them) | Low–Medium | Accept for staging; add sigstore/PEP 740 verification before production if required. |
| No internal mirror / offline wheelhouse | Medium for air-gapped deployment | Mirror the 51 pinned artifacts and keep `--require-hashes` on. Prerequisite, not implemented. |

None of these block isolated non-production staging. The key-rotation scheduling, key custody,
OCR accuracy and (for an air-gapped target) the mirror items block production; the rest are
accepted residuals with the mitigation named.
