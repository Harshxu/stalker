import json, os, sys
sys.path.insert(0, ".")
import config
from datetime import date

# Clear stale latest_scan.json if today's with 0 picks
scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
if os.path.exists(scan_path):
    with open(scan_path) as f:
        d = json.load(f)
    picks = d.get("top_picks", d.get("picks", []))
    print(f"latest_scan.json: date={d.get('date')} picks={len(picks)}")
    if d.get("date") == str(date.today()) and len(picks) == 0:
        os.remove(scan_path)
        print("Removed stale empty latest_scan.json")
    else:
        print("Scan file looks OK or not today's — not removing")
else:
    print("No latest_scan.json found")
