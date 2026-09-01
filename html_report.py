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
import llm_config

from utils import fmt_pct as _fmt_pct

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

AGENTS_JS = """
<script>
// ============ 多 Agent 辩论（浏览器端实时调用 DeepSeek API） ============
const AGENT_ROLES = [
  {key:'market', name:'市场分析师', icon:'📊', system:'你是一位 A 股市场技术分析专家，擅长 K线形态、均线系统、量价关系、趋势判断。请基于给出的技术数据，给出客观的技术面分析和多空判断。'},
  {key:'social', name:'舆情分析师', icon:'💬', system:'你是一位 A 股市场情绪与舆情分析专家，擅长判断散户情绪、市场热度、资金关注度。请基于给出的数据和当前市场环境，分析该股的舆情面和情绪面。'},
  {key:'news', name:'新闻分析师', icon:'📰', system:'你是一位 A 股新闻与事件驱动分析专家，擅长从行业新闻、公司公告、宏观事件中挖掘对个股的影响。请基于给出的信息，分析近期可能影响该股的新闻和事件。'},
  {key:'fundamentals', name:'基本面分析师', icon:'📈', system:'你是一位 A 股基本面分析专家，擅长财务分析、盈利能力评估、估值判断、行业地位分析。请基于给出的数据，分析该股的基本面状况和估值水平。'},
  {key:'policy', name:'政策分析师', icon:'🏛️', system:'你是一位 A 股政策分析专家，擅长判断监管政策、产业政策、窗口指导对个股和板块的影响。A 股是政策市，请重点分析政策面对该股的影响。'},
  {key:'hot_money', name:'游资追踪师', icon:'🔥', system:'你是一位 A 股游资与资金流向分析专家，擅长追踪龙虎榜、大单流向、主力资金动态。游资是 A 股短线定价的核心力量，请分析该股的资金面和游资参与度。'},
  {key:'lockup', name:'解禁监控师', icon:'🔓', system:'你是一位 A 股限售股解禁与减持监控专家，擅长分析限售股解禁、大股东减持、股权质押对股价的供给冲击。解禁是 A 股特有的重大供给冲击因素，请分析该股的解禁与减持风险。'}
];
const RATING_STYLE = {
  BUY:{color:'#e74c3c',bg:'#fdecea',label:'买入 BUY',icon:'🟢'},
  HOLD:{color:'#f39c12',bg:'#fef5e7',label:'持有 HOLD',icon:'🟡'},
  SELL:{color:'#27ae60',bg:'#eafaf1',label:'卖出 SELL',icon:'🔴'}
};
async function agentChat(systemPrompt,userPrompt,temperature){
  temperature = temperature || 0.7;
  const resp = await fetch('https://api.deepseek.com/chat/completions',{
    method:'POST',
    headers:{'Authorization':'Bearer '+DEEPSEEK_API_KEY,'Content-Type':'application/json'},
    body:JSON.stringify({
      model:'deepseek-chat',
      messages:[{role:'system',content:systemPrompt},{role:'user',content:userPrompt}],
      temperature:temperature,max_tokens:4096
    })
  });
  const data = await resp.json();
  if(data.choices && data.choices[0]) return data.choices[0].message.content;
  return '[API错误]';
}
function buildDataBrief(block){
  const d = block.dataset;
  let lines = ['股票: '+d.name+'('+d.tx+')','日期: '+new Date().toISOString().slice(0,10)];
  if(d.price) lines.push('现价: '+d.price+'  涨跌幅: '+d.changePct+'%');
  if(d.cost && d.price){
    const pnlPct = ((parseFloat(d.price)-parseFloat(d.cost))/parseFloat(d.cost)*100).toFixed(2);
    lines.push('持仓成本: '+d.cost+'  持仓盈亏: '+pnlPct+'%');
  }
  if(d.ma20) lines.push('MA20: '+d.ma20+'  MA60: '+d.ma60+'  MA250: '+d.ma250);
  if(d.high20) lines.push('20日最高: '+d.high20+'  20日最低: '+d.low20);
  if(d.change60) lines.push('60日涨跌幅: '+d.change60+'%');
  if(d.volRatio) lines.push('5日/20日量比: '+d.volRatio);
  return lines.join('\\n');
}
async function runAnalyst(role,dataBrief,progressEl){
  progressEl.textContent = '  ['+role.index+'/7] '+role.icon+role.name+' 分析中...';
  const userPrompt = '请分析以下股票数据，给出你的专业判断。要求：\\n1. 先给出你的核心结论（看多/看空/中性）\\n2. 然后给出 3-5 条具体分析依据\\n3. 最后给出风险提示\\n4. 总字数控制在 300 字以内，语言精炼专业\\n\\n'+dataBrief;
  const report = await agentChat(role.system,userPrompt,0.7);
  return {key:role.key,name:role.name,icon:role.icon,report:report};
}
async function bullBearDebate(analystReports,dataBrief,progressEl){
  progressEl.textContent = '  ⚖️ 多空辩论中...';
  let reportsText = analystReports.map(function(r){return '【'+r.icon+r.name+'】\\n'+r.report;}).join('\\n\\n');
  const system = '你是一位资深的 A 股投资辩论主持人，需要整合 7 位分析师的报告，组织一场多空辩论。请分别总结多方观点和空方观点，然后给出辩论后的倾向性判断。';
  const userPrompt = '以下是 7 位分析师对同一只股票的分析报告：\\n\\n'+reportsText+'\\n\\n'+dataBrief+'\\n\\n请输出：\\n1. 【多方核心观点】（3条以内）\\n2. 【空方核心观点】（3条以内）\\n3. 【辩论结论】（多方占优/空方占优/势均力敌，一句话说明理由）\\n总字数控制在 400 字以内。';
  return await agentChat(system,userPrompt,0.6);
}
async function finalDecision(analystReports,debate,dataBrief,progressEl){
  progressEl.textContent = '  🎯 最终决策中...';
  let reportsText = analystReports.map(function(r){return '- '+r.icon+r.name+': '+r.report.slice(0,100)+'...';}).join('\\n');
  const system = '你是一位经验丰富的 A 股投资组合经理，需要综合 7 位分析师报告和多空辩论结果，给出最终投资评级、目标价区间和止损位。评级分为：买入(BUY)、持有(HOLD)、卖出(SELL)三档。目标价基于技术面支撑阻力位和基本面估值给出乐观/中性/悲观三档，止损位基于关键技术支撑位给出。';
  const userPrompt = '股票数据：\\n'+dataBrief+'\\n\\n分析师报告摘要：\\n'+reportsText+'\\n\\n多空辩论结果：\\n'+debate+'\\n\\n请输出严格的 JSON 格式（不要输出其他内容）：\\n{\\n  "rating": "BUY" 或 "HOLD" 或 "SELL",\\n  "confidence": "高" 或 "中" 或 "低",\\n  "one_liner": "一句话理由（50字以内）",\\n  "key_reasons": ["理由1","理由2","理由3"],\\n  "risk_warning": "主要风险提示（30字以内）",\\n  "target_optimistic": "乐观目标价（数字）",\\n  "target_base": "中性目标价（数字）",\\n  "target_pessimistic": "悲观目标价（数字）",\\n  "stop_loss": "止损位（数字）",\\n  "target_logic": "目标价推导逻辑（30字以内）"\\n}';
  const result = await agentChat(system,userPrompt,0.3);
  try{
    const start = result.indexOf('{');
    const end = result.lastIndexOf('}')+1;
    if(start>=0 && end>start) return JSON.parse(result.slice(start,end));
  }catch(e){}
  return {rating:'HOLD',confidence:'低',one_liner:'多空因素交织，建议观望',key_reasons:['分析师观点分歧较大'],risk_warning:'市场不确定性较高',target_optimistic:'',target_base:'',target_pessimistic:'',stop_loss:'',target_logic:''};
}
function renderAgentResult(resultEl,result){
  const style = RATING_STYLE[result.rating] || RATING_STYLE.HOLD;
  let analystHtml = result.analyst_reports.map(function(r){
    return '<div style="margin:8px 0;padding:10px 14px;background:#f8f9fa;border-radius:6px;border-left:3px solid #4a6cf7;">'+
      '<div style="font-weight:600;color:#2c3e50;margin-bottom:4px;">'+r.icon+' '+r.name+'</div>'+
      '<div style="font-size:12px;color:#555;line-height:1.6;white-space:pre-wrap;">'+r.report+'</div></div>';
  }).join('');
  let reasonsHtml = result.key_reasons.map(function(r){return '<li>'+r+'</li>';}).join('');
  resultEl.innerHTML =
    '<details class="card agent-card" open style="margin:0;border:1px solid #e1e8ed;border-radius:8px;overflow:hidden;">'+
    '<summary style="cursor:pointer;padding:14px 18px;background:linear-gradient(135deg,'+style.bg+',#fff);font-weight:600;font-size:15px;list-style:none;">'+
    '<span style="font-size:18px;margin-right:8px;">🤖</span>多Agent深度分析'+
    '<span style="float:right;padding:3px 12px;border-radius:12px;background:'+style.color+';color:#fff;font-size:12px;font-weight:500;">'+
    style.icon+' '+style.label+' · 置信度'+result.confidence+'</span>'+
    '<div style="font-weight:400;font-size:13px;color:#666;margin-top:4px;">'+result.one_liner+'</div></summary>'+
    '<div style="padding:16px 18px;background:#fff;">'+
    '<div style="margin-bottom:14px;padding:12px 16px;background:'+style.bg+';border-radius:6px;">'+
    '<div style="font-weight:600;color:'+style.color+';margin-bottom:6px;">核心逻辑</div>'+
    '<ul style="margin:0;padding-left:20px;font-size:13px;color:#444;line-height:1.7;">'+reasonsHtml+'</ul>'+
    '<div style="margin-top:8px;font-size:12px;color:#888;">⚠️ '+result.risk_warning+'</div></div>'+
    (result.target_base ? '<div style="margin-bottom:14px;padding:12px 16px;background:#f0f4ff;border-radius:6px;">'+
      '<div style="font-weight:600;color:#2c3e50;margin-bottom:8px;">🎯 目标价与止损位</div>'+
      '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px;">'+
        '<div style="flex:1;min-width:80px;text-align:center;padding:8px 4px;background:#eafaf1;border-radius:4px;"><div style="font-size:11px;color:#27ae60;">乐观目标</div><div style="font-size:17px;font-weight:700;color:#27ae60;">'+result.target_optimistic+'</div></div>'+
        '<div style="flex:1;min-width:80px;text-align:center;padding:8px 4px;background:#fef9e7;border-radius:4px;"><div style="font-size:11px;color:#f39c12;">中性目标</div><div style="font-size:17px;font-weight:700;color:#f39c12;">'+result.target_base+'</div></div>'+
        '<div style="flex:1;min-width:80px;text-align:center;padding:8px 4px;background:#fdecea;border-radius:4px;"><div style="font-size:11px;color:#e74c3c;">悲观目标</div><div style="font-size:17px;font-weight:700;color:#e74c3c;">'+result.target_pessimistic+'</div></div>'+
        '<div style="flex:1;min-width:80px;text-align:center;padding:8px 4px;background:#f5f5f5;border-radius:4px;"><div style="font-size:11px;color:#7f8c8d;">止损位</div><div style="font-size:17px;font-weight:700;color:#7f8c8d;">'+result.stop_loss+'</div></div>'+
      '</div>'+
      (result.target_logic ? '<div style="font-size:12px;color:#888;">📐 '+result.target_logic+'</div>' : '')+
    '</div>' : '')+
    '<div style="margin-bottom:14px;"><div style="font-weight:600;color:#2c3e50;margin-bottom:8px;font-size:14px;">⚖️ 多空辩论</div>'+
    '<div style="font-size:12px;color:#555;line-height:1.7;padding:10px 14px;background:#f8f9fa;border-radius:6px;white-space:pre-wrap;">'+result.debate+'</div></div>'+
    '<div><div style="font-weight:600;color:#2c3e50;margin-bottom:8px;font-size:14px;">👥 7 位分析师完整报告</div>'+analystHtml+'</div>'+
    '</div></details>';
  resultEl.style.display = 'block';
}
async function startAgentAnalysis(btn){
  const block = btn.parentElement;
  const resultEl = block.querySelector('.agent-result');
  if(!DEEPSEEK_API_KEY){
    resultEl.innerHTML = '<div style="padding:12px;color:#c0392b;font-size:13px;">⚠️ 未配置 DEEPSEEK_API_KEY，请在 .env 文件中配置后重新生成报告。</div>';
    resultEl.style.display = 'block'; return;
  }
  btn.disabled = true;
  btn.style.opacity = '0.7';
  btn.style.cursor = 'not-allowed';
  resultEl.style.display = 'block';
  resultEl.innerHTML = '<div style="padding:20px;text-align:center;color:#666;font-size:14px;line-height:2;">'+
    '<div style="font-size:24px;margin-bottom:8px;">🤖</div>'+
    '<div>多 Agent 辩论进行中...</div>'+
    '<div style="font-size:12px;color:#999;" id="agent-progress-detail">正在初始化...</div></div>';
  const progressDetail = document.getElementById('agent-progress-detail');
  const dataBrief = buildDataBrief(block);
  try{
    const analystReports = [];
    for(let i=0;i<AGENT_ROLES.length;i++){
      const role = Object.assign({},AGENT_ROLES[i],{index:i+1});
      const report = await runAnalyst(role,dataBrief,progressDetail);
      analystReports.push(report);
    }
    const debate = await bullBearDebate(analystReports,dataBrief,progressDetail);
    const decision = await finalDecision(analystReports,debate,dataBrief,progressDetail);
    const result = Object.assign({},decision,{analyst_reports:analystReports,debate:debate});
    renderAgentResult(resultEl,result);
    btn.style.display = 'none';
  }catch(e){
    resultEl.innerHTML = '<div style="padding:12px;color:#c0392b;font-size:13px;">⚠️ 分析失败: '+e.message+'</div>';
    btn.disabled = false;
    btn.style.opacity = '1';
    btn.style.cursor = 'pointer';
  }
}
</script>
"""


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
<script>const DEEPSEEK_API_KEY="{llm_config.DEEPSEEK_API_KEY}";</script>
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
{AGENTS_JS}
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
