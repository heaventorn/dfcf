# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 主入口

用法：
    python main.py                  # 全流程：登录(如有需要) -> 采集当日市场 -> 生成总结
    python main.py --login-only     # 仅执行登录并保存 Cookie
    python main.py --no-login       # 跳过登录，直接用公开接口采集（行情数据无需登录）

输出：
    output/latest_market.json          原始采集数据
    output/daily_report_*.html         综合图文报告（大盘 + 空中飞人指数 + 个人组合监控 合成一份）
"""

import argparse
import json
import os
import sys

import collector
import config
import html_report


def run(use_login=True):
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
        print("        建议稍等 15~30 分钟后再运行 python main.py，避免生成残缺报告。")
        print()

    # 3. 保存原始数据
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    raw_path = os.path.join(config.OUTPUT_DIR, "latest_market.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("✓ 原始数据已保存:", raw_path)

    # 4. 红利 / 红利低波 ETF 追踪分析 → 生成卡片片段（并入个人组合监控部分）
    dividend_cards = ""
    try:
        import dividend
        div_results = dividend.analyze_all()
        dividend_cards = dividend.generate_cards(div_results)
    except Exception as e:
        print(f"[提示] 红利追踪分析未完成（{e}），个人组合监控将不含红利部分。")

    # 4.1 罗力豪空中飞人指数（泡沫爆破风险指数，第二部分）
    airman_section = ""
    try:
        import airman
        airman_section = airman.generate_report_section()
    except Exception as e:
        print(f"[提示] 空中飞人指数计算未完成（{e}），综合报告将不含该部分。")

    # 4.2 个人组合监控（第三部分，覆盖固收/逆回购/红利ETF追踪/美股高成长）
    portfolio_section = ""
    try:
        import portfolio
        portfolio_data = portfolio.collect_all()
        data["portfolio"] = portfolio_data  # 并入原始数据，便于二次处理
        portfolio_section = portfolio.generate_report_section(portfolio_data, dividend_cards)
    except Exception as e:
        print(f"[提示] 个人组合监控未完成（{e}），综合报告将不含该部分。")

    # 5. 生成综合 HTML 图文报告（大盘 + 空中飞人指数 + 个人组合监控 合成一份）
    html_path = html_report.save_html_report(data, config.OUTPUT_DIR, "", airman_section, portfolio_section)
    print("✓ 综合图文报告已生成:", html_path)

    print()
    print("=" * 60)
    print("  报告已生成，用浏览器打开查看")
    print("=" * 60)

    return {"report": html_path}


def main():
    parser = argparse.ArgumentParser(description="东方财富当日市场信息爬虫")
    parser.add_argument("--login-only", action="store_true", help="仅执行登录并保存 Cookie")
    parser.add_argument("--no-login", action="store_true", help="跳过登录，直接使用公开接口")
    args = parser.parse_args()

    if args.login_only:
        import login
        login.ensure_login()
        return

    run(use_login=not args.no_login)


if __name__ == "__main__":
    sys.exit(main())
