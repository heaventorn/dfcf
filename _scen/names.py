# -*- coding: utf-8 -*-
"""Resolve what each candidate CSI code actually is:
1) the ETF's own disclosed tracking index (fundf10 basics page)
2) a web search for the code string"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

print("=== fundf10 basics: tracking index of each ETF ===")
for code in ["511360", "511520", "511010", "511260", "511220", "511880"]:
    try:
        r = requests.get("https://fundf10.eastmoney.com/jbgk_%s.html" % code,
                         headers=H, timeout=25)
        r.encoding = "utf-8"
        txt = re.sub(r"<[^>]+>", " ", r.text)
        txt = re.sub(r"\s+", " ", txt)
        m = re.search(r"跟踪标的\s*([^ ]{2,40})", txt)
        n = re.search(r"基金全称\s*([^ ]{2,40})", txt)
        print("%s  full=%s  tracked=%s" % (code, n.group(1) if n else "?", m.group(1) if m else "?"))
    except Exception as e:
        print("%s ERR %s %s" % (code, type(e).__name__, e))

print()
print("=== web search for CSI code names ===")
for code in ["H11010", "H11015", "H11006", "H11016", "H11025", "H11001"]:
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": "%s 中证 指数" % code}, headers=H, timeout=25)
        txt = re.sub(r"<[^>]+>", " ", r.text)
        txt = re.sub(r"\s+", " ", txt)
        hits = re.findall(r"([^ ]{0,24}%s[^ ]{0,30})" % code, txt)[:4]
        print("%s -> %s" % (code, " | ".join(hits) if hits else "no hits"))
    except Exception as e:
        print("%s ERR %s %s" % (code, type(e).__name__, e))
