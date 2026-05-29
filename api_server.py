# -*- coding: utf-8 -*-
"""
STALKER - Live API Server
Serves the dashboard (static files) + JSON API endpoints.
Fetches live NSE prices every 20 seconds from Yahoo Finance.

Endpoints:
  GET /               → dashboard
  GET /api/picks      → today's top picks + metadata
  GET /api/live       → live prices for picked stocks (cached 20s)
  GET /api/performance → 30-day performance stats
  GET /api/sectors    → sector strength
"""

import json
import math
import os
import sys
import time
import threading
import logging
from datetime import datetime, date
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from typing import Dict, List, Optional

import yfinance as yf

import config
from data_fetcher import get_browser_session, is_nse_holiday
import db_manager

logger = logging.getLogger(__name__)

# ⚠️ TEMPORARY — HEARTBEAT STATE — REMOVE AFTER TESTING ⚠️
_last_heartbeat_time: float = 0.0
_HEARTBEAT_INTERVAL_SEC: int = 10 * 60   # every 10 minutes

def _maybe_send_heartbeat():
    """Send heartbeat email if 10+ min passed. Called on every HTTP request."""
    global _last_heartbeat_time
    now = time.time()
    if now - _last_heartbeat_time < _HEARTBEAT_INTERVAL_SEC:
        return
    _last_heartbeat_time = now
    try:
        import test_heartbeat
        threading.Thread(
            target=test_heartbeat.send_heartbeat_email,
            daemon=True,
            name="HeartbeatMailer"
        ).start()
        logger.info("[HEARTBEAT] Triggered heartbeat email")
    except Exception as e:
        logger.error(f"[HEARTBEAT] Failed: {e}")
# ⚠️ END TEMPORARY

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _safe_float(val, ndigits: int = 2):
    """Return rounded float or None if val is None / NaN."""
    try:
        f = float(val)
        return round(f, ndigits) if not math.isnan(f) else None
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────
# LIVE PRICE CACHE (updated every 20 seconds)
# ─────────────────────────────────────────────
_price_cache: Dict = {}
_cache_lock  = threading.Lock()
_cache_time  = 0
CACHE_TTL    = 20   # seconds

# Symbols currently being tracked (today's picks)
_tracked_symbols: List[str] = []


def _fetch_live_prices(symbols: List[str]) -> Dict:
    """
    Fetch the latest LIVE price for each symbol.

    Strategy (market open):
      1. Bulk download 5d daily bars -> extract yesterday's close for ALL symbols.
      2. Bulk download 1d/1m intraday bars -> today's last price + session H/L/volume.
      3. Serial fast_info fallback for any symbol that failed both bulk downloads.

    Strategy (market closed):
      1. Bulk download 5d daily bars -> last session's close + prev close for % change.
    """
    if not symbols:
        return {}

    results = {}
    total = len(symbols)

    # Determine if NSE market is currently open (IST)
    from datetime import timezone, timedelta
    IST_OFFSET = timedelta(hours=5, minutes=30)
    now_ist  = datetime.now(timezone.utc) + IST_OFFSET
    now_mins = now_ist.hour * 60 + now_ist.minute
    is_market_open = (
        now_ist.weekday() < 5
        and 9 * 60 + 15 <= now_mins <= 15 * 60 + 30
        and not is_nse_holiday(now_ist.date())
    )

    logger.debug(f"Live prices: market_open={is_market_open}, symbols={total}")

    # Step 1: Pre-fetch previous closes via ONE bulk 5d daily download.
    # This gives us yesterday's close cheaply for ALL symbols at once.
    prev_closes: dict = {}
    last_closes: dict = {}
    try:
        daily_bulk = yf.download(
            symbols, period="5d", interval="1d",
            group_by="ticker", threads=True, progress=False,
            session=get_browser_session(),
        )
        for sym in symbols:
            try:
                df_d = daily_bulk[sym].copy() if total > 1 else daily_bulk
                df_d.dropna(subset=["Close"], inplace=True)
                if df_d.empty:
                    continue
                if len(df_d) >= 2:
                    prev_closes[sym] = float(df_d.iloc[-2]["Close"])
                elif len(df_d) == 1:
                    prev_closes[sym] = float(df_d.iloc[-1]["Open"])
                last_closes[sym] = float(df_d.iloc[-1]["Close"])
            except Exception:
                pass
    except Exception as daily_err:
        logger.warning(f"Daily bulk pre-fetch failed: {daily_err}")

    # Step 2: If market is open, fetch 1m intraday for live price + today's H/L
    intraday: dict = {}
    if is_market_open:
        try:
            intraday_bulk = yf.download(
                symbols, period="1d", interval="1m",
                group_by="ticker", threads=True, progress=False,
                session=get_browser_session(),
            )
            for sym in symbols:
                try:
                    df_m = intraday_bulk[sym].copy() if total > 1 else intraday_bulk
                    df_m.dropna(subset=["Close"], inplace=True)
                    if df_m.empty:
                        continue
                    intraday[sym] = {
                        "last_price": float(df_m.iloc[-1]["Close"]),
                        "day_high":   float(df_m["High"].max()),
                        "day_low":    float(df_m["Low"].min()),
                        "volume":     int(df_m["Volume"].sum()),
                    }
                except Exception:
                    pass
        except Exception as intra_err:
            logger.warning(f"Intraday 1m bulk failed: {intra_err}")

    # Step 3: Build results from what we have
    handled = set()
    for symbol in symbols:
        try:
            prev_close = prev_closes.get(symbol)

            if is_market_open:
                iv = intraday.get(symbol)
                if iv:
                    last_price = iv["last_price"]
                    day_high   = iv["day_high"]
                    day_low    = iv["day_low"]
                    volume     = iv["volume"]
                elif symbol in last_closes:
                    last_price = last_closes[symbol]
                    day_high   = None
                    day_low    = None
                    volume     = None
                else:
                    continue
            else:
                if symbol not in last_closes:
                    continue
                last_price = last_closes[symbol]
                day_high   = None
                day_low    = None
                volume     = None

            change     = round(last_price - prev_close, 2) if prev_close else 0.0
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

            results[symbol] = {
                "symbol":     symbol,
                "name":       symbol.replace(".NS", "").replace(".BO", ""),
                "price":      round(last_price, 2),
                "prev_close": round(prev_close, 2) if prev_close else None,
                "change":     change,
                "change_pct": change_pct,
                "day_high":   _safe_float(day_high),
                "day_low":    _safe_float(day_low),
                "volume":     volume,
                "direction":  "up" if change >= 0 else "down",
                "fetched_at": datetime.now().isoformat(),
            }
            handled.add(symbol)
        except Exception as e:
            logger.debug(f"Error building result for {symbol}: {e}")

    # Step 4: Serial fast_info retry for any symbols still missing
    missing = [s for s in symbols if s not in handled]
    if missing:
        logger.info(f"Retrying {len(missing)} missing symbols via serial history/fast_info: {missing}")
        for symbol in missing:
            try:
                ticker    = yf.Ticker(symbol, session=get_browser_session())
                last_price = None
                prev_close = None
                day_high   = None
                day_low    = None

                # Try fast_info first (lightweight, but can crash on some symbols)
                try:
                    fi         = ticker.fast_info
                    last_price = _safe_float(fi.last_price)
                    prev_close = _safe_float(fi.previous_close)
                    day_high   = _safe_float(fi.day_high)
                    day_low    = _safe_float(fi.day_low)
                except Exception:
                    pass

                # Fall back to ticker.history if fast_info failed
                if last_price is None:
                    try:
                        df_hist = ticker.history(period="5d")
                        df_hist.dropna(subset=["Close"], inplace=True)
                        if not df_hist.empty:
                            last_price = float(df_hist.iloc[-1]["Close"])
                            if len(df_hist) >= 2:
                                prev_close = float(df_hist.iloc[-2]["Close"])
                    except Exception:
                        pass

                if last_price is None:
                    continue

                change     = round(last_price - prev_close, 2) if prev_close else 0.0
                change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
                results[symbol] = {
                    "symbol":     symbol,
                    "name":       symbol.replace(".NS", "").replace(".BO", ""),
                    "price":      last_price,
                    "prev_close": prev_close,
                    "change":     change,
                    "change_pct": change_pct,
                    "day_high":   day_high,
                    "day_low":    day_low,
                    "volume":     None,
                    "direction":  "up" if change >= 0 else "down",
                    "fetched_at": datetime.now().isoformat(),
                }
                time.sleep(0.2)
            except Exception as fe:
                logger.error(f"Serial fallback failed for {symbol}: {fe}")

    return results



def _refresh_cache():
    """Background thread: refreshes live prices every CACHE_TTL seconds."""
    global _price_cache, _cache_time

    while True:
        try:
            # Check market hours (IST timezone)
            from datetime import timezone, timedelta
            IST_OFFSET = timedelta(hours=5, minutes=30)
            now_ist    = datetime.now(timezone.utc) + IST_OFFSET
            
            # Check holiday / weekend
            from data_fetcher import is_nse_holiday
            is_closed = is_nse_holiday(now_ist.date())
            
            current_mins = now_ist.hour * 60 + now_ist.minute
            is_outside_hours = (current_mins < 9*60+15 or current_mins > 16*60) # before 9:15 AM or after 4:00 PM IST
            
            if is_closed or is_outside_hours:
                # Polling paused. Check again in 30 seconds.
                time.sleep(30)
                continue

            with _cache_lock:
                symbols = list(_tracked_symbols)

            if symbols:
                fresh = _fetch_live_prices(symbols)
                with _cache_lock:
                    _price_cache = fresh
                    _cache_time  = time.time()
                logger.debug(f"Live price cache refreshed for {len(fresh)} symbols")

        except Exception as e:
            logger.error(f"Cache refresh error: {e}")

        time.sleep(CACHE_TTL)


def get_cached_prices() -> Dict:
    """Return cached live prices (or fetch immediately if cache is empty/stale)."""
    global _price_cache, _cache_time

    with _cache_lock:
        age = time.time() - _cache_time
        if age < CACHE_TTL * 2 and _price_cache:
            return dict(_price_cache)
        symbols = list(_tracked_symbols)

    # Cache miss — fetch synchronously
    if symbols:
        fresh = _fetch_live_prices(symbols)
        with _cache_lock:
            _price_cache = fresh
            _cache_time  = time.time()
        return fresh

    return {}


def update_tracked_symbols(symbols: List[str]):
    """Called when today's picks change — update which symbols we track live."""
    global _tracked_symbols
    with _cache_lock:
        _tracked_symbols = list(symbols)
    logger.info(f"Tracking {len(symbols)} symbols for live prices")


# ─────────────────────────────────────────────
# API RESPONSES
# ─────────────────────────────────────────────

# Thread safety lock for background scanning
_scan_lock = threading.Lock()
_is_scanning = False

def _run_background_scan_task():
    """Background thread: run pre-market analysis (scan + save to DB, no email)."""
    global _is_scanning
    logger.info("Auto-triggering background pre-market analysis...")
    try:
        import main
        # Use run_premarket_analysis (scan + DB save only)
        # run_morning_scan is NOT called here — the scheduler handles the email at 8:30 AM
        main.run_premarket_analysis()
        logger.info("Background pre-market analysis completed successfully")
    except Exception as e:
        logger.error(f"Auto background scan failed: {e}", exc_info=True)
    finally:
        with _scan_lock:
            _is_scanning = False


def api_picks() -> Dict:
    """Return today's scan picks from DB."""
    global _is_scanning
    
    # 🛑 weekend or NSE holiday check
    if is_nse_holiday(date.today()):
        return {
            "_is_scanning": False,
            "_has_today_picks": False,
            "_market_closed": True,
            "message": "Market is closed today. Have a wonderful day!",
            "picks": [],
            "top_picks": []
        }

    today = str(date.today())
    data = db_manager.get_today_picks()
    if not data:
        # Try local JSON fallback
        scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
        if os.path.exists(scan_path):
            try:
                with open(scan_path) as f:
                    data = json.load(f)
            except Exception:
                data = None

    # Check if the data is actually for today
    has_today_picks = data is not None and data.get("date") == today

    if not has_today_picks:
        # Today's picks don't exist yet, trigger background scan
        with _scan_lock:
            if not _is_scanning:
                _is_scanning = True
                threading.Thread(target=_run_background_scan_task, daemon=True).start()

    if data:
        # Update tracked symbols from picks
        picks = data.get("picks", [])
        syms  = [p["symbol"] for p in picks if p.get("symbol")]
        if syms:
            update_tracked_symbols(syms)

    response = data or {}
    response["_is_scanning"] = _is_scanning
    response["_has_today_picks"] = has_today_picks
    return response


def api_live_prices() -> Dict:
    """Return live prices with P&L vs morning open."""
    if is_nse_holiday(date.today()):
        return {
            "prices": {},
            "count": 0,
            "cache_age_sec": 0,
            "fetched_at": datetime.now().isoformat(),
            "_market_closed": True
        }

    prices   = get_cached_prices()
    today    = str(date.today())

    # Get morning open and close prices to calculate live P&L
    db_prices  = db_manager.get_prices_for_date(today)
    open_prices = db_prices.get("open", {})
    close_prices = db_prices.get("close", {})

    # Also try local JSON
    if not open_prices:
        open_path = os.path.join(config.DATA_DIR, "open_prices.json")
        if os.path.exists(open_path):
            with open(open_path) as f:
                raw = json.load(f)
                open_prices = raw.get("prices", {})

    if not close_prices:
        close_path = os.path.join(config.DATA_DIR, "close_prices.json")
        if os.path.exists(close_path):
            try:
                with open(close_path) as f:
                    raw = json.load(f)
                    close_prices = raw.get("prices", {})
            except Exception:
                close_prices = {}

    # Enrich with P&L from morning entry and EOD close price
    enriched = {}
    for symbol, data in prices.items():
        entry = open_prices.get(symbol, {})
        open_price = entry.get("open") or entry.get("current_price")

        close_entry = close_prices.get(symbol, {})
        close_price = close_entry.get("close") or close_entry.get("current_price")

        live_pnl_pct = None
        live_pnl_rs  = None
        if open_price and open_price > 0:
            # Use EOD close price if available, otherwise fallback to live price
            ref_price = close_price if close_price else data["price"]
            live_pnl_rs  = round(ref_price - open_price, 2)
            live_pnl_pct = round((live_pnl_rs / open_price) * 100, 2)

        enriched[symbol] = {
            **data,
            "open_price":    round(open_price, 2) if open_price else None,
            "close_price":   round(close_price, 2) if close_price else None,
            "live_pnl_rs":   live_pnl_rs,
            "live_pnl_pct":  live_pnl_pct,
            "pnl_direction": "profit" if (live_pnl_pct or 0) > 0 else ("loss" if (live_pnl_pct or 0) < 0 else "neutral"),
        }

    return {
        "prices":       enriched,
        "count":        len(enriched),
        "cache_age_sec": round(time.time() - _cache_time, 1),
        "fetched_at":   datetime.now().isoformat(),
    }


def api_performance() -> Dict:
    """Return 30-day performance stats."""
    return db_manager.get_performance_summary(30)


def api_sectors() -> Dict:
    """Return latest sector trends from last scan."""
    scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
    if os.path.exists(scan_path):
        with open(scan_path) as f:
            data = json.load(f)
            return {
                "sector_trends": data.get("sector_trends", {}),
                "market_trend":  data.get("market_trend", "unknown"),
            }
    return {"sector_trends": {}, "market_trend": "unknown"}


def api_schedule() -> Dict:
    """
    Return the daily automation schedule and status of each task.
    Shows whether each task is done, running, or pending for today (IST).
    """
    from datetime import timezone, timedelta

    IST_OFFSET = timedelta(hours=5, minutes=30)
    now_ist    = datetime.now(timezone.utc) + IST_OFFSET
    now_mins   = now_ist.hour * 60 + now_ist.minute
    weekday    = now_ist.weekday()  # 0=Mon, 6=Sun

    TASKS = [
        {
            "name":      "Pre-Market Analysis",
            "icon":      "🔍",
            "scheduled": "07:00",
            "desc":      f"Full NSE+BSE scan ({len(config.ALL_SYMBOLS)} stocks) using 6-layer scoring — picks today's top 15-20",
        },
        {
            "name":      "Morning Email Dispatch",
            "icon":      "📧",
            "scheduled": "08:30",
            "desc":      "Today's top picks emailed to inbox before market opens at 9:15 AM",
        },
        {
            "name":      "Open Prices Locked",
            "icon":      "🔐",
            "scheduled": "09:20",
            "desc":      "Entry prices recorded once market stabilises after opening",
        },
        {
            "name":      "Close Prices Recorded",
            "icon":      "📦",
            "scheduled": "15:35",
            "desc":      "End-of-day prices saved to MongoDB for P&L calculation",
        },
        {
            "name":      "EOD Report & Email",
            "icon":      "📊",
            "scheduled": "16:00",
            "desc":      "Full P&L results, trade ratings and performance summary emailed",
        },
    ]

    for task in TASKS:
        h, m         = map(int, task["scheduled"].split(":"))
        task_mins    = h * 60 + m
        diff         = task_mins - now_mins

        if weekday >= 5:
            task["status"]      = "weekend"
            task["minutes_away"] = None
        elif diff <= -5:
            task["status"]      = "done"
            task["minutes_away"] = 0
        elif -5 < diff <= 10:
            task["status"]      = "running"
            task["minutes_away"] = max(0, diff)
        else:
            task["status"]      = "pending"
            task["minutes_away"] = diff

    market_open = weekday < 5 and (9*60+15) <= now_mins <= (15*60+30)

    # Pull scan metadata to enrich the schedule page
    picks_data = {}
    try:
        picks_data = db_manager.get_today_picks() or {}
    except Exception:
        pass

    return {
        "tasks":          TASKS,
        "current_ist":    now_ist.strftime("%H:%M:%S"),
        "today":          now_ist.strftime("%Y-%m-%d"),
        "weekday":        weekday,
        "weekday_name":   now_ist.strftime("%A"),
        "is_market_day":  weekday < 5,
        "market_open":    market_open,
        "is_scanning":    _is_scanning,
        "picks_count":    len(picks_data.get("picks", [])),
        "scanned_count":  picks_data.get("scanned", 0),
        "scan_time":      picks_data.get("scan_time", ""),
        "market_trend":   picks_data.get("market_trend", ""),
        "universe_size":  len(config.ALL_SYMBOLS),
    }


def api_eod() -> Dict:
    """Return today's EOD report (P&L results) if generated (available after 4 PM)."""
    today = str(date.today())

    # Try JSON file first (fastest)
    eod_path = os.path.join(config.DATA_DIR, "eod_report.json")
    if os.path.exists(eod_path):
        try:
            with open(eod_path) as f:
                data = json.load(f)
            if data.get("date") == today:
                data["available"] = True
                return data
        except Exception:
            pass

    # Try MongoDB
    try:
        db = db_manager.get_db()
        if db is not None:
            record = db["eod_reports"].find_one({"date": today}, {"_id": 0})
            if record:
                record["available"] = True
                return record
    except Exception:
        pass

    return {"available": False, "date": today}


# ─────────────────────────────────────────────
# HTTP REQUEST HANDLER
# ─────────────────────────────────────────────

class StalkerHandler(SimpleHTTPRequestHandler):
    """
    Hybrid handler: serves static dashboard files + /api/* JSON endpoints.
    """

    def log_message(self, fmt, *args):
        # Only log API calls, not static file loads
        try:
            req_str = str(args[0]) if args else ""
            if "/api/" in req_str:
                logger.debug(f"API: {fmt % args}")
        except Exception:
            pass

    def do_GET(self):
        # _maybe_send_heartbeat()  # ⚠️ TEMP — fires email every 10 min via UptimeRobot pings
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        # ── API Routes ──────────────────────────────────────────
        if path == "/api/picks":
            self._json(api_picks())

        elif path == "/api/live":
            self._json(api_live_prices())

        elif path == "/api/performance":
            self._json(api_performance())

        elif path == "/api/sectors":
            self._json(api_sectors())

        elif path == "/api/schedule":
            self._json(api_schedule())

        elif path == "/api/eod":
            self._json(api_eod())

        elif path == "/api/status":
            self._json({
                "status":         "running",
                "time":           datetime.now().isoformat(),
                "tracked":        len(_tracked_symbols),
                "cache_age_sec":  round(time.time() - _cache_time, 1),
                "universe_size":  len(config.ALL_SYMBOLS),
                "is_scanning":    _is_scanning,
            })

        # ⚠️ TEMPORARY — REMOVE AFTER TESTING ⚠️
        elif path == "/api/heartbeat-test":
            try:
                import test_heartbeat
                test_heartbeat.send_heartbeat_email()
                self._json({
                    "result":          "sent",
                    "to":              test_heartbeat.FORMSUBMIT_TO,
                    "heartbeat_count": test_heartbeat._heartbeat_count,
                    "server_time":     datetime.now().isoformat(),
                    "note":            "Check your inbox. Remove endpoint after testing.",
                })
            except Exception as e:
                self._json({"result": "error", "error": str(e)})

        elif path == "/api/morning-test":
            try:
                import main
                # Try to get today's actual picks, or fall back to mock picks
                scan_data = api_picks()
                picks = scan_data.get("picks", [])
                
                # Transform database picks format to top_picks format expected by _send_morning_email
                top_picks = []
                for p in picks:
                    top_picks.append({
                        "name": p.get("name", p.get("symbol", "")),
                        "symbol": p.get("symbol", ""),
                        "action": p.get("action", "BUY"),
                        "current_price": p.get("current_price", 0),
                        "target_2": p.get("target_2", p.get("target", 0)),
                        "stop_loss": p.get("stop_loss", 0),
                        "total_score": p.get("total_score", p.get("score", 0)),
                        "risk_profile": p.get("risk_profile", "Medium")
                    })
                
                scan_result = {
                    "market_trend": scan_data.get("market_trend", "bullish"),
                    "top_picks": top_picks
                }
                
                if not top_picks:
                    # Use mock picks if no picks are generated yet for today
                    scan_result = {
                        "market_trend": "bullish",
                        "top_picks": [
                            {"name": "RELIANCE.NS", "action": "BUY", "current_price": 1350.50, "target_2": 1385.00, "stop_loss": 1332.00, "total_score": 88.5, "risk_profile": "Medium"},
                            {"name": "TCS.NS", "action": "BUY", "current_price": 2284.20, "target_2": 2340.00, "stop_loss": 2250.00, "total_score": 85.2, "risk_profile": "Low"},
                            {"name": "INFY.NS", "action": "WATCH", "current_price": 1159.90, "target_2": 1195.00, "stop_loss": 1140.00, "total_score": 76.8, "risk_profile": "High"},
                        ]
                    }
                
                main._send_morning_email(scan_result)
                self._json({
                    "result":          "sent",
                    "to":              os.getenv("FORMSUBMIT_TO", ""),
                    "picks_sent":      len(scan_result.get("top_picks", [])),
                    "server_time":     datetime.now().isoformat(),
                    "note":            "Morning Picks test email sent via Brevo! Check your inbox.",
                })
            except Exception as e:
                self._json({"result": "error", "error": str(e)})
        # ⚠️ END TEMPORARY

        else:
            # Serve static dashboard files
            super().do_GET()

    def _json(self, data: dict):
        """Send a JSON response."""
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",  "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


# ─────────────────────────────────────────────
# SERVER STARTUP
# ─────────────────────────────────────────────

def start_server(port: int = config.DASHBOARD_PORT, open_browser: bool = True):
    """
    Start the STALKER live dashboard server.
    Serves dashboard + API, polls live prices in background.
    """
    # Change working dir to dashboard folder so static files are served
    dashboard_dir = os.path.join(config.BASE_DIR, "dashboard")
    os.chdir(dashboard_dir)

    # Load today's picks immediately (may auto-trigger scan if missing)
    picks_data = api_picks()
    picks = picks_data.get("picks", [])
    if picks:
        syms = [p["symbol"] for p in picks if p.get("symbol")]
        update_tracked_symbols(syms)
        print(f"  Tracking {len(syms)} stocks for live prices")
    else:
        print("  Auto-triggering pre-market scan in background...")

    # Start background price refresh thread
    t = threading.Thread(target=_refresh_cache, daemon=True)
    t.start()

    # Start daily morning scan & EOD reporting scheduler thread
    try:
        import main
        import schedule
        main.setup_schedule()
        
        def _run_scheduler_loop():
            while True:
                schedule.run_pending()
                time.sleep(30)
                
        threading.Thread(target=_run_scheduler_loop, daemon=True).start()
        print("  Background daily scheduler activated (8:30 AM scan, EOD reporting auto-enabled)")
    except Exception as e:
        print(f"  Failed to start background scheduler thread: {e}")



    # Open browser
    if open_browser:
        import webbrowser, threading as th
        th.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"\n  Dashboard:  http://localhost:{port}")
    print(f"  Live API:   http://localhost:{port}/api/live")
    print(f"  Picks API:  http://localhost:{port}/api/picks")
    print(f"  Press Ctrl+C to stop\n")

    server = HTTPServer(("", port), StalkerHandler)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    start_server()
