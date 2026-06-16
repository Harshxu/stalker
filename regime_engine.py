"""
STALKER — Regime Engine
Classifies the market into one of 8 adaptive regimes using:
  - Nifty price vs EMA50 / EMA200
  - Market breadth (% stocks above 50-EMA and 200-EMA)
  - Advance/Decline ratio
  - Realized volatility (20d annualized)
  - EMA20 slope direction

8 Regimes:
  Bull_Trend       → Broad participation, price above both MAs
  Bull_Expansion   → Bull + accelerating breadth
  Bull_Exhaustion  → Price above MA200 but breadth deteriorating
  Neutral_Rotation → Between MAs, sectors churning
  Neutral_Compression → Low volatility, tight breadth range
  Bear_Trend       → Price below MA200, weak breadth
  Bear_Panic       → Bear + volatility spike + AD collapse
  Bear_Recovery    → Bear conditions but breadth/AD improving
"""

import logging
import numpy as np
from typing import Tuple, Optional
import pandas as pd

import config

logger = logging.getLogger(__name__)


def get_realized_volatility(nifty_df: pd.DataFrame, window: int = 20) -> float:
    """Returns annualized 20-day realized volatility of Nifty as a percentage."""
    try:
        if nifty_df is None or len(nifty_df) < window + 1:
            return 18.0  # Neutral baseline
        returns = nifty_df["Close"].pct_change().tail(window)
        return float(returns.std() * np.sqrt(252) * 100.0)
    except Exception:
        return 18.0


def get_ema20_slope(nifty_df: pd.DataFrame) -> float:
    """Returns the 3-day slope of the Nifty EMA20 as a % change."""
    try:
        if nifty_df is None or len(nifty_df) < 23:
            return 0.0
        ema20 = nifty_df["Close"].ewm(span=20, adjust=False).mean()
        slope = (ema20.iloc[-1] - ema20.iloc[-3]) / ema20.iloc[-3] * 100.0
        return float(slope)
    except Exception:
        return 0.0


def get_ad_ratio(all_history: dict) -> float:
    """
    Calculates the Advance/Decline ratio across the stock universe.
    Returns a float: > 1.0 means more advances than declines.
    """
    try:
        advances = 0
        declines = 0
        for sym, df in all_history.items():
            if df is not None and len(df) >= 2:
                last_close = float(df["Close"].iloc[-1])
                prev_close = float(df["Close"].iloc[-2])
                if last_close > prev_close:
                    advances += 1
                elif last_close < prev_close:
                    declines += 1
        if declines == 0:
            return 2.0 if advances > 0 else 1.0
        return round(advances / declines, 2)
    except Exception:
        return 1.0


def classify_regime(
    nifty_df: pd.DataFrame,
    breadth_50: float,
    breadth_200: float,
    prev_breadth_50: float = 0.5,
    all_history: Optional[dict] = None,
    ad_ratio: Optional[float] = None,
) -> Tuple[str, bool, dict]:
    """
    Classifies the current market into one of 8 adaptive regimes.

    Args:
        nifty_df: Daily Nifty OHLCV DataFrame
        breadth_50: Fraction of universe stocks trading > 50 EMA today
        breadth_200: Fraction of universe stocks trading > 200 EMA today
        prev_breadth_50: Fraction above 50 EMA on the previous day
        all_history: Dict of {symbol: df} for AD ratio calculation
        ad_ratio: Optional precalculated advance/decline ratio

    Returns:
        (regime_name: str, is_risk_on: bool, regime_data: dict)
    """
    try:
        if nifty_df is None or len(nifty_df) < 50:
            return "Neutral_Compression", False, {}

        close = float(nifty_df["Close"].iloc[-1])
        ema50 = float(nifty_df["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(nifty_df["Close"].rolling(window=200).mean().iloc[-1]) if len(nifty_df) >= 200 else ema50
        realized_vol = get_realized_volatility(nifty_df)
        ema20_slope = get_ema20_slope(nifty_df)
        
        if ad_ratio is None:
            ad_ratio = get_ad_ratio(all_history) if all_history else 1.0

        above_ema50 = close > ema50
        above_ema200 = close > ema200
        breadth_rising = breadth_50 > prev_breadth_50
        broad_participation = breadth_50 >= 0.60  # >60% above 50 EMA
        weak_breadth = breadth_50 < 0.40
        vol_elevated = realized_vol > 22.0  # >22% annualized = elevated
        vol_very_high = realized_vol > 35.0  # >35% = panic zone

        regime_data = {
            "close": close,
            "ema50": round(ema50, 2),
            "ema200": round(ema200, 2),
            "breadth_50": round(breadth_50, 3),
            "breadth_200": round(breadth_200, 3),
            "realized_vol": round(realized_vol, 1),
            "ema20_slope": round(ema20_slope, 3),
            "ad_ratio": round(ad_ratio, 2),
        }

        # ── BULL REGIMES ──
        if above_ema50 and above_ema200:
            if vol_elevated and weak_breadth:
                # Price still above MAs but participation is collapsing — exhaustion
                regime = "Bull_Exhaustion"
                is_risk_on = False
            elif broad_participation and breadth_rising and ema20_slope > 0.05:
                # Strong trend with accelerating breadth — expansion
                regime = "Bull_Expansion"
                is_risk_on = True
            else:
                # Solid bull trend
                regime = "Bull_Trend"
                is_risk_on = True

        # ── BEAR REGIMES ──
        elif not above_ema200:
            if vol_very_high and ad_ratio < 0.5:
                # Full capitulation — panic conditions
                regime = "Bear_Panic"
                is_risk_on = False
            elif breadth_rising and ad_ratio > 1.2 and ema20_slope > 0.0:
                # Still in bear territory but breadth/AD recovering
                regime = "Bear_Recovery"
                is_risk_on = False  # Cautiously risk-off until confirmed
            else:
                regime = "Bear_Trend"
                is_risk_on = False

        # ── NEUTRAL REGIMES (between EMA50 and EMA200) ──
        else:
            if not vol_elevated and abs(ema20_slope) < 0.05 and not breadth_rising:
                # Very tight, low-volatility range — compression before breakout
                regime = "Neutral_Compression"
                is_risk_on = False
            else:
                # Sector rotation, mixed signals
                regime = "Neutral_Rotation"
                is_risk_on = False

        logger.info(
            f"[REGIME] {regime} | Breadth50: {breadth_50:.1%} | "
            f"AD: {ad_ratio:.2f} | Vol: {realized_vol:.1f}% | Slope: {ema20_slope:+.3f}%"
        )
        return regime, is_risk_on, regime_data

    except Exception as e:
        logger.error(f"[REGIME] Error classifying regime: {e}")
        return "Neutral_Rotation", False, {}


def get_legacy_regime(regime_8: str) -> str:
    """Maps 8-state regime to legacy 3-state for backward compatibility."""
    return config.REGIME_LEGACY_MAP.get(regime_8, "Neutral")


def is_buying_permitted(regime_8: str) -> bool:
    """
    Returns True if the regime permits active buying (BUY actions).
    In Bear_Trend and Bear_Panic, new buys are suppressed.
    """
    return regime_8 not in ("Bear_Trend", "Bear_Panic")


def get_ensemble_weights(regime_8: str) -> dict:
    """
    Returns the ensemble sub-model weights for the given 8-state regime.
    Falls back to Neutral_Rotation weights if not found.
    """
    return config.ENSEMBLE_WEIGHTS.get(regime_8, config.ENSEMBLE_WEIGHTS["Neutral_Rotation"])
