# -*- coding: utf-8 -*-
"""双密码验证：手动输入访问密码 + 自动加载本地 pwd.key 的强密码。
校验 Argon2("手动密码 + 文件强密码") 是否匹配内置哈希（Argon2id, 16MB 低内存档）。
pwd.key 仅存本地、不上传仓库；内置哈希为 Argon2 自含盐哈希，无明文。返回 0=通过 1=未通过。"""
import os
import sys
import tkinter as tk

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    _ARGON2_OK = True
except Exception:
    _ARGON2_OK = False

# Argon2id 组合哈希 = Argon2("0762" + 强密码)，程序外一次性生成（16MB 内存档）
ARGON2_HASH = "$argon2id$v=19$m=16384,t=2,p=1$e271hJRDAT/51KFNNb+YrQ$XYTVkcdfu8Ypeq4BFKC6hsQPpR3BsuX3EJXQs5aOd/I"
PH = PasswordHasher(time_cost=2, memory_cost=16384, parallelism=1)


def _load_key():
    """读取本地 pwd.key 中的强密码明文（该文件不上传仓库）"""
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base, "pwd.key"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _verify(pwd):
    """Argon2 校验；密码不匹配返回 False"""
    if not _ARGON2_OK:
        return False
    try:
        PH.verify(ARGON2_HASH, pwd)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def main():
    if not _ARGON2_OK:
        print("错误：缺少 argon2-cffi 库。请先执行: pip install argon2-cffi")
        return 1

    key = _load_key()
    if not key:
        print("错误：找不到本地密钥文件 pwd.key，程序拒绝启动。")
        return 1

    root = tk.Tk()
    root.title("身份验证")
    root.geometry("320x150")
    root.resizable(False, False)
    root.eval("tk::PlaceWindow . center")

    tk.Label(root, text="请输入访问密码", font=("微软雅黑", 11)).pack(pady=(22, 6))
    entry = tk.Entry(root, show="*", font=("微软雅黑", 13), width=18, justify="center")
    entry.pack(pady=4)
    entry.focus_set()

    state = {"ok": False}
    err_label = None

    def on_ok():
        nonlocal err_label
        if _verify(entry.get() + key):
            state["ok"] = True
            root.destroy()
        else:
            if err_label is None:
                err_label = tk.Label(root, text="密码错误", fg="red", font=("微软雅黑", 9))
                err_label.pack()

    def on_cancel():
        root.destroy()

    bf = tk.Frame(root)
    bf.pack(pady=12)
    tk.Button(bf, text="确定", width=8, command=on_ok, default="active").pack(side="left", padx=10)
    tk.Button(bf, text="取消", width=8, command=on_cancel).pack(side="left", padx=10)
    entry.bind("<Return>", lambda e: on_ok())
    entry.bind("<Escape>", lambda e: on_cancel())
    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
