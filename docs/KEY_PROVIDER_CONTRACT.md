# Key provider contract

This document is the contract between the application and *any* source of key
material. It exists so that (a) an auditor can check what the deployment actually
has without reading source, and (b) a future KMS/HSM integration has a written
acceptance specification instead of a vague aspiration.

**What this build does not have:** no KMS, no HSM, no hardware-backed custody of
any kind. Key material is file-based (or environment-injected). The residual
risks of that choice are the subject of `docs/KEY_CUSTODY_THREAT_MODEL.md`
(R1–R10) and are *not* re-argued here.

Related evidence:

* implementation: `dz_erp/key_provider.py`
* provider tests: `tests/test_phase17_key_providers.py`
* consistency test (this document vs. the code):
  `tests/test_phase22_register_and_docs_consistency.py`
* operational rotation/monitoring: `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`,
  `docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md`

## 1. The provider protocol

Every key source implements the `KeyProvider` protocol — deliberately narrow:

| method | obligation |
|---|---|
| `read() -> bytes` | Return the *current* key material, or raise (see §2). Never generate a replacement key as a side effect of being asked for one. |
| `describe() -> dict` | Report the provider's identity and custody posture. Must contain **no key material** — only locations, flags and metadata an auditor can check. |
| `reload()` | Forget any cached material so the next `read()` observes a rotation performed out-of-process. |

A provider does not enumerate key versions (that is the key registry's job) and
does not decide which version is active.

## 2. Failure modes — raise, never fall back

All key errors are subclasses of `KeyConfigurationError`. The distinction is
load-bearing for operators:

| exception | raised when | operator action |
|---|---|---|
| `KeyMaterialUnavailable` | the key file is missing or empty; the environment variable is unset/empty; the file cannot be opened | restore the key from backup, or set the environment variable. **Do not** generate a new key: existing ciphertext would become unreadable. |
| `KeyConfigurationError` (direct) | the key path is a symlink / Windows reparse point; the encoded material is malformed (bad base64/hex) | fix the path or re-encode the material; the refusal is deliberate anti-tamper behaviour |
| `KeyCustodyError` | a POSIX key file's mode grants access beyond the owner (`chmod 600`) | restrict the mode; on Windows see §6 |

The invariant tested in phase 17: a missing key must be *distinguishable* from a
weak key, and **no code path falls back to minting a replacement key** when
material is absent. The one exception is deliberate: a genuinely empty vault
(first use) may create its first key — `dz_erp/intake_ocr.py` gates this
explicitly, and read-only tools construct their providers with
`create=False`-style guards (`dz_erp/rotate_keys.py`) so verification never
creates anything.

## 3. Provider descriptions (the auditable surface)

`describe()` returns a dict; these are the keys each implementation guarantees.
A monitoring or attestation script may rely on exactly these.

### 3.1 `FileKeyProvider` — key material in an owner-only file

| key | value | meaning |
|---|---|---|
| `provider` | `"file"` | |
| `path` | path string | where the material lives |
| `exists` | bool | |
| `is_symlink` | bool | `True` means *do not trust this deployment*; reads refuse symlinks anyway |
| `backupable` | `True` | a file can be included in a backup with the same custody care |
| `hsm_equivalent` | `False` | **always.** A file is not hardware-backed custody. |
| `custody_checked` | bool | whether open-time mode checks are enabled |
| `key_material_exportable` | `True` | the bytes can be copied by anyone with filesystem access |
| `mode`, `size_bytes` | | POSIX: enforced on the opened descriptor. |
| `owner_uid`, `owner_gid`, `mode_enforced` | | POSIX only (`mode_enforced: True`). |
| `note` | | Windows only: mode bits are not access control on this platform. |

Read path hardening: POSIX opens with `O_NOFOLLOW` and verifies the mode of the
*opened descriptor* (closing the check-then-read TOCTOU gap); Windows opens with
`FILE_FLAG_OPEN_REPARSE_POINT`, inspects `FILE_ATTRIBUTE_TAG_INFO` on that same
handle, and refuses every reparse point.

### 3.2 `EnvironmentKeyProvider` — orchestrator-injected secret

| key | value | meaning |
|---|---|---|
| `provider` | `"environment"` | |
| `variable` | variable name | e.g. `ERP_VAULT_KEY` |
| `encoding` | `"base64"` / `"hex"` | material encoding |
| `exists` | bool | variable set and non-empty |
| `is_symlink` | `False` | concept does not apply |
| `backupable` | `False` | the process cannot back up its own environment |
| `hsm_equivalent` | `False` | **always** |
| `custody_checked` | `False` | the provider cannot verify who can read the variable |
| `key_material_exportable` | `True` | visible via `/proc/<pid>/environ`, inherited by child processes, present in crash dumps |
| `note` | | states that this is *not* stronger than an owner-only file |

`write()` always raises: key material is persisted only through the
orchestrator's secret store, never by this process.

### 3.3 `KmsKeyProvider` — the production target, **not implemented**

| key | value | meaning |
|---|---|---|
| `provider` | `"kms"` | |
| `key_id` | identifier | |
| `implemented` | `False` | **no KMS/HSM client is bundled** |
| `backupable` | `False` | |
| `hsm_equivalent` | `False` | **always, until a real integration exists** |
| `custody_checked` | `False` | |
| `key_material_exportable` | `False` | would be true of a *correct* KMS integration |
| `note` | | states that this provider cannot supply key material |

`read()` raises `NotImplementedError` naming the honest reason. A stub that
returned a locally generated key while labelled `"kms"` would be worse than the
absence, so the absence is explicit and machine-readable.

## 4. Acceptance contract for a production KMS/HSM integration

An implementation may only claim hardware custody (`implemented: True`) when
**all** of the following hold; until then `describe()` must keep reporting
`hsm_equivalent: False`:

1. Normal operations perform wrap/unwrap (or encrypt/decrypt) *inside* the
   service boundary; plaintext key material is not downloadable by the
   application.
2. Every stored object remains decryptable after rotation: a per-version key
   reference stays resolvable by the boundary (the same requirement the local
   registry satisfies with versioned key files).
3. Rotation is authorised by the boundary (policy-bound or two-person) and
   logged *by the boundary*, not only by this application's ledger.
4. Deletion is impossible without an explicit, audited, delayed process.
5. The provider's own availability and failure modes are covered by the
   deployment's recovery runbook (a KMS outage is a new failure mode, not an
   upgrade).

Acceptance is demonstrated by replacing `tests/test_phase17_key_providers.py`'s
KMS assertions with integration tests against the real boundary — not by
editing this document.

## 5. Rotation behaviour and the registry

* The key **registry is authoritative** for which version is active; providers
  supply material for a version, they do not decide activation.
* `reload()` exists so an out-of-process rotation (per
  `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`) is observed on the next read
  without restarting the service.
* Verification tooling is read-only by construction: the checkpoint/rotation
  monitors open the ledger with `create=False` and never mint replacement keys
  (tested in phase 20).
* Rotation cannot re-key what it does not control (R4): rows encrypted under
  retired versions stay under their version references; that is why versioned
  material must be retained or migrated per the recovery guide.

## 6. Platform limitation (Windows) — risk S5 / R8

On POSIX, mode bits are enforced by the kernel and checked on the opened file
descriptor. On Windows, mode bits are **not access control**: custody depends on
the NTFS ACLs of the key directory. The code cannot verify ACLs portably, so
`describe()` reports `mode_enforced: False` plus an explanatory `note`, and the
deployment must restrict the directory ACL itself. This is recorded as
deployment requirement S5 in `docs/STAGING_SECURITY_CONFIGURATION.md` and risk
R8 in `docs/KEY_CUSTODY_THREAT_MODEL.md`.

## 7. Configuration surface

| variable | consumed by | precedence |
|---|---|---|
| `ERP_VAULT_KEY` | `dz_erp/intake_ocr.py` (`VAULT_KEY_ENV`) | explicit argument, then `ERP_VAULT_KEY`, then a key file beside the vault |
| `ERP_AUDIT_HMAC_SECRET` | `dz_erp/database.py` (`AUDIT_KEY_ENV`) | key ring's active version first; the environment value must *agree* with it or startup refuses (`docs/` phase-8 evidence) |
| `ERP_KEY_PROVIDER` | `dz_erp/key_provider.py` (`PROVIDER_ENV`), enforced at start-up by `dz_erp/serve.build_app()` | declares the custody the deployment claims; unset means the file-based default, *declared nowhere* |

`tools/staging_acceptance.py` unsets all three variables at startup so acceptance
always exercises the file-based path, never an inherited shell environment.

### 7.1 Fail-closed provider selection

`ERP_KEY_PROVIDER` is a *claim*, and a claim this build cannot honour must stop
start-up rather than be silently downgraded. `dz_erp.key_provider.
configured_provider()` resolves it as follows:

| declared value | behaviour |
|---|---|
| unset / empty | `None` — the honest default: file-based custody, declared nowhere |
| `file` | accepted: file-backed custody, exactly what the build provides |
| `environment` | accepted: orchestrator-injected material (§3.2 caveats still apply) |
| `kms`, `hsm`, or anything else | **`KeyConfigurationError` at start-up.** No KMS/HSM client is bundled (§4), so honouring the declaration is impossible and *silently serving file-backed material under a hardware-custody label* is the failure this gate exists to prevent. |

This is the fail-closed rule in one sentence: **a deployment either runs with
custody it actually has, or it does not run.** Removing the variable restores
the (honest) file-based default; integrating a real provider per §4 is the only
way to make a `kms`/`hsm` declaration startable.

### 7.2 Provider outage behaviour

What each provider's failure looks like, and what the operator must do. The
invariant across all of them: **the service refuses to proceed rather than
falling back** — a fallback would either forge audit chains (audit key) or
silently re-key documents (vault key).

| provider | outage | observable behaviour | operator action |
|---|---|---|---|
| `file` (vault or audit key) | file missing, empty, unreadable, or a symlink/reparse point | `KeyMaterialUnavailable` or `KeyConfigurationError` on the next `read()`; start-up or the affected operation fails with the path named | restore the exact key material from the offline custody backup (`docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`); **do not generate a replacement** — existing ciphertext/audit chains would become unreadable or read as tampered |
| `file` (POSIX mode too open) | mode grants access beyond owner | `KeyCustodyError` naming the path and the `chmod 600` fix | restrict the mode, re-verify with the readiness checker |
| `environment` | variable unset/empty, or malformed base64/hex | `KeyMaterialUnavailable` / `KeyConfigurationError` naming the variable | restore the secret in the orchestrator's store and restart; no local fallback exists by design |
| `ERP_KEY_PROVIDER` misdeclaration | `kms`/`hsm`/unknown while no such provider exists | start-up refuses (`KeyConfigurationError`) before any database or vault is opened | remove the declaration or complete the §4 integration; this is a configuration error, not a runtime outage |
| KMS/HSM (future, §4) | provider unreachable, throttled, or the key is disabled | *not defined by this build* — a real integration must define them and add them to the recovery runbook (§4 item 5) before claiming production readiness | undefined here by construction; no outage behaviour is claimed for a provider that does not exist |

Monitoring hooks for the file/environment outages above are already wired:
`python -m dz_erp.rotate_keys status` reports key availability, and the
checkpoint monitor (exit code 3) reports ledger health
(`docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md`).

## 8. What is explicitly *not* claimed

* No production KMS/HSM integration exists; custody is filesystem-based.
* No split custody / two-person rule is enforced by this build (R3).
* No automatic rotation and no key-age enforcement (R10) — rotation is operator
  driven via `dz_erp/rotate_keys.py` and monitored via the checkpoint runbook.
* Memory custody is impossible in any Python process: the running process holds
  the active key by necessity (R2).

Production deployment therefore requires an accepted custody decision by the
responsible owner (expert-comptable / DPO), including either an approved
acceptance of file-based custody with compensating host controls, or the KMS/HSM
integration of §4.
