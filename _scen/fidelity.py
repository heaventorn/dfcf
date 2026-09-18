# -*- coding: utf-8 -*-
"""Index proxy vs the ETF you can actually buy, on the overlapping window."""

import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))

import bars

NAV = json.load(open(os.path.join(HERE, "etf_nav.json"), encoding="utf-8"))

PAIRS = [("credit", "511030", "H11008"), ("credit", "511220", "H11008"),
         ("credit", "511030", "H11015"),
         ("rate", "511520", "H11006"), ("rate", "511260", "H11006"),
         ("rate", "511270", "H11006"), ("rate", "511010", "H11006"),
         ("cash", "511360", "H11010"), ("cash", "511880", "H11025"),
         ("short", "511360", "H11025")]


def cagr(ser, d0, d1):
    days = sorted(d for d in ser if d0 <= d <= d1)
    if len(days) < 60:
        return None, None, None
    a = datetime.date(int(days[0][:4]), int(days[0][4:6]), int(days[0][6:8]))
    b = datetime.date(int(days[-1][:4]), int(days[-1][4:6]), int(days[-1][6:8]))
    yrs = (b - a).days / 365.25
    return (ser[days[-1]] / ser[days[0]]) ** (1 / yrs) - 1, days[0], days[-1]


def main():
    for leg, etf, code in PAIRS:
        ser = NAV.get(etf)
        if not ser:
            continue
        idx = bars.csi(code)
        if not idx:
            print("%-7s %s vs %s : index unavailable" % (leg, etf, code))
            continue
        d0, d1 = min(ser), max(ser)
        c_etf, a, b = cagr(ser, d0, d1)
        c_idx, _, _ = cagr(idx, a, b)
        if c_idx is None:
            print("%-7s %s vs %s : no overlap" % (leg, etf, code))
            continue
        print("%-7s %s vs %-7s  %s->%s  ETF %5.2f%%  index %5.2f%%  gap %+5.2f pp"
              % (leg, etf, code, a, b, c_etf * 100, c_idx * 100,
                 (c_etf - c_idx) * 100))


if __name__ == "__main__":
    main()
