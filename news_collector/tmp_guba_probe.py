# -*- coding: utf-8 -*-
"""股吧渲染页帖子结构定位（2026-11-09 临时探针 v2）。"""
import re
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=False, args=["--window-position=3000,3000"])
    pg = b.new_page()
    pg.goto("https://guba.eastmoney.com/list,600930.html",
            wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_timeout(12000)
    html = pg.content()
    print("len:", len(html))
    print("article_list 标记:", html.count("article_list"))
    m = re.search(r'var article_list\s*=\s*(\{.{0,200})', html, re.S)
    print("内嵌 JSON 头:", m.group(1)[:150] if m else "无")
    # DOM 特征
    for sel in ["[data-articleid]", ".listitem", "article", "[class*=listitem]",
                "[class*=note]", ".news_list", "[class*=reply]"]:
        try:
            n = len(pg.query_selector_all(sel))
            if n:
                print("命中选择器:", sel, n)
        except Exception:
            pass
    b.close()
