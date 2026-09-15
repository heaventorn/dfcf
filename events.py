# -*- coding: utf-8 -*-
"""全球宏观事件采集(多信源) → 3D 地球页面

信源(均为免费可抓):
  1. 东财 7x24 快讯  newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult(翻页覆盖数天)
  2. 华尔街见闻快讯  api-one.wallstcn.com/apiv1/content/lives (global-channel)

流程:抓取 → 3 天时间窗过滤 → 关键词分级(3 高/2 中)→ 事件性质分类
(🔥 突发:关税/制裁/冲突/袭击/利率行动/违约崩盘等;📊 一般:就业/行情/收益率等数据)
→ geo.locate() 归因国家/城市 → 光点聚合 + 国家边界(GeoJSON),点击查看消息。

页面增强:
  - 左栏"今日热点"卡片:生成时按 今天(北京时间)∩(突发/高影响) 计分取前 8 条,
    点击条目 → 飞往对应地点并查看该国/该点详情。
  - 右下控制面板:图层开关(国界/经纬网/事件层/热点卡)+ 六大洲视角导航与复位;
    查看详情时面板自动淡出,内含影响等级图例与操作提示。

国界数据:assets/world.geojson(本地,Natural Earth 110m,properties.name 英文)。
页面生成时国界 JSON 与地球贴图均内嵌,双击 HTML 即可离线使用。

用法:
    python events.py            # 抓取并生成 events_live.html(真实事件 3D 预览)
"""
import base64
import datetime
import json
import math
import os
import re
import time
import urllib.request

import config

from geo import locate

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
NEWS_URL = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_{n}_{p}_.html"
WALLSTCN_URL = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&client=pc&limit={n}"

# 消息时间窗:只保留最近 N 天（集中在 config.NEWS_WINDOW_DAYS 里调；页面文案也读它）
WINDOW_DAYS = getattr(config, "NEWS_WINDOW_DAYS", 7)

# 命中即"重要事件",关键词给影响等级(3 高 / 2 中)与类型标签
SEV3_KEYWORDS = [
    "央行", "利率决议", "加息", "降息", "FOMC", "非农", "CPI", "关税", "制裁",
    "战争", "袭击", "冲突", "违约", "破产", "崩盘", "暴跌", "LPR", "降准", "美联储", "欧央行",
]
SEV2_KEYWORDS = [
    "通胀", "就业", "GDP", "PMI", "PPI", "社融", "OPEC", "减产", "财报",
    "收益率", "国债", "汇率", "黄金", "原油", "日元", "美元", "欧元", "英镑",
]
TAG_MAP = [
    (["央行", "利率决议", "加息", "降息", "FOMC", "LPR", "降准", "美联储", "欧央行", "日央行"], "货币政策"),
    (["非农", "CPI", "PPI", "PMI", "GDP", "通胀", "就业", "社融"], "宏观数据"),
    (["关税", "制裁", "贸易"], "贸易政策"),
    (["战争", "袭击", "冲突", "导弹"], "地缘政治"),
    (["违约", "破产", "债务"], "债务风险"),
    (["暴跌", "暴涨", "崩盘", "财报", "黄金", "原油", "国债", "收益率", "汇率", "日元", "美元", "欧元", "英镑"], "市场"),
]

# 事件性质分类:🔥 突发(政策突变/地缘/风险事件) vs 📊 一般经济(数据/行情)
BURST_KEYWORDS = [
    "关税", "制裁", "战争", "袭击", "导弹", "冲突", "爆发", "崩盘", "违约", "破产",
    "暴跌", "暴涨", "加息", "降息", "利率决议", "FOMC", "降准", "紧急", "突发",
]

# 今日热点卡片条数上限
HOT_TOP_N = 8

# GeoJSON 国家英文名 → 中文名(值尽量与 geo.SPOTS 的 country 字段一致,便于事件匹配)
COUNTRY_ZH = {
    "Afghanistan": "阿富汗", "Albania": "阿尔巴尼亚", "Algeria": "阿尔及利亚", "Angola": "安哥拉",
    "Argentina": "阿根廷", "Armenia": "亚美尼亚", "Australia": "澳大利亚", "Austria": "奥地利",
    "Azerbaijan": "阿塞拜疆", "Bangladesh": "孟加拉国", "Belarus": "白俄罗斯", "Belgium": "比利时",
    "Benin": "贝宁", "Bolivia": "玻利维亚", "Bosnia and Herzegovina": "波黑", "Botswana": "博茨瓦纳",
    "Brazil": "巴西", "Bulgaria": "保加利亚", "Burkina Faso": "布基纳法索", "Cambodia": "柬埔寨",
    "Cameroon": "喀麦隆", "Canada": "加拿大", "Central African Republic": "中非", "Chad": "乍得",
    "Chile": "智利", "China": "中国", "Colombia": "哥伦比亚", "Congo": "刚果",
    "Costa Rica": "哥斯达黎加", "Croatia": "克罗地亚", "Cuba": "古巴", "Cyprus": "塞浦路斯",
    "Czechia": "捷克", "Czech Republic": "捷克", "Denmark": "丹麦", "Dominican Republic": "多米尼加",
    "Ecuador": "厄瓜多尔", "Egypt": "埃及", "El Salvador": "萨尔瓦多", "Estonia": "爱沙尼亚",
    "Ethiopia": "埃塞俄比亚", "Finland": "芬兰", "France": "法国", "Gabon": "加蓬",
    "Gambia": "冈比亚", "Georgia": "格鲁吉亚", "Germany": "德国", "Ghana": "加纳",
    "Greece": "希腊", "Guatemala": "危地马拉", "Guinea": "几内亚", "Haiti": "海地",
    "Honduras": "洪都拉斯", "Hungary": "匈牙利", "Iceland": "冰岛", "India": "印度",
    "Indonesia": "印尼", "Iran": "伊朗", "Iraq": "伊拉克", "Ireland": "爱尔兰",
    "Israel": "以色列", "Italy": "意大利", "Ivory Coast": "科特迪瓦", "Jamaica": "牙买加",
    "Japan": "日本", "Jordan": "约旦", "Kazakhstan": "哈萨克斯坦", "Kenya": "肯尼亚",
    "Kuwait": "科威特", "Kyrgyzstan": "吉尔吉斯斯坦", "Laos": "老挝", "Latvia": "拉脱维亚",
    "Lebanon": "黎巴嫩", "Liberia": "利比里亚", "Libya": "利比亚", "Lithuania": "立陶宛",
    "Luxembourg": "卢森堡", "Madagascar": "马达加斯加", "Malawi": "马拉维", "Malaysia": "马来西亚",
    "Mali": "马里", "Mauritania": "毛里塔尼亚", "Mexico": "墨西哥", "Moldova": "摩尔多瓦",
    "Mongolia": "蒙古", "Montenegro": "黑山", "Morocco": "摩洛哥", "Mozambique": "莫桑比克",
    "Myanmar": "缅甸", "Namibia": "纳米比亚", "Nepal": "尼泊尔", "Netherlands": "荷兰",
    "New Zealand": "新西兰", "Nicaragua": "尼加拉瓜", "Niger": "尼日尔", "Nigeria": "尼日利亚",
    "North Korea": "朝鲜", "Norway": "挪威", "Oman": "阿曼", "Pakistan": "巴基斯坦",
    "Panama": "巴拿马", "Papua New Guinea": "巴布亚新几内亚", "Paraguay": "巴拉圭", "Peru": "秘鲁",
    "Philippines": "菲律宾", "Poland": "波兰", "Portugal": "葡萄牙", "Qatar": "卡塔尔",
    "Romania": "罗马尼亚", "Russia": "俄罗斯", "Rwanda": "卢旺达", "Saudi Arabia": "沙特",
    "Senegal": "塞内加尔", "Serbia": "塞尔维亚", "Sierra Leone": "塞拉利昂", "Singapore": "新加坡",
    "Slovakia": "斯洛伐克", "Slovenia": "斯洛文尼亚", "Somalia": "索马里", "South Africa": "南非",
    "South Korea": "韩国", "South Sudan": "南苏丹", "Spain": "西班牙", "Sri Lanka": "斯里兰卡",
    "Sudan": "苏丹", "Suriname": "苏里南", "Sweden": "瑞典", "Switzerland": "瑞士",
    "Syria": "叙利亚", "Taiwan": "中国台湾", "Tajikistan": "塔吉克斯坦", "Tanzania": "坦桑尼亚",
    "Thailand": "泰国", "Togo": "多哥", "Tunisia": "突尼斯", "Turkey": "土耳其",
    "Turkmenistan": "土库曼斯坦", "Uganda": "乌干达", "Ukraine": "乌克兰",
    "United Arab Emirates": "阿联酋", "United Kingdom": "英国", "United States of America": "美国",
    "Uruguay": "乌拉圭", "Uzbekistan": "乌兹别克斯坦", "Venezuela": "委内瑞拉", "Vietnam": "越南",
    "Yemen": "也门", "Zambia": "赞比亚", "Zimbabwe": "津巴布韦",
}


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


def _window_start_str(days=WINDOW_DAYS):
    """时间窗起点 'YYYY-MM-DD HH:MM:SS'(同格式字符串可直接比较)。

    config.NEWS_TODAY_ONLY 为真时按「自然日」取今天 0 点（只保留当天新闻）；
    否则退回「最近 N 天」的滚动窗口。
    """
    if getattr(config, "NEWS_TODAY_ONLY", False):
        return time.strftime("%Y-%m-%d 00:00:00")
    return (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def window_label():
    """时间窗文案（页面标题 / 新闻栏统一用它）：“今日” 或 “近 N 天”。"""
    if getattr(config, "NEWS_TODAY_ONLY", False):
        return "今日"
    return "近%d天" % WINDOW_DAYS


# ---------------------------------------------------------------- 广告 / 推广过滤

# 一出现就基本可判定为推广的词（在正常财经与社会新闻里几乎不出现）
AD_HARD_WORDS = (
    "竞猜", "抽奖", "领券", "现金红包", "红包大奖", "优惠券", "扫码领取",
    "直播间福利", "点击抽奖", "免费领红包",
)
# 行动号召（祈使句）
AD_CTA_WORDS = (
    "马上参与", "快来参与", "立即参与", "点击参与", "点击查看", "立即领取",
    "免费领取", "限时领取", "马上报名", "立即下载", "点击下载",
)
# 奖励承诺
AD_PRIZE_WORDS = ("大奖", "红包", "奖金", "礼品", "福利", "奖品", "现金")


def _is_ad(title):
    """判断标题是否为广告 / 平台活动推广。

    规则故意保守（宁可漏杀也不误杀）：只有「明确推广词」、或「行动号召」、
    或「号召 + 奖励承诺」同时出现才判定。
    「开户 / 优惠 / 预约 / 投票 / 活动」这类高频正常词**不**作特征 —— 实测把它们
    当特征会误杀「8月A股两融新开户」「熊猫宝宝参观预约」「机构采购打五折」等真新闻。
    """
    if not getattr(config, "NEWS_AD_FILTER", True):
        return False
    t = title or ""
    if any(w in t for w in AD_HARD_WORDS):
        return True
    if any(w in t for w in AD_CTA_WORDS):
        return True
    return (any(a in t for a in ("参与", "点击", "领取"))
            and any(b in t for b in AD_PRIZE_WORDS))


def _in_window(time_str, ws_str):
    if not time_str:
        return True  # 无时间信息时保留
    return time_str >= ws_str


def _clean_url(u):
    """规整原文链接:补协议头、只允许 http(s);拿不到就返回空串(前端会退化成不可点)。"""
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if not u.startswith(("http://", "https://")):
        return ""
    return u


def _fetch_eastmoney(per_page=100, max_pages=None):
    """东财 7x24 快讯(翻页取回时间窗内全部)。原文链接取返回里的 url_w / url_unique。"""
    if max_pages is None:
        max_pages = int(getattr(config, "NEWS_EM_PAGES", 20))
    ws = _window_start_str()
    out, early = [], False
    for page in range(1, max_pages + 1):
        url = NEWS_URL.format(n=per_page, p=page)
        req = urllib.request.Request(url, headers=UA)
        text = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        if "ajaxResult=" in text:
            text = text.split("=", 1)[1].rstrip(";").strip()
        data = json.loads(text)
        lives = (data or {}).get("LivesList") or []
        if not lives:
            break
        for x in lives:
            tm = (x.get("showtime") or "").strip()
            t = (x.get("title") or "").strip()
            if t and _in_window(tm, ws):
                out.append({"time": tm, "title": t, "src": "东财",
                            "url": _clean_url(x.get("url_w") or x.get("url_unique")
                                              or x.get("url_m") or "")})
            elif tm and tm < ws:
                early = True  # 已翻到窗口外,停止继续翻页
        if early:
            break
    return out


def _fetch_wallstcn(limit=None):
    """华尔街见闻 global-channel 快讯。原文链接取返回里的 uri。"""
    if limit is None:
        limit = int(getattr(config, "NEWS_WALLSTCN_LIMIT", 300))
    url = WALLSTCN_URL.format(n=limit)
    req = urllib.request.Request(url, headers=UA)
    text = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    data = json.loads(text)
    items = ((data or {}).get("data") or {}).get("items") or []
    ws = _window_start_str()
    out = []
    for it in items[:limit]:
        title = (it.get("title") or "").strip()
        if not title:
            title = _strip_html(it.get("content_text") or "")
        if not title:
            continue
        if len(title) > 150:
            title = title[:150] + "…"
        tm = _ts2str(it.get("display_time"))
        if not _in_window(tm, ws):
            continue
        out.append({"time": tm, "title": title, "src": "见闻",
                    "url": _clean_url(it.get("uri") or "")})
    return out


def _classify(title):
    """返回 (sev, tag);不是重要事件返回 (None, None)。"""
    sev = 3 if any(k in title for k in SEV3_KEYWORDS) \
        else (2 if any(k in title for k in SEV2_KEYWORDS) else None)
    if sev is None:
        return None, None
    for kws, tag in TAG_MAP:
        if any(k in title for k in kws):
            return sev, tag
    return sev, "市场"


def _kind(title):
    """事件性质:🔥 突发 / 📊 一般经济。"""
    return "突发" if any(k in title for k in BURST_KEYWORDS) else "常规"


_COUNTRY_CENTERS = None


def _country_centers():
    """{名称(中文优先,另含英文别名): (lat, lng)} —— 由 assets/world.geojson 自动算出各国中心。

    经度用向量平均,保证跨 180° 的国家(俄罗斯、斐济等)也能得到合理中心。
    """
    global _COUNTRY_CENTERS
    if _COUNTRY_CENTERS is not None:
        return _COUNTRY_CENTERS
    out = {}
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "assets", "world.geojson"), encoding="utf-8") as f:
            geo = json.load(f)
        for ft in geo.get("features") or []:
            en = ((ft.get("properties") or {}).get("name") or "").strip()
            if not en:
                continue
            g = ft.get("geometry") or {}
            cs = g.get("coordinates") or []
            polys = [cs] if g.get("type") == "Polygon" else cs
            best, bestn = None, 0
            for poly in polys:
                ring = poly[0] if poly else None
                if ring and len(ring) > bestn:
                    best, bestn = ring, len(ring)
            if not best:
                continue
            lat = sum(p[1] for p in best) / len(best)
            sx = sum(math.cos(math.radians(p[0])) for p in best)
            cx = sum(math.sin(math.radians(p[0])) for p in best)
            lng = math.degrees(math.atan2(cx, sx))
            zh = COUNTRY_ZH.get(en, en)
            out.setdefault(zh, (lat, lng))
            out.setdefault(en, (lat, lng))
    except Exception:
        pass
    _COUNTRY_CENTERS = out
    return out


def _locate_country(title):
    """按"标题里出现的国家名"归因到该国中心(长名优先,避免"中国"抢到"中国台湾")。"""
    try:
        centers = _country_centers()
        names = sorted(centers.keys(), key=len, reverse=True)
        zh_set = set(COUNTRY_ZH.values())
        for name in names:
            if len(name) >= 2 and name in title:
                zh = name if name in zh_set else COUNTRY_ZH.get(name, name)
                lat, lng = centers[name]
                return {"country": zh, "city": "", "lat": round(lat, 3), "lng": round(lng, 3)}
    except Exception:
        pass
    return None


def fetch_events(limit=None, drop_without_geo=True, days=WINDOW_DAYS):
    """多信源抓取(东财 7x24 / 华尔街见闻 / 财联社 / 金十 / 同花顺)→ 时间窗过滤
    → geo 归因(SPOTS 精确地点,兜底按国名归因到该国中心)→ 分级分类。按时间倒序。

    每项: {time, title, src, url, city, country, lat, lng, sev, tag, kind}
    url 为原文链接(点新闻标题可直接跳原站);某条拿不到时为空串。
    sev: 3 高影响 / 2 中影响 / 1 普通快讯(用于提高光点密度,前端以浅色小点呈现)。
    各源条数记录在 feeds.SOURCE_STATUS(供主页展示来源状态,-1 表示该源抓取失败)。
    """
    if limit is None:
        limit = getattr(config, "NEWS_LIMIT", 1000)
    from feeds import fetch_cls, fetch_jin10, fetch_ths, fetch_sina, dedup_key, SOURCE_STATUS

    label = {"_fetch_eastmoney": "东财", "_fetch_wallstcn": "见闻",
             "fetch_cls": "财联社", "fetch_jin10": "金十", "fetch_ths": "同花顺",
             "fetch_sina": "新浪"}
    raw_news = []
    for fn in (_fetch_eastmoney, _fetch_wallstcn, fetch_cls, fetch_jin10, fetch_ths, fetch_sina):
        name = label.get(getattr(fn, "__name__", "?"), "其它")
        try:
            got = fn()
            raw_news += got
            SOURCE_STATUS[name] = len(got)
        except Exception as e:  # 单源失败不阻断整体
            SOURCE_STATUS[name] = -1
            print(f"[提示] 信源 {name} 抓取失败: {e}")

    ws = _window_start_str(days)
    events, seen = [], set()
    for n in raw_news:
        title = n["title"]
        if not title or not _in_window(n["time"], ws):
            continue
        if _is_ad(title):               # 广告 / 平台活动推广直接丢弃
            continue
        key = dedup_key(title)          # 跨信源近似去重(去标点后前 24 字)
        if key in seen:
            continue
        seen.add(key)
        spot = locate(title) or _locate_country(title)   # 先精确地点,再按国名兜底
        if spot is None:
            if drop_without_geo:
                continue
            spot = {"country": "", "city": "", "lat": None, "lng": None}
        sev, tag = _classify(title)
        if sev is None:
            # 放宽:没有关键词的也保留(平台标注的要闻给 2,其余给 1),以增加光点密度
            sev, tag = (2, "市场") if n.get("important") else (1, "其它")
        events.append({
            "time": n["time"], "title": title, "src": n.get("src", ""),
            "url": n.get("url", ""),        # 原文链接:点新闻标题可跳原站
            "city": spot["city"], "country": spot["country"],
            "lat": spot["lat"], "lng": spot["lng"],
            "sev": sev, "tag": tag, "kind": _kind(title),
        })
    events.sort(key=lambda e: e["time"], reverse=True)
    events = events[:limit]

    hit = {}
    for e in events:
        hit[e["src"]] = hit.get(e["src"], 0) + 1
    for k, v in list(SOURCE_STATUS.items()):
        if v >= 0:
            SOURCE_STATUS[k] = hit.get(k, 0)
    return events


def _hot_items(events, top=HOT_TOP_N):
    """今日热点:优先取"今天(北京)"内事件,按 突发+高影响 计分取前 top。

    今天(当地时间 0 点后)无事件时回退到整个时间窗,并在 scope 中注明。
    """
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    pool = [e for e in events if (e.get("time") or "").startswith(today)]
    scope = "今日"
    if not pool:
        pool = events
        scope = window_label()
    scored = []
    for e in pool:
        sc = (e.get("sev") or 0) * 20 + (30 if e.get("kind") == "突发" else 0)
        scored.append((sc, e))
    scored.sort(key=lambda t: (t[0], t[1].get("time") or ""), reverse=True)
    items = []
    for _sc, e in scored[:top]:
        items.append({
            "time": e.get("time", ""), "title": e.get("title", ""),
            "url": e.get("url", ""),
            "country": e.get("country", ""), "city": e.get("city", ""),
            "lat": e.get("lat"), "lng": e.get("lng"),
            "kind": e.get("kind", ""), "sev": e.get("sev", 0),
        })
    return {"scope": scope, "asof": now.strftime("%Y-%m-%d %H:%M"), "items": items}


# ---------------------------------------------------------------- HTML 生成

_PAGE_JS = r"""
  function _hasBurst(d) {
    if (!d || !d.items) return false;
    for (var i = 0; i < d.items.length; i++) { if (d.items[i].kind === '突发') return true; }
    return false;
  }

  // 事件严重度配色:刻意走「品红 → 琥珀 → 青」,避开红/绿。
  // 原因:在 A 股语境里红=涨、绿=跌已经被占用,事件再用红,同一个颜色在同一块屏幕上
  // 就有两种含义,久看必混。下面这个函数是严重度配色的唯一定义点,点/柱/弧/光环/徽章都从它派生。
  function colorOf(sev) { return sev >= 3 ? '#ff4d8d' : (sev === 2 ? '#ffb340' : '#4dd0e1'); }
  function ringColorOf(sev) {
    var c = colorOf(sev);
    var r = parseInt(c.slice(1, 3), 16), g = parseInt(c.slice(3, 5), 16), b = parseInt(c.slice(5, 7), 16);
    return function (t) { return 'rgba(' + r + ',' + g + ',' + b + ',' + (1 - t) + ')'; };
  }
  function _fmtTm(s) { return (s || '').slice(5, 16); }
  function _lvTxt(s) { return s >= 3 ? '高' : '中'; }
  var _SRC_COLOR = { '东财': '#7fa8d9', '见闻': '#c2a6ff', '财联社': '#ffa45c', '金十': '#5fd3c4' };
  function _srcTag(s) {
    var c = _SRC_COLOR[s || ''] || '#7fa8d9';
    return '<span class="src" style="color:' + c + ';border-color:' + c + '">' + (s || '') + '</span>';
  }

  // 同地点多条事件聚合为一个光点(数字徽章由 labels 显示)
  // 键用「国家|城市」:同一个城市在 geo.SPOTS 里可能有多个坐标(如"纽约"相距 6km 的两条),
  // 把经纬度算进 key 会被拆成两个光点、在球面上叠着穿模。city 为空时才退回坐标。
  function _gkey(e) {
    return e.city ? (e.country + '|' + e.city) : (e.country + '|' + e.lat + '|' + e.lng);
  }
  var _seen = {}, groups = [];
  EVENTS.forEach(function (e) {
    var k = _gkey(e);
    if (_seen[k]) {
      var g = _seen[k];
      g.count += 1;
      g.items.push(e);
      if (e.sev > g.sev) g.sev = e.sev;
    } else {
      var nw = { lat: e.lat, lng: e.lng, city: e.city, country: e.country, sev: e.sev,
                 count: 1, items: [e] };
      _seen[k] = nw;
      groups.push(nw);
    }
  });

  // 多事件地点的数字徽章数据(图层开关会复用)
  var multiGroups = groups.filter(function (d) { return d.count > 1; });

  var _panel = document.getElementById('sidePanel');
  var _ctlEl = document.getElementById('ctl');
  function _ctlDim(show) {
    if (!_ctlEl) return;
    _ctlEl.style.opacity = show ? '' : '0';
    _ctlEl.style.pointerEvents = show ? '' : 'none';
  }
  function hidePanel() { _panel.style.display = 'none'; _ctlDim(true); }

  function rowsFor(list) {
    return list.map(function (e) {
      // 有原文链接的标题做成可点(新窗口),点标题跳原站
      var u = String(e.url || "").replace(/"/g, "%22");
      var t = u
        ? '<a href="' + u + '" target="_blank" rel="noopener noreferrer"' +
          ' style="color:#dfe6f2;text-decoration:none"' +
          ' onmouseover="this.style.color=\'#8fd0ff\';this.style.textDecoration=\'underline\'"' +
          ' onmouseout="this.style.color=\'#dfe6f2\';this.style.textDecoration=\'none\'"' +
          ' title="打开原文（' + (e.src || "") + '）">' + e.title +
          '<span style="color:#6b7a97;font-size:10px;margin-left:4px">↗</span></a>'
        : e.title;
      return '<div class="ev"><span class="tm">' + _fmtTm(e.time) + '</span>' +
             _srcTag(e.src) + t + '</div>';
    }).join('');
  }
  // 突发消息 与 一般经济消息 分节展示
  function bodyFor(list) {
    var burst = list.filter(function (e) { return e.kind === '突发'; });
    var rt = list.filter(function (e) { return e.kind !== '突发'; });
    var h = '';
    if (burst.length) h += '<div class="sec">🔥 突发消息</div>' + rowsFor(burst);
    if (rt.length) h += '<div class="sec">📊 一般经济</div>' + rowsFor(rt);
    return h || '<div class="ev" style="color:#8fa1c0">暂无该范围的近期消息</div>';
  }

  function fillPanel(icon, head, meta, body) {
    _panel.style.display = 'block';
    _panel.innerHTML =
      '<div class="ph"><h3>' + icon + ' ' + head + '</h3><span class="x" title="关闭">✕</span></div>' +
      '<div class="meta">' + meta + '</div>' +
      '<div class="list">' + body + '</div>';
    _panel.querySelector('.x').onclick = hidePanel;
    _ctlDim(false); // 查看详情时淡出右下控制面板
  }

  // 点击光点 → 该地点全部事件(突发/一般分节)
  function showGroup(p) {
    fillPanel('📍', p.country + ' · ' + p.city,
      '地点事件 ' + p.count + ' 条 · 影响等级：<span style="color:' + colorOf(p.sev) + '">' +
        _lvTxt(p.sev) + '</span> · 点击其它光点 / 国家可切换',
      bodyFor(p.items));
  }

  // 点击国家 → 该国最近所有信源消息(突发/一般分节)
  function showCountry(zh, en) {
    var list = EVENTS.filter(function (e) { return e.country === zh; });
    var top = 0;
    list.forEach(function (e) { if (e.sev > top) top = e.sev; });
    var meta = '国家近况 · 信源消息共 ' + list.length + ' 条' +
      (list.length ? ' · 最高影响：<span style="color:' + colorOf(top) + '">' + _lvTxt(top) + '</span>' : '');
    var head = zh + (en && en !== zh
      ? ' <span style="color:#6b7a97;font-size:12px;font-weight:400;">' + en + '</span>' : '');
    fillPanel('🗺', head, meta, bodyFor(list));
  }

  var world = Globe()(document.getElementById('globeViz'))
    .backgroundColor('#01030a')
    .showGraticules(true)
    .globeImageUrl(DAY_URL)
    .atmosphereColor('#a9cfff')
    .atmosphereAltitude(0.21)

    // 国家边界(点击国家 → 该国消息)
    .polygonsData(WORLD_GEO.features)
    .polygonAltitude(0.006)
    .polygonCapColor(function () { return 'rgba(90,150,255,0.06)'; })
    .polygonSideColor(function () { return 'rgba(90,150,255,0.03)'; })
    .polygonStrokeColor(function () { return 'rgba(255,255,255,0.28)'; })

    // 飞行弧线:同一主题(tag)的事件分散在多个地点时,从"主地点"(事件最多/影响最高)
    // 连向组内其它地点,表示同一件事在多处发生、彼此相关。
    // 数量控制:每主题最多 3 条、总量不超过 24 条,免得糊成一片。
    // globe.gl 的弧线走的是球面大圆弧,天然贴着地球飞过,不会穿模。
    .arcsData((function () {
      var byTag = {}, out = [], seenArc = {};
      (groups || []).forEach(function (g) {
        ((g.items || [])).forEach(function (e) {
          var t = e.tag || '其它';
          (byTag[t] = byTag[t] || []).push(g);
        });
      });
      Object.keys(byTag).forEach(function (t) {
        var gs = byTag[t].filter(function (g) { return g.sev >= 2 || g.count > 1; });
        var uniq = [], seenPt = {};
        gs.forEach(function (g) {
          var k = g.lat + ',' + g.lng;
          if (!seenPt[k]) { seenPt[k] = 1; uniq.push(g); }
        });
        if (uniq.length < 2) return;
        uniq.sort(function (a, b) { return (b.count * 10 + b.sev) - (a.count * 10 + a.sev); });
        var hub = uniq[0];
        uniq.slice(1, 4).forEach(function (g) {
          var k = hub.lat + ',' + hub.lng + '>' + g.lat + ',' + g.lng;
          if (seenArc[k]) return;
          seenArc[k] = 1;
          out.push({ startLat: hub.lat, startLng: hub.lng, endLat: g.lat, endLng: g.lng,
                     sev: Math.max(hub.sev || 0, g.sev || 0), tag: t });
        });
      });
      return out.slice(0, 24);
    })())
    .arcStartLat(function (d) { return d.startLat; })
    .arcStartLng(function (d) { return d.startLng; })
    .arcEndLat(function (d) { return d.endLat; })
    .arcEndLng(function (d) { return d.endLng; })
    // 起点淡、终点实:配合流动的虚线,像"信号正沿着弧线传导"
    .arcColor(function (d) {
      var c = colorOf(d.sev);
      var r = parseInt(c.slice(1, 3), 16), g = parseInt(c.slice(3, 5), 16), b = parseInt(c.slice(5, 7), 16);
      return ['rgba(' + r + ',' + g + ',' + b + ',0.35)', c];
    })
    .arcStroke(0.4)
    .arcDashLength(0.4)
    .arcDashGap(0.22)
    .arcDashAnimateTime(2600)

    .pointsData(groups)
    .pointLat(function (d) { return d.lat; })
    .pointLng(function (d) { return d.lng; })
    .pointColor(function (d) { return colorOf(d.sev); })
    // 3D 强度柱:只有「高影响(sev>=2) 或 多事件(count>1)」的地点才立起来,其余贴地。
    // 这样既保住体量感,又不会像以前那样"每个点都是柱、同城几根叠着穿模" ——
    // 同城在聚合阶段已合并,所以一个地点只有一根柱。
    // 高度单位是球半径的比例(国界挤出是 0.006),这里 0.05~0.12:比国界明显、又不喧宾夺主。
    .pointAltitude(function (d) {
      if (d.sev < 2 && d.count <= 1) return 0;
      return 0.02 + d.sev * 0.015 + Math.min(0.05, (d.count - 1) * 0.007);
    })
    .pointRadius(function (d) {
      var r = (d.sev >= 2 ? 0.42 : 0.26) + d.sev * 0.1 + (d.count > 1 ? Math.min(0.28, d.count * 0.045) : 0);
      // 立柱的地点把柱身收细,免得挡住周围;贴地圆片保持原尺寸
      return (d.sev < 2 && d.count <= 1) ? r : r * 0.55;
    })
    .pointsTransitionDuration(600)

    // 外圈光环:贴地圆点之外再套一圈脉冲,把"位置在哪里"说得更清楚
    // (替代原来立体柱的体量感;原来这里置空 = 完全关掉了涟漪)
    .ringsData(groups.filter(function (d) { return d.sev >= 2 || d.count > 1; }))
    .ringLat(function (d) { return d.lat; })
    .ringLng(function (d) { return d.lng; })
    .ringColor(function (d) { return ringColorOf(d.sev); })
    .ringMaxRadius(function (d) {
      return 1.6 + d.count * 0.45 + d.sev * 0.9 + (_hasBurst(d) ? 1.4 : 0);
    })
    .ringPropagationSpeed(function (d) { return _hasBurst(d) ? 2.2 : 1.15; })
    .ringRepeatPeriod(function (d) { return _hasBurst(d) ? 420 : 780; })

    // 事件条数数字徽章(仅多事件地点显示)
    .labelsData(multiGroups)
    .labelLat(function (d) { return d.lat; })
    .labelLng(function (d) { return d.lng; })
    .labelText(function (d) { return String(d.count); })
    .labelSize(1)
    .labelColor(function (d) { return colorOf(d.sev); })
    .labelDotRadius(0)

    .onPointClick(function (p) { if (_dragging) { return; } _pointClickedAt = Date.now(); showGroup(p); })
    .onPolygonClick(function () { /* 改用 _bindCountryClick():在自绘地球上精确归属 */ });

  // 固定视角,不自动旋转(交互:拖拽/缩放 + 点击国家或光点)
  world.pointOfView({ lat: 18, lng: 20, altitude: 2.6 }, 0);
  setTimeout(function () { world.pointOfView({ lat: 18, lng: 20, altitude: 1.9 }, 1800); }, 300);

  // ===== 相机自动巡游 =====
  // 四条效果(强度柱 / 飞行弧线 / 光环 / 相机巡游)默认全开,不再有图层面板:
  // 强度柱就是上面 pointAltitude 的默认行为,光环是 ringsData 的默认数据,
  // 弧线是 arcsData 的默认数据,这里只管巡游。
  // 巡游按「影响×10 + 条数」轮播热点;鼠标按下时先让位给用户,松手 8 秒后自动续上。
  (function () {
    var _ci = 0, _timer = null, _resume = null;
    function cruiseTick() {
      var list = (groups || []).slice().sort(function (a, b) {
        return (b.sev * 10 + b.count) - (a.sev * 10 + a.count);
      }).slice(0, 12);
      if (!list.length) return;
      var g = list[_ci % list.length];
      _ci++;
      world.pointOfView({ lat: g.lat, lng: g.lng, altitude: 1.35 }, 1600);
    }
    function pauseCruise() {
      if (_timer) { clearInterval(_timer); _timer = null; }
    }
    function resumeCruise() {
      if (_timer) { return; }
      cruiseTick();
      _timer = setInterval(cruiseTick, 5200);
    }
    var gv = document.getElementById('globeViz');
    if (gv) {
      gv.addEventListener('pointerdown', function () {        // 用户接管镜头
        pauseCruise();
        if (_resume) { clearTimeout(_resume); _resume = null; }
      });
    }
    // 在 window 上收 pointerup:用户可能把鼠标拖到地球外面才松手
    window.addEventListener('pointerup', function () {
      if (_resume) { clearTimeout(_resume); }
      _resume = setTimeout(resumeCruise, 8000);
    });
    resumeCruise();
  })();

  // ===== 星空背景(运行时 canvas 生成星图,不新增资产)+ 大气层 =====
  function makeStarDataURL() {
    try {
      var W = 2048, H = 1024;
      var cv = document.createElement('canvas');
      cv.width = W; cv.height = H;
      var cx = cv.getContext('2d');
      cx.fillStyle = '#000';
      cx.fillRect(0, 0, W, H);
      // 淡淡的银河斜带,增加纵深感
      var grd = cx.createLinearGradient(0, 0, W, H);
      grd.addColorStop(0, 'rgba(140,160,220,0)');
      grd.addColorStop(0.5, 'rgba(150,175,235,0.07)');
      grd.addColorStop(1, 'rgba(140,160,220,0)');
      cx.fillStyle = grd;
      cx.fillRect(0, 0, W, H);
      // 星星(大小/亮度/色温随机)
      for (var i = 0; i < 1100; i++) {
        var px = Math.random() * W, py = Math.random() * H;
        var rv = Math.random();
        var sz = rv < 0.85 ? 0.4 + Math.random() * 0.7 : 1.2 + Math.random() * 1.5;
        var al = 0.25 + Math.random() * 0.75;
        var tone = Math.random() < 0.85 ? 255 : (Math.random() < 0.5 ? 205 : 225);
        var g2 = Math.max(0, tone - Math.floor(Math.random() * 35));
        var b2 = Math.min(255, g2 + Math.floor(Math.random() * 25));
        cx.fillStyle = 'rgba(' + tone + ',' + g2 + ',' + b2 + ',' + al.toFixed(3) + ')';
        cx.beginPath();
        cx.arc(px, py, sz, 0, 6.2832);
        cx.fill();
      }
      return cv.toDataURL('image/png');
    } catch (err) { return ''; }
  }
  try {
    var _stars = makeStarDataURL();
    if (_stars && typeof world.backgroundImageUrl === 'function') {
      world.backgroundImageUrl(_stars);
    }
  } catch (err) { /* 星空不可用则忽略,不影响地球 */ }

  // ===== 观感:海洋流动(停用) =====
  // 曾尝试向地球材质注入自定义 shader 制造海面流动光,实测会导致地球贴图不显示,
  // 已彻底移除(不再调用 globeMaterial / onBeforeCompile)。后续如需海洋动效,
  // 将采用"独立水光层 mesh"方式,不触碰地球本体材质。

  // ===== 左栏"今日热点"卡片 =====
  function goHot(it) {
    var grp = null;
    if (it) {
      for (var h = 0; h < groups.length; h++) {
        var g = groups[h];
        if (g.country === it.country && g.city === it.city &&
            String(g.lat) === String(it.lat) && String(g.lng) === String(it.lng)) { grp = g; break; }
      }
    }
    if (grp) {
      world.pointOfView({ lat: grp.lat, lng: grp.lng, altitude: 1.05 }, 900);
      setTimeout(function () { showGroup(grp); }, 1000);
    } else if (it && it.country) {
      if (it.lat != null && it.lng != null) {
        world.pointOfView({ lat: it.lat, lng: it.lng, altitude: 1.3 }, 900);
      }
      setTimeout(function () { showCountry(it.country, ''); }, 1000);
    }
  }
  function renderHot() {
    var asof = document.getElementById('hotAsOf');
    var box = document.getElementById('hotBody');
    if (!HOT || !HOT.items || !HOT.items.length) {
      if (asof) asof.textContent = '';
      box.innerHTML = '<div class="hnone">暂无高热度事件</div>';
      return;
    }
    if (asof) asof.textContent = (HOT.scope || '') + ' · ' + (HOT.asof || '');
    var h = '';
    for (var k = 0; k < HOT.items.length; k++) {
      var it = HOT.items[k];
      var kc = it.kind === '突发' ? 'hkb' : 'hkn';
      var ctry = it.country ? '<span class="hc">' + it.country + '</span>' : '';
      h += '<div class="hr" data-i="' + k + '"><div class="hr1"><span class="ht">' + _fmtTm(it.time) +
        '</span><span class="hk ' + kc + '">' + (it.kind || '') + '</span>' + ctry +
        '</div><div class="htx">' + it.title + '</div></div>';
    }
    box.innerHTML = h;
    var rows = box.querySelectorAll('.hr');
    for (var m = 0; m < rows.length; m++) {
      (function (row, it) { row.onclick = function () { goHot(it); }; })(rows[m], HOT.items[m]);
    }
  }
  var hotFoldEl = document.getElementById('hotFold');
  var hotBodyEl = document.getElementById('hotBody');
  var hotFolded = false;
  if (hotFoldEl) {
    hotFoldEl.onclick = function () {
      hotFolded = !hotFolded;
      hotBodyEl.style.display = hotFolded ? 'none' : 'block';
      hotFoldEl.textContent = hotFolded ? '＋' : '－';
    };
  }
  renderHot();

  // ===== 真实地球:自建网格 + 着色器(昼夜分界 / 地形起伏 / 海面高光 / 大气边缘) =====
  var EOL = String.fromCharCode(10);
  var _realEarth = null, _earthMat = null;
  var _sunDir = new THREE.Vector3(1, 0, 0);
  var _texReady = 0;
  var EARTH_VS = [
    'uniform sampler2D uElev;',
    'uniform float uElevScale;',
    'uniform vec2 uUvOff;',
    'uniform float uUvFlip;',
    'varying vec2 vUv;',
    'varying vec3 vN;',
    'varying vec3 vW;',
    'void main() {',
    '  vUv = vec2(uv.x + uUvOff.x, mix(uv.y, 1.0 - uv.y, uUvFlip));',
    '  float h = texture2D(uElev, uv).r;',
    '  vec3 pos = position + normal * (h * uElevScale);',
    '  vec4 wp = modelMatrix * vec4(pos, 1.0);',
    '  vW = wp.xyz;',
    '  vN = normalize(mat3(modelMatrix) * normal);',
    '  gl_Position = projectionMatrix * viewMatrix * wp;',
    '}'
  ].join(EOL);
  var EARTH_FSA = [
    'uniform sampler2D uDay;',
    'uniform sampler2D uWater;',
    'uniform vec3 uCamPos;',
    'uniform float uTime;',
    'varying vec2 vUv;',
    'varying vec3 vN;',
    'varying vec3 vW;',
    'float hash21(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }',
    'float cnoise(vec2 p) {',
    '  vec2 i = floor(p), f = fract(p);',
    '  f = f * f * (3.0 - 2.0 * f);',
    '  return mix(mix(hash21(i), hash21(i + vec2(1.0, 0.0)), f.x), mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), f.x), f.y);',
    '}',
    'void main() {',
    '  vec3 n = normalize(vN);',
    '  vec3 V = normalize(uCamPos - vW);',
    '  vec3 base = texture2D(uDay, vUv).rgb;',
    '  vec3 col = base * 1.06;',
    '  float water = texture2D(uWater, vUv).r;',
  ];
  var EARTH_FSB = [
    '  float camDist = length(uCamPos - vW);',
    '  float detail = 1.0 - smoothstep(240.0, 430.0, camDist);',
    '  if (water > 0.30 && detail > 0.02) {',
    '    vec2 wuv = vUv * vec2(180.0, 100.0);',
    '    float t1 = uTime * 0.030;',
    '    float n1 = cnoise(wuv + vec2(t1, t1 * 0.6));',
    '    float n2 = cnoise(wuv * 2.3 - vec2(t1 * 1.4, t1 * 0.8));',
    '    vec3 wn = normalize(n + vec3((n1 - 0.5) * 0.22 * detail, (n2 - 0.5) * 0.22 * detail, 0.0));',
    '    vec3 waterCol = vec3(0.045, 0.13, 0.27);',
    '    vec3 skyCol = vec3(0.26, 0.46, 0.74);',
    '    float shade = 0.90 + 0.20 * (n1 * 0.6 + n2 * 0.4);',
    '    float fres = pow(1.0 - max(dot(wn, V), 0.0), 5.0);',
    '    col = mix(col, waterCol * shade, smoothstep(0.30, 0.62, water) * (0.55 + 0.45 * detail));',
    '    col = mix(col, skyCol, fres * 0.45 * detail);',
    '  }',
    '  col += vec3(0.32, 0.58, 1.0) * pow(1.0 - max(dot(n, V), 0.0), 3.0) * 0.22;',
    '  gl_FragColor = vec4(col, 1.0);',
    '}'
  ];
  var EARTH_FS = EARTH_FSA.concat(EARTH_FSB).join(EOL);

  function texOK() {
    _texReady += 1;
    if (_texReady >= 1 && _realEarth) { _realEarth.visible = true; }
  }
  function loadTex(L, url, fb, count) {
    var done = function () { if (count) texOK(); };
    var t = L.load(url, done, undefined, function () {
      try {
        if (!fb) { done(); return; }
        var img = new Image();
        img.onload = function () { t.image = img; t.needsUpdate = true; done(); };
        img.onerror = function () { done(); };
        img.src = fb;
      } catch (e) { done(); }
    });
    try {
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.ClampToEdgeWrapping;
    } catch (e) {}
    return t;
  }

  var _ray = new THREE.Raycaster(), _ndc = new THREE.Vector2();
  var _pointClickedAt = 0;
  var _dragging = false;   // 拖动地球期间置真:拖动结束的 click 不应改变镜头
  function _pointInRing(lng, lat, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if (((yi > lat) !== (yj > lat)) && (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi)) { inside = !inside; }
    }
    return inside;
  }
  function _countryAt(lat, lng) {
    try {
      var feats = WORLD_GEO.features || [];
      for (var k = 0; k < feats.length; k++) {
        var f = feats[k], g = f.geometry || {}, co = g.coordinates || [];
        var polys = g.type === 'Polygon' ? [co] : (g.type === 'MultiPolygon' ? co : []);
        for (var a = 0; a < polys.length; a++) {
          var ring = polys[a] && polys[a][0];
          if (ring && ring.length > 2 && _pointInRing(lng, lat, ring)) { return f; }
        }
      }
    } catch (e) {}
    return null;
  }
  function _bindCountryClick() {
    try {
      var el = world.renderer ? world.renderer().domElement : null;
      if (!el || !_realEarth) { return; }
      el.addEventListener('pointerdown', function () { _dragging = false; });
      el.addEventListener('pointermove', function (ev) { if (ev.buttons) { _dragging = true; } });
      el.addEventListener('click', function (ev) {
        if (_dragging) { return; }   // 拖动过 → 松开鼠标不改变镜头
        try {
          if (Date.now() - _pointClickedAt < 400) { return; }   // 刚点过光点,交给光点详情
          var r = el.getBoundingClientRect();
          _ndc.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
          _ndc.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
          _ray.setFromCamera(_ndc, world.camera());
          var hits = _ray.intersectObject(_realEarth, false);
          if (!hits.length) { return; }
          var p = hits[0].point.clone().normalize();
          var geo = (typeof world.toGeoCoords === 'function') ? world.toGeoCoords(p)
            : { lat: 90 - Math.acos(Math.max(-1, Math.min(1, p.y))) * 180 / Math.PI,
                lng: 90 - Math.atan2(p.z, p.x) * 180 / Math.PI };
          var lat = geo.lat;
          var lng = ((geo.lng + 540) % 360) - 180;
          var f = _countryAt(lat, lng);
          var en = f ? (((f.properties) || {}).name || '') : '';
          var zh = COUNTRY_ZH[en] || en || (lat.toFixed(1) + '°' + (lng >= 0 ? 'E' : 'W') + ', ' + Math.abs(lng).toFixed(1) + '°');
          world.pointOfView({ lat: lat, lng: lng, altitude: 1.25 }, 900);
          showCountry(zh, en);
        } catch (e) {}
      });
    } catch (e) {}
  }

  function makeEarth() {
    try {
      if (_realEarth) return true;
      var sc = world.scene ? world.scene() : null;
      if (!sc) return false;
      var L = new THREE.TextureLoader();
      L.setCrossOrigin(undefined);
      _earthMat = new THREE.ShaderMaterial({
        uniforms: {
          // 三张都改成内嵌 data URL(见模板里的 DAY8K_URL / ELEV_URL / WATER_URL):
          // file:// 打开时用本地文件路径会让 texSubImage2D 抛 SecurityError,贴图进不去 GPU。
          // uDay 仍保留 DAY_URL 作兜底,万一内嵌图也失败还能退回 blue-marble。
          uDay: { value: loadTex(L, DAY8K_URL, DAY_URL, true) },
          uElev: { value: loadTex(L, ELEV_URL, null, false) },
          uWater: { value: loadTex(L, WATER_URL, null, false) },
          uCamPos: { value: new THREE.Vector3() },
          uTime: { value: 0.0 },
          uElevScale: { value: 0.30 },
          uUvOff: { value: new THREE.Vector2(0.25, 0) },
          uUvFlip: { value: 0.0 }
        },
        vertexShader: EARTH_VS,
        fragmentShader: EARTH_FS
      });
      // 球体网格:160×100 段约 1.6 万个三角面,对 1080p 下的观感毫无增益,却白白吃掉
      // 一半的顶点/片元开销。降到 96×64 后球缘依旧平滑(着色器只做贴图采样,不依赖细分)。
      _realEarth = new THREE.Mesh(new THREE.SphereGeometry(100.05, 96, 64), _earthMat);
      _realEarth.visible = false;
      sc.add(_realEarth);
      _texReady = 0;
      setTimeout(function () { if (_realEarth) { _realEarth.visible = true; } }, 3000);
      setTimeout(function () {
        // 自建地球(着色器+贴图)确认真可用后,隐藏 globe.gl 的原地球:避免深度冲突,并让经纬网正常显示
        try {
          if (_realEarth && _realEarth.visible) {
            var sc0 = world.scene ? world.scene() : null;
            if (sc0) {
              sc0.traverse(function (o) {
                if (!o.isMesh || !o.geometry || !o.material) return;
                var p0 = o.geometry.parameters || {};
                if (o.geometry.type === 'SphereGeometry' && Math.abs((p0.radius || 0) - 100) < 0.01 && o.material.map) {
                  o.visible = false;
                }
              });
            }
          }
        } catch (e) {}
      }, 5000);
      return true;
    } catch (e) { return false; }
  }

  function updateEarthCamera() {
    try {
      if (!_earthMat) return;
      var cam = world.camera ? world.camera() : null;
      if (cam) { _earthMat.uniforms.uCamPos.value.copy(cam.position); }
    } catch (e) {}
  }

  // 地球贴图校正(备用):按 U 水平旋转 90°,按 V 垂直翻转
  document.addEventListener('keydown', function (ev) {
    if (!_earthMat) return;
    var u = _earthMat.uniforms;
    if (ev.key === 'u' || ev.key === 'U') {
      u.uUvOff.value.x = (u.uUvOff.value.x + 0.25) % 1;
    } else if (ev.key === 'v' || ev.key === 'V') {
      u.uUvFlip.value = u.uUvFlip.value > 0.5 ? 0 : 1;
    } else { return; }
    if (window.console && console.log) {
      console.log('[地球贴图校正] 水平偏移', u.uUvOff.value.x.toFixed(2), '圈; 垂直翻转', u.uUvFlip.value > 0.5 ? '开' : '关');
    }
  });

  makeEarth();
  _bindCountryClick();
  var _t0 = Date.now();
  var _earthRAF = 0;
  // 注意用函数声明(而不是 (function earthLoop(){...})() 这种具名函数表达式):
  // 后者的名字只在函数体内部可见,visibilitychange 里就调不到了。
  function earthLoop() {
    updateEarthCamera();
    try { if (_earthMat) { _earthMat.uniforms.uTime.value = (Date.now() - _t0) / 1000; } } catch (e) {}
    _earthRAF = requestAnimationFrame(earthLoop);
  }
  earthLoop();
  // 页面切到后台时停掉渲染循环:省电,切回来也更快进入稳定帧。
  document.addEventListener('visibilitychange', function () {
    try {
      if (document.hidden) {
        if (_earthRAF) { cancelAnimationFrame(_earthRAF); _earthRAF = 0; }
      } else if (!_earthRAF) {
        earthLoop();
      }
    } catch (e) {}
  });

  // 说明:原有一段 applyRealTextures()/waitTexReady() 给 globe.gl 的原地球加
  // specular/高光与 bump 起伏。但那颗原地球在 5 秒后就被上面那段逻辑隐藏了
  // (o.visible = false),自建着色器地球又不读这两个贴图 —— 这段属于纯粹的空转,
  // 白白跑定时器与两次贴图解码。已删除。真正在用的三张贴图见 makeEarth() 的
  // uDay/uElev/uWater(它们走内嵌 data URL,file:// 下也不会被 CORS 拦)。
"""

_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全球宏观事件 · 3D 实时地图</title>
<style>
  /* ===== 设计令牌(与主页同源) =====
     地球页原先自成一派(蓝紫边框 + 一堆硬编码色),这里对齐主页那一套:
     层次靠背景亮度分级、强调色单一(橙)、红绿只留给涨跌语义。 */
  :root{
    --bg-0:#0a0e14; --bg-1:#111721; --bg-2:#18202c; --bg-3:#202b3a;
    --line-1:#1b2430; --line-2:#28323f;
    --fg-0:#e9eef6; --fg-1:#a9b5c6; --fg-2:#78879b; --fg-3:#556072;
    --accent:#ff9f1c; --accent-dim:rgba(255,159,28,.14); --accent-line:rgba(255,159,28,.42);
    --up:#f0453a; --down:#19a35f; --flat:#8b93a1;
    --r-1:4px; --r-2:8px; --r-3:12px;
    --dur-fast:120ms; --dur:220ms; --dur-slow:420ms;
    --ease:cubic-bezier(.2,.7,.2,1);
  }
  html, body { margin: 0; padding: 0; height: 100%; background: #04060d; overflow: hidden;
               font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
                            "PingFang SC", "Hiragino Sans GB", system-ui, sans-serif;
               color: var(--fg-0);
               font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; }
  #globeViz { position: fixed; inset: 0; }
  .hud { position: fixed; z-index: 10; pointer-events: none; }
  #title { left: 22px; top: 18px; max-width: calc(100vw - 460px); }
  #title h1 { font-size: 20px; margin: 0 0 4px; font-weight: 600; letter-spacing: 1px; }
  #title .sub { font-size: 12px; color: var(--fg-1); }
  #tip { right: 22px; bottom: 22px; max-width: 320px; background: rgba(17,23,33,.88);
         border: 1px solid var(--line-1); border-radius: var(--r-2); padding: 12px 14px; font-size: 13px;
         backdrop-filter: blur(4px); display: none; }
  #tip .t { font-size: 14px; font-weight: 600; color: var(--fg-0); margin-bottom: 5px; }
  #tip .m { color: var(--fg-1); font-size: 12px; margin-top: 6px; }
  #back { right: 22px; top: 18px; pointer-events: auto; }
  #back a { color: var(--fg-1); text-decoration: none; font-size: 13px; border: 1px solid var(--line-1);
            background: rgba(17,23,33,.7); padding: 8px 14px; border-radius: var(--r-2); }
  #back a:hover { color: var(--fg-0); border-color: var(--accent-line); }
  /* 右侧详情面板(点击国家/光点后显示) */
  #sidePanel { position: fixed; z-index: 12; right: 18px; top: 64px; bottom: 18px; width: 380px;
               max-width: 50vw; background: rgba(17,23,33,.96); border: 1px solid var(--line-1);
               border-radius: var(--r-3); padding: 14px 16px; box-sizing: border-box;
               box-shadow: 0 6px 22px rgba(0,0,0,.38); font-size: 13px;
               pointer-events: auto; display: none; overflow-y: auto; }
  #sidePanel .ph { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  #sidePanel h3 { margin: 0; font-size: 16px; color: var(--fg-0); font-weight: 700; }
  #sidePanel .x { cursor: pointer; color: var(--fg-1); font-size: 18px; padding: 0 6px; line-height: 1; }
  #sidePanel .x:hover { color: var(--fg-0); }
  #sidePanel .meta { color: var(--fg-1); font-size: 12px; margin: 2px 0 10px; }
  #sidePanel .list { border-top: 1px solid var(--line-1); }
  #sidePanel .sec { color: var(--fg-1); font-size: 12px; font-weight: 700; margin: 10px 0 2px; }
  #sidePanel .ev { border-bottom: 1px solid var(--line-1); padding: 8px 2px; line-height: 1.55; color: var(--fg-0); }
  #sidePanel .tm { color: var(--fg-2); font-size: 11px; margin-right: 6px; white-space: nowrap; }
  #sidePanel .src { font-size: 10px; color: var(--fg-1); border: 1px solid var(--line-2);
                    border-radius: var(--r-1); padding: 0 4px; margin-right: 6px; }
  /* 左栏今日热点卡 */
  #hotWrap { position: fixed; z-index: 11; left: 22px; top: 126px; width: 318px; max-width: 36vw;
             pointer-events: auto; background: rgba(17,23,33,.94); border: 1px solid var(--line-1);
             border-radius: var(--r-3); box-shadow: 0 6px 22px rgba(0,0,0,.38); font-size: 13px;
             overflow: hidden; }
  #hotHead { display: flex; align-items: center; padding: 10px 12px 8px; }
  #hotHead .ht1 { font-size: 15px; font-weight: 700; color: var(--fg-0); letter-spacing: .5px; white-space: nowrap; }
  #hotHead .ht2 { flex: 1; min-width: 0; color: var(--fg-2); font-size: 11px; margin: 0 8px;
                  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #hotFold { cursor: pointer; color: var(--fg-1); border: 1px solid var(--line-2); border-radius: var(--r-1);
             width: 20px; height: 20px; text-align: center; line-height: 18px; font-size: 13px;
             flex: none; user-select: none; }
  #hotFold:hover { color: var(--fg-0); border-color: var(--accent-line); }
  #hotBody { max-height: 46vh; overflow-y: auto; border-top: 1px solid var(--line-1); }
  #hotBody .hr { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--line-1); }
  #hotBody .hr:hover { background: var(--bg-3); }
  #hotBody .hr1 { display: flex; align-items: center; margin-bottom: 3px; }
  #hotBody .ht { color: var(--fg-2); font-size: 11px; margin-right: 8px; white-space: nowrap; }
  #hotBody .hk { font-size: 10px; line-height: 1.6; padding: 0 5px; border-radius: var(--r-1); white-space: nowrap; }
  #hotBody .hkb { color: #ff9d9d; background: rgba(255, 77, 141, .14); border: 1px solid rgba(255, 77, 141, .38); }
  #hotBody .hkn { color: var(--fg-1); background: rgba(120, 150, 220, .10); border: 1px solid var(--line-2); }
  #hotBody .hc { margin-left: auto; color: var(--fg-1); font-size: 11px; padding-left: 8px; white-space: nowrap; }
  #hotBody .htx { color: var(--fg-0); line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2;
                  -webkit-box-orient: vertical; overflow: hidden; }
  #hotBody .hnone { color: var(--fg-2); padding: 14px 12px; }
  #hotHint { color: var(--fg-2); font-size: 11px; padding: 6px 12px 9px; }

  /* 交互过渡:与主页同一套时长与缓动 */
  #back a, #hotBody .hr, #hotFold, #sidePanel .x {
    transition: background-color var(--dur) var(--ease),
                border-color var(--dur) var(--ease),
                color var(--dur-fast) var(--ease);
  }
  @media (max-width: 960px) { #hotWrap { display: none; } }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: .001ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: .001ms !important;
    }
  }
</style>
__EXTRA_HEAD__
</head>
<body>
<div id="globeViz"></div>
__HEADER_HTML__
__EXTRA_BODY__
<div class="hud" id="hotWrap">
  <div id="hotHead">
    <span class="ht1">🔥 今日热点</span>
    <span class="ht2" id="hotAsOf"></span>
    <span id="hotFold" title="收起 / 展开">－</span>
  </div>
  <div id="hotBody"></div>
  <div id="hotHint">点击条目 → 飞往地点并查看该国详情</div>
</div>
<div class="hud" id="tip"></div>
<div class="hud" id="sidePanel"></div>

<script src="assets/three.min.js"></script>
<script src="assets/globe.gl.min.js"></script>
<script>
var SPEC_URL = "__SPECULAR_DATAURL__";
var BUMP_URL = "__BUMP_DATAURL__";
var DAY_URL = "__TEXTURE_DATAURL__";
// 下面三张才是着色器地球真正用的贴图,必须内嵌成 data URL:
// file:// 打开时浏览器把本地图片当"跨源数据",WebGL 的 texSubImage2D 会直接抛
// SecurityError(图片本身却能 load 成功,所以 onError 兜底永远不触发)——只有内嵌
// 成 data URL 才能既离线打开、又让贴图进得去 GPU。
var DAY8K_URL = "__DAY8K_DATAURL__";    // earth8k_day.jpg → 降到 4K
var ELEV_URL = "__ELEV_DATAURL__";      // earth4k_elev.jpg
var WATER_URL = "__WATER_DATAURL__";    // earth4k_water.png → 灰度 JPEG
var EVENTS = __EVENTS_JSON__ || [];
var HOT = __HOT_JSON__ || { items: [] };
var WORLD_GEO = __GEOJSON_DATA__ || { features: [] };
var COUNTRY_ZH = __COUNTRY_ZH__ || {};
var HOME = __PAYLOAD_JSON__ || {};
(function () {
  var tip = document.getElementById('tip');
  if (typeof THREE === 'undefined' || typeof Globe === 'undefined') {
    tip.style.display = 'block';
    tip.innerHTML = '<div class="t">加载失败</div><div class="m">assets/three.min.js 或 assets/globe.gl.min.js 未正确加载，请确认与 assets/ 同级。</div>';
    return;
  }
  if (!WORLD_GEO.features.length) {
    tip.style.display = 'block';
    tip.innerHTML = '<div class="t">国界数据缺失</div><div class="m">页面未注入国界 GeoJSON，国家点击不可用，但光点仍可查看。</div>';
  }
  if (!EVENTS.length) {
    // 事件为空仍渲染地球,仅提示(生成端可能遇信源瞬时空返回)
    tip.style.display = 'block';
    tip.innerHTML = '<div class="t">暂无事件</div><div class="m">本次抓取未归因到地点的重大事件，仍可浏览地球；重新运行生成命令即可刷新事件。</div>';
  }
__PAGE_JS__
__EXTRA_JS__
})();
</script>
</body>
</html>
"""


def _hot_items_json(events):
    return json.dumps(_hot_items(events), ensure_ascii=False).replace("</", "<\\/")


def _tex_dataurl(base, name, max_width=0, quality=0, gray=False):
    """把 assets 下的贴图读成 data URL,可选降采样。

    为什么非内嵌不可:用 file:// 打开页面时,浏览器把本地图片视作"跨源数据",
    WebGL 的 texSubImage2D 会抛 SecurityError,纹理进不去 GPU —— 地球就成了黑球。
    而图片本身能 load 成功,所以 loadTex 的 onError 兜底根本不会触发。
    内嵌成 data URL 后既满足「单文件、离线可开」,又不受这条限制。

    max_width  超过这个宽度就等比缩小(8K 贴图降到 4K 在屏幕上没有可感差别,
               却能把 base64 从 6MB 压到 1.2MB)
    quality    有值时转成 JPEG(地球水图只被 shader 读 .r 通道,丢 alpha 无影响)
    gray       转灰度 JPEG,体积更小
    Pillow 不可用时退回"原图直接内嵌",体积大但功能不受影响。
    """
    path = os.path.join(base, "assets", name)
    with open(path, "rb") as f:
        raw = f.read()
    mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    if max_width or quality or gray:
        try:
            import io as _io
            from PIL import Image
            im = Image.open(_io.BytesIO(raw))
            if max_width and im.width > max_width:
                h = max(1, int(round(im.height * max_width / float(im.width))))
                im = im.resize((max_width, h), Image.LANCZOS)
            buf = _io.BytesIO()
            if gray:
                im.convert("L").save(buf, "JPEG", quality=quality or 85, optimize=True)
                mime = "image/jpeg"
            elif quality:
                im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
                mime = "image/jpeg"
            else:
                im.save(buf, "PNG", optimize=True)
            raw = buf.getvalue()
        except Exception:
            pass
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def _assemble(events, header_html, assets_prefix="assets/",
              extra_head="", extra_body="", extra_js="", payload_json=""):
    """按模板拼装完整 HTML(贴图与国界 GeoJSON 均内嵌,规避 file:// 限制)。

    extra_* 为「主页指挥台」预留的注入点(默认空,不影响 events_live.html / detail 页):
      extra_head  <style>/标记 → 追加在模板样式之后
      extra_body  DOM 片段      → 追加在头部标题之后
      extra_js    JS 片段       → 追加在页面主脚本 IIFE 末尾(可直接访问世界/事件等内部变量)
      payload_json 主页数据 JSON → 注入为全局 HOME
    """
    base = os.path.dirname(os.path.abspath(__file__))
    tex_dataurl = _tex_dataurl(base, "earth-blue-marble.jpg")
    spec_dataurl = _tex_dataurl(base, "earth_specular_2048.jpg")
    bump_dataurl = _tex_dataurl(base, "earth_normal_2048.jpg")
    # 着色器地球真正用的三张:内嵌 + 降采样(原因见 _tex_dataurl)
    day8k_dataurl = _tex_dataurl(base, "earth8k_day.jpg", max_width=4096, quality=88)
    elev_dataurl = _tex_dataurl(base, "earth4k_elev.jpg", max_width=2048, quality=88)
    water_dataurl = _tex_dataurl(base, "earth4k_water.png", max_width=2048, quality=82, gray=True)
    with open(os.path.join(base, "assets", "world.geojson"), encoding="utf-8") as f:
        geo_data = f.read().strip()
    ev_json = json.dumps(events, ensure_ascii=False).replace("</", "<\\/")
    zh_json = json.dumps(COUNTRY_ZH, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE_HTML \
        .replace("assets/three.min.js", assets_prefix + "three.min.js") \
        .replace("assets/globe.gl.min.js", assets_prefix + "globe.gl.min.js") \
        .replace("__HEADER_HTML__", header_html) \
        .replace("__EXTRA_HEAD__", extra_head) \
        .replace("__EXTRA_BODY__", extra_body) \
        .replace("__EXTRA_JS__", extra_js) \
        .replace("__PAYLOAD_JSON__", payload_json or "{}") \
        .replace("__PAGE_JS__", _PAGE_JS) \
        .replace("__EVENTS_JSON__", ev_json) \
        .replace("__HOT_JSON__", _hot_items_json(events)) \
        .replace("__GEOJSON_DATA__", geo_data) \
        .replace("__COUNTRY_ZH__", zh_json) \
        .replace("__TEXTURE_DATAURL__", tex_dataurl) \
        .replace("__SPECULAR_DATAURL__", spec_dataurl) \
        .replace("__BUMP_DATAURL__", bump_dataurl) \
        .replace("__DAY8K_DATAURL__", day8k_dataurl) \
        .replace("__ELEV_DATAURL__", elev_dataurl) \
        .replace("__WATER_DATAURL__", water_dataurl) \
        .replace("__ASSETS__/", assets_prefix) \
        .replace("__EVENT_COUNT__", str(len(events)))


def _preview_header(n):
    return ('<div class="hud" id="title">'
            '<h1>🌍 全球宏观事件 · 3D 实时地图</h1>'
            '<div class="sub">' + window_label() + ' 重要财经事件（东财 7x24 + 华尔街见闻） · 共 '
            + str(n) + ' 条</div></div>')


def build_page(events, out_path):
    """生成真实事件 3D 预览 HTML（events_live.html）。"""
    html = _assemble(events, _preview_header(len(events)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


if __name__ == "__main__":
    from collections import Counter
    evs = fetch_events(limit=120)
    print("events:", len(evs))
    print("by src:", dict(Counter(e["src"] for e in evs)))
    print("by kind:", dict(Counter(e["kind"] for e in evs)))
    t0 = min((e["time"] for e in evs), default="")
    print("oldest kept:", t0)
    hot = _hot_items(evs)
    print("hot scope:", hot["scope"], "| items:", len(hot["items"]))
    for e in hot["items"][:8]:
        print(e["time"], e["kind"], e["country"], "|", e["title"][:44])
    for e in evs[:12]:
        print(e["time"], e["sev"], e["kind"], e["country"], e["src"], "|", e["title"][:40])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events_live.html")
    build_page(evs, out)
    print("saved:", out)
