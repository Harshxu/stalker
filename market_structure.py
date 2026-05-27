"""
STALKER - Market Structure Detector
Identifies Uptrend, Downtrend, Sideways, and Breakout patterns.
Uses swing highs/lows (Higher Highs, Higher Lows, Lower Highs, Lower Lows).
"""

import numpy as np
import pandas as pd
from typing import List, Tuple, Dict
import config


def find_swing_highs(df: pd.DataFrame, lookback: int = 5) -> List[Tuple[int, float]]:
    """Find local swing highs (peaks)."""
    highs = []
    prices = df["High"].values
    for i in range(lookback, len(prices) - lookback):
        window = prices[i - lookback: i + lookback + 1]
        if prices[i] == max(window):
            highs.append((i, prices[i]))
    return highs


def find_swing_lows(df: pd.DataFrame, lookback: int = 5) -> List[Tuple[int, float]]:
    """Find local swing lows (troughs)."""
    lows = []
    prices = df["Low"].values
    for i in range(lookback, len(prices) - lookback):
        window = prices[i - lookback: i + lookback + 1]
        if prices[i] == min(window):
            lows.append((i, prices[i]))
    return lows


def detect_market_structure(df: pd.DataFrame) -> Dict:
    """
    Classify market into: uptrend, downtrend, sideways, or breakout.
    Returns a dict with structure label, strength, and key levels.
    """
    if df is None or len(df) < 30:
        return {
            "structure": "unknown",
            "strength": 0,
            "support": None,
            "resistance": None,
            "trend_description": "Insufficient data",
        }

    lookback = config.SWING_LOOKBACK
    swing_highs = find_swing_highs(df, lookback)
    swing_lows  = find_swing_lows(df, lookback)

    structure = "sideways"
    strength  = 0
    hh_count  = 0   # Higher Highs
    hl_count  = 0   # Higher Lows
    lh_count  = 0   # Lower Highs
    ll_count  = 0   # Lower Lows

    # Analyze swing highs for HH/LH
    if len(swing_highs) >= 2:
        recent_highs = [sh[1] for sh in swing_highs[-4:]]
        for i in range(1, len(recent_highs)):
            move = (recent_highs[i] - recent_highs[i-1]) / recent_highs[i-1]
            if move > config.MIN_SWING_MOVE:
                hh_count += 1
            elif move < -config.MIN_SWING_MOVE:
                lh_count += 1

    # Analyze swing lows for HL/LL
    if len(swing_lows) >= 2:
        recent_lows = [sl[1] for sl in swing_lows[-4:]]
        for i in range(1, len(recent_lows)):
            move = (recent_lows[i] - recent_lows[i-1]) / recent_lows[i-1]
            if move > config.MIN_SWING_MOVE:
                hl_count += 1
            elif move < -config.MIN_SWING_MOVE:
                ll_count += 1

    # Classify structure
    if hh_count >= 1 and hl_count >= 1:
        structure = "uptrend"
        strength  = min(100, (hh_count + hl_count) * 25)
    elif lh_count >= 1 and ll_count >= 1:
        structure = "downtrend"
        strength  = min(100, (lh_count + ll_count) * 25)
    else:
        structure = "sideways"
        strength  = 30

    # Check for breakout from sideways
    if structure == "sideways" and len(df) >= 20:
        recent_range_high = df["High"].iloc[-20:-2].max()
        recent_range_low  = df["Low"].iloc[-20:-2].min()
        current_close     = df["Close"].iloc[-1]
        current_volume    = df["Volume"].iloc[-1]
        avg_volume        = df["Volume"].iloc[-20:-2].mean()

        if current_close > recent_range_high * 1.005 and current_volume > avg_volume * 1.5:
            structure = "breakout"
            strength  = 80

    # Get key support and resistance levels
    support    = float(df["Low"].iloc[-20:].min()) if len(df) >= 20 else float(df["Low"].iloc[-5:].min())
    resistance = float(df["High"].iloc[-20:].max()) if len(df) >= 20 else float(df["High"].iloc[-5:].max())

    # Recent swing support (last confirmed swing low)
    swing_support = None
    if swing_lows:
        swing_support = swing_lows[-1][1]

    structure_labels = {
        "uptrend":   "📈 Uptrend",
        "downtrend": "📉 Downtrend",
        "sideways":  "↔️ Sideways",
        "breakout":  "🚀 Breakout",
        "unknown":   "❓ Unknown",
    }

    trend_descriptions = {
        "uptrend":   "Stock making higher highs and higher lows — best for buying",
        "downtrend": "Stock making lower highs and lower lows — avoid buying",
        "sideways":  "Stock moving in a range — wait for breakout",
        "breakout":  "Price breaking out of range with volume — strong opportunity",
        "unknown":   "Not enough data to determine trend",
    }

    return {
        "structure":         structure,
        "label":             structure_labels.get(structure, structure),
        "strength":          strength,
        "support":           support,
        "resistance":        resistance,
        "swing_support":     swing_support,
        "hh_count":          hh_count,
        "hl_count":          hl_count,
        "lh_count":          lh_count,
        "ll_count":          ll_count,
        "trend_description": trend_descriptions.get(structure, ""),
    }


def is_uptrend(ms: Dict) -> bool:
    return ms.get("structure") in ["uptrend", "breakout"]


def is_downtrend(ms: Dict) -> bool:
    return ms.get("structure") == "downtrend"


def get_structure_score(ms: Dict) -> float:
    """
    Returns 0–25 score based on market structure quality.
    Used in the scoring engine.
    """
    structure = ms.get("structure", "unknown")
    strength  = ms.get("strength", 0)

    base_scores = {
        "uptrend":   20,
        "breakout":  25,
        "sideways":  5,
        "downtrend": 0,
        "unknown":   0,
    }

    base = base_scores.get(structure, 0)
    # Add up to 5 bonus for strong trend strength
    bonus = (strength / 100) * 5 if structure in ["uptrend", "breakout"] else 0
    return min(25, base + bonus)
