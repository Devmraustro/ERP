# Key custody: threat model and residual risk

**Scope.** How key material is stored, what an attacker needs to obtain it, what the code
enforces, and what it does not. This document exists because the answer to "is the key
protected?" is *partially*, and a partial control that is described as complete is worse
than one that is described honestly.

**Status of this deployment.** File-based keys (`0600` in a `0700` directory) with enforced
custody checks. **No HSM. No KMS. No split custody. No two-person rule.** Those are not
planned code changes; they are infrastructure decisions with a budget attached
(§7).

**Related.** `KEY_ROTATION_DESIGN.md` (mechanism), `docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`
(runbook), `dz_erp/key_provider.py` (the code this document describes),
`dz_erp/rotate_keys.py monitor` (the control's own telemetry).

---

## 1. What is being protected, and from whom

### 1.1 Assets

| Asset | Where it lives | What an attacker gains |
|---|---|---|
| Vault encryption key (all versions) | `vault.vault.keys.json`, mode `0600` | Every stored document, in plaintext, for every company |
| Audit HMAC key (all versions) | `erp.sqlite3.audit.keys.json`, `0600` | Ability to forge audit records that verify as authentic |
| Rotation ledger key | `vault.rotation-ledger.rotation.key`, `0600` | Ability to rewrite rotation history undetectably |
| Backup archive key | Held by the operator, **not** stored by the application | Every record inside every backup |
| Key metadata (versions, ages, paths) | Registries, `monitor` output | Rotation state, key age, operational weaknesses |

The first three are the same class of secret: whoever holds them can read the data and
forge its history. There is no separation between "can read documents" and
"can rewrite the audit trail" for an attacker who has both.

### 1.2 Adversaries considered

| # | Adversary | Realistic here? | Primary control |
|---|---|---|---|
| A1 | Another local account on the same host | **Yes** — shared staging hosts, misconfigured containers | `0600`/`0700` enforced on read (`FileKeyProvider`) |
| A2 | A process running as the service account (RCE, SSRF, dependency compromise) | **Yes** | None beyond OS isolation: it can read the keys |
| A3 | Root / hypervisor / cloud operator | Yes | None. Stated, not mitigated |
| A4 | Someone with a copy of a backup, or of the key files | **Yes** — the most likely real incident | Backup encryption + key held outside the archive (§3.3) |
| A5 | A user with legitimate application access but not host access | Yes | Application RBAC + tenant isolation (separate control) |
| A6 | Theft or decommissioning of a disk or VM snapshot | Yes | Volume encryption (deployment control, not this code) |
| A7 | Malicious or careless operator performing a rotation | **Yes** — insider risk is the least discussed | Ledger + journal + no-delete-without-confirmation |
| A8 | Supply-chain compromise of a dependency | Yes | Locked dependency graph, licence/verify tooling; no code mitigation for keys |
| A9 | Network attacker | No direct path | Keys never cross the network (a file-only design removes a whole class of exposure) |

A9 is the one place where the file-based design is *better* than a naive remote-secret
design: there is no key-transport channel to intercept.

---

## 2. Attack paths and what stops them

Each row is executable or verifiable in this repository.

| Path | Control | Evidence |
|---|---|---|
| Read a key file as another local account | `0600`; the mode of the **open descriptor** is checked, not the path | `FileKeyProvider.read`, `tests/test_phase17_key_providers.py::test_mode_is_checked_on_the_descriptor_not_the_path` |
| Replace a key file with a symlink/reparse point | POSIX uses `O_NOFOLLOW`; Windows opens the reparse point with `FILE_FLAG_OPEN_REPARSE_POINT` and rejects `FILE_ATTRIBUTE_REPARSE_POINT` on the opened handle | `test_refuses_to_follow_a_symlinked_key`, `test_windows_provider_rejects_reparse_handle_without_symlink_privilege`, optional real-link integration test |
| Loosen a key's mode and keep using it | Start-up refuses; the service does not start with a readable-by-others key | `test_vault_refuses_to_open_with_a_world_readable_key_file`, `test_audit_store_refuses_a_loose_key_file` |
| Copy a key file out and attack it offline | **Nothing.** Detected only by file-integrity monitoring (deployment) | Residual risk R1 |
| Read the key from the running process | **Nothing.** The active key is in memory by necessity | Residual risk R2 |
| Forge an audit record | HMAC-SHA256 per event, chained | `verify_all_audit_chains`; tamper tests |
| Rewrite rotation history | Ledger MAC chain + off-host checkpoints | `RotationLedger.verify`, `verify_against_checkpoint` |
| Delete a key to hide a compromise | Removal requires explicit confirmation and records a refusal otherwise | `vault-remove` / `audit-remove` refusal tests |
| Redirect a restore into an existing symlink, Windows junction/reparse point, or hard-linked file | Preflight uses `lstat`; reparse points are refused, non-regular targets are refused, and regular targets with `st_nlink > 1` cannot be overwritten | `test_restore_refuses_preexisting_hardlink_target_before_writing`, `test_restore_refuses_existing_windows_junction_prefix` |
| Retire a key that documents still need | Rotation converges and reports `stray_versions`; `COMPLETED_VERIFIED` requires convergence | `tests/test_phase16_rotation_concurrency_convergence.py` |
| Rotate a key while the audit chain is already broken | Refused, and the refusal itself is recorded in the ledger | Phase 14 refusal test |
| Lose a key version | Start-up and read paths raise `VaultKeyUnavailableError` / `AuditKeyUnavailableError`; never silently regenerates | Phase 10/11/12 tests |

### 2.1 What "custody enforced" means precisely

`FileKeyProvider.read()`:

1. **POSIX:** opens with `O_NOFOLLOW`; a symlink raises rather than being followed.
2. **Windows:** opens with `CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`, checks
   `FILE_ATTRIBUTE_REPARSE_POINT` on the same handle, and refuses it before converting
   it to a Python file descriptor.
3. **POSIX:** `fstat`s the descriptor and rejects any group/other permission bit. Windows ACLs
   are not enforced by mode bits and must be configured separately as described in R8.
4. Reads from the same descriptor it validated and caches it for the process lifetime, with
   `reload()` for a rotation on disk.

Steps 2–3 remove the stat-then-open window where a file could be swapped between the check
and the read. This is a real hardening, and it is also bounded: it says nothing about an
attacker who can already read the file *as the owner* (A2, A3).

---

## 3. Residual risks

Severity is for **production with real Algerian financial data**, not for a staging fixture.

### R1 — Key material is exportable (High)

Anyone who can read a key file has the whole key, forever, and can copy it silently. A
file-based key cannot be made non-exportable; only a boundary that performs the
cryptography (KMS/HSM) can. Offline attack on a copied key is limited only by the key's
entropy (Fernet = 128-bit effective), i.e. it is not a realistic attack — the threat is
*possession*, not cryptanalysis.

*Compensating control:* file-integrity monitoring (AIDE/auditd/`inotify`) on the key
directory, alerting on read by unexpected UIDs; host-level access control; the `monitor`
report surfaces mode/owner drift.

### R2 — The running process holds the active key (High, unavoidable)

A compromise of the service account (A2) yields all key material and the ability to forge
audit history. Rotation does not help: the attacker simply waits for the new key to be
loaded. Only an external boundary that never releases raw key material changes this.

*Compensating control:* network isolation, minimal service account privileges, no shell on
the application host, EDR, prompt patching. This is the dominant risk of the deployment
and it should be stated as such in the risk register.

### R3 — No split custody and no two-person rule (High for production)

One operator with file access can rotate, retire and (with confirmation) remove keys. The
ledger records *who* (`resolve_actor`: OS user, host, pid) and *what*, but it cannot prevent
a single authorised operator from doing something destructive, and the actor identity is
only as trustworthy as the host.

*Compensating control:* operational — change management, two people on the console, review
of `monitor` and ledger exports. Not enforced by code.

### R4 — Rotation cannot re-key what it does not control (Medium)

Backups, replicas, exports, snapshots and disaster-recovery copies taken before a rotation
still need the old key. `vault-remove` refuses while copies are unconfirmed; the operator
asserts it. A wrong assertion is silent data loss discovered months later.

*Compensating control:* retain old versions, verify a restore of the oldest retained backup
before retiring, documented retention period.

### R5 — Key deletion is irreversible and partly trusted (Medium)

Removal requires a confirmation flag. The refusal path is recorded; the confirmation is
not independently verified against actual copies.

### R6 — Actor attribution is host-based (Medium)

`resolve_actor()` records the OS user (`getpass.getuser()`), hostname, pid and the invocation
source - and nothing more. A person using the service account is indistinguishable from the
service itself, and the OS account name is only as trustworthy as the host. There is no
authenticated operator identity and no override mechanism; adding one without authentication
would only add a field an attacker can set.

*Compensating control:* perform rotations from a bastion with individual accounts, tie every
run to a change ticket, and correlate ledger `actor` values with change records.

### R7 — The rotation ledger key sits beside the ledger (Medium)

The ledger is tamper-*evident*, not tamper-*proof*: an attacker with the ledger key and
file access can rewrite history. Tail truncation is invisible to the chain alone; only an
off-host checkpoint catches it. Both limits are documented in the module and asserted in
tests rather than glossed over.

*Compensating control:* export checkpoints off-host on a schedule (`ops/` ships systemd
units; the procedure, retention and response steps are in
`docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md`) and verify daily with
`monitor --checkpoint-verify --max-checkpoint-age-days`. Alerts fire on
`LEDGER_UNREADABLE`, `CHECKPOINT_MISMATCH`, `CHECKPOINT_TRUNCATED`, `LEDGER_KEY_UNAVAILABLE`
and a stale checkpoint; all of them exit `3`.

The code now enforces the parts of this control that can be enforced locally: a checkpoint
is refused if the ledger cannot be verified, refused if the destination is inside the
ledger's own directory or the vault, and rejected on read if it is empty, truncated,
wrong-format, wrong-schema or carries a malformed head hash. Previously those documents
were treated as "nothing to compare" and the verification reported success. Scheduling and
the physical separation of the off-host store remain operational, not code, controls.

### R8 — Windows mode bits are not access control (Medium, Windows only)

`describe_key_protection()` returns `enforced: False` on Windows: `chmod` semantics do not
map to NTFS ACLs, so the custody check is skipped and the deployment must restrict the
directory ACL itself. The report says so instead of claiming a control that isn't there.

*Compensating control:* set an explicit ACL on the key directory
(`icacls <dir> /inheritance:r /grant:r "<svc>:F"`), verify with `monitor`.

### R9 — Key material in the backup archive (Medium)

Backups carry every key version so they can be restored without external input. The archive
is Fernet-encrypted, but whoever holds the archive key holds everything inside it,
including keys. This is a deliberate usability/security trade-off, documented in the
recovery guide.

### R10 — No automatic rotation, and no enforcement of key age (Medium)

Rotation is operator-invoked. `monitor --max-key-age-days` reports overdue keys and exits
non-zero, but nothing rotates them. A deployment that ignores the report drifts.

*Compensating control:* schedule `monitor` and alert on its exit code; record rotation
dates in the change log.

---

## 4. What the code enforces today (verifiable)

| Claim | Where | Test |
|---|---|---|
| Keys are `0600` in a directory without group/other access | `key_provider.ensure_key_directory`, `FileKeyProvider.write` | `test_write_creates_owner_only_file_and_directory` |
| A key readable by others is refused at start-up | `require_key_file_custody`, all load paths | Phase 15 (13 tests) |
| Symlinked keys are refused | `FileKeyProvider.read` (`O_NOFOLLOW`) | `test_refuses_to_follow_a_symlinked_key` |
| The check and the read use the same descriptor | `FileKeyProvider.read` (`fstat`) | `test_mode_is_checked_on_the_descriptor_not_the_path` |
| No provider claims HSM equivalence | `describe()` | `test_protection_report_never_claims_hsm_equivalence`, `test_kms_provider_is_not_implemented_and_says_so` |
| The KMS provider refuses rather than faking a key | `KmsKeyProvider.read` | same |
| Overdue keys, unfinished rotations, custody drift and ledger integrity are machine-readable | `rotate_keys monitor` (exit 3 when unhealthy) | Phase 15 (5 tests) |
| No key material appears in reports | `monitor`, `status`, ledger, journals | asserted in Phase 14/15 |

---

## 5. Deployment requirements (not satisfiable by this repository)

These must be in place before real data is processed. They are **open** in the go/no-go
record.

1. Restrict the key directory to the service account only (ACL on Windows, `0700` +
   dedicated UID on POSIX), and place it outside any web root, repository or backup path
   that is readable more widely.
2. File-integrity monitoring on the key directory: alert on any read by an unexpected UID
   and on mode/owner change. This is the only detection for R1.
3. Protected storage for the **backup** archive key, held separately from the backups.
4. Off-host retention of `monitor` output and ledger checkpoints (`--checkpoint-export`),
   with alerts on `healthy: false`. Scheduled export and daily verification units are
   provided in `ops/`; the operator must confirm that `CHECKPOINT_DIR` is genuinely
   off-host, because the code can only reject paths inside the ledger directory and the
   vault. Procedure: `docs/KEY_LEDGER_CHECKPOINT_RUNBOOK.md`.
5. Documented, reviewed rotation schedule, and a restore rehearsal before every key
   retirement.
6. Volume encryption at rest, so a stolen disk yields ciphertext (A6).

---

## 6. Why not "just add KMS support"

Because a KMS integration that still exports raw key material changes nothing about R1/R2,
while allowing the claim "we use a KMS". Honest options:

* **Wrap/unwrap boundary:** the provider asks the boundary to unwrap a data key per
  operation; the plaintext key is never persisted, and rotation re-wraps rather than
  re-encrypting the corpus. This reduces R1 but not R2 (a compromised process still asks
  the boundary to unwrap).
* **Encrypt/decrypt in the boundary:** `Fernet`-style local encryption is replaced by
  boundary calls. This addresses R1/R2 but requires re-encrypting every existing blob and
  puts the boundary on the read path (latency, availability, cost).
* **HSM with a two-person activation policy:** the only option that meaningfully addresses
  R3.

Each is an architecture change with a cost. The `KeyProvider` protocol exists so that the
change is contained when it is funded; `KmsKeyProvider` exists so the gap is visible in
`describe()` until then.

---

## 7. Decision and review triggers

**Decision:** accept R1–R3 and R6–R9 as residual risk for staging and pilot use with
synthetic or low-sensitivity data; **do not** treat them as acceptable for production
financial data without the compensating controls in §5, and do not represent the deployment
as HSM-backed.

**Re-review when any of these occurs:**

- real taxpayer or customer data is loaded;
- the deployment moves to a shared or multi-tenant host;
- a key file's mode or owner changes unexpectedly (the `monitor` alert);
- a rotation is interrupted and not recovered (`open_operations` non-empty);
- more than one operator needs key access;
- an external auditor asks for the custody evidence — §4 is that evidence, and §3 is what
  must be disclosed alongside it.
