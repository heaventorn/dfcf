# -*- coding: utf-8 -*-
"""公共工具模块
================
集中各模块重复的格式化 / 类型转换 / 简单网络请求等小函数，
避免同样的代码在多个文件里复制粘贴（"屎山"的常见来源之一）。
"""

import requests


def to_float(v):
    """安全转 float；空 / 停牌（"-"）/ 非法值返回 None。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def judge_market(breadth, indices):
    """基于市场广度与指数表现，规则化判断当日市场情绪。返回 (情绪标签, 说明)。

    原先定义在 html_report.py（旧「综合报告」模块）。该模块其余内容已无调用者
    （main.py 现在只产出主页 index.html），因此只把这个仍被 home.py 使用的
    判断函数保留下来，放到公共工具模块里。
    """
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


def http_get(url, headers=None, timeout=12):
    """简单 GET 请求（带默认 UA），返回 Response；调用方自行处理 .text / .json()。"""
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=timeout)
