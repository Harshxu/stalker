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


def update_past_picks_returns():
    """
    Updates the multi-day forward returns (3d, 5d, 10d, 20d) for all picks in the last 30 days.
    Calculates returns from the entry price (Open price on execution day) to the Close price on the Nth trading day.
    """
    import pandas as pd
    from datetime import datetime, date, timedelta
    import data_fetcher
    import yfinance as yf
    
    logger.info("[TRACKER] Starting past picks return tracking...")
    today_str = str(date.today())
    
    db = get_db()
    records = []
    
    # 1. Load records from MongoDB or JSON
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            # Get picks from the last 35 days to allow for 20 trading days to pass
            min_date = str(date.today() - timedelta(days=35))
            records = list(col.find({"date": {"$gte": min_date}}))
        except Exception as e:
            logger.error(f"Failed to load picks from MongoDB for tracking: {e}")
            records = []
    
    # JSON fallback
    if not records or db is None:
        records = _read_json("daily_picks.json")
        min_date = str(date.today() - timedelta(days=35))
        records = [r for r in records if r.get("date", "") >= min_date]
        
    if not records:
        logger.info("[TRACKER] No recent picks found to track.")
        return
        
    updated_records = []
    
    for record in records:
        rec_date_str = record.get("date")
        picks = record.get("picks", record.get("top_picks", []))
        if not picks:
            continue
            
        try:
            rec_date = datetime.strptime(rec_date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        
        # Load open prices for this date to get the true entry price
        prices_for_day = get_prices_for_date(rec_date_str)
        open_prices = prices_for_day.get("open", {})
        
        record_changed = False
        
        for pick in picks:
            symbol = pick.get("symbol")
            if not symbol:
                continue
                
            needs_update = (
                pick.get("future_3d_return") is None or
                pick.get("future_5d_return") is None or
                pick.get("future_10d_return") is None or
                pick.get("future_20d_return") is None
            )
            
            if not needs_update:
                continue
                
            # Get entry price (Open price on date T)
            open_q = open_prices.get(symbol, {})
            entry_price = open_q.get("open") or open_q.get("current_price") or pick.get("current_price")
            if not entry_price or entry_price <= 0:
                continue
                
            # Fetch history for this symbol from T to today (approx 2 months to cover the period)
            df_hist = data_fetcher.fetch_stock_history(symbol, period="6mo")
            if df_hist is None or df_hist.empty:
                continue
                
            # Filter history from T onwards
            df_after = df_hist[df_hist.index.date >= rec_date]
            if len(df_after) < 2:
                continue
                
            # Row 0 of df_after is date T (the execution day).
            # The close on day T + N is df_after['Close'].iloc[N] if len(df_after) > N.
            lookbacks = {
                3: "future_3d_return",
                5: "future_5d_return",
                10: "future_10d_return",
                20: "future_20d_return"
            }
            
            for offset, key in lookbacks.items():
                if pick.get(key) is None:
                    if len(df_after) > offset:
                        close_at_offset = float(df_after['Close'].iloc[offset])
                        ret = ((close_at_offset - entry_price) / entry_price) * 100.0
                        pick[key] = round(ret, 2)
                        record_changed = True
                        logger.info(f"[TRACKER] Updated {symbol} {key} for {rec_date_str}: {ret:+.2f}%")
                        
        if record_changed:
            updated_records.append(record)
            
    # Save updated records back
    if updated_records:
        if db is not None:
            try:
                col = db[config.MONGO_COLLECTION_PICKS]
                for rec in updated_records:
                    # Remove BSON ObjectId before update to prevent errors
                    rec_copy = dict(rec)
                    rec_copy.pop("_id", None)
                    date_val = rec_copy.get("date")
                    col.replace_one({"date": date_val}, rec_copy, upsert=True)
                logger.info(f"[TRACKER] Saved {len(updated_records)} updated records to MongoDB.")
            except Exception as e:
                logger.error(f"Failed to save tracked returns to MongoDB: {e}")
                
        # Also write back to JSON
        try:
            all_picks = _read_json("daily_picks.json")
            for rec in updated_records:
                rec_copy = dict(rec)
                rec_copy.pop("_id", None)
                date_val = rec_copy.get("date")
                all_picks = [p for p in all_picks if p.get("date") != date_val]
                all_picks.append(rec_copy)
            all_picks = all_picks[-90:] # Keep last 90 days
            _write_json("daily_picks.json", all_picks)
            logger.info(f"[TRACKER] Saved updated picks back to daily_picks.json fallback.")
        except Exception as e:
            logger.error(f"Failed to save tracked returns to JSON: {e}")


def get_setup_expectancy(setup_name: str, market_regime: str = "Bull", sector: str = "Unknown", score: float = 75.0,
                         volatility_regime: str = "normal", breadth_regime: str = "normal",
                         liquidity_bucket: str = "medium", setup_subtype: str = "standard") -> dict:
    """
    Returns historical expectancy statistics for a setup type, grouped by regime and parameters.
    Applies hierarchical fallback if sample sizes are small, and adjusts expectancy by sample confidence.
    """
    import numpy as np
    from datetime import date, timedelta
    
    # Defaults
    defaults = {
        "PULLBACK": {"win_rate": 62.0, "avg_return": 3.1, "expectancy": 1.92, "std_dev": 1.5, "conf_width": 0.0, "source": "baseline"},
        "BREAKOUT": {"win_rate": 48.0, "avg_return": 4.2, "expectancy": 2.02, "std_dev": 2.5, "conf_width": 0.0, "source": "baseline"},
        "MOMENTUM": {"win_rate": 52.0, "avg_return": 2.8, "expectancy": 1.46, "std_dev": 1.8, "conf_width": 0.0, "source": "baseline"},
        "VALUE_MOMENTUM": {"win_rate": 58.0, "avg_return": 3.5, "expectancy": 2.03, "std_dev": 2.0, "conf_width": 0.0, "source": "baseline"},
        "EARNINGS_RUNNER": {"win_rate": 55.0, "avg_return": 4.0, "expectancy": 2.20, "std_dev": 2.2, "conf_width": 0.0, "source": "baseline"},
        "WATCHLIST_ONLY": {"win_rate": 50.0, "avg_return": 0.0, "expectancy": 0.00, "std_dev": 1.0, "conf_width": 0.0, "source": "baseline"}
    }
    
    # Determine score bucket
    if score < 70.0:
        score_bucket = "below_70"
    elif score < 80.0:
        score_bucket = "70-80"
    elif score < 90.0:
        score_bucket = "80-90"
    else:
        score_bucket = "90+"

    # Load recent picks
    db = get_db()
    records = []
    min_date = str(date.today() - timedelta(days=90))
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            records = list(col.find({"date": {"$gte": min_date}}))
        except Exception:
            records = []
            
    if not records or db is None:
        records = _read_json("daily_picks.json")
        records = [r for r in records if r.get("date", "") >= min_date]
        
    flat_picks = []
    # Round-trip transaction cost assumption (brokerage + slippage at entry & exit)
    slippage_pct = getattr(config, "SLIPPAGE_PCT", 0.0010)
    brokerage_pct = getattr(config, "BROKERAGE_PCT", 0.0005)
    txn_cost_pct = 2.0 * (slippage_pct + brokerage_pct) * 100.0  # converted to %
    
    for r in records:
        picks_list = r.get("picks", r.get("top_picks", []))
        parent_regime = r.get("market_trend", "neutral").capitalize()
        for p in picks_list:
            raw_ret = p.get("future_5d_return") if p.get("future_5d_return") is not None else p.get("future_3d_return")
            if raw_ret is not None:
                net_ret = float(raw_ret) - txn_cost_pct
                p_score = p.get("total_score") or p.get("alpha_score") or 75.0
                if p_score < 70.0:
                    p_bucket = "below_70"
                elif p_score < 80.0:
                    p_bucket = "70-80"
                elif p_score < 90.0:
                    p_bucket = "80-90"
                else:
                    p_bucket = "90+"
                
                flat_picks.append({
                    "trade_type": p.get("trade_type"),
                    "market_regime": parent_regime,
                    "sector": p.get("sector"),
                    "score_bucket": p_bucket,
                    "volatility_regime": p.get("volatility_regime", "normal"),
                    "breadth_regime": p.get("breadth_regime", "normal"),
                    "liquidity_bucket": p.get("liquidity_bucket", "medium"),
                    "setup_subtype": p.get("setup_subtype", "standard"),
                    "net_return": net_ret
                })
                
    # Hierarchical filtering fallback
    matches = []
    source_level = "baseline"
    
    if flat_picks:
        # Level 1: Full Exact Match
        matches = [p for p in flat_picks if 
                   p["trade_type"] == setup_name and 
                   p["market_regime"] == market_regime.capitalize() and 
                   p["sector"] == sector and 
                   p["score_bucket"] == score_bucket and
                   p["volatility_regime"] == volatility_regime and
                   p["breadth_regime"] == breadth_regime and
                   p["liquidity_bucket"] == liquidity_bucket and
                   p["setup_subtype"] == setup_subtype]
        source_level = "exact"
        
        # Level 2: Setup + Regimes + Subtype Match
        if len(matches) < 5:
            matches = [p for p in flat_picks if 
                       p["trade_type"] == setup_name and 
                       p["market_regime"] == market_regime.capitalize() and
                       p["volatility_regime"] == volatility_regime and
                       p["breadth_regime"] == breadth_regime and
                       p["setup_subtype"] == setup_subtype]
            source_level = "setup_regimes_subtype"
            
        # Level 3: Setup + Regime + Volatility Match
        if len(matches) < 5:
            matches = [p for p in flat_picks if 
                       p["trade_type"] == setup_name and 
                       p["market_regime"] == market_regime.capitalize() and
                       p["volatility_regime"] == volatility_regime]
            source_level = "setup_regime_vol"
            
        # Level 4: Setup + Regime Match
        if len(matches) < 5:
            matches = [p for p in flat_picks if 
                       p["trade_type"] == setup_name and 
                       p["market_regime"] == market_regime.capitalize()]
            source_level = "setup_regime"
            
        # Level 5: Setup only Match
        if len(matches) < 5:
            matches = [p for p in flat_picks if p["trade_type"] == setup_name]
            source_level = "setup"

    # Compute live stats if minimum sample size met
    if len(matches) >= 5:
        returns = [m["net_return"] for m in matches]
        wins = sum(1 for r in returns if r > 0)
        win_rate = (wins / len(returns)) * 100.0
        avg_return = sum(returns) / len(returns)
        std_dev = float(np.std(returns)) if len(returns) > 1 else 0.0
        
        # 95% Confidence Interval (Margin of Error)
        conf_width = 1.96 * (std_dev / np.sqrt(len(returns)))
        
        raw_expectancy = (win_rate / 100.0) * avg_return
        # Down-weight expectancy for low sample sizes (n < 30)
        confidence_factor = min(1.0, len(returns) / 30.0)
        expectancy = raw_expectancy * confidence_factor
        
        return {
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_return, 2),
            "expectancy": round(expectancy, 2),
            "std_dev": round(std_dev, 2),
            "conf_width": round(conf_width, 2),
            "sample_size": len(returns),
            "source": f"live_{source_level}"
        }
        
    # Default fallback
    res = dict(defaults.get(setup_name, defaults["MOMENTUM"]))
    res["sample_size"] = len(matches)
    res["source"] = "baseline"
    return res

