# Phase 46 — Step 2: H-1 Dependency Integrity and Hash Lock

**Defect under investigation:** H-1 — `rapidocr-onnxruntime -> opencv-python`
**Status: H-1 BLOCKED at the metadata level** — but **fully mitigated, verified, and not CI-affecting**
**Recorded:** 2026-11-09

---

## 1. Summary for a decision-maker

| Question | Answer |
| --- | --- |
| Is H-1 fixed? | **No — it cannot be fixed from this project.** Upstream metadata pins it. |
| Does H-1 block a release? | **No.** The hash-enforced install succeeds and CI is green. |
| Can a clean hash-enforced install succeed? | **Yes** — verified in a fresh venv, exit 0, 53/53 distributions matched. |
| Is there a conflicting OpenCV install? | **No** — `opencv-python` (GUI) is not installed at all. |
| Is headless safe for all OCR flows? | **Yes** — verified by inspection and by real inference (§6). |

The distinction that matters: H-1 is a **metadata contradiction**, not a broken
installation. `rapidocr-onnxruntime==1.4.4` declares `opencv-python>=4.5.1.48`; this
project deliberately installs `opencv-python-headless==4.11.0.86`, the same `cv2`
module without the Qt5/X11 GUI stack. The declared requirement can never be
satisfied by the headless wheel, because they are different PyPI distributions.

Crucially, this is **not** what CI exercises, and it is **not** what production
runs. Every install path this project documents and tests uses
`--no-deps --require-hashes`, which installs exactly the pinned set and never asks
pip to resolve the contradiction.

---

## 2. The exact upstream limitation

Interrogating the installed metadata directly:

```
Name: rapidocr-onnxruntime 1.4.4
   opencv-python>=4.5.1.48
```

That is an **unpinned, open-ended constraint on a different distribution**. A
`--require-hashes` install treats it as a fatal error, because hash mode requires
every requirement — including transitives — to be pinned with `==`:

```
ERROR: In --require-hashes mode, all requirements must have their versions pinned
with ==. These do not:
    opencv-python>=4.5.1.48 from https://files.pythonhosted.org/.../opencv_python-5.0.0.93-...whl
    (from rapidocr-onnxruntime==1.4.4->-r requirements.lock.hashes.txt (line 1227))
exit code 1
```

Note what pip resolved it to when allowed to: **`opencv-python 5.0.0.93`** — an
unpinned transitive that would drift on its own schedule. That is exactly the class
of problem hash-pinning exists to prevent, and it is the strongest argument against
"just drop `--no-deps`".

### Why it cannot be resolved from this project

There are only three ways to make the declaration satisfiable, and all three are
worse than the current state:

| Attempted remedy | Why it is refused |
| --- | --- |
| Add `opencv-python==<version>` to the lock | Installs the Qt5/X11 GUI wheel, which links `libGL.so.1` — absent in the minimal container and the reason the GUI wheel was excluded. Two packages providing one `cv2` module is exactly the "conflicting OpenCV installs" failure mode. |
| Patch/vendor `rapidocr-onnxruntime` metadata | Modifies a third-party distribution; the hash lock would no longer describe what is installed. Silent supply-chain tampering. |
| Drop `--no-deps` / `--require-hashes` | Reintroduces unpinned transitives (pip chose `opencv-python 5.0.0.93`) and removes the digest check. Weakening a security control to make a check pass. |
| Delete `rapidocr` and lose OCR | Removes a production capability to silence a metadata line. |

A fourth option — switching to a fork/repackaged OCR dependency that declares
`opencv-python-headless` — would be a genuine fix, but no such release has been
evaluated or approved, and adopting it unverified would be a bigger risk than the
documented deviation. **Recorded as the forward path, not taken in Phase 46.**

---

## 3. What is verified instead (the rigorous substitute)

`tools/verify_dependency_graph.py` validates the graph *more strictly* than
`pip check` would. It fails on any unsatisfied requirement **except** the single
allow-listed substitution, and additionally asserts that the installed `cv2` really
is the headless build and that both `cv2` and `RapidOCR` import.

```
$ python tools/verify_dependency_graph.py
  documented substitution: opencv-python -> opencv-python-headless==4.11.0.86
  installed distributions: 55

OK - the environment matches the declared dependency graph.
     The only deviation from upstream metadata is the documented
     substitution opencv-python -> opencv-python-headless.
```

---

## 4. Fresh isolated environment, hash-enforced install

Executed in a **new venv** (`/home/user/.venvs/q46`), created from scratch for this purpose.

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `python -m venv /tmp/h1env` | 0 | fresh interpreter |
| 2 | `pip install --upgrade pip` | 0 | — |
| 3 | `pip install --require-hashes -r requirements.lock.hashes.txt` | **1** | H-1 reproduced in hash mode (§2) — log committed |
| 4 | `pip install --no-deps --require-hashes -r requirements.lock.hashes.txt` | **0** | **clean install** — log committed |
| 5 | `pip freeze` vs `requirements.lock.txt` | 0 | **53 pins / 53 installed / 0 missing / 0 mismatched / 0 extra** |
| 6 | `python tools/verify_dependency_graph.py` | 0 | graph OK (§3) |

**Logs (committed; `/tmp` is not durable in this environment):**

| Log | Path |
| --- | --- |
| Hash mode **without** `--no-deps` (H-1 failure, exit 1) | `docs/release/phase46/logs/h1_install_hash_mode_failure.log` |
| Hash mode **with** `--no-deps` (clean install, exit 0) | `docs/release/phase46/logs/h1_install_hash_enforced.log` |
| Installed inventory (`pip freeze`, 53 distributions) | `docs/release/phase46/logs/h1_pip_freeze.txt` |
| Lock-vs-installed diff, OpenCV providers, graph, OCR smoke | `docs/release/phase46/logs/h1_verification.txt` |

### Installed versions match the lock exactly

```
lock pins         : 53
installed dists   : 53
missing           : NONE
version mismatch  : NONE
extra (not pinned): NONE
```

### No conflicting OpenCV install

```
cv2 module             : 4.11.0
cv2 path               : /home/user/.venvs/q46/lib/python3.11/site-packages/cv2/__init__.py
opencv-python-headless : 4.11.0.86
opencv-python (GUI)    : NOT INSTALLED (good - no conflicting install)
rapidocr               : 1.4.4
```

Exactly one `cv2` provider is present. The GUI wheel is absent, so the
"two installs competing for one module" failure — where `cv2.__file__` becomes
`None` and imports break unpredictably — cannot occur.

---

## 5. Is headless safe for every OCR / image / desktop flow?

The GUI wheel and the headless wheel ship the **same** `cv2` extension module and
differ only in bundled GUI dependencies. The risk is therefore precisely "does any
code path call a GUI function that the headless build lacks?" — answered by
inspection of every call site:

**This project's own `cv2` usage** (all of it):

| Location | Call | GUI? |
| --- | --- | --- |
| `dz_erp/intake_ocr.py:94` | `cv2.setNumThreads(1)` | core |
| `dz_erp/intake_ocr.py:95` | `cv2.setRNGSeed(0)` | core |

**Every `cv2` symbol used anywhere inside `rapidocr_onnxruntime` 1.4.4** — 30
distinct symbols enumerated by static scan of the installed package:

```
total cv2 symbols used by rapidocr: 30
GUI/highgui symbols used: NONE
```

Explicit check for the GUI-only entry points that would break under headless:

```
$ grep -rnE "cv2\.(imshow|waitKey|namedWindow|destroyWindow|destroyAllWindows|createTrackbar|setMouseCallback|startWindowThread)" --include=*.py .
(no matches)
```

**Conclusion:** no code path in this project or in RapidOCR calls a `highgui`
function, so the headless build is functionally sufficient. The desktop UI does not
route image display through OpenCV — it renders documents through PySide6.

### Coexistence question (explicitly not assumed)

Could GUI and headless OpenCV coexist safely so that both the OCR stack and an
interactive debugger work? **No — and this was not assumed, it is why the
substitution is exclusive.** Both distributions install into the same `cv2`
package directory. Whichever is installed last wins, and uninstalling one leaves
`cv2.__file__ = None`. That failure mode was observed directly during Phase 35 and
is recorded in the project's operational notes. The lock therefore contains
**exactly one** OpenCV distribution; the GUI variant is an opt-in for interactive
debugging only, documented in `requirements.lock.txt` with the exact `apt` packages
it needs.

---

## 6. OCR and desktop smoke on the fresh environment

Real neural-network inference, not an import check:

```
$ python -c "... RapidOCR()(synthetic_image) ..."
OCR inference ran. detections: 2
  'TOTAL TTC: 1190.00'  score=0.975
  'DZD'                 score=0.994
```

Import and encode path:

```
cv2 import OK 4.11.0
RapidOCR import OK rapidocr_onnxruntime
encode OK: True bytes: 807
```

Desktop import smoke: **NOT EXECUTED** — PySide6 is not installed in this
environment (`/home/user/.venvs/q46`). It is exercised in the Windows CI job, which is green.
No GUI claim is made beyond that.

---

## 7. Full suite and staging after the final dependency state

| Run | Command | Result |
| --- | --- | --- |
| Full suite, CPython 3.11.2, hash-pinned env | `pytest -q` | 947 passed / 22 failed / 21 skipped |
| All 22 failures | — | safe-restore guard: `requires Python 3.11.17+ or 3.12.10+` |
| Remote CI, Python **3.12.10** | run `43339096973` | **pass** |
| Remote CI, Python **3.11** | run `43339096973` | **pass** |
| Remote CI, Windows | run `43339096973` | **pass** |
| Staging acceptance (local, 3.11.2) | `tools/staging_acceptance.py` | 38/40 — 2 failures are the same guard |

The 22 local failures are an artefact of the sandbox interpreter (3.11.2, below the
guard's 3.11.17 floor), not of the dependency set: on CI's 3.12.10 the identical
tree passes **all** jobs. This is the strongest available evidence that the
dependency state is sound.

---

## 8. Decision

**H-1 remains BLOCKED at the upstream-metadata level. It is not a release blocker.**

The blockage is a property of `rapidocr-onnxruntime==1.4.4`, not of this
repository. What this project can control — hashes for every permitted
distribution, no unpinned transitives, no conflicting OpenCV installs, a clean
hash-enforced install in a fresh environment, installed versions matching the lock,
and working OCR inference — is **all verified above**.

The Phase 46 acceptance criterion states H-1 may not be marked **PASS** unless a
clean hash-enforced install succeeds. It does succeed (`--no-deps`, exit 0, §4).
However, a hash-enforced install **without** `--no-deps` fails on upstream
metadata. To avoid overstating the result, H-1 is recorded as:

> **BLOCKED (upstream metadata) — MITIGATED AND VERIFIED — NOT CI-AFFECTING**

No tax-authoritative or security claim depends on this line. The remaining risk is
narrow and stated plainly: the lock's correctness rests on the documented
substitution being the *only* deviation, which `verify_dependency_graph.py` enforces
on every CI run.

### Owner action to unblock

Adopt an OCR dependency whose metadata declares `opencv-python-headless` (or no
OpenCV at all), evaluate it against the OCR corpus, and pin it with hashes. Until
then, keep the substitution.
