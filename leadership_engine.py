"""
STALKER — Leadership Validation Layer
Validates candidates after quantitative scoring to identify true market leaders.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

import config
import indicators as ind

logger = logging.getLogger(__name__)


def calculate_stability_score(df_hist_1y: pd.DataFrame) -> float:
    """
    Measures how consistently a stock has remained in a leadership structure.
    Checks how many days of the last 60 trading days the price was above its 150-day SMA,
    and its 50-day SMA was above its 150-day SMA.
    """
    if df_hist_1y is None or len(df_hist_1y) < 155:
        return 0.0

    try:
        close = df_hist_1y["Close"]
        sma_50 = ind.calculate_sma(close, 50)
        sma_150 = ind.calculate_sma(close, 150)

        # Look back at the last 60 days
        lookback = min(60, len(df_hist_1y) - 150)
        if lookback <= 0:
            return 0.0

        close_lb = close.iloc[-lookback:]
        s50_lb = sma_50.iloc[-lookback:]
        s150_lb = sma_150.iloc[-lookback:]

        matching_days = 0
        for i in range(lookback):
            idx = -lookback + i
            c_val = close_lb.iloc[i]
            s50_val = s50_lb.iloc[i]
            s150_val = s150_lb.iloc[i]

            if not (np.isnan(s50_val) or np.isnan(s150_val)):
                if c_val > s150_val and s50_val > s150_val:
                    matching_days += 1

        stability_score = (matching_days / float(lookback)) * 100.0
        return float(round(stability_score, 1))

    except Exception as e:
        logger.error(f"Error calculating stability score: {e}")
        return 0.0


def check_minervini_template(df_hist_1y: pd.DataFrame, rs_percentile: float = 70.0) -> Dict:
    """
    Evaluates the 8 conditions of Mark Minervini's Trend Template.
    Requires at least 200 trading days of history.
    """
    if df_hist_1y is None or len(df_hist_1y) < 150:
        return {
            "conditions_passed": 0,
            "tier": "Reject",
            "failed_conditions": [1, 2, 3, 4, 5, 6, 7, 8],
            "score": 0.0,
            "msg": "Insufficient history (<150 days)"
        }

    try:
        close = df_hist_1y["Close"]
        high = df_hist_1y["High"]
        low = df_hist_1y["Low"]

        # 1. Calculate Moving Averages
        sma_50 = ind.calculate_sma(close, 50)
        sma_150 = ind.calculate_sma(close, 150)
        sma_200 = ind.calculate_sma(close, 200)

        # 2. Get today's values
        c_val = float(close.iloc[-1])
        s50 = float(sma_50.iloc[-1])
        s150 = float(sma_150.iloc[-1])
        s200 = float(sma_200.iloc[-1]) if len(sma_200) >= 200 and not np.isnan(sma_200.iloc[-1]) else float(sma_150.iloc[-1])

        # 3. 200 SMA trending up check (1 month = 21 trading days lookback)
        s200_trend_up = False
        if len(sma_200) >= 22:
            s200_prev = sma_200.iloc[-22]
            if not np.isnan(s200_prev) and not np.isnan(s200):
                s200_trend_up = s200 > s200_prev
        else:
            s200_trend_up = True # Fallback if history slightly short

        # 4. 52-week High/Low (last 252 trading days)
        lookback_52w = min(252, len(df_hist_1y))
        high_52w = float(high.iloc[-lookback_52w:].max())
        low_52w = float(low.iloc[-lookback_52w:].min())

        # Evaluate the 8 conditions
        cond_map = {
            1: c_val > s150 and c_val > s200,                                # Price > 150 & 200 SMA
            2: s150 > s200,                                                  # 150 SMA > 200 SMA
            3: s200_trend_up,                                                # 200 SMA trending up 1 month
            4: s50 > s150 and s50 > s200,                                    # 50 SMA > 150 & 200 SMA
            5: c_val > s50,                                                  # Price > 50 SMA
            6: c_val >= (1.30 * low_52w),                                    # Price >= 30% above 52w low
            7: c_val >= (0.75 * high_52w),                                   # Price within 25% of 52w high
            8: rs_percentile >= 70.0                                         # RS rating >= 70
        }

        passed_list = [k for k, v in cond_map.items() if v]
        failed_list = [k for k, v in cond_map.items() if not v]
        conditions_passed = len(passed_list)

        # Check non-negotiable conditions from config
        non_neg = getattr(config, "MINERVINI_NONNEG_CONDITIONS", [1, 2, 5, 6])
        non_neg_failed = [c for c in non_neg if c in failed_list]

        # Determine Tier
        min_conditions = getattr(config, "MINERVINI_MIN_CONDITIONS", 6)
        if conditions_passed < min_conditions:
            tier = "Reject"
        elif conditions_passed == 8:
            tier = "Elite"
        elif conditions_passed == 7:
            tier = "Strong"
        else:
            tier = "Acceptable"

        return {
            "conditions_passed": conditions_passed,
            "tier": tier,
            "failed_conditions": failed_list,
            "non_neg_failed": non_neg_failed,
            "score": round((conditions_passed / 8.0) * 100.0, 1)
        }

    except Exception as e:
        logger.error(f"Error evaluating Minervini Trend Template: {e}")
        return {
            "conditions_passed": 0,
            "tier": "Reject",
            "failed_conditions": [1, 2, 3, 4, 5, 6, 7, 8],
            "score": 0.0,
            "msg": f"Evaluation error: {e}"
        }


def detect_vcp(df_hist_3mo: pd.DataFrame) -> Dict:
    """
    Detects Volatility Contraction Pattern (VCP) features in 3-month history.
    Scored from 0 to 100.
    """
    if df_hist_3mo is None or len(df_hist_3mo) < 20:
        return {
            "is_vcp": False,
            "grade": "None",
            "quality_score": 0.0,
            "contractions_found": 0,
            "atr_compressed": False,
            "volume_tapering": False,
            "tight_closes": False
        }

    try:
        close = df_hist_3mo["Close"]
        high = df_hist_3mo["High"]
        low = df_hist_3mo["Low"]
        volume = df_hist_3mo["Volume"]

        # 1. Base Peak Proximity: Price close to 60-day high (within 12%)
        max_lookback = min(60, len(df_hist_3mo))
        base_high = float(high.iloc[-max_lookback:].max())
        c_val = float(close.iloc[-1])
        dist_from_high = (base_high - c_val) / base_high
        proximity_ok = dist_from_high <= 0.12

        # 2. ATR Compression Check: ATR(10) < ATR(20)
        atr_10 = ind.calculate_atr(df_hist_3mo, 10).iloc[-1]
        atr_20 = ind.calculate_atr(df_hist_3mo, 20).iloc[-1]
        atr_compressed = atr_10 < atr_20 if not (np.isnan(atr_10) or np.isnan(atr_20)) else False

        # 3. Tight Closes (last 5 bars): Low price volatility/spread
        # Max daily range (High - Low) / Close over last 5 days
        ranges = (high.iloc[-5:] - low.iloc[-5:]) / close.iloc[-5:]
        max_spread = float(ranges.max())
        tight_closes = max_spread < 0.025 # Max daily spread under 2.5%

        # 4. Find contractions
        smoothed = close.rolling(window=5, min_periods=1).mean()
        
        peaks = []
        troughs = []
        for i in range(2, len(smoothed) - 2):
            if smoothed.iloc[i] == smoothed.iloc[i-2:i+3].max():
                peaks.append((i, smoothed.iloc[i]))
            elif smoothed.iloc[i] == smoothed.iloc[i-2:i+3].min():
                troughs.append((i, smoothed.iloc[i]))

        contractions = []
        for p_idx, p_val in peaks:
            matching_troughs = [t for t in troughs if t[0] > p_idx]
            if matching_troughs:
                t_idx, t_val = matching_troughs[0]
                depth = (p_val - t_val) / p_val
                if depth > 0.01: # at least 1% pullback
                    contractions.append(depth)

        recent_contractions = contractions[-3:]
        contractions_found = len(recent_contractions)
        
        shrinking_depth = False
        if contractions_found >= 2:
            shrinking_depth = True
            for i in range(1, len(recent_contractions)):
                if recent_contractions[i] > (recent_contractions[i-1] + 0.01):
                    shrinking_depth = False
                    break

        # 5. Volume Tapering: Average volume in the last 10 days < Average volume of prior 20 days
        vol_taper = False
        if len(volume) >= 30:
            vol_recent = volume.iloc[-10:].mean()
            vol_prior = volume.iloc[-30:-10].mean()
            vol_taper = vol_recent < vol_prior

        # Score computation (out of 100)
        quality_score = 0.0
        if proximity_ok:
            quality_score += 20.0
        if atr_compressed:
            quality_score += 20.0
        if tight_closes:
            quality_score += 15.0
        if vol_taper:
            quality_score += 15.0
        
        if contractions_found == 2:
            quality_score += 15.0
        elif contractions_found >= 3:
            quality_score += 30.0

        if shrinking_depth and contractions_found >= 2:
            quality_score += 10.0

        # Quality Grade Thresholds
        min_vcp_score = getattr(config, "VCP_MIN_QUALITY_SCORE", 40.0)
        is_vcp = quality_score >= min_vcp_score

        if not is_vcp:
            grade = "None"
        elif quality_score >= 80:
            grade = "Elite"
        elif quality_score >= 60:
            grade = "Strong"
        else:
            grade = "Weak"

        return {
            "is_vcp": is_vcp,
            "grade": grade,
            "quality_score": round(quality_score, 1),
            "contractions_found": contractions_found,
            "atr_compressed": atr_compressed,
            "volume_tapering": vol_taper,
            "tight_closes": tight_closes
        }

    except Exception as e:
        logger.error(f"Error detecting VCP: {e}")
        return {
            "is_vcp": False,
            "grade": "None",
            "quality_score": 0.0,
            "contractions_found": 0,
            "atr_compressed": False,
            "volume_tapering": False,
            "tight_closes": False
        }


def compute_leadership_score(
    stability_score: float,
    sector_rs_rank: float,
    industry_rs_rank: float,
    market_is_bullish: bool,
    inst_score: float
) -> float:
    """
    Calculates unified hierarchical leadership score (0-100).
    Formula:
      35% Stability Score + 25% Sector RS + 20% Industry RS + 10% Market Regime + 10% Institutional Score
    Uses historical stability score to avoid double counting stock RS.
    """
    market_bonus = 100.0 if market_is_bullish else 0.0
    
    score = (
        (stability_score * 0.35) +
        (sector_rs_rank * 0.25) +
        (industry_rs_rank * 0.20) +
        (market_bonus * 0.10) +
        (inst_score * 0.10)
    )
    return float(round(max(0.0, min(100.0, score)), 2))
