# -*- coding: utf-8 -*-
"""
个股技术分析模块 · 交互式 ECharts K线图
========================================
第一层：K线自动作图 —— 均线 / 趋势线 / 支撑阻力 / 布林带 自动叠加
第二层：选项卡切换多套技术分析理论（道氏 / 江恩 / 布林带），结果叠加到同一张图上

- 数据：腾讯前复权日K（约 2 年 / 520 交易日）
- 输出：ECharts 交互式 HTML 片段（echarts.min.js 已内嵌，报告离线可看）
- 道氏理论：MA20/60/250 三级别趋势 + 枢轴趋势线 + 支撑阻力位 + 量能确认
- 江恩理论：江恩角度线（1x1 / 1x2 / 2x1 / 1x4 / 4x1）+ 回调位（38.2% / 50% / 61.8%）
- 布林带：MA20 ± 2σ

免责声明：技术分析为历史统计与图形工具，信号仅供参考，不构成投资建议。
"""
import json
import os
import time

import numpy as np
import pandas as pd

import kchart
import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ECHARTS_PATH = os.path.join(BASE_DIR, "assets", "echarts.min.js")

# A股配色（红涨绿跌）
RED = "#e64545"
GREEN = "#12a15d"
GRAY = "#9aa5b1"
BLUE = "#2563eb"
DARK = "#1f2d3d"
MA_COLORS = {"MA20": "#f0a500", "MA60": "#2563eb", "MA250": "#7c3aed"}
GANN_COLORS = {"1x1": "#8b5cf6", "1x2": "#0ea5e9", "2x1": "#d946ef",
               "1x4": "#f59e0b", "4x1": "#ef4444"}
FIB_COLORS = {"0.382": "#e91e63", "0.5": "#12a15d", "0.618": "#e64545"}


# ================================================================ 数据与指标

def calc_indicators(df):
    """计算 MA20/60/250 + BOLL(20,2)。返回 df 副本。"""
    out = df.copy()
    c = out["close"]
    for n in (20, 60, 250):
        out[f"ma{n}"] = c.rolling(n).mean()
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    out["boll_mid"] = mid
    out["boll_up"] = mid + 2 * std
    out["boll_low"] = mid - 2 * std
    return out


def fetch_kline(tx_code, n=520):
    """腾讯前复权日K。返回 DataFrame(date/open/close/high/low/volume)。"""
    return kchart.fetch_kline(tx_code, n)


# ================================================================ 结构识别

def _detect_pivots(df, order=3):
    """摆动点（fractal）检测：局部高低点。返回 (枢轴高点索引, 枢轴低点索引)。"""
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    piv_high, piv_low = [], []
    for i in range(order, n - order):
        if all(high[i] >= high[i - j] and high[i] >= high[i + j] for j in range(1, order + 1)):
            piv_high.append(i)
        if all(low[i] <= low[i - j] and low[i] <= low[i + j] for j in range(1, order + 1)):
            piv_low.append(i)
    return piv_high, piv_low


def _dow_trendlines(df, piv_high, piv_low, lookback=80):
    """道氏趋势线：最近一段抬高的枢轴低点连成上升趋势线、降低的枢轴高点连成下降趋势线。
    返回 [(start_idx, start_price, end_idx, end_price, kind)]，趋势线延长到当前K线。"""
    n = len(df)
    last = n - 1
    lines = []
    lows = [(i, float(df["low"].iloc[i])) for i in piv_low if i >= n - lookback]
    highs = [(i, float(df["high"].iloc[i])) for i in piv_high if i >= n - lookback]
    # 上升趋势线：低点抬高
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if b[1] > a[1] and b[0] > a[0]:
            k = (b[1] - a[1]) / (b[0] - a[0])
            y_end = b[1] + k * (last - b[0])
            lines.append((a[0], a[1], last, y_end, "up"))
    # 下降趋势线：高点降低
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if b[1] < a[1] and b[0] > a[0]:
            k = (b[1] - a[1]) / (b[0] - a[0])
            y_end = b[1] + k * (last - b[0])
            lines.append((a[0], a[1], last, y_end, "down"))
    return lines


def _support_resistance(df, piv_high, piv_low, lookback=120):
    """支撑阻力位：近期枢轴高低点 + 整数关口。返回 (support_list, resistance_list)。"""
    recent = df.tail(lookback)
    h = float(recent["high"].max())
    l = float(recent["low"].min())
    supports = []
    resistances = []
    # 枢轴低点 / 高点（去掉离得太近的重复）
    for i in piv_low:
        p = float(df["low"].iloc[i])
        if p >= l * 0.995 and all(abs(p - s) / p > 0.01 for s in supports):
            supports.append(p)
    for i in piv_high:
        p = float(df["high"].iloc[i])
        if p <= h * 1.005 and all(abs(p - s) / p > 0.01 for s in resistances):
            resistances.append(p)
    # 整数关口
    for ref in (l, h):
        if ref > 1:
            if int(ref) > 1 and all(abs(int(ref) - s) / int(ref) > 0.005 for s in supports):
                supports.append(float(int(ref)))
    # 各取最接近当前价的 3 个
    cur = float(df["close"].iloc[-1])
    supports = sorted(supports, key=lambda p: abs(p - cur) if p <= cur else 1e9)[:3]
    resistances = sorted(resistances, key=lambda p: abs(p - cur) if p >= cur else 1e9)[:3]
    return supports, resistances


def _gann_angles(df, piv_low, lookback=120):
    """江恩角度线：从最近显著枢轴低点发出。unit=(近高-枢轴低)/8。
    返回 [(start_idx, start_price, end_idx, end_price, name)]。"""
    if not piv_low:
        return []
    start = piv_low[-1]
    s_price = float(df["low"].iloc[start])
    recent = df.tail(lookback)
    h = float(recent["high"].max())
    unit = (h - s_price) / 8.0
    if unit <= 0:
        unit = s_price * 0.01
    last = len(df) - 1
    ratios = {"1x1": 1.0, "1x2": 0.5, "2x1": 2.0, "1x4": 0.25, "4x1": 4.0}
    out = []
    for name, ratio in ratios.items():
        y_end = s_price + (last - start) * unit * ratio
        out.append((start, s_price, last, y_end, name))
    return out


def _fib_levels(df, lookback=120):
    """回调位：近期高-低区间的 38.2% / 50% / 61.8%。返回 (high, low, [(pct, price)])。"""
    recent = df.tail(lookback)
    h = float(recent["high"].max())
    l = float(recent["low"].min())
    span = h - l
    levels = [(0.382, h - span * 0.382), (0.5, h - span * 0.5), (0.618, h - span * 0.618)]
    return h, l, levels


# ================================================================ 结论文本

def _dow_summary(df, trendlines, supports, resistances):
    cur = float(df["close"].iloc[-1])
    parts = []
    # 三级别趋势
    for ma_n, label in ((250, "主要趋势"), (60, "次要趋势"), (20, "短期趋势")):
        v = df[f"ma{ma_n}"].iloc[-1]
        if not np.isfinite(v):
            continue
        state = "多头" if cur >= v else "空头"
        parts.append(f"{label}（MA{ma_n}）：{state}")
    # 趋势线位置
    for a, pa, b, pb, kind in trendlines:
        if kind == "up":
            y_at = pa if b == a else pb
            parts.append("上升趋势线：价格在其上方（趋势延续）" if cur >= y_at * 0.998 else "上升趋势线：价格跌破（趋势转弱）")
        else:
            y_at = pb
            parts.append("下降趋势线：价格在其下方（弱势）" if cur <= y_at * 1.002 else "下降趋势线：价格突破（反转信号）")
    # 支撑阻力距离
    if supports:
        s = min(supports)
        parts.append(f"近端支撑：{s:.2f}（距当前 {abs(cur - s) / cur * 100:.1f}%）")
    if resistances:
        r = min(resistances)
        parts.append(f"近端阻力：{r:.2f}（距当前 {abs(cur - r) / cur * 100:.1f}%）")
    return "；".join(parts)


def _gann_summary(df, angles, levels):
    cur = float(df["close"].iloc[-1])
    parts = []
    # 角度线位置
    if angles:
        a, pa, b, pb, name = angles[0]
        parts.append(f"1x1 角度线当前位 {pb:.2f}（价格{'上方' if cur >= pb else '下方'}）")
    # 回调位
    for pct, price in levels:
        rel = "上方" if cur >= price else "下方"
        parts.append(f"{int(pct * 100)}% 回调位 {price:.2f}（当前在{rel}）")
    return "；".join(parts)


def _boll_summary(df):
    cur = float(df["close"].iloc[-1])
    up = float(df["boll_up"].iloc[-1])
    low = float(df["boll_low"].iloc[-1])
    mid = float(df["boll_mid"].iloc[-1])
    if cur > up:
        pos = "突破上轨（超买区）"
    elif cur < low:
        pos = "跌破下轨（超卖区）"
    else:
        pos = "轨道内运行"
    return f"BOLL(20,2) 上轨 {up:.2f} / 中轨 {mid:.2f} / 下轨 {low:.2f}；当前{pos}"


# ================================================================ ECharts 组装

def _line_series(name, points, color, dash=False, width=1.4, z=6, label=None):
    """两点连线标注系列。points: [[x_index, y], ...]（x 为可见区索引）。"""
    s = {
        "type": "line", "name": name,
        "xAxisIndex": 0, "yAxisIndex": 0,
        "data": points, "showSymbol": False,
        "lineStyle": {"width": width, "color": color, "type": "dashed" if dash else "solid"},
        "z": z,
    }
    if label is not None:
        s["label"] = {"show": True, "position": "end", "fontSize": 10,
                      "color": color, "formatter": str(label)}
    return s


def _seg_series(name, p0, p1, n, color, dash=False, width=1.4, z=6, label=None):
    """两点线段采样成多点 series。p0/p1 为 (x_index, y)。
    采样成 ~28 段，确保 dataZoom 任意缩放视野内都有数据点（两点线在视野外会被裁剪不渲染）。"""
    x0, y0 = p0
    x1, y1 = p1
    if x1 <= x0:
        pts = [[x0, y0], [x1, y1]]
    else:
        steps = 28
        pts = [[round(x0 + (x1 - x0) * k / steps, 3),
                round(y0 + (y1 - y0) * k / steps, 3)] for k in range(steps + 1)]
    return _line_series(name, pts, color, dash=dash, width=width, z=z, label=label)


def _hline_series(name, price, n, color, dash=True, width=1.4, z=6, label=None):
    """水平线：从可见区起点到终点，采样多点，带价格标签。"""
    return _seg_series(name, (0, price), (n - 1, price), n, color, dash=dash, width=width, z=z,
                       label=(label if label is not None else price))


def build_option(dates, kdata, volumes, ma, dow, gann, boll, show_n=170):
    """组装 ECharts option。返回 (option_dict, legend_groups)。"""
    # 可见范围（后 show_n 根）
    start = max(0, len(dates) - show_n)
    dates_v = dates[start:]
    kdata_v = kdata[start:]
    volumes_v = volumes[start:]

    series = []
    # 基底：K线 + 成交量 + MA20
    series.append({
        "type": "candlestick", "name": "K线", "xAxisIndex": 0, "yAxisIndex": 0,
        "data": kdata_v,
        "itemStyle": {"color": RED, "color0": GREEN, "borderColor": RED, "borderColor0": GREEN},
        "z": 3,
    })
    series.append({
        "type": "bar", "name": "成交量", "xAxisIndex": 1, "yAxisIndex": 1,
        "data": volumes_v, "barWidth": "60%",
        "itemStyle": {"color": "#b9c2cc"}, "z": 2,
    })
    ma20 = [None if not np.isfinite(v) else round(float(v), 3) for v in ma["ma20"][start:]]
    series.append({
        "type": "line", "name": "MA20", "xAxisIndex": 0, "yAxisIndex": 0,
        "data": ma20, "showSymbol": False,
        "lineStyle": {"width": 1.2, "color": MA_COLORS["MA20"]}, "z": 4,
    })

    # 道氏组
    dow_names = ["MA60", "MA250", "道氏·上升趋势线", "道氏·下降趋势线", "道氏·支撑", "道氏·阻力"]
    for n_ in (60, 250):
        arr = [None if not np.isfinite(v) else round(float(v), 3) for v in ma[f"ma{n_}"][start:]]
        series.append({
            "type": "line", "name": f"MA{n_}", "xAxisIndex": 0, "yAxisIndex": 0,
            "data": arr, "showSymbol": False,
            "lineStyle": {"width": 1.2, "color": MA_COLORS[f"MA{n_}"]}, "z": 4,
        })
    n_v = len(dates_v)  # 可见区长度，用于水平线/角度线索引坐标
    for a, pa, b, pb, kind in dow["trendlines"]:
        name = "道氏·上升趋势线" if kind == "up" else "道氏·下降趋势线"
        color = "#2563eb" if kind == "up" else "#7c3aed"
        series.append(_seg_series(name, (max(0, a - start), pa), (max(0, b - start), pb), n_v,
                                  color, dash=True, width=1.6))
    for s_ in dow["supports"]:
        series.append(_hline_series("道氏·支撑", s_, n_v, GREEN, dash=True))
    for r_ in dow["resistances"]:
        series.append(_hline_series("道氏·阻力", r_, n_v, RED, dash=True))

    # 江恩组
    gann_names = []
    for a, pa, b, pb, name in gann["angles"]:
        gann_names.append(f"江恩·角度{name}")
        series.append(_seg_series(f"江恩·角度{name}",
                                  (max(0, a - start), pa), (max(0, b - start), pb), n_v,
                                  GANN_COLORS[name], dash=False, width=1.3, z=5))
    for pct, price in gann["fib"]:
        gann_names.append(f"江恩·回调{int(pct * 100)}%")
        series.append(_hline_series(f"江恩·回调{int(pct * 100)}%", price, n_v,
                                    FIB_COLORS[str(pct)], dash=True, width=1.4, z=5))

    # 布林组
    boll_names = ["BOLL·上轨", "BOLL·中轨", "BOLL·下轨"]
    for n_, col in (("boll_up", "#e64545"), ("boll_mid", "#f0a500"), ("boll_low", "#12a15d")):
        arr = [None if not np.isfinite(v) else round(float(v), 3) for v in boll[n_][start:]]
        series.append({
            "type": "line", "name": n_.replace("boll_up", "BOLL·上轨").replace("boll_mid", "BOLL·中轨").replace("boll_low", "BOLL·下轨"),
            "xAxisIndex": 0, "yAxisIndex": 0, "data": arr, "showSymbol": False,
            "lineStyle": {"width": 1.0, "color": col, "type": "dashed"}, "z": 4,
        })

    # 图例初始状态：基底 + 道氏显示，江恩/布林隐藏
    all_names = ["K线", "成交量", "MA20"] + dow_names + gann_names + boll_names
    selected = {n: (n in dow_names or n in ("K线", "成交量", "MA20")) for n in all_names}

    option = {
        "animation": False,
        "backgroundColor": "#ffffff",
        "legend": {
            "type": "scroll", "top": 2, "left": "center",
            "itemWidth": 14, "itemHeight": 8, "textStyle": {"fontSize": 11, "color": "#4a5568"},
            "selected": selected,
        },
        "tooltip": {
            "trigger": "axis", "axisPointer": {"type": "cross"},
            "backgroundColor": "rgba(31,45,61,0.9)", "textStyle": {"color": "#fff", "fontSize": 12},
            "borderWidth": 0,
        },
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": [
            {"left": 56, "right": 20, "top": 34, "height": "58%"},
            {"left": 56, "right": 20, "top": "78%", "height": "12%"},
        ],
        "xAxis": [
            {"type": "category", "data": dates_v, "gridIndex": 0,
             "axisLine": {"lineStyle": {"color": "#cbd5e1"}}, "axisLabel": {"fontSize": 10}},
            {"type": "category", "data": dates_v, "gridIndex": 1,
             "axisLine": {"lineStyle": {"color": "#cbd5e1"}}, "axisLabel": {"show": False}},
        ],
        "yAxis": [
            {"scale": True, "gridIndex": 0, "splitLine": {"lineStyle": {"color": "#eef1f6"}},
             "axisLabel": {"fontSize": 10}},
            {"gridIndex": 1, "splitLine": {"show": False}, "axisLabel": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 15, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 15, "end": 100,
             "bottom": 0, "height": 18, "borderColor": "#d7dee8",
             "fillerColor": "rgba(37,99,235,0.12)"},
        ],
        "series": series,
    }
    groups = {"道氏": dow_names, "江恩": gann_names, "布林带": boll_names}
    return option, groups


def _echarts_script():
    """返回内嵌的 echarts.min.js 内容（<script> 包裹）。"""
    try:
        with open(ECHARTS_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return "<script>" + f.read() + "</script>"
    except Exception:
        return ""


# ================================================================ 对外入口

def build_tech_card(tx_code, name, embed_echarts=False):
    """生成个股技术分析的交互式 ECharts HTML 片段。
    embed_echarts: 是否同时内嵌 echarts.min.js（整份报告只需一次）。"""
    try:
        df = fetch_kline(tx_code, 520)
        if df is None or len(df) < 80:
            return ""
        df = calc_indicators(df)
    except Exception as e:
        return (f'<div class="hint" style="margin:10px 0;color:#c0392b">'
                f'{name} 技术分析获取失败（{e.__class__.__name__}）</div>')

    # 结构识别
    piv_high, piv_low = _detect_pivots(df, order=3)
    trendlines = _dow_trendlines(df, piv_high, piv_low)
    supports, resistances = _support_resistance(df, piv_high, piv_low)
    angles = _gann_angles(df, piv_low)
    hi, lo, fib = _fib_levels(df)
    boll = {"boll_up": df["boll_up"], "boll_mid": df["boll_mid"], "boll_low": df["boll_low"]}
    ma = {"ma20": df["ma20"], "ma60": df["ma60"], "ma250": df["ma250"]}

    dates = df["date"].tolist()
    kdata = [[round(o, 3), round(c, 3), round(l, 3), round(h, 3)]
             for o, c, l, h in zip(df["open"], df["close"], df["low"], df["high"])]
    volumes = [round(float(v), 1) for v in df["volume"]]

    dow = {"trendlines": trendlines, "supports": supports, "resistances": resistances}
    gann = {"angles": angles, "fib": fib}

    option, groups = build_option(dates, kdata, volumes, ma, dow, gann, boll)

    # 结论文本
    cur = float(df["close"].iloc[-1])
    last_date = str(df["date"].iloc[-1])
    sum_dow = _dow_summary(df, trendlines, supports, resistances)
    sum_gann = _gann_summary(df, angles, fib)
    sum_boll = _boll_summary(df)

    uid = "".join(ch for ch in str(tx_code) if ch.isalnum())
    opt_json = json.dumps(option, ensure_ascii=False)
    groups_json = json.dumps(groups, ensure_ascii=False)

    TAB_LABEL = {"道氏": "道氏理论", "江恩": "江恩理论", "布林带": "布林带"}
    tabs = "".join(
        f'<button class="tech-tab{" active" if g=="道氏" else ""}" data-group="{g}" '
        f'style="padding:4px 12px;border:1px solid #cbd5e1;border-radius:6px;background:'
        f'{"#2563eb" if g=="道氏" else "#fff"};color:{"#fff" if g=="道氏" else "#4a5568"};'
        f'font-size:12px;cursor:pointer;">{TAB_LABEL[g]}</button>' for g in groups)

    echarts_block = _echarts_script() if embed_echarts else ""

    html = f"""
<details class="tech-card" id="tech-details-{uid}" style="margin-top:14px;border:1px solid #e8edf3;border-radius:10px;background:#fcfdff;padding:0 14px;">
  <summary style="padding:12px 0;font-size:14px;font-weight:700;color:#1f2d3d;cursor:pointer;user-select:none;list-style:none;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <span>📈 {name} · 技术分析</span>
    <span style="font-size:12px;color:#8a94a3;font-weight:400;">截至 {last_date} 收盘 {cur:.2f}</span>
    <span style="flex:1"></span>
    <span class="tech-caret" style="font-size:12px;color:#2563eb;font-weight:400;">▸ 点击展开</span>
  </summary>
  <div style="padding-bottom:12px;">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;">
      <span style="font-size:13px;color:#4a5568;">分析理论：</span>
      {tabs}
    </div>
    <div id="tech-sum-{uid}" style="font-size:12px;color:#5a6573;line-height:1.9;background:#f4f7fb;border-radius:6px;padding:8px 10px;margin-bottom:8px;">
      <b>道氏理论：</b>{sum_dow}
    </div>
    <div id="tech-chart-{uid}" style="width:100%;height:560px;"></div>
    <div class="hint" style="margin-top:6px;font-size:12px;color:#8a94a3;">
      交互：滚轮缩放 / 拖拽平移 / 悬停查看数值；选项卡切换技术分析理论，标注叠加在同一张K线上；图例可单独开关每条线。
      技术分析基于历史数据统计，仅供参考，不构成投资建议。
    </div>
  </div>
  <script>
  (function(){{
    var details = document.getElementById('tech-details-{uid}');
    var chart = null, initDone = false;
    var groups = {groups_json};
    var sumTxt = {{
      "道氏": "{sum_dow}",
      "江恩": "{sum_gann}",
      "布林带": "{sum_boll}"
    }};
    function setGroup(g) {{
      var want = {{}};
      (groups[g]||[]).forEach(function(n){{ want[n]=true; }});
      var sel = chart.getOption().legend[0].selected;
      var allOptional = [];
      for (var k in groups) {{ allOptional = allOptional.concat(groups[k]); }}
      allOptional.forEach(function(n){{
        var cur = (sel[n]!==undefined) ? sel[n] : true;
        var target = (want[n]===true);
        if (cur !== target) {{
          chart.dispatchAction({{type:'legendToggleSelect', name:n}});
        }}
      }});
      document.getElementById('tech-sum-{uid}').innerHTML =
        '<b>' + g + '：</b>' + (sumTxt[g]||'');
    }}
    function initChart() {{
      if (initDone) return;
      initDone = true;
      chart = echarts.init(document.getElementById('tech-chart-{uid}'));
      chart.setOption({opt_json});
      var tabs = details.querySelectorAll('.tech-tab');
      for (var i=0;i<tabs.length;i++){{
        (function(btn){{
          btn.addEventListener('click', function(){{
            for (var j=0;j<tabs.length;j++){{
              tabs[j].style.background='#fff'; tabs[j].style.color='#4a5568';
            }}
            btn.style.background='#2563eb'; btn.style.color='#fff';
            setGroup(btn.getAttribute('data-group'));
          }});
        }})(tabs[i]);
      }}
    }}
    details.addEventListener('toggle', function(){{
      if (details.open) {{
        initChart();
        setTimeout(function(){{ if (chart) chart.resize(); }}, 60);
      }}
    }});
  }})();
  </script>
</details>
"""
    return echarts_block + html


if __name__ == "__main__":
    h = build_tech_card("sz000519", "中兵红箭", embed_echarts=True)
    print("卡片长度:", len(h))
    print("含选项卡:", "道氏理论" in h, "江恩理论" in h, "布林带" in h)
    print("含K线数据:", "candlestick" in h)
