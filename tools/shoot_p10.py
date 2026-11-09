# -*- coding: utf-8 -*-
"""只核对「出场规则」页签（第十九轮）：桌面 1440 + 390px。

查什么：控制台报错、横向溢出、面板数、NaN、行高炸、文字竖排、可见列数、
峰值日条形的宽度与条数。

**先过语法闸**：脚本块只要有一个引号错位，整页就是白屏，playwright 只会报
"等 .card 超时"，白等 20 秒还不知道错在哪。先用 node --check 定位到行。
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
OUT = "deliverables/shots_p10_20261108"
URL = "http://127.0.0.1:%s/chanlun_board.html"


def syntax_gate():
    """把内联脚本抽出来交给 node --check。返回错误文本，通过返回 None。"""
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
            return None                      # 没装 node 就跳过，不阻塞
        os.unlink(path)
        if r.returncode != 0:
            # node 报的是临时文件的行号，换算回 html 里的行号更好用
            m = re.search(r":(\d+)\s*\n", r.stderr)
            base = src[:src.find(b)].count("\n") + 1
            hint = ""
            if m:
                hint = " → html 第 %d 行" % (base + int(m.group(1)))
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
        pg.click('[data-view="p10"]')
        pg.wait_for_timeout(900)
        res["面板数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        res["判决块"] = pg.eval_on_selector_all(".p3-verdict", "n=>n.length")
        res["规则行"] = pg.eval_on_selector_all(".p10-row", "n=>n.length")
        res["条件格"] = pg.eval_on_selector_all(".p10-hd", "n=>n.length")
        res["条形数"] = pg.eval_on_selector_all(".p10-ph .bar", "n=>n.length")
        res["导航值"] = pg.inner_text("#nv-p10")
        res["含NaN"] = pg.evaluate(
            "(function(){var t=document.getElementById('app').innerText;"
            "return /NaN|undefined|Infinity|null/.test(t)})()")
        res["横向溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        res["首列宽"] = pg.evaluate(
            "(function(){var r=document.querySelector('.p10-row .row__nm');"
            "return r?Math.round(r.getBoundingClientRect().width):-1})()")
        res["行高异常"] = pg.evaluate(
            "(function(){var r=document.querySelectorAll('.p10-row'),k=0;"
            "for(var i=0;i<r.length;i++){if(r[i].getBoundingClientRect().height>120)k++}"
            "return k})()")
        res["文字竖排"] = pg.evaluate(
            "(function(){var r=document.querySelectorAll('.p10-row .row__nm'),k=0;"
            "for(var i=0;i<r.length;i++){var b=r[i].getBoundingClientRect();"
            "if(b.height>64)k++}return k})()")
        res["条宽非法"] = pg.evaluate(
            "(function(){var f=document.querySelectorAll('.p10-ph .bar'),k=0;"
            "for(var i=0;i<f.length;i++){var q=parseFloat(f[i].style.width);"
            "if(!(q>=0&&q<=100))k++}return k})()")
        res["可见列数"] = pg.evaluate(
            "(function(){var r=document.querySelector('.p10-row:not(.row--head)');"
            "if(!r)return -1;var s=r.querySelectorAll('span'),k=0;"
            "for(var i=0;i<s.length;i++){if(s[i].offsetParent)k++}return k})()")
        res["section数"] = pg.eval_on_selector_all(".ui-panel h2", "n=>n.length")
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
    for w, h, tag in ((1440, 1000, "p10_desktop"), (390, 844, "p10_mobile")):
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
        if res["面板数"] != 5:
            bad.append("面板数%d" % res["面板数"])
        if res["判决块"] < 4:
            bad.append("判决块少")
        if res["规则行"] < 30:
            bad.append("规则行少")
        if res["条件格"] != 3:
            bad.append("条件格%d" % res["条件格"])
        if res["条形数"] < 9:
            bad.append("条形数%d" % res["条形数"])
        if res["首列宽"] < 60:
            bad.append("首列塌")
        if res["条宽非法"]:
            bad.append("条宽")
        if res["行高异常"]:
            bad.append("行高炸")
        if res["文字竖排"]:
            bad.append("竖排")
        want_cols = 6 if w > 600 else 3
        if res["可见列数"] != want_cols:
            bad.append("可见列%d≠%d" % (res["可见列数"], want_cols))
        print("  结论        %s" % ("通过" if not bad else "不通过 → " + ",".join(bad)))
        ok = ok and not bad
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
