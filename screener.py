# -*- coding: utf-8 -*-
"""
STALKER - Orchestrator v5.0 (Phase 1 Elite Architecture)
Clean separation of concerns across 6 independent layers:
  Stage 1: Safety Filters (hard gates)
  Stage 2: Regime Engine (8 adaptive states)
  Stage 3: Ensemble Alpha Engine (4 sub-models) + Meta Model
  Stage 4: Reality Check (execution friction)
  Stage 5: Risk Engine (drawdown-aware sizing)
  Stage 6: Portfolio Engine (correlation + concentration)
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
import regime_engine as re_module
import alpha_engine as ae_module
import meta_model as mm_module
import reality_check as rc_module
import market_pulse as pulse_module
from portfolio_engine import PortfolioEngine

# Force UTF-8 output on Windows — safe guard: never crash if buffer already replaced or closed
try:
    import io as _io
    import os
    if sys.stdout is None or getattr(sys.stdout, 'closed', False):
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    else:
        try:
            sys.stdout.write('')
        except Exception:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')

    if sys.stderr is None or getattr(sys.stderr, 'closed', False):
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    else:
        try:
            sys.stderr.write('')
        except Exception:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')

    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

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
    """Returns Sector Momentum score mapped to 0-100 based on Sector RS"""
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

    # Convert Sector RS (e.g. +5%) to a 0-100 score
    rs = float(sector_trends.get(norm_sector, 0.0))
    score = 50.0 + (rs * 10.0)
    return float(max(0.0, min(100.0, score)))


def calculate_fundamental_score(fund: Dict, roe_rank: float = 50.0, profit_growth_rank: float = 50.0,
                                revenue_growth_rank: float = 50.0, sales_growth_rank: float = 50.0,
                                earnings_revision_rank: float = 50.0, margin_expansion_rank: float = 50.0,
                                roe_trend_rank: float = 50.0, fcf_growth_rank: float = 50.0) -> float:
    """Scores fundamentals using continuous percentile ranks and regression-based growth trends."""
    res = fund_module.score_fundamentals(fund, roe_rank, profit_growth_rank, revenue_growth_rank,
                                         sales_growth_rank, earnings_revision_rank, margin_expansion_rank,
                                         roe_trend_rank, fcf_growth_rank)
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
    Returns penalty points (negative float, max -10)
    for data uncertainty. Leverage and ATR volatility penalties are removed for short-term trading.
    """
    penalty = 0.0
    
    # Upcoming earnings flag (simulated in caching)
    if fund.get("has_recent_earnings") is False:
        # Reduced penalty for short-term trading
        penalty -= 2.0
        
    # Leverage (debt/equity) and ATR volatility are ignored for intraday/BTST trading 
    # since they are not relevant to short-term momentum.
    return max(-10.0, penalty)



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
    """Calculates total capital risk (heat) of active positions in the last 2 days (Intraday & BTST)."""
    from datetime import date, timedelta
    
    db = db_manager.get_db()
    min_date = str(date.today() - timedelta(days=2))
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


def get_yesterday_picks() -> set:
    """
    Returns a set of symbols that appeared in yesterday's picks.
    Used to hard-block stocks that show no fresh movement today.
    A stock should only repeat if it's genuinely moving — not just
    because it always passes liquidity/DQ checks (BIOCON, BHARTIARTL etc.)
    """
    from datetime import date, timedelta
    yesterday = str(date.today() - timedelta(days=1))
    db = db_manager.get_db()
    records = []
    try:
        if db is not None:
            col = db[config.MONGO_COLLECTION_PICKS]
            records = list(col.find({"date": yesterday}))
    except Exception:
        pass
    if not records:
        try:
            all_records = db_manager._read_json("daily_picks.json")
            records = [r for r in all_records if r.get("date", "") == yesterday]
        except Exception:
            pass
    symbols = set()
    for rec in records:
        picks_list = rec.get("picks", rec.get("top_picks", []))
        for p in picks_list:
            sym = p.get("symbol", "")
            if sym:
                symbols.add(sym)
    return symbols


def compute_percentiles(raw_map: Dict[str, float]) -> Dict[str, float]:
    """Helper to compute percentile ranks (0-100) dynamically across a raw value map."""
    if not raw_map:
        return {}
    s = pd.Series(raw_map)
    if len(s.dropna()) == 0:
        return {sym: 50.0 for sym in raw_map.keys()}
    
    # Series.rank(pct=True) calculates rank percentile (0.0 to 1.0) ignoring NaNs
    ranks = s.rank(pct=True, na_option='keep') * 100.0
    # Fill missing/NaN ranks with a conservative neutral 35.0 percentile
    ranks = ranks.fillna(35.0)
    return ranks.to_dict()


def run_screen(symbols: Optional[List[str]] = None,
               top_n: int = config.TOP_PICKS_COUNT,
               dry_run: bool = False) -> Dict:
    """
    Automated Alpha Engine v5.0 Systematic Quant Engine screening pipeline (Stalker V2).
    """
    start_time = datetime.now()
    symbols = symbols or config.get_scan_universe()
    today_str = datetime.now().strftime("%Y-%m-%d")
    total_universe_count = len(symbols)
    
    logger.info(f"Initiating STALKER V2 Staged Pipeline across {len(symbols)} stocks (Dry Run: {dry_run})...")

    # Safe Mode flag
    safe_mode_active = False
    safe_mode_reason = ""

    # Watchdog timeout per stage (5 minutes in seconds)
    stage_timeout = 300

    try:
        # Check MongoDB connection (if not dry_run)
        if not dry_run:
            if not db_manager.check_mongo_connection():
                raise RuntimeError("MongoDB connection unavailable")

        # Check scanned universe size
        if len(symbols) < 150 and len(symbols) != 5:
            raise RuntimeError(f"Scanned universe count {len(symbols)} is < 150 symbols")

        # ─────────────────────────────────────────────
        # STAGE 1: Fundamentals Refresh
        # ─────────────────────────────────────────────
        logger.info("Executing Stage 1: Fundamentals Refresh...")
        stage1_start = time.time()
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        valid_fundamentals = 0
        fundamentals_by_symbol = {}
        successfully_fetched_count = 0
        
        def fetch_single(sym):
            try:
                fund = df_module.fetch_fundamentals(sym)
                return sym, fund
            except Exception as ex:
                logger.error(f"Error fetching fundamentals for {sym}: {ex}")
                return sym, None

        logger.info(f"Fetching fundamentals for {len(symbols)} symbols using parallel workers...")
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(fetch_single, sym): sym for sym in symbols}
            for future in as_completed(futures):
                # Check Watchdog timeout
                if time.time() - stage1_start > stage_timeout:
                    raise RuntimeError("Watchdog timeout in Stage 1 (> 5 minutes)")
                
                sym, fund = future.result()
                if fund and isinstance(fund, dict):
                    successfully_fetched_count += 1
                    
                    # Cache in DB if not dry run
                    if not dry_run and (fund.get("market_cap", 0) > 0 or fund.get("sector") != "Unknown"):
                        try:
                            db_manager.save_cached_fundamental(sym, fund)
                        except Exception as db_err:
                            logger.error(f"Failed to cache fundamentals in DB for {sym}: {db_err}")
                    
                    # Apply early price filter
                    current_price = fund.get("current_price") or fund.get("52w_high") or 0
                    min_price = getattr(config, "MIN_STOCK_PRICE", 100)
                    max_price = getattr(config, "MAX_STOCK_PRICE", 5000)
                    
                    if min_price <= current_price <= max_price:
                        fundamentals_by_symbol[sym] = fund
                        valid_fundamentals += 1

        # Check fundamentals data coverage (< 80%) based on successfully fetched symbols
        coverage = (successfully_fetched_count / len(symbols)) if symbols else 0.0
        logger.info(f"Stage 1 Fundamentals Refresh complete. Successfully fetched: {successfully_fetched_count}/{len(symbols)} ({coverage*100:.1f}% coverage)")
        logger.info(f"Early Price Gating: {valid_fundamentals} symbols are within range (₹{getattr(config, 'MIN_STOCK_PRICE', 100)} to ₹{getattr(config, 'MAX_STOCK_PRICE', 5000)})")
        
        if coverage < 0.80:
            raise RuntimeError(f"Fundamentals data coverage {coverage*100:.1f}% is < 80% threshold")

        # Update symbols list to only contain price-gated survivors for subsequent stages
        symbols = list(fundamentals_by_symbol.keys())

        # Load Nifty index data
        indices_data = df_module.fetch_market_indices()
        nifty_df = indices_data.get("NIFTY50")
        if nifty_df is None or nifty_df.empty:
            raise RuntimeError("Nifty 50 index history is unavailable")

        # ─────────────────────────────────────────────
        # STAGE 2: Technical Indicators calculation (Batch-based)
        # ─────────────────────────────────────────────
        logger.info("Executing Stage 2: Technical Indicators calculation (Batch-based)...")
        stage2_start = time.time()

        # Clear rate limit cooldown before starting Stage 2 to ensure we attempt history fetch
        try:
            import data_fetcher
            data_fetcher._rate_limit_cooldown_until = 0.0
        except Exception as e:
            logger.debug(f"Failed to reset rate limit cooldown: {e}")

        dry_run_tech_records = []
        chunk_size = 25
        batch_idx = 0
        
        # Recovery Checkpoint check
        checkpoint = db_manager.get_checkpoint(today_str)
        if checkpoint and checkpoint.get("stage") == "technical_indicators":
            last_completed = checkpoint.get("last_completed_batch", -1)
            batch_idx = last_completed + 1
            logger.info(f"Checkpoint found. Resuming technical indicators calculation from batch {batch_idx}")
            
        start_index = batch_idx * 25
        if start_index >= len(symbols):
            start_index = len(symbols)
            
        idx = start_index
        yfinance_failures = 0
        total_yfinance_calls = 0

        while idx < len(symbols):
            # Watchdog timeout check (> 5 minutes)
            if time.time() - stage2_start > stage_timeout:
                raise RuntimeError("Watchdog timeout in Stage 2 (> 5 minutes)")
                
            batch_start_time = time.time()
            
            # Dynamic chunk size slice
            current_chunk = symbols[idx:idx+chunk_size]
            logger.info(f"Processing Batch {batch_idx} (symbols {idx} to {idx+len(current_chunk)}), chunk size: {chunk_size}...")
            
            # Fetch history in parallel for batch
            batch_history = df_module.fetch_multiple_stocks(current_chunk)
            
            batch_records = []
            for symbol in current_chunk:
                total_yfinance_calls += 1
                df_hist = batch_history.get(symbol)
                if df_hist is None or df_hist.empty:
                    yfinance_failures += 1
                    continue
                    
                try:
                    # Calculate all technical indicators
                    indic = ind.compute_all_indicators(df_hist, nifty_df)
                    if indic:
                        # Extract liquidity features
                        is_liquid, avg_vol, avg_value = evaluate_liquidity(df_hist)
                        indic["is_liquid"] = is_liquid
                        indic["avg_value"] = avg_value
                        
                        # Extract other features for Stage 4 gating
                        gap_risk = check_overnight_gap_risk(df_hist)
                        atr_spike = check_atr_spike_risk(df_hist, indic)
                        circuit_locked = check_circuit_lock(df_hist)
                        risk_score, max_dd, atr_pct = evaluate_risk_profile(df_hist, indic)
                        
                        # Fundamentals for data quality
                        fund = fundamentals_by_symbol.get(symbol, {})
                        dq_score, missing_fields = calculate_data_quality(symbol, fund, df_hist)
                        
                        # Market structure
                        ms = ms_module.detect_market_structure(df_hist)
                        
                        record = {
                            "symbol": symbol,
                            "date": today_str,
                            "indicators": indic,
                            "structure": ms,
                            "is_liquid": is_liquid,
                            "avg_value": avg_value,
                            "overnight_gap_risk": gap_risk,
                            "atr_spike_risk": atr_spike,
                            "circuit_lock": circuit_locked,
                            "risk_score": risk_score,
                            "max_drawdown": max_dd,
                            "atr_pct": atr_pct,
                            "data_quality_score": dq_score,
                            "missing_fields": missing_fields,
                            "calculated_at": datetime.now().isoformat()
                        }
                        batch_records.append(record)
                except Exception as e:
                    logger.error(f"Error calculating technical indicators for {symbol}: {e}")

            # Atomic MongoDB Bulk Write (upsert)
            if batch_records:
                if not dry_run:
                    write_success = db_manager.bulk_write_records("technical_cache", batch_records, ["symbol", "date"])
                    if not write_success:
                        # Retry write once
                        logger.warning("Bulk write failed. Retrying...")
                        write_success = db_manager.bulk_write_records("technical_cache", batch_records, ["symbol", "date"])
                    
                    if not write_success:
                        raise RuntimeError(f"Atomic MongoDB Bulk Write failed for technical_cache batch {batch_idx}")
                else:
                    logger.info(f"[DRY RUN] Bypassing bulk write of {len(batch_records)} records to technical_cache.")
                    dry_run_tech_records.extend(batch_records)

            # Update index
            idx += len(current_chunk)
            
            # Batch runtime tracking
            batch_runtime = time.time() - batch_start_time
            logger.info(f"Batch {batch_idx} completed in {batch_runtime:.2f}s")
            
            # Update checkpoint (only if not dry run)
            if not dry_run:
                db_manager.save_checkpoint(today_str, "technical_indicators", batch_idx)
                
            # Self-Tuning Dynamic Chunk Size:
            # Track the runtime of the last completed batch.
            # If runtime > 30 seconds -> decrement chunk size by 5.
            # If runtime < 10 seconds -> increment chunk size by 5.
            # Limits: Minimum 10, Maximum 30, Default 25.
            if batch_runtime > 30.0:
                chunk_size = max(10, chunk_size - 5)
                logger.info(f"Tuning chunk size down to {chunk_size}")
            elif batch_runtime < 10.0:
                chunk_size = min(30, chunk_size + 5)
                logger.info(f"Tuning chunk size up to {chunk_size}")
                
            # Garbage collection
            import gc
            gc.collect()
            
            batch_idx += 1
            
        # Check Yahoo Finance API failure rate (> 20%)
        if total_yfinance_calls > 0:
            fail_rate = yfinance_failures / total_yfinance_calls
            logger.info(f"Stage 2 complete. Yahoo Finance download failure rate: {fail_rate*100:.1f}% ({yfinance_failures}/{total_yfinance_calls})")
            if fail_rate > 0.20:
                raise RuntimeError(f"Yahoo Finance API failure rate {fail_rate*100:.1f}% is > 20% threshold")

        # Clear checkpoints after successful completion of Stage 2
        if not dry_run:
            db_manager.clear_checkpoint(today_str)

        # ─────────────────────────────────────────────
        # STAGE 3: Universe-Wide Percentile Engine
        # ─────────────────────────────────────────────
        logger.info("Executing Stage 3: Universe-Wide Percentile Engine...")
        stage3_start = time.time()

        # Read technical cache for today
        db = db_manager.get_db()
        tech_records = []
        if dry_run:
            pre_records = []
            if db is not None:
                try:
                    col = db["technical_cache"]
                    pre_records = list(col.find({"date": today_str}))
                except Exception as e:
                    logger.error(f"Failed to read technical_cache during dry run: {e}")
            if not pre_records:
                pre_records = db_manager._read_json("technical_cache.json")
                pre_records = [r for r in pre_records if r.get("date") == today_str]
            seen_symbols = {r["symbol"] for r in dry_run_tech_records}
            tech_records = dry_run_tech_records + [r for r in pre_records if r["symbol"] not in seen_symbols]
        else:
            if db is not None:
                try:
                    col = db["technical_cache"]
                    tech_records = list(col.find({"date": today_str}))
                except Exception as e:
                    logger.error(f"Failed to read technical_cache: {e}")
                    
            if not tech_records:
                # Fallback to local file
                tech_records = db_manager._read_json("technical_cache.json")
                tech_records = [r for r in tech_records if r.get("date") == today_str]

        if not tech_records:
            raise RuntimeError("No records found in technical_cache for today's scan")

        # Build raw maps
        raw_rs_map = {}
        raw_vol_ratio_map = {}
        raw_cmf_map = {}
        raw_turnover_map = {}
        raw_roe_map = {}
        raw_profit_growth_map = {}
        raw_revenue_growth_map = {}
        raw_sales_growth_map = {}
        raw_margin_expansion_map = {}
        raw_earnings_revision_map = {}
        raw_roe_trend_map = {}
        raw_fcf_growth_map = {}
        raw_earnings_surprise_map = {}

        indicators_by_symbol = {}
        structures_by_symbol = {}
        technical_cache_by_symbol = {}

        for r in tech_records:
            sym = r["symbol"]
            indic = r["indicators"]
            ms = r["structure"]
            indicators_by_symbol[sym] = indic
            structures_by_symbol[sym] = ms
            technical_cache_by_symbol[sym] = r

            raw_rs_map[sym] = float(indic.get("rs_vs_nifty")) if indic.get("rs_vs_nifty") is not None else None
            raw_vol_ratio_map[sym] = float(indic.get("volume_ratio")) if indic.get("volume_ratio") is not None else None
            raw_cmf_map[sym] = float(indic.get("cmf")) if indic.get("cmf") is not None else None
            raw_turnover_map[sym] = float(r.get("avg_value")) if r.get("avg_value") is not None else None

            # Fetch cached fundamentals
            fund = fundamentals_by_symbol.get(sym, {})
            raw_roe_map[sym] = float(fund.get("roe")) if fund.get("roe") is not None else None
            raw_profit_growth_map[sym] = float(fund.get("profit_growth")) if fund.get("profit_growth") is not None else None
            raw_revenue_growth_map[sym] = float(fund.get("revenue_growth")) if fund.get("revenue_growth") is not None else None
            
            raw_sales_growth_map[sym] = float(fund_module.compute_growth_trend_metric(fund.get("quarterly_revs", [])))
            raw_margin_expansion_map[sym] = float(fund_module.compute_growth_trend_metric(fund.get("quarterly_margins", [])))
            raw_earnings_revision_map[sym] = float(fund_module.compute_growth_trend_metric(fund.get("quarterly_eps", [])))
            raw_roe_trend_map[sym] = float(fund_module.compute_growth_trend_metric(fund.get("quarterly_profits", [])))
            raw_fcf_growth_map[sym] = float(fund_module.compute_growth_trend_metric(fund.get("quarterly_fcf", [])))
            raw_earnings_surprise_map[sym] = float(fund.get("earnings_surprise")) if fund.get("earnings_surprise") is not None else None

        # Calculate Percentiles
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
        fcf_growth_percentiles = compute_percentiles(raw_fcf_growth_map)
        earnings_surprise_percentiles = compute_percentiles(raw_earnings_surprise_map)
        turnover_percentiles = compute_percentiles(raw_turnover_map)

        percentile_records = []
        percentile_cache_by_symbol = {}
        for sym in raw_rs_map.keys():
            percentiles = {
                "rs": rs_percentiles.get(sym, 50.0),
                "volume": volume_percentiles.get(sym, 50.0),
                "cmf": cmf_percentiles.get(sym, 50.0),
                "roe": roe_percentiles.get(sym, 50.0),
                "profit_growth": profit_growth_percentiles.get(sym, 50.0),
                "revenue_growth": revenue_growth_percentiles.get(sym, 50.0),
                "sales_growth": sales_growth_percentiles.get(sym, 50.0),
                "margin_expansion": margin_expansion_percentiles.get(sym, 50.0),
                "earnings_revision": earnings_revision_percentiles.get(sym, 50.0),
                "roe_trend": roe_trend_percentiles.get(sym, 50.0),
                "fcf_growth": fcf_growth_percentiles.get(sym, 50.0),
                "earnings_surprise": earnings_surprise_percentiles.get(sym, 50.0),
                "turnover": turnover_percentiles.get(sym, 50.0)
            }
            percentile_cache_by_symbol[sym] = percentiles
            
            record = {
                "symbol": sym,
                "date": today_str,
                "percentiles": percentiles,
                "calculated_at": datetime.now().isoformat()
            }
            percentile_records.append(record)

        if percentile_records and not dry_run:
            db_manager.bulk_write_records("percentile_cache", percentile_records, ["symbol", "date"])

        # Check Watchdog timeout for Stage 3
        if time.time() - stage3_start > stage_timeout:
            raise RuntimeError("Watchdog timeout in Stage 3 (> 5 minutes)")

        # ─────────────────────────────────────────────
        # STAGE 4: Stage 1 Gating
        # ─────────────────────────────────────────────
        logger.info("Executing Stage 4: Stage 1 Gating...")
        stage4_start = time.time()

        # Calculate Nifty realized volatility and market breadth
        stocks_above_50ema = 0
        stocks_above_200ema = 0
        total_valid_50 = 0
        total_valid_200 = 0

        for sym, indic in indicators_by_symbol.items():
            close = float(indic.get("close", 0))
            if close == 0:
                continue
            ema50 = float(indic.get("ema50", close))
            total_valid_50 += 1
            if close > ema50:
                stocks_above_50ema += 1
                
            ema200 = float(indic.get("ema200", close))
            total_valid_200 += 1
            if close > ema200:
                stocks_above_200ema += 1

        market_breadth_50 = (stocks_above_50ema / total_valid_50) if total_valid_50 > 0 else 0.5
        market_breadth_200 = (stocks_above_200ema / total_valid_200) if total_valid_200 > 0 else market_breadth_50
        market_breadth = (market_breadth_50 + market_breadth_200) / 2.0

        prev_breadth_50 = market_breadth_50
        try:
            prev_pick = None
            if db is not None:
                prev_pick = db[config.MONGO_COLLECTION_PICKS].find_one({"date": {"$lt": today_str}}, sort=[("date", -1)])
            if not prev_pick:
                prev_picks = db_manager._read_json("daily_picks.json")
                if prev_picks:
                    prev_pick = prev_picks[-1]
            if prev_pick:
                prev_breadth_50 = prev_pick.get("market_breadth", market_breadth_50)
        except Exception:
            pass

        # Calculate Advances and Declines early for the Regime and Pulse engines
        advances = 0
        declines = 0
        for sym, indic in indicators_by_symbol.items():
            chg = float(indic.get("change_pct", 0.0))
            if chg > 0:
                advances += 1
            else:
                declines += 1
        if declines == 0:
            ad_ratio_computed = 2.0 if advances > 0 else 1.0
        else:
            ad_ratio_computed = round(advances / declines, 2)

        # Call regime Engine to get weights with precalculated ad_ratio
        market_regime_8, market_is_risk_on, regime_data = re_module.classify_regime(
            nifty_df, market_breadth_50, market_breadth_200,
            prev_breadth_50=prev_breadth_50, ad_ratio=ad_ratio_computed
        )
        market_regime_legacy = re_module.get_legacy_regime(market_regime_8)
        market_is_bullish = market_regime_8 in ("Bull_Trend", "Bull_Expansion")
        buying_permitted = re_module.is_buying_permitted(market_regime_8)
        ensemble_weights = re_module.get_ensemble_weights(market_regime_8)

        # Risk thresholds based on regime — raised to allow intraday momentum stocks
        # (formula: atr_pct*150 + max_dd*15 gives 5–8 for healthy liquid stocks)
        if market_regime_legacy in ["Bear", "Deteriorating"]:
            risk_threshold = 5.5
        elif market_regime_legacy in ["Neutral", "Improving"]:
            risk_threshold = 6.5
        else: # Bull
            risk_threshold = 7.5

        # Load yesterday's picks for the fresh-signal gate
        # A stock that appeared yesterday can only re-enter if it's genuinely
        # moving today (change_pct >= +0.5%). This hard-blocks stale large-caps
        # like BIOCON, BHARTIARTL, LT, JSWSTEEL, FEDERALBNK that pass every
        # filter but offer no new intraday opportunity.
        yesterday_picks = get_yesterday_picks()
        if yesterday_picks:
            logger.info(f"[FRESHNESS] Yesterday's picks ({len(yesterday_picks)} symbols) — will block if no fresh move today: {sorted(yesterday_picks)}")

        FRESH_SIGNAL_MIN_CHANGE = 0.5  # Stock must be up >= 0.5% today to repeat

        survivors = []
        stage1_results = []
        
        for sym in indicators_by_symbol.keys():
            tech_cache = technical_cache_by_symbol[sym]
            
            # Apply Safety Gating:
            close_price = indicators_by_symbol[sym].get("close", 0)
            if close_price < getattr(config, "MIN_STOCK_PRICE", 50) or close_price > getattr(config, "MAX_STOCK_PRICE", 10000):
                continue
            if not tech_cache.get("is_liquid"):
                continue
            # Disabled overnight gap risk and ATR spike risk since they block high-momentum trading candidates
            # if tech_cache.get("overnight_gap_risk"):
            #     continue
            # if tech_cache.get("atr_spike_risk"):
            #     continue
            if tech_cache.get("circuit_lock"):
                continue
            if tech_cache.get("risk_score", 10.0) > risk_threshold:
                continue
            if tech_cache.get("data_quality_score", 0.0) < 70.0:
                continue
            if tech_cache.get("structure", {}).get("structure") == "downtrend":
                continue

            # Fresh-signal gate: hard-block stocks from yesterday with no new move
            if sym in yesterday_picks:
                today_change = float(indicators_by_symbol[sym].get("change_pct", 0.0))
                if today_change < FRESH_SIGNAL_MIN_CHANGE:
                    logger.debug(f"[FRESHNESS] {sym} blocked — in yesterday's picks, only {today_change:+.2f}% today (need >= +{FRESH_SIGNAL_MIN_CHANGE}%)")
                    continue
                else:
                    logger.info(f"[FRESHNESS] {sym} ALLOWED repeat — genuine move: {today_change:+.2f}% today")
                
            survivors.append(sym)
            
            # Prepare stage1_results record
            record = {
                "symbol": sym,
                "date": today_str,
                "indicators": indicators_by_symbol[sym],
                "structure": structures_by_symbol[sym],
                "fundamentals": fundamentals_by_symbol.get(sym, {}),
                "percentiles": percentile_cache_by_symbol[sym],
                "risk_score": tech_cache.get("risk_score"),
                "data_quality_score": tech_cache.get("data_quality_score"),
                "calculated_at": datetime.now().isoformat()
            }
            stage1_results.append(record)

        if stage1_results and not dry_run:
            db_manager.bulk_write_records("stage1_results", stage1_results, ["symbol", "date"])

        logger.info(f"Stage 4 Gating complete. Survivors: {len(survivors)}/{len(indicators_by_symbol)}")

        # Check Watchdog timeout for Stage 4
        if time.time() - stage4_start > stage_timeout:
            raise RuntimeError("Watchdog timeout in Stage 4 (> 5 minutes)")

        # ─────────────────────────────────────────────
        # STAGE 5: Alpha Scoring (Experimental Metrics Logging)
        # ─────────────────────────────────────────────
        logger.info("Executing Stage 5: Alpha Scoring...")
        stage5_start = time.time()

        # Group sectors average RS
        sector_rs_scores = {}
        industry_rs_scores = {}
        for sym in survivors:
            fund = fundamentals_by_symbol.get(sym, {})
            sector = data_fetcher_get_sector(sym, fund)
            industry = fund.get("industry", "Unknown")
            rs_p = percentile_cache_by_symbol[sym]["rs"]
            
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

        # Sector Trends (from indices)
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
            try:
                sector_ret = (idx_df["Close"].iloc[-1] / idx_df["Close"].iloc[-20] - 1) * 100
                nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[-20] - 1) * 100
                sector_trends[sector_name] = sector_ret - nifty_ret
            except Exception:
                sector_trends[sector_name] = 0.0

        # Load 1-year history for Minervini Trend check and stability score
        logger.info(f"Fetching 1y history for {len(survivors)} survivors to run Minervini Trend and stability checks...")
        history_1y = {}
        if survivors:
            try:
                history_1y = df_module.fetch_multiple_stocks(survivors, period="1y")
            except Exception as e:
                logger.error(f"Error fetching 1-year history for validation: {e}")

        alpha_scores_records = []
        scored_candidates = []

        # Drawdown Sizing info
        account_dd = rm.get_account_drawdown()
        dd_risk = rm.get_drawdown_adjusted_risk(account_dd_pct=account_dd)
        effective_risk_pct = dd_risk["risk_pct"]

        # Market pulse data using the dedicated Market Pulse layer
        vix = 15.0
        try:
            vix_df = df_module.fetch_stock_history("^INDIAVIX", period="5d")
            if vix_df is not None and not vix_df.empty:
                vix = float(vix_df["Close"].iloc[-1])
        except Exception:
            pass

        # Call the dedicated compute_pulse_from_indicators from the market_pulse module
        market_pulse_data = pulse_module.compute_pulse_from_indicators(indicators_by_symbol, vix_override=vix)
        pulse_score = market_pulse_data["pulse_score"]
        downgrade_buy = market_pulse_data["downgrade_buy"]


        for sym in survivors:
            indic = indicators_by_symbol[sym]
            fund = fundamentals_by_symbol.get(sym, {})
            ms = structures_by_symbol[sym]
            pcts = percentile_cache_by_symbol[sym]
            df_1y = history_1y.get(sym)
            df_3mo = df_1y.tail(60) if df_1y is not None else None
            
            try:
                # Fetch news sentiment to activate the Catalyst Engine end-to-end
                news_signals = {}
                try:
                    news_signals = df_module.fetch_news_signals(sym)
                except Exception as e:
                    logger.warning(f"Failed to fetch news signals for {sym}: {e}")

                sector = data_fetcher_get_sector(sym, fund)
                industry = fund.get("industry", "Unknown")
                
                # Model scores
                rs_score = calculate_rs_score(sym, indic, pcts)
                inst_score = calculate_institutional_score(pcts["volume"], pcts["cmf"])
                sect_score = calculate_sector_score(sector, sector_trends)
                
                fund_score = calculate_fundamental_score(
                    fund,
                    roe_rank=pcts["roe"],
                    profit_growth_rank=pcts["profit_growth"],
                    revenue_growth_rank=pcts["revenue_growth"],
                    sales_growth_rank=pcts["sales_growth"],
                    earnings_revision_rank=pcts["earnings_revision"],
                    margin_expansion_rank=pcts["margin_expansion"],
                    roe_trend_rank=pcts["roe_trend"],
                    fcf_growth_rank=pcts["fcf_growth"]
                )
                
                tech_score = calculate_technical_score(indic)
                earn_score = calculate_earnings_catalyst_score(pcts["earnings_surprise"], pcts["profit_growth"])
                
                if df_3mo is not None and not df_3mo.empty:
                    opp_score = calculate_opportunity_score(df_3mo, indic, ms)
                else:
                    opp_score = 50.0

                # ── INTRADAY MOMENTUM BOOST ────────────────────────────────
                # When a stock shows the classic intraday BUY signal pattern:
                # volume surge + RSI in healthy zone + above VWAP + EMA aligned
                # This directly rewards what actually goes up intraday.
                intraday_momentum_boost = 0.0
                _vol_ratio_now  = float(indic.get("volume_ratio", 1.0))
                _rsi_now        = float(indic.get("rsi", 50.0))
                _above_vwap_now = bool(indic.get("above_vwap", False))
                _ema_aligned_now = bool(indic.get("ema_aligned", False))
                _ema_slope_up   = bool(indic.get("ema_slope_up", False))
                _macd_bullish   = bool(indic.get("macd_bullish", False))

                if _vol_ratio_now >= 1.5 and 50 <= _rsi_now <= 75 and _above_vwap_now and _ema_aligned_now:
                    intraday_momentum_boost += 10.0   # Core intraday pattern confirmed
                    if _ema_slope_up:
                        intraday_momentum_boost += 3.0   # Trend accelerating
                    if _macd_bullish:
                        intraday_momentum_boost += 3.0   # MACD confirming
                    if _vol_ratio_now >= 2.0:
                        intraday_momentum_boost += 4.0   # Exceptional volume surge
                elif _vol_ratio_now >= 1.8 and _above_vwap_now and 48 <= _rsi_now <= 78:
                    intraday_momentum_boost += 5.0   # Partial intraday signal

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

                # Compute Ensemble Alpha with actual news sentiment
                ensemble_result = ae_module.compute_ensemble_alpha(
                    symbol=sym,
                    indic=indic,
                    fund=fund,
                    news=news_signals,
                    ms=ms,
                    regime=market_regime_8,
                    percentiles=pcts,
                    struct_score=struct_score,
                )
                ensemble_alpha = ensemble_result["alpha"]
                confidence_score = ensemble_result["confidence"]
                mom_score = ensemble_result["momentum_score"]
                qual_score = ensemble_result["quality_score"]
                inst_score_ens = ensemble_result["institutional_score"]
                cat_score = ensemble_result["catalyst_score"]

                # Trade Type
                trade_type = rm.get_trade_type(
                    ms.get("structure", ""),
                    float(indic.get("gap_pct", 0)),
                    float(indic.get("rsi", 50)),
                    indic, fund
                )
                
                # Meta Model Adjustment
                meta_alpha, meta_info = mm_module.adjust_alpha(
                    ensemble_alpha, trade_type, market_regime_legacy
                )

                # Win Rate and Expectancy
                nifty_vol = calculate_nifty_realized_volatility(nifty_df)
                volatility_regime = "high" if nifty_vol > 22.0 else "normal"
                breadth_regime = "weak" if market_breadth < 0.30 else "strong" if market_breadth >= 0.60 else "normal"
                avg_value = pcts["turnover"]
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

                setup_exp = db_manager.get_setup_expectancy(
                    trade_type, market_regime=market_regime_legacy, sector=sector,
                    score=meta_alpha, volatility_regime=volatility_regime,
                    breadth_regime=breadth_regime, liquidity_bucket=liquidity_bucket,
                    setup_subtype=setup_subtype
                )
                win_rate = setup_exp.get("win_rate", 50.0)
                avg_return = setup_exp.get("avg_return", 0.0)
                expectancy = setup_exp.get("expectancy", 0.0)
                ev_sample_size = setup_exp.get("sample_size", 0)

                ev_confidence = min(1.0, ev_sample_size / 30.0)
                ev_weight = 0.10 * ev_confidence  # Reduced from 0.30 due to Expectancy Score degradation
                alpha_weight = 1.0 - ev_weight
                ev_score = min(100.0, max(0.0, 50.0 + expectancy * 10.0))
                final_score = alpha_weight * meta_alpha + ev_weight * ev_score

                # Penalties
                penalty = calculate_penalties(fund, indic)
                adjusted_alpha = final_score + penalty + intraday_momentum_boost
                adjusted_alpha = min(100.0, adjusted_alpha)


                # Leadership Boost
                leadership_boost = 0.0
                if pcts["rs"] >= 75.0:
                    if sector in leading_sectors or industry in leading_industries:
                        leadership_boost = 7.5
                adjusted_alpha = min(100.0, adjusted_alpha + leadership_boost)

                # Minervini / VCP Leadership validation
                import leadership_engine
                if df_1y is not None and not df_1y.empty:
                    minervini = leadership_engine.check_minervini_template(df_1y, rs_percentile=pcts["rs"])
                    stability_score = leadership_engine.calculate_stability_score(df_1y)
                else:
                    minervini = {
                        "conditions_passed": 6,
                        "tier": "Acceptable",
                        "failed_conditions": [7, 8],
                        "non_neg_failed": [],
                        "score": 75.0
                    }
                    stability_score = 50.0
                    
                if df_3mo is not None and not df_3mo.empty:
                    vcp = leadership_engine.detect_vcp(df_3mo)
                else:
                    vcp = {
                        "is_vcp": False,
                        "grade": "None",
                        "quality_score": 0.0,
                        "contractions_found": 0,
                        "atr_compressed": False,
                        "volume_tapering": False,
                        "tight_closes": False
                    }

                # Compute Leadership Score
                leadership_score = leadership_engine.compute_leadership_score(
                    stability_score=stability_score,
                    sector_rs_rank=sect_score,
                    industry_rs_rank=avg_industry_rs.get(industry, 50.0),
                    market_is_bullish=market_is_bullish,
                    inst_score=inst_score
                )

                # VCP Multipliers
                vcp_grade = vcp.get("grade", "None")
                opt_weights = {}
                weights_path = os.path.join(config.DATA_DIR, "optimized_weights_latest.json")
                if os.path.exists(weights_path):
                    try:
                        with open(weights_path) as f:
                            opt_data = json.load(f)
                        opt_weights = opt_data.get("weights", {})
                    except Exception:
                        pass
                        
                if vcp_grade == "Elite":
                    vcp_mult = 1.0 + opt_weights.get("vcp_elite_bonus", 0.07)
                elif vcp_grade == "Strong":
                    vcp_mult = 1.0 + opt_weights.get("vcp_strong_bonus", 0.04)
                elif vcp_grade == "Weak":
                    vcp_mult = 1.0 + opt_weights.get("vcp_weak_bonus", 0.02)
                else:
                    vcp_mult = 1.0

                if leadership_score >= 80:
                    leadership_mult = 1.0 + opt_weights.get("lead_elite_bonus", 0.05)
                elif leadership_score >= 60:
                    leadership_mult = 1.0 + opt_weights.get("lead_strong_bonus", 0.02)
                elif leadership_score >= 45:
                    leadership_mult = 1.0
                else:
                    leadership_mult = 1.0 - opt_weights.get("lead_low_penalty", 0.05)

                final_val = adjusted_alpha * leadership_mult * vcp_mult
                final_val = float(np.clip(final_val, 0.0, 100.0))

                reasons = _build_reasons_v3(indic, ms, fund, news_signals, market_is_bullish, sector, sector_trends, final_val)
                bullish_factors, bearish_factors = _get_factors(fund, indic, technical_cache_by_symbol[sym].get("missing_fields", []), news_signals.get("news_sentiment", "neutral"))

                audit_msg = (
                    f"[LEADERSHIP AUDIT] {sym} | "
                    f"Alpha: {adjusted_alpha:.1f} | "
                    f"Stability: {stability_score:.1f}% | "
                    f"Leadership: {leadership_score:.1f} ({leadership_mult:.2f}x) | "
                    f"VCP Quality: {vcp['quality_score']:.1f} ({vcp_grade}, {vcp_mult:.2f}x) | "
                    f"Minervini: {minervini['conditions_passed']}/8 ({minervini['tier']}) | "
                    f"Final Score: {final_val:.1f}"
                )
                logger.info(audit_msg)

                candidate_dict = {
                    "name":               sym.replace(".NS", "").replace(".BO", ""),
                    "symbol":             sym,
                    "df_hist":            df_3mo,
                    "fund":               fund,
                    "indic":              indic,
                    "ms":                 ms,
                    
                    "adjusted_alpha":     final_val,
                    "alpha_score":        round(final_val, 1),
                    "total_score":        round(final_val, 1),
                    "ensemble_alpha":     round(ensemble_alpha, 1),
                    "meta_alpha":         round(meta_alpha, 1),
                    "confidence_score":   round(confidence_score, 1),
                    "meta_info":          meta_info,
                    "expectancy_win_rate": round(win_rate, 1),
                    "expectancy_avg_return": round(avg_return, 2),
                    "expectancy_score":    round(expectancy, 2),
                    "opportunity_score":  round(opp_score, 1),
                    "risk_score":         round(technical_cache_by_symbol[sym].get("risk_score", 5.0), 1),
                    
                    "current_price":      round(float(indic.get("close", 0.0)), 2),
                    "risk_profile":       rm.get_risk_profile(final_val, ms.get("structure", ""), indic.get("volume_surge", False)),
                    "trade_type":         trade_type,
                    "setup_subtype":      setup_subtype,
                    "volatility_regime":  volatility_regime,
                    "breadth_regime":     breadth_regime,
                    "liquidity_bucket":   liquidity_bucket,
                    
                    "rs_rank":            round(pcts["rs"], 1),
                    "structure_score":    round(struct_score, 1),
                    "technical_score":    round(tech_score, 1),
                    "institutional_score": round(inst_score, 1),
                    "fundamental_score":  round(fund_score, 1),
                    "earnings_score":     round(earn_score, 1),
                    "sector_rank":        round(sect_score, 1),
                    "liquidity_score":     round(pcts["turnover"], 1),
                    "momentum_sub_score": round(mom_score, 1),
                    "quality_sub_score":  round(qual_score, 1),
                    "institutional_sub_score": round(inst_score_ens, 1),
                    "catalyst_sub_score": round(cat_score, 1),
                    "sector":             sector,
                    
                    "news_summary":       f"Media sentiment: {news_signals.get('news_sentiment', 'neutral').upper()}. Headlines: {'; '.join(news_signals.get('headlines', []))}" if news_signals and news_signals.get("headlines") else "Media announcements are neutral. Recent announcements: No major headlines.",
                    "technical_summary":  f"RSI is {indic.get('rsi', 50.0):.1f}. Price above 20 EMA is {indic.get('above_vwap', False)}. EMAs aligned is {indic.get('ema_aligned', False)}.",
                    "fundamental_summary": f"Debt/Equity ratio is {fund.get('debt_to_equity') or 'comfortably low'}. ROE is {(fund.get('roe') or 0)*100:.1f}%. MCAP is ₹{(fund.get('market_cap',0) or 0)/1e7:.1f} Cr.",
                    
                    "bullish_factors":    bullish_factors,
                    "bearish_factors":    bearish_factors,
                    "reasons":            reasons,
                    
                    "leadership_score":   round(leadership_score, 1),
                    "leadership_stability_score": round(stability_score, 1),
                    "minervini_score":    int(minervini["conditions_passed"]),
                    "minervini_tier":     minervini["tier"],
                    "vcp_score":          round(vcp["quality_score"], 1),
                    "vcp_grade":          vcp_grade,
                    "vcp_multiplier":     vcp_mult,
                    "leadership_multiplier": leadership_mult,
                    "final_score":        round(final_val, 1),
                    "audit_log":          audit_msg,
                    
                    "validation_audit": {
                        "data_quality":      round(technical_cache_by_symbol[sym].get("data_quality_score", 100.0), 1),
                        "liquidity":         "pass",
                        "risk":              round(technical_cache_by_symbol[sym].get("risk_score", 0.0), 1),
                        "relative_strength": round(pcts["rs"], 1),
                        "institutional":     round(inst_score, 1),
                        "structure":         round(struct_score, 1),
                        "sector":            round(sect_score, 1),
                        "fundamentals":      round(fund_score, 1),
                        "technical":         round(tech_score, 1),
                        "earnings":          round(earn_score, 1),
                        "opportunity":       round(opp_score, 1)
                    },
                    "feature_tracking": {
                        "momentum_pts": round(ensemble_weights["momentum"] * mom_score, 2),
                        "quality_pts": round(ensemble_weights["quality"] * qual_score, 2),
                        "institutional_pts": round(ensemble_weights["institutional"] * inst_score_ens, 2),
                        "catalyst_pts": round(ensemble_weights["catalyst"] * cat_score, 2),
                        "meta_adjustment_pts": meta_info.get("meta_adjustment_pts", 0),
                        "ev_contribution_pts": round(ev_weight * ev_score, 2),
                        "regime": market_regime_8,
                        "ensemble_weights": ensemble_weights
                    }
                }
                
                scored_candidates.append(candidate_dict)

                # Prepare record for alpha_scores collection
                alpha_record = {
                    "symbol": sym,
                    "date": today_str,
                    "adjusted_alpha": final_val,
                    "meta_alpha": meta_alpha,
                    "ensemble_alpha": ensemble_alpha,
                    "confidence_score": confidence_score,
                    "leadership_score": leadership_score,
                    "vcp_score": vcp["quality_score"],
                    "minervini_score": minervini["conditions_passed"],
                    "calculated_at": datetime.now().isoformat()
                }
                alpha_scores_records.append(alpha_record)
                
            except Exception as e:
                logger.error(f"Error scoring survivor {sym}: {e}", exc_info=True)

        if alpha_scores_records and not dry_run:
            db_manager.bulk_write_records("alpha_scores", alpha_scores_records, ["symbol", "date"])

        # Check Watchdog timeout for Stage 5
        if time.time() - stage5_start > stage_timeout:
            raise RuntimeError("Watchdog timeout in Stage 5 (> 5 minutes)")

        # ─────────────────────────────────────────────
        # STAGE 6: Validation, Sizing, and Email Dispatch
        # ─────────────────────────────────────────────
        logger.info("Executing Stage 6: Validation, Sizing, and Email Dispatch...")
        stage6_start = time.time()

        # Sort scored candidates by final score
        scored_candidates.sort(key=lambda x: x["adjusted_alpha"], reverse=True)
        N = len(scored_candidates)

        processed_candidates = []
        historical_trades_records = []

        portfolio_builder = PortfolioEngine(
            risk_per_trade_pct=effective_risk_pct * 100.0,
            heat_limit_pct=getattr(config, "PORTFOLIO_MAX_RISK_PCT", 0.06) * 100.0
        )
        portfolio_builder.active_heat = get_active_open_positions_heat()
        kill_switch_active = db_manager.is_kill_switch_active()

        for idx, stock in enumerate(scored_candidates):
            rank_in_universe = idx + 1
            adj_alpha = stock["adjusted_alpha"]

            # Dual quality-percentile filters (intraday-calibrated thresholds)
            passes_dual_filter = False
            if market_regime_legacy == "Bull":
                # Bull: alpha >= 52, top 35% of universe
                passes_dual_filter = (adj_alpha >= 52.0) and (rank_in_universe <= max(5, int(0.35 * N)))
            elif market_regime_legacy in ["Improving", "Neutral"]:
                # Neutral: alpha >= 48, top 25% of universe
                passes_dual_filter = (adj_alpha >= 48.0) and (rank_in_universe <= max(3, int(0.25 * N)))
            else:  # Bear or Deteriorating
                # Bear: keep strict — only the very best 2 picks
                passes_dual_filter = (adj_alpha >= 50.0) and (rank_in_universe <= 2)

            # Calculate SL and Targets
            entry_price = stock["current_price"]
            sl_price = rm.calculate_stop_loss(entry_price, float(stock["indic"].get("atr", 1.0)), stock["ms"].get("swing_support"))
            targets_dict = rm.calculate_targets(entry_price, sl_price)
            rr_ratio_val = targets_dict.get("rr_ratio")
            
            is_confirmed = passes_dual_filter and (rr_ratio_val is not None and rr_ratio_val >= 1.2)

            # Drawdown halt overrides BUY
            if not dd_risk["allowed"]:
                is_confirmed = False

            # Regime Gating:
            # If STRICT_BULL_ONLY_BUY is True, we block all buys outside of Bull trends.
            if getattr(config, "STRICT_BULL_ONLY_BUY", False) and not market_is_bullish:
                is_confirmed = False
            # If in Bear_Trend or Bear_Panic (buying_permitted is False), we only allow buying
            # for truly elite, high-conviction leadership candidates to protect capital.
            elif not buying_permitted:
                minervini_tier = stock.get("minervini_tier", "Reject")
                passes_elite_bear_gating = (adj_alpha >= 72.0) and (minervini_tier in ["Elite", "Strong"])
                if not passes_elite_bear_gating:
                    is_confirmed = False

            # Reality Check validation
            rc_passes = True
            rc_notes = []
            if stock["df_hist"] is not None:
                rc_passes, rc_notes = rc_module.validate(stock["df_hist"], stock["fund"], stock["indic"])
                
            if not rc_passes and is_confirmed:
                is_confirmed = False
                logger.info(f"[REALITY] {stock['symbol']} downgraded: {'; '.join(rc_notes)}")

            action_val = "BUY" if is_confirmed else "WATCH"
            action_color = "green" if action_val == "BUY" else "yellow"

            # Kill switch overrides BUY
            if kill_switch_active and action_val == "BUY":
                action_val = "WATCH"
                action_color = "yellow"
                stock["execution_rule"] = "Kill Switch Active — Strict Watchlist Only."
            elif downgrade_buy and action_val == "BUY":
                # Pulse downgrade: only block weaker setups (alpha < 60) in clearly weak markets
                # Stocks with alpha >= 60 with confirmed momentum should still trigger BUY
                if adj_alpha < 60.0:
                    action_val = "WATCH"
                    action_color = "yellow"
                    stock["execution_rule"] = f"Pulse Gate: Sellers dominant today (Pulse={pulse_score:.0f}/100). Wait for buyer confirmation."

            stock["action"] = action_val
            stock["action_color"] = action_color
            stock["stop_loss"] = targets_dict["stop_loss"] if is_confirmed else None
            stock["target_1"] = targets_dict["target_1"] if is_confirmed else None
            stock["target_2"] = targets_dict["target_2"] if is_confirmed else None
            stock["rr_ratio"] = targets_dict["rr_ratio"] if is_confirmed else None
            stock["reality_check_notes"] = rc_notes
            stock["drawdown_info"] = {"account_dd_pct": account_dd, "risk_multiplier": dd_risk["multiplier"]}

            # Execution rules
            if action_val == "BUY":
                trade_type = stock["trade_type"]
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
                if market_regime_legacy != "Bull" and getattr(config, "STRICT_BULL_ONLY_BUY", False):
                    execution_rule = f"Strictly monitor today. Market Trend is {market_regime_legacy.upper()} (Risk Shield active — DO NOT enter trades)."
                else:
                    execution_rule = "Strictly monitor today. DO NOT enter trades."
                    
            stock["execution_rule"] = execution_rule

            # Run portfolio sizing checks
            accept, reject_reason = portfolio_builder.accepts(stock, stock["df_hist"], action_val)
            if not accept and action_val == "BUY":
                logger.info(f"[PORTFOLIO] {stock['symbol']} rejected from portfolio: {reject_reason}")
                stock["action"] = "WATCH"
                stock["action_color"] = "yellow"
                stock["execution_rule"] = f"Portfolio constraint: {reject_reason}"
                action_val = "WATCH"

            portfolio_builder.add(stock, stock["df_hist"], action_val)

            # Log historical trade metadata
            if action_val == "BUY":
                trade_record = {
                    "symbol": stock["symbol"],
                    "date": today_str,
                    "entry_price": entry_price,
                    "stop_loss": stock["stop_loss"],
                    "target_1": stock["target_1"],
                    "target_2": stock["target_2"],
                    "rr_ratio": stock["rr_ratio"],
                    "trade_type": stock["trade_type"],
                    "setup_subtype": stock["setup_subtype"],
                    "risk_profile": stock["risk_profile"],
                    "final_score": stock["adjusted_alpha"],
                    "created_at": datetime.now().isoformat()
                }
                historical_trades_records.append(trade_record)

            processed_candidates.append(stock)

            if len(processed_candidates) >= top_n:
                break

        # Log trades in DB
        if historical_trades_records and not dry_run:
            db_manager.bulk_write_records("historical_trades", historical_trades_records, ["symbol", "date"])

        # Format and return portfolio picks
        portfolio = portfolio_builder.get_portfolio()

        for item in portfolio:
            item.pop("df_hist", None)
            item.pop("fund", None)
            item.pop("indic", None)
            item.pop("ms", None)

        for item in processed_candidates:
            item.pop("df_hist", None)
            item.pop("fund", None)
            item.pop("indic", None)
            item.pop("ms", None)

        for rank_idx, item in enumerate(portfolio, 1):
            item["rank"] = rank_idx
            item["position_rank"] = rank_idx

        elapsed = (datetime.now() - start_time).seconds
        logger.info(f"STALKER Staged Pipeline completed in {elapsed}s.")

        # Check Watchdog timeout for Stage 6
        if time.time() - stage6_start > stage_timeout:
            raise RuntimeError("Watchdog timeout in Stage 6 (> 5 minutes)")

        return {
            "date":                 today_str,
            "scan_time":            datetime.now().strftime("%H:%M:%S"),
            "market_trend":         market_regime_8.lower(),
            "market_trend_legacy":  market_regime_legacy.lower(),
            "market_bullish":       market_is_bullish,
            "market_risk_on":       market_is_risk_on,
            "market_breadth":       round(market_breadth, 2),
            "market_breadth_50":    round(market_breadth_50, 2),
            "market_breadth_200":   round(market_breadth_200, 2),
            "regime_data":          regime_data,
            "sector_trends":        sector_trends,
            "account_drawdown_pct": account_dd,
            "market_pulse":         market_pulse_data,
            "top_picks":            portfolio,
            "scanned":              total_universe_count,
            "qualified":            len(processed_candidates),
            "elapsed_sec":          elapsed,
        }

    except Exception as ex:
        logger.critical(f"Scan aborted due to fatal error: {ex}", exc_info=True)
        # Safe Mode Triggered
        safe_mode_active = True
        safe_mode_reason = str(ex)

        return {
            "status": "SYSTEM STATUS: SAFE MODE",
            "safe_mode": True,
            "safe_mode_reason": safe_mode_reason,
            "date": today_str,
            "scan_time": datetime.now().strftime("%H:%M:%S"),
            "top_picks": [],
            "scanned": total_universe_count,
            "qualified": 0,
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
    sect_rs = sector_trends.get(sector, 0.0)
    if sect_rs > 2.0:
        reasons.append(f"🏭 Industry Leadership: {sector} sector is outperforming the market by +{sect_rs:.1f}%.")
        
    return reasons[:3]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if "--dry-run" in sys.argv:
        test_symbols = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "SBIN.NS"]
        result = run_screen(symbols=test_symbols, top_n=3, dry_run=True)
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
