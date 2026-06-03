# -*- coding: utf-8 -*-
"""
STALKER — Live Price Accuracy Test Suite (Standalone)
======================================================
Does NOT import api_server or main — no email, no side effects.
Directly replicates the logic of _fetch_live_prices and cross-checks
it against yfinance fast_info (the ground-truth reference).

Run: python test_prices.py
"""
import sys, io
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import time
import math
import json
import requests as req
from datetime import datetime, timezone, timedelta

import yfinance as yf

# ─── Test symbols ────────────────────────────────────────────────────────────
TEST_SYMBOLS = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "HINDALCO.NS",    # user reported wrong
    "SBIN.NS",
    "ETERNAL.NS",
    "BAJFINANCE.NS",
    "TMPV.NS",
    "WIPRO.NS",
]

TOLERANCE_PCT = 0.5   # ±0.5% max difference between our price and ground truth

IST         = timedelta(hours=5, minutes=30)
now_ist     = datetime.now(timezone.utc) + IST
now_mins    = now_ist.hour * 60 + now_ist.minute
MARKET_OPEN = now_ist.weekday() < 5 and 9*60+15 <= now_mins <= 15*60+30

def safe(val, nd=2):
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, nd)
    except:
        return None

def pct_diff(a, b):
    try:
        return abs((a - b) / b) * 100
    except:
        return None

P = "PASS"
F = "FAIL"
W = "WARN"

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*72)
print(f"  STALKER Price Accuracy Test Suite")
print(f"  Time  : {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")
print(f"  Market: {'OPEN' if MARKET_OPEN else 'CLOSED'}")
print("="*72)


# ─────────────────────────────────────────────────────────────────────────────
# STEP A: Collect ground-truth via fast_info (fastest, most reliable)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[Step A] Fetching ground-truth prices via fast_info for {len(TEST_SYMBOLS)} symbols...")
ground_truth = {}
for sym in TEST_SYMBOLS:
    try:
        fi  = yf.Ticker(sym).fast_info
        lp  = safe(fi.last_price)
        pc  = safe(fi.previous_close)
        dh  = safe(fi.day_high)
        dl  = safe(fi.day_low)
        ground_truth[sym] = {
            "price":     lp,
            "prev_close": pc,
            "day_high":  dh,
            "day_low":   dl,
            "change_pct": round((lp - pc) / pc * 100, 2) if lp and pc else None,
        }
        print(f"  {sym:<20} ₹{lp:>8.2f}   chg={((lp-pc)/pc*100):>+6.2f}%   H=₹{dh:.2f}  L=₹{dl:.2f}" if lp and pc and dh and dl else f"  {sym:<20}  N/A")
        time.sleep(0.12)
    except Exception as e:
        print(f"  {sym:<20}  ERROR: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP B: Replicate _fetch_live_prices() logic (standalone, no import)
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[Step B] Running our fetch logic ({'1m intraday' if MARKET_OPEN else '1d daily'})...")

def get_browser_session():
    s = req.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s

our_results = {}
try:
    fetch_period   = "1d"   if MARKET_OPEN else "5d"
    fetch_interval = "1m"   if MARKET_OPEN else "1d"

    session = get_browser_session()
    data = yf.download(
        TEST_SYMBOLS,
        period=fetch_period,
        interval=fetch_interval,
        group_by="ticker",
        threads=True,
        progress=False,
        session=session,
    )

    for sym in TEST_SYMBOLS:
        try:
            df = data[sym].copy() if len(TEST_SYMBOLS) > 1 else data
            df.dropna(subset=["Close"], inplace=True)
            if df.empty:
                print(f"  {sym:<20}  Empty DataFrame")
                continue

            last_price = float(df.iloc[-1]["Close"])
            day_high   = float(df["High"].max())
            day_low    = float(df["Low"].min())
            vol        = int(df["Volume"].sum()) if MARKET_OPEN else int(df.iloc[-1]["Volume"])

            # Get previous close
            prev_close = None
            if MARKET_OPEN:
                try:
                    dd = yf.download(sym, period="2d", interval="1d", progress=False, session=get_browser_session())
                    dd.dropna(subset=["Close"], inplace=True)
                    prev_close = float(dd.iloc[-2]["Close"]) if len(dd) >= 2 else float(dd.iloc[-1]["Open"])
                except:
                    pass
            else:
                prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else None

            change     = round(last_price - prev_close, 2) if prev_close else 0.0
            change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

            our_results[sym] = {
                "price":      round(last_price, 2),
                "prev_close": round(prev_close, 2) if prev_close else None,
                "change":     change,
                "change_pct": change_pct,
                "day_high":   round(day_high, 2) if not math.isnan(day_high) else None,
                "day_low":    round(day_low, 2)  if not math.isnan(day_low)  else None,
                "volume":     vol,
            }
            print(f"  {sym:<20} ₹{last_price:>8.2f}   chg={change_pct:>+6.2f}%   H=₹{day_high:.2f}  L=₹{day_low:.2f}")
        except Exception as inner:
            print(f"  {sym:<20}  Parse error: {inner}")

except Exception as bulk_err:
    print(f"  Bulk download failed: {bulk_err}. Falling back to fast_info...")
    for sym in TEST_SYMBOLS:
        try:
            fi = yf.Ticker(sym, session=get_browser_session()).fast_info
            lp = safe(fi.last_price)
            pc = safe(fi.previous_close)
            if not lp:
                continue
            change     = round(lp - pc, 2) if pc else 0.0
            change_pct = round(change / pc * 100, 2) if pc else 0.0
            our_results[sym] = {
                "price": lp, "prev_close": pc, "change": change,
                "change_pct": change_pct,
                "day_high": safe(fi.day_high), "day_low": safe(fi.day_low),
            }
            print(f"  {sym:<20} ₹{lp:>8.2f}   chg={change_pct:>+6.2f}% (fast_info fallback)")
            time.sleep(0.15)
        except Exception as fe:
            print(f"  {sym:<20}  fast_info error: {fe}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP C: Compare our results vs ground truth
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[Step C] Cross-checking our output vs ground-truth (tolerance ±{TOLERANCE_PCT}%)\n")
hdr = f"  {'Symbol':<20} {'GT':>10} {'Ours':>10} {'Diff%':>7} {'GT Chg%':>9} {'Our Chg%':>9}  Result"
print(hdr)
print("  " + "─"*(len(hdr)-2))

total = 0
passed = 0
failures = []

for sym in TEST_SYMBOLS:
    gt   = ground_truth.get(sym, {})
    ours = our_results.get(sym, {})

    gtp  = gt.get("price")
    ourp = ours.get("price")
    gtc  = gt.get("change_pct")
    ourc = ours.get("change_pct")

    total += 1

    if gtp is None or ourp is None:
        verdict = f"{W} MISSING"
        failures.append((sym, "price missing"))
    else:
        diff = pct_diff(ourp, gtp)
        if diff is not None and diff <= TOLERANCE_PCT:
            verdict = f"{P}"
            passed += 1
        else:
            verdict = f"{F} diff={diff:.3f}%"
            failures.append((sym, f"price diff={diff:.3f}%"))

    gtp_s  = f"₹{gtp:>8.2f}"  if gtp  else "       N/A"
    ourp_s = f"₹{ourp:>8.2f}" if ourp else "       N/A"
    diffs  = f"{pct_diff(ourp,gtp):>6.3f}%" if (gtp and ourp) else "     N/A"
    gtcs   = f"{gtc:>+8.2f}%" if gtc  is not None else "       N/A"
    ourcs  = f"{ourc:>+8.2f}%" if ourc is not None else "       N/A"

    print(f"  {sym:<20} {gtp_s} {ourp_s} {diffs} {gtcs} {ourcs}  {verdict}")

    # Day high/low checks during market hours
    if MARKET_OPEN:
        gtdh  = gt.get("day_high");  ourdh = ours.get("day_high")
        gtdl  = gt.get("day_low");   ourdl = ours.get("day_low")
        if gtdh and ourdh:
            dh_d = pct_diff(ourdh, gtdh)
            if dh_d and dh_d > TOLERANCE_PCT:
                print(f"    {W} DayHigh mismatch: GT=₹{gtdh} Ours=₹{ourdh} diff={dh_d:.2f}%")
                failures.append((sym, f"day_high diff={dh_d:.2f}%"))
        if gtdl and ourdl:
            dl_d = pct_diff(ourdl, gtdl)
            if dl_d and dl_d > TOLERANCE_PCT:
                print(f"    {W} DayLow  mismatch: GT=₹{gtdl} Ours=₹{ourdl} diff={dl_d:.2f}%")
                failures.append((sym, f"day_low diff={dl_d:.2f}%"))

print(f"\n  Result: {passed}/{total} passed")

if failures:
    print(f"\n  Failures:")
    for sym, reason in failures:
        print(f"    {F} {sym}: {reason}")
else:
    print(f"\n  {P} All checks passed!")


# ─────────────────────────────────────────────────────────────────────────────
# STEP D: Hit the running server /api/live
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n[Step D] Checking /api/live endpoint vs ground-truth\n")
try:
    r = req.get("http://localhost:8090/api/live", timeout=30)
    r.raise_for_status()
    api = r.json()

    prices    = api.get("prices", {})
    mkt_closed = api.get("_market_closed", False)
    cache_age  = api.get("cache_age_sec", "N/A")

    print(f"  Market closed flag : {mkt_closed}")
    print(f"  Symbols tracked    : {api.get('count', 0)}")
    print(f"  Cache age          : {cache_age}s")
    print()

    if mkt_closed:
        if not prices:
            print(f"  {P} Market closed — no live prices exposed (correct)")
        else:
            print(f"  {F} Market closed but prices are still being returned!")
    elif not prices:
        print(f"  {W} No picks tracked yet (morning scan may not have run today)")
    else:
        api_fails = []
        hdr2 = f"  {'Symbol':<20} {'API Price':>10} {'GT Price':>10} {'Diff%':>7} {'API Chg%':>9}  Result"
        print(hdr2)
        print("  " + "─"*(len(hdr2)-2))
        for sym, d in prices.items():
            api_price  = d.get("price")
            api_chg    = d.get("change_pct")
            gt         = ground_truth.get(sym, {})
            gtp        = gt.get("price")

            if gtp and api_price:
                diff = pct_diff(api_price, gtp)
                ok   = diff is not None and diff <= TOLERANCE_PCT
                verd = P if ok else F
                if not ok:
                    api_fails.append((sym, f"diff={diff:.3f}%"))
            else:
                diff = None
                verd = W

            apip_s = f"₹{api_price:>8.2f}" if api_price else "       N/A"
            gtp_s  = f"₹{gtp:>8.2f}"       if gtp       else "       N/A"
            diff_s = f"{diff:>6.3f}%"       if diff is not None else "     N/A"
            chg_s  = f"{api_chg:>+8.2f}%"  if api_chg is not None else "       N/A"

            print(f"  {sym:<20} {apip_s} {gtp_s} {diff_s} {chg_s}  {verd}")

        if api_fails:
            print(f"\n  {F} API price mismatches:")
            for sym, r2 in api_fails:
                print(f"    {sym}: {r2}")
        else:
            print(f"\n  {P} /api/live prices match ground-truth within ±{TOLERANCE_PCT}%")

except req.exceptions.ConnectionError:
    print(f"  {W} localhost:8000 not reachable — server may not be running")
except Exception as ex:
    print(f"  {F} Error: {ex}")


print(f"\n{'='*72}")
print("  Done.")
print(f"{'='*72}\n")
