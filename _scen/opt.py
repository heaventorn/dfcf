# -*- coding: utf-8 -*-
"""Portfolio search: maximise CAGR since 2008 subject to a max-drawdown budget.

Engine = the product's own backtest.run_nav (gate + risk overlay + premium
discipline), so any portfolio found here is directly executable through the
existing strategy board.
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
import premium as pm
import risk
import strategy as st

KEYS = ["cash", "short", "rate", "credit", "divA", "divHK", "broad", "gold", "ndx"]
NAMES = {"cash": "货币", "short": "短债", "rate": "利率债", "credit": "信用债",
         "divA": "A股红利低波", "divHK": "港股红利", "broad": "A股宽基",
         "gold": "黄金", "ndx": "纳指100"}
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


def setup():
    if S:
        return S
    base = dict(st.buckets())

    def patched():
        b = dict(base)
        for k in KEYS:
            if k in b:
                b[k] = dict(b[k], proxy=PROXY[k])
            else:
                b[k] = {"name": NAMES[k], "group": "无风险", "instruments": [],
                        "proxy": PROXY[k]}
        return b

    st.buckets = patched
    backtest._PANEL_CACHE.clear()
    dates, cols = backtest._panel()
    S["cols"] = cols
    S["dates"] = dates
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
    return S


def _init():
    _STATE.update(setup())


def _stt():
    return _STATE or setup()


def _run(tup, drag=0.0, use_gate=True, use_overlay=True):
    s = _stt()
    w = {k: v for k, v in zip(KEYS, tup) if v > 1e-9}
    return backtest.run_nav(
        w, gate=use_gate, drag=drag, months=st.rebalance_months(),
        gate_proxy=s["gate"].get("proxy"), window=s["gate"].get("window", 200),
        cut=s["gate"].get("cut", 0.5), risk_buckets=s["gate"].get("risk_buckets"),
        risk_overlay=(s["overlay"] if use_overlay else None),
        airman_series=s["air"], prem_series=s["prem_s"], prem_blocks=s["prem_b"])


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
        out.append(measure(_run(tup)))
    return out


def sample(rng):
    """Structured sample: safe share first, then split within safe and within risk."""
    safe = rng.uniform(0.10, 0.92)
    sd = [rng.expovariate(1.0) for _ in range(4)]
    t = sum(sd)
    sw = [x / t * safe for x in sd]
    rd = [rng.expovariate(1.0) for _ in range(5)]
    t = sum(rd)
    rw = [x / t * (1 - safe) for x in rd]
    return tuple(sw + rw)


def main():
    setup()
    print("panel %s -> %s (%d days); since-2008 slice starts %s"
          % (S["dates"][0], S["dates"][-1], len(S["dates"]), S["dates"][S["i2008"]]))

    rng = random.Random(20260919)
    N = 24000
    samples = [sample(rng) for _ in range(N)]
    for sid, label in [("A2", "A2"), ("Z0", "Z0"), ("A5", "A5")]:
        w = st.get(sid)["weights"]
        samples.append(tuple(float(w.get(k, 0.0)) for k in KEYS))

    t0 = datetime.datetime.now()
    chunk = 300
    batches = [samples[i:i + chunk] for i in range(0, len(samples), chunk)]
    res = []
    with Pool(processes=14, initializer=_init) as pool:
        for out in pool.imap(_work, batches):
            res.extend(out)
    print("random search: %d portfolios in %.0fs"
          % (len(res), (datetime.datetime.now() - t0).total_seconds()))

    rows = list(zip(samples, res))
    rows.sort(key=lambda x: -x[1][0])

    print()
    print("=== efficient frontier under the live engine (since 2008) ===")
    picked = {}
    for budget in (0.06, 0.08, 0.10, 0.11, 0.12, 0.13, 0.15, 0.20, 0.40):
        best = None
        for tup, (c, m, at) in rows:
            if m >= -budget - 1e-9 and (best is None or c > best[1][0]):
                best = (tup, (c, m, at))
        if best:
            picked[budget] = best
            print("mdd<=%-4s cagr=%6.2f%% mdd=%7.2f%% (%s)  %s"
                  % ("%.0f%%" % (budget * 100), best[1][0] * 100, best[1][1] * 100,
                     best[1][2],
                     " ".join("%s %.0f%%" % (NAMES[k], best[0][i] * 100)
                              for i, k in enumerate(KEYS) if best[0][i] > 0.005)))

    print()
    print("=== the three shipped strategies / same window / same engine ===")
    tail = samples[-3:]
    with Pool(processes=5, initializer=_init) as pool:
        got = pool.map(_work, [[t] for t in tail])
    for sid, out in zip(["A2", "Z0", "A5"], got):
        c, m, at = out[0]
        print("%-3s cagr=%6.2f%% mdd=%7.2f%% (%s)" % (sid, c * 100, m * 100, at))

    with open(os.path.join(HERE, "opt_best.json"), "w", encoding="utf-8") as f:
        json.dump({"keys": KEYS, "names": NAMES,
                   "frontier": {("%.2f" % k): {"weights": list(v[0]), "cagr": v[1][0],
                                               "mdd": v[1][1], "mdd_at": v[1][2]}
                                for k, v in picked.items()}},
                  f, ensure_ascii=False, indent=1)
    print()
    print("saved", os.path.join(HERE, "opt_best.json"))


if __name__ == "__main__":
    main()
