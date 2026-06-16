# -*- coding: utf-8 -*-
"""
STALKER - Self-Correcting Quant Audit Engine
Runs at EOD (after EOD prices are recorded) to analyze today's predictions,
isolate false positives/mistakes, and compile a private reliability report.
Dispatched exclusively to harshkumawat9950@gmail.com (no CCs).
"""

import os
import sys
import io
import json
import logging
from datetime import datetime, date
import config
import db_manager
import main

# Force UTF-8 output on Windows — safe guard: never crash if buffer already replaced
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass  # In-process / thread context — stdout already safe

logger = logging.getLogger(__name__)


def analyze_factor_expectancy() -> tuple:
    """
    Loads all historical picks, filters for completed forward returns,
    and calculates factor correlations, rolling diagnostics, setup win rates, and warnings.
    Returns (warnings_html, html, suggestions_list)
    """
    import pandas as pd
    import numpy as np
    
    db = db_manager.get_db()
    records = []
    
    # 1. Load picks
    if db is not None:
        try:
            col = db[config.MONGO_COLLECTION_PICKS]
            records = list(col.find({}))
        except Exception:
            records = []
            
    # JSON fallback
    if not records or db is None:
        records = db_manager._read_json("daily_picks.json")
        
    if not records:
        return "", "<p style='color:#64748b; font-style:italic;'>No picks records found for expectancy analysis.</p>", []
        
    flat_picks = []
    for r in records:
        picks_list = r.get("picks", r.get("top_picks", []))
        for p in picks_list:
            if p.get("future_1d_return") is not None or p.get("intraday_return") is not None or p.get("future_5d_return") is not None or p.get("future_3d_return") is not None:
                p_copy = dict(p)
                p_copy["date"] = r.get("date")
                flat_picks.append(p_copy)
                
    if not flat_picks:
        return "", "<p style='color:#64748b; font-style:italic;'>No picks with completed forward returns available yet. Expectancy metrics will populate as trades mature (3+ trading days).</p>", []
        
    df = pd.DataFrame(flat_picks)
    # Sort chronologically
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    
    # Clean and parse factor columns
    factor_cols = {
        "rs_rank": "Relative Strength Score",
        "structure_score": "Structure Score",
        "technical_score": "Technical Score",
        "institutional_score": "Institutional Score",
        "fundamental_score": "Fundamental Score",
        "earnings_score": "Earnings Score",
        "sector_rank": "Sector Rank",
        "opportunity_score": "Opportunity Score",
        "expectancy_score": "Expectancy Score",
        "liquidity_score": "Liquidity Score"
    }
    
    for col in factor_cols.keys():
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    return_cols = ["intraday_return", "future_1d_return", "future_3d_return", "future_5d_return", "future_10d_return", "future_20d_return"]
    for col in return_cols:
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    target_ret = "future_1d_return" if df["future_1d_return"].notna().sum() >= 5 \
        else ("intraday_return" if df["intraday_return"].notna().sum() >= 5 \
        else ("future_5d_return" if df["future_5d_return"].notna().sum() >= 5 else "future_3d_return"))
    valid_count = df[target_ret].notna().sum()
    
    # Calculate net returns by deducting round-trip transaction costs
    slippage_pct = getattr(config, "SLIPPAGE_PCT", 0.0010)
    brokerage_pct = getattr(config, "BROKERAGE_PCT", 0.0005)
    txn_cost_pct = 2.0 * (slippage_pct + brokerage_pct) * 100.0
    df["net_target_return"] = df[target_ret] - txn_cost_pct
    
    html = ""
    warnings_html = ""
    suggestions_list = []
    
    # ── 1. ROLLING PERFORMANCE DIAGNOSTICS & DRAWDOWN (last 30 trades) ──
    if valid_count >= 5:
        window_size = min(30, valid_count)
        df["win"] = (df["net_target_return"] > 0).astype(int)
        
        rolling_wr = df["win"].rolling(window=window_size, min_periods=5).mean() * 100.0
        rolling_ret = df["net_target_return"].rolling(window=window_size, min_periods=5).mean()
        
        # Compound growth of a mock 100 capital
        equity_curve = (1.0 + df["net_target_return"] / 100.0).cumprod()
        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        rolling_max_dd = drawdown.rolling(window=window_size, min_periods=5).min() * 100.0
        
        latest_wr = rolling_wr.iloc[-1]
        latest_expectancy = rolling_ret.iloc[-1]
        latest_max_dd = rolling_max_dd.iloc[-1]
        
        # Expectancy Warning Banner
        if latest_expectancy < 0:
            warnings_html += f"""
            <div style='background-color:#fee2e2; border-left:4px solid #ef4444; padding:12px 16px; border-radius:8px; margin-bottom:16px; color:#991b1b;'>
                <strong style='font-size:14px; display:block; margin-bottom:4px;'>⚠️ SYSTEM EXPECTANCY WARNING</strong>
                <p style='margin:0; font-size:13px; line-height:1.4;'>Rolling {window_size}-trade net expectancy has dropped to <strong>{latest_expectancy:+.2f}%</strong>. The system is experiencing high failure rates or excessive transaction costs relative to profit margins. Consider tightening risk limits or scaling back position sizes.</p>
            </div>
            """
            suggestions_list.append({
                "topic": "Negative Expectancy Halt",
                "suggestion": f"Halve position sizing immediately as rolling {window_size}-trade expectancy is negative ({latest_expectancy:.2f}%)."
            })
            
        html += f"""
        <div style='margin-top:20px; padding:16px; background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;'>
            <h3 style='color:#0f172a; border-bottom:2px solid #cbd5e1; padding-bottom:6px; margin-top:0; font-size:15px;'>📈 Rolling Performance Diagnostics (Last {window_size} Trades)</h3>
            <table style='width:100%; border-collapse:collapse; font-size:12px; text-align:left; margin-bottom:8px;'>
                <tr style='background-color:#e2e8f0; color:#475569; font-weight:bold;'>
                    <th style='padding:6px;'>Metric</th>
                    <th style='padding:6px; text-align:right;'>Value</th>
                </tr>
                <tr style='border-bottom:1px solid #e2e8f0;'>
                    <td style='padding:6px; font-weight:500;'>Rolling Win Rate</td>
                    <td style='padding:6px; text-align:right; font-weight:bold;'>{latest_wr:.1f}%</td>
                </tr>
                <tr style='border-bottom:1px solid #e2e8f0;'>
                    <td style='padding:6px; font-weight:500;'>Rolling Net Expectancy</td>
                    <td style='padding:6px; text-align:right; font-weight:bold; color:{"#16a34a" if latest_expectancy >= 0 else "#dc2626"};'>{latest_expectancy:+.2f}%</td>
                </tr>
                <tr style='border-bottom:1px solid #e2e8f0;'>
                    <td style='padding:6px; font-weight:500;'>Rolling Max Drawdown</td>
                    <td style='padding:6px; text-align:right; font-weight:bold; color:#dc2626;'>{latest_max_dd:.2f}%</td>
                </tr>
            </table>
        </div>
        """
        
    # ── 2. ROLLING INFORMATION COEFFICIENT (IC) & DECAY ──
    degraded_factors = []
    ic_rows = ""
    
    if valid_count >= 5:
        window_size = min(30, valid_count)
        for col, label in factor_cols.items():
            if df[col].notna().sum() >= 5:
                rolling_ic = df[col].rolling(window=window_size, min_periods=5).corr(df["net_target_return"])
                
                # Check for decay: rolling IC < 0 for the last 3 consecutive steps
                if len(rolling_ic) >= 3:
                    last_3 = rolling_ic.tail(3).values
                    if np.all(last_3 < 0.0):
                        degraded_factors.append(label)
                        suggestions_list.append({
                            "topic": "Factor Decay Notification",
                            "suggestion": f"Factor '{label}' Rolling IC has dropped below zero consistently over the last 3 evaluations. Recommend decreasing its allocation weight."
                        })
                
                current_ic = rolling_ic.iloc[-1]
                if not np.isnan(current_ic):
                    color = "#16a34a" if current_ic > 0.05 else "#dc2626" if current_ic < -0.05 else "#475569"
                    status = "🔴 DEGRADED" if label in degraded_factors else "🟢 Active"
                    ic_rows += f"<tr style='border-bottom:1px solid #e2e8f0;'><td style='padding:6px; font-weight:500;'>{label}</td><td style='padding:6px; text-align:right; font-weight:bold; color:{color};'>{current_ic:+.2f}</td><td style='padding:6px; text-align:center;'>{status}</td></tr>"

    if ic_rows:
        html += f"""
        <div style='margin-top:20px; padding:16px; background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;'>
            <h3 style='color:#0f172a; border-bottom:2px solid #cbd5e1; padding-bottom:6px; margin-top:0; font-size:15px;'>📉 Factor Information Coefficient (Rolling {window_size}-Trade IC)</h3>
            <table style='width:100%; border-collapse:collapse; font-size:12px; text-align:left;'>
                <tr style='background-color:#e2e8f0; color:#475569; font-weight:bold;'>
                    <th style='padding:6px;'>Factor Name</th>
                    <th style='padding:6px; text-align:right;'>Rolling Correlation</th>
                    <th style='padding:6px; text-align:center;'>Status</th>
                </tr>
                {ic_rows}
            </table>
        </div>
        """
        
    # ── 3. FACTOR ORTHOGONALITY CHECK ──
    ortho_warnings = ""
    valid_factor_cols = [c for c in factor_cols.keys() if c in df.columns and df[c].notna().sum() >= 5]
    if len(valid_factor_cols) >= 2:
        corr_df = df[valid_factor_cols].corr()
        high_corr_pairs = []
        
        for i, col1 in enumerate(corr_df.columns):
            for j, col2 in enumerate(corr_df.columns):
                if i < j:
                    r_val = corr_df.loc[col1, col2]
                    if not np.isnan(r_val) and abs(r_val) > 0.80:
                        high_corr_pairs.append((factor_cols[col1], factor_cols[col2], r_val))
                        
        if high_corr_pairs:
            ortho_warnings += "<ul style='margin:0; padding-left:20px; font-size:12px; color:#b45309;'>"
            for f1, f2, r in high_corr_pairs:
                ortho_warnings += f"<li><strong>{f1}</strong> & <strong>{f2}</strong> correlation is {r:+.2f}. High double-counting risk.</li>"
            ortho_warnings += "</ul>"
            
            warnings_html += f"""
            <div style='background-color:#fffbeb; border-left:4px solid #f59e0b; padding:12px 16px; border-radius:8px; margin-bottom:16px; color:#b45309;'>
                <strong style='font-size:14px; display:block; margin-bottom:4px;'>⚠️ FACTOR ORTHOGONALITY ALERT</strong>
                <p style='margin:0 0 8px 0; font-size:13px; line-height:1.4;'>High cross-correlation (>0.80) detected among active scoring factors. This indicates redundant information and overlap, artificially inflating setup scores:</p>
                {ortho_warnings}
            </div>
            """
        
    # ── 4. STANDARD FACTOR EXPECTANCY TABLE (Historical pearson r) ──
    html += "<div style='margin-top:20px; padding:16px; background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;'>"
    html += "<h3 style='color:#0f172a; border-bottom:2px solid #cbd5e1; padding-bottom:6px; margin-top:0; font-size:15px;'>📊 Static Factor-Return Correlation</h3>"
    
    corrs = []
    for col, label in factor_cols.items():
        if df[col].notna().sum() >= 3:
            r = df[col].corr(df["net_target_return"])
            if not np.isnan(r):
                corrs.append((label, r))
                
    corrs.sort(key=lambda x: abs(x[1]), reverse=True)
    
    if corrs:
        html += f"<p style='margin:0 0 10px 0; font-size:13px; color:#475569;'><strong>Factor Correlation with {target_ret.replace('_', ' ').title()}</strong> (based on {valid_count} matured trades):</p>"
        html += "<table style='width:100%; border-collapse:collapse; font-size:12px; text-align:left; margin-bottom:16px;'>"
        html += "<tr style='background-color:#e2e8f0; color:#475569; font-weight:bold;'><th style='padding:6px;'>Factor Name</th><th style='padding:6px; text-align:right;'>Pearson r</th><th style='padding:6px;'>Predictive Class</th></tr>"
        
        for label, r in corrs:
            power = "Strong Edge" if abs(r) >= 0.3 else "Moderate Edge" if abs(r) >= 0.15 else "Weak/Noise"
            color = "#16a34a" if r > 0.05 else "#dc2626" if r < -0.05 else "#475569"
            html += f"<tr style='border-bottom:1px solid #e2e8f0;'><td style='padding:6px; font-weight:500;'>{label}</td><td style='padding:6px; text-align:right; font-weight:bold; color:{color};'>{r:+.2f}</td><td style='padding:6px; color:#64748b;'>{power}</td></tr>"
        html += "</table>"
    else:
        html += f"<p style='margin:0 0 10px 0; font-size:12px; color:#64748b; font-style:italic;'>Collecting more trade outcomes... (Need at least 3 completed trades to compute correlation. Active count: {valid_count})</p>"
        
    # Setup Expectancy (trade_type)
    trade_type_col = "trade_type" if "trade_type" in df.columns else "setup_type" if "setup_type" in df.columns else None
    if trade_type_col and df[trade_type_col].notna().sum() > 0:
        html += "<p style='margin:12px 0 8px 0; font-size:13px; color:#475569;'><strong>Setup-Specific Expectancy:</strong></p>"
        html += "<table style='width:100%; border-collapse:collapse; font-size:12px; text-align:left;'>"
        html += "<tr style='background-color:#e2e8f0; color:#475569; font-weight:bold;'><th style='padding:6px;'>Setup</th><th style='padding:6px; text-align:center;'>Count</th><th style='padding:6px; text-align:right;'>Win Rate</th><th style='padding:6px; text-align:right;'>Avg 5d Return</th></tr>"
        
        groups = df.groupby(trade_type_col)
        for name, group in groups:
            n_trades = len(group)
            valid_returns = group["net_target_return"].dropna()
            if len(valid_returns) > 0:
                wins = (valid_returns > 0).sum()
                win_rate = (wins / len(valid_returns)) * 100.0
                avg_ret = valid_returns.mean()
                html += f"<tr style='border-bottom:1px solid #e2e8f0;'><td style='padding:6px; font-weight:bold;'>{name}</td><td style='padding:6px; text-align:center;'>{n_trades}</td><td style='padding:6px; text-align:right;'>{win_rate:.1f}%</td><td style='padding:6px; text-align:right; font-weight:bold; color:{'#16a34a' if avg_ret >=0 else '#dc2626'};'>{avg_ret:+.2f}%</td></tr>"
        html += "</table>"
        
    html += "</div>"
    return warnings_html, html, suggestions_list


def run_mistakes_audit():
    logger.info("Starting STALKER Self-Correcting mistakes audit...")
    try:
        db_manager.update_past_picks_returns()
    except Exception as e:
        logger.error(f"Failed to update past picks returns: {e}")

    today = str(date.today())

    # Get EOD report data
    eod_data = db_manager.get_prices_for_date(today)
    open_prices = eod_data.get("open", {})
    close_prices = eod_data.get("close", {})
    
    today_picks_data = db_manager.get_today_picks()
    if not today_picks_data:
        logger.warning("[AUDIT] No picks data found for today. Skipping audit.")
        return

    picks = today_picks_data.get("picks", today_picks_data.get("top_picks", []))
    if not picks:
        logger.warning("[AUDIT] Empty picks list. Skipping audit.")
        return

    nifty_trend = today_picks_data.get("market_trend", "neutral").upper()

    # Retrieve Nifty EOD
    import yfinance as yf
    nifty_chg_pct = 0.0
    try:
        nifty = yf.Ticker("^NSEI").history(period="1d")
        if not nifty.empty:
            nifty_open = float(nifty['Open'].iloc[-1])
            nifty_close = float(nifty['Close'].iloc[-1])
            nifty_chg_pct = ((nifty_close - nifty_open) / nifty_open) * 100.0
    except Exception as ne:
        logger.warning(f"Failed to fetch Nifty details for audit: {ne}")

    hits = []
    mistakes = []
    watchlist_perf = []
    
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0
    active_count = 0

    for pick in picks:
        symbol = pick.get("symbol")
        name = pick.get("name", symbol)
        action = pick.get("action", "WATCH")
        alpha = pick.get("alpha_score") or pick.get("total_score") or 0.0
        
        open_entry = open_prices.get(symbol, {})
        close_entry = close_prices.get(symbol, {})
        
        open_p = open_entry.get("open") or open_entry.get("current_price") or pick.get("current_price")
        close_p = close_entry.get("close") or close_entry.get("current_price")
        
        if not open_p or not close_p:
            continue

        pnl_pct = ((close_p - open_p) / open_p) * 100.0
        
        # Trailing stop-loss for high-beta stocks:
        # If beta > 1.2 and price rises by >= 2% from open, stop-loss trails to cost (open_p).
        # Exits at cost if price hits low_p <= open_p or close_p < open_p.
        hit_trailing_sl = False
        try:
            import data_fetcher
            fund = data_fetcher.fetch_fundamentals(symbol)
            beta = fund.get("beta")
            is_high_beta = beta is not None and beta > 1.2
            if is_high_beta and open_p and open_p > 0:
                high_val = close_entry.get("high") or close_p
                low_val = close_entry.get("low") or close_p
                if high_val >= open_p * 1.02:
                    if low_val <= open_p or close_p < open_p:
                        hit_trailing_sl = True
                        pnl_pct = 0.0
        except Exception as fe:
            logger.debug(f"Failed to evaluate trailing stop loss for {symbol}: {fe}")

        total_pnl += pnl_pct
        active_count += 1
        
        target = pick.get("target_2") or (open_p * 1.10)
        stop_loss = pick.get("stop_loss") or (open_p * 0.95)

        # Classify Outcomes
        outcome = "Neutral"
        details = ""

        if action == "BUY":
            if pnl_pct > 0 or hit_trailing_sl:
                outcome = "HIT"
                total_wins += 1
                hits.append({
                    "name": name,
                    "symbol": symbol,
                    "pnl": pnl_pct,
                    "open": open_p,
                    "close": close_p,
                    "alpha": alpha,
                    "details": "Trailing stop loss protected capital at cost." if hit_trailing_sl else "Bullish target captured or closed in profit."
                })
            else:
                outcome = "MISTAKE"
                total_losses += 1
                
                # Diagnose the mistake
                reason = "Trend reversal under Nifty weakness."
                if nifty_chg_pct > 0.5:
                    reason = "Failed to participate in Nifty rally (relative weakness)."
                
                audit = pick.get("validation_audit", {})
                dq = audit.get("data_quality", 100)
                risk = audit.get("risk", 0.0)
                inst = audit.get("institutional", 80)
                fundamentals = audit.get("fundamentals", 80)

                if risk > 4.2:
                    reason = "Extreme ATR volatility triggered early stop-loss."
                elif inst < 65:
                    reason = "Weak institutional volume support."
                elif fundamentals < 60:
                    reason = "Underlying weak fundamentals (High leverage or low margins)."

                mistakes.append({
                    "name": name,
                    "symbol": symbol,
                    "pnl": pnl_pct,
                    "open": open_p,
                    "close": close_p,
                    "alpha": alpha,
                    "reason": reason,
                    "risk": risk,
                    "dq": dq
                })
        else:
            # Watchlist performance mapping
            outcome = "WATCH_STAY"
            if pnl_pct > 2.0:
                outcome = "MISSED_OPPORTUNITY"
                details = "WATCH signal outperformed. Consider lowering Adjusted Alpha BUY trigger."
            elif pnl_pct < -1.0:
                details = "Correctly avoided forced buy. Watchlist preserved capital."
            else:
                details = "Stock traded sideways, matching WATCH expectations."

            watchlist_perf.append({
                "name": name,
                "symbol": symbol,
                "pnl": pnl_pct,
                "open": open_p,
                "close": close_p,
                "alpha": alpha,
                "outcome": outcome,
                "details": details
            })

    win_rate = (total_wins / (total_wins + total_losses) * 100) if (total_wins + total_losses) > 0 else 0.0
    avg_pnl = (total_pnl / active_count) if active_count > 0 else 0.0

    # ── STRUCTURAL SUGGESTIONS ENGINE ─────────────────────────
    suggestions = []
    
    # Suggestion 1: Regime Adaptability
    if nifty_trend == "NEUTRAL" and total_losses > 0:
        suggestions.append({
            "topic": "Context Gating Optimization",
            "suggestion": "In sideways/neutral markets, raise the BUY threshold to 72 (currently 70) to filter out highly marginal candidates."
        })
    elif nifty_trend == "BEAR" and avg_pnl < -1.0:
        suggestions.append({
            "topic": "Bear Market Gating",
            "suggestion": "Impose a strict -15 point penalty on all non-defense sectors during Bear market regimes."
        })

    # Suggestion 2: Volatility Limits
    high_vol_failures = sum(1 for m in mistakes if m["risk"] > 4.0)
    if high_vol_failures >= 1:
        suggestions.append({
            "topic": "Volatility Gating",
            "suggestion": "Tighten the Stage 1 Hard Risk filter to discard any stocks with Risk Score > 4.2 (currently 5.0) on choppy days."
        })

    # Suggestion 3: Liquidity Safety
    for m in mistakes:
        if m["pnl"] < -3.0:
            suggestions.append({
                "topic": "Stop-Loss Protection",
                "suggestion": "For high-beta stocks, implement a trailing stop-loss that activates once price goes 2% in profit."
            })
            break

    # Run factor expectancy analysis
    warnings_html = ""
    expectancy_html = ""
    try:
        warnings_html, expectancy_html, factor_suggestions = analyze_factor_expectancy()
        suggestions.extend(factor_suggestions)
    except Exception as e:
        logger.error(f"Failed to run factor expectancy analysis: {e}")
        expectancy_html = f"<p>Error in expectancy analysis: {e}</p>"

    # ── EMAIL DISPATCH ────────────────────────────────────────
    subject = f"🧠 STALKER Self-Correcting mistakes Audit & Reliability Strategy — {today}"
    
    hits_rows = "".join(f"""
    <tr style="border-bottom:1px solid #e2e8f0;">
        <td class="stock-name-cell" style="padding:10px;font-weight:bold;color:#1e293b;">{h['name']}</td>
        <td data-label="P&L %" style="padding:10px;text-align:right;color:#16a34a;font-weight:bold;">{h['pnl']:+.2f}%</td>
        <td data-label="Price Run" style="padding:10px;text-align:right;color:#64748b;">₹{h['open']:.2f} ➔ ₹{h['close']:.2f}</td>
        <td data-label="Alpha" style="padding:10px;text-align:center;color:#475569;">{h['alpha']:.1f}</td>
        <td data-label="Outcome" style="padding:10px;color:#475569;font-size:12px;">{h['details']}</td>
    </tr>
    """ for h in hits)

    mistakes_rows = "".join(f"""
    <tr style="border-bottom:1px solid #e2e8f0;background-color:#fffbeb;">
        <td class="stock-name-cell" style="padding:10px;font-weight:bold;color:#991b1b;">{m['name']}</td>
        <td data-label="P&L %" style="padding:10px;text-align:right;color:#dc2626;font-weight:bold;">{m['pnl']:+.2f}%</td>
        <td data-label="Price Run" style="padding:10px;text-align:right;color:#64748b;">₹{m['open']:.2f} ➔ ₹{m['close']:.2f}</td>
        <td data-label="Alpha" style="padding:10px;text-align:center;color:#475569;">{m['alpha']:.1f}</td>
        <td data-label="Root Cause" style="padding:10px;color:#991b1b;font-size:12px;font-weight:500;">❌ {m['reason']}</td>
    </tr>
    """ for m in mistakes)

    watch_rows = "".join(f"""
    <tr style="border-bottom:1px solid #e2e8f0;">
        <td class="stock-name-cell" style="padding:10px;font-weight:bold;color:#475569;">{w['name']}</td>
        <td data-label="P&L %" style="padding:10px;text-align:right;color:{'#16a34a' if w['pnl'] > 0 else '#dc2626'};">{w['pnl']:+.2f}%</td>
        <td data-label="Alpha" style="padding:10px;text-align:center;color:#475569;">{w['alpha']:.1f}</td>
        <td data-label="Audit Details" style="padding:10px;color:#64748b;font-size:12px;">{w['details']}</td>
    </tr>
    """ for w in watchlist_perf)

    suggestions_html = "".join(f"""
    <div style="background-color:#f8fafc;border-left:4px solid #f97316;padding:12px 16px;border-radius:4px;margin-bottom:14px;text-align:left;">
        <strong style="color:#c2410c;font-size:14px;display:block;margin-bottom:4px;">💡 {s['topic']}</strong>
        <p style="margin:0;font-size:13px;color:#334155;line-height:1.5;">{s['suggestion']}</p>
    </div>
    """ for s in suggestions)

    html_body = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            @media screen and (max-width: 600px) {{
                body {{
                    padding: 10px !important;
                }}
                .email-container {{
                    border-radius: 8px !important;
                    box-shadow: none !important;
                }}
                .body-padding {{
                    padding: 16px !important;
                }}
                .mobile-stack {{
                    display: block !important;
                    width: 100% !important;
                }}
                .mobile-card {{
                    display: block !important;
                    width: 100% !important;
                    margin-bottom: 12px !important;
                    box-sizing: border-box !important;
                }}
                .mobile-flex-row {{
                    display: block !important;
                    width: 100% !important;
                }}
                .mobile-text-right {{
                    text-align: left !important;
                    margin-top: 8px !important;
                    display: block !important;
                }}
                /* Responsive Table to Cards */
                .responsive-table {{
                    width: 100% !important;
                    min-width: 100% !important;
                }}
                .responsive-table thead {{
                    display: none !important;
                }}
                .responsive-table tbody,
                .responsive-table tr,
                .responsive-table td {{
                    display: block !important;
                    width: 100% !important;
                    box-sizing: border-box !important;
                }}
                .responsive-table tr {{
                    margin-bottom: 16px !important;
                    border: 1px solid #e2e8f0 !important;
                    border-radius: 12px !important;
                    padding: 14px !important;
                    background-color: #ffffff !important;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05) !important;
                }}
                .responsive-table tr:last-child {{
                    margin-bottom: 0 !important;
                }}
                .responsive-table td {{
                    text-align: left !important;
                    padding: 8px 0 !important;
                    border: none !important;
                    border-bottom: 1px dashed #f1f5f9 !important;
                    display: flex !important;
                    justify-content: space-between !important;
                    align-items: center !important;
                }}
                .responsive-table td:last-child {{
                    border-bottom: none !important;
                    padding-bottom: 0 !important;
                }}
                .responsive-table td:first-child {{
                    padding-top: 0 !important;
                }}
                .responsive-table td.stock-name-cell {{
                    display: block !important;
                    font-size: 15px !important;
                    font-weight: 800 !important;
                    color: #0f172a !important;
                    border-bottom: 2px solid #e2e8f0 !important;
                    padding-bottom: 8px !important;
                    margin-bottom: 8px !important;
                    text-align: left !important;
                }}
                .responsive-table td.stock-name-cell::before {{
                    content: "" !important;
                }}
                .responsive-table td[data-label]::before {{
                    content: attr(data-label) !important;
                    font-weight: 700 !important;
                    color: #64748b !important;
                    font-size: 11px !important;
                    text-transform: uppercase !important;
                    letter-spacing: 0.5px !important;
                }}
            }}
        </style>
    </head>
    <body style="font-family:'Segoe UI',sans-serif;background-color:#f1f5f9;padding:20px;margin:0;">
        <div style="max-width:740px;margin:0 auto;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);border:1px solid #e2e8f0;" class="email-container">
            <!-- Header -->
            <div style="background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);padding:28px 24px;text-align:center;color:#ffffff;">
                <h1 style="margin:0;font-size:24px;font-weight:800;letter-spacing:0.5px;">🧠 STALKER CORE DIAGNOSTICS & AUDIT</h1>
                <p style="margin:6px 0 0 0;opacity:0.9;font-size:13px;font-weight:500;">Private EOD Self-Correction Report · Owner Eyes Only</p>
            </div>
            
            <!-- Summary Stats -->
            <div style="padding:24px;" class="body-padding">
                {warnings_html}
                <div style="display:flex;gap:15px;margin-bottom:24px;" class="mobile-stack">
                    <div style="flex:1;padding:12px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;text-align:center;" class="mobile-card">
                        <div style="font-size:10px;color:#64748b;font-weight:bold;text-transform:uppercase;margin-bottom:4px;">Audit Win Rate</div>
                        <div style="font-size:18px;font-weight:800;color:#1e3b8a;">{win_rate:.1f}%</div>
                    </div>
                    <div style="flex:1;padding:12px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;text-align:center;" class="mobile-card">
                        <div style="font-size:10px;color:#64748b;font-weight:bold;text-transform:uppercase;margin-bottom:4px;">Avg P&L vs Open</div>
                        <div style="font-size:18px;font-weight:800;color:{'#16a34a' if avg_pnl >=0 else '#dc2626'};">{avg_pnl:+.2f}%</div>
                    </div>
                    <div style="flex:1;padding:12px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;text-align:center;" class="mobile-card">
                        <div style="font-size:10px;color:#64748b;font-weight:bold;text-transform:uppercase;margin-bottom:4px;">Diagnosed Mistakes</div>
                        <div style="font-size:18px;font-weight:800;color:#dc2626;">{len(mistakes)}</div>
                    </div>
                </div>

                <!-- 1. Suggestions Box -->
                <h3 style="color:#0f172a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:0;margin-bottom:14px;font-size:16px;">🛠️ Quantitative Recommendations for Reliability</h3>
                {suggestions_html}

                <!-- Expectancy Analysis Engine -->
                {expectancy_html}

                <!-- 2. Hits Table -->
                {f'''
                <h3 style="color:#16a34a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:24px;margin-bottom:12px;font-size:15px;">🟢 Successful Predictions Today ({len(hits)})</h3>
                <table class="responsive-table" style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin-bottom:24px;">
                    <thead>
                        <tr style="background-color:#f8fafc;color:#64748b;font-weight:bold;border-bottom:1px solid #e2e8f0;">
                            <th style="padding:8px;">Stock</th>
                            <th style="padding:8px;text-align:right;">P&L %</th>
                            <th style="padding:8px;text-align:right;">Price Run</th>
                            <th style="padding:8px;text-align:center;">Alpha</th>
                            <th style="padding:8px;">Outcome Analysis</th>
                        </tr>
                    </thead>
                    <tbody>
                        {hits_rows}
                    </tbody>
                </table>
                ''' if hits else ''}

                <!-- 3. Mistakes Table -->
                {f'''
                <h3 style="color:#dc2626;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:24px;margin-bottom:12px;font-size:15px;">🔴 Diagnosed System Mistakes & False Signals ({len(mistakes)})</h3>
                <table class="responsive-table" style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin-bottom:24px;">
                    <thead>
                        <tr style="background-color:#fff5f5;color:#991b1b;font-weight:bold;border-bottom:1px solid #fecaca;">
                            <th style="padding:8px;">Stock</th>
                            <th style="padding:8px;text-align:right;">P&L %</th>
                            <th style="padding:8px;text-align:right;">Price Run</th>
                            <th style="padding:8px;text-align:center;">Alpha</th>
                            <th style="padding:8px;">Root Cause Failure Diagnostics</th>
                        </tr>
                    </thead>
                    <tbody>
                        {mistakes_rows}
                    </tbody>
                </table>
                ''' if mistakes else ''}

                <!-- 4. Watchlist Table -->
                {f'''
                <h3 style="color:#475569;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:24px;margin-bottom:12px;font-size:15px;">🟡 Watchlist Performance & Capital Preservation ({len(watchlist_perf)})</h3>
                <table class="responsive-table" style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin-bottom:12px;">
                    <thead>
                        <tr style="background-color:#f8fafc;color:#64748b;font-weight:bold;border-bottom:1px solid #e2e8f0;">
                            <th style="padding:8px;">Stock</th>
                            <th style="padding:8px;text-align:right;">P&L %</th>
                            <th style="padding:8px;text-align:center;">Alpha</th>
                            <th style="padding:8px;">Safety / Opportunity Audit</th>
                        </tr>
                    </thead>
                    <tbody>
                        {watch_rows}
                    </tbody>
                </table>
                ''' if watchlist_perf else ''}
            </div>

            <!-- Footer -->
            <div style="background-color:#f9fafb;padding:20px;text-align:center;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;">
                <p style="margin:0;">This is a system-generated quant diagnostics log strictly sent to <strong>harshkumawat9950@gmail.com</strong>.</p>
                <p style="margin:4px 0 0 0;">All subscriber CC lists are suppressed for this audit pipeline.</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Trigger sending with CC suppressed.
    # Note: We pass admin_only=True to _send_via_brevo which naturally suppresses the CC list,
    # so we do not mutate os.environ["FORMSUBMIT_CC"] or main.IS_TEST_MODE globally.
    logger.info("Dispatching private EOD self-correction audit mail to owner...")
    main._send_via_brevo(subject, html_body, admin_only=True)
    logger.info("[OK] Mistakes audit dispatch complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_mistakes_audit()
