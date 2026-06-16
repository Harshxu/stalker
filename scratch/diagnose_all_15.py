import sys
sys.path.append("c:/Users/ADMIN/Desktop/antigravity/Stalker")

import data_fetcher as df_module
import indicators as ind
import screener
import market_structure as ms_module

test_symbols = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "SBIN.NS",
    "HINDUNILVR.NS", "ITC.NS", "SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS",
    "TATACONSUM.NS", "BRITANNIA.NS", "NTPC.NS", "POWERGRID.NS", "TRENT.NS"
]

print("Fetching data...")
all_history = df_module.fetch_multiple_stocks(test_symbols, period="3mo")
nifty_df = df_module.fetch_market_indices().get("NIFTY50")

print("\nEvaluating rejections:")
for symbol in test_symbols:
    df_hist = all_history.get(symbol)
    if df_hist is None or len(df_hist) < 20:
        print(f"  {symbol}: REJECTED - No history / short history")
        continue
    
    indic = ind.compute_all_indicators(df_hist, nifty_df)
    if not indic:
        print(f"  {symbol}: REJECTED - Failed indicators calculation")
        continue
        
    is_liquid, avg_vol, avg_value = screener.evaluate_liquidity(df_hist)
    if not is_liquid:
        print(f"  {symbol}: REJECTED - Liquidity gate failed")
        continue
        
    if screener.check_overnight_gap_risk(df_hist):
        print(f"  {symbol}: REJECTED - Overnight gap risk")
        continue
        
    if screener.check_atr_spike_risk(df_hist, indic):
        print(f"  {symbol}: REJECTED - ATR spike risk")
        continue
        
    if screener.check_circuit_lock(df_hist):
        print(f"  {symbol}: REJECTED - Circuit lock")
        continue
        
    risk_score, max_dd, atr_pct = screener.evaluate_risk_profile(df_hist, indic)
    # The regime is Bear, so threshold is 4.2
    if risk_score > 4.2:
        print(f"  {symbol}: REJECTED - Risk score {risk_score:.2f} > 4.2 (threshold in Bear)")
        continue
        
    fund = df_module.fetch_fundamentals(symbol)
    dq_score, missing = screener.calculate_data_quality(symbol, fund, df_hist)
    if dq_score < 70.0:
        print(f"  {symbol}: REJECTED - Data quality {dq_score} < 70")
        continue
        
    ms = ms_module.detect_market_structure(df_hist)
    if ms.get("structure") == "downtrend":
        print(f"  {symbol}: REJECTED - Uptrend requirement failed (downtrend detected)")
        continue
        
    print(f"  🎉 {symbol} PASSED all safety filters!")
