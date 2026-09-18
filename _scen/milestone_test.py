# -*- coding: utf-8 -*-
"""验证 tracker 的历史里程碑分支：假装账本是一年前建的。

份额和金额用今天的实际计划（不动），只把每条腿的 nav0 换成那天真实的
复权净值，等于问“一年前按同样金额买入，今天长什么样”。
注意：这不是实盘口径，只是把 91/183/365/730 天那段代码真跑一遍。
"""
import io
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tracker

START = "20250919"

t = json.load(io.open(os.path.join(BASE, "output", "track.json"),
                      encoding="utf-8"))
t["start"] = START
t["snapshots"] = []
for l in t["legs"]:
    n0 = tracker.nav_at(l["code"], START)
    assert n0, l["code"]
    l["nav0"] = n0
c = t["cash"]
n0 = tracker.nav_at(c["vehicle"], START)
assert n0, c["vehicle"]
c["nav0"] = n0

tmp = os.path.join(BASE, "_scen", "track_test.json")
json.dump(t, io.open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
tracker.FILE = tmp
tracker._NAV.clear()
print(tracker.text_report(tracker.report(record=False)))
print()
print("各腿从 %s 到今天的真实收益：" % START)
for l in t["legs"]:
    n = tracker.nav_latest(l["code"])[0]
    print("  %-6s %-10s %8.3f -> %8.3f  %+7.2f%%"
          % (l["code"], l["name"], l["nav0"], n, (n / l["nav0"] - 1) * 100))
