"""Before/after probe: does the pre-change tree still have the three gaps?

Run with PYTHONPATH=<tree> so the same script measures both trees.
Every check prints OLD-BEHAVIOUR (gap present) or FIXED (gap closed).
"""
import sys, tempfile, inspect
from pathlib import Path

tree = Path(sys.argv[1])
sys.path.insert(0, str(tree))
from dz_erp.auth import AuthManager
from dz_erp.database import ERPDatabase
from dz_erp.intake_ocr import PrivateDocumentVault, update_extracted_field_by_human
from dz_erp.api import create_erp_fastapi_app
from fastapi.testclient import TestClient

tmp = Path(tempfile.mkdtemp())
db = ERPDatabase(str(tmp / "probe.sqlite3"))
with db.transaction() as conn:
    conn.execute("INSERT INTO companies (id, code, name, country_code, chart_template, created_at) VALUES (1,'DZ1','Co','DZ','dz','2026-01-01')")
    conn.execute("INSERT INTO users (id, company_id, username, role, is_active) VALUES (2,1,'clerk','clerk',1)")
auth = AuthManager(db)

# SEC-13: does a one-character password get through a service-level create?
try:
    auth.create_user(username="probe", password="x", role="clerk", company_id=1)
    print("SEC-13 OLD-BEHAVIOUR: a 1-character password was accepted")
except Exception as exc:
    print(f"SEC-13 FIXED: refused ({type(exc).__name__})")

# SEC-14: does the legacy override store an impossible amount?
with db.transaction() as conn:
    conn.execute("INSERT INTO source_documents (id, company_id, filename, mime_type, byte_size, file_sha256, vault_path, security_status, workflow_state, uploaded_by_id, uploaded_at) VALUES ('D1',1,'f.pdf','application/pdf',10,'%s','v','CLEAN','NeedsReview',2,'2026-11-09')" % ("a"*64))
    conn.execute("INSERT INTO ocr_extracted_fields (company_id, document_id, field_name, raw_text, normalized_value, bbox_json, ocr_model_version, created_at) VALUES (1,'D1','amount_ttc_dzd','r','1200.00','[]','e1','2026-11-09')")
try:
    row = update_extracted_field_by_human(db, company_id=1, user_id=2, document_id="D1", field_name="amount_ttc_dzd", new_value="-1.00")
    print(f"SEC-14 OLD-BEHAVIOUR: stored {row['normalized_value']!r} as a human correction")
except Exception as exc:
    print(f"SEC-14 FIXED: refused ({type(exc).__name__})")

# SEC-15a: is an arbitrary Host accepted?
app = create_erp_fastapi_app(db, PrivateDocumentVault(tmp / "vault"))
status = TestClient(app).get("/healthz", headers={"host": "evil.example"}).status_code
print(("SEC-15 OLD-BEHAVIOUR: Host: evil.example accepted (%d)" % status) if status == 200 else f"SEC-15 FIXED: Host refused ({status})")

# SEC-15b: does the server hand proxy trust to uvicorn?
import dz_erp.serve as serve
src = inspect.getsource(serve.main)
print("SEC-15 OLD-BEHAVIOUR: uvicorn starts with its proxy_headers=True default" if "proxy_headers=False" not in src else "SEC-15 FIXED: proxy_headers=False is explicit")
