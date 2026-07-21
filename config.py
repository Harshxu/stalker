"""
STALKER - Stock Market Analyzer Configuration
All system-wide settings, stock universe, and thresholds.
"""

import os
from datetime import time

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass   # dotenv optional; fall back to system env vars

# ─────────────────────────────────────────────
# MONGODB CONFIG (Update when user provides connection string)
# ─────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = "stalker_db"
MONGO_COLLECTION_PICKS = "daily_picks"
MONGO_COLLECTION_PRICES = "price_history"
MONGO_COLLECTION_PERFORMANCE = "performance"

# ─────────────────────────────────────────────
# EMAIL CONFIG — Formsubmit.co (no SMTP, no password)
# POST to https://formsubmit.co/ajax/{email}
# First send triggers a one-time activation email — click it once.
# ─────────────────────────────────────────────
FORMSUBMIT_TO      = os.getenv("FORMSUBMIT_TO", "")
FORMSUBMIT_CC      = os.getenv("FORMSUBMIT_CC", "")
FORMSUBMIT_ENDPOINT = f"https://formsubmit.co/ajax/{FORMSUBMIT_TO}" if FORMSUBMIT_TO else ""

# ─────────────────────────────────────────────
# STOCK UNIVERSE — NSE Blue Chips + Liquid Mid-Caps
# All are high liquidity, under ₹10,000 range considered
# ─────────────────────────────────────────────
NIFTY50_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
    "INFY.NS", "SBIN.NS", "BAJFINANCE.NS", "LT.NS", "HINDUNILVR.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS",
    "WIPRO.NS", "NTPC.NS", "POWERGRID.NS", "ADANIPORTS.NS", "ULTRACEMCO.NS",
    "BAJAJFINSV.NS", "TECHM.NS", "M&M.NS", "NESTLEIND.NS", "ONGC.NS",
    "COALINDIA.NS", "TMPV.NS", "HCLTECH.NS", "GRASIM.NS", "BPCL.NS",
    "INDUSINDBK.NS", "TATASTEEL.NS", "CIPLA.NS", "DRREDDY.NS", "DIVISLAB.NS",
    "SBILIFE.NS", "APOLLOHOSP.NS", "HDFCLIFE.NS", "EICHERMOT.NS", "HEROMOTOCO.NS",
    "JSWSTEEL.NS", "HINDALCO.NS", "SHRIRAMFIN.NS", "BEL.NS", "TRENT.NS",
    "TATACONSUM.NS", "BRITANNIA.NS", "VEDL.NS", "ADANIENT.NS", "BAJAJ-AUTO.NS"
]

# Additional liquid mid-cap & high-growth candidates (railways, energy, financials, etc.)
MIDCAP_SYMBOLS = [
    "ETERNAL.NS", "PAYTM.NS", "IRCTC.NS", "CANBK.NS", "PNB.NS",
    "BANKBARODA.NS", "SAIL.NS", "NHPC.NS", "IOC.NS", "HINDPETRO.NS",
    "RECLTD.NS", "PFC.NS", "IRFC.NS", "BHEL.NS", "HAL.NS",
    "MARICO.NS", "PIDILITIND.NS", "DABUR.NS", "COLPAL.NS", "LUPIN.NS",
    "AUROPHARMA.NS", "TORNTPHARM.NS", "GLAXO.NS", "MPHASIS.NS", "LTTS.NS",
    # Highly active railway stocks
    "RVNL.NS", "IRCON.NS", "RAILTEL.NS", "TEXRAIL.NS", "TITAGARH.NS",
    # Energy, Renewable & Power
    "IREDA.NS", "SJVN.NS", "SUZLON.NS", "TATAPOWER.NS", "CESC.NS", 
    "TORNTPOWER.NS", "JSWENERGY.NS", "ADANIPOWER.NS",
    # Finance, NBFCs & Market Infrastructure
    "CDSL.NS", "BSE.NS", "MCX.NS", "HUDCO.NS", "MANAPPURAM.NS", 
    "MUTHOOTFIN.NS", "UNIONBANK.NS", "IDBI.NS", "J&KBANK.NS", 
    "KARURVYSYA.NS", "CUB.NS", "FEDERALBNK.NS", "IDFCFIRSTB.NS", 
    "BANDHANBNK.NS", "RBLBANK.NS", "LICHSGFIN.NS",
    # IT & High Tech
    "KPITTECH.NS", "COFORGE.NS", "PERSISTENT.NS", "CYIENT.NS", "TATAELXSI.NS", 
    "LTM.NS", "OFSS.NS", "ZENSARTECH.NS", "MAPMYINDIA.NS", "AFFLE.NS",
    # Real Estate & Infra
    "DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS", "PRESTIGE.NS", "SOBHA.NS", 
    "NBCC.NS", "NCC.NS", "GMRAIRPORT.NS", "IRB.NS", "HFCL.NS", "KEC.NS", 
    "ENGINERSIN.NS",
    # Automotive, Tyres & Ancillaries
    "ASHOKLEY.NS", "TVSMOTOR.NS", "BALKRISIND.NS", "APOLLOTYRE.NS", 
    "CEATLTD.NS", "JKTYRE.NS", "EXIDEIND.NS", "ARE&M.NS", "UNOMINDA.NS", 
    "CIEINDIA.NS",
    # Pharma, Healthcare & Biotech
    "BIOCON.NS", "GLENMARK.NS", "IPCALAB.NS", "LAURUSLABS.NS", 
    "NATCOPHARM.NS", "GRANULES.NS", "MARKSANS.NS", "ZYDUSLIFE.NS", "ALKEM.NS",
    # Chemicals & Fertilizers
    "DEEPAKNTR.NS", "SRF.NS", "UPL.NS", "PIIND.NS", "TATACHEM.NS", 
    "GUJALKALI.NS", "COROMANDEL.NS",
    # Consumption & Retail
    "ABFRL.NS", "BATAINDIA.NS", "RELAXO.NS", "KALYANKJIL.NS", "SENCO.NS", 
    "DEVYANI.NS", "WESTLIFE.NS", "CAMPUS.NS"
]

# BSE-listed stocks for broader coverage
BSE_SYMBOLS = [
    # Banking & NBFCs
    "CHOLAFIN.BO",   "ABCAPITAL.BO",  "BAJAJHFL.BO",
    # Consumer & FMCG
    "GODREJCP.BO",   "EMAMILTD.BO",   "VBL.BO",        "JUBLFOOD.BO",
    "BERGEPAINT.BO",
    # Infrastructure & Manufacturing
    "POLYCAB.BO",    "HAVELLS.BO",    "VOLTAS.BO",     "CROMPTON.BO",
    "ASTRAL.BO",     "KAJARIACER.BO", "SUPREMEIND.BO",
]

# Full universe to scan — NSE + BSE combined
ALL_SYMBOLS = NIFTY50_SYMBOLS + MIDCAP_SYMBOLS + BSE_SYMBOLS

def get_scan_universe():
    """Get the active list of symbols to scan. Loads from JSON if available, otherwise falls back to static list."""
    import json
    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base_dir, "data", "universe_symbols.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                syms = json.load(f)
                if isinstance(syms, list) and len(syms) > 0:
                    return syms
        except Exception:
            pass
    return ALL_SYMBOLS

# Market & Sector indices
NIFTY_INDEX = "^NSEI"
BANK_NIFTY = "^NSEBANK"
NIFTY_IT = "^CNXIT"
NIFTY_PHARMA = "^CNXPHARMA"
NIFTY_AUTO = "^CNXAUTO"
NIFTY_FMCG = "^CNXFMCG"
NIFTY_ENERGY = "^CNXENERGY"
NIFTY_METAL = "^CNXMETAL"

# ─────────────────────────────────────────────
# PRICE FILTER
# ─────────────────────────────────────────────
MAX_STOCK_PRICE = 5000     # ₹5,000 max per stock (updated user requirement)
MIN_STOCK_PRICE = 100      # ₹100 min — avoids penny stocks where taxes eat all gains

# ─────────────────────────────────────────────
# INTRADAY STRATEGY PARAMETERS
# (Used by the three-strategy screener)
# ─────────────────────────────────────────────

# Strategy 1 — Opening Range Breakout (ORB)
ORB_RANGE_MINUTES   = 15        # Mark high/low of the first 15 min (9:15–9:30 AM)
ORB_VOLUME_MULT     = 1.5       # Breakout candle must have 1.5× average volume
ORB_RR_MIN          = 1.5       # Minimum risk-to-reward for ORB entry
ORB_MAX_RANGE_PCT   = 0.04      # Reject if opening range > 4% of price (too wide)

# Strategy 2 — VWAP Bounce & Flip
VWAP_RSI_MIN_LONG   = 50        # RSI must be above 50 for long setups
VWAP_RSI_MAX_SHORT  = 50        # RSI must be below 50 for short setups
VWAP_SL_PTS         = 6         # Stop-loss 5-6 pts below VWAP (fixed)
VWAP_EMA_FAST       = 9         # Fast EMA for VWAP bounce confirmation
VWAP_EMA_SLOW       = 21        # Slow EMA for VWAP bounce confirmation
VWAP_MIN_HOLD_TIME  = 30        # Skip VWAP signals before 9:30 AM (30 min after open)

# Strategy 3 — Momentum + Volume Surge
MOM_VOLUME_SURGE_MIN = 1.5      # Volume must be ≥150% of 20-day average
MOM_RSI_ENTRY_MIN   = 50        # RSI entry zone lower bound
MOM_RSI_ENTRY_MAX   = 65        # RSI entry zone upper bound (not already overbought)
MOM_RSI_AVOID_ABOVE = 75        # Avoid entry if RSI > 75 (chasing)
MOM_BASE_WAIT_MIN   = 30        # Wait 30 min after open before entry
MOM_PROFIT_PARTIAL  = 0.50      # Book 50% at first target

# ─────────────────────────────────────────────
# PHASE 1 ELITE ARCHITECTURE: 8 ADAPTIVE REGIMES
# Each regime defines weights for the 4 Ensemble Alpha sub-models.
# Regime is determined by regime_engine.py
# ─────────────────────────────────────────────

# Ensemble sub-model base weights — rebalanced 2026-07-09 based on IC audit
# IC data: institutional +0.34, quality +0.31, catalyst +0.23, momentum/RS -0.07
# Weights now reflect ACTUAL predictive power, not theoretical assumptions
ENSEMBLE_WEIGHTS = {
    # Bull: Institutional leads, quality matters, momentum de-weighted (RS has -0.07 IC)
    "Bull_Trend":        {"momentum": 0.14, "quality": 0.28, "institutional": 0.40, "catalyst": 0.18},
    # Bull Expansion: slightly more institutional (momentum surge), but quality still strong
    "Bull_Expansion":    {"momentum": 0.16, "quality": 0.26, "institutional": 0.42, "catalyst": 0.16},
    # Bull Exhaustion: institutions selling, quality + catalyst signal who survives
    "Bull_Exhaustion":   {"momentum": 0.10, "quality": 0.32, "institutional": 0.38, "catalyst": 0.20},
    # Neutral: Quality and catalyst differentiate winners from losers
    "Neutral_Rotation":  {"momentum": 0.12, "quality": 0.30, "institutional": 0.38, "catalyst": 0.20},
    # Compression: Quiet before breakout — quality + institutional tell who breaks out
    "Neutral_Compression": {"momentum": 0.10, "quality": 0.32, "institutional": 0.40, "catalyst": 0.18},
    # Bear: Safety over momentum — quality and fundamentals protect capital
    "Bear_Trend":        {"momentum": 0.10, "quality": 0.36, "institutional": 0.36, "catalyst": 0.18},
    # Bear Panic: Pure institutional (who is NOT selling) + quality survival
    "Bear_Panic":        {"momentum": 0.08, "quality": 0.34, "institutional": 0.42, "catalyst": 0.16},
    # Recovery: Institutions step in first, then fundamentals confirm recovery
    "Bear_Recovery":     {"momentum": 0.14, "quality": 0.30, "institutional": 0.38, "catalyst": 0.18},
}

# Mapping from 8-state regime to legacy 3-state for backward compatibility
REGIME_LEGACY_MAP = {
    "Bull_Trend": "Bull",
    "Bull_Expansion": "Bull",
    "Bull_Exhaustion": "Neutral",
    "Neutral_Rotation": "Neutral",
    "Neutral_Compression": "Neutral",
    "Bear_Trend": "Bear",
    "Bear_Panic": "Bear",
    "Bear_Recovery": "Neutral",
}

# Legacy REGIME_WEIGHTS kept for backward compat with old screener logic
REGIME_WEIGHTS = {
    "Bull": {
        "rs": 25, "institutional": 20, "structure": 20,
        "technical": 15, "fundamental": 5, "earnings": 5, "sector": 10, "opportunity": 0
    },
    "Neutral": {
        "rs": 15, "institutional": 20, "structure": 15,
        "technical": 15, "fundamental": 15, "earnings": 5, "sector": 10, "opportunity": 5
    },
    "Bear": {
        "rs": 10, "institutional": 15, "structure": 10,
        "technical": 10, "fundamental": 35, "earnings": 5, "sector": 5, "opportunity": 10
    },
    "Improving": {
        "rs": 18, "institutional": 22, "structure": 17,
        "technical": 15, "fundamental": 10, "earnings": 5, "sector": 10, "opportunity": 3
    },
    "Deteriorating": {
        "rs": 12, "institutional": 18, "structure": 12,
        "technical": 12, "fundamental": 25, "earnings": 5, "sector": 6, "opportunity": 10
    }
}

WEIGHT_QUALITY_MOMENTUM = 20  # Quality Momentum feature weight

# Quality Momentum sub-weights (used in legacy screener path)
QM_WEIGHTS = {
    "rs": 0.4,
    "earnings_growth": 0.2,
    "roe": 0.2,
    "fcf_growth": 0.2
}

# ─────────────────────────────────────────────
# STRATEGY DEGRADATION & REPLACEMENT
# If a setup type has been losing for N consecutive days, it gets replaced
# by a fresh strategy from STRATEGY_FALLBACK map.
# ─────────────────────────────────────────────
STRATEGY_DEGRADATION_DAYS = 3       # Days of negative avg return before replacement
STRATEGY_DEGRADATION_THRESHOLD = -0.15  # Avg return below this % triggers replacement

# Replacement map: failing setup → fresh replacement strategy
STRATEGY_FALLBACK = {
    "MOMENTUM":        "INSTITUTIONAL_BREAKOUT",  # IC audit: institutional has +0.34 IC
    "EARNINGS_RUNNER": "QUALITY_TREND",           # IC audit: quality/fundamental has +0.31 IC
    "PULLBACK":        "VWAP_RECLAIM",            # Structure-based entry, cleaner signal
}

# Minimum alpha required per setup type before a BUY is confirmed
# Based on actual performance — worse-performing setups need higher conviction
SETUP_MIN_ALPHA = {
    "VALUE_MOMENTUM":        44.0,  # Best setup (75% WR, +0.25%) — lowest bar
    "INSTITUTIONAL_BREAKOUT": 45.0, # Replacing MOMENTUM — institutional IC is +0.34
    "VWAP_RECLAIM":           47.0, # Replacing PULLBACK — clean VWAP flip signal
    "QUALITY_TREND":          47.0, # Replacing EARNINGS_RUNNER — fundamental conviction
    "BREAKOUT":               48.0, # Limited data — needs volume confirmation
    "PULLBACK":               50.0, # Historical -0.39% — still cautious
    "EARNINGS_RUNNER":        55.0, # Historical -0.24% — high confidence only
    "MOMENTUM":               58.0, # Worst performer (-0.54%) — hardest bar
    "WATCHLIST_ONLY":         65.0, # Not a buy setup — almost never passes
}

# ─────────────────────────────────────────────
# DRAWDOWN-AWARE POSITION SIZING TIERS
# When account equity drops, risk per trade shrinks automatically.
# ─────────────────────────────────────────────
DRAWDOWN_SIZING_TIERS = [
    {"max_dd_pct": 5.0,  "risk_multiplier": 1.0},   # 0-5% DD → full risk
    {"max_dd_pct": 10.0, "risk_multiplier": 0.5},   # 5-10% DD → half risk
    {"max_dd_pct": 15.0, "risk_multiplier": 0.25},  # 10-15% DD → quarter risk
    {"max_dd_pct": 999,  "risk_multiplier": 0.0},   # >15% DD → stop trading
]

# ─────────────────────────────────────────────
# LIQUIDITY & TRANSACTION COSTS
# ─────────────────────────────────────────────
MIN_DAILY_TURNOVER = 100000000  # ₹10 Crore/day
SLIPPAGE_PCT = 0.0010           # 0.10% slippage assumption
BROKERAGE_PCT = 0.0005          # 0.05% brokerage assumption

# ─────────────────────────────────────────────
# TECHNICAL INDICATOR SETTINGS
# ─────────────────────────────────────────────
EMA_SHORT = 20
EMA_LONG  = 50
RSI_PERIOD = 14
RSI_MIN = 45          # Below this = weakness
RSI_MAX = 80          # Above this = overbought (relaxed slightly for intraday momentum leaders)
VOLUME_SURGE_RATIO = 1.8   # Volume should be 1.8x 20-day average
VOLUME_LOOKBACK = 20       # Days for average volume
GAP_UP_THRESHOLD = 1.5    # Minimum gap up % to flag as Gap&Go
VWAP_TOLERANCE = 0.005     # 0.5% tolerance for "above VWAP"

# ─────────────────────────────────────────────
# MARKET STRUCTURE SETTINGS
# ─────────────────────────────────────────────
SWING_LOOKBACK = 8         # Candles to look back for swing highs/lows (wider for daily charts)
MIN_SWING_MOVE = 0.01      # Minimum 1% move to qualify as swing

# ─────────────────────────────────────────────
# RISK MANAGEMENT SETTINGS (from PROMPT.txt)
# ─────────────────────────────────────────────
MIN_RISK_REWARD = 1.2      # Minimum R:R ratio — lowered to 1.2 for intraday (realistic for tight entry zones)
IDEAL_RISK_REWARD = 2.0    # Ideal R:R ratio (1:2)
ACCOUNT_SIZE = 1000000.0   # Default ₹10 Lakhs account size
RISK_PER_TRADE_PCT = 0.02  # Risk max 2% of capital per trade
MAX_CAPITAL_RISK_PCT = 0.02  # Keeping for backward compatibility
PORTFOLIO_MAX_RISK_PCT = 0.30 # Max portfolio risk exposure (increased from 6% to 30% to avoid blocking recommendations)
STOP_LOSS_ATR_MULT = 1.5   # Stop loss = 1.5x ATR below entry — tighter for intraday (better R:R, faster exit)
DAILY_LOSS_LIMIT_PCT = 0.03  # Stop trading after 3% daily drawdown
STRICT_BULL_ONLY_BUY = False   # Allow buys in Neutral/Improving regimes for top leadership picks

# Kill Switch Settings
KILL_SWITCH_LOSS_COUNT = 5 # Number of consecutive losses to trigger switch
KILL_SWITCH_DAYS = 3       # Days to suspend buying (forces WATCH status)

# ─────────────────────────────────────────────
# FUNDAMENTAL THRESHOLDS
# ─────────────────────────────────────────────
MAX_DEBT_TO_EQUITY = 1.5   # Max acceptable debt/equity
MIN_PROMOTER_HOLDING = 35  # Minimum promoter holding %
MIN_MARKET_CAP = 5000      # Min market cap in crores (₹5,000 Cr)

# ─────────────────────────────────────────────
# DATA SETTINGS
# ─────────────────────────────────────────────
HISTORY_PERIOD = "1y"     # 1 year history to support true 200 MA
INTRADAY_INTERVAL = "15m"  # 15-minute candles for setup
DAILY_INTERVAL = "1d"      # Daily candles for trend
TOP_PICKS_COUNT = 15       # Return top 15 picks (user can see 10-15)

# ─────────────────────────────────────────────
# SCHEDULE TIMES (IST)
# ─────────────────────────────────────────────
PREMARKET_ANALYSIS_TIME = time(7, 0)   # 7:00 AM — deep market analysis (NSE+BSE)
MORNING_SCAN_TIME = time(8, 30)        # 8:30 AM — send morning email with picks
OPEN_PRICE_TIME   = time(9, 20)        # 9:20 AM — record open prices
CLOSE_PRICE_TIME  = time(15, 35)       # 3:35 PM — record close prices
EOD_REPORT_TIME   = time(16, 0)        # 4:00 PM — generate EOD report + email

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(BASE_DIR, "data")
REPORTS_DIR  = os.path.join(BASE_DIR, "reports")
LOGS_DIR     = os.path.join(BASE_DIR, "logs")

# Create directories if not exist
for d in [DATA_DIR, REPORTS_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)

# ─────────────────────────────────────────────
# LEADERSHIP VALIDATION LAYER
# ─────────────────────────────────────────────
MINERVINI_MIN_CONDITIONS = 5          # Reject if fewer than 5 pass (relaxed from 6 to avoid being too strict initially)
MINERVINI_NONNEG_CONDITIONS = [1, 2, 5, 6]  # Non-negotiable conditions (must always pass)
VCP_MIN_QUALITY_SCORE = 40.0         # Below this, not qualified
LEADERSHIP_FINAL_WEIGHTS = {
    "alpha": 0.70,
    "leadership": 0.20,
    "vcp": 0.10
}

# ─────────────────────────────────────────────
# DASHBOARD SERVER
# ─────────────────────────────────────────────
DASHBOARD_PORT = int(os.getenv("PORT", 8000))

# ─────────────────────────────────────────────
# EMAIL DISPATCH CONTROL
# ─────────────────────────────────────────────
DISABLE_SUBSCRIBER_EMAILS = True  # Set to True to temporarily stop all subscriber emails (morning & evening)



