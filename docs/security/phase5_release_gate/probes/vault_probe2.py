import os, sys, hashlib, tempfile, pathlib, threading
sys.path.insert(0, os.environ.get("ERP_ROOT", str(pathlib.Path(__file__).resolve().parents[4])))
from dz_erp.intake_ocr import PrivateDocumentVault
tmp = pathlib.Path(tempfile.mkdtemp(prefix="phase5-probe-")); root = tmp / "vault"
v = PrivateDocumentVault(root)
paths = {}
def w(i):
    payload = b"doc%d" % i
    paths[v.store_bytes(7, hashlib.sha256(payload).hexdigest(), payload)] = payload
ts = [threading.Thread(target=w, args=(i,)) for i in range(16)]
[t.start() for t in ts]; [t.join() for t in ts]
print("concurrent: files=%d readable=%s" % (len(paths), all(v.read_bytes(7,7,p)==c for p,c in paths.items())))
print("versions before rotation:", v.scan_blob_versions())
rep = v.rotate_key()
print("rotate_key ok:", {k: rep[k] for k in list(rep)[:6] if k in rep})
print("versions after rotation:", v.scan_blob_versions())
print("still readable:", all(v.read_bytes(7,7,p)==c for p,c in paths.items()))
print("verify_blobs:", v.verify_blobs()["checked"], "checked,", v.verify_blobs()["failed"], "failed")
