# -*- coding: utf-8 -*-
"""pool_pc2 交互验收：控制台无错 + 三级链路（卡片→列表→详情）走通 + 截图。"""
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "file:///" + os.path.join(HERE, "reports", "pool_pc2.html").replace("\\", "/")
OUT = os.path.join(HERE, "reports", "ui_audit")
os.makedirs(OUT, exist_ok=True)

errs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 950})
    pg = ctx.new_page()
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="load", timeout=30000)
    pg.wait_for_timeout(1000)
    pg.screenshot(path=os.path.join(OUT, "pool_pc2_new_cards.png"))
    # 点第一张档卡 h3 → 列表层
    pg.locator(".tcard h3").first.click()
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(OUT, "pool_pc2_new_list.png"))
    # 点第一行 → 详情抽屉
    pg.locator("#lvTable tr.r").first.click()
    pg.wait_for_timeout(600)
    pg.screenshot(path=os.path.join(OUT, "pool_pc2_new_detail.png"))
    b.close()

if errs:
    print("[FAIL] console/pageerror %d 条:" % len(errs))
    for e in errs[:10]:
        print("  ", e)
    raise SystemExit(1)
print("[ok] 三级链路走通，无 console 错误；截图在", OUT)
