"""
STALKER - Data Fetcher Module
Fetches OHLCV, fundamental, and market data from Yahoo Finance (yfinance).
All NSE symbols use .NS suffix. Handles errors gracefully.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import time
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
import config

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Global requests session to enable connection pooling & persistent cookies
_global_session = None

# Rate limiting protection state (cooldown of 30 minutes)
_rate_limit_cooldown_until = 0.0
COOLDOWN_DURATION_SEC = 30 * 60

def mark_rate_limited():
    """Mark that we are rate limited and start cooldown."""
    global _rate_limit_cooldown_until
    _rate_limit_cooldown_until = time.time() + COOLDOWN_DURATION_SEC
    logger.warning(f"Yahoo Finance rate limit hit! Pausing all yfinance network calls for {COOLDOWN_DURATION_SEC // 60} minutes.")

def is_rate_limited() -> bool:
    """Check if we are currently in rate-limiting cooldown."""
    global _rate_limit_cooldown_until
    if _rate_limit_cooldown_until > 0:
        remaining = _rate_limit_cooldown_until - time.time()
        if remaining > 0:
            return True
        else:
            # Cooldown expired
            _rate_limit_cooldown_until = 0.0
    return False

def get_browser_session():
    """Get or create a reusable requests session with browser headers, connection pooling, and automatic retries."""
    global _global_session
    if _global_session is None:
        _global_session = requests.Session()
        
        # Configure retries with small backoff on transient server errors only.
        # Fail fast on 429 to trigger immediate caching cooldown instead of blocking the thread.
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _global_session.mount("https://", adapter)
        _global_session.mount("http://", adapter)
        
        _global_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
    return _global_session



def is_nse_holiday(dt: date) -> bool:
    """
    Check if a given date is a weekend or an official NSE trading holiday in 2026.
    """
    # Weekend check
    if dt.weekday() in (5, 6):
        return True
        
    # Official NSE Holidays for 2026
    nse_holidays_2026 = {
        date(2026, 1, 26),   # Republic Day
        date(2026, 2, 16),   # Mahashivratri
        date(2026, 3, 4),    # Holi
        date(2026, 3, 20),   # Id-ul-Fitr (Ramzan Id)
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Id / Id-ul-Zuha
        date(2026, 6, 25),   # Moharram
        date(2026, 8, 15),   # Independence Day
        date(2026, 9, 25),   # Eid-e-Milad
        date(2026, 10, 2),   # Gandhi Jayanti
        date(2026, 10, 22),  # Dussehra
        date(2026, 11, 12),  # Diwali
        date(2026, 11, 23),  # Guru Nanak Jayanti
        date(2026, 12, 25),  # Christmas
    }
    
    return dt in nse_holidays_2026


# ─────────────────────────────────────────────
# OHLCV Data Fetcher
# ─────────────────────────────────────────────

def fetch_stock_history(symbol: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV data for a stock.
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    """
    if is_rate_limited():
        logger.debug(f"Skipping history fetch for {symbol} due to active rate-limit cooldown.")
        return None

    try:
        ticker = yf.Ticker(symbol, session=get_browser_session())
        df = ticker.history(period=period, interval=interval, auto_adjust=True)

        if df.empty or len(df) < 20:
            logger.warning(f"Insufficient data for {symbol}")
            return None

        df.index = pd.to_datetime(df.index)
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.dropna(inplace=True)

        # Ensure numeric types
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df.dropna(inplace=True)
        return df

    except Exception as e:
        err_msg = str(e)
        if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
            mark_rate_limited()
        logger.error(f"Error fetching history for {symbol}: {e}")
        return None


def fetch_multiple_stocks(symbols: List[str], period: str = "3mo") -> Dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple symbols in bulk using yf.download for high-speed parallel fetching.
    Chunked into groups of 15 to keep memory consumption low and prevent OOM crashes on Render.
    Returns dict: {symbol: DataFrame}
    """
    import gc
    results = {}
    total = len(symbols)
    
    if is_rate_limited():
        logger.warning("Skipping multiple stocks bulk fetch due to active rate-limit cooldown.")
        return {}

    logger.info(f"Fetching data for {total} symbols in chunked parallel batches to prevent memory leaks...")

    # Chunk size of 15 keeps memory overhead extremely low (under 120MB)
    chunk_size = 15
    chunks = [symbols[i:i + chunk_size] for i in range(0, total, chunk_size)]

    for chunk_idx, chunk in enumerate(chunks):
        if is_rate_limited():
            logger.warning("Aborting bulk fetch batch loop due to rate limit hit in previous batch.")
            break
        try:
            logger.info(f"  Downloading batch {chunk_idx + 1}/{len(chunks)} ({len(chunk)} symbols)...")
            data = yf.download(chunk, period=period, interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False, session=get_browser_session())

            chunk_total = len(chunk)
            for symbol in chunk:
                try:
                    if chunk_total == 1:
                        df = data
                    else:
                        if symbol not in data.columns.levels[0]:
                            continue
                        df = data[symbol].copy()

                    if df.empty or len(df) < 20:
                        continue

                    df.index = pd.to_datetime(df.index)
                    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                    df.dropna(inplace=True)

                    # Ensure numeric types
                    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                    df.dropna(inplace=True)
                    if len(df) >= 20:
                        results[symbol] = df

                except Exception as inner_e:
                    logger.debug(f"Error extracting bulk data for {symbol}: {inner_e}")
                    continue

            # Free memory immediately
            del data
            gc.collect()

            # Small breather between chunks to keep CPU/Network usage perfectly relaxed
            time.sleep(0.8)

        except Exception as e:
            err_msg = str(e)
            if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
                mark_rate_limited()
            logger.error(f"Bulk download batch {chunk_idx + 1} failed, falling back to serial: {e}")
            for symbol in chunk:
                if is_rate_limited():
                    break
                df = fetch_stock_history(symbol, period=period)
                if df is not None:
                    results[symbol] = df
                time.sleep(0.1)
                gc.collect()

    return results


def fetch_market_indices() -> Dict[str, Optional[pd.DataFrame]]:
    """
    Fetch data for NIFTY 50 and key sector indices.
    Used to determine overall market + sector strength.
    """
    indices = {
        "NIFTY50": config.NIFTY_INDEX,
        "BANKNIFTY": config.BANK_NIFTY,
        "NIFTY_IT": config.NIFTY_IT,
        "NIFTY_PHARMA": config.NIFTY_PHARMA,
        "NIFTY_AUTO": config.NIFTY_AUTO,
        "NIFTY_FMCG": config.NIFTY_FMCG,
        "NIFTY_ENERGY": config.NIFTY_ENERGY,
        "NIFTY_METAL": config.NIFTY_METAL,
    }

    result = {}
    for name, symbol in indices.items():
        df = fetch_stock_history(symbol, period="1mo")
        result[name] = df
        time.sleep(0.2)

    return result


# ─────────────────────────────────────────────
# Fundamental Data Fetcher
# ─────────────────────────────────────────────

# Global cache for fundamentals
_fundamentals_cache = None
FUNDAMENTALS_CACHE_FILE = os.path.join(config.DATA_DIR, "fundamentals_cache.json")

def fetch_fundamentals(symbol: str) -> Dict:
    """
    Fetch fundamental data with a local JSON cache layer to avoid slow yfinance network calls.
    """
    global _fundamentals_cache
    
    # Lazy load cache from file
    if _fundamentals_cache is None:
        if os.path.exists(FUNDAMENTALS_CACHE_FILE):
            try:
                with open(FUNDAMENTALS_CACHE_FILE, "r") as f:
                    _fundamentals_cache = json.load(f)
            except Exception:
                _fundamentals_cache = {}
        else:
            _fundamentals_cache = {}
            
    # Return from cache if available
    is_blocked = is_rate_limited()
    if symbol in _fundamentals_cache:
        # Check if the cache is older than 7 days
        cached_entry = _fundamentals_cache[symbol]
        cached_time = cached_entry.get("_cached_at")
        if cached_time:
            try:
                age = (datetime.now() - datetime.fromisoformat(cached_time)).days
                if age < 7 or is_blocked:
                    # Return cached metrics (accept any age if blocked to avoid network requests)
                    return cached_entry["data"]
            except Exception:
                if is_blocked:
                    return cached_entry["data"]

    defaults = {
        "symbol": symbol,
        "market_cap": 0,
        "pe_ratio": None,
        "debt_to_equity": None,
        "promoter_holding_pct": None,
        "roe": None,
        "sector": "Unknown",
        "industry": "Unknown",
        "current_price": None,
        "52w_high": None,
        "52w_low": None,
        "avg_volume": None,
        "beta": None,
        "book_value": None,
        "dividend_yield": None,
        "eps": None,
        "revenue_growth": None,
        "profit_growth": None,
        "fii_holding_pct": None,
        "has_recent_earnings": False,
        "earnings_surprise": None,
    }

    if is_blocked:
        logger.debug(f"Skipping fundamentals network fetch for {symbol} due to active rate-limit cooldown.")
        return defaults

    try:
        ticker = yf.Ticker(symbol, session=get_browser_session())
        info = ticker.info

        if not info:
            return defaults

        # Map yfinance fields to our structure
        fundamentals = {
            "symbol": symbol,
            "market_cap": info.get("marketCap", 0) or 0,
            "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
            "debt_to_equity": info.get("debtToEquity"),
            "promoter_holding_pct": None,   # Not directly in yfinance; requires NSE data
            "roe": info.get("returnOnEquity"),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "beta": info.get("beta"),
            "book_value": info.get("bookValue"),
            "dividend_yield": info.get("dividendYield"),
            "eps": info.get("trailingEps"),
            "revenue_growth": info.get("revenueGrowth"),
            "profit_growth": info.get("earningsGrowth"),
            "fii_holding_pct": None,
            "has_recent_earnings": False,
            "earnings_surprise": None,
        }

        # Convert debt_to_equity (yfinance gives it as %, divide by 100)
        if fundamentals["debt_to_equity"] is not None:
            fundamentals["debt_to_equity"] = fundamentals["debt_to_equity"] / 100

        # Skip slow yfinance earnings_dates call as it frequently hangs the morning scan
        fundamentals["has_recent_earnings"] = False
        fundamentals["earnings_surprise"] = None

        # Save to cache
        _fundamentals_cache[symbol] = {
            "_cached_at": datetime.now().isoformat(),
            "data": fundamentals
        }
        try:
            with open(FUNDAMENTALS_CACHE_FILE, "w") as f:
                json.dump(_fundamentals_cache, f, indent=2, default=str)
        except Exception as cache_save_err:
            logger.debug(f"Failed to save fundamentals cache: {cache_save_err}")

        return fundamentals

    except Exception as e:
        err_msg = str(e)
        if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
            mark_rate_limited()
        logger.error(f"Error fetching fundamentals for {symbol}: {e}")
        # Fallback to cache even if stale since network failed
        if symbol in _fundamentals_cache:
            return _fundamentals_cache[symbol]["data"]
        return defaults


# ─────────────────────────────────────────────
# Current Price & Quote
# ─────────────────────────────────────────────

def fetch_current_quote(symbol: str) -> Dict:
    """
    Get the latest quote (current/last price, open, high, low, volume).
    Used during market hours to capture open and close prices.
    """
    if is_rate_limited():
        logger.debug(f"Skipping quote fetch for {symbol} due to active rate-limit cooldown.")
        return {"symbol": symbol, "timestamp": datetime.now().isoformat(), "current_price": None}

    try:
        ticker = yf.Ticker(symbol, session=get_browser_session())
        info = ticker.info

        return {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "open": info.get("regularMarketOpen") or info.get("open"),
            "high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
            "low":  info.get("dayLow") or info.get("regularMarketDayLow"),
            "prev_close": info.get("previousClose"),
            "volume": info.get("regularMarketVolume") or info.get("volume"),
            "avg_volume": info.get("averageVolume"),
        }
    except Exception as e:
        err_msg = str(e)
        if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
            mark_rate_limited()
        logger.error(f"Error fetching quote for {symbol}: {e}")
        return {"symbol": symbol, "timestamp": datetime.now().isoformat(), "current_price": None}


def fetch_open_prices(symbols: List[str], allow_historical: bool = False) -> Dict[str, Dict]:
    """
    Capture opening prices for selected stocks at market open.
    Run this at 9:20 AM for stable open prices.
    Uses bulk download to avoid rate limits, with sequential fallback.
    """
    if not symbols:
        return {}

    # Strict holiday / closed day check
    if is_nse_holiday(date.today()) and not allow_historical:
        logger.warning(f"Today is a weekend or NSE trading holiday. Skipping open prices.")
        return {}

    if is_rate_limited():
        logger.warning("Skipping open prices fetch due to active rate-limit cooldown.")
        return {}

    prices = {}
    logger.info(f"Recording open prices for {len(symbols)} stocks using bulk download...")
    
    try:
        session = get_browser_session()
        # Fetch 1d data for all symbols
        df = yf.download(symbols, period="1d", group_by="ticker", threads=True, progress=False, session=session)
        
        if df.empty:
            logger.warning("Bulk download returned empty DataFrame.")
        else:
            latest_date = df.index[-1].date()
            if latest_date != date.today() and not allow_historical:
                logger.warning(f"Downloaded prices belong to {latest_date}, but today is {date.today()} (Market Closed). Skipping.")
                return {}

            is_single = (len(symbols) == 1)
            
            for symbol in symbols:
                try:
                    if is_single:
                        sym_df = df
                    else:
                        if symbol not in df.columns.levels[0]:
                            continue
                        sym_df = df[symbol]
                    
                    if sym_df.empty:
                        continue
                        
                    latest_row = sym_df.iloc[-1]
                    
                    open_val = latest_row.get("Open")
                    high_val = latest_row.get("High")
                    low_val = latest_row.get("Low")
                    close_val = latest_row.get("Close")
                    volume_val = latest_row.get("Volume")
                    
                    if pd.isna(open_val) or open_val is None:
                        continue
                        
                    prices[symbol] = {
                        "symbol": symbol,
                        "timestamp": datetime.now().isoformat(),
                        "current_price": float(close_val) if not pd.isna(close_val) else float(open_val),
                        "open": float(open_val),
                        "high": float(high_val) if not pd.isna(high_val) else float(open_val),
                        "low": float(low_val) if not pd.isna(low_val) else float(open_val),
                        "volume": float(volume_val) if not pd.isna(volume_val) else 0.0,
                    }
                    logger.info(f"Bulk open price for {symbol}: {prices[symbol]['open']}")
                except Exception as e:
                    logger.debug(f"Failed to extract bulk open price for {symbol}: {e}")
    except Exception as e:
        err_msg = str(e)
        if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
            mark_rate_limited()
        logger.error(f"Bulk open download failed: {e}")
        
    # Fill in any missing symbols using the traditional sequential fallback
    missing_symbols = [s for s in symbols if s not in prices]
    if missing_symbols:
        logger.info(f"Falling back to serial fetching for {len(missing_symbols)} missing stocks...")
        for symbol in missing_symbols:
            if is_rate_limited():
                break
            try:
                quote = fetch_current_quote(symbol)
                prices[symbol] = quote
                logger.info(f"Open price for {symbol}: {quote.get('open')}")
            except Exception as e:
                logger.error(f"Serial fetch failed for {symbol}: {e}")
            time.sleep(0.3)
            
    return prices


def fetch_close_prices(symbols: List[str], allow_historical: bool = False) -> Dict[str, Dict]:
    """
    Capture closing prices at end of trading day (3:30 PM).
    Run this at 3:35 PM.
    Uses bulk download to avoid rate limits, with sequential fallback.
    """
    if not symbols:
        return {}

    # Strict holiday / closed day check
    if is_nse_holiday(date.today()) and not allow_historical:
        logger.warning(f"Today is a weekend or NSE trading holiday. Skipping close prices.")
        return {}

    if is_rate_limited():
        logger.warning("Skipping close prices fetch due to active rate-limit cooldown.")
        return {}

    prices = {}
    logger.info(f"Recording close prices for {len(symbols)} stocks using bulk download...")
    
    try:
        session = get_browser_session()
        df = yf.download(symbols, period="1d", group_by="ticker", threads=True, progress=False, session=session)
        
        if df.empty:
            logger.warning("Bulk download returned empty DataFrame.")
        else:
            latest_date = df.index[-1].date()
            if latest_date != date.today() and not allow_historical:
                logger.warning(f"Downloaded prices belong to {latest_date}, but today is {date.today()} (Market Closed). Skipping.")
                return {}

            is_single = (len(symbols) == 1)
            
            for symbol in symbols:
                try:
                    if is_single:
                        sym_df = df
                    else:
                        if symbol not in df.columns.levels[0]:
                            continue
                        sym_df = df[symbol]
                    
                    if sym_df.empty:
                        continue
                        
                    latest_row = sym_df.iloc[-1]
                    
                    open_val = latest_row.get("Open")
                    high_val = latest_row.get("High")
                    low_val = latest_row.get("Low")
                    close_val = latest_row.get("Close")
                    volume_val = latest_row.get("Volume")
                    
                    if pd.isna(close_val) or close_val is None:
                        continue
                        
                    prices[symbol] = {
                        "symbol": symbol,
                        "timestamp": datetime.now().isoformat(),
                        "close": float(close_val),
                        "high": float(high_val) if not pd.isna(high_val) else float(close_val),
                        "low": float(low_val) if not pd.isna(low_val) else float(close_val),
                        "open": float(open_val) if not pd.isna(open_val) else float(close_val),
                        "volume": float(volume_val) if not pd.isna(volume_val) else 0.0,
                    }
                    logger.info(f"Bulk close price for {symbol}: {prices[symbol]['close']}")
                except Exception as e:
                    logger.debug(f"Failed to extract bulk close price for {symbol}: {e}")
    except Exception as e:
        err_msg = str(e)
        if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
            mark_rate_limited()
        logger.error(f"Bulk close download failed: {e}")
        
    # Fill in any missing symbols using the traditional sequential fallback
    missing_symbols = [s for s in symbols if s not in prices]
    if missing_symbols:
        logger.info(f"Falling back to serial fetching for {len(missing_symbols)} missing stocks...")
        for symbol in missing_symbols:
            if is_rate_limited():
                break
            try:
                quote = fetch_current_quote(symbol)
                if quote and quote.get("current_price"):
                    prices[symbol] = {
                        "symbol": symbol,
                        "timestamp": quote.get("timestamp"),
                        "close": float(quote.get("current_price")),
                        "high": float(quote.get("high") or quote.get("current_price")),
                        "low": float(quote.get("low") or quote.get("current_price")),
                        "open": float(quote.get("open") or quote.get("current_price")),
                        "volume": float(quote.get("volume") or 0),
                    }
                    logger.info(f"Open price for {symbol}: {prices[symbol]['close']}")
            except Exception as e:
                logger.error(f"Error fetching close for {symbol}: {e}")
            time.sleep(0.3)
            
    return prices


# ─────────────────────────────────────────────
# News & Catalyst Detection
# ─────────────────────────────────────────────

# Global cache for news signals
_news_cache = None
NEWS_CACHE_FILE = os.path.join(config.DATA_DIR, "news_cache.json")

def fetch_news_signals(symbol: str) -> Dict:
    """
    Fetch recent news for a stock and detect bullish/bearish catalysts.
    Uses a local JSON cache layer to avoid slow yfinance RSS feed network calls during scoring.
    """
    global _news_cache
    
    # Lazy load cache from file
    if _news_cache is None:
        if os.path.exists(NEWS_CACHE_FILE):
            try:
                with open(NEWS_CACHE_FILE, "r") as f:
                    _news_cache = json.load(f)
            except Exception:
                _news_cache = {}
        else:
            _news_cache = {}

    is_blocked = is_rate_limited()
    # Return from cache if available and fresh (less than 4 hours old) or if rate limited
    if symbol in _news_cache:
        cached_entry = _news_cache[symbol]
        cached_time = cached_entry.get("_cached_at")
        if cached_time:
            try:
                age_hours = (datetime.now() - datetime.fromisoformat(cached_time)).total_seconds() / 3600
                if age_hours < 4 or is_blocked:
                    return cached_entry["data"]
            except Exception:
                if is_blocked:
                    return cached_entry["data"]

    signals = {
        "symbol": symbol,
        "has_news": False,
        "news_sentiment": "neutral",  # bullish / bearish / neutral
        "news_count": 0,
        "headlines": [],
        "catalysts": [],
    }

    if is_blocked:
        logger.debug(f"Skipping news network fetch for {symbol} due to active rate-limit cooldown.")
        return signals

    try:
        ticker = yf.Ticker(symbol, session=get_browser_session())
        news = ticker.news

        if not news:
            # Save empty to cache to avoid repeatedly trying delisted stocks
            _news_cache[symbol] = {
                "_cached_at": datetime.now().isoformat(),
                "data": signals
            }
            try:
                with open(NEWS_CACHE_FILE, "w") as f:
                    json.dump(_news_cache, f, indent=2, default=str)
            except Exception:
                pass
            return signals

        signals["has_news"] = True
        signals["news_count"] = len(news)

        bullish_keywords = [
            "profit", "growth", "beat", "upgrade", "buy", "outperform",
            "strong", "expansion", "order", "contract", "acquisition",
            "quarterly results", "record", "high", "dividend", "bonus"
        ]
        bearish_keywords = [
            "loss", "decline", "downgrade", "sell", "underperform",
            "weak", "fraud", "penalty", "fine", "investigation", "drop",
            "miss", "debt", "default", "crisis", "layoff"
        ]

        bullish_count = 0
        bearish_count = 0
        headlines = []

        for article in news[:10]:  # Check last 10 articles
            title = article.get("title", "").lower()
            headlines.append(article.get("title", ""))

            b_hits = sum(1 for kw in bullish_keywords if kw in title)
            d_hits = sum(1 for kw in bearish_keywords if kw in title)
            bullish_count += b_hits
            bearish_count += d_hits

            # Detect specific catalysts
            if any(kw in title for kw in ["earnings", "quarterly", "q1", "q2", "q3", "q4"]):
                signals["catalysts"].append("Earnings Report")
            if any(kw in title for kw in ["order", "contract", "deal"]):
                signals["catalysts"].append("New Order/Contract")
            if any(kw in title for kw in ["acquisition", "merger", "takeover"]):
                signals["catalysts"].append("M&A Activity")
            if any(kw in title for kw in ["fii", "institutional", "bought"]):
                signals["catalysts"].append("Institutional Buying")

        signals["headlines"] = headlines[:5]
        signals["catalysts"] = list(set(signals["catalysts"]))  # dedup

        # Determine overall sentiment
        if bullish_count > bearish_count + 2:
            signals["news_sentiment"] = "bullish"
        elif bearish_count > bullish_count + 2:
            signals["news_sentiment"] = "bearish"
        else:
            signals["news_sentiment"] = "neutral"

        # Save to cache
        _news_cache[symbol] = {
            "_cached_at": datetime.now().isoformat(),
            "data": signals
        }
        try:
            with open(NEWS_CACHE_FILE, "w") as f:
                json.dump(_news_cache, f, indent=2, default=str)
        except Exception:
            pass

    except Exception as e:
        err_msg = str(e)
        if "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower() or "429" in err_msg or "ratelimit" in type(e).__name__.lower():
            mark_rate_limited()
        logger.error(f"Error fetching news for {symbol}: {e}")
        if symbol in _news_cache:
            return _news_cache[symbol]["data"]

    return signals


# ─────────────────────────────────────────────
# Market Strength Check
# ─────────────────────────────────────────────

def get_market_trend(index_data: pd.DataFrame) -> str:
    """
    Determine if market is bullish, bearish, or sideways.
    Uses last 20 days of index data.
    """
    if index_data is None or len(index_data) < 10:
        return "unknown"

    close = index_data["Close"].values
    sma10 = close[-10:].mean()
    sma20 = close[-20:].mean() if len(close) >= 20 else close.mean()
    latest = close[-1]

    # Price above SMA10 and SMA10 above SMA20 = bullish
    if latest > sma10 and sma10 > sma20:
        return "bullish"
    elif latest < sma10 and sma10 < sma20:
        return "bearish"
    else:
        return "sideways"


def get_sector_for_symbol(symbol: str, fundamentals: Dict) -> str:
    """Return the sector label for a stock (from fundamentals or manual map)."""
    sector = fundamentals.get("sector", "Unknown")

    # Manual overrides for common NSE stocks
    sector_map = {
        "HDFCBANK.NS": "Banking", "ICICIBANK.NS": "Banking", "SBIN.NS": "Banking",
        "AXISBANK.NS": "Banking", "KOTAKBANK.NS": "Banking", "INDUSINDBK.NS": "Banking",
        "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT", "HCLTECH.NS": "IT",
        "TECHM.NS": "IT", "MPHASIS.NS": "IT", "LTTS.NS": "IT",
        "RELIANCE.NS": "Oil & Gas", "ONGC.NS": "Oil & Gas", "BPCL.NS": "Oil & Gas",
        "HPCL.NS": "Oil & Gas", "IOC.NS": "Oil & Gas",
        "SUNPHARMA.NS": "Pharma", "DRREDDY.NS": "Pharma", "CIPLA.NS": "Pharma",
        "DIVISLAB.NS": "Pharma", "LUPIN.NS": "Pharma", "AUROPHARMA.NS": "Pharma",
        "MARUTI.NS": "Auto", "TATAMOTORS.NS": "Auto", "M&M.NS": "Auto",
        "HEROMOTOCO.NS": "Auto", "BAJAJ-AUTO.NS": "Auto", "EICHERMOT.NS": "Auto",
        "HINDUNILVR.NS": "FMCG", "NESTLEIND.NS": "FMCG", "BRITANNIA.NS": "FMCG",
        "MARICO.NS": "FMCG", "DABUR.NS": "FMCG", "COLPAL.NS": "FMCG",
        "TATASTEEL.NS": "Metal", "JSWSTEEL.NS": "Metal", "HINDALCO.NS": "Metal",
        "VEDL.NS": "Metal", "SAIL.NS": "Metal",
        "LT.NS": "Infrastructure", "ADANIPORTS.NS": "Infrastructure",
        "NTPC.NS": "Energy", "POWERGRID.NS": "Energy", "COALINDIA.NS": "Energy",
        "NHPC.NS": "Energy", "ADANIENT.NS": "Energy",
        "BAJFINANCE.NS": "Finance", "BAJAJFINSV.NS": "Finance", "SHRIRAMFIN.NS": "Finance",
        "HDFCLIFE.NS": "Insurance", "SBILIFE.NS": "Insurance",
        "TITAN.NS": "Consumer", "TRENT.NS": "Consumer", "TATACONSUM.NS": "Consumer",
        "IRCTC.NS": "Travel", "ZOMATO.NS": "Food Tech",
        "BHARTIARTL.NS": "Telecom",
        "HAL.NS": "Defence", "BEL.NS": "Defence", "BHEL.NS": "Engineering",
    }

    return sector_map.get(symbol, sector if sector != "Unknown" else "Diversified")


# ─────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if "--test" in sys.argv:
        print("\n🔍 Testing Data Fetcher...")

        # Test single stock
        symbol = "RELIANCE.NS"
        print(f"\n📊 Fetching history for {symbol}...")
        df = fetch_stock_history(symbol)
        if df is not None:
            print(f"   ✅ Got {len(df)} rows. Latest close: ₹{df['Close'].iloc[-1]:.2f}")
        else:
            print("   ❌ Failed")

        # Test fundamentals
        print(f"\n🏢 Fetching fundamentals for {symbol}...")
        f = fetch_fundamentals(symbol)
        print(f"   Sector: {f['sector']}")
        print(f"   Market Cap: ₹{f['market_cap']:,.0f}")
        print(f"   P/E: {f['pe_ratio']}")
        print(f"   Debt/Equity: {f['debt_to_equity']}")

        # Test news
        print(f"\n📰 Fetching news signals for {symbol}...")
        news = fetch_news_signals(symbol)
        print(f"   Sentiment: {news['news_sentiment']}")
        print(f"   Headlines: {len(news['headlines'])}")

        # Test market indices
        print(f"\n📈 Fetching NIFTY 50 data...")
        indices = fetch_market_indices()
        for name, df in indices.items():
            if df is not None:
                trend = get_market_trend(df)
                print(f"   {name}: {trend} (latest: {df['Close'].iloc[-1]:.2f})")

        print("\n✅ Data Fetcher test complete!")
