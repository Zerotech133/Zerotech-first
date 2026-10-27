# -*- coding: utf-8 -*-
"""K-STOCK-317 槽数×regime 联动对照汇总（mid=5 联动 vs 固定 t10/t5）。"""

import json

BASE = r"C:/项目/老田的AI助手/quant_backtest/data/bt2/"
rows = [
    ("三年窗·固定t10(315终选)", "bt2_311y3hst_final.json"),
    ("三年窗·固定t5(313)", "bt2_3133yt5.json"),
    ("三年窗·联动mid=5(317)", "bt2_317y3link5.json"),
    ("一年窗·固定t10(315终选)", "bt2_311y1hst_final.json"),
    ("一年窗·固定t5(313)", "bt2_3131yt5.json"),
    ("一年窗·联动mid=5(317)", "bt2_317y1link5.json"),
]
print(
    "%-24s %9s %7s %7s %8s %8s %s"
    % ("arm", "ret%", "PF", "trades", "mdd%", "仓位%", "mid联动")
)
for label, fp in rows:
    d = json.load(open(BASE + fp, encoding="utf-8"))
    s = d["summary"]
    m = d.get("meta", {})
    link = m.get("top_n_mid") or "-"
    print(
        "%-24s %9s %7s %7s %8s %8s %s"
        % (
            label,
            s["total_return_pct"],
            s["profit_factor"],
            s["n_trades"],
            s["max_drawdown_pct"],
            s.get("avg_invested_pct"),
            ("mid=5·触发%s天" % s.get("n_mid_slot_days")) if link != "-" else "-",
        )
    )
