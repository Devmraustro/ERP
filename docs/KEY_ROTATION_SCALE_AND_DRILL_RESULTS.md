# Rotation at scale: measured load and recovery drill results

This is the evidence for the Phase 4 claim "rotation and recovery were exercised at a
realistic size, and the failure paths were actually interrupted", and — equally important —
a statement of what these numbers do **not** support.

Reproduce with `tools/rotation_scale_drill.py` (no fixtures to maintain, no credentials,
synthetic data only):

```bash
python tools/rotation_scale_drill.py --documents 1000 --events 10000 --json drill.json
```

---

## 1. How the measurement works

Each run builds a fresh synthetic deployment and measures, in one process:

| Measured | How | Why it matters |
|---|---|---|
| Rotation duration and throughput | timed `vault.rotate_key()` / `db.rotate_audit_key()` | The dominant operational cost; sets the maintenance window |
| Verification duration | timed `verify_blobs()` and `verify_all_audit_chains()` | Pre-flight and post-rotation checks are not free on large data |
| Backup / restore duration, archive size | `create_encrypted_backup` / `restore_and_verify_backup` | The recovery path is the one that must work when something is already wrong |
| Restore correctness | manifest SHA-256, per-blob hashes, audit chain verification, **trial balance** | A restore that returns files but not books is not a restore |
| Peak RSS | `resource.getrusage(RUSAGE_SELF).ru_maxrss` | Rotation must not need memory proportional to the corpus |
| Interruption behaviour | a **separate process** is killed with `os._exit(137)` at 10 %, 50 %, 90 %, then resumed | An in-process exception is not an interruption: nothing gets to write a terminal record |
| Convergence | every blob's key-version header re-read after the run | "Everything decrypts" does not prove nothing still needs the old key |
| Concurrent-write safety | 4 writer threads storing documents during the rotation | Real vaults are not quiesced |

Storage is a local filesystem and a local SQLite file. Any number below is a *lower bound*
for production, where storage is networked and the machine is shared.

---

## 2. Environment

```
Linux 6.1.158 x86_64 (glibc 2.36), 2 vCPU, container filesystem
Python 3.11.2, cryptography 44.0.2, SQLite (stdlib)
Fernet (AES-128-CBC + HMAC-SHA256) per blob; HMAC-SHA256 per audit event
```

Two vCPUs in a container is a modest, honest target: it is roughly a small staging VM, not
a production database host.

---

## 3. Results

### 3.1 Throughput

| Documents | Audit events | Vault rotation | Docs/s | Audit rotation | Vault verify | Chain verify |
|---:|---:|---:|---:|---:|---:|---:|
| 200 | 1 000 | 0.27 s | 739 | 0.025 s | 0.012 s | 0.010 s |
| 300 | 3 000 | 0.45 s | 663 | 0.058 s | 0.018 s | 0.026 s |
| 1 000 | 10 000 | 1.45 s | 688 | 0.176 s | 0.073 s | 0.094 s |

Throughput is **flat** (≈660–740 documents/s) across a 5× range, which is what you expect
when per-document cost is dominated by encrypt + atomic write + `fsync`-ish rename rather
than by anything that grows with the corpus. That is the basis for extrapolating linearly
below.

### 3.2 Backup and restore (1 000 documents, 10 000 events)

| Metric | Value |
|---|---|
| Archive size | 2 528 484 bytes (2.4 MiB) for 1.47 MB vault + 2.96 MB DB |
| Backup | 0.41 s |
| Restore + verify | 0.28 s |
| Database SHA-256 vs manifest | verified |
| Vault blobs verified | 1 000 / 1 000 |
| Audit chains after restore | valid |
| Trial balance (debit / credit) | 10 020.00 / 10 020.00 — balanced |

The trial balance is included on purpose: with no posted entries it would trivially read
0.00/0.00 and the restore check would prove nothing about the books. The drill creates five
balanced posted entries per company.

### 3.3 Interruption and recovery (1 000 documents)

The rotator was killed outright — no exception, no terminal ledger record — at three points,
then resumed in a new process.

| Killed at | Blobs still on old key | Readable after the kill | Resume duration | Converged after resume |
|---|---:|---|---:|---|
| 10 % | 900 | yes, 1 000/1 000 | 1.40 s | yes |
| 50 % | 500 | yes, 1 000/1 000 | 0.80 s | yes |
| 90 % | 100 | yes, 1 000/1 000 | 0.28 s | yes |

Recovery time tracks the work remaining, not the corpus size, because rotation is resumable
by construction: the registry is written before the first blob is touched, and a resumed run
skips what is already current. In every case the ledger showed the orphaned `STARTED`
operation, and the resumed run closed it with `RECOVERED` + `RECOVERY`.

The property that matters operationally: **at no point was any document unreadable**, including
between the kill and the resume. A rotation that leaves the vault unreadable after a crash
failure would be worse than not rotating.

### 3.4 Concurrent writes during rotation

| Metric | Value |
|---|---|
| Documents written by 4 threads during the rotation | 2 031 |
| Still readable afterwards | 2 031 / 2 031 |
| Blobs failing decryption | 0 |
| Key versions after the run | `{2: 2031}` — single version, no strays |
| Writer errors | 0 |

### 3.5 Memory

Peak RSS: 104 MB after fixture creation, 110 MB after a 1 000-document rotation including
verification, 124 MB after the restore (the restore holds the decrypted archive in memory).
Rotation does not buffer the corpus: the delta across 200 → 1 000 documents is ~6 MB, not
proportional to the 5× data growth.

---

## 4. Reference workload and thresholds

The thresholds the tool evaluates are **derived from these measurements with a 10× margin**,
and are stated as assumptions rather than as production guarantees:

| Parameter | Value | Basis |
|---|---|---|
| Reference documents | 20 000 | A mid-sized deployment: 10 companies × 2 000 documents |
| Reference audit events | 200 000 | 10 events per document, plus operational events |
| Measured throughput | ≈690 docs/s | §3.1, flat across sizes |
| Assumed production throughput | ≥20 docs/s | 34× margin for networked storage, contention and slower CPUs |
| Vault rotation at reference size | ≈29 s at measured rate; ≈17 min at the floor | 20 000 / 690 and 20 000 / 20 |
| Audit rotation at 200 000 events | ≈3.5 s at measured rate | 0.176 s per 10 000 events, linear in events |
| Chain verification | ≈46 000 events/s measured | 0.094 s for 10 000 events |
| Restore target | ≤15 min | Operator can meet the recovery objective with margin |
| Interruption recovery target | ≤15 min | Same |

Consequence, stated plainly: a 60-minute maintenance window comfortably covers the reference
workload even at 34× the measured cost. A deployment an order of magnitude larger than the
reference (200 000 documents) needs the throughput measured on **its own storage**, not this
table.

---

## 5. What this does not prove

* **Not production data.** Documents are ~1 KiB synthetic bodies. Real scanned PDFs are
  10–1000× larger, and the per-document cost is dominated by ciphertext size, so absolute
  throughput on real invoices will be lower. The drill measures *per-document overhead* and
  interruption behaviour; it does not predict a real corpus.
* **Not a production host.** 2 vCPU container, local filesystem, warm page cache. Networked
  storage, antivirus scanning and a busy host will all reduce these numbers.
* **Not a multi-host or clustered deployment.** Rotation here assumes one process owns the
  vault at a time. Rotating from two hosts simultaneously is not a supported configuration;
  the ledger and rotation lock coordinate within a host, not across hosts.
* **Not a backup-strategy test.** It verifies one archive in one directory. Retention,
  off-site copy, and restore from cold storage are operational, not measured here.
* **Not a security test.** Throughput says nothing about whether the keys are protected; that
  is `docs/KEY_CUSTODY_THREAT_MODEL.md`.
* **Not a durability test.** `atomic_write_bytes` plus rename is crash-safe against process
  death, not against hardware lying about flush semantics (see `KEY_ROTATION_DESIGN.md`).

---

## 6. Defect found by this drill

The drill is not decoration: it found a real data-loss trap that every unit test had passed
over.

With 4 writers active, a rotation reported `COMPLETED_VERIFIED` while **three blobs were
still on the previous key version**. `verify_blobs()` does not catch it — a blob encrypted
with the old key decrypts fine. The consequence is silent: an operator who trusts the report
retires the old key, and those documents become unreadable.

Cause: two windows. `store_bytes` read the active version, then encrypted and wrote, so a
rotation completing in between published a blob on the old version at a path the rotation had
already passed; and `rotate_key` made a single pass over a materialised directory listing, so
documents created behind the listing were never seen.

Fixed in `b042525`: the write is re-checked against the active version and re-encrypted if it
moved, rotation re-scans actual version headers and repeats until nothing references an old
version (bounded), `COMPLETED_VERIFIED` now requires convergence, and `stray_versions` is
reported. Regression tests in `tests/test_phase16_rotation_concurrency_convergence.py`,
including one that fails against the old write path.

This is the argument for keeping the drill runnable and in CI rather than treating a one-off
benchmark as evidence.

---

## 7. Running it

```bash
# Fast smoke (seconds) - enough to verify the harness still works
python tools/rotation_scale_drill.py --documents 40 --events 200

# The run recorded above (~20 s)
python tools/rotation_scale_drill.py --documents 1000 --events 10000 --json drill.json

# Custom interruption points; keep artefacts for inspection
python tools/rotation_scale_drill.py --documents 500 --events 5000 \
    --interrupt-at 0.25,0.75 --state /tmp/drill --keep
```

Exit code is `0` only when every threshold in §4 passes; `1` when the drill itself failed
(and the traceback is printed rather than swallowed). The tool refuses to run into a
non-empty `--state` directory so results cannot be mixed, and it never leaves key material in
`--json` output.
