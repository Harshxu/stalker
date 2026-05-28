import main
import db_manager
import time
from datetime import date

# Activate Sandbox Test Mode (forces yfinance downloads to use the latest completed bar on closed days,
# and redirects emails to a local reports/email_preview.html file to avoid disturbing active users)
main.IS_TEST_MODE = True

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

print("Scrubbing sandbox test data from database to keep metrics clean...")
db_manager.delete_date_data(str(date.today()))

print("Evening Email Test Complete.")
