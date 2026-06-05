"""
STALKER — Meta Model
"The AI watching the AI."

Evaluates whether the current setup TYPE is actually working in the CURRENT REGIME
by checking recent win rates of the same trade_type in db_manager.

Rules:
  - If n < 5 resolved trades for this setup: no adjustment (avoid noise)
  - If win_rate < 40%: apply -15 point penalty (setup is failing in this regime)
  - If win_rate > 65%: apply +5 point bonus (setup is working well)
  - Otherwise: neutral (0 adjustment)

This protects against regime shifts where a formerly-good signal decays.
NOTE: This module observes and adjusts. It does NOT update weights automatically.
"""

import logging
from typing import Dict, Tuple, Optional
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Minimum trades needed before the meta model makes any adjustment
MIN_SAMPLE_SIZE = 5

# Thresholds
PENALTY_THRESHOLD = 0.40   # Win rate below this → penalty
BONUS_THRESHOLD = 0.65     # Win rate above this → bonus
PENALTY_PTS = -15.0
BONUS_PTS = +5.0


def _get_recent_setup_win_rate(
    trade_type: str,
    regime_legacy: str,
    lookback_days: int = 30,
) -> Tuple[float, int]:
    """
    Loads recent picks from db_manager and returns (win_rate, n_trades)
    for the given setup type and legacy regime.
    Only considers picks with a resolved future_5d_return or future_3d_return.
    """
    try:
        import db_manager
        min_date = str(date.today() - timedelta(days=lookback_days))
        records = []

        db = db_manager.get_db()
        if db is not None:
            try:
                import config
                col = db[config.MONGO_COLLECTION_PICKS]
                records = list(col.find({"date": {"$gte": min_date}}))
            except Exception:
                records = []

        if not records:
            records = db_manager._read_json("daily_picks.json")
            records = [r for r in records if r.get("date", "") >= min_date]

        wins = 0
        total = 0

        for r in records:
            rec_regime = r.get("market_trend", "neutral").capitalize()
            picks = r.get("picks", r.get("top_picks", []))

            for p in picks:
                if p.get("trade_type") != trade_type:
                    continue
                # Only analyse resolved trades
                ret = p.get("future_5d_return") if p.get("future_5d_return") is not None \
                    else p.get("future_3d_return")
                if ret is None:
                    continue
                total += 1
                if float(ret) > 0:
                    wins += 1

        if total == 0:
            return 0.0, 0

        return round(wins / total, 3), total

    except Exception as e:
        logger.warning(f"[META] Could not load recent setup win rates: {e}")
        return 0.0, 0


def adjust_alpha(
    alpha: float,
    trade_type: str,
    regime_legacy: str,
    lookback_days: int = 30,
) -> Tuple[float, Dict]:
    """
    Applies a meta-model adjustment to the raw alpha score.

    Args:
        alpha: Raw alpha from alpha_engine (0-100)
        trade_type: e.g. 'BREAKOUT', 'PULLBACK', 'MOMENTUM', etc.
        regime_legacy: 3-state legacy regime ('Bull', 'Neutral', 'Bear')
        lookback_days: How many days of history to evaluate

    Returns:
        (adjusted_alpha, meta_info dict)
    """
    win_rate, n_trades = _get_recent_setup_win_rate(trade_type, regime_legacy, lookback_days)

    adjustment = 0.0
    meta_signal = "neutral"

    if n_trades < MIN_SAMPLE_SIZE:
        # Not enough data — don't touch the alpha
        meta_signal = "insufficient_data"
    elif win_rate < PENALTY_THRESHOLD:
        adjustment = PENALTY_PTS
        meta_signal = "underperforming"
        logger.info(
            f"[META] {trade_type} win rate {win_rate:.0%} in {n_trades} trades "
            f"→ applying {adjustment:+.0f}pt penalty"
        )
    elif win_rate > BONUS_THRESHOLD:
        adjustment = BONUS_PTS
        meta_signal = "outperforming"
        logger.info(
            f"[META] {trade_type} win rate {win_rate:.0%} in {n_trades} trades "
            f"→ applying {adjustment:+.0f}pt bonus"
        )

    adjusted_alpha = round(min(100.0, max(0.0, alpha + adjustment)), 2)

    return adjusted_alpha, {
        "meta_signal": meta_signal,
        "setup_win_rate": round(win_rate * 100.0, 1),
        "setup_n_trades": n_trades,
        "meta_adjustment_pts": adjustment,
        "trade_type": trade_type,
        "regime": regime_legacy,
    }
