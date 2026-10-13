# -*- coding: utf-8 -*-
"""K-STOCK-315：311 锚定口径（hist_st）五臂对照总表汇总。"""
import json

ROWS = [
    ("1 base off", "data/bt2/bt2_311y3hst_base.json"),
    ("2 weak", "data/bt2/bt2_315y3weak.json"),
    ("3 high only", "data/bt2/bt2_315y3high.json"),
    ("4 high+score", "data/bt2/bt2_315y3hsc.json"),
    ("5 high+min80 (final)", "data/bt2/bt2_311y3hst_final.json"),
]

print("%-22s %9s %7s %7s %8s %8s" % ("arm", "ret%", "PF", "trades", "mdd%", "avg_inv%"))
for label, fp in ROWS:
    d = json.load(open(fp, encoding="utf-8"))
    s = d["summary"]
    print("%-22s %9s %7s %7s %8s %8s" % (
        label, s["total_return_pct"], s["profit_factor"], s["n_trades"],
        s["max_drawdown_pct"], s.get("avg_invested_pct")))
