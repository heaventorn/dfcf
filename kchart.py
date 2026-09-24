# -*- coding: utf-8 -*-
"""
行情序列取数与指标计算模块
================================================
- K 线：日 / 周 / 月（前复权），返回 DataFrame
- 分时：当日分钟价格 + 分钟成交量 + 昨收
- 指标：MA5/10/20/50/60/144、BOLL(20,2)、MACD(12,26,9)

数据源：腾讯行情接口（proxy.finance.qq.com / web.ifzq.gtimg.cn）

绘图不在这里：主页用 lightweight-charts（内联在 home.py），个股页用同一套
（stock.html）；曾经的 matplotlib「生成 base64 PNG」链路随旧综合报告一起删除，
连带去掉了 matplotlib / numpy / io 三个依赖。
"""
import requests
import pandas as pd

import config
MA_COLORS = {"MA5": "#f0a500", "MA10": "#e91e63", "MA50": "#2563eb", "MA144": "#7c3aed"}

TX_H = dict(config.HEADERS)
TX_H.pop("Referer", None)


def _tx_get(url, timeout=15):
    r = requests.get(url, headers=TX_H, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ================================================================ 数据抓取

# 周期名 → 腾讯返回里的前复权键 / 不复权键（fqkline 接口按周期返回不同 key）
_PERIOD_KEYS = {
    "day": ("qfqday", "day"),
    "week": ("qfqweek", "week"),
    "month": ("qfqmonth", "month"),
}


def fetch_kline(code="sh000001", n=260, period="day"):
    """腾讯K线（前复权）。period: day / week / month。返回 DataFrame: date/open/close/high/low/volume。

    周期名与返回键一一对应：day→qfqday / week→qfqweek / month→qfqmonth
    （不带 fq 的 day/week/month 是不复权，这里不走 —— 全站口径统一为前复权）。

    注意月K的最后一条：腾讯给的是**当天日期**（如 2026-09-15），不是月末，
    所以当月这根K线的 time 每天都会变；前端按 time 建索引时要把它当"最新一根"
    而不是"这个月的定值"，缓存也要按日失效。
    """
    key_q, key_raw = _PERIOD_KEYS.get(period, _PERIOD_KEYS["day"])
    # 2026-09-24 实测：web.ifzq.gtimg.cn/appstock/app/fqkline/get 已一律返回 HTTP 501，
    # 换到 proxy.finance.qq.com 的同名接口（param 末位仍是 qfq，返回键名保持一致）。
    url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
           f"?param={code},{period},,,{n},qfq")
    j = _tx_get(url)
    node = j.get("data", {}).get(code, {})
    rows = node.get(key_q) or node.get(key_raw) or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["date", "open", "close", "high", "low", "volume"]
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna()


def fetch_minute(code="sh000001"):
    """腾讯当日分时。返回 (times, prices, vols, pre_close)；失败返回 None。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    j = _tx_get(url)
    node = j.get("data", {}).get(code, {})
    d = node.get("data") or {}
    inner = d.get("data")
    pre_close = None
    qt = node.get("qt") or {}
    qq = (qt.get(code) or []) if isinstance(qt, dict) else None
    if qq and len(qq) > 4 and str(qq[4]) not in ("", "0"):
        try:
            pre_close = float(qq[4])
        except (TypeError, ValueError):
            pre_close = None
    if isinstance(inner, str):
        inner = inner.split(",")
    if not isinstance(inner, list) or not inner:
        return None

    times, prices, vols = [], [], []
    for item in inner:
        if isinstance(item, str):
            parts = item.strip().split()
        else:
            parts = item
        if not parts or len(parts) < 3:
            continue
        t = parts[0].strip()
        try:
            p = float(parts[1])
            v = float(parts[2])
        except (TypeError, ValueError):
            continue
        times.append(t)
        prices.append(p)
        vols.append(v)
    if not times:
        return None

    # 每分钟成交量 = 累计量差分
    vol_per_min = [max(0.0, vols[0])]
    for i in range(1, len(vols)):
        vol_per_min.append(max(0.0, vols[i] - vols[i - 1]))
    return times, prices, vol_per_min, pre_close


# ================================================================ 指标计算

def calc_indicators(df):
    """计算 MA5/10/20/50/60/144、BOLL(20,2) 与 MACD(12,26,9)。返回 df 副本。

    列名就是前端「指标切换」要用的键：切 MA / BOLL 时按列名直接取序列，不用在前端
    重算。BOLL 用 (20,2)，与 tech.calc_indicators（空中飞人指数那条链路）保持同一口径，
    两个页面对同一只票的布林带不会打架。
    """
    out = df.copy()
    c = out["close"]
    for n in (5, 10, 20, 50, 60, 144):
        out[f"ma{n}"] = c.rolling(n).mean()
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    out["boll_mid"] = mid
    out["boll_up"] = mid + 2 * std
    out["boll_low"] = mid - 2 * std
    out["ema12"] = c.ewm(span=12, adjust=False).mean()
    out["ema26"] = c.ewm(span=26, adjust=False).mean()
    out["dif"] = out["ema12"] - out["ema26"]
    out["dea"] = out["dif"].ewm(span=9, adjust=False).mean()
    out["macd"] = 2 * (out["dif"] - out["dea"])
    return out


if __name__ == "__main__":
    # 联网冒烟：三种周期各取一次，分时取一次，再把指标列打出来
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for p in ("day", "week", "month"):
        df = fetch_kline("sh000001", 260 if p == "day" else 120, p)
        print("%-5s K线 %4d 根  %s → %s" % (
            p, len(df),
            str(df["date"].iloc[0])[:10] if len(df) else "-",
            str(df["date"].iloc[-1])[:10] if len(df) else "-"))
    if len(df):
        cols = [c for c in calc_indicators(df).columns
                if c not in ("date", "open", "close", "high", "low", "volume")]
        print("指标列:", ", ".join(cols))
    m = fetch_minute("sh000001")
    print("分时 %d 点，昨收 %s" % (len(m[0]) if m else 0, m[3] if m else "-"))
