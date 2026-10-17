# Dependency and License Register

## 1. Upstream ERP & Localization Repositories (`Verified`)

| Repository | Branch / Tag | Exact Pinned Commit SHA | Verified License | Commercial / SaaS / On-Prem Compatibility | Status & Usage Decision |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `odoo/odoo` | `18.0` / `19.0` | `50ff2d0e5db6b9c89e6fbfa86477427b6425b6c3` (`18.0`) / `dbe0c5015da3b1f00c25613572fed695d8470d20` (`19.0`) | `LGPL-3.0` | **Compatible** (weak copyleft; permits proprietary or custom modules alongside LGPL-3.0 addons) | **Approved (`Verified`)** — Base `addons/l10n_dz` files vendored in `vendor/odoo_18_l10n_dz/` |
| `azinak/l10n-dz-account-fix` | `18.0` / `19.0` | `83056300237a6df09ac001d25cc854179774ede2` (`18.0`) / `73ef2ade4c86a336880a953ce79524ffaf32a0a3` (`19.0`) | `LGPL-3.0-only` | **Compatible** | **Approved (`Verified`)** — Vendored in `vendor/azinak_l10n_dz_account_fix/`; all 16 tests executed and passing via `pytest` |
| `taghrastech/odoo-l10n-dz` | `18.0` | `252e35942c45e48681d8b8829b23efb049b584fd` | `LGPL-3.0` | **Compatible** | **Approved (`Verified`)** — `tt_l10n_dz_state` 69-Wilaya XML vendored in `vendor/taghrastech_odoo_l10n_dz/` |
| `softintegration/l10n_dz_company` | `15.0` | `7e3c458cea6dfd8d47bb5f0f322ff8dee9797676` | **Unlicensed** (no `LICENSE` file in repo; only manifest string) | **Incompatible / Legal Risk** (also stops at Odoo 15.0 and uses unvalidated `fields.Char()`) | **Rejected (`Verified`)** — Replaced by clean-room `odoo_addons/l10n_dz_scf_extensions` (`LGPL-3.0-only`) |
| `OCA/server-tools` (`auditlog`) & `OCA/server-ux` (`base_tier_validation`) | `18.0` | `126afd2a62ec528386285aa753b24c15277d1f15` / `8ae4369943650d72619f6b390e367847865fde30` | `AGPL-3.0` (verified in `__manifest__.py`, despite repo root `LICENSE`) | **Incompatible** with proprietary SaaS network distribution without full source disclosure | **Rejected (`Verified`)** — Replaced by clean-room DB-trigger-backed audit & approval engine in `dz_erp/` (`LGPL-3.0-only`) |
| `frappe/erpnext` | `v16.51.1` / `v15.123.0` | `5af6561f5aa48104559692fb4fd82f2580e8d338` / `e864addb6c367688d2b264f284c2dd9b9360a97d` | `GPL-3.0` | Strong copyleft (`GPL-3.0`); `v15` lacks Algeria COA and `v16` misclassifies SCF Class 1 & Class 5 | **Not Selected (`Verified`)** |

---

## 2. OCR, PDF & AI Extraction Stack (`Verified`)

| Component / Package | Pinned Version / Commit SHA | Verified License | Commercial Compatibility | Role in Architecture |
| :--- | :--- | :--- | :--- | :--- |
| `rapidocr-onnxruntime` | `1.4.4` (bundles `PP-OCRv4` ONNX detection/classification/recognition models) | `Apache-2.0` | **Compatible** | Primary CPU-based raster image OCR engine in `dz_erp/intake_ocr.py` |
| `onnxruntime` | `1.21.0` | `MIT` | **Compatible** | Deterministic CPU inference runtime for `PP-OCRv4` ONNX models |
| `opencv-python-headless` | `4.11.0.86` | `Apache-2.0` | **Compatible** | Headless image preprocessing (replaces `opencv-python` to avoid `libGL.so.1` dependency) |
| `pdfplumber` / `pdfminer.six` | `0.11.6` / `20250327` | `MIT` | **Compatible** | Primary digital-born PDF text & bounding-box extractor (French + Arabic) |
| `pypdfium2` | `4.30.1` | `Apache-2.0` / `BSD-3-Clause` | **Compatible** | High-speed PDF-to-raster renderer (`200 DPI`) for scanned PDFs |
| `pypdf` | `5.4.0` | `BSD-3-Clause` | **Compatible** | PDF structure validation |
| `reportlab` & `pillow` | `4.3.1` & `11.1.0` | `BSD-3-Clause` & `MIT-CMU` | **Compatible** | Synthetic Algerian invoice benchmark rendering |
| `PaddlePaddle/PaddleOCR` | `v3.7.0` (`b03f46425e8ff4442b268ce449e3eef758146cd4`) | `Apache-2.0` | **Compatible** | Upstream source of `PP-OCRv4` models; recommended `arabic_PP-OCRv4_rec` head for scanned Arabic |
| `docling-project/docling` | `v2.140.0` (`27a278042c72ac27dcbe83750ee1ad4d908dc18e`) | `MIT` | **Compatible** | Optional layout/table parser for complex multi-page invoices |
| `QwenLM/Qwen3-VL` | `99f0fb6fd3ec6abbaa3fd8ccbfdddcbe0d3a55f5` | `Apache-2.0` | **Compatible** | Optional VLM fallback for unstructured documents (strictly untrusted staging output) |
| `datalab-to/surya` | `f6eb7ffd4d96fb81e062fb7fc0983b1c36f30627` | Code: `GPL-3.0`; Weights: **AI Pubs Open Rail-M** (free only under `$2M` revenue) | **Incompatible** | **Rejected (`Verified`)** |

---

## 3. Core Runtime, Web API, Cryptography & Test Dependencies (`Verified`)

All Python packages installed in `/tmp/erp-venv` are pinned to exact versions in `/home/user/ERP/requirements.lock.txt`:

- `fastapi==0.115.12` (`MIT`)
- `pydantic==2.11.1` & `pydantic_core==2.33.0` (`MIT`)
- `uvicorn==0.34.0` (`BSD-3-Clause`)
- `httpx==0.28.1` (`BSD-3-Clause`)
- `python-multipart==0.0.20` (`Apache-2.0`)
- `cryptography==44.0.2` (`Apache-2.0` / `BSD-3-Clause`) — used for `Fernet` (`AES-128-CBC` + `HMAC-SHA256`) encryption at rest in `PrivateDocumentVault` and encrypted database backups
- `pytest==8.3.5` & `pytest-cov==6.0.0` (`MIT`)
