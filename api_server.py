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
import contextlib
from datetime import datetime, date
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from typing import Dict, List, Optional

import yfinance as yf

import config
from data_fetcher import get_browser_session, is_nse_holiday, is_rate_limited, mark_rate_limited
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
    Fetch the latest LIVE price for each symbol via yfinance.

    Market open:  1m intraday bars  → live price + day H/L + volume
    Market closed: 5d daily bars    → last session close + prev close
    Fallback:     serial fast_info  → for any symbol that fails bulk download
    """
    if not symbols:
        return {}

    results    = {}
    total      = len(symbols)

    @contextlib.contextmanager
    def _silence():
        with open(os.devnull, 'w') as devnull:
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                yield

    from datetime import timezone, timedelta
    IST_OFFSET   = timedelta(hours=5, minutes=30)
    now_ist      = datetime.now(timezone.utc) + IST_OFFSET
    now_mins     = now_ist.hour * 60 + now_ist.minute
    is_market_open = (
        now_ist.weekday() < 5
        and 9 * 60 + 15 <= now_mins <= 15 * 60 + 30
        and not is_nse_holiday(now_ist.date())
    )

    tickers_str = " ".join(symbols)

    # ── Step 1: Always fetch 5d daily for prev_close ────────────────────────
    prev_closes: dict = {}
    last_closes: dict = {}
    if not is_rate_limited():
        try:
            with _silence():
                daily = yf.download(
                    tickers_str, period="5d", interval="1d",
                    group_by="ticker", threads=False, progress=False,
                )
            for sym in symbols:
                try:
                    df = daily[sym].copy() if total > 1 else daily
                    df.dropna(subset=["Close"], inplace=True)
                    if df.empty:
                        continue
                    last_closes[sym] = float(df.iloc[-1]["Close"])
                    if len(df) >= 2:
                        prev_closes[sym] = float(df.iloc[-2]["Close"])
                    else:
                        prev_closes[sym] = float(df.iloc[-1]["Open"])
                except Exception:
                    pass
        except Exception as e:
            err = str(e)
            if "429" in err or "rate limit" in err.lower() or "ratelimit" in type(e).__name__.lower():
                mark_rate_limited()
            logger.warning(f"Daily bulk failed: {e}")

    # ── Step 2: If market open, fetch 1m intraday for live prices ───────────
    intraday: dict = {}
    if is_market_open and not is_rate_limited():
        try:
            with _silence():
                intra = yf.download(
                    tickers_str, period="1d", interval="1m",
                    group_by="ticker", threads=False, progress=False,
                )
            for sym in symbols:
                try:
                    df = intra[sym].copy() if total > 1 else intra
                    df.dropna(subset=["Close"], inplace=True)
                    if df.empty:
                        continue
                    intraday[sym] = {
                        "last_price": float(df.iloc[-1]["Close"]),
                        "day_high":   float(df["High"].max()),
                        "day_low":    float(df["Low"].min()),
                        "volume":     int(df["Volume"].sum()),
                    }
                except Exception:
                    pass
        except Exception as e:
            err = str(e)
            if "429" in err or "rate limit" in err.lower() or "ratelimit" in type(e).__name__.lower():
                mark_rate_limited()
            logger.warning(f"Intraday 1m failed: {e}")

    # ── Step 3: Build results ───────────────────────────────────────────────
    handled = set()
    for sym in symbols:
        try:
            prev_close  = prev_closes.get(sym)
            prev_cached = _price_cache.get(sym, {}) if isinstance(_price_cache, dict) else {}

            if is_market_open:
                iv = intraday.get(sym)
                if iv:
                    last_price = iv["last_price"]
                    day_high   = iv["day_high"]
                    day_low    = iv["day_low"]
                    volume     = iv["volume"]
                elif sym in last_closes:
                    last_price = last_closes[sym]
                    day_high   = prev_cached.get("day_high")
                    day_low    = prev_cached.get("day_low")
                    volume     = None
                else:
                    continue
            else:
                if sym not in last_closes:
                    continue
                last_price = last_closes[sym]
                day_high   = prev_cached.get("day_high")
                day_low    = prev_cached.get("day_low")
                volume     = None

            change     = round(last_price - prev_close, 2) if prev_close else 0.0
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
            results[sym] = {
                "symbol":     sym,
                "name":       sym.replace(".NS", "").replace(".BO", ""),
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
            handled.add(sym)
        except Exception as e:
            logger.debug(f"Result build error {sym}: {e}")

    # ── Step 4: Serial fast_info for any still missing ──────────────────────
    missing = [s for s in symbols if s not in handled]
    if missing and not is_rate_limited():
        logger.info(f"fast_info fallback for {len(missing)} symbols")
        for sym in missing:
            if is_rate_limited():
                break
            try:
                t = yf.Ticker(sym)
                lp = pc = dh = dl = None
                try:
                    fi = t.fast_info
                    lp = _safe_float(fi.last_price)
                    pc = _safe_float(fi.previous_close)
                    dh = _safe_float(fi.day_high)
                    dl = _safe_float(fi.day_low)
                except Exception:
                    pass
                if lp is None:
                    try:
                        h = t.history(period="5d")
                        h.dropna(subset=["Close"], inplace=True)
                        if not h.empty:
                            lp = float(h.iloc[-1]["Close"])
                            pc = float(h.iloc[-2]["Close"]) if len(h) >= 2 else None
                    except Exception:
                        pass
                if lp is None:
                    continue
                change     = round(lp - pc, 2) if pc else 0.0
                change_pct = round((change / pc) * 100, 2) if pc else 0.0
                prev_cached = _price_cache.get(sym, {})
                results[sym] = {
                    "symbol":     sym,
                    "name":       sym.replace(".NS", "").replace(".BO", ""),
                    "price":      lp,
                    "prev_close": pc,
                    "change":     change,
                    "change_pct": change_pct,
                    "day_high":   dh if dh is not None else prev_cached.get("day_high"),
                    "day_low":    dl if dl is not None else prev_cached.get("day_low"),
                    "volume":     None,
                    "direction":  "up" if change >= 0 else "down",
                    "fetched_at": datetime.now().isoformat(),
                }
                time.sleep(0.15)
            except Exception as fe:
                err = str(fe)
                if "429" in err or "rate limit" in err.lower() or "ratelimit" in type(fe).__name__.lower():
                    mark_rate_limited()
                logger.error(f"fast_info failed {sym}: {fe}")

    logger.info(f"[prices] Fetched {len(results)}/{total} symbols (market_open={is_market_open})")
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
            is_outside_hours = (current_mins < 9*60+15 or current_mins > 16*60)
            
            if is_closed or is_outside_hours:
                time.sleep(30)
                continue

            if is_rate_limited():
                logger.debug("[cache] Rate-limited — skipping fetch, keeping existing cache")
                time.sleep(CACHE_TTL)
                continue

            with _cache_lock:
                symbols = list(_tracked_symbols)

            if symbols:
                fresh = _fetch_live_prices(symbols)
                with _cache_lock:
                    if fresh:
                        _price_cache = fresh
                        _cache_time  = time.time()
                        logger.debug(f"[cache] Refreshed {len(fresh)} symbols")
                    else:
                        logger.warning("[cache] Fetch returned no data — keeping stale cache (will retry)")

        except Exception as e:
            logger.error(f"Cache refresh error: {e}")

        time.sleep(CACHE_TTL)


def get_cached_prices() -> Dict:
    """Return cached live prices (or fetch immediately if cache is empty/stale)."""
    global _price_cache, _cache_time

    with _cache_lock:
        age = time.time() - _cache_time
        if (age < CACHE_TTL * 2 and _price_cache) or is_rate_limited():
            return dict(_price_cache)
        symbols = list(_tracked_symbols)

    # Cache miss — fetch synchronously
    if symbols:
        fresh = _fetch_live_prices(symbols)
        with _cache_lock:
            if fresh:
                _price_cache = fresh
                _cache_time  = time.time()
            return dict(_price_cache)

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
    """Background thread: run pre-market analysis (scan + save to DB, no email).
    
    IMPORTANT: This runs in a daemon thread. sys.stdout may be the server's
    closed stream. We redirect all print() output to the logger to prevent
    'I/O operation on closed file' errors from screener.py's print() calls.
    """
    global _is_scanning
    logger.info("Auto-triggering background pre-market analysis...")

    # Redirect sys.stdout to a logger-backed stream for the duration of this thread.
    # This prevents screener.py / main.py print() calls from hitting the
    # server's potentially-closed stdout buffer.
    class _LoggerWriter:
        """Forwards write() calls to logging.info, stripping empty lines."""
        def __init__(self, log):
            self._log = log
            self._buf = ""

        def write(self, msg):
            self._buf += msg
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip()
                if line:
                    self._log.info(line)

        def flush(self):
            if self._buf.strip():
                self._log.info(self._buf.strip())
            self._buf = ""

        def fileno(self):
            raise OSError("LoggerWriter has no file descriptor")

    _thread_logger = logging.getLogger("stalker.background_scan")
    _safe_stdout = _LoggerWriter(_thread_logger)

    original_stdout = sys.stdout
    try:
        sys.stdout = _safe_stdout
        import main as _main
        _main.run_premarket_analysis()
        logger.info("Background pre-market analysis completed successfully.")
    except Exception as e:
        logger.error(f"Auto background scan failed: {e}", exc_info=True)
    finally:
        # Always restore original stdout
        try:
            sys.stdout = original_stdout
        except Exception:
            pass
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

    # Fetch picks data to get current/entry price from the scan as ultimate fallback
    picks_list = []
    try:
        picks_data = db_manager.get_today_picks()
        if not picks_data:
            scan_path = os.path.join(config.DATA_DIR, "latest_scan.json")
            if os.path.exists(scan_path):
                with open(scan_path) as f:
                    picks_data = json.load(f)
        if picks_data:
            picks_list = picks_data.get("picks", [])
    except Exception as pe:
        logger.debug(f"Could not load today's picks for fallback prices: {pe}")

    # Combine keys from both prices cache and _tracked_symbols to prevent dashboard Loading... state
    all_keys = set(prices.keys()) | set(_tracked_symbols)

    # Enrich with P&L from morning entry and EOD close price
    enriched = {}
    for symbol in all_keys:
        data = prices.get(symbol)
        
        entry = open_prices.get(symbol, {})
        open_price = entry.get("open") or entry.get("current_price")

        close_entry = close_prices.get(symbol, {})
        close_price = close_entry.get("close") or close_entry.get("current_price")

        if not data:
            # Missing from active yfinance cache, construct a safe fallback record
            pick_price = None
            for p in picks_list:
                if p.get("symbol") == symbol:
                    pick_price = p.get("current_price")
                    break
            
            ref_price = open_price or pick_price or 0.0
            
            data = {
                "symbol":     symbol,
                "name":       symbol.replace(".NS", "").replace(".BO", ""),
                "price":      round(ref_price, 2),
                "prev_close": round(open_price, 2) if open_price else None,
                "change":     0.0,
                "change_pct": 0.0,
                "day_high":   round(ref_price, 2) if ref_price > 0 else None,
                "day_low":    round(ref_price, 2) if ref_price > 0 else None,
                "volume":     None,
                "direction":  "neutral",
                "fetched_at": datetime.now().isoformat(),
            }

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
            "name":      "Pre-Market 12-Layer Quant Scan",
            "icon":      "🔍",
            "scheduled": "07:00",
            "desc":      f"Data Quality (>=70), Liquidity & Drawdown filters active. Scans {len(config.ALL_SYMBOLS)} stocks and ranks by Adjusted Alpha Score.",
        },
        {
            "name":      "Price Drift Verification",
            "icon":      "⚖️",
            "scheduled": "08:15",
            "desc":      "Cross-checks entry prices of today's picks against the live yfinance feed to correct pre-market price drift.",
        },
        {
            "name":      "Quant Portfolio Email Dispatch",
            "icon":      "📧",
            "scheduled": "08:30",
            "desc":      "Pearson daily correlation filter (r > 0.80) and sector caps applied. Today's top picks emailed to subscribers.",
        },
        {
            "name":      "Open Prices Locked",
            "icon":      "🔐",
            "scheduled": "09:20",
            "desc":      "Entry prices recorded once market stabilises after opening to track P&L performance.",
        },
        {
            "name":      "Closing Prices Recorded",
            "icon":      "📦",
            "scheduled": "15:35",
            "desc":      "End-of-day closing prices recorded to MongoDB database for performance audit.",
        },
        {
            "name":      "EOD Quant Performance & Market Breadth Audit",
            "icon":      "📊",
            "scheduled": "16:00",
            "desc":      "Calculates VWAP relative strength, breakout positions, sector grouping, and market breadth emailed to subscribers.",
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

    # Start daily morning scan & EOD reporting scheduler thread only in production (Render)
    # or if explicitly enabled locally (STALKER_RUN_SCHEDULER=true) to avoid duplicate emails.
    is_render = os.getenv("RENDER") == "true"
    run_scheduler = os.getenv("STALKER_RUN_SCHEDULER") == "true"

    if is_render or run_scheduler:
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
    else:
        print("  Background scheduler bypassed (running locally, evening/morning emails handled by production server)")



    # Open browser
    if open_browser:
        import webbrowser, threading as th
        th.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"\n  Dashboard:  http://localhost:{port}")
    print(f"  Live API:   http://localhost:{port}/api/live")
    print(f"  Picks API:  http://localhost:{port}/api/picks")
    print(f"  Press Ctrl+C to stop\n")

    server = ThreadingHTTPServer(("", port), StalkerHandler)
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    start_server()
