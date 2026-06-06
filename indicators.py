"""
STALKER - Technical Indicators Engine
Calculates VWAP, EMA, RSI, OHL, Volume Surge, ATR.
All internal logic — results are clean numbers for the screener.
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple
import config


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
    Volume Weighted Average Price.
    For daily candles: each row is one full day, so VWAP per day = typical price of that bar.
    For intraday candles: proper cumulative VWAP with daily reset would be needed.
    Since we use daily data (1d interval), typical price IS the correct daily VWAP.
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    return typical_price


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


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD: (EMA12 - EMA26), Signal(EMA9 of MACD), Histogram.
    Returns (macd_line, signal_line, histogram).
    """
    ema_fast   = calculate_ema(series, fast)
    ema_slow   = calculate_ema(series, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands: middle (SMA), upper, lower.
    Returns (upper, middle, lower).
    """
    middle = series.rolling(period).mean()
    std    = series.rolling(period).std()
    upper  = middle + std_dev * std
    lower  = middle - std_dev * std
    return upper, middle, lower


def calculate_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Chaikin Money Flow (CMF) measures institutional accumulation/distribution.
    CMF = Sum(MF Volume, N) / Sum(Volume, N)
    MF Multiplier = [(Close - Low) - (High - Close)] / (High - Low)
    MF Volume = MF Multiplier * Volume
    """
    try:
        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        volume = df["Volume"]
        
        hl_range = high - low
        # Avoid division by zero
        hl_range = hl_range.replace(0, 1e-10)
        
        mf_multiplier = ((close - low) - (high - close)) / hl_range
        mf_volume = mf_multiplier * volume
        
        sum_mf_volume = mf_volume.rolling(window=period).sum()
        sum_volume = volume.rolling(window=period).sum().replace(0, 1e-10)
        
        return sum_mf_volume / sum_volume
    except Exception:
        return pd.Series(0.0, index=df.index)


def volume_surge_ratio(df: pd.DataFrame, lookback: int = 20) -> float:
    """
    Today's volume vs average of last N days.
    2.0 = trading at 2x normal volume.
    """
    if len(df) < lookback + 1:
        return 1.0
    avg_vol = df["Volume"].iloc[-(lookback + 1):-1].mean()
    if avg_vol == 0:
        return 1.0
    return float(df["Volume"].iloc[-1] / avg_vol)


def check_ohl_pattern(df: pd.DataFrame) -> str:
    """
    Open-High-Low Pattern (OHL Strategy):
    - Open == High → bearish (sellers in control)
    - Open == Low  → bullish (buyers in control)
    Returns: 'bullish', 'bearish', or 'neutral'
    """
    if len(df) < 1:
        return "neutral"

    today = df.iloc[-1]
    open_p = today["Open"]
    high_p = today["High"]
    low_p  = today["Low"]

    # Allow 0.1% tolerance for floating point
    tolerance = open_p * 0.001

    if abs(open_p - low_p) <= tolerance:
        return "bullish"   # Open = Low → buyers from the start
    elif abs(open_p - high_p) <= tolerance:
        return "bearish"   # Open = High → sellers dominated
    return "neutral"


def gap_up_percentage(df: pd.DataFrame) -> float:
    """
    Calculate today's gap vs previous close.
    Positive = gap up, Negative = gap down.
    """
    if len(df) < 2:
        return 0.0
    prev_close = df["Close"].iloc[-2]
    today_open = df["Open"].iloc[-1]
    if prev_close == 0:
        return 0.0
    return float(((today_open - prev_close) / prev_close) * 100)


def price_vs_52w(df: pd.DataFrame) -> float:
    """
    How far is current price from 52-week high? (%)
    -5% = 5% below 52-week high (near highs = bullish momentum)
    -50% = far from highs = weak
    """
    if len(df) < 10:
        return -100.0
    period_high = df["High"].rolling(min(252, len(df))).max().iloc[-1]
    current     = df["Close"].iloc[-1]
    return float(((current - period_high) / period_high) * 100)


def relative_strength_vs_nifty(stock_df: pd.DataFrame, nifty_df: pd.DataFrame, period: int = 20) -> float:
    """
    Relative Strength: stock performance vs NIFTY over last N days.
    Positive = outperforming market.
    """
    try:
        stock_return = (stock_df["Close"].iloc[-1] / stock_df["Close"].iloc[-period] - 1) * 100
        nifty_return = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[-period] - 1) * 100
        return float(stock_return - nifty_return)
    except Exception:
        return 0.0


def calculate_multi_horizon_rs(stock_df: pd.DataFrame, nifty_df: pd.DataFrame) -> float:
    """
    Multi-Horizon Relative Strength (Quality Momentum Spec):
    Combines 20-day (40%), 60-day (40%), and RS Slope (20%).
    """
    try:
        rs_20 = relative_strength_vs_nifty(stock_df, nifty_df, period=20)
        rs_60 = relative_strength_vs_nifty(stock_df, nifty_df, period=60) if len(stock_df) >= 60 and len(nifty_df) >= 60 else rs_20
        
        # RS Slope (difference between 20d and 60d as a proxy for slope/acceleration)
        rs_slope = rs_20 - (rs_60 / 3) 
        
        return float(0.40 * rs_20 + 0.40 * rs_60 + 0.20 * rs_slope)
    except Exception:
        return 0.0


def calculate_atr_slope(atr_series: pd.Series, period: int = 10) -> float:
    """
    Calculates the slope of the ATR over a lookback window to check volatility contraction.
    Negative slope indicates volatility contraction.
    """
    try:
        if len(atr_series) < period:
            return 0.0
        y = atr_series.tail(period).values
        x = np.arange(len(y))
        slope, _ = np.polyfit(x, y, 1)
        return float(slope)
    except Exception:
        return 0.0


def compute_all_indicators(df: pd.DataFrame, nifty_df: Optional[pd.DataFrame] = None) -> dict:
    """
    Master function: compute all indicators for a stock DataFrame.
    Returns a clean dict of indicator values (latest values only).
    """
    if df is None or len(df) < 20:
        return {}

    # Resample to weekly for multi-timeframe confirmation
    try:
        weekly_df = df.resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        weekly_close = weekly_df['Close']
        weekly_ema20 = calculate_ema(weekly_close, 20)
        weekly_ema50 = calculate_ema(weekly_close, 50)
        latest_weekly_ema20 = float(weekly_ema20.iloc[-1]) if not weekly_ema20.empty else 0.0
        latest_weekly_ema50 = float(weekly_ema50.iloc[-1]) if not weekly_ema50.empty else 0.0
        weekly_trend_bullish = latest_weekly_ema20 > latest_weekly_ema50
    except Exception:
        latest_weekly_ema20 = 0.0
        latest_weekly_ema50 = 0.0
        weekly_trend_bullish = False

    close  = df["Close"]
    volume = df["Volume"]

    # EMAs
    ema20 = calculate_ema(close, config.EMA_SHORT)
    ema50 = calculate_ema(close, config.EMA_LONG)

    # RSI
    rsi = calculate_rsi(close, config.RSI_PERIOD)

    # VWAP
    vwap = calculate_vwap(df)

    # ATR
    atr = calculate_atr(df)

    # MACD
    macd_line, macd_signal, macd_hist = calculate_macd(close)

    # Bollinger Bands
    bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(close)

    # Latest values
    latest_close  = float(close.iloc[-1])
    latest_ema20  = float(ema20.iloc[-1])
    latest_ema50  = float(ema50.iloc[-1])
    latest_rsi    = float(rsi.iloc[-1])
    latest_vwap   = float(vwap.iloc[-1])
    latest_atr    = float(atr.iloc[-1])
    latest_volume = float(volume.iloc[-1])
    vol_ratio     = volume_surge_ratio(df)
    gap_pct       = gap_up_percentage(df)
    ohl_signal    = check_ohl_pattern(df)
    dist_52w_high = price_vs_52w(df)

    # Relative strength (Multi-Horizon)
    rs_vs_nifty = 0.0
    if nifty_df is not None:
        rs_vs_nifty = calculate_multi_horizon_rs(df, nifty_df)

    # Key boolean signals
    above_vwap       = latest_close > latest_vwap * (1 - config.VWAP_TOLERANCE)
    ema_aligned      = latest_ema20 > latest_ema50
    ema_bullish_slope = float(ema20.iloc[-1]) > float(ema20.iloc[-3])
    rsi_healthy      = config.RSI_MIN <= latest_rsi <= config.RSI_MAX
    volume_surge     = vol_ratio >= config.VOLUME_SURGE_RATIO
    volume_expansion = vol_ratio >= 1.5
    volume_dry_up    = vol_ratio <= 0.5
    gap_up           = gap_pct >= config.GAP_UP_THRESHOLD
    
    # Risk-Adjusted Momentum (Sharpe-like score)
    try:
        returns = close.pct_change().tail(20)
        volatility = returns.std() * np.sqrt(252)
        ret_20 = (latest_close / close.iloc[-20] - 1)
        sharpe_like_score = float(ret_20 / volatility) if volatility > 0 else 0.0
    except Exception:
        sharpe_like_score = 0.0

    # MACD bullish: line above signal, histogram positive
    macd_bullish = (float(macd_line.iloc[-1]) > float(macd_signal.iloc[-1]) and
                    float(macd_hist.iloc[-1]) > 0)

    # Bollinger squeeze (low volatility = potential breakout)
    bb_width = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])
    bb_avg_width = float((bb_upper - bb_lower).rolling(20).mean().iloc[-1]) if len(df) >= 20 else bb_width
    bb_squeeze = bb_width < bb_avg_width * 0.7 if bb_avg_width > 0 else False
    bb_width_ratio = bb_width / bb_avg_width if bb_avg_width > 0 else 1.0

    # ATR Slope
    atr_slope = calculate_atr_slope(atr, period=10)

    # CMF
    cmf_series = calculate_cmf(df)
    latest_cmf = float(cmf_series.iloc[-1]) if not cmf_series.empty else 0.0

    # Pullback distance from 20 EMA
    dist_from_ema20 = float((latest_close - latest_ema20) / latest_ema20 * 100.0) if latest_ema20 else 0.0

    return {
        # Raw values
        "close":          latest_close,
        "ema20":          latest_ema20,
        "ema50":          latest_ema50,
        "rsi":            latest_rsi,
        "vwap":           latest_vwap,
        "atr":            latest_atr,
        "atr_slope":      atr_slope,
        "volume":         latest_volume,
        "volume_ratio":   vol_ratio,
        "gap_pct":        gap_pct,
        "dist_52w_high":  dist_52w_high,
        "rs_vs_nifty":    rs_vs_nifty,
        "macd":           float(macd_line.iloc[-1]),
        "macd_signal":    float(macd_signal.iloc[-1]),
        "macd_hist":      float(macd_hist.iloc[-1]),
        "bb_upper":       float(bb_upper.iloc[-1]),
        "bb_lower":       float(bb_lower.iloc[-1]),
        "bb_width_ratio": bb_width_ratio,
        "cmf":            latest_cmf,
        "dist_from_ema20": dist_from_ema20,

        # Boolean signals
        "above_vwap":     above_vwap,
        "ema_aligned":    ema_aligned,
        "ema_slope_up":   ema_bullish_slope,
        "rsi_healthy":    rsi_healthy,
        "volume_surge":   volume_surge,
        "volume_expansion": volume_expansion,
        "volume_dry_up":  volume_dry_up,
        "gap_up":         gap_up,
        "macd_bullish":   macd_bullish,
        "bb_squeeze":     bb_squeeze,
        "ohl_signal":     ohl_signal,
        "weekly_trend_bullish": weekly_trend_bullish,
        "sharpe_like_score": sharpe_like_score,
    }
