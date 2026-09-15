# 东方财富 · 当日市场信息爬虫

自动登录东方财富账号，抓取当天 A 股市场信息，并自动生成**「3D 地球指挥台」主页**（单文件自包含 HTML：中间 3D 地球 + 左侧行情/新闻日历 + 右侧持仓/风险指数）。

## 功能

- **登录**：Playwright 弹出浏览器窗口，用「东方财富 App」扫码登录，会话 Cookie 本地持久化；登录失效后自动重新引导
- **多源自动切换**（核心特性）：每种数据配置多个数据源，某个来源被风控/限流（断连、空数据、结构异常、风控页）时，自动切换到下一可用来源；异常来源进入冷却期并自动恢复，运行结束打印各来源健康状态
- **采集**（当日实时数据）：
  - 主要指数：上证、深证成指、创业板指、科创50、北证50、沪深300
  - 市场广度：全市场涨跌家数、涨停/跌停家数、高位连板
  - 板块表现：行业板块涨幅榜、概念板块涨幅榜
  - 个股表现：涨幅榜 Top10
- **分红记录取数**：天天基金 F10 分红送配（`sources.get_dividends`）；原先基于它的红利 ETF「股息率分位 / 买卖点」分析模块已随旧报告链路移除
- **罗力豪空中飞人指数**（主页右下方窗口）：泡沫爆破风险指数（0-100，越高越危险），五维评分（估值 20% / 盈利证伪 15% / 杠杆 15% / 流动性利率 25% / 政治宏观 25%），全部指标自动抓取打分
- **主页（3D 地球指挥台）**：中间 3D 地球（宏观事件光点 / 国界 / 洲际导航）+ 左侧「大A行情概况 + 白底可缩放行情图 / 新闻·日历」+ 右侧「我的持仓 + 我的自选 / 空中飞人指数」，生成**单文件自包含 HTML**（`output/index.html`，离线可开）
- **实时新闻推送**（`live_server.py`）：脚本启动后自动在后台拉起，**每 5 分钟**重抓一次全球新闻（东财 7x24 / 华尔街见闻 / 财联社 / 金十 / 同花顺 / 新浪 7x24）；主页按版本号轮询 `/api/news`，有新内容就**增量刷新地球光点与新闻栏**（不整页重载、不重新下载贴图）；关掉启动脚本窗口时服务随之退出
- **新闻可跳原文**：新闻栏与详情面板里的标题都是链接（新窗口打开原站），六个信源在抓取时都保留了原文地址；点条目的**其余位置**仍是原有的「飞向地球光点 + 展开详情」，互不干扰
- **我的自选**（主页右上方窗口）：自己维护的标的清单，分 **ETF / 股票 / 其他** 三组（存 `watchlist.json`）。**只跟行情——不记成本、不算盈亏**（那是「我的持仓」的事）。在持仓管理页 `http://127.0.0.1:8765` 的「📌 自选管理」标签填个代码就能加入；场内 ETF/股票取实时价，场外基金取净值（带净值日），国债逆回购显示的是年化利率
- **个股终端**（`stock.py` + `stock.html`）：主页里点持仓/自选任意一行 → **同一个窗口**进入个股页（详细报价 / 五档盘口 / 分时含均价 / 日K / 月K / 均线·BOLL 指标切换 / 量能 / MACD），返回键或 Esc 回主页。后台按 **3.5s / 60s / 300s** 分档刷新，**同一只票合并请求**（多开标签页也只打一次上游），**收盘、午休、周末自动停刷**，页面关掉 60 秒后连后台也不再为它抓数

## 环境要求

- Windows / macOS / Linux
- Python 3.12（实测 3.12.10；3.10+ 基本可用）
- 依赖：见 `requirements.txt`（`requests`、`pandas`、`numpy`、`playwright`、`argon2-cffi`；`Pillow` 可选，装了会给主页内嵌贴图降采样）

## 安装

```bash
pip install -r requirements.txt
```

Playwright 浏览器下载较慢时，**推荐使用国内镜像（npmmirror）**：

```bash
# Windows PowerShell
$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright/"
python -m playwright install chromium
```

```bash
# macOS / Linux
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ python -m playwright install chromium
```

## 使用

```bash
# 1. 完整流程（自动登录 + 采集 + 生成 HTML 报告）
python main.py

# 2. 仅登录（保存会话，之后可跳过重复扫码）
python main.py --login-only

# 3. 跳过登录，直接用公开接口采集（行情数据本就无需登录）
python main.py --no-login

# 4. 查看历史快照趋势 / 环比（需先采集过至少两轮）
python history.py
python history.py --days 30 --field breadth_up   # 某指标时间序列
```

首次运行 `python main.py` 会弹出浏览器窗口，用「东方财富 App」扫一扫登录页面左侧二维码即可。
登录成功后 Cookie 保存到 `cookies.json`，下次运行自动复用，无需重复扫码。

### 手动粘贴 Cookie（备用登录方式）

如果扫码登录不方便，可手动粘贴：

```bash
python -c "import login; login.manual_login()"
```

从已登录东财的浏览器按 F12 → Network，复制任意请求头里的 Cookie 字符串粘贴即可。

## 输出

运行后生成到 `output/` 目录：

| 文件 | 说明 |
| --- | --- |
| `index.html` | **主页（3D 地球指挥台）**：单文件自包含；建议经本地服务打开 http://127.0.0.1:8766/output/index.html |
| `latest_market.json` | 当日采集的原始数据（结构化，方便二次处理） |
| `history.db` | **历史快照库**（SQLite，每轮一条）；趋势 / 环比查询：`python history.py` |
| `live_news.json` | 实时新闻服务最近一次抓取结果（调试 / 离线查看，由 `live_server.py` 写入） |

## 罗力豪空中飞人指数

泡沫爆破 / 经济危机风险指数，**0-100 分，越高越危险**。评分标准**固定**，全部指标由公开数据自动采集、按固定阈值打分，**无需人工填分**。

- **分级**：0-25 安全区 | 26-50 警戒区 | 51-75 高危区 | 76-100 爆破临界区
- **五维权重（固定）**：估值·市场过热 20% / 盈利·成本挤压 15% / 杠杆·财政债务 15% / 流动性·利率 25% / 政治·宏观 25%
- **15 个计分子项（全部自动）**：
  - 估值：标普500距52周高点、纳指距52周高点、VIX 自满度
  - 盈利/成本：核心CPI同比、原油价格
  - 杠杆/财政：美国债务/GDP、债务总额同比、联邦基金利率
  - 流动性/利率：10Y美债、2Y美债、USD/JPY、收益率曲线(10Y-2Y)
  - 政治/宏观：政策不确定性指数、美元广义指数、30Y美债
- **参考指标（不计分，独立展示）**：恐慌指数 VIX、黄金价格（含注释）
- **数据源**：FRED（美债/通胀/VIX/美元/债务/政策不确定性等）、腾讯（黄金/原油）、新浪（USD/JPY）
- 阈值规则定义在 `airman.py` 的 `_rule_*` 函数中（固定标准，如需调整改代码即可）
- 每次运行实时抓取并打分，输出总分 + 五维得分条 + 已计分信号清单 + 参考指标，渲染在主页右下方窗口

## 目录结构

```
dfcf-main/
├── main.py                  # 主入口（登录 → 采集 → 落库 → 生成主页 → 拉起服务）
├── login.py                 # 登录模块（扫码 / 手动 Cookie）
├── sources.py               # 多数据源适配层（异常检测 + 自动切换 + 冷却恢复 + 连接级重试）
├── collector.py             # 数据采集模块（调用 sources 多源取数）
├── history.py               # 历史快照存储 / 查询（SQLite；python history.py）
├── airman.py                # 罗力豪空中飞人指数模块（固定评分标准，全自动）
├── home.py                  # 主页数据聚合与页面生成
├── events.py                # 全球宏观事件采集 + 地球页资源
├── geo.py / feeds.py        # 地名归因 / 快讯源
├── portfolio.py             # 持仓 / 自选行情采集（positions.json / watchlist.json）
├── kchart.py                # K线（日/周/月，前复权）与分时取数 + MA/BOLL/MACD 指标
├── stock.py                 # 个股终端数据层 + StockHub 后台分档刷新（含离线自检 CLI）
├── stock.html               # 个股终端页面（分时 / 日K / 月K + 指标切换 + 五档盘口）
├── live_server.py           # 常驻服务（127.0.0.1:8766：主页静态发布 + /api/news + /api/stock + /stock 个股页）
├── position_manager.py      # 持仓 / 自选管理本地服务（127.0.0.1:8765）
├── positions.json           # 我的持仓（代码 / 成本 / 数量；用来算市值与盈亏）
├── watchlist.json           # 我的自选（只跟行情、不记成本；ETF / 股票 / 其他 三组）
├── config.py                # 配置（指数、板块、多源冷却、网络重试、Cookie 路径等）
├── utils.py                 # 公共工具（格式化 / 类型转换 / 市场情绪判断）
├── requirements.txt         # 依赖清单
├── test_sources.py          # 多源适配层冒烟测试（联网；python test_sources.py）
└── output/                  # 输出目录（自动创建）
```

## 多数据源机制

| 数据类型 | 源1（首选） | 源2 | 源3 | 源4 |
| --- | --- | --- | --- | --- |
| 指数行情 | 东财 push2 | 东财 push2delay 镜像 | 腾讯 qt.gtimg | 新浪 hq.sinajs |
| 个股涨/跌幅榜 | 东财 clist | push2delay | 新浪 getHQNodeData | 腾讯榜单（仅成交额） |
| 板块涨跌榜 | 东财 clist | push2delay | — | — |
| 全市场涨跌家数 | 东财 clist 分页 | push2delay | 新浪分页 | — |
| 涨停/跌停池 | 东财 push2ex | —（无镜像，失败时如实置空） | | |
| 财经快讯 | 东财 newsapi | 新浪 7x24 | | |
| 历史K线 | 腾讯 ifzq | 东财 push2his | 新浪 | |
| 实时行情（ETF） | 腾讯 qt.gtimg | 新浪 hq.sinajs | | |
| 个股详细报价（含五档 / 市值 / 涨跌停 / 均价） | 腾讯 qt.gtimg | 东财 push2（多盘后固定价格） | | |
| 个股当日分时（含均价） | 东财 trends2 | 腾讯 ifzq minute | | |
| 个股前复权K线（日/周/月） | 腾讯 ifzq | 东财 push2his | | |
| 分红记录 | 天天基金 fundf10 | （偶发空数据自动重试 3 次） | | |

**三层容错**（由内到外）：
1. **连接级重试**：断连 / 超时 / 5xx / 429 自动退避重试（`config.HTTP_RETRIES`）；
2. **来源切换**：硬异常（断连 / 非 200 / 风控页）指数退避冷却并切源；软异常（空数据 /
   校验不过）只切源、不冷却；
3. **整体重试**：所有来源都失败时按 `config.COLLECT_RETRIES` 整链重试。

**风控判定分档**：中文强特征（“访问过于频繁”等）任何响应都判；英文宽泛词
（verify / captcha ...）只在 HTML 响应或 JSON 解析失败时判 —— 避免正常数据正文里
出现这类词被误杀成风控页。

**代码缺陷可见**：非数据源异常（KeyError / AttributeError 等）不再被静默当作
「来源故障」吞掉，而是打印 traceback、计入运行结束的健康报告，且不计入来源冷却。

**代理支持**：读取 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（可在 `config.USE_ENV_PROXY`
关掉强制直连）。

**冷却恢复**：硬异常来源按连续失败次数指数退避（15s→30s→60s…，上限 10 分钟），
冷却结束后自动恢复探测，无需人工干预。

## 常见问题

- **部分数据为空 / 接口超时**：程序已内置多源自动切换——东财 push2 被限流时自动改用
  push2delay 延迟镜像 / 腾讯 / 新浪；若多个数据源均异常才可能为空，稍等 15~30 分钟重跑即可。
- **登录后 Cookie 失效**：Cookie 有时效，失效时删除 `cookies.json` 后重新执行 `python main.py` 即可重新扫码。
- **浏览器未弹出**：确认已执行 `python -m playwright install chromium`（建议用国内镜像）。
- **新闻没在动 / 看不到自动刷新**：确认 8766 端口上跑的是 `live_server.py`。
  浏览器访问 http://127.0.0.1:8766/api/health 应返回 JSON；
  若 404 说明有别的程序占着端口，关掉旧窗口重跑启动脚本即可。
- **主页点持仓行没反应 / 个股页打不开**：主页是生成产物，改动代码后要重跑一次
  `启动爬虫.bat`；个股页需要 8766 服务在跑（`/api/stock`）。
- **个股页某块显示「—」**：那一块数据源降级了，看 `/api/stock/health` 的 `errors`
  与 `/api/health`；逆回购 / 场外基金本身没有分时与 K 线，主页里也不给入口。

## 版本与近期变更

当前版本 **v3.2.0**：三块功能（主页 3D 地球指挥台 / 个股终端 / 持仓·自选管理）+ 一条数据链，
常驻服务是**一个进程托管两个端口**（8766 主页·新闻·个股；8765 持仓自选）。

- **v3.2.0 个股终端**：新增 `stock.py`（数据层 + `StockHub` 分档刷新 + 离线自检）与
  `stock.html`（分时 / 日K / 月K + 均线·BOLL 切换 + 量能 + MACD + 五档盘口）；
  主页持仓/自选点行进个股页（同窗口，返回键或 Esc 回主页）；`live_server.py` 新增
  `/api/stock`、`/api/stock/health`、`/stock` 路由与 `--with-positions`
- **v3.1.x**：我的自选（`watchlist.json` + 管理页「自选管理」标签）、新闻标题可跳原文、
  主页与地球页深色设计令牌统一、贴图内嵌 data URL（`file://` 直开不再黑球）
- **v3.1.0**：`live_server.py` 实时新闻推送（每 5 分钟增量刷新）、`history.py` 历史快照
- **v3.0.0**：3D 地球指挥台主页 + 多信源 + 交互式行情图

**代码清理（2026-09）**：删掉所有已无调用者的链路 ——
旧「家庭投资组合」写死清单（固收 / 高成长 / 纳指标普 ETF，以及它们每轮白跑的采集）、
`agents.py` + `llm_config.py`（多 Agent 辩论，从未启用）、`events.build_detail_page`、
`sources.apply_cookies` / `get_us_quotes`、`kchart` 的 matplotlib 出图链
（连带去掉 matplotlib 依赖）、`portfolio.generate_report_section` / `generate_report` /
`save_report` 及那份 HTML 报告片段、`serve_page.py`、`globe_proto.html`、
`static_assets.py`、`migrate_inline_assets.py`、三个 `verify_p*.py`（写死了原作者机器路径，
在本机跑不起来）与 715KB 无人引用的 `assets/earth-night.jpg`。

自检入口（都不动正式产物）：

```bash
py -3.12 stock.py              # 个股数据层离线自检
py -3.12 stock.py --hub-test   # 后台刷新四条不变量
py -3.12 home.py --probe       # 主页探针 → output/_home_probe.html
py -3.12 test_sources.py       # 数据源冒烟（联网）
```

## 合规与免责

- 本工具仅用于个人学习与研究，请勿用于商业用途或对东财服务器造成过大压力（已内置限速）。
- 请遵守东方财富网站服务条款，控制请求频率。
- 程序自动抓取公开行情数据并归纳生成报告，仅供参考，不构成任何投资建议。
