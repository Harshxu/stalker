"""
STALKER - Fundamentals Scorer
Checks company health: debt, promoter holding, FII activity, P/E, earnings.
Returns a 0–10 score used in the master scoring engine.
"""

from typing import Dict
import config


import numpy as np
from typing import Dict, List
import config


def compute_growth_trend_metric(y: List[float]) -> float:
    """
    Computes a linear regression trend metric that rewards:
    - Magnitude: Regression slope normalized by mean absolute value of the series.
    - Consistency: Pearson correlation coefficient between quarters and values.
    - Acceleration: Regression slope fitted to quarter-on-quarter changes.
    """
    if len(y) < 2:
        return 0.0
    try:
        y_arr = np.array(y, dtype=float)
        mean_val = np.mean(np.abs(y_arr))
        if mean_val == 0:
            mean_val = 1e-10
            
        x = np.arange(len(y_arr))
        slope, _ = np.polyfit(x, y_arr, 1)
        relative_slope = slope / mean_val
        
        # Consistency (r between time index and values)
        if len(y_arr) >= 3:
            r = np.corrcoef(x, y_arr)[0, 1]
            consistency = r if not np.isnan(r) else 0.0
        else:
            consistency = 1.0 if y_arr[-1] > y_arr[0] else -1.0 if y_arr[-1] < y_arr[0] else 0.0
            
        # Acceleration (slope of quarterly changes)
        if len(y_arr) >= 3:
            diffs = np.diff(y_arr)
            acc_slope, _ = np.polyfit(np.arange(len(diffs)), diffs, 1)
            acceleration = acc_slope / mean_val
        else:
            acceleration = 0.0
            
        # Composite score
        return float(0.5 * relative_slope + 0.3 * consistency + 0.2 * acceleration)
    except Exception:
        return 0.0


def score_fundamentals(fund: Dict, roe_rank: float = 50.0, profit_growth_rank: float = 50.0,
                       revenue_growth_rank: float = 50.0, sales_growth_rank: float = 50.0,
                       earnings_revision_rank: float = 50.0, margin_expansion_rank: float = 50.0,
                       roe_trend_rank: float = 50.0) -> Dict:
    """
    Score a company's fundamentals out of 10 using continuous percentile ranks.
    Combines Static Health (70%) and dynamic trend regression (30%).
    """
    current_price = fund.get("current_price") or 0
    if current_price and (current_price > getattr(config, "MAX_STOCK_PRICE", 10000) or current_price < getattr(config, "MIN_STOCK_PRICE", 50)):
        return {
            "score": 0,
            "disqualified": True,
            "reason": f"Price ₹{current_price:.0f} outside range ₹{config.MIN_STOCK_PRICE}–₹{config.MAX_STOCK_PRICE}",
            "flags": [],
            "alerts": ["Price out of range"],
        }

    market_cap = fund.get("market_cap", 0) or 0
    market_cap_cr = market_cap / 1e7
    if market_cap_cr < getattr(config, "MIN_MARKET_CAP", 5000):
        return {
            "score": 0,
            "disqualified": True,
            "reason": f"Market Cap ₹{market_cap_cr:.0f} Cr is below minimum ₹{config.MIN_MARKET_CAP} Cr",
            "flags": [],
            "alerts": ["Market Cap too low"],
        }

    # ── Static Health Score (Max 7.0) ─────────────────
    # Average of roe_rank, profit_growth_rank, and revenue_growth_rank
    static_avg = (roe_rank + profit_growth_rank + revenue_growth_rank) / 3.0
    static_score = (static_avg / 100.0) * 7.0

    # ── Dynamic Trend Score (Max 3.0) ──────────────────
    # Average of sales_growth_rank, margin_expansion_rank, earnings_revision_rank, and roe_trend_rank
    trend_avg = (sales_growth_rank + margin_expansion_rank + earnings_revision_rank + roe_trend_rank) / 4.0
    trend_score = (trend_avg / 100.0) * 3.0

    # ── Leverage Debt Penalty ─────────────────────────
    de = fund.get("debt_to_equity")
    de_penalty = 0.0
    flags = []
    alerts = []

    if de is not None:
        if de > getattr(config, "MAX_DEBT_TO_EQUITY", 1.5):
            de_penalty = 1.5
            alerts.append(f"High debt levels (D/E: {de:.2f})")
        elif de <= 0.3:
            flags.append("Very low debt")
        else:
            flags.append("Manageable debt")

    if roe_rank >= 70.0:
        flags.append("Top-tier profitability")
    if profit_growth_rank >= 70.0:
        flags.append("Top-tier growth")

    total_score = max(0.0, min(10.0, static_score + trend_score - de_penalty))

    return {
        "score":         round(total_score, 2),
        "disqualified":  False,
        "reason":        None,
        "flags":         flags,        # Positive signals
        "alerts":        alerts,       # Warning signals
        "market_cap_cr": round(market_cap_cr, 0),
        "de_ratio":      de,
        "roe_pct":       round((fund.get("roe") or 0) * 100, 1),
        "pe_ratio":      fund.get("pe_ratio"),
    }


def get_sector_health_bonus(sector: str, sector_trends: Dict) -> float:
    """
    Returns 0–3 bonus points if the stock's sector is currently strong.
    """
    trend = sector_trends.get(sector, "unknown")
    if trend == "bullish":
        return 3.0
    elif trend == "sideways":
        return 1.0
    return 0.0
