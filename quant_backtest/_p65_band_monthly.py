# -*- coding: utf-8 -*-
"""P65 breadth 分档逐月：检验「甜点区 50-60」是否只在样本内成立。"""
import json

SRC = r"C:/项目/老田的AI助手/quant_backtest/reports/_p65_env_split_v2.json"
d = json.load(open(SRC, encoding="utf-8"))
bm = d["env_split"]["breadth_monthly"]
bands = ["br<30", "br30_40", "br40_50", "br50_60", "br60_70", "br>=70"]
months = sorted({r["month"] for v in bm.values() for r in v["rows"]})

print("逐月 T+5（%，括号为该档当月笔数）")
print("%-8s" % "month" + "".join("%19s" % b for b in bands))
for m in months:
    line = "%-8s" % m
    for b in bands:
        r = [x for x in bm[b]["rows"] if x["month"] == m][0]
        v = r["t5"]
        line += "%19s" % ("—" if v is None else "%.2f(%d)" % (v * 100, r["n_buy"]))
    print(line)

print()
print("逐月 T+10（%）")
print("%-8s" % "month" + "".join("%19s" % b for b in bands))
for m in months:
    line = "%-8s" % m
    for b in bands:
        r = [x for x in bm[b]["rows"] if x["month"] == m][0]
        v = r["t10"]
        line += "%19s" % ("—" if v is None else "%.2f" % (v * 100))
    print(line)

print()
half = len(months) // 2
iss, oos = months[:half], months[half:]


def seg(b, ms, h):
    v = [x[h] for x in bm[b]["rows"] if x["month"] in ms and x.get(h) is not None]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


print("分段月均（跳过该档无信号的月）")
for tag, ms in (("IS ", iss), ("OOS", oos)):
    print(" %s %s~%s" % (tag, ms[0], ms[-1]))
    for b in bands:
        a, n = seg(b, ms, "t5")
        c, _ = seg(b, ms, "t10")
        print("   %-8s T+5 %9s  T+10 %9s  (月 %d)" % (
            b, "n/a" if a is None else "%+.3f%%" % (a * 100),
            "n/a" if c is None else "%+.3f%%" % (c * 100), n))

print()
print("甜点区 br50_60 逐月正负计数")
pos = sum(1 for x in bm["br50_60"]["rows"] if x["t5"] is not None and x["t5"] > 0)
neg = sum(1 for x in bm["br50_60"]["rows"] if x["t5"] is not None and x["t5"] <= 0)
pos10 = sum(1 for x in bm["br50_60"]["rows"] if x["t10"] is not None and x["t10"] > 0)
neg10 = sum(1 for x in bm["br50_60"]["rows"] if x["t10"] is not None and x["t10"] <= 0)
print("  T+5  : 正 %d 月 / 负 %d 月" % (pos, neg))
print("  T+10 : 正 %d 月 / 负 %d 月" % (pos10, neg10))
