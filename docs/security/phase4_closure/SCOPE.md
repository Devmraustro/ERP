# SEC-13 / SEC-14 / SEC-15 closure — patch scope

Two equally verified application routes (both reproduced byte-identically, see
`PHASE4_SEC131415_CLOSURE_REPORT.md` §3):

| Route | Patch | Applies to |
|---|---|---|
| **Layered (preferred)** | `phase4-sec13-14-15-delta.patch` | a workspace at `5a3e47dd` **+** `../security-selective-integration.patch` |
| **One-shot** | `full-security-state-from-5a3e47dd.patch` | a clean checkout of `5a3e47dd` (supersedes the preserved patch; includes it) |

Both routes then need `untracked-files/tests/test_phase59_sec13_14_15_closure.py`
copied into `tests/`; the layered route also needs the Phase-2 untracked module.

| File | SEC-13 | SEC-14 | SEC-15 | V1 scope docs |
|---|---|---|---|---|
| `dz_erp/auth.py` | ✅ | | | |
| `dz_erp/security_audit.py` | ✅ | | | |
| `dz_erp/intake_ocr.py` | | ✅ | | |
| `dz_erp/net.py` | | | ✅ | |
| `dz_erp/serve.py` | | | ✅ | |
| `dz_erp/api.py` | ✅ (2 endpoints, policy on create) | | ✅ (Host middleware, host policy in diagnostics) | |
| `ops/erp.env.example` | ✅ (`ERP_PASSWORD_MIN_LENGTH`) | | ✅ (`ERP_ALLOWED_HOSTS`, `FORWARDED_ALLOW_IPS` note) | ✅ (KMS/MinIO out) |
| `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` | ✅ (§6.2) | | ✅ (§6.1) | ✅ (§6.3) |
| `tests/test_phase59_sec13_14_15_closure.py` | ✅ | ✅ | ✅ | |
| `tests/test_phase7_authentication_remediation.py` | fixture compliance (7-char literal → 10) | | | |
| `tests/test_phase8_upload_boundary_and_ci.py` | contract pin 33 → 35 with rationale | | | |

`api.py` and `ops/erp.env.example` are shared by two findings, so the per-finding
patches below are for *review convenience only* and are not standalone:

* `sec13-password-policy-core.patch` — `auth.py` + `security_audit.py`
* `sec14-ocr-override-validation.patch` — `intake_ocr.py` (fully standalone)
* `sec15-host-and-proxy-core.patch` — `net.py` + `serve.py`

`full-security-state-from-5a3e47dd.patch` = preserved patch + delta, generated
as one `git diff`; it is byte-identical in effect to applying the two in order.
