# -*- coding: utf-8 -*-
"""Deployable frontier: what is left of the 8%/-13% idea once you insist on
caps you can actually hold (QDII premium, gold size, real credit vehicles) and
on a credit haircut for proxy-vs-vehicle slippage.

usage: py _scen/opt4.py [credit_extra_haircut]
"""

import datetime
import json
import os
import random
import sys
from multiprocessing import Pool

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import opt2 as O

KEYS = O.KEYS
CREDIT_EXTRA = float(sys.argv[1]) if len(sys.argv) > 1 else 0.008

CAPS = {"cash": 0.15, "short": 0.30, "rate": 0.25, "credit": 0.20, "divA": 0.15,
        "divHK": 0.20, "broad": 0.15, "gold": 0.20, "ndx": 0.20}

# deployable candidates, hand-built
CAND = {
    "Z0 现状":            (0.341, 0.0, 0.284, 0.0, 0.114, 0.061, 0.05, 0.06, 0.09),
    "现金10%其余债券":     (0.03, 0.07, 0.525, 0.0, 0.114, 0.061, 0.05, 0.06, 0.09),
    "现金5%+利率债30%":    (0.02, 0.03, 0.30, 0.10, 0.13, 0.09, 0.04, 0.11, 0.18),
    "均衡版":             (0.03, 0.05, 0.22, 0.15, 0.10, 0.12, 0.03, 0.15, 0.15),
    "进攻版":             (0.02, 0.03, 0.15, 0.15, 0.10, 0.15, 0.03, 0.17, 0.20),
}


def _fee():
    O.FEE["credit"] = 0.004 + CREDIT_EXTRA


def sample(rng):
    while True:
        raw = [rng.expovariate(0.9) for _ in KEYS]
        t = sum(raw)
        w = {k: raw[i] / t for i, k in enumerate(KEYS)}
        if all(w[k] <= CAPS[k] + 1e-9 for k in KEYS):
            return tuple(w[k] for k in KEYS)


def _init4():
    _fee()
    O._init()


def _meas(navs, dates, i0, i1):
    seg, dd = navs[i0:i1], dates[i0:i1]
    if len(seg) < 60:
        return None
    a = datetime.date(int(dd[0][:4]), int(dd[0][4:6]), int(dd[0][6:8]))
    b = datetime.date(int(dd[-1][:4]), int(dd[-1][4:6]), int(dd[-1][6:8]))
    yrs = (b - a).days / 365.25
    cagr = (seg[-1] / seg[0]) ** (1 / yrs) - 1 if yrs > 0.5 else 0.0
    peak, mdd, at = seg[0], 0.0, dd[0]
    for i, v in enumerate(seg):
        peak = max(peak, v)
        if v / peak - 1 < mdd:
            mdd, at = v / peak - 1, dd[i]
    # worst calendar year + worst rolling 3y
    yr = {}
    for i, d in enumerate(dd):
        yr.setdefault(d[:4], [v0 := seg[i], v0])[1] = seg[i]
    years = sorted(yr)
    wy = min((yr[y][1] / yr[prev][1] - 1 for prev, y in zip(years, years[1:])),
             default=0.0)
    w3 = 0.0
    for i in range(len(seg)):
        j = i + 750
        if j < len(seg):
            w3 = min(w3, (seg[j] / seg[i]) ** (1 / 3.0) - 1)
    return cagr, mdd, at, wy, w3


def _work4(batch):
    if not O._STATE:
        _init4()
    s = O.S
    n = len(s["dates"])
    cuts = O.CUTS
    out = []
    for tup in batch:
        navs = O._run(tup, True)
        out.append([_meas(navs, s["dates"], s["i2008"], n)]
                   + [_meas(navs, s["dates"], s["i2008"], c) for c in cuts]
                   + [_meas(navs, s["dates"], c, n) for c in cuts])
    return out


def pct(x):
    return "  n/a " if x is None else "%6.2f%%" % (x * 100)


def line(tag, tup, r):
    f = r[0]
    print("  %-22s cagr=%s mdd=%s worstY=%s worst3Y=%s at=%s   %s"
          % (tag, pct(f[0]), pct(f[1]), pct(f[3]), pct(f[4]), f[2],
             " ".join("%s%.0f" % (O.NAMES[k][:2], tup[i] * 100)
                      for i, k in enumerate(KEYS) if tup[i] > 0.005)))


def main():
    _fee()
    O.setup()
    s = O.S
    print("credit haircut = %.2f%%/yr extra;  caps=%s"
          % (CREDIT_EXTRA * 100, {k: "%.0f%%" % (v * 100) for k, v in CAPS.items()}))

    rng = random.Random(7)
    allx = [sample(rng) for _ in range(9000)]
    for v in CAND.values():
        allx.append(v)

    chunk = 200
    batches = [allx[i:i + chunk] for i in range(0, len(allx), chunk)]
    t0 = datetime.datetime.now()
    res = []
    with Pool(processes=16, initializer=_init4) as pool:
        for out in pool.imap(_work4, batches):
            res.extend(out)
    print("evaluated %d in %.0fs" % (len(res), (datetime.datetime.now() - t0).total_seconds()))
    rows = list(zip(allx, res))

    print()
    print("head  cagr   mdd    worstY  worst3Y  at        weights(full 2008+, fees)")
    for tag, v in CAND.items():
        for x, r in rows:
            if x == v:
                line(tag, x, r)
                break

    print()
    print("=== best under caps, by mdd budget (full 2008+, fees) ===")
    picks = {}
    for b in (0.08, 0.09, 0.10, 0.11, 0.12, 0.13):
        best = None
        for tup, r in rows:
            if r[0][1] >= -b - 1e-9 and (best is None or r[0][0] > best[1][0][0]):
                best = (tup, r)
        if best:
            picks["%.2f" % b] = best
            line("mdd<=%.0f%%" % (b * 100), best[0], best[1])
            r = best[1]
            print("        oos 2008-2016->2017+:  IS %s / OOS %s   |  IS %s / OOS %s"
                  % (pct(r[1][0]), pct(r[3][0]), pct(r[1][1]), pct(r[3][1])))

    out = {"credit_extra": CREDIT_EXTRA, "caps": CAPS,
           "picks": {k: {"weights": list(v[0]), "full": list(v[1][0]),
                         "is2016": list(v[1][1]), "oos2017": list(v[1][3]),
                         "is2019": list(v[1][2]), "oos2020": list(v[1][4])}
                     for k, v in picks.items()},
           "cand": {t: list(x) for t, x in CAND.items()}}
    with open(os.path.join(HERE, "opt4.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved", os.path.join(HERE, "opt4.json"))


if __name__ == "__main__":
    main()
