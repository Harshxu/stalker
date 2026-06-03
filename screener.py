# -*- coding: utf-8 -*-
"""
STALKER - Stock Market Screener & Alpha Engine v3.0 (Production Quant)
Scans the stock universe, executes strict safety gates, runs 7 weighted alpha scoring models,
and constructs a risk-diversified top conviction portfolio today.
"""

import sys
import io
import os
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

import config
import data_fetcher as df_module
import indicators as ind
import market_structure as ms_module
import fundamentals as fund_module
import risk_manager as rm
import db_manager

# Force UTF-8 output on Windows to handle special chars
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# STAGE 1 — HARD FILTERS (SAFETY LAYER)
# ═══════════════════════════════════════════════

def calculate_data_quality(symbol: str, fund: Dict, df_hist) -> Tuple[float, List[str]]:
    """
    Evaluates data freshness and missing critical records.
    Returns (data_quality_score, list_of_missing_fields)
    """
    score = 100.0
    missing = []
    
    # Check critical fundamental data
    critical_fund_fields = {
        "revenue_growth": "Revenue Growth",
        "roe": "ROE",
        "debt_to_equity": "Debt Metrics"
    }
    
    for field, label in critical_fund_fields.items():
        val = fund.get(field)
        if val is None or (isinstance(val, (int, float)) and np.isnan(val)):
            score -= 10.0
            missing.append(label)
            
    # Check price volume data
    if df_hist is None or len(df_hist) < 20:
        score -= 20.0
        missing.append("Price/Volume Data")
        
    # Check cache age / data freshness
    cached_time = fund.get("_cached_at")
    if cached_time:
        try:
            age = (datetime.now() - datetime.fromisoformat(cached_time)).days
            if age > 7:
                score -= 10.0
        except Exception:
            pass
            
    return max(0.0, score), missing


def evaluate_liquidity(df_hist) -> Tuple[bool, float, float]:
    """
    Checks if average volume and daily traded value meet institutional limits.
    Returns (is_liquid, avg_volume, avg_traded_value)
    """
    try:
        closes = df_hist['Close'].tail(20)
        volumes = df_hist['Volume'].tail(20)
        
        avg_vol = float(volumes.mean())
        latest_close = float(closes.iloc[-1])
        avg_turnover = avg_vol * latest_close
        
        # Hard Stage 1 Liquidity check: avg_turnover >= config.MIN_DAILY_TURNOVER (₹10 Crore/day)
        is_liquid = avg_turnover >= getattr(config, "MIN_DAILY_TURNOVER", 100000000)
        return is_liquid, avg_vol, avg_turnover
    except Exception:
        return False, 0.0, 0.0


def check_overnight_gap_risk(df_hist) -> bool:
    """Checks if the stock has excessive overnight gap-up/down frequency (>3% on >5 of past 20 days)"""
    try:
        if len(df_hist) < 21:
            return False
        closes = df_hist['Close'].tail(21)
        opens = df_hist['Open'].tail(21)
        prev_closes = closes.shift(1).dropna()
        today_opens = opens.iloc[1:]
        gaps = ((today_opens - prev_closes) / prev_closes).abs() * 100.0
        excessive_days = sum(1 for g in gaps if g > 3.0)
        return excessive_days > 5
    except Exception:
        return False


def check_atr_spike_risk(df_hist, indic: Dict) -> bool:
    """Checks if the stock has abnormal ATR volatility spikes (latest ATR > 2x 20d average ATR)"""
    try:
        closes = df_hist['Close']
        highs = df_hist['High']
        lows = df_hist['Low']
        
        # Recompute ATR series to get the historical trend
        tr1 = highs - lows
        tr2 = (highs - closes.shift(1)).abs()
        tr3 = (lows - closes.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = tr.ewm(span=14, adjust=False).mean()
        
        latest_atr = float(atr_series.iloc[-1])
        avg_atr = float(atr_series.tail(20).mean())
        return latest_atr > 2.0 * avg_atr
    except Exception:
        return False


def check_circuit_lock(df_hist) -> bool:
    """Checks if stock is locked in circuit limit (range = 0 on low volume)"""
    try:
        latest = df_hist.iloc[-1]
        high = float(latest["High"])
        low = float(latest["Low"])
        volume = float(latest["Volume"])
        
        if high == low:
            return True
            
        avg_volume_20 = df_hist["Volume"].tail(20).mean()
        if volume < 5000 and volume < avg_volume_20 * 0.05:
            return True
            
        return False
    except Exception:
        return False


def evaluate_risk_profile(df_hist, indic: Dict) -> Tuple[float, float, float]:
    """
    Calculates ATR/Price volatility, Maximum Drawdown over last 60 sessions.
    Returns (risk_score_0_to_10, max_drawdown, atr_pct)
    """
    try:
        close = float(df_hist['Close'].iloc[-1])
        atr = float(indic.get("atr", close * 0.01))
        atr_pct = atr / close
        
        # Calculate 60-day historical drawdown
        closes = df_hist['Close'].tail(60)
        roll_max = closes.cummax()
        drawdowns = (closes - roll_max) / roll_max
        max_dd = float(abs(drawdowns.min()))
        
        # Construct risk score (0-10)
        risk_score = (atr_pct * 150) + (max_dd * 15)
        risk_score = min(10.0, max(0.0, risk_score))
        
        return risk_score, max_dd, atr_pct
    except Exception:
        return 10.0, 1.0, 0.1


# ═══════════════════════════════════════════════
# STAGE 2 — MARKET CONTEXT ENGINE
# ═══════════════════════════════════════════════

def calculate_nifty_realized_volatility(nifty_df: pd.DataFrame) -> float:
    """Calculates Nifty 20-day annualized realized volatility percentage"""
    try:
        if nifty_df is None or len(nifty_df) < 21:
            return 15.0
        returns = nifty_df['Close'].pct_change().tail(20)
        daily_std = returns.std()
        realized_vol = daily_std * np.sqrt(252) * 100.0
        return float(realized_vol)
    except Exception:
        return 15.0


def get_market_regime_state(nifty_df, market_breadth: float, prev_breadth: float = 0.5) -> Tuple[str, bool]:
    """
    Determines Nifty market regime context with 5 states: Bull, Improving, Neutral, Deteriorating, Bear.
    Returns (regime_name, market_is_bullish)
    """
    if nifty_df is None or len(nifty_df) < 50:
        return "Neutral", False
        
    try:
        close = float(nifty_df['Close'].iloc[-1])
        ema50 = float(nifty_df['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
        ma200 = float(nifty_df['Close'].rolling(window=200).mean().iloc[-1]) if len(nifty_df) >= 200 else ema50
        
        breadth_rising = market_breadth > prev_breadth
        nifty_ema20 = nifty_df['Close'].ewm(span=20, adjust=False).mean()
        ema20_slope_up = float(nifty_ema20.iloc[-1]) > float(nifty_ema20.iloc[-3])
        
        is_bull_trend = close > ema50 and close > ma200
        is_bear_trend = close < ma200
        
        if is_bull_trend:
            if market_breadth < 0.40 or (not breadth_rising and not ema20_slope_up):
                return "Deteriorating", False
            return "Bull", True
            
        elif is_bear_trend:
            if market_breadth > 0.40 and breadth_rising and ema20_slope_up:
                return "Improving", False
            return "Bear", False
            
        else: # Sideways / Neutral
            if breadth_rising and ema20_slope_up:
                return "Improving", False
            elif not breadth_rising and not ema20_slope_up:
                return "Deteriorating", False
            else:
                return "Neutral", False
    except Exception:
        return "Neutral", False


def get_market_regime(nifty_df, market_breadth: float = 0.5, prev_breadth: float = 0.5) -> str:
    """
    Determines market trend context.
    Returns "Bull", "Improving", "Neutral", "Deteriorating", or "Bear"
    """
    regime, _ = get_market_regime_state(nifty_df, market_breadth, prev_breadth)
    return regime


# ═══════════════════════════════════════════════
# STAGE 3 — ALPHA RANKING ENGINE
# ═══════════════════════════════════════════════

def calculate_rs_score(symbol: str, indic: Dict, rs_ranks: Dict[str, float]) -> float:
    """Returns Relative Strength Rank percentile (0-100)"""
    return rs_ranks.get(symbol, 50.0)


def calculate_institutional_score(volume_rank: float, cmf_rank: float) -> float:
    """
    Scores institutional activity using percentile ranks of CMF and volume ratio.
    Returns a calibrated 0-100 score.
    """
    return float(0.50 * cmf_rank + 0.50 * volume_rank)


def calculate_sector_score(sector: str, sector_trends: Dict) -> float:
    """Returns Sector Momentum score (100 for bullish, 70 sideways, 20 bearish)"""
    # Normalize sector names to match index trends
    norm_sector = sector
    sector_lower = sector.lower()
    if "tech" in sector_lower or "software" in sector_lower or "it" == sector_lower:
        norm_sector = "IT"
    elif "bank" in sector_lower or "finance" in sector_lower or "financial" in sector_lower:
        norm_sector = "Banking"
    elif "pharma" in sector_lower or "health" in sector_lower:
        norm_sector = "Pharma"
    elif "auto" in sector_lower or "car" in sector_lower:
        norm_sector = "Auto"
    elif "fmcg" in sector_lower or "consumer defensive" in sector_lower or "food" in sector_lower:
        norm_sector = "FMCG"
    elif "energy" in sector_lower or "power" in sector_lower or "utilities" in sector_lower or "oil" in sector_lower or "gas" in sector_lower:
        norm_sector = "Energy"
    elif "metal" in sector_lower or "basic materials" in sector_lower or "steel" in sector_lower or "mining" in sector_lower:
        norm_sector = "Metal"

    trend = sector_trends.get(norm_sector, "unknown")
    if trend == "bullish":
        return 100.0
    elif trend == "sideways":
        return 70.0
    elif trend == "bearish":
        return 20.0
    return 50.0


def calculate_fundamental_score(fund: Dict, roe_rank: float = 50.0, profit_growth_rank: float = 50.0,
                                revenue_growth_rank: float = 50.0, sales_growth_rank: float = 50.0,
                                earnings_revision_rank: float = 50.0, margin_expansion_rank: float = 50.0,
                                roe_trend_rank: float = 50.0) -> float:
    """Scores fundamentals using continuous percentile ranks and regression-based growth trends."""
    res = fund_module.score_fundamentals(fund, roe_rank, profit_growth_rank, revenue_growth_rank,
                                         sales_growth_rank, earnings_revision_rank, margin_expansion_rank, roe_trend_rank)
    return float(res.get("score", 5.0) * 10.0) # scaled to 0-100


def calculate_technical_score(indic: Dict) -> float:
    """
    Scores RSI (using continuous Gaussian curve centered at 60), EMA alignment,
    MACD slope/histogram, and Bollinger squeeze. Returns 0-100 score.
    """
    score = 0.0
    rsi = float(indic.get("rsi", 50.0))
    
    # 1. Continuous RSI Gaussian curve centered at 60 (max 30 points)
    rsi_score = 30.0 * np.exp( - (rsi - 60.0)**2 / (2 * 12.0**2) )
    score += rsi_score
        
    # 2. EMA Alignment 20 > 50 (max 25 pts)
    if indic.get("ema_aligned"):
        score += 25.0
        
    # 3. Price vs VWAP & EMA slope (max 20 pts)
    if indic.get("above_vwap"):
        score += 10.0
    if indic.get("ema_slope_up"):
        score += 10.0
    
    # 4. MACD Confirmation (max 15 pts)
    if indic.get("macd_bullish"):
        score += 15.0
    elif float(indic.get("macd_hist", 0)) < 0:
        score -= 5.0
    
    # 5. Bollinger Squeeze bonus (max 10 pts)
    if indic.get("bb_squeeze"):
        score += 10.0
        
    return max(0.0, min(100.0, score))


def calculate_earnings_catalyst_score(surprise_rank: float, profit_growth_rank: float) -> float:
    """Scores earnings surprise and EPS profit growth trends continuously."""
    return float(0.60 * surprise_rank + 0.40 * profit_growth_rank)


def calculate_opportunity_score(df_hist, indic: Dict, ms: Dict) -> float:
    """
    Scores trade timing continuously:
    - Breakouts: decays as distance from resistance increases, scaled by volume.
    - Pullbacks: decays as distance from 20 EMA increases, scaled by low volume.
    - Support proximity: decays as distance from swing support increases.
    - Volatility compression: rewards ATR contraction and BB squeeze.
    """
    try:
        close = float(df_hist['Close'].iloc[-1])
        resistance = ms.get("resistance")
        support = ms.get("swing_support") or ms.get("support")
        vol_ratio = float(indic.get("volume_ratio", 1.0))
        dist_ema20 = float(indic.get("dist_from_ema20", 0.0))
        
        score = 0.0
        
        # 1. Breakout timing (continuous)
        if resistance:
            if close > resistance:
                dist_pct = (close - resistance) / resistance
                # Decays as price goes further from resistance breakout point
                decay = np.exp(-dist_pct / 0.05) # 5% decay scale
                volume_factor = min(2.0, vol_ratio) / 1.5
                score += 35.0 * decay * volume_factor
            else:
                # Direct resistance penalty (buying right into a selling wall)
                dist_pct = (resistance - close) / close
                if dist_pct <= 0.05:
                    penalty = 15.0 * (1.0 - dist_pct / 0.05)
                    score -= penalty
                    
        # 2. Pullback timing (continuous)
        # Optimal pullback distance is close to 0 (right at 20 EMA)
        # Use Gaussian curve centered at -1.0% with width of 2.0%
        if indic.get("ema_aligned"):
            pullback_score = 30.0 * np.exp( - (dist_ema20 - (-1.0))**2 / (2 * 2.0**2) )
            volume_factor = np.exp( - max(0.0, vol_ratio - 1.0)**2 / (2 * 0.5**2) )
            score += pullback_score * volume_factor
                
        # 3. Support proximity timing (continuous)
        if support and close >= support:
            dist_pct = (close - support) / close
            decay = np.exp(-dist_pct / 0.05)
            score += 20.0 * decay
                
        # 4. Open-High-Low Pattern
        if indic.get("ohl_signal") == "bullish":
            score += 15.0
            
        # 5. Price near 52-week high (momentum)
        dist_52w = float(indic.get("dist_52w_high", -100))
        if -5.0 <= dist_52w <= 0:
            score += 10.0
            
        # 6. Volatility Contraction / Squeeze Boost (up to +10 points)
        atr_slope = float(indic.get("atr_slope", 0.0))
        bb_width_ratio = float(indic.get("bb_width_ratio", 1.0))
        contraction_boost = 0.0
        if atr_slope < 0.0:
            contraction_boost += 5.0
        if bb_width_ratio < 0.8:
            contraction_boost += 5.0 * (1.0 - bb_width_ratio) / 0.2
        score += min(10.0, contraction_boost)
            
        return max(0.0, min(100.0, score))
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════
# PORTFOLIO CORRELATION ENGINE
# ═══════════════════════════════════════════════

def get_historical_correlation(df1, df2, lookbacks: List[int] = [20, 60]) -> float:
    """Calculates max absolute correlation of daily returns over multiple lookbacks"""
    try:
        returns1 = df1['Close'].pct_change()
        returns2 = df2['Close'].pct_change()
        
        # Align on index (dates)
        aligned = pd.concat([returns1, returns2], axis=1, join='inner').dropna()
        if len(aligned) < 20:
            return 0.0
            
        corrs = []
        for l in lookbacks:
            subset = aligned.tail(l)
            if len(subset) >= 10:
                corr = subset.iloc[:, 0].corr(subset.iloc[:, 1])
                if not np.isnan(corr):
                    corrs.append(abs(corr))
                    
        return max(corrs) if corrs else 0.0
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════
# PENALTY ENGINE
# ═══════════════════════════════════════════════

def calculate_penalties(fund: Dict, indic: Dict) -> float:
    """
    Returns penalty points (negative float, max -30)
    for data uncertainty, leverage, and upcoming events.
    """
    penalty = 0.0
    
    # Upcoming earnings flag (simulated in caching)
    if fund.get("has_recent_earnings") is False:
        # Penalize data uncertainty
        penalty -= 5.0
        
    # High leverage debt/equity > 1.2
    de = fund.get("debt_to_equity")
    if de is not None and de > 1.2:
        penalty -= 10.0
        
    # Erratic high ATR volatility
    close = float(indic.get("close", 100))
    atr = float(indic.get("atr", close * 0.01))
    if (atr / close) > 0.06:
        penalty -= 10.0
        
    return max(-30.0, penalty)



def calculate_avg_pairwise_correlation(dfs: List[pd.DataFrame]) -> float:
    """Calculates the average absolute pairwise return correlation among a list of stock DataFrames."""
    if len(dfs) < 2:
        return 0.0
    try:
        returns_list = []
        for i, df in enumerate(dfs):
            ret = df['Close'].pct_change()
            returns_list.append(ret.rename(f"stock_{i}"))
        
        aligned = pd.concat(returns_list, axis=1, join='inner').dropna()
        if len(aligned) < 10:
            return 0.0
            
        corr_matrix = aligned.corr()
        upper_tri = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
        vals = upper_tri.stack().values
        
        if len(vals) == 0:
            return 0.0
        return float(np.mean(np.abs(vals)))
    except Exception:
        return 0.0


def get_active_open_positions_heat() -> float:
    """Calculates total capital risk (heat) of active positions in the last 20 days."""
    from datetime import date, timedelta
    
    db = db_manager.get_db()
    min_date = str(date.today() - timedelta(days=20))
    records = []
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            records = list(col.find({"date": {"$gte": min_date}}))
        except Exception:
            records = []
            
    if not records or db is None:
        try:
            records = db_manager._read_json("daily_picks.json")
            records = [r for r in records if r.get("date", "") >= min_date]
        except Exception:
            records = []
            
    active_count = 0
    for r in records:
        picks_list = r.get("picks", r.get("top_picks", []))
        for p in picks_list:
            if p.get("action") == "BUY":
                active_count += 1
                
    risk_per_trade_pct = getattr(config, "RISK_PER_TRADE_PCT", 0.02) * 100.0
    return active_count * risk_per_trade_pct


def compute_percentiles(raw_map: Dict[str, float]) -> Dict[str, float]:
    """Helper to compute percentile ranks (0-100) dynamically across a raw value map."""
    if not raw_map:
        return {}
    symbols = list(raw_map.keys())
    vals = np.array(list(raw_map.values()), dtype=float)
    if len(vals) == 0:
        return {}
    if np.all(vals == vals[0]):
        return {sym: 50.0 for sym in symbols}
    
    ranks = {}
    for sym, val in raw_map.items():
        if pd.isna(val):
            ranks[sym] = 0.0
        else:
            ranks[sym] = float(np.sum(vals <= val) / len(vals) * 100.0)
    return ranks


def run_screen(symbols: Optional[List[str]] = None,
               top_n: int = config.TOP_PICKS_COUNT) -> Dict:
    """
    Automated Alpha Engine v5.0 Systematic Quant Engine screening pipeline.
    """
    start_time = datetime.now()
    symbols = symbols or config.ALL_SYMBOLS
    logger.info(f"Initiating STALKER Alpha Engine v5.0 across {len(symbols)} stocks...")

    # Load indices and all stocks history first to calculate market breadth
    print(f"\n📥 Loading price structures for Nifty and {len(symbols)} stocks...")
    indices_data = df_module.fetch_market_indices()
    nifty_df = indices_data.get("NIFTY50")
    all_history = df_module.fetch_multiple_stocks(symbols)

    # Calculate Market Breadth (percentage of universe stocks trading above 20-day EMA)
    stocks_above_20ema = 0
    total_valid_stocks = 0
    for sym, df_hist in all_history.items():
        if df_hist is not None and len(df_hist) >= 20:
            total_valid_stocks += 1
            close = float(df_hist['Close'].iloc[-1])
            ema20 = float(df_hist['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
            if close > ema20:
                stocks_above_20ema += 1
                
    market_breadth = (stocks_above_20ema / total_valid_stocks) if total_valid_stocks > 0 else 0.5
    print(f"📊 Market Breadth (stocks above 20 EMA): {market_breadth*100:.1f}%")

    # Evaluate market environment regime with 5 states
    print("🌐 Evaluating market environment regime...")
    db = db_manager.get_db()
    prev_breadth = market_breadth
    try:
        from datetime import date, timedelta
        prev_pick = None
        if db is not None:
            prev_pick = db[config.MONGO_COLLECTION_PICKS].find_one({"date": {"$lt": str(date.today())}}, sort=[("date", -1)])
        if not prev_pick:
            prev_picks = db_manager._read_json("daily_picks.json")
            if prev_picks:
                prev_pick = prev_picks[-1]
        if prev_pick:
            prev_breadth = prev_pick.get("market_breadth", market_breadth)
    except Exception:
        pass

    market_regime = get_market_regime(nifty_df, market_breadth, prev_breadth)
    market_is_bullish = market_regime == "Bull"
    print(f"   Market regime: {market_regime.upper()}")

    # Scan sector indices
    sector_trends = {}
    sector_index_map = {
        "Banking":    "BANKNIFTY",
        "IT":         "NIFTY_IT",
        "Pharma":     "NIFTY_PHARMA",
        "Auto":       "NIFTY_AUTO",
        "FMCG":       "NIFTY_FMCG",
        "Energy":     "NIFTY_ENERGY",
        "Metal":      "NIFTY_METAL",
    }
    for sector_name, idx_key in sector_index_map.items():
        idx_df = indices_data.get(idx_key)
        sector_trends[sector_name] = df_module.get_market_trend(idx_df) if idx_df is not None else "unknown"

    # ─────────────────────────────────────────────
    # STAGE 1: Hard Safety Gating Filter
    # ─────────────────────────────────────────────
    print(f"\n🛡️ Running Stage 1 Safety Filters & technical calculations...")
    survivors = []
    
    for symbol in symbols:
        df_hist = all_history.get(symbol)
        if df_hist is None or len(df_hist) < 20:
            continue
            
        try:
            # Compute technical indicators
            indic = ind.compute_all_indicators(df_hist, nifty_df)
            if not indic:
                continue
                
            # Hard Liquidity Gating
            is_liquid, avg_vol, avg_value = evaluate_liquidity(df_hist)
            if not is_liquid:
                continue
                
            # Hard Overnight Gap-Risk Safety Filter
            if check_overnight_gap_risk(df_hist):
                continue
                
            # Hard ATR Volatility Spike Safety Filter
            if check_atr_spike_risk(df_hist, indic):
                continue
                
            # Hard Circuit-Lock Safety Filter
            if check_circuit_lock(df_hist):
                continue
                
            # Hard Risk Gating
            risk_score, max_dd, atr_pct = evaluate_risk_profile(df_hist, indic)
            risk_threshold = 4.2 if market_regime in ["Neutral", "Bear", "Deteriorating"] else 5.0
            if risk_score > risk_threshold:  # Gating threshold
                continue
                
            # Fundamentals and news (pre-fetched/cached)
            fund = df_module.fetch_fundamentals(symbol)
            news = df_module.fetch_news_signals(symbol)
            
            # Hard Data Quality Gating
            dq_score, missing_fields = calculate_data_quality(symbol, fund, df_hist)
            if dq_score < 70.0:  # Gating threshold
                continue
                
            # Detect structure
            ms = ms_module.detect_market_structure(df_hist)
            
            # Hard Downtrend Rejection — NEVER buy into a confirmed downtrend
            if ms.get("structure") == "downtrend":
                continue
            
            survivors.append({
                "symbol": symbol,
                "df_hist": df_hist,
                "indic": indic,
                "fund": fund,
                "news": news,
                "ms": ms,
                "data_quality_score": dq_score,
                "missing_fields": missing_fields,
                "risk_score": risk_score,
                "max_drawdown": max_dd,
                "atr_pct": atr_pct,
                "avg_value": avg_value
            })
            
        except Exception as e:
            logger.error(f"Error in Stage 1 filters for {symbol}: {e}")
            continue

    print(f"   {len(survivors)} stocks passed Stage 1 Safety filters.")

    if not survivors:
        return {
            "date":           datetime.now().strftime("%Y-%m-%d"),
            "scan_time":      datetime.now().strftime("%H:%M:%S"),
            "market_trend":   market_regime.lower(),
            "market_bullish": market_is_bullish,
            "market_breadth": round(market_breadth, 2),
            "sector_trends":  sector_trends,
            "top_picks":      [],
            "scanned":        len(symbols),
            "qualified":      0,
            "elapsed_sec":    (datetime.now() - start_time).seconds,
        }

    # ── COMPUTE PERCENTILE MAPS ACROSS SURVIVORS ──
    raw_rs_map = {item["symbol"]: float(item["indic"].get("rs_vs_nifty", 0.0)) for item in survivors}
    raw_vol_ratio_map = {item["symbol"]: float(item["indic"].get("volume_ratio", 1.0)) for item in survivors}
    raw_cmf_map = {item["symbol"]: float(item["indic"].get("cmf", 0.0)) for item in survivors}
    raw_roe_map = {item["symbol"]: float(item["fund"].get("roe") or 0.0) for item in survivors}
    raw_profit_growth_map = {item["symbol"]: float(item["fund"].get("profit_growth") or 0.0) for item in survivors}
    raw_revenue_growth_map = {item["symbol"]: float(item["fund"].get("revenue_growth") or 0.0) for item in survivors}
    
    raw_sales_growth_map = {}
    raw_margin_expansion_map = {}
    raw_earnings_revision_map = {}
    raw_roe_trend_map = {}
    raw_earnings_surprise_map = {}
    raw_turnover_map = {item["symbol"]: float(item["avg_value"]) for item in survivors}
    
    for item in survivors:
        sym = item["symbol"]
        f = item["fund"]
        raw_sales_growth_map[sym] = float(fund_module.compute_growth_trend_metric(f.get("quarterly_revs", [])))
        raw_margin_expansion_map[sym] = float(fund_module.compute_growth_trend_metric(f.get("quarterly_margins", [])))
        raw_earnings_revision_map[sym] = float(fund_module.compute_growth_trend_metric(f.get("quarterly_eps", [])))
        raw_roe_trend_map[sym] = float(fund_module.compute_growth_trend_metric(f.get("quarterly_profits", [])))
        raw_earnings_surprise_map[sym] = float(f.get("earnings_surprise") or 0.0)
        
    rs_percentiles = compute_percentiles(raw_rs_map)
    volume_percentiles = compute_percentiles(raw_vol_ratio_map)
    cmf_percentiles = compute_percentiles(raw_cmf_map)
    roe_percentiles = compute_percentiles(raw_roe_map)
    profit_growth_percentiles = compute_percentiles(raw_profit_growth_map)
    revenue_growth_percentiles = compute_percentiles(raw_revenue_growth_map)
    sales_growth_percentiles = compute_percentiles(raw_sales_growth_map)
    margin_expansion_percentiles = compute_percentiles(raw_margin_expansion_map)
    earnings_revision_percentiles = compute_percentiles(raw_earnings_revision_map)
    roe_trend_percentiles = compute_percentiles(raw_roe_trend_map)
    earnings_surprise_percentiles = compute_percentiles(raw_earnings_surprise_map)
    turnover_percentiles = compute_percentiles(raw_turnover_map)

    # ── SECTOR & INDUSTRY MOMENTUM LEADERSHIP ──
    sector_rs_scores = {}
    industry_rs_scores = {}
    
    for item in survivors:
        sym = item["symbol"]
        f = item["fund"]
        sector = data_fetcher_get_sector(sym, f)
        industry = f.get("industry", "Unknown")
        rs_p = rs_percentiles.get(sym, 50.0)
        
        if sector not in sector_rs_scores:
            sector_rs_scores[sector] = []
        sector_rs_scores[sector].append(rs_p)
        
        if industry not in industry_rs_scores:
            industry_rs_scores[industry] = []
        industry_rs_scores[industry].append(rs_p)
        
    avg_sector_rs = {sect: float(np.mean(scores)) for sect, scores in sector_rs_scores.items()}
    avg_industry_rs = {ind_name: float(np.mean(scores)) for ind_name, scores in industry_rs_scores.items()}
    
    leading_sectors = set()
    if avg_sector_rs:
        sorted_sects = sorted(avg_sector_rs.items(), key=lambda x: x[1], reverse=True)
        cutoff = max(1, int(len(sorted_sects) * 0.25))
        leading_sectors = set(sect for sect, _ in sorted_sects[:cutoff])
        
    leading_industries = set()
    if avg_industry_rs:
        sorted_inds = sorted(avg_industry_rs.items(), key=lambda x: x[1], reverse=True)
        cutoff = max(1, int(len(sorted_inds) * 0.25))
        leading_industries = set(ind_name for ind_name, _ in sorted_inds[:cutoff])

    # ─────────────────────────────────────────────
    # STAGE 3: Alpha Scoring Models
    # ─────────────────────────────────────────────
    print(f"\n📊 Running Stage 3 Alpha Ranking Models...")
    temp_candidates = []
    
    for item in survivors:
        symbol = item["symbol"]
        df_hist = item["df_hist"]
        indic = item["indic"]
        fund = item["fund"]
        news = item["news"]
        ms = item["ms"]
        
        try:
            sector = data_fetcher_get_sector(symbol, fund)
            industry = fund.get("industry", "Unknown")
            
            # Model Scores (dynamic percentiles)
            rs_score = calculate_rs_score(symbol, indic, rs_percentiles)
            inst_score = calculate_institutional_score(volume_percentiles[symbol], cmf_percentiles[symbol])
            sect_score = calculate_sector_score(sector, sector_trends)
            
            fund_score = calculate_fundamental_score(
                fund,
                roe_rank=roe_percentiles[symbol],
                profit_growth_rank=profit_growth_percentiles[symbol],
                revenue_growth_rank=revenue_growth_percentiles[symbol],
                sales_growth_rank=sales_growth_percentiles[symbol],
                earnings_revision_rank=earnings_revision_percentiles[symbol],
                margin_expansion_rank=margin_expansion_percentiles[symbol],
                roe_trend_rank=roe_trend_percentiles[symbol]
            )
            
            tech_score = calculate_technical_score(indic)
            earn_score = calculate_earnings_catalyst_score(earnings_surprise_percentiles[symbol], profit_growth_percentiles[symbol])
            opp_score = calculate_opportunity_score(df_hist, indic, ms)
            
            structure = ms.get("structure", "sideways")
            ms_strength = ms.get("strength", 0)
            structure_scores = {
                "breakout": 100.0,
                "uptrend": 80.0,
                "sideways": 30.0,
                "downtrend": 0.0,
                "unknown": 20.0,
            }
            struct_score = structure_scores.get(structure, 20.0)
            if structure in ["uptrend", "breakout"] and ms_strength >= 75:
                struct_score = min(100.0, struct_score + 15.0)

            # 1. Composite Alpha Score calculation
            alpha_score = (
                (getattr(config, "WEIGHT_RS", 15) / 100.0) * rs_score +
                (getattr(config, "WEIGHT_MARKET_STRUCTURE", 15) / 100.0) * struct_score +
                (getattr(config, "WEIGHT_TECHNICAL", 15) / 100.0) * tech_score +
                (getattr(config, "WEIGHT_INSTITUTIONAL", 15) / 100.0) * inst_score +
                (getattr(config, "WEIGHT_FUNDAMENTALS", 20) / 100.0) * fund_score +
                (getattr(config, "WEIGHT_EARNINGS", 10) / 100.0) * earn_score +
                (getattr(config, "WEIGHT_SECTOR", 5) / 100.0) * sect_score +
                (getattr(config, "WEIGHT_OPPORTUNITY", 5) / 100.0) * opp_score
            )
            
            # Momentum Leadership Boost (up to +10 alpha points)
            leadership_boost = 0.0
            if rs_score >= 75.0:
                if sector in leading_sectors or industry in leading_industries:
                    leadership_boost = 7.5
            alpha_score = min(100.0, alpha_score + leadership_boost)
            
            # 2. Setup Expectancy & Classification Engine
            trade_type = rm.get_trade_type(
                ms.get("structure", ""), 
                float(indic.get("gap_pct", 0)), 
                float(indic.get("rsi", 50)), 
                indic, 
                fund
            )
            
            # Regimes and bucketing
            nifty_vol = calculate_nifty_realized_volatility(nifty_df)
            volatility_regime = "high" if nifty_vol > 22.0 else "normal"
            breadth_regime = "weak" if market_breadth < 0.30 else "strong" if market_breadth >= 0.60 else "normal"
            
            avg_value = item["avg_value"]
            liquidity_bucket = "high" if avg_value >= 500000000 else "medium" if avg_value >= 150000000 else "low"
            
            dist_ema20 = float(indic.get("dist_from_ema20", 0.0))
            bb_squeeze = indic.get("bb_squeeze", False)
            dist_52w = float(indic.get("dist_52w_high", -100))
            
            if trade_type == "BREAKOUT":
                setup_subtype = "bb_squeeze" if bb_squeeze else "52w_high_breakout" if dist_52w >= -2.0 else "standard"
            elif trade_type == "PULLBACK":
                setup_subtype = "ema20_pullback" if abs(dist_ema20) <= 1.0 else "standard"
            else:
                setup_subtype = "standard"

            # Fetch setup expectancy statistics
            setup_exp = db_manager.get_setup_expectancy(
                trade_type,
                market_regime=market_regime,
                sector=sector,
                score=alpha_score,
                volatility_regime=volatility_regime,
                breadth_regime=breadth_regime,
                liquidity_bucket=liquidity_bucket,
                setup_subtype=setup_subtype
            )
            win_rate = setup_exp.get("win_rate", 50.0)
            avg_return = setup_exp.get("avg_return", 0.0)
            expectancy = setup_exp.get("expectancy", 0.0)
            confidence_score = win_rate
            
            # 3. Penalty Engine
            penalty = calculate_penalties(fund, indic)
            if market_regime == "Bear":
                is_defense = sector.lower() in ["healthcare", "consumer defensive", "utilities"]
                if not is_defense:
                    penalty -= 15.0
            adjusted_alpha = alpha_score + penalty
            
            # Format Bullet Points Reasons
            sentiment = news.get("news_sentiment", "neutral")
            reasons = _build_reasons_v3(indic, ms, fund, news, market_is_bullish, sector, sector_trends, adjusted_alpha)
            bullish_factors, bearish_factors = _get_factors(fund, indic, item["missing_fields"], sentiment)
            
            temp_candidates.append({
                "name":               symbol.replace(".NS", "").replace(".BO", ""),
                "symbol":             symbol,
                "adjusted_alpha":     adjusted_alpha,
                "alpha_score":        round(adjusted_alpha, 1),
                "total_score":        round(adjusted_alpha, 1),
                "confidence_score":   round(confidence_score, 1),
                "expectancy_win_rate": round(win_rate, 1),
                "expectancy_avg_return": round(avg_return, 2),
                "expectancy_score":    round(expectancy, 2),
                "opportunity_score":  round(opp_score, 1),
                "risk_score":         round(item["risk_score"], 1),
                
                # Full output dictionary fields
                "current_price":      round(float(df_hist['Close'].iloc[-1]), 2),
                "risk_profile":       rm.get_risk_profile(adjusted_alpha, ms.get("structure", ""), indic.get("volume_surge", False)),
                "trade_type":         trade_type,
                "setup_subtype":      setup_subtype,
                "volatility_regime":  volatility_regime,
                "breadth_regime":     breadth_regime,
                "liquidity_bucket":   liquidity_bucket,
                
                "rs_rank":            round(rs_score, 1),
                "structure_score":    round(struct_score, 1),
                "technical_score":    round(tech_score, 1),
                "institutional_score": round(inst_score, 1),
                "fundamental_score":  round(fund_score, 1),
                "earnings_score":     round(earn_score, 1),
                "sector_rank":        round(sect_score, 1),
                "liquidity_score":     round(turnover_percentiles.get(symbol, 50.0), 1),
                "sector":             sector,
                
                "news_summary":       f"Media announcements are {sentiment}. Recent announcements: {', '.join(news.get('headlines', ['No major headlines']))[:150]}.",
                "technical_summary":  f"RSI is {indic.get('rsi', 50.0):.1f}. Price above 20 EMA is {indic.get('above_vwap', False)}. EMAs aligned is {indic.get('ema_aligned', False)}.",
                "fundamental_summary": f"Debt/Equity ratio is {fund.get('debt_to_equity') or 'comfortably low'}. ROE is {(fund.get('roe') or 0)*100:.1f}%. MCAP is ₹{(fund.get('market_cap',0) or 0)/1e7:.1f} Cr.",
                
                "bullish_factors":    bullish_factors,
                "bearish_factors":    bearish_factors,
                "reasons":            reasons,
                "validation_audit": {
                    "data_quality":      round(item["data_quality_score"], 1),
                    "liquidity":         "pass",
                    "risk":              round(item["risk_score"], 1),
                    "relative_strength": round(rs_score, 1),
                    "institutional":     round(inst_score, 1),
                    "structure":         round(struct_score, 1),
                    "sector":            round(sect_score, 1),
                    "fundamentals":      round(fund_score, 1),
                    "technical":         round(tech_score, 1),
                    "earnings":          round(earn_score, 1),
                    "opportunity":       round(opp_score, 1)
                },
                "df_hist":            df_hist,
                "indic":              indic,
                "ms":                 ms
            })
            
        except Exception as e:
            logger.error(f"Error in Alpha Ranking for {symbol}: {e}")
            continue

    # Sort candidates by Adjusted Alpha score descending to determine ranks and apply dual filters
    temp_candidates.sort(key=lambda x: x["adjusted_alpha"], reverse=True)
    N = len(temp_candidates)
    
    candidates = []
    for idx, stock in enumerate(temp_candidates):
        rank_in_universe = idx + 1
        adj_alpha = stock["adjusted_alpha"]
        
        # Enforce dual quality-percentile filters
        passes_dual_filter = False
        if market_regime == "Bull":
            passes_dual_filter = (adj_alpha >= 70.0) and (rank_in_universe <= max(1, int(0.10 * N)))
        elif market_regime in ["Improving", "Neutral"]:
            passes_dual_filter = (adj_alpha >= 75.0) and (rank_in_universe <= max(1, int(0.05 * N)))
        else: # Bear or Deteriorating
            passes_dual_filter = (adj_alpha >= 80.0) and (rank_in_universe <= max(1, int(0.03 * N)))

        # Calculate stop loss, targets & check R:R ratio
        entry_price = stock["current_price"]
        sl_price = rm.calculate_stop_loss(entry_price, float(stock["indic"].get("atr", 1.0)), stock["ms"].get("swing_support"))
        targets_dict = rm.calculate_targets(entry_price, sl_price)
        rr_ratio_val = targets_dict.get("rr_ratio")
        
        is_confirmed = passes_dual_filter and (rr_ratio_val is not None and rr_ratio_val >= 1.5)
        
        # Strict Bullish Regime Gating Check
        if getattr(config, "STRICT_BULL_ONLY_BUY", False) and market_regime != "Bull":
            is_confirmed = False
            
        action_val = "BUY" if is_confirmed else "WATCH"
        action_color = "green" if action_val == "BUY" else "yellow"
        
        stock["action"] = action_val
        stock["action_color"] = action_color
        stock["stop_loss"] = targets_dict["stop_loss"] if is_confirmed else None
        stock["target_1"] = targets_dict["target_1"] if is_confirmed else None
        stock["target_2"] = targets_dict["target_2"] if is_confirmed else None
        stock["rr_ratio"] = targets_dict["rr_ratio"] if is_confirmed else None
        
        # Setup-specific execution rules
        trade_type = stock["trade_type"]
        if action_val == "BUY":
            if trade_type == "BREAKOUT":
                execution_rule = "Buy ONLY if price breaks above today's VWAP on 15m chart with high volume (vol ratio > 1.8x) after 9:30 AM."
            elif trade_type == "PULLBACK":
                execution_rule = "Buy near 20 EMA on 15m chart ONLY when a green support bounce candle forms with low volume."
            elif trade_type == "MOMENTUM":
                execution_rule = "Buy ONLY if today's 9:15 AM Open equals today's Low (OHL Long setup). Skip if it dips below Open."
            elif trade_type == "VALUE_MOMENTUM":
                execution_rule = "Buy near swing support when RSI shows bullish divergence and volume increases."
            elif trade_type == "EARNINGS_RUNNER":
                execution_rule = "Buy on confirmed post-earnings trend continuation. Confirm breakout with 1.8x volume."
            else:
                execution_rule = "Confirm breakout with 1.8x volume and price above VWAP before entry."
        else:
            if market_regime != "Bull" and getattr(config, "STRICT_BULL_ONLY_BUY", False):
                execution_rule = f"Strictly monitor today. Market Trend is {market_regime.upper()} (Risk Shield active — DO NOT enter trades)."
            else:
                execution_rule = "Strictly monitor today. DO NOT enter trades."
        
        stock["execution_rule"] = execution_rule
        
        # Pop transient dictionaries before saving
        stock.pop("indic", None)
        stock.pop("ms", None)
        
        candidates.append(stock)

    # ─────────────────────────────────────────────
    # STAGE 4: Portfolio Assembly & Correlation Control
    # ─────────────────────────────────────────────
    print(f"\n💼 Constructing portfolio & running Pearson Correlation controls...")
    portfolio = []
    sector_counts = {}
    industry_counts = {}
    
    # Active heat check
    active_heat = get_active_open_positions_heat()
    heat_limits = {
        "Bull": 12.0,
        "Improving": 10.0,
        "Neutral": 8.0,
        "Deteriorating": 6.0,
        "Bear": 4.0
    }
    heat_limit = heat_limits.get(market_regime, 8.0)
    risk_per_trade_pct = getattr(config, "RISK_PER_TRADE_PCT", 0.02) * 100.0
    proposed_dfs = []
    
    for stock in candidates:
        if stock["action"] != "BUY":
            continue
            
        symbol = stock["symbol"]
        sector = stock["sector"]
        industry = stock["fund"].get("industry", "Unknown")
        df_hist = stock["df_hist"]
        
        # 1. Sector Concentration Limit check (Max 2 stocks per sector)
        s_count = sector_counts.get(sector, 0)
        if s_count >= 2:
            continue
            
        # 2. Industry Concentration Limit check (Max 1 stock per industry)
        ind_count = industry_counts.get(industry, 0)
        if ind_count >= 1:
            continue
            
        # 3. Portfolio Heat Control
        if active_heat + risk_per_trade_pct > heat_limit:
            print(f"   Skipping {symbol}: Heat limit exceeded ({active_heat + risk_per_trade_pct:.1f}% > {heat_limit:.1f}%)")
            continue
            
        # 4. Pearson Correlation Gating
        temp_dfs = proposed_dfs + [df_hist]
        if len(temp_dfs) >= 2:
            avg_corr = calculate_avg_pairwise_correlation(temp_dfs)
            if avg_corr > 0.60:
                print(f"   Skipping {symbol}: Average pairwise correlation would be {avg_corr:.2f} (> 0.60)")
                continue
                
        # Proximity correlation (single asset check)
        single_correlated = False
        for active in portfolio:
            r = get_historical_correlation(df_hist, active["df_hist"])
            if r > 0.80:
                single_correlated = True
                break
        if single_correlated:
            continue
            
        # Passed portfolio construction! Add to final selection.
        portfolio.append(stock)
        proposed_dfs.append(df_hist)
        sector_counts[sector] = s_count + 1
        industry_counts[industry] = ind_count + 1
        active_heat += risk_per_trade_pct
        
        if len(portfolio) >= top_n:
            break

    # Clean up DF before return to avoid serialisation issues
    for item in portfolio:
        item.pop("df_hist", None)
        
    for item in candidates:
        item.pop("df_hist", None)
        
    # Apply formal sequential numbering to active Rank
    for rank_idx, item in enumerate(portfolio, 1):
        item["rank"] = rank_idx
        item["position_rank"] = rank_idx

    elapsed = (datetime.now() - start_time).seconds
    print(f"\n✅ STALKER Alpha Engine completed in {elapsed}s.")
    print(f"   Scanned: {len(symbols)} | Qualified: {len(candidates)} | Top Picks Portfolio: {len(portfolio)} opportunities.")

    return {
        "date":           datetime.now().strftime("%Y-%m-%d"),
        "scan_time":      datetime.now().strftime("%H:%M:%S"),
        "market_trend":   market_regime.lower(),
        "market_bullish": market_is_bullish,
        "market_breadth": round(market_breadth, 2),
        "sector_trends":  sector_trends,
        "top_picks":      portfolio,
        "scanned":        len(symbols),
        "qualified":      len(candidates),
        "elapsed_sec":    elapsed,
    }


# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

def data_fetcher_get_sector(symbol, fund):
    try:
        from data_fetcher import get_sector_for_symbol
        return get_sector_for_symbol(symbol, fund)
    except Exception:
        return fund.get("sector", "Unknown")


def _get_factors(fund: Dict, indic: Dict, missing_fields: List[str], sentiment: str) -> Tuple[List[str], List[str]]:
    bullish = []
    bearish = []
    
    if indic.get("above_vwap"):
        bullish.append("Strong intraday momentum (Price above VWAP)")
    if indic.get("ema_aligned"):
        bullish.append("Uptrend conformation (20 EMA > 50 EMA)")
    if float(indic.get("volume_ratio", 1.0)) >= 1.8:
        bullish.append("Institutional volume expansion")
    if sentiment == "bullish":
        bullish.append("Constructive corporate announcements flow")
        
    # Bearish signals
    de = fund.get("debt_to_equity")
    if de is not None and de > 1.2:
        bearish.append("Erratic debt/equity ratio exceeding safety thresholds")
    if float(indic.get("rsi", 50)) > 75:
        bearish.append("Asset trading in temporary overbought extremes")
    for f in missing_fields:
        bearish.append(f"Missing critical metric: {f}")
        
    return bullish[:3], bearish[:2]


def _build_reasons_v3(indic, ms, fund, news, market_bullish, sector, sector_trends, alpha) -> List[str]:
    reasons = []
    
    # 1. Relative outperformance
    rs = indic.get("rs_vs_nifty", 0)
    if rs >= 2.0:
        reasons.append(f"📈 Relative Outperformance: Led Nifty index by +{rs:.1f}% over the last 20 trading sessions.")
        
    # 2. Institutional surge
    vol_ratio = indic.get("volume_ratio", 1.0)
    if vol_ratio >= 1.5:
        reasons.append(f"📊 Volume Acceleration: Volume surged at {vol_ratio:.1f}x normal daily average, proving accumulation.")
        
    # 3. EMA alignments
    if indic.get("ema_aligned"):
        reasons.append("⚡ Confirmed Uptrend Structure: Solid technical slope with short and medium term EMAs aligned bullishly.")
        
    # 4. Sector Rotation
    sect_trend = sector_trends.get(sector, "unknown")
    if sect_trend == "bullish":
        reasons.append(f"🏭 Industry Leadership: Strong capital inflows in the {sector} sector today.")
        
    return reasons[:3]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if "--dry-run" in sys.argv:
        test_symbols = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "SBIN.NS"]
        result = run_screen(symbols=test_symbols, top_n=3)
        print("\n" + "="*60)
        print("STALKER ALPHA ENGINE V3.0 (DRY RUN)")
        print("="*60)
        for pick in result["top_picks"]:
            print(f"\n{pick['rank']}. {pick['name']} | Alpha Score: {pick['alpha_score']} | Confidence: {pick['confidence_score']} | Action: {pick['action']}")
            print(f"   Entry: {pick['current_price']} | SL: {pick['stop_loss']} | T1: {pick['target_1']} | T2: {pick['target_2']} | R:R: {pick.get('rr_ratio')}")
            print(f"   DQ: {pick['validation_audit']['data_quality']} | Liquidity: {pick['validation_audit']['liquidity']} | Risk: {pick['validation_audit']['risk']}")
            print(f"   News Sentiment: {pick['news_summary'][:80]}...")
            print("   Bullish Factors:")
            for bf in pick['bullish_factors']:
                print(f"     * {bf}")
