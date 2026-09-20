# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 持仓与自选监控模块

两块清单，都在主页右上角窗口展示：
  1) 我的持仓（positions.json，手动维护代码/成本/数量）：算市值、当日盈亏与累计盈亏
  2) 我的自选（watchlist.json，ETF / 股票 / 其他 三组）：只跟行情，不记成本、不算盈亏

数据源（走 sources.py 多源适配，异常自动切源）：
  - 场内 ETF / 股票 / 逆回购：实时行情（腾讯主源，新浪兜底）
  - 场外基金：天天基金最新净值（api.fund.eastmoney.com/f10/lsjz）

历史包袱已清掉：早期那份「家庭投资组合」写死清单（固收区/高成长区 + 纳指标普 ETF）
及其每轮白跑的两次采集、以及旧的 HTML 报告片段生成器，都已删除 ——
它们的内容作为种子写进了 watchlist.json，功能由「我的自选」承接。
"""

import json
import os
import time

import requests

import config
import sources

from config import BASE_DIR
from utils import to_float as _to_float


POS_FILE = config.POS_FILE


def load_positions():
    """读取真实持仓清单（positions.json，手动维护：代码/成本价/数量）。"""
    try:
        with open(POS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("positions", [])
    except Exception:
        return []


# ---------------------------------------------------------------- 我的自选
#
# 与「持仓」的区别：自选只跟行情，不记成本/数量、不算盈亏（要算盈亏的放 positions.json）。
# 分三组：etf（场内 ETF）/ stock（股票）/ other（场外基金、国债逆回购等非 ETF/股票品种）。
WATCH_FILE = config.WATCH_FILE
WATCH_GROUPS = ("etf", "stock", "other")

# watchlist.json 不存在时的种子自选：把原先写死的「配置标的行情」按新分组搬进来，
# 避免升级后主页右窗口突然变空。不想要就在管理页逐条删（删空后正常显示空态）。
WATCH_SEED = [
    {"tx": "sh511880", "code": "511880", "name": "银华日利ETF", "group": "etf",
     "kind": "货币ETF", "note": "场内货基，流动性≈活期"},
    {"tx": "sh511520", "code": "511520", "name": "政金债ETF富国", "group": "etf",
     "kind": "政金债ETF", "note": "跟踪中债 7-10 年政策性金融债"},
    {"tx": "sh511010", "code": "511010", "name": "国债ETF国泰", "group": "etf",
     "kind": "国债ETF", "note": "跟踪上证 5 年+10 年国债"},
    {"tx": "sh511260", "code": "511260", "name": "十年国债ETF", "group": "etf",
     "kind": "国债ETF", "note": "跟踪中证 10 年期国债"},
    {"tx": "sh511360", "code": "511360", "name": "短融ETF海富通", "group": "etf",
     "kind": "短债ETF", "note": "久期短、波动小，稳健底仓"},
    {"tx": "sh513100", "code": "513100", "name": "纳指ETF", "group": "etf",
     "kind": "QDII-ETF", "note": "跟踪纳斯达克 100"},
    {"tx": "sh513500", "code": "513500", "name": "标普500ETF", "group": "etf",
     "kind": "QDII-ETF", "note": "跟踪标普 500"},
    {"tx": "sh204001", "code": "204001", "name": "GC001", "group": "other",
     "kind": "国债逆回购", "note": "1 天期，价格=年化利率(%)"},
    {"tx": "sz131810", "code": "131810", "name": "R-001", "group": "other",
     "kind": "国债逆回购", "note": "1 天期，价格=年化利率(%)"},
    {"code": "320007", "name": "诺安成长混合", "group": "other",
     "kind": "场外基金", "note": "半导体成长风格，看净值"},
    {"code": "003095", "name": "中欧医疗健康混合A", "group": "other",
     "kind": "场外基金", "note": "医药成长风格，看净值"},
    {"code": "003834", "name": "华夏能源革新股票A", "group": "other",
     "kind": "场外基金", "note": "新能源成长风格，看净值"},
]


def guess_market(code):
    """按代码前缀判断市场（sh 沪 / sz 深），用于自动补 tx 字段。

    与 position_manager.guess_market 保持一致的规则：159/150/16 开头是深市基金，
    51/56/58/60/68 与 11（沪市可转债）走沪市，其余（0/3 等）默认深市。
    """
    code = (code or "").strip()
    if code.startswith(("159", "150", "16")):
        return "sz"
    if code.startswith(("51", "56", "58", "60", "68", "11")):
        return "sh"
    return "sz"


def _watch_item(x, group=None):
    """把一条自选规整成统一结构（只留展示字段，绝无成本/数量）。

    tx 只给「场内可交易」的品种自动补：场外基金没有实时行情（只有净值），
    补上 tx 会让采集层误以为它能走行情接口。
    """
    code = str(x.get("code") or "").strip()
    kind = str(x.get("kind") or "").strip()
    tx = str(x.get("tx") or "").strip()
    if not tx and code and "场外基金" not in kind:
        tx = guess_market(code) + code
    g = group if group in WATCH_GROUPS else str(x.get("group") or "").strip()
    return {
        "tx": tx,
        "code": code,
        "name": str(x.get("name") or "").strip(),
        "group": g if g in WATCH_GROUPS else "other",
        "kind": kind,
        "note": str(x.get("note") or "").strip(),
    }


def load_watchlist():
    """读取「我的自选」（watchlist.json）。

    只有**文件不存在**时才写入种子自选；文件存在但 items 为空，说明用户把自选
    清空了 —— 要尊重这个结果（返回空列表，主页显示空态），不能又种回种子。
    """
    if os.path.exists(WATCH_FILE):
        try:
            with open(WATCH_FILE, "r", encoding="utf-8") as f:
                items = (json.load(f) or {}).get("items") or []
            return [_watch_item(x) for x in items if isinstance(x, dict)]
        except Exception:
            return []
    return save_watchlist(WATCH_SEED)


def save_watchlist(items):
    """写回 watchlist.json，返回规整后的条目列表。"""
    out = [_watch_item(x) for x in (items or []) if isinstance(x, dict)]
    with open(WATCH_FILE, "w", encoding="utf-8") as f:
        json.dump({"_comment": "我的自选：只跟行情，不记成本/数量（要算盈亏的用 positions.json）。"
                              "group ∈ etf | stock | other。",
                   "items": out}, f, ensure_ascii=False, indent=2)
    return out

# ---------------------------------------------------------------- 采集

def _fund_nav(code):
    """天天基金最新单位净值（lsjz 接口）。返回 {nav_date, nav, acc_nav, pct} 或 None。"""
    url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=2"
    h = dict(config.HEADERS)
    h["Referer"] = "https://fundf10.eastmoney.com/"
    r = requests.get(url, headers=h, timeout=15)
    r.raise_for_status()
    j = r.json()
    lst = (j.get("Data") or {}).get("LSJZList") or []
    if not lst:
        return None
    it = lst[0]
    return {"nav_date": it.get("FSRQ"), "nav": it.get("DWJZ"),
            "acc_nav": it.get("LJJZ"), "pct": it.get("JZZZL")}


def collect_positions():
    """真实持仓监控：读 positions.json，拉实时价，计算市值/当日盈亏/持仓盈亏/收益率。"""
    pos = load_positions()
    if not pos:
        return {"items": [], "error": "positions.json 为空或不存在，请在项目目录维护持仓清单"}
    q = sources.get_realtime_quotes([pp["tx"] for pp in pos])
    items = []
    total_mv = total_cost = total_day = total_pnl = 0.0
    for pp in pos:
        d = q.get(pp["code"]) or {}
        price = _to_float(d.get("price"))
        prev = _to_float(d.get("prev_close"))
        cost = pp.get("cost")
        shares = pp.get("shares") or 0
        cost_val = cost * shares if cost else 0.0
        mv = price * shares if price is not None else None
        pnl = (mv - cost_val) if mv is not None else None
        pnl_pct = (pnl / cost_val * 100) if (pnl is not None and cost_val) else None
        day_pnl = (price - prev) * shares if (price is not None and prev is not None) else None
        day_pct = (day_pnl / (prev * shares) * 100) if (day_pnl is not None and prev) else None
        if mv is not None:
            total_mv += mv
        total_cost += cost_val
        if pnl is not None:
            total_pnl += pnl
        if day_pnl is not None:
            total_day += day_pnl
        items.append({
            "name": pp.get("name", pp["code"]), "code": pp["code"], "tx": pp["tx"],
            "shares": shares, "cost": cost,
            "price": price, "prev_close": prev,
            "mv": mv, "pnl": pnl, "pnl_pct": pnl_pct,
            "day_pnl": day_pnl, "day_pct": day_pct,
            "note": pp.get("note", ""),
        })
    total_pct = (total_pnl / total_cost * 100) if total_cost else None
    return {"items": items, "total_mv": total_mv, "total_cost": total_cost,
            "total_pnl": total_pnl, "total_pct": total_pct, "total_day": total_day,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def collect_watchlist():
    """「我的自选」行情采集，按 etf / stock / other 三组返回。

    - etf / stock：走 sources.get_realtime_quotes（现价 / 涨跌幅 / 昨收）
    - other：**场内**品种（逆回购等，有 tx）同上；**场外**基金（无 tx）走 _fund_nav 取净值
    只产出行情，不碰成本与盈亏 —— 那是 collect_positions() 的事。
    """
    items = load_watchlist()
    out = {"etf": [], "stock": [], "other": [],
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not items:
        return out

    intraday = [x for x in items if x.get("tx")]
    quotes = {}
    if intraday:
        try:
            quotes = sources.get_realtime_quotes([x["tx"] for x in intraday]) or {}
        except Exception as e:
            out["error"] = str(e)
            quotes = {}

    for it in items:
        row = {"name": it["name"] or it["code"], "code": it["code"],
               "kind": it.get("kind", ""), "note": it.get("note", "")}
        if it.get("tx"):
            d = quotes.get(it["code"]) or {}
            row["price"] = d.get("price")
            row["pct"] = d.get("pct")
            row["prev_close"] = d.get("prev_close")
            # 逆回购的"价格"就是年化利率%，前端要带 % 显示
            row["is_rate"] = "逆回购" in (it.get("kind") or "")
        else:
            nav = None
            try:
                nav = _fund_nav(it["code"])
            except Exception:
                nav = None
            row["price"] = nav.get("nav") if nav else None
            row["pct"] = nav.get("pct") if nav else None
            row["nav_date"] = nav.get("nav_date") if nav else None
            row["is_nav"] = True
        out[it.get("group") or "other"].append(row)
    return out


def collect_all():
    """采集持仓与自选的全部行情数据。

    返回 {time, watchlist, positions}。以前还有 fixed_income / high_risk 两组「写死清单」
    的行情 —— 那是主页旧「配置标的行情」窗口用的，v3.1.2 起已被「我的自选」取代，
    没人再读它，所以连同那两次每轮都白跑的网络请求一起删掉了。
    """
    result = {"time": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        result["watchlist"] = collect_watchlist()
    except Exception as e:
        result["watchlist"] = {"etf": [], "stock": [], "other": [], "error": str(e)}
    try:
        result["positions"] = collect_positions()
    except Exception as e:
        result["positions"] = {"items": [], "error": str(e)}
    return result
