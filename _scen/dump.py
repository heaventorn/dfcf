# -*- coding: utf-8 -*-
"""把 strategy.json 的 strategies 段原样打出来（改文件前对一下原文）。"""

import io
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

t = open(os.path.join(BASE, "strategy.json"), encoding="utf-8").read()
i = t.find('"strategies"')
print(t[i:i + 2600])
