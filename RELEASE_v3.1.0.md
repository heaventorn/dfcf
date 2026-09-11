# v3.1.0 · 实时新闻 + 历史快照 + 工程加固

在 v3.0.0「3D 地球指挥台」的基础上，这一版把主页从**一次性快照**变成**会自己更新的活页面**，
并把数据从「看完就丢」变成「可回溯的时间序列」；同时清理了旧报告链路、加固了多源取数。

---

## 本版重点

### 📡 实时新闻推送（新增 `live_server.py`）
- 启动脚本自动在后台拉起「主页 + 实时新闻」服务（8766）：同一端口既发布静态主页，
  也提供新闻 API —— **同源，无需 CORS**
- **每 5 分钟**自动重抓一次全球新闻（间隔见 `config.NEWS_REFRESH_SECONDS`），
  主页按版本号轮询 `/api/news`，有新内容就**增量刷新地球光点与新闻栏**
  —— 不整页重载、不重下地球贴图、无闪烁
- 接口：`/api/news`（新闻 + 热点 + 光点聚合 + 版本号）、`/api/news?ver=N`（版本未变只回
  `{"unchanged":true}`）、`/api/health`（上次抓取时间 / 下次刷新倒计时 / 条数 / 错误）
- **关掉启动脚本窗口 → 后台服务一起退出**：原先用 `CREATE_NO_WINDOW` 会给子进程新建独立
  console，关窗口后它变成孤儿进程继续占端口（8765 持仓服务同样受影响）；本版改为让子进程
  **继承启动脚本的 console**，窗口关闭 / Ctrl+C 时一并结束

### 🗞 新闻口径改为「当天」，条数大幅提高
- 时间窗从「最近 3 天滚动窗口」改为 **当天（自然日 0 点起）**：`config.NEWS_TODAY_ONLY`
- 条数上限 300 → **1000**；新闻栏渲染条数 60 → **300**（`config.NEWS_LIMIT` / `NEWS_FEED_ROWS`）
- 各信源抓取深度集中到 `config`（`NEWS_EM_PAGES` / `NEWS_THS_LIMIT` / `NEWS_THS_PAGES` /
  `NEWS_CLS_LIMIT` / `NEWS_JIN10_LIMIT` / `NEWS_WALLSTCN_LIMIT`），避免白天新闻量暴涨时
  被翻页上限截断
- **新增信源：新浪财经 7x24**（`feeds.fetch_sina`，可翻页取回当天全部）
- 页面文案随口径统一：`window_label()` 输出「今日」/「近 N 天」，标题、新闻栏、事件页共用

### 🗄 历史快照存储（新增 `history.py`）
- 每轮采集落一条 SQLite 快照到 `output/history.db`（零新增依赖，标准库 `sqlite3`）
  - 打平列：涨跌家数 / 涨跌停家数 / 上证收盘与涨跌幅 / 领涨板块，另存完整 payload JSON
  - 同时记录各数据源健康状态（成功 / 失败 / 连续失败 / 是否冷却）
- 查询：`record_run` / `latest` / `previous` / `recent` / `series` / `compare` / `report`
- 命令行：`python history.py`、`--days 30`、`--field breadth_up`、`--json`
- 落库失败不影响主流程

### 🛡 多源取数加固（`sources.py` / `config.py`）
- **连接级重试**：断连 / 超时 / 5xx / 429 自动退避重试（`HTTP_RETRIES` / `HTTP_BACKOFF`）
- **环境代理支持**：读取 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（`USE_ENV_PROXY` 可强制直连）
- **风控判定分档**：中文强特征任何响应都判；`verify` / `captcha` 这类英文宽泛词**只在 HTML
  响应或 JSON 解析失败时**才判 —— 避免正常数据正文里出现这些词被误杀成风控页而误切源
- **代码缺陷可见**：非数据源异常（`KeyError` / `AttributeError` …）不再被 `except Exception`
  静默当成「来源故障」吞掉，而是打印 traceback、计入运行结束的健康报告，且**不计入来源冷却**
- 死配置 `config.RETRIES`（全仓零引用）清除，采集项重试统一为 `COLLECT_RETRIES`

### 🧹 工程结构
- **前端资源外置机制**：新增 `static_assets.py`（加载器）+ `migrate_inline_assets.py`
  （一次性迁移）。迁移脚本取 Python **解释后的字符串值**写文件、再回读做逐字节校验，
  因此不存在转义风险；之后改配色 / 布局 / 图表样式只需动 `static/` 下的文件
- **移除旧「综合报告」链路**：`dividend.py`（472 行，无任何调用者）与 `html_report.py`
  （其 `judge_market` 已迁到 `utils.py`，其余无人调用）
- `main.py` 的 `run()`（103 行）拆成 6 个小函数：`_collect_airman` / `_collect_portfolio` /
  `_record_history` / `_build_home_page` / `_start_services` / `_open_page`
- 地球光点的**涟漪（扩散脉冲环）关闭**：三处 `ringsData` 全部置空
  （初始化 / 筛选联动 / 新闻刷新），光点与数字徽章不受影响
- 离线验证脚本：`verify_p1_sources.py`（切源 / 冷却 / 风控 / 重试）、
  `verify_p2_history.py`（落库与查询）、`verify_p8_static.py`（外置前后产物逐字节一致）

---

## 运行方式

```
1. 安装依赖:  pip install -r requirements.txt
2. 双击运行:  启动爬虫.bat        (或: py -3.12 main.py)
3. 浏览器会打开: http://127.0.0.1:8766/output/index.html
```

运行结束会自动后台拉起两个常驻服务：

| 端口 | 用途 |
|---|---|
| 8766 | **主页 + 实时新闻**（`live_server.py`）—— 静态发布 + `/api/news`，每 5 分钟自动刷新新闻 |
| 8765 | 持仓管理（`position_manager.py`）—— 浏览器里增删买卖，写回 `positions.json` |

> 关掉启动脚本窗口（或 Ctrl+C）时，这两个服务会**一起退出**，不再残留。
> `serve_page.py`（纯静态服务）保留备用，但已不再由 `main.py` 拉起。

### 常用命令

```bash
python main.py                     # 采集 + 生成主页 + 拉起服务
python history.py                  # 历史快照趋势 / 环比
python live_server.py --once       # 只抓一次新闻，写 output/live_news.json 后退出
python live_server.py --interval 60   # 单独跑新闻服务，改成 1 分钟刷新
python migrate_inline_assets.py --dry-run   # 查看前端资源外置计划
```

### 可选配置
- 多 Agent 辩论/点评需要 DeepSeek Key：项目根目录建 `.env`，写入 `DEEPSEEK_API_KEY=sk-xxx`
- 持仓：编辑 `positions.json`，或在 8765 页面里管理
- 新闻相关：`NEWS_TODAY_ONLY` / `NEWS_REFRESH_SECONDS` / `NEWS_POLL_SECONDS` / `NEWS_LIMIT` 等见 `config.py`

---

## 升级须知

- **主页需要重新生成**：`output/index.html` 是生成产物，本版改了页面 JS，请重跑一次
  `python main.py`；若 8766 端口上还跑着旧进程，先关掉窗口再启动
- 旧的 `output/daily_report_*.html` 流程（`dividend.py` / `html_report.py`）已移除，
  相关内容并入主页；如需单独报告页可在 `main.py` 里恢复生成
- 若你希望把 CSS / JS 从 Python 里彻底抽出来，执行一次 `python migrate_inline_assets.py`
  （迁移脚本会先备份被改文件，并对每个资源做「写入 → 回读」逐字节校验）
- `.gitignore` 已排除 `.env` / `cookies.json` / `pwd.key` / `output/`；**仓库内不含任何密钥**

## 主要文件

```
main.py                  主流程：采集 → 落库 → 生成主页 → 拉起服务
live_server.py           主页 + 实时新闻服务（8766）：静态发布 + /api/news + 后台定时抓取
history.py               历史快照存储 / 查询（SQLite）
home.py                  主页数据聚合与页面生成（含实时刷新前端逻辑）
events.py                全球宏观事件采集 + 3D 地球页模板
geo.py / feeds.py        地点归因 / 六个信源（东财 / 见闻 / 财联社 / 金十 / 同花顺 / 新浪）
sources.py               多源适配层：异常检测 + 切换 + 冷却 + 连接级重试 + 代理
collector.py / airman.py 大盘采集 / 罗力豪空中飞人指数
portfolio.py             个人组合监控
position_manager.py      持仓管理服务（8765）
static_assets.py         前端资源加载器        migrate_inline_assets.py  一次性外置迁移
verify_p1_sources.py / verify_p2_history.py / verify_p8_static.py   离线验证脚本
assets/                  本地资源：three.js / globe.gl / lightweight-charts / 8K 贴图 / world.geojson
```
