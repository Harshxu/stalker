import logging
import sys
import pandas as pd
import numpy as np

# Add parent dir to path
sys.path.append("c:/Users/ADMIN/Desktop/antigravity/Stalker")

import data_fetcher as df_module
import indicators as ind
import leadership_engine
import db_manager

logging.basicConfig(level=logging.INFO)

symbol = "RELIANCE.NS"
print(f"Fetching 1y history for {symbol}...")
df_1y = df_module.fetch_stock_history(symbol, period="1y")
df_3mo = df_module.fetch_stock_history(symbol, period="3mo")

if df_1y is None or df_3mo is None:
    print("Failed to fetch historical data!")
    sys.exit(1)

print(f"History lengths: 1y={len(df_1y)}, 3mo={len(df_3mo)}")

# Mock a candidate dict as produced by Stage 3
candidate = {
    "name": "RELIANCE",
    "symbol": symbol,
    "adjusted_alpha": 85.0,
    "alpha_score": 85.0,
    "total_score": 85.0,
    "rs_rank": 82.0,
    "sector_rank": 78.0,
    "institutional_score": 75.0,
    "sector": "Energy",
    "fund": {"industry": "Oil & Gas Integration"}
}

avg_industry_rs = {"Oil & Gas Integration": 72.0}
market_is_bullish = False

print("\n--- Running Leadership Validation ---")

# 1. Minervini Check
minervini = leadership_engine.check_minervini_template(df_1y, rs_percentile=candidate["rs_rank"])
print(f"Minervini check: {minervini}")

# 2. VCP Detection
vcp = leadership_engine.detect_vcp(df_3mo)
print(f"VCP check: {vcp}")

# 3. Leadership Stability Score
stability_score = leadership_engine.calculate_stability_score(df_1y)
print(f"Stability score: {stability_score}%")

# 4. Leadership Score
leadership_score = leadership_engine.compute_leadership_score(
    stability_score=stability_score,
    sector_rs_rank=candidate["sector_rank"],
    industry_rs_rank=avg_industry_rs.get(candidate["fund"]["industry"], 50.0),
    market_is_bullish=market_is_bullish,
    inst_score=candidate["institutional_score"]
)
print(f"Leadership score: {leadership_score}")

# 5. Multipliers & Final Score Calculation
vcp_grade = vcp.get("grade", "None")
if vcp_grade == "Elite":
    vcp_mult = 1.07
elif vcp_grade == "Strong":
    vcp_mult = 1.04
elif vcp_grade == "Weak":
    vcp_mult = 1.02
else:
    vcp_mult = 1.00

if leadership_score >= 80:
    leadership_mult = 1.05
elif leadership_score >= 60:
    leadership_mult = 1.02
elif leadership_score >= 45:
    leadership_mult = 1.00
else:
    leadership_mult = 0.95

alpha_val = candidate["alpha_score"]
final_val = alpha_val * leadership_mult * vcp_mult

print(f"Alpha: {alpha_val} | VCP Mult: {vcp_mult}x | Leadership Mult: {leadership_mult}x | Final Score: {final_val:.2f}")

# 6. Test attribution database saving
db_manager.save_feature_attributions_for_day("2026-06-06", [
    {
        "symbol": symbol,
        "alpha_score": alpha_val,
        "leadership_score": leadership_score,
        "minervini_score": minervini["conditions_passed"],
        "vcp_score": vcp["quality_score"],
        "final_score": final_val
    }
])

# Read the JSON back to confirm saving
attr = db_manager._read_json("feature_attributions.json")
print(f"Attributions stored in JSON: {attr}")
