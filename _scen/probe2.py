# -*- coding: utf-8 -*-
"""Scratch probe 2: resolve index names for CSI codes, and debug the EM fetch."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

H_CSI = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Referer": "https://www.csindex.com.cn/"}
H_EM = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/"}

print("=== EM raw debug (511880) ===")
try:
    r = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                     params={"secid": "1.511880", "fields1": "f1,f2,f3,f4,f5,f6",
                             "fields2": "f51,f52,f53,f54,f55,f56,f57",
                             "klt": 101, "fqt": 1, "beg": "20040101", "end": "20500101"},
                     headers=H_EM, timeout=30)
    print("status", r.status_code, "len", len(r.text))
    print(r.text[:300])
except Exception as e:
    print("ERR", type(e).__name__, e)

print("=== EM raw debug (511520 via 0. prefix) ===")
try:
    r = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                     params={"secid": "1.511520", "fields1": "f1,f2,f3,f4,f5,f6",
                             "fields2": "f51,f52,f53,f54,f55,f56,f57",
                             "klt": 101, "fqt": 0, "beg": "0", "end": "20500101"},
                     headers=H_EM, timeout=30)
    print("status", r.status_code, "len", len(r.text))
    print(r.text[:300])
except Exception as e:
    print("ERR", type(e).__name__, e)

print("=== CSI name resolution ===")
paths = [
    "https://www.csindex.com.cn/csindex-home/indexInfo/index-basic-info?indexCode=%s",
    "https://www.csindex.com.cn/csindex-home/index-list/query-index-item?indexCode=%s",
    "https://www.csindex.com.cn/csindex-home/index/index-basic-info?indexCode=%s",
    "https://www.csindex.com.cn/csindex-home/index-detail/basic-info?indexCode=%s",
]
for p in paths:
    try:
        r = requests.get(p % "H11001", headers=H_CSI, timeout=20)
        print("PATH", p.split("csindex-home")[1][:45], "->", r.status_code, r.text[:160].replace("\n", " "))
    except Exception as e:
        print("PATH", p.split("csindex-home")[1][:45], "-> ERR", type(e).__name__, e)
