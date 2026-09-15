# -*- coding: utf-8 -*-
"""
个股终端 · 数据层
==================
把「点开一只票要看的东西」组装成一份 JSON：详细报价 + 当日分时（价 / 均价 / 分钟量）
+ 多周期前复权K线（含 MA / BOLL / MACD / 成交量），外加「现在是不是交易时段」。

这一层只出数据 —— 页面（stock 页）与后台分档刷新（后台按 config.STOCK_*_POLL 抓）
接在它上面。先做数据层的理由很实在：「数据够不够、稳不稳」决定了页面能做多细，
接口一抖页面再漂亮也是白搭。

与既有模块的关系（全部复用，不重复造）：
  - 报价：sources.get_quote_detail（腾讯全字段主源 / 东财兜底）
  - 分时：sources.get_intraday（东财 trends2 主源，带精确均价 / 腾讯兜底）
  - K线：sources.get_kline_period（腾讯前复权主源 / 东财兜底）+ kchart.calc_indicators
  - 代码归一：portfolio.guess_market（与持仓 / 自选同一套沪 / 深判定）
  - 时段判断：本模块 market_state()（纯函数，可离线测）

自检：
    py -3.12 stock.py                    # 离线自检（时段 + 指标 + 形状，不联网）
    py -3.12 stock.py --live 600941      # 抓真实数据打摘要
    py -3.12 stock.py --live 600941 --json   # 打印完整 JSON
"""
import calendar
import datetime
import json
import math
import sys
import time

import config
import sources
import threading
from concurrent.futures import ThreadPoolExecutor


# ---------------------------------------------------------------- 代码归一

def tx_code(code):
    """600941 / sh600941 / 600941.SH → sh600941。

    沪 / 深判定直接复用 portfolio.guess_market —— 持仓、自选、个股终端必须是同一套
    规则，否则同一个代码在不同页面会拼出不同的 tx（v3.1.2 里已经有过两份实现，
    别再添第三份）。
    """
    s = str(code or "").strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.endswith((".sh", ".ss")):
        return "sh" + s[:-3]
    if s.endswith(".sz"):
        return "sz" + s[:-3]
    import portfolio
    return portfolio.guess_market(s) + s


# ---------------------------------------------------------------- 交易时段

def _hm(h, m):
    return datetime.time(h, m)


def _at(now, h, m):
    return datetime.datetime.combine(now.date(), _hm(h, m))


def _next_open(now):
    """下一个交易日的开盘时刻（09:15 集合竞价开始）。只跳周末，不查节假日日历。"""
    d = now.date() + datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return datetime.datetime.combine(d, _hm(9, 15))


def _state(kind, trading, label, next_at, now):
    return {"state": kind, "trading": trading, "label": label,
            "next_at": next_at.strftime("%Y-%m-%d %H:%M:%S") if next_at else "",
            "now": now.strftime("%Y-%m-%d %H:%M:%S")}


def market_state(now=None):
    """当前所处的交易时段（纯函数，传 now 就能离线测）。

    状态：weekend 周末 / pre 集合竞价 / open 连续竞价 / lunch 午间休市 /
          post 盘后固定价格（15:00-15:30，量额这时才定型） / closed 已收盘。
    trading=True 的状态才是「数据还在动」的，前端只在这几个状态下开定时器。

    交易日历（节假日）**故意不维护**：节假日会白刷几轮，但每轮数据一模一样，
    后台按「内容没变就不更新版本号」自然就停下来了 —— 比养一张节假日表划算得多。
    """
    now = now or datetime.datetime.now()
    if now.weekday() >= 5:
        return _state("weekend", False, "周末休市", _next_open(now), now)
    t = now.time()
    if _hm(9, 15) <= t < _hm(9, 30):
        return _state("pre", True, "集合竞价", _at(now, 9, 30), now)
    if _hm(9, 30) <= t < _hm(11, 30):
        return _state("open", True, "交易中", _at(now, 11, 30), now)
    if _hm(11, 30) <= t < _hm(13, 0):
        return _state("lunch", False, "午间休市", _at(now, 13, 0), now)
    if _hm(13, 0) <= t < _hm(15, 0):
        return _state("open", True, "交易中", _at(now, 15, 0), now)
    if _hm(15, 0) <= t < _hm(15, 30):
        return _state("post", True, "盘后固定价格", _at(now, 15, 30), now)
    return _state("closed", False, "已收盘", _next_open(now), now)


def poll_seconds(state, kind="quote"):
    """按状态给刷新间隔（秒）：非交易时段返回 0 = 不要开定时器。

    这一档表就是先前定下来的分档刷新 —— 盘口 3.5s / 分时 60s / K线 300s。
    写成函数而不是散在页面里，是为了让前端与后台都从同一处取数。
    """
    if not (state or {}).get("trading") and getattr(config, "STOCK_STOP_WHEN_CLOSED", True):
        return 0
    return {"quote": getattr(config, "STOCK_QUOTE_POLL", 3.5),
            "intraday": getattr(config, "STOCK_INTRADAY_POLL", 60),
            "kline": getattr(config, "STOCK_KLINE_POLL", 300),
            "fx": getattr(config, "STOCK_FX_POLL", 3600)}.get(kind, 0)


# ---------------------------------------------------------------- 数据包组装

def _num(x, nd=2):
    """安全取数：None / '-' / NaN / inf 一律回 None，避免 NaN 混进 JSON（前端会 NaN 满屏）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd) if nd is not None else v


def build_kline_pack(rows, bars=None, ma_windows=(5, 10, 20, 50, 60, 144)):
    """K线 + 指标 → 前端图表形状。

    rows: sources.get_kline_period() 的返回（[{date, open, close, high, low, volume}]）。
    返回 {"bars": [...], "ma": {"5": [...], ...}, "boll": {...}, "macd": {...}}，
    其中每个点都是 {"time": "YYYY-MM-DD", "value": x}，可直接喂给 lightweight-charts。
    指标全部用 kchart.calc_indicators 算（MA / BOLL(20,2) / MACD(12,26,9) 一份口径）。
    """
    empty = {"bars": [], "ma": {}, "boll": {}, "macd": {}}
    if not rows:
        return empty
    import pandas as pd
    import kchart

    df = kchart.calc_indicators(pd.DataFrame(rows))
    if bars:
        df = df.tail(int(bars))

    pack = {"bars": [], "ma": {}, "boll": {}, "macd": {}}
    for _, r in df.iterrows():
        t = str(r["date"])[:10]
        pack["bars"].append({"time": t, "open": _num(r["open"]), "high": _num(r["high"]),
                             "low": _num(r["low"]), "close": _num(r["close"]),
                             "volume": _num(r["volume"], 0)})

    def series(col):
        out = []
        for _, r in df.iterrows():
            v = _num(r.get(col))
            if v is not None:
                out.append({"time": str(r["date"])[:10], "value": v})
        return out

    for n in ma_windows:
        col = "ma%d" % n
        if col in df.columns:
            arr = series(col)
            if arr:
                pack["ma"][str(n)] = arr
    for key, col in (("mid", "boll_mid"), ("up", "boll_up"), ("low", "boll_low")):
        if col in df.columns:
            arr = series(col)
            if arr:
                pack["boll"][key] = arr
    for key, col in (("dif", "dif"), ("dea", "dea"), ("hist", "macd")):
        if col in df.columns:
            arr = series(col)
            if arr:
                pack["macd"][key] = arr
    return pack


def build_intraday_pack(raw):
    """分时 → 前端图表形状 {"date","pre_close","points":[{time,value,vol}],"avg":[{time,value}]}。

    time 的做法与主页 generate_chart_data 完全一致：把「当天日期 + 09:30」直接当
    UTC 时间戳传进去，lightweight-charts 按 UTC 渲染整数时间戳，于是显示出来正好是
    09:30 —— 不用改图表库的时区设置，两个页面的横轴口径也一致。
    均价是独立序列（东财给精确值，腾讯兜底给近似值）。
    """
    out = {"date": "", "pre_close": None, "points": [], "avg": []}
    raw = raw or {}
    day_txt = raw.get("date") or time.strftime("%Y-%m-%d")
    try:
        y, m, d = (int(x) for x in day_txt.split("-")[:3])
        day = datetime.date(y, m, d)
    except (TypeError, ValueError):
        day = datetime.date.today()
    out["date"] = day.isoformat()
    out["pre_close"] = _num(raw.get("pre_close"))
    for p in (raw.get("points") or []):
        try:
            hh, mm = int(str(p.get("time"))[:2]), int(str(p.get("time"))[3:5])
            ts = calendar.timegm((day.year, day.month, day.day, hh, mm, 0, 0, 0, 0))
        except (TypeError, ValueError, IndexError):
            continue
        val = _num(p.get("price"))
        if val is None:
            continue
        out["points"].append({"time": ts, "value": val, "vol": _num(p.get("vol"), 0)})
        avg = _num(p.get("avg"))
        if avg is not None:
            out["avg"].append({"time": ts, "value": avg})
    return out


def build_stock_payload(code, period="day", kline_bars=None, with_intraday=True):
    """组装一份完整的个股数据包。每一块独立失败：缺的块留空并记进 errors。

    返回：
      {"code","pure_code","name","period","asof","market": market_state(),
       "quote":{...}, "kline":{bars,ma,boll,macd}, "intraday":{...},
       "errors":{"quote": "..."}}
    """
    tx = tx_code(code)
    payload = {
        "code": tx, "pure_code": tx[2:], "name": "", "period": period,
        "asof": time.strftime("%Y-%m-%d %H:%M:%S"),
        "market": market_state(),
        "quote": {}, "kline": {"bars": [], "ma": {}, "boll": {}, "macd": {}},
        "intraday": {"date": "", "pre_close": None, "points": [], "avg": []},
        "errors": {},
    }

    try:
        q = sources.get_quote_detail([tx]) or {}
        payload["quote"] = q.get(tx[2:]) or q.get(tx) or {}
        payload["name"] = (payload["quote"] or {}).get("name") or ""
    except Exception as e:
        payload["errors"]["quote"] = "%s: %s" % (type(e).__name__, e)

    try:
        rows = sources.get_kline_period(tx, period=period, n=kline_bars)
        payload["kline"] = build_kline_pack(rows, bars=kline_bars)
        if not payload["kline"]["bars"]:
            payload["errors"]["kline"] = "K线为空"
    except Exception as e:
        payload["errors"]["kline"] = "%s: %s" % (type(e).__name__, e)

    if with_intraday:
        try:
            payload["intraday"] = build_intraday_pack(sources.get_intraday(tx))
            if not payload["intraday"]["points"]:
                payload["errors"]["intraday"] = "分时为空"
        except Exception as e:
            payload["errors"]["intraday"] = "%s: %s" % (type(e).__name__, e)
    return payload


# ---------------------------------------------------------------- 自检

def _default_quote_fetcher(tx):
    """默认报价取数：sources 返回的键是不带前缀的代码。"""
    q = sources.get_quote_detail([tx]) or {}
    return q.get(tx[2:]) or q.get(tx) or {}


def _default_intraday_fetcher(tx):
    return build_intraday_pack(sources.get_intraday(tx))


def _default_kline_fetcher(tx, period):
    return build_kline_pack(sources.get_kline_period(tx, period=period))


_KINDS = ("quote", "intraday", "kline")


class _Entry:
    """一只票的缓存条目（数据 + 版本号 + 各档位上次刷新时间）。"""

    __slots__ = ("tx", "name", "periods", "quote", "intraday", "kline",
                 "errors", "at", "fp", "ver", "seen", "locks")

    def __init__(self, tx):
        self.tx = tx
        self.name = ""
        self.periods = set()        # 被页面订阅的K线周期（day / month）
        self.quote = {}
        self.intraday = {"date": "", "pre_close": None, "points": [], "avg": []}
        self.kline = {}             # period -> {bars, ma, boll, macd}
        self.errors = {}
        self.at = {}                # kind -> 上次抓取完成时间
        self.fp = {}                # kind -> 内容指纹（变了才涨版本号）
        self.ver = 0
        self.seen = 0.0             # 最后一次被页面读取的时间（订阅续期）
        # 锁按「档位」分，而不是整只票一把：同票合并只需要保证同一档位不重复抓，
        # 但 K线一次要 1~3 秒，如果占着整票的锁，3.5 秒的报价档会被它堵住变陈旧。
        self.locks = {k: threading.Lock() for k in _KINDS}


class StockHub:
    """后台按档位刷新被订阅的个股，并把结果缓存在内存里。

    三条线程各管一档（报价 3.5s / 分时 60s / K线 300s），互不阻塞：报价最频繁也最容易
    失败，不能拖住分时；K线最重（要算指标），也不能压在报价线程上。

    三条不变量是需求直接要求的，也都写进了离线自检（见 _selftest_hub）：
      1. **同票合并**：HTTP 读取只读缓存，只有冷启动时才同步建一次，且每只票一把锁 ——
         10 个标签页同时刷同一只票，上游仍然只被打一次；之后由档位线程按间隔抓。
      2. **非交易时段停止**：state.trading 为假时后台一次上游都不打，直接睡到下一个
         状态切换点（开盘 / 午休结束）；从交易切到非交易时先「收盘定型」抓最后一次。
      3. **内容没变不涨版本号**：前端带 ver 轮询时，没变化只回 {"unchanged": true}，
         3.5 秒一轮的轮询绝大多数轮次只花几十字节，页面也不用重画。

    取数函数可注入（fetchers）—— 交易时段的行为不能等到开盘才能验证。
    """

    def __init__(self, intervals=None, state_fn=None, fetchers=None,
                 max_codes=None, ttl=None, now=None):
        self._iv = dict(intervals or {})
        self._state_fn = state_fn or market_state
        self._fetch = {"quote": _default_quote_fetcher,
                       "intraday": _default_intraday_fetcher,
                       "kline": _default_kline_fetcher}
        if fetchers:
            self._fetch.update(fetchers)
        self._max = int(max_codes or getattr(config, "STOCK_CACHE_MAX", 30))
        self._ttl = float(ttl if ttl is not None else getattr(config, "STOCK_SUBSCRIBE_TTL", 60))
        self._now = now or time.time
        self._lock = threading.Lock()      # 保护 _codes 这张表本身
        self._codes = {}
        self._stop = threading.Event()
        self._threads = []
        self._last_trading = None
        self.stats = {"build": {k: 0 for k in _KINDS}, "skipped_closed": 0,
                      "final": 0, "evicted": 0}

    # ---- 对外：读 ----
    def read(self, code, period="day", client_ver=0, with_intraday=True):
        """页面读一次。返回 (payload, ver, changed)。

        changed=False 表示客户端手上的版本就是最新的，调用方只回一个 unchanged 空响应即可。
        冷启动（这只票还没缓存）会**同步**抓一次 —— 首次打开要等几秒，但拿到的是完整数据；
        之后的刷新全部由后台档位线程负责，这里永远只读缓存。
        """
        tx = tx_code(code)
        period = period if period in ("day", "month") else "day"
        ent = self._get(tx)
        with self._lock:
            ent.seen = self._now()
        ent.periods.add(period)
        if not ent.at:
            # 冷启动：三块并行抓。锁是按档位分的，三者互不等待，首次打开约等于
            # 「最慢的那一块」，而不是三段之和（实测 5.5s → 2~3s）。
            with ThreadPoolExecutor(max_workers=len(_KINDS)) as ex:
                list(ex.map(lambda k: self._build(ent, k, only_if_missing=True), _KINDS))
        elif period not in ent.kline:
            self._build(ent, "kline", period=period, only_if_missing=True)
        ver = ent.ver
        if client_ver and client_ver == ver:
            return None, ver, False
        return self._payload(ent, period, with_intraday), ver, True

    def health(self):
        """给 /api/stock/health 用的状态快照（订阅了谁、每档多久没刷、有没有报错）。"""
        now, st = self._now(), self._state_fn()
        with self._lock:
            codes = [{
                "code": e.tx, "name": e.name, "ver": e.ver,
                "seen_age": round(now - e.seen, 1) if e.seen else None,
                "age": {k: (round(now - v, 1) if v else None) for k, v in e.at.items()},
                "periods": sorted(e.periods),
                "errors": dict(e.errors),
            } for e in self._codes.values()]
        return {"market": st,
                "intervals": {k: self.interval_for(k, st) for k in _KINDS},
                "subscribe_ttl": self._ttl, "max_codes": self._max,
                "codes": codes,
                "stats": {k: (dict(v) if isinstance(v, dict) else v)
                          for k, v in self.stats.items()}}

    # ---- 对外：生命周期 ----
    def start(self):
        for kind in _KINDS:
            t = threading.Thread(target=self._loop, args=(kind,),
                                 name="stock-" + kind, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    # ---- 档位 ----
    def interval_for(self, kind, state=None):
        """该档位的刷新间隔（秒）。0 = 不要刷新（非交易时段 / 开关关掉）。"""
        if kind in self._iv:
            return float(self._iv[kind])
        return float(poll_seconds(state or self._state_fn(), kind))

    def _loop(self, kind):
        while not self._stop.is_set():
            try:
                wait = self._tick(kind)
            except Exception as e:
                print("[stock] %s 档位异常：%s: %s" % (kind, type(e).__name__, e), flush=True)
                wait = 5.0
            self._stop.wait(max(0.25, min(wait, 300.0)))

    def _tick(self, kind):
        """刷新到期且被订阅的票；返回下一次该醒来的秒数。"""
        st = self._state_fn()
        iv = self.interval_for(kind, st)
        if iv <= 0 or not st.get("trading"):
            self._enter_closed()          # 刚收盘：再抓一次定型，之后不再打上游
            return self._sleep_until_next_state(st)
        with self._lock:
            self._last_trading = True
        now, due, wait = self._now(), [], iv
        for ent in self._active(now):
            left = iv - (now - ent.at.get(kind, 0))
            if left <= 0:
                due.append(ent)
            else:
                wait = min(wait, left)
        for ent in due:
            self._build(ent, kind)
        return max(0.25, wait)

    def _sleep_until_next_state(self, st):
        """睡到下一个状态切换点（开盘 / 午休结束），期间不做任何上游请求。

        上限 300 秒：既不因为系统休眠或改时间睡死，一天也就几次空转，
        换来的是「非交易时段一次上游都不打」。
        """
        self.stats["skipped_closed"] += 1
        try:
            nxt = datetime.datetime.strptime(st.get("next_at") or "",
                                             "%Y-%m-%d %H:%M:%S")
            delta = (nxt - datetime.datetime.now()).total_seconds()
        except (TypeError, ValueError):
            delta = 300.0
        return max(1.0, min(delta, 300.0))

    def _finalize(self):
        """收盘定型：把还在订阅的票再抓一次（盘后固定价格 15:00-15:30 这段也在其中）。"""
        for ent in self._active(self._now()):
            for kind in _KINDS:
                self._build(ent, kind)
        self.stats["final"] += 1

    def _enter_closed(self):
        """交易 → 非交易 的切换处理。

        三条档位线程会在同一瞬间各自发现「刚收盘」，所以定型必须只做一次：
        谁先把标记翻过去谁负责抓，其余线程直接去睡 —— 否则收盘瞬间会被抓三遍。
        """
        with self._lock:
            do = self._last_trading is True
            self._last_trading = False
        if do:
            self._finalize()

    # ---- 内部 ----
    def _active(self, now):
        with self._lock:
            return [e for e in self._codes.values() if (now - e.seen) <= self._ttl]

    def _get(self, tx):
        with self._lock:
            ent = self._codes.get(tx)
            if ent is not None:
                return ent
            if len(self._codes) >= self._max:
                victim = min(self._codes.values(), key=lambda e: e.seen)
                self._codes.pop(victim.tx, None)
                self.stats["evicted"] += 1
            ent = _Entry(tx)
            self._codes[tx] = ent
            return ent

    def _build(self, ent, kind, period=None, only_if_missing=False):
        """抓一档并写回缓存。同票合并就发生在这一层：同一档位的抓取串行，重复的直接跳过。"""
        with ent.locks[kind]:
            if only_if_missing:
                if kind != "kline" and ent.at.get(kind):
                    return False
                if kind == "kline" and self._kline_done(ent, period):
                    return False
            self.stats["build"][kind] += 1
            try:
                if kind == "quote":
                    data = self._fetch["quote"](ent.tx) or {}
                    if data:
                        ent.quote = data
                        ent.name = data.get("name") or ent.name
                        ent.errors.pop("quote", None)
                    else:
                        ent.errors["quote"] = "报价为空"
                    self._touch(ent, "quote", (data.get("price"), data.get("volume_shou"),
                                               data.get("amount_wan"), data.get("time")))
                elif kind == "intraday":
                    data = self._fetch["intraday"](ent.tx) or {}
                    if data.get("points"):
                        ent.intraday = data
                        ent.errors.pop("intraday", None)
                    else:
                        ent.errors["intraday"] = "分时为空"
                    pts = data.get("points") or []
                    self._touch(ent, "intraday",
                                (data.get("date"), data.get("pre_close"), len(pts),
                                 pts[-1].get("value") if pts else None))
                else:
                    periods = [period] if period else (sorted(ent.periods) or ["day"])
                    for pd in periods:
                        pack = self._fetch["kline"](ent.tx, pd) or {}
                        bars = pack.get("bars") or []
                        if bars:
                            ent.kline[pd] = pack
                            ent.errors.pop("kline:" + pd, None)
                        else:
                            ent.errors["kline:" + pd] = "K线为空"
                        ent.at["kline:" + pd] = self._now()
                        self._touch(ent, "kline:" + pd,
                                    (len(bars), bars[0].get("time") if bars else None,
                                     bars[-1].get("time") if bars else None,
                                     bars[-1].get("close") if bars else None))
                    return True
            except Exception as e:
                ent.errors[kind] = "%s: %s" % (type(e).__name__, e)
            ent.at[kind] = self._now()
            return True

    def _kline_done(self, ent, period):
        if period:
            return bool(ent.kline.get(period))
        return bool(ent.kline)

    def _touch(self, ent, kind, fingerprint):
        """内容变了才涨版本号 —— 3.5 秒一轮的轮询靠这个把绝大多数轮次压成空响应。"""
        if ent.fp.get(kind) != fingerprint:
            ent.fp[kind] = fingerprint
            ent.ver += 1

    def _payload(self, ent, period, with_intraday=True):
        ts = ent.at.get("quote") or self._now()
        st = self._state_fn()
        return {
            "code": ent.tx, "pure_code": ent.tx[2:], "name": ent.name, "period": period,
            "asof": datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "market": st,
            # 前端就按这三个值开定时器；非交易时段给 0（STOCK_STOP_WHEN_CLOSED），
            # 页面据此不再轮询 —— 与后台「非交易时段停刷」是同一处配置。
            "poll": {k: self.interval_for(k, st) for k in _KINDS},
            "quote": ent.quote,
            "kline": ent.kline.get(period) or {"bars": [], "ma": {}, "boll": {}, "macd": {}},
            "intraday": ent.intraday if with_intraday
                        else {"date": "", "pre_close": None, "points": [], "avg": []},
            "errors": dict(ent.errors), "ver": ent.ver,
        }

_FAKE_CASES = [
    # (时间, 期望状态, 是否算交易时段)
    ("2026-09-15 09:00:00", "closed", False),
    ("2026-09-15 09:20:00", "pre", True),
    ("2026-09-15 10:00:00", "open", True),
    ("2026-09-15 12:00:00", "lunch", False),
    ("2026-09-15 14:00:00", "open", True),
    ("2026-09-15 15:10:00", "post", True),
    ("2026-09-15 16:00:00", "closed", False),
    ("2026-09-19 10:00:00", "weekend", False),   # 2026-09-19 是周六
]


def _selftest_offline():
    """离线自检：不联网，只验证时段判断、指标口径与数据形状。"""
    bad = []
    for txt, want_state, want_trading in _FAKE_CASES:
        now = datetime.datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")
        st = market_state(now)
        if st["state"] != want_state or st["trading"] != want_trading:
            bad.append("时段 %s → %s/%s，期望 %s/%s"
                       % (txt, st["state"], st["trading"], want_state, want_trading))
    print("时段判断：%d 组用例%s" % (len(_FAKE_CASES), "全部通过" if not bad else "有失败"))

    # 合成 60 根K线：收盘价 10 + i*0.1，足够让 MA5/BOLL(20) 出值
    rows = []
    day = datetime.date(2026, 6, 1)
    for i in range(60):
        c = 10 + i * 0.1
        rows.append({"date": (day + datetime.timedelta(days=i)).isoformat(),
                     "open": round(c - 0.05, 3), "close": round(c, 3),
                     "high": round(c + 0.1, 3), "low": round(c - 0.1, 3),
                     "volume": 1000 + i})
    pack = build_kline_pack(rows)
    n_bars = len(pack["bars"])
    checks = [
        ("bars 长度", n_bars == 60),
        ("ma5 = bars-4", len(pack["ma"].get("5", [])) == n_bars - 4),
        ("boll 三线齐全", all(k in pack["boll"] for k in ("mid", "up", "low"))),
        ("boll 长度", len(pack["boll"]["mid"]) == n_bars - 19),
        ("macd 三线齐全", all(k in pack["macd"] for k in ("dif", "dea", "hist"))),
        ("macd 长度等于 bars", len(pack["macd"]["dif"]) == n_bars),
        ("无 NaN 混入", all(v["value"] is not None for v in pack["ma"]["5"])),
    ]
    for nm, ok in checks:
        if not ok:
            bad.append("K线数据包：" + nm)
    print("K线+指标：%d 项检查%s" % (len(checks), "全部通过" if all(o for _, o in checks) else "有失败"))

    # 分时：time 要变成当天 UTC 时间戳（01:30Z 显示成 09:30），均价单独成序列
    ipack = build_intraday_pack({"date": "2026-09-15", "pre_close": 98.16,
                                 "points": [{"time": "09:30", "price": 98.16, "avg": 98.16, "vol": 163},
                                            {"time": "15:00", "price": 97.79, "avg": 97.93, "vol": 199}]})
    # 09:30 按 UTC 传（见 build_intraday_pack 注释），所以这里也按 09:30 断言
    want_ts = calendar.timegm((2026, 9, 15, 9, 30, 0, 0, 0, 0))
    ip_checks = [
        ("分时点数 = 2", len(ipack["points"]) == 2),
        ("均值线点数 = 2", len(ipack["avg"]) == 2),
        ("time 为当天 UTC 时间戳", ipack["points"][0]["time"] == want_ts),
        ("昨收带出", ipack["pre_close"] == 98.16),
    ]
    for nm, ok in ip_checks:
        if not ok:
            bad.append("分时数据包：" + nm)
    print("分时数据包：%d 项检查%s" % (len(ip_checks), "全部通过" if all(o for _, o in ip_checks) else "有失败"))

    # 非交易时段必须返回 0（前端据此不开定时器）
    closed = market_state(datetime.datetime.strptime("2026-09-15 16:00:00", "%Y-%m-%d %H:%M:%S"))
    open_st = market_state(datetime.datetime.strptime("2026-09-15 14:00:00", "%Y-%m-%d %H:%M:%S"))
    poll_checks = [("收盘后 quote 间隔 = 0", poll_seconds(closed, "quote") == 0),
                   ("盘中 quote 间隔 = 3.5", poll_seconds(open_st, "quote") == 3.5),
                   ("盘中 kline 间隔 = 300", poll_seconds(open_st, "kline") == 300)]
    for nm, ok in poll_checks:
        if not ok:
            bad.append("刷新档位：" + nm)
    print("刷新档位：%d 项检查%s" % (len(poll_checks), "全部通过" if all(o for _, o in poll_checks) else "有失败"))

    if bad:
        print("\n失败项：")
        for b in bad:
            print("  -", b)
        return 1
    print("\n离线自检全部通过。")
    return 0


def _selftest_hub():
    """离线自检后台刷新的三条不变量（假 state_fn + 假取数，不联网）。

    要验的是「什么时候打上游、打了几次」，与真实行情无关；而交易时段的行为更不可能
    等到开盘才能验 —— 所以取数与市场状态都做成可注入的。
    """
    bad = []
    lock = threading.Lock()
    calls = {"quote": 0, "intraday": 0, "kline": 0}
    px = {"v": 10.0}
    flag = {"trading": True}

    def state_fn():
        on = flag["trading"]
        return {"state": "open" if on else "closed", "trading": on,
                "label": "交易中" if on else "已收盘", "next_at": "", "now": ""}

    def fetch_quote(tx):
        with lock:
            calls["quote"] += 1
        time.sleep(0.08)          # 模拟网络耗时，让并发读真的挤在同一时刻
        return {"name": "假票", "price": px["v"], "volume_shou": 100,
                "amount_wan": 1.0, "time": "t", "src": "fake"}

    def fetch_intraday(tx):
        with lock:
            calls["intraday"] += 1
        return {"date": "2026-09-15", "pre_close": 10.0,
                "points": [{"time": 1, "value": 10.0, "vol": 1}],
                "avg": [{"time": 1, "value": 10.0}]}

    def fetch_kline(tx, period):
        with lock:
            calls["kline"] += 1
        return {"bars": [{"time": "2026-09-14", "close": 9.9},
                         {"time": "2026-09-15", "close": px["v"]}],
                "ma": {"5": [{"time": "2026-09-15", "value": 10.0}]},
                "boll": {}, "macd": {"dif": [{"time": "2026-09-15", "value": 0.1}]}}

    hub = StockHub(intervals={"quote": 0.25, "intraday": 0.25, "kline": 0.25},
                   state_fn=state_fn, ttl=60,
                   fetchers={"quote": fetch_quote, "intraday": fetch_intraday,
                             "kline": fetch_kline})

    # 1) 同票合并：10 个线程同时冷读同一只票，上游只应被打一次
    box, ths = [], []
    for _ in range(10):
        ths.append(threading.Thread(target=lambda: box.append(hub.read("600941")[1])))
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    cold = (calls["quote"], calls["intraday"], calls["kline"])
    ok = cold == (1, 1, 1)
    print("同票合并：10 个并发冷读 → 上游 quote=%d intraday=%d kline=%d（期望 1/1/1）%s"
          % (cold + ("  ✓" if ok else "  ✗",)))
    if not ok:
        bad.append("同票合并失败：%s" % (cold,))

    v1 = hub.read("600941")[1]
    hub.start()

    # 2) 档位刷新 + 内容没变就不涨版本号：1.3 秒内读 40 次，上游只该按 0.25s 档位抓几次
    base_q = calls["quote"]
    unchanged = 0
    for _ in range(40):
        _, ver, changed = hub.read("600941", client_ver=v1)
        if not changed:
            unchanged += 1
        time.sleep(0.03)
    tier = calls["quote"] - base_q
    ok2 = tier <= 8 and unchanged >= 30
    print("档位刷新：1.3 秒内读 40 次 → 上游只抓 %d 次（档位 0.25s），其中 %d 次回 unchanged%s"
          % (tier, unchanged, "  ✓" if ok2 else "  ✗"))
    if not ok2:
        bad.append("档位刷新/版本号异常：上游 %d 次、unchanged %d 次" % (tier, unchanged))

    # 3) 内容变了才涨版本号并回数据
    px["v"] = 11.0
    time.sleep(0.6)
    payload, ver2, changed = hub.read("600941", client_ver=v1)
    ok3 = changed and ver2 > v1 and (payload or {}).get("quote", {}).get("price") == 11.0
    print("内容变化：价格 10.0 → 11.0 后 ver %d → %d，changed=%s，回包价格=%s%s"
          % (v1, ver2, changed, (payload or {}).get("quote", {}).get("price"),
             "  ✓" if ok3 else "  ✗"))
    if not ok3:
        bad.append("内容变化未触发版本号/回包")

    # 4) 收盘：定型恰好一次，之后完全不打上游
    flag["trading"] = False
    time.sleep(0.6)
    mid = dict(calls)
    time.sleep(0.5)
    after = dict(calls)
    ok4 = (hub.stats["final"] == 1 and after == mid and hub.stats["skipped_closed"] >= 1)
    print("收盘停止：定型 %d 次，休眠计数 %d，0.5 秒后上游调用 %s → %s%s"
          % (hub.stats["final"], hub.stats["skipped_closed"], mid, after,
             "  ✓" if ok4 else "  ✗"))
    if not ok4:
        bad.append("非交易时段未停刷：定型 %d 次，%s → %s" % (hub.stats["final"], mid, after))

    hub.stop()
    if bad:
        print("\n失败项：")
        for b in bad:
            print("  -", b)
        return 1
    print("\n后台刷新自检全部通过。")
    return 0


def _live(code, dump_json=False):
    """联网自检：抓一只票的真实数据打摘要（页面接上去之前，先看数据够不够用）。"""
    t0 = time.time()
    pl = build_stock_payload(code, period="day")
    q = pl["quote"] or {}
    k = pl["kline"]
    iv = pl["intraday"]
    if dump_json:
        print(json.dumps(pl, ensure_ascii=False, indent=2))
        return 0
    print("代码 %s  名称 %s  市场 %s（%s）  用时 %.1fs"
          % (pl["code"], pl["name"] or "?", pl["market"]["state"], pl["market"]["label"],
             time.time() - t0))
    if q:
        print("报价 现价 %s  涨跌 %s(%s%%)  开 %s  高 %s  低 %s  昨收 %s"
              % (q.get("price"), q.get("change"), q.get("pct"), q.get("open"),
                 q.get("high"), q.get("low"), q.get("prev_close")))
        print("     量 %s 手  额 %s 万  换手 %s%%  量比 %s  均价 %s  源 %s"
              % (q.get("volume_shou"), q.get("amount_wan"), q.get("turnover"),
                 q.get("volume_ratio"), q.get("avg_price"), q.get("src")))
        print("     总市值 %s 亿  流通 %s 亿  市盈动 %s  市净 %s  涨停 %s  跌停 %s"
              % (q.get("market_cap_yi"), q.get("float_cap_yi"), q.get("pe_dyn"),
                 q.get("pb"), q.get("limit_up"), q.get("limit_down")))
        bids = q.get("bids") or []
        asks = q.get("asks") or []
        print("     买盘 %s" % (" / ".join("%s×%s" % (p, v) for p, v in bids) or "—"))
        print("     卖盘 %s" % (" / ".join("%s×%s" % (p, v) for p, v in asks) or "—"))
        if q.get("post_volume"):
            print("     盘后固定价格 %s  量 %s  额 %s"
                  % (q.get("post_price"), q.get("post_volume"), q.get("post_amount")))
    print("日K  %d 根（%s → %s）+ MA %s + BOLL %s + MACD %s"
          % (len(k["bars"]),
             k["bars"][0]["time"] if k["bars"] else "-",
             k["bars"][-1]["time"] if k["bars"] else "-",
             ",".join(sorted(k["ma"], key=int)) or "无",
             "有" if k["boll"] else "无", "有" if k["macd"] else "无"))
    print("分时 %d 点  均价线 %d 点  昨收 %s"
          % (len(iv["points"]), len(iv["avg"]), iv["pre_close"]))
    if pl["errors"]:
        print("失败块：%s" % json.dumps(pl["errors"], ensure_ascii=False))
    else:
        print("三块数据全部就绪。")

    ml = build_stock_payload(code, period="month", with_intraday=False)
    mb = ml["kline"]["bars"]
    print("月K  %d 根（%s → %s）  MA/BOLL/MACD：%s/%s/%s"
          % (len(mb), mb[0]["time"] if mb else "-", mb[-1]["time"] if mb else "-",
             ",".join(sorted(ml["kline"]["ma"], key=int)) or "无",
             "有" if ml["kline"]["boll"] else "无",
             "有" if ml["kline"]["macd"] else "无"))
    if ml["errors"]:
        print("月K失败块：%s" % json.dumps(ml["errors"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--hub-test" in args:
        sys.exit(_selftest_hub())
    if "--live" in args:
        i = args.index("--live")
        code = args[i + 1] if len(args) > i + 1 else "600941"
        sys.exit(_live(code, dump_json="--json" in args))
    sys.exit(_selftest_offline())
