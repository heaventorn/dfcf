# -*- coding: utf-8 -*-
"""Fetch cumulative-NAV history for the bond/money ETFs from fund.eastmoney.com
and cache it in _scen/etf_nav.json (scratch, not part of the product)."""

import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_nav.json")

CODES = ["511880", "511360", "511520", "511010", "511260", "511220", "159926", "511990",
         "511030", "511070", "511200", "511270", "511060",
         "512890", "513630", "518880", "510300"]


def fetch_ac(code):
    """累计净值序列 {YYYYMMDD: value}。"""
    r = requests.get("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://fund.eastmoney.com/%s.html" % code},
                     timeout=30)
    out = {}
    m = re.search(r"var Data_ACWorthTrend\s*=\s*(\[.*?\]);", r.text)
    if m:
        for x in json.loads(m.group(1)):
            try:
                ms, v = int(x[0]), float(x[1])
            except (TypeError, ValueError, IndexError):
                continue
            d = datetime.datetime.utcfromtimestamp(ms / 1000.0 + 8 * 3600)
            if v > 0:
                out[d.strftime("%Y%m%d")] = v
    return out


def yearly(ser):
    days = sorted(ser)
    out, first, prev_last = {}, None, None
    for d in days:
        y = d[:4]
        if y not in out:
            out[y] = [ser[d], ser[d]]
        out[y][1] = ser[d]
    res = {}
    prev = None
    for y in sorted(out):
        base = prev if prev is not None else out[y][0]
        if prev is not None:
            res[y] = out[y][1] / base - 1
        prev = out[y][1]
    return res


def stats(ser):
    days = sorted(ser)
    yrs = (datetime.date(int(days[-1][:4]), int(days[-1][4:6]), int(days[-1][6:8]))
           - datetime.date(int(days[0][:4]), int(days[0][4:6]), int(days[0][6:8]))).days / 365.25
    cagr = (ser[days[-1]] / ser[days[0]]) ** (1 / yrs) - 1
    rets = [ser[days[i]] / ser[days[i - 1]] - 1 for i in range(1, len(days))]
    mu = sum(rets) / len(rets)
    vol = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5
    peak, mdd, mdd_at = ser[days[0]], 0.0, days[0]
    for d in days:
        peak = max(peak, ser[d])
        dd = ser[d] / peak - 1
        if dd < mdd:
            mdd, mdd_at = dd, d
    return days, yrs, cagr, vol, mdd, mdd_at


blob = {}
for c in CODES:
    try:
        ser = fetch_ac(c)
    except Exception as e:
        print("%s ERR %s %s" % (c, type(e).__name__, e))
        continue
    if not ser:
        print("%s EMPTY" % c)
        continue
    blob[c] = ser
    days, yrs, cagr, vol, mdd, mdd_at = stats(ser)
    yr = yearly(ser)
    neg = [y for y in yr if yr[y] < 0]
    print("%s  %s -> %s  %.1fy  cagr=%5.2f%%  vol=%4.2f%%  mdd=%6.2f%%(%s)  neg_years=%s"
          % (c, days[0], days[-1], yrs, cagr * 100, vol * 100, mdd * 100, mdd_at, neg or "none"))
    print("      yearly: " + "  ".join("%s %+.2f%%" % (y, yr[y] * 100) for y in sorted(yr)))

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(blob, f, ensure_ascii=False)
print("saved", OUT, "codes", sorted(blob))
