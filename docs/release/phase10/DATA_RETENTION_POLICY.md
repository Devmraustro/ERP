# Data Retention Policy — V1

**Document owner:** the operating company (not the software vendor)
**Status:** DRAFT — **every statutory period below is PENDING** expert-comptable or
legal confirmation. No Algerian retention period is asserted in this document.
**Applies to:** the V1 deployment (source tree + CPython 3.12.10 + Local File Vault +
SQLite), as described in `docs/release/phase9/DEPLOYMENT_READINESS_REPORT.md`.

> **Why this document contains no statutory numbers.** Retention periods for Algerian
> accounting and tax records, and for personal data under Law 18-07, are legal
> questions. Inventing a plausible-looking number would be worse than admitting the
> gap: an operator would delete records that must be kept, or keep data that must be
> erased, while believing the policy was authoritative. Every period is therefore
> marked **PENDING — requires [expert-comptable | legal counsel] approval**, with the
> question written out so it can be answered.

## 1. Scope and vocabulary

| Term | Meaning here |
| --- | --- |
| **Record** | a row in the ERP database (invoice, journal entry, partner, user, audit event) |
| **Source document** | the original supplier invoice file the user supplied (PDF/photo) |
| **Derived artefact** | OCR text, extracted fields, review decisions, corrections |
| **Backup** | an encrypted archive produced by `dz_erp.ops_backup` |
| **Deletion** | irreversible removal from the live store *and*, after the backup window, from backups |
| **Archive** | a record moved out of the live database but preserved, readable and reconstructible |
| **Legal hold** | a flag that suspends all deletion for a defined set of records |

## 2. Classes of data and their periods

| # | Data class | Where it lives | Period | Authority / status |
| --- | --- | --- | --- | --- |
| R1 | Financial records: journal entries, journal lines, trial balance inputs | `journal_entries`, `journal_entry_lines` | **PENDING** | expert-comptable + legal |
| R2 | Supplier invoices and their validation/review state | `supplier_invoices` | **PENDING** | expert-comptable |
| R3 | Supporting documents: the original invoice files | Local File Vault (`*.enc`) | **PENDING** — must not be shorter than R1/R2 | expert-comptable + legal |
| R4 | OCR source files and derived extraction (text, fields, boxes, confidence) | `documents`, extraction tables, vault blobs | **PENDING**; operational default: same as R2 | expert-comptable |
| R5 | Audit log: hash-chained audit events | `audit_events` (+ `ERP_SECURITY_LOG_PATH` JSONL) | **PENDING** — the chain is evidence; deleting part of it destroys verifiability | legal counsel |
| R6 | Security log: authentication and access events | `ERP_SECURITY_LOG_PATH` (rotated daily, 14 kept) | **PENDING**; operational default 14 days | legal counsel (Law 18-07) |
| R7 | User records: identity, role, active flag, password hash | `users` | **PENDING** after deactivation | legal counsel |
| R8 | Backups | `ERP_BACKUP_DIR` (off-host) | **7 daily + 4 weekly** (configured, enforced by `dz_erp.ops_backup prune`) — **PENDING** as a *statutory* answer | company + expert-comptable |
| R9 | Operational logs: readiness checks, backup verification summaries | journal, `verify-status.json` | 8 weeks (configured via logrotate) | company |
| R10 | Personal data inside invoices (supplier contacts, employee references) | within R2/R3 | **PENDING** | legal counsel (Law 18-07) |

**The operational defaults in R8/R9 are configuration, not a legal position.** They can
be changed by the company without a code change; the statutory question in R1–R7 and
R10 cannot.

## 3. Deletion: who may authorise it

Deletion is the only irreversible operation in the system; the software deliberately
has **no API endpoint** that hard-deletes an invoice, a journal line or an audit event.
Records are reversed (a compensating entry) or deactivated (a user), never removed.

| Deletion target | Authorisation required | Mechanism |
| --- | --- | --- |
| Any record covered by a legal hold | **Prohibited** until the hold is lifted in writing | application refuses; hold flag in backup manifests |
| Journal entries / invoices (R1, R2) | **Not permitted.** Reverse instead — the reversal is the record | `POST /api/v1/invoices/{id}/reverse` |
| Supporting documents (R3) | Written instruction from the company's legal representative + expiry of the retention period | vault is append-only in V1; removal is a manual, documented operation on the host |
| Audit events (R5) | **Not permitted** while the retention period runs; afterwards, only the whole chain, never a prefix | `verify_audit_chain` will fail on a broken chain, by design |
| Backups (R8) | Automatic at the end of the retention window; any manual deletion is logged | `ops_backup prune`, `prune --dry-run` to preview |
| User records (R7) | Company administrator, with an audit event | deactivation UI/API; anonymisation **PENDING** legal |

Every authorised deletion must be recorded with: date, actor, data class, record
identifiers, authority (ticket/written instruction), and the retention rule applied.

## 4. Legal hold

1. A legal hold is declared in writing by the company's legal representative and covers
   an explicit set of records (invoice numbers, date range, partner).
2. On declaration, the operations owner sets `legal_hold: true` in the manifests of all
   backups that contain the affected period, and marks the records in the operational
   register. `ops_backup prune` will never delete a held artefact (enforced in code and
   covered by `test_retention_respects_a_legal_hold`).
3. Deletion, reversal-by-policy and retention pruning are suspended for the held set.
4. The hold is lifted only in writing; lifting is recorded with the same detail as a
   deletion authorisation.
5. **Gap, stated plainly:** V1 has no per-record hold flag in the database. A hold is
   currently an operational procedure plus backup-level protection, not an enforced
   database constraint. Enforcing it needs a schema change and is **not implemented**.

## 5. Archiving

- **V1 archive = the encrypted backup.** There is no separate "cold store" tier and no
  partial archive of individual records.
- An archive must always include, together: the database, the vault, the manifest, and
  **all key versions** referenced by the archive (the backup already carries the key
  registry, encrypted inside the envelope).
- An archive without its key material is *not* an archive — it is unreadable bytes.
  The one rule that cannot be broken: **the backup encryption key and the archived
  archive must never be stored in the same place.**
- Archives are verified by hash on the day they are written and by `verify --deep`
  weekly (restore + trial balance + audit chain).

## 6. Restoration

| Scenario | Procedure | Evidence |
| --- | --- | --- |
| Wrong data entry | reverse the entry, never restore | `END_TO_END` scenario 5 |
| Deleted by mistake (not possible through the API) | restore into a *separate* directory, extract the record, re-enter it with the normal workflow | `BACKUP_AND_RECOVERY_RUNBOOK.md` |
| Corruption / disk loss | restore the newest verified archive into a fresh directory, verify, then switch the service to it | runbook §5 |
| Audit or legal request | restore a copy for inspection; never modify the live database | runbook §6 |

Restores are always performed **into a new directory** by
`dz_erp.security_audit.restore_and_verify_backup`; it refuses to overwrite an existing
one. A restore is verified by hash, `integrity_check`, foreign keys, trial balance and
audit chain before anyone relies on it.

## 7. Retention of backups versus retention of records

The two must not be confused:

- **Record retention** (R1–R7) says how long the company must *keep* the data.
- **Backup retention** (R8) says how long a *recoverable copy* exists.

If backup retention is shorter than record retention — the normal case — then data
deleted from the live system becomes unrecoverable after the backup window, while the
records that must be kept remain in the live database. **If a record must be kept for
longer than the backup window, it must never be deleted from the live database**,
because the backups that could restore it will have expired.

## 8. Open questions that block this policy from becoming authoritative

| # | Question | Addressed to | Status |
| --- | --- | --- | --- |
| Q1 | Retention period for accounting records and supporting documents (R1–R3) | expert-comptable / legal | **PENDING** |
| Q2 | Retention period for the audit trail, and whether it may ever be pruned (R5) | legal | **PENDING** |
| Q3 | Is a hash-chained audit log acceptable as evidence in Algerian proceedings, and in what exported form? | legal | **PENDING** |
| Q4 | Law 18-07 obligations: lawful basis, retention, and the data subject's deletion right versus the accounting retention duty (R10) | legal | **PENDING** |
| Q5 | May user records be anonymised instead of kept (R7)? | legal | **PENDING** |
| Q6 | Is off-host backup storage inside Algeria required? | legal | **PENDING** |
| Q7 | Must source documents be kept in their original format and hash? (V1 keeps both) | expert-comptable | **PENDING** |
| Q8 | Does a legal hold need a court order, or is a written internal decision sufficient? | legal | **PENDING** |

## 9. What this policy does *not* claim

It does not claim that the configured windows satisfy Algerian law. It claims that:
the software enforces whatever is configured; deletion of financial and audit records
is not exposed as an operation; backups are pruned with a hard floor (never the newest,
never a held artefact); and every unknown has a named owner and a written question.
