# -*- coding: utf-8 -*-
"""日线数据层：给策略 / 回测 / 盯盘提供统一的「日期 -> 收盘价」序列。

三个来源，各司其职：
  csi     中证指数官网（含全收益指数，2004 起）  —— 回测主力
  em      东财 push2his（商品 / 海外指数 / 港股） —— 回测补充
  tx      腾讯 K 线（复用 kchart.fetch_kline）    —— 实盘 ETF 盯盘

缓存落在 output/bars_cache.json。历史序列是只增不改的，所以命中规则是
「缓存里最后一天已经贴近今天」就直接复用，而不是「必须今天抓过」——
中证官网一次全历史要几十秒，七个代理串起来就是十分钟，每天重抓一遍
会让回测页面没法用。默认容忍 5 个自然日（覆盖周末和长假）。

取数失败不抛给调用方：返回已缓存的部分，宁可页面用旧数据也不要打不开。
"""

import datetime
import json
import os
import sys
import threading
import time

import requests

import config

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CACHE_FILE = os.path.join(config.OUTPUT_DIR, "bars_cache.json")

# 缓存容忍天数：缓存里最后一天离今天不超过这么多天，就认为够新、不重抓。
KEEP_DAYS = 5
# 腾讯日线是实盘要用的现价，容忍度必须小
TX_KEEP_DAYS = 1

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}
_CSI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.csindex.com.cn/",
}

_lock = threading.Lock()
_cache = None


def _today():
    return datetime.date.today().strftime("%Y%m%d")


def _load():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            _cache = json.load(f)
    except Exception:
        _cache = {}
    return _cache


def _save():
    """落盘。多线程并发预热时会被频繁调用，所以序列化放在锁外做。"""
    with _lock:
        blob = json.dumps(_cache, ensure_ascii=False)
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(blob)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def _floats(data):
    return {str(k): float(v) for k, v in data.items()}


def _cached(key, keep_days=None):
    """够新就返回缓存，否则 None。"""
    with _lock:
        raw = _load().get(key)
    if not raw or not raw.get("data"):
        return None
    if raw.get("day") == _today():
        return _floats(raw["data"])
    keep = KEEP_DAYS if keep_days is None else keep_days
    cutoff = (datetime.date.today() - datetime.timedelta(days=keep)).strftime("%Y%m%d")
    if max(str(k) for k in raw["data"]) >= cutoff:
        return _floats(raw["data"])
    return None


def _stale(key):
    """不管新不新，把缓存里已有的那份拿出来（取数失败时的兜底）。"""
    with _lock:
        raw = _load().get(key)
    return _floats((raw or {}).get("data") or {})


def _store(key, data):
    with _lock:
        _load()[key] = {"day": _today(), "data": data}
    _save()
    return data


def cache_info():
    """给页面用：每条缓存抓于哪天、覆盖到哪天、多少行。"""
    with _lock:
        raw = dict(_load())
    out = []
    for k, v in raw.items():
        d = v.get("data") or {}
        out.append({"key": k, "day": v.get("day"), "rows": len(d),
                    "from": min(d) if d else "", "to": max(d) if d else ""})
    return sorted(out, key=lambda x: x["key"])


def tx_code(code):
    """511880 -> sh511880；159941 -> sz159941；已带前缀的原样返回。"""
    c = str(code or "").strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    return ("sh" if c[:1] in ("5", "6", "9", "2") else "sz") + c


# ------------------------------------------------------------------ 数据源

def csi(code, beg="20040101", end=None):
    """中证指数官网日收盘。返回 {YYYYMMDD: close}。"""
    key = "csi:%s" % code
    hit = _cached(key)
    if hit:
        return hit
    end = end or _today()
    out = {}
    for attempt in range(3):
        try:
            r = requests.get(
                "https://www.csindex.com.cn/csindex-home/perf/index-perf",
                params={"indexCode": code, "startDate": beg, "endDate": end},
                headers=_CSI_HEADERS, timeout=45)
            for x in (r.json().get("data") or []):
                d = str(x.get("tradeDate") or "")
                c = x.get("close")
                if len(d) == 8 and d != "20000101" and c:
                    out[d] = float(c)
            if out:
                break
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    if out:
        return _store(key, out)
    return _stale(key)


def em(secid, fqt=1, beg="20040101"):
    """东财 push2his 日收盘。返回 {YYYYMMDD: close}。"""
    key = "em:%s:%d" % (secid, fqt)
    hit = _cached(key)
    if hit:
        return hit
    out = {}
    for attempt in range(4):
        try:
            r = requests.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={"secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57",
                        "klt": 101, "fqt": fqt, "beg": beg, "end": "20500101"},
                headers=_EM_HEADERS, timeout=45)
            for k in ((r.json().get("data") or {}).get("klines") or []):
                p = k.split(",")
                if len(p) >= 3:
                    try:
                        out[p[0].replace("-", "")] = float(p[2])
                    except ValueError:
                        pass
            if out:
                break
        except Exception:
            pass
        time.sleep(2.0 * (attempt + 1))
    if out:
        return _store(key, out)
    return _stale(key)


def tx(code, n=320):
    """腾讯前复权日线（走 kchart，和其他页面同一条源）。返回 {YYYYMMDD: close}。"""
    key = "tx:%s:%d" % (tx_code(code), n)
    hit = _cached(key, keep_days=TX_KEEP_DAYS)
    if hit:
        return hit
    try:
        import kchart
        df = kchart.fetch_kline(tx_code(code), n=n)
        out = {str(r["date"]).replace("-", ""): float(r["close"])
               for _, r in df.iterrows()}
        if out:
            return _store(key, out)
    except Exception:
        pass
    return _stale(key)


# ------------------------------------------------------------- 汇率（合成）

# 2010-08-23 之前没有离岸人民币数据，用公开的美元兑人民币中间价重建。
_CNY_ANCHORS = [
    ("20040102", 8.2767), ("20050720", 8.2765), ("20050721", 8.1100),
    ("20051230", 8.0702), ("20061229", 7.8087), ("20070629", 7.6155),
    ("20071228", 7.3046), ("20080430", 6.9890), ("20080731", 6.8388),
    ("20081231", 6.8346), ("20091231", 6.8282), ("20100618", 6.8275),
    ("20100820", 6.7900),
]


def _d(s):
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def usdcny(dates):
    """给一串 YYYYMMDD，返回同口径的汇率路径（人民币/美元）。

    2010-08-23 起用真实离岸 CNH；之前用中间价锚点分段线性重建，
    并在接入日之前的 60 个自然日里平滑过渡到真实序列，避免拼接处跳空。
    """
    real = em("133.USDCNH", 0)
    real_days = sorted(real)
    real_start = real_days[0] if real_days else "29991231"
    scale = (real[real_start] / _CNY_ANCHORS[-1][1]) if real_days else 1.0
    ax = [_d(d) for d, _ in _CNY_ANCHORS]
    av = [v for _, v in _CNY_ANCHORS]
    out, cur_ix = {}, 0
    cur_v = real[real_days[0]] if real_days else None
    for ds in sorted(dates):
        if ds >= real_start:
            while cur_ix < len(real_days) - 1 and real_days[cur_ix + 1] <= ds:
                cur_ix += 1
                cur_v = real[real_days[cur_ix]]
            out[ds] = cur_v
            continue
        t = _d(ds)
        if t <= ax[0]:
            v = av[0]
        else:
            v = av[-1]
            for i in range(1, len(ax)):
                if t <= ax[i]:
                    f = (t - ax[i - 1]).days / (ax[i] - ax[i - 1]).days
                    v = av[i - 1] + f * (av[i] - av[i - 1])
                    break
        gap = (ax[-1] - t).days
        if gap <= 0:
            v *= scale
        elif gap < 60:
            v *= scale + (1 - scale) * gap / 60.0
        out[ds] = v
    return out


# ------------------------------------------------------------- 代理序列

def proxy_series(spec):
    """按 strategy.json 里的 proxy 说明取一条回测用的净值序列。"""
    if not spec:
        return {}
    src, code = spec.get("src"), spec.get("code")
    if src == "csi":
        base = csi(code)
    elif src == "tx":
        base = tx(code, spec.get("n", 5000))
    elif src in ("em", "em_fx"):
        base = em(code, spec.get("fqt", 0))
    else:
        return {}

    # 海外指数要换成人民币口径（QDII 不对冲汇率）
    if src == "em_fx" and base:
        fx = usdcny(base.keys())
        base = {d: v * fx[d] for d, v in base.items() if d in fx}

    sp = spec.get("splice")
    if sp and sp.get("before") and base:
        cut = str(sp["before"])
        ay = float(sp.get("add_yield") or 0.0)
        head = proxy_series({k: v for k, v in sp.items()
                             if k not in ("before", "add_yield")})
        if head:
            # 价格指数 + 常数年化股息 = 全收益。不补这一段，用价格指数回测
            # 红利指数会系统性低估一大截（港股红利年化股息 7% 上下，
            # 等于把十几年的收益砍掉一半）。
            if ay:
                grown, acc, prev = {}, 1.0, None
                for d in sorted(head):
                    if prev is not None:
                        acc *= (1.0 + ay) ** ((_d(d) - _d(prev)).days / 365.25)
                    prev = d
                    grown[d] = head[d] * acc
                head = grown
            first = next((d for d in sorted(base) if d >= cut), None)
            if first:
                # 对齐拼接口：老序列整体缩放到新序列在切口处的水平
                hp = [d for d in sorted(head) if d <= first]
                hv = head[hp[-1]] if hp else head[sorted(head)[-1]]
                anchor = hv / base[first]
                out = {}
                for d in sorted(set(head) | set(base)):
                    if d < cut and d in head:
                        out[d] = head[d]
                    elif d >= cut and d in base:
                        out[d] = base[d] * anchor
                base = out
    return base


def last_price(code):
    """实盘最新价。失败返回 None。"""
    try:
        import sources
        q = sources.get_realtime_quotes([tx_code(code)]) or {}
        for v in q.values():
            p = v.get("price")
            if p:
                return float(p)
    except Exception:
        pass
    hist = tx(code, 10)
    if hist:
        return hist[sorted(hist)[-1]]
    return None


# ------------------------------------------------------------- 预热 / 迁移

def warm(specs, parallel=6):
    """并行把所有代理序列抓一遍落缓存。

    specs 是 proxy 字典的集合（strategy.py 从 strategy.json 里收集）。
    中证官网单条全历史要几十秒，串行七个就是十分钟；这里并发拉，
    任何一条失败都只是少一条，不影响其余的。
    返回 {"ok": [...], "fail": [...], "secs": float}。
    """
    from concurrent.futures import ThreadPoolExecutor

    todo, seen = [], set()
    for spec in specs:
        if not spec:
            continue
        items = [spec]
        sp = spec.get("splice")
        if sp:
            items.append({k: v for k, v in sp.items()
                          if k not in ("before", "add_yield")})
        # 人民币口径还需要汇率那条腿
        if spec.get("src") == "em_fx":
            items.append({"src": "em", "code": "133.USDCNH", "fqt": 0})
        for it in items:
            tag = "%s:%s" % (it.get("src"), it.get("code"))
            if tag not in seen:
                seen.add(tag)
                todo.append((tag, it))

    t0 = time.time()
    ok, fail = [], []

    def _one(item):
        tag, spec = item
        try:
            return tag, len(proxy_series(spec))
        except Exception as e:
            return tag, "ERR %s: %s" % (type(e).__name__, e)

    with ThreadPoolExecutor(max_workers=max(1, int(parallel))) as ex:
        for tag, res in ex.map(_one, todo):
            if isinstance(res, int) and res > 0:
                ok.append("%s(%d)" % (tag, res))
            else:
                fail.append("%s -> %s" % (tag, res))
    return {"ok": ok, "fail": fail, "secs": round(time.time() - t0, 1)}


# 早期在 _bt_tmp/cache 里抓好的原始序列，可以一次性灌进正式缓存，
# 省掉第一次跑回测的十分钟。文件不存在就跳过，纯加速用。
_SEED_MAP = {
    "money": ("csi", "H11025"), "bond": ("csi", "H11001"),
    "treasury": ("csi", "H11006"), "divLv": ("csi", "H20269"),
    "divA": ("csi", "H00922"), "broad": ("csi", "H00300"),
    "hkDivPx": ("csi", "H11141"), "hkDivTR": ("csi", "H20914"),
    "gold": ("em", "118.Au9999"), "ndx": ("em", "100.NDX"),
    "usdcnh": ("em", "133.USDCNH"),
}


def seed_from_dir(path):
    """把外部目录里 {名字: {日期: 值}}.json 灌进缓存。返回灌入条数。"""
    n = 0
    for name, (src, code) in _SEED_MAP.items():
        f = os.path.join(path, name + ".json")
        if not os.path.exists(f):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                ser = json.load(fh)
        except Exception:
            continue
        data = {str(k): float(v) for k, v in ser.items() if v}
        if not data:
            continue
        key = ("csi:%s" % code) if src == "csi" else ("em:%s:0" % code)
        _store(key, data)
        n += 1
    return n
