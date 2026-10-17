# Procurement, Stock, and Three-Way Matching Specification (Phase 4)

## 1. End-to-End Procure-to-Pay (`P2P`) Lifecycle (`Verified`)

The procurement and accounting engine (`dz_erp/procurement_stock.py`) implements the complete Algerian SCF procure-to-pay lifecycle:

1. **Purchase Order (`create_purchase_order`):**
   - Records ordered quantities (`qty_ordered`), agreed unit prices (`unit_price_dzd`), and TVA rates (`tax_rate`) per `purchase_order_lines` row.
   - Creates **no** General Ledger entry and **no** stock move.
2. **Physical Goods Receipt (`receive_goods_against_po`):**
   - Validates physical receipt into a designated warehouse (`WH-MAIN`, etc.).
   - **Exclusive Stock Movement Trigger:** Creates `stock_moves` (`IN`) and increments `stock_quants.qty_on_hand`.
   - **Automatic Backorder Generation:** If `qty_done < remaining_qty`, updates `purchase_orders.state` to `'PARTIALLY_RECEIVED'` and automatically creates a `'DRAFT_BACKORDER'` `goods_receipts` record for the unreceived balance.
   - **Perpetual Inventory Accounting (`SCF Class 3`):** Posts a balanced stock journal entry (`STK`):
     - **Debit `31` / `30` (Stocks):** `qty_done * unit_price_dzd`
     - **Credit `380` (*Achats stockés — Compte transitoire*):** `qty_done * unit_price_dzd`
3. **Deterministic Validation & Three-Way Matching (`create_and_validate_supplier_invoice`):**
   - **Cross-Foot Math:** Verifies $\sum \text{line\_ht} = \text{amount\_ht\_dzd}$, $\sum \text{line\_tva} = \text{amount\_tva\_dzd}$, *Droit de Timbre* rule compliance, and $\text{HT} + \text{TVA} + \text{Timbre} = \text{amount\_ttc\_dzd}$ (`0.00 DZD` tolerance).
   - **Supplier Identifier Validation:** Verifies partner `NIF`, `NIS`, `AI`, and `RC`.
   - **Normalized & Fuzzy Duplicate Detection:**
     - Exact match on `(company_id, supplier_id, normalized_invoice_no)` where `normalized_invoice_no = re.sub(r"[^A-Z0-9]", "", raw.upper())` transitions immediately to `DuplicateRejected`.
     - Near-duplicate match (Levenshtein distance $\le 1$ on `normalized_invoice_no` with identical `amount_ttc_dzd` for the same supplier) flags `FUZZY_DUPLICATE_SUSPECTED` and holds the invoice in `NeedsReview`.
   - **Three-Way Match (`PO` vs. `GRN` vs. `Invoice`):**
     - Verifies `invoiced_qty <= (po_line.qty_received - po_line.qty_invoiced)` (blocking invoices created before goods receipt or over-billed partial invoices with `THREE_WAY_MATCH_QTY_MISMATCH`).
     - Verifies `unit_price_dzd == po_line.unit_price_dzd` (`THREE_WAY_MATCH_PRICE_MISMATCH`).
4. **Human Approval with Segregation of Duties (`submit_invoice_for_approval`, `decide_invoice_approval`):**
   - Only `Validated` invoices can be submitted for approval.
   - Approver must hold role `approver`, `accountant`, or `admin` **and** must not be the invoice creator (`approver_user_id != created_by_id`).
   - Records `approved_payload_sha256` on `supplier_invoices` and `approval_requests`. Any subsequent modification to draft lines (`modify_draft_invoice_line`) recomputes `payload_sha256`, invalidates the approval (`decision = 'INVALIDATED'`), and reverts the invoice to `NeedsReview`.
5. **Idempotent General Ledger Posting (`post_supplier_invoice`):**
   - Requires a caller-supplied `idempotency_key` (`erp_idempotency_key` / `idempotency_keys`). Replaying the same key returns the cached response (`idempotent_replay = True`) without duplicating entries.
   - Posts a balanced purchase journal entry (`ACH`):
     - **Debit `380` (*Achats stockés*)** for storable PO/GRN-matched lines (and automatically reconciles the `380` debit line against the `380` credit line created at Goods Receipt), or **Debit `60x` / `61x` / `62x`** for non-stocked expenses.
     - **Debit `4456` (*Taxes sur le chiffre d'affaires déductibles*)** with G50 tag `E3B34` (`19%`) or `E3B33` (`9%`).
     - **Debit `645` (*Droits d'enregistrement et de timbre*)** if `amount_timbre_dzd > 0.00`.
     - **Credit `401` (*Fournisseurs de stocks et services*)** for `amount_ttc_dzd`.
   - **Zero Duplicate Stock Movement:** Posting a supplier invoice **never** creates a `stock_moves` row or alters `stock_quants`.
6. **Credit Note Reversal (`reverse_posted_invoice`):**
   - Posted invoices and journal entries cannot be edited or deleted (`trg_je_immutable_update`, `trg_je_immutable_delete`).
   - Corrections are executed via `reverse_posted_invoice`, which creates a new `CREDIT_NOTE` (`AVOIR`) invoice and posts an exact reversing journal entry (`OD`), linking `reversal_entry_id` on the original journal entry while decrementing `po_line.qty_invoiced` and un-reconciling `380` so a replacement invoice can be processed cleanly.

---

## 2. Concurrency & Negative-Stock Prevention (`deduct_stock_concurrent_safe`)

- All inventory mutations execute inside `BEGIN IMMEDIATE` database transactions (`SERIALIZABLE` / write-locked isolation).
- If `qty_on_hand - qty_to_deduct < 0`, the transaction aborts and raises `FinancialIntegrityError("INSUFFICIENT_STOCK_ERROR")`.
- Verified under multi-threaded contention (`test_concurrent_stock_updates_never_go_negative`: 5 worker threads concurrently attempting to deduct 4 units each from 10 available units $\rightarrow$ exactly 2 succeed, 3 fail cleanly with `INSUFFICIENT_STOCK_ERROR`, leaving `2.000000` units on hand).
