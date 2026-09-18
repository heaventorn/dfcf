# -*- coding: utf-8 -*-
"""One clean table: every candidate on the same engine, same window(s),
same fee assumption. This is the version to quote.
"""

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest
import opt2 as O

O.KEYS = ["cash", "short", "rate", "bond", "credit", "divA", "divHK", "broad",
          "gold", "ndx"]
O.NAMES["bond"] = "全债"
O.PROXY["bond"] = {"src": "csi", "code": "H11001"}
O.FEE["bond"] = 0.002

K = O.KEYS


def W(cash=0.0, short=0.0, rate=0.0, bond=0.0, credit=0.0, divA=0.0,
      divHK=0.0, broad=0.0, gold=0.0, ndx=0.0):
    d = dict(cash=cash, short=short, rate=rate, bond=bond, credit=credit,
             divA=divA, divHK=divHK, broad=broad, gold=gold, ndx=ndx)
    assert abs(sum(d.values()) - 1.0) < 1e-6, (d, sum(d.values()))
    return tuple(d[k] for k in K)


Z0 = dict(cash=0.341, bond=0.284, divA=0.114, divHK=0.061, broad=0.05,
          gold=0.06, ndx=0.09)


def z0(**over):
    d = dict(Z0)
    d.update(over)
    return W(**d)


BOOK = [
    ("A2 保守E20(已发布)", W(cash=0.4125, bond=0.3375, divA=0.072, divHK=0.04,
                             broad=0.032, gold=0.05, ndx=0.056)),
    ("Z0 上轮讨论版(在跑)", W(**Z0)),
    ("A5 进取E35(已发布)", W(cash=0.33, bond=0.27, divA=0.126, divHK=0.07,
                             broad=0.056, gold=0.05, ndx=0.098)),
    ("S2 现金5%+短融29%(第二轮)", z0(cash=0.05, short=0.291)),
    ("S4 现金5%+利率债29%(第二轮)", z0(cash=0.05, rate=0.291)),
    ("S4b 现金5%+债券全给7-10年利率债", z0(cash=0.05, rate=0.575, bond=0.0)),
    ("R1 稳妥版", W(cash=0.05, short=0.03, bond=0.34, credit=0.06, divA=0.10,
                    divHK=0.12, broad=0.02, gold=0.15, ndx=0.13)),
    ("R2 进取版", W(cash=0.03, short=0.02, bond=0.25, credit=0.10, divA=0.10,
                    divHK=0.15, broad=0.03, gold=0.17, ndx=0.15)),
    ("F11 前沿(mdd<=11%,带约束)", W(cash=0.01, short=0.03, rate=0.10, credit=0.16,
                                    divA=0.10, divHK=0.18, broad=0.04, gold=0.18,
                                    ndx=0.20)),
    ("F13 前沿(2008后最优,mdd13%)", W(cash=0.01, short=0.08, rate=0.04, credit=0.05,
                                      divA=0.01, divHK=0.20, gold=0.28, ndx=0.33)),
]


def seg_stats(navs, dates, beg):
    i0 = next(i for i, d in enumerate(dates) if d >= beg)
    seg = [v / navs[i0] for v in navs[i0:]]
    return backtest.metrics(seg, dates[i0:])


def main():
    O.setup()
    s = O.S
    dates = s["dates"]
    print("panel %s -> %s ; fees charged per leg" % (dates[0], dates[-1]))
    print()
    print("%-30s %21s %21s %21s"
          % ("", "2008-01 onwards", "2017-01 onwards", "2020-01 onwards"))
    print("%-30s %7s %7s %6s |%7s %7s %6s |%7s %7s %6s"
          % ("portfolio", "cagr", "mdd", "vol", "cagr", "mdd", "vol",
             "cagr", "mdd", "vol"))
    out = {}
    for name, tup in BOOK:
        navs = O._run(tup, True)
        cells, rec = [], {}
        for beg in ("20080101", "20170101", "20200101"):
            m = seg_stats(navs, dates, beg)
            rec[beg] = {"cagr": m["cagr"], "mdd": m["mdd"], "mdd_at": m["mdd_at"],
                        "vol": m["vol"], "worst_year": m["worst_year"],
                        "worst_3y": m["worst_3y"], "under_years": m["under_years"]}
            cells.append("%6.2f%% %6.2f%% %5.2f%%" % (m["cagr"] * 100,
                                                      m["mdd"] * 100,
                                                      m["vol"] * 100))
        out[name] = {"weights": dict(zip(K, tup)), "windows": rec}
        print("%-30s %s |%s |%s" % (name, cells[0], cells[1], cells[2]))

    print()
    print("=== 2008+ detail: worst calendar year / worst rolling 3y / max underwater")
    for name, _t in BOOK:
        r = out[name]["windows"]["20080101"]
        print("  %-30s worstYr %6.2f%%  worst3Y %6.2f%%  underwater %4.1fy  mdd at %s"
              % (name, r["worst_year"] * 100, (r["worst_3y"] or 0) * 100,
                 r["under_years"], r["mdd_at"]))

    print()
    print("=== crisis windows (2008+ segment, per-leg fees) ===")
    for nm, lo, hi in backtest.CRISES:
        row = "  %-20s" % nm
        for name in ("Z0 上轮讨论版(在跑)", "S2 现金5%+短融29%(第二轮)",
                     "R1 稳妥版", "R2 进取版"):
            tup = dict(BOOK)[name]
            navs = O._run(tup, True)
            i0 = next(i for i, d in enumerate(dates) if d >= "20080101")
            seg = [v / navs[i0] for v in navs[i0:]]
            ws = backtest.window_stats(seg, dates[i0:], lo, hi)
            row += (" %s %+6.2f%%/%6.2f%%" % (name.split()[0], ws["ret"] * 100,
                                              ws["mdd"] * 100)) if ws else ""
        print(row)

    print()
    print("=== yearly returns (2008+, per-leg fees) ===")
    ytab = {}
    for name in ("Z0 上轮讨论版(在跑)", "S4 现金5%+利率债29%(第二轮)", "R1 稳妥版",
                 "R2 进取版", "F13 前沿(2008后最优,mdd13%)"):
        navs = O._run(dict(BOOK)[name], True)
        i0 = next(i for i, d in enumerate(dates) if d >= "20080101")
        seg = [v / navs[i0] for v in navs[i0:]]
        ytab[name] = backtest.metrics(seg, dates[i0:])["yearly"]
    years = sorted(next(iter(ytab.values())))
    print("  %-6s %s" % ("year", " ".join("%9s" % n.split()[0] for n in ytab)))
    for y in years:
        print("  %-6s %s" % (y, " ".join(
            ("%8.2f%%" % (ytab[n][y] * 100)) if y in ytab[n] else "      n/a"
            for n in ytab)))

    with open(os.path.join(HERE, "report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved _scen/report.json")


if __name__ == "__main__":
    main()
