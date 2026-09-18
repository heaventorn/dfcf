# -*- coding: utf-8 -*-
"""债券腿换久期：同一个 R1 组合，只把债券腿的代理指数换掉。

H11010 短久期（1 年以内）、H11001 中证全债（基准，约 4-5 年）、
H11016/H11017 中久期、H11005 长久期（约 10 年）。
用来回答「3 年短债是不是比 7-10 年长债稳」。
"""

import datetime
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import backtest
import bars
import strategy as st

BOND = st.buckets()["bond"]["proxy"]["code"]
_orig = bars.proxy_series
ALT = None


def patched(proxy):
    if ALT and proxy and proxy.get("code") == BOND:
        return bars.csi(ALT)
    return _orig(proxy)


bars.proxy_series = patched


def own_stats(code):
    s = bars.csi(code)
    ds = [d for d in sorted(s) if d >= "20080101"]
    v = [s[d] for d in ds]
    a = datetime.date(int(ds[0][:4]), int(ds[0][4:6]), int(ds[0][6:8]))
    b = datetime.date(int(ds[-1][:4]), int(ds[-1][4:6]), int(ds[-1][6:8]))
    yrs = (b - a).days / 365.25
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    yr = {}
    for i in range(1, len(v)):
        yr[ds[i][:4]] = yr.get(ds[i][:4], 1.0) * (v[i] / v[i - 1])
    return (v[-1] / v[0]) ** (1 / yrs) - 1, mdd, yr


CASES = [("H11010 短久期(<1年)", "H11010"),
         ("H11001 中证全债(基准)", "H11001"),
         ("H11005 长久期(约10年)", "H11005")]
NOTE = ("说明：H11001 与 H11005 是同一套指数编制下的中/长两档，H11010 是短久期档；"
        "三个都从 2004-01 起、点数相同，可以直接比。")
print(NOTE)

print("债券指数本身（2008-01 起）：")
for nm, code in CASES:
    c, mdd, yr = own_stats(code)
    print("  %-22s 年化 %5.2f%%  最大回撤 %6.2f%%  2008年 %6.2f%%  2013年 %6.2f%%"
          % (nm, c * 100, mdd * 100, (yr.get("2008", 1) - 1) * 100,
             (yr.get("2013", 1) - 1) * 100))

print()
print("同一个 R1 组合（扣 0.45%/年，2006-01 起）：")
print("  %-22s %8s %9s %8s %7s %12s %9s %9s"
      % ("债券腿用", "年化", "最大回撤", "波动", "夏普", "回撤区间", "2008年", "2013年"))
for nm, code in CASES:
    ALT = code
    backtest._PANEL_CACHE.clear()
    backtest._memo.clear()
    m = backtest.result("Z0", drag=0.0045, with_curve=False)
    yr = m.get("yearly") or {}
    print("  %-22s %7.2f%% %8.2f%% %7.2f%% %7.2f %5s→%-6s %8s %8s"
          % (nm, m["cagr"] * 100, m["mdd"] * 100, m["vol"] * 100,
             m["cagr"] / m["vol"],
             (m.get("mdd_from") or "")[2:], (m.get("mdd_at") or "")[2:],
             ("%.2f%%" % (yr["2008"] * 100)) if "2008" in yr else "-",
             ("%.2f%%" % (yr["2013"] * 100)) if "2013" in yr else "-"))
