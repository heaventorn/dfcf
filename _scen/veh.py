# -*- coding: utf-8 -*-
"""Which tradeable vehicles actually exist, and since when?"""

import json
import re
import urllib.request

CODES = ["511030", "511220", "159926", "511200", "511270", "511060", "511070",
         "511380", "159972", "511210", "511180", "511280", "159649", "511020",
         "511230", "511010", "511260", "511520", "511360", "511880", "511290",
         "512890", "513630", "518880", "513100", "510300", "511090", "511100"]


def fetch(code):
    u = "http://fund.eastmoney.com/pingzhongdata/%s.js" % code
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")


def main():
    for c in CODES:
        try:
            txt = fetch(c)
        except Exception as e:
            print(c, "ERR", e)
            continue
        m = re.search(r"Data_ACWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
        n = len(eval(m.group(1))) if m else 0
        nm = re.search(r'fS_name\s*=\s*"(.*?)"', txt)
        if n == 0:
            print(c, "no nav", nm.group(1) if nm else "?")
            continue
        arr = eval(m.group(1))
        import datetime
        d0 = datetime.datetime.utcfromtimestamp(arr[0][0] / 1000).strftime("%Y%m%d")
        d1 = datetime.datetime.utcfromtimestamp(arr[-1][0] / 1000).strftime("%Y%m%d")
        print(c, n, d0, "->", d1, nm.group(1) if nm else "?")


if __name__ == "__main__":
    main()
