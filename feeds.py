# -*- coding: utf-8 -*-
"""补充信源:财联社电报 + 金十快讯
================================================
两个都是公开可抓的免费接口(属逆向私有接口,改版可能失效,因此都做成"可独立失败",
任意一个不可用都不影响主页生成)。

财联社  GET https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9
        Referer: https://www.cls.cn/telegraph
        返回 data.roll_data[]:id / title / brief / content / ctime(秒) / level(加红要闻为 A、B)
        注:老接口 /nodeapi/telegraphList(+本地 sign)已下线(404),故走公开缓存接口

金十    GET https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1
        头:Referer / Origin = https://www.jin10.com/、x-app-id、x-version: 1.0.0
        x-app-id 是页面里的公开常量,运行时从官网 JS(chunk-common.*.js)提取,
        失败则回退内置常量(实测可用);取出后缓存 1 小时
        返回 data[]:data.content / time(已是北京时间字符串) / important(1 = 要闻)

对外接口:
    fetch_cls(limit)   -> [{time, title, src:'财联社', important}]
    fetch_jin10(limit) -> [{time, title, src:'金十', important}]
    dedup_key(title)   -> 跨信源近似去重用的 key(去标点后的前 24 字)
    SOURCE_STATUS      -> {源名: 本次条数}(-1 表示该源抓取失败)
"""
import json
import re
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

CLS_URL = "https://www.cls.cn/api/cache"
JIN10_URL = "https://flash-api.jin10.com/get_flash_list"
JIN10_APP_ID_FALLBACK = "bVBF4FyRTn5NJF5n"   # 官网 JS 里的公开常量(提取失败时兜底)
MAX_TITLE = 150

# 各信源最近一次抓取条数(供主页"来源状态"展示)
SOURCE_STATUS = {}

_jin10_app = {"id": "", "ts": 0.0}


def _strip_html(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _ts2str(v):
    """unix 秒/毫秒 → 'YYYY-MM-DD HH:MM:SS'(北京时间 UTC+8)。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v or "")
    if n > 1e12:
        n /= 1000.0
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(n) + 8 * 3600))


def dedup_key(title):
    """跨信源近似去重的 key:去标点与空白后取前 24 字。"""
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", title or "")
    return t[:24]


def _clip(title):
    title = (title or "").strip()
    return (title[:MAX_TITLE] + "…") if len(title) > MAX_TITLE else title


def _jin10_app_id():
    """金十 x-app-id:优先从官网 JS 提取,失败回退内置常量(缓存 1 小时)。"""
    now = time.time()
    if _jin10_app["id"] and now - _jin10_app["ts"] < 3600:
        return _jin10_app["id"]
    aid = ""
    try:
        home = urllib.request.urlopen(
            urllib.request.Request("https://www.jin10.com/", headers=UA), timeout=12
        ).read().decode("utf-8", "ignore")
        m = re.search(r"//www\.jin10\.com(/new/js/chunk-common\.[0-9a-f]+\.js)", home)
        if m:
            js = urllib.request.urlopen(
                urllib.request.Request("https://www.jin10.com" + m.group(1), headers=UA),
                timeout=15,
            ).read().decode("utf-8", "ignore")
            m2 = re.search(r'x-app-id"\s*:\s*"([A-Za-z0-9]+)"', js)
            if m2:
                aid = m2.group(1)
    except Exception:
        aid = ""
    _jin10_app["id"] = aid or JIN10_APP_ID_FALLBACK
    _jin10_app["ts"] = now
    return _jin10_app["id"]


def fetch_cls(limit=None):
    """财联社电报(公开缓存接口)。返回 [{time, title, src, important}]。"""
    import config as _cfg
    if limit is None:
        limit = int(getattr(_cfg, "NEWS_CLS_LIMIT", 150))
    url = CLS_URL + "?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9"
    h = dict(UA)
    h["Referer"] = "https://www.cls.cn/telegraph"
    text = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) \
        .read().decode("utf-8", "ignore")
    data = json.loads(text)
    roll = ((data or {}).get("data") or {}).get("roll_data") or []
    out = []
    for it in roll[:limit]:
        title = (it.get("title") or "").strip() or \
            _strip_html(it.get("brief") or it.get("content") or "")
        title = re.sub(r"^财联社\d+月\d+日电[，,:：]?", "", title).strip()
        title = _clip(title)
        if not title:
            continue
        lv = (it.get("level") or "").upper()
        out.append({"time": _ts2str(it.get("ctime")), "title": title,
                    "src": "财联社", "important": 1 if lv in ("A", "B") else 0})
    return out


def fetch_jin10(limit=None):
    """金十快讯(channel=-8200 全球)。返回 [{time, title, src, important}]。"""
    import config as _cfg
    if limit is None:
        limit = int(getattr(_cfg, "NEWS_JIN10_LIMIT", 150))
    h = dict(UA)
    h.update({"Referer": "https://www.jin10.com/", "Origin": "https://www.jin10.com",
              "x-app-id": _jin10_app_id(), "x-version": "1.0.0"})
    url = JIN10_URL + "?channel=-8200&vip=1"
    text = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) \
        .read().decode("utf-8", "ignore")
    data = json.loads(text)
    out = []
    for it in (data or {}).get("data") or []:
        d = it.get("data") or {}
        title = _clip(_strip_html(d.get("content") or d.get("title") or ""))
        if not title:
            continue
        out.append({"time": (it.get("time") or "").strip(), "title": title,
                    "src": "金十", "important": int(it.get("important") or 0)})
        if len(out) >= limit:
            break
    return out


THS_URL = "https://news.10jqka.com.cn/tapp/news/push/stock/"


def fetch_ths(limit=None, pages=None, page_size=100):
    """同花顺快讯(公开接口)。返回 [{time, title, src:'同花顺', important}]。"""
    import config as _cfg
    if limit is None:
        limit = int(getattr(_cfg, "NEWS_THS_LIMIT", 400))
    if pages is None:
        pages = int(getattr(_cfg, "NEWS_THS_PAGES", 8))
    out = []
    h = dict(UA)
    h["Referer"] = "https://news.10jqka.com.cn/realtimenews.html"
    for p in range(1, pages + 1):
        try:
            url = "%s?page=%d&tag=&track=website&pagesize=%d" % (THS_URL, p, page_size)
            text = urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=15
            ).read().decode("utf-8", "ignore")
            data = json.loads(text)
            lst = ((data or {}).get("data") or {}).get("list") or []
        except Exception:
            break
        if not lst:
            break
        for it in lst:
            title = _clip(_strip_html(it.get("title") or it.get("digest") or ""))
            if not title:
                continue
            out.append({"time": _ts2str(it.get("ctime")), "title": title,
                        "src": "同花顺", "important": 1 if str(it.get("color") or "") in ("2", "3") else 0})
            if len(out) >= limit:
                return out
        time.sleep(0.2)
    return out


SINA_URL = "https://zhibo.sina.com.cn/api/zhibo/feed"


def fetch_sina(limit=None, pages=None, page_size=100):
    """新浪财经 7x24 直播（可翻页，历史约 900 条）。返回 [{time, title, src, important}]。

    与 sources._sina_news 用的是同一接口，但那边只取首页做「财经快讯」展示，
    这里翻页取回当天全部，供全球事件地球使用。
    """
    import config as _cfg
    if limit is None:
        limit = int(getattr(_cfg, "NEWS_SINA_LIMIT", 500))
    if pages is None:
        pages = int(getattr(_cfg, "NEWS_SINA_PAGES", 6))
    h = dict(UA)
    h["Referer"] = "https://finance.sina.com.cn/7x24/"
    out = []
    for p in range(1, pages + 1):
        url = ("%s?page=%d&page_size=%d&zhibo_id=152&tag_id=0&dire=f&dpc=1&pagesize=%d"
               % (SINA_URL, p, page_size, page_size))
        try:
            text = urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=15
            ).read().decode("utf-8", "ignore")
            data = json.loads(text)
            lst = ((((data or {}).get("result") or {}).get("data") or {})
                   .get("feed") or {}).get("list") or []
        except Exception:
            break
        if not lst:
            break
        for it in lst:
            title = _clip(_strip_html(it.get("rich_text") or ""))
            if not title:
                continue
            out.append({"time": (it.get("create_time") or "").strip(), "title": title,
                        "src": "新浪", "important": 0})
            if len(out) >= limit:
                return out
        time.sleep(0.2)
    return out


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    for name, fn in (("财联社", fetch_cls), ("金十", fetch_jin10)):
        try:
            rows = fn()
            print("==", name, "条数:", len(rows))
            for x in rows[:5]:
                print("   ", x["time"], "要闻" if x.get("important") else "   ", "|", x["title"][:56])
        except Exception as e:
            print("==", name, "失败:", e.__class__.__name__, e)
