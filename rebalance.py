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
import secure_store
import strategy

MIN_TRADE = 500.0  # 计划金额小于这个数就不列出来，免得一堆没意义的碎单


def _round_lot(shares, lot):
    return int(shares // lot) * lot


def _why_text(drift, band):
    """买卖行的那句「为什么」。别把没超阈值的漂移说成超了。

    计划是按「差额 ≥ MIN_TRADE 就列出来」生成的，所以会出现漂移 4.5%、
    阈值 5% 也给你补一手的情况 —— 那是顺手补齐，不是超阈值。
    """
    if abs(drift) >= band:
        return "偏离目标 %.1f%%，超过阈值 %.1f%%" % (abs(drift) * 100,
                                                    band * 100)
    return ("偏离目标 %.1f%%，没到阈值 %.1f%%（顺手补齐；不想动就只执行标了"
            "「买入 / 卖出」里超阈值的那几行）" % (abs(drift) * 100, band * 100))


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
            if not (owned.get(b["key"]) or []) and not (b.get("target_w") or 0):
                # 目标权重就是 0 的桶（R2 的纳指），写「差额太小」会让人以为
                # 是暂时不动。说清楚：这一版根本不配它。
                row["action"] = "不买"
                row["why"] = "这一档目标权重是 0，本方案不配置"
            else:
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
                if b["key"] == "cash":
                    # 现金桶的「当前金额」是总额减掉持仓市值，本来就没有
                    # 可卖的「持仓」。现金多于目标的意思是少留点现金，
                    # 多出来的那部分差额已经体现在下面各桶的买入计划里，
                    # 写成「该减但没有持仓，跳过」会让人以为哪里算错了。
                    row["why"] = ("现金高于目标：多出来的这部分不用动，"
                                  "差额已经算进下面各桶的买入")
                    row["action"] = "持有"
                else:
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
                            "price": px, "shares": sh,
                            "bucket": b["key"], "paper": bool(p.get("paper"))})
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
            row["why"] = _why_text(b["drift"], band)
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
            row["why"] = _why_text(b["drift"], band)
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
    return config.POS_FILE


def _log_file():
    return os.path.join(config.OUTPUT_DIR, "plan_history.jsonl")


def apply(rows, sid=None, note="", paper=None, to_ledger=True):
    """把计划写进 positions.json，并同步记进现金流台账。

    只处理买/卖两种动作，跳过的一律不动。买用**当前参考价**当成本记，
    这只是记账起点，实际成本请自己按成交价改（持仓页可以直接双击改）。

    为什么要顺手写台账：卖出一笔如果只改 positions.json、不动台账，账上现金
    就少了这一笔回流，后面「我赚了多少钱」会一路错下去，而且错得看不出来。
    所以买入记一笔支出（含手续费）、卖出记一笔收入（扣手续费），
    两边永远一起动。

    paper=None 表示「跟着这只持仓原来的标记走」：卖实验仓位记的就是实验卖出，
    买一只已经标了实验的也继续算实验。要整体转成真实成交用 mark_real()。
    """
    sid = sid or strategy.active_id()
    data = strategy.load_positions()
    pos = data.setdefault("positions", [])
    done, cash_rows = [], []

    for r in rows or []:
        act = r.get("action")
        if act not in ("买入", "卖出"):
            continue
        key = r.get("key")
        row_fee = float(r.get("fee") or 0)
        row_amt = float(r.get("amount") or 0)
        legs = r.get("legs") or ([{"code": r.get("instrument_code"),
                                   "name": r.get("instrument"),
                                   "price": r.get("price"),
                                   "shares": r.get("shares"), "bucket": key,
                                   "paper": r.get("paper")}]
                                 if act == "买入" else [])
        for leg in legs:
            code = str(leg.get("code") or "").strip()
            if not code:
                continue
            sh = int(leg.get("shares") or 0)
            px = float(leg.get("price") or 0)
            if sh <= 0 or px <= 0:
                continue
            hit = next((p for p in pos if str(p.get("code")) == code), None)
            amt = sh * px
            # 手续费按这一腿占整行的比例摊；一行只有一腿时就是全额。
            fee = row_fee * (amt / row_amt) if row_amt else 0.0
            pf = (bool((hit or {}).get("paper", leg.get("paper")))
                  if paper is None else bool(paper))
            if act == "买入":
                if hit:
                    tot = float(hit.get("cost") or 0) * float(hit.get("shares") or 0)
                    new_sh = int(hit.get("shares") or 0) + sh
                    hit["cost"] = round((tot + px * sh) / new_sh, 4) if new_sh else px
                    hit["shares"] = new_sh
                    hit["bucket"] = key
                    hit["paper"] = pf
                else:
                    pos.append({"tx": _tx(code), "code": code,
                                "name": leg.get("name") or code,
                                "cost": round(px, 4), "shares": sh,
                                "bucket": key, "paper": pf,
                                "note": "策略 %s 买入" % sid})
                done.append({"action": "买入", "code": code, "shares": sh,
                             "price": px, "bucket": key, "paper": pf})
            else:
                if not hit:
                    continue
                hold = int(hit.get("shares") or 0)
                if sh >= hold:
                    pos.remove(hit)
                else:
                    hit["shares"] = hold - sh
                done.append({"action": "卖出", "code": code, "shares": sh,
                             "price": px, "bucket": key, "paper": pf})
            cash_rows.append({"kind": act, "amount": round(amt, 2),
                              "fee": round(fee, 2), "code": code,
                              "bucket": leg.get("bucket") or key,
                              "shares": float(sh), "price": px, "paper": pf,
                              "note": note or ("执行计划 %s" % sid)})

    data["positions"] = pos
    with secure_store.open_writer(_positions_file()) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    rec = {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "strategy": sid, "note": note, "trades": done}
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        secure_store.append_line(_log_file(), json.dumps(rec, ensure_ascii=False))
    except Exception:
        pass

    n_led = 0
    if to_ledger and cash_rows:
        try:
            import ledger
            n_led = len(ledger.add_many(cash_rows, journal=False))
        except Exception:
            n_led = 0

    try:
        import journal
        detail = "；".join("%s %s ×%d @%.3f" % (t["action"], t["code"],
                                              t["shares"], t["price"])
                           for t in done) or "没有可执行的行"
        if cash_rows:
            detail += "。已同步记进现金流台账 %d 笔" % n_led
        if note:
            detail += "；备注：%s" % note
        journal.log("执行", "执行买卖计划 %s" % sid, detail=detail,
                    level="act", sid=sid,
                    ref={"n": len(done), "ledger": n_led, "log_at": rec["at"]})
    except Exception:
        pass
    return {"ok": True, "applied": len(done), "trades": done, "ledger": n_led,
            "positions": pos, "log": rec}


def mark_real(note=""):
    """实验仓位 → 真实成交：把 paper 标记去掉，别的什么都不动。

    真下单之后要做的两步是：
      1. 到持仓页把每只的成本价改成**实际成交价**、份额改成**实际成交股数**；
      2. 回来点这个（或者反过来，先点再改，无所谓）。

    它只做一件事：把 positions.json 和台账里的 paper 标记清掉，日志记一笔。
    价格、份额、现金一分不动 —— 那些是事实，不该被这个动作改。
    之所以要清：页面上会一直显示「这是实验仓位，不是真花的钱」，
    不清掉，以后回看根本分不出哪一段是真账。
    """
    data = strategy.load_positions()
    pos = data.get("positions") or []
    n_pos = 0
    for p in pos:
        if p.get("paper"):
            p.pop("paper", None)
            n_pos += 1
    if n_pos:
        with secure_store.open_writer(_positions_file()) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    n_led = 0
    try:
        import ledger
        d = ledger.load()
        for e in d.get("entries") or []:
            if e.get("paper"):
                e["paper"] = False
                n_led += 1
        if n_led:
            ledger.save(d)
    except Exception as e:
        return {"ok": False, "msg": "台账没改成功：%s: %s" % (type(e).__name__, e),
                "positions": n_pos}

    try:
        import journal
        journal.log("建仓", "实验仓位转为真实成交",
                    detail="持仓 %d 笔、台账 %d 笔去掉实验标记%s"
                           % (n_pos, n_led, ("；" + note) if note else ""),
                    level="act")
    except Exception:
        pass
    return {"ok": True, "positions": n_pos, "ledger": n_led,
            "msg": "已把 %d 笔持仓、%d 笔台账标成真实成交" % (n_pos, n_led)}


# ------------------------------------------------------------------ 一键建仓

def seed(sid=None, capital=None, force=False, note="", paper=True, dry=False):
    """一键全部建仓：按当前计划，把每个非现金桶一次买到目标金额。

    给「还没买任何东西」用的入口。账户空的时候逐笔确认没有意义 —— 每一条
    计划都是「买入」，差额就是目标金额本身，点 7 次和点 1 次结果一样。

    一次写三处：
      positions.json        份额 / 成本（账本 = 我实际持有什么）
      output/ledger.json    入金 + 每一笔买入（现金流台账）
      output/journal.jsonl  一条事件
    实验仓位（paper=True）跟真仓位唯一的区别是带一个标记，报告里会写出来。

    跳过的东西必须说清楚，不能默默少买一个桶：现金桶不买（它就是没买的
    那部分）、目标权重是 0 的桶不买、场内溢价被闸门拦住的桶不买。每一条
    都进返回值的 skipped，页面上照着显示。
    """
    sid = sid or strategy.active_id()
    p = plan(sid=sid)
    if not p.get("ok"):
        return {"ok": False, "msg": "计划没算出来：%s" % p.get("msg")}
    if p.get("blocked"):
        return {"ok": False, "msg": "差额不可信：持仓市值超过你填的可投资总额。"
                                    "先去右上角把「总额」改对。"}
    cap = float(capital or p.get("capital") or 0)
    if cap <= 0:
        return {"ok": False, "msg": "先填可投资总额（右上角输入框，或 --capital）。"}

    data = strategy.load_positions()
    exist = [x for x in (data.get("positions") or [])
             if float(x.get("shares") or 0) > 0]
    if exist and not force:
        return {"ok": False, "need_force": True,
                "msg": "账本里已经有 %d 笔持仓，全部建仓会把它们整个替换掉。"
                       "确认就再点一次「覆盖重建」。" % len(exist)}

    bk = strategy.buckets()
    legs, skipped = [], []
    for r in p["rows"]:
        key = r.get("key")
        spec = bk.get(key) or {}
        name = r.get("name") or spec.get("name") or key
        code = (r.get("instrument_code")
                or (spec.get("instruments") or [None])[0])
        tv = float(r.get("target_value") or 0)
        if key == "cash":
            why = "现金桶不买 —— 它就是「还没买」的那部分"
        elif r.get("action") == "买入" and int(r.get("shares") or 0) > 0:
            legs.append({
                "key": key, "name": name, "code": code,
                "price": float(r["price"]), "shares": int(r["shares"]),
                "amount": float(r["amount"]), "fee": float(r.get("fee") or 0),
                "target_w": float(r.get("target_w") or 0),
            })
            continue
        elif r.get("blocked_by_premium"):
            why = r.get("why") or "场内溢价过高，暂缓买入"
        elif tv <= 0:
            why = "本方案这一档目标权重是 0，不配置"
        else:
            why = r.get("why") or "差额不足一手 / 取不到价格"
        skipped.append({"key": key, "name": name, "code": code,
                        "target_value": tv, "why": why})

    if not legs:
        return {"ok": False, "msg": "按现在的计划一笔都不用买。", "skipped": skipped}

    spent = sum(l["amount"] for l in legs)
    fees = sum(l["fee"] for l in legs)
    if spent + fees > cap + 1:
        return {"ok": False, "msg": "要买 ¥%.0f（含费），总额只有 ¥%.0f。"
                                    % (spent + fees, cap)}

    out = {"ok": True, "dry": bool(dry), "sid": sid, "capital": cap,
           "legs": legs, "skipped": skipped, "spent": spent, "fees": fees,
           "cash": cap - spent - fees}
    if dry:
        return out

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag = note or ("一键建仓 %s%s" % (sid, "（实验仓位）" if paper else ""))

    # 盈亏追踪的起点必须在写 positions.json **之前**建出来：建账用的是「计划里
    # 那一批买入」，等账本写满了，同一份计划就只剩「持有」，什么也建不出来。
    # 有了起点，monitor 那边才会开始记日快照（没建账它一条都不肯记）。
    try:
        import tracker
        rep = tracker.report(record=False, json_out=True)
        if rep.get("ok"):
            out["tracker"] = "盈亏追踪已有起点 %s（%s）" % (
                rep.get("start"), rep.get("basis"))
        else:
            r2 = tracker.init(capital=cap, sid=sid)
            out["tracker"] = ("已建盈亏追踪起点 %s" % r2.get("start")
                              if r2.get("ok")
                              else "起点没建起来：%s" % r2.get("msg"))
    except Exception as e:
        out["tracker"] = "盈亏追踪没跑起来：%s: %s" % (type(e).__name__, e)

    pos = []
    for l in legs:
        pos.append({"tx": _tx(l["code"]), "code": l["code"], "name": l["name"],
                    "cost": round(l["price"], 4), "shares": int(l["shares"]),
                    "bucket": l["key"], "note": tag,
                    "paper": bool(paper), "at": stamp})
    with secure_store.open_writer(_positions_file()) as f:
        json.dump({"positions": pos}, f, ensure_ascii=False, indent=2)
    out["positions"] = len(pos)

    # 现金流台账：先把「入金」补齐到可投资总额，再逐笔记买入。
    # 补齐而不是无脑加 —— 重复建仓不会凭空多出一笔本金。
    try:
        import ledger
        t = ledger.totals()
        batch, need = [], cap - float(t.get("net_in") or 0)
        if need > 1:
            batch.append({"kind": "入金", "amount": need, "paper": bool(paper),
                          "note": tag + " · 可投资总额"})
        for l in legs:
            batch.append({"kind": "买入", "amount": l["amount"], "fee": l["fee"],
                          "code": l["code"], "bucket": l["key"],
                          "shares": l["shares"], "price": l["price"],
                          "paper": bool(paper), "note": tag})
        added = ledger.add_many(batch, journal=False)
        out["ledger"] = len(added)
        out["ledger_in"] = round(need, 2) if need > 1 else 0.0
    except Exception as e:
        out["ledger"] = 0
        out["ledger_error"] = "%s: %s" % (type(e).__name__, e)

    try:
        import journal
        detail = ("买入 %d 笔 · 投入 ¥%s · 手续费 ¥%s · 入金 ¥%s · 剩余现金 ¥%s"
                  % (len(legs), format(spent, ",.0f"), format(fees, ",.2f"),
                     format(out.get("ledger_in") or 0, ",.0f"),
                     format(out["cash"], ",.0f")))
        if skipped:
            detail += "；跳过 " + "、".join("%s（%s）" % (s["name"], s["why"])
                                            for s in skipped)
        journal.log("建仓", "一键全部建仓%s" % ("（实验仓位）" if paper else ""),
                    detail=detail, level="act", sid=sid,
                    ref={"codes": [l["code"] for l in legs], "paper": bool(paper),
                         "spent": round(spent, 2)})
    except Exception:
        pass

    out["msg"] = ("已写入 %d 笔%s，投入 ¥%s，现金留 ¥%s"
                  % (len(legs), "实验仓位" if paper else "持仓",
                     format(spent, ",.0f"), format(out["cash"], ",.0f")))
    return out


def history(limit=50):
    try:
        with secure_store.open_reader(_log_file()) as f:
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
