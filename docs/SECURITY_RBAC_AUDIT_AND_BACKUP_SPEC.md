# Security, RBAC, Audit Trail, and Backup/Restore Specification (Phase 5)

## 1. Server-Side RBAC & Segregation of Duties Matrix (`Verified`)

All permissions are enforced server-side in `dz_erp/procurement_stock.py`, `dz_erp/security_audit.py`, and `dz_erp/api.py` (never relying on UI state):

| Operation | `clerk` | `warehouse` | `approver` | `accountant` | `auditor` | `admin` | Additional Constraint |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| Upload Document / Edit OCR Draft | Yes | No | Yes | Yes | No | Yes | Same `company_id`, `is_active = 1` |
| Create / Validate Purchase Order | Yes | No | Yes | Yes | No | Yes | Same `company_id`, `is_active = 1` |
| Validate Goods Receipt / Stock Move | No | Yes | No | No | No | Yes | Exclusive trigger for `stock_moves` |
| Create / Submit Supplier Invoice | Yes | No | Yes | Yes | No | Yes | Requires `Validated` state to submit |
| Approve / Reject Supplier Invoice | No | No | Yes | Yes | No | Yes | **Segregation of Duties:** `approver_user_id != created_by_id` |
| Post Invoice / Credit Note Reversal | No | No | No | Yes | No | Yes | Requires `Approved` state + matching `approved_payload_sha256` + `Idempotency-Key` |
| Read Invoices & Vault Files | Yes | No | Yes | Yes | Yes | Yes | Strict tenant check (`user.company_id == record.company_id`) |

---

## 2. Multi-Company Tenant Isolation (`Verified`)

1. **Relational Queries:** Every table containing tenant data (`accounts`, `account_groups`, `partners`, `products`, `source_documents`, `purchase_orders`, `goods_receipts`, `stock_moves`, `stock_quants`, `supplier_invoices`, `approval_requests`, `journal_entries`, `journal_entry_lines`, `idempotency_keys`, `audit_events`) carries a `NOT NULL` `company_id` foreign key.
2. **API Header & Session Verification (`dz_erp/api.py`):** Every HTTP request authenticates `X-User-Id` and `X-Company-Id`, verifying that the user exists, has `is_active == 1`, and belongs to `X-Company-Id` (`401 Unauthorized` for missing/revoked credentials; `403 Forbidden` for cross-company attempts).
3. **Private Document Vault Isolation (`read_source_document_file_secure`):** Vault reads verify `source_documents.company_id == user.company_id` before decrypting the underlying `.enc` blob.

---

## 3. Tamper-Evident, Trigger-Protected Audit Trail (`dz_erp/database.py`)

1. **Append-Only Database Triggers:**
   - `trg_audit_no_update`: Blocks any SQL `UPDATE` on `audit_events` (`RAISE(ABORT, 'AUDIT_TAMPER_VIOLATION: audit_events is append-only; UPDATE is prohibited.')`).
   - `trg_audit_no_delete`: Blocks any SQL `DELETE` on `audit_events` (`RAISE(ABORT, 'AUDIT_TAMPER_VIOLATION: audit_events is append-only; DELETE is prohibited.')`).
2. **Cryptographic Hash Chain (`HMAC-SHA256`):**
   - Each event computes `event_hash = HMAC_SHA256(secret, "{company_id}|{occurred_at}|{actor_user_id}|{action_type}|{target_model}|{target_id}|{before_json}|{after_json}|{prev_hash}")`, chaining from the previous event's `event_hash` (genesis `prev_hash = "0" * 64`).
   - `db.verify_audit_chain(company_id)` recomputes every event's HMAC and verifies unbroken chain continuity from genesis to tip.

---

## 4. Secret & Algerian PII Log Redaction (`StructuredRedactingLogger`)

`dz_erp/security_audit.py` automatically scrubs sensitive keys and Algerian PII patterns before emitting structured JSON logs:
- **Sensitive Keys Redacted (`[REDACTED]`):** `password`, `secret`, `token`, `api_token`, `Authorization`, `encryption_key`, `hmac_secret`, `bank_rib`, `raw_text`.
- **Regex Value Patterns Redacted:**
  - Bearer JWT/API tokens $\rightarrow$ `[REDACTED_TOKEN]`
  - Algerian IBAN (`\bDZ[0-9]{2}[0-9A-Z]{16,24}\b`) $\rightarrow$ `[REDACTED_DZ_IBAN]`
  - 18-digit Algerian National ID / NIN (`\b[0-9]{18}\b`) $\rightarrow$ `[REDACTED_NIN]`
  - 20-digit Bank RIB (`\b[0-9]{20}\b`) $\rightarrow$ `[REDACTED_RIB]`

---

## 5. Encrypted Backup & Isolated Restore Verification (`create_encrypted_backup`, `restore_and_verify_backup`)

1. **Consistent Snapshot & Encryption (`create_encrypted_backup`):**
   - Uses SQLite's online backup API (`src_conn.backup(dst_conn)`) to capture a consistent snapshot (`erp_snapshot.sqlite3`).
   - Computes SHA-256 digests of the database snapshot and every `.enc` file in `PrivateDocumentVault`, writing `manifest.json`.
   - Packages `erp_snapshot.sqlite3`, `manifest.json`, and `vault/` into a `.tar.gz` stream and encrypts the entire archive with `Fernet` (`AES-128-CBC` + `HMAC-SHA256`).
2. **Isolated Restore & Verification (`restore_and_verify_backup`):**
   - Decrypts and extracts the archive into an isolated directory (checking all tar members against path traversal).
   - Verifies `erp_snapshot.sqlite3` and every vault `.enc` file against `manifest.json` SHA-256 hashes.
   - Verifies that the restored General Ledger trial balance is in exact equilibrium ($\sum \text{Debit} = \sum \text{Credit}$) and that `verify_audit_chain` passes for every company.
   - **Data Protection Note:** While technical security controls (encryption at rest, RBAC, tenant isolation, PII log redaction) are implemented and verified, formal legal compliance under **Algerian Law 18-07** (including ANPDP declaration/authorization for personal data processing) requires review by qualified Algerian legal counsel.
