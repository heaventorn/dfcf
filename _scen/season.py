# -*- coding: utf-8 -*-
"""红利 ETF 的季节性 / 波段可信度检验。

要回答的问题：
  1) 所谓「一月份跌到低点」是真的吗？还是分红除息造成的错觉？
  2) 一年里最低点落在哪个月，分布到底有多集中？
  3) 「1 月低买、高点卖出、一年做好几次」这种规则，历史上能不能跑赢买入持有？
"""

import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
HERE = os.path.dirname(os.path.abspath(__file__))

import bars
import requests

NAV = json.load(open(os.path.join(HERE, "etf_nav.json"), encoding="utf-8"))
MONTH = ["1月", "2月", "3月", "4月", "5月", "6月",
         "7月", "8月", "9月", "10月", "11月", "12月"]


def fund_nav_pair(code):
    """单位净值 + 累计净值。两者之比的变化 = 分红。"""
    r = requests.get("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://fund.eastmoney.com/%s.html" % code},
                     timeout=30)
    out = {}
    for name, tag in (("unit", "Data_netWorthTrend"), ("acc", "Data_ACWorthTrend")):
        m = re.search(r"var %s\s*=\s*(\[.*?\]);" % tag, r.text)
        if not m:
            continue
        ser = {}
        for x in json.loads(m.group(1)):
            try:
                if isinstance(x, dict):          # 单位净值是 {x: 日期, y: 净值}
                    ms, v = x.get("x"), x.get("y")
                else:
                    ms, v = x[0], x[1]
                ms, v = int(ms), float(v)
            except (TypeError, ValueError, IndexError):
                continue
            d = datetime.datetime.fromtimestamp(
                ms / 1000.0 + 8 * 3600, datetime.timezone.utc)
            if v > 0:
                ser[d.strftime("%Y%m%d")] = v
        out[name] = ser
    return out


def dividends(code):
    p = fund_nav_pair(code)
    u, a = p.get("unit") or {}, p.get("acc") or {}
    days = sorted(set(u) & set(a))
    out = []
    for i in range(1, len(days)):
        d0, d1 = days[i - 1], days[i]
        ra = a[d1] / a[d0]
        ru = u[d1] / u[d0]
        if ru <= 0:
            continue
        f = ra / ru
        if f > 1.0012:
            out.append((d1, 1 - 1 / f))
    return out


def month_ends(ser):
    out = {}
    for d in sorted(ser):
        out[d[:6]] = ser[d]
    return out


def daily(ser, beg=None):
    return [(d, ser[d]) for d in sorted(ser) if not beg or d >= beg]


def cagr_of(navs, days):
    a = datetime.date(int(days[0][:4]), int(days[0][4:6]), int(days[0][6:8]))
    b = datetime.date(int(days[-1][:4]), int(days[-1][4:6]), int(days[-1][6:8]))
    yrs = (b - a).days / 365.25
    return (navs[-1] / navs[0]) ** (1 / yrs) - 1, yrs


def mdd_of(navs):
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def consecutive_months(keys):
    for k0, k1 in zip(keys, keys[1:]):
        same_year = k0[:4] == k1[:4] and int(k1[4:]) - int(k0[4:]) == 1
        year_join = k0[4:] == "12" and k1[4:] == "01"
        if same_year or year_join:
            yield k0, k1


# ------------------------------------------------------------------ A 分红

def section_dividend():
    print("=" * 78)
    print("A. 分红除息日（单位净值跌、累计净值不跌的那一天）")
    for code, name in (("512890", "红利低波ETF"), ("513630", "港股红利ETF"),
                       ("510300", "沪深300ETF")):
        try:
            ds = dividends(code)
        except Exception as e:
            print("  %s %s 取数失败 %s" % (code, name, e))
            continue
        if not ds:
            print("  %s %s：这段历史里没有分红" % (code, name))
            continue
        bym = {}
        for d, r in ds:
            bym.setdefault(d[4:6], []).append((d, r))
        print("  %s %s：%d 次分红，按月份 %s"
              % (code, name, len(ds),
                 " ".join("%s月%d次" % (m, len(v)) for m, v in sorted(bym.items()))))
        for m in sorted(bym):
            for d, r in bym[m]:
                # 注意：这里是「单位净值相对复权净值的当天落差」，不是
                # 每股派了多少钱 —— pingzhongdata 的两个字段口径不一致，
                # 只能用来判断「哪一天净值有过非行情性的跳低」。
                print("       %s  单位净值当天比复权净值多跌 %0.2f%%"
                      % (d, r * 100))


# ------------------------------------------------------------------ B 季节性

def section_season(ser, label, beg=None):
    me = month_ends(ser)
    keys = [k for k in sorted(me) if not beg or k >= beg]
    rets = {}
    for k0, k1 in zip(keys, keys[1:]):
        same_year = k0[:4] == k1[:4] and int(k1[4:]) - int(k0[4:]) == 1
        year_join = k0[4:] == "12" and k1[4:] == "01"
        if same_year or year_join:
            rets[k1] = me[k1] / me[k0] - 1
    print()
    print("  [%s] %s ~ %s，%d 个月" % (label, keys[0], keys[-1], len(rets)))
    print("      月份     平均      中位     上涨概率     最差      最好")
    avgs = {}
    for i in range(1, 13):
        vals = [v for k, v in rets.items() if int(k[4:]) == i]
        avgs[i] = sum(vals) / len(vals) if vals else 0.0
    worst = min(avgs, key=lambda m: avgs[m])
    for i in range(1, 13):
        vals = sorted(v for k, v in rets.items() if int(k[4:]) == i)
        if not vals:
            continue
        avg = avgs[i]
        med = vals[len(vals) // 2]
        win = sum(1 for v in vals if v > 0) / len(vals)
        mark = "   <<< 平均最差月" if i == worst else ""
        print("      %-5s %8.2f%% %8.2f%% %9.0f%% %9.2f%% %9.2f%%%s"
              % (MONTH[i - 1], avg * 100, med * 100, win * 100,
                 vals[0] * 100, vals[-1] * 100, mark))


def section_lowmonth(ser, label, beg=None):
    days = daily(ser, beg)
    years = {}
    for d, v in days:
        years.setdefault(d[:4], []).append((d, v))
    low, high, n = {}, {}, 0
    for y, arr in years.items():
        if len(arr) < 200:
            continue
        n += 1
        lo = min(arr, key=lambda x: x[1])[0]
        hi = max(arr, key=lambda x: x[1])[0]
        low[int(lo[4:6])] = low.get(int(lo[4:6]), 0) + 1
        high[int(hi[4:6])] = high.get(int(hi[4:6]), 0) + 1
    print()
    print("  [%s] %d 个完整年份，年内最低点 / 最高点落在几月：" % (label, n))
    print("        " + " ".join("%3d月" % m for m in range(1, 13)))
    print("      低点" + " ".join("%5d" % low.get(m, 0) for m in range(1, 13)))
    print("      高点" + " ".join("%5d" % high.get(m, 0) for m in range(1, 13)))
    for name, ms in (("12-2月", (12, 1, 2)), ("1月", (1,)), ("4-6月", (4, 5, 6))):
        c = sum(low.get(m, 0) for m in ms)
        print("      低点出现在 %-6s：%2d/%2d = %3.0f%%" % (name, c, n, c / n * 100))


# ------------------------------------------------------------------ D 规则

def rule_months(ser, skip, beg=None):
    me = month_ends(ser)
    keys = [k for k in sorted(me) if not beg or k >= beg]
    last_day = {}
    for d in sorted(ser):
        last_day[d[:6]] = d
    navs = [1.0]
    days = []
    for k0, k1 in consecutive_months(keys):
        r = 0.0 if int(k1[4:]) in skip else me[k1] / me[k0] - 1
        navs.append(navs[-1] * (1 + r))
        days.append(last_day[k1])
    c, _ = cagr_of(navs, [last_day[keys[0]]] + days)
    return c, mdd_of(navs), navs


def rule_ma(ser, fast=20, slow=60, beg=None, cost=0.0005):
    days = [d for d, _ in daily(ser, beg)]
    vals = [ser[d] for d in days]
    n = len(vals)
    ma = lambda i, w: (sum(vals[i - w + 1:i + 1]) / w) if i >= w - 1 else None
    navs, in_mkt, switches = [1.0], False, 0
    for i in range(1, n):
        f, s = ma(i - 1, fast), ma(i - 1, slow)          # 用昨日收盘的信号
        want = bool(f and s and vals[i - 1] > f and f > s)
        fee = 0.0
        if want != in_mkt:
            switches += 1
            in_mkt = want
            fee = cost                                   # 换仓单边成本
        r = vals[i] / vals[i - 1] - 1 if in_mkt else 0.0  # 今天才吃到
        navs.append(navs[-1] * (1 + r) * (1 - fee))
    c, yrs = cagr_of(navs, days)
    return c, mdd_of(navs), switches, yrs


def section_rules(ser, label, beg=None):
    days = [d for d, _ in daily(ser, beg)]
    vals = [ser[d] for d in days]
    c_bh, yrs = cagr_of(vals, days)
    me = month_ends(ser)
    mk = [k for k in sorted(me) if not beg or k >= beg]
    mdd_bh_m = mdd_of([me[k] for k in mk])       # 同口径（月末采样）的买入持有回撤
    print()
    print("  [%s] %s ~ %s（%.1f 年）  买入持有：年化 %.2f%%，最大回撤 %.2f%%"
          % (label, days[0], days[-1], yrs, c_bh * 100, mdd_of(vals) * 100))
    print("      （同口径对照：月末采样下买入持有最大回撤 %.2f%%）" % (mdd_bh_m * 100))
    print("      规则                                    年化      最大回撤")
    for skip, nm in (([1], "只回避 1 月（2-12 月持有）"),
                     ([1, 2], "只回避 1-2 月"),
                     ([4, 5], "只回避 4-5 月"),
                     ([6, 7], "只回避 6-7 月")):
        c, mdd, _ = rule_months(ser, skip, beg)
        print("      %-38s %8.2f%% %10.2f%%" % (nm, c * 100, mdd * 100))
    for f, s in ((20, 60), (60, 120)):
        c, mdd, sw, y = rule_ma(ser, f, s, beg)
        print("      %-38s %8.2f%% %10.2f%%   换手 %d 次（每年 %.1f 次）"
              % ("MA%d/MA%d 多头才持有(单边万5)" % (f, s), c * 100, mdd * 100, sw, sw / y))


def section_miss_best(ser, label, beg=None):
    days = [d for d, _ in daily(ser, beg)]
    vals = [ser[d] for d in days]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    order = sorted(range(len(rets)), key=lambda i: -rets[i])
    base, _ = cagr_of(vals, days)
    print()
    print("  [%s]" % label)
    for k in (5, 10, 20):
        drop = set(order[:k])
        navs = [1.0]
        for i in range(len(rets)):
            navs.append(navs[-1] * (1 if i in drop else (1 + rets[i])))
        c, _ = cagr_of(navs, days)
        print("      错过涨得最好的 %2d 天：年化 %.2f%%（买入持有 %.2f%%）"
              % (k, c * 100, base * 100))


def section_swings(ser, label, beg=None):
    days = [d for d, _ in daily(ser, beg)]
    vals = [ser[d] for d in days]
    per_year = {}
    peak, peak_i, trough, cur_y = vals[0], 0, None, days[0][:4]
    for i, v in enumerate(vals):
        y = days[i][:4]
        if v > peak:
            if trough is not None and v / trough - 1 >= 0.08:
                per_year[cur_y] = per_year.get(cur_y, 0) + 1
            peak, peak_i, trough = v, i, None
        else:
            if v / peak - 1 <= -0.08 and trough is None:
                trough = v
            elif trough is not None and v < trough:
                trough = v
        cur_y = y
    print()
    print("  [%s] 每年「从阶段高点跌 ≥8%%、之后又涨回 8%%」的次数（事后视角）："
          % label)
    print("      " + "  ".join("%s:%d" % (y, per_year.get(y, 0))
                               for y in sorted(per_year)))


def main():
    section_dividend()

    print()
    print("=" * 78)
    print("B. 月度季节性")
    idx_div = bars.csi("H20269")
    idx_hk = bars.csi("H11141")
    idx_hs = bars.csi("H00300")
    etf = NAV.get("512890") or {}
    if idx_div:
        section_season(idx_div, "中证红利低波 H20269（含股息）", "20090101")
    if etf:
        section_season(etf, "512890 红利低波ETF（累计净值）", "20190101")
    if idx_hs:
        section_season(idx_hs, "沪深300全收益 H00300（对照）", "20090101")

    print()
    print("=" * 78)
    print("C. 年内最低点 / 最高点落在哪个月")
    if idx_div:
        section_lowmonth(idx_div, "中证红利低波 H20269", "20090101")
    if etf:
        section_lowmonth(etf, "512890 红利低波ETF", "20190101")
    if idx_hs:
        section_lowmonth(idx_hs, "沪深300全收益（对照）", "20090101")

    print()
    print("=" * 78)
    print("D. 择时规则 vs 买入持有")
    if idx_div:
        section_rules(idx_div, "中证红利低波 H20269", "20090101")
    if etf:
        section_rules(etf, "512890 红利低波ETF", "20190101")

    print()
    print("=" * 78)
    print("E. 踏空（空仓怕的不是跌，是错过上涨）")
    if etf:
        section_miss_best(etf, "512890 红利低波ETF", "20190101")
    if idx_div:
        section_miss_best(idx_div, "中证红利低波 H20269", "20090101")

    print()
    print("=" * 78)
    print("F. 一年有几次波段机会")
    if etf:
        section_swings(etf, "512890 红利低波ETF", "20190101")
    if idx_hk:
        section_swings(idx_hk, "港股通高股息 H11141", "20090101")


if __name__ == "__main__":
    main()
