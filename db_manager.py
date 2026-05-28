"""
STALKER - Database Manager
Stores daily picks, open/close prices, and performance history.
Uses MongoDB when available, falls back to local JSON files.
"""

import json
import os
import logging
from datetime import datetime, date
from typing import Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Try MongoDB — fallback to JSON
# ─────────────────────────────────────────────
_mongo_client = None
_mongo_db     = None
_use_mongo    = False

def _init_mongo():
    global _mongo_client, _mongo_db, _use_mongo
    try:
        from pymongo import MongoClient
        _mongo_client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=3000)
        _mongo_client.server_info()   # Ping
        _mongo_db  = _mongo_client[config.MONGO_DB_NAME]
        _use_mongo = True
        logger.info("[OK] MongoDB connected")
    except Exception as e:
        _use_mongo = False
        logger.warning(f"MongoDB unavailable — using local JSON files. ({e})")


def get_db():
    global _use_mongo
    if not _use_mongo and _mongo_db is None:
        _init_mongo()
    return _mongo_db if _use_mongo else None


# ─────────────────────────────────────────────
# JSON fallback path helpers
# ─────────────────────────────────────────────

def _json_path(filename: str) -> str:
    return os.path.join(config.DATA_DIR, filename)


def _read_json(filename: str) -> list:
    path = _json_path(filename)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def _write_json(filename: str, data: list):
    path = _json_path(filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ─────────────────────────────────────────────
# SAVE DAILY PICKS (Morning)
# ─────────────────────────────────────────────

def save_daily_picks(scan_result: Dict) -> bool:
    """
    Save today's top picks with all metadata.
    Called at 8:30 AM after the morning scan.
    """
    today = str(date.today())

    record = {
        "date":          today,
        "scan_time":     scan_result.get("scan_time"),
        "market_trend":  scan_result.get("market_trend"),
        "sector_trends": scan_result.get("sector_trends"),
        "picks":         scan_result.get("top_picks", []),
        "scanned":       scan_result.get("scanned", 0),
        "created_at":    datetime.now().isoformat(),
    }

    db = get_db()
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            col.replace_one({"date": today}, record, upsert=True)
            logger.info(f"[DB] Picks saved to MongoDB for {today}")
            return True
        except Exception as e:
            logger.error(f"MongoDB save error: {e}")

    # JSON fallback
    all_picks = _read_json("daily_picks.json")
    all_picks = [p for p in all_picks if p.get("date") != today]  # Remove existing today
    all_picks.append(record)
    all_picks = all_picks[-90:]   # Keep last 90 days
    _write_json("daily_picks.json", all_picks)
    logger.info(f"[FILE] Picks saved to JSON for {today}")
    return True


# ─────────────────────────────────────────────
# SAVE PRICES (Open & Close)
# ─────────────────────────────────────────────

def save_open_prices(prices: Dict[str, Dict]) -> bool:
    """Save opening prices at 9:20 AM."""
    today = str(date.today())

    record = {
        "date":       today,
        "time":       "open",
        "time_of_day": "open",
        "timestamp":  datetime.now().isoformat(),
        "prices":     prices,
    }

    db = get_db()
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PRICES]
            col.replace_one({"date": today, "time": "open"}, record, upsert=True)
            return True
        except Exception as e:
            logger.error(f"MongoDB save open prices error: {e}")

    all_prices = _read_json("price_history.json")
    all_prices = [p for p in all_prices if not (p.get("date") == today and p.get("time") == "open")]
    all_prices.append(record)
    all_prices = all_prices[-180:]
    _write_json("price_history.json", all_prices)
    return True


def save_close_prices(prices: Dict[str, Dict]) -> bool:
    """Save closing prices at 3:35 PM."""
    today = str(date.today())

    record = {
        "date":       today,
        "time":       "close",
        "time_of_day": "close",
        "timestamp":  datetime.now().isoformat(),
        "prices":     prices,
    }

    db = get_db()
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PRICES]
            col.replace_one({"date": today, "time": "close"}, record, upsert=True)
            return True
        except Exception as e:
            logger.error(f"MongoDB save close prices error: {e}")

    all_prices = _read_json("price_history.json")
    all_prices = [p for p in all_prices if not (p.get("date") == today and p.get("time") == "close")]
    all_prices.append(record)
    all_prices = all_prices[-180:]
    _write_json("price_history.json", all_prices)
    return True


def save_eod_report(eod_data: Dict) -> bool:
    """Save the final EOD analysis report to the database."""
    today = eod_data.get("date", str(date.today()))

    db = get_db()
    if db is not None:
        try:
            col = db["eod_reports"]
            col.replace_one({"date": today}, eod_data, upsert=True)
            logger.info(f"[DB] EOD Report saved to MongoDB for {today}")
            return True
        except Exception as e:
            logger.error(f"MongoDB save EOD report error: {e}")

    all_reports = _read_json("eod_reports_history.json")
    all_reports = [r for r in all_reports if r.get("date") != today]
    all_reports.append(eod_data)
    all_reports = all_reports[-180:]
    _write_json("eod_reports_history.json", all_reports)
    logger.info(f"[FILE] EOD Report saved to JSON for {today}")
    return True


# ─────────────────────────────────────────────
# FETCH FOR DASHBOARD
# ─────────────────────────────────────────────

def get_today_picks() -> Optional[Dict]:
    """Get today's picks for the dashboard."""
    today = str(date.today())

    db = get_db()
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            record = col.find_one({"date": today}, {"_id": 0})
            if record:
                return record
        except Exception:
            pass

    all_picks = _read_json("daily_picks.json")
    for record in reversed(all_picks):
        if record.get("date") == today:
            return record
    return None


def get_recent_picks(days: int = 7) -> List[Dict]:
    """Get picks from the last N days."""
    db = get_db()
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            records = list(col.find({}, {"_id": 0}).sort("date", -1).limit(days))
            return records
        except Exception:
            pass

    all_picks = _read_json("daily_picks.json")
    return list(reversed(all_picks[-days:]))


def get_prices_for_date(target_date: str) -> Dict:
    """Get both open and close prices for a specific date."""
    db = get_db()
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PRICES]
            records = list(col.find({"date": target_date}, {"_id": 0}))
            result = {}
            for r in records:
                result[r["time"]] = r.get("prices", {})
            return result
        except Exception:
            pass

    all_prices = _read_json("price_history.json")
    result = {}
    for r in all_prices:
        if r.get("date") == target_date:
            result[r["time"]] = r.get("prices", {})
    return result


def get_performance_summary(days: int = 30) -> Dict:
    """
    Calculate win rate, avg P&L, best/worst pick over last N days.
    Used in dashboard performance panel.
    """
    recent = get_recent_picks(days)
    if not recent:
        return {"win_rate": 0, "total_trades": 0, "avg_pnl": 0, "best": None, "worst": None}

    wins = 0
    losses = 0
    total_pnl_pct = 0
    trade_count = 0
    best_pick = None
    worst_pick = None
    best_pnl = -999
    worst_pnl = 999

    for day_record in recent:
        day = day_record.get("date", "")
        picks = day_record.get("picks", [])
        prices = get_prices_for_date(day)

        open_prices  = prices.get("open", {})
        close_prices = prices.get("close", {})

        for pick in picks:
            symbol = pick.get("symbol")
            if not symbol:
                continue

            open_q  = open_prices.get(symbol, {})
            close_q = close_prices.get(symbol, {})

            open_price  = open_q.get("open") or open_q.get("current_price")
            close_price = close_q.get("close") or close_q.get("current_price")

            if not open_price or not close_price or open_price == 0:
                continue

            pnl_pct = ((close_price - open_price) / open_price) * 100
            trade_count += 1
            total_pnl_pct += pnl_pct

            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1

            if pnl_pct > best_pnl:
                best_pnl  = pnl_pct
                best_pick = {"symbol": symbol, "pnl_pct": round(pnl_pct, 2), "date": day}

            if pnl_pct < worst_pnl:
                worst_pnl  = pnl_pct
                worst_pick = {"symbol": symbol, "pnl_pct": round(pnl_pct, 2), "date": day}

    win_rate = (wins / trade_count * 100) if trade_count > 0 else 0
    avg_pnl  = (total_pnl_pct / trade_count) if trade_count > 0 else 0

    return {
        "win_rate":    round(win_rate, 1),
        "total_trades": trade_count,
        "wins":        wins,
        "losses":      losses,
        "avg_pnl":     round(avg_pnl, 2),
        "best":        best_pick,
        "worst":       worst_pick,
    }


def cleanup_old_data(days_to_keep: int = 10):
    """
    Purge records older than N days from MongoDB to free up space.
    Keep latest `days_to_keep` days.
    """
    from datetime import timedelta
    limit_date = str(date.today() - timedelta(days=days_to_keep))
    logger.info(f"[CLEANUP] Starting MongoDB cleanup: deleting data older than {limit_date} ({days_to_keep} days ago)")
    
    db = get_db()
    if db is None:
        logger.info("MongoDB not connected. Skipping cleanup.")
        return False
        
    try:
        # 1. Purge daily picks
        picks_col = db[config.MONGO_COLLECTION_PICKS]
        picks_res = picks_col.delete_many({"date": {"$lt": limit_date}})
        logger.info(f"Deleted {picks_res.deleted_count} old records from {config.MONGO_COLLECTION_PICKS}")
        
        # 2. Purge price history
        prices_col = db[config.MONGO_COLLECTION_PRICES]
        prices_res = prices_col.delete_many({"date": {"$lt": limit_date}})
        logger.info(f"Deleted {prices_res.deleted_count} old records from {config.MONGO_COLLECTION_PRICES}")
        
        # 3. Purge EOD reports
        eod_col = db["eod_reports"]
        eod_res = eod_col.delete_many({"date": {"$lt": limit_date}})
        logger.info(f"Deleted {eod_res.deleted_count} old records from eod_reports")
        
        # 4. Purge performance
        perf_col = db[config.MONGO_COLLECTION_PERFORMANCE]
        perf_res = perf_col.delete_many({"date": {"$lt": limit_date}})
        logger.info(f"Deleted {perf_res.deleted_count} old records from {config.MONGO_COLLECTION_PERFORMANCE}")
        
        logger.info("[OK] MongoDB cleanup complete!")
        return True
    except Exception as e:
        logger.error(f"Error during MongoDB cleanup: {e}", exc_info=True)
        return False


def delete_date_data(target_date: str, delete_picks: bool = False) -> bool:
    """Delete all records for a specific date (used to clean up test data)."""
    db = get_db()
    if db is not None:
        try:
            db[config.MONGO_COLLECTION_PRICES].delete_many({"date": target_date})
            db["eod_reports"].delete_many({"date": target_date})
            if delete_picks:
                db[config.MONGO_COLLECTION_PICKS].delete_many({"date": target_date})
            logger.info(f"[DB] Test data cleared for {target_date}")
        except Exception as e:
            logger.error(f"Error clearing test data from MongoDB: {e}")
            
    # JSON fallback
    for filename in ["price_history.json", "eod_reports_history.json"]:
        path = _json_path(filename)
        if os.path.exists(path):
            try:
                data = _read_json(filename)
                filtered = [r for r in data if r.get("date") != target_date]
                _write_json(filename, filtered)
            except Exception as e:
                logger.error(f"Error clearing JSON test data for {filename}: {e}")
                
    if delete_picks:
        filename = "daily_picks.json"
        path = _json_path(filename)
        if os.path.exists(path):
            try:
                data = _read_json(filename)
                filtered = [r for r in data if r.get("date") != target_date]
                _write_json(filename, filtered)
            except Exception as e:
                logger.error(f"Error clearing JSON test data for {filename}: {e}")
                
    return True

