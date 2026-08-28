# -*- coding: utf-8 -*-
"""多源适配层功能测试（实际请求，验证解析与自动切源）。"""
import json
import sources

print("=" * 60)
print("1) 指数 get_indices()")
idx = sources.get_indices()
print("   ", [(x["name"], x["price"], x["change_pct"], x["amount"]) for x in idx])

print("=" * 60)
print("2) 个股涨幅榜 get_stock_rank('f3','desc',5)")
st = sources.get_stock_rank("f3", "desc", 5)
print("   ", [(x["name"], x["code"], x["change_pct"], x["price"], x["amount"]) for x in st])

print("=" * 60)
print("3) 个股跌幅榜 get_stock_rank('f3','asc',3)")
sd = sources.get_stock_rank("f3", "asc", 3)
print("   ", [(x["name"], x["change_pct"]) for x in sd])

print("=" * 60)
print("4) 个股成交额榜 get_stock_rank('f62','desc',3)")
sa = sources.get_stock_rank("f62", "desc", 3)
print("   ", [(x["name"], x["amount"]) for x in sa])

print("=" * 60)
print("5) 行业板块 get_sector_rank('industry',5)")
ind = sources.get_sector_rank("industry", 5)
print("   ", [(x["name"], x["change_pct"], x["lead_stock"]) for x in ind])

print("=" * 60)
print("6) 概念板块 get_sector_rank('concept',3)")
con = sources.get_sector_rank("concept", 3)
print("   ", [(x["name"], x["change_pct"]) for x in con])

print("=" * 60)
print("7) 板块跌幅 get_sector_rank_fall(3)")
indf = sources.get_sector_rank_fall(3)
print("   ", [(x["name"], x["change_pct"]) for x in indf])

print("=" * 60)
print("8) 涨停池 get_limit_pool('zt')")
zt = sources.get_limit_pool("zt")
print("   count:", zt["count"], "sample:", [(x["name"], x["lbc"]) for x in zt["items"][:3]])

print("=" * 60)
print("9) 跌停池 get_limit_pool('dt')")
dt = sources.get_limit_pool("dt")
print("   count:", dt["count"], "sample:", [(x["name"], x["lbc"]) for x in dt["items"][:3]])

print("=" * 60)
print("10) 财经快讯 get_flash_news(3)")
news = sources.get_flash_news(3)
print("   ", [(x["time"], x["title"][:20]) for x in news])

print("=" * 60)
print("11) 全市场涨跌家数 get_market_breadth()")
b = sources.get_market_breadth()
print("   ", b)

print("=" * 60)
print("12) 实时行情 get_realtime_quotes (510880/512890/515450)")
q = sources.get_realtime_quotes(["sh510880", "sh512890", "sh515450"])
print("   ", {k: (v.get("name"), v.get("price"), v.get("pct")) for k, v in q.items()})

print("=" * 60)
print("13) K线 get_kline('sh510880','1.510880')")
tech, yld = sources.get_kline("sh510880", "1.510880")
print("   tech n=", len(tech), "last=", tech[-1] if tech else None)
print("   yld  n=", len(yld), "last=", yld[-1] if yld else None)

print("=" * 60)
print("14) 分红 get_dividends('510880')")
divs = sources.get_dividends("510880")
print("   n=", len(divs), "sample=", divs[:2])

print()
print("=" * 60)
print("数据源健康状态：")
print(sources.health_report())
