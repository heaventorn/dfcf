# -*- coding: utf-8 -*-
"""Why does Z0's drawdown depend on how the bond bucket is proxied?"""

import datetime
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest
import bars
import strategy as st


def stat(ser, label, beg="20080101"):
    days = sorted(d for d in ser if d >= beg)
    if len(days) < 60:
        print("%-28s n=%d (too short)" % (label, len(days)))
        return
    a = datetime.date(int(days[0][:4]), int(days[0][4:6]), int(days[0][6:8]))
    b = datetime.date(int(days[-1][:4]), int(days[-1][4:6]), int(days[-1][6:8]))
    yrs = (b - a).days / 365.25
    cagr = (ser[days[-1]] / ser[days[0]]) ** (1 / yrs) - 1
    peak, mdd, at = ser[days[0]], 0.0, days[0]
    for d in days:
        peak = max(peak, ser[d])
        if ser[d] / peak - 1 < mdd:
            mdd, at = ser[d] / peak - 1, d
    print("%-28s %s->%s  cagr=%6.2f%%  mdd=%7.2f%% (%s)"
          % (label, days[0], days[-1], cagr * 100, mdd * 100, at))


def main():
    print("--- buckets declared in strategy.json")
    for k, v in st.buckets().items():
        print("   ", k, v.get("name"), v.get("instruments"), "proxy=", v.get("proxy"))
    m = backtest.bucket_series_map()
    print("--- bucket_series_map keys:", sorted(m))
    for k in sorted(m):
        ser = m[k]
        if ser:
            stat(ser, "bucket %s (%d pts)" % (k, len(ser)))
    for code in ("H11006", "H11001", "H11008", "H11010", "H11025", "H11015"):
        ser = bars.csi(code)
        if ser:
            stat(ser, "csi %s" % code)


if __name__ == "__main__":
    main()
