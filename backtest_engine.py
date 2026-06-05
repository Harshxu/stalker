"""
STALKER — Walk-Forward Backtesting Engine
Runs the STALKER screening pipeline over historical periods to produce
honest out-of-sample performance metrics.

Walk-Forward Windows (default):
  Train 2018-2020 → Test 2021
  Train 2019-2021 → Test 2022
  Train 2020-2022 → Test 2023
  Train 2021-2023 → Test 2024
  Train 2022-2024 → Test 2025

Metrics per window:
  CAGR, Sharpe, Sortino, Max Drawdown,
  Win Rate, Profit Factor, Total Trades

Run as standalone:
  python backtest_engine.py

Results saved to:
  data/backtest_results.json
  reports/backtest_report.md
"""

import json
import logging
import os
import sys
import io
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

import config

# Force UTF-8 output on Windows — safe guard: never crash if buffer already replaced
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass  # In-process / thread context — stdout already safe

logger = logging.getLogger(__name__)

# ── Walk-Forward Window Definitions ──────────────────────────────────
WALK_FORWARD_WINDOWS = [
    {"label": "2018-2020 → 2021", "train_start": "2018-01-01", "train_end": "2020-12-31", "test_start": "2021-01-01", "test_end": "2021-12-31"},
    {"label": "2019-2021 → 2022", "train_start": "2019-01-01", "train_end": "2021-12-31", "test_start": "2022-01-01", "test_end": "2022-12-31"},
    {"label": "2020-2022 → 2023", "train_start": "2020-01-01", "train_end": "2022-12-31", "test_start": "2023-01-01", "test_end": "2023-12-31"},
    {"label": "2021-2023 → 2024", "train_start": "2021-01-01", "train_end": "2023-12-31", "test_start": "2024-01-01", "test_end": "2024-12-31"},
    {"label": "2022-2024 → 2025", "train_start": "2022-01-01", "train_end": "2024-12-31", "test_start": "2025-01-01", "test_end": "2025-06-01"},
]

# ── Performance Metrics ──────────────────────────────────────────────

def compute_metrics(returns: List[float], period_years: float = 1.0) -> Dict:
    """
    Computes standard quant performance metrics from a list of trade returns (%).
    Returns a dict of labelled metrics.
    """
    if not returns:
        return {
            "total_trades": 0, "win_rate": 0.0, "avg_return": 0.0,
            "cagr": 0.0, "sharpe": 0.0, "sortino": 0.0,
            "max_drawdown": 0.0, "profit_factor": 0.0,
        }

    arr = np.array(returns, dtype=float)
    n = len(arr)

    wins = arr[arr > 0]
    losses = arr[arr <= 0]

    win_rate = len(wins) / n * 100.0
    avg_return = float(np.mean(arr))

    # Equity curve (compound growth from 100)
    equity = 100.0
    peak = 100.0
    curve = [100.0]
    max_dd = 0.0
    for r in arr:
        equity *= (1.0 + r / 100.0)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100.0
        max_dd = max(max_dd, dd)
        curve.append(equity)

    # CAGR
    total_return = (curve[-1] - 100.0) / 100.0
    cagr = ((1.0 + total_return) ** (1.0 / max(period_years, 0.1)) - 1.0) * 100.0

    # Sharpe (annualised, assumes ~250 trades/year, risk-free rate = 6.5% India)
    RISK_FREE_ANNUAL = 6.5
    risk_free_per_trade = RISK_FREE_ANNUAL / 250.0
    excess = arr - risk_free_per_trade
    sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(250)) if np.std(excess) > 0 else 0.0

    # Sortino (only downside deviation)
    downside = arr[arr < risk_free_per_trade] - risk_free_per_trade
    sortino_denom = float(np.std(downside) * np.sqrt(250)) if len(downside) > 0 else 0.0
    sortino = float(np.mean(excess) * np.sqrt(250) / sortino_denom) if sortino_denom > 0 else 0.0

    # Profit Factor
    gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = abs(float(np.sum(losses))) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "total_trades": n,
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_return, 2),
        "cagr": round(cagr, 1),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown": round(max_dd, 1),
        "profit_factor": round(profit_factor, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


# ── Historical Data Helpers ──────────────────────────────────────────

def _fetch_history(symbol: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """Fetches OHLCV from yfinance for the given date range."""
    try:
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        logger.warning(f"Could not fetch {symbol}: {e}")
        return None


# ── Simplified Scoring for Backtest ─────────────────────────────────

def _simple_screen(df_hist: pd.DataFrame, nifty_hist: pd.DataFrame, as_of_date: str) -> Optional[float]:
    """
    Simplified screening logic for backtesting.
    Returns a score (0-100) or None if the stock fails basic filters.
    This mirrors the core alpha logic without live data dependencies.
    """
    try:
        as_of = pd.Timestamp(as_of_date)
        hist = df_hist[df_hist.index <= as_of].tail(100)
        nifty = nifty_hist[nifty_hist.index <= as_of].tail(100)

        if len(hist) < 50 or hist["Volume"].tail(20).mean() < 100_000:
            return None

        close = float(hist["Close"].iloc[-1])

        # Liquidity filter
        avg_turnover = float(hist["Close"].tail(20).mean() * hist["Volume"].tail(20).mean())
        if avg_turnover < 50_000_000:  # ₹5 Crore/day minimum for backtest
            return None

        # Price filter
        if close > config.MAX_STOCK_PRICE or close < config.MIN_STOCK_PRICE:
            return None

        # EMA alignment
        ema20 = float(hist["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(hist["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
        ema_aligned = ema20 > ema50

        # Downtrend rejection
        if not ema_aligned:
            return None

        # RSI
        delta = hist["Close"].diff()
        gain = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if loss.iloc[-1] != 0 else 50.0

        if rsi > 80 or rsi < 40:
            return None

        # RS vs Nifty (20d)
        if len(nifty) >= 21:
            stock_ret = (hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1) * 100
            nifty_ret = (nifty["Close"].iloc[-1] / nifty["Close"].iloc[-21] - 1) * 100
            rs_vs_nifty = stock_ret - nifty_ret
        else:
            rs_vs_nifty = 0.0

        # Volume ratio
        avg_vol = float(hist["Volume"].tail(20).mean())
        latest_vol = float(hist["Volume"].iloc[-1])
        vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

        # Simple score
        score = 50.0
        score += rs_vs_nifty * 2.0           # RS contribution
        score += (rsi - 50) * 0.5            # RSI contribution
        score += (vol_ratio - 1.0) * 10.0   # Volume contribution
        if ema_aligned:
            score += 10.0

        return round(min(100.0, max(0.0, score)), 1)

    except Exception:
        return None


def _get_forward_return(df_hist: pd.DataFrame, as_of_date: str, hold_days: int = 5) -> Optional[float]:
    """Gets the N-day forward return from as_of_date close."""
    try:
        as_of = pd.Timestamp(as_of_date)
        future = df_hist[df_hist.index > as_of].head(hold_days)
        if len(future) < hold_days:
            return None
        entry = float(df_hist[df_hist.index <= as_of]["Close"].iloc[-1])
        exit_price = float(future["Close"].iloc[-1])
        if entry <= 0:
            return None
        return round((exit_price - entry) / entry * 100.0, 2)
    except Exception:
        return None


# ── Walk-Forward Runner ──────────────────────────────────────────────

def run_window(
    window: Dict,
    symbols: List[str],
    top_n: int = 10,
    hold_days: int = 5,
    scan_frequency_days: int = 5,
) -> Dict:
    """
    Runs backtesting for a single walk-forward window.
    Scans every `scan_frequency_days` trading days in the test period.
    """
    label = window["label"]
    test_start = window["test_start"]
    test_end = window["test_end"]
    data_start = window["train_start"]  # Pull data from train start for indicator lookbacks

    print(f"\n{'='*60}")
    print(f"  Walk-Forward Window: {label}")
    print(f"  Test Period: {test_start} → {test_end}")
    print(f"{'='*60}")

    # 1. Fetch all data upfront (train + test)
    print(f"  Fetching market data for {len(symbols)} symbols...")
    nifty_full = _fetch_history("^NSEI", data_start, test_end)
    if nifty_full is None:
        logger.error("Could not fetch Nifty data. Skipping window.")
        return {"label": label, "error": "No Nifty data"}

    all_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _fetch_history(sym, data_start, test_end)
        if df is not None:
            all_data[sym] = df

    print(f"  {len(all_data)}/{len(symbols)} symbols loaded.")

    # 2. Build list of scan dates within test period
    test_dates = nifty_full[
        (nifty_full.index >= pd.Timestamp(test_start)) &
        (nifty_full.index <= pd.Timestamp(test_end))
    ].index.tolist()

    scan_dates = test_dates[::scan_frequency_days]
    print(f"  Scanning on {len(scan_dates)} dates (every {scan_frequency_days} trading days)...")

    all_trades: List[float] = []
    daily_trades = []

    for scan_date in scan_dates:
        scan_str = scan_date.strftime("%Y-%m-%d")
        candidates = []

        for sym, df in all_data.items():
            score = _simple_screen(df, nifty_full, scan_str)
            if score is not None and score >= 65.0:
                candidates.append({"symbol": sym, "score": score})

        # Sort by score, take top N
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top_candidates = candidates[:top_n]

        for cand in top_candidates:
            sym = cand["symbol"]
            df = all_data[sym]
            fwd_ret = _get_forward_return(df, scan_str, hold_days)
            if fwd_ret is not None:
                all_trades.append(fwd_ret)
                daily_trades.append({
                    "date": scan_str,
                    "symbol": sym,
                    "score": cand["score"],
                    "return": fwd_ret,
                })

    # 3. Compute metrics
    test_start_dt = datetime.strptime(test_start, "%Y-%m-%d")
    test_end_dt = datetime.strptime(min(test_end, date.today().isoformat()), "%Y-%m-%d")
    period_years = max(0.1, (test_end_dt - test_start_dt).days / 365.25)

    metrics = compute_metrics(all_trades, period_years)
    metrics["label"] = label
    metrics["test_start"] = test_start
    metrics["test_end"] = test_end
    metrics["scan_dates_count"] = len(scan_dates)
    metrics["symbols_tested"] = len(all_data)

    print(f"\n  ✅ Results for {label}:")
    print(f"     Trades:       {metrics['total_trades']}")
    print(f"     Win Rate:     {metrics['win_rate']:.1f}%")
    print(f"     Avg Return:   {metrics['avg_return']:+.2f}%")
    print(f"     CAGR:         {metrics['cagr']:+.1f}%")
    print(f"     Sharpe:       {metrics['sharpe']:.2f}")
    print(f"     Sortino:      {metrics['sortino']:.2f}")
    print(f"     Max DD:       {metrics['max_drawdown']:.1f}%")
    print(f"     Profit Factor: {metrics['profit_factor']:.2f}")

    return metrics


# ── Report Generator ─────────────────────────────────────────────────

def generate_report(all_results: List[Dict]) -> str:
    """Generates a markdown report comparing all walk-forward windows."""
    lines = [
        "# STALKER Walk-Forward Backtest Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Summary Across All Windows",
        "",
        "| Window | Trades | Win Rate | Avg Return | CAGR | Sharpe | Sortino | Max DD | Profit Factor |",
        "|--------|--------|----------|-----------|------|--------|---------|--------|---------------|",
    ]

    for r in all_results:
        if "error" in r:
            lines.append(f"| {r['label']} | ERROR | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {r['label']} | {r['total_trades']} | {r['win_rate']:.1f}% | "
            f"{r['avg_return']:+.2f}% | {r['cagr']:+.1f}% | "
            f"{r['sharpe']:.2f} | {r['sortino']:.2f} | "
            f"{r['max_drawdown']:.1f}% | {r['profit_factor']:.2f} |"
        )

    # Add interpretation
    valid = [r for r in all_results if "error" not in r and r["total_trades"] > 0]
    if valid:
        avg_sharpe = np.mean([r["sharpe"] for r in valid])
        avg_winrate = np.mean([r["win_rate"] for r in valid])
        avg_cagr = np.mean([r["cagr"] for r in valid])
        lines += [
            "",
            "## Overall Assessment",
            "",
            f"- **Average CAGR**: {avg_cagr:+.1f}%",
            f"- **Average Sharpe**: {avg_sharpe:.2f}",
            f"- **Average Win Rate**: {avg_winrate:.1f}%",
            "",
        ]
        if avg_sharpe > 1.5:
            lines.append("> ✅ **Strong**: Sharpe > 1.5 across walk-forward windows. Alpha is robust.")
        elif avg_sharpe > 0.8:
            lines.append("> ⚠️ **Moderate**: Sharpe 0.8–1.5. Alpha exists but needs refinement.")
        else:
            lines.append("> 🔴 **Weak**: Sharpe < 0.8. Review scoring logic before live deployment.")

        lines += [
            "",
            "> [!NOTE]",
            "> This is a simplified backtest using the core momentum/RS screen only.",
            "> Live results include fundamentals, quality, and institutional signals which cannot be fully replicated historically.",
            "> Use this as a sanity check, not a precise forecast.",
        ]

    return "\n".join(lines)


# ── Main Runner ───────────────────────────────────────────────────────

def run_backtest(
    symbols: Optional[List[str]] = None,
    windows: Optional[List[Dict]] = None,
    top_n: int = 10,
    hold_days: int = 5,
    verbose: bool = True,
) -> List[Dict]:
    """
    Runs the full walk-forward backtest.

    Args:
        symbols: Stock universe (defaults to Nifty 50)
        windows: Walk-forward windows (defaults to WALK_FORWARD_WINDOWS)
        top_n: Max picks per scan day
        hold_days: Forward return holding period in trading days
        verbose: Print progress

    Returns:
        List of result dicts (one per window)
    """
    symbols = symbols or config.NIFTY50_SYMBOLS  # Use Nifty 50 only — faster backtest
    windows = windows or WALK_FORWARD_WINDOWS

    print(f"\n{'═'*60}")
    print(f"  STALKER Walk-Forward Backtest Engine")
    print(f"  Universe: {len(symbols)} stocks | {len(windows)} windows")
    print(f"  Hold Period: {hold_days} trading days | Top {top_n} picks per scan")
    print(f"{'═'*60}")

    all_results = []
    for window in windows:
        try:
            result = run_window(window, symbols, top_n=top_n, hold_days=hold_days)
            all_results.append(result)
        except Exception as e:
            logger.error(f"Window {window['label']} failed: {e}")
            all_results.append({"label": window["label"], "error": str(e)})

    # Save JSON results
    results_path = os.path.join(config.DATA_DIR, "backtest_results.json")
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n💾 Results saved to {results_path}")

    # Generate and save markdown report
    report_md = generate_report(all_results)
    report_path = os.path.join(config.REPORTS_DIR, "backtest_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)
    print(f"📄 Report saved to {report_path}")

    return all_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Allow running a single window for quick validation
    import argparse
    parser = argparse.ArgumentParser(description="STALKER Walk-Forward Backtest Engine")
    parser.add_argument("--window", type=int, default=None, help="Run single window index (0-4)")
    parser.add_argument("--hold", type=int, default=5, help="Holding period in trading days")
    parser.add_argument("--top-n", type=int, default=10, help="Top N picks per scan")
    args = parser.parse_args()

    windows_to_run = WALK_FORWARD_WINDOWS
    if args.window is not None:
        windows_to_run = [WALK_FORWARD_WINDOWS[args.window]]

    run_backtest(
        symbols=config.NIFTY50_SYMBOLS,
        windows=windows_to_run,
        top_n=args.top_n,
        hold_days=args.hold,
    )
