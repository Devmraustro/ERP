import json, os, sys, tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["ERP_DB_PATH"] = str(tmp / "erp.sqlite3")
os.environ["ERP_VAULT_PATH"] = str(tmp / "vault")
sys.path.insert(0, os.environ.get("ERP_ROOT", str(pathlib.Path(__file__).resolve().parents[4])))
from fastapi.testclient import TestClient
from dz_erp.api import create_erp_fastapi_app
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault
from dz_erp.auth import hash_password

db = ERPDatabase(os.environ["ERP_DB_PATH"])
vault = PrivateDocumentVault(os.environ["ERP_VAULT_PATH"])
app = create_erp_fastapi_app(db, vault)
with db.transaction() as conn:
    conn.execute("INSERT INTO companies (id, code, name, country_code, chart_template, created_at) VALUES (1,'DZ1','Smoke Co','DZ','dz','2026-11-01')")
    conn.execute("INSERT INTO users (id, company_id, username, role, is_active, password_hash) VALUES (1,1,'admin','admin',1,?)",
                 (hash_password("Smoke#2026x", iterations=1000),))
c = TestClient(app)
print("healthz:", c.get("/healthz").status_code, c.get("/healthz").json())
print("openapi disabled:", c.get("/openapi.json").status_code)
print("unauth users list:", c.get("/api/v1/users").status_code)
tok = c.post("/api/v1/auth/token", json={"username":"admin","password":"Smoke#2026x"})
print("token:", tok.status_code, "prefix:", tok.json().get("access_token","")[:13])
H = {"Authorization": f"Bearer {tok.json()['access_token']}"}
print("session:", c.get("/api/v1/auth/session", headers=H).status_code)
print("rate-limit policy:", c.get("/api/v1/system/rate-limit-policy", headers=H).status_code,
      "hosts:", c.get("/api/v1/system/rate-limit-policy", headers=H).json().get("host_policy", {}).get("allowed_hosts"))
print("bad host:", c.get("/healthz", headers={"host":"evil.example"}).status_code)
print("upload-policy (public):", c.get("/api/v1/upload-policy").status_code)
print("wrong password:", c.post("/api/v1/auth/token", json={"username":"admin","password":"nope"}).status_code)
print("short new password (SEC-13):", c.post("/api/v1/auth/password", headers=H, json={"current_password":"Smoke#2026x","new_password":"short"}).status_code)
# vault round-trip through the API-independent service
