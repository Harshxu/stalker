import json
import os
import main
import config

# Activate Sandbox Test Mode (forces yfinance downloads to use the latest completed bar on closed days,
# and redirects emails to a local reports/email_preview.html file to avoid disturbing active users)
main.IS_TEST_MODE = True

scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
if os.path.exists(scan_path):
    with open(scan_path, "r") as f:
        scan_result = json.load(f)
    print("Sending test morning mail...")
    main._send_morning_email(scan_result)
    print("Test mail script complete.")
else:
    print("No scan data found to test.")
