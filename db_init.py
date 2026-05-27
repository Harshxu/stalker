"""
STALKER - MongoDB Database Initializer
Creates the database, all collections, and indexes on Atlas.
Run once: python db_init.py
"""

import sys
import os
from datetime import datetime

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME   = "stalker_db"

# ─────────────────────────────────────────────
# Collection Schemas (as comments for reference)
# ─────────────────────────────────────────────
#
# daily_picks      → one doc per trading day, stores top picks + metadata
# price_history    → open & close prices per symbol per day
# performance      → daily P&L summary per day
# stocks_universe  → master list of all tracked stocks + static info
# system_logs      → scan logs, errors, system events
#
# ─────────────────────────────────────────────

COLLECTIONS = {

    "daily_picks": {
        "description": "Top stock picks generated each morning",
        "indexes": [
            {"keys": [("date", -1)], "unique": True, "name": "idx_date_unique"},
            {"keys": [("picks.symbol", 1)], "name": "idx_symbol"},
            {"keys": [("market_trend", 1)], "name": "idx_market_trend"},
        ],
        "sample_doc": {
            "date":          "2026-05-27",
            "scan_time":     "08:32:15",
            "market_trend":  "bullish",        # bullish / bearish / sideways
            "market_bullish": True,
            "sector_trends": {                 # sector → bullish/bearish/sideways
                "Banking": "bullish",
                "IT":      "sideways",
            },
            "picks": [                         # list of top N picks (sorted by score)
                {
                    "symbol":        "RELIANCE.NS",
                    "name":          "RELIANCE",
                    "total_score":   78.5,
                    "action":        "BUY",          # BUY / WATCH / AVOID
                    "risk_profile":  "Low Risk",
                    "trade_type":    "Momentum Trade",
                    "sector":        "Oil & Gas",
                    "current_price": 1430.50,
                    "stop_loss":     1395.20,
                    "target_1":      1483.20,
                    "target_2":      1518.40,
                    "rr_ratio":      2.1,
                    "gap_pct":       2.3,
                    "volume_ratio":  2.8,
                    "rsi":           62.4,
                    "structure":     "uptrend",
                    "news_sentiment": "bullish",
                    "reasons": [
                        "Stock is in a strong uptrend",
                        "Trading at 2.8x normal volume",
                        "Gapped up 2.3% at open",
                    ],
                    "fund_flags":  ["Low debt", "Strong profit growth"],
                    "fund_alerts": [],
                }
            ],
            "scanned":     75,
            "qualified":   18,
            "elapsed_sec": 42,
            "created_at":  "2026-05-27T08:32:57",
        }
    },

    "price_history": {
        "description": "Opening and closing prices for picked stocks each day",
        "indexes": [
            {"keys": [("date", -1), ("time_of_day", 1)], "unique": True, "name": "idx_date_time_unique"},
            {"keys": [("symbol", 1)], "name": "idx_symbol"},
            {"keys": [("date", -1)], "name": "idx_date"},
        ],
        "sample_doc": {
            "date":        "2026-05-27",
            "time_of_day": "open",              # "open" (9:20 AM) or "close" (3:35 PM)
            "timestamp":   "2026-05-27T09:20:05",
            "symbol":      "RELIANCE.NS",
            "open":        1430.50,
            "high":        1451.00,
            "low":         1425.10,
            "close":       None,                # filled at close time
            "volume":      4500000,
            "prev_close":  1397.80,
        }
    },

    "performance": {
        "description": "End-of-day P&L summary for each trading day",
        "indexes": [
            {"keys": [("date", -1)], "unique": True, "name": "idx_date_unique"},
            {"keys": [("win_rate", -1)], "name": "idx_win_rate"},
        ],
        "sample_doc": {
            "date":          "2026-05-27",
            "generated_at":  "2026-05-27T16:02:00",
            "market_trend":  "bullish",
            "total_picks":   12,
            "wins":          8,
            "losses":        3,
            "pending":       1,
            "win_rate":      72.7,
            "total_pnl_pct": 4.2,
            "avg_pnl_pct":   0.35,
            "best_pick": {
                "symbol":   "TCS.NS",
                "pnl_pct":  2.8,
            },
            "worst_pick": {
                "symbol":   "SBIN.NS",
                "pnl_pct":  -0.9,
            },
            "results": [                        # per-stock breakdown
                {
                    "symbol":      "RELIANCE.NS",
                    "action":      "BUY",
                    "score":       78.5,
                    "open_price":  1430.50,
                    "close_price": 1468.20,
                    "pnl_rupees":  37.70,
                    "pnl_pct":     2.63,
                    "hit_target":  True,
                    "hit_sl":      False,
                    "result":      "WIN",
                }
            ],
        }
    },

    "stocks_universe": {
        "description": "Master list of all NSE stocks tracked by STALKER",
        "indexes": [
            {"keys": [("symbol", 1)], "unique": True, "name": "idx_symbol_unique"},
            {"keys": [("sector", 1)], "name": "idx_sector"},
            {"keys": [("is_active", 1)], "name": "idx_active"},
        ],
        "sample_doc": {
            "symbol":        "RELIANCE.NS",
            "name":          "Reliance Industries Ltd",
            "sector":        "Oil & Gas",
            "industry":      "Oil & Gas Integrated",
            "is_nifty50":    True,
            "is_active":     True,
            "added_on":      "2026-05-27",
            "market_cap_cr": 1820000,          # in Crores
            "avg_price":     1430.0,
            "notes":         "",
        }
    },

    "system_logs": {
        "description": "Scan logs, errors, and system events",
        "indexes": [
            {"keys": [("timestamp", -1)], "name": "idx_timestamp"},
            {"keys": [("level", 1)], "name": "idx_level"},
            {"keys": [("event", 1)], "name": "idx_event"},
        ],
        "sample_doc": {
            "timestamp": "2026-05-27T08:32:00",
            "level":     "INFO",               # INFO / WARNING / ERROR
            "event":     "morning_scan",       # morning_scan / open_prices / close_prices / eod_report
            "message":   "Morning scan completed: 75 scanned, 12 picked",
            "data":      {},
        }
    },
}


# ─────────────────────────────────────────────
# NIFTY 50 MASTER DATA (pre-seed)
# ─────────────────────────────────────────────
STOCKS_SEED = [
    # symbol, name, sector, nifty50
    ("RELIANCE.NS",   "Reliance Industries Ltd",       "Oil & Gas",       True),
    ("TCS.NS",        "Tata Consultancy Services",     "IT",              True),
    ("HDFCBANK.NS",   "HDFC Bank Ltd",                 "Banking",         True),
    ("ICICIBANK.NS",  "ICICI Bank Ltd",                "Banking",         True),
    ("BHARTIARTL.NS", "Bharti Airtel Ltd",             "Telecom",         True),
    ("INFY.NS",       "Infosys Ltd",                   "IT",              True),
    ("SBIN.NS",       "State Bank of India",           "Banking",         True),
    ("BAJFINANCE.NS", "Bajaj Finance Ltd",             "Finance",         True),
    ("LT.NS",         "Larsen & Toubro Ltd",           "Infrastructure",  True),
    ("HINDUNILVR.NS", "Hindustan Unilever Ltd",        "FMCG",            True),
    ("AXISBANK.NS",   "Axis Bank Ltd",                 "Banking",         True),
    ("KOTAKBANK.NS",  "Kotak Mahindra Bank",           "Banking",         True),
    ("MARUTI.NS",     "Maruti Suzuki India",           "Auto",            True),
    ("SUNPHARMA.NS",  "Sun Pharmaceutical",            "Pharma",          True),
    ("TITAN.NS",      "Titan Company Ltd",             "Consumer",        True),
    ("WIPRO.NS",      "Wipro Ltd",                     "IT",              True),
    ("NTPC.NS",       "NTPC Ltd",                      "Energy",          True),
    ("POWERGRID.NS",  "Power Grid Corporation",        "Energy",          True),
    ("ADANIPORTS.NS", "Adani Ports & SEZ",             "Infrastructure",  True),
    ("ULTRACEMCO.NS", "UltraTech Cement",              "Infrastructure",  True),
    ("BAJAJFINSV.NS", "Bajaj Finserv Ltd",             "Finance",         True),
    ("TECHM.NS",      "Tech Mahindra Ltd",             "IT",              True),
    ("M&M.NS",        "Mahindra & Mahindra",           "Auto",            True),
    ("NESTLEIND.NS",  "Nestle India Ltd",              "FMCG",            True),
    ("ONGC.NS",       "Oil & Natural Gas Corp",        "Oil & Gas",       True),
    ("COALINDIA.NS",  "Coal India Ltd",                "Energy",          True),
    ("TATAMOTORS.NS", "Tata Motors Ltd",               "Auto",            True),
    ("HCLTECH.NS",    "HCL Technologies",              "IT",              True),
    ("GRASIM.NS",     "Grasim Industries",             "Diversified",     True),
    ("BPCL.NS",       "Bharat Petroleum Corp",         "Oil & Gas",       True),
    ("INDUSINDBK.NS", "IndusInd Bank Ltd",             "Banking",         True),
    ("TATASTEEL.NS",  "Tata Steel Ltd",                "Metal",           True),
    ("CIPLA.NS",      "Cipla Ltd",                     "Pharma",          True),
    ("DRREDDY.NS",    "Dr Reddys Laboratories",        "Pharma",          True),
    ("DIVISLAB.NS",   "Divis Laboratories",            "Pharma",          True),
    ("SBILIFE.NS",    "SBI Life Insurance",            "Insurance",       True),
    ("APOLLOHOSP.NS", "Apollo Hospitals",              "Healthcare",      True),
    ("HDFCLIFE.NS",   "HDFC Life Insurance",           "Insurance",       True),
    ("EICHERMOT.NS",  "Eicher Motors Ltd",             "Auto",            True),
    ("HEROMOTOCO.NS", "Hero MotoCorp Ltd",             "Auto",            True),
    ("JSWSTEEL.NS",   "JSW Steel Ltd",                 "Metal",           True),
    ("HINDALCO.NS",   "Hindalco Industries",           "Metal",           True),
    ("SHRIRAMFIN.NS", "Shriram Finance Ltd",           "Finance",         True),
    ("BEL.NS",        "Bharat Electronics Ltd",        "Defence",         True),
    ("TRENT.NS",      "Trent Ltd",                     "Consumer",        True),
    ("TATACONSUM.NS", "Tata Consumer Products",        "FMCG",            True),
    ("BRITANNIA.NS",  "Britannia Industries",          "FMCG",            True),
    ("VEDL.NS",       "Vedanta Ltd",                   "Metal",           True),
    ("ADANIENT.NS",   "Adani Enterprises",             "Energy",          True),
    ("BAJAJ-AUTO.NS", "Bajaj Auto Ltd",                "Auto",            True),
    # Mid-caps
    ("ZOMATO.NS",     "Zomato Ltd",                    "Food Tech",       False),
    ("IRCTC.NS",      "IRCTC Ltd",                     "Travel",          False),
    ("CANBK.NS",      "Canara Bank",                   "Banking",         False),
    ("PNB.NS",        "Punjab National Bank",          "Banking",         False),
    ("BANKBARODA.NS", "Bank of Baroda",                "Banking",         False),
    ("SAIL.NS",       "Steel Authority of India",      "Metal",           False),
    ("NHPC.NS",       "NHPC Ltd",                      "Energy",          False),
    ("IOC.NS",        "Indian Oil Corporation",        "Oil & Gas",       False),
    ("HPCL.NS",       "Hindustan Petroleum",           "Oil & Gas",       False),
    ("RECLTD.NS",     "REC Ltd",                       "Finance",         False),
    ("PFC.NS",        "Power Finance Corp",            "Finance",         False),
    ("IRFC.NS",       "Indian Railway Finance",        "Finance",         False),
    ("BHEL.NS",       "Bharat Heavy Electricals",      "Engineering",     False),
    ("HAL.NS",        "Hindustan Aeronautics",         "Defence",         False),
    ("MARICO.NS",     "Marico Ltd",                    "FMCG",            False),
    ("PIDILITIND.NS", "Pidilite Industries",           "Chemicals",       False),
    ("DABUR.NS",      "Dabur India Ltd",               "FMCG",            False),
    ("COLPAL.NS",     "Colgate-Palmolive India",       "FMCG",            False),
    ("LUPIN.NS",      "Lupin Ltd",                     "Pharma",          False),
    ("AUROPHARMA.NS", "Aurobindo Pharma",              "Pharma",          False),
    ("MPHASIS.NS",    "Mphasis Ltd",                   "IT",              False),
    ("LTTS.NS",       "L&T Technology Services",      "IT",              False),
]


# ─────────────────────────────────────────────
# MAIN INITIALIZER
# ─────────────────────────────────────────────

def initialize_database():
    print("\n" + "="*55)
    print("  STALKER — MongoDB Atlas Database Setup")
    print("="*55)

    if not MONGO_URI:
        print("\n[ERROR] MONGO_URI not set in .env file")
        sys.exit(1)

    print(f"\n  Connecting to MongoDB Atlas...")

    try:
        from pymongo import MongoClient, DESCENDING, ASCENDING
        from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)

        # Test connection
        client.admin.command("ping")
        print("  Connected successfully!\n")

        db = client[DB_NAME]
        print(f"  Database: {DB_NAME}")
        print()

        # ── Create Collections + Indexes ─────────────────
        for col_name, schema in COLLECTIONS.items():
            print(f"  [{col_name}]")
            print(f"    Description: {schema['description']}")

            col = db[col_name]

            # Create indexes
            for idx in schema.get("indexes", []):
                keys = [(k, DESCENDING if v == -1 else ASCENDING) for k, v in idx["keys"]]
                options = {
                    "name":       idx["name"],
                    "background": True,
                }
                if idx.get("unique"):
                    options["unique"] = True

                try:
                    col.create_index(keys, **options)
                    print(f"    Index created: {idx['name']}")
                except Exception as e:
                    print(f"    Index already exists / skipped: {idx['name']}")

            print()

        # ── Seed stocks_universe ──────────────────────────
        col_stocks = db["stocks_universe"]
        existing   = col_stocks.count_documents({})

        if existing == 0:
            print("  Seeding stocks_universe with NSE master data...")
            docs = []
            for sym, name, sector, is_nifty50 in STOCKS_SEED:
                docs.append({
                    "symbol":        sym,
                    "name":          name,
                    "sector":        sector,
                    "is_nifty50":    is_nifty50,
                    "is_active":     True,
                    "added_on":      str(datetime.now().date()),
                    "market_cap_cr": None,
                    "avg_price":     None,
                    "notes":         "",
                })
            col_stocks.insert_many(docs)
            print(f"    Seeded {len(docs)} stocks")
        else:
            print(f"  stocks_universe already has {existing} stocks — skipped seeding")

        print()

        # ── Insert first system log ───────────────────────
        db["system_logs"].insert_one({
            "timestamp": datetime.now().isoformat(),
            "level":     "INFO",
            "event":     "db_init",
            "message":   "STALKER database initialized successfully",
            "data":      {
                "db_name":     DB_NAME,
                "collections": list(COLLECTIONS.keys()),
                "stocks_count": len(STOCKS_SEED),
            }
        })

        # ── Summary ───────────────────────────────────────
        print("="*55)
        print("  DATABASE SETUP COMPLETE!")
        print("="*55)
        print()
        print("  Collections created:")
        for col_name in COLLECTIONS:
            count = db[col_name].count_documents({})
            print(f"    {col_name:<25} {count:>4} documents")
        print()
        print(f"  Atlas URL: {MONGO_URI.split('@')[1] if '@' in MONGO_URI else MONGO_URI}")
        print()
        print("  You're ready! Run:")
        print("    python -X utf8 main.py --mode scan")
        print()

        client.close()
        return True

    except Exception as e:
        print(f"\n  [ERROR] Failed to connect: {e}")
        print()
        print("  Check:")
        print("    1. MONGO_URI is correct in .env")
        print("    2. Your IP is whitelisted in Atlas (Network Access)")
        print("    3. Username/password are correct")
        return False


if __name__ == "__main__":
    success = initialize_database()
    sys.exit(0 if success else 1)
