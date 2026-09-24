# -*- coding: utf-8 -*-
"""东方财富爬虫 - 配置项"""

import os
import shutil

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ 数据目录
# 分工（方案 B：本地数据统一收进一个目录，整目录不入库）：
#   data/   你手动维护的数据 + 凭据（持仓 / 自选 / 策略蓝图 / cookies / pwd.key / secrets.json）
#   output/ 程序生成、删掉能重算的缓存与报告
# 为什么分开：data/ 是「丢了就没了」的东西，必须挡住误上传；output/ 是产物，丢了重跑即可。
#
# 迁移与播种（进程启动时自动做一次，幂等）：
#   1) 项目根目录若还留有同名旧文件（positions.json / strategy.json / ...），搬进 data/
#   2) data/ 里缺 positions.json / watchlist.json / strategy.json 时，用 data_templates/ 里的
#      *.example.json 播种 —— 全新克隆下来也能直接跑，不用手工建文件
DATA_DIR = os.path.join(BASE_DIR, "data")
TEMPLATE_DIR = os.path.join(BASE_DIR, "data_templates")

_MOVE_NAMES = ("cookies.json", "pwd.key", "secrets.json", "positions.json",
               "watchlist.json", "strategy.json")
_SEED_NAMES = ("positions.json", "watchlist.json", "strategy.json")

_data_ready = False


def ensure_data_dir():
    """建好 data/，把根目录旧文件搬进来，缺的文件用模板播种。可重复调用。"""
    global _data_ready
    if _data_ready:
        return DATA_DIR
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        return DATA_DIR
    for name in _MOVE_NAMES:
        src = os.path.join(BASE_DIR, name)
        dst = os.path.join(DATA_DIR, name)
        if not os.path.exists(src):
            continue
        if _present(dst):
            # 两边都有：根目录那份更新，说明是「迁移之后、旧进程还没重启时写下的」，
            # 以它为准，data/ 里那份先留成 .bak，免得两头不一致把最新一笔吃掉。
            cur = dst if os.path.exists(dst) else dst + ".enc"
            if not _newer(src, cur):
                continue
            try:
                shutil.move(cur, cur + ".bak")
            except Exception:
                pass
        try:
            os.replace(src, dst)
        except OSError:
            try:
                shutil.move(src, dst)
            except Exception:
                pass
    for name in _SEED_NAMES:
        dst = os.path.join(DATA_DIR, name)
        tpl = os.path.join(TEMPLATE_DIR, name.replace(".json", ".example.json"))
        if not _present(dst) and os.path.exists(tpl):
            try:
                shutil.copyfile(tpl, dst)
            except Exception:
                pass
    _data_ready = True
    return DATA_DIR


def _newer(a, b):
    """a 的修改时间是否比 b 新（取不到就当不比它新）。"""
    try:
        return os.path.getmtime(a) > os.path.getmtime(b)
    except OSError:
        return False


def _present(path):
    """data/ 里这份数据在不在：明文或密文（*.enc）任一存在都算。

    加密后明文会被删掉、只剩 positions.json.enc，所以「缺文件就播种」必须先看密文，
    否则每次启动都会用模板把已加密的持仓盖掉。
    """
    return os.path.exists(path) or os.path.exists(path + ".enc")


ensure_data_dir()

# Cookie 持久化文件
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.json")
# 本机强密码文件（含二级密码明文，绝不入库）
PWD_KEY_FILE = os.path.join(DATA_DIR, "pwd.key")
# DeepSeek / 大模型 Key
SECRETS_FILE = os.path.join(DATA_DIR, "secrets.json")
# 账本：我实际持有什么
POS_FILE = os.path.join(DATA_DIR, "positions.json")
# 自选：只跟行情、不记成本
WATCH_FILE = os.path.join(DATA_DIR, "watchlist.json")
# 策略蓝图：目标权重 / 再平衡 / 趋势闸
STRATEGY_FILE = os.path.join(DATA_DIR, "strategy.json")
# Edge --app 独立窗口的专用 profile（main.py 用）。
# 留在项目根目录：浏览器进程长时间持有它，活着的 profile 不适合搬家，
# 而且它是缓存不是数据。.gitignore 已排除。
EDGE_PROFILE_DIR = os.path.join(BASE_DIR, ".edge_profile")

# 输出目录
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# ------------------------------------------------------------------ 加密保险库
# secure_store.py 用：下面这些文件落盘一律是密文（同名 + ".enc"），明文只在内存里。
# data/ 目录下所有 *.json 也会被自动纳入（以后新加的数据文件不用再登记）。
VAULT_KEYRING_FILE = os.path.join(DATA_DIR, "keyring.json")
SECURE_FILES = (
    POS_FILE, WATCH_FILE, STRATEGY_FILE, COOKIE_FILE, SECRETS_FILE,
    os.path.join(OUTPUT_DIR, "ledger.json"),          # 现金流台账（多少钱进出）
    os.path.join(OUTPUT_DIR, "journal.jsonl"),        # 操作日志
    os.path.join(OUTPUT_DIR, "journal_state.json"),
    os.path.join(OUTPUT_DIR, "plan_history.jsonl"),   # 执行过的买卖计划
    os.path.join(OUTPUT_DIR, "track.json"),           # 盈亏追踪
    os.path.join(OUTPUT_DIR, "track.bak.json"),
    os.path.join(OUTPUT_DIR, "monitor.json"),
    os.path.join(OUTPUT_DIR, "holding_eval.json"),
)

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

# ---- 涨跌家数：一条请求拿到全市场（sources._em_breadth_fast 使用）----
# 原理：市场指数行情里的 f104/f105/f106 就是该指数的「涨/跌/平家数」。
# 只登记「市场指数」（沪市 / 深市 / 北交所），不要把沪深300、科创50 这类
# 成分指数混进来 —— 那种指数返回的是成分股家数，会把总数算错。
BREADTH_SECIDS = [
    ("1.000001", "沪市"),
    ("0.399001", "深市"),
    ("0.899050", "北交所"),
]

# 涨跌家数字段：f104=上涨家数 f105=下跌家数 f106=平盘家数
BREADTH_FIELDS = "f12,f14,f104,f105,f106"

# 是否保留「逐页爬全市场」的深度兜底（原实现，约 56 次请求）。
# 默认关闭：它是把自己打成限流的主要原因，快线（上面那条）失败时
# 优先用新浪分页兜底，没必要再回头去撞同一个接口。
BREADTH_DEEP_FALLBACK = False

# 新浪分页兜底取前几页当作涨跌停池（sources._sina_limit_pool 使用）。
# 接口按涨跌幅排序，涨/跌停都堆在最前面，两页（200 只）足够覆盖。
LIMIT_POOL_SINA_PAGES = 2

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

# 东财直连源（em / emdelay，host = push2.eastmoney.com / push2delay.eastmoney.com）
# 是否仍排在各路取数的第一位。
# 实测（2026-09-24）：本机网络访问这两个域名的 /api/... 接口会被服务器直接断开，
# 连真实浏览器打开同一个接口也一样（ERR_EMPTY_RESPONSE），属于线路/IP 级拦截，
# 不是代码问题。而新浪 / 腾讯 / push2ex（涨停池）都正常。
# 排在最前面＝每次取数都要先白等一两秒才切源，所以默认 False：
# 把 em 一族挪到列表末尾，只在别的源都不行时再试它。
# 哪天这条线路恢复（比如换了网络 / 换了 IP），把这里改成 True 就回到原来的优先级。
EM_FIRST = False

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
# 过滤广告 / 平台活动推广（例如「…马上参与CPI行情竞猜，赢现金红包大奖！」）
# 判定采用「明确推广词」或「行动号召 + 奖励承诺」组合，故意保守：
# 「开户 / 优惠 / 预约 / 投票 / 活动」这类词在正常财经与社会新闻里太常见，
# 不能当广告特征（实测会把「两融新开户」「熊猫参观预约」「机构采购优惠」误杀）。
NEWS_AD_FILTER = True
# 一轮保留的最大事件条数。注意它是「按时间倒序取前 N 条」，太小会把时间窗
# 反向截短（例如 3 天窗 + 300 条，实测只覆盖约 12 小时）。
NEWS_LIMIT = 1000
# 主页新闻栏渲染的条数（滚动区，太多会拖慢首屏与刷新）
NEWS_FEED_ROWS = 300
# 前端轮询 /api/news 的间隔（秒）；后端按版本号比对，无变化时只回一个空响应
NEWS_POLL_SECONDS = 60
# 实时新闻服务端口（与主页静态服务同端口：静态文件 + /api/news 同源）
LIVE_PORT = 8766

# ---- 个股终端（stock.py 数据层 / 后续的 stock 页与后台刷新）----
# 刷新档位：盘口/现价 3.5 秒 —— 比新闻的 5 分钟高两个数量级，因此后台必须做
# 「同一只票 X 秒内只打一次上游」的合并，否则多开几个标签页就把上游打爆。
STOCK_QUOTE_POLL = 3.5        # 盘口 / 现价
STOCK_INTRADAY_POLL = 60      # 分时 + 均价（东财 trends2 是分钟粒度，不必更快）
STOCK_KLINE_POLL = 300        # 日K / 月K（前复权；缓存按日失效，见 kchart.fetch_kline 注释）
STOCK_FX_POLL = 3600          # 汇率（H股 / A-H 比价用，阶段 2）
# 默认取多少根K线：月K要够长，否则 MA / BOLL 全是空值
STOCK_KLINE_BARS = 180
STOCK_MONTH_BARS = 120
# 非交易时段（收盘 / 午休 / 周末）是否停掉前端定时器：只保留最后一份收盘数据
STOCK_STOP_WHEN_CLOSED = True
# 每只票最多同时缓存多少只（StockHub 用，防止开一堆标签页把内存撑爆）
STOCK_CACHE_MAX = 30
# 订阅有效期（秒）：页面最后一次读取超过这个时间就认为「没人看了」，
# 后台不再为它抓上游 —— 否则关掉页面之后服务会一直刷到收盘
STOCK_SUBSCRIBE_TTL = 60

# 指数列表字段
INDEX_FIELDS = "f2,f3,f4,f6,f12,f14"

# 股票列表字段
STOCK_FIELDS = "f2,f3,f12,f14,f62"

# 行业/概念板块字段
SECTOR_FIELDS = "f3,f12,f14,f62,f104,f105,f128"
