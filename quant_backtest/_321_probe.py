# -*- coding: utf-8 -*-
"""321 勘查：计划任务清单 + tdx manifest 运行痕迹。"""
import json

print("== manifest ==")
m = json.load(open(r"E:/老田的AI助手/quant_backtest/data/tdx/_manifest.json", encoding="utf-8"))
for t in ("ts_daily", "ts_adj_factor"):
    e = m.get(t) or {}
    if isinstance(e, dict):
        keys = [k for k in e.keys()][:10]
        print(t, "keys:", keys)
        for k in ("done", "latest", "latest_date", "updated_at", "last_run", "rows"):
            if k in e:
                print("   ", k, "=", e[k])
