# SPDX-License-Identifier: LGPL-3.0-only
"""Phase 10 — the OpenCV dependency contract.

`pip check` reports:

    rapidocr-onnxruntime 1.4.4 requires opencv-python, which is not installed.

while the OCR pipeline works. That is not a contradiction to be waved away; it is a
naming disagreement that has to be *proven* harmless or fixed. This module pins down
the evidence:

* what the OCR engine declares and what it actually imports;
* that the installed ``cv2`` comes from the headless distribution and is a
  ``GUI: NONE`` build (the only kind that imports on a server without libGL);
* that the engine only uses non-GUI OpenCV APIs;
* that a real inference extracts real fields through the headless build;
* that the pinned *non-headless* wheel cannot even be imported here, which is why
  changing the lock to satisfy the metadata would break every headless deployment;
* that the decision is written down in the lock file itself.

The remediation decision recorded by these tests: **keep the headless wheel**. The
alternative - pinning ``opencv-python`` - is not a fix, it is an outage.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from importlib import metadata

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RAPIDOCR_DECLARATION = "opencv-python>=4.5.1.48"
HEADLESS_DISTRIBUTION = "opencv-python-headless"
GUI_ONLY_CALLS = ("imshow", "namedWindow", "waitKey", "destroyAllWindows", "VideoCapture")


def test_the_engine_declares_the_non_headless_wheel() -> None:
    """This is the *cause* of the `pip check` line; recorded so it cannot drift."""
    requires = metadata.distribution("rapidocr-onnxruntime").requires or []
    opencv_requirements = [r for r in requires if r.split()[0].startswith("opencv")]
    assert opencv_requirements == [RAPIDOCR_DECLARATION]


def test_the_installed_cv2_is_the_headless_build() -> None:
    cv2 = pytest.importorskip("cv2")
    build = cv2.getBuildInformation()
    gui_line = next((line for line in build.splitlines() if line.strip().startswith("GUI:")), "")
    assert "NONE" in gui_line, f"expected a GUI-less OpenCV build, got: {gui_line!r}"
    # And the distribution that supplies cv2 must be the headless one.
    headless = metadata.distribution(HEADLESS_DISTRIBUTION)
    assert headless.version.startswith("4.")
    # The GUI distribution must not be installed alongside it: two wheels writing the
    # same cv2/ directory is how "works on my machine" starts.
    try:
        metadata.distribution("opencv-python")
    except metadata.PackageNotFoundError:
        pass
    else:  # pragma: no cover - this would be a real packaging defect
        raise AssertionError("opencv-python is installed next to opencv-python-headless")


def test_the_engine_uses_no_gui_only_opencv_api() -> None:
    """A GUI call would make the headless wheel insufficient in practice."""
    package = pathlib.Path(pytest.importorskip("rapidocr_onnxruntime").__file__).parent
    offenders = []
    for source in package.rglob("*.py"):
        text = source.read_text(encoding="utf-8", errors="ignore")
        for call in GUI_ONLY_CALLS:
            if re.search(rf"cv2\.{call}\s*\(", text):
                offenders.append(f"{source.name}: cv2.{call}")
    assert offenders == [], f"OCR engine calls GUI-only OpenCV APIs: {offenders}"


def test_a_real_inference_extracts_fields_through_the_headless_build() -> None:
    """The decisive test: the pipeline produces values, not an ImportError."""
    from dz_erp.benchmark import render_synthetic_invoice_pdf
    from dz_erp.intake_ocr import extract_document_ocr

    spec = {
        "invoice_number": "FA-2026-0777",
        "supplier_name": "SARL Remediation",
        "supplier_nif": "001216099123456",
        "supplier_nis": "099116010123456",
        "supplier_ai": "16019912345",
        "supplier_rc": "16/00-0991234B12",
        "invoice_date": "2026-05-01",
        "payment_method": "BANK_TRANSFER",
        "lines": [
            {
                "sku": "SKU-1",
                "description": "Fournitures",
                "qty": 2,
                "unit_price_dzd": "1500.00",
                "tax_rate": 19,
                "line_ht_dzd": "3000.00",
            }
        ],
        "amount_ht_dzd": "3000.00",
        "amount_tva_dzd": "570.00",
        "amount_timbre_dzd": "0.00",
        "amount_ttc_dzd": "3570.00",
    }
    pdf = render_synthetic_invoice_pdf(spec)
    result = extract_document_ocr(pdf, mime_type="application/pdf", force_image_ocr=True)

    assert result["engine_name"], "engine identity must be recorded"
    assert result["fields"], "no fields were extracted"
    supplier = result["fields"]["supplier_name"]["normalized_value"]
    assert "Remediation" in supplier.replace(" ", "")
    assert result["fields"]["invoice_number"]["normalized_value"].replace(" ", "")
    assert result["latency_ms"] >= 0


def test_the_installed_cv2_does_not_link_libgl() -> None:
    """The property that makes the headless wheel correct on a server.

    ``opencv-python``'s ``cv2`` links ``libGL.so.1``; ``opencv-python-headless`` does
    not. This checks the loaded shared object's dynamic dependencies directly, so the
    evidence does not depend on whether *this* host happens to have libGL installed.

    Measured separately, in a throwaway virtual environment, installing the exact
    pinned GUI wheel ``opencv-python==4.11.0.86`` and importing it on this same host
    fails with ``ImportError: libGL.so.1: cannot open shared object file`` - which is
    why the lock keeps the headless wheel (see docs/release/phase10/
    DEPENDENCY_REMEDIATION_REPORT.md).
    """
    cv2 = pytest.importorskip("cv2")
    shared_objects = sorted(pathlib.Path(cv2.__file__).parent.glob("*.so"))
    assert shared_objects, "no cv2 shared object found"
    offenders = []
    for so_path in shared_objects:
        completed = subprocess.run(
            ["ldd", str(so_path)], capture_output=True, text=True, timeout=60
        )
        for line in completed.stdout.splitlines():
            library = line.split("=>")[0].strip().split(" ")[0]
            if library.startswith("libGL") or library.startswith("libEGL"):
                offenders.append(f"{so_path.name} -> {library}")
    assert offenders == [], f"headless cv2 still links a GUI library: {offenders}"


def test_the_lock_records_the_decision() -> None:
    lock = (REPO_ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    assert "opencv-python-headless" in lock
    assert "rapidocr-onnxruntime 1.4.4 requires opencv-python" in lock
    assert "not concealed" in lock.lower() or "NOT concealed" in lock
