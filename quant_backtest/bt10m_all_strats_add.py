# -*- coding: utf-8 -*-
"""全策略总测追加组合腿：S2_stop12（合并 t=1.99 全场最高、零执行代价）
× 入场侧最优（A3/D1）。追加进 bt10m_all_strats.json。

  C5_A3+S2 = slot_mode=score + stop −12%
  C6_D1+S2 = slot_mode=score + cap25 + stop −12%

用法：python bt10m_all_strats_add.py
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import bt10m  # noqa: E402
import bt10m_all_strats as base  # noqa: E402
from bt10m_partial_tp import pair_alpha  # noqa: E402

OUT = base.OUT
COST = base.COST

ADD = [
    ("C5_A3+S2", {"slot_mode": "score", "stop_pct": -0.12}, "组合"),
    ("C6_D1+S2", {"slot_mode": "score", "slot_score_cap": 0.25,
                  "stop_pct": -0.12}, "组合"),
]


def main():
    t0 = time.time()
    _, err = base.eng.run_job({"preset": "v3", "d0": base.WINDOWS[0][1],
                               "d1": base.WINDOWS[0][2], "cost": COST},
                              "allstrat_boot2", progress=lambda *a, **k: None)
    assert not err, err
    navs = {}
    for wtag, w0, w1 in base.WINDOWS:
        nav, _ = bt10m.load_market_bench(w0, w1)
        navs[wtag] = (nav, sorted(nav))

    out = json.load(open(OUT, encoding="utf-8"))
    for tag, extra, kind in ADD:
        extra = dict(extra)
        rows, pooled = base.run_variant(tag, extra, navs)
        m = sum(pooled) / len(pooled)
        import statistics
        sd = statistics.stdev(pooled) or 1e-9
        t = m / (sd / len(pooled) ** 0.5)
        ry = [r for r in rows if r["w"] == "RY"][0]
        out["variants"][tag] = {"kind": kind, "windows": rows,
                                "ry_ret": ry["ret"], "ry_mdd": ry["mdd"],
                                "merged": {"n": len(pooled),
                                           "alpha_bp": round(m * 100),
                                           "t": round(t, 2)}}
        print("%-16s [%s] 合并 n %3d  α %+5dbp  t %5.2f   RY %+6.2f%%"
              % (tag, kind, len(pooled), m * 100, t, ry["ret"]), flush=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("追加完成 %s（%.0fs）" % (OUT, time.time() - t0))


if __name__ == "__main__":
    main()
