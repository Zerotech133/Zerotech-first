# -*- coding: utf-8 -*-
"""回归守卫：/sa 页面不得在 _html() 完整响应后再写 wfile（K-ORG-20261026-SAWRITE）。

背景（2026-10-26 发布后巡检）：_sa_page 在 self._html(body) 之后又多写了一次
self.wfile.write(body)。body 是 str，而 wfile 需要 bytes → TypeError；
此时响应头与 Content-Length 已发出，客户端仍收到 200，但服务端每访问一次 /sa
就留一条 Traceback（线上 8 处 Traceback 里的一条隐性噪声）。

本测试为源码级静态断言，不依赖网络/服务/数据库，毫秒级；防"双重写"回归。
"""
import os
import re

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_backtest.py")


def _source():
    with open(SRC, encoding="utf-8") as f:
        return f.read()


def test_no_html_then_wfile_double_write():
    """全文件禁止 self._html(...) 后紧跟 self.wfile.write(...)（响应已发完）。"""
    src = _source()
    hit = re.search(
        r"self\._html\([^)\n]*\)[ \t]*\r?\n\s*self\.wfile\.write\(", src)
    assert hit is None, (
        "发现双重写：self._html() 之后又 self.wfile.write()（响应已结束，"
        "str 写 bytes 流会 TypeError）。删除多余的那一行。")


def test_sa_page_still_sends_via_html():
    """/sa 页面仍必须通过 self._html(body) 发送（防止误删整段）。"""
    src = _source()
    anchor = src.find('pill_nav("sa")')
    assert anchor >= 0, "未找到 /sa 页面模板锚点 pill_nav(\"sa\")"
    window = src[anchor:anchor + 500]
    assert "self._html(body)" in window, "/sa 页面模板之后未见 self._html(body)"
