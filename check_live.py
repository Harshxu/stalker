import requests, json
d = requests.get('http://localhost:8000/api/live').json()
prices = d.get('prices', {})
print(f"Total symbols: {d.get('count')}")
print(f"Cache age: {d.get('cache_age_sec')}s")
print()
for s, v in list(prices.items())[:8]:
    print(f"{s:<22} price={v['price']:<10} chg%={v['change_pct']:<8} prev={v.get('prev_close')}")
