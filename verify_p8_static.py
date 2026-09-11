# -*- coding: utf-8 -*-
"""验证脚本（写在项目工作区外，可随意删除）：第 8 项「内联资源外置」的等价性验证。

做三件事：
  1) 资源常量对照：迁移后的 HOME_CSS / HOME_JS / _PAGE_JS / _PAGE_HTML 的 sha256，
     必须与 _refactor_backup/_baseline_assets.json 里记录的「迁移前」哈希完全一致
     —— 这直接证明外置是纯搬运，一个字符都没变。
  2) 产物对照：用冻结的确定性 payload 重新生成 index.html 与事件页，
     与 _refactor_backup/_baseline_home.html / _baseline_events.html 逐字节比对。
  3) static/ 文件存在性检查。

全部 PASS ⇒ 外置零回归。

运行：python _p8_verify_tmp.py
（需先执行过 python migrate_inline_assets.py）
"""
import hashlib
import json
import os
import sys
import traceback

ROOT = r"c:\Users\Admin\Desktop\dfcf-main\dfcf-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   << " + str(detail)))
    if not cond:
        FAILS.append(name)


BAK = os.path.join(ROOT, "_refactor_backup")
base_path = os.path.join(BAK, "_baseline_assets.json")
if not os.path.isfile(base_path):
    print("!! 找不到基线文件：", base_path)
    print("   （它由建立基线的那一步生成；若已被清理，请改用 _p1_verify_tmp.py 之外的方式核对）")
    sys.exit(2)

with open(base_path, encoding="utf-8") as f:
    base_assets = json.load(f)

import econ_calendar  # noqa: E402
import events  # noqa: E402
import home  # noqa: E402
import static_assets  # noqa: E402

print("== 1) 资源常量哈希对照（迁移前 vs 迁移后） ==")
cur = {
    "home.HOME_CSS": home.HOME_CSS,
    "home.HOME_JS": home.HOME_JS,
    "events._PAGE_JS": events._PAGE_JS,
    "events._PAGE_HTML": events._PAGE_HTML,
}
for key, val in cur.items():
    want = (base_assets.get(key) or {}).get("sha256")
    got = hashlib.sha256(val.encode("utf-8")).hexdigest()
    check(f"{key} 哈希一致", want == got, f"want={want} got={got} len={len(val)}")

print("== 2) static/ 文件存在性 ==")
for f in ("home.css", "home.js", "events_page.js", "events_page.html",
          "positions_page.html"):
    check(f"static/{f} 存在", static_assets.exists(f))

print("== 3) 产物逐字节对照 ==")
# 冻结网络依赖，构造与建基线时完全相同的确定性 payload
econ_calendar.fetch_calendar = lambda *a, **k: []
DATA = {
    "time": "2026-01-01 09:30:00", "indices": [], "breadth": {},
    "limit_up": {"count": 0, "items": []}, "limit_down": {"count": 0, "items": []},
    "industry_up": [], "industry_down": [], "concept_up": [],
    "stock_up": [], "stock_down": [], "news": [],
}
try:
    payload = home.build_payload(DATA, events=[])
    out = os.path.join(BAK, "_after_home.html")
    home.build_home([], payload, out, assets_prefix="../assets/", chart_data=None)
    new = open(out, "rb").read()
    old = open(os.path.join(BAK, "_baseline_home.html"), "rb").read()
    check("index.html 与重构前逐字节一致", new == old,
          f"after={len(new)}B sha256={hashlib.sha256(new).hexdigest()[:16]} / "
          f"before={len(old)}B sha256={hashlib.sha256(old).hexdigest()[:16]}")

    ep = os.path.join(BAK, "_after_events.html")
    events.build_page([], ep)
    new2 = open(ep, "rb").read()
    old2 = open(os.path.join(BAK, "_baseline_events.html"), "rb").read()
    check("事件页与重构前逐字节一致", new2 == old2,
          f"after={len(new2)}B / before={len(old2)}B")
except Exception:
    traceback.print_exc()
    check("产物生成未抛异常", False, "见上方堆栈")

print()
if FAILS:
    print("!! FAILED %d:" % len(FAILS), FAILS)
    sys.exit(1)
print("ALL PASS —— 外置等价，产物与重构前完全一致")
