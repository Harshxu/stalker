import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

tests = [
    ("Tickertape",    "https://api.tickertape.in/stocks/quotes/ALKEM"),
    ("MoneyControl",  "https://priceapi.moneycontrol.com/pricefeed/nse/equitycash/ALKEM"),
    ("Groww_v2",      "https://groww.in/v1/api/stocks_data/v2/tr_live_data/exchange/NSE/segment/CASH/search_id/NSE_ALKEM"),
    ("BSE",           "https://api.bseindia.com/BseIndiaAPI/api/ComHeader/w?quotetype=EQ&scripcode=524348&seriesid="),
]

for name, url in tests:
    try:
        r = requests.get(url, headers=headers, timeout=6)
        print(f"{name}: HTTP {r.status_code} -> {r.text[:200]}")
    except Exception as e:
        print(f"{name}: ERROR {e}")
    print()
