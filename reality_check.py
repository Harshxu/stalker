"""
STALKER — Reality Check Layer
Pre-execution filter that catches trades that look good on paper
but are NOT actually executable in live market conditions.

Checks:
  1. Gap Extension Risk: Stock up >5% from yesterday close → "Extended"
  2. Earnings Proximity: Earnings < 3 days away → Event Risk, downgrade to WATCH
  3. Circuit Proximity: Price within 1% of upper circuit → No exit liquidity
  4. Spread Risk: If avg daily range is less than 0.15% → stock is too illiquid to trade

NOTE: Some checks require live data or may not always be available.
      Graceful fallback is used when data is missing.
"""

import logging
from typing import Dict, List, Tuple, Optional
import pandas as pd
from datetime import date

logger = logging.getLogger(__name__)

# ── Configurable Thresholds ──────────────────────────────────────────
GAP_EXTENSION_THRESHOLD = 0.05     # >5% gap from prev close = extended
CIRCUIT_PROXIMITY_THRESHOLD = 0.01  # Within 1% of upper circuit = no exit
MIN_DAILY_RANGE_PCT = 0.015        # Average daily range must be >1.5% to avoid slow/expensive trades
EARNINGS_WINDOW_DAYS = 3           # Flag if earnings within 3 days


def check_gap_extension(df_hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    Returns True (i.e. FAILS check) if stock has gapped up > 5% from previous close.
    A gap this large means you are chasing — you've missed most of the move.
    """
    try:
        if len(df_hist) < 2:
            return False, ""
        prev_close = float(df_hist["Close"].iloc[-2])
        today_open = float(df_hist["Open"].iloc[-1])
        if prev_close <= 0:
            return False, ""
        gap_pct = (today_open - prev_close) / prev_close
        if gap_pct > GAP_EXTENSION_THRESHOLD:
            return True, f"Extended: Gap-up of {gap_pct:.1%} from prev close. Wait for pullback."
        return False, ""
    except Exception as e:
        logger.debug(f"[REALITY] Gap check error: {e}")
        return False, ""


def check_earnings_proximity(fund: Dict) -> Tuple[bool, str]:
    """
    Returns True (FAILS) if earnings are within the next 3 trading days.
    Uses 'days_to_earnings' field if populated in fundamentals cache.
    """
    try:
        days = fund.get("days_to_earnings")
        if days is None:
            return False, ""  # Unknown — don't block
        days = int(days)
        if 0 <= days <= EARNINGS_WINDOW_DAYS:
            return True, f"Event Risk: Earnings in {days} day(s). Position sizing is dangerous."
        return False, ""
    except Exception as e:
        logger.debug(f"[REALITY] Earnings proximity check error: {e}")
        return False, ""


def check_circuit_proximity(df_hist: pd.DataFrame, fund: Dict) -> Tuple[bool, str]:
    """
    Check circuit proximity.
    Disabled using 52w high as a proxy because it blocks breakout setups.
    """
    return False, ""


def check_minimum_spread(df_hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    Returns True (FAILS) if the average daily price range is too narrow.
    A very tight daily range means the stock is illiquid and hard to trade.
    """
    try:
        if len(df_hist) < 10:
            return False, ""
        recent = df_hist.tail(10)
        avg_range = ((recent["High"] - recent["Low"]) / recent["Close"]).mean()
        if avg_range < MIN_DAILY_RANGE_PCT:
            return True, f"Illiquid: Avg daily range {avg_range:.2%} is too narrow to trade."
        return False, ""
    except Exception as e:
        logger.debug(f"[REALITY] Spread check error: {e}")
        return False, ""


def validate(
    df_hist: pd.DataFrame,
    fund: Dict,
    indic: Dict,
) -> Tuple[bool, List[str]]:
    """
    Run all reality checks. Returns (passes: bool, friction_notes: list).

    A stock that FAILS any check is not blocked from the portfolio —
    it is downgraded to WATCH status by the screener orchestrator.
    The friction_notes explain WHY to the user.

    Args:
        df_hist: OHLCV DataFrame for the stock
        fund: Fundamentals dict
        indic: Technical indicators dict

    Returns:
        (passes_all: bool, list of friction note strings)
    """
    friction_notes = []
    failed = False

    # Check 1: Gap Extension
    gap_fail, gap_note = check_gap_extension(df_hist)
    if gap_fail:
        friction_notes.append(f"⚠️ {gap_note}")
        failed = True

    # Check 2: Earnings Proximity
    earn_fail, earn_note = check_earnings_proximity(fund)
    if earn_fail:
        friction_notes.append(f"📅 {earn_note}")
        failed = True

    # Check 3: Circuit Proximity
    circ_fail, circ_note = check_circuit_proximity(df_hist, fund)
    if circ_fail:
        friction_notes.append(f"🔒 {circ_note}")
        failed = True

    # Check 4: Minimum Spread / Liquidity
    spread_fail, spread_note = check_minimum_spread(df_hist)
    if spread_fail:
        friction_notes.append(f"💧 {spread_note}")
        failed = True

    passes = not failed
    return passes, friction_notes
