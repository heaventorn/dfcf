# -*- coding: utf-8 -*-
"""分层风险减仓。

这一层只改「目标权重」，不直接下单：
  1. 趋势闸由 strategy.gate_state() 负责（H00300 与 200 日均线）；
  2. 空中飞人指数负责宏观风险预算；
  3. H00300 回撤负责危机中的追加减仓。

多层同时触发时按顺序相乘，但保留一个风险资产下限，避免一次危机把权益腿
砍到接近零。买卖仍然统一由 rebalance.py 生成。
"""

import datetime
import json
import os
import threading

import config

SNAPSHOT_FILE = os.path.join(config.OUTPUT_DIR, "airman_snapshot.json")
_lock = threading.Lock()

DEFAULT = {
    "enabled": True,
    "risk_buckets": ["divA", "divHK", "broad", "ndx"],
    "sink": "cash",
    # 最终风险资产保留比例。0.30 = 最多砍掉 70% 的初始风险敞口。
    "min_keep": 0.30,
    # 降档动作门槛：风险预算变动不足这个幅度就不折腾。
    "min_step": 0.05,
    # 减仓可以随时执行（危机里要快），加仓只在半年再平衡月执行（避免来回穿越）。
    "cut_anytime": True,
    "add_at_rebalance_only": True,
    "airman": {
        "enabled": True,
        "levels": [
            {"score": 76, "mult": 0.50, "name": "爆破临界区"},
            {"score": 51, "mult": 0.70, "name": "高危区"},
            {"score": 26, "mult": 0.90, "name": "警戒区"},
            {"score": 0, "mult": 1.00, "name": "安全区"},
        ],
    },
    "drawdown": {
        "enabled": True,
        "window": 252,
        "levels": [
            {"dd": -0.15, "mult": 0.40, "name": "深度危机"},
            {"dd": -0.12, "mult": 0.60, "name": "危机"},
            {"dd": -0.08, "mult": 0.80, "name": "中度回撤"},
            {"dd": -0.06, "mult": 0.90, "name": "回撤预警"},
            {"dd": 0.00, "mult": 1.00, "name": "正常"},
        ],
    },
}


def _merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize(cfg=None):
    return _merge(DEFAULT, cfg or {})


def airman_layer(score, cfg=None):
    c = normalize(cfg).get("airman") or {}
    if not c.get("enabled", True):
        return {"key": "airman", "enabled": False, "active": False,
                "mult": 1.0, "name": "已关闭", "score": None}
    if score is None:
        return {"key": "airman", "enabled": True, "active": False,
                "mult": 1.0, "name": "无数据", "score": None}
    levels = sorted(c.get("levels") or [], key=lambda x: float(x.get("score") or 0),
                    reverse=True)
    for lv in levels:
        if score >= float(lv.get("score") or 0):
            mult = float(lv.get("mult") or 1.0)
            return {"key": "airman", "enabled": True, "active": mult < 0.999,
                    "mult": mult, "name": lv.get("name") or "空中飞人",
                    "score": float(score)}
    return {"key": "airman", "enabled": True, "active": False,
            "mult": 1.0, "name": "安全区", "score": float(score)}


def drawdown_layer(dd, cfg=None):
    c = normalize(cfg).get("drawdown") or {}
    if not c.get("enabled", True):
        return {"key": "drawdown", "enabled": False, "active": False,
                "mult": 1.0, "name": "已关闭", "dd": None}
    if dd is None:
        return {"key": "drawdown", "enabled": True, "active": False,
                "mult": 1.0, "name": "无数据", "dd": None}
    levels = sorted(c.get("levels") or [], key=lambda x: float(x.get("dd") or 0))
    for lv in levels:
        if dd <= float(lv.get("dd") or 0):
            mult = float(lv.get("mult") or 1.0)
            return {"key": "drawdown", "enabled": True, "active": mult < 0.999,
                    "mult": mult, "name": lv.get("name") or "回撤",
                    "dd": float(dd)}
    return {"key": "drawdown", "enabled": True, "active": False,
            "mult": 1.0, "name": "正常", "dd": float(dd)}


def state(score=None, dd=None, cfg=None, trend_on=None, trend_cut=None):
    """返回当前各层状态与合并后的风险预算乘数。"""
    c = normalize(cfg)
    layers = [airman_layer(score, c), drawdown_layer(dd, c)]
    raw = 1.0
    for x in layers:
        if x.get("enabled", True):
            raw *= float(x.get("mult") or 1.0)
    floor = max(0.0, min(1.0, float(c.get("min_keep") or 0.30)))
    mult = max(floor, min(1.0, raw))
    for x in layers:
        x["mult"] = round(float(x.get("mult") or 1.0), 4)
        x["share_of_cut"] = (round(1.0 - float(x.get("mult") or 1.0), 4)
                             if x.get("active") else 0.0)
    active = mult < 0.999
    parts = [x.get("name") or x.get("key") for x in layers
             if x.get("enabled", True) and x.get("active")]
    if trend_on:
        name = "趋势闸触发"
        if trend_cut is not None:
            name += "（风险资产 ×%.0f%%）" % ((1.0 - float(trend_cut)) * 100)
        parts.append(name)
    return {
        "enabled": bool(c.get("enabled", True)),
        "active": active,
        "multiplier": mult,
        "raw_multiplier": raw,
        "floor": floor,
        "risk_buckets": list(c.get("risk_buckets") or []),
        "sink": c.get("sink") or "cash",
        "layers": layers,
        "trend_on": bool(trend_on),
        "trend_cut": None if trend_cut is None else float(trend_cut),
        "summary": " / ".join(parts) if parts else "正常",
    }


def apply_weights(weights, risk_state, cfg=None):
    """把合并后的风险预算乘数作用到风险资产上，腾出的仓位转到 cash。"""
    c = normalize(cfg)
    w = {k: float(v) for k, v in (weights or {}).items() if float(v) > 1e-12}
    if not c.get("enabled", True) or not risk_state:
        return w
    mult = float(risk_state.get("multiplier") or 1.0)
    if mult >= 0.999:
        return w
    risk = list(risk_state.get("risk_buckets") or c.get("risk_buckets") or [])
    total = sum(w.get(k, 0.0) for k in risk)
    if total <= 1e-12:
        return w
    keep = total * mult
    for k in risk:
        if k in w:
            w[k] *= keep / total
    sink = risk_state.get("sink") or c.get("sink") or "cash"
    w[sink] = w.get(sink, 0.0) + (total - keep)
    return {k: v for k, v in w.items() if v > 1e-12}


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_airman(res, refs=None):
    """保存最新空中飞人快照；失败不影响主流程。"""
    if not res:
        return None
    payload = {
        "at": res.get("time") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": res.get("total"),
        "level": res.get("level_name"),
        "color": res.get("level_color"),
        "dims": res.get("dims") or [],
        "signals": res.get("signals") or [],
        "refs": refs or [],
    }
    with _lock:
        _atomic_json(SNAPSHOT_FILE, payload)
    return payload


def load_airman():
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_airman_series():
    """回测用：读飞人总分时间序列，返回 {YYYYMMDD: score}。

    来源：历史回补代理（airman_history.json）→ 实盘快照（history.db / snapshot）。
    实盘快照优先，因为它没有缺项。
    """
    out = {}
    hist_path = os.path.join(config.OUTPUT_DIR, "airman_history.json")
    try:
        with open(hist_path, encoding="utf-8") as f:
            for d, v in (json.load(f).get("points") or {}).items():
                d = str(d).replace("-", "")[:8]
                if len(d) == 8 and v is not None:
                    out[d] = float(v)
    except Exception:
        pass
    try:
        import history
        for ts, total in history.risk_series():
            d = str(ts or "").replace("-", "")[:8]
            if len(d) == 8 and total is not None:
                out[d] = float(total)
    except Exception:
        pass
    snap = load_airman()
    d = str(snap.get("at") or "").replace("-", "")[:8]
    if len(d) == 8 and snap.get("total") is not None:
        out[d] = float(snap["total"])
    return out


def latest_score():
    snap = load_airman()
    try:
        return float(snap.get("total"))
    except (TypeError, ValueError):
        return None


def airman_history_meta():
    """回测用的飞人历史代理序列说明；没有回补文件时返回 {}。"""
    hist_path = os.path.join(config.OUTPUT_DIR, "airman_history.json")
    try:
        with open(hist_path, encoding="utf-8") as f:
            d = json.load(f)
        return {"method": d.get("method"), "unavailable": d.get("unavailable") or [],
                "points": len(d.get("points") or {})}
    except Exception:
        return {}
