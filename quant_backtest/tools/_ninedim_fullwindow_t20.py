# -*- coding: utf-8 -*-
"""ninedim 满窗 T+20 检验（K-STOCK 挂账待办清偿）。
数据源: data/nine_dim_replay.jsonl（双周锚 20240902-20261008, 52 锚）。
口径: 仅 mature20=true 行; 超额 = r20 - bench20（同锚基准）。
输出: 控制台报告 + reports/_ninedim_fullwindow_t20_20261015.json。
"""

import json
import statistics as st
from collections import defaultdict

FP = "data/nine_dim_replay.jsonl"
OUT = "reports/_ninedim_fullwindow_t20_20261015.json"

rows = [json.loads(l) for l in open(FP, encoding="utf-8") if l.strip()]
mature = [r for r in rows if r.get("mature20") and r.get("r20") is not None]
for r in mature:
    r["ex20"] = r["r20"] - (r.get("bench20") or 0.0)


def stats(sub):
    if not sub:
        return {"n": 0}
    ex = [r["ex20"] for r in sub]
    return {
        "n": len(sub),
        "hit": round(sum(1 for e in ex if e > 0) / len(ex), 4),
        "mean_ex": round(st.mean(ex), 2),
        "median_ex": round(st.median(ex), 2),
        "mean_r20": round(st.mean(r["r20"] for r in sub), 2),
        "p10_ex": round(sorted(ex)[int(0.1 * len(ex))], 2),
        "p90_ex": round(sorted(ex)[min(int(0.9 * len(ex)), len(ex) - 1)], 2),
    }


report = {
    "window": {
        "first_anchor": min(r["anchor"] for r in rows),
        "last_anchor": max(r["anchor"] for r in rows),
        "anchors": len(set(r["anchor"] for r in rows)),
        "rows_total": len(rows),
        "rows_mature20": len(mature),
    },
    "overall": stats(mature),
    "by_score_band": {},
    "by_year": {},
    "by_cov": {},
}

# score 分层（反指验证：高分是否更差）
bands = [(0, 10), (10, 20), (20, 40), (40, 70), (70, 1e9)]
for lo, hi in bands:
    sub = [r for r in mature if lo <= (r.get("score") or 0) < hi]
    report["by_score_band"][f"{lo}-{hi if hi < 1e9 else '+'}"] = stats(sub)

# 按年
byyear = defaultdict(list)
for r in mature:
    byyear[r["anchor"][:4]].append(r)
for y in sorted(byyear):
    report["by_year"][y] = stats(byyear[y])

# cov 覆盖度分层（低 cov 票信号质量）
for lo, hi in [(0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]:
    sub = [r for r in mature if lo <= (r.get("cov") or 0) < hi]
    report["by_cov"][f"{lo}-{hi}"] = stats(sub)

# 逐锚超额均值的时间序列（稳定性）
byanchor = defaultdict(list)
for r in mature:
    byanchor[r["anchor"]].append(r)
ts = {a: round(st.mean(x["ex20"] for x in rs), 2) for a, rs in sorted(byanchor.items())}
neg_anchors = sum(1 for v in ts.values() if v < 0)
report["anchor_ts"] = ts
report["anchors_neg_frac"] = round(neg_anchors / len(ts), 3)

json.dump(report, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("=== ninedim 满窗 T+20 检验 ===")
print("窗口:", report["window"])
print("总体:", report["overall"])
print("score 分层:")
for k, v in report["by_score_band"].items():
    print(" ", k, v)
print("按年:")
for k, v in report["by_year"].items():
    print(" ", k, v)
print("cov 分层:")
for k, v in report["by_cov"].items():
    print(" ", k, v)
print("锚日负超额占比:", report["anchors_neg_frac"])
print("机读底稿 ->", OUT)
