# -*- coding: utf-8 -*-
"""验证 score_3buy 量纲修复：① 信号是否逐条不变（只有 score 变）；② 位置到底有没有信息。

为什么要单独验这两条：
  · 修法把 bars 的收盘价从复权改回原始价。结构层（包含处理/分型/笔/中枢）只用 h/l，
    所以信号本该逐条不变——若不逐条一致，说明这个修法动到了结构，不是「只修评分」。
  · 位置分原来是恒 40（常数），修完才真的在排序。但「贴近中枢上沿=好」只是假设，
    要先看数据：按信号日的 close/zg−1 分桶，看之后 20 个交易日的真实收益。

用法：python bt10m_score_diff.py
"""
import json
import os
import statistics
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
EV = os.path.join(BASE, "data", "sig_events")

WINDOWS = [
    ("IN", "sig_events_market_v3_20251013_20261012.json"),
    ("OOS", "sig_events_market_v3_20241008_20251010.json"),
    ("PRE", "sig_events_market_v3_20231010_20240930.json"),
]

BUCKETS = [(None, 0.02, "≤2%"), (0.02, 0.05, "2~5%"), (0.05, 0.10, "5~10%"),
           (0.10, 0.20, "10~20%"), (0.20, None, ">20%")]


def load(fp):
    return json.load(open(fp, encoding="utf-8"))["events"]


def main():
    eng._ensure_market_loaded()
    idx = {d: i for i, d in enumerate(eng.cal_idx_all)}
    report = {}

    for tag, fn in WINDOWS:
        fp = os.path.join(EV, fn)
        bak = fp + ".bak-prescore"
        new = load(fp)
        old = load(bak) if os.path.exists(bak) else []

        def key(e):
            return (e["day"], e["stock"], e["type"])

        nk = {key(e) for e in new}
        ok = {key(e) for e in old}
        omap = {key(e): e for e in old}
        same_val = diff_zg = 0
        sc_old = Counter()
        sc_new = Counter()
        for e in new:
            k = key(e)
            o = omap.get(k)
            if o is None:
                continue
            if e.get("zg") == o.get("zg") and e.get("zd") == o.get("zd"):
                same_val += 1
            else:
                diff_zg += 1
            sc_old[o.get("score")] += 1
            sc_new[e.get("score")] += 1

        print("\n########## %s" % tag)
        print("事件数：旧 %d → 新 %d（新增 %d / 消失 %d）"
              % (len(old), len(new), len(nk - ok), len(ok - nk)))
        print("共有事件里 zg/zd 完全相同 %d 条，不同 %d 条" % (same_val, diff_zg))
        if old:
            print("旧 score 唯一值 %d 个（最小 %s / 最大 %s）"
                  % (len(sc_old), min(x for x in sc_old if x is not None),
                     max(x for x in sc_old if x is not None)))
            print("新 score 唯一值 %d 个（最小 %s / 最大 %s）"
                  % (len(sc_new), min(x for x in sc_new if x is not None),
                     max(x for x in sc_new if x is not None)))

        # ---- 位置是否有信息：3买后 20 个交易日的真实收益，按 close/zg−1 分桶 ----
        buckets = {b[2]: [] for b in BUCKETS}
        ts = []
        for e in new:
            if e["type"] != "3买":
                continue
            zg = e.get("zg")
            d = e["day"]
            if not zg or zg <= 0 or d not in idx:
                continue
            c = eng.px_raw(e["stock"], d, "close")
            if c is None:
                continue
            t = c / zg - 1.0
            ts.append(t)
            i = idx[d]
            j = i + 20
            if j >= len(eng.cal_idx_all):
                continue
            p0 = eng.px(e["stock"], d, "close")
            p1 = eng.px(e["stock"], eng.cal_idx_all[j], "close")
            if not p0 or not p1:
                continue
            r = (p1 / p0 - 1) * 100
            for lo, hi, lab in BUCKETS:
                if (lo is None or t > lo) and (hi is None or t <= hi):
                    buckets[lab].append(r)
                    break

        print("3买信号 %d 条；close/zg−1 中位 %+.1f%%，≤2%% 占 %.1f%%"
              % (len(ts), statistics.median(ts) * 100,
                 100.0 * sum(1 for x in ts if x <= 0.02) / max(1, len(ts))))
        print("  信号后 20 交易日收益（按位置分桶）：")
        for lo, hi, lab in BUCKETS:
            v = buckets[lab]
            if not v:
                continue
            tval = 0.0
            if len(v) > 1 and statistics.stdev(v):
                tval = statistics.mean(v) / (statistics.stdev(v) / len(v) ** 0.5)
            print("    %-7s n=%5d  均值 %+6.2f%%  中位 %+6.2f%%  t=%5.2f"
                  % (lab, len(v), statistics.mean(v), statistics.median(v), tval))
        report[tag] = {
            "n_old": len(old), "n_new": len(new),
            "added": len(nk - ok), "gone": len(ok - nk),
            "zg_same": same_val, "zg_diff": diff_zg,
            "buckets": {lab: {"n": len(v), "mean": round(statistics.mean(v), 2)}
                        for lab, v in buckets.items() if v},
        }

    out = os.path.join(BASE, "reports", "bt10m_score_diff.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % out)


if __name__ == "__main__":
    main()
