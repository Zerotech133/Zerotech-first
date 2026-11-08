# -*- coding: utf-8 -*-
"""只核对「口径修正」页签（G3）：桌面 + 390px，控制台、溢出、列宽、判定徽章。"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

OUT = "deliverables/shots_p8_20261108"
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
        pg.click('[data-view="p8"]')
        pg.wait_for_timeout(900)
        res["面板数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        res["判决块"] = pg.eval_on_selector_all(".p3-verdict", "n=>n.length")
        res["表格行"] = pg.eval_on_selector_all(".p8-row", "n=>n.length")
        res["判定徽章"] = pg.eval_on_selector_all(".p8-tag", "n=>n.length")
        res["失效徽章"] = pg.eval_on_selector_all(".p8-tag--dead", "n=>n.length")
        res["导航值"] = pg.inner_text("#nv-p8")
        res["含NaN"] = pg.evaluate(
            "(function(){var t=document.getElementById('app').innerText;"
            "return /NaN|undefined|Infinity|null/.test(t)})()")
        res["横向溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        res["最宽元素"] = pg.evaluate(
            "Math.max.apply(null,[].map.call(document.querySelectorAll('*'),"
            "e=>e.scrollWidth||0))")
        res["首列宽"] = pg.evaluate(
            "(function(){var r=document.querySelector('.p8-row .row__nm');"
            "return r?Math.round(r.getBoundingClientRect().width):-1})()")
        res["条宽非法"] = pg.evaluate(
            "(function(){var f=document.querySelectorAll('.p7-bar__track i'),k=0;"
            "for(var i=0;i<f.length;i++){var p=parseFloat(f[i].style.width);"
            "if(!(p>=0&&p<=100))k++}return k})()")
        res["条色数"] = pg.evaluate(
            "(function(){var f=document.querySelectorAll('.p7-bar__track i'),"
            "s={};for(var i=0;i<f.length;i++){s[getComputedStyle(f[i]).backgroundColor]=1}"
            "return Object.keys(s).length})()")
        res["行高异常"] = pg.evaluate(
            "(function(){var r=document.querySelectorAll('.p8-row'),k=0;"
            "for(var i=0;i<r.length;i++){if(r[i].getBoundingClientRect().height>120)k++}"
            "return k})()")
        res["文字竖排"] = pg.evaluate(
            "(function(){var r=document.querySelectorAll('.p8-row .row__nm'),k=0;"
            "for(var i=0;i<r.length;i++){var b=r[i].getBoundingClientRect();"
            "if(b.height>60)k++}return k})()")
        pg.screenshot(path="%s/%s.png" % (OUT, tag), full_page=True)
        b.close()
    return res, errs


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8124"
    ok = True
    for w, h, tag in ((1440, 1000, "p8_desktop"), (390, 844, "p8_mobile")):
        res, errs = run(port, w, h, tag)
        print("== %dpx" % w)
        for k, v in res.items():
            print("  %-10s %s" % (k, v))
        print("  console    %s" % (errs if errs else "0 条"))
        bad = []
        if errs:
            bad.append("console")
        if res["含NaN"]:
            bad.append("NaN")
        if res["横向溢出"]:
            bad.append("溢出")
        if res["面板数"] != 4:
            bad.append("面板数")
        if res["判定徽章"] < 12:
            bad.append("徽章少")
        if res["失效徽章"] < 3:
            bad.append("失效徽章少")
        if res["首列宽"] < 70:
            bad.append("首列塌")
        if res["条宽非法"]:
            bad.append("条宽")
        if res["条色数"] < 3:
            bad.append("条色未区分")
        if res["行高异常"]:
            bad.append("行高炸")
        if res["文字竖排"]:
            bad.append("竖排")
        print("  结论        %s" % ("通过" if not bad else "不通过 → " + ",".join(bad)))
        ok = ok and not bad
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
