# -*- coding: utf-8 -*-
"""
主板（上证指数）分时图 + 日K线图 生成模块
================================================
- 分时图：当日分时价格线 + 昨收基准 + 分钟成交量
- 日K线：K线蜡烛 + MA5 / MA10 / MA50 / MA144 均线
         下方附带成交量附图 + MACD(12,26,9) 附图
- 输出：base64 PNG 内嵌到自包含 HTML（离线可看）

数据源：腾讯行情接口（web.ifzq.gtimg.cn）
"""
import io
import math

import requests
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

import config

# 中文显示
_CN_FONTS = ["Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS"]
_avail = {f.name for f in fm.fontManager.ttflist}
for _f in _CN_FONTS:
    if _f in _avail:
        plt.rcParams["font.sans-serif"] = [_f] + list(plt.rcParams.get("font.sans-serif", []))
        break
plt.rcParams["axes.unicode_minus"] = False

# 配色（A股习惯：红涨绿跌）
RED = "#e64545"
GREEN = "#12a15d"
GRAY = "#9aa5b1"
BLUE = "#2563eb"
DARK = "#1f2d3d"
MA_COLORS = {"MA5": "#f0a500", "MA10": "#e91e63", "MA50": "#2563eb", "MA144": "#7c3aed"}

TX_H = dict(config.HEADERS)
TX_H.pop("Referer", None)


def _tx_get(url, timeout=15):
    r = requests.get(url, headers=TX_H, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ================================================================ 数据抓取

def fetch_kline(code="sh000001", n=260):
    """腾讯日K（前复权）。返回 DataFrame: date/open/close/high/low/volume。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{n},qfq"
    j = _tx_get(url)
    node = j.get("data", {}).get(code, {})
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["date", "open", "close", "high", "low", "volume"]
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna()


def fetch_minute(code="sh000001"):
    """腾讯当日分时。返回 (times, prices, vols, pre_close)；失败返回 None。"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    j = _tx_get(url)
    node = j.get("data", {}).get(code, {})
    d = node.get("data") or {}
    inner = d.get("data")
    pre_close = None
    qt = node.get("qt") or {}
    qq = (qt.get(code) or []) if isinstance(qt, dict) else None
    if qq and len(qq) > 4 and str(qq[4]) not in ("", "0"):
        try:
            pre_close = float(qq[4])
        except (TypeError, ValueError):
            pre_close = None
    if isinstance(inner, str):
        inner = inner.split(",")
    if not isinstance(inner, list) or not inner:
        return None

    times, prices, vols = [], [], []
    for item in inner:
        if isinstance(item, str):
            parts = item.strip().split()
        else:
            parts = item
        if not parts or len(parts) < 3:
            continue
        t = parts[0].strip()
        try:
            p = float(parts[1])
            v = float(parts[2])
        except (TypeError, ValueError):
            continue
        times.append(t)
        prices.append(p)
        vols.append(v)
    if not times:
        return None

    # 每分钟成交量 = 累计量差分
    vol_per_min = [max(0.0, vols[0])]
    for i in range(1, len(vols)):
        vol_per_min.append(max(0.0, vols[i] - vols[i - 1]))
    return times, prices, vol_per_min, pre_close


# ================================================================ 指标计算

def calc_indicators(df):
    """计算 MA5/10/50/144 与 MACD(12,26,9)。返回 df 副本。"""
    out = df.copy()
    c = out["close"]
    for n in (5, 10, 50, 144):
        out[f"ma{n}"] = c.rolling(n).mean()
    out["ema12"] = c.ewm(span=12, adjust=False).mean()
    out["ema26"] = c.ewm(span=26, adjust=False).mean()
    out["dif"] = out["ema12"] - out["ema26"]
    out["dea"] = out["dif"].ewm(span=9, adjust=False).mean()
    out["macd"] = 2 * (out["dif"] - out["dea"])
    return out


# ================================================================ 绘图

def _kline_png_base64(df, show_n=None):
    """日K线 + 均线 + 成交量 + MACD 三面板图，返回 base64 PNG。

    show_n: 仅显示最近 show_n 个交易日（指标需在更长历史上已计算好）。
    """
    if show_n and len(df) > show_n:
        df = df.tail(show_n).reset_index(drop=True)
    n = len(df)
    if n < 5:
        return None
    x = np.arange(n)
    up = df["close"].values >= df["open"].values
    upc = np.where(up, RED, GREEN)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(11, 9),
        gridspec_kw={"height_ratios": [3.6, 1.0, 1.2]},
        sharex=True,
    )
    fig.subplots_adjust(left=0.065, right=0.97, top=0.95, bottom=0.06, hspace=0.08)

    # --- 上：K线 + 均线 ---
    ax1.vlines(x, df["low"].values, df["high"].values, color=upc, linewidth=0.9, zorder=2)
    body_bottom = np.minimum(df["open"].values, df["close"].values)
    body_hei = np.abs(df["close"].values - df["open"].values)
    ax1.bar(x, body_hei, bottom=body_bottom, width=0.7, color=upc,
            edgecolor=upc, linewidth=0.6, zorder=3, align="center")

    last_close = float(df["close"].iloc[-1])
    for n_, color in ((5, MA_COLORS["MA5"]), (10, MA_COLORS["MA10"]),
                      (50, MA_COLORS["MA50"]), (144, MA_COLORS["MA144"])):
        col = f"ma{n_}"
        if col not in df.columns:
            continue
        ma = df[col].values
        if len(ma) == 0 or not np.isfinite(ma[-1]):
            continue
        ax1.plot(x, ma, color=color, linewidth=1.2, zorder=4,
                 label=f"MA{n_} {ma[-1]:.1f}")

    ax1.set_title(f"上证指数 · 日K线（近半年，{n}个交易日）　最新 {last_close:.2f}　"
                  f"MA5/10/50/144 / 成交量 / MACD", fontsize=13, color=DARK, pad=10)
    ax1.legend(loc="upper left", fontsize=9, ncol=4, frameon=False)
    ax1.grid(axis="y", color="#e6eaf0", linewidth=0.8)
    ax1.set_ylabel("点位", fontsize=10)
    ax1.tick_params(axis="y", labelsize=9)

    # --- 中：成交量 ---
    vol = df["volume"].values / 1e8  # 亿手（实际为成交手数，仅展示量级）
    ax2.bar(x, vol, color=upc, width=0.7, align="center")
    ax2.grid(axis="y", color="#e6eaf0", linewidth=0.8)
    ax2.set_ylabel("成交量(亿手)", fontsize=9)
    ax2.tick_params(axis="y", labelsize=8)

    # --- 下：MACD ---
    macd = df["macd"].values
    dif = df["dif"].values
    dea = df["dea"].values
    ax3.bar(x, macd, color=np.where(macd >= 0, RED, GREEN), width=0.7, align="center", zorder=2)
    ax3.plot(x, dif, color="#f0a500", linewidth=1.0, label=f"DIF {dif[-1]:.1f}", zorder=3)
    ax3.plot(x, dea, color="#2563eb", linewidth=1.0, label=f"DEA {dea[-1]:.1f}", zorder=3)
    ax3.axhline(0, color="#9aa5b1", linewidth=0.8)
    ax3.legend(loc="upper left", fontsize=9, ncol=2, frameon=False)
    ax3.grid(axis="y", color="#e6eaf0", linewidth=0.8)
    ax3.set_ylabel("MACD", fontsize=9)
    ax3.tick_params(axis="y", labelsize=8)

    # X 轴日期刻度（约 6 个）
    step = max(1, n // 6)
    ticks = list(range(0, n, step))
    ax3.set_xticks(ticks)
    ax3.set_xticklabels([df["date"].iloc[i][5:] for i in ticks], fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    import base64
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _minute_png_base64(times, prices, vols, pre_close):
    """分时图：价格线 + 昨收基准 + 分钟成交量，返回 base64 PNG。"""
    n = len(prices)
    if n < 2:
        return None
    x = np.arange(n)
    p = np.array(prices)
    pre = pre_close if pre_close else float(p[0])

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 5.2),
        gridspec_kw={"height_ratios": [3.0, 1.0]},
        sharex=True,
    )
    fig.subplots_adjust(left=0.07, right=0.93, top=0.93, bottom=0.12, hspace=0.06)

    up = p >= pre
    base_color = BLUE
    ax1.plot(x, p, color=base_color, linewidth=1.5)
    ax1.axhline(pre, color=GRAY, linewidth=1.0, linestyle="--")
    ax1.fill_between(x, p, pre, where=(p >= pre), color=RED, alpha=0.08)
    ax1.fill_between(x, p, pre, where=(p < pre), color=GREEN, alpha=0.08)

    # 右轴：涨跌幅
    pct = (p - pre) / pre * 100
    ax1b = ax1.twinx()
    ax1b.plot(x, pct, color="none")
    ax1b.set_ylim(pct.min() - 0.3, pct.max() + 0.3)
    ax1b.set_ylabel("涨跌幅 %", fontsize=9)
    ax1b.tick_params(axis="y", labelsize=8)

    last_pct = (p[-1] - pre) / pre * 100
    ax1.set_title(
        f"上证指数 · 分时　最新 {p[-1]:.2f}　昨收 {pre:.2f}　涨跌 {last_pct:+.2f}%",
        fontsize=13, color=DARK, pad=10,
    )
    ax1.grid(axis="y", color="#e6eaf0", linewidth=0.8)
    ax1.set_ylabel("点位", fontsize=10)
    ax1.tick_params(axis="y", labelsize=9)

    # 成交量
    vmax = max(max(vols, default=1) / 1e6, 1)
    ax2.bar(x, np.array(vols) / 1e6, color=np.where(up, RED, GREEN), width=0.7, align="center")
    ax2.set_ylabel("分钟量(百万手)", fontsize=9)
    ax2.set_ylim(0, vmax * 1.15)
    ax2.tick_params(axis="y", labelsize=8)
    ax2.grid(axis="y", color="#e6eaf0", linewidth=0.8)

    # 时间刻度
    idx_map = {"0930": 0, "1030": 60, "1130": 120, "1330": 181, "1430": 241, "1500": n - 1}
    labs, pos = [], []
    for k in ("0930", "1030", "1130", "1330", "1430", "1500"):
        for i, t in enumerate(times):
            if t == k:
                pos.append(i)
                labs.append(f"{k[:2]}:{k[2:]}")
                break
    if len(pos) < 2:  # fallback 均匀刻度
        pos = [0, n // 4, n // 2, (3 * n) // 4, n - 1]
        labs = ["09:30", "10:30", "11:30", "14:00", "15:00"]
    ax2.set_xticks(pos)
    ax2.set_xticklabels(labs, fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    import base64
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ================================================================ 对外入口

def generate_index_chart_section(code="sh000001"):
    """生成「主板（上证指数）分时 + 日K」HTML 片段；失败返回提示文案。"""
    try:
        kdf = fetch_kline(code, 260)
        minute = fetch_minute(code)
    except Exception as e:
        return (f'<div class="hint" style="margin:10px 0;">'
                f'主板行情图获取失败（{e.__class__.__name__}），请稍后重试。</div>')

    kimg = None
    if len(kdf):
        _kdf = calc_indicators(kdf)   # 在完整历史上算指标（MA144 需要 144+ 天）
        kimg = _kline_png_base64(_kdf, show_n=125)   # 仅显示最近半年（约125个交易日）
    mimg = _minute_png_base64(*minute) if minute else None

    out = ['<div style="margin-top:14px;">']
    if mimg:
        out.append(f'<img src="data:image/png;base64,{mimg}" '
                   f'alt="上证指数分时图" style="width:100%;height:auto;display:block;'
                   f'border:1px solid #eef1f6;border-radius:8px;"/>')
    else:
        out.append('<div class="hint">分时图获取失败（盘前或接口异常）</div>')
    if kimg:
        out.append(f'<img src="data:image/png;base64,{kimg}" '
                   f'alt="上证指数日K线" style="width:100%;height:auto;display:block;margin-top:14px;'
                   f'border:1px solid #eef1f6;border-radius:8px;"/>')
    else:
        out.append('<div class="hint">日K线图获取失败</div>')
    out.append('</div>')
    return "".join(out)


if __name__ == "__main__":
    html = generate_index_chart_section()
    print("生成片段长度:", len(html))
    print("包含分时图:", "上证指数 · 分时" in html)
    print("包含日K图:", "上证指数 · 日K线" in html)
