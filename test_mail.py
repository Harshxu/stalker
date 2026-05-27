import json
import os
import main
import config

scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
if os.path.exists(scan_path):
    with open(scan_path, "r") as f:
        scan_result = json.load(f)
    print("Sending test morning mail...")
    main._send_morning_email(scan_result)
    print("Test mail script complete.")
else:
    print("No scan data found to test.")
