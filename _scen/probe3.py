# -*- coding: utf-8 -*-
"""Scratch probe 3: CSI index names, Tencent ETF klines, fund NAV/AC series."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

H_CSI = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Referer": "https://www.csindex.com.cn/"}
H_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

print("=== CSI search guesses ===")
guesses = [
    ("GET", "https://www.csindex.com.cn/csindex-home/search/search-index",
     {"searchInput": "短融", "pageNum": "1", "pageSize": "10"}),
    ("GET", "https://www.csindex.com.cn/csindex-home/index-list/index-list-info",
     {"indexCode": "H11010"}),
    ("GET", "https://www.csindex.com.cn/csindex-home/index-list/query-index-item",
     {"indexCode": "H11010", "pageNum": "1", "pageSize": "10"}),
    ("GET", "https://www.csindex.com.cn/csindex-home/index-list/index-list",
     {"indexCode": "H11010"}),
    ("GET", "https://www.csindex.com.cn/csindex-home/indexInfo/index-info",
     {"indexCode": "H11010"}),
    ("GET", "https://www.csindex.com.cn/csindex-home/index-detail/index-detail",
     {"indexCode": "H11010"}),
    ("GET", "https://www.csindex.com.cn/csindex-home/perf/index-info",
     {"indexCode": "H11010"}),
]
for method, url, params in guesses:
    try:
        r = requests.request(method, url, params=params, headers=H_CSI, timeout=15)
        body = r.text[:200].replace("\n", " ")
        print("%-4s %-58s -> %s %s" % (method, url.split("csindex-home")[1][:56], r.status_code, body))
    except Exception as e:
        print("%-4s %-58s -> ERR %s" % (method, url.split("csindex-home")[1][:56], type(e).__name__))

print("=== Tencent kline for ETFs (raw, first window) ===")
for code in ["sh511880", "sh511360", "sh511520", "sh511010", "sh511260"]:
    try:
        r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                         params={"param": "%s,day,2004-01-01,2010-01-01,640," % code},
                         headers=H_TX, timeout=20)
        j = r.json()
        node = (j.get("data") or {}).get(code) or {}
        rows = node.get("day") or node.get("qfqday") or []
        print("%s rows=%3d first=%s last=%s" % (code, len(rows),
              rows[0][0] if rows else "-", rows[-1][0] if rows else "-"))
    except Exception as e:
        print("%s ERR %s %s" % (code, type(e).__name__, e))

print("=== fund pingzhongdata (NAV trend / AC trend) ===")
for code in ["511880", "511360", "511520", "511010", "511260"]:
    try:
        r = requests.get("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://fund.eastmoney.com/%s.html" % code},
                         timeout=25)
        m1 = re.search(r"var Data_netWorthTrend\s*=\s*(\[.*?\]);", r.text)
        m2 = re.search(r"var Data_ACWorthTrend\s*=\s*(\[.*?\]);", r.text)
        n1 = len(json.loads(m1.group(1))) if m1 else 0
        n2 = len(json.loads(m2.group(1))) if m2 else 0
        print("%s netWorth=%4d acWorth=%4d len=%d" % (code, n1, n2, len(r.text)))
    except Exception as e:
        print("%s ERR %s %s" % (code, type(e).__name__, e))
