# -*- coding: utf-8 -*-
"""压力测试：把 2008 年债券腿的收益改写成 -10%（当年实际是涨的），
看三个方案的年度最大回撤会走到哪。用来给「回撤不能只靠股债对冲」那句话
提供数字。

做法：只改债券腿 2008 年那一段的日收益（按比例缩放到全年 -10%），
之后所有年份的收益保持不变（整条曲线整体下移一个常数倍）。
R2 起债券拆成两档（bond = 7-10 年 H11004、bond3 = 1-3 年 H11002），
两档一起压 —— 久期越长，这一项越难受。
"""

import io
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import backtest
import bars
import strategy as st

BOND_BUCKETS = ("bond", "bond3")
YEAR = "2008"
SCALE = -0.10          # 目标：这一年债券腿 -10%

_orig = bars.proxy_series
BONDS = {}
PROXIES = {}
for _k in BOND_BUCKETS:
    _p = (st.buckets().get(_k) or {}).get("proxy") or {}
    if _p.get("code"):
        PROXIES[_p["code"]] = _p
        BONDS[_p["code"]] = _orig(_p)


def patched(proxy):
    s = _orig(proxy)
    if not s or not proxy or proxy.get("code") not in BONDS:
        return s
    days = sorted(s)
    y = [d for d in days if d.startswith(YEAR)]
    if not y:
        return s
    before = [d for d in days if d < y[0]]
    if not before:
        return s
    anchor = before[-1]
    F = s[y[-1]] / s[anchor]
    k = math.log(1.0 + SCALE) / math.log(F)
    out, cur, prev = {}, s[anchor], anchor
    for d in before:
        out[d] = s[d]
    for d in days:
        if d <= anchor:
            continue
        r = s[d] / s[prev] - 1.0
        if d.startswith(YEAR):
            r = (1.0 + r) ** k - 1.0
        cur = cur * (1.0 + r)
        out[d] = cur
        prev = d
    return out


bars.proxy_series = patched
backtest._PANEL_CACHE.clear()
backtest._memo.clear()

d, c = backtest._panel()
print("panel %s ~ %s" % (d[0], d[-1]))


def year_ret(ser, y):
    ds = sorted(ser)
    got = [x for x in ds if x.startswith(y)]
    if not got:
        return None
    prior = [x for x in ds if x < got[0]]
    a = ser[prior[-1]] if prior else ser[got[0]]
    return ser[got[-1]] / a - 1.0


for code, ser in BONDS.items():
    print("债券腿 %s %s 年收益：原始 %.2f%% → 压测 %.2f%%"
          % (code, YEAR, year_ret(ser, YEAR) * 100,
             year_ret(patched(dict(PROXIES[code])), YEAR) * 100))
print()
print("%-4s %-12s %10s %10s" % ("id", "名称", "年化", "最大回撤"))
for sid in ("A2", "A5", "Z0"):
    m = backtest.result(sid, drag=0.0045, with_curve=False)
    print("%-4s %-12s %9.2f%% %9.2f%%"
          % (sid, m.get("name"), m["cagr"] * 100, m["mdd"] * 100))
