import sys
import os
import shutil
import unittest.mock as mock

# Adjust path to find Stalker modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main
import config

# Force test mode to save previews instead of sending via Brevo API
main.IS_TEST_MODE = True

PREVIEW_PATH = os.path.join(config.REPORTS_DIR, "email_preview.html")

def save_and_copy(dest_name):
    # Ensure reports directory exists
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    dest_path = os.path.join(config.REPORTS_DIR, dest_name)
    if os.path.exists(PREVIEW_PATH):
        shutil.copy(PREVIEW_PATH, dest_path)
        print(f"Saved preview copy to: {dest_path}")
    else:
        print(f"Error: {PREVIEW_PATH} not found to copy.")

def test_morning():
    print("\n--- Testing Morning Email responsive layout ---")
    mock_scan = {
        "top_picks": [
            {
                "symbol": "SBIN",
                "name": "State Bank of India",
                "action": "BUY",
                "current_price": 650.25,
                "target_2": 685.00,
                "stop_loss": 628.00,
                "risk_profile": "Medium",
                "total_score": 8.5
            },
            {
                "symbol": "HDFCBANK",
                "name": "HDFC Bank Ltd",
                "action": "BUY",
                "current_price": 1650.00,
                "target_2": 1720.00,
                "stop_loss": 1610.00,
                "risk_profile": "Low",
                "total_score": 9.2
            },
            {
                "symbol": "TATASTEEL",
                "name": "Tata Steel Ltd",
                "action": "WATCH",
                "current_price": 145.50,
                "target_2": 152.00,
                "stop_loss": 141.00,
                "risk_profile": "High",
                "total_score": 7.1
            }
        ],
        "market_trend": "BULLISH",
        "scanned": 150
    }
    mock_verification = {
        "quality": "HIGH",
        "updated": 1
    }
    
    main._send_morning_email(mock_scan, mock_verification)
    save_and_copy("morning_preview.html")

def test_closed():
    print("\n--- Testing Market Closed Email responsive layout ---")
    main._send_market_closed_email()
    save_and_copy("closed_preview.html")

def test_eod():
    print("\n--- Testing EOD Report Email responsive layout ---")
    mock_eod = {
        "date": "2026-06-16",
        "picks": [
            {
                "name": "State Bank of India",
                "action": "BUY",
                "open": 650.25,
                "close": 662.00,
                "vwap": 658.10,
                "vwap_trend": "BULLISH",
                "rel_strength": 1.25,
                "momentum_score": 8.5,
                "pnl_pct": 1.81,
                "sector": "Banking & Fin"
            },
            {
                "name": "HDFC Bank Ltd",
                "action": "BUY",
                "open": 1650.00,
                "close": 1640.00,
                "vwap": 1642.50,
                "vwap_trend": "BEARISH",
                "rel_strength": -0.5,
                "momentum_score": 7.8,
                "pnl_pct": -0.61,
                "sector": "Banking & Fin"
            },
            {
                "name": "Tata Steel",
                "action": "WATCH",
                "open": 145.50,
                "close": 148.20,
                "vwap": 147.00,
                "vwap_trend": "BULLISH",
                "rel_strength": 0.8,
                "momentum_score": 7.1,
                "pnl_pct": 1.86,
                "sector": "Metal & Mining"
            }
        ],
        "performance": {
            "win_rate": 66.7,
            "total_trades": 3
        },
        "is_test": True,
        "nifty_chg_pct": 0.45,
        "pct_above_open": 66.7,
        "pct_outperform_nifty": 66.7,
        "hot_sector": "Banking & Fin",
        "hot_sector_pnl": 0.60
    }
    main._send_email_report(mock_eod)
    save_and_copy("eod_preview.html")

def test_mistakes_audit():
    print("\n--- Testing EOD Mistakes Self-Correction Audit Email responsive layout ---")
    
    with mock.patch("db_manager.get_today_picks") as mock_picks, \
         mock.patch("db_manager.get_prices_for_date") as mock_prices_date, \
         mock.patch("db_manager.update_past_picks_returns") as mock_update_past:
         
         mock_picks.return_value = {
             "market_trend": "BULLISH",
             "picks": [
                 {
                     "symbol": "SBIN",
                     "name": "State Bank of India",
                     "action": "BUY",
                     "total_score": 8.5,
                     "validation_audit": {
                         "data_quality": 98,
                         "risk": 2.5,
                         "institutional": 85,
                         "fundamentals": 80
                     }
                 },
                 {
                     "symbol": "HDFCBANK",
                     "name": "HDFC Bank Ltd",
                     "action": "BUY",
                     "total_score": 9.2,
                     "validation_audit": {
                         "data_quality": 95,
                         "risk": 4.8, # ATR volatility risk score > 4.2
                         "institutional": 80,
                         "fundamentals": 75
                     }
                 },
                 {
                     "symbol": "TATASTEEL",
                     "name": "Tata Steel Ltd",
                     "action": "WATCH",
                     "total_score": 7.1,
                     "validation_audit": {
                         "data_quality": 90,
                         "risk": 1.8,
                         "institutional": 70,
                         "fundamentals": 72
                     }
                 }
             ]
         }
         
         mock_prices_date.return_value = {
             "open": {
                 "SBIN": {"open": 650.25},
                 "HDFCBANK": {"open": 1650.00},
                 "TATASTEEL": {"open": 145.50}
             },
             "close": {
                 "SBIN": {"close": 662.00},
                 "HDFCBANK": {"close": 1590.00}, # loss to trigger mistakes
                 "TATASTEEL": {"close": 148.20} # watch outperforming
             }
         }
         
         # Mock yfinance ticker NSEI
         with mock.patch("yfinance.Ticker") as mock_ticker:
             import pandas as pd
             mock_history = mock.MagicMock()
             mock_history.empty = False
             # create simple dataframe
             df = pd.DataFrame({"Open": [20000.0], "Close": [20100.0]}, index=pd.to_datetime(["2026-06-16"]))
             mock_ticker.return_value.history.return_value = df
             
             import generate_mistakes_audit
             generate_mistakes_audit.run_mistakes_audit()
             
             save_and_copy("mistakes_audit_preview.html")

if __name__ == "__main__":
    test_morning()
    test_closed()
    test_eod()
    test_mistakes_audit()
    print("\nAll tests completed.")
