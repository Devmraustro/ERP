# Rotation ledger checkpoints — operational runbook

**Control:** S4 (rotation history rewrite / undetectable tail truncation)
**Code:** `dz_erp/rotation_audit.py` (`export_checkpoint`, `load_checkpoint`, `validate_checkpoint`,
`verify_against_checkpoint`), `dz_erp/rotate_keys.py monitor`
**Tests:** `tests/test_phase20_checkpoints_and_monitoring.py`, `tests/test_phase14_rotation_audit_ledger.py`

---

## 1. Why this exists

Every privileged key operation (vault rotation, audit rotation, checkpoint export) appends a record to an
HMAC-chained ledger. Each record commits to its predecessor, so a modified or reordered record is detected.

The chain **cannot** detect removal of records from the **end**. A ledger truncated to its first *n*
records is a valid prefix: every MAC still verifies, the sequence is still contiguous, and `verify()`
reports `valid: true`. An attacker who can write the ledger file — or an operator "cleaning up" — can
therefore erase the most recent operations, including the record of their own access, without leaving a
trace in the file that remains.

The only defence is a **head hash retained where the attacker is not**. That is a checkpoint:

```json
{
  "format": "dz-erp-rotation-ledger",
  "schema": 1,
  "exported_at": "2026-10-23T09:14:02.418831+00:00",
  "record_count": 42,
  "head_hash": "…64 lowercase hex characters…",
  "last_recorded_at": "2026-10-23T09:14:01.900112+00:00"
}
```

The checkpoint carries **no key material and no record contents** — only a record count and a hash. It is
safe to store in a system that is less protected than the ERP host, which is the point: if it is lost
along with the ledger, the control is gone.

A checkpoint proves one thing only: *at `exported_at`, the ledger held `record_count` records and ended
at `head_hash`.* Verification then checks that the record at that position still has that hash, and that
no records below it have been removed.

---

## 2. What is enforced in code

| Behaviour | Status |
|---|---|
| Export refuses if the ledger does not verify | Enforced (`RotationLedgerTamperError`) |
| Export refuses if a key version needed to check the ledger is unavailable | Enforced (`RotationLedgerError`) |
| Export refuses a destination inside the ledger's directory or the vault | Enforced (`RotationLedgerCheckpointError`) |
| Verification rejects an empty, partial, wrong-format, wrong-schema, non-integer or negative checkpoint | Enforced (`validate_checkpoint`) |
| Verification rejects a head hash that is not 64 lowercase hex | Enforced |
| Fewer records than the checkpoint reports → `RotationLedgerTruncatedError` | Enforced |
| Different hash at the checkpointed position → `CHECKPOINT_MISMATCH` | Enforced |
| More records than the checkpoint reports → normal, reported as `records_appended_since_checkpoint` | Enforced |
| **Exporting on a schedule** | **Not enforced — this runbook** |
| **Storing the checkpoint on another host** | **Not enforced beyond refusing same-directory paths — this runbook** |
| Staleness alarm | Available (`--max-checkpoint-age-days`); only active when the operator sets it |

The last three rows are the honest boundary: the code can make a *correct* checkpoint and can refuse an
obviously useless one, but it cannot know whether a path that leaves the machine is really off-host, and it
cannot run on a schedule by itself.

### Exit codes (`python -m dz_erp.rotate_keys monitor`)

| Code | Meaning |
|---|---|
| `0` | Healthy: ledger and checkpoint match, keys within age limits, no unfinished rotation, custody tight |
| `3` | Unhealthy: truncation, `CHECKPOINT_MISMATCH`, stale checkpoint, overdue key, unfinished rotation, loose key permissions, missing key, or required external provider absent |
| `2` | Refused configuration error: ledger key missing/unreadable, keyring invalid (`REFUSED:` on stderr) |
| `1` | Unexpected operation failure |

`3` is the code a scheduler must alarm on. Treat any non-zero as a page.

---

## 3. Scheduled export (pick one)

Run as the same OS user that owns the key files, from the deployment directory. `${STATE}` is the
directory holding `erp.sqlite3` and `vault/`.

### systemd timer (recommended)

`ops/dz-erp-checkpoint-export.service` and `ops/dz-erp-checkpoint-export.timer` are provided. Install:

```bash
sudo cp ops/dz-erp-checkpoint-export.* /etc/systemd/system/
sudo systemctl edit dz-erp-checkpoint-export.service   # set User=, WorkingDirectory=, ERP_STATE=
sudo systemctl enable --now dz-erp-checkpoint-export.timer
systemctl list-timers dz-erp-checkpoint-export.timer
```

The unit writes to `/var/lib/dz-erp-checkpoints/` — **this must be a mount that survives loss of the ERP
host** (separate machine, NFS/SMB share, or object-store client). A directory on the same disk is not a
checkpoint; it is a second copy of the ledger's fate.

`ops/dz-erp-checkpoint-verify.service` and `ops/dz-erp-checkpoint-verify.timer` run the daily
verification. They are separate from the export so that a failing verification cannot be masked
by a successful export in the same unit:

```bash
sudo cp ops/dz-erp-checkpoint-verify.* /etc/systemd/system/
sudo systemctl enable --now dz-erp-checkpoint-verify.timer
```

### cron

```cron
# Every 15 minutes, then verify against the previous export.
*/15 * * * * dz-erp cd /srv/dz-erp && \
  python -m dz_erp.rotate_keys monitor --vault "$STATE/vault" \
    --checkpoint-export /mnt/audit-offsite/checkpoints/vault.rotation.checkpoint.json \
  >> /var/log/dz-erp/checkpoint.log 2>&1
```

### Retention

Keep a **rolling history**, not just the newest file, so an incident can bound when a change happened:

```bash
# Keep one file per hour for 30 days, then one per day for a year.
mv /mnt/audit-offsite/checkpoints/vault.rotation.checkpoint.json \
   /mnt/audit-offsite/checkpoints/vault.rotation.$(date -u +%Y%m%dT%H%M%SZ).json
```

An object store with versioning or WORM retention is preferable to local rotation. Whatever the mechanism,
**deletion of old checkpoints must require a different credential from the one that runs the ERP** —
otherwise the attacker deletes the evidence with the same access that lets them truncate the ledger.

---

## 4. Daily verification

```bash
python -m dz_erp.rotate_keys monitor \
  --vault "$STATE/vault" \
  --checkpoint-verify /mnt/audit-offsite/checkpoints/vault.rotation.checkpoint.json \
  --max-checkpoint-age-days 2
echo "exit=$?"
```

`--max-checkpoint-age-days` should be roughly twice the export interval. If the export runs hourly, a
checkpoint older than 2 days means exports have been failing silently — an alarm, not a formality,
because the window of undetectable truncation is exactly the age of the newest checkpoint.

The report embeds the result:

```json
"checkpoint_verification": {
  "valid": true,
  "checkpoint_matched": true,
  "checkpoint_record_count": 42,
  "checkpoint_exported_at": "2026-10-23T09:14:02+00:00",
  "records_appended_since_checkpoint": 3,
  "age_days": 0.02
}
```

---

## 5. Responding to an alarm

**`CHECKPOINT_TRUNCATED` (exit 3).** The ledger holds fewer records than a retained checkpoint recorded.

1. Do **not** restart services, rotate keys, or "repair" the ledger. Every one of those changes evidence.
2. Copy the ledger file, its keyring, `security.jsonl` and the checkpoint to a separate host immediately.
3. Record `expected_record_count`, `actual_record_count` and the ledger's `last_recorded_at` in the
   incident log. The missing records are the operations between the checkpoint's `last_recorded_at` and
   now — that is the window to investigate.
4. Compare against the operational `security.jsonl` and the vault/audit rotation journals, which are
   separate files. If those show operations the ledger no longer contains, the truncation is confirmed and
   the incident is a compromise of the ledger key or of the host, not a storage fault.
5. Restore the ledger from backup only after imaging the current one, and only if a backup checkpoint
   independently matches the restored head.

**`CHECKPOINT_MISMATCH` (exit 3).** The record at the checkpointed position has a different hash: the
ledger was rewritten from that point onward, not merely shortened. Same containment, higher severity —
assume the key was used outside the application.

**`LEDGER_KEY_UNAVAILABLE` (exit 3) or `REFUSED` (exit 2).** The key material needed to check the ledger
is missing. This is *not* evidence of tampering and must not be recorded as such. Restore the key version
from escrow/backup and re-verify. Note that a missing keyring also means **nothing can be proven about the
ledger** in the meantime — the ledger is unverified, not verified-and-fine.

Monitoring deliberately opens the ledger read-only. Earlier versions opened it with `create=True`, which
generated a fresh keyring when the key was missing and then reported `RECORD_TAMPERED`; that produced a
false tampering accusation and destroyed the evidence that the key had disappeared.

**Stale checkpoint (exit 3).** Exports are not running. Fix the timer; do not lower the age limit to make
the alarm stop.

---

## 6. Limits this control does **not** cover

* **An attacker who holds the ledger key and the checkpoint.** If the compromise includes the off-host
  store, they can forge a consistent history and a matching checkpoint. Only a checkpoint on media or in an
  account they do not control, with independent credentials, resists this.
* **Simultaneous total loss.** A checkpoint on the same disk, host or backup set as the ledger detects
  nothing. The code refuses paths inside the ledger's directory and the vault, but cannot tell whether a
  mount is physically separate — verify that yourself.
* **Records that never existed.** A compromise that operates entirely outside the application writes no
  ledger record; the ledger is evidence of what the application did, not of what happened on the host.
* **Time.** `exported_at` is the host clock. If the host clock is untrusted, treat checkpoint timestamps as
  ordering hints, not proof of when something happened.
