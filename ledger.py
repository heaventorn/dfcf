# -*- coding: utf-8 -*-
"""现金流台账：钱从哪来、到哪去，跟 positions.json 对得上。

positions.json 只回答「我现在持有什么」，它不知道你往里投了多少、取现多少、
收了多少分红、交了多少手续费。于是「我投了多少钱」和「我赚了多少钱」永远
分不开 —— 分几批建仓、场内外混着买的时候，这两个数会越差越远。

这个文件补的就是那一半。

存储：output/ledger.json

一笔长这样：
    {"id": 3, "date": "20260920", "at": "2026-09-20 21:10:04",
     "kind": "买入", "amount": 120000.0, "fee": 24.0,
     "code": "512890", "bucket": "divA", "shares": 120000, "price": 1.0,
     "paper": false, "note": "第2批"}

口径（谁加谁减，只有这一处定义）：
    入金 +     出金 −     买入 −(金额 + 手续费)     卖出 +(金额 − 手续费)
    分红 +     利息 +     手续费 −                   调整 ±金额（可为负）
  「金额」永远填正数，方向由类型决定；只有「调整」允许填负数。

由此得到两个不会被行情带偏的数：
    净投入 = Σ入金 − Σ出金           （我到底往里放了多少本金）
    台账现金 = Σ上面那一列            （账上还剩多少没买）
  再叠加持仓市值，就有「我赚了多少钱」。持仓页那个「现金 = 总额 − 现在市值」
  是另一个口径，会随行情漂；两者的差就是浮动盈亏 —— 台账会把它单独列出来。

用法：
    py ledger.py add --kind 入金 --amount 100000 --note "第1批"
    py ledger.py list --limit 30
    py ledger.py summary
    py ledger.py --json summary
"""

from __future__ import print_function

import argparse
import datetime
import io
import json
import os
import sys
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config

FILE = os.path.join(config.OUTPUT_DIR, "ledger.json")

KINDS = ("入金", "出金", "买入", "卖出", "分红", "利息", "手续费", "调整")

_lock = threading.Lock()


# ------------------------------------------------------------------ 小工具

def _today(day=None):
    return (day or datetime.date.today()).strftime("%Y%m%d")


def _dump(obj):
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FILE)
        return True
    except Exception:
        return False


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("version", 1)
    d.setdefault("seq", 0)
    d.setdefault("entries", [])
    return d


def save(d):
    d["asof"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return _dump(d)


def signed(e):
    """这一笔对「台账现金」的增减。全系统只有这一处在定义方向。"""
    k = e.get("kind")
    a = float(e.get("amount") or 0)
    fee = float(e.get("fee") or 0)
    if k == "买入":
        return -(a + fee)
    if k == "卖出":
        return a - fee
    if k in ("入金", "分红", "利息"):
        return a
    if k in ("出金", "手续费"):
        return -a
    if k == "调整":
        return a
    return 0.0


# ------------------------------------------------------------------ 记一笔

def make(kind, amount, fee=0.0, code="", bucket="", shares=0.0,
         price=0.0, note="", paper=False, date=None, at=None):
    """拼一笔（不落盘）。"""
    kind = (kind or "").strip()
    if kind not in KINDS:
        raise ValueError("不认识的类型：%s（只能是 %s）" % (kind, " / ".join(KINDS)))
    a = float(amount or 0)
    if a < 0 and kind != "调整":
        raise ValueError("「%s」的金额要填正数，方向由类型决定" % kind)
    if a == 0 and not fee:
        raise ValueError("金额是 0，没什么可记的")
    t = at if isinstance(at, datetime.datetime) else datetime.datetime.now()
    return {"date": _today(date), "at": t.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind, "amount": round(a, 2), "fee": round(float(fee or 0), 2),
            "code": str(code or "").strip(), "bucket": bucket or "",
            "shares": float(shares or 0), "price": float(price or 0),
            "paper": bool(paper), "note": note or ""}


def _append(items, journal=True):
    if not items:
        return True
    with _lock:
        d = load()
        seq = int(d.get("seq") or 0)
        stamped = []
        for it in items:
            seq += 1
            it = dict(it)
            it["id"] = seq
            d["entries"].append(it)
            stamped.append(it)
        d["seq"] = seq
        ok = save(d)
    if journal:
        try:
            import journal as jr
            for it in stamped:
                jr.log("账本", "%s ¥%s" % (it["kind"],
                                           format(float(it["amount"]), ",.2f")),
                       detail=" · ".join(x for x in [
                           it.get("note"),
                           (it.get("code") or "") + " ×%g" % it.get("shares", 0)
                           if it.get("code") else "",
                           "实验仓位" if it.get("paper") else ""] if x),
                       level="act", ref={"ledger_id": it.get("id")})
        except Exception:
            pass
    return ok


def add(kind, amount, journal=True, **kw):
    """记一笔并落盘。返回这一笔。"""
    it = make(kind, amount, **kw)
    _append([it], journal=journal)
    return it


def add_many(items, journal=True):
    """一次记一批（建仓用）。任何一笔不合法就整批拒绝，不留半截。"""
    if not items:
        return []
    out = [make(**dict(x)) for x in items]
    _append(out, journal=journal)
    return out


def entries(limit=None, kind=None, code=None, paper=None, newest_first=True):
    es = load().get("entries") or []
    out = []
    for e in es:
        if kind and e.get("kind") != kind:
            continue
        if code and e.get("code") != code:
            continue
        if paper is not None and bool(e.get("paper")) != bool(paper):
            continue
        out.append(e)
    if limit:
        out = out[-int(limit):]
    return out[::-1] if newest_first else out


# ------------------------------------------------------------------ 汇总

def totals():
    """按类型汇总，纯记账口径，不碰行情。"""
    es = load().get("entries") or []
    by = {k: 0.0 for k in KINDS}
    fees, cash, paper_in = 0.0, 0.0, 0.0
    for e in es:
        k = e.get("kind")
        if k in by:
            by[k] += float(e.get("amount") or 0)
        fees += float(e.get("fee") or 0)
        cash += signed(e)
        if k == "入金" and e.get("paper"):
            paper_in += float(e.get("amount") or 0)
    return {"ok": True, "n": len(es), "by_kind": by,
            "net_in": by["入金"] - by["出金"], "cash": cash,
            "buy": by["买入"], "sell": by["卖出"],
            "dividend": by["分红"], "interest": by["利息"],
            "fee": fees + by["手续费"],
            "net_cost": by["买入"] - by["卖出"],
            "paper_in": paper_in,
            "first": (es[0] or {}).get("date") if es else None,
            "last": (es[-1] or {}).get("date") if es else None}


def market_value():
    """持仓市值与成本：positions.json + 现价。取不到给 ok=False。"""
    try:
        import strategy
        pos = [p for p in (strategy.load_positions() or {}).get("positions") or []
               if float(p.get("shares") or 0) > 0]
        px = strategy.prices([p.get("code") for p in pos]) if pos else {}
        mv, cost = 0.0, 0.0
        for p in pos:
            code = str(p.get("code") or "")
            sh = float(p.get("shares") or 0)
            c = float(p.get("cost") or 0)
            n = px.get(code) or c
            mv += sh * n
            cost += sh * c
        return {"ok": True, "n": len(pos), "value": mv, "cost": cost}
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}


def summary(with_market=True):
    """台账全貌。页面顶上那排卡片就是这份。"""
    t = totals()
    es = load().get("entries") or []
    days = None
    if es:
        try:
            a = datetime.datetime.strptime(es[0]["date"], "%Y%m%d").date()
            days = max((datetime.date.today() - a).days, 0)
        except Exception:
            days = None

    out = dict(t)
    out["days"] = days
    out["market"] = None
    out["total"] = None
    out["pnl"] = None
    out["ret"] = None
    out["ann"] = None
    out["float_pnl"] = None
    out["recon"] = None
    if not with_market:
        return out

    m = market_value()
    out["market"] = m
    if not m.get("ok"):
        return out
    total = m["value"] + t["cash"]
    out["total"] = total
    out["pnl"] = total - t["net_in"]
    out["ret"] = (out["pnl"] / t["net_in"]) if t["net_in"] else None
    if out["ret"] is not None and days and days >= 30:
        out["ann"] = (1 + out["ret"]) ** (365.0 / days) - 1
    # 浮动盈亏 = 现在市值 − 买入净成本 − 手续费。持仓页那套「现金 = 总额 −
    # 现在市值」是倒算的，行情一动它就跟着动，两者相差的就是这个数。
    out["float_pnl"] = m["value"] - t["net_cost"] - t["fee"]
    out["recon"] = {
        "cost_basis": t["net_cost"] + t["fee"],
        "market_value": m["value"],
        "float_pnl": out["float_pnl"],
        "cash_ledger": t["cash"],
        "note": "持仓页的「现金 = 总额 − 现在市值」是倒算的，会随行情漂；"
                "台账这份是真现金（总额 − 买入 + 卖出 − 费用）。"
                "两者相差的就是浮动盈亏。",
    }
    return out


# ------------------------------------------------------------------ 输出

def _pct(v, d=2):
    return "-" if v is None else ("%+.*f%%" % (d, v * 100))


def _yuan(v):
    return "-" if v is None else format(float(v), ",.0f")


def text_report(r):
    L = []
    L.append("=" * 64)
    L.append("现金流台账 · %d 笔 · 起 %s" % (r["n"], r["first"] or "-"))
    L.append("-" * 64)
    L.append("累计入金 %s   累计出金 %s   净投入 %s"
             % (_yuan(r["by_kind"]["入金"]), _yuan(r["by_kind"]["出金"]),
                _yuan(r["net_in"])))
    L.append("买入 %s   卖出 %s   分红 %s   利息 %s   手续费 %s"
             % (_yuan(r["buy"]), _yuan(r["sell"]), _yuan(r["dividend"]),
                _yuan(r["interest"]), _yuan(r["fee"])))
    L.append("台账现金 %s" % _yuan(r["cash"]))
    if r.get("total") is not None:
        L.append("-" * 64)
        L.append("持仓市值 %s   总资产 %s"
                 % (_yuan(r["market"]["value"]), _yuan(r["total"])))
        L.append("累计盈亏 %s   收益率 %s   年化 %s（%s 天）"
                 % (_yuan(r["pnl"]), _pct(r["ret"]), _pct(r["ann"]),
                    r.get("days")))
        L.append("浮动盈亏 %s（市值 − 买入净成本 − 手续费）"
                 % _yuan(r["float_pnl"]))
    if r.get("paper_in"):
        L.append("⚠ 其中 %s 是实验（纸上）建仓，不是真花的钱。" % _yuan(r["paper_in"]))
    L.append("=" * 64)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="现金流台账")
    ap.add_argument("cmd", nargs="?", default="summary",
                    choices=["add", "list", "summary"])
    ap.add_argument("--kind", default=None)
    ap.add_argument("--amount", type=float, default=None)
    ap.add_argument("--fee", type=float, default=0.0)
    ap.add_argument("--code", default="")
    ap.add_argument("--bucket", default="")
    ap.add_argument("--shares", type=float, default=0.0)
    ap.add_argument("--price", type=float, default=0.0)
    ap.add_argument("--note", default="")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if a.cmd == "add":
        if not a.kind or a.amount is None:
            print("要填 --kind 和 --amount")
            return 1
        try:
            e = add(a.kind, a.amount, fee=a.fee, code=a.code, bucket=a.bucket,
                    shares=a.shares, price=a.price, note=a.note, paper=a.paper)
        except ValueError as err:
            print(err)
            return 1
        print("已记：", json.dumps(e, ensure_ascii=False))
        return 0

    if a.cmd == "list":
        es = entries(limit=a.limit)
        if a.json:
            print(json.dumps(es, ensure_ascii=False, indent=1))
            return 0
        for e in es:
            print("%s #%-4s %-4s %12s %s %s"
                  % (e.get("at"), e.get("id"), e.get("kind"),
                     format(float(e.get("amount") or 0), ",.2f"),
                     e.get("code") or "", e.get("note") or ""))
        return 0

    r = summary()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    print(text_report(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
