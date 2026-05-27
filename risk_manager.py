"""
STALKER - Risk Manager
Calculates stop loss, target, position size, and risk/reward ratio.
Implements the anti-loss logic from PROMPT.txt.
Result shown to user as plain numbers — no jargon.
"""

import math
from typing import Dict, Optional
import config


def calculate_stop_loss(current_price: float, atr: float,
                         swing_support: Optional[float] = None) -> float:
    """
    Stop loss = best of:
    - 1.5x ATR below current price
    - Just below recent swing low (if available)
    """
    atr_stop = current_price - (config.STOP_LOSS_ATR_MULT * atr)

    if swing_support and swing_support < current_price:
        swing_stop = swing_support * 0.99   # 1% below swing low
        # Use whichever stop is tighter but still below current price
        stop = max(atr_stop, swing_stop)
    else:
        stop = atr_stop

    return round(max(stop, current_price * 0.90), 2)   # Never more than 10% loss


def calculate_targets(entry: float, stop_loss: float) -> Dict:
    """
    Calculate targets based on 1:1.5 and 1:2 R/R ratios.
    """
    risk = entry - stop_loss
    if risk <= 0:
        risk = entry * 0.02   # Default 2% risk

    target_1 = round(entry + risk * 1.5, 2)   # Conservative (1:1.5)
    target_2 = round(entry + risk * 2.0, 2)   # Ideal (1:2)
    target_3 = round(entry + risk * 3.0, 2)   # Stretch goal (1:3)

    rr_ratio = risk and round((target_2 - entry) / risk, 2)

    return {
        "entry":     round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "target_1":  target_1,
        "target_2":  target_2,
        "target_3":  target_3,
        "risk_per_share": round(risk, 2),
        "rr_ratio":  rr_ratio,
    }


def calculate_position_size(capital: float, entry: float, stop_loss: float,
                              risk_pct: float = config.MAX_CAPITAL_RISK_PCT) -> Dict:
    """
    Position sizing: risk only 1–2% of capital per trade.
    Returns number of shares and total capital required.
    """
    risk_amount  = capital * risk_pct
    risk_per_share = entry - stop_loss

    if risk_per_share <= 0:
        return {"shares": 0, "capital_needed": 0, "risk_amount": 0}

    shares        = math.floor(risk_amount / risk_per_share)
    capital_needed = shares * entry

    return {
        "shares":         shares,
        "capital_needed": round(capital_needed, 2),
        "risk_amount":    round(risk_amount, 2),
        "risk_pct":       risk_pct * 100,
    }


def is_risk_reward_acceptable(entry: float, stop_loss: float, target: float) -> bool:
    """Check if R/R meets minimum requirement."""
    risk   = entry - stop_loss
    reward = target - entry
    if risk <= 0:
        return False
    return (reward / risk) >= config.MIN_RISK_REWARD


def get_risk_profile(score: float, ms_structure: str, volume_surge: bool) -> str:
    """
    Returns a plain-English risk label for the end user.
    Score 75+ = Low Risk, 55–74 = Medium Risk, <55 = High Risk
    """
    if score >= 75 and ms_structure in ["uptrend", "breakout"] and volume_surge:
        return "Low Risk"
    elif score >= 55:
        return "Medium Risk"
    else:
        return "High Risk"


def get_trade_type(ms_structure: str, gap_pct: float, rsi: float) -> str:
    """
    Suggest trade type in plain English for the end user.
    """
    if gap_pct >= config.GAP_UP_THRESHOLD and ms_structure in ["uptrend", "breakout"]:
        return "Gap & Go"
    elif ms_structure == "breakout":
        return "Breakout Trade"
    elif ms_structure == "uptrend" and rsi < 60:
        return "Trend Continuation"
    elif ms_structure == "uptrend":
        return "Momentum Trade"
    else:
        return "Watchlist Only"


def check_daily_loss_limit(trade_history: list) -> Dict:
    """
    Check if daily loss limit has been hit.
    Returns whether trading should stop.
    """
    if not trade_history:
        return {"stop_trading": False, "reason": None, "consecutive_losses": 0}

    today_trades = [t for t in trade_history if t.get("date") == str(__import__("datetime").date.today())]

    # Count consecutive losses
    consecutive_losses = 0
    for trade in reversed(today_trades):
        if trade.get("result", "").lower() == "loss":
            consecutive_losses += 1
        else:
            break

    # Calculate total daily P&L
    daily_pnl_pct = sum(t.get("pnl_pct", 0) for t in today_trades)

    stop = False
    reason = None

    if consecutive_losses >= 2:
        stop = True
        reason = f"⚠️ 2 consecutive losses today. Take a break — protect your capital."
    elif daily_pnl_pct <= -config.DAILY_LOSS_LIMIT_PCT * 100:
        stop = True
        reason = f"⚠️ Daily loss limit reached ({daily_pnl_pct:.1f}%). Stop trading today."

    return {
        "stop_trading":       stop,
        "reason":             reason,
        "consecutive_losses": consecutive_losses,
        "daily_pnl_pct":      round(daily_pnl_pct, 2),
    }
