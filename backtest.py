# -*- coding: utf-8 -*-
"""回测引擎：读 strategy.json 的权重，用 bars.py 的代理序列算净值。

一条硬规矩：**回测和实盘读同一份 strategy.json**。
策略参数只写在一个地方，回测跑出来的配置就是实盘要执行的配置，
不存在「纸上跑的是 A，手上做的是 B」这种漂移。想改策略改 json，
改完回测数字自己会跟着变。

口径（与早期 _bt_tmp/bt_long.py 完全一致，可以对数验证）：
  * 样本 2006-01-04 起，取所有代理都有值之后的日子
  * 半年再平衡（默认 1 月 / 7 月的第一个交易日），每次把权重拉回目标
  * 趋势闸只在再平衡日看：沪深300 收盘跌破 200 日均线，风险资产目标权重砍半，
    砍掉的部分进货币
  * 不含交易成本，费用单独用 drag 参数模拟（默认 0，页面可加 0.45%/年）
"""

import datetime
import json
import os
import threading

import bars
import config
import risk

START = "20060104"
CACHE_FILE = os.path.join(config.OUTPUT_DIR, "backtest_cache.json")

_lock = threading.Lock()
_memo = {}


def _d(s):
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ------------------------------------------------------------------ 面板

def bucket_series_map():
    """{bucket: {日期: 净值}}，按 strategy.json 里的 proxy 取。"""
    import strategy as st
    out = {}
    for key, cfg in st.buckets().items():
        out[key] = bars.proxy_series(cfg.get("proxy"))
    return out


def panel(start=START, end=None):
    """对齐成一张宽表。

    交易日并集，前值填充：黄金 / 美股这些代理的假期和 A 股不同，
    缺的那天用最近一次收盘顶上，避免某些腿被凭空算成 0。
    返回 (dates, {bucket: [值...]})。
    """
    ser = bucket_series_map()
    if any(not v for v in ser.values()):
        # 有腿是空的（首次运行 / 缓存过期）：并发拉一遍再重来，
        # 否则后面 run_nav 会拿不到列、整条策略算不出来
        import strategy as st
        bars.warm([c.get("proxy") for c in st.buckets().values()
                   if c.get("proxy")])
        ser = bucket_series_map()
    keys = [k for k, v in ser.items() if v]
    if not keys:
        return [], {}
    all_days = sorted(set().union(*[set(ser[k]) for k in keys]))
    all_days = [d for d in all_days
                if d >= start and (end is None or d <= end)]
    last = dict.fromkeys(keys)
    dates, cols = [], {k: [] for k in keys}
    for d in all_days:
        for k in keys:
            v = ser[k].get(d)
            if v is not None:
                last[k] = v
        if all(last[k] is not None for k in keys):
            dates.append(d)
            for k in keys:
                cols[k].append(last[k])
    return dates, cols


_PANEL_CACHE = {}


def _panel():
    if not _PANEL_CACHE:
        d, c = panel()
        _PANEL_CACHE["dates"], _PANEL_CACHE["cols"] = d, c
    return _PANEL_CACHE["dates"], _PANEL_CACHE["cols"]


# ------------------------------------------------------------------ 净值

def _ma(values, window):
    """滚动均线，前 window-1 个位置为 None。"""
    out, acc = [None] * len(values), 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        if i >= window - 1:
            out[i] = acc / window
    return out


def _drawdowns(values, window):
    """滚动 window 日高点的回撤序列。"""
    out = [None] * len(values)
    for i, v in enumerate(values):
        if v is None:
            continue
        lo = max(0, i - window + 1)
        high = max(x for x in values[lo:i + 1] if x is not None)
        if high:
            out[i] = v / high - 1
    return out


def _prem_asof(series, d):
    """取 <= d 的最近一个溢价值；没有就返回 None（不拦）。"""
    if not series:
        return None
    ds = [x for x in series if x <= d]
    return series[max(ds)] if ds else None


def effective_weights(weights, gate, cut, risk_buckets):
    """把趋势闸作用在目标权重上：跌破均线就把风险腿砍掉 cut 比例，转去货币。

    闸门只改「目标权重」，不直接下单 —— 买卖永远由再平衡那一套统一出，
    否则两条路径（配置漂移 / 择时）会互相打架，仓位对不上账。
    """
    if not gate:
        return dict(weights)
    w = dict(weights)
    total = sum(v for k, v in w.items() if k in risk_buckets)
    if total <= 1e-9:
        return w
    keep = total * (1.0 - cut)
    for k in risk_buckets:
        if k in w:
            w[k] *= keep / total
    w["cash"] = w.get("cash", 0.0) + (total - keep)
    return w


def run_nav(weights, gate=False, drag=0.0, months=(1, 7), gate_proxy=None,
            window=200, cut=0.5, risk_buckets=None, notes=None,
            risk_overlay=None, airman_series=None, stats=None,
            prem_series=None, prem_blocks=None):
    """跑一条净值曲线。weights 会被归一化（和不能不为 1，但要容忍手抖）。"""
    dates, cols = _panel()
    if not dates:
        return []
    s = sum(weights.values())
    w = {k: v / s for k, v in weights.items() if v > 1e-9}
    missing = [k for k in w if k not in cols]
    if missing:
        # 某条腿的代理一支都没取到。硬撑着算会得出一个假的漂亮曲线，
        # 所以把它剔掉、按剩下的重新归一，并在结果里明确标出来。
        if notes is not None:
            notes.append("缺少代理数据，已剔除：%s" % "、".join(missing))
        w = {k: v for k, v in w.items() if k in cols}
        if not w:
            return []
        s = sum(w.values())
        w = {k: v / s for k, v in w.items()}
    risk_buckets = list(risk_buckets or [k for k in w if k != "cash"])

    ma = None
    gate_filled = None
    if gate or (risk_overlay and risk_overlay.get("enabled", True)):
        gp = gate_proxy or {"src": "csi", "code": "H00300"}
        gser = bars.proxy_series(gp) or cols.get("broad")
        if isinstance(gser, dict):
            gvals = [gser.get(d) for d in dates]
            # 闸门指数缺某天就用上一次的值（和面板同样的前值填充规则）
            filled, prev = [], None
            for v in gvals:
                if v is None:
                    v = prev
                else:
                    prev = v
                filled.append(v)
            if all(v is not None for v in filled):
                ma = _ma(filled, window)
                gate_filled = filled

    dd_series = None
    if risk_overlay and risk_overlay.get("enabled", True) and gate_filled:
        dd_cfg = risk.normalize(risk_overlay).get("drawdown") or {}
        if dd_cfg.get("enabled", True):
            dd_series = _drawdowns(gate_filled, int(dd_cfg.get("window") or 252))

    air_days = sorted(airman_series or {})
    air_pos = -1
    if risk_overlay and risk_overlay.get("enabled", True) and not air_days and notes is not None:
        notes.append("空中飞人历史序列为空，本次回测只应用趋势闸与 H00300 回撤层。")

    rc = risk.normalize(risk_overlay) if risk_overlay else {}
    min_step = float(rc.get("min_step") or 0.0)
    cut_anytime = bool(rc.get("cut_anytime", True))
    add_at_rebalance_only = bool(rc.get("add_at_rebalance_only", True))
    prem_series = prem_series or {}
    prem_blocks = prem_blocks or {}

    shares = {k: w[k] / cols[k][0] for k in w}
    navs, prev_m, prev_risk = [], None, None
    gate_on_state = False
    for i, d in enumerate(dates):
        m = d[4:6]
        month_rolled = bool(i and m != prev_m)
        rolled = bool(month_rolled and int(m) in months)
        can_act = not (gate and (ma is None or ma[i] is None))
        if month_rolled:
            while air_pos + 1 < len(air_days) and air_days[air_pos + 1] <= d:
                air_pos += 1
            score = airman_series.get(air_days[air_pos]) if air_pos >= 0 else None
            if rolled:
                gate_on_state = bool(gate and ma and ma[i] is not None
                                     and gate_filled[i] < ma[i])
            rs = None
            if risk_overlay and risk_overlay.get("enabled", True):
                rs = risk.state(score=score,
                                dd=(dd_series[i] if dd_series else None),
                                cfg=risk_overlay, trend_on=gate_on_state,
                                trend_cut=cut)
            rsig = round(float(rs.get("multiplier") or 1.0), 6) if rs else None
            diff = (rsig - prev_risk) if (rsig is not None and prev_risk is not None) else 0.0
            risk_cut = bool(diff <= -min_step and cut_anytime)
            risk_add = bool(diff >= min_step and not add_at_rebalance_only)
            risk_changed = risk_cut or risk_add
            if can_act and (rolled or risk_changed):
                if stats is not None:
                    stats["actions"] = int(stats.get("actions") or 0) + 1
                    if risk_changed and not rolled:
                        stats["risk_actions"] = int(stats.get("risk_actions") or 0) + 1
                        if risk_cut:
                            stats["risk_cuts"] = int(stats.get("risk_cuts") or 0) + 1
                        else:
                            stats["risk_adds"] = int(stats.get("risk_adds") or 0) + 1
                weff = w
                if gate_on_state:
                    weff = effective_weights(w, True, cut, risk_buckets)
                if rs and rs.get("enabled", True):
                    weff = risk.apply_weights(weff, rs, cfg=risk_overlay)
                tot = sum(shares[k] * cols[k][i] for k in w)
                # 溢价纪律：该买、但场内正溢价超过上限时不买，钱留在现金腿。
                # 不这样做，回测等于假设你每次都能按净值买到纳指 ETF。
                skipped = 0.0
                for k in w:
                    target_v = tot * weff[k]
                    cur_v = shares[k] * cols[k][i]
                    if k in prem_blocks and target_v > cur_v + 1e-9:
                        p = _prem_asof(prem_series.get(k), d)
                        if p is not None and p > prem_blocks[k]:
                            skipped += target_v - cur_v
                            target_v = cur_v
                            if stats is not None:
                                stats["premium_skips"] = int(
                                    stats.get("premium_skips") or 0) + 1
                    shares[k] = target_v / cols[k][i]
                if skipped and "cash" in shares:
                    shares["cash"] += skipped / cols["cash"][i]
            # 只有真正执行过的信号才更新基准；未执行的加仓信号被锁住，等半年再平衡再说。
            if can_act and (rolled or risk_changed):
                prev_risk = rsig
        prev_m = m
        navs.append(sum(shares[k] * cols[k][i] for k in w))
    if drag:
        navs = [navs[i] * (1 - drag) ** (i / 252.0) for i in range(len(navs))]
    return navs


# ------------------------------------------------------------------ 指标

def _mdd(navs, dates):
    peak, mdd, at, start = navs[0], 0.0, dates[0], dates[0]
    peak_at = dates[0]
    for i, v in enumerate(navs):
        if v > peak:
            peak, peak_at = v, dates[i]
        dd = v / peak - 1
        if dd < mdd:
            mdd, at, start = dd, dates[i], peak_at
    return mdd, at, start


def _underwater(navs, dates):
    """最长回本时间（年）：从水下到重新创新高，取最长的一段。"""
    peak, peak_i, longest = navs[0], 0, 0
    for i, v in enumerate(navs):
        if v >= peak:
            longest = max(longest, i - peak_i)
            peak, peak_i = v, i
    longest = max(longest, len(navs) - 1 - peak_i)
    return longest / 252.0


def metrics(navs, dates=None):
    dates = dates or _panel()[0]
    if len(navs) < 2:
        return {}
    yrs = (_d(dates[-1]) - _d(dates[0])).days / 365.25
    cagr = navs[-1] ** (1 / yrs) - 1
    mdd, at, from_ = _mdd(navs, dates)
    rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    mu = sum(rets) / len(rets)
    vol = (sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * 252 ** 0.5
    w3 = None
    if len(navs) > 756:
        w3 = min((navs[i + 756] / navs[i]) ** (1 / 3) - 1
                 for i in range(len(navs) - 756))
    yr = _yearly(navs, dates)
    return {
        "cagr": cagr, "mdd": mdd, "mdd_at": at, "mdd_from": from_,
        "vol": vol, "sharpe": (cagr / vol) if vol else 0.0,
        "under_years": _underwater(navs, dates),
        "worst_3y": w3, "worst_year": min(yr.values()) if yr else None,
        "years": yrs, "start": dates[0], "end": dates[-1],
        "nav": navs[-1], "yearly": yr,
    }


def _yearly(navs, dates):
    buckets = {}
    for d, v in zip(dates, navs):
        y = d[:4]
        if y not in buckets:
            buckets[y] = [v, v]
        buckets[y][1] = v
    out, prev = {}, None
    for y in sorted(buckets):
        base = prev if prev is not None else buckets[y][0]
        if prev is not None:
            out[y] = buckets[y][1] / base - 1
        prev = buckets[y][1]
    return out


def window_stats(navs, dates, lo, hi):
    """某个危机窗口内的区间收益与最大回撤。"""
    idx = [i for i, d in enumerate(dates) if lo <= d <= hi]
    if len(idx) < 3:
        return None
    seg = [navs[i] for i in idx]
    peak, mdd = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"ret": seg[-1] / seg[0] - 1, "mdd": mdd,
            "from": dates[idx[0]], "to": dates[idx[-1]]}


CRISES = [
    ("2008 全球金融危机", "20071016", "20081104"),
    ("2008-09 见底全程", "20071016", "20090309"),
    ("2015 股灾", "20150612", "20160128"),
    ("2018 熊市", "20180124", "20181228"),
    ("2020 疫情", "20200113", "20200323"),
    ("2022 全球加息", "20211101", "20221031"),
]


# ------------------------------------------------------------------ 上层

def _sig():
    """数据指纹：起始日 + 末日 + 天数。数据没变就不重跑。"""
    dates, cols = _panel()
    if not dates:
        return "empty"
    return "%s-%s-%d" % (dates[0], dates[-1], len(dates))


def run(sid, drag=0.0):
    """跑单个策略，返回 (navs, dates, meta)。结果按 (id, drag) 记忆。"""
    import strategy as st
    cfg = st.get(sid)
    key = (sid, round(float(drag), 6), _sig())
    with _lock:
        if key in _memo:
            return _memo[key]
    notes = []
    gate = bool(cfg.get("gate"))
    g = st.gate_cfg()
    overlay = st.risk_cfg() if cfg.get("risk_overlay", True) else None
    if overlay is not None and not overlay.get("enabled", True):
        overlay = None
    air = risk.load_airman_series() if overlay else {}
    prem_series, prem_blocks = {}, {}
    try:
        import premium as pm
        for key, cfgb in st.buckets().items():
            pc = cfgb.get("premium") or {}
            ins = [c for c in (cfgb.get("instruments") or []) if c]
            if not pc or not ins or not pm.is_on_exchange(ins[0]):
                continue
            s = pm.series(ins[0])          # 只读缓存，不在这里抓数据
            if s:
                prem_series[key] = s
                prem_blocks[key] = float(pc.get("block") or 0.03)
    except Exception:
        prem_series, prem_blocks = {}, {}
    if prem_series:
        notes.append("溢价纪律已启用：%s 的场内溢价超过阈值时不买入，钱留现金腿。"
                     % "、".join(sorted(prem_series)))
    else:
        notes.append("溢价历史没有缓存，本次回测未启用溢价纪律"
                     "（跑一次主页面或预热后即可生效）。")
    if air:
        hm = risk.airman_history_meta()
        if hm:
            notes.append("空中飞人层使用%s：%d 个月末代理点位；缺 %s 四项历史，属代理值。"
                         % ("历史回补序列", hm.get("points") or 0,
                            "、".join(hm.get("unavailable") or [])))
    stats = {}
    navs = run_nav(cfg["weights"], gate=gate, drag=drag,
                   months=st.rebalance_months(),
                   gate_proxy=g.get("proxy"), window=g.get("window", 200),
                   cut=g.get("cut", 0.5), risk_buckets=g.get("risk_buckets"),
                   notes=notes, risk_overlay=overlay, airman_series=air,
                   stats=stats, prem_series=prem_series,
                   prem_blocks=prem_blocks)
    dates = _panel()[0]
    with _lock:
        _memo[key] = (navs, dates, {"gate": gate, "drag": drag,
                                    "risk_overlay": bool(overlay),
                                    "airman_points": len(air),
                                    "actions": int(stats.get("actions") or 0),
                                    "risk_actions": int(stats.get("risk_actions") or 0),
                                    "risk_cuts": int(stats.get("risk_cuts") or 0),
                                    "risk_adds": int(stats.get("risk_adds") or 0),
                                    "premium_skips": int(stats.get("premium_skips") or 0),
                                    "warnings": notes})
    return _memo[key]


def result(sid, drag=0.0, with_curve=True, points=260):
    """给页面用的一整份结果。"""
    import strategy as st
    navs, dates, meta = run(sid, drag=drag)
    if not navs:
        return {"ok": False, "msg": "样本为空（代理序列没取到）"}
    m = metrics(navs, dates)
    m["crisis"] = {name: window_stats(navs, dates, lo, hi)
                   for name, lo, hi in CRISES}
    cfg = st.get(sid)
    m["id"] = sid
    m["name"] = cfg.get("name", sid)
    m["risk"] = cfg.get("risk")
    m["gate"] = bool(cfg.get("gate"))
    m["risk_overlay"] = bool(meta.get("risk_overlay"))
    m["airman_points"] = int(meta.get("airman_points") or 0)
    m["actions"] = int(meta.get("actions") or 0)
    m["risk_actions"] = int(meta.get("risk_actions") or 0)
    m["risk_cuts"] = int(meta.get("risk_cuts") or 0)
    m["risk_adds"] = int(meta.get("risk_adds") or 0)
    m["premium_skips"] = int(meta.get("premium_skips") or 0)
    m["weights"] = cfg["weights"]
    m["warnings"] = meta.get("warnings") or []
    if with_curve:
        step = max(1, len(navs) // max(60, points))
        m["curve"] = [[dates[i], round(navs[i], 6)] for i in range(0, len(navs), step)]
        if m["curve"][-1][0] != dates[-1]:
            m["curve"].append([dates[-1], round(navs[-1], 6)])
    return m


def result_all(drag=0.0):
    """所有策略的指标（不含曲线，给对比表用）。"""
    import strategy as st
    out = {}
    for sid in st.list_ids():
        r = result(sid, drag=drag, with_curve=False)
        if r.get("cagr") is not None:
            out[sid] = {k: r.get(k) for k in
                        ("id", "name", "risk", "gate", "risk_overlay",
                         "airman_points", "actions", "risk_actions",
                         "risk_cuts", "risk_adds", "cagr", "mdd",
                         "premium_skips",
                         "vol", "sharpe", "worst_3y", "worst_year",
                         "under_years", "start", "end", "weights")}
    return out


def reset():
    """清掉记忆（改了 strategy.json 或想重取数时用）。"""
    with _lock:
        _memo.clear()
    _PANEL_CACHE.clear()
