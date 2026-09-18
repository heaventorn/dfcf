# -*- coding: utf-8 -*-
"""Match real ETF cumulative-NAV series against candidate CSI bond indices,
so the long-history backtest can use a proxy with a defensible identity."""

import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import bars

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "etf_nav.json"), encoding="utf-8") as f:
    ETFS = json.load(f)

CAND = ["H11001", "H11006", "H11007", "H11008", "H11009", "H11010",
        "H11014", "H11015", "H11016", "H11017", "H11018", "H11019", "H11023"]

IDX = {}
for c in CAND:
    s = bars.csi(c)
    if s:
        IDX[c] = s


def clean(ser, cap=0.03):
    """Drop data artifacts: a bond/money fund never moves 3% in a day."""
    days = sorted(ser)
    out, bad = {}, []
    for i in range(1, len(days)):
        r = ser[days[i]] / ser[days[i - 1]] - 1
        if abs(r) > cap:
            bad.append((days[i], r))
            continue
        out[days[i]] = r
    return out, bad


print("=== 511880 artifact scan (largest daily moves) ===")
raw = ETFS["511880"]
days = sorted(raw)
moves = sorted(((abs(raw[days[i]] / raw[days[i - 1]] - 1), days[i],
                 raw[days[i - 1]], raw[days[i]]) for i in range(1, len(days))), reverse=True)[:6]
for m in moves:
    print("  %s  %+.2f%%  %.4f -> %.4f" % (m[1], m[0] * 100 * (1 if m[3] > m[2] else -1), m[2], m[3]))

print()
print("=== ETF vs CSI candidate tracking (daily returns, common days) ===")
for code in ["511360", "511520", "511010", "511260", "511220"]:
    if code not in ETFS:
        continue
    er, bad = clean(ETFS[code])
    edays = sorted(er)
    print("-- %s  %s -> %s  (%d d, dropped %d artifacts)" % (code, edays[0], edays[-1], len(edays), len(bad)))
    rows = []
    for c, s in IDX.items():
        ir = {}
        d = sorted(s)
        for i in range(1, len(d)):
            ir[d[i]] = s[d[i]] / s[d[i - 1]] - 1
        common = [x for x in edays if x in ir]
        if len(common) < 200:
            continue
        a = [er[x] for x in common]
        b = [ir[x] for x in common]
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(len(a)))
        va = sum((x - ma) ** 2 for x in a) ** 0.5
        vb = sum((x - mb) ** 2 for x in b) ** 0.5
        corr = cov / (va * vb) if va * vb else 0.0
        te = (sum((a[i] - b[i]) ** 2 for i in range(len(a))) / len(a)) ** 0.5 * 252 ** 0.5
        diff = (ma * 252) - (mb * 252)
        rows.append((corr, c, diff, te, len(common)))
    rows.sort(reverse=True)
    for corr, c, diff, te, n in rows[:4]:
        print("   corr=%.3f  CSI %-7s ann.diff=%+6.2f%%  tracking_err=%5.2f%%  n=%d"
              % (corr, c, diff * 100, te * 100, n))
