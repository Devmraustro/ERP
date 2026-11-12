#!/usr/bin/env python3
"""Phase 8C harness - TLS termination + reverse proxy in front of the real app.

What this does (all locally, nothing leaves the machine):
  1. generates a throw-away CA and a ``localhost`` server certificate;
  2. starts the real application with ``python -m dz_erp.serve``-equivalent settings
     (uvicorn, plain HTTP on 127.0.0.1:8099) configured with
     ``ERP_ALLOWED_HOSTS=localhost,127.0.0.1`` and ``ERP_TRUSTED_PROXIES=127.0.0.1``;
  3. starts a TLS-terminating reverse proxy on 127.0.0.1:8443 that forwards to the
     backend, overwrites ``X-Forwarded-For`` with the real peer, sets
     ``X-Forwarded-Proto: https`` and adds ``Strict-Transport-Security``;
  4. starts a plain-HTTP listener on 127.0.0.1:8080 that 301-redirects to HTTPS;
  5. runs the checks and prints a PASS/FAIL/NOT EXECUTED line for each.

Honest limits, printed as part of the output:
  * the certificate is self-signed, so "validates against the public trust store"
    is NOT EXECUTED (it cannot be, without a publicly trusted certificate);
  * HSTS is emitted by this harness's edge, not by the application - the app is
    designed to sit behind a TLS-terminating proxy, so app-level HSTS is
    NOT IMPLEMENTED BY DESIGN and is reported as such.

Usage:  ERP_ROOT=/path/to/tree ERP_VENV=/path/to/venv python tls_proxy_harness.py
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("ERP_ROOT", "/tmp/gate9"))
VENV = Path(os.environ.get("ERP_VENV", "/tmp/v9"))
BACKEND_PORT = int(os.environ.get("ERP_BACKEND_PORT", "8099"))
TLS_PORT = int(os.environ.get("ERP_TLS_PORT", "8443"))
REDIRECT_PORT = int(os.environ.get("ERP_REDIRECT_PORT", "8080"))

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, verdict: str, detail: str) -> None:
    RESULTS.append((name, verdict, detail))
    print(f"[{verdict:^13}] {name}: {detail}", flush=True)


# ---------------------------------------------------------------------------
# certificates
# ---------------------------------------------------------------------------
def make_certificates(out: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ERP Phase-8C Test CA")])
    now = datetime.now(timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name).public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (out / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    (out / "server.pem").write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    (out / "server.key").write_bytes(srv_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    os.chmod(out / "server.key", 0o600)
    return out / "ca.pem", out / "server.pem", out / "server.key"


# ---------------------------------------------------------------------------
# reverse proxy
# ---------------------------------------------------------------------------
class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the harness output readable
        pass

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        peer = self.client_address[0]
        headers = {k: v for k, v in self.headers.items()}
        # The edge owns the client identity: whatever the caller sent is replaced.
        headers["X-Forwarded-For"] = peer
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = headers.get("Host", "")
        conn = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=15)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(key, value)
            # Edge-owned hardening header.
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            conn.close()

    do_GET = _forward
    do_POST = _forward


class RedirectHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        host = self.headers.get("Host", "localhost").split(":")[0]
        self.send_response(301)
        self.send_header("Location", f"https://{host}:{TLS_PORT}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve_https(cert: Path, key: Path) -> ThreadingHTTPServer:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    httpd = ThreadingHTTPServer(("127.0.0.1", TLS_PORT), ProxyHandler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def serve_redirect() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", REDIRECT_PORT), RedirectHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def wait_port(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def https_request(context: ssl.SSLContext, method: str, path: str, body: bytes | None = None,
                  headers: dict | None = None, host: str = f"localhost:{TLS_PORT}"):
    conn = http.client.HTTPSConnection("127.0.0.1", TLS_PORT, context=context, timeout=15)
    merged = {"Host": host, "Connection": "close"}
    merged.update(headers or {})
    conn.request(method, path, body=body, headers=merged)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, dict(response.getheaders()), payload


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="phase8c-"))
    ca_pem, srv_pem, srv_key = make_certificates(work)
    db = work / "erp.sqlite3"
    env = dict(os.environ)
    env.update(
        LD_LIBRARY_PATH="/tmp/py312/lib:/tmp/py312deps/lib",
        SSL_CERT_FILE="/etc/ssl/certs/ca-certificates.crt",
        PYTHONPATH=str(ROOT),
        ERP_DB_PATH=str(db),
        ERP_VAULT_PATH=str(work / "vault"),
        ERP_ALLOWED_HOSTS="localhost,127.0.0.1",
        ERP_TRUSTED_PROXIES="127.0.0.1",
        ERP_AUTH_FAILURE_LIMIT="3",
        ERP_AUTH_FAILURE_WINDOW_SECONDS="120",
    )
    print(f"== staging workdir: {work}")
    boot = subprocess.run(
        [str(VENV / "bin" / "python"), "-m", "dz_erp.serve", "--bootstrap-admin", "admin",
         "--company-name", "Staging Co"],
        input="Staging#2026x\nStaging#2026x\n", text=True, env=env, capture_output=True,
    )
    print("== bootstrap:", (boot.stdout or boot.stderr).strip().splitlines()[-1:])

    backend = subprocess.Popen(
        [str(VENV / "bin" / "python"), "-m", "dz_erp.serve", "--host", "127.0.0.1",
         "--port", str(BACKEND_PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    https_srv = serve_https(srv_pem, srv_key)
    redirect_srv = serve_redirect()
    try:
        if not wait_port(BACKEND_PORT):
            record("backend start", "FAIL", "uvicorn did not open the port")
            return 1
        time.sleep(0.5)

        trusted = ssl.create_default_context(cafile=str(ca_pem))

        # 1. HTTPS through the proxy, certificate validated against our CA
        status, headers, payload = https_request(trusted, "GET", "/healthz")
        record("HTTPS request via reverse proxy",
               "PASS" if status == 200 and b'"status":"ok"' in payload else "FAIL",
               f"status={status} body={payload[:40]!r}")
        record("HSTS present on the TLS edge",
               "PASS" if "Strict-Transport-Security" in headers else "FAIL",
               f"hsts={headers.get('Strict-Transport-Security')!r} (emitted by the harness edge, "
               "not by the application)")

        # 2. certificate validation: untrusted (default store) must fail
        try:
            strict = ssl.create_default_context()
            https_request(strict, "GET", "/healthz")
            record("self-signed certificate rejected by the default trust store", "FAIL",
                   "the client accepted an untrusted certificate")
        except ssl.SSLCertVerificationError as exc:
            record("self-signed certificate rejected by the default trust store", "PASS",
                   f"{type(exc).__name__}: {str(exc)[:80]}")
        record("certificate valid against the PUBLIC trust store", "NOT EXECUTED",
               "requires a publicly trusted certificate; a self-signed CA cannot prove it")

        # 3. plain HTTP against the TLS port must not be served
        try:
            conn = http.client.HTTPConnection("127.0.0.1", TLS_PORT, timeout=10)
            conn.request("GET", "/healthz")
            conn.getresponse()
            record("plaintext request to the TLS port", "FAIL", "served without TLS")
        except Exception as exc:
            record("plaintext request to the TLS port", "PASS", f"refused ({type(exc).__name__})")

        # 4. HTTP -> HTTPS redirect at the edge
        conn = http.client.HTTPConnection("127.0.0.1", REDIRECT_PORT, timeout=10)
        conn.request("GET", "/healthz", headers={"Host": "localhost"})
        redirect = conn.getresponse()
        location = redirect.getheader("Location") or ""
        record("HTTP to HTTPS redirect",
               "PASS" if redirect.status in (301, 302, 308) and location.startswith("https://") else "FAIL",
               f"status={redirect.status} location={location} (edge behaviour; the app itself does not redirect)")

        # 5. Host allow-list through the proxy
        status, _, _ = https_request(trusted, "GET", "/healthz", host="evil.example")
        record("Host header injection through the proxy", "PASS" if status == 400 else "FAIL",
               f"status={status}")
        status, _, _ = https_request(trusted, "GET", "/healthz", host="localhost")
        record("allow-listed Host accepted", "PASS" if status == 200 else "FAIL", f"status={status}")

        # 6. API connectivity through TLS
        status, _, payload = https_request(
            trusted, "POST", "/api/v1/auth/token",
            body=json.dumps({"username": "admin", "password": "Staging#2026x"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        token = ""
        if status == 200:
            token = json.loads(payload).get("access_token", "")
        record("token issuance over HTTPS through the proxy",
               "PASS" if status == 200 and token.startswith("dzsess_") else "FAIL",
               f"status={status} token_prefix={token[:8] or '-'}")
        if token:
            status, _, _ = https_request(trusted, "GET", "/api/v1/auth/session",
                                         headers={"Authorization": f"Bearer {token}"})
            record("authenticated call over HTTPS", "PASS" if status == 200 else "FAIL", f"status={status}")

        # 7. X-Forwarded-For spoofing by the client: the edge overwrites it, so the
        #    per-client failure budget cannot be rotated by editing the header.
        codes = []
        for i in range(6):
            status, _, _ = https_request(
                trusted, "POST", "/api/v1/auth/token",
                body=json.dumps({"username": "admin", "password": "nope"}).encode(),
                headers={"Content-Type": "application/json",
                         "X-Forwarded-For": f"198.51.100.{i}"},
            )
            codes.append(status)
        record("X-Forwarded-For rotation cannot bypass the limiter",
               "PASS" if 429 in codes else "FAIL", f"statuses={codes}")

        # 8. X-Forwarded-Proto spoofing directly against the backend (bypassing the edge)
        conn = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=10)
        conn.request("GET", "/healthz", headers={"Host": "evil.example",
                                                 "X-Forwarded-Proto": "https"})
        direct = conn.getresponse()
        record("X-Forwarded-Proto cannot whitelist a bad Host",
               "PASS" if direct.status == 400 else "FAIL", f"status={direct.status}")

        # 9. direct backend access with a spoofed XFF (peer is 127.0.0.1 = trusted here)
        conn = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=10)
        conn.request("GET", "/healthz", headers={"Host": "localhost",
                                                 "X-Forwarded-For": "203.0.113.77"})
        record("direct peer reaching the backend", "OBSERVED",
               f"status={conn.getresponse().status} (the deployment must not expose the backend port)")
    finally:
        https_srv.shutdown()
        redirect_srv.shutdown()
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()

    print("\n== summary")
    for name, verdict, detail in RESULTS:
        print(f"  {verdict:^13} | {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
