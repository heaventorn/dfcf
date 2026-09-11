# -*- coding: utf-8 -*-
"""临时离线验证脚本(scratch,用完即删):P2-13 历史快照落库与查询验证。

不需要网络,不写项目目录(用临时目录里的 sqlite 文件)。
运行: python _p2_verify_tmp.py
"""
import json
import os
import sys
import tempfile

ROOT = r"c:\Users\Admin\Desktop\dfcf-main\dfcf-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import history  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   << " + str(detail)))
    if not cond:
        FAILS.append(name)


db = os.path.join(tempfile.gettempdir(), "dfcf_history_test.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(db + suffix)
    except OSError:
        pass

DATA1 = {
    "time": "2026-01-05 15:00:00",
    "indices": [{"name": "上证指数", "price": 3200.5, "change_pct": 0.85, "amount": 4.2e11}],
    "breadth": {"total": 5200, "up": 3100, "down": 1800, "flat": 300},
    "limit_up": {"count": 42, "items": []},
    "limit_down": {"count": 5, "items": []},
    "industry_up": [{"name": "半导体", "change_pct": 3.2}],
}
DATA2 = {
    "time": "2026-01-06 15:00:00",
    "indices": [{"name": "上证指数", "price": 3180.0, "change_pct": -0.64, "amount": 3.9e11}],
    "breadth": {"total": 5200, "up": 1500, "down": 3500, "flat": 200},
    "limit_up": {"count": 18, "items": []},
    "limit_down": {"count": 23, "items": []},
    "industry_up": [{"name": "银行", "change_pct": 1.1}],
}
HEALTH = [{"name": "em", "state": "正常", "ok": 5, "fail": 1, "fails": 0},
          {"name": "tx", "state": "冷却中", "ok": 2, "fail": 3, "fails": 2}]

sid1 = history.record_run(DATA1, health=HEALTH, note="test", db_path=db)
sid2 = history.record_run(DATA2, health=HEALTH, note="test", db_path=db)
check("落库返回递增 id", sid1 == 1 and sid2 == 2, (sid1, sid2))

latest = history.latest(db_path=db)
check("latest 取到最新一轮", bool(latest) and latest["ts"] == DATA2["time"],
      latest and latest["ts"])
check("payload 解析回完整 data",
      bool(latest) and latest["data"]["limit_up"]["count"] == 18)
check("打平列 breadth_down 正确", bool(latest) and latest["breadth_down"] == 3500,
      latest and latest["breadth_down"])
check("上证价格提取正确", bool(latest) and abs(latest["sh_close"] - 3180.0) < 1e-6,
      latest and latest["sh_close"])
check("领涨板块提取正确", bool(latest) and latest["lead_sector"] == "银行",
      latest and latest["lead_sector"])

prev = history.previous(db_path=db)
check("previous 取到上一轮", bool(prev) and prev["ts"] == DATA1["time"])

cmp_ = history.compare(db_path=db)
check("compare 上涨家数差值正确", cmp_["breadth_up"]["delta"] == 1500 - 3100, cmp_["breadth_up"])
check("compare 涨停家数差值正确", cmp_["limit_up"]["delta"] == 18 - 42, cmp_["limit_up"])
check("compare 领涨板块切换正确", cmp_["lead_sector"]["prev"] == "半导体"
      and cmp_["lead_sector"]["cur"] == "银行", cmp_["lead_sector"])

rows = history.recent(limit=10, db_path=db)
check("recent 返回两轮", len(rows) == 2, len(rows))
check("recent 不含 payload/data", "payload" not in rows[0] and "data" not in rows[0])

pts = history.series("breadth_up", days=3650, db_path=db)
check("series 时间序列升序", [p[1] for p in pts] == [3100, 1500], pts)

h = history.source_health(sid1, db_path=db)
check("来源健康明细落库", len(h) == 2 and h[0]["name"] == "em", h)

txt = history.report(7, db_path=db)
check("report 文本含环比段", "环比" in txt and "上涨家数" in txt)
js = history.report(7, db_path=db, as_json=True)
check("report --json 合法 JSON", json.loads(js)["count"] == 2)

try:
    history.series("no_such_field", db_path=db)
    check("未知指标被拒绝(防注入)", False, "未抛异常")
except ValueError:
    check("未知指标被拒绝(防注入)", True)

try:
    print()
    print(txt)
except Exception:
    pass

for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(db + suffix)
    except OSError:
        pass

print()
if FAILS:
    print("!! FAILED %d:" % len(FAILS), FAILS)
    sys.exit(1)
print("ALL PASS")
