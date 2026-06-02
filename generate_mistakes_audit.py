# -*- coding: utf-8 -*-
"""
STALKER - Self-Correcting Quant Audit Engine
Runs at EOD (after EOD prices are recorded) to analyze today's predictions,
isolate false positives/mistakes, and compile a private reliability report.
Dispatched exclusively to harshkumawat9950@gmail.com (no CCs).
"""

import os
import sys
import json
import logging
from datetime import datetime, date
import config
import db_manager
import main

# Force UTF-8 output
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

logger = logging.getLogger(__name__)


def run_mistakes_audit():
    logger.info("Starting STALKER Self-Correcting mistakes audit...")
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
        total_pnl += pnl_pct
        active_count += 1
        
        target = pick.get("target_2") or (open_p * 1.10)
        stop_loss = pick.get("stop_loss") or (open_p * 0.95)

        # Classify Outcomes
        outcome = "Neutral"
        details = ""

        if action == "BUY":
            if pnl_pct > 0:
                outcome = "HIT"
                total_wins += 1
                hits.append({
                    "name": name,
                    "symbol": symbol,
                    "pnl": pnl_pct,
                    "open": open_p,
                    "close": close_p,
                    "alpha": alpha,
                    "details": "Bullish target captured or closed in profit."
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

    # If no suggestions compiled, add general optimization advice
    if not suggestions:
        suggestions.append({
            "topic": "Model Balance",
            "suggestion": "The 7 scoring modules operated with high accuracy. No structural modifications required."
        })

    # ── EMAIL DISPATCH ────────────────────────────────────────
    subject = f"🧠 STALKER Self-Correcting mistakes Audit & Reliability Strategy — {today}"
    
    hits_rows = "".join(f"""
    <tr style="border-bottom:1px solid #e2e8f0;">
        <td style="padding:10px;font-weight:bold;color:#1e293b;">{h['name']}</td>
        <td style="padding:10px;text-align:right;color:#16a34a;font-weight:bold;">{h['pnl']:+.2f}%</td>
        <td style="padding:10px;text-align:right;color:#64748b;">₹{h['open']:.2f} ➔ ₹{h['close']:.2f}</td>
        <td style="padding:10px;text-align:center;color:#475569;">{h['alpha']:.1f}</td>
        <td style="padding:10px;color:#475569;font-size:12px;">{h['details']}</td>
    </tr>
    """ for h in hits)

    mistakes_rows = "".join(f"""
    <tr style="border-bottom:1px solid #e2e8f0;background-color:#fffbeb;">
        <td style="padding:10px;font-weight:bold;color:#991b1b;">{m['name']}</td>
        <td style="padding:10px;text-align:right;color:#dc2626;font-weight:bold;">{m['pnl']:+.2f}%</td>
        <td style="padding:10px;text-align:right;color:#64748b;">₹{m['open']:.2f} ➔ ₹{m['close']:.2f}</td>
        <td style="padding:10px;text-align:center;color:#475569;">{m['alpha']:.1f}</td>
        <td style="padding:10px;color:#991b1b;font-size:12px;font-weight:500;">❌ {m['reason']}</td>
    </tr>
    """ for m in mistakes)

    watch_rows = "".join(f"""
    <tr style="border-bottom:1px solid #e2e8f0;">
        <td style="padding:10px;font-weight:bold;color:#475569;">{w['name']}</td>
        <td style="padding:10px;text-align:right;color:{'#16a34a' if w['pnl'] > 0 else '#dc2626'};">{w['pnl']:+.2f}%</td>
        <td style="padding:10px;text-align:center;color:#475569;">{w['alpha']:.1f}</td>
        <td style="padding:10px;color:#64748b;font-size:12px;">{w['details']}</td>
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
    <body style="font-family:'Segoe UI',sans-serif;background-color:#f1f5f9;padding:20px;margin:0;">
        <div style="max-width:740px;margin:0 auto;background-color:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,0.08);border:1px solid #e2e8f0;">
            <!-- Header -->
            <div style="background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);padding:28px 24px;text-align:center;color:#ffffff;">
                <h1 style="margin:0;font-size:24px;font-weight:800;letter-spacing:0.5px;">🧠 STALKER CORE DIAGNOSTICS & AUDIT</h1>
                <p style="margin:6px 0 0 0;opacity:0.9;font-size:13px;font-weight:500;">Private EOD Self-Correction Report · Owner Eyes Only</p>
            </div>
            
            <!-- Summary Stats -->
            <div style="padding:24px;">
                <div style="display:flex;gap:15px;margin-bottom:24px;">
                    <div style="flex:1;padding:12px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;text-align:center;">
                        <div style="font-size:10px;color:#64748b;font-weight:bold;text-transform:uppercase;margin-bottom:4px;">Audit Win Rate</div>
                        <div style="font-size:18px;font-weight:800;color:#1e3b8a;">{win_rate:.1f}%</div>
                    </div>
                    <div style="flex:1;padding:12px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;text-align:center;">
                        <div style="font-size:10px;color:#64748b;font-weight:bold;text-transform:uppercase;margin-bottom:4px;">Avg P&L vs Open</div>
                        <div style="font-size:18px;font-weight:800;color:{'#16a34a' if avg_pnl >=0 else '#dc2626'};">{avg_pnl:+.2f}%</div>
                    </div>
                    <div style="flex:1;padding:12px;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;text-align:center;">
                        <div style="font-size:10px;color:#64748b;font-weight:bold;text-transform:uppercase;margin-bottom:4px;">Diagnosed Mistakes</div>
                        <div style="font-size:18px;font-weight:800;color:#dc2626;">{len(mistakes)}</div>
                    </div>
                </div>

                <!-- 1. Suggestions Box -->
                <h3 style="color:#0f172a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:0;margin-bottom:14px;font-size:16px;">🛠️ Quantitative Recommendations for Reliability</h3>
                {suggestions_html}

                <!-- 2. Hits Table -->
                {f'''
                <h3 style="color:#16a34a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-top:24px;margin-bottom:12px;font-size:15px;">🟢 Successful Predictions Today ({len(hits)})</h3>
                <table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin-bottom:24px;">
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
                <table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin-bottom:24px;">
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
                <table style="width:100%;border-collapse:collapse;font-size:12px;text-align:left;margin-bottom:12px;">
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
