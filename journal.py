# -*- coding: utf-8 -*-
"""事件日志：一行一条、只追加、不改写。

为什么要有它：体检那几档（monitor.py）每次只留**最新一份**结论，台账
（ledger.py）记的是钱，中间缺了「什么时候发生了什么事」—— 闸门哪天翻的、
哪笔计划什么时候执行的、哪个月报了警、模型当时说了什么。半年后要复盘
「当时为什么这么做」，靠的就是这一份，页面上任何一块都不能替代它。

存储：
    output/journal.jsonl        正文，追加写，一行一条 JSON
    output/journal_state.json   只存「上一次的值」，用来做「变了才记」

「变了才记」用在闸门 / 风险乘数这种每天都会算、但只在翻转那天才有意义的
东西上：log_change() 对比上次的值，一样就不写。第一跑没有历史值时也会记
一条，那是「基线」，不是误报。

用法：
    py journal.py tail --limit 30
    py journal.py write --kind 系统 --title "手工记一笔" --detail "..."
    py journal.py --json tail
"""

from __future__ import print_function

import argparse
import datetime
import io
import json
import os
import sys
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config

FILE = os.path.join(config.OUTPUT_DIR, "journal.jsonl")
STATE = os.path.join(config.OUTPUT_DIR, "journal_state.json")

# 类型（页面上的筛选就是这几个）
KINDS = ("建仓", "执行", "闸门", "风险", "报警", "评估", "模型", "账本", "系统")
# 级别：info 记个数 · act 动手了 · warn 要留意 · alert 报警线触发
LEVELS = ("info", "act", "warn", "alert")

MAX_LINES = 8000          # 超过就只留最后这么多行，老的滚掉
_lock = threading.Lock()


# ------------------------------------------------------------------ 小工具

def _now(at=None):
    if at is None:
        return datetime.datetime.now()
    if isinstance(at, datetime.datetime):
        return at
    return datetime.datetime.now()


def _dump(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _rotate():
    """文件太长了就只留最后 MAX_LINES 行。只在追加后顺手看一眼大小。"""
    try:
        if os.path.getsize(FILE) < 2 * 1024 * 1024:
            return
        with open(FILE, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if len(lines) <= MAX_LINES:
            return
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_LINES:])
        os.replace(tmp, FILE)
    except Exception:
        pass


# ------------------------------------------------------------------ 写

def log(kind, title, detail="", level="info", sid=None, ref=None, at=None):
    """追加一条。写不进去（磁盘满 / 权限）也不抛 —— 记日志失败不该弄挂主流程。"""
    t = _now(at)
    e = {"at": t.strftime("%Y-%m-%d %H:%M:%S"),
         "date": t.strftime("%Y%m%d"),
         "kind": kind or "系统",
         "level": level if level in LEVELS else "info",
         "title": title or "",
         "detail": detail or "",
         "sid": sid,
         "ref": ref}
    with _lock:
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            with open(FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
        except Exception:
            return e
        _rotate()
    return e


def state(key, default=None):
    return _load_json(STATE, {}).get(key, default)


def all_state():
    return _load_json(STATE, {})


def set_state(key, value):
    d = _load_json(STATE, {})
    if not isinstance(d, dict):
        d = {}
    d[key] = value
    return _dump(STATE, d)


def log_change(key, value, kind, title, detail="", level="info",
               sid=None, ref=None):
    """值变了才记一条（并记下「从什么变成什么」）。没变返回 None。

    判断依据是 output/journal_state.json，不是日志本身 —— 所以日志可以随便
    删、随便归档，不会因为「翻不到上一条」而误报一串翻转。
    """
    prev = state(key)
    if prev == value:
        return None
    set_state(key, value)
    e = log(kind, title, detail, level=level, sid=sid, ref=ref)
    e["prev"] = prev
    return e


# ------------------------------------------------------------------ 读

def read(limit=120, kind=None, sid=None, level=None, newest_first=True):
    out = []
    try:
        with open(FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if kind and e.get("kind") != kind:
                    continue
                if sid and e.get("sid") != sid:
                    continue
                if level and e.get("level") != level:
                    continue
                out.append(e)
    except Exception:
        return []
    out = out[-int(limit):] if limit else out
    return out[::-1] if newest_first else out


def stats():
    """页面上那行「共 N 条 · 起 至 止 · 各类型多少」。"""
    es = read(limit=0, newest_first=False)
    by = {}
    for e in es:
        by[e.get("kind") or "-"] = by.get(e.get("kind") or "-", 0) + 1
    return {"ok": True, "total": len(es),
            "first": (es[0] or {}).get("at") if es else None,
            "last": (es[-1] or {}).get("at") if es else None,
            "by_kind": by, "kinds": list(KINDS),
            "state": all_state()}


# ------------------------------------------------------------------ 命令行

def main():
    ap = argparse.ArgumentParser(description="事件日志（只追加）")
    ap.add_argument("cmd", nargs="?", default="tail", choices=["tail", "write", "stats"])
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--kind", default="系统")
    ap.add_argument("--title", default="")
    ap.add_argument("--detail", default="")
    ap.add_argument("--level", default="info")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    if a.cmd == "write":
        e = log(a.kind, a.title or "(无标题)", a.detail, level=a.level)
        print("已记：", json.dumps(e, ensure_ascii=False))
        return 0
    if a.cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
        return 0

    items = read(limit=a.limit)
    if a.json:
        print(json.dumps(items, ensure_ascii=False, indent=1))
        return 0
    st = stats()
    print("共 %d 条 · 起 %s · 止 %s" % (st["total"], st["first"] or "-",
                                        st["last"] or "-"))
    for e in items:
        print("%s [%s] %-4s %s %s"
              % (e.get("at"), e.get("kind"), e.get("level") or "",
                 e.get("title"), ("— " + e["detail"]) if e.get("detail") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
