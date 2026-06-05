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
    - 2.0x ATR below current price (wider to survive intraday noise)
    - Just below recent swing low (if available)
    Pick the WIDER (lower) stop to avoid noise triggers.
    """
    atr_stop = current_price - (config.STOP_LOSS_ATR_MULT * atr)

    if swing_support and swing_support < current_price:
        swing_stop = swing_support * 0.99   # 1% below swing low
        # Use whichever stop is WIDER (lower) to survive normal volatility
        stop = min(atr_stop, swing_stop)
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
                              risk_pct: float = getattr(config, "RISK_PER_TRADE_PCT", 0.02)) -> Dict:
    """
    Volatility Adjusted Position Sizing: 
    Position Size = Account Risk / ATR Risk (which is represented by Entry - Stop Loss).
    High volatility stocks automatically get smaller allocation.
    """
    risk_amount  = capital * risk_pct
    risk_per_share = entry - stop_loss

    if risk_per_share <= 0:
        return {"shares": 0, "capital_needed": 0, "risk_amount": 0}

    # Volatility adjustment: wider stop loss (due to higher ATR) results in fewer shares
    shares        = math.floor(risk_amount / risk_per_share)
    capital_needed = shares * entry

    return {
        "shares":         shares,
        "capital_needed": round(capital_needed, 2),
        "risk_amount":    round(risk_amount, 2),
        "risk_pct":       risk_pct * 100,
        "volatility_adjusted": True
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


def get_trade_type(ms_structure: str, gap_pct: float, rsi: float, indic: dict = None, fund: dict = None) -> str:
    """
    Suggest setup type in plain English for the end user and tracking database.
    Setups: BREAKOUT, PULLBACK, MOMENTUM, VALUE_MOMENTUM, EARNINGS_RUNNER
    """
    indic = indic or {}
    fund = fund or {}
    try:
        dist_ema20 = float(indic.get("dist_from_ema20", 0.0))
        vol_ratio = float(indic.get("volume_ratio", 1.0))
        ema_aligned = indic.get("ema_aligned", False)
        
        # Check Earnings Runner first
        earn_surprise = fund.get("earnings_surprise") or 0.0
        profit_growth = fund.get("profit_growth") or 0.0
        if (earn_surprise > 5.0 or profit_growth > 0.20) and ema_aligned:
            return "EARNINGS_RUNNER"
            
        # Check Pullback
        # Close near 20 EMA (within -4% to +0.8%) under low volume
        if ema_aligned and (-4.0 <= dist_ema20 <= 0.8) and vol_ratio < 1.1:
            return "PULLBACK"
            
        # Check Breakout
        if ms_structure == "breakout" or (indic.get("volume_surge") and ms_structure == "uptrend"):
            return "BREAKOUT"
            
        # Check Value Momentum
        pe = fund.get("pe_ratio")
        roe = fund.get("roe") or 0.0
        if pe is not None and 0 < pe < 25 and roe > 0.15 and ema_aligned:
            return "VALUE_MOMENTUM"
            
        # Check Momentum
        if ema_aligned and rsi > 58:
            return "MOMENTUM"
            
        return "MOMENTUM" if ema_aligned else "WATCHLIST_ONLY"
    except Exception:
        return "MOMENTUM"


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


# ─────────────────────────────────────────────
# DRAWDOWN-AWARE POSITION SIZING (Phase 1 Elite)
# ─────────────────────────────────────────────

def get_account_drawdown(lookback_days: int = 30) -> float:
    """
    Synthesizes an equity curve from the last N days of resolved picks
    in db_manager and returns the current drawdown from peak (as a positive %).

    Returns 0.0 if insufficient data.
    """
    try:
        import db_manager
        from datetime import date, timedelta

        min_date = str(date.today() - timedelta(days=lookback_days))
        records = db_manager.get_recent_picks(lookback_days)

        # Flatten all resolved picks into a time-ordered return series
        flat_returns = []
        for r in sorted(records, key=lambda x: x.get("date", "")):
            picks = r.get("picks", r.get("top_picks", []))
            rec_date = r.get("date", "")
            for p in picks:
                ret = p.get("future_5d_return") if p.get("future_5d_return") is not None \
                    else p.get("future_3d_return")
                if ret is not None and p.get("action") == "BUY":
                    flat_returns.append(float(ret))

        if len(flat_returns) < 5:
            return 0.0

        import numpy as np
        # Build equity curve: compound growth from 100
        equity = 100.0
        peak = 100.0
        curve = []
        for r in flat_returns:
            equity *= (1.0 + r / 100.0)
            peak = max(peak, equity)
            curve.append(equity)

        current_dd = (peak - equity) / peak * 100.0
        return round(max(0.0, current_dd), 2)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[RISK] Could not compute account drawdown: {e}")
        return 0.0


def get_drawdown_adjusted_risk(
    base_risk_pct: float = None,
    account_dd_pct: float = None,
) -> Dict:
    """
    Applies the drawdown-aware sizing tier to the base risk per trade.

    Tiers (from config.DRAWDOWN_SIZING_TIERS):
      DD 0-5%    → full base risk (1.0x multiplier)
      DD 5-10%   → 0.5x
      DD 10-15%  → 0.25x
      DD >15%    → 0.0x (stop trading)

    Returns:
        {
          "allowed": bool,
          "risk_pct": float (adjusted risk per trade as a decimal, e.g. 0.01),
          "multiplier": float,
          "account_dd_pct": float,
          "reason": str,
        }
    """
    if base_risk_pct is None:
        base_risk_pct = getattr(config, "RISK_PER_TRADE_PCT", 0.02)

    if account_dd_pct is None:
        account_dd_pct = get_account_drawdown()

    tiers = getattr(config, "DRAWDOWN_SIZING_TIERS", [
        {"max_dd_pct": 5.0,  "risk_multiplier": 1.0},
        {"max_dd_pct": 10.0, "risk_multiplier": 0.5},
        {"max_dd_pct": 15.0, "risk_multiplier": 0.25},
        {"max_dd_pct": 999,  "risk_multiplier": 0.0},
    ])

    multiplier = 1.0
    reason = "Normal sizing"

    for tier in sorted(tiers, key=lambda t: t["max_dd_pct"]):
        if account_dd_pct <= tier["max_dd_pct"]:
            multiplier = tier["risk_multiplier"]
            if multiplier == 0.0:
                reason = f"DRAWDOWN HALT: Account DD {account_dd_pct:.1f}% exceeds 15% threshold."
            elif multiplier < 1.0:
                reason = f"Reduced sizing: Account DD {account_dd_pct:.1f}% → {multiplier:.0%} of base risk."
            break

    adjusted_risk = base_risk_pct * multiplier
    allowed = multiplier > 0.0

    return {
        "allowed": allowed,
        "risk_pct": round(adjusted_risk, 4),
        "multiplier": multiplier,
        "account_dd_pct": account_dd_pct,
        "reason": reason,
    }

