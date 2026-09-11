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
  右上 ⭐ 我的持仓(positions.json,空态给出管理入口)+ 🧺 配置标的行情(固收 / 逆回购 / 高成长 / 纳指标普)
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
        } for x in items],
    }


def _assets_payload(portfolio_data):
    """右上窗口之二:配置标的行情(固收 / 逆回购 / 高成长基金 / 纳指标普 ETF)。"""
    pd = portfolio_data or {}
    fi = pd.get("fixed_income") or {}
    hr = pd.get("high_risk") or {}
    return {
        "fixed": [{"name": _s(x.get("name")), "kind": _s(x.get("kind")),
                   "price": _s(x.get("price")), "pct": x.get("pct")} for x in (fi.get("etfs") or [])],
        "repos": [{"name": _s(x.get("name")), "kind": _s(x.get("kind")),
                   "price": _s(x.get("price")), "pct": x.get("pct")} for x in (fi.get("repos") or [])],
        "funds": [{"name": _s(x.get("name")), "nav": _s(x.get("nav")), "pct": x.get("pct"),
                   "nav_date": _s(x.get("nav_date"))} for x in (hr.get("funds") or [])],
        "qdii": [{"name": _s(x.get("name")), "price": _s(x.get("price")), "pct": x.get("pct")}
                 for x in (hr.get("etfs") or [])],
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
        "assets": _assets_payload(portfolio_data),
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
  .win { background: rgba(8,13,28,.92); border: 1px solid #26334f; border-radius: 12px;
         box-shadow: 0 8px 30px rgba(0,0,0,.45); display: flex; flex-direction: column;
         overflow: hidden; min-height: 0; }
  .wh { display: flex; align-items: center; gap: 6px; padding: 8px 12px 7px;
        border-bottom: 1px solid #1a2438; flex: none; }
  .wh .wt { font-size: 13px; font-weight: 700; color: #fff; white-space: nowrap; }
  .wh .ws { flex: 1; min-width: 0; color: #6b7a97; font-size: 11px; text-align: right;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .wfold { cursor: pointer; color: #8fa1c0; border: 1px solid #2a3a55; border-radius: 6px;
           width: 18px; height: 18px; text-align: center; line-height: 16px; font-size: 12px;
           flex: none; user-select: none; }
  .wfold:hover { color: #fff; }
  .wb { padding: 8px 12px 10px; overflow-y: auto; overflow-x: hidden; }
  .wb::-webkit-scrollbar { width: 6px; }
  .wb::-webkit-scrollbar-thumb { background: #24304a; border-radius: 3px; }
  .tab { cursor: pointer; color: #8fa1c0; border: 1px solid #2a3a55; border-radius: 999px;
         padding: 1px 8px; font-size: 11px; user-select: none; }
  .tab.on { background: rgba(90,150,255,.2); border-color: #4a7fd0; color: #e2edff; }
  .pos { color: #ff6b6b; }
  .neg { color: #3ddc97; }
  .flat { color: #9aa5b1; }
  .hintxt { color: #6b7a97; font-size: 11px; }
  .suninfo { color: #8fb6ff; font-size: 11px; margin-left: 8px; }
  .empty { color: #8fa1c0; font-size: 12.5px; line-height: 1.9; padding: 4px 0; }
  .btnlink { display: inline-block; margin-top: 6px; color: #e2edff; text-decoration: none;
             background: rgba(90,150,255,.18); border: 1px solid #4a7fd0; border-radius: 8px;
             padding: 5px 10px; font-size: 12px; }
  .btnlink:hover { background: rgba(90,150,255,.32); }

  .idxgrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }
  .idxcard { background: rgba(15,25,48,.6); border: 1px solid #1e2b45; border-radius: 8px;
             padding: 6px 8px; }
  .idxcard .in { color: #9fb0cf; font-size: 11px; }
  .idxcard .ip { font-size: 15px; font-weight: 700; color: #e8eefc; line-height: 1.35; }
  .idxcard .ic { font-size: 11.5px; }
  .brow { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 8px; margin: 8px 0 4px;
          font-size: 12px; }
  .brow .bl { color: #6b7a97; font-size: 11px; }
  .brow .bv { color: #dfe6f2; font-weight: 600; }
  .sent { margin: 6px 0; font-size: 12.5px; color: #cfdaf0; }
  .lbt { font-size: 11.5px; color: #9fb0cf; margin: 4px 0 2px; line-height: 1.9; }
  .lbt i { font-style: normal; color: #ffb300; margin-left: 3px; }
  .sub2 { color: #8fa1c0; font-size: 11.5px; font-weight: 700; margin: 8px 0 2px; }
  .sec2 { color: #c8d3e8; font-size: 11.5px; font-weight: 700; margin: 10px 0 3px;
          border-top: 1px dashed #1a2438; padding-top: 7px; }
  .tb { width: 100%; border-collapse: collapse; font-size: 12px; }
  .tb td { padding: 3px 2px; border-bottom: 1px solid #141d33; color: #dfe6f2; }
  .tb td:first-child { max-width: 118px; overflow: hidden; text-overflow: ellipsis;
                       white-space: nowrap; }

  /* ===== 行情图:白底券商风格(lightweight-charts) ===== */
  #tvBox { margin-top: 8px; background: #ffffff; border: 1px solid #dfe6ee; border-radius: 8px;
           padding: 6px 4px 2px; }
  #tvMain { height: 206px; }
  #tvVol { height: 64px; border-top: 1px solid #eef1f6; }
  #tvMain, #tvVol { width: 100%; }
  #tvBox .tvline { display: flex; align-items: flex-start; gap: 4px; }
  #tvLegend { width: 58px; flex: none; padding: 2px 0 0; font-size: 10.5px; line-height: 1.8; }
  #tvLegend .lg { display: flex; align-items: center; gap: 3px; color: #6b7a97; white-space: nowrap; }
  #tvLegend .lg i { width: 8px; height: 8px; border-radius: 2px; flex: none; }
  #tvLegend .lg b { font-weight: 700; color: #2b3440; margin-left: auto; }
  #tvLegend .lg.up b { color: #e64545; }
  #tvLegend .lg.down b { color: #12a15d; }
  .tvcharts { flex: 1; min-width: 0; }
  #tvBox .chint { color: #8a94a3; font-size: 10.5px; margin: 2px 0 4px; text-align: center; }
  #tvBox .empty { color: #8a94a3; padding: 6px 8px; }
  /* 永久隐藏 TradingView 水印(与 layout.attributionLogo=false 双保险) */
  #tvBox a, #tvMain a, #tvVol a { display: none !important; visibility: hidden !important; }

  .fbar { padding: 6px 2px 4px; }
  .fbar input { width: 100%; box-sizing: border-box; background: rgba(15,25,48,.75);
                border: 1px solid #2a3a55; border-radius: 6px; color: #dfe6f2;
                font-size: 12px; padding: 5px 8px; outline: none; }
  .fbar input:focus { border-color: #4a7fd0; }
  .fchips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
  .fchip { cursor: pointer; font-size: 10.5px; color: #8fa1c0; border: 1px solid #2a3a55;
           border-radius: 999px; padding: 1px 7px; user-select: none; }
  .fchip.on { background: rgba(90,150,255,.22); border-color: #4a7fd0; color: #e2edff; }
  .fclear { cursor: pointer; font-size: 10.5px; color: #ff9d9d; border: 1px solid rgba(255,82,82,.35);
            border-radius: 999px; padding: 1px 7px; margin-left: auto; }
  .fhit { color: #6b7a97; font-size: 10.5px; margin-top: 4px; }
  .nr { padding: 7px 4px; border-bottom: 1px solid #141d33; cursor: pointer; }
  .nr:hover { background: rgba(70,110,190,.14); }
  .nr1 { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
  .nr .nt { color: #6b7a97; font-size: 11px; white-space: nowrap; }
  .nr .nk { font-size: 10px; padding: 0 5px; border-radius: 4px; white-space: nowrap; }
  .nr .kb { color: #ff9d9d; background: rgba(255,82,82,.14); border: 1px solid rgba(255,82,82,.38); }
  .nr .kn { color: #8fd0ff; background: rgba(79,195,247,.1); border: 1px solid rgba(79,195,247,.28); }
  .nr .nc { color: #9fb0cf; font-size: 11px; white-space: nowrap; }
  .nr .nd { width: 7px; height: 7px; border-radius: 50%; margin-left: auto; }
  .nr .ntx { color: #dfe6f2; font-size: 12.5px; line-height: 1.5; display: -webkit-box;
             -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .cr { display: flex; align-items: flex-start; gap: 6px; padding: 5px 2px;
        border-bottom: 1px solid #141d33; font-size: 12px; color: #dfe6f2; line-height: 1.5; }
  .cr .ct { color: #8fd0ff; font-size: 11px; white-space: nowrap; min-width: 32px; }
  .cr .cst { color: #ffb300; font-size: 10px; white-space: nowrap; }
  .cr .ck { font-size: 10px; padding: 0 5px; border-radius: 4px; white-space: nowrap;
            color: #c8d3e8; background: rgba(120,150,220,.12); border: 1px solid #2a3a55; }
  .cr .creg { color: #9fb0cf; font-size: 11px; white-space: nowrap; }
  .calAll { margin: 6px 0 2px; }
  .calAll summary { cursor: pointer; color: #6b7a97; font-size: 11.5px; }

  .airtop { display: flex; align-items: center; gap: 12px; }
  .airnum { font-size: 42px; font-weight: 800; line-height: 1; }
  .airlv { color: #fff; font-weight: 700; font-size: 12px; border-radius: 999px; padding: 3px 12px; }
  .airbar { position: relative; height: 12px; background: #1a2438; border-radius: 6px;
            margin: 10px 0 2px; overflow: hidden; }
  .airbar .fill { height: 100%; border-radius: 6px; }
  .airbar i { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255,255,255,.28); }
  .airscale { display: flex; justify-content: space-between; color: #6b7a97; font-size: 10.5px; }
  .dimr { display: flex; align-items: center; gap: 6px; margin: 4px 0; font-size: 11.5px; }
  .dimr .dn { width: 108px; color: #9fb0cf; white-space: nowrap; overflow: hidden;
              text-overflow: ellipsis; }
  .dimr .db { flex: 1; height: 9px; background: #1a2438; border-radius: 5px; overflow: hidden; }
  .dimr .db i { display: block; height: 100%; border-radius: 5px; }
  .dimr .dv { width: 26px; text-align: right; color: #dfe6f2; }
  .refr { background: rgba(15,25,48,.6); border-left: 3px solid #4a7fd0; border-radius: 6px;
          padding: 5px 9px; margin: 5px 0; font-size: 12px; color: #dfe6f2; }
  .sigfold summary { cursor: pointer; color: #8fa1c0; font-size: 11.5px; margin: 6px 0; }
  .sig { font-size: 11.5px; color: #cfdaf0; padding: 3px 0; line-height: 1.6; }
  .sig i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }

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
    <div class="wh"><span class="wt">🧺 配置标的行情</span><span class="ws">固收 / 逆回购 / 高成长 / 纳指标普</span>
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
    }

    // ---------- 左上:行情图(白底券商风格 + 缩放/拖动/十字光标) ----------
    var LC = window.LightweightCharts;
    var chMain = null, chVol = null, mainSeries = [], volSeries = null, chartMode = 'minute';
    var UP = '#e64545', DOWN = '#12a15d';
    var MA_COLORS = { '5': '#f0a500', '10': '#e91e63', '50': '#2563eb', '144': '#7c3aed' };

    function chartOpts(el, h) {
      return {
        width: (el && el.clientWidth) || 320,
        height: h,
        layout: {
          background: { color: '#ffffff' },
          textColor: '#5a6573',
          fontSize: 10,
          attributionLogo: false          // 永久移除 TradingView 水印
        },
        grid: { vertLines: { color: '#f0f3f7' }, horzLines: { color: '#f0f3f7' } },
        rightPriceScale: { borderColor: '#e0e6ee', scaleMargins: { top: 0.12, bottom: 0.12 } },
        timeScale: { borderColor: '#e0e6ee', timeVisible: true, secondsVisible: false },
        crosshair: { mode: LC && LC.CrosshairMode ? LC.CrosshairMode.Normal : 0 },
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
      return '<div class="nr" data-k="' + key + '"><div class="nr1">' +
        '<span class="nt">' + _fmtTm(it.time) + '</span>' +
        '<span class="nk ' + kc + '">' + esc(it.kind || '') + '</span>' +
        (it.country ? '<span class="nc">' + esc(it.country) + '</span>' : '') +
        '<span class="nd" style="background:' + colorOf(it.sev) + '"></span></div>' +
        '<div class="ntx">' + esc(it.title) + '</div></div>';
    }
    function renderNews() {
      var h = '';
      var hots = (HOT && HOT.items) ? HOT.items.filter(passFilter) : [];
      hits += hots.length;
      if (hots.length) {
        h += '<div class="sec2" style="border-top:none">🔥 今日热点 · ' + esc(HOT.scope || '') + ' ' +
             esc(HOT.asof || '') + '</div>';
        hots.forEach(function (it, i) { h += newsRow(it, 'hot:' + i); });
      }
      var list = newsSorted().filter(passFilter);
      var hits = list.length;
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
      $('newsBody').innerHTML = h;
      var rows = $('newsBody').querySelectorAll('.nr');
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
        h += '<tr><td>' + esc(x.name) + '</td><td>' + (x.price || '-') + '</td>' +
             '<td class="' + cls(x.day_pnl) + '">' + fmtY(x.day_pnl) + '</td>' +
             '<td class="' + cls(x.pnl) + '">' + fmtY(x.pnl) + '</td></tr>';
      });
      h += '</tbody></table>';
      $('posBody').innerHTML = h;
    }
    function renderAssets() {
      var A = HOME.assets || {};
      var groups = [['① 固收 / 货币 ETF', A.fixed], ['② 国债逆回购', A.repos],
                    ['③ 高成长主动基金', A.funds], ['④ 纳指 / 标普 ETF', A.qdii]];
      var h = '';
      groups.forEach(function (g) {
        if (!g[1] || !g[1].length) return;
        h += '<div class="sub2">' + g[0] + '</div><table class="tb"><tbody>';
        g[1].forEach(function (x) {
          h += '<tr><td>' + esc(x.name) + '</td><td>' + (x.price || x.nav || '-') + '</td>' +
               '<td class="' + cls(x.pct) + '">' + (x.pct == null || x.pct === '' ? '-' : pctTxt(x.pct)) +
               '</td></tr>';
        });
        h += '</tbody></table>';
      });
      $('assetsBody').innerHTML = h || '<div class="empty">暂无配置标的行情(重新运行脚本后生成)</div>';
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
      var body = '<div class="ev" style="background:rgba(70,110,190,.10);border-radius:8px;padding:10px;">' +
                 esc(it.title) + '</div>';
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
    renderAssets();
    renderAir();
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
    print("资产条数:", {k: len(v) for k, v in pl["assets"].items()})
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
