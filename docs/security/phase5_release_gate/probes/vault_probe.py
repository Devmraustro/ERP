import os, sys, stat, tempfile, pathlib, threading
sys.path.insert(0, os.environ.get("ERP_ROOT", str(pathlib.Path(__file__).resolve().parents[4])))
from dz_erp.intake_ocr import PrivateDocumentVault

tmp = pathlib.Path(tempfile.mkdtemp(prefix="phase5-probe-"))
root = tmp / "vault"
v = PrivateDocumentVault(root)
payload = b"%PDF-1.7 SECRET-MARKER-UNIQUE-918273645 payroll 1 234 567 DZD"
sha = __import__("hashlib").sha256(payload).hexdigest()
path = v.store_bytes(company_id=7, file_sha256=sha, content=payload)
st = os.stat(path)
print("blob mode:", oct(stat.S_IMODE(st.st_mode)))
print("blob dir mode:", oct(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)))
print("vault root mode:", oct(stat.S_IMODE(os.stat(root).st_mode)))
print("key file exists:", v.vault_key_path.exists(), "mode:",
      oct(stat.S_IMODE(os.stat(v.vault_key_path).st_mode)) if v.vault_key_path.exists() else None)
raw = pathlib.Path(path).read_bytes()
print("plaintext in blob:", b"SECRET-MARKER" in raw, "| blob bytes:", len(raw), "| magic:", raw[:4])
print("temp leftovers:", [p.name for p in tmp.rglob("*") if p.is_file() and not p.name.endswith(('.enc','.key'))])
print("read back ok:", v.read_bytes(7, 7, path) == payload)
try:
    v.read_bytes(7, 8, path); print("tenant isolation: FAILED")
except Exception as e:
    print("tenant isolation:", type(e).__name__)
# tamper: flip one ciphertext byte -> must fail closed, never return wrong plaintext
tampered = bytearray(raw); tampered[-5] ^= 0xFF
tp = root / "company_7" / ("0" * 64 + ".enc"); tp.write_bytes(bytes(tampered))
try:
    v.read_bytes(7, 7, str(tp)); print("tamper detection: FAILED")
except Exception as e:
    print("tamper detection:", type(e).__name__)
tp.unlink()
print("verify_blobs:", v.verify_blobs())
# concurrency: 16 threads storing distinct contents
written, errs = {}, []
def w(i):
    try:
        payload = b"doc%d" % i
        written[v.store_bytes(7, __import__("hashlib").sha256(payload).hexdigest(), payload)] = payload
    except Exception as e:
        errs.append(repr(e))
ts = [threading.Thread(target=w, args=(i,)) for i in range(16)]
[t.start() for t in ts]; [t.join() for t in ts]
print("concurrent stores: files=%d errors=%s" % (len(written), errs[:2]))
print("all readable:", all(v.read_bytes(7, 7, p_) == c for p_, c in written.items()))
# permission denied on a read-only vault
ro = tmp / "vault_ro"; PrivateDocumentVault(ro)
os.chmod(ro / "company_7" if False else ro, 0o500)
try:
    PrivateDocumentVault(ro).store_bytes(7, "a"*64, b"x"); print("permission denied: NOT RAISED")
except Exception as e:
    print("permission denied:", type(e).__name__)
finally:
    os.chmod(ro, 0o700)
