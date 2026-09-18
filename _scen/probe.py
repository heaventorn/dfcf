# -*- coding: utf-8 -*-
"""Scratch probe: what long-history proxies exist for short-duration bonds,
and what real ETF price history can EM give us."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bars


def stats(s):
    d = sorted(s)
    v0, v1 = s[d[0]], s[d[-1]]
    yrs = (int(d[-1][:4]) - int(d[0][:4])) + (int(d[-1][4:6]) - int(d[0][4:6])) / 12.0
    cagr = (v1 / v0) ** (1 / max(yrs, 0.1)) - 1
    rets = [s[d[i]] / s[d[i - 1]] - 1 for i in range(1, len(d))]
    mu = sum(rets) / len(rets)
    vol = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5
    return d, cagr, vol


etfs = ["511880", "511360", "511520", "511010", "511260", "511220", "511180", "159972", "159926"]
for c in etfs:
    s = bars.em("1." + c, fqt=1)
    if s:
        d, cagr, vol = stats(s)
        print("ETF %s rows=%5d %s -> %s cagr=%6.2f%% vol=%5.2f%% first=%.4f last=%.4f"
              % (c, len(d), d[0], d[-1], cagr * 100, vol * 100, s[d[0]], s[d[-1]]))
    else:
        print("ETF %s EMPTY" % c)

print("-" * 70)
codes = ["H11001", "H11006", "H11007", "H11008", "H11009", "H11010", "H11011", "H11012",
         "H11013", "H11014", "H11015", "H11016", "H11017", "H11018", "H11019", "H11020",
         "H11021", "H11022", "H11023", "H11024", "H11025", "H11026", "H11027", "H11028",
         "H11029", "H11030", "H11031", "H11032", "H11033", "H11034"]
for c in codes:
    s = bars.csi(c)
    if not s:
        continue
    d, cagr, vol = stats(s)
    print("CSI %-8s rows=%5d %s -> %s cagr=%6.2f%% vol=%5.2f%%"
          % (c, len(d), d[0], d[-1], cagr * 100, vol * 100))
