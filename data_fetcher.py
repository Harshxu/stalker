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

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# OHLCV Data Fetcher
# ─────────────────────────────────────────────

def fetch_stock_history(symbol: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    Fetch historical OHLCV data for a stock.
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    """
    try:
        ticker = yf.Ticker(symbol)
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
        logger.error(f"Error fetching history for {symbol}: {e}")
        return None


def fetch_multiple_stocks(symbols: List[str], period: str = "3mo") -> Dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple symbols with rate-limiting.
    Returns dict: {symbol: DataFrame}
    """
    results = {}
    total = len(symbols)

    logger.info(f"Fetching data for {total} symbols...")

    for i, symbol in enumerate(symbols, 1):
        print(f"\r  Fetching {i}/{total}: {symbol:<20}", end="", flush=True)

        df = fetch_stock_history(symbol, period=period)
        if df is not None:
            results[symbol] = df

        # Rate limiting — avoid Yahoo Finance blocks
        time.sleep(0.3)

    print()  # newline after progress
    logger.info(f"Successfully fetched {len(results)}/{total} symbols")
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

def fetch_fundamentals(symbol: str) -> Dict:
    """
    Fetch fundamental data: P/E, debt, promoter holding, market cap, sector.
    Returns dict with fundamental metrics.
    """
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

    try:
        ticker = yf.Ticker(symbol)
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

        # Check recent earnings (within last 30 days)
        try:
            earnings = ticker.earnings_dates
            if earnings is not None and not earnings.empty:
                recent = earnings[earnings.index <= pd.Timestamp.now()]
                if not recent.empty:
                    latest = recent.index[0]
                    days_ago = (pd.Timestamp.now() - latest).days
                    if days_ago <= 30:
                        fundamentals["has_recent_earnings"] = True
                        surprise = recent.iloc[0].get("Surprise(%)")
                        fundamentals["earnings_surprise"] = surprise
        except Exception:
            pass

        return fundamentals

    except Exception as e:
        logger.error(f"Error fetching fundamentals for {symbol}: {e}")
        return defaults


# ─────────────────────────────────────────────
# Current Price & Quote
# ─────────────────────────────────────────────

def fetch_current_quote(symbol: str) -> Dict:
    """
    Get the latest quote (current/last price, open, high, low, volume).
    Used during market hours to capture open and close prices.
    """
    try:
        ticker = yf.Ticker(symbol)
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
        logger.error(f"Error fetching quote for {symbol}: {e}")
        return {"symbol": symbol, "timestamp": datetime.now().isoformat(), "current_price": None}


def fetch_open_prices(symbols: List[str]) -> Dict[str, Dict]:
    """
    Capture opening prices for selected stocks at market open.
    Run this at 9:20 AM for stable open prices.
    """
    prices = {}
    for symbol in symbols:
        quote = fetch_current_quote(symbol)
        prices[symbol] = quote
        time.sleep(0.3)
        logger.info(f"Open price for {symbol}: {quote.get('open')}")
    return prices


def fetch_close_prices(symbols: List[str]) -> Dict[str, Dict]:
    """
    Capture closing prices at end of trading day (3:30 PM).
    Run this at 3:35 PM.
    """
    prices = {}
    for symbol in symbols:
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
        except Exception as e:
            logger.error(f"Error fetching close for {symbol}: {e}")
        time.sleep(0.3)

    return prices


# ─────────────────────────────────────────────
# News & Catalyst Detection
# ─────────────────────────────────────────────

def fetch_news_signals(symbol: str) -> Dict:
    """
    Fetch recent news for a stock and detect bullish/bearish catalysts.
    Returns a signal dict with sentiment and key events.
    """
    signals = {
        "symbol": symbol,
        "has_news": False,
        "news_sentiment": "neutral",  # bullish / bearish / neutral
        "news_count": 0,
        "headlines": [],
        "catalysts": [],
    }

    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news

        if not news:
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

    except Exception as e:
        logger.error(f"Error fetching news for {symbol}: {e}")

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
