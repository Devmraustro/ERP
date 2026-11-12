# Phase 10 — OpenCV wheel decision (`opencv-python` vs `opencv-python-headless`)

> **File note.** This document was originally named `DEPENDENCY_REMEDIATION_REPORT.md`.
> It is renamed here because that path now carries the P0 dependency *security*
> remediation report required by the Phase 10 scope. The content below is the
> OpenCV packaging decision and is unchanged.

**Question:** `pip check` says `rapidocr-onnxruntime 1.4.4 requires opencv-python, which
is not installed`, yet OCR works. Should the lock be changed?

**Answer: no. The lock stays on `opencv-python-headless`.** Evidence below.

## 1. The conflict, stated exactly

```
$ pip check
rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.

$ pip install --no-deps --require-hashes -r requirements.lock.hashes.txt   # exit 0
$ python -c "import cv2; print(cv2.__version__)"                           # 4.11.0
```

Installed distributions: `opencv-python-headless 4.11.0.86` (supplies the `cv2`
package), `rapidocr-onnxruntime 1.4.4` (declares `opencv-python>=4.5.1.48`).

## 2. What the engine actually requires

```python
>>> metadata.distribution('rapidocr-onnxruntime').requires
['pyclipper>=1.2.0', 'opencv-python>=4.5.1.48', 'numpy<3.0.0,>=1.19.5', 'six>=1.15.0',
 'Shapely!=2.0.4,>=1.7.1', 'PyYAML', 'Pillow', 'onnxruntime>=1.7.0', 'tqdm']
```

The requirement is a **distribution name**, not an import. The engine's code is:

```python
import cv2          # rapidocr_onnxruntime/main.py, cal_rec_boxes/main.py,
                    # ch_ppocr_cls/text_cls.py, ch_ppocr_det/utils.py, ch_ppocr_rec/text_recognize.py
```

Both wheels install the same `cv2` package and the same import path. Which *functions*
are used is what decides whether the headless build is enough:

| Check | Result |
| --- | --- |
| GUI-only APIs (`imshow`, `namedWindow`, `waitKey`, `destroyAllWindows`, `VideoCapture`) anywhere in `rapidocr_onnxruntime` | **none found** (grep + `tests/test_phase63_dependency_contract.py`) |
| Installed build | `GUI: NONE` (`cv2.getBuildInformation()`) |
| Dynamic dependencies of `cv2/*.so` | no `libGL`/`libEGL` (`ldd`, asserted by test) |

## 3. The decisive experiment

A throwaway virtual environment, installing **the exact pinned GUI wheel**:

```
$ python3.12 -m venv /tmp/vcv
$ /tmp/vcv/bin/pip install opencv-python==4.11.0.86      # exit 0
$ /tmp/vcv/bin/python -c "import cv2"
ImportError: libGL.so.1: cannot open shared object file: No such file or directory
```

Switch the lock to `opencv-python` and **every headless server running this application
fails at import time** — OCR, and anything else that pulls `cv2`, dies. This is the same
missing `libGL.so.1` that blocks the Qt desktop client in this image; it is the normal
state of a minimal Linux server, not an exotic defect.

With the headless wheel, the pipeline runs and produces values (executed, real
inference on a synthetic Algerian invoice, image path forced):

```
engine_name      rapidocr-onnxruntime
model_version    rapidocr-1.4.4-ppocrv4
confidence_kind  engine_line
latency_ms       ~2 260 ms/page (rasterised path)
fields.supplier_name.normalized_value   "SARLRemediation"
fields.invoice_number / invoice_date / supplier_nif / supplier_ai  extracted
```

## 4. Options considered

| Option | Verdict |
| --- | --- |
| **A. Keep `opencv-python-headless`** (current) | **CHOSEN.** Functionally complete, server-safe; the only cost is a `pip check` line whose meaning is "a name is different". |
| B. Switch to `opencv-python` | Rejected: breaks import on any server without libGL (proved above); adds a GUI stack to a headless service. |
| C. Install both | Rejected: two distributions writing the same `cv2/` directory — last-installed wins, nondeterministic, and the GUI one wins often enough to break hosts intermittently. |
| D. `pip install --no-deps` + a local metapackage named `opencv-python` that depends on the headless wheel | Rejected for V1: introduces a privately built distribution into the supply chain, needs its own hash, and hides the same fact from anyone reading the lock. |
| E. Vendor a patched `rapidocr-onnxruntime` with corrected metadata | Rejected for V1: forking a dependency to change a string; must be re-applied on every upgrade. |

## 5. What was changed

**Nothing in the dependency lock.** The change is that the decision is now *tested*:

`tests/test_phase63_dependency_contract.py` (6 tests, all passing) asserts:

1. the engine declares the non-headless wheel (so drift is visible);
2. the installed `cv2` is a `GUI: NONE` build from `opencv-python-headless`;
3. the engine calls no GUI-only OpenCV API;
4. a **real inference extracts real fields** through the headless build;
5. the loaded `cv2` shared objects link **no `libGL`/`libEGL`** (`ldd`);
6. the lock file documents the defect and its resolution rather than concealing it.

## 6. Residual risk

- A future `rapidocr-onnxruntime` release could start using a GUI-only API. Test 3 fails
  immediately in that case — the tripwire exists for exactly this.
- `pip check` will keep reporting this line. The operator runbook and this report say
  why; do not "fix" it with option B.
- `opencv-python-headless` still ships FFmpeg and BLAS shared objects; the image is
  ~90 MB. Acceptable for V1, worth revisiting if image size becomes a constraint.

## 7. Verdict

**Investigated, tested, decided: keep the headless wheel.** The conflict is a packaging
metadata mismatch, not a runtime defect; the alternative would convert a warning into a
production outage.

## 8. Scope boundary — this report covers OpenCV only

The decision above concerns `opencv-python` vs `opencv-python-headless` and nothing
else. It is **not** a clean bill of health for the rest of the lockfile. The Phase 10
audit found 143 distinct advisories across eight other pins (pypdf, pillow, starlette,
cryptography, python-multipart, pdfminer.six, certifi, pytest) — those libraries parse
untrusted supplier documents and are a **P0 production blocker**. See
`DEPENDENCY_REMEDIATION_REPORT.md` for the executed remediation and
`DEPENDENCY_AUDIT_FINDING.md` for the original finding. Do not read the "keep the headless wheel" verdict as applying to them.
