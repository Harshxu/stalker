# -*- coding: utf-8 -*-
"""
STALKER - Main Orchestrator
Runs the morning scan, records prices, generates reports.
Scheduled automatically via Windows Task Scheduler.
"""
import sys, io
# Force UTF-8 output on Windows to handle special chars
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import os
import sys
import json
import logging
import schedule
import time
import requests
from datetime import datetime, date
from typing import Dict

import config
import screener
import db_manager
import data_fetcher

# ─────────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────────
log_file = os.path.join(config.LOGS_DIR, f"stalker_{date.today()}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# STATE: Today's picks (shared between functions)
# ─────────────────────────────────────────────
_today_scan_result: Dict = {}
_today_symbols_picked = []


# ═══════════════════════════════════════════════
# TASK 0: PRE-MARKET DEEP ANALYSIS (7:00 AM)
# ═══════════════════════════════════════════════

def run_premarket_analysis():
    """Run full market analysis at 7 AM so picks are ready before market opens."""
    global _today_scan_result, _today_symbols_picked

    print("\n" + "="*60)
    print("  STALKER -- PRE-MARKET ANALYSIS (7:00 AM)")
    print(f"  {datetime.now().strftime('%A, %d %B %Y -- %I:%M %p')}")
    print("="*60 + "\n")

    logger.info("Pre-market analysis started at 7:00 AM")

    try:
        # Run the full screener — full universe, all indicators
        result = screener.run_screen(top_n=config.TOP_PICKS_COUNT)
        _today_scan_result = result

        picks = result.get("top_picks", [])
        _today_symbols_picked = [p["symbol"] for p in picks]

        # Save to DB immediately so dashboard can read it
        db_manager.save_daily_picks(result)

        # Automatically clean up MongoDB data older than 10 days to save space
        try:
            db_manager.cleanup_old_data(days_to_keep=10)
        except Exception as clean_err:
            logger.error(f"Failed to auto-cleanup MongoDB space: {clean_err}")

        # Save scan result to JSON for dashboard
        scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
        with open(scan_path, "w") as f:
            json.dump(result, f, indent=2, default=str)

        logger.info(f"Pre-market analysis complete. {len(picks)} picks ready for today.")
        _print_picks_summary(result)

    except Exception as e:
        logger.error(f"Pre-market analysis failed: {e}", exc_info=True)


# ═══════════════════════════════════════════════
# TASK 1: MORNING EMAIL (8:30 AM)
# ═══════════════════════════════════════════════

def run_morning_scan():
    """Send the morning email at 8:30 AM using pre-computed picks from 7 AM analysis."""
    global _today_scan_result, _today_symbols_picked

    print("\n" + "="*60)
    print("  STALKER -- MORNING EMAIL DISPATCH (8:30 AM)")
    print(f"  {datetime.now().strftime('%A, %d %B %Y -- %I:%M %p')}")
    print("="*60 + "\n")

    logger.info("Morning email dispatch started")

    try:
        # If pre-market scan didn't run (e.g. server just started), run it now
        if not _today_scan_result:
            logger.info("No pre-market result found — running scan now...")
            result = screener.run_screen(top_n=config.TOP_PICKS_COUNT)
            _today_scan_result = result
            picks = result.get("top_picks", [])
            _today_symbols_picked = [p["symbol"] for p in picks]
            db_manager.save_daily_picks(result)
            scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
            with open(scan_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
            _print_picks_summary(result)
        else:
            logger.info(f"Using pre-market picks: {len(_today_symbols_picked)} stocks ready")

        # Send automated morning email with today's top picks
        _send_morning_email(_today_scan_result)

        # Open dashboard in browser
        _open_dashboard()

    except Exception as e:
        logger.error(f"Morning email dispatch failed: {e}", exc_info=True)


# ═══════════════════════════════════════════════
# TASK 2: RECORD OPEN PRICES (9:20 AM)
# ═══════════════════════════════════════════════

def record_open_prices():
    global _today_symbols_picked

    if not _today_symbols_picked:
        # Load from DB if morning scan not run in this session
        today_picks = db_manager.get_today_picks()
        if today_picks:
            _today_symbols_picked = [p["symbol"] for p in today_picks.get("picks", [])]

    if not _today_symbols_picked:
        logger.warning("No picks to record open prices for")
        return

    logger.info(f"Recording open prices for {len(_today_symbols_picked)} stocks...")
    prices = data_fetcher.fetch_open_prices(_today_symbols_picked)
    db_manager.save_open_prices(prices)

    # Update dashboard file
    open_path = os.path.join(config.DATA_DIR, "open_prices.json")
    with open(open_path, "w") as f:
        json.dump({"date": str(date.today()), "prices": prices}, f, indent=2, default=str)

    logger.info("Open prices recorded")


# ═══════════════════════════════════════════════
# TASK 3: RECORD CLOSE PRICES (3:35 PM)
# ═══════════════════════════════════════════════

def record_close_prices():
    global _today_symbols_picked

    if not _today_symbols_picked:
        today_picks = db_manager.get_today_picks()
        if today_picks:
            _today_symbols_picked = [p["symbol"] for p in today_picks.get("picks", [])]

    if not _today_symbols_picked:
        logger.warning("No picks to record close prices for")
        return

    logger.info(f"Recording close prices for {len(_today_symbols_picked)} stocks...")
    prices = data_fetcher.fetch_close_prices(_today_symbols_picked)
    db_manager.save_close_prices(prices)

    close_path = os.path.join(config.DATA_DIR, "close_prices.json")
    with open(close_path, "w") as f:
        json.dump({"date": str(date.today()), "prices": prices}, f, indent=2, default=str)

    logger.info("Close prices recorded")


# ═══════════════════════════════════════════════
# TASK 4: EOD REPORT & EMAIL (4:00 PM)
# ═══════════════════════════════════════════════

def generate_eod_report():
    logger.info("Generating EOD report...")

    today = str(date.today())
    today_picks = db_manager.get_today_picks()
    prices = db_manager.get_prices_for_date(today)
    perf   = db_manager.get_performance_summary(30)

    if not today_picks:
        logger.warning("No picks data for EOD report")
        return

    picks       = today_picks.get("picks", [])
    open_prices = prices.get("open", {})
    close_prices = prices.get("close", {})

    # Build P&L per stock
    pnl_results = []
    for pick in picks:
        symbol = pick.get("symbol")
        name   = pick.get("name", symbol)
        op = open_prices.get(symbol, {})
        cp = close_prices.get(symbol, {})

        open_p  = op.get("open") or op.get("current_price")
        close_p = cp.get("close") or cp.get("current_price")

        if open_p and close_p and open_p > 0:
            pnl_pct    = ((close_p - open_p) / open_p) * 100
            pnl_rupees = close_p - open_p
            result_str = "✅ Profit" if pnl_pct > 0 else "❌ Loss"
            color      = "green" if pnl_pct > 0 else "red"
        else:
            pnl_pct    = None
            pnl_rupees = None
            result_str = "⏳ Data pending"
            color      = "gray"

        pnl_results.append({
            "symbol":     symbol,
            "name":       name,
            "open":       open_p,
            "close":      close_p,
            "high":       cp.get("high"),
            "low":        cp.get("low"),
            "pnl_pct":    round(pnl_pct, 2) if pnl_pct is not None else None,
            "pnl_rupees": round(pnl_rupees, 2) if pnl_rupees is not None else None,
            "result":     result_str,
            "color":      color,
            "action":     pick.get("action"),
            "score":      pick.get("total_score"),
            "target":     pick.get("target_2"),
            "stop_loss":  pick.get("stop_loss"),
        })

    # Save EOD report data
    eod_data = {
        "date":        today,
        "picks":       pnl_results,
        "performance": perf,
        "market":      today_picks.get("market_trend"),
        "generated_at": datetime.now().isoformat(),
    }

    eod_path = os.path.join(config.DATA_DIR, "eod_report.json")
    with open(eod_path, "w") as f:
        json.dump(eod_data, f, indent=2, default=str)

    # Save to historical database
    db_manager.save_eod_report(eod_data)

    # Send email report
    _send_email_report(eod_data)

    logger.info("EOD report generated")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _send_morning_email(scan_result: Dict):
    endpoint = config.FORMSUBMIT_ENDPOINT
    if not endpoint:
        logger.info("Formsubmit not configured (FORMSUBMIT_TO missing) — skipping morning email")
        return

    try:
        picks = scan_result.get("top_picks", [])[:10]
        market = scan_result.get("market_trend", "unknown").upper()
        date_str = str(date.today())
        
        payload = {
            "name": "STALKER Market Analyzer",
            "_subject": f"STALKER Morning Picks — {date_str}",
            "_template": "table",
            "_captcha": "false",
            "_autoresponse": "false",
            "Market Trend": market,
        }
        
        for i, p in enumerate(picks, 1):
            action = p.get('action', '')
            emoji = "🟢" if action == "BUY" else "🟡" if action == "WATCH" else "🔴"
            name = p.get('name', p.get('symbol', ''))
            price = p.get('current_price', 0)
            target = p.get('target_2', 0)
            stop_loss = p.get('stop_loss', 0)
            
            payload[f"{i}. {name}"] = f"Open: Rs.{price:,.2f} | Target: Rs.{target:,.2f} | SL: Rs.{stop_loss:,.2f} | Action: {emoji} {action}"

        if getattr(config, "FORMSUBMIT_CC", ""):
            payload["_cc"] = config.FORMSUBMIT_CC

        response = requests.post(
            endpoint,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": "http://localhost:8000/",
            },
            timeout=15,
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success") == "true" or result.get("success") is True:
                logger.info(f"Morning email sent via Formsubmit to {config.FORMSUBMIT_TO}")
            else:
                logger.warning(f"Formsubmit response: {result}")
        else:
            logger.error(f"Formsubmit HTTP {response.status_code}: {response.text[:200]}")

    except Exception as e:
        logger.error(f"Morning email send failed: {e}")


def _print_picks_summary(result: Dict):
    """Print clean summary to console."""
    picks  = result.get("top_picks", [])
    market = result.get("market_trend", "unknown").upper()

    print(f"\n{'='*60}")
    print(f"  🏆 TODAY'S TOP {len(picks)} PICKS  |  Market: {market}")
    print(f"{'='*60}")
    for i, p in enumerate(picks, 1):
        action_emoji = "🟢" if p["action"] == "BUY" else "🟡" if p["action"] == "WATCH" else "🔴"
        print(f"  {i:2}. {action_emoji} {p['name']:<15} ₹{p['current_price']:>8,.2f}  "
              f"Score:{p['total_score']:>5.1f}  SL:₹{p['stop_loss']:>8,.2f}  "
              f"Target:₹{p['target_2']:>8,.2f}  [{p['risk_profile']}]")
    print(f"\n  Open browser → http://localhost:{config.DASHBOARD_PORT}")
    print(f"{'='*60}\n")


def _open_dashboard():
    """Launch the web dashboard in default browser."""
    import webbrowser
    dashboard_path = os.path.join(config.BASE_DIR, "dashboard", "index.html")
    if os.path.exists(dashboard_path):
        webbrowser.open(f"http://localhost:{config.DASHBOARD_PORT}")
    else:
        webbrowser.open(f"file:///{dashboard_path.replace(os.sep, '/')}")


def _send_email_report(eod_data: Dict):
    """
    Send EOD report via Formsubmit.co AJAX API.
    No SMTP, no password — just a POST request.
    """
    endpoint = config.FORMSUBMIT_ENDPOINT
    if not endpoint:
        logger.info("Formsubmit not configured (FORMSUBMIT_TO missing) — skipping email")
        return

    try:
        date_str  = eod_data.get("date", "today")
        perf      = eod_data.get("performance", {})
        win_rate  = perf.get("win_rate", 0)
        picks     = eod_data.get("picks", [])
        wins      = sum(1 for p in picks if (p.get("pnl_pct") or 0) > 0)
        losses    = sum(1 for p in picks if (p.get("pnl_pct") or 0) < 0)

        payload = {
            "name": "STALKER Market Analyzer",
            "_subject": f"STALKER EOD Report — {date_str} | Win Rate: {win_rate:.1f}% | W:{wins} L:{losses}",
            "_template": "table",
            "_captcha": "false",
            "_autoresponse": "false",
            "30-Day Win Rate": f"{win_rate:.1f}%",
            "Avg Daily P&L": f"{perf.get('avg_pnl', 0):+.2f}%",
            "Total Picks": str(perf.get('total_trades', 0))
        }

        for i, p in enumerate(picks, 1):
            pnl = p.get("pnl_pct")
            pnl_str = f"{pnl:+.2f}%" if pnl is not None else "Pending"
            action = p.get('action', '')
            emoji = "🟢" if action == "BUY" else "🟡"
            
            # Fetch requested fields
            open_p = p.get("open", 0)
            close_p = p.get("close", 0)
            high_p = p.get("high", 0) or 0
            low_p = p.get("low", 0) or 0
            target_p = p.get("target", 0) or 0
            sl_p = p.get("stop_loss", 0) or 0

            # Calculate 1-10 rating based on heuristic
            rating = 5  # default
            if pnl is not None:
                if action == "BUY":
                    if pnl > 0:
                        # Profit! How close to target?
                        if target_p > open_p and close_p >= target_p:
                            rating = 10
                        else:
                            # partial profit
                            rating = 8
                    else:
                        # Loss. Hit SL?
                        if sl_p > 0 and close_p <= sl_p:
                            rating = 0
                        else:
                            rating = 3
                else: # WATCH / AVOID
                    if pnl < 0:
                        # Successfully avoided a dropping stock
                        rating = 10
                    elif pnl > 0:
                        # Missed a rally
                        rating = 3
                    else:
                        rating = 5

            row_key = f"{i}. {p.get('name', '')} (Morning: {action})"
            row_val = (
                f"Open: Rs.{open_p:,.2f} | Target: Rs.{target_p:,.2f} | SL: Rs.{sl_p:,.2f} | "
                f"High/Low: Rs.{high_p:,.2f}/Rs.{low_p:,.2f} | "
                f"Close: Rs.{close_p:,.2f} | P&L: {pnl_str} | Rating: {rating}/10"
            )
            payload[row_key] = row_val

        if getattr(config, "FORMSUBMIT_CC", ""):
            payload["_cc"] = config.FORMSUBMIT_CC

        response = requests.post(
            endpoint,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Accept":       "application/json",
                "Referer":      "http://localhost:8000/",
            },
            timeout=15,
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success") == "true" or result.get("success") is True:
                logger.info(f"Email sent via Formsubmit to {config.FORMSUBMIT_TO}")
            else:
                # Usually means activation email was just sent
                logger.warning(
                    f"Formsubmit response: {result}. "
                    f"If first send, check {config.FORMSUBMIT_TO} for activation email and click the link."
                )
        else:
            logger.error(f"Formsubmit HTTP {response.status_code}: {response.text[:200]}")

    except requests.exceptions.Timeout:
        logger.error("Formsubmit request timed out")
    except Exception as e:
        logger.error(f"Email send failed: {e}")


# ─────────────────────────────────────────────
# SCHEDULER SETUP
# ─────────────────────────────────────────────

def setup_schedule():
    """Set up automatic daily schedule."""
    # 7:00 AM — Pre-market deep analysis (runs while market is still closed)
    schedule.every().monday.at("07:00").do(run_premarket_analysis)
    schedule.every().tuesday.at("07:00").do(run_premarket_analysis)
    schedule.every().wednesday.at("07:00").do(run_premarket_analysis)
    schedule.every().thursday.at("07:00").do(run_premarket_analysis)
    schedule.every().friday.at("07:00").do(run_premarket_analysis)

    # 8:30 AM — Send morning email with today's top picks
    schedule.every().monday.at("08:30").do(run_morning_scan)
    schedule.every().tuesday.at("08:30").do(run_morning_scan)
    schedule.every().wednesday.at("08:30").do(run_morning_scan)
    schedule.every().thursday.at("08:30").do(run_morning_scan)
    schedule.every().friday.at("08:30").do(run_morning_scan)

    # 9:20 AM — Lock in opening prices once market has stabilised
    schedule.every().monday.at("09:20").do(record_open_prices)
    schedule.every().tuesday.at("09:20").do(record_open_prices)
    schedule.every().wednesday.at("09:20").do(record_open_prices)
    schedule.every().thursday.at("09:20").do(record_open_prices)
    schedule.every().friday.at("09:20").do(record_open_prices)

    # 3:35 PM — Lock in closing prices
    schedule.every().monday.at("15:35").do(record_close_prices)
    schedule.every().tuesday.at("15:35").do(record_close_prices)
    schedule.every().wednesday.at("15:35").do(record_close_prices)
    schedule.every().thursday.at("15:35").do(record_close_prices)
    schedule.every().friday.at("15:35").do(record_close_prices)

    # 4:00 PM — Generate EOD report & send evening email
    schedule.every().monday.at("16:00").do(generate_eod_report)
    schedule.every().tuesday.at("16:00").do(generate_eod_report)
    schedule.every().wednesday.at("16:00").do(generate_eod_report)
    schedule.every().thursday.at("16:00").do(generate_eod_report)
    schedule.every().friday.at("16:00").do(generate_eod_report)

    logger.info("Schedule: 7:00 AM analysis | 8:30 AM morning email | 9:20 AM open | 3:35 PM close | 4:00 PM EOD report")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="STALKER — Stock Market Analyzer")
    parser.add_argument("--mode", choices=["run", "scan", "test", "serve"], default="run",
                        help="run=full scheduler | scan=run once now | test=5 stocks | serve=dashboard only")
    parser.add_argument("--capital", type=float, default=100000,
                        help="Your trading capital in ₹ (default: ₹1,00,000)")
    args = parser.parse_args()

    print("""
+-------------------------------------------+
|     STALKER                               |
|     Indian Stock Market Analyzer          |
+-------------------------------------------+
    """)

    if args.mode == "test":
        print("🧪 Running test scan (5 stocks)...")
        import screener
        result = screener.run_screen(
            symbols=["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "SBIN.NS"],
            top_n=5
        )
        _print_picks_summary(result)

    elif args.mode == "scan":
        print("🔍 Running full morning scan now...")
        run_morning_scan()

    elif args.mode == "serve":
        print(f"Live dashboard starting at http://localhost:{config.DASHBOARD_PORT}")
        import api_server
        api_server.start_server(port=config.DASHBOARD_PORT, open_browser=True)

    else:  # run — full scheduler mode
        setup_schedule()
        print("✅ STALKER is running. Press Ctrl+C to stop.")
        print(f"   Scheduled: 8:30 AM scan | 9:20 AM open prices | 3:35 PM close | 4:00 PM report")
        print(f"   Dashboard: python main.py --mode serve\n")

        while True:
            schedule.run_pending()
            time.sleep(30)
