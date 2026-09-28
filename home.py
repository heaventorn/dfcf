# -*- coding: utf-8 -*-
"""主页数据聚合（页面长什么样见 dashboard.py）
================================================
这个模块只干「把数据算出来」这一件事：采集结果 + 持仓 + 自选 + 日历 + 空中飞人
指数，聚合成一份 payload；再提供上证分时 / 日K 序列。最后由 main.py 调
build_home 生成 output/index.html。

页面本身（版式、配色、任务栏、图表）已经搬到 dashboard.py，这里的两个
build_* 只是转发：
    output/index.html      大盘总览
    output/portfolio.html  自选与持仓

对外接口:
    build_payload(...)        聚合主页需要的全部数据
    payload_json(...)         序列化为可嵌入 <script> 的 JSON
    generate_chart_data(...)  上证分时 / 日K 序列
    build_home(...)           生成主页 HTML（转给 dashboard）
    build_assets(...)         生成自选与持仓页（转给 dashboard）
"""
import calendar
import datetime
import json
import math
import os

import config
import econ_calendar


# ---------------------------------------------------------------- 小工具

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _s(v, default=""):
    return default if v is None else str(v)


# ---------------------------------------------------------------- 各窗口 payload

def _market_payload(data):
    """左上窗口:主要指数 + 涨跌家数 + 情绪 + 涨停跌停 + 个股涨幅榜。"""
    data = data or {}
    indices = data.get("indices") or []
    breadth = data.get("breadth") or {}
    zt = data.get("limit_up") or {}
    dt = data.get("limit_down") or {}
    st = data.get("stock_up") or []

    label, desc = "", ""
    try:
        from utils import judge_market
        label, desc = judge_market(breadth, indices)
    except Exception:
        label, desc = "", ""

    amount_total = sum(_num(x.get("amount")) for x in indices if isinstance(x, dict))

    def _lbc_top(bucket, n=6):
        items = [x for x in (bucket.get("items") or []) if isinstance(x, dict)]
        items.sort(key=lambda x: _num(x.get("lbc"), 1), reverse=True)
        return [{"name": _s(x.get("name")), "lbc": int(_num(x.get("lbc"), 1))} for x in items[:n]]

    return {
        "indices": [{
            "name": _s(x.get("name")),
            "price": _s(x.get("price")),
            "pct": _num(x.get("change_pct")),
            "amount": _num(x.get("amount")),
        } for x in indices if isinstance(x, dict)],
        "breadth": {
            "up": int(_num(breadth.get("up"))),
            "down": int(_num(breadth.get("down"))),
            "flat": int(_num(breadth.get("flat"))),
            "total": int(_num(breadth.get("total"))),
        },
        "sentiment": {"label": label, "desc": desc},
        "amount_total": round(amount_total, 2),
        "limit_up": {"n": len(zt.get("items") or []), "top": _lbc_top(zt)},
        "limit_down": {"n": len(dt.get("items") or []), "top": _lbc_top(dt)},
        "stock_up": [{
            "name": _s(x.get("name")), "code": _s(x.get("code")),
            "price": _s(x.get("price")), "pct": _num(x.get("change_pct")),
            "amount": _num(x.get("amount")),
        } for x in st[:10] if isinstance(x, dict)],
    }


def _has_stock_page(code):
    """这个标的有没有「个股详情页」可看。

    逆回购（沪 204xxx / 深 1318xx、1319xx）没有有意义的 K 线，点进去只有一条直线；
    场外基金没有实时行情与分时。这两类不给入口 —— 入口给了却点进去是空页，比没有更糟。
    """
    c = str(code or "").strip()
    if not c:
        return False
    return not (c.startswith("204") or c.startswith("1318") or c.startswith("1319"))


def _positions_payload(portfolio_data):
    """右上窗口之一:真实持仓(positions.json)。为空时给出空态提示。"""
    pos = ((portfolio_data or {}).get("positions") or {})
    items = pos.get("items") or []
    if not items:
        return {
            "empty": True,
            "note": _s(pos.get("error")) or "positions.json 暂无持仓,可在持仓管理页添加后重新运行",
            "manage_url": "http://127.0.0.1:8765",
        }
    return {
        "empty": False,
        "manage_url": "http://127.0.0.1:8765",
        "total_mv": _num(pos.get("total_mv")),
        "total_cost": _num(pos.get("total_cost")),
        "total_pnl": _num(pos.get("total_pnl")),
        "total_pct": pos.get("total_pct"),
        "total_day": _num(pos.get("total_day")),
        "items": [{
            "name": _s(x.get("name")), "code": _s(x.get("code")),
            "shares": x.get("shares"), "price": x.get("price"), "cost": x.get("cost"),
            "mv": x.get("mv"), "day_pnl": x.get("day_pnl"),
            "pnl": x.get("pnl"), "pnl_pct": x.get("pnl_pct"),
            "note": _s(x.get("note")),
            "page": _has_stock_page(x.get("code")),
        } for x in items],
    }


def _watchlist_payload(portfolio_data):
    """右上窗口之二:「我的自选」——按 etf / stock / other 三组给行情。

    与「我的持仓」的区别：这里**只跟行情**，没有成本价、不算盈亏。
    场外基金给的是净值（带 nav_date）；国债逆回购的"价格"就是年化利率（is_rate）。
    """
    wl = ((portfolio_data or {}).get("watchlist") or {})
    groups = []
    for key, label in (("etf", "ETF"), ("stock", "股票"), ("other", "其他")):
        rows = []
        for x in (wl.get(key) or []):
            if not isinstance(x, dict):
                continue
            rows.append({
                "name": _s(x.get("name")), "code": _s(x.get("code")),
                "kind": _s(x.get("kind")), "note": _s(x.get("note")),
                "price": x.get("price"), "pct": x.get("pct"),
                "nav_date": _s(x.get("nav_date")),
                "is_rate": bool(x.get("is_rate")), "is_nav": bool(x.get("is_nav")),
                # 场外基金（只有净值）不给个股页入口：那边没有分时与 K 线
                "page": (not bool(x.get("is_nav"))) and _has_stock_page(x.get("code")),
            })
        groups.append({"key": key, "label": label, "rows": rows})
    return {
        "groups": groups,
        "total": sum(len(g["rows"]) for g in groups),
        "error": _s(wl.get("error")),
        "asof": _s(wl.get("time")),
        "manage_url": "http://127.0.0.1:8765",
    }


def _airman_payload(res, refs):
    """右下窗口:空中飞人指数(总分 / 等级 / 五维 / 计分信号 / 参考指标)。"""
    if not res:
        return {"empty": True, "note": "本次未计算出空中飞人指数(抓取失败或未运行)"}
    dims = [{
        "key": _s(d.get("key")), "name": _s(d.get("name")),
        "weight": _num(d.get("weight")), "score": d.get("score"),
    } for d in (res.get("dims") or [])]
    signals = [{
        "key": _s(s.get("key")), "name": _s(s.get("name")), "score": s.get("score"),
        "status": _s(s.get("status")), "text": _s(s.get("raw_text")),
    } for s in (res.get("signals") or [])]
    return {
        "empty": False,
        "total": round(_num(res.get("total")), 1),
        "level": _s(res.get("level_name")),
        "color": _s(res.get("level_color")),
        "time": _s(res.get("time")),
        "dims": dims,
        "signals": signals,
        "refs": [{"key": _s(r.get("key")), "name": _s(r.get("name")),
                  "display": _s(r.get("display")), "src": _s(r.get("src"))} for r in (refs or [])],
    }


# ---------------------------------------------------------------- 对外入口(payload)

def _source_status():
    """各信源最近一次抓取条数(来自 feeds.SOURCE_STATUS;-1 表示该源失败)。"""
    try:
        import feeds
        return dict(feeds.SOURCE_STATUS)
    except Exception:
        return {}


def _window_label():
    """时间窗文案（与 events.window_label() 保持一致）：“今日” / “近 N 天”。"""
    if getattr(config, "NEWS_TODAY_ONLY", False):
        return "今日"
    return "近%d天" % int(getattr(config, "NEWS_WINDOW_DAYS", 7))


def build_payload(data, events=None, airman_res=None, airman_refs=None,
                  portfolio_data=None, calendar_res=None, window_days=3):
    """聚合主页四个窗口所需的全部数据(纯数据,不含 HTML/JS)。"""
    if portfolio_data is None:
        portfolio_data = (data or {}).get("portfolio")
    if calendar_res is None:
        try:
            calendar_res = econ_calendar.fetch_calendar(events)
        except Exception as e:
            calendar_res = {"source": "none", "note": "日历采集异常(%s)" % e.__class__.__name__, "days": []}

    return {
        "asof": _s((data or {}).get("time")),
        "market": _market_payload(data),
        "calendar": calendar_res or {},
        "positions": _positions_payload(portfolio_data),
        "watchlist": _watchlist_payload(portfolio_data),
        "airman": _airman_payload(airman_res, airman_refs),
        "meta": {
            "events": len(events or []),
            "window_days": window_days,
            "window_label": _window_label(),
            "output_dir": config.OUTPUT_DIR,
            "sources": _source_status(),
            # 实时新闻：前端按 poll_seconds 轮询 /api/news，后端每 refresh_seconds 秒重抓
            "live": {
                "poll_seconds": int(getattr(config, "NEWS_POLL_SECONDS", 60)),
                "refresh_seconds": int(getattr(config, "NEWS_REFRESH_SECONDS", 300)),
                "feed_rows": int(getattr(config, "NEWS_FEED_ROWS", 300)),
                "limit": int(getattr(config, "NEWS_LIMIT", 1000)),
                "window_days": int(getattr(config, "NEWS_WINDOW_DAYS", 7)),
                "today_only": bool(getattr(config, "NEWS_TODAY_ONLY", False)),
                "api": "/api/news",
            },
        },
    }


def payload_json(payload):
    """把 payload 序列化为可安全嵌入 <script> 的 JSON 文本。"""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------- 行情序列(供 lightweight-charts)

def generate_chart_data(code="sh000001", daily_bars=125):
    """取上证「分时 + 日K(含 MA5/10/50/144)」序列,交给前端 lightweight-charts 渲染。

    返回 {"minute": {"date","pre_close","points":[{time,value,vol}]},
          "daily":  {"bars":[{time,open,high,low,close,volume}], "ma": {"5":[{time,value}] ...}}}
    任一步失败都返回空结构(前端显示提示),不影响主页生成。
    """
    out = {"minute": {"date": "", "pre_close": None, "points": []},
           "daily": {"bars": [], "ma": {}}}
    now = datetime.datetime.now()
    day = datetime.date(now.year, now.month, now.day)

    # ---- 分时(时间戳按 UTC 传入,前端即按 09:30 显示) ----
    try:
        import kchart
        m = kchart.fetch_minute(code)
        if m:
            times, prices, vols, pre_close = m
            points = []
            for i, t in enumerate(times):
                try:
                    hh, mm = int(str(t)[:2]), int(str(t)[2:4])
                    ts = calendar.timegm((day.year, day.month, day.day, hh, mm, 0, 0, 0, 0))
                    points.append({"time": ts, "value": round(float(prices[i]), 2),
                                   "vol": round(float(vols[i]), 2)})
                except (TypeError, ValueError, IndexError):
                    continue
            out["minute"] = {"date": day.isoformat(),
                             "pre_close": round(float(pre_close), 2) if pre_close else None,
                             "points": points}
    except Exception:
        pass

    # ---- 日K + 均线 ----
    try:
        import kchart
        kdf = kchart.fetch_kline(code, 260)
        if kdf is not None and len(kdf):
            df = kchart.calc_indicators(kdf).tail(daily_bars)
            bars = []
            for _, r in df.iterrows():
                bars.append({
                    "time": str(r["date"])[:10],
                    "open": round(float(r["open"]), 2), "high": round(float(r["high"]), 2),
                    "low": round(float(r["low"]), 2), "close": round(float(r["close"]), 2),
                    "volume": float(r["volume"]),
                })
            ma = {}
            for n in (5, 10, 50, 144):
                col = "ma%d" % n
                if col not in df.columns:
                    continue
                arr = []
                for _, r in df.iterrows():
                    try:
                        v = float(r[col])
                    except (TypeError, ValueError):
                        continue
                    if math.isnan(v) or math.isinf(v):
                        continue
                    arr.append({"time": str(r["date"])[:10], "value": round(v, 2)})
                if arr:
                    ma[str(n)] = arr
            out["daily"] = {"bars": bars, "ma": ma}
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 生成主页
#
# 页面长什么样，全部交给 dashboard.py：
#   output/index.html      大盘总览（无地球 · 左侧任务栏 · 满屏面板）
#   output/portfolio.html  自选与持仓
# 历史上这里有一份约 1150 行的 HOME_CSS + HOME_JS，负责在地球旁边摆四个信息窗口；
# 那套 DOM / JS 已经整体退役，改成「服务端渲染 + 一个定时刷新快讯的小脚本」。


def _load_market_snapshot():
    """读 output/latest_market.json（主页 / 自选页都只依赖这份快照）。"""
    path = os.path.join(config.OUTPUT_DIR, "latest_market.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def build_home(events, payload, out_path, assets_prefix="assets/", chart_data=None,
               market=None, poll_seconds=None):
    """生成主页 output/index.html（保持旧签名：main.py 不用改）。"""
    import dashboard
    if chart_data is None:
        chart_data = generate_chart_data()
    if market is None:
        market = _load_market_snapshot()
    if poll_seconds is None:
        poll_seconds = int(getattr(config, "NEWS_POLL_SECONDS", 60))
    return dashboard.build_overview_html(payload, market, chart_data, out_path,
                                         poll_seconds=poll_seconds)


def build_assets(payload, out_path, market=None, poll_seconds=None):
    """生成 output/portfolio.html（自选与持仓）。"""
    import dashboard
    if market is None:
        market = _load_market_snapshot()
    if poll_seconds is None:
        poll_seconds = int(getattr(config, "NEWS_POLL_SECONDS", 60))
    return dashboard.build_assets_html(payload, market, out_path,
                                       poll_seconds=poll_seconds)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    base = os.path.dirname(os.path.abspath(__file__))
    raw_path = os.path.join(base, "output", "latest_market.json")
    market = {}
    try:
        with open(raw_path, encoding="utf-8") as f:
            market = json.load(f)
        print("已载入:", raw_path)
    except Exception as e:
        print("载入 latest_market.json 失败(将测空态):", e)

    pl = build_payload(market, events=[], airman_res=None, airman_refs=None, portfolio_data=None)
    print("asof:", pl["asof"])
    print("指数:", [(x["name"], x["pct"]) for x in pl["market"]["indices"]][:6])
    print("广度:", pl["market"]["breadth"], "| 情绪:", pl["market"]["sentiment"])
    print("涨停/跌停:", pl["market"]["limit_up"]["n"], "/", pl["market"]["limit_down"]["n"],
          "| 连板:", pl["market"]["limit_up"]["top"][:3])
    print("个股榜前3:", [(x["name"], x["pct"]) for x in pl["market"]["stock_up"][:3]])
    print("持仓:", {k: v for k, v in pl["positions"].items() if k != "items"})
    print("自选:", [(g["label"], len(g["rows"])) for g in pl["watchlist"]["groups"]],
          "| 合计", pl["watchlist"]["total"])
    print("飞人:", {k: v for k, v in pl["airman"].items() if k != "signals"})
    print("日历来源:", (pl["calendar"] or {}).get("source"),
          "| 今日条数:", ((pl["calendar"] or {}).get("days") or [{}])[0].get("total"))

    cd = generate_chart_data()
    print("分时点数:", len(cd["minute"]["points"]), "| 昨收:", cd["minute"]["pre_close"],
          "| 日K根数:", len(cd["daily"]["bars"]), "| MA:", sorted(cd["daily"]["ma"].keys()))
    print("JSON 长度:", len(payload_json(pl)))

    if "--probe" in sys.argv:
        probe = os.path.join(base, "output", "_home_probe.html")
        build_home([], pl, probe, assets_prefix="../assets/", chart_data=cd)
        print("已生成主页探针:", probe)
