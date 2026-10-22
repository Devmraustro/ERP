# Key Rotation — Design and Implementation

**Status:** IMPLEMENTED AND TESTED (vault and audit rotation are independent changes)
**Branch:** `arena/6c474c64-erp`
**Supersedes:** the previous *design-phase* version of this document, which described a plan that
had not been implemented. Every statement below describes code in this repository.

---

## 0. Scope and separation

There are two protected stores with two different cryptographic properties, and they are rotated
by two different mechanisms:

| | Vault encryption keys | Audit HMAC keys |
|---|---|---|
| Protects | Document plaintext at rest | Tamper-evidence of `audit_events` |
| Primitive | Fernet (AES-128-CBC + HMAC-SHA256), one key per blob | HMAC-SHA256 chain over event fields |
| Rotation means | **Re-encrypt** every blob under a new key version | **Sign new events** under a new version; never rewrite old ones |
| Data rewrite | Yes, blob by blob, atomically, resumably | **No. Rewriting an audit event is prohibited.** |
| Implementation | `dz_erp/key_rotation.py`, `PrivateDocumentVault` | `dz_erp/database.py`, `ERPDatabase` |
| Tests | `tests/test_phase10_vault_key_rotation.py` | `tests/test_phase11_audit_key_rotation.py` |
| Tests (shared) | `tests/test_phase12_key_migration_and_backup.py`, `tests/test_phase13_key_rotation_regression.py` | same |

Combining them into one operation was deliberately rejected: a failure in a bulk blob rewrite
must not be able to leave the audit chain in an intermediate state, and the audit change needs no
bulk rewrite at all.

---

## 1. Shared foundation: the key registry

`dz_erp/key_rotation.py` defines `KeyRing`, a validated mapping from integer version to key
material, and the blob header codec. Nothing in this module imports the vault or the database.

### 1.1 Invariants (enforced on construction and on every mutation)

* Exactly one entry is `active`, and it is `current_version`. A registry where the active version
  is ambiguous is rejected rather than resolved by precedence.
* Versions are unique integers in `1..255` (the version travels in one header byte).
* **No two versions may hold identical key material.** Reusing material under a second version
  would silently remove the domain separation that makes per-version verification meaningful.
* Every version's material is non-empty. Vault material must additionally parse as a Fernet key,
  which is checked eagerly for *every* retained version at start-up so a corrupted historical key
  is a start-up failure rather than a surprise during an incident.

### 1.2 On-disk format

```json
{
  "format": "dz-erp-keyring",
  "schema": 1,
  "kind": "vault",
  "current_version": 2,
  "keys": {
    "1": {"material_b64": "…", "created_at": "2026-10-22T…", "status": "retired"},
    "2": {"material_b64": "…", "created_at": "2026-10-22T…", "status": "active"}
  }
}
```

* `format` + `schema` are validated on load, so a future format cannot be silently misread.
* `kind` is validated (`vault` vs `audit`), so an audit registry copied into a vault path is
  rejected instead of being interpreted as vault keys.
* Statuses: `active`, `retired`, `compromised`.

### 1.3 Persistence rules

* Written with mode `0600` to a temporary file in the same directory, `fsync`ed, then
  `os.replace`d over the target, followed by a directory `fsync` on POSIX. A crash can never
  leave a truncated registry.
* Registries live **beside** the store they protect, never inside the vault blob directory.
* The registry is written **before** the process switches keys, so no data can ever be written
  under a key the persisted registry does not name.

---

## 2. Vault encryption-key rotation

### 2.1 Ciphertext format

New blobs are written as:

```
"DZKV" | version (1 byte) | Fernet token
```

Blobs written before this change are a bare Fernet token and are **defined** to be key
version 1.

**Why the discriminator is unambiguous, not merely unlikely:** a Fernet token is base64url text
whose first encoded byte is always `g` (`0x67`, the encoding of the token-version byte `0x80`),
while the magic begins with `D` (`0x44`). A legacy token can therefore never start with `DZKV`.
Detection needs no database column, no migration, no heuristic and no trial decryption.

A marked blob whose version byte is `0` or missing is **corrupt**, not legacy, and raises
`BlobFormatError` instead of being guessed at.

### 2.2 Key sources and precedence

| Source | Meaning |
|---|---|
| `keyring` argument | Explicit registry (used by restore) |
| `<vault>.vault.keys.json` | Authoritative once it exists |
| `ERP_VAULT_KEY` | Bootstrap key for an un-rotated vault |
| `<vault>.vault.key` (mode `0600`) | Bootstrap key, generated on first use |
| explicit `encryption_key` argument | Bootstrap key, highest precedence while no registry exists |

Once a registry exists, a supplied key or environment variable that **contradicts** its active
version is a `KeyConfigurationError`. It is not resolved by precedence: writing new documents
under a key the rest of the deployment cannot read is worse than refusing to start.

### 2.3 Rotation workflow (`PrivateDocumentVault.rotate_key`)

1. Validate the new key material (Fernet parse) before anything changes.
2. Take the exclusive rotation lock (`<vault>.vault.rotation.lock`): non-blocking, so a second
   concurrent run fails immediately instead of racing.
3. Scan every blob header; if any blob references a version the registry lacks, abort **before
   mutating anything**.
4. Build the new registry in memory, preserving all existing versions; persist it atomically; only
   then adopt it in process.
5. For each blob, in sorted order:
   * capture `(st_ino, st_mtime_ns, st_size)`, read, split the header;
   * skip if already at the target version;
   * decrypt with the blob's own version, re-encrypt with the target version;
   * **decrypt the new blob again and compare with the original plaintext** before it may replace
     the old one;
   * write a temporary file, re-stat the original, and `os.replace` only if it is unchanged;
   * on contention, re-read and retry (bounded); report as skipped if it keeps changing.
6. Verify every blob decrypts after the sweep.
7. Journal each decision; return a reconciliation report.

### 2.4 Interruption and resumption

The registry is persisted before the first blob is touched, so an interruption at any point leaves
**both** keys available and **every** blob readable. Re-running `rotate_key(<same new key>)`
detects that the material is already registered, treats the operation as a resume, and does not
create a third version. Blobs already at the target version are counted as
`skipped_already_current`, never as `completed`.

A journal line is written for `rotation_started`, each `blob_reencrypted` / `blob_failed` /
`blob_contended`, and `rotation_finished`. An interrupted run therefore has no
`rotation_finished` record - the absence is the signal.

### 2.5 Concurrency and its limits

The store is content-addressed (`<sha256(plaintext)>.enc`), so an ordinary new document creates a
new path and never collides with a rotation. For a path that *is* being rewritten concurrently,
rotation uses an optimistic check: identity and timestamps are captured before the read and
re-verified before the atomic replace. A changed file is never overwritten; it is re-read and the
new content is rotated instead, so the concurrent writer's bytes survive (proved by a forced-race
test).

**Limitation, stated plainly:** this is optimistic concurrency, not a distributed lock. There is a
narrow window between the final `stat` and `os.replace`. Closing it fully would require holding an
advisory lock on every blob for the whole batch, which is not implemented. The rotation lock stops
two *rotations* from overlapping; it does not stop an unrelated external writer that ignores the
protocol. Deployment guidance: run rotation in a maintenance window.

### 2.6 Key lifecycle

* `set_active_key_version(v)` — documented rollback. Writes nothing new to old blobs, deletes
  nothing; documents written under the newer version stay readable.
* `retire_key(v)` — marks a version `retired`: still usable to decrypt, never chosen for new
  writes. The active version cannot be retired.
* `remove_key(v, confirm_retained_copies=…)` — refused while the **active** version, refused while
  any blob header still references it, and refused without an explicit acknowledgement that
  backups/replicas/exports may still contain ciphertext encrypted with it. Removing a key is the
  only irreversible operation in this module, so it is never implicit.

Completion of a re-encryption is **not** evidence that a key is safe to destroy. That is why the
confirmation flag exists and why it is required even when no live blob uses the version.

---

## 3. Audit HMAC-key rotation

### 3.1 Why the design differs

Each `audit_events` row stores `prev_hash` and `event_hash`, where

```
event_hash = HMAC-SHA256(key, company_id|occurred_at|actor|action|model|target|before|after|prev_hash)
```

Re-signing an old event would mean rewriting audit history - precisely what the append-only
triggers and the chain exist to prevent. Rotation is therefore **additive**: it changes which key
signs *new* events and retains the keys that signed *old* ones.

### 3.2 Schema

```sql
ALTER TABLE audit_events ADD COLUMN hmac_key_version INTEGER NOT NULL DEFAULT 1;
```

Applied idempotently by `_migrate_schema` on every start-up (`PRAGMA table_info` guard), so both a
fresh database and a representative legacy database reach the same schema. `DEFAULT 1` is the
factual backfill: rows that predate versioning were signed with the only key there was.

**The MAC input is unchanged.** The version is a property of the row, not of the signed payload.
That is what allows every historical hash to verify without being recomputed - and it is why the
tests assert that the stored hashes are byte-identical across a rotation.

`audit_events` remains append-only: the immutability triggers block any `UPDATE`, including one
that would rewrite `hmac_key_version`.

### 3.3 Verification (`verify_audit_chain`)

For each row, in id order:

1. Structural check: `prev_hash` must equal the previous row's `event_hash` (or 64 zeros for the
   first). This runs **before** key selection, so a deleted, reordered or spliced event is
   detectable even when a key is missing.
2. Key selection: the key for `hmac_key_version`.
3. Recompute and compare with `hmac.compare_digest`.

Outcomes are deliberately distinguishable:

| Result | Meaning |
|---|---|
| `valid: true` | Every event verified; `key_versions` reports the mix |
| `reason: EVENT_HASH_TAMPERED` | A key was available and the MAC did not match |
| `reason: PREV_HASH_MISMATCH` | The chain is broken (deleted / reordered / inserted event) |
| `reason: AUDIT_KEY_UNAVAILABLE` + `missing_key_versions` | Cannot judge; **never** reported as valid |

A missing key can therefore neither validate a chain nor frame an operator for tampering.
Start-up (`validate_audit_key_configuration`) additionally refuses to run when any stored event
needs an absent version.

Verification is per company: tampering in one tenant's chain does not affect another's verdict,
and `verify_all_audit_chains()` reports each independently.

### 3.4 Rotation workflow (`ERPDatabase.rotate_audit_key`)

1. Refuse in-memory databases (no durable registry).
2. Validate the new material (≥ 32 bytes) and that it is not already registered.
3. Acquire the exclusive rotation lock.
4. **Refuse to rotate if the current chain does not already verify** — a key change on top of a
   broken chain would bury the original evidence. Tamper → `AuditTamperError`; missing key →
   `AuditKeyUnavailableError`.
5. Build and persist the new registry atomically, then adopt it in memory.
6. Re-verify the whole chain. Both verdicts are reported.
7. Journal `previous_version`, `target_version`, `resumed` and the post-rotation verdict.

Because the only state change is one atomic file replace, there is no partial state: either the
rotation happened (new events use the new key, old events keep theirs) or nothing changed. A
simulated write failure is tested to leave the active version, the events and the chain untouched.

### 3.5 Rollback

`set_active_audit_key_version(v)` reactivates an existing version for new events. History is
untouched; the newer version stays retained so events signed during its active period still
verify. A version marked `compromised` cannot be reactivated without an explicit override.

### 3.6 Deleting a version

`remove_audit_key_version(v, confirm_retained_copies=…)` is refused while the **active** version or
while any event references it (`KeyVersionInUseError`), and requires the same explicit
acknowledgement of retained backups.

---

## 4. Backup and restore

`create_encrypted_backup` now carries, **inside the Fernet-encrypted archive**:

* `vault_keyring_b64` / `audit_keyring_b64` — every version, so a restore can read ciphertext
  written before *and* after a rotation;
* `vault_key_versions` / `audit_key_versions` — a **material-free** summary (version numbers,
  statuses, active version) that is also returned to the caller, so an operator can tell from the
  backup alone which keys a restore will require;
* the legacy single-key fields, kept for compatibility with archives written before registries
  existed.

No key material is exposed in the returned metadata or in any log; the registries exist only
inside the archive's own encryption.

`restore_and_verify_backup` rebuilds both registries, writes them (mode `0600`) beside the restored
artefacts, and writes the legacy `.vault.key` / `.audit.key` files with **version-1** material so
that the documented fallback keeps meaning "the historical key" rather than whichever key happens
to be active. It validates environment configuration *before* writing anything, distinguishes a
missing key (`VaultKeyUnavailableError` / `AuditKeyUnavailableError`) from corruption
(`FinancialIntegrityError` / `InvalidToken`), and does not leave a usable-looking partial restore.

---

## 5. Threat model

### 5.1 What this implementation protects

* **Key loss without data loss.** A missing key version is detected at start-up and on read; the
  service fails closed instead of substituting a key. No code path generates a replacement key
  when data already depends on the missing one.
* **Silent substitution.** A contradicting explicit key, environment variable or stale legacy key
  file is a configuration error.
* **Partial or interrupted rotation.** Everything stays readable; resumption is deterministic.
* **Accidental destruction.** The active key and any key still referenced by data cannot be
  removed; removal of a retired key requires an explicit acknowledgement of retained copies.
* **Secret leakage into artefacts.** Registries and journals are `0600`; journals contain only
  versions, statuses, timestamps and content hashes; tests assert key material is absent from
  logs, journals and reports.
* **Tamper-evidence across rotation.** Historical events verify with their own key; missing keys
  are never conflated with tampering.

### 5.2 What it does not protect against, and the limits of this deployment

* **An attacker with the service account's file access.** Keys are files with `0600`. There is no
  HSM, no KMS, no split custody and no two-person rule. Anyone who can read the key files can
  decrypt the vault and forge a chain. Volume encryption and OS-level separation remain
  deployment controls.
* **A compromised running process.** It holds the active key material in memory by necessity.
* **Offline copies.** Rotation cannot re-key backups, replicas, exports or snapshots that are not
  under its control. Those copies need the old key, which is why deletion requires explicit
  confirmation.
* **The optimistic-concurrency window** described in §2.5.
* **Rotation is not itself an event in the audit chain.** It is recorded in the rotation journal
  and the returned report. `audit_events.company_id` is `NOT NULL` and every read is tenant-scoped,
  while key rotation is a deployment-global operation with no trustworthy tenant to attribute it
  to; attributing it to an arbitrary tenant would fabricate association. Wiring rotation into a
  global, non-tenant chain is an outstanding follow-up (§7).

---

## 6. Operational procedure

Summarised here; the step-by-step runbook, including verification queries and failure handling, is
in `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`.

```
1. Take a verified backup and confirm it restores into an isolated directory.
2. Confirm every key version named by the backup is available off-host.
3. (Optional, recommended) Rotation with --dry-run equivalent: scan_blob_versions()/verify_blobs().
4. Vault:   vault.rotate_key()        -> report.verified_ok must be true
   Audit:   db.rotate_audit_key()     -> report.chain_valid_after must be true
5. Re-run application tests / smoke checks.
6. Keep the previous version `retired`. Do not remove it until every backup that needs it has
   expired - and then only with confirm_retained_copies=True.
```

---

## 7. Known limitations / follow-ups (not implemented)

| Item | Impact | Note |
|---|---|---|
| No HSM/KMS-backed key custody | High for production | Requires infrastructure and budget; not a code change. |
| Rotation is operator-invoked, not scheduled | Medium | Run on a documented schedule; there is no automatic rotation daemon. |
| Rotation is not recorded in the tenant audit chain | Medium | Needs a global, non-tenant audit stream (see §5.2). |
| Optimistic concurrency only | Low | Documented; maintenance window recommended. |
| `rapidocr-onnxruntime` successor (`rapidocr` 3.x) already uses the headless OpenCV build | Low (upstream alignment) | A major-version migration; see the dependency evidence in `FINAL_AUDIT_REPORT.md`. |
| Vault rotation has no resume checkpoint for very large vaults | Low | Resumption re-scans; already-current blobs are skipped, so cost is proportional to the remaining work plus one header scan. |

---

*Implemented and verified on branch `arena/6c474c64-erp`. Test evidence is recorded in
`FINAL_AUDIT_REPORT.md`.*
