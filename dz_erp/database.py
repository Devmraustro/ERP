# SPDX-License-Identifier: LGPL-3.0-only
"""Database engine, schema DDL, immutability triggers, and hash-chained audit log."""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .rotation_audit import RecordedOperation, RotationLedger
from .key_provider import FileKeyProvider
from .key_rotation import (
    LEGACY_KEY_VERSION,
    MIN_HMAC_KEY_BYTES,
    KeyConfigurationError,
    KeyRing,
    KeyVersionInUseError,
    KeyVersionUnavailableError,
    RotationJournal,
    rotation_lock,
)


class FinancialIntegrityError(RuntimeError):
    """Raised when an operation violates accounting or stock integrity constraints."""


class AuditTamperError(RuntimeError):
    """Raised when an attempt is made to mutate or delete audit records."""


class AuditKeyUnavailableError(KeyVersionUnavailableError):
    """Raised when the audit key is missing while audit events already exist.

    Distinct from ``AuditTamperError`` so an operator can tell "the key was
    lost" from "someone edited the chain" - the two need opposite responses.
    """


class TenantIsolationError(PermissionError):
    """Raised when a cross-company data or file access is attempted."""


class AuthorizationError(PermissionError):
    """Raised when a user lacks permission or violates Segregation of Duties."""


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    code                    TEXT NOT NULL UNIQUE,
    name                    TEXT NOT NULL,
    country_code            TEXT NOT NULL DEFAULT 'DZ',
    currency_code           TEXT NOT NULL DEFAULT 'DZD',
    chart_template          TEXT NOT NULL DEFAULT 'dz',
    parent_company_id       INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    nif                     TEXT,
    nis                     TEXT,
    ai                      TEXT,
    rc                      TEXT,
    default_receivable_code TEXT NOT NULL DEFAULT '411',
    default_payable_code    TEXT NOT NULL DEFAULT '401',
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id       INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    username        TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL CHECK (role IN (
                        'clerk', 'warehouse', 'approver', 'accountant',
                        'treasury', 'auditor', 'admin', 'ocr_worker'
                    )),
    is_active       INTEGER NOT NULL DEFAULT 1,
    api_token_hash  TEXT,
    password_hash   TEXT,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    password_updated_at TEXT
);

-- Server-issued, revocable, expiring bearer session tokens (see dz_erp/auth.py).
-- Only the SHA-256 digest of a token is persisted; the plaintext is shown once
-- at issuance time and is never recoverable from the database.
CREATE TABLE IF NOT EXISTS auth_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    token_prefix    TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    revoked_at      TEXT,
    revocation_reason TEXT,
    last_used_at    TEXT,
    issued_to_ip    TEXT,
    user_agent      TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_expiry ON auth_tokens(expires_at);

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    xml_id          TEXT NOT NULL,
    code            TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    name_fr         TEXT NOT NULL,
    name_ar         TEXT,
    account_type    TEXT NOT NULL,
    reconcile       INTEGER NOT NULL DEFAULT 0 CHECK (reconcile IN (0, 1)),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    is_custom_type  INTEGER NOT NULL DEFAULT 0 CHECK (is_custom_type IN (0, 1)),
    UNIQUE(company_id, code),
    UNIQUE(company_id, xml_id)
);

CREATE TABLE IF NOT EXISTS account_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    xml_id          TEXT NOT NULL,
    code_prefix     TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    name_fr         TEXT NOT NULL,
    name_ar         TEXT,
    UNIQUE(company_id, xml_id)
);

CREATE TABLE IF NOT EXISTS taxes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    xml_id              TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    name_fr             TEXT NOT NULL,
    type_tax_use        TEXT NOT NULL CHECK (type_tax_use IN ('sale', 'purchase')),
    rate_percent        TEXT NOT NULL,
    tax_account_code    TEXT,
    exemption_reason    TEXT NOT NULL DEFAULT 'NONE' CHECK (exemption_reason IN (
                            'NONE', 'EXEMPT', 'EXPORT', 'IMPORT', 'EU_FREE_TRADE', 'OUT_OF_SCOPE'
                        )),
    g50_base_tag        TEXT,
    g50_tax_tag         TEXT,
    valid_from          TEXT NOT NULL DEFAULT '2020-01-01',
    valid_to            TEXT,
    active              INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id, xml_id)
);

CREATE TABLE IF NOT EXISTS timbre_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    version_code    TEXT NOT NULL,
    valid_from      TEXT NOT NULL,
    valid_to        TEXT,
    min_amount_dzd  TEXT NOT NULL DEFAULT '5.00',
    max_amount_dzd  TEXT,
    threshold_dzd   TEXT NOT NULL DEFAULT '300.00',
    brackets_json   TEXT NOT NULL,
    UNIQUE(company_id, version_code)
);

CREATE TABLE IF NOT EXISTS partners (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    partner_type    TEXT NOT NULL CHECK (partner_type IN ('supplier', 'customer', 'both')),
    country_code    TEXT NOT NULL DEFAULT 'DZ',
    tax_regime      TEXT NOT NULL DEFAULT 'REEL' CHECK (tax_regime IN ('REEL', 'IFU', 'EXEMPT', 'FOREIGN')),
    nif             TEXT,
    nis             TEXT,
    ai              TEXT,
    rc              TEXT,
    nin             TEXT,
    wilaya_code     TEXT,
    bank_rib        TEXT,
    receivable_code TEXT,
    payable_code    TEXT,
    UNIQUE(company_id, nif)
);

CREATE TABLE IF NOT EXISTS products (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id              INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    sku                     TEXT NOT NULL,
    name                    TEXT NOT NULL,
    uom                     TEXT NOT NULL DEFAULT 'UNIT',
    is_storable             INTEGER NOT NULL DEFAULT 1 CHECK (is_storable IN (0, 1)),
    standard_cost_dzd       TEXT NOT NULL DEFAULT '0.000000',
    stock_account_code      TEXT NOT NULL DEFAULT '31',
    clearing_account_code   TEXT NOT NULL DEFAULT '380',
    expense_account_code    TEXT NOT NULL DEFAULT '600',
    income_account_code     TEXT NOT NULL DEFAULT '700',
    UNIQUE(company_id, sku)
);

CREATE TABLE IF NOT EXISTS source_documents (
    id                  TEXT PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id           INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    filename            TEXT NOT NULL,
    mime_type           TEXT NOT NULL,
    byte_size           INTEGER NOT NULL CHECK (byte_size > 0),
    page_count          INTEGER NOT NULL DEFAULT 1 CHECK (page_count >= 1),
    file_sha256         TEXT NOT NULL,
    vault_path          TEXT NOT NULL,
    encrypted_at_rest   INTEGER NOT NULL DEFAULT 1,
    security_status     TEXT NOT NULL CHECK (security_status IN ('PENDING', 'CLEAN', 'QUARANTINED')),
    security_findings   TEXT,
    workflow_state      TEXT NOT NULL CHECK (workflow_state IN (
                            'Uploaded', 'SecurityChecked', 'Processing', 'NeedsReview',
                            'Failed', 'Validated', 'PendingApproval', 'Approved',
                            'Posted', 'Rejected', 'DuplicateRejected'
                        )),
    rejection_reason    TEXT,
    uploaded_by_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    uploaded_at         TEXT NOT NULL,
    ocr_Engine          TEXT,
    ocr_model_version   TEXT,
    ocr_latency_ms      REAL,
    extraction_route    TEXT CHECK (extraction_route IS NULL
                            OR extraction_route IN ('standard_review', 'manual_transcription')),
    route_reason        TEXT,
    declared_language   TEXT,
    script_arabic_letters INTEGER,
    script_latin_letters  INTEGER
);

CREATE TABLE IF NOT EXISTS ocr_extracted_fields (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id              INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    document_id             TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
    page_number             INTEGER NOT NULL DEFAULT 1,
    line_index              INTEGER,
    field_name              TEXT NOT NULL,
    raw_text                TEXT NOT NULL,
    normalized_value        TEXT,
    -- Nullable on purpose: native PDF extraction measures no confidence, and
    -- storing a constant there would present fabricated certainty as data.
    confidence              REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    confidence_kind         TEXT NOT NULL DEFAULT 'engine_line'
                                CHECK (confidence_kind IN ('engine_line', 'not_measured', 'legacy_line_score')),
    confidence_scope        TEXT NOT NULL DEFAULT 'line'
                                CHECK (confidence_scope IN ('line', 'unavailable')),
    confidence_calibrated   INTEGER NOT NULL DEFAULT 0 CHECK (confidence_calibrated IN (0, 1)),
    bbox_json               TEXT NOT NULL,
    ocr_model_version       TEXT NOT NULL,
    human_override_value    TEXT,
    edited_by_id            INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    edited_at               TEXT,
    created_at              TEXT NOT NULL
);

-- Immutability trigger: raw OCR output must NEVER be overwritten when a human edits a draft
CREATE TRIGGER IF NOT EXISTS trg_ocr_field_preserve_raw
BEFORE UPDATE ON ocr_extracted_fields
FOR EACH ROW
WHEN OLD.raw_text != NEW.raw_text
  -- IS NOT, not !=, so that NULL (not measured) is compared correctly.
  OR OLD.confidence IS NOT NEW.confidence
  OR OLD.confidence_kind != NEW.confidence_kind
  OR OLD.confidence_scope != NEW.confidence_scope
  OR OLD.confidence_calibrated != NEW.confidence_calibrated
  OR OLD.ocr_model_version != NEW.ocr_model_version
BEGIN
    SELECT RAISE(ABORT, 'OCR_RAW_IMMUTABILITY_VIOLATION: raw_text, confidence, its provenance and ocr_model_version cannot be overwritten.');
END;

CREATE TABLE IF NOT EXISTS purchase_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id       INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    po_number       TEXT NOT NULL,
    supplier_id     INTEGER NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    currency_code   TEXT NOT NULL DEFAULT 'DZD',
    state           TEXT NOT NULL CHECK (state IN ('DRAFT', 'CONFIRMED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED')),
    created_by_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at      TEXT NOT NULL,
    UNIQUE(company_id, po_number)
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    po_id           INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    description     TEXT NOT NULL,
    qty_ordered     TEXT NOT NULL,
    qty_received    TEXT NOT NULL DEFAULT '0.000000',
    qty_invoiced    TEXT NOT NULL DEFAULT '0.000000',
    unit_price_dzd  TEXT NOT NULL,
    tax_rate        TEXT NOT NULL DEFAULT '19.0'
);

CREATE TABLE IF NOT EXISTS goods_receipts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id           INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    grn_number          TEXT NOT NULL,
    po_id               INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    warehouse_code      TEXT NOT NULL DEFAULT 'WH-MAIN',
    picking_type        TEXT NOT NULL DEFAULT 'IN' CHECK (picking_type IN ('IN', 'RETURN_OUT')),
    backorder_of_id     INTEGER REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    state               TEXT NOT NULL CHECK (state IN ('DRAFT', 'DONE', 'CANCELLED')),
    validated_by_id     INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    validated_at        TEXT,
    journal_entry_id    INTEGER,
    UNIQUE(company_id, grn_number)
);

CREATE TABLE IF NOT EXISTS goods_receipt_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    grn_id          INTEGER NOT NULL REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    po_line_id      INTEGER NOT NULL REFERENCES purchase_order_lines(id) ON DELETE RESTRICT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    qty_done        TEXT NOT NULL,
    unit_cost_dzd   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_quants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    warehouse_code  TEXT NOT NULL,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    qty_on_hand     TEXT NOT NULL DEFAULT '0.000000',
    UNIQUE(company_id, warehouse_code, product_id)
);

CREATE TABLE IF NOT EXISTS stock_moves (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    grn_id              INTEGER NOT NULL REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    warehouse_code      TEXT NOT NULL,
    direction           TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
    qty                 TEXT NOT NULL,
    unit_cost_dzd       TEXT NOT NULL,
    total_valuation_dzd TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_invoices (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id              INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id               INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    document_id             TEXT REFERENCES source_documents(id) ON DELETE RESTRICT,
    supplier_id             INTEGER NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    po_id                   INTEGER REFERENCES purchase_orders(id) ON DELETE RESTRICT,
    grn_id                  INTEGER REFERENCES goods_receipts(id) ON DELETE RESTRICT,
    invoice_type            TEXT NOT NULL DEFAULT 'IN_INVOICE' CHECK (invoice_type IN ('IN_INVOICE', 'IN_REFUND')),
    supplier_invoice_no     TEXT NOT NULL,
    normalized_invoice_no   TEXT NOT NULL,
    fiscal_year             INTEGER NOT NULL,
    invoice_date            TEXT NOT NULL,
    due_date                TEXT,
    currency_code           TEXT NOT NULL DEFAULT 'DZD',
    payment_method          TEXT NOT NULL DEFAULT 'BANK_TRANSFER' CHECK (payment_method IN ('CASH', 'BANK_TRANSFER', 'CHEQUE')),
    amount_ht_dzd           TEXT NOT NULL,
    amount_tva_dzd          TEXT NOT NULL,
    amount_timbre_dzd       TEXT NOT NULL DEFAULT '0.00',
    amount_ttc_dzd          TEXT NOT NULL,
    workflow_state          TEXT NOT NULL CHECK (workflow_state IN (
                                'Uploaded', 'SecurityChecked', 'Processing', 'NeedsReview',
                                'Failed', 'Validated', 'PendingApproval', 'Approved',
                                'Posted', 'Rejected', 'DuplicateRejected'
                            )),
    validation_errors_json  TEXT,
    payload_sha256          TEXT NOT NULL,
    approved_payload_sha256 TEXT,
    created_by_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approved_by_id          INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    approved_at             TEXT,
    posted_by_id            INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    posted_at               TEXT,
    journal_entry_id        INTEGER,
    reversal_of_invoice_id  INTEGER REFERENCES supplier_invoices(id) ON DELETE RESTRICT,
    created_at              TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_posted_supplier_invoice
ON supplier_invoices(company_id, supplier_id, normalized_invoice_no, fiscal_year)
WHERE workflow_state = 'Posted' AND invoice_type = 'IN_INVOICE';

CREATE TABLE IF NOT EXISTS supplier_invoice_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    invoice_id      INTEGER NOT NULL REFERENCES supplier_invoices(id) ON DELETE RESTRICT,
    po_line_id      INTEGER REFERENCES purchase_order_lines(id) ON DELETE RESTRICT,
    product_id      INTEGER REFERENCES products(id) ON DELETE RESTRICT,
    description     TEXT NOT NULL,
    qty             TEXT NOT NULL,
    unit_price_dzd  TEXT NOT NULL,
    discount_pct    TEXT NOT NULL DEFAULT '0.00',
    tax_rate        TEXT NOT NULL DEFAULT '19.0',
    line_ht_dzd     TEXT NOT NULL,
    line_tva_dzd    TEXT NOT NULL,
    line_ttc_dzd    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    target_model    TEXT NOT NULL,
    target_id       INTEGER NOT NULL,
    payload_sha256  TEXT NOT NULL,
    requested_by_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    requested_at    TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('PENDING', 'APPROVED', 'REJECTED', 'INVALIDATED')),
    decided_by_id   INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    decided_at      TEXT,
    reason          TEXT,
    CHECK (decision != 'APPROVED' OR (decided_by_id IS NOT NULL AND decided_by_id != requested_by_id))
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    branch_id           INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    entry_number        TEXT NOT NULL,
    journal_code        TEXT NOT NULL,
    entry_date          TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('DRAFT', 'POSTED')),
    source_model        TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    source_document_id  TEXT REFERENCES source_documents(id) ON DELETE RESTRICT,
    total_debit_dzd     TEXT NOT NULL,
    total_credit_dzd    TEXT NOT NULL,
    reversal_entry_id   INTEGER REFERENCES journal_entries(id) ON DELETE RESTRICT,
    posted_by_id        INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    posted_at           TEXT,
    CHECK (total_debit_dzd = total_credit_dzd),
    CHECK (state = 'POSTED' AND posted_by_id IS NOT NULL AND posted_at IS NOT NULL OR state = 'DRAFT'),
    UNIQUE(company_id, entry_number)
);

CREATE TABLE IF NOT EXISTS journal_entry_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    journal_entry_id    INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE RESTRICT,
    account_code        TEXT NOT NULL,
    partner_id          INTEGER REFERENCES partners(id) ON DELETE RESTRICT,
    label               TEXT NOT NULL,
    debit_dzd           TEXT NOT NULL DEFAULT '0.00',
    credit_dzd          TEXT NOT NULL DEFAULT '0.00',
    g50_tag             TEXT,
    reconciled          INTEGER NOT NULL DEFAULT 0 CHECK (reconciled IN (0, 1)),
    reconciliation_ref  TEXT
);

-- Immutability triggers on posted journal entries
CREATE TRIGGER IF NOT EXISTS trg_je_immutable_delete
BEFORE DELETE ON journal_entries
FOR EACH ROW
WHEN OLD.state = 'POSTED'
BEGIN
    SELECT RAISE(ABORT, 'FINANCIAL_INTEGRITY_VIOLATION: Posted journal entry cannot be deleted; use a reversal entry.');
END;

CREATE TRIGGER IF NOT EXISTS trg_je_immutable_update
BEFORE UPDATE ON journal_entries
FOR EACH ROW
WHEN OLD.state = 'POSTED'
  AND (
      NEW.state != 'POSTED'
      OR NEW.company_id != OLD.company_id
      OR NEW.entry_number != OLD.entry_number
      OR NEW.entry_date != OLD.entry_date
      OR NEW.total_debit_dzd != OLD.total_debit_dzd
      OR NEW.total_credit_dzd != OLD.total_credit_dzd
      OR NEW.source_model != OLD.source_model
      OR NEW.source_id != OLD.source_id
  )
BEGIN
    SELECT RAISE(ABORT, 'FINANCIAL_INTEGRITY_VIOLATION: Posted journal entry cannot be modified or unposted.');
END;

CREATE TRIGGER IF NOT EXISTS trg_jel_immutable_delete
BEFORE DELETE ON journal_entry_lines
FOR EACH ROW
WHEN (SELECT state FROM journal_entries WHERE id = OLD.journal_entry_id) = 'POSTED'
BEGIN
    SELECT RAISE(ABORT, 'FINANCIAL_INTEGRITY_VIOLATION: Posted journal entry lines cannot be deleted.');
END;

CREATE TRIGGER IF NOT EXISTS trg_jel_immutable_update
BEFORE UPDATE ON journal_entry_lines
FOR EACH ROW
WHEN (SELECT state FROM journal_entries WHERE id = OLD.journal_entry_id) = 'POSTED'
  AND (
      NEW.account_code != OLD.account_code
      OR NEW.debit_dzd != OLD.debit_dzd
      OR NEW.credit_dzd != OLD.credit_dzd
      OR NEW.company_id != OLD.company_id
      OR NEW.journal_entry_id != OLD.journal_entry_id
  )
BEGIN
    SELECT RAISE(ABORT, 'FINANCIAL_INTEGRITY_VIOLATION: Posted journal entry line amounts and accounts are immutable.');
END;

CREATE TABLE IF NOT EXISTS idempotency_records (
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    idempotency_key     TEXT NOT NULL,
    operation           TEXT NOT NULL,
    request_sha256      TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED')),
    response_json       TEXT,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (company_id, idempotency_key, operation)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    occurred_at         TEXT NOT NULL,
    actor_user_id       INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    action_type         TEXT NOT NULL,
    target_model        TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    correlation_id      TEXT,
    before_json         TEXT,
    after_json          TEXT NOT NULL,
    -- Which audit key version signed this row. Added with a DEFAULT of 1 so
    -- every pre-existing row keeps the verifiable meaning it already had: it
    -- was signed with the only key that existed, which is now version 1.
    hmac_key_version    INTEGER NOT NULL DEFAULT 1,
    prev_hash           TEXT NOT NULL,
    event_hash          TEXT NOT NULL
);

-- Append-only triggers on audit_events
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_TAMPER_VIOLATION: audit_events is append-only; UPDATE is prohibited.');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_TAMPER_VIOLATION: audit_events is append-only; DELETE is prohibited.');
END;
"""


# The only secret that ships in the source tree.  It is public by construction,
# so it may only ever be used by an ephemeral database whose audit trail nobody
# will ever try to trust.  See _resolve_audit_secret.
DEFAULT_AUDIT_HMAC_SECRET = b"dz-erp-default-test-hmac-key-change-in-prod"

# Environment variable that supplies the audit HMAC key in a real deployment.
AUDIT_KEY_ENV = "ERP_AUDIT_HMAC_SECRET"

# Suffix of the per-database key file created beside a file-backed database.
# This file holds the *version 1* (legacy) HMAC key and remains the bootstrap
# key of a database that has never been rotated.
AUDIT_KEY_SUFFIX = ".audit.key"

# Version registry, created beside the database by the first rotation. Once it
# exists it is authoritative for every version, exactly like the vault ring.
AUDIT_KEYRING_SUFFIX = ".audit.keys.json"

# Rotation journal and exclusive rotation lock, both beside the database.
AUDIT_ROTATION_JOURNAL_SUFFIX = ".audit.rotation.jsonl"


def _file_backed_db_has_audit_events(db_path: str) -> bool:
    """True when ``db_path`` is an existing database that already holds audit rows.

    Read-only and failure-tolerant: any error means "cannot tell", which is
    treated as "no events" so a genuinely fresh database still initialises.
    """
    if not Path(db_path).exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return False
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_events'"
        ).fetchone()
        if not has_table:
            return False
        return conn.execute("SELECT 1 FROM audit_events LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _resolve_audit_secret(db_path: str, explicit: Optional[bytes]) -> bytes:
    """Resolve the HMAC key that chains ``audit_events``.

    Precedence, most to least trusted:

    1. A secret passed explicitly by the caller.
    2. ``ERP_AUDIT_HMAC_SECRET`` from the environment.
    3. A key file beside a *file-backed* database, generated on first use and
       created with mode ``0600`` so it stays readable only by the database
       owner.  Reusing the file (rather than deriving the key from the database
       contents, which would be circular) is what lets ``verify_audit_chain``
       still succeed after a restart.
    4. The documented test default, for an in-memory database only.

    A file-backed database must never reach case 4.  The default is published in
    the source tree, so falling back to it would let anyone who has read this
    repository forge an arbitrary audit chain - silently, with no error and no
    warning.  Generating a key on first use makes the secure path the default
    path instead of a deployment step someone can forget.
    """
    if explicit:
        return explicit
    from_env = os.environ.get("ERP_AUDIT_HMAC_SECRET")
    if from_env:
        return from_env.encode("utf-8")

    if db_path != ":memory:":
        key_path = Path(db_path).with_name(Path(db_path).name + AUDIT_KEY_SUFFIX)
        if key_path.exists():
            return FileKeyProvider(key_path).read()

        # The key file is gone but the database may already hold audit events.
        # Minting a replacement here is the silent-data-integrity case: every
        # existing chain would then read as EVENT_HASH_TAMPERED, which blames
        # an attacker for what is really a lost key and sends the operator
        # looking for an intrusion that never happened.  Fail loudly, and only
        # generate when the database is genuinely fresh.
        if _file_backed_db_has_audit_events(db_path):
            raise AuditKeyUnavailableError(
                f"The audit key file {key_path} is missing but {db_path} already "
                "contains audit events. Refusing to generate a replacement key: "
                "doing so would make every existing chain fail verification and "
                "report tampering. Restore the key file, or set "
                "ERP_AUDIT_HMAC_SECRET."
            )
        secret = base64.urlsafe_b64encode(os.urandom(32))
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # O_EXCL: if two processes race to initialise the same database, the
            # loser adopts the winner's key rather than replacing it.
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return FileKeyProvider(key_path).read()
        with os.fdopen(fd, "wb") as handle:
            handle.write(secret)
        return secret

    return DEFAULT_AUDIT_HMAC_SECRET


def _resolve_audit_keyring(db_path: str, explicit: Optional[bytes]) -> tuple:
    """Resolve the audit key version registry for ``db_path``.

    A persisted registry is authoritative. Until one exists the resolved single
    secret is version 1, which is exactly what every existing database means.

    A caller-supplied secret or environment variable that contradicts a
    persisted registry is a configuration error, not a precedence question:
    silently preferring either would sign new events with a key the verifier
    does not know about.
    """
    if db_path == ":memory:":
        # No durable registry is possible, and the documented test default is
        # only reachable here. Rotation is refused for in-memory databases.
        return KeyRing.bootstrap("audit", _resolve_audit_secret(db_path, explicit)), None

    ring_path = Path(db_path).with_name(Path(db_path).name + AUDIT_KEYRING_SUFFIX)
    if ring_path.exists():
        ring = KeyRing.load(ring_path, "audit")
        active = ring.active_material
        if explicit is not None and bytes(explicit) != active:
            raise KeyConfigurationError(
                f"The explicit audit key disagrees with the active key (version {ring.active_version}) "
                f"in {ring_path}. Refusing to guess which key is authoritative."
            )
        from_env = os.environ.get(AUDIT_KEY_ENV)
        if from_env and from_env.encode("utf-8") != active:
            raise KeyConfigurationError(
                f"ERP_AUDIT_HMAC_SECRET disagrees with the active key (version {ring.active_version}) "
                f"in {ring_path}. Once a key registry exists it is authoritative; update or remove the "
                "environment variable."
            )
        return ring, ring_path

    # No registry yet: the legacy single key is version 1, and the path records
    # where a registry will be written if this database is ever rotated.
    return KeyRing.bootstrap("audit", _resolve_audit_secret(db_path, explicit)), ring_path


class ERPDatabase:
    """Thread-safe transactional database manager with audit hash-chaining."""

    def __init__(
        self,
        db_path: str = ":memory:",
        hmac_secret: Optional[bytes] = None,
        *,
        read_only: bool = False,
    ) -> None:
        self.db_path = db_path
        self._read_only = read_only
        if read_only and db_path == ":memory:":
            raise ValueError("Read-only mode requires an existing file-backed database.")
        if read_only and not Path(db_path).is_file():
            raise FileNotFoundError(f"Read-only database does not exist: {db_path}")
        if read_only:
            legacy_key_path = Path(db_path).with_name(Path(db_path).name + AUDIT_KEY_SUFFIX)
            keyring_path = Path(db_path).with_name(Path(db_path).name + AUDIT_KEYRING_SUFFIX)
            has_key_source = bool(
                hmac_secret
                or os.environ.get(AUDIT_KEY_ENV)
                or legacy_key_path.is_file()
                or keyring_path.is_file()
            )
            if not has_key_source:
                raise AuditKeyUnavailableError(
                    f"Read-only inspection of {db_path} found no audit-key source "
                    f"({legacy_key_path.name}, {keyring_path.name}, or {AUDIT_KEY_ENV}). "
                    "Refusing to generate replacement key material."
                )
        self._audit_keyring, self._keyring_path = _resolve_audit_keyring(db_path, hmac_secret)
        # Retained as the *active* key because existing code and tests read it,
        # but it is now one entry in a versioned registry rather than the only
        # key the chain can be verified with.
        self._hmac_secret = self._audit_keyring.active_material
        self._lock = threading.RLock()
        self._persistent_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._persistent_conn = self._create_connection()
            self._init_schema(self._persistent_conn)
        elif read_only:
            # Inspection paths must not auto-migrate a database, create missing
            # schema objects, or switch its journal mode. The read-only URI is
            # enforced by SQLite itself, not by a caller convention.
            pass
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            # sqlite3.Connection's context manager commits/rolls back but does
            # not close the connection. Close it explicitly so WAL checkpointing
            # is completed here, not later during unrelated read-only inspection
            # or garbage collection.
            conn = self._create_connection()
            try:
                with conn:
                    self._init_schema(conn)
            finally:
                conn.close()
        # Fail closed at start-up: if any stored event needs a key version this
        # process does not have, the service must not come up pretending the
        # chain is merely "unverified".
        self.validate_audit_key_configuration()

    # ------------------------------------------------------------------
    # Audit key configuration
    # ------------------------------------------------------------------
    @property
    def audit_keyring(self) -> KeyRing:
        return self._audit_keyring

    def _ensure_writable(self, operation: str) -> None:
        """Prevent non-SQL side effects when the database is opened for inspection."""
        if self._read_only:
            raise PermissionError(
                f"ERPDatabase is in read-only inspection mode; refusing to {operation}."
            )

    @property
    def audit_keyring_path(self) -> Optional[Path]:
        return self._keyring_path

    @property
    def active_audit_key_version(self) -> int:
        return self._audit_keyring.active_version

    def audit_events_by_key_version(self) -> Dict[int, int]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT hmac_key_version AS v, COUNT(*) AS n FROM audit_events GROUP BY hmac_key_version"
            ).fetchall()
        return {int(row["v"]): int(row["n"]) for row in rows}

    def validate_audit_key_configuration(self) -> Dict[str, Any]:
        """Refuse to run when a stored event's signing key is unavailable."""
        by_version = self.audit_events_by_key_version()
        missing = sorted(v for v in by_version if not self._audit_keyring.has_version(v))
        if missing:
            raise AuditKeyUnavailableError(
                f"{sum(by_version[v] for v in missing)} audit event(s) in {self.db_path} were signed with "
                f"key version(s) {missing}, which are absent from the key registry "
                f"{self._keyring_path}. Restore those key versions before starting the service; "
                "verifying with a different key would misreport an intact chain as tampered."
            )
        return {
            "active_version": self._audit_keyring.active_version,
            "versions": list(self._audit_keyring.versions),
            "events_by_version": by_version,
        }

    def _create_connection(self) -> sqlite3.Connection:
        if self._read_only:
            uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=30.0,
                check_same_thread=False,
                isolation_level=None,
            )
        else:
            conn = sqlite3.connect(
                self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None
            )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:" and not self._read_only:
            conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_SQL)
        self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Idempotently upgrade pre-existing databases to the authenticated schema.

        `CREATE TABLE IF NOT EXISTS` cannot add columns to a table that already
        exists, so databases created before authentication was introduced are
        brought forward here. Safe to run on every startup.
        """
        user_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if user_cols and "password_hash" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if user_cols and "failed_login_count" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0")
        if user_cols and "locked_until" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN locked_until TEXT")
        if user_cols and "password_updated_at" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_updated_at TEXT")
        # Legacy column retained for schema compatibility; superseded by auth_tokens.
        if user_cols and "api_token_hash" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN api_token_hash TEXT")

        # Audit key versioning (Phase 2 of the key-rotation work). Every row
        # that exists before this migration was signed with the only key there
        # was, so the DEFAULT of 1 is the factual backfill rather than a guess.
        # The migration is idempotent and never rewrites a stored hash: the old
        # events stay verifiable exactly as they are.
        audit_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()
        }
        if audit_cols and "hmac_key_version" not in audit_cols:
            conn.execute(
                "ALTER TABLE audit_events ADD COLUMN hmac_key_version INTEGER NOT NULL DEFAULT 1"
            )

        self._migrate_ocr_confidence(conn)
        self._migrate_document_routing(conn)

    def _migrate_document_routing(self, conn: sqlite3.Connection) -> None:
        """Add the review-routing columns to `source_documents`, leaving old rows NULL.

        Existing documents keep `NULL` rather than a default of `standard_review`:
        they were extracted before routing existed, and claiming they were routed
        normally would assert a decision that was never made. A reader that sees
        `NULL` knows to treat the route as unknown.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(source_documents)").fetchall()}
        if not cols:
            return
        additions = {
            "extraction_route": (
                "TEXT CHECK (extraction_route IS NULL OR extraction_route IN "
                "('standard_review', 'manual_transcription'))"
            ),
            "route_reason": "TEXT",
            "declared_language": "TEXT",
            "script_arabic_letters": "INTEGER",
            "script_latin_letters": "INTEGER",
        }
        for column, definition in additions.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE source_documents ADD COLUMN {column} {definition}")

    def _migrate_ocr_confidence(self, conn: sqlite3.Connection) -> None:
        """Bring `ocr_extracted_fields` forward to nullable, provenance-bearing confidence.

        SQLite cannot drop the old NOT NULL constraint with ALTER TABLE, so the
        table must be rebuilt. The complete rename/create/copy/drop/trigger sequence
        is deliberately one explicit transaction. Do not use executescript() here:
        sqlite3.executescript() commits a pending transaction before running its
        script, which would make a failed or interrupted rebuild non-atomic.

        Legacy native-PDF rows are marked not_measured and their fabricated
        confidence is cleared. OCR engine scores are retained as legacy_line_score;
        raw text, bounding boxes, model version and human overrides are copied.
        """
        cols = conn.execute("PRAGMA table_info(ocr_extracted_fields)").fetchall()
        if not cols:
            return
        by_name = {row["name"]: row for row in cols}
        already_migrated = (
            "confidence_kind" in by_name
            and "confidence_scope" in by_name
            and not by_name["confidence"]["notnull"]
        )
        if already_migrated:
            return

        # PRAGMA foreign_keys is a no-op inside a transaction, so refuse an
        # unexpected caller transaction rather than silently rebuilding without
        # the intended FK setting.
        if conn.in_transaction:
            raise sqlite3.OperationalError(
                "OCR confidence migration must start outside an active transaction."
            )
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TRIGGER IF EXISTS trg_ocr_field_preserve_raw")
            conn.execute("ALTER TABLE ocr_extracted_fields RENAME TO ocr_extracted_fields_legacy")
            conn.execute(
                """
                CREATE TABLE ocr_extracted_fields (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id              INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
                    document_id             TEXT NOT NULL REFERENCES source_documents(id) ON DELETE RESTRICT,
                    page_number             INTEGER NOT NULL DEFAULT 1,
                    line_index              INTEGER,
                    field_name              TEXT NOT NULL,
                    raw_text                TEXT NOT NULL,
                    normalized_value        TEXT,
                    confidence              REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
                    confidence_kind         TEXT NOT NULL DEFAULT 'engine_line'
                                                CHECK (confidence_kind IN ('engine_line', 'not_measured', 'legacy_line_score')),
                    confidence_scope        TEXT NOT NULL DEFAULT 'line'
                                                CHECK (confidence_scope IN ('line', 'unavailable')),
                    confidence_calibrated   INTEGER NOT NULL DEFAULT 0 CHECK (confidence_calibrated IN (0, 1)),
                    bbox_json               TEXT NOT NULL,
                    ocr_model_version       TEXT NOT NULL,
                    human_override_value    TEXT,
                    edited_by_id            INTEGER REFERENCES users(id) ON DELETE RESTRICT,
                    edited_at               TEXT,
                    created_at              TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ocr_extracted_fields (
                    id, company_id, document_id, page_number, line_index, field_name,
                    raw_text, normalized_value, confidence, confidence_kind, confidence_scope,
                    confidence_calibrated, bbox_json, ocr_model_version, human_override_value,
                    edited_by_id, edited_at, created_at
                )
                SELECT
                    l.id, l.company_id, l.document_id, l.page_number, l.line_index, l.field_name,
                    l.raw_text, l.normalized_value,
                    CASE WHEN d.ocr_engine = 'pdfplumber' THEN NULL ELSE l.confidence END,
                    CASE WHEN d.ocr_engine = 'pdfplumber' THEN 'not_measured' ELSE 'legacy_line_score' END,
                    CASE WHEN d.ocr_engine = 'pdfplumber' THEN 'unavailable' ELSE 'line' END,
                    0, l.bbox_json, l.ocr_model_version, l.human_override_value,
                    l.edited_by_id, l.edited_at, l.created_at
                FROM ocr_extracted_fields_legacy l
                LEFT JOIN source_documents d ON d.id = l.document_id
                """
            )
            # Foreign keys are disabled only to permit the rebuild. Validate the
            # replacement table explicitly before discarding the legacy copy; old
            # databases may contain orphans because their schema did not enforce
            # the same constraints. If any references are invalid, fail closed and
            # roll back to the original table rather than preserve silent orphans.
            violations = conn.execute(
                "PRAGMA foreign_key_check(ocr_extracted_fields)"
            ).fetchall()
            if violations:
                table, rowid, parent, foreign_key_id = violations[0]
                raise sqlite3.IntegrityError(
                    "OCR confidence migration would introduce "
                    f"{len(violations)} foreign-key violation(s); first violation: "
                    f"table={table}, rowid={rowid}, parent={parent}, fk={foreign_key_id}."
                )
            conn.execute("DROP TABLE ocr_extracted_fields_legacy")
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_ocr_field_preserve_raw
                BEFORE UPDATE ON ocr_extracted_fields
                FOR EACH ROW
                WHEN OLD.raw_text != NEW.raw_text
                  OR OLD.confidence IS NOT NEW.confidence
                  OR OLD.confidence_kind != NEW.confidence_kind
                  OR OLD.confidence_scope != NEW.confidence_scope
                  OR OLD.confidence_calibrated != NEW.confidence_calibrated
                  OR OLD.ocr_model_version != NEW.ocr_model_version
                BEGIN
                    SELECT RAISE(ABORT, 'OCR_RAW_IMMUTABILITY_VIOLATION: raw_text, confidence, its provenance and ocr_model_version cannot be overwritten.');
                END
                """
            )
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Execute an atomic IMMEDIATE transaction with automatic commit/rollback."""
        with self._lock:
            conn = self._persistent_conn if self._persistent_conn is not None else self._create_connection()
            in_tx = conn.in_transaction
            try:
                if not in_tx:
                    conn.execute("BEGIN;" if self._read_only else "BEGIN IMMEDIATE;")
                yield conn
                if not in_tx:
                    conn.execute("COMMIT;")
            except sqlite3.IntegrityError as exc:
                if not in_tx and conn.in_transaction:
                    conn.execute("ROLLBACK;")
                msg = str(exc)
                if "AUDIT_TAMPER_VIOLATION" in msg:
                    raise AuditTamperError(msg) from exc
                if "FINANCIAL_INTEGRITY_VIOLATION" in msg or "OCR_RAW_IMMUTABILITY_VIOLATION" in msg:
                    raise FinancialIntegrityError(msg) from exc
                raise
            except Exception:
                if not in_tx and conn.in_transaction:
                    conn.execute("ROLLBACK;")
                raise
            finally:
                if self._persistent_conn is None:
                    conn.close()

    def _compute_audit_hash(
        self,
        company_id: int,
        occurred_at: str,
        actor_user_id: Optional[int],
        action_type: str,
        target_model: str,
        target_id: str,
        before_json: Optional[str],
        after_json: str,
        prev_hash: str,
        key_version: Optional[int] = None,
    ) -> str:
        """HMAC one event with the key for *key_version*.

        The MAC input is byte-for-byte what it has always been, so events
        written before versioning existed verify unchanged. The version is a
        property of the *row* (persisted alongside the hash), not of the MAC
        input, which is what makes historical verification possible without
        rewriting - and therefore without re-signing - those events.
        """
        version = self._audit_keyring.active_version if key_version is None else int(key_version)
        try:
            material = self._audit_keyring.material_for(version)
        except KeyVersionUnavailableError as exc:
            raise AuditKeyUnavailableError(str(exc)) from exc
        payload = "|".join(
            [
                str(company_id),
                occurred_at,
                str(actor_user_id or ""),
                action_type,
                target_model,
                str(target_id),
                before_json or "",
                after_json,
                prev_hash,
            ]
        ).encode("utf-8")
        return hmac.new(material, payload, hashlib.sha256).hexdigest()

    def append_audit_event(
        self,
        conn: sqlite3.Connection,
        company_id: int,
        actor_user_id: Optional[int],
        action_type: str,
        target_model: str,
        target_id: str,
        after_state: Dict[str, Any],
        before_state: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> int:
        """Append a cryptographically hash-chained audit event inside the active transaction."""
        last_row = conn.execute(
            "SELECT event_hash FROM audit_events WHERE company_id = ? ORDER BY id DESC LIMIT 1",
            (company_id,),
        ).fetchone()
        prev_hash = last_row["event_hash"] if last_row else ("0" * 64)
        occurred_at = datetime.now(timezone.utc).isoformat()
        before_json = json.dumps(before_state, sort_keys=True, ensure_ascii=False) if before_state is not None else None
        after_json = json.dumps(after_state, sort_keys=True, ensure_ascii=False)
        key_version = self._audit_keyring.active_version
        event_hash = self._compute_audit_hash(
            company_id=company_id,
            occurred_at=occurred_at,
            actor_user_id=actor_user_id,
            action_type=action_type,
            target_model=target_model,
            target_id=str(target_id),
            before_json=before_json,
            after_json=after_json,
            prev_hash=prev_hash,
            key_version=key_version,
        )
        cur = conn.execute(
            """
            INSERT INTO audit_events (
                company_id, occurred_at, actor_user_id, action_type,
                target_model, target_id, correlation_id, before_json, after_json,
                hmac_key_version, prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                occurred_at,
                actor_user_id,
                action_type,
                target_model,
                str(target_id),
                correlation_id,
                before_json,
                after_json,
                key_version,
                prev_hash,
                event_hash,
            ),
        )
        return int(cur.lastrowid)

    def verify_audit_chain(self, company_id: int) -> Dict[str, Any]:
        """Verify chain continuity and per-event HMAC integrity for a company.

        Each event is checked with the key version recorded on its own row, so a
        chain that spans a rotation verifies end to end.

        A version whose key is missing produces ``AUDIT_KEY_UNAVAILABLE``, never
        ``valid: True``: the unverifiable events are counted and named, so a lost
        key cannot be reported as an intact chain, and an intact chain cannot be
        reported as tampering.
        """
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE company_id = ? ORDER BY id ASC",
                (company_id,),
            ).fetchall()
        expected_prev = "0" * 64
        missing_versions: set = set()
        events_by_version: Dict[str, int] = {}
        verified = 0
        for idx, row in enumerate(rows):
            version = (
                int(row["hmac_key_version"])
                if "hmac_key_version" in row.keys()
                else LEGACY_KEY_VERSION
            )
            events_by_version[str(version)] = events_by_version.get(str(version), 0) + 1

            # Chain linkage is structural and independent of key availability:
            # a deleted, reordered or spliced event is detectable even when a
            # key is missing, so this check runs first.
            if row["prev_hash"] != expected_prev:
                return {
                    "valid": False,
                    "checked_count": verified,
                    "broken_at_id": row["id"],
                    "reason": "PREV_HASH_MISMATCH",
                    "event_count": len(rows),
                    "key_versions": events_by_version,
                }
            try:
                recomputed = self._compute_audit_hash(
                    company_id=row["company_id"],
                    occurred_at=row["occurred_at"],
                    actor_user_id=row["actor_user_id"],
                    action_type=row["action_type"],
                    target_model=row["target_model"],
                    target_id=row["target_id"],
                    before_json=row["before_json"],
                    after_json=row["after_json"],
                    prev_hash=row["prev_hash"],
                    key_version=version,
                )
            except AuditKeyUnavailableError:
                # Cannot judge this event either way. Keep walking so the rest
                # of the chain is still checked, but never return valid=True.
                missing_versions.add(version)
                expected_prev = row["event_hash"]
                continue
            if not hmac.compare_digest(recomputed, row["event_hash"]):
                return {
                    "valid": False,
                    "checked_count": verified,
                    "broken_at_id": row["id"],
                    "reason": "EVENT_HASH_TAMPERED",
                    "event_count": len(rows),
                    "key_versions": events_by_version,
                }
            verified += 1
            expected_prev = row["event_hash"]

        if missing_versions:
            return {
                "valid": False,
                "reason": "AUDIT_KEY_UNAVAILABLE",
                "key_unavailable": True,
                "missing_key_versions": sorted(missing_versions),
                "verified_count": verified,
                "unverifiable_count": len(rows) - verified,
                "event_count": len(rows),
                "key_versions": events_by_version,
                "broken_at_id": None,
            }
        return {
            "valid": True,
            "checked_count": len(rows),
            "last_hash": expected_prev,
            "event_count": len(rows),
            "key_versions": events_by_version,
        }

    # ------------------------------------------------------------------
    # Audit key rotation
    # ------------------------------------------------------------------
    def verify_all_audit_chains(self) -> Dict[str, Any]:
        """Verify the chain of every company, with a single overall verdict."""
        with self.transaction() as conn:
            company_ids = [int(r["id"]) for r in conn.execute("SELECT id FROM companies").fetchall()]
        per_company = {cid: self.verify_audit_chain(cid) for cid in company_ids}
        key_unavailable = sorted(
            {v for r in per_company.values() if r.get("key_unavailable") for v in r["missing_key_versions"]}
        )
        return {
            "valid": all(r["valid"] for r in per_company.values()),
            "companies": per_company,
            "key_unavailable": bool(key_unavailable),
            "missing_key_versions": key_unavailable,
        }

    def _require_verifiable_chain(self, phase: str) -> Dict[str, Any]:
        """Refuse to change signing keys while the chain cannot be verified."""
        result = self.verify_all_audit_chains()
        if result["valid"]:
            return result
        broken = {
            cid: r.get("reason")
            for cid, r in result["companies"].items()
            if not r["valid"]
        }
        if result["key_unavailable"]:
            raise AuditKeyUnavailableError(
                f"Cannot {phase}: the audit chain needs key version(s) "
                f"{result['missing_key_versions']} that are not available. Restore them first."
            )
        raise AuditTamperError(
            f"Refusing to {phase}: the audit chain of company/companies {broken} does not verify "
            f"({sorted(set(broken.values()))}). A key change on top of an unverifiable chain would make the "
            "original damage impossible to reconstruct; investigate the chain first."
        )

    def _persist_audit_keyring(self, working: KeyRing) -> None:
        """Write the registry atomically, then adopt it in memory.

        The on-disk registry is updated before the running process switches
        keys, so a crash can never leave events signed with a key the registry
        does not know about.
        """
        self._ensure_writable("persist audit key material")
        if self._keyring_path is None:
            raise KeyConfigurationError(
                "This database has no durable key registry (in-memory databases cannot be rotated)."
            )
        working.save(self._keyring_path)
        self._audit_keyring = working
        self._hmac_secret = working.active_material

    def rotate_audit_key(
        self,
        new_key: Optional[bytes] = None,
        *,
        new_version: Optional[int] = None,
        ledger: Optional[RotationLedger] = None,
    ) -> Dict[str, Any]:
        """Activate a new audit signing key; historical events keep their key.

        Unlike the vault there is nothing to re-encrypt: a MAC cannot be
        recomputed without rewriting history, and rewriting history is exactly
        what an audit trail must never do. Rotation therefore means "sign new
        events with version N+1, and keep version N so every existing event
        still verifies". The only state change is one atomic registry write, so
        an interruption either happens (both keys retained, new events signed
        with the new key) or does not (nothing changes).
        """
        self._ensure_writable("rotate the audit key")
        if self._keyring_path is None:
            raise KeyConfigurationError(
                "In-memory databases have no durable key registry, so their audit key cannot be rotated."
            )
        if new_key is None:
            new_key = base64.urlsafe_b64encode(os.urandom(32))
        new_key = bytes(new_key)
        if len(new_key) < MIN_HMAC_KEY_BYTES:
            raise KeyConfigurationError(
                f"New audit key material is {len(new_key)} bytes; at least {MIN_HMAC_KEY_BYTES} are required."
            )

        journal = RotationJournal(
            Path(self.db_path).with_name(Path(self.db_path).name + AUDIT_ROTATION_JOURNAL_SUFFIX)
        )
        lock_path = Path(self.db_path).with_name(Path(self.db_path).name + ".audit.rotation.lock")
        # Recorded in the independent rotation ledger, NOT in audit_events: the
        # ledger has its own key, so losing or rotating the audit key cannot
        # erase the history of operations performed on it.
        ledger = ledger or RotationLedger.for_store(Path(self.db_path))
        with RecordedOperation(
            ledger,
            operation="AUDIT_KEY_ROTATION",
            store=f"audit:{self.db_path}",
            from_version=self._audit_keyring.active_version,
        ) as recorded, rotation_lock(lock_path):
            before = self._require_verifiable_chain("rotate the audit signing key")

            working = KeyRing.from_dict(self._audit_keyring.to_dict())
            previous_version = working.active_version
            resume_version = working.version_of_material(new_key)
            if resume_version is not None:
                target_version = resume_version
                resume = True
                if working.active_version != target_version:
                    working.set_active(target_version)
            else:
                target_version = working.add_version(new_key, version=new_version, min_bytes=MIN_HMAC_KEY_BYTES)
                resume = False

            recorded.note_target_version(target_version)
            journal.record(
                event="rotation_started",
                previous_version=previous_version,
                target_version=target_version,
                resumed=resume,
            )
            self._persist_audit_keyring(working)
            after = self.verify_all_audit_chains()
            journal.record(
                event="rotation_finished",
                target_version=target_version,
                chain_valid=after["valid"],
            )

            # The chain must verify after the change for the rotation to count
            # as successful; an unverified outcome is recorded as such.
            status = "COMPLETED_VERIFIED" if after["valid"] else "COMPLETED_UNVERIFIED"
            event_counts = self.audit_events_by_key_version()
            recorded.record_summary(
                counts={
                    "events_before": sum(before["companies"][c]["event_count"] for c in before["companies"]),
                    "events_after": sum(event_counts.values()),
                    "events_rekeyed": 0,
                },
                integrity={
                    "verified": after["valid"],
                    "checked": sum(
                        after["companies"][c]["checked_count"] for c in after["companies"]
                    ),
                    "failures": 0 if after["valid"] else 1,
                },
                retained_versions=list(self._audit_keyring.versions),
                detail={
                    "resumed": resume,
                    "journal": str(journal.path),
                    "history_rewritten": False,
                },
            )

            return {
                "db_path": self.db_path,
                "keyring_path": str(self._keyring_path),
                "journal_path": str(journal.path),
                "previous_version": previous_version,
                "active_version": target_version,
                "resumed": resume,
                "events_by_version": event_counts,
                "chain_valid_before": before["valid"],
                "chain_valid_after": after["valid"],
                "retained_versions": list(self._audit_keyring.versions),
                "status": status,
                "ledger": {"path": str(ledger.path), "operation_id": recorded.operation_id},
            }

    def set_active_audit_key_version(self, version: int) -> Dict[str, Any]:
        """Documented rollback: sign new events with an existing version again."""
        self._ensure_writable("change the active audit key version")
        if self._keyring_path is None:
            raise KeyConfigurationError("This database has no durable key registry.")
        working = KeyRing.from_dict(self._audit_keyring.to_dict())
        working.set_active(version)
        self._persist_audit_keyring(working)
        return {
            "active_version": working.active_version,
            "events_unchanged": True,
            "chain_valid": self.verify_all_audit_chains()["valid"],
        }

    def remove_audit_key_version(
        self, version: int, *, confirm_retained_copies: bool = False
    ) -> Dict[str, Any]:
        """Delete a key version - refused while any event still depends on it."""
        self._ensure_writable("remove an audit key version")
        if self._keyring_path is None:
            raise KeyConfigurationError("This database has no durable key registry.")
        version = int(version)
        if version == self._audit_keyring.active_version:
            raise KeyConfigurationError(
                f"Audit key version {version} is the active version and cannot be removed."
            )
        by_version = self.audit_events_by_key_version()
        if by_version.get(version):
            raise KeyVersionInUseError(
                f"Refusing to remove audit key version {version}: {by_version[version]} event(s) in "
                f"{self.db_path} are still signed with it. Historical events cannot be re-signed without "
                "rewriting the audit trail."
            )
        working = KeyRing.from_dict(self._audit_keyring.to_dict())
        working.remove(version, confirm_retained_copies=confirm_retained_copies)
        self._persist_audit_keyring(working)
        return {"removed_version": version, "active_version": working.active_version}
