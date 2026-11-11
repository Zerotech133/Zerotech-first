# -*- coding: utf-8 -*-
"""P64 逐月明细：基准 vs 各变体，含样本外段落差与 202609 崩溃月对照。"""
import json

REPORTS = r"C:/项目/老田的AI助手/quant_backtest/reports"
d = json.load(open(REPORTS + "/_p64_weight_12m.json", encoding="utf-8"))
rows = d["weight_ab"]["monthly"]["rows"]
by = {}
for r in rows:
    by.setdefault(r["cfg"], {})[r["month"]] = r
months = sorted({r["month"] for r in rows})
cfgs = [c for c in ("w4_rps25", "w4_rps30", "w4_rps35", "w4_rps40", "w4_reb35", "w4_reb40")]

print("逐月 T+5（%）与笔数")
print("%-8s" % "month" + "".join("%18s" % c.replace("w4_", "") for c in cfgs))
for m in months:
    line = "%-8s" % m
    for c in cfgs:
        r = by.get(c, {}).get(m, {})
        v = r.get("t5")
        line += "%18s" % ("—" if v is None else "%.3f(%d)" % (v * 100, r.get("n_buy", 0)))
    print(line)

print("\n逐月 T+10（%）")
print("%-8s" % "month" + "".join("%18s" % c.replace("w4_", "") for c in cfgs))
for m in months:
    line = "%-8s" % m
    for c in cfgs:
        r = by.get(c, {}).get(m, {})
        v = r.get("t10")
        line += "%18s" % ("—" if v is None else "%.3f" % (v * 100))
    print(line)

half = len(months) // 2
iss, oos = months[:half], months[half:]
print("\n分段月均（T+5 / T+10），段内按月算术平均（跳过无信号月）")


def seg(c, ms, h):
    vals = [by[c][m][h] for m in ms if by.get(c, {}).get(m, {}).get(h) is not None]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


for tag, ms in (("IS  前%d月" % half, iss), ("OOS 后%d月" % (len(months) - half), oos)):
    print("  %s  %s ~ %s" % (tag, ms[0], ms[-1]))
    for c in cfgs:
        a5, n5 = seg(c, ms, "t5")
        a10, n10 = seg(c, ms, "t10")
        print("    %-9s T+5 %8s  T+10 %8s  (有效月 %d/%d)" % (
            c.replace("w4_", ""),
            "n/a" if a5 is None else "%+.4f%%" % (a5 * 100),
            "n/a" if a10 is None else "%+.4f%%" % (a10 * 100), n5, n10))
