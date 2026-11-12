import os, pathlib, stat, sys, tempfile
sys.path.insert(0, os.environ.get("ERP_ROOT", str(pathlib.Path(__file__).resolve().parents[4])))
from dz_erp.security_audit import JsonlSecurityLog, StructuredRedactingLogger
tmp = pathlib.Path(tempfile.mkdtemp(prefix="phase5-probe-"))
log_path = tmp / "security.jsonl"
sink = JsonlSecurityLog(log_path)
log = StructuredRedactingLogger(sink=sink)
secrets = {
    "password": "Sup3rSecret!2026",
    "token": "dzsess_ABCDEFGHIJKLMNOP",
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
    "bank_rib": "00799999000012345678",
    "raw_text": "FACTURE N° 2026/0912 total 1 234 567,00 DZD",
}
log.log("WARNING", "auth_failure", "corr-1", username="admin", client_ip="10.0.0.9",
        note="NIN 109876543210987654 and IBAN DZ5900000000000000000001", **secrets)
text = log_path.read_text()
print("log mode:", oct(stat.S_IMODE(os.stat(log_path).st_mode)))
print("file lines:", text.count("\n"))
for k, v in secrets.items():
    leaked = v in text or (k == "authorization" and "eyJhbGciOiJIUzI1NiJ9" in text)
    print(f"  leak {k}: {leaked}")
print("NIN leaked:", "109876543210987654" in text, "| IBAN leaked:", "DZ5900000000000000000001" in text)
print("redaction markers:", text.count("[REDACTED]"), "value-pattern markers:",
      text.count("[REDACTED_TOKEN]"), text.count("[REDACTED_NIN]"), text.count("[REDACTED_IBAN]") + text.count("[REDACTED_DZ_IBAN]"), text.count("[REDACTED_RIB]"))
print("record survived:", "auth_failure" in text and "10.0.0.9" in text)
