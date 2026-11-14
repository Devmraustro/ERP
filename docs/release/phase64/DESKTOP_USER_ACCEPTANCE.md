# Phase 64 — Desktop User Acceptance

| | |
|---|---|
| Date | 2026-11-14 (UTC) |
| Status | **NOT EXECUTED — GATE BLOCKED.** The Qt application cannot be started in this environment (no display, PySide6 not installable). No screen was observed by this session. |
| Scope | What an accountant or clerk sees and does, on Windows, with synthetic data. The server-side behaviour behind each screen **was** exercised over HTTP in Phase 67; this document is about the client. |

## 1. What is known without running the client

| Fact | Source |
|---|---|
| The client is a thin Qt front-end over the same HTTP API tested in Phase 67 | `dz_erp/desktop/api_client.py` |
| Two modes: embedded single workstation (default) and company server (`--mode server --server-url …`) | `docs/DESKTOP_APPLICATION.md` §3 |
| French and Arabic interface, Arabic right-to-left | same, §4 |
| Qt tests pass in CI on Linux off-screen | Phase 62, job 132484609813 |
| The credit-note defect fixed in Phase 67 affected the client too: it calls the same three URLs, so before the fix a reversal started from the desktop could never be approved or posted | `api_client.py` uses `/api/v1/invoices/{id}/submit-approval`, `/decide-approval`, `/post` |

## 2. Acceptance script

Use the roles created in Phase 67: one clerk, one approver, one accountant, one auditor. Each tester
records pass/fail and what they actually saw. A step fails if the tester could not complete it without
help, even when the software behaved as designed — say which.

| # | Role | Action | Expected | Result |
|---|---|---|---|---|
| 1 | Admin | First run: create company and admin | wizard completes; lands on the main window | NOT EXECUTED |
| 2 | Admin | Create the four users | each can log in; a short password is refused with a readable message | NOT EXECUTED |
| 3 | Clerk | Create a supplier with NIF, NIS, AI, RC | saved; an invalid NIF is refused with a message that says what is wrong | NOT EXECUTED |
| 4 | Accountant | Create a stock item | saved; an unknown account code is refused | NOT EXECUTED |
| 5 | Clerk | Upload a synthetic supplier invoice PDF | appears in the review queue with extracted fields | NOT EXECUTED |
| 6 | Clerk | Correct an extracted field and confirm | correction saved; original OCR value still visible | NOT EXECUTED |
| 7 | Clerk | Enter a supplier invoice (bank transfer) | state `Validated` | NOT EXECUTED |
| 8 | Clerk | Enter the same invoice number again | clearly shown as a rejected duplicate — **not** as a success | NOT EXECUTED |
| 9 | Clerk | Submit for approval; try to approve it | approval is not offered or is refused | NOT EXECUTED |
| 10 | Approver | Approve | state `Approved` | NOT EXECUTED |
| 11 | Accountant | Post | journal entry number shown; pressing the button twice does not post twice | NOT EXECUTED |
| 12 | Accountant | Enter a cash invoice with the wrong stamp duty | held for review with the expected amount shown | NOT EXECUTED |
| 13 | Accountant | Reverse the posted invoice; submit the credit note | credit note appears, awaiting approval | NOT EXECUTED |
| 14 | Approver | Approve the credit note | `Approved` | NOT EXECUTED |
| 15 | Accountant | Post the credit note | posted; supplier balance returns to the pre-invoice value | NOT EXECUTED |
| 16 | Auditor | Open the trial balance | balanced; no create or post action is available | NOT EXECUTED |
| 17 | Any | Switch to Arabic | layout mirrors; no clipped or missing glyphs; amounts stay readable | NOT EXECUTED |
| 18 | Any | Log out, log in as another user | previous user's data and rights are gone | NOT EXECUTED |
| 19 | Any | Disconnect the server (server mode) or kill the embedded service | a clear error; no crash; no half-saved document after reconnect | NOT EXECUTED |
| 20 | Any | Close the application during an upload | restarts cleanly; no orphan document | NOT EXECUTED |

## 3. Things the tester will not find, because they are not implemented

Do not log these as defects; they are outside version 1 (`docs/release/final/ERP_PRODUCTION_RELEASE_HANDOVER.md` §10):
customer invoices and receivables, payments and bank reconciliation, period lock and year-end close,
customs/import declarations, a purchase-order or goods-receipt screen backed by an HTTP route, a backup
button, and Arabic recognition of scanned images (Arabic scans go to manual transcription).

## 4. Sign-off

| Field | Value |
|---|---|
| Testers (name, role in the company) | |
| Commit / executable SHA-256 | |
| Date | |
| Steps passed / failed / not executed | / / |
| Usability reservations | |
| Signature of the business owner | |

**Gate status: BLOCKED** until completed by named users.
