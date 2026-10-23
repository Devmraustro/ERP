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
| `onnxruntime` | `1.31.0` | `MIT` | **Compatible** | Deterministic CPU inference runtime for `PP-OCRv4` ONNX models |
| `opencv-python-headless` | `4.11.0.86` | `Apache-2.0` | **Compatible** | Headless image preprocessing. **Deliberately substituted** for the GUI `opencv-python` wheel that `rapidocr-onnxruntime` declares — see §2.1 |
| `pdfplumber` / `pdfminer.six` | `0.11.6` / `20250327` | `MIT` | **Compatible** | Primary digital-born PDF text & bounding-box extractor (French + Arabic) |
| `pypdfium2` | `5.14.0` | `Apache-2.0` / `BSD-3-Clause` | **Compatible** | High-speed PDF-to-raster renderer (`200 DPI`) for scanned PDFs |
| `pypdf` | `5.4.0` | `BSD-3-Clause` | **Compatible** | PDF structure validation |
| `reportlab` & `pillow` | `4.3.1` & `11.1.0` | `BSD-3-Clause` & `MIT-CMU` | **Compatible** | Synthetic Algerian invoice benchmark rendering |
| `PaddlePaddle/PaddleOCR` | `v3.7.0` (`b03f46425e8ff4442b268ce449e3eef758146cd4`) | `Apache-2.0` | **Compatible** | Upstream source of `PP-OCRv4` models; recommended `arabic_PP-OCRv4_rec` head for scanned Arabic |
| `docling-project/docling` | `v2.140.0` (`27a278042c72ac27dcbe83750ee1ad4d908dc18e`) | `MIT` | **Compatible** | Optional layout/table parser for complex multi-page invoices |
| `QwenLM/Qwen3-VL` | `99f0fb6fd3ec6abbaa3fd8ccbfdddcbe0d3a55f5` | `Apache-2.0` | **Compatible** | Optional VLM fallback for unstructured documents (strictly untrusted staging output) |
| `datalab-to/surya` | `f6eb7ffd4d96fb81e062fb7fc0983b1c36f30627` | Code: `GPL-3.0`; Weights: **AI Pubs Open Rail-M** (free only under `$2M` revenue) | **Incompatible** | **Rejected (`Verified`)** |

### 2.1 OpenCV build substitution — `opencv-python-headless` for `opencv-python`

`rapidocr-onnxruntime==1.4.4` declares `opencv-python>=4.5.1.48`, which resolves
to the **GUI** wheel. That wheel bundles the Qt5/xcb GUI stack and links
`libGL.so.1`, which is not present in a minimal Linux container, so `import cv2`
fails with `ImportError: libGL.so.1: cannot open shared object file`.

Evidence that the GUI stack is unnecessary for this project:

- `nm -D --undefined-only cv2.abi3.so` shows the extension module references
  **zero** `gl*` symbols. The `libGL.so.1` requirement comes entirely from the
  bundled Qt5 GUI libraries, not from `cv2` itself.
- `dz_erp/intake_ocr.py` uses only the core `cv2` API (`imread`, `resize`,
  `cvtColor`, `threshold`, …) and never any GUI / `highgui` function.

**Resolution.** `opencv-python-headless==4.11.0.86` provides the identical
`cv2` module and identical `numpy` ABI requirements with no OpenGL or Qt
dependency. It is the declared dependency in `pyproject.toml`, and the GUI
`opencv-python` wheel is **excluded** from `requirements.lock.txt`. Because that
lockfile enumerates the complete transitive closure, it is installed with
`--no-deps` — which is both standard lockfile practice and the mechanism that
makes the substitution order-independent.

> Install-order was explicitly tested and **rejected** as a solution: two
> independent clean installs, one with `opencv-python-headless` first and one
> with it last in the requirements file, both ended with the GUI build owning
> `site-packages/cv2/`. pip does not preserve requirements-file order for these
> two packages.

**Known, documented deviation.** With this lockfile, `pip check` reports exactly
one line:

```
rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.
```

This is an intentional substitution of an upstream-only declaration. It is
neither concealed nor worked around with a blanket `--no-deps` on
`rapidocr-onnxruntime`. Instead the graph is validated explicitly and more
strictly than `pip check` by `tools/verify_dependency_graph.py`, which fails on
*any* unsatisfied requirement other than this single allow-listed substitution,
asserts that the installed `cv2` really is the headless build (the GUI payload
directory `opencv_python.libs/` must be absent), and asserts that both `cv2` and
`RapidOCR` import successfully.

**System library requirements.**

| Class | Requirement |
| :--- | :--- |
| Mandatory runtime | **None.** Every pinned runtime dependency is a pure-Python or wheel package. |
| Optional (GUI OpenCV build only) | `libgl1`, `libglib2.0-0` — only needed if the GUI `opencv-python` build is deliberately reinstalled for interactive debugging. |
| Source for `libGL` | No PyPI package provides it (`https://pypi.org/pypi/libgl1/json` returns `404`); a system package manager is the only source. |

### 2.2 Vendored benchmark font (deterministic rendering)

`dz_erp/benchmark.py` renders the synthetic Algerian invoices that the OCR
accuracy assertions are measured against. It previously resolved its font from a
hardcoded **system** path (`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`)
with a silent fallback to Helvetica. That made the measured accuracy a function
of the host image: on a runner with a different DejaVu build, or none at all,
glyph shapes shift and the numbers move — reproduced locally as
`table_line_association_accuracy` dropping from `1.0` to `0.8333`, below the
`0.85` threshold, with no code change.

The font is therefore vendored in-repository and takes precedence:

| File | License | Notes |
| :--- | :--- | :--- |
| `dz_erp/assets/fonts/DejaVuSans.ttf` | Bitstream Vera / DejaVu (permissive; DejaVu changes are public domain) | Full copyright notice retained alongside as `DejaVuSans.COPYRIGHT` |
| `dz_erp/assets/fonts/DejaVuSans.COPYRIGHT` | — | Upstream attribution, retained verbatim |

The system path remains only as a fallback for environments that predate the
vendored copy. `test_benchmark_font_is_bundled_and_resolves_without_the_system_
font` and `test_benchmark_rendering_is_reproducible` prevent regression.

### 2.3 Deterministic OCR inference

`rapidocr-onnxruntime` builds its ONNX Runtime sessions with
`intra_op_num_threads = -1`, i.e. one thread per core.  Parallel floating-point
reductions then execute in a core-count-dependent order, so the *same image* can
produce slightly different logits - and therefore a different recognised
character - on a different machine.  OpenCV's internal parallel loops are the
same class of problem inside RapidOCR's preprocessing.

The measured effect was real: the benchmark scored
`overall_field_exact_match_rate = 0.9097` on the authoring machine and `0.8958`
on a GitHub Actions runner, with no code change between them, which is what made
the `>= 0.90` assertion fail there.

**Fix.** `dz_erp.intake_ocr.get_rapid_ocr()` constructs the engine with
`intra_op_num_threads=1` and `inter_op_num_threads=1` and pins
`cv2.setNumThreads(1)`.  Single-threaded reductions have a fixed order, so
inference is bit-identical on every CPU.  The cost is throughput, which is
irrelevant here: the engine serves single-document intake, not batch processing.

**Residual noise.** ONNX Runtime, OpenCV and PDFium still dispatch SIMD kernels
at runtime based on host CPU features, so a *borderline* glyph can still be
recognised differently on a different machine.  The benchmark thresholds are
therefore floors with a deliberate margin below the measured value (documented
at the assertion site) rather than tight bounds on it.  A genuine regression
moves the metric by tens of points, not by single fields.

---

**CI verification.** This resolution is confirmed on a clean GitHub Actions runner
(Ubuntu, Python 3.11 and 3.12, no system graphics libraries installed): run
`39077748167` (`push`) and `39077751326` (`pull_request`) both succeeded with the
install, graph-validation and cv2/RapidOCR import steps green on both versions.

---

## 3. Core Runtime, Web API, Cryptography & Test Dependencies (`Verified`)

Every Python package in the environment is pinned to an exact version in `requirements.lock.txt` **and to exact artifact digests** in `requirements.lock.hashes.txt` (see §4):

- `fastapi==0.115.12` (`MIT`)
- `pydantic==2.11.1` & `pydantic_core==2.33.0` (`MIT`)
- `uvicorn==0.34.0` (`BSD-3-Clause`)
- `httpx==0.28.1` (`BSD-3-Clause`)
- `python-multipart==0.0.20` (`Apache-2.0`)
- `cryptography==44.0.2` (`Apache-2.0` / `BSD-3-Clause`) — used for `Fernet` (`AES-128-CBC` + `HMAC-SHA256`) encryption at rest in `PrivateDocumentVault` and encrypted database backups
- `pytest==8.3.5` & `pytest-cov==6.0.0` (`MIT`)
- `pytest-asyncio==1.3.0` (`Apache-2.0`) — coroutine test support for the bounded-upload reader
- `ruff==0.14.0` (`MIT`) — static check used by CI

---

## 4. Artifact integrity: hash-pinned installation (`Verified`)

**Gap this closes.** `requirements.lock.txt` pins versions, not bytes. A version pin says
"install SQLAlchemy 2.0.40"; it does not say *which* SQLAlchemy 2.0.40. A compromised index, a
misconfigured internal mirror or a hijacked DNS answer satisfies the pin with a different wheel.

**Control.** `requirements.lock.hashes.txt` lists every pinned distribution with the SHA-256 of
every distribution file PyPI published for that version, and both CI jobs install with:

```
pip install --no-deps --require-hashes -r requirements.lock.hashes.txt
```

| Property | Value |
|---|---|
| Pinned distributions | 51 (matching `requirements.lock.txt` exactly) |
| Total digests | 1 277 |
| Algorithms | `sha256` only |
| Coverage | every authentic file per version (all platform/Python wheels + sdist) |
| Generator | `tools/generate_hash_lock.py --write` (PyPI JSON API) |
| Drift check in CI | `tools/generate_hash_lock.py --check` |
| Drift check in tests | `tests/test_phase19_dependency_integrity.py` (15 tests) |

All-platform digests are included deliberately: a per-platform hash set would either break the
Windows CI job or force separate lockfiles for each OS. Including every genuine file for the
pinned version still rejects anything that is not a real release of that version, which is the
threat being addressed.

### 4.1 Verified behaviour (executed, not assumed)

| Check | Result |
|---|---|
| Clean install of all 51 packages from the hashed lockfile | **success** |
| Full test suite inside that separately-built environment | **435 passed** |
| `pip check` in that environment | only the documented `opencv-python` substitution |
| Install with one digest deliberately altered | **rejected**, exit 1, "THESE PACKAGES DO NOT MATCH THE HASHES" |
| Hash lockfile truncated to simulate generation drift | `--check` fails; 3 tests fail |

### 4.2 What hash pinning does not cover

* **Publisher compromise.** Hashes pin bytes; they do not vet who published them. A malicious
  genuine release of an already-pinned version would match. Provenance attestations
  (PEP 740 / sigstore) are not implemented, so this remains an accepted residual risk (B2).
* **Anything installed outside the lockfile.** A developer running `pip install <pkg>` by hand is
  not covered; the rule is that CI and deployments install only from the hash lockfile.
* **The OpenCV substitution remains a metadata deviation.** It is allow-listed in
  `tools/verify_dependency_graph.py` with an explanation (§2.1), not concealed, and the
  substitution is itself hash-pinned.

### 4.3 Offline / air-gapped deployment

The lockfile makes an internal mirror viable but does **not** provision one. For a deployment
that cannot reach PyPI, the infrastructure prerequisite is:

1. mirror the exact 51 pinned artifacts (the JSON API records the filenames), and
2. serve them from an internal index, keeping `--require-hashes` enabled — a mirror that serves
   different bytes is then rejected rather than trusted.

No mirror is configured in this repository, and none is claimed to exist.
