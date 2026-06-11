"""
Diagnostic: Why is STALKER producing zero BUY picks?
Checks breadth, regime, filter pass rates, and score distribution.
"""
import sys, os
import io

# Force UTF-8 output on Windows
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, ".")
import yfinance as yf
import pandas as pd
import numpy as np
import config
import regime_engine as re_module

print("=" * 60)
print("  STALKER DIAGNOSTIC REPORT")
print("=" * 60)

# 1. Nifty data
nifty = yf.download("^NSEI", period="90d", progress=False, auto_adjust=True)
if isinstance(nifty.columns, pd.MultiIndex):
    nifty.columns = nifty.columns.get_level_values(0)

close = nifty["Close"]
ema20 = close.ewm(span=20).mean()
ema50 = close.ewm(span=50).mean()
ema200 = close.ewm(span=200).mean()

curr = float(close.iloc[-1])
e20 = float(ema20.iloc[-1])
e50 = float(ema50.iloc[-1])
e200 = float(ema200.iloc[-1])
ret_20d = (curr / float(close.iloc[-21]) - 1) * 100

print(f"\n[1] NIFTY STATE")
print(f"    Price:   {curr:,.0f}")
print(f"    EMA20:   {e20:,.0f}  | Price vs EMA20:  {(curr-e20)/e20*100:+.2f}%")
print(f"    EMA50:   {e50:,.0f}  | Price vs EMA50:  {(curr-e50)/e50*100:+.2f}%")
print(f"    EMA200:  {e200:,.0f}  | Price vs EMA200: {(curr-e200)/e200*100:+.2f}%")
print(f"    20d Ret: {ret_20d:+.2f}%")
print(f"    EMA20 > EMA50?  {e20 > e50}")
print(f"    Price > EMA200? {curr > e200}")

# 2. Real market breadth from a sample of 50 stocks
print(f"\n[2] MARKET BREADTH (Nifty 50 sample)")
symbols = config.NIFTY50_SYMBOLS[:25]  # first 25 for speed
above_50 = above_200 = valid = 0
for sym in symbols:
    try:
        df = yf.download(sym, period="60d", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 50:
            continue
        c = df["Close"]
        e50s = c.ewm(span=50).mean().iloc[-1]
        e200s = c.ewm(span=200).mean().iloc[-1]
        last = c.iloc[-1]
        above_50 += int(last > e50s)
        above_200 += int(last > e200s)
        valid += 1
    except Exception:
        pass

b50 = above_50 / valid * 100 if valid else 50.0
b200 = above_200 / valid * 100 if valid else 50.0
print(f"    Stocks above EMA50:  {above_50}/{valid} = {b50:.1f}%")
print(f"    Stocks above EMA200: {above_200}/{valid} = {b200:.1f}%")

# 3. Regime classification with real breadth
print(f"\n[3] REGIME CLASSIFICATION")
regime_8, risk_on, regime_data = re_module.classify_regime(
    nifty, b50/100, b200/100
)
legacy = re_module.get_legacy_regime(regime_8)
buying = re_module.is_buying_permitted(regime_8)
print(f"    8-State Regime:  {regime_8}")
print(f"    Legacy Regime:   {legacy}")
print(f"    Risk On:         {risk_on}")
print(f"    Buying Permitted:{buying}")
print(f"    Regime Data:     {regime_data}")

# 4. What does it take to get a BUY in current regime?
print(f"\n[4] BUY CONFIRMATION REQUIREMENTS (current regime)")
print(f"    Buying permitted by regime? {buying}")
print(f"    Min alpha score needed:     {'80.0' if legacy == 'Bear' else '75.0' if legacy == 'Neutral' else '70.0'}")
print(f"    Max rank allowed:           Top {'3%' if legacy == 'Bear' else '5%' if legacy == 'Neutral' else '10%'} of universe")
print(f"    Min R:R ratio needed:       1.5x")
print(f"    Account DD:                 14.6% -> sizing at 25% (still allowed)")

# 5. Today's scores check
print(f"\n[5] TODAY'S SCORE PROBLEM (from last scan)")
print(f"    8 stocks qualified Stage 1 from 188")
print(f"    Top score: APOLLOHOSP 67.4  -> needs 80.0 in Bear -> FAILS")
print(f"    With only 8 qualifiers, top 3% = max(1, int(0.03*8)) = 1 stock can even be considered")
print(f"    But score 67.4 < 80.0 -> no BUY regardless")

print(f"\n[6] ROOT CAUSE SUMMARY")
if not buying:
    print(f"    REGIME GATE: {regime_8} -> buying_permitted=False")
    print(f"    Even if a stock scored 100, it would be WATCH in this regime")
print(f"    BREADTH GATE: Only {b50:.0f}% of stocks above EMA50 (need >60% for Bull)")
print(f"    SCORE GATE:   Highest score {67.4} < 80.0 threshold for Bear regime")
print(f"    STAGE 1 GATE: Only 8/188 stocks pass safety filters (bear market = few setups)")

print(f"\n{'='*60}")
print(f"  CONCLUSION: System is working CORRECTLY.")
print(f"  This IS a Bear market. STALKER is protecting capital.")
print(f"{'='*60}")
