# -*- coding: utf-8 -*-
"""策略体检：日快照 / 周风险简检 / 月全面体检 / 季大考 + 三条报警线。

另有单独的**持仓评估**（holdings 档，monthly 会自动带上一次）：那一层不看
组合，只看手里每只产品本身 —— 跟踪误差、成交额、溢价。见 holding_eval.py。

为什么要分四档（一句话：不同的问题有不同的观察周期）
  · 每天只记不判。净值、累计、当时的闸门与风险乘数。快照漏了就补不回来，
    所以这条必须自动化，但它**不下结论** —— 一天的涨跌全是噪声。
  · 每周只盯风险。趋势闸、空中飞人、回撤档位、实际波动。仍然不评价收益。
  · 每月做体检。目标 vs 实际（漂移）、实际 vs 同期回测（在不在 ±1σ 带宽里）、
    对手基准（什么都不做能拿多少）、分腿贡献。
  · 每季度大考。策略还成不成立：滚动窗口、归因、要不要改。**「不改」也要
    明确写下来**，因为默认答案就是不改，除非数据说话。

三条报警线
  A1 收益带外：连续两个月，实际累计落在同期回测的 ±1σ 之外
  A2 该动没动：有桶漂移超过 band，且已经进了再平衡月（1/7 月）却还没执行计划
  A3 该减没减：闸门 / 分层减仓处于「减仓」状态，但实际风险腿占比高于目标

口径
  · 实际收益优先用账本 positions.json（真买了）；没有就退到 tracker 的纸上建仓
  · 期望收益与 σ 都来自 backtest（同策略、同期、含 0.45%/年费用）
  · 结论落 output/monitor.json；报警单独留一份历史，便于事后回看

用法
    py monitor.py                 按今天该做哪一档跑一档
    py monitor.py --level weekly  指定档位
                                  （daily/weekly/monthly/quarterly/holdings/all）
    py monitor.py --json          给页面 / 别的脚本用
    py monitor.py --no-record     只算不落盘
"""

from __future__ import print_function

import argparse
import datetime
import json
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import backtest
import config
import rebalance
import strategy

FILE = os.path.join(config.OUTPUT_DIR, "monitor.json")

DRAG = 0.0045          # 期望口径的费率档，和策略台上 0.45%/年 那一档一致
A1_LEVELS = 2          # 收益带外连续几个月才算报警
A3_TOL = 0.02          # 风险腿实际占比高出目标多少，算「该减没减」
# 被闸门 / 分层减仓管着的桶。**不要在代码里写死**：黄金 2026-09 起也进来了，
# 写死的话 A3 会漏掉它，报出来的“风险腿”跟目标权重对不上。
RISK_KEYS_FALLBACK = ("divA", "divHK", "broad", "ndx")
LEVELS = ("daily", "weekly", "monthly", "quarterly", "holdings")


def _today(day=None):
    return (day or datetime.date.today()).strftime("%Y%m%d")


def _ym(day=None):
    return (day or datetime.date.today()).strftime("%Y-%m")


# ------------------------------------------------------------------ 落盘

def _load():
    try:
        with open(FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if not isinstance(st, dict):
        st = {}
    st.setdefault("version", 1)
    st.setdefault("days", [])
    st.setdefault("checks", [])
    st.setdefault("alerts", [])
    return st


def _save(st):
    st["asof"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FILE)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 取数

def signals():
    """当前闸门 + 分层减仓状态。取不到就给空 dict，不让体检整个挂掉。"""
    gw = {}
    try:
        gw = strategy.gate_state() or {}
    except Exception as e:
        gw = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    rs = {}
    try:
        rs = strategy.risk_state(gw) or {}
    except Exception as e:
        rs = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    return gw, rs


def drift_rows(sid=None):
    """目标 vs 实际。strategy.monitor() 的 buckets，这里只要行。"""
    try:
        return (strategy.monitor(sid) or {}).get("buckets") or []
    except Exception:
        return []


def _journal_signals(gw, rs, sid=None):
    """闸门 / 风险乘数**变了才**往事件日志里记一条。

    这两样每天都在算，但一年也就翻几次。全记下来等于没记，所以用
    journal.log_change()：值一样直接返回，值变了才落一行，并且带上
    「从什么变成什么」，事后回看得懂。
    """
    try:
        import journal
        if gw.get("ok"):
            on = bool(gw.get("on"))
            journal.log_change(
                "gate", {"on": on, "index": gw.get("index")},
                "闸门", "趋势闸%s" % ("触发 · 风险腿打折" if on else "回到未触发"),
                detail="%s 收 %.2f / %d 日均线 %.2f，差 %+.2f%%；触发时风险腿保留 %d%%"
                       % (gw.get("index"), float(gw.get("close") or 0),
                          gw.get("window") or 200, float(gw.get("ma") or 0),
                          (gw.get("gap") or 0) * 100,
                          round((1 - float(gw.get("cut") or 0.5)) * 100)),
                level="warn" if on else "info", sid=sid)
        m = round(float(rs.get("multiplier") or 1.0), 3)
        am = rs.get("airman") or {}
        dd = (rs.get("drawdown") or {}).get("dd")
        journal.log_change(
            "risk_mult", m, "风险",
            "风险乘数 ×%.2f（%s）" % (m, rs.get("summary") or "-"),
            detail="空中飞人 %s%s · 近端回撤 %s"
                   % (am.get("level") or "-",
                      ("（%.1f 分）" % float(am["score"]))
                      if am.get("score") is not None else "",
                      ("%+.2f%%" % (dd * 100)) if dd is not None else "-"),
            level="warn" if m < 0.999 else "info", sid=sid)
    except Exception:
        pass


def held_count():
    """账本里有几笔真持仓。0 表示还没建仓。"""
    try:
        pos = (strategy.load_positions() or {}).get("positions") or []
        return len([p for p in pos if float(p.get("shares") or 0) > 0])
    except Exception:
        return 0


def risk_keys():
    """当前算作风险资产的桶，读 strategy.json 的 gate.risk_buckets。"""
    try:
        ks = strategy.gate_cfg().get("risk_buckets")
        if ks:
            return list(ks)
    except Exception:
        pass
    return list(RISK_KEYS_FALLBACK)


def _expected(sid, days):
    """同期回测口径：波动率 + 换算到这个持有期的 1σ 带宽。"""
    try:
        r = backtest.result(sid, drag=DRAG, with_curve=False)
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    vol = r.get("vol")
    band = None
    if vol and days:
        band = float(vol) * ((max(int(days), 1) / 365.0) ** 0.5)
    return {"ok": True, "vol": vol, "band": band, "drag_bp": int(DRAG * 10000),
            "cagr": r.get("cagr"), "mdd": r.get("mdd"),
            "worst_3y": r.get("worst_3y"), "worst_year": r.get("worst_year"),
            "sharpe": r.get("sharpe"), "under_years": r.get("under_years"),
            "years": r.get("years"), "crisis": r.get("crisis") or {}}


def _cash_bench(start, today=None):
    """什么都不做能拿多少：现金桶的第一只代理（货基 / 短融）。"""
    try:
        import bars
        spec = (strategy.buckets().get("cash") or {}).get("proxy")
        ser = bars.proxy_series(spec) or {}
        days = sorted(ser)
        a = next((d for d in days if d >= start), None)
        b = next((d for d in reversed(days) if d <= _today(today)), None)
        if a and b and ser[a]:
            return ser[b] / ser[a] - 1
    except Exception:
        pass
    return None


def _realized_vol(st, window=60):
    """近 window 条日快照推出来的年化波动。样本不够给 None。"""
    rows = [d for d in st.get("days") or [] if d.get("cum") is not None]
    if len(rows) < 20:
        return None, 0
    rows = rows[-(window + 1):]
    rets = []
    for i in range(1, len(rows)):
        a = 1.0 + float(rows[i - 1]["cum"])
        b = 1.0 + float(rows[i]["cum"])
        if a > 0:
            rets.append(b / a - 1.0)
    if len(rets) < 10:
        return None, len(rets)
    n = len(rets)
    mu = sum(rets) / n
    var = sum((r - mu) ** 2 for r in rets) / (n - 1)
    return (var ** 0.5) * (252 ** 0.5), n


# ------------------------------------------------------------------ 日

def daily(record=True, sid=None, force=False):
    """每天一条：只记不判。同一天重复调用只留最后一条。

    两个闸：
      · 还没建账（tracker 没 init）不写 —— 写进去也全是空的，只是垃圾行
      · 非交易日不写 —— 行情日期不等于今天，说明今天没有新净值，
        记下来只会让「日快照」里混进周末和节假日
    想看不想写、或者要补记，用 record=False / force=True。
    """
    import tracker
    sid = sid or strategy.active_id()
    st = _load()
    gw, rs = signals()
    try:
        rep = tracker.report(record=record, json_out=True)
    except Exception as e:
        rep = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    today = _today()
    prev = next((d for d in reversed(st["days"])
                 if d.get("date") != today and d.get("value") is not None), None)
    pnl_day = None
    if prev and rep.get("value") is not None:
        pnl_day = float(rep.get("value")) - float(prev["value"])
    flow_day = 0.0
    try:
        import ledger
        for e in ledger.entries(newest_first=False):
            if e.get("date") == today and e.get("kind") in ("入金", "出金"):
                flow_day += (float(e.get("amount") or 0)
                             if e["kind"] == "入金" else -float(e.get("amount") or 0))
    except Exception:
        pass
    row = {
        "date": today, "sid": sid, "ok": bool(rep.get("ok")),
        "basis": rep.get("basis"), "held_days": rep.get("days"),
        "value": rep.get("value"), "cum": rep.get("cum"),
        "bench_cum": rep.get("bench_cum"),
        "prev_value": prev.get("value") if prev else None,
        "pnl_day": pnl_day, "flow_day": flow_day,
        "mult": rs.get("multiplier"), "gate_on": gw.get("on"),
        "trend_gap": gw.get("gap"), "dd": gw.get("dd"),
        "airman": (rs.get("airman") or {}).get("score"),
        "summary": rs.get("summary"), "msg": rep.get("msg"),
    }
    market_day = gw.get("date") == today
    _journal_signals(gw, rs, sid)
    skip = None
    if not rep.get("ok"):
        skip = rep.get("msg") or "追踪没建账"
    elif not market_day and not force:
        skip = "今天不是交易日（行情日期 %s），不记" % (gw.get("date") or "-")

    if record and not skip:
        st["days"] = [d for d in st["days"] if d.get("date") != today]
        st["days"].append(row)
        st["days"] = st["days"][-900:]
        st["active"] = sid
        _save(st)
    return {"ok": True, "level": "daily", "row": row, "skipped": skip,
            "snapshot_count": len(st["days"]), "track": rep}


# ------------------------------------------------------------------ 报警线

def alerts(st=None, drift=None, rs=None):
    """三条报警线。每条都给 firing 和理由，没触发的也返回，便于页面展示。"""
    st = st if st is not None else _load()
    if drift is None:
        drift = drift_rows()
    if rs is None:
        _, rs = signals()
    n_held = held_count()
    out = []

    # A1 收益带外：连续两个月落在同期回测 ±1σ 之外
    rows = [c for c in st.get("checks") or []
            if c.get("level") == "monthly" and c.get("diff") is not None
            and c.get("band")]
    if len(rows) < A1_LEVELS:
        out.append({"code": "A1", "title": "收益带外", "firing": False,
                    "state": "样本不足",
                    "detail": "月体检记录 %d 条，连续 %d 个月才判"
                              % (len(rows), A1_LEVELS)})
    else:
        tail = rows[-A1_LEVELS:]
        bad = [r for r in tail if abs(r["diff"]) > r["band"]]
        out.append({
            "code": "A1", "title": "收益带外",
            "firing": len(bad) == A1_LEVELS,
            "state": "报警" if len(bad) == A1_LEVELS else "正常",
            "detail": "最近 %d 个月偏离 %s；门槛 ±1σ"
                      % (A1_LEVELS, " / ".join(
                          "%+.2f%%(±%.2f%%)" % (r["diff"] * 100, r["band"] * 100)
                          for r in tail)),
        })

    # A2 该动没动：漂移超 band + 进了再平衡月 + 本月还没执行过计划
    over = [r for r in drift if r.get("over_band")] if n_held else []
    is_rb = bool(rebalance.is_rebalance_month())
    done = False
    try:
        for h in rebalance.history(30):
            if str(h.get("at") or "")[:7] == _ym():
                done = True
                break
    except Exception:
        pass
    nxt = None
    try:
        nxt = rebalance.next_rebalance()[0]
    except Exception:
        pass
    firing2 = bool(over) and is_rb and not done and n_held > 0
    out.append({
        "code": "A2", "title": "该动没动", "firing": firing2,
        "state": "报警" if firing2 else ("待命" if over else "正常"),
        "detail": ("还没建仓，漂移这一项不判。" if not n_held else
                   ("漂移超 band 的桶：%s；%s本月%s执行过计划"
                    % ("、".join(r["name"] for r in over) if over else "无",
                       "已进入再平衡月，" if is_rb else "当前不是再平衡月，",
                       "" if done else "还没")))
                  + ("；下次再平衡 %s" % nxt if (nxt and n_held) else ""),
    })

    # A3 该减没减：还在减仓状态，但风险腿实际占比高于目标
    mult = float((rs or {}).get("multiplier") or 1.0)
    keys = set(risk_keys())
    act = sum(float(r.get("actual_w") or 0) for r in drift
              if r.get("key") in keys)
    tgt = sum(float(r.get("target_w") or 0) for r in drift
              if r.get("key") in keys)
    over_w = act - tgt
    firing3 = mult < 0.999 and over_w > A3_TOL and n_held > 0
    out.append({
        "code": "A3", "title": "该减没减", "firing": firing3,
        "state": "报警" if firing3 else ("待命" if mult < 0.999 else "正常"),
        "detail": ("风险乘数 %.2f（%s）；%s"
                   % (mult, (rs or {}).get("summary") or "-",
                      "风险腿 目标 %.1f%% / 实际 %.1f%%，差 %+.1f%%"
                      % (tgt * 100, act * 100, over_w * 100)
                      if n_held else "还没建仓，这一项不判")),
    })
    return out


# ------------------------------------------------------------------ 周

def weekly(record=True, sid=None):
    """每周只盯风险，不评价收益。"""
    st = _load()
    sid = sid or st.get("active") or strategy.active_id()
    gw, rs = signals()
    drift = drift_rows(sid)
    vol_act, n = _realized_vol(st)
    last = (st["days"] or [{}])[-1]
    exp = _expected(sid, last.get("held_days"))
    _journal_signals(gw, rs, sid)

    al = alerts(st, drift, rs)
    firing = [a for a in al if a.get("firing")]
    n_held = held_count()
    over = [r for r in drift if r.get("over_band")] if n_held else []
    mult = float(rs.get("multiplier") or 1.0)
    if firing:
        verdict = "报警"
    elif mult < 0.999 or over:
        verdict = "警戒"
    else:
        verdict = "正常"

    items = []
    if not n_held:
        items.append({
            "name": "建仓状态", "value": "还没建仓",
            "note": "账本 positions.json 是空的，下面的漂移只是纸面差额，先录持仓。",
        })
    items.append({
        "name": "趋势闸", "value": ("触发" if gw.get("on") else "未触发")
        if gw.get("ok") else "取不到",
        "note": ("%s 收 %s / %d日均线 %s，差 %+.2f%%"
                 % (gw.get("index"), gw.get("close"), gw.get("window") or 200,
                    gw.get("ma"), (gw.get("gap") or 0) * 100))
                if gw.get("ok") else (gw.get("msg") or ""),
    })
    items.append({
        "name": "空中飞人",
        "value": ("%s 分" % rs.get("airman", {}).get("score")
                  if (rs.get("airman") or {}).get("score") is not None else "无分数"),
        "note": "档位：%s" % ((rs.get("airman") or {}).get("level") or "-"),
    })
    items.append({
        "name": "回撤档位",
        "value": ("%+.2f%%" % (gw["dd"] * 100)) if gw.get("dd") is not None else "-",
        "note": "近 %s 个交易日高点回撤" % (gw.get("dd_window") or 252),
    })
    items.append({
        "name": "风险乘数", "value": "%.2f" % mult,
        "note": rs.get("summary") or "-",
    })
    items.append({
        "name": "实际波动",
        "value": ("%.2f%%" % (vol_act * 100)) if vol_act else "样本不足",
        "note": ("近 %d 个快照；同期回测口径 %.2f%%"
                 % (n, (exp.get("vol") or 0) * 100)) if vol_act else
                "需要至少 20 个日快照",
    })
    items.append({
        "name": "漂移超限",
        "value": ("%d 个桶" % len(over)) if over else "无",
        "note": "、".join("%s %+.1f%%" % (r["name"], r["drift"] * 100)
                          for r in over) or "全部在 band 之内",
    })

    entry = {"date": _today(), "level": "weekly", "sid": sid,
             "verdict": verdict, "items": items, "alerts": al,
             "headline": "风险%s" % verdict}
    if record:
        st["checks"] = [c for c in st["checks"]
                        if not (c.get("level") == "weekly"
                                and c.get("date") == entry["date"])]
        st["checks"].append(entry)
        st["checks"] = st["checks"][-400:]
        _record_alerts(st, al)
        _save(st)
    return {"ok": True, **entry}


# ------------------------------------------------------------------ 月

def monthly(record=True, sid=None):
    """每月全面体检：漂移 + 实际 vs 回测 + 对手基准 + 分腿。"""
    import tracker
    st = _load()
    sid = sid or st.get("active") or strategy.active_id()
    gw, rs = signals()
    drift = drift_rows(sid)
    try:
        rep = tracker.report(record=False, json_out=True)
    except Exception as e:
        rep = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    held = rep.get("days")
    exp = _expected(sid, held)
    act_cum, bench_cum = rep.get("cum"), rep.get("bench_cum")
    diff = (act_cum - bench_cum) if (act_cum is not None
                                     and bench_cum is not None) else None
    band = exp.get("band")
    cash_cum = _cash_bench(rep.get("start")) if rep.get("ok") else None

    over = [r for r in drift if r.get("over_band")]
    al = alerts(st, drift, rs)
    firing = [a for a in al if a.get("firing")]
    n_held = held_count()
    if not n_held:
        over = []

    in_band = None
    if diff is not None and band:
        in_band = abs(diff) <= band
    verdict = "报警" if firing else ("警戒" if over or not (in_band is not False)
                                     else "偏弱")
    if in_band is False and not firing:
        verdict = "警戒"

    legs = []
    for r in sorted(rep.get("legs") or [], key=lambda x: -(x.get("value") or 0)):
        legs.append({"bucket": r.get("bucket"), "name": r.get("name"),
                     "code": r.get("code"), "ret": r.get("ret"),
                     "value": r.get("value")})

    items = [
    ]
    if not n_held:
        items.append({
            "name": "建仓状态", "value": "还没建仓",
            "note": "账本 positions.json 是空的；先录持仓，体检才有意义。",
        })
    items += [
        {"name": "实际累计", "value": _pct(act_cum),
         "note": "持有 %s 天 · 年化 %s · 口径 %s"
                 % (held, _pct(rep.get("ann")), rep.get("basis") or "-")},
        {"name": "同期回测", "value": _pct(bench_cum),
         "note": "同策略、同窗口、含 %.2f%%/年费用" % (DRAG * 100)},
        {"name": "偏离", "value": _pct(diff),
         "note": ("在 ±1σ（±%s）之内，属于正常波动" % _pct(band)) if in_band
                 else ("超出 ±1σ（±%s），连续两个月就会报警" % _pct(band))
                 if in_band is False else "样本不足"},
        {"name": "什么都不做", "value": _pct(cash_cum),
         "note": "货币/短融口径；比它低说明担的风险没换来东西"},
        {"name": "风险乘数", "value": "%.2f" % (rs.get("multiplier") or 1.0),
         "note": rs.get("summary") or "-"},
        {"name": "漂移超限", "value": ("%d 个桶" % len(over)) if over else "无",
         "note": "、".join("%s %+.1f%%" % (r["name"], r["drift"] * 100)
                           for r in over) or "全部在 band 之内"},
        {"name": "回测体检", "value": "最大回撤 %s" % _pct(exp.get("mdd")),
         "note": "年化 %s · 最差年 %s · 最差3年 %s · Sharpe %.2f"
                 % (_pct(exp.get("cagr")), _pct(exp.get("worst_year")),
                    _pct(exp.get("worst_3y")), exp.get("sharpe") or 0)},
    ]

    entry = {"date": _today(), "level": "monthly", "sid": sid,
             "verdict": verdict, "items": items, "alerts": al, "legs": legs,
             "diff": diff, "band": band, "in_band": in_band,
             "act_cum": act_cum, "bench_cum": bench_cum, "cash_cum": cash_cum,
             "held_days": held, "drift": drift,
             "headline": "实际 %s / 回测 %s / 偏离 %s"
                         % (_pct(act_cum), _pct(bench_cum), _pct(diff))}

    # 持仓评估：产品层面的那三件事，一个月一次，跟着月检一起跑。
    # 只影响结论展示，不影响上面的漂移 / 偏离口径；失败也不阻断月检。
    try:
        h = holdings(record=record, sid=sid)
        if h.get("ok"):
            entry["holdings"] = {"verdict": h.get("verdict"),
                                 "n": h.get("n"), "bad": h.get("bad"),
                                 "text": h.get("text"), "asof": h.get("asof")}
            entry["items"].append(
                {"name": "持仓评估", "value": "%s" % (h.get("verdict") or "-"),
                 "note": "%s；看下面「持仓评估」面板（holdings 档，%s）"
                         % (h.get("text") or "-", h.get("asof") or "-")})
    except Exception as e:
        entry["holdings"] = {"verdict": "-",
                             "text": "%s: %s" % (type(e).__name__, e)}
        entry["items"].append({"name": "持仓评估", "value": "取不到",
                               "note": "%s: %s" % (type(e).__name__, e)})
    if record:
        st["checks"] = [c for c in st["checks"]
                        if not (c.get("level") == "monthly"
                                and c.get("date") == entry["date"])]
        st["checks"].append(entry)
        st["checks"] = st["checks"][-400:]
        _record_alerts(st, al)
        if entry["verdict"] in ("报警", "警戒"):
            try:
                import journal
                journal.log("评估", "月全面体检：%s" % entry["verdict"],
                            detail=entry["headline"], sid=sid,
                            level="alert" if entry["verdict"] == "报警" else "warn")
            except Exception:
                pass
        _save(st)
    return {"ok": True, **entry}


# ------------------------------------------------------------------ 持仓评估

def holdings(record=True, sid=None):
    """持仓评估：一个月一次，只看产品本身，不看组合。

    组合层面（漂移 / 回撤 / 闸门）那几档的输入全是指数和权重，没有一行看过
    手里那只 ETF；这一档补上：跟踪误差、成交额、溢价。结论只有三种 ——
    正常 / 关注 / 要查，而且每条理由都写出来。

    取数失败不算「产品坏了」：写「取不到」，不抛给上层。
    """
    import holding_eval
    st = _load()
    sid = sid or st.get("active") or strategy.active_id()
    try:
        rep = holding_eval.evaluate(sid=sid)
    except Exception as e:
        return {"ok": False, "level": "holdings",
                "msg": "%s: %s" % (type(e).__name__, e)}
    if not rep.get("ok"):
        return {"ok": False, "level": "holdings", "msg": rep.get("msg")}

    rows = rep.get("rows") or []
    bad = [r for r in rows if r.get("verdict") != "正常"]
    te_rows = [r for r in rows if (r.get("tracking") or {}).get("ok")]
    tv_rows = [r for r in rows if (r.get("turnover") or {}).get("ok")]
    pm_rows = [r for r in rows
               if (r.get("premium") or {}).get("premium") is not None]

    def worst_te():
        if not te_rows:
            return None
        return max(te_rows, key=lambda r: r["tracking"]["te"])

    def thinnest():
        if not tv_rows:
            return None
        return min(tv_rows, key=lambda r: r["turnover"]["avg"])

    def richest():
        if not pm_rows:
            return None
        return max(pm_rows, key=lambda r: r["premium"]["premium"])

    def smallest():
        xs = [r for r in rows
              if (r.get("profile") or {}).get("scale") is not None]
        return min(xs, key=lambda r: r["profile"]["scale"]) if xs else None

    w_te, thin, rich, small = worst_te(), thinnest(), richest(), smallest()
    items = [
        {"name": "结论", "value": rep.get("verdict") or "-",
         "note": "；".join("%s(%s) %s" % (r.get("code_name"), r.get("code"),
                                          r.get("verdict"))
                           for r in bad) or "全部产品都正常"},
        {"name": "评估范围", "value": "%d 只" % len(rows),
         "note": "桶里 instruments 列出的每一只，一个月跑一次；跟踪误差 / "
                 "日均成交 / 规模 / 溢价，四项都是自动取的；%s"
                 % (rep.get("asof") or "-")},
        {"name": "跟踪误差最差",
         "value": ("%.2f%%/年" % (w_te["tracking"]["te"] * 100)) if w_te else "-",
         "note": ("%s(%s) vs %s；区间差 %s"
                  % (w_te.get("code_name"), w_te.get("code"),
                     w_te["tracking"].get("bench"),
                     _pct(w_te["tracking"].get("gap")))) if w_te else "没有可比样本"},
        {"name": "成交额最小",
         "value": ("%.2f 亿/日" % (thin["turnover"]["avg"] / 1e8)) if thin else "-",
         "note": ("%s(%s)；近 %d 日均值%s"
                  % (thin.get("code_name"), thin.get("code"),
                     thin["turnover"].get("n") or 0,
                     "；规模最小 %s %.2f 亿"
                     % (small.get("code_name"), small["profile"]["scale"])
                     if (small and small.get("profile", {}).get("scale") is not None)
                     else "")) if thin else "取不到成交额"},
        {"name": "溢价最高",
         "value": _pct(rich["premium"]["premium"], 2) if rich else "-",
         "note": ("%s(%s)；近三年 %s 分位"
                  % (rich.get("code_name"), rich.get("code"),
                     "%.0f%%" % rich["premium"]["percentile"]
                     if rich["premium"].get("percentile") is not None else "?"))
                 if rich else "取不到估值"},
    ]

    entry = {"date": _today(), "level": "holdings", "sid": sid,
             "verdict": rep.get("verdict") or "-", "items": items,
             "rows": rows, "alerts": alerts(st), "month": rep.get("month"),
             "n": len(rows), "bad": len(bad),
             "text": "；".join("%s %s" % (r.get("code_name"), r.get("verdict"))
                               for r in bad) or "全部正常",
             "asof": rep.get("asof"),
             "headline": "持仓评估 %s：%s"
                         % (rep.get("verdict"),
                            "；".join("%s %s" % (r.get("code"), r.get("verdict"))
                                      for r in bad) or "全部正常")}
    if record:
        st["checks"] = [c for c in st["checks"]
                        if not (c.get("level") == "holdings"
                                and c.get("month") == entry["month"])]
        st["checks"].append(entry)
        st["checks"] = st["checks"][-400:]
        # 产品层面出问题（规模缩水 / 跟踪变差 / 溢价离谱）跟组合漂移是两回事，
        # 但它更隐蔽 —— 留一行日志，下个月翻回来看得见变化。
        if entry["verdict"] != "正常":
            try:
                import journal
                journal.log("评估", "持仓评估：%s" % entry["verdict"],
                            detail=entry["text"], sid=sid, level="warn",
                            ref={"month": entry.get("month")})
            except Exception:
                pass
        _save(st)
    return {"ok": True, **entry}


# ------------------------------------------------------------------ 季

def quarterly(record=True, sid=None):
    """每季大考：策略还成不成立。结论要明确写，包括「不改」。"""
    import tracker
    st = _load()
    sid = sid or st.get("active") or strategy.active_id()
    base = monthly(record=False, sid=sid)
    exp = _expected(sid, base.get("held_days"))
    gw, rs = signals()

    # 归因：把「实际 − 回测」拆成 漂移贡献 与 残差
    drift = base.get("drift") or []
    legs = {l.get("bucket"): l for l in (base.get("legs") or [])}
    drift_effect, priced = 0.0, 0.0
    detail = []
    for r in drift:
        l = legs.get(r.get("key"))
        if not l or l.get("ret") is None:
            continue
        c = float(r.get("drift") or 0) * float(l["ret"])
        drift_effect += c
        priced += abs(float(r.get("drift") or 0))
        detail.append({"name": r.get("name"), "drift": r.get("drift"),
                       "leg_ret": l["ret"], "effect": c})
    diff = base.get("diff")
    resid = (diff - drift_effect) if diff is not None else None

    quarters = [c for c in st.get("checks") or []
                if c.get("level") == "monthly" and c.get("diff") is not None]
    n_out = len([c for c in quarters
                 if c.get("band") and abs(c["diff"]) > c["band"]])

    issues = []
    if base.get("in_band") is False:
        issues.append("本期偏离超出 ±1σ")
    if n_out >= A1_LEVELS:
        issues.append("已连续 %d 个月落在带外" % n_out)
    if abs(drift_effect) > 0.01:
        issues.append("配置漂移本身贡献了 %s，说明再平衡不够及时"
                      % _pct(drift_effect))
    if resid is not None and abs(resid) > 0.02:
        issues.append("残差 %s 偏大，指向跟踪误差 / 溢价 / 费率"
                      % _pct(resid))

    items = [
        {"name": "本期结论", "value": "不改" if not issues else "要查",
         "note": "默认答案就是不改；只有下面这些理由才考虑动策略"},
        {"name": "策略还成立吗", "value": "成立",
         "note": "回测口径：年化 %s · 最大回撤 %s · 最差3年 %s"
                 % (_pct(exp.get("cagr")), _pct(exp.get("mdd")),
                    _pct(exp.get("worst_3y")))},
        {"name": "漂移贡献", "value": _pct(drift_effect),
         "note": "Σ 偏离权重 × 该腿收益；%s"
                 % ("，".join("%s %s×%s" % (d["name"], _pct(d["drift"]),
                                            _pct(d["leg_ret"]))
                              for d in detail[:3]) or "没有可算的腿")},
        {"name": "残差", "value": _pct(resid),
         "note": "偏离 − 漂移贡献；含跟踪误差、溢价、费用、择时"},
        {"name": "需要查的", "value": ("%d 条" % len(issues)) if issues else "无",
         "note": "；".join(issues) or "没有触发条件"},
        {"name": "危机对照", "value": "%d 次" % len(exp.get("crisis") or {}),
         "note": "；".join("%s %s" % (k, _pct(v.get("ret")))
                           for k, v in list((exp.get("crisis") or {}).items())[:3])
                 or "-"},
    ]
    entry = {"date": _today(), "level": "quarterly", "sid": sid,
             "verdict": "要查" if issues else "正常",
             "items": items, "alerts": base.get("alerts") or [],
             "drift_effect": drift_effect, "resid": resid, "issues": issues,
             "diff": diff, "band": base.get("band"),
             "headline": "结论：%s" % ("不改" if not issues else "要查"),
             "attribution": detail}
    if record:
        st["checks"] = [c for c in st["checks"]
                        if not (c.get("level") == "quarterly"
                                and c.get("date") == entry["date"])]
        st["checks"].append(entry)
        st["checks"] = st["checks"][-400:]
        try:
            import journal
            journal.log("评估", "季大考：%s" % entry["headline"],
                        detail="；".join(i["name"] + " " + str(i["value"])
                                         for i in entry["items"]),
                        sid=sid, level="warn" if issues else "info")
        except Exception:
            pass
        _save(st)
    return {"ok": True, **entry}


# ------------------------------------------------------------------ 工具

def _pct(v, digits=2):
    return "-" if v is None else ("%+.*f%%" % (digits, v * 100))


def _record_alerts(st, al):
    """报警只在「本月同一条」没记过的时候追加，避免刷屏。"""
    hist = st.setdefault("alerts", [])
    fresh = []
    for a in al:
        if not a.get("firing"):
            continue
        if any(x.get("code") == a["code"] and x.get("ym") == _ym()
               for x in hist):
            continue
        hist.append({"ym": _ym(), "date": _today(), "code": a["code"],
                     "title": a["title"], "detail": a.get("detail")})
        fresh.append(a)
    st["alerts"] = hist[-200:]
    # 报警线是「必须留痕」的那一类：进了事件日志才能事后数「这一年报了几次、
    # 报完我做了什么」。同一档位同一个月只记一次，规则跟上面一致。
    if fresh:
        try:
            import journal
            for a in fresh:
                journal.log("报警", "%s %s" % (a.get("code"), a.get("title") or ""),
                            detail=a.get("detail") or "", level="alert")
        except Exception:
            pass


def level_for_today(today=None):
    """今天该跑哪一档：周一跑周检，月初跑月检，季初跑大考。"""
    d = today or datetime.date.today()
    if d.month in (1, 4, 7, 10) and d.day <= 7:
        return "quarterly"
    if d.day <= 3:
        return "monthly"
    if d.weekday() == 0:
        return "weekly"
    return "daily"


def run(level=None, record=True, sid=None):
    level = level or level_for_today()
    if level == "daily":
        return daily(record=record, sid=sid)
    if level == "weekly":
        return weekly(record=record, sid=sid)
    if level == "monthly":
        return monthly(record=record, sid=sid)
    if level == "quarterly":
        return quarterly(record=record, sid=sid)
    if level == "holdings":
        return holdings(record=record, sid=sid)
    if level == "all":
        return {k: run(k, record=record, sid=sid) for k in LEVELS}
    return {"ok": False, "msg": "不认识的档位：%s" % level}


def status():
    """给页面用的一份摘要：最近一次各档结论 + 报警历史。"""
    st = _load()
    latest = {}
    for c in st.get("checks") or []:
        c = dict(c)
        # 持仓评估那档带 12 只产品的明细，太重；页面单独去
        # /api/strategy/holdings 取，这里只留结论。
        c.pop("rows", None)
        latest[c.get("level")] = c
    return {"ok": True, "asof": st.get("asof"), "active": st.get("active"),
            "today_level": level_for_today(),
            "day_count": len(st.get("days") or []),
            "last_day": (st.get("days") or [{}])[-1],
            "latest": latest,
            "alerts": alerts(st),
            "alert_history": (st.get("alerts") or [])[-20:],
            "recent_days": (st.get("days") or [])[-30:]}


def text_report(r, depth=0):
    pad = "  " * depth
    if not r.get("ok"):
        print("%s[失败] %s" % (pad, r.get("msg")))
        return
    print("%s%s %s" % (pad, r.get("level"), r.get("headline") or ""))
    for it in r.get("items") or []:
        print("%s  %-10s %-22s %s" % (pad, it.get("name"), it.get("value"),
                                      it.get("note") or ""))
    for a in r.get("alerts") or []:
        print("%s  [%s] %-8s %s" % (pad, a.get("code"), a.get("state"),
                                    a.get("detail") or ""))


# ------------------------------------------------------------------ 自动化

class DailyRecorder(threading.Thread):
    """后台每天记一条日快照。

    只有在「行情日期 == 今天」时才记，所以周末和节假日不会平白多出空行。
    同一天重复跑只会覆盖同一条，多跑无害。
    """

    def __init__(self, interval=1800):
        threading.Thread.__init__(self, name="monitor-daily")
        self.daemon = True
        self.interval = int(interval)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        import tracker
        time.sleep(20)          # 让别的服务先起完
        while not self._stop.is_set():
            try:
                gw, _ = signals()
                if gw.get("date") == _today():
                    r = daily(record=True)
                    if r.get("row", {}).get("ok"):
                        print("[monitor] 日快照已记：%s 累计 %s"
                              % (r["row"]["date"],
                                 _pct(r["row"].get("cum"))), flush=True)
            except Exception as e:
                print("[monitor] 日快照失败：%s: %s"
                      % (type(e).__name__, e), flush=True)
            self._stop.wait(self.interval)


def main():
    ap = argparse.ArgumentParser(description="策略体检（四档 + 三条报警线）")
    ap.add_argument("--level", default=None,
                    help="daily / weekly / monthly / quarterly / holdings / all")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--status", action="store_true", help="只看当前状态")
    args = ap.parse_args()

    if args.status:
        r = {"ok": True, "status": status()}
    else:
        r = run(args.level, record=not args.no_record)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    if "status" in r:
        s = r["status"]
        print("今天该跑：%s · 日快照 %d 条 · 最近一条 %s"
              % (s["today_level"], s["day_count"],
                 (s.get("last_day") or {}).get("date") or "-"))
        for k, v in (s.get("latest") or {}).items():
            print("  %-10s %s" % (k, v.get("headline")))
        for a in s.get("alerts") or []:
            print("  [%s] %-8s %s" % (a.get("code"), a.get("state"),
                                      a.get("detail")))
        return 0
    if args.level == "all":
        for k in LEVELS:
            text_report(r[k])
        return 0
    text_report(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
