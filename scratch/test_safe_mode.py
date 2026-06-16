import os
import sys
from datetime import date

stalker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(stalker_dir)

import db_manager
import screener
import main

print("Running Safe Mode trigger behavior tests...")

# 1. Force MongoDB connection check to fail
db_manager.check_mongo_connection = lambda: False

print("Simulating MongoDB connection failure during scan...")
result = screener.run_screen(symbols=["RELIANCE.NS"], top_n=1, dry_run=False)

print(f"Scan result: {result}")

safe_mode_active = result.get("safe_mode") == True
status_ok = result.get("status") == "SYSTEM STATUS: SAFE MODE"
reason_ok = "MongoDB connection" in result.get("safe_mode_reason", "")

print(f"Safe Mode Active: {safe_mode_active}")
print(f"Status OK: {status_ok}")
print(f"Reason OK: {reason_ok}")

if safe_mode_active and status_ok and reason_ok:
    print("[PASS] Safe Mode trigger on DB failure behaves correctly!")
else:
    print("[FAIL] Safe Mode trigger on DB failure failed.")
    sys.exit(1)

# 2. Test scanned universe size trigger (< 150 symbols, and not 5 symbols)
print("\nSimulating universe size trigger...")
# Restore connection checker
db_manager.check_mongo_connection = lambda: True

# Process with 10 symbols (which is < 150 and not 5)
result_size = screener.run_screen(symbols=["TCS.NS"] * 10, top_n=2, dry_run=False)
print(f"Scan result: {result_size}")

size_triggered = result_size.get("safe_mode") == True and "scanned universe count" in result_size.get("safe_mode_reason", "").lower()
print(f"Universe size trigger: {size_triggered}")

if size_triggered:
    print("[PASS] Safe Mode trigger on Universe Size behaves correctly!")
else:
    print("[FAIL] Safe Mode trigger on Universe Size failed.")
    sys.exit(1)

# 3. Test morning email dispatch behavior for Safe Mode
print("\nTesting morning email dispatch behavior under Safe Mode...")
# Mock _send_admin_alert to avoid sending a real email but log the call
alert_called = False
alert_reason = ""
alert_detail = ""

def mock_send_admin_alert(reason, detail=""):
    global alert_called, alert_reason, alert_detail
    alert_called = True
    alert_reason = reason
    alert_detail = detail
    print(f"[MOCK ALERT] Admin alert dispatched! Reason: {reason}")

main._send_admin_alert = mock_send_admin_alert

# Call _send_morning_email with the safe mode result
main._send_morning_email(result)

print(f"Admin Alert called: {alert_called}")
print(f"Alert Reason: {alert_reason}")

if alert_called and "SAFE MODE" in alert_reason:
    print("[PASS] Morning email handler correctly dispatches admin alert and returns!")
else:
    print("[FAIL] Morning email handler failed to dispatch admin alert.")
    sys.exit(1)

print("\n[ALL PASS] All Safe Mode behavior tests passed successfully!")
