# -*- coding: utf-8 -*-
"""
Force-send today's morning picks email right now.
Usage: python send_now.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import os, json
import config
import screener
import db_manager
import main

# Disable test mode - send real email
main.IS_TEST_MODE = False

print("=" * 55)
print("  STALKER -- MANUAL MORNING EMAIL DISPATCH")
print("=" * 55)

# Check if we have today's scan already
from datetime import date
today_str = str(date.today())

# Try loading existing scan first
result = None
scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
if os.path.exists(scan_path):
    with open(scan_path) as f:
        local = json.load(f)
    if local.get("date") == today_str:
        print(f"Found today's existing scan ({len(local.get('top_picks', []))} picks). Using it.")
        result = local

if not result:
    print("No today's scan found. Running full screener now (2-4 min)...")
    result = screener.run_screen(top_n=config.TOP_PICKS_COUNT)
    db_manager.save_daily_picks(result)
    with open(scan_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

picks = result.get("top_picks", [])
print(f"\nPicks ready: {len(picks)} stocks\n")
for i, p in enumerate(picks[:10], 1):
    sym    = p.get("symbol", "")
    action = p.get("action", "")
    score  = p.get("total_score", 0)
    price  = p.get("current_price", 0)
    print(f"  {i:2}. {sym:20} | {action:5} | Price: {price:,.1f} | Score: {score:.1f}")

print("\nSending morning email to all subscribers...")
main._send_morning_email(result, {})
print("\nDone! Check inbox.")
