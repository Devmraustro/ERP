# Log retention, access and deletion

Phase 42 — a review of finding **F-02**: the durable security log holds every
request plus authentication events, so someone has to decide how long it lives,
who may read it, and when it must be destroyed. This document separates what the
software does (implemented and verified) from what an organisation must decide
(a legal determination, `BLOCKED_EXTERNAL`).

---

## 1. What is actually written

Verified by probing a live app instance (independent script, previous pass) and
asserted in `tests/test_phase35_api_rate_limiting_and_request_logging.py`:

| Event | Fields |
|---|---|
| `HTTP_REQUEST` | `method`, `path` (query string stripped), `status_code`, `duration_ms`, `correlation_id`, `client_ip` |
| `HTTP_REQUEST_FAILED` | `method`, `path`, `duration_ms`, `correlation_id` |
| `AUTH_LOGIN_REJECTED` / `SECURITY_AUTH_RATE_LIMITED` / `SECURITY_RATE_LIMITED` | `username` (auth events), `client_ip`, `method`, `path`, `code`, `detail`, `retry_after_seconds`, `correlation_id` |
| `AUTH_REQUEST_REJECTED` | `client_ip`, `path`, `method`, `code`, `detail` |
| `AUTH_TOKEN_ISSUED` / `AUTH_TOKEN_REVOKED` | `username` (issued only), `reason` (revoke only) — never the token |
| `RATE_LIMIT_BACKEND_UNAVAILABLE` (Phase 39) | `failure_mode`, `error_type`, `username`, `client_ip` |

**Never written:** passwords, presented passwords even when wrong, bearer tokens,
the `Authorization` header, request bodies, query strings, user-agent strings.
Asserted by tests (`test_no_password_or_token_is_ever_logged`,
`test_backend_failure_is_audited_without_the_error_text`) and by the independent
probe.

**Personal data that *is* written, deliberately:** the account name on
authentication events, and the client IP address on every request. Both exist so
that a credential-stuffing run is attributable and a refused attempt has a
subject. They are personal data; their retention is a determination, not a
constant.

## 2. Rotation and storage bounds (implemented)

| Control | Default | Variable |
|---|---|---|
| Single-file size before rotation | 100 MiB in code; **10 MiB in `ops/erp.env.example`** | `ERP_SECURITY_LOG_MAX_SIZE` |
| Rotated files retained | 10 (so ≤ ~11 × the size above: ~1.1 GiB at the code default, ~110 MiB as shipped) | `ERP_SECURITY_LOG_MAX_FILES` |
| Rotation mechanics | atomic `os.replace`, serialised by a per-path lock, applied on write | — |
| Write durability | one JSON object per line, appended under a per-path lock | — |
| File mode | created `0600` (`os.open(..., 0o600)`); rotated files inherit the directory's permissions | operator (`chown`/ACL on the log directory) |

So the log is **bounded on disk** and **never truncated in place**. What it is
not is bounded in *time*: the oldest retained file is whatever fits inside the
size × count budget, not "N days". On a quiet system the file can live for
years; on a busy one it may rotate daily.

## 3. Access controls (partly implemented)

* The file is created with mode `0600` by `dz_erp/security_audit.py` itself;
  the compose deployment mounts `/var/log/erp` as a volume owned by the service
  account, so the log is not world-readable even before hardening.
* No API endpoint serves the log. There is no download, search or export path in
  the application.
* Reading it therefore requires host access — which is the correct boundary, and
  also the reason the deployment guide requires the host be treated as
  sensitive.

**Not implemented:** role-based access to log data inside the application, an
audit trail of *who read the log*, or shipping to a central store. Those are
operator/platform work; claiming otherwise would be false.

## 4. Retention decision (BLOCKED_EXTERNAL)

The retention period cannot be chosen by this repository. It depends on:

1. the lawful basis for processing (security logging is commonly
   legitimate-interest or legal-obligation based, but that is a legal call);
2. statutory retention duties for accounting and tax evidence in Algeria, which
   apply to the *ledger* and its audit trail rather than to HTTP access logs;
3. the data-subject rights regime under Law 18-07 / ANPDP (access, objection,
   erasure) and how a request is satisfied against a security log;
4. sector or engagement-specific requirements imposed by the operator's auditor
   or the DGI.

**Owner:** the data protection owner (DPO) together with counsel.
**Evidence that closes it:** a dated written schedule stating, per log class, the
retention period, the legal reference, the deletion trigger and the person
accountable. Until then, the matrix row `R-LAW` stays
`Blocked (external evidence required)` and this repository must not be described
as retention-compliant.

Interim operating rule (a default an operator may adopt, not a compliance claim):
keep 90 days of request logs and 12 months of authentication-failure events,
rotate as configured, and delete the oldest rotated file at each rotation.

## 5. Deletion (not implemented; procedure only)

There is no automatic deletion by age — only rotation with a fixed file count.
To delete by age an operator must schedule it on the host, for example:

```bash
# Example only; the period must come from §4, not from this document.
find /var/log/erp -name 'security*.jsonl*' -mtime +120 -delete
```

Any such deletion is irreversible and destroys evidence. It must be:
* recorded (who ran it, when, under which schedule);
* preceded by an export if the period overlaps an open incident;
* never applied to the audit chain in the database — that is a different store
  with its own immutability triggers and must not be touched.

## 6. Incident investigation

* **Correlation.** Every response carries `X-Request-Id`; the same value is in
  every log line for that request, so a user-reported reference maps to records
  without searching by personal data.
* **Order of access.** Read the audit chain in the database for financial and
  security decisions (`audit_events`), then the log for the request envelope
  (which request, from where, what status). The log says *who tried*; the audit
  chain says *what was decided*.
* **Proof of integrity.** The audit chain is HMAC-chained and verifiable
  (`verify_audit_chain`); the log file is not chained. A log line is therefore
  evidence of an attempt, not of a decision, and must be corroborated with the
  chain where a decision is at issue.
* **Preservation.** Before rotating or deleting anything during an incident, copy
  the affected files aside; the runbook in `docs/INCIDENT_RESPONSE_PLAN.md`
  governs severity and roles.

## 7. What would make this complete

| Gap | Owner | Closing evidence |
|---|---|---|
| Retention period and deletion schedule (§4) | DPO + counsel | Signed schedule with legal references |
| Automated age-based deletion | Engineering/Operations | A scheduled job with a dry-run report, tested on a copy |
| Off-host shipping and integrity protection of the log | Operations | Shipping configured, destination access-controlled, a restored sample proven |
| Access audit inside the application | Engineering | Any endpoint that exposes log data must itself be audited; currently none exists, so this is latent |
| Redaction review when new fields are added | Engineering | A test that fails when a new field is added to a log call site without a redaction decision (future work) |

## 8. Summary

The software is **credential-free and bounded on disk today**, verified. It is
**not retention-compliant, not access-audited and not shipped off-host**, and
none of those can be closed by code alone. That is why `R-LAW` and the `F-02`
row in `docs/REMAINING_BLOCKERS.md` remain open.
