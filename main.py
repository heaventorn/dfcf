# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 主入口

用法：
    python main.py                  # 全流程：登录(如有需要) -> 采集当日市场 -> 生成主页并自动打开
    python main.py --login-only     # 仅执行登录并保存 Cookie
    python main.py --no-login       # 跳过登录，直接用公开接口采集（行情数据无需登录）
    python main.py --no-open        # 生成完成后不自动打开浏览器

输出：
    output/latest_market.json          原始采集数据
    output/index.html                  主页「3D 地球指挥台」= 中间 3D 地球
                                       + 左侧「大A行情概况 + 白底可缩放行情图 / 新闻·日历」
                                       + 右侧「我的持仓 + 配置标的行情 / 空中飞人指数」

主页通过本地静态服务访问：http://127.0.0.1:8766/output/index.html
（走 http 而不是 file://：浏览器的 file:// 安全策略会拦截页面读取本地 8K 地球贴图，
  表现为「国界/光点都在，但地球是黑球、只剩一圈亮边」。）

运行完会自动后台拉起两个常驻服务并打开主页：
    8766  主页静态服务(serve_page.py)    8765  持仓管理服务(position_manager.py)
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


def _start_bg(script, port, label):
    """后台拉起一个常驻服务(端口已被占用则跳过);返回是否端口就绪。"""
    import socket
    import subprocess
    import time as _time

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"✓ {label}已在运行: http://127.0.0.1:{port}")
            return True

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, path], creationflags=flags)
    for _ in range(20):
        _time.sleep(0.2)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
            s2.settimeout(0.5)
            if s2.connect_ex(("127.0.0.1", port)) == 0:
                print(f"✓ {label}已后台启动: http://127.0.0.1:{port}")
                return True
    print(f"[提示] {label}启动较慢,稍后可直接访问 http://127.0.0.1:{port}")
    return False


def run(use_login=True, open_browser=True):
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
    airman_res = None
    airman_refs = []
    try:
        import airman
        raw_a = airman.collect_all()
        airman_res = airman.compute_index(raw_a)
        airman_refs = airman.collect_reference(raw_a)
    except Exception as e:
        print(f"[提示] 空中飞人指数计算未完成（{e}），主页右下方将显示为空。")

    # 5. 个人组合监控 + 真实持仓（主页右上方窗口）
    try:
        import portfolio
        data["portfolio"] = portfolio.collect_all()
    except Exception as e:
        print(f"[提示] 个人组合监控未完成（{e}），主页右上方将显示为空。")

    # 6. 生成主页「3D 地球指挥台」（唯一产出页面:output/index.html）
    home_path = None
    try:
        import events as globe_events
        import home as home_mod
        evs = globe_events.fetch_events(limit=300)
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
    except Exception as e:
        print(f"[提示] 主页生成未完成（{e}），可运行 python home.py --probe 排查。")

    # 6.1 持仓管理服务（127.0.0.1:8765）
    try:
        _start_bg("position_manager.py", 8765, "持仓管理服务")
    except Exception as e:
        print(f"[提示] 持仓管理服务自动启动失败（{e}）")

    # 6.2 主页静态服务（127.0.0.1:8766）——file:// 下浏览器会拦本地贴图,主页统一走 http
    serve_ok = False
    try:
        serve_ok = _start_bg("serve_page.py", 8766, "主页静态服务")
    except Exception as e:
        print(f"[提示] 主页静态服务自动启动失败（{e}），将退回 file:// 打开（地球贴图可能不显示）")

    # 7. 自动打开主页（--no-open 可跳过）
    if open_browser and home_path:
        try:
            import webbrowser
            from pathlib import Path
            url = PAGE_URL if serve_ok else Path(home_path).resolve().as_uri()
            webbrowser.open(url)
            print("✓ 已在浏览器打开主页:", url)
        except Exception as e:
            print(f"[提示] 自动打开主页失败（{e}），请手动访问 {PAGE_URL}")

    print()
    print("=" * 60)
    print("  主页:", PAGE_URL)
    print("  文件:", home_path or "(生成失败,详见上方提示)")
    print("=" * 60)

    return {"home": home_path, "url": PAGE_URL}


def main():
    parser = argparse.ArgumentParser(description="东方财富当日市场信息爬虫")
    parser.add_argument("--login-only", action="store_true", help="仅执行登录并保存 Cookie")
    parser.add_argument("--no-login", action="store_true", help="跳过登录，直接使用公开接口")
    parser.add_argument("--no-open", action="store_true", help="生成完成后不自动打开浏览器")
    args = parser.parse_args()

    if args.login_only:
        import login
        login.ensure_login()
        return

    run(use_login=not args.no_login, open_browser=not args.no_open)


if __name__ == "__main__":
    sys.exit(main())
