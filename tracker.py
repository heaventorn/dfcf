# -*- coding: utf-8 -*-
"""盈亏追踪器：把起点记下来，之后按最新净值算累计收益和年化。

它跟回测是两件事：
  · 回测 = 「历史上如果这么做，长这样」；
  · 追踪 = 「我从某天开始真这么做，到现在长这样」。
半年、一年后要看的就是后者，顺便和同期回测对照，差在哪一腿也看得见。

口径：
  · 净值用东财的**复权净值**（含分红再投），和 strategy.json 里各腿代理
    的口径一致；取不到时退回腾讯日收盘价（那种情况下分红没算进来）。
  · 「现金」默认按 511360 短融计息（策略里现金桶的第一只）。你要是就放
    活期，把 track.json 里 cash.vehicle 改成 null 就行。
  · 还没实际买入时是「纸上建仓」；等真买了、并且账本 positions.json 有
    数据了，报告会同时给出实盘口径（以 positions.json 的份额和成本为准）。

用法：
    py tracker.py init            按当前计划建账，今天就是起点
    py tracker.py                 打报告（顺手记一条当日快照）
    py tracker.py report --json   给页面 / 别的脚本用
    py tracker.py init --force    推翻重来，旧的存成 track.bak.json
"""

from __future__ import print_function

import argparse
import datetime
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config
import strategy

FILE = os.path.join(config.OUTPUT_DIR, "track.json")
NAV_CACHE = os.path.join(config.OUTPUT_DIR, "track_nav.json")
CASH_VEHICLE = "511360"      # 现金桶打底的那只
BENCH_DRAG = 0.0045          # 对照回测用的费率档（和页面上 0.45% 那一档一致）
MILESTONES = (91, 183, 365, 730)   # 3 个月 / 半年 / 1 年 / 2 年

_NAV = {}


# ------------------------------------------------------------------ 小工具

def _today():
    return datetime.date.today().strftime("%Y%m%d")


def _date(s):
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _days(a, b):
    return (_date(b) - _date(a)).days


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _dump(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


# ------------------------------------------------------------------ 净值

def nav_series(code, force=False):
    """{YYYYMMDD: 复权净值}。东财 pingzhongdata 的 Data_ACWorthTrend。"""
    if code in _NAV and not force:
        return _NAV[code]
    cache = _load(NAV_CACHE, {})
    hit = cache.get(code) or {}
    if hit.get("day") == _today() and hit.get("data"):
        _NAV[code] = hit["data"]
        return _NAV[code]
    out = {}
    try:
        import requests
        r = requests.get(
            "https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": "https://fund.eastmoney.com/%s.html" % code},
            timeout=30)
        m = re.search(r"var Data_ACWorthTrend\s*=\s*(\[.*?\]);", r.text)
        if m:
            for x in json.loads(m.group(1)):
                try:
                    ms, v = (x.get("x"), x.get("y")) if isinstance(x, dict) \
                        else (x[0], x[1])
                    ms, v = int(ms), float(v)
                except (TypeError, ValueError, IndexError):
                    continue
                d = datetime.datetime.fromtimestamp(
                    ms / 1000.0 + 8 * 3600, datetime.timezone.utc)
                if v > 0:
                    out[d.strftime("%Y%m%d")] = v
    except Exception:
        out = {}
    if out:
        cache[code] = {"day": _today(), "data": out}
        _dump(NAV_CACHE, cache)
        _NAV[code] = out
        return out
    # 取不到复权净值就退回日收盘（这时分红没算进去，报告里会标出来）
    if hit.get("data"):
        _NAV[code] = hit["data"]
        return _NAV[code]
    try:
        import premium
        s = premium.px_series(code) or {}
        s = {k: float(v) for k, v in s.items() if v}
    except Exception:
        s = {}
    _NAV[code] = s
    return s


def nav_at(code, day):
    """那一天（或之前最近一天）的复权净值。"""
    s = nav_series(code)
    keys = [k for k in s if k <= day]
    if not keys:
        return None
    return s[max(keys)]


def nav_latest(code):
    s = nav_series(code)
    if not s:
        return None, None
    k = max(s)
    return s[k], k


# ------------------------------------------------------------------ 建账

def init(capital=None, sid=None, force=False):
    old = _load(FILE, None)
    if old and not force:
        return {"ok": False,
                "msg": "已经建过账了（起点 %s）。要重来加 --force，"
                       "旧的会存成 track.bak.json" % old.get("start")}
    sid = sid or strategy.active_id()
    cap = float(capital or strategy.account().get("capital") or 0)
    if cap <= 0:
        return {"ok": False, "msg": "先在 strategy.json 的 account.capital "
                                    "填可投资总额，或者加 --capital 1000000"}
    import rebalance
    plan = rebalance.plan(sid=sid)
    if not plan.get("ok"):
        return {"ok": False, "msg": "计划没算出来：%s" % plan.get("msg")}
    start = _today()
    legs, spent = [], 0.0
    for r in plan["rows"]:
        if r.get("action") != "买入" or not r.get("amount"):
            continue
        code = r["instrument"]
        shares = float(r["shares"])
        n0 = nav_at(code, start)
        if not n0:
            return {"ok": False, "msg": "%s 没有历史净值，建不了账" % code}
        amt = shares * float(r["price"])
        legs.append({"bucket": r.get("key"), "code": code, "name": r.get("name"),
                     "shares": shares, "nav0": n0, "amount": round(amt, 2)})
        spent += amt
    if not legs:
        return {"ok": False, "msg": "今天的计划一笔都不用买，没什么可建的"}
    veh_nav = nav_at(CASH_VEHICLE, start)
    cash = {"amount": round(cap - spent, 2), "vehicle": CASH_VEHICLE,
            "nav0": veh_nav,
            "note": "闲钱默认按 %s 计息；就放活期的话把 vehicle 改成 null"
                    % CASH_VEHICLE}
    t = {"_comment": "盈亏追踪的起点。改了 legs 的份额就等于手工记账。",
         "strategy": sid, "start": start, "capital": round(cap, 2),
         "basis": "paper",
         "note": "纸上建仓：还没实际买入，按计划该买的份额记账。真买了之后"
                 "把份额改成实际成交数（或者在策略台点确认执行写进 "
                 "positions.json），报告会自动改成实盘口径。",
         "legs": legs, "cash": cash, "snapshots": []}
    if old:
        _dump(os.path.join(config.OUTPUT_DIR, "track.bak.json"), old)
    _dump(FILE, t)
    rec = report(record=True)
    return {"ok": True, "file": FILE, "start": start, "legs": len(legs),
            "spent": round(spent, 2), "cash": cash["amount"], "report": rec}


# ------------------------------------------------------------------ 估值

def _leg_rows(t, day=None):
    rows, total = [], 0.0
    last_day = day or _today()
    for l in t["legs"]:
        code = l["code"]
        n = nav_at(code, last_day) if day else nav_latest(code)[0]
        if not n:
            n = l["nav0"]
        # 投入的钱 × 这一腿的总收益率。份额只用来显示和对账 —— 份额是按
        # 场内价买的，而复权净值的量纲和场内价不一样，直接相乘会差一个比例。
        r = (n / l["nav0"] - 1) if l["nav0"] else 0.0
        v = float(l.get("amount") or 0) * (1 + r)
        total += v
        rows.append({"bucket": l.get("bucket"), "code": code, "name": l.get("name"),
                     "shares": l["shares"], "nav0": l["nav0"], "nav": n,
                     "ret": r,
                     "value": v, "amount0": l.get("amount")})
    c = t.get("cash") or {}
    amt = float(c.get("amount") or 0)
    veh, n0 = c.get("vehicle"), c.get("nav0")
    if veh and n0:
        n = (nav_at(veh, last_day) if day else nav_latest(veh)[0]) or n0
        cv = amt * (1 + (n / n0 - 1))
    else:
        n, cv = None, amt
    total += cv
    rows.append({"bucket": "cash", "code": veh or "现金", "name": "货币/逆回购",
                 "shares": None, "nav0": n0, "nav": n,
                 "ret": (cv / amt - 1) if amt else 0.0,
                 "value": cv, "amount0": amt})
    return rows, total


def _actual(t):
    """实盘口径：positions.json 有货才算。成本按记录里的 cost，没花掉的是现金。"""
    pos = (strategy.load_positions() or {}).get("positions") or []
    pos = [p for p in pos if float(p.get("shares") or 0) > 0]
    if not pos:
        return None
    cap = float(t.get("capital") or 0)
    px = strategy.prices([p.get("code") for p in pos])
    rows, mv, cost = [], 0.0, 0.0
    for p in pos:
        code = str(p.get("code") or "")
        shares = float(p.get("shares") or 0)
        c = float(p.get("cost") or 0)
        n = px.get(code) or c          # 现价口径：和 cost、份额同一量纲
        mv += shares * n
        cost += shares * c
        rows.append({"code": code, "name": p.get("name") or code,
                     "shares": shares, "nav": n, "cost": c,
                     "ret": (n / c - 1) if c else 0.0, "value": shares * n})
    return {"value": mv + max(cap - cost, 0.0), "invested": mv,
            "cost": cost, "cash": max(cap - cost, 0.0), "rows": rows,
            "note": "实盘口径按现价×份额 + 没花掉的现金；分红现金没算进来"
                    "（所以会比含分红的口径略低）"}


def _bench(t):
    """同期回测：从建账那天起，同一个策略（扣 0.45%/年）。"""
    import backtest
    sid = t.get("strategy") or strategy.active_id()
    try:
        navs, dates, _meta = backtest.run(sid, drag=BENCH_DRAG)
    except Exception:
        return None
    if not navs:
        return None
    idx = [i for i, d in enumerate(dates) if d >= t["start"]]
    # 建账当天回测数据常常还停在最近一个交易日（比如周六建账），
    # 这时拿最后一个交易日当基准，只差一天，不影响看趋势。
    i0 = idx[0] if idx else max(len(dates) - 1, 0)
    return {"id": sid, "dates": dates[i0:], "navs": navs[i0:]}


def _bench_at(b, day):
    for d, v in zip(reversed(b["dates"]), reversed(b["navs"])):
        if d <= day:
            return v / b["navs"][0] - 1, d
    # 基准日还在后面（回测的最后一格常常是下一个交易日），就当还没开始跑。
    return 0.0, b["dates"][0]


# ------------------------------------------------------------------ 报告

def report(record=False, json_out=False):
    t = _load(FILE, None)
    if not t:
        return {"ok": False, "msg": "还没建账：先跑 py tracker.py init"}
    start, cap = t["start"], float(t["capital"])
    today = _today()
    days = _days(start, today)
    rows, paper = _leg_rows(t)
    act = _actual(t)
    b = _bench(t)
    cum = paper / cap - 1
    ann = (paper / cap) ** (365.0 / days) - 1 if days >= 30 else None
    b_cum, b_day = _bench_at(b, today) if b else (None, None)
    b_ann = None
    if b_cum is not None and b_day:
        bd = _days(b["dates"][0], b_day)
        b_ann = (1 + b_cum) ** (365.0 / bd) - 1 if bd >= 30 else None

    marks = []
    for m in MILESTONES:
        d = (_date(start) + datetime.timedelta(days=m)).strftime("%Y%m%d")
        if d > today:
            marks.append({"days": m, "date": d, "elapsed": False,
                          "left": _days(today, d)})
            continue
        _r, v = _leg_rows(t, day=d)
        bc = _bench_at(b, d)[0] if b else None
        marks.append({"days": m, "date": d, "elapsed": True,
                      "cum": v / cap - 1,
                      "ann": (v / cap) ** (365.0 / m) - 1,
                      "bench_cum": bc,
                      "bench_ann": ((1 + bc) ** (365.0 / m) - 1)
                      if bc is not None else None})

    out = {"ok": True, "file": FILE, "strategy": t.get("strategy"),
           "start": start, "today": today, "days": days, "capital": cap,
           "basis": "actual" if act else t.get("basis", "paper"),
           "value": paper, "cum": cum, "ann": ann,
           "bench_cum": b_cum, "bench_ann": b_ann, "bench_day": b_day,
           "legs": rows, "actual": act, "milestones": marks,
           "snapshots": (t.get("snapshots") or [])[-24:],
           "snapshot_count": len(t.get("snapshots") or []),
           "cash_note": (t.get("cash") or {}).get("note"),
           "value_note": t.get("note")}
    if act:
        out["paper_value"] = paper
        out["paper_cum"] = cum
        out["paper_ann"] = ann
        out["value"] = act["value"]
        out["cum"] = act["value"] / cap - 1
        out["ann"] = ((act["value"] / cap) ** (365.0 / days) - 1
                      if days >= 30 else None)
    if record:
        snap = {"date": today, "value": round(out["value"], 2),
                "cum": out["cum"], "ann": out["ann"], "days": days,
                "bench_cum": b_cum, "basis": out["basis"]}
        snaps = [s for s in (t.get("snapshots") or []) if s.get("date") != today]
        snaps.append(snap)
        t["snapshots"] = snaps[-400:]
        _dump(FILE, t)
        out["snapshot"] = snap
    if json_out:
        return out
    return out


def _pct(v, digits=2):
    return "-" if v is None else ("%+.*f%%" % (digits, v * 100))


def text_report(r):
    if not r.get("ok"):
        return r.get("msg") or "报告失败"
    L = []
    L.append("=" * 66)
    L.append("盈亏追踪 · %s · 起点 %s · 已过 %d 天"
             % (r["strategy"], r["start"], r["days"]))
    L.append("口径：%s%s" % ("实盘 positions.json" if r["basis"] == "actual"
                            else "纸上建仓（还没实际买入）",
                            "" if r["basis"] == "actual" else "，" + str(r["value_note"] or "")))
    L.append("-" * 66)
    L.append("%-12s %-6s %9s %9s %10s %9s"
             % ("桶", "代码", "份额", "成本", "现价/净值", "这一腿"))
    for x in r["legs"]:
        L.append("%-12s %-6s %9s %9s %10s %9s"
                 % (x.get("name") or "", x.get("code") or "",
                    ("%.0f" % x["shares"]) if x.get("shares") else "-",
                    "%.3f" % x["nav0"] if x.get("nav0") else "-",
                    "%.3f" % x["nav"] if x.get("nav") else "-",
                    _pct(x.get("ret"), 2)))
    L.append("-" * 66)
    L.append("总市值 %.0f 元（本金 %.0f）   累计 %s   年化 %s"
             % (r["value"], r["capital"], _pct(r["cum"]), _pct(r["ann"])))
    if r.get("bench_cum") is not None:
        L.append("同期回测（%s，扣 0.45%%/年，到 %s）：累计 %s  年化 %s"
                 % (r["strategy"], r["bench_day"], _pct(r["bench_cum"]),
                    _pct(r["bench_ann"])))
        L.append("差：累计 %s" % _pct(r["cum"] - r["bench_cum"]))
    if r.get("basis") == "actual" and r.get("paper_cum") is not None:
        L.append("（纸上口径对照：累计 %s 年化 %s）"
                 % (_pct(r["paper_cum"]), _pct(r["paper_ann"])))
    L.append("-" * 66)
    L.append("%-12s %-12s %9s %9s %9s" % ("里程碑", "到哪天", "累计", "年化", "回测年化"))
    for m in r["milestones"]:
        if not m.get("elapsed"):
            L.append("%-12s %-12s 还没到（还有 %d 天）"
                     % ("%d 天" % m["days"], m["date"], m["left"]))
            continue
        L.append("%-12s %-12s %9s %9s %9s"
                 % ("%d 天" % m["days"], m["date"], _pct(m["cum"]),
                    _pct(m["ann"]), _pct(m.get("bench_ann"))))
    L.append("=" * 66)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="盈亏追踪")
    ap.add_argument("cmd", nargs="?", default="report",
                    choices=["init", "report"])
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    a = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if a.cmd == "init":
        r = init(a.capital, a.strategy, a.force)
        if not r.get("ok"):
            print(r["msg"])
            return 1
        print("建账完成：起点 %s，%d 条腿，已投入 %.0f 元，现金 %.0f 元"
              % (r["start"], r["legs"], r["spent"], r["cash"]))
        print(r["report"] if a.json else text_report(r["report"]))
        return 0
    r = report(record=not a.no_record, json_out=a.json)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json
          else text_report(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
