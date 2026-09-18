# -*- coding: utf-8 -*-
"""如果把仓位往红利腿上挪，回测会变成什么样。

以当前启用的 Z0（R1 稳妥版）为基准，只改权重，其余口径一律不动：
同一份 gate、同一套风险预算、同样扣 0.45%/年。
"""

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import backtest
import strategy as st

BASE = dict(st.get("Z0")["weights"])
_orig_get = st.get


def variant(**kw):
    w = dict(BASE)
    for k, v in kw.items():
        w[k] = v
    s = sum(w.values())
    return {k: v / s for k, v in w.items() if v > 1e-9}


CASES = [
    ("基准 R1", dict(BASE)),
    ("纳指 13% 全给 A股红利", variant(ndx=0.0, divA=BASE["divA"] + 0.13)),
    ("纳指 13% 全给 港股红利", variant(ndx=0.0, divHK=BASE["divHK"] + 0.13)),
    ("纳指 13% 拆给两腿红利", variant(ndx=0.0, divA=BASE["divA"] + 0.065,
                                      divHK=BASE["divHK"] + 0.065)),
    ("纳指+黄金 28% 拆给两腿红利", variant(ndx=0.0, gold=0.0,
                                            divA=BASE["divA"] + 0.14,
                                            divHK=BASE["divHK"] + 0.14)),
    ("红利腿各加到 16%（减债减金）",
     variant(divA=0.16, divHK=0.16, bond=0.32, gold=0.11, ndx=0.13)),
]

print("%-26s %8s %9s %8s %7s" % ("情形", "年化", "最大回撤", "波动", "夏普"))
for name, w in CASES:
    cfg = dict(_orig_get("Z0"))
    cfg["weights"] = w
    st.get = lambda sid, _c=cfg: _c if sid == "Z0" else _orig_get(sid)
    backtest._memo.clear()
    m = backtest.result("Z0", drag=0.0045, with_curve=False)
    print("%-26s %7.2f%% %8.2f%% %7.2f%% %7.2f"
          % (name, m["cagr"] * 100, m["mdd"] * 100, m["vol"] * 100,
             m["cagr"] / m["vol"]))
st.get = _orig_get
