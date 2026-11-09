# -*- coding: utf-8 -*-
"""入场时价格相对 ZG 的位置，到底有多重要。

`exit_zg_lab` 显示 ZG 止损触发率 86%、中位第 1 天就触发 —— 意思是绝大多数
信号入场时价格已经在 ZG 附近或下方。如果这成立，那些「买点」在入场那一刻
就已经失效了，止损只是在事后补救。

本脚本把入场时价格相对 ZG 的位置分档，看 20 日收益。这决定了池内择时的
第一条硬规则该写「过 ZG 才允许动」，还是「在 ZG 附近就可以试」。
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
for _p in (QB, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ASHARE = os.path.join(QB, "data", "ashare")
SHADOW = os.path.join(QB, "data", "combo_shadow.jsonl")
OUT = os.path.join(ROOT, "portal", "entry_zone_lab.json")

HOLD = 20
BINS = [(-99, -5, "<-5%"), (-5, -1, "-5~-1%"), (-1, 1, "-1~+1%"),
        (1, 3, "+1~+3%"), (3, 8, "+3~+8%"), (8, 99, ">+8%")]


def _one(rec):
    path = os.path.join(ASHARE, rec["code"].replace(".", "_") + ".parquet")
    if not os.path.exists(path):
        return None
    import chanlun_scan as cs
    import chanlun_signal as csig

    bars = cs._bars_from_parquet(path)
    if not bars or len(bars) < 130:
        return None
    idx = {str(b["dt"]).replace("-", "")[:8]: i for i, b in enumerate(bars)}
    i0 = idx.get(str(rec["entry_date"]).replace("-", "")[:8])
    if i0 is None or i0 + HOLD >= len(bars):
        return None
    st = csig.analyze(bars[: i0 + 1])
    pvs = st.get("pivots") or []
    if not pvs:
        return None
    zg, zd = float(pvs[-1]["zg"]), float(pvs[-1]["zd"])
    px = bars[i0]["c"]
    if px <= 0 or zd <= 0:
        return None
    zone = "above" if px > zg else ("inside" if px > zd else "below")
    return {"code": rec["code"], "dt": bars[i0]["dt"], "type": rec.get("type"),
            "zone": zone, "d_zg": round((px / zg - 1) * 100, 2),
            "r20": round((bars[i0 + HOLD]["c"] / px - 1) * 100, 3)}


def tt(v):
    if len(v) < 3 or v.std(ddof=1) == 0:
        return 0.0
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def main():
    recs = []
    with open(SHADOW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("pass") and r.get("mature"):
                recs.append({"code": r["code"], "entry_date": r.get("entry_date"),
                             "type": r.get("type")})
    rows = []
    with ProcessPoolExecutor(max_workers=12) as ex:
        for r in ex.map(_one, recs, chunksize=20):
            if r:
                rows.append(r)
    print("可算 %d 笔（样本 %d）\n" % (len(rows), len(recs)))

    print("=" * 76)
    print("入场时价格相对 ZG 的位置 × 之后 %d 日收益" % HOLD)
    print("=" * 76)
    print("位置分布：%s"
          % "  ".join("%s=%d" % (k, v) for k, v in Counter(r["zone"] for r in rows).items()))
    print("\n%-8s %5s %9s %9s %9s %8s"
          % ("zone", "笔数", "20日均", "中位", "正收益", "t"))
    out_zone = {}
    for z in ("above", "inside", "below"):
        v = np.array([r["r20"] for r in rows if r["zone"] == z], float)
        if len(v) == 0:
            continue
        out_zone[z] = {"n": len(v), "mean": round(float(v.mean()), 3),
                       "median": round(float(np.median(v)), 3),
                       "win": round(float((v > 0).mean() * 100), 1),
                       "t": round(tt(v), 2)}
        print("%-8s %5d %8.2f%% %8.2f%% %8.1f%% %7.2f"
              % (z, len(v), v.mean(), np.median(v), (v > 0).mean() * 100, tt(v)))

    print("\n按 现价/ZG−1 细分：")
    print("%-10s %5s %9s %9s %9s %8s" % ("区间", "笔数", "20日均", "中位", "正收益", "t"))
    out_bin = {}
    for lo, hi, lab in BINS:
        v = np.array([r["r20"] for r in rows if lo <= r["d_zg"] < hi], float)
        if len(v) < 5:
            continue
        out_bin[lab] = {"n": len(v), "mean": round(float(v.mean()), 3),
                        "median": round(float(np.median(v)), 3),
                        "win": round(float((v > 0).mean() * 100), 1),
                        "t": round(tt(v), 2)}
        print("%-10s %5d %8.2f%% %8.2f%% %8.1f%% %7.2f"
              % (lab, len(v), v.mean(), np.median(v), (v > 0).mean() * 100, tt(v)))

    # 按信号类型分开看：三买本来就该在 ZG 上方，二买/一买在下方不奇怪
    print("\n按信号类型 × zone：")
    for typ in sorted({r["type"] for r in rows if r["type"]}):
        sub = [r for r in rows if r["type"] == typ]
        parts = []
        for z in ("above", "inside", "below"):
            v = np.array([r["r20"] for r in sub if r["zone"] == z], float)
            parts.append("%s n=%d 均%+.2f%%" % (z, len(v), v.mean()) if len(v) >= 5 else "%s n<5" % z)
        print("  %-4s %s" % (typ, " ｜ ".join(parts)))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"n": len(rows), "hold": HOLD, "zone": out_zone, "bins": out_bin},
                  f, ensure_ascii=False, indent=1)
    print("\n→ %s" % OUT)


if __name__ == "__main__":
    main()
