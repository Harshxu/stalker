"""
STALKER — Meta Model v2.0
"The AI watching the AI."

Evaluates whether the current setup TYPE is working in the CURRENT REGIME
by checking rolling win rate AND average return quality in db_manager.

v2.0 adds STRATEGY DEGRADATION & AUTO-REPLACEMENT:
  - Tracks per-setup avg return over last 3-6 days (not just 30 days)
  - If avg return is consistently negative for N days → marks setup as DEGRADED
  - DEGRADED setups are REPLACED with a fresh strategy from config.STRATEGY_FALLBACK
  - Fresh strategies: INSTITUTIONAL_BREAKOUT, QUALITY_TREND, VWAP_RECLAIM

Penalty/Bonus Rules (tightened from v1.0):
  - n < 5 resolved trades: no adjustment (avoid noise)
  - win_rate < 35% OR avg_return < -0.3%: -25 point penalty (was -15)
  - win_rate > 60%: +12 point bonus (was +5)
  - Setup degraded 3+ days: apply STRATEGY_FALLBACK label
"""

import logging
from typing import Dict, Tuple, Optional
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# ── Thresholds (v2.0 — tightened from audit data) ─────────────────────
MIN_SAMPLE_SIZE = 5

PENALTY_THRESHOLD_WINRATE = 0.35   # Win rate below 35% → penalty (was 40%)
PENALTY_THRESHOLD_RETURN  = -0.30  # Avg return below -0.30% → penalty even with ok WR
BONUS_THRESHOLD = 0.60             # Win rate above 60% → bonus (was 65%)
PENALTY_PTS = -25.0                # Stronger punishment (was -15)
BONUS_PTS   = +12.0                # Stronger reward (was +5)

DEGRADATION_RETURN_THRESHOLD = -0.15  # Avg return below this = degraded day
DEGRADATION_DAYS_REQUIRED    = 3      # Consecutive degraded days before replacement


def _load_recent_picks(lookback_days: int) -> list:
    """Shared loader: returns flat list of pick records from DB or JSON."""
    try:
        import db_manager
        import config
        min_date = str(date.today() - timedelta(days=lookback_days))
        db = db_manager.get_db()
        if db is not None:
            try:
                col = db[config.MONGO_COLLECTION_PICKS]
                return list(col.find({"date": {"$gte": min_date}}))
            except Exception:
                pass
        records = db_manager._read_json("daily_picks.json")
        return [r for r in records if r.get("date", "") >= min_date]
    except Exception:
        return []


def _resolve_return(pick: dict) -> Optional[float]:
    """Gets the best available resolved return for a pick."""
    for key in ("future_1d_return", "intraday_return", "future_5d_return", "future_3d_return"):
        val = pick.get(key)
        if val is not None:
            return float(val)
    return None


def get_setup_performance(
    trade_type: str,
    lookback_days: int = 30,
) -> Dict:
    """
    Returns win_rate, avg_return, and n_trades for a given setup
    over the last N days. Uses resolved trades only.
    """
    records = _load_recent_picks(lookback_days)
    wins = 0
    total = 0
    returns = []

    for r in records:
        for p in r.get("picks", r.get("top_picks", [])):
            if p.get("trade_type") != trade_type:
                continue
            ret = _resolve_return(p)
            if ret is None:
                continue
            total += 1
            returns.append(ret)
            if ret > 0:
                wins += 1

    win_rate = round(wins / total, 3) if total > 0 else 0.0
    avg_return = round(sum(returns) / len(returns), 4) if returns else 0.0

    return {
        "win_rate":   win_rate,
        "avg_return": avg_return,
        "n_trades":   total,
    }


def check_strategy_degradation(trade_type: str, days: int = None) -> Dict:
    """
    Checks if a setup has produced consistently negative avg returns
    over the last N days (default from config.STRATEGY_DEGRADATION_DAYS).

    Returns:
        {
          "is_degraded": bool,
          "consecutive_negative_days": int,
          "replacement_strategy": str or None,
          "daily_returns": list[float]
        }
    """
    try:
        import config
        required_days = days or getattr(config, "STRATEGY_DEGRADATION_DAYS", 3)
        threshold     = getattr(config, "STRATEGY_DEGRADATION_THRESHOLD", -0.15)
        fallback_map  = getattr(config, "STRATEGY_FALLBACK", {})
    except Exception:
        required_days = 3
        threshold = -0.15
        fallback_map = {}

    # Check each of the last N days individually
    daily_returns = []
    today_str = str(date.today())

    for days_ago in range(1, required_days + 2):  # +2 buffer for weekends/gaps
        check_date = str(date.today() - timedelta(days=days_ago))
        if len(daily_returns) >= required_days:
            break
        try:
            import db_manager
            import config
            db = db_manager.get_db()
            records = []
            if db is not None:
                col = db[config.MONGO_COLLECTION_PICKS]
                records = list(col.find({"date": check_date}))
            if not records:
                all_recs = db_manager._read_json("daily_picks.json")
                records = [r for r in all_recs if r.get("date", "") == check_date]

            day_returns = []
            for r in records:
                for p in r.get("picks", r.get("top_picks", [])):
                    if p.get("trade_type") != trade_type:
                        continue
                    ret = _resolve_return(p)
                    if ret is not None:
                        day_returns.append(ret)

            if day_returns:
                daily_returns.append(sum(day_returns) / len(day_returns))
        except Exception:
            pass

    # Count consecutive negative days
    consecutive_negative = 0
    for dr in daily_returns:
        if dr < threshold:
            consecutive_negative += 1
        else:
            break  # Reset — must be CONSECUTIVE

    is_degraded = consecutive_negative >= required_days
    replacement = fallback_map.get(trade_type) if is_degraded else None

    if is_degraded:
        logger.warning(
            f"[META] STRATEGY DEGRADED: {trade_type} has been negative for "
            f"{consecutive_negative} consecutive days (returns: {[round(r, 2) for r in daily_returns]}). "
            f"Replacing with: {replacement}"
        )

    return {
        "is_degraded":              is_degraded,
        "consecutive_negative_days": consecutive_negative,
        "replacement_strategy":     replacement,
        "daily_returns":            [round(r, 3) for r in daily_returns],
    }


def adjust_alpha(
    alpha: float,
    trade_type: str,
    regime_legacy: str,
    lookback_days: int = 30,
) -> Tuple[float, Dict]:
    """
    Applies a meta-model adjustment to the raw alpha score.

    v2.0 changes:
    - Stronger penalty (-25 pts, was -15)
    - Checks avg return quality in addition to win rate
    - Detects strategy degradation over last 3-6 days
    - Auto-replaces degraded setup with fresh strategy label

    Args:
        alpha:          Raw alpha from alpha_engine (0-100)
        trade_type:     e.g. 'BREAKOUT', 'MOMENTUM', 'INSTITUTIONAL_BREAKOUT'
        regime_legacy:  3-state legacy regime ('Bull', 'Neutral', 'Bear')
        lookback_days:  How many days of history to evaluate (30-day window)

    Returns:
        (adjusted_alpha, meta_info dict)
    """
    perf = get_setup_performance(trade_type, lookback_days)
    win_rate   = perf["win_rate"]
    avg_return = perf["avg_return"]
    n_trades   = perf["n_trades"]

    adjustment = 0.0
    meta_signal = "neutral"
    effective_trade_type = trade_type

    # ── Step 1: Check strategy degradation (3-6 day consecutive negative) ─
    degradation = check_strategy_degradation(trade_type)
    if degradation["is_degraded"] and degradation["replacement_strategy"]:
        effective_trade_type = degradation["replacement_strategy"]
        meta_signal = f"degraded→replaced:{effective_trade_type}"
        # Apply a penalty to push this stock toward replacement-strategy scoring
        adjustment = -20.0
        logger.info(
            f"[META] {trade_type} → REPLACED by {effective_trade_type} "
            f"({degradation['consecutive_negative_days']}d negative streak)"
        )
    elif n_trades < MIN_SAMPLE_SIZE:
        # Not enough data — don't touch the alpha
        meta_signal = "insufficient_data"
    elif win_rate < PENALTY_THRESHOLD_WINRATE or avg_return < PENALTY_THRESHOLD_RETURN:
        # Underperforming on win rate OR return quality
        adjustment = PENALTY_PTS
        meta_signal = "underperforming"
        logger.info(
            f"[META] {trade_type}: WR={win_rate:.0%}, AvgRet={avg_return:.2f}% "
            f"in {n_trades} trades → penalty {adjustment:+.0f}pt"
        )
    elif win_rate > BONUS_THRESHOLD and avg_return > 0:
        # Genuinely outperforming on BOTH win rate and positive returns
        adjustment = BONUS_PTS
        meta_signal = "outperforming"
        logger.info(
            f"[META] {trade_type}: WR={win_rate:.0%}, AvgRet={avg_return:.2f}% "
            f"in {n_trades} trades → bonus {adjustment:+.0f}pt"
        )

    adjusted_alpha = round(min(100.0, max(0.0, alpha + adjustment)), 2)

    return adjusted_alpha, {
        "meta_signal":          meta_signal,
        "setup_win_rate":       round(win_rate * 100.0, 1),
        "setup_avg_return":     round(avg_return, 3),
        "setup_n_trades":       n_trades,
        "meta_adjustment_pts":  adjustment,
        "trade_type":           trade_type,
        "effective_trade_type": effective_trade_type,
        "is_degraded":          degradation["is_degraded"],
        "consecutive_neg_days": degradation["consecutive_negative_days"],
        "regime":               regime_legacy,
    }
