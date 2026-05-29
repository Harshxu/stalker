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
from data_fetcher import is_nse_holiday

# Global Test/Sandbox Flag (bypasses holiday checks and mocks emails in testing mode)
IS_TEST_MODE = False

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

    if is_nse_holiday(date.today()) and not IS_TEST_MODE:
        logger.info("Today is a weekend or NSE trading holiday. Skipping pre-market analysis.")
        return

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
# TASK 0b: PRICE VERIFICATION (8:15 AM)
# ═══════════════════════════════════════════════

# Stores the last verification report for the morning email
_verification_report: Dict = {}

def verify_picks_prices():
    """
    Run at 8:15 AM — 15 minutes before the morning email.
    Cross-checks each pick's recorded current_price against fresh yfinance fast_info.
    Corrects drifted prices in memory and DB so the morning email is 100%% accurate.
    Generates a verification report that gets injected into the email.
    """
    global _today_scan_result, _today_symbols_picked, _verification_report

    if is_nse_holiday(date.today()) and not IS_TEST_MODE:
        logger.info("Holiday — skipping price verification.")
        return

    import math, time as _time
    import yfinance as yf

    logger.info("=" * 55)
    logger.info("  PRICE VERIFICATION (8:15 AM)")
    logger.info("=" * 55)

    # ── Load today's picks if not already in memory ──────────────────────
    if not _today_scan_result:
        today_str = str(date.today())
        try:
            db_picks = db_manager.get_today_picks()
            if db_picks and db_picks.get("date") == today_str:
                _today_scan_result = db_picks
                picks_list = db_picks.get("picks", db_picks.get("top_picks", []))
                _today_symbols_picked = [p["symbol"] for p in picks_list if p.get("symbol")]
                logger.info(f"Loaded {len(_today_symbols_picked)} picks from DB for verification")
        except Exception as db_err:
            logger.warning(f"Could not load picks from DB: {db_err}")

    picks = _today_scan_result.get("top_picks", _today_scan_result.get("picks", []))
    if not picks:
        logger.warning("No picks found to verify — skipping verification")
        return

    TOLERANCE_PCT = 2.0   # Flag if price drifted more than 2%% since 7 AM scan
    verified_rows = []
    updated_count = 0
    failed_count  = 0
    total         = len(picks)

    def _safe(val, nd=2):
        try:
            f = float(val)
            return None if math.isnan(f) else round(f, nd)
        except:
            return None

    logger.info(f"Verifying prices for {total} picks via yfinance fast_info...")
    logger.info(f"  {'Stock':<20} {'Scan Price':>12} {'Live Price':>12} {'Drift%':>8} {'Status':<12}")
    logger.info("  " + "-" * 66)

    for pick in picks:
        symbol    = pick.get("symbol", "")
        scan_price = pick.get("current_price")
        live_price = None
        prev_close = None
        status     = "OK"

        if data_fetcher.is_rate_limited():
            status = "COOLDOWN"
            failed_count += 1
        else:
            try:
                ticker = yf.Ticker(symbol, session=data_fetcher.get_browser_session())

                # Try fast_info first
                try:
                    fi         = ticker.fast_info
                    live_price = _safe(fi.last_price)
                    prev_close = _safe(fi.previous_close)
                except Exception:
                    pass

                # Fallback: ticker.history
                if live_price is None:
                    try:
                        df = ticker.history(period="5d")
                        df.dropna(subset=["Close"], inplace=True)
                        if not df.empty:
                            live_price = round(float(df.iloc[-1]["Close"]), 2)
                            if len(df) >= 2:
                                prev_close = round(float(df.iloc[-2]["Close"]), 2)
                    except Exception:
                        pass

                if live_price is None:
                    status = "FETCH FAIL"
                    failed_count += 1
                elif scan_price:
                    drift = abs((live_price - scan_price) / scan_price) * 100
                    if drift > TOLERANCE_PCT:
                        # Price drifted significantly since the 7 AM scan — update it
                        old_price = scan_price
                        pick["current_price"] = live_price
                        if prev_close and live_price:
                            pick["change_pct"] = round((live_price - prev_close) / prev_close * 100, 2)
                        # Recalculate stop_loss and targets proportionally if they were set
                        # (only if targets exist and are based on %%age from entry)
                        for field in ["stop_loss", "target_1", "target_2"]:
                            orig = pick.get(field)
                            if orig and scan_price > 0:
                                ratio = orig / scan_price
                                pick[field] = round(live_price * ratio, 2)
                        status = f"UPDATED ({drift:.1f}%% drift)"
                        updated_count += 1
                        logger.info(f"  Price updated for {symbol}: ₹{old_price} → ₹{live_price} (drift={drift:.1f}%%)")
                    else:
                        status = f"OK ({drift:.2f}%% diff)"
                else:
                    # No scan price was set — fill it in
                    pick["current_price"] = live_price
                    status = "FILLED IN"

            except Exception as e:
                status = f"ERROR"
                failed_count += 1
                err_msg = str(e)
                if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
                    data_fetcher.mark_rate_limited()
                logger.error(f"  Verification error for {symbol}: {e}")

        name_short = pick.get("name", symbol)[:18]
        sp_str = f"₹{scan_price:>9.2f}" if scan_price else "          N/A"
        lp_str = f"₹{live_price:>9.2f}" if live_price else "          N/A"
        drift_str = ""
        if scan_price and live_price:
            d = abs((live_price - scan_price) / scan_price) * 100
            drift_str = f"{d:>7.2f}%%"
        else:
            drift_str = "       N/A"

        logger.info(f"  {name_short:<20} {sp_str} {lp_str} {drift_str} {status}")

        verified_rows.append({
            "symbol":     symbol,
            "name":       pick.get("name", symbol),
            "scan_price": scan_price,
            "live_price": live_price,
            "status":     status,
            "action":     pick.get("action", ""),
            "stop_loss":  pick.get("stop_loss"),
            "target_2":   pick.get("target_2"),
        })

        _time.sleep(0.15)   # polite delay between tickers

    # ── Save updated scan result back to DB and disk ──────────────────────
    if updated_count > 0:
        logger.info(f"Saving {updated_count} corrected prices back to DB...")
        try:
            db_manager.save_daily_picks(_today_scan_result)
        except Exception as db_err:
            logger.warning(f"Failed to re-save updated picks to DB: {db_err}")
        try:
            scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
            with open(scan_path, "w") as f:
                json.dump(_today_scan_result, f, indent=2, default=str)
        except Exception:
            pass

    # ── Build verification report dict (used by morning email) ───────────
    pass_count = total - failed_count
    pass_rate  = round(pass_count / total * 100) if total else 0

    _verification_report = {
        "verified_at":   datetime.now().isoformat(),
        "total":         total,
        "updated":       updated_count,
        "failed":        failed_count,
        "pass_rate":     pass_rate,
        "rows":          verified_rows,
        "data_quality":  "HIGH" if pass_rate >= 90 else "MEDIUM" if pass_rate >= 70 else "LOW",
    }

    logger.info("=" * 55)
    logger.info(f"  Verification complete: {pass_count}/{total} OK, {updated_count} updated, {failed_count} failed")
    logger.info(f"  Data quality: {_verification_report['data_quality']} ({pass_rate}%% pass rate)")
    logger.info("=" * 55)


def run_morning_scan():
    """Send the morning email at 8:30 AM using pre-computed picks from 7 AM analysis."""
    global _today_scan_result, _today_symbols_picked

    if is_nse_holiday(date.today()) and not IS_TEST_MODE:
        logger.info("Today is a weekend or NSE trading holiday. Dispatching 'Market Closed Today' morning email...")
        _send_market_closed_email()
        return

    print("\n" + "="*60)
    print("  STALKER -- MORNING EMAIL DISPATCH (8:30 AM)")
    print(f"  {datetime.now().strftime('%A, %d %B %Y -- %I:%M %p')}")
    print("="*60 + "\n")

    logger.info("Morning email dispatch started")

    try:
        today_str = str(date.today())
        
        # 1. Check if memory has it (already loaded in same process)
        # 2. Check if DB has today's picks (pre-computed at 7:00 AM)
        # 3. Check if local latest_scan.json has today's picks
        if not _today_scan_result:
            logger.info("No pre-market result in process memory — checking database...")
            try:
                db_picks = db_manager.get_today_picks()
                if db_picks and db_picks.get("date") == today_str:
                    logger.info("Found today's pre-computed picks in MongoDB!")
                    _today_scan_result = db_picks
                    picks = db_picks.get("picks", [])
                    _today_symbols_picked = [p["symbol"] for p in picks if p.get("symbol")]
            except Exception as db_err:
                logger.error(f"Failed to check MongoDB for pre-market picks: {db_err}")

        if not _today_scan_result:
            logger.info("Checking local latest_scan.json fallback...")
            scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
            if os.path.exists(scan_path):
                try:
                    with open(scan_path) as f:
                        local_data = json.load(f)
                        if local_data and local_data.get("date") == today_str:
                            logger.info("Found today's pre-computed picks in latest_scan.json!")
                            _today_scan_result = local_data
                            picks = local_data.get("top_picks", local_data.get("picks", []))
                            _today_symbols_picked = [p["symbol"] for p in picks if p.get("symbol")]
                except Exception as json_err:
                    logger.error(f"Failed to load local scan fallback: {json_err}")

        # 4. If still empty (meaning 7:00 AM pre-market analysis failed or was skipped), run new scan
        if not _today_scan_result:
            logger.warning("No pre-computed 7:00 AM picks found in DB or local fallback. Initiating immediate scan...")
            result = screener.run_screen(top_n=config.TOP_PICKS_COUNT)
            _today_scan_result = result
            picks = result.get("top_picks", [])
            _today_symbols_picked = [p["symbol"] for p in picks if p.get("symbol")]
            db_manager.save_daily_picks(result)
            scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
            with open(scan_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
            _print_picks_summary(result)
        else:
            logger.info(f"Using pre-market picks: {len(_today_symbols_picked)} stocks ready")

        # Send automated morning email with today's top picks + verification report
        _send_morning_email(_today_scan_result, _verification_report)

        # Open dashboard in browser
        _open_dashboard()

    except Exception as e:
        logger.error(f"Morning email dispatch failed: {e}", exc_info=True)


# ═══════════════════════════════════════════════
# TASK 2: RECORD OPEN PRICES (9:20 AM)
# ═══════════════════════════════════════════════

def record_open_prices():
    global _today_symbols_picked

    # Strict holiday / closed day check
    if is_nse_holiday(date.today()) and not IS_TEST_MODE:
        logger.info("Today is a weekend or NSE trading holiday. Skipping open prices.")
        return

    if not _today_symbols_picked:
        # Load from DB if morning scan not run in this session
        today_picks = db_manager.get_today_picks()
        if today_picks:
            _today_symbols_picked = [p["symbol"] for p in today_picks.get("picks", [])]

    if not _today_symbols_picked:
        logger.warning("No picks to record open prices for")
        return

    logger.info(f"Recording open prices for {len(_today_symbols_picked)} stocks...")
    prices = data_fetcher.fetch_open_prices(_today_symbols_picked, allow_historical=IS_TEST_MODE)
    if not prices:
        logger.info("No open prices retrieved (Market is closed today).")
        return

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

    # Strict holiday / closed day check
    if is_nse_holiday(date.today()) and not IS_TEST_MODE:
        logger.info("Today is a weekend or NSE trading holiday. Skipping close prices.")
        return

    if not _today_symbols_picked:
        today_picks = db_manager.get_today_picks()
        if today_picks:
            _today_symbols_picked = [p["symbol"] for p in today_picks.get("picks", [])]

    if not _today_symbols_picked:
        logger.warning("No picks to record close prices for")
        return

    logger.info(f"Recording close prices for {len(_today_symbols_picked)} stocks...")
    prices = data_fetcher.fetch_close_prices(_today_symbols_picked, allow_historical=IS_TEST_MODE)
    if not prices:
        logger.info("No close prices retrieved (Market is closed today).")
        return

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

    # Strict holiday / closed day check
    if is_nse_holiday(date.today()) and not IS_TEST_MODE:
        logger.info("Today is a weekend or NSE trading holiday. Skipping EOD report generation (silent evening rule).")
        return

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

    # Prevent calculating/sending fake data if prices are completely missing
    if (not open_prices or not close_prices) and not IS_TEST_MODE:
        logger.warning("Missing open or close price data for today. Skipping EOD report to avoid sending unverified details.")
        return

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
        "is_test":     IS_TEST_MODE,
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

def _send_via_brevo(subject: str, html_body: str) -> bool:
    """Send an email using Brevo REST API over HTTPS."""
    if IS_TEST_MODE:
        logger.info(f"[TEST_MODE] Real email send bypassed to avoid disturbing users. Subject: {subject}")
        preview_path = os.path.join(config.REPORTS_DIR, "email_preview.html")
        try:
            with open(preview_path, "w", encoding="utf-8") as f:
                f.write(html_body)
            logger.info(f"[TEST_MODE] Preview saved locally at: {preview_path}")
        except Exception as e:
            logger.error(f"[TEST_MODE] Failed to save email preview: {e}")
        return True

    api_key = os.getenv("BREVO_API_KEY", "")
    mail_to = os.getenv("FORMSUBMIT_TO", "")
    mail_from = os.getenv("FORMSUBMIT_TO", "")
    
    if not api_key:
        logger.error("[BREVO] BREVO_API_KEY environment variable is not set!")
        return False
        
    if not mail_to:
        logger.error("[BREVO] FORMSUBMIT_TO is not set!")
        return False

    payload = {
        "sender": {"name": "STALKER Market Analyzer", "email": mail_from},
        "to":     [{"email": mail_to}],
        "subject": subject,
        "htmlContent": html_body,
    }
    
    # Add CC if configured
    cc_emails = os.getenv("FORMSUBMIT_CC", "")
    if cc_emails:
        payload["cc"] = [{"email": email.strip()} for email in cc_emails.split(",") if email.strip()]

    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "accept":       "application/json",
                "api-key":      api_key,
                "content-type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"[BREVO] Email successfully sent to {mail_to}")
            return True
        else:
            logger.error(f"[BREVO] Failed to send email (HTTP {resp.status_code}): {resp.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"[BREVO] Error during email send: {e}")
        return False


def _send_morning_email(scan_result: Dict, verification: Dict = None):
    try:
        picks    = scan_result.get("top_picks", [])[:10]
        market   = scan_result.get("market_trend", "unknown").upper()
        date_str = str(date.today())
        ver      = verification or {}

        # ── Verification badge HTML ───────────────────────────────────────
        qual = ver.get("data_quality", "")
        if qual == "HIGH":
            ver_badge = f"""<div style="display:inline-block;padding:6px 14px;background:#dcfce7;
                border-radius:9999px;font-size:12px;font-weight:bold;color:#16a34a;margin-bottom:16px;">
                ✅ Price Data Verified — {ver.get('pass_rate',100)}% accuracy confirmed at {ver.get('verified_at','')[11:16]} IST
                </div>"""
        elif qual == "MEDIUM":
            ver_badge = f"""<div style="display:inline-block;padding:6px 14px;background:#fef9c3;
                border-radius:9999px;font-size:12px;font-weight:bold;color:#a16207;margin-bottom:16px;">
                ⚠️ Price Data Partially Verified — {ver.get('pass_rate',0)}% accuracy
                </div>"""
        elif qual == "LOW":
            ver_badge = """<div style=\"display:inline-block;padding:6px 14px;background:#fee2e2;
                border-radius:9999px;font-size:12px;font-weight:bold;color:#dc2626;margin-bottom:16px;\">
                ⚠️ Price Verification Issues — treat entry prices as indicative
                </div>"""
        else:
            ver_badge = ""  # verification not run yet

        # ── Updated count note ────────────────────────────────────────────
        if ver.get("updated", 0) > 0:
            updated_note = f"""<div style="margin-top:8px;font-size:12px;color:#6b7280;">
                💡 {ver['updated']} stock price(s) auto-corrected from 7AM scan values to latest live prices.
                </div>"""
        else:
            updated_note = ""
        
        subject = f"🟢 STALKER Morning Picks — {date_str} (Trend: {market})"
        
        # Build premium HTML email body
        rows_html = ""
        for i, p in enumerate(picks, 1):
            action = p.get('action', '')
            action_color = "#16a34a" if action == "BUY" else "#d97706" if action == "WATCH" else "#dc2626"
            action_bg = "#f0fdf4" if action == "BUY" else "#fffbeb" if action == "WATCH" else "#fef2f2"
            
            name = p.get('name', p.get('symbol', ''))
            price = p.get('current_price') if p.get('current_price') is not None else 0
            target = p.get('target_2') if p.get('target_2') is not None else 0
            stop_loss = p.get('stop_loss') if p.get('stop_loss') is not None else 0
            risk = p.get('risk_profile') if p.get('risk_profile') is not None else 'Medium'
            score = p.get('total_score') if p.get('total_score') is not None else 0
            
            rows_html += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 12px 8px; font-weight: bold; color: #1f2937;">{i}. {name}</td>
                <td style="padding: 12px 8px; text-align: center;">
                    <span style="display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: bold; background-color: {action_bg}; color: {action_color};">
                        {action}
                    </span>
                </td>
                <td style="padding: 12px 8px; text-align: right; color: #374151; font-weight: 500;">₹{price:,.2f}</td>
                <td style="padding: 12px 8px; text-align: right; color: #16a34a; font-weight: bold;">₹{target:,.2f}</td>
                <td style="padding: 12px 8px; text-align: right; color: #dc2626; font-weight: bold;">₹{stop_loss:,.2f}</td>
                <td style="padding: 12px 8px; text-align: center; color: #4b5563;">{score:.1f}</td>
                <td style="padding: 12px 8px; text-align: center; color: #6b7280; font-size: 12px;">{risk}</td>
            </tr>
            """
            
        html_body = f"""
        <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f3f4f6; padding: 20px; margin: 0;">
            <div style="max-width: 680px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); padding: 30px 24px; text-align: center; color: #ffffff;">
                    <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 0.5px;">🎯 STALKER MARKET ANALYSIS</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px; font-weight: 500;">Pre-Market Strategy & Top Stock Picks</p>
                </div>
                
                <!-- Info Section -->
                <div style="padding: 24px; background-color: #ffffff;">
                    <div style="display: flex; justify-content: space-between; border-bottom: 2px solid #f3f4f6; padding-bottom: 15px; margin-bottom: 20px;">
                        <div>
                            <span style="font-size: 12px; color: #9ca3af; text-transform: uppercase; font-weight: bold;">Analysis Date</span>
                            <div style="font-size: 16px; font-weight: bold; color: #1f2937;">{date_str}</div>
                        </div>
                        <div style="text-align: right;">
                            <span style="font-size: 12px; color: #9ca3af; text-transform: uppercase; font-weight: bold;">Market Outlook</span>
                            <div style="font-size: 16px; font-weight: bold; color: #3b82f6;">{market}</div>
                        </div>
                    </div>

                    <!-- Verification Badge -->
                    <div style="text-align:center;">{ver_badge}{updated_note}</div>

                    <!-- Table Title -->
                    <h3 style="margin: 0 0 12px 0; color: #1e3a8a; font-weight: 700; font-size: 18px;">🔥 Today's Top Picks</h3>
                    
                    <!-- Table -->
                    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                        <thead>
                            <tr style="background-color: #f8fafc; border-bottom: 2px solid #e2e8f0; color: #475569; font-weight: bold;">
                                <th style="padding: 10px 8px; text-align: left;">Stock</th>
                                <th style="padding: 10px 8px; text-align: center;">Action</th>
                                <th style="padding: 10px 8px; text-align: right;">Open (Entry)</th>
                                <th style="padding: 10px 8px; text-align: right;">Target 2</th>
                                <th style="padding: 10px 8px; text-align: right;">Stop Loss</th>
                                <th style="padding: 10px 8px; text-align: center;">Score</th>
                                <th style="padding: 10px 8px; text-align: center;">Risk</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows_html}
                        </tbody>
                    </table>
                    
                    <!-- Note Section -->
                    <div style="margin-top: 30px; padding: 15px; background-color: #eff6ff; border-left: 4px solid #3b82f6; border-radius: 4px; font-size: 13px; color: #1e40af; line-height: 1.5;">
                        <strong>💡 Trading Strategy Note:</strong> Entry is recommended at or near the opening price. Strictly follow the Stop Loss to manage capital risk. Let the targets execute automatically, or trail your Stop Loss in profit to capture maximum gains.
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="background-color: #f9fafb; padding: 20px; text-align: center; border-top: 1px solid #f3f4f6; font-size: 12px; color: #9ca3af;">
                    <p style="margin: 0;">Sent automatically by STALKER Server on Render.</p>
                    <p style="margin: 5px 0 0 0;">This email is confidential and intended solely for the recipient.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        _send_via_brevo(subject, html_body)
    except Exception as e:
        logger.error(f"Morning email failed to build: {e}")


def _send_market_closed_email():
    """Send a friendly morning email stating that the market is closed today."""
    date_str = datetime.now().strftime('%A, %d %B %Y')
    subject = f"🔔 STALKER Info — Market Closed Today ({date_str})"
    
    html_body = f"""
    <html>
    <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f3f4f6; padding: 20px; margin: 0;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);">
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); padding: 35px 24px; text-align: center; color: #ffffff;">
                <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 0.5px;">🔔 MARKET CLOSED TODAY</h1>
                <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px; font-weight: 500;">STALKER Market Analyzer</p>
            </div>
            
            <!-- Info Section -->
            <div style="padding: 30px 24px; text-align: center; background-color: #ffffff;">
                <div style="font-size: 48px; margin-bottom: 20px;">☕</div>
                <h3 style="margin: 0 0 10px 0; color: #1e3a8a; font-weight: 700; font-size: 20px;">Market is Closed Today</h3>
                <p style="font-size: 15px; color: #4b5563; line-height: 1.6; margin: 0 0 20px 0;">
                    Today, <strong>{date_str}</strong>, is a stock market holiday / weekend. No trading picks will be generated, and no EOD reports will be sent.
                </p>
                <div style="display: inline-block; padding: 10px 20px; background-color: #eff6ff; border-radius: 8px; font-size: 14px; color: #1e40af; font-weight: bold;">
                    Have a wonderful day and enjoy your time off!
                </div>
            </div>
            
            <!-- Footer -->
            <div style="background-color: #f9fafb; padding: 20px; text-align: center; border-top: 1px solid #f3f4f6; font-size: 12px; color: #9ca3af;">
                <p style="margin: 0;">Sent automatically by STALKER Server.</p>
            </div>
        </div>
    </body>
    </html>
    """
    _send_via_brevo(subject, html_body)


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
    """Send EOD report via Brevo REST API over HTTPS."""
    try:
        date_str  = eod_data.get("date", "today")
        picks     = eod_data.get("picks", [])
        wins      = sum(1 for p in picks if (p.get("pnl_pct") or 0) > 0)
        losses    = sum(1 for p in picks if (p.get("pnl_pct") or 0) < 0)
        
        # Calculate today's specific 1-day metrics
        total_executed = wins + losses
        today_win_rate = (wins / total_executed) * 100 if total_executed > 0 else 0.0
        
        executed_picks = [p for p in picks if p.get("pnl_pct") is not None]
        today_avg_pnl = (sum(p.get("pnl_pct") for p in executed_picks) / len(executed_picks)) if executed_picks else 0.0
        today_pnl_color = "#16a34a" if today_avg_pnl >= 0 else "#dc2626"
        
        is_test = eod_data.get("is_test", False)
        subject_prefix = "⚠️ [TEST / SIMULATED] " if is_test else ""
        subject = f"{subject_prefix}📊 STALKER EOD Report — {date_str} | Today's WR: {today_win_rate:.1f}% | W:{wins} L:{losses}"
        
        rows_html = ""
        for i, p in enumerate(picks, 1):
            name = p.get('name', '')
            action = p.get('action', '')
            action_color = "#16a34a" if action == "BUY" else "#d97706"
            action_bg = "#f0fdf4" if action == "BUY" else "#fffbeb"
            
            open_p = p.get("open") if p.get("open") is not None else 0.0
            close_p = p.get("close") if p.get("close") is not None else 0.0
            high_p = p.get("high") if p.get("high") is not None else 0.0
            low_p = p.get("low") if p.get("low") is not None else 0.0
            target_p = p.get("target") if p.get("target") is not None else 0.0
            sl_p = p.get("stop_loss") if p.get("stop_loss") is not None else 0.0
            
            pnl = p.get("pnl_pct")
            pnl_str = f"{pnl:+.2f}%" if pnl is not None else "Pending"
            pnl_color = "#16a34a" if (pnl or 0) > 0 else "#dc2626" if (pnl or 0) < 0 else "#6b7280"
            pnl_font_weight = "bold" if pnl is not None else "normal"
            
            # Rating calculation
            rating = 5  # default
            if pnl is not None:
                if action == "BUY":
                    if pnl > 0:
                        if target_p > open_p and close_p >= target_p:
                            rating = 10
                        else:
                            rating = 8
                    else:
                        if sl_p > 0 and close_p <= sl_p:
                            rating = 0
                        else:
                            rating = 3
                else: # WATCH / AVOID
                    if pnl < 0:
                        rating = 10
                    elif pnl > 0:
                        rating = 3
                    else:
                        rating = 5
            
            rating_color = "#16a34a" if rating >= 8 else "#d97706" if rating >= 5 else "#dc2626"
            
            rows_html += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 12px 8px; font-weight: bold; color: #1f2937;">{i}. {name}</td>
                <td style="padding: 12px 8px; text-align: center;">
                    <span style="display: inline-block; padding: 4px 8px; border-radius: 9999px; font-size: 11px; font-weight: bold; background-color: {action_bg}; color: {action_color};">
                        {action}
                    </span>
                </td>
                <td style="padding: 12px 8px; text-align: right; color: #4b5563;">₹{open_p:,.2f}</td>
                <td style="padding: 12px 8px; text-align: right; color: #4b5563;">₹{close_p:,.2f}</td>
                <td style="padding: 12px 8px; text-align: right; color: #6b7280; font-size: 12px;">₹{high_p:,.2f} / ₹{low_p:,.2f}</td>
                <td style="padding: 12px 8px; text-align: right; color: {pnl_color}; font-weight: {pnl_font_weight};">{pnl_str}</td>
                <td style="padding: 12px 8px; text-align: center; color: {rating_color}; font-weight: bold; font-size: 15px;">{rating}<span style="font-size: 11px; color: #9ca3af;">/10</span></td>
            </tr>
            """
            
        test_banner_html = ""
        if is_test:
            test_banner_html = """
            <div style="background-color: #fef3c7; border-left: 4px solid #d97706; padding: 12px 16px; margin: 15px 24px; border-radius: 4px; font-size: 13px; color: #92400e; text-align: left;">
                <strong>⚠️ Sandbox Test Mode:</strong> Today's market is closed. This report contains simulated data based on the latest available trading day's prices for testing purposes. Real database records have not been altered.
            </div>
            """

        html_body = f"""
        <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f3f4f6; padding: 20px; margin: 0;">
            <div style="max-width: 720px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 30px 24px; text-align: center; color: #ffffff;">
                    <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 0.5px;">📊 END OF DAY REPORT</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px; font-weight: 500;">Performance Audit & Trades Summary</p>
                </div>
                
                {test_banner_html}
                
                <!-- Info Section -->
                <div style="padding: 24px; background-color: #ffffff;">
                    <!-- Key Cards -->
                    <div style="display: flex; gap: 15px; margin-bottom: 25px;">
                        <div style="flex: 1; padding: 15px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; text-align: center;">
                            <div style="font-size: 11px; color: #64748b; font-weight: bold; text-transform: uppercase; margin-bottom: 5px;">Today's Win Rate</div>
                            <div style="font-size: 22px; font-weight: 800; color: #1e3b8a;">{today_win_rate:.1f}%</div>
                        </div>
                        <div style="flex: 1; padding: 15px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; text-align: center;">
                            <div style="font-size: 11px; color: #64748b; font-weight: bold; text-transform: uppercase; margin-bottom: 5px;">Today's Avg P&L</div>
                            <div style="font-size: 22px; font-weight: 800; color: {today_pnl_color};">{today_avg_pnl:+.2f}%</div>
                        </div>
                        <div style="flex: 1; padding: 15px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; text-align: center;">
                            <div style="font-size: 11px; color: #64748b; font-weight: bold; text-transform: uppercase; margin-bottom: 5px;">Today's Results</div>
                            <div style="font-size: 20px; font-weight: 800; color: #475569;">W: <span style="color:#16a34a;">{wins}</span> | L: <span style="color:#dc2626;">{losses}</span></div>
                        </div>
                    </div>
                    
                    <!-- Table Title -->
                    <h3 style="margin: 0 0 12px 0; color: #0f172a; font-weight: 700; font-size: 18px;">📈 Trade Execution & Analysis</h3>
                    
                    <!-- Table -->
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                        <thead>
                            <tr style="background-color: #f8fafc; border-bottom: 2px solid #e2e8f0; color: #475569; font-weight: bold;">
                                <th style="padding: 10px 8px; text-align: left;">Stock</th>
                                <th style="padding: 10px 8px; text-align: center;">Type</th>
                                <th style="padding: 10px 8px; text-align: right;">Entry (Open)</th>
                                <th style="padding: 10px 8px; text-align: right;">Exit (Close)</th>
                                <th style="padding: 10px 8px; text-align: right;">High / Low</th>
                                <th style="padding: 10px 8px; text-align: right;">P&L %</th>
                                <th style="padding: 10px 8px; text-align: center;">Rating</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows_html}
                        </tbody>
                    </table>
                </div>
                
                <!-- Footer -->
                <div style="background-color: #f9fafb; padding: 20px; text-align: center; border-top: 1px solid #f3f4f6; font-size: 12px; color: #9ca3af;">
                    <p style="margin: 0;">Sent automatically by STALKER Server on Render.</p>
                    <p style="margin: 5px 0 0 0;">This email is confidential and intended solely for the recipient.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        _send_via_brevo(subject, html_body)
    except Exception as e:
        logger.error(f"EOD email failed to build: {e}")


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

    # 8:15 AM — Price verification: cross-check picks vs live prices, update if drifted
    schedule.every().monday.at("08:15").do(verify_picks_prices)
    schedule.every().tuesday.at("08:15").do(verify_picks_prices)
    schedule.every().wednesday.at("08:15").do(verify_picks_prices)
    schedule.every().thursday.at("08:15").do(verify_picks_prices)
    schedule.every().friday.at("08:15").do(verify_picks_prices)

    # 8:30 AM — Send morning email with today's top picks (verified prices)
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

    logger.info("Schedule: 7:00 AM analysis | 8:15 AM price verify | 8:30 AM morning email | 9:20 AM open | 3:35 PM close | 4:00 PM EOD report")


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
