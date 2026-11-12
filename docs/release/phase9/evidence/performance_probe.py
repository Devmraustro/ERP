#!/usr/bin/env python3
"""Phase 9G - measured latency / throughput / memory on synthetic data.

No number in the report is estimated: this script produces every one of them.

It measures, against a real uvicorn server over real TCP (not the in-process test
client, so the network stack is included) plus a direct in-process OCR loop:

  * request latency percentiles for /healthz, token issuance, an authenticated
    listing and the trial-balance report;
  * OCR wall-clock time per page on a synthetic Arabic/French invoice PDF produced
    by the project's own generator;
  * peak RSS of the server process;
  * database size after the synthetic load.

Usage: ERP_ROOT=/tree ERP_VENV=/venv python performance_probe.py
"""

from __future__ import annotations

import http.client
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Default to the checkout this script lives in, so the drill is reproducible from
# any copy of the tree; ERP_ROOT overrides it for a separate deployment.
ROOT = Path(os.environ.get("ERP_ROOT") or Path(__file__).resolve().parents[4])
VENV = Path(os.environ.get("ERP_VENV") or Path(sys.executable).parent.parent)
PORT = int(os.environ.get("ERP_PERF_PORT", "8097"))

sys.path.insert(0, str(ROOT))


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def timed(call, repeat: int) -> dict:
    samples: list[float] = []
    statuses: list[int] = []
    for _ in range(repeat):
        started = time.perf_counter()
        result = call()
        samples.append((time.perf_counter() - started) * 1000.0)
        statuses.append(result if isinstance(result, int) else result.status)
    return {
        "n": repeat,
        "p50_ms": round(statistics.median(samples), 2),
        "mean_ms": round(statistics.fmean(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "max_ms": round(max(samples), 2),
        "statuses": sorted(set(statuses)),
    }


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="phase9g-"))
    env = dict(os.environ)
    env.update(
        LD_LIBRARY_PATH="/tmp/py312/lib:/tmp/py312deps/lib",
        SSL_CERT_FILE="/etc/ssl/certs/ca-certificates.crt",
        PYTHONPATH=str(ROOT),
        ERP_DB_PATH=str(work / "perf.sqlite3"),
        ERP_VAULT_PATH=str(work / "vault"),
    )
    boot = subprocess.run(
        [str(VENV / "bin" / "python"), "-m", "dz_erp.serve", "--bootstrap-admin", "admin",
         "--company-name", "Perf Co"],
        input="Perf#2026x\nPerf#2026x\n", text=True, env=env, capture_output=True,
    )
    print(json.dumps({"event": "bootstrap", "tail": (boot.stdout or boot.stderr).strip().splitlines()[-1:]}))

    server = subprocess.Popen(
        [str(VENV / "bin" / "python"), "-m", "dz_erp.serve", "--host", "127.0.0.1",
         "--port", str(PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results: dict = {}
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1)
                conn.request("GET", "/healthz")
                if conn.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            print(json.dumps({"event": "error", "detail": "server did not start"}))
            return 1

        def request(method: str, path: str, body: bytes | None = None,
                    headers: dict | None = None) -> int:
            conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            merged = {"Host": f"127.0.0.1:{PORT}", "Connection": "close"}
            merged.update(headers or {})
            conn.request(method, path, body=body, headers=merged)
            response = conn.getresponse()
            response.read()  # drain so the socket closes cleanly
            conn.close()
            return response.status

        results["healthz"] = timed(lambda: request("GET", "/healthz"), 200)

        token_holder: dict = {}

        def login() -> int:
            conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            body = json.dumps({"username": "admin", "password": "Perf#2026x"}).encode()
            conn.request("POST", "/api/v1/auth/token", body=body,
                         headers={"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json",
                                  "Connection": "close"})
            response = conn.getresponse()
            payload = json.loads(response.read())
            conn.close()
            token_holder["token"] = payload.get("access_token", "")
            return response.status

        results["token_issue"] = timed(login, 30)
        auth = {"Authorization": f"Bearer {token_holder.get('token', '')}"}
        results["authenticated_listing"] = timed(
            lambda: request("GET", "/api/v1/partners", headers=auth), 100)
        results["trial_balance"] = timed(
            lambda: request("GET", "/api/v1/accounting/trial-balance", headers=auth), 50)

        # Concurrent load: 20 threads x 25 requests against /healthz.
        import threading

        concurrency_latencies: list[float] = []
        errors: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            local: list[float] = []
            local_errors: list[int] = []
            for _ in range(25):
                started = time.perf_counter()
                try:
                    status = request("GET", "/healthz")
                except OSError:
                    status = 0
                local.append((time.perf_counter() - started) * 1000.0)
                if status != 200:
                    local_errors.append(status)
            with lock:
                concurrency_latencies.extend(local)
                errors.extend(local_errors)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        t0 = time.perf_counter()
        [t.start() for t in threads]
        [t.join() for t in threads]
        elapsed = time.perf_counter() - t0
        results["concurrency"] = {
            "threads": 20,
            "requests": len(concurrency_latencies),
            "wall_seconds": round(elapsed, 2),
            "rps": round(len(concurrency_latencies) / elapsed, 1),
            "p50_ms": round(statistics.median(concurrency_latencies), 2),
            "p95_ms": round(percentile(concurrency_latencies, 0.95), 2),
            "max_ms": round(max(concurrency_latencies), 2),
            "non_200": len(errors),
        }

        results["server_peak_rss_kb"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        results["db_bytes"] = (work / "perf.sqlite3").stat().st_size
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    # OCR throughput, in-process, on the project's own synthetic invoice renderer.
    try:
        from dz_erp.benchmark import render_synthetic_invoice_pdf
        from dz_erp.intake_ocr import extract_document_ocr

        pdf_dir = work / "pdfs"
        pdf_dir.mkdir()
        spec = {
            "invoice_number": "FA-2026-0042",
            "supplier_name": "SARL Perf Fournitures",
            "supplier_nif": "001216099123456",
            "supplier_nis": "099116010123456",
            "supplier_ai": "16019912345",
            "supplier_rc": "16/00-0991234B12",
            "invoice_date": "2026-02-01",
            "payment_method": "Virement",
            "arabic_header": "فاتورة رقم 2026/0042",
            "lines": [
                {"sku": "SKU-001", "description": "Fournitures de bureau", "qty": 10,
                 "unit_price_dzd": "1000.00", "tax_rate": 19, "line_ht_dzd": "10000.00"},
            ],
            "amount_ht_dzd": "10000.00",
            "amount_tva_dzd": "1900.00",
            "amount_timbre_dzd": "0.00",
            "amount_ttc_dzd": "11900.00",
        }
        timings = []
        for index in range(3):
            pdf_bytes = render_synthetic_invoice_pdf(spec)
            pdf_path = pdf_dir / f"invoice_{index}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            started = time.perf_counter()
            extract_document_ocr(pdf_path.read_bytes(), mime_type="application/pdf")
            timings.append(round(time.perf_counter() - started, 2))
        results["ocr_native_text_seconds"] = timings
        results["ocr_native_text_mean_seconds"] = round(statistics.fmean(timings), 2)

        # The neural path: force rasterisation + RapidOCR inference on the same PDF.
        image_timings = []
        sample = (pdf_dir / "invoice_0.pdf").read_bytes()
        for _ in range(2):
            started = time.perf_counter()
            extract_document_ocr(sample, mime_type="application/pdf", force_image_ocr=True)
            image_timings.append(round(time.perf_counter() - started, 2))
        results["ocr_neural_seconds"] = image_timings
        results["ocr_neural_mean_seconds"] = round(statistics.fmean(image_timings), 2)
    except Exception as exc:
        results["ocr_error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps({"event": "performance", **results}, indent=1, default=str))
    (work / "results.json").write_text(json.dumps(results, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
