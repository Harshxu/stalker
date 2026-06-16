"""
STALKER — Market Pulse Engine
Quantifies real buyer vs seller balance each morning using 5 independent signals.

Signals:
  1. India VIX         → Fear gauge (high = sellers panicking)
  2. Advance/Decline   → How many stocks up vs down in the universe today
  3. Buying Pressure   → (Close - Low) / (High - Low) aggregate (1.0 = pure buyers)
  4. CMF Aggregate     → Chaikin Money Flow net across universe (positive = inflow)
  5. Volume Surge      → Today's volume vs 20-day average (>1 = participation growing)

Output:
  pulse_score : 0-100  (>60 = buyers in control, <40 = sellers in control)
  pulse_label : "BUYERS_STRONG" | "BUYERS_SLIGHT" | "NEUTRAL" | "SELLERS_SLIGHT" | "SELLERS_STRONG"
  signal_dict : individual signal values for dashboard

Used as an additional CONFIRMATION gate — does not block picks,
but downgrades BUY → WATCH when sellers are clearly dominant (score < 35).
"""

import logging
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────
VIX_LOW    = 13.0   # VIX below this = calm market, buyers confident
VIX_HIGH   = 20.0   # VIX above this = fear, sellers active
VIX_PANIC  = 25.0   # VIX above this = panic selling

PULSE_DOWNGRADE_THRESHOLD = 35.0   # Below this → downgrade BUY to WATCH


# ── Signal 1: India VIX ──────────────────────────────────────

def get_india_vix() -> Tuple[float, float]:
    """
    Fetches India VIX from yfinance (^INDIAVIX).
    Returns (vix_value, vix_score_0_to_100).
    Score: 100 = very low fear (buyers confident), 0 = extreme panic.
    """
    try:
        import yfinance as yf
        df = yf.download("^INDIAVIX", period="5d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return 15.0, 50.0

        vix = float(df["Close"].dropna().iloc[-1])

        # Score: inverse of VIX — low VIX = high buyer confidence
        if vix <= VIX_LOW:
            score = 85.0
        elif vix <= VIX_HIGH:
            # Linear interpolation from 85 → 50 between VIX_LOW and VIX_HIGH
            score = 85.0 - (vix - VIX_LOW) / (VIX_HIGH - VIX_LOW) * 35.0
        elif vix <= VIX_PANIC:
            # Linear from 50 → 15 between VIX_HIGH and VIX_PANIC
            score = 50.0 - (vix - VIX_HIGH) / (VIX_PANIC - VIX_HIGH) * 35.0
        else:
            score = max(0.0, 15.0 - (vix - VIX_PANIC) * 2.0)

        return round(vix, 2), round(score, 1)

    except Exception as e:
        logger.warning(f"[PULSE] VIX fetch failed: {e}")
        return 15.0, 50.0


# ── Signal 2: Advance / Decline ──────────────────────────────

def compute_advance_decline(all_history: Dict[str, pd.DataFrame]) -> Tuple[int, int, float]:
    """
    Counts how many stocks in the universe closed UP vs DOWN today.
    Returns (advances, declines, ad_score_0_to_100).
    Score: 100 = all stocks up, 0 = all stocks down, 50 = balanced.
    """
    advances = 0
    declines = 0

    for sym, df in all_history.items():
        if df is None or len(df) < 2:
            continue
        try:
            prev_close = float(df["Close"].iloc[-2])
            today_close = float(df["Close"].iloc[-1])
            if prev_close <= 0:
                continue
            if today_close > prev_close:
                advances += 1
            elif today_close < prev_close:
                declines += 1
        except Exception:
            continue

    total = advances + declines
    if total == 0:
        return 0, 0, 50.0

    ratio = advances / total  # 0 to 1
    score = ratio * 100.0     # 0 to 100

    return advances, declines, round(score, 1)


# ── Signal 3: Aggregate Buying Pressure ──────────────────────

def compute_buying_pressure(all_history: Dict[str, pd.DataFrame]) -> float:
    """
    Measures where price closed within its daily range across the universe.
    Formula: (Close - Low) / (High - Low) per stock, then averaged.
    - 1.0 = closed at the high (pure buyer dominance)
    - 0.5 = closed exactly in the middle (balanced)
    - 0.0 = closed at the low (pure seller dominance)
    Returns score 0-100.
    """
    pressures = []

    for sym, df in all_history.items():
        if df is None or len(df) < 1:
            continue
        try:
            row = df.iloc[-1]
            high = float(row["High"])
            low  = float(row["Low"])
            close = float(row["Close"])
            range_size = high - low
            if range_size < 0.01:
                continue
            bp = (close - low) / range_size
            pressures.append(bp)
        except Exception:
            continue

    if not pressures:
        return 50.0

    avg_bp = float(np.mean(pressures))
    score = avg_bp * 100.0  # 0 to 100

    return round(score, 1)


# ── Signal 4: Aggregate CMF ──────────────────────────────────

def compute_aggregate_cmf(all_history: Dict[str, pd.DataFrame], period: int = 20) -> float:
    """
    Computes average Chaikin Money Flow across the universe.
    CMF > 0 = net money inflow (buyers), < 0 = net outflow (sellers).
    Maps to 0-100 score: 50 = neutral, 100 = max inflow, 0 = max outflow.
    """
    cmf_values = []

    for sym, df in all_history.items():
        if df is None or len(df) < period + 1:
            continue
        try:
            recent = df.tail(period).copy()
            high   = recent["High"].astype(float)
            low    = recent["Low"].astype(float)
            close  = recent["Close"].astype(float)
            volume = recent["Volume"].astype(float)

            hl_range = (high - low).replace(0, np.nan)
            mf_multiplier = ((close - low) - (high - close)) / hl_range
            mf_volume = mf_multiplier * volume

            cmf = mf_volume.sum() / volume.sum() if volume.sum() > 0 else 0.0
            cmf_values.append(float(cmf))
        except Exception:
            continue

    if not cmf_values:
        return 50.0

    avg_cmf = float(np.mean(cmf_values))
    # Map CMF (-1 to +1) → score (0 to 100), clamped
    score = (avg_cmf + 1.0) / 2.0 * 100.0
    score = max(0.0, min(100.0, score))

    return round(score, 1)


# ── Signal 5: Volume Surge ───────────────────────────────────

def compute_volume_surge(all_history: Dict[str, pd.DataFrame], lookback: int = 20) -> float:
    """
    Measures if today's volume is above the 20-day average across the universe.
    Vol ratio > 1.0 = more participation than usual (conviction behind the move).
    Returns score 0-100: 50 = normal volume, 100 = 2x normal, 0 = 50% of normal.
    """
    ratios = []

    for sym, df in all_history.items():
        if df is None or len(df) < lookback + 1:
            continue
        try:
            avg_vol = float(df["Volume"].iloc[-(lookback + 1):-1].mean())
            today_vol = float(df["Volume"].iloc[-1])
            if avg_vol <= 0:
                continue
            ratio = today_vol / avg_vol
            ratios.append(ratio)
        except Exception:
            continue

    if not ratios:
        return 50.0

    avg_ratio = float(np.mean(ratios))
    # Map: ratio 0.5 → score 25, ratio 1.0 → score 50, ratio 2.0 → score 100
    score = min(100.0, avg_ratio * 50.0)
    return round(score, 1)


# ── Composite Pulse Score ────────────────────────────────────

PULSE_WEIGHTS = {
    "vix":      0.25,   # Fear gauge (most important — market-wide)
    "ad_ratio": 0.30,   # Who's winning: buyers or sellers (breadth of move)
    "bp":       0.20,   # Where did price close in its range
    "cmf":      0.15,   # Money flow direction
    "volume":   0.10,   # Conviction (volume confirmation)
}


def compute_pulse(
    all_history: Dict[str, pd.DataFrame],
    vix_override: Optional[float] = None,
) -> Dict:
    """
    Computes the full Market Pulse — buyer vs seller balance.

    Args:
        all_history: Dict of {symbol: OHLCV DataFrame}
        vix_override: If provided, uses this VIX value instead of fetching

    Returns dict with:
        pulse_score    : 0-100 composite score
        pulse_label    : human-readable label
        vix            : India VIX value
        advances       : stocks up today
        declines       : stocks down today
        ad_score       : advance/decline score (0-100)
        buying_pressure: avg (close-low)/(high-low) score (0-100)
        cmf_score      : aggregate CMF score (0-100)
        volume_score   : volume surge score (0-100)
        downgrade_buy  : True if pulse is weak enough to downgrade BUY→WATCH
    """
    # --- Signal 1: VIX ---
    if vix_override is not None:
        vix_val = vix_override
        # Re-score from override value
        if vix_val <= VIX_LOW:
            vix_score = 85.0
        elif vix_val <= VIX_HIGH:
            vix_score = 85.0 - (vix_val - VIX_LOW) / (VIX_HIGH - VIX_LOW) * 35.0
        elif vix_val <= VIX_PANIC:
            vix_score = 50.0 - (vix_val - VIX_HIGH) / (VIX_PANIC - VIX_HIGH) * 35.0
        else:
            vix_score = max(0.0, 15.0 - (vix_val - VIX_PANIC) * 2.0)
    else:
        vix_val, vix_score = get_india_vix()

    # --- Signal 2: Advance / Decline ---
    advances, declines, ad_score = compute_advance_decline(all_history)

    # --- Signal 3: Buying Pressure ---
    bp_score = compute_buying_pressure(all_history)

    # --- Signal 4: CMF ---
    cmf_score = compute_aggregate_cmf(all_history)

    # --- Signal 5: Volume ---
    vol_score = compute_volume_surge(all_history)

    # --- Composite ---
    w = PULSE_WEIGHTS
    pulse_score = (
        w["vix"]      * vix_score +
        w["ad_ratio"] * ad_score  +
        w["bp"]       * bp_score  +
        w["cmf"]      * cmf_score +
        w["volume"]   * vol_score
    )
    pulse_score = round(min(100.0, max(0.0, pulse_score)), 1)

    # --- Label ---
    if pulse_score >= 65:
        label = "BUYERS_STRONG"
        emoji = "🟢"
    elif pulse_score >= 55:
        label = "BUYERS_SLIGHT"
        emoji = "🟡"
    elif pulse_score >= 45:
        label = "NEUTRAL"
        emoji = "⚪"
    elif pulse_score >= 35:
        label = "SELLERS_SLIGHT"
        emoji = "🟠"
    else:
        label = "SELLERS_STRONG"
        emoji = "🔴"

    downgrade = pulse_score < PULSE_DOWNGRADE_THRESHOLD

    result = {
        "pulse_score":      pulse_score,
        "pulse_label":      label,
        "pulse_emoji":      emoji,
        "vix":              vix_val,
        "vix_score":        round(vix_score, 1),
        "advances":         advances,
        "declines":         declines,
        "ad_ratio":         round(advances / max(1, advances + declines), 3),
        "ad_score":         ad_score,
        "buying_pressure":  bp_score,
        "cmf_score":        cmf_score,
        "volume_score":     vol_score,
        "downgrade_buy":    downgrade,
        "interpretation":   _interpret(pulse_score, vix_val, advances, declines, bp_score),
    }

    logger.info(
        f"[PULSE] Score={pulse_score} ({label}) | VIX={vix_val:.1f} | "
        f"A/D={advances}/{declines} | BP={bp_score:.0f} | CMF={cmf_score:.0f} | Vol={vol_score:.0f}"
    )

    return result


def compute_pulse_from_indicators(
    indicators_by_symbol: Dict[str, Dict],
    vix_override: Optional[float] = None,
) -> Dict:
    """
    Computes the Market Pulse using precalculated indicators from the stock universe.
    This avoids re-fetching OHLCV DataFrames and runs in sub-millisecond time.
    """
    # 1. India VIX Signal
    if vix_override is not None:
        vix_val = vix_override
        if vix_val <= VIX_LOW:
            vix_score = 85.0
        elif vix_val <= VIX_HIGH:
            vix_score = 85.0 - (vix_val - VIX_LOW) / (VIX_HIGH - VIX_LOW) * 35.0
        elif vix_val <= VIX_PANIC:
            vix_score = 50.0 - (vix_val - VIX_HIGH) / (VIX_PANIC - VIX_HIGH) * 35.0
        else:
            vix_score = max(0.0, 15.0 - (vix_val - VIX_PANIC) * 2.0)
    else:
        vix_val, vix_score = get_india_vix()

    # 2. Advance / Decline
    advances = 0
    declines = 0
    buying_pressures = []
    cmf_scores = []
    volume_ratios = []

    for sym, indic in indicators_by_symbol.items():
        if not indic:
            continue
        
        # A/D
        chg = float(indic.get("change_pct", 0.0))
        if chg > 0:
            advances += 1
        elif chg < 0:
            declines += 1
            
        # Buying Pressure
        bp = indic.get("buying_pressure")
        if bp is not None:
            buying_pressures.append(float(bp))
            
        # CMF
        cmf = indic.get("cmf")
        if cmf is not None:
            # Map CMF (-1 to +1) to 0-100 score
            cmf_score = (float(cmf) + 1.0) / 2.0 * 100.0
            cmf_scores.append(cmf_score)
            
        # Volume Surge
        vol_ratio = indic.get("volume_ratio")
        if vol_ratio is not None:
            # Map: ratio 0.5 -> 25, 1.0 -> 50, 2.0 -> 100
            vol_score = min(100.0, float(vol_ratio) * 50.0)
            volume_ratios.append(vol_score)

    total_ad = advances + declines
    ad_score = (advances / total_ad * 100.0) if total_ad > 0 else 50.0

    bp_score = float(np.mean(buying_pressures)) * 100.0 if buying_pressures else 50.0
    cmf_score_val = float(np.mean(cmf_scores)) if cmf_scores else 50.0
    vol_score_val = float(np.mean(volume_ratios)) if volume_ratios else 50.0

    # 3. Composite Pulse Calculation
    w = PULSE_WEIGHTS
    pulse_score = (
        w["vix"]      * vix_score +
        w["ad_ratio"] * ad_score  +
        w["bp"]       * bp_score  +
        w["cmf"]      * cmf_score_val +
        w["volume"]   * vol_score_val
    )
    pulse_score = round(min(100.0, max(0.0, pulse_score)), 1)

    # 4. Labeling
    if pulse_score >= 65:
        label = "BUYERS_STRONG"
        emoji = "🟢"
    elif pulse_score >= 55:
        label = "BUYERS_SLIGHT"
        emoji = "🟡"
    elif pulse_score >= 45:
        label = "NEUTRAL"
        emoji = "⚪"
    elif pulse_score >= 35:
        label = "SELLERS_SLIGHT"
        emoji = "🟠"
    else:
        label = "SELLERS_STRONG"
        emoji = "🔴"

    downgrade = pulse_score < PULSE_DOWNGRADE_THRESHOLD

    result = {
        "pulse_score":      pulse_score,
        "pulse_label":      label,
        "pulse_emoji":      emoji,
        "vix":              vix_val,
        "vix_score":        round(vix_score, 1),
        "advances":         advances,
        "declines":         declines,
        "ad_ratio":         round(advances / max(1, advances + declines), 3),
        "ad_score":         round(ad_score, 1),
        "buying_pressure":  round(bp_score, 1),
        "cmf_score":        round(cmf_score_val, 1),
        "volume_score":     round(vol_score_val, 1),
        "downgrade_buy":    downgrade,
        "interpretation":   _interpret(pulse_score, vix_val, advances, declines, bp_score),
    }

    logger.info(
        f"[PULSE] Precalculated Score={pulse_score} ({label}) | VIX={vix_val:.1f} | "
        f"A/D={advances}/{declines} | BP={bp_score:.0f}% | CMF={cmf_score_val:.0f}% | Vol={vol_score_val:.0f}%"
    )

    return result


def _interpret(score: float, vix: float, adv: int, dec: int, bp: float) -> str:
    """Returns a plain-English one-line interpretation of the pulse."""
    total = adv + dec
    ad_pct = round(adv / total * 100) if total > 0 else 50

    if score >= 65:
        return (
            f"Buyers clearly in control. {ad_pct}% of stocks advancing, "
            f"VIX={vix:.1f} (calm), price closing near highs (BP={bp:.0f}%). "
            f"Good conditions to enter setups."
        )
    elif score >= 55:
        return (
            f"Mild buying bias. {ad_pct}% stocks up, VIX={vix:.1f}. "
            f"Conditions acceptable — size positions conservatively."
        )
    elif score >= 45:
        return (
            f"Market balanced. {ad_pct}% stocks up, VIX={vix:.1f}. "
            f"No clear buyer or seller edge — wait for cleaner setups."
        )
    elif score >= 35:
        return (
            f"Sellers slightly dominant. Only {ad_pct}% stocks advancing, "
            f"VIX={vix:.1f}. Avoid aggressive entries today."
        )
    else:
        return (
            f"Sellers in control. Only {ad_pct}% stocks advancing, "
            f"VIX={vix:.1f} (elevated), price closing near lows (BP={bp:.0f}%). "
            f"Stay flat — protect capital."
        )
