# -*- coding: utf-8 -*-
"""只读自检：把 houfeng.html 渲染成截图，用于确认降级线区块是否真的显示。"""
from __future__ import annotations

import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "houfeng.html"
OUT = HERE / "_shot_houfeng.png"


def main() -> None:
    url = SRC.as_uri()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 900, "height": 1400}, device_scale_factor=1)
        pg.goto(url)
        pg.wait_for_timeout(1200)
        # 定位"生产池滚动超额"区块并单独截一张
        box = pg.query_selector("#rollingBox")
        txt = box.inner_text() if box else "(未找到 rollingBox)"
        print("=== rollingBox 文本 ===")
        print(txt)
        # 该区块在第二屏（s1），分屏结构默认隐藏；强制全部展开再截整页
        pg.evaluate("document.querySelectorAll('.scr').forEach(function(e){e.style.display='block';})")
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT), full_page=True)
        print("SHOT", OUT)
        b.close()


if __name__ == "__main__":
    main()
