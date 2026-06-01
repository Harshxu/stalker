import json
import os
import main
import config

# Clear CC list temporarily for testing to ensure no emails go to other subscribers
os.environ["FORMSUBMIT_CC"] = ""
main.IS_TEST_MODE = False

scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
if os.path.exists(scan_path):
    with open(scan_path, "r") as f:
        scan_result = json.load(f)
    print("Sending live test morning mail ONLY to your email (CC list suppressed)...")
    main._send_morning_email(scan_result)
    print("Test mail sent successfully to harshkumawat9950@gmail.com!")
else:
    print("No scan data found to test.")
