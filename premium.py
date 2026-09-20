# -*- coding: utf-8 -*-
"""ETF 场内溢价 / 折价。

溢价率 = 场内价 / 基金实时估值(IOPV) - 1。

为什么必须用 IOPV，而不是「现价 ÷ 最新单位净值」：
单位净值是**已披露**的，A 股 ETF 的是昨天收盘算出来的，纳指 QDII 的要等美股
收盘、还会再滞后一两天。拿今天的现价去除昨天的净值，算出来的一多半是
「今天涨了多少」，不是溢价。实测：沪深300ETF 会算出 +1.13%、黄金ETF +1.32%，
而两者的真实溢价都在 ±0.1% 以内 —— 那个数字其实就是当天的涨跌幅。

腾讯行情第 78 个字段是交易所发布的 IOPV 实时估值，和现价同一时点，可以直接
相除。第 81 个字段是最近一次披露的单位净值，只作兜底和展示。

历史分位用「当日收盘 ÷ 当日单位净值」：同一天两边都是当天口径，和 IOPV 算的
是同一个东西。IOPV 只有实时值、没有历史，回补不了。
"""

import datetime
import json
import os
import re
import threading
import time

import config

CACHE_FILE = os.path.join(config.OUTPUT_DIR, "premium_cache.json")
NAV_TTL = 12 * 3600.0
PX_TTL = 12 * 3600.0
QUOTE_TTL = 30.0
STATS_WINDOW = 756          # 约 3 年交易日
_lock = threading.Lock()
_quote_cache = {"at": 0.0, "data": {}}

_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
_TX_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 场内基金代码：沪市 5xxxxx（ETF/LOF）、深市 15xxxx/16xxxx。
# 场外基金（如 270042、040046）在行情接口里会命中同号的债券或别的品种，
# 拿它的价格去算溢价会得到 +1152% 这种荒唐值，必须挡在行情查询之外。
_ON_EXCHANGE = re.compile(r"^(?:5\d{5}|1[56]\d{4})$")


def is_on_exchange(code):
    return bool(_ON_EXCHANGE.match(str(code or "")))


# ------------------------------------------------------------------ 实时行情

def quotes(codes):
    """腾讯实时行情。返回 {code: {price, iopv, nav, name, time}}。

    一次请求最多 50 只；带 30 秒缓存，翻页面不会反复打上游。
    """
    import bars
    want = [c for c in dict.fromkeys(codes or []) if c and is_on_exchange(c)]
    if not want:
        return {}
    now = time.time()
    with _lock:
        if now - _quote_cache["at"] < QUOTE_TTL:
            hit = _quote_cache["data"]
            if all(c in hit for c in want):
                return {c: hit[c] for c in want}
    import requests
    out = {}
    for i in range(0, len(want), 50):
        chunk = want[i:i + 50]
        url = "https://qt.gtimg.cn/q=" + ",".join(bars.tx_code(c) for c in chunk)
        try:
            r = requests.get(url, headers=_TX_H, timeout=15)
            r.encoding = "gbk"
        except Exception:
            continue
        for line in r.text.splitlines():
            if "~" not in line:
                continue
            try:
                p = line.split('"')[1].split("~")
            except IndexError:
                continue
            if len(p) < 82 or not p[2]:
                continue

            def num(i):
                try:
                    return float(p[i])
                except (TypeError, ValueError, IndexError):
                    return None

            out[p[2]] = {"code": p[2], "name": p[1], "price": num(3),
                         "pre_close": num(4), "iopv": num(78), "nav": num(81),
                         "time": p[30] if len(p) > 30 else None}
    if out:
        with _lock:
            data = dict(_quote_cache["data"]) if now - _quote_cache["at"] < QUOTE_TTL else {}
            data.update(out)
            _quote_cache["at"] = now
            _quote_cache["data"] = data
    return out


# ------------------------------------------------------------------ 缓存

def _load():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("nav", {})
            d.setdefault("px", {})
            return d
    except Exception:
        pass
    return {"nav": {}, "px": {}}


def _save(d):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def _fresh(node, ttl):
    try:
        return (time.time() - float(node.get("at") or 0)) < ttl
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ 历史

def _nav_from_js(text):
    """从 pingzhongdata 的 JS 里抠出单位净值 + 分红/折算记录。

    第二个返回值是 {日期: 说明}，只在基金做过分红或份额折算时才有值。
    持仓评估要靠它把「分红那天净值凭空掉一块」从跟踪误差里剔掉，
    不然后面会算出 510300 一年 2.55% 的跟踪误差（真实值 0.19%）。
    """
    import re
    nav, ev = {}, {}
    m = re.search(r"var Data_netWorthTrend\s*=\s*(\[.*?\]);", text)
    for x in (json.loads(m.group(1)) if m else []):
        try:
            ms, v = float(x.get("x")), float(x.get("y"))
        except (TypeError, ValueError):
            continue
        # 时间戳是北京时间当日 0 点
        d = datetime.datetime.utcfromtimestamp(ms / 1000.0 + 8 * 3600)
        if v > 0:
            nav[d.strftime("%Y%m%d")] = v
        if x.get("unitMoney"):
            ev[d.strftime("%Y%m%d")] = str(x["unitMoney"])
    return nav, ev


def fetch_nav(code, want_events=False):
    """全部历史单位净值 {YYYYMMDD: nav}（want_events=True 时带上分红记录）。

    走 pingzhongdata：一个请求带回全部历史（513100 有 3217 个点）。
    lsjz 接口每页最多 20 条，拉十几年要点一百多次，只做兜底 ——
    而且兜底那条没有分红记录，只能给空表。
    """
    import re
    import requests
    out, ev = {}, {}
    try:
        url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0",
                                       "Referer": "https://fund.eastmoney.com/%s.html" % code},
                         timeout=30)
        out, ev = _nav_from_js(r.text)
    except Exception:
        out = {}
    if out:
        return (out, ev) if want_events else out
    for page in range(1, 4):
        try:
            r = requests.get("https://api.fund.eastmoney.com/f10/lsjz",
                             params={"fundCode": code, "pageIndex": page, "pageSize": 20},
                             headers=_UA, timeout=25)
            rows = ((r.json().get("Data") or {}).get("LSJZList")) or []
        except Exception:
            break
        for x in rows:
            d = str(x.get("FSRQ") or "").replace("-", "")
            try:
                v = float(x.get("DWJZ"))
            except (TypeError, ValueError):
                continue
            if len(d) == 8 and v > 0:
                out[d] = v
        if len(rows) < 20:
            break
    return (out, ev) if want_events else out


def _js_object(text, var):
    """从 JS 里抠一个对象字面量（按大括号配对，不吃掉嵌套的 }）。"""
    import re
    m = re.search(r"var %s\s*=\s*\{" % re.escape(var), text)
    if not m:
        return None
    i = text.find("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1])
                except Exception:
                    return None
        j += 1
    return None


def _profile_from_js(text):
    """基金概况：规模（季报口径，亿元）+ 机构持有比例。

    规模用来看清盘线（连续 60 个工作日低于 5000 万就清盘），持有结构用来看
    大额赎回风险。两个都在同一份 pingzhongdata 里，不用另接接口。
    """
    prof = {}
    d = _js_object(text, "Data_fluctuationScale")
    if d:
        cats = d.get("categories") or []
        # 注意这里的结构：series 里是「每个报告期一个点」，不是「每条序列一组数」
        ys = []
        for x in (d.get("series") or []):
            try:
                ys.append(float(x.get("y")))
            except (TypeError, ValueError):
                continue
        if cats and ys:
            prof["scale"] = ys[-1]
            prof["scale_date"] = str(cats[-1])
            prof["scale_prev"] = ys[-2] if len(ys) > 1 else None
    d = _js_object(text, "Data_assetAllocation")
    if d:
        for s in d.get("series") or []:
            if "现金" in str(s.get("name")):
                xs = s.get("data") or []
                if xs:
                    try:
                        prof["cash_pct"] = float(xs[-1])
                    except (TypeError, ValueError):
                        pass
    d = _js_object(text, "Data_holderStructure")
    if d:
        cats = d.get("categories") or []
        for s in d.get("series") or []:
            if "机构" in str(s.get("name")):
                xs = s.get("data") or []
                if xs:
                    try:
                        prof["inst_pct"] = float(xs[-1])
                        prof["inst_date"] = str(cats[-1]) if cats else None
                    except (TypeError, ValueError):
                        pass
    return prof


def fetch_profile(code):
    import requests
    try:
        url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0",
                                       "Referer": "https://fund.eastmoney.com/%s.html" % code},
                         timeout=30)
        return _profile_from_js(r.text)
    except Exception:
        return {}


def profile(code, force=False):
    """基金规模 / 持有结构。缓存 12 小时，和净值一个节奏。"""
    with _lock:
        node = (_load().get("profile", {}).get(code) or {})
    if not force and _fresh(node, NAV_TTL) and node.get("data"):
        return dict(node["data"])
    data = fetch_profile(code)
    if data:
        with _lock:
            cache = _load()
            cache.setdefault("profile", {})[code] = {"at": time.time(),
                                                     "data": data}
            _save(cache)
        return data
    with _lock:
        return dict((_load().get("profile", {}).get(code) or {}).get("data") or {})


def _tx_chunk(tx, beg, end):
    import requests
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    # 结尾留空 = 不复权。前复权会把份额折算前的价格整体缩放，
    # 拿去和当年的单位净值比会得到 -80% 这种荒唐数字。
    r = requests.get(url, params={"param": "%s,day,%s,%s,640," % (tx, beg, end)},
                     headers=_TX_H, timeout=25)
    rows = (((r.json().get("data") or {}).get(tx) or {}).get("day")) or []
    out = {}
    for k in rows:
        if len(k) >= 3:
            try:
                out[str(k[0]).replace("-", "")] = float(k[2])
            except (TypeError, ValueError):
                pass
    return out


def fetch_px(code, beg="20040101"):
    """不复权日收盘 {YYYYMMDD: close}。腾讯单次最多 640 根，按窗口切片。"""
    import bars
    tx = bars.tx_code(code)
    out = {}
    cur = datetime.date(int(beg[:4]), int(beg[4:6]), int(beg[6:8]))
    today = datetime.date.today()
    while cur <= today:
        end = min(cur + datetime.timedelta(days=800), today)
        try:
            out.update(_tx_chunk(tx, cur.isoformat(), end.isoformat()))
        except Exception:
            pass
        cur = end + datetime.timedelta(days=1)
    if out:
        out.pop(max(out), None)      # 当天可能只走了半场
    return out


def _cached_series(key, code):
    with _lock:
        node = (_load()[key].get(code) or {})
    return {k: float(v) for k, v in (node.get("data") or {}).items()}


def nav_series(code, force=False, want_events=False):
    """单位净值历史。want_events=True 时返回 (净值, 分红/折算记录)。

    老缓存里没有分红记录，第一次要分红记录时会重抓一次（每只基金一次）。
    """
    with _lock:
        node = (_load()["nav"].get(code) or {})
    hit = bool(_fresh(node, NAV_TTL) and node.get("data"))
    if hit and want_events and "events" not in node:
        hit = False       # 补抓：升级前存下来的缓存没有分红记录
    if hit and not force:
        data = {k: float(v) for k, v in node["data"].items()}
        return (data, node.get("events") or {}) if want_events else data
    nav, ev = fetch_nav(code, want_events=True)
    if nav:
        with _lock:
            cache = _load()
            cache["nav"][code] = {"at": time.time(), "data": nav, "events": ev}
            _save(cache)
        return (nav, ev) if want_events else nav
    data = _cached_series("nav", code)
    return (data, {}) if want_events else data


def px_series(code, force=False):
    if not force:
        with _lock:
            node = (_load()["px"].get(code) or {})
        if _fresh(node, PX_TTL) and node.get("data"):
            return {k: float(v) for k, v in node["data"].items()}
    data = fetch_px(code)
    if data:
        with _lock:
            cache = _load()
            cache["px"][code] = {"at": time.time(), "data": data}
            _save(cache)
        return data
    return _cached_series("px", code)


# ------------------------------------------------------------------ 计算

def series(code, force=False):
    """历史溢价序列 {YYYYMMDD: premium}（当日收盘 ÷ 当日单位净值）。

    价格历史只读缓存、不在这里抓：分位是锦上添花，不能让它把页面卡住。
    冷启动返回空表，等 warm() 在后台补上。
    """
    nav = nav_series(code, force=force)
    px = px_series(code, force=force) if force else _cached_series("px", code)
    out = {}
    for d in set(nav) & set(px):
        n = nav[d]
        if n > 0:
            out[d] = px[d] / n - 1.0
    return out


def _stats(s, window=STATS_WINDOW):
    ds = sorted(s)[-window:]
    xs = sorted(s[d] for d in ds)
    if not xs:
        return {}
    n = len(xs)
    return {"n": n, "from": ds[0], "to": ds[-1],
            "median": xs[n // 2],
            "p10": xs[max(0, int(n * 0.10) - 1)],
            "p90": xs[min(n - 1, int(n * 0.90))],
            "max": xs[-1], "min": xs[0]}


def snapshot(code, quote=None):
    """当前溢价。优先 IOPV（同一点），没有才退回最新单位净值（会滞后）。"""
    if not is_on_exchange(code):
        nav = nav_series(code)
        if not nav:
            return None
        nd = max(nav)
        # 场外基金没有二级市场溢价，按净值申购；这里只给最新净值做参考。
        return {"code": code, "name": code, "otc": True, "premium": None,
                "price": None, "iopv": None, "nav": nav[nd], "nav_date": nd,
                "basis": nav[nd], "basis_src": "nav", "percentile": None,
                "stats": {}, "quote_time": None,
                "asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    if quote is None:
        quote = quotes([code]).get(code) or {}
    px = quote.get("price")
    if not px:
        hist = _cached_series("px", code)
        px = hist.get(max(hist)) if hist else None
    nav = nav_series(code)
    nd = max(nav) if nav else None
    navv = nav.get(nd) if nd else quote.get("nav")
    iopv = quote.get("iopv")
    if iopv and iopv > 0:
        basis, src = float(iopv), "iopv"
    elif navv and navv > 0:
        basis, src = float(navv), "nav"
    else:
        return None
    if not px:
        return None
    cur = float(px) / basis - 1.0
    s = series(code)
    st = _stats(s)
    pct = None
    if st.get("n"):
        xs = sorted(s[d] for d in sorted(s)[-st["n"]:])
        pct = sum(1 for x in xs if x <= cur) / len(xs) * 100.0
    return {"code": code, "name": quote.get("name") or code,
            "price": float(px), "iopv": float(iopv) if iopv else None,
            "nav": float(navv) if navv else None, "nav_date": nd,
            "basis": basis, "basis_src": src, "premium": cur,
            "percentile": pct, "stats": st,
            "quote_time": quote.get("time"),
            "asof": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}


def attach(codes, quotes_map=None):
    """批量取快照。一次行情请求搞定所有标的。"""
    want = [c for c in dict.fromkeys(codes or []) if c]
    qm = quotes_map if quotes_map is not None else quotes(want)
    out = {}
    for c in want:
        try:
            s = snapshot(c, qm.get(c))
        except Exception:
            s = None
        if s:
            out[c] = s
    return out


def gate(snap, warn=0.01, block=0.03):
    """按溢价给买卖动作定级：ok / warn / block。"""
    if not snap or snap.get("premium") is None:
        return {"level": "unknown", "reason": "拿不到估值，无法判断溢价"}
    p = float(snap["premium"])
    lag = "（按最新净值估算，非实时）" if snap.get("basis_src") == "nav" else ""
    if p >= block:
        return {"level": "block", "premium": p,
                "reason": "溢价 %.1f%%，超过 %.1f%% 上限，别在场内买%s"
                          % (p * 100, block * 100, lag)}
    if p >= warn:
        return {"level": "warn", "premium": p,
                "reason": "溢价 %.1f%%，偏高%s" % (p * 100, lag)}
    if p <= -warn:
        return {"level": "good", "premium": p,
                "reason": "折价 %.1f%%，场内买比净值还便宜" % (-p * 100)}
    return {"level": "ok", "premium": p, "reason": "溢价 %.2f%%，正常" % (p * 100)}


def warm(codes):
    """后台预热：把净值和价格历史拉全，之后算历史分位就不用等。"""
    for c in dict.fromkeys(codes or []):
        try:
            nav_series(c, want_events=True)
            px_series(c)
        except Exception:
            pass
