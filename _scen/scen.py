# -*- coding: utf-8 -*-
"""Scenario backtest: what happens if the cash bucket is replaced by
short-term note / rate-bond ETFs.

Reuses the product's own engine (backtest.run_nav + risk overlay + premium
discipline) so the numbers are directly comparable with the strategy board.
"""

import datetime
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))

import backtest
import premium as pm
import risk
import strategy as st

# ---------------------------------------------------------------- extra legs
LEGS = {
    "short": ("短融", "H11010", "中证0-1年综合债指数"),
    "rate": ("利率债", "H11006", "中证国债指数"),
}

_orig_buckets = st.buckets


def patched_buckets():
    b = dict(_orig_buckets())
    for k, (name, code, idx) in LEGS.items():
        b[k] = {"name": name, "group": "无风险", "instruments": [],
                "proxy": {"src": "csi", "code": code}}
    return b


st.buckets = patched_buckets
backtest._PANEL_CACHE.clear()

DATES, COLS = backtest._panel()
print("panel: %s -> %s  %d days" % (DATES[0], DATES[-1], len(DATES)))

GATE = st.gate_cfg()
OVERLAY = st.risk_cfg()
AIR = risk.load_airman_series()
PREM_S, PREM_B = {}, {}
for key, cfgb in _orig_buckets().items():
    pc = cfgb.get("premium") or {}
    ins = [c for c in (cfgb.get("instruments") or []) if c]
    if not pc or not ins or not pm.is_on_exchange(ins[0]):
        continue
    s = pm.series(ins[0])
    if s:
        PREM_S[key] = s
        PREM_B[key] = float(pc.get("block") or 0.03)

Z0 = dict(st.get("Z0")["weights"])

SCEN = [
    ("S0-now", "现状（现金腿=货基）", dict(Z0)),
    ("S1", "现金砍到20%，其余给短融", dict(Z0, cash=0.20, short=0.141)),
    ("S2", "现金砍到5%，其余给短融", dict(Z0, cash=0.05, short=0.291)),
    ("S3", "现金5%+短融15%+利率债14%", dict(Z0, cash=0.05, short=0.150, rate=0.141)),
    ("S4", "现金5%，其余给利率债", dict(Z0, cash=0.05, rate=0.291)),
    ("S5", "现金5%+短融29%+债券腿换成利率债", dict(Z0, cash=0.05, short=0.291, bond=0.0, rate=0.284)),
]


def run(w, drag=0.0):
    navs = backtest.run_nav(w, gate=True, drag=drag, months=st.rebalance_months(),
                            gate_proxy=GATE.get("proxy"), window=GATE.get("window", 200),
                            cut=GATE.get("cut", 0.5), risk_buckets=GATE.get("risk_buckets"),
                            risk_overlay=OVERLAY, airman_series=AIR,
                            prem_series=PREM_S, prem_blocks=PREM_B)
    return navs, backtest.metrics(navs, DATES)


print()
print("=== portfolio backtest (2006-01 start, same gate/overlay/premium rules) ===")
print("%-5s %-32s %8s %8s %9s %7s %9s %9s %7s"
      % ("id", "scenario", "cagr", "net.45", "mdd", "vol", "worstYr", "worst3Y", "back"))
res = {}
for sid, name, w in SCEN:
    navs0, m0 = run(w, 0.0)
    _, m1 = run(w, 0.0045)
    res[sid] = (m0, m1, navs0)
    print("%-5s %-32s %7.2f%% %7.2f%% %8.2f%% %6.2f%% %8.2f%% %8.2f%% %6.1fy"
          % (sid, name, m0["cagr"] * 100, m1["cagr"] * 100, m0["mdd"] * 100,
             m0["vol"] * 100, m0["worst_year"] * 100, (m0["worst_3y"] or 0) * 100,
             m0["under_years"]))

print()
print("=== crisis windows (no fee, window return / window maxdd) ===")
for nm, lo, hi in backtest.CRISES:
    line = "%-18s" % nm
    for sid, _n, _w in SCEN:
        ws = backtest.window_stats(res[sid][2], DATES, lo, hi)
        line += (" %s:%+6.2f%%/%6.2f%%" % (sid, ws["ret"] * 100, ws["mdd"] * 100)) if ws else (" %s: n/a" % sid)
    print(line)

print()
print("=== yearly returns (no fee) ===")
years = sorted(res["S0-now"][0]["yearly"])
print("%-6s %s" % ("year", " ".join("%9s" % sid for sid, _n, _w in SCEN)))
for y in years:
    cells = []
    for sid, _n, _w in SCEN:
        v = res[sid][0]["yearly"].get(y)
        cells.append("%8.2f%%" % (v * 100) if v is not None else "      n/a")
    print("%-6s %s" % (y, " ".join(cells)))

# ---------------------------------------------------------------- leg stats
def sub_window(navs, dates, days_back):
    """Stats over the final `days_back` calendar days."""
    end = datetime.date(int(dates[-1][:4]), int(dates[-1][4:6]), int(dates[-1][6:8]))
    lo = (end - datetime.timedelta(days=days_back)).strftime("%Y%m%d")
    idx = [i for i, d in enumerate(dates) if d >= lo]
    seg = [navs[i] for i in idx]
    if len(seg) < 30:
        return None
    yrs = (end - datetime.date(int(dates[idx[0]][:4]), int(dates[idx[0]][4:6]),
                               int(dates[idx[0]][6:8]))).days / 365.25
    cagr = (seg[-1] / seg[0]) ** (1 / yrs) - 1
    peak, mdd = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return cagr, mdd


print()
print("=== recent regimes: last 5y / 3y / 1y (no fee) ===")
print("%-5s %22s %22s %22s" % ("id", "5y cagr/mdd", "3y cagr/mdd", "1y return/mdd"))
for sid, _n, _w in SCEN:
    navs, dates = res[sid][2], DATES
    row = ""
    for dib in (1826, 1095, 365):
        r = sub_window(navs, dates, dib)
        row += (" %9.2f%%/%8.2f%%" % (r[0] * 100, r[1] * 100)) if r else " %22s" % "n/a"
    print("%-5s%s" % (sid, row))

print()
print("=== today's effective target weights (gate on + live overlay) ===")
GW = st.gate_state()
RST = st.risk_state(GW)
print("gate.on=%s  overlay.mult=%.2f  %s" % (GW.get("on"), RST.get("multiplier"), RST.get("summary")))
for sid, name, w in SCEN:
    weff = backtest.effective_weights(w, True, GATE.get("cut", 0.5), GATE.get("risk_buckets"))
    weff = risk.apply_weights(weff, RST, cfg=OVERLAY)
    tot = sum(weff.values()) or 1.0
    parts = "  ".join("%s %.1f%%" % (k, weff[k] / tot * 100)
                      for k in ["cash", "short", "rate", "bond", "gold", "divA", "divHK", "broad", "ndx"]
                      if weff.get(k))
    print("%-5s %s" % (sid, parts))

print()
print("=== S6: do not touch any weight, only swap the cash leg from money fund to short bond ===")
_cash_proxy = _orig_buckets()["cash"].get("proxy")


def patched_buckets_cash(proxy):
    b = patched_buckets()
    b["cash"] = dict(b["cash"], proxy=proxy)
    return b


for pcode, pname in [("H11010", "中证0-1年综合债指数"), ("H11015", "中证短债指数")]:
    st.buckets = (lambda p=pcode: patched_buckets_cash({"src": "csi", "code": p}))
    backtest._PANEL_CACHE.clear()
    dts, _cols = backtest._panel()
    navs = backtest.run_nav(Z0, gate=True, drag=0.0, months=st.rebalance_months(),
                            gate_proxy=GATE.get("proxy"), window=GATE.get("window", 200),
                            cut=GATE.get("cut", 0.5), risk_buckets=GATE.get("risk_buckets"),
                            risk_overlay=OVERLAY, airman_series=AIR,
                            prem_series=PREM_S, prem_blocks=PREM_B)
    m = backtest.metrics(navs, dts)
    print("Z0 weights, cash leg = %s(%s): cagr=%.2f%% (+%.2f%%) mdd=%.2f%% worstYr=%.2f%% worst3Y=%.2f%%"
          % (pname, pcode, m["cagr"] * 100, (m["cagr"] - res["S0-now"][0]["cagr"]) * 100,
             m["mdd"] * 100, m["worst_year"] * 100, (m["worst_3y"] or 0) * 100))

st.buckets = patched_buckets
backtest._PANEL_CACHE.clear()


def rebuild(short_code, short_name):
    LEGS["short"] = ("短融", short_code, short_name)
    backtest._PANEL_CACHE.clear()
    return backtest._panel()


for scode, sname in [("H11015", "中证短债指数"), ("H11014", "中证短融指数")]:
    dts, cols = rebuild(scode, sname)
    for sid, w in [("S2", dict(Z0, cash=0.05, short=0.291)),
                   ("S3", dict(Z0, cash=0.05, short=0.150, rate=0.141))]:
        navs = backtest.run_nav(w, gate=True, drag=0.0, months=st.rebalance_months(),
                                gate_proxy=GATE.get("proxy"), window=GATE.get("window", 200),
                                cut=GATE.get("cut", 0.5), risk_buckets=GATE.get("risk_buckets"),
                                risk_overlay=OVERLAY, airman_series=AIR,
                                prem_series=PREM_S, prem_blocks=PREM_B)
        m = backtest.metrics(navs, dts)
        print("%-5s with %s(%s) proxy: cagr=%.2f%% mdd=%.2f%% worstYr=%.2f%% worst3Y=%.2f%% (%s->%s)"
              % (sid, sname, scode, m["cagr"] * 100, m["mdd"] * 100,
                 m["worst_year"] * 100, (m["worst_3y"] or 0) * 100, m["start"], m["end"]))
rebuild("H11010", "中证0-1年综合债指数")

print()
print("=== the safe legs themselves ===")


def leg_stats(ser, label):
    """ser is either {date: value} or (dates, values)."""
    if isinstance(ser, tuple):
        d = [x for x, v in zip(ser[0], ser[1]) if v is not None]
        ser = {x: v for x, v in zip(ser[0], ser[1]) if v is not None}
    else:
        d = sorted(ser)
    yrs = (datetime.date(int(d[-1][:4]), int(d[-1][4:6]), int(d[-1][6:8]))
           - datetime.date(int(d[0][:4]), int(d[0][4:6]), int(d[0][6:8]))).days / 365.25
    cagr = (ser[d[-1]] / ser[d[0]]) ** (1 / yrs) - 1
    rets = [ser[d[i]] / ser[d[i - 1]] - 1 for i in range(1, len(d))]
    mu = sum(rets) / len(rets)
    vol = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5
    peak, mdd, mdd_at = ser[d[0]], 0.0, d[0]
    for x in d:
        peak = max(peak, ser[x])
        if ser[x] / peak - 1 < mdd:
            mdd, mdd_at = ser[x] / peak - 1, x
    loss = tot = 0
    w1 = w3 = None
    for i in range(250, len(d)):
        tot += 1
        r1 = ser[d[i]] / ser[d[i - 250]] - 1
        w1 = r1 if w1 is None else min(w1, r1)
        if r1 < 0:
            loss += 1
    for i in range(750, len(d)):
        r3 = (ser[d[i]] / ser[d[i - 750]]) ** (1 / 3) - 1
        w3 = r3 if w3 is None else min(w3, r3)
    yb = {}
    for x in d:
        yb.setdefault(x[:4], [ser[x], ser[x]])[1] = ser[x]
    yrs_sorted = sorted(yb)
    wy, wy_year = None, "-"
    prev = None
    for y in yrs_sorted:
        base = prev if prev is not None else yb[y][0]
        if prev is not None:
            r = yb[y][1] / base - 1
            if wy is None or r < wy:
                wy, wy_year = r, y
        prev = yb[y][1]
    print("%-30s %5.1fy cagr=%5.2f%% vol=%4.2f%% mdd=%6.2f%%(%s) worstYr=%6.2f%%(%s) worst1y=%6.2f%% worst3y=%5.2f%% neg1y=%4.1f%%"
          % (label, yrs, cagr * 100, vol * 100, mdd * 100, mdd_at,
             (wy or 0) * 100, wy_year, (w1 or 0) * 100, (w3 or 0) * 100,
             (loss / tot * 100) if tot else 0))


for key, (name, code, idxname) in LEGS.items():
    leg_stats((DATES, COLS[key]), "%s proxy %s(%s)" % (name, idxname, code))
leg_stats((DATES, COLS["cash"]), "money proxy H11025")
leg_stats((DATES, COLS["bond"]), "total-bond proxy H11001")
import bars as _bars
leg_stats(_bars.csi("H11015"), "short-bond proxy H11015(中证短债)")
leg_stats(_bars.csi("H11014"), "short-note proxy H11014(中证短融)")
leg_stats(_bars.csi("H11007"), "financial-bond proxy H11007(中证金融债)")

with open(os.path.join(HERE, "etf_nav.json"), encoding="utf-8") as f:
    ETFS = json.load(f)
for code, label in [("511880", "511880 money ETF"), ("511360", "511360 short-note ETF"),
                    ("511520", "511520 policy-bank 7-10y"), ("511260", "511260 10y treasury"),
                    ("511220", "511220 urban-invest bond"), ("511010", "511010 5y treasury")]:
    s = ETFS.get(code)
    if not s:
        continue
    if code == "511880":
        s = {k: v for k, v in s.items() if k >= "20130403"}
    leg_stats(s, label)

print()
print("=== trailing 12m / 36m on the real ETFs ===")
for code, label in [("511880", "511880 money ETF"), ("511360", "511360 short-note ETF"),
                    ("511520", "511520 policy-bank 7-10y"), ("511260", "511260 10y treasury"),
                    ("511220", "511220 urban-invest bond")]:
    s = ETFS.get(code)
    if not s:
        continue
    d = sorted(s)
    cur = s[d[-1]]
    def back(n):
        tgt = (datetime.date(int(d[-1][:4]), int(d[-1][4:6]), int(d[-1][6:8]))
               - datetime.timedelta(days=n)).strftime("%Y%m%d")
        cand = [x for x in d if x <= tgt]
        return s[cand[-1]] if cand else None
    v12, v36 = back(365), back(1095)
    print("%-30s 12m=%+6.2f%%  36m=%+6.2f%% (ann %+5.2f%%)"
          % (label, (cur / v12 - 1) * 100 if v12 else float("nan"),
             (cur / v36 - 1) * 100 if v36 else float("nan"),
             ((cur / v36) ** (1 / 3) - 1) * 100 if v36 else float("nan")))
