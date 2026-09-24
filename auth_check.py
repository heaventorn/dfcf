# -*- coding: utf-8 -*-
"""启动身份验证：解锁本地保险库，然后启动主程序。

校验规则（v3.3 起）：
    密码 + data/pwd.key  --Argon2id-->  KEK  --解开 data/keyring.json-->  数据密钥

数据密钥只通过环境变量交给子进程，不落盘；程序退出就没了。
第一次运行（还没有 keyring.json）时会顺手把现存明文隐私文件加密成 *.enc。

用法：
    python auth_check.py                # 校验通过后自动启动 main.py
    python auth_check.py --check-only   # 只校验密码，不启动程序
    python auth_check.py --run strategy.py

返回 0=通过 1=未通过。
"""

import argparse
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import secure_store


def main(argv=None):
    ap = argparse.ArgumentParser(description="DFCF 启动身份验证")
    ap.add_argument("--check-only", action="store_true", help="只校验密码，不启动程序")
    ap.add_argument("--run", default="main.py", help="校验通过后启动的脚本")
    ap.add_argument("rest", nargs="*", help="传给那个脚本的参数")
    args = ap.parse_args(argv)

    if not secure_store.deps_ok():
        print(secure_store.deps_hint())
        return 1

    key = secure_store.unlock_interactive()
    if key is None:
        print("[!] 身份验证未通过，程序退出。")
        return 1

    if args.check_only:
        print("[OK] 身份验证通过，保险库已解锁。")
        return 0

    target = os.path.join(BASE_DIR, args.run)
    if not os.path.isfile(target):
        print("[!] 找不到要启动的脚本：", target)
        return 1

    env = os.environ.copy()
    env[secure_store.ENV_KEY] = secure_store.export_key(key)
    cmd = [sys.executable, target] + list(args.rest)
    print("[OK] 身份验证通过，启动 %s ..." % args.run)
    try:
        return subprocess.run(cmd, env=env).returncode
    except KeyboardInterrupt:
        return 1
    except OSError as e:
        print("[!] 启动失败：%s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
