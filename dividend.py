# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 红利 / 红利低波 ETF 追踪分析模块

针对红利类生息资产的"股息率择时"逻辑设计：
  - 股息率 = 每股分红 / 价格，价格越低股息率越高，越有吸引力
  - 用"当前股息率在自身历史序列中的分位"判断高估/低估
  - 结合价格均线、价格历史分位、分红周期给出买卖点参考

覆盖标的（可在 ETF_CONFIG 中扩展）：
  510880 红利ETF华泰柏瑞     跟踪上证红利指数
  515450 红利低波50ETF南方   跟踪标普中国A股大盘红利低波50指数

数据源（全部走 sources.py 多源适配，异常自动切源）：
  - 实时行情：腾讯 qt.gtimg.cn（主） / 新浪 hq.sinajs.cn（兜底）
  - 历史K线 ：腾讯 web.ifzq.gtimg.cn（主） / 东财 push2his / 新浪
  - 分红记录：天天基金 fundf10
"""

import os
import time

import sources

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# ---------------------------------------------------------------- 标的配置
ETF_CONFIG = [
    {
        "code": "510880", "secid": "1.510880", "tx": "sh510880",
        "name": "红利ETF华泰柏瑞", "index": "上证红利指数",
        "desc": "A股首只红利主题ETF，跟踪上证红利指数，历史上每年约1月分红一次。",
    },
    {
        "code": "515450", "secid": "1.515450", "tx": "sh515450",
        "name": "红利低波50ETF南方", "index": "标普中国A股大盘红利低波50指数",
        "desc": "跟踪标普大盘红利低波50指数，历史上约每年7月、12月各分红一次。",
    },
]


# ---------------------------------------------------------------- 实时行情（腾讯主源 / 新浪兜底）

def get_realtime_quotes():
    """实时行情。返回 {code: {price, prev_close, pct, high, low, amount}}"""
    return sources.get_realtime_quotes([e["tx"] for e in ETF_CONFIG])


def get_kline(tx_code, secid):
    """
    获取日线K线（腾讯主源 / 东财 / 新浪兜底，异常自动切源）。
    返回 (tech_kline, yield_kline)：
      - tech：前复权价，用于均线/分位等技术指标
      - yield：不复权价，用于历史股息率（贴近除息日真实价格）
    """
    return sources.get_kline(tx_code, secid)


# ---------------------------------------------------------------- 分红记录（天天基金）

def get_dividends(code):
    """分红送配记录（天天基金 F10，多源适配层处理重试）。"""
    return sources.get_dividends(code)


# ---------------------------------------------------------------- 技术指标

def calc_ma(closes, n):
    """近 n 日均线。"""
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def calc_metrics(klines):
    """基于K线计算技术指标。"""
    if not klines:
        return {}
    closes = [k["close"] for k in klines]
    price = closes[-1]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    # 近250日(52周)高低
    w_high = max(highs[-250:]) if len(highs) >= 250 else max(highs)
    w_low = min(lows[-250:]) if len(lows) >= 250 else min(lows)
    # 近3年高低（全区间）
    his_high = max(highs)
    his_low = min(lows)
    # 价格分位：当前价在近3年价格区间的百分位
    if his_high > his_low:
        price_pct = (price - his_low) / (his_high - his_low) * 100
    else:
        price_pct = 50.0
    return {
        "price": price,
        "ma20": calc_ma(closes, 20),
        "ma60": calc_ma(closes, 60),
        "ma250": calc_ma(closes, 250),
        "week_high": w_high,
        "week_low": w_low,
        "hist_high": his_high,
        "hist_low": his_low,
        "price_pct": price_pct,          # 0-100
        "drawdown_from_high": (price / w_high - 1) * 100,   # 距52周高点回撤%
        "n": len(closes),
    }


# ---------------------------------------------------------------- 分析核心

def _price_on_date(klines, date_str):
    """返回指定日期(YYYY-MM-DD)收盘价；找不到返回None。"""
    for k in klines:
        if k["date"] == date_str:
            return k["close"]
    return None


def _percentile_rank(value, series):
    """value 在 series 中的百分位（0-100）。"""
    if not series:
        return None
    below = sum(1 for s in series if s <= value)
    return below / len(series) * 100


def analyze_etf(cfg):
    """单只 ETF 综合分析，返回分析结果字典。"""
    code = cfg["code"]
    quotes = get_realtime_quotes()
    tech_k, yld_k = get_kline(cfg["tx"], cfg["secid"])
    divs = get_dividends(code)

    q = quotes.get(code, {})
    m = calc_metrics(tech_k)

    # ---- 股息率分析 ----
    # 历史股息率序列：每次分红额 / 除息日不复权收盘价（贴近当时真实交易价）
    hist_yield = []
    for d in divs:
        ex_date = d["ex_date"].replace("/", "-") if d["ex_date"] else None
        px = _price_on_date(yld_k, ex_date) if ex_date else None
        if px:
            hist_yield.append(round(d["per_share"] / px * 100, 2))

    # 当前股息率：最近12个月累计分红 / 现价（实时价优先）
    current_price = q.get("price") or m.get("price")
    cur_yield = None
    if current_price and divs:
        today = time.strftime("%Y-%m-%d")
        cur_year = int(today[:4])
        last12 = [d for d in divs if d["pay_date"] and d["pay_date"][:7] >= f"{cur_year-1}-{today[5:7]}"]
        if last12:
            total_per_share = sum(d["per_share"] for d in last12)
            cur_yield = round(total_per_share / current_price * 100, 2)
        else:
            # 兜底：最近一次分红年化
            cur_yield = round(divs[0]["per_share"] / current_price * 100, 2)

    # 当前股息率分位（分红样本不足3次则不可用）
    yield_pct = None
    yield_reliable = len(hist_yield) >= 3
    if yield_reliable and cur_yield:
        yield_pct = _percentile_rank(cur_yield, hist_yield)

    # ---- 分红周期推断 ----
    month_cnt = {}
    for d in divs:
        if d["ex_date"]:
            mm = d["ex_date"][5:7]
            month_cnt[mm] = month_cnt.get(mm, 0) + 1
    expected_months = sorted(month_cnt, key=lambda m: month_cnt[m], reverse=True)[:2] if month_cnt else []
    # 距下次预计分红的自然日
    next_days = None
    if expected_months:
        today = time.localtime()
        for mm in expected_months:
            m_int = int(mm)
            y = today.tm_year
            if m_int < today.tm_mon or (m_int == today.tm_mon):
                y += 1
            try:
                target = time.mktime((y, m_int, 15, 0, 0, 0, 0, 0, 0))
            except Exception:
                continue
            days = int((target - time.time()) / 86400)
            if days >= 0:
                next_days = days
                break

    # ---- 买卖点信号 ----
    signals = []
    score = 0

    # 1) 股息率分位（核心）
    if yield_pct is not None:
        if yield_pct >= 70:
            signals.append("股息率处于历史高位区（分位 %.0f%%），低估，有吸引力" % yield_pct)
            score += 2
        elif yield_pct <= 30:
            signals.append("股息率处于历史低位区（分位 %.0f%%），估值偏贵" % yield_pct)
            score -= 2
        else:
            signals.append("股息率处于历史中位（分位 %.0f%%），估值中性" % yield_pct)
    elif cur_yield is not None:
        signals.append("历史现金分红样本较少，股息率分位信号不适用（当前股息率 %.2f%% 仅供参考）" % cur_yield)

    # 2) 价格 vs 年线
    if m.get("ma250"):
        if current_price and current_price > m["ma250"]:
            signals.append("现价站上 250 日年线，中期趋势偏多")
            score += 1
        else:
            signals.append("现价位于 250 日年线下方，中期趋势偏弱")
            score -= 1

    # 3) 价格历史分位
    pp = m.get("price_pct")
    if pp is not None:
        if pp <= 30:
            signals.append("价格处于近3年相对低位（分位 %.0f%%）" % pp)
            score += 1
        elif pp >= 70:
            signals.append("价格处于近3年相对高位（分位 %.0f%%）" % pp)
            score -= 1

    # 4) 距52周高点回撤
    dd = m.get("drawdown_from_high")
    if dd is not None and dd <= -15:
        signals.append("较52周高点回撤 %.1f%%，短期调整较充分" % dd)
        score += 1

    # 综合结论
    if score >= 3:
        conclusion, tone = "分批买入 / 加仓区", "up"
    elif score >= 1:
        conclusion, tone = "持有观察 / 逢低布局", "mid"
    elif score <= -2:
        conclusion, tone = "逢高减仓 / 回避区", "down"
    else:
        conclusion, tone = "中性观望", "mid"

    return {
        "cfg": cfg,
        "quote": q,
        "metrics": m,
        "dividends": divs,
        "cur_yield": cur_yield,
        "yield_pct": yield_pct,
        "hist_yield": hist_yield,
        "expected_months": expected_months,
        "next_days": next_days,
        "signals": signals,
        "score": score,
        "conclusion": conclusion,
        "tone": tone,
    }


def analyze_all():
    """分析全部标的。"""
    results = []
    for cfg in ETF_CONFIG:
        try:
            results.append(analyze_etf(cfg))
        except Exception as e:
            results.append({"cfg": cfg, "error": str(e), "conclusion": "分析失败", "tone": "mid"})
    return results


# ---------------------------------------------------------------- HTML 报告

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"Microsoft YaHei","PingFang SC",Arial,sans-serif; background:#eef1f6; color:#2b3440; padding:24px 16px 40px; }
.wrap { max-width:980px; margin:0 auto; }
header.title { text-align:center; padding:24px 20px; background:linear-gradient(135deg,#7a3c0a,#b8651a); color:#fff; border-radius:14px; box-shadow:0 6px 18px rgba(122,60,10,.25); }
header.title h1 { font-size:23px; letter-spacing:1px; }
header.title .sub { margin-top:8px; font-size:13px; opacity:.85; }
.card { background:#fff; border-radius:12px; padding:20px 22px; margin-top:18px; box-shadow:0 2px 8px rgba(31,45,61,.08); }
.card h2 { font-size:17px; color:#1f2d3d; border-left:4px solid #b8651a; padding-left:10px; margin-bottom:14px; }
table { width:100%; border-collapse:collapse; font-size:13px; margin-top:10px; }
th,td { padding:8px 10px; text-align:center; border-bottom:1px solid #eef1f6; }
thead th { background:#f7f0e8; color:#6b5a4a; font-weight:600; }
.pos { color:#e64545; font-weight:600; }
.neg { color:#12a15d; font-weight:600; }
.badge { display:inline-block; padding:6px 16px; border-radius:8px; color:#fff; font-weight:700; font-size:14px; }
.badge.up { background:linear-gradient(135deg,#e64545,#c0392b); }
.badge.down { background:linear-gradient(135deg,#2f9e77,#12a15d); }
.badge.mid { background:linear-gradient(135deg,#b8651a,#d98e32); }
.signal { margin:8px 0; padding:8px 12px; background:#f8fafc; border-left:3px solid #b8651a; border-radius:6px; font-size:13px; color:#4a5568; line-height:1.7; }
.statline { display:flex; gap:12px; flex-wrap:wrap; margin:10px 0; }
.stat { flex:1; min-width:110px; text-align:center; background:#f7f9fc; border-radius:10px; padding:12px 6px; }
.stat .n { font-size:22px; font-weight:700; }
.stat .t { font-size:11px; color:#8a94a3; margin-top:4px; }
.yieldbar { height:10px; background:#f1f4f9; border-radius:6px; margin:6px 0 2px; position:relative; }
.yieldbar .fill { height:100%; border-radius:6px; background:linear-gradient(90deg,#d98e32,#e64545); }
.yieldbar .mark { position:absolute; top:-3px; width:3px; height:16px; background:#1f2d3d; border-radius:2px; }
footer { text-align:center; color:#9aa5b1; font-size:12px; margin-top:26px; line-height:1.8; }
.hint { font-size:12px; color:#8a94a3; }
"""


def _fmt_pct(v, signed=True):
    if v is None:
        return "-"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def _card_html(r):
    """单只标的的分析卡片 HTML（供独立报告与综合报告片段共用）。"""
    cfg = r.get("cfg", {})
    if r.get("error"):
        return (f'<section class="card"><h2>{cfg.get("name","")}（{cfg.get("code","")}）</h2>'
                f'<p style="color:#c0392b">分析失败：{r["error"]}</p></section>')
    q = r.get("quote", {})
    m = r.get("metrics", {})
    price = q.get("price") or m.get("price")
    tone = r.get("tone", "mid")
    concl = r.get("conclusion", "-")

    # 股息率分位 → 简单买卖建议（仅按股息率分位单一因子）
    yp = r.get("yield_pct")
    yield_bar = ""
    if yp is not None:
        if yp >= 80:
            adv, adv_color, adv_note = "买入 / 加仓区", "#12a15d", "股息率处历史高位、相对便宜，适合分批买入"
        elif yp >= 60:
            adv, adv_color, adv_note = "逢低吸纳", "#5bb98c", "股息率偏高、估值偏低，可逢低分批布局"
        elif yp >= 40:
            adv, adv_color, adv_note = "持有吃息", "#d98e32", "股息率处历史中位、估值中性，拿住吃息即可"
        elif yp >= 20:
            adv, adv_color, adv_note = "停止买入", "#e8963a", "股息率偏低、估值偏贵，暂不加仓"
        else:
            adv, adv_color, adv_note = "逢高减仓", "#e64545", "股息率处历史低位、估值偏贵，可考虑高抛"
        yield_bar = (
            f'<div style="margin:12px 0 4px;">'
            f'<div style="font-size:13px;color:#4a5568;margin-bottom:6px;">股息率分位买卖建议（仅按分位判断，分位越高越便宜）</div>'
            f'<div style="display:flex;height:26px;border-radius:6px;overflow:hidden;position:relative;">'
            f'<div style="width:20%;background:#e64545;"></div>'
            f'<div style="width:20%;background:#e8963a;"></div>'
            f'<div style="width:20%;background:#f1c453;"></div>'
            f'<div style="width:20%;background:#7ec9a6;"></div>'
            f'<div style="width:20%;background:#12a15d;"></div>'
            f'<div style="position:absolute;left:{yp:.0f}%;top:-4px;width:3px;height:34px;'
            f'background:#1f2d3d;border-radius:2px;"></div>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#8a94a3;margin-top:2px;">'
            f'<span>减仓<br>分位&lt;20</span><span>停止买入<br>20~40</span><span>持有吃息<br>40~60</span>'
            f'<span>逢低吸纳<br>60~80</span><span>买入加仓<br>≥80</span></div>'
            f'<div style="margin-top:8px;font-size:13px;color:#4a5568;">'
            f'当前分位 <b>{yp:.0f}%</b> → 简单建议：<b style="color:{adv_color}">{adv}</b>　'
            f'<span class="hint">{adv_note}</span></div>'
            f'</div>'
        )

    # 信号列表
    sig_html = "".join(f'<div class="signal">{s}</div>' for s in r.get("signals", []))

    next_div = f"约 {r['next_days']} 天后" if r.get("next_days") is not None else "暂未推断"

    return f"""
<section class="card">
  <h2>{cfg.get('name','')}（{cfg.get('code','')}）<span class="hint">　跟踪：{cfg.get('index','')}</span></h2>
  <p class="hint" style="margin-bottom:10px">{cfg.get('desc','')}</p>

  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:6px;">
    <span class="badge {tone}">{concl}</span>
    <span style="font-size:22px;font-weight:700;color:#1f2d3d">{price if price is not None else '-'}</span>
    <span class="{'pos' if (q.get('pct') or 0)>=0 else 'neg'}">{_fmt_pct(q.get('pct'))}</span>
  </div>

  <div class="statline">
    <div class="stat"><div class="n" style="color:#b8651a">{_fmt_pct(r.get('cur_yield'), signed=False)}</div><div class="t">当前股息率</div></div>
    <div class="stat"><div class="n">{m.get('ma250','-') if m.get('ma250') else '-'}</div><div class="t">250日年线</div></div>
    <div class="stat"><div class="n">{f"{m.get('price_pct',0):.0f}%" if m.get('price_pct') is not None else '-'}</div><div class="t">近3年价格分位</div></div>
    <div class="stat"><div class="n">{f"{m.get('drawdown_from_high',0):.1f}%" if m.get('drawdown_from_high') is not None else '-'}</div><div class="t">距52周高点</div></div>
    <div class="stat"><div class="n">{next_div}</div><div class="t">距下次预计分红</div></div>
  </div>

  {yield_bar}
  {sig_html}
</section>"""


def generate_cards(results):
    """生成各标的分析卡片的 HTML 片段（不带标题/说明）。

    供个人组合监控（portfolio.generate_report_section）作为「红利/红利低波 ETF 追踪」
    子区块嵌入综合报告；也可用于调试。
    """
    return "".join(_card_html(r) for r in results)


def generate_report_section(results):
    """生成红利追踪分析的 HTML 内容片段（用于嵌入综合报告）。

    包含：第二部分的标题分隔、追踪逻辑说明、各标的卡片。
    由 main.py 传入 html_report.save_html_report(..., dividend_section=...) 合成一份综合报告。
    """
    cards = "".join(_card_html(r) for r in results)
    return f"""
  <!-- 第二部分：红利 / 红利低波 ETF 追踪分析 -->
  <section class="card divider">
    <h2>第二部分 · 红利 / 红利低波 ETF 追踪分析</h2>
    <div style="font-size:13px;color:#4a5568;line-height:2;">
      红利类生息资产的择时核心是<b>股息率</b>：股息率 = 每股分红 ÷ 价格，价格越低股息率越高、越有吸引力。
      本部分把<b>当前股息率放在它自身历史序列中的分位</b>来判断贵贱（分位越高越便宜），
      并叠加 250 日年线、近 3 年价格分位、距 52 周高点回撤等信号，汇总给出买卖点参考。
      信号<b>仅供参考</b>，不构成投资建议。
    </div>
  </section>
  {cards}"""


def generate_report(results):
    """生成红利追踪分析 HTML 报告（独立文件，调试/单独使用用）。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cards = "".join(_card_html(r) for r in results)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>红利 / 红利低波 ETF 追踪分析</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="title">
    <h1>红利 · 红利低波 ETF 追踪分析</h1>
    <div class="sub">数据时间：{now}　|　标的：510880 / 515450　|　数据来源：腾讯行情 / 东方财富 / 天天基金</div>
  </header>

  <section class="card">
    <h2>追踪逻辑说明</h2>
    <div style="font-size:13px;color:#4a5568;line-height:2;">
      红利类生息资产的择时核心是<b>股息率</b>：股息率 = 每股分红 ÷ 价格，价格越低股息率越高、越有吸引力。
      本报告把<b>当前股息率放在它自身历史序列中的分位</b>来判断贵贱（分位越高越便宜），
      并叠加 250 日年线、近 3 年价格分位、距 52 周高点回撤等信号，汇总给出买卖点参考。
      信号<b>仅供参考</b>，不构成投资建议。
    </div>
  </section>

  {cards}

  <footer>
    由程序自动抓取公开数据计算生成，仅供参考，不构成任何投资建议。<br>
    报告生成时间：{now}
  </footer>
</div>
</body>
</html>"""
    return html


def save_report(results, out_dir=None):
    out_dir = out_dir or OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    html = generate_report(results)
    path = os.path.join(out_dir, time.strftime("dividend_report_%Y%m%d_%H%M.html"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


if __name__ == "__main__":
    res = analyze_all()
    p = save_report(res)
    print("红利追踪报告已生成:", p)
    for r in res:
        if r.get("error"):
            print(r["cfg"]["code"], "失败:", r["error"])
        else:
            print(r["cfg"]["code"], r["cfg"]["name"], "| 股息率:", r.get("cur_yield"),
                  "| 分位:", r.get("yield_pct"), "| 结论:", r.get("conclusion"))
