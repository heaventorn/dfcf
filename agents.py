# -*- coding: utf-8 -*-
"""
多 Agent 个股深度分析模块
移植 TradingAgents-Astock 的多 Agent 辩论架构，复用 DFCF 现有数据源
7 分析师 → 多空辩论 → 最终评级
"""
import json
import os
import time
from datetime import datetime

import requests

import llm_config
import tech

# ============================================================
# LLM 封装
# ============================================================

class DeepSeekLLM:
    """DeepSeek API 调用封装"""

    def __init__(self, api_key=None, model=None, base_url=None):
        self.api_key = api_key or llm_config.DEEPSEEK_API_KEY
        self.model = model or llm_config.DEEPSEEK_MODEL
        self.base_url = base_url or llm_config.DEEPSEEK_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, system_prompt, user_prompt, temperature=0.7, max_tokens=None):
        """单次对话调用"""
        max_tokens = max_tokens or llm_config.AGENT_MAX_TOKENS
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"[LLM 调用失败: {e.__class__.__name__}: {e}]"


# ============================================================
# 数据获取
# ============================================================

def get_stock_data(tx_code, name):
    """获取个股分析所需数据，复用 DFCF 现有接口"""
    result = {"tx": tx_code, "name": name, "date": datetime.now().strftime("%Y-%m-%d")}

    # K线 + 技术指标（复用 tech.py）
    try:
        df = tech.fetch_kline(tx_code, 250)
        if df is not None and len(df) >= 60:
            df = tech.calc_indicators(df)
            latest = df.iloc[-1]
            result["price"] = round(float(latest["close"]), 2)
            result["change_pct"] = round(float(latest["pct_chg"]), 2) if "pct_chg" in df.columns else None
            result["ma20"] = round(float(latest["ma20"]), 2) if "ma20" in df.columns else None
            result["ma60"] = round(float(latest["ma60"]), 2) if "ma60" in df.columns else None
            result["ma250"] = round(float(latest["ma250"]), 2) if "ma250" in df.columns else None
            # 近20日高低点
            recent = df.tail(20)
            result["high_20d"] = round(float(recent["high"].max()), 2)
            result["low_20d"] = round(float(recent["low"].min()), 2)
            # 近60日涨跌幅
            if len(df) >= 60:
                price_60d = float(df.iloc[-60]["close"])
                result["change_60d_pct"] = round((result["price"] - price_60d) / price_60d * 100, 2)
            # 成交量趋势
            vol_5 = float(df.tail(5)["volume"].mean())
            vol_20 = float(df.tail(20)["volume"].mean())
            result["vol_ratio"] = round(vol_5 / vol_20, 2) if vol_20 > 0 else None
    except Exception as e:
        result["tech_error"] = str(e)

    return result


def format_data_brief(data):
    """把数据格式化成分析师可读的简要文本"""
    lines = [f"股票: {data['name']}({data['tx']})  日期: {data['date']}"]
    if "price" in data:
        lines.append(f"现价: {data['price']}  涨跌幅: {data.get('change_pct', 'N/A')}%")
        lines.append(f"MA20: {data.get('ma20', 'N/A')}  MA60: {data.get('ma60', 'N/A')}  MA250: {data.get('ma250', 'N/A')}")
        lines.append(f"20日最高: {data.get('high_20d', 'N/A')}  20日最低: {data.get('low_20d', 'N/A')}")
        if "change_60d_pct" in data:
            lines.append(f"60日涨跌幅: {data['change_60d_pct']}%")
        if data.get("vol_ratio"):
            lines.append(f"5日/20日量比: {data['vol_ratio']}")
    return "\n".join(lines)


# ============================================================
# 7 个分析师
# ============================================================

ANALYST_ROLES = [
    {
        "key": "market",
        "name": "市场分析师",
        "icon": "📊",
        "system": "你是一位 A 股市场技术分析专家，擅长 K线形态、均线系统、量价关系、趋势判断。请基于给出的技术数据，给出客观的技术面分析和多空判断。",
    },
    {
        "key": "social",
        "name": "舆情分析师",
        "icon": "💬",
        "system": "你是一位 A 股市场情绪与舆情分析专家，擅长判断散户情绪、市场热度、资金关注度。请基于给出的数据和当前市场环境，分析该股的舆情面和情绪面。",
    },
    {
        "key": "news",
        "name": "新闻分析师",
        "icon": "📰",
        "system": "你是一位 A 股新闻与事件驱动分析专家，擅长从行业新闻、公司公告、宏观事件中挖掘对个股的影响。请基于给出的信息，分析近期可能影响该股的新闻和事件。",
    },
    {
        "key": "fundamentals",
        "name": "基本面分析师",
        "icon": "📈",
        "system": "你是一位 A 股基本面分析专家，擅长财务分析、盈利能力评估、估值判断、行业地位分析。请基于给出的数据，分析该股的基本面状况和估值水平。",
    },
    {
        "key": "policy",
        "name": "政策分析师",
        "icon": "🏛️",
        "system": "你是一位 A 股政策分析专家，擅长判断监管政策、产业政策、窗口指导对个股和板块的影响。A 股是政策市，请重点分析政策面对该股的影响。",
    },
    {
        "key": "hot_money",
        "name": "游资追踪师",
        "icon": "🔥",
        "system": "你是一位 A 股游资与资金流向分析专家，擅长追踪龙虎榜、大单流向、主力资金动态。游资是 A 股短线定价的核心力量，请分析该股的资金面和游资参与度。",
    },
    {
        "key": "lockup",
        "name": "解禁监控师",
        "icon": "🔓",
        "system": "你是一位 A 股限售股解禁与减持监控专家，擅长分析限售股解禁、大股东减持、股权质押对股价的供给冲击。解禁是 A 股特有的重大供给冲击因素，请分析该股的解禁与减持风险。",
    },
]


def run_analyst(role, data_brief, llm):
    """运行单个分析师，返回报告文本"""
    user_prompt = f"""请分析以下股票数据，给出你的专业判断。要求：
1. 先给出你的核心结论（看多/看空/中性）
2. 然后给出 3-5 条具体分析依据
3. 最后给出风险提示
4. 总字数控制在 300 字以内，语言精炼专业

{data_brief}
"""
    report = llm.chat(role["system"], user_prompt, temperature=0.7)
    return report


# ============================================================
# 多空辩论 + 最终决策
# ============================================================

def bull_bear_debate(analyst_reports, data_brief, llm):
    """多空辩论：整合7份分析师报告，进行多空辩论"""
    reports_text = "\n\n".join([
        f"【{r['icon']}{r['name']}】\n{r['report']}"
        for r in analyst_reports
    ])
    system = "你是一位资深的 A 股投资辩论主持人，需要整合 7 位分析师的报告，组织一场多空辩论。请分别总结多方观点和空方观点，然后给出辩论后的倾向性判断。"
    user_prompt = f"""以下是 7 位分析师对同一只股票的分析报告：

{reports_text}

{data_brief}

请输出：
1. 【多方核心观点】（3条以内）
2. 【空方核心观点】（3条以内）
3. 【辩论结论】（多方占优/空方占优/势均力敌，一句话说明理由）
总字数控制在 400 字以内。
"""
    return llm.chat(system, user_prompt, temperature=0.6)


def final_decision(analyst_reports, debate, data_brief, llm):
    """最终决策：综合所有信息，给出评级和一句话理由"""
    reports_text = "\n".join([f"- {r['icon']}{r['name']}: {r['report'][:100]}..." for r in analyst_reports])
    system = "你是一位经验丰富的 A 股投资组合经理，需要综合 7 位分析师报告和多空辩论结果，给出最终投资评级。评级分为：强烈买入(BUY)、持有(HOLD)、卖出(SELL)三档。"
    user_prompt = f"""股票数据：
{data_brief}

分析师报告摘要：
{reports_text}

多空辩论结果：
{debate}

请输出严格的 JSON 格式（不要输出其他内容）：
{{
  "rating": "BUY" 或 "HOLD" 或 "SELL",
  "confidence": "高" 或 "中" 或 "低",
  "one_liner": "一句话理由（50字以内）",
  "key_reasons": ["理由1", "理由2", "理由3"],
  "risk_warning": "主要风险提示（30字以内）"
}}
"""
    result = llm.chat(system, user_prompt, temperature=0.3, max_tokens=1024)
    # 尝试解析 JSON
    try:
        # 提取 JSON 部分
        start = result.find("{")
        end = result.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(result[start:end])
    except Exception:
        pass
    # 解析失败，返回默认
    return {
        "rating": "HOLD",
        "confidence": "低",
        "one_liner": "多空因素交织，建议观望",
        "key_reasons": ["分析师观点分歧较大", "技术面与基本面信号不一致"],
        "risk_warning": "市场不确定性较高",
    }


# ============================================================
# 缓存
# ============================================================

def _cache_path(tx_code, date_str):
    """获取缓存文件路径"""
    llm_config.ensure_cache_dir()
    return os.path.join(llm_config.AGENT_CACHE_DIR, f"{tx_code}_{date_str}.json")


def _load_cache(tx_code, date_str):
    """加载缓存"""
    path = _cache_path(tx_code, date_str)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_cache(tx_code, date_str, result):
    """保存缓存"""
    path = _cache_path(tx_code, date_str)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
# 统一入口
# ============================================================

def analyze_stock(tx_code, name, use_cache=True):
    """
    对单只股票进行多 Agent 深度分析
    返回: {rating, confidence, one_liner, key_reasons, risk_warning, analyst_reports, debate, data}
    """
    if not llm_config.is_enabled():
        return None

    date_str = datetime.now().strftime("%Y%m%d")

    # 检查缓存
    if use_cache:
        cached = _load_cache(tx_code, date_str)
        if cached:
            return cached

    llm = DeepSeekLLM()

    # 1. 获取数据
    print(f"  [Agent] 获取 {name}({tx_code}) 数据...")
    data = get_stock_data(tx_code, name)
    data_brief = format_data_brief(data)

    # 2. 7 分析师
    print(f"  [Agent] 7 位分析师分析中...")
    analyst_reports = []
    for i, role in enumerate(ANALYST_ROLES):
        print(f"    [{i+1}/7] {role['icon']}{role['name']}...")
        report = run_analyst(role, data_brief, llm)
        analyst_reports.append({
            "key": role["key"],
            "name": role["name"],
            "icon": role["icon"],
            "report": report,
        })
        time.sleep(0.3)  # 避免限流

    # 3. 多空辩论
    print(f"  [Agent] 多空辩论中...")
    debate = bull_bear_debate(analyst_reports, data_brief, llm)
    time.sleep(0.3)

    # 4. 最终决策
    print(f"  [Agent] 最终决策中...")
    decision = final_decision(analyst_reports, debate, data_brief, llm)

    result = {
        "tx": tx_code,
        "name": name,
        "date": date_str,
        "rating": decision["rating"],
        "confidence": decision.get("confidence", "中"),
        "one_liner": decision.get("one_liner", ""),
        "key_reasons": decision.get("key_reasons", []),
        "risk_warning": decision.get("risk_warning", ""),
        "analyst_reports": analyst_reports,
        "debate": debate,
        "data": data,
    }

    # 保存缓存
    _save_cache(tx_code, date_str, result)

    return result


# ============================================================
# HTML 生成
# ============================================================

RATING_STYLE = {
    "BUY": {"color": "#e74c3c", "bg": "#fdecea", "label": "买入 BUY", "icon": "🟢"},
    "HOLD": {"color": "#f39c12", "bg": "#fef5e7", "label": "持有 HOLD", "icon": "🟡"},
    "SELL": {"color": "#27ae60", "bg": "#eafaf1", "label": "卖出 SELL", "icon": "🔴"},
}


def build_agent_card(result):
    """生成多 Agent 分析的 HTML 折叠卡"""
    if result is None:
        return ""

    rating = result.get("rating", "HOLD")
    style = RATING_STYLE.get(rating, RATING_STYLE["HOLD"])

    # 分析师报告 HTML
    analyst_html = ""
    for r in result.get("analyst_reports", []):
        report_text = r["report"].replace("\n", "<br>")
        analyst_html += f"""
        <div class="agent-report" style="margin:8px 0;padding:10px 14px;background:#f8f9fa;border-radius:6px;border-left:3px solid #4a6cf7;">
          <div style="font-weight:600;color:#2c3e50;margin-bottom:4px;">{r['icon']} {r['name']}</div>
          <div style="font-size:12px;color:#555;line-height:1.6;">{report_text}</div>
        </div>"""

    debate_text = result.get("debate", "").replace("\n", "<br>")
    reasons_html = "".join([f"<li>{r}</li>" for r in result.get("key_reasons", [])])

    card = f"""
  <details class="card agent-card" style="margin:12px 0;border:1px solid #e1e8ed;border-radius:8px;overflow:hidden;">
    <summary style="cursor:pointer;padding:14px 18px;background:linear-gradient(135deg,{style['bg']},#fff);font-weight:600;font-size:15px;list-style:none;">
      <span style="font-size:18px;margin-right:8px;">🤖</span>
      多Agent深度分析
      <span style="float:right;padding:3px 12px;border-radius:12px;background:{style['color']};color:#fff;font-size:12px;font-weight:500;">
        {style['icon']} {style['label']} · 置信度{result.get('confidence','中')}
      </span>
      <div style="font-weight:400;font-size:13px;color:#666;margin-top:4px;">{result.get('one_liner','')}</div>
    </summary>
    <div style="padding:16px 18px;background:#fff;">
      <div style="margin-bottom:14px;padding:12px 16px;background:{style['bg']};border-radius:6px;">
        <div style="font-weight:600;color:{style['color']};margin-bottom:6px;">核心逻辑</div>
        <ul style="margin:0;padding-left:20px;font-size:13px;color:#444;line-height:1.7;">
          {reasons_html}
        </ul>
        <div style="margin-top:8px;font-size:12px;color:#888;">⚠️ {result.get('risk_warning','')}</div>
      </div>
      <div style="margin-bottom:14px;">
        <div style="font-weight:600;color:#2c3e50;margin-bottom:8px;font-size:14px;">⚖️ 多空辩论</div>
        <div style="font-size:12px;color:#555;line-height:1.7;padding:10px 14px;background:#f8f9fa;border-radius:6px;">{debate_text}</div>
      </div>
      <div>
        <div style="font-weight:600;color:#2c3e50;margin-bottom:8px;font-size:14px;">👥 7 位分析师完整报告</div>
        {analyst_html}
      </div>
    </div>
  </details>"""

    return card


def analyze_positions(positions, use_cache=True):
    """
    对持仓列表中的所有股票进行多 Agent 分析
    positions: [{"tx": "sz000519", "name": "中兵红箭", ...}, ...]
    返回 HTML 片段
    """
    if not llm_config.is_enabled():
        return '<div class="hint" style="margin:10px 0;color:#888;font-size:12px;">🤖 多Agent分析未启用（配置 DEEPSEEK_API_KEY 后开启）</div>'

    cards = ""
    for pos in positions:
        tx = pos.get("tx", "")
        name = pos.get("name", "")
        if not tx:
            continue
        print(f"\n[Agent] 分析 {name}({tx})...")
        try:
            result = analyze_stock(tx, name, use_cache=use_cache)
            if result:
                cards += build_agent_card(result)
        except Exception as e:
            print(f"  [Agent] {name} 分析失败: {e}")
            cards += f'<div class="hint" style="margin:10px 0;color:#c0392b;font-size:12px;">🤖 {name} 多Agent分析失败（{e.__class__.__name__}）</div>'

    return cards
