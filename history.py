# -*- coding: utf-8 -*-
"""历史快照存储（SQLite）—— 把「当日快照」变成可回溯的时间序列
====================================================================
此前每轮运行只覆盖 output/latest_market.json 与 output/index.html，历史数据被
直接丢弃，于是「比昨天怎么样」「涨跌家数这一周怎么走」这类问题无法回答。

本模块把每轮采集结果 + 数据源健康状态落到 output/history.db：
  - 只用标准库 sqlite3，零新增依赖；
  - 单文件（output/history.db），可直接复制 / 备份 / 用任意 SQLite 工具打开；
  - 打平出常用列（涨跌家数、涨跌停数、上证收盘与涨跌幅、领涨板块）便于查询，
    同时保留完整 payload JSON（原始结构，方便二次分析）。

对外接口：
    record_run(data, health=None, note=None) -> int   # 落一轮快照，返回 snapshot_id
    latest() / previous() -> dict|None                # 最近一轮 / 再上一轮完整快照
    recent(limit=10) -> [dict]                        # 最近 N 轮概要（不含 payload）
    series(field, days=30) -> [(ts, value)]           # 某指标的时间序列
    compare() -> dict                                 # 最近一轮 vs 上一轮的关键差异
    report(days=7) -> str                             # 人类可读摘要

命令行：
    python history.py                        # 最近 7 轮概要 + 环比
    python history.py --days 30
    python history.py --field breadth_up --days 30
    python history.py --json                 # JSON 输出，便于二次处理
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

import config

# 可查询的打平列（白名单：既用于建表，也用于 series() 的参数校验，避免 SQL 注入）
FIELDS = (
    "breadth_up", "breadth_down", "breadth_flat", "breadth_total",
    "limit_up", "limit_down",
    "sh_close", "sh_pct", "sh_amount",
    "lead_sector_pct",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,   -- 采集时间（data['time']）
    recorded_at    TEXT NOT NULL,   -- 落库时间
    breadth_up     INTEGER,         -- 上涨家数
    breadth_down   INTEGER,         -- 下跌家数
    breadth_flat   INTEGER,         -- 平盘家数
    breadth_total  INTEGER,         -- 覆盖股票数
    limit_up       INTEGER,         -- 涨停家数
    limit_down     INTEGER,         -- 跌停家数
    sh_close       REAL,            -- 上证收盘价
    sh_pct         REAL,            -- 上证涨跌幅(%)
    sh_amount      REAL,            -- 上证成交额
    lead_sector    TEXT,            -- 领涨行业板块
    lead_sector_pct REAL,           -- 领涨板块涨幅(%)
    note           TEXT,            -- 备注（如运行模式）
    payload        TEXT NOT NULL    -- 完整 data JSON
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS source_health (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    name        TEXT NOT NULL,      -- 来源代号（em / emdelay / tx / sina ...）
    state       TEXT,               -- 正常 / 冷却中
    ok          INTEGER,            -- 累计成功次数
    fail        INTEGER,            -- 累计失败次数
    fails       INTEGER,            -- 连续失败次数
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
);
CREATE INDEX IF NOT EXISTS idx_health_snapshot ON source_health(snapshot_id);

CREATE TABLE IF NOT EXISTS risk_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    total       REAL,
    level_name  TEXT,
    dims        TEXT,
    signals     TEXT,
    refs        TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_risk_ts ON risk_snapshots(ts);
"""


# ---------------------------------------------------------------- 基础

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _db_path(db_path=None):
    return db_path or os.path.join(config.OUTPUT_DIR, "history.db")


def _connect(db_path=None):
    path = _db_path(db_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _num(v):
    """安全转数值（int 保持 int，其余转 float）；失败返回 None。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract(data):
    """从 collector.collect_all() 的 data 提取打平列。"""
    data = data or {}
    breadth = data.get("breadth") or {}
    limit_up = data.get("limit_up") or {}
    limit_down = data.get("limit_down") or {}

    sh = {}
    for item in (data.get("indices") or []):
        if "上证" in str((item or {}).get("name") or ""):
            sh = item or {}
            break

    lead = (data.get("industry_up") or [None])[0] or {}

    total = breadth.get("total")
    if total is None and breadth:
        parts = [_num(breadth.get(k)) or 0 for k in ("up", "down", "flat")]
        total = sum(parts) or None

    return {
        "ts": str(data.get("time") or _now()),
        "recorded_at": _now(),
        "breadth_up": _num(breadth.get("up")),
        "breadth_down": _num(breadth.get("down")),
        "breadth_flat": _num(breadth.get("flat")),
        "breadth_total": _num(total),
        "limit_up": _num(limit_up.get("count")),
        "limit_down": _num(limit_down.get("count")),
        "sh_close": _num(sh.get("price")),
        "sh_pct": _num(sh.get("change_pct")),
        "sh_amount": _num(sh.get("amount")),
        "lead_sector": (lead.get("name") or None),
        "lead_sector_pct": _num(lead.get("change_pct")),
    }


def _decode(row):
    """sqlite3.Row -> dict，并把 payload JSON 解析回 data。"""
    if row is None:
        return None
    out = dict(row)
    raw = out.pop("payload", None)
    try:
        out["data"] = json.loads(raw) if raw else None
    except Exception:
        out["data"] = None
    return out


# ---------------------------------------------------------------- 写入

def record_run(data, health=None, note=None, db_path=None):
    """落一轮快照（含数据源健康状态），返回 snapshot_id。

    health: sources.health_snapshot() 的结果（[{name, state, ok, fail, fails}]）。
    落库失败时抛异常，由调用方决定是否影响主流程（main.py 里是「不影响」）。
    """
    row = _extract(data)
    row["payload"] = json.dumps(data or {}, ensure_ascii=False)
    row["note"] = note

    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO snapshots (ts, recorded_at, breadth_up, breadth_down, "
            "breadth_flat, breadth_total, limit_up, limit_down, sh_close, sh_pct, "
            "sh_amount, lead_sector, lead_sector_pct, note, payload) VALUES "
            "(:ts, :recorded_at, :breadth_up, :breadth_down, :breadth_flat, "
            ":breadth_total, :limit_up, :limit_down, :sh_close, :sh_pct, "
            ":sh_amount, :lead_sector, :lead_sector_pct, :note, :payload)",
            row)
        sid = cur.lastrowid
        for h in (health or []):
            conn.execute(
                "INSERT INTO source_health (snapshot_id, name, state, ok, fail, fails) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, h.get("name"), h.get("state"), h.get("ok"),
                 h.get("fail"), h.get("fails")))
        conn.commit()
        return sid
    finally:
        conn.close()


def record_risk(res, refs=None, note=None, db_path=None):
    """落一条空中飞人指数快照，供分层减仓和后续回测使用。"""
    if not res:
        return None
    row = {
        "ts": str(res.get("time") or _now()),
        "recorded_at": _now(),
        "total": _num(res.get("total")),
        "level_name": res.get("level_name"),
        "dims": json.dumps(res.get("dims") or [], ensure_ascii=False),
        "signals": json.dumps(res.get("signals") or [], ensure_ascii=False),
        "refs": json.dumps(refs or [], ensure_ascii=False),
        "note": note,
    }
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO risk_snapshots (ts, recorded_at, total, level_name, "
            "dims, signals, refs, note) VALUES (:ts, :recorded_at, :total, "
            ":level_name, :dims, :signals, :refs, :note)", row)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------- 查询

_SUMMARY_COLS = ("id, ts, recorded_at, breadth_up, breadth_down, breadth_flat, "
                 "breadth_total, limit_up, limit_down, sh_close, sh_pct, "
                 "lead_sector, lead_sector_pct")


def latest(db_path=None):
    """最近一轮完整快照（含 payload 解析出的 data）；无记录返回 None。"""
    conn = _connect(db_path)
    try:
        return _decode(conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone())
    finally:
        conn.close()


def previous(db_path=None):
    """再上一轮快照；不足两轮返回 None。"""
    conn = _connect(db_path)
    try:
        return _decode(conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1 OFFSET 1").fetchone())
    finally:
        conn.close()


def recent(limit=10, db_path=None):
    """最近 N 轮概要（不含 payload，按时间倒序）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_SUMMARY_COLS} FROM snapshots ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def source_health(snapshot_id, db_path=None):
    """某轮快照的来源健康明细。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, state, ok, fail, fails FROM source_health "
            "WHERE snapshot_id = ? ORDER BY name", (int(snapshot_id),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def series(field, days=30, db_path=None):
    """某指标的时间序列 [(ts, value)]，按时间升序；field 必须在 FIELDS 白名单内。"""
    if field not in FIELDS:
        raise ValueError(f"未知指标 {field!r}，可选：{', '.join(FIELDS)}")
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT ts, {field} AS value FROM snapshots "
            "WHERE ts >= ? ORDER BY ts ASC, id ASC", (cutoff,)).fetchall()
        return [(r["ts"], r["value"]) for r in rows]
    finally:
        conn.close()


def risk_latest(db_path=None):
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM risk_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        out = dict(row)
        for key in ("dims", "signals", "refs"):
            try:
                out[key] = json.loads(out.get(key) or "[]")
            except Exception:
                out[key] = []
        return out
    finally:
        conn.close()


def risk_series(db_path=None):
    """返回空中飞人总分的 [(ts, total)]，按时间升序。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT ts, total FROM risk_snapshots "
            "WHERE total IS NOT NULL ORDER BY ts ASC, id ASC").fetchall()
        return [(r["ts"], r["total"]) for r in rows]
    finally:
        conn.close()


def compare(db_path=None):
    """最近一轮 vs 上一轮的关键差异；数据不足时返回 {}。"""
    cur, prev = latest(db_path), previous(db_path)
    if not cur or not prev:
        return {}
    out = {"ts": cur.get("ts"), "prev_ts": prev.get("ts")}
    for f in FIELDS:
        a, b = cur.get(f), prev.get(f)
        out[f] = {"cur": a, "prev": b,
                  "delta": (a - b) if isinstance(a, (int, float))
                  and isinstance(b, (int, float)) else None}
    out["lead_sector"] = {"cur": cur.get("lead_sector"), "prev": prev.get("lead_sector")}
    return out


# ---------------------------------------------------------------- 人类可读摘要

def _fmt(v, digits=2, suffix=""):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}{suffix}"
    return f"{v}{suffix}"


def _delta(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f" ({v:+.2f})"
    return f" ({v:+d})"


def report(days=7, db_path=None, as_json=False):
    """最近 N 轮概要 + 环比摘要（CLI 默认输出）。"""
    rows = recent(limit=days, db_path=db_path)
    cmp_ = compare(db_path=db_path)
    body = {"count": len(rows), "snapshots": rows, "compare": cmp_}
    if as_json:
        return json.dumps(body, ensure_ascii=False, indent=2)

    if not rows:
        return ("历史库还是空的。先运行一次 python main.py（或 python collector.py）"
                "采集数据，之后这里就能看到趋势。")

    lines = [f"历史快照：共 {len(rows)} 轮（最近在前，最多 {days} 轮）", ""]
    lines.append(f"{'时间':<20}{'涨/跌':>12}{'涨停/跌停':>12}{'上证':>18}  领涨板块")
    for r in rows:
        up, down = r.get("breadth_up"), r.get("breadth_down")
        lu, ld = r.get("limit_up"), r.get("limit_down")
        sh = r.get("sh_close")
        pct = r.get("sh_pct")
        sh_txt = f"{_fmt(sh)} ({_fmt(pct, 2, '%')})" if sh is not None else "-"
        # 注意：这几段宽度要和表头的 12/12/18 对齐，且两两之间必须留分隔，
        # 否则「1038/4358」会和「38/4」粘成「1038/  435838/       4」。
        updown = f"{_fmt(up)}/{_fmt(down)}"
        limitupdown = f"{_fmt(lu)}/{_fmt(ld)}"
        lines.append(
            f"{str(r.get('ts'))[:19]:<20}"
            f"{updown:>12}"
            f"{limitupdown:>12}"
            f"{sh_txt:>18}  {r.get('lead_sector') or '-'}")

    if cmp_:
        lines += ["", f"环比（{cmp_.get('ts')} vs {cmp_.get('prev_ts')}）："]
        names = {
            "breadth_up": "上涨家数", "breadth_down": "下跌家数",
            "limit_up": "涨停家数", "limit_down": "跌停家数",
            "sh_close": "上证收盘", "sh_pct": "上证涨跌幅",
            "breadth_total": "覆盖股票数", "lead_sector_pct": "领涨板块涨幅",
        }
        for f, label in names.items():
            d = cmp_.get(f) or {}
            if d.get("cur") is None and d.get("prev") is None:
                continue
            lines.append(f"  {label:<12} {_fmt(d.get('prev'))} → {_fmt(d.get('cur'))}"
                         f"{_delta(d.get('delta'))}")
        ls = cmp_.get("lead_sector") or {}
        if ls.get("cur") != ls.get("prev"):
            lines.append(f"  {'领涨板块':<12} {ls.get('prev') or '-'} → {ls.get('cur') or '-'}")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="历史快照查询（output/history.db）")
    parser.add_argument("--days", type=int, default=7, help="回看轮数 / 天数（默认 7）")
    parser.add_argument("--field", help=f"输出某指标的时间序列，可选：{', '.join(FIELDS)}")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--db", help="指定数据库路径（默认 output/history.db）")
    args = parser.parse_args(argv)

    if args.field:
        try:
            pts = series(args.field, args.days, db_path=args.db)
        except ValueError as e:
            print(f"[错误] {e}")
            return 2
        if args.json:
            print(json.dumps(pts, ensure_ascii=False, indent=2))
        elif not pts:
            print(f"「{args.field}」在最近 {args.days} 天没有数据。")
        else:
            print(f"「{args.field}」最近 {args.days} 天：")
            for ts, v in pts:
                print(f"  {ts}  {_fmt(v)}")
        return 0

    print(report(args.days, db_path=args.db, as_json=args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
