# -*- coding: utf-8 -*-
"""静态前端资源加载器
========================
把原先内联在 Python 模块里的 CSS / JS / HTML 片段外置成 static/ 下的真实文件：
改版（配色、布局、图表样式）时只动前端文件，不必再碰 Python 代码。

约定：
  - static/ 下的文件保存资源的**原文**（不含 Python 侧的三引号与其首尾换行）；
  - 需要与重构前产物逐字节一致的调用方，用显式拼接把边界留在 Python 侧，例如：
        HOME_CSS = "\\n<style>\\n" + load("home.css") + "</style>\\n"
  - 读不到文件时抛 RuntimeError 并给出明确路径 —— 不静默返回空串，
    否则页面会「静默坏掉」（样式全丢却看不出原因）。

注：读取用默认文本模式（universal newlines），因此文件是 LF 还是 CRLF 都会被
归一成 "\\n"，与 Python 三引号常量里的换行保持一致。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

_cache = {}


def path(name):
    """资源文件绝对路径（name 形如 "home.css" / "events_page.js"）。"""
    return os.path.join(STATIC_DIR, name)


def load(name, use_cache=True):
    """读取 static/<name> 的文本内容（UTF-8）。"""
    if use_cache and name in _cache:
        return _cache[name]
    p = path(name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise RuntimeError(
            f"静态资源缺失或不可读: {p}（{e}）。请确认 static/ 目录随项目一起部署。"
        ) from e
    if use_cache:
        _cache[name] = text
    return text


def exists(name):
    """资源文件是否存在（供启动自检）。"""
    return os.path.isfile(path(name))
