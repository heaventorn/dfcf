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
- **主页（3D 地球指挥台）**：中间 3D 地球（宏观事件光点 / 国界 / 洲际导航）+ 左侧「大A行情概况 + 白底可缩放行情图 / 新闻·日历」+ 右侧「我的持仓 + 配置标的行情 / 空中飞人指数」，生成**单文件自包含 HTML**（`output/index.html`，离线可开）
- **实时新闻推送**（`live_server.py`）：脚本启动后自动在后台拉起，**每 5 分钟**重抓一次全球新闻（东财 7x24 / 华尔街见闻 / 财联社 / 金十 / 同花顺）；主页按版本号轮询 `/api/news`，有新内容就**增量刷新地球光点与新闻栏**（不整页重载、不重新下载贴图）；关掉启动脚本窗口时服务随之退出
- **个人组合监控**：覆盖家庭组合三类资产——低风险固收（货币/国债/短债ETF + 国债逆回购）、红利低波 ETF、美股高成长（英伟达/谷歌）

## 环境要求

- Windows / macOS / Linux
- Python 3.9+（开发环境为 3.13）
- 依赖：见 `requirements.txt`（`requests`、`pandas`、`numpy`、`matplotlib`、`playwright`、`argon2-cffi`）

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
├── portfolio.py             # 个人组合监控（tech.py / kchart.py / agents.py 配合）
├── live_server.py           # 主页 + 实时新闻服务（127.0.0.1:8766，静态发布 + /api/news）
├── position_manager.py      # 持仓管理本地服务（127.0.0.1:8765）
├── serve_page.py            # 纯静态服务（已被 live_server.py 取代，保留备用）
├── static_assets.py         # 静态资源加载器（读取并缓存 static/ 下的 css / js）
├── static/                  # 前端资源（改样式只动这里，不必碰 Python）
├── config.py                # 配置（指数、板块、多源冷却、网络重试、Cookie 路径等）
├── utils.py                 # 公共工具（格式化 / 类型转换 / 市场情绪判断）
├── requirements.txt         # 依赖清单
├── test_sources.py          # 多源适配层冒烟测试（联网；python test_sources.py）
├── migrate_inline_assets.py # 一次性迁移：把内联 CSS/JS/HTML 外置到 static/
├── verify_p1_sources.py     # 离线验证：多源健壮性（切源 / 冷却 / 风控 / 重试）
├── verify_p2_history.py     # 离线验证：历史快照落库与查询
├── verify_p8_static.py      # 离线验证：外置后产物与重构前逐字节一致
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
- **新闻没在动 / 看不到自动刷新**：确认 8766 端口上跑的是 `live_server.py`（而不是旧的
  `serve_page.py`）。浏览器访问 http://127.0.0.1:8766/api/health 应返回 JSON；
  若 404 说明旧服务还占着端口，关掉旧窗口重跑启动脚本即可。

## 变更说明（本次重构）

- **实时新闻推送**（新增 `live_server.py`）：启动脚本会后台拉起「主页 + 实时新闻」服务，
  每 5 分钟自动重抓一次全球新闻，并通过 `/api/news` 让主页**增量刷新**地球光点与新闻栏。
  服务**继承启动脚本的 console**，因此关掉窗口会一起退出（不再留下关不掉的孤儿进程）。
  刷新间隔见 `config.NEWS_REFRESH_SECONDS`，前端轮询间隔见 `config.NEWS_POLL_SECONDS`。
  手动预览：`python live_server.py --once`（抓一次写 `output/live_news.json`）、
  `python live_server.py --interval 60`（改成 1 分钟刷新）。

- **多源健壮性**：新增连接级重试与环境代理支持；风控关键词判定收窄到 HTML 响应；
  代码缺陷不再被当作来源故障静默吞掉（会进健康报告）。
- **历史快照**：新增 `history.py`，每轮采集落一条 SQLite 快照（`output/history.db`），
  支持趋势与环比查询（`python history.py`）。
- **前端资源外置**：CSS / JS / HTML 从 Python 模块搬到 `static/`。执行一次
  `python migrate_inline_assets.py` 完成迁移（脚本对每个资源做「写入 → 回读」
  逐字节校验，产物不变）；之后改配色 / 布局 / 图表样式只需编辑 `static/` 下的文件。
- **目录清理**：移除已无调用者的旧「综合报告」链路 —— `dividend.py` 与 `html_report.py`
  （其中仍被使用的 `judge_market` 已迁到 `utils.py`）。原文件保留在 `_refactor_backup/`
  以备回滚。
- **验证脚本**：`verify_p1_sources.py` / `verify_p2_history.py` / `verify_p8_static.py`
  为离线验证脚本（不联网、不写项目数据），可自证上述改动。

## 合规与免责

- 本工具仅用于个人学习与研究，请勿用于商业用途或对东财服务器造成过大压力（已内置限速）。
- 请遵守东方财富网站服务条款，控制请求频率。
- 程序自动抓取公开行情数据并归纳生成报告，仅供参考，不构成任何投资建议。
