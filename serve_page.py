# -*- coding: utf-8 -*-
"""主页静态服务(常驻)
================================================
把项目根目录当作静态站点发布,主页通过 http 访问:

    http://127.0.0.1:8766/output/index.html

为什么不用 file:// 直接双击?浏览器的 file:// 安全策略会拦截页面发起的本地图片/纹理
读取(表现为:国界、光点在,地球本体是黑球、只剩大气一圈亮边)。走 http 就没有这些限制,
地球的 8K 日间/夜景/云/高程/水面贴图都能正常加载。

用法:
    python serve_page.py        # 前台运行(可 Ctrl+C 关闭)
main.py 会自动把它后台拉起(端口已被占用则跳过),与持仓管理服务(8765)同一套路。
"""
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8766
STATIC_EXT = (".jpg", ".jpeg", ".png", ".js", ".css", ".geojson")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def end_headers(self):
        # 大贴图允许缓存(避免每次刷新都重新读 20MB),页面本身不缓存(便于刷新看最新数据)
        if self.path.lower().endswith(STATIC_EXT):
            self.send_header("Cache-Control", "max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass  # 静默


def main():
    if not os.path.exists(os.path.join(BASE_DIR, "output", "index.html")):
        print("[提示] 还没生成主页,请先运行 python main.py")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 56)
    print("  主页静态服务已启动(常驻)")
    print(f"  主页地址: http://127.0.0.1:{PORT}/output/index.html")
    print("  关闭: 按 Ctrl+C")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.server_close()


if __name__ == "__main__":
    main()
