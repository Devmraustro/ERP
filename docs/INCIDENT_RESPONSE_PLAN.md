# Incident response plan

**Scope:** a running deployment of this application. The plan is written so that
someone woken at 03:00 can follow it without reading the code.

**Honest status:** the *technical* capabilities it relies on exist and are tested
(audit chain verification, token revocation, log evidence, backup restore, key
rotation). The *organisational* parts — who is called, who decides, where the
evidence is stored — are placeholders that a named owner must fill in before this
plan is anything more than a template. Unfilled fields are marked `TBD`.

---

## 1. Severity

| Level | Definition | Examples | Response |
|---|---|---|---|
| **S1** | Financial integrity or confidentiality is or may be compromised | posted records altered, audit chain fails, cross-tenant data exposed, vault key leaked, database corrupt | immediate; freeze writes; notify owner |
| **S2** | Service unavailable or a security control disabled | API down for users, restore verification failing, rate limiting disabled by misconfiguration | same day |
| **S3** | Degraded or suspicious | repeated authentication failures for one account, unusual upload volume, alert noise | next business day |
| **S4** | Cosmetic / documentation | misleading message, doc drift | backlog |

## 2. Roles (fill in)

| Role | Responsibility | Name / contact |
|---|---|---|
| Incident commander | decides, communicates, owns the timeline | `TBD` |
| Application owner | knows the deployment, can stop it | `TBD` |
| Data owner (accountant) | decides on financial impact and correction | `TBD` |
| Legal / privacy contact | decides on notification duties | `TBD` |
| Platform/infra | host, network, TLS, storage | `TBD` |

## 3. First ten minutes (do these in order)

1. **Write down the time and what you observed.** No interpretation yet.
2. **Preserve evidence before changing anything**:
   * copy `$ERP_SECURITY_LOG_PATH` (and rotated files) to a separate location;
   * copy the database file *and* its `-wal`/`-shm` files;
   * record `git rev-parse HEAD` of the deployment and the deployed image digest.
3. **Stop the bleeding, minimally:** if financial integrity may be affected, stop
   the service (one process owns the SQLite writer — a second writer is worse than
   downtime).
4. **Revoke sessions** if credentials may be compromised:
   `POST /api/v1/auth/revoke` with the affected token, or revoke all tokens for the
   user through `AuthManager`.
5. **Verify the audit chain now** — before and after any intervention — so you can
   prove what changed while you were working:
   `ERPDatabase.verify_audit_chain(company_id=…)`.
6. **Escalate per §1.** Do not investigate alone past the first hour.

## 4. Playbooks

### 4.1 Suspected unauthorised financial record
1. Freeze: stop the service.
2. `verify_audit_chain` for the company. A failure means evidence tampering — go to
   §4.2 immediately.
3. Pull the audit trail for the suspicious invoice: create → review decisions →
   submit → approve → post → any reversal. The chain names the actor and the time.
4. Compare the posted journal lines with the approved payload digest. A mismatch is
   an S1: the approval gate was bypassed, which is a code-level defect and must be
   reported as such.
5. Correction is a **reversal credit note**, never an in-place edit. Posted records
   are immutable by design.

### 4.2 Audit chain verification failure
1. Treat as S1 — evidence of tampering or of a lost HMAC key version.
2. Determine which key version fails: `python -m dz_erp.rotate_keys status`.
3. If the key version is simply *missing* (restore gone wrong), recover the key
   material from backup; do **not** re-sign or rewrite events.
4. If the key exists and the chain still fails, the stored events were altered.
   Preserve, report to the owner, and prepare a statement of the affected range.

### 4.3 Credential leak or brute force
1. `SECURITY_AUTH_RATE_LIMITED` spikes identify the targeted accounts.
2. Revoke the affected tokens; force a password reset for the targeted users.
3. If the leak is a token, its TTL bounds exposure — record the window.
4. If the leak is a password, check `auth_tokens` for sessions issued from
   unexpected addresses and `audit_events` for actions taken in the window.

### 4.4 Data corruption or failed restore
Follow `docs/BACKUP_AND_RECOVERY_RUNBOOK.md` §4. A restore that fails verification
is itself an incident: the backup chain is not trustworthy until explained.

### 4.5 Key compromise
`docs/KEY_MIGRATION_AND_RECOVERY_GUIDE.md`: rotate, re-encrypt the vault, export a
ledger checkpoint off-host, and record the version boundary in the incident log.

## 5. Evidence handling

* Keep an append-only incident file: timestamp, actor, action, artefact path.
* Hash any exported evidence file (`sha256sum`) and record the digest.
* Never edit the live security log to "clean it up". Copy, then rotate.
* Retention of incident evidence is a legal decision (`TBD`).

## 6. Notification

| Audience | Trigger | Owner | Deadline |
|---|---|---|---|
| Application owner | any S1/S2 | incident commander | immediately |
| Data owner / accountant | any financial integrity event | incident commander | same day |
| Affected tenants | confirmed exposure of their data | data owner | `TBD` (legal input) |
| Regulator (ANPDP) | personal-data breach | legal contact | `TBD` — **do not invent a deadline**; it is a legal determination |

## 7. After the incident

1. Confirm the service is healthy: `/healthz`, one login, one invoice read, audit
   chain verifies.
2. Write the post-incident review **within five working days**: timeline, root
   cause, what made it worse, and the test that will catch it next time.
3. Add that test to the suite before closing the incident — an incident without a
   regression test is a promise, not a fix.
4. Update `docs/RISK_REGISTER.md` with the new row and its honest likelihood.

## 8. What this plan does not have

* No tested contact list, no on-call rota, no escalation timings.
* No alert delivery has ever been demonstrated for this system (see
  `docs/PRODUCTION_READINESS_AND_GO_NO_GO.md`).
* No legal deadlines are stated, because stating one without legal input would be
  inventing a compliance requirement.

Until §2 and §6 are filled by a named owner and one drill has been run end to end,
this is a template.
