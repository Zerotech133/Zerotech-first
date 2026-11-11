# -*- coding: utf-8 -*-
"""UI 审计截图：把平台关键页面用无头浏览器截成 PNG，供设计评审。"""
import os
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = [
    ("index", os.path.join(HERE, "h5_daily", "index.html")),
    ("pool_pc2", os.path.join(HERE, "reports", "pool_pc2.html")),
    ("pc", os.path.join(HERE, "h5_daily", "pc.html")),
    ("pool_report_h5", os.path.join(HERE, "h5_daily", "pool_report_h5.html")),
    ("sector_platform", os.path.join(HERE, "h5_daily", "sector_platform.html")),
    ("chanlun_tool", os.path.join(HERE, "h5_daily", "chanlun_tool.html")),
    ("bt_hub", os.path.join(HERE, "h5_daily", "bt_hub.html")),
    ("sim_accounts", os.path.join(HERE, "h5_daily", "sim_accounts.html")),
    ("premarket", os.path.join(HERE, "h5_daily", "premarket.html")),
    ("positions", os.path.join(HERE, "h5_daily", "positions.html")),
]
OUT = os.path.join(HERE, "reports", "ui_audit")
os.makedirs(OUT, exist_ok=True)

shots = sys.argv[1:] or None
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 950},
                        device_scale_factor=1)
    pg = ctx.new_page()
    for name, path in PAGES:
        if shots and name not in shots:
            continue
        if not os.path.exists(path):
            print("[skip] %s 不存在" % name)
            continue
        url = "file:///" + path.replace("\\", "/")
        try:
            pg.goto(url, wait_until="load", timeout=30000)
            pg.wait_for_timeout(1200)
            pg.screenshot(path=os.path.join(OUT, name + ".png"))
            print("[ok] %s.png" % name)
        except Exception as e:  # noqa: BLE001
            print("[fail] %s: %s" % (name, e))
    b.close()
print("输出目录:", OUT)
