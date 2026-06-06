import logging
import sys
import pandas as pd
import numpy as np

# Add parent dir to path
sys.path.append("c:/Users/ADMIN/Desktop/antigravity/Stalker")

import data_fetcher as df_module
import indicators as ind
import screener
import market_structure as ms_module

logging.basicConfig(level=logging.INFO)

symbol = "RELIANCE.NS"
print(f"Fetching 3mo data for {symbol}...")
df_hist = df_module.fetch_stock_history(symbol, period="3mo")
if df_hist is None or df_hist.empty:
    print("Failed to fetch data!")
    sys.exit(1)

print(f"Length of history: {len(df_hist)}")

# 1. indicators
nifty_df = df_module.fetch_market_indices().get("NIFTY50")
indic = ind.compute_all_indicators(df_hist, nifty_df)
print(f"Indicators computed: {bool(indic)}")

# 2. evaluate_liquidity
is_liquid, avg_vol, avg_value = screener.evaluate_liquidity(df_hist)
print(f"evaluate_liquidity: {is_liquid} (avg_vol={avg_vol}, avg_value={avg_value})")

# 3. check_overnight_gap_risk
gap_risk = screener.check_overnight_gap_risk(df_hist)
print(f"check_overnight_gap_risk: {gap_risk}")

# 4. check_atr_spike_risk
atr_risk = screener.check_atr_spike_risk(df_hist, indic)
print(f"check_atr_spike_risk: {atr_risk}")

# 5. check_circuit_lock
circuit_lock = screener.check_circuit_lock(df_hist)
print(f"check_circuit_lock: {circuit_lock}")

# 6. evaluate_risk_profile
risk_score, max_dd, atr_pct = screener.evaluate_risk_profile(df_hist, indic)
print(f"evaluate_risk_profile: risk_score={risk_score}, max_dd={max_dd}, atr_pct={atr_pct}")

# 7. calculate_data_quality
fund = df_module.fetch_fundamentals(symbol)
dq_score, missing = screener.calculate_data_quality(symbol, fund, df_hist)
print(f"calculate_data_quality: dq_score={dq_score}, missing={missing}")

# 8. detect_market_structure
ms = ms_module.detect_market_structure(df_hist)
print(f"detect_market_structure: {ms}")
