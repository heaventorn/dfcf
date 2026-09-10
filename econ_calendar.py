# -*- coding: utf-8 -*-
"""财经日历(会议 / 经济数据)采集模块
================================================
用途:为主页左下窗口提供「今日 / 明日 会议 · 经济数据」列表。

主源(已实测可用,免费):
    东方财富「财经日历」https://data.eastmoney.com/cjrl/
    接口:datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_CPH_FECALENDAR
    字段:START_DATE / END_DATE / FE_NAME / FE_TYPE / STD_TYPE_CODE / CITY / SPONSOR_NAME

兜底(主源不可用时):
    从已抓的新闻事件(EVENTS)里按关键词提取「会议 / 数据」类条目,并在结果里标注 source="news"。

对外接口:
    fetch_calendar(events=None, days=2) -> {
        "source": "eastmoney" | "news" | "none",
        "note":   "来源说明 / 降级原因",
        "time":   "采集时间",
        "days":   [{"date": "...", "label": "今日", "total": n,
                    "items": [{"time","name","kind","star","region","sponsor"}]}]
    }
star 为重要性(3 重点 / 2 一般 / 1 其它),前端默认只展示 star>=2,其余折叠。
"""
import datetime
import json
import urllib.request
from urllib.parse import urlencode

import config

CAL_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CAL_REPORT = "RPT_CPH_FECALENDAR"
CAL_COLUMNS = "START_DATE,END_DATE,FE_CODE,FE_NAME,FE_TYPE,STD_TYPE_CODE,CITY,SPONSOR_NAME,CONTENT"

# 每天最多保留多少条(避免 payload 过大)
MAX_ITEMS_PER_DAY = 90

# 强重点关键词(名称或类型命中 → ★★★)
STRONG = (
    "CPI", "PPI", "PCE", "GDP", "非农", "失业率", "利率", "议息", "加息", "降息",
    "美联储", "FOMC", "PMI", "社融", "M2", "贸易帐", "外汇储备", "零售", "工业增加值",
    "社会消费品",
)
# 次重点关键词(命中 → ★★★;但「报告期」类细粒度统计数据降为 ★★)
KEY_HIGH = STRONG + ("央行", "决议", "听证", "财报", "进出口")
# 一般关键词(仅匹配名称与类型,避免正文里的“报告期”等噪声)
KEY_MID = (
    "会议", "峰会", "论坛", "大会", "发布会", "磋商", "谈判", "会谈", "展览", "博览会",
    "听证", "决议", "讲话", "数据",
)


def _headers():
    h = dict(config.HEADERS)
    h["Referer"] = "https://data.eastmoney.com/cjrl/"
    return h


def _classify(name, fe_type, content, std_code):
    """判断条目类别(会议 / 数据)与重要性(3 重点 / 2 一般 / 1 其它)。

    重要性只由「名称 + 类型」决定(正文多用于描述,噪声大,仅参与类别判断);
    名称带「报告期」的细粒度统计数据(如某国原油产量)最高只给 ★★,避免刷屏。
    """
    name = name or ""
    fe_type = fe_type or ""
    blob = name + " " + (content or "")
    up = (name + " " + fe_type).upper()

    if fe_type == "经济数据" or ("数据" in name and "会议" not in fe_type):
        kind = "数据"
    elif fe_type and "会议" in fe_type:
        kind = "会议"
    else:
        kind = "会议" if any(k in blob for k in ("会议", "峰会", "论坛", "大会", "展览")) else "数据"

    if any(k.upper() in up for k in STRONG):
        star = 3
    elif any(k.upper() in up for k in KEY_HIGH):
        star = 2 if "报告期" in name else 3
    elif any(k in up for k in KEY_MID) or str(std_code or "") == "1":
        star = 2
    else:
        star = 1
    return kind, star


def _query(start_date, end_date_exclusive, page_size=200, timeout=20):
    """查询 [start_date, end_date_exclusive) 内仍在进行/即将开始的日历事件。"""
    q = chr(39)
    params = {
        "reportName": CAL_REPORT,
        "columns": CAL_COLUMNS,
        "pageSize": str(page_size),
        "pageNumber": "1",
        "sortColumns": "START_DATE",
        "sortTypes": "1",
        "source": "WEB",
        "client": "WEB",
        "filter": ("(END_DATE>=" + q + start_date + q + ")(START_DATE<" + q + end_date_exclusive + q + ")"),
    }
    url = CAL_URL + "?" + urlencode(params)
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "ignore"))
    res = data.get("result") or {}
    return res.get("data") or []


def _fallback_from_events(events, days=2):
    """主源不可用时的兜底:从新闻事件里按关键词提取会议 / 数据类条目(仅归入今日)。"""
    items = []
    seen = set()
    for e in (events or []):
        title = (e.get("title") or "").strip()
        if not title or title in seen:
            continue
        if not any(k in title for k in KEY_HIGH + KEY_MID):
            continue
        seen.add(title)
        kind, star = _classify(title, "", title, "")
        items.append({
            "time": (e.get("time") or "")[11:16] or "全天",
            "name": title[:90],
            "kind": kind,
            "star": star,
            "region": e.get("country") or e.get("city") or "",
            "sponsor": "新闻提取",
        })
    items.sort(key=lambda x: (-x["star"], x["time"]))
    return items[:MAX_ITEMS_PER_DAY]


def fetch_calendar(events=None, days=2):
    """抓取今日起的财经日历(默认今日 + 明日)。失败自动降级为新闻提取,绝不抛异常。"""
    today = datetime.date.today()
    out = {
        "source": "eastmoney",
        "note": "来源:东方财富 · 财经日历(data.eastmoney.com/cjrl)",
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": [],
    }
    end = today + datetime.timedelta(days=days)

    rows = []
    try:
        rows = _query(today.isoformat(), end.isoformat())
    except Exception as e:
        out["note"] = "财经日历接口不可用(%s),已改用新闻提取" % e.__class__.__name__
        rows = []

    if rows:
        buckets = {}
        for it in rows:
            name = (it.get("FE_NAME") or "").strip()
            if not name:
                continue
            s_raw = (it.get("START_DATE") or "")[:10]
            e_raw = (it.get("END_DATE") or s_raw)[:10]
            try:
                sd = datetime.date.fromisoformat(s_raw)
                ed = datetime.date.fromisoformat(e_raw)
            except ValueError:
                continue
            if ed < sd:
                ed = sd
            kind, star = _classify(name, it.get("FE_TYPE"), it.get("CONTENT"), it.get("STD_TYPE_CODE"))
            hhmm = (it.get("START_DATE") or "")[11:16]
            row = {
                "time": hhmm if hhmm and hhmm != "00:00" else "全天",
                "name": name,
                "kind": kind,
                "star": star,
                "region": (it.get("CITY") or "").strip(),
                "sponsor": (it.get("SPONSOR_NAME") or "").strip(),
            }
            for i in range(days):
                d = today + datetime.timedelta(days=i)
                if sd <= d <= ed:
                    buckets.setdefault(d, []).append(dict(row))

        for i in range(days):
            d = today + datetime.timedelta(days=i)
            items = buckets.get(d, [])
            items.sort(key=lambda x: (-x["star"], 0 if x["time"] == "全天" else 1, x["time"]))
            label = "今日" if i == 0 else ("明日" if i == 1 else d.strftime("%m-%d"))
            out["days"].append({
                "date": d.isoformat(), "label": label,
                "total": len(items), "items": items[:MAX_ITEMS_PER_DAY],
            })
        if not any(dd["items"] for dd in out["days"]):
            out["source"] = "none"
            out["note"] = "今日与明日暂无日历事件(数据源可用但返回为空)"
        return out

    # ---- 兜底:新闻关键词提取 ----
    fb = _fallback_from_events(events, days=days)
    for i in range(days):
        d = today + datetime.timedelta(days=i)
        label = "今日" if i == 0 else ("明日" if i == 1 else d.strftime("%m-%d"))
        out["days"].append({
            "date": d.isoformat(), "label": label,
            "total": len(fb) if i == 0 else 0,
            "items": fb if i == 0 else [],
        })
    out["source"] = "news" if fb else "none"
    if not fb:
        out["note"] = "财经日历接口不可用,且新闻中未提取到会议 / 数据类条目"
    return out


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    res = fetch_calendar()
    print("来源:", res["source"])
    print("说明:", res["note"])
    for d in res["days"]:
        key = [x for x in d["items"] if x["star"] >= 3]
        mid = [x for x in d["items"] if x["star"] == 2]
        print("-- %s %s 共 %d 条(重点 %d / 一般 %d)" % (d["label"], d["date"], d["total"], len(key), len(mid)))
        for x in (key + mid)[:8]:
            print("   ", x["time"], "★" * x["star"], x["kind"], x["region"], "|", x["name"][:44])
