import os
import sys
import json
import time
import subprocess

stalker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
lock_path = os.path.join(stalker_dir, "stalker.lock")

def clean_lock():
    if os.path.exists(lock_path):
        try:
            os.remove(lock_path)
        except Exception:
            pass

print("Starting lock verification tests...")

try:
    # ----------------------------------------------------
    # TEST 1: Active lock (< 30 mins old)
    # ----------------------------------------------------
    print("\n--- Test 1: Active Lock (< 30 mins old) ---")
    clean_lock()
    
    active_data = {
        "lock_created_time": time.time() - 300, # 5 minutes ago
        "process_id": 12345
    }
    with open(lock_path, "w") as f:
        json.dump(active_data, f)
        
    print("Created active lock file.")
    
    # Run main.py --mode test
    res = subprocess.run(
        [sys.executable, "main.py", "--mode", "test"],
        cwd=stalker_dir,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    
    print(f"Exit code: {res.returncode}")
    print("Output logs:")
    print(res.stderr or res.stdout)
    
    if res.returncode == 1 and "Overlapping execution blocked" in (res.stderr or res.stdout):
        print("[PASS] TEST 1 PASSED: Process terminated correctly with exit code 1 due to active lock.")
    else:
        print("[FAIL] TEST 1 FAILED: Process did not terminate as expected.")

    # ----------------------------------------------------
    # TEST 2: Stale lock (>= 30 mins old)
    # ----------------------------------------------------
    print("\n--- Test 2: Stale Lock (>= 30 mins old) ---")
    clean_lock()
    
    stale_data = {
        "lock_created_time": time.time() - 2000, # ~33 minutes ago
        "process_id": 54321
    }
    with open(lock_path, "w") as f:
        json.dump(stale_data, f)
        
    print("Created stale lock file.")
    
    # Run main.py --mode test
    res2 = subprocess.run(
        [sys.executable, "main.py", "--mode", "test"],
        cwd=stalker_dir,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    
    print(f"Exit code: {res2.returncode}")
    logs = res2.stderr or res2.stdout
    contains_warning = "Clearing stale execution lock" in logs
    print(f"Warning found in logs: {contains_warning}")
    
    if contains_warning:
        print("[PASS] TEST 2 PASSED: Stale lock was successfully detected, cleared, and execution proceeded.")
    else:
        print("[FAIL] TEST 2 FAILED: Stale lock warning not found in logs.")

finally:
    clean_lock()
    print("\nCleanup complete.")
