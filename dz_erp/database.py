# SPDX-License-Identifier: LGPL-3.0-only
"""Database engine, schema DDL, immutability triggers, and hash-chained audit log."""

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


class FinancialIntegrityError(RuntimeError):
    """Raised when an operation violates accounting or stock integrity constraints."""


class AuditTamperError(RuntimeError):
    """Raised when an attempt is made to mutate or delete audit records."""


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
    api_token_hash  TEXT
);

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
    ocr_latency_ms      REAL
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
    confidence              REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
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
  OR OLD.confidence != NEW.confidence
  OR OLD.ocr_model_version != NEW.ocr_model_version
BEGIN
    SELECT RAISE(ABORT, 'OCR_RAW_IMMUTABILITY_VIOLATION: raw_text, confidence, and ocr_model_version cannot be overwritten.');
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
    posted_by_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    posted_at           TEXT NOT NULL,
    CHECK (total_debit_dzd = total_credit_dzd),
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


class ERPDatabase:
    """Thread-safe transactional database manager with audit hash-chaining."""

    def __init__(self, db_path: str = ":memory:", hmac_secret: Optional[bytes] = None) -> None:
        self.db_path = db_path
        self._hmac_secret = hmac_secret or os.environ.get(
            "ERP_AUDIT_HMAC_SECRET", "dz-erp-default-test-hmac-key-change-in-prod"
        ).encode("utf-8")
        self._lock = threading.RLock()
        self._persistent_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._persistent_conn = self._create_connection()
            self._init_schema(self._persistent_conn)
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            with self._create_connection() as conn:
                self._init_schema(conn)

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA_SQL)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Execute an atomic IMMEDIATE transaction with automatic commit/rollback."""
        with self._lock:
            conn = self._persistent_conn if self._persistent_conn is not None else self._create_connection()
            in_tx = conn.in_transaction
            try:
                if not in_tx:
                    conn.execute("BEGIN IMMEDIATE;")
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
    ) -> str:
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
        return hmac.new(self._hmac_secret, payload, hashlib.sha256).hexdigest()

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
        )
        cur = conn.execute(
            """
            INSERT INTO audit_events (
                company_id, occurred_at, actor_user_id, action_type,
                target_model, target_id, correlation_id, before_json, after_json,
                prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                prev_hash,
                event_hash,
            ),
        )
        return int(cur.lastrowid)

    def verify_audit_chain(self, company_id: int) -> Dict[str, Any]:
        """Verify hash-chain continuity and HMAC integrity for all audit events of a company."""
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE company_id = ? ORDER BY id ASC",
                (company_id,),
            ).fetchall()
        expected_prev = "0" * 64
        for idx, row in enumerate(rows):
            if row["prev_hash"] != expected_prev:
                return {
                    "valid": False,
                    "checked_count": idx,
                    "broken_at_id": row["id"],
                    "reason": "PREV_HASH_MISMATCH",
                }
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
            )
            if not hmac.compare_digest(recomputed, row["event_hash"]):
                return {
                    "valid": False,
                    "checked_count": idx,
                    "broken_at_id": row["id"],
                    "reason": "EVENT_HASH_TAMPERED",
                }
            expected_prev = row["event_hash"]
        return {"valid": True, "checked_count": len(rows), "last_hash": expected_prev}
