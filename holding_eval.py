# -*- coding: utf-8 -*-
"""持仓评估：一个月一次，只看「手里这只产品本身好不好」。

跟体检的四档是两件事：
  · 日 / 周 / 月 / 季那四档看的是**组合**：漂移、回撤、闸门、实际 vs 回测。
    它们的输入全是指数和权重，没有一行看过你手里那只 ETF 本身。
  · 这一层看的是**产品**：跟踪误差、流动性、溢价。回答的是
    「这东西坏了没有 / 买得进买不进 / 有没有更便宜的同样的东西」，
    不回答「现在该不该买」—— 后者是择时，回测证明不了。

四项，全部自动取数：
  1. 跟踪误差   ETF 净值 vs 该桶的指数基准，近 250 个交易日，年化
  2. 流动性     近 20 日成交额（腾讯日线 量 × 100 × 收盘）
  3. 规模       季报口径，看 5000 万清盘线（同一份基金档案里就有）
  4. 溢价/折价  复用 premium：现在多少、历史分位、桶的溢价闸门

明确不做（要手工维护，暂时不进来）：费率、基金公告、基金经理变更、
「当初为什么买它」的复核。

三个口径坑，都在这个文件里解决：
  · **交易日历不一样**。ETF 净值走 A 股日历，基准指数（美股 / 港股）不是。
    直接取日期交集，会把「指数多走的那一天」算成跟踪误差。所以基准一律按
    「净值日期当天或之前最近的一个值」取（as-of 对齐），前后腿走同一段区间。
  · **分红 / 份额折算日**。那天净值会凭空掉一块，那是分红不是跟踪差。
    用 pingzhongdata 的 unitMoney 字段标出来剔除，并在结果里单列。
    实测：不剔，510300 的跟踪误差是 2.55%/年；剔掉 20260119 那天才是 0.19%。
  · **基准指数本身可能不对**。两种情形都当「不判」处理，不许它变成假报警：
      东财 100.NDX 返回的其实是纳斯达克综合指数(.IXIC)，不是纳指100；
      513630 跟踪的是标普港股通低波红利，回测代理用的是中证港股通高股息。
    按桶的 eval_bench 配置走：能换成对的就换，换不了就标 N/A。

用法
    py holding_eval.py            算一次（当月已算过就读缓存）
    py holding_eval.py --force    强制重算
    py holding_eval.py --json     给页面 / 别的脚本用
"""

from __future__ import print_function

import argparse
import bisect
import datetime
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import bars
import config
import premium
import strategy

FILE = os.path.join(config.OUTPUT_DIR, "holding_eval.json")

TE_WINDOW = 250       # 跟踪误差窗口（交易日），约一年
TE_MIN_N = 120        # 有效样本不够就不下结论，宁可说「样本不足」
DROP_GAP = 0.025      # 单日偏离超过这个数，当异常日剔除并单列
LIQ_WINDOW = 20       # 成交额取近 20 日均值
LIQ_WEAK = 5e7        # 5000 万/日 以下算偏弱
LIQ_THIN = 1e7        # 1000 万/日 以下算太薄（家庭账户也难进出）
# 跟踪误差的警戒线（年化），按桶的风险组给：无风险产品没那么大波动空间，
# 拿股票的线去管债券等于不管。
TE_WARN = {"无风险": 0.02, "中风险": 0.04, "避险": 0.03, "高风险": 0.06}
PREMIUM_WARN, PREMIUM_BLOCK = 0.01, 0.03

_lock = threading.Lock()


# ------------------------------------------------------------------ 工具

def _ym(day=None):
    return (day or datetime.date.today()).strftime("%Y-%m")


def _asof(days_sorted, d):
    """最后一个 <= d 的日期。基准指数比净值少几天时，用它顶上去。"""
    i = bisect.bisect_right(days_sorted, d) - 1
    return days_sorted[i] if i >= 0 else None


def _name(spec):
    return spec.get("name") or spec.get("code") or "-"


def _num(v, digits=2):
    return "-" if v is None else ("%+.*f%%" % (digits, v * 100))


# ------------------------------------------------------------------ 基准

def bench_of(spec):
    """该桶的评估基准。eval_bench 是评估专用，没配就用回测代理。"""
    b = spec.get("eval_bench") or spec.get("proxy") or {}
    b = dict(b)
    # 显示名：优先基准自己的名字，其次桶名（代理就是这只桶的指标时）
    if not b.get("name") and spec.get("name") and b.get("code"):
        b["name"] = "%s（%s）" % (spec["name"], b["code"])
    b["name"] = b.get("name") or spec.get("name") or b.get("code") or "-"
    if b.get("skip"):
        return {}, b, "skip"
    try:
        ser = bars.proxy_series({k: v for k, v in b.items()
                                 if not str(k).startswith("_") and k != "skip"})
    except Exception:
        ser = {}
    return ser or {}, b, ("ok" if ser else "empty")


# ------------------------------------------------------------------ 跟踪误差

def tracking(code, spec):
    """ETF 净值 vs 基准指数：年化跟踪误差 + 区间累计偏离。"""
    nav, ev = premium.nav_series(code, want_events=True)
    if not nav:
        return {"ok": False, "msg": "拿不到净值"}
    idx, bspec, state = bench_of(spec)
    if state == "skip":
        return {"ok": False, "skip": True, "bench": _name(bspec),
                "msg": bspec.get("_comment") or "该桶没有可比的基准指数"}
    if not idx:
        return {"ok": False, "bench": "%s/%s" % (bspec.get("src"), bspec.get("code")),
                "msg": "基准指数取不到"}

    idd = sorted(idx)
    ds = sorted(nav)
    rows = []
    for i in range(1, len(ds)):
        a, b = ds[i - 1], ds[i]
        ia, ib = _asof(idd, a), _asof(idd, b)
        if not ia or not ib or ia == ib:
            continue
        if nav[a] <= 0 or idx[ia] <= 0:
            continue
        rows.append((b, nav[b] / nav[a] - 1.0, idx[ib] / idx[ia] - 1.0,
                     b in ev))
    rows = rows[-TE_WINDOW:]
    keep = [r for r in rows if not r[3] and abs(r[1] - r[2]) <= DROP_GAP]
    drop = [{"date": b, "gap": f - i,
             "why": (ev.get(b) or "单日偏离异常，疑似分红/折算")[:40]}
            for b, f, i, e in rows if e or abs(f - i) > DROP_GAP]
    if len(keep) < TE_MIN_N:
        return {"ok": False, "skip": True, "bench": _name(bspec),
                "msg": "有效样本只有 %d 天，不够判（要 %d 天）"
                       % (len(keep), TE_MIN_N)}

    diffs = [f - i for _, f, i, _ in keep]
    n = len(diffs)
    mu = sum(diffs) / n
    var = sum((d - mu) ** 2 for d in diffs) / (n - 1)
    cf = ci = 1.0
    for _, f, i, _ in keep:
        cf *= 1 + f
        ci *= 1 + i
    return {"ok": True, "te": (var ** 0.5) * (252 ** 0.5),
            "bias": mu * 252, "n": n,
            "fund_ret": cf - 1, "idx_ret": ci - 1, "gap": (cf - ci),
            "from": keep[0][0], "to": keep[-1][0],
            "bench": _name(bspec), "dropped": drop[-3:]}


# ------------------------------------------------------------------ 成交额

def turnover(code, window=LIQ_WINDOW):
    """近 window 日成交额。腾讯日线的量是「手」，×100×收盘 = 元。"""
    try:
        import kchart
        df = kchart.fetch_kline(bars.tx_code(code), n=window + 8)
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    if df is None or len(df) == 0:
        return {"ok": False, "msg": "拿不到日线"}
    amts = [(float(r["volume"]) * 100.0 * float(r["close"]),
             str(r["date"])[:10]) for _, r in df.tail(window).iterrows()
            if float(r["close"]) > 0]
    if not amts:
        return {"ok": False, "msg": "成交额算不出来"}
    vals = [a for a, _ in amts]
    return {"ok": True, "avg": sum(vals) / len(vals), "last": vals[-1],
            "last_date": amts[-1][1], "n": len(vals),
            "min": min(vals), "max": max(vals)}


# ------------------------------------------------------------------ 溢价

def fund_profile(code):
    """基金档案：规模（清盘线）+ 机构持有比例 + 现金占净比。都自动取。"""
    try:
        p = premium.profile(code)
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    if not p:
        return {"ok": False, "msg": "拿不到基金档案"}
    return {"ok": True, **p}


def premium_row(code, spec, quote=None):
    """当前溢价 + 历史分位 + 桶的溢价闸门。复用 premium，不另算一套。"""
    try:
        snap = premium.snapshot(code, quote)
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
    if not snap:
        return {"ok": False, "msg": "拿不到估值"}
    cfg = spec.get("premium") or {}
    warn = float(cfg.get("warn") or PREMIUM_WARN)
    block = float(cfg.get("block") or PREMIUM_BLOCK)
    g = premium.gate(snap, warn, block)
    return {"ok": True, "premium": snap.get("premium"),
            "percentile": snap.get("percentile"),
            "src": snap.get("basis_src"), "price": snap.get("price"),
            "iopv": snap.get("iopv"), "nav": snap.get("nav"),
            "time": snap.get("quote_time"),
            "median": (snap.get("stats") or {}).get("median"),
            "p10": (snap.get("stats") or {}).get("p10"),
            "p90": (snap.get("stats") or {}).get("p90"),
            "warn": warn, "block": block, "gate": g}


# ------------------------------------------------------------------ 单只结论

def judge(spec, row):
    """一只产品的结论。理由都写出来，页面直接显示，不藏。"""
    notes, level = [], "正常"

    def bump(to):
        order = {"正常": 0, "关注": 1, "要查": 2}
        if order[to] > order[level]:
            return to
        return level

    te = row.get("tracking") or {}
    limit = TE_WARN.get(spec.get("group"), 0.04)
    if te.get("ok"):
        if te["te"] > limit * 1.5:
            level = bump("要查")
            notes.append("跟踪误差 %.2f%%/年，是同类警戒线 %.1f%% 的 1.5 倍以上"
                         % (te["te"] * 100, limit * 100))
        elif te["te"] > limit:
            level = bump("关注")
            notes.append("跟踪误差 %.2f%%/年，高于 %.1f%% 警戒线"
                         % (te["te"] * 100, limit * 100))
        else:
            notes.append("跟踪误差 %.2f%%/年，在 %.1f%% 以内"
                         % (te["te"] * 100, limit * 100))
        span = te["te"] * ((te["n"] / 252.0) ** 0.5)
        gap = te.get("gap")
        if gap is not None and abs(gap) > max(0.03, 2 * span):
            level = bump("关注")
            notes.append("区间累计差 %s（基金 %s vs 基准 %s），比跟踪误差本身还大，"
                         "值得看看是分红税、汇率还是换了标的"
                         % (_num(gap), _num(te.get("fund_ret")),
                            _num(te.get("idx_ret"))))
        if te.get("dropped"):
            notes.append("剔除 %d 个异常日（最近 %s）"
                         % (len(te["dropped"]), te["dropped"][-1]["date"]))
    elif te.get("skip"):
        notes.append("跟踪误差不判：%s" % (te.get("msg") or "-"))
    else:
        level = bump("关注")
        notes.append("跟踪误差取不到：%s" % (te.get("msg") or "-"))

    tv = row.get("turnover") or {}
    if tv.get("ok"):
        if tv["avg"] < LIQ_THIN:
            level = bump("要查")
            notes.append("日均成交 %.0f 万，太薄，几十万的单子就会砸出坑"
                         % (tv["avg"] / 1e4))
        elif tv["avg"] < LIQ_WEAK:
            level = bump("关注")
            notes.append("日均成交 %.0f 万，偏弱" % (tv["avg"] / 1e4))
        else:
            notes.append("日均成交 %.2f 亿，够用" % (tv["avg"] / 1e8))
    else:
        level = bump("关注")
        notes.append("成交额取不到：%s" % (tv.get("msg") or "-"))

    pf = row.get("profile") or {}
    if pf.get("ok"):
        sc = pf.get("scale")
        if sc is None:
            notes.append("规模取不到")
        elif sc < 0.5:
            level = bump("要查")
            notes.append("规模只有 %.2f 亿（%s），贴着 5000 万清盘线，说清就见清盘公告"
                         % (sc, pf.get("scale_date") or "-"))
        elif sc < 2:
            level = bump("关注")
            notes.append("规模 %.2f 亿（%s），偏小，赎回一多就被动"
                         % (sc, pf.get("scale_date") or "-"))
        else:
            notes.append("规模 %.1f 亿（%s）"
                         % (sc, pf.get("scale_date") or "-"))
        prev = pf.get("scale_prev")
        if sc is not None and prev and prev > 0 and sc < prev * 0.7:
            notes.append("规模环比降了 %.0f%%，看一眼是不是大额赎回"
                         % ((1 - sc / prev) * 100))
    else:
        notes.append("规模 / 持有结构取不到：%s" % (pf.get("msg") or "-"))

    pm = row.get("premium") or {}
    if pm.get("ok") and pm.get("premium") is not None:
        g = pm.get("gate") or {}
        if g.get("level") == "block":
            level = bump("要查")
            notes.append(g.get("reason") or "溢价过高")
        elif g.get("level") in ("warn",):
            level = bump("关注")
            notes.append(g.get("reason") or "溢价偏高")
        elif g.get("level") == "good":
            notes.append(g.get("reason") or "折价")
        else:
            notes.append(g.get("reason") or "溢价正常")
        if pm.get("percentile") is not None:
            notes.append("处于近三年 %.0f%% 分位（中位 %s）"
                         % (pm["percentile"], _num(pm.get("median"))))
    elif pm.get("ok"):
        notes.append("溢价拿不到（%s 没有实时估值）" % row.get("code"))
    else:
        level = bump("关注")
        notes.append("溢价取不到：%s" % (pm.get("msg") or "-"))
    return level, notes


# ------------------------------------------------------------------ 汇总

def evaluate(force=False, sid=None):
    """跑一遍持仓评估。当月算过就直接读缓存（一个月一次）。"""
    st = load()
    ym = _ym()
    if not force and st.get("month") == ym and st.get("rows"):
        return {"ok": True, "cached": True, **st}

    bk = strategy.buckets()
    codes = []
    for spec in bk.values():
        codes.extend(spec.get("instruments") or [])
    codes = list(dict.fromkeys(codes))
    try:
        qm = premium.quotes(codes)
    except Exception:
        qm = {}

    # 成交额要单独打腾讯，串行 12 只太慢，并发跑
    tv = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(turnover, c): c for c in codes}
        for f, c in futs.items():
            try:
                tv[c] = f.result()
            except Exception as e:
                tv[c] = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    rows, worst = [], "正常"
    order = {"正常": 0, "关注": 1, "要查": 2}
    for key, spec in bk.items():
        for code in spec.get("instruments") or []:
            row = {"key": key, "bucket": spec.get("name"), "group": spec.get("group"),
                   "code": code, "code_name": (qm.get(code) or {}).get("name") or code,
                   "tracking": tracking(code, spec),
                   "turnover": tv.get(code) or {"ok": False, "msg": "没跑"},
                   "profile": fund_profile(code),
                   "premium": premium_row(code, spec, qm.get(code))}
            level, notes = judge(spec, row)
            row["verdict"] = level
            row["notes"] = notes
            rows.append(row)
            if order[level] > order[worst]:
                worst = level

    st = {"month": ym, "asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
          "verdict": worst, "rows": rows, "sid": sid or strategy.active_id()}
    save(st)
    return {"ok": True, "cached": False, **st}


def summary(report=None):
    """给月检 / 页面用的一句话摘要。"""
    r = report if report is not None else evaluate()
    rows = r.get("rows") or []
    if not rows:
        return {"verdict": "-", "text": "还没有评估结果"}
    bad = [x for x in rows if x.get("verdict") != "正常"]
    text = "、".join("%s(%s) %s" % (x["code_name"], x["code"], x["verdict"])
                     for x in bad) or "全部正常"
    return {"verdict": r.get("verdict"), "n": len(rows), "bad": len(bad),
            "text": text, "asof": r.get("asof")}


def warm():
    """后台先把这一个月要用的数抓齐，页面点的时候就不用等。"""
    try:
        bk = strategy.buckets()
        codes = []
        for spec in bk.values():
            for c in spec.get("instruments") or []:
                try:
                    premium.nav_series(c, want_events=True)
                except Exception:
                    pass
            try:
                bench_of(spec)
            except Exception:
                pass
            codes.extend(spec.get("instruments") or [])
        for c in dict.fromkeys(codes):
            try:
                premium.profile(c)
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(turnover, dict.fromkeys(codes)))
    except Exception:
        pass


# ------------------------------------------------------------------ 缓存

def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save(st):
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FILE)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 输出

def text_report(r):
    if not r.get("ok"):
        print("[失败] %s" % r.get("msg"))
        return
    print("持仓评估 %s（%s）结论：%s%s"
          % (r.get("month"), r.get("asof"), r.get("verdict"),
             "（读缓存）" if r.get("cached") else ""))
    for x in r.get("rows") or []:
        t = x.get("tracking") or {}
        tv = x.get("turnover") or {}
        pm = x.get("premium") or {}
        print("  [%s] %-7s %-22s %s"
              % (x.get("verdict"), x.get("code"), x.get("code_name"),
                 x.get("bucket")))
        print("        跟踪误差 %s/年  区间差 %s  样本 %s 天  基准 %s"
              % ("%.2f%%" % (t["te"] * 100) if t.get("ok") else "N/A",
                 _num(t.get("gap")) if t.get("ok") else "-",
                 t.get("n") or "-", t.get("bench") or "-"))
        print("        日均成交 %s   溢价 %s%s"
              % ("%.2f 亿" % (tv["avg"] / 1e8) if tv.get("ok") else "N/A",
                 _num(pm.get("premium")) if pm.get("ok") else "N/A",
                 ("（近三年 %.0f%% 分位）" % pm["percentile"])
                 if pm.get("ok") and pm.get("percentile") is not None else ""))
        pf = x.get("profile") or {}
        if pf.get("ok") and pf.get("scale") is not None:
            print("        规模 %.1f 亿（%s）%s"
                  % (pf["scale"], pf.get("scale_date") or "-",
                     "  机构 %.0f%%" % pf["inst_pct"]
                     if pf.get("inst_pct") is not None else ""))
        for n in x.get("notes") or []:
            print("        · %s" % n)


def main():
    ap = argparse.ArgumentParser(description="持仓评估（月度）")
    ap.add_argument("--force", action="store_true", help="忽略当月缓存重算")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true", help="只要一句摘要")
    args = ap.parse_args()
    r = evaluate(force=args.force)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    if args.summary:
        s = summary(r)
        print("%s · %s" % (s.get("verdict"), s.get("text")))
        return 0
    text_report(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
