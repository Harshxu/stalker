# -*- coding: utf-8 -*-
import json, os, datetime

data_dir = r'c:\Users\ADMIN\Desktop\antigravity\Stalker\data'
files = ['fundamentals_cache.json', 'news_cache.json', 'latest_scan.json']
for fname in files:
    path = os.path.join(data_dir, fname)
    if os.path.exists(path):
        mtime = os.path.getmtime(path)
        dt = datetime.datetime.fromtimestamp(mtime)
        size = os.path.getsize(path)
        print(fname + ": last modified " + dt.strftime("%Y-%m-%d %H:%M") + " | size=" + str(size//1024) + "KB")
