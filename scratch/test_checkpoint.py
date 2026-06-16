import os
import sys
import time
from datetime import date
import subprocess

stalker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(stalker_dir)

import db_manager

today_str = str(date.today())

print("Starting checkpoint recovery verification test...")

# 1. Clean up any existing lock or checkpoints
if os.path.exists(os.path.join(stalker_dir, "stalker.lock")):
    try:
        os.remove(os.path.join(stalker_dir, "stalker.lock"))
    except Exception:
        pass

db_manager.clear_checkpoint(today_str)

# 2. Save a recovery checkpoint for today's date at batch 0
print("Writing mock checkpoint for batch 0...")
db_manager.save_checkpoint(today_str, "technical_indicators", 0)

# Verify checkpoint is written
chk = db_manager.get_checkpoint(today_str)
if chk and chk.get("last_completed_batch") == 0:
    print(f"Mock checkpoint verified: {chk}")
else:
    print(f"Failed to verify checkpoint. Found: {chk}")
    sys.exit(1)

# 3. Run main.py --mode test
print("Running main.py --mode test to verify resumption...")
res = subprocess.run(
    [sys.executable, "main.py", "--mode", "test"],
    cwd=stalker_dir,
    capture_output=True,
    text=True,
    encoding="utf-8"
)

print(f"Exit code: {res.returncode}")
logs = res.stderr or res.stdout

# Clean up logs encoding before printing to avoid console errors
safe_logs = logs.encode('ascii', errors='replace').decode('ascii')
print("Output logs:")
print(safe_logs)

# 4. Verify logs and checkpoint clearance
chk_after = db_manager.get_checkpoint(today_str)
print(f"Checkpoint after run: {chk_after}")

resume_logged = "Resuming technical indicators calculation from batch 1" in logs
checkpoint_cleared = chk_after is None

print(f"Resume logged: {resume_logged}")
print(f"Checkpoint cleared: {checkpoint_cleared}")

if resume_logged and checkpoint_cleared:
    print("[PASS] Checkpoint recovery test PASSED successfully!")
else:
    print("[FAIL] Checkpoint recovery test FAILED.")
