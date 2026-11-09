# -*- coding: utf-8 -*-
"""「势」判据的横向比较：哪个变量才真的区分后续收益。

背景
----
第一版势档位用「价格在中枢上/中/下 × 中枢上移/走平/下移」合成，逻辑上是
缠论标准推法，但实测 GREEN 组之后 20 日 −1.38%（t=−2.72）、AMBER 组
+0.91%，**完全非单调且方向相反**。说明那套判据测的不是「势」，是「已经
涨了多少」——它是动量的重述，而 A 股指数在 20 日尺度上是反转的。

所以这里把所有能想到的势代理一次性拉平比较，看谁有区分度：
  1. zone            价格在中枢上/中/下
  2. pv_dir          中枢上移/走平/下移
  3. ma_align        现价 vs MA20 vs MA60 排列
  4. last_sig_alive  最后一个买点是否被破坏（缠论本意的「结构是否有效」）
  5. qpos60          60 日区间分位
  6. ret20           20 日动量
  7. drawdown_from_60h  距 60 日高点回撤
  8. n_bars_since_sig   距最后一个买点多少根

评判标准不是「哪个符合缠论教科书」，而是「哪个能把后续收益切开」。
单调性 + 高-低组间差 + 组内 t 值，三个一起看。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(ROOT, "quant_backtest"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chanlun_regime as RG  # noqa: E402

H = 20          # 主判视界


def collect(code, asof, lookback):
    """逐日抽出所有候选变量的原始取值 + 20 日后续收益。"""
    bars = RG._load_index(code, asof)
    lo = max(140, len(bars) - lookback)
    recs = []
    for j in range(lo, len(bars)):
        if j + H >= len(bars):
            continue
        st = RG.structure_state(bars[: j + 1])
        if not st:
            continue
        i = j
        ma20, ma60 = st["ma20"], st["ma60"]
        px = st["px"]
        ma_align = None
        if ma20 and ma60:
            ma_align = ("多头" if px > ma20 > ma60 else
                        ("空头" if px < ma20 < ma60 else "交织"))
        sig = st["last_sig"]
        alive = None
        since = None
        if sig:
            alive = (sig["broken_pct"] is None or sig["broken_pct"] > -3)
            since = sig["bars_ago"]
        seg60 = bars[max(0, i - 59): i + 1]
        hi60 = max(b["h"] for b in seg60)
        recs.append({
            "dt": st["asof"],
            "fwd": bars[j + H]["c"] / bars[j]["c"] - 1,
            "zone": st["zone"], "pv_dir": st["pv_dir"],
            "ma_align": ma_align, "last_sig_alive": alive,
            "sig_type": sig["type"] if sig else None,
            "since_sig": since,
            "qpos60": st["qpos60"], "ret20": st["ret20_pct"],
            "dd60": round((px / hi60 - 1) * 100, 2) if hi60 else None,
        })
    return recs


def bucket(recs, key, bins=None, labels=None):
    by = {}
    for r in recs:
        v = r.get(key)
        if v is None:
            continue
        if bins is not None:
            lab = None
            for b, l in zip(bins, labels):
                if v <= b:
                    lab = l
                    break
            if lab is None:
                lab = labels[-1] if v > bins[-1] else None
            if lab is None:
                continue
        else:
            lab = str(v)
        by.setdefault(lab, []).append(r["fwd"])
    return by


def report(name, by, order=None):
    keys = order if order else sorted(by)
    keys = [k for k in keys if k in by]
    if len(keys) < 2:
        return None
    print("  %s" % name)
    stats = []
    for k in keys:
        v = np.asarray(by[k], float)
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 and v.std(ddof=1) else 0
        stats.append((k, len(v), v.mean() * 100, t))
        print("    %-10s n=%3d  20日均 %+7.2f%%  t=%+5.2f" % (k, len(v), v.mean() * 100, t))
    means = [s[2] for s in stats]
    spread = max(means) - min(means)
    mono = all(a < b for a, b in zip(means[:-1], means[1:])) or \
        all(a > b for a, b in zip(means[:-1], means[1:]))
    print("    极差 %.2fpp  单调 %s" % (spread, "是" if mono else "否"))
    return {"spread": round(spread, 2), "mono": mono,
            "buckets": [{"k": s[0], "n": s[1], "mean_pct": round(s[2], 3),
                         "t": round(s[3], 2)} for s in stats]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000985.CSI")
    ap.add_argument("--asof", default="20261106")
    ap.add_argument("--lookback", type=int, default=600)
    ap.add_argument("--json", default=os.path.join(ROOT, "portal", "regime_criteria.json"))
    args = ap.parse_args()

    recs = collect(args.code, args.asof.replace("-", ""), args.lookback)
    print("=" * 78)
    print("势判据横向比较 · %s · %s ~ %s · n=%d"
          % (args.code, recs[0]["dt"], recs[-1]["dt"], len(recs)))
    print("=" * 78)

    allf = np.asarray([r["fwd"] for r in recs], float)
    print("\n基线：任意日买入持有 %d 日 = %+.2f%%（n=%d）\n"
          % (H, allf.mean() * 100, len(allf)))

    out = {}
    out["zone"] = report("1) zone 价格在中枢位置",
                         bucket(recs, "zone"), ["above", "inside", "below"])
    out["pv_dir"] = report("2) pv_dir 中枢方向",
                           bucket(recs, "pv_dir", None), ["1", "0", "-1"])
    out["ma_align"] = report("3) ma_align 均线排列",
                             bucket(recs, "ma_align"), ["多头", "交织", "空头"])
    out["last_sig_alive"] = report("4) last_sig_alive 最后买点是否有效",
                                   bucket(recs, "last_sig_alive"), ["True", "False"])
    out["qpos60"] = report("5) qpos60 60日分位",
                           bucket(recs, "qpos60", [-1, 0.2, 0.4, 0.6, 0.8, 2],
                                  ["<0.2", "0.2~0.4", "0.4~0.6", "0.6~0.8", ">0.8"]),
                           ["<0.2", "0.2~0.4", "0.4~0.6", "0.6~0.8", ">0.8"])
    out["ret20"] = report("6) ret20 20日动量",
                          bucket(recs, "ret20", [-10, -5, 0, 5, 10, 999],
                                 ["<-10%", "-10~-5%", "-5~0%", "0~5%", "5~10%", ">10%"]),
                          ["<-10%", "-10~-5%", "-5~0%", "0~5%", "5~10%", ">10%"])
    out["dd60"] = report("7) dd60 距60日高点回撤",
                         bucket(recs, "dd60", [-20, -10, -5, -2, 999],
                                ["<-20%", "-20~-10%", "-10~-5%", "-5~-2%", ">-2%"]),
                         ["<-20%", "-20~-10%", "-10~-5%", "-5~-2%", ">-2%"])
    out["sig_type"] = report("8) sig_type 最后买点类型",
                             bucket(recs, "sig_type"), ["1买", "2买", "3买"])

    # ---- 二维交叉：哪两个组合最能把收益切开 ----
    print("\n" + "=" * 78)
    print("二维交叉：ma_align × zone")
    cross = {}
    for r in recs:
        k = "%s|%s" % (r["ma_align"], r["zone"])
        cross.setdefault(k, []).append(r["fwd"])
    rows = []
    for k in sorted(cross):
        v = np.asarray(cross[k], float)
        if len(v) < 15:
            continue
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) else 0
        rows.append((k, len(v), v.mean() * 100, t))
    for k, n, m, t in sorted(rows, key=lambda x: -x[2]):
        print("  %-16s n=%3d  20日均 %+7.2f%%  t=%+5.2f" % (k, n, m, t))
    out["cross_ma_zone"] = [{"k": k, "n": n, "mean_pct": round(m, 3),
                             "t": round(t, 2)} for k, n, m, t in rows]

    print("\n" + "=" * 78)
    print("读法提醒：n 小于 30 的组别不要引用；20 日收益未扣成本、未年化。")
    print("=" * 78)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump({"code": args.code, "n": len(recs), "h": H,
                   "baseline_pct": round(float(allf.mean()) * 100, 3),
                   "criteria": out}, f, ensure_ascii=False, indent=1)
    print("→ %s" % args.json)


if __name__ == "__main__":
    main()
