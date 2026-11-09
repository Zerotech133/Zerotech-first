# -*- coding: utf-8 -*-
"""出场模拟的回归检查：sim() 的结果必须与池的已记录结果对得上。

## 为什么需要这个

`exit_rule_study.sim()` 是自己实现的出场模拟，与产线回填（`chanlun_ext_backfill.py`
的 `settle`/`finalize`）是两套独立代码。一旦价格面板加载、复权处理或下标语义改动，
模拟结果会静默偏移，而所有出场规则结论都建立在它上面。

本脚本给出两个断言，任何一项失败就 exit 1：

[断言 1] hold_20 模拟 vs 池 ret（只取 exit_reason=='hold20' 的笔）
         中位数差必须 ≈ −0.20pp（双边成本）。容差 ±0.05pp。
         实测 −0.2028pp。

[断言 2] 止损单调性：trail 越紧，平均持有越短。
         trail_04 < trail_08 < trail_20。违反说明触发逻辑写错。

## 为什么用中位数而不是均值

均值实测 −0.3307pp，比理论值低 0.13pp。原因不是 bug，是约 1% 的笔存在分红除权
口径差异（p1 = −1.90pp）。中位数对这批异常不敏感，是更干净的校验量。
均值偏离必须**单独记录**（它在报告里作为"模拟与池 0.13pp 复权差"的依据）。

## 用法

  python exit_sim_consistency.py            # 默认 300 笔抽样
  python exit_sim_consistency.py --all      # 全量（约 60 秒）
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exit_rule_study as S  # noqa: E402
import exit_rule_study2 as S2  # noqa: E402
from portfolio_mtm import load_price_panel  # noqa: E402

COST_PP = -0.1999          # 双边成本对收益率的影响
TOL = 0.05                 # 中位数容差（pp）
SEED = 20261108


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全量（默认抽样 300 笔）")
    ap.add_argument("--since", default="20231009")
    a = ap.parse_args()

    rows = S2.load_pool(a.since)
    P = load_price_panel({r["code"] for r in rows}, S.TDX, ohlc=True)
    keep = []
    for r in rows:
        p = P.get(r["code"])
        if not p:
            continue
        ei = p["pos"].get(r["d"])
        if ei is None:
            continue
        r["ei"] = ei
        r["_p"] = p
        keep.append(r)
    pool_n = len(keep)
    if not a.all:
        rng = np.random.default_rng(SEED)
        keep = [keep[i] for i in rng.choice(pool_n, min(300, pool_n), replace=False)]
    print("池 %d 笔，检查样本 %d 笔" % (pool_n, len(keep)))

    ok = True

    # ---------------- 断言 1 ----------------
    diffs = []
    for r in keep:
        if r["exit_reason"] != "hold20":
            continue
        p, ei = r["_p"], r["ei"]
        xi, px, days, why = S.sim(("hold", 0.0, 0.0, 20), p["o"], p["h"], p["l"],
                                  p["c"], ei, r["entry_px"])
        diffs.append((S.net_ret(r["entry_px"], px) - r["pool_ret"]) * 100)
    diffs = np.array(diffs)
    med, mean = float(np.median(diffs)), float(diffs.mean())
    dev = med - COST_PP
    st1 = abs(dev) <= TOL
    ok &= st1
    print("\n[断言 1] hold_20 模拟 vs 池 ret（n=%d，仅 exit_reason=='hold20'）" % len(diffs))
    print("  中位数差 %+.4fpp（期望 %+.4f ± %.2f）→ %s"
          % (med, COST_PP, TOL, "PASS" if st1 else "FAIL 偏差 %+.4f" % dev))
    print("  均值差   %+.4fpp  ← 偏低来自约 1%% 的除权口径异常笔，非 bug，需在报告中单独说明"
          % mean)
    print("  p1/p99  %+.2f / %+.2f" % (np.percentile(diffs, 1), np.percentile(diffs, 99)))

    # ---------------- 断言 2 ----------------
    print("\n[断言 2] 止损单调性（越紧 → 持有越短）")
    means = {}
    samp = keep[: min(800, len(keep))]
    for x in (0.04, 0.08, 0.20):
        ds = [S.sim(("trail", x, 0.0, 20), r["_p"]["o"], r["_p"]["h"], r["_p"]["l"],
                    r["_p"]["c"], r["ei"], r["entry_px"])[2] for r in samp]
        means[x] = float(np.mean(ds))
        print("  trail_%02d 均持 %.2f 日" % (int(x * 100), means[x]))
    mono = means[0.04] < means[0.08] < means[0.20]
    ok &= mono
    print("  单调性 → %s" % ("PASS" if mono else "FAIL"))

    # ---------------- 边界 ----------------
    print("\n[断言 3] 序列末尾不足 20 根不崩")
    last = max(keep, key=lambda r: r["ei"])
    try:
        res = S.sim(("trail", 0.08, 0.0, 20), last["_p"]["o"], last["_p"]["h"],
                    last["_p"]["l"], last["_p"]["c"], last["ei"], last["entry_px"])
        st3 = res[2] >= 1 and np.isfinite(res[1])
        ok &= st3
        print("  ei=%d / len=%d → exit=%s %s"
              % (last["ei"], len(last["_p"]["c"]), res, "PASS" if st3 else "FAIL"))
    except Exception as e:  # noqa: BLE001
        ok = False
        print("  FAIL 抛异常：%r" % e)

    print("\n%s" % ("全部通过" if ok else "存在失败项，出场规则结论不可用"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
