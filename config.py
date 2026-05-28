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
    "COALINDIA.NS", "TATAMOTORS.NS", "HCLTECH.NS", "GRASIM.NS", "BPCL.NS",
    "INDUSINDBK.NS", "TATASTEEL.NS", "CIPLA.NS", "DRREDDY.NS", "DIVISLAB.NS",
    "SBILIFE.NS", "APOLLOHOSP.NS", "HDFCLIFE.NS", "EICHERMOT.NS", "HEROMOTOCO.NS",
    "JSWSTEEL.NS", "HINDALCO.NS", "SHRIRAMFIN.NS", "BEL.NS", "TRENT.NS",
    "TATACONSUM.NS", "BRITANNIA.NS", "VEDL.NS", "ADANIENT.NS", "BAJAJ-AUTO.NS"
]

# Additional liquid mid-cap & high-growth candidates (railways, energy, financials, etc.)
MIDCAP_SYMBOLS = [
    "ZOMATO.NS", "PAYTM.NS", "IRCTC.NS", "CANBK.NS", "PNB.NS",
    "BANKBARODA.NS", "SAIL.NS", "NHPC.NS", "IOC.NS", "HPCL.NS",
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
    "LTIM.NS", "OFSS.NS", "ZENSARTECH.NS", "MAPMYINDIA.NS", "AFFLE.NS",
    # Real Estate & Infra
    "DLF.NS", "GODREJPROP.NS", "OBEROLRLTY.NS", "PRESTIGE.NS", "SOBHA.NS", 
    "NBCC.NS", "NCC.NS", "GMRINFRA.NS", "IRB.NS", "HFCL.NS", "KEC.NS", 
    "ENGINERSIN.NS",
    # Automotive, Tyres & Ancillaries
    "ASHOKLEY.NS", "TVSMOTOR.NS", "BALKRISIND.NS", "APOLLOTYRE.NS", 
    "CEAT.NS", "JKTYRE.NS", "EXIDEIND.NS", "AMARAJABAT.NS", "UNOINDA.NS", 
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
    "IDFCFIRSTB.BO", "FEDERALBNK.BO", "BANDHANBNK.BO", "RBLBANK.BO",
    "LICHSGFIN.BO",  "MUTHOOTFIN.BO", "MANAPPURAM.BO", "CHOLAFIN.BO",
    "ABCAPITAL.BO",  "BAJAJHFL.BO",
    # Consumer & FMCG
    "GODREJCP.BO",   "EMAMILTD.BO",   "VBL.BO",        "JUBLFOOD.BO",
    "BERGEPAINT.BO",
    # IT & Tech
    "PERSISTENT.BO", "COFORGE.BO",    "LTIM.BO",       "TATAELXSI.BO",
    # Infrastructure & Manufacturing
    "POLYCAB.BO",    "HAVELLS.BO",    "VOLTAS.BO",     "CROMPTON.BO",
    "ASTRAL.BO",     "KAJARIACER.BO", "SUPREMEIND.BO",
    # Pharma
    "ZYDUSLIFE.BO",  "ALKEM.BO",
]

# Full universe to scan — NSE + BSE combined
ALL_SYMBOLS = NIFTY50_SYMBOLS + MIDCAP_SYMBOLS + BSE_SYMBOLS

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
MAX_STOCK_PRICE = 10000    # ₹10,000 max per stock (user requirement)
MIN_STOCK_PRICE = 50       # Avoid penny stocks

# ─────────────────────────────────────────────
# SCORING WEIGHTS (Total = 100)
# ─────────────────────────────────────────────
WEIGHT_MARKET_STRUCTURE = 25
WEIGHT_VOLUME            = 20
WEIGHT_TECHNICAL         = 20
WEIGHT_MOMENTUM          = 15
WEIGHT_FUNDAMENTALS      = 10
WEIGHT_RISK_REWARD       = 10

# ─────────────────────────────────────────────
# TECHNICAL INDICATOR SETTINGS
# ─────────────────────────────────────────────
EMA_SHORT = 20
EMA_LONG  = 50
RSI_PERIOD = 14
RSI_MIN = 45          # Below this = weakness
RSI_MAX = 78          # Above this = overbought
VOLUME_SURGE_RATIO = 1.8   # Volume should be 1.8x 20-day average
VOLUME_LOOKBACK = 20       # Days for average volume
GAP_UP_THRESHOLD = 1.5    # Minimum gap up % to flag as Gap&Go
VWAP_TOLERANCE = 0.005     # 0.5% tolerance for "above VWAP"

# ─────────────────────────────────────────────
# MARKET STRUCTURE SETTINGS
# ─────────────────────────────────────────────
SWING_LOOKBACK = 5         # Candles to look back for swing highs/lows
MIN_SWING_MOVE = 0.01      # Minimum 1% move to qualify as swing

# ─────────────────────────────────────────────
# RISK MANAGEMENT SETTINGS (from PROMPT.txt)
# ─────────────────────────────────────────────
MIN_RISK_REWARD = 1.5      # Minimum R:R ratio (1:1.5)
IDEAL_RISK_REWARD = 2.0    # Ideal R:R ratio (1:2)
MAX_CAPITAL_RISK_PCT = 0.02  # Risk max 2% of capital per trade
STOP_LOSS_ATR_MULT = 1.5   # Stop loss = 1.5x ATR below entry
DAILY_LOSS_LIMIT_PCT = 0.03  # Stop trading after 3% daily drawdown

# ─────────────────────────────────────────────
# FUNDAMENTAL THRESHOLDS
# ─────────────────────────────────────────────
MAX_DEBT_TO_EQUITY = 1.5   # Max acceptable debt/equity
MIN_PROMOTER_HOLDING = 35  # Minimum promoter holding %
MIN_MARKET_CAP = 5000      # Min market cap in crores (₹5,000 Cr)

# ─────────────────────────────────────────────
# DATA SETTINGS
# ─────────────────────────────────────────────
HISTORY_PERIOD = "3mo"     # 3 months history
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
# DASHBOARD SERVER
# ─────────────────────────────────────────────
DASHBOARD_PORT = int(os.getenv("PORT", 8000))

