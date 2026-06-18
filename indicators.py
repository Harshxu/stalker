# -*- coding: utf-8 -*-
"""
STALKER - Technical Indicators Engine v2.0
Three-strategy intraday engine:
  1. Opening Range Breakout (ORB)
  2. VWAP Bounce & Flip
  3. Momentum + Volume Surge
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple, Dict
import config


# ─────────────────────────────────────────────
# CORE MATH UTILITIES
# ─────────────────────────────────────────────

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (RSI)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Daily VWAP: (High + Low + Close) / 3 — typical price.
    For daily candles this is the correct per-bar VWAP.
    """
    return (df["High"] + df["Low"] + df["Close"]) / 3


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — measures volatility."""
    high = df["High"]
    low  = df["Low"]
    prev_close = df["Close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low  - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema_fast  = calculate_ema(series, fast)
    ema_slow  = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(series: pd.Series, period: int = 20,
                               std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: (upper, middle, lower)."""
    middle = series.rolling(period).mean()
    std    = series.rolling(period).std()
    upper  = middle + std_dev * std
    lower  = middle - std_dev * std
    return upper, middle, lower


def calculate_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Chaikin Money Flow (CMF) — institutional accumulation/distribution."""
    try:
        high   = df["High"]
        low    = df["Low"]
        close  = df["Close"]
        volume = df["Volume"]
        hl_range = (high - low).replace(0, 1e-10)
        mf_mult   = ((close - low) - (high - close)) / hl_range
        mf_vol    = mf_mult * volume
        return mf_vol.rolling(period).sum() / volume.rolling(period).sum().replace(0, 1e-10)
    except Exception:
        return pd.Series(0.0, index=df.index)


def volume_surge_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    """Today's volume vs 20-day average. 2.0 = 2× normal volume."""
    if len(df) < lookback + 1:
        return 1.0
    avg_vol = df["Volume"].iloc[-(lookback + 1):-1].mean()
    return float(df["Volume"].iloc[-1] / avg_vol) if avg_vol > 0 else 1.0


def gap_up_percentage(df: pd.DataFrame) -> float:
    """Today's opening gap vs previous close (%)."""
    if len(df) < 2:
        return 0.0
    prev_close = df["Close"].iloc[-2]
    today_open = df["Open"].iloc[-1]
    return float(((today_open - prev_close) / prev_close) * 100) if prev_close > 0 else 0.0


def price_vs_52w(df: pd.DataFrame) -> float:
    """Distance from 52-week high (negative = below peak)."""
    if len(df) < 10:
        return -100.0
    period_high = df["High"].rolling(min(252, len(df))).max().iloc[-1]
    current     = df["Close"].iloc[-1]
    return float(((current - period_high) / period_high) * 100)


def relative_strength_vs_nifty(stock_df: pd.DataFrame, nifty_df: pd.DataFrame,
                                period: int = 20) -> float:
    """RS: stock 20d return minus Nifty 20d return."""
    try:
        s = (stock_df["Close"].iloc[-1] / stock_df["Close"].iloc[-period] - 1) * 100
        n = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[-period] - 1) * 100
        return float(s - n)
    except Exception:
        return 0.0


def calculate_multi_horizon_rs(stock_df: pd.DataFrame, nifty_df: pd.DataFrame) -> float:
    """Multi-horizon RS: 1M×10% + 3M×20% + 6M×30% + 12M×40%."""
    try:
        avail = min(len(stock_df), len(nifty_df))
        periods = [20, 60, 120, 250]
        weights = [0.10, 0.20, 0.30, 0.40]
        active_p, active_w = [], []
        for p, w in zip(periods, weights):
            if avail >= p:
                active_p.append(p)
                active_w.append(w)
        if not active_p:
            return relative_strength_vs_nifty(stock_df, nifty_df, max(5, avail - 1))
        tw = sum(active_w)
        return float(sum(relative_strength_vs_nifty(stock_df, nifty_df, p) * w / tw
                         for p, w in zip(active_p, active_w)))
    except Exception:
        return 0.0


def calculate_atr_slope(atr_series: pd.Series, period: int = 10) -> float:
    """Slope of ATR — negative = volatility contraction (VCP signal)."""
    try:
        if len(atr_series) < period:
            return 0.0
        y = atr_series.tail(period).values
        slope, _ = np.polyfit(np.arange(len(y)), y, 1)
        return float(slope)
    except Exception:
        return 0.0


def check_ohl_pattern(df: pd.DataFrame) -> str:
    """Open-High-Low pattern: 'bullish' (O=L), 'bearish' (O=H), or 'neutral'."""
    if len(df) < 1:
        return "neutral"
    today = df.iloc[-1]
    tol   = today["Open"] * 0.001
    if abs(today["Open"] - today["Low"]) <= tol:
        return "bullish"
    if abs(today["Open"] - today["High"]) <= tol:
        return "bearish"
    return "neutral"


# ─────────────────────────────────────────────────────────────
# STRATEGY 1 — OPENING RANGE BREAKOUT (ORB) ANALYSIS
# ─────────────────────────────────────────────────────────────

def compute_orb_signals(df: pd.DataFrame) -> Dict:
    """
    Detects ORB setup quality using DAILY bars as proxy.
    Since intraday 15-min data requires a live feed, we use:
      - Opening Range = the day's first bar proxy (Open / High / Low)
      - Breakout signal = Close > High of the opening candle (proxied by day)
      - Volume confirmation = volume_ratio >= ORB_VOLUME_MULT

    Scores on a 0-100 scale for the screener ranking.
    """
    try:
        if len(df) < 5:
            return {"orb_score": 0, "orb_signal": "none", "orb_range_pct": 0.0,
                    "orb_volume_confirmed": False, "orb_breakout": False}

        latest     = df.iloc[-1]
        open_p     = float(latest["Open"])
        high_p     = float(latest["High"])
        low_p      = float(latest["Low"])
        close_p    = float(latest["Close"])
        prev_close = float(df.iloc[-2]["Close"])

        # Approximate opening range as gap (prev close → today open) + first-bar range
        orb_high   = max(open_p, prev_close * 1.001)   # OR High proxy
        orb_low    = min(open_p, prev_close * 0.999)   # OR Low proxy
        or_range   = orb_high - orb_low
        or_range_pct = (or_range / open_p) if open_p > 0 else 0.01

        # Reject overly wide opening ranges (> ORB_MAX_RANGE_PCT = 4%)
        too_wide = or_range_pct > getattr(config, "ORB_MAX_RANGE_PCT", 0.04)

        # Breakout signal: close ended above the opening range high
        bullish_bo = close_p > orb_high
        bearish_bo = close_p < orb_low

        # Volume filter
        avg_vol    = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 22 else float(df["Volume"].mean())
        vol_today  = float(latest["Volume"])
        vol_mult   = vol_today / avg_vol if avg_vol > 0 else 1.0
        vol_ok     = vol_mult >= getattr(config, "ORB_VOLUME_MULT", 1.5)

        # Target / SL based on OR range (1.5× to 2× range)
        if bullish_bo and not too_wide:
            entry    = orb_high
            sl       = orb_low
            t1       = entry + 1.5 * or_range
            t2       = entry + 2.0 * or_range
            rr       = (t2 - entry) / (entry - sl) if (entry - sl) > 0 else 0.0
        elif bearish_bo and not too_wide:
            entry    = orb_low
            sl       = orb_high
            t1       = entry - 1.5 * or_range
            t2       = entry - 2.0 * or_range
            rr       = (entry - t2) / (sl - entry) if (sl - entry) > 0 else 0.0
        else:
            entry = sl = t1 = t2 = 0.0
            rr    = 0.0

        rr_ok = rr >= getattr(config, "ORB_RR_MIN", 1.5)

        # Score
        score = 0.0
        if bullish_bo and not too_wide:
            score += 40.0
        elif bearish_bo and not too_wide:
            score += 30.0
        if vol_ok:
            score += 30.0
        if rr_ok:
            score += 20.0
        if gap_up_percentage(df) > 0.5:
            score += 10.0  # Gap-up days are preferred

        signal = "bullish_breakout" if (bullish_bo and not too_wide) else \
                 "bearish_breakout" if (bearish_bo and not too_wide) else "no_breakout"

        return {
            "orb_score":            round(min(score, 100.0), 1),
            "orb_signal":           signal,
            "orb_range_pct":        round(or_range_pct * 100, 2),
            "orb_range_too_wide":   too_wide,
            "orb_volume_confirmed": vol_ok,
            "orb_vol_ratio":        round(vol_mult, 2),
            "orb_breakout":         bullish_bo or bearish_bo,
            "orb_rr":               round(rr, 2),
            "orb_rr_ok":            rr_ok,
            "orb_entry":            round(entry, 2),
            "orb_sl":               round(sl, 2),
            "orb_t1":               round(t1, 2),
            "orb_t2":               round(t2, 2),
        }
    except Exception:
        return {"orb_score": 0, "orb_signal": "none", "orb_range_pct": 0.0,
                "orb_volume_confirmed": False, "orb_breakout": False,
                "orb_rr": 0.0, "orb_rr_ok": False,
                "orb_entry": 0, "orb_sl": 0, "orb_t1": 0, "orb_t2": 0}


# ─────────────────────────────────────────────────────────────
# STRATEGY 2 — VWAP BOUNCE & FLIP ANALYSIS
# ─────────────────────────────────────────────────────────────

def compute_vwap_signals(df: pd.DataFrame) -> Dict:
    """
    Detects VWAP Bounce (trend continuation) and VWAP Flip (trend reversal).

    Setup A — VWAP Bounce:
      Price above VWAP → pulls back to VWAP → hammer/engulfing → long
    Setup B — VWAP Flip:
      Price below VWAP → breaks above with volume → retest → long

    Uses daily bars as proxy (true intraday requires live 5-min feed).
    Scores 0-100.
    """
    try:
        if len(df) < 10:
            return {"vwap_score": 0, "vwap_signal": "none", "vwap_setup": "none",
                    "above_vwap": False, "rsi_ok_long": False}

        close   = df["Close"]
        high    = df["High"]
        low     = df["Low"]
        volume  = df["Volume"]

        vwap_series = calculate_vwap(df)
        rsi_series  = calculate_rsi(close, 14)
        ema9_series = calculate_ema(close, 9)
        ema21_series = calculate_ema(close, 21)

        latest_close = float(close.iloc[-1])
        prev_close   = float(close.iloc[-2])
        latest_vwap  = float(vwap_series.iloc[-1])
        latest_rsi   = float(rsi_series.iloc[-1])
        latest_ema9  = float(ema9_series.iloc[-1])
        latest_ema21 = float(ema21_series.iloc[-1])

        above_vwap     = latest_close > latest_vwap
        ema9_above_21  = latest_ema9 > latest_ema21
        rsi_ok_long    = latest_rsi >= getattr(config, "VWAP_RSI_MIN_LONG", 50)
        rsi_ok_short   = latest_rsi <= getattr(config, "VWAP_RSI_MAX_SHORT", 50)

        # Distance from VWAP (%)
        vwap_dist_pct  = ((latest_close - latest_vwap) / latest_vwap) * 100 if latest_vwap > 0 else 0.0

        # Volume confirmation
        avg_vol  = float(volume.iloc[-21:-1].mean()) if len(df) >= 22 else float(volume.mean())
        vol_mult = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
        vol_ok   = vol_mult >= 1.3

        # Setup A — Bounce: was above VWAP, pulled back close to it, now bouncing
        # Proxy: price yesterday was further above VWAP, today close to/at it
        prev_vwap  = float(vwap_series.iloc[-2])
        prev_above = float(close.iloc[-2]) > prev_vwap
        near_vwap  = abs(vwap_dist_pct) <= 0.5   # within 0.5% of VWAP

        setup_a = prev_above and near_vwap and rsi_ok_long and ema9_above_21

        # Setup B — Flip: was below VWAP, now close crossed above with volume
        was_below   = float(close.iloc[-2]) < prev_vwap
        now_above   = above_vwap
        flip_setup  = was_below and now_above and vol_ok and rsi_ok_long

        # Score
        score = 0.0
        setup_label = "none"

        if setup_a:
            score += 40.0
            setup_label = "bounce"
            if rsi_ok_long:
                score += 20.0
            if ema9_above_21:
                score += 15.0
            if vol_ok:
                score += 15.0
            if near_vwap:
                score += 10.0

        elif flip_setup:
            score += 35.0
            setup_label = "flip"
            if rsi_ok_long:
                score += 20.0
            if vol_ok:
                score += 25.0
            if ema9_above_21:
                score += 10.0
            if near_vwap:
                score += 10.0

        # SL/Target for VWAP setups
        sl_long  = latest_vwap - getattr(config, "VWAP_SL_PTS", 6)
        t1_long  = latest_close + (latest_close - sl_long) * 1.5
        t2_long  = latest_close + (latest_close - sl_long) * 2.0
        rr_long  = (t2_long - latest_close) / (latest_close - sl_long) if (latest_close - sl_long) > 0 else 0.0

        return {
            "vwap_score":       round(min(score, 100.0), 1),
            "vwap_signal":      setup_label,
            "vwap_setup":       setup_label,
            "above_vwap":       above_vwap,
            "vwap_dist_pct":    round(vwap_dist_pct, 2),
            "rsi_ok_long":      rsi_ok_long,
            "ema9_above_21":    ema9_above_21,
            "vwap_vol_ok":      vol_ok,
            "vwap_sl":          round(sl_long, 2),
            "vwap_t1":          round(t1_long, 2),
            "vwap_t2":          round(t2_long, 2),
            "vwap_rr":          round(rr_long, 2),
            "latest_vwap":      round(latest_vwap, 2),
            "latest_rsi":       round(latest_rsi, 1),
        }
    except Exception:
        return {"vwap_score": 0, "vwap_signal": "none", "vwap_setup": "none",
                "above_vwap": False, "rsi_ok_long": False,
                "vwap_sl": 0, "vwap_t1": 0, "vwap_t2": 0, "vwap_rr": 0}


# ─────────────────────────────────────────────────────────────
# STRATEGY 3 — MOMENTUM + VOLUME SURGE ANALYSIS
# ─────────────────────────────────────────────────────────────

def compute_momentum_signals(df: pd.DataFrame, nifty_df: Optional[pd.DataFrame] = None) -> Dict:
    """
    Detects high-probability Momentum + Volume Surge setups.

    Criteria:
      - Volume >= 150% of 20-day average (institutional participation)
      - RSI in 50–65 range (not chasing, not weak)
      - Price forming a base after initial surge (pullback/consolidation)
      - Catalyst confirmed by gap-up or strong price change
      - Above 5-day EMA (short-term momentum intact)

    Scores 0-100.
    """
    try:
        if len(df) < 22:
            return {"mom_score": 0, "mom_signal": "none", "vol_surge": False,
                    "rsi_ok": False, "has_catalyst": False}

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        rsi_series  = calculate_rsi(close, 14)
        ema5_series = calculate_ema(close, 5)
        ema20_series = calculate_ema(close, 20)
        vwap_series = calculate_vwap(df)

        latest_close = float(close.iloc[-1])
        latest_rsi   = float(rsi_series.iloc[-1])
        latest_ema5  = float(ema5_series.iloc[-1])
        latest_ema20 = float(ema20_series.iloc[-1])
        latest_vwap  = float(vwap_series.iloc[-1])

        # Volume surge filter
        avg_vol   = float(volume.iloc[-21:-1].mean())
        vol_today = float(volume.iloc[-1])
        vol_ratio = vol_today / avg_vol if avg_vol > 0 else 1.0
        vol_surge = vol_ratio >= getattr(config, "MOM_VOLUME_SURGE_MIN", 1.5)

        # RSI entry zone
        rsi_min = getattr(config, "MOM_RSI_ENTRY_MIN", 50)
        rsi_max = getattr(config, "MOM_RSI_ENTRY_MAX", 65)
        rsi_avoid = getattr(config, "MOM_RSI_AVOID_ABOVE", 75)
        rsi_ok = rsi_min <= latest_rsi <= rsi_max
        not_chasing = latest_rsi <= rsi_avoid

        # Catalyst signal: gap or strong single-day move (>1%)
        gap_pct    = gap_up_percentage(df)
        day_chg    = float(((latest_close - close.iloc[-2]) / close.iloc[-2]) * 100) if len(close) >= 2 else 0.0
        has_catalyst = abs(gap_pct) >= 0.5 or abs(day_chg) >= 1.5

        # Base formation: did price pull back from its intraday high?
        # Proxy: close is not at the day's high (i.e., not still spiking)
        day_high  = float(high.iloc[-1])
        day_low   = float(low.iloc[-1])
        day_range = day_high - day_low
        close_pos = (latest_close - day_low) / day_range if day_range > 0 else 0.5
        # Ideal base: closed in 30%-70% of day range (not at extremes)
        formed_base = 0.30 <= close_pos <= 0.80

        # Trend filters
        above_ema5  = latest_close > latest_ema5
        above_ema20 = latest_close > latest_ema20
        above_vwap_flag = latest_close > latest_vwap

        # Relative strength vs Nifty
        rs = calculate_multi_horizon_rs(df, nifty_df) if nifty_df is not None else 0.0

        # SL / Target
        # SL = low of the base candle (day's low)
        sl   = day_low * 0.999  # 0.1% below day low
        risk = latest_close - sl
        t1   = latest_close + risk * 1.0   # 1:1
        t2   = latest_close + risk * 2.0   # 1:2
        rr   = (t2 - latest_close) / risk if risk > 0 else 0.0

        # Score
        score = 0.0
        if vol_surge:
            score += 35.0
        if rsi_ok:
            score += 25.0
        if has_catalyst:
            score += 15.0
        if formed_base:
            score += 10.0
        if above_ema5:
            score += 5.0
        if above_vwap_flag:
            score += 5.0
        if rs > 2.0:
            score += 5.0
        if not not_chasing:
            score -= 20.0  # Hard penalty for chasing (RSI > 75)

        signal = "momentum_buy" if (vol_surge and rsi_ok and has_catalyst and above_ema5) else \
                 "momentum_watch" if (vol_surge and has_catalyst) else "no_momentum"

        return {
            "mom_score":        round(min(max(score, 0.0), 100.0), 1),
            "mom_signal":       signal,
            "vol_surge":        vol_surge,
            "vol_ratio":        round(vol_ratio, 2),
            "rsi_ok":           rsi_ok,
            "rsi_value":        round(latest_rsi, 1),
            "has_catalyst":     has_catalyst,
            "gap_pct":          round(gap_pct, 2),
            "day_change_pct":   round(day_chg, 2),
            "formed_base":      formed_base,
            "close_position":   round(close_pos, 2),
            "above_ema5":       above_ema5,
            "above_vwap":       above_vwap_flag,
            "rs_vs_nifty":      round(rs, 2),
            "mom_sl":           round(sl, 2),
            "mom_t1":           round(t1, 2),
            "mom_t2":           round(t2, 2),
            "mom_rr":           round(rr, 2),
        }
    except Exception:
        return {"mom_score": 0, "mom_signal": "none", "vol_surge": False,
                "rsi_ok": False, "has_catalyst": False,
                "mom_sl": 0, "mom_t1": 0, "mom_t2": 0, "mom_rr": 0}


# ─────────────────────────────────────────────────────────────
# MASTER INDICATOR COMPUTATION (called by screener)
# ─────────────────────────────────────────────────────────────

def compute_all_indicators(df: pd.DataFrame,
                           nifty_df: Optional[pd.DataFrame] = None) -> dict:
    """
    Computes all indicators required by STALKER's three-strategy screener.
    Returns a clean dict of latest values + strategy signal sub-dicts.
    """
    if df is None or len(df) < 20:
        return {}

    close  = df["Close"]
    volume = df["Volume"]

    # ── Core indicators ──────────────────────────────────────
    ema20  = calculate_ema(close, 20)
    ema50  = calculate_ema(close, 50)
    ema9   = calculate_ema(close, 9)
    ema21  = calculate_ema(close, 21)
    rsi    = calculate_rsi(close, 14)
    vwap   = calculate_vwap(df)
    atr    = calculate_atr(df)
    macd_line, macd_signal, macd_hist = calculate_macd(close)
    bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(close)
    cmf_series = calculate_cmf(df)
    atr_slope  = calculate_atr_slope(atr, period=10)

    latest_close  = float(close.iloc[-1])
    latest_ema20  = float(ema20.iloc[-1])
    latest_ema50  = float(ema50.iloc[-1])
    latest_ema9   = float(ema9.iloc[-1])
    latest_ema21  = float(ema21.iloc[-1])
    latest_rsi    = float(rsi.iloc[-1])
    latest_vwap   = float(vwap.iloc[-1])
    latest_atr    = float(atr.iloc[-1])
    latest_cmf    = float(cmf_series.iloc[-1]) if not cmf_series.empty else 0.0

    vol_ratio  = volume_surge_ratio(df)
    gap_pct    = gap_up_percentage(df)
    ohl_signal = check_ohl_pattern(df)
    dist_52w   = price_vs_52w(df)
    change_pct = float(((latest_close - close.iloc[-2]) / close.iloc[-2]) * 100) if len(close) >= 2 else 0.0

    rs_vs_nifty = 0.0
    if nifty_df is not None:
        rs_vs_nifty = calculate_multi_horizon_rs(df, nifty_df)

    above_vwap   = latest_close > latest_vwap * (1 - config.VWAP_TOLERANCE)
    ema_aligned  = latest_ema20 > latest_ema50
    ema9_above21 = latest_ema9 > latest_ema21
    volume_surge = vol_ratio >= config.VOLUME_SURGE_RATIO
    macd_bullish = (float(macd_line.iloc[-1]) > float(macd_signal.iloc[-1]) and
                    float(macd_hist.iloc[-1]) > 0)

    bb_width     = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])
    bb_avg_width = float((bb_upper - bb_lower).rolling(20).mean().iloc[-1]) if len(df) >= 20 else bb_width
    bb_squeeze   = bb_width < bb_avg_width * 0.7 if bb_avg_width > 0 else False
    bb_width_ratio = bb_width / bb_avg_width if bb_avg_width > 0 else 1.0

    dist_from_ema20 = float(((latest_close - latest_ema20) / latest_ema20) * 100) if latest_ema20 else 0.0

    latest_high = float(df["High"].iloc[-1])
    latest_low  = float(df["Low"].iloc[-1])
    range_size  = latest_high - latest_low
    buying_pressure = (latest_close - latest_low) / range_size if range_size > 0.01 else 0.5

    try:
        returns = close.pct_change().tail(20)
        volatility = returns.std() * np.sqrt(252)
        ret_20     = (latest_close / close.iloc[-20] - 1)
        sharpe_like_score = float(ret_20 / volatility) if volatility > 0 else 0.0
    except Exception:
        sharpe_like_score = 0.0

    # ── Three-strategy signals ────────────────────────────────
    orb_signals  = compute_orb_signals(df)
    vwap_signals = compute_vwap_signals(df)
    mom_signals  = compute_momentum_signals(df, nifty_df)

    # ── Composite strategy score (0-100) ─────────────────────
    # Weighted: ORB 33% + VWAP 33% + Momentum 34%
    strategy_score = (
        0.33 * orb_signals.get("orb_score", 0) +
        0.33 * vwap_signals.get("vwap_score", 0) +
        0.34 * mom_signals.get("mom_score", 0)
    )

    # Determine dominant strategy
    scores = {
        "ORB":      orb_signals.get("orb_score", 0),
        "VWAP":     vwap_signals.get("vwap_score", 0),
        "MOMENTUM": mom_signals.get("mom_score", 0),
    }
    dominant_strategy = max(scores, key=scores.get)

    return {
        # ── Raw core values ──────────────────────────────────
        "close":            latest_close,
        "ema20":            latest_ema20,
        "ema50":            latest_ema50,
        "ema9":             latest_ema9,
        "ema21":            latest_ema21,
        "rsi":              latest_rsi,
        "vwap":             latest_vwap,
        "atr":              latest_atr,
        "atr_slope":        atr_slope,
        "volume":           float(volume.iloc[-1]),
        "volume_ratio":     vol_ratio,
        "gap_pct":          gap_pct,
        "change_pct":       change_pct,
        "dist_52w_high":    dist_52w,
        "rs_vs_nifty":      rs_vs_nifty,
        "macd":             float(macd_line.iloc[-1]),
        "macd_signal":      float(macd_signal.iloc[-1]),
        "macd_hist":        float(macd_hist.iloc[-1]),
        "bb_upper":         float(bb_upper.iloc[-1]),
        "bb_lower":         float(bb_lower.iloc[-1]),
        "bb_width_ratio":   bb_width_ratio,
        "cmf":              latest_cmf,
        "dist_from_ema20":  dist_from_ema20,
        "buying_pressure":  buying_pressure,
        "sharpe_like_score": sharpe_like_score,

        # ── Boolean signals ──────────────────────────────────
        "above_vwap":       above_vwap,
        "ema_aligned":      ema_aligned,
        "ema9_above_21":    ema9_above21,
        "ema_slope_up":     float(ema20.iloc[-1]) > float(ema20.iloc[-3]),
        "rsi_healthy":      config.RSI_MIN <= latest_rsi <= config.RSI_MAX,
        "volume_surge":     volume_surge,
        "volume_expansion": vol_ratio >= 1.5,
        "volume_dry_up":    vol_ratio <= 0.5,
        "gap_up":           gap_pct >= config.GAP_UP_THRESHOLD,
        "macd_bullish":     macd_bullish,
        "bb_squeeze":       bb_squeeze,
        "ohl_signal":       ohl_signal,

        # ── Strategy sub-signals ─────────────────────────────
        "orb":              orb_signals,
        "vwap_strat":       vwap_signals,
        "momentum":         mom_signals,

        # ── Composite strategy ranking ───────────────────────
        "strategy_score":       round(strategy_score, 1),
        "dominant_strategy":    dominant_strategy,
    }
