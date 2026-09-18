# -*- coding: utf-8 -*-
"""Frontier search v2: same engine, but
  (a) fees are charged per leg (not one global drag), and
  (b) a diversified variant with a 25% cap per leg.
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

import backtest
import bars
import premium as pm
import risk
import strategy as st

KEYS = ["cash", "short", "rate", "credit", "divA", "divHK", "broad", "gold", "ndx"]
NAMES = {"cash": "货币", "short": "短债", "rate": "利率债", "credit": "信用债",
         "divA": "A股红利低波", "divHK": "港股红利", "broad": "A股宽基",
         "gold": "黄金", "ndx": "纳指100"}
# what the real vehicle actually costs per year (管理费+托管+跟踪偏离的粗估)
FEE = {"cash": 0.002, "short": 0.002, "rate": 0.002, "credit": 0.004,
       "divA": 0.003, "divHK": 0.003, "broad": 0.002, "gold": 0.007, "ndx": 0.012}
PROXY = {
    "cash":  {"src": "csi", "code": "H11025"},
    "short": {"src": "csi", "code": "H11010"},
    "rate":  {"src": "csi", "code": "H11006"},
    "credit": {"src": "csi", "code": "H11008"},
    "divA":  {"src": "csi", "code": "H20269"},
    "divHK": {"src": "csi", "code": "H20914",
              "splice": {"src": "csi", "code": "H11141", "add_yield": 0.0709,
                         "before": "20141114"}},
    "broad": {"src": "csi", "code": "H00300"},
    "gold":  {"src": "em", "code": "118.Au9999"},
    "ndx":   {"src": "em_fx", "code": "100.NDX"},
}

S = {}
_STATE = {}

# split points used by the out-of-sample check (opt3.py)
CUTS = []
FOLDS = [("2008-2016", "2017-2026"), ("2008-2019", "2020-2026")]


def _years(d0, d):
    a = datetime.date(int(d0[:4]), int(d0[4:6]), int(d0[6:8]))
    b = datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    return (b - a).days / 365.25


def setup():
    if S:
        return S
    base = dict(st.buckets())

    def patched():
        b = dict(base)
        for k in KEYS:
            b[k] = dict(b.get(k) or {"name": NAMES[k], "group": "", "instruments": []},
                        proxy=PROXY[k])
        return b

    st.buckets = patched

    # raw panel
    backtest._PANEL_CACHE.clear()
    raw = backtest.panel()
    # fee-charged panel: scale each leg by (1-fee)^t
    series = backtest.bucket_series_map()
    for k, ser in series.items():
        f = FEE.get(k)
        if not f or not ser:
            continue
        d0 = min(ser)
        series[k] = {d: v * (1.0 - f) ** _years(d0, d) for d, v in ser.items()}
    dates = raw[0]
    cols = {}
    for k, ser in series.items():
        if not ser:
            continue
        vals, last = [], None
        for d in dates:
            v = ser.get(d)
            if v is not None:
                last = v
            vals.append(last)
        cols[k] = vals
    fee = (dates, cols)
    S["raw"], S["fee"] = raw, fee
    S["gate"] = st.gate_cfg()
    S["overlay"] = st.risk_cfg()
    S["air"] = risk.load_airman_series()
    ps, pb = {}, {}
    for key, cfgb in base.items():
        pc = cfgb.get("premium") or {}
        ins = [c for c in (cfgb.get("instruments") or []) if c]
        if not pc or not ins or not pm.is_on_exchange(ins[0]):
            continue
        s = pm.series(ins[0])
        if s:
            ps[key] = s
            pb[key] = float(pc.get("block") or 0.03)
    S["prem_s"], S["prem_b"] = ps, pb
    S["i2008"] = next(i for i, d in enumerate(dates) if d >= "20080101")
    S["dates"] = dates
    CUTS[:] = [next(i for i, d in enumerate(dates) if d >= c)
               for c in ("20170101", "20200101")]
    return S


def _init():
    _STATE.update(setup())


def _stt():
    return _STATE or setup()


def _run(tup, fee_mode):
    s = _stt()
    backtest._panel = (lambda f=fee_mode: s["fee"] if f else s["raw"])
    w = {k: v for k, v in zip(KEYS, tup) if v > 1e-9}
    return backtest.run_nav(
        w, gate=True, drag=0.0, months=st.rebalance_months(),
        gate_proxy=s["gate"].get("proxy"), window=s["gate"].get("window", 200),
        cut=s["gate"].get("cut", 0.5), risk_buckets=s["gate"].get("risk_buckets"),
        risk_overlay=s["overlay"], airman_series=s["air"],
        prem_series=s["prem_s"], prem_blocks=s["prem_b"])


def measure(navs):
    s = _stt()
    i0 = s["i2008"]
    seg, dates = navs[i0:], s["dates"][i0:]
    if len(seg) < 100:
        return -9.0, -9.0, "-"
    yrs = (datetime.date(int(dates[-1][:4]), int(dates[-1][4:6]), int(dates[-1][6:8]))
           - datetime.date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:8]))).days / 365.25
    cagr = (seg[-1] / seg[0]) ** (1 / yrs) - 1
    peak, mdd, at = seg[0], 0.0, dates[0]
    for i, v in enumerate(seg):
        peak = max(peak, v)
        if v / peak - 1 < mdd:
            mdd, at = v / peak - 1, dates[i]
    return cagr, mdd, at


def _work(batch):
    if not _STATE:
        _init()
    out = []
    for tup in batch:
        out.append((measure(_run(tup, False)), measure(_run(tup, True))))
    return out


def sample(rng, cap=None):
    while True:
        safe = rng.uniform(0.05, 0.92)
        sd = [rng.expovariate(0.8) for _ in range(4)]
        t = sum(sd)
        sw = [x / t * safe for x in sd]
        rd = [rng.expovariate(0.7) for _ in range(5)]
        t = sum(rd)
        rw = [x / t * (1 - safe) for x in rd]
        w = sw + rw
        if cap is None or max(w) <= cap + 1e-9:
            return tuple(w)


def frontier(rows, label, budgets=(0.08, 0.10, 0.11, 0.12, 0.13, 0.15)):
    print()
    print("=== %s ===" % label)
    out = {}
    for b in budgets:
        best = None
        for tup, (nf, fm) in rows:
            if fm[1] >= -b - 1e-9 and (best is None or fm[0] > best[2][0]):
                best = (tup, nf, fm)
        if best:
            out[b] = best
            print("mdd<=%-4s cagr=%6.2f%% (no-fee %6.2f%%) mdd=%7.2f%% (%s)  %s"
                  % ("%.0f%%" % (b * 100), best[2][0] * 100, best[1][0] * 100,
                     best[2][1] * 100, best[2][2],
                     " ".join("%s %.0f%%" % (NAMES[k], best[0][i] * 100)
                              for i, k in enumerate(KEYS) if best[0][i] > 0.005)))
    return out


def main():
    setup()
    s = S
    print("panel %s -> %s (%d days), since 2008 from %s"
          % (s["dates"][0], s["dates"][-1], len(s["dates"]), s["dates"][s["i2008"]]))

    rng = random.Random(7)
    N = 9000
    free = [sample(rng) for _ in range(N)]
    capped = [sample(rng, cap=0.25) for _ in range(N)]
    for sid in ("A2", "Z0", "A5"):
        w = st.get(sid)["weights"]
        t = tuple(float(w.get(k, 0.0)) for k in KEYS)
        free.append(t)
        capped.append(t)

    t0 = datetime.datetime.now()
    allx = free + capped
    chunk = 200
    batches = [allx[i:i + chunk] for i in range(0, len(allx), chunk)]
    res = []
    with Pool(processes=16, initializer=_init) as pool:
        for out in pool.imap(_work, batches):
            res.extend(out)
    print("evaluated %d portfolios in %.0fs"
          % (len(res), (datetime.datetime.now() - t0).total_seconds()))

    rows_free = list(zip(free, res[:len(free)]))
    rows_cap = list(zip(capped, res[len(free):]))
    f1 = frontier(rows_free, "unconstrained (fees charged per leg)")
    f2 = frontier(rows_cap, "each leg capped at 25%")

    out = {"keys": KEYS, "names": NAMES,
           "unconstrained": {("%.2f" % k): {"weights": list(v[0]), "cagr": v[2][0],
                                            "cagr_no_fee": v[1][0], "mdd": v[2][1]}
                             for k, v in f1.items()},
           "capped25": {("%.2f" % k): {"weights": list(v[0]), "cagr": v[2][0],
                                       "cagr_no_fee": v[1][0], "mdd": v[2][1]}
                        for k, v in f2.items()}}
    with open(os.path.join(HERE, "opt2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print()
    print("saved", os.path.join(HERE, "opt2.json"))


if __name__ == "__main__":
    main()
