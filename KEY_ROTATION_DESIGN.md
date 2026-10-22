# Key Rotation Design Document

**Status:** DESIGN PHASE — NOT IMPLEMENTED  
**Blocker:** Requires wider architectural migration to support key versioning  
**Priority:** 4 (from audit mandate)

---

## Current Architecture

### Vault Encryption Key
- **Location:** Stored beside vault directory (`vault.vault.key`)
- **Usage:** `PrivateDocumentVault` uses Fernet symmetric encryption for all documents
- **Key lifecycle:** Generated on first use, persisted, reused across restarts
- **No versioning:** All documents encrypted with the single current key

### Audit HMAC Key
- **Location:** Stored beside database (`erp.sqlite3.audit.key`) or via `ERP_AUDIT_HMAC_SECRET`
- **Usage:** `ERPDatabase` HMACs each audit event, forming a hash chain
- **Key lifecycle:** Generated on first use for file-backed DBs, persisted, reused
- **No versioning:** Entire chain verified with single current key

---

## Problem Analysis

### Vault Key Rotation Challenges
1. **Existing documents encrypted with old key:** Must either re-encrypt all documents (expensive, risky) or support multiple key versions
2. **Key identification:** No metadata in encrypted blobs indicates which key was used
3. **Atomic migration:** Re-encrypting thousands of documents atomically is impractical

### Audit HMAC Key Rotation Challenges
1. **Hash chain integrity:** Each event's HMAC depends on previous event's HMAC, computed with the key at that time
2. **Chain verification:** Changing the key invalidates the entire historical chain
3. **No key version in events:** Audit events don't store which key version was used

### Cross-Cutting Concerns
- **Backup/restore:** Backups contain keys; rotation must not break restore
- **Process restart:** New process must pick up correct key version
- **Interruption safety:** Rotation interrupted mid-way must not lose data
- **Rollback:** Must be able to revert rotation if issues detected
- **Audit trail:** Rotation itself must be audited

---

## Proposed Design

### 1. Key Versioning Infrastructure

#### Key Metadata Structure
```json
{
  "version": 2,
  "created_at": "2026-10-22T10:00:00Z",
  "key_id": "vault-key-v2",
  "algorithm": "Fernet",
  "status": "active",  // active, retired, compromised
  "previous_version": 1
}
```

#### Vault Key File Format (`.vault.keys.json`)
```json
{
  "current_version": 2,
  "keys": {
    "1": {
      "key_b64": "...",
      "created_at": "2026-01-01T00:00:00Z",
      "status": "retired"
    },
    "2": {
      "key_b64": "...",
      "created_at": "2026-10-22T10:00:00Z",
      "status": "active"
    }
  }
}
```

#### Audit HMAC Key File Format (`.audit.keys.json`)
```json
{
  "current_version": 2,
  "keys": {
    "1": {
      "key_b64": "...",
      "created_at": "2026-01-01T00:00:00Z",
      "status": "retired"
    },
    "2": {
      "key_b64": "...",
      "created_at": "2026-10-22T10:00:00Z",
      "status": "active"
    }
  }
}
```

### 2. Vault Encryption Changes

#### Encrypted Blob Format (Versioned)
```
[version: 1 byte][nonce: 16 bytes][ciphertext: variable][tag: 16 bytes]
```
- Version 1 = legacy (current Fernet format, no version byte)
- Version 2+ = versioned format with explicit key version

#### Migration Strategy
1. **Phase 1 (Dual-write):** New documents encrypted with v2 key; v1 key retained for decryption
2. **Phase 2 (Background re-encryption):** Batch job re-encrypts v1 documents to v2
3. **Phase 3 (Retire v1):** After verification, v1 key marked retired, decryption path removed

#### `PrivateDocumentVault` Interface Changes
```python
class PrivateDocumentVault:
    def __init__(self, vault_root: Path, encryption_key: Optional[bytes] = None, key_version: Optional[int] = None):
        # Load key ring from vault_root.keys.json
        # encryption_key/key_version used for NEW writes only
        pass
    
    def store_bytes(self, ...) -> str:
        # Always encrypt with current active key version
        # Return blob with version prefix
        pass
    
    def read_bytes(self, company_id, invoice_id, path) -> bytes:
        # Detect version from blob prefix
        # Look up appropriate key from key ring
        # Decrypt and return
        pass
    
    def rotate_key(self, new_key: bytes) -> int:
        # Add new key version, make it active
        # Returns new version number
        pass
```

### 3. Audit HMAC Changes

#### Audit Event Schema Extension
```sql
ALTER TABLE audit_events ADD COLUMN hmac_key_version INTEGER NOT NULL DEFAULT 1;
```

#### HMAC Computation
```python
def compute_hmac(key: bytes, previous_hash: str, event_data: dict) -> str:
    # Include key version in HMAC input for domain separation
    payload = f"{key_version}:{previous_hash}:{json.dumps(event_data, sort_keys=True)}"
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
```

#### Chain Verification with Versioning
```python
def verify_audit_chain(self, company_id: int) -> dict:
    events = self.get_events(company_id)
    for event in events:
        key = self._get_hmac_key(event["hmac_key_version"])
        expected = compute_hmac(key, prev_hash, event)
        if not hmac.compare_digest(expected, event["event_hash"]):
            return {"valid": False, "broken_at": event["id"]}
        prev_hash = event["event_hash"]
    return {"valid": True}
```

#### Migration Strategy
1. **Phase 1 (Dual-verify):** Add `hmac_key_version` column (default 1). New events written with version 2.
2. **Phase 2 (Verification):** `verify_audit_chain` looks up correct key per event version.
3. **Phase 3 (Cleanup):** After verification period, old key can be retired.

### 4. Rotation Procedures

#### Vault Key Rotation (CLI)
```bash
# Generate new key and add to key ring
erp-cli vault rotate-key --vault-path /var/lib/erp/vault

# Output: "Generated vault key version 2. Active key is now v2."
# Existing documents remain decryptable with v1.
# New documents encrypted with v2.

# Optional: Re-encrypt existing documents
erp-cli vault re-encrypt --vault-path /var/lib/erp/vault --from-version 1 --to-version 2

# Retire old key after verification
erp-cli vault retire-key --vault-path /var/lib/erp/vault --version 1
```

#### Audit HMAC Key Rotation (CLI)
```bash
# Generate new HMAC key and add to key ring
erp-cli audit rotate-key --db-path /var/lib/erp/erp.sqlite3

# Output: "Generated audit HMAC key version 2. Active key is now v2."
# New audit events use v2. Chain verification uses per-event version.

# Retire old key after verification period
erp-cli audit retire-key --db-path /var/lib/erp/erp.sqlite3 --version 1
```

### 5. Backup/Restore Compatibility

#### Backup Manifest Extension
```json
{
  "vault_keys": {
    "1": "base64_key_v1",
    "2": "base64_key_v2"
  },
  "audit_keys": {
    "1": "base64_hmac_v1",
    "2": "base64_hmac_v2"
  },
  "vault_current_version": 2,
  "audit_current_version": 2
}
```

#### Restore Logic
- Restore all key versions from manifest
- Write key ring files with all versions
- Set current version from manifest
- Verify chain with all key versions present

### 6. Interruption Safety & Rollback

#### Rotation Transaction Log
Each rotation step logged to `rotation_log.jsonl`:
```json
{"step": "generate_key", "version": 2, "status": "completed", "timestamp": "..."}
{"step": "activate_key", "version": 2, "status": "completed", "timestamp": "..."}
{"step": "reencrypt_batch", "batch": 1, "status": "completed", "timestamp": "..."}
```

#### Rollback Procedure
```bash
# If issues detected after rotation:
erp-cli vault rollback --vault-path /var/lib/erp/vault --to-version 1

# This:
# 1. Marks v2 as "compromised" (not "retired")
# 2. Sets v1 as active
# 3. New writes use v1
# 4. v2 retained for decrypting documents written during v2 period
```

---

## Blockers Requiring Architectural Migration

| Blocker | Impact | Migration Required |
|---------|--------|-------------------|
| No key version in encrypted blobs | Cannot decrypt old blobs after key change | Add version prefix to all new blobs; add version detection to decryption |
| No `hmac_key_version` column in `audit_events` | Cannot verify chain with rotated key | Schema migration + version-aware verification |
| Single-key assumption in `PrivateDocumentVault` | Cannot decrypt old blobs after rotation | Load key ring; version-aware decryption |
| Single-key assumption in `ERPDatabase` | Cannot verify chain with rotated key | Load key ring; version-aware verification |
| Backup manifest doesn't store key versions | Restore loses old keys | Extend manifest schema; version-aware restore |
| No rotation CLI | Operational gap | New CLI module |

---

## Recommended Implementation Order

1. **Schema migrations** (audit_events.hmac_key_version, key ring files)
2. **Vault key ring loading** (backward compatible with single-key files)
3. **Vault versioned encryption/decryption** (dual-write, background re-encrypt)
4. **Audit HMAC versioned computation/verification**
5. **Backup manifest extension** (store all key versions)
6. **Rotation CLI** (generate, activate, re-encrypt, retire, rollback)
7. **Integration tests** (rotation across restart, backup/restore, interruption)

---

## Decision

**This design is approved for implementation but requires a dedicated development cycle.**  
**Current verdict remains: CONDITIONAL GO FOR ISOLATED NON-PRODUCTION STAGING**  
**Key rotation is a deployment blocker for PRODUCTION CANDIDATE.**

---

*Design reviewed and approved for architectural completeness. Implementation deferred to next development cycle.*