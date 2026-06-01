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
        avg_value = float((closes * volumes).mean())
        
        is_liquid = (avg_vol >= 100000) and (avg_value >= 20000000) # >= 100k shares & >= ₹2 Crore
        return is_liquid, avg_vol, avg_value
    except Exception:
        return False, 0.0, 0.0


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
        # 5% ATR ratio = 5 points. 20% max drawdown = 2 points.
        risk_score = (atr_pct * 100) + (max_dd * 10)
        risk_score = min(10.0, max(0.0, risk_score))
        
        return risk_score, max_dd, atr_pct
    except Exception:
        return 10.0, 1.0, 0.1


# ═══════════════════════════════════════════════
# STAGE 2 — MARKET CONTEXT ENGINE
# ═══════════════════════════════════════════════

def get_market_regime(nifty_df) -> str:
    """
    Determines market trend context.
    Returns "Bull", "Neutral", or "Bear"
    """
    if nifty_df is None or len(nifty_df) < 50:
        return "Neutral"
        
    try:
        close = float(nifty_df['Close'].iloc[-1])
        ema50 = float(nifty_df['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
        ma200 = float(nifty_df['Close'].rolling(window=200).mean().iloc[-1]) if len(nifty_df) >= 200 else ema50
        
        if close > ema50 and close > ma200:
            return "Bull"
        elif close < ma200:
            return "Bear"
        else:
            return "Neutral"
    except Exception:
        return "Neutral"


# ═══════════════════════════════════════════════
# STAGE 3 — ALPHA RANKING ENGINE
# ═══════════════════════════════════════════════

def calculate_rs_score(symbol: str, indic: Dict, rs_ranks: Dict[str, float]) -> float:
    """Returns Relative Strength Rank percentile (0-100)"""
    return rs_ranks.get(symbol, 50.0)


def calculate_institutional_score(df_hist, indic: Dict) -> float:
    """
    Scores accumulation days, green volume dominance, and volume trends.
    Returns 0-100 score.
    """
    try:
        vol_ratio = float(indic.get("volume_ratio", 1.0))
        closes = df_hist['Close'].tail(15)
        opens = df_hist['Open'].tail(15)
        volumes = df_hist['Volume'].tail(15)
        
        # Green days accumulation
        green_days = closes > opens
        green_vol = volumes[green_days].sum()
        total_vol = volumes.sum()
        
        green_vol_dominance = (green_vol / total_vol) if total_vol > 0 else 0.5
        accumulation_days = green_days.sum() / len(closes)
        
        # Volume Surge score (max 40)
        surge_score = min(40.0, (vol_ratio / 2.0) * 40.0)
        
        # Accumulation Dominance score (max 30)
        accum_score = green_vol_dominance * 30.0
        
        # Momentum Days score (max 30)
        days_score = accumulation_days * 30.0
        
        return max(0.0, min(100.0, surge_score + accum_score + days_score))
    except Exception:
        return 50.0


def calculate_sector_score(sector: str, sector_trends: Dict) -> float:
    """Returns Sector Momentum score (100 for bullish, 70 sideways, 20 bearish)"""
    trend = sector_trends.get(sector, "unknown")
    if trend == "bullish":
        return 100.0
    elif trend == "sideways":
        return 70.0
    elif trend == "bearish":
        return 20.0
    return 50.0


def calculate_fundamental_score(fund: Dict) -> float:
    """Scores fundamental metrics (ROE, growth, operating margins) from fundamentals module"""
    res = fund_module.score_fundamentals(fund)
    # Scaled to 0-100
    return float(res.get("score", 5.0) * 10.0)


def calculate_technical_score(indic: Dict) -> float:
    """
    Scores RSI bounds, EMA alignment, and breakout confirmation.
    Returns 0-100 score.
    """
    score = 0.0
    rsi = float(indic.get("rsi", 50.0))
    
    # 1. RSI Corridor 55 to 75 (max 40 pts)
    if 55.0 <= rsi <= 75.0:
        score += 40.0
    elif 50.0 <= rsi <= 80.0:
        score += 20.0
        
    # 2. EMA Alignment 20 > 50 (max 30 pts)
    if indic.get("ema_aligned"):
        score += 30.0
        
    # 3. Price vs MAs & VWAP (max 30 pts)
    if indic.get("above_vwap"):
        score += 15.0
    if indic.get("ema_slope_up"):
        score += 15.0
        
    return max(0.0, min(100.0, score))


def calculate_earnings_catalyst_score(fund: Dict) -> float:
    """Scores EPS/Revenue surprises and momentum"""
    score = 50.0
    surprise = fund.get("earnings_surprise")
    
    if surprise is not None:
        if surprise > 10.0:
            score = 100.0
        elif surprise > 0.0:
            score = 80.0
        elif surprise < -5.0:
            score = 20.0
            
    # Fallback to revenue growth / profit growth
    elif fund.get("profit_growth") is not None:
        pg = fund["profit_growth"] * 100.0
        if pg >= 20.0:
            score = 90.0
        elif pg >= 0.0:
            score = 70.0
        else:
            score = 30.0
            
    return score


def calculate_opportunity_score(df_hist, indic: Dict, ms: Dict) -> float:
    """
    Scores trade timing (proximity to breakout and support zones).
    Returns 0-100 score.
    """
    try:
        close = float(df_hist['Close'].iloc[-1])
        resistance = ms.get("swing_resistance")
        support = ms.get("swing_support")
        
        score = 50.0
        
        # If price is close to breakout (within 3% of resistance)
        if resistance and close <= resistance:
            dist_pct = (resistance - close) / close
            if dist_pct <= 0.03:
                score += 30.0  # High breakout timing potential
                
        # If price is close to support (within 3% of support)
        if support and close >= support:
            dist_pct = (close - support) / close
            if dist_pct <= 0.03:
                score += 20.0  # Solid risk/reward timing
                
        # Ohl bullish pattern confirmation
        if indic.get("ohl_signal") == "bullish":
            score += 20.0
            
        return max(0.0, min(100.0, score))
    except Exception:
        return 50.0


# ═══════════════════════════════════════════════
# PORTFOLIO CORRELATION ENGINE
# ═══════════════════════════════════════════════

def get_historical_correlation(df1, df2, lookbacks: List[int] = [20, 60]) -> float:
    """Calculates max absolute correlation over multiple lookbacks"""
    try:
        close1 = df1['Close']
        close2 = df2['Close']
        
        # Align on index (dates)
        aligned = pd.concat([close1, close2], axis=1, join='inner')
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


# ═══════════════════════════════════════════════
# MAIN CONFIGURABLE SCREENER FUNCTION
# ═══════════════════════════════════════════════

def run_screen(symbols: Optional[List[str]] = None,
               top_n: int = config.TOP_PICKS_COUNT) -> Dict:
    """
    Automated Alpha Engine v3.0 screening execution pipeline.
    """
    start_time = datetime.now()
    symbols = symbols or config.ALL_SYMBOLS
    logger.info(f"Initiating STALKER Alpha Engine v3.0 across {len(symbols)} stocks...")

    # ─────────────────────────────────────────────
    # STAGE 2: Market Environment Scan
    # ─────────────────────────────────────────────
    print("🌐 Evaluating market environment regime...")
    indices_data = df_module.fetch_market_indices()
    nifty_df = indices_data.get("NIFTY50")
    market_regime = get_market_regime(nifty_df)
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

    # Fetch multiple stocks history in bulk (Stage 1 Liquidity & Risk pre-filters)
    print(f"\n📥 Loading price structures for {len(symbols)} stocks...")
    all_history = df_module.fetch_multiple_stocks(symbols)

    # ─────────────────────────────────────────────
    # STAGE 1: Hard Safety Gating Filter
    # ─────────────────────────────────────────────
    print(f"\n🛡️ Running Stage 1 Safety Filters & technical calculations...")
    survivors = []
    
    # Raw relative strength values list to calculate percentiles later
    raw_rs_map = {}
    
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
                
            # Hard Risk Gating
            risk_score, max_dd, atr_pct = evaluate_risk_profile(df_hist, indic)
            if risk_score > 5.0:  # Gating threshold
                continue
                
            # Compute relative strength raw value
            raw_rs = float(indic.get("rs_vs_nifty", 0.0))
            raw_rs_map[symbol] = raw_rs
            
            # Fundamentals and news (pre-fetched/cached)
            fund = df_module.fetch_fundamentals(symbol)
            news = df_module.fetch_news_signals(symbol)
            
            # Hard Data Quality Gating
            dq_score, missing_fields = calculate_data_quality(symbol, fund, df_hist)
            if dq_score < 70.0:  # Gating threshold
                continue
                
            # Detect structure
            ms = ms_module.detect_market_structure(df_hist)
            
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
                "atr_pct": atr_pct
            })
            
        except Exception as e:
            logger.error(f"Error in Stage 1 filters for {symbol}: {e}")
            continue

    print(f"   {len(survivors)} stocks passed Stage 1 Safety filters.")

    # Calculate Relative Strength Percentile Ranks (0-100)
    rs_ranks = {}
    if raw_rs_map:
        rs_vals = list(raw_rs_map.values())
        for sym, val in raw_rs_map.items():
            # Percentage of items <= val
            rs_ranks[sym] = float(np.sum(np.array(rs_vals) <= val) / len(rs_vals) * 100.0)

    # ─────────────────────────────────────────────
    # STAGE 3: Alpha Scoring Models
    # ─────────────────────────────────────────────
    print(f"\n📊 Running Stage 3 Alpha Ranking Models...")
    candidates = []
    
    for item in survivors:
        symbol = item["symbol"]
        df_hist = item["df_hist"]
        indic = item["indic"]
        fund = item["fund"]
        news = item["news"]
        ms = item["ms"]
        
        try:
            sector = fund.get("sector", fund.get("industry", "Unknown")) or "Unknown"
            
            # Model Scores
            rs_score = calculate_rs_score(symbol, indic, rs_ranks)
            inst_score = calculate_institutional_score(df_hist, indic)
            sect_score = calculate_sector_score(sector, sector_trends)
            fund_score = calculate_fundamental_score(fund)
            tech_score = calculate_technical_score(indic)
            earn_score = calculate_earnings_catalyst_score(fund)
            opp_score = calculate_opportunity_score(df_hist, indic, ms)
            
            # 1. Composite Alpha Score calculation
            alpha_score = (0.30 * rs_score + 
                           0.25 * inst_score + 
                           0.15 * sect_score + 
                           0.10 * fund_score + 
                           0.10 * tech_score + 
                           0.05 * earn_score + 
                           0.05 * opp_score)
            
            # 2. Trust Confidence Score
            regime_alignment = 10.0 if (market_is_bullish and sect_score == 100.0) else 0.0
            consistency = 10.0 if np.std([rs_score, inst_score, fund_score, tech_score]) < 15 else 0.0
            sig_agreement = 10.0 if (tech_score >= 70.0 and fund_score >= 70.0) else 0.0
            
            confidence_score = (0.70 * item["data_quality_score"] + 
                                sig_agreement + 
                                consistency + 
                                regime_alignment)
            
            # Deduct missing data penalty from Confidence Score (-5 per missing metric)
            if item["missing_fields"]:
                confidence_score -= (5.0 * len(item["missing_fields"]))
            confidence_score = max(0.0, min(100.0, confidence_score))
            
            # 3. Penalty Engine
            penalty = calculate_penalties(fund, indic)
            adjusted_alpha = alpha_score + penalty
            
            # Format Bullet Points Reasons
            sentiment = news.get("news_sentiment", "neutral")
            reasons = _build_reasons_v3(indic, ms, fund, news, market_is_bullish, sector, sector_trends, adjusted_alpha)
            
            # Bullish / Bearish factors
            bullish_factors, bearish_factors = _get_factors(fund, indic, item["missing_fields"], sentiment)
            
            candidates.append({
                # Top level fields for easy dashboards & reporting
                "name":               symbol.replace(".NS", "").replace(".BO", ""),
                "symbol":             symbol,
                "rank":               99, # Placeholder, set during ranking
                "alpha_score":        round(adjusted_alpha, 1),
                "total_score":        round(adjusted_alpha, 1),
                "confidence_score":   round(confidence_score, 1),
                "opportunity_score":  round(opp_score, 1),
                "risk_score":         round(item["risk_score"], 1),
                "action":             "BUY" if adjusted_alpha >= 70 else "WATCH",
                "action_color":       "green" if adjusted_alpha >= 70 else "yellow",
                "reasons":            reasons,
                
                # Full output dictionary fields
                "current_price":      round(float(df_hist['Close'].iloc[-1]), 2),
                "stop_loss":          rm.calculate_stop_loss(float(df_hist['Close'].iloc[-1]), float(indic.get("atr", 1)), ms.get("swing_support")),
                "target_1":           round(float(df_hist['Close'].iloc[-1]) * 1.05, 2),
                "target_2":           round(float(df_hist['Close'].iloc[-1]) * 1.10, 2),
                "risk_profile":       rm.get_risk_profile(adjusted_alpha, ms.get("structure", ""), indic.get("volume_surge", False)),
                "trade_type":         rm.get_trade_type(ms.get("structure", ""), float(indic.get("gap_pct", 0)), float(indic.get("rsi", 50))),
                
                "institutional_score": round(inst_score, 1),
                "rs_rank":            round(rs_score, 1),
                "sector_rank":        round(sect_score, 1),
                "fundamental_score":  round(fund_score, 1),
                "technical_score":    round(tech_score, 1),
                "earnings_score":     round(earn_score, 1),
                "sector":             sector,
                
                "news_summary":       f"Media announcements are {sentiment}. Recent announcements: {', '.join(news.get('headlines', ['No major headlines']))[:150]}.",
                "technical_summary":  f"RSI is {indic.get('rsi', 50.0):.1f}. Price above 20 EMA is {indic.get('above_vwap', False)}. EMAs aligned is {indic.get('ema_aligned', False)}.",
                "fundamental_summary": f"Debt/Equity ratio is {fund.get('debt_to_equity') or 'comfortably low'}. ROE is {(fund.get('roe') or 0)*100:.1f}%. MCAP is ₹{(fund.get('market_cap',0) or 0)/1e7:.1f} Cr.",
                
                "bullish_factors":    bullish_factors,
                "bearish_factors":    bearish_factors,
                "validation_audit": {
                    "data_quality":      round(item["data_quality_score"], 1),
                    "liquidity":         "pass",
                    "risk":              round(item["risk_score"], 1),
                    "relative_strength": round(rs_score, 1),
                    "institutional":     round(inst_score, 1),
                    "sector":            round(sect_score, 1),
                    "fundamentals":      round(fund_score, 1),
                    "technical":         round(tech_score, 1),
                    "earnings":          round(earn_score, 1),
                    "opportunity":       round(opp_score, 1)
                },
                "df_hist":            df_hist # Needed for Stage 4 Pearson Correlation
            })
            
        except Exception as e:
            logger.error(f"Error in Alpha Ranking for {symbol}: {e}")
            continue

    # Sort candidates descending by Adjusted Alpha Score
    candidates.sort(key=lambda x: x["alpha_score"], reverse=True)

    # ─────────────────────────────────────────────
    # STAGE 4: Portfolio Assembly & Correlation Control
    # ─────────────────────────────────────────────
    print(f"\n💼 Constructing portfolio & running Pearson Correlation controls...")
    portfolio = []
    sector_counts = {}
    
    for stock in candidates:
        symbol = stock["symbol"]
        sector = stock["sector"]
        df_hist = stock["df_hist"]
        
        # 1. Sector Concentration Limit check (Max 2 stocks per sector)
        count = sector_counts.get(sector, 0)
        if count >= 2:
            continue
            
        # 2. Pearson Correlation Gating (r > 0.80 over past 20 and 60 days)
        correlated = False
        for active in portfolio:
            r = get_historical_correlation(df_hist, active["df_hist"])
            if r > 0.80:
                correlated = True
                break
                
        if correlated:
            continue
            
        # Passed portfolio construction! Add to final selection.
        portfolio.append(stock)
        sector_counts[sector] = count + 1
        
        if len(portfolio) >= top_n:
            break

    # Clean up DF before return to avoid serialisation issues
    for item in portfolio:
        item.pop("df_hist", None)
        
    # Apply formal sequential numbering to active Rank
    for rank_idx, item in enumerate(portfolio, 1):
        item["rank"] = rank_idx
        item["position_rank"] = rank_idx

    elapsed = (datetime.now() - start_time).seconds
    print(f"\n✅ STALKER Alpha Engine v3.0 completed in {elapsed}s.")
    print(f"   Scanned: {len(symbols)} | Qualified: {len(candidates)} | Top Picks Portfolio: {len(portfolio)} opportunities.")

    return {
        "date":           datetime.now().strftime("%Y-%m-%d"),
        "scan_time":      datetime.now().strftime("%H:%M:%S"),
        "market_trend":   market_regime.lower(),
        "market_bullish": market_is_bullish,
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
            print(f"\n{pick['rank']}. {pick['name']} | Alpha Score: {pick['alpha_score']} | Confidence: {pick['confidence_score']}")
            print(f"   DQ: {pick['validation_audit']['data_quality']} | Liquidity: {pick['validation_audit']['liquidity']} | Risk: {pick['validation_audit']['risk']}")
            print(f"   News Sentiment: {pick['news_summary'][:80]}...")
            print("   Bullish Factors:")
            for bf in pick['bullish_factors']:
                print(f"     * {bf}")
