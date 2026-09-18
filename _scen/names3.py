# -*- coding: utf-8 -*-
"""把中证债券指数家族的名字查出来，好挑一个真正「3 年期」的代理。"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.csindex.com.cn/"}
CODES = ["H11002", "H11003", "H11004", "H11006", "H11007", "H11009",
         "H11012", "H11013", "H11014", "H11017", "H11018", "H11019",
         "H11077", "H11075", "H11074", "H11073", "H11071", "H11070"]

for code in CODES:
    try:
        r = requests.get(
            "https://www.csindex.com.cn/csindex-home/perf/index-perf",
            params={"indexCode": code, "startDate": "20250101",
                    "endDate": "20250110"},
            headers=H, timeout=30)
        d = r.json().get("data") or []
        if not d:
            print("%-7s 无数据" % code)
            continue
        print("%-7s %s" % (code, d[0].get("indexNameCnAll")))
    except Exception as e:
        print(code, "ERR", e)
