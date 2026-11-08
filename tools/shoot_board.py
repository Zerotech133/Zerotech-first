# -*- coding: utf-8 -*-
"""对 chanlun_board.html 做渲染核对：控制台报错、分组点击、详情弹层、四本账、截图。

页面是「卡片分组 → 列表 → 详情」加四个视图（池子 / 模拟仓 / 候风盘 / 口径），
所以核对要真的点：卡片、个股、分栏、候风盘的止损止盈列与净值曲线。

用法:
    python tools/shoot_board.py [port]     # 默认 8080（ai-portal 容器）
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

OUT = "deliverables/shots_board_20261108"
URL = "http://127.0.0.1:%s/chanlun_board.html"


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8080"
    os.makedirs(OUT, exist_ok=True)
    errs = []
    res = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        pg.on("console", lambda m: errs.append("%s: %s" % (m.type, m.text))
              if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errs.append("pageerror: %s" % e))
        pg.goto(URL % port, wait_until="networkidle")
        pg.wait_for_selector(".card", timeout=20000)

        res["势档"] = pg.inner_text(".gate__state b")
        res["KPI"] = pg.eval_on_selector_all(".ui-kpi", "n=>n.length")
        res["快捷卡"] = pg.eval_on_selector_all("[data-quick]", "n=>n.length")
        res["分组卡"] = pg.eval_on_selector_all("[data-grp]", "n=>n.length")
        res["横向溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        pg.screenshot(path=OUT + "/01_pool_cards.png", full_page=False)

        # 按板块切换 → 点分组 → 点个股
        pg.click('[data-dim="ind"]')
        pg.wait_for_timeout(250)
        res["板块组数"] = pg.eval_on_selector_all("[data-grp]", "n=>n.length")
        pg.click("[data-grp]")
        pg.wait_for_selector("[data-code]", timeout=10000)
        res["列表行数"] = pg.eval_on_selector_all("[data-code]", "n=>n.length")
        pg.click("[data-code]")
        pg.wait_for_selector("dialog.ui-drawer", timeout=10000)
        res["详情字段数"] = pg.eval_on_selector_all("dialog.ui-drawer .kv dt", "n=>n.length")
        res["dialog 已打开"] = pg.evaluate(
            "!!document.querySelector('dialog.ui-drawer')?.open")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        res["Esc 关闭"] = pg.evaluate("!document.querySelector('dialog.ui-drawer')")

        # 模拟仓（产线台账）
        pg.click('[data-view="book"]')
        pg.wait_for_selector("[data-btab]", timeout=10000)
        res["账本分栏"] = pg.eval_on_selector_all("[data-btab]", "n=>n.length")
        res["台账持仓行数"] = pg.eval_on_selector_all("[data-code]", "n=>n.length")
        pg.screenshot(path=OUT + "/05_book.png")

        # ── 候风盘 ───────────────────────────────────────────────
        pg.click('[data-view="hf"]')
        pg.wait_for_selector("[data-hftab]", timeout=15000)
        pg.wait_for_timeout(300)
        res["候风KPI"] = pg.eval_on_selector_all(".ui-kpi", "n=>n.length")
        res["净值曲线点数"] = pg.evaluate(
            "(()=>{var l=document.querySelector('.hfchart polyline');"
            "return l?l.getAttribute('points').trim().split(/\\s+/).length:0})()")
        res["持仓行数"] = pg.eval_on_selector_all("[data-hfcode]", "n=>n.length")
        res["止损列样例"] = pg.eval_on_selector_all(
            "[data-hfcode] .sl", "n=>n.slice(0,3).map(x=>x.textContent.replace(/\\s+/g,''))")
        res["止盈列样例"] = pg.eval_on_selector_all(
            "[data-hfcode] .tp", "n=>n.slice(0,3).map(x=>x.textContent.replace(/\\s+/g,''))")
        res["止损列空值"] = pg.evaluate(
            "Array.from(document.querySelectorAll('[data-hfcode] .sl'))"
            ".filter(x=>/^[—-]+$/.test(x.textContent.trim())).length")
        res["净值KPI文本"] = pg.inner_text(".ui-kpi b")
        pg.screenshot(path=OUT + "/08_hf_positions.png", full_page=False)

        # 点一只持仓 → 候风详情（含逐日轨迹）
        if pg.eval_on_selector_all("[data-hfcode]", "n=>n.length"):
            pg.click("[data-hfcode]")
            pg.wait_for_selector("dialog.ui-drawer", timeout=10000)
            res["候风详情字段"] = pg.eval_on_selector_all("dialog.ui-drawer .kv dt", "n=>n.length")
            res["轨迹节点数"] = pg.eval_on_selector_all(
                "dialog.ui-drawer .chain li", "n=>n.length")
            res["详情形含止损"] = "止损位" in pg.inner_text("dialog.ui-drawer")
            pg.screenshot(path=OUT + "/09_hf_detail.png")
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(150)

        # 当日动作
        pg.click('[data-hftab="today"]')
        pg.wait_for_timeout(400)
        pg.evaluate("document.querySelector('.hfbar--today')"
                    ".scrollIntoView({block:'center'})")
        pg.wait_for_timeout(200)
        res["当日动作块"] = pg.inner_text(".hfbar--today").replace("\n", " ")
        pg.screenshot(path=OUT + "/10_hf_today.png")

        # 逐笔（1MB 按需加载）
        pg.click('[data-hftab="trades"]')
        pg.wait_for_selector("[data-hftrade]", timeout=30000)
        pg.wait_for_timeout(300)
        res["逐笔行数"] = pg.eval_on_selector_all("[data-hftrade]", "n=>n.length")
        res["逐笔首行"] = pg.inner_text("[data-hftrade] .row__nm").split("\n")[0]
        pg.screenshot(path=OUT + "/11_hf_trades.png")

        # 买点对照
        pg.click('[data-view="ab"]')
        pg.wait_for_selector(".ab-row[data-absel]", timeout=20000)
        pg.wait_for_timeout(400)
        res["对照组合数"] = pg.eval_on_selector_all("[data-absel]", "n=>n.length")
        res["对照曲线数"] = pg.eval_on_selector_all(".abchart polyline", "n=>n.length")
        res["对照选中"] = pg.eval_on_selector(".ab-row.is-on .row__nm b", "n=>n.textContent")
        res["对照条形"] = pg.eval_on_selector_all(".ab-bar__track i",
                                                   "n=>n.map(x=>x.style.width)")
        pg.screenshot(path=OUT + "/13_ab.png", full_page=False)
        # 点另一行，确认能切换（曲线应变，选中断言）
        pg.click('[data-absel="all"]')
        pg.wait_for_timeout(350)
        res["切换后选中"] = pg.eval_on_selector(".ab-row.is-on .row__nm b", "n=>n.textContent")
        res["切换后溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")

        # 升级验证
        pg.click('[data-view="up"]')
        pg.wait_for_selector(".up-row", timeout=20000)
        pg.wait_for_timeout(400)
        res["升级面板数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        res["结构位行数"] = pg.eval_on_selector_all(".up-row .row__nm", "n=>n.length")
        res["被挡直方"] = pg.eval_on_selector_all(".up-hist__bar i",
                                                   "n=>n.map(x=>x.style.width)")
        res["升级首卡"] = pg.inner_text(".ab-kpis .ab-kpi").replace("\n", " ")[:60]
        pg.screenshot(path=OUT + "/14_up_sl.png", full_page=False)
        pg.evaluate("document.querySelectorAll('.ui-panel')[2]"
                    ".scrollIntoView({block:'start'})")
        pg.wait_for_timeout(250)
        pg.screenshot(path=OUT + "/15_up_tag.png", full_page=False)
        res["升级溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")

        # 三件事验证
        pg.click('[data-view="p3"]')
        pg.wait_for_selector(".p3-row", timeout=20000)
        pg.wait_for_timeout(400)
        res["三件事面板数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        res["冷却档数"] = pg.eval_on_selector_all(".p3-row .row__nm", "n=>n.length")
        res["判决块数"] = pg.eval_on_selector_all(".p3-verdict", "n=>n.length")
        res["首判决"] = pg.inner_text(".p3-verdict").replace("\n", " ")[:70]
        res["交叉表有3买"] = "3买" in pg.inner_text(".ui-panel:nth-of-type(2)")
        res["三件事溢出"] = pg.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        pg.screenshot(path=OUT + "/16_p3_lock.png", full_page=False)
        pg.evaluate("document.querySelectorAll('.ui-panel')[1]"
                    ".scrollIntoView({block:'start'})")
        pg.wait_for_timeout(250)
        pg.screenshot(path=OUT + "/17_p3_rank.png", full_page=False)
        pg.evaluate("document.querySelectorAll('.ui-panel')[2]"
                    ".scrollIntoView({block:'start'})")
        pg.wait_for_timeout(250)
        pg.screenshot(path=OUT + "/18_p3_short.png", full_page=False)

        # 口径
        pg.click('[data-view="about"]')
        pg.wait_for_timeout(250)
        res["口径块数"] = pg.eval_on_selector_all(".ui-panel", "n=>n.length")
        pg.screenshot(path=OUT + "/06_about.png")

        # 移动端
        m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        m.on("pageerror", lambda e: errs.append("mobile pageerror: %s" % e))
        m.goto(URL % port, wait_until="networkidle")
        m.wait_for_selector(".card", timeout=20000)
        res["移动端溢出"] = m.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        m.click('[data-view="hf"]')
        m.wait_for_selector("[data-hfcode]", timeout=15000)
        m.wait_for_timeout(300)
        res["移动端候风溢出"] = m.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        res["移动端可见止损列"] = m.eval_on_selector_all(
            "[data-hfcode] .sl", "n=>n.filter(x=>x.offsetParent!==null).length")
        m.screenshot(path=OUT + "/12_mobile_hf.png", full_page=False)
        m.evaluate("document.querySelector('[data-hfcode]')"
                   ".scrollIntoView({block:'center'})")
        m.wait_for_timeout(250)
        m.screenshot(path=OUT + "/12b_mobile_hf_table.png", full_page=False)
        m.click('[data-view="p3"]')
        m.wait_for_selector(".p3-row", timeout=15000)
        m.wait_for_timeout(300)
        res["移动端三件事溢出"] = m.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        m.screenshot(path=OUT + "/19b_mobile_p3.png", full_page=False)
        m.click('[data-view="ab"]')
        m.wait_for_selector("[data-absel]", timeout=15000)
        m.wait_for_timeout(300)
        res["移动端对照溢出"] = m.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        m.screenshot(path=OUT + "/13b_mobile_ab.png", full_page=False)
        m.click('[data-view="up"]')
        m.wait_for_selector(".up-row", timeout=15000)
        m.wait_for_timeout(300)
        res["移动端升级溢出"] = m.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 1")
        m.screenshot(path=OUT + "/14b_mobile_up.png", full_page=False)
        b.close()

    print("=" * 78)
    print("渲染核对 · 结构池 / 模拟仓 / 候风盘")
    print("=" * 78)
    bad = 0
    for k, v in res.items():
        flag = ""
        if k in ("横向溢出", "移动端溢出", "移动端候风溢出") and v is True:
            flag, bad = "  ← 溢出", bad + 1
        if k == "净值曲线点数" and (v or 0) < 50:
            flag, bad = "  ← 曲线点数太少", bad + 1
        if k == "止损列空值" and (v or 0) > 0:
            flag, bad = "  ← 有持仓没算止损位", bad + 1
        if k == "详情字段数" and (v or 0) < 6:
            flag, bad = "  ← 字段太少", bad + 1
        if k == "候风详情字段" and (v or 0) < 8:
            flag, bad = "  ← 候风详情字段太少", bad + 1
        if k == "dialog 已打开" and v is not True:
            flag, bad = "  ← 没弹出来", bad + 1
        if k == "持仓行数" and (v or 0) < 1:
            flag, bad = "  ← 候风盘没有持仓行", bad + 1
        if k == "移动端可见止损列" and (v or 0) < 1:
            flag, bad = "  ← 移动端看不到止损", bad + 1
        print("  %-16s %s%s" % (k, v, flag))
    print("-" * 78)
    print("  控制台异常 %d 条" % len(errs))
    for e in errs[:8]:
        print("    " + e)
    print("  结论：%s" % ("通过" if (not errs and not bad) else
                         "有 %d 处问题" % (bad + len(errs))))
    return 0 if (not errs and not bad) else 1


if __name__ == "__main__":
    sys.exit(main())
