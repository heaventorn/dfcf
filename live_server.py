# -*- coding: utf-8 -*-
"""主页 + 实时新闻服务（常驻）
================================
一个进程同时做两件事——**同端口同源**，前端无需 CORS：

  1) 静态发布项目根目录（与 serve_page.py 同样的职责）
     主页走 http://127.0.0.1:8766/output/index.html
     （不用 file://：浏览器会拦截页面读取本地 8K 地球贴图）
  2) 每 N 秒（默认 300 = 5 分钟）后台自动抓一次全球新闻，缓存最新结果并对外提供：
        GET /api/news          最新新闻 + 热点 + 地球光点聚合 + 版本号
        GET /api/news?ver=N    版本未变时只回 {"version": N, "unchanged": true}
        GET /api/health        服务状态（上次抓取时间 / 下次刷新倒计时 / 条数 / 错误）

前端（主页 HOME_JS 末尾的 Live 片段）轮询 /api/news，发现 version 变化就调用
window.__liveRefresh(events, hot, groups) **增量刷新**地球光点与新闻栏，不整页重载。

生命周期：由 main.py 以后台子进程拉起，**继承启动脚本的 console**，因此
关掉「启动爬虫.bat」窗口（或 Ctrl+C）时会一起退出，不留残余服务。
（早期版本用 CREATE_NO_WINDOW 会给子进程新建独立 console，关窗口杀不掉它。）

用法：
    python live_server.py             # 前台运行（Ctrl+C 关闭）
    python live_server.py --once      # 只抓一次，写入 output/live_news.json 后退出
    python live_server.py --interval 60 --port 8766
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config  # noqa: E402

PORT = getattr(config, "LIVE_PORT", 8766)
STATIC_EXT = (".jpg", ".jpeg", ".png", ".js", ".css", ".geojson")


# ---------------------------------------------------------------- 光点聚合

def _group_key(e):
    """光点聚合键：优先「国家|城市」。

    不能把经纬度放进 key —— 同一个城市在 geo.SPOTS 里可能对应多个坐标
    （例如"纽约"既有 40.75/-73.97 也有 40.71/-74.01，相距仅 6km），
    那样会被拆成两个光点、在球面上叠着穿模。city 为空时才退回坐标。
    """
    if e.get("city"):
        return "%s|%s" % (e.get("country"), e.get("city"))
    return "%s|%s|%s" % (e.get("country"), e.get("lat"), e.get("lng"))


def build_groups(events):
    """按「国家|城市」合并事件为地球光点。

    与 events.py 前端 _PAGE_JS 里的聚合规则保持一致（同一地点合并、sev 取最大、
    count 累加、items 收全部），这样动态刷新出来的光点与首屏渲染完全同构。
    """
    seen, groups = {}, []
    for e in (events or []):
        key = _group_key(e)
        g = seen.get(key)
        if g is not None:
            g["count"] += 1
            g["items"].append(e)
            if (e.get("sev") or 0) > (g.get("sev") or 0):
                g["sev"] = e.get("sev")
        else:
            nw = {"lat": e.get("lat"), "lng": e.get("lng"), "city": e.get("city"),
                  "country": e.get("country"), "sev": e.get("sev"),
                  "count": 1, "items": [e]}
            seen[key] = nw
            groups.append(nw)
    return groups


# ---------------------------------------------------------------- 新闻中心

class NewsHub:
    """后台定时抓取全球新闻，维护一份可被 HTTP 线程安全读取的快照。

    抓取失败时**保留上一次的数据**（只记录 error），避免把已有内容清空。
    """

    def __init__(self, interval=None, limit=None):
        self.interval = int(interval or getattr(config, "NEWS_REFRESH_SECONDS", 300))
        self.limit = int(limit or getattr(config, "NEWS_LIMIT", 1000))
        self._lock = threading.Lock()
        self._data = {"version": 0, "asof": "", "events": [], "hot": {"items": []},
                      "groups": [], "sources": {}, "count": 0, "error": None}
        self._next_at = 0.0
        self._stop = threading.Event()
        self._thread = None

    # ---- 读取 ----
    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def next_at(self):
        return self._next_at

    # ---- 抓取 ----
    def refresh(self):
        import events as events_mod
        try:
            from feeds import SOURCE_STATUS
        except Exception:
            SOURCE_STATUS = {}

        try:
            evs = events_mod.fetch_events(limit=self.limit)
            snap = {
                "version": self._data["version"] + 1,
                "asof": time.strftime("%Y-%m-%d %H:%M:%S"),
                "events": evs,
                "hot": events_mod._hot_items(evs),
                "groups": build_groups(evs),
                "sources": dict(SOURCE_STATUS),
                "count": len(evs),
                "error": None,
            }
            with self._lock:
                self._data = snap
            print("[live] 新闻已刷新 #%d：%d 条 · %s"
                  % (snap["version"], snap["count"], snap["asof"]), flush=True)
            return snap
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, e)
            with self._lock:
                self._data["error"] = msg
            print("[live] 新闻刷新失败：%s" % msg, flush=True)
            return None

    # ---- 后台循环 ----
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="news-refresh", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self.refresh()
            self._next_at = time.time() + self.interval
            self._stop.wait(self.interval)


# ---------------------------------------------------------------- HTTP

HUB = None  # 由 main() 注入
STOCK_HUB = None  # 个股后台刷新（StockHub），同样由 main() 注入


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        # 大贴图允许缓存，页面本身与 API 不缓存（便于立刻看到新数据）
        if self.path.lower().split("?")[0].endswith(STATIC_EXT):
            self.send_header("Cache-Control", "max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 允许用 file:// 或在其它端口打开主页时也能读到（同源访问不需要，但无害）
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path == "/api/news":
            snap = HUB.snapshot() if HUB else {}
            ver = 0
            for kv in qs.split("&"):
                if kv.startswith("ver="):
                    try:
                        ver = int(kv[4:])
                    except ValueError:
                        ver = 0
            if ver and ver == snap.get("version"):
                self._json({"version": ver, "unchanged": True})
            else:
                self._json(snap)
            return

        if path == "/api/health":
            snap = HUB.snapshot() if HUB else {}
            nxt = HUB.next_at() if HUB else 0
            self._json({
                "status": "ok",
                "version": snap.get("version", 0),
                "count": snap.get("count", 0),
                "asof": snap.get("asof", ""),
                "interval": HUB.interval if HUB else None,
                "next_refresh_in": max(0, int((nxt or 0) - time.time())) if HUB else None,
                "error": snap.get("error"),
            })
            return

        # ---- 个股终端：一次读回「报价 + 分时 + K线」----
        # 前端带 ver 轮询：内容没变只回 {"unchanged": true}（3.5 秒一轮绝大多数走这条路）。
        # 真正的抓取全在 StockHub 的后台档位线程里，这里只读缓存 —— 多个标签页同时刷
        # 同一只票，上游也只会被打一次。
        if path == "/api/stock":
            q = parse_qs(qs)
            code = (q.get("code") or [""])[0].strip()
            if not code:
                self._json({"ok": False, "msg": "缺少 code 参数（如 code=600941）"}, 400)
                return
            if STOCK_HUB is None:
                self._json({"ok": False, "msg": "个股服务未启动"}, 503)
                return
            try:
                client_ver = int((q.get("ver") or ["0"])[0] or 0)
            except ValueError:
                client_ver = 0
            period = (q.get("period") or ["day"])[0]
            with_intraday = (q.get("intraday") or ["1"])[0] != "0"
            try:
                payload, ver, changed = STOCK_HUB.read(
                    code, period=period, client_ver=client_ver,
                    with_intraday=with_intraday)
            except Exception as e:
                self._json({"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}, 500)
                return
            if not changed:
                self._json({"ok": True, "unchanged": True, "ver": ver})
            else:
                payload["ok"] = True
                self._json(payload)
            return

        if path == "/api/stock/health":
            if STOCK_HUB is None:
                self._json({"ok": False, "msg": "个股服务未启动"}, 503)
                return
            payload = {"ok": True}
            payload.update(STOCK_HUB.health())
            self._json(payload)
            return

        # 个股页：/stock?code=600941（页面自己在前端取 /api/stock）。
        # 给个不带 .html 的短地址，方便收藏与从主页跳进来。
        if path == "/stock":
            self.path = "/stock.html"
            return super().do_GET()

        return super().do_GET()

    def log_message(self, *args):
        pass  # 静默


# ---------------------------------------------------------------- 入口

def main():
    global HUB, STOCK_HUB
    parser = argparse.ArgumentParser(description="主页 + 实时新闻服务（常驻）")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--interval", type=int,
                        default=getattr(config, "NEWS_REFRESH_SECONDS", 300),
                        help="自动抓取新闻的间隔（秒，默认 300）")
    parser.add_argument("--once", action="store_true",
                        help="只抓一次并写入 output/live_news.json 后退出")
    parser.add_argument("--with-positions", action="store_true",
                        help="在同一进程里同时托管持仓管理服务（8765）—— 启动时只多一个窗口")
    args = parser.parse_args()

    if args.once:
        hub = NewsHub(interval=args.interval)
        snap = hub.refresh()
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        out = os.path.join(config.OUTPUT_DIR, "live_news.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(snap or {}, f, ensure_ascii=False, indent=1)
        print("已写入:", out)
        return 0 if snap else 1

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    HUB = NewsHub(interval=args.interval)
    HUB.start()

    # 个股后台刷新：分档抓被订阅的票（3.5s / 60s / 300s），非交易时段自动停。
    # 起不来也不影响主页与新闻 —— 少一个功能比整站起不来强。
    try:
        import stock as stock_mod
        STOCK_HUB = stock_mod.StockHub()
        STOCK_HUB.start()
    except Exception as e:
        STOCK_HUB = None
        print("[提示] 个股服务未启动：%s: %s" % (type(e).__name__, e))

    if not os.path.exists(os.path.join(BASE_DIR, "output", "index.html")):
        print("[提示] 还没生成主页，请先运行 python main.py")

    # 同一进程里顺带托管 8765（持仓管理）：启动只占一个窗口，关掉窗口两个服务一起停。
    # 端口被占（比如旧版服务还在跑）时只降级提示，不影响 8766 本身。
    pos_server, pos_port = None, 8765
    if args.with_positions:
        try:
            import position_manager
            pos_port = getattr(position_manager, "PORT", 8765)
            pos_server = position_manager.serve_in_thread()
        except OSError as e:
            print("[提示] 8765 持仓管理未随本进程启动（端口被占用？）：%s" % e)
        except Exception as e:
            print("[提示] 8765 持仓管理启动失败：%s: %s" % (type(e).__name__, e))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("=" * 58)
    print("  主页 + 实时新闻服务已启动（后台常驻）")
    print("  主页: http://127.0.0.1:%d/output/index.html" % args.port)
    print("  新闻: http://127.0.0.1:%d/api/news   每 %ds 自动刷新" % (args.port, HUB.interval))
    if STOCK_HUB is not None:
        print("  个股: http://127.0.0.1:%d/api/stock?code=600941  （3.5s/60s/300s 分档，非交易时段自动停）"
              % args.port)
    if pos_server is not None:
        print("  持仓: http://127.0.0.1:%d   （与本进程同体，随窗口一起退出）" % pos_port)
    print("  关闭: 关掉启动脚本窗口 / Ctrl+C")
    print("=" * 58)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        HUB.stop()
        if STOCK_HUB is not None:
            STOCK_HUB.stop()
        if pos_server is not None:
            pos_server.shutdown()
        server.server_close()
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
