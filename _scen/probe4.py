# -*- coding: utf-8 -*-
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

t = open(os.path.join(BASE, "strategy.json"), encoding="utf-8").read()
i = t.find('"risk_overlay"')
print(t[i:i + 2200])
print("=" * 60)
cfg = json.loads(t)
print("keys:", list(cfg.keys()))
print("llm:", json.dumps(cfg.get("llm"), ensure_ascii=False)[:300])
for sid, v in cfg["strategies"].items():
    print(sid, json.dumps(v, ensure_ascii=False)[:400])
