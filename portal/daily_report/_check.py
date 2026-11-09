# -*- coding: utf-8 -*-
"""对每日汇报 H5 做移动端截图 + 交互自检（只读，不改页面）。

检查项：
  1. 四屏切换后每屏都能渲染（无 JS 报错）
  2. 点个股卡能开抽屉，抽屉里三条判据都出现 ✓
  3. 返回能关抽屉
产物：shots/s0..s3.png、shot_drawer.png
"""
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = "file:///" + os.path.join(HERE, "index.html").replace("\\", "/")
SHOTS = os.path.join(HERE, "shots")


def main():
    os.makedirs(SHOTS, exist_ok=True)
    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 390, "height": 844},
                        device_scale_factor=2, is_mobile=True,
                        has_touch=True)
        pg.on("pageerror", lambda e: errs.append("pageerror: %s" % e))
        pg.on("console", lambda m: errs.append("console.%s: %s" % (m.type, m.text))
              if m.type == "error" else None)
        pg.goto(PAGE, wait_until="load")
        pg.wait_for_timeout(400)

        tabs = pg.query_selector_all("#tabs button")
        print("tabs:", len(tabs))
        for i, t in enumerate(tabs):
            t.click()
            pg.wait_for_timeout(320)
            pg.screenshot(path=os.path.join(SHOTS, "s%d.png" % i))
            cls = pg.eval_on_selector_all(".scr.on", "els=>els.map(e=>e.id)")
            print("  tab %d -> active screens %s" % (i, cls))

        # 回到选股屏，点第一只
        tabs[1].click()
        pg.wait_for_timeout(300)
        n_stk = pg.eval_on_selector_all("#stkList .stk", "els=>els.length")
        n_exp = pg.evaluate("() => (((window.__R__||{}).counts||{}).confluence)")
        print("stock cards:", n_stk, "| 数据包 confluence:", n_exp)
        # 原来这里写死 == 7，换任何一个非 7 只的交易日自检就挂，
        # 等于把自检绑在一天上。改成与数据包比。
        assert n_stk == n_exp, "个股卡 %d 张，数据包 confluence %s——两者不一致" % (n_stk, n_exp)

        # 持仓跟踪卡：数据包有 hold 就必须渲染出内容，否则说明接的那一列断了
        has_hold = pg.evaluate("() => !!(window.__R__||{}).hold")
        hold_txt = pg.inner_text("#holdCard").strip()
        print("hold card:", ("有内容" if hold_txt else "空"),
              "| 数据包 hold:", has_hold)
        if has_hold:
            assert hold_txt, "数据包有 hold，但跟踪卡渲染为空"
            assert "买入后平均" in hold_txt, (
                "跟踪卡文案缺失，实际: %r" % hold_txt[:80])
            pg.click("#holdCard .card")
            pg.wait_for_timeout(450)
            hb = pg.inner_text("#shB")
            for k in ("买入之后赚了多少", "这几个数怎么算的"):
                assert k in hb, "跟踪抽屉缺少段落: %s" % k
            pg.screenshot(path=os.path.join(SHOTS, "shot_hold.png"))
            pg.click("#back")
            pg.wait_for_timeout(400)

        pg.click("#stkList .stk")
        pg.wait_for_timeout(500)
        opened = pg.eval_on_selector(".sheet", "e=>e.classList.contains('on')")
        title = pg.inner_text("#shT")
        body = pg.inner_text("#shB")
        print("drawer open:", opened, "| title:", title)
        for k in ("① 跌够了", "② 大钱在进", "③ 公司在托",
                  "当天行情", "触发公告原文", "数据出处"):
            assert k in body, "抽屉缺少段落: %s" % k
        pg.screenshot(path=os.path.join(SHOTS, "shot_drawer.png"),
                      full_page=False)
        pg.click("#back")
        pg.wait_for_timeout(450)
        closed = not pg.eval_on_selector(".sheet", "e=>e.classList.contains('on')")
        print("drawer closed:", closed)
        assert closed, "返回未关闭抽屉"

        # 抽屉内滚动截图（证据下半段）
        pg.click("#stkList .stk")
        pg.wait_for_timeout(400)
        pg.eval_on_selector("#shB", "e=>e.scrollTop=e.scrollHeight")
        pg.wait_for_timeout(250)
        pg.screenshot(path=os.path.join(SHOTS, "shot_drawer2.png"))
        pg.click("#back")
        pg.wait_for_timeout(400)

        # ── 数据驱动守卫 ───────────────────────────────────────────
        # 首屏三个数字必须等于数据包里的 counts / data_health，
        # 任何一处被改回写死，这里立刻失败（2026-11-09 的债就此关门）。
        got = pg.evaluate("""() => {
          var R = window.__R__ || {};
          var b = document.querySelectorAll('#nums .num b');
          return {
            ui: [b[0] && b[0].textContent, b[1] && b[1].textContent,
                 b[2] && b[2].textContent],
            cf: R.counts && R.counts.confluence,
            un: R.counts && R.counts.union,
            gap: ((R.data_health && R.data_health.missing) || []).length,
            sets: ((R.data_health && R.data_health.sets) || []).length,
            title: (document.getElementById('heroTitle') || {}).textContent || '',
            kicker: (document.getElementById('kicker') || {}).textContent || ''
          };
        }""")
        print("首屏:", got["ui"], "| 数据包:", got["cf"], got["un"], "缺口", got["gap"])
        assert got["ui"][0] == str(got["cf"]), (
            "首屏第一个数字 %r 与 counts.confluence %r 不一致——疑似又写死了"
            % (got["ui"][0], got["cf"]))
        assert got["ui"][1] == str(got["un"]), (
            "首屏第二个数字 %r 与 counts.union %r 不一致——疑似又写死了"
            % (got["ui"][1], got["un"]))
        assert got["ui"][2] == str(got["gap"]), (
            "首屏第三个数字 %r 与 data_health.missing 长度 %r 不一致"
            % (got["ui"][2], got["gap"]))
        assert got["sets"] == 7, "data_health.sets 应为 7 类，实际 %r" % got["sets"]
        assert got["title"].strip(), "首屏标题为空"
        assert "2026" not in got["kicker"] or "/" not in got["kicker"], got["kicker"]

        b.close()

    if errs:
        print("\n!! JS errors:")
        for e in errs:
            print("   ", e)
        return 1
    print("\nOK: 无 JS 错误")
    return 0


if __name__ == "__main__":
    sys.exit(main())
