# -*- coding: utf-8 -*-
"""
多数据源适配层（sources）
========================
解决单一数据源（如东财 push2）高频请求被风控/限流的问题：
  - 每种数据配置多个来源，按优先级依次尝试可用来源；
  - 任一来源出现「数据异常」（断连、超时、空数据、结构异常、风控页）即自动切换到下一个；
  - 异常来源进入冷却期（按连续失败次数指数退避），冷却结束自动恢复；
  - 全程记录每个来源的健康状态，可在运行结束输出健康报告。

数据来源代号：
  em       东方财富 push2（实时行情）
  emdelay  东方财富 push2delay（延迟镜像，返回格式与 push2 完全一致，主源被限流时顶上）
  push2ex  东方财富 涨停/跌停池（无镜像）
  newsapi  东方财富 7x24 快讯
  tx       腾讯行情（qt.gtimg.cn / web.ifzq.gtimg.cn / proxy.finance.qq.com）
  sina     新浪财经（hq.sinajs.cn / Market_Center / getKLineData / 7x24 直播）
  fund10   天天基金 F10（分红送配）

对外公开函数（供 collector / dividend 调用，返回结构与原单源版本一致）：
  get_indices() / get_stock_rank() / get_sector_rank() / get_sector_rank_fall()
  get_market_breadth() / get_limit_pool() / get_flash_news()
  get_realtime_quotes() / get_kline() / get_dividends()
"""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config


# ================================================================ 基础工具

class DataAnomaly(Exception):
    """数据异常：请求本身成功，但返回内容不符合预期（空 / 结构错 / 风控页）。

    soft=True 表示「软异常」（请求成功但内容为空/校验不过）：
    多为数据源侧偶发，冷却很短，便于整体重试时快速再试；
    soft=False 表示「硬异常」（断连 / 非200 / 风控页）：
    多为被限流/风控，冷却按连续失败指数退避，自动切到下一来源。
    """

    def __init__(self, msg, soft=False):
        super().__init__(msg)
        self.soft = soft


def _empty(msg):
    """软异常：内容为空/不足/结构不对（请求本身是成功的）。"""
    return DataAnomaly(msg, soft=True)


def _to_float(v):
    """安全转 float；空/停牌等非数值返回 None。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mk_session(referer=None):
    h = dict(config.HEADERS)
    if referer:
        h["Referer"] = referer
    s = requests.Session()
    s.headers.update(h)
    return s


# 各来源独立会话（互不影响）
_em_sess = _mk_session("https://quote.eastmoney.com/")
_emdelay_sess = _mk_session("https://quote.eastmoney.com/")
_push2ex_sess = _mk_session("https://quote.eastmoney.com/")
_tx_sess = _mk_session()
_sina_sess = _mk_session("https://finance.sina.com.cn/")
_fund_sess = _mk_session("https://fundf10.eastmoney.com/")
_news_sess = _mk_session()


def load_cookies():
    """把已保存的登录 Cookie 载入东财系列会话（行情接口无需登录，但有登录态更稳）。"""
    try:
        with open(config.COOKIE_FILE, "r", encoding="utf-8") as f:
            ck = json.load(f)
        _em_sess.cookies.update(ck)
        _emdelay_sess.cookies.update(ck)
        _push2ex_sess.cookies.update(ck)
        return True
    except Exception:
        return False


# 风控特征文本（命中即视为数据异常，触发切源）
_BOT_MARKS = (
    "访问过于频繁", "请求过于频繁", "访问受限", "验证码", "封禁",
    "forbidden", "verify", "captcha", "freq", "too many", "rate limit",
)


def _is_bot_page(text):
    t = (text or "").lower()
    return any(m.lower() in t for m in _BOT_MARKS)


def _req_json(sess, url, params=None, timeout=None):
    """发 GET 请求并解析 JSON；连接失败 / 非200 / 风控页 / 解析失败都抛 DataAnomaly。"""
    timeout = timeout or config.TIMEOUT
    try:
        r = sess.get(url, params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise DataAnomaly(f"网络错误({type(e).__name__})") from e
    if r.status_code != 200:
        raise DataAnomaly(f"HTTP {r.status_code}")
    text = r.text or ""
    if _is_bot_page(text):
        raise DataAnomaly("疑似风控页面")
    try:
        return r.json()
    except Exception:
        raise DataAnomaly("JSON 解析失败")


def _req_text(sess, url, params=None, timeout=None, encoding=None):
    """发 GET 请求并返回文本；连接失败 / 非200 / 风控页抛 DataAnomaly。"""
    timeout = timeout or config.TIMEOUT
    try:
        r = sess.get(url, params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise DataAnomaly(f"网络错误({type(e).__name__})") from e
    if r.status_code != 200:
        raise DataAnomaly(f"HTTP {r.status_code}")
    if encoding:
        r.encoding = encoding
    text = r.text or ""
    if _is_bot_page(text):
        raise DataAnomaly("疑似风控页面")
    return text


# ================================================================ 来源健康状态

class _Src:
    __slots__ = ("name", "fails", "cooldown_until", "ok", "fail")

    def __init__(self, name):
        self.name = name
        self.fails = 0          # 连续失败次数
        self.cooldown_until = 0.0
        self.ok = 0
        self.fail = 0


_sources = {}
_lock = threading.Lock()


def _src(name):
    with _lock:
        if name not in _sources:
            _sources[name] = _Src(name)
        return _sources[name]


def _usable(name):
    return time.time() >= _src(name).cooldown_until


def _mark_ok(name):
    s = _src(name)
    with _lock:
        s.fails = 0
        s.cooldown_until = 0.0
        s.ok += 1


def _mark_fail(name, soft=False):
    s = _src(name)
    with _lock:
        s.fails += 1
        s.fail += 1
        if soft:
            # 软异常：请求成功但内容为空/校验不过——是内容问题而非被限流，
            # 不设冷却（避免污染同一轮后续请求），交给 rounds 重试机制兜底。
            s.cooldown_until = 0.0
        else:
            # 硬异常：断连/非200/风控页——多为被限流/风控，指数退避冷却并切源
            cd = min(config.SOURCE_COOLDOWN_BASE * (2 ** (s.fails - 1)),
                     config.SOURCE_COOLDOWN_MAX)
            s.cooldown_until = time.time() + cd


def health_report():
    """输出各来源健康状态，用于运行结束汇总。"""
    lines = []
    for name in sorted(_sources):
        s = _src(name)
        state = "冷却中" if time.time() < s.cooldown_until else "正常"
        lines.append(f"  {name:<9} {state}  成功={s.ok} 失败={s.fail} 连续失败={s.fails}")
    return "\n".join(lines)


def _switch_log(key, name, reason):
    print(f"    [切源] {key}: {name} 数据异常（{reason}），切换下一来源")


# ================================================================ 多源取数统一入口

def fetch(key, providers, validate=None, rounds=2):
    """
    按优先级依次尝试多个来源，命中「数据异常」自动切换；全部失败可整体重试。

    providers: [(来源名, 无参可调用)]，可调用返回数据，异常抛 DataAnomaly。
    validate : 可选，对返回数据做结构校验 fn(data)->bool，False 视为数据异常。
    rounds   : 全部来源失败后的整体重试轮数（含首次）。
    返回 (来源名, 数据)；全部失败返回 (None, None)。
    """
    for rnd in range(rounds):
        for name, fn in providers:
            if not _usable(name):
                continue  # 冷却期，跳过
            try:
                data = fn()
                if validate is not None and not validate(data):
                    raise _empty("结构校验未通过")
                _mark_ok(name)
                return name, data
            except DataAnomaly as e:
                _mark_fail(name, soft=e.soft)
                _switch_log(key, name, str(e))
            except Exception as e:
                _mark_fail(name)
                _switch_log(key, name, f"{type(e).__name__}: {e}")
            time.sleep(config.SOURCE_SWITCH_DELAY)
        if rnd < rounds - 1:
            time.sleep(config.SOURCE_ALLFAIL_DELAY)
    return None, None


# ================================================================ 东财 push2 / push2delay

_EM = "push2.eastmoney.com"
_EMD = "push2delay.eastmoney.com"


def _em_diff(data):
    d = (data or {}).get("data") or {}
    return d.get("diff") or []


def _em_indices(host):
    """东财指数行情（f2价格 f3涨跌幅 f4涨跌额 f6成交额）。"""
    secids = ",".join(s for s, _ in config.INDEX_SECIDS)
    data = _req_json(_em_sess if host == _EM else _emdelay_sess,
                     f"https://{host}/api/qt/ulist.np/get",
                     params={"fltt": 2, "invt": 2, "fields": config.INDEX_FIELDS,
                             "secids": secids})
    diff = _em_diff(data)
    if len(diff) < 3:
        raise _empty("指数返回不足")
    return [{
        "name": x.get("f14"), "price": x.get("f2"),
        "change": x.get("f4"), "change_pct": x.get("f3"),
        "amount": x.get("f6"),
    } for x in diff]


def _em_stock_rank(host, fid, order, limit):
    """东财个股榜（fid=f3 涨跌幅 / f62 成交额；order desc/asc）。"""
    po = 1 if order == "desc" else 0
    data = _req_json(_em_sess if host == _EM else _emdelay_sess,
                     f"https://{host}/api/qt/clist/get",
                     params={"pn": 1, "pz": limit, "po": po, "np": 1,
                             "fltt": 2, "invt": 2, "fid": fid,
                             "fs": config.ALL_A_FS, "fields": config.STOCK_FIELDS})
    diff = _em_diff(data)
    if not diff:
        raise _empty("个股榜为空")
    out = []
    for x in diff:
        try:
            amt = abs(float(x.get("f62"))) if x.get("f62") is not None else None
        except (TypeError, ValueError):
            amt = None
        out.append({"code": x.get("f12"), "name": x.get("f14"),
                    "price": x.get("f2"), "change_pct": x.get("f3"),
                    "amount": amt})
    return out


def _em_sector_rank(host, kind, limit):
    """东财板块涨跌幅榜（industry/concept）。"""
    fs = "m:90+t:2" if kind == "industry" else "m:90+t:3"
    data = _req_json(_em_sess if host == _EM else _emdelay_sess,
                     f"https://{host}/api/qt/clist/get",
                     params={"pn": 1, "pz": limit, "po": 1, "np": 1,
                             "fltt": 2, "invt": 2, "fid": "f3",
                             "fs": fs, "fields": config.SECTOR_FIELDS})
    diff = _em_diff(data)
    if not diff:
        raise _empty("板块榜为空")
    return [{
        "name": x.get("f14"), "change_pct": x.get("f3"),
        "amount": x.get("f62"), "up": x.get("f104"),
        "down": x.get("f105"), "lead_stock": x.get("f128"),
    } for x in diff]


def _em_sector_fall(host, limit):
    """东财行业板块跌幅榜（升序）。"""
    data = _req_json(_em_sess if host == _EM else _emdelay_sess,
                     f"https://{host}/api/qt/clist/get",
                     params={"pn": 1, "pz": limit, "po": 0, "np": 1,
                             "fltt": 2, "invt": 2, "fid": "f3",
                             "fs": "m:90+t:2", "fields": config.SECTOR_FIELDS})
    diff = _em_diff(data)
    if not diff:
        raise _empty("板块跌幅榜为空")
    return [{"name": x.get("f14"), "change_pct": x.get("f3"),
             "lead_stock": x.get("f128")} for x in diff]


def _em_fetch_page(host, sess, pn):
    """东财全市场单页涨跌幅；失败返回 (pn, None)，交由上层补拉。"""
    try:
        data = _req_json(sess, f"https://{host}/api/qt/clist/get",
                         params={"pn": pn, "pz": config.PAGE_SIZE, "po": 1, "np": 1,
                                 "fltt": 2, "invt": 2, "fid": "f3",
                                 "fs": config.ALL_A_FS, "fields": "f3"})
        diff = (data or {}).get("data", {}).get("diff") or []
        vals = [_to_float(x.get("f3")) for x in diff if isinstance(x, dict)]
        return pn, vals or None
    except Exception:
        return pn, None


def _em_breadth(host):
    """东财全市场涨跌家数（分页并发 + 失败补拉 + 覆盖率校验）。"""
    sess = _em_sess if host == _EM else _emdelay_sess
    first = _req_json(sess, f"https://{host}/api/qt/clist/get",
                      params={"pn": 1, "pz": config.PAGE_SIZE, "po": 1, "np": 1,
                              "fltt": 2, "invt": 2, "fid": "f3",
                              "fs": config.ALL_A_FS, "fields": "f3"})
    total = (first or {}).get("data", {}).get("total") or 0
    if total < 100:
        raise _empty(f"全市场 total 异常({total})")
    pages = (total + config.PAGE_SIZE - 1) // config.PAGE_SIZE
    pct_list = [_to_float(x.get("f3")) for x in ((first or {}).get("data", {}).get("diff") or [])]

    missing = list(range(2, pages + 1))
    for _ in range(3):  # 最多补拉 3 轮
        if not missing:
            break
        with ThreadPoolExecutor(max_workers=config.WORKERS) as ex:
            futs = {ex.submit(_em_fetch_page, host, sess, pn): pn for pn in missing}
            new_missing = []
            for fut in as_completed(futs):
                pn, vals = fut.result()
                if vals:
                    pct_list.extend(vals)
                else:
                    new_missing.append(pn)
                time.sleep(config.PAGE_DELAY)
        missing = new_missing
        if missing:
            print(f"    [补拉] 东财分页有 {len(missing)} 页失败，稍候补拉 ...")
            time.sleep(2.5)

    # 覆盖率校验：拿到 <60% 视为数据异常（触发切源）
    if len(pct_list) < total * 0.6:
        raise _empty(f"广度覆盖率不足({len(pct_list)}/{total})")

    up = sum(1 for p in pct_list if p is not None and p > 0)
    down = sum(1 for p in pct_list if p is not None and p < 0)
    return {"total": len(pct_list), "up": up, "down": down, "flat": len(pct_list) - up - down}


def _em_limit_pool(kind):
    """东财涨停/跌停池（唯一来源）。"""
    url = ("https://push2ex.eastmoney.com/getTopicZTPool" if kind == "zt"
           else "https://push2ex.eastmoney.com/getTopicDTPool")
    params = {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
              "Pageindex": 0, "pagesize": 200,
              "sort": "fbt:asc" if kind == "zt" else "fund:asc",
              "date": time.strftime("%Y%m%d")}
    data = _req_json(_push2ex_sess, url, params=params)
    d = (data or {}).get("data")
    if not isinstance(d, dict):
        raise _empty("涨跌停池 data 缺失")
    pool = d.get("pool") or []
    items = [{
        "code": x.get("c"), "name": x.get("n"),
        "price": (x.get("p") or 0) / 1000,
        "change_pct": x.get("zdp"), "amount": x.get("amount"),
        "lbc": x.get("lbc"), "first_time": x.get("fbt"), "last_time": x.get("lbt"),
    } for x in pool]
    return {"count": d.get("tc", len(items)), "items": items}


def _em_news(limit):
    """东财 7x24 财经快讯（剥离 var ajaxResult= 前缀）。"""
    url = ("https://newsapi.eastmoney.com/kuaixun/v1/"
           f"getlist_102_ajaxResult_{limit}_1_.html")
    text = _req_text(_news_sess, url)
    if "ajaxResult=" in text:
        text = text.split("=", 1)[1].rstrip(";").strip()
    data = json.loads(text)
    lives = (data or {}).get("LivesList") or []
    if not lives:
        raise _empty("快讯为空")
    return [{
        "time": x.get("showtime"), "title": (x.get("title") or "").strip(),
        "digest": (x.get("digest") or "").strip(), "url": x.get("url_w"),
    } for x in lives]


def _em_kline(secid, adj):
    """东财日线K线（klt=101；adj: qfq前复权 / bfq不复权）。"""
    data = _req_json(_em_sess, "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                     params={"secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
                             "fields2": "f51,f52,f53,f54,f55,f56,f57",
                             "klt": 101, "fqt": 1 if adj == "qfq" else 0,
                             "beg": "20200101", "end": "20261231"})
    klines = ((data or {}).get("data") or {}).get("klines") or []
    if len(klines) < 30:
        raise _empty("K线不足")
    rows = []
    for k in klines:
        parts = k.split(",")
        if len(parts) < 6:
            continue
        rows.append({"date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
                     "high": float(parts[3]), "low": float(parts[4]), "volume": float(parts[5])})
    if len(rows) < 30:
        raise _empty("K线不足")
    return rows


# ================================================================ 腾讯

_INDEX_TX = {"1.000001": "sh000001", "0.399001": "sz399001", "0.399006": "sz399006",
             "1.000688": "sh000688", "0.899050": "bj899050", "1.000300": "sh000300"}


def _tx_parse_quotes(codes):
    """腾讯行情批量解析（指数与个股同格式）。返回 {code: {name,price,prev_close,change,pct,high,low,amount_wan}}。"""
    r = _req_text(_tx_sess, "https://qt.gtimg.cn/q=" + ",".join(codes), encoding="gbk")
    result = {}
    for line in r.split(";"):
        line = line.strip()
        if "=" not in line or '"' not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('"')
        p = body.split("~")
        if len(p) < 35:
            continue
        result[p[2]] = {
            "name": p[1], "price": _to_float(p[3]), "prev_close": _to_float(p[4]),
            "change": _to_float(p[31]) if len(p) > 31 else None,
            "pct": _to_float(p[32]) if len(p) > 32 else None,
            "high": _to_float(p[33]) if len(p) > 33 else None,
            "low": _to_float(p[34]) if len(p) > 34 else None,
            "amount_wan": _to_float(p[37]) if len(p) > 37 else None,  # 成交额(万元)
        }
    return result


def _tx_indices():
    codes = [_INDEX_TX[s] for s, _ in config.INDEX_SECIDS]
    q = _tx_parse_quotes(codes)
    out = []
    for secid, name in config.INDEX_SECIDS:
        tx = _INDEX_TX[secid]
        item = q.get(tx) or q.get(tx[2:])
        if not item or item.get("price") is None:
            continue
        out.append({
            "name": item.get("name") or name, "price": item.get("price"),
            "change": item.get("change"), "change_pct": item.get("pct"),
            "amount": (item.get("amount_wan") * 10000) if item.get("amount_wan") is not None else None,
        })
    if len(out) < 3:
        raise _empty("腾讯指数返回不足")
    return out


def _tx_amount_rank(limit):
    """腾讯成交额榜（getBoardRankList sort_type=turnover）。"""
    r = _req_json(_tx_sess, "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList",
                  params={"board_code": "aStock", "sort_type": "turnover", "direct": "down",
                          "offset": 0, "count": limit})
    if r.get("code") != 0:
        raise _empty(f"腾讯榜单接口错误({r.get('msg')})")
    ls = (r.get("data") or {}).get("rank_list") or []
    if not ls:
        raise _empty("腾讯成交额榜为空")
    out = []
    for x in ls:
        code = re.sub(r"^(sh|sz|bj)", "", x.get("code") or "")
        turnover = _to_float(x.get("turnover"))
        out.append({
            "code": code, "name": x.get("name"), "price": x.get("zxj"),
            "change_pct": x.get("zdf"),
            "amount": turnover * 10000 if turnover is not None else None,
        })
    return out


def _tx_kline(tx_code, adj):
    """腾讯日线K线（web.ifzq.gtimg.cn，分段拉取合并）。
    腾讯不复权返回的 key 是 day（前复权才是 qfqday），两个都尝试。"""
    qfq = "qfq" if adj == "qfq" else "bfq"
    keys = ["qfqday"] if qfq == "qfq" else ["day", "bfqday"]
    segments = [("2026-12-31", "2024-01-01"), ("2024-01-01", "2021-06-01"),
                ("2021-06-01", "2019-06-01"), ("2019-06-01", "2016-06-01")]
    all_rows = {}
    for end, beg in segments:
        data = _req_json(_tx_sess, "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                         params={"param": f"{tx_code},day,{beg},{end},640,{qfq}"})
        d = data.get("data") or {}
        if not isinstance(d, dict):
            continue
        node = d.get(tx_code) or {}
        rows_raw = []
        for k in keys:
            if isinstance(node.get(k), list):
                rows_raw = node[k]
                break
        for k in rows_raw:
            if len(k) < 6:
                continue
            try:
                all_rows[k[0]] = {"date": k[0], "open": float(k[1]), "close": float(k[2]),
                                  "high": float(k[3]), "low": float(k[4]), "volume": float(k[5])}
            except (TypeError, ValueError):
                continue
    rows = sorted(all_rows.values(), key=lambda x: x["date"])
    if len(rows) < 30:
        raise _empty("腾讯K线不足")
    return rows


# ================================================================ 新浪

_INDEX_SINA = {"1.000001": "sh000001", "0.399001": "sz399001", "0.399006": "sz399006",
               "1.000688": "sh000688", "0.899050": "bj899050", "1.000300": "sh000300"}


def _sina_indices():
    """新浪指数（s_ 简版：名称,现价,涨跌额,涨跌幅,成交量,成交额；成交额单位不可靠置空）。"""
    codes = [_INDEX_SINA[s] for s, _ in config.INDEX_SECIDS]
    text = _req_text(_sina_sess, "https://hq.sinajs.cn/list=" + ",".join("s_" + c for c in codes),
                     encoding="gbk")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line or '"' not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('";')
        p = body.split(",")
        if len(p) < 4 or _to_float(p[1]) is None:
            continue
        out.append({"name": p[0], "price": _to_float(p[1]), "change": _to_float(p[2]),
                    "change_pct": _to_float(p[3]), "amount": None})
    if len(out) < 3:
        raise _empty("新浪指数返回不足")
    return out


def _sina_hqnode(page, num, sort, asc):
    """新浪行情中心列表接口（node=hs_a 沪深京 A 股）。"""
    data = _req_text(_sina_sess,
                     "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                     "Market_Center.getHQNodeData",
                     params={"page": page, "num": num, "sort": sort, "asc": asc,
                             "node": "hs_a", "_s_r_a": "init"},
                     encoding="gbk")
    try:
        arr = json.loads(data)
    except Exception:
        raise _empty("新浪列表解析失败")
    return arr or []


def _sina_stock_rank(sort, order, limit):
    """新浪个股榜（sort=changepercent/amount；order desc/asc）。"""
    asc = 0 if order == "desc" else 1
    arr = _sina_hqnode(1, limit, sort, asc)
    if not arr:
        raise _empty("新浪个股榜为空")
    return [{
        "code": x.get("code"), "name": x.get("name"),
        "price": x.get("trade"), "change_pct": x.get("changepercent"),
        "amount": x.get("amount"),
    } for x in arr]


def _sina_breadth():
    """新浪全市场涨跌家数（分页统计，最后兜底）。"""
    total = up = down = flat = 0
    page = 1
    while True:
        arr = _sina_hqnode(page, 100, "symbol", 1)
        if not arr:
            break
        for x in arr:
            cp = _to_float(x.get("changepercent"))
            total += 1
            if cp is None or cp == 0:
                flat += 1
            elif cp > 0:
                up += 1
            else:
                down += 1
        if len(arr) < 100:
            break
        page += 1
        if page > 200:
            break
        time.sleep(0.15)
    if total < 100:
        raise _empty("新浪广度数据不足")
    return {"total": total, "up": up, "down": down, "flat": flat}


def _sina_kline(symbol, adj):
    """新浪日线K线（不复权价，datalen=1023≈近4年）。"""
    data = _req_text(_sina_sess,
                     "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                     "CN_MarketData.getKLineData",
                     params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": 1023},
                     encoding="gbk")
    try:
        arr = json.loads(data)
    except Exception:
        raise _empty("新浪K线解析失败")
    if not arr:
        raise _empty("新浪K线为空")
    return [{"date": x.get("day"), "open": _to_float(x.get("open")), "close": _to_float(x.get("close")),
             "high": _to_float(x.get("high")), "low": _to_float(x.get("low")),
             "volume": _to_float(x.get("volume"))} for x in arr]


def _sina_quotes(codes):
    """新浪个股/ETF实时行情（全量格式，字段与腾讯同构）。codes 形如 sh510880。"""
    text = _req_text(_sina_sess, "https://hq.sinajs.cn/list=" + ",".join(codes),
                     encoding="gbk")
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line or '"' not in line:
            continue
        m = re.match(r"var hq_str_(\w+)=", line)
        if not m:
            continue
        body = line.split("=", 1)[1].strip().strip('";')
        p = body.split(",")
        if len(p) < 6:
            continue
        code = m.group(1)[2:]  # 去掉 sh/sz 前缀
        result[code] = {
            "name": p[0], "price": _to_float(p[3]) if len(p) > 3 else None,
            "prev_close": _to_float(p[2]) if len(p) > 2 else None,
            "change": _to_float(p[31]) if len(p) > 31 else None,
            "pct": _to_float(p[32]) if len(p) > 32 else None,
            "high": _to_float(p[33]) if len(p) > 33 else None,
            "low": _to_float(p[34]) if len(p) > 34 else None,
            "amount_wan": _to_float(p[37]) if len(p) > 37 else None,
        }
    return result


def _sina_news(limit):
    """新浪 7x24 快讯（zhibo feed）。"""
    data = _req_json(_sina_sess, "https://zhibo.sina.com.cn/api/zhibo/feed",
                     params={"page": 1, "page_size": limit, "zhibo_id": 152,
                             "tag_id": 0, "dire": "f", "dpc": 1, "pagesize": limit})
    feed = (((data or {}).get("result") or {}).get("data") or {}).get("feed") or {}
    items = feed.get("list") or []
    if not items:
        raise _empty("新浪快讯为空")
    out = []
    for x in items:
        title = re.sub(r"<[^>]+>", "", x.get("rich_text") or "").strip()
        if not title:
            continue
        out.append({"time": x.get("create_time") or "", "title": title[:100],
                    "digest": "", "url": x.get("url") or ""})
        if len(out) >= limit:
            break
    if not out:
        raise _empty("新浪快讯解析为空")
    return out


# ================================================================ 天天基金

def _fund10_dividends(code):
    """天天基金 F10 分红送配（HTML 解析）。"""
    text = _req_text(_fund_sess, f"https://fundf10.eastmoney.com/fhsp_{code}.html",
                     encoding="utf-8")
    rows = re.findall(r"<tr>(.*?)</tr>", text, re.S)
    out = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        m = re.search(r"每10份派现金([\d.]+)元", "".join(cells))
        if not m:
            continue
        per10 = float(m.group(1))
        if len(cells) >= 5:
            out.append({"year": cells[0], "record_date": cells[1], "ex_date": cells[2],
                        "per_10": per10, "per_share": per10 / 10.0, "pay_date": cells[4]})
    return out


# ================================================================ 公开 API（面向 collector / dividend）

def get_indices():
    """主要指数行情，返回 [{name, price, change, change_pct, amount}]。"""
    providers = [
        ("em", lambda: _em_indices(_EM)),
        ("emdelay", lambda: _em_indices(_EMD)),
        ("tx", _tx_indices),
        ("sina", _sina_indices),
    ]
    _, data = fetch("指数", providers,
                    validate=lambda d: len(d) >= 3 and sum(1 for x in d if x.get("price") is not None) >= 3)
    return data or []


def get_stock_rank(fid="f3", order="desc", limit=10):
    """个股榜：fid=f3 涨跌幅 / f62 成交额；order desc/asc。返回 [{code,name,price,change_pct,amount}]。"""
    providers = [
        ("em", lambda: _em_stock_rank(_EM, fid, order, limit)),
        ("emdelay", lambda: _em_stock_rank(_EMD, fid, order, limit)),
    ]
    if fid == "f62":
        providers.append(("sina", lambda: _sina_stock_rank("amount", order, limit)))
        providers.append(("tx", lambda: _tx_amount_rank(limit)))
    else:
        providers.append(("sina", lambda: _sina_stock_rank("changepercent", order, limit)))
    _, data = fetch(f"个股榜({fid}:{order})", providers, validate=lambda d: len(d) >= 1)
    return data or []


def get_sector_rank(kind="industry", limit=10):
    """板块涨跌幅榜。返回 [{name, change_pct, amount, up, down, lead_stock}]。"""
    providers = [
        ("em", lambda: _em_sector_rank(_EM, kind, limit)),
        ("emdelay", lambda: _em_sector_rank(_EMD, kind, limit)),
    ]
    _, data = fetch(f"板块榜({kind})", providers, validate=lambda d: len(d) >= 1)
    return data or []


def get_sector_rank_fall(limit=5):
    """行业板块跌幅榜。返回 [{name, change_pct, lead_stock}]。"""
    providers = [
        ("em", lambda: _em_sector_fall(_EM, limit)),
        ("emdelay", lambda: _em_sector_fall(_EMD, limit)),
    ]
    _, data = fetch("板块跌幅榜", providers, validate=lambda d: len(d) >= 1)
    return data or []


def get_market_breadth():
    """全市场涨跌家数。返回 {total, up, down, flat}。"""
    providers = [
        ("em", lambda: _em_breadth(_EM)),
        ("emdelay", lambda: _em_breadth(_EMD)),
        ("sina", _sina_breadth),
    ]
    _, data = fetch("涨跌家数", providers, validate=lambda d: (d.get("total") or 0) >= 100, rounds=1)
    return data or {"total": 0, "up": 0, "down": 0, "flat": 0}


def get_limit_pool(kind="zt"):
    """涨停池(zt)/跌停池(dt)。返回 {count, items}。"""
    _, data = fetch(f"涨跌停池({kind})", [("push2ex", lambda: _em_limit_pool(kind))])
    return data or {"count": 0, "items": []}


def get_flash_news(limit=30):
    """7x24 财经快讯。返回 [{time, title, digest, url}]。"""
    providers = [
        ("newsapi", lambda: _em_news(limit)),
        ("sina", lambda: _sina_news(limit)),
    ]
    _, data = fetch("财经快讯", providers, validate=lambda d: len(d) >= 1)
    return data or []


def get_realtime_quotes(codes):
    """实时行情（腾讯主源 / 新浪兜底）。codes 形如 [sh510880, ...]。
    返回 {code: {name, price, prev_close, change, pct, high, low, amount_wan}}。"""
    def _q_tx():
        return _tx_parse_quotes(codes)

    def _q_sina():
        return _sina_quotes(codes)

    def _valid(d):
        return any(v and v.get("price") is not None for v in d.values())

    _, data = fetch("实时行情", [("tx", _q_tx), ("sina", _q_sina)], validate=_valid)
    return data or {}


def get_us_quotes(codes):
    """美股实时行情（腾讯源）。

    codes 形如 [usNVDA, usGOOGL]（us + 股票代码）。
    注意：美股在新浪为独立格式（gb_ 前缀），不做新浪兜底，仅腾讯源。
    腾讯返回的行情 code 字段形如 NVDA.OQ，这里按股票代码前缀匹配后，
    按传入顺序返回 [{code, name, price, pct}]。
    """
    def _q_tx():
        q = _tx_parse_quotes(codes)
        out = []
        for c in codes:
            base = c[2:] if c.startswith("us") else c  # usNVDA -> NVDA
            hit = None
            for k, v in q.items():
                if k.split(".")[0] == base:
                    hit = v
                    break
            if hit:
                out.append({"code": base, "name": hit.get("name"),
                            "price": hit.get("price"), "pct": hit.get("pct")})
        return out

    def _valid(d):
        return len(d) >= 1 and any(x and x.get("price") is not None for x in d)

    _, data = fetch("美股行情", [("tx", _q_tx)], validate=_valid)
    return data or []


def get_kline(tx_code, secid):
    """日线K线（腾讯主源 / 东财 / 新浪兜底）。
    返回 (tech, yld)：
      - tech：前复权，用于均线/分位等技术指标
      - yld ：不复权，用于历史股息率（贴近除息日真实价格）"""
    def _k_tx(adj):
        return _tx_kline(tx_code, adj)

    def _k_em(adj):
        return _em_kline(secid, adj)

    def _k_sina(adj):
        symbol = tx_code if tx_code.startswith(("sh", "sz", "bj")) else tx_code
        return _sina_kline(symbol, adj)

    def _valid(d):
        return isinstance(d, list) and len(d) >= 30

    tech = fetch("K线(前复权)", [("tx", lambda: _k_tx("qfq")),
                                ("em", lambda: _k_em("qfq")),
                                ("sina", lambda: _k_sina("qfq"))],
                 validate=_valid)[1] or []
    yld = fetch("K线(不复权)", [("tx", lambda: _k_tx("bfq")),
                              ("em", lambda: _k_em("bfq")),
                              ("sina", lambda: _k_sina("bfq"))],
                validate=_valid)[1] or []
    if not yld:
        yld = tech
    return tech, yld


def get_dividends(code):
    """分红送配记录（天天基金 F10）。返回 [{year, record_date, ex_date, per_10, per_share, pay_date}]。
    fundf10 偶发返回「暂无分红信息」（软异常），rounds=3 会整体重试最多 3 次；
    3 轮仍为空则按数据源真实情况处理（该基金暂无分红记录）。"""
    _, data = fetch(f"分红({code})", [("fund10", lambda: _fund10_dividends(code))],
                    validate=lambda d: len(d) >= 1, rounds=3)
    return data or []
