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

## 6. Technical compliance checklist (Law 18-07 / ANPDP)

Status vocabulary: `IMPLEMENTED` (code exists), `TESTED` (automated tests),
`CI_VERIFIED`, `STAGING_VERIFIED`, `EXTERNALLY_APPROVED`, `BLOCKED`, `NOT_VERIFIED`,
`NOT_APPLICABLE`. A row may be `IMPLEMENTED` and `TESTED` while still being
`BLOCKED` on external evidence — that is the expected state for this project.

| Obligation | Technical control in the code | Missing technical control | Legal decision | External evidence |
|---|---|---|---|---|
| Registration / notification with the ANPDP (where applicable) | none — registration is an organisational act | n/a | ANPDP registration or notification, as applicable | filed reference |
| Lawful basis for processing | none in code — the basis is a legal determination per processing purpose | n/a | lawful basis per purpose (contract, legal obligation, legitimate interest) | written determination |
| Data minimisation | only the fields the schema needs are stored; no user-agent or request body in logs; query strings stripped from `HTTP_REQUEST` | none identified | scope of processing agreed | processing register |
| Purpose limitation | B2B accounting scope; security logging purpose documented in `docs/LOG_RETENTION_AND_ACCESS.md` §1 | none identified | documented purposes per category | processing register |
| Storage limitation / retention | Size- and count-bounded security log (100 MiB × 10 default, configurable); age-based deletion available as an opt-in, policy-driven capability (`ERP_SECURITY_LOG_MAX_AGE_DAYS`, default disabled) | no automatic deletion by age unless configured; no off-host shipping; no in-app access audit | retention period per record class, legal reference, deletion trigger, accountable person | signed retention schedule |
| Integrity and confidentiality | Encryption at rest for documents (Fernet vault); hashed passwords and session tokens; tenant isolation on every query; audit chain HMAC-chained; redaction of NIN/RIB/IBAN/tokens in logs | no KMS/HSM (file-backed keys); no off-host checkpointing | key custody and backup custody decisions | provider contract / recorded acceptance |
| Access control | Role-based access; SoD (creator cannot approve); audit immutability by trigger; file mode `0600` on logs | no in-application log read/search/export endpoint (by design); no role-based log access inside the app | access policy and access-audit policy | access matrix signed by the DPO |
| Data subject rights (access, rectification, objection, erasure) | deactivation instead of hard delete (audit trail preserved); partner supersession; no erasure workflow | no subject-access or erasure workflow; no pseudonymisation of historical records | resolution of the retention/erasure conflict for posted financial records | signed procedure |
| Cross-border transfers | none — hosting is an operator decision | none | hosting location, transfer assessment, adequacy or safeguards | transfer assessment memo |
| Security incident handling | durable redacted security log; audit chain; backup/restore; key rotation with verification | no alert channel (detection exists, nobody is paged); no breach-notification procedure | breach notification procedure and timelines with a named owner | procedure with tested contact list |
| Processor / vendor responsibilities | none in code | none | processor agreements with hosting and any third-party service | executed agreements |
| Accountability / documentation | processing register not implemented | n/a | processing register, DPO appointment, ANPDP filing where applicable | filed reference |

**Summary.** The implemented technical controls are real and tested (encryption at rest,
redaction, tenant isolation, audit immutability, bounded logging, key rotation). Every
row that requires a legal determination, a signed procedure or an external artefact is
`BLOCKED_EXTERNAL` and is **not** claimed as closed by this repository. Nothing in
this document or in the code should be read as a compliance attestation.
