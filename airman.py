# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 罗力豪空中飞人指数模块

「空中飞人」比喻：泡沫越高、钢丝越细，爆破风险越大，指数越高。
指数为 0-100 分制，越高代表「泡沫爆破 / 经济危机」风险越大。

【评分标准已固定，全自动】19 个计分子项全部由固定阈值规则驱动，
程序每次运行实时抓取数据并自动打分，无需任何人工填分。

构成（权重已固定）：
  A 估值/市场过热 20%   B 盈利/成本挤压 15%   C 杠杆/财政债务 15%
  D 流动性/利率 25%     E 政治/宏观 25%

数据源（全部公开接口，已验证可用）：
  FRED（圣路易斯联储）：SP500 / NASDAQCOM / VIXCLS / CPILFESL / GFDEBTN / GFDEGDQ188S /
                         FEDFUNDS / DGS10 / DGS2 / DGS30 / T10Y2Y / USEPUINDXD / DTWEXBGS /
                         BAMLH0A0HYM2（高收益债利差）/ DFII10（10年期TIPS实际利率）
  中证指数官网：沪深300 市盈率历史分位（index-perf 的 peg 字段）
  东方财富数据中心：两市融资余额（RPTA_WEB_RZRQ_GGMX 按最近交易日全市场求和）
  腾讯：纽约黄金 hf_GC、纽约原油 hf_CL
  新浪：外汇 USD/JPY

【参考指标】恐慌指数（VIX）与黄金价格作为独立参考展示在指数下方，
不计入总分（避免与 A3 VIX 重复计分）。

分级：0-25 安全区 | 26-50 警戒区 | 51-75 高危区 | 76-100 爆破临界区

输出：generate_report_section() 返回 HTML 内容片段，由 main.py 嵌入综合报告第二部分。
"""

import os
import re
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_NAME = "罗力豪空中飞人指数"

# ---------------------------------------------------------------- 固定权重
WEIGHTS = {"A": 0.20, "B": 0.15, "C": 0.15, "D": 0.25, "E": 0.25}

LEVELS = [
    (76, "爆破临界区", "red"),
    (51, "高危区", "orange"),
    (26, "警戒区", "yellow"),
    (0, "安全区", "green"),
]

# ---------------------------------------------------------------- 工具

def _http_get(url, headers=None, timeout=12):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=timeout)


def _fred_rows(sid):
    """FRED 公开 CSV：返回 [(date, value), ...] 按时间升序（去重保留首个）。"""
    r = _http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=12)
    r.raise_for_status()
    rows = []
    seen = set()
    for line in r.text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) >= 2 and parts[1] and parts[1] != ".":
            d = parts[0]
            if d in seen:
                continue
            seen.add(d)
            try:
                rows.append((d, float(parts[1])))
            except ValueError:
                continue
    return rows


def _fred_latest(sid):
    rows = _fred_rows(sid)
    return rows[-1][1] if rows else None


def _fred_drawdown(sid, lookback=252):
    """当前价相对近 lookback 个交易日最高点的回撤%（负值）。"""
    rows = _fred_rows(sid)
    if len(rows) < 2:
        return None
    latest = rows[-1][1]
    window = rows[-lookback:]
    high = max(v for _, v in window)
    if high <= 0:
        return None
    return (latest / high - 1) * 100


def _fred_yoy(sid, lag):
    """最新值相对 lag 期前的同比增幅%。"""
    rows = _fred_rows(sid)
    if len(rows) <= lag:
        return None
    prev = rows[-1 - lag][1]
    if prev <= 0:
        return None
    return (rows[-1][1] / prev - 1) * 100


def _get_usdjpy():
    """新浪外汇 USD/JPY。返回 (现价, 涨跌幅%)。"""
    r = _http_get("https://hq.sinajs.cn/list=fx_susdjpy",
                  headers={"Referer": "https://finance.sina.com.cn/"}, timeout=8)
    m = re.search(r'="([^"]*)"', r.text)
    if not m:
        return None, None
    f = m.group(1).split(",")
    try:
        price = float(f[1])
        chg = float(f[12]) * 100 if len(f) > 12 and f[12] else None
        return price, chg
    except (ValueError, IndexError):
        return None, None


def _get_tencent_fut(code):
    """腾讯期货/贵金属行情。返回 (现价, 涨跌幅%)。"""
    r = _http_get(f"https://qt.gtimg.cn/q={code}", timeout=8)
    m = re.search(r'="([^"]*)"', r.text)
    if not m:
        return None, None
    f = m.group(1).split(",")
    try:
        return float(f[0]), f[1] if len(f) > 1 and f[1] else None
    except (ValueError, IndexError):
        return None, None


# ---------------------------------------------------------------- 固定评分规则（0-10 分，越高风险越大）

def _rule_drawdown(dd):
    """距 52 周高点回撤%（负值）：回撤越小=越贴高点=越过热。"""
    r = -(dd if dd is not None else 99)
    if r < 2:
        return 9
    if r < 5:
        return 7
    if r < 10:
        return 5
    if r < 20:
        return 3
    return 1


def _rule_vix(v):
    """VIX 越低=市场越自满=泡沫隐患越大。"""
    if v < 12:
        return 9
    if v < 15:
        return 7
    if v < 20:
        return 5
    if v < 25:
        return 3
    return 1


def _rule_cpi_yoy(v):
    """核心 CPI 同比：通胀越高=成本挤压盈利越重。"""
    if v < 2:
        return 2
    if v < 3:
        return 4
    if v < 4:
        return 6
    if v < 5:
        return 8
    return 9


def _rule_oil(v):
    """原油价格：越高=能源成本/供应链压力越大。"""
    if v < 60:
        return 2
    if v < 80:
        return 4
    if v < 100:
        return 6
    if v < 120:
        return 7
    return 8


def _rule_debt_gdp(v):
    """美国债务/GDP：越高=财政可持续性越差。"""
    if v < 100:
        return 4
    if v < 115:
        return 6
    if v < 125:
        return 7
    if v < 135:
        return 8
    return 9


def _rule_debt_yoy(v):
    """美国债务总额同比：增速越快=财政扩张越激进。"""
    if v < 5:
        return 3
    if v < 7:
        return 5
    if v < 9:
        return 7
    return 8


def _rule_fedfunds(v):
    """联邦基金利率：越高=资金成本/利息负担越重。"""
    if v < 2:
        return 2
    if v < 3:
        return 4
    if v < 4:
        return 6
    if v < 5:
        return 7
    return 8


def _rule_dgs10(v):
    """10 年期美债收益率：越高=压制成长股估值、融资成本越贵。"""
    if v < 3:
        return 2
    if v < 3.5:
        return 4
    if v < 4:
        return 6
    if v < 4.5:
        return 8
    if v < 5:
        return 9
    return 10


def _rule_dgs2(v):
    """2 年期美债收益率：越高=短端收紧/加息预期越强。"""
    if v < 3:
        return 2
    if v < 4:
        return 4
    if v < 5:
        return 6
    if v < 5.5:
        return 7
    return 8


def _rule_usdjpy(v):
    """USD/JPY：越高=日元越弱=套息交易累积越重、解除风险越大。"""
    if v < 120:
        return 2
    if v < 135:
        return 4
    if v < 150:
        return 6
    if v < 165:
        return 8
    return 10


def _rule_t10y2y(v):
    """10Y-2Y 利差：倒挂越深=衰退/盈利下修预警越强。"""
    if v < -0.5:
        return 9
    if v < 0:
        return 7
    if v < 0.5:
        return 4
    if v < 1:
        return 3
    return 2


def _rule_epu(v):
    """经济政策不确定性指数：越高=政策/地缘风险越大。"""
    if v < 150:
        return 3
    if v < 200:
        return 5
    if v < 250:
        return 7
    if v < 300:
        return 8
    return 9


def _rule_dxy(v):
    """美元广义指数：越高=全球美元流动性越紧。"""
    if v < 110:
        return 3
    if v < 115:
        return 5
    if v < 120:
        return 7
    if v < 125:
        return 8
    return 9


def _rule_dgs30(v):
    """30 年期美债收益率：越高=财政/期限溢价担忧越重。"""
    if v < 3.5:
        return 2
    if v < 4:
        return 4
    if v < 4.5:
        return 6
    if v < 5:
        return 7
    if v < 5.5:
        return 8
    return 9


def _rule_pe_pct(pct):
    """沪深300 PE 历史分位（%）：分位越高=估值越贵。"""
    if pct >= 85:
        return 9
    if pct >= 70:
        return 7
    if pct >= 50:
        return 5
    if pct >= 25:
        return 3
    return 1


def _rule_rzrq(bal):
    """两市融资余额（亿元）：融资盘越大=场内杠杆越热。"""
    if bal >= 28000:
        return 9
    if bal >= 25000:
        return 7
    if bal >= 20000:
        return 5
    if bal >= 15000:
        return 3
    return 1


def _rule_hyoas(v):
    """高收益债信用利差（%）：极低=信用自满（泡沫隐患），极高=信用压力已爆发。"""
    if v < 3.0:
        return 7
    if v < 4.0:
        return 5
    if v < 5.5:
        return 3
    if v < 7.0:
        return 6
    return 9


def _rule_dfii10(v):
    """10 年期 TIPS 实际利率（%）：越高=真实流动性越紧。"""
    if v >= 2.5:
        return 8
    if v >= 2.0:
        return 6
    if v >= 1.5:
        return 4
    if v >= 1.0:
        return 2
    return 1


# ---------------------------------------------------------------- 评分卡定义（固定标准）

DIMS = [
    {"key": "A", "name": "估值 / 市场过热", "weight": WEIGHTS["A"], "items": [
        {"key": "A1", "name": "标普500 距52周高点", "src": "FRED SP500", "rule": _rule_drawdown,
         "fetch": "sp500_dd", "fmt": "回撤 {v:.1f}%"},
        {"key": "A2", "name": "纳指 距52周高点", "src": "FRED NASDAQCOM", "rule": _rule_drawdown,
         "fetch": "nasdaq_dd", "fmt": "回撤 {v:.1f}%"},
        {"key": "A3", "name": "VIX 市场自满度", "src": "FRED VIXCLS", "rule": _rule_vix,
         "fetch": "vix", "fmt": "{v:.1f}"},
        {"key": "A4", "name": "沪深300 PE 历史分位", "src": "中证指数官网", "rule": _rule_pe_pct,
         "fetch": "csi300_pe_pct", "fmt": "{v:.0f}%（近6年分位）"},
    ]},  # A 结束
    {"key": "B", "name": "盈利 / 成本挤压", "weight": WEIGHTS["B"], "items": [
        {"key": "B1", "name": "核心CPI同比", "src": "FRED CPILFESL", "rule": _rule_cpi_yoy,
         "fetch": "cpi_yoy", "fmt": "{v:.2f}%"},
        {"key": "B2", "name": "原油价格", "src": "腾讯 hf_CL", "rule": _rule_oil,
         "fetch": "oil", "fmt": "{v:.0f} 美元/桶"},
    ]},
    {"key": "C", "name": "杠杆 / 财政债务", "weight": WEIGHTS["C"], "items": [
        {"key": "C1", "name": "美国债务/GDP", "src": "FRED GFDEGDQ188S", "rule": _rule_debt_gdp,
         "fetch": "debt_gdp", "fmt": "{v:.1f}%"},
        {"key": "C2", "name": "美国债务总额同比", "src": "FRED GFDEBTN", "rule": _rule_debt_yoy,
         "fetch": "debt_yoy", "fmt": "{v:.1f}%"},
        {"key": "C3", "name": "联邦基金利率", "src": "FRED FEDFUNDS", "rule": _rule_fedfunds,
         "fetch": "fedfunds", "fmt": "{v:.2f}%"},
        {"key": "C4", "name": "两市融资余额", "src": "东财数据中心", "rule": _rule_rzrq,
         "fetch": "rzrq_balance", "fmt": "{v:.0f} 亿元"},
    ]},  # C 结束
    {"key": "D", "name": "流动性 / 利率", "weight": WEIGHTS["D"], "items": [
        {"key": "D1", "name": "10年期美债收益率", "src": "FRED DGS10", "rule": _rule_dgs10,
         "fetch": "dgs10", "fmt": "{v:.2f}%"},
        {"key": "D2", "name": "2年期美债收益率", "src": "FRED DGS2", "rule": _rule_dgs2,
         "fetch": "dgs2", "fmt": "{v:.2f}%"},
        {"key": "D3", "name": "USD/JPY 套息", "src": "新浪外汇", "rule": _rule_usdjpy,
         "fetch": "usdjpy", "fmt": "{v:.2f}"},
        {"key": "D4", "name": "收益率曲线(10Y-2Y)", "src": "FRED T10Y2Y", "rule": _rule_t10y2y,
         "fetch": "t10y2y", "fmt": "{v:+.2f}%" if False else "{v:+.2f}"},
        {"key": "D5", "name": "高收益债信用利差", "src": "FRED BAMLH0A0HYM2", "rule": _rule_hyoas,
         "fetch": "hyoas", "fmt": "{v:.2f}%"},
        {"key": "D6", "name": "10年期实际利率(TIPS)", "src": "FRED DFII10", "rule": _rule_dfii10,
         "fetch": "dfii10", "fmt": "{v:.2f}%"},
    ]},  # D 结束
    {"key": "E", "name": "政治 / 宏观", "weight": WEIGHTS["E"], "items": [
        {"key": "E1", "name": "政策不确定性指数", "src": "FRED USEPUINDXD", "rule": _rule_epu,
         "fetch": "epu", "fmt": "{v:.0f}"},
        {"key": "E2", "name": "美元广义指数", "src": "FRED DTWEXBGS", "rule": _rule_dxy,
         "fetch": "dxy", "fmt": "{v:.1f}"},
        {"key": "E3", "name": "30年期美债收益率", "src": "FRED DGS30", "rule": _rule_dgs30,
         "fetch": "dgs30", "fmt": "{v:.2f}%"},
    ]},
]

# 参考指标（不计入总分，仅展示在指数下方）
REFERENCE_ITEMS = [
    {"key": "VIX", "name": "恐慌指数（VIX）", "src": "FRED VIXCLS",
     "note": "市场恐慌/自满的标尺，类似高盛金融状况指数(FCI)、美银牛熊指标等投行风险指数；"
             "越低越自满、越高越恐慌。作为情绪参考。"},
    {"key": "GOLD", "name": "黄金价格", "src": "腾讯 hf_GC",
     "note": "避险资产，地缘冲突、通胀与财政担忧升温时金价往往走高，作为尾部风险参考。"},
]


def _csi300_pe_pct():
    """沪深300 市盈率历史分位（%）。数据来自中证指数官网（index-perf 的 peg=市盈率），
    官方数据存在滞后（最新仅到约 2025 年底），故仅作结构性估值参考。"""
    try:
        url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
               "?indexCode=000300&startDate=2020-01-01&endDate=2030-01-01")
        r = _http_get(url, headers={"Referer": "https://www.csindex.com.cn/"}, timeout=15)
        j = r.json()
        vals = [x.get("peg") for x in (j.get("data") or []) if x.get("peg") is not None]
        if len(vals) < 60:
            return None
        cur = vals[-1]
        return sum(1 for v in vals if v <= cur) / len(vals) * 100.0
    except Exception:
        return None


def _rzrq_balance():
    """两市融资余额合计（亿元）。东财融资融券个股明细按最近交易日翻页求和。"""
    from urllib.parse import urlencode, quote
    try:
        base = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        head = {"Referer": "https://data.eastmoney.com/"}
        # 1) 取最新交易日
        p = {"reportName": "RPTA_WEB_RZRQ_GGMX", "columns": "ALL", "pageSize": "1",
             "pageNumber": "1", "sortColumns": "DATE", "sortTypes": "-1",
             "source": "WEB", "client": "WEB"}
        r = _http_get(base + "?" + urlencode(p), headers=head, timeout=12)
        j = r.json()
        rows = (j.get("result") or {}).get("data") or []
        if not rows or not rows[0].get("DATE"):
            return None
        date = rows[0]["DATE"]
        # 2) 按日期翻页求和（全市场 RZYE 融资余额）
        total = 0.0
        pn = 1
        while pn <= 15:
            f = quote("(DATE='%s')" % date)
            q = urlencode({"reportName": "RPTA_WEB_RZRQ_GGMX", "columns": "ALL",
                           "pageSize": "500", "pageNumber": str(pn),
                           "source": "WEB", "client": "WEB"}) + "&filter=" + f
            r = _http_get(base + "?" + q, headers=head, timeout=12)
            j = r.json()
            res = j.get("result") or {}
            data = res.get("data") or []
            if not data:
                break
            for x in data:
                if x.get("RZYE"):
                    total += float(x["RZYE"])
            if pn * 500 >= (res.get("count") or 0):
                break
            pn += 1
        return total / 1e8
    except Exception:
        return None


# ---------------------------------------------------------------- 采集

def collect_all():
    """采集全部计分子项原始数据。返回 {fetch_key: value}。"""
    raw = {}
    # A 估值/过热
    raw["sp500_dd"] = _fred_drawdown("SP500")
    raw["nasdaq_dd"] = _fred_drawdown("NASDAQCOM")
    raw["vix"] = _fred_latest("VIXCLS")
    # B 盈利/成本
    raw["cpi_yoy"] = _fred_yoy("CPILFESL", 12)
    raw["oil"], _ = _get_tencent_fut("hf_CL")
    # C 杠杆/财政
    raw["debt_gdp"] = _fred_latest("GFDEGDQ188S")
    raw["debt_yoy"] = _fred_yoy("GFDEBTN", 4)
    raw["fedfunds"] = _fred_latest("FEDFUNDS")
    # A4 沪深300 估值分位
    raw["csi300_pe_pct"] = _csi300_pe_pct()
    # C4 两市融资余额
    raw["rzrq_balance"] = _rzrq_balance()
    # D 流动性/利率
    raw["dgs10"] = _fred_latest("DGS10")
    raw["dgs2"] = _fred_latest("DGS2")
    raw["usdjpy"], raw["usdjpy_chg"] = _get_usdjpy()
    raw["t10y2y"] = _fred_latest("T10Y2Y")
    raw["hyoas"] = _fred_latest("BAMLH0A0HYM2")
    raw["dfii10"] = _fred_latest("DFII10")
    # E 政治/宏观
    raw["epu"] = _fred_latest("USEPUINDXD")
    raw["dxy"] = _fred_latest("DTWEXBGS")
    raw["dgs30"] = _fred_latest("DGS30")
    return raw


def collect_reference(raw):
    """采集参考指标（不计分）：恐慌指数 VIX、黄金价格。"""
    refs = []
    vix = raw.get("vix")
    if vix is not None:
        refs.append({"key": "VIX", "name": REFERENCE_ITEMS[0]["name"], "src": REFERENCE_ITEMS[0]["src"],
                     "display": f"{vix:.1f}", "note": REFERENCE_ITEMS[0]["note"]})
    gold, chg = _get_tencent_fut("hf_GC")
    if gold is not None:
        disp = f"{gold:.0f}"
        if chg:
            try:
                disp += f"（日 {float(chg):+.2f}%）"
            except ValueError:
                pass
        refs.append({"key": "GOLD", "name": REFERENCE_ITEMS[1]["name"], "src": REFERENCE_ITEMS[1]["src"],
                     "display": disp, "note": REFERENCE_ITEMS[1]["note"]})
    return refs


# ---------------------------------------------------------------- 计算

def compute_index(raw):
    """按固定评分卡汇总计算指数。返回结果 dict。"""
    dim_results = []
    signals = []
    for dim in DIMS:
        item_scores = []
        for it in dim["items"]:
            v = raw.get(it["fetch"])
            if v is None:
                signals.append({"key": it["key"], "name": it["name"], "score": None,
                                "status": "miss", "note": "数据获取失败", "auto": True,
                                "raw_text": ""})
                continue
            score = it["rule"](v)
            item_scores.append(score)
            raw_text = it["fmt"].format(v=v)
            status = "red" if score >= 7 else ("orange" if score >= 5 else "green")
            signals.append({"key": it["key"], "name": it["name"], "score": score,
                            "status": status, "note": raw_text, "auto": True,
                            "raw_text": raw_text})
        dim_score = sum(item_scores) / len(item_scores) if item_scores else None
        dim_results.append({"key": dim["key"], "name": dim["name"],
                            "weight": dim["weight"], "score": dim_score,
                            "n_items": len(dim["items"]), "n_scored": len(item_scores)})

    # 总分：只按已评分维度加权；某维度全缺则按其权重归一化
    total = 0.0
    w_sum = 0.0
    for d in dim_results:
        if d["score"] is not None:
            total += d["score"] * d["weight"]
            w_sum += d["weight"]
    if w_sum > 0:
        total = total / w_sum * 10.0   # 维度分为 0-10 制，转 0-100 需 ×10
    total = max(0.0, min(100.0, total))

    level_name, level_color = "安全区", "green"
    for low, name, color in LEVELS:
        if total >= low:
            level_name, level_color = name, color
            break

    return {
        "total": total,
        "level_name": level_name,
        "level_color": level_color,
        "dims": dim_results,
        "signals": signals,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------- HTML 片段

def generate_report_section():
    """生成「罗力豪空中飞人指数」HTML 内容片段（综合报告第二部分）。"""
    raw = collect_all()
    res = compute_index(raw)
    refs = collect_reference(raw)
    total = res["total"]
    lv = res["level_name"]
    lc = res["level_color"]

    LV_COLORS = {"red": "#e64545", "orange": "#e8963a", "yellow": "#d9a41b", "green": "#12a15d"}
    lv_badge = (f'<span style="display:inline-block;padding:6px 16px;border-radius:8px;color:#fff;'
                f'font-weight:700;font-size:14px;background:{LV_COLORS.get(lc, "#5a6573")};">{lv}</span>')
    gauge = (f'<div style="display:flex;align-items:center;gap:22px;flex-wrap:wrap;">'
             f'<div style="text-align:center;min-width:150px;">'
             f'<div style="font-size:58px;font-weight:800;color:#1f2d3d;line-height:1">{total:.0f}</div>'
             f'<div class="hint">总分 / 100</div></div>'
             f'<div style="flex:1;min-width:260px;">'
             f'<div style="font-size:15px;margin-bottom:8px;">当前等级：{lv_badge}</div>'
             f'<div class="yieldbar" style="height:14px;">'
             f'<div class="fill" style="width:{total:.1f}%;background:linear-gradient(90deg,#12a15d,#f6c344,#e64545)"></div>'
             f'<div class="mark" style="left:25%"></div><div class="mark" style="left:50%"></div>'
             f'<div class="mark" style="left:75%"></div></div>'
             f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#8a94a3;margin-top:2px;">'
             f'<span>0 安全</span><span>25</span><span>50 警戒</span><span>75 高危</span><span>100 临界</span></div>'
             f'</div></div>')

    # 五维得分
    dim_html = ""
    for d in res["dims"]:
        if d["score"] is None:
            dim_html += (f'<div style="display:flex;align-items:center;margin:6px 0;">'
                         f'<div style="width:130px;font-size:13px;color:#4a5568;">{d["key"]} {d["name"]}'
                         f'<span class="hint">（{d["weight"]*100:.0f}%）</span></div>'
                         f'<div style="flex:1;font-size:12px;color:#8a94a3;">数据缺失</div></div>')
            continue
        color = "#e64545" if d["score"] >= 7 else ("#e8963a" if d["score"] >= 5 else "#12a15d")
        dim_html += (f'<div style="display:flex;align-items:center;margin:6px 0;">'
                     f'<div style="width:130px;font-size:13px;color:#4a5568;">{d["key"]} {d["name"]}'
                     f'<span class="hint">（{d["weight"]*100:.0f}%）</span></div>'
                     f'<div style="flex:1;background:#f1f4f9;border-radius:6px;height:18px;position:relative;overflow:hidden;">'
                     f'<div style="height:100%;width:{max(2,d["score"]/10*100):.1f}%;background:{color};border-radius:6px;"></div></div>'
                     f'<div style="width:44px;text-align:right;font-size:13px;font-weight:600;color:#2b3440;">{d["score"]:.1f}</div>'
                     f'</div>')

    # 信号清单
    sig_html = ""
    n_miss = 0
    for s in res["signals"]:
        if s["status"] == "miss":
            n_miss += 1
            sig_html += (f'<div class="signal" style="border-left-color:#8a94a3;">'
                         f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#8a94a3;margin-right:6px;"></span>'
                         f'<b>{s["key"]} {s["name"]}</b>　<span class="hint">数据获取失败，未计分</span></div>')
            continue
        dot = "#e64545" if s["status"] == "red" else ("#e8963a" if s["status"] == "orange" else "#12a15d")
        sig_html += (f'<div class="signal" style="border-left-color:{dot}">'
                     f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{dot};margin-right:6px;"></span>'
                     f'<b>{s["key"]} {s["name"]}</b>　分 {s["score"]:.0f}　'
                     f'<span class="hint">{s["raw_text"]}</span></div>')

    # 参考指标（不计分）
    ref_html = ""
    for r in refs:
        ref_html += (f'<div style="background:#fff;border-left:3px solid #2563eb;border-radius:6px;'
                     f'padding:10px 14px;margin:8px 0;">'
                     f'<div><b>{r["name"]}</b>　'
                     f'<span style="font-size:17px;font-weight:700;color:#1f2d3d;">{r["display"]}</span>'
                     f'<span class="hint">　{r["src"]}</span></div>'
                     f'<div class="hint" style="margin-top:2px;">{r["note"]}</div></div>')

    miss_note = f'　|　<span class="hint">其中 {n_miss} 项数据获取失败未计分</span>' if n_miss else ""

    section = f"""
  <!-- 第三部分：罗力豪空中飞人指数 -->
  <section class="card divider">
    <h2>第三部分 · {INDEX_NAME}</h2>
    <div class="hint" style="margin-bottom:10px">
      定义：泡沫越高、钢丝越细，爆破风险越大，指数越高（0-100，越高越危险）。{res["time"]} 测算。
      评分标准固定，全部指标由公开数据自动采集、按固定阈值打分，无人工项。
    </div>
    {gauge}
    <details class="fold" style="margin-top:14px;">
      <summary><span>五维风险得分（固定权重：D/E 各 25%，A 20%，B/C 各 15%）{miss_note}</span><span class="btn-tag"></span></summary>
      <div style="margin-top:8px;">{dim_html}</div>
    </details>
    <details class="fold" style="margin-top:12px;">
      <summary><span>已计分信号清单（红=高危触发，橙=临界，绿=正常）</span><span class="btn-tag"></span></summary>
      <div style="margin-top:8px;">{sig_html}</div>
    </details>
    <details class="fold" style="margin-top:12px;">
      <summary><span>参考指标（独立观察，不计入总分）</span><span class="btn-tag"></span></summary>
      <div style="margin-top:8px;">{ref_html}</div>
    </details>
    <div class="hint" style="margin-top:10px">数据源：FRED（美债/通胀/VIX/美元/债务/政策不确定性等）、腾讯（黄金/原油）、新浪（USD/JPY）</div>
  </section>"""
    return section


if __name__ == "__main__":
    raw = collect_all()
    r = compute_index(raw)
    print(f"总分: {r['total']:.1f}  等级: {r['level_name']}")
    for d in r["dims"]:
        print(f"  {d['key']} {d['name']}: {d['score'] if d['score'] is None else round(d['score'],1)} (权重 {d['weight']*100:.0f}%)")
    for s in r["signals"]:
        print(f"  {s['key']} {s['name']}: {s['score']} [{s['raw_text']}]")
    for rf in collect_reference(raw):
        print(f"参考 {rf['key']}: {rf['display']}")
