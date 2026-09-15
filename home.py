# -*- coding: utf-8 -*-
"""主页(3D 地球指挥台)数据聚合与侧栏生成
================================================
主页 = 中间 3D 地球 + 四周四个信息窗口(左侧上下、右侧上下),由 main.py 生成
output/index.html 并自动打开;重新运行脚本即可刷新为最新快照。

侧栏窗口:
  左上 📈 大A行情概况(指数 / 涨跌家数 / 成交额 / 涨停跌停 / 个股榜)
       + 行情图「分时 / 日K · 成交量」——TradingView lightweight-charts 渲染,
         白底券商风格(红涨绿跌),支持滚轮缩放、拖动平移、十字光标;
         库的 TradingView 水印已用 attributionLogo=false + CSS + DOM 清理三重移除
  左下 📰 新闻 / 📅 日历(今日热点置顶 + 近 3 天事件;日历为今日/明日会议与经济数据)
  右上 ⭐ 我的持仓(positions.json,空态给出管理入口)+ 📌 我的自选(watchlist.json;ETF/股票/其他三组,只跟行情、不记成本)
  右下 🪂 空中飞人指数(总分 / 等级 / 五维 / 参考指标 / 计分信号)

交互:
  点新闻或地球光点 → 相机飞向并放大该地点,右栏切换为「新闻详情 / 相关新闻」
  点国家 → 相机飞向并放大该国,右栏显示「该国近 3 天新闻列表」;点「← 返回」复原侧栏

对外接口:
    build_payload(...)        聚合四个窗口的数据
    payload_json(...)         序列化为可嵌入 <script> 的 JSON
    generate_chart_data(...)  上证分时 / 日K 序列(供 lightweight-charts 渲染)
    build_home(...)           生成主页 HTML
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


# ---------------------------------------------------------------- 主页样式

HOME_CSS = """
<style>
  /* ===== 设计令牌 · 沉稳专业 =====
     层次靠「背景亮度分级」而不是彩色边框;强调色收敛到单一橙色,红绿只留给涨跌语义。
     以后调整配色只需要动这一块。 */
  :root{
    --bg-0:#0a0e14; --bg-1:#111721; --bg-2:#18202c; --bg-3:#202b3a;
    --line-1:#1b2430; --line-2:#28323f;
    --fg-0:#e9eef6; --fg-1:#a9b5c6; --fg-2:#78879b; --fg-3:#556072;
    --accent:#ff9f1c; --accent-dim:rgba(255,159,28,.14); --accent-line:rgba(255,159,28,.42);
    --up:#f0453a; --down:#19a35f; --flat:#8b93a1;
    --r-1:4px; --r-2:8px; --r-3:12px;
    --dur-fast:120ms; --dur:220ms; --dur-slow:420ms;
    --ease:cubic-bezier(.2,.7,.2,1);
  }
  /* 字体与数字:显式声明字体栈(原来没声明,全站吃系统默认),
     并全局开 tabular-nums 让数字等宽 —— 专业感的骨架其实在这里 */
  body{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",
        "PingFang SC","Hiragino Sans GB",sans-serif;
        font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1;
        -webkit-font-smoothing:antialiased; }

  /* ===== 主页指挥台:三列布局(左 356 / 中自适应 / 右 376) ===== */
  #globeViz { left: 372px; right: 392px; }
  #title { left: 372px; right: 392px; top: 10px; max-width: none; text-align: center; }
  #title h1 { font-size: 18px; }
  #title .sub { font-size: 11.5px; }
  #hotWrap { display: none; }
  #tip { right: 406px; bottom: 96px; }
  #sidePanel { right: 14px; top: 14px; bottom: 14px; width: 376px; max-width: none; z-index: 20; }
  #sidePanel .x { font-size: 0; }
  #sidePanel .x::after { content: "← 返回"; font-size: 12px; }

  .homecol { position: fixed; z-index: 13; top: 14px; bottom: 14px; width: 356px;
             display: flex; flex-direction: column; gap: 10px; pointer-events: auto; }
  #colL { left: 14px; }
  #colR { right: 14px; width: 376px; }
  .win { background: var(--bg-1); border: 1px solid var(--line-1); border-radius: var(--r-3);
         box-shadow: 0 6px 22px rgba(0,0,0,.38); display: flex; flex-direction: column;
         overflow: hidden; min-height: 0; }
  .wh { display: flex; align-items: center; gap: 6px; padding: 8px 12px 7px;
        border-bottom: 1px solid var(--line-1); flex: none; }
  .wh .wt { font-size: 13px; font-weight: 700; color: var(--fg-0); white-space: nowrap;
            letter-spacing: .2px; }
  .wh .ws { flex: 1; min-width: 0; color: var(--fg-2); font-size: 11px; text-align: right;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .wfold { cursor: pointer; color: var(--fg-1); border: 1px solid var(--line-2);
           border-radius: var(--r-1);
           width: 18px; height: 18px; text-align: center; line-height: 16px; font-size: 12px;
           flex: none; user-select: none; }
  .wfold:hover { color: var(--fg-0); border-color: var(--accent-line); }
  .wb { padding: 8px 12px 10px; overflow-y: auto; overflow-x: hidden; }
  .wb::-webkit-scrollbar { width: 6px; }
  .wb::-webkit-scrollbar-thumb { background: var(--line-2); border-radius: 3px; }
  .tab { cursor: pointer; color: var(--fg-1); border: 1px solid var(--line-2); border-radius: 999px;
         padding: 1px 8px; font-size: 11px; user-select: none; }
  .tab.on { background: var(--accent-dim); border-color: var(--accent-line); color: var(--accent); }
  .pos { color: var(--up); }
  .neg { color: var(--down); }
  .flat { color: var(--flat); }
  .hintxt { color: var(--fg-2); font-size: 11px; }
  .suninfo { color: var(--fg-1); font-size: 11px; margin-left: 8px; }
  .empty { color: var(--fg-1); font-size: 12.5px; line-height: 1.9; padding: 4px 0; }
  .btnlink { display: inline-block; margin-top: 6px; color: var(--accent); text-decoration: none;
             background: var(--accent-dim); border: 1px solid var(--accent-line);
             border-radius: var(--r-2); padding: 5px 10px; font-size: 12px; }
  .btnlink:hover { background: rgba(255,159,28,.26); }

  .idxgrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
  .idxcard { background: var(--bg-2); border: 1px solid var(--line-1); border-radius: var(--r-2);
             padding: 6px 8px; }
  .idxcard .in { color: var(--fg-2); font-size: 11px; }
  .idxcard .ip { font-size: 15px; font-weight: 700; color: var(--fg-0); line-height: 1.35; }
  .idxcard .ic { font-size: 11.5px; }
  .brow { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; margin: 8px 0 4px;
          font-size: 12px; }
  .brow .bl { color: var(--fg-2); font-size: 11px; }
  .brow .bv { color: var(--fg-0); font-weight: 600; }
  .sent { margin: 6px 0; font-size: 12.5px; color: var(--fg-1); }
  .lbt { font-size: 11.5px; color: var(--fg-1); margin: 4px 0 2px; line-height: 1.9; }
  .lbt i { font-style: normal; color: var(--accent); margin-left: 3px; }
  .sub2 { color: var(--fg-1); font-size: 11.5px; font-weight: 700; margin: 8px 0 2px; }
  .sec2 { color: var(--fg-1); font-size: 11.5px; font-weight: 700; margin: 10px 0 3px;
          border-top: 1px dashed var(--line-1); padding-top: 7px; }
  .tb { width: 100%; border-collapse: collapse; font-size: 12px; }
  .tb td { padding: 3px 2px; border-bottom: 1px solid var(--line-1); color: var(--fg-0); }
  .tb td:first-child { max-width: 118px; overflow: hidden; text-overflow: ellipsis;
                       white-space: nowrap; }
  /* 持仓 / 自选：整行可点，跳个股详情页。行会随刷新重建，所以样式挂在 tr 上、
     点击走容器上的事件委托（见 HOME_JS 的 bindStockRows）。 */
  .tb tr.rowlink { cursor: pointer; }
  .tb tr.rowlink:hover td { background: rgba(255,255,255,.025); }
  .tb tr.rowlink:hover td:first-child { color: var(--accent); }
  .tb tr.rowlink td:first-child::after {
    content: "›"; color: var(--fg-3); font-size: 13px; margin-left: 5px;
    opacity: 0; transition: opacity var(--dur-fast) var(--ease);
  }
  .tb tr.rowlink:hover td:first-child::after { opacity: 1; }
  /* 行内的名字是真链接，但不显示下划线（下划线只在悬停时出现） */
  .tb .rowlink-a { color: inherit; text-decoration: none; }
  .tb tr.rowlink:hover .rowlink-a { text-decoration: underline; }

  /* ===== 行情图:改为深色(底色走 token;canvas 那层由 chartOpts 设成透明) ===== */
  #tvBox { margin-top: 8px; background: var(--bg-2); border: 1px solid var(--line-1);
           border-radius: var(--r-2); padding: 6px 4px 2px; }
  #tvMain { height: 206px; }
  #tvVol { height: 64px; border-top: 1px solid var(--line-1); }
  #tvMain, #tvVol { width: 100%; }
  #tvBox .tvline { display: flex; align-items: flex-start; gap: 4px; }
  #tvLegend { width: 58px; flex: none; padding: 2px 0 0; font-size: 10.5px; line-height: 1.8; }
  #tvLegend .lg { display: flex; align-items: center; gap: 3px; color: var(--fg-2); white-space: nowrap; }
  #tvLegend .lg i { width: 8px; height: 8px; border-radius: 2px; flex: none; }
  #tvLegend .lg b { font-weight: 700; color: var(--fg-0); margin-left: auto; }
  #tvLegend .lg.up b { color: var(--up); }
  #tvLegend .lg.down b { color: var(--down); }
  .tvcharts { flex: 1; min-width: 0; }
  #tvBox .chint { color: var(--fg-2); font-size: 10.5px; margin: 2px 0 4px; text-align: center; }
  #tvBox .empty { color: var(--fg-2); padding: 6px 8px; }
  /* 永久隐藏 TradingView 水印(与 layout.attributionLogo=false 双保险) */
  #tvBox a, #tvMain a, #tvVol a { display: none !important; visibility: hidden !important; }

  .fbar { padding: 6px 2px 4px; }
  .fbar input { width: 100%; box-sizing: border-box; background: var(--bg-2);
                border: 1px solid var(--line-2); border-radius: var(--r-1); color: var(--fg-0);
                font-size: 12px; padding: 5px 8px; outline: none; }
  .fbar input:focus { border-color: var(--accent-line); }
  .fchips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
  .fchip { cursor: pointer; font-size: 10.5px; color: var(--fg-1); border: 1px solid var(--line-2);
           border-radius: 999px; padding: 1px 7px; user-select: none; }
  .fchip.on { background: var(--accent-dim); border-color: var(--accent-line); color: var(--accent); }
  .fclear { cursor: pointer; font-size: 10.5px; color: #ff9d9d; border: 1px solid rgba(240,69,58,.35);
            border-radius: 999px; padding: 1px 7px; margin-left: auto; }
  .fhit { color: var(--fg-2); font-size: 10.5px; margin-top: 4px; }
  .nr { padding: 7px 4px; border-bottom: 1px solid var(--line-1); cursor: pointer; }
  .nr:hover { background: var(--bg-3); }
  .nr1 { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
  .nr .nt { color: var(--fg-2); font-size: 11px; white-space: nowrap; }
  .nr .nk { font-size: 10px; padding: 0 5px; border-radius: var(--r-1); white-space: nowrap; }
  .nr .kb { color: #ff9d9d; background: rgba(255,77,141,.13); border: 1px solid rgba(255,77,141,.34); }
  .nr .kn { color: var(--fg-1); background: rgba(120,150,220,.10); border: 1px solid var(--line-2); }
  .nr .nc { color: var(--fg-2); font-size: 11px; white-space: nowrap; }
  .nr .nd { width: 7px; height: 7px; border-radius: 50%; margin-left: auto; }
  .nr .ntx { color: var(--fg-0); font-size: 12.5px; line-height: 1.5; display: -webkit-box;
             -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .cr { display: flex; align-items: flex-start; gap: 6px; padding: 5px 2px;
        border-bottom: 1px solid var(--line-1); font-size: 12px; color: var(--fg-0); line-height: 1.5; }
  .cr .ct { color: var(--fg-1); font-size: 11px; white-space: nowrap; min-width: 32px; }
  .cr .cst { color: var(--accent); font-size: 10px; white-space: nowrap; }
  .cr .ck { font-size: 10px; padding: 0 5px; border-radius: var(--r-1); white-space: nowrap;
            color: var(--fg-1); background: rgba(120,150,220,.10); border: 1px solid var(--line-2); }
  .cr .creg { color: var(--fg-2); font-size: 11px; white-space: nowrap; }
  .calAll { margin: 6px 0 2px; }
  .calAll summary { cursor: pointer; color: var(--fg-2); font-size: 11.5px; }

  .airtop { display: flex; align-items: center; gap: 12px; }
  .airnum { font-size: 42px; font-weight: 800; line-height: 1; }
  .airlv { color: var(--fg-0); font-weight: 700; font-size: 12px; border-radius: 999px; padding: 3px 12px; }
  .airbar { position: relative; height: 12px; background: var(--bg-2); border-radius: var(--r-1);
            margin: 10px 0 2px; overflow: hidden; }
  .airbar .fill { height: 100%; border-radius: var(--r-1); }
  .airbar i { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,.28); }
  .airscale { display: flex; justify-content: space-between; color: var(--fg-2); font-size: 10.5px; }
  .dimr { display: flex; align-items: center; gap: 6px; margin: 4px 0; font-size: 11.5px; }
  .dimr .dn { width: 108px; color: var(--fg-1); white-space: nowrap; overflow: hidden;
              text-overflow: ellipsis; }
  .dimr .db { flex: 1; height: 9px; background: var(--bg-2); border-radius: var(--r-1); overflow: hidden; }
  .dimr .db i { display: block; height: 100%; border-radius: var(--r-1); }
  .dimr .dv { width: 26px; text-align: right; color: var(--fg-0); }
  .refr { background: var(--bg-2); border-left: 3px solid var(--accent); border-radius: var(--r-1);
          padding: 5px 9px; margin: 5px 0; font-size: 12px; color: var(--fg-0); }
  .sigfold summary { cursor: pointer; color: var(--fg-1); font-size: 11.5px; margin: 6px 0; }
  .sig { font-size: 11.5px; color: var(--fg-1); padding: 3px 0; line-height: 1.6; }
  .sig i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }

  /* ===== 动效系统 =====
     统一时长与缓动:整个终端所有 hover / focus / 状态切换都走同一条曲线,
     动效才会像"一个系统"而不是每个地方各写一个 ease。
     时长语义:--dur-fast 120ms(颜色/文字) / --dur 220ms(背景/边框) / --dur-slow 420ms(入场)。 */
  .win, .idxcard, .wb, .nr, .cr, .tab, .fchip, .fclear, .btnlink, .wfold, .calAll summary,
  .sigfold summary, .airbar, .airbar .fill, .dimr .db, .dimr .db i, .tb td, .refr {
    transition: background-color var(--dur) var(--ease),
                border-color var(--dur) var(--ease),
                color var(--dur-fast) var(--ease),
                opacity var(--dur) var(--ease),
                box-shadow var(--dur) var(--ease),
                transform var(--dur) var(--ease);
  }
  /* 可交互元素的统一反馈:亮度上浮 + 极轻微位移,幅度刻意做小 */
  .idxcard:hover { background: var(--bg-3); border-color: var(--line-2); }
  .nr:hover .ntx { color: #fff; }
  .tab:hover, .fchip:hover { color: var(--fg-0); border-color: var(--line-2); }
  .btnlink:hover { transform: translateY(-1px); }
  .tb tr:hover td { background: rgba(255,255,255,.02); }
  .wfold:active { transform: scale(.92); }

  /* 首屏入场:卡片错峰淡入上浮。作用在 .win 容器上(它不会被数据刷新重建),
     所以只播一次;里面被重建的内容不会反复闪。 */
  @keyframes _win-in {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: none; }
  }
  .homecol .win { animation: _win-in var(--dur-slow) var(--ease) both; }
  .homecol .win:nth-child(1) { animation-delay: 0ms; }
  .homecol .win:nth-child(2) { animation-delay: 40ms; }
  .homecol .win:nth-child(3) { animation-delay: 80ms; }
  #sidePanel { animation: _win-in var(--dur-slow) var(--ease) both; }

  /* 数据刷新:涨跌用一次性背景脉冲,而不是常驻闪烁 */
  @keyframes _flash-up   { 0% { background: rgba(240,69,58,.16); }  100% { background: transparent; } }
  @keyframes _flash-down { 0% { background: rgba(25,163,95,.16); }  100% { background: transparent; } }
  .flash-up   { animation: _flash-up   620ms var(--ease) 1; }
  .flash-down { animation: _flash-down 620ms var(--ease) 1; }

  /* 尊重系统设置:开了"减少动态效果"就关掉 CSS 动画与过渡
     (地球上的 WebGL 动效由 JS 侧读同一个媒体查询来处理) */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: .001ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: .001ms !important;
    }
  }

  @media (max-width: 1280px) {
    #colR { display: none; }
    #sidePanel { width: 340px; }
    #globeViz { left: 372px; right: 14px; }
    #title { left: 372px; right: 14px; }
    #tip { right: 28px; }
  }
</style>
"""


# ---------------------------------------------------------------- 侧栏 DOM

def _sidebar_html(assets_prefix="assets/"):
    """生成主页左右侧栏 DOM(数据由页面 JS 从全局 HOME 渲染,行情图用 lightweight-charts)。"""
    return f"""
<script src="{assets_prefix}lightweight-charts.standalone.production.js"></script>
<div class="homecol" id="colL">
  <div class="win" id="wMarket" style="flex:0 0 auto;">
    <div class="wh"><span class="wt">📈 大A行情概况</span>
      <span class="tab on" data-c="minute">分时</span><span class="tab" data-c="daily">日K</span>
      <span class="ws" id="mktAsOf"></span>
      <span class="wfold" data-w="wMarket" title="收起 / 展开">－</span></div>
    <div class="wb" id="mktWrap">
      <div id="mktBody"></div>
      <div id="tvBox">
        <div class="tvline">
          <div id="tvLegend"></div>
          <div class="tvcharts">
            <div id="tvMain"></div>
            <div id="tvVol"></div>
          </div>
        </div>
        <div class="chint">滚轮缩放 · 拖动平移 · 十字光标看价</div>
        <div id="tvTip" class="empty" style="display:none"></div>
      </div>
    </div>
  </div>
  <div class="win" id="wNews" style="flex:1 1 auto;">
    <div class="wh"><span class="wt">📰 新闻</span>
      <span class="tab on" data-t="news">新闻</span><span class="tab" data-t="cal">📅 日历</span>
      <span class="ws" id="newsAsOf"></span>
      <span class="wfold" data-w="wNews" title="收起 / 展开">－</span></div>
    <div class="fbar">
      <input id="fq" type="text" placeholder="搜索 标题/国家/城市/来源　(按 / 聚焦)">
      <div class="fchips" id="fchips"></div>
      <div class="fhit" id="fHit"></div>
    </div>
    <div class="wb" id="newsBody" style="flex:1 1 auto;"></div>
  </div>
</div>
<div class="homecol" id="colR">
  <div class="win" id="wPos" style="flex:0 0 auto;">
    <div class="wh"><span class="wt">⭐ 我的持仓</span><span class="ws" id="posAsOf"></span>
      <span class="wfold" data-w="wPos" title="收起 / 展开">－</span></div>
    <div class="wb" id="posBody"></div>
  </div>
  <div class="win" id="wAssets" style="flex:0 0 auto;">
    <div class="wh"><span class="wt">📌 我的自选</span><span class="ws" id="watchAsOf">ETF / 股票 / 其他</span>
      <span class="wfold" data-w="wAssets" title="收起 / 展开">－</span></div>
    <div class="wb" id="assetsBody"></div>
  </div>
  <div class="win" id="wAir" style="flex:1 1 auto;">
    <div class="wh"><span class="wt">🪂 空中飞人指数</span><span class="ws" id="airAsOf"></span>
      <span class="wfold" data-w="wAir" title="收起 / 展开">－</span></div>
    <div class="wb" id="airBody" style="flex:1 1 auto;"></div>
  </div>
</div>
"""


def _home_header(payload):
    meta = payload.get("meta") or {}
    asof = _s(payload.get("asof"))
    sub = "数据时间 " + (asof[5:16] if len(asof) >= 16 else asof) + \
          " · 近 " + str(meta.get("window_days") or 3) + " 天事件 " + str(meta.get("events") or 0) + " 条"
    return ('<div class="hud" id="title"><h1>🌍 全球宏观事件 · 指挥台</h1>'
            '<div class="sub">' + sub + '</div></div>')


# ---------------------------------------------------------------- 侧栏 JS

HOME_JS = r"""
  // ===================== 主页指挥台(共享外层作用域:world / EVENTS / HOT / showGroup ... ) =====================
  (function () {
    var MKT = (HOME && HOME.market) || {};
    var newsTab = 'news';

    function $(id) { return document.getElementById(id); }
    function esc(s) {
      return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function cls(v) { v = +v || 0; return v > 0 ? 'pos' : (v < 0 ? 'neg' : 'flat'); }
    function pctTxt(v) { v = +v || 0; return (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }
    function fmtY(v) {
      v = +v || 0; var s = v < 0 ? '-' : '', a = Math.abs(v);
      if (a >= 1e8) return s + (a / 1e8).toFixed(2) + '亿';
      if (a >= 1e4) return s + (a / 1e4).toFixed(2) + '万';
      return s + a.toFixed(2);
    }
    function fmtAmt(v) {
      v = +v || 0;
      if (v >= 1e12) return (v / 1e12).toFixed(2) + '万亿';
      if (v >= 1e8) return (v / 1e8).toFixed(0) + '亿';
      if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
      return String(Math.round(v));
    }
    function stars(n) { var s = ''; for (var i = 0; i < n; i++) s += '★'; return s; }

    // ---------- 个股详情页入口（持仓 / 自选点行跳转） ----------
    // 行会随每次刷新重建，所以点击用**事件委托**挂在容器上，而不是逐行绑 onclick。
    // 跳转走同一个窗口（不再 window.open 开一堆新窗口），个股页有返回键回到这里。
    function stockUrl(code) {
      // 走 8766 服务时用短地址 /stock；直接 file:// 双击打开时退回同级的 stock.html
      var base = (location.protocol === 'file:') ? '../stock.html' : '/stock';
      return base + '?code=' + encodeURIComponent(code);
    }
    function stockRowOpen(x) {
      // 后端已经判过「有没有详情页可看」（逆回购 / 场外基金不给入口）
      return (x && x.page && x.code)
        ? '<tr class="rowlink" data-stock="' + esc(x.code) +
          '" title="点击查看个股详情（Ctrl / 中键可开新标签）">'
        : '<tr>';
    }
    function stockNameCell(x, name) {
      // 名字做成真链接：中键 / Ctrl+点击 仍能开新标签（普通点击才走同窗口跳转）
      return (x && x.page && x.code)
        ? '<a class="rowlink-a" href="' + stockUrl(x.code) + '">' + esc(name) + '</a>'
        : esc(name);
    }
    function bindStockRows(boxId) {
      var box = $(boxId);
      if (!box) return;
      box.addEventListener('click', function (ev) {
        // 点在链接上就交给浏览器处理（中键、Ctrl+点击、右键「新标签打开」都还在）
        var el = ev.target;
        while (el && el !== box) {
          if (el.tagName && el.tagName.toLowerCase() === 'a') return;
          el = el.parentNode;
        }
        el = ev.target;
        while (el && el !== box) {
          if (el.getAttribute && el.getAttribute('data-stock')) {
            var u = stockUrl(el.getAttribute('data-stock'));
            // 带修饰键时按老习惯开新标签（点名字链接时浏览器自己会处理）
            if (ev.ctrlKey || ev.metaKey || ev.shiftKey) {
              var w = window.open(u, '_blank');
              if (!w) location.href = u;      // 弹窗被拦就退回同窗口，别让这一下白点
            } else {
              location.href = u;
            }
            return;
          }
          el = el.parentNode;
        }
      });
    }

    // ---------- 数字入场动效(工具) ----------
    // 系统是否要求"减少动态效果":CSS 侧已用媒体查询关掉动画,这里给 JS 侧一个判据
    function _reducedMotion() {
      try {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      } catch (e) { return false; }
    }
    // 把一个"纯数字"元素的文本从 0 滚到目标值(约 620ms,缓出)。
    // 结束时刻意写回原始文本,避免 toFixed 与页面已有格式(小数位/千分位)对不上。
    function rollNumber(el, dur) {
      var txt = (el.textContent || '').trim();
      var target = parseFloat(txt.replace(/,/g, ''));
      if (!isFinite(target) || target === 0) { return; }
      var dec = (txt.split('.')[1] || '').length;
      var t0 = 0;
      dur = dur || 620;
      function step(ts) {
        if (!t0) { t0 = ts; }
        var k = Math.min(1, (ts - t0) / dur);
        var eased = 1 - Math.pow(1 - k, 3);        // easeOutCubic,与 CSS 的 --ease 观感一致
        el.textContent = (target * eased).toFixed(dec);
        if (k < 1) { requestAnimationFrame(step); }
        else { el.textContent = txt; }
      }
      requestAnimationFrame(step);
    }

    // ---------- 左上:大A行情概况 ----------
    function renderMarket() {
      var asof = HOME.asof || '';
      if ($('mktAsOf')) $('mktAsOf').textContent = asof ? asof.slice(5, 16) : '';
      var h = '<div class="idxgrid">';
      (MKT.indices || []).forEach(function (x) {
        h += '<div class="idxcard"><div class="in">' + esc(x.name) + '</div>' +
             '<div class="ip ' + cls(x.pct) + '">' + (x.price || '-') + '</div>' +
             '<div class="ic ' + cls(x.pct) + '">' + pctTxt(x.pct) + '</div></div>';
      });
      h += '</div>';
      var b = MKT.breadth || {};
      h += '<div class="brow"><span class="bl">涨跌家数</span>' +
           '<span class="bv pos">涨 ' + (b.up || 0) + '</span>' +
           '<span class="bv neg">跌 ' + (b.down || 0) + '</span>' +
           '<span class="bv flat">平 ' + (b.flat || 0) + '</span></div>';
      var sen = MKT.sentiment || {};
      if (sen.label) {
        h += '<div class="sent"><b>' + esc(sen.label) + '</b> <span class="hintxt">' +
             esc(sen.desc || '') + '</span></div>';
      }
      h += '<div class="brow"><span class="bl">成交额</span><span class="bv">' + fmtAmt(MKT.amount_total) +
           '</span><span class="bl">涨停</span><span class="bv pos">' + ((MKT.limit_up || {}).n || 0) +
           '</span><span class="bl">跌停</span><span class="bv neg">' + ((MKT.limit_down || {}).n || 0) + '</span></div>';
      var lb = (MKT.limit_up || {}).top || [];
      if (lb.length) {
        h += '<div class="lbt">高位连板:' + lb.map(function (x) {
          return esc(x.name) + '<i>' + x.lbc + '板</i>';
        }).join(' ') + '</div>';
      }
      var st = MKT.stock_up || [];
      if (st.length) {
        h += '<div class="sub2">个股涨幅榜</div><table class="tb"><tbody>';
        st.slice(0, 6).forEach(function (x) {
          h += '<tr><td>' + esc(x.name) + '</td><td class="hintxt">' + esc(x.code) + '</td>' +
               '<td>' + (x.price || '-') + '</td><td class="' + cls(x.pct) + '">' + pctTxt(x.pct) + '</td></tr>';
        });
        h += '</tbody></table>';
      }
      $('mktBody').innerHTML = h;
      // 数字入场:指数点位从 0 滚到真实值。纯视觉,失败也不影响数据展示。
      try {
        if (!_reducedMotion()) {
          var _ips = $('mktBody').querySelectorAll('.idxcard .ip');
          for (var _i = 0; _i < _ips.length; _i++) { rollNumber(_ips[_i]); }
        }
      } catch (e) {}
    }

    // ---------- 左上:行情图(白底券商风格 + 缩放/拖动/十字光标) ----------
    var LC = window.LightweightCharts;
    var chMain = null, chVol = null, mainSeries = [], volSeries = null, chartMode = 'minute';
    var UP = '#f0453a', DOWN = '#19a35f';        // 与 CSS 里的 --up/--down 保持一致
    var MA_COLORS = { '5': '#f0a500', '10': '#e91e63', '50': '#2563eb', '144': '#7c3aed' };

    function chartOpts(el, h) {
      return {
        width: (el && el.clientWidth) || 320,
        height: h,
        layout: {
          // 透明底:让 #tvBox 的深色背景透上来。
          // 原来写死 '#ffffff',是这套深色界面里最大的一处白块。
          background: { color: 'transparent' },
          textColor: '#78879b',
          fontSize: 10,
          attributionLogo: false          // 永久移除 TradingView 水印
        },
        grid: { vertLines: { color: '#1b2430' }, horzLines: { color: '#1b2430' } },
        rightPriceScale: { borderColor: '#1b2430', scaleMargins: { top: 0.12, bottom: 0.12 } },
        timeScale: { borderColor: '#1b2430', timeVisible: true, secondsVisible: false },
        crosshair: {
          mode: LC && LC.CrosshairMode ? LC.CrosshairMode.Normal : 0,
          vertLine: { color: '#556072', labelBackgroundColor: '#202b3a' },
          horzLine: { color: '#556072', labelBackgroundColor: '#202b3a' }
        },
        localization: { locale: 'zh-CN' },
        handleScroll: true,
        handleScale: true
      };
    }
    // 兜底:把水印 <a> 元素从 DOM 里摘掉(attributionLogo:false 若被库版本忽略也能生效)
    function killLogo() {
      try {
        var els = document.querySelectorAll('#tvBox a, #tvMain a, #tvVol a');
        for (var i = 0; i < els.length; i++) {
          if (els[i].parentNode) els[i].parentNode.removeChild(els[i]);
        }
      } catch (e) {}
    }
    function renderLegend(mode) {
      var box = $('tvLegend');
      if (!box) return;
      var C = (HOME && HOME.charts) || {};
      var h = '';
      if (mode === 'daily') {
        var bars = (C.daily || {}).bars || [];
        if (bars.length) {
          var last = bars[bars.length - 1];
          var prev = bars.length > 1 ? bars[bars.length - 2] : last;
          var chg = (prev && prev.close) ? (last.close - prev.close) / prev.close * 100 : 0;
          var cl = chg >= 0 ? 'up' : 'down';
          h += '<div class="lg ' + cl + '">收盘<b>' + last.close.toFixed(2) + '</b></div>';
          h += '<div class="lg ' + cl + '">涨跌<b>' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</b></div>';
        }
        var ma = (C.daily || {}).ma || {};
        ['5', '10', '50', '144'].forEach(function (k) {
          var arr = ma[k] || [];
          if (!arr.length) return;
          h += '<div class="lg"><i style="background:' + (MA_COLORS[k] || '#2563eb') + '"></i>MA' + k +
               '<b>' + arr[arr.length - 1].value.toFixed(1) + '</b></div>';
        });
      }
      box.innerHTML = h;
      box.style.display = h ? 'block' : 'none';
    }

    function clearSeries() {
      for (var i = 0; i < mainSeries.length; i++) {
        try { chMain.removeSeries(mainSeries[i]); } catch (e) {}
      }
      mainSeries = [];
      if (volSeries) { try { chVol.removeSeries(volSeries); } catch (e) {} volSeries = null; }
    }
    function renderCharts(mode) {
      if (!LC || !chMain) return;
      var C = (HOME && HOME.charts) || {};
      clearSeries();
      var ok = false;
      if (mode === 'daily') {
        var d = C.daily || {};
        var bars = d.bars || [];
        if (bars.length) {
          var cs = chMain.addCandlestickSeries({
            upColor: UP, downColor: DOWN,
            borderUpColor: UP, borderDownColor: DOWN,
            wickUpColor: UP, wickDownColor: DOWN
          });
          cs.setData(bars);
          mainSeries.push(cs);
          Object.keys(d.ma || {}).forEach(function (k) {
            var ln = chMain.addLineSeries({
              color: MA_COLORS[k] || '#2563eb', lineWidth: 1,
              priceLineVisible: false, lastValueVisible: false  // 不设 title:图内不留均线文字
            });
            ln.setData(d.ma[k]);
            mainSeries.push(ln);
          });
          volSeries = chVol.addHistogramSeries({ priceFormat: { type: 'volume' },
            priceLineVisible: false, lastValueVisible: false });
          volSeries.setData(bars.map(function (b) {
            return { time: b.time, value: b.volume,
                     color: (b.close >= b.open ? 'rgba(230,69,69,.55)' : 'rgba(18,161,93,.55)') };
          }));
          ok = true;
        }
      } else {
        var m = C.minute || {};
        var pts = m.points || [];
        if (pts.length) {
          var ls = chMain.addLineSeries({ color: '#2563eb', lineWidth: 2, priceLineVisible: false });
          ls.setData(pts.map(function (p) { return { time: p.time, value: p.value }; }));
          if (m.pre_close) {
            ls.createPriceLine({ price: m.pre_close, color: '#9aa5b1', lineWidth: 1,
              lineStyle: 2, axisLabelVisible: true, title: '昨收' });
          }
          mainSeries.push(ls);
          volSeries = chVol.addHistogramSeries({ priceFormat: { type: 'volume' },
            priceLineVisible: false, lastValueVisible: false });
          volSeries.setData(pts.map(function (p) {
            return { time: p.time, value: p.vol,
                     color: (m.pre_close && p.value >= m.pre_close
                             ? 'rgba(230,69,69,.55)' : 'rgba(18,161,93,.55)') };
          }));
          ok = true;
        }
      }
      var tip = $('tvTip');
      if (tip) tip.style.display = ok ? 'none' : 'block';
      if (!ok && tip) tip.textContent = '行情图数据获取失败(盘前或接口异常)';
      if (ok) {
        try { chMain.timeScale().fitContent(); chVol.timeScale().fitContent(); } catch (e) {}
      }
      renderLegend(mode);
      killLogo();
    }
    function initCharts() {
      if (!LC || !$('tvMain') || !$('tvVol')) {
        var tip0 = $('tvTip');
        if (tip0) { tip0.style.display = 'block'; tip0.textContent = '图表库未加载(assets/lightweight-charts.standalone.production.js)'; }
        return;
      }
      chMain = LC.createChart($('tvMain'), chartOpts($('tvMain'), 206));
      chVol = LC.createChart($('tvVol'), chartOpts($('tvVol'), 64));
      try {
        chMain.timeScale().subscribeVisibleLogicalRangeChange(function (r) {
          if (r) { try { chVol.timeScale().setVisibleLogicalRange(r); } catch (e) {} }
        });
      } catch (e) {}
      renderCharts(chartMode);
      killLogo();
      setTimeout(killLogo, 300);
      setTimeout(killLogo, 1500);
      var cts = document.querySelectorAll('#wMarket .tab');
      for (var i = 0; i < cts.length; i++) {
        (function (t) {
          t.onclick = function () {
            chartMode = t.getAttribute('data-c');
            for (var k = 0; k < cts.length; k++) {
              cts[k].className = (cts[k] === t) ? 'tab on' : 'tab';
            }
            renderCharts(chartMode);
          };
        })(cts[i]);
      }
      window.addEventListener('resize', function () {
        try {
          chMain.applyOptions({ width: $('tvMain').clientWidth });
          chVol.applyOptions({ width: $('tvVol').clientWidth });
          killLogo();
        } catch (e) {}
      });
    }

    // ---------- 左下:新闻(含 搜索 / 筛选) ----------
    var FILTER = { q: '', kind: '', sev: '', src: '' };
    var FCHIPS = [['kind', '', '全部'], ['kind', '突发', '🔥突发'], ['kind', '常规', '📊常规'],
                  ['sev', '3', '影响高'], ['sev', '2', '影响中'],
                  ['src', '东财', '东财'], ['src', '见闻', '见闻'], ['src', '财联社', '财联社'], ['src', '金十', '金十']];
    function saveFilter() { try { localStorage.setItem('dfcf_filter', JSON.stringify(FILTER)); } catch (e) {} }
    function loadFilter() { try { var s0 = localStorage.getItem('dfcf_filter'); if (s0) FILTER = JSON.parse(s0) || FILTER; } catch (e) {} }
    function passFilter(e) {
      if (FILTER.kind && e.kind !== FILTER.kind) return false;
      if (FILTER.sev && String(e.sev || '') !== String(FILTER.sev)) return false;
      if (FILTER.src && e.src !== FILTER.src) return false;
      if (FILTER.q) {
        var sf = ((e.title || '') + ' ' + (e.country || '') + ' ' + (e.city || '') + ' ' + (e.src || '')).toLowerCase();
        if (sf.indexOf(FILTER.q.toLowerCase()) < 0) return false;
      }
      return true;
    }

    var LINK_MAP = false;
    function applyMapFilter() {
      try {
        if (typeof LAYER !== 'undefined' && LAYER && !LAYER.ev) return;
        var arr = groups;
        if (LINK_MAP && (FILTER.q || FILTER.kind || FILTER.sev || FILTER.src)) {
          arr = groups.filter(function (g) {
            return (g.items || []).some(function (e) { return passFilter(e); });
          });
        }
        world.pointsData(arr);
        world.ringsData([]);   // 涟漪已关闭（数据置空即不渲染任何环）
        world.labelsData(arr.filter(function (d) { return d.count > 1; }));
      } catch (e) {}
    }

    function newsSorted() {
      return (EVENTS || []).slice().sort(function (a, b) {
        return String(b.time || '').localeCompare(String(a.time || ''));
      }).slice(0, (HOME.meta && HOME.meta.feed_rows) || 300);
    }
    function newsRow(it, key) {
      var kc = it.kind === '突发' ? 'kb' : 'kn';
      // 标题带原文链接:点标题直接跳原站(新窗口);点这一行的其它地方仍是飞向地球光点
      var u = it.url || '';
      var body = u
        ? '<a class="ntx" href="' + esc(u) + '" target="_blank" rel="noopener noreferrer"' +
          ' title="打开原文（' + esc(it.src || '') + '）" onclick="event.stopPropagation()"' +
          ' style="text-decoration:none;color:#dfe6f2"' +
          ' onmouseover="this.style.color=\'#8fd0ff\';this.style.textDecoration=\'underline\'"' +
          ' onmouseout="this.style.color=\'#dfe6f2\';this.style.textDecoration=\'none\'">' +
          esc(it.title) + '<span style="color:#6b7a97;font-size:10px;margin-left:4px">↗</span></a>'
        : '<div class="ntx">' + esc(it.title) + '</div>';
      return '<div class="nr" data-k="' + key + '"><div class="nr1">' +
        '<span class="nt">' + _fmtTm(it.time) + '</span>' +
        '<span class="nk ' + kc + '">' + esc(it.kind || '') + '</span>' +
        (it.country ? '<span class="nc">' + esc(it.country) + '</span>' : '') +
        '<span class="nd" style="background:' + colorOf(it.sev) + '"></span></div>' +
        body + '</div>';
    }
    function renderNews() {
      var nb = $('newsBody');
      // 增量刷新前先记下滚动位置:fetch 轮询每 60 秒重画一次 newsBody,
      // 不记的话列表会弹回顶部、正在看的那条被顶走 —— 这才是"刷新闪一下"的实际来源。
      var keepTop = nb ? nb.scrollTop : 0;
      var h = '';
      // 命中数是「热点 + 事件」两块之和。原代码先 hits += hots.length,随后又用
      // var hits = list.length 覆盖掉,把热点的条数丢了(且第一次 += 时 hits 还没声明)。
      var hits = 0;
      var hots = (HOT && HOT.items) ? HOT.items.filter(passFilter) : [];
      hits += hots.length;
      if (hots.length) {
        h += '<div class="sec2" style="border-top:none">🔥 今日热点 · ' + esc(HOT.scope || '') + ' ' +
             esc(HOT.asof || '') + '</div>';
        hots.forEach(function (it, i) { h += newsRow(it, 'hot:' + i); });
      }
      var list = newsSorted().filter(passFilter);
      hits += list.length;
      var S = (HOME.meta || {}).sources || {}, sp = [];
      Object.keys(S).forEach(function (k) { sp.push(k + (S[k] < 0 ? ' ✕' : ' ' + S[k])); });
      var liveAt = (HOME.meta && HOME.meta.live_at) ? ' · ⟳ ' + esc(HOME.meta.live_at) + ' 已更新' : '';
      var srcTxt = sp.length ? '<div class="hintxt" style="margin:2px 0 0">信源:' + sp.join(' · ') + liveAt + '</div>' : '';
      h += '<div class="sec2">📰 ' + esc((HOME.meta || {}).window_label || '近3天') + '事件(' +
           (EVENTS || []).length + ' 条)</div>' + srcTxt;
      list.forEach(function (e, i) { h += newsRow(e, 'ev:' + i); });
      if (!hits) {
        h += '<div class="empty">' + ((FILTER.q || FILTER.kind || FILTER.sev || FILTER.src) ? '没有符合筛选条件的事件' : '暂无事件(重新运行脚本即可刷新)') + '</div>';
      }
      if ($('fHit')) $('fHit').textContent = '命中 ' + hits + ' 条 / 事件共 ' + (EVENTS || []).length + ' 条';
      if (nb) {
        nb.innerHTML = h;
        nb.scrollTop = keepTop;                // 复原滚动位置,增量刷新不再跳回顶部
      }
      var rows = nb ? nb.querySelectorAll('.nr') : [];
      for (var i = 0; i < rows.length; i++) {
        (function (r) { r.onclick = function () { focusNews(r.getAttribute('data-k')); }; })(rows[i]);
      }
      applyMapFilter();
    }

    // ---------- 左下:日历 ----------
    function calRow(x) {
      return '<div class="cr"><span class="ct">' + esc(x.time) + '</span>' +
        '<span class="cst">' + stars(x.star || 1) + '</span>' +
        '<span class="ck">' + esc(x.kind || '数据') + '</span>' +
        (x.region ? '<span class="creg">' + esc(x.region) + '</span>' : '') +
        '<span>' + esc(x.name) + '</span></div>';
    }
    function renderCal() {
      var C = HOME.calendar || {};
      var h = '<div class="hintxt" style="margin-bottom:4px">' + esc(C.note || '') + '</div>';
      var days = C.days || [];
      for (var i = 0; i < days.length; i++) {
        var d = days[i], items = d.items || [];
        var key = items.filter(function (x) { return (x.star || 1) >= 2; });
        var rest = items.filter(function (x) { return (x.star || 1) < 2; });
        h += '<div class="sec2"' + (i === 0 ? ' style="border-top:none"' : '') + '>📅 ' + esc(d.label) +
             ' ' + esc(String(d.date || '').slice(5)) + ' · 共 ' + (d.total || 0) + ' 条</div>';
        var show = key.length ? key : items.slice(0, 10);
        if (!show.length) h += '<div class="empty">暂无</div>';
        show.forEach(function (x) { h += calRow(x); });
        if (rest.length) {
          h += '<details class="calAll"><summary>展开其它 ' + rest.length + ' 条</summary>' +
               rest.map(calRow).join('') + '</details>';
        }
      }
      $('newsBody').innerHTML = h;
    }

    // ---------- 右上:持仓 / 配置标的 ----------
    function renderPos() {
      var P = HOME.positions || {};
      if ($('posAsOf')) $('posAsOf').textContent = HOME.asof ? HOME.asof.slice(5, 16) : '';
      if (P.empty) {
        $('posBody').innerHTML = '<div class="empty">📭 ' + esc(P.note || '暂无持仓') + '</div>' +
          '<a class="btnlink" href="' + (P.manage_url || 'http://127.0.0.1:8765') +
          '" target="_blank">＋ 记录持仓(打开持仓管理)</a>';
        return;
      }
      var h = '<div class="brow" style="margin-top:2px"><span class="bl">市值</span><span class="bv">' +
              fmtY(P.total_mv) + '</span><span class="bl">成本</span><span class="bv">' + fmtY(P.total_cost) +
              '</span></div><div class="brow"><span class="bl">累计</span><span class="bv ' + cls(P.total_pnl) +
              '">' + fmtY(P.total_pnl) + (P.total_pct == null ? '' : ' (' + pctTxt(P.total_pct) + ')') +
              '</span><span class="bl">当日</span><span class="bv ' + cls(P.total_day) + '">' +
              fmtY(P.total_day) + '</span></div>';
      h += '<table class="tb"><tbody>';
      (P.items || []).forEach(function (x) {
        h += stockRowOpen(x) + '<td>' + stockNameCell(x, x.name) + '</td><td>' + (x.price || '-') + '</td>' +
             '<td class="' + cls(x.day_pnl) + '">' + fmtY(x.day_pnl) + '</td>' +
             '<td class="' + cls(x.pnl) + '">' + fmtY(x.pnl) + '</td></tr>';
      });
      h += '</tbody></table>';
      $('posBody').innerHTML = h;
    }
    function renderWatchlist() {
      // 「我的自选」:三组(ETF / 股票 / 其他)固定都列出来,空组也给提示;
      // 只跟行情,没有成本/盈亏(那是 renderPos 的持仓)
      var W = HOME.watchlist || {};
      var groups = W.groups || [];
      var total = W.total || 0;
      if (!total) {
        $('assetsBody').innerHTML = '<div class="empty">📌 还没有自选标的' +
          (W.error ? '(采集异常：' + esc(W.error) + ')' : '') + '</div>' +
          '<a class="btnlink" href="' + (W.manage_url || 'http://127.0.0.1:8765') +
          '" target="_blank">＋ 添加自选(打开持仓管理)</a>';
        if ($('watchAsOf')) $('watchAsOf').textContent = 'ETF / 股票 / 其他';
        return;
      }
      var h = '';
      groups.forEach(function (g) {
        var rows = g.rows || [];
        h += '<div class="sub2">' + esc(g.label) +
             '<span class="hintxt" style="margin-left:6px;font-weight:400">' + rows.length + ' 条</span></div>';
        if (!rows.length) {
          h += '<div class="empty" style="padding:2px 0 8px">暂无' + esc(g.label) +
               '，点下方「管理自选」添加</div>';
          return;
        }
        h += '<table class="tb"><tbody>';
        rows.forEach(function (x) {
          var val = (x.price == null || x.price === '') ? '-'
                    : (x.is_rate ? esc(String(x.price)) + '%' : esc(String(x.price)));
          var sub = x.kind ? '<div class="hintxt" style="font-size:10px">' + esc(x.kind) +
                             (x.is_nav && x.nav_date ? ' · 净值日 ' + esc(x.nav_date) : '') + '</div>' : '';
          h += stockRowOpen(x) + '<td>' + stockNameCell(x, x.name) + sub + '</td>' +
               '<td>' + val + '</td>' +
               '<td class="' + cls(x.pct) + '">' +
               (x.pct == null || x.pct === '' ? '-' : pctTxt(x.pct)) + '</td></tr>';
        });
        h += '</tbody></table>';
      });
      h += '<div class="hintxt" style="margin-top:6px">共 ' + total + ' 条' +
           (W.asof ? ' · ' + esc(String(W.asof).slice(5, 16)) : '') +
           ' · <a href="' + (W.manage_url || 'http://127.0.0.1:8765') +
           '" target="_blank" style="color:#8fb6ff">管理自选</a></div>';
      $('assetsBody').innerHTML = h;
      if ($('watchAsOf')) $('watchAsOf').textContent = 'ETF ' + groups.filter(function (g) {
        return g.key === 'etf'; })[0]?.rows.length + ' · 股票 ' +
        (groups.filter(function (g) { return g.key === 'stock'; })[0]?.rows.length || 0) + ' · 其他 ' +
        (groups.filter(function (g) { return g.key === 'other'; })[0]?.rows.length || 0);
    }

    // ---------- 右下:空中飞人指数 ----------
    function renderAir() {
      var A = HOME.airman || {};
      if (A.empty) {
        $('airBody').innerHTML = '<div class="empty">' + esc(A.note || '暂无指数') + '</div>';
        return;
      }
      var col = { red: '#e64545', orange: '#e8963a', yellow: '#d9a41b', green: '#12a15d' }[A.color] || '#e8963a';
      if ($('airAsOf')) $('airAsOf').textContent = String(A.time || '').slice(5, 16);
      var h = '<div class="airtop"><div class="airnum" style="color:' + col + '">' + A.total + '</div>' +
              '<div><div class="airlv" style="background:' + col + '">' + esc(A.level) + '</div>' +
              '<div class="hintxt" style="margin-top:4px">0-100,越高风险越大</div></div></div>' +
              '<div class="airbar"><div class="fill" style="width:' + Math.max(1, Math.min(100, A.total)) +
              '%;background:linear-gradient(90deg,#12a15d,#f6c344,#e64545)"></div>' +
              '<i style="left:25%"></i><i style="left:50%"></i><i style="left:75%"></i></div>' +
              '<div class="airscale"><span>0 安全</span><span>50 警戒</span><span>100 临界</span></div>';
      h += '<div class="sub2">五维风险得分</div>';
      (A.dims || []).forEach(function (d) {
        if (d.score == null) {
          h += '<div class="dimr"><span class="dn">' + esc(d.key) + ' ' + esc(d.name) + '</span>' +
               '<span class="hintxt">数据缺失</span></div>';
          return;
        }
        var dc = d.score >= 7 ? '#e64545' : (d.score >= 5 ? '#e8963a' : '#12a15d');
        h += '<div class="dimr"><span class="dn">' + esc(d.key) + ' ' + esc(d.name) +
             ' <span class="hintxt">' + Math.round((d.weight || 0) * 100) + '%</span></span>' +
             '<span class="db"><i style="width:' + Math.max(2, d.score / 10 * 100) + '%;background:' + dc +
             '"></i></span><span class="dv">' + d.score.toFixed(1) + '</span></div>';
      });
      (A.refs || []).forEach(function (r) {
        h += '<div class="refr"><b>' + esc(r.name) + '</b> ' + esc(r.display) +
             ' <span class="hintxt">' + esc(r.src) + '</span></div>';
      });
      var sig = A.signals || [];
      if (sig.length) {
        h += '<details class="sigfold"><summary>计分信号 ' + sig.length + ' 项(点击展开)</summary>';
        sig.forEach(function (s) {
          var dot = s.status === 'red' ? '#e64545' : (s.status === 'orange' ? '#e8963a'
                   : (s.status === 'miss' ? '#8a94a3' : '#12a15d'));
          h += '<div class="sig"><i style="background:' + dot + '"></i>' + esc(s.key) + ' ' + esc(s.name) +
               ' <span class="hintxt">' + esc(s.text || '数据获取失败') + '</span></div>';
        });
        h += '</details>';
      }
      $('airBody').innerHTML = h;
    }

    // ---------- 交互:新闻 / 国家 / 光点 → 飞向 + 右栏详情 ----------
    function showEventPanel(it) {
      var peers = (EVENTS || []).filter(function (e) { return e.country === it.country; });
      var meta = _fmtTm(it.time) + ' · ' + esc(it.country || '-') +
                 (it.city ? ' · ' + esc(it.city) : '') + (it.src ? ' · ' + _srcTag(it.src) : '') +
                 '<br><span style="color:' + colorOf(it.sev) + '">' + _lvTxt(it.sev) + '</span> · ' +
                 esc(it.kind || '');
      var u = String(it.url || '').replace(/"/g, '%22');
      var body = '<div class="ev" style="background:rgba(70,110,190,.10);border-radius:8px;padding:10px;">' +
                 (u ? '<a href="' + u + '" target="_blank" rel="noopener noreferrer"' +
                      ' style="color:#dfe6f2;text-decoration:none">' + esc(it.title) + '</a>'
                    : esc(it.title)) + '</div>';
      if (u) {
        body += '<div style="margin:8px 0 2px"><a class="btnlink" href="' + u +
                '" target="_blank" rel="noopener noreferrer">🔗 查看原文' +
                (it.src ? '（' + esc(it.src) + '）' : '') + '</a></div>';
      }
      body += '<div class="sec">' + esc(it.country || '该地点') + ' 相关新闻(' + peers.length + ' 条)</div>' +
              rowsFor(peers);
      fillPanel('📰', '新闻详情', meta, body);
    }
    function focusNews(key) {
      var parts = String(key || '').split(':'), which = parts[0], idx = parseInt(parts[1], 10) || 0;
      var it = which === 'hot' ? ((HOT.items || [])[idx]) : newsSorted()[idx];
      if (!it) return;
      if (it.lat != null && it.lng != null) {
        try { world.pointOfView({ lat: it.lat, lng: it.lng, altitude: 1.05 }, 900); } catch (e) {}
      }
      showEventPanel(it);
    }
    function polyCenter(poly) {
      var g = (poly || {}).geometry || {}, cs = g.coordinates || [];
      var ring = null;
      if (g.type === 'Polygon') ring = cs[0];
      else if (g.type === 'MultiPolygon') {
        var best = 0;
        for (var i = 0; i < cs.length; i++) {
          var r0 = cs[i] && cs[i][0];
          if (r0 && r0.length > best) { best = r0.length; ring = r0; }
        }
      }
      if (!ring || !ring.length) return null;
      var x = 0, y = 0, n = 0;
      for (var k = 0; k < ring.length; k++) {
        var p = ring[k];
        if (!p || p.length < 2) continue;
        x += +p[0] || 0; y += +p[1] || 0; n++;
      }
      return n ? [y / n, x / n] : null;
    }
    world.onPointClick(function (p) {
      try { world.pointOfView({ lat: p.lat, lng: p.lng, altitude: 1.0 }, 900); } catch (e) {}
      showGroup(p);
    });


    // ---------- 搜索 / 筛选 交互 ----------
    function initFilter() {
      loadFilter();
      var holder = $('fchips');
      if (holder) {
        holder.innerHTML = FCHIPS.map(function (c) {
          return '<span class="fchip" data-f="' + c[0] + '" data-v="' + c[1] + '">' + c[2] + '</span>';
        }).join('') + '<span class="fchip" id="fLink">🔗联动地图</span><span class="fclear" id="fClear">清空</span>';
        var cs = holder.querySelectorAll('.fchip');
        for (var i = 0; i < cs.length; i++) (function (c) {
          var f = c.getAttribute('data-f'), v = c.getAttribute('data-v');
          if (String(FILTER[f] || '') === v) c.className = 'fchip on';
          c.onclick = function () {
            FILTER[f] = (String(FILTER[f] || '') === v) ? '' : v;
            for (var k = 0; k < cs.length; k++) {
              var cc = cs[k];
              cc.className = (cc.getAttribute('data-f') === f && String(FILTER[f]) === cc.getAttribute('data-v')) ? 'fchip on' : 'fchip';
            }
            saveFilter(); renderNews();
          };
        })(cs[i]);
      }
      var q = $('fq');
      if (q) {
        q.value = FILTER.q || '';
        q.oninput = function () { FILTER.q = q.value.trim(); saveFilter(); renderNews(); };
        q.onkeydown = function (ev) { if (ev.key === 'Escape') { q.value = ''; FILTER.q = ''; saveFilter(); renderNews(); } };
      }
      var cl = $('fClear');
      if (cl) cl.onclick = function () {
        FILTER = { q: '', kind: '', sev: '', src: '' };
        if (q) q.value = '';
        var all = document.querySelectorAll('.fchip');
        for (var k = 0; k < all.length; k++) all[k].className = 'fchip';
        saveFilter(); renderNews();
      };
      var lk = $('fLink');
      if (lk) lk.onclick = function () {
        LINK_MAP = !LINK_MAP;
        lk.className = LINK_MAP ? 'fchip on' : 'fchip';
        applyMapFilter();
      };
      document.addEventListener('keydown', function (ev) {
        var a = document.activeElement || {};
        if (ev.key === '/' && a.tagName !== 'INPUT') { ev.preventDefault(); if (q) q.focus(); }
      });
    }


    // ---------- 窗口:tab / 折叠 ----------
    var tabs = document.querySelectorAll('#wNews .tab');
    for (var ti = 0; ti < tabs.length; ti++) {
      (function (t) {
        t.onclick = function () {
          newsTab = t.getAttribute('data-t');
          for (var k = 0; k < tabs.length; k++) {
            tabs[k].className = (tabs[k] === t) ? 'tab on' : 'tab';
          }
          if (newsTab === 'cal') renderCal(); else renderNews();
        };
      })(tabs[ti]);
    }
    var folds = document.querySelectorAll('.wfold');
    for (var fi = 0; fi < folds.length; fi++) {
      (function (f) {
        f.onclick = function () {
          var w = $(f.getAttribute('data-w'));
          if (!w) return;
          var b = w.querySelector('.wb');
          var hid = b.style.display === 'none';
          b.style.display = hid ? '' : 'none';
          w.style.flex = hid ? '' : '0 0 auto';
          f.textContent = hid ? '－' : '＋';
          if (hid) {
            fitGlobe();
            try {
              if (chMain) chMain.applyOptions({ width: $('tvMain').clientWidth });
              if (chVol) chVol.applyOptions({ width: $('tvVol').clientWidth });
            } catch (e) {}
            killLogo();
          }
        };
      })(folds[fi]);
    }

    // ---------- 地球尺寸跟随中间列 ----------
    function fitGlobe() {
      try {
        var el = $('globeViz');
        if (el && world.width) world.width(el.clientWidth).height(el.clientHeight);
      } catch (e) {}
    }
    fitGlobe();
    setTimeout(fitGlobe, 80);
    window.addEventListener('resize', fitGlobe);

    // ---------- 初始化 ----------
    initFilter();
    renderMarket();
    initCharts();
    renderNews();
    renderPos();
    renderWatchlist();
    renderAir();
    bindStockRows('posBody');     // 持仓行 → 个股页
    bindStockRows('assetsBody');  // 自选行 → 个股页
    window.__applyMapFilter = applyMapFilter;

    // ---------- 实时新闻推送(Live) ----------
    // 后端 live_server.py 每 NEWS_REFRESH_SECONDS 秒重抓一次全球新闻；这里按
    // meta.live.poll_seconds 轮询 /api/news，用版本号比对，有变化才增量刷新：
    //   EVENTS / HOT / groups 换成新的 → 重设地球光点 → renderNews() 重画新闻栏。
    // 整页不重新加载，地球贴图不会重下，视觉上无闪烁。
    function liveApply(pack) {
      try {
        if (!pack || !pack.version) { return; }
        if (pack.events) { EVENTS = pack.events; }
        if (pack.hot) { HOT = pack.hot; }
        if (pack.groups) {
          groups = pack.groups;
          multiGroups = groups.filter(function (d) { return d.count > 1; });
        }
        if (pack.sources) { HOME.meta.sources = pack.sources; }
        HOME.meta.live_at = (pack.asof || '').slice(11, 16) || new Date().toTimeString().slice(0, 5);
        HOME.meta.events = (EVENTS || []).length;
        try {
          world.pointsData(groups).ringsData([]).labelsData(multiGroups);
        } catch (e) {}
        renderNews();          // 内部会再调 applyMapFilter()，光点与列表一起更新
      } catch (e) {
        if (window.console) { console.warn('[live] 增量刷新失败:', e); }
      }
    }
    window.__liveRefresh = liveApply;

    var LIVE_VER = 0;
    function pollLive() {
      try {
        if (!window.fetch) { return; }
        if (location.protocol !== 'http:' && location.protocol !== 'https:') { return; }
        var api = (HOME.meta && HOME.meta.live && HOME.meta.live.api) || '/api/news';
        fetch(api + '?ver=' + LIVE_VER, { cache: 'no-store' }).then(function (r) {
          return r.ok ? r.json() : null;
        }).then(function (j) {
          if (!j || !j.version || j.version === LIVE_VER) { return; }
          LIVE_VER = j.version;
          if (!j.unchanged) { liveApply(j); }
        }).catch(function () { /* 服务未启动时静默降级 */ });
      } catch (e) {}
    }
    if (window.fetch && (location.protocol === 'http:' || location.protocol === 'https:')) {
      var pollSec = (HOME.meta && HOME.meta.live && HOME.meta.live.poll_seconds) || 60;
      setInterval(pollLive, Math.max(15, pollSec) * 1000);
      setTimeout(pollLive, 4000);
    }
  })();
"""


# ---------------------------------------------------------------- 生成主页

def build_home(events, payload, out_path, assets_prefix="assets/", chart_data=None):
    """生成主页 HTML(中间 3D 地球 + 左右四个信息窗口)。"""
    import events as events_mod
    if chart_data is None:
        chart_data = generate_chart_data()
    payload = dict(payload or {})
    payload["charts"] = chart_data

    html = events_mod._assemble(
        events, _home_header(payload), assets_prefix=assets_prefix,
        extra_head=HOME_CSS, extra_body=_sidebar_html(assets_prefix),
        extra_js=HOME_JS, payload_json=payload_json(payload),
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


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
