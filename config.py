# -*- coding: utf-8 -*-
"""东方财富爬虫 - 配置项"""

import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Cookie 持久化文件
COOKIE_FILE = os.path.join(BASE_DIR, "cookies.json")

# 输出目录
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# 请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://quote.eastmoney.com/",
}

# 主要指数 secid（沪市=1.xxx，深市=0.xxx）
INDEX_SECIDS = [
    ("1.000001", "上证指数"),
    ("0.399001", "深证成指"),
    ("0.399006", "创业板指"),
    ("1.000688", "科创50"),
    ("0.899050", "北证50"),
    ("1.000300", "沪深300"),
]

# 全市场 A 股板块筛选条件（沪深京）
ALL_A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# 单页大小（东财 clist 接口单页上限约 100）
PAGE_SIZE = 100

# 并行抓取线程数（东财对高频请求限流，保持偏低以降低触发概率）
WORKERS = 2

# 单页请求间隔（秒）
PAGE_DELAY = 0.3

# 单页重试次数
RETRIES = 4

# 请求超时（秒）
TIMEOUT = 15

# ---- 多数据源自动切换（sources.py 使用）----
# 来源「硬异常」（断连/非200/风控页，多为被限流）后的冷却基础秒数（按连续失败指数退避：15s, 30s, 60s ...）
SOURCE_COOLDOWN_BASE = 15
# 冷却上限（秒），避免长时间彻底不用某个来源
SOURCE_COOLDOWN_MAX = 600
# 切换来源间的最小停顿（秒），避免对下一个来源请求过急
SOURCE_SWITCH_DELAY = 0.4
# 全部来源失败后、整体重试前的等待（秒）
SOURCE_ALLFAIL_DELAY = 3.0

# 指数列表字段
INDEX_FIELDS = "f2,f3,f4,f6,f12,f14"

# 股票列表字段
STOCK_FIELDS = "f2,f3,f12,f14,f62"

# 行业/概念板块字段
SECTOR_FIELDS = "f3,f12,f14,f62,f104,f105,f128"
