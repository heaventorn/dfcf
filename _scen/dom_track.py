# -*- coding: utf-8 -*-
"""无浏览器检查策略台「盈亏追踪」页签：真开一次页面，点页签，读回每块内容。

用法：py _scen/dom_track.py [url]
只读，不会写任何数据（页面自己会记一条当天快照，和平时打开页面一样）。
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/strategy"

from playwright.sync_api import sync_playwright

IDS = ["trackTop", "trackCards", "trackLegs", "trackNote",
       "trackMarks", "trackSnaps", "trackMsg"]

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="networkidle", timeout=120000)
    pg.click("#tab-track")
    pg.wait_for_timeout(4000)
    print("--- 买卖计划（配置与计划页签）---")
    print(pg.inner_text("#plan").strip())
    print()
    for i in IDS:
        txt = pg.inner_text("#" + i) if pg.query_selector("#" + i) else "(缺元素)"
        print("--- %s ---" % i)
        print(txt.strip() or "(空)")
    print()
    print("页面报错：", errs or "无")
    b.close()
