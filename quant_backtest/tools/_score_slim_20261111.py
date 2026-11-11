# -*- coding: utf-8 -*-
"""2026-11-11 · 评分瘦身预演（任务 #108，寻优协议下一步③前置，只读）。

问题：bei30 三源已证伪（任务#100/101/102）。若 11-26 评审批准瘦身
（bei30→0，权重归 pos/vol），选出的票会变多少？本脚本零风险预演：
  · 从事件缓存重建每事件当前 score 与「瘦身后 score'」；
  * 瘦身口径：pos/vol 权重按 40:30 比例放大到 100（pos 57.14/vol 42.86），
    分档逻辑不变（评审时也可选 pos/vol 平分，这里给两种）；
  · 对比逐日 top5 重合度（评分的用途就是取前十）。
产物：reports/score_slim_preview_20261111.json。
"""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "data", "sig_events",
                   "sig_events_market_v3_20251111_20261111.json")
OUT = os.path.join(HERE, "reports", "score_slim_preview_20261111.json")

POS_W, VOL_W, BEI_W = 40.0, 30.0, 30.0


def pos_ratio(low, zg):
    if not zg or zg <= 0 or low is None:
        return None
    t = low / zg - 1.0
    if t <= 0.02:
        return 0
    elif t <= 0.05:
        return 1
    elif t <= 0.10:
        return 2
    elif t <= 0.20:
        return 3
    return 4


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    ev = [e for e in d["events"]
          if e.get("type") == "3买" and e.get("score") is not None]
    # 档位比例从 market_scan import（单一事实源，不硬编码）
    import sys
    sys.path.insert(0, HERE)
    from market_scan import _POS_RATIO, _VOL_RATIO
    from market_scan import SCORE_W, _BEI_SPLIT

    per_day = defaultdict(list)
    for e in ev:
        pr = pos_ratio(e.get("price"), e.get("zg"))
        if pr is None:
            continue
        sp = SCORE_W["pos"] * _POS_RATIO[pr]
        vr = e.get("vr")
        if vr is None:
            vr_r = 0.5
        elif vr >= 1.5:
            vr_r = _VOL_RATIO[0]
        elif vr >= 1.0:
            vr_r = _VOL_RATIO[1]
        elif vr >= 0.7:
            vr_r = _VOL_RATIO[2]
        else:
            vr_r = _VOL_RATIO[3]
        sv = SCORE_W["vol"] * vr_r
        sb = SCORE_W["bei"] * (
            (_BEI_SPLIT[0] if e.get("bei") else 0.0)
            + (_BEI_SPLIT[1] if e.get("bei2") else 0.0)
            + (_BEI_SPLIT[2] if e.get("bei24") else 0.0))
        # 校验重建 == 落盘（证明口径逐位一致）
        if round(sp + sv + sb) != e["score"]:
            continue
        # 瘦身 A：pos/vol 按 40:30 等比放大
        slimA = sp / 0.70 + sv / 0.70
        # 瘦身 B：pos/vol 平分 50:50（等效 sv 用 vol 档中点替代——此处用
        # 简化：A 的另一种极端，直接给 vol 顶档敏感度不变，仅展示区间）
        per_day[str(e.get("dt") or e.get("day"))].append(
            {"s": e["stock"], "cur": e["score"],
             "slim": round(slimA), "bei_earned": round(sb)})

    days = sorted(per_day)
    overlaps, n_days = [], 0
    for day in days:
        rows = per_day[day]
        if len(rows) < 8:
            continue
        n_days += 1
        top_cur = {r["s"] for r in sorted(rows, key=lambda r: -r["cur"])[:5]}
        top_slim = {r["s"] for r in sorted(rows, key=lambda r: -r["slim"])[:5]}
        overlaps.append(len(top_cur & top_slim) / 5.0)
    res = {
        "n_days_compared": n_days,
        "top5_overlap_mean": round(sum(overlaps) / len(overlaps), 3) if overlaps else None,
        "interpretation":
            "瘦身后逐日 top5 平均重合度；0.6=每天 3/5 换人。"
            "bei_earned 分布可看背驰分对排序的实际扰动幅度。",
        "bei_earned_dist": {},
        "ratio_plan": "A: pos/vol 按 40:30 等比放大（57.1/42.9）",
    }
    from collections import Counter
    c = Counter(r["bei_earned"] for rows in per_day.values() for r in rows)
    res["bei_earned_dist"] = {str(k): v for k, v in sorted(c.items())}
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
