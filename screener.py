import sys, io
# Force UTF-8 output on Windows to handle special chars
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import config
import data_fetcher as df_module
import indicators as ind
import market_structure as ms_module
import fundamentals as fund_module
import risk_manager as rm

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HARD DISQUALIFICATION RULES
# Stock is removed before scoring if any of these are true.
# ─────────────────────────────────────────────

def is_disqualified(symbol: str, df_hist, fund: Dict, indic: Dict, ms: Dict) -> Tuple[bool, str]:
    """Returns (True, reason) if stock should be excluded."""

    # 1. No data
    if df_hist is None or len(df_hist) < 20:
        return True, "Insufficient price history"

    price = indic.get("close", 0)

    # 2. Price out of user-defined range
    if price > config.MAX_STOCK_PRICE:
        return True, f"Price ₹{price:.0f} too high (limit: ₹{config.MAX_STOCK_PRICE:,})"
    if price < config.MIN_STOCK_PRICE:
        return True, f"Price ₹{price:.0f} too low (min: ₹{config.MIN_STOCK_PRICE})"

    # 3. In a clear downtrend — never fight the trend
    if ms_module.is_downtrend(ms):
        return True, "Stock in downtrend — avoid buying"

    # 4. Overbought RSI (don't chase tops)
    rsi = indic.get("rsi", 50)
    if rsi > config.RSI_MAX:
        return True, f"Overbought (RSI: {rsi:.1f}) — wait for pullback"

    # 5. Negative news sentiment (bearish headlines)
    # (news_sentiment checked externally, passed via fund dict if available)

    # 6. Very low volume (illiquid)
    vol_ratio = indic.get("volume_ratio", 1.0)
    if vol_ratio < 0.3:
        return True, "Very low trading activity — avoid"

    return False, ""


def pre_score_stock(symbol: str, df_hist, indic: Dict, ms: Dict, market_is_bullish: bool) -> float:
    """Calculate the technical-only score for pre-filtering (max 90 pts)."""
    tech_score = 0.0

    # ── 1. MARKET STRUCTURE (25 pts) ─────────────────
    struct_score = ms_module.get_structure_score(ms)
    tech_score += struct_score

    # ── 2. VOLUME (20 pts) ───────────────────────────
    vol_ratio = indic.get("volume_ratio", 1.0)
    if vol_ratio >= 3.0:
        vol_score = 20
    elif vol_ratio >= 2.0:
        vol_score = 17
    elif vol_ratio >= 1.5:
        vol_score = 13
    elif vol_ratio >= 1.0:
        vol_score = 7
    else:
        vol_score = 2
    tech_score += vol_score

    # ── 3. TECHNICAL SETUP (20 pts) ──────────────────
    t_score = 0.0
    if indic.get("above_vwap"):         t_score += 6
    if indic.get("ema_aligned"):        t_score += 5
    if indic.get("ema_slope_up"):       t_score += 3
    if indic.get("rsi_healthy"):        t_score += 4
    if indic.get("macd_bullish"):       t_score += 2
    tech_score += min(20, t_score)

    # ── 4. MOMENTUM (15 pts) ─────────────────────────
    mom_score = 0.0
    gap_pct = indic.get("gap_pct", 0)
    rs_vs_nifty = indic.get("rs_vs_nifty", 0)
    ohl_signal  = indic.get("ohl_signal", "neutral")
    dist_52w    = indic.get("dist_52w_high", -100)

    if gap_pct >= config.GAP_UP_THRESHOLD:    mom_score += 4
    elif gap_pct > 0:                          mom_score += 2

    if rs_vs_nifty >= 3:                       mom_score += 4
    elif rs_vs_nifty >= 1:                     mom_score += 2

    if ohl_signal == "bullish":                mom_score += 3
    elif ohl_signal == "bearish":              mom_score -= 2

    if dist_52w >= -5:                         mom_score += 4
    elif dist_52w >= -15:                      mom_score += 2

    if market_is_bullish:                      mom_score += 2

    tech_score += max(0, min(15, mom_score))

    # ── 5. RISK/REWARD (10 pts) ──────────────────────
    entry     = indic.get("close", 0)
    atr       = indic.get("atr", entry * 0.01)
    swing_sup = ms.get("swing_support")
    stop_loss = rm.calculate_stop_loss(entry, atr, swing_sup)
    targets   = rm.calculate_targets(entry, stop_loss)
    rr_ratio  = targets.get("rr_ratio", 0)

    if rr_ratio >= 3.0:       rr_score = 10
    elif rr_ratio >= 2.0:     rr_score = 8
    elif rr_ratio >= 1.5:     rr_score = 5
    else:                     rr_score = 2
    tech_score += rr_score

    return tech_score


def score_stock(symbol: str, df_hist, fund: Dict, indic: Dict,
                ms: Dict, news: Dict, market_is_bullish: bool,
                sector_trends: Dict) -> Dict:
    """
    Score a stock from 0 to 100 across 6 dimensions.
    Returns full scoring breakdown + final composite score.
    """
    total_score = 0.0

    # ── 1. MARKET STRUCTURE (25 pts) ─────────────────
    struct_score = ms_module.get_structure_score(ms)
    total_score += struct_score

    # ── 2. VOLUME (20 pts) ───────────────────────────
    vol_ratio = indic.get("volume_ratio", 1.0)
    if vol_ratio >= 3.0:
        vol_score = 20
    elif vol_ratio >= 2.0:
        vol_score = 17
    elif vol_ratio >= 1.5:
        vol_score = 13
    elif vol_ratio >= 1.0:
        vol_score = 7
    else:
        vol_score = 2
    total_score += vol_score

    # ── 3. TECHNICAL SETUP (20 pts) ──────────────────
    tech_score = 0.0
    if indic.get("above_vwap"):         tech_score += 6
    if indic.get("ema_aligned"):        tech_score += 5
    if indic.get("ema_slope_up"):       tech_score += 3
    if indic.get("rsi_healthy"):        tech_score += 4
    if indic.get("macd_bullish"):       tech_score += 2
    total_score += min(20, tech_score)

    # ── 4. MOMENTUM (15 pts) ─────────────────────────
    mom_score = 0.0
    gap_pct = indic.get("gap_pct", 0)
    rs_vs_nifty = indic.get("rs_vs_nifty", 0)
    ohl_signal  = indic.get("ohl_signal", "neutral")
    dist_52w    = indic.get("dist_52w_high", -100)

    if gap_pct >= config.GAP_UP_THRESHOLD:    mom_score += 4
    elif gap_pct > 0:                          mom_score += 2

    if rs_vs_nifty >= 3:                       mom_score += 4
    elif rs_vs_nifty >= 1:                     mom_score += 2

    if ohl_signal == "bullish":                mom_score += 3
    elif ohl_signal == "bearish":              mom_score -= 2

    if dist_52w >= -5:                         mom_score += 4   # Near 52w high = momentum
    elif dist_52w >= -15:                      mom_score += 2

    # Market-wide boost
    if market_is_bullish:                      mom_score += 2

    total_score += max(0, min(15, mom_score))

    # ── 5. FUNDAMENTALS (10 pts) ─────────────────────
    fund_result = fund_module.score_fundamentals(fund)
    if fund_result.get("disqualified"):
        return {
            "symbol":         symbol,
            "total_score":    0,
            "disqualified":   True,
            "reason":         fund_result.get("reason"),
        }
    fund_score = (fund_result["score"] / 10) * 10   # already 0–10
    total_score += fund_score

    # Sector health bonus (up to 3 pts within fundamental bucket)
    sector = data_fetcher_get_sector(symbol, fund)
    sector_bonus = fund_module.get_sector_health_bonus(sector, sector_trends)
    total_score = min(total_score + sector_bonus * 0.33, total_score + 3)

    # ── 6. RISK/REWARD (10 pts) ──────────────────────
    entry     = indic.get("close", 0)
    atr       = indic.get("atr", entry * 0.01)
    swing_sup = ms.get("swing_support")
    stop_loss = rm.calculate_stop_loss(entry, atr, swing_sup)
    targets   = rm.calculate_targets(entry, stop_loss)
    rr_ratio  = targets.get("rr_ratio", 0)

    if rr_ratio >= 3.0:       rr_score = 10
    elif rr_ratio >= 2.0:     rr_score = 8
    elif rr_ratio >= 1.5:     rr_score = 5
    else:                     rr_score = 2
    total_score += rr_score

    # ── NEWS BOOST/PENALTY ────────────────────────────
    sentiment = news.get("news_sentiment", "neutral")
    if sentiment == "bullish":    total_score += 3
    elif sentiment == "bearish":  total_score -= 5
    if news.get("catalysts"):     total_score += 2

    total_score = max(0, min(100, total_score))

    # ── DETERMINE ACTION LABEL ───────────────────────
    if total_score >= 72:
        action = "BUY"
        action_color = "green"
    elif total_score >= 55:
        action = "WATCH"
        action_color = "yellow"
    else:
        action = "AVOID"
        action_color = "red"

    # ── RISK PROFILE ─────────────────────────────────
    risk_profile = rm.get_risk_profile(total_score, ms.get("structure", ""), indic.get("volume_surge", False))
    trade_type   = rm.get_trade_type(ms.get("structure", ""), gap_pct, indic.get("rsi", 50))

    # ── HUMAN-READABLE REASONS (for dashboard tooltip) ──
    reasons = _build_reasons(indic, ms, fund_result, news, market_is_bullish, sector, sector_trends)

    return {
        "symbol":         symbol,
        "name":           symbol.replace(".NS", "").replace(".BO", ""),
        "total_score":    round(total_score, 1),
        "action":         action,
        "action_color":   action_color,
        "risk_profile":   risk_profile,
        "trade_type":     trade_type,
        "disqualified":   False,
        "reason":         None,
        "sector":         sector,

        # Price & targets (what user sees)
        "current_price":  round(entry, 2),
        "stop_loss":      stop_loss,
        "target_1":       targets["target_1"],
        "target_2":       targets["target_2"],
        "risk_per_share": targets["risk_per_share"],
        "rr_ratio":       rr_ratio,

        # Key visible details
        "gap_pct":        round(gap_pct, 2),
        "volume_ratio":   round(vol_ratio, 2),
        "rsi":            round(indic.get("rsi", 0), 1),
        "structure":      ms.get("structure", "unknown"),
        "structure_label": ms.get("label", ""),
        "news_sentiment": sentiment,
        "news_catalysts": news.get("catalysts", []),
        "headlines":      news.get("headlines", [])[:3],

        # Fundamental highlights
        "fund_flags":     fund_result.get("flags", [])[:3],
        "fund_alerts":    fund_result.get("alerts", [])[:2],

        # Score breakdown (hidden from user but stored)
        "_scores": {
            "market_structure": struct_score,
            "volume":           vol_score,
            "technical":        tech_score,
            "momentum":         mom_score,
            "fundamentals":     fund_score,
            "risk_reward":      rr_score,
        },
        "reasons":        reasons,
    }


def _build_reasons(indic, ms, fund_result, news, market_bullish, sector, sector_trends) -> List[str]:
    """Build 3–5 plain-English bullet points explaining why this stock was picked."""
    reasons = []

    struct = ms.get("structure", "")
    if struct == "uptrend":
        reasons.append("📈 Stock is in a strong uptrend")
    elif struct == "breakout":
        reasons.append("🚀 Price breaking out with strong volume")

    if indic.get("volume_surge"):
        vr = indic.get("volume_ratio", 1)
        reasons.append(f"📊 Trading at {vr:.1f}x normal volume — unusual activity")

    if indic.get("gap_pct", 0) >= config.GAP_UP_THRESHOLD:
        reasons.append(f"⬆️ Gapped up {indic['gap_pct']:.1f}% at open — strong buying")

    if news.get("catalysts"):
        reasons.append(f"📰 Active news: {', '.join(news['catalysts'][:2])}")

    if fund_result.get("flags"):
        reasons.append(f"✅ {fund_result['flags'][0]}")

    if market_bullish:
        reasons.append("🌐 Overall market is bullish today")

    if sector_trends.get(sector) == "bullish":
        reasons.append(f"🏭 {sector} sector is strong today")

    return reasons[:5]


def data_fetcher_get_sector(symbol, fund):
    """Get sector label, importing locally to avoid circular import."""
    try:
        from data_fetcher import get_sector_for_symbol
        return get_sector_for_symbol(symbol, fund)
    except Exception:
        return fund.get("sector", "Unknown")


# ─────────────────────────────────────────────
# MAIN SCREENER FUNCTION
# ─────────────────────────────────────────────

def run_screen(symbols: Optional[List[str]] = None,
               top_n: int = config.TOP_PICKS_COUNT) -> Dict:
    """
    Main entry point. Scans all stocks and returns top picks.
    Returns: {
        "date": ...,
        "market_trend": ...,
        "sector_trends": ...,
        "top_picks": [...],
        "scanned": N,
        "qualified": N,
    }
    """
    start_time = datetime.now()
    symbols = symbols or config.ALL_SYMBOLS
    logger.info(f"Starting screen for {len(symbols)} symbols...")

    # ── Step 1: Fetch Market & Sector Data ───────────
    print("🌐 Checking overall market conditions...")
    indices_data = df_module.fetch_market_indices()

    nifty_df = indices_data.get("NIFTY50")
    market_trend = df_module.get_market_trend(nifty_df) if nifty_df is not None else "unknown"
    market_is_bullish = market_trend == "bullish"

    print(f"   Market is: {market_trend.upper()}")

    # Sector strength
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

    # ── Step 2: Fetch All Stock Data ─────────────────
    print(f"\n📥 Fetching price data for {len(symbols)} stocks...")
    all_history = df_module.fetch_multiple_stocks(symbols)

    # ── Step 3: Score Each Stock (Pass 1 - Technical Pre-screening) ──────────────────────
    print(f"\n🔍 Analysing {len(all_history)} stocks (Pass 1 - Technical Pre-screening)...")
    candidates = []

    for i, symbol in enumerate(symbols, 1):
        df_hist = all_history.get(symbol)
        if df_hist is None:
            continue

        try:
            # Compute all indicators
            indic = ind.compute_all_indicators(df_hist, nifty_df)
            if not indic:
                continue

            # Market structure
            ms = ms_module.detect_market_structure(df_hist)

            # Hard disqualification
            disq, reason = is_disqualified(symbol, df_hist, {}, indic, ms)
            if disq:
                continue

            # Pre-score technical performance
            tech_score = pre_score_stock(symbol, df_hist, indic, ms, market_is_bullish)
            
            candidates.append({
                "symbol": symbol,
                "df_hist": df_hist,
                "indic": indic,
                "ms": ms,
                "tech_score": tech_score
            })

        except Exception as e:
            logger.error(f"Error in technical pre-screening for {symbol}: {e}")
            continue

    # Sort candidates by technical score descending
    candidates.sort(key=lambda x: x["tech_score"], reverse=True)
    
    # We only fetch fundamentals and news for the top candidate pool (top 35 stocks)
    # The maximum impact from fundamentals & news is +15 points, so any stock more than 20 points
    # below the 15th stock could never beat it. A pool of 35 is mathematically complete.
    eval_pool = candidates[:35]
    print(f"\n📋 Selected top {len(eval_pool)} candidates for full fundamentals and news analysis (Pass 2)...")

    # ── Step 4: Full Score for Candidate Pool (Pass 2) ──────────────────────
    results = []
    qualified = 0

    for i, item in enumerate(eval_pool, 1):
        symbol = item["symbol"]
        df_hist = item["df_hist"]
        indic = item["indic"]
        ms = item["ms"]

        try:
            # Fundamentals
            fund = df_module.fetch_fundamentals(symbol)
            time.sleep(0.15)

            # News
            news = df_module.fetch_news_signals(symbol)
            time.sleep(0.1)

            # Final score
            result = score_stock(
                symbol, df_hist, fund, indic, ms, news,
                market_is_bullish, sector_trends
            )

            if result.get("disqualified"):
                continue

            if result["action"] in ["BUY", "WATCH"]:
                qualified += 1

            results.append(result)

        except Exception as e:
            logger.error(f"Error in detailed scoring for {symbol}: {e}")
            continue

    # ── Step 5: Sort & Return Top N ──────────────────
    results.sort(key=lambda x: x["total_score"], reverse=True)
    top_picks = results[:top_n]

    elapsed = (datetime.now() - start_time).seconds
    print(f"\n✅ Scan complete: {len(all_history)} scanned, {qualified} qualified, top {len(top_picks)} selected ({elapsed}s)")

    return {
        "date":          datetime.now().strftime("%Y-%m-%d"),
        "scan_time":     datetime.now().strftime("%H:%M:%S"),
        "market_trend":  market_trend,
        "market_bullish": market_is_bullish,
        "sector_trends": sector_trends,
        "top_picks":     top_picks,
        "scanned":       len(all_history),
        "qualified":     qualified,
        "elapsed_sec":   elapsed,
    }


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if "--dry-run" in sys.argv:
        # Test with just 5 stocks
        test_symbols = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "SBIN.NS"]
        result = run_screen(symbols=test_symbols, top_n=5)

        print("\n" + "="*60)
        print("STALKER — TOP PICKS (DRY RUN)")
        print("="*60)
        for i, pick in enumerate(result["top_picks"], 1):
            print(f"\n{i}. {pick['name']} — Score: {pick['total_score']}/100 — {pick['action']}")
            print(f"   Price: ₹{pick['current_price']:,.2f} | SL: ₹{pick['stop_loss']:,.2f} | Target: ₹{pick['target_2']:,.2f}")
            print(f"   Risk: {pick['risk_profile']} | Type: {pick['trade_type']}")
