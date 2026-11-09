# -*- coding: utf-8 -*-
"""数据驱动守卫：篡改数据包，验证页面每个数字都真的跟着变。

为什么需要这个而不是普通自检：
  `_check.py` 只能发现「页面 ≠ 数据包」。如果模板里写死 7、数据包里也是 7，
  两边一致，断言照样通过——写死的坑就藏住了。
  本脚本把数据包里所有会显示的数字统统 +1（并制造一个数据缺口），
  页面必须逐项跟随；任何一处没跟随，说明那处还是硬编码。

用法（Py311，需 playwright）：
  "/c/Program Files/Python311/python.exe" _check_drift.py
退出码 0 = 全部跟随；1 = 有写死残留。
"""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = r"C:\项目\zerobound-kg\factor_proto\data\report_20261106.json"
TMP_JSON = r"C:\Temp\_drift_report.json"
TMP_HTML = r"C:\Temp\_drift_index.html"


def mutate(d):
    """把每个会显示的数字都改掉，并制造一个缺口。"""
    d = copy.deepcopy(d)
    d["counts"]["confluence"] += 1
    d["counts"]["union"] += 1
    d["counts"]["overlap"] += 1
    for i, f in enumerate(d["counts"].get("factor_breakdown") or []):
        f["n"] += 10 + i
    d["summary"]["top_industry"]["n"] += 1
    d["summary"]["pct_min"] = -88.88
    d["summary"]["pct_max"] = -0.01
    d["summary"]["worst"]["name"] = "漂移测试股"
    d["summary"]["worst"]["amount_yi"] = 777.77

    sets = d["data_health"]["sets"]
    for s in sets:
        s["n"] += 1
        s["by_ex"] = {k: v + 1 for k, v in (s["by_ex"] or {}).items()}
    # 制造缺口：把两融标成缺失
    for s in sets:
        if s["id"] == "margin_detail":
            s["ok"] = False
            s["n"] = 0
            s["by_ex"] = {"SH": 0, "SZ": 0, "BJ": 0}
    d["data_health"]["missing"] = ["margin_detail"]
    d["data_health"]["complete"] = len(sets) - 1
    d["margin"]["asof"] = {"SH": 11, "SZ": 0, "BJ": 0}
    d["margin"]["missing"] = ["SZ", "BJ"]

    # 持仓跟踪也要漂移。这一块最容易写成「今天赚 2.6%」的静态文案，
    # 因为它天天都有值、看起来又对，写死了没人发现。
    h = d.get("hold")
    if h and h.get("batches"):
        h["day_index"] = 9
        h["as_of"] = "14:07:00"
        h["bench"]["ret"] = -0.0456
        b0 = h["batches"][0]
        b0["ret"] = 0.0777
        b0["excess"] = 0.1234
        b0["win"] = 2
        b0["n_priced"] = 5

    # 系统屏（R.sys，来自每日简报）同样要漂移。这里最容易残留
    # 「149 条 / 17193 次 / 24 张」这类天天有值的数字。
    sy = d.get("sys")
    if sy:
        if sy.get("signals"):
            sy["signals"]["n"] = 150
            sy["signals"]["top"] = [
                {"name": "漂移股A", "dir": "sell", "score": 55.5, "conf": 0.9}]
        if sy.get("risk"):
            sy["risk"][0] = {"r": "R1", "pass": 111, "block": 22,
                             "score": 0.5555, "gate": "block"}
        if sy.get("lineage"):
            sy["lineage"]["tables"] = 25
            sy["lineage"]["broken"] = 1
            sy["lineage"]["stale"] = 3
            sy["lineage"]["stale_detail"] = [["drift_table", 5]]
        sy["src"] = "brief_drift_test.md"
    return d


def main():
    if not os.path.exists(DATA):
        print("找不到数据包: %s" % DATA)
        return 2
    src = json.load(io.open(DATA, encoding="utf-8"))
    d = mutate(src)
    json.dump(d, io.open(TMP_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    r = subprocess.run([sys.executable, os.path.join(HERE, "build.py"),
                        "--data", TMP_JSON, "--out", TMP_HTML, "--no-houfeng"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("构建失败:", r.stdout, r.stderr)
        return 2

    from playwright.sync_api import sync_playwright

    exp = {
        "confluence": str(d["counts"]["confluence"]),
        "union": str(d["counts"]["union"]),
        "gap": str(len(d["data_health"]["missing"])),
        "fb0": str(d["counts"]["factor_breakdown"][0]["n"]),
        "worst": d["summary"]["worst"]["name"],
        "ds0": str(d["data_health"]["sets"][0]["n"]),
    }
    fails = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 390, "height": 844},
                        device_scale_factor=2, is_mobile=True, has_touch=True)
        pg.goto("file:///" + TMP_HTML.replace("\\", "/"), wait_until="load")
        pg.wait_for_timeout(450)

        ui = pg.evaluate("""() => {
          var b = document.querySelectorAll('#nums .num b');
          return [b[0] && b[0].textContent, b[1] && b[1].textContent,
                  b[2] && b[2].textContent];
        }""")
        title = pg.inner_text("#heroTitle")
        print("首屏数字 UI=%s  期望=[%s, %s, %s]"
              % (ui, exp["confluence"], exp["union"], exp["gap"]))
        for i, k in enumerate(("confluence", "union", "gap")):
            if ui[i] != exp[k]:
                fails.append("首屏第 %d 个数字 UI=%r 期望 %r（该处仍写死）"
                             % (i + 1, ui[i], exp[k]))
        if "整段没了" not in title:
            fails.append("标题未随缺口切换：%r" % title.replace("\n", " "))

        pg.click("#tabs button[data-k='s1']")
        pg.wait_for_timeout(320)
        body = pg.inner_text("#unionBody")
        print("并集卡:", body.replace("\n", " ")[:90])
        if exp["fb0"] not in body:
            fails.append("并集卡未跟随 factor_breakdown（期望含 %s）" % exp["fb0"])
        note = pg.inner_text("#pickNote")
        if exp["worst"] not in note:
            fails.append("选股警示未跟随 summary.worst（期望含 %s）" % exp["worst"])

        # ── 持仓跟踪卡 ──
        h = d.get("hold")
        if h and h.get("batches"):
            b0 = h["batches"][0]
            hc = pg.inner_text("#holdCard")
            print("跟踪卡:", hc.replace("\n", " ")[:110])
            for frag, why in (
                    ("7.8%", "买入后收益"),
                    ("第9天", "持有日序号"),
                    ("-4.56%", "基准收益"),
                    ("12.3", "超额"),
                    ("5 只里 2 只", "跑赢只数/总数")):
                if frag not in hc:
                    fails.append("跟踪卡未跟随%s（期望含 %r）" % (why, frag))
            # 个股卡右侧也应换成持有收益
            stk = pg.inner_text("#stkList")
            if "持有第9天" not in stk:
                fails.append("个股卡未跟随持有日（期望「持有第9天」）")

        pg.click("#tabs button[data-k='s2']")
        pg.wait_for_timeout(320)
        ds = pg.inner_text("#dsList")
        print("数据屏首行:", ds.replace("\n", " ")[:70])
        if exp["ds0"] not in ds:
            fails.append("数据屏未跟随 data_health.sets[0].n（期望含 %s）" % exp["ds0"])

        # ── 系统屏里两处「这 N 只」 ──
        pg.click("#tabs button[data-k='s3']")
        pg.wait_for_timeout(320)
        for sid, why in (("sysCntA", "导语"), ("sysCntB", "另一套模型注解")):
            got = pg.inner_text("#" + sid)
            if got.strip() != exp["confluence"]:
                fails.append("系统屏%s的「这 N 只」写死：UI=%r 期望 %r"
                             % (why, got, exp["confluence"]))
        # ── 系统屏全部数字/文案跟随 R.sys ──
        sy = d.get("sys")
        if sy:
            s3txt = pg.inner_text("#s3")
            sys_exp = [
                ("150", "信号条数 sysSigN"),
                ("打分最高的是 漂移股A（55.5 分）", "信号摘要 sysSigTop"),
                ("共 133 次下单请求，放行 111 次、拦下 22 次", "风控总数 sysRiskMain"),
                ("风险分 0.56", "风险分 sysRiskSub"),
                ("25", "数据表张数 sysLinN"),
                ("1 张断供，3 张超过 5 个交易日没更新", "体检摘要 sysLinMain"),
                ("brief_drift_test.md", "来源标注 footSrc"),
            ]
            for frag, why in sys_exp:
                if frag not in s3txt:
                    fails.append("系统屏未跟随%s（期望含 %r）" % (why, frag))
            if "链路全通" in s3txt and "出了故障" not in s3txt:
                pass  # 链路全有 ok 才会这样，漂移数据没动 chains 属正常
            # 链路标题：漂移数据 chains 未改，state 保持 fail → 应含「出了故障」
            if "出了故障" not in s3txt:
                fails.append("系统屏标题未由链路状态生成（期望含「出了故障」）")
            # 抽屉：信号抽屉应显示漂移后的方向（卖出）
            pg.evaluate("() => { document.querySelector('[data-ev=signals]').click(); }")
            pg.wait_for_timeout(250)
            drawer = pg.inner_text("#shB")
            if "共 150 条" not in drawer or "卖出" not in drawer:
                fails.append("信号抽屉未跟随（期望含「共 150 条」「卖出」）")
            pg.evaluate("() => { document.getElementById('back').click(); }")
            pg.wait_for_timeout(200)
            # 风控抽屉
            pg.evaluate("() => { document.querySelector('[data-ev=risk]').click(); }")
            pg.wait_for_timeout(250)
            drawer = pg.inner_text("#shB")
            if "<td>111</td>" not in drawer.replace("\n", "") and "111" not in drawer:
                fails.append("风控抽屉未跟随（期望 R1 放行 111）")
            pg.evaluate("() => { document.getElementById('back').click(); }")
            pg.wait_for_timeout(200)
            # 血缘抽屉
            pg.evaluate("() => { document.querySelector('[data-ev=lineage]').click(); }")
            pg.wait_for_timeout(250)
            drawer = pg.inner_text("#shB")
            if "drift_table" not in drawer:
                fails.append("血缘抽屉未跟随（期望含 drift_table）")
            pg.evaluate("() => { document.getElementById('back').click(); }")
        b.close()

    if fails:
        print("\n!! 仍有写死残留：")
        for f in fails:
            print("   -", f)
        return 1
    print("\nOK: 全部检查项跟随数据包，无写死残留")
    for f in (TMP_JSON, TMP_HTML):
        try:
            os.remove(f)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
