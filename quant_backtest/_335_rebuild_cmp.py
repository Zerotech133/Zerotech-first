# -*- coding: utf-8 -*-
"""K-STOCK-335 · 重建 327 正式版三臂对照表 + meta 生效证据核验。"""
import json
import os

QBT = os.path.dirname(os.path.abspath(__file__))
ARMS = ["dknife", "chan_base", "chan_final"]

rows, meta_lines = [], []
for suf in ARMS:
    fp = os.path.join(QBT, "data", "bt2", "bt2_327_%s.json" % suf)
    d = json.load(open(fp, encoding="utf-8"))
    m, s = d["meta"], d["summary"]
    mp = m.get("manual_picks") or []
    rows.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        suf, s["total_return_pct"], s["profit_factor"], s["n_trades"],
        s["win_rate_pct"], s["max_drawdown_pct"], s.get("avg_invested_pct"),
        len(mp)))
    meta_lines.append("%s: picks=%d gate=%s min=%s hist_st=%s" % (
        suf, len(mp), m.get("gate_mode"), m.get("min_score"),
        "on" if (m.get("hist_st") or {}).get("on") else "OFF"))

lines = ["# K-STOCK-327 正式版：双刀×缠论并轨对照（窗 20260901~20261012·同宇宙 5037 只·终版快照）",
         "",
         "| 臂 | ret% | PF | 笔数 | 胜率% | 回撤% | 平均仓位% | manual_picks |",
         "|---|---|---|---|---|---|---|---|"] + rows + [
         "", "## meta 生效证据", ""] + ["- " + x for x in meta_lines]

out = os.path.join(QBT, "_327_compare.md")
open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
