"""
STALKER - Fundamentals Scorer
Checks company health: debt, promoter holding, FII activity, P/E, earnings.
Returns a 0–10 score used in the master scoring engine.
"""

from typing import Dict
import config


def score_fundamentals(fund: Dict) -> Dict:
    """
    Score a company's fundamentals out of 10.
    Returns score + individual sub-scores + readable flags.
    """
    score  = 0.0
    flags  = []
    alerts = []

    # ── Price Filter (Hard Rule) ──────────────────────
    current_price = fund.get("current_price") or 0
    if current_price and (current_price > config.MAX_STOCK_PRICE or current_price < config.MIN_STOCK_PRICE):
        return {
            "score": 0,
            "disqualified": True,
            "reason": f"Price ₹{current_price:.0f} outside range ₹{config.MIN_STOCK_PRICE}–₹{config.MAX_STOCK_PRICE}",
            "flags": [],
            "alerts": ["Price out of range"],
        }

    # ── Market Cap (min ₹5,000 Cr) ───────────────────
    market_cap = fund.get("market_cap", 0) or 0
    if market_cap > 0:
        market_cap_cr = market_cap / 1e7   # Convert to Crores
        if market_cap_cr >= config.MIN_MARKET_CAP:
            score += 2.0
            flags.append("Large & liquid company")
        elif market_cap_cr >= 1000:
            score += 1.0
            flags.append("Mid-cap company")
        else:
            alerts.append("Small company — higher risk")

    # ── Debt to Equity ───────────────────────────────
    de = fund.get("debt_to_equity")
    if de is not None:
        if de <= 0.3:
            score += 2.5
            flags.append("Very low debt")
        elif de <= 0.8:
            score += 2.0
            flags.append("Manageable debt")
        elif de <= config.MAX_DEBT_TO_EQUITY:
            score += 1.0
        else:
            score -= 1.0
            alerts.append("High debt levels")
    else:
        score += 1.0   # Neutral when data unavailable

    # ── Return on Equity (ROE) ───────────────────────
    roe = fund.get("roe")
    if roe is not None:
        roe_pct = roe * 100
        if roe_pct >= 20:
            score += 2.0
            flags.append("High profitability (ROE > 20%)")
        elif roe_pct >= 12:
            score += 1.5
            flags.append("Good profitability")
        elif roe_pct >= 0:
            score += 0.5
        else:
            alerts.append("Company losing money on equity")

    # ── Revenue/Earnings Growth ──────────────────────
    rev_growth = fund.get("revenue_growth")
    profit_growth = fund.get("profit_growth")

    if profit_growth is not None:
        pg = profit_growth * 100
        if pg >= 20:
            score += 2.0
            flags.append(f"Strong profit growth ({pg:.1f}%)")
        elif pg >= 10:
            score += 1.0
            flags.append(f"Growing profits ({pg:.1f}%)")
        elif pg < 0:
            alerts.append("Declining profits")

    elif rev_growth is not None:
        rg = rev_growth * 100
        if rg >= 15:
            score += 1.0
            flags.append(f"Revenue growing ({rg:.1f}%)")

    # ── Recent Earnings Beat ─────────────────────────
    if fund.get("has_recent_earnings"):
        surprise = fund.get("earnings_surprise")
        if surprise and surprise > 5:
            score += 1.5
            flags.append(f"Recent earnings beat (+{surprise:.1f}% surprise)")
        elif surprise and surprise > 0:
            score += 0.5
            flags.append("Recent earnings on track")
        elif surprise and surprise < -5:
            alerts.append("Recent earnings miss")
            score -= 0.5

    # ── P/E Ratio Check ─────────────────────────────
    pe = fund.get("pe_ratio")
    if pe is not None and pe > 0:
        if pe < 15:
            flags.append("Value stock (low P/E)")
            score += 0.5
        elif pe > 80:
            alerts.append("Expensive valuation (very high P/E)")

    # Cap the score at 10
    score = max(0, min(10, score))

    return {
        "score":         round(score, 2),
        "disqualified":  False,
        "reason":        None,
        "flags":         flags,        # Positive signals
        "alerts":        alerts,       # Warning signals
        "market_cap_cr": round((fund.get("market_cap", 0) or 0) / 1e7, 0),
        "de_ratio":      de,
        "roe_pct":       round((fund.get("roe") or 0) * 100, 1),
        "pe_ratio":      pe,
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
