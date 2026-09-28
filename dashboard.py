# -*- coding: utf-8 -*-
"""DFCF 本地终端 · 页面渲染（无地球 · 左侧任务栏 · 满屏面板）
================================================================
把已经确认过的两页样版落到正式代码里：

    大盘总览      output/index.html      主页。原来中间那块 3D 地球整块去掉
    自选与持仓    output/portfolio.html  持仓 / 自选 / 资产桶 / 风险检查集中一页

左侧是 174px 的纯文字任务栏，六个入口单击直达（同窗口跳转）：
    大盘总览 / 自选与持仓 / 个股详情 / 资产配置桶 / 策略执行台 / 全球眼

和旧版主页的区别（有意为之）：

  * 不再加载 three.js + globe.gl + 内嵌的 8K 地球贴图。旧的 index.html 有 5 MB，
    绝大部分是贴图；新版整页只有几十 KB，打开就是面板，不用等地球渲染。
  * 图表不依赖 lightweight-charts：分时 / 日K 由 Python 直接画成内联 SVG，
    少一个 JS 库、少一次外链加载，离线双击也能看。
  * 页面文字是生成时写进 HTML 的；JS 只做一件事 —— 定时刷新「财经快讯」。

对外接口：
    build_home(...)     生成主页 output/index.html（保持旧签名，main.py 不用改）
    build_assets(...)   生成 output/portfolio.html
"""
import html
import os

import config


# ---------------------------------------------------------------- 设计令牌

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg-0:#0a0e14; --bg-1:#111721; --bg-2:#18202c; --bg-3:#202b3a;
    --line-1:#1b2430; --line-2:#28323f;
    --fg-0:#e9eef6; --fg-1:#a9b5c6; --fg-2:#78879b; --fg-3:#556072;
    --accent:#ff9f1c; --accent-dim:rgba(255,159,28,.14); --accent-line:rgba(255,159,28,.42);
    --up:#f0453a; --down:#19a35f; --flat:#8b93a1; --blue:#4aa8ff;
  }
  *{box-sizing:border-box;}
  html,body{height:100%;margin:0;overflow:hidden;}
  body{background:var(--bg-0);color:var(--fg-0);font-size:12px;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
       font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased;}
  .app{display:grid;grid-template-columns:174px 1fr;height:100vh;overflow:hidden;}
  .rail{background:linear-gradient(180deg,#0d131c,#0a0e14);border-right:1px solid var(--line-1);
        display:flex;flex-direction:column;padding:12px 10px;gap:4px;overflow:hidden;}
  .rail .brand{font-size:15px;font-weight:800;letter-spacing:2px;padding:6px 8px 14px;}
  .rail .brand span{color:var(--accent);font-size:11px;letter-spacing:1px;margin-left:6px;}
  .ritem{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:8px;
        color:var(--fg-1);text-decoration:none;font-size:13px;border:1px solid transparent;
        transition:background 140ms ease,color 140ms ease,border-color 140ms ease;}
  .ritem:hover{background:var(--bg-2);color:var(--fg-0);}
  .ritem.on{background:var(--accent-dim);border-color:var(--accent-line);color:var(--accent);font-weight:700;}
  .railtip{margin-top:auto;color:var(--fg-3);font-size:11px;line-height:1.55;padding:8px;}
  .board{height:100vh;min-height:0;padding:10px 12px 12px;display:flex;flex-direction:column;gap:8px;}
  .topbar{display:flex;align-items:baseline;gap:14px;padding:0 2px;flex:none;}
  .topbar .t1{font-size:15px;font-weight:800;letter-spacing:.5px;}
  .topbar .t2{color:var(--fg-2);font-size:11.5px;}
  .topbar .t3{margin-left:auto;color:var(--accent);font-size:11.5px;
        border:1px dashed var(--accent-line);border-radius:999px;padding:3px 10px;white-space:nowrap;}
  .strip{display:grid;gap:6px;flex:none;}
  .metric{background:var(--bg-1);border:1px solid var(--line-1);border-radius:8px;
        padding:5px 9px;display:flex;flex-direction:column;gap:1px;min-width:0;}
  .metric .mname{color:var(--fg-2);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .metric .mval{font-size:18px;font-weight:700;line-height:1.15;}
  .metric .mpct{font-size:12px;font-weight:700;}
  .metric .msub{color:var(--fg-3);font-size:10.5px;}
  .up{color:var(--up);} .down{color:var(--down);} .flat{color:var(--flat);}
  .metric.up .mval,.metric.up .mpct{color:var(--up);}
  .metric.down .mval,.metric.down .mpct{color:var(--down);}
  .grid{flex:1;min-height:0;display:grid;gap:6px;grid-template-columns:repeat(4,minmax(0,1fr));}
  .grid.rows4{grid-template-rows:1.45fr 1fr 1.15fr .95fr;}
  .grid.rows3{grid-template-rows:1.05fr 1.1fr .95fr;}
  .panel{background:var(--bg-1);border:1px solid var(--line-1);border-radius:8px;
        display:flex;flex-direction:column;overflow:hidden;min-height:0;}
  .panel header{display:flex;align-items:center;gap:8px;padding:6px 9px 5px;
        border-bottom:1px solid var(--line-1);flex:none;}
  .panel h2{margin:0;font-size:12.5px;font-weight:700;letter-spacing:.3px;}
  .panel .ph{margin-left:auto;color:var(--fg-2);font-size:11px;white-space:nowrap;
        overflow:hidden;text-overflow:ellipsis;}
  .pbody{flex:1;min-height:0;overflow:hidden;padding:5px 9px 7px;}
  .pbody.scroll{overflow-y:auto;overflow-x:hidden;}
  .span2{grid-column:span 2;} .span2r{grid-row:span 2;} .span4{grid-column:span 4;}
  .chartempty{color:var(--fg-3);font-size:11.5px;padding:8px 0;}
  .hint{color:var(--fg-3);font-size:10.5px;margin-top:6px;line-height:1.5;}
  .spark,.candle{width:100%;height:100%;display:block;}
  .preline{stroke:#78879b;stroke-width:.8;stroke-dasharray:3 3;}
  .wick{stroke-width:1;} .wick.up{stroke:var(--up);} .wick.down{stroke:var(--down);}
  rect.up{fill:var(--up);} rect.down{fill:var(--down);}
  .brow{display:grid;grid-template-columns:110px 1fr 60px 72px;align-items:center;
        gap:6px;padding:1.5px 0;font-size:12px;}
  .bname{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .btrack{background:var(--bg-2);height:7px;border-radius:2px;overflow:hidden;}
  .btrack i{display:block;height:100%;}
  .btrack i.up{background:var(--up);} .btrack i.down{background:var(--down);}
  .bval{text-align:right;font-weight:700;}
  .bnote{color:var(--fg-3);font-size:10.5px;text-align:right;white-space:nowrap;
        overflow:hidden;text-overflow:ellipsis;}
  .trow{display:grid;grid-template-columns:1.3fr .8fr .8fr .75fr .8fr .6fr;gap:5px;
        padding:2.5px 0;border-bottom:1px solid rgba(27,36,48,.7);font-size:12px;
        color:inherit;text-decoration:none;}
  a.trow:hover{background:var(--bg-2);}
  .trow.head{color:var(--fg-3);font-size:10.5px;border-bottom:1px solid var(--line-2);}
  .trow.head:hover{background:transparent;}
  .tname{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .tcode{color:var(--fg-3);font-size:11px;}
  .tprice,.tval,.tnote,.tw{text-align:right;}
  .tnote{color:var(--fg-2);font-size:11px;}
  .lrow{display:grid;grid-template-columns:46px 1fr 40px;gap:6px;align-items:baseline;
        padding:2.5px 0;font-size:12px;}
  .lbadge{color:var(--accent);font-weight:700;}
  .lnames{color:var(--fg-1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .lnum{text-align:right;color:var(--fg-3);font-size:11px;}
  .nrow{display:grid;grid-template-columns:46px 1fr;gap:7px;padding:2px 0;
        border-bottom:1px solid rgba(27,36,48,.7);}
  .ntime{color:var(--fg-3);font-size:11px;}
  .ntitle{color:var(--fg-1);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
        text-decoration:none;display:block;}
  a.ntitle:hover{color:var(--fg-0);}
  .newscols{columns:2;column-gap:20px;}
  .distbar{display:flex;height:18px;border-radius:3px;overflow:hidden;background:var(--bg-2);}
  .distbar i{display:block;height:100%;}
  .distbar i.up{background:var(--up);} .distbar i.down{background:var(--down);}
  .distbar i.flat{background:var(--flat);}
  .distlegend{display:flex;justify-content:space-between;margin-top:8px;font-size:12px;}
  .statgrid{display:grid;grid-template-columns:1fr 1fr;gap:7px 14px;margin-top:10px;}
  .statgrid div{display:flex;justify-content:space-between;border-bottom:1px dashed var(--line-2);
        padding-bottom:3px;}
  .statgrid span{color:var(--fg-2);} .statgrid b{font-weight:700;}
  .chkrow{display:grid;grid-template-columns:1fr 96px;gap:8px;align-items:baseline;
        padding:3.5px 0;font-size:12px;border-bottom:1px dashed var(--line-2);}
  .chkrow .ck{text-align:right;font-weight:700;font-size:11px;}
  .chkrow .ck.ok{color:var(--down);} .chkrow .ck.warn{color:var(--accent);}
  .chkrow .ck.mute{color:var(--fg-3);font-weight:400;}
  .lnote{color:var(--fg-2);font-size:11px;}
  .bkrow{display:grid;grid-template-columns:104px 1fr 52px 84px;gap:6px;align-items:center;
        font-size:12px;padding:3.5px 0;}
  .bkrow .bktrack{background:var(--bg-2);height:10px;border-radius:2px;overflow:hidden;}
  .bkrow i{display:block;height:100%;background:var(--accent);opacity:.78;border-radius:2px;}
  .bkrow i.over{background:var(--up);}
  .bkrow b{text-align:right;} .bkrow em{color:var(--fg-3);font-size:11px;text-align:right;font-style:normal;}
  .wgroup{margin-bottom:9px;}
  .wgroup .wg{color:var(--fg-2);font-size:11px;margin:0 0 3px;}
  .wrow{display:grid;grid-template-columns:1fr 74px 62px;gap:6px;font-size:12px;padding:2px 0;
        color:inherit;text-decoration:none;}
  a.wrow:hover{background:var(--bg-2);}
  .wrow .wc{color:var(--fg-3);font-size:11px;}
  .wrow .wv{text-align:right;font-weight:700;}
  .kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}
  .kpi div{background:var(--bg-2);border-radius:6px;padding:6px 9px;min-width:0;}
  .kpi span{display:block;color:var(--fg-2);font-size:11px;}
  .kpi b{font-size:17px;white-space:nowrap;}
  ::-webkit-scrollbar{width:8px;height:8px;}
  ::-webkit-scrollbar-thumb{background:#243040;border-radius:4px;}
  ::-webkit-scrollbar-track{background:transparent;}
</style>
</head>
<body>
<div class="app">
  <nav class="rail">
    <div class="brand">DFCF<span>终端</span></div>
    __RAIL__
    <div class="railtip">__RAILTIP__</div>
  </nav>
  <main class="board">
    <div class="topbar">
      <span class="t1">__H1__</span>
      <span class="t2">__SUB__</span>
      <span class="t3">__BADGE__</span>
    </div>
    __BOARD__
  </main>
</div>
__SCRIPT__
</body>
</html>
"""


# ---------------------------------------------------------------- 小工具

def _e(value):
    return html.escape(str(value if value is not None else ""))


def _fnum(value, digits=2, dash="—"):
    try:
        return ("%%.%df" % digits) % float(value)
    except (TypeError, ValueError):
        return dash


def _pct_class(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "flat"
    return "up" if v > 0 else ("down" if v < 0 else "flat")


def _pct_text(value, digits=2):
    """+1.23% / -0.45%，永远带符号。"""
    try:
        return ("{:+." + str(int(digits)) + "f}%").format(float(value))
    except (TypeError, ValueError):
        return "—"


def _amount_text(value, dash="—"):
    """成交额：亿 / 万，够用就行（不追求跟行情软件逐位一致）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return dash
    if v >= 1e12:
        return "%.2f万亿" % (v / 1e12)
    if v >= 1e8:
        return "%.0f亿" % (v / 1e8)
    if v >= 1e4:
        return "%.0f万" % (v / 1e4)
    return "%.0f" % v


def _money_text(value, dash="—"):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return dash
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e8:
        return "%s%.2f亿" % (sign, a / 1e8)
    if a >= 1e4:
        return "%s%.2f万" % (sign, a / 1e4)
    return "%s%.0f" % (sign, a)


# ---------------------------------------------------------------- 图形（内联 SVG）

def _sparkline(points, width=900, height=300, pre_close=None, color="#4aa8ff"):
    vals = [p.get("value") for p in points if isinstance(p.get("value"), (int, float))]
    if len(vals) < 2:
        return '<div class="chartempty">暂无分时数据</div>'
    lo, hi = min(vals), max(vals)
    if pre_close:
        lo, hi = min(lo, float(pre_close)), max(hi, float(pre_close))
    pad = ((hi - lo) or 1.0) * 0.08
    lo, hi = lo - pad, hi + pad
    span = (hi - lo) or 1.0
    step = width / float(len(vals) - 1)
    pts = ["%.1f,%.1f" % (i * step, height - (v - lo) / span * height)
           for i, v in enumerate(vals)]
    poly = " ".join(pts)
    pre_line = ""
    if pre_close:
        y = height - (float(pre_close) - lo) / span * height
        pre_line = '<line x1="0" y1="%.1f" x2="%d" y2="%.1f" class="preline"/>' % (y, width, y)
    return ('<svg class="spark" viewBox="0 0 %d %d" preserveAspectRatio="none">'
            '<polygon points="0,%d %s %d,%d" fill="rgba(74,168,255,.13)"/>%s'
            '<polyline points="%s" fill="none" stroke="%s" stroke-width="1.4"/></svg>'
            % (width, height, height, poly, width, height, pre_line, poly, color))


def _candles(bars, ma, width=900, height=260, limit=60):
    bars = (bars or [])[-limit:]
    if len(bars) < 2:
        return '<div class="chartempty">暂无日K数据</div>'
    try:
        lo = min(float(b["low"]) for b in bars)
        hi = max(float(b["high"]) for b in bars)
    except (KeyError, TypeError, ValueError):
        return '<div class="chartempty">暂无日K数据</div>'
    span = (hi - lo) or 1.0
    step = width / float(len(bars))
    body = step * 0.62

    def y_of(value):
        return height - (float(value) - lo) / span * height

    parts = []
    for i, bar in enumerate(bars):
        x = i * step + step / 2
        cls = "up" if float(bar["close"]) >= float(bar["open"]) else "down"
        top = y_of(max(float(bar["open"]), float(bar["close"])))
        bot = y_of(min(float(bar["open"]), float(bar["close"])))
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="wick %s"/>'
                     % (x, y_of(bar["high"]), x, y_of(bar["low"]), cls))
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="%s"/>'
                     % (x - body / 2, top, body, max(1.0, bot - top), cls))
    for key, color in (("5", "#ffd166"), ("10", "#4aa8ff"), ("50", "#c084fc")):
        series = [row for row in ((ma or {}).get(key) or [])
                  if str(row.get("time")) >= str(bars[0].get("time"))]
        series = series[: len(bars)]
        if len(series) < 2:
            continue
        pts = " ".join("%.1f,%.1f" % (i * step + step / 2, y_of(row["value"]))
                       for i, row in enumerate(series))
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.1" opacity=".85"/>'
                     % (pts, color))
    return '<svg class="candle" viewBox="0 0 %d %d" preserveAspectRatio="none">%s</svg>' % (
        width, height, "".join(parts))


def _bar_row(name, pct, extra, span=6.0, note_limit=14):
    try:
        v = float(pct)
    except (TypeError, ValueError):
        v = 0.0
    width = min(100.0, abs(v) / span * 100.0)
    cls = _pct_class(v)
    note = str(extra or "")
    if len(note) > note_limit:
        note = note[:note_limit] + "…"
    return ('<div class="brow"><span class="bname">%s</span>'
            '<span class="btrack"><i class="%s" style="width:%.1f%%"></i></span>'
            '<span class="bval %s">%s</span><span class="bnote">%s</span></div>'
            % (_e(name), cls, width, cls, _pct_text(v), _e(note)))


def _breadth_bar(breadth):
    total = float((breadth or {}).get("total") or 0) or 1.0
    up = float((breadth or {}).get("up") or 0)
    down = float((breadth or {}).get("down") or 0)
    flat = float((breadth or {}).get("flat") or 0)
    return ('<div class="distbar"><i class="up" style="width:%.2f%%"></i>'
            '<i class="flat" style="width:%.2f%%"></i>'
            '<i class="down" style="width:%.2f%%"></i></div>'
            % (up / total * 100, flat / total * 100, down / total * 100))


# ---------------------------------------------------------------- 任务栏

def _rail_items():
    """六个入口。href 用「相对页面」的写法：双击 file:// 打开也能点得动，
    走 8766 服务时同样是这几个相对路径。"""
    globe = str(getattr(config, "GODSEYE_URL", "http://127.0.0.1:5180/") or "")
    return [
        ("overview", "大盘总览", "index.html"),
        ("assets", "自选与持仓", "portfolio.html"),
        ("stock", "个股详情", "../stock.html?code=600941"),
        ("buckets", "资产配置桶", "../strategy.html"),
        ("strategy", "策略执行台", "../strategy.html"),
        ("globe", "全球眼", globe),
    ]


def _rail_html(active):
    out = []
    for key, label, href in _rail_items():
        out.append('<a class="ritem%s" href="%s">%s</a>'
                   % (" on" if key == active else "", _e(href), _e(label)))
    return "".join(out)


_LIVE_JS = r"""
<script>
(function () {
  // 「财经快讯」定时刷新：live_server.py 每 5 分钟重抓一次，这里按版本号比对，
  // 只有真变了才重画这一段，页面其它部分不动（也不闪）。
  var api = "/api/news", pollSec = __POLL__, ver = 0;
  var box = document.getElementById('newsList');
  var note = document.getElementById('newsNote');
  if (!box || !window.fetch) return;
  if (location.protocol !== 'http:' && location.protocol !== 'https:') return;

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function hhmm(s) {
    var m = String(s || '').match(/(\d{1,2}:\d{2})/);
    return m ? m[1] : String(s || '').slice(-5);
  }
  function draw(list) {
    var h = '';
    for (var i = 0; i < list.length; i++) {
      var it = list[i] || {};
      var title = esc(it.title);
      var url = String(it.url || '');
      h += '<div class="nrow"><span class="ntime">' + esc(hhmm(it.time)) + '</span>' +
           (url ? '<a class="ntitle" href="' + esc(url) + '" target="_blank" rel="noopener">' + title + '</a>'
                : '<span class="ntitle">' + title + '</span>') + '</div>';
    }
    box.innerHTML = h || '<div class="chartempty">暂无快讯</div>';
  }
  function tick() {
    fetch(api + '?ver=' + ver, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.version || j.version === ver) return;
        ver = j.version;
        if (j.unchanged) return;
        var list = (j.events || []).slice(0, 60);
        if (!list.length) return;
        draw(list);
        if (note) note.textContent = hhmm(j.asof) + ' 已刷新 · 点标题看原文';
      })
      .catch(function () { /* 服务没起来就保持静态内容，不打扰 */ });
  }
  setInterval(tick, Math.max(60, pollSec) * 1000);
  setTimeout(tick, 5000);
})();
</script>
"""


def _page(title, active, h1, sub, badge, board, rail_tip, script="", poll_seconds=60):
    out = TEMPLATE
    for key, value in {
        "TITLE": _e(title),
        "RAIL": _rail_html(active),
        "RAILTIP": _e(rail_tip),
        "H1": _e(h1),
        "SUB": _e(sub),
        "BADGE": _e(badge),
        "BOARD": board,
        "SCRIPT": script.replace("__POLL__", str(max(60, int(poll_seconds or 60)))),
    }.items():
        out = out.replace("__%s__" % key, value)
    return out


# ---------------------------------------------------------------- 大盘总览

def _metric_cards(market):
    indices = (market or {}).get("indices") or []
    breadth = (market or {}).get("breadth") or {}
    limit_up = (market or {}).get("limit_up") or {}
    limit_down = (market or {}).get("limit_down") or {}

    cards = []
    for item in indices[:6]:
        cards.append(
            '<div class="metric %s"><span class="mname">%s</span>'
            '<span class="mval">%s</span><span class="mpct">%s</span>'
            '<span class="msub">额 %s</span></div>'
            % (_pct_class(item.get("change_pct")), _e(item.get("name")),
               _fnum(item.get("price")), _pct_text(item.get("change_pct")),
               _amount_text(item.get("amount"))))
    up_n = int(float(breadth.get("up") or 0))
    down_n = int(float(breadth.get("down") or 0))
    cards.append(
        '<div class="metric"><span class="mname">涨跌家数</span>'
        '<span class="mval"><b class="up">%d</b> / <b class="down">%d</b></span>'
        '<span class="msub">平 %s · 共 %s</span>%s</div>'
        % (up_n, down_n, _e(breadth.get("flat")), _e(breadth.get("total")),
           _breadth_bar(breadth)))
    top_items = limit_up.get("items") or [{}]
    top_lbc = top_items[0].get("lbc") or "—"
    cards.append(
        '<div class="metric"><span class="mname">涨停 / 跌停</span>'
        '<span class="mval"><b class="up">%s</b> / <b class="down">%s</b></span>'
        '<span class="msub">最高连板 %s</span></div>'
        % (_e(limit_up.get("count")), _e(limit_down.get("count")), _e(top_lbc)))
    return cards


def _stock_rows(rows, limit=13):
    out = []
    for x in (rows or [])[:limit]:
        code = str(x.get("code") or "")
        cell = ('<a class="trow" href="../stock.html?code=%s" title="打开 %s 的个股终端">' % (_e(code), _e(code)))
        out.append(
            cell +
            '<span class="tname">%s</span><span class="tcode">%s</span>'
            '<span class="tprice">%s</span><span class="tval %s">%s</span>'
            '<span class="tnote">%s</span><span class="tw">%s</span></a>'
            % (_e(x.get("name")), _e(code), _fnum(x.get("price"), 3),
               _pct_class(x.get("change_pct")), _pct_text(x.get("change_pct")),
               _amount_text(x.get("amount")), "—"))
    return "".join(out) or '<div class="chartempty">无数据</div>'


def _ladder_rows(limit_up):
    ladders = {}
    for item in (limit_up or {}).get("items") or []:
        try:
            key = int(float(item.get("lbc") or 1))
        except (TypeError, ValueError):
            key = 1
        ladders.setdefault(key, []).append(item)
    if not ladders:
        return '<div class="chartempty">无涨停</div>'
    return "".join(
        '<div class="lrow"><span class="lbadge">%d板</span><span class="lnames">%s</span>'
        '<span class="lnum">%d只</span></div>'
        % (key, "、".join(_e(x.get("name")) for x in ladders[key][:8]), len(ladders[key]))
        for key in sorted(ladders, reverse=True))


def _news_rows(news, limit=22):
    out = []
    for item in (news or [])[:limit]:
        t = str(item.get("time") or "")
        t = t[11:16] if len(t) >= 16 else t[-5:]
        url = str(item.get("url") or "")
        title = _e(item.get("title"))
        link = ('<a class="ntitle" href="%s" target="_blank" rel="noopener">%s</a>'
                % (_e(url), title)) if url else '<span class="ntitle">%s</span>' % title
        out.append('<div class="nrow"><span class="ntime">%s</span>%s</div>' % (_e(t), link))
    return "".join(out) or '<div class="chartempty">暂无快讯</div>'


def overview_board(market, chart, refresh_minutes=5):
    market = market or {}
    chart = chart or {}
    minute = ((chart.get("minute") or {}).get("points") or [])
    daily = chart.get("daily") or {}
    breadth = market.get("breadth") or {}
    limit_up = market.get("limit_up") or {}
    limit_down = market.get("limit_down") or {}
    indices = market.get("indices") or []

    cards = _metric_cards(market)
    last_val = (minute[-1] if minute else {}).get("value") if isinstance(minute, list) else None
    pre_close = (chart.get("minute") or {}).get("pre_close")

    up_n = int(float(breadth.get("up") or 0))
    down_n = int(float(breadth.get("down") or 0))
    industry = "".join(_bar_row(x.get("name"), x.get("change_pct"), x.get("lead_stock") or "")
                       for x in (market.get("industry_up") or [])[:8]) or '<div class="chartempty">无数据</div>'
    concept = "".join(_bar_row(x.get("name"), x.get("change_pct"), x.get("lead_stock") or "")
                      for x in (market.get("concept_up") or [])[:8]) or '<div class="chartempty">无数据</div>'

    # 注意：_breadth_bar() 的返回值里已经带字面量 %（例如 width:34.56%），
    # 所以不能再把它拼进同一个 %-格式串，否则会被当成格式符。分开拼。
    breadth_panel = _breadth_bar(breadth)
    breadth_panel += (
        '<div class="distlegend"><span class="up">上涨 %d</span>'
        '<span class="flat">平 %s</span><span class="down">下跌 %d</span></div>'
    ) % (up_n, _e(breadth.get("flat")), down_n)
    breadth_panel += (
        '<div class="statgrid">'
        '<div><span>成交额</span><b>%s</b></div>'
        '<div><span>涨停</span><b class="up">%s</b></div>'
        '<div><span>跌停</span><b class="down">%s</b></div>'
        '<div><span>总家数</span><b>%s</b></div>'
        '</div>'
    ) % (_amount_text((indices[0] if indices else {}).get("amount")),
         _e(limit_up.get("count")), _e(limit_down.get("count")),
         _e(breadth.get("total")))

    return (
        '<div class="strip" style="grid-template-columns:repeat(%d,minmax(0,1fr));">%s</div>'
        '<div class="grid rows4">'
        '<section class="panel span2 span2r"><header><h2>上证分时</h2>'
        '<span class="ph">%s</span></header><div class="pbody">%s</div></section>'
        '<section class="panel"><header><h2>涨跌分布</h2><span class="ph">全市场</span></header>'
        '<div class="pbody">%s</div></section>'
        '<section class="panel"><header><h2>涨停梯队</h2><span class="ph">按连板高度</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel"><header><h2>行业板块</h2><span class="ph">涨幅榜 · 前 8</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel"><header><h2>概念板块</h2><span class="ph">涨幅榜 · 前 8</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel span2"><header><h2>上证日K</h2>'
        '<span class="ph">近 60 根 · MA5 / MA10 / MA50</span></header>'
        '<div class="pbody">%s</div></section>'
        '<section class="panel"><header><h2>个股涨幅榜</h2><span class="ph">点一行看个股</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel"><header><h2>个股跌幅榜</h2><span class="ph">点一行看个股</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel span4"><header><h2>财经快讯</h2>'
        '<span class="ph" id="newsNote">每 %d 分钟自动刷新 · 点标题看原文</span></header>'
        '<div class="pbody scroll newscols" id="newsList">%s</div></section>'
        '</div>'
        % (len(cards), "".join(cards),
           _e("昨收 %s · 现 %s" % (_fnum(pre_close), _fnum(last_val))),
           _sparkline(minute, 900, 300, pre_close),
           breadth_panel,
           _ladder_rows(limit_up),
           industry,
           concept,
           _candles(daily.get("bars"), daily.get("ma"), 900, 260),
           _stock_rows(market.get("stock_up")),
           _stock_rows(market.get("stock_down")),
           max(1, int(refresh_minutes or 5)),
           _news_rows(market.get("news")))
    )


def build_overview_html(payload, market, chart_data, out_path, poll_seconds=60):
    asof = str((market or {}).get("time") or (payload or {}).get("asof") or "")
    refresh_minutes = max(1, int(getattr(config, "NEWS_REFRESH_SECONDS", 300) or 300) // 60)
    board = overview_board(market, chart_data, refresh_minutes=refresh_minutes)
    html_text = _page(
        title="DFCF 本地终端 · 大盘总览",
        active="overview",
        h1="大盘总览",
        sub="数据时间 %s · 已移除中央地球仪，全部改成满屏面板" % (asof[5:16] if len(asof) >= 16 else asof),
        badge="A 股 · 实时快照",
        board=board,
        rail_tip="左侧任务栏：大盘总览 / 自选与持仓 / 个股详情 / 资产配置桶 / 策略执行台 / 全球眼。",
        script=_LIVE_JS,
        poll_seconds=poll_seconds,
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return out_path


# ---------------------------------------------------------------- 自选与持仓

def _positions_panel(payload):
    pos = (payload or {}).get("positions") or {}
    if pos.get("empty") or not pos.get("items"):
        return ('<div class="chartempty">%s</div>'
                '<div class="hint">添加持仓：<a href="http://127.0.0.1:8765" '
                'style="color:var(--blue)">http://127.0.0.1:8765</a>（持仓管理页），'
                '加完重新运行一次启动脚本即可。</div>'
                % _e(pos.get("note") or "暂无持仓"))

    total_mv = float(pos.get("total_mv") or 0) or 1.0
    rows = []
    for x in pos.get("items") or []:
        code = str(x.get("code") or "")
        weight = float(x.get("mv") or 0) / total_mv * 100.0
        cell = ('<a class="trow" href="../stock.html?code=%s" title="打开 %s 的个股终端">'
                % (_e(code), _e(code))) if x.get("page") else '<div class="trow">'
        rows.append(
            cell +
            '<span class="tname">%s</span><span class="tcode">%s</span>'
            '<span class="tprice">%s</span><span class="tval %s">%s</span>'
            '<span class="tnote">%s</span><span class="tw">%.1f%%</span></%s>'
            % (_e(x.get("name")), _e(code), _fnum(x.get("price"), 3),
               _pct_class(x.get("pnl_pct")), _pct_text(x.get("pnl_pct")),
               _money_text(x.get("mv")), weight,
               "a" if x.get("page") else "div"))
    head = ('<div class="trow head"><span>标的</span><span>代码</span><span>现价</span>'
            '<span>盈亏</span><span>市值</span><span>占比</span></div>')
    return head + "".join(rows)


def _buckets_panel(buckets):
    """资产配置桶：目标权重 vs 实际权重。数据来自加密库（strategy.json.enc），
    读不到就显示一句人话，不影响页面其它部分。"""
    if not buckets:
        return ('<div class="chartempty">暂未读到配置桶</div>'
                '<div class="hint">配置桶存在加密库（strategy.json.enc）里，'
                '需要启动脚本解锁保险库后才读得到。</div>')
    rows = []
    for b in buckets:
        actual, target = float(b.get("actual") or 0), float(b.get("target") or 0)
        width = min(100.0, actual * 100.0)
        cls = "over" if abs(actual - target) >= 0.05 else ""
        rows.append(
            '<div class="bkrow"><span>%s</span>'
            '<span class="bktrack"><i class="%s" style="width:%.1f%%"></i></span>'
            '<b>%.1f%%</b><em>目标 %.1f%%</em></div>'
            % (_e(b.get("name")), cls, width, actual * 100, target * 100))
    return "".join(rows)


def _watchlist_panel(payload):
    wl = (payload or {}).get("watchlist") or {}
    groups = wl.get("groups") or []
    out = []
    for g in groups:
        rows = []
        for x in g.get("rows") or []:
            code = str(x.get("code") or "")
            pct = "—" if x.get("pct") is None else _pct_text(x.get("pct"), 2)
            if x.get("is_rate"):
                pct = ("%.2f%%" % float(x["pct"])) if x.get("pct") is not None else "—"
            tag = ('<a class="wrow" href="../stock.html?code=%s" title="打开 %s 的个股终端">'
                   % (_e(code), _e(code))) if x.get("page") else '<div class="wrow">'
            rows.append(tag +
                        '<span class="wname">%s</span><span class="wc">%s</span>'
                        '<span class="wv %s">%s</span></%s>'
                        % (_e(x.get("name")), _e(code), _pct_class(x.get("pct")), pct,
                           "a" if x.get("page") else "div"))
        out.append('<div class="wgroup"><p class="wg">%s · %d 只</p>%s</div>'
                   % (_e(g.get("label")), len(g.get("rows") or []),
                      "".join(rows) or '<div class="chartempty">空</div>'))
    if not out:
        return ('<div class="chartempty">%s</div>'
                '<div class="hint">添加自选：http://127.0.0.1:8765（持仓管理页）。</div>'
                % _e(wl.get("error") or "暂无自选"))
    return "".join(out)


def _overview_kpi(payload):
    pos = (payload or {}).get("positions") or {}
    wl = (payload or {}).get("watchlist") or {}
    items = pos.get("items") or []
    total_mv = float(pos.get("total_mv") or 0)
    day = float(pos.get("total_day") or 0)
    pnl = float(pos.get("total_pnl") or 0)
    pct = pos.get("total_pct")
    top_code = "—"
    top_w = 0.0
    if items and total_mv > 0:
        best = max(items, key=lambda x: float(x.get("mv") or 0))
        top_code = str(best.get("name") or best.get("code") or "—")
        top_w = float(best.get("mv") or 0) / total_mv * 100.0
    kpi = (
        '<div class="kpi">'
        '<div><span>总市值</span><b>%s</b></div>'
        '<div><span>当日盈亏</span><b class="%s">%s</b></div>'
        '<div><span>占比最高</span><b>%s</b></div>'
        '<div><span>持仓只数</span><b>%d</b></div>'
        '</div>'
        % (_money_text(total_mv), _pct_class(day), _money_text(day),
           _e(top_code), len(items)))
    stats = (
        '<div class="statgrid">'
        '<div><span>当日盈亏比</span><b class="%s">%s</b></div>'
        '<div><span>累计盈亏</span><b class="%s">%s</b></div>'
        '<div><span>最大单腿</span><b>%.1f%%</b></div>'
        '<div><span>自选池</span><b>%s 只</b></div>'
        '</div>'
        % (_pct_class(pct if pct is not None else 0),
           _pct_text(pct) if pct is not None else "—",
           _pct_class(pnl), _money_text(pnl), top_w,
           _e(wl.get("total") or 0)))
    return kpi + stats


def _distribution_panel(payload):
    pos = (payload or {}).get("positions") or {}
    items = pos.get("items") or []
    total_mv = float(pos.get("total_mv") or 0) or 1.0
    rows = [x for x in items if float(x.get("mv") or 0) > 0]
    rows.sort(key=lambda x: float(x.get("mv") or 0), reverse=True)
    if not rows:
        return '<div class="chartempty">暂无持仓</div>'
    return "".join(
        _bar_row(x.get("name"), float(x.get("mv") or 0) / total_mv * 100.0,
                 "市值 " + _money_text(x.get("mv")), span=30.0, note_limit=18)
        for x in rows)


def _vault_unlocked():
    """保险库（加密库）现在能不能读。

    生成页面时是同步调用 secure_store 的：**未解锁它会弹密码提示**，
    卡在提示上就等于整站起不来。所以凡是碰加密库的地方，先过这一关；
    没解锁就优雅降级（正常流程里 main.py 开头已经解锁过）。
    """
    try:
        import secure_store
        return secure_store.unlocked_key() is not None
    except Exception:
        return False


def _checks_panel(payload, buckets):
    pos = (payload or {}).get("positions") or {}
    wl = (payload or {}).get("watchlist") or {}
    items = pos.get("items") or []
    total_mv = float(pos.get("total_mv") or 0)
    rows = []

    if items and total_mv > 0:
        top = max(items, key=lambda x: float(x.get("mv") or 0))
        top_w = float(top.get("mv") or 0) / total_mv * 100.0
        rows.append(("单腿集中度（最大 %s %.1f%%，上限 30%%）"
                     % (top.get("name") or top.get("code"), top_w),
                     "✓ 通过" if top_w < 30 else "超限",
                     "ok" if top_w < 30 else "warn"))
    else:
        rows.append(("单腿集中度", "无持仓", "mute"))

    if buckets:
        cash = next((b for b in buckets if b.get("key") == "cash"), None)
        drift = [b for b in buckets if abs(float(b.get("actual") or 0) - float(b.get("target") or 0)) >= 0.05]
        if cash:
            rows.append(("现金比例 %.1f%%（目标 %.1f%% ±5）"
                         % (float(cash["actual"]) * 100, float(cash["target"]) * 100),
                         "✓ 通过" if abs(float(cash["actual"]) - float(cash["target"])) < 0.05 else "偏离",
                         "ok" if abs(float(cash["actual"]) - float(cash["target"])) < 0.05 else "warn"))
        rows.append(("桶位偏离超 band（±5%）", "%d 项" % len(drift),
                     "ok" if not drift else "warn"))
    else:
        rows.append(("桶位偏离检查", "未读到配置桶", "mute"))

    n_watch = int((wl.get("total") or 0) or 0)
    rows.append(("自选池（%d 只）" % n_watch, "✓ 正常" if n_watch else "空", "ok" if n_watch else "mute"))

    if _vault_unlocked():
        try:
            import rebalance
            nxt = rebalance.next_rebalance()
            label = nxt.get("date") or "未配置"
            rows.append(("下次再平衡窗口", "%s（%s 天后）" % (label, nxt.get("in_days")),
                         "ok" if label != "未配置" else "mute"))
        except Exception as e:
            rows.append(("下次再平衡窗口", "读不到（%s）" % e.__class__.__name__, "mute"))
    else:
        rows.append(("下次再平衡窗口", "保险库未解锁", "mute"))
    return "".join(
        '<div class="chkrow"><span>%s</span><span class="ck %s">%s</span></div>'
        % (_e(label), cls, _e(status)) for label, status, cls in rows)


def _read_buckets():
    """从加密库里读「目标权重 vs 实际权重」。读不到就返回 []，页面照常显示。

    必须先确认保险库**已经解锁**再动手：secure_store 的读取在未解锁时会
    弹密码提示，而这里是生成页面时同步调用的 —— 卡在提示上就等于整站起不来。
    （正常流程里 main.py 开头已经解锁过，所以走的是「已解锁」那条路。）
    """
    if not _vault_unlocked():
        return []
    try:
        import strategy
        sid = strategy.active_id()
        av = strategy.account_view()
        target = strategy.target_weights(sid)
        bk = strategy.buckets()
    except Exception:
        return []
    capital = float(av.get("capital") or 0)
    actual_amt = av.get("buckets") or {}
    if capital <= 0:
        return []
    out = []
    for key, cfg in (bk or {}).items():
        if key.startswith("_"):
            continue
        out.append({
            "key": key,
            "name": cfg.get("name") or key,
            "target": float(target.get(key) or 0),
            "actual": float(actual_amt.get(key) or 0) / capital,
        })
    out.sort(key=lambda b: -b["target"])
    return out


def assets_board(payload, buckets):
    return (
        '<div class="grid rows3">'
        '<section class="panel span2"><header><h2>我的持仓</h2>'
        '<span class="ph">现价 / 盈亏 / 市值 / 占比 · 点一行看个股</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel span2"><header><h2>资产配置桶</h2>'
        '<span class="ph">目标 vs 实际（读加密库）</span></header>'
        '<div class="pbody">%s'
        '<div class="hint">实际权重 = 该桶市值 / 家庭可投资总额；'
        '偏离超过 ±5%%（band）才需要动手。</div></div></section>'
        '<section class="panel span2"><header><h2>我的自选</h2>'
        '<span class="ph">ETF / 股票 / 其他 · 只跟行情</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel span2"><header><h2>组合概览</h2>'
        '<span class="ph">按当前快照</span></header>'
        '<div class="pbody">%s</div></section>'
        '<section class="panel span2"><header><h2>持仓分布</h2>'
        '<span class="ph">按标的占比</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '<section class="panel span2"><header><h2>风险与再平衡检查</h2>'
        '<span class="ph">每次打开自动跑一遍</span></header>'
        '<div class="pbody scroll">%s</div></section>'
        '</div>'
        % (_positions_panel(payload),
           _buckets_panel(buckets),
           _watchlist_panel(payload),
           _overview_kpi(payload),
           _distribution_panel(payload),
           _checks_panel(payload, buckets))
    )


def build_assets_html(payload, market, out_path, poll_seconds=60):
    buckets = _read_buckets()
    pos = (payload or {}).get("positions") or {}
    wl = (payload or {}).get("watchlist") or {}
    sub = ("%d 只持仓 · %s 只自选 · %d 个配置桶 · 全部读本地加密库"
           % (len(pos.get("items") or []), wl.get("total") or 0, len(buckets)))
    html_text = _page(
        title="DFCF 本地终端 · 自选与持仓",
        active="assets",
        h1="自选与持仓",
        sub=sub,
        badge="私密数据 · 本地加密库",
        board=assets_board(payload, buckets),
        rail_tip="这里只读本地加密库；改持仓 / 自选请到持仓管理页（8765）。",
        poll_seconds=poll_seconds,
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return out_path
