# -*- coding: utf-8 -*-
"""K-STOCK-313：终选参数敏感性复验汇总（311 hist_st 新宇宙）。"""

import json

ROWS = [
    ("3y base(no gate)", "data/bt2/bt2_311y3hst_base.json"),
    ("3y s70", "data/bt2/bt2_3133ys70.json"),
    ("3y s80 final", "data/bt2/bt2_311y3hst_final.json"),
    ("3y s85", "data/bt2/bt2_3133ys85.json"),
    ("3y t5", "data/bt2/bt2_3133yt5.json"),
    ("3y t10 final", "data/bt2/bt2_311y3hst_final.json"),
    ("3y t20", "data/bt2/bt2_3133yt20.json"),
    ("1y base(no gate)", "data/bt2/bt2_311y1hst_base.json"),
    ("1y s70", "data/bt2/bt2_3131ys70.json"),
    ("1y s80 final", "data/bt2/bt2_311y1hst_final.json"),
    ("1y s85", "data/bt2/bt2_3131ys85.json"),
    ("1y t5", "data/bt2/bt2_3131yt5.json"),
    ("1y t10 final", "data/bt2/bt2_311y1hst_final.json"),
    ("1y t20", "data/bt2/bt2_3131yt20.json"),
]

print("%-20s %9s %7s %7s %8s" % ("arm", "ret%", "PF", "trades", "mdd%"))
for label, fp in ROWS:
    d = json.load(open(fp, encoding="utf-8"))
    s = d["summary"]
    print(
        "%-20s %9s %7s %7s %8s"
        % (
            label,
            s["total_return_pct"],
            s["profit_factor"],
            s["n_trades"],
            s["max_drawdown_pct"],
        )
    )
