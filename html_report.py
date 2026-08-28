# -*- coding: utf-8 -*-
"""
东方财富爬虫 - HTML 图文报告生成模块

输入：collector.collect_all() 返回的原始数据 dict
输出：自包含的 HTML 文件（内联 SVG + CSS 图表，离线可直接打开）

图表方案说明：为保证报告离线可看、不依赖网络 CDN，
全部图表使用内联 SVG / CSS 绘制（柱状图、环形图、条形图）。
"""

import time
import kchart

# ---------------------------------------------------------------- 工具函数

def _fmt_amount(v):
    """格式化成交额（元 -> 亿/万亿）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    if v >= 1e12:
        return f"{v / 1e12:.2f}万亿"
    if v >= 1e8:
        return f"{v / 1e8:.0f}亿"
    if v >= 1e4:
        return f"{v / 1e4:.0f}万"
    return f"{v:.0f}"


def _fmt_pct(v):
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "-"


def _sf(v):
    """安全转 float：兼容字符串/数字/None，失败返回 0.0"""
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def judge_market(breadth, indices):
    """基于市场广度与指数表现，规则化判断当日市场情绪。返回 (情绪标签, 说明)。"""
    up = breadth.get("up", 0)
    down = breadth.get("down", 0)
    total = breadth.get("total", 1) or 1
    up_ratio = up / total * 100

    sh = 0.0
    for idx in indices:
        if idx.get("name") == "上证指数":
            try:
                sh = float(idx.get("change_pct") or 0)
            except (TypeError, ValueError):
                sh = 0.0
            break

    if sh >= 1.5 and up_ratio >= 70:
        return "普涨强势", f"主要指数放量上行，超{up_ratio:.0f}%个股上涨，做多情绪旺盛。"
    if sh <= -1.5 and up_ratio <= 30:
        return "普跌弱势", f"指数明显下挫，仅{up_ratio:.0f}%个股上涨，市场情绪偏冷。"
    if up_ratio >= 60:
        return "涨多跌少", f"指数{sh:+.2f}%，上涨个股占比{up_ratio:.0f}%，赚钱效应尚可。"
    if up_ratio <= 40:
        return "跌多涨少", f"指数{sh:+.2f}%，上涨个股占比仅{up_ratio:.0f}%，分化明显。"
    return "震荡分化", f"指数{sh:+.2f}%，涨跌家数接近，市场呈结构性行情。"

# ---------------------------------------------------------------- 配色
RED = "#e64545"      # 涨 / 红
GREEN = "#12a15d"    # 跌 / 绿
GRAY = "#9aa5b1"     # 平 / 灰
BLUE = "#2563eb"     # 强调蓝
DARK = "#1f2d3d"     # 深色文字
CARD = "#ffffff"

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"Microsoft YaHei","PingFang SC","Helvetica Neue",Arial,sans-serif;
       background:#eef1f6; color:#2b3440; padding:24px 16px 40px; }
.wrap { max-width:960px; margin:0 auto; }
header.title { text-align:center; padding:26px 20px; background:linear-gradient(135deg,#1f2d3d,#2f4b7c);
               color:#fff; border-radius:14px; box-shadow:0 6px 18px rgba(31,45,61,.18); }
header.title h1 { font-size:24px; letter-spacing:1px; }
header.title .sub { margin-top:8px; font-size:13px; opacity:.85; }
.card { background:CARD; border-radius:12px; padding:22px 24px; margin-top:20px;
        box-shadow:0 2px 8px rgba(31,45,61,.08); }
.card h2 { font-size:17px; color:DARK; border-left:4px solid BLUE; padding-left:10px; margin-bottom:16px; }
.hint { font-size:12px; color:#8a94a3; margin-top:4px; }

/* 一句话点评 */
.verdict { display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
.verdict .badge { font-size:20px; font-weight:700; padding:10px 20px; border-radius:10px; color:#fff; }
.verdict .desc { font-size:15px; color:#4a5568; line-height:1.7; flex:1; min-width:220px; }
.badge.up   { background:linear-gradient(135deg,#f0564a,#e64545); }
.badge.down { background:linear-gradient(135deg,#2f9e77,#12a15d); }
.badge.mid  { background:linear-gradient(135deg,#4a6fa5,#2f4b7c); }

/* 指数表格 */
table { width:100%; border-collapse:collapse; font-size:14px; }
th,td { padding:10px 12px; text-align:center; }
thead th { background:#f1f4f9; color:#5a6573; font-weight:600; }
tbody tr { border-bottom:1px solid #eef1f6; }
tbody tr:hover { background:#f8fafc; }
.pos { color:RED; font-weight:600; }
.neg { color:GREEN; font-weight:600; }
.flat{ color:GRAY; }

/* 横向条形图 */
.hbar { display:flex; align-items:center; margin:9px 0; }
.hbar .lbl { width:150px; font-size:13px; color:#4a5568; text-align:right; padding-right:12px; white-space:nowrap; }
.hbar .track { flex:1; background:#f1f4f9; border-radius:6px; height:22px; position:relative; overflow:hidden; }
.hbar .fill { height:100%; border-radius:6px; min-width:2px; }
.hbar .val { width:88px; text-align:left; padding-left:10px; font-size:13px; font-weight:600; }

/* 双栏 */
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
@media (max-width:760px){ .grid2{grid-template-columns:1fr;} .hbar .lbl{width:110px;} }

/* 统计卡 */
.statbox { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:10px; }
.stat { flex:1; min-width:120px; text-align:center; background:#f7f9fc; border-radius:10px; padding:14px 8px; }
.stat .n { font-size:26px; font-weight:700; }
.stat .t { font-size:12px; color:#8a94a3; margin-top:4px; }

/* 通用徽标（红利结论等复用） */
.badge { display:inline-block; padding:6px 16px; border-radius:8px; color:#fff; font-weight:700; font-size:14px; }

/* 红利追踪部分组件 */
.statline { display:flex; gap:12px; flex-wrap:wrap; margin:10px 0; }
.signal { margin:8px 0; padding:8px 12px; background:#f8fafc; border-left:3px solid #2563eb; border-radius:6px; font-size:13px; color:#4a5568; line-height:1.7; }
.yieldbar { height:10px; background:#f1f4f9; border-radius:6px; margin:6px 0 2px; position:relative; }
.yieldbar .fill { height:100%; border-radius:6px; background:linear-gradient(90deg,#d98e32,#e64545); }
.yieldbar .mark { position:absolute; top:-3px; width:3px; height:16px; background:#1f2d3d; border-radius:2px; }
.divider { border-top:3px solid #2563eb; margin-top:32px; padding-top:4px; }

footer { text-align:center; color:#9aa5b1; font-size:12px; margin-top:26px; line-height:1.8; }

/* 可折叠按钮（市场广度 / 板块 / 个股 Top10） */
details.fold > summary {
  list-style:none; cursor:pointer; user-select:none;
  display:flex; align-items:center; justify-content:space-between;
  background:linear-gradient(135deg,#f1f4f9,#e8edf5);
  border:1px solid #d5dde8; border-radius:10px;
  padding:12px 16px; font-size:15px; font-weight:700; color:#1f2d3d;
}
details.fold > summary::-webkit-details-marker { display:none; }
details.fold > summary:hover { background:linear-gradient(135deg,#e8edf5,#dfe7f2); border-color:#2563eb; }
details.fold > summary .btn-tag {
  font-size:12px; color:#2563eb; background:#fff;
  border:1px solid #b9cdf4; border-radius:7px; padding:5px 14px; font-weight:600;
  box-shadow:0 1px 2px rgba(37,99,235,.15); white-space:nowrap;
}
details.fold > summary .btn-tag::before { content:"▾ 点击展开"; }
details.fold[open] > summary .btn-tag::before { content:"▴ 点击收起"; }
details.fold[open] { border-color:#2563eb; }
"""


# ---------------------------------------------------------------- SVG 图表

def _bar_chart_svg(items, width=920, height=230, bar_width=54, pad=16):
    """
    竖向柱状图：红涨绿跌，0 基线。
    items: [(label, value)]
    """
    if not items:
        return ""
    labels = [i[0] for i in items]
    values = [float(i[1]) for i in items]
    max_abs = max(max(values), -min(values), 0.1)

    plot_w = width - 80                     # 左侧留刻度
    n = len(items)
    step = plot_w / n
    base_y = height - 44                    # 0 基线 Y
    scale = (height - 70) / max_abs         # 像素/单位

    parts = []
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block">'
    )
    # 网格线 + 0 基线
    parts.append(f'<line x1="60" y1="{base_y}" x2="{width-10}" y2="{base_y}" '
                 f'stroke="#c3ccd8" stroke-width="1.5"/>')
    parts.append(f'<text x="52" y="{base_y+4}" text-anchor="end" font-size="11" fill="#8a94a3">0</text>')
    top_v = max_abs
    parts.append(f'<text x="52" y="{base_y - scale*top_v + 4}" text-anchor="end" font-size="11" '
                 f'fill="#8a94a3">{top_v:+.1f}%</text>')

    for i, (label, value) in enumerate(zip(labels, values)):
        cx = 60 + step * i + step / 2
        h = abs(value) * scale
        color = RED if value > 0 else (GREEN if value < 0 else GRAY)
        if value > 0:
            y = base_y - h
        elif value < 0:
            y = base_y
        else:
            y = base_y - 1
        h = max(h, 2)
        parts.append(
            f'<rect x="{cx - bar_width/2:.1f}" y="{y:.1f}" width="{bar_width}" '
            f'height="{h:.1f}" rx="4" fill="{color}" opacity="0.9"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{base_y + 20}" text-anchor="middle" font-size="12" '
            f'fill="#4a5568">{label}</text>'
        )
        # 数值标签
        if value > 0:
            ty = y - 7
        else:
            ty = base_y + 18
        parts.append(
            f'<text x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="11" '
            f'font-weight="600" fill="{color}">{value:+.2f}%</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _donut_svg(segments, size=220, thickness=34):
    """
    环形图（涨/跌/平占比）。
    segments: [(label, value, color)]
    """
    total = sum(s[1] for s in segments) or 1
    r = (size - thickness) / 2 - 6
    c = 2 * 3.1415926 * r
    cx = cy = size / 2
    parts = [f'<svg viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:{size}px;height:{size}px">']
    acc = 0.0
    for label, value, color in segments:
        frac = value / total
        dash = frac * c
        gap = c - dash
        # 从 12 点方向开始（-90°）
        offset = -acc * c
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{thickness}" stroke-dasharray="{dash:.2f} {gap:.2f}" '
            f'stroke-dashoffset="{offset:.2f}" stroke-linecap="butt"/>'
        )
        acc += frac
    # 中心文字
    pct = segments[0][1] / total * 100
    parts.append(
        f'<text x="{cx}" y="{cy-6}" text-anchor="middle" font-size="30" font-weight="700" '
        f'fill="{DARK}">{pct:.0f}%</text>'
    )
    parts.append(
        f'<text x="{cx}" y="{cy+18}" text-anchor="middle" font-size="12" fill="#8a94a3">'
        f'上涨占比</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _hbar_html(rows, max_pct=None):
    """CSS 横向条形图（板块/概念涨幅榜）。"""
    if not rows:
        return '<div class="hint">暂无数据</div>'
    vals = [float(r.get("change_pct") or 0) for r in rows]
    max_v = max_pct or (max(vals, default=1) or 1)
    out = []
    for r in rows:
        v = float(r.get("change_pct") or 0)
        w = max(3, v / max_v * 100)
        color = RED if v >= 0 else GREEN
        amt = _fmt_amount(r.get("amount")) if r.get("amount") is not None else ""
        name = r.get("name", "-")
        out.append(
            f'<div class="hbar"><div class="lbl">{name}</div>'
            f'<div class="track"><div class="fill" style="width:{w:.1f}%;background:{color}"></div></div>'
            f'<div class="val { "pos" if v>=0 else "neg" }">{_fmt_pct(v)}</div>'
            f'</div>'
        )
    return "".join(out)


def _legend(items):
    """图例。items: [(label, color)]"""
    out = ['<div style="display:flex;gap:16px;justify-content:center;margin-top:10px;flex-wrap:wrap">']
    for label, color in items:
        out.append(
            f'<span style="font-size:12px;color:#5a6573;">'
            f'<i style="display:inline-block;width:10px;height:10px;border-radius:2px;'
            f'background:{color};margin-right:5px;"></i>{label}</span>'
        )
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------- 主体

def generate_html_report(data, dividend_section="", airman_section="", portfolio_section=""):
    """生成完整的 HTML 报告字符串。

    dividend_section: 红利追踪分析的 HTML 内容片段（dividend.generate_report_section）。
    airman_section:   罗力豪空中飞人指数的 HTML 内容片段（airman.generate_report_section）。
    传入后在对应位置嵌入，合成一份综合报告；不传则省略对应部分。
    """
    now = data.get("time", time.strftime("%Y-%m-%d %H:%M:%S"))
    indices = data.get("indices", [])
    breadth = data.get("breadth", {})
    zt = data.get("limit_up", {})
    dt = data.get("limit_down", {})
    ind_up = data.get("industry_up", [])[:8]
    con_up = data.get("concept_up", [])[:8]
    st_up = data.get("stock_up", [])[:10]

    label, desc = judge_market(breadth, indices)
    badge_cls = "up" if label in ("普涨强势", "涨多跌少") else ("down" if label in ("普跌弱势", "跌多涨少") else "mid")

    # ---- 指数表格 ----
    KEEP_IDX = ("上证指数", "深证成指")
    idx_rows = []
    for idx in indices:
        if idx.get("name") not in KEEP_IDX:
            continue
        _cp = _sf(idx.get("change_pct")); cls = "pos" if _cp > 0 else ("neg" if _cp < 0 else "flat")
        idx_rows.append(
            f"<tr><td><b>{idx.get('name','-')}</b></td>"
            f"<td>{idx.get('price','-')}</td>"
            f"<td class='{cls}'>{_fmt_pct(idx.get('change_pct'))}</td>"
            f"<td>{_fmt_amount(idx.get('amount'))}</td></tr>"
        )

    # ---- 市场广度 ----
    total = breadth.get("total", 0) or 1
    up_n, down_n, flat_n = breadth.get("up", 0), breadth.get("down", 0), breadth.get("flat", 0)
    donut = _donut_svg([("上涨", up_n, RED), ("下跌", down_n, GREEN), ("平盘", flat_n, GRAY)])
    legend = _legend([("上涨", RED), ("下跌", GREEN), ("平盘", GRAY)])

    zt_items = zt.get("items", [])
    high_lbc = ""
    if zt_items:
        max_lbc = max((x.get("lbc") or 1) for x in zt_items)
        high_lbc = "、".join(x["name"] for x in zt_items if (x.get("lbc") or 1) >= max(3, max_lbc - 1))
        high_lbc = f'<div class="hint" style="margin-top:8px">高位连板：{high_lbc}（最高 {max_lbc} 连板）</div>'

    # ---- 个股涨幅榜 ----
    st_rows = []
    for i, s in enumerate(st_up, 1):
        cls = "pos" if _sf(s.get("change_pct")) >= 0 else "neg"
        st_rows.append(
            f"<tr><td>{i}</td><td>{s.get('name','-')}</td>"
            f"<td>{s.get('code','-')}</td>"
            f"<td>{s.get('price','-')}</td>"
            f"<td class='{cls}'>{_fmt_pct(s.get('change_pct'))}</td>"
            f"<td>{_fmt_amount(s.get('amount'))}</td></tr>"
        )

    # ---- 主板（上证指数）分时 + 日K图 ----
    try:
        kchart_section = kchart.generate_index_chart_section()
    except Exception:
        kchart_section = ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>大盘与泡沫报告</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

  <header class="title">
    <h1>大盘与泡沫报告</h1>
    <div class="sub">数据采集时间：{now}　|　数据来源：东方财富 / FRED / 腾讯 / 新浪</div>
  </header>

  <!-- 一句话点评 -->
  <section class="card">
    <h2>一句话点评</h2>
    <div class="verdict">
      <div class="badge {badge_cls}">{label}</div>
      <div class="desc">{desc}</div>
    </div>
  </section>

  <!-- 主要指数 -->
  <section class="card">
    <h2>主要指数 · 主板行情</h2>
    <table>
      <thead><tr><th>指数</th><th>收盘点位</th><th>涨跌幅</th><th>成交额</th></tr></thead>
      <tbody>{''.join(idx_rows)}</tbody>
    </table>
    {kchart_section}
  </section>

  <!-- 市场广度 -->
  <details class="fold card">
    <summary><span>市场广度与赚钱效应</span><span class="btn-tag"></span></summary>
    <div style="margin-top:14px;">
      <div style="display:flex;gap:26px;align-items:center;flex-wrap:wrap;">
        <div>{donut}</div>
        <div style="flex:1;min-width:260px;">
          <div class="statbox">
            <div class="stat"><div class="n" style="color:{RED}">{up_n}</div><div class="t">上涨家数</div></div>
            <div class="stat"><div class="n" style="color:{GREEN}">{down_n}</div><div class="t">下跌家数</div></div>
            <div class="stat"><div class="n" style="color:{GRAY}">{flat_n}</div><div class="t">平盘家数</div></div>
          </div>
          <div style="font-size:14px;color:#4a5568;line-height:2;">
            全市场 A 股共 <b>{total}</b> 只：上涨 <b style="color:{RED}">{up_n}</b> 只，下跌
            <b style="color:{GREEN}">{down_n}</b> 只，平盘 <b>{flat_n}</b> 只<br>
            涨停 <b style="color:{RED}">{zt.get('count','-')}</b> 家（含 ST/连板），跌停
            <b style="color:{GREEN}">{dt.get('count','-')}</b> 家
            {high_lbc}
          </div>
        </div>
      </div>
      {legend}
    </div>
  </details>

  <!-- 板块表现 -->
  <details class="fold card">
    <summary><span>板块表现</span><span class="btn-tag"></span></summary>
    <div style="margin-top:14px;">
      <div class="grid2">
        <div>
          <h3 style="font-size:14px;color:#5a6573;margin-bottom:8px;">行业板块涨幅居前</h3>
          {_hbar_html(ind_up)}
        </div>
        <div>
          <h3 style="font-size:14px;color:#5a6573;margin-bottom:8px;">概念板块涨幅居前</h3>
          {_hbar_html(con_up)}
        </div>
      </div>
    </div>
  </details>

  <!-- 个股表现 -->
  <details class="fold card">
    <summary><span>个股表现 · 涨幅榜 Top10</span><span class="btn-tag"></span></summary>
    <div style="margin-top:14px;">
      <table>
        <thead><tr><th>#</th><th>名称</th><th>代码</th><th>最新价</th><th>涨跌幅</th><th>成交额</th></tr></thead>
        <tbody>{''.join(st_rows)}</tbody>
      </table>
    </div>
  </details>

  {dividend_section}

  {portfolio_section}

  {airman_section}

  <footer>
    以上内容由程序自动抓取东方财富公开数据并归纳生成，仅供参考，不构成任何投资建议。<br>
    报告生成时间：{now}
  </footer>

</div>
</body>
</html>"""
    return html


def save_html_report(data, out_dir, dividend_section="", airman_section="", portfolio_section=""):
    """生成并保存综合 HTML 报告文件（大盘 + 可选红利追踪 + 可选空中飞人指数），返回文件路径。"""
    import os
    os.makedirs(out_dir, exist_ok=True)
    html = generate_html_report(data, dividend_section, airman_section, portfolio_section)
    path = os.path.join(out_dir, time.strftime("daily_report_%Y%m%d_%H%M.html"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


if __name__ == "__main__":
    import json
    with open("output/latest_market.json", encoding="utf-8") as f:
        d = json.load(f)
    p = save_html_report(d, "output")
    print("已生成:", p)
