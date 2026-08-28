# -*- coding: utf-8 -*-
"""公共工具模块
================
集中各模块重复的格式化 / 类型转换 / 简单网络请求等小函数，
避免同样的代码在多个文件里复制粘贴（"屎山"的常见来源之一）。
"""

import requests

import config


def to_float(v):
    """安全转 float；空 / 停牌（"-"）/ 非法值返回 None。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_price(v, digits=2):
    """价格 / 金额格式化（保留 digits 位小数）；空值 / 非法值返回 "-"。"""
    if v is None:
        return "-"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def fmt_pct(v, signed=True):
    """百分比格式化，默认带正负号（如 +1.23%）；空值 / 非法值返回 "-"。"""
    if v is None:
        return "-"
    try:
        v = float(v)
        return f"{v:+.2f}%" if signed else f"{v:.2f}%"
    except (TypeError, ValueError):
        return "-"


def http_get(url, headers=None, timeout=12):
    """简单 GET 请求（带默认 UA），返回 Response；调用方自行处理 .text / .json()。"""
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if headers:
        h.update(headers)
    return requests.get(url, headers=h, timeout=timeout)
