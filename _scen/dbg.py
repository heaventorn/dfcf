# -*- coding: utf-8 -*-
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest
import opt2 as O

O.setup()
s = O.S
for tag in ("raw", "fee"):
    dates, cols = s[tag]
    print(tag, "dates", len(dates), dates[0], dates[-1], "keys", sorted(cols))
    for k in sorted(cols):
        v = cols[k]
        print("    %-7s n=%d first=%s last=%s" % (k, len(v), v[0], v[-1]))

w = {"cash": 0.341, "rate": 0.284, "divA": 0.114, "divHK": 0.061,
     "broad": 0.05, "gold": 0.06, "ndx": 0.09}
print("weights sum", sum(w.values()))
navs = O._run(tuple(w.get(k, 0.0) for k in O.KEYS), False)
print("run_nav len", len(navs), "first", navs[0], "mid", navs[len(navs) // 2],
      "last", navs[-1])
print("backtest._panel is patched:", backtest._panel.__name__)
