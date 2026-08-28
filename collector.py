# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 当日市场数据采集模块（多源自动切换版）

数据获取统一走 sources.py 多源适配层：
  - 指数 / 个股榜 / 板块榜 / 涨跌家数 / 财经快讯 等，每种数据都有多个数据源；
  - 任一来源出现数据异常（断连 / 空数据 / 结构异常 / 风控页）时自动切换到下一来源；
  - 异常来源进入冷却期并自动恢复，运行结束打印来源健康报告。

返回结构与原单源版本完全一致，main.py / html_report.py 无需改动。
"""

import json
import os
import time

import config
from sources import (
    load_cookies,
    get_indices,
    get_stock_rank,
    get_sector_rank,
    get_sector_rank_fall,
    get_market_breadth,
    get_limit_pool,
    get_flash_news,
    health_report,
)


def _retry_on_empty(func, *args, retries=3, **kwargs):
    """关键采集项：结果为空时整体重试（多源已内部切源，这里兜底整链重试）。"""
    result = func(*args, **kwargs)
    for _ in range(retries - 1):
        if result:
            break
        print(f"    [重试] {getattr(func, '__name__', '采集项')} 结果为空，稍候整体重试 ...")
        time.sleep(2.0)
        result = func(*args, **kwargs)
    return result


def collect_all():
    """采集当日全部市场信息，返回字典。

    顺序说明：先把轻量请求（指数/涨跌停池/板块/个股/快讯）做完，
    全市场分页统计（请求量大、易触发限流）放到最后。
    """
    load_cookies()
    print("[1/8] 抓取主要指数 ...")
    indices = _retry_on_empty(get_indices)

    print("[2/8] 抓取涨停池 ...")
    zt = get_limit_pool("zt")

    print("[3/8] 抓取跌停池 ...")
    dt = get_limit_pool("dt")

    print("[4/8] 抓取行业板块涨幅榜 ...")
    industry_up = _retry_on_empty(get_sector_rank, "industry", 10)
    industry_down = _retry_on_empty(get_sector_rank_fall, 5)

    print("[5/8] 抓取概念板块涨幅榜 ...")
    concept_up = _retry_on_empty(get_sector_rank, "concept", 8)

    print("[6/8] 抓取个股排行 ...")
    stock_up = _retry_on_empty(get_stock_rank, "f3", "desc", 10)      # 涨幅榜
    stock_down = _retry_on_empty(get_stock_rank, "f3", "asc", 10)     # 跌幅榜

    print("[7/8] 抓取财经快讯 ...")
    news = get_flash_news(30)

    print("[8/8] 统计全市场涨跌家数 ...")
    breadth = get_market_breadth()

    data = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "indices": indices,
        "breadth": breadth,
        "limit_up": zt,
        "limit_down": dt,
        "industry_up": industry_up,
        "industry_down": industry_down,
        "concept_up": concept_up,
        "stock_up": stock_up,
        "stock_down": stock_down,
        "news": news,
    }

    # 数据完整性检查（多源全部失败时对应数据可能为空，主动提示）
    missing = [
        k for k in ("indices", "breadth", "industry_up", "concept_up", "stock_up")
        if not data.get(k)
    ]
    if missing:
        print()
        print("[警告] 以下数据抓取为空（多个数据源均异常，可能全站限流或网络问题）：")
        print("       ", "、".join(missing))
        print("        建议稍等 15~30 分钟后再运行 python main.py，避免生成残缺报告。")
        print()

    # 来源健康报告
    print("数据源健康状态：")
    print(health_report())

    return data


if __name__ == "__main__":
    d = collect_all()
    out = os.path.join(config.OUTPUT_DIR, time.strftime("market_%Y%m%d_%H%M.json"))
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print("已保存:", out)
    print("指数:", [(x["name"], x["price"], x["change_pct"]) for x in d["indices"]])
    print("涨跌:", d["breadth"], "| 涨停:", d["limit_up"]["count"], "| 跌停:", d["limit_down"]["count"])
