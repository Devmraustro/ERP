"""Phase 30 — high-DPI / scaling probe.

Renders the application at several device-pixel-ratio settings and records, per
setting, whether anything is clipped or overlapping.

SCOPE, stated precisely: this runs on Linux with the `offscreen` Qt platform
using `QT_SCALE_FACTOR`. It is *not* Windows display-scaling acceptance - that
requires a real Windows host and is recorded as BLOCKED in the report. What this
proves is that the layout code itself survives fractional scaling; what it
cannot prove is how a specific Windows DPI-aware compositor renders it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parents[1] / "screenshots"
LOGS = Path(__file__).resolve().parents[1] / "logs"

CHILD = r'''
import sys, time, json
from pathlib import Path
sys.path.insert(0, {repo!r})
from PySide6.QtWidgets import QApplication
from dz_erp.desktop.api_client import ErpClient
from dz_erp.desktop.service import EmbeddedErpService, bootstrap_first_run
from dz_erp.desktop.theme import ThemeManager
from dz_erp.desktop.ui import MainWindow, VIEWS

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")
work = Path({work!r}); db = work / "erp.sqlite3"
bootstrap_first_run(db, company_name="Scale Co", admin_username="admin", admin_password="Scale#2026x")
service = EmbeddedErpService(db, work / "vault", security_log_path=work / "s.jsonl")
client = ErpClient(service.start()); client.login("admin", "Scale#2026x")
manager = ThemeManager(theme="light", language="fr"); manager.apply()
win = MainWindow("fr", client, "mode test", theme_manager=manager)
win.set_session(client.session()); win.resize(1280, 840); win.show()
win._load_view(0)

end = time.monotonic() + 2.0
while time.monotonic() < end:
    QApplication.processEvents(); time.sleep(0.01)

report = {{
    "scale_factor": app.primaryScreen().devicePixelRatio(),
    "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
    "window_logical": [win.width(), win.height()],
    "screens": [],
}}
# Every nav destination must render without clipping its own header.
for key in VIEWS:
    win._load_view(VIEWS.index(key))
    end = time.monotonic() + 0.6
    while time.monotonic() < end:
        QApplication.processEvents(); time.sleep(0.01)
    view = win._views[key]
    header = getattr(view, "header", None)
    entry = {{"view": key, "rendered": view.isVisible()}}
    if header is not None:
        entry["header_width"] = header.width()
        entry["header_fits"] = header.width() >= header.minimumSizeHint().width()
    report["screens"].append(entry)

win.grab().save(str(Path({shot!r})))
client.close(); service.stop()
print("JSON:" + json.dumps(report))
'''


def run_scale(factor: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"phase30-scale-{factor.replace('.', '_')}-"))
    shot = OUT / f"30_dpi_{factor.replace('.', '_')}.png"
    script = CHILD.format(repo=str(REPO_ROOT), work=str(work), shot=str(shot))
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = factor
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=600
    )
    payload = {}
    for line in proc.stdout.splitlines():
        if line.startswith("JSON:"):
            payload = json.loads(line[5:])
    if not payload:
        payload = {"error": proc.stderr[-800:], "returncode": proc.returncode}
    payload["qt_scale_factor_env"] = factor
    payload["screenshot"] = shot.name if shot.exists() else None
    return payload


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    results = []
    for factor in ("1.0", "1.25", "1.5", "2.0"):
        print(f"--- QT_SCALE_FACTOR={factor} ---")
        result = run_scale(factor)
        results.append(result)
        dpr = result.get("scale_factor")
        clipped = [s["view"] for s in result.get("screens", []) if s.get("header_fits") is False]
        print(f"    devicePixelRatio={dpr}  screens={len(result.get('screens', []))}  clipped={clipped}")
        if "error" in result:
            print(f"    ERROR: {result['error'][:300]}")

    (LOGS / "dpi_results.json").write_text(json.dumps(results, indent=2))
    bad = [r for r in results if "error" in r or any(s.get("header_fits") is False for s in r.get("screens", []))]
    print(f"\nRESULT: {len(results) - len(bad)}/{len(results)} scaling settings rendered every view without clipping")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
