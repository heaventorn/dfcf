# -*- coding: utf-8 -*-
"""临时离线验证脚本(scratch,用完即删):P1 健壮性改造的行为验证。

全部用 mock 替换网络层,不产生真实请求。
"""
import json
import os
import sys

ROOT = r"c:\Users\Admin\Desktop\dfcf-main\dfcf-main"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config  # noqa: E402
import sources  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   << " + str(detail)))
    if not cond:
        FAILS.append(name)


class FakeResp:
    def __init__(self, text, status=200, ct="application/json"):
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": ct}

    def json(self):
        return json.loads(self.text)


def patch_get(resp):
    sources._get = lambda sess, url, params=None, timeout=None: resp


print("== [5] 风控判定收窄 ==")
patch_get(FakeResp('{"t": "please verify your account frequency"}'))
try:
    d = sources._req_json(None, "http://x")
    check("正常JSON含verify/freq不误判风控", isinstance(d, dict))
except sources.DataAnomaly as e:
    check("正常JSON含verify/freq不误判风控", False, e)

patch_get(FakeResp("<html><body>verify</body></html>", ct="text/html"))
try:
    sources._req_text(None, "http://x")
    check("HTML含verify判风控", False, "no-raise")
except sources.DataAnomaly as e:
    check("HTML含verify判风控", "风控" in str(e), e)

patch_get(FakeResp("访问过于频繁", ct="text/plain"))
try:
    sources._req_text(None, "http://x")
    check("纯文本中文强特征判风控", False, "no-raise")
except sources.DataAnomaly as e:
    check("纯文本中文强特征判风控", "风控" in str(e), e)

patch_get(FakeResp("some text mentioning verify token", ct="text/plain"))
try:
    t = sources._req_text(None, "http://x")
    check("纯文本英文弱特征不误判风控", t.startswith("some text"), t[:20])
except sources.DataAnomaly as e:
    check("纯文本英文弱特征不误判风控", False, e)

patch_get(FakeResp("not a json", ct="text/plain"))
try:
    sources._req_json(None, "http://x")
    check("JSON解析失败如实报错", False, "no-raise")
except sources.DataAnomaly as e:
    check("JSON解析失败如实报错", "JSON 解析失败" in str(e), e)

print("== [4] fetch 异常白名单 ==")
config.SOURCE_SWITCH_DELAY = 0.0
config.SOURCE_ALLFAIL_DELAY = 0.0


def _boom():
    raise KeyError("missing_field")


sources._sources.clear()
sources._internal_defects.clear()
name, data = sources.fetch("t", [("s1", _boom), ("s2", lambda: 42)])
check("代码缺陷时仍切到下一来源", name == "s2" and data == 42, (name, data))
check("代码缺陷计入_internal_defects", len(sources._internal_defects) == 1,
      sources._internal_defects)
check("代码缺陷不计入来源冷却", sources._src("s1").cooldown_until == 0.0,
      sources._src("s1").cooldown_until)
check("代码缺陷计入失败计数", sources._src("s1").fail == 1, sources._src("s1").fail)
check("health_report暴露代码缺陷", "代码缺陷" in sources.health_report())

sources._sources.clear()
sources._internal_defects.clear()


def _soft():
    raise sources._empty("结构校验未通过")


name, data = sources.fetch("t", [("s1", _soft), ("s2", lambda: 7)])
check("软异常切源成功", name == "s2" and data == 7, (name, data))
check("软异常不冷却", sources._src("s1").cooldown_until == 0.0)
check("软异常不计入缺陷", not sources._internal_defects)

sources._sources.clear()


def _hard():
    raise sources.DataAnomaly("HTTP 500")


name, data = sources.fetch("t", [("s1", _hard), ("s2", lambda: 8)], rounds=1)
check("硬异常冷却生效", sources._src("s1").cooldown_until > 0, sources._src("s1").cooldown_until)
check("硬异常在health_report显示冷却中", "冷却中" in sources.health_report(),
      sources.health_report())

sources._sources.clear()
name, data = sources.fetch("t", [("s1", lambda: []), ("s2", lambda: [1, 2])],
                           validate=lambda d: len(d) >= 1)
check("validate不通过时切源", name == "s2" and data == [1, 2], (name, data))

sources._sources.clear()
name, data = sources.fetch("t", [("s1", _boom)], rounds=1)
check("全部失败返回(None,None)", name is None and data is None, (name, data))

print("== [6] 连接重试与代理 ==")
s = sources._mk_session()
ad = s.adapters["https://"]
check("HTTPAdapter挂了Retry", isinstance(ad.max_retries, Retry), type(ad.max_retries))
check("Retry.total==HTTP_RETRIES", ad.max_retries.total == config.HTTP_RETRIES,
      ad.max_retries.total)
check("连接池大小生效", ad._pool_connections == config.HTTP_POOL_SIZE, ad._pool_connections)

os.environ["ALL_PROXY"] = "http://127.0.0.1:9/"
s2 = sources._mk_session()
check("ALL_PROXY显式生效", s2.proxies.get("https") == "http://127.0.0.1:9/", s2.proxies)
del os.environ["ALL_PROXY"]

_old = config.USE_ENV_PROXY
config.USE_ENV_PROXY = False
s3 = sources._mk_session()
check("USE_ENV_PROXY=False强制直连", s3.trust_env is False, s3.trust_env)
config.USE_ENV_PROXY = _old

print("== [7] config 重试次数统一 ==")
import collector  # noqa: E402

check("config无RETRIES死配置", not hasattr(config, "RETRIES"))
check("config.COLLECT_RETRIES==3", config.COLLECT_RETRIES == 3, config.COLLECT_RETRIES)
calls = []


def _f():
    calls.append(1)
    return []


collector._retry_on_empty(_f)
check("collector重试次数==COLLECT_RETRIES", len(calls) == config.COLLECT_RETRIES, len(calls))
calls2 = []
collector._retry_on_empty(_f, retries=1)
check("显式retries覆盖config", len(calls2) == 1, len(calls2))

print()
if FAILS:
    print("!! FAILED %d:" % len(FAILS), FAILS)
    sys.exit(1)
print("ALL PASS")
