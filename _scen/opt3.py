# -*- coding: utf-8 -*-
"""Out-of-sample honesty check.

Search the frontier on 2008->2016 (and 2008->2019), then measure the *same*
weights on the years the search never saw. If a portfolio only works because
it loaded up on the things that happened to win 2008-2026, the out-of-sample
half will say so.
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


def _measure(navs, dates, i0, i1):
    seg, dd = navs[i0:i1], dates[i0:i1]
    if len(seg) < 60:
        return None
    a = datetime.date(int(dd[0][:4]), int(dd[0][4:6]), int(dd[0][6:8]))
    b = datetime.date(int(dd[-1][:4]), int(dd[-1][4:6]), int(dd[-1][6:8]))
    yrs = (b - a).days / 365.25
    cagr = (seg[-1] / seg[0]) ** (1 / yrs) - 1 if yrs > 0.5 else 0.0
    peak, mdd = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return cagr, mdd


def _work3(batch):
    if not O._STATE:
        O._init()
    s = O.S
    n = len(s["dates"])
    out = []
    for tup in batch:
        navs = O._run(tup, True)
        row = [_measure(navs, s["dates"], s["i2008"], n)]
        for cut in O.CUTS:
            row.append(_measure(navs, s["dates"], s["i2008"], cut))
            row.append(_measure(navs, s["dates"], cut, n))
        out.append(row)
    return out


def show(label, rows, budget=0.13):
    print()
    print("=== %s ===" % label)
    for fold, (is_lbl, oos_lbl) in enumerate(O.FOLDS):
        best = None
        for tup, row in rows:
            isc, ism = row[1 + fold * 2]
            if ism is None or ism < -budget - 1e-9:
                continue
            if best is None or isc > best[1]:
                best = (tup, isc, ism, row[2 + fold * 2])
        if not best:
            print("  no portfolio met the budget in %s" % is_lbl)
            continue
        tup, isc, ism, oos = best
        print("  %s: cagr=%6.2f%% mdd=%7.2f%%   ->  %s: cagr=%6.2f%% mdd=%7.2f%%"
              % (is_lbl, isc * 100, ism * 100, oos_lbl, oos[0] * 100, oos[1] * 100))
        print("      %s" % " ".join("%s %.0f%%" % (O.NAMES[k], tup[i] * 100)
                                    for i, k in enumerate(KEYS) if tup[i] > 0.005))


def main():
    O.setup()
    s = O.S
    print("panel %s -> %s" % (s["dates"][0], s["dates"][-1]))

    rng = random.Random(11)
    N = 9000
    allx = [O.sample(rng, cap=0.25) for _ in range(N)]
    for sid in ("A2", "Z0", "A5"):
        w = O.st.get(sid)["weights"]
        allx.append(tuple(float(w.get(k, 0.0)) for k in KEYS))

    chunk = 200
    batches = [allx[i:i + chunk] for i in range(0, len(allx), chunk)]
    t0 = datetime.datetime.now()
    res = []
    with Pool(processes=16, initializer=O._init) as pool:
        for out in pool.imap(_work3, batches):
            res.extend(out)
    print("evaluated %d portfolios in %.0fs"
          % (len(res), (datetime.datetime.now() - t0).total_seconds()))
    rows = list(zip(allx, res))

    for sid in ("A2", "Z0", "A5"):
        w = O.st.get(sid)["weights"]
        tup = tuple(float(w.get(k, 0.0)) for k in KEYS)
        for x, r in rows:
            if x == tup:
                print("  %s full (fees): cagr=%6.2f%% mdd=%7.2f%%"
                      % (sid, r[0][0] * 100, r[0][1] * 100))

    for budget in (0.13, 0.12):
        show("in-sample search, mdd budget %.0f%%" % (budget * 100), rows, budget)

    print()
    print("=== every portfolio, full 2008+ (fees) ===")
    ok = [r[0] for _, r in rows if r[0]]
    ok.sort(key=lambda x: -x[0])
    print("  top-5 by cagr:      %s"
          % "  ".join("%.2f%%/%.2f%%" % (c * 100, m * 100) for c, m in ok[:5]))
    print("  median combo:       %.2f%%/%.2f%%" % (ok[len(ok) // 2][0] * 100,
                                                    ok[len(ok) // 2][1] * 100))


if __name__ == "__main__":
    main()
