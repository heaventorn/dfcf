# -*- coding: utf-8 -*-
"""策略层：strategy.json 是唯一真相。

分工（这条边界要守住，不然系统会慢慢长歪）：
  positions.json  券商账本 —— 我手上**实际**拿着什么（成本 / 数量）。
  strategy.json   策略蓝图 —— 我**应该**拿着什么（目标权重 / 再平衡规则）。
  两者的差额，就是系统要给出的买卖计划。

本模块只负责把蓝图翻译成机器能用的东西：目标权重、趋势闸状态、
实际持仓对照。它不下单、不算钱，那些在 rebalance.py。
"""

import datetime
import json
import os
import threading
import time

import bars
import config
import risk

FILE = config.STRATEGY_FILE

_lock = threading.Lock()
_cfg = None
_stamp = None

# 现价短缓存（key 是排序后的代码元组）：一次页面加载会连着问两轮价格
_PX_CACHE = {}
_PX_TTL = 10.0


# ------------------------------------------------------------------ 装载

def _stamp_of(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load(force=False):
    """读 strategy.json。改了文件不用重启服务 —— mtime 变了自动重载。"""
    global _cfg, _stamp
    old = _cfg
    with _lock:
        st = _stamp_of(FILE)
        if _cfg is not None and not force and st == _stamp:
            return _cfg
        with open(FILE, encoding="utf-8") as f:
            _cfg = json.load(f)
        _stamp = st
        new = _cfg
    # 换了配置就留一行日志。改权重是这套系统里最该留痕的动作之一 ——
    # 半年后回看「什么时候把黄金从 10% 调到 15%」，只有这一处记得住。
    if old is not None and old is not new:
        try:
            d = describe_change(old, new)
            if d:
                import journal
                journal.log("系统", "strategy.json 改了", detail=d, level="act")
        except Exception:
            pass
    return new


def describe_change(a, b):
    """两份配置差在哪，写成人话。只挑有意义的项，最多列 6 条。"""
    out = []
    bk = {}
    for cfg in (a, b):
        for k, v in (cfg.get("buckets") or {}).items():
            bk.setdefault(k, v.get("name") or k)
    sa, sb = a.get("strategies") or {}, b.get("strategies") or {}
    for sid in sorted(set(sa) | set(sb)):
        na, nb = sa.get(sid) or {}, sb.get(sid) or {}
        for k in sorted(set(na.get("weights") or {}) | set(nb.get("weights") or {})):
            x = float((na.get("weights") or {}).get(k) or 0)
            y = float((nb.get("weights") or {}).get(k) or 0)
            if abs(x - y) > 1e-9:
                out.append("%s 的%s %.2f%% → %.2f%%"
                           % (nb.get("name") or sid, bk.get(k, k), x * 100, y * 100))
    for key, path in (("capital", ("account", "capital")),
                      ("band", ("rebalance", "band")),
                      ("cut", ("gate", "cut")),
                      ("months", ("rebalance", "months")),
                      ("enabled", ("risk_overlay", "enabled"))):
        x = a
        y = b
        for p in path:
            x = (x or {}).get(p) if isinstance(x, dict) else None
            y = (y or {}).get(p) if isinstance(y, dict) else None
        if x != y:
            out.append("%s %s → %s" % ("/".join(path), x, y))
    if len(out) > 6:
        return "；".join(out[:6]) + "；等 %d 项" % (len(out) - 6)
    return "；".join(out)


def reload():
    return load(force=True)


def save(cfg):
    """回写（只用于改 active / capital 这类开关，不用于改策略本身）。"""
    global _cfg, _stamp
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    with _lock:
        _cfg = cfg
        _stamp = _stamp_of(FILE)
    return cfg


# ------------------------------------------------------------------ 读配置

def buckets():
    return load()["buckets"]


def bucket_keys():
    return [k for k in buckets() if not k.startswith("_")]


def strategies():
    return load()["strategies"]


def list_ids():
    """按 risk 从小到大排，页面上的下拉框就是这个顺序。"""
    s = strategies()
    return sorted(s, key=lambda k: (float(s[k].get("risk") or 0), k))


def get(sid):
    s = strategies()
    if sid not in s:
        raise KeyError("没有这个策略：%s" % sid)
    return s[sid]


def cat_of(sid):
    """策略的「类别」标签 —— 只给页面看的，不参与任何计算。

    strategy.json 里给策略写了 cat 就用它；没写就按 risk 分三档，
    免得蓝图里改了个数、页面上还挂着旧的类别名。
    """
    c = get(sid)
    cat = (c.get("cat") or "").strip()
    if cat:
        return cat
    try:
        r = float(c.get("risk") or 0)
    except (TypeError, ValueError):
        r = 0.0
    if r < 0.25:
        return "稳健"
    if r < 0.34:
        return "均衡"
    return "进取"


def active_id():
    a = load().get("active")
    return a if a in strategies() else list_ids()[0]


def set_active(sid):
    if sid not in strategies():
        raise KeyError("没有这个策略：%s" % sid)
    old = active_id()
    cfg = load()
    cfg["active"] = sid
    save(cfg)
    if old != sid:
        try:
            import journal
            journal.log("系统", "切换策略：%s → %s" % (old, sid),
                        detail="%s → %s" % (get(old).get("name") or old,
                                            get(sid).get("name") or sid),
                        sid=sid, level="act")
        except Exception:
            pass
    return sid


def gate_cfg():
    return load().get("gate") or {}


def risk_cfg():
    import risk
    return risk.normalize(load().get("risk_overlay") or {})


def rebalance_months():
    return tuple((load().get("rebalance") or {}).get("months") or (1, 7))


def band():
    return float((load().get("rebalance") or {}).get("band") or 0.05)


def lot():
    return int((load().get("trade") or {}).get("lot") or 100)


def fee_rate():
    return float((load().get("trade") or {}).get("fee_rate") or 0.0)


def account():
    acc = dict(load().get("account") or {})
    acc["capital"] = float(acc.get("capital") or 0)
    acc["cash_manual"] = float(acc.get("cash_manual") or 0)
    return acc


def llm_cfg():
    return load().get("llm") or {}


# ------------------------------------------------------------------ 目标权重

def target_weights(sid, gate_on=None, risk_state=None):
    """某个策略的目标权重。

    gate_on 为 None 时按「闸门关着」算，也就是策略的设计权重；
    显式传 True/False 才切换趋势闸。回测里由 backtest 自己按日期逐次决定，
    这里主要给实盘盯盘用（当前这一刻闸门是开还是关）。

    risk_state 是 risk.state() 的结果，会在趋势闸之后再执行空中飞人 /
    H00300 回撤的分层减仓。
    """
    w = dict(get(sid).get("weights") or {})
    total = sum(w.values())
    if total > 0:
        w = {k: v / total for k, v in w.items()}
    if get(sid).get("gate") and gate_on:
        g = gate_cfg()
        keys = g.get("risk_buckets") or [k for k in w if k != "cash"]
        cut = float(g.get("cut") or 0.5)
        total_risk = sum(v for k, v in w.items() if k in keys)
        if total_risk > 1e-9:
            keep = total_risk * (1.0 - cut)
            for k in keys:
                if k in w:
                    w[k] *= keep / total_risk
            w["cash"] = w.get("cash", 0.0) + (total_risk - keep)
    if risk_state and risk_state.get("enabled", True):
        w = risk.apply_weights(w, risk_state, cfg=risk_cfg())
    return {k: v for k, v in w.items() if v > 1e-9}


def gate_state(with_live=False):
    """当前趋势闸：H00300（沪深300全收益）收盘 vs 200 日均线。

    on=True 表示「跌破均线、闸门触发」，风险资产目标权重按 cut 打折。
    回测里闸门只在再平衡日看一次；这里每天都算，是为了让盯盘页面能提前
    看到「再平衡日大概会怎么处理」，不是每天都要动手。
    """
    g = gate_cfg()
    window = int(g.get("window") or 200)
    spec = g.get("proxy") or {"src": "csi", "code": "H00300"}
    src = spec.get("code") or "H00300"
    ser = bars.proxy_series(spec)
    if not ser:
        return {"ok": False, "msg": "取不到 %s 的日线" % src}
    days = sorted(ser)
    vals = [ser[d] for d in days]
    if len(vals) < window:
        return {"ok": False, "msg": "样本不够 %d 天" % window}
    ma = sum(vals[-window:]) / float(window)
    close = vals[-1]
    dd_window = int((risk_cfg().get("drawdown") or {}).get("window") or 252)
    segment = vals[-dd_window:] if len(vals) >= dd_window else vals
    high = max(segment) if segment else close
    dd = close / high - 1 if high else None
    return {
        "ok": True, "on": close < ma, "close": close, "ma": ma,
        "gap": close / ma - 1, "date": days[-1], "window": window,
        "cut": float(g.get("cut") or 0.5), "index": src,
        "high": high, "dd": dd, "dd_window": dd_window,
        "live": False, "asof_live": None,
    }


def risk_state(gw=None):
    """当前分层减仓状态：空中飞人 + H00300 回撤。"""
    cfg = risk_cfg()
    snap = risk.load_airman()
    score = snap.get("total")
    dd = (gw or {}).get("dd")
    trend_on = bool((gw or {}).get("on")) if (gw or {}).get("ok") else None
    trend_cut = (gw or {}).get("cut") if (gw or {}).get("ok") else None
    st = risk.state(score=score, dd=dd, cfg=cfg,
                    trend_on=trend_on, trend_cut=trend_cut)
    st["airman"] = {
        "score": None if score is None else float(score),
        "level": snap.get("level"),
        "at": snap.get("at"),
    }
    st["drawdown"] = {
        "dd": None if dd is None else float(dd),
        "high": (gw or {}).get("high"),
        "window": (gw or {}).get("dd_window"),
    }
    return st


# ------------------------------------------------------------------ 账本

def _positions_file():
    return config.POS_FILE


def load_positions():
    try:
        with open(_positions_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"positions": []}


def prices(codes):
    """批量取现价。实时接口一把抓，失败退回最后一根日线收盘。

    带 10 秒短缓存：翻一次页面会连着问两轮（配置对照 + 买卖计划），
    不去重就等于把上游打两遍。10 秒对看仓位来说完全够用。
    """
    codes = [c for c in codes if c]
    out = {}
    if not codes:
        return out
    ck = tuple(sorted(set(codes)))
    now = time.time()
    with _lock:
        hit = _PX_CACHE.get(ck)
    if hit and now - hit[0] < _PX_TTL:
        return dict(hit[1])
    try:
        import sources
        tx_codes = [bars.tx_code(c) for c in codes]
        q = sources.get_realtime_quotes(tx_codes) or {}
        for tx, v in q.items():
            if v and v.get("price"):
                out[tx[-6:]] = float(v["price"])
    except Exception:
        pass
    for c in codes:
        if c not in out:
            p = bars.last_price(c)
            if p:
                out[c] = p
    if out:
        with _lock:
            _PX_CACHE[ck] = (now, dict(out))
    return out


def account_view(with_live_price=True):
    """把账本折算成「每个桶现在实际占多少」。

    现金口径：capital 是家庭可投资总额（手动填），
    没买成资产的那部分就是现金。capital=0 时退回「持仓市值 + cash_manual」。
    """
    acc = account()
    data = load_positions()
    pos = data.get("positions") or []
    px = prices([p.get("code") for p in pos]) if with_live_price else {}

    rows, mv_total = [], 0.0
    bk = buckets()
    by_bucket = {k: 0.0 for k in bk if not k.startswith("_")}
    unclassified = []
    for p in pos:
        code = str(p.get("code") or "").strip()
        shares = float(p.get("shares") or 0)
        price = px.get(code) or float(p.get("cost") or 0)
        mv = price * shares
        mv_total += mv
        key = p.get("bucket")
        row = {"code": code, "name": p.get("name") or code,
               "shares": shares, "price": price, "cost": float(p.get("cost") or 0),
               "value": mv, "bucket": key, "px_live": code in px,
               "paper": bool(p.get("paper"))}
        rows.append(row)
        if key in by_bucket:
            by_bucket[key] += mv
        else:
            unclassified.append(row)

    capital = acc["capital"] or (mv_total + acc["cash_manual"])
    cash = capital - mv_total
    by_bucket["cash"] = by_bucket.get("cash", 0.0) + max(cash, 0.0)
    if cash < 0:
        # 市值超过填写的总额：说明 capital 填小了，标出来而不是硬算
        by_bucket["cash"] = by_bucket.get("cash", 0.0) + cash

    return {"capital": capital, "invested": mv_total, "cash": cash,
            "cash_manual": acc["cash_manual"], "buckets": by_bucket,
            "positions": rows, "unclassified": unclassified,
            "capital_manual": acc["capital"]}


# ------------------------------------------------------------------ 买卖评级

RATING_TEXT = {"buy": "买入", "hold": "不动", "sell": "卖出"}


def rating_of(row, band_value, gate_on=None, risk=None, risk_keys=()):
    """买卖评级：只有**买入 / 不动 / 卖出**三档。

    刻意不用任何预测涨跌的技术指标。只用三件已经算出来的事实：
      1. 差额 —— 这个桶离目标还差多少钱（超过 band 才动）
      2. 溢价 —— 该买的时候，场内价是不是已经贵出格（贵就先别追）
      3. 闸门 / 分层减仓 —— 风控正开着的时候，风险腿不再加

    好处是每一档都能指着依据说话：给的是「现在该不该动」，不是「以后
    会涨还是会跌」。评级变了，一定是因为这三样里有一样变了。
    """
    dv = float(row.get("delta_value") or 0.0)
    key = row.get("key")
    cash = (key == "cash")
    gate = (row.get("premium_gate") or {}).get("level")
    prem = (row.get("premium") or {}).get("premium")
    rs = risk or {}
    cut = bool(gate_on) or bool(rs.get("active"))   # 闸门或分层减仓在管着

    if band_value and dv > band_value:
        lvl, why = "buy", "低于目标 ¥%.0f" % dv
    elif band_value and dv < -band_value:
        lvl, why = "sell", "超出目标 ¥%.0f" % abs(dv)
    else:
        lvl, why = "hold", "差额在 ±¥%.0f 以内" % band_value

    if lvl == "buy" and gate == "block":
        lvl = "hold"
        why = ("该买，但场内溢价 %s 超过上限，先别在场内追"
               % ("%.1f%%" % (prem * 100) if prem is not None else "过高"))
    if lvl == "buy" and cut and key in risk_keys:
        lvl = "hold"
        why = "该买，但闸门/减仓开着，风险腿先不加"
    if lvl == "sell" and cash:
        why = "现金超配 ¥%.0f，该投出去" % abs(dv)
    if lvl == "buy" and cash:
        why = "现金低于目标 ¥%.0f，等卖出回笼补上" % dv
    if lvl == "sell" and gate == "block":
        why += "；溢价高，卖反而划算"
    return {"level": lvl, "text": RATING_TEXT[lvl], "why": why}


def monitor(sid=None, with_live_price=True):
    """目标 vs 实际。漂移超过 band 的桶会被标出来。"""
    sid = sid or active_id()
    view = account_view(with_live_price)
    cap = view["capital"] or 0.0
    gw = gate_state(with_live=with_live_price)
    gate_on = gw.get("on") if get(sid).get("gate") else None
    rs = risk_state(gw)
    tw = target_weights(sid, gate_on=gate_on, risk_state=rs)
    bd = band()
    bk = buckets()

    rows = []
    for key, cfg in bk.items():
        if key.startswith("_"):
            continue
        t = tw.get(key, 0.0)
        actual = view["buckets"].get(key, 0.0)
        a = (actual / cap) if cap else 0.0
        drift = a - t
        rows.append({
            "key": key, "name": cfg.get("name") or key,
            "group": cfg.get("group") or "", "note": cfg.get("note") or "",
            "instruments": cfg.get("instruments") or [],
            "target_w": t, "actual_w": a, "drift": drift,
            "target_value": t * cap, "actual_value": actual,
            "delta_value": t * cap - actual,
            "over_band": abs(drift) > bd,
        })
    rows.sort(key=lambda r: -r["target_w"])

    # 溢价：场内 ETF 的成交价可能明显高于它背后的净值，纳指 QDII 尤甚。
    # 这一块只改「怎么买」和「要不要现在买」，不改目标权重。
    premium_asof, premium_warm, premium_error = None, False, None
    warnings_pre = []
    try:
        import premium as prem
        codes, per_bucket = [], {}
        for r in rows:
            cfgb = bk.get(r["key"]) or {}
            ins = [c for c in (cfgb.get("instruments") or []) if c]
            per_bucket[r["key"]] = list(ins)
            codes.extend(ins)
        for p in view["positions"]:
            c = p.get("code")
            if not c:
                continue
            codes.append(c)
            if p.get("bucket") in per_bucket:
                per_bucket[p["bucket"]].append(c)
        snaps = prem.attach(codes)
        premium_asof = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for r in rows:
            cfgb = bk.get(r["key"]) or {}
            pc = cfgb.get("premium") or {}
            warn = float(pc.get("warn") or 0.01)
            block = float(pc.get("block") or 0.03)
            held = [p for p in view["positions"] if p.get("bucket") == r["key"]]
            items, nav_value = [], 0.0
            for c in dict.fromkeys(per_bucket.get(r["key"]) or []):
                s = snaps.get(c)
                if not s:
                    continue
                item = dict(s)
                item["held"] = any(p.get("code") == c for p in held)
                item["gate"] = prem.gate(s, warn, block)
                items.append(item)
                if item["held"] and s.get("premium") is not None:
                    v = sum(p["value"] for p in held if p.get("code") == c)
                    nav_value += v / (1.0 + float(s["premium"]))
            if not items:
                r["premium"] = None
                r["premium_gate"] = {"level": "unknown",
                                     "reason": "没取到净值，按平价处理"}
                r["premium_items"] = []
                continue
            primary = next((i for i in items if i["held"]), items[0])
            r["premium"] = {k: v for k, v in primary.items() if k != "gate"}
            r["premium_gate"] = primary["gate"]
            r["premium_items"] = items
            r["premium_alt"] = pc.get("alt") or []
            if nav_value:
                r["nav_value"] = nav_value
                r["nav_gap"] = r["actual_value"] - nav_value
        for r in rows:
            if (r.get("premium_gate") or {}).get("level") == "block":
                warnings_pre.append(r)
        premium_warm = any((i.get("stats") or {}).get("n")
                           for r in rows for i in (r.get("premium_items") or []))
    except Exception as e:
        premium_error = "%s: %s" % (type(e).__name__, e)
        for r in rows:
            r.setdefault("premium", None)
            r.setdefault("premium_gate", {"level": "unknown",
                                          "reason": "溢价模块不可用"})
            r.setdefault("premium_items", [])

    # 两种会让「差额」彻底失真的情况，必须说出来而不是默默算出一个大数字
    warnings = []
    for r in warnings_pre:
        g = r.get("premium_gate") or {}
        alt = r.get("premium_alt") or []
        tip = ""
        if alt:
            tip = " 替代：%s。" % "、".join(
                "%s(%s)" % (a.get("name"), a.get("code")) for a in alt[:3])
        warnings.append("%s腿：%s%s"
                        % (r.get("name") or r.get("key"),
                           g.get("reason") or "溢价过高，别买", tip))
    if view["capital_manual"] and view["invested"] > view["capital"] + 1:
        warnings.append(
            "持仓市值 ¥%.0f 已经超过你填的可投资总额 ¥%.0f（差 ¥%.0f）。"
            "capital 填小了，下面的目标和差额全部不准 —— 先去上面把总额改对。"
            % (view["invested"], view["capital"],
               view["invested"] - view["capital"]))
    if cap and view["unclassified"]:
        uv = sum(p["value"] for p in view["unclassified"])
        warnings.append(
            "有 ¥%.0f（占总额 %.1f%%）的持仓没归到任何资产桶，不参与配置计算。"
            "要么在持仓页给它们指定桶，要么把这块从 capital 里扣掉 —— "
            "否则它们会一直显得「这批钱凭空不见了」。"
            % (uv, uv / cap * 100))
    if not view["capital_manual"] and not view["positions"]:
        warnings.append("还没填可投资总额，也没录持仓。先在右上角填一个数。")

    # 建仓以来的盈亏 / 最大回撤：拿到各腿的复权净值现算一遍，不落盘（见
    # tracker.bucket_stats 的注释）。取不到就留空，不让整张表跟着挂掉。
    since, since_error = None, None
    try:
        import tracker
        since = tracker.bucket_stats()
        if not since.get("ok"):
            since_error, since = since.get("msg"), None
    except Exception as e:
        since_error = "%s: %s" % (type(e).__name__, e)

    band_value = bd * cap
    rk = gate_cfg().get("risk_buckets") or []
    for r in rows:
        r["since"] = ((since or {}).get("buckets") or {}).get(r["key"])
        r["rating"] = rating_of(r, band_value, gate_on, rs, rk)

    return {
        "ok": True, "id": sid, "name": get(sid).get("name") or sid,
        "capital": cap, "invested": view["invested"], "cash": view["cash"],
        "capital_manual": view["capital_manual"],
        "band": bd, "gate": gw, "gate_used": gate_on, "risk": rs,
        "band_value": band_value, "since": since, "since_error": since_error,
        "premium_asof": premium_asof, "premium_warm": premium_warm,
        "premium_error": premium_error,
        "buckets": rows, "unclassified": view["unclassified"],
        "positions": view["positions"],
        "alerts": [r for r in rows if r["over_band"]],
        "warnings": warnings,
        "active": active_id(),
    }


# ------------------------------------------------------------------ 建仓说明书

def books(capital=None):
    """三套策略各自的建仓说明书：设计权重 / 现在目标 / 目标金额 / 标的。

    和 monitor() 的分工要说清楚：
      monitor 回答「我这套现在偏了多少」—— 只有当前激活的那套说得通，
              另外两套没有持仓，差额全是假的（都等于目标金额）。
      这里   回答「这套方案本来该买什么、买多少」—— 所以不碰持仓、不拉
              行情，三套给的是同一份口径，切页面切到哪套都不变。

    两个权重的区别：
      设计 = strategy.json 里写的权重，是方案本来的样子。
      现在 = 设计权重经趋势闸 / 分层减仓压缩之后，今天真正该用的权重。
             闸门关着、风险正常的时候，两个数一模一样。
    """
    if capital is None:
        capital = account().get("capital") or 0.0
    cap = float(capital or 0.0)
    gw = gate_state()
    rs = risk_state(gw)
    bd = band()
    bk = buckets()
    risk_keys = gate_cfg().get("risk_buckets") or []

    out = []
    for sid in list_ids():
        cfg = get(sid)
        gate_on = gw.get("on") if cfg.get("gate") else None
        design = target_weights(sid, gate_on=False, risk_state=None)
        now = target_weights(sid, gate_on=gate_on, risk_state=rs)
        rows = []
        for key, bcfg in bk.items():
            if key.startswith("_"):
                continue
            d = float(design.get(key) or 0.0)
            n = float(now.get(key) or 0.0)
            pc = bcfg.get("premium") or {}
            rows.append({
                "key": key, "name": bcfg.get("name") or key,
                "group": bcfg.get("group") or "",
                "note": bcfg.get("note") or "",
                "instruments": bcfg.get("instruments") or [],
                "alt": pc.get("alt") or [],
                "design_w": d, "now_w": n,
                "design_value": d * cap, "target_value": n * cap,
            })
        rows.sort(key=lambda r: (-r["now_w"], -r["design_w"]))
        out.append({
            "id": sid, "name": cfg.get("name") or sid,
            "cat": cfg.get("cat") or "", "risk": cfg.get("risk"),
            "gate": bool(cfg.get("gate")), "gate_on": gate_on,
            "rows": rows,
            "total": sum(r["target_value"] for r in rows),
            "risk_w": sum(r["now_w"] for r in rows if r["key"] in risk_keys),
        })

    # 页面按「稳健 → 均衡 → 进取」排。list_ids() 是按 risk 数值排的，
    # 那个顺序是给下拉框用的，拿来当说明书目录会颠三倒四。
    order = {"稳健": 0, "均衡": 1, "进取": 2}
    out.sort(key=lambda b: (order.get(b["cat"], 9), b["id"]))

    return {
        "ok": True, "capital": cap, "band": bd, "band_value": bd * cap,
        "gate": {"ok": gw.get("ok"), "on": gw.get("on"), "date": gw.get("date"),
                 "gap": gw.get("gap"), "window": gw.get("window"),
                 "index": gw.get("index"), "cut": gw.get("cut")},
        "risk": {"enabled": rs.get("enabled"), "active": rs.get("active"),
                 "multiplier": rs.get("multiplier"),
                 "summary": rs.get("summary")},
        "risk_keys": risk_keys,
        "books": out,
        "asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
