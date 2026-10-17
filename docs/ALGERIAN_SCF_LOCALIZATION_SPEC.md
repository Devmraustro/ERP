# Algerian SCF Localization Specification (Phase 2)

## 1. Statutory & Regulatory Disclaimer

- **Engineering Status:** `Passed` (30/30 automated tests passing, including all 16 upstream `azinak/l10n-dz-account-fix` tests and our Phase 2 SQL/migration/tax/G50 tests).
- **Statutory Sign-Off Status:** **Not certified.** While engineered to conform to Algerian *Système Comptable Financier* (SCF — Law `07-11` and Executive Decree `08-156`) and *Code des Impôts Directs / Code du Timbre*, this software has **not** received formal written sign-off from a licensed Algerian *Expert-Comptable* or *Commissaire aux Comptes* (*Ordre National des Experts-Comptables — ONEC*). All G50 outputs are explicitly labeled `PREPARATION_WORKBOOK_ONLY__REQUIRES_ONEC_EXPERT_COMPTABLE_SIGN_OFF`.

---

## 2. Verified Defects in Upstream Odoo `addons/l10n_dz` (`18.0` / `19.0`) & Implemented Fixes

### 2.1 Default Customer Receivable Account (`411` vs `413`)
- **Upstream Defect (`vendor/odoo_18_l10n_dz/models/template_dz.py`):** Sets `account_default_pos_receivable_account_id` and `property_account_receivable_id` to `l10n_dz_413` (*Clients - Effets à recevoir* — Notes/Bills Receivable) instead of `l10n_dz_411` (*Clients* — Trade Receivables).
- **Implemented Fix (`dz_erp/localization.py` & `vendor/azinak_l10n_dz_account_fix`):** Overrides `property_account_receivable_id` to `l10n_dz_411` (`411`), while keeping `413` strictly for bills/notes receivable and `401` (*Fournisseurs de stocks et services*) as default payable.

### 2.2 Account Type Reclassifications (24 Accounts)
Upstream `vendor/odoo_18_l10n_dz/data/template/account.account-dz.csv` misclassifies 24 accounts, including setting 10 non-cash Class 5 accounts (`501`–`509`, `519`, `521`, `529`, `591`, `594`) to `asset_cash` (which causes Odoo to auto-create spurious bank journals on chart installation) and setting `481` (*Provisions - passifs courants*) to `asset_receivable`.

| Account Code | French Title (SCF) | Upstream Type (Bug) | Corrected Type (SCF) |
| :--- | :--- | :--- | :--- |
| `390`, `391`, `392`, `393`, `394`, `395`, `397` | Pertes de valeur sur stocks | `income` | `asset_current` |
| `481` | Provisions - passifs courants | `asset_receivable` | `liability_current` |
| `491`, `495`, `496` | Pertes de valeur sur comptes de tiers | `liability_current` | `asset_current` |
| `501`, `502`, `503`, `506`, `508` | Valeurs mobilières de placement (VMP) | `asset_cash` | `asset_current` |
| `509` | Versements restant à effectuer sur VMP non libérées | `asset_cash` | `liability_current` |
| `519`, `529` | Concours bancaires courants / Instruments financiers | `asset_cash` | `liability_current` |
| `521` | Banques, instruments financiers dérivés | `asset_cash` | `asset_current` |
| `591`, `594` | Pertes de valeur sur actifs financiers courants | `asset_cash` | `asset_current` |
| `619`, `629` | Rabais, remises et ristournes obtenus sur services | `income` | `expense` |

### 2.3 Reconciliation Flags (`reconcile = True`)
Enabled `reconcile = True` on the 4 third-party advance/remuneration accounts omitted by upstream:
- `409`: *Fournisseurs - Débiteurs : avances et acomptes versés, RRR à obtenir, autres créances*
- `419`: *Clients créditeurs - avances reçues, RRR à accorder et autres avoirs à établir*
- `421`: *Personnel - Rémunérations dues*
- `425`: *Personnel - Avances et acomptes accordés*

### 2.4 French Group Translation Fixes & Arabic SCF Labels
- Corrected French mistranslations in `account.group-dz.csv`:
  - Group `32`: `"Pertes de valeur sur Autres approvisionnements"` $\rightarrow$ `"Autres approvisionnements"` (Arabic: `"تموينات أخرى"`).
  - Group `66`: `"Charges financières différées"` $\rightarrow$ `"Charges financières"` (Arabic: `"الأعباء المالية"`).
- Populated Arabic (`ar_001`) SCF account and group titles across core operational classes (`101`–`709`).

### 2.5 Algerian Administrative Geography (69 Wilayas)
- Integrated all **69 Algerian Wilayas** (`01` Adrar through `58` El Meniaa + Wilayas `59`–`69` established under the 2025 administrative reform: Aflou, Barika, Ksar Chellala, Messaad, Aïn Oussera, Boussaâda, El Abiodh Sidi Cheikh, El Kantara, Bir El Ater, Ksar El Boukhari, El Aricha) from `vendor/taghrastech_odoo_l10n_dz/tt_l10n_dz_state/data/res_country_state.xml`.

### 2.6 Algerian Party Identifiers (`NIF`, `NIS`, `RC`, `AI`, `NIN`)
Implemented in both `dz_erp/localization.py` (`validate_algerian_identifiers`) and `odoo_addons/l10n_dz_scf_extensions/models/res_partner_dz.py`:
- **NIF (*Numéro d'Identification Fiscale*):** 15 or 20 numeric digits (`^[0-9]{15}([0-9]{5})?$`).
- **NIS (*Numéro d'Identification Statistique*):** 15 numeric digits (`^[0-9]{15}$`).
- **AI (*Article d'Imposition*):** 11 numeric digits (`^[0-9]{11}$`).
- **RC (*Registre du Commerce*):** Wilaya/antenne + alphanumeric registration (`^[0-9]{2}/[0-9]{2}-[0-9A-Z]{7,10}$`).
- **NIN (*Numéro d'Identification National*):** 18 numeric digits (`^[0-9]{18}$`).

---

## 3. DZD Monetary Precision, TVA & *Droit de Timbre* (`dz_erp/decimal_utils.py`, `dz_erp/localization.py`)

1. **Exact Decimal Arithmetic:** All monetary computations use Python `Decimal` with `ROUND_HALF_UP` to `0.01` DZD. Passing a binary `float` raises `FloatMonetaryError` immediately.
2. **TVA Rates:** Versioned tax rules supporting `19.0%` (*TVA Taux Normal*), `9.0%` (*TVA Taux Réduit*), and `0.0%` (*Exonéré*) on both purchases (`4456`) and sales (`4457`).
3. **Graduated *Droit de Timbre* (Cash Stamp Duty — Art. 258 *Code du Timbre* / LF 2025):**
   - Applies **only** when `payment_method == "CASH"`; `BANK_TRANSFER`, `CHEQUE`, and `CARD` yield `0.00 DZD`.
   - Exempt if cash base $\le 300.00\text{ DZD}$.
   - $1.0\%$ for base $\le 30,000.00\text{ DZD}$ (minimum $5.00\text{ DZD}$).
   - $1.5\%$ for $30,000.00 < \text{base} \le 100,000.00\text{ DZD}$.
   - $2.0\%$ for $\text{base} > 100,000.00\text{ DZD}$ (capped at $10,000.00\text{ DZD}$).

---

## 4. Conservative Migration Rules (`apply_algerian_localization_fixes`)

1. **Idempotent:** Running the migration $N$ times produces changes only on the first pass (`receivable_updated == False` and empty diffs on subsequent passes).
2. **Preserves User Customizations:** Only updates company default receivable if it is still set to upstream `413` (`is_custom_receivable == 0`), and only updates `account_type` if the current type still equals the upstream defective value and `is_custom_type == 0`.
3. **Preserves Archived State:** Updates `account_type` and `reconcile` without altering `active = 0`.
4. **Never Rewrites Posted Entries:** Historical posted journal entries (`journal_entries.state = 'POSTED'`) are never modified or deleted.
5. **Multi-Company Scoped:** Non-Algerian companies (`country_code != 'DZ'`) are skipped completely.
