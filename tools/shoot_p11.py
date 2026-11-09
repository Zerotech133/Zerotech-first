# -*- coding: utf-8 -*-
"""只核对「入场特征」页签（第二十轮）：桌面 1440 + 390px。

查什么：控制台报错、横向溢出、面板数、NaN、行高炸、文字竖排、可见列数、
覆盖率胶囊数、单变量表行数、holdout 行数、迁移散点圆点数。

先过语法闸：脚本块只要有一个引号错位整页就白屏，playwright 只会白等 20 秒。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(os.path.dirname(HERE), "portal", "chanlun_board.html")
OUT = "deliverables/shots_p11_20261108"
URL = "http://127.0.0.1:%s/chanlun_board.html"

PANELS = 5
CHIPS = 23          # 16 个新特征 + 7 个旧字段
SCREEN_ROWS = 20    # 单变量表：每个特征一行（有 t 的才会出现）
HOLD_ROWS = 6       # 表头 + 5 条候选
DOTS = 80           # 迁移散点：92 个桶，允许个别缺失


def syntax_gate():
    src = open(HTML, encoding="utf-8").read()
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", src, re.S)
    for i, b in enumerate(blocks):
        if not b.strip():
            continue
        fd, path = tempfile.mkstemp(suffix=".js")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(b)
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            os.unlink(path)
            return None
        os.unlink(path)
        if r.returncode != 0:
            m = re.search(r":(\d+)\s*\n", r.stderr)
            base = src[:src.find(b)].count("\n") + 1
            hint = " → html 第 %d 行" % (base + int(m.group(1))) if m else ""
            return "script#%d 语法错%s\n%s" % (i, hint, r.stderr.strip()[:800])
    return None


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
        pg.click('[data-view="p11"]')
        pg.wait_for_timeout(900)
        res["面板数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        res["判决块"] = pg.eval_on_selector_all(".p3-verdict", "n=>n.length")
        res["覆盖胶囊"] = pg.eval_on_selector_all(".p11-cv > div", "n=>n.length")
        res["单变量行"] = pg.eval_on_selector_all(".p11-row", "n=>n.length")
        res["hold行"] = pg.eval_on_selector_all(".p11-hold", "n=>n.length")
        res["散点"] = pg.eval_on_selector_all(".p11sc circle", "n=>n.length")
        res["导航值"] = pg.inner_text("#nv-p11")
        res["含NaN"] = pg.evaluate(
            "(function(){var t=document.getElementById('app').innerText;"
            "return /NaN|undefined|Infinity|null/.test(t)})()")
        res["横向溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        res["首列宽"] = pg.evaluate(
            "(function(){var r=document.querySelector('.p11-row .row__nm');"
            "return r?Math.round(r.getBoundingClientRect().width):-1})()")
        res["行高异常"] = pg.evaluate(
            "(function(){var r=document.querySelectorAll('.p11-row,.p11-hold'),k=0;"
            "for(var i=0;i<r.length;i++){if(r[i].getBoundingClientRect().height>120)k++}"
            "return k})()")
        res["文字竖排"] = pg.evaluate(
            "(function(){var r=document.querySelectorAll('.p11-row .row__nm'),k=0;"
            "for(var i=0;i<r.length;i++){var b=r[i].getBoundingClientRect();"
            "if(b.height>64)k++}return k})()")
        res["可见列数"] = pg.evaluate(
            "(function(){var r=document.querySelector('.p11-row:not(.row--head)');"
            "if(!r)return -1;var k=0;"
            "for(var i=0;i<r.children.length;i++){"
            "if(r.children[i].tagName==='SPAN'&&r.children[i].offsetParent)k++}return k})()")
        res["散点越界"] = pg.evaluate(
            "(function(){var s=document.querySelector('.p11sc');if(!s)return -1;"
            "var b=s.getBoundingClientRect(),k=0,c=s.querySelectorAll('circle');"
            "for(var i=0;i<c.length;i++){var q=c[i].getBoundingClientRect();"
            "if(q.left<b.left-1||q.right>b.right+1||q.top<b.top-1||q.bottom>b.bottom+1)k++}"
            "return k})()")
        pg.screenshot(path="%s/%s.png" % (OUT, tag), full_page=True)
        b.close()
    return res, errs


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8131"
    err = syntax_gate()
    if err:
        print("语法闸未过，先修这个（白屏时 playwright 只会报超时）：")
        print(err)
        return 2
    ok = True
    for w, h, tag in ((1440, 1000, "p11_desktop"), (390, 844, "p11_mobile")):
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
        if res["面板数"] != PANELS:
            bad.append("面板数%d" % res["面板数"])
        if res["判决块"] < 3:
            bad.append("判决块%d" % res["判决块"])
        if res["覆盖胶囊"] != CHIPS:
            bad.append("胶囊%d" % res["覆盖胶囊"])
        if res["单变量行"] < SCREEN_ROWS:
            bad.append("单变量行%d" % res["单变量行"])
        if res["hold行"] != HOLD_ROWS:
            bad.append("hold行%d" % res["hold行"])
        if res["散点"] < DOTS:
            bad.append("散点%d" % res["散点"])
        if res["首列宽"] < 60:
            bad.append("首列塌")
        if res["行高异常"]:
            bad.append("行高炸")
        if res["文字竖排"]:
            bad.append("竖排")
        if res["散点越界"]:
            bad.append("散点越界")
        want_col = 6 if w > 600 else 3
        if res["可见列数"] != want_col:
            bad.append("可见列%d" % res["可见列数"])
        print("  → %s" % ("通过" if not bad else "不通过：" + "、".join(bad)))
        if bad:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
