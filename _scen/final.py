# -*- coding: utf-8 -*-
"""定稿版候选：债券拆成「3 年档 bond3 + 7-10 年档 bond」，纳指不买。

比较纳指那 13% 的三种去向，顺便看新的债券代理（H11002/H11004）下
各方案的长度到底是多少。
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import backtest
import strategy as st

_orig_buckets = st.buckets
_orig_get = st.get


def patched_buckets():
    b = dict(_orig_buckets())
    b["bond3"] = {
        "name": "债券(1-3年)",
        "group": "无风险",
        "instruments": ["511030", "511220"],
        "premium": {"warn": 0.005, "block": 0.02},
        "proxy": {"src": "csi", "code": "H11002"},
    }
    b["bond"] = dict(b["bond"])
    b["bond"]["instruments"] = ["511520", "511270"]
    b["bond"]["proxy"] = {"src": "csi", "code": "H11004"}
    return b


st.buckets = patched_buckets

R1 = {"cash": 0.08, "bond": 0.40, "divA": 0.10, "divHK": 0.12,
      "broad": 0.02, "gold": 0.15, "ndx": 0.13}

CASES = [
    ("R1 原样（纳指13%）",
     dict(R1, bond3=0.15, bond=0.25)),
    ("纳指→债券",
     dict(R1, ndx=0.0, bond3=0.20, bond=0.33)),
    ("纳指→红利对半分",
     dict(R1, ndx=0.0, bond3=0.15, bond=0.25, divA=0.165, divHK=0.185)),
    ("纳指→红利 8 成 + 债券 2 成",
     dict(R1, ndx=0.0, bond3=0.17, bond=0.27, divA=0.15, divHK=0.17)),
]

print("债券指数本身（2008-01 起）：")
for code, nm in (("H11002", "中证1-3年综合债"),
                 ("H11003", "中证3-7年综合债"),
                 ("H11004", "中证7-10年综合债"),
                 ("H11001", "中证全债(旧代理)")):
    s = backtest.bars.csi(code)
    ds = [d for d in sorted(s) if d >= "20080101"]
    v = [s[d] for d in ds]
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    yr = {}
    for i in range(1, len(v)):
        yr[ds[i][:4]] = yr.get(ds[i][:4], 1.0) * (v[i] / v[i - 1])
    print("  %-16s 年化 %5.2f%%  最大回撤 %6.2f%%  2008 %6.2f%%  2013 %6.2f%%"
          % (nm, ((v[-1] / v[0]) ** (365.25 / ((len(ds)) / 243.0)) - 1) * 100,
             mdd * 100, (yr.get("2008", 1) - 1) * 100,
             (yr.get("2013", 1) - 1) * 100))

print()
print("%-24s %8s %9s %8s %7s %8s %8s" %
      ("组合", "年化", "最大回撤", "波动", "夏普", "最差年", "最差3年"))
for nm, w in CASES:
    cfg = dict(_orig_get("Z0"))
    cfg["weights"] = w
    st.get = lambda sid, _c=cfg: _c if sid == "Z0" else _orig_get(sid)
    backtest._PANEL_CACHE.clear()
    backtest._memo.clear()
    m = backtest.result("Z0", drag=0.0045, with_curve=False)
    print("%-24s %7.2f%% %8.2f%% %7.2f%% %7.2f %7.2f%% %7.2f%%"
          % (nm, m["cagr"] * 100, m["mdd"] * 100, m["vol"] * 100,
             m["cagr"] / m["vol"], m["worst_year"] * 100,
             m["worst_3y"] * 100))
st.get = _orig_get
