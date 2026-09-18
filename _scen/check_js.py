# -*- coding: utf-8 -*-
"""把页面里的内联 <script> 抠出来，逐个交给 node --check 做语法校验。

改完前端先跑这个 —— 整页白屏基本都是脚本语法错误，这个能在打开浏览器之前抓到。

用法：
    py _scen/check_js.py                      # 默认查 strategy.html / stock.html
    py _scen/check_js.py output/index.html    # 主页是 5MB 的生成产物，要查就显式点名
"""

import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
DEFAULT = ["strategy.html", "stock.html"]


def scripts(path):
    s = io.open(path, encoding="utf-8").read()
    # 带 src= 的外链脚本没内容可查，跳过
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", s, re.S)


def main():
    names = sys.argv[1:] or DEFAULT
    bad = 0
    for n in names:
        p = n if os.path.isabs(n) else os.path.join(BASE, n)
        if not os.path.exists(p):
            print("%-22s 找不到文件" % n)
            bad += 1
            continue
        blocks = scripts(p)
        if not blocks:
            print("%-22s 没有内联 <script>" % os.path.basename(n))
            continue
        for i, b in enumerate(blocks, 1):
            f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                            encoding="utf-8")
            f.write(b)
            f.close()
            try:
                r = subprocess.run(["node", "--check", f.name],
                                   capture_output=True, text=True)
                code, err = r.returncode, (r.stderr or "").strip()
            except FileNotFoundError:
                print("没装 node，跳过")
                return 0
            finally:
                os.unlink(f.name)
            tag = "%s #%d" % (os.path.basename(n), i)
            if code == 0:
                print("%-22s OK（%d 字符）" % (tag, len(b)))
            else:
                bad += 1
                print("%-22s 语法错误\n%s" % (tag, err))
    print()
    print("全部通过" if not bad else "%d 块有问题" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
