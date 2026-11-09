# -*- coding: utf-8 -*-
"""只核对「三方向验证」页签：桌面 + 390px，控制台、溢出、列宽、必需区块。"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

OUT = "deliverables/shots_p4_20261108"
URL = "http://127.0.0.1:%s/chanlun_board.html"


def run(port, w, h, tag):
    os.makedirs(OUT, exist_ok=True)
    errs, res = [], {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        pg.on("console", lambda m: errs.append("%s: %s" % (m.type, m.text))
              if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errs.append("pageerror: %s" % e))
        pg.goto(URL % port, wait_until="networkidle")
        pg.wait_for_selector(".card", timeout=20000)
        pg.click('[data-view="p4"]')
        pg.wait_for_timeout(600)
        res["面板数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        res["判决块"] = pg.eval_on_selector_all(".p3-verdict", "n=>n.length")
        res["表格行"] = pg.eval_on_selector_all(".p4-row", "n=>n.length")
        res["KPI"] = pg.eval_on_selector_all(".ab-kpi", "n=>n.length")
        res["导航值"] = pg.inner_text("#nv-p4")
        res["横向溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        res["最宽元素"] = pg.evaluate(
            "Math.max.apply(null,[].map.call(document.querySelectorAll('*'),"
            "e=>e.scrollWidth||0))")
        # 首列不能被固定列挤成 0
        res["首列宽"] = pg.evaluate(
            "(function(){var r=document.querySelector('.p4-row .row__nm');"
            "return r?Math.round(r.getBoundingClientRect().width):-1})()")
        res["内联样式"] = pg.evaluate(
            "document.querySelectorAll('#app [style]').length")
        pg.screenshot(path="%s/%s.png" % (OUT, tag), full_page=True)
        b.close()
    return res, errs


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8080"
    for w, h, tag in ((1440, 1000, "p4_desktop"), (390, 844, "p4_mobile")):
        res, errs = run(port, w, h, tag)
        print("== %dpx" % w)
        for k, v in res.items():
            print("  %-8s %s" % (k, v))
        print("  console  %s" % (errs if errs else "0 条"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
