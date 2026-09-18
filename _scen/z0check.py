# -*- coding: utf-8 -*-
"""Same Z0 weights, four ways, to pin down the drawdown discrepancy."""

import datetime
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest
import premium as pm
import risk
import strategy as st

import opt2 as O

Z0 = dict(st.get("Z0")["weights"])
print("Z0 as published:", Z0)


def stat(navs, dates, beg="20080101"):
    i0 = next(i for i, d in enumerate(dates) if d >= beg)
    seg, dd = navs[i0:], dates[i0:]
    a = datetime.date(int(dd[0][:4]), int(dd[0][4:6]), int(dd[0][6:8]))
    b = datetime.date(int(dd[-1][:4]), int(dd[-1][4:6]), int(dd[-1][6:8]))
    yrs = (b - a).days / 365.25
    cagr = (seg[-1] / seg[0]) ** (1 / yrs) - 1
    peak, mdd, at = seg[0], 0.0, dd[0]
    for i, v in enumerate(seg):
        peak = max(peak, v)
        if v / peak - 1 < mdd:
            mdd, at = v / peak - 1, dd[i]
    return cagr, mdd, at


def orig_run(w, beg="20060104"):
    """scen.py style: untouched bucket map (bond -> H11001)."""
    dates, _ = backtest._panel()
    return backtest.run_nav(
        w, gate=True, drag=0.0, months=st.rebalance_months(),
        gate_proxy=st.gate_cfg().get("proxy"), window=200, cut=0.5,
        risk_buckets=st.gate_cfg().get("risk_buckets"), risk_overlay=st.risk_cfg(),
        airman_series=risk.load_airman_series()), dates


def main():
    # (a) published 7 buckets, no fees
    navs, dates = orig_run(Z0)
    print("a) 7 buckets w/ bond=H11001, no fees   cagr=%6.2f%% mdd=%7.2f%% (%s)"
          % tuple(x * 100 if isinstance(x, float) else x for x in stat(navs, dates)))

    # now switch to opt2's patched universe
    O.setup()
    s = O.S
    dts = s["dates"]
    tot = sum(Z0.values())
    print("   sum of published weights = %.4f" % tot)
    t_bond = tuple(float(Z0.get(k, 0.0)) for k in O.KEYS)  # 'bond' -> 0
    t_rate = tuple(float(Z0.get(k, 0.0)) + (float(Z0.get("bond", 0.0)) if k == "rate" else 0.0)
                   for k in O.KEYS)

    for label, tup, feemode in (("b) rate=H11006, no fees", t_rate, False),
                                ("c) rate=H11006, per-leg fees", t_rate, True),
                                ("d) bond dropped (71.6%), no fees", t_bond, False),
                                ("e) bond dropped (71.6%), fees", t_bond, True)):
        navs = O._run(tup, feemode)
        i0 = next(i for i, d in enumerate(dts) if d >= "20080101")
        print("     [dbg] len(navs)=%d len(dts)=%d i0=%d seg0=%.4f segN=%.4f %s %s"
              % (len(navs), len(dts), i0, navs[i0], navs[-1], dts[i0], dts[-1]))
        print("%-34s cagr=%6.2f%% mdd=%7.2f%% (%s)  navs %d %.4f->%.4f"
              % ((label,) + tuple(stat(navs, dts)) + (len(navs), navs[0], navs[-1])))


if __name__ == "__main__":
    main()
