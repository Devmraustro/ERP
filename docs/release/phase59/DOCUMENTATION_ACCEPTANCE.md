# Phase 59G — Documentation Acceptance

**Branch:** `arena/63d762c8-erp` · **Base commit:** `098483a1f02df0308f3889c93714efab5d13b550`

---

## 1. Deliverables

| Document | Language | Audience | Status |
|---|---|---|---|
| `docs/user/USER_GUIDE_FR.md` | French | operators | **NEW** — written against the shipped UI strings |
| `docs/user/USER_GUIDE_AR.md` | Arabic | operators | **NEW** — same content, RTL-aware |
| `docs/admin/ADMINISTRATION_AND_RECOVERY.md` | English | administrator | **NEW** |
| `docs/release/phase59/INSTALLATION_AND_STARTUP_ACCEPTANCE.md` | English | release reviewer | **NEW** |
| `docs/release/phase59/END_TO_END_OPERATIONAL_ACCEPTANCE.md` | English | release reviewer | **NEW** |
| `docs/release/phase59/ACCOUNTING_AND_COMPLIANCE_READINESS.md` | English | reviewer / expert | **NEW** |
| `docs/release/phase59/DATA_PROTECTION_AND_RECOVERY_ACCEPTANCE.md` | English | reviewer | **NEW** |
| `docs/release/phase59/SECURITY_AND_RELIABILITY_ACCEPTANCE.md` | English | reviewer | **NEW** |
| `docs/release/phase59/USER_ACCEPTANCE_REPORT.md` | English | reviewer | **NEW** |
| `docs/release/phase59/DOCUMENTATION_ACCEPTANCE.md` | English | reviewer | this file |

## 2. The rule applied: never document a feature that does not exist

Every claim in the two operator guides was checked against **shipped** strings or **executed**
behaviour. No screen, button or message was invented.

| Claim in the guide | Verified against |
|---|---|
| Navigation labels and subtitles | `dz_erp/desktop/i18n.py::_FR` — `nav_*`, `page_*_sub` read out of the shipped dictionary |
| Invoice states (`Brouillon … Doublon rejeté`) | `STATE_KEYS` mapped through `_FR` |
| "Points à traiter avant soumission" banner | `review_blockers_title` + the four `review_blocker_*` keys |
| `[manual entry]` means a human read the value | `HUMAN_ENTRY_RAW` in `dz_erp/review_policy.py`; observed in the E2E field list |
| "Ce champ n'a pas été extrait…" | `review_missing_hint` |
| "Décision refusée : {detail}" | `review_decide_failed` |
| `SEGREGATION_OF_DUTIES_VIOLATION` | **executed** — HTTP `403` with that exact string |
| `Role 'approver' is not authorized…` | **executed** — HTTP `403` |
| `Cannot post invoice N in state …` | **executed** — HTTP `409` |
| `Invalid NIF '123': must be 15 or 20 digits.` | **executed** — HTTP `422` |
| `stock_account_code '9999' is not an account…` | **executed** — HTTP `422` |
| 5-attempt / 15-minute lockout | `dz_erp/auth.py::MAX_FAILED_LOGINS`, `LOGIN_LOCKOUT_SECONDS`; **executed** |
| 286 strings in each of three languages | **executed** — parity check |
| Minimum window 940 × 620 | `self.setMinimumSize(940, 620)` |
| `30`, `380`, `401`, `4456`, `601` are real accounts, `300` is not | **executed** against the seeded chart |
| Threshold `300.00`, floor `5.00`, cash-only timbre | `calculate_droit_de_timbre` — live table in 59C §3 |

### 2.1 Features explicitly declared **absent** (and therefore not documented as usable)

The guides state these do not exist, because they do not:

* withholding-tax calculation (`partners.tax_regime` exists; no withholding engine);
* Law 18-07 data-subject rights workflow (access / rectification / objection);
* automatic G50 filing (the system prepares aggregates; filing is manual);
* customer-facing tax invoicing (supplier invoices only);
* any legal certification (`Q1`–`Q12` PENDING, `G5` BLOCKED).

## 3. Commands in the documentation were executed

| Command | Document | Executed |
|---|---|---|
| `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | admin §2 | yes (CI form; local `--no-deps` form) |
| `python tools/generate_hash_lock.py --check` | admin §2 | yes — `OK - 53 pinned distributions, 1317 hashes` |
| `… --desktop --check` | admin §2 | yes — `OK - 4 pinned distributions, 20 hashes` |
| `python -m dz_erp.serve --bootstrap-admin …` | admin §2 | yes |
| `sqlite3 … 'SELECT COUNT(*) FROM accounts;'` → 294 | admin §2 | yes |
| `python -m dz_erp.ops_backup … run` / `verify` | admin §4 | yes |
| `… verify --deep` | admin §4 | **executed and FAILED** — Python floor; the document says so explicitly |
| `restore_and_verify_backup(...)` snippet | admin §6 | **NOT EXECUTED** (same floor) — marked as such in the doc |
| `curl … /api/v1/users` create + deactivate | admin §7 | create executed; **deactivate NOT executed** |
| `--host 0.0.0.0` refusal | admin §2 warning | yes |

Two documented commands could not be executed here. Both are **labelled in place**, and §6 of the
admin guide states the interpreter requirement in bold. No command is presented as tested when it was
not.

## 4. What still needs a human before these guides are final

| Item | Why |
|---|---|
| Arabic review by a native speaker | The Arabic guide is written by the assistant, not a professional translator. Terminology (النظام المحاسبي المالي، الطابع الجبائي، إشعار دائن) follows standard Algerian usage but has **not** been reviewed |
| French review by an accountant | Same reason; phrasing is precise but not professionally proofread |
| Screenshots | None. Headless environment, and a fabricated screenshot would be worse than none |
| Windows-specific paths | The guides avoid Windows-specific commands rather than guess them |

## 5. Feedback loop

If an operator finds a message in the application that is **not** in this guide, the guide is
incomplete — record the exact string and the screen. Do not paraphrase it in a bug report; the exact
string is what maps to an i18n key.
