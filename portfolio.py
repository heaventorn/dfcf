# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 个人组合监控模块

根据「家庭投资组合」做每日监控，覆盖三类核心资产：
  1) 低风险固收区（约 48%）：货币ETF / 国债ETF / 短债ETF / 国债逆回购
  2) 红利低波区（约 30%）：红利 / 红利低波 ETF（详细股息率分析见 dividend.py）
  3) 高成长权益区（约 20%）：国内高风险主动基金 + 纳指/标普 ETF

数据源（走 sources.py 多源适配）：
  - 货币 / 债 / 逆回购：腾讯实时行情（get_realtime_quotes，新浪兜底）
  - 主动基金：天天基金净值接口（api.fund.eastmoney.com/f10/lsjz）
  - 纳指/标普 ETF：腾讯实时行情（get_realtime_quotes）
异常自动切源 / 容错，某类数据失败不影响其它部分。
"""

import json
import os
import re
import time

import requests

import config
import sources
import tech

from config import BASE_DIR, OUTPUT_DIR
from utils import to_float as _to_float, fmt_price as _fmt_price, fmt_pct as _fmt_pct


POS_FILE = os.path.join(BASE_DIR, "positions.json")


def load_positions():
    """读取真实持仓清单（positions.json，手动维护：代码/成本价/数量）。"""
    try:
        with open(POS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("positions", [])
    except Exception:
        return []

# ---------------------------------------------------------------- 标的配置

# 低风险固收区（约 50% 仓位）
FIXED_INCOME = [
    {"tx": "sh511880", "code": "511880", "name": "银华日利ETF", "kind": "货币ETF",
     "note": "场内货基，净值约100元，流动性≈活期"},
    {"tx": "sh511520", "code": "511520", "name": "政金债ETF富国", "kind": "政金债ETF",
     "note": "跟踪中债7-10年政策性金融债指数，久期较长、利率敏感"},
    {"tx": "sh511010", "code": "511010", "name": "国债ETF国泰", "kind": "国债ETF",
     "note": "跟踪上证5年+10年国债，久期较长、利率敏感"},
    {"tx": "sh511260", "code": "511260", "name": "十年国债ETF", "kind": "国债ETF",
     "note": "跟踪中证10年期国债，久期长，利率上行时回撤偏大"},
    {"tx": "sh511360", "code": "511360", "name": "短融ETF海富通", "kind": "短债ETF",
     "note": "跟踪中证短融，久期短、波动小，稳健底仓"},
]

# 国债逆回购（行情价格即年化利率 %）
REPO = [
    {"tx": "sh204001", "code": "204001", "name": "GC001", "kind": "沪市逆回购",
     "note": "1天期，价格=年化利率(%)，T+1资金可用"},
    {"tx": "sz131810", "code": "131810", "name": "R-001", "kind": "深市逆回购",
     "note": "1天期，价格=年化利率(%)，T+1资金可用"},
]

# 国内高风险主动基金（约 25% 仓位中的基金部分，示例标的，可自行替换）
HIGH_RISK_FUNDS = [
    {"code": "320007", "name": "诺安成长混合", "note": "半导体成长风格，高波动"},
    {"code": "003095", "name": "中欧医疗健康混合A", "note": "医药成长风格，高波动"},
    {"code": "003834", "name": "华夏能源革新股票A", "note": "新能源成长风格，高波动"},
]

# 纳指 / 标普 ETF（场内可交易，替代直接买美股）
QDII_ETF = [
    {"tx": "sh513100", "code": "513100", "name": "纳指ETF", "note": "跟踪纳斯达克100，场内T+1"},
    {"tx": "sh513500", "code": "513500", "name": "标普500ETF", "note": "跟踪标普500，场内T+1"},
]


# ---------------------------------------------------------------- 采集

def collect_fixed_income():
    """低风险固收 + 国债逆回购实时行情。返回 {etfs: [...], repos: [...]}"""
    etf_codes = [e["tx"] for e in FIXED_INCOME]
    repo_codes = [r["tx"] for r in REPO]
    q = sources.get_realtime_quotes(etf_codes + repo_codes)

    etfs = []
    for e in FIXED_INCOME:
        d = q.get(e["code"]) or {}
        etfs.append({"name": e["name"], "kind": e["kind"], "note": e["note"],
                     "price": d.get("price"), "pct": d.get("pct")})
    repos = []
    for r in REPO:
        d = q.get(r["code"]) or {}
        repos.append({"name": r["name"], "kind": r["kind"], "note": r["note"],
                      "price": d.get("price"), "pct": d.get("pct")})
    return {"etfs": etfs, "repos": repos}


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


def collect_high_risk():
    """国内高风险主动基金（天天基金净值）+ 纳指/标普 ETF（腾讯行情）。"""
    funds = []
    for f in HIGH_RISK_FUNDS:
        try:
            nav = _fund_nav(f["code"])
        except Exception:
            nav = None
        funds.append({"name": f["name"], "note": f["note"],
                      "nav_date": nav.get("nav_date") if nav else None,
                      "nav": nav.get("nav") if nav else None,
                      "acc_nav": nav.get("acc_nav") if nav else None,
                      "pct": nav.get("pct") if nav else None})
    q = sources.get_realtime_quotes([e["tx"] for e in QDII_ETF])
    etfs = []
    for e in QDII_ETF:
        d = q.get(e["code"]) or {}
        etfs.append({"name": e["name"], "note": e["note"],
                     "price": d.get("price"), "pct": d.get("pct")})
    return {"funds": funds, "etfs": etfs}


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


def collect_all():
    """采集全部个人组合监控数据。返回 {fixed_income, us_stocks, time}"""
    result = {"time": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        result["fixed_income"] = collect_fixed_income()
    except Exception as e:
        result["fixed_income"] = {"etfs": [], "repos": [], "error": str(e)}
    try:
        result["high_risk"] = collect_high_risk()
    except Exception as e:
        result["high_risk"] = {"error": str(e)}
    try:
        result["positions"] = collect_positions()
    except Exception as e:
        result["positions"] = {"items": [], "error": str(e)}
    return result


# ---------------------------------------------------------------- HTML 报告片段

def _pct_cls(v):
    if v is None:
        return "flat"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "flat"
    return "pos" if v > 0 else ("neg" if v < 0 else "flat")








def generate_report_section(data, dividend_cards=""):
    """生成个人组合监控的 HTML 内容片段（嵌入综合报告，第二部分）。

    dividend_cards: 红利/红利低波 ETF 追踪卡片的 HTML 片段
    （dividend.generate_cards 生成），作为本部分的「② 红利低波区」子区块嵌入。
    """

    fi = (data or {}).get("fixed_income") or {}
    hr = (data or {}).get("high_risk") or {}
    pos = (data or {}).get("positions") or {}
    pos_items = pos.get("items") or []
    if pos_items:
        pos_rows = ""
        for it in pos_items:
            pos_rows += (
                f"<tr><td><b>{it['name']}</b><div class='hint'>{it['code']}</div></td>"
                f"<td>{it['shares']}</td>"
                f"<td>{_fmt_price(it.get('price'))}</td>"
                f"<td>{_fmt_price(it.get('cost'))}</td>"
                f"<td>{_fmt_price(it.get('mv'))}</td>"
                f"<td class='{_pct_cls(it.get('day_pnl'))}'>{_fmt_price(it.get('day_pnl'))}</td>"
                f"<td class='{_pct_cls(it.get('pnl'))}'>{_fmt_price(it.get('pnl'))}"
                f"<span class='hint'> ({_fmt_pct(it.get('pnl_pct'))})</span></td>"
                f"<td style='text-align:left;color:#8a94a3;font-size:12px'>{it.get('note','')}</td></tr>"
            )
        tech_blocks = ""
        for _ti, it in enumerate(pos_items):
            _tc = it.get("tx") or ""
            if _tc:
                tech_blocks += tech.build_tech_card(_tc, it["name"], embed_echarts=(_ti == 0))
        pos_block = f'''
  <details class="card" open>
    <summary style="font-size:15px;font-weight:700;color:#1f2d3d;cursor:pointer;user-select:none;">⭐ 我的真实持仓 · 自动盯价 <a href="http://127.0.0.1:8765" target="_blank" style="font-size:12px;color:#3b82f6;text-decoration:none;margin-left:8px;">⚙️ 管理持仓（增删/买卖）</a> <span class="hint">持仓清单在 positions.json 自动维护，价格与盈亏每日自动计算；运行 position_manager.py 后可在浏览器在线增删买卖</span></summary>
    <div style="margin-top:12px;">
      <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px;font-size:13px;color:#4a5568;">
        <span>证券市值 <b>{_fmt_price(pos.get('total_mv'))}</b></span>
        <span>持仓成本 <b>{_fmt_price(pos.get('total_cost'))}</b></span>
        <span>累计盈亏 <b class='{_pct_cls(pos.get('total_pnl'))}'>{_fmt_price(pos.get('total_pnl'))} ({_fmt_pct(pos.get('total_pct'))})</b></span>
        <span>当日盈亏 <b class='{_pct_cls(pos.get('total_day'))}'>{_fmt_price(pos.get('total_day'))}</b></span>
      </div>
      <table>
        <thead><tr><th>股票</th><th>持仓</th><th>现价</th><th>成本</th><th>市值</th><th>当日盈亏</th><th>持仓盈亏</th><th>说明</th></tr></thead>
        <tbody>{pos_rows}</tbody>
      </table>
      {tech_blocks}
      <div class="hint">注：持仓清单来自 positions.json（代码/成本价/数量），当日盈亏按昨收计算；如需增删持仓直接编辑该文件即可。每只股票下方附交互式技术分析图（道氏 / 江恩 / 布林带选项卡切换）。</div>
    </div>
  </details>'''
    else:
        pos_block = "" 

    # ---- 固收 ETF 表 ----
    fi_rows = ""
    for e in fi.get("etfs", []):
        fi_rows += (
            f"<tr><td><b>{e['name']}</b><div class='hint'>{e['kind']}</div></td>"
            f"<td>{_fmt_price(e.get('price'))}</td>"
            f"<td class='{_pct_cls(e.get('pct'))}'>{_fmt_pct(e.get('pct'))}</td>"
            f"<td style='text-align:left;color:#8a94a3;font-size:12px'>{e.get('note','')}</td></tr>"
        )
    # ---- 逆回购表 ----
    repo_rows = ""
    for r in fi.get("repos", []):
        repo_rows += (
            f"<tr><td><b>{r['name']}</b><div class='hint'>{r['kind']}</div></td>"
            f"<td>{_fmt_price(r.get('price'))}%</td>"
            f"<td class='{_pct_cls(r.get('pct'))}'>{_fmt_pct(r.get('pct'))}</td>"
            f"<td style='text-align:left;color:#8a94a3;font-size:12px'>{r.get('note','')}</td></tr>"
        )
    # ---- 高风险主动基金表 ----
    fund_rows = ""
    for f_item in hr.get("funds", []):
        fund_rows += (
            f"<tr><td><b>{f_item['name']}</b></td>"
            f"<td>{_fmt_price(f_item.get('nav'), 4)}</td>"
            f"<td class='{_pct_cls(f_item.get('pct'))}'>{_fmt_pct(f_item.get('pct'))}</td>"
            f"<td style='font-size:12px;color:#8a94a3'>{f_item.get('nav_date') or '-'}</td>"
            f"<td style='text-align:left;color:#8a94a3;font-size:12px'>{f_item.get('note','')}</td></tr>"
        )
    # ---- 纳指/标普 ETF 表 ----
    etf_rows = ""
    for e_item in hr.get("etfs", []):
        etf_rows += (
            f"<tr><td><b>{e_item['name']}</b></td>"
            f"<td>{_fmt_price(e_item.get('price'))}</td>"
            f"<td class='{_pct_cls(e_item.get('pct'))}'>{_fmt_pct(e_item.get('pct'))}</td>"
            f"<td style='text-align:left;color:#8a94a3;font-size:12px'>{e_item.get('note','')}</td></tr>"
        )

    fi_err = ""
    if fi.get("error"):
        fi_err = f'<div class="hint" style="color:#c0392b">固收数据获取异常：{fi["error"]}</div>'
    hr_err = ""
    if isinstance(hr, dict) and hr.get("error"):
        hr_err = f'<div class="hint" style="color:#c0392b">主动基金数据获取异常：{hr["error"]}</div>'

    if not (fi_rows or repo_rows or fund_rows or etf_rows or dividend_cards):
        return ""

    return f"""
  <!-- 第二部分：个人组合监控 -->
  <section class="card divider">
    <h2>第二部分 · 个人组合监控</h2>
    <div style="font-size:13px;color:#4a5568;line-height:2;">
      按家庭组合做每日盯盘，目标配置：<b>现金≈2% / 稳健固收48% / 红利·REITs 30% / 高风险主动基金20%</b>，<b>点击各类标题展开/收起明细</b>：
      <b>① 低风险固收（约48%）</b>：货币ETF / 国债ETF / 短债ETF / 国债逆回购；
      <b>② 红利低波（约30%）</b>：股息率详表；
      <b>③ 高风险主动基金 + 纳指ETF（约20%）</b>：国内主动基金 / 纳指 / 标普 ETF。
      行情仅供个人参考，不构成投资建议。
    </div>
  </section>
{pos_block}
  <details class="card">
    <summary style="font-size:15px;font-weight:700;color:#1f2d3d;cursor:pointer;user-select:none;">① 低风险固收区（约48%）· 货币ETF / 国债ETF / 短债ETF / 国债逆回购　<span class="hint">点击展开</span></summary>
    <div style="margin-top:12px;">
      {fi_err}
      <div style="font-size:14px;color:#5a6573;margin-bottom:6px;">固收 / 货币 ETF 实时行情</div>
      <table>
        <thead><tr><th>标的</th><th>现价 / 净值</th><th>涨跌幅</th><th>说明</th></tr></thead>
        <tbody>{fi_rows or '<tr><td colspan="4" class="hint">暂无数据</td></tr>'}</tbody>
      </table>
      <div style="font-size:14px;color:#5a6573;margin:14px 0 6px;">国债逆回购 · 年化利率</div>
      <table>
        <thead><tr><th>品种</th><th>年化利率</th><th>较前收</th><th>说明</th></tr></thead>
        <tbody>{repo_rows or '<tr><td colspan="4" class="hint">暂无数据</td></tr>'}</tbody>
      </table>
    </div>
  </details>

  <details class="card">
    <summary style="font-size:15px;font-weight:700;color:#1f2d3d;cursor:pointer;user-select:none;">② 红利 / 红利低波 ETF 追踪（约30%）　<span class="hint">点击展开</span></summary>
    <div style="margin-top:12px;">
      <div style="font-size:13px;color:#4a5568;line-height:2;">
        红利类生息资产的择时核心是<b>股息率</b>：股息率 = 每股分红 ÷ 价格，价格越低股息率越高、越有吸引力。
        以下把<b>当前股息率放在它自身历史序列中的分位</b>来判断贵贱（分位越高越便宜），
        并叠加 250 日年线、近 3 年价格分位、距 52 周高点回撤等信号，汇总给出买卖点参考。
        信号<b>仅供参考</b>，不构成投资建议。
      </div>
      {dividend_cards}
    </div>
  </details>

  <details class="card">
    <summary style="font-size:15px;font-weight:700;color:#1f2d3d;cursor:pointer;user-select:none;">③ 国内高风险主动基金 + 纳指ETF（约20%）　<span class="hint">点击展开</span></summary>
    <div style="margin-top:12px;">
      {hr_err}
      <div style="font-size:14px;color:#5a6573;margin-bottom:6px;">高风险主动基金 · 最新净值</div>
      <table>
        <thead><tr><th>基金</th><th>单位净值</th><th>日涨跌幅</th><th>净值日期</th><th>说明</th></tr></thead>
        <tbody>{fund_rows or '<tr><td colspan="5" class="hint">暂无数据</td></tr>'}</tbody>
      </table>
      <div style="font-size:14px;color:#5a6573;margin:14px 0 6px;">纳指 / 标普 ETF · 场内行情</div>
      <table>
        <thead><tr><th>标的</th><th>最新价</th><th>涨跌幅</th><th>说明</th></tr></thead>
        <tbody>{etf_rows or '<tr><td colspan="4" class="hint">暂无数据</td></tr>'}</tbody>
      </table>
      <div class="hint">注：主动基金为最新披露净值（QDII 类基金净值有延迟）；纳指/标普ETF为场内实时价，需留意溢价。</div>
    </div>
  </details>
"""


def generate_report(data):
    """生成独立 HTML 报告（调试用）。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    body = generate_report_section(data)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>个人组合监控</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:"Microsoft YaHei",Arial,sans-serif; background:#eef1f6; color:#2b3440; padding:24px 16px 40px; }}
.wrap {{ max-width:960px; margin:0 auto; }}
header.title {{ text-align:center; padding:26px 20px; background:linear-gradient(135deg,#1f2d3d,#2f4b7c); color:#fff; border-radius:14px; }}
header.title h1 {{ font-size:24px; }}
header.title .sub {{ margin-top:8px; font-size:13px; opacity:.85; }}
.card {{ background:#fff; border-radius:12px; padding:22px 24px; margin-top:20px; box-shadow:0 2px 8px rgba(31,45,61,.08); }}
.card h2 {{ font-size:17px; color:#1f2d3d; border-left:4px solid #2563eb; padding-left:10px; margin-bottom:16px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ padding:10px 12px; text-align:center; }}
thead th {{ background:#f1f4f9; color:#5a6573; font-weight:600; }}
tbody tr {{ border-bottom:1px solid #eef1f6; }}
.pos {{ color:#e64545; font-weight:600; }}
.neg {{ color:#12a15d; font-weight:600; }}
.flat {{ color:#9aa5b1; }}
.hint {{ font-size:12px; color:#8a94a3; }}
.divider {{ border-top:3px solid #2563eb; margin-top:32px; padding-top:4px; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="title">
    <h1>个人组合监控</h1>
    <div class="sub">数据时间：{now}　|　数据来源：腾讯行情 / 新浪</div>
  </header>
  {body}
</div>
</body>
</html>"""
    return html


def save_report(data, out_dir=None):
    out_dir = out_dir or OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    html = generate_report(data)
    path = os.path.join(out_dir, time.strftime("portfolio_report_%Y%m%d_%H%M.html"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


if __name__ == "__main__":
    res = collect_all()
    p = save_report(res)
    print("个人组合监控报告已生成:", p)
    fi = res.get("fixed_income", {})
    for e in fi.get("etfs", []):
        print(e["name"], "|", e.get("price"), "|", e.get("pct"))
    for r in fi.get("repos", []):
        print(r["name"], "年化", r.get("price"))
    hr = res.get("high_risk", {})
    for f_item in hr.get("funds", []):
        print(f_item["name"], "| 净值", f_item.get("nav"), "|", f_item.get("pct"), "|", f_item.get("nav_date"))
    for e_item in hr.get("etfs", []):
        print(e_item["name"], "|", e_item.get("price"), "|", e_item.get("pct"))