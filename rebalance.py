# -*- coding: utf-8 -*-
"""再平衡 / 下单计划：把「应该持有什么」和「实际持有什么」的差，翻译成买卖。

刻意不做的事：
  * **不自动交易**。这里只产出计划，你点确认才写进 positions.json，
    而 positions.json 是账本（记录你实际成交了什么），不是委托单。
    没有券商 API、没有钱会自己动，这样出 bug 最多是算错一笔建议，
    不会把账户打错。
  * 不预测涨跌。买卖点只有两个来源：配置漂移超过阈值、趋势闸改变目标权重。
    两条路都汇到同一句「目标权重变了多少」，出口只有一个。
"""

import datetime
import json
import os

import config
import strategy

MIN_TRADE = 500.0  # 计划金额小于这个数就不列出来，免得一堆没意义的碎单


def _round_lot(shares, lot):
    return int(shares // lot) * lot


def next_rebalance(today=None):
    """下一个再平衡月的第一个自然日（真正的交易日由人看盘确认）。"""
    t = today or datetime.date.today()
    months = strategy.rebalance_months()
    y = t.year
    for _ in range(3):
        for m in sorted(months):
            d = datetime.date(y, m, 1)
            if d > t:
                return {"date": d.strftime("%Y-%m-%d"), "in_days": (d - t).days,
                        "months": list(months)}
        y += 1
    return {"date": None, "in_days": None, "months": list(months)}


def is_rebalance_month(today=None):
    t = today or datetime.date.today()
    return t.month in strategy.rebalance_months()


def plan(sid=None, prices=None, with_live_price=True, min_trade=MIN_TRADE):
    """产出买卖计划。

    每个桶给三件事：目标金额、当前金额、差额。
    差额为正 → 该买；为负 → 该卖。具体买哪只，优先用你已经持有的那只
    （避免同一个桶越买越碎），没持有就用配置里列的第一只。
    """
    sid = sid or strategy.active_id()
    m = strategy.monitor(sid, with_live_price=with_live_price)
    bk = strategy.buckets()
    lot = strategy.lot()
    fee = strategy.fee_rate()
    cap = m["capital"]
    band = m["band"]

    owned = {}
    for p in m["positions"]:
        owned.setdefault(p.get("bucket"), []).append(p)

    # 总额填得比持仓市值还小的时候，差额全是假的。这种时候给一堆
    # 「买入 3400 万」的计划比不给更危险，所以直接拦掉，先让人去改总额。
    blocked = bool(m.get("capital_manual") and m["invested"] > m["capital"] + 1)

    rows, buys, sells = [], 0.0, 0.0
    for b in m["buckets"]:
        delta = b["delta_value"]
        row = dict(b)
        row["fee"] = 0.0
        row["shares"] = 0
        row["amount"] = 0.0
        row["action"] = "持有"
        row["instrument"] = None
        row["price"] = None
        row["why"] = ""

        if blocked:
            row["action"] = "跳过"
            row["why"] = "可投资总额填错了（小于持仓市值），先改总额再算"
            rows.append(row)
            continue

        if abs(delta) < min_trade:
            row["why"] = "差额太小，不值得动"
            rows.append(row)
            continue

        # 溢价闸：该买但场内太贵的时候不买。买溢价 14% 的纳指 ETF，等于
        # 一进场就亏 14%，等溢价回落或者走场外申购都比硬买强。
        pg = b.get("premium_gate") or {}
        pv = b.get("premium") or {}
        if delta > 0 and pg.get("level") == "block":
            row["action"] = "暂缓买入"
            row["blocked_by_premium"] = True
            row["why"] = pg.get("reason") or "场内溢价过高，暂缓买入"
            alts = b.get("premium_alt") or []
            if alts:
                row["why"] += "；可用 %s（%s）走场外申购" % (
                    alts[0].get("name") or "", alts[0].get("code") or "")
            rows.append(row)
            continue

        held = owned.get(b["key"]) or []
        if delta < 0:
            # 卖：只能卖手上真有的
            if not held:
                row["why"] = "该减但没有持仓，跳过"
                row["action"] = "跳过"
                rows.append(row)
                continue
            target_amt = abs(delta)
            got, got_amt = [], 0.0
            for p in sorted(held, key=lambda x: -x["value"]):
                if got_amt >= target_amt:
                    break
                px = p["price"] or 0
                if px <= 0:
                    continue
                need = target_amt - got_amt
                sh = min(int(p["shares"]), _round_lot(need / px, lot))
                if sh <= 0:
                    continue
                got.append({"code": p["code"], "name": p["name"],
                            "price": px, "shares": sh})
                got_amt += sh * px
            if not got:
                row["why"] = "差额不足一手"
                row["action"] = "跳过"
                rows.append(row)
                continue
            row["action"] = "卖出"
            row["instrument"] = " + ".join(g["name"] for g in got)
            row["price"] = got[0]["price"]
            row["shares"] = sum(g["shares"] for g in got)
            row["amount"] = got_amt
            row["fee"] = got_amt * fee
            row["legs"] = got
            row["why"] = "偏离目标 %.1f%%，超过阈值 %.1f%%" % (
                abs(b["drift"]) * 100, band * 100)
            if pg.get("level") in ("block", "warn"):
                row["why"] += "。注意现在溢价 %.1f%%，溢价卖出反而占便宜" % (
                    float(pv.get("premium") or 0) * 100)
            sells += got_amt
        else:
            pick = held[0] if held else None
            code = pick["code"] if pick else (b["instruments"] or [None])[0]
            if not code:
                row["why"] = "配置里没写可买标的"
                row["action"] = "跳过"
                rows.append(row)
                continue
            px = (prices or {}).get(code)
            if not px and pick:
                px = pick.get("price")
            if not px:
                px = strategy.prices([code]).get(code)
            if not px:
                row["why"] = "取不到 %s 的价格" % code
                row["action"] = "跳过"
                rows.append(row)
                continue
            sh = _round_lot(delta / px, lot)
            if sh <= 0:
                row["why"] = "差额不足一手（%.0f 元 / %.3f）" % (delta, px)
                row["action"] = "跳过"
                row["instrument"] = pick["name"] if pick else code
                row["price"] = px
                rows.append(row)
                continue
            row["action"] = "买入"
            row["instrument"] = pick["name"] if pick else code
            row["instrument_code"] = code
            row["price"] = px
            row["shares"] = sh
            row["amount"] = sh * px
            row["fee"] = sh * px * fee
            row["why"] = "低于目标 %.1f%%，超过阈值 %.1f%%" % (
                abs(b["drift"]) * 100, band * 100)
            buys += sh * px
        rows.append(row)

    order = {"cash": 0, "bond": 1, "gold": 2}
    rows.sort(key=lambda r: (order.get(r["key"], 5), -r["target_w"]))

    src = "实时价" if with_live_price else "成本价"
    notes = ["价格为%s；实际成交以你下单那一刻为准。" % src,
             "最小交易单位 %d 股，费率按 %.2f%% 估。" % (lot, fee * 100)]
    if m["gate"].get("ok"):
        notes.append("趋势闸：H00300 %s vs 200日均线 %.1f（%+.1f%%）→ 闸门%s。"
                     % (m["gate"]["date"], m["gate"]["ma"],
                        m["gate"]["gap"] * 100,
                        "触发，风险腿打折" if m["gate"]["on"] else "未触发"))
    rs = m.get("risk") or {}
    if rs.get("enabled", True):
        notes.append("风险减仓层：%s，风险资产预算 ×%.2f。" %
                     (rs.get("summary") or "正常",
                      float(rs.get("multiplier") or 1.0)))
    prem_rows = [r for r in m.get("buckets") or []
                 if (r.get("premium") or {}).get("premium") is not None
                 and abs((r.get("premium") or {}).get("premium")) >= 0.005]
    if prem_rows:
        notes.append("场内溢价（现价 ÷ IOPV，%s）：%s。"
                     % (m.get("premium_asof") or "实时",
                        "；".join("%s %+.1f%%" % (r["name"],
                                                  r["premium"]["premium"] * 100)
                                  for r in prem_rows)))
    blk = [r for r in rows if r.get("blocked_by_premium")]
    if blk:
        notes.append("有 %d 个桶因溢价过高暂缓买入，钱先留现金，等溢价回落"
                     "或走场外申购，别在场内硬买。" % len(blk))
    if not is_rebalance_month():
        rm = "/".join(map(str, strategy.rebalance_months()))
        if rs.get("active"):
            notes.append("风险减仓层已生效：本月出现的卖出属于「按层减仓」，建议照做；"
                         "回补仓位等 %s 再平衡月再一次性做，避免来回穿越。" % rm)
        else:
            notes.append("本月不是再平衡月（%s），下面只是「漂移超阈值」的提示，"
                         "可以选择等到再平衡月一起做。" % rm)
    if blocked:
        notes.append("⚠️ 计划已停用：持仓市值超过你填的可投资总额，"
                     "现在的差额没有意义。先到右上角把总额改成真实数字。")

    return {
        "ok": True, "id": sid, "name": m["name"], "capital": cap,
        "invested": m["invested"], "cash": m["cash"], "band": band,
        "gate": m["gate"], "gate_used": m["gate_used"],
        "risk": m.get("risk"),
        "risk_used": bool((m.get("risk") or {}).get("active")),
        "premium_blocked": len(blk),
        "rows": rows, "notes": notes,
        "total_buy": buys, "total_sell": sells,
        "net": buys - sells,
        "est_fee": sum(r["fee"] for r in rows),
        "alerts": m["alerts"],
        "unclassified": m["unclassified"],
        "warnings": m.get("warnings") or [],
        "blocked": blocked,
        "next_rebalance": next_rebalance(),
        "is_rebalance_month": is_rebalance_month(),
        "cash_after": cap - (m["invested"] + buys - sells),
        "asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ------------------------------------------------------------------ 落地

def _positions_file():
    return os.path.join(config.BASE_DIR, "positions.json")


def _log_file():
    return os.path.join(config.OUTPUT_DIR, "plan_history.jsonl")


def apply(rows, sid=None, note=""):
    """把计划写进 positions.json。

    只处理买/卖两种动作，跳过的一律不动。买用**当前参考价**当成本记，
    这只是记账起点，实际成本请自己按成交价改（持仓页可以直接双击改）。
    """
    sid = sid or strategy.active_id()
    data = strategy.load_positions()
    pos = data.setdefault("positions", [])
    done = []

    for r in rows or []:
        act = r.get("action")
        if act not in ("买入", "卖出"):
            continue
        key = r.get("key")
        legs = r.get("legs") or ([{"code": r.get("instrument_code"),
                                   "name": r.get("instrument"),
                                   "price": r.get("price"),
                                   "shares": r.get("shares")}]
                                 if act == "买入" else [])
        for leg in legs:
            code = str(leg.get("code") or "").strip()
            if not code:
                continue
            sh = int(leg.get("shares") or 0)
            px = float(leg.get("price") or 0)
            if sh <= 0:
                continue
            hit = next((p for p in pos if str(p.get("code")) == code), None)
            if act == "买入":
                if hit:
                    tot = float(hit.get("cost") or 0) * float(hit.get("shares") or 0)
                    new_sh = int(hit.get("shares") or 0) + sh
                    hit["cost"] = round((tot + px * sh) / new_sh, 4) if new_sh else px
                    hit["shares"] = new_sh
                    hit["bucket"] = key
                else:
                    pos.append({"tx": _tx(code), "code": code,
                                "name": leg.get("name") or code,
                                "cost": round(px, 4), "shares": sh,
                                "bucket": key, "note": "策略 %s 买入" % sid})
                done.append({"action": "买入", "code": code, "shares": sh,
                             "price": px, "bucket": key})
            else:
                if not hit:
                    continue
                hold = int(hit.get("shares") or 0)
                if sh >= hold:
                    pos.remove(hit)
                else:
                    hit["shares"] = hold - sh
                done.append({"action": "卖出", "code": code, "shares": sh,
                             "price": px, "bucket": key})

    data["positions"] = pos
    with open(_positions_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    rec = {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "strategy": sid, "note": note, "trades": done}
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(_log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {"ok": True, "applied": len(done), "trades": done,
            "positions": pos, "log": rec}


def history(limit=50):
    try:
        with open(_log_file(), encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
    except Exception:
        return []
    out = []
    for l in lines[-limit:]:
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out[::-1]


def _tx(code):
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    return ("sh" if c[:1] in ("5", "6", "9", "2") else "sz") + c
