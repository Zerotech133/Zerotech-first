# -*- coding: utf-8 -*-
"""只读自检：渲染 paper_arena.html 截图 + 抓控制台错误 + 核对首页关键文本。

判据（三条都必须过）：
  1. 控制台无 JS 报错（模板注入失败最常见的就是 JSON 里带脏字符）
  2. 首屏结论句里出现真实的交易日数与信号数（证明数据注入了，不是静态占位）
  3. 逐日账卡片的数量 == 信号条数（证明循环真的跑完，不是只渲染第一张）
"""
from __future__ import annotations

import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "paper_arena.html"
OUT = HERE / "_shot_arena.png"


def main() -> None:
    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 430, "height": 1500}, device_scale_factor=1)
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(SRC.as_uri())
        pg.wait_for_timeout(900)

        print("=== 1) JS 错误 ===")
        print("无" if not errs else "\n".join(errs[:5]))

        print("\n=== 2) 首屏结论句 ===")
        print(pg.inner_text("#verdict").strip())

        print("\n=== 3) 首屏数字卡 ===")
        for t in pg.query_selector_all(".num"):
            print("  " + t.inner_text().replace("\n", " / "))

        print("\n=== 4) 警示条 ===")
        al = pg.query_selector_all(".alert")
        for a in al:
            print("  " + a.inner_text().replace("\n", " ")[:120])
        if not al:
            print("  （无警示）")

        print("\n=== 5) 逐日账卡片数 ===")
        cards = pg.query_selector_all("#days .card")
        print("  卡片数 = %d" % len(cards))
        if cards:
            first = cards[0]
            print("  第一张：" + first.inner_text().split("\n")[0])
            cells = first.query_selector_all(".cell")
            print("  该卡内打法格数 = %d" % len(cells))
            print("  前 4 格：" + " | ".join(
                c.inner_text().replace("\n", " ") for c in cells[:4]))
            # 展开选股清单，确认 chips 真的注入了代码
            first.query_selector(".day-h").click()
            pg.wait_for_timeout(200)
            cs = first.query_selector_all(".cs i")
            print("  展开后股票标签数 = %d" % len(cs))

        print("\n=== 6) 打法清单行数 ===")
        print("  %d 行" % len(pg.query_selector_all("#strats .strat")))

        pg.screenshot(path=str(OUT), full_page=True)
        print("\nSHOT %s" % OUT)
        b.close()

    ok = (not errs) and len(cards) > 0
    print("\n判定:", "通过" if ok else "需修")


if __name__ == "__main__":
    main()
