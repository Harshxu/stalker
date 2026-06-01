import os
from datetime import date
import main
import db_manager

# Clear CC list temporarily for testing to ensure no emails go to other subscribers
os.environ["FORMSUBMIT_CC"] = ""
main.IS_TEST_MODE = False

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

print("Evening Email Test Complete successfully to harshkumawat9950@gmail.com!")
