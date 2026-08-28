# -*- coding: utf-8 -*-
"""
东方财富爬虫 - 登录模块

两种登录方式：
  1) qr_login()      : Playwright 弹出浏览器窗口，用「东方财富 App」扫码登录（推荐，安全）
  2) manual_login()  : 手动粘贴浏览器 Cookie（备用）

登录成功后，Cookie 保存到 cookies.json，供 collector 加载使用。
"""

import json
import os
import time

import config

# 东财独立登录页（点击官网"登录"后跳转到的地址）
LOGIN_URL = "https://passport2.eastmoney.com/pub/login?backurl=https%3A%2F%2Fwww.eastmoney.com%2F"

# 登录后页面会跳回的目标
BACK_URL = "https://www.eastmoney.com/"

# 标识"已登录"的关键 cookie 关键字
AUTH_KEYWORDS = ("auth_token", "em_token", "userinfo", "token", "sid")


def save_cookies(cookie_list):
    """把浏览器 Cookie 列表（[{name,value,domain,...}]）转成 {name:value} 字典保存。"""
    cj = {}
    for c in cookie_list:
        if c.get("name") and c.get("value") is not None:
            cj[c["name"]] = c["value"]
    with open(config.COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cj, f, ensure_ascii=False, indent=2)
    return cj


def _cookie_names(page):
    try:
        return {c["name"].lower() for c in page.context.cookies()}
    except Exception:
        return set()


def _detect_login(page, initial_names):
    """
    判断是否登录成功：
      - Cookie 集合相对登录前出现新增的鉴权类 cookie
      - 或页面已跳回 backurl
    """
    current = _cookie_names(page)
    new_cookies = current - initial_names
    if any(k in name for name in new_cookies for k in AUTH_KEYWORDS):
        return True
    try:
        if page.url.startswith(BACK_URL):
            return True
    except Exception:
        pass
    return False


def qr_login(timeout_seconds=180):
    """
    使用 Playwright 弹出浏览器，打开东财登录页，等待 App 扫码登录。
    返回：登录成功返回保存的 cookies 字典；失败返回 None。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        initial_names = _cookie_names(page)

        print("=" * 60)
        print("  已打开东方财富登录页（浏览器窗口）")
        print("  请用「东方财富 App」右上角 + 扫一扫，扫描页面上的二维码")
        print("  登录成功后本程序自动保存会话（最多等待 {} 秒）".format(timeout_seconds))
        print("=" * 60)

        deadline = time.time() + timeout_seconds
        last_url = ""
        while time.time() < deadline:
            if _detect_login(page, initial_names):
                cookies = page.context.cookies()
                cj = save_cookies(cookies)
                print("✓ 登录成功，会话已保存到", config.COOKIE_FILE)
                browser.close()
                return cj
            try:
                url = page.url
            except Exception:
                url = ""
            if url != last_url:
                print("  [页面]", url[:90])
                last_url = url
            time.sleep(2)

        print("✗ 等待超时，未检测到登录。可重试，或改用 manual_login() 手动粘贴 Cookie。")
        browser.close()
        return None


def manual_login():
    """
    手动粘贴 Cookie 方式：
    1. 浏览器登录东财后，按 F12 -> Network，复制任意请求的 Cookie 头
    2. 粘贴给本程序
    """
    print("请从已登录的浏览器中复制 Cookie（格式：a=1; b=2; ...）：")
    raw = input("Cookie > ").strip()
    cj = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cj[k.strip()] = v.strip()
    if not cj:
        print("未解析到任何 Cookie。")
        return None
    with open(config.COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cj, f, ensure_ascii=False, indent=2)
    print("✓ Cookie 已保存到", config.COOKIE_FILE)
    return cj


def ensure_login():
    """确保存在有效 Cookie：已有则直接使用，否则引导登录。"""
    if os.path.exists(config.COOKIE_FILE):
        try:
            with open(config.COOKIE_FILE, "r", encoding="utf-8") as f:
                cj = json.load(f)
            if cj:
                print("✓ 检测到已保存的登录会话，直接使用。")
                return cj
        except Exception:
            pass
    print("未找到有效登录会话，开始扫码登录 ...")
    return qr_login()


if __name__ == "__main__":
    ensure_login()
