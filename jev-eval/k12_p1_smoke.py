# -*- coding: utf-8 -*-
"""K12 P1 冒烟测试：company_360 三件（per 切换/在职历任/游资区间）"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\项目\老田的AI助手\quant_backtest")
import kg_portal as K

print("== 1) 600519.SH 默认（最新报告期） ==")
d = K.api("company360", "600519.SH", 90)
print("ok=%s per=%s" % (d.get("ok"), d.get("per")))
hp = d.get("hist", {}).get("hold_periods")
print("hold_periods(%d)=%s" % (len(hp or []), hp))
ps = [n for n in d["nodes"] if n["sector"] == "person"]
cur = [p for p in ps if not p.get("past")]
past = [p for p in ps if p.get("past")]
print("高管图上 %d：在职 %d / 历任 %d" % (len(ps), len(cur), len(past)))
if past:
    print("历任样本:", past[0]["label"], past[0]["props"].get("任期"),
          past[0]["props"].get("状态"))
if cur:
    print("在职样本:", cur[0]["label"], cur[0]["props"].get("任期"))
rs = [r for r in d["rel_stats"] if r["rel"] == "SERVES_AS"]
print("SERVES_AS note:", rs[0]["note"] if rs else "—")

print("\n== 2) 600519.SH 指定旧报告期 per ==")
if hp and len(hp) > 1:
    p_old = hp[-1]
    d2 = K.api("company360", "600519.SH", 90, per=p_old)
    print("per(传入 %s)=实际 %s" % (p_old, d2.get("per")))
    hs = [n for n in d2["nodes"] if n["sector"] == "holder"]
    print("股东节点 %d，报告期 props=%s" % (len(hs),
          hs[0]["props"].get("报告期") if hs else "—"))
d3 = K.api("company360", "600519.SH", 90, per="19990101")
print("非法 per 回退最新:", d3.get("per"))

print("\n== 3) 002743.SZ 游资区间 ==")
d4 = K.api("company360", "002743.SZ", 180)
print("ok=%s" % d4.get("ok"))
for n in [x for x in d4["nodes"] if x["sector"] == "hotmoney"][:4]:
    print(" ", n["label"], "| 区间", n["props"].get("区间"),
          "| 最近", n["props"].get("最近"))
print("\nALL SMOKE DONE")
