# -*- coding: utf-8 -*-
"""Print the official name of each candidate CSI index (from the perf API)."""

import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.csindex.com.cn/"}
CODES = ["H11001", "H11006", "H11007", "H11008", "H11009", "H11010", "H11014",
         "H11015", "H11016", "H11017", "H11018", "H11019", "H11023", "H11025"]

for c in CODES:
    try:
        r = requests.get("https://www.csindex.com.cn/csindex-home/perf/index-perf",
                         params={"indexCode": c, "startDate": "20260910", "endDate": "20260918"},
                         headers=H, timeout=30)
        d = r.json().get("data") or []
        if d:
            print("%-7s %-28s %s" % (c, d[0].get("indexNameCnAll"), d[0].get("indexNameEnAll")))
        else:
            print("%-7s (no data)" % c)
    except Exception as e:
        print("%-7s ERR %s" % (c, type(e).__name__))
