"""
STALKER — Ensemble Alpha Engine
Replaces the monolithic 8-factor weighted sum with 4 isolated sub-models.
Each sub-model is self-contained and testable independently.

Sub-Models:
  1. Momentum Engine  → RS, weekly trend, Sharpe-adjusted momentum
  2. Quality Engine   → ROE, FCF growth, margins, debt
  3. Institutional Engine → CMF, volume profile, dry-up signal
  4. Catalyst Engine  → Earnings surprise, revision trend, news

Final Alpha = weighted ensemble of the 4 sub-models (weights from regime_engine).
Also returns a Confidence Score (0-100) based on data completeness.
"""

import logging
import numpy as np
from typing import Dict, Tuple
import pandas as pd

import config
import fundamentals as fund_module

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SUB-MODEL 1: MOMENTUM ENGINE
# ─────────────────────────────────────────────

def score_momentum(
    symbol: str,
    indic: Dict,
    rs_percentile: float,
    struct_score: float,
) -> Tuple[float, int]:
    """
    Scores momentum quality and trend robustness.

    Returns:
        (score 0-100, populated_inputs count)
    """
    populated = 0
    score = 0.0

    # 1. RS Percentile rank (primary signal) — max 40 pts
    if rs_percentile is not None:
        score += 0.40 * rs_percentile
        populated += 1

    # 2. Weekly EMA trend confirmation (multi-timeframe filter) — max 20 pts
    weekly_trend = indic.get("weekly_trend_bullish")
    if weekly_trend is not None:
        score += 20.0 if weekly_trend else 0.0
        populated += 1

    # 3. Sharpe-adjusted momentum (smooth trend gets rewarded more) — max 25 pts
    sharpe_mom = float(indic.get("sharpe_like_score", 0.0))
    # Sharpe momentum is already normalised; clip to [-3, 3] and map to 0-25
    sharpe_score = 25.0 * max(0.0, min(1.0, (sharpe_mom + 1.5) / 3.0))
    score += sharpe_score
    populated += 1

    # 4. Market Structure bonus — max 15 pts
    score += 0.15 * struct_score
    populated += 1

    return round(min(100.0, max(0.0, score)), 2), populated


# ─────────────────────────────────────────────
# SUB-MODEL 2: QUALITY ENGINE
# ─────────────────────────────────────────────

def score_quality(
    fund: Dict,
    roe_percentile: float,
    fcf_percentile: float,
    margin_percentile: float,
    revenue_growth_percentile: float,
) -> Tuple[float, int]:
    """
    Scores business quality and financial health.

    Returns:
        (score 0-100, populated_inputs count)
    """
    populated = 0
    score = 0.0

    # 1. ROE percentile rank — max 30 pts
    if roe_percentile is not None:
        score += 0.30 * roe_percentile
        populated += 1

    # 2. FCF Growth trend rank — max 25 pts
    if fcf_percentile is not None:
        score += 0.25 * fcf_percentile
        populated += 1

    # 3. Operating Margin expansion rank — max 25 pts
    if margin_percentile is not None:
        score += 0.25 * margin_percentile
        populated += 1

    # 4. Revenue Growth trend rank — max 15 pts
    if revenue_growth_percentile is not None:
        score += 0.15 * revenue_growth_percentile
        populated += 1

    # 5. Leverage penalty — max -15 pts
    de = fund.get("debt_to_equity")
    if de is not None:
        populated += 1
        if de > 2.0:
            score -= 15.0
        elif de > 1.2:
            score -= 8.0 * ((de - 1.2) / 0.8)

    return round(min(100.0, max(0.0, score)), 2), populated


# ─────────────────────────────────────────────
# SUB-MODEL 3: INSTITUTIONAL ENGINE
# ─────────────────────────────────────────────

def score_institutional(
    indic: Dict,
    volume_percentile: float,
    cmf_percentile: float,
) -> Tuple[float, int]:
    """
    Scores institutional accumulation and supply/demand imbalance.

    Returns:
        (score 0-100, populated_inputs count)
    """
    populated = 0
    score = 0.0

    # 1. CMF (Chaikin Money Flow) percentile — max 40 pts
    if cmf_percentile is not None:
        score += 0.40 * cmf_percentile
        populated += 1

    # 2. Volume Ratio percentile — max 30 pts
    if volume_percentile is not None:
        score += 0.30 * volume_percentile
        populated += 1

    # 3. Volume Dry-up Signal (key accumulation pattern) — max 20 pts
    # On pullbacks, volume should contract — institutions are NOT distributing
    vol_dryup = indic.get("volume_dry_up")
    if vol_dryup is not None:
        populated += 1
        if vol_dryup:
            score += 20.0
        # If volume expanding on a pullback, subtract — could be distribution
        else:
            vol_ratio = float(indic.get("volume_ratio", 1.0))
            dist_ema20 = float(indic.get("dist_from_ema20", 0.0))
            if dist_ema20 < -1.0 and vol_ratio > 1.5:
                score -= 10.0  # Possible distribution

    # 4. CMF absolute value sanity check — raw CMF > 0 = buying pressure
    cmf_raw = float(indic.get("cmf", 0.0))
    if cmf_raw > 0.10:
        score += 10.0
    elif cmf_raw < -0.10:
        score -= 10.0
    populated += 1

    return round(min(100.0, max(0.0, score)), 2), populated


# ─────────────────────────────────────────────
# SUB-MODEL 4: CATALYST ENGINE
# ─────────────────────────────────────────────

def score_catalyst(
    fund: Dict,
    news: Dict,
    earnings_surprise_percentile: float,
    earnings_revision_percentile: float,
    profit_growth_percentile: float,
) -> Tuple[float, int]:
    """
    Scores catalyst quality — earnings, revisions, and news.

    Returns:
        (score 0-100, populated_inputs count)
    """
    populated = 0
    score = 0.0

    # 1. Earnings Surprise percentile rank — max 35 pts
    if earnings_surprise_percentile is not None:
        score += 0.35 * earnings_surprise_percentile
        populated += 1

    # 2. EPS Revision trend — max 30 pts
    if earnings_revision_percentile is not None:
        score += 0.30 * earnings_revision_percentile
        populated += 1

    # 3. Profit Growth trend rank — max 25 pts
    if profit_growth_percentile is not None:
        score += 0.25 * profit_growth_percentile
        populated += 1

    # 4. News Sentiment modifier — max ±10 pts
    sentiment = news.get("news_sentiment", "neutral") if news else "neutral"
    if sentiment == "bullish":
        score += 10.0
    elif sentiment == "bearish":
        score -= 10.0
    populated += 1

    return round(min(100.0, max(0.0, score)), 2), populated


# ─────────────────────────────────────────────
# ENSEMBLE COMBINER
# ─────────────────────────────────────────────

def compute_ensemble_alpha(
    symbol: str,
    indic: Dict,
    fund: Dict,
    news: Dict,
    ms: Dict,
    regime: str,
    percentiles: Dict[str, float],
    struct_score: float = 50.0,
) -> Dict:
    """
    Combines 4 sub-model scores into a single ensemble alpha.

    Args:
        symbol: Stock ticker
        indic: Technical indicator dict from indicators.py
        fund: Fundamentals dict from data_fetcher.py
        news: News signals dict
        ms: Market structure dict
        regime: 8-state regime string (e.g. 'Bull_Trend')
        percentiles: Dict of {metric_name: percentile_rank} for this symbol
        struct_score: Structure score (0-100) from market_structure.py

    Returns:
        Dict with alpha, confidence, sub-scores, regime_weights used
    """
    # Pull percentiles
    rs_p = percentiles.get("rs", 50.0)
    vol_p = percentiles.get("volume", 50.0)
    cmf_p = percentiles.get("cmf", 50.0)
    roe_p = percentiles.get("roe", 50.0)
    fcf_p = percentiles.get("fcf_growth", 50.0)
    margin_p = percentiles.get("margin_expansion", 50.0)
    rev_growth_p = percentiles.get("revenue_growth", 50.0)
    earn_surprise_p = percentiles.get("earnings_surprise", 50.0)
    earn_revision_p = percentiles.get("earnings_revision", 50.0)
    profit_growth_p = percentiles.get("profit_growth", 50.0)

    # Score all 4 sub-models
    mom_score, mom_inputs = score_momentum(symbol, indic, rs_p, struct_score)
    qual_score, qual_inputs = score_quality(fund, roe_p, fcf_p, margin_p, rev_growth_p)
    inst_score, inst_inputs = score_institutional(indic, vol_p, cmf_p)
    cat_score, cat_inputs = score_catalyst(fund, news, earn_surprise_p, earn_revision_p, profit_growth_p)

    # Get regime-specific ensemble weights
    try:
        weights = config.ENSEMBLE_WEIGHTS.get(regime, config.ENSEMBLE_WEIGHTS["Neutral_Rotation"])
    except Exception:
        weights = {"momentum": 0.35, "quality": 0.25, "institutional": 0.25, "catalyst": 0.15}

    w_mom = weights["momentum"]
    w_qual = weights["quality"]
    w_inst = weights["institutional"]
    w_cat = weights["catalyst"]

    # Weighted ensemble
    alpha = (
        w_mom * mom_score +
        w_qual * qual_score +
        w_inst * inst_score +
        w_cat * cat_score
    )
    alpha = round(min(100.0, max(0.0, alpha)), 2)

    # Confidence Score = data completeness (0-100)
    # Max possible populated inputs = mom(4) + qual(5) + inst(4) + cat(4) = 17
    total_inputs = mom_inputs + qual_inputs + inst_inputs + cat_inputs
    max_inputs = 17
    data_confidence = round((total_inputs / max_inputs) * 100.0, 1)

    return {
        "alpha": alpha,
        "confidence": data_confidence,
        "momentum_score": mom_score,
        "quality_score": qual_score,
        "institutional_score": inst_score,
        "catalyst_score": cat_score,
        "regime_weights": weights,
        "sub_model_inputs": {
            "momentum": mom_inputs,
            "quality": qual_inputs,
            "institutional": inst_inputs,
            "catalyst": cat_inputs,
        }
    }
