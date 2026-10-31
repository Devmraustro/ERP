# Data protection and compliance posture

**Read this first:** this document describes what the software *does* and what it
*does not* do about personal-data protection. It is not legal advice and it is not
a compliance attestation. Questions about Algerian Law 18-07 (protection of
natural persons in the processing of personal data) require a qualified adviser;
several items below are marked `BLOCKED_EXTERNAL` for exactly that reason.

---

## 1. What data the system holds

| Category | Where | Why it is there |
|---|---|---|
| Business contact data (supplier/customer name, NIF, NIS, AI, RC, NIN, address, bank RIB) | `partners` | legal identification of the counterparty on invoices |
| Employee-identifying data (username, role, hashed password, login timestamps, IP, user agent) | `users`, `auth_tokens`, security log | authentication and accountability |
| Financial records (invoices, journal entries, G50 figures) | `journal_entries`, `supplier_invoices` | accounting record |
| Documents (supplier invoices, possibly containing personal data) | encrypted vault | the source of record |

The system is designed for **B2B accounting**, not for consumer personal data.
That is a limitation to state in a privacy notice, not a licence to ignore one.

## 2. Controls that exist in the code

| Property | Implementation | Evidence |
|---|---|---|
| Documents encrypted at rest | Fernet-encrypted vault, key held beside it (or by a provider) | intake/OCR suites |
| Passwords never stored in a readable form | salted hash with a work factor | auth suites |
| Session tokens stored as hashes only | `auth_tokens` stores a digest | `test_phase7_…` |
| Credentials never appear in logs | structured logger redacts credential-shaped values; asserted | `test_no_password_or_token_is_ever_logged` |
| Personal identifiers are validated, not inferred | NIF/NIS/AI/NIN/RC format checks | `tests/test_phase2_localization.py` |
| Every access to a document or invoice is attributable | audit events, hash-chained | `test_phase5_…`, `test_phase11_…` |
| Deletion of a user is deactivation, not erasure | `deactivate_user` preserves the audit trail | `test_phase28_user_admin.py` |
| Tenant separation | every query carries `company_id` | multiple suites |

## 3. The tension the software cannot resolve

Accounting law requires that posted records and their audit trail be **retained**
and remain verifiable. Data-protection law gives a person a right to **erasure**.
For posted financial records those obligations conflict, and a database trigger is
not the place to decide the conflict.

What the system does today:

* a user cannot be hard-deleted (deactivation only), so the audit trail survives;
* posted journal entries and raw OCR text are immutable;
* partner records can be superseded, but the historical invoices that cite them
  are retained unchanged.

What it does **not** do: it does not implement a subject-access or erasure
workflow, it does not pseudonymise historical records, and it does not expire
anything. Those are policy decisions that must be made by the engagement's legal
adviser and implemented deliberately — with the retention obligation in view.

## 4. Cross-border and hosting questions

Where the database, the vault and the security log physically reside decides which
obligations attach. Nothing in this repository constrains that: the deployment guide
describes a single host with volumes, and an operator could equally place it
offshore. This must be decided, documented and reviewed before real data is loaded.

## 5. Items that remain `BLOCKED_EXTERNAL`

| Item | Blocked on | Evidence required |
|---|---|---|
| Registration / notification with the ANPDP (if applicable to the processing) | legal adviser | filed reference |
| Retention period per record class (invoices, journal, logs, backups) | accountant + legal | written schedule |
| Erasure and subject-access procedure | legal | signed procedure, with the conflict in §3 resolved |
| Privacy notice content and lawful basis | legal | published notice |
| Cross-border transfer assessment | legal | assessment memo |
| Breach notification procedure and timelines | legal + operations | procedure with named owner and tested contact list |
| Processor agreements (hosting provider) | legal | executed agreement |

None of these can be closed by code, and none has been claimed as closed. Until
they are, the release readiness matrix keeps the corresponding rows
**Blocked (external evidence required)** and no statement in this repository should
be read as a compliance claim.
