# -*- coding: utf-8 -*-
"""一次性迁移：把内联在 Python 里的 CSS / JS / HTML 外置到 static/
=====================================================================
为什么要做
----------
home.py / events.py / position_manager.py 里各有一段巨大的三引号字符串常量
（CSS / JS / HTML），后果是：改一行样式要动 Python；没有语法高亮与检查；
字符串里的转义（r-string / \\n / \\" ...）极易写错。

本脚本把它们的**解释后的值**原样写到 static/ 下的真实文件，并把定义处替换成
「读文件」形式：

    HOME_CSS = \"\"\"
    <style> ... </style>
    \"\"\"                      →      HOME_CSS = _assets.load("home.css")

为什么用脚本而不是手抄
----------------------
直接搬运源码文本需要人肉判断转义（r-string 的 \\n 与普通字符串的 \\n 含义不同）。
本脚本取的是 Python 解释后的字符串值，写入文件后立刻回读并**逐字节比对**，
因此不存在转义风险；比对不一致会直接报错并中止（不落盘改写）。

用法
----
    python migrate_inline_assets.py --dry-run   # 只看计划，不改任何文件
    python migrate_inline_assets.py             # 执行迁移（自动备份被改文件）

迁移后
------
    改样式 / JS  →  直接编辑 static/*.css | *.js（Python 侧不用动）
    校验等价性  →  python _p8_verify_tmp.py（对比重构前后的产物字节）
"""
import argparse
import hashlib
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.chdir(BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (模块名, 属性名, 目标文件) —— 值原样落到 static/<目标文件>
JOBS = [
    ("home", "HOME_CSS", "home.css"),
    ("home", "HOME_JS", "home.js"),
    ("events", "_PAGE_JS", "events_page.js"),
    ("events", "_PAGE_HTML", "events_page.html"),
    ("position_manager", "PAGE_HTML", "positions_page.html"),
]

# 每个被改写的 .py 都需要这个导入
IMPORT_LINE = "import static_assets as _assets"
BAK_DIR = os.path.join(BASE, "_refactor_backup")
STATIC_DIR = os.path.join(BASE, "static")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path):
    """默认文本模式读取（universal newlines），与 static_assets.load 保持一致。"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # newline="\n"：显式写 LF，避免 Windows 下变成 CRLF（读回来虽然会归一，
    # 但保持文件干净，diff / 版本控制更友好）。
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _find_block(lines, attr):
    """定位 ``ATTR = ...`` 定义块行号区间 [i, j]（0 基，闭区间）；找不到返回 None。"""
    prefix = attr + " = "
    for i, ln in enumerate(lines):
        if not ln.startswith(prefix):
            continue
        rest = ln[len(prefix):]
        quote = '"""' if '"""' in rest else ("'''" if "'''" in rest else None)
        if quote is None:
            return i, i                      # 单行赋值
        tail = rest.split(quote, 1)[1]
        if quote in tail:
            return i, i                      # 同行闭合
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == quote:
                return i, j
        return None
    return None


def _ensure_import(path, lines):
    """确保文件里有 ``import static_assets as _assets``；返回新的行列表。"""
    if any(ln.strip() == IMPORT_LINE for ln in lines):
        return lines
    idx = 0
    for i, ln in enumerate(lines[:60]):      # 只在文件头部的 import 区里找
        if ln.startswith(("import ", "from ")):
            idx = i + 1
    lines.insert(idx, IMPORT_LINE)
    return lines


def migrate(dry_run=False):
    os.makedirs(STATIC_DIR, exist_ok=True)
    if not dry_run:
        os.makedirs(BAK_DIR, exist_ok=True)

    # 先 import，拿到「解释后的值」
    import events
    import home
    import position_manager

    mods = {"home": home, "events": events, "position_manager": position_manager}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    touched = set()
    problems = []

    print("=" * 68)
    print("  内联前端资源外置" + ("（dry-run，不写盘）" if dry_run else ""))
    print("=" * 68)

    for mod_name, attr, target in JOBS:
        mod = mods[mod_name]
        src_path = os.path.join(BASE, mod_name + ".py")
        src = _read(src_path)
        lines = src.split("\n")

        block = _find_block(lines, attr)
        if block is None:
            problems.append(f"{mod_name}.py: 找不到 {attr} 的定义块")
            print(f"[跳过] {mod_name}.{attr}：找不到定义块")
            continue

        i, j = block
        current = "\n".join(lines[i:j + 1])
        if "static_assets" in current:
            print(f"[跳过] {mod_name}.{attr}：已是 load() 形式，无需迁移")
            continue

        value = getattr(mod, attr)
        if not isinstance(value, str):
            problems.append(f"{mod_name}.{attr} 不是字符串")
            continue

        target_path = os.path.join(STATIC_DIR, target)

        if dry_run:
            print(f"[计划] {mod_name}.{attr}（{len(value)} 字符）"
                  f" → static/{target}；定义块行 {i + 1}-{j + 1} 改为 load()")
            continue

        # 1) 写文件
        _write(target_path, value)

        # 2) 回读比对（等价性自证）
        back = _read(target_path)
        if back != value:
            problems.append(f"{mod_name}.{attr}: 回读与常量不一致，已中止")
            print(f"[失败] {mod_name}.{attr}：回读不一致！(写入 {len(value)} → 读回 {len(back)})")
            continue

        # 3) 改写定义块
        lines[i:j + 1] = [f'{attr} = _assets.load("{target}")']
        lines = _ensure_import(src_path, lines)

        # 4) 备份 + 落盘
        shutil.copy2(src_path, os.path.join(BAK_DIR, f"pre_static_{stamp}_{mod_name}.py"))
        _write(src_path, "\n".join(lines))
        touched.add(mod_name)

        print(f"[完成] {mod_name}.{attr}（{len(value)} 字符, sha256 {_sha(value)[:16]}）"
              f" → static/{target}；{mod_name}.py 行 {i + 1}-{j + 1} → load()")

    print("-" * 68)
    if problems:
        print("发现问题（未处理完）：")
        for p in problems:
            print("  -", p)
        return 1
    if dry_run:
        print("dry-run 结束，未修改任何文件。")
        return 0
    print(f"已改写：{', '.join(sorted(touched)) or '（无）'}")
    print("下一步：")
    print("  1) 抽查 static/ 下的文件（首行可能是空行 / <style> 标签 —— 这是为了让"
          "生成物与重构前逐字节一致）")
    print("  2) python _p8_verify_tmp.py  ← 对比重构前后产物是否字节一致")
    return 0


def main():
    parser = argparse.ArgumentParser(description="内联 CSS/JS/HTML 外置到 static/")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不改文件")
    args = parser.parse_args()
    return migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
