# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 主入口

用法：
    python main.py                  # 全流程：登录(如有需要) -> 采集当日市场 -> 生成主页并打开独立窗口
    python main.py --login-only     # 仅执行登录并保存 Cookie
    python main.py --no-login       # 跳过登录，直接用公开接口采集（行情数据无需登录）
    python main.py --no-open        # 生成完成后不自动打开窗口

输出：
    output/latest_market.json          原始采集数据
    output/history.db                  历史快照库（每轮一条，趋势/环比查询：python history.py）
    output/index.html                  主页「3D 地球指挥台」= 中间 3D 地球
                                       + 左侧「大A行情概况 + 深色可缩放行情图 / 新闻·日历」
                                       + 右侧「我的持仓 + 我的自选 / 空中飞人指数」

主页通过本地静态服务访问：http://127.0.0.1:8766/output/index.html
（走 http 而不是 file://：浏览器的 file:// 安全策略会拦截页面读取本地地球贴图，
  表现为「国界/光点都在，但地球是黑球、只剩一圈亮边」。）

打开方式：优先用 Edge 的 --app 参数开一个「没有地址栏 / 标签栏」的独立窗口，看起来
就是个本地应用；本机找不到 Edge 时自动退回系统默认浏览器。--no-open 可完全跳过。

运行完会自动后台拉起两个常驻服务并打开主页：
    8766  主页 + 实时新闻服务(live_server.py，每 5 分钟自动刷新全球新闻)
    8765  持仓管理服务(position_manager.py)
两个服务继承本进程的 console：关掉启动脚本窗口 / Ctrl+C 会一起退出，不留残余进程。
"""

import argparse
import json
import os
import sys

# 控制台输出统一用 UTF-8(Windows 下若为 GBK 代码页,中文与 ✓ 等符号会直接抛
# UnicodeEncodeError 中断整个流程;启动爬虫.bat 已带 chcp 65001)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import collector
import config

PAGE_URL = "http://127.0.0.1:8766/output/index.html"


def _start_bg(script, port, label, extra_args=None):
    """后台拉起一个常驻服务(端口已被占用则跳过);返回是否端口就绪。

    刻意**不**用 CREATE_NO_WINDOW：那会给子进程新建一个独立 console，关掉启动脚本
    窗口后它就成了孤儿进程、继续占着端口。让子进程继承当前 console，Windows 在
    窗口关闭 / Ctrl+C 时会一并结束它，即「关掉 bat，后台服务也自动关闭」。
    """
    import socket
    import subprocess
    import time as _time

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"✓ {label}已在运行: http://127.0.0.1:{port}")
            return True

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    cmd = [sys.executable, path] + list(extra_args or [])
    subprocess.Popen(cmd)   # 继承 console，随启动脚本一起退出
    for _ in range(20):
        _time.sleep(0.2)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
            s2.settimeout(0.5)
            if s2.connect_ex(("127.0.0.1", port)) == 0:
                print(f"✓ {label}已后台启动: http://127.0.0.1:{port}")
                return True
    print(f"[提示] {label}启动较慢,稍后可直接访问 http://127.0.0.1:{port}")
    return False


def _collect_airman():
    """罗力豪空中飞人指数（主页右下方窗口）；失败返回 (None, [])。"""
    try:
        import airman
        raw_a = airman.collect_all()
        res = airman.compute_index(raw_a)
        refs = airman.collect_reference(raw_a)
        try:
            import risk
            risk.save_airman(res, refs)
            import history
            history.record_risk(res, refs=refs, note="main.py")
        except Exception as e:
            print(f"[提示] 空中飞人风险快照落盘未完成（{e}），不影响主页。")
        return res, refs
    except Exception as e:
        print(f"[提示] 空中飞人指数计算未完成（{e}），主页右下方将显示为空。")
        return None, []


def _collect_portfolio(data):
    """个人组合监控 + 真实持仓（主页右上方窗口）；失败不影响主流程。"""
    try:
        import portfolio
        data["portfolio"] = portfolio.collect_all()
    except Exception as e:
        print(f"[提示] 个人组合监控未完成（{e}），主页右上方将显示为空。")
    return data.get("portfolio")


def _record_history(data, use_login=True):
    """历史快照落库（output/history.db）；失败不影响主流程。"""
    try:
        import history
        import sources
        sid = history.record_run(
            data,
            health=sources.health_snapshot(),
            note="main.py" + ("" if use_login else " --no-login"),
        )
        print(f"✓ 历史快照已入库: output/history.db (snapshot #{sid})")
        return sid
    except Exception as e:
        print(f"[提示] 历史快照落库未完成（{e}），不影响本次报告。")
        return None


def _build_home_page(data, airman_res, airman_refs):
    """生成主页「3D 地球指挥台」（唯一产出页面）；失败返回 None。"""
    try:
        import events as globe_events
        import home as home_mod
        evs = globe_events.fetch_events()   # 条数上限与时间窗由 config 决定
        payload = home_mod.build_payload(
            data, events=evs, airman_res=airman_res, airman_refs=airman_refs,
            portfolio_data=data.get("portfolio"),
            window_days=globe_events.WINDOW_DAYS,
        )
        chart_data = home_mod.generate_chart_data()
        home_path = os.path.join(config.OUTPUT_DIR, "index.html")
        home_mod.build_home(evs, payload, home_path, assets_prefix="../assets/",
                            chart_data=chart_data)
        print("✓ 主页（3D 地球指挥台）已生成:", home_path)
        return home_path
    except Exception as e:
        print(f"[提示] 主页生成未完成（{e}），可运行 python home.py --probe 排查。")
        return None


def _start_services():
    """后台拉起**一个**常驻服务进程，它同时托管 8766（主页 + 实时新闻 + 个股接口）
    与 8765（持仓管理）；返回主页服务是否就绪。

    主页统一走 http 而不是 file://：浏览器的 file:// 安全策略会拦截页面读取
    本地 8K 地球贴图（表现为「地球是黑球，只剩一圈亮边」）。
    8766 使用 live_server.py：同一端口既发布静态主页，也提供 /api/news
    （前端按版本号轮询，后端每 config.NEWS_REFRESH_SECONDS 秒自动重抓新闻）。

    两个端口合并进同一个进程（--with-positions）是刻意的：以前分两个子进程，
    启动时会多出**两个**服务窗口，看着像开了两个程序；现在只有一个，
    关掉启动脚本窗口时两个端口一起停，也不会留下孤儿进程。
    """
    try:
        return _start_bg("live_server.py", 8766, "主页 + 实时新闻 + 持仓管理服务",
                         extra_args=["--with-positions"])
    except Exception as e:
        print(f"[提示] 主页/新闻服务自动启动失败（{e}），将退回 file:// 打开（地球贴图可能不显示）")
        return False


# Edge 的几种常见安装位置（64 位系统上也可能只装了 32 位那份）
_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\Application\msedge.exe"),
)


def _find_edge():
    """找一个可用的 Edge 可执行文件；找不到返回 None。"""
    for p in _EDGE_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def _open_app_window(url):
    """用 Edge 的 --app 模式开一个「没有地址栏/标签栏」的独立窗口。

    --app 是 Chromium 系的内置参数：窗口里只有网页内容，看起来就是个本地应用，
    比自己用 Qt 内嵌一个浏览器省事得多（渲染内核本来就是同一个）。
    --user-data-dir 单独放一份 profile，免得和日常浏览器的标签/窗口混在一起。
    返回 True 表示已用这种方式打开；False 表示环境不支持，调用方应退回 webbrowser。
    """
    exe = _find_edge()
    if not exe:
        return False
    try:
        import subprocess
        profile = config.EDGE_PROFILE_DIR
        subprocess.Popen([
            exe,
            "--app=" + url,
            "--window-size=1600,940",
            "--user-data-dir=" + profile,
        ])
        return True
    except Exception:
        return False


def _open_page(home_path, serve_ok):
    """打开主页：优先用 Edge 的 --app 独立窗口，退化时用系统默认浏览器。"""
    if not home_path:
        print("[提示] 主页未生成，跳过自动打开。")
        return
    try:
        from pathlib import Path
        url = PAGE_URL if serve_ok else Path(home_path).resolve().as_uri()
        if _open_app_window(url):
            print("✓ 已打开主页窗口（Edge --app，无地址栏）:", url)
            return
        import webbrowser
        webbrowser.open(url)
        print("✓ 已在浏览器打开主页:", url)
    except Exception as e:
        print(f"[提示] 自动打开主页失败（{e}），请手动访问 {PAGE_URL}")


def _ensure_vault():
    """解锁本地保险库：隐私文件都是密文，没有密钥读不出来。"""
    try:
        import secure_store
    except Exception as e:
        print("[提示] 加密模块加载失败：%s" % e)
        return True
    try:
        if secure_store.unlock_interactive() is not None:
            return True
    except Exception as e:
        print("[提示] 保险库解锁出错：%s" % e)
    return False


def run(use_login=True, open_browser=True):
    if not _ensure_vault():
        print("[!] 保险库未解锁，程序退出。")
        return None
    print("=" * 60)
    print("  东方财富 · 当日市场信息采集与总结")
    print("=" * 60)

    # 1. 登录（可选）
    if use_login:
        try:
            import login
            login.ensure_login()
        except Exception as e:
            print(f"[提示] 登录环节未完成（{e}），继续使用公开接口采集。")

    # 2. 采集
    data = collector.collect_all()

    # 2.1 数据完整性检查（东财限流时某些数据可能为空，主动提示）
    missing = [
        k for k in ("indices", "breadth", "industry_up", "concept_up", "stock_up")
        if not data.get(k)
    ]
    if missing:
        print()
        print("[警告] 以下数据抓取为空（很可能触发了东方财富的临时限流）：")
        print("       ", "、".join(missing))
        print("        建议稍等 15~30 分钟后再运行 python main.py，避免生成残缺数据。")
        print()

    # 3. 保存原始数据
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    raw_path = os.path.join(config.OUTPUT_DIR, "latest_market.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("✓ 原始数据已保存:", raw_path)

    # 4. 罗力豪空中飞人指数（主页右下方窗口）
    airman_res, airman_refs = _collect_airman()

    # 5. 个人组合监控 + 真实持仓（主页右上方窗口）
    _collect_portfolio(data)

    # 5.1 历史快照落库（output/history.db；查询趋势 / 环比：python history.py）
    _record_history(data, use_login=use_login)

    # 6. 生成主页「3D 地球指挥台」（唯一产出页面:output/index.html）
    home_path = _build_home_page(data, airman_res, airman_refs)

    # 6.1 / 6.2 常驻后台服务：8765 持仓管理 / 8766 主页 + 实时新闻(每 5 分钟刷新)
    serve_ok = _start_services()

    # 7. 自动打开主页（--no-open 可跳过）
    if open_browser:
        _open_page(home_path, serve_ok)

    print()
    print("=" * 60)
    print("  主页:", PAGE_URL)
    print("  新闻:", "http://127.0.0.1:8766/api/news",
          "（每 %d 秒自动刷新）" % getattr(config, "NEWS_REFRESH_SECONDS", 300))
    print("  持仓/自选:", "http://127.0.0.1:8765", "（与主页同进程托管）")
    print("  策略执行台:", "http://127.0.0.1:8766/strategy",
          "（配置对照 / 买卖计划 / 回测 / 模型分析）")
    print("  个股页:", "主页里点持仓/自选任意一行即可进入（Ctrl/中键可开新标签）")
    print("  文件:", home_path or "(生成失败,详见上方提示)")
    print("=" * 60)

    return {"home": home_path, "url": PAGE_URL}


def main():
    parser = argparse.ArgumentParser(description="东方财富当日市场信息爬虫")
    parser.add_argument("--login-only", action="store_true", help="仅执行登录并保存 Cookie")
    parser.add_argument("--no-login", action="store_true", help="跳过登录，直接使用公开接口")
    parser.add_argument("--no-open", action="store_true", help="生成完成后不自动打开浏览器")
    parser.add_argument("--airman-backfill", action="store_true",
                        help="按月末回补空中飞人历史代理序列（供策略回测使用）")
    args = parser.parse_args()

    if not _ensure_vault():
        print("[!] 保险库未解锁，程序退出。")
        return 1

    if args.login_only:
        import login
        login.ensure_login()
        return

    if args.airman_backfill:
        import airman
        pts = airman.backfill_history()
        print("✓ 空中飞人历史代理序列已回补：%d 个月末点位" % len(pts))
        return

    run(use_login=not args.no_login, open_browser=not args.no_open)


if __name__ == "__main__":
    sys.exit(main())
