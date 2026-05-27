import main
import db_manager
import time

print("Checking for morning picks data...")
picks = db_manager.get_today_picks()
if not picks:
    print("No morning picks found for today! The EOD report requires morning picks to calculate P&L.")
    print("Please run a morning scan first.")
    exit(1)

print("Fetching today's real open prices...")
main.record_open_prices()

print("Fetching today's real high/low/close prices...")
main.record_close_prices()

print("Generating EOD Report and triggering the Evening Email...")
main.generate_eod_report()

print("Evening Email Test Complete.")
