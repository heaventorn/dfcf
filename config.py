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

# 采集项整体重试次数（collector._retry_on_empty 使用：结果为空时整链重试）
COLLECT_RETRIES = 3

# 全市场涨跌家数分页补拉轮数（sources._em_breadth 使用）
BREADTH_REFILL_ROUNDS = 3

# 请求超时（秒）
TIMEOUT = 15

# ---- 网络层（sources._mk_session 使用）----
# 连接级重试：断连 / 超时 / 5xx / 429 自动退避重试，吸收网络偶发抖动
HTTP_RETRIES = 2
# 退避因子（urllib3 backoff_factor）：重试间隔 0.5s / 1s ...
HTTP_BACKOFF = 0.5
# 连接池大小
HTTP_POOL_SIZE = 8
# 是否读取环境代理（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY）；False = 强制直连
USE_ENV_PROXY = True

# ---- 多数据源自动切换（sources.py 使用）----
# 来源「硬异常」（断连/非200/风控页，多为被限流）后的冷却基础秒数（按连续失败指数退避：15s, 30s, 60s ...）
SOURCE_COOLDOWN_BASE = 15
# 冷却上限（秒），避免长时间彻底不用某个来源
SOURCE_COOLDOWN_MAX = 600
# 切换来源间的最小停顿（秒），避免对下一个来源请求过急
SOURCE_SWITCH_DELAY = 0.4
# 全部来源失败后、整体重试前的等待（秒）
SOURCE_ALLFAIL_DELAY = 3.0

# ---- 实时新闻服务（live_server.py 使用）----
# 后台自动抓取全球新闻的间隔（秒）；300 = 5 分钟
NEWS_REFRESH_SECONDS = 300
# 只保留「当天」（自然日 0 点起）的新闻；False 则退回 NEWS_WINDOW_DAYS 的滚动窗口
NEWS_TODAY_ONLY = True
# 新闻时间窗（天）：仅 NEWS_TODAY_ONLY=False 时生效（events.WINDOW_DAYS 与页面文案都读它）
NEWS_WINDOW_DAYS = 7
# 各信源抓取深度（页数 / 条数）：保证白天新闻量大时「当天」条数不被页数截断
NEWS_EM_PAGES = 20          # 东财 7x24 翻页上限（每页 100 条）
NEWS_THS_LIMIT = 400        # 同花顺条数上限
NEWS_THS_PAGES = 8          # 同花顺翻页上限（每页 100 条）
NEWS_CLS_LIMIT = 150        # 财联社条数上限
NEWS_JIN10_LIMIT = 150      # 金十条数上限
NEWS_WALLSTCN_LIMIT = 300   # 华尔街见闻条数上限
NEWS_SINA_LIMIT = 500       # 新浪财经 7x24 条数上限
NEWS_SINA_PAGES = 6         # 新浪财经 7x24 翻页上限（每页 100 条）
# 一轮保留的最大事件条数。注意它是「按时间倒序取前 N 条」，太小会把时间窗
# 反向截短（例如 3 天窗 + 300 条，实测只覆盖约 12 小时）。
NEWS_LIMIT = 1000
# 主页新闻栏渲染的条数（滚动区，太多会拖慢首屏与刷新）
NEWS_FEED_ROWS = 300
# 前端轮询 /api/news 的间隔（秒）；后端按版本号比对，无变化时只回一个空响应
NEWS_POLL_SECONDS = 60
# 实时新闻服务端口（与主页静态服务同端口：静态文件 + /api/news 同源）
LIVE_PORT = 8766

# 指数列表字段
INDEX_FIELDS = "f2,f3,f4,f6,f12,f14"

# 股票列表字段
STOCK_FIELDS = "f2,f3,f12,f14,f62"

# 行业/概念板块字段
SECTOR_FIELDS = "f3,f12,f14,f62,f104,f105,f128"
